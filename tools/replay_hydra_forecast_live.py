"""Drive the live forecast monitor with a saved Hydra capture, end to end.

A fake IPC serves the capture's agent slot payload, account and team, and the
recorded native decision snapshots are fed in their original order. The real
isolated engine converts the inputs and runs the forecast. Nothing touches a
game process; the controller's regroup call is replaced by a recorder.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import tempfile
import time
from typing import Any

import chimera_controller as controller
from hydra_forecast_live import HydraForecastMonitor


class CaptureIpc:
    def __init__(self, capture: Path, team: dict[str, Any]):
        self.source = json.loads((capture / "replay-source-candidate.json").read_text(encoding="utf-8"))
        self.account_state = json.loads((capture / "account-state.json").read_text(encoding="utf-8"))
        self.team = team

    def replay_input(self) -> dict[str, Any]:
        return self.source

    def account(self) -> dict[str, Any]:
        return self.account_state

    def lifecycle(self) -> dict[str, Any]:
        return {"screen": "battle", "battle": dict(self.team)}


def native_states(path: Path, generation: int) -> list[dict[str, Any]]:
    states: dict[int, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            state = row.get("payload")
            if (row.get("channel") == "decision" and isinstance(state, dict)
                    and state.get("type") == "decision_state"
                    and state.get("battleGeneration") == generation):
                states[state["sequence"]] = state
    return [states[key] for key in sorted(states)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--bundle", type=Path, required=True,
                        help="runtime bundle directory with raid_offline_probe.exe")
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--conditions", type=str, required=True,
                        help="JSON list of devourOrderRetryConditions")
    parser.add_argument("--settle-after", type=int, default=20,
                        help="native windows fed before waiting for the forecast")
    args = parser.parse_args()
    strategy = json.loads(args.strategy.read_text(encoding="utf-8"))
    strategy = strategy.get("strategy", strategy)
    strategy.setdefault("objectives", {})
    strategy["objectives"]["devourOrderForecast"] = True
    strategy["objectives"]["devourOrderRetryConditions"] = json.loads(args.conditions)
    controller.validate_strategy_config(strategy, boss_mode="hydra")
    provenance = json.loads((args.capture / "replay-source" / "capture-provenance.json")
                            .read_text(encoding="utf-8"))
    team = {"heroTypeIds": provenance["teamHeroTypeIds"], "heroIds": provenance["teamHeroIds"]}
    ipc = CaptureIpc(args.capture, team)
    lines: list[str] = []
    work = Path(tempfile.mkdtemp(prefix="hydra-forecast-live-"))
    monitor = HydraForecastMonitor(emit=lambda text: (lines.append(text), print(text, flush=True)),
                                   bundle_provider=lambda pid: args.bundle.resolve(),
                                   work_root=work)
    runtime_state: dict[str, Any] = {"regroupRetries": 0}
    memory = controller.SkillCapabilityMemory()
    controller.ACTIVE_BOSS_MODE = "hydra"
    trigger = None
    fed = 0
    started = time.monotonic()
    for state in native_states(args.capture / "raw.jsonl.gz", args.generation):
        memory.observe_state(state)
        controller.evaluate_hydra_devour_retry_trigger(strategy, state, runtime_state)
        tracker = runtime_state.get("hydraDevourTracker", {})
        result = monitor.observe(strategy, state, ipc=ipc, capability_memory=memory,
                                 marked_target=controller.hydra_marked_target,
                                 tracker_armed=tracker.get("armed") is True)
        fed += 1
        if result is not None:
            trigger = result
            break
        if fed == args.settle_after and monitor.battle and monitor.battle.job \
                and monitor.battle.job.thread is not None:
            monitor.battle.job.thread.join(timeout=400)
    summary = {
        "fedStates": fed,
        "elapsedSeconds": round(time.monotonic() - started, 2),
        "monitor": monitor.telemetry(),
        "regroupTrigger": None if trigger is None else {
            "conditionIndex": trigger.condition_index, "markIndex": trigger.mark_index,
            "actualHeroTypeId": trigger.actual_hero_type_id, "applyTurn": trigger.apply_turn,
            "predictedSequence": list(trigger.predicted_sequence)},
        "workDirectory": str(work),
        "messages": lines,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

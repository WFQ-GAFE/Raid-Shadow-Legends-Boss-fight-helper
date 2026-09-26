"""Field-compare forecast decision states with same-battle native snapshots.

The forecast is rerun with the saved inputs; every offline decision_state
whose turn has a native agent snapshot of the same battle is compared on the
fields the policy can read. Differences are listed, never hidden.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import chimera_controller as controller
from hydra_forecast import run_forecast


ENTITY_FIELDS = ("typeId", "currentFormIndex", "turnCount", "healthPct", "dead", "active",
                 "isHydraHead", "isHydraNeck", "headState", "devouredHeroId", "isDevouring",
                 "digestionTurns", "battlePosition", "states", "avatar")
SKILL_FIELDS = ("typeId", "level", "cooldown", "maxCooldown", "ready", "disabled",
                "heroSkill", "activeSkill", "hiddenOnHud")
EFFECT_FIELDS = ("id", "producerId", "skillTypeId", "effectTypeId", "effectKindId", "effectKind",
                 "effectGroupId", "applyTurn", "lifetime", "turnsLeft", "devouredHeroId")
HUD_FIELDS = ("slot", "skillId", "typeId", "cooldown", "defaultCooldown", "passive", "blocked",
              "ready", "validTargetIds")
TOP_FIELDS = ("activeHeroId", "activeHeroTypeId", "activeHeroTurnCount", "activeHeroFormIndex",
              "activeHeroSkillsUpdateCounter", "activeHeroIsMetamorph", "activeHeroIsTransformed")
BATTLE_FIELDS = ("kindId", "round", "turn", "playerTurnCount", "finished", "autoMode", "hydraBattle")


class Diff:
    def __init__(self) -> None:
        self.equal = 0
        self.items: list[dict[str, Any]] = []

    def check(self, path: str, live: Any, offline: Any) -> None:
        if isinstance(live, float) or isinstance(offline, float):
            same = (isinstance(live, (int, float)) and isinstance(offline, (int, float))
                    and abs(float(live) - float(offline)) <= 1e-9 * max(1.0, abs(float(live))))
        else:
            same = live == offline
        if same:
            self.equal += 1
        else:
            self.items.append({"path": path, "live": live, "offline": offline})


def compare_state(live: dict[str, Any], offline: dict[str, Any], diff: Diff, prefix: str) -> None:
    for key in TOP_FIELDS:
        diff.check(f"{prefix}.{key}", live.get(key), offline.get(key))
    for key in BATTLE_FIELDS:
        diff.check(f"{prefix}.battle.{key}", live["battle"].get(key), offline["battle"].get(key))
    diff.check(f"{prefix}.hydra.turnCount", live["hydra"].get("turnCount"), offline["hydra"].get("turnCount"))
    diff.check(f"{prefix}.battleRandom.words", live["battleRandom"].get("words"),
               offline["battleRandom"].get("words"))
    diff.check(f"{prefix}.skills.count", len(live["skills"]), len(offline["skills"]))
    for index, (a, b) in enumerate(zip(live["skills"], offline["skills"])):
        for key in HUD_FIELDS:
            diff.check(f"{prefix}.skills[{index}].{key}", a.get(key), b.get(key))
    for side in ("heroes", "bosses"):
        diff.check(f"{prefix}.{side}.ids", [item["id"] for item in live[side]],
                   [item["id"] for item in offline[side]])
        offline_by_id = {item["id"]: item for item in offline[side]}
        for entity in live[side]:
            other = offline_by_id.get(entity["id"])
            path = f"{prefix}.{side}[{entity['id']}]"
            if other is None:
                diff.check(path, "present", "absent")
                continue
            for key in ENTITY_FIELDS:
                diff.check(f"{path}.{key}", entity.get(key), other.get(key))
            diff.check(f"{path}.teamPosition", entity.get("teamPosition"), other.get("teamPosition"))
            diff.check(f"{path}.skills.count", len(entity["skills"]), len(other["skills"]))
            for index, (a, b) in enumerate(zip(entity["skills"], other["skills"])):
                for key in SKILL_FIELDS:
                    diff.check(f"{path}.skills[{index}].{key}", a.get(key), b.get(key))
            diff.check(f"{path}.effects.count", len(entity["effects"]), len(other["effects"]))
            for index, (a, b) in enumerate(zip(entity["effects"], other["effects"])):
                for key in EFFECT_FIELDS:
                    diff.check(f"{path}.effects[{index}].{key}", a.get(key), b.get(key))
            diff.check(f"{path}.canonicalHeadTypeId",
                       controller.canonical_hydra_head_type_id(entity),
                       controller.canonical_hydra_head_type_id(other))


def native_states(path: Path, generation: int) -> dict[int, dict[str, Any]]:
    states: dict[int, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            state = row.get("payload")
            if (row.get("channel") != "decision" or not isinstance(state, dict)
                    or state.get("type") != "decision_state"
                    or state.get("battleGeneration") != generation
                    or not state.get("skills")
                    or state.get("battle", {}).get("waitingForManualCommand") is not True):
                continue
            states.setdefault(state["battle"]["turn"], state)
    return states


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--team-provenance", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True, help="observer raw.jsonl.gz")
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    strategy = json.loads(args.strategy.read_text(encoding="utf-8"))
    strategy = strategy.get("strategy", strategy)
    provenance = json.loads(args.team_provenance.read_text(encoding="utf-8"))
    team = {"heroTypeIds": provenance["teamHeroTypeIds"], "heroIds": provenance["teamHeroIds"]}
    live = native_states(args.native, args.generation)
    diff = Diff()
    compared: list[int] = []

    def on_decision(state: dict[str, Any], record: dict[str, Any]) -> None:
        turn = state["battle"]["turn"]
        native = live.get(turn)
        if native is None:
            return
        live_copy = json.loads(json.dumps(native))
        offline_copy = json.loads(json.dumps(state))
        for item in (live_copy, offline_copy):
            controller.annotate_team_positions(item, {"screen": "battle", "battle": team})
        compare_state(live_copy, offline_copy, diff, f"turn{turn}")
        compared.append(turn)

    forecast = run_forecast(args.probe.resolve(), args.input.resolve(), strategy,
                            team_selection=team, on_decision=on_decision)
    report = {"schema": 1, "scope": "forecast_decision_state_vs_native_snapshot",
              "forecastStatus": forecast.get("status"), "comparedWindows": len(compared),
              "nativeWindows": len(live), "equalFields": diff.equal,
              "differenceCount": len(diff.items), "differences": diff.items[:200]}
    body = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "differences"}, ensure_ascii=False))
    for item in diff.items[:40]:
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    main()

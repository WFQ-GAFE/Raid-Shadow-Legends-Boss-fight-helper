"""Save each Chimera battle's start data and a per-decision trace.

The saved folder (cache/chimera-capture/<setup>-<ms>/) is the input for
checking the offline original engine against a real battle: the battle's own
BattleSetup/BattleSettings as the game serialized them, and for every player
decision the turn, active hero, battle RNG words, Chimera form, trial
progress and the command the controller submitted. Nothing here changes how
the battle is played.
"""
from __future__ import annotations

import copy
import gzip
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from chimera_replay_source import validate_chimera_replay_source
from hydra_replay_source import ReplaySourceError
from strategy_storage import atomic_write_bytes, atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
WORK_ROOT = PROJECT_ROOT / "cache" / "chimera-capture"
KEEP_WORK_DIRECTORIES = 20
INPUT_WAIT_SECONDS = 20.0
MAX_BUFFERED_RECORDS = 20_000
MAX_BUFFERED_STATES = 400
# Static per-difficulty data the agent repeats in every decision; saved once.
STATIC_STATE_KEYS = ("trialCatalog",)
TRIAL_KEYS = ("id", "started", "completed", "currentRaw", "targetRaw", "currentCounter",
              "counterLimit", "selfTurnWhichCompleted")


def _prune(root: Path) -> None:
    try:
        entries = sorted((item for item in root.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-KEEP_WORK_DIRECTORIES]:
        shutil.rmtree(stale, ignore_errors=True)


def _entities(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def trace_record(state: dict[str, Any]) -> dict[str, Any]:
    """Compact, replay-relevant view of one native decision state."""
    battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
    rng = state.get("battleRandom") if isinstance(state.get("battleRandom"), dict) else {}
    chimera = state.get("chimera") if isinstance(state.get("chimera"), dict) else {}
    chimera_id = chimera.get("id")
    boss = next((item for item in _entities(state, "bosses") if item.get("id") == chimera_id), None)
    trials = []
    for trial in (boss or {}).get("challenges", []) if isinstance(boss, dict) else []:
        if isinstance(trial, dict) and (trial.get("started") or trial.get("completed")):
            trials.append({key: trial.get(key) for key in TRIAL_KEYS if key in trial})
    return {
        "sequence": state.get("sequence"),
        "observedAtTick": state.get("observedAtTick"),
        "round": battle.get("round"),
        "turn": battle.get("turn"),
        "playerTurnCount": battle.get("playerTurnCount"),
        "finished": battle.get("finished"),
        "currentDamage": battle.get("currentDamage"),
        "activeHeroId": state.get("activeHeroId"),
        "activeHeroTypeId": state.get("activeHeroTypeId"),
        "activeHeroTurnCount": state.get("activeHeroTurnCount"),
        "rng": {"words": rng.get("words"), "turn": rng.get("turn"),
                "playerTurnCount": rng.get("playerTurnCount"), "readStatus": rng.get("readStatus")},
        "chimera": {"formIndex": chimera.get("currentFormIndex"), "turnCount": chimera.get("turnCount")},
        "heroes": [[hero.get("id"), hero.get("typeId"), hero.get("healthRaw"), hero.get("dead")]
                   for hero in _entities(state, "heroes")],
        "boss": ([boss.get("id"), boss.get("typeId"), boss.get("healthRaw"), boss.get("currentFormIndex")]
                 if isinstance(boss, dict) else None),
        "trials": trials,
    }


class ChimeraCaptureMonitor:
    """One per controller process; follows the battle generation in decisions."""

    def __init__(self, emit: Callable[[str], None] | None = None, work_root: Path = WORK_ROOT,
                 validator: Callable[..., tuple[dict[str, Any], bytes, bytes]] = validate_chimera_replay_source,
                 clock: Callable[[], float] = time.monotonic):
        self.emit = emit or (lambda text: print(text, flush=True))
        self.work_root = work_root
        self.validator = validator
        self.clock = clock
        self.generation: int | None = None
        self.opening: dict[str, Any] | None = None
        self.from_opening = False
        self.folder: Path | None = None
        self.status = "idle"
        self.reason: str | None = None
        self.started_at = 0.0
        self.buffer: list[dict[str, Any]] = []
        self.state_buffer: list[bytes] = []
        self.records = 0
        self.states = 0
        self.snapshots: dict[str, Any] = {}
        self.static_saved = False
        self.static_payload: dict[str, Any] | None = None

    def _reset(self, generation: int, state: dict[str, Any]) -> None:
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        self.generation = generation
        self.from_opening = battle.get("playerTurnCount") in (0, 1)
        self.opening = state if self.from_opening else None
        self.folder = None
        self.status = "waiting" if self.from_opening else "not_from_opening"
        self.reason = None if self.from_opening else "takeover_started_mid_battle"
        self.started_at = self.clock()
        self.buffer = []
        self.state_buffer = []
        self.records = 0
        self.states = 0
        self.snapshots = {}
        self.static_saved = False
        self.static_payload = None

    def _append_state(self, state: dict[str, Any]) -> None:
        """Complete decision state (minus repeated static data), gzip member per line."""
        if self.status == "not_from_opening":
            return
        if self.static_payload is None:
            static = {key: state[key] for key in STATIC_STATE_KEYS if key in state}
            if static:
                self.static_payload = static
        stripped = {key: value for key, value in state.items() if key not in STATIC_STATE_KEYS}
        line = gzip.compress((json.dumps(stripped, ensure_ascii=False, separators=(",", ":"))
                              + "\n").encode("utf-8"), compresslevel=6)
        if self.folder is None:
            if len(self.state_buffer) < MAX_BUFFERED_STATES:
                self.state_buffer.append(line)
            return
        try:
            with open(self.folder / "decision-states.jsonl.gz", "ab") as stream:
                stream.write(line)
            self.states += 1
        except OSError:
            pass
        if not self.static_saved and self.static_payload is not None:
            try:
                atomic_write_json(self.folder / "decision-static.json", self.static_payload)
                self.static_saved = True
            except OSError:
                pass

    def _write_snapshots(self) -> None:
        for name, payload in self.snapshots.items():
            try:
                atomic_write_json(self.folder / name, payload)
            except OSError:
                pass

    def _append(self, record: dict[str, Any]) -> None:
        if self.status == "not_from_opening":
            return  # A replay needs every command from the opening.
        record = {"generation": self.generation, **record}
        if self.folder is None:
            if len(self.buffer) < MAX_BUFFERED_RECORDS:
                self.buffer.append(record)
            return
        try:
            with open(self.folder / "live-trace.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.records += 1
        except OSError:
            pass

    def _open_folder(self, name: str) -> Path | None:
        try:
            self.work_root.mkdir(parents=True, exist_ok=True)
            folder = self.work_root / f"{name}-{int(time.time() * 1000)}"
            folder.mkdir(parents=False, exist_ok=False)
        except OSError:
            return None
        self.folder = folder
        pending, self.buffer = self.buffer, []
        for record in pending:
            self._append({key: value for key, value in record.items() if key != "generation"})
        pending_states, self.state_buffer = self.state_buffer, []
        try:
            with open(folder / "decision-states.jsonl.gz", "ab") as stream:
                for line in pending_states:
                    stream.write(line)
            self.states += len(pending_states)
        except OSError:
            pass
        if self.static_payload is not None:
            try:
                atomic_write_json(folder / "decision-static.json", self.static_payload)
                self.static_saved = True
            except OSError:
                pass
        self._write_snapshots()
        _prune(self.work_root)
        return folder

    def _fail(self, reason: str) -> None:
        self.status = "unavailable"
        self.reason = reason
        folder = self._open_folder(f"generation-{self.generation}")
        if folder is not None:
            try:
                atomic_write_json(folder / "capture-failure.json", {
                    "schema": 1, "battleGeneration": self.generation, "reason": reason,
                    "fromOpening": self.from_opening,
                    "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            except OSError:
                pass
        self.emit(f"奇美拉开局数据未保存（{reason}）；不影响本场战斗。")

    def _try_capture(self, ipc: Any) -> None:
        source = ipc.replay_input()
        waited = self.clock() - self.started_at
        if not isinstance(source, dict) or source.get("battleGeneration") != self.generation:
            if waited > INPUT_WAIT_SECONDS:
                self._fail("代理未发布本局开局数据")
            return
        if source.get("type") != "chimera_replay_source" or source.get("status") != "captured":
            self._fail(f"代理未取得本局开局数据：{source.get('reason') or source.get('status')}")
            return
        account = ipc.account()
        account_name = account.get("accountName") if isinstance(account, dict) else None
        try:
            provenance, setups, settings = self.validator(
                source, self.opening, account, account_name if isinstance(account_name, str) else "")
        except ReplaySourceError as error:
            if str(error).startswith("opening_") and waited <= INPUT_WAIT_SECONDS:
                return  # The opening snapshot may still lack hero models.
            self._fail(f"开局数据校验失败：{error}")
            return
        folder = self._open_folder(provenance["battleSetupId"])
        if folder is None:
            self._fail("无法创建保存目录")
            return
        try:
            atomic_write_bytes(folder / "battle-setup.json", setups)
            atomic_write_bytes(folder / "battle-settings.json", settings)
            atomic_write_json(folder / "capture-provenance.json", provenance)
        except OSError as error:
            self._fail(f"写入失败：{error}")
            return
        self.status = "saved"
        self.emit(f"奇美拉开局数据已保存（用于离线模拟核对）：{folder.name}")

    def observe_decision(self, state: dict[str, Any], *, ipc: Any, config: dict[str, Any] | None = None,
                         capability_memory: Any = None) -> None:
        generation = state.get("battleGeneration")
        if type(generation) is not int or generation <= 0 or state.get("bossMode") != "chimera":
            return
        if generation != self.generation:
            self._reset(generation, state)
            if self.from_opening:
                # What the controller decided with at the opening, so an
                # offline run of the same strategy can be checked against it.
                if isinstance(config, dict):
                    self.snapshots["strategy.json"] = copy.deepcopy(config)
                payload = getattr(capability_memory, "payload", None)
                if callable(payload):
                    self.snapshots["capability-memory.json"] = payload()
        self._append({"event": "decision", **trace_record(state)})
        self._append_state(state)
        if self.status == "waiting":
            try:
                self._try_capture(ipc)
            except Exception as error:  # Capture is diagnostic; never stop control.
                self._fail(f"{type(error).__name__}: {error}")

    def observe_command(self, state: dict[str, Any], *, skill_type_id: Any, target_id: Any,
                        rule: str, status: Any, executed: bool) -> None:
        if state.get("battleGeneration") != self.generation:
            return
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        self._append({"event": "command", "sequence": state.get("sequence"),
                      "turn": battle.get("turn"), "playerTurnCount": battle.get("playerTurnCount"),
                      "activeHeroId": state.get("activeHeroId"), "skillTypeId": skill_type_id,
                      "targetId": target_id, "rule": rule, "ackStatus": status, "executed": executed})

    def telemetry(self) -> dict[str, Any] | None:
        if self.generation is None:
            return None
        return {"status": self.status, "reason": self.reason, "fromOpening": self.from_opening,
                "folder": self.folder.name if self.folder else None, "records": self.records,
                "states": self.states}


def recent_captures(limit: int = 5, root: Path | None = None) -> list[dict[str, Any]]:
    """Newest-first saved capture folders, for diagnostics."""
    base = root or WORK_ROOT
    try:
        folders = sorted((item for item in base.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    result = []
    for folder in folders:
        trace = folder / "live-trace.jsonl"
        try:
            lines = sum(1 for _ in open(trace, encoding="utf-8")) if trace.is_file() else 0
        except OSError:
            lines = 0
        result.append({"folder": folder.name, "saved": (folder / "battle-setup.json").is_file(),
                       "traceRecords": lines})
    return result


if __name__ == "__main__":
    json.dump(recent_captures(20), sys.stdout, ensure_ascii=False, indent=1)
    print()

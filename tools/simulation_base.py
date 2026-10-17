"""What a simulation's base battle may lack: this rotation's boss and this account's team level.

A simulation builds on a saved battle (its seed, settings and stage). Two of its
facts can be out of date for the battle being planned:

* the rotation: stage ids are 80R900D (Hydra) and 130R900D (Chimera), R the
  rotation (the Hydra's heads, the Chimera's attributes). The team screen shows
  the current stage (lifecycle selection.stageId); when the game has shown a
  newer rotation than the base battle's, the boss is rebuilt on it
  (boss_stages.stage_battle_setup, checked against real battles of both
  rotations for both bosses, JSON text identical);
* the team level: the player side's `f.l` adds a few percent of HP, ATK and DEF
  (a battle with f.l 25 instead of 23 gives the heroes +1.5-2%). A swapped-in
  team takes it from its own account's newest saved battle (`f.i` = account id).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable

from boss_stages import difficulty_of, stage_with_difficulty
from strategy_storage import atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
CURRENT_STAGES = PROJECT_ROOT / "cache" / "current-stages.json"
BOSS_MODES = ("chimera", "hydra")


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def rotation_of(stage_id: Any) -> int | None:
    """The rotation digit of a Hydra or Chimera stage id, None for anything else."""
    if not _integer(stage_id) or stage_id < 1_000_000:
        return None
    return (stage_id // 10_000) % 10


def battle_facts(setup: Any) -> dict[str, Any]:
    """Owner (account id), team level and battle time (epoch seconds) of a saved BattleSetup."""
    first = setup[0] if isinstance(setup, list) and setup else setup
    if not isinstance(first, dict):
        return {}
    team = first.get("f") if isinstance(first.get("f"), dict) else {}
    facts: dict[str, Any] = {}
    if _integer(team.get("i")):
        facts["ownerId"] = team["i"]
    if _integer(team.get("l")):
        facts["teamLevel"] = team["l"]
    if _integer(first.get("t")) and first["t"] > 0:
        facts["battleTime"] = first["t"] / 1000
    return facts


def read_battle_facts(folder: Path) -> dict[str, Any]:
    try:
        return battle_facts(json.loads((folder / "battle-setup.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def rotation_stage(base_stage: Any, base_time: float | None, current: dict[str, Any] | None) -> int | None:
    """The current rotation's stage on the base battle's difficulty, when the game showed a
    newer rotation than the base battle's; None keeps the base battle's own."""
    if not isinstance(current, dict) or not _integer(base_stage):
        return None
    seen, seen_at = current.get("stageId"), current.get("seenAt")
    if rotation_of(seen) is None or rotation_of(base_stage) is None or rotation_of(seen) == rotation_of(base_stage):
        return None
    if base_time is not None and (not isinstance(seen_at, (int, float)) or seen_at <= base_time):
        return None  # the base battle is newer than what the game last showed
    return stage_with_difficulty(seen, difficulty_of(base_stage))


class CurrentStages:
    """The boss stage the team screen last showed, per boss (it names the current rotation).
    Saved when it changes, so a restart keeps it; `seenAt` is when that stage was first seen."""

    def __init__(self, path: Path = CURRENT_STAGES) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.values: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.values is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            self.values = {mode: dict(value) for mode, value in (raw if isinstance(raw, dict) else {}).items()
                           if mode in BOSS_MODES and isinstance(value, dict) and _integer(value.get("stageId"))
                           and isinstance(value.get("seenAt"), (int, float))}
        return self.values

    def remember(self, boss_mode: Any, stage_id: Any, now: float | None = None) -> None:
        if boss_mode not in BOSS_MODES or rotation_of(stage_id) is None:
            return
        with self.lock:
            values = self._load()
            if (values.get(boss_mode) or {}).get("stageId") == stage_id:
                return
            values[boss_mode] = {"stageId": stage_id, "seenAt": time.time() if now is None else now}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.path, values)
            except OSError:
                pass  # kept in memory for this session

    def latest(self, boss_mode: str) -> dict[str, Any] | None:
        with self.lock:
            value = self._load().get(boss_mode)
            return dict(value) if value else None


def account_team_level(account_id: Any, roots: Iterable[Path]) -> int | None:
    """The team level (`f.l`) of the account's newest saved battle of either boss, None without one."""
    try:
        wanted = int(account_id)
    except (TypeError, ValueError):
        return None
    folders: list[Path] = []
    for root in roots:
        try:
            folders.extend(item for item in root.iterdir() if (item / "battle-setup.json").is_file())
        except OSError:
            continue
    for folder in sorted(folders, key=lambda item: item.stat().st_mtime, reverse=True):
        facts = read_battle_facts(folder)
        if facts.get("ownerId") == wanted and _integer(facts.get("teamLevel")):
            return facts["teamLevel"]
    return None


def base_notes(row: dict[str, Any], boss_mode: str, current: dict[str, Any] | None, account_id: Any,
               account_level: int | None, stages: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fields for a base-battle row of the simulation panel: the rotation the simulation will use
    when the game has moved on (with the Hydra's heads), and whether the base battle belongs to
    another account whose team level this account cannot replace."""
    notes: dict[str, Any] = {}
    target = rotation_stage(row.get("stageId"), row.get("battleTime"), current)
    if target is not None:
        notes["rotationStageId"] = target
        stage = ((stages or {}).get("stages") or {}).get(str(target))
        if boss_mode == "hydra" and isinstance(stage, dict):
            units = stage.get("units") or []
            notes["rotationHeadTypeIds"] = [unit["i"] for unit in units if not unit.get("reserve")] \
                + [unit["i"] for unit in units if unit.get("reserve")]
    owner = row.get("ownerId")
    if _integer(owner) and str(owner) != str(account_id or "") and account_id:
        notes["otherAccount"] = True
        notes["teamLevelKnown"] = account_level is not None
    return notes

"""What a simulation's base battle may lack: the current rotation and the account's team level (simulation_base)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from simulation_base import (CurrentStages, account_team_level, base_notes, battle_facts, rotation_of,
                             rotation_stage)


def setup(owner: int, level: int, time_ms: int = 1_000_000_000_000) -> list[dict]:
    return [{"i": 8039003, "t": time_ms, "f": {"i": owner, "l": level, "h": [{"i": 1}]}, "s": {"h": []}}]


def test_rotation_is_the_stage_id_digit_of_both_bosses() -> None:
    assert rotation_of(8049002) == 4 and rotation_of(8039003) == 3
    assert rotation_of(13019005) == 1 and rotation_of(13049006) == 4
    assert rotation_of(26050) is None and rotation_of(True) is None and rotation_of("8049002") is None


def test_battle_facts_name_owner_level_and_time() -> None:
    assert battle_facts(setup(154445582, 23)) == {"ownerId": 154445582, "teamLevel": 23, "battleTime": 1_000_000_000.0}
    assert battle_facts(None) == {} and battle_facts([{"f": {}}]) == {}


def test_a_newer_rotation_seen_in_the_game_replaces_the_base_battles() -> None:
    newer = {"stageId": 8049002, "seenAt": 2_000_000_000.0}
    # Same difficulty as the base battle (Brutal, 3), the rotation the game showed (4).
    assert rotation_stage(8039003, 1_000_000_000.0, newer) == 8049003
    assert rotation_stage(8039003, None, newer) == 8049003  # no battle time: the game's stage wins
    assert rotation_stage(13019005, 1_000_000_000.0, {"stageId": 13049006, "seenAt": 2e9}) == 13049005
    # Seen before the base battle, the same rotation, nothing seen: the base battle stays.
    assert rotation_stage(8039003, 3_000_000_000.0, newer) is None
    assert rotation_stage(8049003, 1_000_000_000.0, newer) is None
    assert rotation_stage(8039003, 1_000_000_000.0, None) is None
    assert rotation_stage(None, 1_000_000_000.0, newer) is None


def test_the_current_stage_is_saved_when_it_changes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "current-stages.json"
        stages = CurrentStages(path)
        assert stages.latest("hydra") is None
        stages.remember("hydra", 8039003, now=100.0)
        stages.remember("hydra", 8039003, now=200.0)  # the same stage keeps when it was first seen
        assert stages.latest("hydra") == {"stageId": 8039003, "seenAt": 100.0}
        stages.remember("hydra", 8049002, now=300.0)
        stages.remember("chimera", 13019005, now=400.0)
        for ignored in (("hydra", None), ("hydra", 26050), ("arena", 8049002)):
            stages.remember(*ignored, now=500.0)
        reloaded = CurrentStages(path)
        assert reloaded.latest("hydra") == {"stageId": 8049002, "seenAt": 300.0}
        assert reloaded.latest("chimera") == {"stageId": 13019005, "seenAt": 400.0}
        path.write_text("not json", encoding="utf-8")
        assert CurrentStages(path).latest("hydra") is None


def test_the_team_level_comes_from_the_accounts_newest_battle_of_either_boss() -> None:
    import os
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name, owner, level, modified in (("a", 154445582, 22, 100), ("b", 154445582, 23, 200),
                                              ("c", 95815853, 25, 300)):
            folder = root / ("hydra" if name != "b" else "chimera") / name
            folder.mkdir(parents=True)
            (folder / "battle-setup.json").write_text(json.dumps(setup(owner, level)), encoding="utf-8")
            os.utime(folder, (modified, modified))
        roots = [root / "hydra", root / "chimera", root / "missing"]
        assert account_team_level("154445582", roots) == 23
        assert account_team_level(95815853, roots) == 25
        assert account_team_level("1", roots) is None and account_team_level(None, roots) is None


def test_base_notes_tell_the_panel_what_the_simulation_changes() -> None:
    stages = {"stages": {"8049003": {"units": [{"i": 26050, "reserve": False}, {"i": 26180, "reserve": True},
                                               {"i": 26230, "reserve": False}]}}}
    row = {"stageId": 8039003, "battleTime": 1e9, "ownerId": 95815853}
    current = {"stageId": 8049002, "seenAt": 2e9}
    notes = base_notes(row, "hydra", current, "154445582", None, stages)
    assert notes == {"rotationStageId": 8049003, "rotationHeadTypeIds": [26050, 26230, 26180],
                     "otherAccount": True, "teamLevelKnown": False}
    assert base_notes(row, "hydra", current, "154445582", 23, stages)["teamLevelKnown"] is True
    assert base_notes(row, "chimera", None, "95815853", None) == {}
    assert base_notes({**row, "ownerId": None}, "hydra", None, "154445582", None) == {}

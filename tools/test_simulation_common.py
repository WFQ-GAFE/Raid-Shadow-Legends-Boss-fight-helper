"""Battle snapshots and run files shared by the strategy simulations."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from simulation_common import action_uses, battle_snapshot, read_run, rule_index_by_name, write_run


def test_snapshot_folds_stacks_and_keeps_what_the_turn_log_shows() -> None:
    state = {
        "heroes": [{"id": 0, "typeId": 100, "healthPct": 55.55, "dead": False, "currentFormIndex": 1,
                    "effects": [{"effectTypeId": 80, "turnsLeft": 2}, {"effectTypeId": 80, "turnsLeft": 3},
                                {"effectTypeId": 150, "turnsLeft": 1}, {"effectTypeId": 0}],
                    "skills": [{"typeId": 1001, "cooldown": 0}, {"typeId": 1002, "cooldown": 3}]},
                   {"id": 1, "typeId": 200, "healthPct": 0, "dead": True, "effects": []}],
        "bosses": [{"id": 8, "typeId": 26040, "healthPct": 80.0, "effects": [], "devouredHeroId": 1,
                    "isHydraNeck": True, "skills": [{"typeId": 9, "cooldown": 1}]}],
    }
    actors = battle_snapshot(state)["actors"]
    assert actors[0] == {"id": 0, "t": 100, "s": "a", "hp": 55.5 if round(55.55, 1) == 55.5 else 55.6,
                         "fx": [[80, 3, 2], [150, 1, 1]], "f": 1, "cd": [[1001, 0], [1002, 3]]}
    assert actors[1]["d"] == 1 and "cd" not in actors[1]
    assert actors[2] == {"id": 8, "t": 26040, "s": "e", "hp": 80.0, "fx": [], "dv": 1, "neck": 1}


def test_run_files_are_compressed_and_older_reports_still_read() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        write_run(folder, 3, {"status": "complete", "text": "奇美拉"})
        assert (folder / "run-03.json.gz").is_file()
        assert read_run(folder, 3) == {"status": "complete", "text": "奇美拉"}
        (folder / "run-04.json").write_text(json.dumps({"status": "partial"}), encoding="utf-8")
        assert read_run(folder, 4) == {"status": "partial"} and read_run(folder, 5) is None


def test_rule_labels_resolve_to_the_rule_they_extend() -> None:
    strategy = {"rules": [{"name": "马里斯"}, {"name": "马里斯 · 默认技能顺序"}]}
    assert rule_index_by_name(strategy, "马里斯 · 默认技能顺序 · Ram形态首回合技能") == 2
    assert rule_index_by_name(strategy, "其他") is None


def test_action_uses_name_what_an_action_set_off() -> None:
    # An ally attack in the engine's compact form (src/offline_runtime/command_breakdown.hpp).
    raw = [[2, 95102, 8, "input", 10222.4], [5, 100501, 8, "team", 76838.7], [2, 0, -1, "other", 27662.0],
           [-1, 0, -1, "other", 3686.2], [3, 1, 2, "unknown", 5.0], "bad", [1, 2, 3]]
    assert action_uses(raw) == [
        {"actorId": 2, "skillTypeId": 95102, "targetId": 8, "trigger": "input", "damage": 10222},
        {"actorId": 5, "skillTypeId": 100501, "targetId": 8, "trigger": "team", "damage": 76839},
        {"actorId": 2, "skillTypeId": 0, "targetId": -1, "trigger": "other", "damage": 27662},
        {"actorId": -1, "skillTypeId": 0, "targetId": -1, "trigger": "other", "damage": 3686},
    ]
    assert action_uses(None) == [] and action_uses([]) == []

"""Boss skill names and descriptions for enemy actions in simulation reports."""
import tempfile
from pathlib import Path

from boss_skills import BossSkillCatalog, enemy_skill_ids, listed_skills


def test_catalog_learns_and_persists_boss_skills() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "boss-skills.json"
        catalog = BossSkillCatalog(path)
        timeline = [
            {"source": "enemy", "skillTypeId": 260401}, {"source": "policy", "skillTypeId": 99501},
            {"source": "enemy", "skillTypeId": 260007}, {"source": "enemy", "skillTypeId": 260401},
        ]
        assert enemy_skill_ids(timeline) == [260007, 260401]
        heads = [{"typeId": 26040, "skills": [{"typeId": 260401, "name": "苦痛巨颌", "description": "攻击<color=#1ee600>x</color>",
                                               "defaultCooldown": 0}]}]
        assert catalog.learn(listed_skills(heads), static=True)
        assert not catalog.learn(listed_skills(heads), static=True)
        # A live state only fills gaps: a name for an unknown skill, never over static text.
        assert catalog.learn([{"typeId": 260007, "name": "吞噬"}, {"typeId": 260401, "name": "别名"}])
        assert catalog.lookup([260401])["260401"] == {"name": "苦痛巨颌", "description": "攻击<color=#1ee600>x</color>",
                                                       "defaultCooldown": 0}
        assert catalog.missing(enemy_skill_ids(timeline)) == [260007]
        # The static data's answer, including skills it does not have (never asked again).
        catalog.learn([{"typeId": 260007, "name": "吞噬", "description": "吞下被标记的英雄", "defaultCooldown": 0},
                       {"typeId": 260011}], static=True)
        assert catalog.missing([260007, 260011, 260401]) == []
        assert "260011" not in catalog.lookup([260011])
        reloaded = BossSkillCatalog(path)
        assert reloaded.lookup([260007, 260401]) == catalog.lookup([260007, 260401])
        assert reloaded.missing([260011, 1266901]) == [1266901]


def test_boss_skills_inside_actions_count_when_the_actors_are_known() -> None:
    timeline = [{"source": "policy", "skillTypeId": 99002,
                 "uses": [{"actorId": 1, "skillTypeId": 99002, "trigger": "input"},
                          {"actorId": 6, "skillTypeId": 260401, "trigger": "provoke"}]}]
    actors = [{"actorId": 1, "player": True}, {"actorId": 6, "player": False}]
    assert enemy_skill_ids(timeline) == []
    assert enemy_skill_ids(timeline, actors) == [260401]

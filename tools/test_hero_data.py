from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import hero_data
from hero_data import HeroDataService, compact, max_level_stats


def fx(value: float) -> dict[str, int]:
    return {"m_rawValue": int(round(value * 2 ** 32))}


def raw_report() -> dict:
    """A trimmed hero-data report: Sydax (10540) with ascension 3 and 6, as the probe writes it."""
    stats = {"Health": fx(106), "Attack": fx(74), "Defence": fx(120), "Speed": fx(99), "Resistance": fx(30),
             "CriticalChance": fx(15), "CriticalDamage": fx(50)}
    skill_a1 = {"Id": 105401, "Targets": {"hasValue": True, "value": "AliveEnemies"},
                "SkillLevelBonuses": [{"Value": fx(0.05)}, {"SkillBonusType": "EffectChance", "Value": fx(0.1)}],
                "Effects": [
                    {"Id": 1, "KindId": "Damage", "MultiplierFormula": "3.9*DEF"},
                    {"Id": 2, "KindId": "ApplyDebuff", "TargetParams": {"TargetType": "RelationTarget"},
                     "Relation": {"EffectTypeId": {"hasValue": True, "value": 1}},
                     "Chance": {"hasValue": True, "value": fx(0.8)},
                     "ApplyStatusEffectParams": {"StatusEffectInfos": [{"TypeId": 131, "Duration": 2}]}}]}
    skill_a2 = {"Id": 105402, "Cooldown": 4, "Targets": {"hasValue": True, "value": "AliveEnemies"},
                "SkillLevelBonuses": [{"SkillBonusType": "CooltimeTurn", "Value": fx(1)}],
                "Effects": [
                    {"Id": 3, "KindId": "Damage", "TargetParams": {"TargetType": "AllEnemies"},
                     "MultiplierFormula": "1.5*DEF+0.035*TRG_HP"},
                    {"Id": 4, "KindId": "ApplyDebuff", "TargetParams": {"TargetType": "RelationTarget"},
                     "Relation": {"EffectTypeId": {"hasValue": True, "value": 3}},
                     "ApplyStatusEffectParams": {"StatusEffectInfos": [{"TypeId": 151, "Duration": 2}]}},
                    {"Id": 5, "KindId": "ChangeDefenceModifier", "MultiplierFormula": "0.2"},
                    {"Id": 6, "KindId": "IncreaseStamina", "TargetParams": {"TargetType": "AllAllies"}}]}
    skill_a3 = {"Id": 105403, "Cooldown": 5, "Targets": {"hasValue": True, "value": "AliveAllies"},
                "Effects": [
                    {"Id": 7, "KindId": "ApplyBuff", "TargetParams": {"TargetType": "AllAllies"},
                     "Condition": "true",
                     "ApplyStatusEffectParams": {"StatusEffectInfos": [{"TypeId": 141, "Duration": 2}]}},
                    {"Id": 8, "KindId": "IgnoreDefenceModifier", "MultiplierFormula": "-0.5"},
                    {"Id": 9}]}  # EffectKindId 0: Revive
    passive = {"Id": 105404, "Group": "Passive", "Effects": [
        {"Id": 10, "KindId": "Heal", "TargetParams": {"TargetType": "Producer"}}]}
    hidden = {"Id": 105405, "Visibility": "HiddenOnHud"}
    base = {"Id": 10540, "Fraction": "DarkElves", "Rarity": "Legendary",
            "LeaderSkills": [{"StatKindId": "Defence", "Amount": fx(0.3)},
                             {"StatKindId": "Accuracy", "IsAbsolute": True, "Amount": fx(70),
                              "Area": {"hasValue": True, "value": "Arena"}}],
            "Forms": [{"Element": "Spirit", "Role": "Defense", "BaseStats": stats,
                       "SkillTypeIds": [105401, 105402, 105403]}]}
    ascended = json.loads(json.dumps(base))
    ascended["Id"] = 10546
    ascended["Forms"][0]["BaseStats"]["Resistance"] = fx(40)
    ascended["Forms"][0]["SkillTypeIds"].append(105404)
    return {
        "heroes": [
            {"id": 10540, "hero": base, "skills": [skill_a1, skill_a2, skill_a3]},
            {"id": 10546, "hero": ascended, "skills": [105401, 105402, 105403, passive, hidden]},
        ],
        "texts": {"l10n:hero-type/name?id=10540#static": "弑君者赛达克斯",
                  "l10n:area/name?id=3#static": "竞技场", "l10n:area/name?id=8#static": "六头蛇",
                  "l10n:skill/name?id=105401#static": "血肉切割",
                  "l10n:skill/description?id=105401#static": "攻击1名敌人。"},
        "effectTypes": [{"Id": 130, "KindId": "StatusReduceAttack", "MultiplierFormula": "0.25*TRG_B_ATK"},
                        {"Id": 131, "KindId": "StatusReduceAttack", "MultiplierFormula": "0.5*TRG_B_ATK"},
                        {"Id": 141, "KindId": "StatusIncreaseDefence", "MultiplierFormula": "0.6*TRG_B_DEF"},
                        {"Id": 151, "KindId": "Enfeeble"}],
        "labels": {"l10n:battle-hud/effect-kindId?id=statusreduceattack#label": "降低攻击",
                   "l10n:status-effects/effect-kindId?id=statusreduceattack#description": "降低斗士的战斗攻击力25%或50%。",
                   "l10n:battle-hud/effect-kindId?id=statusincreasedefence#label": "增加防御",
                   "l10n:battle-hud/effect-kindId?id=enfeeble#label": "衰弱",
                   "l10n:status-effects/effect-kindId?id=enfeeble#description": "enfeeble ???",
                   "l10n:hero/fraction?name=DarkElves#label": "暗黑精灵", "l10n:hero/element/spirit#label": "精神",
                   "l10n:hero/role/defence#label": "防御", "l10n:hero/rarity/legendary#label": "传说",
                   "l10n:common/hero-stats/short/critChance#label": "暴击率"},
        "enums": {"SharedModel.Battle.Effects.StatusEffectTypeId": {"131": "DecreaseAttack50", "141": "IncreaseDefence60",
                                                                   "151": "DecreaseDefence60", "999": "Unused"},
                  "SharedModel.Meta.Stages.AreaTypeId": {"3": "Arena", "8": "Hydra"}},
    }


def test_max_level_stats_match_the_hero_screen() -> None:
    # Autumn Ava (7376), ascension 6: the hero screen shows 19650 / 936 / 1332, speed 110.
    stats = {"Health": fx(119), "Attack": fx(85), "Defence": fx(121), "Speed": fx(110), "Resistance": fx(40),
             "CriticalChance": fx(15), "CriticalDamage": fx(50)}
    assert max_level_stats(stats) == [19650, 936, 1332, 110, 40, 0, 15, 50]


def test_compact_keeps_search_tags_and_profile() -> None:
    data = compact(raw_report())
    hero = data["heroes"]["10540"]
    assert "10546" not in data["heroes"]
    assert hero["name"] == "弑君者赛达克斯" and hero["faction"] == "DarkElves" and hero["rarity"] == "Legendary"
    assert hero["forms"] == [{"element": "Spirit", "role": "Defense", "stats": max_level_stats(
        raw_report()["heroes"][0]["hero"]["Forms"][0]["BaseStats"]), "skills": [105401, 105402, 105403]}]
    assert hero["aura"] == [{"stat": "Defence", "value": 30, "absolute": False},
                            {"stat": "Accuracy", "value": 70, "absolute": True, "area": "Arena"}]
    assert hero["ascension"]["6"]["skills"] == [[105404]]
    assert hero["ascension"]["6"]["stats"][0][4] == 40

    a1, a2, a3 = (data["skills"][str(skill)] for skill in (105401, 105402, 105403))
    assert a1["name"] == "血肉切割" and a1["desc"] == "攻击1名敌人。"
    assert a1["damage"] == ["3.9*DEF"] and a1["scaling"] == ["DEF"] and "aoe" not in a1
    assert a1["debuffs"] == [[131, 2, 80, "target", False]]
    assert a1["books"] == {"Attack": 5, "EffectChance": 10}
    # The debuff follows its AoE hit; a positive defence modifier is not "ignore DEF".
    assert a2["cd"] == 4 and a2["aoe"] is True and a2["scaling"] == ["DEF", "TRG_HP"]
    assert a2["damageScopes"] == ["all"] and "damageScopes" not in a1
    assert a2["debuffs"] == [[151, 2, None, "all", False]]
    assert a2["special"] == ["IncreaseStamina"] and a2["books"] == {"CooltimeTurn": 1}
    assert a3["buffs"] == [[141, 2, None, "all", True]]
    assert a3["special"] == ["IgnoreDefence", "Revive"]
    assert data["skills"]["105404"]["passive"] is True and data["skills"]["105404"]["special"] == ["Heal"]
    assert data["skills"]["105405"]["hidden"] is True
    # The game's names; strengths only where a kind has several; placeholder descriptions left out.
    assert data["statusEffects"] == {
        "131": {"native": "DecreaseAttack50", "kind": "StatusReduceAttack", "name": "降低攻击",
                "desc": "降低斗士的战斗攻击力25%或50%。", "strength": 50},
        "141": {"native": "IncreaseDefence60", "kind": "StatusIncreaseDefence", "name": "增加防御"},
        "151": {"native": "DecreaseDefence60", "kind": "Enfeeble", "name": "衰弱"},
    }
    assert data["labels"] == {"faction": {"DarkElves": "暗黑精灵"}, "rarity": {"Legendary": "传说"},
                              "element": {"Spirit": "精神"}, "role": {"Defense": "防御"},
                              "stat": {"CriticalChance": "暴击率"}, "area": {"Arena": "竞技场", "Hydra": "六头蛇"}}


def wait_ready(service: HeroDataService, pid: int | None = None) -> dict:
    for _ in range(200):
        snapshot = service.snapshot(pid)
        if snapshot["status"] != "building":
            return snapshot
        time.sleep(0.01)
    raise AssertionError("hero data never finished building")


def test_service_builds_once_per_bundle_and_serves_the_cache() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "hero-data"
        bundle = Path(folder) / "bundle-abc"
        bundle.mkdir()
        calls = []

        def runner(probe: Path, directory: Path) -> dict:
            calls.append((probe.name, directory))
            return raw_report()

        service = HeroDataService(root, bundle_provider=lambda pid: bundle, runner=runner)
        assert service.snapshot(None)["status"] == "building"
        snapshot = wait_ready(service)
        assert snapshot["status"] == "ready" and "10540" in snapshot["data"]["heroes"]
        assert calls == [("raid_offline_probe.exe", bundle)]
        assert (root / "bundle-abc.json").is_file()

        # A new service (next start) uses the file without running the probe again.
        again = HeroDataService(root, bundle_provider=lambda pid: bundle, runner=runner)
        assert again.snapshot(None)["status"] == "ready"
        again.checked_at = 0.0
        wait_ready(again)
        time.sleep(0.05)
        assert len(calls) == 1

        # Without the game the cached data is still served.
        def missing(pid: int | None) -> Path:
            raise RuntimeError("找不到游戏安装目录；请先打开游戏")

        offline = HeroDataService(root, bundle_provider=missing, runner=runner)
        assert offline.snapshot(None)["status"] == "ready"


def test_service_reports_why_data_is_unavailable() -> None:
    with tempfile.TemporaryDirectory() as folder:
        def missing(pid: int | None) -> Path:
            raise RuntimeError("找不到游戏安装目录；请先打开游戏")

        service = HeroDataService(Path(folder), bundle_provider=missing, runner=lambda probe, bundle: {})
        snapshot = wait_ready(service)
        assert snapshot == {"status": "unavailable", "reason": "找不到游戏安装目录；请先打开游戏"}
        assert hero_data.RECHECK_SECONDS > 15

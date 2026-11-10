"""Default rules for champions a strategy has none for (simulation_rules.py)."""
from __future__ import annotations

import chimera_controller as controller
from simulation_rules import champion, default_order, with_default_rules

HERO_DATA = {
    "heroes": {
        "9800": {"name": "库罗莎", "forms": [{"skills": [98001, 98002, 98003, 98004]}],
                 "catalogForms": {"0": [[98001, 98002, 98003]], "6": [[98001, 98002, 98003, 98004]]}},
        "9600": {"name": "西奥多西娅", "forms": [{"skills": [96001, 96002]}, {"skills": [896001, 896002]}]},
    },
    "skills": {
        "98001": {"name": "A1"}, "98002": {"name": "A2", "cd": 4}, "98003": {"name": "A3", "cd": 5},
        "98004": {"name": "A4", "passive": True},
        "96001": {}, "96002": {"cd": 4}, "896001": {}, "896002": {"cd": 4, "hidden": False},
    },
}


def test_champion_ids_drop_the_ascension_level() -> None:
    assert [champion(9806), champion(9800), champion(26906), champion(9810)] == [9800, 9800, 26900, 9810]


def test_the_default_order_puts_long_cooldowns_first_and_skips_passives() -> None:
    assert [(entry["skillTypeId"], entry["skillSlot"], entry["formIndex"]) for entry in default_order(HERO_DATA, 9806)] == [
        (98003, 3, 0), (98002, 2, 0), (98001, 1, 0)]
    # Every form of a mythical champion, the base form first among equals.
    assert [(entry["skillTypeId"], entry["formIndex"]) for entry in default_order(HERO_DATA, 9600)] == [
        (96002, 0), (896002, 1), (96001, 0), (896001, 1)]
    assert default_order(HERO_DATA, 1234) is None


def test_only_champions_without_a_rule_get_one_and_the_strategy_validates() -> None:
    config = {"executionMode": "list", "objectives": {}, "rules": [
        {"name": "库罗莎 · 自定义", "when": {"activeHeroTypeId": [9800, 9806]},
         "action": {"type": "cast", "skillTypeId": 98003, "skillSlot": 3, "target": {"type": "self"}}}]}
    result, added = with_default_rules(config, [9806, 9606, 1234, 9600], HERO_DATA)
    assert added == [9600] and len(result["rules"]) == 2 and len(config["rules"]) == 1
    rule = result["rules"][1]
    assert rule["name"] == "西奥多西娅 · 默认技能顺序" and rule["when"] == {"activeHeroTypeId": list(range(9600, 9607))}
    controller.validate_strategy_config(result)
    unchanged, none = with_default_rules(config, [9806], HERO_DATA)
    assert unchanged is config and none == []

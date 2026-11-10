"""Rules for champions a strategy has none for, so a simulation with a chosen team can play.

A simulation follows the rules strictly: a champion without a rule stops the run. A team
picked for a simulation ("custom", team_setups.simulation_team) may bring champions the
strategy never mentions; each gets a default skill order made from the game's static data
(tools/hero_data.py): active skills with the longest cooldown first, A1 last, every form of
a mythical champion. The strategy's own rules are kept as they are, first.
"""
from __future__ import annotations

import copy
from typing import Any

DEFAULT_ORDER_NAME = "默认技能顺序"  # the editor's name for a default rule (the UI translates it)


def champion(type_id: int) -> int:
    """The base id: battles and rosters add the ascension level (1-6)."""
    level = type_id % 10
    return type_id - level if 1 <= level <= 6 else type_id


def _named_champions(rule: dict[str, Any]) -> set[int]:
    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    values = when.get("activeHeroTypeId")
    values = values if isinstance(values, list) else [values]
    return {champion(value) for value in values if isinstance(value, int) and not isinstance(value, bool)}


def default_order(hero_data: dict[str, Any], type_id: int) -> list[dict[str, Any]] | None:
    """prioritySkills for a champion at its ascension level, or None when the data does not know it."""
    hero = (hero_data.get("heroes") or {}).get(str(champion(type_id)))
    if not isinstance(hero, dict):
        return None
    forms = (hero.get("catalogForms") or {}).get(str(type_id - champion(type_id))) \
        or (hero.get("catalogForms") or {}).get("0") \
        or [form.get("skills") or [] for form in hero.get("forms") or []]
    skills = hero_data.get("skills") or {}
    entries = []
    for form_index, form in enumerate(forms):
        for slot, skill_type_id in enumerate(form, 1):
            skill = skills.get(str(skill_type_id)) or {}
            if not isinstance(skill_type_id, int) or skill.get("passive") or skill.get("hidden"):
                continue
            entries.append((skill.get("cd") or 0, slot, form_index, skill_type_id))
    entries.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
    seen: set[int] = set()
    order = []
    for _, slot, form_index, skill_type_id in entries:
        if skill_type_id in seen:
            continue
        seen.add(skill_type_id)
        order.append({"skillTypeId": skill_type_id, "skillSlot": slot, "formIndex": form_index, "isTransform": False,
                      "target": {"type": "auto"}})
    return order or None


def with_default_rules(config: dict[str, Any], type_ids: list[int], hero_data: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    """The strategy with a default rule for each champion of the team it has no rule for.

    Returns the new strategy and the champions that got one (those the data does not know
    are left out: their run stops, as it would live).
    """
    rules = [rule for rule in config.get("rules") or [] if isinstance(rule, dict)]
    covered = set().union(*(_named_champions(rule) for rule in rules)) if rules else set()
    added_rules, added = [], []
    for type_id in type_ids:
        if not isinstance(type_id, int) or champion(type_id) in covered or champion(type_id) in added:
            continue
        order = default_order(hero_data, type_id)
        if order is None:
            continue
        base = champion(type_id)
        name = ((hero_data.get("heroes") or {}).get(str(base)) or {}).get("name") or str(base)
        added_rules.append({"name": f"{name} · {DEFAULT_ORDER_NAME}", "when": {"activeHeroTypeId": list(range(base, base + 7))},
                            "action": {"type": "defaultSkillPriority", "prioritySkills": order,
                                       "blockedSkillTypeIds": [], "reserveStrictRuleSkills": True}})
        added.append(base)
    if not added_rules:
        return config, []
    result = copy.deepcopy(config)
    result["rules"] = [*copy.deepcopy(rules), *added_rules]
    return result, added

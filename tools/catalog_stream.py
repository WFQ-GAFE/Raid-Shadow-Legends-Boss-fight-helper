"""Keep static catalog identities separate from per-turn state."""
from typing import Any
import copy


def merge_skill_catalog(incoming: list, previous: list | None = None) -> list[dict]:
    """One choice per observed HUD position and form; fresh observations win.

    Ascension can replace a skill TypeId without adding a fourth button.
    Never collapse by name: different buttons/forms may legitimately share it.
    """
    incoming = incoming if isinstance(incoming, list) else []
    previous = previous if isinstance(previous, list) else []
    excluded = {row.get('typeId') for row in incoming if isinstance(row, dict) and
                (row.get('activeSkill') is False or row.get('hiddenOnHud') is True or row.get('passive') is True)}
    positions = set()
    identities = set()
    result = []
    for row in incoming + previous:
        if not isinstance(row, dict) or row.get('typeId') in excluded:
            continue
        if row.get('activeSkill') is False or row.get('hiddenOnHud') is True or row.get('passive') is True:
            continue
        form = row.get('formIndex', 0) or 0
        slot = row.get('slot')
        position = (form, slot) if type(slot) is int and slot > 0 else None
        identity = (form, row.get('typeId')) if type(row.get('typeId')) is int else None
        if (position is not None and position in positions) or (identity is not None and identity in identities):
            continue
        result.append(copy.deepcopy(row))
        if position is not None: positions.add(position)
        if identity is not None: identities.add(identity)
    return result

ENTITY_KEYS = frozenset({"typeId", "name", "nameKey", "avatar", "isMetamorph", "runtimeTypeIds", "resourceKind"})
SKILL_KEYS = frozenset({"slot", "typeId", "name", "nameKey", "description", "descriptionKey", "icon", "formIndex", "defaultCooldown", "activeSkill", "hiddenOnHud", "effectSummary", "isTransform"})


def static_entity(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: item for key, item in value.items() if key in ENTITY_KEYS}
    if isinstance(value.get("skills"), list):
        result["skills"] = [
            {key: item for key, item in skill.items() if key in SKILL_KEYS}
            for skill in value["skills"] if isinstance(skill, dict)
        ]
    return result

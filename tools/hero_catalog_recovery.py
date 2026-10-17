"""Recover the UI champion catalog from copied, offline static data.

This module does no I/O, does not import a game/agent service, and never starts
a simulation. A caller supplies HeroDataService's successful compact snapshot,
persists the returned catalog and advances its catalog revision when changed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogRecovery:
    catalog: dict[int, dict[str, Any]]
    changed: bool
    added: int
    updated: int
    source_count: int


def _identifier(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value) if int(value) > 0 else None
    return None


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _unresolved_name(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    text = value.strip()
    return text.lower().startswith("l10n:") or bool(re.fullmatch(
        r"(?:champion|hero|skill|英雄|技能|斗士)\s*\d+(?:\s+(?:name|title))?", text, re.IGNORECASE))


def _ordered_forms(hero: dict[str, Any]) -> tuple[list[Any], list[int]]:
    """Use a complete ascended form, never append relative ascension deltas."""
    complete = hero.get("catalogForms")
    if isinstance(complete, dict):
        versions = [(int(key), value) for key, value in complete.items()
                    if str(key).isdigit() and 0 <= int(key) <= 6 and isinstance(value, list)
                    and value and all(isinstance(form, list) for form in value)]
        if versions:
            return max(versions, key=lambda pair: pair[0])[1], sorted(level for level, _ in versions)
    # Pre-schema-5 data cannot reveal the original positions of added/replaced
    # skills. The base kit is still useful; inventing an A4 from a delta is not.
    return [form.get("skills", []) for form in hero.get("forms", []) if isinstance(form, dict)], [0]


def _skills(hero: dict[str, Any], skills: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    forms, levels = _ordered_forms(hero)
    result: list[dict[str, Any]] = []
    for form_index, raw_ids in enumerate(forms):
        if not isinstance(raw_ids, list):
            continue
        ids = list(dict.fromkeys(identifier for raw in raw_ids if (identifier := _identifier(raw)) is not None))
        slot = 0
        for identifier in ids:
            detail = skills.get(str(identifier))
            if not isinstance(detail, dict) or detail.get("passive") is True or detail.get("hidden") is True:
                continue
            # A linked implementation skill follows the parent's book level;
            # it is not an additional HUD button when its parent is present.
            if _identifier(detail.get("levelOf")) in ids:
                continue
            slot += 1
            row: dict[str, Any] = {"typeId": identifier, "formIndex": form_index, "slot": slot,
                                   "activeSkill": True, "hiddenOnHud": False}
            for source, target in (("name", "name"), ("desc", "description"), ("cd", "defaultCooldown")):
                value = detail.get(source)
                if not _missing(value):
                    row[target] = copy.deepcopy(value)
            if "defaultCooldown" not in row:
                row["defaultCooldown"] = 0
            result.append(row)
    return result, levels


def catalog_from_hero_data(data: Any) -> dict[int, dict[str, Any]]:
    """Return named playable entries; malformed/empty snapshots yield no changes."""
    if not isinstance(data, dict) or not isinstance(data.get("heroes"), dict) \
            or not isinstance(data.get("skills"), dict):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for raw_id, raw in data["heroes"].items():
        identifier = _identifier(raw_id)
        if identifier is None or not isinstance(raw, dict) or _unresolved_name(raw.get("name")):
            continue
        if not isinstance(raw.get("forms"), list) or not raw["forms"]:
            continue
        skills, levels = _skills(raw, data["skills"])
        result[identifier] = {"typeId": identifier, "name": raw["name"].strip(),
                              "avatar": f"HeroAvatars/{identifier}",
                              "runtimeTypeIds": [identifier + level for level in levels],
                              "isMetamorph": len(raw["forms"]) > 1, "skills": skills}
    return result


def _merge_missing(existing: dict[str, Any], recovered: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(existing)
    for key, value in recovered.items():
        if _missing(result.get(key)) or (key == "name" and _unresolved_name(result.get(key))):
            if not _missing(value):
                result[key] = copy.deepcopy(value)
    return result


def _merge_skills(existing: Any, recovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous = [copy.deepcopy(row) for row in existing if isinstance(row, dict)] if isinstance(existing, list) else []
    positions: set[tuple[int, int]] = set()
    exact: dict[tuple[int, int], int] = {}
    for index, row in enumerate(previous):
        form = row.get("formIndex", 0)
        if not isinstance(form, int) or isinstance(form, bool):
            form = 0
        identifier = _identifier(row.get("typeId"))
        if identifier is not None:
            exact.setdefault((form, identifier), index)
        slot = _identifier(row.get("slot"))
        if slot is not None:
            positions.add((form, slot))
    for row in recovered:
        form, identifier, slot = row["formIndex"], row["typeId"], row["slot"]
        index = exact.get((form, identifier))
        if index is not None:
            previous[index] = _merge_missing(previous[index], row)
        elif (form, slot) not in positions:
            # Keep authoritative game slot/form order. A different ascension's
            # replacement at the same position must not become a second A3/A4.
            previous.append(copy.deepcopy(row))
            exact[(form, identifier)] = len(previous) - 1
            positions.add((form, slot))
    return previous


def merge_hero_catalog(existing: dict[int, dict[str, Any]], data: Any) -> CatalogRecovery:
    """Fill missing champions/fields without replacing live names, slots or forms.

    Existing input and snapshot are never mutated. The returned catalog is ready
    for canonicalize_hero_catalog/static_entity in the web service. Only the
    caller writes cache files or advances its revision.
    """
    recovered = catalog_from_hero_data(data)
    catalog = copy.deepcopy(existing)
    added = updated = 0
    for identifier, entry in recovered.items():
        previous = catalog.get(identifier)
        if not isinstance(previous, dict):
            catalog[identifier] = entry
            added += 1
            continue
        merged = _merge_missing(previous, {key: value for key, value in entry.items() if key != "skills"})
        aliases = previous.get("runtimeTypeIds")
        if isinstance(aliases, list):
            merged["runtimeTypeIds"] = list(dict.fromkeys([*aliases, *entry["runtimeTypeIds"]]))
        merged["skills"] = _merge_skills(previous.get("skills"), entry["skills"])
        if merged != previous:
            catalog[identifier] = merged
            updated += 1
    changed = bool(added or updated)
    return CatalogRecovery(catalog if changed else existing, changed, added, updated, len(recovered))

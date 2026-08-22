#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
RESOURCE_ROOT = Path(os.environ.get("CHIMERA_RESOURCE_ROOT", PROJECT_ROOT)).resolve()
ROTATION_ARCHIVE = RESOURCE_ROOT / "data" / "chimera-rotation-catalogs.json"
TRIAL_RECIPES = RESOURCE_ROOT / "data" / "chimera-trial-recipes.json"
SKILL_CAPABILITIES = RESOURCE_ROOT / "data" / "chimera-skill-capabilities.json"
UI_CATALOG_CACHE = PROJECT_ROOT / "cache" / "chimera-ui-catalog.json"
HERO_CATALOG_CACHE = PROJECT_ROOT / "cache" / "chimera-hero-catalog.json"


def is_unresolved_skill_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered.startswith("l10n:skill/"):
        return True
    if re.fullmatch(r"(?:技能|skill)\s*\d+", text, flags=re.IGNORECASE):
        return True
    parts = lowered.split()
    return (
        len(parts) == 3
        and parts[0] == "skill"
        and parts[1].isdigit()
        and parts[2] in {"name", "title"}
    )


def skill_display_name(skill: dict[str, Any]) -> str:
    name = str(skill.get("name") or "").strip()
    if not is_unresolved_skill_name(name):
        return name
    slot = skill.get("slot")
    if isinstance(slot, int) and not isinstance(slot, bool) and slot > 0:
        return f"技能 {slot}"
    type_id = skill.get("typeId")
    return f"技能 {type_id}" if isinstance(type_id, int) else "技能"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return copy.deepcopy(default)


def _source_signature() -> list[dict[str, Any]]:
    result = []
    for path in (ROTATION_ARCHIVE, TRIAL_RECIPES, SKILL_CAPABILITIES):
        if not path.is_file():
            continue
        stat = path.stat()
        result.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )
    return result


def _newest_entry(values: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    candidates = [
        (str(value.get("firstSeenUtc") or ""), fingerprint, value)
        for fingerprint, value in values.items()
        if isinstance(value, dict)
    ]
    if not candidates:
        return None, None
    _seen, fingerprint, value = max(candidates, key=lambda item: (item[0], item[1]))
    return fingerprint, value


def _build_ui_catalog(signature: list[dict[str, Any]]) -> dict[str, Any]:
    archive = _read_json(ROTATION_ARCHIVE, {})
    definition_fp, definition_entry = _newest_entry(archive.get("trialDefinitions", {}))
    reward_fp, reward_entry = _newest_entry(archive.get("rewardRotations", {}))
    definition = definition_entry.get("definition", {}) if definition_entry else {}
    difficulties = [
        copy.deepcopy(value)
        for value in definition.get("difficulties", [])
        if isinstance(value, dict)
    ]

    reward_by_difficulty: dict[int, dict[int, dict[str, Any]]] = {}
    rewards = reward_entry.get("rewards", {}) if reward_entry else {}
    for difficulty in rewards.get("difficulties", []):
        if not isinstance(difficulty, dict) or not isinstance(difficulty.get("difficultyId"), int):
            continue
        reward_by_difficulty[difficulty["difficultyId"]] = {
            trial["id"]: copy.deepcopy(trial["reward"])
            for trial in difficulty.get("trials", [])
            if isinstance(trial, dict)
            and isinstance(trial.get("id"), int)
            and isinstance(trial.get("reward"), dict)
        }
    for difficulty in difficulties:
        by_id = reward_by_difficulty.get(difficulty.get("difficultyId"), {})
        for trial in difficulty.get("trials", []):
            if isinstance(trial, dict) and trial.get("id") in by_id:
                trial["reward"] = copy.deepcopy(by_id[trial["id"]])

    recipe_payload = _read_json(TRIAL_RECIPES, {})
    capability_payload = _read_json(SKILL_CAPABILITIES, {})
    return {
        "schemaVersion": 2,
        "sources": signature,
        "trialDefinitionFingerprint": definition_fp,
        "rewardRotationFingerprint": reward_fp,
        "knownRewardRotations": copy.deepcopy(archive.get("rewardRotations", {})),
        "knownAttributeRotations": copy.deepcopy(archive.get("attributeRotations", {})),
        "difficulties": difficulties,
        "trialRecipes": [
            copy.deepcopy(value)
            for value in recipe_payload.get("recipes", [])
            if isinstance(value, dict)
        ],
        "skillCapabilityCount": len(capability_payload.get("skills", {})),
    }


def ensure_ui_catalog_cache() -> dict[str, Any]:
    """Build one startup catalog and avoid rewriting it while sources are unchanged."""
    signature = _source_signature()
    cached = _read_json(UI_CATALOG_CACHE, {})
    if (
        isinstance(cached, dict)
        and cached.get("schemaVersion") == 2
        and cached.get("sources") == signature
        and isinstance(cached.get("difficulties"), list)
    ):
        return cached
    rebuilt = _build_ui_catalog(signature)
    UI_CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    UI_CATALOG_CACHE.write_text(
        json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rebuilt


def cached_trials(catalog: dict[str, Any], difficulty_id: int) -> list[dict[str, Any]]:
    for difficulty in catalog.get("difficulties", []):
        if isinstance(difficulty, dict) and difficulty.get("difficultyId") == difficulty_id:
            return [
                copy.deepcopy(value)
                for value in difficulty.get("trials", [])
                if isinstance(value, dict)
            ]
    return []


def cache_live_rewards(
    catalog: dict[str, Any],
    difficulty_id: int,
    trials: list[dict[str, Any]],
    reward_fingerprint: str | None,
) -> dict[str, Any]:
    """Persist a newly observed reward rotation without retaining battle progress."""
    if not reward_fingerprint:
        return catalog
    updated = copy.deepcopy(catalog)
    for difficulty in updated.get("difficulties", []):
        if not isinstance(difficulty, dict) or difficulty.get("difficultyId") != difficulty_id:
            continue
        live_by_id = {
            value.get("id"): value
            for value in trials
            if isinstance(value, dict) and isinstance(value.get("id"), int)
        }
        for cached in difficulty.get("trials", []):
            live = live_by_id.get(cached.get("id")) if isinstance(cached, dict) else None
            if isinstance(live, dict) and isinstance(live.get("reward"), dict):
                cached["reward"] = copy.deepcopy(live["reward"])
        break
    updated["rewardRotationFingerprint"] = reward_fingerprint
    live_rotations = updated.setdefault("liveRewardRotations", {})
    rotation = live_rotations.setdefault(reward_fingerprint, {})
    rotation[str(difficulty_id)] = [
        {"id": value.get("id"), "reward": copy.deepcopy(value.get("reward"))}
        for value in trials
        if isinstance(value, dict)
        and isinstance(value.get("id"), int)
        and isinstance(value.get("reward"), dict)
    ]
    if updated != catalog:
        UI_CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        UI_CATALOG_CACHE.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return updated


def cache_live_rotation_catalog(
    catalog: dict[str, Any],
    live_catalog: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Replace startup trial definitions and rewards with the live rotation."""
    difficulties = [
        copy.deepcopy(value)
        for value in live_catalog.get("difficulties", [])
        if isinstance(value, dict)
        and isinstance(value.get("difficultyId"), int)
        and isinstance(value.get("trials"), list)
    ]
    reward_fingerprint = identity.get("rewardRotationFingerprint")
    if not difficulties or not isinstance(reward_fingerprint, str) or not reward_fingerprint:
        return catalog
    updated = copy.deepcopy(catalog)
    updated["difficulties"] = difficulties
    for key in (
        "trialDefinitionFingerprint",
        "rewardRotationFingerprint",
        "attributeRotationFingerprint",
    ):
        value = identity.get(key)
        if isinstance(value, str) and value:
            updated[key] = value
    live_rotations = updated.setdefault("liveRewardRotations", {})
    live_rotations[reward_fingerprint] = {
        str(difficulty["difficultyId"]): [
            {"id": trial.get("id"), "reward": copy.deepcopy(trial.get("reward"))}
            for trial in difficulty.get("trials", [])
            if isinstance(trial, dict)
            and isinstance(trial.get("id"), int)
            and isinstance(trial.get("reward"), dict)
        ]
        for difficulty in difficulties
    }
    if updated != catalog:
        UI_CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        UI_CATALOG_CACHE.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return updated


def infer_trial_difficulty_id(config: dict[str, Any], default: int = 5) -> int:
    values: list[int] = []
    objectives = config.get("objectives")
    if isinstance(objectives, dict):
        values.extend(
            value
            for value in objectives.get("mandatoryTrialIds", [])
            if isinstance(value, int)
        )
    for rule in config.get("rules", []):
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if isinstance(when, dict):
            for key in (
                "completedTrialsAll",
                "incompleteTrialsAll",
                "activeTrialsAny",
                "eligibleTrialsAny",
                "lockedTrialsAny",
                "impossibleTrialsAny",
            ):
                values.extend(value for value in when.get(key, []) if isinstance(value, int))
        action = rule.get("action")
        if isinstance(action, dict):
            values.extend(value for value in action.get("trialIds", []) if isinstance(value, int))
    for trial_id in values:
        difficulty_id = (trial_id - 8_000_000) // 100
        if 1 <= difficulty_id <= 6:
            return difficulty_id
    return default


def load_hero_catalog(defaults: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result = copy.deepcopy(defaults)
    payload = _read_json(HERO_CATALOG_CACHE, {})
    for raw_id, hero in payload.get("heroes", {}).items():
        if str(raw_id).isdigit() and isinstance(hero, dict):
            result[int(raw_id)] = copy.deepcopy(hero)
    return result


def save_hero_catalog(catalog: dict[int, dict[str, Any]]) -> bool:
    payload = {
        "schemaVersion": 1,
        "heroes": {str(hero_id): copy.deepcopy(hero) for hero_id, hero in sorted(catalog.items())},
    }
    existing = _read_json(HERO_CATALOG_CACHE, {})
    if existing == payload:
        return False
    HERO_CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HERO_CATALOG_CACHE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True

#!/usr/bin/env python3
"""Shared mode definitions and strategy storage for alliance boss battles.

The hero/skill/effect catalog is intentionally global.  Only the small piece
of user-authored strategy that is genuinely different is stored per mode.
"""

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
STRATEGY_STORE = PROJECT_ROOT / "config" / "raid-boss-strategies.user.json"
LEGACY_CHIMERA_STRATEGY = PROJECT_ROOT / "config" / "chimera-strategy.user.json"


MODE_SPECS: dict[str, dict[str, Any]] = {
    "chimera": {
        "id": "chimera",
        "label": "奇美拉",
        "battleKind": "AllianceChimera",
        "teamSize": 5,
        "hasTrials": True,
        "status": "ready",
    },
    "hydra": {
        "id": "hydra",
        "label": "六头蛇",
        "battleKind": "AllianceHydra",
        "teamSize": 6,
        "hasTrials": False,
        # Execution is enabled only after the native runtime has positively
        # identified a Hydra battle.  The UI and strategy editor can be used
        # before that first live calibration.
        "status": "calibration",
    },
}

# Only the six heads that have complete gameplay definitions and portraits.
# Stone (26000) and Electric (26280) are installed art placeholders without
# combat parameters, localization, portraits, or complete skill definitions.
HYDRA_HEAD_TYPE_IDS = (26040, 26080, 26120, 26160, 26200, 26240)
HYDRA_HEAD_TYPE_ID_SET = frozenset(HYDRA_HEAD_TYPE_IDS)
HYDRA_RESERVED_HEAD_TYPE_IDS = frozenset({26000, 26280})
HYDRA_EXPOSED_NECK_SKILL_TYPE_IDS = frozenset({260009, 260010, 260011})
HYDRA_MARKER_SKILL_TYPE_IDS = frozenset({260006, 260007, 260008})


def canonical_hydra_head_type_id(value: Any) -> int | None:
    """Resolve a live Hydra variant TypeId to its stable catalog identity."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    if not isinstance(value, dict):
        return None

    # Live Hydra variants use different actor TypeIds, while their avatar path
    # retains the stable catalog identity (including paths such as 26240-1).
    for key in ("avatar", "avatarUrl"):
        source = value.get(key)
        if not isinstance(source, str):
            continue
        match = re.match(r"(\d+)", source.rsplit("/", 1)[-1])
        if match:
            candidate = int(match.group(1))
            if candidate > 0:
                return candidate

    # Skill families provide a second independent identity when an avatar is
    # absent.  For example, skill 261201 belongs to stable head 26120.
    for skill in value.get("skills", []):
        if not isinstance(skill, dict):
            continue
        skill_type_id = skill.get("typeId")
        if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool):
            candidate = skill_type_id // 10
            if 25_000 <= candidate < 30_000:
                return candidate

    type_id = value.get("typeId")
    return (
        type_id
        if isinstance(type_id, int)
        and not isinstance(type_id, bool)
        and type_id > 0
        else None
    )


def hydra_head_is_exposed_neck(value: Any) -> bool:
    """Recognize exposed necks even when the native booleans stay false."""
    if not isinstance(value, dict):
        return False
    if value.get("isHydraNeck") is True or value.get("headState") == "exposed_neck":
        return True
    skill_type_ids = {
        skill.get("typeId")
        for skill in value.get("skills", [])
        if isinstance(skill, dict)
        and isinstance(skill.get("typeId"), int)
        and not isinstance(skill.get("typeId"), bool)
    }
    return bool(skill_type_ids & HYDRA_EXPOSED_NECK_SKILL_TYPE_IDS)


def hydra_head_has_native_markers(value: Any) -> bool:
    """Return true only for entities carrying Hydra's shared native skills."""
    if not isinstance(value, dict):
        return False
    skill_type_ids = {
        skill.get("typeId")
        for skill in value.get("skills", [])
        if isinstance(skill, dict)
        and isinstance(skill.get("typeId"), int)
        and not isinstance(skill.get("typeId"), bool)
    }
    return len(skill_type_ids & HYDRA_MARKER_SKILL_TYPE_IDS) >= 2


def normalize_hydra_head(value: dict[str, Any]) -> dict[str, Any]:
    """Add stable identity and inferred neck state without losing actor data."""
    result = dict(value)
    canonical_type_id = canonical_hydra_head_type_id(value)
    if canonical_type_id is not None:
        result["canonicalTypeId"] = canonical_type_id
    if hydra_head_is_exposed_neck(value):
        result["isHydraNeck"] = True
        result["headState"] = "exposed_neck"
    return result

HYDRA_BATTLE_TURN_CONDITION_KEYS = frozenset(
    {
        "round",
        "turn",
        "playerTurnCount",
        "chimeraTurnCount",
        "activeHeroTurnCount",
        "turnAtLeast",
        "turnAtMost",
        "chimeraTurnAtLeast",
        "chimeraTurnAtMost",
    }
)


def normalize_mode(value: Any) -> str:
    mode = str(value or "chimera").strip().lower()
    if mode not in MODE_SPECS:
        raise ValueError(f"未知 Boss 模式：{value}")
    return mode


def mode_spec(mode: Any) -> dict[str, Any]:
    return copy.deepcopy(MODE_SPECS[normalize_mode(mode)])


def strategy_template(mode: Any = "chimera") -> dict[str, Any]:
    mode_id = normalize_mode(mode)
    spec = MODE_SPECS[mode_id]
    objectives: dict[str, Any] = {
        "minimumDamage": 0,
        "maxRegroupRetries": 10,
        "onAllMetAtResult": "hold_for_user",
    }
    if spec["hasTrials"]:
        objectives.update(
            {
                "mandatoryTrialIds": [],
                "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
            }
        )
    else:
        objectives.update(
            {
                "onTeamDefeatedBeforeMinimumDamage": "free_regroup_and_retry_manual",
                "rescueDevouredChampion": True,
            }
        )
    return {
        "name": f"{mode_id}-user-strategy",
        "mode": "execute",
        "bossMode": mode_id,
        "scope": {"battleKind": spec["battleKind"]},
        "objectives": objectives,
        "safety": {
            "requireManualCommandWindow": True,
            "requireAcceptableTargetSet": True,
            "requireFreshSnapshotMs": 1500,
            "maxCommandsPerTurn": 1,
            "onUnknownState": "pause",
            "onNoMatchingRule": "pause",
        },
        "rules": [],
    }


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def default_store() -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for mode in MODE_SPECS:
        strategy = strategy_template(mode)
        strategy["name"] = "默认策略"
        modes[mode] = {
            "activeStrategyId": "default",
            "strategies": {"default": strategy},
        }
    return {
        "schemaVersion": 2,
        "activeMode": "chimera",
        "modes": modes,
    }


def _strategy_section(value: Any, mode: str) -> dict[str, Any]:
    strategies: dict[str, dict[str, Any]] = {}
    active_id = "default"
    if isinstance(value, dict) and isinstance(value.get("strategies"), dict):
        for raw_id, raw_strategy in value["strategies"].items():
            strategy_id = str(raw_id).strip()
            if strategy_id and isinstance(raw_strategy, dict):
                strategies[strategy_id] = copy.deepcopy(raw_strategy)
        requested_active = str(value.get("activeStrategyId") or "").strip()
        if requested_active in strategies:
            active_id = requested_active
    elif isinstance(value, dict):
        # Schema 1 stored one strategy directly under each Boss mode.
        strategies["default"] = copy.deepcopy(value)

    if not strategies:
        strategy = strategy_template(mode)
        strategy["name"] = "默认策略"
        strategies["default"] = strategy
    if active_id not in strategies:
        active_id = next(iter(strategies))

    default = strategies.get("default")
    if isinstance(default, dict) and str(default.get("name") or "") in {
        "",
        f"{mode}-user-strategy",
    }:
        default["name"] = "默认策略"
    return {"activeStrategyId": active_id, "strategies": strategies}


def normalize_strategy_store(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    modes = raw.get("modes")
    if not isinstance(modes, dict):
        # Accept a legacy single-mode file passed directly to the controller.
        modes = {"chimera": raw}
    result = default_store()
    for mode in MODE_SPECS:
        result["modes"][mode] = _strategy_section(modes.get(mode), mode)
    try:
        result["activeMode"] = normalize_mode(raw.get("activeMode"))
    except ValueError:
        result["activeMode"] = "chimera"
    return result


def load_strategy_store(path: Path = STRATEGY_STORE) -> dict[str, Any]:
    raw = _read_object(path)
    if raw is None:
        raw = default_store()
        legacy = _read_object(LEGACY_CHIMERA_STRATEGY)
        if legacy is not None:
            raw["modes"]["chimera"] = legacy
    return normalize_strategy_store(raw)


def active_strategy_id(store: dict[str, Any], mode: Any) -> str:
    mode_id = normalize_mode(mode)
    normalized = normalize_strategy_store(store)
    section = normalized["modes"][mode_id]
    return str(section["activeStrategyId"])


def strategy_profiles_for_mode(
    store: dict[str, Any], mode: Any
) -> list[dict[str, Any]]:
    mode_id = normalize_mode(mode)
    normalized = normalize_strategy_store(store)
    section = normalized["modes"][mode_id]
    active_id = str(section["activeStrategyId"])
    result: list[dict[str, Any]] = []
    for strategy_id, strategy in section["strategies"].items():
        team = strategy.get("team") if isinstance(strategy, dict) else None
        hero_ids = (
            team.get("heroTypeIds", team.get("heroIds", []))
            if isinstance(team, dict)
            else []
        )
        saved_hero_ids = [
            hero_id
            for hero_id in hero_ids
            if isinstance(hero_id, int)
            and not isinstance(hero_id, bool)
            and hero_id > 0
        ] if isinstance(hero_ids, list) else []
        rules = strategy.get("rules") if isinstance(strategy, dict) else []
        result.append(
            {
                "id": strategy_id,
                "name": str(strategy.get("name") or "未命名策略"),
                "active": strategy_id == active_id,
                "teamHeroIds": saved_hero_ids,
                "ruleCount": len(rules) if isinstance(rules, list) else 0,
            }
        )
    return result


def strategy_for_mode(
    store: dict[str, Any], mode: Any, strategy_id: str | None = None
) -> dict[str, Any]:
    mode_id = normalize_mode(mode)
    normalized = normalize_strategy_store(store)
    section = normalized["modes"][mode_id]
    selected_id = str(strategy_id or section["activeStrategyId"])
    value = section["strategies"].get(selected_id)
    if not isinstance(value, dict):
        raise ValueError(f"策略组不存在：{selected_id}")
    strategy = copy.deepcopy(value)
    return sanitize_strategy_for_mode(strategy, mode_id)


def sanitize_strategy_for_mode(
    strategy: dict[str, Any], mode: Any
) -> dict[str, Any]:
    """Remove unstable whole-battle turn counters from Hydra rule conditions."""
    mode_id = normalize_mode(mode)
    result = copy.deepcopy(strategy)
    if mode_id != "hydra":
        return result

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "hydraHeadPriority" and isinstance(
            node.get("headTypeIds"), list
        ):
            node["headTypeIds"] = [
                type_id
                for type_id in node["headTypeIds"]
                if type_id not in HYDRA_RESERVED_HEAD_TYPE_IDS
            ]
        when = node.get("when")
        if isinstance(when, dict):
            for key in HYDRA_BATTLE_TURN_CONDITION_KEYS:
                when.pop(key, None)
        for key, value in node.items():
            if key != "when":
                visit(value)

    visit(result.get("rules"))
    visit(result.get("strategyTree"))
    return result


def update_mode_strategy(
    store: dict[str, Any], mode: Any, strategy: dict[str, Any],
    strategy_id: str | None = None,
) -> dict[str, Any]:
    mode_id = normalize_mode(mode)
    result = normalize_strategy_store(store)
    section = result["modes"][mode_id]
    selected_id = str(strategy_id or section["activeStrategyId"]).strip()
    if not selected_id:
        raise ValueError("策略组 ID 不能为空")
    section["strategies"][selected_id] = sanitize_strategy_for_mode(strategy, mode_id)
    section["activeStrategyId"] = selected_id
    result["schemaVersion"] = 2
    result["activeMode"] = mode_id
    return result


def select_mode_strategy(
    store: dict[str, Any], mode: Any, strategy_id: str
) -> dict[str, Any]:
    mode_id = normalize_mode(mode)
    result = normalize_strategy_store(store)
    section = result["modes"][mode_id]
    if strategy_id not in section["strategies"]:
        raise ValueError(f"策略组不存在：{strategy_id}")
    section["activeStrategyId"] = strategy_id
    result["activeMode"] = mode_id
    return result


def delete_mode_strategy(
    store: dict[str, Any], mode: Any, strategy_id: str
) -> dict[str, Any]:
    mode_id = normalize_mode(mode)
    result = normalize_strategy_store(store)
    section = result["modes"][mode_id]
    strategies = section["strategies"]
    if strategy_id not in strategies:
        raise ValueError(f"策略组不存在：{strategy_id}")
    if len(strategies) <= 1:
        raise ValueError("每种 Boss 模式至少保留一个策略组")
    del strategies[strategy_id]
    if section["activeStrategyId"] == strategy_id:
        section["activeStrategyId"] = next(iter(strategies))
    result["activeMode"] = mode_id
    return result

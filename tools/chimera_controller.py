from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from boss_modes import (
    canonical_hydra_head_type_id,
    hydra_head_has_native_markers,
    hydra_head_is_exposed_neck,
    load_strategy_store,
    mode_spec,
    normalize_mode,
    strategy_for_mode,
)
from controller_pause import ControllerPauseEvent
from chimera_catalog_cache import (
    is_unresolved_skill_name,
    load_hero_catalog,
    skill_display_name,
)
from inject_probe import queue_command, queue_lifecycle_command, set_takeover
from named_mutex import NamedMutex


ACTIVE_BOSS_MODE = "chimera"
FIXED_CHIMERA_FORM_INTERVAL = 5
FIXED_CHIMERA_FORM_CYCLE = (
    "Ultimate",
    "Ram",
    "Ultimate",
    "Lion",
    "Ultimate",
    "Snake",
)
CANONICAL_TRIAL_RECIPE_DIFFICULTY_ID = 5
COMMAND_ACK_TIMEOUT_SECONDS = 3.0
COMMAND_CONFIRM_TIMEOUT_SECONDS = 45.0
LIFECYCLE_START_BATTLE = 1
LIFECYCLE_FREE_REGROUP = 2
LIFECYCLE_PREPARE_FREE_REGROUP = 3
LIFECYCLE_REFRESH_TEAM_SELECTION = 4
LIFECYCLE_SELECT_HEROES = 5
LIFECYCLE_RESTART_HYDRA_RESULT = 6
LIFECYCLE_RESTART_CHIMERA_RESULT = 7
LIFECYCLE_START_NONCE = 0x80000001
LIFECYCLE_FREE_REGROUP_NONCE = 0x80000002
LIFECYCLE_PREPARE_FREE_REGROUP_NONCE = 0x80000100
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetTickCount64.argtypes = []
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
kernel32.WaitForSingleObject.restype = ctypes.c_uint32
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
_PARENT_EXITED = threading.Event()
_PARENT_CLEANUP_FINISHED = threading.Event()
PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
RESOURCE_ROOT = Path(os.environ.get("CHIMERA_RESOURCE_ROOT", PROJECT_ROOT)).resolve()
DEFAULT_ROTATION_ARCHIVE = RESOURCE_ROOT / "data" / "chimera-rotation-catalogs.json"
DEFAULT_CAPABILITY_CACHE = RESOURCE_ROOT / "data" / "chimera-skill-capabilities.json"
DEFAULT_TRIAL_RECIPES = RESOURCE_ROOT / "data" / "chimera-trial-recipes.json"
CURRENT_AGENT = (
    PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll"
)
_ARCHIVED_ROTATION_OBSERVATIONS: set[tuple[Any, ...]] = set()
_INITIAL_SKILL_NAMES = {
    int(skill["typeId"]): str(skill.get("name") or "")
    for hero in load_hero_catalog({}).values()
    if isinstance(hero, dict)
    for skill in hero.get("skills", [])
    if isinstance(skill, dict)
    and isinstance(skill.get("typeId"), int)
    and not is_unresolved_skill_name(skill.get("name"))
}


def configure_text_streams() -> None:
    """Emit controller logs as UTF-8 even inside a frozen Windows worker."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(
                    encoding="utf-8",
                    errors="backslashreplace",
                    line_buffering=True,
                    write_through=True,
                )
            except (OSError, TypeError, ValueError):
                pass
SUPPORTED_CONDITION_KEYS = frozenset(
    {
        "form",
        "activeHeroTypeId",
        "activeHeroFormIndex",
        "activeHeroIsMetamorph",
        "activeHeroIsTransformed",
        "transformationReady",
        "round",
        "turn",
        "playerTurnCount",
        "chimeraTurnCount",
        "activeHeroTurnCount",
        "targetHeadTypeId",
        "targetHeadState",
        "allianceDifficultyId",
        "chimeraStageId",
        "stageRotationIndex",
        "nextForm",
        "catalogFingerprint",
        "trialDefinitionFingerprint",
        "rewardRotationFingerprint",
        "attributeRotationFingerprint",
        "turnAtLeast",
        "turnAtMost",
        "chimeraTurnAtLeast",
        "chimeraTurnAtMost",
        "activeHeroHpPctBelow",
        "bossHpPctBelow",
        "currentDamageAtLeast",
        "currentDamageBelow",
        "hydraHeadCountAtLeast",
        "hydraHeadCountAtMost",
        "devouringHeadsAtLeast",
        "exposedNecksAtLeast",
        "targetHeadDigestionTurnsAtLeast",
        "targetHeadDigestionTurnsAtMost",
        "bossEffectSlotsAtLeast",
        "bossEffectSlotsAtMost",
        "activeHeroEffectSlotsAtLeast",
        "activeHeroEffectSlotsAtMost",
        "anyAllyEffectSlotsAtLeast",
        "allAlliesEffectSlotsAtMost",
        "deadAlliesAtLeast",
        "livingAlliesAtLeast",
        "turnsUntilFormChangeAtLeast",
        "turnsUntilFormChangeAtMost",
        "anyAllyHpPctBelow",
        "bossHasEffects",
        "bossMissingEffects",
        "activeHeroHasEffects",
        "activeHeroMissingEffects",
        "bossHasEffect",
        "bossMissingEffect",
        "activeHeroHasEffect",
        "activeHeroMissingEffect",
        "anyAllyHasEffect",
        "anyAllyMissingEffect",
        "allAlliesHaveEffect",
        "allyHasEffect",
        "allyMissingEffect",
        "effectConditions",
        "effectConditionsMode",
        "skillCooldownConditions",
        "skillCooldownConditionsMode",
        "allyEffectSlotsAtLeast",
        "allyEffectSlotsAtMost",
        "completedTrialsAll",
        "completedTrialsAny",
        "incompleteTrialsAll",
        "startedTrialsAll",
        "startedTrialsAny",
        "activeTrialsAll",
        "activeTrialsAny",
        "eligibleTrialsAll",
        "eligibleTrialsAny",
        "lockedTrialsAny",
        "possibleTrialsAll",
        "impossibleTrialsAny",
        "trialProgressAtLeast",
        "trialProgressBelow",
    }
)


def validate_condition_values(when: dict[str, Any], *, path: str) -> None:
    for mode_key, conditions_key in (
        ("effectConditionsMode", "effectConditions"),
        ("skillCooldownConditionsMode", "skillCooldownConditions"),
    ):
        mode = when.get(mode_key)
        if mode is not None and mode not in {"all", "any"}:
            raise ValueError(f"{path}.{mode_key} 只允许 all 或 any")
        if mode is not None and conditions_key not in when:
            raise ValueError(f"{path}.{mode_key} 缺少对应的 {conditions_key}")
    effect_conditions = when.get("effectConditions")
    if effect_conditions is not None:
        if not isinstance(effect_conditions, list) or not effect_conditions:
            raise ValueError(f"{path}.effectConditions 必须是非空数组")
        for index, condition in enumerate(effect_conditions):
            condition_path = f"{path}.effectConditions[{index}]"
            if not isinstance(condition, dict):
                raise ValueError(f"{condition_path} 必须是对象")
            if condition.get("target") not in {
                "boss", "bossPriority", "bossAny", "bossAll", "ally"
            }:
                raise ValueError(f"{condition_path}.target 不受支持")
            if condition.get("presence") not in {"has", "missing"}:
                raise ValueError(f"{condition_path}.presence 不受支持")
            if not isinstance(condition.get("effect"), dict) or not condition["effect"]:
                raise ValueError(f"{condition_path}.effect 必须指定具体效果")
    cooldown_conditions = when.get("skillCooldownConditions")
    if cooldown_conditions is None:
        return
    if not isinstance(cooldown_conditions, list) or not cooldown_conditions:
        raise ValueError(f"{path}.skillCooldownConditions 必须是非空数组")
    for index, condition in enumerate(cooldown_conditions):
        condition_path = f"{path}.skillCooldownConditions[{index}]"
        if not isinstance(condition, dict):
            raise ValueError(f"{condition_path} 必须是对象")
        for key in ("heroTypeId", "skillTypeId"):
            value = condition.get(key)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(f"{condition_path}.{key} 必须是正整数")
        lower = condition.get("turnsAtLeast")
        upper = condition.get("turnsAtMost")
        if lower is None and upper is None:
            raise ValueError(f"{condition_path} 至少需要一个冷却回合范围")
        for key, value in (("turnsAtLeast", lower), ("turnsAtMost", upper)):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{condition_path}.{key} 必须是非负整数")
        if isinstance(lower, int) and isinstance(upper, int) and lower > upper:
            raise ValueError(f"{condition_path} 的最小冷却不能大于最大冷却")


def validate_default_skill_policy(policy: Any, *, path: str) -> None:
    if not isinstance(policy, dict):
        raise ValueError(f"{path} 必须是对象")
    priority_skills = policy.get("prioritySkills")
    if not isinstance(priority_skills, list):
        raise ValueError(f"{path}.prioritySkills 必须是数组")
    seen_skill_ids: set[int] = set()
    for index, entry in enumerate(priority_skills):
        entry_path = f"{path}.prioritySkills[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{entry_path} 必须是对象")
        skill_type_id = entry.get("skillTypeId")
        if (
            not isinstance(skill_type_id, int)
            or isinstance(skill_type_id, bool)
            or skill_type_id <= 0
            or skill_type_id in seen_skill_ids
        ):
            raise ValueError(f"{entry_path}.skillTypeId 必须是互不重复的正整数")
        seen_skill_ids.add(skill_type_id)
        if entry.get("skillSlot") is not None and (
            not isinstance(entry["skillSlot"], int)
            or isinstance(entry["skillSlot"], bool)
            or entry["skillSlot"] <= 0
        ):
            raise ValueError(f"{entry_path}.skillSlot 必须是正整数")
        if entry.get("isTransform") is not None and not isinstance(
            entry["isTransform"], bool
        ):
            raise ValueError(f"{entry_path}.isTransform 必须是布尔值")
    first_turn_skill = policy.get("firstTurnSkill")
    if first_turn_skill is not None:
        first_turn_path = f"{path}.firstTurnSkill"
        if not isinstance(first_turn_skill, dict):
            raise ValueError(f"{first_turn_path} 必须是对象")
        skill_type_id = first_turn_skill.get("skillTypeId")
        if (
            not isinstance(skill_type_id, int)
            or isinstance(skill_type_id, bool)
            or skill_type_id <= 0
        ):
            raise ValueError(f"{first_turn_path}.skillTypeId 必须是正整数")
        if first_turn_skill.get("skillSlot") is not None and (
            not isinstance(first_turn_skill["skillSlot"], int)
            or isinstance(first_turn_skill["skillSlot"], bool)
            or first_turn_skill["skillSlot"] <= 0
        ):
            raise ValueError(f"{first_turn_path}.skillSlot 必须是正整数")
        if first_turn_skill.get("isTransform") is not None and not isinstance(
            first_turn_skill["isTransform"], bool
        ):
            raise ValueError(f"{first_turn_path}.isTransform 必须是布尔值")
    blocked = policy.get("blockedSkillTypeIds", [])
    if (
        not isinstance(blocked, list)
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in blocked
        )
        or len(blocked) != len(set(blocked))
    ):
        raise ValueError(
            f"{path}.blockedSkillTypeIds 必须是互不重复的正整数数组"
        )


def validate_strategy_node(node: Any, *, path: str = "strategyTree") -> None:
    if not isinstance(node, dict):
        raise ValueError(f"{path} 必须是对象")
    node_type = node.get("type", "rule")
    if node_type in {"priority", "selector"}:
        children = node.get("children")
        if not isinstance(children, list):
            raise ValueError(f"{path}.children 必须是数组")
        for index, child in enumerate(children):
            validate_strategy_node(child, path=f"{path}.children[{index}]")
        return
    if node_type in {"branch", "condition"}:
        when = node.get("when")
        if not isinstance(when, dict) or any(
            not isinstance(key, str) or key not in SUPPORTED_CONDITION_KEYS
            for key in when
        ):
            raise ValueError(f"{path}.when 包含未知条件")
        validate_condition_values(when, path=f"{path}.when")
        validate_strategy_node(node.get("then"), path=f"{path}.then")
        if node.get("else") is not None:
            validate_strategy_node(node.get("else"), path=f"{path}.else")
        return
    if node_type == "pause":
        return
    if node_type not in {
        "rule",
        "cast",
        "transform",
        "maintainEffects",
        "executeTrialRecipe",
        "defaultSkillPriority",
    }:
        raise ValueError(f"{path}.type 不受支持：{node_type}")
    if node_type == "rule":
        when = node.get("when", {})
        if not isinstance(when, dict) or any(
            not isinstance(key, str) or key not in SUPPORTED_CONDITION_KEYS
            for key in when
        ):
            raise ValueError(f"{path}.when 包含未知条件")
        validate_condition_values(when, path=f"{path}.when")
        action = node.get("action")
    else:
        action = node
    if not isinstance(action, dict):
        raise ValueError(f"{path}.action 必须是对象")
    action_type = action.get("type")
    if action_type == "transform":
        for key in ("skillSlot", "skillTypeId"):
            if key in action and (
                not isinstance(action[key], int)
                or isinstance(action[key], bool)
                or action[key] <= 0
            ):
                raise ValueError(f"{path}.action.{key} 必须是正整数")
        if action.get("toFormIndex") not in {None, 0, 1}:
            raise ValueError(f"{path}.action.toFormIndex 只允许 0 或 1")
        return
    if action_type == "defaultSkillPriority":
        validate_default_skill_policy(action, path=f"{path}.action")
        form_policies = action.get("formPolicies")
        if form_policies is not None:
            if not isinstance(form_policies, dict) or not form_policies:
                raise ValueError(f"{path}.action.formPolicies 必须是非空对象")
            supported_forms = {"Ultimate", "Ram", "Lion", "Snake"}
            for form, policy in form_policies.items():
                if form not in supported_forms:
                    raise ValueError(
                        f"{path}.action.formPolicies 包含未知奇美拉形态：{form}"
                    )
                validate_default_skill_policy(
                    policy, path=f"{path}.action.formPolicies.{form}"
                )
        if action.get("reserveStrictRuleSkills", True) is not True:
            raise ValueError(
                f"{path}.action.reserveStrictRuleSkills 当前必须为 true"
            )
        return
    if action_type == "maintainEffects":
        requirements = action.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"{path}.action.requirements must be a non-empty array")
        for index, requirement in enumerate(requirements):
            requirement_path = f"{path}.action.requirements[{index}]"
            if not isinstance(requirement, dict):
                raise ValueError(f"{requirement_path} must be an object")
            if requirement.get("scope") not in {"boss", "activeHero", "anyAlly"}:
                raise ValueError(f"{requirement_path}.scope is not supported")
            selectors = requirement_effect_selectors(requirement)
            if not selectors:
                raise ValueError(
                    f"{requirement_path} needs effect or a non-empty anyOf array"
                )
            keep_turns = requirement.get("keepTurnsAtLeast", 1)
            if (
                not isinstance(keep_turns, int)
                or isinstance(keep_turns, bool)
                or keep_turns < 0
            ):
                raise ValueError(
                    f"{requirement_path}.keepTurnsAtLeast must be a non-negative integer"
                )
        for key in ("probeUnknownSkills", "probeTransforms"):
            if key in action and not isinstance(action[key], bool):
                raise ValueError(f"{path}.action.{key} must be true or false")
        return
    if action_type == "executeTrialRecipe":
        trial_ids = action.get("trialIds")
        if trial_ids is not None and not configured_trial_ids(trial_ids):
            raise ValueError(f"{path}.action.trialIds must contain positive trial IDs")
        prepare_turns = action.get("prepareWithinBossTurns", 0)
        if (
            not isinstance(prepare_turns, int)
            or isinstance(prepare_turns, bool)
            or not 0 <= prepare_turns <= FIXED_CHIMERA_FORM_INTERVAL
        ):
            raise ValueError(
                f"{path}.action.prepareWithinBossTurns must be between 0 and "
                f"{FIXED_CHIMERA_FORM_INTERVAL}"
            )
        for key in ("probeUnknownSkills", "probeTransforms"):
            if key in action and not isinstance(action[key], bool):
                raise ValueError(f"{path}.action.{key} must be true or false")
        return
    if action_type != "cast":
        raise ValueError(f"{path}.action.type 不受支持")
    if "skillSlot" not in action and "skillTypeId" not in action:
        raise ValueError(f"{path}.action 缺少技能")
    if "target" not in action:
        raise ValueError(f"{path}.action 缺少目标")
    target = action.get("target")
    if isinstance(target, dict) and target.get("type") == "allyPosition":
        position = target.get("position")
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or not 1 <= position <= 6
        ):
            raise ValueError(
                f"{path}.action.target.position 必须是 1–6"
            )
    if isinstance(target, dict) and target.get("type") == "hydraHeadPriority":
        head_type_ids = target.get("headTypeIds", [])
        if (
            not isinstance(head_type_ids, list)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in head_type_ids
            )
            or len(head_type_ids) != len(set(head_type_ids))
        ):
            raise ValueError(
                f"{path}.action.target.headTypeIds 必须是互不重复的正整数数组"
            )
        if target.get("fallback", "lowestHp") not in {
            "lowestHp", "devouring", "exposedNeck", "none"
        }:
            raise ValueError(f"{path}.action.target.fallback 不受支持")


@dataclass(frozen=True)
class Decision:
    rule: str
    skill: dict[str, Any]
    target_id: int
    target_label: str
    capability_probe: bool = False
    trial_id: int | None = None


class SkillCapabilityMemory:
    """Small, roster-independent memory learned from live AppliedEffect models."""

    def __init__(self) -> None:
        self._capabilities: dict[int, list[dict[str, Any]]] = {}
        self._performance: dict[int, dict[str, Any]] = {}
        self._probed_skill_type_ids: set[int] = set()
        self.dirty = False

    @classmethod
    def load(cls, path: Path) -> "SkillCapabilityMemory":
        memory = cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return memory
        skills = payload.get("skills") if isinstance(payload, dict) else None
        if not isinstance(skills, dict):
            return memory
        for raw_skill_type_id, raw_capabilities in skills.items():
            try:
                skill_type_id = int(raw_skill_type_id)
            except (TypeError, ValueError):
                continue
            if skill_type_id <= 0 or not isinstance(raw_capabilities, list):
                continue
            for capability in raw_capabilities:
                if isinstance(capability, dict):
                    memory._remember(skill_type_id, capability, mark_dirty=False)
        performance = payload.get("performance") if isinstance(payload, dict) else None
        if isinstance(performance, dict):
            for raw_skill_type_id, raw_statistics in performance.items():
                try:
                    skill_type_id = int(raw_skill_type_id)
                except (TypeError, ValueError):
                    continue
                if skill_type_id > 0 and isinstance(raw_statistics, dict):
                    memory._performance[skill_type_id] = raw_statistics
        memory.dirty = False
        return memory

    def save_if_changed(self, path: Path) -> bool:
        if not self.dirty:
            return False
        payload = {
            "version": 2,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "skills": {
                str(skill_type_id): capabilities
                for skill_type_id, capabilities in sorted(self._capabilities.items())
            },
            "performance": {
                str(skill_type_id): statistics
                for skill_type_id, statistics in sorted(self._performance.items())
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        self.dirty = False
        return True

    @staticmethod
    def _normalized_capability(capability: dict[str, Any]) -> dict[str, Any] | None:
        target_scope = capability.get("targetScope")
        if target_scope not in {"boss", "ally", "self"}:
            return None
        normalized: dict[str, Any] = {"targetScope": target_scope}
        for key in (
            "effectTypeId",
            "effectKindId",
            "effectKind",
            "lifetime",
            "turnsLeft",
        ):
            value = capability.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                normalized[key] = value
        if not any(
            key in normalized for key in ("effectTypeId", "effectKindId", "effectKind")
        ):
            return None
        return normalized

    def _remember(
        self,
        skill_type_id: int,
        capability: dict[str, Any],
        *,
        mark_dirty: bool = True,
    ) -> bool:
        normalized = self._normalized_capability(capability)
        if normalized is None:
            return False
        items = self._capabilities.setdefault(skill_type_id, [])
        identity = (
            normalized.get("targetScope"),
            normalized.get("effectTypeId"),
            normalized.get("effectKindId"),
            normalized.get("effectKind"),
        )
        for existing in items:
            existing_identity = (
                existing.get("targetScope"),
                existing.get("effectTypeId"),
                existing.get("effectKindId"),
                existing.get("effectKind"),
            )
            if existing_identity != identity:
                continue
            changed = False
            for key in ("lifetime", "turnsLeft"):
                observed = normalized.get(key)
                previous = existing.get(key)
                if isinstance(observed, int) and (
                    not isinstance(previous, int) or observed > previous
                ):
                    existing[key] = observed
                    changed = True
            if changed and mark_dirty:
                self.dirty = True
            return changed
        items.append(normalized)
        if mark_dirty:
            self.dirty = True
        return True

    def observe_state(self, state: dict[str, Any]) -> int:
        learned = 0
        entities = (
            [(entity, "ally") for entity in state_entities(state, "heroes")]
            + [(entity, "boss") for entity in state_entities(state, "bosses")]
        )
        for entity, entity_scope in entities:
            entity_id = entity.get("id")
            for effect in entity.get("effects", []):
                if not isinstance(effect, dict):
                    continue
                skill_type_id = effect.get("skillTypeId")
                if (
                    not isinstance(skill_type_id, int)
                    or isinstance(skill_type_id, bool)
                    or skill_type_id <= 0
                ):
                    continue
                producer_id = effect.get("producerId")
                target_scope = entity_scope
                if entity_scope == "ally" and producer_id == entity_id:
                    target_scope = "self"
                capability = {
                    "targetScope": target_scope,
                    **{
                        key: effect[key]
                        for key in (
                            "effectTypeId",
                            "effectKindId",
                            "effectKind",
                            "lifetime",
                            "turnsLeft",
                        )
                        if key in effect
                    },
                }
                if self._remember(skill_type_id, capability):
                    learned += 1
        return learned

    def capabilities_for(self, skill_type_id: int) -> list[dict[str, Any]]:
        return list(self._capabilities.get(skill_type_id, []))

    @staticmethod
    def _update_damage_statistics(
        statistics: dict[str, Any], damage: float
    ) -> None:
        samples = int(statistics.get("samples", 0))
        previous_ema = statistics.get("emaDamage")
        ema = (
            float(damage)
            if not isinstance(previous_ema, (int, float)) or samples <= 0
            else float(previous_ema) * 0.75 + float(damage) * 0.25
        )
        statistics["samples"] = samples + 1
        statistics["emaDamage"] = round(ema, 3)
        statistics["maxDamage"] = round(
            max(float(statistics.get("maxDamage", 0)), float(damage)), 3
        )

    def observe_action_damage(
        self,
        skill_type_id: int,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        trial_id: int | None = None,
    ) -> bool:
        """Learn an in-memory damage estimate from one confirmed submitted action."""
        if skill_type_id <= 0:
            return False
        before_battle = before.get("battle", {})
        after_battle = after.get("battle", {})
        before_damage = before_battle.get("currentDamage")
        after_damage = after_battle.get("currentDamage")
        if not isinstance(before_damage, (int, float)) or not isinstance(
            after_damage, (int, float)
        ):
            return False
        damage = max(0.0, float(after_damage) - float(before_damage))
        statistics = self._performance.setdefault(skill_type_id, {})
        self._update_damage_statistics(statistics, damage)
        if isinstance(trial_id, int) and trial_id > 0:
            before_trial = trial_status_by_id(before).get(trial_id, {})
            after_trial = trial_status_by_id(after).get(trial_id, {})
            before_progress = before_trial.get("current")
            after_progress = after_trial.get("current")
            if isinstance(before_progress, (int, float)) and isinstance(
                after_progress, (int, float)
            ):
                credited = max(0.0, float(after_progress) - float(before_progress))
                trials = statistics.setdefault("trials", {})
                trial_statistics = trials.setdefault(str(trial_id), {})
                self._update_damage_statistics(trial_statistics, credited)
        self.dirty = True
        return True

    def damage_score(self, skill_type_id: int, trial_id: int | None = None) -> float | None:
        statistics = self._performance.get(skill_type_id)
        if not isinstance(statistics, dict):
            return None
        selected = statistics
        if isinstance(trial_id, int) and trial_id > 0:
            trials = statistics.get("trials")
            trial_statistics = (
                trials.get(str(trial_id)) if isinstance(trials, dict) else None
            )
            if isinstance(trial_statistics, dict) and int(
                trial_statistics.get("samples", 0)
            ) > 0:
                selected = trial_statistics
        value = selected.get("emaDamage")
        if isinstance(value, (int, float)) and int(selected.get("samples", 0)) > 0:
            return float(value)
        return None

    def mark_probed(self, skill_type_id: int) -> None:
        if skill_type_id > 0:
            self._probed_skill_type_ids.add(skill_type_id)

    def was_probed(self, skill_type_id: int) -> bool:
        return skill_type_id in self._probed_skill_type_ids


@dataclass(frozen=True)
class ObjectiveReport:
    mandatory_trial_ids: tuple[int, ...]
    completed_trial_ids: tuple[int, ...]
    missing_trial_ids: tuple[int, ...]
    impossible_trial_ids: tuple[int, ...]
    current_damage: float
    minimum_damage: float

    @property
    def all_met(self) -> bool:
        return (
            not self.missing_trial_ids
            and self.current_damage >= self.minimum_damage
        )

    @property
    def mandatory_impossible(self) -> bool:
        return bool(self.impossible_trial_ids)


@dataclass(frozen=True)
class AccountBinding:
    pid: int
    account_name: str
    user_id: int


class TakeoverInterrupted(RuntimeError):
    pass


class ControllerPaused(RuntimeError):
    pass


class ParentProcessExited(RuntimeError):
    pass


class GamePaused(RuntimeError):
    pass


def start_parent_watchdog(parent_pid: int | None) -> None:
    if not isinstance(parent_pid, int) or parent_pid <= 0:
        return
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    if not handle:
        _PARENT_EXITED.set()
        return

    def watch() -> None:
        try:
            if kernel32.WaitForSingleObject(handle, INFINITE) == WAIT_OBJECT_0:
                _PARENT_EXITED.set()
                # Normal controller iterations observe the event and disarm the
                # takeover session.  If the controller itself is wedged, do not
                # let it survive its UI parent indefinitely.
                if not _PARENT_CLEANUP_FINISHED.wait(60.0):
                    os._exit(9)
        finally:
            kernel32.CloseHandle(handle)

    threading.Thread(
        target=watch,
        daemon=True,
        name="chimera-parent-watchdog",
    ).start()


_PAUSE_EVENT: ControllerPauseEvent | None = None

# These acknowledgements mean that the game moved on after a snapshot was
# published (for example because the user acted first).  The injected agent has
# already rejected the old command on the Unity main thread, so the controller
# can safely wait for the next snapshot instead of ending the takeover session.
RECOVERABLE_COMMAND_REJECTIONS = frozenset(
    {"guard_failed", "battle_guard_changed", "duplicate_turn"}
)


def recoverable_command_rejection(
    acknowledgement: dict[str, Any] | None,
) -> bool:
    return bool(
        isinstance(acknowledgement, dict)
        and acknowledgement.get("status") == "rejected"
        and acknowledgement.get("reason") in RECOVERABLE_COMMAND_REJECTIONS
    )


class FreeRegroupCompleted(RuntimeError):
    pass


def account_binding(account: dict[str, Any] | None, pid: int) -> AccountBinding:
    if not isinstance(account, dict):
        raise ValueError("尚未读取到游戏内账户，拒绝仅按 PID 接管")
    account_pid = account.get("pid")
    account_name = account.get("accountName")
    user_id = account.get("userId")
    if account_pid != pid:
        raise ValueError("账户快照不属于所选 Raid 进程")
    if not isinstance(account_name, str) or not account_name.strip():
        raise ValueError("未读取到有效的游戏内用户名")
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
    ):
        raise ValueError("未读取到有效的游戏玩家 ID")
    return AccountBinding(pid=pid, account_name=account_name, user_id=user_id)


def require_account_binding(ipc: AgentIpc, expected: AccountBinding) -> None:
    try:
        current = account_binding(ipc.account(), expected.pid)
    except ValueError as error:
        raise TakeoverInterrupted(f"账户身份无法确认，工具已停止接管：{error}") from error
    if current != expected:
        raise TakeoverInterrupted(
            "检测到游戏内账户已变化，工具已停止接管："
            f"原账户 {expected.account_name}（{expected.user_id}），"
            f"当前账户 {current.account_name}（{current.user_id}）"
        )


def require_takeover_active(ipc: AgentIpc, session_id: int) -> None:
    if _PAUSE_EVENT is not None and _PAUSE_EVENT.is_set():
        raise ControllerPaused()
    lifecycle = ipc.lifecycle()
    if not lifecycle:
        raise RuntimeError("尚未收到代理接管状态")
    if lifecycle.get("sessionId") != session_id:
        raise RuntimeError("代理接管会话已经变化")
    state = lifecycle.get("takeoverState")
    if state == "interrupted":
        reason = lifecycle.get("reason", "安全守卫触发")
        source = lifecycle.get("inputSource", "代理安全守卫")
        if reason == "game_pause_clicked":
            raise GamePaused()
        raise TakeoverInterrupted(
            f"代理已安全中断接管（原因：{reason}；来源：{source}）"
        )
    if state != "active":
        raise RuntimeError(f"代理未处于接管状态：{state}")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象：{path}")
    return value


def validate_strategy_config(
    config: dict[str, Any], *, boss_mode: str | None = None
) -> None:
    boss_mode = normalize_mode(boss_mode or config.get("bossMode", "chimera"))
    mode = config.get("mode", "observe")
    if mode not in {"observe", "execute"}:
        raise ValueError("策略 mode 只能是 observe 或 execute")

    objectives = config.get("objectives", {})
    if not isinstance(objectives, dict):
        raise ValueError("objectives 必须是对象")
    if boss_mode == "chimera":
        raw_trial_ids = objectives.get(
            "mandatoryTrials", objectives.get("mandatoryTrialIds", [])
        )
        raw_trial_items = as_list(raw_trial_ids) if raw_trial_ids is not None else []
        trial_ids = configured_trial_ids(raw_trial_ids)
        if len(trial_ids) != len(raw_trial_items):
            raise ValueError("必要试炼 ID 必须是互不重复的正整数")

    minimum_damage = objectives.get("minimumDamage", 0)
    if (
        not isinstance(minimum_damage, (int, float))
        or isinstance(minimum_damage, bool)
        or not math.isfinite(float(minimum_damage))
        or minimum_damage < 0
    ):
        raise ValueError("minimumDamage 必须是非负有限数值")
    for key in ("maxRegroupRetries",):
        value = objectives.get(key, 10)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} 必须是非负整数")
    if boss_mode == "chimera":
        behavior = objectives.get(
            "onMandatoryTrialImpossible", "free_regroup_and_retry_manual"
        )
        if behavior not in {
            "free_regroup_and_retry_manual",
            "free_regroup_and_stop",
        }:
            raise ValueError("未知的必要试炼失败处理方式")
    else:
        behavior = objectives.get(
            "onTeamDefeatedBeforeMinimumDamage",
            "free_regroup_and_retry_manual",
        )
        if behavior not in {
            "free_regroup_and_retry_manual",
            "hold_for_user",
        }:
            raise ValueError("未知的六头蛇未达伤害目标处理方式")
    if objectives.get("onAllMetAtResult", "hold_for_user") != "hold_for_user":
        raise ValueError("结算处理目前只允许 hold_for_user")

    safety = config.get("safety", {})
    if not isinstance(safety, dict):
        raise ValueError("safety 必须是对象")
    fresh_ms = safety.get("requireFreshSnapshotMs", 500)
    if (
        not isinstance(fresh_ms, int)
        or isinstance(fresh_ms, bool)
        or not 50 <= fresh_ms <= 60_000
    ):
        raise ValueError("requireFreshSnapshotMs 必须是 50–60000 的整数")
    max_per_turn = safety.get("maxCommandsPerTurn", 1)
    if max_per_turn != 1 or isinstance(max_per_turn, bool):
        raise ValueError("当前安全协议只允许 maxCommandsPerTurn=1")
    for key in ("onUnknownState", "onNoMatchingRule"):
        if safety.get(key, "pause") != "pause":
            raise ValueError(f"{key} 目前只允许 pause")

    tree = config.get("strategyTree")
    rules = config.get("rules")
    if tree is not None and not isinstance(tree, dict):
        raise ValueError("strategyTree 必须是对象")
    if tree is None and not isinstance(rules, list):
        raise ValueError("缺少有效的 strategyTree 或 rules")
    if isinstance(tree, dict):
        validate_strategy_node(tree)
    if isinstance(rules, list):
        for index, rule in enumerate(rules):
            validate_strategy_node(rule, path=f"rules[{index}]")

    team = config.get("team")
    if team is not None:
        if not isinstance(team, dict):
            raise ValueError("team 必须是对象")
        hero_ids = team.get("heroTypeIds", team.get("heroIds"))
        instance_ids = team.get("heroInstanceIds")
        expected_count = 5 if boss_mode == "chimera" else 6
        if (
            not isinstance(hero_ids, list)
            or len(hero_ids) > expected_count
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in hero_ids
            )
            or len(set(hero_ids)) != len(hero_ids)
        ):
            count_label = "五" if boss_mode == "chimera" else "六"
            raise ValueError(
                f"team.heroTypeIds 最多包含{count_label}个互不重复的正整数"
            )
        if instance_ids is not None and (
            not isinstance(instance_ids, list)
            or len(instance_ids) != len(hero_ids)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in instance_ids
            )
            or len(set(instance_ids)) != len(instance_ids)
        ):
            raise ValueError(
                "team.heroInstanceIds 必须与已保存英雄一一对应且互不重复"
            )


def rotation_stage_observation(
    catalog_payload: dict[str, Any],
    lifecycle: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    stage_id: int | None = None
    difficulty_id: int | None = None
    if isinstance(state, dict):
        raw_stage = state.get("chimeraStageId")
        raw_difficulty = state.get("allianceChimeraDifficultyId")
        stage_id = raw_stage if isinstance(raw_stage, int) and raw_stage > 0 else None
        difficulty_id = (
            raw_difficulty
            if isinstance(raw_difficulty, int) and raw_difficulty > 0
            else None
        )
    if stage_id is None and isinstance(lifecycle, dict):
        selection = lifecycle.get("selection")
        if isinstance(selection, dict):
            raw_stage = selection.get("stageId")
            if isinstance(raw_stage, int) and raw_stage > 0:
                stage_id = raw_stage

    stage_rotation_index: int | None = None
    catalog = catalog_payload.get("catalog", {})
    for difficulty in catalog.get("difficulties", []) if isinstance(catalog, dict) else []:
        if not isinstance(difficulty, dict):
            continue
        stage_ids = difficulty.get("stageIds", [])
        if stage_id in stage_ids:
            stage_rotation_index = stage_ids.index(stage_id)
            if difficulty_id is None:
                raw_difficulty = difficulty.get("difficultyId")
                if isinstance(raw_difficulty, int):
                    difficulty_id = raw_difficulty
            break
    return {
        "stageId": stage_id,
        "stageRotationIndex": stage_rotation_index,
        "difficultyId": difficulty_id,
    }


def split_rotation_catalog(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    catalog = payload.get("catalog")
    identity = payload.get("identity")
    definitions: dict[str, Any] = {"available": False, "difficulties": []}
    rewards: dict[str, Any] = {"difficulties": []}
    attributes: dict[str, Any] = {
        "metadata": identity.get("metadata") if isinstance(identity, dict) else None,
        "difficulties": [],
    }
    if not isinstance(catalog, dict):
        return definitions, rewards, attributes
    definitions["available"] = catalog.get("available") is True
    for difficulty in catalog.get("difficulties", []):
        if not isinstance(difficulty, dict):
            continue
        difficulty_id = difficulty.get("difficultyId")
        definition_trials: list[dict[str, Any]] = []
        reward_trials: list[dict[str, Any]] = []
        for trial in difficulty.get("trials", []):
            if not isinstance(trial, dict):
                continue
            definition_trials.append(
                {key: value for key, value in trial.items() if key != "reward"}
            )
            reward_trials.append(
                {"id": trial.get("id"), "reward": trial.get("reward")}
            )
        definitions["difficulties"].append(
            {
                "difficultyId": difficulty_id,
                "difficulty": difficulty.get("difficulty"),
                "trials": definition_trials,
            }
        )
        rewards["difficulties"].append(
            {"difficultyId": difficulty_id, "trials": reward_trials}
        )
        attributes["difficulties"].append(
            {
                "difficultyId": difficulty_id,
                "health": difficulty.get("health"),
                "stageIds": difficulty.get("stageIds", []),
            }
        )
    return definitions, rewards, attributes


def archive_rotation_catalog_if_changed(
    ipc: AgentIpc,
    path: Path,
    *,
    state: dict[str, Any] | None = None,
) -> tuple[str, int | None, int | None] | None:
    payload = ipc.rotation_catalog()
    if not isinstance(payload, dict):
        return None
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        return None
    definition_fingerprint = identity.get("trialDefinitionFingerprint")
    reward_fingerprint = identity.get("rewardRotationFingerprint")
    attribute_fingerprint = identity.get("attributeRotationFingerprint")
    if not all(
        isinstance(value, str) and value
        for value in (
            definition_fingerprint,
            reward_fingerprint,
            attribute_fingerprint,
        )
    ):
        return None
    lifecycle = ipc.lifecycle()
    observation = rotation_stage_observation(payload, lifecycle, state)
    key = (
        reward_fingerprint,
        observation.get("stageId"),
        observation.get("difficultyId"),
    )
    memory_key = (
        str(path.resolve()),
        definition_fingerprint,
        reward_fingerprint,
        attribute_fingerprint,
        observation.get("stageId"),
        observation.get("stageRotationIndex"),
        observation.get("difficultyId"),
    )
    if memory_key in _ARCHIVED_ROTATION_OBSERVATIONS and path.exists():
        return key
    definitions, rewards, attributes = split_rotation_catalog(payload)

    mutex = NamedMutex(r"Local\RaidChimeraRotationArchive")
    mutex.acquire()
    try:
        archive: dict[str, Any] = {
            "schemaVersion": 2,
            "trialDefinitions": {},
            "rewardRotations": {},
            "attributeRotations": {},
        }
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(loaded, dict)
                and loaded.get("schemaVersion") == 2
                and isinstance(loaded.get("trialDefinitions"), dict)
                and isinstance(loaded.get("rewardRotations"), dict)
                and isinstance(loaded.get("attributeRotations"), dict)
            ):
                archive = loaded
        changed = False
        now = datetime.now(timezone.utc).isoformat()
        trial_definitions = archive["trialDefinitions"]
        reward_rotations = archive["rewardRotations"]
        attribute_rotations = archive["attributeRotations"]
        if definition_fingerprint not in trial_definitions:
            trial_definitions[definition_fingerprint] = {
                "firstSeenUtc": now,
                "definition": definitions,
            }
            changed = True
        reward_added = False
        if reward_fingerprint not in reward_rotations:
            reward_rotations[reward_fingerprint] = {
                "firstSeenUtc": now,
                "trialDefinitionFingerprint": definition_fingerprint,
                "rewards": rewards,
            }
            reward_added = True
            changed = True
        record = attribute_rotations.get(attribute_fingerprint)
        if not isinstance(record, dict):
            record = {
                "firstSeenUtc": now,
                "attributes": attributes,
                "observations": [],
            }
            attribute_rotations[attribute_fingerprint] = record
            changed = True
        observations = record.setdefault("observations", [])
        observation_key = (
            observation.get("stageId"),
            observation.get("stageRotationIndex"),
            observation.get("difficultyId"),
        )
        has_stage_observation = any(value is not None for value in observation_key)
        if has_stage_observation and not any(
            isinstance(item, dict)
            and (
                item.get("stageId"),
                item.get("stageRotationIndex"),
                item.get("difficultyId"),
            )
            == observation_key
            for item in observations
        ):
            observations.append({**observation, "firstSeenUtc": now})
            changed = True
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(archive, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
            if reward_added:
                print(
                    "已归档新的奇美拉试炼奖励轮换："
                    f"{reward_fingerprint}。",
                    flush=True,
                )
        _ARCHIVED_ROTATION_OBSERVATIONS.add(memory_key)
        return key
    finally:
        mutex.close()


def decision_rotation_observation_key(
    state: dict[str, Any] | None,
) -> tuple[str, int | None, int | None] | None:
    if not isinstance(state, dict):
        return None
    identity = state.get("rotationIdentity")
    if not isinstance(identity, dict):
        return None
    fingerprint = identity.get(
        "rewardRotationFingerprint", identity.get("catalogFingerprint")
    )
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    stage_id = state.get("chimeraStageId")
    difficulty_id = state.get("allianceChimeraDifficultyId")
    return (
        fingerprint,
        stage_id if isinstance(stage_id, int) and stage_id > 0 else None,
        difficulty_id
        if isinstance(difficulty_id, int) and difficulty_id > 0
        else None,
    )


def latest_decision_state(pid: int) -> dict[str, Any] | None:
    try:
        with AgentIpc(pid) as ipc:
            state = ipc.decision()
            return state if is_battle_decision_state(state) else None
    except FileNotFoundError:
        return None


def is_battle_decision_state(state: Any) -> bool:
    """Reject catalog snapshots that share the native decision-state channel."""
    return isinstance(state, dict) and state.get("type") == "decision_state"


def latest_account_state(pid: int) -> dict[str, Any] | None:
    try:
        with AgentIpc(pid) as ipc:
            return ipc.account()
    except FileNotFoundError:
        return None


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def effects_of(entity: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for effect in entity.get("effects", []):
        if not isinstance(effect, dict):
            continue
        name = effect.get("effectKind")
        if isinstance(name, str) and name:
            result.add(name)
        kind_id = effect.get("effectKindId")
        if isinstance(kind_id, int):
            result.add(str(kind_id))
        # effectKind intentionally groups weak and strong variants together.
        # Keep the concrete type ID as well so a strategy can distinguish them.
        type_id = effect.get("effectTypeId")
        if isinstance(type_id, int):
            result.add(str(type_id))
    return result


def state_entities(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = state.get(key, [])
    return [value for value in values if isinstance(value, dict)]


def hydra_head_is_devouring(entity: dict[str, Any]) -> bool:
    devoured_id = entity.get("devouredHeroId")
    return entity.get("isDevouring") is True or (
        isinstance(devoured_id, int)
        and not isinstance(devoured_id, bool)
        and devoured_id >= 0
    )


def hydra_devouring_head_ids(state: dict[str, Any]) -> set[int]:
    """Return authoritative Hydra head actor IDs that currently hold a hero.

    A long Hydra battle keeps old head UI contexts in the client dictionary.
    Once that dictionary outgrows the agent snapshot, a newly spawned head can
    be present in a skill's legal target IDs before it is present in ``bosses``.
    The Devoured effect on the victim still identifies its producer (the head)
    and is therefore the most stable link for rescue targeting.
    """
    result: set[int] = set()
    for boss in state_entities(state, "bosses"):
        actor_id = boss.get("id")
        if (
            isinstance(actor_id, int)
            and not isinstance(actor_id, bool)
            and actor_id >= 0
            and hydra_head_is_devouring(boss)
        ):
            result.add(actor_id)
    for hero in state_entities(state, "heroes"):
        for effect in hero.get("effects", []):
            if not isinstance(effect, dict):
                continue
            if effect.get("effectKind") != "Devoured" and effect.get(
                "effectKindId"
            ) != 9024:
                continue
            producer_id = effect.get("producerId")
            if (
                isinstance(producer_id, int)
                and not isinstance(producer_id, bool)
                and producer_id >= 0
            ):
                result.add(producer_id)
    return result


def hydra_devouring_target_label(
    state: dict[str, Any], actor_id: int, fallback: str
) -> str:
    for hero in state_entities(state, "heroes"):
        for effect in hero.get("effects", []):
            if not isinstance(effect, dict) or effect.get("producerId") != actor_id:
                continue
            if effect.get("effectKind") == "Devoured" or effect.get(
                "effectKindId"
            ) == 9024:
                victim = hero.get("name")
                if isinstance(victim, str) and victim:
                    return f"正在吞噬·{victim}"
    return fallback


def hydra_target_sort_key(entity: dict[str, Any]) -> tuple[float, int]:
    health = entity.get("healthPct")
    actor_id = entity.get("id")
    return (
        float(health)
        if isinstance(health, (int, float)) and not isinstance(health, bool)
        else 101.0,
        int(actor_id)
        if isinstance(actor_id, int) and not isinstance(actor_id, bool)
        else 2**31 - 1,
    )


def hydra_priority_targets(
    selector: dict[str, Any], bosses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return stable identity-first targets without relying on field position."""
    living = [boss for boss in bosses if boss.get("dead") is not True]
    ordered: list[dict[str, Any]] = []
    used_actor_ids: set[int] = set()

    def append_targets(values: list[dict[str, Any]]) -> None:
        for boss in sorted(values, key=hydra_target_sort_key):
            actor_id = boss.get("id")
            if not isinstance(actor_id, int) or actor_id in used_actor_ids:
                continue
            used_actor_ids.add(actor_id)
            ordered.append(boss)

    priorities = selector.get("headTypeIds", [])
    if isinstance(priorities, list):
        for wanted_type_id in priorities:
            if not isinstance(wanted_type_id, int) or isinstance(
                wanted_type_id, bool
            ):
                continue
            append_targets(
                [
                    boss
                    for boss in living
                    if canonical_hydra_head_type_id(boss) == wanted_type_id
                ]
            )

    fallback = selector.get("fallback", "lowestHp")
    remaining = [boss for boss in living if boss.get("id") not in used_actor_ids]
    if fallback == "devouring":
        append_targets(
            [boss for boss in remaining if hydra_head_is_devouring(boss)]
        )
    elif fallback == "exposedNeck":
        append_targets(
            [
                boss
                for boss in remaining
                if hydra_head_is_exposed_neck(boss)
            ]
        )
    elif fallback == "lowestHp":
        append_targets(remaining)
    return ordered


def current_boss(state: dict[str, Any]) -> dict[str, Any] | None:
    bosses = state_entities(state, "bosses")
    condition_boss_id = state.get("_conditionBossId")
    if isinstance(condition_boss_id, int):
        selected = next(
            (boss for boss in bosses if boss.get("id") == condition_boss_id),
            None,
        )
        if selected is not None:
            return selected
    chimera_id = state.get("chimera", {}).get("id")
    return next(
        (boss for boss in bosses if boss.get("id") == chimera_id),
        bosses[0] if bosses else None,
    )


def match_skill_cooldown_conditions(
    requested: Any,
    state_or_heroes: dict[str, Any] | list[dict[str, Any]],
    mode: Any = "all",
) -> bool:
    if mode not in {"all", "any"} or not isinstance(requested, list) or not requested:
        return False
    state = state_or_heroes if isinstance(state_or_heroes, dict) else {}
    heroes = (
        state_entities(state, "heroes")
        if isinstance(state_or_heroes, dict)
        else state_or_heroes
    )

    def stable_type_id(hero: dict[str, Any]) -> int | None:
        source = str(hero.get("avatar") or hero.get("avatarUrl") or "")
        tail = source.rsplit("/", 1)[-1].split("-", 1)[0]
        return int(tail) if tail.isdigit() else None

    def skill_for_hero(
        hero: dict[str, Any], skill_type_id: Any
    ) -> dict[str, Any] | None:
        # The top-level list is the active hero's authoritative HUD/model
        # snapshot. Other heroes retain their own model skill list.
        if hero.get("id") == state.get("activeHeroId"):
            active_skill = next(
                (
                    skill
                    for skill in state.get("skills", [])
                    if isinstance(skill, dict)
                    and skill.get("typeId") == skill_type_id
                ),
                None,
            )
            if active_skill is not None:
                return active_skill
        return next(
            (
                skill
                for skill in hero.get("skills", [])
                if isinstance(skill, dict)
                and skill.get("typeId") == skill_type_id
            ),
            None,
        )

    results: list[bool] = []
    for condition in requested:
        if not isinstance(condition, dict):
            return False
        hero_type_id = condition.get("heroTypeId")
        skill_type_id = condition.get("skillTypeId")
        living = [hero for hero in heroes if hero.get("dead") is not True]
        hero = next(
            (
                candidate
                for candidate in living
                if candidate.get("typeId") == hero_type_id
                or stable_type_id(candidate) == hero_type_id
            ),
            None,
        )
        # Runtime/form TypeIds may differ from the stable identity saved by the
        # editor. An exact skill TypeId is a safe final identity link.
        if hero is None:
            hero = next(
                (
                    candidate
                    for candidate in living
                    if skill_for_hero(candidate, skill_type_id) is not None
                ),
                None,
            )
        skill = skill_for_hero(hero, skill_type_id) if hero is not None else None
        cooldown = skill.get("cooldown") if skill else None
        if (
            not isinstance(cooldown, int)
            or isinstance(cooldown, bool)
            or cooldown < 0
        ):
            results.append(False)
            continue
        lower = condition.get("turnsAtLeast")
        upper = condition.get("turnsAtMost")
        matched = True
        if isinstance(lower, int) and cooldown < lower:
            matched = False
        if isinstance(upper, int) and cooldown > upper:
            matched = False
        results.append(matched)
    return any(results) if mode == "any" else all(results)


def state_for_rule_target(
    state: dict[str, Any], action: Any
) -> dict[str, Any]:
    if not isinstance(action, dict):
        return state
    selector = action.get("target")
    if isinstance(selector, str):
        selector = {"type": selector}
    if not isinstance(selector, dict):
        return state
    selector_type = selector.get("type")
    bosses = [
        boss for boss in state_entities(state, "bosses")
        if boss.get("dead") is not True
    ]
    # Scope target-dependent conditions to the same legal target set that the
    # selected skill will use.  This prevents a preferred Hydra identity from
    # satisfying a condition when that head cannot actually be attacked and
    # the final action would fall through to another head.
    skill = select_skill(action, state) if action.get("type") == "cast" else None
    valid_target_ids = {
        value
        for value in (skill.get("validTargetIds", []) if skill else [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if valid_target_ids:
        bosses = [boss for boss in bosses if boss.get("id") in valid_target_ids]
    selected: dict[str, Any] | None = None
    if selector_type == "devouringHead":
        devouring_ids = hydra_devouring_head_ids(state)
        selected = next(
            (
                boss for boss in bosses
                if hydra_head_is_devouring(boss) or boss.get("id") in devouring_ids
            ),
            None,
        )
        if selected is None:
            unresolved_ids = sorted(
                devouring_ids & valid_target_ids
                if valid_target_ids
                else devouring_ids
            )
            if unresolved_ids:
                selected = {
                    "id": unresolved_ids[0],
                    "isDevouring": True,
                    "effects": [],
                }
    elif selector_type == "exposedNeck":
        selected = next(
            (
                boss for boss in bosses
                if hydra_head_is_exposed_neck(boss)
            ),
            None,
        )
    elif selector_type == "lowestHpBoss":
        selected = min(
            bosses,
            key=lambda boss: boss.get("healthPct")
            if isinstance(boss.get("healthPct"), (int, float))
            else 101,
            default=None,
        )
    elif selector_type == "hydraHeadPriority":
        selected = next(iter(hydra_priority_targets(selector, bosses)), None)
    elif selector_type == "hydraHeadSlot":
        # Legacy rules used an unstable visual slot.  Keep them safe by
        # treating the old selector as a loose lowest-health fallback.
        selected = next(
            iter(
                hydra_priority_targets(
                    {"type": "hydraHeadPriority", "fallback": "lowestHp"},
                    bosses,
                )
            ),
            None,
        )
    if selected is None or not isinstance(selected.get("id"), int):
        return state
    scoped = dict(state)
    scoped["_conditionBossId"] = selected["id"]
    if not any(
        boss.get("id") == selected["id"]
        for boss in state_entities(state, "bosses")
    ):
        scoped["bosses"] = [*state_entities(state, "bosses"), selected]
    return scoped


def trial_status_by_id(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    boss = current_boss(state)
    result: dict[int, dict[str, Any]] = {}
    if not boss:
        return result
    for trial in boss.get("challenges", []):
        if not isinstance(trial, dict):
            continue
        trial_id = trial.get("id")
        if isinstance(trial_id, int):
            result[trial_id] = trial
    return result


def configured_trial_ids(value: Any) -> tuple[int, ...]:
    result: list[int] = []
    for item in as_list(value) if value is not None else []:
        trial_id = item.get("id") if isinstance(item, dict) else item
        if (
            isinstance(trial_id, int)
            and not isinstance(trial_id, bool)
            and trial_id > 0
            and trial_id not in result
        ):
            result.append(trial_id)
    return tuple(result)


def objective_trial_ids(
    config: dict[str, Any], state: dict[str, Any]
) -> tuple[int, ...]:
    """Return selected mandatory trials in prerequisite-first order."""
    objectives = config.get("objectives", {})
    if not isinstance(objectives, dict):
        objectives = {}
    configured_mandatory_ids = configured_trial_ids(
        objectives.get("mandatoryTrials", objectives.get("mandatoryTrialIds", []))
    )
    statuses = trial_status_by_id(state)
    mandatory_order: list[int] = []
    visiting: set[int] = set()

    def add_trial_with_prerequisites(trial_id: int) -> None:
        if trial_id in mandatory_order or trial_id in visiting:
            return
        visiting.add(trial_id)
        prerequisites = configured_trial_ids(
            statuses.get(trial_id, {}).get("requiredPrerequisiteTrialIds", [])
        )
        for prerequisite_id in prerequisites:
            add_trial_with_prerequisites(prerequisite_id)
        visiting.discard(trial_id)
        if trial_id not in mandatory_order:
            mandatory_order.append(trial_id)

    for configured_id in configured_mandatory_ids:
        add_trial_with_prerequisites(configured_id)
    return tuple(mandatory_order)


def evaluate_objectives(
    config: dict[str, Any], state: dict[str, Any]
) -> ObjectiveReport:
    objectives = config.get("objectives", {})
    if not isinstance(objectives, dict):
        objectives = {}
    statuses = trial_status_by_id(state)
    mandatory_ids = objective_trial_ids(config, state)
    completed = tuple(
        trial_id
        for trial_id in mandatory_ids
        if statuses.get(trial_id, {}).get("completed") is True
    )
    missing = tuple(trial_id for trial_id in mandatory_ids if trial_id not in completed)
    boss_turn = state.get("chimera", {}).get("turnCount")

    def explicitly_or_deadline_impossible(trial_id: int) -> bool:
        status = statuses.get(trial_id, {})
        if status.get("possible") is False or status.get("impossible") is True:
            return True
        deadline = status.get("lastEligibleBossTurn")
        return (
            isinstance(boss_turn, int)
            and not isinstance(boss_turn, bool)
            and isinstance(deadline, int)
            and not isinstance(deadline, bool)
            and boss_turn > deadline
        )

    # Fail closed: only runtime-published impossibility or an already-expired
    # runtime-published deadline may trigger a free regroup. A form transition,
    # missing progress, or a guessed damage forecast is not enough.
    impossible = tuple(
        trial_id
        for trial_id in missing
        if explicitly_or_deadline_impossible(trial_id)
    )
    battle = state.get("battle", {})
    damage = battle.get("currentDamage", 0)
    minimum_damage = objectives.get("minimumDamage", 0)
    return ObjectiveReport(
        mandatory_trial_ids=mandatory_ids,
        completed_trial_ids=completed,
        missing_trial_ids=missing,
        impossible_trial_ids=impossible,
        current_damage=float(damage) if isinstance(damage, (int, float)) else 0.0,
        minimum_damage=(
            float(minimum_damage)
            if isinstance(minimum_damage, (int, float))
            else 0.0
        ),
    )


def match_effect_condition(
    entity: dict[str, Any] | None,
    required: Any,
    *,
    missing: bool,
) -> bool:
    if entity is None:
        return False
    actual = effects_of(entity)
    wanted = {str(value) for value in as_list(required)}
    if not wanted:
        return False
    return wanted.isdisjoint(actual) if missing else wanted.issubset(actual)


def effect_matches(effect: dict[str, Any], selector: Any) -> bool:
    if isinstance(selector, (str, int)):
        selector = {"kind": selector}
    if not isinstance(selector, dict):
        return False
    if not selector:
        return False
    wanted_kind = selector.get("kind")
    if wanted_kind is not None and str(wanted_kind) not in {
        str(effect.get("effectKind")),
        str(effect.get("effectKindId")),
    }:
        return False
    wanted_type = selector.get("effectTypeId")
    if wanted_type is not None and effect.get("effectTypeId") != wanted_type:
        return False
    turns_left = effect.get("turnsLeft")
    for key, comparison in (
        ("turnsAtLeast", lambda actual, wanted: actual >= wanted),
        ("turnsAtMost", lambda actual, wanted: actual <= wanted),
    ):
        if key not in selector:
            continue
        wanted_turns = selector[key]
        if (
            not isinstance(turns_left, int)
            or isinstance(turns_left, bool)
            or not isinstance(wanted_turns, int)
            or isinstance(wanted_turns, bool)
            or wanted_turns < 0
            or not comparison(turns_left, wanted_turns)
        ):
            return False
    producer_id = selector.get("producerId")
    if producer_id is not None and effect.get("producerId") != producer_id:
        return False
    return True


def entity_has_effect(entity: dict[str, Any] | None, selector: Any) -> bool:
    if entity is None:
        return False
    return any(
        isinstance(effect, dict) and effect_matches(effect, selector)
        for effect in entity.get("effects", [])
    )


def effect_count(entity: dict[str, Any] | None) -> int:
    if entity is None:
        return 0
    return sum(isinstance(effect, dict) for effect in entity.get("effects", []))


# The agent exposes the game's effect-kind ID for live effects. RAID keeps
# ordinary buffs in the 2xxx range and debuffs in the 3xxx range. Type-ID
# fallbacks cover saved capability data and older captures that did not retain
# the kind ID correctly.
BUFF_EFFECT_TYPE_IDS = frozenset(
    {
        50, 60, 91, 100, 120, 121, 140, 141, 160, 161, 220, 221, 240,
        241, 260, 261, 280, 310, 320, 370, 410, 411, 480, 481, 511, 600,
        610, 620, 650, 670, 710, 711, 760, 780, 840, 870, 880, 991,
    }
)
DEBUFF_EFFECT_TYPE_IDS = frozenset(
    {
        10, 20, 30, 40, 70, 80, 81, 110, 130, 131, 150, 151, 170, 171,
        230, 231, 250, 251, 270, 271, 290, 350, 351, 431, 440, 460, 470,
        490, 491, 500, 630, 640, 720, 721, 740, 860, 930, 940,
    }
)


def effect_polarity(effect: dict[str, Any] | None) -> str | None:
    if not isinstance(effect, dict):
        return None
    effect_kind_id = effect.get("effectKindId")
    if isinstance(effect_kind_id, int) and not isinstance(effect_kind_id, bool):
        if 2_000 <= effect_kind_id < 3_000:
            return "buff"
        if 3_000 <= effect_kind_id < 4_000:
            return "debuff"
    effect_type_id = effect.get("effectTypeId")
    if isinstance(effect_type_id, int) and not isinstance(effect_type_id, bool):
        if effect_type_id in BUFF_EFFECT_TYPE_IDS:
            return "buff"
        if effect_type_id in DEBUFF_EFFECT_TYPE_IDS:
            return "debuff"
    return None


def buff_count(entity: dict[str, Any] | None) -> int:
    if entity is None:
        return 0
    return sum(
        effect_polarity(effect) == "buff"
        for effect in entity.get("effects", [])
        if isinstance(effect, dict)
    )


def debuff_count(entity: dict[str, Any] | None) -> int:
    if entity is None:
        return 0
    return sum(
        effect_polarity(effect) == "debuff"
        for effect in entity.get("effects", [])
        if isinstance(effect, dict)
    )


def current_stage_rotation_index(state: dict[str, Any]) -> int | None:
    stage_id = state.get("chimeraStageId")
    catalog = state.get("trialCatalog")
    if not isinstance(stage_id, int) or not isinstance(catalog, dict):
        return None
    for difficulty in catalog.get("difficulties", []):
        if not isinstance(difficulty, dict):
            continue
        stage_ids = difficulty.get("stageIds", [])
        if stage_id in stage_ids:
            return stage_ids.index(stage_id)
    return None


def turns_until_form_change(state: dict[str, Any]) -> int | None:
    identity = state.get("rotationIdentity")
    metadata = identity.get("metadata") if isinstance(identity, dict) else None
    interval = metadata.get("turnsBetweenForms") if isinstance(metadata, dict) else None
    if not isinstance(interval, int) or interval <= 0:
        interval = FIXED_CHIMERA_FORM_INTERVAL
    turn_count = state.get("chimera", {}).get("turnCount")
    if (
        not isinstance(turn_count, int)
        or turn_count < 0
    ):
        return None
    elapsed = turn_count % interval
    if turn_count == 0:
        return interval
    return 0 if elapsed == 0 else interval - elapsed


def canonical_chimera_form(value: Any) -> Any:
    if isinstance(value, str) and value.casefold() in {"snake", "viper"}:
        return "Snake"
    return value


def next_chimera_form(state: dict[str, Any]) -> str | None:
    identity = state.get("rotationIdentity")
    metadata = identity.get("metadata") if isinstance(identity, dict) else None
    interval = metadata.get("turnsBetweenForms") if isinstance(metadata, dict) else None
    if not isinstance(interval, int) or interval <= 0:
        interval = FIXED_CHIMERA_FORM_INTERVAL
    turn_count = state.get("chimera", {}).get("turnCount")
    if not isinstance(turn_count, int) or turn_count < 0:
        return None
    phase_index = (
        0
        if turn_count == 0
        else ((turn_count - 1) // interval) % len(FIXED_CHIMERA_FORM_CYCLE)
    )
    return FIXED_CHIMERA_FORM_CYCLE[
        (phase_index + 1) % len(FIXED_CHIMERA_FORM_CYCLE)
    ]


def selected_ally(
    heroes: list[dict[str, Any]], selector: dict[str, Any]
) -> dict[str, Any] | None:
    actor_id = selector.get("actorId")
    hero_type_id = selector.get("heroTypeId")
    if actor_id is None and hero_type_id is None:
        return None
    return next(
        (
            hero
            for hero in heroes
            if (actor_id is None or hero.get("id") == actor_id)
            and (hero_type_id is None or hero.get("typeId") == hero_type_id)
        ),
        None,
    )


def match_effect_conditions(
    required: Any,
    boss: dict[str, Any] | None,
    bosses: list[dict[str, Any]],
    heroes: list[dict[str, Any]],
    mode: Any = "all",
) -> bool:
    """Match the unified UI effect-condition list with explicit group logic."""
    if mode not in {"all", "any"} or not isinstance(required, list) or not required:
        return False
    results: list[bool] = []
    for condition in required:
        if not isinstance(condition, dict):
            return False
        target = condition.get("target")
        presence = condition.get("presence")
        selector = condition.get("effect")
        if target not in {
            "boss", "bossPriority", "bossAny", "bossAll", "ally"
        } or presence not in {"has", "missing"}:
            return False
        if not isinstance(selector, dict) or not selector:
            return False
        if target == "ally":
            entity = selected_ally(heroes, condition)
            if entity is None:
                return False
            present = entity_has_effect(entity, selector)
            results.append(present if presence == "has" else not present)
            continue
        if target in {"boss", "bossPriority"}:
            if boss is None:
                return False
            present = entity_has_effect(boss, selector)
            results.append(present if presence == "has" else not present)
            continue
        living_bosses = [item for item in bosses if item.get("dead") is not True]
        if not living_bosses:
            return False
        presence_results = [
            entity_has_effect(entity, selector) if presence == "has"
            else not entity_has_effect(entity, selector)
            for entity in living_bosses
        ]
        results.append(
            all(presence_results) if target == "bossAll" else any(presence_results)
        )
    return any(results) if mode == "any" else all(results)


def annotate_team_positions(
    state: dict[str, Any], lifecycle: dict[str, Any] | None
) -> None:
    """Attach stable 1-based preparation slots to live battle hero entities."""
    if not isinstance(lifecycle, dict):
        return
    screen = lifecycle.get("screen")
    section = lifecycle.get("battle" if screen == "battle" else "selection")
    if not isinstance(section, dict):
        return
    type_ids = section.get("heroTypeIds")
    expected_team_size = 6 if state.get("bossMode") == "hydra" else 5
    if not isinstance(type_ids, list) or len(type_ids) != expected_team_size:
        return
    normalized = [
        value
        for value in type_ids
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    if len(normalized) != expected_team_size:
        return
    state["teamHeroTypeIds"] = normalized
    instance_ids = section.get("heroIds")
    if isinstance(instance_ids, list) and len(instance_ids) == expected_team_size:
        state["teamHeroInstanceIds"] = list(instance_ids)

    heroes = state_entities(state, "heroes")
    assigned: set[int] = set()
    for position, type_id in enumerate(normalized, 1):
        hero = next(
            (
                item
                for item in heroes
                if id(item) not in assigned and item.get("typeId") == type_id
            ),
            None,
        )
        if hero is not None:
            hero["teamPosition"] = position
            assigned.add(id(hero))


def select_transform_skill(
    state: dict[str, Any], action: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the current form's ready self-targeting metamorph toggle."""
    if state.get("activeHeroIsMetamorph") is not True:
        return None
    active_hero_id = state.get("activeHeroId")
    if not isinstance(active_hero_id, int):
        return None
    candidates = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("ready") is True
        and skill.get("passive") is not True
        and skill.get("blocked") is not True
        and isinstance(skill.get("slot"), int)
        and skill["slot"] >= 4
        and active_hero_id in skill.get("validTargetIds", [])
        and (
            not isinstance(action, dict)
            or "skillTypeId" not in action
            or skill.get("typeId") == action.get("skillTypeId")
        )
        and (
            not isinstance(action, dict)
            or "skillSlot" not in action
            or skill.get("slot") == action.get("skillSlot")
        )
    ]
    return max(candidates, key=lambda skill: skill["slot"], default=None)


def matches(when: dict[str, Any], state: dict[str, Any]) -> bool:
    if not isinstance(when, dict) or any(
        not isinstance(key, str) or key not in SUPPORTED_CONDITION_KEYS
        for key in when
    ):
        return False
    battle = state.get("battle", {})
    chimera = state.get("chimera", {})
    heroes = state_entities(state, "heroes")
    bosses = state_entities(state, "bosses")
    active = next(
        (hero for hero in heroes if hero.get("id") == state.get("activeHeroId")),
        None,
    )
    boss = current_boss(state)
    trials = trial_status_by_id(state)

    equality_fields = {
        "form": chimera.get("currentForm"),
        "activeHeroTypeId": state.get("activeHeroTypeId"),
        "activeHeroFormIndex": state.get("activeHeroFormIndex"),
        "activeHeroIsMetamorph": state.get("activeHeroIsMetamorph"),
        "activeHeroIsTransformed": state.get("activeHeroIsTransformed"),
        "transformationReady": select_transform_skill(state) is not None,
        "round": battle.get("round"),
        "turn": battle.get("turn"),
        "playerTurnCount": battle.get("playerTurnCount"),
        "chimeraTurnCount": chimera.get("turnCount"),
        "activeHeroTurnCount": state.get("activeHeroTurnCount"),
        "targetHeadTypeId": canonical_hydra_head_type_id(boss) if boss else None,
        "targetHeadState": boss.get("headState") if boss else None,
        "allianceDifficultyId": state.get("allianceChimeraDifficultyId"),
        "chimeraStageId": state.get("chimeraStageId"),
        "stageRotationIndex": current_stage_rotation_index(state),
        "nextForm": next_chimera_form(state),
        "catalogFingerprint": (
            state.get("rotationIdentity", {}).get("catalogFingerprint")
            if isinstance(state.get("rotationIdentity"), dict)
            else None
        ),
        "trialDefinitionFingerprint": (
            state.get("rotationIdentity", {}).get("trialDefinitionFingerprint")
            if isinstance(state.get("rotationIdentity"), dict)
            else None
        ),
        "rewardRotationFingerprint": (
            state.get("rotationIdentity", {}).get("rewardRotationFingerprint")
            if isinstance(state.get("rotationIdentity"), dict)
            else None
        ),
        "attributeRotationFingerprint": (
            state.get("rotationIdentity", {}).get("attributeRotationFingerprint")
            if isinstance(state.get("rotationIdentity"), dict)
            else None
        ),
    }
    for key, actual in equality_fields.items():
        expected = as_list(when[key]) if key in when else []
        if key in {"form", "nextForm"}:
            actual = canonical_chimera_form(actual)
            expected = [canonical_chimera_form(value) for value in expected]
        if key in when and actual not in expected:
            return False

    comparisons = {
        "turnAtLeast": (battle.get("turn"), lambda a, b: a >= b),
        "turnAtMost": (battle.get("turn"), lambda a, b: a <= b),
        "chimeraTurnAtLeast": (
            chimera.get("turnCount"),
            lambda a, b: a >= b,
        ),
        "chimeraTurnAtMost": (
            chimera.get("turnCount"),
            lambda a, b: a <= b,
        ),
        "activeHeroHpPctBelow": (
            active.get("healthPct") if active else None,
            lambda a, b: a < b,
        ),
        "bossHpPctBelow": (
            boss.get("healthPct") if boss else None,
            lambda a, b: a < b,
        ),
        "currentDamageAtLeast": (
            battle.get("currentDamage"),
            lambda a, b: a >= b,
        ),
        "currentDamageBelow": (
            battle.get("currentDamage"),
            lambda a, b: a < b,
        ),
        "hydraHeadCountAtLeast": (
            sum(item.get("dead") is not True for item in bosses),
            lambda a, b: a >= b,
        ),
        "hydraHeadCountAtMost": (
            sum(item.get("dead") is not True for item in bosses),
            lambda a, b: a <= b,
        ),
        "devouringHeadsAtLeast": (
            sum(
                hydra_head_is_devouring(item)
                for item in bosses
            ),
            lambda a, b: a >= b,
        ),
        "exposedNecksAtLeast": (
            sum(
                hydra_head_is_exposed_neck(item)
                for item in bosses
            ),
            lambda a, b: a >= b,
        ),
        "targetHeadDigestionTurnsAtLeast": (
            boss.get("digestionTurns") if boss else None,
            lambda a, b: a >= b,
        ),
        "targetHeadDigestionTurnsAtMost": (
            boss.get("digestionTurns") if boss else None,
            lambda a, b: a <= b,
        ),
        "bossEffectSlotsAtLeast": (effect_count(boss), lambda a, b: a >= b),
        "bossEffectSlotsAtMost": (effect_count(boss), lambda a, b: a <= b),
        "activeHeroEffectSlotsAtLeast": (
            effect_count(active),
            lambda a, b: a >= b,
        ),
        "activeHeroEffectSlotsAtMost": (
            effect_count(active),
            lambda a, b: a <= b,
        ),
        "anyAllyEffectSlotsAtLeast": (
            max((effect_count(hero) for hero in heroes), default=0),
            lambda a, b: a >= b,
        ),
        "allAlliesEffectSlotsAtMost": (
            max((effect_count(hero) for hero in heroes), default=0),
            lambda a, b: a <= b,
        ),
        "deadAlliesAtLeast": (
            sum(hero.get("dead") is True for hero in heroes),
            lambda a, b: a >= b,
        ),
        "livingAlliesAtLeast": (
            sum(hero.get("dead") is not True for hero in heroes),
            lambda a, b: a >= b,
        ),
        "turnsUntilFormChangeAtLeast": (
            turns_until_form_change(state),
            lambda a, b: a >= b,
        ),
        "turnsUntilFormChangeAtMost": (
            turns_until_form_change(state),
            lambda a, b: a <= b,
        ),
    }
    for key, (actual, operation) in comparisons.items():
        if key not in when:
            continue
        wanted = when[key]
        if (
            not isinstance(actual, (int, float))
            or isinstance(actual, bool)
            or not isinstance(wanted, (int, float))
            or isinstance(wanted, bool)
            or not operation(actual, wanted)
        ):
            return False

    if "anyAllyHpPctBelow" in when:
        threshold = when["anyAllyHpPctBelow"]
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            return False
        if not any(
            isinstance(hero.get("healthPct"), (int, float))
            and hero["healthPct"] < threshold
            and not hero.get("dead", False)
            for hero in heroes
        ):
            return False

    effect_checks = (
        ("bossHasEffects", boss, False),
        ("bossMissingEffects", boss, True),
        ("activeHeroHasEffects", active, False),
        ("activeHeroMissingEffects", active, True),
    )
    for key, entity, missing in effect_checks:
        if key in when and not match_effect_condition(
            entity, when[key], missing=missing
        ):
            return False

    detailed_effect_checks = (
        ("bossHasEffect", boss, False),
        ("bossMissingEffect", boss, True),
        ("activeHeroHasEffect", active, False),
        ("activeHeroMissingEffect", active, True),
    )
    for key, entity, missing in detailed_effect_checks:
        if key not in when:
            continue
        present = entity_has_effect(entity, when[key])
        if present == missing:
            return False

    living_heroes = [hero for hero in heroes if hero.get("dead") is not True]
    for key, mode in (
        ("anyAllyHasEffect", "any_has"),
        ("anyAllyMissingEffect", "any_missing"),
        ("allAlliesHaveEffect", "all_have"),
    ):
        if key not in when:
            continue
        selector = when[key]
        if not living_heroes:
            return False
        presence = [entity_has_effect(hero, selector) for hero in living_heroes]
        if mode == "any_has" and not any(presence):
            return False
        if mode == "any_missing" and all(presence):
            return False
        if mode == "all_have" and not all(presence):
            return False

    for key, missing in (
        ("allyHasEffect", False),
        ("allyMissingEffect", True),
    ):
        requested = when.get(key)
        if requested is None:
            continue
        if not isinstance(requested, dict):
            return False
        ally = selected_ally(heroes, requested)
        effect_selector = requested.get("effect")
        if ally is None or effect_selector is None:
            return False
        present = entity_has_effect(ally, effect_selector)
        if present == missing:
            return False

    if "effectConditions" in when and not match_effect_conditions(
        when["effectConditions"],
        boss,
        bosses,
        heroes,
        when.get("effectConditionsMode", "all"),
    ):
        return False

    if "skillCooldownConditions" in when and not match_skill_cooldown_conditions(
        when["skillCooldownConditions"],
        state,
        when.get("skillCooldownConditionsMode", "all"),
    ):
        return False

    if (
        "effectConditionsMode" in when and "effectConditions" not in when
    ) or (
        "skillCooldownConditionsMode" in when
        and "skillCooldownConditions" not in when
    ):
        return False

    for key, operation in (
        ("allyEffectSlotsAtLeast", lambda actual, wanted: actual >= wanted),
        ("allyEffectSlotsAtMost", lambda actual, wanted: actual <= wanted),
    ):
        requested = when.get(key)
        if requested is None:
            continue
        if not isinstance(requested, dict):
            return False
        ally = selected_ally(heroes, requested)
        wanted = requested.get("count")
        if (
            ally is None
            or not isinstance(wanted, int)
            or not operation(effect_count(ally), wanted)
        ):
            return False

    completed_ids = {
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("completed") is True
    }
    started_ids = {
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("started") is True
    }
    active_ids = {
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("activeInChain") is True
        and trial.get("completed") is not True
    }
    eligible_ids = {
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("eligibleNow") is True
        and trial.get("completed") is not True
    }
    locked_ids = {
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("chainState") == "locked"
    }
    set_checks = (
        ("completedTrialsAll", completed_ids, "all"),
        ("completedTrialsAny", completed_ids, "any"),
        ("incompleteTrialsAll", completed_ids, "none"),
        ("startedTrialsAll", started_ids, "all"),
        ("startedTrialsAny", started_ids, "any"),
        ("activeTrialsAll", active_ids, "all"),
        ("activeTrialsAny", active_ids, "any"),
        ("eligibleTrialsAll", eligible_ids, "all"),
        ("eligibleTrialsAny", eligible_ids, "any"),
        ("lockedTrialsAny", locked_ids, "any"),
        (
            "possibleTrialsAll",
            {
                trial_id
                for trial_id, trial in trials.items()
                if trial.get("possible") is True or trial.get("completed") is True
            },
            "all",
        ),
        (
            "impossibleTrialsAny",
            {
                trial_id
                for trial_id, trial in trials.items()
                if trial.get("possible") is False
                or trial.get("impossible") is True
            },
            "any",
        ),
    )
    for key, actual, mode in set_checks:
        if key not in when:
            continue
        wanted = set(configured_trial_ids(when[key]))
        if not wanted:
            return False
        if mode == "all" and not wanted.issubset(actual):
            return False
        if mode == "any" and wanted.isdisjoint(actual):
            return False
        if mode == "none" and not wanted.isdisjoint(actual):
            return False

    for key, comparison in (
        ("trialProgressAtLeast", lambda actual, wanted: actual >= wanted),
        ("trialProgressBelow", lambda actual, wanted: actual < wanted),
    ):
        requested = when.get(key)
        if requested is None:
            continue
        if not isinstance(requested, dict):
            return False
        if not requested:
            return False
        for raw_id, threshold in requested.items():
            try:
                trial_id = int(raw_id)
            except (TypeError, ValueError):
                return False
            progress = trials.get(trial_id, {}).get("progressRatio")
            if (
                not isinstance(progress, (int, float))
                or isinstance(progress, bool)
                or not isinstance(threshold, (int, float))
                or isinstance(threshold, bool)
                or not comparison(progress, threshold)
            ):
                return False
    return True


def select_skill(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    if "skillTypeId" not in action and "skillSlot" not in action:
        return None
    for skill in state.get("skills", []):
        if not isinstance(skill, dict) or not skill.get("ready", False):
            continue
        if "skillTypeId" in action and skill.get("typeId") != action["skillTypeId"]:
            continue
        if "skillSlot" in action and skill.get("slot") != action["skillSlot"]:
            continue
        return skill
    return None


def select_target(
    selector: Any,
    skill: dict[str, Any],
    state: dict[str, Any],
) -> tuple[int, str] | None:
    valid_ordered: list[int] = []
    for value in skill.get("validTargetIds", []):
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value not in valid_ordered
        ):
            valid_ordered.append(value)
    valid = set(valid_ordered)
    if not valid:
        return None
    if isinstance(selector, str):
        selector = {"type": selector}
    if not isinstance(selector, dict):
        return None

    selector_type = selector.get("type", "boss")
    heroes = state_entities(state, "heroes")
    bosses = state_entities(state, "bosses")
    active_hero_id = state.get("activeHeroId")
    chimera_id = state.get("chimera", {}).get("id")
    devouring_head_ids = hydra_devouring_head_ids(state)

    def unresolved_hydra_target() -> tuple[int, str] | None:
        """Trust the current SkillData target window over a stale boss cache.

        Hydra replaces heads with new battle actors throughout a long fight.  An
        older/bounded boss UI snapshot can therefore contain only historical
        actors while GetAcceptableTargets already returns the four current head
        IDs.  Those IDs are the authoritative legality check used by the game,
        so a generic/automatic target may safely use one even before its head
        metadata reaches the snapshot.
        """
        hydra = state.get("hydra")
        if state.get("bossMode") != "hydra" and not (
            isinstance(hydra, dict) and hydra.get("active") is True
        ):
            return None
        hero_ids = {
            item.get("id")
            for item in heroes
            if isinstance(item.get("id"), int)
            and not isinstance(item.get("id"), bool)
        }
        for target_id in valid_ordered:
            if target_id not in hero_ids:
                return (
                    target_id,
                    f"六头蛇目标 {target_id}（蛇头快照未同步，已按技能合法目标选择）",
                )
        return None

    if selector_type == "auto":
        dead_candidates = [
            item
            for item in heroes
            if item.get("id") in valid and item.get("dead") is True
        ]
        if dead_candidates:
            target = min(
                dead_candidates,
                key=lambda item: int(item.get("teamPosition", 99)),
            )
            return target["id"], f"复活·{target.get('name', target['id'])}"
        if isinstance(chimera_id, int) and chimera_id in valid:
            return chimera_id, "奇美拉"
        if isinstance(active_hero_id, int) and active_hero_id in valid:
            return active_hero_id, "自己"
        valid_bosses = [
            item
            for item in bosses
            if item.get("id") in valid and item.get("dead") is not True
        ]
        unresolved_devouring_ids = sorted(
            target_id
            for target_id in devouring_head_ids
            if target_id in valid
            and not any(item.get("id") == target_id for item in valid_bosses)
        )
        if unresolved_devouring_ids:
            target_id = unresolved_devouring_ids[0]
            return target_id, hydra_devouring_target_label(
                state, target_id, f"正在吞噬的蛇头 {target_id}"
            )
        if valid_bosses:
            target = next(
                (
                    item
                    for item in valid_bosses
                    if hydra_head_is_devouring(item)
                    or item.get("id") in devouring_head_ids
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        item
                        for item in valid_bosses
                        if hydra_head_is_exposed_neck(item)
                    ),
                    None,
                )
            if target is None:
                target = min(
                    valid_bosses,
                    key=lambda item: item.get("healthPct")
                    if isinstance(item.get("healthPct"), (int, float))
                    else 101,
                )
            target_label = target.get("name", "Boss")
            if target.get("id") in devouring_head_ids:
                target_label = hydra_devouring_target_label(
                    state,
                    target["id"],
                    f"正在吞噬·{target_label}",
                )
            return target["id"], target_label
        candidates = [
            item
            for item in heroes
            if item.get("id") in valid and not item.get("dead", False)
        ]
        if not candidates:
            return unresolved_hydra_target()
        target = min(
            candidates,
            key=lambda item: item.get("healthPct")
            if isinstance(item.get("healthPct"), (int, float))
            else 101,
        )
        return target["id"], target.get("name", str(target["id"]))
    if selector_type == "boss":
        if isinstance(chimera_id, int) and chimera_id in valid:
            return chimera_id, "奇美拉"
        boss = next((item for item in bosses if item.get("id") in valid), None)
        return (
            (boss["id"], boss.get("name", "Boss"))
            if boss
            else unresolved_hydra_target()
        )
    if selector_type in {
        "lowestHpBoss",
        "devouringHead",
        "exposedNeck",
        "hydraHeadPriority",
        "hydraHeadSlot",
    }:
        candidates = [
            item
            for item in bosses
            if item.get("id") in valid and item.get("dead") is not True
        ]
        if selector_type == "devouringHead":
            candidates = [
                item
                for item in candidates
                if hydra_head_is_devouring(item)
                or item.get("id") in devouring_head_ids
            ]
            if not candidates:
                unresolved_ids = sorted(devouring_head_ids & valid)
                if unresolved_ids:
                    target_id = unresolved_ids[0]
                    return target_id, hydra_devouring_target_label(
                        state, target_id, f"正在吞噬的蛇头 {target_id}"
                    )
        elif selector_type == "exposedNeck":
            candidates = [
                item
                for item in candidates
                if hydra_head_is_exposed_neck(item)
            ]
        elif selector_type == "hydraHeadPriority":
            candidates = hydra_priority_targets(selector, candidates)
        elif selector_type == "hydraHeadSlot":
            candidates = hydra_priority_targets(
                {"type": "hydraHeadPriority", "fallback": "lowestHp"},
                candidates,
            )
        if not candidates:
            return None
        target = (
            candidates[0]
            if selector_type in {"hydraHeadPriority", "hydraHeadSlot"}
            else min(candidates, key=hydra_target_sort_key)
        )
        target_label = target.get("name", f"蛇头 {target['id']}")
        if selector_type == "devouringHead":
            target_label = hydra_devouring_target_label(
                state,
                target["id"],
                f"正在吞噬·{target_label}",
            )
        return target["id"], target_label
    if selector_type == "self":
        return (
            (active_hero_id, "自己")
            if isinstance(active_hero_id, int) and active_hero_id in valid
            else None
        )
    if selector_type == "allyHeroTypeId":
        wanted = selector.get("heroTypeId")
        hero = next(
            (
                item
                for item in heroes
                if item.get("typeId") == wanted and item.get("id") in valid
            ),
            None,
        )
        return (hero["id"], hero.get("name", str(wanted))) if hero else None
    if selector_type == "allyPosition":
        position = selector.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= 6:
            return None
        hero = next(
            (
                item
                for item in heroes
                if item.get("teamPosition") == position and item.get("id") in valid
            ),
            None,
        )
        return (
            (hero["id"], f"{position}号位·{hero.get('name', hero['id'])}")
            if hero
            else None
        )
    if selector_type == "lowestHpAlly":
        candidates = [
            item
            for item in heroes
            if item.get("id") in valid
            and not item.get("dead", False)
            and isinstance(item.get("healthPct"), (int, float))
        ]
        if not candidates:
            return None
        hero = min(candidates, key=lambda item: item["healthPct"])
        return hero["id"], hero.get("name", str(hero["id"]))
    return None


def safety_reason(
    state: dict[str, Any],
    max_age_ms: int,
    *,
    ignore_freshness: bool = False,
) -> str | None:
    battle = state.get("battle", {})
    active_hero_id = state.get("activeHeroId")
    active_hero_type_id = state.get("activeHeroTypeId")
    active_entity = next(
        (
            hero
            for hero in state_entities(state, "heroes")
            if hero.get("id") == active_hero_id
            and hero.get("typeId") == active_hero_type_id
        ),
        None,
    )
    if ACTIVE_BOSS_MODE == "hydra":
        bosses = state_entities(state, "bosses")
        hydra_identity_confirmed = (
            state.get("bossMode") == "hydra"
            or battle.get("hydraBattle") is True
            or any(
                boss.get("isHydraHead") is True
                or hydra_head_is_exposed_neck(boss)
                or hydra_head_has_native_markers(boss)
                for boss in bosses
            )
        )
        mode_checks = (
            (hydra_identity_confirmed, "当前回合快照尚未确认是六头蛇战斗"),
        )
    else:
        chimera = state.get("chimera", {})
        chimera_identity_confirmed = (
            state.get("bossMode") == "chimera"
            or battle.get("chimeraPreset") is True
            or (
                isinstance(chimera, dict)
                and isinstance(chimera.get("typeId"), int)
                and chimera.get("typeId", 0) > 0
            )
        )
        mode_checks = (
            (chimera_identity_confirmed, "当前回合快照尚未确认是奇美拉战斗"),
        )
    checks = (*mode_checks,
        (battle.get("finished") is False, "战斗已结束"),
        (battle.get("autoMode") is False, "游戏处于自动模式"),
        (battle.get("waitingForManualCommand") is True, "当前不等待手动指令"),
        (
            isinstance(active_hero_id, int)
            and active_hero_id >= 0
            and active_entity is not None,
            "行动英雄尚未与战斗实体同步",
        ),
    )
    for passed, reason in checks:
        if not passed:
            return reason
    if ignore_freshness:
        return None
    observed = state.get("observedAtTick")
    if not isinstance(observed, int):
        return "快照没有时间戳"
    age = int(kernel32.GetTickCount64()) - observed
    if age < 0 or age > max_age_ms:
        return f"快照已过期（{age} ms）"
    return None


def wait_for_command_ack(
    ipc: AgentIpc,
    *,
    session_id: int,
    nonce: int,
    timeout_seconds: float = COMMAND_ACK_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        acknowledgement = ipc.acknowledgement()
        if (
            acknowledgement
            and acknowledgement.get("sessionId") == session_id
            and acknowledgement.get("nonce") == nonce
        ):
            return acknowledgement
        time.sleep(0.01)
    return None


def lifecycle_nonce(seed: int, offset: int) -> int:
    payload = ((int(seed) & 0x000FFFFF) * 512 + int(offset)) & 0x7FFFFFFF
    return 0x80000000 | payload


def wait_for_lifecycle_screen(
    ipc: AgentIpc,
    session_id: int,
    wanted: str,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        last = ipc.lifecycle()
        if isinstance(last, dict) and last.get("screen") == wanted:
            return last
        time.sleep(0.05)
    raise RuntimeError(f"等待进入 {wanted} 超时；最后状态：{last}")


def turn_key(state: dict[str, Any]) -> tuple[Any, ...]:
    battle = state.get("battle", {})
    pointers = state.get("pointers", {})
    return (
        state.get("pid"),
        pointers.get("mode"),
        battle.get("round"),
        battle.get("turn"),
        battle.get("playerTurnCount"),
        state.get("activeHeroId"),
        state.get("activeHeroTurnCount"),
        state.get("activeHeroFormIndex"),
        state.get("activeHeroSkillsUpdateCounter"),
    )


def hero_action_window_key(state: dict[str, Any]) -> tuple[Any, ...]:
    """Identify one hero action window without volatile skill refresh counters."""
    battle = state.get("battle", {})
    return (
        state.get("pid"),
        battle.get("round"),
        battle.get("turn"),
        battle.get("playerTurnCount"),
        state.get("activeHeroId"),
        state.get("activeHeroTurnCount"),
        state.get("activeHeroFormIndex"),
    )


def annotate_chimera_form_first_turn(
    state: dict[str, Any], runtime_state: dict[str, Any]
) -> None:
    """Mark the active hero's first observed action in each Chimera form phase.

    The game only exposes a battle-wide personal turn counter. This tracker
    follows real form transitions and remembers which heroes have completed an
    action window in the current phase. A controller attached mid-phase stays
    conservative until the next form transition, so resuming cannot replay an
    opener in the middle of a phase.
    """
    chimera = state.get("chimera", {})
    battle = state.get("battle", {})
    current_form = canonical_chimera_form(
        chimera.get("currentForm") if isinstance(chimera, dict) else None
    )
    active_hero_id = state.get("activeHeroId")
    if (
        not isinstance(current_form, str)
        or not isinstance(active_hero_id, int)
        or isinstance(active_hero_id, bool)
    ):
        state["_chimeraFormHeroFirstTurn"] = False
        return

    chimera_turn = chimera.get("turnCount") if isinstance(chimera, dict) else None
    player_turn = battle.get("playerTurnCount") if isinstance(battle, dict) else None
    hero_turn = state.get("activeHeroTurnCount")
    tracker = runtime_state.get("chimeraFormTurnTracker")

    def at_battle_opening() -> bool:
        return (
            isinstance(player_turn, int)
            and not isinstance(player_turn, bool)
            and player_turn in {0, 1}
            and isinstance(hero_turn, int)
            and not isinstance(hero_turn, bool)
            and hero_turn in {0, 1}
            and (
                not isinstance(chimera_turn, int)
                or isinstance(chimera_turn, bool)
                or chimera_turn in {0, 1}
            )
        )

    reset_battle = False
    if isinstance(tracker, dict):
        previous_chimera_turn = tracker.get("lastChimeraTurn")
        previous_player_turn = tracker.get("lastPlayerTurn")
        reset_battle = (
            isinstance(chimera_turn, int)
            and not isinstance(chimera_turn, bool)
            and isinstance(previous_chimera_turn, int)
            and not isinstance(previous_chimera_turn, bool)
            and chimera_turn < previous_chimera_turn
        ) or (
            isinstance(player_turn, int)
            and not isinstance(player_turn, bool)
            and isinstance(previous_player_turn, int)
            and not isinstance(previous_player_turn, bool)
            and player_turn < previous_player_turn
            and at_battle_opening()
        )

    if not isinstance(tracker, dict) or reset_battle:
        tracker = {
            "form": current_form,
            "armed": at_battle_opening(),
            "seenHeroIds": set(),
            "pendingHeroId": None,
            "pendingWindow": None,
        }
        runtime_state["chimeraFormTurnTracker"] = tracker
    elif tracker.get("form") != current_form:
        tracker.update(
            {
                "form": current_form,
                "armed": True,
                "seenHeroIds": set(),
                "pendingHeroId": None,
                "pendingWindow": None,
            }
        )

    seen = tracker.get("seenHeroIds")
    if not isinstance(seen, set):
        seen = (
            {
                value
                for value in seen
                if isinstance(value, int) and not isinstance(value, bool)
            }
            if isinstance(seen, (list, tuple, set))
            else set()
        )
        tracker["seenHeroIds"] = seen

    current_window = hero_action_window_key(state)
    pending_hero_id = tracker.get("pendingHeroId")
    pending_window = tracker.get("pendingWindow")
    if (
        isinstance(pending_hero_id, int)
        and not isinstance(pending_hero_id, bool)
        and pending_window is not None
        and pending_window != current_window
    ):
        seen.add(pending_hero_id)

    state["_chimeraFormHeroFirstTurn"] = bool(
        tracker.get("armed") is True and active_hero_id not in seen
    )
    state["_chimeraFormPhase"] = current_form
    tracker["pendingHeroId"] = active_hero_id
    tracker["pendingWindow"] = current_window
    tracker["lastChimeraTurn"] = chimera_turn
    tracker["lastPlayerTurn"] = player_turn


def wait_for_turn_advance(
    ipc: AgentIpc,
    baseline: dict[str, Any],
    session_id: int,
    timeout_seconds: float = COMMAND_CONFIRM_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    baseline_key = turn_key(baseline)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        lifecycle = ipc.lifecycle()
        if isinstance(lifecycle, dict) and lifecycle.get("screen") == "result":
            return None
        current = ipc.decision()
        if current and turn_key(current) != baseline_key:
            return current
        time.sleep(0.05)
    return None


def start_first_battle_if_ready(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    command_nonce: int = LIFECYCLE_START_NONCE,
    desired_hero_ids: list[int] | None = None,
    desired_hero_type_ids: list[int] | None = None,
    boss_mode: str | None = None,
    announce_wait: bool = False,
) -> bool:
    lifecycle = ipc.lifecycle()
    if not lifecycle or lifecycle.get("screen") != "team_selection":
        return False
    selection = lifecycle.get("selection")
    if not isinstance(selection, dict):
        raise RuntimeError("已看到 Boss 队伍界面，但尚未取得队伍状态")
    selected_mode = selection.get("bossMode")
    mode = normalize_mode(boss_mode or selected_mode or ACTIVE_BOSS_MODE)
    label = "六头蛇" if mode == "hydra" else "奇美拉"
    team_size = 6 if mode == "hydra" else 5
    if selected_mode in {"chimera", "hydra"} and selected_mode != mode:
        raise RuntimeError(
            f"当前是{'六头蛇' if selected_mode == 'hydra' else '奇美拉'}队伍界面，"
            f"与准备接管的{label}模式不一致"
        )
    selected_count = len(
        {
            item
            for item in selection.get("heroIds", [])
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        }
    )
    if selection.get("filled") is not True and announce_wait:
        print(
            f"当前{label}队伍已选择 {selected_count}/{team_size} 名英雄；"
            "将按当前队伍自动开始战斗。",
            flush=True,
        )

    def complete_ids(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        result = [
            item
            for item in value
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        ]
        return result if len(result) == team_size and len(set(result)) == team_size else []

    current_hero_ids = complete_ids(selection.get("heroIds"))
    current_hero_type_ids = complete_ids(selection.get("heroTypeIds"))
    expected_hero_ids = complete_ids(desired_hero_ids)
    expected_hero_type_ids = complete_ids(desired_hero_type_ids)
    selection_is_full = selection.get("filled") is True
    if selection_is_full and expected_hero_ids and current_hero_ids != expected_hero_ids:
        raise RuntimeError(f"当前{team_size}人队伍与策略组保存队伍不一致（具体英雄副本）")
    if selection_is_full and expected_hero_type_ids and current_hero_type_ids != expected_hero_type_ids:
        raise RuntimeError(f"当前{team_size}人队伍与策略组保存队伍不一致（英雄身份或顺序）")
    context = selection.get("context")
    if not isinstance(context, int) or context <= 0:
        raise RuntimeError("首场自动开始已取消：队伍界面实例已经变化")
    acknowledgement: dict[str, Any] | None = None
    start_deadline = time.monotonic() + 6.0
    start_attempt = 0
    retryable_reasons = {
        "auto_battle_disable_failed",
        "auto_battle_disable_pending",
        "team_selection_guard_failed",
    }
    while time.monotonic() < start_deadline:
        attempt_nonce = (
            command_nonce
            if start_attempt == 0
            else lifecycle_nonce(command_nonce, start_attempt)
        )
        result = queue_lifecycle_command(
            pid,
            agent,
            session_id=session_id,
            context=context,
            action=LIFECYCLE_START_BATTLE,
            nonce=attempt_nonce,
        )
        if not result.get("queued"):
            raise RuntimeError(f"代理拒绝首场自动开始请求：{result}")
        acknowledgement = wait_for_command_ack(
            ipc,
            session_id=session_id,
            nonce=attempt_nonce,
            timeout_seconds=1.5,
        )
        if acknowledgement and acknowledgement.get("status") == "submitted":
            break
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        current = ipc.lifecycle() or {}
        current_selection = current.get("selection", {})
        same_selection = (
            current.get("screen") == "team_selection"
            and isinstance(current_selection, dict)
            and current_selection.get("context") == context
        )
        if (
            reason in retryable_reasons
            and same_selection
            and current_selection.get("quickBattle") is not True
        ):
            start_attempt += 1
            if start_attempt == 1:
                print(
                    f"正在等待{label}准备界面完成队伍读取并关闭自动战斗……",
                    flush=True,
                )
            time.sleep(0.12)
            continue
        break
    if not acknowledgement or acknowledgement.get("status") != "submitted":
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        current = ipc.lifecycle() or {}
        current_selection = current.get("selection", {})
        if isinstance(current_selection, dict):
            if current_selection.get("autoBattle") is True:
                reason = "自动战斗仍处于开启状态"
            elif current_selection.get("quickBattle") is True:
                reason = "快速战斗仍处于开启状态"
            elif current_selection.get("filled") is not True:
                reason = "未满队伍自动开战请求被游戏拒绝"
            elif current_selection.get("valid") is not True:
                reason = "准备队伍仍在解析刚刚切换的英雄"
        diagnostic = ipc.diagnostic()
        detail = f"；代理诊断：{diagnostic}" if diagnostic else ""
        raise RuntimeError(f"首场自动开始未通过安全检查：{reason}{detail}")
    print(f"已用当前选定的 {selected_count} 名英雄开始首场{label}战斗。", flush=True)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        current = ipc.lifecycle()
        if current and current.get("screen") == "battle":
            return True
        decision = ipc.decision()
        if is_battle_decision_state(decision):
            detected_mode = decision.get("bossMode")
            if detected_mode == mode:
                return True
        time.sleep(0.05)
    raise RuntimeError(f"开始请求已提交，但没有确认进入{label}战斗")


def submit_free_regroup(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int = LIFECYCLE_FREE_REGROUP_NONCE,
) -> dict[str, Any]:
    lifecycle = ipc.lifecycle()
    battle = lifecycle.get("battle", {}) if isinstance(lifecycle, dict) else {}
    context = battle.get("context") if isinstance(battle, dict) else None
    if (
        not lifecycle
        or lifecycle.get("screen") != "battle"
        or not isinstance(context, int)
        or context <= 0
    ):
        raise RuntimeError("必要试炼已不可能完成，但当前战斗实例已变化，未执行免费重整")
    preparation_nonce = lifecycle_nonce(nonce, 1)
    preparation = queue_lifecycle_command(
        pid,
        agent,
        session_id=session_id,
        context=context,
        action=LIFECYCLE_PREPARE_FREE_REGROUP,
        nonce=preparation_nonce,
    )
    if not preparation.get("queued"):
        raise RuntimeError(f"代理拒绝准备免费重整：{preparation}")
    preparation_ack = wait_for_command_ack(
        ipc,
        session_id=session_id,
        nonce=preparation_nonce,
        timeout_seconds=5.0,
    )
    if not preparation_ack or preparation_ack.get("status") != "submitted":
        reason = preparation_ack.get("reason") if preparation_ack else "回执超时"
        raise RuntimeError(f"免费重整准备失败：{reason}")

    deadline = time.monotonic() + 15.0
    attempt = 0
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        current_lifecycle = ipc.lifecycle() or {}
        if current_lifecycle.get("screen") == "team_selection":
            return preparation_ack
        attempt += 1
        execute_nonce = lifecycle_nonce(nonce, 1 + attempt)
        result = queue_lifecycle_command(
            pid,
            agent,
            session_id=session_id,
            context=context,
            action=LIFECYCLE_FREE_REGROUP,
            nonce=execute_nonce,
        )
        if not result.get("queued"):
            raise RuntimeError(f"代理拒绝免费重整请求：{result}")
        acknowledgement = wait_for_command_ack(
            ipc,
            session_id=session_id,
            nonce=execute_nonce,
            timeout_seconds=2.0,
        )
        if acknowledgement and acknowledgement.get("status") == "submitted":
            return acknowledgement
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        # Some server responses finish the regroup between queueing action 2
        # and its main-thread guard. In that race the old battle context is
        # correctly rejected, but arrival at a verified team screen is the
        # successful terminal state of the regroup.
        latest_lifecycle = ipc.lifecycle() or {}
        if (
            reason == "battle_context_changed"
            and latest_lifecycle.get("screen") == "team_selection"
        ):
            return preparation_ack
        if reason not in {
            "free_regroup_not_prepared",
            "free_regroup_guard_failed",
        }:
            raise RuntimeError(f"免费重整没有通过安全检查：{reason}")
        time.sleep(0.1)
    raise RuntimeError("奇美拉退出验证在 15 秒内没有完成，未执行免费重整")


def refresh_team_selection(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int,
) -> dict[str, Any]:
    lifecycle = ipc.lifecycle() or {}
    selection = lifecycle.get("selection") or {}
    context = selection.get("context") if isinstance(selection, dict) else None
    if lifecycle.get("screen") != "team_selection" or not isinstance(context, int):
        raise RuntimeError("当前不是可刷新的奇美拉队伍界面")
    refresh_nonce = lifecycle_nonce(nonce, 300)
    queued = queue_lifecycle_command(
        pid,
        agent,
        session_id=session_id,
        context=context,
        action=LIFECYCLE_REFRESH_TEAM_SELECTION,
        nonce=refresh_nonce,
    )
    if not queued.get("queued"):
        raise RuntimeError(f"代理拒绝刷新队伍状态：{queued}")
    acknowledgement = wait_for_command_ack(
        ipc,
        session_id=session_id,
        nonce=refresh_nonce,
        timeout_seconds=5.0,
    )
    if not acknowledgement or acknowledgement.get("status") != "validated":
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        raise RuntimeError(f"队伍状态刷新失败：{reason}")
    refreshed = ipc.lifecycle() or {}
    if refreshed.get("screen") != "team_selection":
        raise RuntimeError("刷新后队伍界面实例已经变化")
    return refreshed


def select_team_heroes(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    hero_ids: list[int],
    nonce: int,
) -> dict[str, Any]:
    lifecycle = ipc.lifecycle() or {}
    selection = lifecycle.get("selection") or {}
    context = selection.get("context") if isinstance(selection, dict) else None
    if lifecycle.get("screen") != "team_selection" or not isinstance(context, int):
        raise RuntimeError("自动选人时已不在奇美拉队伍界面")
    select_nonce = lifecycle_nonce(nonce, 350)
    queued = queue_lifecycle_command(
        pid,
        agent,
        session_id=session_id,
        context=context,
        action=LIFECYCLE_SELECT_HEROES,
        nonce=select_nonce,
        hero_ids=hero_ids,
    )
    if not queued.get("queued"):
        raise RuntimeError(f"代理拒绝自动选择队伍：{queued}")
    acknowledgement = wait_for_command_ack(
        ipc,
        session_id=session_id,
        nonce=select_nonce,
        timeout_seconds=5.0,
    )
    if not acknowledgement or acknowledgement.get("status") not in {
        "submitted",
        "validated",
    }:
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        raise RuntimeError(f"自动选择队伍失败：{reason}")
    refreshed = ipc.lifecycle() or {}
    current = (refreshed.get("selection") or {}).get("heroIds", [])
    if (
        refreshed.get("screen") != "team_selection"
        or current != hero_ids
        or (refreshed.get("selection") or {}).get("filled") is not True
    ):
        raise RuntimeError("自动选人后的五人队伍复核失败")
    return refreshed


def free_regroup_and_retry_manual(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int,
    desired_hero_ids: list[int] | None = None,
    desired_hero_type_ids: list[int] | None = None,
) -> None:
    before = ipc.lifecycle() or {}
    before_battle = before.get("battle") or {}
    old_context = before_battle.get("context")
    original_hero_ids = [
        value
        for value in before_battle.get("heroIds", [])
        if isinstance(value, int) and value > 0
    ]
    original_hero_type_ids = [
        value
        for value in before_battle.get("heroTypeIds", [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    configured_hero_ids = [
        value
        for value in (desired_hero_ids or [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    configured_hero_type_ids = [
        value
        for value in (desired_hero_type_ids or [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    if len(original_hero_ids) == 5:
        expected_hero_ids = original_hero_ids
        if configured_hero_ids and configured_hero_ids != expected_hero_ids:
            raise RuntimeError(
                "The live battle team does not match the configured five-hero team"
            )
    elif len(configured_hero_ids) == 5 and len(set(configured_hero_ids)) == 5:
        expected_hero_ids = configured_hero_ids
    else:
        raise RuntimeError(
            "No exact five-hero team is available for safe regroup verification"
        )
    expected_hero_type_ids = (
        original_hero_type_ids
        if len(original_hero_type_ids) == 5
        else configured_hero_type_ids
    )
    if (
        len(configured_hero_type_ids) == 5
        and len(original_hero_type_ids) == 5
        and configured_hero_type_ids != original_hero_type_ids
    ):
        raise RuntimeError("实战英雄身份与策略组保存的五人队伍不一致")
    if not isinstance(old_context, int) or old_context <= 0:
        raise RuntimeError("重整前没有可验证的战斗实例")
    submit_free_regroup(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        nonce=nonce,
    )
    regrouped_selection = wait_for_lifecycle_screen(
        ipc, session_id, "team_selection", timeout_seconds=30.0
    )
    refreshed = refresh_team_selection(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        nonce=nonce,
    )
    selection = refreshed.get("selection") or {}
    regrouped_hero_ids = [
        value
        for value in selection.get("heroIds", [])
        if isinstance(value, int) and value > 0
    ]
    regrouped_hero_type_ids = [
        value
        for value in selection.get("heroTypeIds", [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    if (
        not regrouped_hero_ids
        and len(expected_hero_ids) == 5
        and selection.get("valid") is True
        and selection.get("bossMode") != "hydra"
    ):
        refreshed = select_team_heroes(
            ipc,
            pid=pid,
            agent=agent,
            session_id=session_id,
            hero_ids=expected_hero_ids,
            nonce=nonce,
        )
        selection = refreshed.get("selection") or {}
        regrouped_hero_ids = list(selection.get("heroIds", []))
        regrouped_hero_type_ids = list(selection.get("heroTypeIds", []))
    if (
        selection.get("valid") is not True
        or selection.get("filled") is not True
        or len(regrouped_hero_ids) != 5
        or selection.get("bossMode") == "hydra"
        or selection.get("quickBattle") is True
    ):
        raise RuntimeError("免费重整后的队伍未通过奇美拉五人队伍/快速战斗守卫")
    if regrouped_hero_ids != expected_hero_ids:
        raise RuntimeError("免费重整后队伍发生变化，已停止在队伍界面")
    if expected_hero_type_ids and regrouped_hero_type_ids != expected_hero_type_ids:
        raise RuntimeError("免费重整后英雄身份或顺序发生变化，已停止在队伍界面")
    started = start_first_battle_if_ready(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        command_nonce=lifecycle_nonce(nonce, 400),
        desired_hero_ids=expected_hero_ids,
        desired_hero_type_ids=expected_hero_type_ids or None,
    )
    if not started:
        raise RuntimeError("免费重整后没有开始新的奇美拉战斗")
    new_battle = wait_for_lifecycle_screen(
        ipc, session_id, "battle", timeout_seconds=30.0
    )
    new_battle_state = new_battle.get("battle") or {}
    new_context = new_battle_state.get("context")
    if not isinstance(new_context, int) or new_context <= 0:
        raise RuntimeError("重新开战后未取得新战斗实例")
    if new_battle_state.get("bossMode") not in {None, "chimera"}:
        raise RuntimeError("重新开战后进入的不是奇美拉战斗")
    restarted_hero_ids = [
        value
        for value in new_battle_state.get("heroIds", [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    restarted_hero_type_ids = [
        value
        for value in new_battle_state.get("heroTypeIds", [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    if restarted_hero_ids and restarted_hero_ids != expected_hero_ids:
        raise RuntimeError("重新开战后的五人队伍与重整前不一致")
    if (
        expected_hero_type_ids
        and restarted_hero_type_ids
        and restarted_hero_type_ids != expected_hero_type_ids
    ):
        raise RuntimeError("重新开战后的英雄身份或顺序与重整前不一致")

    # ClientBattleViewContext is a reusable UI object.  The game can keep the
    # same address when a free regroup goes battle -> team selection -> battle,
    # so pointer inequality is not a valid new-battle requirement.  The fully
    # observed team-selection transition above is the authoritative boundary.
    # When lifecycle ordering metadata is available, also reject an old battle
    # snapshot that predates that boundary.
    selection_sequence = regrouped_selection.get("sequence")
    battle_sequence = new_battle.get("sequence")
    if (
        isinstance(selection_sequence, int)
        and isinstance(battle_sequence, int)
        and battle_sequence <= selection_sequence
    ):
        raise RuntimeError("重新开战后读取到了重整前的旧战斗状态")
    selection_tick = regrouped_selection.get("observedAtTick")
    battle_tick = new_battle.get("observedAtTick")
    if (
        isinstance(selection_tick, int)
        and isinstance(battle_tick, int)
        and battle_tick < selection_tick
    ):
        raise RuntimeError("重新开战后的状态时间早于队伍重整")
    print(
        "必要试炼已不可完成；免费重整后已核对原五人队伍，"
        "关闭自动战斗并以手动模式进入下一次尝试。",
        flush=True,
    )


def free_regroup_and_stop(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
) -> None:
    submit_free_regroup(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
    )
    print(
        "必要试炼已被游戏状态明确判定为不可完成；已触发免费重整并停止，"
        "不会自动开始下一轮。",
        flush=True,
    )


def _restart_from_result(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int,
    boss_mode: str,
    action: int,
    desired_hero_ids: list[int] | None,
    desired_hero_type_ids: list[int] | None,
) -> bool:
    mode = normalize_mode(boss_mode)
    label = mode_spec(mode)["label"]
    team_size = mode_spec(mode)["teamSize"]
    lifecycle = ipc.lifecycle() or {}
    result_state = lifecycle.get("result") or {}
    context = result_state.get("context") if isinstance(result_state, dict) else None
    if (
        lifecycle.get("screen") != "result"
        or result_state.get("bossMode") != mode
        or not isinstance(context, int)
        or context <= 0
    ):
        raise RuntimeError(f"{label}结算实例已经变化，未执行自动重整")

    restart_nonce = lifecycle_nonce(nonce, 400)
    queued = queue_lifecycle_command(
        pid,
        agent,
        session_id=session_id,
        context=context,
        action=action,
        nonce=restart_nonce,
    )
    if not queued.get("queued"):
        raise RuntimeError(f"代理拒绝{label}自动重整请求：{queued}")
    acknowledgement = wait_for_command_ack(
        ipc,
        session_id=session_id,
        nonce=restart_nonce,
        timeout_seconds=5.0,
    )
    if not acknowledgement or acknowledgement.get("status") != "submitted":
        reason = acknowledgement.get("reason") if acknowledgement else "回执超时"
        raise RuntimeError(f"{label}自动重整没有通过安全检查：{reason}")

    deadline = time.monotonic() + 30.0
    stable_selection_key: tuple[Any, ...] | None = None
    stable_since = 0.0
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        current = ipc.lifecycle() or {}
        screen = current.get("screen")
        if screen == "team_selection":
            selection = current.get("selection") or {}
            if not isinstance(selection, dict):
                time.sleep(0.05)
                continue
            selection_key = (
                selection.get("context"),
                selection.get("areaTypeId"),
                selection.get("stageId"),
                selection.get("valid"),
                tuple(selection.get("heroIds") or []),
                tuple(selection.get("heroTypeIds") or []),
            )
            if selection.get("valid") is not True:
                stable_selection_key = None
                stable_since = 0.0
                time.sleep(0.05)
                continue
            now = time.monotonic()
            if selection_key != stable_selection_key:
                stable_selection_key = selection_key
                stable_since = now
                time.sleep(0.05)
                continue
            # The result button performs an asynchronous server-side regroup.
            # Wait for the returned selection and its current (even partial)
            # roster to remain unchanged before pressing Start.
            if now - stable_since < 2.0:
                time.sleep(0.05)
                continue
            started = start_first_battle_if_ready(
                ipc,
                pid=pid,
                agent=agent,
                session_id=session_id,
                command_nonce=lifecycle_nonce(nonce, 401),
                desired_hero_ids=desired_hero_ids,
                desired_hero_type_ids=desired_hero_type_ids,
                boss_mode=mode,
            )
            if started:
                return True
        elif screen == "battle":
            battle = current.get("battle") or {}
            if not isinstance(battle, dict) or battle.get("bossMode") != mode:
                raise RuntimeError(f"自动重整后进入的不是{label}战斗，已停止接管")
            actual_instances = battle.get("heroIds")
            actual_types = battle.get("heroTypeIds")
            actual_team_is_full = (
                isinstance(actual_types, list)
                and len(actual_types) == team_size
                and all(
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value > 0
                    for value in actual_types
                )
                and len(set(actual_types)) == team_size
            )
            if actual_team_is_full and desired_hero_ids and actual_instances != desired_hero_ids:
                raise RuntimeError(f"自动重整后的{team_size}人队伍与策略组不一致（具体英雄副本）")
            if actual_team_is_full and desired_hero_type_ids and actual_types != desired_hero_type_ids:
                raise RuntimeError(f"自动重整后的{team_size}人队伍与策略组不一致（英雄身份或顺序）")
            return True
        time.sleep(0.05)
    current = ipc.lifecycle() or {}
    if current.get("screen") == "team_selection":
        return False
    raise RuntimeError(f"{label}自动重整后 30 秒内没有进入准备界面或新战斗")


def restart_hydra_from_result(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int,
    desired_hero_ids: list[int] | None,
    desired_hero_type_ids: list[int] | None,
) -> bool:
    return _restart_from_result(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        nonce=nonce,
        boss_mode="hydra",
        action=LIFECYCLE_RESTART_HYDRA_RESULT,
        desired_hero_ids=desired_hero_ids,
        desired_hero_type_ids=desired_hero_type_ids,
    )


def restart_chimera_from_result(
    ipc: AgentIpc,
    *,
    pid: int,
    agent: Path,
    session_id: int,
    nonce: int,
    desired_hero_ids: list[int] | None,
    desired_hero_type_ids: list[int] | None,
) -> bool:
    return _restart_from_result(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        nonce=nonce,
        boss_mode="chimera",
        action=LIFECYCLE_RESTART_CHIMERA_RESULT,
        desired_hero_ids=desired_hero_ids,
        desired_hero_type_ids=desired_hero_type_ids,
    )


def result_screen_reached(
    ipc: AgentIpc,
    *,
    config: dict[str, Any] | None = None,
    pid: int | None = None,
    agent: Path | None = None,
    session_id: int = 0,
    boss_mode: str | None = None,
    execute_requested: bool = False,
    runtime_state: dict[str, Any] | None = None,
    desired_hero_ids: list[int] | None = None,
    desired_hero_type_ids: list[int] | None = None,
    nonce: int = LIFECYCLE_FREE_REGROUP_NONCE,
) -> bool:
    lifecycle = ipc.lifecycle()
    if not lifecycle or lifecycle.get("screen") != "result":
        return False
    ledger = ipc.battle_ledger() or {}
    detected_mode = normalize_mode(
        boss_mode or ledger.get("bossMode") or ACTIVE_BOSS_MODE
    )
    raw_damage = ledger.get("damage")
    damage = (
        float(raw_damage)
        if isinstance(raw_damage, (int, float))
        and not isinstance(raw_damage, bool)
        and math.isfinite(float(raw_damage))
        and raw_damage >= 0
        else None
    )

    if config is None:
        print(
            "战斗已结束，已停留在结算画面；"
            f"伤害 {raw_damage if damage is not None else '未知'}，"
            f"完成试炼 {ledger.get('completedChallengeCount', '未知')}。"
            "工具不会保存结果或开始下一轮。",
            flush=True,
        )
        return True

    objectives = config.get("objectives") or {}
    minimum_damage = float(objectives.get("minimumDamage", 0))
    runtime = runtime_state if runtime_state is not None else {}

    if detected_mode == "chimera":
        last_report = runtime.get("lastObjectiveReport")
        if not isinstance(last_report, dict):
            last_report = {}
        mandatory_ids = configured_trial_ids(
            last_report.get(
                "mandatoryTrialIds",
                objectives.get(
                    "mandatoryTrials",
                    objectives.get("mandatoryTrialIds", []),
                ),
            )
        )
        completed_ids = set(
            configured_trial_ids(last_report.get("completedTrialIds", []))
        )
        completed_ids.update(
            configured_trial_ids(ledger.get("completedChallengeIds", []))
        )
        missing_ids = tuple(
            trial_id for trial_id in mandatory_ids if trial_id not in completed_ids
        )
        damage_met = minimum_damage <= 0 or (
            damage is not None and damage >= minimum_damage
        )
        trials_met = not missing_ids
        if damage_met and trials_met:
            print(
                f"奇美拉全部目标已达成：伤害 {damage:g}/{minimum_damage:g}，"
                f"必要试炼 {len(mandatory_ids)}/{len(mandatory_ids)}；"
                "已停留在结算画面并暂停接管，不会自动保存结果。",
                flush=True,
            )
            return True

        behavior = objectives.get(
            "onObjectivesUnmetAtResult",
            objectives.get(
                "onMandatoryTrialImpossible",
                "free_regroup_and_retry_manual",
            ),
        )
        execute = execute_requested and config.get("mode") == "execute"
        damage_label = (
            f"{damage:g}/{minimum_damage:g}"
            if damage is not None
            else f"未知/{minimum_damage:g}"
        )
        missing_label = (
            ", ".join(map(str, missing_ids)) if missing_ids else "无"
        )
        if behavior != "free_regroup_and_retry_manual" or not execute:
            print(
                "奇美拉结算目标未全部达成："
                f"伤害 {damage_label}，未完成试炼 {missing_label}；"
                "当前设置不执行自动重整，已停留在结算画面。",
                flush=True,
            )
            return True

        used = int(runtime.get("regroupRetries", 0))
        maximum = int(objectives.get("maxRegroupRetries", 10))
        if maximum > 0 and used >= maximum:
            print(
                "奇美拉结算目标未全部达成，"
                f"且已达到自动重整上限 {maximum}；已停留在结算画面。",
                flush=True,
            )
            return True
        if pid is None or agent is None or session_id <= 0:
            raise RuntimeError("奇美拉自动重整缺少已验证的控制会话")

        print(
            "奇美拉结算目标未全部达成："
            f"伤害 {damage_label}，未完成试炼 {missing_label}；"
            f"正在执行第 {used + 1} 次免费重整并重新开战。",
            flush=True,
        )
        restarted = restart_chimera_from_result(
            ipc,
            pid=pid,
            agent=agent,
            session_id=session_id,
            nonce=nonce,
            desired_hero_ids=desired_hero_ids,
            desired_hero_type_ids=desired_hero_type_ids,
        )
        runtime["regroupRetries"] = used + 1
        runtime.pop("lastObjectiveReport", None)
        if restarted:
            print("奇美拉已用当前队伍重新进入手动战斗。", flush=True)
        else:
            print(
                "奇美拉已进入准备界面；正在等待当前队伍状态稳定，"
                "接管保持运行。",
                flush=True,
            )
        return False

    if damage is None:
        print(
            "六头蛇战斗已结束，但结算伤害仍不可用；为避免错误重试，"
            "已停留在结算画面并停止接管。",
            flush=True,
        )
        return True
    if damage >= minimum_damage:
        print(
            f"六头蛇伤害目标已达成：{damage:g}/{minimum_damage:g}；"
            "已停留在结算画面并暂停接管，不会自动保存结果。",
            flush=True,
        )
        return True

    behavior = objectives.get(
        "onTeamDefeatedBeforeMinimumDamage",
        "free_regroup_and_retry_manual",
    )
    execute = execute_requested and config.get("mode") == "execute"
    if behavior != "free_regroup_and_retry_manual" or not execute:
        print(
            f"六头蛇战斗结束时伤害未达目标：{damage:g}/{minimum_damage:g}；"
            "当前设置不执行自动重整，已停留在结算画面。",
            flush=True,
        )
        return True

    used = int(runtime.get("regroupRetries", 0))
    maximum = int(objectives.get("maxRegroupRetries", 10))
    if maximum > 0 and used >= maximum:
        print(
            f"六头蛇伤害未达目标（{damage:g}/{minimum_damage:g}），"
            f"且已达到自动重整上限 {maximum}；已停留在结算画面。",
            flush=True,
        )
        return True
    if pid is None or agent is None or session_id <= 0:
        raise RuntimeError("六头蛇自动重整缺少已验证的控制会话")

    print(
        f"六头蛇伤害未达目标：{damage:g}/{minimum_damage:g}；"
        f"正在执行第 {used + 1} 次免费重整并重新开战。",
        flush=True,
    )
    restarted = restart_hydra_from_result(
        ipc,
        pid=pid,
        agent=agent,
        session_id=session_id,
        nonce=nonce,
        desired_hero_ids=desired_hero_ids,
        desired_hero_type_ids=desired_hero_type_ids,
    )
    runtime["regroupRetries"] = used + 1
    if restarted:
        print("六头蛇已用当前队伍重新进入手动战斗。", flush=True)
    else:
        print(
            "六头蛇已进入准备界面；正在等待当前队伍状态稳定，"
            "接管保持运行。",
            flush=True,
        )
    return False


def effect_identity_selector(selector: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in selector.items()
        if key not in {"turnsAtLeast", "turnsAtMost", "producerId"}
    }


def requirement_effect_selectors(
    requirement: dict[str, Any]
) -> list[dict[str, Any]]:
    effect = requirement.get("effect")
    if isinstance(effect, dict) and effect and any(
        key in effect for key in ("kind", "effectTypeId")
    ):
        return [effect]
    any_of = requirement.get("anyOf")
    if not isinstance(any_of, list):
        return []
    return [
        selector
        for selector in any_of
        if isinstance(selector, dict)
        and selector
        and any(key in selector for key in ("kind", "effectTypeId"))
    ]


def effect_requirement_entities(
    requirement: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    scope = requirement.get("scope")
    if scope == "boss":
        boss = current_boss(state)
        return [boss] if boss is not None else []
    heroes = [
        hero for hero in state_entities(state, "heroes")
        if hero.get("dead") is not True
    ]
    if scope == "activeHero":
        active_hero_id = state.get("activeHeroId")
        return [hero for hero in heroes if hero.get("id") == active_hero_id]
    if scope == "anyAlly":
        return heroes
    return []


def effect_requirement_satisfied(
    requirement: dict[str, Any], state: dict[str, Any]
) -> bool:
    selectors = requirement_effect_selectors(requirement)
    if not selectors:
        return False
    entities = effect_requirement_entities(requirement, state)
    for selector in selectors:
        wanted = dict(selector)
        wanted["turnsAtLeast"] = max(
            int(requirement.get("keepTurnsAtLeast", 1)),
            int(wanted.get("turnsAtLeast", 0)),
        )
        if any(entity_has_effect(entity, wanted) for entity in entities):
            return True
    return False


def capability_matches_requirement(
    capability: dict[str, Any], requirement: dict[str, Any]
) -> bool:
    scope = requirement.get("scope")
    target_scope = capability.get("targetScope")
    if scope == "boss" and target_scope != "boss":
        return False
    if scope in {"activeHero", "anyAlly"} and target_scope not in {"ally", "self"}:
        return False
    return any(
        effect_matches(capability, effect_identity_selector(selector))
        for selector in requirement_effect_selectors(requirement)
    )


def effect_requirement_target(
    requirement: dict[str, Any],
    skill: dict[str, Any],
    state: dict[str, Any],
    *,
    capability: dict[str, Any] | None = None,
) -> tuple[int, str] | None:
    valid = {
        target_id
        for target_id in skill.get("validTargetIds", [])
        if isinstance(target_id, int) and not isinstance(target_id, bool)
    }
    if not valid:
        return None
    scope = requirement.get("scope")
    if scope == "boss":
        return select_target({"type": "boss"}, skill, state)
    active_hero_id = state.get("activeHeroId")
    if capability is not None and capability.get("targetScope") == "self":
        return select_target({"type": "self"}, skill, state)
    if scope == "activeHero":
        return select_target({"type": "self"}, skill, state)
    heroes = [
        hero
        for hero in state_entities(state, "heroes")
        if hero.get("dead") is not True and hero.get("id") in valid
    ]
    if not heroes:
        return None
    # Prefer self for a friendly skill because a self buff is deterministic and
    # follows the same actor into its later damaging turn. Otherwise use the
    # lowest-health legal ally.
    self_entity = next(
        (hero for hero in heroes if hero.get("id") == active_hero_id), None
    )
    chosen = self_entity or min(
        heroes,
        key=lambda hero: (
            float(hero.get("healthPct", 101))
            if isinstance(hero.get("healthPct"), (int, float))
            else 101.0
        ),
    )
    return int(chosen["id"]), str(chosen.get("name", chosen["id"]))


def effect_target_has_capacity(
    requirement: dict[str, Any],
    target_id: int,
    state: dict[str, Any],
    *,
    capability: dict[str, Any] | None = None,
) -> bool:
    entities = state_entities(state, "heroes") + state_entities(state, "bosses")
    target = next((entity for entity in entities if entity.get("id") == target_id), None)
    if target is None:
        return False
    for selector in requirement_effect_selectors(requirement):
        if entity_has_effect(target, effect_identity_selector(selector)):
            return True
    polarity = effect_polarity(capability)
    if polarity is None:
        polarities = {
            value
            for selector in requirement_effect_selectors(requirement)
            if (value := effect_polarity(selector)) is not None
        }
        if len(polarities) == 1:
            polarity = next(iter(polarities))
    if polarity == "buff":
        return buff_count(target) < 10
    if polarity == "debuff":
        return debuff_count(target) < 10
    return effect_count(target) < 10


def maintain_effects_decision(
    name: str,
    action: dict[str, Any],
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory,
) -> Decision | None:
    requirements = [
        requirement
        for requirement in action.get("requirements", [])
        if isinstance(requirement, dict)
        and not effect_requirement_satisfied(requirement, state)
    ]
    if not requirements:
        return None
    ready_skills = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("ready") is True
        and skill.get("blocked") is not True
        and skill.get("passive") is not True
        and isinstance(skill.get("typeId"), int)
    ]

    # Known providers always take priority over exploration.
    for requirement in requirements:
        for skill in ready_skills:
            skill_type_id = int(skill["typeId"])
            for capability in capability_memory.capabilities_for(skill_type_id):
                if not capability_matches_requirement(capability, requirement):
                    continue
                target = effect_requirement_target(
                    requirement,
                    skill,
                    state,
                    capability=capability,
                )
                if target is None or not effect_target_has_capacity(
                    requirement, target[0], state, capability=capability
                ):
                    continue
                return Decision(
                    rule=f"{name}：维持必要效果",
                    skill=skill,
                    target_id=target[0],
                    target_label=target[1],
                )

    if action.get("probeUnknownSkills") is not True:
        return None
    transform = select_transform_skill(state)
    for requirement in requirements:
        candidates = sorted(
            ready_skills,
            key=lambda skill: int(skill.get("slot", 0)),
            reverse=True,
        )
        for skill in candidates:
            skill_type_id = int(skill["typeId"])
            if (
                capability_memory.was_probed(skill_type_id)
                or capability_memory.capabilities_for(skill_type_id)
            ):
                continue
            is_transform = bool(
                transform is not None
                and transform.get("typeId") == skill_type_id
                and transform.get("slot") == skill.get("slot")
            )
            if is_transform and action.get("probeTransforms") is not True:
                continue
            # Basic attacks rarely teach a missing support effect and are kept
            # as the ordinary fallback instead of spending the probe budget.
            if skill.get("slot") == 1:
                continue
            target = effect_requirement_target(requirement, skill, state)
            if target is None or not effect_target_has_capacity(
                requirement, target[0], state
            ):
                continue
            return Decision(
                rule=f"{name}：学习未知技能效果",
                skill=skill,
                target_id=target[0],
                target_label=target[1],
                capability_probe=True,
            )
    return None


_TRIAL_RECIPE_CACHE: dict[int, dict[str, Any]] | None = None


def load_trial_recipes(
    path: Path = DEFAULT_TRIAL_RECIPES,
) -> dict[int, dict[str, Any]]:
    global _TRIAL_RECIPE_CACHE
    if path == DEFAULT_TRIAL_RECIPES and _TRIAL_RECIPE_CACHE is not None:
        return _TRIAL_RECIPE_CACHE
    payload = load_json(path)
    raw_recipes = payload.get("recipes")
    if not isinstance(raw_recipes, list):
        raise ValueError("trial recipe file needs a recipes array")
    recipes: dict[int, dict[str, Any]] = {}
    for index, recipe in enumerate(raw_recipes):
        if not isinstance(recipe, dict):
            raise ValueError(f"trial recipe {index} must be an object")
        trial_id = recipe.get("trialId")
        if (
            not isinstance(trial_id, int)
            or isinstance(trial_id, bool)
            or trial_id <= 0
            or trial_id in recipes
        ):
            raise ValueError(f"trial recipe {index} has an invalid or duplicate trialId")
        if recipe.get("automation") not in {"automatic", "assisted", "manual"}:
            raise ValueError(f"trial recipe {trial_id} has invalid automation")
        if recipe.get("actionGoal") not in {
            "damageBoss",
            "applyDistinctEffects",
            "periodicDamage",
            "survive",
            "absorbDamage",
            "reflectDamage",
            "resistDebuffs",
            "manual",
        }:
            raise ValueError(f"trial recipe {trial_id} has invalid actionGoal")
        requirements = recipe.get("requirements", [])
        if not isinstance(requirements, list):
            raise ValueError(f"trial recipe {trial_id} requirements must be an array")
        for requirement in requirements:
            if (
                not isinstance(requirement, dict)
                or requirement.get("scope") not in {"boss", "activeHero", "anyAlly"}
                or not requirement_effect_selectors(requirement)
            ):
                raise ValueError(f"trial recipe {trial_id} has an invalid requirement")
        difficulty_overrides = recipe.get("difficultyOverrides", {})
        if not isinstance(difficulty_overrides, dict):
            raise ValueError(
                f"trial recipe {trial_id} difficultyOverrides must be an object"
            )
        for difficulty_id, override in difficulty_overrides.items():
            if (
                difficulty_id not in {"1", "2", "3", "4", "5", "6"}
                or not isinstance(override, dict)
            ):
                raise ValueError(
                    f"trial recipe {trial_id} has an invalid difficulty override"
                )
            for field, value in override.items():
                if field not in {
                    "minimumBossDebuffs",
                    "minimumActiveHeroBuffs",
                    "maximumBossBuffs",
                    "minimumLivingAllies",
                } or (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise ValueError(
                        f"trial recipe {trial_id} has invalid {field} for "
                        f"difficulty {difficulty_id}"
                    )
        recipes[trial_id] = dict(recipe)
    if path == DEFAULT_TRIAL_RECIPES:
        _TRIAL_RECIPE_CACHE = recipes
    return recipes


def canonical_trial_recipe_id(trial_id: int) -> int:
    """Locate the shared recipe skeleton for a trial ordinal."""
    offset = trial_id - 8_000_000
    difficulty_id, ordinal = divmod(offset, 100)
    if 1 <= difficulty_id <= 6 and 1 <= ordinal <= 27:
        return (
            8_000_000
            + CANONICAL_TRIAL_RECIPE_DIFFICULTY_ID * 100
            + ordinal
        )
    return trial_id


def trial_recipe_for_id(
    recipes: dict[int, dict[str, Any]], trial_id: int
) -> dict[str, Any] | None:
    recipe = recipes.get(canonical_trial_recipe_id(trial_id))
    if recipe is None:
        return None
    effective = dict(recipe)
    offset = trial_id - 8_000_000
    difficulty_id, ordinal = divmod(offset, 100)
    if 1 <= difficulty_id <= 6 and 1 <= ordinal <= 27:
        overrides = recipe.get("difficultyOverrides", {})
        override = (
            overrides.get(str(difficulty_id))
            if isinstance(overrides, dict)
            else None
        )
        if isinstance(override, dict):
            effective.update(override)
        effective["trialId"] = trial_id
        effective["allianceDifficultyId"] = difficulty_id
    effective.pop("difficultyOverrides", None)
    return effective


def effect_type_identity(effect: dict[str, Any]) -> tuple[str, str]:
    effect_type_id = effect.get("effectTypeId")
    if isinstance(effect_type_id, int) and not isinstance(effect_type_id, bool):
        return "type", str(effect_type_id)
    effect_kind_id = effect.get("effectKindId")
    if isinstance(effect_kind_id, int) and not isinstance(effect_kind_id, bool):
        return "kind", str(effect_kind_id)
    return "name", str(effect.get("effectKind", ""))


def select_ready_boss_damage(
    name: str,
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory | None = None,
    *,
    trial_id: int | None = None,
    preferred_skill_type_ids: tuple[int, ...] = (),
) -> Decision | None:
    boss = current_boss(state)
    if boss is None or not isinstance(boss.get("id"), int):
        return None
    boss_id = int(boss["id"])
    candidates = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("ready") is True
        and skill.get("blocked") is not True
        and skill.get("passive") is not True
        and boss_id in skill.get("validTargetIds", [])
    ]
    preferred_rank = {
        skill_type_id: len(preferred_skill_type_ids) - index
        for index, skill_type_id in enumerate(preferred_skill_type_ids)
    }

    def support_utility(value: dict[str, Any]) -> int:
        skill_type_id = value.get("typeId")
        if (
            capability_memory is None
            or not isinstance(skill_type_id, int)
            or isinstance(skill_type_id, bool)
        ):
            return 0
        seen: set[tuple[Any, ...]] = set()
        score = 0
        for capability in capability_memory.capabilities_for(skill_type_id):
            identity = (
                capability.get("targetScope"),
                capability.get("effectTypeId"),
                capability.get("effectKindId"),
                capability.get("effectKind"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            scope = capability.get("targetScope")
            # Team-wide buffs and boss debuffs usually contribute more than
            # their caster's direct hit, especially for support champions.
            score += 2 if scope in {"ally", "boss"} else 1
        return score

    def priority(value: dict[str, Any]) -> tuple[int, int, int, int, float, int]:
        skill_type_id = value.get("typeId")
        score = (
            capability_memory.damage_score(skill_type_id, trial_id)
            if capability_memory is not None
            and isinstance(skill_type_id, int)
            and not isinstance(skill_type_id, bool)
            else None
        )
        configured_rank = (
            preferred_rank.get(skill_type_id, 0)
            if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool)
            else 0
        )
        # A form-specific default policy is the user's strongest combat signal.
        # Without one, known team/boss utility outranks direct-hit history;
        # damage remains the tiebreaker and unknown skills still get bounded
        # exploration instead of being permanently ignored.
        return (
            int(configured_rank > 0),
            configured_rank,
            support_utility(value),
            1 if score is None else 0,
            float(score or 0),
            int(value.get("slot", 0)),
        )

    skill = max(candidates, key=priority, default=None)
    if skill is None:
        return None
    skill_type_id = skill.get("typeId")
    selection_reason = (
        "沿用当前形态技能优先级"
        if isinstance(skill_type_id, int) and preferred_rank.get(skill_type_id, 0) > 0
        else "综合辅助价值与历史伤害"
    )
    return Decision(
        rule=f"{name}（{selection_reason}）",
        skill=skill,
        target_id=boss_id,
        target_label=str(boss.get("name", "奇美拉")),
        trial_id=trial_id,
    )


def select_basic_boss_preparation(
    name: str, state: dict[str, Any]
) -> Decision | None:
    boss = current_boss(state)
    if boss is None or not isinstance(boss.get("id"), int):
        return None
    boss_id = int(boss["id"])
    skill = next(
        (
            candidate
            for candidate in state.get("skills", [])
            if isinstance(candidate, dict)
            and candidate.get("slot") == 1
            and candidate.get("ready") is True
            and candidate.get("blocked") is not True
            and candidate.get("passive") is not True
            and boss_id in candidate.get("validTargetIds", [])
        ),
        None,
    )
    if skill is None:
        return None
    return Decision(
        rule=name,
        skill=skill,
        target_id=boss_id,
        target_label=str(boss.get("name", "奇美拉")),
    )


PREPARATION_PROTECTIVE_EFFECT_KINDS = {
    "Shield",
    "Negator",
    "StatusIncreaseDefence",
    "ReduceDamageTaken",
    "BlockDebuff",
    "Unkillable",
    "ReviveOnDeath",
}


def adaptive_combat_decision(
    name: str,
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory,
    *,
    excluded_skill_type_ids: set[int] | None = None,
) -> Decision | None:
    """Use a hero's useful ready skills when no trial-specific action applies.

    Explicit default rules remain the primary baseline.  This is the final
    baseline for an automatic-trial policy without a usable default rule, so it
    prefers revival, urgent known protection and ordinary cooldown skills over
    repeatedly falling back to the basic attack.
    """
    excluded = excluded_skill_type_ids or set()
    heroes = state_entities(state, "heroes")
    dead_hero_ids = {
        hero.get("id")
        for hero in heroes
        if hero.get("dead") is True and isinstance(hero.get("id"), int)
    }
    living_health = [
        float(hero["healthPct"])
        for hero in heroes
        if hero.get("dead") is not True
        and isinstance(hero.get("healthPct"), (int, float))
        and not isinstance(hero.get("healthPct"), bool)
    ]
    team_needs_protection = bool(living_health and min(living_health) <= 50.0)
    transform = select_transform_skill(state)
    candidates: list[
        tuple[tuple[int, int, int, int, float, int], dict[str, Any], tuple[int, str], str]
    ] = []
    for skill in state.get("skills", []):
        if (
            not isinstance(skill, dict)
            or skill.get("ready") is not True
            or skill.get("blocked") is True
            or skill.get("passive") is True
        ):
            continue
        skill_type_id = skill.get("typeId")
        if isinstance(skill_type_id, int) and skill_type_id in excluded:
            continue
        target = select_target({"type": "auto"}, skill, state)
        if target is None:
            continue
        capabilities = (
            capability_memory.capabilities_for(skill_type_id)
            if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool)
            else []
        )
        revives_dead = target[0] in dead_hero_ids
        protective = team_needs_protection and any(
            capability.get("targetScope") in {"self", "ally"}
            and capability.get("effectKind") in PREPARATION_PROTECTIVE_EFFECT_KINDS
            for capability in capabilities
        )
        is_transform = transform is skill
        slot = int(skill.get("slot", 0))
        damage_score = (
            capability_memory.damage_score(skill_type_id)
            if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool)
            else None
        )
        rank = (
            int(revives_dead),
            int(protective),
            int(not is_transform),
            int(slot > 1),
            float(damage_score) if damage_score is not None else -1.0,
            slot,
        )
        reason = (
            "优先复活阵亡队友"
            if revives_dead
            else "队伍低生命，优先使用已知防护技能"
            if protective
            else "按常规技能优先级继续战斗"
        )
        candidates.append((rank, skill, target, reason))
    if not candidates:
        return None
    _, skill, target, reason = max(candidates, key=lambda item: item[0])
    return Decision(
        rule=f"{name}：{reason}",
        skill=skill,
        target_id=target[0],
        target_label=target[1],
    )


def select_upcoming_trial_preparation(
    name: str,
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory,
    requirements: list[dict[str, Any]],
) -> Decision | None:
    """Protect the team while preserving cooldown-based trial providers."""
    reserved: set[int] = set()
    ready_skills = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("ready") is True
        and skill.get("blocked") is not True
        and skill.get("passive") is not True
        and isinstance(skill.get("typeId"), int)
    ]
    for skill in ready_skills:
        skill_type_id = int(skill["typeId"])
        if int(skill.get("slot", 0)) <= 1:
            continue
        if any(
            capability_matches_requirement(capability, requirement)
            for requirement in requirements
            for capability in capability_memory.capabilities_for(skill_type_id)
        ):
            reserved.add(skill_type_id)

    for skill in sorted(
        ready_skills, key=lambda value: int(value.get("slot", 0)), reverse=True
    ):
        skill_type_id = int(skill["typeId"])
        if skill_type_id in reserved:
            continue
        capabilities = capability_memory.capabilities_for(skill_type_id)
        # Share-damage can move lethal damage onto the wrong champion, so it is
        # deliberately not treated as a generic preparation defence.
        if any(capability.get("effectKind") == "ShareDamage" for capability in capabilities):
            continue
        for capability in capabilities:
            if (
                capability.get("targetScope") not in {"self", "ally"}
                or capability.get("effectKind") not in PREPARATION_PROTECTIVE_EFFECT_KINDS
            ):
                continue
            requirement = {
                "scope": "activeHero"
                if capability.get("targetScope") == "self"
                else "anyAlly",
                "effect": effect_identity_selector(capability),
                "keepTurnsAtLeast": 0,
            }
            target = effect_requirement_target(
                requirement, skill, state, capability=capability
            )
            if target is None:
                continue
            return Decision(
                rule=f"{name}：先建立保护并保留试炼关键技能",
                skill=skill,
                target_id=target[0],
                target_label=target[1],
            )
    return adaptive_combat_decision(
        f"{name}：保留试炼关键技能",
        state,
        capability_memory,
        excluded_skill_type_ids=reserved,
    )


def new_effect_decision(
    name: str,
    scope: str,
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory,
    *,
    forbidden_effect_type_ids: set[int] | None = None,
    probe_unknown_skills: bool = False,
    probe_transforms: bool = False,
) -> Decision | None:
    forbidden = forbidden_effect_type_ids or set()
    if scope == "boss":
        target_entities = [current_boss(state)]
    else:
        target_entities = effect_requirement_entities(
            {"scope": "activeHero" if scope == "activeHero" else "anyAlly"},
            state,
        )
    target_entities = [entity for entity in target_entities if entity is not None]
    present = {
        effect_type_identity(effect)
        for entity in target_entities
        for effect in entity.get("effects", [])
        if isinstance(effect, dict)
    }
    ready_skills = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("ready") is True
        and skill.get("blocked") is not True
        and skill.get("passive") is not True
        and isinstance(skill.get("typeId"), int)
    ]
    for skill in sorted(
        ready_skills, key=lambda value: int(value.get("slot", 0)), reverse=True
    ):
        skill_type_id = int(skill["typeId"])
        for capability in capability_memory.capabilities_for(skill_type_id):
            target_scope = capability.get("targetScope")
            if scope == "boss" and target_scope != "boss":
                continue
            if scope != "boss" and target_scope not in {"self", "ally"}:
                continue
            effect_type_id = capability.get("effectTypeId")
            if isinstance(effect_type_id, int) and effect_type_id in forbidden:
                continue
            if effect_type_identity(capability) in present:
                continue
            selector: dict[str, Any]
            if isinstance(effect_type_id, int):
                selector = {"effectTypeId": effect_type_id}
            else:
                selector = {"kind": capability.get("effectKind")}
            requirement = {
                "scope": scope,
                "effect": selector,
                "keepTurnsAtLeast": 0,
            }
            target = effect_requirement_target(
                requirement, skill, state, capability=capability
            )
            if target is None or not effect_target_has_capacity(
                requirement, target[0], state, capability=capability
            ):
                continue
            return Decision(
                rule=name,
                skill=skill,
                target_id=target[0],
                target_label=target[1],
            )

    if not probe_unknown_skills:
        return None
    transform = select_transform_skill(state)
    for skill in sorted(
        ready_skills, key=lambda value: int(value.get("slot", 0)), reverse=True
    ):
        skill_type_id = int(skill["typeId"])
        if (
            skill.get("slot") == 1
            or capability_memory.was_probed(skill_type_id)
            or capability_memory.capabilities_for(skill_type_id)
        ):
            continue
        is_transform = bool(
            transform is not None
            and transform.get("typeId") == skill_type_id
            and transform.get("slot") == skill.get("slot")
        )
        if is_transform and not probe_transforms:
            continue
        synthetic = {
            "scope": scope,
            "effect": {"kind": "__unknown_effect_probe__"},
            "keepTurnsAtLeast": 0,
        }
        target = effect_requirement_target(synthetic, skill, state)
        if target is None or not effect_target_has_capacity(
            synthetic, target[0], state
        ):
            continue
        return Decision(
            rule=f"{name}：学习未知技能效果",
            skill=skill,
            target_id=target[0],
            target_label=target[1],
            capability_probe=True,
        )
    return None


def execute_trial_recipe_decision(
    name: str,
    action: dict[str, Any],
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory,
    preferred_skill_type_ids: tuple[int, ...] = (),
    reserved_skill_type_ids: set[int] | None = None,
) -> Decision | None:
    reserved = reserved_skill_type_ids or set()
    if reserved:
        # A skill referenced by an explicit strict rule belongs to that rule.
        # Trial automation may observe effects it already produced, but must not
        # spend the skill while the strict rule is waiting for its full condition.
        state = {
            **state,
            "skills": [
                skill
                for skill in state.get("skills", [])
                if not isinstance(skill, dict)
                or skill.get("typeId") not in reserved
            ],
        }
    try:
        recipes = load_trial_recipes()
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    trials = trial_status_by_id(state)
    requested_ids = configured_trial_ids(action.get("trialIds"))
    eligible_ids = [
        trial_id
        for trial_id, trial in trials.items()
        if trial.get("eligibleNow") is True and trial.get("completed") is not True
    ]
    if requested_ids:
        eligible_ids = [trial_id for trial_id in requested_ids if trial_id in eligible_ids]
    if not eligible_ids and requested_ids:
        prepare_within = int(action.get("prepareWithinBossTurns", 0))
        remaining = turns_until_form_change(state)
        upcoming_form = canonical_chimera_form(next_chimera_form(state))
        if (
            prepare_within > 0
            and isinstance(remaining, int)
            and 0 <= remaining <= prepare_within
        ):
            for trial_id in requested_ids:
                trial = trials.get(trial_id, {})
                recipe = trial_recipe_for_id(recipes, trial_id)
                if (
                    trial.get("activeInChain") is True
                    and trial.get("completed") is not True
                    and recipe
                    and canonical_chimera_form(recipe.get("form")) == upcoming_form
                ):
                    requirements = [
                        requirement
                        for requirement in recipe.get("requirements", [])
                        if isinstance(requirement, dict)
                    ]
                    return select_upcoming_trial_preparation(
                        f"{name} · 为{recipe.get('name', trial_id)}准备",
                        state,
                        capability_memory,
                        requirements,
                    )
    probe_unknown = action.get("probeUnknownSkills") is True
    probe_transforms = action.get("probeTransforms") is True
    for trial_id in eligible_ids:
        recipe = trial_recipe_for_id(recipes, trial_id)
        if not recipe or recipe.get("automation") == "manual":
            continue
        recipe_name = str(recipe.get("name", trial_id))
        requirements = [
            requirement
            for requirement in recipe.get("requirements", [])
            if isinstance(requirement, dict)
        ]
        maintenance_action = {
            "type": "maintainEffects",
            "requirements": requirements,
            "probeUnknownSkills": probe_unknown,
            "probeTransforms": probe_transforms,
        }
        if requirements:
            maintenance = maintain_effects_decision(
                f"{name} · {recipe_name}",
                maintenance_action,
                state,
                capability_memory,
            )
            if maintenance is not None:
                return maintenance
            if not all(
                effect_requirement_satisfied(requirement, state)
                for requirement in requirements
            ):
                # This actor cannot currently supply the trial effect.  Defer
                # to its explicit/default combat policy instead of consuming
                # every such turn with a basic attack.
                continue

        boss = current_boss(state)
        active = next(
            (
                hero for hero in state_entities(state, "heroes")
                if hero.get("id") == state.get("activeHeroId")
            ),
            None,
        )
        forbidden = {
            value
            for value in recipe.get("forbiddenBossEffectTypeIds", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if boss is not None and any(
            isinstance(effect, dict)
            and effect.get("effectTypeId") in forbidden
            for effect in boss.get("effects", [])
        ):
            continue
        maximum_boss_buffs = int(recipe.get("maximumBossBuffs", 10))
        if boss is not None and buff_count(boss) > maximum_boss_buffs:
            continue
        minimum_boss_debuffs = int(recipe.get("minimumBossDebuffs", 0))
        if debuff_count(boss) < minimum_boss_debuffs:
            decision = new_effect_decision(
                f"{name} · {recipe_name}：累积Boss减益",
                "boss",
                state,
                capability_memory,
                forbidden_effect_type_ids=forbidden,
                probe_unknown_skills=probe_unknown,
                probe_transforms=probe_transforms,
            )
            if decision is not None:
                return decision
            continue
        minimum_active_buffs = int(recipe.get("minimumActiveHeroBuffs", 0))
        if buff_count(active) < minimum_active_buffs:
            decision = new_effect_decision(
                f"{name} · {recipe_name}：累积行动者增益",
                "activeHero",
                state,
                capability_memory,
                probe_unknown_skills=probe_unknown,
                probe_transforms=probe_transforms,
            )
            if decision is not None:
                return decision
            continue
        minimum_living = int(recipe.get("minimumLivingAllies", 0))
        if minimum_living and sum(
            hero.get("dead") is not True for hero in state_entities(state, "heroes")
        ) < minimum_living:
            continue

        goal = recipe.get("actionGoal")
        if goal == "damageBoss":
            damage = select_ready_boss_damage(
                f"{name} · {recipe_name}：满足条件后攻击",
                state,
                capability_memory,
                trial_id=trial_id,
                preferred_skill_type_ids=preferred_skill_type_ids,
            )
            if damage is not None:
                return damage
            continue
        if goal in {"periodicDamage", "applyDistinctEffects"}:
            # Maintenance/count branches above are the meaningful trial work.
            # Once those conditions are established, let the hero's normal
            # rotation continue instead of forcing a boss-targeting basic hit.
            continue
        if goal in {
            "survive", "absorbDamage", "reflectDamage", "resistDebuffs"
        }:
            defensive = new_effect_decision(
                f"{name} · {recipe_name}：补充防护效果",
                "activeHero",
                state,
                capability_memory,
                probe_unknown_skills=probe_unknown,
                probe_transforms=probe_transforms,
            )
            if defensive is not None:
                return defensive
            # Survival-style trials advance through the boss response, not by
            # demanding a player basic attack.  Preserve the configured combat
            # rotation when no useful defensive effect can be added now.
            continue
    # Automatic trials are an overlay, not a complete combat rotation.  Let
    # later strict/default rules run when this actor cannot advance a trial.
    return None


DEFAULT_POLICY_SCOPE_KEYS = frozenset(
    {
        "form",
        "activeHeroTypeId",
        "activeHeroFormIndex",
        "activeHeroIsMetamorph",
        "activeHeroIsTransformed",
    }
)

# These conditions determine whether a strict rule belongs to the current
# trial window at all. Unlike effect/cooldown/timing conditions, a different or
# completed trial is not something worth reserving a skill for.
STRICT_RESERVATION_SCOPE_KEYS = DEFAULT_POLICY_SCOPE_KEYS | frozenset(
    {
        "activeTrialsAll",
        "activeTrialsAny",
        "eligibleTrialsAll",
        "eligibleTrialsAny",
    }
)


def strict_rule_reserved_skill_ids(
    rules: list[Any], state: dict[str, Any]
) -> set[int]:
    reserved: set[int] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = rule.get("action")
        if not isinstance(action, dict) or action.get("type") not in {"cast", "transform"}:
            continue
        when = rule.get("when", {})
        if not isinstance(when, dict) or not matches(
            {
                key: value
                for key, value in when.items()
                if key in STRICT_RESERVATION_SCOPE_KEYS
            },
            state,
        ):
            continue
        # A basic skill has no cooldown to preserve. Reserving an A1 for a
        # future strict condition can deadlock the actor when every other skill
        # is cooling down, even though that A1 will still be available in the
        # intended trigger window.
        skill_slot = action.get("skillSlot")
        if skill_slot == 1:
            continue
        skill_type_id = action.get("skillTypeId")
        if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool):
            live_skill = next(
                (
                    skill
                    for skill in state.get("skills", [])
                    if isinstance(skill, dict)
                    and skill.get("typeId") == skill_type_id
                ),
                None,
            )
            if isinstance(live_skill, dict) and live_skill.get("slot") == 1:
                continue
            reserved.add(skill_type_id)
    return reserved


def default_skill_policy_for_state(
    action: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Overlay the current Chimera form's default skill policy when present."""
    form_policies = action.get("formPolicies")
    current_form = canonical_chimera_form(
        state.get("chimera", {}).get("currentForm")
    )
    if not isinstance(form_policies, dict) or not isinstance(current_form, str):
        return action
    form_policy = form_policies.get(current_form)
    if not isinstance(form_policy, dict):
        return action
    effective = {**action, **form_policy}
    # The top-level policy mirrors Ultimate for backwards compatibility.
    # An opener is not an inheritable default: omitting it from Ram/Lion/Snake
    # explicitly means that form has no first-turn action.
    if "firstTurnSkill" not in form_policy:
        effective.pop("firstTurnSkill", None)
    effective.pop("formPolicies", None)
    effective["type"] = "defaultSkillPriority"
    return effective


def default_combat_skill_priority(
    rules: list[Any], state: dict[str, Any]
) -> tuple[int, ...]:
    """Return the active hero's configured priority for the current form."""
    result: list[int] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = rule.get("action")
        if not isinstance(action, dict) or action.get("type") != "defaultSkillPriority":
            continue
        when = rule.get("when", {})
        if not isinstance(when, dict) or not matches(
            {key: value for key, value in when.items() if key in DEFAULT_POLICY_SCOPE_KEYS},
            state,
        ):
            continue
        effective = default_skill_policy_for_state(action, state)
        blocked = {
            value
            for value in effective.get("blockedSkillTypeIds", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
        for entry in effective.get("prioritySkills", []):
            skill_type_id = entry.get("skillTypeId") if isinstance(entry, dict) else None
            if (
                isinstance(skill_type_id, int)
                and not isinstance(skill_type_id, bool)
                and skill_type_id not in blocked
                and skill_type_id not in result
            ):
                result.append(skill_type_id)
    return tuple(result)


def default_skill_priority_decision(
    name: str,
    action: dict[str, Any],
    state: dict[str, Any],
    reserved_skill_type_ids: set[int] | None = None,
) -> Decision | None:
    action = default_skill_policy_for_state(action, state)
    blocked = {
        value
        for value in action.get("blockedSkillTypeIds", [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    reserved = reserved_skill_type_ids or set()
    for entry in action.get("prioritySkills", []):
        if not isinstance(entry, dict):
            continue
        skill_type_id = entry.get("skillTypeId")
        if skill_type_id in blocked or skill_type_id in reserved:
            continue
        decision = default_skill_entry_decision(name, entry, state)
        if decision is not None:
            return decision
    return None


def default_skill_entry_decision(
    name: str,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> Decision | None:
    """Select one configured default skill with its own target policy."""
    if entry.get("isTransform"):
        skill = select_transform_skill(state, entry)
        target_selector: Any = {"type": "self"}
    else:
        skill = select_skill(entry, state)
        target_selector = entry.get("target", {"type": "auto"})
    if skill is None:
        return None
    valid_target_ids = {
        value
        for value in skill.get("validTargetIds", [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    has_dead_legal_target = any(
        hero.get("dead") is True and hero.get("id") in valid_target_ids
        for hero in state_entities(state, "heroes")
    )
    target = select_target(
        {"type": "auto"} if has_dead_legal_target else target_selector,
        skill,
        state,
    )
    selector_type = (
        target_selector.get("type")
        if isinstance(target_selector, dict)
        else target_selector
    )
    used_automatic_fallback = False
    if target is None and selector_type != "auto":
        target = select_target({"type": "auto"}, skill, state)
        used_automatic_fallback = target is not None
    if target is None:
        return None
    target_label = target[1]
    if used_automatic_fallback:
        target_label = f"{target_label}（优先目标不可用，已自动选择）"
    return Decision(
        rule=name,
        skill=skill,
        target_id=target[0],
        target_label=target_label,
    )


def first_turn_default_decision(
    rules: list[Any], state: dict[str, Any]
) -> Decision | None:
    """Run an explicit opener before strict and normal default rules."""
    is_chimera = ACTIVE_BOSS_MODE == "chimera" and isinstance(
        state.get("chimera"), dict
    )
    form_first_turn = state.get("_chimeraFormHeroFirstTurn")
    if is_chimera and isinstance(form_first_turn, bool):
        if not form_first_turn:
            return None
    else:
        # Hydra keeps its battle-wide opener. The fallback also supports direct
        # strategy evaluation when no live form tracker has annotated the state.
        hero_turn_count = state.get("activeHeroTurnCount")
        if (
            not isinstance(hero_turn_count, int)
            or isinstance(hero_turn_count, bool)
            or hero_turn_count not in {0, 1}
        ):
            return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = rule.get("action")
        if not isinstance(action, dict) or action.get("type") != "defaultSkillPriority":
            continue
        action = default_skill_policy_for_state(action, state)
        entry = action.get("firstTurnSkill")
        if not isinstance(entry, dict):
            continue
        when = rule.get("when", {})
        if not isinstance(when, dict) or not matches(
            {key: value for key, value in when.items() if key in DEFAULT_POLICY_SCOPE_KEYS},
            state,
        ):
            continue
        current_form = canonical_chimera_form(
            state.get("_chimeraFormPhase")
            or state.get("chimera", {}).get("currentForm")
        )
        opener_label = (
            f"{current_form or '当前'}形态首回合技能"
            if is_chimera
            else "首回合技能"
        )
        decision = default_skill_entry_decision(
            f"{rule.get('name', '默认技能顺序')} · {opener_label}",
            entry,
            state,
        )
        if decision is not None:
            return decision
    return None


def decision_from_action(
    name: str,
    action: dict[str, Any],
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory | None = None,
    reserved_skill_type_ids: set[int] | None = None,
    automatic_trial_ids: tuple[int, ...] = (),
    preferred_skill_type_ids: tuple[int, ...] = (),
) -> Decision | None:
    action_type = action.get("type")
    if action_type == "defaultSkillPriority":
        return default_skill_priority_decision(
            name, action, state, reserved_skill_type_ids
        )
    if action_type == "executeTrialRecipe":
        memory = capability_memory or SkillCapabilityMemory()
        memory.observe_state(state)
        scoped_action = action
        if "trialIds" not in action and automatic_trial_ids:
            scoped_action = {**action, "trialIds": list(automatic_trial_ids)}
        return execute_trial_recipe_decision(
            name,
            scoped_action,
            state,
            memory,
            preferred_skill_type_ids,
            reserved_skill_type_ids,
        )
    if action_type == "maintainEffects":
        memory = capability_memory or SkillCapabilityMemory()
        memory.observe_state(state)
        return maintain_effects_decision(name, action, state, memory)
    if action_type == "transform":
        skill = select_transform_skill(state, action)
        if skill is None:
            return None
        target = select_target({"type": "self"}, skill, state)
        if target is None:
            return None
        return Decision(
            rule=name,
            skill=skill,
            target_id=target[0],
            target_label=target[1],
        )
    if action_type != "cast":
        return None
    if "target" not in action:
        return None
    skill = select_skill(action, state)
    if skill is None:
        return None
    target = select_target(action.get("target", "boss"), skill, state)
    if target is None:
        return None
    return Decision(
        rule=name,
        skill=skill,
        target_id=target[0],
        target_label=target[1],
    )


def evaluate_strategy_node(
    node: Any,
    state: dict[str, Any],
    *,
    depth: int = 0,
    capability_memory: SkillCapabilityMemory | None = None,
    reserved_skill_type_ids: set[int] | None = None,
    automatic_trial_ids: tuple[int, ...] = (),
    preferred_skill_type_ids: tuple[int, ...] = (),
) -> Decision | None:
    if depth > 32 or not isinstance(node, dict):
        return None
    node_type = node.get("type", "rule")
    if node_type in {"priority", "selector"}:
        children = node.get("children", [])
        if not isinstance(children, list):
            return None
        for child in children:
            decision = evaluate_strategy_node(
                child,
                state,
                depth=depth + 1,
                capability_memory=capability_memory,
                reserved_skill_type_ids=reserved_skill_type_ids,
                automatic_trial_ids=automatic_trial_ids,
                preferred_skill_type_ids=preferred_skill_type_ids,
            )
            if decision is not None:
                return decision
        return None
    if node_type in {"branch", "condition"}:
        selected = node.get("then") if matches(node.get("when", {}), state) else node.get("else")
        return evaluate_strategy_node(
            selected,
            state,
            depth=depth + 1,
            capability_memory=capability_memory,
            reserved_skill_type_ids=reserved_skill_type_ids,
            automatic_trial_ids=automatic_trial_ids,
            preferred_skill_type_ids=preferred_skill_type_ids,
        )
    if node_type == "pause":
        return None
    if node_type not in {
        "rule",
        "cast",
        "transform",
        "maintainEffects",
        "executeTrialRecipe",
        "defaultSkillPriority",
    }:
        return None
    action = node.get(
        "action",
        node
        if node_type in {
            "cast",
            "transform",
            "maintainEffects",
            "executeTrialRecipe",
            "defaultSkillPriority",
        }
        else {},
    )
    if not isinstance(action, dict):
        return None
    if node_type == "rule":
        state = state_for_rule_target(state, action)
        if not matches(node.get("when", {}), state):
            return None
    return decision_from_action(
        str(node.get("name", f"strategy-node-{depth}")),
        action,
        state,
        capability_memory,
        reserved_skill_type_ids,
        automatic_trial_ids,
        preferred_skill_type_ids,
    )


def evaluate(
    config: dict[str, Any],
    state: dict[str, Any],
    capability_memory: SkillCapabilityMemory | None = None,
) -> Decision | None:
    automatic_trial_ids = objective_trial_ids(config, state)
    rules = config.get("rules", [])
    if isinstance(rules, list):
        first_turn = first_turn_default_decision(rules, state)
        if first_turn is not None:
            return first_turn
    preferred_skill_type_ids = (
        default_combat_skill_priority(rules, state)
        if isinstance(rules, list)
        else ()
    )
    tree = config.get("strategyTree")
    if isinstance(tree, dict):
        return evaluate_strategy_node(
            tree,
            state,
            capability_memory=capability_memory,
            automatic_trial_ids=automatic_trial_ids,
            preferred_skill_type_ids=preferred_skill_type_ids,
        )
    if not isinstance(rules, list):
        return None
    reserved = strict_rule_reserved_skill_ids(rules, state)

    # Explicit cast/transform/effect rules are strict regardless of where the
    # editor happens to display an automatic-trial rule. Preserve their mutual
    # order, but always evaluate them before trial automation.
    for rule in rules:
        action = rule.get("action") if isinstance(rule, dict) else None
        action_type = action.get("type") if isinstance(action, dict) else None
        if action_type in {"defaultSkillPriority", "executeTrialRecipe"}:
            continue
        decision = evaluate_strategy_node(
            rule,
            state,
            capability_memory=capability_memory,
            reserved_skill_type_ids=reserved,
            automatic_trial_ids=automatic_trial_ids,
            preferred_skill_type_ids=preferred_skill_type_ids,
        )
        if decision is not None:
            return decision

    matched_trial_policy_name: str | None = None
    for rule in rules:
        action = rule.get("action") if isinstance(rule, dict) else None
        if not isinstance(action, dict) or action.get("type") != "executeTrialRecipe":
            continue
        if (
            matched_trial_policy_name is None
            and isinstance(rule, dict)
        ):
            when = rule.get("when", {})
            scoped_state = state_for_rule_target(state, action)
            if isinstance(when, dict) and matches(when, scoped_state):
                matched_trial_policy_name = str(
                    rule.get("name", "按当前试炼自动决策")
                )
        decision = evaluate_strategy_node(
            rule,
            state,
            capability_memory=capability_memory,
            reserved_skill_type_ids=reserved,
            automatic_trial_ids=automatic_trial_ids,
            preferred_skill_type_ids=preferred_skill_type_ids,
        )
        if decision is not None:
            return decision

    for rule in rules:
        action = rule.get("action") if isinstance(rule, dict) else None
        if not isinstance(action, dict) or action.get("type") != "defaultSkillPriority":
            continue
        decision = evaluate_strategy_node(
            rule,
            state,
            capability_memory=capability_memory,
            reserved_skill_type_ids=reserved,
            automatic_trial_ids=automatic_trial_ids,
            preferred_skill_type_ids=preferred_skill_type_ids,
        )
        if decision is not None:
            return decision
    if matched_trial_policy_name is not None:
        memory = capability_memory or SkillCapabilityMemory()
        memory.observe_state(state)
        return adaptive_combat_decision(
            f"{matched_trial_policy_name} · 当前无可执行试炼专用动作",
            state,
            memory,
            excluded_skill_type_ids=reserved,
        )
    return None


def no_decision_diagnostic(config: dict[str, Any], state: dict[str, Any]) -> str:
    """Summarise why the active hero's flat rules did not yield an action."""
    rules = config.get("rules")
    if not isinstance(rules, list):
        return "策略树没有产生可执行动作"
    candidates: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
        hero_scope = {
            key: value
            for key, value in when.items()
            if key in {"activeHeroTypeId"}
        }
        if hero_scope and not matches(hero_scope, state):
            continue
        name = str(rule.get("name") or "未命名规则")
        scoped_state = state_for_rule_target(
            state,
            rule.get("action") if isinstance(rule.get("action"), dict) else {},
        )
        if not matches(when, scoped_state):
            expected_form = when.get("activeHeroFormIndex")
            actual_form = state.get("activeHeroFormIndex")
            if expected_form is not None and not matches(
                {"activeHeroFormIndex": expected_form}, state
            ):
                reason = f"要求英雄形态 {expected_form}，当前为 {actual_form}"
            elif "form" in when and not matches({"form": when["form"]}, state):
                reason = f"奇美拉形态条件不满足（当前 {state.get('form', '未知')}）"
            else:
                reason = "触发条件不满足"
        else:
            action = rule.get("action") if isinstance(rule.get("action"), dict) else {}
            action_type = action.get("type")
            if action_type == "defaultSkillPriority":
                reason = "条件满足，但优先列表中没有已就绪且目标合法的技能"
            elif action_type == "executeTrialRecipe":
                reason = "条件满足，但当前没有可安全执行的试炼动作或基础技能"
            else:
                reason = "条件满足，但指定技能未就绪或没有合法目标"
        candidates.append(f"“{name}”：{reason}")
    if not candidates:
        return "没有为当前英雄配置规则"
    shown = candidates[:3]
    suffix = f"；另有 {len(candidates) - len(shown)} 条" if len(candidates) > len(shown) else ""
    return "已检查 " + "；".join(shown) + suffix


def process_state(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    agent: Path,
    ipc: AgentIpc,
    session_id: int,
    execute_requested: bool,
    nonce: int,
    ignore_freshness: bool = False,
    runtime_state: dict[str, Any] | None = None,
    capability_memory: SkillCapabilityMemory | None = None,
) -> bool:
    require_takeover_active(ipc, session_id)
    annotate_team_positions(state, ipc.lifecycle())
    if capability_memory is not None:
        capability_memory.observe_state(state)
    max_age = int(config.get("safety", {}).get("requireFreshSnapshotMs", 500))
    reason = safety_reason(
        state,
        max_age,
        ignore_freshness=ignore_freshness,
    )
    if reason:
        print(f"暂停：{reason}", flush=True)
        return False
    objective_report = evaluate_objectives(config, state)
    if runtime_state is not None and ACTIVE_BOSS_MODE == "chimera":
        runtime_state["lastObjectiveReport"] = {
            "mandatoryTrialIds": list(objective_report.mandatory_trial_ids),
            "completedTrialIds": list(objective_report.completed_trial_ids),
            "missingTrialIds": list(objective_report.missing_trial_ids),
            "currentDamage": objective_report.current_damage,
            "minimumDamage": objective_report.minimum_damage,
        }
    if (
        objective_report.mandatory_trial_ids
        or objective_report.minimum_damage > 0
    ):
        print(
            "目标进度："
            f"必要试炼 {len(objective_report.completed_trial_ids)}/"
            f"{len(objective_report.mandatory_trial_ids)}，"
            f"伤害 {objective_report.current_damage:g}/"
            f"{objective_report.minimum_damage:g}",
            flush=True,
        )
    if objective_report.mandatory_impossible:
        objectives = config.get("objectives", {})
        objective_config = objectives if isinstance(objectives, dict) else {}
        behavior = (
            objectives.get(
                "onMandatoryTrialImpossible", "free_regroup_and_retry_manual"
            )
            if isinstance(objectives, dict)
            else "free_regroup_and_retry_manual"
        )
        print(
            "必要试炼已不可完成："
            + ", ".join(map(str, objective_report.impossible_trial_ids)),
            flush=True,
        )
        execute = execute_requested and config.get("mode") == "execute"
        if behavior == "free_regroup_and_retry_manual" and execute:
            runtime = runtime_state if runtime_state is not None else {}
            used = int(runtime.get("regroupRetries", 0))
            maximum = int(objective_config.get("maxRegroupRetries", 10))
            if maximum > 0 and used >= maximum:
                raise RuntimeError(
                    f"已达到免费重整重试上限 {maximum}，停止接管"
                )
            free_regroup_and_retry_manual(
                ipc,
                pid=int(state["pid"]),
                agent=agent,
                session_id=session_id,
                nonce=nonce,
                desired_hero_ids=(
                    list(config["team"]["heroInstanceIds"])
                    if isinstance(config.get("team"), dict)
                    and isinstance(config["team"].get("heroInstanceIds"), list)
                    else None
                ),
                desired_hero_type_ids=(
                    list(config["team"].get("heroTypeIds", config["team"].get("heroIds")))
                    if isinstance(config.get("team"), dict)
                    and isinstance(config["team"].get("heroTypeIds", config["team"].get("heroIds")), list)
                    else None
                ),
            )
            runtime["regroupRetries"] = used + 1
            return False
        if behavior == "free_regroup_and_stop" and execute:
            free_regroup_and_stop(
                ipc,
                pid=int(state["pid"]),
                agent=agent,
                session_id=session_id,
            )
            raise FreeRegroupCompleted()
        if not execute:
            print("观察模式：不会实际触发免费重整。", flush=True)
        else:
            print(f"未知的必要试炼失败处理方式：{behavior}", flush=True)
        return False
    if ACTIVE_BOSS_MODE == "chimera":
        annotate_chimera_form_first_turn(
            state, runtime_state if runtime_state is not None else {}
        )
    decision = evaluate(config, state, capability_memory)
    active_hero_label = str(
        state.get("activeHeroName")
        or (
            f"英雄 {state['activeHeroTypeId']}"
            if isinstance(state.get("activeHeroTypeId"), int)
            else "未知英雄"
        )
    )
    if decision is None:
        diagnostic = no_decision_diagnostic(config, state)
        print(
            f"未行动：当前英雄“{active_hero_label}”没有匹配且可安全执行的规则"
            "（条件不满足、技能未就绪或没有合法目标）；"
            f"{diagnostic}；接管保持等待。",
            flush=True,
        )
        return False

    skill = decision.skill
    skill_label = skill_display_name(skill)
    if is_unresolved_skill_name(skill.get("name")):
        skill_label = _INITIAL_SKILL_NAMES.get(skill.get("typeId"), skill_label)
    action_summary = (
        f"准备执行：英雄“{active_hero_label}”命中规则“{decision.rule}”，"
        f"使用“{skill_label}”，目标“{decision.target_label}”；"
    )
    if decision.rule.endswith("首回合技能"):
        if ACTIVE_BOSS_MODE == "chimera":
            action_summary += (
                f"英雄在 {state.get('_chimeraFormPhase', '当前')} 形态的第一次行动；"
            )
        else:
            action_summary += (
                f"英雄个人首回合（游戏计数 {state.get('activeHeroTurnCount', '?')}）；"
            )
    if ACTIVE_BOSS_MODE == "hydra":
        hydra = state.get("hydra", {})
        hydra_turn = (
            hydra.get("turnCount", "?")
            if isinstance(hydra, dict)
            else "?"
        )
        living_heads = sum(
            boss.get("dead") is not True
            for boss in state_entities(state, "bosses")
        )
        action_summary += f"六头蛇第 {hydra_turn} 回合，在场目标 {living_heads} 个"
    else:
        chimera = state.get("chimera", {})
        predicted_form = next_chimera_form(state)
        remaining_form_turns = turns_until_form_change(state)
        action_summary += (
            f"奇美拉第 {chimera.get('turnCount', '?')} 回合，"
            f"形态 {chimera.get('currentForm', 'Unknown')}，"
            f"下一形态 {predicted_form or 'Unknown'}，"
            f"距切换 "
            f"{remaining_form_turns if remaining_form_turns is not None else '?'} 回合"
        )
    print(action_summary, flush=True)
    execute = execute_requested and config.get("mode") == "execute"
    pointers = state.get("pointers", {})
    battle = state.get("battle", {})
    region_id = battle.get("regionTypeId")
    result = queue_command(
        int(state["pid"]),
        agent,
        session_id=session_id,
        context=int(pointers.get("context", 0)),
        generator=int(pointers["generator"]),
        mode=int(pointers["mode"]),
        skill_data=int(skill["skillDataPtr"]),
        target_id=decision.target_id,
        skill_id=int(skill["skillId"]),
        verified_skill_type_id=int(skill["typeId"]),
        expected_area_id=int(battle["areaTypeId"]),
        expected_region_id=int(region_id) if isinstance(region_id, int) else -(2**31),
        expected_round=int(battle["round"]),
        expected_turn=int(battle["turn"]),
        expected_player_turn_count=int(battle["playerTurnCount"]),
        expected_active_hero_id=int(state["activeHeroId"]),
        expected_active_hero_turn_count=int(state["activeHeroTurnCount"]),
        expected_active_hero_form_index=int(state["activeHeroFormIndex"]),
        execute=execute,
        nonce=nonce,
    )
    if not result.get("queued"):
        raise RuntimeError(f"代理拒绝接收请求：{result}")
    acknowledgement = wait_for_command_ack(
        ipc,
        session_id=session_id,
        nonce=nonce,
    )
    if acknowledgement is None:
        raise RuntimeError(f"代理回执超时（请求 {nonce}）")
    expected_status = "submitted" if execute else "validated"
    if acknowledgement.get("status") != expected_status:
        if recoverable_command_rejection(acknowledgement):
            rejection_reason = str(acknowledgement.get("reason") or "")
            changed_label = (
                "代理二次安全校验未通过"
                if rejection_reason == "guard_failed"
                else "游戏状态已由手动操作或回合推进改变"
            )
            diagnostic_reader = getattr(ipc, "diagnostic", None)
            guard_detail = (
                diagnostic_reader()
                if rejection_reason == "guard_failed" and callable(diagnostic_reader)
                else None
            )
            detail_suffix = (
                f"；校验详情：{guard_detail}"
                if isinstance(guard_detail, str) and guard_detail
                else ""
            )
            print(
                f"未执行：英雄“{active_hero_label}”命中规则“{decision.rule}”，"
                f"但{changed_label}（{rejection_reason or '状态已变化'}）；"
                "旧请求已被安全拒绝，"
                f"接管保持运行并等待下一回合{detail_suffix}。",
                flush=True,
            )
            return False
        raise RuntimeError(
            f"代理拒绝请求：{acknowledgement.get('reason') or acknowledgement.get('status')}"
        )
    if execute and decision.capability_probe and capability_memory is not None:
        skill_type_id = skill.get("typeId")
        if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool):
            capability_memory.mark_probed(skill_type_id)
    if execute:
        advanced = wait_for_turn_advance(ipc, state, session_id)
        if advanced is None:
            lifecycle_after = ipc.lifecycle() or {}
            if lifecycle_after.get("screen") == "result":
                print(
                    f"执行成功：英雄“{active_hero_label}”已按规则“{decision.rule}”"
                    f"施放“{skill_label}”，随后战斗进入结算画面。",
                    flush=True,
                )
                return True
            print(
                f"已提交：英雄“{active_hero_label}”已按规则“{decision.rule}”"
                f"施放“{skill_label}”，但暂未观察到回合推进；"
                "本回合不会重复操作，接管保持运行并继续等待新状态。",
                flush=True,
            )
            return True
        if capability_memory is not None:
            learned = capability_memory.observe_state(advanced)
            if learned:
                print(f"已从本次动作学习 {learned} 条技能效果能力。", flush=True)
            skill_type_id = skill.get("typeId")
            if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool):
                capability_memory.observe_action_damage(
                    skill_type_id,
                    state,
                    advanced,
                    trial_id=decision.trial_id,
                )
        if ACTIVE_BOSS_MODE == "hydra":
            previous_hydra = state.get("hydra", {})
            next_hydra = advanced.get("hydra", {})
            previous_turn = (
                previous_hydra.get("turnCount", "?")
                if isinstance(previous_hydra, dict)
                else "?"
            )
            next_turn = (
                next_hydra.get("turnCount", "?")
                if isinstance(next_hydra, dict)
                else "?"
            )
            progress_label = f"六头蛇回合 {previous_turn}→{next_turn}"
        else:
            previous_chimera = state.get("chimera", {})
            next_chimera = advanced.get("chimera", {})
            progress_label = (
                f"奇美拉回合 {previous_chimera.get('turnCount', '?')}→"
                f"{next_chimera.get('turnCount', '?')}，"
                f"形态 {previous_chimera.get('currentForm', 'Unknown')}→"
                f"{next_chimera.get('currentForm', 'Unknown')}"
            )
        print(
            f"执行成功：英雄“{active_hero_label}”已按规则“{decision.rule}”"
            f"施放“{skill_label}”，目标“{decision.target_label}”；{progress_label}。",
            flush=True,
        )
    else:
        print(
            f"只读验证完成：英雄“{active_hero_label}”会命中规则“{decision.rule}”，"
            f"使用“{skill_label}”，目标“{decision.target_label}”；未向游戏提交行动。",
            flush=True,
        )
    return True


def main() -> int:
    global _PAUSE_EVENT, ACTIVE_BOSS_MODE
    configure_text_streams()
    parser = argparse.ArgumentParser(description="奇美拉策略控制器")
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--account-name",
        help="界面选定的游戏内用户名；控制器会与共享状态精确核对",
    )
    parser.add_argument(
        "--account-user-id",
        type=int,
        help="界面选定的游戏玩家 ID；代理会在游戏主线程再次核对",
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/chimera-strategy.example.json")
    )
    parser.add_argument(
        "--boss-mode",
        choices=("chimera", "hydra"),
        default="chimera",
        help="选择共享控制器中的联盟 Boss 模式",
    )
    parser.add_argument(
        "--agent", type=Path, default=Path("build/agent-1236/Release/RaidChimeraAgent.dll")
    )
    parser.add_argument(
        "--rotation-archive",
        type=Path,
        default=DEFAULT_ROTATION_ARCHIVE,
        help="只在发现新试炼/奖励目录或新属性阶段时更新的轮换档案",
    )
    parser.add_argument("--once", action="store_true", help="只处理最新快照")
    parser.add_argument("--execute", action="store_true", help="允许实际执行")
    parser.add_argument(
        "--capability-cache",
        type=Path,
        default=DEFAULT_CAPABILITY_CACHE,
        help="Only rewritten when a new skill-to-effect capability is learned.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="在当前 Boss 队伍界面用已选队伍开始首场战斗",
    )
    parser.add_argument(
        "--bootstrap-current",
        action="store_true",
        help=(
            "启动时允许使用最近的等待回合；"
            "游戏主线程守卫仍会重新验证全部对象"
        ),
    )
    parser.add_argument(
        "--max-commands",
        type=int,
        default=0,
        help="成功提交指定数量的请求后停止；0 表示不限制",
    )
    args = parser.parse_args()
    ACTIVE_BOSS_MODE = args.boss_mode
    start_parent_watchdog(args.parent_pid)

    try:
        config_path = args.config.resolve()
        raw_config = load_json(config_path)
        config = (
            strategy_for_mode(load_strategy_store(config_path), args.boss_mode)
            if isinstance(raw_config.get("modes"), dict)
            else raw_config
        )
        validate_strategy_config(config, boss_mode=args.boss_mode)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"策略配置无效，未接管游戏：{error}", flush=True)
        return 2
    agent = args.agent.resolve()
    requested_agent_parts = {part.lower() for part in agent.parts}
    requested_versioned_agent = any(
        part.startswith("agent-") and part[6:].isdigit()
        for part in requested_agent_parts
    )
    if (
        requested_versioned_agent
        and agent != CURRENT_AGENT.resolve()
        and CURRENT_AGENT.is_file()
    ):
        agent = CURRENT_AGENT.resolve()
    capability_cache = args.capability_cache.resolve()
    capability_memory = SkillCapabilityMemory.load(capability_cache)
    mutex = NamedMutex(rf"Local\RaidChimeraController-{args.pid}")
    try:
        mutex.acquire()
    except RuntimeError:
        print(f"PID {args.pid} 已有一个控制器在运行。", flush=True)
        return 4
    session_id = 0
    takeover_armed = False
    pause_event = ControllerPauseEvent(args.pid)
    try:
        pause_event.open()
        _PAUSE_EVENT = pause_event
        with AgentIpc(args.pid) as ipc:
            header = ipc.header()
            if not header.get("ready"):
                print(f"代理版本不匹配或尚未就绪：{header}", flush=True)
                return 5
            binding = account_binding(ipc.account(), args.pid)
            if args.account_name is not None and args.account_name != binding.account_name:
                print(
                    f"所选游戏内用户名已变化：预期 {args.account_name}，"
                    f"当前 {binding.account_name}",
                    flush=True,
                )
                return 5
            if (
                args.account_user_id is not None
                and args.account_user_id != binding.user_id
            ):
                print(
                    f"所选游戏玩家 ID 已变化：预期 {args.account_user_id}，"
                    f"当前 {binding.user_id}",
                    flush=True,
                )
                return 5
            session_id = secrets.randbits(63) or 1
            takeover = set_takeover(
                args.pid,
                agent,
                session_id=session_id,
                active=True,
                expected_user_id=binding.user_id,
                boss_mode=args.boss_mode,
            )
            if not takeover.get("accepted"):
                print(f"代理拒绝接管会话：{takeover}", flush=True)
                return 5
            takeover_armed = True
            require_takeover_active(ipc, session_id)
            require_account_binding(ipc, binding)
            print(
                f"已绑定游戏内账户 {binding.account_name}（玩家 ID {binding.user_id}）；"
                "账户变化会立即停止接管。",
                flush=True,
            )
            runtime_state: dict[str, Any] = {"regroupRetries": 0}
            rotation_archive = args.rotation_archive.resolve()
            last_rotation_archive_key = archive_rotation_catalog_if_changed(
                ipc, rotation_archive
            )
            lifecycle_at_start = ipc.lifecycle() or {}
            last_lifecycle_sequence = lifecycle_at_start.get("sequence")
            auto_start_enabled = (
                args.auto_start
                and args.execute
                and config.get("mode") == "execute"
            )
            configured_team = config.get("team")
            desired_hero_type_ids = (
                list(configured_team.get("heroTypeIds", configured_team.get("heroIds")))
                if isinstance(configured_team, dict)
                and isinstance(configured_team.get("heroTypeIds", configured_team.get("heroIds")), list)
                else None
            )
            desired_hero_ids = (
                list(configured_team["heroInstanceIds"])
                if isinstance(configured_team, dict)
                and isinstance(configured_team.get("heroInstanceIds"), list)
                else None
            )
            auto_start_attempted = False
            if auto_start_enabled:
                require_account_binding(ipc, binding)
                auto_start_attempted = start_first_battle_if_ready(
                    ipc,
                    pid=args.pid,
                    agent=agent,
                    session_id=session_id,
                    desired_hero_ids=desired_hero_ids,
                    desired_hero_type_ids=desired_hero_type_ids,
                    boss_mode=args.boss_mode,
                    announce_wait=True,
                )
            if args.once:
                state = ipc.decision()
                if not is_battle_decision_state(state):
                    print("尚未收到每回合决策快照")
                    return 2
                require_account_binding(ipc, binding)
                return 0 if process_state(
                    config,
                    state,
                    agent=agent,
                    ipc=ipc,
                    session_id=session_id,
                    execute_requested=args.execute,
                    nonce=1,
                    ignore_freshness=args.bootstrap_current,
                    runtime_state=runtime_state,
                    capability_memory=capability_memory,
                ) else 3

            print(
                f"正在监听测试账户 PID {args.pid}；"
                "点击游戏内暂停或主工具的“暂停接管”即可停止。",
                flush=True,
            )
            last_sequence: int | None = None
            nonce = 1
            submitted = 0
            initial = ipc.decision()
            if is_battle_decision_state(initial):
                initial_rotation_key = decision_rotation_observation_key(initial)
                if initial_rotation_key != last_rotation_archive_key:
                    last_rotation_archive_key = archive_rotation_catalog_if_changed(
                        ipc, rotation_archive, state=initial
                    )
                last_sequence = initial.get("sequence")
                if auto_start_attempted:
                    print(
                        f"已从{mode_spec(args.boss_mode)['label']}准备界面启动战斗；"
                        "忽略进入战斗前的旧回合快照，等待首个新回合。",
                        flush=True,
                    )
                else:
                    require_account_binding(ipc, binding)
                    if process_state(
                        config,
                        initial,
                        agent=agent,
                        ipc=ipc,
                        session_id=session_id,
                        execute_requested=args.execute,
                        nonce=nonce,
                        ignore_freshness=args.bootstrap_current,
                        runtime_state=runtime_state,
                        capability_memory=capability_memory,
                    ):
                        submitted += 1
                    nonce += 1
                    if args.max_commands > 0 and submitted >= args.max_commands:
                        return 0
            while True:
                if _PARENT_EXITED.is_set():
                    raise ParentProcessExited
                require_takeover_active(ipc, session_id)
                require_account_binding(ipc, binding)
                current_lifecycle = ipc.lifecycle() or {}
                current_lifecycle_sequence = current_lifecycle.get("sequence")
                if current_lifecycle_sequence != last_lifecycle_sequence:
                    last_lifecycle_sequence = current_lifecycle_sequence
                    archived_key = archive_rotation_catalog_if_changed(
                        ipc, rotation_archive
                    )
                    if archived_key is not None:
                        last_rotation_archive_key = archived_key
                if result_screen_reached(
                    ipc,
                    config=config,
                    pid=args.pid,
                    agent=agent,
                    session_id=session_id,
                    boss_mode=args.boss_mode,
                    execute_requested=args.execute,
                    runtime_state=runtime_state,
                    desired_hero_ids=desired_hero_ids,
                    desired_hero_type_ids=desired_hero_type_ids,
                    nonce=nonce,
                ):
                    return 0
                if auto_start_enabled and not auto_start_attempted:
                    require_account_binding(ipc, binding)
                    auto_start_attempted = start_first_battle_if_ready(
                        ipc,
                        pid=args.pid,
                        agent=agent,
                        session_id=session_id,
                        desired_hero_ids=desired_hero_ids,
                        desired_hero_type_ids=desired_hero_type_ids,
                        boss_mode=args.boss_mode,
                    )
                state = ipc.decision()
                if is_battle_decision_state(state):
                    state_rotation_key = decision_rotation_observation_key(state)
                    if state_rotation_key != last_rotation_archive_key:
                        archived_key = archive_rotation_catalog_if_changed(
                            ipc, rotation_archive, state=state
                        )
                        if archived_key is not None:
                            last_rotation_archive_key = archived_key
                    sequence = state.get("sequence")
                    if sequence != last_sequence:
                        last_sequence = sequence
                        require_account_binding(ipc, binding)
                        if process_state(
                            config,
                            state,
                            agent=agent,
                            ipc=ipc,
                            session_id=session_id,
                            execute_requested=args.execute,
                            nonce=nonce,
                            runtime_state=runtime_state,
                            capability_memory=capability_memory,
                        ):
                            submitted += 1
                        nonce += 1
                        if args.max_commands > 0 and submitted >= args.max_commands:
                            print("已达到本次测试的动作上限。", flush=True)
                            return 0
                time.sleep(0.05)
    except KeyboardInterrupt:
        print("已停止。")
        return 0
    except ControllerPaused:
        print("已按用户要求暂停接管；游戏保持在当前状态。", flush=True)
        return 0
    except ParentProcessExited:
        print("主工具已经关闭；控制器已解除接管并退出。", flush=True)
        return 0
    except GamePaused:
        print("检测到游戏内暂停按钮；工具已解除接管。", flush=True)
        return 8
    except TakeoverInterrupted as error:
        print(str(error), flush=True)
        return 6
    except FreeRegroupCompleted:
        return 7
    except Exception as error:
        print(f"控制器为安全起见已停止：{error}", flush=True)
        return 5
    finally:
        try:
            capability_memory.save_if_changed(capability_cache)
        except OSError as error:
            print(f"技能能力缓存写入失败：{error}", flush=True)
        if takeover_armed and session_id:
            try:
                set_takeover(
                    args.pid,
                    agent,
                    session_id=session_id,
                    active=False,
                )
            except Exception as error:
                print(f"接管会话清理失败：{error}", flush=True)
        _PAUSE_EVENT = None
        pause_event.close()
        mutex.close()
        _PARENT_CLEANUP_FINISHED.set()


if __name__ == "__main__":
    sys.exit(main())

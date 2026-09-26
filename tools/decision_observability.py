"""Bounded, read-only explanations of decisions already made by the engine."""
from __future__ import annotations
import copy
import json
from typing import Any, Callable


def emit_telemetry(**values: Any) -> None:
    print("@@raid-telemetry " + json.dumps(values, ensure_ascii=False, separators=(",", ":")), flush=True)


def condition_details(node: Any, state: dict, matches: Callable,
                      budget: list[int], depth: int = 0, count_checks: Callable | None = None) -> dict:
    """Keep AND/OR grouping and each candidate's results together."""
    if not isinstance(node, dict) or budget[0] <= 0 or depth > 16:
        return {"truncated": True}
    budget[0] -= 1
    result = {"passed": matches({"conditionTree": node}, state)}
    if node.get("type") == "group":
        result.update(type="group", operator=node.get("operator"), negate=node.get("negate", False), children=[
            condition_details(child, state, matches, budget, depth + 1, count_checks)
            for child in node.get("children", [])[:128]
        ])
    else:
        result["condition"] = copy.deepcopy(node)
        if node.get("type") == "effectCount" and count_checks is not None:
            result["counts"] = count_checks(node, state)
    return result


def pick(value: Any, keys: tuple[str, ...]) -> dict:
    return {key: copy.deepcopy(value[key]) for key in keys if key in value} if isinstance(value, dict) else {}


def decision_context(state: dict) -> dict:
    """Retain observed combat inputs; never serialize pointers or account data."""
    entities = []
    for kind in ("bosses", "heroes"):
        for entity in state.get(kind, [])[:12]:
            if not isinstance(entity, dict):
                continue
            row = pick(entity, ("id", "typeId", "name", "dead", "healthPct", "healthRaw", "maxHealthRaw", "modelFound"))
            row["kind"] = kind
            row["effects"] = [pick(effect, ("effectTypeId", "effectKindId", "effectKind", "name", "turnsLeft"))
                              for effect in entity.get("effects", [])[:64]]
            row["challenges"] = [pick(trial, ("id", "name", "activeInChain", "eligibleNow", "completed", "progressRatio", "possible", "impossible", "lastEligibleBossTurn", "impossibilityReason"))
                                 for trial in entity.get("challenges", [])[:128]]
            entities.append(row)
    context = {
        "activeHero": pick(state, ("activeHeroId", "activeHeroTypeId", "activeHeroName", "activeHeroTurnCount", "activeHeroFormIndex")),
        "battle": pick(state.get("battle"), ("kind", "round", "turn", "playerTurnCount", "currentDamage", "finished")),
        "battleGeneration": state.get("battleGeneration"),
        "observedAtTick": state.get("observedAtTick"),
        "chimera": pick(state.get("chimera"), ("id", "currentForm", "turnCount")),
        "entities": entities,
        "skills": [pick(skill, ("slot", "typeId", "name", "ready", "cooldown", "blocked", "validTargetIds"))
                   for skill in state.get("skills", [])[:16]],
        "reservedStrictSkillTypeIds": state.get("_reservedStrictSkillTypeIds", []),
    }
    if state.get("bossMode") == "hydra":
        # The normal controller already receives this stable native checkpoint.
        # Preserve it with the submitted decision so a sidecar stopping or
        # missing a publication does not erase every later RNG comparison.
        context["battleRandom"] = pick(state.get("battleRandom"), (
            "schema", "available", "source", "capturePoint", "readStatus",
            "words", "turn", "playerTurnCount", "seedAvailable", "seed",
            "battleSetupIdAvailable", "battleSetupId",
        ))
    return context


def record_rule(state: dict, rule: Any, index: int, decision: Any,
                matches: Callable, scoped_states: Callable, count_checks: Callable | None = None) -> None:
    trace = state.get("_decisionTrace")
    if not isinstance(trace, list) or len(trace) >= 100 or not isinstance(rule, dict):
        return
    when = rule.get("when", {})
    if not isinstance(when, dict):
        return
    hero = {key: value for key, value in when.items() if key == "activeHeroTypeId"}
    if hero and not matches(hero, state):
        return
    action = rule.get("action", {})
    candidates = scoped_states(state, action) if isinstance(action, dict) else [state]
    checks = [{"key": key, "passed": any(matches({key: value}, candidate) for candidate in candidates)}
              for key, value in when.items()][:32]
    condition_match = any(matches(when, candidate) for candidate in candidates)
    trace.append({
        "index": index + 1, "name": str(rule.get("name") or f"规则 {index + 1}"),
        "kind": action.get("type", "cast") if isinstance(action, dict) else "cast",
        "outcome": "selected" if decision is not None else "unavailable" if condition_match else "condition_failed",
        "conditions": checks,
        "action": pick(action, ("type", "skillTypeId", "skillSlot", "target")),
        "candidates": [{
            "targetId": candidate.get("_conditionBossId", candidate.get("chimera", {}).get("id")),
            "matched": matches(when, candidate),
            "conditionTree": condition_details(when["conditionTree"], candidate, matches, [128], count_checks=count_checks),
        } for candidate in candidates[:12]] if "conditionTree" in when else [],
    })


def decision_details(state: dict, decision: Any) -> dict:
    skill = getattr(decision, "skill", {}) or {}
    target = getattr(decision, "target_id", None)
    targets = skill.get("validTargetIds", [])
    return {
        "sequence": state.get("sequence"), "hero": state.get("activeHeroName"),
        "rule": getattr(decision, "rule", None),
        "skill": skill.get("name") or skill.get("typeId"),
        "target": getattr(decision, "target_label", None),
        "targetId": target,
        "legalTargetIds": targets if isinstance(targets, list) else [],
        "rules": state.get("_decisionTrace", []),
        "flowTrace": state.get("_flowTrace", []),
        "flowRevision": state.get("_flowRevision"),
        "status": "selected" if decision is not None else "waiting",
        "skillSlot": skill.get("slot"), "skillTypeId": skill.get("typeId"),
        "context": decision_context(state),
    }


def devour_details(runtime: dict, config: dict, trigger: Any = None) -> dict:
    tracker = runtime.get("hydraDevourTracker", {})
    maximum = int(config.get("objectives", {}).get("maxRegroupRetries", 10))
    used = int(runtime.get("regroupRetries", 0))
    return {
        "armed": tracker.get("armed") is True,
        "sequence": tracker.get("sequence", []),
        "retriesUsed": used, "retriesMaximum": maximum,
        "retriesRemaining": max(0, maximum - used) if maximum > 0 else None,
        "trigger": ({"conditionIndex": trigger.condition_index + 1,
                     "markIndex": trigger.mark_index, "hero": trigger.actual_hero_name}
                    if trigger is not None else None),
    }

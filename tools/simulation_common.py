"""Pieces shared by the Chimera and Hydra strategy simulations.

* which rule a decision came from (the rule trace, or the rule a derived
  decision label extends);
* the "stuck" report: where the live takeover would stall, with the active
  hero's skills and why each of its rules did not act;
* a compact battle snapshot per decision (health, buffs/debuffs, skill
  cooldowns) for the report's turn log;
* run files, gzip-compressed since the snapshots make them large.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import chimera_controller as controller
from strategy_storage import atomic_write_bytes


def selected_rule(trace: object) -> tuple[int | None, str | None]:
    for entry in trace if isinstance(trace, list) else []:
        if isinstance(entry, dict) and entry.get("outcome") == "selected":
            index = entry.get("index")
            return (index if isinstance(index, int) else None), entry.get("name")
    return None, None


def rule_index_by_name(strategy: dict[str, Any], decided: str | None) -> int | None:
    """1-based rule whose name the decision's label extends.

    First-turn skills, trial-automation fallbacks and mythic follow-ups are
    labelled "<rule name> · <detail>" without passing through the rule trace.
    """
    if not isinstance(decided, str):
        return None
    best: tuple[int, int] | None = None
    for index, rule in enumerate(strategy.get("rules") or [], 1):
        name = rule.get("name") if isinstance(rule, dict) else None
        if isinstance(name, str) and name and (decided == name or decided.startswith(name + " · ")):
            if best is None or len(name) > best[1]:
                best = (index, len(name))
    return best[0] if best else None


def decision_rule_index(strategy: dict[str, Any], state: dict[str, Any], rule: str | None) -> int | None:
    index, name = selected_rule(state.get("_decisionTrace"))
    return index if name == rule else rule_index_by_name(strategy, rule)


def stuck_report(strategy: dict[str, Any], state: dict[str, Any], reason: str,
                 detail: str | None, **extra: Any) -> dict[str, Any]:
    """Where the live takeover would stall: what the hero had and why no rule fired."""
    battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
    reserved = {value for value in state.get("_reservedStrictSkillTypeIds") or [] if isinstance(value, int)}
    try:
        reserved.update(controller.trial_rule_skill_owners(strategy.get("rules") or [], state))
    except Exception:
        pass
    skills = []
    for skill in state.get("skills", []):
        if not isinstance(skill, dict) or not isinstance(skill.get("typeId"), int):
            continue
        skills.append({"typeId": skill["typeId"], "slot": skill.get("slot"), "ready": skill.get("ready") is True,
                       "cooldown": skill.get("cooldown"), "defaultCooldown": skill.get("defaultCooldown"),
                       "validTargets": len(skill.get("validTargetIds") or []),
                       "reserved": skill["typeId"] in reserved})
    try:
        rules = controller.no_decision_report(strategy, state)
    except Exception:
        rules = []
    return {"reason": reason, "turn": battle.get("turn"),
            "activeHeroId": state.get("activeHeroId"), "activeHeroTypeId": state.get("activeHeroTypeId"),
            "activeHeroFormIndex": state.get("activeHeroFormIndex"), "skills": skills, "rules": rules[:40],
            "detail": detail, "snapshot": battle_snapshot(state), **extra}


def _effects(entity: dict[str, Any]) -> list[list[int]]:
    """[effectTypeId, turnsLeft, count] per effect type, stacks folded together."""
    folded: dict[int, list[int]] = {}
    for effect in entity.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        type_id = effect.get("effectTypeId")
        if not isinstance(type_id, int) or isinstance(type_id, bool) or type_id <= 0:
            continue
        turns = effect.get("turnsLeft")
        turns = turns if isinstance(turns, int) and not isinstance(turns, bool) else -1
        entry = folded.setdefault(type_id, [type_id, turns, 0])
        entry[1] = max(entry[1], turns)
        entry[2] += 1
    return list(folded.values())


def _actor(entity: dict[str, Any], side: str) -> dict[str, Any] | None:
    identity, type_id = entity.get("id"), entity.get("typeId")
    if not isinstance(identity, int) or not isinstance(type_id, int):
        return None
    health = entity.get("healthPct")
    row: dict[str, Any] = {
        "id": identity, "t": type_id, "s": side,
        "hp": round(float(health), 1) if isinstance(health, (int, float)) and not isinstance(health, bool) else None,
        "fx": _effects(entity),
    }
    if entity.get("dead") is True:
        row["d"] = 1
    form = entity.get("currentFormIndex")
    if isinstance(form, int) and form > 0:
        row["f"] = form
    if side == "a":
        cooldowns = [[skill["typeId"], skill.get("cooldown") if isinstance(skill.get("cooldown"), int) else -1]
                     for skill in entity.get("skills") or []
                     if isinstance(skill, dict) and isinstance(skill.get("typeId"), int)]
        if cooldowns:
            row["cd"] = cooldowns
    devoured = entity.get("devouredHeroId")
    if isinstance(devoured, int) and devoured >= 0:
        row["dv"] = devoured
    if entity.get("isHydraNeck") is True or entity.get("headState") == "exposed_neck":
        row["neck"] = 1
    return row


def battle_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Health, buffs/debuffs and skill cooldowns of every actor at one decision."""
    actors = []
    for key, side in (("heroes", "a"), ("bosses", "e")):
        for entity in state.get(key) or []:
            if isinstance(entity, dict):
                row = _actor(entity, side)
                if row is not None:
                    actors.append(row)
    return {"actors": actors}


# Battle stats in the agent's numericObservation order = the team view's StatKindId 1..8.
STAT_NAMES = ("Health", "Attack", "Defence", "Speed", "Resistance", "Accuracy", "CriticalChance", "CriticalDamage")


def opening_stats(state: dict[str, Any]) -> dict[str, list[float | None]]:
    """Each hero's battle stats at a decision (every bonus included), by hero type id."""
    result: dict[str, list[float | None]] = {}
    for hero in state.get("heroes") or []:
        if not isinstance(hero, dict) or not isinstance(hero.get("typeId"), int):
            continue
        observation = hero.get("numericObservation") if isinstance(hero.get("numericObservation"), dict) else {}
        raw = observation.get("statsRaw") if isinstance(observation.get("statsRaw"), dict) else {}
        values: list[float | None] = []
        for name in STAT_NAMES:
            try:
                values.append(round(int(raw[name]) / 2**32, 4))
            except (KeyError, TypeError, ValueError):
                values.append(None)
        if any(value is not None for value in values):
            result[str(hero["typeId"])] = values
    return result


USE_TRIGGERS = frozenset({"input", "team", "counter", "provoke", "activate", "effect", "passive", "other"})


def action_uses(raw: Any) -> list[dict[str, Any]]:
    """What one engine command set off (src/offline_runtime/command_breakdown.hpp), for the action log.

    Each skill use (the command's own, ally attacks, counterattacks, provoked
    attacks, activated skills, damaging passives) with the damage its user dealt
    to the other team; "other" is damage no skill use accounts for (damage over
    time, effects placed earlier), per dealer. Runs from before 1.1.1 have none.
    """
    uses = []
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, list) or len(entry) != 5:
            continue
        actor_id, skill_type_id, target_id, trigger, damage = entry
        if trigger not in USE_TRIGGERS or not isinstance(actor_id, int) or not isinstance(damage, (int, float)):
            continue
        uses.append({"actorId": actor_id, "skillTypeId": skill_type_id if isinstance(skill_type_id, int) else 0,
                     "targetId": target_id if isinstance(target_id, int) else -1, "trigger": trigger,
                     "damage": round(float(damage))})
    return uses


def run_path(folder: Path, index: int) -> Path:
    return folder / f"run-{int(index):02d}.json.gz"


def write_run(folder: Path, index: int, result: dict[str, Any]) -> None:
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_write_bytes(run_path(folder, index), gzip.compress(payload, compresslevel=6))


def read_run(folder: Path, index: int) -> dict[str, Any] | None:
    """A run file, compressed (1.1.1) or plain JSON (earlier reports)."""
    try:
        compressed = run_path(folder, index)
        if compressed.is_file():
            value = json.loads(gzip.decompress(compressed.read_bytes()).decode("utf-8"))
        else:
            value = json.loads((folder / f"run-{int(index):02d}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, EOFError):
        return None
    return value if isinstance(value, dict) else None

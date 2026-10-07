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


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# What is left of an absorbing buff (engine policy state, 1.1.3): the value now and
# the initial one. Shield and magma shield, golden armor, stone skin, life barrier.
ABSORB_FIELDS = (("shieldValue", "shieldInitial"), ("goldenArmorValue", "goldenArmorInitial"),
                 ("stoneSkinValue", "stoneSkinInitial"), ("cocoonValue", None))


def _absorb(effect: dict[str, Any]) -> dict[str, int] | None:
    """{"v": value left, "i": initial} or, for a hit counter shield, {"n": hits left, "i": initial hits}."""
    if _number(effect.get("hitShieldHits")):
        return {"n": int(effect["hitShieldHits"]),
                **({"i": int(effect["hitShieldInitial"])} if _number(effect.get("hitShieldInitial")) else {})}
    for value_key, initial_key in ABSORB_FIELDS:
        if _number(effect.get(value_key)):
            initial = effect.get(initial_key) if initial_key else None
            return {"v": round(effect[value_key]), **({"i": round(initial)} if _number(initial) else {})}
    return None


def _effects(entity: dict[str, Any]) -> list[list[Any]]:
    """[effectTypeId, turnsLeft, count(, absorb)] per effect type, stacks folded together."""
    folded: dict[int, list[Any]] = {}
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
        absorb = _absorb(effect)
        if absorb:
            if len(entry) == 3:
                entry.append({key: 0 for key in absorb})
            for key, value in absorb.items():
                entry[3][key] = entry[3].get(key, 0) + value
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
    if any(len(effect) > 3 for effect in row["fx"]):
        # Max health (buffs included), to set a shield against.
        raw = ((entity.get("numericObservation") or {}).get("statsRaw") or {}).get("Health")
        try:
            row["mh"] = round(int(raw) / 2**32)
        except (TypeError, ValueError):
            pass
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
        # A sixth item (1.1.3 engine): the actors the use's damage hit, when a
        # skill picks its own target (the Head of Mischief's steal).
        if not isinstance(entry, list) or len(entry) not in (5, 6):
            continue
        actor_id, skill_type_id, target_id, trigger, damage = entry[:5]
        if trigger not in USE_TRIGGERS or not isinstance(actor_id, int) or not isinstance(damage, (int, float)):
            continue
        use = {"actorId": actor_id, "skillTypeId": skill_type_id if isinstance(skill_type_id, int) else 0,
               "targetId": target_id if isinstance(target_id, int) else -1, "trigger": trigger,
               "damage": round(float(damage))}
        hits = entry[5] if len(entry) == 6 and isinstance(entry[5], list) else []
        if hits and all(isinstance(hit, int) and not isinstance(hit, bool) for hit in hits):
            use["hits"] = hits
        uses.append(use)
    return uses


MISS_FIELDS = ("heroTypeId", "skillTypeId", "target", "position", "reach")


def target_misses(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per rule: the decisions where its skill was ready but its target could not be chosen, with one example."""
    by_rule: dict[Any, dict[str, Any]] = {}
    for decision in decisions:
        seen: set[Any] = set()
        for miss in decision.get("targetMisses") or []:
            key = miss.get("ruleIndex") or miss.get("rule")
            if key in seen:
                continue
            seen.add(key)
            entry = by_rule.setdefault(key, {"ruleIndex": miss.get("ruleIndex"), "rule": miss.get("rule"), "count": 0,
                                             **{name: miss[name] for name in MISS_FIELDS if name in miss}})
            entry["count"] += 1
    return list(by_rule.values())


def attach_target_misses(rules: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> None:
    """Each rule's target misses across runs: per run on average, how many runs, and an example."""
    count = max(len(summaries), 1)
    totals: dict[int, dict[str, Any]] = {}
    for summary in summaries:
        for miss in summary.get("targetMisses") or []:
            if not isinstance(miss.get("ruleIndex"), int):
                continue
            entry = totals.setdefault(miss["ruleIndex"], {"total": 0, "runs": 0, "example": miss})
            entry["total"] += miss.get("count", 0)
            entry["runs"] += 1
    for rule in rules:
        entry = totals.get(rule.get("ruleIndex"))
        if entry:
            rule["targetMisses"] = round(entry["total"] / count, 2)
            rule["targetMissRuns"] = entry["runs"]
            rule["targetMiss"] = {name: entry["example"][name] for name in MISS_FIELDS if name in entry["example"]}


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

"""Why a Chimera trial did not move in a simulated run: its conditions at each moment it could have progressed.

A trial can progress only while it runs (from its start until it is completed) and the
Chimera is in its form. Its recipe (data/chimera-trial-recipes.json) says what the moments
are and what must hold then:

* damageBoss, periodicDamage: each action of the player's team that damaged the Chimera;
  applyDistinctEffects: each action of the player's team;
* absorbDamage, resistDebuffs: each champion the Chimera's own skill hit;
* survive: each Boss turn.

Each condition (an effect on the Chimera, on the champion acting or hit, on any ally; a
number of buffs or debuffs; living allies; a hit strong enough to kill for blocked damage)
is checked in the state before that action: the decision state of the player's actions,
and the effects the 1.1.6 engine writes before each Chimera action. These are facts of the
run, not advice; manual trials are not checked.
"""
from __future__ import annotations

from typing import Any

import chimera_controller as controller

PLAYER_GOALS = {"damageBoss", "periodicDamage", "applyDistinctEffects"}
BOSS_GOALS = {"absorbDamage", "resistDebuffs"}
TURN_GOALS = {"survive"}
COUNTS = (("minimumBossDebuffs", "boss", "debuff", "atLeast"), ("minimumActiveHeroBuffs", "actor", "buff", "atLeast"),
          ("maximumBossBuffs", "boss", "buff", "atMost"))


def trial_form(trial_id: int) -> int | None:
    """Ram 1, Lion 2, Snake 3 (the log rows' form numbers): ids run form, trial, level."""
    ordinal = (trial_id - 8_000_000) % 100
    return (ordinal - 1) // 9 + 1 if 1 <= ordinal <= 27 else None


def conditions(recipe: dict[str, Any], boss_moments: bool) -> list[dict[str, Any]]:
    """The recipe's checks; on a Chimera action "actor" is the champion it hit (target)."""
    checks: list[dict[str, Any]] = []
    for requirement in recipe.get("requirements") or []:
        if not isinstance(requirement, dict):
            continue
        ids = sorted({selector["effectTypeId"] for selector in controller.requirement_effect_selectors(requirement)
                      if isinstance(selector.get("effectTypeId"), int)})
        scope = requirement.get("scope")
        on = "boss" if scope == "boss" else "ally" if scope == "anyAlly" else "target" if boss_moments else "actor"
        if ids:
            checks.append({"kind": "effect", "on": on, "effectTypeIds": ids})
    for key, on, polarity, bound in COUNTS:
        value = recipe.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            checks.append({"kind": "count", "on": "target" if on == "actor" and boss_moments else on,
                           "polarity": polarity, "bound": bound, "count": value})
    forbidden = [value for value in recipe.get("forbiddenBossEffectTypeIds") or [] if isinstance(value, int)]
    if forbidden:
        checks.append({"kind": "forbidden", "on": "boss", "effectTypeIds": sorted(forbidden)})
    living = recipe.get("minimumLivingAllies")
    if isinstance(living, int) and not isinstance(living, bool):
        checks.append({"kind": "living", "on": "ally", "count": living})
    if "blockedDamage" in (recipe.get("requiredMechanics") or []) and boss_moments:
        # Unkillable stops only damage that would kill (Block Damage stops any): a hit that could not kill blocks nothing.
        checks.append({"kind": "lethal", "on": "target"})
    for index, check in enumerate(checks):
        check["key"] = f"{index}:{check['kind']}:{check['on']}"
    return checks


def _actors(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [actor for actor in snapshot.get("actors") or [] if isinstance(actor, dict) and not actor.get("d")]


def _count(actor: dict[str, Any] | None, polarity: str) -> int:
    if actor is None:
        return 0
    return sum(effect[2] if len(effect) > 2 and isinstance(effect[2], int) else 1
               for effect in actor.get("fx") or []
               if isinstance(effect, list) and effect and controller.effect_polarity({"effectTypeId": effect[0]}) == polarity)


def _has(actor: dict[str, Any] | None, ids: list[int]) -> bool:
    return actor is not None and any(isinstance(effect, list) and effect and effect[0] in ids for effect in actor.get("fx") or [])


def _holds(check: dict[str, Any], snapshot: dict[str, Any], who: int | None, lethal: bool) -> bool:
    actors = _actors(snapshot)
    boss = next((actor for actor in actors if actor.get("s") == "e"), None)
    allies = [actor for actor in actors if actor.get("s") == "a"]
    one = next((actor for actor in allies if actor.get("id") == who), None)
    subject = boss if check["on"] == "boss" else one
    if check["kind"] == "effect":
        return any(_has(actor, check["effectTypeIds"]) for actor in allies) if check["on"] == "ally" \
            else _has(subject, check["effectTypeIds"])
    if check["kind"] == "count":
        count = _count(subject, check["polarity"])
        return count >= check["count"] if check["bound"] == "atLeast" else count <= check["count"]
    if check["kind"] == "forbidden":
        return not _has(boss, check["effectTypeIds"])
    if check["kind"] == "living":
        return len(allies) >= check["count"]
    return lethal


def _hits(row: dict[str, Any], players: set[int]) -> list[tuple[int, bool]]:
    """The champions the Chimera's skills hit in one action (its own turn, counterattacks and skills it
    activates during the player's actions), and whether each one's damage could have killed."""
    amounts: dict[int, float] = {}
    for use in row.get("uses") or []:
        if use.get("actorId") in players or not isinstance(use.get("actorId"), int) or use["actorId"] < 0:
            continue
        hits = use.get("hits") or []
        for actor_id, amount in zip(hits, use.get("hitDamage") or [None] * len(hits)):
            amounts[actor_id] = amounts.get(actor_id, 0.0) + (amount if isinstance(amount, (int, float)) else 0.0)
    if not amounts and row.get("source") == "enemy" and isinstance(row.get("targetId"), int):
        amounts[row["targetId"]] = 0.0  # an engine before 1.1.3 names only the target
    health = {change["actorId"]: change for change in row.get("health") or []}
    result = []
    for actor_id, amount in amounts.items():
        if actor_id not in players:
            continue
        change = health.get(actor_id)
        # Enough to kill: as much damage as the health it met (an Unkillable hero keeps 1 HP).
        result.append((actor_id, change is not None and amount > 0 and amount >= change["before"]))
    return result


def diagnose_run(rows: list[dict[str, Any]], trial_ids: list[int], recipes: dict[int, dict[str, Any]],
                 players: set[int]) -> list[dict[str, Any]]:
    """Per trial: its moments in this run and how often each condition held (run_timeline rows)."""
    report = []
    for trial_id in trial_ids:
        recipe = controller.trial_recipe_for_id(recipes, trial_id)
        goal = recipe.get("actionGoal") if recipe else None
        entry: dict[str, Any] = {"trialId": trial_id, "goal": goal, "started": False, "moments": 0, "unknown": 0,
                                 "allMet": 0, "progressed": 0, "conditions": []}
        if recipe is None or goal not in PLAYER_GOALS | BOSS_GOALS | TURN_GOALS:
            entry["checked"] = False
            report.append(entry)
            continue
        entry["checked"] = True
        checks = conditions(recipe, goal in BOSS_GOALS)
        met = {check["key"]: 0 for check in checks}
        form = trial_form(trial_id)
        running = done = False
        seen_turns: set[int] = set()
        for row in rows:
            change = next((item for item in row.get("trials") or [] if item.get("trialId") == trial_id), None)
            if change and change.get("start"):
                running = True
                entry["started"] = True
                entry["startBossTurn"] = row.get("bossTurns")
            if not running or done:
                continue
            if change and ((change.get("after") or 0) > (change.get("before") or 0) or change.get("completed")):
                entry["progressed"] += 1  # actions that advanced it, whatever their kind
            if change and change.get("completed"):
                done = True
            if row.get("form") != form:
                continue
            enemy = row.get("source") == "enemy"
            if goal in PLAYER_GOALS:
                if enemy or (goal != "applyDistinctEffects" and not row.get("damage")):
                    continue
                moments = [(row.get("actorId"), False)]
            elif goal in BOSS_GOALS:
                moments = _hits(row, players)
            else:
                if row.get("bossTurns") in seen_turns:
                    continue
                seen_turns.add(row.get("bossTurns"))
                moments = [(None, False)]
            snapshot = row.get("state")
            for who, lethal in moments:
                entry["moments"] += 1
                if not isinstance(snapshot, dict):
                    entry["unknown"] += 1
                    continue
                held = [check for check in checks if _holds(check, snapshot, who, lethal)]
                for check in held:
                    met[check["key"]] += 1
                if len(held) == len(checks):
                    entry["allMet"] += 1
        entry["conditions"] = [{**check, "met": met[check["key"]]} for check in checks]
        report.append(entry)
    return report


def merge(diagnoses: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Across runs: per trial, the runs it started in and the summed moments and conditions."""
    merged: dict[int, dict[str, Any]] = {}
    for run in diagnoses:
        for entry in run or []:
            total = merged.setdefault(entry["trialId"], {
                "trialId": entry["trialId"], "goal": entry.get("goal"), "checked": entry.get("checked", False), "runs": 0,
                "startedRuns": 0, "moments": 0, "unknown": 0, "allMet": 0, "progressed": 0,
                "conditions": [{**check, "met": 0} for check in entry.get("conditions") or []]})
            total["runs"] += 1
            total["startedRuns"] += 1 if entry.get("started") else 0
            for key in ("moments", "unknown", "allMet", "progressed"):
                total[key] += entry.get(key) or 0
            for condition, check in zip(total["conditions"], entry.get("conditions") or []):
                condition["met"] += check.get("met") or 0
    return list(merged.values())

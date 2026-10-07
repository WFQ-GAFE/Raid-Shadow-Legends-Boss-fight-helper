"""Simulate a captured Hydra battle with the player's current strategy.

The isolated original engine (raid_offline_probe.exe forecast) plays the
battle the controller captured at a Hydra opening: same team, gear and heads,
with the captured seed (that exact battle) or with other seeds (the same team
facing different luck), up to the game's turn limit. Every player turn is
decided by the unchanged controller strategy code (HydraOfflinePolicySession).
The rules are followed strictly: a turn where they give no legal action ends
that run as "stuck" with the hero, its skills and every rule checked. Nothing
here opens the game or sends a command to it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import threading
import time
from typing import Any, Callable

import chimera_controller as controller
from hydra_forecast import ForecastError, _command_line, evaluate_conditions, mark_stream, swallow_stream
from hydra_offline_policy import HydraOfflinePolicySession
from simulation_common import (action_uses, attach_target_misses, battle_snapshot, decision_rule_index, opening_stats,
                               stuck_report, target_misses)


SCHEMA = 1
MAX_GAME_TURN = 1000
MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 330.0
# HydraOfflinePolicySession reasons that are the rules' doing (a live
# takeover would stall), not a broken input.
STUCK_REASONS = {
    "policy_returned_no_skill_command": "no_matching_rule",
    "policy_command_not_legal_in_supplied_state": "rule_command_not_legal",
    "decision_turn_not_monotonic": "no_progress",
}


class HydraSimulationSession(HydraOfflinePolicySession):
    """The forecast's policy session, plus the rule each action came from and stuck reports."""

    def decide(self, supplied_state: object) -> dict[str, Any]:
        self.last_state = None
        decision = super().decide(supplied_state)
        state = self.last_state or (supplied_state if isinstance(supplied_state, dict) else {})
        if state.get("_targetMisses"):
            decision["targetMisses"] = state["_targetMisses"]
        if decision.get("status") == "command":
            decision["ruleIndex"] = decision_rule_index(self.strategy, state, decision.get("rule"))
            if any(isinstance(entry, dict) and entry.get("outcome") == "reservation_released"
                   for entry in state.get("_decisionTrace") or []):
                decision["reservationReleased"] = True
            return decision
        reason = STUCK_REASONS.get(str(decision.get("reason")))
        if reason is not None:
            detail = None
            if reason == "no_matching_rule":
                try:
                    detail = controller.no_decision_diagnostic(self.strategy, state)
                except Exception:
                    detail = None
            hydra = state.get("hydra") if isinstance(state.get("hydra"), dict) else {}
            decision["stuck"] = stuck_report(self.strategy, state, reason, detail,
                                             hydraTurns=hydra.get("turnCount"))
        return decision


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_simulation(probe: Path, packed_input: Path, strategy: dict[str, Any], *,
                   seed: int | None = None,
                   capability_memory: controller.SkillCapabilityMemory | None = None,
                   team_selection: dict[str, Any] | None = None,
                   timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                   cancel: threading.Event | None = None,
                   on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One isolated original-engine Hydra battle driven by the strategy."""
    started = time.monotonic()
    result: dict[str, Any] = {"schema": SCHEMA, "type": "hydra_strategy_simulation", "status": "unknown",
                              "reason": None, "requestedSeed": seed, "decisions": [], "marks": []}
    for name in ("battle-setup.msgpack", "battle-settings.msgpack"):
        if not (packed_input / name).is_file():
            result["reason"] = f"input_missing:{name}"
            return result
    result["inputs"] = {"battleSetupSha256": _sha256(packed_input / "battle-setup.msgpack"),
                        "strategySha256": hashlib.sha256(json.dumps(
                            strategy, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
    try:
        session = HydraSimulationSession(
            strategy, capability_memory=copy.deepcopy(capability_memory) if capability_memory else None,
            team_selection=team_selection)
    except ValueError as error:
        result["reason"] = str(error)
        return result
    parameter = str(MAX_GAME_TURN) if seed is None else f"{MAX_GAME_TURN}:{int(seed)}"
    process = subprocess.Popen(
        [str(probe), "forecast", str(packed_input), parameter],
        cwd=str(probe.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stderr_chunks: list[bytes] = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True)
    drain.start()
    timed_out = threading.Event()

    def expire() -> None:
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(timeout_seconds, expire)
    watchdog.daemon = True
    watchdog.start()
    wrapper: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = result["decisions"]
    try:
        assert process.stdout is not None and process.stdin is not None
        while True:
            line = process.stdout.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST_BYTES:
                raise ForecastError("decision_request_exceeds_bound")
            if line.startswith(b'{"childExitCode"'):
                wrapper = json.loads(line + process.stdout.read(64 * 1024 * 1024))
                break
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("type") != "decision_request":
                raise ForecastError("unexpected_probe_output")
            sequence, state = message.get("sequence"), message.get("state")
            if type(sequence) is not int or not isinstance(state, dict):
                raise ForecastError("decision_request_invalid")
            if cancel is not None and cancel.is_set():
                raise ForecastError("simulation_cancelled")
            decision = session.decide(state)
            if "openingStats" not in result:
                result["openingStats"] = opening_stats(state)
            battle = state.get("battle", {})
            hydra = state.get("hydra") if isinstance(state.get("hydra"), dict) else {}
            command = decision.get("command") if isinstance(decision.get("command"), dict) else {}
            decisions.append({
                "sequence": sequence, "turn": battle.get("turn"), "playerTurnCount": battle.get("playerTurnCount"),
                "hydraTurns": hydra.get("turnCount"),
                "activeHeroId": state.get("activeHeroId"), "activeHeroTypeId": state.get("activeHeroTypeId"),
                "damage": battle.get("currentDamage"), "snapshot": battle_snapshot(state),
                "status": decision.get("status"), "reason": decision.get("reason"),
                "skillTypeId": command.get("skillTypeId"), "targetId": command.get("targetId"),
                "rule": decision.get("rule"), "ruleIndex": decision.get("ruleIndex"),
                **({"reservationReleased": True} if decision.get("reservationReleased") else {}),
                **({"targetMisses": decision["targetMisses"]} if decision.get("targetMisses") else {}),
                **({"stuck": decision["stuck"]} if isinstance(decision.get("stuck"), dict) else {})})
            if on_progress is not None and len(decisions) % 20 == 1:
                on_progress({"decisions": len(decisions), "turn": battle.get("turn")})
            process.stdin.write(_command_line(decision, sequence).encode("ascii"))
            process.stdin.flush()
    except Exception as error:  # A failed simulation is reported, never raised to the caller.
        process.kill()
        result["reason"] = (str(error) if isinstance(error, ForecastError)
                            else f"simulation_channel_failed:{type(error).__name__}")
    finally:
        watchdog.cancel()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        drain.join(timeout=5)
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    if timed_out.is_set():
        result["reason"] = "simulation_timeout"
        return result
    if result["reason"]:
        return result
    if wrapper is None:
        tail = b"".join(stderr_chunks)[-400:].decode("utf-8", "replace").strip()
        result["reason"] = "probe_report_missing" + (f":{tail}" if tail else "")
        return result
    observation = wrapper.get("observation")
    if (wrapper.get("childExitCode") != 0 or wrapper.get("timedOut") is not False
            or wrapper.get("nativeFault") is not None or not isinstance(observation, dict)
            or observation.get("appContainer") is not True
            or observation.get("phase") != "policy_forecast_executed"):
        result["reason"] = "isolated_engine_failed"
        result["engineFailure"] = {"childExitCode": wrapper.get("childExitCode"),
                                   "stage": observation.get("stage") if isinstance(observation, dict) else None,
                                   "error": observation.get("error") if isinstance(observation, dict) else None,
                                   "nativeFault": wrapper.get("nativeFault")}
        return result
    engine = {key: value for key, value in observation.items()
              if key not in ("schema", "pid", "appContainer", "capabilityCount", "parentMemoryAccessDenied",
                             "phase", "hungerEvents", "resultHungerEvents")}
    result["engine"] = engine
    try:
        result["marks"] = mark_stream(observation)
        result["swallows"] = swallow_stream(observation, result["marks"])
    except ForecastError as error:
        result["marksIssue"] = str(error)
    return _settle(result)


def _settle(result: dict[str, Any]) -> dict[str, Any]:
    """The run's status from how the engine stopped; a stop the rules caused is "stuck"."""
    engine = result.get("engine") or {}
    decisions = result.get("decisions") or []
    stop = engine.get("stopReason")
    result["status"] = "complete" if stop in ("battle_finished", "game_turn_limit") else "partial"
    if result["status"] != "complete":
        result["reason"] = stop
    last = decisions[-1] if decisions else None
    if stop == "policy_stopped" and isinstance(last, dict) and isinstance(last.get("stuck"), dict):
        result["status"] = "stuck"
        result["reason"] = last["stuck"].get("reason")
        result["stuck"] = last["stuck"]
    elif stop == "policy_command_rejected" and isinstance(last, dict):
        # The rule's command passed the state's legality data but the original
        # engine refused it: the live game would refuse it the same way.
        result["status"] = "stuck"
        result["reason"] = "engine_rejected_command"
        result["stuck"] = {"reason": "engine_rejected_command", "turn": last.get("turn"),
                           "hydraTurns": last.get("hydraTurns"), "activeHeroId": last.get("activeHeroId"),
                           "activeHeroTypeId": last.get("activeHeroTypeId"), "rule": last.get("rule"),
                           "ruleIndex": last.get("ruleIndex"), "skillTypeId": last.get("skillTypeId"),
                           "targetId": last.get("targetId"), "detail": engine.get("policyStop"),
                           "skills": [], "rules": [], "snapshot": last.get("snapshot")}
    return result


# The engine facts a live opening forecast keeps (hydra_forecast.run_forecast).
FORECAST_ENGINE_KEYS = ("battleSetupId", "seed", "stageId", "turn", "battleFinished", "stopReason", "resultType",
                        "finishCause", "commands", "policyStop", "effectiveMaxTurnsInBattle", "playerActors",
                        "actors", "hydraDamage")


def forecast_run(forecast: dict[str, Any]) -> dict[str, Any] | None:
    """A live opening forecast as one simulation run, for its report.

    Needs a forecast made with HydraSimulationSession decisions (rule numbers,
    stall reports); None when the engine never played the battle.
    """
    if not forecast.get("stopReason"):
        return None
    decisions = []
    for decision in forecast.get("decisions") or []:
        command = decision.get("command") if isinstance(decision.get("command"), dict) else {}
        decisions.append({key: value for key, value in decision.items() if key not in ("command", "rngBefore", "round")}
                         | {"skillTypeId": command.get("skillTypeId"), "targetId": command.get("targetId")})
    result: dict[str, Any] = {
        "schema": SCHEMA, "type": "hydra_strategy_simulation", "status": "unknown", "reason": None,
        "decisions": decisions, "marks": forecast.get("marks") or [], "swallows": forecast.get("swallows"),
        "engine": {**{key: forecast.get(key) for key in FORECAST_ENGINE_KEYS if key in forecast},
                   "turns": forecast.get("engineTurns") or []},
        "elapsedSeconds": forecast.get("elapsedSeconds"),
    }
    return _settle(result)


def battle_report(record_id: str, strategy: dict[str, Any], provenance: dict[str, Any], capture_id: str,
                  forecast: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The report summary of a live opening forecast and its one run (None without a played battle)."""
    run = forecast_run(forecast)
    if run is None:
        return None
    summary = summarize_run(run, strategy)
    summary["index"] = 1
    return {
        "schema": 1, "id": record_id, "kind": "battle", "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "complete",
        "strategy": {"id": strategy.get("id"), "name": strategy.get("name"), "rules": len(strategy.get("rules") or [])},
        "capture": {"id": capture_id, "stageId": provenance.get("stageId"), "seed": provenance.get("seed"),
                    "teamHeroTypeIds": provenance.get("teamHeroTypeIds"),
                    "difficulty": difficulty_for_stage(provenance.get("stageId"))},
        "runs": [summary], "aggregate": aggregate([summary], strategy)}, run


def difficulty_for_stage(stage_id: object) -> int | None:
    # Hydra stages end in the difficulty, e.g. 8039003 (1 Normal .. 4 Nightmare).
    return stage_id % 10 if isinstance(stage_id, int) and 8000000 < stage_id < 9000000 else None


def _player_rows(result: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Engine command rows with their damage, paired with the decision behind player rows."""
    engine = result.get("engine") or {}
    decisions = result.get("decisions") or []
    rows = []
    previous = 0
    player_index = 0
    for row in engine.get("turns") or []:
        damage_after = row.get("damageAfter")
        damage = damage_after - previous if isinstance(damage_after, (int, float)) else 0
        previous = damage_after if isinstance(damage_after, (int, float)) else previous
        decision: dict[str, Any] = {}
        if row.get("playerAction"):
            decision = decisions[player_index] if player_index < len(decisions) else {}
            player_index += 1
        rows.append(({**row, "damage": damage}, decision))
    return rows


def devour_verdict(result: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    """Whether the live opening forecast would regroup on this run (devour order or damage goal)."""
    objectives = strategy.get("objectives") if isinstance(strategy.get("objectives"), dict) else {}
    conditions = objectives.get("devourOrderRetryConditions") or []
    minimum = objectives.get("minimumDamage") or 0
    engine = result.get("engine") or {}
    forecast = {"status": "complete" if result.get("status") == "complete" else "incomplete",
                "reason": result.get("reason"), "marks": result.get("marks") or [],
                "swallows": result.get("swallows"),
                "hydraDamage": engine.get("hydraDamage"), "turn": engine.get("turn"),
                "effectiveMaxTurnsInBattle": engine.get("effectiveMaxTurnsInBattle"),
                "horizon": "battle_finished" if engine.get("stopReason") == "battle_finished" else "turn_limit"}
    return evaluate_conditions(conditions if isinstance(conditions, list) else [], forecast,
                               minimum if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) else 0)


def summarize_run(result: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    """Compact per-run facts the report shows and aggregates."""
    engine = result.get("engine") or {}
    actors = {actor["actorId"]: actor for actor in engine.get("actors", []) if isinstance(actor, dict)}
    rules: dict[str, dict[str, Any]] = {}
    deaths: list[dict[str, Any]] = []
    head_kills: dict[int, int] = {}
    for row, decision in _player_rows(result):
        if row.get("playerAction") and decision:
            key = str(decision.get("ruleIndex")) if decision.get("ruleIndex") is not None else f"name:{decision.get('rule')}"
            entry = rules.setdefault(key, {"ruleIndex": decision.get("ruleIndex"), "rule": decision.get("rule"),
                                           "uses": 0, "damage": 0.0, "skills": {}, "heroes": {}})
            entry["uses"] += 1
            entry["damage"] += float(row["damage"] or 0)
            entry["skills"][str(row.get("skillTypeId"))] = entry["skills"].get(str(row.get("skillTypeId")), 0) + 1
            entry["heroes"][str(row.get("actorTypeId"))] = entry["heroes"].get(str(row.get("actorTypeId")), 0) + 1
        for actor_id in row.get("deaths") or []:
            actor = actors.get(actor_id) if actor_id >= 0 else None
            if actor is None:
                continue
            if actor.get("player"):
                deaths.append({"actorId": actor_id, "heroTypeId": actor.get("heroTypeId"), "turn": row.get("turnBefore")})
            else:
                head_kills[actor.get("heroTypeId")] = head_kills.get(actor.get("heroTypeId"), 0) + 1
    decisions = result.get("decisions") or []
    verdict = devour_verdict(result, strategy)
    swallows = result.get("swallows")
    swallowed_marks = ({item.get("markIndex") for item in swallows if isinstance(item, dict)}
                       if isinstance(swallows, list) else None)
    return {
        "seed": engine.get("seed"), "capturedSeed": engine.get("capturedSeed"),
        "exact": not engine.get("seedOverridden", False),
        "status": result.get("status"), "reason": result.get("reason"),
        "turn": engine.get("turn"), "hydraTurns": next((item.get("hydraTurns") for item in reversed(decisions)
                                                       if isinstance(item.get("hydraTurns"), int)), None),
        "damage": engine.get("hydraDamage"), "commands": engine.get("commands"),
        "stuck": result.get("stuck"),
        "reservationReleases": sum(1 for decision in decisions if decision.get("reservationReleased")),
        "targetMisses": target_misses(decisions),
        "marks": [{"markIndex": mark.get("markIndex"), "heroTypeId": mark.get("heroTypeId"),
                   "applyTurn": mark.get("applyTurn"),
                   # Whether this mark ended in a swallow (None: no swallow data).
                   "swallowed": (mark.get("markIndex") in swallowed_marks) if swallowed_marks is not None else None}
                  for mark in result.get("marks") or []],
        "deaths": deaths,
        "headKills": {str(type_id): count for type_id, count in head_kills.items()},
        "rules": sorted(rules.values(), key=lambda item: (item["ruleIndex"] is None, item["ruleIndex"] or 0)),
        "verdict": {key: verdict.get(key) for key in ("verdict", "reason", "violations", "damage")},
        "elapsedSeconds": result.get("elapsedSeconds"),
    }


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def aggregate(summaries: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
    """Across runs: damage, devour marks, deaths, head kills, rules, stuck runs, regroups."""
    finished = [item for item in summaries if item.get("status") in ("complete", "partial", "stuck")]
    battle_ends = [item for item in finished if item.get("status") == "complete"]
    objectives = strategy.get("objectives") if isinstance(strategy.get("objectives"), dict) else {}
    minimum = objectives.get("minimumDamage") if isinstance(objectives.get("minimumDamage"), (int, float)) else 0
    damage = [float(item.get("damage") or 0) for item in battle_ends]
    marks: dict[int, dict[str, Any]] = {}
    for item in finished:
        seen: set[int] = set()
        for mark in item.get("marks", []):
            type_id = mark.get("heroTypeId")
            if not isinstance(type_id, int):
                continue
            entry = marks.setdefault(type_id, {"heroTypeId": type_id, "runs": 0, "marks": 0, "firstTurns": []})
            entry["marks"] += 1
            if type_id not in seen:
                seen.add(type_id)
                entry["runs"] += 1
                if isinstance(mark.get("applyTurn"), int):
                    entry["firstTurns"].append(mark["applyTurn"])
    deaths: dict[int, list[int]] = {}
    for item in finished:
        seen = set()
        for death in item.get("deaths", []):
            type_id = death.get("heroTypeId")
            if isinstance(type_id, int) and type_id not in seen:
                seen.add(type_id)
                deaths.setdefault(type_id, []).append(death.get("turn") or 0)
    rules: dict[str, dict[str, Any]] = {}
    total_damage = 0.0
    for item in finished:
        for rule in item.get("rules", []):
            key = f"index:{rule['ruleIndex']}" if rule.get("ruleIndex") is not None else f"name:{rule.get('rule')}"
            entry = rules.setdefault(key, {"ruleIndex": rule.get("ruleIndex"), "rule": rule.get("rule"),
                                           "runsUsed": 0, "uses": 0, "damage": 0.0})
            entry["runsUsed"] += 1
            entry["uses"] += rule.get("uses", 0)
            entry["damage"] += rule.get("damage", 0.0)
            total_damage += rule.get("damage", 0.0)
    count = max(len(finished), 1)
    rule_list = []
    configured = strategy.get("rules") if isinstance(strategy.get("rules"), list) else []
    for index, rule in enumerate(configured, 1):
        entry = rules.pop(f"index:{index}", None) or {"ruleIndex": index, "runsUsed": 0, "uses": 0, "damage": 0.0}
        entry["rule"] = str(rule.get("name") or f"规则 {index}") if isinstance(rule, dict) else f"规则 {index}"
        rule_list.append(entry)
    rule_list.extend(rules.values())
    for entry in rule_list:
        entry["usesPerRun"] = round(entry["uses"] / count, 2)
        entry["damageShare"] = round(entry["damage"] / total_damage, 4) if total_damage else 0.0
        entry["trialGains"] = {}
        entry.pop("damage", None)
    attach_target_misses(rule_list, finished)
    stuck_runs = [{"index": item.get("index"), "seed": item.get("seed"), "exact": item.get("exact"),
                   **{key: item["stuck"].get(key) for key in ("reason", "turn", "hydraTurns", "activeHeroTypeId", "rule")}}
                  for item in finished if item.get("status") == "stuck" and isinstance(item.get("stuck"), dict)]
    retries = [item for item in finished if (item.get("verdict") or {}).get("verdict") == "retry"]
    kills = [sum(item.get("headKills", {}).values()) for item in battle_ends]
    return {
        "runs": len(summaries), "finishedRuns": len(finished), "battleEndRuns": len(battle_ends),
        "minimumDamage": minimum,
        "minimumDamageRuns": sum(1 for value in damage if value >= minimum) if minimum else None,
        "damage": {"min": min(damage, default=0.0), "median": _median(damage), "max": max(damage, default=0.0)},
        "marks": [{"heroTypeId": entry["heroTypeId"], "runs": entry["runs"],
                   "marksPerRun": round(entry["marks"] / count, 2),
                   "firstTurnMedian": _median([float(turn) for turn in entry["firstTurns"]])}
                  for entry in sorted(marks.values(), key=lambda entry: -entry["runs"])],
        "marksPerRunMedian": _median([float(len(item.get("marks", []))) for item in battle_ends]),
        "headKillsPerRunMedian": _median([float(value) for value in kills]),
        "deaths": [{"heroTypeId": type_id, "runs": len(turns), "firstTurnMedian": _median([float(t) for t in turns])}
                   for type_id, turns in sorted(deaths.items(), key=lambda pair: -len(pair[1]))],
        "rules": rule_list,
        "stuckRuns": stuck_runs,
        "reservationReleasesPerRun": round(sum(item.get("reservationReleases") or 0 for item in finished) / count, 2),
        "devourConditions": len(objectives.get("devourOrderRetryConditions") or []),
        "regroupRuns": len(retries),
        "regroupCauses": {
            "damage": sum(1 for item in retries if any(v.get("cause") == "damage" for v in item["verdict"].get("violations") or [])),
            "devour": sum(1 for item in retries if any(v.get("cause") != "damage" for v in item["verdict"].get("violations") or [])),
        },
    }


def run_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every action of one run with its rule, damage, deaths, marks and the state before it."""
    marks_by_command: dict[int, list[int]] = {}
    for mark in result.get("marks") or []:
        if isinstance(mark.get("commandIndex"), int):
            marks_by_command.setdefault(mark["commandIndex"], []).append(mark.get("actorId"))
    rows = []
    hydra_turns = 0
    for index, (row, decision) in enumerate(_player_rows(result)):
        if isinstance(decision.get("hydraTurns"), int):
            hydra_turns = decision["hydraTurns"]
        rows.append({
            "turn": row.get("turnBefore"), "hydraTurns": hydra_turns,
            "actorId": row.get("actorId"), "actorTypeId": row.get("actorTypeId"),
            "source": "policy" if row.get("playerAction") else "enemy",
            "skillTypeId": row.get("skillTypeId"), "targetId": row.get("targetId"),
            "damage": round(float(row.get("damage") or 0)), "deaths": row.get("deaths") or [],
            "uses": action_uses(row.get("uses")), "marked": marks_by_command.get(index, []),
            "rule": decision.get("rule"), "ruleIndex": decision.get("ruleIndex"),
            "reservationReleased": decision.get("reservationReleased") is True,
            **({"targetMisses": decision["targetMisses"]} if decision.get("targetMisses") else {}),
            **({"state": decision["snapshot"]} if isinstance(decision.get("snapshot"), dict) else {}),
        })
    return rows

"""Forecast a Hydra battle's devour-mark order from its captured opening.

The original engine runs in the probe's zero-capability AppContainer from the
battle's own BattleSetup/BattleSettings. At every player command window it
asks this process for a command; the answer comes from the unchanged list
policy (``chimera_controller.evaluate``) through ``HydraOfflinePolicySession``.
Enemy turns use the original enemy AI. Nothing here opens the game process.

A forecast is only a forecast: it stays valid while the live battle follows
the same commands. Every failure is reported as ``unknown``; an ``unknown``
forecast must never trigger a regroup.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

import chimera_controller as controller
from hydra_offline_policy import HydraOfflinePolicySession


SCHEMA = 1
MAX_GAME_TURN = 1000
MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 330.0
# Condition keys whose live inputs the original model does not reproduce.
UNSUPPORTED_CONDITION_KEYS = frozenset({"currentDamageAtLeast", "currentDamageBelow"})
UNSUPPORTED_ACTION_TYPES = frozenset({"executeTrialRecipe"})


class ForecastError(RuntimeError):
    """A stable reason for an unknown forecast."""


def strategy_forecast_issue(strategy: dict[str, Any]) -> str | None:
    """Reject strategies whose decisions depend on inputs the engine lacks."""
    if not isinstance(strategy, dict) or strategy.get("bossMode", "hydra") != "hydra":
        return "not_a_hydra_strategy"
    if isinstance(strategy.get("strategyTree"), dict):
        return "strategy_tree_not_supported"
    rules = strategy.get("rules")
    if not isinstance(rules, list) or not rules:
        return "strategy_has_no_rules"
    stack: list[Any] = [rules]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in UNSUPPORTED_CONDITION_KEYS:
                    return f"condition_not_forecastable:{key}"
                if key == "type" and value in UNSUPPORTED_ACTION_TYPES:
                    return f"action_not_forecastable:{value}"
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def mark_stream(report: dict[str, Any]) -> list[dict[str, Any]]:
    """HungerCounter application events, deduplicated and cross-checked.

    The processor result stream is authoritative for order. The independent
    post-command state scan must be an ordered subset of it: a scanned mark the
    results lack means incomplete event coverage and the stream is refused. A
    result event the scan lacks is a mark removed within the same command
    (for example the battle ended or the target died); it still counts.
    """
    actors = {item.get("actorId"): item for item in report.get("playerActors", [])
              if isinstance(item, dict)}
    events: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for event in report.get("resultHungerEvents", []):
        key = (event.get("actorId"), event.get("effectId"), event.get("applyTurn"))
        if key in seen:
            continue
        seen.add(key)
        actor = actors.get(event.get("actorId"))
        if actor is None:
            raise ForecastError("mark_target_not_a_player_actor")
        events.append({
            "markIndex": len(events) + 1,
            "applyTurn": event.get("applyTurn"),
            "actorId": event.get("actorId"),
            "heroTypeId": actor.get("heroTypeId"),
            "inventoryHeroId": actor.get("inventoryHeroId"),
            "appliedEffectId": event.get("effectId"),
            "commandIndex": event.get("commandIndex"),
        })
    scanned = [(item.get("actorId"), item.get("appliedEffectId"), item.get("applyTurn"))
               for item in report.get("hungerEvents", [])]
    keys = [(item["actorId"], item["appliedEffectId"], item["applyTurn"]) for item in events]
    position = 0
    for key in scanned:
        while position < len(keys) and keys[position] != key:
            position += 1
        if position == len(keys):
            raise ForecastError("mark_event_streams_disagree")
        position += 1
    scanned_keys = set(scanned)
    for item, key in zip(events, keys):
        item["seenAfterCommand"] = key in scanned_keys
    return events


def evaluate_conditions(conditions: list[dict[str, Any]], forecast: dict[str, Any],
                        minimum_damage: float = 0) -> dict[str, Any]:
    """Apply devourOrderRetryConditions and the damage goal to a complete forecast.

    ``retry`` needs a violation inside the forecast; ``unknown`` never
    regroups. Marks are counted per application event, including the opening
    mark and a re-mark after the marked hero died. The damage goal compares
    the battle's final Hydra damage (the UI counter's definition) with
    ``minimum_damage``.
    """
    if forecast.get("status") != "complete":
        return {"verdict": "unknown", "reason": forecast.get("reason") or "forecast_incomplete",
                "violations": [], "unresolved": []}
    marks = forecast.get("marks", [])
    violations: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            continue
        mark_index = condition.get("markIndex")
        expected = [value for value in condition.get("heroTypeIds", [])
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0]
        relation = condition.get("relation")
        if relation == "neverMarked":
            limit = condition.get("markLimit")
            limit = limit if type(limit) is int and 1 <= limit <= 100 else None
            considered = marks if limit is None else marks[:limit]
            hit = next((mark for mark in considered if mark["heroTypeId"] in expected), None)
            if hit is not None:
                violations.append({"conditionIndex": index, "markIndex": hit["markIndex"],
                                   "relation": "neverMarked", "markLimit": limit,
                                   "expectedHeroTypeIds": expected,
                                   "actualHeroTypeId": hit["heroTypeId"],
                                   "actualActorId": hit["actorId"],
                                   "applyTurn": hit["applyTurn"]})
            elif ((limit is None or len(marks) < limit)
                  and forecast.get("horizon") != "battle_finished"
                  and isinstance(forecast.get("effectiveMaxTurnsInBattle"), int)
                  and isinstance(forecast.get("turn"), int)
                  and forecast["turn"] < forecast["effectiveMaxTurnsInBattle"]):
                # The battle could continue beyond the simulated turns.
                unresolved.append({"conditionIndex": index, "markLimit": limit,
                                   "reason": "forecast_shorter_than_battle"})
            continue
        relation = "isAnyOf" if relation == "isAnyOf" else "isNoneOf"
        if not isinstance(mark_index, int) or isinstance(mark_index, bool) or mark_index < 1:
            continue
        if len(marks) < mark_index:
            unresolved.append({"conditionIndex": index, "markIndex": mark_index,
                               "reason": "mark_not_reached_in_forecast"})
            continue
        actual = marks[mark_index - 1]
        violated = (actual["heroTypeId"] not in expected if relation == "isAnyOf"
                    else actual["heroTypeId"] in expected)
        if violated:
            violations.append({"conditionIndex": index, "markIndex": mark_index,
                               "relation": relation, "expectedHeroTypeIds": expected,
                               "actualHeroTypeId": actual["heroTypeId"],
                               "actualActorId": actual["actorId"],
                               "applyTurn": actual["applyTurn"]})
    damage = None
    if isinstance(minimum_damage, (int, float)) and not isinstance(minimum_damage, bool) \
            and minimum_damage > 0:
        predicted = forecast.get("hydraDamage")
        if not isinstance(predicted, int) or isinstance(predicted, bool):
            unresolved.append({"conditionIndex": None, "reason": "predicted_damage_unavailable"})
        else:
            damage = {"predicted": predicted, "minimum": minimum_damage,
                      "met": predicted >= minimum_damage}
            if not damage["met"]:
                violations.append({"conditionIndex": None, "cause": "damage",
                                   "predictedDamage": predicted, "minimumDamage": minimum_damage,
                                   "applyTurn": forecast.get("turn")})
    return {"verdict": "retry" if violations else "continue", "reason": None,
            "violations": violations, "unresolved": unresolved, "damage": damage}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_line(decision: dict[str, Any], sequence: int) -> str:
    if decision.get("status") != "command":
        reason = str(decision.get("reason") or "policy_unknown")
        cleaned = "".join(c if (c.isascii() and (c.isalnum() or c in "_:")) else "_" for c in reason)
        return f"stop\t{sequence}\t{cleaned[:128].lower() or 'policy_unknown'}\n"
    command = decision["command"]
    return (f"command\t{sequence}\t{decision['activeHeroId']}\t"
            f"{command['skillTypeId']}\t{command['targetId']}\n")


def run_forecast(probe: Path, packed_input: Path, strategy: dict[str, Any], *,
                 team_selection: dict[str, Any] | None = None,
                 capability_memory: controller.SkillCapabilityMemory | None = None,
                 max_game_turn: int = MAX_GAME_TURN,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 on_decision: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
                 cancel: threading.Event | None = None,
                 ) -> dict[str, Any]:
    """Run one isolated original-engine battle driven by the list policy."""
    started = time.monotonic()
    result: dict[str, Any] = {
        "schema": SCHEMA, "type": "hydra_devour_forecast", "status": "unknown",
        "reason": None, "maxGameTurn": max_game_turn, "marks": [], "decisions": [],
        "futurePredictionVerified": False,
    }
    issue = strategy_forecast_issue(strategy)
    if issue:
        result["reason"] = issue
        return result
    if not 1 <= max_game_turn <= MAX_GAME_TURN:
        raise ValueError("max_game_turn must be between 1 and 1000")
    for name in ("battle-setup.msgpack", "battle-settings.msgpack"):
        if not (packed_input / name).is_file():
            result["reason"] = f"input_missing:{name}"
            return result
    result["inputs"] = {
        "battleSetupSha256": _sha256(packed_input / "battle-setup.msgpack"),
        "battleSettingsSha256": _sha256(packed_input / "battle-settings.msgpack"),
        "strategySha256": hashlib.sha256(json.dumps(
            strategy, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    session = HydraOfflinePolicySession(
        strategy, require_rng=False,
        capability_memory=capability_memory or controller.SkillCapabilityMemory(),
        team_selection=team_selection)
    process = subprocess.Popen(
        [str(probe), "forecast", str(packed_input), str(max_game_turn)],
        cwd=str(probe.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
                # The launcher's final wrapper follows the worker's exit.
                wrapper = json.loads(line + process.stdout.read(MAX_REQUEST_BYTES))
                if not isinstance(wrapper, dict):
                    raise ForecastError("probe_report_invalid")
                break
            message = json.loads(line)
            if isinstance(message, dict) and message.get("type") == "decision_request":
                sequence = message.get("sequence")
                state = message.get("state")
                if type(sequence) is not int or not isinstance(state, dict):
                    raise ForecastError("decision_request_invalid")
                if cancel is not None and cancel.is_set():
                    raise ForecastError("forecast_cancelled")
                decision = session.decide(state)
                battle = state.get("battle", {})
                rng = state.get("battleRandom", {})
                record = {
                    "sequence": sequence, "turn": battle.get("turn"),
                    "round": battle.get("round"),
                    "playerTurnCount": battle.get("playerTurnCount"),
                    "activeHeroId": state.get("activeHeroId"),
                    "activeHeroTypeId": state.get("activeHeroTypeId"),
                    "damage": battle.get("currentDamage"),
                    "rngBefore": rng.get("words") if isinstance(rng, dict) else None,
                    "status": decision.get("status"),
                    "reason": decision.get("reason"),
                    "command": decision.get("command"),
                    "rule": decision.get("rule"),
                }
                decisions.append(record)
                if on_decision is not None:
                    on_decision(state, record)
                process.stdin.write(_command_line(decision, sequence).encode("ascii"))
                process.stdin.flush()
            elif isinstance(message, dict) and "childExitCode" in message:
                wrapper = message
            else:
                raise ForecastError("unexpected_probe_output")
    except Exception as error:  # Any failure is an unknown forecast, never a crash.
        process.kill()
        result["reason"] = (str(error) if isinstance(error, ForecastError)
                            else f"forecast_channel_failed:{type(error).__name__}")
    finally:
        watchdog.cancel()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        drain.join(timeout=5)
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    result["policyDecisions"] = len(decisions)
    if timed_out.is_set():
        result["reason"] = "forecast_timeout"
        return result
    if result["reason"]:
        return result
    if wrapper is None:
        tail = b"".join(stderr_chunks)[-400:].decode("utf-8", "replace").strip()
        result["reason"] = "probe_report_missing" + (f":{tail}" if tail else "")
        return result
    observation = wrapper.get("observation")
    result["probe"] = {key: wrapper.get(key) for key in
                       ("childExitCode", "timedOut", "elapsedMs", "nativeFault")}
    if (wrapper.get("childExitCode") != 0 or wrapper.get("timedOut") is not False
            or wrapper.get("nativeFault") is not None or not isinstance(observation, dict)
            or observation.get("appContainer") is not True
            or observation.get("capabilityCount") != 0
            or observation.get("parentMemoryAccessDenied") is not True):
        result["reason"] = "isolated_engine_failed"
        if isinstance(observation, dict):
            result["probe"]["phase"] = observation.get("phase")
            result["probe"]["stage"] = observation.get("stage")
            result["probe"]["error"] = observation.get("error")
        return result
    if observation.get("phase") != "policy_forecast_executed" or observation.get("policyDriven") is not True:
        result["reason"] = "isolated_engine_not_policy_driven"
        return result
    for key in ("battleSetupId", "seed", "stageId", "turn", "battleFinished", "stopReason",
                "resultType", "finishCause", "commands", "policyRequests", "policyCommands",
                "policyStop", "effectiveMaxTurnsInBattle", "playerActors",
                "playerCooldownHypothesis", "enemyCooldownHypothesis", "hydraDamage"):
        result[key] = observation.get(key)
    result["engineTurns"] = observation.get("turns", [])
    stop = observation.get("stopReason")
    if stop not in ("battle_finished", "game_turn_limit"):
        policy_stop = observation.get("policyStop") or {}
        last = decisions[-1] if decisions else {}
        result["reason"] = (f"{stop}:{policy_stop.get('detail') or last.get('reason') or ''}"
                            if stop else "engine_stop_unknown")
        return result
    try:
        result["marks"] = mark_stream(observation)
    except ForecastError as error:
        result["reason"] = str(error)
        return result
    result["status"] = "complete"
    result["horizon"] = "battle_finished" if stop == "battle_finished" else "turn_limit"
    return result


def opening_parity(forecast: dict[str, Any], live_state: dict[str, Any]) -> str | None:
    """Bind a forecast to the live battle's first player command window.

    The live decision before any player command must show the same Setup ID,
    seed, turn, active hero and all four RNG words as the forecast's first
    request, and the same marked hero as the forecast's opening mark.
    """
    decisions = forecast.get("decisions") or []
    if not decisions:
        return "forecast_has_no_decisions"
    first = decisions[0]
    rng = live_state.get("battleRandom")
    battle = live_state.get("battle", {})
    if not isinstance(rng, dict) or rng.get("available") is not True:
        return "live_rng_unavailable"
    if rng.get("battleSetupId") != forecast.get("battleSetupId") or rng.get("seed") != forecast.get("seed"):
        return "live_battle_identity_differs"
    if (battle.get("turn") != first.get("turn")
            or battle.get("playerTurnCount") != first.get("playerTurnCount")
            or live_state.get("activeHeroId") != first.get("activeHeroId")
            or live_state.get("activeHeroTypeId") != first.get("activeHeroTypeId")):
        return "live_opening_window_differs"
    if rng.get("words") != first.get("rngBefore"):
        return "live_opening_rng_differs"
    marks = forecast.get("marks") or []
    live_marked = controller.hydra_marked_target(live_state)
    if marks and marks[0].get("applyTurn") == 0:
        if not isinstance(live_marked, dict) or live_marked.get("id") != marks[0]["actorId"]:
            return "live_opening_mark_differs"
    return None


def trace_divergence(forecast: dict[str, Any], live_state: dict[str, Any]) -> str | None:
    """Compare a later live player window with the forecast trace."""
    battle = live_state.get("battle", {})
    turn = battle.get("turn")
    rng = live_state.get("battleRandom")
    entry = next((item for item in forecast.get("decisions", []) if item.get("turn") == turn), None)
    if entry is None:
        return f"live_turn_{turn}_absent_from_forecast"
    if (live_state.get("activeHeroId") != entry.get("activeHeroId")
            or battle.get("playerTurnCount") != entry.get("playerTurnCount")):
        return f"live_turn_{turn}_actor_differs"
    if (isinstance(rng, dict) and rng.get("available") is True
            and isinstance(rng.get("words"), list) and rng["words"] != entry.get("rngBefore")):
        return f"live_turn_{turn}_rng_differs"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True,
                        help="directory with battle-setup.msgpack and battle-settings.msgpack")
    parser.add_argument("--strategy", type=Path, required=True,
                        help="Hydra strategy snapshot (object or export with a 'strategy' key)")
    parser.add_argument("--team-provenance", type=Path,
                        help="capture-provenance.json with teamHeroTypeIds/teamHeroIds")
    parser.add_argument("--capability-cache", type=Path)
    parser.add_argument("--max-game-turn", type=int, default=MAX_GAME_TURN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    strategy = json.loads(args.strategy.read_text(encoding="utf-8"))
    if isinstance(strategy.get("strategy"), dict):
        strategy = strategy["strategy"]
    team = None
    if args.team_provenance:
        provenance = json.loads(args.team_provenance.read_text(encoding="utf-8"))
        team = {"heroTypeIds": provenance["teamHeroTypeIds"], "heroIds": provenance["teamHeroIds"]}
    memory = (controller.SkillCapabilityMemory.load(args.capability_cache)
              if args.capability_cache else controller.SkillCapabilityMemory())
    forecast = run_forecast(args.probe.resolve(), args.input.resolve(), strategy,
                            team_selection=team, capability_memory=memory,
                            max_game_turn=args.max_game_turn)
    objectives = strategy.get("objectives", {}) if isinstance(strategy, dict) else {}
    forecast["conditionEvaluation"] = evaluate_conditions(
        copy.deepcopy(objectives.get("devourOrderRetryConditions", [])), forecast,
        objectives.get("minimumDamage", 0))
    body = json.dumps(forecast, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    summary = {key: forecast.get(key) for key in
               ("status", "reason", "horizon", "turn", "stopReason", "battleFinished",
                "policyDecisions", "elapsedSeconds")}
    summary["marks"] = [(m["markIndex"], m["applyTurn"], m["actorId"], m["heroTypeId"])
                        for m in forecast.get("marks", [])]
    summary["conditionEvaluation"] = forecast["conditionEvaluation"]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

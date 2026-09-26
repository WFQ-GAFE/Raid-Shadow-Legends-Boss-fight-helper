"""Simulate a captured Chimera battle with the player's current strategy.

The isolated original engine (raid_offline_probe.exe chimera-forecast) plays
the battle that the controller captured at a Chimera opening: same team, gear
and Chimera, either with the captured seed (that exact battle) or with other
seeds (the same team facing different luck). Every player turn is decided by
the unchanged controller strategy code on a decision state built from the
engine. The rules are followed strictly: a turn where they produce no legal
action (the live takeover would stall there) ends that run and is recorded
as "stuck" with the hero, its skills and every rule checked. Nothing here
opens the game or sends a command to it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

import chimera_controller as controller


SCHEMA = 1
MAX_REQUEST_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 330.0
FIXED_ONE = 4294967296.0
# One engine action row (see src/offline_runtime/chimera_forecast.hpp).
ACTION_FIELDS = ("turn", "bossTurns", "form", "actorId", "actorTypeId", "source", "skillTypeId",
                 "targetId", "damage", "trials", "deaths", "autoReason", "rng")
FORM_INTERVAL = 5
# Decisions for the same actor on the same battle turn before a run counts as
# not progressing (e.g. rules switching a mythic form back and forth).
MAX_SAME_TURN_DECISIONS = 6


class SimulationError(RuntimeError):
    """A stable reason why a simulation could not produce a result."""


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _selected_rule(trace: object) -> tuple[int | None, str | None]:
    for entry in trace if isinstance(trace, list) else []:
        if isinstance(entry, dict) and entry.get("outcome") == "selected":
            index = entry.get("index")
            return (index if isinstance(index, int) else None), entry.get("name")
    return None, None


def _rule_index_by_name(strategy: dict[str, Any], decided: str | None) -> int | None:
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


class ChimeraOfflinePolicySession:
    """Runs the live controller's per-turn steps on offline decision states.

    Mirrors process_state: team slots, capability learning, the trial planner
    runtime, the form first-turn marker, a pending mythic follow-up, then the
    list rules; after each command the post-turn bookkeeping on the next state.
    """

    def __init__(self, strategy: dict[str, Any], *,
                 capability_memory: controller.SkillCapabilityMemory | None = None,
                 static_state: dict[str, Any] | None = None,
                 team_selection: dict[str, Any] | None = None,
                 start_selection: dict[str, Any] | None = None):
        if not isinstance(strategy, dict):
            raise ValueError("A Chimera strategy snapshot is required")
        controller.require_list_execution(strategy)
        self.strategy = copy.deepcopy(strategy)
        self.capability_memory = capability_memory or controller.SkillCapabilityMemory()
        self.static_state = copy.deepcopy(static_state or {})
        self.team_selection = copy.deepcopy(team_selection) if team_selection else None
        self.start_selection = copy.deepcopy(start_selection) if start_selection else None
        self.runtime_state: dict[str, Any] = {}
        self.previous: tuple[dict[str, Any], controller.Decision] | None = None
        self.objective_events: list[dict[str, Any]] = []
        self._objective_seen: set[str] = set()
        self._turn_marker: tuple[Any, Any] | None = None
        self._same_turn_decisions = 0

    def _prepare(self, supplied: dict[str, Any]) -> dict[str, Any]:
        state = copy.deepcopy(supplied)
        for key, value in self.static_state.items():
            state.setdefault(key, copy.deepcopy(value))
        if self.start_selection is not None:
            state.setdefault("chimeraStartSelection", copy.deepcopy(self.start_selection))
        return state

    def _after_previous_command(self, advanced: dict[str, Any]) -> None:
        """Bookkeeping process_state does once the submitted turn has advanced."""
        if self.previous is None:
            return
        before, decision = self.previous
        self.previous = None
        skill_type_id = decision.skill.get("typeId")
        if isinstance(decision.trial_id, int) and decision.trial_id > 0:
            before_trial = controller.trial_status_by_id(before).get(decision.trial_id, {})
            after_trial = controller.trial_status_by_id(advanced).get(decision.trial_id, {})
            before_current, _ = controller.trial_progress_values(before_trial)
            after_current, _ = controller.trial_progress_values(after_trial)
            progressed = bool(before_current is not None and after_current is not None
                              and after_current > before_current) or bool(
                before_trial.get("completed") is not True and after_trial.get("completed") is True)
            if progressed and isinstance(skill_type_id, int):
                controller.remember_trial_contributor(self.runtime_state, decision.trial_id, skill_type_id)
        self.capability_memory.observe_state(advanced)
        if isinstance(skill_type_id, int) and not isinstance(skill_type_id, bool):
            self.capability_memory.observe_action_damage(skill_type_id, before, advanced,
                                                         trial_id=decision.trial_id)

    def _record_objectives(self, state: dict[str, Any]) -> None:
        """Moments where the live controller would regroup instead of playing on."""
        chimera = state.get("chimera") if isinstance(state.get("chimera"), dict) else {}
        boss_turn = chimera.get("turnCount")
        report = controller.evaluate_objectives(self.strategy, state)
        if report.mandatory_impossible and "impossible" not in self._objective_seen:
            self._objective_seen.add("impossible")
            self.objective_events.append({"event": "mandatory_trials_impossible", "bossTurn": boss_turn,
                                          "trialIds": list(report.impossible_trial_ids)})

    def _stuck(self, state: dict[str, Any], reason: str, detail: str | None) -> dict[str, Any]:
        """Where the live takeover would stall: what the hero had and why no rule fired."""
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        chimera = state.get("chimera") if isinstance(state.get("chimera"), dict) else {}
        reserved = {value for value in state.get("_reservedStrictSkillTypeIds") or [] if isinstance(value, int)}
        owners = controller.trial_rule_skill_owners(self.strategy.get("rules") or [], state)
        reserved.update(owners)
        skills = []
        for skill in state.get("skills", []):
            if not isinstance(skill, dict) or not isinstance(skill.get("typeId"), int):
                continue
            skills.append({"typeId": skill["typeId"], "slot": skill.get("slot"), "ready": skill.get("ready") is True,
                           "cooldown": skill.get("cooldown"), "defaultCooldown": skill.get("defaultCooldown"),
                           "validTargets": len(skill.get("validTargetIds") or []),
                           "reserved": skill["typeId"] in reserved})
        try:
            rules = controller.no_decision_report(self.strategy, state)
        except Exception:
            rules = []
        return {"status": "stuck", "reason": reason, "detail": detail, "stuck": {
            "reason": reason, "turn": battle.get("turn"), "bossTurns": chimera.get("turnCount"),
            "form": controller.canonical_chimera_form(chimera.get("currentForm")),
            "activeHeroId": state.get("activeHeroId"), "activeHeroTypeId": state.get("activeHeroTypeId"),
            "activeHeroFormIndex": state.get("activeHeroFormIndex"), "skills": skills, "rules": rules[:40],
            "detail": detail}}

    def decide(self, supplied: object) -> dict[str, Any]:
        if not isinstance(supplied, dict) or supplied.get("bossMode") != "chimera":
            return {"status": "unknown", "reason": "not_a_chimera_decision_state"}
        state = self._prepare(supplied)
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        marker = (battle.get("turn"), state.get("activeHeroId"))
        self._same_turn_decisions = self._same_turn_decisions + 1 if marker == self._turn_marker else 1
        self._turn_marker = marker
        if self._same_turn_decisions > MAX_SAME_TURN_DECISIONS:
            return self._stuck(state, "no_progress",
                               f"{MAX_SAME_TURN_DECISIONS} decisions on turn {marker[0]} without the battle advancing")
        previous_mode = controller.ACTIVE_BOSS_MODE
        try:
            controller.ACTIVE_BOSS_MODE = "chimera"
            self._after_previous_command(state)
            if self.team_selection is not None:
                controller.annotate_team_positions(state, {"screen": "battle", "battle": self.team_selection})
            self.capability_memory.observe_state(state)
            self._record_objectives(state)
            controller.refresh_trial_planner_runtime(self.runtime_state, state)
            controller.annotate_chimera_form_first_turn(state, self.runtime_state)
            state["_decisionTrace"] = []
            state["_reservedStrictSkillTypeIds"] = []
            decision = controller.pending_mythic_followup_decision(self.runtime_state, state)
            if decision is None:
                decision = controller.evaluate(self.strategy, state, self.capability_memory, self.runtime_state)
            diagnostic = None if decision is not None else controller.no_decision_diagnostic(self.strategy, state)
        except Exception as error:
            return {"status": "unknown", "reason": f"policy_evaluation_failed:{type(error).__name__}",
                    "detail": str(error)[:300]}
        finally:
            controller.ACTIVE_BOSS_MODE = previous_mode
        rule_index, rule_name = _selected_rule(state.get("_decisionTrace"))
        if decision is None:
            return self._stuck(state, "no_matching_rule", diagnostic)
        skill = decision.skill if isinstance(decision.skill, dict) else {}
        matching = [item for item in state.get("skills", [])
                    if item.get("skillId") == skill.get("skillId") and item.get("typeId") == skill.get("typeId")]
        if (len(matching) != 1 or matching[0].get("ready") is not True
                or not _integer(decision.target_id)
                or decision.target_id not in matching[0].get("validTargetIds", [])):
            stuck = self._stuck(state, "rule_command_not_legal",
                                f"{decision.rule}: skill {skill.get('typeId')} target {decision.target_id}")
            stuck["stuck"]["rule"] = decision.rule
            return stuck
        if (isinstance(decision.mythic_followup_action, dict) and isinstance(decision.mythic_followup_rule, str)
                and decision.mythic_followup_form_index in {0, 1}):
            self.runtime_state["mythicSkillFollowup"] = {
                "activeHeroId": state.get("activeHeroId"), "formIndex": decision.mythic_followup_form_index,
                "rule": decision.mythic_followup_rule, "action": decision.mythic_followup_action}
        elif decision.consumes_mythic_followup:
            self.runtime_state.pop("mythicSkillFollowup", None)
        if decision.capability_probe and isinstance(skill.get("typeId"), int):
            self.capability_memory.mark_probed(skill["typeId"])
        self.previous = (state, decision)
        released = any(isinstance(entry, dict) and entry.get("outcome") == "reservation_released"
                       for entry in state.get("_decisionTrace") or [])
        return {"status": "command", "actorId": state["activeHeroId"], "skillTypeId": skill["typeId"],
                "skillSlot": skill.get("slot"), "targetId": decision.target_id, "rule": decision.rule,
                "ruleIndex": (rule_index if rule_name == decision.rule
                              else _rule_index_by_name(self.strategy, decision.rule)),
                "trialId": decision.trial_id, **({"reservationReleased": True} if released else {})}


def _reply_line(decision: dict[str, Any], sequence: int) -> str:
    if decision["status"] == "command":
        return f"command\t{sequence}\t{decision['actorId']}\t{decision['skillTypeId']}\t{decision['targetId']}\n"
    return f"stop\t{sequence}\t{str(decision.get('reason') or 'unknown').split(':')[0]}\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_simulation(probe: Path, packed_input: Path, strategy: dict[str, Any], *,
                   seed: int | None = None,
                   capability_memory: controller.SkillCapabilityMemory | None = None,
                   static_state: dict[str, Any] | None = None,
                   team_selection: dict[str, Any] | None = None,
                   start_selection: dict[str, Any] | None = None,
                   timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                   cancel: threading.Event | None = None,
                   on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One isolated original-engine Chimera battle driven by the strategy."""
    started = time.monotonic()
    result: dict[str, Any] = {"schema": SCHEMA, "type": "chimera_strategy_simulation", "status": "unknown",
                              "reason": None, "requestedSeed": seed, "decisions": []}
    for name in ("battle-setup.msgpack", "battle-settings.msgpack"):
        if not (packed_input / name).is_file():
            result["reason"] = f"input_missing:{name}"
            return result
    result["inputs"] = {"battleSetupSha256": _sha256(packed_input / "battle-setup.msgpack"),
                        "strategySha256": hashlib.sha256(json.dumps(
                            strategy, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
    try:
        session = ChimeraOfflinePolicySession(
            strategy, capability_memory=copy.deepcopy(capability_memory) if capability_memory else None,
            static_state=static_state, team_selection=team_selection, start_selection=start_selection)
    except ValueError as error:
        result["reason"] = str(error)
        return result
    process = subprocess.Popen(
        [str(probe), "chimera-forecast", str(packed_input), "captured" if seed is None else str(int(seed))],
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
                raise SimulationError("decision_request_exceeds_bound")
            if line.startswith(b'{"childExitCode"'):
                wrapper = json.loads(line + process.stdout.read(64 * 1024 * 1024))
                break
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("type") != "decision_request":
                raise SimulationError("unexpected_probe_output")
            sequence, state = message.get("sequence"), message.get("state")
            if type(sequence) is not int or not isinstance(state, dict):
                raise SimulationError("decision_request_invalid")
            if cancel is not None and cancel.is_set():
                raise SimulationError("simulation_cancelled")
            decision = session.decide(state)
            battle = state.get("battle", {})
            chimera = state.get("chimera", {})
            decisions.append({
                "sequence": sequence, "turn": battle.get("turn"), "playerTurnCount": battle.get("playerTurnCount"),
                "bossTurns": chimera.get("turnCount"), "form": chimera.get("currentFormIndex"),
                "activeHeroId": state.get("activeHeroId"), "activeHeroTypeId": state.get("activeHeroTypeId"),
                "damage": battle.get("currentDamage"), **decision})
            if on_progress is not None and len(decisions) % 10 == 1:
                on_progress({"decisions": len(decisions), "bossTurns": chimera.get("turnCount")})
            process.stdin.write(_reply_line(decision, sequence).encode("utf-8"))
            process.stdin.flush()
    except Exception as error:  # A failed simulation is reported, never raised to the caller.
        process.kill()
        result["reason"] = (str(error) if isinstance(error, SimulationError)
                            else f"simulation_channel_failed:{type(error).__name__}")
    finally:
        watchdog.cancel()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        drain.join(timeout=5)
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    result["objectiveEvents"] = session.objective_events
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
                                   "stage": (observation or {}).get("stage") if isinstance(observation, dict) else None,
                                   "error": (observation or {}).get("error") if isinstance(observation, dict) else None,
                                   "nativeFault": wrapper.get("nativeFault")}
        return result
    engine = {key: value for key, value in observation.items()
              if key not in ("schema", "pid", "appContainer", "capabilityCount", "parentMemoryAccessDenied", "phase")}
    engine["actions"] = [dict(zip(ACTION_FIELDS, row)) for row in engine.get("actions", [])]
    result["engine"] = engine
    result["status"] = "complete" if engine.get("stopReason") == "battle_finished" else "partial"
    if engine.get("stopReason") != "battle_finished":
        result["reason"] = engine.get("stopReason")
    last = decisions[-1] if decisions else None
    if engine.get("stopReason") == "policy_stopped" and isinstance(last, dict) and last.get("status") == "stuck":
        result["status"] = "stuck"
        result["reason"] = last.get("reason")
        result["stuck"] = last.get("stuck")
    elif engine.get("stopReason") == "policy_command_rejected" and isinstance(last, dict):
        # The rule's command passed the state's legality data but the original
        # engine refused it: the live game would refuse it the same way.
        result["status"] = "stuck"
        result["reason"] = "engine_rejected_command"
        result["stuck"] = {"reason": "engine_rejected_command", "turn": last.get("turn"),
                           "bossTurns": last.get("bossTurns"), "activeHeroId": last.get("activeHeroId"),
                           "activeHeroTypeId": last.get("activeHeroTypeId"), "rule": last.get("rule"),
                           "ruleIndex": last.get("ruleIndex"), "skillTypeId": last.get("skillTypeId"),
                           "targetId": last.get("targetId"), "detail": engine.get("policyStop"),
                           "skills": [], "rules": []}
    return result


def form_window(boss_turns: int) -> int:
    """0-based five-Boss-turn window a player action belongs to (turns 1-5 → 0)."""
    return max(int(boss_turns) - 1, 0) // FORM_INTERVAL


def action_window(action: dict[str, Any]) -> int:
    """The Chimera's own action starts its next turn (and possibly a new form)."""
    boss_turns = int(action.get("bossTurns") or 0)
    return form_window(boss_turns + 1 if action.get("source") == "enemy" else boss_turns)


def summarize_run(result: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    """Compact per-run facts the report shows and aggregates."""
    engine = result.get("engine") or {}
    actions = engine.get("actions") or []
    decisions = result.get("decisions") or []
    objectives = strategy.get("objectives") if isinstance(strategy.get("objectives"), dict) else {}
    mandatory = [int(item) for item in controller.configured_trial_ids(
        objectives.get("mandatoryTrials", objectives.get("mandatoryTrialIds", [])))]
    trials = {trial["id"]: trial for trial in engine.get("trials", []) if isinstance(trial, dict)}
    best_ratio: dict[int, float] = {}
    progress_by_window: dict[int, dict[int, float]] = {}
    for action in actions:
        window = action_window(action)
        for trial_id, before, after, _, _, started, completed in action.get("trials") or []:
            target = trials.get(trial_id, {}).get("targetRaw") or 0
            ratio = 1.0 if completed else (after / target if target else 0.0)
            best_ratio[trial_id] = max(best_ratio.get(trial_id, 0.0), ratio)
            if ratio <= 0:
                continue  # Started or counter-only changes carry no progress.
            window_values = progress_by_window.setdefault(window, {})
            window_values[trial_id] = max(window_values.get(trial_id, 0.0), ratio)
    completed = {trial_id: trial.get("selfTurnWhichCompleted")
                 for trial_id, trial in trials.items() if trial.get("completed")}
    # Rule statistics: each decision request produced exactly one player
    # action, in order; attribute that action's damage and trial gains.
    player_actions = [action for action in actions if action.get("source") != "enemy"]
    rules: dict[str, dict[str, Any]] = {}
    for decision_index, (action, decision) in enumerate(zip(player_actions, decisions)):
        action["decisionIndex"] = decision_index
        key = str(decision.get("ruleIndex")) if decision.get("ruleIndex") is not None else f"name:{decision.get('rule')}"
        entry = rules.setdefault(key, {"ruleIndex": decision.get("ruleIndex"), "rule": decision.get("rule"),
                                       "uses": 0, "damage": 0.0, "trialGains": {}, "skills": {}, "heroes": {}})
        entry["uses"] += 1
        entry["damage"] += float(action.get("damage") or 0)
        entry["skills"][str(action.get("skillTypeId"))] = entry["skills"].get(str(action.get("skillTypeId")), 0) + 1
        entry["heroes"][str(action.get("actorTypeId"))] = entry["heroes"].get(str(action.get("actorTypeId")), 0) + 1
        for trial_id, before, after, _, _, _, done in action.get("trials") or []:
            target = trials.get(trial_id, {}).get("targetRaw") or 0
            gain = (after - before) / target if target and after > before else 0.0
            if gain > 0 or done:
                entry["trialGains"][str(trial_id)] = round(entry["trialGains"].get(str(trial_id), 0.0) + gain, 6)
    deaths = []
    actors = {actor["actorId"]: actor for actor in engine.get("actors", []) if isinstance(actor, dict)}
    for action in actions:
        for actor_id in action.get("deaths") or []:
            if actor_id >= 0 and actors.get(actor_id, {}).get("player"):
                deaths.append({"actorId": actor_id, "heroTypeId": actors[actor_id].get("heroTypeId"),
                               "bossTurn": action.get("bossTurns"), "turn": action.get("turn")})
    return {
        "seed": engine.get("seed"), "capturedSeed": engine.get("capturedSeed"),
        "exact": not engine.get("seedOverridden", False),
        "status": result.get("status"), "reason": result.get("reason"),
        "bossTurns": engine.get("bossTurns"), "damage": engine.get("damage"),
        "bossDamageTaken": engine.get("bossDamageTaken"),
        "commands": engine.get("commands"),
        "stuck": result.get("stuck"),
        "reservationReleases": sum(1 for decision in decisions if decision.get("reservationReleased")),
        "completedTrials": completed,
        "mandatory": [{"trialId": trial_id, "completed": trial_id in completed,
                       "completedBossTurn": completed.get(trial_id),
                       "bestRatio": round(best_ratio.get(trial_id, 0.0), 4),
                       "started": bool(trials.get(trial_id, {}).get("started"))}
                      for trial_id in mandatory],
        "progressByWindow": {str(window): {str(k): round(v, 4) for k, v in values.items()}
                             for window, values in sorted(progress_by_window.items())},
        "rules": sorted(rules.values(), key=lambda item: (item["ruleIndex"] is None, item["ruleIndex"] or 0)),
        "deaths": deaths,
        "objectiveEvents": result.get("objectiveEvents") or [],
        "elapsedSeconds": result.get("elapsedSeconds"),
    }

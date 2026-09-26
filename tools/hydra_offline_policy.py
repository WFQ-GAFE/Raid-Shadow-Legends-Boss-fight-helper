"""Pure offline Hydra policy bridge for an isolated original-engine runner.

Input is one already-constructed decision snapshot per player command window.
Output is one legal skill/target command, or explicit unknown. The module does
not open a game process, request a live snapshot, or submit any command.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, TextIO

import chimera_controller as controller


PROTOCOL_SCHEMA = 1


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _entity_issue(entity: object, path: str) -> str | None:
    if not isinstance(entity, dict):
        return f"{path}_not_object"
    if not _integer(entity.get("id")) or not _integer(entity.get("typeId"), 1):
        return f"{path}_identity_missing"
    if type(entity.get("dead")) is not bool:
        return f"{path}_dead_state_missing"
    health = entity.get("healthPct")
    if type(health) not in (int, float) or not 0 <= health <= 100:
        return f"{path}_health_missing"
    if not isinstance(entity.get("effects"), list) or not isinstance(entity.get("states"), dict):
        return f"{path}_effects_or_states_missing"
    if any(not isinstance(effect, dict)
           or not _integer(effect.get("id"))
           or not _integer(effect.get("effectTypeId"))
           or not _integer(effect.get("applyTurn"))
           for effect in entity["effects"]):
        return f"{path}_effect_invalid"
    return None


def decision_state_issue(state: object, *, require_rng: bool = False) -> str | None:
    """Reject missing policy inputs instead of interpreting them as empty/false."""
    if not isinstance(state, dict):
        return "decision_state_not_object"
    if state.get("bossMode") != "hydra":
        return "not_hydra_state"
    battle = state.get("battle")
    if not isinstance(battle, dict) or battle.get("hydraBattle") is not True:
        return "hydra_battle_identity_missing"
    if battle.get("finished") is not False or battle.get("waitingForManualCommand") is not True:
        return "not_a_manual_player_command_window"
    if any(not _integer(battle.get(key), 1 if key in ("round", "turn") else 0)
           for key in ("round", "turn", "playerTurnCount")):
        return "battle_turn_missing"
    if (not _integer(state.get("battleGeneration"), 1)
            or not _integer(state.get("activeHeroId"))
            or not _integer(state.get("activeHeroTypeId"), 1)
            or not _integer(state.get("activeHeroTurnCount"))
            or not _integer(state.get("activeHeroFormIndex"))):
        return "active_hero_or_generation_missing"
    hydra = state.get("hydra")
    if not isinstance(hydra, dict) or hydra.get("active") is not True \
            or not _integer(hydra.get("turnCount")):
        return "hydra_turn_state_missing"
    skills = state.get("skills")
    if not isinstance(skills, list) or not skills:
        return "active_hero_skills_missing"
    seen_skills: set[tuple[int, int]] = set()
    for skill in skills:
        if (not isinstance(skill, dict)
                or not _integer(skill.get("skillId"))
                or not _integer(skill.get("slot"), 1)
                or skill["slot"] != skill["skillId"] + 1
                or not _integer(skill.get("typeId"), 1)
                or type(skill.get("ready")) is not bool
                or type(skill.get("passive")) is not bool
                or type(skill.get("blocked")) is not bool
                or not _integer(skill.get("cooldown"))
                or not isinstance(skill.get("validTargetIds"), list)
                or any(not _integer(target) for target in skill["validTargetIds"])):
            return "active_hero_skill_invalid"
        identity = (skill["skillId"], skill["typeId"])
        if identity in seen_skills:
            return "duplicate_active_hero_skill"
        seen_skills.add(identity)
    heroes = state.get("heroes")
    bosses = state.get("bosses")
    if not isinstance(heroes, list) or not heroes or not isinstance(bosses, list) or not bosses:
        return "hero_or_head_roster_missing"
    for key, entities in (("hero", heroes), ("head", bosses)):
        for entity in entities:
            issue = _entity_issue(entity, key)
            if issue is not None:
                return issue
    active = [hero for hero in heroes if hero["id"] == state["activeHeroId"]]
    if len(active) != 1 or active[0]["typeId"] != state["activeHeroTypeId"] \
            or active[0]["dead"] is True:
        return "active_hero_not_in_live_roster"
    if require_rng:
        # The policy itself does not consume RNG. The forecasting protocol does
        # require a battle-bound checkpoint so later engine comparisons can.
        from verify_hydra_policy_replay import valid_rng_for_action
        if not valid_rng_for_action({
            "turn": battle,
            "battleRandomBefore": state.get("battleRandom"),
        }):
            return "battle_rng_checkpoint_missing"
    return None


class HydraOfflinePolicySession:
    """One battle, one persistent policy memory and one monotonic state stream."""

    def __init__(self, strategy: dict[str, Any], *, require_rng: bool = False,
                 capability_memory: controller.SkillCapabilityMemory | None = None,
                 runtime_state: dict[str, Any] | None = None,
                 team_selection: dict[str, Any] | None = None):
        if not isinstance(strategy, dict) or strategy.get("bossMode", "hydra") != "hydra":
            raise ValueError("A Hydra strategy snapshot is required")
        controller.require_list_execution(strategy)
        self.strategy = copy.deepcopy(strategy)
        self.require_rng = require_rng
        self.capability_memory = capability_memory or controller.SkillCapabilityMemory()
        self.runtime_state = runtime_state if runtime_state is not None else {}
        # Same shape as the lifecycle "battle" section the live controller
        # passes to annotate_team_positions: heroTypeIds/heroIds in team order.
        self.team_selection = copy.deepcopy(team_selection) if team_selection else None
        self.generation: int | None = None
        self.setup_id: str | None = None
        self.seed: int | None = None
        self.last_turn = -1
        self.last_player_turn = -1
        self.last_sequence = -1
        self.stopped = False

    @staticmethod
    def _unknown(reason: str, state: object) -> dict[str, Any]:
        battle = state.get("battle", {}) if isinstance(state, dict) else {}
        return {"schema": PROTOCOL_SCHEMA, "type": "policy_decision",
                "status": "unknown", "reason": reason,
                "turn": {key: battle.get(key) for key in ("round", "turn", "playerTurnCount")},
                "command": None, "futurePredictionVerified": False}

    def decide(self, supplied_state: object) -> dict[str, Any]:
        if self.stopped:
            return self._unknown("session_stopped_after_unknown", supplied_state)
        issue = decision_state_issue(supplied_state, require_rng=self.require_rng)
        if issue:
            self.stopped = True
            return self._unknown(issue, supplied_state)
        state = copy.deepcopy(supplied_state)
        battle = state["battle"]
        generation = state["battleGeneration"]
        turn = battle["turn"]
        player_turn = battle["playerTurnCount"]
        sequence = state.get("sequence")
        if self.generation is None:
            self.generation = generation
        elif generation != self.generation:
            self.stopped = True
            return self._unknown("battle_generation_changed", state)
        if turn <= self.last_turn or player_turn <= self.last_player_turn:
            self.stopped = True
            return self._unknown("decision_turn_not_monotonic", state)
        if sequence is not None:
            if not _integer(sequence, 1) or sequence <= self.last_sequence:
                self.stopped = True
                return self._unknown("native_sequence_not_monotonic", state)
            self.last_sequence = sequence
        rng = state.get("battleRandom")
        if self.require_rng and isinstance(rng, dict):
            setup_id, seed = rng["battleSetupId"], rng["seed"]
            if self.setup_id is None:
                self.setup_id, self.seed = setup_id, seed
            elif (setup_id, seed) != (self.setup_id, self.seed):
                self.stopped = True
                return self._unknown("battle_rng_identity_changed", state)
        self.last_turn, self.last_player_turn = turn, player_turn
        previous_mode = controller.ACTIVE_BOSS_MODE
        try:
            controller.ACTIVE_BOSS_MODE = "hydra"
            # Mirror process_state's inputs and order: team slots, capability
            # learning, a pending mythic follow-up, then the list rules.
            if self.team_selection is not None:
                controller.annotate_team_positions(
                    state, {"screen": "battle", "battle": self.team_selection})
            self.capability_memory.observe_state(state)
            state["_decisionTrace"] = []
            state["_reservedStrictSkillTypeIds"] = []
            decision = controller.pending_mythic_followup_decision(self.runtime_state, state)
            if decision is None:
                decision = controller.evaluate(
                    self.strategy, state, self.capability_memory, self.runtime_state)
        except Exception as error:
            self.stopped = True
            return self._unknown(f"policy_evaluation_failed:{type(error).__name__}", state)
        finally:
            controller.ACTIVE_BOSS_MODE = previous_mode
        if decision is None or not isinstance(decision.skill, dict):
            self.stopped = True
            return self._unknown("policy_returned_no_skill_command", state)
        skill = decision.skill
        matching = [item for item in state["skills"]
                    if item["skillId"] == skill.get("skillId")
                    and item["typeId"] == skill.get("typeId")
                    and item["slot"] == skill.get("slot")]
        if (len(matching) != 1 or matching[0]["ready"] is not True
                or matching[0]["passive"] is True or matching[0]["blocked"] is True
                or matching[0]["cooldown"] != 0
                or not _integer(decision.target_id)
                or decision.target_id not in matching[0]["validTargetIds"]):
            self.stopped = True
            return self._unknown("policy_command_not_legal_in_supplied_state", state)
        # Post-submit bookkeeping of process_state that changes later choices.
        if (isinstance(decision.mythic_followup_action, dict)
                and isinstance(decision.mythic_followup_rule, str)
                and decision.mythic_followup_form_index in {0, 1}):
            self.runtime_state["mythicSkillFollowup"] = {
                "activeHeroId": state.get("activeHeroId"),
                "formIndex": decision.mythic_followup_form_index,
                "rule": decision.mythic_followup_rule,
                "action": decision.mythic_followup_action,
            }
        elif decision.consumes_mythic_followup:
            self.runtime_state.pop("mythicSkillFollowup", None)
        if decision.capability_probe:
            self.capability_memory.mark_probed(skill["typeId"])
        return {"schema": PROTOCOL_SCHEMA, "type": "policy_decision",
                "status": "command", "reason": None,
                "battleGeneration": generation,
                "nativeSequence": sequence,
                "turn": {key: battle[key] for key in ("round", "turn", "playerTurnCount")},
                "activeHeroId": state["activeHeroId"],
                "activeHeroTypeId": state["activeHeroTypeId"],
                "command": {"skillId": skill["skillId"], "skillSlot": skill["slot"],
                            "skillTypeId": skill["typeId"], "targetId": decision.target_id},
                "rule": decision.rule,
                "futurePredictionVerified": False}


def run_json_lines(session: HydraOfflinePolicySession, source: TextIO, destination: TextIO) -> None:
    """Versioned line protocol; stop the stream on the first unknown."""
    for line in source:
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("schema") != PROTOCOL_SCHEMA \
                    or message.get("type") != "decision_state":
                result = session._unknown("unsupported_decision_protocol", message)
            else:
                result = session.decide(message.get("state"))
        except json.JSONDecodeError:
            result = session._unknown("invalid_decision_json", None)
        destination.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        destination.flush()
        if result["status"] != "command":
            session.stopped = True
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, type=Path,
                        help="exact, local Hydra strategy snapshot JSON")
    parser.add_argument("--capability-cache", type=Path)
    parser.add_argument("--capability-seed", type=Path)
    parser.add_argument("--allow-missing-rng", action="store_true",
                        help="retrospective policy parity only; never a forecasting run")
    args = parser.parse_args()
    strategy = json.loads(args.strategy.read_text(encoding="utf-8"))
    memory = (controller.SkillCapabilityMemory.load(args.capability_cache, args.capability_seed)
              if args.capability_cache is not None
              else controller.SkillCapabilityMemory())
    run_json_lines(HydraOfflinePolicySession(
        strategy, require_rng=not args.allow_missing_rng,
        capability_memory=memory), sys.stdin, sys.stdout)

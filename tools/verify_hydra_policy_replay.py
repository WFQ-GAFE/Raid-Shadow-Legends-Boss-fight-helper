"""Check whether the recorded Hydra policy reproduces submitted actions.

This is a retrospective decision-parity check. It does not simulate the
BattleProcessor, predict mark order, or control a game process.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chimera_controller import SkillCapabilityMemory, evaluate
from strategy_storage import atomic_write_json


def external_journal_rows(directory: Path) -> list[dict[str, Any]]:
    """Load a completed, separately copied controller log from this capture.

    The observer cannot always read the controller's AppData log while it is
    running. A final copy lives beside the native capture and is only used for
    retrospective analysis; a live snapshot is deliberately ignored.
    """
    path = directory / "controller-decisions-final.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{number}: invalid JSON") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{number}: expected an object")
            if row.get("bossMode") == "hydra":
                rows.append(row)
    return rows


def decision_matches_native(row: dict[str, Any], state: dict[str, Any]) -> bool:
    """Bind a copied journal decision to one native snapshot, beyond sequence."""
    decision = row.get("decision")
    context = decision.get("context") if isinstance(decision, dict) else None
    battle = context.get("battle") if isinstance(context, dict) else None
    active = context.get("activeHero") if isinstance(context, dict) else None
    native_battle = state.get("battle")
    return bool(
        isinstance(battle, dict) and isinstance(active, dict)
        and isinstance(native_battle, dict)
        and decision.get("sequence") == state.get("sequence")
        and context.get("battleGeneration") == state.get("battleGeneration")
        and context.get("observedAtTick") == state.get("observedAtTick")
        and active.get("activeHeroId") == state.get("activeHeroId")
        and active.get("activeHeroTypeId") == state.get("activeHeroTypeId")
        and all(battle.get(key) == native_battle.get(key)
                for key in ("round", "turn", "playerTurnCount"))
    )


def valid_rng_for_action(action: dict[str, Any]) -> bool:
    """A replay checkpoint must identify the same turn and one stable RNG state."""
    rng = action.get("battleRandomBefore")
    turn = action.get("turn")
    if not isinstance(rng, dict) or not isinstance(turn, dict):
        return False
    if (type(rng.get("schema")) is not int or rng["schema"] != 1
            or rng.get("source") != "BattleState.Random_fields"
            or rng.get("capturePoint") != "published_snapshot"
            or rng.get("available") is not True
            or rng.get("readStatus") != "stable_double_read"):
        return False
    words = rng.get("words")
    if (not isinstance(words, list) or len(words) != 4
            or any(type(value) is not int or not 0 <= value < (1 << 32)
                   for value in words)
            or not any(words)):
        return False
    if any(type(rng.get(key)) is not int or rng[key] != turn.get(key)
           for key in ("turn", "playerTurnCount")):
        return False
    if (rng.get("seedAvailable") is not True
            or type(rng.get("seed")) is not int
            or not -(1 << 31) <= rng["seed"] < (1 << 31)):
        return False
    setup_id = rng.get("battleSetupId")
    return (rng.get("battleSetupIdAvailable") is True
            and type(setup_id) is str
            and len(setup_id) == 32
            and setup_id != "0" * 32
            and all(char in "0123456789abcdef" for char in setup_id))


def recorded_action(state: dict, logged: dict, decision_number: int,
                    policy_matched: bool) -> dict[str, Any]:
    """Build a bounded command record that a native offline runner can consume.

    This only packages an action already submitted in the observed battle. It
    does not claim that replaying the action list will reproduce its states.
    """
    skill_type_id = logged.get("skillTypeId")
    target_id = logged.get("targetId")
    active_id = state.get("activeHeroId")
    active_type_id = state.get("activeHeroTypeId")
    skills = state.get("skills")
    if not isinstance(skills, list):
        skills = []
    candidates = [skill for skill in skills if isinstance(skill, dict)
                  and skill.get("typeId") == skill_type_id]
    skill = candidates[0] if len(candidates) == 1 else None
    valid_targets = skill.get("validTargetIds") if isinstance(skill, dict) else None
    slot_id = skill.get("skillId") if isinstance(skill, dict) else None
    slot = skill.get("slot") if isinstance(skill, dict) else logged.get("skillSlot")
    legal = bool(
        isinstance(skill, dict)
        and type(slot_id) is int and slot_id >= 0
        and type(slot) is int and slot > 0
        and skill.get("ready") is True
        and skill.get("passive") is not True
        and isinstance(valid_targets, list)
        and type(target_id) is int and target_id in valid_targets
    )
    entities = []
    for key in ("heroes", "bosses"):
        value = state.get(key)
        if isinstance(value, list):
            entities.extend(value)
    target = next((item for item in entities if isinstance(item, dict)
                   and item.get("id") == target_id), None)
    battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
    state_bytes = json.dumps(state, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    return {
        "decisionNumber": decision_number,
        "nativeSequence": state.get("sequence"),
        "preStateSha256": hashlib.sha256(state_bytes).hexdigest(),
        "turn": {
            "generation": state.get("battleGeneration"),
            "round": battle.get("round"),
            "turn": battle.get("turn"),
            "playerTurnCount": battle.get("playerTurnCount"),
            "activeHeroId": active_id,
            "activeHeroTypeId": active_type_id,
            "activeHeroTurnCount": state.get("activeHeroTurnCount"),
            "activeHeroFormIndex": state.get("activeHeroFormIndex"),
            "activeHeroSkillsUpdateCounter": state.get("activeHeroSkillsUpdateCounter"),
        },
        "command": {
            "skillId": slot_id,
            "skillSlot": slot,
            "skillTypeId": skill_type_id,
            "targetId": target_id,
            "targetTypeId": target.get("typeId") if isinstance(target, dict) else None,
        },
        "actionWasLegalInCapturedState": legal,
        "policyMatchedSubmittedAction": policy_matched,
        "battleRandomBefore": state.get("battleRandom"),
    }


def command_script_tsv(actions: list[dict[str, Any]]) -> str:
    """Serialize only a complete, already-observed action sequence."""
    if not actions:
        raise ValueError("Cannot create an empty native command script")
    lines = [
        "scope\tcontroller_submitted_skill_commands_only",
        "schema\t1",
        "decisionNumber\tround\tturn\tplayerTurnCount\tactiveHeroId\tactiveHeroTypeId\t"
        "skillId\tskillTypeId\ttargetId\tpreStateSha256",
    ]
    for item in actions:
        if item.get("actionWasLegalInCapturedState") is not True or item.get(
            "policyMatchedSubmittedAction"
        ) is not True:
            raise ValueError("Refusing a command script with a policy mismatch or illegal captured action")
        turn = item.get("turn")
        command = item.get("command")
        if not isinstance(turn, dict) or not isinstance(command, dict):
            raise ValueError("Captured action is missing its turn or command fields")
        values = [
            item.get("decisionNumber"), turn.get("round"), turn.get("turn"),
            turn.get("playerTurnCount"), turn.get("activeHeroId"),
            turn.get("activeHeroTypeId"), command.get("skillId"),
            command.get("skillTypeId"), command.get("targetId"),
            item.get("preStateSha256"),
        ]
        if any(type(value) is not int for value in values[:-1]):
            raise ValueError("Captured command script contains a non-integer action field")
        if not isinstance(values[-1], str) or len(values[-1]) != 64:
            raise ValueError("Captured command script contains an invalid pre-state hash")
        lines.append("\t".join(str(value) for value in values))
    return "\n".join(lines) + "\n"


def collect_rows(directories: list[Path], *, native_generation: int | None = None
                 ) -> tuple[dict[int, dict], list[dict], list[dict]]:
    states: dict[int, dict] = {}
    journal_by_identity: dict[tuple[Any, ...], dict] = {}
    statuses = []
    def add_journal(payload: dict[str, Any]) -> None:
        if payload.get("bossMode") != "hydra":
            return
        identity = (payload.get("run"), payload.get("time"),
                    payload.get("event"), payload.get("decisionNumber"))
        previous = journal_by_identity.get(identity)
        if previous is not None and previous != payload:
            raise ValueError(f"Controller journal identity {identity} conflicts")
        journal_by_identity[identity] = payload

    for directory in directories:
        status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        if status.get("phase") != "stopped":
            raise ValueError(f"Observer capture is not stopped: {directory}")
        statuses.append(status)
        with gzip.open(directory / "raw.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                row = json.loads(line)
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                if row.get("channel") == "decision" and payload.get("type") == "decision_state":
                    if payload.get("battle", {}).get("hydraBattle") is not True:
                        continue
                    sequence = payload.get("sequence")
                    if (native_generation is not None
                            and payload.get("battleGeneration") != native_generation):
                        continue
                    if type(sequence) is not int or sequence <= 0:
                        raise ValueError(f"{directory.name}:{line_number}: invalid native sequence")
                    previous = states.get(sequence)
                    if previous is not None and previous != payload:
                        raise ValueError(f"Native sequence {sequence} has conflicting snapshots")
                    states[sequence] = payload
                elif row.get("channel") == "controller_journal":
                    add_journal(payload)
        for payload in external_journal_rows(directory):
            add_journal(payload)
    for row in journal_by_identity.values():
        if row.get("event") != "decision":
            continue
        decision = row.get("decision")
        sequence = decision.get("sequence") if isinstance(decision, dict) else None
        state = states.get(sequence) if type(sequence) is int else None
        if state is not None and not decision_matches_native(row, state):
            raise ValueError(f"Controller decision {row.get('decisionNumber')} does not match native snapshot {sequence}")
    return states, list(journal_by_identity.values()), statuses


def verify(directories: list[Path], *, native_generation: int | None = None) -> dict[str, Any]:
    states, journal, statuses = collect_rows(directories,
                                            native_generation=native_generation)
    pids = {status.get("pid") for status in statuses}
    instance_ids = {status.get("agent", {}).get("instanceId") for status in statuses
                    if isinstance(status.get("agent"), dict)}
    generations = {state.get("battleGeneration") for state in states.values()}
    if len(pids) != 1 or None in pids:
        raise ValueError("Capture folders have different or missing process identities")
    if len(instance_ids) != 1 or None in instance_ids:
        raise ValueError("Capture folders have different or missing agent instances")
    if len(generations) != 1 or None in generations:
        raise ValueError("Capture folders have different or missing battle generations")
    decision_rows = [row for row in journal if row.get("event") == "decision"]
    bound = [row for row in decision_rows
             if row.get("decision", {}).get("sequence") in states]
    runs = {row.get("run") for row in bound if isinstance(row.get("run"), str)}
    if len(runs) != 1:
        raise ValueError(f"Expected one native-bound controller run, found {len(runs)}")
    run_id = next(iter(runs))
    run_rows = [row for row in journal if row.get("run") == run_id]
    starts = [row for row in run_rows if row.get("event") == "start"]
    if len(starts) != 1 or not isinstance(starts[0].get("strategy"), dict):
        raise ValueError("The exact strategy snapshot is missing or ambiguous")
    strategy = starts[0]["strategy"]
    strategy_id = starts[0].get("strategyId")
    running_revision = starts[0].get("runningRevision")

    commands = [row for row in run_rows
                if row.get("event") == "command"
                and isinstance(row.get("command"), dict)
                and row["command"].get("status") == "submitted"]
    decisions_by_number: dict[int, dict] = {}
    for row in run_rows:
        number = row.get("decisionNumber")
        if row.get("event") != "decision" or type(number) is not int:
            continue
        previous = decisions_by_number.get(number)
        if previous is not None and previous != row:
            raise ValueError(f"Decision number {number} has conflicting journal records")
        decisions_by_number[number] = row

    memory = SkillCapabilityMemory()
    runtime_state: dict[str, Any] = {}
    comparisons = []
    action_script = []
    unpaired_commands = []
    for command in sorted(commands, key=lambda row: row.get("decisionNumber", -1)):
        number = command.get("decisionNumber")
        decision_row = decisions_by_number.get(number)
        if decision_row is None:
            unpaired_commands.append(number)
            continue
        logged = decision_row.get("decision")
        sequence = logged.get("sequence") if isinstance(logged, dict) else None
        state = states.get(sequence) if type(sequence) is int else None
        if state is None:
            unpaired_commands.append(number)
            continue
        try:
            result = evaluate(strategy, json.loads(json.dumps(state)), memory, runtime_state)
            actual = ((result.skill.get("typeId"), result.target_id)
                      if result is not None and isinstance(result.skill, dict)
                      else (None, None))
        except Exception as error:
            actual = None
            failure = f"{type(error).__name__}: {error}"
        else:
            failure = None
        expected = (logged.get("skillTypeId"), logged.get("targetId"))
        comparisons.append({
            "decisionNumber": number,
            "sequence": sequence,
            "expected": expected,
            "actual": actual,
            "matched": actual == expected,
            "failure": failure,
        })
        action_script.append(recorded_action(state, logged, number,
                                             actual == expected))

    divergences = [item for item in comparisons if not item["matched"]]
    account_names = {status.get("accountName") for status in statuses}
    if len(account_names) != 1:
        raise ValueError("Capture folders belong to different account names")
    replay_inputs: dict[tuple[Any, ...], dict] = {}
    for state in states.values():
        captured = state.get("hydraReplayInput")
        if not isinstance(captured, dict) or captured.get("status") != "captured":
            continue
        key = (captured.get("battleGeneration"), captured.get("battleSetupId"))
        metadata = {key: captured.get(key) for key in (
            "battleGeneration", "battleSetupId", "agentBuildId", "engineVersion",
            "seed", "battleKindId", "stageId", "battleSetupBytes", "battleSettingsBytes",
        )}
        prior = replay_inputs.get(key)
        if prior is not None and prior != metadata:
            raise ValueError(f"Conflicting packed replay input metadata for {key}")
        replay_inputs[key] = metadata
    random_states_complete = bool(action_script) and all(
        valid_rng_for_action(item) for item in action_script)
    manual_turns = {}
    for state in states.values():
        battle = state.get("battle")
        if not isinstance(battle, dict) or battle.get("waitingForManualCommand") is not True:
            continue
        active_id = state.get("activeHeroId")
        heroes = state.get("heroes") if isinstance(state.get("heroes"), list) else []
        active = next((hero for hero in heroes
                       if isinstance(hero, dict) and hero.get("id") == active_id
                       and hero.get("side") == "ally"), None)
        if active is None:
            continue
        key = (state.get("battleGeneration"), battle.get("round"), battle.get("turn"),
               battle.get("playerTurnCount"), active_id, active.get("typeId"))
        manual_turns[key] = key
    commanded_turns = {
        (item["turn"].get("generation"), item["turn"].get("round"),
         item["turn"].get("turn"), item["turn"].get("playerTurnCount"),
         item["turn"].get("activeHeroId"), item["turn"].get("activeHeroTypeId"))
        for item in action_script if isinstance(item.get("turn"), dict)
    }
    uncommanded_manual_turns = sorted(set(manual_turns) - commanded_turns,
                                      key=lambda item: tuple(
                                          value if type(value) is int else -1 for value in item
                                      ))
    all_submitted_commands_reconstructed = (
        len(action_script) == len(commands)
        and not unpaired_commands
        and all(item["actionWasLegalInCapturedState"] for item in action_script)
        and not divergences
    )
    return {
        "schema": 2,
        "accountName": next(iter(account_names)),
        "pid": next(iter(pids)),
        "agentInstanceId": next(iter(instance_ids)),
        "battleGeneration": next(iter(generations)),
        "run": run_id,
        "strategyId": strategy_id,
        "runningRevision": running_revision,
        "strategyRuleCount": len(strategy.get("rules", [])),
        "decisionStateCount": len(states),
        "submittedCommandCount": len(commands),
        "pairedAndEvaluatedCount": len(comparisons),
        "unpairedCommandCount": len(unpaired_commands),
        "unpairedDecisionNumbers": unpaired_commands,
        "exactPolicyMatches": len(comparisons) - len(divergences),
        "policyDivergenceCount": len(divergences),
        "firstDivergences": divergences[:10],
        "recordedActionTrace": action_script,
        "replayInputMetadata": list(replay_inputs.values()),
        "replayCaptureReadiness": {
            "allSubmittedCommandsReconstructed": all_submitted_commands_reconstructed,
            "sampledPlayerManualWindowCount": len(manual_turns),
            "sampledManualWindowsWithoutSubmittedCommandCount": len(uncommanded_manual_turns),
            "sampledManualWindowsWithoutSubmittedCommand": uncommanded_manual_turns[:20],
            "scope": "published decision snapshots only; this does not assert a complete engine event stream",
            "singlePackedStartInputCaptured": len(replay_inputs) == 1,
            "rngCapturedBeforeEverySubmittedAction": random_states_complete,
            "battleProcessorReplayVerified": False,
            "futureMarkPredictionVerified": False,
        },
        "scope": "retrospective_policy_decision_parity_on_recorded_states",
        "battleProcessorReplayVerified": False,
        "rngCallTraceVerified": False,
        "futureMarkPredictionVerified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--actions-output", type=Path,
                        help="write a TSV of the exact, already-submitted player commands")
    parser.add_argument("--native-generation", type=int,
                        help="isolate one battle when the observer saw automatic restarts")
    args = parser.parse_args()
    report = verify(args.directories, native_generation=args.native_generation)
    atomic_write_json(args.output, report)
    if args.actions_output is not None:
        script = command_script_tsv(report["recordedActionTrace"])
        temporary = args.actions_output.with_suffix(args.actions_output.suffix + ".tmp")
        temporary.write_text(script, encoding="utf-8", newline="\n")
        temporary.replace(args.actions_output)
    print(json.dumps(report, ensure_ascii=False, indent=2))

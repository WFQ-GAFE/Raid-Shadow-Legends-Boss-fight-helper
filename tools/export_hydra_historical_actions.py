"""Export observed Hydra controller actions for offline replay research.

Only existing observer and controller journals are read. This intentionally does
not produce validated-actions.tsv: the historical records do not contain the
exact post-command RNG state or SetOnCooldown flag required by replay-script.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Non-object row: {path}:{number}")
                yield value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export(capture: Path, journals: list[Path], destination: Path) -> dict:
    if not journals:
        raise ValueError("At least one controller journal is required")
    provenance = _read_json(capture / "replay-source" / "capture-provenance.json")
    status = _read_json(capture / "status.json")
    generation = provenance.get("battleGeneration")
    setup_id = provenance.get("battleSetupId")
    seed = provenance.get("seed")
    if (provenance.get("type") != "verified_hydra_replay_source"
            or type(generation) is not int or not isinstance(setup_id, str)
            or type(seed) is not int):
        raise ValueError("Capture lacks a verified Hydra setup identity")
    raw_path = capture / "raw.jsonl.gz"
    native = {}
    for row in _rows(raw_path):
        value = row.get("payload")
        if (row.get("channel") != "decision" or not isinstance(value, dict)
                or value.get("type") != "decision_state"
                or value.get("battleGeneration") != generation):
            continue
        random = value.get("battleRandom") or {}
        if (random.get("battleSetupId") != setup_id or random.get("seed") != seed):
            raise ValueError("Native snapshot has a different setup or seed")
        sequence = value.get("sequence")
        if type(sequence) is not int:
            raise ValueError("Native decision has no sequence")
        if sequence in native and native[sequence] != value:
            raise ValueError(f"Conflicting native decision sequence {sequence}")
        native[sequence] = value
    if not native:
        raise ValueError("No matching native decision snapshots")

    journal_rows = []
    for journal in journals:
        journal_rows.extend(_rows(journal))
    starts = [row for row in journal_rows if row.get("event") == "start"]
    if len(starts) != 1 or not isinstance(starts[0].get("strategy"), dict):
        raise ValueError("Exactly one full controller strategy is required")
    run = starts[0].get("run")
    if (not isinstance(run, str) or any(row.get("run") != run for row in journal_rows)
            or any(row.get("bossMode") != "hydra" for row in journal_rows)):
        raise ValueError("Journals do not belong to one Hydra controller run")
    attached = [row.get("lifecycle", {}).get("agent") for row in journal_rows
                if row.get("event") == "lifecycle"
                and row.get("lifecycle", {}).get("event") == "agent_attached"]
    agent = status.get("agent") or {}
    if (len(attached) != 1 or not isinstance(attached[0], dict)
            or attached[0].get("instanceId") != agent.get("instanceId")
            or attached[0].get("buildId") != agent.get("buildId")):
        raise ValueError("Controller and observer agent instances do not match")

    decisions = {}
    for row in journal_rows:
        if row.get("event") != "decision":
            continue
        number = row.get("decisionNumber")
        if number in decisions:
            raise ValueError(f"Repeated controller decision number {number}")
        decision = row.get("decision") or {}
        context = decision.get("context") or {}
        if context.get("battleGeneration") != generation:
            raise ValueError(f"Decision {number} belongs to another battle generation")
        decisions[number] = row

    actions = []
    native_count = 0
    last_turn = 0
    for row in journal_rows:
        if row.get("event") != "command" or (row.get("command") or {}).get("status") != "submitted":
            continue
        number = row.get("decisionNumber")
        source = decisions.get(number)
        if source is None or source["time"] > row["time"]:
            raise ValueError(f"Submitted command {number} has no preceding decision")
        decision = source["decision"]
        context = decision["context"]
        battle = context["battle"]
        hero = context["activeHero"]
        turn = battle.get("turn")
        if type(turn) is not int or turn <= last_turn:
            raise ValueError(f"Submitted command {number} has no advancing game turn")
        last_turn = turn
        sequence = decision.get("sequence")
        observed = native.get(sequence)
        words = None
        if observed is not None:
            checks = (
                ("tick", context.get("observedAtTick"), observed.get("observedAtTick")),
                ("turn", turn, observed.get("battle", {}).get("turn")),
                ("round", battle.get("round"), observed.get("battle", {}).get("round")),
                ("playerTurnCount", battle.get("playerTurnCount"),
                 observed.get("battle", {}).get("playerTurnCount")),
                ("actorId", hero.get("activeHeroId"), observed.get("activeHeroId")),
                ("actorTypeId", hero.get("activeHeroTypeId"), observed.get("activeHeroTypeId")),
            )
            for field, left, right in checks:
                if left != right:
                    raise ValueError(f"Action {len(actions) + 1} native {field} mismatch")
            words = (observed.get("battleRandom") or {}).get("words")
            if (not isinstance(words, list) or len(words) != 4
                    or any(type(word) is not int for word in words)):
                raise ValueError(f"Action {len(actions) + 1} has no valid native RNG before")
            native_count += 1
        action = {
            "index": len(actions) + 1,
            "decisionNumber": number,
            "nativeDecisionSequence": sequence,
            "decisionObservedAtTick": context.get("observedAtTick"),
            "turnBefore": turn,
            "roundBefore": battle.get("round"),
            "playerTurnCountBefore": battle.get("playerTurnCount"),
            "actorIdLive": hero.get("activeHeroId"),
            "actorTypeId": hero.get("activeHeroTypeId"),
            "skillTypeId": decision.get("skillTypeId"),
            "skillSlot": decision.get("skillSlot"),
            "targetIdLive": decision.get("targetId"),
            "rngBeforeObserved": words,
            "rngBeforeSource": "same_sequence_native_snapshot" if words is not None else None,
            "setOnCooldown": None,
            "turnAfter": None,
            "rngAfter": None,
            "commandStatus": "submitted",
        }
        if (type(action["skillTypeId"]) is not int or type(action["targetIdLive"]) is not int):
            raise ValueError(f"Action {action['index']} has no selected skill or target")
        actions.append(action)
    if not actions:
        raise ValueError("No submitted player actions")
    turns = {row["decision"]["context"]["battle"]["turn"] for row in decisions.values()}
    missing_turns = [turn for turn in range(1, max(turns) + 1) if turn not in turns]
    first_no_native = next((action for action in actions if action["rngBeforeObserved"] is None), None)
    report = {
        "schema": 1,
        "status": "historical_actions_exported_strict_replay_unavailable",
        "capture": str(capture.resolve()),
        "controllerJournals": [{"path": str(path.resolve()), "sha256": _sha256(path)}
                               for path in journals],
        "rawCaptureSha256": _sha256(raw_path),
        "battleSetupId": setup_id,
        "seed": seed,
        "battleGeneration": generation,
        "controllerRun": run,
        "agentBuildId": agent.get("buildId"),
        "agentInstanceId": agent.get("instanceId"),
        "journalPidBinding": status.get("journalPidBinding"),
        "controllerDecisions": len(decisions),
        "submittedPlayerActions": len(actions),
        "nativeRngBeforeActions": native_count,
        "firstActionWithoutNativeRngBefore": None if first_no_native is None else {
            "index": first_no_native["index"], "turn": first_no_native["turnBefore"],
            "decisionNumber": first_no_native["decisionNumber"],
            "nativeDecisionSequence": first_no_native["nativeDecisionSequence"],
        },
        "firstTurn": actions[0]["turnBefore"],
        "lastSubmittedTurn": actions[-1]["turnBefore"],
        "distinctDecisionTurns": len(turns),
        "turnsWithoutDecisionSnapshot": len(missing_turns),
        "firstTurnsWithoutDecisionSnapshot": missing_turns[:20],
        "completeNativeEventStream": status.get("completeEventStream") is True,
        "firstStrictReplayBlocker": {
            "actionIndex": 1,
            "missingFields": ["setOnCooldown", "turnAfter", "rngAfter"],
            "reason": "Neither controller journal nor native observer records the exact completed SkillCommand and post-ApplyCommand RNG checkpoint",
        },
        "strictReplayScriptCreated": False,
        "futurePredictionVerified": False,
    }
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "historical-actions.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for action in actions:
            stream.write(json.dumps(action, ensure_ascii=False, separators=(",", ":")) + "\n")
    (destination / "strategy.json").write_text(
        json.dumps(starts[0]["strategy"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--journal", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.capture, args.journal, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()

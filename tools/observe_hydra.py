"""Bounded, read-only capture of the existing agent's published Hydra state.

This does not load an agent, call game methods, issue commands, or access live
RNG objects. It can preserve RNG words published by a compatible newer agent.
Publication gaps are not a count of missing game actions. The agent publishes
snapshots, not an exhaustive game-event stream.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def utc():
    return datetime.now(timezone.utc).isoformat()


def objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def hunger(effect):
    return (effect.get("effectKind") == "HungerCounter" or effect.get("effectKindId") == 9020
            or effect.get("effectTypeId") == 680 or effect.get("skillTypeId") == 260006)


def published_rng(state):
    """Accept only an explicit complete field observation for this snapshot."""
    rng, battle = state.get("battleRandom"), state.get("battle")
    if not isinstance(rng, dict) or not isinstance(battle, dict):
        return None
    if (rng.get("schema") != 1 or rng.get("available") is not True
            or rng.get("source") != "BattleState.Random_fields"
            or rng.get("readStatus") != "stable_double_read"):
        return None
    words = rng.get("words")
    if not isinstance(words, list) or len(words) != 4 or not any(words):
        return None
    if any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in words):
        return None
    for key in ("turn", "playerTurnCount"):
        if type(rng.get(key)) is not int or rng[key] != battle.get(key):
            return None
    return rng


class Timeline:
    def __init__(self):
        self.previous = None
        self.generation = 0
        self.snapshots = 0
        self.mark_observations = 0
        self.rng_snapshots = 0

    def observe(self, state):
        battle = state.get("battle", {})
        if not isinstance(battle, dict) or not (battle.get("hydraBattle") is True or state.get("bossMode") == "hydra"):
            return []
        # Missing actor arrays must not be interpreted as everyone losing marks.
        if not isinstance(state.get("heroes"), list) or not isinstance(state.get("bosses"), list):
            return [{"event": "incomplete_snapshot"}]
        heroes, bosses = objects(state["heroes"]), objects(state["bosses"])
        marks = {str(h["id"]): {"id": h["id"], "typeId": h.get("typeId"), "name": h.get("name"),
                                "effects": [e for e in objects(h.get("effects")) if hunger(e)]}
                 for h in heroes if h.get("id") is not None and not h.get("dead")
                 and any(hunger(e) for e in objects(h.get("effects")))}
        victims = {}
        dead_ids = {h.get("id") for h in heroes if h.get("dead") is True}
        for h in heroes:
            if h.get("dead") is True:
                continue
            for e in objects(h.get("effects")):
                if e.get("effectKind") == "Devoured" or e.get("effectKindId") == 9024:
                    victims[str(h.get("id"))] = e.get("producerId")
        for h in bosses:
            if h.get("dead") is True:
                continue
            for source in [h, *objects(h.get("effects"))]:
                victim = source.get("devouredHeroId")
                if type(victim) is int and victim >= 0 and victim not in dead_ids:
                    victims[str(victim)] = h.get("id")
        current = {"context": state.get("pointers", {}).get("context"),
                   "nativeGeneration": state.get("battleGeneration"),
                   "turn": battle.get("turn"), "playerTurn": battle.get("playerTurnCount"),
                   "activeHeroId": state.get("activeHeroId"), "marks": marks, "victims": victims,
                   "dead": sorted(str(h["id"]) for h in heroes if h.get("dead") and "id" in h),
                   "heads": {str(h.get("id")): {k: h.get(k) for k in ("typeId", "headState", "dead", "isHydraNeck")} for h in bosses},
                   "finished": battle.get("finished") is True}
        prev = self.previous
        changed_context = prev and any(current[k] is not None and prev[k] is not None and current[k] != prev[k]
                                       for k in ("context", "nativeGeneration"))
        rewound = prev and type(current["playerTurn"]) is int and type(prev["playerTurn"]) is int and current["playerTurn"] < prev["playerTurn"]
        events = []
        if prev is None or changed_context or rewound:
            self.generation += 1
            events.append({"event": "first_observed_state", "openingObserved": current["playerTurn"] in (0, 1), "state": current})
            prev = None
        def applications(marked):
            return {(actor, effect.get("id"), effect.get("applyTurn"))
                    for actor, mark in marked.items() for effect in mark["effects"]}
        if prev is None or applications(current["marks"]) != applications(prev["marks"]):
            self.mark_observations += int(bool(marks))
            events.append({"event": "mark_observed", "before": prev["marks"] if prev else None, "after": marks})
        for key in ("victims", "dead", "heads", "finished"):
            if prev is not None and current[key] != prev[key]:
                events.append({"event": key + "_changed", "before": prev[key], "after": current[key]})
        self.previous = current
        self.snapshots += 1
        self.rng_snapshots += published_rng(state) is not None
        return [{"generation": self.generation, "turn": current["turn"], "playerTurn": current["playerTurn"], **e} for e in events]


def stable_publication(ipc, name):
    slot = getattr(ipc.state, name)
    for _ in range(3):
        before = int(slot.sequence)
        if before & 1:
            continue
        payload = ipc.read_text(name)
        after = int(slot.sequence)
        if before == after and not after & 1:
            return after, payload
    return None


class JournalTail:
    """Preserve new Hydra diagnostics, explicitly unbound to a PID until correlated."""
    def __init__(self, directory):
        self.directory = directory
        self.positions = {p: p.stat().st_size for p in directory.glob("decisions-*.jsonl")}

    def read(self):
        for path in self.directory.glob("decisions-*.jsonl"):
            try:
                with path.open("rb") as stream:
                    stream.seek(self.positions.get(path, 0))
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line.endswith(b"\n"):
                            self.positions[path] = start
                            break
                        self.positions[path] = stream.tell()
                        try:
                            value = json.loads(line)
                        except ValueError:
                            continue
                        if value.get("bossMode") == "hydra":
                            yield {"sourceFile": path.name, "pidBinding": "unverified", "payload": value}
            except FileNotFoundError:
                continue


def run(args):
    from agent_ipc import AgentIpc
    from hydra_capture_sidecar import BattleCacheCollector
    from hydra_replay_source import ReplaySourceCollector
    from strategy_storage import atomic_write_json
    directory = Path(args.output).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    timeline, counts, cursors = Timeline(), Counter(), {}
    natural_cache = BattleCacheCollector(directory)
    replay_source = ReplaySourceCollector(directory, args.account)
    data_root = Path(os.environ["LOCALAPPDATA"]) / "WFQ-GAFE"
    journals = [JournalTail(data_root / name / "logs") for name in
                ("RaidBossStrategyStudio", "RaidBossStrategyStudio-Flow-1.0.6")]
    started, first_battle, ended, last_status, last_journal = time.monotonic(), None, None, 0, 0
    next_battle_seen = False
    status = {"schema": 1, "pid": args.pid, "accountName": args.account, "phase": "connecting", "startedAt": utc(),
              "rngCaptured": False, "completeEventStream": False, "pollMilliseconds": args.poll_ms,
              "singleBattle": bool(getattr(args, "single_battle", False)),
              "limits": {"waitSeconds": args.wait_seconds, "battleSeconds": args.battle_seconds, "maxRawBytes": args.max_mib * 1024 * 1024},
              "journalPidBinding": "unverified", "publicationGaps": 0,
              "replayInput": replay_source.status,
              "naturalBattleCache": natural_cache.status}
    raw_bytes = 0
    reason = "unknown"
    with AgentIpc(args.pid) as ipc, gzip.open(directory / "raw.jsonl.gz", "wt", encoding="utf-8") as raw, \
            (directory / "timeline.jsonl").open("w", encoding="utf-8") as events:
        header = ipc.header()
        account = ipc.account() or {}
        if (not header["ready"] or account.get("accountName") != args.account
                or type(account.get("userId")) is not int or account["userId"] <= 0):
            raise ValueError("Agent readiness/account mismatch; no battle capture started")
        replay_source.observe_account(account)
        status["agent"] = header
        status["phase"] = "waiting_for_hydra"
        atomic_write_json(directory / "account-state-snapshot.json", {
            "schema": 1, "source": "AgentIpc.account",
            "pid": args.pid, "agentInstanceId": header["instanceId"],
            "agentBuildId": header.get("buildId"),
            "account": {"type": "account_state", "accountName": args.account,
                        "userId": account["userId"]},
        })
        atomic_write_json(directory / "manifest.json", status)

        def write(channel, value):
            nonlocal raw_bytes
            entry = {"time": utc(), "elapsedSeconds": round(time.monotonic() - started, 4), "channel": channel, **value}
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            raw.write(line)
            raw_bytes += len(line.encode("utf-8"))
            counts[channel] += 1

        try:
            while True:
                now = time.monotonic()
                if (directory / "STOP").exists():
                    reason = "stop_requested"
                    break
                if ipc.header()["instanceId"] != header["instanceId"] or not ipc.header()["ready"]:
                    reason = "agent_changed_or_not_ready"
                    break
                replay_source.observe_account(ipc.account())
                for channel in ("decision", "acknowledgement", "lifecycle", "battle_ledger", "diagnostic", "replay_input"):
                    if channel == "replay_input" and replay_source.terminal:
                        continue
                    if (channel == "replay_input" and
                            int(ipc.state.replay_input.sequence) == cursors.get(channel)):
                        continue
                    try:
                        publication = stable_publication(ipc, channel)
                    except (UnicodeError, ValueError):
                        if channel != "replay_input":
                            raise
                        replay_source.reject("replay_slot_read_invalid")
                        status["replayInput"] = replay_source.status
                        continue
                    if publication is None:
                        continue
                    sequence, payload = publication
                    previous = cursors.get(channel)
                    if sequence == previous:
                        continue
                    cursors[channel] = sequence
                    if previous is not None and sequence > previous + 2:
                        gap = (sequence - previous) // 2 - 1
                        status["publicationGaps"] += gap
                        write("publication_gap", {"slot": channel, "skippedPublications": gap})
                    if channel == "replay_input":
                        replay_source.observe_slot(sequence, payload)
                        status["replayInput"] = replay_source.status
                        if payload:
                            write(channel, {"slotSequence": sequence,
                                            "payload": replay_source.status})
                        continue
                    try:
                        value = json.loads(payload) if payload else None
                    except ValueError:
                        value = payload
                    write(channel, {"slotSequence": sequence, "payload": value})
                    natural_cache.observe(channel, value)
                    if channel == "decision" and isinstance(value, dict):
                        replay_source.observe_decision(value)
                        status["replayInput"] = replay_source.status
                        observations = timeline.observe(value)
                        if observations or timeline.snapshots:
                            if first_battle is None and timeline.snapshots:
                                first_battle = now
                                status["phase"] = "recording"
                            for observation in observations:
                                events.write(json.dumps({"time": utc(), **observation}, ensure_ascii=False) + "\n")
                            if timeline.previous and timeline.previous["finished"] and ended is None:
                                ended = now
                        if getattr(args, "single_battle", False) and timeline.generation >= 2:
                            next_battle_seen = True
                            break
                    if channel == "lifecycle" and isinstance(value, dict) and first_battle is not None:
                        if value.get("screen") == "result" and ended is None:
                            ended = now
                if next_battle_seen:
                    reason = "next_battle_observed"
                    break
                if now - last_journal >= 1:
                    for journal in journals:
                        for entry in journal.read():
                            write("controller_journal", {"sourceDirectory": str(journal.directory), **entry})
                    last_journal = now
                if now - last_status >= 2:
                    status["naturalBattleCache"] = natural_cache.maybe_capture(now)
                    status.update(updatedAt=utc(), elapsedSeconds=round(now - started), channels=dict(counts),
                                  rawBytes=raw_bytes, hydraSnapshots=timeline.snapshots, generations=timeline.generation,
                                  markedStatesObserved=timeline.mark_observations, latest=timeline.previous,
                                  rngCaptured=bool(timeline.rng_snapshots), rngSnapshotCount=timeline.rng_snapshots)
                    atomic_write_json(directory / "status.json", status)
                    raw.flush()
                    events.flush()
                    last_status = now
                if raw_bytes >= args.max_mib * 1024 * 1024:
                    reason = "size_limit"
                    break
                if first_battle is None and now - started >= args.wait_seconds:
                    reason = "no_battle_before_wait_limit"
                    break
                if first_battle is not None and now - first_battle >= args.battle_seconds:
                    reason = "battle_time_limit"
                    break
                if ended is not None and now - ended >= 15:
                    reason = "result_observed"
                    break
                time.sleep(args.poll_ms / 1000)
        except KeyboardInterrupt:
            reason = "interrupted"
        except Exception as error:
            reason = "recorder_error"
            status["error"] = repr(error)
            raise
        finally:
            replay_source.finalize()
            status["naturalBattleCache"] = natural_cache.status
            status.update(phase="stopped", stopReason=reason, updatedAt=utc(), channels=dict(counts), rawBytes=raw_bytes,
                          replayInput=replay_source.status,
                          hydraSnapshots=timeline.snapshots, generations=timeline.generation,
                          markedStatesObserved=timeline.mark_observations, latest=timeline.previous,
                          rngCaptured=bool(timeline.rng_snapshots), rngSnapshotCount=timeline.rng_snapshots)
            atomic_write_json(directory / "status.json", status)
    print(json.dumps({"directory": str(directory), "reason": reason, "hydraSnapshots": timeline.snapshots}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--poll-ms", type=int, default=100)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--battle-seconds", type=int, default=5400)
    parser.add_argument("--max-mib", type=int, default=512)
    parser.add_argument("--single-battle", action="store_true",
                        help="Stop when the next observed Hydra battle begins")
    options = parser.parse_args()
    if min(options.poll_ms, options.wait_seconds, options.battle_seconds, options.max_mib) <= 0:
        parser.error("All limits must be positive")
    run(options)

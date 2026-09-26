import copy
import argparse
import gzip
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from observe_hydra import Timeline, published_rng, run


def snapshot(turn=0, marked=1, context=100):
    return {"battle": {"hydraBattle": True, "playerTurnCount": turn, "turn": turn},
            "pointers": {"context": context}, "heroes": [
                {"id": 1, "effects": [{"effectKindId": 9020}] if marked == 1 else []},
                {"id": 2, "effects": [{"effectTypeId": 680}] if marked == 2 else []}], "bosses": []}


class TimelineTests(unittest.TestCase):
    def test_same_hero_new_effect_is_another_application(self):
        timeline = Timeline()
        state = snapshot()
        state["heroes"][0]["effects"][0].update(id=10, applyTurn=0)
        timeline.observe(state)
        state = copy.deepcopy(state)
        state["heroes"][0]["effects"][0].update(id=20, applyTurn=50)
        rows = timeline.observe(state)
        self.assertEqual(rows[0]["event"], "mark_observed")
        self.assertEqual(timeline.mark_observations, 2)

    def test_rng_requires_complete_explicit_matching_sample(self):
        state = snapshot()
        self.assertIsNone(published_rng(state))
        state["battleRandom"] = {"schema": 1, "available": True, "readStatus": "stable_double_read",
                                 "source": "BattleState.Random_fields", "turn": 0, "playerTurnCount": 0,
                                 "words": [0xFFFFFFFF, 2, 3, 4]}
        self.assertIsNotNone(published_rng(state))
        for key, bad in (("turn", 1), ("readStatus", "changed_during_read"), ("words", [1, 2, 3]),
                         ("words", [False, 2, 3, 4]), ("words", [1<<32, 2, 3, 4]), ("words", [0, 0, 0, 0])):
            changed = copy.deepcopy(state)
            changed["battleRandom"][key] = bad
            self.assertIsNone(published_rng(changed))

    def test_first_midfight_snapshot_is_not_an_opening_prediction(self):
        timeline = Timeline()
        state = snapshot(90)
        original = copy.deepcopy(state)
        rows = timeline.observe(state)
        self.assertFalse(rows[0]["openingObserved"])
        self.assertEqual(state, original)
        self.assertEqual(timeline.mark_observations, 1)
        self.assertEqual(timeline.observe(state), [])

    def test_clear_then_same_target_is_observed_again(self):
        timeline = Timeline()
        timeline.observe(snapshot())
        timeline.observe(snapshot(2, None))
        rows = timeline.observe(snapshot(3, 1))
        self.assertEqual([r["event"] for r in rows], ["mark_observed"])
        self.assertEqual(timeline.mark_observations, 2)

    def test_missing_actors_does_not_clear_mark(self):
        timeline = Timeline()
        timeline.observe(snapshot())
        state = snapshot(1)
        del state["heroes"]
        self.assertEqual(timeline.observe(state)[0]["event"], "incomplete_snapshot")
        self.assertIn("1", timeline.previous["marks"])

    def test_context_or_rewound_turn_starts_new_observation(self):
        timeline = Timeline()
        timeline.observe(snapshot(30))
        rows = timeline.observe(snapshot(0))
        self.assertEqual(rows[0]["event"], "first_observed_state")
        self.assertEqual(rows[0]["generation"], 2)
        timeline.observe(snapshot(0, context=200))
        self.assertEqual(timeline.generation, 3)

    def test_victim_effect_links_head_when_top_level_is_missing(self):
        timeline = Timeline()
        timeline.observe(snapshot())
        state = snapshot(1)
        state["heroes"][0]["effects"].append({"effectKindId": 9024, "producerId": 99})
        rows = timeline.observe(state)
        self.assertEqual(next(r for r in rows if r["event"] == "victims_changed")["after"], {"1": 99})

    def test_dead_victims_retaining_effects_are_not_still_being_digested(self):
        timeline = Timeline()
        state = snapshot()
        state["heroes"][0].update(dead=True, effects=[{"effectKindId": 9024, "producerId": 99}])
        state["bosses"] = [{"id": 99, "devouredHeroId": 1}]
        timeline.observe(state)
        self.assertEqual(timeline.previous["victims"], {})

    def test_single_battle_stops_after_preserving_first_decision(self):
        class FakeIpc:
            def __init__(self):
                self.state = SimpleNamespace(**{
                    name: SimpleNamespace(sequence=2 if name == "decision" else 0)
                    for name in ("decision", "acknowledgement", "lifecycle",
                                 "battle_ledger", "diagnostic", "replay_input")})
                first = {**snapshot(context=100), "type": "decision_state",
                         "bossMode": "hydra", "battleGeneration": 1}
                second = {**snapshot(context=200), "type": "decision_state",
                          "bossMode": "hydra", "battleGeneration": 2}
                self.decisions = [json.dumps(first), json.dumps(second)]
                self.index = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def header(self):
                return {"ready": True, "instanceId": 7}

            def account(self):
                return {"type": "account_state", "accountName": "test",
                        "userId": 123}

            def read_text(self, name):
                return self.decisions[self.index] if name == "decision" else None

        class FakeCache:
            def __init__(self, _):
                self.status = {"captured": False}

            def observe(self, *_):
                return None

            def maybe_capture(self, _):
                return self.status

        ipc = FakeIpc()

        def advance(_):
            ipc.index = 1
            ipc.state.decision.sequence = 4

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            options = argparse.Namespace(pid=1234, account="test", output=output,
                                         poll_ms=1, wait_seconds=1000,
                                         battle_seconds=1000, max_mib=1,
                                         single_battle=True)
            with (mock.patch("agent_ipc.AgentIpc", return_value=ipc),
                  mock.patch("hydra_capture_sidecar.BattleCacheCollector", FakeCache),
                  mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}),
                  mock.patch("observe_hydra.time.sleep", side_effect=advance)):
                run(options)
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["stopReason"], "next_battle_observed")
            self.assertEqual(status["generations"], 2)
            account = json.loads((output / "account-state-snapshot.json").read_text(
                encoding="utf-8"))
            self.assertEqual(account["account"]["userId"], 123)
            self.assertEqual(account["agentInstanceId"], 7)
            with gzip.open(output / "raw.jsonl.gz", "rt", encoding="utf-8") as stream:
                decisions = [json.loads(line) for line in stream
                             if json.loads(line).get("channel") == "decision"]
            self.assertEqual(len(decisions), 2)
            self.assertEqual(decisions[0]["payload"]["battleGeneration"], 1)


if __name__ == "__main__":
    unittest.main()

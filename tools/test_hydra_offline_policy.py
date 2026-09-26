"""Pure policy bridge tests; historical capture is optional local evidence."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import unittest

from hydra_offline_policy import HydraOfflinePolicySession, run_json_lines


def fixture() -> tuple[dict, dict]:
    state = {
        "type": "decision_state", "sequence": 1, "battleGeneration": 4,
        "bossMode": "hydra",
        "battle": {"hydraBattle": True, "finished": False,
                   "waitingForManualCommand": True, "round": 1,
                   "turn": 3, "playerTurnCount": 1},
        "activeHeroId": 0, "activeHeroTypeId": 100,
        "activeHeroTurnCount": 1, "activeHeroFormIndex": 0,
        "hydra": {"active": True, "turnCount": 3},
        "skills": [{"skillId": 0, "slot": 1, "typeId": 1001,
                    "ready": True, "passive": False, "blocked": False,
                    "cooldown": 0, "validTargetIds": [8]}],
        "heroes": [{"id": 0, "typeId": 100, "dead": False,
                    "healthPct": 100.0, "effects": [], "states": {}}],
        "bosses": [{"id": 8, "typeId": 26040, "dead": False,
                    "healthPct": 80.0, "effects": [], "states": {},
                    "isHydraNeck": False, "isDevouring": False,
                    "devouredHeroId": -1}],
    }
    strategy = {"bossMode": "hydra", "rules": [{
        "name": "one-legal-hit", "when": {"activeHeroTypeId": [100]},
        "action": {"type": "cast", "skillTypeId": 1001, "skillSlot": 1,
                   "target": {"type": "boss"}},
    }]}
    return state, strategy


class OfflinePolicyBridgeTests(unittest.TestCase):
    def test_returns_only_legal_command_and_preserves_input(self):
        state, strategy = fixture()
        original = copy.deepcopy(state)
        result = HydraOfflinePolicySession(strategy).decide(state)
        self.assertEqual(result["status"], "command")
        self.assertEqual(result["command"], {
            "skillId": 0, "skillSlot": 1,
            "skillTypeId": 1001, "targetId": 8,
        })
        self.assertEqual(state, original)
        self.assertFalse(result["futurePredictionVerified"])

    def test_missing_skill_or_effect_state_returns_unknown_and_stops(self):
        state, strategy = fixture()
        session = HydraOfflinePolicySession(strategy)
        state["bosses"][0].pop("effects")
        result = session.decide(state)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "head_effects_or_states_missing")
        state, _ = fixture()
        self.assertEqual(session.decide(state)["reason"], "session_stopped_after_unknown")

    def test_unavailable_skill_never_falls_back_to_default(self):
        state, strategy = fixture()
        state["skills"][0]["ready"] = False
        state["skills"][0]["validTargetIds"] = []
        result = HydraOfflinePolicySession(strategy).decide(state)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["command"])

    def test_second_turn_requires_monotonic_state_and_same_generation(self):
        state, strategy = fixture()
        session = HydraOfflinePolicySession(strategy)
        self.assertEqual(session.decide(state)["status"], "command")
        repeated = copy.deepcopy(state)
        repeated["sequence"] += 1
        self.assertEqual(session.decide(repeated)["reason"], "decision_turn_not_monotonic")
        session = HydraOfflinePolicySession(strategy)
        self.assertEqual(session.decide(state)["status"], "command")
        changed = copy.deepcopy(state)
        changed["sequence"] += 1
        changed["battle"]["turn"] += 1
        changed["battle"]["playerTurnCount"] += 1
        changed["battleGeneration"] += 1
        self.assertEqual(session.decide(changed)["reason"], "battle_generation_changed")

    def test_forecast_mode_requires_battle_bound_rng(self):
        state, strategy = fixture()
        result = HydraOfflinePolicySession(strategy, require_rng=True).decide(state)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "battle_rng_checkpoint_missing")

    def test_json_lines_protocol_stops_on_first_unknown(self):
        state, strategy = fixture()
        valid = {"schema": 1, "type": "decision_state", "state": state}
        invalid = {"schema": 1, "type": "decision_state", "state": {}}
        source = io.StringIO("\n".join(map(json.dumps, (valid, invalid, valid))) + "\n")
        destination = io.StringIO()
        run_json_lines(HydraOfflinePolicySession(strategy), source, destination)
        results = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual([item["status"] for item in results], ["command", "unknown"])

    def test_historical_gafee_239_policy_choices(self):
        root = Path(__file__).resolve().parents[1] / "out"
        directories = [root / "hydra-observation-20260920-2348-gafee",
                       root / "hydra-observation-20260920-2352-gafee-detail"]
        if not all((directory / "raw.jsonl.gz").is_file() for directory in directories):
            self.skipTest("Local historical capture is unavailable")
        from verify_hydra_policy_replay import collect_rows, verify

        states, journal, _ = collect_rows(directories)
        report = verify(directories)
        starts = [row for row in journal if row.get("event") == "start"
                  and isinstance(row.get("strategy"), dict)]
        self.assertEqual(len(starts), 1)
        session = HydraOfflinePolicySession(starts[0]["strategy"])
        actions = report["recordedActionTrace"]
        self.assertEqual(len(actions), 239)
        for number, expected in enumerate(actions, 1):
            actual = session.decide(states[expected["nativeSequence"]])
            self.assertEqual(actual["status"], "command", (number, actual))
            self.assertEqual(actual["command"]["skillTypeId"],
                             expected["command"]["skillTypeId"], number)
            self.assertEqual(actual["command"]["targetId"],
                             expected["command"]["targetId"], number)


if __name__ == "__main__":
    unittest.main()

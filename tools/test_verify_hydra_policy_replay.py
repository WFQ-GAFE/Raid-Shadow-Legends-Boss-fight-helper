import unittest
import json
import tempfile
from pathlib import Path

from verify_hydra_policy_replay import (
    command_script_tsv, decision_matches_native, external_journal_rows,
    recorded_action, valid_rng_for_action,
)


class RecordedActionTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "sequence": 17,
            "battleGeneration": 4,
            "activeHeroId": 2,
            "activeHeroTypeId": 9906,
            "activeHeroTurnCount": 3,
            "activeHeroFormIndex": 0,
            "activeHeroSkillsUpdateCounter": 1,
            "battle": {"round": 1, "turn": 9, "playerTurnCount": 6},
            "skills": [{
                "slot": 2, "skillId": 1, "typeId": 99002,
                "ready": True, "passive": False,
                "validTargetIds": [8, 9],
            }],
            "heroes": [{"id": 2, "typeId": 9906}],
            "bosses": [{"id": 8, "typeId": 26140}],
        }
        self.logged = {"skillTypeId": 99002, "skillSlot": 2, "targetId": 8}

    def test_records_legal_policy_matched_command_and_state_hash(self):
        result = recorded_action(self.state, self.logged, 5, True)

        self.assertTrue(result["actionWasLegalInCapturedState"])
        self.assertTrue(result["policyMatchedSubmittedAction"])
        self.assertEqual(result["command"], {
            "skillId": 1,
            "skillSlot": 2,
            "skillTypeId": 99002,
            "targetId": 8,
            "targetTypeId": 26140,
        })
        self.assertEqual(len(result["preStateSha256"]), 64)

    def test_rejects_target_outside_captured_legal_target_set(self):
        self.logged["targetId"] = 7

        result = recorded_action(self.state, self.logged, 5, True)

        self.assertFalse(result["actionWasLegalInCapturedState"])

    def test_keeps_random_state_explicitly_missing(self):
        result = recorded_action(self.state, self.logged, 5, True)

        self.assertIsNone(result["battleRandomBefore"])

    def test_replay_rng_requires_stable_words_and_matching_turn(self):
        self.state["battleRandom"] = {
            "schema": 1, "source": "BattleState.Random_fields",
            "capturePoint": "published_snapshot", "available": True,
            "readStatus": "stable_double_read", "words": [1, 2, 3, 4],
            "turn": 9, "playerTurnCount": 6, "seedAvailable": True,
            "seed": 42, "battleSetupIdAvailable": True,
            "battleSetupId": "1" * 32,
        }
        action = recorded_action(self.state, self.logged, 5, True)
        self.assertTrue(valid_rng_for_action(action))
        for field, value in (("available", False), ("words", [0, 0, 0, 0]),
                             ("turn", 10), ("seedAvailable", False),
                             ("battleSetupId", "0" * 32)):
            changed = {**action, "battleRandomBefore": {
                **action["battleRandomBefore"], field: value}}
            self.assertFalse(valid_rng_for_action(changed), field)

    def test_serializes_only_verified_actions_for_native_replay(self):
        action = recorded_action(self.state, self.logged, 5, True)

        lines = command_script_tsv([action]).splitlines()

        self.assertEqual(lines[0], "scope\tcontroller_submitted_skill_commands_only")
        self.assertEqual(lines[1], "schema\t1")
        self.assertIn("\t1\t9\t6\t2\t9906\t1\t99002\t8\t", lines[3])

    def test_refuses_a_policy_mismatch(self):
        action = recorded_action(self.state, self.logged, 5, False)

        with self.assertRaises(ValueError):
            command_script_tsv([action])

    def test_copied_journal_binds_to_exact_native_turn_and_tick(self):
        self.state["observedAtTick"] = 12345
        row = {"decision": {"sequence": 17, "context": {
            "battleGeneration": 4, "observedAtTick": 12345,
            "battle": {"round": 1, "turn": 9, "playerTurnCount": 6},
            "activeHero": {"activeHeroId": 2, "activeHeroTypeId": 9906},
        }}}
        self.assertTrue(decision_matches_native(row, self.state))
        row["decision"]["context"]["observedAtTick"] += 1
        self.assertFalse(decision_matches_native(row, self.state))

    def test_only_completed_final_controller_copy_is_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sample = {"bossMode": "hydra", "event": "decision", "run": "sample"}
            (directory / "controller-decisions-live-snapshot.jsonl").write_text(
                json.dumps(sample) + "\n", encoding="utf-8")
            self.assertEqual(external_journal_rows(directory), [])
            (directory / "controller-decisions-final.jsonl").write_text(
                json.dumps(sample) + "\n", encoding="utf-8")
            self.assertEqual(external_journal_rows(directory), [sample])


if __name__ == "__main__":
    unittest.main()

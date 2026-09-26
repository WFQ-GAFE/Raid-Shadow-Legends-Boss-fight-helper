"""The shared replay slot is accepted only for the observed opening battle."""
from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from agent_ipc import (AGENT_BUILD_ID, SHARED_STATE_VERSION, AgentSharedState,
                       DiagnosticJsonSlot, ReplayInputJsonSlot)
from hydra_replay_source import (ReplaySourceCollector, ReplaySourceError,
                                 SETTINGS_SOURCE, SETUPS_SOURCE,
                                 validate_replay_source)


def records() -> tuple[dict, dict, dict]:
    guid = "00112233-4455-6677-8899-aabbccddeeff"
    native_guid = uuid.UUID(guid).bytes_le.hex()
    heroes = [{"id": slot, "typeId": 10000 + slot,
               "modelFound": True, "battlePosition": slot}
              for slot in range(6)]
    setup = {"z": guid, "r": -123456, "k": 5, "i": 8019001,
             "f": {"i": 123456789,
                   "h": [{"t": slot, "h": 900000 + slot, "i": hero["typeId"]}
                         for slot, hero in enumerate(heroes)]}}
    source = {"schema": 1, "type": "hydra_replay_source", "status": "captured",
              "battleGeneration": 4, "battleSetupId": native_guid,
              "seed": -123456, "stageId": 8019001,
              "battleSetupsJson": json.dumps([setup], separators=(",", ":")),
              "battleSettingsJson": '{"ActiveEngineVersion":1,"WarmupBattleRandomCount":13,"MaxTurnsInBattle":1000,"b":{"x":2}}',
              "battleSetupsSource": SETUPS_SOURCE,
              "battleSettingsSource": SETTINGS_SOURCE,
              "observedAtTick": 2000}
    decision = {"type": "decision_state", "bossMode": "hydra",
                "battleGeneration": 4, "observedAtTick": 1000,
                "chimeraStageId": 8019001,
                "hydraStartSelection": {"stageId": 8019001,
                                        "heroIds": [900000 + slot for slot in range(6)],
                                        "heroTypeIds": [10000 + slot for slot in range(6)]},
                "battle": {"hydraBattle": True, "kindId": 5,
                           "turn": 1, "playerTurnCount": 0, "finished": False},
                "battleRandom": {"schema": 1, "available": True,
                                 "source": "BattleState.Random_fields",
                                 "readStatus": "stable_double_read",
                                 "turn": 1, "playerTurnCount": 0,
                                 "words": [1, 2, 3, 4],
                                 "seedAvailable": True, "seed": -123456,
                                 "battleSetupIdAvailable": True,
                                 "battleSetupId": native_guid},
                "heroes": heroes}
    account = {"type": "account_state", "accountName": "test-account",
               "userId": 123456789}
    return source, decision, account


class ReplaySourceTests(unittest.TestCase):
    def test_shared_slot_layout_and_version(self):
        self.assertEqual((SHARED_STATE_VERSION, AGENT_BUILD_ID), (5, 2026092702))
        self.assertEqual(ctypes.sizeof(ReplayInputJsonSlot), 2_097_168)
        self.assertEqual(AgentSharedState.replay_input.offset,
                         AgentSharedState.diagnostic.offset + ctypes.sizeof(DiagnosticJsonSlot))
        self.assertEqual(ctypes.sizeof(AgentSharedState), 2_719_920)

    def test_saves_original_bounded_json_once_after_opening_identity(self):
        source, decision, account = records()
        with tempfile.TemporaryDirectory() as temporary:
            collector = ReplaySourceCollector(Path(temporary), "test-account")
            payload = json.dumps(source)
            collector.observe_slot(2, payload)
            self.assertEqual(collector.status["status"], "waiting_for_opening_identity")
            self.assertEqual((Path(temporary) / "replay-source-candidate.json").read_text(),
                             payload)
            collector.observe_account(account)
            collector.observe_decision(decision)
            self.assertTrue(collector.status["captured"])
            output = Path(temporary) / "replay-source"
            self.assertEqual((output / "battle-setup.json").read_text(),
                             source["battleSetupsJson"])
            self.assertEqual((output / "battle-settings.json").read_text(),
                             source["battleSettingsJson"])
            provenance = json.loads((output / "capture-provenance.json").read_text())
            self.assertEqual(provenance["accountUserId"], account["userId"])
            self.assertEqual(provenance["teamHeroTypeIds"],
                             [10000 + slot for slot in range(6)])
            collector.observe_slot(4, json.dumps({**source, "seed": 1}))
            self.assertEqual(len(list(output.iterdir())), 3)

    def test_rejects_each_independent_identity_mismatch(self):
        source, decision, account = records()
        cases = []
        changed = copy.deepcopy(source)
        changed["battleSetupId"] = "f" * 32
        cases.append((changed, decision, account, "source_battleSetupId_differs_from_opening"))
        changed = copy.deepcopy(source)
        changed["seed"] += 1
        cases.append((changed, decision, account, "source_seed_differs_from_opening"))
        changed = copy.deepcopy(source)
        changed["stageId"] += 1
        cases.append((changed, decision, account, "source_stageId_differs_from_opening"))
        changed = copy.deepcopy(source)
        setup = json.loads(changed["battleSetupsJson"])
        setup[0]["f"]["h"][0]["i"] += 1
        changed["battleSetupsJson"] = json.dumps(setup)
        cases.append((changed, decision, account, "battle_setup_team_mismatch"))
        changed = copy.deepcopy(source)
        setup = json.loads(changed["battleSetupsJson"])
        setup[0]["f"]["h"][0]["h"] += 1
        changed["battleSetupsJson"] = json.dumps(setup)
        cases.append((changed, decision, account, "battle_setup_team_mismatch"))
        changed = copy.deepcopy(account)
        changed["userId"] += 1
        cases.append((source, decision, changed, "battle_setup_owner_mismatch"))
        changed = copy.deepcopy(decision)
        changed["battle"]["playerTurnCount"] = 3
        changed["battleRandom"]["playerTurnCount"] = 3
        cases.append((source, changed, account, "opening_decision_not_at_start"))
        changed = copy.deepcopy(decision)
        del changed["hydraStartSelection"]
        cases.append((source, changed, account, "opening_start_selection_missing"))
        changed = copy.deepcopy(decision)
        for hero in changed["heroes"]:
            hero["battlePosition"] = -1
        cases.append((source, changed, account, "opening_team_positions_missing"))
        for candidate, opening, game_account, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ReplaySourceError, reason):
                validate_replay_source(candidate, opening, game_account, "test-account")

    def test_rejects_wrong_payload_shapes_sources_and_duplicate_keys(self):
        source, decision, account = records()
        for field, replacement, reason in (
            ("battleSetupsJson", "{}", "battle_setups_not_single_list"),
            ("battleSettingsJson", "[]", "battle_settings_not_object"),
            ("battleSettingsJson", '{"ActiveEngineVersion":1}',
             "battle_settings_WarmupBattleRandomCount_invalid"),
            ("battleSetupsJson", '[{"z":1,"z":2}]', "duplicate_json_key"),
            ("battleSettingsSource", "guessed", "source_origin_invalid"),
        ):
            with self.subTest(field=field, replacement=replacement):
                changed = {**source, field: replacement}
                with self.assertRaisesRegex(ReplaySourceError, reason):
                    validate_replay_source(changed, decision, account, "test-account")

    def test_unavailable_or_unmatched_source_does_not_write_files(self):
        source, decision, account = records()
        with tempfile.TemporaryDirectory() as temporary:
            collector = ReplaySourceCollector(Path(temporary), "test-account")
            collector.observe_slot(2, json.dumps({"schema": 1,
                "type": "hydra_replay_source", "status": "unavailable",
                "reason": "settings_missing"}))
            self.assertEqual(collector.status["reason"], "settings_missing")
            self.assertFalse((Path(temporary) / "replay-source").exists())
        with tempfile.TemporaryDirectory() as temporary:
            collector = ReplaySourceCollector(Path(temporary), "test-account")
            collector.observe_account(account)
            collector.observe_decision({**decision, "battleGeneration": 3})
            collector.observe_slot(2, json.dumps(source))
            collector.finalize()
            self.assertEqual(collector.status["reason"],
                             "matching_opening_decision_unavailable")
            self.assertFalse((Path(temporary) / "replay-source").exists())
            self.assertTrue((Path(temporary) / "replay-source-candidate.json").is_file())

    def test_rejects_account_change_before_source_is_saved(self):
        source, _, account = records()
        with tempfile.TemporaryDirectory() as temporary:
            collector = ReplaySourceCollector(Path(temporary), "test-account")
            collector.observe_account(account)
            collector.observe_slot(2, json.dumps(source))
            collector.observe_account({**account, "userId": 999})
            self.assertEqual(collector.status["reason"], "game_account_changed")
            self.assertFalse((Path(temporary) / "replay-source").exists())

    def test_incomplete_first_opening_is_replaced_by_complete_decision(self):
        source, decision, account = records()
        incomplete = copy.deepcopy(decision)
        incomplete["heroes"] = incomplete["heroes"][:1]
        with tempfile.TemporaryDirectory() as temporary:
            collector = ReplaySourceCollector(Path(temporary), "test-account")
            collector.observe_account(account)
            collector.observe_slot(2, json.dumps(source))
            collector.observe_decision(incomplete)
            self.assertIsNone(collector.opening)
            self.assertFalse(collector.status["captured"])
            collector.observe_decision(decision)
            self.assertTrue(collector.status["captured"])
            self.assertTrue((Path(temporary) / "replay-source" /
                             "capture-provenance.json").is_file())


if __name__ == "__main__":
    unittest.main()

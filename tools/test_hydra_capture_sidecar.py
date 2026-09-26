"""The natural cache sidecar must be bound to the same observed battle."""
from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock

from hydra_capture_sidecar import BattleCacheCollector


def records() -> tuple[dict, dict]:
    lifecycle = {"type": "lifecycle_state", "screen": "battle", "battleGeneration": 4,
                 "battle": {"bossMode": "hydra", "context": 555,
                            "stageId": 8039003, "heroIds": [10, 11, 12, 13, 14, 15],
                            "heroTypeIds": [100, 100, 102, 103, 104, 105]}}
    decision = {"type": "decision_state", "bossMode": "hydra", "battleGeneration": 4,
                "pointers": {"context": 555},
                "battle": {"hydraBattle": True, "turn": 1, "playerTurnCount": 0},
                "battleRandom": {"schema": 1, "available": True,
                                 "source": "BattleState.Random_fields",
                                 "readStatus": "stable_double_read",
                                 "turn": 1, "playerTurnCount": 0,
                                 "words": [1, 2, 3, 4], "seedAvailable": True,
                                 "seed": -123, "battleSetupIdAvailable": True,
                                 "battleSetupId": "0102030405060708090a0b0c0d0e0f10"}}
    return lifecycle, decision


class BattleCacheCollectorTests(TestCase):
    def test_waits_for_same_battle_then_retries_and_saves_once(self):
        sidecar = BattleCacheCollector(Path("out/test"))
        lifecycle, decision = records()
        sidecar.observe("lifecycle", lifecycle)
        with mock.patch("hydra_capture_sidecar.capture") as read_cache:
            self.assertEqual(sidecar.maybe_capture(0)["status"], "waiting_for_battle_identity")
            sidecar.observe("decision", decision)
            read_cache.side_effect = [
                {"status": "cache_value_missing", "registryKeysScanned": 11},
                {"status": "verified_battle_setup_saved", "outputDirectory": "out/test/cache"},
            ]
            self.assertEqual(sidecar.maybe_capture(0)["status"], "cache_value_missing")
            self.assertEqual(sidecar.maybe_capture(1)["attempts"], 1)
            self.assertTrue(sidecar.maybe_capture(6)["captured"])
            self.assertEqual(sidecar.maybe_capture(12)["attempts"], 2)
            self.assertEqual(read_cache.call_count, 2)
            first = read_cache.call_args_list[0].args[0]
            self.assertEqual(first.seed, -123)
            self.assertEqual(first.stage_id, 8039003)
            self.assertEqual(first.hero_instance_ids, [10, 11, 12, 13, 14, 15])

    def test_rejects_cross_generation_or_context_or_incomplete_rng(self):
        lifecycle, decision = records()
        for change in ("generation", "context", "rng"):
            sidecar = BattleCacheCollector(Path("out/test"))
            bad = dict(decision)
            if change == "generation":
                bad["battleGeneration"] = 5
            elif change == "context":
                bad["pointers"] = {"context": 556}
            else:
                bad["battleRandom"] = {**decision["battleRandom"], "available": False}
            sidecar.observe("lifecycle", lifecycle)
            sidecar.observe("decision", bad)
            with mock.patch("hydra_capture_sidecar.capture") as read_cache:
                self.assertEqual(sidecar.maybe_capture(0)["attempts"], 0)
                read_cache.assert_not_called()

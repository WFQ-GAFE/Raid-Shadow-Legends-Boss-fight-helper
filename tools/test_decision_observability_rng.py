"""Controller journal keeps the native RNG bound to each Hydra decision."""
from __future__ import annotations

import unittest

from decision_observability import decision_context


class HydraDecisionRngJournalTests(unittest.TestCase):
    def test_hydra_context_preserves_battle_identity_and_rng_without_mutation(self):
        rng = {"schema": 1, "available": True,
               "source": "BattleState.Random_fields",
               "capturePoint": "published_snapshot",
               "readStatus": "stable_double_read",
               "words": [1, 2, 3, 4], "turn": 9,
               "playerTurnCount": 7,
               "seedAvailable": True, "seed": 42,
               "battleSetupIdAvailable": True,
               "battleSetupId": "a" * 32,
               "unrelatedPointer": 12345678}
        state = {"bossMode": "hydra", "battleRandom": rng,
                 "battle": {"turn": 9, "round": 1,
                            "playerTurnCount": 7},
                 "heroes": [], "bosses": []}
        context = decision_context(state)
        self.assertEqual(context["battleRandom"]["words"], [1, 2, 3, 4])
        self.assertEqual(context["battleRandom"]["battleSetupId"], "a" * 32)
        self.assertNotIn("unrelatedPointer", context["battleRandom"])
        context["battleRandom"]["words"][0] = 99
        self.assertEqual(rng["words"], [1, 2, 3, 4])

    def test_other_boss_context_does_not_add_rng(self):
        context = decision_context({"bossMode": "chimera", "heroes": [],
                                    "bosses": [], "battleRandom": {"words": [1, 2, 3, 4]}})
        self.assertNotIn("battleRandom", context)


if __name__ == "__main__":
    unittest.main()

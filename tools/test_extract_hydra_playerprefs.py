from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
import unittest
import uuid

from tools.extract_hydra_playerprefs import (
    cache_user_id_from_value_name,
    decode_cache_value,
    select_exact_battle,
    summarize_hydra_setup,
)


def fixture() -> tuple[dict, str]:
    battle_id = "00112233-4455-6677-8899-aabbccddeeff"
    model = {
        "z": battle_id,
        "r": -123456,
        "k": 5,
        "i": 8019001,
        "f": {
            "i": 123456789,
            "h": [
                {"t": slot, "h": 900000 + slot, "i": 10000 + slot, "s": [{"i": 100000 + slot, "l": 6}]}
                for slot in range(6)
            ]
        },
    }
    return model, battle_id


class ExtractHydraPlayerPrefsTests(unittest.TestCase):
    def test_bounded_game_cache_decode_and_exact_battle_match(self):
        model, battle_id = fixture()
        encoded = base64.b64encode(gzip.compress(json.dumps([model]).encode())).decode()
        decoded = decode_cache_value(encoded)
        raw_guid = uuid.UUID(battle_id).bytes_le.hex()
        matched, summary = select_exact_battle(
            decoded, raw_guid, -123456, [900000 + index for index in range(6)],
            123456789,
        )
        self.assertEqual(matched, model)
        self.assertEqual(summary["stageId"], 8019001)
        self.assertEqual(summary["heroTypeIds"], [10000 + index for index in range(6)])

    def test_mismatched_team_or_seed_refuses_capture(self):
        model, battle_id = fixture()
        with self.assertRaisesRegex(ValueError, "seed differs"):
            select_exact_battle([model], battle_id, 5,
                                [900000 + index for index in range(6)], 123456789)
        with self.assertRaisesRegex(ValueError, "hero instances"):
            select_exact_battle([model], battle_id, -123456, [1, 2, 3, 4, 5, 6], 123456789)
        with self.assertRaisesRegex(ValueError, "team owner"):
            select_exact_battle([model], battle_id, -123456,
                                [900000 + index for index in range(6)], 987654321)
        with self.assertRaisesRegex(ValueError, "stage differs"):
            select_exact_battle([model], battle_id, -123456,
                                [900000 + index for index in range(6)], 123456789,
                                stage_id=8039003)
        with self.assertRaisesRegex(ValueError, "hero types"):
            select_exact_battle([model], battle_id, -123456,
                                [900000 + index for index in range(6)], 123456789,
                                hero_type_ids=[1, 2, 3, 4, 5, 6])

    def test_incomplete_team_is_rejected(self):
        model, _ = fixture()
        model["f"]["h"].pop()
        with self.assertRaisesRegex(ValueError, "exactly six"):
            summarize_hydra_setup(model)

    def test_real_battle_one_based_slots_preserve_team_order(self):
        model, _ = fixture()
        for hero in model["f"]["h"]:
            hero["t"] += 1
        model["f"]["h"].reverse()
        summary = summarize_hydra_setup(model)
        self.assertEqual(summary["inventoryHeroIds"],
                         [900000 + index for index in range(6)])
        model["f"]["h"][1]["t"] = 0
        with self.assertRaisesRegex(ValueError, "duplicated or incomplete"):
            summarize_hydra_setup(model)

    def test_oversized_or_wrong_format_cache_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_cache_value("not base64")
        with self.assertRaises(ValueError):
            decode_cache_value(base64.b64encode(b"not-gzip").decode())

    def test_original_game_json_fixture_and_account_binding(self):
        path = Path(__file__).parent / "fixtures" / "hydra-setup-cache-original-json.json"
        original_json = path.read_bytes().strip()
        models = decode_cache_value(base64.b64encode(gzip.compress(original_json)).decode())
        cache_user_id = cache_user_id_from_value_name(
            "Production_BattleSetupCache_123456789_h3232628825"
        )
        model, summary = select_exact_battle(
            models, "0102030405060708090a0b0c0d0e0f10", -123456,
            [900001, 900002, 900003, 900004, 900005, 900006], cache_user_id,
        )
        self.assertEqual(summary["battleSetupIdJson"], "04030201-0605-0807-090a-0b0c0d0e0f10")
        self.assertEqual(summary["teamOwnerId"], 123456789)
        self.assertEqual(model["f"]["i"], cache_user_id)
        with self.assertRaisesRegex(ValueError, "team owner"):
            select_exact_battle(models, "0102030405060708090a0b0c0d0e0f10", -123456,
                                [900001, 900002, 900003, 900004, 900005, 900006],
                                cache_user_id_from_value_name("BattleSetupCache_987654321"))
        with self.assertRaisesRegex(ValueError, "account"):
            cache_user_id_from_value_name("BattleSetupCache_invalid")


if __name__ == "__main__":
    unittest.main()

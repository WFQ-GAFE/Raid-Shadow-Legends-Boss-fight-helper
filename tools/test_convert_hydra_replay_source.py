"""The copied Hydra input converts only with a matching isolated-runtime report."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from convert_hydra_replay_source import ConversionError, convert, validate_conversion


SETUP = b'[{"z":"04030201-0605-0807-090a-0b0c0d0e0f10"}]'
SETTINGS = b'{"ActiveEngineVersion":1,"WarmupBattleRandomCount":13,"MaxTurnsInBattle":1000}'
GUID = "0102030405060708090a0b0c0d0e0f10"
HERO_IDS = [900001, 900002, 900003, 900004, 900005, 900006]


def source_provenance() -> dict:
    return {
        "schema": 1, "type": "verified_hydra_replay_source",
        "battleGeneration": 4, "battleSetupId": GUID, "seed": -123456,
        "stageId": 8019001, "accountUserId": 123456789,
        "teamHeroIds": HERO_IDS,
        "activeEngineVersion": 1, "warmupBattleRandomCount": 13,
        "maxTurnsInBattle": 1000,
        "battleSetups": {"file": "battle-setup.json", "bytes": len(SETUP),
                         "sha256": hashlib.sha256(SETUP).hexdigest()},
        "battleSettings": {"file": "battle-settings.json", "bytes": len(SETTINGS),
                           "sha256": hashlib.sha256(SETTINGS).hexdigest()},
    }


def converted_wrapper() -> dict:
    return {
        "childExitCode": 0, "timedOut": False, "nativeFault": None,
        "observation": {
            "schema": 1, "synthetic": False,
            "appContainer": True, "capabilityCount": 0,
            "parentMemoryAccessDenied": True,
            "phase": "original_setup_messagepack_executed",
            "guidBytesHex": GUID, "seed": -123456,
            "stageId": 8019001, "teamOwnerId": 123456789,
            "inventoryHeroIds": HERO_IDS,
            "activeEngineVersion": 1, "warmupBattleRandomCount": 13,
            "maxTurnsInBattle": 1000,
            "battleSetupBytes": 3,
            "battleSetupMessagePackBase64": base64.b64encode(b"\x91\xa1x").decode(),
            "battleSettingsBytes": 3,
            "battleSettingsMessagePackBase64": base64.b64encode(b"\x91\xa1y").decode(),
        },
    }


class ConversionTests(unittest.TestCase):
    def test_matching_isolated_conversion_retains_identity_and_output_hashes(self):
        setup, settings, report = validate_conversion(converted_wrapper(), source_provenance())
        self.assertEqual((setup, settings), (b"\x91\xa1x", b"\x91\xa1y"))
        self.assertEqual(report["battleSetup"]["sha256"], hashlib.sha256(setup).hexdigest())
        self.assertEqual(report["battleSettings"]["sha256"], hashlib.sha256(settings).hexdigest())
        self.assertTrue(report["isolationVerified"])
        self.assertFalse(report["futurePredictionVerified"])

    def test_rejects_missing_isolation_or_native_fault(self):
        for section, key, bad in (
            ("root", "childExitCode", False),
            ("root", "timedOut", True),
            ("root", "nativeFault", {"code": "exception"}),
            ("observation", "schema", 2),
            ("observation", "synthetic", True),
            ("observation", "appContainer", False),
            ("observation", "capabilityCount", False),
            ("observation", "capabilityCount", 1),
            ("observation", "parentMemoryAccessDenied", False),
            ("observation", "phase", "isolation_verified"),
        ):
            with self.subTest(section=section, key=key, bad=bad):
                wrapper = converted_wrapper()
                target = wrapper if section == "root" else wrapper["observation"]
                target[key] = bad
                with self.assertRaisesRegex(ConversionError, "required isolation"):
                    validate_conversion(wrapper, source_provenance())

    def test_rejects_each_identity_or_settings_mismatch(self):
        for field in ("guidBytesHex", "seed", "stageId", "teamOwnerId",
                      "inventoryHeroIds", "activeEngineVersion",
                      "warmupBattleRandomCount", "maxTurnsInBattle"):
            with self.subTest(field=field):
                wrapper = converted_wrapper()
                old = wrapper["observation"][field]
                wrapper["observation"][field] = old + [1] if isinstance(old, list) else (
                    "f" * 32 if isinstance(old, str) else old + 1)
                with self.assertRaisesRegex(ConversionError, field):
                    validate_conversion(wrapper, source_provenance())

    def test_rejects_corrupt_original_converter_payload(self):
        for field, value in (("battleSetupMessagePackBase64", "not base64!"),
                             ("battleSetupBytes", 2),
                             ("battleSettingsMessagePackBase64", ""),
                             ("battleSettingsBytes", False)):
            with self.subTest(field=field):
                wrapper = converted_wrapper()
                wrapper["observation"][field] = value
                with self.assertRaises(ConversionError):
                    validate_conversion(wrapper, source_provenance())

    def test_source_hash_is_checked_before_probe_and_matching_result_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "replay-source"
            source.mkdir()
            (source / "battle-setup.json").write_bytes(SETUP)
            (source / "battle-settings.json").write_bytes(SETTINGS)
            (source / "capture-provenance.json").write_text(
                json.dumps(source_provenance()), encoding="utf-8")
            probe = root / "probe.exe"
            probe.write_bytes(b"test-only")
            original = (source / "battle-settings.json").read_bytes()
            (source / "battle-settings.json").write_bytes(original + b" ")
            with patch("convert_hydra_replay_source.subprocess.run") as run:
                with self.assertRaisesRegex(ConversionError, "hash or length changed"):
                    convert(source, probe)
                run.assert_not_called()
            (source / "battle-settings.json").write_bytes(original)
            result = SimpleNamespace(returncode=0, stdout=json.dumps(converted_wrapper()), stderr="")
            with patch("convert_hydra_replay_source.subprocess.run", return_value=result) as run:
                report = convert(source, probe)
                run.assert_called_once()
            self.assertEqual(report["battleGeneration"], 4)
            self.assertEqual((source / "packed" / "battle-setup.msgpack").read_bytes(), b"\x91\xa1x")
            self.assertEqual((source / "packed" / "battle-settings.msgpack").read_bytes(), b"\x91\xa1y")
            self.assertEqual(json.loads((source / "packed" / "conversion-report.json").read_text()),
                             report)

    @unittest.skipUnless(os.environ.get("RAID_TEST_ISOLATED_PROBE") == "1",
                         "opt-in original-runtime AppContainer test")
    def test_real_isolated_probe_converts_synthetic_json(self):
        root = Path(__file__).resolve().parent.parent
        sample = (root / "out/hydra-offline-runtime-20260921/"
                  "json-convert-settings-normal-user")
        probe = (root / "out/hydra-offline-runtime-20260921/Release/"
                 "raid_offline_probe.exe")
        self.assertTrue(sample.is_dir() and probe.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "replay-source"
            source.mkdir()
            for name in ("battle-setup.json", "battle-settings.json"):
                shutil.copyfile(sample / name, source / name)
            provenance = source_provenance()
            for field, name in (("battleSetups", "battle-setup.json"),
                                ("battleSettings", "battle-settings.json")):
                data = (source / name).read_bytes()
                provenance[field] = {"file": name, "bytes": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()}
            (source / "capture-provenance.json").write_text(
                json.dumps(provenance), encoding="utf-8")
            report = convert(source, probe)
            self.assertEqual(report["status"],
                             "original_json_converted_and_identity_verified")
            self.assertEqual(report["battleSetup"]["bytes"], 453)
            self.assertEqual(report["battleSettings"]["bytes"], 1042)
            self.assertEqual((source / "packed" / "battle-setup.msgpack").stat().st_size, 453)
            self.assertEqual((source / "packed" / "battle-settings.msgpack").stat().st_size, 1042)


if __name__ == "__main__":
    unittest.main()

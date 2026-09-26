import gzip
import json
from pathlib import Path
import tempfile
import unittest

from export_hydra_historical_actions import export


class HistoricalActionsExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capture = self.root / "capture"
        (self.capture / "replay-source").mkdir(parents=True)
        self._json(self.capture / "replay-source" / "capture-provenance.json", {
            "type": "verified_hydra_replay_source", "battleGeneration": 2,
            "battleSetupId": "abcdef", "seed": 123,
        })
        self._json(self.capture / "status.json", {
            "agent": {"instanceId": 99, "buildId": 7},
            "journalPidBinding": "unverified", "completeEventStream": False,
        })
        with gzip.open(self.capture / "raw.jsonl.gz", "wt", encoding="utf-8") as stream:
            stream.write(json.dumps({"channel": "decision", "payload": {
                "type": "decision_state", "battleGeneration": 2, "sequence": 3,
                "observedAtTick": 500, "battle": {"turn": 1, "round": 1,
                                                        "playerTurnCount": 0},
                "activeHeroId": 0, "activeHeroTypeId": 800,
                "battleRandom": {"battleSetupId": "abcdef", "seed": 123,
                                 "words": [1, 2, 3, 4]},
            }}) + "\n")
        self.journal = self.root / "journal.jsonl"
        self.journal_rows = [
            {"event": "start", "run": "run1", "bossMode": "hydra",
             "strategy": {"name": "team", "rules": []}},
            {"event": "lifecycle", "run": "run1", "bossMode": "hydra",
             "lifecycle": {"event": "agent_attached",
                           "agent": {"instanceId": 99, "buildId": 7}}},
            self._decision(1, 3, 1, 500),
            {"event": "command", "run": "run1", "bossMode": "hydra",
             "decisionNumber": 1, "command": {"status": "submitted"}},
            self._decision(2, 5, 2, 600),
            {"event": "command", "run": "run1", "bossMode": "hydra",
             "decisionNumber": 2, "command": {"status": "submitted"}},
        ]
        self._write_journal()

    @staticmethod
    def _decision(number, sequence, turn, tick):
        return {"event": "decision", "run": "run1", "bossMode": "hydra",
                "time": f"2026-01-01T00:00:{number:02d}Z",
                "decisionNumber": number,
                "decision": {"sequence": sequence, "skillTypeId": 801,
                             "skillSlot": 1, "targetId": 6,
                             "context": {"battleGeneration": 2,
                                         "observedAtTick": tick,
                                         "battle": {"turn": turn, "round": 1,
                                                    "playerTurnCount": turn - 1},
                                         "activeHero": {"activeHeroId": 0,
                                                        "activeHeroTypeId": 800}}}}

    @staticmethod
    def _json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def _write_journal(self):
        for row in self.journal_rows:
            if row["event"] == "command":
                row["time"] = f"2026-01-01T00:00:{row['decisionNumber'] + 10:02d}Z"
        self.journal.write_text(
            "".join(json.dumps(row) + "\n" for row in self.journal_rows),
            encoding="utf-8")

    def test_exports_only_observed_values_and_no_validated_script(self):
        output = self.root / "new-output"
        report = export(self.capture, [self.journal], output)
        self.assertEqual(report["submittedPlayerActions"], 2)
        self.assertEqual(report["nativeRngBeforeActions"], 1)
        self.assertEqual(report["firstActionWithoutNativeRngBefore"]["index"], 2)
        self.assertFalse(report["strictReplayScriptCreated"])
        self.assertFalse((output / "validated-actions.tsv").exists())
        rows = [json.loads(line) for line in
                (output / "historical-actions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["rngBeforeObserved"], [1, 2, 3, 4])
        self.assertIsNone(rows[0]["rngAfter"])
        self.assertIsNone(rows[0]["setOnCooldown"])
        self.assertIsNone(rows[1]["rngBeforeObserved"])
        with self.assertRaises(FileExistsError):
            export(self.capture, [self.journal], output)

    def test_rejects_native_controller_identity_mismatch(self):
        self.journal_rows[2]["decision"]["context"]["activeHero"]["activeHeroId"] = 1
        self._write_journal()
        with self.assertRaisesRegex(ValueError, "native actorId mismatch"):
            export(self.capture, [self.journal], self.root / "bad")
        self.assertFalse((self.root / "bad").exists())


if __name__ == "__main__":
    unittest.main()

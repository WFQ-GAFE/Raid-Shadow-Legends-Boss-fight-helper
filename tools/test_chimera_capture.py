"""Chimera start-data capture: validation and the per-decision trace."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
import tempfile
import uuid

from chimera_capture_live import ChimeraCaptureMonitor, INPUT_WAIT_SECONDS
from chimera_replay_source import validate_chimera_replay_source
from hydra_replay_source import SETTINGS_SOURCE, SETUPS_SOURCE, ReplaySourceError


GUID = "00112233-4455-6677-8899-aabbccddeeff"
NATIVE_GUID = uuid.UUID(GUID).bytes_le.hex()
ACCOUNT = {"type": "account_state", "accountName": "tester", "userId": 123456789}


def records(generation: int = 7) -> tuple[dict, dict]:
    heroes = [{"id": slot, "typeId": 20000 + slot, "modelFound": True, "battlePosition": slot + 1,
               "healthRaw": 1000, "dead": False} for slot in range(5)]
    setup = {"z": GUID, "r": 2128982206, "k": 8, "i": 13029005,
             "f": {"i": ACCOUNT["userId"],
                   "h": [{"t": slot + 1, "h": 700000 + slot, "i": hero["typeId"]}
                         for slot, hero in enumerate(heroes)]},
             "s": {"h": [{"i": 26866, "l": 280, "t": 1}]}}
    source = {"schema": 1, "type": "chimera_replay_source", "status": "captured",
              "battleGeneration": generation, "battleSetupId": NATIVE_GUID,
              "seed": 2128982206, "stageId": 13029005,
              "battleSetupsJson": json.dumps([setup], separators=(",", ":")),
              "battleSettingsJson": '{"ActiveEngineVersion":11750,"WarmupBattleRandomCount":13,"MaxTurnsInBattle":1500}',
              "battleSetupsSource": SETUPS_SOURCE, "battleSettingsSource": SETTINGS_SOURCE,
              "observedAtTick": 2000}
    decision = {"type": "decision_state", "bossMode": "chimera", "battleGeneration": generation,
                "sequence": 1, "observedAtTick": 1000, "chimeraStageId": 13029005,
                "chimeraStartSelection": {"stageId": 13029005,
                                          "heroIds": [700000 + slot for slot in range(5)],
                                          "heroTypeIds": [hero["typeId"] for hero in heroes]},
                "battle": {"kindId": 8, "hydraBattle": False, "finished": False, "round": 1,
                           "turn": 1, "playerTurnCount": 0, "currentDamage": 0},
                "battleRandom": {"schema": 1, "available": True, "source": "BattleState.Random_fields",
                                 "readStatus": "stable_double_read", "turn": 1, "playerTurnCount": 0,
                                 "words": [1, 2, 3, 4], "seedAvailable": True, "seed": 2128982206,
                                 "battleSetupIdAvailable": True, "battleSetupId": NATIVE_GUID},
                "activeHeroId": 0, "activeHeroTypeId": 20000, "activeHeroTurnCount": 0,
                "chimera": {"id": 5, "currentFormIndex": 0, "turnCount": 0},
                "heroes": heroes,
                "bosses": [{"id": 5, "typeId": 26866, "healthRaw": 1, "currentFormIndex": 0,
                            "challenges": [{"id": 8000501, "started": True, "completed": False,
                                            "currentRaw": 0, "targetRaw": 5153960755200000},
                                           {"id": 8000502, "started": False, "completed": False}]}]}
    return source, decision


def test_valid_source_yields_provenance_and_original_bytes() -> None:
    source, decision = records()
    provenance, setups, settings = validate_chimera_replay_source(source, decision, ACCOUNT, "tester")
    assert provenance["type"] == "verified_chimera_replay_source"
    assert provenance["teamHeroTypeIds"] == [20000 + slot for slot in range(5)]
    assert provenance["bossHeroTypeId"] == 26866 and provenance["bossLevel"] == 280
    assert provenance["openingRandomWords"] == [1, 2, 3, 4]
    assert setups == source["battleSetupsJson"].encode()
    assert settings == source["battleSettingsJson"].encode()


def expect_refusal(source: dict, decision: dict, reason: str) -> None:
    try:
        validate_chimera_replay_source(source, decision, ACCOUNT, "tester")
    except ReplaySourceError as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"accepted; expected {reason}")


def test_refuses_mismatched_identity_team_and_mode() -> None:
    source, decision = records()
    bad = copy.deepcopy(source)
    bad["seed"] = 1
    expect_refusal(bad, decision, "source_seed_differs_from_opening")
    bad = copy.deepcopy(source)
    bad["type"] = "hydra_replay_source"
    expect_refusal(bad, decision, "source_schema_or_status_invalid")
    bad_decision = copy.deepcopy(decision)
    bad_decision["heroes"][0]["typeId"] = 99999
    bad_decision["chimeraStartSelection"]["heroTypeIds"][0] = 99999
    expect_refusal(source, bad_decision, "battle_setup_team_mismatch")
    bad_decision = copy.deepcopy(decision)
    bad_decision["battle"]["playerTurnCount"] = 5
    bad_decision["battleRandom"]["playerTurnCount"] = 5
    expect_refusal(source, bad_decision, "opening_decision_not_at_start")
    bad_decision = copy.deepcopy(decision)
    bad_decision["battle"]["kindId"] = 5
    expect_refusal(source, bad_decision, "opening_decision_not_chimera")
    bad = copy.deepcopy(source)
    setup = json.loads(bad["battleSetupsJson"])[0]
    setup["f"]["i"] = 1
    bad["battleSetupsJson"] = json.dumps([setup])
    expect_refusal(bad, decision, "battle_setup_owner_mismatch")


class FakeIpc:
    def __init__(self, source: dict | None):
        self.source = source

    def replay_input(self) -> dict | None:
        return self.source

    def account(self) -> dict:
        return ACCOUNT


def test_monitor_saves_source_and_trace_from_opening() -> None:
    source, decision = records()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        messages: list[str] = []
        monitor = ChimeraCaptureMonitor(emit=messages.append, work_root=root)

        class Memory:
            def payload(self) -> dict:
                return {"version": 2, "skills": {"200001": [{"targetScope": "boss"}]}}

        opening = dict(decision, trialCatalog={"difficulties": [1, 2]})
        monitor.observe_decision(opening, ipc=FakeIpc(source), config={"mode": "execute", "rules": []},
                                 capability_memory=Memory())
        monitor.observe_command(decision, skill_type_id=200001, target_id=5, rule="r", status="submitted",
                                executed=True)
        later = copy.deepcopy(decision)
        later["battle"].update(turn=3, playerTurnCount=1)
        later["sequence"] = 2
        monitor.observe_decision(later, ipc=FakeIpc(source))
        folders = list(root.iterdir())
        assert len(folders) == 1 and folders[0].name.startswith(NATIVE_GUID)
        for name in ("battle-setup.json", "battle-settings.json", "capture-provenance.json"):
            assert (folders[0] / name).is_file(), name
        lines = [json.loads(line) for line in (folders[0] / "live-trace.jsonl").read_text("utf-8").splitlines()]
        assert [line["event"] for line in lines] == ["decision", "command", "decision"]
        assert lines[0]["rng"]["words"] == [1, 2, 3, 4]
        assert [trial["id"] for trial in lines[0]["trials"]] == [8000501]
        assert lines[1]["skillTypeId"] == 200001 and lines[1]["targetId"] == 5
        assert monitor.telemetry()["status"] == "saved"
        with gzip.open(folders[0] / "decision-states.jsonl.gz", "rt", encoding="utf-8") as stream:
            states = [json.loads(line) for line in stream]
        assert [state["sequence"] for state in states] == [1, 2]
        assert "trialCatalog" not in states[0]
        assert json.loads((folders[0] / "decision-static.json").read_text("utf-8")) == {
            "trialCatalog": {"difficulties": [1, 2]}}
        assert json.loads((folders[0] / "strategy.json").read_text("utf-8"))["mode"] == "execute"
        assert "200001" in json.loads((folders[0] / "capability-memory.json").read_text("utf-8"))["skills"]
        assert any("已保存" in message for message in messages)


def test_monitor_skips_mid_battle_takeover_and_reports_missing_source() -> None:
    source, decision = records()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        monitor = ChimeraCaptureMonitor(emit=lambda _: None, work_root=root)
        mid = copy.deepcopy(decision)
        mid["battle"]["playerTurnCount"] = 12
        monitor.observe_decision(mid, ipc=FakeIpc(source))
        assert monitor.telemetry()["status"] == "not_from_opening"
        assert list(root.iterdir()) == []

        now = [0.0]
        monitor = ChimeraCaptureMonitor(emit=lambda _: None, work_root=root, clock=lambda: now[0])
        opening = copy.deepcopy(decision)
        opening["battleGeneration"] = 9
        monitor.observe_decision(opening, ipc=FakeIpc(source))  # source is for generation 7
        assert monitor.telemetry()["status"] == "waiting"
        now[0] = INPUT_WAIT_SECONDS + 1
        monitor.observe_decision(opening, ipc=FakeIpc(source))
        telemetry = monitor.telemetry()
        assert telemetry["status"] == "unavailable"
        failure = root / telemetry["folder"] / "capture-failure.json"
        assert json.loads(failure.read_text("utf-8"))["battleGeneration"] == 9

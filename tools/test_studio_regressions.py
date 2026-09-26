"""Isolated regressions for persistence, lifecycle, state and transport."""
import ctypes
import json
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import chimera_web as web
import controller_manager as manager_module
from agent_ipc import AGENT_BUILD_ID, AgentIpc, DecisionJsonSlot
from boss_modes import default_store, normalize_hydra_head, strategy_template
from catalog_stream import static_entity
from controller_manager import ControllerManager
from controller_pause import ControllerPauseEvent
from hydra_state import hydra_head_is_devouring, hydra_devouring_head_ids
from strategy_storage import StrategyStoreError, atomic_write_json, read_object, recover_strategy_store, storage_health, write_strategy_store


def test_cancel_before_spawn() -> None:
    manager = ControllerManager()
    manager.preparing = True
    manager.pid = os.getpid()
    def worker(kind):
        manager.stop(None)
        return ["offline-worker"]
    with (
        patch.object(manager_module, "require_expected_account"),
        patch.object(manager, "_run_injector", return_value=(SimpleNamespace(returncode=0),
            {"agentLoaded": True, "agentCompatible": True, "agentReady": True,
             "agentStatus": {"compatible": True, "buildId": AGENT_BUILD_ID}})),
        patch.object(manager_module, "worker_command", side_effect=worker),
        patch.object(manager_module.subprocess, "Popen") as launch,
    ):
        manager._prepare_and_run(os.getpid(), "offline", 1, "hydra")
    launch.assert_not_called()
    assert manager.status == "已暂停" and not manager.snapshot()["running"]


def test_cancel_survives_child_initialization() -> None:
    parent = ControllerPauseEvent(os.getpid(), "offlineregression")
    child = ControllerPauseEvent(os.getpid(), "offlineregression")
    try:
        parent.open()
        assert parent.signal()
        child.open(reset=False)
        assert child.is_set()
    finally:
        child.close()
        parent.close()


def test_cancel_during_process_handoff() -> None:
    manager = ControllerManager()
    manager.preparing = True
    manager.pid = os.getpid()
    manager.pause_token = "handofftest"
    manager.pause_event = ControllerPauseEvent(os.getpid(), manager.pause_token).open()
    observed = []
    def spawn(*args, **kwargs):
        manager.stop(99999)  # Caller PID cannot redirect cancellation.
        with_event = ControllerPauseEvent(os.getpid(), manager.pause_token)
        with_event.open(reset=False)
        observed.append(with_event.is_set())
        with_event.close()
        return SimpleNamespace(stdout=[], wait=lambda: 0, poll=lambda: 0)
    with (
        patch.object(manager_module, "require_expected_account"),
        patch.object(manager, "_run_injector", return_value=(SimpleNamespace(returncode=0),
            {"agentLoaded": True, "agentCompatible": True, "agentReady": True,
             "agentStatus": {"compatible": True, "buildId": AGENT_BUILD_ID}})),
        patch.object(manager_module, "worker_command", return_value=["offline-worker"]),
        patch.object(manager_module.subprocess, "Popen", side_effect=spawn),
    ):
        manager._prepare_and_run(os.getpid(), "offline", 1, "hydra")
    assert observed == [True]
    assert manager.status == "已暂停"


def test_corrupt_store_is_preserved_and_recoverable() -> None:
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "strategies.json"
        original = default_store()
        write_strategy_store(path, original)
        changed = default_store()
        changed["modes"]["hydra"]["strategies"]["default"]["name"] = "second"
        write_strategy_store(path, changed)
        corrupt = b'{"modes": broken'
        path.write_bytes(corrupt)
        assert storage_health(path)["ok"] is False
        try:
            write_strategy_store(path, changed)
        except StrategyStoreError:
            pass
        else:
            raise AssertionError("Corruption must block saving")
        assert path.read_bytes() == corrupt
        recover_strategy_store(path)
        assert read_object(path) == changed
        preserved = list(path.parent.glob("*.recovery-*.json"))
        assert len(preserved) == 1 and preserved[0].read_bytes() == corrupt


def test_optimistic_save_rejects_conflicting_window() -> None:
    with TemporaryDirectory() as temporary:
        service = object.__new__(web.ChimeraService)
        service.lock = threading.RLock()
        path = Path(temporary) / "strategies.json"
        atomic_write_json(path, default_store())
        with patch.object(web, "STRATEGY_STORE", path):
            old = service.strategy_bundle("hydra")
            new = {**old["config"], "name": "new window"}
            saved = service.save_strategy(new, "hydra", expected_revision=old["revision"])
            try:
                service.save_strategy(old["config"], "hydra", expected_revision=old["revision"])
            except ValueError:
                pass
            else:
                raise AssertionError("Expected revision conflict")
            assert service.strategy_bundle("hydra")["revision"] == saved["revision"]


def test_incremental_logs_rollover_and_clear() -> None:
    manager = ControllerManager()
    manager.append("first", "hydra")
    initial = manager.snapshot("hydra")
    assert initial["logs"] == ["first"] and initial["logsReset"]
    assert manager.snapshot("hydra", after=initial["logCursor"])["logs"] == []
    manager.append("second", "hydra")
    delta = manager.snapshot("hydra", after=initial["logCursor"])
    assert delta["logs"] == ["second"] and not delta["logsReset"]
    for index in range(810):
        manager.append(str(index), "hydra")
    stale = manager.snapshot("hydra", after=delta["logCursor"])
    assert stale["logsReset"] and len(stale["logs"]) == 800
    manager.clear_logs("hydra")
    cleared = manager.snapshot("hydra", after=stale["logCursor"])
    assert cleared["logsReset"] and cleared["logs"] == []
    assert manager.snapshot("chimera", after=cleared["logCursor"])["logsReset"]


def test_shared_hydra_evidence() -> None:
    head = {"id": 22, "typeId": 26120, "isDevouring": False, "devouredHeroId": -1,
            "effects": [{"effectKind": "Digestion"}]}
    assert hydra_head_is_devouring(head)
    assert normalize_hydra_head(head)["isDevouring"]
    assert hydra_devouring_head_ids({"heroes": [{"effects": [{"effectKind": "Devoured", "producerId": 22}]}]}) == {22}


def test_static_catalog_does_not_change_with_combat() -> None:
    hero = {"id": 12, "typeId": 99, "name": "A", "healthPct": 100, "effects": [],
            "skills": [{"slot": 1, "typeId": 991, "name": "A1", "cooldown": 0, "validTargetIds": [1]}]}
    changed = {**hero, "healthPct": 20, "effects": [{"effectKind": "Fear"}],
               "skills": [{**hero["skills"][0], "cooldown": 3, "validTargetIds": [2]}]}
    assert static_entity(hero) == static_entity(changed)
    service = object.__new__(web.ChimeraService)
    service.lock = threading.RLock()
    service.catalog_epoch = 1
    service.catalog_session = "test"
    service.hero_catalog = {99: static_entity(hero)}
    service.hydra_head_catalog = {}
    service.effect_options = []
    service.ui_catalog = {"difficulties": []}
    full = service.catalog_payload()
    assert len(full["heroes"]) == 1
    with patch.object(service, "heroes", side_effect=AssertionError("Must not rebuild unchanged catalog")):
        assert service.catalog_payload(full["catalogRevision"]) == {"catalogRevision": full["catalogRevision"]}


def test_complete_overflow_frame_fails_closed() -> None:
    slot = DecisionJsonSlot()
    payload = json.dumps({"type": "ipc_error", "error": "payload_too_large", "requiredBytes": 262156, "capacity": 262144}).encode()
    ctypes.memmove(ctypes.addressof(slot) + 16, payload, len(payload))
    slot.length = len(payload)
    slot.sequence = 2
    ipc = AgentIpc(os.getpid())
    with patch.object(ipc, "read_text", return_value=AgentIpc._read_slot(slot)):
        try:
            ipc.decision()
        except ValueError as error:
            assert "262156" in str(error)
        else:
            raise AssertionError("Overflow must never become an executable snapshot")


def test_telemetry_does_not_pollute_logs() -> None:
    manager = ControllerManager()
    manager.append('@@raid-telemetry {"decision":{"rule":"strict"}}', "hydra")
    snapshot = manager.snapshot("hydra")
    assert snapshot["logs"] == []
    assert snapshot["telemetry"]["decision"]["rule"] == "strict"


def test_decision_explanation_preserves_rule_precedence() -> None:
    from chimera_controller import evaluate
    from test_chimera_strategy_tree import sample_state
    state = sample_state()
    config = {"rules": [
        {"name": "unavailable", "when": {"activeHeroTypeId": [8896]}, "action": {"type": "cast", "skillTypeId": 88963, "target": "boss"}},
        {"name": "strict winner", "when": {"activeHeroTypeId": [8896]}, "action": {"type": "cast", "skillTypeId": 88961, "target": "boss"}},
        {"name": "unused", "action": {"type": "cast", "skillTypeId": 88962, "target": "self"}},
    ]}
    before = evaluate(config, state)
    state["_decisionTrace"] = []
    after = evaluate(config, state)
    assert before == after and after.rule == "strict winner"
    assert [row["outcome"] for row in state["_decisionTrace"]] == ["unavailable", "selected"]
    assert state["_decisionTrace"][-1]["index"] == 2


def test_export_recovery_validates_before_touching_original() -> None:
    service = object.__new__(web.ChimeraService)
    service.lock = threading.RLock()
    service.controller = ControllerManager()
    with TemporaryDirectory() as temporary, patch.object(web, "STRATEGY_STORE", Path(temporary) / "strategies.json"):
        path = web.STRATEGY_STORE
        path.write_bytes(b"broken original")
        document = {"format": "raid-boss-strategy", "version": 1, "bossMode": "hydra",
                    "strategy": strategy_template("hydra")}
        for invalid in ({**document, "strategy": None}, {**document, "version": True}, {**document, "bossMode": "chimera"}):
            try:
                service.recover_strategies(invalid, "hydra")
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid recovery must fail")
            assert path.read_bytes() == b"broken original"
        service.controller.preparing = True
        try:
            service.recover_strategies(document, "hydra")
        except ValueError:
            pass
        else:
            raise AssertionError("Recovery must not change a running strategy")
        service.controller.preparing = False
        document["strategy"]["name"] = "Restored export"
        service.recover_strategies(document, "hydra")
        assert service.strategy_bundle("hydra")["config"]["name"] == "Restored export"
        assert storage_health(path)["ok"]
        assert next(path.parent.glob("*.recovery-*.json")).read_bytes() == b"broken original"

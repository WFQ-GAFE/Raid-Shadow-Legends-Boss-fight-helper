"""Offline conflicting-snapshot and result action regression tests."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import chimera_controller as controller
import controller_manager as manager_module
from test_chimera_lifecycle_flow import FakeResultIpc, SESSION


def rejects(function, reason):
    try:
        function()
    except RuntimeError as error:
        assert reason in str(error), str(error)
    else:
        raise AssertionError('Unconfirmed result was accepted')


def test_old_result_newer_running_battle_never_restarts():
    for mode in ('chimera', 'hydra'):
        ipc = FakeResultIpc(81561400, mode)
        ipc.decision = lambda: {'battleGeneration': 1, 'observedAtTick': 200,
                               'battle': {'turn': 410, 'finished': False}}
        with patch.object(controller, 'queue_lifecycle_command') as queue:
            rejects(lambda: controller.wait_for_turn_advance(ipc, {'battle': {'turn': 409}}, SESSION, .5),
                    'newer_running_battle_conflicts')
            rejects(lambda: controller.result_screen_reached(ipc, config={'mode': 'execute', 'objectives': {}},
                pid=1, agent=Path('unused'), session_id=SESSION, boss_mode=mode, execute_requested=True),
                'newer_running_battle_conflicts')
            rejects(lambda: controller.restart_chimera_from_result(ipc, pid=1, agent=Path('unused'),
                session_id=SESSION, nonce=1, desired_hero_ids=None, desired_hero_type_ids=None), 'newer_running_battle_conflicts')
            queue.assert_not_called()


def test_pre_result_live_snapshot_does_not_block_real_result():
    ipc = FakeResultIpc(81561400, 'chimera')
    ipc.decision = lambda: {'battleGeneration': 1, 'observedAtTick': 99,
                            'battle': {'turn': 409, 'finished': False}}
    controller.require_confirmed_result(ipc)
    assert controller.wait_for_turn_advance(ipc, {'battle': {'turn': 409}}, SESSION, .5) is None


def test_legacy_agent_unknown_end_and_different_battle_are_rejected():
    for field in ('confirmed', 'battleFinished', 'source', 'battleGeneration', 'openedAtTick'):
        ipc = FakeResultIpc(1, 'chimera')
        del ipc.current_lifecycle['result'][field]
        rejects(lambda: controller.require_confirmed_result(ipc), '结算状态未通过核对')
    ipc = FakeResultIpc(1, 'chimera')
    ipc.decision = lambda: {'battleGeneration': 2, 'observedAtTick': 200, 'battle': {'finished': False}}
    rejects(lambda: controller.require_confirmed_result(ipc), 'battle_snapshot_identity_missing_or_changed')


def test_stale_ledger_and_dialog_closing_during_read_are_rejected():
    ipc = FakeResultIpc(1, 'chimera')
    old = ipc.battle_ledger()
    old['battleGeneration'] = 2
    ipc.battle_ledger = lambda: old
    rejects(lambda: controller.require_confirmed_result(ipc), 'result_ledger_mismatch')
    ipc = FakeResultIpc(1, 'chimera')
    def close():
        ipc.current_lifecycle = {'screen': 'battle'}
        return {}
    ipc.decision = close
    rejects(lambda: controller.require_confirmed_result(ipc), 'result_changed_during_read')


def test_result_rejection_is_durable_and_excludes_pointers():
    ipc = FakeResultIpc(1, 'chimera')
    ipc.current_lifecycle['result']['confirmed'] = False
    with TemporaryDirectory() as directory, patch.object(manager_module, 'PROJECT_ROOT', Path(directory)):
        manager = manager_module.ControllerManager()
        with patch.object(controller, 'emit_telemetry', side_effect=lambda **data:
                          manager.append('@@raid-telemetry ' + json.dumps(data))):
            rejects(lambda: controller.require_confirmed_result(ipc), 'result_not_confirmed')
        manager.clear_logs('chimera')
        rows = [json.loads(line) for line in manager.journal.path.read_text(encoding='utf-8').splitlines()]
        assert rows[-1]['event'] == 'lifecycle'
        assert rows[-1]['lifecycle']['reason'] == 'result_not_confirmed'
        assert '9001' not in json.dumps(rows)  # synthetic result context address
        assert rows[-1]['lifecycle']['result']['confirmed'] is False


def test_native_recheck_rejection_is_recorded_and_does_not_start_team():
    ipc = FakeResultIpc(1, 'chimera')
    with patch.object(controller, 'queue_lifecycle_command', return_value={'queued': True}), \
         patch.object(controller, 'wait_for_command_ack', return_value={'status': 'rejected', 'reason': 'result_not_confirmed'}), \
         patch.object(controller, 'start_first_battle_if_ready') as start, \
         patch.object(controller, 'emit_telemetry') as emit:
        rejects(lambda: controller.restart_chimera_from_result(ipc, pid=1, agent=Path('unused'),
            session_id=SESSION, nonce=1, desired_hero_ids=None, desired_hero_type_ids=None), 'result_not_confirmed')
        start.assert_not_called()
        assert emit.call_args.kwargs['lifecycle']['acknowledgement']['reason'] == 'result_not_confirmed'

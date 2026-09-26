"""Offline replay of preparation/result overlap; no game operations."""
import copy
from pathlib import Path
from unittest.mock import patch
import chimera_controller as controller
from test_chimera_lifecycle_flow import FakeResultIpc, SESSION, active_lifecycle


def replay(screen_at):
    clock = [0.0]
    ipc = FakeResultIpc(35521231, 'chimera')
    initial = copy.deepcopy(ipc.current_lifecycle)
    selection = active_lifecycle('team_selection', {'selection': {'context': 9002,
        'valid': True, 'filled': True, 'bossMode': 'chimera', 'stageId': 1,
        'autoBattle': False, 'quickBattle': False,
        'heroIds': [1, 2, 3, 4, 5], 'heroTypeIds': [11, 12, 13, 14, 15]}})
    unconfirmed = copy.deepcopy(initial)
    unconfirmed['result'].update(confirmed=False, battleFinished=False)
    def state():
        screen = screen_at(clock[0])
        return {'initial': initial, 'selection': selection, 'unconfirmed': unconfirmed,
                'unknown': active_lifecycle('unknown', {})}[screen]
    ipc.lifecycle = state
    observations, started_at = [], []
    with patch.object(controller.time, 'monotonic', side_effect=lambda: clock[0]), \
         patch.object(controller.time, 'sleep', side_effect=lambda value: clock.__setitem__(0, clock[0] + value)), \
         patch.object(controller, 'queue_lifecycle_command', return_value={'queued': True}) as queue, \
         patch.object(controller, 'wait_for_command_ack', return_value={'status': 'submitted'}), \
         patch.object(controller, 'start_first_battle_if_ready', side_effect=lambda *a, **kw: (started_at.append(clock[0]) or True)), \
         patch.object(controller, 'emit_telemetry', side_effect=lambda **row: observations.append(row['lifecycle'])):
        error = None
        try:
            controller.restart_chimera_from_result(ipc, pid=1, agent=Path('offline'), session_id=SESSION,
                nonce=1, desired_hero_ids=None, desired_hero_type_ids=None)
        except RuntimeError as exception:
            error = str(exception)
        assert queue.call_count == 1
    return error, started_at, observations


def test_retained_preparation_restarts_once_after_stability_wait():
    # Native guard retires the prior result, keeping selection authoritative.
    error, started, events = replay(lambda t: 'initial' if t < .3 else 'selection')
    assert error is None and len(started) == 1 and 2.3 <= started[0] < 2.5
    assert events[-1]['event'] == 'result_restart_completed'
    observed = next(e for e in events if e.get('screen') == 'team_selection')
    assert observed['selection']['valid'] and observed['selection']['heroTypeIds'] == [11,12,13,14,15]
    assert 'context' not in observed['selection'] and 'heroIds' not in observed['selection']


def test_unconfirmed_result_overlap_has_accurate_timeout_and_no_blind_start():
    error, started, events = replay(lambda t: 'initial' if t < .3 else 'selection' if t < 1.2 else 'unconfirmed')
    assert not started and '曾进入准备界面' in error
    assert events[-1]['event'] == 'result_restart_timeout'
    assert events[-1]['sawTeamSelection'] is True
    assert events[-1]['result']['confirmed'] is False and events[-1]['screen'] == 'result'
    assert 6 <= sum(e['event'] == 'result_restart_progress' for e in events) <= 10


def test_never_observed_preparation_is_distinguished():
    error, started, events = replay(lambda t: 'initial')
    assert not started and '没有进入准备界面' in error
    assert events[-1]['sawTeamSelection'] is False


def test_leaving_preparation_resets_the_stability_wait():
    error, started, _ = replay(lambda t: 'initial' if t < .3 else 'selection' if t < 1.2 else 'unknown' if t < 2.2 else 'selection')
    assert error is None and len(started) == 1 and 4.2 <= started[0] < 4.4

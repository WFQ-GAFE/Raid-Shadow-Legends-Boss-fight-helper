import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import controller_manager as manager_module
from lifecycle_observability import ObservedIpc


class FakeIpc:
    def __init__(self):
        self.value = {'sequence': 3, 'agentInstanceId': 77, 'screen': 'result', 'events': [
            {'sequence': 1, 'reason': 'battle_finished_observed'},
            {'sequence': 2, 'reason': 'result_screen_observed'},
            {'sequence': 3, 'reason': 'result_dialog_disabled'}]}
        self.ledger = {}
    def lifecycle(self): return self.value
    def battle_ledger(self): return self.ledger


def test_short_lived_transitions_are_recovered_once_without_changing_state():
    base = FakeIpc()
    output = []
    ipc = ObservedIpc(base, lambda **row: output.append(row['lifecycle']))
    assert ipc.lifecycle() is base.value
    count = len(output)
    ipc.lifecycle()
    assert len(output) == count
    assert [r['observation']['reason'] for r in output if r['event'] == 'native_transition'] == [
        'battle_finished_observed', 'result_screen_observed', 'result_dialog_disabled']


def test_ring_overflow_is_explicit_and_agent_reload_resets_cursor():
    base = FakeIpc(); output = []
    ipc = ObservedIpc(base, lambda **r: output.append(r['lifecycle']))
    ipc.lifecycle()
    base.value = {'sequence': 40, 'agentInstanceId': 77, 'events': [{'sequence': 40, 'reason': 'later'}]}
    ipc.lifecycle()
    gaps = [r for r in output if r['event'] == 'native_history_gap']
    assert gaps[-1]['fromSequence'] == 4 and gaps[-1]['toSequence'] == 39
    base.value = {'sequence': 1, 'agentInstanceId': 88, 'events': [{'sequence': 1, 'reason': 'reloaded'}]}
    ipc.lifecycle()
    assert any(r.get('observation', {}).get('reason') == 'reloaded' for r in output)


def test_terminal_snapshot_preserves_effects_and_availability_without_addresses():
    base = FakeIpc(); output = []
    base.ledger = {'terminalState': {'battleGeneration': 2, 'observedAtTick': 100,
        'actorsAvailable': True, 'bossesAvailable': False,
        'pointers': {'context': 999999}, 'battle': {'finished': True, 'turn': 410},
        'heroes': [{'id': 4, 'dead': True, 'healthPct': 0, 'effects': [{'effectTypeId': 470, 'turnsLeft': 2}]}]}}
    ipc = ObservedIpc(base, lambda **r: output.append(r['lifecycle']))
    ipc.lifecycle()
    row = next(r for r in output if r['event'] == 'terminal_battle_observed')
    assert row['availability']['bossesAvailable'] is False
    assert row['context']['entities'][0]['effects'][0]['effectTypeId'] == 470
    assert row['context']['battle']['finished'] is True
    assert '999999' not in json.dumps(output)


def test_console_and_critical_history_survive_ui_clear_and_decision_rotation():
    with TemporaryDirectory() as directory, patch.object(manager_module, 'PROJECT_ROOT', Path(directory)):
        manager = manager_module.ControllerManager()
        manager.journal.max_bytes = 600
        manager.journal.keep_files = 2
        manager.append('奇美拉结算目标未全部达成：未完成试炼 8000621；正在执行第 3 次免费重整。')
        manager.append('@@raid-telemetry ' + json.dumps({'lifecycle': {'event': 'result_objectives_unmet', 'missingTrialIds': [8000621]}}))
        for i in range(30):
            manager.append('@@raid-telemetry ' + json.dumps({'decision': {'number': i, 'data': 'x' * 100}}))
        manager.clear_logs('chimera')
        assert manager.snapshot()['logs'] == []
        records = [json.loads(line) for line in manager.critical_journal.path.read_text(encoding='utf-8').splitlines()]
        assert any(r['event'] == 'console' and '8000621' in r['message'] for r in records)
        assert any(r.get('lifecycle', {}).get('event') == 'result_objectives_unmet' for r in records)
        assert not any(r['event'] == 'decision' for r in records)


def test_console_redacts_binding_and_guard_dump():
    sanitize = manager_module.diagnostic_console_text
    assert 'secret-name' not in sanitize('已绑定游戏内账户 secret-name（玩家 ID 12345）')
    assert '777777' not in sanitize('未执行；校验详情：context=777777')
    assert 'private-token' not in sanitize("失败 token='private-token', context=999999")


def test_preparation_details_and_ignored_result_survive_observation():
    base = FakeIpc()
    base.value.update(screen='team_selection', selection={
        'valid': True, 'filled': True, 'bossMode': 'chimera', 'heroTypeIds': [1,2,3,4,5],
        'context': 987654321, 'autoBattle': False, 'quickBattle': False})
    base.value['events'] = [{'sequence': 3, 'reason': 'stale_result_dialog_ignored',
        'screen': 'team_selection', 'uiEpoch': 5, 'resultGenerationRetired': True}]
    output = []
    ObservedIpc(base, lambda **row: output.append(row['lifecycle'])).lifecycle()
    event = next(row for row in output if row['event'] == 'native_transition')
    assert event['observation']['resultGenerationRetired'] is True
    state = next(row for row in output if row['event'] == 'lifecycle_observed')
    assert state['selection']['valid'] and state['selection']['heroTypeIds'] == [1,2,3,4,5]
    assert '987654321' not in json.dumps(output)

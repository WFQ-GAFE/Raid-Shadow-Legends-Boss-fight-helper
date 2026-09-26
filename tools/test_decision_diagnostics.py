"""Offline history/reason tests, using synthetic inputs and temporary files."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import controller_manager as manager_module
from chimera_controller import evaluate
from controller_manager import ControllerManager
from decision_journal import DecisionJournal
from decision_observability import decision_details
from lydia_trial_rules import BASE_GROUPS, update_profile
from test_lydia_trial_rules import profile, state


def traced(snapshot):
    config = update_profile(profile())
    original = copy.deepcopy(snapshot)
    before = evaluate(config, snapshot)
    snapshot['_decisionTrace'] = []
    snapshot['_reservedStrictSkillTypeIds'] = []
    after = evaluate(config, snapshot)
    assert before == after
    for key, value in original.items():
        assert snapshot[key] == value
    return decision_details(snapshot, after)


def test_eight_icons_can_explain_a_specific_missing_base():
    base = [group[1][-1] for group in BASE_GROUPS]
    # Seven base effects plus Poison Sensitivity: exactly eight observed icons.
    details = traced(state(base[:-1] + [500]))
    assert details['skillTypeId'] == 47102
    row = details['rules'][0]
    assert row['outcome'] == 'condition_failed'
    candidate = row['candidates'][0]
    assert candidate['targetId'] == 5 and candidate['matched'] is False
    children = candidate['conditionTree']['children']
    failed = [child for child in children if child['passed'] is False]
    assert len(failed) == 1
    def leaves(node):
        if 'condition' in node:
            return [node]
        return [leaf for child in node.get('children', []) for leaf in leaves(child)]
    burn_checks = [leaf for leaf in leaves(failed[0])
                   if leaf['condition'].get('effect', {}).get('kind') == 'AoEContinuousDamage']
    assert burn_checks and all(leaf['passed'] is False for leaf in burn_checks)
    context = details['context']
    assert len(context['entities'][0]['effects']) == 8
    assert context['reservedStrictSkillTypeIds'] == [47103]


def test_duration_failure_is_distinguishable_from_missing_effect():
    snapshot = state([group[1][-1] for group in BASE_GROUPS])
    snapshot['bosses'][0]['effects'][-1]['turnsLeft'] = 0
    details = traced(snapshot)
    assert details['rules'][0]['outcome'] == 'condition_failed'
    assert details['context']['entities'][0]['effects'][-1]['effectTypeId'] == 470
    assert details['context']['entities'][0]['effects'][-1]['turnsLeft'] == 0
    del snapshot['bosses'][0]['effects'][-1]['turnsLeft']
    details = traced(snapshot)
    assert 'turnsLeft' not in details['context']['entities'][0]['effects'][-1]


def test_cooldown_and_invalid_targets_are_distinguishable_from_conditions():
    base = [group[1][-1] for group in BASE_GROUPS]
    snapshot = state(base, ready=(1, 2))
    snapshot['skills'][2]['cooldown'] = 2
    details = traced(snapshot)
    assert details['rules'][0]['outcome'] == 'unavailable'
    assert details['rules'][0]['candidates'][0]['matched'] is True
    assert details['context']['skills'][2]['cooldown'] == 2
    snapshot = state(base)
    snapshot['skills'][2]['validTargetIds'] = []
    details = traced(snapshot)
    assert details['rules'][0]['outcome'] == 'unavailable'
    assert details['context']['skills'][2]['ready'] is True
    assert details['context']['skills'][2]['validTargetIds'] == []


def test_grouped_alternatives_do_not_report_successful_or_group_as_failure():
    details = traced(state([group[1][-1] for group in BASE_GROUPS]))
    assert details['skillTypeId'] == 47103
    groups = details['rules'][0]['candidates'][0]['conditionTree']['children']
    assert all(group['passed'] for group in groups)
    assert any(child['passed'] is False for group in groups for child in group.get('children', []))


def test_context_excludes_account_credentials_and_pointers():
    snapshot = state([])
    snapshot.update(accountName='private-account', token='private-token', pointers={'generator': 123})
    snapshot['skills'][0]['skillDataPtr'] = 456
    snapshot['bosses'][0]['effects'] = [{'effectTypeId': 470, 'turnsLeft': 2, 'pointer': 789}]
    encoded = json.dumps(traced(snapshot))
    assert all(word not in encoded for word in ('private-account', 'private-token', 'generator', 'skillDataPtr', 'pointer'))


def test_history_survives_clear_and_manager_restart_with_revision_and_ack():
    with TemporaryDirectory() as temporary, patch.object(manager_module, 'PROJECT_ROOT', Path(temporary)):
        manager = ControllerManager()
        manager.running_revision = 'loaded-revision'
        manager.strategy_id = 'marius-test'
        manager.diagnostic_run = 'offline-run'
        manager.append('@@raid-telemetry ' + json.dumps({'decision': traced(state([]))}))
        manager.append('@@raid-telemetry {"command":{"status":"rejected","reason":"guard_failed"}}')
        manager.clear_logs('chimera')
        assert manager.snapshot()['logs'] == []
        records = [json.loads(line) for line in manager.journal.path.read_text(encoding='utf-8').splitlines()]
        assert [row['event'] for row in records] == ['decision', 'command']
        assert all(row['runningRevision'] == 'loaded-revision' and row['strategyId'] == 'marius-test'
                   and row['decisionNumber'] == 1 and row['run'] == 'offline-run' for row in records)
        restarted = ControllerManager()
        assert restarted.snapshot()['telemetry'] == {}
        assert manager.journal.path.is_file()


def test_bounded_rotation_leaves_other_files_untouched():
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        unrelated = directory / 'user-notes.jsonl'
        unrelated.write_text('keep', encoding='utf-8')
        journal = DecisionJournal(directory, max_bytes=350, keep_files=3)
        for index in range(20):
            assert journal.write('decision', number=index, value='x' * 100)
        files = [path for path in directory.iterdir() if journal.PATTERN.fullmatch(path.name)]
        assert len(files) == 3 and all(path.stat().st_size <= 350 for path in files)
        assert json.loads(journal.path.read_text(encoding='utf-8'))['number'] == 19
        assert unrelated.read_text(encoding='utf-8') == 'keep'


def test_unwritable_history_keeps_controller_telemetry_working():
    with TemporaryDirectory() as temporary, patch.object(manager_module, 'PROJECT_ROOT', Path(temporary)):
        (Path(temporary) / 'logs').write_text('not a directory', encoding='utf-8')
        manager = ControllerManager()
        manager.append('@@raid-telemetry {"decision":{"rule":"first"}}')
        manager.append('@@raid-telemetry {"decision":{"rule":"second"}}')
        snapshot = manager.snapshot()
        assert snapshot['telemetry']['decision']['rule'] == 'second'
        assert snapshot['diagnosticError'] and len(snapshot['logs']) == 1
        assert '出手诊断无法保存' in snapshot['logs'][0]

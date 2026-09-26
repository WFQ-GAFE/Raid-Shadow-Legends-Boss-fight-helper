"""Graph execution, list migration, validation and preview isolation (no live IPC)."""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import chimera_controller as c
from strategy_flow import convert_rules, validate_flow, evaluate_flow, flow_revision, replay_flow
from test_trial_skill_reservation import fixture
from test_chimera_strategy_tree import sample_state
from preview_runtime import preview_root, seed_preview


def config_for(rules):
    return {"rules": rules, "executionMode": "flow", "strategyFlow": convert_rules({"rules": rules})}


def signature(decision):
    return None if decision is None else (decision.rule, decision.skill["typeId"], decision.target_id)


def test_conversion_preserves_trial_reservation_and_release_for_all_rule_orders():
    for reverse in (False, True):
        for ready in (False, True):
            for completed in (False, True):
                state, owner, generic, fallback = fixture()
                if ready: owner['when']['chimeraTurnAtLeast'] = 1
                if completed: state['bosses'][0]['challenges'][0]['completed'] = True
                rules = [owner, generic, fallback] if reverse else [generic, owner, fallback]
                config = config_for(rules)
                original = c.evaluate({"rules": rules}, copy.deepcopy(state))
                converted = evaluate_flow(config, state)
                assert signature(original) == signature(converted)
                assert state['_flowTrace'][-1]['outcome'] == 'selected'
                assert sum(row['outcome'] == 'selected' for row in state['_flowTrace']) == 1


def test_conversion_keeps_phase_order_openers_and_form_policies():
    state, owner, generic, fallback = fixture()
    default = {'name': 'default', 'when': {}, 'action': {'type': 'defaultSkillPriority',
        'firstTurnSkill': {'skillTypeId': 88962, 'skillSlot': 2, 'target': {'type': 'self'}},
        'prioritySkills': [fallback['action']], 'blockedSkillTypeIds': [],
        'formPolicies': {'Ram': {'prioritySkills': [fallback['action']], 'firstTurnSkill': fallback['action']}}}}
    for form in ('Ram', 'Lion', 'Snake', 'Ultimate'):
        for first in (False, True):
            for blocked in (False, True):
                local = copy.deepcopy(state)
                local['chimera']['currentForm'] = form
                local['_chimeraFormHeroFirstTurn'] = first
                local['skills'][0]['ready'] = not blocked
                rules = [default, owner, generic]
                assert signature(c.evaluate({'rules': rules}, copy.deepcopy(local))) == signature(evaluate_flow(config_for(rules), local))


def test_false_and_unavailable_can_take_different_shared_branches():
    state, owner, generic, fallback = fixture()
    config = config_for([generic, fallback])
    nodes = config['strategyFlow']['nodes']
    nodes['rule-1']['edges'] = {'noMatch': 'wait', 'unavailable': 'rule-2'}
    nodes['rule-1']['rule']['when'] = {'chimeraTurnAtLeast': 99}
    assert evaluate_flow(config, state) is None
    assert [row['nodeId'] for row in state['_flowTrace']] == ['opener', 'rule-1', 'wait']
    nodes['rule-1']['rule']['when'] = {}
    state['skills'][1]['ready'] = False
    assert evaluate_flow(config, state).rule == fallback['name']
    assert state['_flowTrace'][1]['outcome'] == 'unavailable'


def test_condition_and_terminal_wait_do_not_fall_through():
    state, owner, generic, fallback = fixture()
    config = config_for([generic, fallback])
    flow = config['strategyFlow']
    flow['nodes']['choice'] = {'type': 'condition', 'name': 'form', 'rule': {'when': {'form': ['Snake']}, 'action': generic['action']},
                              'edges': {'yes': 'wait', 'no': 'opener'}}
    flow['entry'] = 'choice'
    state['chimera']['currentForm'] = 'Snake'
    assert evaluate_flow(config, state) is None
    assert [row['nodeId'] for row in state['_flowTrace']] == ['choice', 'wait']
    state['chimera']['currentForm'] = 'Ram'
    assert evaluate_flow(config, state).rule == generic['name']


def test_explicit_reservation_blocks_generic_and_allows_designated_owner():
    state, owner, generic, fallback = fixture()
    owner['when'] = {'chimeraTurnAtLeast': 99}  # no automatic trial reservation
    config = config_for([generic, owner, fallback])
    flow = config['strategyFlow']
    flow['nodes']['reserve'] = {'type': 'reserve', 'name': 'keep S2', 'rule': generic,
                               'owner': 'rule-2', 'edges': {'next': 'opener'}}
    flow['entry'] = 'reserve'
    assert evaluate_flow(config, state).rule == fallback['name']
    flow['nodes']['rule-2']['rule']['when'] = {}
    assert evaluate_flow(config, state).rule == owner['name']
    flow['nodes']['reserve']['rule'] = {**generic, 'when': {'form': ['Snake']}}
    assert evaluate_flow(config, state).rule == generic['name']


def test_explicit_reservation_does_not_leak_between_turns_or_input_states():
    state, owner, generic, fallback = fixture()
    before = copy.deepcopy(state)
    evaluate_flow(config_for([owner, generic, fallback]), state)
    assert '_trialSkillOwners' not in state
    assert state['skills'] == before['skills']
    assert evaluate_flow(config_for([generic, fallback]), state).rule == generic['name']


def test_validation_rejects_cycles_dangling_unreachable_invalid_owner_and_conditions():
    _, owner, generic, fallback = fixture()
    base = config_for([generic, fallback])['strategyFlow']
    changes = [
        lambda f: f['nodes']['wait'].update(type='opener', edges={'next': 'opener'}),
        lambda f: f['nodes']['opener']['edges'].update(next='missing'),
        lambda f: f['nodes'].update(orphan={'type': 'pause', 'edges': {}}),
        lambda f: f['nodes']['rule-1']['rule']['when'].update(unknownCondition=1),
        lambda f: f['nodes']['opener'].update(type='reserve', rule=generic, owner='wait'),
        lambda f: f['nodes']['rule-1']['edges'].pop('unavailable'),
        lambda f: f.update(entry=['bad']),
    ]
    for change in changes:
        flow = copy.deepcopy(base); change(flow)
        try: validate_flow(flow, c.validate_strategy_node)
        except ValueError: pass
        else: raise AssertionError('Invalid graph accepted')


def test_graph_revision_ignores_layout_but_changes_with_execution():
    flow = convert_rules({'rules': []}); before = flow_revision(flow)
    flow['nodes']['opener']['x'] = 800
    assert flow_revision(flow) == before
    flow['nodes']['opener']['edges']['next'] = 'wait'
    assert flow_revision(flow) != before


def test_replay_is_offline_and_preserves_snapshot():
    state, owner, generic, fallback = fixture(); before = copy.deepcopy(state)
    with patch.object(c, 'queue_command', side_effect=AssertionError('No game commands in replay')):
        result = replay_flow(config_for([owner, generic, fallback]), state)
    assert state == before
    assert result['rule'] == fallback['name'] and result['flowTrace']
    assert result['flowRevision']


def test_adaptive_tail_and_trial_phase_match_list_engine():
    state, owner, generic, fallback = fixture()
    trial = {'name': 'automatic', 'when': {}, 'action': {'type': 'executeTrialRecipe'}}
    # Force the dedicated recipe to be unavailable, while testing the real adaptive tail.
    original = c.evaluate_strategy_node
    def without_recipe(node, *args, **kwargs):
        if node.get('action', {}).get('type') == 'executeTrialRecipe': return None
        return original(node, *args, **kwargs)
    with patch.object(c, 'evaluate_strategy_node', side_effect=without_recipe):
        for rules in ([trial], [trial, generic], [owner, trial]):
            assert signature(c.evaluate({'rules': rules}, copy.deepcopy(state))) == signature(evaluate_flow(config_for(rules), copy.deepcopy(state)))


def test_hydra_engine_mode_is_preserved_and_one_action_selected():
    _, _, generic, fallback = fixture()
    state = sample_state(); state['battle']['kindId'] = 7
    previous = c.ACTIVE_BOSS_MODE
    try:
        c.ACTIVE_BOSS_MODE = 'hydra'
        rules = [generic, fallback]
        assert signature(c.evaluate({'rules': rules}, copy.deepcopy(state))) == signature(evaluate_flow(config_for(rules), state))
        assert c.ACTIVE_BOSS_MODE == 'hydra'
    finally: c.ACTIVE_BOSS_MODE = previous


def test_preview_seeds_once_and_never_overwrites_stable_or_preview():
    with tempfile.TemporaryDirectory() as directory:
        stable = Path(directory) / 'RaidBossStrategyStudio'
        source = stable / 'config/raid-boss-strategies.user.json'
        source.parent.mkdir(parents=True)
        source.write_text('{"rules": []}', encoding='utf-8')
        destination = preview_root(stable)
        seed_preview(stable, destination)
        copied = destination / source.relative_to(stable)
        assert json.loads(copied.read_text()) == {'rules': []}
        copied.write_text('{"rules": [{"name": "draft"}]}', encoding='utf-8')
        seed_preview(stable, destination)
        assert 'draft' in copied.read_text()
        assert source.read_text() == '{"rules": []}'


def test_retired_flow_export_preserves_data_but_import_rejects_execution():
    import threading
    import chimera_web as web
    from strategy_storage import write_strategy_store
    from boss_modes import default_store, update_mode_strategy
    _, _, generic, fallback = fixture()
    config = {**web.strategy_template('chimera'), **config_for([generic, fallback])}
    with tempfile.TemporaryDirectory() as directory:
        store_path = Path(directory) / 'strategies.json'
        store = update_mode_strategy(default_store(), 'chimera', config)
        write_strategy_store(store_path, store)
        service = object.__new__(web.ChimeraService)
        service.lock = threading.RLock()
        with patch.object(web, 'STRATEGY_STORE', store_path), patch.object(service, 'strategy_store', side_effect=lambda: json.loads(store_path.read_text(encoding='utf-8'))):
            document = service.export_strategy_profile('default', 'chimera')
            assert document['version'] == 2  # Stable 1.0.5 rejects rather than silently running the list backup.
            try:
                service.import_strategy_profile(document, 'chimera')
            except ValueError as error:
                assert '1.0.6' in str(error)
            else:
                raise AssertionError('Retired flow must not be imported as executable')
            assert service.strategy('chimera')['strategyFlow'] == config['strategyFlow']


def test_start_journal_preserves_flow_and_decision_route_without_starting_worker():
    import controller_manager as managers
    from decision_observability import decision_details
    state, owner, generic, fallback = fixture()
    config = config_for([owner, generic, fallback])
    decision = evaluate_flow(config, state)
    with tempfile.TemporaryDirectory() as directory, patch.object(managers, 'PROJECT_ROOT', Path(directory)), \
            patch.object(managers, 'ControllerPauseEvent'), patch.object(managers.threading, 'Thread') as worker:
        manager = managers.ControllerManager()
        manager.start(123, 'offline account', 456, 'chimera', config=config, strategy_id='offline-flow')
        assert worker.call_args.kwargs['target'] == manager._prepare_and_run
        records = [json.loads(line) for line in manager.journal.path.read_text(encoding='utf-8').splitlines()]
        start = next(row for row in records if row['event'] == 'start')
        assert start['strategy']['strategyFlow'] == config['strategyFlow']
        assert start['strategy']['executionMode'] == 'flow'
        manager.append('@@raid-telemetry ' + json.dumps({'decision': decision_details(state, decision)}))
        records = [json.loads(line) for line in manager.journal.path.read_text(encoding='utf-8').splitlines()]
        logged = next(row['decision'] for row in records if row['event'] == 'decision')
        assert logged['flowTrace'] == state['_flowTrace']
        assert logged['flowRevision'] == flow_revision(config['strategyFlow'])
        assert any(row.get('reason') == 'skill_reserved' for row in logged['flowTrace'])
        manager.pause_event.close()


def test_lydia_ten_debuff_rules_keep_same_choices_after_graph_conversion():
    from lydia_trial_rules import BASE_GROUPS, update_profile
    from test_lydia_trial_rules import profile, state as lydia_state
    original = update_profile(profile())
    graph = {**copy.deepcopy(original), 'executionMode': 'flow', 'strategyFlow': convert_rules(original)}
    bases = [group[1][-1] for group in BASE_GROUPS]
    for effects in (bases, bases + [500], bases + [110], bases + [500, 110], bases[:-1] + [490], bases[:-1] + [491], []):
        for ready in ((1, 2, 3), (1, 2), (1,)):
            for completed in (False, True):
                snapshot = lydia_state(effects, ready=ready, completed=completed)
                assert signature(c.evaluate(original, copy.deepcopy(snapshot))) == signature(evaluate_flow(graph, snapshot))


def test_live_evaluator_rejects_retired_flow_without_falling_back_to_list():
    state, owner, generic, fallback = fixture()
    before = copy.deepcopy(state)
    try:
        c.evaluate(config_for([generic, fallback]), state)
    except ValueError as error:
        assert '1.0.6' in str(error)
    else:
        raise AssertionError('Hidden flow execution or silent list fallback')
    assert state == before
    archived = {'rules': [generic, fallback], 'executionMode': 'list', 'strategyFlow': {'archived': True}}
    assert c.evaluate(archived, state) is not None

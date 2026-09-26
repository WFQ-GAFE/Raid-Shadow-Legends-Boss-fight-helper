"""Trial casts reserve cooldowns against generic explicit rules and openers."""
import copy
from chimera_controller import evaluate
from test_chimera_strategy_tree import sample_state


def fixture():
    state = sample_state()
    state['bosses'][0]['challenges'] = [{'id': 8000502, 'eligibleNow': True, 'activeInChain': True, 'completed': False}]
    state['_decisionTrace'] = []
    action = {'type': 'cast', 'skillTypeId': 88962, 'skillSlot': 2, 'target': {'type': 'self'}}
    owner = {'name': 'trial-S2', 'when': {'activeHeroTypeId': [8896], 'form': ['Ram'],
        'eligibleTrialsAny': [8000502], 'chimeraTurnAtLeast': 99}, 'action': copy.deepcopy(action)}
    generic = {'name': 'generic-S2', 'when': {}, 'action': action}
    fallback = {'name': 'fallback-A1', 'when': {}, 'action': {'type': 'cast',
        'skillTypeId': 88961, 'skillSlot': 1, 'target': {'type': 'boss'}}}
    return state, owner, generic, fallback


def test_generic_cast_cannot_spend_trial_skill_in_either_rule_order():
    for reverse in (False, True):
        state, owner, generic, fallback = fixture()
        rules = [owner, generic] if reverse else [generic, owner]
        decision = evaluate({'rules': [*rules, fallback]}, state)
        assert decision.rule == 'fallback-A1'
        assert any(row.get('reason') == 'skill_reserved_for_trial_rule' and row['reservedByRules'] == ['trial-S2'] for row in state['_decisionTrace'])
        assert all(isinstance(row.get('conditions'), list) for row in state['_decisionTrace'])
        assert all(isinstance(row.get('name'), str) and isinstance(row.get('outcome'), str) for row in state['_decisionTrace'])


def test_ready_trial_owner_wins_even_when_generic_rule_is_first():
    state, owner, generic, fallback = fixture()
    owner['when']['chimeraTurnAtLeast'] = 1
    assert evaluate({'rules': [generic, owner, fallback]}, state).rule == 'trial-S2'


def test_finished_trial_or_different_form_releases_generic_skill():
    for mode in ('completed', 'ineligible', 'form'):
        state, owner, generic, fallback = fixture()
        if mode == 'completed': state['bosses'][0]['challenges'][0]['completed'] = True
        elif mode == 'ineligible': state['bosses'][0]['challenges'][0]['eligibleNow'] = False
        else: state['chimera']['currentForm'] = 'Lion'
        assert evaluate({'rules': [generic, owner, fallback]}, state).rule == 'generic-S2'


def test_no_fallback_does_not_cast_and_a_trial_a1_is_never_reserved():
    state, owner, generic, fallback = fixture()
    assert evaluate({'rules': [generic, owner]}, state) is None
    # A1 has no cooldown to keep; reserving it stalled heroes whose other
    # skills were cooling down (1.0.6).
    owner['action'] = copy.deepcopy(fallback['action'])
    assert evaluate({'rules': [owner, fallback]}, state).rule == 'fallback-A1'


def test_default_priority_uses_a_reserved_skill_rather_than_stalling():
    state, owner, generic, fallback = fixture()
    default = {'name': 'default', 'when': {}, 'action': {'type': 'defaultSkillPriority',
        'prioritySkills': [{'skillTypeId': 88962, 'skillSlot': 2, 'target': {'type': 'self'}}],
        'blockedSkillTypeIds': []}}
    decision = evaluate({'rules': [owner, default]}, state)
    assert decision.rule.startswith('default · ') and decision.skill['typeId'] == 88962
    assert any(row.get('outcome') == 'reservation_released' for row in state['_decisionTrace'])


def test_first_turn_skill_cannot_bypass_trial_reservation():
    state, owner, generic, fallback = fixture()
    state['_chimeraFormHeroFirstTurn'] = True
    opener = {'name': 'opener', 'when': {}, 'action': {'type': 'defaultSkillPriority',
        'firstTurnSkill': {'skillTypeId': 88962, 'skillSlot': 2, 'target': {'type': 'self'}},
        'prioritySkills': [fallback['action']], 'blockedSkillTypeIds': []}}
    assert evaluate({'rules': [opener, generic, owner, fallback]}, state).rule == 'fallback-A1'


def test_one_ready_trial_owner_can_execute_while_another_is_waiting():
    state, owner, generic, fallback = fixture()
    ready = copy.deepcopy(owner)
    ready['name'] = 'ready-trial-S2'
    ready['when']['chimeraTurnAtLeast'] = 1
    assert evaluate({'rules': [generic, owner, ready, fallback]}, state).rule == 'ready-trial-S2'


def test_reservation_does_not_leak_into_later_evaluations():
    state, owner, generic, fallback = fixture()
    assert evaluate({'rules': [generic, owner, fallback]}, state).rule == 'fallback-A1'
    assert evaluate({'rules': [generic, fallback]}, state).rule == 'generic-S2'

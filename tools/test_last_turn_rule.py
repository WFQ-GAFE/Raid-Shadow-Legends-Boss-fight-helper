"""Rules that wait for the hero's last action before the Boss (lastTurnBeforeBoss, 1.1.5)."""
import copy

from chimera_controller import LAST_TURN_BEFORE_BOSS, evaluate, validate_strategy_config
from test_chimera_strategy_tree import sample_state
from ui_text import render


S2 = {'type': 'cast', 'skillTypeId': 88962, 'skillSlot': 2, 'target': {'type': 'self'}}
A1 = {'type': 'cast', 'skillTypeId': 88961, 'skillSlot': 1, 'target': {'type': 'boss'}}


def rules(*, second_waiting=False):
    waiting = {'name': 'wait-S2', 'when': {}, 'action': copy.deepcopy(S2), LAST_TURN_BEFORE_BOSS: True}
    other = {'name': 'other-S2', 'when': {}, 'action': copy.deepcopy(S2)}
    if second_waiting:
        other[LAST_TURN_BEFORE_BOSS] = True
    fallback = {'name': 'fallback-A1', 'when': {}, 'action': copy.deepcopy(A1)}
    return [waiting, other, fallback]


def state_with(answer=None):
    state = sample_state()
    state['_decisionTrace'] = []
    calls = []
    if answer is not None:
        def oracle(alternative, decision):
            calls.append((alternative.rule, decision.rule))
            return answer
        state['_lastTurnOracle'] = oracle
    return state, calls


def test_without_a_simulation_the_rule_fires_at_once():
    state, _ = state_with()
    decision = evaluate({'rules': rules()}, state)
    assert decision.rule == 'wait-S2' and decision.waited_rule is None


def test_the_hero_acting_again_before_the_boss_takes_the_other_action_now():
    state, calls = state_with('again')
    decision = evaluate({'rules': rules()}, state)
    # The stand-in is what the hero would do without this rule.
    assert decision.rule == 'other-S2' and decision.waited_rule == 'wait-S2'
    assert calls == [('other-S2', 'wait-S2')]
    check = [row for row in state['_decisionTrace'] if row.get('outcome') == 'last_turn_check']
    assert check == [{'outcome': 'last_turn_check', 'rule': 'wait-S2', 'alternative': 'other-S2', 'result': 'again'}]


def test_the_last_action_before_the_boss_casts_the_rule():
    for answer in ('boss', 'end', 'unknown'):
        state, calls = state_with(answer)
        decision = evaluate({'rules': rules()}, state)
        assert decision.rule == 'wait-S2' and decision.waited_rule is None and len(calls) == 1


def test_another_waiting_rule_is_never_the_stand_in():
    state, calls = state_with('again')
    decision = evaluate({'rules': rules(second_waiting=True)}, state)
    # One check only (the stand-in is not checked again) and never the other waiting rule.
    assert decision.rule == 'fallback-A1' and decision.waited_rule == 'wait-S2'
    assert calls == [('fallback-A1', 'wait-S2')]


def test_with_nothing_else_to_do_the_rule_fires_now():
    state, calls = state_with('again')
    decision = evaluate({'rules': rules()[:1]}, state)
    assert decision.rule == 'wait-S2' and decision.waited_rule is None and calls == []


def test_the_flag_must_be_a_boolean():
    config = {'mode': 'execute', 'rules': rules()}
    validate_strategy_config(config)
    config['rules'][0][LAST_TURN_BEFORE_BOSS] = 'yes'
    try:
        validate_strategy_config(config)
    except ValueError as error:
        assert 'rules[0].lastTurnBeforeBoss' in render(str(error), 'zh-CN')
    else:
        raise AssertionError('a non-boolean lastTurnBeforeBoss must be rejected')

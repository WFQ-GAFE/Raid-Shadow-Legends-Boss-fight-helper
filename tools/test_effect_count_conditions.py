"""Offline behavior checks for visual per-target buff/debuff count rules."""
import copy
from chimera_controller import effect_count_checks, matches, validate_condition_tree
from decision_observability import condition_details


def effect(kind, type_id):
    return {'effectKindId': kind, 'effectTypeId': type_id, 'turnsLeft': 2}


def state(buffs=2, debuffs=8):
    return {'chimera': {'id': 5}, 'bosses': [
        {'id': 5, 'typeId': 26916, 'effects': [effect(2004, 280) for _ in range(buffs)]
         + [effect(3009, 80) for _ in range(debuffs)]}],
        'heroes': [{'id': 0, 'typeId': 4716, 'teamPosition': 1, 'effects': [effect(2102, 141)]}]}


def node(polarity='debuff', target='boss', **fields):
    return {'type': 'effectCount', 'target': target, 'polarity': polarity, 'countAtLeast': 10, **fields}


def check(condition, snapshot):
    validate_condition_tree(condition, path='test')
    return matches({'conditionTree': condition}, snapshot)


def test_mixed_effects_do_not_fill_debuff_or_buff_count():
    snapshot = state()
    assert check(node('all'), snapshot)
    assert not check(node('buff'), snapshot)
    assert not check(node('debuff'), snapshot)
    assert check(node('buff'), state(10, 0))
    assert not check(node('debuff'), state(10, 0))
    assert check(node('debuff'), state(0, 10))
    assert not check(node('buff'), state(0, 10))
    assert check(node('debuff', countAtMost=10), state(1, 10))
    assert not check(node('debuff', countAtMost=10), state(0, 11))


def test_effect_slots_not_kinds_stacks_or_duration_and_unknowns_not_classified():
    snapshot = state(0, 9)
    # Nine separate poison slots count as nine, even with identical type IDs.
    snapshot['bosses'][0]['effects'].append({'effectTypeId': 740, 'turnsLeft': 0})
    assert check(node(), snapshot)  # Smite fallback identity; observed slot remains present.
    snapshot['bosses'][0]['effects'][-1] = {'effectTypeId': 999999, 'stacks': 20, 'turnsLeft': 10}
    assert not check(node(), snapshot)
    assert check(node('all'), snapshot)
    assert effect_count_checks(node(), snapshot)[0]['other'] == 1
    snapshot['bosses'][0]['effects'] = [effect(3009, 80) | {'stacks': 10}]
    assert not check(node(), snapshot)


def test_targets_are_counted_separately_and_unknown_snapshots_do_not_mean_zero():
    snapshot = state(0, 6)
    second = copy.deepcopy(snapshot['bosses'][0])
    second['id'] = 6
    snapshot['bosses'].append(second)
    assert not check(node(target='bossAny'), snapshot)
    second['effects'] += [effect(3009, 80)] * 4
    assert check(node(target='bossAny'), snapshot)
    assert not check(node(target='bossAll'), snapshot)
    snapshot['_conditionBossId'] = 6
    assert check(node(target='bossPriority'), snapshot)
    second['dead'] = True
    assert not check(node(target='bossAny'), snapshot)
    assert not check(node(target='bossPriority'), snapshot)
    assert check(node('buff', target='ally', heroTypeId=4716, teamPosition=1, countAtLeast=1), snapshot)
    assert not check(node('buff', target='ally', heroTypeId=4716, teamPosition=2, countAtLeast=1), snapshot)
    snapshot['heroes'].append(copy.deepcopy(snapshot['heroes'][0]) | {'id': 1, 'teamPosition': 2, 'effects': []})
    assert check(node('buff', target='ally', heroTypeId=4716, teamPosition=1, countAtLeast=1), snapshot)
    assert not check(node('buff', target='ally', heroTypeId=4716, teamPosition=2, countAtLeast=1), snapshot)
    assert not check(node('buff', target='ally', heroTypeId=4716, countAtLeast=1), snapshot)
    empty = state(0, 0)
    assert check(node(countAtLeast=0, countAtMost=0), empty)
    del empty['bosses'][0]['effects']
    assert not check(node(countAtLeast=0, countAtMost=0), empty)
    assert not check(node(negate=True), empty)


def test_log_explains_count_and_composes_with_forbidden_effect_guard():
    snapshot = state(0, 10)
    tree = {'type': 'group', 'operator': 'all', 'children': [node(),
        {'type': 'effect', 'target': 'boss', 'presence': 'missing', 'effect': {'effectTypeId': 290}}]}
    assert check(tree, snapshot)
    snapshot['bosses'][0]['effects'][-1] = effect(3007, 290)
    assert not check(tree, snapshot)
    detail = condition_details(node(), state(), matches, [128], count_checks=effect_count_checks)
    assert detail['passed'] is False
    row = detail['counts'][0]
    assert (row['buffs'], row['debuffs'], row['count'], row['countAtLeast']) == (2, 8, 8, 10)
    for polarity in ('buff', 'debuff', 'all'):
        assert check(node(polarity, negate=True), state()) is not check(node(polarity), state())


def test_malformed_ranges_are_rejected_before_execution():
    for changes in ({'polarity': 'anything'}, {'countAtLeast': True}, {'countAtLeast': -1},
                    {'countAtLeast': 1.5}, {'countAtLeast': None}, {'countAtMost': 9},
                    {'target': 'ally', 'heroTypeId': 0}, {'target': 'ally', 'heroTypeId': 4716, 'teamPosition': 9}):
        condition = node(**changes)
        try:
            validate_condition_tree(condition, path='test')
        except ValueError:
            pass
        else:
            raise AssertionError(condition)
        assert not matches({'conditionTree': condition | {'negate': True}}, state())


def main():
    for name, test in list(globals().items()):
        if name.startswith('test_') and callable(test):
            test()
            print('PASS', name)


if __name__ == '__main__':
    main()

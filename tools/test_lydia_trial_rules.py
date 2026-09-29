"""Lydia's ten-debuff trial rules as a realistic strategy fixture.

Pure decision tests using artificial states; never calls process_state/IPC.
The rule builder below establishes enough existing, named debuffs for A3's
permitted effects to fill the remaining slots if they land. It cannot
guarantee rolls. Other diagnostics tests reuse it.
"""
import copy
import unittest

from chimera_controller import evaluate, matches, strict_rule_reserved_skill_ids, validate_strategy_config

TRIAL_ID = 8000621
PREFIX = '十减益试炼 · 莉迪亚'
# Eight applicable base categories for this team's Viper trial. Fear and True
# Fear cannot be applied to this form and must never substitute for a category.
# Strong/weak variants are conservatively counted as a single category.
BASE_GROUPS = (
    ('降低攻击', (130, 131)),
    ('降低防御', (150, 151)),
    ('虚弱', (350, 351)),
    ('降低速度', (170, 171)),
    ('降低抗性', (720, 721)),
    ('苦痛连接', (640,)),
    ('血液榨取', (460,)),
    ('生命值燃烧', (470,)),
)
# Published effect kinds cover both strengths without duplicating the threshold
# expression. Smite is also observed on this team via blessings.
BASE_KINDS = (
    'StatusReduceAttack', 'StatusReduceDefence', 'IncreaseDamageTaken',
    'StatusReduceSpeed', 'StatusReduceResistance', 'MirrorDamage',
    'LifeDrainOnDamage', 'AoEContinuousDamage',
)


def effect_node(identity: int, *, missing: bool = False) -> dict:
    selector = {'effectTypeId': identity}
    if not missing:
        # Missing duration data and already expired effects cannot qualify.
        selector['turnsAtLeast'] = 1
    return {'type': 'effect', 'target': 'boss',
            'presence': 'missing' if missing else 'has', 'effect': selector}


def at_most_one_missing(nodes: list[dict]) -> dict | None:
    """Balanced AND/OR expression compatible with the existing 64-node editor.

    One half must be complete and the other may miss one category. None denotes
    the always-true requirement of zero out of one; it is simplified away.
    """
    if len(nodes) <= 1:
        return None
    if len(nodes) == 2:
        return {'type': 'group', 'operator': 'any', 'children': nodes}

    def all_of(items):
        items = [item for item in items if item is not None]
        return items[0] if len(items) == 1 else {'type': 'group', 'operator': 'all', 'children': items}

    midpoint = len(nodes) // 2
    left, right = nodes[:midpoint], nodes[midpoint:]
    return {'type': 'group', 'operator': 'any', 'children': [
        all_of([all_of(left), at_most_one_missing(right)]),
        all_of([at_most_one_missing(left), all_of(right)]),
    ]}


def build_lydia_rules() -> list[dict]:
    scope = {'activeHeroTypeId': [4710, 4716], 'form': ['Snake'], 'activeTrialsAny': [TRIAL_ID]}
    nodes = [effect_node(290, missing=True), effect_node(100, missing=True),
             {'type': 'group', 'operator': 'any', 'children':
              [effect_node(500, missing=True), effect_node(110, missing=True)]}]
    categories = [
        {'type': 'effect', 'target': 'boss', 'presence': 'has',
         'effect': {'kind': kind, 'turnsAtLeast': 1}}
        for kind in BASE_KINDS
    ]
    categories.append(effect_node(740))
    nodes.append(at_most_one_missing(categories))
    # In agent.cpp, eligibleNow = activeInChain && matchingCurrentForm. The
    # explicit Snake form plus active trial scope therefore identifies the
    # window, including for strict skill reservation, without a second rule.
    # The original default policy then skips reserved A3 and selects A2/A1.
    return [{
        'name': f'{PREFIX} 九类可用减益任八类时用A3补位',
        'when': {**scope, 'conditionTree': {'type': 'group', 'operator': 'all', 'children': nodes}},
        'action': {'type': 'cast', 'skillSlot': 3, 'skillTypeId': 47103, 'target': {'type': 'boss'}},
    }]


def update_profile(profile: dict) -> dict:
    if profile.get('name', '').strip().casefold() != 'marius team 2' or profile.get('bossMode') != 'chimera':
        raise ValueError('此修改仅适用于奇美拉 marius team 2')
    if TRIAL_ID not in profile.get('objectives', {}).get('mandatoryTrialIds', []):
        raise ValueError('目标策略没有选择试炼 8000621')
    if not set(profile.get('team', {}).get('heroTypeIds', [])) & {4710, 4716}:
        raise ValueError('目标队伍中没有莉迪亚')
    # An opener runs before strict rules, so an A3 opener would invalidate this
    # configuration-only guard. Do not silently edit unrelated opener policy.
    for rule in profile.get('rules', []):
        action = rule.get('action', {})
        if action.get('type') != 'defaultSkillPriority':
            continue
        forms = action.get('formPolicies', {})
        policy = forms.get('Snake', action)
        if policy.get('firstTurnSkill', {}).get('skillTypeId') == 47103:
            raise ValueError('蛇形存在莉迪亚 A3 首回合覆盖，需要先明确处理')
    result = copy.deepcopy(profile)
    previous = next((rule for rule in result['rules'] if rule.get('name', '').startswith(PREFIX)), None)
    originals = [rule for rule in result['rules'] if not rule.get('name', '').startswith(PREFIX)]
    result['rules'] = build_lydia_rules() + originals
    if previous:
        # Preserve user changes to the managed rule's scope (e.g. eligible trial).
        result['rules'][0]['when'].update({key: value for key, value in previous.get('when', {}).items()
                                          if key != 'conditionTree'})
    return result


def profile():
    return {'name': 'marius team 2', 'mode': 'execute', 'bossMode': 'chimera',
            'scope': {'battleKind': 'AllianceChimera'},
            'team': {'heroTypeIds': [7376, 10416, 8736, 9896, 4716]},
            'objectives': {'mandatoryTrialIds': [8000620, TRIAL_ID]},
            'rules': [{'name': 'original default',
                       'when': {'activeHeroTypeId': [4710, 4716], 'form': ['Snake']},
                       'action': {'type': 'defaultSkillPriority', 'reserveStrictRuleSkills': True,
                                  'prioritySkills': [{'skillSlot': slot, 'skillTypeId': 47100 + slot,
                                                      'target': {'type': 'boss'}} for slot in (3, 2, 1)]}}]}


def state(types=(), *, active=True, completed=False, eligible=True, ready=(1, 2, 3), form='Snake'):
    kinds = {value: kind for (_, values), kind in zip(BASE_GROUPS, BASE_KINDS) for value in values}
    effects = [{'effectTypeId': value, 'effectKind': kinds.get(value, ''), 'turnsLeft': 2} for value in types]
    skills = [{'slot': slot, 'typeId': 47100 + slot, 'ready': slot in ready,
               'skillId': slot - 1, 'validTargetIds': [5]} for slot in (1, 2, 3)]
    return {'bossMode': 'chimera', 'activeHeroId': 4, 'activeHeroTypeId': 4716,
            'activeHeroTurnCount': 3, 'form': form, 'skills': skills,
            '_chimeraFormHeroFirstTurn': False,
            'battle': {'kind': 'AllianceChimera', 'round': 1, 'turn': 123, 'currentDamage': 1},
            'chimera': {'id': 5, 'currentForm': form, 'turnCount': 23},
            'heroes': [{'id': 4, 'typeId': 4716, 'dead': False, 'effects': [], 'skills': skills}],
            'bosses': [{'id': 5, 'typeId': 8000, 'healthPct': 100, 'effects': effects,
                        'challenges': [{'id': TRIAL_ID, 'activeInChain': active,
                                        'eligibleNow': eligible, 'completed': completed}]}]}


class LydiaRulesTests(unittest.TestCase):
    def setUp(self):
        self.original = profile()
        self.updated = update_profile(self.original)
        self.base = [group[1][-1] for group in BASE_GROUPS]

    def selected(self, snapshot):
        decision = evaluate(self.updated, snapshot)
        return decision.skill['typeId'] if decision else None

    def test_originals_preserved_and_update_is_idempotent(self):
        self.assertEqual(self.original['rules'], self.updated['rules'][1:])
        self.assertEqual(2, len(self.updated['rules']))
        self.assertEqual(1, len(build_lydia_rules()))
        self.assertEqual(self.updated, update_profile(self.updated))
        self.assertEqual(self.original, {**self.updated, 'rules': self.original['rules']})
        validate_strategy_config(self.updated)

    def test_eight_bases_and_ninth_slot_refresh_cases(self):
        for extra in ([], [500], [110]):
            self.assertEqual(47103, self.selected(state(self.base + extra)))
        # Both present -> A3 can only refresh, so use A2/A1 instead.
        self.assertEqual(47102, self.selected(state(self.base + [500, 110])))

    def test_strong_and_weak_variants_share_one_required_category(self):
        self.assertEqual(47103, self.selected(state([group[1][0] for group in BASE_GROUPS])))
        self.assertEqual(47103, self.selected(state(self.base)))

    def test_viper_fear_and_true_fear_cannot_replace_any_required_base(self):
        self.assertEqual(8, len(BASE_GROUPS))
        for missing in range(len(self.base)):
            actual = self.base[:missing] + self.base[missing + 1:]
            for immune_effects in ([490], [491], [490, 491]):
                self.assertEqual(47102, self.selected(state(actual + immune_effects)))
        selectors = []
        def visit(node):
            if node.get('type') == 'effect':
                selectors.append(node['effect'].get('effectTypeId'))
            for child in node.get('children', []):
                visit(child)
        visit(self.updated['rules'][0]['when']['conditionTree'])
        self.assertTrue({490, 491}.isdisjoint(selectors))

    def test_default_policy_already_handles_fallback_without_extra_cast_rules(self):
        snapshot = state([])
        self.assertEqual({47103}, strict_rule_reserved_skill_ids(self.updated['rules'], snapshot))
        decision = evaluate(self.updated, snapshot)
        self.assertEqual(47102, decision.skill['typeId'])
        self.assertTrue(decision.rule.startswith('original default'))
        snapshot['skills'][1]['ready'] = False
        self.assertEqual(47101, self.selected(snapshot))

    def test_replaces_legacy_managed_rules_without_touching_originals(self):
        legacy = copy.deepcopy(self.original)
        legacy['rules'] = [{'name': PREFIX + ' old ' + str(index)} for index in range(11)] + legacy['rules']
        self.assertEqual(self.updated, update_profile(legacy))

    def test_insufficient_bases_and_buffs_cannot_fake_slots(self):
        self.assertEqual(47102, self.selected(state(self.base[:7] + [500, 110])))
        self.assertEqual(47102, self.selected(state(self.base[:7] + [500, 100, 141, 161])))
        self.assertEqual(47102, self.selected(state(self.base[:7] + [500] * 3)))

    def test_block_active_or_block_debuffs_prevents_a3(self):
        for identity in (290, 100):
            self.assertEqual(47102, self.selected(state(self.base + [identity])))

    def test_missing_and_expired_duration_cannot_qualify(self):
        for duration in (None, 0):
            snapshot = state(self.base)
            snapshot['bosses'][0]['effects'][0]['turnsLeft'] = duration
            self.assertEqual(47102, self.selected(snapshot))

    def test_conditions_are_current_not_assumed_from_prior_cast(self):
        self.assertEqual(47103, self.selected(state(self.base)))
        self.assertEqual(47102, self.selected(state(self.base + [110, 290])))
        self.assertEqual(47103, self.selected(state(self.base + [110])))

    def test_unready_skill_and_invalid_target_not_used(self):
        self.assertEqual(47102, self.selected(state(self.base, ready=(1, 2))))
        self.assertEqual(47101, self.selected(state([], ready=(1,))))
        snapshot = state(self.base)
        snapshot['skills'][2]['validTargetIds'] = []
        self.assertEqual(47102, self.selected(snapshot))

    def test_a3_is_reserved_only_for_current_trial_window(self):
        for args in ({'active': False, 'eligible': False}, {'completed': True}):
            self.assertEqual(47103, self.selected(state([], **args)))
        self.assertEqual(47102, self.selected(state([], eligible=False)))
        self.assertIsNone(self.selected(state(self.base, form='Ram')))

    def test_a3_opener_conflict_is_rejected(self):
        self.original['rules'][0]['action']['formPolicies'] = {'Snake': {
            'firstTurnSkill': {'skillTypeId': 47103}}}
        with self.assertRaises(ValueError):
            update_profile(self.original)

    def test_all_presence_combinations_obey_precast_predicate(self):
        rules = build_lydia_rules()
        identities = [group[1][-1] for group in BASE_GROUPS] + [740]
        for mask in range(1 << len(identities)):
            chosen = [identity for index, identity in enumerate(identities) if mask & (1 << index)]
            for extras in ([], [500], [110], [500, 110], [290], [100]):
                snapshot = state(chosen + extras)
                allowed = any(matches(rule['when'], snapshot) for rule in rules)
                expected = len(chosen) >= 8 and not ({290, 100} & set(extras)) and not {500, 110} <= set(extras)
                self.assertEqual(expected, allowed, (mask, extras))

    def test_smite_replaces_each_missing_base_but_fear_never_does(self):
        for index in range(8):
            other = self.base[:index] + self.base[index + 1:]
            for extra in ([], [500], [110]):
                self.assertEqual(47103, self.selected(state(other + [740] + extra)))
            self.assertEqual(47102, self.selected(state(other + [490, 491])))
        self.assertEqual(47102, self.selected(state(self.base[:6] + [740])))

    def test_existing_scope_changes_are_preserved(self):
        self.updated['rules'][0]['when']['eligibleTrialsAny'] = [TRIAL_ID]
        again = update_profile(self.updated)
        self.assertEqual([TRIAL_ID], again['rules'][0]['when']['eligibleTrialsAny'])
        validate_strategy_config(again)


if __name__ == '__main__':
    unittest.main()

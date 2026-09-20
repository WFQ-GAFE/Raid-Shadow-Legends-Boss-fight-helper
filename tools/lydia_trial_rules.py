"""Build 1.0.5-compatible Lydia rules without adding live controller APIs.

These predicates establish enough existing, named debuffs for A3's permitted
effects to fill the remaining slots if they land. They cannot guarantee rolls.
"""
from __future__ import annotations

import copy

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


def group_node(identities: tuple[int, ...]) -> dict:
    nodes = [effect_node(identity) for identity in identities]
    return nodes[0] if len(nodes) == 1 else {'type': 'group', 'operator': 'any', 'children': nodes}


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

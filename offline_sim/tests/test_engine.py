import dataclasses
import unittest

from offline_sim.benchmark import benchmark
from offline_sim.engine import Action, Actor, Effect, IllegalAction, Scenario, Skill, TrialLedger, TrialWindow, run, trial_policy
from offline_sim.fixtures import ram_tail_fixture


def single_actor(*skills, **kwargs):
    return Scenario('人工边界测试', (Actor('a', 'A', tuple(skills)),), ('a',), **kwargs)


BASIC = Skill('basic', '普通攻击', 'boss', damage=10)


class LegalityTests(unittest.TestCase):
    def test_reject_wrong_actor_target_skill_and_stale_actions_without_consuming_rng(self):
        window = TrialWindow(ram_tail_fixture(), 3)
        observed = window.observe()
        legal = observed.legal_actions[0]
        for invalid in (dataclasses.replace(legal, actor='debuffer_a'),
                        dataclasses.replace(legal, target='unknown'),
                        dataclasses.replace(legal, skill='invented'),
                        dataclasses.replace(legal, observation_id=-1)):
            before = (window._rng.getstate(), window._index, dict(window._cooldowns), dict(window._effects['boss']))
            with self.assertRaises(IllegalAction):
                window.step(invalid)
            self.assertEqual(before, (window._rng.getstate(), window._index, dict(window._cooldowns), dict(window._effects['boss'])))
            self.assertEqual([], window.events)
        window.step(legal)
        with self.assertRaises(IllegalAction):
            window.step(legal)
        self.assertEqual(1, len(window.events))

    def test_cooldown_uses_owner_turns_and_observation_is_idempotent(self):
        strong = Skill('strong', '冷却技能', 'boss', cooldown=3, damage=100)
        window = TrialWindow(single_actor(BASIC, strong), 0)
        window.step(window.observe().legal_actions[1])
        for _ in range(2):
            observed = window.observe()
            cooldown = dict(window._cooldowns)
            self.assertIs(observed, window.observe())
            self.assertEqual(cooldown, window._cooldowns)
            self.assertEqual(['basic'], [action.skill for action in observed.legal_actions])
            with self.assertRaises(IllegalAction):
                window.step(Action('a', 'strong', 'boss', observed.observation_id))
            window.step(observed.legal_actions[0])
        self.assertEqual(['basic', 'strong'], [action.skill for action in window.observe().legal_actions])

    def test_teammate_turn_does_not_tick_other_hero_cooldown(self):
        skill = dataclasses.replace(BASIC, cooldown=3)
        scenario = Scenario('人工双角色', (Actor('a', 'A', (skill,)), Actor('b', 'B', (BASIC,))), ('a', 'b'))
        window = TrialWindow(scenario, 0)
        window.step(window.observe().legal_actions[0])
        window.step(window.observe().legal_actions[0])
        self.assertEqual(3, window._cooldowns[('a', 'basic')])

    def test_passive_and_blocked_skills_are_not_clickable(self):
        window = TrialWindow(single_actor(BASIC, dataclasses.replace(BASIC, identity='passive', passive=True),
                                          dataclasses.replace(BASIC, identity='blocked', blocked=True)), 0)
        self.assertEqual(['basic'], [action.skill for action in window.observe().legal_actions])

    def test_self_target_is_actual_current_actor(self):
        window = TrialWindow(single_actor(Skill('buff', '增益', 'self')), 0)
        self.assertEqual('a', window.observe().legal_actions[0].target)
        with self.assertRaises(IllegalAction):
            window.step(Action('a', 'buff', 'boss', 0))

    def test_unsupported_state_stops_without_inventing_action(self):
        scenario = single_actor(BASIC)
        dead = dataclasses.replace(scenario, actors=(dataclasses.replace(scenario.actors[0], alive=False),))
        self.assertEqual('unsupported_dead_actor_transition', run(dead, trial_policy, 0)[0]['stoppedReason'])
        blocked = single_actor(dataclasses.replace(BASIC, blocked=True))
        result, events = run(blocked, trial_policy, 0)
        self.assertEqual('no_modelled_legal_action', result['stoppedReason'])
        self.assertEqual([], events)

    def test_policy_observation_has_no_hidden_rng_and_is_immutable(self):
        observed = TrialWindow(ram_tail_fixture(), 777).observe()
        fields = {field.name for field in dataclasses.fields(observed)}
        self.assertFalse(fields & {'seed', 'rng', '_rng', 'future', 'future_rolls', 'window'})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            observed.stage = 3
        with self.assertRaises(dataclasses.FrozenInstanceError):
            observed.ready_skills[0].cooldown = 0

    def test_reject_real_prediction_label_and_invalid_model_parameters(self):
        for scenario in (dataclasses.replace(single_actor(BASIC), evidence='verified_real_roster'),
                         single_actor(BASIC, boss_turns=0), single_actor(BASIC, boss_turns=1.5),
                         single_actor(dataclasses.replace(BASIC, damage=float('inf'))),
                         single_actor(dataclasses.replace(BASIC, damage_kind='unknown')),
                         single_actor(Skill('nan', '异常概率', 'boss', effects=(Effect('x', 'boss', 1, float('nan')),))),
                         single_actor(BASIC, effect_slots=11)):
            with self.assertRaises(ValueError):
                TrialWindow(scenario, 0)


class TrialAccountingTests(unittest.TestCase):
    def setUp(self):
        self.scenario = ram_tail_fixture(1)

    def test_distinct_effects_need_accuracy_and_repeated_identity_counts_once(self):
        ledger = TrialLedger()
        ledger.record(self.scenario, 0, set(), set(), ['weaken'], 0)
        self.assertEqual(set(), ledger.accepted_effects)
        ledger.record(self.scenario, 0, {'increase_accuracy'}, set(), ['weaken', 'weaken'], 0)
        ledger.record(self.scenario, 0, {'increase_accuracy'}, {'weaken'}, ['weaken'], 0)
        self.assertEqual({'weaken'}, ledger.accepted_effects)
        self.assertEqual(0, ledger.stage)

    def test_unlock_does_not_count_same_cast_for_next_trial(self):
        ledger = TrialLedger()
        ledger.record(self.scenario, 0, {'increase_accuracy', 'increase_critical_rate'}, {'weaken'},
                      [str(index) for index in range(7)], 9_000_000)
        self.assertEqual(1, ledger.stage)
        self.assertEqual(0, ledger.cumulative_damage)
        self.assertEqual(['8000604'], ledger.completed)

    def test_cumulative_requires_both_conditions_at_damage_time(self):
        ledger = TrialLedger(stage=1)
        for own, boss in ((set(), {'weaken'}), ({'increase_critical_rate'}, set())):
            ledger.record(self.scenario, 1, own, boss, [], 9_000_000)
        self.assertEqual(0, ledger.cumulative_damage)
        ledger.record(self.scenario, 1, {'increase_critical_damage'}, {'weaken'}, [], 900_000)
        ledger.record(self.scenario, 1, {'increase_critical_rate'}, {'weaken'}, [], 1_100_000)
        self.assertEqual(2, ledger.stage)
        self.assertEqual(2_000_000, ledger.cumulative_damage)

    def test_single_cast_never_sums_separate_casts_or_enemy_max_hp_damage(self):
        ledger = TrialLedger(stage=2)
        conditions = {'weaken', 'decrease_defence'}
        for _ in range(3):
            ledger.record(self.scenario, 2, set(), conditions, [], 400_000)
        ledger.record(self.scenario, 2, set(), conditions, [], 9_000_000, 'enemy_max_hp')
        self.assertEqual(2, ledger.stage)
        self.assertEqual(400_000, ledger.best_single_cast)
        ledger.record(self.scenario, 2, set(), conditions, [], 750_000)
        self.assertEqual(3, ledger.stage)

    def test_damage_does_not_benefit_from_effect_applied_after_it(self):
        buff = Skill('buff', '测试暴击增益', 'self', effects=(Effect('increase_critical_rate', 'team', 4),))
        weaken = Skill('weaken', '伤害后施加虚弱', 'boss', damage=100, effects=(Effect('weaken', 'boss', 4),))
        window = TrialWindow(single_actor(buff, weaken, BASIC), 0)
        window.ledger.stage = 1
        window.step(window.observe().legal_actions[0])
        window.step(window.observe().legal_actions[1])
        self.assertEqual(0, window.ledger.cumulative_damage)
        window.step(window.observe().legal_actions[2])
        self.assertEqual(10, window.ledger.cumulative_damage)

    def test_slot_capacity_and_resistance_are_not_counted_as_success(self):
        effects = tuple(Effect(str(index), 'boss', 4) for index in range(11)) + (Effect('resisted', 'boss', 4, 0),)
        accuracy = Skill('accuracy', '精准', 'self', effects=(Effect('increase_accuracy', 'team', 3),))
        debuffs = Skill('debuffs', '多减益', 'boss', effects=effects)
        window = TrialWindow(single_actor(accuracy, debuffs, distinct_required=20), 0)
        window.step(window.observe().legal_actions[0])
        window.step(window.observe().legal_actions[1])
        self.assertEqual(10, len(window._effects['boss']))
        self.assertEqual(10, len(window.ledger.accepted_effects))
        self.assertEqual(['resisted'], window.events[-1]['effectsResisted'])
        self.assertEqual('10', window.events[-1]['effectsBlocked'][0]['effect'])
        window.step(window.observe().legal_actions[1])
        self.assertEqual(10, len(window.ledger.accepted_effects))
        self.assertEqual(10, len(window.events[-1]['effectsRefreshed']))

    def test_expiration_keeps_accepted_history(self):
        accuracy = Skill('accuracy', '精准', 'self', effects=(Effect('increase_accuracy', 'team', 3),))
        debuff = Skill('debuff', '短减益', 'boss', effects=(Effect('weaken', 'boss', 1),))
        window = TrialWindow(single_actor(accuracy, debuff), 0)
        window.step(window.observe().legal_actions[0])
        window.step(window.observe().legal_actions[1])
        self.assertEqual({}, window._effects['boss'])
        self.assertEqual({'weaken'}, window.ledger.accepted_effects)

    def test_full_fixture_chain_is_reproducible(self):
        first = run(self.scenario, trial_policy, 71)
        self.assertTrue(first[0]['allCompleted'])
        self.assertEqual(['8000604', '8000605', '8000606'], first[0]['completed'])
        self.assertEqual(first, run(self.scenario, trial_policy, 71))

    def test_finisher_is_reserved_before_unlock_but_not_past_last_opportunity(self):
        reserved = Skill('burst', '爆发', 'boss', damage=900_000, reserve_for_finisher=True)
        observed = TrialWindow(single_actor(BASIC, reserved), 0).observe()
        self.assertEqual('basic', trial_policy(observed).skill)
        self.assertEqual('basic', trial_policy(dataclasses.replace(observed, stage=1)).skill)
        self.assertEqual('burst', trial_policy(dataclasses.replace(observed, stage=1, actor_turns_remaining=1)).skill)

    def test_benchmark_keeps_failures_and_all_seeds(self):
        report = benchmark(self.scenario, 4, 99)
        for policy in report['policies']:
            self.assertEqual([99, 100, 101, 102], [case['offlineSeed'] for case in policy['cases']])
            self.assertEqual(4, sum(policy['stopReasons'].values()))
        self.assertEqual(0, report['policies'][0]['allCompletedCount'])
        self.assertEqual(4, report['policies'][1]['allCompletedCount'])
        self.assertFalse(report['canPredictRealBattle'])
        with self.assertRaises(ValueError):
            benchmark(self.scenario, 0, 99)


if __name__ == '__main__':
    unittest.main()

"""A deliberately limited, explicit model of the Chimera Ram tail chain.

The fixture model exercises planning invariants. It is not a calibrated RAID
battle engine and must never be presented as a prediction for a real roster.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

MODEL_TRIAL_IDS = ('8000604', '8000605', '8000606')


class IllegalAction(ValueError):
    pass


@dataclass(frozen=True)
class Effect:
    identity: str
    recipient: str  # boss or team
    duration: int
    probability: float = 1.0


@dataclass(frozen=True)
class Skill:
    identity: str
    name: str
    target: str  # boss or self; effects have their own recipients
    cooldown: int = 0
    damage: int = 0
    effects: tuple[Effect, ...] = ()
    passive: bool = False
    blocked: bool = False
    reserve_for_finisher: bool = False
    damage_kind: str = 'direct'  # enemy_max_hp is legal but excluded from trial 3


@dataclass(frozen=True)
class Actor:
    identity: str
    name: str
    skills: tuple[Skill, ...]
    alive: bool = True


@dataclass(frozen=True)
class Scenario:
    name: str
    actors: tuple[Actor, ...]
    action_order: tuple[str, ...]
    boss_turns: int = 5
    distinct_required: int = 7
    cumulative_required: int = 2_000_000
    single_cast_required: int = 750_000
    evidence: str = 'synthetic_fixture'
    effect_slots: int = 10  # fixture capacity per recipient, not a general effect engine

    def validate(self) -> None:
        if self.evidence != 'synthetic_fixture':
            raise ValueError('真实队伍模拟尚未实现；不能将此简化引擎标为已验证模型')
        if not 1 <= len(self.actors) <= 5:
            raise ValueError('奇美拉模型必须包含 1–5 个角色')
        identities = [actor.identity for actor in self.actors]
        if any(not name or name == 'boss' for name in identities) or len(set(identities)) != len(identities):
            raise ValueError('角色实例必须唯一')
        if sorted(self.action_order) != sorted(identities):
            raise ValueError('此版本要求每轮给每个角色一个明确行动机会')
        if type(self.boss_turns) is not int or not 1 <= self.boss_turns <= 100:
            raise ValueError('无效窗口长度')
        if any(type(value) is not int or value <= 0 for value in
               (self.distinct_required, self.cumulative_required, self.single_cast_required)):
            raise ValueError('试炼门槛必须为正数')
        if type(self.effect_slots) is not int or not 1 <= self.effect_slots <= 10:
            raise ValueError('模型效果栏上限必须为 1–10')
        for actor in self.actors:
            names = [skill.identity for skill in actor.skills]
            if not names or any(not name for name in names) or len(names) != len(set(names)):
                raise ValueError('每个角色的技能必须非空且唯一')
            for skill in actor.skills:
                if (skill.target not in {'boss', 'self'}
                        or type(skill.cooldown) is not int or skill.cooldown < 0
                        or type(skill.damage) is not int or skill.damage < 0
                        or skill.damage_kind not in {'direct', 'enemy_max_hp'}):
                    raise ValueError('不支持的技能定义')
                if skill.target != 'boss' and skill.damage:
                    raise ValueError('当前模型只支持对 Boss 的直接伤害')
                for effect in skill.effects:
                    if (not effect.identity or effect.recipient not in {'boss', 'team'}
                            or type(effect.duration) is not int or effect.duration <= 0
                            or not 0 <= effect.probability <= 1):
                        raise ValueError('不支持的效果定义')


@dataclass(frozen=True)
class Action:
    actor: str
    skill: str
    target: str
    observation_id: int


@dataclass(frozen=True)
class Observation:
    """Only observable model state; no random seed, future rolls or RNG handle."""
    observation_id: int
    actor: str
    boss_turn: int
    stage: int  # 0: distinct effects; 1: cumulative; 2: single cast; 3: done
    accepted_effects: frozenset[str]
    cumulative_damage: int
    boss_effects: frozenset[str]
    actor_effects: frozenset[str]
    legal_actions: tuple[Action, ...]
    ready_skills: tuple[Skill, ...]
    actor_turns_remaining: int
    cumulative_required: int
    single_cast_required: int


@dataclass
class TrialLedger:
    stage: int = 0
    accepted_effects: set[str] = field(default_factory=set)
    cumulative_damage: int = 0
    best_single_cast: int = 0
    completed: list[str] = field(default_factory=list)

    def record(self, scenario: Scenario, stage_at_start: int,
               actor_effects: set[str], boss_effects_before_damage: set[str],
               successfully_applied: list[str], direct_damage: int,
               damage_kind: str = 'direct') -> None:
        # This model explicitly unlocks the next trial at the NEXT action.
        # Exact same-cast unlock semantics require game evidence before use.
        if stage_at_start == 0:
            if 'increase_accuracy' in actor_effects:
                self.accepted_effects.update(successfully_applied)
            if len(self.accepted_effects) >= scenario.distinct_required:
                self.completed.append(MODEL_TRIAL_IDS[0])
                self.stage = 1
        elif stage_at_start == 1:
            if ('weaken' in boss_effects_before_damage
                    and bool({'increase_critical_rate', 'increase_critical_damage'} & actor_effects)):
                self.cumulative_damage += direct_damage
            if self.cumulative_damage >= scenario.cumulative_required:
                self.completed.append(MODEL_TRIAL_IDS[1])
                self.stage = 2
        elif stage_at_start == 2:
            if damage_kind == 'direct' and {'weaken', 'decrease_defence'} <= boss_effects_before_damage:
                self.best_single_cast = max(self.best_single_cast, direct_damage)
                if direct_damage >= scenario.single_cast_required:
                    self.completed.append(MODEL_TRIAL_IDS[2])
                    self.stage = 3


class TrialWindow:
    def __init__(self, scenario: Scenario, seed: int) -> None:
        scenario.validate()
        self.scenario = scenario
        self._rng = random.Random(seed)
        self._actors = {actor.identity: actor for actor in scenario.actors}
        self._cooldowns = {(actor.identity, skill.identity): 0 for actor in scenario.actors for skill in actor.skills}
        self._effects: dict[str, dict[str, int]] = {actor.identity: {} for actor in scenario.actors}
        self._effects['boss'] = {}
        self._index = 0
        self._observation: Observation | None = None
        self.ledger = TrialLedger()
        self.total_damage = 0
        self.events: list[dict] = []
        self.stopped_reason: str | None = None

    @property
    def done(self) -> bool:
        return self.ledger.stage == 3 or self._index >= self.scenario.boss_turns * len(self.scenario.action_order) or self.stopped_reason is not None

    def observe(self) -> Observation | None:
        if self.done:
            return None
        if self._observation is not None:
            return self._observation
        actor = self._actors[self.scenario.action_order[self._index % len(self.scenario.action_order)]]
        if not actor.alive:
            self.stopped_reason = 'unsupported_dead_actor_transition'
            return None
        for skill in actor.skills:
            key = (actor.identity, skill.identity)
            self._cooldowns[key] = max(0, self._cooldowns[key] - 1)
        ready = tuple(skill for skill in actor.skills if not skill.passive and not skill.blocked and self._cooldowns[(actor.identity, skill.identity)] == 0)
        actions = tuple(Action(actor.identity, skill.identity, 'boss' if skill.target == 'boss' else actor.identity, self._index) for skill in ready)
        self._observation = Observation(self._index, actor.identity,
            self._index // len(self.scenario.action_order), self.ledger.stage,
            frozenset(self.ledger.accepted_effects), self.ledger.cumulative_damage,
            frozenset(self._effects['boss']), frozenset(self._effects[actor.identity]), actions, ready,
            self.scenario.boss_turns - self._index // len(self.scenario.action_order),
            self.scenario.cumulative_required, self.scenario.single_cast_required)
        if not actions:
            self.stopped_reason = 'no_modelled_legal_action'
            return None
        return self._observation

    @staticmethod
    def _tick(effects: dict[str, int]) -> None:
        for identity in list(effects):
            effects[identity] -= 1
            if effects[identity] <= 0:
                del effects[identity]

    def step(self, action: Action) -> None:
        observation = self.observe()
        if observation is None or action not in observation.legal_actions:
            raise IllegalAction('拒绝动作：角色、技能、目标、冷却或观察序号不合法')
        skill = next(skill for skill in observation.ready_skills if skill.identity == action.skill)
        applied, resisted, blocked, refreshed = [], [], [], []
        # Damage before effects is an explicit fixture assumption, not a
        # universal statement about RAID skills. No text parsing occurs here.
        damage = skill.damage
        self.total_damage += damage
        for effect in skill.effects:
            if self._rng.random() >= effect.probability:
                resisted.append(effect.identity)
                continue
            recipients = ['boss'] if effect.recipient == 'boss' else [actor.identity for actor in self.scenario.actors if actor.alive]
            for recipient in recipients:
                current = self._effects[recipient]
                if effect.identity not in current and len(current) >= self.scenario.effect_slots:
                    blocked.append({'effect': effect.identity, 'recipient': recipient, 'reason': 'effect_slots_full'})
                    continue
                if effect.identity in current:
                    refreshed.append({'effect': effect.identity, 'recipient': recipient})
                current[effect.identity] = max(effect.duration, current.get(effect.identity, 0))
                if recipient == 'boss':
                    applied.append(effect.identity)
        self.ledger.record(self.scenario, observation.stage, set(observation.actor_effects),
            set(observation.boss_effects), applied, damage, skill.damage_kind)
        self._cooldowns[(action.actor, action.skill)] = skill.cooldown
        self.events.append({'step': self._index, 'bossTurn': observation.boss_turn,
            'actor': action.actor, 'skill': action.skill, 'target': action.target,
            'stageBefore': observation.stage, 'stageAfter': self.ledger.stage,
            'directDamage': damage, 'effectsApplied': applied, 'effectsResisted': resisted,
            'damageKind': skill.damage_kind, 'effectsBlocked': blocked, 'effectsRefreshed': refreshed,
            'actorEffectsBefore': sorted(observation.actor_effects),
            'bossEffectsBefore': sorted(observation.boss_effects),
            'legalSkillsBefore': [item.skill for item in observation.legal_actions],
            'acceptedEffects': sorted(self.ledger.accepted_effects),
            'cumulativeDamage': self.ledger.cumulative_damage,
            'bestSingleCast': self.ledger.best_single_cast})
        self._tick(self._effects[action.actor])
        self._index += 1
        if self._index % len(self.scenario.action_order) == 0:
            self._tick(self._effects['boss'])
        self._observation = None

    def result(self) -> dict:
        return {'evidence': self.scenario.evidence, 'scope': 'ram_tail_trial_window_only',
            'completed': list(self.ledger.completed), 'allCompleted': self.ledger.stage == 3,
            'actions': self._index, 'totalModelDamage': self.total_damage,
            'acceptedEffects': sorted(self.ledger.accepted_effects),
            'cumulativeQualifiedDamage': self.ledger.cumulative_damage,
            'bestQualifiedSingleCast': self.ledger.best_single_cast,
            'stoppedReason': self.stopped_reason or ('completed' if self.ledger.stage == 3 else 'window_expired' if self.done else 'in_progress'),
            'pendingTrial': MODEL_TRIAL_IDS[self.ledger.stage] if self.ledger.stage < 3 else None}


Policy = Callable[[Observation], Action]


def damage_policy(observation: Observation) -> Action:
    skill = max(observation.ready_skills, key=lambda skill: skill.damage)
    return next(action for action in observation.legal_actions if action.skill == skill.identity)


def trial_policy(observation: Observation) -> Action:
    """Fixture policy: conditional setup, unique effects, reserved finisher."""
    def score(skill: Skill) -> tuple[int, int]:
        identities = {effect.identity for effect in skill.effects}
        if observation.stage == 0:
            setup = 'increase_accuracy' in identities and 'increase_accuracy' not in observation.actor_effects
            useful = len({effect.identity for effect in skill.effects if effect.recipient == 'boss'} - observation.accepted_effects)
            return (3 if setup else 2 if useful else -1 if skill.reserve_for_finisher else 0,
                    useful if useful else skill.damage)
        if observation.stage == 1:
            if 'increase_critical_rate' in identities and 'increase_critical_rate' not in observation.actor_effects:
                return 3, 0
            if 'weaken' in identities and 'weaken' not in observation.boss_effects:
                return 3, 0
            # Do not reserve a future-trial skill beyond its final opportunity.
            # This prioritizes completed prefixes when the full chain is lost.
            reserve = skill.reserve_for_finisher and observation.actor_turns_remaining > 1
            return (-1 if reserve else 1), skill.damage
        if observation.stage == 2:
            if ({'weaken', 'decrease_defence'} - observation.boss_effects) & identities:
                return 3, 0
            return (1 if skill.damage_kind == 'direct' else 0), skill.damage
        return 0, skill.damage
    skill = max(observation.ready_skills, key=score)
    return next(action for action in observation.legal_actions if action.skill == skill.identity)


def run(scenario: Scenario, policy: Policy, seed: int) -> tuple[dict, list[dict]]:
    window = TrialWindow(scenario, seed)
    while (observation := window.observe()) is not None:
        window.step(policy(observation))
    return window.result(), window.events

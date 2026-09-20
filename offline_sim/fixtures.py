"""Artificial test actors. These are NOT real RAID champion definitions."""
from .engine import Actor, Effect, Scenario, Skill


def ram_tail_fixture(probability: float = .9) -> Scenario:
    def basic(identity: str, damage: int) -> Skill:
        return Skill(identity, '基础攻击', 'boss', damage=damage)
    def debuffs(*identities: str) -> tuple[Effect, ...]:
        return tuple(Effect(identity, 'boss', 3, probability) for identity in identities)
    return Scenario('人工模型：公羊尾巴试炼链', (
        Actor('support', '测试角色：增益提供者', (
            basic('s1', 50000),
            Skill('s2', '测试增益技能', 'self', 3, effects=(Effect('increase_accuracy', 'team', 3), Effect('increase_critical_rate', 'team', 3))))),
        Actor('debuffer_a', '测试角色：减益甲', (
            basic('a1', 80000), Skill('a2', '测试减益组合甲', 'boss', 3, 100000, debuffs('weaken', 'decrease_defence', 'decrease_attack')))),
        Actor('debuffer_b', '测试角色：减益乙', (
            basic('b1', 80000), Skill('b2', '测试减益组合乙', 'boss', 3, 100000, debuffs('block_buffs', 'decrease_speed', 'poison', 'leech')))),
        Actor('finisher', '测试角色：单次门槛输出', (
            basic('f1', 400000), Skill('f2', '测试爆发技能', 'boss', 3, 900000, reserve_for_finisher=True))),
        Actor('damage', '测试角色：常规输出', (
            basic('d1', 350000), Skill('d2', '测试常规技能', 'boss', 4, 600000))),
    ), ('support', 'debuffer_a', 'debuffer_b', 'finisher', 'damage'))

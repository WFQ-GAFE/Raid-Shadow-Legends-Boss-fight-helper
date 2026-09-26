"""Retain every synthetic trial; never select a favorable seed to report."""
from collections import Counter
from statistics import median

from .engine import MODEL_TRIAL_IDS, Scenario, damage_policy, run, trial_policy


def benchmark(scenario: Scenario, runs: int, seed: int) -> dict:
    if type(runs) is not int or not 1 <= runs <= 2000:
        raise ValueError('推演次数必须在 1–2000 之间')
    if type(seed) is not int:
        raise ValueError('离线随机种子必须是整数')
    scenario.validate()
    policies = []
    for name, policy in (('damage_first', damage_policy), ('trial_chain', trial_policy)):
        cases = []
        for index in range(runs):
            result, events = run(scenario, policy, seed + index)
            cases.append({'case': index + 1, 'offlineSeed': seed + index, 'result': result, 'events': events})
        complete_actions = [case['result']['actions'] for case in cases if case['result']['allCompleted']]
        policies.append({'policy': name, 'runs': runs,
                         'allCompletedCount': len(complete_actions),
                         'completionRate': len(complete_actions) / runs,
                         'completedCountByTrial': {identity: sum(identity in case['result']['completed'] for case in cases)
                                                   for identity in MODEL_TRIAL_IDS},
                         'medianActionsAmongCompleted': median(complete_actions) if complete_actions else None,
                         'stopReasons': dict(Counter(case['result']['stoppedReason'] for case in cases)),
                         'cases': cases})
    return {'format': 'raid-offline-fixture-report', 'schemaVersion': 1, 'evidence': 'synthetic_fixture',
            'canPredictRealTrials': False, 'canPredictRealBattle': False,
            'scenario': scenario.name, 'runsPerPolicy': runs, 'seedStart': seed,
            'scope': 'ram_tail_trial_window_only', 'trialIds': list(MODEL_TRIAL_IDS),
            'policies': policies}


def benchmark_markdown(report: dict) -> str:
    lines = ['# 奇美拉试炼链：人工模型验证', '',
             '**下面所有结果均来自人工测试角色和简化规则，不是你的真实队伍成功率，也不是完整战斗预测。**', '',
             '本轮模型：公羊尾巴 8000604 → 8000605 → 8000606。每项策略使用连续编号的离线随机种子，包含全部成功与失败样本。', '',
             '| 测试策略 | 样本数 | 完成第一项 | 完成第二项 | 完成全链 |',
             '| --- | --- | --- | --- | --- |']
    names = {'damage_first': '模型内优先直接伤害', 'trial_chain': '模型内按试炼条件调度'}
    for policy in report['policies']:
        counts = policy['completedCountByTrial']
        lines.append(f"| {names[policy['policy']]} | {policy['runs']} | {counts[MODEL_TRIAL_IDS[0]]} | {counts[MODEL_TRIAL_IDS[1]]} | {policy['allCompletedCount']} |")
    lines += ['', '这两个策略都只从当前模型允许的动作中选择。比较用于检查计数、预留和失败路径；不是对现有游戏控制器的性能测评。', '',
              '人工模型没有 Boss 攻击、生命值、死亡过程、真实速度条、伤害公式、装备或被动，因此不输出“整场伤害预测”。', '',
              '## 固定首个样本的动作记录', '',
              '始终展示第一号样本，不筛选更好看的结果。完整记录保存在 results.json。', '']
    case = report['policies'][1]['cases'][0]
    lines += [f"离线种子：{case['offlineSeed']}；停止原因：{case['result']['stoppedReason']}；完成项：{case['result']['completed']}。", '',
              '| 动作 | 模型 Boss 轮次 | 角色 | 技能 | 试炼阶段 | 已接受减益种类 | 合格累计伤害 |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    for event in case['events']:
        lines.append(f"| {event['step'] + 1} | {event['bossTurn'] + 1} | {event['actor']} | {event['skill']} | {event['stageBefore'] + 1} → {event['stageAfter'] + 1 if event['stageAfter'] < 3 else '完成'} | {len(event['acceptedEffects'])} | {event['cumulativeDamage']} |")
    lines += ['', '## 模型明确采用的假设', '',
              '- 每个模型 Boss 轮次中，五个角色按固定顺序各有一次行动；这不是游戏速度计算。',
              '- 当前动作先造成设定的直接伤害，再尝试施加效果；下一试炼从下一动作起解锁。',
              '- 效果按测试身份去重；每个接收者最多 10 格，同身份只刷新且不缩短持续时间。',
              '- 角色效果在其动作结束时递减，Boss 效果在整轮结束时递减；效果到期不清除已经接受的试炼历史。',
              '- 冷却只随所属角色的行动机会递减；重复读取状态不会加速冷却。',
              '- 概率是测试参数，策略只能读取当前可观察状态，不能读取随机数发生器或未来结果。',
              '- 遇到未建模的死亡状态或无合法技能会停止，不会伪造行动机会。',
              '- 伤害门槛 200 万 / 75 万为人工测试参数，未作为真实难度门槛使用。', '',
              '这些假设需要逐条用离线参考回放校准，才可以扩展到真实英雄与完整战斗。', '']
    return '\n'.join(lines)

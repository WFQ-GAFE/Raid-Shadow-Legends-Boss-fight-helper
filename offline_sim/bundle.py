"""Read-only, allowlisted projection of existing JSON exports for offline review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .engine import MODEL_TRIAL_IDS

MAX_INPUT_BYTES = 32 * 1024 * 1024


def read_json(path: Path) -> tuple[dict, dict]:
    # Plain files only. No URLs, plugins, object deserialization or game APIs.
    if not path.is_file():
        raise ValueError(f'找不到本地 JSON 文件：{path.name}')
    with path.open('rb') as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f'文件超过 32 MiB：{path.name}')
    def invalid_constant(value: str) -> None:
        raise ValueError(f'JSON 含非有限数值：{value}')
    value = json.loads(raw.decode('utf-8-sig'), parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError(f'JSON 顶层必须是对象：{path.name}')
    return value, {'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _ids(values: object, label: str) -> list[int]:
    if not isinstance(values, list) or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError(f'{label} 必须是正整数列表')
    return list(values)


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f'{label} 必须是对象')
    return value


def _text(value: object, limit: int = 24000) -> str:
    return value[:limit] if isinstance(value, str) else ''


def build_bundle(export: dict, hero_catalog: dict, trial_catalog: dict) -> dict:
    if (export.get('format') != 'raid-boss-strategy' or type(export.get('version')) is not int
            or export['version'] != 1 or export.get('bossMode') != 'chimera'):
        raise ValueError('仅接受 version=1 的奇美拉 raid-boss-strategy 导出')
    strategy = _object(export.get('strategy'), 'strategy')
    if strategy.get('bossMode', 'chimera') != 'chimera':
        raise ValueError('策略内部 Boss 模式不一致')
    team = _object(strategy.get('team'), 'strategy.team')
    requested_heroes = _ids(team.get('heroTypeIds'), '队伍英雄')
    if not 1 <= len(requested_heroes) <= 5:
        raise ValueError('奇美拉队伍必须包含 1–5 个位置')
    objectives = _object(strategy.get('objectives', {}), 'objectives')
    goals = list(dict.fromkeys(_ids(objectives.get('mandatoryTrialIds', []), '必选试炼')))
    if len(goals) > 27:
        raise ValueError('必选试炼超过 27 项')
    minimum_damage = objectives.get('minimumDamage')
    if minimum_damage is not None and (type(minimum_damage) is not int or minimum_damage < 0):
        raise ValueError('最低伤害必须是非负整数')

    aliases: dict[int, dict] = {}
    for hero in _object(hero_catalog.get('heroes'), 'heroes').values():
        hero = _object(hero, '英雄目录条目')
        canonical = _ids([hero.get('typeId')], '英雄类型')[0]
        for alias in set(_ids(hero.get('runtimeTypeIds', []), '英雄类型别名') + [canonical]):
            if alias in aliases and aliases[alias]['typeId'] != canonical:
                raise ValueError(f'英雄类型别名冲突：{alias}')
            aliases[alias] = hero
    roster, unknown_heroes = [], []
    for slot, requested_id in enumerate(requested_heroes, 1):
        hero = aliases.get(requested_id)
        if hero is None:
            unknown_heroes.append(requested_id)
            roster.append({'slot': slot, 'exportTypeId': requested_id, 'resolved': False})
            continue
        skills = hero.get('skills', [])
        if not isinstance(skills, list):
            raise ValueError('英雄技能列表格式错误')
        projected_skills = []
        for item in skills:
            item = _object(item, '技能')
            # Default cooldown is reference text metadata, never instance cooldown.
            selected = {key: item[key] for key in ('typeId', 'slot', 'formIndex', 'defaultCooldown', 'activeSkill', 'hiddenOnHud')
                        if type(item.get(key)) in (int, bool)}
            selected.update(name=_text(item.get('name'), 240), description=_text(item.get('description')),
                            executableModel=False)
            projected_skills.append(selected)
        roster.append({'slot': slot, 'exportTypeId': requested_id, 'canonicalTypeId': hero['typeId'],
                       'name': _text(hero.get('name'), 240), 'resolved': True, 'skills': projected_skills})

    difficulties = trial_catalog.get('difficulties')
    if not isinstance(difficulties, list):
        raise ValueError('试炼目录缺少难度列表')
    trial_index, groups = {}, {}
    for difficulty in difficulties:
        difficulty = _object(difficulty, '战斗难度')
        battle_difficulty = _ids([difficulty.get('difficultyId')], '战斗难度')[0]
        entries = difficulty.get('trials', [])
        if not isinstance(entries, list):
            raise ValueError('试炼列表格式错误')
        for item in entries:
            item = _object(item, '试炼')
            trial_id = _ids([item.get('id')], '试炼编号')[0]
            rank = _ids([item.get('difficultyId')], '链内级别')[0]
            if trial_id in trial_index:
                raise ValueError(f'试炼编号重复：{trial_id}')
            form, part = _text(item.get('form'), 40), _text(item.get('part'), 40)
            group = (battle_difficulty, form, part)
            projected = {'id': trial_id, 'battleDifficultyId': battle_difficulty,
                         'battleDifficulty': _text(difficulty.get('difficulty'), 80),
                         'form': form, 'part': part, 'chainRank': rank,
                         'description': _text(item.get('description')),
                         'executableRealModel': False}
            trial_index[trial_id] = projected
            groups.setdefault(group, []).append(projected)
    for entries in groups.values():
        ranks = [item['chainRank'] for item in entries]
        if sorted(ranks) != [1, 2, 3]:
            raise ValueError('每条目录试炼链必须具有唯一且完整的 1/2/3 级')
        entries.sort(key=lambda item: item['chainRank'])
        for index, item in enumerate(entries):
            item['prerequisiteIds'] = [entry['id'] for entry in entries[:index]]

    selected, unknown_goals = {}, []
    for goal in goals:
        item = trial_index.get(goal)
        if item is None:
            unknown_goals.append(goal)
            continue
        for identity in item['prerequisiteIds'] + [goal]:
            selected[identity] = dict(trial_index[identity], explicitlySelected=identity in goals)
    required_trials = sorted(selected.values(), key=lambda item: item['id'])
    selected_difficulties = sorted({item['battleDifficultyId'] for item in required_trials})
    gaps = [
        {'code': 'instance_stats', 'detail': '缺少每个队伍位置的实际属性、技能升级、装备、天赋、祝福和遗物。'},
        {'code': 'skill_event_models', 'detail': '技能说明没有转换成经过验证的命中、目标、效果顺序、伤害公式和被动事件。'},
        {'code': 'boss_and_turn_order', 'detail': '尚未建模 Boss 技能、速度与行动条、形态切换、对决和存活状态。'},
        {'code': 'trial_event_semantics', 'detail': '链顺序按本地目录推导；计数身份、单次施法边界、条件检查时点和重置规则缺少回放校验。'},
        {'code': 'random_distributions', 'detail': '缺少经过校准的暴击、弱击、抵抗和被动触发概率模型。'},
        {'code': 'reference_replays', 'detail': '缺少能够逐事件比较预测与实际结果的完整离线回放样本。'},
    ]
    if unknown_heroes:
        gaps.append({'code': 'unresolved_heroes', 'detail': f'英雄类型无法解析：{unknown_heroes}'})
    if unknown_goals:
        gaps.append({'code': 'unresolved_trials', 'detail': f'试炼编号无法解析：{unknown_goals}'})
    if len(selected_difficulties) > 1:
        gaps.append({'code': 'mixed_battle_difficulties', 'detail': '目标跨多个战斗难度，不能作为同一场战斗。'})
    if not goals:
        gaps.append({'code': 'no_selected_trials', 'detail': '策略没有选中必做试炼。'})
    return {'format': 'raid-offline-reference-bundle', 'schemaVersion': 1, 'bossMode': 'chimera',
            'evidence': 'static_export_only', 'strategyName': _text(strategy.get('name'), 240),
            'goals': {'mandatoryTrialIds': goals, 'minimumDamage': minimum_damage},
            'roster': roster, 'requiredTrials': required_trials,
            'chainOrderEvidence': 'inferred_from_catalog_form_part_and_rank',
            'readiness': {'canPredictRealTrials': False, 'canPredictRealBattle': False,
                          'status': 'reference_only_missing_verified_mechanics', 'gaps': gaps},
            'prototypeCoverage': {'evidence': 'synthetic_fixture', 'trialIds': list(MODEL_TRIAL_IDS),
                                  'selectedGoalsOutsideFixture': [goal for goal in goals if str(goal) not in MODEL_TRIAL_IDS],
                                  'note': '这些编号只标识人工模型所参考的链，不能表示真实试炼已验证。'}}


def _line(value: object) -> str:
    return str(value).replace('\r', ' ').replace('\n', ' ').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')


def readiness_markdown(bundle: dict) -> str:
    roster = bundle['roster']
    lines = ['# 奇美拉离线数据检查', '',
             '**目前只能用于数据核对，不能预测真实队伍的试炼成功率或完整战斗伤害。**', '',
             f"策略：{_line(bundle['strategyName'])}；已解析队伍 {sum(item['resolved'] for item in roster)}/{len(roster)}。", '',
             '## 队伍', '', '| 位置 | 英雄 | 技能目录条数 |', '| --- | --- | --- |']
    for item in roster:
        lines.append(f"| {item['slot']} | {_line(item.get('name', '未解析'))} | {len(item.get('skills', []))} |")
    lines += ['', '技能目录中的冷却为默认值，未视为实际队伍的升级后冷却。', '',
              '## 已选目标及前置', '',
              '顺序根据本地目录的形态、部位和链内级别推导；这是待校验的依赖清单。', '',
              '| 试炼 | 形态 / 部位 | 战斗难度 | 链内级别 | 来源 | 前置 |',
              '| --- | --- | --- | --- | --- | --- |']
    for item in bundle['requiredTrials']:
        lines.append(f"| {item['id']} | {_line(item['form'])} / {_line(item['part'])} | {_line(item['battleDifficulty'])} | {item['chainRank']} | {'已选' if item['explicitlySelected'] else '前置'} | {', '.join(map(str, item['prerequisiteIds'])) or '无'} |")
    lines += ['', '## 尚缺的校准数据与机制', '']
    lines += [f"- {item['detail']}" for item in bundle['readiness']['gaps']]
    lines += ['', '## 本轮原型覆盖', '',
              '人工模型只覆盖 8000604 → 8000605 → 8000606 的计数与调度验证，角色、伤害、速度顺序和概率均为测试设定。', '',
              f"当前已选目标中，未在该人工模型内的编号：{bundle['prototypeCoverage']['selectedGoalsOutsideFixture']}。", '',
              '工具仅把本地 JSON 中允许的目录字段整理为独立资料包。没有复制执行规则、重试指令、账号字段或进程信息；也没有向游戏执行动作。', '']
    return '\n'.join(lines)

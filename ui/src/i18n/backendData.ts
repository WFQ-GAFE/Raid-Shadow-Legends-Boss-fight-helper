import type { MessageKey, MessageParams } from './index'
import type { Locale } from './locales'

// Text the backend keeps in Chinese because it stores or compares it: names it
// gives things, and the labels of its decisions. Shown by its message when a
// whole text, argument or label part is one of them; everything else passes.
type Translator = (locale: Locale, key: MessageKey, params?: MessageParams) => string

const DATA_VALUES: [RegExp, MessageKey, string?][] = [
  [/^默认策略$/, 'app.defaultStrategy'], [/^未命名策略$/, 'chimeraSim.unnamedStrategy'],
  [/^规则 (\d+)$/, 'app.rule', 'value'], [/^技能 (\d+)$/, 'app.skill', 'slot'], [/^蛇头 (\d+)$/, 'app.hydraHeadNumber', 'id'],
  [/^增益$/, 'effect.group.buff'], [/^减益$/, 'effect.group.debuff'], [/^特殊$/, 'effect.group.special'],
  [/^奇美拉$/, 'mode.chimera'], [/^六头蛇$/, 'mode.hydra'], [/^英雄 (\d+)$/, 'app.champion', 'heroTypeId'],
  [/^未命名规则$/, 'rule.unnamed'], [/^试炼专用规则$/, 'rule.trialRule'], [/^当前$/, 'rule.currentForm'], [/^导入策略$/, 'rule.importedStrategy'],
]

function wholeValue(text: string, locale: Locale, t: Translator): string | null {
  const trimmed = text.trim()
  for (const [pattern, key, argument] of DATA_VALUES) {
    const match = pattern.exec(trimmed)
    if (match) return t(locale, key, argument ? { [argument]: match[1] } : undefined)
  }
  return null
}

// The labels the controller gives its decisions ("<rule name> · <detail>") are
// data too: the simulations find the rule by that prefix. Their generated parts
// are shown in the window's language; rule, champion and trial names stay.
const RULE_FORMS: Record<string, MessageKey> = {
  Ultimate: 'chimera.form.0', Ram: 'chimera.form.1', Lion: 'chimera.form.2', Snake: 'chimera.form.3', Viper: 'chimera.form.3',
  当前: 'rule.currentForm', 变形形态: 'profile.alternateForm', 原始形态: 'profile.baseForm',
}
const RULE_FIXED: Record<string, MessageKey> = {
  默认技能顺序: 'rule.defaultOrder', 首回合技能: 'rule.firstTurn', 当前无可执行试炼专用动作: 'rule.noTrialAction',
  '保留技能已无替代，按默认顺序使用': 'rule.reservedFallback', 按当前试炼自动决策: 'rule.byTrial', 保留下一试炼关键技能: 'rule.keepNextTrialSkills',
}
const RULE_STEPS = ['等待队友补齐条件', '补充不同Boss减益', '探索新Boss减益', '补充不同行动者增益', '探索新行动者增益',
  '条件完整，推进试炼伤害', '施加试炼持续伤害', '执行已识别的专用机制', '补充防护效果']
const RULE_NOTES = ['维持必要效果', '学习未知技能效果', '先建立保护并保留试炼关键技能', '保留试炼关键技能', '优先复活阵亡队友',
  '队伍低生命，优先使用已知防护技能', '按常规技能优先级继续战斗']
const RULE_REASONS = ['沿用当前形态技能优先级', '综合辅助价值与历史伤害']

// Parts of the names the rule editor gives new rules ("<champion> · <skill> → <target>",
// App.tsx autoName) and their arguments. A strategy made in Chinese shows them in the
// window's language; champion, skill and head names stay as the game wrote them.
type Generated = [MessageKey, string[]][]
const GENERATED_ACTIONS: Generated = [['app.switchForm', []], ['app.castSkill', []], ['app.returnToBaseForm', []],
  ['app.switchToAlternateForm', []], ['app.switchMythicalForm', []], ['app.decideByTheCurrentTrial', []],
  ['app.keepBuffsDebuffsUpAutomatically', []]]
const GENERATED_TARGETS: Generated = [['app.self', []], ['app.lowestHpAlly', []], ['app.specifiedAlly', []],
  ['app.allyInSlot', ['position']], ['app.lowestHpHeadOldSlot', []], ['app.byTypePriority', ['join', 'value']],
  ['app.headTypePriorityLowestHp', []], ['app.devouringHead', []], ['app.exposedNeck', []], ['app.lowestHpHead', []],
  ['app.lowestDefHead', []], ['app.chimeraBoss', []], ['app.ownTargetPerSkillSet', ['customTargets']],
  ['app.eachSkillPicksALegal', []]]
// Earlier Chinese wording of a part, still in the names of rules saved before it changed.
const EARLIER: Partial<Record<MessageKey, string[]>> = { 'app.exposedNeck': ['暴露蛇颈'] }
const patterns = new Map<Generated, { key: MessageKey; params: string[]; pattern: RegExp }[]>()

// Each Chinese message as a pattern: its arguments become groups (formatted with markers).
function compiled(list: Generated, t: Translator) {
  let result = patterns.get(list)
  if (!result) {
    result = list.flatMap(([key, params]) => {
      const text = t('zh-CN', key, Object.fromEntries(params.map((param, index) => [param, `${index}`])))
      return [text, ...(EARLIER[key] ?? [])].map((wording) => {
        const source = wording.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\d/g, '([\\s\\S]*?)')
        return { key, params, pattern: new RegExp(`^${source}$`) }
      })
    })
    patterns.set(list, result)
  }
  return result
}

function generatedPart(text: string, list: Generated, locale: Locale, t: Translator): string | null {
  for (const { key, params, pattern } of compiled(list, t)) {
    const match = pattern.exec(text)
    if (match) return t(locale, key, Object.fromEntries(params.map((param, index) => [param, match[index + 1]])))
  }
  return null
}

function ruleSegment(segment: string, locale: Locale, t: Translator): string | null {
  const form = (name: string) => RULE_FORMS[name] ? t(locale, RULE_FORMS[name]) : name
  if (RULE_FIXED[segment]) return t(locale, RULE_FIXED[segment])
  let match = /^(.+)形态首回合技能$/.exec(segment)
  if (match) return t(locale, 'rule.formFirstTurn', { form: form(match[1]) })
  if ((match = /^自动切换至(.+)$/.exec(segment))) return t(locale, 'rule.autoSwitch', { form: form(match[1]) })
  if ((match = /^为(.+)准备：提前建立条件$/.exec(segment))) return t(locale, 'rule.prepareEarly', { trial: match[1] })
  if ((match = /^为(.+)准备$/.exec(segment))) return t(locale, 'rule.prepare', { trial: match[1] })
  if ((match = /^保留试炼 (\d+) 的关键技能$/.exec(segment))) return t(locale, 'rule.keepTrialSkills', { trial: match[1] })
  if ((match = /^(.+)（(.+)）：(.+)$/.exec(segment)) && RULE_STEPS.includes(match[3])) {
    const progress = match[2] === '等待游戏计数' ? t(locale, 'rule.waitingCount') : match[2]
    return t(locale, 'rule.recipeStep', { recipe: match[1], progress, step: t(locale, `rule.step.${RULE_STEPS.indexOf(match[3])}` as MessageKey) })
  }
  return null
}

function ruleLabel(text: string, locale: Locale, t: Translator): string | null {
  let match = /^(.+)：(.+)$/.exec(text)
  if (match && RULE_NOTES.includes(match[2]))
    return t(locale, 'rule.withNote', { name: ruleLabel(match[1], locale, t) ?? match[1], note: t(locale, `rule.note.${RULE_NOTES.indexOf(match[2])}` as MessageKey) })
  if ((match = /^(.+)（(.+)）$/.exec(text)) && RULE_REASONS.includes(match[2]))
    return t(locale, 'rule.withReason', { name: match[1], reason: t(locale, `rule.reason.${RULE_REASONS.indexOf(match[2])}` as MessageKey) })
  // "<champion> · <skill> → <target>[ · <detail>]": the target can hold " · " and " → " itself.
  const arrow = text.indexOf(' → ')
  if (arrow > 0) {
    const head = text.slice(0, arrow)
    const rest = text.slice(arrow + 3).split(' · ')
    for (let count = rest.length; count > 0; count -= 1) {
      const target = generatedPart(rest.slice(0, count).join(' · '), GENERATED_TARGETS, locale, t)
      if (target === null) continue
      const tail = rest.slice(count).join(' · ')
      return `${segmentLabel(head, locale, t) ?? head} → ${target}${tail ? ` · ${segmentLabel(tail, locale, t) ?? tail}` : ''}`
    }
  }
  return segmentLabel(text, locale, t)
}

function segmentLabel(text: string, locale: Locale, t: Translator): string | null {
  const segments = text.split(' · ')
  const translated = segments.map((segment) => wholeValue(segment, locale, t) ?? ruleSegment(segment, locale, t)
    ?? generatedPart(segment, GENERATED_ACTIONS, locale, t))
  return translated.some((value) => value !== null) ? translated.map((value, index) => value ?? segments[index]).join(' · ') : null
}

export function backendDataValue(text: string, locale: Locale, t: Translator): string | null {
  return wholeValue(text, locale, t) ?? (/[一-鿿]/.test(text) ? ruleLabel(text.trim(), locale, t) : null)
}

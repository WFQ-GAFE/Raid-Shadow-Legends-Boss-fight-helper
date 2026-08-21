import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BookOpenCheck,
  Bot,
  Check,
  ChevronDown,
  CirclePause,
  CirclePlay,
  Copy,
  Crosshair,
  Database,
  Edit3,
  Gauge,
  Gem,
  Languages,
  Layers3,
  Maximize2,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Swords,
  Trash2,
  Users,
  Waves,
  X,
  Zap,
} from 'lucide-react'
import { getInitialLanguage, installDocumentLocalization, saveLanguage, type UiLanguage } from './i18n'

type JsonObject = Record<string, unknown>
type BossMode = 'chimera' | 'hydra'

type BossModeSpec = {
  id: BossMode
  label: string
  battleKind: string
  teamSize: number
  hasTrials: boolean
  status: string
}

type RewardEntry = {
  typeId?: number
  type?: string
  probability?: number
  minCount?: number
  maxCount?: number
  resourceTypeId?: number
  resourceType?: string
  blackMarketItemId?: number
}

type TrialReward = {
  rollCount?: number
  entries?: RewardEntry[]
}

type Skill = {
  slot: number
  typeId?: number
  name?: string
  description?: string
  icon?: string
  formIndex?: number
  effectSummary?: string
  defaultCooldown?: number
  isTransform?: boolean
}

type Hero = {
  typeId: number
  name: string
  avatar?: string
  isMetamorph?: boolean
  runtimeTypeIds?: number[]
  skills: Skill[]
}

type HydraHead = {
  id?: number
  typeId: number
  canonicalTypeId?: number
  resourceKind?: string
  name: string
  avatar?: string
  healthPct?: number
  dead?: boolean
  isHydraHead?: boolean
  isHydraNeck?: boolean
  isDevouring?: boolean
  headState?: string
}

type RaidProcess = {
  pid: number
  label: string
  accountName?: string
  userId?: number
  error?: string
}

type Trial = {
  id: number
  form?: string
  difficulty?: string
  difficultyId?: number
  description?: string
  effects?: { id?: number; name?: string }[]
  reward?: TrialReward
  completed?: boolean
  possible?: boolean
  eligibleNow?: boolean
}

type Difficulty = {
  difficultyId: number
  difficulty: string
  trials: Trial[]
}

type EffectOption = {
  token: string
  icon: string
  label: string
  group: string
}

const ENGLISH_EFFECT_NAMES: Record<string, string> = {
  BlockHeal: 'Heal Reduction 100%',
  BlockHeal2: 'Heal Reduction 50%',
  ContinuousDamage: 'Poison 5%',
  ContinuousDamage2: 'Poison 2.5%',
  StatusReduceAttack: 'Decrease Attack 25%',
  StatusReduceAttack2: 'Decrease Attack 50%',
  StatusReduceDefence: 'Decrease Defence 30%',
  StatusReduceDefence2: 'Decrease Defence 60%',
  StatusReduceSpeed: 'Decrease Speed 15%',
  StatusReduceSpeed2: 'Decrease Speed 30%',
  StatusIncreaseAttack: 'Increase Attack 25%',
  StatusIncreaseAttack2: 'Increase Attack 50%',
  StatusIncreaseDefence: 'Increase Defence 30%',
  StatusIncreaseDefence2: 'Increase Defence 60%',
  StatusIncreaseSpeed: 'Increase Speed 15%',
  StatusIncreaseSpeed2: 'Increase Speed 30%',
  Invisible: 'Veil',
  Invisible2: 'Perfect Veil',
  FireMark: 'HP Burn',
  ElectricMark: 'Smite',
}

function splitIdentifier(value: string) {
  return value
    .replace(/^Status/, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Za-z])(\d+)/g, '$1 $2')
    .trim()
}

const ENGLISH_HYDRA_HEAD_NAMES: Record<string, string> = {
  Stone: 'Stone Head',
  Support: 'Head of Decay',
  Ghost: 'Head of Torment',
  Poison: 'Head of Blight',
  Tank: 'Head of Suffering',
  Thief: 'Head of Mischief',
  Berserk: 'Head of Wrath',
  Electric: 'Electric Head',
}

function hydraHeadDisplayName(head?: HydraHead): string {
  if (!head) return ''
  if (document.documentElement.lang !== 'en') return head.name
  if (head.resourceKind) {
    return ENGLISH_HYDRA_HEAD_NAMES[head.resourceKind] ?? `${splitIdentifier(head.resourceKind)} Head`
  }
  return head.name.replace(/^蛇头\s*/u, 'Hydra Head ')
}

function effectDisplay(effect: EffectOption) {
  const english = document.documentElement.lang === 'en'
  return {
    label: english ? ENGLISH_EFFECT_NAMES[effect.icon] ?? splitIdentifier(effect.icon) : effect.label,
    group: english
      ? ({ 增益: 'Buff', 减益: 'Debuff', 特殊: 'Special', 奇美拉: 'Chimera' }[effect.group] ?? effect.group)
      : effect.group,
  }
}

type Rule = {
  name?: string
  when?: JsonObject
  action?: JsonObject
}

type Strategy = {
  name?: string
  mode?: string
  scope?: JsonObject
  objectives?: {
    mandatoryTrialIds?: number[]
    minimumDamage?: number
    maxRegroupRetries?: number
    [key: string]: unknown
  }
  safety?: JsonObject
  team?: { heroIds?: number[]; heroTypeIds?: number[]; heroInstanceIds?: number[] }
  rules?: Rule[]
}

type StrategyProfile = {
  id: string
  name: string
  active?: boolean
  teamHeroIds: number[]
  ruleCount: number
}

type StrategyBundle = {
  config: Strategy
  activeStrategyId: string
  strategyProfiles: StrategyProfile[]
  message?: string
}

type LiveState = {
  bossMode?: BossMode
  screen?: string
  statusLabel?: string
  form?: string
  activeHeroName?: string
  activeHeroTypeId?: number
  bossHpPct?: number
  waitingForCommand?: boolean
  chimeraTurn?: number
  hydraTurn?: number
  headCount?: number
  heads?: HydraHead[]
  playerTurn?: number
  damage?: number
  points?: number
  teamHeroIds?: number[]
  teamHeroInstanceIds?: number[]
  completedTrialIds?: number[]
  trials?: Trial[]
  agentReady?: boolean
  modeReady?: boolean
  error?: string
}

type ControllerState = {
  running: boolean
  status: string
  pid?: number
  bossMode?: BossMode
  logMode?: BossMode
  logs: string[]
  error?: string
}

type Bootstrap = {
  bossMode: BossMode
  modes: BossModeSpec[]
  processes: RaidProcess[]
  config: Strategy
  activeStrategyId: string
  strategyProfiles: StrategyProfile[]
  heroes: Hero[]
  hydraHeads: HydraHead[]
  difficulties: Difficulty[]
  effects: EffectOption[]
  selectedPid?: number
  state?: LiveState
  controller: ControllerState
  cacheStatus?: string
}

const token = new URLSearchParams(window.location.search).get('token') ?? ''

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.body) headers.set('Content-Type', 'application/json')
  if (token) headers.set('X-Chimera-Token', token)
  const response = await fetch(path, { ...init, headers, cache: 'no-store' })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`)
  }
  return payload as T
}

const FORM_LABEL: Record<string, string> = {
  Ultimate: '终极形态',
  Ram: '公羊形态',
  Lion: '狮子形态',
  Snake: '毒蛇形态',
}

const TRIAL_LEVEL: Record<string, string> = {
  Easy: '简单',
  Normal: '普通',
  Hard: '苦难',
}

const BOSS_DIFFICULTY: Record<string, string> = {
  Easy: '简单',
  Normal: '普通',
  Hard: '困难',
  Brutal: '残暴',
  Nightmare: '噩梦',
  UltraNightmare: '终极噩梦',
}

const ALL_FORMS = ['Ultimate', 'Ram', 'Lion', 'Snake']
const HYDRA_BATTLE_TURN_CONDITION_KEYS = [
  'round',
  'turn',
  'playerTurnCount',
  'chimeraTurnCount',
  'activeHeroTurnCount',
  'turnAtLeast',
  'turnAtMost',
  'chimeraTurnAtLeast',
  'chimeraTurnAtMost',
]
const LEGACY_EFFECT_KEYS = [
  'bossHasEffects',
  'bossMissingEffects',
  'bossHasEffect',
  'bossMissingEffect',
  'activeHeroHasEffect',
  'activeHeroMissingEffect',
  'anyAllyHasEffect',
  'anyAllyMissingEffect',
  'allyHasEffect',
  'allyMissingEffect',
] as const

type EffectConditionValue = {
  id: string
  target: 'boss' | 'bossPriority' | 'bossAny' | 'bossAll' | 'ally'
  heroTypeId: string
  presence: 'has' | 'missing'
  token: string
  turnsAtLeast: string
  turnsAtMost: string
}

type SkillCooldownConditionValue = {
  id: string
  heroTypeId: string
  skillTypeId: string
  turnsAtLeast: string
  turnsAtMost: string
}

let effectConditionSequence = 0
let skillCooldownConditionSequence = 0

function createEffectCondition(value: Partial<EffectConditionValue> = {}): EffectConditionValue {
  effectConditionSequence += 1
  return {
    id: `effect-condition-${effectConditionSequence}`,
    target: 'boss',
    heroTypeId: '',
    presence: 'has',
    token: '',
    turnsAtLeast: '',
    turnsAtMost: '',
    ...value,
  }
}

function createSkillCooldownCondition(value: Partial<SkillCooldownConditionValue> = {}): SkillCooldownConditionValue {
  skillCooldownConditionSequence += 1
  return {
    id: `skill-cooldown-condition-${skillCooldownConditionSequence}`,
    heroTypeId: '',
    skillTypeId: '',
    turnsAtLeast: '',
    turnsAtMost: '',
    ...value,
  }
}

function cleanText(value?: string) {
  return (value ?? '').replace(/<[^>]+>/g, '')
}

function formatNumber(value?: number) {
  return new Intl.NumberFormat('zh-CN').format(value ?? 0)
}

function damageInMillions(value?: number) {
  const millions = Number(value ?? 0) / 1_000_000
  return Number(millions.toFixed(3))
}

const REWARD_RESOURCE_LABELS: Record<string, string> = {
  RelicCraftMaterial_Chimera_Rare: '稀有遗物锻造材料',
  RelicCraftMaterial_Chimera_Epic: '史诗遗物锻造材料',
  RelicCraftMaterial_Chimera_Legendary: '传说遗物锻造材料',
  RelicCraftMaterial_Chimera_Mythical: '神话遗物锻造材料',
}

const REWARD_CHEST_LABELS: Record<number, string> = {
  19001: '遗物石宝箱 I',
  19002: '遗物石宝箱 II',
  19003: '遗物石宝箱 III',
  19004: '遗物石宝箱 IV',
}

function rewardIdentity(entry: RewardEntry) {
  if (typeof entry.resourceTypeId === 'number') return `resource-${entry.resourceTypeId}`
  if (entry.type === 'RelicStones') return 'relic-stones'
  if (typeof entry.blackMarketItemId === 'number') return `bmi-${entry.blackMarketItemId}`
  return 'chimera'
}

function rewardLabel(entry: RewardEntry) {
  if (entry.resourceType && REWARD_RESOURCE_LABELS[entry.resourceType]) return REWARD_RESOURCE_LABELS[entry.resourceType]
  if (typeof entry.blackMarketItemId === 'number') return REWARD_CHEST_LABELS[entry.blackMarketItemId] ?? '奇美拉奖励宝箱'
  if (entry.type === 'RelicStones') return '随机遗物石'
  return entry.resourceType || entry.type || '奇美拉奖励'
}

function rewardCount(entry: RewardEntry) {
  const minimum = Number(entry.minCount ?? 0)
  const maximum = Number(entry.maxCount ?? 0)
  if (maximum > 0 && maximum !== minimum) return `×${minimum}–${maximum}`
  return `×${minimum || maximum || 1}`
}

function rewardRarity(entry: RewardEntry) {
  const resource = String(entry.resourceType ?? '').toLowerCase()
  if (resource.includes('mythical') || entry.blackMarketItemId === 19004) return 'mythical'
  if (resource.includes('legendary') || entry.blackMarketItemId === 19003) return 'legendary'
  if (resource.includes('epic') || entry.blackMarketItemId === 19002) return 'epic'
  return 'rare'
}

function selectNumericInput(event: { currentTarget: HTMLInputElement }) {
  event.currentTarget.select()
}

function asNumberArray(value: unknown): number[] {
  if (Array.isArray(value)) return value.filter((item): item is number => typeof item === 'number')
  return typeof value === 'number' ? [value] : []
}

function ruleHeroIds(rule: Rule) {
  return asNumberArray(rule.when?.activeHeroTypeId)
}

function heroRuntimeIds(hero?: Hero) {
  return hero ? (hero.runtimeTypeIds?.length ? hero.runtimeTypeIds : [hero.typeId]) : []
}

function heroMatchesIds(hero: Hero, ids: number[]) {
  return heroRuntimeIds(hero).some((id) => ids.includes(id))
}

function heroByRuntimeId(heroes: Hero[], typeId?: number) {
  return typeof typeId === 'number'
    ? heroes.find((hero) => heroRuntimeIds(hero).includes(typeId))
    : undefined
}

function defaultHeroPriorityIds(hero?: Hero) {
  return [...(hero?.skills ?? [])]
    .filter((skill): skill is Skill & { typeId: number } => typeof skill.typeId === 'number')
    .sort((left, right) => Number(left.isTransform) - Number(right.isTransform) || right.slot - left.slot)
    .map((skill) => skill.typeId)
}

function ruleForms(rule: Rule): string[] {
  const value = rule.when?.form
  return Array.isArray(value) ? value.map(String) : value ? [String(value)] : ALL_FORMS
}

function actionLabel(rule: Rule, heroes: Hero[]) {
  const action = rule.action ?? {}
  if (action.type === 'defaultSkillPriority') {
    const count = Array.isArray(action.prioritySkills) ? action.prioritySkills.length : 0
    return `默认技能顺序 · ${count} 个可用技能`
  }
  if (action.type === 'executeTrialRecipe') return '按当前试炼自动决策'
  if (action.type === 'maintainEffects') return '自动维持增益 / 减益'
  const slot = typeof action.skillSlot === 'number' ? action.skillSlot : undefined
  const skillTypeId = typeof action.skillTypeId === 'number' ? action.skillTypeId : undefined
  const hero = heroes.find((item) => heroMatchesIds(item, ruleHeroIds(rule)))
  const skill = hero?.skills.find((item) => skillTypeId ? item.typeId === skillTypeId : item.slot === slot)
  if (action.type === 'transform') {
    const direction = action.toFormIndex === 0 ? '切回原始形态' : action.toFormIndex === 1 ? '切换至变形形态' : '切换神话形态'
    return skill?.name ? `${direction} · ${skill.name}` : direction
  }
  return skill?.name || (slot ? `技能 ${slot}` : '施放技能')
}

function targetLabel(rule: Rule, heroes: Hero[], hydraHeads: HydraHead[] = []): string {
  const action = rule.action ?? {}
  if (action.type === 'defaultSkillPriority') {
    const firstEntry = Array.isArray(action.prioritySkills)
      ? action.prioritySkills.find((entry) => entry && typeof entry === 'object' && !Array.isArray(entry)) as JsonObject | undefined
      : undefined
    const preferred = firstEntry?.target
    const preferredObject = typeof preferred === 'string' ? { type: preferred } : (preferred as JsonObject | undefined)
    if (!preferredObject?.type || preferredObject.type === 'auto') return '按技能合法目标自动选择'
    return `优先${targetLabel({ action: { type: 'cast', target: preferredObject } }, heroes, hydraHeads)}，无效时自动选择`
  }
  if (action.type === 'transform') return '自己'
  const raw = action.target
  const target = typeof raw === 'string' ? { type: raw } : ((raw ?? {}) as JsonObject)
  if (target.type === 'self') return '自己'
  if (target.type === 'lowestHpAlly') return '生命最低的队友'
  if (target.type === 'allyHeroTypeId') {
    return heroByRuntimeId(heroes, typeof target.heroTypeId === 'number' ? target.heroTypeId : undefined)?.name ?? '指定队友'
  }
  if (target.type === 'allyPosition') return `${target.position ?? '?'} 号位队友`
  if (target.type === 'hydraHeadSlot') return '生命最低蛇头（旧位置规则已安全迁移）'
  if (target.type === 'hydraHeadPriority') {
    const names = asNumberArray(target.headTypeIds).slice(0, 3).map((typeId) => {
      const head = hydraHeads.find((item) => item.typeId === typeId)
      return head ? hydraHeadDisplayName(head) : `蛇头 ${typeId}`
    })
    return names.length ? `按类型优先：${names.join(' → ')}${asNumberArray(target.headTypeIds).length > 3 ? '…' : ''}` : '蛇头类型优先 · 生命最低兜底'
  }
  if (target.type === 'devouringHead') return '正在吞噬的蛇头'
  if (target.type === 'exposedNeck') return '暴露蛇颈'
  if (target.type === 'lowestHpBoss') return '生命最低蛇头'
  return '奇美拉 Boss'
}

function conditionLabel(rule: Rule, effects: EffectOption[] = [], heroes: Hero[] = []) {
  const when = rule.when ?? {}
  const ignored = new Set(['activeHeroTypeId', 'form', 'activeHeroFormIndex', 'activeHeroIsMetamorph'])
  const summarized = new Set<string>()
  const turn = when.chimeraTurnAtLeast ?? when.chimeraTurnCount
  const next = typeof when.nextForm === 'string' ? FORM_LABEL[when.nextForm] : undefined
  const parts = [turn !== undefined ? `Boss 回合 ${turn}+` : '', next ? `下一形态：${next}` : ''].filter(Boolean)
  if (turn !== undefined) summarized.add(when.chimeraTurnAtLeast !== undefined ? 'chimeraTurnAtLeast' : 'chimeraTurnCount')
  if (next) summarized.add('nextForm')
  const bossHas = Array.isArray(when.bossHasEffects) ? when.bossHasEffects : []
  if (bossHas.length) {
    const names = bossHas.slice(0, 2).map((token) => {
      const effect = effects.find((item) => item.token === String(token))
      return effect ? effectDisplay(effect).label : String(token)
    })
    parts.push(`Boss 有：${names.join('、')}${bossHas.length > 2 ? '…' : ''}`)
    summarized.add('bossHasEffects')
  }
  const unifiedEffects = Array.isArray(when.effectConditions) ? when.effectConditions : []
  if (unifiedEffects.length) {
    const joiner = when.effectConditionsMode === 'any' ? ' 或 ' : '、'
    const descriptions = unifiedEffects.slice(0, 2).flatMap((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
      const condition = raw as JsonObject
      const selector = condition.effect && typeof condition.effect === 'object' && !Array.isArray(condition.effect) ? condition.effect as JsonObject : {}
      const token = selector.effectTypeId == null ? String(selector.kind ?? '') : String(selector.effectTypeId)
      const effect = effects.find((item) => item.token === token)
      const effectName = effect ? effectDisplay(effect).label : (token || '效果')
      const heroTypeId = asNumberArray(condition.heroTypeId)[0]
      const subject = condition.target === 'ally'
        ? heroByRuntimeId(heroes, heroTypeId)?.name ?? '指定英雄'
        : condition.target === 'bossAll'
          ? '全部蛇头'
          : condition.target === 'bossAny'
            ? '任一蛇头'
            : condition.target === 'bossPriority'
              ? '优先目标蛇头'
              : 'Boss'
      return [`${subject}${condition.presence === 'missing' ? '缺少' : '拥有'}${effectName}`]
    })
    if (descriptions.length) parts.push(`${descriptions.join(joiner)}${unifiedEffects.length > 2 ? '…' : ''}`)
    summarized.add('effectConditions')
    summarized.add('effectConditionsMode')
  }
  const cooldownConditions = Array.isArray(when.skillCooldownConditions) ? when.skillCooldownConditions : []
  if (cooldownConditions.length) {
    const joiner = when.skillCooldownConditionsMode === 'any' ? ' 或 ' : '、'
    const descriptions = cooldownConditions.slice(0, 2).flatMap((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
      const condition = raw as JsonObject
      const hero = heroByRuntimeId(heroes, typeof condition.heroTypeId === 'number' ? condition.heroTypeId : undefined)
      const skill = hero?.skills.find((item) => item.typeId === condition.skillTypeId)
      const range = condition.turnsAtLeast !== undefined && condition.turnsAtMost !== undefined
        ? `${condition.turnsAtLeast}–${condition.turnsAtMost}`
        : condition.turnsAtLeast !== undefined ? `≥${condition.turnsAtLeast}` : `≤${condition.turnsAtMost}`
      return [`${hero?.name ?? '指定英雄'}·${skill?.name ?? '指定技能'}冷却${range}`]
    })
    if (descriptions.length) parts.push(`${descriptions.join(joiner)}${cooldownConditions.length > 2 ? '…' : ''}`)
    summarized.add('skillCooldownConditions')
    summarized.add('skillCooldownConditionsMode')
  }
  const remaining = Object.keys(when).filter((key) => !ignored.has(key) && !summarized.has(key)).length
  if (remaining) parts.push(`另有 ${remaining} 项条件`)
  return parts.join(' · ') || '行动窗口满足时'
}

function HeroAvatar({ hero, size = 'md' }: { hero?: Hero; size?: 'sm' | 'md' | 'lg' }) {
  const [sourceIndex, setSourceIndex] = useState(0)
  const sources = hero
    ? [`/api/asset/hero/${hero.typeId}`, `/hero-fallbacks/${hero.typeId}.png`]
    : []
  useEffect(() => setSourceIndex(0), [hero?.typeId])
  return (
    <span className={`avatar avatar-${size}`} aria-hidden="true">
      {hero && sourceIndex < sources.length ? (
        <img src={sources[sourceIndex]} alt="" onError={() => setSourceIndex((current) => current + 1)} />
      ) : (
        <span>{hero?.name?.slice(0, 1) || '?'}</span>
      )}
    </span>
  )
}

function HydraHeadIcon({ head, size = 'md' }: { head?: HydraHead; size?: 'sm' | 'md' | 'lg' }) {
  const [failed, setFailed] = useState(false)
  const identity = head?.canonicalTypeId ?? head?.typeId
  useEffect(() => setFailed(false), [identity])
  return (
    <span className={`avatar hydra-head-avatar avatar-${size}`} aria-hidden="true">
      {head && !failed
        ? <img src={`/api/asset/head/${identity}`} alt="" onError={() => setFailed(true)} />
        : <Waves size={size === 'sm' ? 15 : size === 'lg' ? 24 : 19} />}
    </span>
  )
}

function SkillIcon({ hero, skill, slot }: { hero?: Hero; skill?: Skill; slot?: number }) {
  const [failed, setFailed] = useState(false)
  const identity = skill?.typeId ?? slot
  useEffect(() => setFailed(false), [hero?.typeId, identity])
  if (!hero || !identity || failed) {
    return <span className="skill-icon"><Zap size={17} /></span>
  }
  return (
    <span className="skill-icon">
      <img src={`/api/asset/skill/${hero.typeId}/${identity}`} alt="" onError={() => setFailed(true)} />
    </span>
  )
}

function EffectIcon({ effect }: { effect: EffectOption }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [effect.icon])
  return (
    <span className="effect-icon">
      {!failed ? <img src={`/api/asset/effect/${encodeURIComponent(effect.icon)}`} alt="" onError={() => setFailed(true)} /> : <Sparkles size={14} />}
    </span>
  )
}

function EffectPicker({
  effects,
  value,
  onValue,
}: {
  effects: EffectOption[]
  value: string
  onValue: (value: string) => void
}) {
  const [search, setSearch] = useState('')
  const selected = effects.find((effect) => effect.token === value)
  const visible = effects.filter((effect) => {
    const display = effectDisplay(effect)
    return `${effect.label} ${effect.group} ${display.label} ${display.group} ${effect.token}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  })
  const selectedDisplay = selected ? effectDisplay(selected) : undefined

  function choose(event: React.MouseEvent<HTMLButtonElement>, token: string) {
    onValue(token)
    const details = event.currentTarget.closest('details') as HTMLDetailsElement | null
    if (details) details.open = false
  }

  return (
    <details className="effect-picker">
      <summary>
        {selected ? <EffectIcon effect={selected} /> : <span className="effect-icon"><Sparkles size={14} /></span>}
        <span>{selected ? <><strong>{selectedDisplay?.label}</strong><small>{selectedDisplay?.group}</small></> : <strong>不限</strong>}</span>
        <ChevronDown size={15} />
      </summary>
      <div className="effect-picker-menu">
        <label className="effect-picker-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} onClick={(event) => event.stopPropagation()} placeholder="搜索效果、类别或编号" /></label>
        <div className="effect-picker-options">
          <button type="button" className={!value ? 'effect-picker-option active' : 'effect-picker-option'} onClick={(event) => choose(event, '')}><span className="effect-icon"><Sparkles size={14} /></span><span><strong>不限</strong><small>不判断此项</small></span></button>
          {visible.map((effect) => { const display = effectDisplay(effect); return <button type="button" key={effect.token} className={effect.token === value ? 'effect-picker-option active' : 'effect-picker-option'} onClick={(event) => choose(event, effect.token)}><EffectIcon effect={effect} /><span><strong>{display.label}</strong><small>{display.group} · ID {effect.token}</small></span></button> })}
        </div>
      </div>
    </details>
  )
}

function RewardIcon({ entry }: { entry: RewardEntry }) {
  const identity = rewardIdentity(entry)
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [identity])
  return (
    <span className={`reward-icon ${rewardRarity(entry)}`} aria-hidden="true">
      {!failed
        ? <img src={`/api/asset/reward/${encodeURIComponent(identity)}`} alt="" onError={() => setFailed(true)} />
        : <Gem size={20} />}
    </span>
  )
}

function TrialRewards({ reward, compact = false }: { reward?: TrialReward; compact?: boolean }) {
  const entries = reward?.entries?.filter((entry) => entry && typeof entry === 'object') ?? []
  if (!entries.length) return <span className="reward-empty">当前奖励尚未读取</span>
  return (
    <span className={`reward-list ${compact ? 'compact' : ''}`}>
      {entries.map((entry, index) => (
        <span className="reward-item" key={`${rewardIdentity(entry)}-${index}`} title={`${rewardLabel(entry)} ${rewardCount(entry)}`}>
          <RewardIcon entry={entry} />
          <span className="reward-copy">
            <strong>{rewardLabel(entry)}</strong>
            <small>{rewardCount(entry)}{typeof entry.probability === 'number' && entry.probability < 100 ? ` · ${entry.probability}%` : ''}</small>
          </span>
        </span>
      ))}
    </span>
  )
}

function NumericInput({
  value,
  onValue,
  decimal = false,
  maximum,
  placeholder,
}: {
  value: number
  onValue: (value: number) => void
  decimal?: boolean
  maximum?: number
  placeholder?: string
}) {
  const normalized = Number.isFinite(value) ? String(value) : '0'
  const [draft, setDraft] = useState(normalized)
  useEffect(() => setDraft(normalized), [normalized])

  function accept(raw: string, finalize = false) {
    const pattern = decimal ? /^\d*(?:\.\d*)?$/ : /^\d*$/
    if (!pattern.test(raw)) return
    setDraft(raw)
    if (!raw && !finalize) return
    const parsed = Number(raw || 0)
    if (!Number.isFinite(parsed)) return
    const next = Math.max(0, maximum === undefined ? parsed : Math.min(parsed, maximum))
    if (finalize) setDraft(String(next))
    onValue(next)
  }

  return (
    <input
      type="text"
      inputMode={decimal ? 'decimal' : 'numeric'}
      value={draft}
      onFocus={selectNumericInput}
      onChange={(event) => accept(event.target.value)}
      onBlur={(event) => accept(event.target.value, true)}
      placeholder={placeholder}
    />
  )
}

function Metric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="metric">
      <span className="metric-icon">{icon}</span>
      <span><small>{label}</small><strong>{value}</strong></span>
    </div>
  )
}

function TrialPicker({
  open,
  onOpenChange,
  trials,
  selected,
  onApply,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  trials: Trial[]
  selected: number[]
  onApply: (ids: number[]) => void
}) {
  const [draft, setDraft] = useState<number[]>(selected)
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) setDraft(selected)
  }, [open, selected])

  const visible = trials.filter((trial) => {
    const term = search.trim()
    const rewards = trial.reward?.entries?.map(rewardLabel).join(' ') ?? ''
    return !term || `${cleanText(trial.description)} ${rewards}`.includes(term)
  })

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content trial-dialog">
          <div className="dialog-heading">
            <div><Dialog.Title>选择必做试炼</Dialog.Title><Dialog.Description>每项都显示当前轮换奖励，可按奖励名称搜索后决定是否必做。</Dialog.Description></div>
            <Dialog.Close className="icon-button" aria-label="关闭"><X size={19} /></Dialog.Close>
          </div>
          <label className="search-box"><Search size={19} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索试炼内容或奖励" /></label>
          <div className="trial-list">
            {visible.map((trial) => {
              const checked = draft.includes(trial.id)
              return (
                <button
                  type="button"
                  key={trial.id}
                  className={`trial-option ${checked ? 'selected' : ''}`}
                  onClick={() => setDraft((current) => checked ? current.filter((id) => id !== trial.id) : [...current, trial.id])}
                >
                  <span className="check-box">{checked && <Check size={15} />}</span>
                  <span className="trial-copy">
                    <span className="trial-tags">
                      <em>{FORM_LABEL[trial.form ?? ''] ?? trial.form}</em>
                      <em>{TRIAL_LEVEL[trial.difficulty ?? ''] ?? trial.difficulty}</em>
                      {trial.completed && <em className="success">已完成</em>}
                    </span>
                    <strong>{cleanText(trial.description)}</strong>
                    <span className="trial-reward-heading">当前轮换奖励</span>
                    <TrialRewards reward={trial.reward} />
                  </span>
                </button>
              )
            })}
          </div>
          <div className="dialog-footer">
            <span>已选择 {draft.length} 项</span>
            <div><button className="button ghost" onClick={() => setDraft([])}>清空</button><button className="button primary" onClick={() => { onApply(draft); onOpenChange(false) }}>应用选择</button></div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function RuleEditor({
  open,
  onOpenChange,
  initial,
  allRules,
  heroes,
  hydraHeads,
  team,
  effects,
  bossMode,
  onSave,
}: {
  open: boolean
  onOpenChange: (value: boolean) => void
  initial?: Rule
  allRules: Rule[]
  heroes: Hero[]
  hydraHeads: HydraHead[]
  team: number[]
  effects: EffectOption[]
  bossMode: BossMode
  onSave: (rule: Rule) => void
}) {
  const firstHero = heroes[0]?.typeId ?? 0
  const [name, setName] = useState('')
  const [ruleKind, setRuleKind] = useState<'strict' | 'default'>('strict')
  const [heroId, setHeroId] = useState(firstHero)
  const [allHeroes, setAllHeroes] = useState(false)
  const [heroSearch, setHeroSearch] = useState('')
  const [forms, setForms] = useState<string[]>(ALL_FORMS)
  const [heroForm, setHeroForm] = useState('any')
  const [actionType, setActionType] = useState('cast')
  const [slot, setSlot] = useState(1)
  const [skillTypeId, setSkillTypeId] = useState<number | undefined>()
  const [prioritySkillIds, setPrioritySkillIds] = useState<number[]>([])
  const [blockedSkillIds, setBlockedSkillIds] = useState<number[]>([])
  const [target, setTarget] = useState('boss')
  const [targetPosition, setTargetPosition] = useState(1)
  const [headPriorityIds, setHeadPriorityIds] = useState<number[]>([])
  const [advanced, setAdvanced] = useState('{}')
  const [turnMin, setTurnMin] = useState('')
  const [turnMax, setTurnMax] = useState('')
  const [switchWithin, setSwitchWithin] = useState('')
  const [nextForm, setNextForm] = useState('')
  const [damageMin, setDamageMin] = useState('')
  const [effectConditions, setEffectConditions] = useState<EffectConditionValue[]>([])
  const [effectConditionsMode, setEffectConditionsMode] = useState<'all' | 'any'>('all')
  const [skillCooldownConditions, setSkillCooldownConditions] = useState<SkillCooldownConditionValue[]>([])
  const [skillCooldownConditionsMode, setSkillCooldownConditionsMode] = useState<'all' | 'any'>('all')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    const when = { ...(initial?.when ?? {}) }
    const ids = asNumberArray(when.activeHeroTypeId)
    const initialHero = heroes.find((candidate) => heroMatchesIds(candidate, ids))
    setName(initial?.name ?? '')
    setHeroId(initialHero?.typeId ?? firstHero)
    setAllHeroes(ids.length > 1 && heroes.length > 0 && heroes.every((hero) => heroRuntimeIds(hero).some((id) => ids.includes(id))))
    setHeroSearch('')
    setForms(ruleForms(initial ?? {}))
    setHeroForm(when.activeHeroFormIndex === 0 ? 'original' : when.activeHeroFormIndex === 1 ? 'transformed' : 'any')
    const action = initial?.action ?? {}
    const defaultRule = action.type === 'defaultSkillPriority'
    setRuleKind(defaultRule ? 'default' : 'strict')
    setActionType(typeof action.type === 'string' ? action.type : 'cast')
    setSlot(typeof action.skillSlot === 'number' ? action.skillSlot : 1)
    setSkillTypeId(typeof action.skillTypeId === 'number' ? action.skillTypeId : undefined)
    const catalogPriority = defaultHeroPriorityIds(initialHero ?? heroes[0])
    const savedPriorityEntries = Array.isArray(action.prioritySkills)
      ? action.prioritySkills.filter((entry): entry is JsonObject => Boolean(entry && typeof entry === 'object' && !Array.isArray(entry)))
      : []
    const savedPriority = savedPriorityEntries.flatMap((entry) => typeof entry.skillTypeId === 'number' ? [entry.skillTypeId] : [])
    setPrioritySkillIds([...savedPriority, ...catalogPriority.filter((id) => !savedPriority.includes(id))])
    setBlockedSkillIds(asNumberArray(action.blockedSkillTypeIds))
    const rawTarget = action.target ?? (defaultRule ? savedPriorityEntries.find((entry) => entry.target)?.target : undefined)
    const targetObject = typeof rawTarget === 'string' ? { type: rawTarget } : ((rawTarget ?? {}) as JsonObject)
    const savedTargetType = typeof targetObject.type === 'string' ? targetObject.type : undefined
    setTarget(savedTargetType === 'hydraHeadSlot' ? 'hydraHeadPriority' : (savedTargetType ?? (defaultRule ? 'auto' : (bossMode === 'hydra' ? 'hydraHeadPriority' : 'boss'))))
    setTargetPosition(typeof targetObject.position === 'number' ? targetObject.position : 1)
    setHeadPriorityIds(asNumberArray(targetObject.headTypeIds))
    const turnAtLeast = bossMode === 'chimera' ? when.chimeraTurnAtLeast : undefined
    const turnAtMost = bossMode === 'chimera' ? when.chimeraTurnAtMost : undefined
    setTurnMin(turnAtLeast == null ? '' : String(turnAtLeast))
    setTurnMax(turnAtMost == null ? '' : String(turnAtMost))
    setSwitchWithin(when.turnsUntilFormChangeAtMost == null ? '' : String(when.turnsUntilFormChangeAtMost))
    setNextForm(typeof when.nextForm === 'string' ? when.nextForm : '')
    setDamageMin(when.currentDamageAtLeast == null ? '' : String(damageInMillions(Number(when.currentDamageAtLeast))))
    const fallbackTeamHeroId = team.find((typeId) => Number.isInteger(typeId) && typeId > 0) ?? ids[0] ?? 0
    const actionTeamHeroId = team.find((typeId) => {
      const teamHero = heroByRuntimeId(heroes, typeId)
      return ids.includes(typeId) || (teamHero ? heroMatchesIds(teamHero, ids) : false)
    }) ?? ids[0] ?? fallbackTeamHeroId
    const nextEffectConditions: EffectConditionValue[] = []
    const nextCooldownConditions: SkillCooldownConditionValue[] = []
    const appendEffectCondition = (
      target: EffectConditionValue['target'],
      presence: 'has' | 'missing',
      selector: unknown,
      heroTypeId = 0,
    ) => {
      const value = selector && typeof selector === 'object' && !Array.isArray(selector)
        ? selector as JsonObject
        : { kind: selector }
      const token = value.effectTypeId == null ? String(value.kind ?? '') : String(value.effectTypeId)
      if (!token) return
      const normalizedHeroTypeId = target === 'ally' ? heroTypeId : 0
      const duplicate = nextEffectConditions.find((condition) => condition.target === target
        && condition.presence === presence
        && condition.token === token
        && Number(condition.heroTypeId || 0) === normalizedHeroTypeId)
      const turnsAtLeast = presence === 'has' && value.turnsAtLeast != null ? String(value.turnsAtLeast) : ''
      const turnsAtMost = presence === 'has' && value.turnsAtMost != null ? String(value.turnsAtMost) : ''
      if (duplicate) {
        if (turnsAtLeast) duplicate.turnsAtLeast = turnsAtLeast
        if (turnsAtMost) duplicate.turnsAtMost = turnsAtMost
        return
      }
      nextEffectConditions.push(createEffectCondition({
        target,
        presence,
        heroTypeId: target === 'ally' ? String(normalizedHeroTypeId || '') : '',
        token,
        turnsAtLeast,
        turnsAtMost,
      }))
    }

    if (Array.isArray(when.effectConditions)) {
      for (const rawCondition of when.effectConditions) {
        if (!rawCondition || typeof rawCondition !== 'object' || Array.isArray(rawCondition)) continue
        const condition = rawCondition as JsonObject
        const target = condition.target === 'ally'
          ? 'ally'
          : condition.target === 'bossAll'
            ? 'bossAll'
            : condition.target === 'bossAny'
              ? 'bossAny'
              : condition.target === 'bossPriority'
                ? 'bossPriority'
                : condition.target === 'boss'
                  ? (bossMode === 'hydra' ? 'bossPriority' : 'boss')
                  : undefined
        const presence = condition.presence === 'missing' ? 'missing' : condition.presence === 'has' ? 'has' : undefined
        if (!target || !presence) continue
        appendEffectCondition(target, presence, condition.effect, asNumberArray(condition.heroTypeId)[0] ?? fallbackTeamHeroId)
      }
    }
    setEffectConditionsMode(when.effectConditionsMode === 'any' ? 'any' : 'all')
    delete when.effectConditions
    delete when.effectConditionsMode

    for (const token of Array.isArray(when.bossHasEffects) ? when.bossHasEffects : []) appendEffectCondition('boss', 'has', token)
    for (const token of Array.isArray(when.bossMissingEffects) ? when.bossMissingEffects : []) appendEffectCondition('boss', 'missing', token)
    appendEffectCondition('boss', 'has', when.bossHasEffect)
    appendEffectCondition('boss', 'missing', when.bossMissingEffect)
    appendEffectCondition('ally', 'has', when.activeHeroHasEffect, actionTeamHeroId)
    appendEffectCondition('ally', 'missing', when.activeHeroMissingEffect, actionTeamHeroId)
    appendEffectCondition('ally', 'has', when.anyAllyHasEffect, fallbackTeamHeroId)
    appendEffectCondition('ally', 'missing', when.anyAllyMissingEffect, fallbackTeamHeroId)
    for (const [key, presence] of [['allyHasEffect', 'has'], ['allyMissingEffect', 'missing']] as const) {
      const requested = when[key]
      if (!requested || typeof requested !== 'object' || Array.isArray(requested)) continue
      const request = requested as JsonObject
      appendEffectCondition('ally', presence, request.effect, asNumberArray(request.heroTypeId)[0] ?? fallbackTeamHeroId)
    }
    for (const key of LEGACY_EFFECT_KEYS) delete when[key]
    setEffectConditions(nextEffectConditions)
    if (Array.isArray(when.skillCooldownConditions)) {
      for (const rawCondition of when.skillCooldownConditions) {
        if (!rawCondition || typeof rawCondition !== 'object' || Array.isArray(rawCondition)) continue
        const condition = rawCondition as JsonObject
        const heroTypeId = typeof condition.heroTypeId === 'number' ? condition.heroTypeId : fallbackTeamHeroId
        const skillTypeId = typeof condition.skillTypeId === 'number' ? condition.skillTypeId : 0
        if (heroTypeId <= 0 || skillTypeId <= 0) continue
        nextCooldownConditions.push(createSkillCooldownCondition({
          heroTypeId: String(heroTypeId),
          skillTypeId: String(skillTypeId),
          turnsAtLeast: condition.turnsAtLeast == null ? '' : String(condition.turnsAtLeast),
          turnsAtMost: condition.turnsAtMost == null ? '' : String(condition.turnsAtMost),
        }))
      }
    }
    setSkillCooldownConditionsMode(when.skillCooldownConditionsMode === 'any' ? 'any' : 'all')
    delete when.skillCooldownConditions
    delete when.skillCooldownConditionsMode
    setSkillCooldownConditions(nextCooldownConditions)
    delete when.activeHeroTypeId
    delete when.form
    delete when.activeHeroFormIndex
    delete when.chimeraTurnAtLeast
    delete when.chimeraTurnAtMost
    delete when.turnAtLeast
    delete when.turnAtMost
    delete when.turnsUntilFormChangeAtMost
    delete when.nextForm
    delete when.currentDamageAtLeast
    if (bossMode === 'hydra') {
      for (const key of HYDRA_BATTLE_TURN_CONDITION_KEYS) delete when[key]
    }
    setAdvanced(JSON.stringify(when, null, 2))
    setError('')
  }, [open, initial, firstHero, bossMode])

  const hero = heroes.find((item) => item.typeId === heroId)
  const allHeroSkills = hero?.skills ?? []
  const skills = heroForm === 'any'
    ? allHeroSkills
    : allHeroSkills.filter((item) => (item.formIndex ?? 0) === (heroForm === 'transformed' ? 1 : 0))
  const visibleHeroes = heroes
    .filter((item) => item.name.toLocaleLowerCase().includes(heroSearch.trim().toLocaleLowerCase()))
    .slice(0, 48)
  const teamSize = bossMode === 'hydra' ? 6 : 5
  const teamHeroes = team.slice(0, teamSize).map((typeId) => heroByRuntimeId(heroes, typeId))
  const selectedSkillCandidate = skills.find((item) => item.typeId === skillTypeId)
    ?? skills.find((item) => item.slot === slot)
    ?? skills[0]
  const selectedSkill = actionType === 'transform' && !selectedSkillCandidate?.isTransform
    ? skills.find((item) => item.isTransform) ?? selectedSkillCandidate
    : selectedSkillCandidate
  const reservedSkillIds = new Set(allRules.flatMap((rule) => {
    if (rule === initial || !hero || !heroMatchesIds(hero, ruleHeroIds(rule))) return []
    const action = rule.action ?? {}
    return action.type === 'cast' || action.type === 'transform'
      ? asNumberArray(action.skillTypeId)
      : []
  }))

  function commit() {
    try {
      const extra = JSON.parse(advanced || '{}')
      if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error('高级条件必须是一个对象')
      if (bossMode === 'hydra') {
        for (const key of HYDRA_BATTLE_TURN_CONDITION_KEYS) delete extra[key]
      }
      if (bossMode === 'chimera' && !forms.length) throw new Error('至少选择一个奇美拉形态')
      const when: JsonObject = {
        ...(ruleKind === 'strict' ? extra : {}),
        activeHeroTypeId: ruleKind === 'strict' && allHeroes
          ? Array.from(new Set(heroes.flatMap((item) => heroRuntimeIds(item))))
          : heroRuntimeIds(hero),
      }
      if (bossMode === 'chimera') when.form = forms
      const actionPinsHeroForm = ruleKind === 'strict' && (actionType === 'cast' || actionType === 'transform')
      if (actionPinsHeroForm && heroForm !== 'any') {
        when.activeHeroFormIndex = heroForm === 'original' ? 0 : 1
      } else if (actionPinsHeroForm && !allHeroes && typeof selectedSkill?.formIndex === 'number' && hero?.isMetamorph) {
        when.activeHeroFormIndex = selectedSkill.formIndex
      }
      if (ruleKind === 'strict') {
        const numericConditions: Array<[string, string]> = [
          ...(bossMode === 'chimera' ? [
            ['chimeraTurnAtLeast', turnMin] as [string, string],
            ['chimeraTurnAtMost', turnMax] as [string, string],
            ['turnsUntilFormChangeAtMost', switchWithin] as [string, string],
          ] : []),
          ['currentDamageAtLeast', damageMin],
        ]
        for (const [key, raw] of numericConditions) {
          if (raw.trim()) {
            const number = Number(raw)
            if (!Number.isFinite(number) || number < 0) throw new Error('常用触发条件必须是非负数')
            when[key] = key === 'currentDamageAtLeast' ? number * 1_000_000 : number
          }
        }
        if (bossMode === 'chimera' && nextForm) when.nextForm = nextForm
        if (effectConditions.length) {
          const savedConditions: JsonObject[] = []
          for (const value of effectConditions) {
            if (!value.token) throw new Error('请为每一条效果条件选择具体效果，或删除未完成的条件')
            const selector: JsonObject = /^\d+$/.test(value.token)
              ? { effectTypeId: Number(value.token) }
              : { kind: value.token }
            const condition: JsonObject = {
              target: value.target,
              presence: value.presence,
              effect: selector,
            }
            if (value.target === 'ally') {
              const selectedHeroTypeId = Number(value.heroTypeId)
              if (!Number.isInteger(selectedHeroTypeId) || selectedHeroTypeId <= 0 || !team.slice(0, teamSize).includes(selectedHeroTypeId)) {
                throw new Error('英雄效果条件必须从当前准备队伍中选择一名具体英雄')
              }
              condition.heroTypeId = selectedHeroTypeId
            }
            for (const [turnKey, raw] of (value.presence === 'has' ? [['turnsAtLeast', value.turnsAtLeast], ['turnsAtMost', value.turnsAtMost]] : []) as Array<['turnsAtLeast' | 'turnsAtMost', string]>) {
              if (!raw.trim()) continue
              const turns = Number(raw)
              if (!Number.isInteger(turns) || turns < 0) throw new Error('效果剩余回合必须是非负整数')
              selector[turnKey] = turns
            }
            savedConditions.push(condition)
          }
          when.effectConditions = savedConditions
          when.effectConditionsMode = effectConditionsMode
        }
        if (skillCooldownConditions.length) {
          const savedCooldownConditions: JsonObject[] = []
          for (const value of skillCooldownConditions) {
            const selectedHeroTypeId = Number(value.heroTypeId)
            const selectedSkillTypeId = Number(value.skillTypeId)
            const selectedHero = heroByRuntimeId(heroes, selectedHeroTypeId)
            if (!Number.isInteger(selectedHeroTypeId) || selectedHeroTypeId <= 0 || !team.slice(0, teamSize).includes(selectedHeroTypeId) || !selectedHero) {
              throw new Error('技能冷却条件必须从当前准备队伍中选择一名具体英雄')
            }
            if (!Number.isInteger(selectedSkillTypeId) || !selectedHero.skills.some((skill) => skill.typeId === selectedSkillTypeId)) {
              throw new Error('请为技能冷却条件选择该英雄的具体技能')
            }
            const condition: JsonObject = {
              heroTypeId: selectedHeroTypeId,
              skillTypeId: selectedSkillTypeId,
            }
            for (const [turnKey, raw] of [['turnsAtLeast', value.turnsAtLeast], ['turnsAtMost', value.turnsAtMost]] as const) {
              if (!raw.trim()) continue
              const turns = Number(raw)
              if (!Number.isInteger(turns) || turns < 0) throw new Error('技能剩余冷却必须是非负整数')
              condition[turnKey] = turns
            }
            if (condition.turnsAtLeast === undefined && condition.turnsAtMost === undefined) {
              throw new Error('每条技能冷却条件至少填写一个剩余冷却范围')
            }
            if (typeof condition.turnsAtLeast === 'number' && typeof condition.turnsAtMost === 'number' && condition.turnsAtLeast > condition.turnsAtMost) {
              throw new Error('技能冷却条件的最小回合不能大于最大回合')
            }
            savedCooldownConditions.push(condition)
          }
          when.skillCooldownConditions = savedCooldownConditions
          when.skillCooldownConditionsMode = skillCooldownConditionsMode
        }
      }
      const selectedTarget: JsonObject = target === 'allyPosition'
        ? { type: target, position: targetPosition }
        : target === 'hydraHeadPriority'
          ? { type: target, headTypeIds: headPriorityIds, fallback: 'lowestHp' }
          : { type: target }
      const baseAction: JsonObject = ruleKind === 'default'
        ? {
            type: 'defaultSkillPriority',
            prioritySkills: prioritySkillIds
              .filter((id) => !blockedSkillIds.includes(id))
              .flatMap((id) => {
                const skill = hero?.skills.find((item) => item.typeId === id)
                 return skill ? [{ skillTypeId: id, skillSlot: skill.slot, formIndex: skill.formIndex ?? 0, isTransform: Boolean(skill.isTransform), target: selectedTarget }] : []
              }),
            blockedSkillTypeIds: blockedSkillIds,
            reserveStrictRuleSkills: true,
          }
        : actionType === 'transform'
        ? {
            type: 'transform',
            skillSlot: selectedSkill?.slot ?? slot,
            ...(selectedSkill?.typeId ? { skillTypeId: selectedSkill.typeId } : {}),
            toFormIndex: (selectedSkill?.formIndex ?? 0) === 0 ? 1 : 0,
          }
        : actionType === 'executeTrialRecipe'
          ? { type: 'executeTrialRecipe' }
          : actionType === 'maintainEffects'
            ? { type: 'maintainEffects' }
            : {
                type: 'cast',
                skillSlot: selectedSkill?.slot ?? slot,
                ...(!allHeroes && selectedSkill?.typeId ? { skillTypeId: selectedSkill.typeId } : {}),
                target: target === 'allyHeroTypeId' && initial?.action?.target
                    ? initial.action.target
                    : selectedTarget,
              }
      const effectiveActionType = ruleKind === 'default' ? 'defaultSkillPriority' : actionType
      const action: JsonObject = initial?.action?.type === effectiveActionType
        ? { ...initial.action, ...baseAction }
        : baseAction
      if (ruleKind === 'strict' && actionType === 'cast' && (allHeroes || !selectedSkill?.typeId)) {
        delete action.skillTypeId
      }
      const autoName = ruleKind === 'default'
        ? `${hero?.name ?? '英雄'} · 默认技能顺序`
        : `${hero?.name ?? '英雄'} · ${actionType === 'transform' ? '切换形态' : actionLabel({ when, action }, heroes)} → ${targetLabel({ when, action }, heroes, hydraHeads)}`
      onSave({ name: name.trim() || autoName, when, action })
      onOpenChange(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content rule-dialog">
          <div className="dialog-heading">
            <div><Dialog.Title>{initial ? '编辑策略规则' : '添加策略规则'}</Dialog.Title><Dialog.Description>用英雄、形态、具体技能和目标描述一次行动。</Dialog.Description></div>
            <Dialog.Close className="icon-button" aria-label="关闭"><X size={19} /></Dialog.Close>
          </div>
          <div className="form-grid">
            <label className="field span-2"><span>规则名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="留空会自动生成" /></label>
            <div className="field span-2">
              <span>行动英雄</span>
              <div className="hero-chooser">
                <label className="hero-search"><Search size={15} /><input value={heroSearch} onChange={(event) => setHeroSearch(event.target.value)} placeholder="搜索英雄名称" /></label>
                {ruleKind === 'strict' && <button type="button" aria-pressed={allHeroes} className={allHeroes ? 'all-heroes active' : 'all-heroes'} onClick={() => { const next = !allHeroes; setAllHeroes(next); setActionType(next && bossMode === 'chimera' ? 'executeTrialRecipe' : 'cast') }}><Users size={15} />{allHeroes ? '取消任意英雄' : '任意行动英雄'}</button>}
              </div>
              {!allHeroes && <div className="hero-library">
                {visibleHeroes.map((item) => <button type="button" key={item.typeId} className={item.typeId === heroId ? 'hero-option active' : 'hero-option'} onClick={() => { setHeroId(item.typeId); setAllHeroes(false); setSkillTypeId(undefined); setSlot(1); setHeroForm('any'); setPrioritySkillIds(defaultHeroPriorityIds(item)); setBlockedSkillIds([]); setActionType(ruleKind === 'default' ? 'defaultSkillPriority' : 'cast') }}><HeroAvatar hero={item} size="sm" /><span><strong>{item.name}</strong><small>{item.isMetamorph ? '神话 · 双形态' : `${item.skills.length} 个主动技能`}</small></span></button>)}
                {!visibleHeroes.length && <span className="library-empty">没有找到英雄</span>}
              </div>}
              <small className="library-hint">英雄库已按头像身份去重；最多显示前 48 个搜索结果，共 {heroes.length} 名。</small>
            </div>
            <div className="rule-kind span-2">
              <span className="mode-heading">规则类型</span>
              <button type="button" className={ruleKind === 'strict' ? 'active' : ''} onClick={() => { setRuleKind('strict'); setActionType('cast') }}><ShieldCheck size={16} /><span><strong>严格执行规则</strong><small>只有全部条件满足时才释放指定技能</small></span></button>
              <button type="button" className={ruleKind === 'default' ? 'active' : ''} onClick={() => { setRuleKind('default'); setAllHeroes(false); setActionType('defaultSkillPriority'); if (ruleKind !== 'default') setTarget('auto'); if (!prioritySkillIds.length) setPrioritySkillIds(defaultHeroPriorityIds(hero)) }}><Sparkles size={16} /><span><strong>默认技能规则</strong><small>没有严格规则可执行时，按设定顺序选择技能</small></span></button>
            </div>
            {bossMode === 'chimera' && <fieldset className="field span-2"><legend>奇美拉形态</legend><div className="chip-group">{ALL_FORMS.map((form) => <button type="button" key={form} className={forms.includes(form) ? 'chip active' : 'chip'} onClick={() => setForms((current) => current.includes(form) ? current.filter((item) => item !== form) : [...current, form])}>{FORM_LABEL[form]}</button>)}</div></fieldset>}
            {hero?.isMetamorph && <fieldset className="field span-2"><legend>英雄形态与技能组</legend><div className="chip-group"><button type="button" className={heroForm === 'any' ? 'chip active' : 'chip'} onClick={() => setHeroForm('any')}>同时查看两套</button><button type="button" className={heroForm === 'original' ? 'chip active' : 'chip'} onClick={() => { setHeroForm('original'); setSkillTypeId(undefined); setSlot(1) }}>原始形态</button><button type="button" className={heroForm === 'transformed' ? 'chip active' : 'chip'} onClick={() => { setHeroForm('transformed'); setSkillTypeId(undefined); setSlot(1) }}>变形形态</button></div></fieldset>}
            {ruleKind === 'default' && <fieldset className="skill-policy span-2">
              <legend>默认技能释放顺序 <small>严格规则始终优先，且其技能会自动保留</small></legend>
              <p>从上到下寻找第一个已就绪技能；默认禁用的技能不会由该规则释放。复活技能会先选择已经死亡且可复活的队友。</p>
              <div className="skill-policy-list">{prioritySkillIds.map((id, index) => {
                const skill = hero?.skills.find((item) => item.typeId === id)
                if (!skill) return null
                const blocked = blockedSkillIds.includes(id)
                const reserved = reservedSkillIds.has(id)
                const allowedRank = prioritySkillIds.slice(0, index + 1).filter((value) => !blockedSkillIds.includes(value)).length
                return <div className={`skill-policy-row${blocked ? ' blocked' : ''}`} key={id}>
                  <span className="skill-rank">{blocked ? '—' : allowedRank}</span>
                  <SkillIcon hero={hero} skill={skill} slot={skill.slot} />
                  <span className="skill-policy-copy">
                    <strong>{skill.name || `技能 ${skill.slot}`}</strong>
                    <small>{hero?.isMetamorph ? `${(skill.formIndex ?? 0) === 1 ? '变形形态' : '原始形态'} · ` : ''}技能 {skill.slot}{skill.defaultCooldown ? ` · 冷却 ${skill.defaultCooldown}` : ' · 无冷却'}{skill.isTransform ? ' · 形态切换' : ''}</small>
                    <em>{skill.isTransform ? ((skill.formIndex ?? 0) === 0 ? '切换至变形形态' : '切回原始形态') : (skill.effectSummary || cleanText(skill.description) || '暂未读取到技能说明')}</em>
                  </span>
                  {reserved && <em className="reserved-badge">严格规则保留</em>}
                  <div className="skill-policy-actions"><button type="button" title="提高技能优先级" disabled={index === 0} onClick={() => setPrioritySkillIds((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title="降低技能优先级" disabled={index === prioritySkillIds.length - 1} onClick={() => setPrioritySkillIds((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" className={blocked ? 'blocked-toggle active' : 'blocked-toggle'} onClick={() => setBlockedSkillIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])}>{blocked ? '恢复默认使用' : '禁止默认释放'}</button></div>
                </div>
              })}</div>
              <small className="policy-note">“禁止默认释放”只约束默认规则；如果你另外建立了明确的严格规则，严格规则仍可调用该技能。</small>
            </fieldset>}
            {ruleKind === 'default' && <fieldset className="target-picker span-2">
              <legend>默认技能优先目标 <small>优先目标对当前技能不合法或不存在时，自动改用游戏允许的目标</small></legend>
              <div className="condition-grid">
                <label className="field"><span>优先方式</span><select value={target} onChange={(event) => setTarget(event.target.value)}>
                  <option value="auto">自动合法目标</option>
                  {bossMode === 'chimera'
                    ? <option value="boss">奇美拉 Boss</option>
                    : <><option value="hydraHeadPriority">按蛇头类型优先</option><option value="devouringHead">正在吞噬的蛇头</option><option value="exposedNeck">暴露蛇颈</option><option value="lowestHpBoss">生命最低蛇头</option></>}
                  <option value="self">自己</option>
                  <option value="lowestHpAlly">生命最低队友</option>
                  <option value="allyPosition">准备队伍指定位置</option>
                </select></label>
                {target === 'allyPosition' && <label className="field"><span>队伍位置</span><select value={targetPosition} onChange={(event) => setTargetPosition(Number(event.target.value))}>{Array.from({ length: teamSize }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}. {teamHeroes[index]?.name ?? '未读取'}</option>)}</select></label>}
              </div>
              {bossMode === 'hydra' && target === 'hydraHeadPriority' && <div className="head-priority-builder">
                <div className="head-priority-heading"><span><strong>蛇头类型优先级</strong><small>按顺序尝试；不在场、已死亡或当前技能不可选择时自动跳过。</small></span><em>{headPriorityIds.length ? `已排列 ${headPriorityIds.length} 种` : '未设置时自动选择'}</em></div>
                <div className="head-type-library">{hydraHeads.map((head) => { const rank = headPriorityIds.indexOf(head.typeId); return <button type="button" key={head.typeId} className={rank >= 0 ? 'head-type-option active' : 'head-type-option'} onClick={() => { if (rank < 0) setHeadPriorityIds((current) => [...current, head.typeId]) }}><HydraHeadIcon head={head} size="md" /><span><strong>{hydraHeadDisplayName(head)}</strong><small>{rank >= 0 ? `优先级 ${rank + 1}` : '加入优先目标'}</small></span>{rank >= 0 && <em>{rank + 1}</em>}</button> })}</div>
                {headPriorityIds.length > 0 && <div className="head-priority-list">{headPriorityIds.map((typeId, index) => { const head = hydraHeads.find((item) => item.typeId === typeId); return <div className="head-priority-row" key={typeId}><span className="skill-rank">{index + 1}</span><HydraHeadIcon head={head ?? { typeId, name: `蛇头 ${typeId}` }} size="sm" /><span><strong>{head ? hydraHeadDisplayName(head) : `蛇头 ${typeId}`}</strong><small>目标无效时继续尝试下一项</small></span><div><button type="button" title="提高优先级" disabled={index === 0} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title="降低优先级" disabled={index === headPriorityIds.length - 1} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" title="移除" onClick={() => setHeadPriorityIds((current) => current.filter((value) => value !== typeId))}><Trash2 size={15} /></button></div></div> })}</div>}
              </div>}
              <small className="policy-note">复活技能属于特殊情况：只要死亡队友是合法目标，就始终先复活；没有死亡队友时再按这里的优先方式处理。</small>
            </fieldset>}
            {ruleKind === 'strict' && <>
            <div className="action-mode span-2">
              <button type="button" className={actionType === 'cast' || actionType === 'transform' ? 'active' : ''} onClick={() => { setAllHeroes(false); setActionType(selectedSkill?.isTransform ? 'transform' : 'cast') }}><Zap size={16} /><span><strong>指定技能</strong><small>从英雄的实际技能图标选择</small></span></button>
              {bossMode === 'chimera' && <button type="button" className={actionType === 'executeTrialRecipe' ? 'active' : ''} onClick={() => setActionType('executeTrialRecipe')}><Sparkles size={16} /><span><strong>试炼自动决策</strong><small>按当前可完成试炼选择行动</small></span></button>}
              {initial?.action?.type === 'maintainEffects' && <button type="button" className={actionType === 'maintainEffects' ? 'active' : ''} onClick={() => setActionType('maintainEffects')}><ShieldCheck size={16} /><span><strong>维持效果</strong><small>兼容已有高级规则</small></span></button>}
            </div>
            {(actionType === 'cast' || actionType === 'transform') && <>
              <fieldset className="skill-picker span-2">
                <legend>选择实际技能 <small>变形也占用技能、受冷却限制</small></legend>
                <div className="skill-library">{skills.map((skill) => <button type="button" key={`${skill.formIndex ?? 0}-${skill.slot}-${skill.typeId ?? ''}`} className={selectedSkill?.typeId === skill.typeId ? `skill-option active${skill.isTransform ? ' transform' : ''}` : `skill-option${skill.isTransform ? ' transform' : ''}`} onClick={() => { setSkillTypeId(skill.typeId); setSlot(skill.slot); setActionType(skill.isTransform ? 'transform' : 'cast') }}><SkillIcon hero={hero} skill={skill} slot={skill.slot} /><span><strong>{skill.name || `技能 ${skill.slot}`}</strong><small>{hero?.isMetamorph ? `${(skill.formIndex ?? 0) === 1 ? '变形形态' : '原始形态'} · ` : ''}技能 {skill.slot}{skill.defaultCooldown ? ` · 冷却 ${skill.defaultCooldown}` : ' · 无冷却'}</small><em>{skill.isTransform ? ((skill.formIndex ?? 0) === 0 ? '切换至变形形态' : '切回原始形态') : (skill.effectSummary || cleanText(skill.description).slice(0, 56) || '等待读取效果')}</em></span></button>)}</div>
              </fieldset>
              {actionType === 'cast' && <fieldset className="target-picker span-2">
                <legend>技能释放目标 <small>{bossMode === 'hydra' ? '蛇头按固定类型身份判断，不使用会变化的场上位置' : `队伍位置来自当前准备界面的 ${teamSize} 个槽位`}</small></legend>
                <div className="target-quick">
                  {bossMode === 'chimera'
                    ? <button type="button" className={target === 'boss' ? 'active' : ''} onClick={() => setTarget('boss')}><Crosshair size={16} />奇美拉 Boss</button>
                    : <><button type="button" className={target === 'hydraHeadPriority' ? 'active' : ''} onClick={() => setTarget('hydraHeadPriority')}><Waves size={16} />按蛇头类型优先</button><button type="button" className={target === 'devouringHead' ? 'active' : ''} onClick={() => setTarget('devouringHead')}><Crosshair size={16} />正在吞噬的蛇头</button><button type="button" className={target === 'exposedNeck' ? 'active' : ''} onClick={() => setTarget('exposedNeck')}><Zap size={16} />暴露蛇颈</button><button type="button" className={target === 'lowestHpBoss' ? 'active' : ''} onClick={() => setTarget('lowestHpBoss')}><Activity size={16} />生命最低蛇头</button></>}
                  <button type="button" className={target === 'self' ? 'active' : ''} onClick={() => setTarget('self')}><HeroAvatar hero={hero} size="sm" />自己</button><button type="button" className={target === 'lowestHpAlly' ? 'active' : ''} onClick={() => setTarget('lowestHpAlly')}><Activity size={16} />生命最低队友</button>
                </div>
                {bossMode === 'hydra' && target === 'hydraHeadPriority' && <div className="head-priority-builder">
                  <div className="head-priority-heading"><span><strong>完整蛇头类型库 · {hydraHeads.length} 种</strong><small>来自游戏静态目录，不受本轮四个在场蛇头限制；点击加入优先级。</small></span><em>{headPriorityIds.length ? `已排列 ${headPriorityIds.length} 种` : '尚未指定，直接使用安全兜底'}</em></div>
                  <div className="head-type-library">{hydraHeads.map((head) => { const rank = headPriorityIds.indexOf(head.typeId); return <button type="button" key={head.typeId} className={rank >= 0 ? 'head-type-option active' : 'head-type-option'} onClick={() => { setTarget('hydraHeadPriority'); if (rank < 0) setHeadPriorityIds((current) => [...current, head.typeId]) }}><HydraHeadIcon head={head} size="md" /><span><strong>{hydraHeadDisplayName(head)}</strong><small>{rank >= 0 ? `优先级 ${rank + 1}` : '加入优先目标'}</small></span>{rank >= 0 && <em>{rank + 1}</em>}</button> })}</div>
                  {headPriorityIds.length > 0 && <div className="head-priority-list">{headPriorityIds.map((typeId, index) => { const head = hydraHeads.find((item) => item.typeId === typeId); return <div className="head-priority-row" key={typeId}><span className="skill-rank">{index + 1}</span><HydraHeadIcon head={head ?? { typeId, name: `蛇头 ${typeId}` }} size="sm" /><span><strong>{head ? hydraHeadDisplayName(head) : `蛇头 ${typeId}`}</strong><small>不存在、已死亡或不是合法目标时自动跳过</small></span><div><button type="button" title="提高优先级" disabled={index === 0} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title="降低优先级" disabled={index === headPriorityIds.length - 1} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" title="移除" onClick={() => setHeadPriorityIds((current) => current.filter((value) => value !== typeId))}><Trash2 size={15} /></button></div></div> })}</div>}
                  <div className="head-priority-fallback"><ShieldCheck size={17} /><span><strong>宽松安全兜底</strong><small>优先类型都不在场时，选择当前技能可攻击且生命最低的蛇头；绝不会按数组顺序或场上位置猜目标。</small></span></div>
                </div>}
                <div className="ally-target-heading"><Users size={15} /><span><strong>队友目标</strong><small>以下位置只属于准备队伍，不是蛇头站位。</small></span></div>
                <div className={`position-targets position-targets-${teamSize}`}>{Array.from({ length: teamSize }, (_, index) => { const member = teamHeroes[index]; const position = index + 1; return <button type="button" key={position} className={target === 'allyPosition' && targetPosition === position ? 'position-target active' : 'position-target'} onClick={() => { setTarget('allyPosition'); setTargetPosition(position) }}><span className="position-number">{position}</span><HeroAvatar hero={member} size="md" /><span><strong>{member?.name ?? '未读取'}</strong><small>{position} 号位</small></span></button> })}</div>
              </fieldset>}
              <div className="skill-detail span-2"><SkillIcon hero={hero} skill={selectedSkill} slot={slot} /><span><strong>{selectedSkill?.name || `技能 ${slot}`}{selectedSkill?.isTransform ? ' · 形态切换技能' : ''}</strong><em>{cleanText(selectedSkill?.description) || '完整技能说明会在游戏读取后自动缓存'}</em><small>{selectedSkill?.isTransform ? `当前形态技能冷却：${selectedSkill.defaultCooldown ?? '待读取'} 回合；只有就绪时才会执行` : (selectedSkill?.effectSummary || '尚未学习到该技能的效果记录')}</small></span></div>
            </>}
            <fieldset className="condition-builder span-2">
              <legend>常用触发条件</legend>
              <div className="condition-grid">
                {bossMode === 'chimera' && <label className="field"><span>Boss 回合 ≥</span><input type="text" inputMode="numeric" value={turnMin} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && setTurnMin(event.target.value)} placeholder="不限" /></label>}
                {bossMode === 'chimera' && <label className="field"><span>Boss 回合 ≤</span><input type="text" inputMode="numeric" value={turnMax} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && setTurnMax(event.target.value)} placeholder="不限" /></label>}
                {bossMode === 'chimera' && <label className="field"><span>距切换形态 ≤</span><input type="text" inputMode="numeric" value={switchWithin} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && Number(event.target.value || 0) <= 5 && setSwitchWithin(event.target.value)} placeholder="不限" /></label>}
                {bossMode === 'chimera' && <label className="field"><span>下一形态</span><select value={nextForm} onChange={(event) => setNextForm(event.target.value)}><option value="">不限</option>{ALL_FORMS.map((form) => <option key={form} value={form}>{FORM_LABEL[form]}</option>)}</select></label>}
                <label className="field"><span>当前伤害 ≥（M）</span><input type="text" inputMode="decimal" value={damageMin} onFocus={selectNumericInput} onChange={(event) => /^\d*(?:\.\d*)?$/.test(event.target.value) && setDamageMin(event.target.value)} placeholder="例如 150" /></label>
              </div>
            </fieldset>
            <fieldset className="cooldown-condition-builder span-2">
              <legend>技能冷却条件 <small>读取准备队伍中具体英雄的技能剩余冷却回合</small></legend>
              <div className="effect-condition-heading"><span><strong>{skillCooldownConditions.length ? `已添加 ${skillCooldownConditions.length} 条` : '按需添加'}</strong><small>{skillCooldownConditionsMode === 'any' ? '任意一条冷却条件满足即可。' : '所有冷却条件都必须同时满足。'}</small></span><div className="condition-heading-actions"><label className="condition-logic-select"><span>组合</span><select value={skillCooldownConditionsMode} onChange={(event) => setSkillCooldownConditionsMode(event.target.value === 'any' ? 'any' : 'all')}><option value="all">全部满足（并且）</option><option value="any">任意满足（或者）</option></select></label><button type="button" className="button ghost" onClick={() => { const heroTypeId = team.slice(0, teamSize).find((value) => value > 0) ?? 0; const conditionHero = heroByRuntimeId(heroes, heroTypeId); const conditionSkill = conditionHero?.skills.find((skill) => typeof skill.typeId === 'number'); setSkillCooldownConditions((current) => [...current, createSkillCooldownCondition({ heroTypeId: heroTypeId ? String(heroTypeId) : '', skillTypeId: conditionSkill?.typeId ? String(conditionSkill.typeId) : '' })]) }}><Plus size={15} />添加冷却条件</button></div></div>
              {skillCooldownConditions.length ? <div className="cooldown-condition-list">{skillCooldownConditions.map((condition, index) => {
                const selectedHeroTypeId = Number(condition.heroTypeId)
                const selectedHero = heroByRuntimeId(heroes, selectedHeroTypeId)
                const selectedCooldownSkill = selectedHero?.skills.find((skill) => skill.typeId === Number(condition.skillTypeId))
                const updateCondition = (changes: Partial<SkillCooldownConditionValue>) => setSkillCooldownConditions((current) => current.map((item) => item.id === condition.id ? { ...item, ...changes } : item))
                return <div className="cooldown-condition-row" key={condition.id}>
                  <span className="effect-condition-index">{String(index + 1).padStart(2, '0')}</span>
                  <HeroAvatar hero={selectedHero} size="sm" />
                  <label className="field"><span>具体英雄</span><select value={condition.heroTypeId} onChange={(event) => { const nextHeroTypeId = Number(event.target.value); const nextHero = heroByRuntimeId(heroes, nextHeroTypeId); const nextSkill = nextHero?.skills.find((skill) => typeof skill.typeId === 'number'); updateCondition({ heroTypeId: event.target.value, skillTypeId: nextSkill?.typeId ? String(nextSkill.typeId) : '' }) }}>{team.slice(0, teamSize).map((typeId, teamIndex) => { const teamHero = heroByRuntimeId(heroes, typeId); return typeId > 0 ? <option key={`${typeId}-${teamIndex}`} value={typeId}>{teamIndex + 1}. {teamHero?.name ?? `英雄 ${typeId}`}</option> : null })}</select></label>
                  <SkillIcon hero={selectedHero} skill={selectedCooldownSkill} slot={selectedCooldownSkill?.slot} />
                  <label className="field"><span>具体技能</span><select value={condition.skillTypeId} onChange={(event) => updateCondition({ skillTypeId: event.target.value })}>{(selectedHero?.skills ?? []).filter((skill) => typeof skill.typeId === 'number').map((skill) => <option key={skill.typeId} value={skill.typeId}>{skill.name || `技能 ${skill.slot}`}</option>)}</select></label>
                  <label className="effect-turn-field"><span>冷却 ≥</span><input type="text" inputMode="numeric" value={condition.turnsAtLeast} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtLeast: event.target.value })} placeholder="不限" /></label>
                  <label className="effect-turn-field"><span>冷却 ≤</span><input type="text" inputMode="numeric" value={condition.turnsAtMost} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtMost: event.target.value })} placeholder="不限" /></label>
                  <button type="button" className="effect-condition-delete" aria-label={`删除技能冷却条件 ${index + 1}`} onClick={() => setSkillCooldownConditions((current) => current.filter((item) => item.id !== condition.id))}><Trash2 size={15} /></button>
                </div>
              })}</div> : <div className="effect-condition-empty"><Activity size={22} /><span><strong>没有技能冷却限制</strong><small>当前规则不会读取其他英雄的技能冷却；需要联动时添加一条即可。</small></span></div>}
            </fieldset>
            <fieldset className="effect-condition-builder span-2">
              <legend>效果条件 <small>目标、已有或缺少、剩余回合统一在一条条件中设置</small></legend>
              <div className="effect-condition-heading"><span><strong>{effectConditions.length ? `已添加 ${effectConditions.length} 条` : '按需添加'}</strong><small>{effectConditionsMode === 'any' ? '下面任意一条效果条件满足即可。' : '下面所有效果条件都必须同时满足。'}</small></span><div className="condition-heading-actions"><label className="condition-logic-select"><span>组合</span><select value={effectConditionsMode} onChange={(event) => setEffectConditionsMode(event.target.value === 'any' ? 'any' : 'all')}><option value="all">全部满足（并且）</option><option value="any">任意满足（或者）</option></select></label><button type="button" className="button ghost" onClick={() => setEffectConditions((current) => [...current, createEffectCondition({ target: bossMode === 'hydra' ? 'bossPriority' : 'boss' })])}><Plus size={15} />添加效果条件</button></div></div>
              {effectConditions.length ? <div className="effect-condition-list">
                {effectConditions.map((condition, index) => {
                  const selectedHeroTypeId = Number(condition.heroTypeId)
                  const selectedTeamHero = heroByRuntimeId(heroes, selectedHeroTypeId)
                  const savedHeroOutsideTeam = condition.target === 'ally' && selectedHeroTypeId > 0 && !team.slice(0, teamSize).includes(selectedHeroTypeId)
                  const updateCondition = (changes: Partial<EffectConditionValue>) => setEffectConditions((current) => current.map((item) => item.id === condition.id ? { ...item, ...changes } : item))
                  return <div className="effect-condition-row" key={condition.id}>
                    <span className="effect-condition-index">{String(index + 1).padStart(2, '0')}</span>
                    <div className="effect-condition-target">
                      {condition.target === 'ally' ? <HeroAvatar hero={selectedTeamHero} size="sm" /> : <span className="effect-target-boss"><Crosshair size={16} /></span>}
                      <span><select aria-label={`效果条件 ${index + 1} 的目标`} value={condition.target === 'ally' ? `ally:${condition.heroTypeId}` : condition.target} onChange={(event) => { const [targetType, rawHeroTypeId = ''] = event.target.value.split(':'); const bossTarget = targetType === 'bossAll' || targetType === 'bossAny' || targetType === 'bossPriority' ? targetType : 'boss'; updateCondition({ target: targetType === 'ally' ? 'ally' : bossTarget, heroTypeId: targetType === 'ally' ? rawHeroTypeId : '' }) }}>{bossMode === 'hydra' ? <><option value="bossPriority">规则优先目标蛇头</option><option value="bossAny">任一在场蛇头</option><option value="bossAll">全部在场蛇头</option></> : <option value="boss">奇美拉 Boss</option>}{savedHeroOutsideTeam && <option value={`ally:${condition.heroTypeId}`}>已保存英雄 {condition.heroTypeId}</option>}{team.slice(0, teamSize).map((typeId, teamIndex) => { const teamHero = heroByRuntimeId(heroes, typeId); return typeId > 0 ? <option key={`${typeId}-${teamIndex}`} value={`ally:${typeId}`}>{teamIndex + 1}. {teamHero?.name ?? `英雄 ${typeId}`}</option> : null })}</select><small>{condition.target === 'ally' ? selectedTeamHero?.name ?? '准备队伍英雄' : condition.target === 'bossAll' ? '四个当前蛇头必须全部满足' : condition.target === 'bossAny' ? '任意一个当前蛇头满足即可' : bossMode === 'hydra' ? '按本规则技能目标优先级选中的蛇头' : '当前 Boss'}</small></span>
                    </div>
                    <div className="effect-presence-toggle" role="group" aria-label={`效果条件 ${index + 1} 的状态`}><button type="button" className={condition.presence === 'has' ? 'active' : ''} onClick={() => updateCondition({ presence: 'has' })}>必须已有</button><button type="button" className={condition.presence === 'missing' ? 'active missing' : ''} onClick={() => updateCondition({ presence: 'missing', turnsAtLeast: '', turnsAtMost: '' })}>必须缺少</button></div>
                    <EffectPicker effects={effects} value={condition.token} onValue={(token) => updateCondition({ token })} />
                    <label className="effect-turn-field"><span>剩余 ≥</span><input type="text" inputMode="numeric" value={condition.turnsAtLeast} disabled={!condition.token || condition.presence === 'missing'} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtLeast: event.target.value })} placeholder={condition.presence === 'missing' ? '不适用' : '不限'} /></label>
                    <label className="effect-turn-field"><span>剩余 ≤</span><input type="text" inputMode="numeric" value={condition.turnsAtMost} disabled={!condition.token || condition.presence === 'missing'} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtMost: event.target.value })} placeholder={condition.presence === 'missing' ? '不适用' : '不限'} /></label>
                    <button type="button" className="effect-condition-delete" aria-label={`删除效果条件 ${index + 1}`} onClick={() => setEffectConditions((current) => current.filter((item) => item.id !== condition.id))}><Trash2 size={15} /></button>
                  </div>
                })}
              </div> : <div className="effect-condition-empty"><ShieldCheck size={22} /><span><strong>没有效果限制</strong><small>当前规则不会检查任何增益或减益；需要时添加一条即可。</small></span></div>}
            </fieldset>
            <details className="advanced-conditions span-2"><summary>完整条件数据 <small>试炼进度、队友状态与效果剩余回合等高级条件</small></summary><label className="field"><textarea rows={7} value={advanced} onChange={(event) => setAdvanced(event.target.value)} spellCheck={false} /></label></details>
            </>}
          </div>
          {error && <div className="inline-error">{error}</div>}
          <div className="dialog-footer"><span>{ruleKind === 'default' ? '默认规则只在没有严格规则可执行时接管。' : '英雄与行动条件必须满足；各条件组按所选“并且/或者”计算。'}</span><div><Dialog.Close className="button ghost">取消</Dialog.Close><button className="button primary" onClick={commit}>保存规则</button></div></div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function App() {
  const [language, setLanguage] = useState<UiLanguage>(getInitialLanguage)
  const [bossMode, setBossMode] = useState<BossMode>('chimera')
  const [data, setData] = useState<Bootstrap | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedPid, setSelectedPid] = useState<number | undefined>()
  const [config, setConfig] = useState<Strategy>({})
  const [activeStrategyId, setActiveStrategyId] = useState('default')
  const [strategyProfiles, setStrategyProfiles] = useState<StrategyProfile[]>([])
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileDialog, setProfileDialog] = useState<'create' | 'rename' | null>(null)
  const [profileName, setProfileName] = useState('')
  const [live, setLive] = useState<LiveState>({})
  const [controller, setController] = useState<ControllerState>({ running: false, status: '已停止', logs: [] })
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [showLogs, setShowLogs] = useState(false)
  const [logsExpanded, setLogsExpanded] = useState(false)
  const compactLogRef = useRef<HTMLPreElement | null>(null)
  const expandedLogRef = useRef<HTMLPreElement | null>(null)
  const [trialOpen, setTrialOpen] = useState(false)
  const [ruleOpen, setRuleOpen] = useState(false)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [trialSearchDifficulty, setTrialSearchDifficulty] = useState<number>(5)

  useLayoutEffect(() => {
    saveLanguage(language)
    document.title = language === 'en' ? 'Alliance Boss Strategy Studio' : '联盟 Boss 策略中心'
    const root = document.body
    return root ? installDocumentLocalization(root, language) : undefined
  }, [language])

  function changeLanguage(next: UiLanguage) {
    saveLanguage(next)
    setLanguage(next)
  }

  useEffect(() => {
    if (!token) return
    const session = new EventSource(`/api/session?token=${encodeURIComponent(token)}`)
    return () => session.close()
  }, [])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      if (showLogs && compactLogRef.current) {
        compactLogRef.current.scrollTop = compactLogRef.current.scrollHeight
      }
      if (logsExpanded && expandedLogRef.current) {
        expandedLogRef.current.scrollTop = expandedLogRef.current.scrollHeight
      }
    })
    return () => window.cancelAnimationFrame(frame)
  }, [controller.logs.length, controller.logs[controller.logs.length - 1], showLogs, logsExpanded])

  const load = useCallback(async (pid?: number, quiet = false, requestedMode?: BossMode) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const mode = requestedMode ?? 'chimera'
      const params = new URLSearchParams({ mode })
      if (pid) params.set('pid', String(pid))
      const next = await api<Bootstrap>(`/api/bootstrap?${params}`)
      setData(next)
      setBossMode(next.bossMode)
      setConfig(next.config)
      setActiveStrategyId(next.activeStrategyId ?? 'default')
      setStrategyProfiles(next.strategyProfiles ?? [])
      setController(next.controller)
      setLive(next.state ?? {})
      const resolvedPid = pid ?? next.selectedPid ?? next.processes[0]?.pid
      setSelectedPid(resolvedPid)
      const inferredDifficulty = Number(String(next.config.objectives?.mandatoryTrialIds?.[0] ?? '8000501').slice(4, 6))
      if (inferredDifficulty >= 1 && inferredDifficulty <= 6) setTrialSearchDifficulty(inferredDifficulty)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!selectedPid || loading) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api<{ state: LiveState; controller: ControllerState; heroes?: Hero[]; hydraHeads?: HydraHead[]; effects?: EffectOption[]; difficulties?: Difficulty[] }>(`/api/state?pid=${selectedPid}&mode=${bossMode}`)
        setLive(next.state)
        setController(next.controller)
        if (next.heroes || next.hydraHeads || next.effects || next.difficulties) {
          setData((current) => current ? {
            ...current,
            heroes: next.heroes ?? current.heroes,
            hydraHeads: next.hydraHeads ?? current.hydraHeads,
            effects: next.effects ?? current.effects,
            difficulties: next.difficulties ?? current.difficulties,
          } : current)
        }
      } catch { /* the next successful poll restores the status */ }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [selectedPid, loading, bossMode])

  const heroes = data?.heroes ?? []
  const hydraHeads = data?.hydraHeads ?? []
  const effects = data?.effects ?? []
  const rules = config.rules ?? []
  const objectives = config.objectives ?? {}
  const difficulty = data?.difficulties.find((item) => item.difficultyId === trialSearchDifficulty)
  const trials = difficulty?.trials ?? []
  const selectedTrials = objectives.mandatoryTrialIds ?? []
  const selectedProcess = data?.processes.find((process) => process.pid === selectedPid)
  const team = (live.teamHeroIds?.length ? live.teamHeroIds : config.team?.heroTypeIds ?? config.team?.heroIds) ?? []
  const activeModeSpec = data?.modes.find((mode) => mode.id === bossMode)
  const teamSize = activeModeSpec?.teamSize ?? (bossMode === 'hydra' ? 6 : 5)
  const selectedStrategyProfile = strategyProfiles.find((profile) => profile.id === activeStrategyId)
  const selectedStrategyName = selectedStrategyProfile?.name === '默认策略'
    ? (language === 'en' ? 'Default Strategy' : '默认策略')
    : selectedStrategyProfile?.name || config.name || (language === 'en' ? 'Default Strategy' : '默认策略')
  const savedStrategyTeam = selectedStrategyProfile?.teamHeroIds?.length
    ? selectedStrategyProfile.teamHeroIds
    : config.team?.heroTypeIds ?? config.team?.heroIds ?? []

  const trialCatalogById = useMemo(() => new Map(
    (data?.difficulties ?? []).flatMap((item) => item.trials).map((trial) => [trial.id, trial]),
  ), [data?.difficulties])

  const completedTrials = useMemo(() => {
    const ids = new Set<number>(live.completedTrialIds ?? [])
    for (const trial of live.trials ?? []) if (trial.completed) ids.add(trial.id)
    return [...ids].map((id) => trialCatalogById.get(id) ?? { id, description: `试炼 ${id}` })
  }, [live.completedTrialIds, live.trials, trialCatalogById])

  const selectedTrialDescriptions = useMemo(
    () => trials.filter((trial) => selectedTrials.includes(trial.id)).map((trial) => cleanText(trial.description)),
    [trials, selectedTrials],
  )

  function updateObjective(key: string, value: number | number[]) {
    setConfig((current) => ({
      ...current,
      objectives: { ...(current.objectives ?? {}), [key]: value },
    }))
  }

  function applyStrategyBundle(result: StrategyBundle) {
    setConfig(result.config)
    setActiveStrategyId(result.activeStrategyId)
    setStrategyProfiles(result.strategyProfiles)
    const inferredDifficulty = Number(String(result.config.objectives?.mandatoryTrialIds?.[0] ?? '8000501').slice(4, 6))
    if (inferredDifficulty >= 1 && inferredDifficulty <= 6) setTrialSearchDifficulty(inferredDifficulty)
    setEditIndex(null)
    setRuleOpen(false)
  }

  function configForSave(capturePreparedTeam: boolean): Strategy {
    const liveTeam = live.teamHeroIds ?? []
    if (!capturePreparedTeam || liveTeam.length !== teamSize || new Set(liveTeam).size !== teamSize || liveTeam.some((value) => value <= 0)) {
      return config
    }
    const liveInstances = live.teamHeroInstanceIds ?? []
    return {
      ...config,
      team: {
        heroTypeIds: [...liveTeam],
        ...(liveInstances.length === teamSize && new Set(liveInstances).size === teamSize
          ? { heroInstanceIds: [...liveInstances] }
          : {}),
      },
    }
  }

  async function save(showMessage = true, capturePreparedTeam = true) {
    setError('')
    try {
      const result = await api<StrategyBundle>('/api/config', { method: 'POST', body: JSON.stringify({ bossMode, strategyId: activeStrategyId, config: configForSave(capturePreparedTeam) }) })
      applyStrategyBundle(result)
      if (showMessage) setNotice(result.message ?? '策略组已保存')
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return false
    }
  }

  function openProfileNameDialog(kind: 'create' | 'rename') {
    const currentName = selectedStrategyName
    setProfileName(kind === 'create'
      ? (language === 'en' ? `Copy of ${currentName}` : `${currentName} 副本`)
      : currentName)
    setProfileDialog(kind)
  }

  async function commitProfileName() {
    const name = profileName.trim()
    if (!name || profileBusy) return
    setProfileBusy(true)
    setError('')
    try {
      if (profileDialog === 'rename' && !(await save(false, false))) return
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({
          action: profileDialog,
          bossMode,
          strategyId: activeStrategyId,
          name,
          ...(profileDialog === 'create' ? { config: configForSave(true) } : {}),
        }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? (profileDialog === 'create' ? '新策略组已创建' : '策略组已重命名'))
      setProfileDialog(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function selectStrategyProfile(strategyId: string) {
    if (strategyId === activeStrategyId || profileBusy || controller.running) return
    setProfileBusy(true)
    setError('')
    try {
      if (!(await save(false, false))) return
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'select', bossMode, strategyId }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? '已切换策略组')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function deleteStrategyProfile() {
    if (strategyProfiles.length <= 1 || profileBusy || controller.running) return
    const profileLabel = selectedStrategyProfile?.name || config.name || '当前策略组'
    const confirmed = window.confirm(language === 'en'
      ? `Delete strategy group “${profileLabel}”? This cannot be undone.`
      : `确定删除策略组“${profileLabel}”吗？此操作无法撤销。`)
    if (!confirmed) return
    setProfileBusy(true)
    setError('')
    try {
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'delete', bossMode, strategyId: activeStrategyId }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? '策略组已删除')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function start() {
    if (!selectedPid) return setError('请选择一个已识别的游戏内账户')
    // Starting must preserve this profile's saved team so the controller can
    // select it even when another team is currently shown in the game.
    if (!(await save(false, false))) return
    setError('')
    setController((current) => ({ ...current, status: '正在准备代理…' }))
    try {
      const next = await api<{ controller: ControllerState }>('/api/start', { method: 'POST', body: JSON.stringify({ pid: selectedPid, bossMode }) })
      setController(next.controller)
      setNotice('策略接管已启动')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function stop() {
    try {
      const next = await api<{ controller: ControllerState }>('/api/stop', { method: 'POST', body: JSON.stringify({ pid: selectedPid, bossMode }) })
      setController(next.controller)
      setNotice('已请求暂停接管')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function clearLogs() {
    try {
      const next = await api<{ controller: ControllerState }>('/api/logs/clear', { method: 'POST', body: JSON.stringify({ bossMode }) })
      setController(next.controller)
      setNotice(`已清空${activeModeSpec?.label ?? '当前模式'}运行记录`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function refreshAccounts() {
    setRefreshing(true)
    await load(selectedPid, false, bossMode)
    setRefreshing(false)
  }

  async function switchBossMode(nextMode: BossMode) {
    if (nextMode === bossMode || controller.running) return
    setNotice('')
    setError('')
    setBossMode(nextMode)
    await load(selectedPid, false, nextMode)
  }

  function saveRule(rule: Rule) {
    setConfig((current) => {
      const next = [...(current.rules ?? [])]
      if (editIndex === null) next.push(rule)
      else next[editIndex] = rule
      return { ...current, rules: next }
    })
    setEditIndex(null)
  }

  function moveRule(index: number, direction: number) {
    const target = index + direction
    if (target < 0 || target >= rules.length) return
    setConfig((current) => {
      const next = [...(current.rules ?? [])]
      ;[next[index], next[target]] = [next[target], next[index]]
      return { ...current, rules: next }
    })
  }

  if (loading && !data) {
    return <main className="loading-screen"><div className="language-switcher loading-language-switcher" data-i18n-skip role="group" aria-label="Language / 语言"><Languages size={18} /><span>Language</span><button type="button" className={language === 'en' ? 'active' : ''} onClick={() => changeLanguage('en')}>English</button><button type="button" className={language === 'zh-CN' ? 'active' : ''} onClick={() => changeLanguage('zh-CN')}>中文</button></div><span className="brand-mark"><Swords /></span><h1>正在建立联盟 Boss 资源缓存</h1><p>读取游戏内账户、英雄、技能与模式资料…</p><span className="loader" /></main>
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark"><Swords size={23} /></span><span><strong>联盟 Boss 策略中心</strong><small>Raid Boss Strategy Studio</small></span></div>
        <div className="account-picker">
          <span className={`status-dot ${selectedProcess?.accountName ? 'online' : ''}`} />
          <select value={selectedPid ?? ''} onChange={(event) => { const pid = Number(event.target.value); setSelectedPid(pid); void load(pid, true, bossMode) }} disabled={controller.running}>
            {!data?.processes.length && <option value="">没有找到 Raid 账户</option>}
            {data?.processes.map((process) => <option key={process.pid} value={process.pid}>{process.label}</option>)}
          </select>
          <button className="icon-button" onClick={() => void refreshAccounts()} disabled={refreshing || controller.running} title="刷新账户"><RefreshCw size={18} className={refreshing ? 'spin' : ''} /></button>
        </div>
        <div className="topbar-actions">
          <div className="live-badge"><Activity size={16} /><span>{live.statusLabel || controller.status || '等待状态'}</span></div>
          <div className="language-switcher" data-i18n-skip role="group" aria-label="Language / 语言">
            <Languages size={18} />
            <span>Language</span>
            <button type="button" className={language === 'en' ? 'active' : ''} onClick={() => changeLanguage('en')}>English</button>
            <button type="button" className={language === 'zh-CN' ? 'active' : ''} onClick={() => changeLanguage('zh-CN')}>中文</button>
          </div>
        </div>
      </header>

      <nav className="mode-switcher" aria-label="联盟 Boss 模式">
        {(data?.modes ?? []).map((mode) => (
          <button key={mode.id} type="button" className={bossMode === mode.id ? 'active' : ''} disabled={controller.running} onClick={() => void switchBossMode(mode.id)}>
            <span className="mode-icon">{mode.id === 'chimera' ? <Swords size={21} /> : <Waves size={21} />}</span>
            <span><strong>{mode.label}</strong><small>{mode.id === 'chimera' ? '形态轮换 · 试炼与奖励' : '四头在场 · 吞噬与斩首'}</small></span>
            {mode.id === 'hydra' && mode.status !== 'ready' && !(bossMode === 'hydra' && live.modeReady) && <em>待实战标定</em>}
          </button>
        ))}
      </nav>

      {(error || notice) && <div className={`toast ${error ? 'error' : ''}`}><span>{error || notice}</span><button onClick={() => { setError(''); setNotice('') }}><X size={16} /></button></div>}
      {!error && selectedProcess?.error && <div className="toast error"><span>账户读取失败：{selectedProcess.error}</span></div>}

      <main className="workspace">
        <aside className="control-column">
          <section className="card team-card">
            <div className="section-heading"><span><Users size={18} />当前队伍</span><em>{team.length}/{teamSize}</em></div>
            <div className={`team-row team-${teamSize}`}>
              {Array.from({ length: teamSize }, (_, index) => {
                const hero = heroByRuntimeId(heroes, team[index])
                return <div className="team-member" key={index}><HeroAvatar hero={hero} size="lg" /><small>{hero?.name ?? '待读取'}</small></div>
              })}
            </div>
            <div className="catalog-ready"><Database size={14} /><span>英雄库已就绪</span><strong>{heroes.length} 名英雄</strong></div>
          </section>

          <section className="card objectives-card">
            <div className="section-heading"><span><Crosshair size={18} />战斗目标</span><em>自动追踪</em></div>
            <div className="two-fields">
              {bossMode === 'chimera' && <label className="field"><span>Boss 难度</span><select value={trialSearchDifficulty} onChange={(event) => { setTrialSearchDifficulty(Number(event.target.value)); updateObjective('mandatoryTrialIds', []) }}>{data?.difficulties.map((item) => <option key={item.difficultyId} value={item.difficultyId}>{BOSS_DIFFICULTY[item.difficulty] ?? item.difficulty}</option>)}</select></label>}
              <label className="field"><span>最多免费重整</span><NumericInput value={objectives.maxRegroupRetries ?? 10} onValue={(value) => updateObjective('maxRegroupRetries', value)} /></label>
              <label className="field"><span>最低伤害（M）</span><NumericInput decimal value={damageInMillions(objectives.minimumDamage)} onValue={(value) => updateObjective('minimumDamage', value * 1_000_000)} placeholder="例如 300" /></label>
            </div>
            {bossMode === 'chimera' && <button className={`trial-trigger ${selectedTrials.length ? 'has-selection' : ''}`} onClick={() => setTrialOpen(true)}>
              <span className="trial-trigger-icon"><BookOpenCheck size={21} /></span>
              <span><small>必做试炼</small><strong>{selectedTrials.length ? `已选择 ${selectedTrials.length} 项` : '点击选择试炼'}</strong><em>{selectedTrialDescriptions[0] || '按当前难度读取完整试炼内容'}</em></span>
              <ChevronDown size={18} />
            </button>}
            <p className="behavior-note"><ShieldCheck size={16} />{bossMode === 'chimera' ? '必做试炼已不可能完成时免费重整；全部目标达成后停在结算页，不自动保留结果。' : '优先解救被吞噬英雄并利用暴露蛇颈；达到最低伤害后停在结算页，不自动保留结果。'}</p>
            {bossMode === 'hydra' && !live.modeReady && <p className="mode-calibration"><Waves size={16} /><span><strong>等待首次实战标定</strong>进入六头蛇准备界面或手动战斗后，工具会读取区域、四个蛇头和六人队伍；读取成功前不会执行任何操作。</span></p>}
          </section>

          <section className="run-card">
            <div className="run-status"><span className={controller.running ? 'pulse' : ''}><Bot size={19} /></span><span><small>接管状态</small><strong>{controller.status}</strong></span></div>
            <div className="run-actions">
              <button className="button pause" disabled={!controller.running} onClick={() => void stop()}><CirclePause size={19} />暂停</button>
              <button className="button start" disabled={controller.running || !selectedProcess?.accountName || (bossMode === 'hydra' && !live.modeReady)} onClick={() => void start()}><CirclePlay size={19} />开始执行</button>
            </div>
            <small className="run-hint">只有这里或游戏内暂停会中断接管</small>
          </section>
        </aside>

        <section className="strategy-column">
          <section className="card strategy-profile-card">
            <div className="strategy-profile-identity">
              <span className="strategy-profile-icon"><Layers3 size={21} /></span>
              <span><small>当前策略组</small><strong data-i18n-skip>{selectedStrategyName}</strong></span>
            </div>
            <label className="strategy-profile-select">
              <span>切换策略组</span>
              <select data-i18n-skip value={activeStrategyId} disabled={controller.running || profileBusy} onChange={(event) => void selectStrategyProfile(event.target.value)}>
                {strategyProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name === '默认策略' ? (language === 'en' ? 'Default Strategy' : '默认策略') : profile.name} · {profile.ruleCount} {language === 'en' ? 'rules' : '条规则'}</option>)}
              </select>
            </label>
            <div className="strategy-profile-team">
              <span><small>已保存队伍</small><strong>{savedStrategyTeam.length === teamSize ? `${savedStrategyTeam.length}/${teamSize}` : '等待保存'}</strong></span>
              <div>{savedStrategyTeam.slice(0, teamSize).map((typeId, index) => <HeroAvatar key={`${typeId}-${index}`} hero={heroByRuntimeId(heroes, typeId)} size="sm" />)}</div>
              {savedStrategyTeam.length !== teamSize && <em>点击保存时记录当前准备队伍</em>}
            </div>
            <div className="strategy-profile-actions">
              <button className="button ghost" disabled={controller.running || profileBusy} onClick={() => openProfileNameDialog('create')}><Copy size={16} />创建副本</button>
              <button className="icon-button" title="重命名策略组" disabled={controller.running || profileBusy} onClick={() => openProfileNameDialog('rename')}><Edit3 size={16} /></button>
              <button className="icon-button danger" title="删除策略组" disabled={controller.running || profileBusy || strategyProfiles.length <= 1} onClick={() => void deleteStrategyProfile()}><Trash2 size={16} /></button>
            </div>
          </section>

          <div className="overview-grid">
            <Metric label="当前伤害" value={formatNumber(live.damage)} icon={<Swords size={18} />} />
            <Metric label="当前积分" value={formatNumber(live.points)} icon={<Gauge size={18} />} />
            <Metric label="Boss 回合" value={String(bossMode === 'chimera' ? live.chimeraTurn ?? 0 : live.hydraTurn ?? 0)} icon={<Activity size={18} />} />
            <Metric label={bossMode === 'chimera' ? '已完成试炼' : '当前目标'} value={bossMode === 'chimera' ? `${completedTrials.length}` : `${live.headCount ?? 0} 个蛇头`} icon={<Check size={20} />} />
          </div>

          {bossMode === 'chimera' ? <section className="card completed-trials-card">
            <div className="completed-trials-heading">
              <span><BookOpenCheck size={20} /><strong>本次已完成试炼与奖励</strong></span>
              <em>{completedTrials.length ? `已完成 ${completedTrials.length} 项` : '等待战斗进度'}</em>
            </div>
            {completedTrials.length ? (
              <div className="completed-trials-list">
                {completedTrials.map((trial) => (
                  <article className="completed-trial" key={trial.id}>
                    <span className="completed-check"><Check size={17} /></span>
                    <span className="completed-trial-copy">
                      <span className="trial-tags"><em>{FORM_LABEL[trial.form ?? ''] ?? trial.form ?? '奇美拉'}</em><em>{TRIAL_LEVEL[trial.difficulty ?? ''] ?? trial.difficulty ?? '试炼'}</em></span>
                      <strong>{cleanText(trial.description) || '已完成试炼'}</strong>
                    </span>
                    <TrialRewards reward={trial.reward} compact />
                  </article>
                ))}
              </div>
            ) : <div className="completed-trials-empty"><Check size={17} /><span>战斗中完成试炼后，这里会立即显示试炼内容、当前轮换奖励和奖励图标。</span></div>}
          </section> : <section className="card completed-trials-card hydra-summary">
            <div className="completed-trials-heading"><span><Waves size={20} /><strong>六头蛇战斗重点</strong></span><em>{live.modeReady ? '状态已连接' : '等待六头蛇状态'}</em></div>
            {(live.heads?.length ?? 0) > 0 ? <div className="live-hydra-heads">{live.heads?.map((liveHead, index) => {
              const identity = liveHead.canonicalTypeId ?? liveHead.typeId
              const catalogHead = hydraHeads.find((head) => head.typeId === identity)
              const head = { ...catalogHead, ...liveHead, canonicalTypeId: identity, name: catalogHead?.name || liveHead.name || `蛇头 ${identity}` }
              const stateLabel = head.dead ? '已死亡' : head.isHydraNeck || head.headState === 'exposed_neck' ? '暴露蛇颈' : head.isDevouring ? '正在吞噬' : '可作为目标'
              return <article className={`live-hydra-head${head.dead ? ' dead' : ''}`} key={`${head.id ?? head.typeId}-${index}`}><HydraHeadIcon head={head} size="lg" /><span><strong>{hydraHeadDisplayName(head)}</strong><small>{stateLabel}</small></span></article>
            })}</div> : <div className="hydra-head-catalog">{hydraHeads.map((head) => <span key={head.typeId}><HydraHeadIcon head={head} size="md" /><small>{hydraHeadDisplayName(head)}</small></span>)}</div>}
            <div className="hydra-safe-target-note"><ShieldCheck size={16} /><span><strong>按蛇头身份选择，不按站位</strong><small>每次行动都会重新识别在场蛇头；优先目标未出现、死亡或不可攻击时自动跳过并使用生命最低的合法蛇头。</small></span></div>
          </section>}

          <section className="card rules-card">
            <div className="rules-header">
              <div><span className="eyebrow"><Sparkles size={14} />策略树</span><h2>行动规则</h2><p>严格规则始终先执行；全部严格规则都不可执行时，才使用英雄的默认技能顺序。</p></div>
              <div className="toolbar"><button className="button ghost" onClick={() => void save()}><Save size={17} />保存</button><button className="button primary" onClick={() => { setEditIndex(null); setRuleOpen(true) }}><Plus size={17} />添加规则</button></div>
            </div>
            <div className="rules-list">
              {!rules.length && <div className="empty-state"><Database size={34} /><strong>还没有策略规则</strong><span>添加第一条规则后才能开始执行。</span><button className="button primary" onClick={() => { setEditIndex(null); setRuleOpen(true) }}><Plus size={17} />添加规则</button></div>}
              {rules.map((rule, index) => {
                const hero = heroes.find((item) => heroMatchesIds(item, ruleHeroIds(rule)))
                const action = rule.action ?? {}
                const firstPriority = Array.isArray(action.prioritySkills) && action.prioritySkills[0] && typeof action.prioritySkills[0] === 'object' ? action.prioritySkills[0] as JsonObject : undefined
                const slot = typeof action.skillSlot === 'number' ? action.skillSlot : typeof firstPriority?.skillSlot === 'number' ? firstPriority.skillSlot : undefined
                const skillTypeId = typeof action.skillTypeId === 'number' ? action.skillTypeId : typeof firstPriority?.skillTypeId === 'number' ? firstPriority.skillTypeId : undefined
                const skill = hero?.skills.find((item) => skillTypeId ? item.typeId === skillTypeId : item.slot === slot)
                return (
                  <article className="rule-row" key={`${index}-${rule.name ?? ''}`}>
                    <span className="priority">{String(index + 1).padStart(2, '0')}</span>
                    <HeroAvatar hero={hero} />
                    <div className="rule-primary"><strong>{rule.name || `规则 ${index + 1}`}</strong><span>{hero?.name ?? (action.type === 'executeTrialRecipe' ? '任意行动英雄' : '未指定英雄')}</span></div>
                    <div className="form-pills">{bossMode === 'chimera' ? ruleForms(rule).slice(0, 4).map((form) => <span key={form}>{FORM_LABEL[form] ?? form}</span>) : <span>六头蛇全程</span>}</div>
                    <div className="rule-action"><SkillIcon hero={hero} skill={skill} slot={slot} /><span><small>行动</small><strong>{actionLabel(rule, heroes)}</strong></span></div>
                    <div className="rule-target"><Crosshair size={16} /><span><small>目标</small><strong>{targetLabel(rule, heroes, hydraHeads)}</strong></span></div>
                    <div className="rule-condition"><small>{action.type === 'defaultSkillPriority' ? '默认技能规则' : '严格执行规则'}</small><span>{action.type === 'defaultSkillPriority' ? `禁用 ${asNumberArray(action.blockedSkillTypeIds).length} 个技能` : conditionLabel(rule, effects, heroes)}</span></div>
                    <div className="rule-buttons">
                      <button className="icon-button" title="上移" disabled={index === 0} onClick={() => moveRule(index, -1)}><ArrowUp size={16} /></button>
                      <button className="icon-button" title="下移" disabled={index === rules.length - 1} onClick={() => moveRule(index, 1)}><ArrowDown size={16} /></button>
                      <button className="icon-button" title="编辑" onClick={() => { setEditIndex(index); setRuleOpen(true) }}><Edit3 size={16} /></button>
                      <button className="icon-button danger" title="删除" onClick={() => setConfig((current) => ({ ...current, rules: (current.rules ?? []).filter((_, itemIndex) => itemIndex !== index) }))}><Trash2 size={16} /></button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>

          <section className={`log-drawer ${showLogs ? 'open' : ''}`}>
            <div className="log-handle">
              <button className="log-toggle" onClick={() => setShowLogs((value) => !value)}><span><Activity size={16} />{activeModeSpec?.label ?? '当前模式'}运行记录 <em>{controller.logs.length}</em></span><ChevronDown size={17} /></button>
              <button className="log-maximize" title="放大运行记录" aria-label="放大运行记录" onClick={() => setLogsExpanded(true)}><Maximize2 size={16} /></button>
            </div>
            {showLogs && <pre ref={compactLogRef} tabIndex={0}>{controller.logs.length ? controller.logs.slice(-200).join('\n') : '尚无运行记录'}</pre>}
          </section>
        </section>
      </main>

      <Dialog.Root open={profileDialog !== null} onOpenChange={(open) => { if (!open && !profileBusy) setProfileDialog(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content profile-dialog">
            <div className="dialog-heading">
              <span><Dialog.Title>{profileDialog === 'create' ? '创建策略组副本' : '重命名策略组'}</Dialog.Title><Dialog.Description>{profileDialog === 'create' ? '复制当前规则、战斗目标和准备队伍，之后可以独立修改。' : '只修改策略组名称，不会改变其中的规则。'}</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label="关闭"><X size={19} /></Dialog.Close>
            </div>
            <label className="field profile-name-field"><span>策略组名称</span><input autoFocus maxLength={60} value={profileName} onChange={(event) => setProfileName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void commitProfileName() }} placeholder="例如：高速队、稳定队" /></label>
            <div className="dialog-footer"><span>{profileDialog === 'create' ? '新副本会立即成为当前策略组。' : '名称会立即保存。'}</span><div><Dialog.Close className="button ghost" disabled={profileBusy}>取消</Dialog.Close><button className="button primary" disabled={!profileName.trim() || profileBusy} onClick={() => void commitProfileName()}>{profileBusy ? '正在保存…' : profileDialog === 'create' ? '创建副本' : '保存名称'}</button></div></div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {bossMode === 'chimera' && <TrialPicker open={trialOpen} onOpenChange={setTrialOpen} trials={trials} selected={selectedTrials} onApply={(ids) => updateObjective('mandatoryTrialIds', ids)} />}
      <RuleEditor open={ruleOpen} onOpenChange={setRuleOpen} initial={editIndex === null ? undefined : rules[editIndex]} allRules={rules} heroes={heroes} hydraHeads={hydraHeads} team={team} effects={effects} bossMode={bossMode} onSave={saveRule} />
      <Dialog.Root open={logsExpanded} onOpenChange={setLogsExpanded}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog">
            <div className="dialog-heading log-dialog-heading">
              <span><Dialog.Title>{activeModeSpec?.label ?? '当前模式'}完整运行记录</Dialog.Title><Dialog.Description>只显示当前 Boss 模式的记录；另一模式的日志会独立保留。</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label="关闭完整运行记录"><X size={19} /></Dialog.Close>
            </div>
            <div className="log-dialog-status"><span className={`status-dot ${controller.running ? 'online' : ''}`} /><strong>{controller.status}</strong><em>{controller.logs.length} 条记录</em></div>
            <pre ref={expandedLogRef} tabIndex={0}>{controller.logs.length ? controller.logs.join('\n') : '尚无运行记录'}</pre>
            <div className="dialog-footer"><span>每种 Boss 模式分别保留当前会话最近 800 条记录。</span><div><button className="button ghost" onClick={() => void clearLogs()}><Trash2 size={15} />清空当前模式</button><Dialog.Close className="button primary">收起运行记录</Dialog.Close></div></div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}

export default App

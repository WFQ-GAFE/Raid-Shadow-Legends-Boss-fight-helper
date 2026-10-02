import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type SetStateAction } from 'react'
import { DraftStore } from './draftStore'
import { RequestScope, mergeLogDelta } from './requestScope'
import { useSerialPoll } from './useSerialPoll'
import { LogView } from './LogView'
import { DecisionRows } from './DecisionRows'
import { CollapsiblePanel } from './CollapsiblePanel'
import { TeamPreviewDialog, TeamPreviewSummary, type TeamPreviewSummaryState, type TeamSnapshot } from './TeamPreview'
import { TeamPicker, type ChosenTeam, type RosterHero } from './TeamPicker'
import { loadHeroData, withoutStrength, type HeroData } from './heroData'
import { effectName } from './effectNames'
import { BattleForecastPanel, ChimeraSimulationPanel, SimulationReport, ruleUsageByIndex, type BattleForecastTelemetry, type SimulationOverview, type SimulationSummary } from './ChimeraSimulation'
import { HydraSimulationPanel, HydraSimulationReport, type HydraSimulationOverview } from './HydraSimulation'
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
  Download,
  Edit3,
  Gem,
  Layers3,
  ListOrdered,
  Maximize2,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Swords,
  Trash2,
  Upload,
  Users,
  Waves,
  X,
  Zap,
} from 'lucide-react'
import { backendText, dataName, gameText, getInitialLocale, hasMessage, I18nProvider, isLocale, saveLocale, setActiveLocale, tr, type MessageKey, type UiLanguage } from './i18n'
import { installDocumentLocalization } from './i18n/dom'
import { LanguagePicker } from './LanguagePicker'
import { AdviceNote, damageText, HeroIcon, SimProvider, simulationSaveText, stuckReasonText, type FailureAdvice, type SimulationPackageStatus } from './SimulationShared'
import { HoverCard } from './HoverCard'

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
  defence?: number
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
  formId?: number
  part?: string
  partId?: number
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
  labelEn?: string
  group: string
  nativeName?: string
  iconReady?: boolean
  // Already in the window's language (options the editor builds itself).
  name?: string
}

function splitIdentifier(value: string) {
  return value
    .replace(/^Status/, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Za-z])(\d+)/g, '$1 $2')
    .trim()
}


function hydraHeadDisplayName(head?: HydraHead): string {
  if (!head) return ''
  if (document.documentElement.lang === 'zh-CN') return head.name
  if (head.resourceKind) {
    const key = `hydra.head.${head.resourceKind}`
    return hasMessage(key) ? tr(key) : tr('app.hydraHeadKind', { kind: splitIdentifier(head.resourceKind) })
  }
  const number = head.name.match(/^蛇头\s*(.*)$/u)
  return number ? tr('app.hydraHeadNumber', { id: number[1] }) : head.name
}

// The backend's effect groups (data values) and their names.
const EFFECT_GROUP_KEYS: Record<string, MessageKey> = { 增益: 'effect.group.buff', 减益: 'effect.group.debuff', 特殊: 'effect.group.special', 奇美拉: 'effect.group.chimera' }

function effectDisplay(effect: EffectOption) {
  return {
    label: effect.name ?? effectName(effect),
    group: EFFECT_GROUP_KEYS[effect.group] ? tr(EFFECT_GROUP_KEYS[effect.group]) : effect.group,
  }
}

type Rule = {
  name?: string
  when?: JsonObject
  action?: JsonObject
}

type DefaultSkillPolicyDraft = {
  prioritySkillIds: number[]
  blockedSkillIds: number[]
  firstTurnSkillId?: number
  skillTargets: Record<number, JsonObject>
  hydraTargetSkillId?: number
}

type HydraDevourRetryCondition = {
  // Mark position for isAnyOf/isNoneOf. neverMarked applies to every mark,
  // optionally only within the first markLimit marks.
  markIndex?: number
  relation: 'isAnyOf' | 'isNoneOf' | 'neverMarked'
  heroTypeIds: number[]
  markLimit?: number
}

function normalizedDevourRetryCondition(condition: HydraDevourRetryCondition): HydraDevourRetryCondition {
  if (condition.relation === 'neverMarked') {
    const limit = Math.floor(condition.markLimit ?? 0)
    return { relation: 'neverMarked', heroTypeIds: condition.heroTypeIds, ...(limit > 0 ? { markLimit: Math.min(100, limit) } : {}) }
  }
  return { markIndex: Math.max(1, Math.min(100, Math.floor(condition.markIndex ?? 1))), relation: condition.relation, heroTypeIds: condition.heroTypeIds }
}

const EMPTY_HYDRA_DEVOUR_RETRY_CONDITIONS: HydraDevourRetryCondition[] = []

type Strategy = {
  name?: string
  mode?: string
  scope?: JsonObject
  objectives?: {
    mandatoryTrialIds?: number[]
    devourOrderRetryConditions?: HydraDevourRetryCondition[]
    devourOrderForecast?: boolean
    battleForecast?: boolean
    minimumDamage?: number
    maxRegroupRetries?: number
    [key: string]: unknown
  }
  safety?: JsonObject
  team?: { heroIds?: number[]; heroTypeIds?: number[]; heroInstanceIds?: number[] }
  referenceTeam?: TeamSnapshot
  simulationPackage?: StrategySimulationPackage
  rules?: Rule[]
  executionMode?: 'list'
}

type StrategyProfile = {
  id: string
  name: string
  active?: boolean
  teamHeroIds: number[]
  ruleCount: number
}

type StorageHealth = { ok: boolean; error?: string; backups: string[] }

// Whose strategy groups these are: every game account keeps its own (key = game user id).
type StrategyAccount = { key: string | null; name: string | null }

type StrategyBundle = {
  revision: string
  config: Strategy
  activeStrategyId: string
  strategyProfiles: StrategyProfile[]
  strategyAccount?: StrategyAccount
  message?: string
  simulationPackage?: SimulationPackageStatus
}

type StrategySimulationPackage = {
  schema: number
  savedAt?: string
  bossMode?: BossMode
  team?: TeamSnapshot
  opening?: JsonObject
  accountBonuses?: JsonObject
}

type StrategyExportDocument = {
  format: 'raid-boss-strategy'
  version: number
  exportedAt: string
  bossMode: BossMode
  strategy: Strategy
  teamSnapshot?: TeamSnapshot
  simulationPackage?: StrategySimulationPackage
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
  chimeraDifficultyId?: number
  hydraTurn?: number
  headCount?: number
  heads?: HydraHead[]
  playerTurn?: number
  damage?: number
  teamHeroIds?: number[]
  teamHeroInstanceIds?: number[]
  completedTrialIds?: number[]
  trials?: Trial[]
  agentReady?: boolean
  modeReady?: boolean
  error?: string
}

type DecisionTrace = {
  rule?: string; hero?: string; skill?: string | number; target?: string; targetId?: number
  legalTargetIds?: number[]; status?: string
  rules?: { index: number; name: string; kind: string; outcome: string; conditions: { key: string; passed: boolean }[] }[]
}
type HydraForecastTelemetry = {
  status: 'waiting_input' | 'running' | 'applied' | 'unavailable' | 'not_opening' | 'unrecorded'
  verdict?: 'retry' | 'continue' | 'unknown'
  reason?: string | null
  conclusion?: string | null
  finishedAt?: string | null
  checkedWindows?: number
  horizon?: 'battle_finished' | 'turn_limit'
  turn?: number
  elapsedSeconds?: number
  battleSetupId?: string
  predictedDamage?: number | null
  minimumDamage?: number | null
  marks?: { markIndex: number; heroTypeId: number; applyTurn: number }[]
  advice?: FailureAdvice | null
  // The forecast's report (a one-run Hydra simulation report) and where the rules left a hero without an action.
  recordId?: string
  stuck?: { reason: string; turn?: number; activeHeroTypeId?: number }
}

// A saved battle forecast read from the data directory, newest first.
type HydraForecastSummary = HydraForecastTelemetry & { id: string; startedAt?: string }

function hydraForecastStatusLabel(forecast: HydraForecastTelemetry) {
  if (forecast.status === 'waiting_input') return tr('app.waitingForThisBattleS')
  if (forecast.status === 'running') return tr('app.forecastingInTheBackgroundThe')
  if (forecast.status === 'not_opening') return tr('app.notTakenOverFromThe')
  if (forecast.status === 'unavailable' && forecast.reason === 'damage_threshold_too_close') return tr('app.damageIsCloseToA')
  if (forecast.status === 'unavailable') return tr('app.undeterminedNoRegroupBasedOn')
  if (forecast.status === 'unrecorded') return tr('app.noConclusionRecordedOlderVersion')
  return forecast.verdict === 'retry' ? tr('app.predictedToViolateTheConditions') : tr('app.conditionsMetTheBattleContinues')
}

function HydraForecastEntry({ title, forecast, observed, heroes, language, onOpenReport }: { title: string; forecast: HydraForecastTelemetry; observed?: { name: string; heroTypeId: number }[]; heroes: Hero[]; language: UiLanguage; onOpenReport: (id: string) => void }) {
  const name = (heroTypeId: number) => heroByRuntimeId(heroes, heroTypeId)?.name ?? tr('app.champion', { heroTypeId })
  const predicted = (forecast.marks ?? []).map((mark) => tr('app.devourMarkEntry', { markIndex: mark.markIndex, heroTypeId: name(mark.heroTypeId), applyTurn: mark.applyTurn })).join(' → ')
  const actual = (observed ?? []).map((mark, index) => `${index + 1}.${mark.name || name(mark.heroTypeId)}`).join(' → ')
  return (
    <article className={`hydra-forecast-entry ${forecast.status}`}>
      <header><strong>{title}</strong><span>{hydraForecastStatusLabel(forecast)}{forecast.finishedAt ? ` · ${forecast.finishedAt}` : ''}</span></header>
      {forecast.conclusion && <p>{forecast.conclusion}</p>}
      <AdviceNote language={language} advice={forecast.advice} />
      {forecast.stuck && <p className="bad stuck-inline" data-i18n-skip><HeroIcon typeId={forecast.stuck.activeHeroTypeId} size="xs" />{tr('hydraSim.forecastStoppedOnTurn', { turn: forecast.stuck.turn ?? '?', reason: stuckReasonText(language, forecast.stuck.reason) })}</p>}
      {typeof forecast.predictedDamage === 'number' && <p><span>{tr('app.predictedTotalDamage')}</span>{tr('app.colon')}<span data-i18n-skip>{damageText(forecast.predictedDamage)}{typeof forecast.minimumDamage === 'number' ? ` / ${damageText(forecast.minimumDamage)}` : ''}</span></p>}
      {predicted && <p><span>{tr('app.predictedMarksTurn')}</span>{tr('app.colon')}<span data-i18n-skip>{predicted}</span></p>}
      {actual && <p><span>{tr('app.observedSoFar')}</span>{tr('app.colon')}<span data-i18n-skip>{actual}</span></p>}
      {forecast.recordId && <button className="button ghost" onClick={() => onOpenReport(forecast.recordId!)}><ListOrdered size={15} />{tr('chimeraSim.openThisBattleSSimulation')}</button>}
    </article>
  )
}

function HydraForecastPanel({ forecast, history, observed, heroes, language, onOpenReport }: { forecast?: HydraForecastTelemetry; history: HydraForecastSummary[]; observed?: { name: string; heroTypeId: number }[]; heroes: Hero[]; language: UiLanguage; onOpenReport: (id: string) => void }) {
  const previous = history.filter((entry) => !forecast || entry.battleSetupId !== forecast.battleSetupId)
  const latest = forecast ?? history[0]
  return (
    <SimProvider lang={language} boss="hydra" heroes={heroes}>
      <CollapsiblePanel id="hydra:forecast" title={tr('app.battleStartDevourForecast')} hint={toolText(latest ? hydraForecastStatusLabel(latest) : tr('app.noRecordsYet'))}>
        <div className="hydra-forecast-body">
          {forecast && <HydraForecastEntry title={tr('app.thisBattle')} forecast={forecast} observed={observed} heroes={heroes} language={language} onOpenReport={onOpenReport} />}
          {previous.map((entry) => <HydraForecastEntry key={entry.id} title={tr('app.openingAt', { startedAt: entry.startedAt ?? '' })} forecast={entry} heroes={heroes} language={language} onOpenReport={onOpenReport} />)}
          {!forecast && !previous.length && <p className="hydra-forecast-empty">{tr('app.withTheForecastOnEach')}</p>}
        </div>
      </CollapsiblePanel>
    </SimProvider>
  )
}

type ControllerState = {
  runningRevision?: string; strategyId?: string
  logCursor?: string; logsReset?: boolean
  telemetry?: {
    decision?: DecisionTrace
    command?: { status?: string; reason?: string }
    retriesUsed?: number
    devour?: { armed: boolean; sequence: { name: string; heroTypeId: number }[]; retriesUsed: number; retriesMaximum?: number; retriesRemaining?: number | null; trigger?: { conditionIndex: number; markIndex: number; hero: string } }
    devourForecast?: HydraForecastTelemetry
    battleForecast?: BattleForecastTelemetry
  }
  running: boolean
  status: string
  pid?: number
  bossMode?: BossMode
  logMode?: BossMode
  logs: string[]
  error?: string
}

type Bootstrap = {
  revision: string
  storageHealth?: StorageHealth
  catalogRevision?: string
  bossMode: BossMode
  modes: BossModeSpec[]
  processes: RaidProcess[]
  config: Strategy
  activeStrategyId: string
  strategyProfiles: StrategyProfile[]
  strategyAccount?: StrategyAccount
  heroes: Hero[]
  hydraHeads: HydraHead[]
  difficulties: Difficulty[]
  effects: EffectOption[]
  language?: UiLanguage
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
  const payload = await response.json().catch(() => undefined)
  if (!response.ok) {
    throw new Error(payload?.error || tr('app.requestFailed', { status: response.status }))
  }
  // A body cut off in transfer must not look like an empty success.
  if (payload === undefined) throw new Error(tr('app.theResponseWasIncompletePlease'))
  return payload as T
}

// Form names on the narrow rule-row chips; the full name is on hover.
// The name the backend gives an unnamed rule in reports (data: compared, not shown).
const backendRuleName = (number: number) => `规则 ${number}`

// Boss modes by id; the backend's labels are Chinese data.
const modeName = (id: string) => hasMessage(`mode.${id}`) ? tr(`mode.${id}` as MessageKey) : id

// What the user was doing when unsaved changes interrupted it (app.unsaved.saveBefore).
type UnsavedAction = 'rename' | 'switch' | 'export' | 'start'

const FORM_INDEX: Record<string, number> = { Ultimate: 0, Ram: 1, Lion: 2, Snake: 3, Viper: 3 }
const formShort = (form: string) => FORM_INDEX[form] === undefined ? undefined : tr(`chimera.form.${FORM_INDEX[form]}` as MessageKey)
const formLabel = (form: string) => FORM_INDEX[form] === undefined ? undefined : tr(`chimera.formName.${FORM_INDEX[form]}` as MessageKey)
const TRIAL_LEVEL_KEYS: Record<string, MessageKey> = { Easy: 'trial.level.1', Normal: 'trial.level.2', Hard: 'trial.level.3' }
const trialLevel = (level: string) => TRIAL_LEVEL_KEYS[level] ? tr(TRIAL_LEVEL_KEYS[level]) : undefined
const BOSS_DIFFICULTY_INDEX: Record<string, number> = { Easy: 0, Normal: 1, Hard: 2, Brutal: 3, Nightmare: 4, UltraNightmare: 5 }
const bossDifficulty = (name: string) => BOSS_DIFFICULTY_INDEX[name] === undefined ? undefined : tr(`boss.difficulty.${BOSS_DIFFICULTY_INDEX[name]}` as MessageKey)

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

type HeroStateConditionValue = {
  id: string
  heroTypeId: string
  teamPosition: string
  state: 'alive' | 'dead'
}

type ConditionEffectNode = EffectConditionValue & {
  type: 'effect'
  negate: boolean
}

type ConditionEffectCountNode = {
  id: string
  type: 'effectCount'
  target: EffectConditionValue['target']
  heroTypeId: string
  teamPosition: string
  polarity: 'all' | 'buff' | 'debuff'
  countAtLeast: string
  countAtMost: string
  negate: boolean
}

type ConditionCooldownNode = SkillCooldownConditionValue & {
  type: 'skillCooldown'
  negate: boolean
}

type ConditionHeroStateNode = HeroStateConditionValue & {
  type: 'heroState'
  negate: boolean
}

type ConditionGroupNode = {
  id: string
  type: 'group'
  operator: 'all' | 'any'
  negate: boolean
  children: ConditionTreeNode[]
}

type ConditionTreeNode = ConditionEffectNode | ConditionEffectCountNode | ConditionCooldownNode | ConditionHeroStateNode | ConditionGroupNode

let effectConditionSequence = 0
let skillCooldownConditionSequence = 0
let heroStateConditionSequence = 0
let conditionGroupSequence = 0
let effectCountSequence = 0

function createConditionEffectCount(value: Partial<ConditionEffectCountNode> = {}): ConditionEffectCountNode {
  effectCountSequence += 1
  return { id: `effect-count-${effectCountSequence}`, type: 'effectCount', target: 'boss',
    heroTypeId: '', teamPosition: '', polarity: 'debuff', countAtLeast: '10', countAtMost: '', negate: false, ...value }
}

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

function createConditionEffect(value: Partial<EffectConditionValue> = {}): ConditionEffectNode {
  return { ...createEffectCondition(value), type: 'effect', negate: false }
}

function createConditionCooldown(value: Partial<SkillCooldownConditionValue> = {}): ConditionCooldownNode {
  return { ...createSkillCooldownCondition(value), type: 'skillCooldown', negate: false }
}

function createConditionHeroState(value: Partial<HeroStateConditionValue> = {}): ConditionHeroStateNode {
  heroStateConditionSequence += 1
  return {
    id: `hero-state-condition-${heroStateConditionSequence}`,
    type: 'heroState',
    heroTypeId: '',
    teamPosition: '',
    state: 'alive',
    negate: false,
    ...value,
  }
}

function createConditionGroup(
  children: ConditionTreeNode[] = [],
  operator: 'all' | 'any' = 'all',
): ConditionGroupNode {
  conditionGroupSequence += 1
  return {
    id: `condition-group-${conditionGroupSequence}`,
    type: 'group',
    operator,
    negate: false,
    children,
  }
}

function updateConditionTreeNode(
  node: ConditionTreeNode,
  id: string,
  updater: (current: ConditionTreeNode) => ConditionTreeNode,
): ConditionTreeNode {
  if (node.id === id) return updater(node)
  if (node.type !== 'group') return node
  return {
    ...node,
    children: node.children.map((child) => updateConditionTreeNode(child, id, updater)),
  }
}

function removeConditionTreeNode(node: ConditionGroupNode, id: string): ConditionGroupNode {
  return {
    ...node,
    children: node.children
      .filter((child) => child.id !== id)
      .map((child) => child.type === 'group' ? removeConditionTreeNode(child, id) : child),
  }
}

function conditionLeafCount(node: ConditionTreeNode): number {
  return node.type === 'group'
    ? node.children.reduce((sum, child) => sum + conditionLeafCount(child), 0)
    : 1
}

function hydrateConditionTree(raw: unknown): ConditionTreeNode | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const value = raw as JsonObject
  const negate = value.negate === true
  if (value.type === 'group') {
    const children = Array.isArray(value.children)
      ? value.children.flatMap((child) => {
          const hydrated = hydrateConditionTree(child)
          return hydrated ? [hydrated] : []
        })
      : []
    if (!children.length) return undefined
    return { ...createConditionGroup(children, value.operator === 'any' ? 'any' : 'all'), negate }
  }
  if (value.type === 'effectCount') {
    if (!['all', 'buff', 'debuff'].includes(String(value.polarity))) return undefined
    const target = ['boss', 'bossPriority', 'bossAny', 'bossAll', 'ally'].includes(String(value.target))
      ? value.target as ConditionEffectCountNode['target'] : 'boss'
    return createConditionEffectCount({ target, polarity: value.polarity as ConditionEffectCountNode['polarity'],
      heroTypeId: typeof value.heroTypeId === 'number' ? String(value.heroTypeId) : '',
      teamPosition: typeof value.teamPosition === 'number' ? String(value.teamPosition) : '',
      countAtLeast: value.countAtLeast == null ? '' : String(value.countAtLeast),
      countAtMost: value.countAtMost == null ? '' : String(value.countAtMost), negate })
  }
  if (value.type === 'effect') {
    const target = value.target === 'ally'
      ? 'ally'
      : value.target === 'bossAll'
        ? 'bossAll'
        : value.target === 'bossAny'
          ? 'bossAny'
          : value.target === 'bossPriority'
            ? 'bossPriority'
            : 'boss'
    const effect = value.effect && typeof value.effect === 'object' && !Array.isArray(value.effect)
      ? value.effect as JsonObject
      : {}
    const token = effect.effectTypeId == null ? String(effect.kind ?? '') : String(effect.effectTypeId)
    if (!token) return undefined
    return {
      ...createConditionEffect({
        target,
        presence: value.presence === 'missing' ? 'missing' : 'has',
        heroTypeId: target === 'ally' && typeof value.heroTypeId === 'number' ? String(value.heroTypeId) : '',
        token,
        turnsAtLeast: effect.turnsAtLeast == null ? '' : String(effect.turnsAtLeast),
        turnsAtMost: effect.turnsAtMost == null ? '' : String(effect.turnsAtMost),
      }),
      negate,
    }
  }
  if (value.type === 'skillCooldown') {
    if (typeof value.heroTypeId !== 'number' || typeof value.skillTypeId !== 'number') return undefined
    return {
      ...createConditionCooldown({
        heroTypeId: String(value.heroTypeId),
        skillTypeId: String(value.skillTypeId),
        turnsAtLeast: value.turnsAtLeast == null ? '' : String(value.turnsAtLeast),
        turnsAtMost: value.turnsAtMost == null ? '' : String(value.turnsAtMost),
      }),
      negate,
    }
  }
  if (value.type === 'heroState') {
    if (typeof value.heroTypeId !== 'number') return undefined
    return {
      ...createConditionHeroState({
        heroTypeId: String(value.heroTypeId),
        teamPosition: typeof value.teamPosition === 'number' ? String(value.teamPosition) : '',
        state: value.state === 'dead' ? 'dead' : 'alive',
      }),
      negate,
    }
  }
  return undefined
}

function cleanText(value?: string) {
  return (value ?? '').replace(/<[^>]+>/g, '')
}

// Trial and skill descriptions are game text in the client's language; the
// English phrase table must not rewrite parts of them.
function gameDescription(value?: string) {
  return gameText(cleanText(value))
}

// Tool text rendered where the document translator is switched off (panel
// hints also carry player-named strategies, which stay as written).
const APP_NAME = 'RSL-Boss-helper'

function toolText(value: string) {
  return backendText(value)
}

function configuredTrialDifficulty(strategy: Strategy): number | undefined {
  const trialId = (strategy.objectives?.mandatoryTrialIds ?? []).find(
    (value) => Number.isInteger(value) && value > 8_000_000,
  )
  if (typeof trialId !== 'number') return undefined
  const difficultyId = Math.floor((trialId - 8_000_000) / 100)
  return difficultyId >= 1 && difficultyId <= 6 ? difficultyId : undefined
}

function damageInMillions(value?: number) {
  const millions = Number(value ?? 0) / 1_000_000
  return Number(millions.toFixed(3))
}

// Chimera reward chests by black market item id: the chest tier.
const REWARD_CHEST_TIERS: Record<number, string> = { 19001: 'I', 19002: 'II', 19003: 'III', 19004: 'IV' }

function rewardIdentity(entry: RewardEntry) {
  if (typeof entry.resourceTypeId === 'number') return `resource-${entry.resourceTypeId}`
  if (entry.type === 'RelicStones') return 'relic-stones'
  if (typeof entry.blackMarketItemId === 'number') return `bmi-${entry.blackMarketItemId}`
  return 'chimera'
}

function rewardLabel(entry: RewardEntry) {
  const resourceKey = `reward.resource.${entry.resourceType}`
  if (entry.resourceType && hasMessage(resourceKey)) return tr(resourceKey)
  if (typeof entry.blackMarketItemId === 'number') {
    const tier = REWARD_CHEST_TIERS[entry.blackMarketItemId]
    return tier ? tr('reward.relicStoneChest', { tier }) : tr('app.chimeraRewardChest')
  }
  if (entry.type === 'RelicStones') return tr('app.randomRelicStone')
  return entry.resourceType || entry.type || tr('app.chimeraReward')
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

// Each catalog is indexed once: the first champion for each runtime id, and for
// each base or runtime id (the fallback below), in catalog order.
const heroIndexes = new WeakMap<Hero[], { runtime: Map<number, Hero>; base: Map<number, Hero> }>()

function heroIndex(heroes: Hero[]) {
  let index = heroIndexes.get(heroes)
  if (!index) {
    index = { runtime: new Map(), base: new Map() }
    for (const hero of heroes) {
      for (const id of heroRuntimeIds(hero)) if (!index.runtime.has(id)) index.runtime.set(id, hero)
      for (const id of [hero.typeId, ...heroRuntimeIds(hero)]) if (!index.base.has(id)) index.base.set(id, hero)
    }
    heroIndexes.set(heroes, index)
  }
  return index
}

function heroByRuntimeId(heroes: Hero[], typeId?: number) {
  if (typeof typeId !== 'number') return undefined
  const index = heroIndex(heroes)
  const exact = index.runtime.get(typeId)
  if (exact) return exact

  // Team selection reports the rank-specific HeroTypeId (base id + 1..6).
  // A hero can be selected before that alias has been written to the catalog,
  // so fall back to its stable base id instead of leaving the old card visible.
  const rankSuffix = typeId % 10
  if (rankSuffix < 1 || rankSuffix > 6) return undefined
  return index.base.get(typeId - rankSuffix)
}

function defaultHeroPriorityIds(hero?: Hero) {
  return [...(hero?.skills ?? [])]
    .filter((skill): skill is Skill & { typeId: number } => typeof skill.typeId === 'number')
    .sort((left, right) => Number(left.isTransform) - Number(right.isTransform) || right.slot - left.slot)
    .map((skill) => skill.typeId)
}

function cloneSkillTargets(targets: Record<number, JsonObject>): Record<number, JsonObject> {
  return Object.fromEntries(Object.entries(targets).map(([id, target]) => [id, {
    ...target,
    ...(Array.isArray(target.headTypeIds) ? { headTypeIds: [...target.headTypeIds] } : {}),
  }]))
}

function cloneDefaultSkillPolicy(policy: DefaultSkillPolicyDraft): DefaultSkillPolicyDraft {
  return {
    prioritySkillIds: [...policy.prioritySkillIds],
    blockedSkillIds: [...policy.blockedSkillIds],
    firstTurnSkillId: policy.firstTurnSkillId,
    skillTargets: cloneSkillTargets(policy.skillTargets),
    hydraTargetSkillId: policy.hydraTargetSkillId,
  }
}

function readDefaultSkillPolicy(
  rawPolicy: JsonObject,
  hero?: Hero,
  fallback?: DefaultSkillPolicyDraft,
): DefaultSkillPolicyDraft {
  const catalogPriority = defaultHeroPriorityIds(hero)
  const rawPrioritySkills = Array.isArray(rawPolicy.prioritySkills) ? rawPolicy.prioritySkills as unknown[] : undefined
  const entries = rawPrioritySkills
    ? rawPrioritySkills.filter((entry): entry is JsonObject => Boolean(entry && typeof entry === 'object' && !Array.isArray(entry)))
    : []
  const savedPriority = entries.flatMap((entry) => typeof entry.skillTypeId === 'number' ? [entry.skillTypeId] : [])
  const prioritySkillIds = rawPrioritySkills
    ? [...savedPriority, ...catalogPriority.filter((id) => !savedPriority.includes(id))]
    : [...(fallback?.prioritySkillIds ?? catalogPriority)]
  const blockedSkillIds = Array.isArray(rawPolicy.blockedSkillTypeIds)
    ? asNumberArray(rawPolicy.blockedSkillTypeIds)
    : [...(fallback?.blockedSkillIds ?? [])]
  const hasFirstTurn = Object.prototype.hasOwnProperty.call(rawPolicy, 'firstTurnSkill')
  const rawFirstTurn = rawPolicy.firstTurnSkill && typeof rawPolicy.firstTurnSkill === 'object' && !Array.isArray(rawPolicy.firstTurnSkill)
    ? rawPolicy.firstTurnSkill as JsonObject
    : undefined
  const firstTurnSkillId = hasFirstTurn
    ? (typeof rawFirstTurn?.skillTypeId === 'number' ? rawFirstTurn.skillTypeId : undefined)
    : fallback?.firstTurnSkillId
  const skillTargets = fallback ? cloneSkillTargets(fallback.skillTargets) : {}
  for (const entry of entries) {
    if (typeof entry.skillTypeId !== 'number') continue
    const rawTarget = entry.target ?? rawPolicy.target
    const target = typeof rawTarget === 'string' ? { type: rawTarget } : rawTarget
    if (!target || typeof target !== 'object' || Array.isArray(target)) continue
    const targetObject = target as JsonObject
    skillTargets[entry.skillTypeId] = {
      ...targetObject,
      type: targetObject.type === 'hydraHeadSlot' ? 'hydraHeadPriority' : (targetObject.type ?? 'auto'),
      ...(Array.isArray(targetObject.headTypeIds) ? { headTypeIds: [...targetObject.headTypeIds] } : {}),
    }
  }
  if (typeof rawFirstTurn?.skillTypeId === 'number') {
    const rawTarget = typeof rawFirstTurn.target === 'string' ? { type: rawFirstTurn.target } : rawFirstTurn.target
    if (rawTarget && typeof rawTarget === 'object' && !Array.isArray(rawTarget)) {
      skillTargets[rawFirstTurn.skillTypeId] = { ...(rawTarget as JsonObject) }
    }
  }
  return {
    prioritySkillIds,
    blockedSkillIds,
    firstTurnSkillId,
    skillTargets,
    hydraTargetSkillId: savedPriority.find((id) => skillTargets[id]?.type === 'hydraHeadPriority')
      ?? fallback?.hydraTargetSkillId,
  }
}

function ruleForms(rule: Rule): string[] {
  const value = rule.when?.form
  return Array.isArray(value) ? value.map(String) : value ? [String(value)] : ALL_FORMS
}

function actionLabel(rule: Rule, heroes: Hero[]) {
  const action = rule.action ?? {}
  if (action.type === 'defaultSkillPriority') {
    const formPolicies = action.formPolicies && typeof action.formPolicies === 'object' && !Array.isArray(action.formPolicies)
      ? Object.values(action.formPolicies as JsonObject).filter((policy) => policy && typeof policy === 'object' && !Array.isArray(policy))
      : []
    if (formPolicies.length) return tr('app.defaultSkillOrderOther', { formPoliciesCount: formPolicies.length })
    const count = Array.isArray(action.prioritySkills) ? action.prioritySkills.length : 0
    const firstTurn = action.firstTurnSkill && typeof action.firstTurnSkill === 'object' && !Array.isArray(action.firstTurnSkill)
      ? action.firstTurnSkill as JsonObject
      : undefined
    const firstTurnSkillId = typeof firstTurn?.skillTypeId === 'number' ? firstTurn.skillTypeId : undefined
    const hero = heroes.find((item) => heroMatchesIds(item, ruleHeroIds(rule)))
    const opener = hero?.skills.find((skill) => skill.typeId === firstTurnSkillId)
    return tr('app.defaultSkillOrderOther2', { count, name: opener ? tr('app.firstTurnSuffix', { name: opener.name || tr('app.skill', { slot: opener.slot }) }) : '' })
  }
  if (action.type === 'executeTrialRecipe') return tr('app.decideByTheCurrentTrial')
  if (action.type === 'maintainEffects') return tr('app.keepBuffsDebuffsUpAutomatically')
  const slot = typeof action.skillSlot === 'number' ? action.skillSlot : undefined
  const skillTypeId = typeof action.skillTypeId === 'number' ? action.skillTypeId : undefined
  const hero = heroes.find((item) => heroMatchesIds(item, ruleHeroIds(rule)))
  const skill = hero?.skills.find((item) => skillTypeId ? item.typeId === skillTypeId : item.slot === slot)
  if (action.type === 'transform') {
    const direction = action.toFormIndex === 0 ? tr('app.returnToBaseForm') : action.toFormIndex === 1 ? tr('app.switchToAlternateForm') : tr('app.switchMythicalForm')
    return skill?.name ? `${direction} · ${skill.name}` : direction
  }
  return skill?.name || (slot ? tr('app.skill', { slot }) : tr('app.castSkill'))
}

function targetLabel(rule: Rule, heroes: Hero[], hydraHeads: HydraHead[] = []): string {
  const action = rule.action ?? {}
  if (action.type === 'defaultSkillPriority') {
    const rawFormPolicies = action.formPolicies && typeof action.formPolicies === 'object' && !Array.isArray(action.formPolicies)
      ? Object.values(action.formPolicies as JsonObject)
      : []
    const policies = rawFormPolicies.length ? rawFormPolicies : [action]
    const entries = policies.flatMap((policy) => policy && typeof policy === 'object' && !Array.isArray(policy) && Array.isArray((policy as JsonObject).prioritySkills)
      ? ((policy as JsonObject).prioritySkills as unknown[]).filter((entry): entry is JsonObject => Boolean(entry && typeof entry === 'object' && !Array.isArray(entry)))
      : [])
    const customTargets = entries.filter((entry) => {
      const target = typeof entry.target === 'string' ? { type: entry.target } : entry.target as JsonObject | undefined
      return target?.type && target.type !== 'auto' && !entry.isTransform
    }).length
    return customTargets
      ? tr('app.ownTargetPerSkillSet', { customTargets })
      : tr('app.eachSkillPicksALegal')
  }
  if (action.type === 'transform') return tr('app.self')
  const raw = action.target
  const target = typeof raw === 'string' ? { type: raw } : ((raw ?? {}) as JsonObject)
  if (target.type === 'self') return tr('app.self')
  if (target.type === 'lowestHpAlly') return tr('app.lowestHpAlly')
  if (target.type === 'allyHeroTypeId') {
    return heroByRuntimeId(heroes, typeof target.heroTypeId === 'number' ? target.heroTypeId : undefined)?.name ?? tr('app.specifiedAlly')
  }
  if (target.type === 'allyPosition') return tr('app.allyInSlot', { position: String(target.position ?? '?') })
  if (target.type === 'hydraHeadSlot') return tr('app.lowestHpHeadOldSlot')
  if (target.type === 'hydraHeadPriority') {
    const names = asNumberArray(target.headTypeIds).slice(0, 3).map((typeId) => {
      const head = hydraHeads.find((item) => item.typeId === typeId)
      return head ? hydraHeadDisplayName(head) : tr('app.hydraHead', { typeId })
    })
    return names.length ? tr('app.byTypePriority', { join: names.join(' → '), value: asNumberArray(target.headTypeIds).length > 3 ? '…' : '' }) : tr('app.headTypePriorityLowestHp')
  }
  if (target.type === 'devouringHead') return tr('app.devouringHead')
  if (target.type === 'exposedNeck') return tr('app.exposedNeck')
  if (target.type === 'lowestHpBoss') return tr('app.lowestHpHead')
  if (target.type === 'lowestDefenseBoss') return tr('app.lowestDefHead')
  return tr('app.chimeraBoss')
}

function conditionLabel(rule: Rule, effects: EffectOption[] = [], heroes: Hero[] = [], trials: Trial[] = []) {
  const when = rule.when ?? {}
  const ignored = new Set(['activeHeroTypeId', 'form', 'activeHeroFormIndex', 'activeHeroIsMetamorph'])
  const summarized = new Set<string>()
  const turn = when.chimeraTurnAtLeast ?? when.chimeraTurnCount
  const next = typeof when.nextForm === 'string' ? formLabel(when.nextForm) : undefined
  const parts = [turn !== undefined ? tr('app.bossTurn', { turn: String(turn) }) : '', next ? tr('app.nextForm', { next }) : ''].filter(Boolean)
  if (turn !== undefined) summarized.add(when.chimeraTurnAtLeast !== undefined ? 'chimeraTurnAtLeast' : 'chimeraTurnCount')
  if (next) summarized.add('nextForm')
  const eligibleTrialIds = asNumberArray(when.eligibleTrialsAny)
  if (eligibleTrialIds.length) {
    const selectedTrial = trials.find((trial) => trial.id === eligibleTrialIds[0])
    const description = gameDescription(selectedTrial?.description) || tr('app.trial', { eligibleTrialIds: eligibleTrialIds[0] })
    parts.push(tr('app.trialActive', { description }))
    summarized.add('eligibleTrialsAny')
  }
  const bossHas = Array.isArray(when.bossHasEffects) ? when.bossHasEffects : []
  if (bossHas.length) {
    const names = bossHas.slice(0, 2).map((token) => {
      const effect = effects.find((item) => item.token === String(token))
      return effect ? effectDisplay(effect).label : String(token)
    })
    parts.push(tr('app.bossHas', { join: names.join(tr('app.listComma')), value: bossHas.length > 2 ? '…' : '' }))
    summarized.add('bossHasEffects')
  }
  const unifiedEffects = Array.isArray(when.effectConditions) ? when.effectConditions : []
  if (unifiedEffects.length) {
    const joiner = when.effectConditionsMode === 'any' ? tr('app.or') : tr('app.listComma')
    const descriptions = unifiedEffects.slice(0, 2).flatMap((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
      const condition = raw as JsonObject
      const selector = condition.effect && typeof condition.effect === 'object' && !Array.isArray(condition.effect) ? condition.effect as JsonObject : {}
      const token = selector.effectTypeId == null ? String(selector.kind ?? '') : String(selector.effectTypeId)
      const effect = effects.find((item) => item.token === token)
      const effectName = effect ? effectDisplay(effect).label : (token || tr('app.effect'))
      const heroTypeId = asNumberArray(condition.heroTypeId)[0]
      const subject = condition.target === 'ally'
        ? heroByRuntimeId(heroes, heroTypeId)?.name ?? tr('app.specifiedChampion')
        : condition.target === 'bossAll'
          ? tr('app.allHeads')
          : condition.target === 'bossAny'
            ? tr('app.anyHead')
            : condition.target === 'bossPriority'
              ? tr('app.priorityHead')
              : 'Boss'
      return [`${subject}${condition.presence === 'missing' ? tr('app.lacks') : tr('app.has')}${effectName}`]
    })
    if (descriptions.length) parts.push(`${descriptions.join(joiner)}${unifiedEffects.length > 2 ? '…' : ''}`)
    summarized.add('effectConditions')
    summarized.add('effectConditionsMode')
  }
  const cooldownConditions = Array.isArray(when.skillCooldownConditions) ? when.skillCooldownConditions : []
  if (cooldownConditions.length) {
    const joiner = when.skillCooldownConditionsMode === 'any' ? tr('app.or') : tr('app.listComma')
    const descriptions = cooldownConditions.slice(0, 2).flatMap((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
      const condition = raw as JsonObject
      const hero = heroByRuntimeId(heroes, typeof condition.heroTypeId === 'number' ? condition.heroTypeId : undefined)
      const skill = hero?.skills.find((item) => item.typeId === condition.skillTypeId)
      const range = condition.turnsAtLeast !== undefined && condition.turnsAtMost !== undefined
        ? `${condition.turnsAtLeast}–${condition.turnsAtMost}`
        : condition.turnsAtLeast !== undefined ? `≥${condition.turnsAtLeast}` : `≤${condition.turnsAtMost}`
      return [tr('app.skillCooldownCondition', { name: hero?.name ?? tr('app.specifiedChampion'), name2: skill?.name ?? tr('app.specifiedSkill'), range })]
    })
    if (descriptions.length) parts.push(`${descriptions.join(joiner)}${cooldownConditions.length > 2 ? '…' : ''}`)
    summarized.add('skillCooldownConditions')
    summarized.add('skillCooldownConditionsMode')
  }
  if (when.conditionTree && typeof when.conditionTree === 'object' && !Array.isArray(when.conditionTree)) {
    const describeTree = (raw: unknown): { leaves: number; text: string } => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { leaves: 0, text: '?' }
      const node = raw as JsonObject
      const negated = node.negate === true ? 'NOT ' : ''
      if (node.type === 'group') {
        const children = Array.isArray(node.children) ? node.children.map(describeTree).filter((child) => child.leaves > 0) : []
        const joiner = node.operator === 'any' ? ' OR ' : ' AND '
        return { leaves: children.reduce((sum, child) => sum + child.leaves, 0), text: `${negated}(${children.slice(0, 3).map((child) => child.text).join(joiner)}${children.length > 3 ? '…' : ''})` }
      }
      if (node.type === 'effectCount') {
        const category = node.polarity === 'buff' ? tr('app.buffCount') : node.polarity === 'debuff' ? tr('app.debuffCount') : tr('app.totalEffects')
        const range = node.countAtLeast === node.countAtMost ? `=${node.countAtLeast}`
          : `${node.countAtLeast == null ? '' : `≥${node.countAtLeast}`}${node.countAtMost == null ? '' : ` ≤${node.countAtMost}`}`
        return { leaves: 1, text: `${negated}${category}${range}` }
      }
      const leafLabel = node.type === 'skillCooldown'
        ? tr('app.cooldown')
        : node.type === 'heroState'
          ? tr('app.championAlive')
          : tr('app.effect')
      return { leaves: 1, text: `${negated}${leafLabel}` }
    }
    const described = describeTree(when.conditionTree)
    if (described.leaves) parts.push(tr('app.logicOther', { text: described.text, leaves: described.leaves }))
    summarized.add('conditionTree')
  }
  const remaining = Object.keys(when).filter((key) => !ignored.has(key) && !summarized.has(key)).length
  if (remaining) parts.push(tr('app.moreConditions', { remaining }))
  return parts.join(' · ') || tr('app.wheneverTheActionWindowMatches')
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
        <img src={sources[sourceIndex]} alt="" loading="lazy" decoding="async" onError={() => setSourceIndex((current) => current + 1)} />
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
      <img src={`/api/asset/skill/${hero.typeId}/${identity}`} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
    </span>
  )
}

// Skill details for a hover card. The card is outside the translated page, so
// tool text picks its language here; skill names and descriptions are game text
// and stay in the client's language.
function SkillDetailCard({ hero, skill, slot }: { hero?: Hero; skill?: Skill; slot?: number }) {
  const number = skill?.slot ?? slot
  const alternate = (skill?.formIndex ?? 0) === 1
  const facts = [
    hero?.name,
    hero?.isMetamorph ? tr(alternate ? 'profile.alternateForm' : 'profile.baseForm') : '',
    number ? tr('profile.skill', { id: number }) : '',
    skill?.defaultCooldown ? tr('app.skillCard.cooldown', { turns: skill.defaultCooldown }) : tr('profile.noCooldown'),
    skill?.isTransform ? tr('app.skillCard.formSwitch') : '',
  ].filter(Boolean).join(' · ')
  const description = cleanText(skill?.description).trim()
  return <div className="hover-skill skill-hover-detail">
    <strong>{skill?.name || tr('profile.skill', { id: number ?? '' })}</strong>
    <small>{facts}</small>
    {description ? <p>{description}</p>
      : skill?.effectSummary ? <p>{toolText(skill.effectSummary)}</p>
        : <small>{tr('app.skillCard.noDescription')}</small>}
  </div>
}

function EffectIcon({ effect }: { effect: EffectOption }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [effect.icon, effect.iconReady])
  return (
    <span className="effect-icon">
      {effect.iconReady !== false && !failed ? <img src={`/api/asset/effect/${encodeURIComponent(effect.icon)}`} alt="" onError={() => setFailed(true)} /> : <Sparkles size={14} />}
    </span>
  )
}

function effectPickerSelection(effects: EffectOption[], value: string): EffectOption | undefined {
  if (!value) return undefined
  const exact = effects.find((effect) => effect.token === value)
  if (exact) return exact
  // Saved kind predicates intentionally cover multiple strengths. Display the
  // category without converting the saved token to a particular type ID.
  const category = effects.find((effect) => effect.icon === value)
  if (category) {
    return { ...category, token: value, name: tr('app.byEffectKind', { label: withoutStrength(effectName(category)) }) }
  }
  return { token: value, icon: 'Status_Effect_Temp', iconReady: false, label: value, name: tr('app.savedEffect', { value }), group: '特殊' }
}

function EffectPicker({
  effects,
  value,
  onValue,
  required = false,
}: {
  effects: EffectOption[]
  value: string
  onValue: (value: string) => void
  required?: boolean
}) {
  const [search, setSearch] = useState('')
  const selected = effectPickerSelection(effects, value)
  const visible = effects.filter((effect) => {
    const display = effectDisplay(effect)
    return `${effect.label} ${effect.labelEn ?? ''} ${effect.nativeName ?? ''} ${effect.group} ${display.label} ${display.group} ${effect.token}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
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
        <span>{selected ? <><strong>{selectedDisplay?.label}</strong><small>{selectedDisplay?.group}</small></> : <strong>{required ? tr('app.chooseAnEffect') : tr('app.any')}</strong>}</span>
        <ChevronDown size={15} />
      </summary>
      <div className="effect-picker-menu">
        <label className="effect-picker-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} onClick={(event) => event.stopPropagation()} placeholder={tr('app.searchEffectGroupOrId')} /></label>
        <div className="effect-picker-options">
          <button type="button" className={!value ? 'effect-picker-option active' : 'effect-picker-option'} onClick={(event) => choose(event, '')}><span className="effect-icon"><Sparkles size={14} /></span><span><strong>{required ? tr('app.clearSelection') : tr('app.any')}</strong><small>{required ? tr('app.toCountEffectsAddAn') : tr('app.ignoreThisCondition')}</small></span></button>
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
  if (!entries.length) return <span className="reward-empty">{tr('app.currentRewardsNotReadYet')}</span>
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
            <div><Dialog.Title>{tr('app.selectRequiredTrials')}</Dialog.Title><Dialog.Description>{tr('app.eachTrialShowsItsCurrent')}</Dialog.Description></div>
            <Dialog.Close className="icon-button" aria-label={tr('app.close')}><X size={19} /></Dialog.Close>
          </div>
          <label className="search-box"><Search size={19} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={tr('app.searchTrialTextOrRewards')} /></label>
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
                      <em>{formLabel(trial.form ?? '') ?? trial.form}</em>
                      <em>{trialLevel(trial.difficulty ?? '') ?? trial.difficulty}</em>
                      {trial.completed && <em className="success">{tr('app.completed')}</em>}
                    </span>
                    <strong>{gameDescription(trial.description)}</strong>
                    <span className="trial-reward-heading">{tr('app.currentRotationReward')}</span>
                    <TrialRewards reward={trial.reward} />
                  </span>
                </button>
              )
            })}
          </div>
          <div className="dialog-footer">
            <span>{tr('app.draftSelected', { draftCount: draft.length })}</span>
            <div><button className="button ghost" onClick={() => setDraft([])}>{tr('app.clear')}</button><button className="button primary" onClick={() => { onApply(draft); onOpenChange(false) }}>{tr('app.applySelection')}</button></div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function HydraDevourRetryPicker({
  open,
  onOpenChange,
  heroes,
  team,
  conditions,
  forecast,
  onApply,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  heroes: Hero[]
  team: number[]
  conditions: HydraDevourRetryCondition[]
  forecast: boolean
  onApply: (conditions: HydraDevourRetryCondition[], forecast: boolean) => void
}) {
  const [draft, setDraft] = useState<HydraDevourRetryCondition[]>(conditions)
  const [draftForecast, setDraftForecast] = useState(forecast)
  const teamHeroTypeIds = [...new Set(team.filter((value) => Number.isInteger(value) && value > 0))]

  useEffect(() => {
    if (!open) return
    setDraft(conditions)
    setDraftForecast(forecast)
  }, [open, conditions, forecast])

  function updateCondition(index: number, changes: Partial<HydraDevourRetryCondition>) {
    setDraft((current) => current.map((condition, itemIndex) => itemIndex === index ? { ...condition, ...changes } : condition))
  }

  function addCondition() {
    if (!teamHeroTypeIds.length) return
    setDraft((current) => [...current, {
      markIndex: Math.min(100, current.length + 1),
      relation: 'isNoneOf',
      heroTypeIds: [teamHeroTypeIds[0]],
    }])
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content early-retry-dialog">
          <div className="dialog-heading">
            <div><Dialog.Title>{tr('app.hydraDevourOrderRegroup')}</Dialog.Title><Dialog.Description>{tr('app.theFirstDevourMarkIs')}</Dialog.Description></div>
            <Dialog.Close className="icon-button" aria-label={tr('app.close')}><X size={19} /></Dialog.Close>
          </div>
          <div className="early-retry-list">
            <button type="button" className={`devour-forecast-toggle ${draftForecast ? 'selected' : ''}`} aria-pressed={draftForecast} onClick={() => setDraftForecast((current) => !current)}>
              <span className="check-box">{draftForecast && <Check size={14} />}</span>
              <span><strong>{tr('app.forecastTheDevourOrderAt')}</strong><small>{tr('app.afterTheBattleStartsThe')}</small></span>
            </button>
            {!teamHeroTypeIds.length && <div className="effect-condition-empty"><Waves size={22} /><span><strong>{tr('app.preparedTeamNotReadYet')}</strong><small>{tr('app.openTheHydraPreparationScreen')}</small></span></div>}
            {teamHeroTypeIds.length > 0 && !draft.length && <div className="effect-condition-empty"><Activity size={22} /><span><strong>{tr('app.noDevourOrderConditionsYet')}</strong><small>{tr('app.withoutConditionsDevourTargetsNever')}</small></span></div>}
            {draft.map((condition, index) => (
              <article className="early-retry-condition" key={index}>
                <div className="early-retry-condition-heading"><strong>{tr('app.condition', { value: index + 1 })}</strong><button type="button" className="icon-button danger" aria-label={tr('app.removeDevourOrderCondition', { value: index + 1 })} onClick={() => setDraft((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} /></button></div>
                <div className="early-retry-fields">
                  {condition.relation === 'neverMarked'
                    ? <label className="field"><span>{tr('app.scopeSwallowsCausedByThe')}</span><NumericInput value={condition.markLimit ?? 0} maximum={100} onValue={(value) => updateCondition(index, { markLimit: value })} /></label>
                    : <label className="field"><span>{tr('app.devourMarkNumber')}</span><NumericInput value={condition.markIndex ?? 1} onValue={(value) => updateCondition(index, { markIndex: value })} /></label>}
                  <label className="field"><span>{tr('app.targetRequirement')}</span><select value={condition.relation} onChange={(event) => {
                    const relation = event.target.value === 'isAnyOf' ? 'isAnyOf' : event.target.value === 'neverMarked' ? 'neverMarked' : 'isNoneOf'
                    updateCondition(index, relation === 'neverMarked' ? { relation } : { relation, markIndex: condition.markIndex ?? Math.min(100, index + 1) })
                  }}><option value="isNoneOf">{tr('app.mustNotBeASelected')}</option><option value="isAnyOf">{tr('app.mustBeOneOfThe')}</option><option value="neverMarked">{tr('app.selectedChampionsAreNeverDevoured')}</option></select></label>
                </div>
                {condition.relation === 'neverMarked' && <p className="devour-condition-note">{tr('app.onlyAChosenChampionActually')}</p>}
                <div className="devour-retry-heroes">
                  {teamHeroTypeIds.map((heroTypeId) => {
                    const hero = heroByRuntimeId(heroes, heroTypeId)
                    const checked = condition.heroTypeIds.includes(heroTypeId)
                    return <button type="button" key={heroTypeId} className={checked ? 'selected' : ''} onClick={() => updateCondition(index, { heroTypeIds: checked ? condition.heroTypeIds.filter((value) => value !== heroTypeId) : [...condition.heroTypeIds, heroTypeId] })}><span className="check-box">{checked && <Check size={14} />}</span><HeroAvatar hero={hero} size="sm" /><span><strong>{hero?.name ?? tr('app.champion', { heroTypeId })}</strong></span></button>
                  })}
                </div>
              </article>
            ))}
          </div>
          <div className="dialog-footer">
            <button className="button ghost" disabled={!teamHeroTypeIds.length || draft.length >= 20} onClick={addCondition}><Plus size={15} />{tr('app.addOrderCondition')}</button>
            <div><Dialog.Close className="button ghost">{tr('app.cancel')}</Dialog.Close><button className="button primary" onClick={() => { onApply(draft.filter((condition) => condition.heroTypeIds.length).map(normalizedDevourRetryCondition), draftForecast); onOpenChange(false) }}>{tr('app.applyConditions')}</button></div>
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
  trials,
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
  trials: Trial[]
  bossMode: BossMode
  onSave: (rule: Rule) => void
}) {
  const firstTeamHero = team
    .map((typeId) => heroByRuntimeId(heroes, typeId))
    .find((candidate) => candidate !== undefined)
  const firstHero = firstTeamHero?.typeId ?? heroes[0]?.typeId ?? 0
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
  const [defaultPolicyForm, setDefaultPolicyForm] = useState('Ultimate')
  const [defaultPolicies, setDefaultPolicies] = useState<Record<string, DefaultSkillPolicyDraft>>({})
  const [target, setTarget] = useState('boss')
  const [targetPosition, setTargetPosition] = useState(1)
  const [headPriorityIds, setHeadPriorityIds] = useState<number[]>([])
  const [advanced, setAdvanced] = useState('{}')
  const [turnMin, setTurnMin] = useState('')
  const [turnMax, setTurnMax] = useState('')
  const [switchWithin, setSwitchWithin] = useState('')
  const [nextForm, setNextForm] = useState('')
  const [eligibleTrialId, setEligibleTrialId] = useState(0)
  const [damageMin, setDamageMin] = useState('')
  const [conditionTree, setConditionTree] = useState<ConditionGroupNode>(() => createConditionGroup())
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
    const policyHero = initialHero ?? heroes[0]
    const basePolicy = readDefaultSkillPolicy(action, policyHero)
    const rawFormPolicies = action.formPolicies && typeof action.formPolicies === 'object' && !Array.isArray(action.formPolicies)
      ? action.formPolicies as JsonObject
      : {}
    if (bossMode === 'chimera') {
      const nextPolicies: Record<string, DefaultSkillPolicyDraft> = {}
      // A form-specific policy inherits the normal skill order/targets from
      // the legacy top-level policy, but its opener is always independent.
      // The top-level policy serializes Ultimate for backwards compatibility;
      // inheriting its opener here would silently copy it to every form.
      const policyFallback = { ...basePolicy, firstTurnSkillId: undefined }
      const hasFormPolicies = Object.keys(rawFormPolicies).length > 0
      for (const form of ALL_FORMS) {
        const rawFormPolicy = rawFormPolicies[form]
        nextPolicies[form] = rawFormPolicy && typeof rawFormPolicy === 'object' && !Array.isArray(rawFormPolicy)
          ? readDefaultSkillPolicy(rawFormPolicy as JsonObject, policyHero, policyFallback)
          : cloneDefaultSkillPolicy(
              !hasFormPolicies && form === 'Ultimate' ? basePolicy : policyFallback,
            )
      }
      setDefaultPolicies(nextPolicies)
      setDefaultPolicyForm(ruleForms(initial ?? {})[0] ?? 'Ultimate')
    } else {
      setDefaultPolicies({ all: basePolicy })
      setDefaultPolicyForm('all')
    }
    const rawTarget = defaultRule ? undefined : action.target
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
    const savedEligibleTrials = asNumberArray(when.eligibleTrialsAny)
    setEligibleTrialId(savedEligibleTrials.length === 1 ? savedEligibleTrials[0] : 0)
    if (savedEligibleTrials.length === 1) delete when.eligibleTrialsAny
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
    const legacyEffectMode = when.effectConditionsMode === 'any' ? 'any' : 'all'
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
    const legacyCooldownMode = when.skillCooldownConditionsMode === 'any' ? 'any' : 'all'
    delete when.skillCooldownConditions
    delete when.skillCooldownConditionsMode
    const hydratedTree = hydrateConditionTree(when.conditionTree)
    delete when.conditionTree
    if (hydratedTree) {
      setConditionTree(
        hydratedTree.type === 'group'
          ? hydratedTree
          : createConditionGroup([hydratedTree]),
      )
    } else {
      const legacyChildren: ConditionTreeNode[] = []
      if (nextEffectConditions.length) {
        const effectNodes = nextEffectConditions.map((condition) => ({
          ...condition,
          type: 'effect' as const,
          negate: false,
        }))
        legacyChildren.push(
          effectNodes.length === 1
            ? effectNodes[0]
            : createConditionGroup(effectNodes, legacyEffectMode),
        )
      }
      if (nextCooldownConditions.length) {
        const cooldownNodes = nextCooldownConditions.map((condition) => ({
          ...condition,
          type: 'skillCooldown' as const,
          negate: false,
        }))
        legacyChildren.push(
          cooldownNodes.length === 1
            ? cooldownNodes[0]
            : createConditionGroup(cooldownNodes, legacyCooldownMode),
        )
      }
      setConditionTree(createConditionGroup(legacyChildren, 'all'))
    }
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
  const defaultPolicyKey = bossMode === 'chimera' ? defaultPolicyForm : 'all'
  const fallbackDefaultPolicy = readDefaultSkillPolicy({}, hero)
  const activeDefaultPolicy = defaultPolicies[defaultPolicyKey] ?? fallbackDefaultPolicy
  const prioritySkillIds = activeDefaultPolicy.prioritySkillIds
  const blockedSkillIds = activeDefaultPolicy.blockedSkillIds
  const firstTurnSkillId = activeDefaultPolicy.firstTurnSkillId
  const defaultSkillTargets = activeDefaultPolicy.skillTargets
  const defaultHydraTargetSkillId = activeDefaultPolicy.hydraTargetSkillId

  function updateActiveDefaultPolicy(updater: (current: DefaultSkillPolicyDraft) => DefaultSkillPolicyDraft) {
    setDefaultPolicies((current) => {
      const existing = current[defaultPolicyKey] ?? fallbackDefaultPolicy
      return { ...current, [defaultPolicyKey]: updater(cloneDefaultSkillPolicy(existing)) }
    })
  }

  function setPrioritySkillIds(value: number[] | ((current: number[]) => number[])) {
    updateActiveDefaultPolicy((current) => ({
      ...current,
      prioritySkillIds: typeof value === 'function' ? value(current.prioritySkillIds) : value,
    }))
  }

  function setBlockedSkillIds(value: number[] | ((current: number[]) => number[])) {
    updateActiveDefaultPolicy((current) => ({
      ...current,
      blockedSkillIds: typeof value === 'function' ? value(current.blockedSkillIds) : value,
    }))
  }

  function setFirstTurnSkillId(value: number | undefined) {
    updateActiveDefaultPolicy((current) => ({ ...current, firstTurnSkillId: value }))
  }

  function setDefaultSkillTargets(value: Record<number, JsonObject> | ((current: Record<number, JsonObject>) => Record<number, JsonObject>)) {
    updateActiveDefaultPolicy((current) => ({
      ...current,
      skillTargets: typeof value === 'function' ? value(current.skillTargets) : value,
    }))
  }

  function setDefaultHydraTargetSkillId(value: number | undefined) {
    updateActiveDefaultPolicy((current) => ({ ...current, hydraTargetSkillId: value }))
  }

  function resetDefaultPoliciesForHero(nextHero?: Hero) {
    const base = readDefaultSkillPolicy({}, nextHero)
    if (bossMode === 'chimera') {
      setDefaultPolicies(Object.fromEntries(ALL_FORMS.map((form) => [form, cloneDefaultSkillPolicy(base)])))
      setDefaultPolicyForm('Ultimate')
    } else {
      setDefaultPolicies({ all: base })
      setDefaultPolicyForm('all')
    }
  }

  const skills = heroForm === 'any'
    ? allHeroSkills
    : allHeroSkills.filter((item) => (item.formIndex ?? 0) === (heroForm === 'transformed' ? 1 : 0))
  const teamHeroRank = new Map<number, number>()
  team.forEach((typeId, index) => {
    const teamHero = heroByRuntimeId(heroes, typeId)
    if (teamHero && !teamHeroRank.has(teamHero.typeId)) teamHeroRank.set(teamHero.typeId, index)
  })
  const visibleHeroes = heroes
    .filter((item) => item.name.toLocaleLowerCase().includes(heroSearch.trim().toLocaleLowerCase()))
    .sort((left, right) => {
      const leftRank = teamHeroRank.get(left.typeId)
      const rightRank = teamHeroRank.get(right.typeId)
      if (leftRank === undefined && rightRank === undefined) return 0
      if (leftRank === undefined) return 1
      if (rightRank === undefined) return -1
      return leftRank - rightRank
    })
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
  const defaultHydraTargetSkill = hero?.skills.find((skill) => skill.typeId === defaultHydraTargetSkillId)
  const defaultHydraHeadPriorityIds = asNumberArray(
    defaultHydraTargetSkillId === undefined ? undefined : defaultSkillTargets[defaultHydraTargetSkillId]?.headTypeIds,
  )

  function setDefaultSkillTargetType(skillId: number, type: string) {
    setDefaultSkillTargets((current) => {
      const previous = current[skillId] ?? { type: 'auto' }
      const next = type === 'allyPosition'
        ? { type, position: typeof previous.position === 'number' ? previous.position : 1 }
        : type === 'hydraHeadPriority'
          ? { type, headTypeIds: asNumberArray(previous.headTypeIds), fallback: 'lowestHp' }
          : { type }
      return { ...current, [skillId]: next }
    })
    if (type === 'hydraHeadPriority') setDefaultHydraTargetSkillId(skillId)
  }

  function setDefaultSkillTargetPosition(skillId: number, position: number) {
    setDefaultSkillTargets((current) => ({ ...current, [skillId]: { type: 'allyPosition', position } }))
  }

  function updateDefaultHydraHeadPriority(updater: (current: number[]) => number[]) {
    if (defaultHydraTargetSkillId === undefined) return
    setDefaultSkillTargets((current) => {
      const target = current[defaultHydraTargetSkillId] ?? { type: 'hydraHeadPriority' }
      return {
        ...current,
        [defaultHydraTargetSkillId]: {
          ...target,
          type: 'hydraHeadPriority',
          headTypeIds: updater(asNumberArray(target.headTypeIds)),
          fallback: 'lowestHp',
        },
      }
    })
  }

  function serializeDefaultSkillPolicy(policy: DefaultSkillPolicyDraft): JsonObject {
    const firstTurnSkill = hero?.skills.find((item) => item.typeId === policy.firstTurnSkillId)
    const firstTurnEntry: JsonObject | undefined = firstTurnSkill?.typeId
      ? {
          skillTypeId: firstTurnSkill.typeId,
          skillSlot: firstTurnSkill.slot,
          formIndex: firstTurnSkill.formIndex ?? 0,
          isTransform: Boolean(firstTurnSkill.isTransform),
          target: firstTurnSkill.isTransform
            ? { type: 'self' }
            : (policy.skillTargets[firstTurnSkill.typeId] ?? { type: 'auto' }),
        }
      : undefined
    return {
      ...(firstTurnEntry ? { firstTurnSkill: firstTurnEntry } : {}),
      prioritySkills: policy.prioritySkillIds
        .filter((id) => !policy.blockedSkillIds.includes(id))
        .flatMap((id) => {
          const skill = hero?.skills.find((item) => item.typeId === id)
          const skillTarget = skill?.isTransform
            ? { type: 'self' }
            : (policy.skillTargets[id] ?? { type: 'auto' })
          return skill ? [{ skillTypeId: id, skillSlot: skill.slot, formIndex: skill.formIndex ?? 0, isTransform: Boolean(skill.isTransform), target: skillTarget }] : []
        }),
      blockedSkillTypeIds: policy.blockedSkillIds,
    }
  }

  function serializeConditionNode(node: ConditionTreeNode): JsonObject {
    if (node.type === 'group') {
      if (!node.children.length) throw new Error(tr('app.aLogicGroupNeedsAt'))
      return {
        type: 'group',
        operator: node.operator,
        ...(node.negate ? { negate: true } : {}),
        children: node.children.map(serializeConditionNode),
      }
    }
    if (node.type === 'effectCount') {
      const condition: JsonObject = { type: 'effectCount', target: node.target, polarity: node.polarity,
        ...(node.negate ? { negate: true } : {}) }
      if (node.target === 'ally') {
        const heroTypeId = Number(node.heroTypeId)
        const position = Number(node.teamPosition)
        if (!Number.isInteger(heroTypeId) || heroTypeId <= 0 || !team.slice(0, teamSize).includes(heroTypeId)) {
          throw new Error(tr('app.chooseASpecificChampionFrom'))
        }
        condition.heroTypeId = heroTypeId
        if (node.teamPosition) {
          if (!Number.isInteger(position) || position < 1 || position > teamSize || team[position - 1] !== heroTypeId) {
            throw new Error(tr('app.chooseASpecificChampionFrom'))
          }
          condition.teamPosition = position
        }
      }
      for (const key of ['countAtLeast', 'countAtMost'] as const) {
        if (!node[key].trim()) continue
        const count = Number(node[key])
        if (!Number.isSafeInteger(count) || count < 0) throw new Error(tr('app.effectCountsMustBeNon'))
        condition[key] = count
      }
      if (condition.countAtLeast === undefined && condition.countAtMost === undefined) throw new Error(tr('app.enterAtLeastOneEffect'))
      if (typeof condition.countAtLeast === 'number' && typeof condition.countAtMost === 'number' && condition.countAtLeast > condition.countAtMost) throw new Error(tr('app.theMinimumCountCannotExceed'))
      return condition
    }
    if (node.type === 'effect') {
      if (!node.token) throw new Error(tr('app.chooseAnEffectForEvery'))
      const selector: JsonObject = /^\d+$/.test(node.token)
        ? { effectTypeId: Number(node.token) }
        : { kind: node.token }
      const condition: JsonObject = {
        type: 'effect',
        target: node.target,
        presence: node.presence,
        effect: selector,
        ...(node.negate ? { negate: true } : {}),
      }
      if (node.target === 'ally') {
        const selectedHeroTypeId = Number(node.heroTypeId)
        if (!Number.isInteger(selectedHeroTypeId) || selectedHeroTypeId <= 0 || !team.slice(0, teamSize).includes(selectedHeroTypeId)) {
          throw new Error(tr('app.chooseASpecificChampionFrom'))
        }
        condition.heroTypeId = selectedHeroTypeId
      }
      for (const [turnKey, raw] of (node.presence === 'has' ? [['turnsAtLeast', node.turnsAtLeast], ['turnsAtMost', node.turnsAtMost]] : []) as Array<['turnsAtLeast' | 'turnsAtMost', string]>) {
        if (!raw.trim()) continue
        const turns = Number(raw)
        if (!Number.isInteger(turns) || turns < 0) throw new Error(tr('app.remainingEffectTurnsMustBe'))
        selector[turnKey] = turns
      }
      return condition
    }
    if (node.type === 'heroState') {
      const selectedHeroTypeId = Number(node.heroTypeId)
      const selectedTeamPosition = Number(node.teamPosition)
      const teamHeroTypeId = team[selectedTeamPosition - 1]
      if (
        !Number.isInteger(selectedHeroTypeId)
        || selectedHeroTypeId <= 0
        || !Number.isInteger(selectedTeamPosition)
        || selectedTeamPosition < 1
        || selectedTeamPosition > teamSize
        || teamHeroTypeId !== selectedHeroTypeId
      ) {
        throw new Error(tr('app.theAliveConditionNeedsA'))
      }
      return {
        type: 'heroState',
        heroTypeId: selectedHeroTypeId,
        teamPosition: selectedTeamPosition,
        state: node.state,
        ...(node.negate ? { negate: true } : {}),
      }
    }
    const selectedHeroTypeId = Number(node.heroTypeId)
    const selectedSkillTypeId = Number(node.skillTypeId)
    const selectedHero = heroByRuntimeId(heroes, selectedHeroTypeId)
    if (!Number.isInteger(selectedHeroTypeId) || selectedHeroTypeId <= 0 || !team.slice(0, teamSize).includes(selectedHeroTypeId) || !selectedHero) {
      throw new Error(tr('app.chooseASpecificChampionFrom2'))
    }
    if (!Number.isInteger(selectedSkillTypeId) || !selectedHero.skills.some((skill) => skill.typeId === selectedSkillTypeId)) {
      throw new Error(tr('app.chooseOneOfTheChampion'))
    }
    const condition: JsonObject = {
      type: 'skillCooldown',
      heroTypeId: selectedHeroTypeId,
      skillTypeId: selectedSkillTypeId,
      ...(node.negate ? { negate: true } : {}),
    }
    for (const [turnKey, raw] of [['turnsAtLeast', node.turnsAtLeast], ['turnsAtMost', node.turnsAtMost]] as const) {
      if (!raw.trim()) continue
      const turns = Number(raw)
      if (!Number.isInteger(turns) || turns < 0) throw new Error(tr('app.remainingCooldownMustBeA'))
      condition[turnKey] = turns
    }
    if (condition.turnsAtLeast === undefined && condition.turnsAtMost === undefined) {
      throw new Error(tr('app.eachCooldownConditionNeedsAt'))
    }
    if (typeof condition.turnsAtLeast === 'number' && typeof condition.turnsAtMost === 'number' && condition.turnsAtLeast > condition.turnsAtMost) {
      throw new Error(tr('app.theMinimumCooldownCannotExceed'))
    }
    return condition
  }

  function commit() {
    try {
      const extra = JSON.parse(advanced || '{}')
      if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error(tr('app.advancedConditionsMustBeAn'))
      if (bossMode === 'hydra') {
        for (const key of HYDRA_BATTLE_TURN_CONDITION_KEYS) delete extra[key]
      }
      if (bossMode === 'chimera' && ruleKind === 'strict' && !forms.length) throw new Error(tr('app.selectAtLeastOneChimera'))
      const when: JsonObject = {
        ...(ruleKind === 'strict' ? extra : {}),
        activeHeroTypeId: ruleKind === 'strict' && allHeroes
          ? Array.from(new Set(heroes.flatMap((item) => heroRuntimeIds(item))))
          : heroRuntimeIds(hero),
      }
      if (bossMode === 'chimera') when.form = ruleKind === 'default' ? ALL_FORMS : forms
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
            if (!Number.isFinite(number) || number < 0) throw new Error(tr('app.commonTriggersMustBeNon'))
            when[key] = key === 'currentDamageAtLeast' ? number * 1_000_000 : number
          }
        }
        if (bossMode === 'chimera' && nextForm) when.nextForm = nextForm
        if (bossMode === 'chimera' && eligibleTrialId > 0) {
          when.eligibleTrialsAny = [eligibleTrialId]
        }
        if (conditionTree.children.length) {
          when.conditionTree = serializeConditionNode(conditionTree)
        }
      }
      const selectedTarget: JsonObject = target === 'allyPosition'
        ? { type: target, position: targetPosition }
        : target === 'hydraHeadPriority'
          ? { type: target, headTypeIds: headPriorityIds, fallback: 'lowestHp' }
          : { type: target }
      const serializedDefaultPolicies = bossMode === 'chimera'
        ? Object.fromEntries(ALL_FORMS.map((form) => [
            form,
            serializeDefaultSkillPolicy(defaultPolicies[form] ?? fallbackDefaultPolicy),
          ]))
        : undefined
      const serializedDefaultBase = bossMode === 'chimera'
        ? serializedDefaultPolicies?.Ultimate ?? serializeDefaultSkillPolicy(fallbackDefaultPolicy)
        : serializeDefaultSkillPolicy(defaultPolicies.all ?? fallbackDefaultPolicy)
      const baseAction: JsonObject = ruleKind === 'default'
        ? {
            type: 'defaultSkillPriority',
            ...serializedDefaultBase,
            ...(serializedDefaultPolicies ? { formPolicies: serializedDefaultPolicies } : {}),
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
                ...(!allHeroes && hero?.isMetamorph && typeof selectedSkill?.formIndex === 'number'
                  ? { formIndex: selectedSkill.formIndex }
                  : {}),
                target: target === 'allyHeroTypeId' && initial?.action?.target
                    ? initial.action.target
                    : selectedTarget,
              }
      const effectiveActionType = ruleKind === 'default' ? 'defaultSkillPriority' : actionType
      const action: JsonObject = initial?.action?.type === effectiveActionType
        ? { ...initial.action, ...baseAction }
        : baseAction
      if (ruleKind === 'default') {
        delete action.target
        if (!serializedDefaultBase.firstTurnSkill) delete action.firstTurnSkill
        if (bossMode !== 'chimera') delete action.formPolicies
      }
      if (ruleKind === 'strict' && actionType === 'cast' && (allHeroes || !selectedSkill?.typeId)) {
        delete action.skillTypeId
      }
      const autoName = ruleKind === 'default'
        ? tr('app.defaultSkillOrderOf', { name: hero?.name ?? tr('app.champion2') })
        : `${hero?.name ?? tr('app.champion2')} · ${actionType === 'transform' ? tr('app.switchForm') : actionLabel({ when, action }, heroes)} → ${targetLabel({ when, action }, heroes, hydraHeads)}`
      onSave({ name: name.trim() || autoName, when, action })
      onOpenChange(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  function mutateConditionNode(
    id: string,
    updater: (current: ConditionTreeNode) => ConditionTreeNode,
  ) {
    setConditionTree((current) => updateConditionTreeNode(current, id, updater) as ConditionGroupNode)
  }

  function appendConditionNode(groupId: string, child: ConditionTreeNode) {
    mutateConditionNode(groupId, (current) => current.type === 'group'
      ? { ...current, children: [...current.children, child] }
      : current)
  }

  function removeConditionNode(id: string) {
    setConditionTree((current) => removeConditionTreeNode(current, id))
  }

  function defaultCooldownNode(): ConditionCooldownNode {
    const heroTypeId = team.slice(0, teamSize).find((value) => value > 0) ?? 0
    const conditionHero = heroByRuntimeId(heroes, heroTypeId)
    const conditionSkill = conditionHero?.skills.find((skill) => typeof skill.typeId === 'number')
    return createConditionCooldown({
      heroTypeId: heroTypeId ? String(heroTypeId) : '',
      skillTypeId: conditionSkill?.typeId ? String(conditionSkill.typeId) : '',
    })
  }

  function defaultHeroStateNode(): ConditionHeroStateNode {
    const teamIndex = team.slice(0, teamSize).findIndex((value) => value > 0)
    const position = teamIndex >= 0 ? teamIndex + 1 : 0
    const heroTypeId = position ? team[position - 1] : 0
    return createConditionHeroState({
      heroTypeId: heroTypeId ? String(heroTypeId) : '',
      teamPosition: position ? String(position) : '',
    })
  }

  function renderConditionNode(
    node: ConditionTreeNode,
    depth: number,
    path: string,
  ): ReactNode {
    if (node.type === 'group') {
      return <div className={`condition-tree-group${depth === 0 ? ' root' : ''}${node.negate ? ' negated' : ''}`} key={node.id}>
        <div className="condition-tree-group-heading">
          <span className="condition-tree-path">{depth === 0 ? tr('app.overallLogic') : tr('app.logicGroup', { path })}</span>
          <label className="condition-logic-select"><span>{tr('app.groupOperator')}</span><select value={node.operator} onChange={(event) => mutateConditionNode(node.id, (current) => current.type === 'group' ? { ...current, operator: event.target.value === 'any' ? 'any' : 'all' } : current)}><option value="all">{tr('app.matchAllAnd')}</option><option value="any">{tr('app.matchAnyOr')}</option></select></label>
          <button type="button" className={node.negate ? 'condition-negate active' : 'condition-negate'} onClick={() => mutateConditionNode(node.id, (current) => ({ ...current, negate: !current.negate }))}>{node.negate ? tr('app.negatedNot') : tr('app.negateNot')}</button>
          <div className="condition-tree-add-actions">
            <details className="condition-add-menu">
              <summary className="button ghost"><Plus size={14} />{tr('app.addCondition')}<ChevronDown size={14} /></summary>
              <div className="condition-add-menu-popover">
                <button type="button" onClick={(event) => { appendConditionNode(node.id, createConditionEffect({ target: bossMode === 'hydra' ? 'bossPriority' : 'boss' })); event.currentTarget.closest('details')?.removeAttribute('open') }}><ShieldCheck size={17} /><span><strong>{tr('app.effectState')}</strong><small>{tr('app.buffsDebuffsAndRemainingTurns')}</small></span></button>
                <button type="button" onClick={(event) => { appendConditionNode(node.id, createConditionEffectCount({ target: bossMode === 'hydra' ? 'bossPriority' : 'boss' })); event.currentTarget.closest('details')?.removeAttribute('open') }}><Layers3 size={17} /><span><strong>{tr('app.effectCount')}</strong><small>{tr('app.countOfBuffsDebuffsOr')}</small></span></button>
                <button type="button" onClick={(event) => { appendConditionNode(node.id, defaultCooldownNode()); event.currentTarget.closest('details')?.removeAttribute('open') }}><Zap size={17} /><span><strong>{tr('app.skillCooldown')}</strong><small>{tr('app.currentCooldownOfAChampion')}</small></span></button>
                <button type="button" onClick={(event) => { appendConditionNode(node.id, defaultHeroStateNode()); event.currentTarget.closest('details')?.removeAttribute('open') }}><Users size={17} /><span><strong>{tr('app.championAliveOrDead')}</strong><small>{tr('app.requireAnAllyToBe')}</small></span></button>
                <button type="button" onClick={(event) => { appendConditionNode(node.id, createConditionGroup([createConditionEffect({ target: bossMode === 'hydra' ? 'bossPriority' : 'boss' })])); event.currentTarget.closest('details')?.removeAttribute('open') }}><Layers3 size={17} /><span><strong>{tr('app.nestedGroup')}</strong><small>{tr('app.combineMoreAndOrAnd')}</small></span></button>
              </div>
            </details>
          </div>
          {depth > 0 && <button type="button" className="effect-condition-delete" aria-label={tr('app.removeLogicGroup', { path })} onClick={() => removeConditionNode(node.id)}><Trash2 size={15} /></button>}
        </div>
        <div className="condition-tree-children">
          {node.children.length
            ? node.children.map((child, index) => renderConditionNode(child, depth + 1, path ? `${path}.${index + 1}` : String(index + 1)))
            : <div className="effect-condition-empty"><ShieldCheck size={22} /><span><strong>{tr('app.thisLogicGroupHasNo')}</strong><small>{tr('app.addEffectStatesEffectCounts')}</small></span></div>}
        </div>
      </div>
    }
    if (node.type === 'effectCount') {
      const updateCount = (changes: Partial<ConditionEffectCountNode>) => mutateConditionNode(node.id, (current) => current.type === 'effectCount' ? { ...current, ...changes } : current)
      const selectedHero = heroByRuntimeId(heroes, Number(node.heroTypeId))
      const selectedPosition = node.teamPosition || String(team.slice(0, teamSize).findIndex((id) => id === Number(node.heroTypeId)) + 1)
      const selection = node.target === 'ally' ? `ally:${selectedPosition}` : node.target
      return <div className={`effect-count-condition-row condition-tree-leaf${node.negate ? ' negated' : ''}`} key={node.id}>
        <span className="effect-condition-index">{path}</span>
        <label className="field"><span>{tr('app.countOn')}</span><select aria-label={tr('app.effectCountTarget', { path })} value={selection} onChange={(event) => {
          const [target, rawPosition] = event.target.value.split(':')
          const position = Number(rawPosition)
          updateCount({ target: target as ConditionEffectCountNode['target'], teamPosition: target === 'ally' ? rawPosition : '', heroTypeId: target === 'ally' ? String(team[position - 1]) : '' })
        }}>
          {bossMode === 'hydra' ? <><option value="bossPriority">{tr('app.theRuleSPriorityHead')}</option><option value="bossAny">{tr('app.anyHeadOnTheField')}</option><option value="bossAll">{tr('app.allHeadsOnTheField')}</option></> : <option value="boss">{tr('app.chimeraBoss')}</option>}
          {node.target === 'ally' && !team.slice(0, teamSize).includes(Number(node.heroTypeId)) && <option value={selection}>{tr('app.savedChampion', { heroTypeId: node.heroTypeId })}</option>}
          {team.slice(0, teamSize).map((id, index) => id > 0 && <option key={index} value={`ally:${index + 1}`}>{index + 1}. {heroByRuntimeId(heroes, id)?.name ?? tr('app.champion3', { id })}</option>)}
        </select></label>
        <label className="field"><span>{tr('app.countWhat')}</span><select value={node.polarity} onChange={(event) => updateCount({ polarity: event.target.value as ConditionEffectCountNode['polarity'] })}><option value="all">{tr('app.allEffects')}</option><option value="buff">{tr('app.buffsOnly')}</option><option value="debuff">{tr('app.debuffsOnly')}</option></select></label>
        <label className="effect-turn-field"><span>{tr('app.countAtLeast')}</span><input aria-label={tr('app.effectCountMinimum', { path })} type="text" inputMode="numeric" value={node.countAtLeast} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCount({ countAtLeast: event.target.value })} placeholder={tr('app.any')} /></label>
        <label className="effect-turn-field"><span>{tr('app.countAtMost')}</span><input aria-label={tr('app.effectCountMaximum', { path })} type="text" inputMode="numeric" value={node.countAtMost} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCount({ countAtMost: event.target.value })} placeholder={tr('app.any')} /></label>
        <div className="condition-leaf-actions"><button type="button" className={node.negate ? 'condition-negate active' : 'condition-negate'} title={tr('app.negateThisCondition')} onClick={() => updateCount({ negate: !node.negate })}>NOT</button><button type="button" className="effect-condition-delete" aria-label={tr('app.removeEffectCountCondition', { path })} onClick={() => removeConditionNode(node.id)}><Trash2 size={15} /></button></div>
        <p className="effect-count-hint">{node.target === 'ally' && selectedHero && <span data-i18n-skip>{selectedHero.name} · </span>}<span>{tr('app.eachTargetIsCountedOn')}</span></p>
      </div>
    }
    if (node.type === 'heroState') {
      const selectedPosition = Number(node.teamPosition)
      const selectedHeroTypeId = Number(node.heroTypeId)
      const selectedHero = heroByRuntimeId(heroes, selectedHeroTypeId)
      const updateCondition = (changes: Partial<ConditionHeroStateNode>) => mutateConditionNode(node.id, (current) => current.type === 'heroState' ? { ...current, ...changes } : current)
      const savedHeroOutsideTeam = selectedHeroTypeId > 0 && (!Number.isInteger(selectedPosition) || team[selectedPosition - 1] !== selectedHeroTypeId)
      return <div className={`hero-state-condition-row condition-tree-leaf${node.negate ? ' negated' : ''}`} key={node.id}>
        <span className="effect-condition-index">{path}</span>
        <HeroAvatar hero={selectedHero} size="sm" />
        <label className="field"><span>{tr('app.specificChampion')}</span><select value={node.teamPosition} onChange={(event) => { const nextPosition = Number(event.target.value); const nextHeroTypeId = team[nextPosition - 1] ?? 0; updateCondition({ teamPosition: event.target.value, heroTypeId: nextHeroTypeId ? String(nextHeroTypeId) : '' }) }}>{savedHeroOutsideTeam && <option value={node.teamPosition}>{tr('app.savedChampion', { heroTypeId: node.heroTypeId })}</option>}{team.slice(0, teamSize).map((typeId, teamIndex) => { const teamHero = heroByRuntimeId(heroes, typeId); return typeId > 0 ? <option key={`${typeId}-${teamIndex}`} value={teamIndex + 1}>{teamIndex + 1}. {teamHero?.name ?? tr('app.champion4', { typeId })}</option> : null })}</select></label>
        <div className="hero-state-toggle" role="group" aria-label={tr('app.championAliveCondition', { path })}><button type="button" className={node.state === 'alive' ? 'active' : ''} onClick={() => updateCondition({ state: 'alive' })}>{tr('app.mustBeAlive')}</button><button type="button" className={node.state === 'dead' ? 'active dead' : ''} onClick={() => updateCondition({ state: 'dead' })}>{tr('app.mustBeDead')}</button></div>
        <div className="condition-leaf-actions"><button type="button" className={node.negate ? 'condition-negate active' : 'condition-negate'} title={tr('app.negateThisCondition')} onClick={() => updateCondition({ negate: !node.negate })}>NOT</button><button type="button" className="effect-condition-delete" aria-label={tr('app.removeChampionAliveCondition', { path })} onClick={() => removeConditionNode(node.id)}><Trash2 size={15} /></button></div>
      </div>
    }
    if (node.type === 'skillCooldown') {
      const selectedHeroTypeId = Number(node.heroTypeId)
      const selectedHero = heroByRuntimeId(heroes, selectedHeroTypeId)
      const selectedCooldownSkill = selectedHero?.skills.find((skill) => skill.typeId === Number(node.skillTypeId))
      const updateCondition = (changes: Partial<ConditionCooldownNode>) => mutateConditionNode(node.id, (current) => current.type === 'skillCooldown' ? { ...current, ...changes } : current)
      return <div className={`cooldown-condition-row condition-tree-leaf${node.negate ? ' negated' : ''}`} key={node.id}>
        <span className="effect-condition-index">{path}</span>
        <HeroAvatar hero={selectedHero} size="sm" />
        <label className="field"><span>{tr('app.specificChampion')}</span><select value={node.heroTypeId} onChange={(event) => { const nextHeroTypeId = Number(event.target.value); const nextHero = heroByRuntimeId(heroes, nextHeroTypeId); const nextSkill = nextHero?.skills.find((skill) => typeof skill.typeId === 'number'); updateCondition({ heroTypeId: event.target.value, skillTypeId: nextSkill?.typeId ? String(nextSkill.typeId) : '' }) }}>{team.slice(0, teamSize).map((typeId, teamIndex) => { const teamHero = heroByRuntimeId(heroes, typeId); return typeId > 0 ? <option key={`${typeId}-${teamIndex}`} value={typeId}>{teamIndex + 1}. {teamHero?.name ?? tr('app.champion4', { typeId })}</option> : null })}</select></label>
        <HoverCard focusable={false} content={selectedCooldownSkill ? <SkillDetailCard hero={selectedHero} skill={selectedCooldownSkill} /> : null}>
          <SkillIcon hero={selectedHero} skill={selectedCooldownSkill} slot={selectedCooldownSkill?.slot} /></HoverCard>
        <label className="field"><span>{tr('app.specificSkill')}</span><select value={node.skillTypeId} onChange={(event) => updateCondition({ skillTypeId: event.target.value })}>{(selectedHero?.skills ?? []).filter((skill) => typeof skill.typeId === 'number').map((skill) => <option key={skill.typeId} value={skill.typeId}>{skill.name || tr('app.skill', { slot: skill.slot })}</option>)}</select></label>
        <label className="effect-turn-field"><span>{tr('app.cooldownAtLeast')}</span><input type="text" inputMode="numeric" value={node.turnsAtLeast} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtLeast: event.target.value })} placeholder={tr('app.any')} /></label>
        <label className="effect-turn-field"><span>{tr('app.cooldownAtMost')}</span><input type="text" inputMode="numeric" value={node.turnsAtMost} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtMost: event.target.value })} placeholder={tr('app.any')} /></label>
        <div className="condition-leaf-actions"><button type="button" className={node.negate ? 'condition-negate active' : 'condition-negate'} title={tr('app.negateThisCondition')} onClick={() => updateCondition({ negate: !node.negate })}>NOT</button><button type="button" className="effect-condition-delete" aria-label={tr('app.removeCooldownCondition', { path })} onClick={() => removeConditionNode(node.id)}><Trash2 size={15} /></button></div>
      </div>
    }
    const selectedHeroTypeId = Number(node.heroTypeId)
    const selectedTeamHero = heroByRuntimeId(heroes, selectedHeroTypeId)
    const savedHeroOutsideTeam = node.target === 'ally' && selectedHeroTypeId > 0 && !team.slice(0, teamSize).includes(selectedHeroTypeId)
    const updateCondition = (changes: Partial<ConditionEffectNode>) => mutateConditionNode(node.id, (current) => current.type === 'effect' ? { ...current, ...changes } : current)
    return <div className={`effect-condition-row condition-tree-leaf${node.negate ? ' negated' : ''}`} key={node.id}>
      <span className="effect-condition-index">{path}</span>
      <div className="effect-condition-target">
        {node.target === 'ally' ? <HeroAvatar hero={selectedTeamHero} size="sm" /> : <span className="effect-target-boss"><Crosshair size={16} /></span>}
        <span><select aria-label={tr('app.effectConditionTarget', { path })} value={node.target === 'ally' ? `ally:${node.heroTypeId}` : node.target} onChange={(event) => { const [targetType, rawHeroTypeId = ''] = event.target.value.split(':'); const bossTarget = targetType === 'bossAll' || targetType === 'bossAny' || targetType === 'bossPriority' ? targetType : 'boss'; updateCondition({ target: targetType === 'ally' ? 'ally' : bossTarget, heroTypeId: targetType === 'ally' ? rawHeroTypeId : '' }) }}>{bossMode === 'hydra' ? <><option value="bossPriority">{tr('app.theRuleSPriorityHead')}</option><option value="bossAny">{tr('app.anyHeadOnTheField')}</option><option value="bossAll">{tr('app.allHeadsOnTheField')}</option></> : <option value="boss">{tr('app.chimeraBoss')}</option>}{savedHeroOutsideTeam && <option value={`ally:${node.heroTypeId}`}>{tr('app.savedChampion', { heroTypeId: node.heroTypeId })}</option>}{team.slice(0, teamSize).map((typeId, teamIndex) => { const teamHero = heroByRuntimeId(heroes, typeId); return typeId > 0 ? <option key={`${typeId}-${teamIndex}`} value={`ally:${typeId}`}>{teamIndex + 1}. {teamHero?.name ?? tr('app.champion4', { typeId })}</option> : null })}</select></span>
      </div>
      <div className="effect-presence-toggle" role="group" aria-label={tr('app.effectConditionState', { path })}><button type="button" className={node.presence === 'has' ? 'active' : ''} onClick={() => updateCondition({ presence: 'has' })}>{tr('app.mustHave')}</button><button type="button" className={node.presence === 'missing' ? 'active missing' : ''} onClick={() => updateCondition({ presence: 'missing', turnsAtLeast: '', turnsAtMost: '' })}>{tr('app.mustLack')}</button></div>
      <EffectPicker required effects={effects} value={node.token} onValue={(token) => updateCondition({ token })} />
      <label className="effect-turn-field"><span>{tr('app.remainingAtLeast')}</span><input type="text" inputMode="numeric" value={node.turnsAtLeast} disabled={!node.token || node.presence === 'missing'} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtLeast: event.target.value })} placeholder={node.presence === 'missing' ? tr('app.nA') : tr('app.any')} /></label>
      <label className="effect-turn-field"><span>{tr('app.remainingAtMost')}</span><input type="text" inputMode="numeric" value={node.turnsAtMost} disabled={!node.token || node.presence === 'missing'} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && updateCondition({ turnsAtMost: event.target.value })} placeholder={node.presence === 'missing' ? tr('app.nA') : tr('app.any')} /></label>
      <div className="condition-leaf-actions"><button type="button" className={node.negate ? 'condition-negate active' : 'condition-negate'} title={tr('app.negateThisCondition')} onClick={() => updateCondition({ negate: !node.negate })}>NOT</button><button type="button" className="effect-condition-delete" aria-label={tr('app.removeEffectCondition', { path })} onClick={() => removeConditionNode(node.id)}><Trash2 size={15} /></button></div>
    </div>
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content rule-dialog">
          <div className="dialog-heading">
            <div><Dialog.Title>{initial ? tr('app.editStrategyRule') : tr('app.addStrategyRule')}</Dialog.Title><Dialog.Description className="sr-only">{tr('app.setUpAnActionRule')}</Dialog.Description></div>
            <Dialog.Close className="icon-button" aria-label={tr('app.close')}><X size={19} /></Dialog.Close>
          </div>
          <div className="form-grid">
            <label className="field span-2"><span>{tr('app.ruleName')}</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder={tr('app.leaveBlankToGenerateOne')} /></label>
            <div className="field span-2">
              <span>{tr('app.actingChampion')}</span>
              <div className="hero-chooser">
                <label className="hero-search"><Search size={15} /><input value={heroSearch} onChange={(event) => setHeroSearch(event.target.value)} placeholder={tr('app.searchChampionName')} /></label>
                {ruleKind === 'strict' && <button type="button" aria-pressed={allHeroes} className={allHeroes ? 'all-heroes active' : 'all-heroes'} onClick={() => { const next = !allHeroes; setAllHeroes(next); setActionType(next && bossMode === 'chimera' ? 'executeTrialRecipe' : 'cast') }}><Users size={15} />{allHeroes ? tr('app.stopUsingAnyChampion') : tr('app.anyActingChampion')}</button>}
              </div>
              {!allHeroes && <div className="hero-library">
                {visibleHeroes.map((item) => <button type="button" key={item.typeId} className={item.typeId === heroId ? 'hero-option active' : 'hero-option'} onClick={() => { setHeroId(item.typeId); setAllHeroes(false); setSkillTypeId(undefined); setSlot(1); setHeroForm('any'); resetDefaultPoliciesForHero(item); setActionType(ruleKind === 'default' ? 'defaultSkillPriority' : 'cast') }}><HeroAvatar hero={item} size="sm" /><span><strong>{item.name}</strong><small>{item.isMetamorph ? tr('app.mythicalTwoForms') : tr('app.activeSkillCount', { skillsCount: item.skills.length })}</small></span></button>)}
                {!visibleHeroes.length && <span className="library-empty">{tr('app.noChampionFound')}</span>}
              </div>}
            </div>
            <div className="rule-kind span-2">
              <span className="mode-heading">{tr('app.ruleType')}</span>
              <button type="button" className={ruleKind === 'strict' ? 'active' : ''} onClick={() => { setRuleKind('strict'); setActionType('cast') }}><ShieldCheck size={16} /><span><strong>{tr('app.strictRule')}</strong></span></button>
              <button type="button" className={ruleKind === 'default' ? 'active' : ''} onClick={() => { setRuleKind('default'); setAllHeroes(false); setActionType('defaultSkillPriority'); if (!prioritySkillIds.length) setPrioritySkillIds(defaultHeroPriorityIds(hero)) }}><Sparkles size={16} /><span><strong>{tr('app.defaultSkillRule')}</strong></span></button>
            </div>
            {bossMode === 'chimera' && ruleKind === 'strict' && <fieldset className="field span-2"><legend>{tr('app.chimeraForm')}</legend><div className="chip-group">{ALL_FORMS.map((form) => <button type="button" key={form} className={forms.includes(form) ? 'chip active' : 'chip'} onClick={() => setForms((current) => current.includes(form) ? current.filter((item) => item !== form) : [...current, form])}>{formLabel(form)}</button>)}</div></fieldset>}
            {hero?.isMetamorph && <fieldset className="field span-2"><legend>{tr('app.championFormAndSkillSet')}</legend><div className="chip-group"><button type="button" className={heroForm === 'any' ? 'chip active' : 'chip'} onClick={() => setHeroForm('any')}>{tr('app.showBothSets')}</button><button type="button" className={heroForm === 'original' ? 'chip active' : 'chip'} onClick={() => { setHeroForm('original'); setSkillTypeId(undefined); setSlot(1) }}>{tr('app.baseForm')}</button><button type="button" className={heroForm === 'transformed' ? 'chip active' : 'chip'} onClick={() => { setHeroForm('transformed'); setSkillTypeId(undefined); setSlot(1) }}>{tr('app.alternateForm')}</button></div></fieldset>}
            {ruleKind === 'default' && <fieldset className="skill-policy span-2">
              <legend>{tr('app.defaultSkillOrderAndTargets')}</legend>
              {bossMode === 'chimera' && <div className="default-form-policy-tabs"><span>{tr('app.setSeparatelyForEachChimera')}</span><div className="chip-group">{ALL_FORMS.map((form) => <button type="button" key={form} className={defaultPolicyForm === form ? 'chip active' : 'chip'} onClick={() => setDefaultPolicyForm(form)}>{formLabel(form)}</button>)}</div></div>}
              <div className="first-turn-policy">
                <span><strong>{bossMode === 'chimera' ? tr('app.championSFirstTurnSkill') : tr('app.championSFirstTurnSkill2')}</strong><small>{bossMode === 'chimera' ? tr('app.runsBeforeStrictRulesEach') : tr('app.runsBeforeStrictRules')}</small></span>
                <select value={firstTurnSkillId ?? ''} onChange={(event) => setFirstTurnSkillId(event.target.value ? Number(event.target.value) : undefined)}>
                  <option value="">{tr('app.none')}</option>
                  {allHeroSkills.filter((skill): skill is Skill & { typeId: number } => typeof skill.typeId === 'number').map((skill) => <option key={`${skill.formIndex ?? 0}-${skill.typeId}`} value={skill.typeId}>{skill.name || tr('app.skill', { slot: skill.slot })}{hero?.isMetamorph ? ` · ${(skill.formIndex ?? 0) === 1 ? tr('app.alternateForm') : tr('app.baseForm')}` : ''}</option>)}
                </select>
              </div>
              <div className="skill-policy-list">{prioritySkillIds.map((id, index) => {
                const skill = hero?.skills.find((item) => item.typeId === id)
                if (!skill) return null
                const blocked = blockedSkillIds.includes(id)
                const firstTurnSelected = firstTurnSkillId === id
                const reserved = reservedSkillIds.has(id)
                const allowedRank = prioritySkillIds.slice(0, index + 1).filter((value) => !blockedSkillIds.includes(value)).length
                const skillTarget = skill.isTransform ? { type: 'self' } : (defaultSkillTargets[id] ?? { type: 'auto' })
                const skillTargetType = typeof skillTarget.type === 'string' ? skillTarget.type : 'auto'
                return <div className={`skill-policy-row${blocked ? ' blocked' : ''}`} key={id}>
                  <span className="skill-rank">{blocked ? '—' : allowedRank}</span>
                  <HoverCard className="skill-policy-skill" inline={false} focusable={false} content={<SkillDetailCard hero={hero} skill={skill} />}>
                    <SkillIcon hero={hero} skill={skill} slot={skill.slot} />
                    <span className="skill-policy-copy">
                      <strong>{skill.name || tr('app.skill', { slot: skill.slot })}</strong>
                      <small>{hero?.isMetamorph ? `${(skill.formIndex ?? 0) === 1 ? tr('app.alternateForm') : tr('app.baseForm')} · ` : ''}{tr('app.skill', { slot: skill.slot })}{skill.defaultCooldown ? tr('app.cooldownSuffix', { defaultCooldown: skill.defaultCooldown }) : tr('app.noCooldownSuffix')}{skill.isTransform ? tr('app.formSwitchSuffix') : ''}</small>
                      {reserved && <em className="reserved-badge">{tr('app.reservedByAStrictRule')}</em>}
                    </span>
                  </HoverCard>
                  <div className="skill-policy-target">
                    <select aria-label={tr('app.targetOf', { name: skill.name || tr('app.skill', { slot: skill.slot }) })} value={skillTargetType} disabled={(blocked && !firstTurnSelected) || Boolean(skill.isTransform)} onChange={(event) => setDefaultSkillTargetType(id, event.target.value)}>
                      <option value="auto">{tr('app.automaticLegalTarget')}</option>
                      {bossMode === 'chimera'
                        ? <option value="boss">{tr('app.chimeraBoss')}</option>
                        : <><option value="hydraHeadPriority">{tr('app.byHeadTypePriority')}</option><option value="devouringHead">{tr('app.devouringHead')}</option><option value="exposedNeck">{tr('app.exposedNeck')}</option><option value="lowestDefenseBoss">{tr('app.lowestDefHead')}</option><option value="lowestHpBoss">{tr('app.lowestHpHead')}</option></>}
                      <option value="self">{tr('app.self')}</option>
                      <option value="lowestHpAlly">{tr('app.lowestHpAlly2')}</option>
                      <option value="allyPosition">{tr('app.preparedTeamSlot')}</option>
                    </select>
                    {skillTargetType === 'allyPosition' && <select aria-label={tr('app.teamTargetOf', { name: skill.name || tr('app.skill', { slot: skill.slot }) })} value={typeof skillTarget.position === 'number' ? skillTarget.position : 1} disabled={blocked && !firstTurnSelected} onChange={(event) => setDefaultSkillTargetPosition(id, Number(event.target.value))}>{Array.from({ length: teamSize }, (_, teamIndex) => <option key={teamIndex + 1} value={teamIndex + 1}>{teamIndex + 1}. {teamHeroes[teamIndex]?.name ?? tr('app.notRead')}</option>)}</select>}
                    {bossMode === 'hydra' && skillTargetType === 'hydraHeadPriority' && <button type="button" className={defaultHydraTargetSkillId === id ? 'active' : ''} disabled={blocked && !firstTurnSelected} onClick={() => setDefaultHydraTargetSkillId(id)}>{tr('app.headOrder', { headTypeIdsCount: asNumberArray(skillTarget.headTypeIds).length || '' })}</button>}
                  </div>
                  <div className="skill-policy-actions"><button type="button" title={tr('app.raiseSkillPriority')} disabled={index === 0} onClick={() => setPrioritySkillIds((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title={tr('app.lowerSkillPriority')} disabled={index === prioritySkillIds.length - 1} onClick={() => setPrioritySkillIds((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" className={blocked ? 'blocked-toggle active' : 'blocked-toggle'} onClick={() => setBlockedSkillIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])}>{blocked ? tr('app.allowInTheDefaultRule') : tr('app.blockInTheDefaultRule')}</button></div>
                </div>
              })}</div>
              {bossMode === 'hydra' && defaultHydraTargetSkillId !== undefined && defaultSkillTargets[defaultHydraTargetSkillId]?.type === 'hydraHeadPriority' && <div className="head-priority-builder default-skill-head-priority">
                <div className="head-priority-heading"><strong>{defaultHydraTargetSkill?.name || tr('app.skill2')}{tr('app.headPrioritySuffix')}</strong><em>{defaultHydraHeadPriorityIds.length ? tr('app.defaultHeadTypeCount', { defaultHydraHeadPriorityIdsCount: defaultHydraHeadPriorityIds.length }) : tr('app.automaticFallback')}</em></div>
                <div className="head-type-library">{hydraHeads.map((head) => { const rank = defaultHydraHeadPriorityIds.indexOf(head.typeId); return <button type="button" key={head.typeId} className={rank >= 0 ? 'head-type-option active' : 'head-type-option'} onClick={() => { if (rank < 0) updateDefaultHydraHeadPriority((current) => [...current, head.typeId]) }}><HydraHeadIcon head={head} size="md" /><span><strong>{hydraHeadDisplayName(head)}</strong>{rank >= 0 && <small>{tr('app.priority', { value: rank + 1 })}</small>}</span>{rank >= 0 && <em>{rank + 1}</em>}</button> })}</div>
                {defaultHydraHeadPriorityIds.length > 0 && <div className="head-priority-list">{defaultHydraHeadPriorityIds.map((typeId, index) => { const head = hydraHeads.find((item) => item.typeId === typeId); return <div className="head-priority-row" key={typeId}><span className="skill-rank">{index + 1}</span><HydraHeadIcon head={head ?? { typeId, name: tr('app.hydraHead', { typeId }) }} size="sm" /><span><strong>{head ? hydraHeadDisplayName(head) : tr('app.hydraHead', { typeId })}</strong></span><div><button type="button" title={tr('app.raisePriority')} disabled={index === 0} onClick={() => updateDefaultHydraHeadPriority((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title={tr('app.lowerPriority')} disabled={index === defaultHydraHeadPriorityIds.length - 1} onClick={() => updateDefaultHydraHeadPriority((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" title={tr('app.remove')} onClick={() => updateDefaultHydraHeadPriority((current) => current.filter((value) => value !== typeId))}><Trash2 size={15} /></button></div></div> })}</div>}
              </div>}
            </fieldset>}
            {ruleKind === 'strict' && <>
            <div className="action-mode span-2">
              <button type="button" className={actionType === 'cast' || actionType === 'transform' ? 'active' : ''} onClick={() => { setAllHeroes(false); setActionType(selectedSkill?.isTransform ? 'transform' : 'cast') }}><Zap size={16} /><span><strong>{tr('app.specifiedSkill')}</strong></span></button>
              {bossMode === 'chimera' && <button type="button" className={actionType === 'executeTrialRecipe' ? 'active' : ''} onClick={() => setActionType('executeTrialRecipe')}><Sparkles size={16} /><span><strong>{tr('app.automaticTrialDecisions')}</strong><small>{tr('app.coordinatesTheTeamByTrial')}</small></span></button>}
              {initial?.action?.type === 'maintainEffects' && <button type="button" className={actionType === 'maintainEffects' ? 'active' : ''} onClick={() => setActionType('maintainEffects')}><ShieldCheck size={16} /><span><strong>{tr('app.maintainEffects')}</strong></span></button>}
            </div>
            {(actionType === 'cast' || actionType === 'transform') && <>
              <fieldset className="skill-picker span-2">
                <legend>{tr('app.chooseTheSkill')}</legend>
                <div className="skill-library">{skills.map((skill) => <HoverCard key={`${skill.formIndex ?? 0}-${skill.slot}-${skill.typeId ?? ''}`} className="skill-option-anchor" inline={false} focusable={false} content={<SkillDetailCard hero={hero} skill={skill} />}><button type="button" className={selectedSkill?.typeId === skill.typeId ? `skill-option active${skill.isTransform ? ' transform' : ''}` : `skill-option${skill.isTransform ? ' transform' : ''}`} onClick={() => { setSkillTypeId(skill.typeId); setSlot(skill.slot); setActionType(skill.isTransform ? 'transform' : 'cast') }}><SkillIcon hero={hero} skill={skill} slot={skill.slot} /><span><strong>{skill.name || tr('app.skill', { slot: skill.slot })}</strong><small>{hero?.isMetamorph ? `${(skill.formIndex ?? 0) === 1 ? tr('app.alternateForm') : tr('app.baseForm')} · ` : ''}{tr('app.skill', { slot: skill.slot })}{skill.defaultCooldown ? tr('app.cooldownSuffix', { defaultCooldown: skill.defaultCooldown }) : tr('app.noCooldownSuffix')}</small></span></button></HoverCard>)}</div>
              </fieldset>
              {actionType === 'cast' && <fieldset className="target-picker span-2">
                <legend>{tr('app.skillTarget')}</legend>
                <div className="target-quick">
                  {bossMode === 'chimera'
                    ? <button type="button" className={target === 'boss' ? 'active' : ''} onClick={() => setTarget('boss')}><Crosshair size={16} />{tr('app.chimeraBoss')}</button>
                    : <><button type="button" className={target === 'hydraHeadPriority' ? 'active' : ''} onClick={() => setTarget('hydraHeadPriority')}><Waves size={16} />{tr('app.byHeadTypePriority')}</button><button type="button" className={target === 'devouringHead' ? 'active' : ''} onClick={() => setTarget('devouringHead')}><Crosshair size={16} />{tr('app.devouringHead')}</button><button type="button" className={target === 'exposedNeck' ? 'active' : ''} title={tr('app.withSeveralExposedNecksThe')} onClick={() => setTarget('exposedNeck')}><Zap size={16} />{tr('app.exposedNeck')}</button><button type="button" className={target === 'lowestDefenseBoss' ? 'active' : ''} title={tr('app.theHeadWithTheLowest')} onClick={() => setTarget('lowestDefenseBoss')}><ShieldCheck size={16} />{tr('app.lowestDefHead')}</button><button type="button" className={target === 'lowestHpBoss' ? 'active' : ''} onClick={() => setTarget('lowestHpBoss')}><Activity size={16} />{tr('app.lowestHpHead')}</button></>}
                  <button type="button" className={target === 'self' ? 'active' : ''} onClick={() => setTarget('self')}><HeroAvatar hero={hero} size="sm" />{tr('app.self')}</button><button type="button" className={target === 'lowestHpAlly' ? 'active' : ''} onClick={() => setTarget('lowestHpAlly')}><Activity size={16} />{tr('app.lowestHpAlly2')}</button>
                </div>
                {bossMode === 'hydra' && target === 'hydraHeadPriority' && <div className="head-priority-builder">
                  <div className="head-priority-heading"><strong>{tr('app.headPriority')}</strong><em>{headPriorityIds.length ? tr('app.headTypeCount', { headPriorityIdsCount: headPriorityIds.length }) : tr('app.automaticFallback')}</em></div>
                  <div className="head-type-library">{hydraHeads.map((head) => { const rank = headPriorityIds.indexOf(head.typeId); return <button type="button" key={head.typeId} className={rank >= 0 ? 'head-type-option active' : 'head-type-option'} onClick={() => { setTarget('hydraHeadPriority'); if (rank < 0) setHeadPriorityIds((current) => [...current, head.typeId]) }}><HydraHeadIcon head={head} size="md" /><span><strong>{hydraHeadDisplayName(head)}</strong><small>{rank >= 0 ? tr('app.priority', { value: rank + 1 }) : tr('app.addAsPriorityTarget')}</small></span>{rank >= 0 && <em>{rank + 1}</em>}</button> })}</div>
                  {headPriorityIds.length > 0 && <div className="head-priority-list">{headPriorityIds.map((typeId, index) => { const head = hydraHeads.find((item) => item.typeId === typeId); return <div className="head-priority-row" key={typeId}><span className="skill-rank">{index + 1}</span><HydraHeadIcon head={head ?? { typeId, name: tr('app.hydraHead', { typeId }) }} size="sm" /><span><strong>{head ? hydraHeadDisplayName(head) : tr('app.hydraHead', { typeId })}</strong></span><div><button type="button" title={tr('app.raisePriority')} disabled={index === 0} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next })}><ArrowUp size={15} /></button><button type="button" title={tr('app.lowerPriority')} disabled={index === headPriorityIds.length - 1} onClick={() => setHeadPriorityIds((current) => { const next = [...current]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next })}><ArrowDown size={15} /></button><button type="button" title={tr('app.remove')} onClick={() => setHeadPriorityIds((current) => current.filter((value) => value !== typeId))}><Trash2 size={15} /></button></div></div> })}</div>}
                </div>}
                <div className="ally-target-heading"><Users size={15} /><span><strong>{tr('app.allyTarget')}</strong></span></div>
                <div className={`position-targets position-targets-${teamSize}`}>{Array.from({ length: teamSize }, (_, index) => { const member = teamHeroes[index]; const position = index + 1; return <button type="button" key={position} className={target === 'allyPosition' && targetPosition === position ? 'position-target active' : 'position-target'} onClick={() => { setTarget('allyPosition'); setTargetPosition(position) }}><span className="position-number">{position}</span><HeroAvatar hero={member} size="md" /><span><strong>{member?.name ?? tr('app.notRead')}</strong></span></button> })}</div>
              </fieldset>}
              <div className="skill-detail span-2"><SkillIcon hero={hero} skill={selectedSkill} slot={slot} /><span><strong>{selectedSkill?.name || tr('app.skill', { slot })}{selectedSkill?.isTransform ? tr('app.formSwitchSkillSuffix') : ''}</strong><em>{gameDescription(selectedSkill?.description) || selectedSkill?.effectSummary || tr('app.skillDescriptionNotReadYet')}</em></span></div>
            </>}
            <fieldset className="condition-builder span-2">
              <legend>{tr('app.commonTriggers')}</legend>
              <div className="condition-grid">
                {bossMode === 'chimera' && <label className="field"><span>{tr('app.bossTurnAtLeast')}</span><input type="text" inputMode="numeric" value={turnMin} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && setTurnMin(event.target.value)} placeholder={tr('app.any')} /></label>}
                {bossMode === 'chimera' && <label className="field"><span>{tr('app.bossTurnAtMost')}</span><input type="text" inputMode="numeric" value={turnMax} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && setTurnMax(event.target.value)} placeholder={tr('app.any')} /></label>}
                {bossMode === 'chimera' && <label className="field"><span>{tr('app.turnsToFormChangeAtMost')}</span><input type="text" inputMode="numeric" value={switchWithin} onFocus={selectNumericInput} onChange={(event) => /^\d*$/.test(event.target.value) && Number(event.target.value || 0) <= 5 && setSwitchWithin(event.target.value)} placeholder={tr('app.any')} /></label>}
                {bossMode === 'chimera' && <label className="field"><span>{tr('app.nextForm2')}</span><select value={nextForm} onChange={(event) => setNextForm(event.target.value)}><option value="">{tr('app.any')}</option>{ALL_FORMS.map((form) => <option key={form} value={form}>{formLabel(form)}</option>)}</select></label>}
                <label className="field"><span>{tr('app.currentDamageAtLeast')}</span><input type="text" inputMode="decimal" value={damageMin} onFocus={selectNumericInput} onChange={(event) => /^\d*(?:\.\d*)?$/.test(event.target.value) && setDamageMin(event.target.value)} placeholder={tr('app.eG')} /></label>
                {bossMode === 'chimera' && <label className="field trial-active-condition"><span>{tr('app.onlyWhileThisTrialIs')}<small>{tr('app.theRuleIsIgnoredWhen')}</small></span><select value={eligibleTrialId || ''} onChange={(event) => setEligibleTrialId(Number(event.target.value) || 0)}><option value="">{tr('app.anyTrialState')}</option>{eligibleTrialId > 0 && !trials.some((trial) => trial.id === eligibleTrialId) && <option value={eligibleTrialId}>{tr('app.savedTrial', { eligibleTrialId })}</option>}{trials.map((trial) => <option key={trial.id} value={trial.id}>{formLabel(trial.form ?? '') ?? trial.form ?? tr('app.chimera')} · {trialLevel(trial.difficulty ?? '') ?? trial.difficulty ?? tr('app.trial2')} · {gameDescription(trial.description) || tr('app.trial3', { id: trial.id })}</option>)}</select></label>}
              </div>
            </fieldset>
            <fieldset className="effect-condition-builder condition-tree-builder span-2">
              <legend>{tr('app.conditionLogic')}</legend>
              <div className="effect-condition-heading">
                <span><strong>{conditionLeafCount(conditionTree) ? tr('app.conditionsAdded', { conditionTree: conditionLeafCount(conditionTree) }) : tr('app.addAsNeeded')}</strong><small>{tr('app.nestAndAndOrFreely')}</small></span>
              </div>
              {renderConditionNode(conditionTree, 0, '')}
            </fieldset>
            <details className="advanced-conditions span-2"><summary>{tr('app.fullConditionData')}</summary><label className="field"><textarea rows={7} value={advanced} onChange={(event) => setAdvanced(event.target.value)} spellCheck={false} /></label></details>
            </>}
          </div>
          {error && <div className="inline-error">{error}</div>}
          <div className="dialog-footer actions-only"><div><Dialog.Close className="button ghost">{tr('app.cancel')}</Dialog.Close><button className="button primary" onClick={commit}>{tr('app.saveRule')}</button></div></div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function App() {
  const [language, setLanguage] = useState<UiLanguage>(getInitialLocale)
  setActiveLocale(language)
  const [bossMode, setBossMode] = useState<BossMode>('chimera')
  const [data, setData] = useState<Bootstrap | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedPid, setSelectedPid] = useState<number | undefined>()
  const [config, setConfigState] = useState<Strategy>({})
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const [connectionError, setConnectionError] = useState('')
  const [drafts] = useState(() => {
    try { return new DraftStore<Strategy>(window.localStorage) } catch { return new DraftStore<Strategy>() }
  })
  const editorRef = useRef({ key: '', mode: 'chimera' as BossMode, value: {} as Strategy })
  const strategyAccountRef = useRef<StrategyAccount>({ key: null, name: null })
  const [strategyAccount, setStrategyAccountState] = useState<StrategyAccount>({ key: null, name: null })
  const [strategyAccountChanged, setStrategyAccountChanged] = useState(0)
  function setStrategyAccount(account: StrategyAccount | undefined) {
    const next = account ?? { key: null, name: null }
    strategyAccountRef.current = next
    setStrategyAccountState(next)
  }
  // Drafts belong to one account's strategy group (the same ids exist in every account).
  const draftKey = (mode: BossMode, id: string) =>
    `${strategyAccountRef.current.key ? `${strategyAccountRef.current.key}:` : ''}${mode}:${id}`
  const loadScope = useRef(new RequestScope())
  const pollScope = useRef(new RequestScope())
  const catalogRevisionRef = useRef('')
  const pollFenceRef = useRef(0)
  const controllerRef = useRef<ControllerState>({ running: false, status: tr('app.stopped'), logs: [] })
  const [loadingContext, setLoadingContext] = useState(false)
  function setConfig(update: SetStateAction<Strategy>) {
    const next = typeof update === 'function' ? update(editorRef.current.value) : update
    editorRef.current.value = next
    drafts.edit(editorRef.current.key, next)
    setConfigState(next)
  }
  function showDraft(mode: BossMode, id: string, value: Strategy, revision: string) {
    const key = draftKey(mode, id)
    const entry = drafts.open(key, value, revision)
    editorRef.current = { key, mode, value: entry.value }
    setConfigState(entry.value)
  }
  const [activeStrategyId, setActiveStrategyId] = useState('default')
  const [strategyProfiles, setStrategyProfiles] = useState<StrategyProfile[]>([])
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileDialog, setProfileDialog] = useState<'create' | 'rename' | null>(null)
  const [profileName, setProfileName] = useState('')
  // An action on a strategy group with unsaved changes asks first: save, discard or cancel.
  const [unsavedPrompt, setUnsavedPrompt] = useState<{ action: UnsavedAction; resolve: (choice: 'save' | 'discard' | 'cancel') => void } | null>(null)
  const [live, setLive] = useState<LiveState>({})
  const [controller, setControllerState] = useState<ControllerState>({ running: false, status: tr('app.stopped'), logs: [] })
  const setController = useCallback((update: SetStateAction<ControllerState>) => {
    const next = typeof update === 'function' ? update(controllerRef.current) : update
    controllerRef.current = next
    setControllerState(next)
  }, [])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [showLogs, setShowLogs] = useState(false)
  const [logsExpanded, setLogsExpanded] = useState(false)
  const [ruleSearch, setRuleSearch] = useState('')
  const strategyImportRef = useRef<HTMLInputElement | null>(null)
  const [trialOpen, setTrialOpen] = useState(false)
  const [hydraDevourRetryOpen, setHydraDevourRetryOpen] = useState(false)
  const [hydraForecasts, setHydraForecasts] = useState<HydraForecastSummary[]>([])
  const [chimeraSimulation, setChimeraSimulation] = useState<SimulationOverview>()
  const [hydraSimulation, setHydraSimulation] = useState<HydraSimulationOverview>()
  const [simulationContextRevision, setSimulationContextRevision] = useState(0)
  const [hydraReportId, setHydraReportId] = useState<string | null>(null)
  const [teamPreviewRevision, setTeamPreviewRevision] = useState('')
  const [teamPreview, setTeamPreview] = useState<TeamSnapshot | null>(null)
  const [teamDialog, setTeamDialog] = useState<'live' | 'reference' | null>(null)
  const [teamPickerOpen, setTeamPickerOpen] = useState(false)
  const [simulationSummary, setSimulationSummary] = useState<SimulationSummary | null>(null)
  const [simulationReportId, setSimulationReportId] = useState<string | null>(null)
  const [highlightRule, setHighlightRule] = useState<number | null>(null)
  const [ruleOpen, setRuleOpen] = useState(false)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [trialSearchDifficulty, setTrialSearchDifficulty] = useState<number>(5)

  useLayoutEffect(() => {
    saveLocale(language)
    document.title = APP_NAME
    const root = document.body
    return root ? installDocumentLocalization(root, language) : undefined
  }, [language])

  function changeLanguage(next: UiLanguage) {
    saveLocale(next)
    setLanguage(next)
    void api<{ language: UiLanguage }>('/api/preferences', {
      method: 'POST',
      body: JSON.stringify({ language: next }),
    }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
  }

  useEffect(() => {
    if (!token) return
    const session = new EventSource(`/api/session?token=${encodeURIComponent(token)}`)
    return () => session.close()
  }, [])

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (drafts.hasUnsaved()) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => { window.removeEventListener('beforeunload', warn); loadScope.current.invalidate(); pollScope.current.invalidate() }
  }, [drafts])

  const load = useCallback(async (pid?: number, quiet = false, requestedMode?: BossMode, discardKey?: string) => {
    loadScope.current.invalidate()
    pollScope.current.invalidate()
    setChimeraSimulation(undefined)
    setHydraSimulation(undefined)
    const ticket = loadScope.current.begin(30000)
    setLoading(true)
    setLoadingContext(true)
    setLive({ statusLabel: tr('app.refreshingState'), modeReady: false })
    setError('')
    try {
      const mode = requestedMode ?? 'chimera'
      const params = new URLSearchParams({ mode })
      if (pid) params.set('pid', String(pid))
      const next = await api<Bootstrap>(`/api/bootstrap?${params}`, { signal: ticket.signal })
      if (!ticket.current()) return
      setData(next)
      catalogRevisionRef.current = next.catalogRevision ?? ''
      if (isLocale(next.language)) setLanguage(next.language)
      setBossMode(next.bossMode)
      setStrategyAccount(next.strategyAccount)
      if (discardKey === draftKey(next.bossMode, next.activeStrategyId ?? 'default')) drafts.forget(discardKey)
      showDraft(next.bossMode, next.activeStrategyId ?? 'default', next.config, next.revision)
      setActiveStrategyId(next.activeStrategyId ?? 'default')
      setStrategyProfiles(next.strategyProfiles ?? [])
      setController(next.controller)
      setLive(next.state ?? {})
      setSelectedPid(next.selectedPid ?? next.processes[0]?.pid)
      setConnectionError('')
      const inferredDifficulty = next.state?.chimeraDifficultyId ?? configuredTrialDifficulty(next.config)
      if (inferredDifficulty) setTrialSearchDifficulty(inferredDifficulty)
    } catch (reason) {
      if (ticket.current()) {
        setError(reason instanceof Error ? reason.message : String(reason))
        setConnectionError(tr('app.stateRefreshFailedPleaseRetry'))
      }
    } finally {
      ticket.finish()
      if (ticket.current()) { setLoading(false); setLoadingContext(false) }
    }
    void quiet
  }, [drafts, setController])

  useEffect(() => { void load() }, [load])

  // The preparation-screen team is fetched only when the agent published a
  // new one or its battle stats finished computing.
  useEffect(() => {
    if (!teamPreviewRevision) {
      setTeamPreview(null)
      return
    }
    let cancelled = false
    api<{ preview: TeamSnapshot | null }>(`/api/team-preview${selectedPid ? `?pid=${selectedPid}` : ''}`)
      .then((value) => { if (!cancelled) setTeamPreview(value.preview) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [teamPreviewRevision, selectedPid])

  type StateResponse = { _fence?: number; strategyAccount?: StrategyAccount; state: LiveState; controller: ControllerState; hydraForecasts?: HydraForecastSummary[]; chimeraSimulation?: SimulationOverview; hydraSimulation?: HydraSimulationOverview; teamPreview?: TeamPreviewSummaryState | null; storageHealth?: StorageHealth; catalogRevision?: string; strategyProfiles?: StrategyProfile[]; heroes?: Hero[]; hydraHeads?: HydraHead[]; effects?: EffectOption[]; difficulties?: Difficulty[] }
  const pollRequest = useCallback((signal: AbortSignal) => {
    const params = new URLSearchParams({ mode: bossMode, catalogRevision: catalogRevisionRef.current })
    if (selectedPid) params.set('pid', String(selectedPid))
    if (controllerRef.current.logCursor) params.set('logCursor', controllerRef.current.logCursor)
    const fence = pollFenceRef.current
    return api<StateResponse>(`/api/state?${params}`, { signal }).then(next => ({ ...next, _fence: fence }))
  }, [bossMode, selectedPid])
  const receivePoll = useCallback((next: StateResponse) => {
    if (next._fence !== pollFenceRef.current) return
    setLive(next.state)
    setController(current => mergeLogDelta(current, next.controller))
    if (next.hydraForecasts) setHydraForecasts(next.hydraForecasts)
    if (next.chimeraSimulation) setChimeraSimulation(next.chimeraSimulation)
    if (next.hydraSimulation) setHydraSimulation(next.hydraSimulation)
    setTeamPreviewRevision(next.teamPreview?.revision ?? '')
    // Another account logged in: its own strategy groups replace these.
    if (next.strategyAccount && next.strategyAccount.key !== strategyAccountRef.current.key) {
      setStrategyAccountChanged(value => value + 1)
      return
    }
    if (next.strategyProfiles) setStrategyProfiles(next.strategyProfiles)
    catalogRevisionRef.current = next.catalogRevision ?? catalogRevisionRef.current
    setData(current => current ? {
      ...current,
      storageHealth: next.storageHealth ?? current.storageHealth,
      heroes: next.heroes ?? current.heroes,
      hydraHeads: next.hydraHeads ?? current.hydraHeads,
      effects: next.effects ?? current.effects,
      difficulties: next.difficulties ?? current.difficulties,
    } : current)
    setConnectionError(next.state.error ?? '')
  }, [setController])
  const pollFailed = useCallback((reason: unknown) => {
    setConnectionError(reason instanceof Error ? reason.message : String(reason))
    setLive(current => ({ ...current, modeReady: false, agentReady: false }))
  }, [])
  useSerialPoll(Boolean(data) && !loading, `${bossMode}:${selectedPid}:${activeStrategyId}:${simulationContextRevision}`, pollScope.current, pollRequest, receivePoll, pollFailed)
  useEffect(() => {
    if (strategyAccountChanged) void load(selectedPid, true, bossMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyAccountChanged])

  useEffect(() => {
    const difficultyId = live.chimeraDifficultyId
    if (bossMode === 'chimera' && difficultyId && difficultyId >= 1 && difficultyId <= 6) {
      setTrialSearchDifficulty(difficultyId)
    }
  }, [bossMode, live.chimeraDifficultyId])

  const heroes = data?.heroes ?? []
  const heroNameOf = useCallback((typeId: number) => heroByRuntimeId(heroes, typeId)?.name ?? (tr('app.champion4', { typeId })), [heroes, language])
  const heroAvatarOf = useCallback((typeId: number) => <HeroAvatar hero={heroByRuntimeId(heroes, typeId)} size="md" />, [heroes])
  const hydraHeads = data?.hydraHeads ?? []
  const effects = data?.effects ?? []
  const rules = config.rules ?? []
  const objectives = config.objectives ?? {}
  const difficulty = data?.difficulties.find((item) => item.difficultyId === trialSearchDifficulty)
  const trials = difficulty?.trials ?? []
  const selectedTrials = objectives.mandatoryTrialIds ?? []
  const hydraDevourRetryConditions = objectives.devourOrderRetryConditions ?? EMPTY_HYDRA_DEVOUR_RETRY_CONDITIONS
  const selectedProcess = data?.processes.find((process) => process.pid === selectedPid)
  const strategyTeamIds = config.team?.heroTypeIds ?? config.team?.heroIds ?? []
  const team = strategyTeamIds.length ? strategyTeamIds : live.teamHeroIds ?? []
  const activeModeSpec = data?.modes.find((mode) => mode.id === bossMode)
  const teamSize = activeModeSpec?.teamSize ?? (bossMode === 'hydra' ? 6 : 5)
  // The full team on the preparation screen now, to set as the strategy's team.
  const preparationTeam = ((): ChosenTeam | null => {
    const types = live.teamHeroIds ?? []
    const instances = live.teamHeroInstanceIds ?? []
    const valid = (values: number[]) => values.every((value) => Number.isInteger(value) && value > 0) && new Set(values).size === values.length
    return live.screen === 'team_selection' && types.length === teamSize && instances.length === types.length && valid(types) && valid(instances)
      ? { heroTypeIds: [...types], heroInstanceIds: [...instances] } : null
  })()
  const preparationMatches = preparationTeam !== null
    && preparationTeam.heroInstanceIds.join(',') === (config.team?.heroInstanceIds ?? []).join(',')
  const selectedStrategyProfile = strategyProfiles.find((profile) => profile.id === activeStrategyId)
  const selectedStrategyName = dataName(selectedStrategyProfile?.name || config.name || '') || tr('app.defaultStrategy')
  const configuredTeam = config.team?.heroTypeIds ?? config.team?.heroIds
  const hasSavedStrategyTeam = Array.isArray(configuredTeam)
  const savedStrategyTeam = (selectedStrategyProfile?.teamHeroIds ?? configuredTeam ?? [])
    .filter((typeId) => Number.isInteger(typeId) && typeId > 0)
  const draftDirty = drafts.dirty(editorRef.current.key)
  // The team being edited is not the saved one yet.
  const teamUnsaved = draftDirty && (configuredTeam ?? []).join(',') !== (selectedStrategyProfile?.teamHeroIds ?? configuredTeam ?? []).join(',')

  const trialCatalogById = useMemo(() => new Map(
    (data?.difficulties ?? []).flatMap((item) => item.trials).map((trial) => [trial.id, trial]),
  ), [data?.difficulties])

  // Simulation usage per rule, only for the strategy that was simulated.
  const ruleUsage = useMemo(() => (
    simulationSummary && (!simulationSummary.strategy.id || simulationSummary.strategy.id === activeStrategyId)
      ? ruleUsageByIndex(simulationSummary) : new Map<number, never>()
  ), [simulationSummary, activeStrategyId])
  const onSimulationSummary = useCallback((value: SimulationSummary | null) => setSimulationSummary(value), [])
  const jumpToRule = useCallback((index: number) => {
    setHighlightRule(index)
    window.setTimeout(() => document.getElementById(`rule-row-${index}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 150)
    window.setTimeout(() => setHighlightRule((current) => (current === index ? null : current)), 2800)
  }, [])

  const completedTrials = useMemo(() => {
    const ids = new Set<number>(live.completedTrialIds ?? [])
    for (const trial of live.trials ?? []) if (trial.completed) ids.add(trial.id)
    return [...ids].map((id) => trialCatalogById.get(id) ?? { id, description: tr('app.trial3', { id }) })
  }, [live.completedTrialIds, live.trials, trialCatalogById, language])

  const selectedTrialDescriptions = useMemo(
    () => trials.filter((trial) => selectedTrials.includes(trial.id)).map((trial) => gameDescription(trial.description)),
    [trials, selectedTrials],
  )

  function updateObjective(key: string, value: boolean | number | number[] | HydraDevourRetryCondition[]) {
    setConfig((current) => ({
      ...current,
      objectives: { ...(current.objectives ?? {}), [key]: value },
    }))
  }

  function applyRequiredTrials(ids: number[]) {
    setConfig((current) => ({
      ...current,
      objectives: {
        ...(current.objectives ?? {}),
        mandatoryTrialIds: ids,
      },
    }))
  }

  function changeTrialDifficulty(difficultyId: number) {
    setTrialSearchDifficulty(difficultyId)
    setConfig((current) => ({
      ...current,
      objectives: {
        ...(current.objectives ?? {}),
        mandatoryTrialIds: [],
      },
    }))
  }

  function applyStrategyBundle(result: StrategyBundle) {
    invalidateSimulationSources()
    showDraft(editorRef.current.mode, result.activeStrategyId, result.config, result.revision)
    setActiveStrategyId(result.activeStrategyId)
    setStrategyProfiles(result.strategyProfiles)
    const inferredDifficulty = configuredTrialDifficulty(result.config)
    if (inferredDifficulty) setTrialSearchDifficulty(inferredDifficulty)
    setEditIndex(null)
    setRuleOpen(false)
  }

  function invalidateSimulationSources() {
    pollFenceRef.current++
    pollScope.current.invalidate()
    setChimeraSimulation(current => current ? { ...current, teamSources: undefined,
      captures: current.captures.filter(item => !item.id.startsWith('strategy-package:')) } : current)
    setHydraSimulation(current => current ? { ...current, teamSources: undefined,
      captures: current.captures.filter(item => !item.id.startsWith('strategy-package:')) } : current)
    setSimulationContextRevision(current => current + 1)
  }

  // Every save also packages the team's complete simulation data.
  // `value` replaces the edited strategy first.
  async function save(showMessage = true, snapshotTeam = true, value?: Strategy) {
    if (savingRef.current || loadingContext || data?.storageHealth?.ok === false) return false
    savingRef.current = true
    setSaving(true)
    setError('')
    if (value) setConfig(value)
    const context = { ...editorRef.current }
    const entry = drafts.get(context.key)
    const generation = entry?.generation ?? 0
    try {
      const result = await api<StrategyBundle>('/api/config', {
        method: 'POST',
        body: JSON.stringify({ bossMode: context.mode, strategyId: activeStrategyId,
          config: context.value, snapshotTeam, pid: selectedPid,
          expectedRevision: entry?.revision || undefined }),
      })
      const updated = drafts.saved(context.key, generation, result.config, result.revision)
      if (editorRef.current.key === context.key && updated) {
        invalidateSimulationSources()
        editorRef.current.value = updated.value
        setConfigState(updated.value)
        setStrategyProfiles(result.strategyProfiles)
        if (showMessage) setNotice(simulationSaveText(language, result.simulationPackage, result.message ?? tr('app.strategyGroupSaved')))
      }
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return false
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  function askUnsaved(action: UnsavedAction) {
    return new Promise<'save' | 'discard' | 'cancel'>((resolve) => setUnsavedPrompt({ action, resolve }))
  }

  // Before an action that works on the saved strategy group; false when the user cancels.
  // Discarding restores the last saved version of this group.
  async function settleUnsaved(action: UnsavedAction, snapshotTeam = true) {
    const context = { ...editorRef.current }
    if (!drafts.dirty(context.key)) return true
    const choice = await askUnsaved(action)
    setUnsavedPrompt(null)
    if (choice === 'cancel') return false
    if (choice === 'save') return save(true, snapshotTeam)
    const entry = drafts.get(context.key)
    drafts.forget(context.key)
    if (entry) showDraft(context.mode, activeStrategyId, entry.saved, entry.revision)
    setNotice(tr('app.unsavedChangesDiscarded'))
    return true
  }

  // Choosing a team only edits the strategy group, like editing a rule: it is kept
  // once the group is saved, and "Load saved version" undoes it.
  function applyStrategyTeam(chosen: ChosenTeam) {
    setTeamPickerOpen(false)
    setConfig((current) => ({ ...current, team: { heroTypeIds: [...chosen.heroTypeIds], heroInstanceIds: [...chosen.heroInstanceIds] } }))
    setError('')
    setNotice(tr('app.theStrategySTeamChanged'))
  }

  async function loadRoster(): Promise<RosterHero[]> {
    if (selectedPid === null || selectedPid === undefined) throw new Error(tr('app.openTheGameToRead'))
    return (await api<{ heroes: RosterHero[] }>(`/api/roster?pid=${selectedPid}`)).heroes
  }

  // Champion search tags and profiles, read once from the game's static data.
  function loadPickerHeroData() {
    const query = selectedPid === null || selectedPid === undefined ? '' : `?pid=${selectedPid}`
    return loadHeroData(() => api<{ status: string; data?: HeroData; reason?: string }>(`/api/hero-data${query}`))
  }

  async function beginRename() {
    if (profileBusy || !(await settleUnsaved('rename'))) return
    openProfileNameDialog('rename')
  }

  function openProfileNameDialog(kind: 'create' | 'rename') {
    const currentName = selectedStrategyName
    setProfileName(kind === 'create'
      ? (tr('app.copyOf', { currentName }))
      : currentName)
    setProfileDialog(kind)
  }

  async function commitProfileName() {
    const name = profileName.trim()
    if (!name || profileBusy) return
    setProfileBusy(true)
    setError('')
    try {
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({
          action: profileDialog,
          bossMode,
          strategyId: activeStrategyId,
          name,
          pid: selectedPid,
          ...(profileDialog === 'create' ? {
            config: editorRef.current.value,
            snapshotTeam: true,
            pid: selectedPid,
          } : {}),
        }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? (profileDialog === 'create' ? tr('app.newStrategyGroupCreated') : tr('app.strategyGroupRenamed')))
      setProfileDialog(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function selectStrategyProfile(strategyId: string) {
    if (strategyId === activeStrategyId || profileBusy || loading || controller.running) return
    setProfileBusy(true)
    setError('')
    try {
      if (!(await settleUnsaved('switch'))) return
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'select', bossMode, strategyId, pid: selectedPid }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? tr('app.strategyGroupSwitched'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function deleteStrategyProfile() {
    if (strategyProfiles.length <= 1 || profileBusy || controller.running) return
    const profileLabel = selectedStrategyProfile?.name || config.name || tr('app.currentStrategyGroup')
    const confirmed = window.confirm(tr('app.deleteStrategyGroupThisCannot', { profileLabel }))
    if (!confirmed) return
    setProfileBusy(true)
    setError('')
    try {
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'delete', bossMode, strategyId: activeStrategyId, pid: selectedPid }),
      })
      drafts.forget(editorRef.current.key)
      applyStrategyBundle(result)
      setNotice(result.message ?? tr('app.strategyGroupDeleted'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function exportStrategyProfile() {
    if (profileBusy || controller.running) return
    setProfileBusy(true)
    setError('')
    try {
      if (!(await settleUnsaved('export'))) return
      const result = await api<{ document: StrategyExportDocument }>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'export', bossMode, strategyId: activeStrategyId, pid: selectedPid }),
      })
      const safeName = selectedStrategyName.replace(/[<>:"/\\|?*\u0000-\u001f]+/g, '-').replace(/[. ]+$/g, '').trim() || 'strategy'
      const blob = new Blob([JSON.stringify(result.document, null, 2)], { type: 'application/json;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${bossMode}-${safeName}.raid-strategy.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setNotice(result.document.simulationPackage?.team?.simulation && result.document.simulationPackage.opening && result.document.simulationPackage.accountBonuses
        ? tr('app.strategyExportedWithSimulationPackage')
        : result.document.teamSnapshot?.simulation
        ? tr('app.strategyExportedWithItsTeam')
        : result.document.teamSnapshot
          ? tr('app.strategyExportedWithTheTeam')
          : tr('app.strategyExportedWithoutTheTeam'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function importStrategyFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file || profileBusy || controller.running) return
    if (file.size > 16 * 1024 * 1024) {
      setError(tr('app.theStrategyFileCannotExceed'))
      return
    }
    setProfileBusy(true)
    setError('')
    try {
      const document = JSON.parse(await file.text()) as unknown
      if (data?.storageHealth?.ok === false) {
        await api('/api/config/recover', { method: 'POST', body: JSON.stringify({ bossMode, document, pid: selectedPid }) })
        await load(selectedPid, false, bossMode)
        setNotice(tr('app.strategyRestoredTheOriginalFile'))
        return
      }
      const result = await api<StrategyBundle>('/api/strategy/profile', {
        method: 'POST',
        body: JSON.stringify({ action: 'import', bossMode, document, pid: selectedPid }),
      })
      applyStrategyBundle(result)
      setNotice(result.message ?? tr('app.strategyImportedAsANew'))
    } catch (reason) {
      setError(reason instanceof SyntaxError ? tr('app.theStrategyFileIsNot') : reason instanceof Error ? reason.message : String(reason))
    } finally {
      setProfileBusy(false)
    }
  }

  async function start() {
    if (profileBusy || saving || loading || data?.storageHealth?.ok === false) return
    if (!selectedPid) return setError(tr('app.selectADetectedGameAccount'))
    // Starting must preserve this profile's saved team so the controller can
    // select it even when another team is currently shown in the game.
    if (!(await settleUnsaved('start'))) return
    pollFenceRef.current++
    setError('')
    setController((current) => ({ ...current, running: true, status: tr('app.preparingTheAgent') }))
    try {
      const next = await api<{ controller: ControllerState }>('/api/start', { method: 'POST', body: JSON.stringify({ pid: selectedPid, bossMode }) })
      pollFenceRef.current++
      setController(next.controller)
      setNotice(tr('app.strategyTakeoverStarted'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function stop() {
    pollFenceRef.current++
    try {
      const next = await api<{ controller: ControllerState }>('/api/stop', { method: 'POST', body: JSON.stringify({ pid: selectedPid, bossMode }) })
      pollFenceRef.current++
      setController(next.controller)
      setNotice(tr('app.takeoverPauseRequested'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function clearLogs() {
    pollFenceRef.current++
    try {
      const next = await api<{ controller: ControllerState }>('/api/logs/clear', { method: 'POST', body: JSON.stringify({ bossMode }) })
      pollFenceRef.current++
      setController(next.controller)
      setNotice(tr('app.runLogCleared', { bossMode: modeName(bossMode) }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function recoverConfig() {
    setError('')
    try {
      await api('/api/config/recover', { method: 'POST', body: JSON.stringify({ pid: selectedPid }) })
      await load(selectedPid, false, bossMode)
      setNotice(tr('app.restoredTheLatestValidBackup'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  async function refreshAccounts() {
    setRefreshing(true)
    await load(selectedPid, false, bossMode)
    setRefreshing(false)
  }

  async function discardDraft() {
    if (!window.confirm(tr('app.discardUnsavedChangesInThis'))) return
    await load(selectedPid, false, bossMode, editorRef.current.key)
  }

  async function switchBossMode(nextMode: BossMode) {
    if (nextMode === bossMode || controller.running || profileBusy || saving || loadingContext) return
    setNotice('')
    setError('')
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
    return <I18nProvider locale={language}><main className="loading-screen"><LanguagePicker locale={language} onChange={changeLanguage} className="loading-language-switcher" /><span className="brand-mark"><img src="/app-icon.png" alt="" /></span><h1>{tr('app.buildingTheBossResourceCache')}</h1><p>{tr('app.readingTheGameAccountChampions')}</p><span className="loader" /></main></I18nProvider>
  }

  return (
    <I18nProvider locale={language}>
    <div className="app-shell">
      <header className="topbar">
        <div className="brand" data-i18n-skip><span className="brand-mark"><img src="/app-icon.png" alt="" /></span><span><strong>{APP_NAME}</strong><small>{__APP_VERSION__}</small></span></div>
        <div className="account-picker">
          <span className={`status-dot ${selectedProcess?.accountName ? 'online' : ''}`} />
          <select value={selectedPid ?? ''} onChange={(event) => { const pid = Number(event.target.value); void load(pid, true, bossMode) }} disabled={controller.running || loadingContext || profileBusy || saving}>
            {!data?.processes.length && <option value="">{tr('app.noRaidAccountFound')}</option>}
            {data?.processes.map((process) => <option key={process.pid} value={process.pid}>{backendText(process.label)}</option>)}
          </select>
          <button className="icon-button" onClick={() => void refreshAccounts()} disabled={refreshing || controller.running} title={tr('app.refreshAccounts')}><RefreshCw size={18} className={refreshing ? 'spin' : ''} /></button>
        </div>
        <div className="topbar-actions">
          {controller.running && <button className="button pause" onClick={() => void stop()}><CirclePause size={17} />{tr('app.pauseTakeover')}</button>}
          <div className={`live-badge${connectionError ? ' offline' : ''}`} title={connectionError || undefined}><Activity size={16} /><span>{connectionError ? tr('app.disconnectedShowingTheLastReceived') : backendText(live.statusLabel || controller.status) || tr('app.waitingForStatus')}</span></div>
          <LanguagePicker locale={language} onChange={changeLanguage} />
        </div>
      </header>

      <nav className="mode-switcher" aria-label={tr('app.bossMode')}>
        {(data?.modes ?? []).map((mode) => (
          <button key={mode.id} type="button" className={bossMode === mode.id ? 'active' : ''} disabled={controller.running || loadingContext || profileBusy || saving} onClick={() => void switchBossMode(mode.id)}>
            <span className="mode-icon">{mode.id === 'chimera' ? <Swords size={21} /> : <Waves size={21} />}</span>
            <span><strong>{modeName(mode.id)}</strong></span>
            {mode.id === 'hydra' && mode.status !== 'ready' && !(bossMode === 'hydra' && live.modeReady) && <em>{tr('app.needsLiveCalibration')}</em>}
          </button>
        ))}
      </nav>

      {(error || notice) && <div className={`toast ${error ? 'error' : ''}`}><span>{backendText(error || notice)}</span><button onClick={() => { setError(''); setNotice('') }}><X size={16} /></button></div>}
      {!error && selectedProcess?.error && <div className="toast error"><span>{tr('app.accountReadFailed', { error: backendText(selectedProcess.error) })}</span></div>}

      <main className="workspace">
        <aside className="control-column">
          <CollapsiblePanel key={`${bossMode}:team`} id={`${bossMode}:team`} title={tr('app.strategySTeam')} hint={`${team.filter((id) => id > 0).length}/${teamSize}`}>
<section className="card team-card">
            <div className="section-heading"><span><Users size={18} />{tr('app.strategySTeam')}</span><em>{team.filter((typeId) => typeId > 0).length}/{teamSize}</em></div>
            <div className={`team-row team-${teamSize}`}>
              {Array.from({ length: teamSize }, (_, index) => {
                const hero = heroByRuntimeId(heroes, team[index])
                return <div className="team-member" key={index}><HeroAvatar hero={hero} size="lg" /><small>{hero?.name ?? tr('app.notReadYet')}</small></div>
              })}
            </div>
            <div className="team-card-actions">
              <button type="button" className="button ghost" disabled={selectedPid === null || selectedPid === undefined || saving || loadingContext || controller.running} onClick={() => setTeamPickerOpen(true)}><Users size={15} />{tr('app.chooseChampions')}</button>
              {preparationTeam && <button type="button" className="button ghost" disabled={preparationMatches || saving || loadingContext || controller.running} onClick={() => applyStrategyTeam(preparationTeam)}><Crosshair size={15} />{tr('app.useThePreparationScreenTeam')}</button>}
            </div>
            {teamUnsaved && <small className="team-card-note unsaved">{tr('app.teamChangedNotSavedYet')}</small>}
            {preparationTeam && !preparationMatches && <small className="team-card-note">{tr('app.thePreparationScreenTeamDiffers')}</small>}
            <TeamPreviewSummary language={language} preview={teamPreview?.bossMode === bossMode ? teamPreview : null}
              reference={config.referenceTeam} onOpenPreview={() => setTeamDialog('live')} onOpenReference={() => setTeamDialog('reference')} />
          </section>
          </CollapsiblePanel>

          <CollapsiblePanel key={`${bossMode}:objectives`} id={`${bossMode}:objectives`} title={tr('app.battleGoals')} >
<section className="card objectives-card">
            <div className="section-heading"><span><Crosshair size={18} />{tr('app.battleGoals')}</span><em>{tr('app.trackedAutomatically')}</em></div>
            <div className="two-fields">
              {bossMode === 'chimera' && <label className="field"><span>{tr('app.bossDifficulty')}</span><select value={trialSearchDifficulty} onChange={(event) => changeTrialDifficulty(Number(event.target.value))}>{data?.difficulties.map((item) => <option key={item.difficultyId} value={item.difficultyId}>{bossDifficulty(item.difficulty) ?? item.difficulty}</option>)}</select></label>}
              <label className="field"><span>{tr('app.maximumFreeRegroups')}</span><NumericInput value={objectives.maxRegroupRetries ?? 10} onValue={(value) => updateObjective('maxRegroupRetries', value)} /></label>
              <label className="field"><span>{tr('app.minimumDamageM')}</span><NumericInput decimal value={damageInMillions(objectives.minimumDamage)} onValue={(value) => updateObjective('minimumDamage', value * 1_000_000)} placeholder={tr('app.eG2')} /></label>
            </div>
            {bossMode === 'chimera' && <button className={`trial-trigger ${selectedTrials.length ? 'has-selection' : ''}`} onClick={() => setTrialOpen(true)}>
              <span className="trial-trigger-icon"><BookOpenCheck size={21} /></span>
              <span><small>{tr('app.requiredTrials')}</small><strong>{selectedTrials.length ? tr('app.trialsSelected', { selectedTrialsCount: selectedTrials.length }) : tr('app.clickToChooseTrials')}</strong><em>{selectedTrialDescriptions[0] || tr('app.readsEveryTrialForThe')}</em></span>
              <ChevronDown size={18} />
            </button>}
            {bossMode === 'chimera' && <button type="button" className={`devour-forecast-toggle ${objectives.battleForecast !== false ? 'selected' : ''}`} aria-pressed={objectives.battleForecast !== false} onClick={() => updateObjective('battleForecast', objectives.battleForecast === false)}>
              <span className="check-box">{objectives.battleForecast !== false && <Check size={14} />}</span>
              <span><strong>{tr('app.simulateTheWholeBattleAt')}</strong><small>{tr('app.whenTakingOverFromThe')}</small></span>
            </button>}
            {bossMode === 'hydra' && <button className={`trial-trigger early-retry-trigger ${hydraDevourRetryConditions.length ? 'has-selection' : ''}`} disabled={!team.some((value) => value > 0)} onClick={() => setHydraDevourRetryOpen(true)}>
              <span className="trial-trigger-icon"><RefreshCw size={20} /></span>
              <span><small>{tr('app.devourOrderRegroup')}</small><strong>{hydraDevourRetryConditions.length ? tr('app.devourConditionsSet', { hydraDevourRetryConditionsCount: hydraDevourRetryConditions.length }) : tr('app.setByMarkOrderAnd')}</strong><em>{objectives.devourOrderForecast ? tr('app.forecastAtBattleStartThen') : tr('app.firstTargetReadAtBattle')}</em></span>
              <ChevronDown size={18} />
            </button>}
            {bossMode === 'hydra' && !live.modeReady && <p className="mode-calibration"><Waves size={16} /><span><strong>{tr('app.waitingForTheFirstLive')}</strong>{tr('app.enterTheHydraPreparationScreen')}</span></p>}
          </section>
          </CollapsiblePanel>

          <CollapsiblePanel key={`${bossMode}:run`} id={`${bossMode}:run`} title={tr('app.controller')} hint={toolText(controller.status)}>
<section className="run-card">
            <div className="run-status"><span className={controller.running ? 'pulse' : ''}><Bot size={19} /></span><span><small>{tr('app.takeoverStatus')}</small><strong>{backendText(controller.status)}</strong></span></div>
            <div className="run-actions">
              <button className="button pause" disabled={!controller.running} onClick={() => void stop()}><CirclePause size={19} />{tr('app.pause')}</button>
              <button className="button start" disabled={saving || loadingContext || data?.storageHealth?.ok === false || Boolean(connectionError) || controller.running || !selectedProcess?.accountName || (bossMode === 'hydra' && !live.modeReady)} onClick={() => void start()}><CirclePlay size={19} />{tr('app.start')}</button>
            </div>
          </section>
          </CollapsiblePanel>
        </aside>

        <section className="strategy-column">
          <CollapsiblePanel key={`${bossMode}:profiles`} id={`${bossMode}:profiles`} title={tr('app.strategyGroups')} hint={selectedStrategyName}>
<section className="card strategy-profile-card">
            <div className="strategy-profile-identity">
              <span className="strategy-profile-icon"><Layers3 size={21} /></span>
              <span><small>{tr('app.currentStrategyGroup')}{strategyAccount.name ? <em className="strategy-account" data-i18n-skip> · {strategyAccount.name}</em> : null}</small><strong data-i18n-skip>{selectedStrategyName}</strong></span>
            </div>
            <label className="strategy-profile-select">
              <span>{tr('app.switchStrategyGroup')}</span>
              <select data-i18n-skip value={activeStrategyId} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy} onChange={(event) => void selectStrategyProfile(event.target.value)}>
                {strategyProfiles.map((profile) => <option key={profile.id} value={profile.id}>{dataName(profile.name)} · {tr('app.ruleCount', { count: profile.ruleCount })}</option>)}
              </select>
            </label>
            <div className="strategy-profile-team">
              <span><small>{tr('app.savedTeam')}</small><strong>{hasSavedStrategyTeam ? `${savedStrategyTeam.length}/${teamSize}` : tr('app.notSavedYet')}</strong></span>
              <div>{savedStrategyTeam.slice(0, teamSize).map((typeId, index) => <HeroAvatar key={`${typeId}-${index}`} hero={heroByRuntimeId(heroes, typeId)} size="sm" />)}</div>
            </div>
            <div className="strategy-profile-actions">
              <button className={`button ${draftDirty ? 'primary' : 'ghost'} profile-save-button`} title={draftDirty ? tr('app.thisStrategyGroupHasUnsaved') : tr('app.saveThisStrategyGroup')} disabled={saving || loadingContext || data?.storageHealth?.ok === false} onClick={() => void save()}><Save size={16} /><span>{draftDirty ? tr('app.saveChanges') : tr('app.save')}</span></button>
              <button className="button ghost profile-copy-button" title={tr('app.createCopy')} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy} onClick={() => openProfileNameDialog('create')}><Copy size={16} /><span>{tr('app.createCopy')}</span></button>
              <input ref={strategyImportRef} className="sr-only" type="file" accept=".json,.raid-strategy.json,application/json" onChange={(event) => void importStrategyFile(event)} />
              <button className="icon-button" title={tr('app.importStrategy')} aria-label={tr('app.importStrategy')} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy} onClick={() => strategyImportRef.current?.click()}><Upload size={16} /></button>
              <button className="icon-button" title={tr('app.exportStrategy')} aria-label={tr('app.exportStrategy')} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy} onClick={() => void exportStrategyProfile()}><Download size={16} /></button>
              <button className="icon-button" title={tr('app.renameStrategyGroup')} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy} onClick={() => void beginRename()}><Edit3 size={16} /></button>
              <button className="icon-button danger" title={tr('app.deleteStrategyGroup')} disabled={loadingContext || data?.storageHealth?.ok === false || controller.running || profileBusy || strategyProfiles.length <= 1} onClick={() => void deleteStrategyProfile()}><Trash2 size={16} /></button>
            </div>
          </section>
          </CollapsiblePanel>

          <CollapsiblePanel key={`${bossMode}:overview`} id={`${bossMode}:overview`} title={tr('app.battleOverview')} hint={damageText(live.damage ?? 0)}>
          <div className="overview-grid">
            <Metric label={tr('app.currentDamage')} value={damageText(live.damage ?? 0)} icon={<Swords size={18} />} />
            <Metric label={tr('app.bossTurn2')} value={String(bossMode === 'chimera' ? live.chimeraTurn ?? 0 : live.hydraTurn ?? 0)} icon={<Activity size={18} />} />
            <Metric label={bossMode === 'chimera' ? tr('app.completedTrials') : tr('app.currentTargets')} value={bossMode === 'chimera' ? `${completedTrials.length}` : tr('app.headCount', { headCount: live.headCount ?? 0 })} icon={<Check size={20} />} />
          </div>
          </CollapsiblePanel>

          {bossMode === 'chimera' ? <CollapsiblePanel key={`${bossMode}:trials`} id={`${bossMode}:trials`} title={tr('app.completedTrials')} hint={completedTrials.length}>
<section className="card completed-trials-card">
            <div className="completed-trials-heading">
              <span><BookOpenCheck size={20} /><strong>{tr('app.trialsCompletedThisRunAnd')}</strong></span>
              <em>{completedTrials.length ? tr('app.trialsCompletedCount', { completedTrialsCount: completedTrials.length }) : tr('app.waitingForBattleProgress')}</em>
            </div>
            {completedTrials.length ? (
              <div className="completed-trials-list">
                {completedTrials.map((trial) => (
                  <article className="completed-trial" key={trial.id}>
                    <span className="completed-check"><Check size={17} /></span>
                    <span className="completed-trial-copy">
                      <span className="trial-tags"><em>{formLabel(trial.form ?? '') ?? trial.form ?? tr('app.chimera')}</em><em>{trialLevel(trial.difficulty ?? '') ?? trial.difficulty ?? tr('app.trial2')}</em></span>
                      <strong>{gameDescription(trial.description) || tr('app.completedTrials')}</strong>
                    </span>
                    <TrialRewards reward={trial.reward} compact />
                  </article>
                ))}
              </div>
            ) : <div className="completed-trials-empty"><Check size={17} /><span>{tr('app.noTrialsCompletedYet')}</span></div>}
          </section>
          </CollapsiblePanel> : <CollapsiblePanel key={`${bossMode}:heads`} id={`${bossMode}:heads`} title={tr('app.hydraStatus')} >
<section className="card completed-trials-card hydra-summary">
            <div className="completed-trials-heading"><span><Waves size={20} /><strong>{tr('app.hydraBattleFocus')}</strong></span><em>{live.modeReady ? tr('app.stateConnected') : tr('app.waitingForTheHydraState')}</em></div>
            {(live.heads?.length ?? 0) > 0 ? <div className="live-hydra-heads">{live.heads?.map((liveHead, index) => {
              const identity = liveHead.canonicalTypeId ?? liveHead.typeId
              const catalogHead = hydraHeads.find((head) => head.typeId === identity)
              const head = { ...catalogHead, ...liveHead, canonicalTypeId: identity, name: catalogHead?.name || liveHead.name || tr('app.hydraHead2', { identity }) }
              const stateLabel = head.dead ? tr('app.dead') : head.isHydraNeck || head.headState === 'exposed_neck' ? tr('app.exposedNeck') : head.isDevouring ? tr('app.devouring') : tr('app.targetable')
              return <article className={`live-hydra-head${head.dead ? ' dead' : ''}`} key={`${head.id ?? head.typeId}-${index}`}><HydraHeadIcon head={head} size="lg" /><span><strong>{hydraHeadDisplayName(head)}</strong><small>{stateLabel}{!head.dead && typeof head.defence === 'number' ? tr('app.defenseSuffix', { toLocaleString: head.defence.toLocaleString('en-US') }) : ''}</small></span></article>
            })}</div> : <div className="hydra-head-catalog">{hydraHeads.map((head) => <span key={head.typeId}><HydraHeadIcon head={head} size="md" /><small>{hydraHeadDisplayName(head)}</small></span>)}</div>}
          </section>
          </CollapsiblePanel>}

          {data?.storageHealth?.ok === false && <CollapsiblePanel key={`${bossMode}:recovery`} id={`${bossMode}:recovery`} title={tr('app.strategyRecovery')} >
<section className="card recovery-card" role="alert">
            <strong>{tr('app.theStrategyFileNeedsRecovery')}</strong><p>{data.storageHealth.error}</p>
            <button className="button primary" disabled={controller.running || !data.storageHealth.backups.length} onClick={() => void recoverConfig()}>{tr('app.restoreTheLatestValidBackup')}</button>
            <button className="button ghost" disabled={controller.running || profileBusy} onClick={() => strategyImportRef.current?.click()}>{tr('app.restoreThisModeFromAn')}</button>
            {!data.storageHealth.backups.length && <p>{tr('app.noValidBackupIsAvailable')}</p>}
          </section>
          </CollapsiblePanel>}
          {bossMode === 'hydra' && (objectives.devourOrderForecast === true || controller.telemetry?.devourForecast || hydraForecasts.length > 0) && <HydraForecastPanel forecast={controller.telemetry?.devourForecast} history={hydraForecasts} observed={controller.telemetry?.devour?.sequence} heroes={heroes} language={language} onOpenReport={setHydraReportId} />}
          {bossMode === 'chimera' && <BattleForecastPanel language={language} enabled={objectives.battleForecast !== false} forecast={controller.telemetry?.battleForecast}
            history={chimeraSimulation?.battleForecasts ?? []} heroes={heroes} effects={effects} trialById={trialCatalogById} onOpenReport={setSimulationReportId} />}
          {bossMode === 'hydra' && <HydraSimulationPanel key={`${strategyAccount.key}:${activeStrategyId}`} language={language} overview={hydraSimulation} heroes={heroes} heads={hydraHeads} effects={effects} request={api}
            draft={config} strategyId={activeStrategyId} strategyName={selectedStrategyName} strategyTeam={savedStrategyTeam}
            pid={selectedPid} onOpenReport={setHydraReportId} />}
          {bossMode === 'chimera' && <ChimeraSimulationPanel key={`${strategyAccount.key}:${activeStrategyId}`} language={language} overview={chimeraSimulation} heroes={heroes} effects={effects} trialById={trialCatalogById} request={api}
            draft={config} strategyId={activeStrategyId} strategyName={selectedStrategyName} strategyTeam={savedStrategyTeam}
            pid={selectedPid} onOpenReport={setSimulationReportId} onSummary={onSimulationSummary} />}
          <CollapsiblePanel key={`${bossMode}:decision`} id={`${bossMode}:decision`} title={tr('app.decisionExplanation')}
            hint={controller.telemetry?.decision ? controller.telemetry.decision.hero : toolText(tr('app.noRecordsYet'))}>
          <section className="card decision-panel">
            {controller.telemetry?.decision ? <>
              <p data-i18n-skip>{controller.telemetry.decision.hero} · {controller.telemetry.decision.rule ? backendText(controller.telemetry.decision.rule, language, false) : tr('app.waitingForAnAvailableRule')}</p>
              <p><span>{tr('app.skillAndTarget')}</span>{tr('app.colon')}<span data-i18n-skip>{backendText(controller.telemetry.decision.skill ?? '—', language, false)} → {backendText(controller.telemetry.decision.target ?? '—', language, false)}</span></p>
              <p>{tr('app.legalTargetIds')}<span data-i18n-skip>{controller.telemetry.decision.legalTargetIds?.join(', ') || '—'}</span></p>
              {controller.telemetry.command && <p>{tr('app.actionAcknowledgement')}<span data-i18n-skip>{controller.telemetry.command.status} {backendText(controller.telemetry.command.reason ?? '', language, false)}</span></p>}
              <p className="muted">{tr('app.openingSkillsTakePrecedenceThen')}</p>
              <DecisionRows rows={controller.telemetry.decision.rules} />
            </> : <p>{tr('app.runAStrategyToSee')}</p>}
          </section>
          </CollapsiblePanel>


          <CollapsiblePanel key={`${bossMode}:rules`} id={`${bossMode}:rules`} title={tr('app.actionRules')} rules hint={rules.length}>
<section className="card rules-card">
            <div className="rules-header">
              <div><span className="eyebrow"><Sparkles size={14} />{tr('app.strategyTree')}</span><h2>{tr('app.actionRules')}</h2></div>
              <label className="rule-search"><Search size={16} /><input value={ruleSearch} onChange={(event) => setRuleSearch(event.target.value)} placeholder={tr('app.findChampionSkillOrRule')} aria-label={tr('app.findActionRules')} />{ruleSearch && <button type="button" className="icon-button" onClick={() => setRuleSearch('')} aria-label={tr('app.clearSearch')}><X size={15} /></button>}</label>
              <div className="toolbar">{drafts.dirty(editorRef.current.key) && <><span className="draft-dirty">{tr('app.unsaved')}</span><button className="button ghost" disabled={saving || profileBusy || loadingContext || data?.storageHealth?.ok === false} onClick={() => void discardDraft()}>{tr('app.loadSavedVersion')}</button></>}<button className="button ghost" disabled={saving || loadingContext || data?.storageHealth?.ok === false} onClick={() => void save()}><Save size={17} />{tr('app.save')}</button><button className="button primary" onClick={() => { setEditIndex(null); setRuleOpen(true) }}><Plus size={17} />{tr('app.addRule')}</button></div>
            </div>
            <div className="rules-list">
              {!rules.length && <div className="empty-state"><Database size={34} /><strong>{tr('app.noStrategyRulesYet')}</strong><button className="button primary" onClick={() => { setEditIndex(null); setRuleOpen(true) }}><Plus size={17} />{tr('app.addRule')}</button></div>}
              {rules.map((rule, index) => {
                const hero = heroes.find((item) => heroMatchesIds(item, ruleHeroIds(rule)))
                if (ruleSearch.trim() && ![rule.name, hero?.name, actionLabel(rule, heroes), conditionLabel(rule, effects, heroes, trials)].join(" ").toLocaleLowerCase().includes(ruleSearch.trim().toLocaleLowerCase())) return null
                const action = rule.action ?? {}
                const firstPriority = Array.isArray(action.prioritySkills) && action.prioritySkills[0] && typeof action.prioritySkills[0] === 'object' ? action.prioritySkills[0] as JsonObject : undefined
                const slot = typeof action.skillSlot === 'number' ? action.skillSlot : typeof firstPriority?.skillSlot === 'number' ? firstPriority.skillSlot : undefined
                const skillTypeId = typeof action.skillTypeId === 'number' ? action.skillTypeId : typeof firstPriority?.skillTypeId === 'number' ? firstPriority.skillTypeId : undefined
                const skill = hero?.skills.find((item) => skillTypeId ? item.typeId === skillTypeId : item.slot === slot)
                const usage = ruleUsage.get(index + 1)
                const usageMatches = usage !== undefined && usage.rule === (rule.name || backendRuleName(index + 1))
                return (
                  <article className={`rule-row${highlightRule === index + 1 ? ' highlight' : ''}`} id={`rule-row-${index + 1}`} key={`${index}-${rule.name ?? ''}`}>
                    <span className="priority">{String(index + 1).padStart(2, '0')}</span>
                    <HeroAvatar hero={hero} />
                    <div className="rule-primary"><strong>{rule.name || tr('app.rule', { value: index + 1 })}</strong><span>{hero?.name ?? (action.type === 'executeTrialRecipe' ? tr('app.anyActingChampion') : tr('app.noChampionSet'))}</span>
                      {bossMode === 'chimera' && usageMatches && <em className={`sim-usage${usage.uses > 0 ? '' : ' unused'}`} title={tr('app.fromTheLatestStrategySimulation')} data-i18n-skip>{usage.uses > 0
                        ? (tr('app.simulatedPerRun', { usesPerRun: usage.usesPerRun }))
                        : (tr('app.unusedInSimulation'))}</em>}</div>
                    <div className="form-pills">{bossMode === 'chimera' ? ruleForms(rule).slice(0, 4).map((form) => <span key={form} title={formLabel(form) ?? form}>{formShort(form) ?? form}</span>) : <span>{tr('app.wholeHydraBattle')}</span>}</div>
                    <HoverCard className="rule-action" inline={false} focusable={false} content={skill ? <SkillDetailCard hero={hero} skill={skill} slot={slot} /> : null}>
                      <SkillIcon hero={hero} skill={skill} slot={slot} /><span><small>{tr('app.action')}</small><strong>{actionLabel(rule, heroes)}</strong></span></HoverCard>
                    <div className="rule-target"><Crosshair size={16} /><span><small>{tr('app.target')}</small><strong>{targetLabel(rule, heroes, hydraHeads)}</strong></span></div>
                    <div className="rule-condition"><small>{action.type === 'defaultSkillPriority' ? tr('app.defaultSkillRule') : tr('app.strictRule')}</small><span>{action.type === 'defaultSkillPriority' ? (action.formPolicies ? tr('app.setPerChimeraForm') : tr('app.skillsBlocked', { blockedSkillTypeIdsCount: asNumberArray(action.blockedSkillTypeIds).length })) : conditionLabel(rule, effects, heroes, trials)}</span></div>
                    <div className="rule-buttons">
                      <button className="icon-button" title={tr('app.moveUp')} disabled={index === 0} onClick={() => moveRule(index, -1)}><ArrowUp size={16} /></button>
                      <button className="icon-button" title={tr('app.moveDown')} disabled={index === rules.length - 1} onClick={() => moveRule(index, 1)}><ArrowDown size={16} /></button>
                      <button className="icon-button" title={tr('app.edit')} onClick={() => { setEditIndex(index); setRuleOpen(true) }}><Edit3 size={16} /></button>
                      <button className="icon-button danger" title={tr('app.delete')} onClick={() => setConfig((current) => ({ ...current, rules: (current.rules ?? []).filter((_, itemIndex) => itemIndex !== index) }))}><Trash2 size={16} /></button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>
          </CollapsiblePanel>

          <section className={`log-drawer ${showLogs ? 'open' : ''}`}>
            <div className="log-handle">
              <button className="log-toggle" onClick={() => setShowLogs((value) => !value)}><span><Activity size={16} />{tr('app.runLog', { bossMode: modeName(bossMode) })} <em>{controller.logs.length}</em></span><ChevronDown size={17} /></button>
              <button className="log-maximize" title={tr('app.expandRunLog')} aria-label={tr('app.expandRunLog')} onClick={() => setLogsExpanded(true)}><Maximize2 size={16} /></button>
            </div>
            {showLogs && <LogView key={bossMode} logs={controller.logs.slice(-200)} />}
          </section>
        </section>
      </main>

      <Dialog.Root open={profileDialog !== null} onOpenChange={(open) => { if (!open && !profileBusy) setProfileDialog(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content profile-dialog">
            <div className="dialog-heading">
              <span><Dialog.Title>{profileDialog === 'create' ? tr('app.copyStrategyGroup') : tr('app.renameStrategyGroup')}</Dialog.Title><Dialog.Description>{profileDialog === 'create' ? tr('app.copiesTheCurrentRulesBattle') : tr('app.changesOnlyTheGroupS')}</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label={tr('app.close')}><X size={19} /></Dialog.Close>
            </div>
            <label className="field profile-name-field"><span>{tr('app.strategyGroupName')}</span><input autoFocus maxLength={60} value={profileName} onChange={(event) => setProfileName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void commitProfileName() }} placeholder={tr('app.eGSpeedTeamSafe')} /></label>
            <div className="dialog-footer"><span>{profileDialog === 'create' ? tr('app.theNewCopyBecomesThe') : tr('app.theNameIsSavedRight')}</span><div><Dialog.Close className="button ghost" disabled={profileBusy}>{tr('app.cancel')}</Dialog.Close><button className="button primary" disabled={!profileName.trim() || profileBusy} onClick={() => void commitProfileName()}>{profileBusy ? tr('app.saving') : profileDialog === 'create' ? tr('app.createCopy') : tr('app.saveName')}</button></div></div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={unsavedPrompt !== null} onOpenChange={(open) => { if (!open) unsavedPrompt?.resolve('cancel') }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content profile-dialog unsaved-dialog">
            <div className="dialog-heading">
              <span><Dialog.Title>{tr('app.thisStrategyGroupHasUnsaved')}</Dialog.Title><Dialog.Description>{tr('app.unsaved.saveBefore', { action: unsavedPrompt?.action ?? 'other' })}</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label={tr('app.close')}><X size={19} /></Dialog.Close>
            </div>
            <div className="dialog-footer"><span /><div>
              <button className="button ghost" onClick={() => unsavedPrompt?.resolve('cancel')}>{tr('app.cancel')}</button>
              <button className="button ghost discard-button" onClick={() => unsavedPrompt?.resolve('discard')}>{tr('app.discardChanges')}</button>
              <button className="button primary" autoFocus onClick={() => unsavedPrompt?.resolve('save')}><Save size={16} />{tr('app.save')}</button>
            </div></div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {bossMode === 'chimera' && <TrialPicker open={trialOpen} onOpenChange={setTrialOpen} trials={trials} selected={selectedTrials} onApply={applyRequiredTrials} />}
      {bossMode === 'hydra' && <HydraDevourRetryPicker open={hydraDevourRetryOpen} onOpenChange={setHydraDevourRetryOpen} heroes={heroes} team={team} conditions={hydraDevourRetryConditions} forecast={objectives.devourOrderForecast === true} onApply={(conditions, forecast) => setConfig((current) => ({ ...current, objectives: { ...(current.objectives ?? {}), devourOrderRetryConditions: conditions, devourOrderForecast: forecast } }))} />}
      <RuleEditor open={ruleOpen} onOpenChange={setRuleOpen} initial={editIndex === null ? undefined : rules[editIndex]} allRules={rules} heroes={heroes} hydraHeads={hydraHeads} team={team} effects={effects} trials={trials} bossMode={bossMode} onSave={saveRule} />
      <TeamPreviewDialog language={language}
        snapshot={teamDialog === 'live' ? teamPreview : teamDialog === 'reference' ? config.referenceTeam ?? null : null}
        title={teamDialog === 'reference' ? (tr('app.authorSTeamSetup')) : (tr('app.currentTeamSetup'))}
        description={teamDialog === 'reference'
          ? (tr('app.sharedWithThisStrategyCaptured', { capturedAt: config.referenceTeam?.capturedAt ?? '' }))
          : (tr('app.preparationScreenReadExportedWith', { capturedAt: teamPreview?.capturedAt ?? '' }))}
        onClose={() => setTeamDialog(null)}
        heroName={heroNameOf}
        heroAvatar={heroAvatarOf}
        skillName={(heroTypeId, skillTypeId) => heroByRuntimeId(heroes, heroTypeId)?.skills.find((skill) => skill.typeId === skillTypeId)?.name ?? `${skillTypeId}`} />
      <TeamPicker language={language} open={teamPickerOpen} teamSize={teamSize} bossMode={bossMode}
        initial={{ heroTypeIds: config.team?.heroTypeIds ?? [], heroInstanceIds: config.team?.heroInstanceIds ?? [] }}
        loadRoster={loadRoster} loadHeroData={loadPickerHeroData} effects={effects}
        onClose={() => setTeamPickerOpen(false)} onApply={(chosen) => void applyStrategyTeam(chosen)}
        heroName={heroNameOf} heroAvatar={heroAvatarOf} />
      <SimulationReport language={language} simulationId={simulationReportId} onClose={() => setSimulationReportId(null)} heroes={heroes}
        effects={effects} trialById={trialCatalogById} request={api} onJumpToRule={jumpToRule} />
      <HydraSimulationReport language={language} simulationId={hydraReportId} onClose={() => setHydraReportId(null)} heroes={heroes}
        heads={hydraHeads} effects={effects} request={api} onJumpToRule={jumpToRule} />
      <Dialog.Root open={logsExpanded} onOpenChange={setLogsExpanded}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog">
            <div className="dialog-heading log-dialog-heading">
              <span><Dialog.Title>{tr('app.fullRunLog', { bossMode: modeName(bossMode) })}</Dialog.Title><Dialog.Description>{tr('app.onlyTheCurrentBossMode')}</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label={tr('app.closeFullRunLog')}><X size={19} /></Dialog.Close>
            </div>
            <div className="log-dialog-status"><span className={`status-dot ${controller.running ? 'online' : ''}`} /><strong>{backendText(controller.status)}</strong><em>{tr('app.entryCount', { logsCount: controller.logs.length })}</em></div>
            <LogView key={bossMode} logs={controller.logs} />
            <div className="dialog-footer"><span>{tr('app.eachBossModeKeepsIts')}</span><div><button className="button ghost" onClick={() => void clearLogs()}><Trash2 size={15} />{tr('app.clearThisMode')}</button><Dialog.Close className="button primary">{tr('app.collapseRunLog')}</Dialog.Close></div></div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
    </I18nProvider>
  )
}

export default App

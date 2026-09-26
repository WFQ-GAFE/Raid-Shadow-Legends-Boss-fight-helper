import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { CollapsiblePanel } from './CollapsiblePanel'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { translateToolText, type UiLanguage } from './i18n'

// Strategy simulation: the captured Chimera battle replayed in the isolated
// original engine with the rules being edited. See tools/chimera_simulation*.py.

type Request = <T>(path: string, init?: RequestInit) => Promise<T>

export type SimulationHero = { typeId: number; name: string; runtimeTypeIds?: number[]; skills: { typeId?: number; slot: number; name?: string }[] }
export type SimulationTrial = { id: number; form?: string; difficulty?: string; description?: string }

export type SimulationCapture = {
  id: string
  capturedAt?: string
  stageId?: number
  difficulty?: number
  seed?: number
  teamHeroTypeIds: number[]
  bossHeroTypeId?: number
  strategyName?: string | null
}

export type SimulationJob = {
  id: string
  status: 'running' | 'complete' | 'failed' | 'cancelled'
  runs: number
  finishedRuns: number
  currentBossTurn?: number | null
  phase?: 'preparing' | 'running' | 'complete' | 'cancelled' | 'failed'
  reason?: string
  message?: string
  startedAt?: string
}

export type SimulationRecent = {
  id: string
  createdAt?: string
  strategyName?: string
  captureId?: string
  status?: string
  runs?: number
  allMandatoryRuns?: number
  finishedRuns?: number
  stuckRuns?: number
  verdict?: BattleForecastVerdict
}

export type SimulationOverview = { captures: SimulationCapture[]; job?: SimulationJob | null; recent: SimulationRecent[]; battleForecasts?: SimulationRecent[] }

// Why a run stopped: the live takeover would have stalled on this turn.
type StuckRule = { ruleIndex: number; name: string; actionType?: string; code: string; expected?: unknown; actual?: unknown; reason?: string }
type StuckSkill = { typeId: number; slot?: number; ready: boolean; cooldown?: number; defaultCooldown?: number; validTargets: number; reserved: boolean }
type StuckDetail = {
  reason: string
  turn?: number
  bossTurns?: number
  form?: string | null
  activeHeroId?: number
  activeHeroTypeId?: number
  activeHeroFormIndex?: number
  skills?: StuckSkill[]
  rules?: StuckRule[]
  detail?: string | null
  rule?: string | null
}
type StuckRun = { index: number; seed: number; exact: boolean; reason: string; turn?: number; bossTurns?: number; form?: string | null; activeHeroTypeId?: number; rule?: string | null }

// The whole-battle simulation a takeover runs at each Chimera opening
// (tools/chimera_forecast_live.py).
type BattleForecastVerdict = {
  status: string
  reason?: string | null
  conclusion?: string | null
  finishedAt?: string | null
  verdict?: 'continue' | 'retry' | 'unavailable'
  cause?: string | null
  missingTrialIds?: number[]
  damage?: number
  minimumDamage?: number
}
export type BattleForecastTelemetry = BattleForecastVerdict & {
  checkedTurns?: number
  recordId?: string
  goalsMet?: boolean
  bossTurns?: number
  stuck?: { reason: string; turn?: number; bossTurns?: number; form?: string | null; activeHeroTypeId?: number }
}

type TrialAggregate = {
  trialId: number
  mandatory: boolean
  completedRuns: number
  runs: number
  completedBossTurnMedian: number | null
  bestRatioMedian: number | null
  bestRatioMax: number
  windows: Record<string, { median: number | null; max: number }>
}

export type RuleAggregate = {
  ruleIndex: number | null
  rule: string
  runsUsed: number
  uses: number
  usesPerRun: number
  damageShare: number
  trialGains: Record<string, number>
  auto?: boolean
}

type Aggregate = {
  runs: number
  finishedRuns: number
  battleEndRuns?: number
  allMandatoryRuns: number
  mandatoryTrialIds: number[]
  minimumDamage: number
  trials: TrialAggregate[]
  damage: { min: number; median: number | null; max: number }
  bossDamage: { min: number; median: number | null; max: number }
  deaths: { heroTypeId: number; runs: number; firstBossTurnMedian: number | null }[]
  rules: RuleAggregate[]
  stuckRuns?: StuckRun[]
  reservationReleasesPerRun?: number
  // Before 1.0.6 strict runs, the game's auto battle played turns without a rule.
  autoTurnsPerRun?: number
  autoReasons?: Record<string, number>
  regroupEvents: { event: string; runs: number; bossTurnMedian: number | null; trialIds: number[] }[]
}

type RunSummary = {
  index: number
  seed: number
  exact: boolean
  status: string
  reason?: string | null
  bossTurns?: number
  damage?: number
  bossDamageTaken?: number
  commands?: number
  stuck?: StuckDetail | null
  reservationReleases?: number
  completedTrials: Record<string, number>
  mandatory: { trialId: number; completed: boolean; completedBossTurn: number | null; bestRatio: number }[]
  deaths: { actorId: number; heroTypeId: number; bossTurn: number }[]
  objectiveEvents: { event: string; bossTurn: number; trialIds: number[] }[]
}

export type SimulationSummary = {
  id: string
  kind?: 'battle'
  verdict?: BattleForecastVerdict
  createdAt?: string
  status: string
  reason?: string
  strategy: { id?: string; name?: string; rules?: number }
  capture: { id: string; stageId?: number; seed?: number; teamHeroTypeIds?: number[]; bossHeroTypeId?: number; difficulty?: number }
  runs?: RunSummary[]
  aggregate?: Aggregate
}

type TimelineRow = {
  turn: number
  bossTurns: number
  window: number
  form: number
  actorId: number
  actorTypeId: number
  source: 'policy' | 'auto' | 'enemy'
  skillTypeId: number
  targetId: number
  damage: number
  trials: { trialId: number; before: number | null; after: number | null; counterBefore: number; counterAfter: number; started: boolean; completed: boolean }[]
  deaths: number[]
  rule?: string | null
  ruleIndex?: number | null
  reservationReleased?: boolean
  autoDetail?: string | null
}

type RunDetail = {
  index: number
  status: string
  reason?: string | null
  stuck?: StuckDetail | null
  seed: number
  actors: { actorId: number; heroTypeId: number; player: boolean; dead: boolean; healthPct: number }[]
  timeline: TimelineRow[]
}

// The panel and report render both languages themselves (and opt out of
// the document phrase translator); rule names and controller reasons are
// user/controller text and go through the shared phrase table in English.
type Lang = UiLanguage
const LangContext = createContext<Lang>('zh-CN')

function useText() {
  const lang = useContext(LangContext)
  return { lang, t: (zh: string, en: string) => (lang === 'en' ? en : zh) }
}

function toolText(lang: Lang, value: string) {
  return lang === 'en' ? translateToolText(value) : value
}

const FORM_ZH = ['终极', '公羊', '狮子', '毒蛇']
const FORM_EN = ['Ultimate', 'Ram', 'Lion', 'Viper']
const FORM_INDEX: Record<string, number> = { Ultimate: 0, Ram: 1, Lion: 2, Snake: 3, Viper: 3 }
const LEVEL_ZH: Record<string, string> = { Easy: '简单', Normal: '普通', Hard: '苦难' }
const FORM_ORDER = ['Ram', 'Lion', 'Snake', 'Viper']
const RUN_CHOICES = [1, 5, 10, 20]

function formName(lang: Lang, index: number | undefined) {
  if (index === undefined || index < 0 || index > 3) return ''
  return lang === 'en' ? FORM_EN[index] : FORM_ZH[index]
}

// Game descriptions carry Unity rich-text colour tags.
function plainText(value?: string) {
  return (value ?? '').replace(/<[^>]+>/g, '').trim()
}

function percent(value: number | null | undefined, digits = 0) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`
}

function damageText(lang: Lang, value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const size = Math.abs(value)
  if (lang === 'en') {
    if (size >= 1e9) return `${(value / 1e9).toFixed(2)}B`
    if (size >= 1e6) return `${(value / 1e6).toFixed(1)}M`
    if (size >= 1e3) return `${(value / 1e3).toFixed(1)}K`
    return String(Math.round(value))
  }
  if (size >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (size >= 1e4) return `${(value / 1e4).toFixed(1)} 万`
  return String(Math.round(value))
}

function windowLabel(lang: Lang, window: number) {
  const first = window * 5 + 1
  // Boss turns 1-5 are Ultimate; afterwards Ram, Ultimate, Lion, Ultimate, Viper, Ultimate repeat.
  const forms = [0, 1, 0, 2, 0, 3]
  return `${formName(lang, forms[window % 6])} ${first}–${first + 4}`
}

export function trialShortLabel(lang: Lang, trial: SimulationTrial | undefined, id: number) {
  if (!trial) return lang === 'en' ? `Trial ${id}` : `试炼 ${id}`
  const form = trial.form !== undefined ? formName(lang, FORM_INDEX[trial.form]) || trial.form : ''
  const level = trial.difficulty ? (lang === 'en' ? trial.difficulty : LEVEL_ZH[trial.difficulty] ?? trial.difficulty) : ''
  return `${form}${level ? (lang === 'en' ? ' ' : '·') + level : ''} ${id}`
}

// Battles report rank-specific hero type ids (base id + 1..6); the catalog
// lists them as runtimeTypeIds, like heroByRuntimeId in App.tsx.
function findHero(heroes: SimulationHero[], typeId: number | undefined) {
  if (typeof typeId !== 'number') return undefined
  const ids = (hero: SimulationHero) => (hero.runtimeTypeIds?.length ? hero.runtimeTypeIds : [hero.typeId])
  const exact = heroes.find((hero) => hero.typeId === typeId || ids(hero).includes(typeId))
  if (exact) return exact
  const rank = typeId % 10
  return rank >= 1 && rank <= 6 ? heroes.find((hero) => hero.typeId === typeId - rank || ids(hero).includes(typeId - rank)) : undefined
}

function heroName(lang: Lang, heroes: SimulationHero[], typeId: number | undefined) {
  return findHero(heroes, typeId)?.name ?? (typeId ? (lang === 'en' ? `Champion ${typeId}` : `英雄 ${typeId}`) : '—')
}

function skillName(lang: Lang, heroes: SimulationHero[], heroTypeId: number, skillTypeId: number) {
  const skill = findHero(heroes, heroTypeId)?.skills.find((item) => item.typeId === skillTypeId)
  return skill?.name ?? (lang === 'en' ? `Skill ${skillTypeId}` : `技能 ${skillTypeId}`)
}

function ruleLabel(lang: Lang, rule: string | null | undefined) {
  return rule ? toolText(lang, rule) : ''
}

function formNameByKey(lang: Lang, form: string | null | undefined) {
  return form ? formName(lang, FORM_INDEX[form]) || form : ''
}

const STUCK_REASON: Record<string, [string, string]> = {
  no_matching_rule: ['没有匹配且可执行的规则', 'no rule matched with a usable skill'],
  rule_command_not_legal: ['规则选出的技能或目标不合法', 'the rule chose a skill or target that is not legal'],
  no_progress: ['同一回合反复决策而战斗没有推进', 'kept deciding on one turn without the battle advancing'],
  engine_rejected_command: ['游戏引擎拒绝了规则给出的指令', "the game engine rejected the rule's command"],
}

function stuckReasonText(lang: Lang, reason: string) {
  const text = STUCK_REASON[reason]
  return text ? (lang === 'en' ? text[1] : text[0]) : reason
}

function listText(value: unknown) {
  return Array.isArray(value) ? value.join('/') : String(value ?? '?')
}

// Why one rule of the stuck champion did not act (codes from no_decision_report).
function ruleCheckText(lang: Lang, rule: StuckRule) {
  const en = lang === 'en'
  switch (rule.code) {
    case 'hero_form_mismatch': return en ? `needs champion form ${listText(rule.expected)}, current ${listText(rule.actual)}` : `要求英雄形态 ${listText(rule.expected)}，当前为 ${listText(rule.actual)}`
    case 'chimera_form_mismatch': {
      const expected = Array.isArray(rule.expected) ? rule.expected.map((form) => formNameByKey(lang, String(form))).join('/') : listText(rule.expected)
      const actual = formNameByKey(lang, String(rule.actual ?? '')) || '?'
      return en ? `only in ${expected} form (current ${actual})` : `只在${expected}形态生效（当前${actual}）`
    }
    case 'conditions_not_met': return en ? 'conditions not met' : '触发条件不满足'
    case 'default_no_ready_skill': return en ? 'no skill in its priority list is ready with a legal target' : '优先列表中没有已就绪且目标合法的技能'
    case 'trial_no_action': return en ? 'no safe trial action or basic skill available' : '没有可安全执行的试炼动作或基础技能'
    case 'skill_unavailable': return en ? 'conditions met, but the skill is not ready or has no legal target' : '条件满足，但指定技能未就绪或没有合法目标'
    default: return toolText(lang, rule.reason ?? rule.code)
  }
}

// Findings a player can act on, strongest first.
function findings(lang: Lang, aggregate: Aggregate, heroes: SimulationHero[], trialById: Map<number, SimulationTrial>) {
  const en = lang === 'en'
  const lines: { tone: 'bad' | 'warn' | 'good'; text: string }[] = []
  const finished = Math.max(aggregate.finishedRuns, 1)
  const stuckGroups = new Map<string, StuckRun[]>()
  for (const run of aggregate.stuckRuns ?? []) {
    const key = `${run.activeHeroTypeId}:${run.form}:${run.reason}`
    stuckGroups.set(key, [...(stuckGroups.get(key) ?? []), run])
  }
  for (const runs of stuckGroups.values()) {
    const first = runs[0]
    const name = heroName(lang, heroes, first.activeHeroTypeId)
    const turns = runs.map((run) => run.bossTurns ?? '?').join(en ? ', ' : '、')
    const form = formNameByKey(lang, first.form)
    lines.push({ tone: 'bad', text: en
      ? `${runs.length}/${finished} runs stopped: ${name}${form ? ` (${form} form)` : ''} ${stuckReasonText(lang, first.reason)} (Boss turn ${turns}). A live takeover would stall there; open the run for the champion's skills and every rule checked.`
      : `${runs.length}/${finished} 场模拟中断：${name}${form ? `（${form}形态）` : ''}${stuckReasonText(lang, first.reason)}（Boss 第 ${turns} 回合）。实战接管会在这里卡住；打开该场可查看当时的技能状态和逐条规则检查。` })
  }
  for (const trial of aggregate.trials.filter((item) => item.mandatory)) {
    if (trial.completedRuns >= finished) continue
    const label = trialShortLabel(lang, trialById.get(trial.trialId), trial.trialId)
    lines.push({ tone: 'bad', text: en
      ? `Mandatory trial ${label} was completed in only ${trial.completedRuns}/${finished} runs; median best progress ${percent(trial.bestRatioMedian)}.`
      : `必要试炼 ${label} 只在 ${trial.completedRuns}/${finished} 场完成；最佳进度中位 ${percent(trial.bestRatioMedian)}。` })
  }
  for (const event of aggregate.regroupEvents) {
    const turn = event.bossTurnMedian ?? '?'
    lines.push({ tone: 'bad', text: en
      ? `${event.runs}/${finished} runs could no longer complete the mandatory trials around Boss turn ${turn} (a live takeover would free-regroup there).`
      : `${event.runs}/${finished} 场在 Boss 第 ${turn} 回合前后已无法完成必要试炼（实战会在此时免费重整）。` })
  }
  for (const death of aggregate.deaths) {
    const name = heroName(lang, heroes, death.heroTypeId)
    const turn = death.firstBossTurnMedian ?? '?'
    lines.push({ tone: 'warn', text: en
      ? `${name} died in ${death.runs}/${finished} runs (median Boss turn ${turn}).`
      : `${name} 在 ${death.runs}/${finished} 场阵亡（中位 Boss 第 ${turn} 回合）。` })
  }
  const unused = aggregate.rules.filter((rule) => rule.ruleIndex !== null && rule.uses === 0)
  if (unused.length) {
    const names = unused.slice(0, 5).map((rule) => `#${rule.ruleIndex} ${ruleLabel(lang, rule.rule)}`).join(en ? ', ' : '、')
    lines.push({ tone: 'warn', text: en
      ? `${unused.length} rules were never used in any run: ${names}${unused.length > 5 ? ' …' : ''}`
      : `${unused.length} 条规则在所有模拟中都没有被用到：${names}${unused.length > 5 ? ' …' : ''}` })
  }
  if ((aggregate.reservationReleasesPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: en
      ? `On average ${aggregate.reservationReleasesPerRun} actions per run had to use a skill reserved for a strict or trial rule, because nothing else was usable. Check that each champion's default skill order has a usable fallback.`
      : `平均每场 ${aggregate.reservationReleasesPerRun} 次行动只能动用为严格/试炼规则保留的技能（没有其他可用技能）。请检查各英雄默认技能顺序是否有可用的备选。` })
  }
  if ((aggregate.autoTurnsPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: en
      ? `On average ${aggregate.autoTurnsPerRun} actions per run had no matching rule (this older report let the game's auto battle play them).`
      : `平均每场 ${aggregate.autoTurnsPerRun} 次行动没有匹配的规则（这份旧报告由游戏自动战斗代打了这些回合）。` })
  }
  if (!lines.length) lines.push({ tone: 'good', text: en
    ? 'Every mandatory trial was completed in every run; nothing needs attention.'
    : '所有必要试炼在每一场模拟中都完成了，没有发现需要处理的问题。' })
  return lines
}

export function ruleUsageByIndex(summary: SimulationSummary | null | undefined) {
  const usage = new Map<number, RuleAggregate>()
  for (const rule of summary?.aggregate?.rules ?? []) if (rule.ruleIndex !== null) usage.set(rule.ruleIndex, rule)
  return usage
}

function jobText(lang: Lang, job: SimulationJob) {
  const en = lang === 'en'
  const done = `${job.finishedRuns ?? 0}/${job.runs}`
  switch (job.phase ?? job.status) {
    case 'preparing': return en ? 'Preparing the offline engine' : '准备离线引擎'
    case 'running': return (en ? `Simulating · ${done} runs done` : `正在模拟 · 已完成 ${done} 场`)
      + (job.currentBossTurn ? (en ? ` · Boss turn ${job.currentBossTurn}` : ` · Boss 第 ${job.currentBossTurn} 回合`) : '')
    case 'complete': return en ? 'Simulation complete' : '模拟完成'
    case 'cancelled': return en ? `Stopped (${done} runs done)` : `已停止（完成 ${done} 场）`
    case 'failed': return (en ? 'Simulation failed: ' : '模拟无法进行：') + toolText(lang, job.reason ?? job.message ?? '')
    default: return toolText(lang, job.message ?? '')
  }
}

export function ChimeraSimulationPanel({ language, overview, heroes, trialById, request, draft, strategyId, strategyName, strategyTeam, pid, onOpenReport, onSummary }: {
  language: Lang
  overview?: SimulationOverview
  heroes: SimulationHero[]
  trialById: Map<number, SimulationTrial>
  request: Request
  draft: unknown
  strategyId: string
  strategyName: string
  strategyTeam: number[]
  pid?: number
  onOpenReport: (id: string) => void
  onSummary: (summary: SimulationSummary | null) => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const captures = overview?.captures ?? []
  const job = overview?.job ?? null
  const [captureId, setCaptureId] = useState('')
  const [runs, setRuns] = useState(10)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<SimulationSummary | null>(null)
  const running = job?.status === 'running'
  useEffect(() => {
    if (captureId && captures.some((item) => item.id === captureId)) return
    const sameTeam = captures.find((item) => item.teamHeroTypeIds.length && item.teamHeroTypeIds.every((typeId) => strategyTeam.includes(typeId)))
    setCaptureId((sameTeam ?? captures[0])?.id ?? '')
  }, [captures, captureId, strategyTeam])
  const latestId = job && job.status !== 'running' ? job.id : overview?.recent?.[0]?.id
  useEffect(() => {
    if (!latestId || summary?.id === latestId) return
    let cancelled = false
    request<SimulationSummary>(`/api/chimera-simulation?id=${encodeURIComponent(latestId)}`)
      .then((value) => { if (!cancelled) { setSummary(value); onSummary(value) } })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [latestId, summary?.id, request, onSummary])
  const capture = captures.find((item) => item.id === captureId)
  const teamDiffers = capture && strategyTeam.length > 0 && !capture.teamHeroTypeIds.every((typeId) => strategyTeam.includes(typeId))
  const start = async () => {
    setError('')
    try {
      await request('/api/chimera-simulation/start', { method: 'POST', body: JSON.stringify({ config: draft, strategyId, strategyName, captureId, runs, pid }) })
    } catch (reason) {
      setError(toolText(language, reason instanceof Error ? reason.message : String(reason)))
    }
  }
  const stop = () => { void request('/api/chimera-simulation/stop', { method: 'POST', body: '{}' }).catch(() => undefined) }
  const aggregate = summary?.aggregate
  const hint = running
    ? t(`模拟中 ${job?.finishedRuns ?? 0}/${job?.runs ?? 0}`, `Running ${job?.finishedRuns ?? 0}/${job?.runs ?? 0}`)
    : aggregate
      ? t(`必要试炼全完成 ${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} 场`, `All mandatory trials in ${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} runs`)
      : captures.length ? t('可以模拟', 'Ready') : t('需要一场奇美拉开局数据', 'Needs a Chimera battle capture')
  return (
    <LangContext.Provider value={language}>
      <CollapsiblePanel id="chimera:simulation" title={t('策略模拟', 'Strategy Simulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!captures.length ? <p className="muted">{t(
            '还没有奇美拉开局数据。用 1.0.6 预览版从开局接管打一场奇美拉后，就能用那场战斗的队伍、装备和奇美拉模拟当前规则。',
            'No Chimera battle has been captured yet. Take over one Chimera battle from its start with the 1.0.6 preview; its team, gear and Chimera can then be used to simulate your current rules.')}</p> : <>
            <p className="muted">{t(
              '在离线原版引擎里重打一场已保存的奇美拉：第 1 场用原战斗的随机种子（完全复现那场战斗），其余场次换随机种子，检验规则是否稳定。使用的是编辑器里当前的规则（包括未保存的修改）。',
              "Replays a saved Chimera battle in an offline copy of the game's own engine. Run 1 uses the original battle seed (an exact replay of that battle); the other runs use other seeds to test how stable the rules are. Uses the rules currently in the editor, including unsaved changes.")}</p>
            <p className="muted">{t(
              '模拟严格按规则出手，不会让游戏自动战斗代打：某个英雄轮到出手却没有可执行的规则（实战接管会卡住）时，那一场就在此中断并记录原因。',
              "Runs follow the rules strictly and never let the game's auto battle play a turn: when a champion has to act and no rule gives a usable action (a live takeover would stall there), that run stops and records why.")}</p>
            <div className="simulation-controls">
              <label className="field"><span>{t('战斗数据', 'Battle data')}</span><select value={captureId} onChange={(event) => setCaptureId(event.target.value)} disabled={running}>
                {captures.map((item) => <option key={item.id} value={item.id}>{item.capturedAt} · {t('难度', 'Difficulty')} {item.difficulty ?? '?'} · {item.teamHeroTypeIds.map((typeId) => heroName(language, heroes, typeId)).join(t('、', ', '))}</option>)}
              </select></label>
              <label className="field"><span>{t('模拟场数', 'Runs')}</span><select value={runs} onChange={(event) => setRuns(Number(event.target.value))} disabled={running}>
                {RUN_CHOICES.map((value) => <option key={value} value={value}>{value === 1 ? t('1 场（只复现原战斗）', '1 (original battle only)') : t(`${value} 场`, `${value} runs`)}</option>)}
              </select></label>
              {running
                ? <button className="button ghost" onClick={stop}><CircleStop size={16} />{t('停止', 'Stop')}</button>
                : <button className="button primary" onClick={() => void start()} disabled={!captureId}><CirclePlay size={16} />{t('模拟当前规则', 'Simulate current rules')}</button>}
            </div>
            {teamDiffers && <p className="simulation-warning"><AlertTriangle size={14} />{t(
              '这场战斗的队伍和当前策略组不同；模拟按战斗里的英雄出手，没有规则的英雄一轮到出手，那一场模拟就会中断。',
              "This battle's team differs from the strategy's team. The simulation uses the battle's champions; a run stops as soon as a champion without rules has to act.")}</p>}
            {error && <p className="simulation-warning"><AlertTriangle size={14} />{error}</p>}
            {job && job.status !== 'complete' && <div className="simulation-progress">
              <div className="bar"><span style={{ width: `${Math.round(((job.finishedRuns ?? 0) / Math.max(job.runs, 1)) * 100)}%` }} /></div>
              <span>{jobText(language, job)}</span>
            </div>}
            {aggregate && summary && <SimulationDigest summary={summary} heroes={heroes} trialById={trialById} onOpen={() => onOpenReport(summary.id)} />}
            {(overview?.recent?.length ?? 0) > 1 && <div className="simulation-history">
              <small>{t('最近的模拟', 'Recent simulations')}</small>
              {overview!.recent.slice(0, 6).map((item) => (
                <button key={item.id} className="simulation-history-row" onClick={() => onOpenReport(item.id)}>
                  <span>{item.createdAt?.slice(5, 16)}</span><strong>{item.strategyName || t('未命名策略', 'Unnamed strategy')}</strong>
                  <em>{item.status === 'complete'
                    ? t(`必要试炼全完成 ${item.allMandatoryRuns ?? 0}/${item.finishedRuns ?? 0}`, `All mandatory ${item.allMandatoryRuns ?? 0}/${item.finishedRuns ?? 0}`)
                      + (item.stuckRuns ? t(` · 中断 ${item.stuckRuns}`, ` · ${item.stuckRuns} stopped`) : '')
                    : item.status === 'failed' ? t('失败', 'Failed') : item.status === 'cancelled' ? t('已停止', 'Stopped') : item.status}</em>
                </button>
              ))}
            </div>}
          </>}
        </section>
      </CollapsiblePanel>
    </LangContext.Provider>
  )
}

function battleForecastLabel(lang: Lang, forecast: BattleForecastVerdict & { verdict?: string }) {
  const en = lang === 'en'
  switch (forecast.status) {
    case 'waiting_capture': return en ? 'Waiting for the opening data' : '等待本局开局数据'
    case 'running': return en ? 'Simulating in the background; the battle continues' : '后台模拟中，战斗照常进行'
    case 'not_opening': return en ? 'Not taken over from the opening; skipped' : '不是从开局接管，已跳过'
    case 'unavailable': return en ? 'Undetermined; no regroup based on it' : '无法判断，本场不据此重整'
    default: return forecast.verdict === 'retry'
      ? (en ? 'Goals predicted to fail; free regroup' : '预计无法完成目标，已免费重整')
      : (en ? 'Goals predicted to be met; battle continues' : '预计能完成目标，继续战斗')
  }
}

export function BattleForecastPanel({ language, enabled, forecast, history, heroes, onOpenReport }: {
  language: Lang
  enabled: boolean
  forecast?: BattleForecastTelemetry
  history: SimulationRecent[]
  heroes: SimulationHero[]
  onOpenReport: (id: string) => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const previous = history.filter((entry) => entry.verdict && entry.id !== forecast?.recordId)
  const latest = forecast ?? (history[0]?.verdict ? { ...history[0].verdict } : undefined)
  const hint = !enabled ? t('已关闭', 'Off') : latest ? battleForecastLabel(language, latest) : t('暂无记录', 'No records yet')
  return (
    <LangContext.Provider value={language}>
      <CollapsiblePanel id="chimera:battle-forecast" title={t('开局整场模拟', 'Opening battle simulation')} hint={hint}>
        <div className="hydra-forecast-body" data-i18n-skip>
          {forecast && <article className={`hydra-forecast-entry ${forecast.status}`}>
            <header><strong>{t('本场', 'This battle')}</strong><span>{battleForecastLabel(language, forecast)}{forecast.finishedAt ? ` · ${forecast.finishedAt}` : ''}</span></header>
            {forecast.conclusion && <p>{toolText(language, forecast.conclusion)}</p>}
            {forecast.stuck && <p className="bad">{t(
              `模拟在 Boss 第 ${forecast.stuck.bossTurns ?? '?'} 回合中断：${heroName(language, heroes, forecast.stuck.activeHeroTypeId)}${stuckReasonText(language, forecast.stuck.reason)}`,
              `The simulation stopped on Boss turn ${forecast.stuck.bossTurns ?? '?'}: ${heroName(language, heroes, forecast.stuck.activeHeroTypeId)} — ${stuckReasonText(language, forecast.stuck.reason)}`)}</p>}
            {forecast.recordId && <button className="button ghost" onClick={() => onOpenReport(forecast.recordId!)}><ListOrdered size={15} />{t('查看这场的模拟报告', 'Open this battle\'s simulation report')}</button>}
          </article>}
          {previous.slice(0, 5).map((entry) => (
            <button key={entry.id} className="simulation-history-row" onClick={() => onOpenReport(entry.id)}>
              <span>{entry.createdAt?.slice(5, 16)}</span><strong>{entry.strategyName || t('未命名策略', 'Unnamed strategy')}</strong>
              <em className={entry.verdict?.verdict === 'retry' ? 'bad' : ''}>{battleForecastLabel(language, entry.verdict!)}</em>
            </button>
          ))}
          {!forecast && !previous.length && <p className="hydra-forecast-empty">{enabled
            ? t('从开局接管奇美拉时，工具会在后台按当前规则模拟整场战斗（约 5–10 秒），与已进行的实战回合逐项核对一致后，若预计必要试炼或最低伤害无法完成、或规则会卡住，立即免费重整。每场的模拟报告会保留在这里。',
              'When a Chimera takeover starts at the opening, the tool simulates the whole battle with the current rules in the background (about 5–10 s). Once it matches every live turn so far, a predicted failure of the mandatory trials or minimum damage, or rules that would stall, triggers a free regroup at once. Each battle\'s report is kept here.')
            : t('已在“战斗目标”中关闭开局整场模拟。', 'The opening battle simulation is turned off under Battle goals.')}</p>}
        </div>
      </CollapsiblePanel>
    </LangContext.Provider>
  )
}

function SimulationDigest({ summary, heroes, trialById, onOpen }: { summary: SimulationSummary; heroes: SimulationHero[]; trialById: Map<number, SimulationTrial>; onOpen: () => void }) {
  const { lang, t } = useText()
  const aggregate = summary.aggregate!
  const mandatory = aggregate.trials.filter((trial) => trial.mandatory)
  const others = aggregate.trials.filter((trial) => !trial.mandatory && trial.completedRuns > 0)
  return (
    <div className="simulation-digest">
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('未命名策略', 'Unnamed strategy')}</strong><span>{summary.createdAt} · {t(`${aggregate.finishedRuns} 场`, `${aggregate.finishedRuns} runs`)}</span></div>
      <div className="simulation-trials">
        {mandatory.map((trial) => <span key={trial.trialId} className={`trial-rate ${trial.completedRuns === aggregate.finishedRuns ? 'good' : trial.completedRuns ? 'warn' : 'bad'}`} title={plainText(trialById.get(trial.trialId)?.description)}>
          {t('必要', 'Mandatory')} · {trialShortLabel(lang, trialById.get(trial.trialId), trial.trialId)} <b>{trial.completedRuns}/{trial.runs}</b>{trial.completedRuns < trial.runs && <em>{t('最佳', 'best')} {percent(trial.bestRatioMedian)}</em>}
        </span>)}
        {others.map((trial) => <span key={trial.trialId} className="trial-rate neutral" title={plainText(trialById.get(trial.trialId)?.description)}>{trialShortLabel(lang, trialById.get(trial.trialId), trial.trialId)} <b>{trial.completedRuns}/{trial.runs}</b></span>)}
      </div>
      <div className="simulation-facts">
        <span>{t('伤害中位', 'Median damage')} {damageText(lang, aggregate.damage.median || aggregate.bossDamage.median)}</span>
        {aggregate.deaths.length > 0 && <span>{t('阵亡：', 'Deaths: ')}{aggregate.deaths.map((death) => `${heroName(lang, heroes, death.heroTypeId)} ${t(`${death.runs}场`, `${death.runs} runs`)}`).join(t('、', ', '))}</span>}
        {(aggregate.stuckRuns?.length ?? 0) > 0 && <span className="bad">{t(`规则卡住中断 ${aggregate.stuckRuns!.length} 场`, `${aggregate.stuckRuns!.length} runs stopped by the rules`)}</span>}
        {(aggregate.autoTurnsPerRun ?? 0) > 0 && <span>{t('无规则出手', 'Actions without a rule')} {aggregate.autoTurnsPerRun}{t('/场', '/run')}</span>}
      </div>
      <button className="button ghost" onClick={onOpen}><ListOrdered size={15} />{t('查看完整报告', 'Open full report')}</button>
    </div>
  )
}

type Tab = 'overview' | 'trials' | 'rules' | 'log'

export function SimulationReport({ language, simulationId, onClose, heroes, trialById, request, onJumpToRule }: {
  language: Lang
  simulationId: string | null
  onClose: () => void
  heroes: SimulationHero[]
  trialById: Map<number, SimulationTrial>
  request: Request
  onJumpToRule: (index: number) => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const [summary, setSummary] = useState<SimulationSummary | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [runIndex, setRunIndex] = useState(1)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!simulationId) return
    setSummary(null)
    setTab('overview')
    setRunIndex(1)
    setError('')
    request<SimulationSummary>(`/api/chimera-simulation?id=${encodeURIComponent(simulationId)}`)
      .then(setSummary).catch((reason) => setError(toolText(language, reason instanceof Error ? reason.message : String(reason))))
  }, [simulationId, request, language])
  const aggregate = summary?.aggregate
  const description = summary
    ? t(`${summary.createdAt} · 战斗数据 ${summary.capture.id} · 难度 ${summary.capture.difficulty ?? '?'} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} 场完成模拟（第 1 场为原战斗复现）`,
        `${summary.createdAt} · Battle data ${summary.capture.id} · Difficulty ${summary.capture.difficulty ?? '?'} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} runs simulated (run 1 replays the original battle)`)
    : t('读取中…', 'Loading…')
  const tabs: [Tab, string][] = [['overview', t('总览', 'Overview')], ['trials', t('试炼', 'Trials')], ['rules', t('规则', 'Rules')], ['log', t('出手记录', 'Action log')]]
  return (
    <LangContext.Provider value={language}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading">
              <span><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('开局整场模拟报告', 'Opening battle simulation report') : t('策略模拟报告', 'Strategy simulation report')} · {summary?.strategy.name || '…'}</Dialog.Title>
                <Dialog.Description>{description}</Dialog.Description></span>
              <Dialog.Close className="icon-button" aria-label={t('关闭模拟报告', 'Close simulation report')}><X size={19} /></Dialog.Close>
            </div>
            <div className="simulation-tabs" role="tablist">
              {tabs.map(([key, label]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
              ))}
            </div>
            <div className="simulation-report-body">
              {error && <p className="simulation-warning"><AlertTriangle size={14} />{error}</p>}
              {summary?.status === 'failed' && <p className="simulation-warning"><AlertTriangle size={14} />{t('模拟失败：', 'Simulation failed: ')}{toolText(language, summary.reason ?? '')}</p>}
              {aggregate && summary && tab === 'overview' && <OverviewTab summary={summary} heroes={heroes} trialById={trialById} onRun={(index) => { setRunIndex(index); setTab('log') }} onJump={(index) => { onClose(); onJumpToRule(index) }} />}
              {aggregate && tab === 'trials' && <TrialsTab aggregate={aggregate} trialById={trialById} />}
              {aggregate && tab === 'rules' && <RulesTab aggregate={aggregate} trialById={trialById} onJump={(index) => { onClose(); onJumpToRule(index) }} />}
              {summary && tab === 'log' && <LogTab summary={summary} runIndex={runIndex} setRunIndex={setRunIndex} heroes={heroes} trialById={trialById} request={request} onJump={(index) => { onClose(); onJumpToRule(index) }} />}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </LangContext.Provider>
  )
}

function deathCounts(deaths: { heroTypeId: number }[]) {
  const counts = new Map<number, number>()
  for (const death of deaths) counts.set(death.heroTypeId, (counts.get(death.heroTypeId) ?? 0) + 1)
  return [...counts.entries()]
}

// One stopped run: the champion's skills at that moment and why each of its
// rules did not act.
function StuckCard({ run, stuck, heroes, onJump, onLog }: { run: { index: number; exact?: boolean }; stuck: StuckDetail; heroes: SimulationHero[]; onJump: (index: number) => void; onLog?: () => void }) {
  const { lang, t } = useText()
  const heroTypeId = stuck.activeHeroTypeId
  const name = heroName(lang, heroes, heroTypeId)
  const form = formNameByKey(lang, stuck.form)
  return (
    <article className="stuck-card">
      <header>
        <AlertTriangle size={15} />
        <strong>{t(`第 ${run.index} 场${run.exact ? '（原战斗）' : ''}在 Boss 第 ${stuck.bossTurns ?? '?'} 回合中断`, `Run ${run.index}${run.exact ? ' (original battle)' : ''} stopped on Boss turn ${stuck.bossTurns ?? '?'}`)}</strong>
        {onLog && <button className="link-button" onClick={onLog}>{t('出手记录', 'Action log')}</button>}
      </header>
      <p>{t(`${name}${form ? `（奇美拉${form}形态）` : ''}轮到出手：${stuckReasonText(lang, stuck.reason)}。实战接管会在这里停下等待。`,
        `${name}${form ? ` (Chimera ${form} form)` : ''} had to act: ${stuckReasonText(lang, stuck.reason)}. A live takeover would stop and wait here.`)}</p>
      {stuck.rule && <p className="muted">{t('规则：', 'Rule: ')}{ruleLabel(lang, stuck.rule)}</p>}
      {(stuck.skills?.length ?? 0) > 0 && <div className="stuck-skills">
        {stuck.skills!.map((skill) => (
          <span key={skill.typeId} className={`stuck-skill ${skill.ready ? 'ready' : ''}`}>
            <b>{typeof heroTypeId === 'number' ? skillName(lang, heroes, heroTypeId, skill.typeId) : skill.typeId}</b>
            <em>{skill.ready ? t('就绪', 'ready') : skill.cooldown ? t(`冷却 ${skill.cooldown}`, `cooldown ${skill.cooldown}`) : t('不可用', 'unavailable')}</em>
            {skill.reserved && <em className="warn">{t('被规则保留', 'reserved by a rule')}</em>}
            {skill.ready && skill.validTargets === 0 && <em className="warn">{t('无合法目标', 'no legal target')}</em>}
          </span>
        ))}
      </div>}
      {(stuck.rules?.length ?? 0) > 0 ? <ul className="stuck-rules">
        {stuck.rules!.map((rule) => (
          <li key={rule.ruleIndex}>
            <button className="link-button" onClick={() => onJump(rule.ruleIndex)}>#{rule.ruleIndex} {ruleLabel(lang, rule.name)}</button>
            <span>{ruleCheckText(lang, rule)}</span>
          </li>
        ))}
      </ul> : stuck.reason === 'no_matching_rule' && <p className="muted">{t('这个英雄没有任何规则。', 'This champion has no rules at all.')}</p>}
    </article>
  )
}

function OverviewTab({ summary, heroes, trialById, onRun, onJump }: { summary: SimulationSummary; heroes: SimulationHero[]; trialById: Map<number, SimulationTrial>; onRun: (index: number) => void; onJump: (index: number) => void }) {
  const { lang, t } = useText()
  const aggregate = summary.aggregate!
  return <>
    <div className="simulation-metrics">
      <div><small>{t('必要试炼全部完成', 'All mandatory trials completed')}</small><strong>{t(`${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} 场`, `${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} runs`)}</strong></div>
      <div><small>{t('伤害（中位 · 最低–最高）', 'Damage (median · min–max)')}</small><strong>{damageText(lang, aggregate.damage.median || aggregate.bossDamage.median)}</strong><span>{damageText(lang, aggregate.damage.min || aggregate.bossDamage.min)} – {damageText(lang, aggregate.damage.max || aggregate.bossDamage.max)}</span></div>
      {aggregate.minimumDamage > 0 && <div><small>{t('最低伤害目标', 'Minimum damage goal')}</small><strong>{damageText(lang, aggregate.minimumDamage)}</strong></div>}
      <div><small>{t('规则卡住中断', 'Stopped by the rules')}</small><strong className={aggregate.stuckRuns?.length ? 'bad' : ''}>{t(`${aggregate.stuckRuns?.length ?? 0}/${aggregate.finishedRuns} 场`, `${aggregate.stuckRuns?.length ?? 0}/${aggregate.finishedRuns} runs`)}</strong></div>
    </div>
    {summary.verdict?.conclusion && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}>{toolText(lang, summary.verdict.conclusion)}</p>}
    <ul className="simulation-findings">
      {findings(lang, aggregate, heroes, trialById).map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}{line.text}</li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!} heroes={heroes} onJump={onJump} onLog={() => onRun(run.index)} />)}
    <table className="simulation-table">
      <thead><tr><th>{t('场次', 'Run')}</th><th>{t('随机种子', 'Seed')}</th><th>{t('结果', 'Result')}</th><th>{t('必要试炼', 'Mandatory trials')}</th><th>{t('完成试炼', 'Trials completed')}</th><th>{t('伤害', 'Damage')}</th><th>{t('阵亡', 'Deaths')}</th><th>{t('Boss 回合', 'Boss turns')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => (
          <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{t('原战斗', 'Original')}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t(`中断 · Boss 第 ${run.stuck.bossTurns ?? '?'} 回合`, `Stopped · Boss turn ${run.stuck.bossTurns ?? '?'}`)}</em>
              : run.status === 'complete' ? t('打完', 'Finished') : toolText(lang, run.reason ?? run.status)}</td>
            <td>{run.mandatory.map((item) => <span key={item.trialId} className={`dot ${item.completed ? 'good' : 'bad'}`} title={`${trialShortLabel(lang, trialById.get(item.trialId), item.trialId)} ${item.completed
              ? t(`第 ${item.completedBossTurn} 回合完成`, `completed on Boss turn ${item.completedBossTurn}`)
              : t(`最佳 ${percent(item.bestRatio)}`, `best ${percent(item.bestRatio)}`)}`} />)}</td>
            <td>{Object.keys(run.completedTrials).length}</td>
            <td>{damageText(lang, run.damage || run.bossDamageTaken)}</td>
            <td>{run.deaths.length ? deathCounts(run.deaths).map(([typeId, count]) => `${heroName(lang, heroes, typeId)}${count > 1 ? ` ×${count}` : ''}`).join(t('、', ', ')) : '—'}</td>
            <td>{run.bossTurns ?? '—'}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('出手记录', 'Action log')}</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  </>
}

function TrialsTab({ aggregate, trialById }: { aggregate: Aggregate; trialById: Map<number, SimulationTrial> }) {
  const { lang, t } = useText()
  const [showAll, setShowAll] = useState(false)
  const windows = useMemo(() => [...new Set(aggregate.trials.flatMap((trial) => Object.keys(trial.windows).map(Number)))].sort((a, b) => a - b), [aggregate])
  const visible = aggregate.trials.filter((trial) => showAll || trial.mandatory || trial.completedRuns > 0 || trial.bestRatioMax > 0)
  const byForm = (trial: TrialAggregate) => FORM_ORDER.indexOf(trialById.get(trial.trialId)?.form ?? '')
  return <>
    <label className="simulation-toggle"><input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />{t('显示没有进度的试炼', 'Show trials without progress')}</label>
    <div className="simulation-scroll">
      <table className="simulation-table trials">
        <thead><tr><th>{t('试炼', 'Trial')}</th><th>{t('完成', 'Completed')}</th><th>{t('完成回合（中位）', 'Completion turn (median)')}</th><th>{t('最佳进度（中位）', 'Best progress (median)')}</th>{windows.map((window) => <th key={window}>{windowLabel(lang, window)}</th>)}</tr></thead>
        <tbody>
          {visible.slice().sort((a, b) => byForm(a) - byForm(b) || a.trialId - b.trialId).map((trial) => {
            const info = trialById.get(trial.trialId)
            return <tr key={trial.trialId} className={trial.mandatory ? 'mandatory' : ''}>
              <td title={plainText(info?.description)}><strong>{trialShortLabel(lang, info, trial.trialId)}</strong>{trial.mandatory && <em className="tag">{t('必要', 'Mandatory')}</em>}<small>{plainText(info?.description)}</small></td>
              <td className={trial.completedRuns === trial.runs ? 'good' : trial.completedRuns ? 'warn' : 'bad'}>{trial.completedRuns}/{trial.runs}</td>
              <td>{trial.completedBossTurnMedian ?? '—'}</td>
              <td>{percent(trial.bestRatioMedian)}</td>
              {windows.map((window) => {
                const cell = trial.windows[String(window)]
                return <td key={window}>{cell ? <span className="cell-bar" title={`${t('最高', 'Max')} ${percent(cell.max)}`}><span style={{ width: `${Math.min(100, (cell.median ?? 0) * 100)}%` }} /><em>{percent(cell.median)}</em></span> : ''}</td>
              })}
            </tr>
          })}
        </tbody>
      </table>
    </div>
    <p className="muted">{t(
      '每列是一个五回合的形态窗口；数值为该窗口结束时的进度中位数（100% 表示已完成）。同一形态会出现两次，前一次没完成的试炼可以在第二次继续。',
      'Each column is a five-turn form window; values are the median progress at the end of that window (100% = completed). Each form appears twice, and an unfinished trial can continue in its second window.')}</p>
  </>
}

function RulesTab({ aggregate, trialById, onJump }: { aggregate: Aggregate; trialById: Map<number, SimulationTrial>; onJump: (index: number) => void }) {
  const { lang, t } = useText()
  return <>
    <p className="muted">{t(
      '按策略中的顺序列出每条规则在模拟里的使用情况。“试炼贡献”是这条规则的出手平均每场推进了该试炼多少进度。',
      'Every rule in strategy order with how it was used in the simulations. "Trial contribution" is how much progress the rule\'s actions added to each trial per run on average.')}</p>
    <div className="simulation-scroll">
      <table className="simulation-table rules">
        <thead><tr><th>#</th><th>{t('规则', 'Rule')}</th><th>{t('每场使用', 'Uses per run')}</th><th>{t('用到的场次', 'Runs used')}</th><th>{t('伤害占比', 'Damage share')}</th><th>{t('试炼贡献', 'Trial contribution')}</th></tr></thead>
        <tbody>
          {aggregate.rules.map((rule, index) => {
            const label = rule.auto ? t('（无可用规则，自动战斗）', '(no usable rule, auto battle)') : ruleLabel(lang, rule.rule)
            return <tr key={`${rule.ruleIndex ?? 'x'}-${index}`} className={rule.ruleIndex !== null && rule.uses === 0 ? 'unused' : rule.auto ? 'auto' : ''}>
              <td>{rule.ruleIndex ?? '—'}</td>
              <td>{rule.ruleIndex !== null ? <button className="link-button" onClick={() => onJump(rule.ruleIndex!)}>{label}</button> : label}{rule.ruleIndex !== null && rule.uses === 0 && <em className="tag warn">{t('未使用', 'Unused')}</em>}</td>
              <td>{rule.usesPerRun}</td>
              <td>{rule.runsUsed}/{aggregate.finishedRuns}</td>
              <td><span className="cell-bar"><span style={{ width: `${Math.min(100, rule.damageShare * 100)}%` }} /><em>{percent(rule.damageShare, 1)}</em></span></td>
              <td>{Object.entries(rule.trialGains).filter(([, gain]) => gain >= 0.005).sort(([, a], [, b]) => b - a).map(([trial, gain]) => <span key={trial} className="trial-chip" title={plainText(trialById.get(Number(trial))?.description)}>{trialShortLabel(lang, trialById.get(Number(trial)), Number(trial))} +{percent(gain)}</span>)}</td>
            </tr>
          })}
        </tbody>
      </table>
    </div>
  </>
}

const PAGE_TURNS = 8

function LogTab({ summary, runIndex, setRunIndex, heroes, trialById, request, onJump }: { summary: SimulationSummary; runIndex: number; setRunIndex: (index: number) => void; heroes: SimulationHero[]; trialById: Map<number, SimulationTrial>; request: Request; onJump: (index: number) => void }) {
  const { lang, t } = useText()
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [hero, setHero] = useState(0)
  const [onlyTrials, setOnlyTrials] = useState(false)
  const [onlyReserved, setOnlyReserved] = useState(false)
  const [showBoss, setShowBoss] = useState(false)
  const [pages, setPages] = useState(1)
  useEffect(() => {
    setDetail(null)
    setPages(1)
    request<RunDetail>(`/api/chimera-simulation?id=${encodeURIComponent(summary.id)}&run=${runIndex}`).then(setDetail).catch(() => undefined)
  }, [summary.id, runIndex, request])
  const actors = useMemo(() => new Map((detail?.actors ?? []).map((actor) => [actor.actorId, actor])), [detail])
  const groups = useMemo(() => {
    const rows = (detail?.timeline ?? []).filter((row) =>
      (showBoss || row.source !== 'enemy') && (!hero || row.actorTypeId === hero)
      && (!onlyTrials || row.trials.some((trial) => (trial.after ?? 0) > (trial.before ?? 0) || trial.completed))
      && (!onlyReserved || row.reservationReleased === true || row.source === 'auto'))
    const map = new Map<number, TimelineRow[]>()
    for (const row of rows) map.set(row.bossTurns, [...(map.get(row.bossTurns) ?? []), row])
    return [...map.entries()]
  }, [detail, hero, onlyTrials, onlyReserved, showBoss])
  const chimera = t('奇美拉', 'Chimera')
  const targetName = (id: number) => {
    const actor = actors.get(id)
    if (!actor) return id < 0 ? '—' : `#${id}`
    return actor.player ? heroName(lang, heroes, actor.heroTypeId) : chimera
  }
  const team = [...new Set((detail?.actors ?? []).filter((actor) => actor.player).map((actor) => actor.heroTypeId))]
  const remaining = groups.length - pages * PAGE_TURNS
  return <>
    <div className="simulation-log-filters">
      <div className="run-picker">{(summary.runs ?? []).map((run) => run.index).sort((a, b) => a - b).map((index) => (
        <button key={index} className={index === runIndex ? 'active' : ''} onClick={() => setRunIndex(index)}>{index === 1 ? t('1 原战斗', '1 Original') : index}</button>
      ))}</div>
      <select value={hero} onChange={(event) => setHero(Number(event.target.value))}><option value={0}>{t('全部英雄', 'All champions')}</option>{team.map((typeId) => <option key={typeId} value={typeId}>{heroName(lang, heroes, typeId)}</option>)}</select>
      <label><input type="checkbox" checked={onlyTrials} onChange={(event) => setOnlyTrials(event.target.checked)} />{t('只看推进试炼的出手', 'Only actions that advanced a trial')}</label>
      <label><input type="checkbox" checked={onlyReserved} onChange={(event) => setOnlyReserved(event.target.checked)} />{t('只看动用保留技能的出手', 'Only actions that used a reserved skill')}</label>
      <label><input type="checkbox" checked={showBoss} onChange={(event) => setShowBoss(event.target.checked)} />{t('显示奇美拉的行动', "Show the Chimera's actions")}</label>
    </div>
    {!detail ? <p className="muted">{t('读取中…', 'Loading…')}</p> : <div className="simulation-log">
      {groups.slice(0, pages * PAGE_TURNS).map(([bossTurns, rows]) => {
        const damage = rows.reduce((total, row) => total + (row.source !== 'enemy' ? row.damage : 0), 0)
        return <details key={bossTurns} open={bossTurns >= 6 && rows.some((row) => row.trials.length > 0)}>
          <summary><strong>{t(`Boss 第 ${bossTurns} 回合后`, `After Boss turn ${bossTurns}`)}</strong><span>{t(
            `${formName(lang, rows[0].form)}形态 · ${rows.length} 次行动 · 伤害 ${damageText(lang, damage)}`,
            `${formName(lang, rows[0].form)} form · ${rows.length} actions · damage ${damageText(lang, damage)}`)}</span></summary>
          {rows.map((row, index) => (
            <div key={index} className={`log-row ${row.source}`}>
              <span className="who">{row.source === 'enemy' ? chimera : heroName(lang, heroes, row.actorTypeId)}</span>
              <span className="what">{row.source === 'enemy' ? t(`技能 ${row.skillTypeId}`, `Skill ${row.skillTypeId}`) : skillName(lang, heroes, row.actorTypeId, row.skillTypeId)} → {targetName(row.targetId)}</span>
              <span className="why">{row.source === 'policy' ? `#${row.ruleIndex ?? '—'} ${ruleLabel(lang, row.rule)}`
                : row.source === 'auto' ? `${t('自动：', 'Auto: ')}${row.autoDetail ? toolText(lang, row.autoDetail) : t('无可用规则', 'no usable rule')}` : ''}
                {row.reservationReleased && <em className="tag warn">{t('动用保留技能', 'reserved skill used')}</em>}</span>
              <span className="dmg">{row.damage ? damageText(lang, row.damage) : ''}</span>
              <span className="trials">{row.trials.filter((trial) => trial.completed || (trial.after ?? 0) !== (trial.before ?? 0)).map((trial) => (
                <em key={trial.trialId} className={trial.completed ? 'good' : ''} title={plainText(trialById.get(trial.trialId)?.description)}>{trialShortLabel(lang, trialById.get(trial.trialId), trial.trialId)} {percent(trial.before)}→{percent(trial.after)}{trial.completed ? ' ✓' : ''}</em>
              ))}{row.deaths.filter((id) => id >= 0).map((id) => <em key={`d${id}`} className="bad">{t(`${targetName(id)} 阵亡`, `${targetName(id)} died`)}</em>)}</span>
            </div>
          ))}
        </details>
      })}
      {remaining <= 0 && detail.stuck && <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck} heroes={heroes} onJump={onJump} />}
      {remaining > 0 && <button className="button ghost" onClick={() => setPages((value) => value + 1)}>{t(`显示后面的回合（还有 ${remaining} 个 Boss 回合）`, `Show later turns (${remaining} more Boss turns)`)}</button>}
      {!groups.length && <p className="muted">{t('没有符合筛选条件的行动。', 'No actions match the filters.')}</p>}
    </div>}
  </>
}

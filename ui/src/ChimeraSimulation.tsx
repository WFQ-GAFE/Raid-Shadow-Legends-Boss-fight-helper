import { useEffect, useMemo, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { CollapsiblePanel } from './CollapsiblePanel'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { HoverCard } from './HoverCard'
import {
  ActionLog, AdviceNote, CapturePicker, HeroIcon, RulesTable, SimProvider, SimulationTeamButton, StuckCard, TeamSourcePicker, TextWithTrials,
  TrialCard, TrialTag,
  adviceTitle,
  bossDifficultyText, damageText, hasChainedSkill, formName, formNameByKey, originalRunLabel, percent, plainText, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel,
  teamSourceOptions, toolText, trialShortLabel, useSim,
  type ActorInfo, type BossSkill, type Lang, type LogRow, type RuleAggregate, type SimEffect, type SimulationHero, type SimulationTrial, type StuckDetail,
  type FailureAdvice, type SimulationTeam, type TeamSource, type TeamSources,
} from './SimulationShared'

// Strategy simulation: the captured Chimera battle replayed in the isolated
// original engine with the rules being edited. See tools/chimera_simulation*.py.

type Request = <T>(path: string, init?: RequestInit) => Promise<T>

export type { RuleAggregate, SimulationHero, SimulationTrial } from './SimulationShared'

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
  currentTurn?: number | null
  phase?: 'preparing' | 'running' | 'complete' | 'cancelled' | 'failed'
  reason?: string
  message?: string
  startedAt?: string
  advice?: FailureAdvice | null
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
  damageMedian?: number | null
  regroupRuns?: number
  verdict?: BattleForecastVerdict
}

export type SimulationOverview = { captures: SimulationCapture[]; job?: SimulationJob | null; recent: SimulationRecent[]; battleForecasts?: SimulationRecent[]; teamSources?: TeamSources }

type StuckRun = { index: number; seed: number; exact: boolean; reason: string; turn?: number; bossTurns?: number; form?: string | null; activeHeroTypeId?: number; rule?: string | null }

// The whole-battle simulation a takeover runs at each Chimera opening
// (tools/chimera_forecast_live.py).
type BattleForecastVerdict = {
  status: string
  reason?: string | null
  advice?: FailureAdvice | null
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
  capture: { id: string; stageId?: number; seed?: number; teamHeroTypeIds?: number[]; bossHeroTypeId?: number; difficulty?: number; teamSource?: string; teamSavedAt?: string }
  // The team the simulation ran with (reports from 1.1.1 on).
  team?: SimulationTeam | null
  runs?: RunSummary[]
  aggregate?: Aggregate
}

type TimelineRow = LogRow & {
  bossTurns: number
  window: number
  form: number
  trials: { trialId: number; before: number | null; after: number | null; counterBefore: number; counterAfter: number; started: boolean; completed: boolean }[]
}

type RunDetail = {
  index: number
  status: string
  reason?: string | null
  stuck?: StuckDetail | null
  seed: number
  actors: (ActorInfo & { dead: boolean; healthPct: number })[]
  timeline: TimelineRow[]
  bossSkills?: Record<string, BossSkill>
}

const FORM_ORDER = ['Ram', 'Lion', 'Snake', 'Viper']
const RUN_CHOICES = [1, 5, 10, 20, 100]

function windowLabel(lang: Lang, window: number) {
  const first = window * 5 + 1
  // Boss turns 1-5 are Ultimate; afterwards Ram, Ultimate, Lion, Ultimate, Viper, Ultimate repeat.
  const forms = [0, 1, 0, 2, 0, 3]
  return `${formName(lang, forms[window % 6])} ${first}–${first + 4}`
}

export { trialShortLabel }

function difficultyText(lang: Lang, difficulty?: number) {
  return bossDifficultyText(lang, 'chimera', difficulty)
}

type Finding = { tone: 'bad' | 'warn' | 'good'; text: ReactNode }

function HeroList({ typeIds }: { typeIds: number[] }) {
  return <span className="inline-heroes">{typeIds.map((typeId, index) => <HeroIcon key={`${typeId}-${index}`} typeId={typeId} size="xs" />)}</span>
}

// Findings a player can act on, strongest first.
function useFindings(aggregate: Aggregate): Finding[] {
  const { lang, t } = useSim()
  const en = lang === 'en'
  const lines: Finding[] = []
  const finished = Math.max(aggregate.finishedRuns, 1)
  const stuckGroups = new Map<string, StuckRun[]>()
  for (const run of aggregate.stuckRuns ?? []) {
    const key = `${run.activeHeroTypeId}:${run.form}:${run.reason}`
    stuckGroups.set(key, [...(stuckGroups.get(key) ?? []), run])
  }
  for (const runs of stuckGroups.values()) {
    const first = runs[0]
    const turns = runs.map((run) => run.bossTurns ?? '?').join(en ? ', ' : '、')
    const form = formNameByKey(lang, first.form)
    lines.push({ tone: 'bad', text: <>{t(`${runs.length}/${finished} 场模拟中断：`, `${runs.length}/${finished} runs stopped: `)}<HeroList typeIds={[first.activeHeroTypeId ?? 0]} />{en
      ? `${form ? ` (${form} form)` : ''} ${stuckReasonText(lang, first.reason)} (Boss turn ${turns}). A live takeover would stall there; open the run for the champion's skills and every rule checked.`
      : `${form ? `（${form}形态）` : ''}${stuckReasonText(lang, first.reason)}（Boss 第 ${turns} 回合）。实战接管会在这里卡住；打开该场可查看当时的技能状态和逐条规则检查。`}</> })
  }
  for (const trial of aggregate.trials.filter((item) => item.mandatory)) {
    if (trial.completedRuns >= finished) continue
    lines.push({ tone: 'bad', text: <>{t('必要试炼 ', 'Mandatory trial ')}<TrialTag id={trial.trialId} />{en
      ? ` was completed in only ${trial.completedRuns}/${finished} runs; median best progress ${percent(trial.bestRatioMedian)}.`
      : ` 只在 ${trial.completedRuns}/${finished} 场完成；最佳进度中位 ${percent(trial.bestRatioMedian)}。`}</> })
  }
  for (const event of aggregate.regroupEvents) {
    const turn = event.bossTurnMedian ?? '?'
    lines.push({ tone: 'bad', text: <>{en
      ? `${event.runs}/${finished} runs could no longer complete the mandatory trials around Boss turn ${turn} (a live takeover would free-regroup there)`
      : `${event.runs}/${finished} 场在 Boss 第 ${turn} 回合前后已无法完成必要试炼（实战会在此时免费重整）`}
      {event.trialIds.length > 0 && <>{t('：', ': ')}{event.trialIds.map((id) => <TrialTag key={id} id={id} />)}</>}{t('。', '.')}</> })
  }
  for (const death of aggregate.deaths) {
    const turn = death.firstBossTurnMedian ?? '?'
    lines.push({ tone: 'warn', text: <><HeroList typeIds={[death.heroTypeId]} />{en
      ? ` died in ${death.runs}/${finished} runs (median Boss turn ${turn}).`
      : ` 在 ${death.runs}/${finished} 场阵亡（中位 Boss 第 ${turn} 回合）。`}</> })
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

export function jobText(lang: Lang, job: SimulationJob) {
  const en = lang === 'en'
  const done = `${job.finishedRuns ?? 0}/${job.runs}`
  switch (job.phase ?? job.status) {
    case 'preparing': return en ? 'Preparing the offline engine' : '准备离线引擎'
    case 'running': return (en ? `Simulating · ${done} runs done` : `正在模拟 · 已完成 ${done} 场`)
      + (job.currentBossTurn ? (en ? ` · Boss turn ${job.currentBossTurn}` : ` · Boss 第 ${job.currentBossTurn} 回合`) : '')
      + (job.currentTurn ? (en ? ` · turn ${job.currentTurn}` : ` · 第 ${job.currentTurn} 回合`) : '')
    case 'complete': return en ? 'Simulation complete' : '模拟完成'
    case 'cancelled': return en ? `Stopped (${done} runs done)` : `已停止（完成 ${done} 场）`
    case 'failed': return (en ? 'Simulation failed: ' : '模拟无法进行：') + toolText(lang, job.reason ?? job.message ?? '')
    default: return toolText(lang, job.message ?? '')
  }
}

export function ChimeraSimulationPanel({ language, overview, heroes, effects, trialById, request, draft, strategyId, strategyName, strategyTeam, pid, onOpenReport, onSummary }: {
  language: Lang
  overview?: SimulationOverview
  heroes: SimulationHero[]
  effects: SimEffect[]
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
  const [teamSource, setTeamSource] = useState<TeamSource>('battle')
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
  const teamOptions = teamSourceOptions(language, overview?.teamSources, capture?.teamHeroTypeIds ?? [], strategyTeam)
  const teamReady = teamOptions.find((option) => option.source === teamSource)?.ready ?? false
  const swapped = teamSource !== 'battle'
  const teamDiffers = !swapped && capture && strategyTeam.length > 0 && !capture.teamHeroTypeIds.every((typeId) => strategyTeam.includes(typeId))
  const start = async () => {
    setError('')
    try {
      await request('/api/chimera-simulation/start', { method: 'POST', body: JSON.stringify({ config: draft, strategyId, strategyName, captureId, runs, pid, teamSource }) })
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
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} heroes={heroes} effects={effects} trialById={trialById}>
      <CollapsiblePanel id="chimera:simulation" title={t('策略模拟', 'Strategy Simulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!captures.length ? <p className="muted">{t(
            '还没有奇美拉开局数据。从开局接管打一场奇美拉后，就能用那场战斗的队伍、装备和奇美拉模拟当前规则。',
            'No Chimera battle has been captured yet. Take over one Chimera battle from its start; its team, gear and Chimera can then be used to simulate your current rules.')}</p> : <>
            <p className="muted">{t(
              '在离线原版引擎里重打一场已保存的奇美拉：第 1 场用原战斗的随机种子（完全复现那场战斗），其余场次每次模拟都抽新的随机种子，检验规则是否稳定。使用的是编辑器里当前的规则（包括未保存的修改）。',
              "Replays a saved Chimera battle in an offline copy of the game's own engine. Run 1 uses the original battle seed (an exact replay of that battle); the other runs draw fresh random seeds every time to test how stable the rules are. Uses the rules currently in the editor, including unsaved changes.")}</p>
            <p className="muted">{t(
              '模拟严格按规则出手，不会让游戏自动战斗代打：某个英雄轮到出手却没有可执行的规则（实战接管会卡住）时，那一场就在此中断并记录原因。',
              "Runs follow the rules strictly and never let the game's auto battle play a turn: when a champion has to act and no rule gives a usable action (a live takeover would stall there), that run stops and records why.")}</p>
            <div className="field team-source-field"><span>{t('队伍来源', 'Team')}</span>
              <TeamSourcePicker options={teamOptions} value={teamSource} onChange={setTeamSource} disabled={running} /></div>
            {swapped && <p className="muted">{t(
              '把策略组的队伍放进下面选的开局：Boss、关卡和随机种子不变，英雄和装备换成所选队伍的。出手完全按策略规则，不使用游戏里设定的技能顺序。',
              "Puts the strategy's team into the opening chosen below: boss, stage and random seed stay, the champions and gear become the chosen team's. Every action follows the strategy's rules; the in-game skill order is not used.")}</p>}
            <div className="simulation-controls">
              <div className="field"><span>{swapped ? t('开局（Boss、关卡和随机种子）', 'Opening (boss, stage and seed)') : t('战斗数据（候选队伍）', 'Battle data (candidate teams)')}</span>
                <CapturePicker captures={captures} value={captureId} onChange={setCaptureId} disabled={running} difficultyText={(difficulty) => difficultyText(language, difficulty)} /></div>
              <label className="field"><span>{t('模拟场数', 'Runs')}</span><select value={runs} onChange={(event) => setRuns(Number(event.target.value))} disabled={running}>
                {RUN_CHOICES.map((value) => <option key={value} value={value}>{value === 1 ? (swapped ? t('1 场（只用原战斗种子）', '1 (original seed only)') : t('1 场（只复现原战斗）', '1 (original battle only)')) : t(`${value} 场`, `${value} runs`)}</option>)}
              </select></label>
              {running
                ? <button className="button ghost" onClick={stop}><CircleStop size={16} />{t('停止', 'Stop')}</button>
                : <button className="button primary" onClick={() => void start()} disabled={!captureId || !teamReady}><CirclePlay size={16} />{t('模拟当前规则', 'Simulate current rules')}</button>}
            </div>
            {swapped && <p className="team-source-check">{teamCheckText(language, overview?.teamSources?.check)}</p>}
            {teamDiffers && <p className="simulation-warning"><AlertTriangle size={14} />{t(
              '这场战斗的队伍和当前策略组不同；模拟按战斗里的英雄出手，没有规则的英雄一轮到出手，那一场模拟就会中断。',
              "This battle's team differs from the strategy's team. The simulation uses the battle's champions; a run stops as soon as a champion without rules has to act.")}</p>}
            {error && <p className="simulation-warning"><AlertTriangle size={14} />{error}</p>}
            {job && job.status !== 'complete' && <div className="simulation-progress">
              <div className="bar"><span style={{ width: `${Math.round(((job.finishedRuns ?? 0) / Math.max(job.runs, 1)) * 100)}%` }} /></div>
              <span>{jobText(language, job)}</span>
            </div>}
            {job?.status === 'failed' && <AdviceNote language={language} advice={job.advice} />}
            {aggregate && summary && <SimulationDigest summary={summary} onOpen={() => onOpenReport(summary.id)} />}
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
    </SimProvider>
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

export function BattleForecastPanel({ language, enabled, forecast, history, heroes, effects, trialById, onOpenReport }: {
  language: Lang
  enabled: boolean
  forecast?: BattleForecastTelemetry
  history: SimulationRecent[]
  heroes: SimulationHero[]
  effects: SimEffect[]
  trialById: Map<number, SimulationTrial>
  onOpenReport: (id: string) => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const previous = history.filter((entry) => entry.verdict && entry.id !== forecast?.recordId)
  const latest = forecast ?? (history[0]?.verdict ? { ...history[0].verdict } : undefined)
  const hint = !enabled ? t('已关闭', 'Off') : latest ? battleForecastLabel(language, latest) : t('暂无记录', 'No records yet')
  return (
    <SimProvider lang={language} boss="chimera" heroes={heroes} effects={effects} trialById={trialById}>
      <CollapsiblePanel id="chimera:battle-forecast" title={t('开局整场模拟', 'Opening battle simulation')} hint={hint}>
        <div className="hydra-forecast-body" data-i18n-skip>
          {forecast && <article className={`hydra-forecast-entry ${forecast.status}`}>
            <header><strong>{t('本场', 'This battle')}</strong><span>{battleForecastLabel(language, forecast)}{forecast.finishedAt ? ` · ${forecast.finishedAt}` : ''}</span></header>
            {forecast.conclusion && <p><TextWithTrials text={forecast.conclusion} /></p>}
            <AdviceNote language={language} advice={forecast.advice} />
            {forecast.stuck && <p className="bad stuck-inline"><HeroIcon typeId={forecast.stuck.activeHeroTypeId} size="xs" />{t(
              `模拟在 Boss 第 ${forecast.stuck.bossTurns ?? '?'} 回合中断：${stuckReasonText(language, forecast.stuck.reason)}`,
              `The simulation stopped on Boss turn ${forecast.stuck.bossTurns ?? '?'}: ${stuckReasonText(language, forecast.stuck.reason)}`)}</p>}
            {forecast.recordId && <button className="button ghost" onClick={() => onOpenReport(forecast.recordId!)}><ListOrdered size={15} />{t('查看这场的模拟报告', 'Open this battle\'s simulation report')}</button>}
          </article>}
          {previous.slice(0, 5).map((entry) => (
            <button key={entry.id} className="simulation-history-row" title={adviceTitle(language, entry.verdict?.advice)} onClick={() => onOpenReport(entry.id)}>
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
    </SimProvider>
  )
}

function deathCounts(deaths: { heroTypeId: number }[]) {
  const counts = new Map<number, number>()
  for (const death of deaths) counts.set(death.heroTypeId, (counts.get(death.heroTypeId) ?? 0) + 1)
  return [...counts.entries()]
}

function SimulationDigest({ summary, onOpen }: { summary: SimulationSummary; onOpen: () => void }) {
  const { t } = useSim()
  const aggregate = summary.aggregate!
  const mandatory = aggregate.trials.filter((trial) => trial.mandatory)
  const others = aggregate.trials.filter((trial) => !trial.mandatory && trial.completedRuns > 0)
  return (
    <div className="simulation-digest">
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('未命名策略', 'Unnamed strategy')}</strong><span>{summary.createdAt} · {t(`${aggregate.finishedRuns} 场`, `${aggregate.finishedRuns} runs`)}</span></div>
      <div className="simulation-trials">
        {mandatory.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-rate ${trial.completedRuns === aggregate.finishedRuns ? 'good' : trial.completedRuns ? 'warn' : 'bad'}`}>
          {' '}<b>{trial.completedRuns}/{trial.runs}</b>{trial.completedRuns < trial.runs && <em>{t('最佳', 'best')} {percent(trial.bestRatioMedian)}</em>}<em>{t('必要', 'mandatory')}</em>
        </TrialTag>)}
        {others.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className="trial-rate neutral">{' '}<b>{trial.completedRuns}/{trial.runs}</b></TrialTag>)}
      </div>
      <div className="simulation-facts">
        <span>{t('伤害中位', 'Median damage')} {damageText(aggregate.damage.median || aggregate.bossDamage.median)}</span>
        {aggregate.deaths.length > 0 && <span className="inline-heroes">{t('阵亡：', 'Deaths: ')}{aggregate.deaths.map((death) => <span key={death.heroTypeId}><HeroIcon typeId={death.heroTypeId} size="xs" />{t(`${death.runs}场`, `${death.runs}`)}</span>)}</span>}
        {(aggregate.stuckRuns?.length ?? 0) > 0 && <span className="bad">{t(`规则卡住中断 ${aggregate.stuckRuns!.length} 场`, `${aggregate.stuckRuns!.length} runs stopped by the rules`)}</span>}
        {(aggregate.autoTurnsPerRun ?? 0) > 0 && <span>{t('无规则出手', 'Actions without a rule')} {aggregate.autoTurnsPerRun}{t('/场', '/run')}</span>}
      </div>
      <button className="button ghost" onClick={onOpen}><ListOrdered size={15} />{t('查看完整报告', 'Open full report')}</button>
    </div>
  )
}

type Tab = 'overview' | 'trials' | 'rules' | 'log'

export function SimulationReport({ language, simulationId, onClose, heroes, effects, trialById, request, onJumpToRule }: {
  language: Lang
  simulationId: string | null
  onClose: () => void
  heroes: SimulationHero[]
  effects: SimEffect[]
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
    ? t(`${summary.createdAt} · ${difficultyText(language, summary.capture.difficulty)} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} 场完成模拟（${summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? '第 1 场用原战斗的随机种子' : '第 1 场为原战斗复现'}）`,
        `${summary.createdAt} · ${difficultyText(language, summary.capture.difficulty)} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} runs simulated (${summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? 'run 1 uses the original battle seed' : 'run 1 replays the original battle'})`)
    : t('读取中…', 'Loading…')
  const tabs: [Tab, string][] = [['overview', t('总览', 'Overview')], ['trials', t('试炼', 'Trials')], ['rules', t('规则', 'Rules')], ['log', t('出手记录', 'Action log')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} heroes={heroes} effects={effects} trialById={trialById}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading">
              <span><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('开局整场模拟报告', 'Opening battle simulation report') : t('策略模拟报告', 'Strategy simulation report')} · {summary?.strategy.name || '…'}</Dialog.Title>
                <Dialog.Description>{description}</Dialog.Description></span>
              {summary?.capture.teamSource && summary.capture.teamSource !== 'battle' && <em className="tag team-source-tag" title={summary.capture.teamSavedAt ?? ''}>{teamSourceLabel(language, summary.capture.teamSource)}</em>}
              {summary?.capture.teamHeroTypeIds && <HeroList typeIds={summary.capture.teamHeroTypeIds} />}
              <SimulationTeamButton team={summary?.team} />
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
              {aggregate && summary && tab === 'overview' && <OverviewTab summary={summary} onRun={(index) => { setRunIndex(index); setTab('log') }} onJump={jump} />}
              {aggregate && tab === 'trials' && <TrialsTab aggregate={aggregate} />}
              {aggregate && tab === 'rules' && <>
                <p className="muted">{t(
                  '按策略中的顺序列出每条规则在模拟里的使用情况。“试炼贡献”是这条规则的出手平均每场推进了该试炼多少进度。',
                  'Every rule in strategy order with how it was used in the simulations. "Trial contribution" is how much progress the rule\'s actions added to each trial per run on average.')}</p>
                <RulesTable rules={aggregate.rules} finishedRuns={aggregate.finishedRuns} withTrials onJump={jump} /></>}
              {summary && tab === 'log' && <LogTab summary={summary} runIndex={runIndex} setRunIndex={setRunIndex} request={request} onJump={jump} />}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </SimProvider>
  )
}

function OverviewTab({ summary, onRun, onJump }: { summary: SimulationSummary; onRun: (index: number) => void; onJump: (index: number) => void }) {
  const { lang, t, swapped } = useSim()
  const aggregate = summary.aggregate!
  const lines = useFindings(aggregate)
  return <>
    <div className="simulation-metrics">
      <div><small>{t('必要试炼全部完成', 'All mandatory trials completed')}</small><strong>{t(`${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} 场`, `${aggregate.allMandatoryRuns}/${aggregate.finishedRuns} runs`)}</strong></div>
      <div><small>{t('伤害（中位 · 最低–最高）', 'Damage (median · min–max)')}</small><strong>{damageText(aggregate.damage.median || aggregate.bossDamage.median)}</strong><span>{damageText(aggregate.damage.min || aggregate.bossDamage.min)} – {damageText(aggregate.damage.max || aggregate.bossDamage.max)}</span></div>
      {aggregate.minimumDamage > 0 && <div><small>{t('最低伤害目标', 'Minimum damage goal')}</small><strong>{damageText(aggregate.minimumDamage)}</strong></div>}
      <div><small>{t('规则卡住中断', 'Stopped by the rules')}</small><strong className={aggregate.stuckRuns?.length ? 'bad' : ''}>{t(`${aggregate.stuckRuns?.length ?? 0}/${aggregate.finishedRuns} 场`, `${aggregate.stuckRuns?.length ?? 0}/${aggregate.finishedRuns} runs`)}</strong></div>
    </div>
    {summary.verdict?.conclusion && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}><TextWithTrials text={summary.verdict.conclusion} /></p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t(` Boss 第 ${run.stuck!.bossTurns ?? '?'} 回合`, `Boss turn ${run.stuck!.bossTurns ?? '?'}`)}
      context={formNameByKey(lang, run.stuck!.form) ? t(`（奇美拉${formNameByKey(lang, run.stuck!.form)}形态）`, ` (Chimera ${formNameByKey(lang, run.stuck!.form)} form)`) : ''}
      onJump={onJump} onLog={() => onRun(run.index)} />)}
    <table className="simulation-table">
      <thead><tr><th>{t('场次', 'Run')}</th><th>{t('随机种子', 'Seed')}</th><th>{t('结果', 'Result')}</th><th>{t('必要试炼', 'Mandatory trials')}</th><th>{t('完成试炼', 'Trials completed')}</th><th>{t('伤害', 'Damage')}</th><th>{t('阵亡', 'Deaths')}</th><th>{t('Boss 回合', 'Boss turns')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => (
          <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t(`中断 · Boss 第 ${run.stuck.bossTurns ?? '?'} 回合`, `Stopped · Boss turn ${run.stuck.bossTurns ?? '?'}`)}</em>
              : run.status === 'complete' ? t('打完', 'Finished') : toolText(lang, run.reason ?? run.status)}</td>
            <td>{run.mandatory.map((item) => <HoverCard key={item.trialId} content={<>
              <TrialCard id={item.trialId} />
              <p className="hover-note">{item.completed ? t(`第 ${item.completedBossTurn} 回合完成`, `Completed on Boss turn ${item.completedBossTurn}`) : t(`未完成 · 最佳 ${percent(item.bestRatio)}`, `Not completed · best ${percent(item.bestRatio)}`)}</p>
            </>}><span className={`dot ${item.completed ? 'good' : 'bad'}`} /></HoverCard>)}</td>
            <td>{Object.keys(run.completedTrials).length ? <HoverCard content={<div className="hover-trial-list">{Object.keys(run.completedTrials).map((id) => <span key={id}>{trialShortLabel(lang, undefined, Number(id))}</span>)}</div>}>
              <span className="count-chip">{Object.keys(run.completedTrials).length}</span></HoverCard> : 0}</td>
            <td>{damageText(run.damage || run.bossDamageTaken)}</td>
            <td>{run.deaths.length ? <span className="inline-heroes">{deathCounts(run.deaths).map(([typeId, count]) => <span key={typeId}><HeroIcon typeId={typeId} size="xs" />{count > 1 ? `×${count}` : ''}</span>)}</span> : '—'}</td>
            <td>{run.bossTurns ?? '—'}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('出手记录', 'Action log')}</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  </>
}

function TrialsTab({ aggregate }: { aggregate: Aggregate }) {
  const { lang, trialById, t } = useSim()
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
              <td><TrialTag id={trial.trialId} className="strong" />{trial.mandatory && <em className="tag">{t('必要', 'Mandatory')}</em>}<small>{plainText(info?.description)}</small></td>
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

function LogTab({ summary, runIndex, setRunIndex, request, onJump }: { summary: SimulationSummary; runIndex: number; setRunIndex: (index: number) => void; request: Request; onJump: (index: number) => void }) {
  const { lang, t } = useSim()
  const [detail, setDetail] = useState<RunDetail | null>(null)
  useEffect(() => {
    setDetail(null)
    request<RunDetail>(`/api/chimera-simulation?id=${encodeURIComponent(summary.id)}&run=${runIndex}`).then(setDetail).catch(() => undefined)
  }, [summary.id, runIndex, request])
  const filters = useMemo(() => [
    { key: 'trials', label: t('只看推进试炼的出手', 'Only actions that advanced a trial'), test: (row: TimelineRow) => row.trials.some((trial) => (trial.after ?? 0) > (trial.before ?? 0) || trial.completed) },
    { key: 'chain', label: t('只看有组队攻击、反击等连带行动的出手', 'Only actions with ally attacks, counterattacks and the like'), test: (row: TimelineRow) => hasChainedSkill(row.uses) },
    { key: 'reserved', label: t('只看动用保留技能的出手', 'Only actions that used a reserved skill'), test: (row: TimelineRow) => row.reservationReleased === true || row.source === 'auto' },
  ], [t])
  const runs = (summary.runs ?? []).map((run) => run.index).sort((a, b) => a - b)
  return <ActionLog<TimelineRow>
    rows={detail?.timeline ?? null}
    bossSkills={detail?.bossSkills}
    actors={detail?.actors ?? []}
    runs={runs}
    runIndex={runIndex}
    setRunIndex={setRunIndex}
    groupOf={(row) => row.bossTurns}
    groupTitle={(bossTurns, rows) => {
      const damage = rows.reduce((total, row) => total + (row.source !== 'enemy' ? row.damage : 0), 0)
      return <><strong>{t(`Boss 第 ${bossTurns} 回合后`, `After Boss turn ${bossTurns}`)}</strong><span>{t(
        `${formName(lang, rows[0].form)}形态 · ${rows.length} 次行动 · 伤害 ${damageText(damage)}`,
        `${formName(lang, rows[0].form)} form · ${rows.length} actions · damage ${damageText(damage)}`)}</span></>
    }}
    openByDefault={(bossTurns, rows) => bossTurns >= 6 && rows.some((row) => row.trials.length > 0)}
    filters={filters}
    enemyToggle={t('显示奇美拉的行动', "Show the Chimera's actions")}
    extras={(row) => row.trials.filter((trial) => trial.completed || (trial.after ?? 0) !== (trial.before ?? 0)).map((trial) => (
      <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-progress ${trial.completed ? 'good' : ''}`}> {percent(trial.before)}→{percent(trial.after)}{trial.completed ? ' ✓' : ''}</TrialTag>
    ))}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t(` Boss 第 ${detail.stuck.bossTurns ?? '?'} 回合`, `Boss turn ${detail.stuck.bossTurns ?? '?'}`)} onJump={onJump} /> : undefined}
  />
}

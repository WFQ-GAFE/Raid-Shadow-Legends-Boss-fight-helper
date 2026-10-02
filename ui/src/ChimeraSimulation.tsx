import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { translate, useI18n } from './i18n'
import * as Dialog from '@radix-ui/react-dialog'
import { CollapsiblePanel } from './CollapsiblePanel'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { HoverCard } from './HoverCard'
import {
  ActionLog, AdviceNote, CapturePicker, HeroIcon, RulesTable, SimProvider, SimulationTeamButton, StuckCard, TeamSourcePicker, TextWithTrials,
  TrialCard, TrialTag,
  adviceTitle,
  bossDifficultyText, damageText, hasChainedSkill, formName, formNameByKey, originalRunLabel, percent, plainText, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel,
  toolText, trialShortLabel, useSim, useSimulationInputs,
  type ActorInfo, type BossSkill, type Lang, type LogRow, type RuleAggregate, type SimEffect, type SimulationHero, type SimulationTrial, type StuckDetail,
  type FailureAdvice, type SimulationTeam, type TeamSources,
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

export type SimulationOverview = { strategyId?: string; captures: SimulationCapture[]; job?: SimulationJob | null; recent: SimulationRecent[]; battleForecasts?: SimulationRecent[]; teamSources?: TeamSources }

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
  const lines: Finding[] = []
  const finished = Math.max(aggregate.finishedRuns, 1)
  const stuckGroups = new Map<string, StuckRun[]>()
  for (const run of aggregate.stuckRuns ?? []) {
    const key = `${run.activeHeroTypeId}:${run.form}:${run.reason}`
    stuckGroups.set(key, [...(stuckGroups.get(key) ?? []), run])
  }
  for (const runs of stuckGroups.values()) {
    const first = runs[0]
    const turns = runs.map((run) => run.bossTurns ?? '?').join(translate(lang, 'common.listSeparator'))
    const form = formNameByKey(lang, first.form)
    lines.push({ tone: 'bad', text: <>{t('chimeraSim.runsStopped', { runsCount: runs.length, finished })}<HeroList typeIds={[first.activeHeroTypeId ?? 0]} />{translate(lang, 'chimeraSim.bossTurnALiveTakeover', { form: form || 'none', reason: stuckReasonText(lang, first.reason), turns })}</> })
  }
  for (const trial of aggregate.trials.filter((item) => item.mandatory)) {
    if (trial.completedRuns >= finished) continue
    lines.push({ tone: 'bad', text: <>{t('chimeraSim.mandatoryTrial')}<TrialTag id={trial.trialId} />{translate(lang, 'chimeraSim.wasCompletedInOnlyRuns', { completedRuns: trial.completedRuns, finished, bestRatioMedian: percent(trial.bestRatioMedian) })}</> })
  }
  for (const event of aggregate.regroupEvents) {
    const turn = event.bossTurnMedian ?? '?'
    lines.push({ tone: 'bad', text: <>{translate(lang, 'chimeraSim.runsCouldNoLongerComplete', { runs: event.runs, finished, turn })}
      {event.trialIds.length > 0 && <>{t('chimeraSim.text')}{event.trialIds.map((id) => <TrialTag key={id} id={id} />)}</>}{t('chimeraSim.text2')}</> })
  }
  for (const death of aggregate.deaths) {
    const turn = death.firstBossTurnMedian ?? '?'
    lines.push({ tone: 'warn', text: <><HeroList typeIds={[death.heroTypeId]} />{translate(lang, 'chimeraSim.diedInRunsMedianBoss', { runs: death.runs, finished, turn })}</> })
  }
  const unused = aggregate.rules.filter((rule) => rule.ruleIndex !== null && rule.uses === 0)
  if (unused.length) {
    const names = unused.slice(0, 5).map((rule) => `#${rule.ruleIndex} ${ruleLabel(lang, rule.rule)}`).join(translate(lang, 'common.listSeparator'))
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.rulesWereNeverUsedIn', { unusedCount: unused.length, names, value: unused.length > 5 ? ' …' : '' }) })
  }
  if ((aggregate.reservationReleasesPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.onAverageActionsPerRun', { reservationReleasesPerRun: aggregate.reservationReleasesPerRun }) })
  }
  if ((aggregate.autoTurnsPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.onAverageActionsPerRun2', { autoTurnsPerRun: aggregate.autoTurnsPerRun }) })
  }
  if (!lines.length) lines.push({ tone: 'good', text: translate(lang, 'chimeraSim.everyMandatoryTrialWasCompleted') })
  return lines
}

export function ruleUsageByIndex(summary: SimulationSummary | null | undefined) {
  const usage = new Map<number, RuleAggregate>()
  for (const rule of summary?.aggregate?.rules ?? []) if (rule.ruleIndex !== null) usage.set(rule.ruleIndex, rule)
  return usage
}

export function jobText(lang: Lang, job: SimulationJob) {
  const done = `${job.finishedRuns ?? 0}/${job.runs}`
  switch (job.phase ?? job.status) {
    case 'preparing': return translate(lang, 'chimeraSim.preparingTheOfflineEngine')
    case 'running': return translate(lang, 'chimeraSim.simulatingRunsDone', { done })
      + (job.currentBossTurn ? translate(lang, 'chimeraSim.bossTurn2', { currentBossTurn: job.currentBossTurn }) : '')
      + (job.currentTurn ? translate(lang, 'chimeraSim.turn', { currentTurn: job.currentTurn }) : '')
    case 'complete': return translate(lang, 'chimeraSim.simulationComplete')
    case 'cancelled': return translate(lang, 'chimeraSim.stoppedRunsDone', { done })
    case 'failed': return translate(lang, 'chimeraSim.simulationFailed2') + toolText(lang, job.reason ?? job.message ?? '')
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
  const { t } = useI18n()
  const job = overview?.job ?? null
  const { captures, sources, capture, captureId, setCaptureId, teamSource, setTeamSource, teamOptions, teamReady } = useSimulationInputs(
    language, strategyId, overview?.strategyId && overview.strategyId !== strategyId ? [] : overview?.captures ?? [], overview?.teamSources, strategyTeam)
  const [runs, setRuns] = useState(10)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<SimulationSummary | null>(null)
  const running = job?.status === 'running'
  const latestId = job && job.status !== 'running' ? job.id : overview?.recent?.[0]?.id
  useEffect(() => {
    if (!latestId || summary?.id === latestId) return
    let cancelled = false
    request<SimulationSummary>(`/api/chimera-simulation?id=${encodeURIComponent(latestId)}`)
      .then((value) => { if (!cancelled) { setSummary(value); onSummary(value) } })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [latestId, summary?.id, request, onSummary])
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
    ? t('chimeraSim.running', { finishedRuns: job?.finishedRuns ?? 0, runs: job?.runs ?? 0 })
    : aggregate
      ? t('chimeraSim.allMandatoryTrialsInRuns', { allMandatoryRuns: aggregate.allMandatoryRuns, finishedRuns: aggregate.finishedRuns })
      : captures.length ? t('chimeraSim.ready') : t('chimeraSim.needsAChimeraBattleCapture')
  return (
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} heroes={heroes} effects={effects} trialById={trialById}>
      <CollapsiblePanel id="chimera:simulation" title={t('chimeraSim.strategySimulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!captures.length ? <p className="muted">{t('chimeraSim.noChimeraBattleHasBeen')}</p> : <>
            <p className="muted">{t(capture?.id.startsWith('strategy-package:') && teamSource === 'author' && sources?.author?.offlineReady
              ? 'sim.replaysSavedStrategyPackage' : 'chimeraSim.replaysASavedChimeraBattle')}</p>
            <p className="muted">{t('chimeraSim.runsFollowTheRulesStrictly')}</p>
            <div className="field team-source-field"><span>{t('chimeraSim.team')}</span>
              <TeamSourcePicker options={teamOptions} value={teamSource} onChange={setTeamSource} disabled={running} /></div>
            {swapped && <p className="muted">{t('chimeraSim.putsTheStrategySTeam')}</p>}
            <div className="simulation-controls">
              <div className="field"><span>{swapped ? t('chimeraSim.openingBossStageAndSeed') : t('chimeraSim.battleDataCandidateTeams')}</span>
                <CapturePicker captures={captures} value={captureId} onChange={setCaptureId} disabled={running} difficultyText={(difficulty) => difficultyText(language, difficulty)} /></div>
              <label className="field"><span>{t('chimeraSim.runs')}</span><select value={runs} onChange={(event) => setRuns(Number(event.target.value))} disabled={running}>
                {RUN_CHOICES.map((value) => <option key={value} value={value}>{value === 1 ? (swapped ? t('chimeraSim.originalSeedOnly') : t('chimeraSim.originalBattleOnly')) : t('chimeraSim.runs2', { value })}</option>)}
              </select></label>
              {running
                ? <button className="button ghost" onClick={stop}><CircleStop size={16} />{t('chimeraSim.stop')}</button>
                : <button className="button primary" onClick={() => void start()} disabled={!captureId || !teamReady}><CirclePlay size={16} />{t('chimeraSim.simulateCurrentRules')}</button>}
            </div>
            {swapped && <p className="team-source-check">{teamCheckText(language, sources?.check)}</p>}
            {teamDiffers && <p className="simulation-warning"><AlertTriangle size={14} />{t('chimeraSim.thisBattleSTeamDiffers')}</p>}
            {error && <p className="simulation-warning"><AlertTriangle size={14} />{error}</p>}
            {job && job.status !== 'complete' && <div className="simulation-progress">
              <div className="bar"><span style={{ width: `${Math.round(((job.finishedRuns ?? 0) / Math.max(job.runs, 1)) * 100)}%` }} /></div>
              <span>{jobText(language, job)}</span>
            </div>}
            {job?.status === 'failed' && <AdviceNote language={language} advice={job.advice} />}
            {aggregate && summary && <SimulationDigest summary={summary} onOpen={() => onOpenReport(summary.id)} />}
            {(overview?.recent?.length ?? 0) > 1 && <div className="simulation-history">
              <small>{t('chimeraSim.recentSimulations')}</small>
              {overview!.recent.slice(0, 6).map((item) => (
                <button key={item.id} className="simulation-history-row" onClick={() => onOpenReport(item.id)}>
                  <span>{item.createdAt?.slice(5, 16)}</span><strong>{item.strategyName || t('chimeraSim.unnamedStrategy')}</strong>
                  <em>{item.status === 'complete'
                    ? t('chimeraSim.allMandatory', { allMandatoryRuns: item.allMandatoryRuns ?? 0, finishedRuns: item.finishedRuns ?? 0 })
                      + (item.stuckRuns ? t('chimeraSim.stopped', { stuckRuns: item.stuckRuns }) : '')
                    : item.status === 'failed' ? t('chimeraSim.failed') : item.status === 'cancelled' ? t('chimeraSim.stopped2') : item.status}</em>
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
  switch (forecast.status) {
    case 'waiting_capture': return translate(lang, 'chimeraSim.waitingForTheOpeningData')
    case 'running': return translate(lang, 'chimeraSim.simulatingInTheBackgroundThe')
    case 'not_opening': return translate(lang, 'chimeraSim.notTakenOverFromThe')
    case 'unavailable': return translate(lang, 'chimeraSim.undeterminedNoRegroupBasedOn')
    default: return forecast.verdict === 'retry'
      ? translate(lang, 'chimeraSim.goalsPredictedToFailFree')
      : translate(lang, 'chimeraSim.goalsPredictedToBeMet')
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
  const { t } = useI18n()
  const previous = history.filter((entry) => entry.verdict && entry.id !== forecast?.recordId)
  const latest = forecast ?? (history[0]?.verdict ? { ...history[0].verdict } : undefined)
  const hint = !enabled ? t('chimeraSim.off') : latest ? battleForecastLabel(language, latest) : t('chimeraSim.noRecordsYet')
  return (
    <SimProvider lang={language} boss="chimera" heroes={heroes} effects={effects} trialById={trialById}>
      <CollapsiblePanel id="chimera:battle-forecast" title={t('chimeraSim.openingBattleSimulation')} hint={hint}>
        <div className="hydra-forecast-body" data-i18n-skip>
          {forecast && <article className={`hydra-forecast-entry ${forecast.status}`}>
            <header><strong>{t('chimeraSim.thisBattle')}</strong><span>{battleForecastLabel(language, forecast)}{forecast.finishedAt ? ` · ${forecast.finishedAt}` : ''}</span></header>
            {forecast.conclusion && <p><TextWithTrials text={forecast.conclusion} /></p>}
            <AdviceNote language={language} advice={forecast.advice} />
            {forecast.stuck && <p className="bad stuck-inline"><HeroIcon typeId={forecast.stuck.activeHeroTypeId} size="xs" />{t('chimeraSim.theSimulationStoppedOnBoss', { bossTurns: forecast.stuck.bossTurns ?? '?', reason: stuckReasonText(language, forecast.stuck.reason) })}</p>}
            {forecast.recordId && <button className="button ghost" onClick={() => onOpenReport(forecast.recordId!)}><ListOrdered size={15} />{t('chimeraSim.openThisBattleSSimulation')}</button>}
          </article>}
          {previous.slice(0, 5).map((entry) => (
            <button key={entry.id} className="simulation-history-row" title={adviceTitle(language, entry.verdict?.advice)} onClick={() => onOpenReport(entry.id)}>
              <span>{entry.createdAt?.slice(5, 16)}</span><strong>{entry.strategyName || t('chimeraSim.unnamedStrategy')}</strong>
              <em className={entry.verdict?.verdict === 'retry' ? 'bad' : ''}>{battleForecastLabel(language, entry.verdict!)}</em>
            </button>
          ))}
          {!forecast && !previous.length && <p className="hydra-forecast-empty">{enabled
            ? t('chimeraSim.whenAChimeraTakeoverStarts')
            : t('chimeraSim.theOpeningBattleSimulationIs')}</p>}
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
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('chimeraSim.unnamedStrategy')}</strong><span>{summary.createdAt} · {t('chimeraSim.runs3', { finishedRuns: aggregate.finishedRuns })}</span></div>
      <div className="simulation-trials">
        {mandatory.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-rate ${trial.completedRuns === aggregate.finishedRuns ? 'good' : trial.completedRuns ? 'warn' : 'bad'}`}>
          {' '}<b>{trial.completedRuns}/{trial.runs}</b>{trial.completedRuns < trial.runs && <em>{t('chimeraSim.best')} {percent(trial.bestRatioMedian)}</em>}<em>{t('chimeraSim.mandatory')}</em>
        </TrialTag>)}
        {others.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className="trial-rate neutral">{' '}<b>{trial.completedRuns}/{trial.runs}</b></TrialTag>)}
      </div>
      <div className="simulation-facts">
        <span>{t('chimeraSim.medianDamage')} {damageText(aggregate.damage.median || aggregate.bossDamage.median)}</span>
        {aggregate.deaths.length > 0 && <span className="inline-heroes">{t('chimeraSim.deaths')}{aggregate.deaths.map((death) => <span key={death.heroTypeId}><HeroIcon typeId={death.heroTypeId} size="xs" />{t('chimeraSim.text3', { runs: death.runs })}</span>)}</span>}
        {(aggregate.stuckRuns?.length ?? 0) > 0 && <span className="bad">{t('chimeraSim.runsStoppedByTheRules', { stuckRunsCount: aggregate.stuckRuns!.length })}</span>}
        {(aggregate.autoTurnsPerRun ?? 0) > 0 && <span>{t('chimeraSim.actionsWithoutARule')} {aggregate.autoTurnsPerRun}{t('chimeraSim.run')}</span>}
      </div>
      <button className="button ghost" onClick={onOpen}><ListOrdered size={15} />{t('chimeraSim.openFullReport')}</button>
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
  const { t } = useI18n()
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
    ? t('chimeraSim.runsSimulated', { createdAt: summary.createdAt, difficulty: difficultyText(language, summary.capture.difficulty), finishedRuns: aggregate?.finishedRuns ?? 0, runs: aggregate?.runs ?? 0, seed: summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? 'original' : 'replay' })
    : t('sim.loading')
  const tabs: [Tab, string][] = [['overview', t('chimeraSim.overview')], ['trials', t('chimeraSim.trials')], ['rules', t('chimeraSim.rules')], ['log', t('sim.actionLog')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} heroes={heroes} effects={effects} trialById={trialById}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading">
              <span><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('chimeraSim.openingBattleSimulationReport') : t('chimeraSim.strategySimulationReport')} · {summary?.strategy.name || '…'}</Dialog.Title>
                <Dialog.Description>{description}</Dialog.Description></span>
              {summary?.capture.teamSource && summary.capture.teamSource !== 'battle' && <em className="tag team-source-tag" title={summary.capture.teamSavedAt ?? ''}>{teamSourceLabel(language, summary.capture.teamSource)}</em>}
              {summary?.capture.teamHeroTypeIds && <HeroList typeIds={summary.capture.teamHeroTypeIds} />}
              <SimulationTeamButton team={summary?.team} />
              <Dialog.Close className="icon-button" aria-label={t('chimeraSim.closeSimulationReport')}><X size={19} /></Dialog.Close>
            </div>
            <div className="simulation-tabs" role="tablist">
              {tabs.map(([key, label]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
              ))}
            </div>
            <div className="simulation-report-body">
              {error && <p className="simulation-warning"><AlertTriangle size={14} />{error}</p>}
              {summary?.status === 'failed' && <p className="simulation-warning"><AlertTriangle size={14} />{t('chimeraSim.simulationFailed')}{toolText(language, summary.reason ?? '')}</p>}
              {aggregate && summary && tab === 'overview' && <OverviewTab summary={summary} onRun={(index) => { setRunIndex(index); setTab('log') }} onJump={jump} />}
              {aggregate && tab === 'trials' && <TrialsTab aggregate={aggregate} />}
              {aggregate && tab === 'rules' && <>
                <p className="muted">{t('chimeraSim.everyRuleInStrategyOrder')}</p>
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
      <div><small>{t('chimeraSim.allMandatoryTrialsCompleted')}</small><strong>{t('chimeraSim.runs4', { allMandatoryRuns: aggregate.allMandatoryRuns, finishedRuns: aggregate.finishedRuns })}</strong></div>
      <div><small>{t('chimeraSim.damageMedianMinMax')}</small><strong>{damageText(aggregate.damage.median || aggregate.bossDamage.median)}</strong><span>{damageText(aggregate.damage.min || aggregate.bossDamage.min)} – {damageText(aggregate.damage.max || aggregate.bossDamage.max)}</span></div>
      {aggregate.minimumDamage > 0 && <div><small>{t('chimeraSim.minimumDamageGoal')}</small><strong>{damageText(aggregate.minimumDamage)}</strong></div>}
      <div><small>{t('chimeraSim.stoppedByTheRules')}</small><strong className={aggregate.stuckRuns?.length ? 'bad' : ''}>{t('chimeraSim.runs5', { stuckRunsCount: aggregate.stuckRuns?.length ?? 0, finishedRuns: aggregate.finishedRuns })}</strong></div>
    </div>
    {summary.verdict?.conclusion && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}><TextWithTrials text={summary.verdict.conclusion} /></p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t('chimeraSim.bossTurn', { bossTurns: run.stuck!.bossTurns ?? '?' })}
      context={formNameByKey(lang, run.stuck!.form) ? t('chimeraSim.chimeraForm', { formNameByKey: formNameByKey(lang, run.stuck!.form) }) : ''}
      onJump={onJump} onLog={() => onRun(run.index)} />)}
    <table className="simulation-table">
      <thead><tr><th>{t('chimeraSim.run2')}</th><th>{t('chimeraSim.seed')}</th><th>{t('chimeraSim.result')}</th><th>{t('chimeraSim.mandatoryTrials')}</th><th>{t('chimeraSim.trialsCompleted')}</th><th>{t('chimeraSim.damage')}</th><th>{t('chimeraSim.deaths2')}</th><th>{t('chimeraSim.bossTurns')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => (
          <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t('chimeraSim.stoppedBossTurn', { bossTurns: run.stuck.bossTurns ?? '?' })}</em>
              : run.status === 'complete' ? t('chimeraSim.finished') : toolText(lang, run.reason ?? run.status)}</td>
            <td>{run.mandatory.map((item) => <HoverCard key={item.trialId} content={<>
              <TrialCard id={item.trialId} />
              <p className="hover-note">{item.completed ? t('chimeraSim.completedOnBossTurn', { completedBossTurn: item.completedBossTurn }) : t('chimeraSim.notCompletedBest', { bestRatio: percent(item.bestRatio) })}</p>
            </>}><span className={`dot ${item.completed ? 'good' : 'bad'}`} /></HoverCard>)}</td>
            <td>{Object.keys(run.completedTrials).length ? <HoverCard content={<div className="hover-trial-list">{Object.keys(run.completedTrials).map((id) => <span key={id}>{trialShortLabel(lang, undefined, Number(id))}</span>)}</div>}>
              <span className="count-chip">{Object.keys(run.completedTrials).length}</span></HoverCard> : 0}</td>
            <td>{damageText(run.damage || run.bossDamageTaken)}</td>
            <td>{run.deaths.length ? <span className="inline-heroes">{deathCounts(run.deaths).map(([typeId, count]) => <span key={typeId}><HeroIcon typeId={typeId} size="xs" />{count > 1 ? `×${count}` : ''}</span>)}</span> : '—'}</td>
            <td>{run.bossTurns ?? '—'}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('sim.actionLog')}</button></td>
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
    <label className="simulation-toggle"><input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />{t('chimeraSim.showTrialsWithoutProgress')}</label>
    <div className="simulation-scroll">
      <table className="simulation-table trials">
        <thead><tr><th>{t('chimeraSim.trial')}</th><th>{t('chimeraSim.completed')}</th><th>{t('chimeraSim.completionTurnMedian')}</th><th>{t('chimeraSim.bestProgressMedian')}</th>{windows.map((window) => <th key={window}>{windowLabel(lang, window)}</th>)}</tr></thead>
        <tbody>
          {visible.slice().sort((a, b) => byForm(a) - byForm(b) || a.trialId - b.trialId).map((trial) => {
            const info = trialById.get(trial.trialId)
            return <tr key={trial.trialId} className={trial.mandatory ? 'mandatory' : ''}>
              <td><TrialTag id={trial.trialId} className="strong" />{trial.mandatory && <em className="tag">{t('chimeraSim.mandatory2')}</em>}<small>{plainText(info?.description)}</small></td>
              <td className={trial.completedRuns === trial.runs ? 'good' : trial.completedRuns ? 'warn' : 'bad'}>{trial.completedRuns}/{trial.runs}</td>
              <td>{trial.completedBossTurnMedian ?? '—'}</td>
              <td>{percent(trial.bestRatioMedian)}</td>
              {windows.map((window) => {
                const cell = trial.windows[String(window)]
                return <td key={window}>{cell ? <span className="cell-bar" title={`${t('chimeraSim.max')} ${percent(cell.max)}`}><span style={{ width: `${Math.min(100, (cell.median ?? 0) * 100)}%` }} /><em>{percent(cell.median)}</em></span> : ''}</td>
              })}
            </tr>
          })}
        </tbody>
      </table>
    </div>
    <p className="muted">{t('chimeraSim.eachColumnIsAFive')}</p>
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
    { key: 'trials', label: t('chimeraSim.onlyActionsThatAdvancedA'), test: (row: TimelineRow) => row.trials.some((trial) => (trial.after ?? 0) > (trial.before ?? 0) || trial.completed) },
    { key: 'chain', label: t('chimeraSim.onlyActionsWithAllyAttacks'), test: (row: TimelineRow) => hasChainedSkill(row.uses) },
    { key: 'reserved', label: t('chimeraSim.onlyActionsThatUsedA'), test: (row: TimelineRow) => row.reservationReleased === true || row.source === 'auto' },
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
      return <><strong>{t('chimeraSim.afterBossTurn', { bossTurns })}</strong><span>{t('chimeraSim.formActionsDamage', { form: formName(lang, rows[0].form), rowsCount: rows.length, damage: damageText(damage) })}</span></>
    }}
    openByDefault={(bossTurns, rows) => bossTurns >= 6 && rows.some((row) => row.trials.length > 0)}
    filters={filters}
    enemyToggle={t('chimeraSim.showTheChimeraSActions')}
    extras={(row) => row.trials.filter((trial) => trial.completed || (trial.after ?? 0) !== (trial.before ?? 0)).map((trial) => (
      <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-progress ${trial.completed ? 'good' : ''}`}> {percent(trial.before)}→{percent(trial.after)}{trial.completed ? ' ✓' : ''}</TrialTag>
    ))}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t('chimeraSim.bossTurn', { bossTurns: detail.stuck.bossTurns ?? '?' })} onJump={onJump} /> : undefined}
  />
}

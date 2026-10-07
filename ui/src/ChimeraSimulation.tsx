import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { translate, useI18n } from './i18n'
import * as Dialog from '@radix-ui/react-dialog'
import { CollapsiblePanel } from './CollapsiblePanel'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { HoverCard } from './HoverCard'
import {
  ActionLog, AdviceNote, ChimeraIcon, HeroIcon, RulesTable, SimProvider, SimulationBase, SimulationReadFeedback, SimulationSampleNote, SimulationTeamButton, StuckCard, TeamSourcePicker, TextWithTrials,
  FocusPicker, TrialButton, TrialCard, TrialTag, trialIdentity, trialTurnScope,
  adviceTitle,
  bossDifficultyText, damageText, hasChainedSkill, formName, formNameByKey, originalRunLabel, percent, plainText, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel,
  targetMissFindings, toolText, trialShortLabel, simulationHealthInput, simulationInputSignature, simulationResultParametersChanged, simulationSamples, useRunDifficulty, useSim, useSimulationInputs, useSimulationResource,
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
  // The alliance Chimera's HP already lost when the battle started (it carries over).
  bossHealthLost?: number
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

// difficultyHealth: each difficulty's full HP (game stage data), for an opening's starting HP in percent.
export type SimulationOverview = { strategyId?: string; captures: SimulationCapture[]; job?: SimulationJob | null; recent: SimulationRecent[]; battleForecasts?: SimulationRecent[]; teamSources?: TeamSources
  difficultyHealth?: Record<number, number> }

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
  // Whether any run made progress (only this decides which trials the table shows).
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
  // openingDifficulty: the saved opening's own, when the boss came from the game's stage data
  // (another difficulty or starting HP); bossHealthPercent: the Chimera's HP at the start then.
  capture: { id: string; stageId?: number; seed?: number; teamHeroTypeIds?: number[]; bossHeroTypeId?: number; difficulty?: number; teamSource?: string; teamSavedAt?: string
    openingStageId?: number; openingDifficulty?: number; bossHealthPercent?: number }
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
// Easy to Ultra-Nightmare: an opening runs on any of them (tools/boss_stages.py).
const DIFFICULTIES = [1, 2, 3, 4, 5, 6]

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
function useFindings(aggregate: Aggregate, runs?: RunSummary[]): Finding[] {
  const { lang, t } = useSim()
  const lines: Finding[] = []
  const finished = aggregate.finishedRuns
  if (!finished) return [{ tone: 'warn', text: t('sim.noCalculations') }]
  if (!simulationSamples(aggregate, runs).hasValid) lines.push({ tone: 'warn', text: t('sim.noValidSamples') })
  if (!aggregate.mandatoryTrialIds.length) lines.push({ tone: 'warn', text: t(aggregate.minimumDamage > 0 ? 'sim.noTrialGoals' : 'sim.noGoals') })
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
    lines.push({ tone: 'bad', text: <>{t('chimeraSim.mandatoryTrial')}<TrialTag id={trial.trialId} />{translate(lang, 'chimeraSim.wasCompletedInOnlyRuns', { completedRuns: trial.completedRuns, finished })}</> })
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
  lines.push(...targetMissFindings(lang, aggregate.rules))
  if ((aggregate.reservationReleasesPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.onAverageActionsPerRun', { reservationReleasesPerRun: aggregate.reservationReleasesPerRun }) })
  }
  if ((aggregate.autoTurnsPerRun ?? 0) > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.onAverageActionsPerRun2', { autoTurnsPerRun: aggregate.autoTurnsPerRun }) })
  }
  // Strongest first: problems before warnings (the order within each is kept).
  lines.sort((left, right) => Number(right.tone === 'bad') - Number(left.tone === 'bad'))
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
    case 'complete': return translate(lang, 'sim.calculationsFinished')
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
  const { sources, capture, captureId, teamSource, setTeamSource, teamOptions, teamReady } = useSimulationInputs(
    language, strategyId, overview?.strategyId && overview.strategyId !== strategyId ? [] : overview?.captures ?? [], overview?.teamSources, strategyTeam)
  const [runs, setRuns] = useState(10)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [stopSent, setStopSent] = useState(false)
  const [origins, setOrigins] = useState<Record<string, string>>({})
  const actionPending = useRef(false)
  const running = job?.status === 'running'
  const latestId = job && job.status !== 'running' ? job.id : overview?.recent?.[0]?.id
  const resource = useSimulationResource<SimulationSummary>(request, latestId ? `/api/chimera-simulation?id=${encodeURIComponent(latestId)}` : null)
  const summary = resource.data
  // The base battle's difficulty and HP play as saved; another difficulty or HP is built from the game's data.
  const baseDifficulty = capture?.difficulty
  const [chosenDifficulty, setDifficulty] = useRunDifficulty('chimera', baseDifficulty, DIFFICULTIES)
  const [health, setHealth] = useState<string | null>(null)
  useEffect(() => setHealth(null), [captureId])
  const otherDifficulty = baseDifficulty !== undefined && chosenDifficulty !== undefined && chosenDifficulty !== baseDifficulty
  const fullHealth = baseDifficulty !== undefined ? overview?.difficultyHealth?.[baseDifficulty] : undefined
  const baseHealth = capture?.bossHealthLost !== undefined && fullHealth ? Math.max(0, 100 * (1 - capture.bossHealthLost / fullHealth)) : undefined
  const healthInput = simulationHealthInput(health, otherDifficulty ? 100 : baseHealth)
  const healthInvalid = healthInput.invalid
  const shownHealth = health ?? (otherDifficulty ? '100' : baseHealth !== undefined ? String(Math.round(baseHealth * 10) / 10) : '')
  const inputSignature = simulationInputSignature({ config: draft, strategyId, captureId, runs, teamSource, difficulty: chosenDifficulty,
    bossHealth: healthInput.value ?? healthInput.defaultValue,
    teamSavedAt: teamSource === 'author' ? sources?.author?.savedAt : teamSource === 'strategy' ? sources?.strategy?.savedAt : undefined })
  const inputsChanged = Boolean(summary && (origins[summary.id] ? origins[summary.id] !== inputSignature
    : simulationResultParametersChanged(summary.capture, { captureId, difficulty: chosenDifficulty, teamSource,
      bossHealthPercent: healthInput.value ?? healthInput.defaultValue,
      teamSavedAt: teamSource === 'author' ? sources?.author?.savedAt : teamSource === 'strategy' ? sources?.strategy?.savedAt : undefined })))
  useEffect(() => { onSummary(inputsChanged ? null : summary) }, [summary, inputsChanged, onSummary])
  useEffect(() => { setStopSent(false) }, [job?.id, running])
  const start = async () => {
    if (actionPending.current || healthInvalid) return
    actionPending.current = true
    setStarting(true)
    setError('')
    try {
      const result = await request<{ job?: SimulationJob }>('/api/chimera-simulation/start', { method: 'POST', body: JSON.stringify({
        config: draft, strategyId, strategyName, captureId, runs, pid, teamSource,
        ...(otherDifficulty ? { difficulty: chosenDifficulty } : {}), ...(healthInput.value !== undefined ? { bossHealth: healthInput.value } : {}) }) })
      if (result.job?.id) setOrigins((current) => ({ ...current, [result.job!.id]: inputSignature }))
    } catch (reason) {
      setError(toolText(language, reason instanceof Error ? reason.message : String(reason)))
    } finally {
      setStarting(false)
      actionPending.current = false
    }
  }
  const stop = async () => {
    if (actionPending.current || stopSent) return
    actionPending.current = true
    setStopping(true)
    setError('')
    try {
      await request('/api/chimera-simulation/stop', { method: 'POST', body: '{}' })
      setStopSent(true)
    } catch (reason) {
      setError(t('sim.stopFailed', { reason: toolText(language, reason instanceof Error ? reason.message : String(reason)) }))
    } finally { setStopping(false); actionPending.current = false }
  }
  const aggregate = summary?.aggregate
  const blocked = running || starting
  const hint = running
    ? t('chimeraSim.running', { finishedRuns: job?.finishedRuns ?? 0, runs: job?.runs ?? 0 })
    : aggregate
      ? inputsChanged ? t('sim.inputsChanged') : t('sim.previousResultHint', { value: simulationSamples(aggregate, summary?.runs).hasValid
        ? aggregate.mandatoryTrialIds.length ? t('chimeraSim.allMandatoryTrialsInRuns', { allMandatoryRuns: aggregate.allMandatoryRuns, finishedRuns: aggregate.finishedRuns }) : t('sim.noTrialGoals')
        : t('sim.noValidSamples') })
      : capture ? t('chimeraSim.ready') : t('chimeraSim.needsAChimeraBattleCapture')
  return (
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} rebuilt={summary?.capture.openingDifficulty !== undefined}
      heroes={heroes} effects={effects} trialById={trialById}>
      <CollapsiblePanel id="chimera:simulation" title={t('chimeraSim.strategySimulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!capture ? <p className="muted">{t('chimeraSim.noChimeraBattleHasBeen')}</p> : <>
            <p className="muted">{t('chimeraSim.simulatesOnDifficulty')}</p>
            <p className="muted">{t('chimeraSim.runsFollowTheRulesStrictly')}</p>
            <div className="field team-source-field"><span>{t('chimeraSim.team')}</span>
              <TeamSourcePicker options={teamOptions} value={teamSource} onChange={setTeamSource} disabled={blocked} /></div>
            <p className="team-source-check">{teamCheckText(language, sources?.check)}</p>
            <SimulationBase capture={capture} difficultyText={(difficulty) => difficultyText(language, difficulty)}>
              {baseHealth !== undefined && <span>{t('chimeraSim.bossHealthTag', { percent: percent(baseHealth / 100, baseHealth % 1 ? 1 : 0) })}</span>}
            </SimulationBase>
            <div className="simulation-controls">
              <label className="field"><span>{t('sim.runDifficulty')}</span>
                <select value={chosenDifficulty} onChange={(event) => setDifficulty(Number(event.target.value))} disabled={blocked}>
                  {DIFFICULTIES.map((value) => <option key={value} value={value}>{difficultyText(language, value)}{value === baseDifficulty ? t('sim.openingsDifficulty') : ''}</option>)}
                </select></label>
              <div className="field" title={t('chimeraSim.bossHealthHint', { opening: baseHealth !== undefined ? percent(baseHealth / 100, 1) : 'unknown' })}>
                {/* The reset sits on the label line, so the input lines up with the selects beside it. */}
                <span className="field-heading"><label htmlFor="simulation-boss-health">{t('chimeraSim.bossHealth')}</label>
                  {health !== null && <button type="button" className="link-button" disabled={blocked} onClick={() => setHealth(null)}>{t('sim.restoreHealth')}</button>}</span>
                <span className="percent-input"><input id="simulation-boss-health" type="number" min={0.1} max={100} step={0.1} value={shownHealth} disabled={blocked}
                  aria-invalid={healthInvalid} aria-describedby="simulation-health-help" onChange={(event) => setHealth(event.target.value)} /><em>%</em></span></div>
              <label className="field"><span>{t('chimeraSim.runs')}</span><select value={runs} onChange={(event) => setRuns(Number(event.target.value))} disabled={blocked}>
                {RUN_CHOICES.map((value) => <option key={value} value={value}>{value === 1 ? t('chimeraSim.originalSeedOnly') : t('chimeraSim.runs2', { value })}</option>)}
              </select></label>
              {running
                ? <button className="button ghost" onClick={() => void stop()} disabled={stopping || stopSent}><CircleStop size={16} />{stopping ? t('sim.stopping') : t('chimeraSim.stop')}</button>
                : <button className="button primary" onClick={() => void start()} disabled={starting || !captureId || !teamReady || healthInvalid}><CirclePlay size={16} />{starting ? t('sim.starting') : t('chimeraSim.simulateCurrentRules')}</button>}
            </div>
            <p id="simulation-health-help" className={healthInvalid ? 'simulation-warning' : 'muted'} role={healthInvalid ? 'alert' : undefined}>{healthInvalid
              ? t('sim.healthRangeError') : healthInput.defaultValue !== undefined ? t('sim.healthDefault', { value: Math.round(healthInput.defaultValue * 10) / 10 }) : t('sim.healthDefaultOpening')}</p>
            {stopSent && running && <p className="muted simulation-request-status" role="status">{t('sim.stopSent')}</p>}
            {otherDifficulty && <p className="muted">{t('chimeraSim.otherDifficultyHint', { difficulty: difficultyText(language, chosenDifficulty) })}</p>}
            {error && <p className="simulation-warning" role="alert"><AlertTriangle size={14} />{error}</p>}
            {job && job.status !== 'complete' && <div className="simulation-progress">
              <div className="bar"><span style={{ width: `${Math.round(((job.finishedRuns ?? 0) / Math.max(job.runs, 1)) * 100)}%` }} /></div>
              <span>{jobText(language, job)}</span>
            </div>}
            {job?.status === 'failed' && <AdviceNote language={language} advice={job.advice} />}
            <SimulationReadFeedback resource={resource} />
            {summary && <div className="simulation-result-source"><strong>{t('sim.lastSimulation')}</strong><span>{t('sim.resultInputs', { difficulty: difficultyText(language, summary.capture.difficulty), source: teamSourceLabel(language, summary.capture.teamSource), runs: aggregate?.runs ?? summary.runs?.length ?? 0, capture: summary.capture.id })}</span>
              {summary.capture.bossHealthPercent !== undefined && <span>{t('chimeraSim.bossHealthTag', { percent: percent(summary.capture.bossHealthPercent / 100, 1) })}</span>}
              {inputsChanged && <p className="simulation-warning" role="status">{t('sim.inputsChanged')}</p>}</div>}
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
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('chimeraSim.unnamedStrategy')}</strong><span>{summary.createdAt}</span></div>
      <SimulationSampleNote aggregate={aggregate} runs={summary.runs} />
      <div className="simulation-trials">
        {mandatory.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-rate ${aggregate.finishedRuns > 0 && trial.completedRuns === aggregate.finishedRuns ? 'good' : trial.completedRuns ? 'warn' : 'bad'}`}>
          {' '}<b>{trial.completedRuns}/{trial.runs}</b><em>{t('chimeraSim.mandatory')}</em>
        </TrialTag>)}
        {others.map((trial) => <TrialTag key={trial.trialId} id={trial.trialId} className="trial-rate neutral">{' '}<b>{trial.completedRuns}/{trial.runs}</b></TrialTag>)}
      </div>
      <div className="simulation-facts">
        <span>{t('chimeraSim.medianDamage')} {simulationSamples(aggregate, summary.runs).hasValid ? damageText(aggregate.damage.median || aggregate.bossDamage.median) : '—'}</span>
        {!aggregate.mandatoryTrialIds.length && <span>{t('sim.noTrialGoals')}</span>}
        {aggregate.minimumDamage <= 0 && <span>{t('sim.goalNotSet')}</span>}
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
  const resource = useSimulationResource<SimulationSummary>(request, simulationId ? `/api/chimera-simulation?id=${encodeURIComponent(simulationId)}` : null)
  const summary = resource.data
  const [tab, setTab] = useState<Tab>('overview')
  const [runIndex, setRunIndex] = useState(1)
  useEffect(() => {
    setTab('overview')
    setRunIndex(1)
  }, [simulationId])
  const aggregate = summary?.aggregate
  const rebuilt = summary?.capture.openingDifficulty !== undefined
  const otherDifficulty = rebuilt && summary?.capture.openingDifficulty !== summary?.capture.difficulty
  const description = summary
    ? t('sim.reportSummary', { createdAt: summary.createdAt, difficulty: difficultyText(language, summary.capture.difficulty), calculated: aggregate?.finishedRuns ?? 0, total: aggregate?.runs ?? 0 })
    : t('sim.loading')
  const tabs: [Tab, string][] = [['overview', t('chimeraSim.overview')], ['trials', t('chimeraSim.trials')], ['rules', t('chimeraSim.rules')], ['log', t('sim.actionLog')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="chimera" teamSource={summary?.capture.teamSource} rebuilt={rebuilt} heroes={heroes} effects={effects} trialById={trialById}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading simulation-report-heading">
              <div className="simulation-report-title"><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('chimeraSim.openingBattleSimulationReport') : t('chimeraSim.strategySimulationReport')} · {summary?.strategy.name || '…'}</Dialog.Title>
                <Dialog.Close className="icon-button" aria-label={t('chimeraSim.closeSimulationReport')}><X size={19} /></Dialog.Close></div>
              <div className="simulation-report-meta"><Dialog.Description>{description}</Dialog.Description>
              {otherDifficulty && <em className="tag team-source-tag" title={t('sim.rebuiltHint')}>
                {t('sim.rebuiltFrom', { opening: difficultyText(language, summary?.capture.openingDifficulty) })}</em>}
              {rebuilt && summary?.capture.bossHealthPercent !== undefined && <em className="tag team-source-tag">
                {t('chimeraSim.bossHealthTag', { percent: percent(summary.capture.bossHealthPercent / 100, Number.isInteger(summary.capture.bossHealthPercent) ? 0 : 1) })}</em>}
              {summary?.capture.teamSource && summary.capture.teamSource !== 'battle' && <em className="tag team-source-tag" title={summary.capture.teamSavedAt ?? ''}>{teamSourceLabel(language, summary.capture.teamSource)}</em>}
              {summary?.capture.teamHeroTypeIds && <HeroList typeIds={summary.capture.teamHeroTypeIds} />}
              <SimulationTeamButton team={summary?.team} />
              </div>
            </div>
            <div className="simulation-tabs" role="tablist">
              {tabs.map(([key, label]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
              ))}
            </div>
            <div className="simulation-report-body">
              <SimulationReadFeedback resource={resource} />
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
  const lines = useFindings(aggregate, summary.runs)
  const samples = simulationSamples(aggregate, summary.runs)
  const hasGoals = aggregate.mandatoryTrialIds.length > 0 || aggregate.minimumDamage > 0
  return <>
    <SimulationSampleNote aggregate={aggregate} runs={summary.runs} />
    <div className="simulation-metrics">
      <div><small>{t('chimeraSim.allMandatoryTrialsCompleted')}</small><strong>{aggregate.mandatoryTrialIds.length ? t('chimeraSim.runs4', { allMandatoryRuns: aggregate.allMandatoryRuns, finishedRuns: aggregate.finishedRuns }) : '—'}</strong>{!aggregate.mandatoryTrialIds.length && <span>{t('sim.noTrialGoals')}</span>}</div>
      <div><small>{t('chimeraSim.damageMedianMinMax')}</small><strong>{samples.hasValid ? damageText(aggregate.damage.median || aggregate.bossDamage.median) : '—'}</strong><span>{samples.hasValid ? `${damageText(aggregate.damage.min || aggregate.bossDamage.min)} – ${damageText(aggregate.damage.max || aggregate.bossDamage.max)}` : '—'}</span></div>
      <div><small>{t('chimeraSim.minimumDamageGoal')}</small><strong>{aggregate.minimumDamage > 0 ? damageText(aggregate.minimumDamage) : '—'}</strong>{aggregate.minimumDamage <= 0 && <span>{t('sim.goalNotSet')}</span>}</div>
      <div><small>{t('chimeraSim.stoppedByTheRules')}</small><strong className={aggregate.stuckRuns?.length ? 'bad' : ''}>{t('chimeraSim.runs5', { stuckRunsCount: aggregate.stuckRuns?.length ?? 0, finishedRuns: aggregate.finishedRuns })}</strong></div>
    </div>
    {summary.verdict?.conclusion && (summary.verdict.verdict === 'retry' || samples.hasValid && hasGoals) && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}><TextWithTrials text={summary.verdict.conclusion} /></p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t('chimeraSim.bossTurn', { bossTurns: run.stuck!.bossTurns ?? '?' })}
      context={formNameByKey(lang, run.stuck!.form) ? t('chimeraSim.chimeraForm', { formNameByKey: formNameByKey(lang, run.stuck!.form) }) : ''}
      onJump={onJump} onLog={() => onRun(run.index)} />)}
    <div className="simulation-scroll" tabIndex={0} aria-label={t('chimeraSim.overview')}><table className="simulation-table">
      <thead><tr><th>{t('chimeraSim.run2')}</th><th>{t('chimeraSim.seed')}</th><th>{t('chimeraSim.result')}</th><th>{t('chimeraSim.mandatoryTrials')}</th><th>{t('chimeraSim.trialsCompleted')}</th><th>{t('chimeraSim.damage')}</th><th>{t('chimeraSim.deaths2')}</th><th>{t('chimeraSim.bossTurns')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => (
          <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t('chimeraSim.stoppedBossTurn', { bossTurns: run.stuck.bossTurns ?? '?' })}</em>
              : run.status === 'complete' ? t('chimeraSim.finished') : run.status === 'partial' ? t('sim.partialCalculation') : toolText(lang, run.reason ?? run.status)}</td>
            <td>{run.mandatory.map((item) => <HoverCard key={item.trialId} content={<>
              <TrialCard id={item.trialId} />
              <p className="hover-note">{item.completed ? t('chimeraSim.completedOnBossTurn', { completedBossTurn: item.completedBossTurn })
                : item.bestRatio > 0 ? t('chimeraSim.notCompletedBest', { bestRatio: percent(item.bestRatio) }) : t('chimeraSim.notCompleted')}</p>
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
    </table></div>
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
    <div className="simulation-scroll" tabIndex={0} aria-label={t('chimeraSim.trials')}>
      <table className="simulation-table trials">
        <thead><tr><th>{t('chimeraSim.trial')}</th><th>{t('chimeraSim.completed')}</th><th>{t('chimeraSim.completionTurnMedian')}</th>{windows.map((window) => <th key={window}>{windowLabel(lang, window)}</th>)}</tr></thead>
        <tbody>
          {visible.slice().sort((a, b) => byForm(a) - byForm(b) || a.trialId - b.trialId).map((trial) => {
            const info = trialById.get(trial.trialId)
            return <tr key={trial.trialId} className={trial.mandatory ? 'mandatory' : ''}>
              <td><TrialTag id={trial.trialId} className="strong" />{trial.mandatory && <em className="tag">{t('chimeraSim.mandatory2')}</em>}<small>{plainText(info?.description)}</small></td>
              <td className={trial.completedRuns === trial.runs ? 'good' : trial.completedRuns ? 'warn' : 'bad'}>{trial.completedRuns}/{trial.runs}</td>
              <td>{trial.completedBossTurnMedian ?? '—'}</td>
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
  const { lang, t, trialById } = useSim()
  // A response for another report or run never replaces the one asked for last.
  const resource = useSimulationResource<RunDetail>(request, `/api/chimera-simulation?id=${encodeURIComponent(summary.id)}&run=${runIndex}`)
  const detail = resource.data
  // A trial picked in the run's trial list: only the turns of its form, until it was completed.
  // Kept when another run is shown, so runs compare on the same trial.
  const [focus, setFocus] = useState<number | null>(null)
  // A Chimera form to look at (kept across runs too).
  const [form, setForm] = useState<number | null>(null)
  const run = summary.runs?.find((item) => item.index === runIndex)
  const focusForm = focus === null ? undefined : trialIdentity(trialById.get(focus), focus).form
  const focusDoneAt = focus === null ? undefined : run?.completedTrials[String(focus)]
  const scope = useMemo(() => {
    const trialTurns = focus === null ? null : trialTurnScope(detail?.timeline ?? [], trialById.get(focus), focus, focusDoneAt)
    if (trialTurns === null && form === null) return null
    return (row: TimelineRow) => (trialTurns === null || trialTurns(row)) && (form === null || row.form === form)
  }, [focus, focusDoneAt, trialById, detail, form])
  const forms = [...new Set((detail?.timeline ?? []).map((row) => row.form))].filter((value) => typeof value === 'number').sort()
  const formPicker = <FocusPicker label={t('app.chimeraForm')} value={form} onChange={setForm}
    options={forms.map((value) => ({ key: value, icon: <ChimeraIcon size="xs" form={value} />, name: formName(lang, value) }))} />
  const shown = (trial: TimelineRow['trials'][number]) => focus === null || trial.trialId === focus
  const filters = useMemo(() => [
    { key: 'trials', label: t('chimeraSim.onlyActionsThatAdvancedA'), test: (row: TimelineRow) => row.trials.some((trial) => shown(trial) && ((trial.after ?? 0) > (trial.before ?? 0) || trial.completed)) },
    { key: 'chain', label: t('chimeraSim.onlyActionsWithAllyAttacks'), test: (row: TimelineRow) => hasChainedSkill(row.uses) },
    { key: 'reserved', label: t('chimeraSim.onlyActionsThatUsedA'), test: (row: TimelineRow) => row.reservationReleased === true || row.source === 'auto' },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [t, focus])
  const runs = (summary.runs ?? []).map((run) => run.index).sort((a, b) => a - b)
  const mandatoryIds = new Set(run?.mandatory.map((item) => item.trialId))
  const otherDone = Object.entries(run?.completedTrials ?? {}).filter(([id]) => !mandatoryIds.has(Number(id)))
    .sort(([, left], [, right]) => left - right)
  const pick = (id: number) => setFocus((current) => current === id ? null : id)
  const header = run && (run.mandatory.length > 0 || otherDone.length > 0) && <div className="run-trials">
    <span className="run-trials-label">{t('chimeraSim.runTrials')}</span>
    {run.mandatory.map((item) => <TrialButton key={item.trialId} id={item.trialId} mandatory active={focus === item.trialId}
      tone={item.completed ? 'done' : 'missed'} onClick={() => pick(item.trialId)}
      note={item.completed && item.completedBossTurn !== null
        ? t('chimeraSim.completedOnBossTurn', { completedBossTurn: item.completedBossTurn })
        : t('chimeraSim.notCompletedBest', { bestRatio: percent(item.bestRatio) })} />)}
    {otherDone.map(([id, bossTurns]) => <TrialButton key={id} id={Number(id)} active={focus === Number(id)} tone="done"
      onClick={() => pick(Number(id))} note={t('chimeraSim.completedOnBossTurn', { completedBossTurn: bossTurns })} />)}
    <small>{focus === null ? t('chimeraSim.pickTrialHint')
      : <>{t('chimeraSim.trialTurnsShown', { form: formName(lang, focusForm) })}{typeof focusDoneAt === 'number'
          ? ` · ${t('chimeraSim.trialTurnsUntil', { bossTurns: focusDoneAt })}` : ''}
        <button type="button" className="link-button" onClick={() => setFocus(null)}>{t('chimeraSim.allTurns')}</button></>}</small>
  </div>
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
    openByDefault={(bossTurns, rows) => focus !== null
      ? rows.some((row) => row.trials.some((trial) => trial.trialId === focus))
      : bossTurns >= 6 && rows.some((row) => row.trials.length > 0)}
    filters={filters}
    toolbar={formPicker}
    header={header}
    scope={scope}
    enemyToggle={t('chimeraSim.showTheChimeraSActions')}
    rowWhen={(row) => t('chimeraSim.bossTurn', { bossTurns: row.bossTurns })}
    extras={(row) => row.trials.filter((trial) => shown(trial) && (trial.completed || (trial.after ?? 0) !== (trial.before ?? 0))).map((trial) => (
      <TrialTag key={trial.trialId} id={trial.trialId} className={`trial-progress ${trial.completed ? 'good' : ''}`}> {percent(trial.before)}→{percent(trial.after)}{trial.completed ? ' ✓' : ''}</TrialTag>
    ))}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t('chimeraSim.bossTurn', { bossTurns: detail.stuck.bossTurns ?? '?' })} onJump={onJump} /> : undefined}
    failure={resource.status === 'error' ? <SimulationReadFeedback resource={resource} log /> : undefined}
    stopped={Boolean(detail?.stuck)}
    onResetFilters={() => { setFocus(null); setForm(null) }}
    unavailable={detail && form !== null && !forms.includes(form) ? [formName(lang, form)] : []}
  />
}

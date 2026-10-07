import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { translate, useI18n } from './i18n'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { CollapsiblePanel } from './CollapsiblePanel'
import { HoverCard } from './HoverCard'
import { jobText, type SimulationJob } from './ChimeraSimulation'
import {
  ActionLog, AdviceNote, FocusPicker, HeadIcon, HeroIcon, RulesTable, SimProvider, SimulationBase, SimulationReadFeedback, SimulationSampleNote, SimulationTeamButton, StuckCard, TeamSourcePicker,
  bossDifficultyText, damageText, findHead, hasChainedSkill, headName, originalRunLabel, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel, targetMissFindings, toolText, simulationInputSignature, simulationResultParametersChanged, simulationSamples, useRunDifficulty, useSim, useSimulationInputs, useSimulationResource,
  type ActorInfo, type BossSkill, type Lang, type LogRow, type RuleAggregate, type SimEffect, type SimHead, type SimulationHero, type StuckDetail,
  type SimulationTeam, type TeamSources,
} from './SimulationShared'

// Hydra strategy simulation: the rules being edited played in the isolated
// original engine on the chosen difficulty, up to the game's turn limit, built
// on the newest saved Hydra battle. See tools/hydra_simulation*.py.

type Request = <T>(path: string, init?: RequestInit) => Promise<T>

// headTypeIds: the week's heads (the four in play, then the two in reserve).
type HydraCapture = { id: string; capturedAt?: string; stageId?: number; difficulty?: number; seed?: number; teamHeroTypeIds: number[]; headTypeIds?: number[]; strategyName?: string | null }
type HydraRecent = { id: string; createdAt?: string; strategyName?: string; status?: string; runs?: number; finishedRuns?: number; damageMedian?: number | null; regroupRuns?: number; stuckRuns?: number }
export type HydraSimulationOverview = { strategyId?: string; captures: HydraCapture[]; job?: SimulationJob | null; recent: HydraRecent[]; teamSources?: TeamSources }

type StuckRun = { index: number; seed: number; exact: boolean; reason: string; turn?: number; hydraTurns?: number; activeHeroTypeId?: number; rule?: string | null }
type Violation = { conditionIndex: number | null; cause?: string; markIndex?: number; relation?: string; expectedHeroTypeIds?: number[]; actualHeroTypeId?: number; applyTurn?: number; predictedDamage?: number; minimumDamage?: number; swallowed?: boolean }

type Aggregate = {
  runs: number
  finishedRuns: number
  battleEndRuns: number
  minimumDamage: number
  minimumDamageRuns: number | null
  damage: { min: number; median: number | null; max: number }
  marks: { heroTypeId: number; runs: number; marksPerRun: number; firstTurnMedian: number | null }[]
  marksPerRunMedian: number | null
  headKillsPerRunMedian: number | null
  deaths: { heroTypeId: number; runs: number; firstTurnMedian: number | null }[]
  rules: RuleAggregate[]
  stuckRuns: StuckRun[]
  reservationReleasesPerRun: number
  devourConditions: number
  regroupRuns: number
  regroupCauses: { damage: number; devour: number }
}

type RunSummary = {
  index: number
  seed: number
  exact: boolean
  status: string
  reason?: string | null
  turn?: number
  hydraTurns?: number | null
  damage?: number
  stuck?: StuckDetail | null
  // swallowed: whether the head actually swallowed the marked hero (null: no swallow data)
  marks: { markIndex: number; heroTypeId: number; applyTurn: number; swallowed?: boolean | null }[]
  deaths: { actorId: number; heroTypeId: number; turn: number }[]
  headKills: Record<string, number>
  verdict: { verdict: 'retry' | 'continue' | 'unknown'; reason?: string | null; violations?: Violation[] }
}

type Summary = {
  id: string
  // 'battle': a live opening forecast saved as a one-run report (tools/hydra_forecast_live.py).
  kind?: 'battle'
  verdict?: { status?: string; verdict?: 'retry' | 'continue' | 'unknown' | null; conclusion?: string | null }
  createdAt?: string
  status: string
  reason?: string
  strategy: { id?: string; name?: string; rules?: number }
  // openingDifficulty: the saved opening's own, when it ran on another difficulty (boss from the game's stage data).
  capture: { id: string; stageId?: number; seed?: number; teamHeroTypeIds?: number[]; difficulty?: number; teamSource?: string; teamSavedAt?: string; openingStageId?: number; openingDifficulty?: number }
  // The team the simulation ran with (reports from 1.1.1 on).
  team?: SimulationTeam | null
  runs?: RunSummary[]
  aggregate?: Aggregate
}

type HydraRow = LogRow & { hydraTurns: number; marked: number[] }
type RunDetail = { index: number; status: string; reason?: string | null; stuck?: StuckDetail | null; seed: number; actors: (ActorInfo & { dead: boolean })[]; timeline: HydraRow[]; bossSkills?: Record<string, BossSkill> }

const RUN_CHOICES = [1, 5, 10, 20, 100]
// Normal, Hard, Brutal, Nightmare: an opening runs on any of them (tools/boss_stages.py).
const DIFFICULTIES = [1, 2, 3, 4]
// The battle turn counts every action; the log shows it in blocks.
const TURNS_PER_GROUP = 20

function difficultyText(lang: Lang, difficulty?: number) {
  return bossDifficultyText(lang, 'hydra', difficulty)
}

function HeroList({ typeIds }: { typeIds: number[] }) {
  return <span className="inline-heroes">{typeIds.map((typeId, index) => <HeroIcon key={`${typeId}-${index}`} typeId={typeId} size="xs" />)}</span>
}

function violationText(lang: Lang, violation: Violation): ReactNode {
  if (violation.cause === 'damage') return translate(lang, 'hydraSim.damageBelowTheGoal', { predictedDamage: damageText(violation.predictedDamage), minimumDamage: damageText(violation.minimumDamage) })
  const who = <HeroList typeIds={[violation.actualHeroTypeId ?? 0]} />
  if (violation.relation === 'neverMarked' && violation.swallowed) return <>{who}{translate(lang, 'hydraSim.swallowedMarkTurn', { markIndex: violation.markIndex, applyTurn: violation.applyTurn })}</>
  if (violation.relation === 'neverMarked') return <>{translate(lang, 'hydraSim.devourMarkOn')}{who}{translate(lang, 'hydraSim.turn2', { applyTurn: violation.applyTurn })}</>
  return <>{translate(lang, 'hydraSim.markOn', { markIndex: violation.markIndex })}{who}{translate(lang, 'hydraSim.turn2', { applyTurn: violation.applyTurn })}</>
}

type Finding = { tone: 'bad' | 'warn' | 'good'; text: ReactNode }

function useFindings(aggregate: Aggregate, runs?: RunSummary[]): Finding[] {
  const { lang, t } = useSim()
  const finished = aggregate.finishedRuns
  const lines: Finding[] = []
  if (!finished) return [{ tone: 'warn', text: t('sim.noCalculations') }]
  const samples = simulationSamples(aggregate, runs)
  if (!samples.hasValid) lines.push({ tone: 'warn', text: t('sim.noValidSamples') })
  else if (samples.valid < finished) lines.push({ tone: 'warn', text: t('sim.partialCalculation') })
  if (aggregate.minimumDamage <= 0 && !aggregate.devourConditions) lines.push({ tone: 'warn', text: t('sim.noGoals') })
  const groups = new Map<string, StuckRun[]>()
  for (const run of aggregate.stuckRuns) groups.set(`${run.activeHeroTypeId}:${run.reason}`, [...(groups.get(`${run.activeHeroTypeId}:${run.reason}`) ?? []), run])
  for (const runs of groups.values()) {
    const turns = runs.map((run) => run.turn ?? '?').join(translate(lang, 'common.listSeparator'))
    lines.push({ tone: 'bad', text: <>{t('chimeraSim.runsStopped', { runsCount: runs.length, finished })}<HeroList typeIds={[runs[0].activeHeroTypeId ?? 0]} />{translate(lang, 'hydraSim.turnALiveTakeoverWould', { reason: stuckReasonText(lang, runs[0].reason), turns })}</> })
  }
  if (aggregate.regroupRuns > 0) {
    lines.push({ tone: 'bad', text: translate(lang, 'hydraSim.runsWouldMakeTheLive', { regroupRuns: aggregate.regroupRuns, finished, devour: aggregate.regroupCauses.devour, damage: aggregate.regroupCauses.damage }) })
  }
  if (aggregate.minimumDamage > 0 && aggregate.minimumDamageRuns !== null && aggregate.minimumDamageRuns < aggregate.battleEndRuns) {
    lines.push({ tone: 'bad', text: translate(lang, 'hydraSim.onlyRunsReachedTheMinimum', { minimumDamageRuns: aggregate.minimumDamageRuns, battleEndRuns: aggregate.battleEndRuns, minimumDamage: damageText(aggregate.minimumDamage) }) })
  }
  for (const death of aggregate.deaths) {
    lines.push({ tone: 'warn', text: <><HeroList typeIds={[death.heroTypeId]} />{translate(lang, 'hydraSim.diedInRunsMedianTurn', { runs: death.runs, finished, firstTurnMedian: death.firstTurnMedian ?? '?' })}</> })
  }
  const unused = aggregate.rules.filter((rule) => rule.ruleIndex !== null && rule.uses === 0)
  if (unused.length) {
    const names = unused.slice(0, 5).map((rule) => `#${rule.ruleIndex} ${ruleLabel(lang, rule.rule)}`).join(translate(lang, 'common.listSeparator'))
    lines.push({ tone: 'warn', text: translate(lang, 'chimeraSim.rulesWereNeverUsedIn', { unusedCount: unused.length, names, value: unused.length > 5 ? ' …' : '' }) })
  }
  lines.push(...targetMissFindings(lang, aggregate.rules))
  if (aggregate.reservationReleasesPerRun > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'hydraSim.onAverageActionsPerRun', { reservationReleasesPerRun: aggregate.reservationReleasesPerRun }) })
  }
  // Strongest first: problems before warnings (the order within each is kept).
  lines.sort((left, right) => Number(right.tone === 'bad') - Number(left.tone === 'bad'))
  if (!lines.length) lines.push({ tone: 'good', text: translate(lang, 'hydraSim.everyRunFinishedWithoutStopping') })
  return lines
}

export function HydraSimulationPanel({ language, overview, heroes, heads, effects, request, draft, strategyId, strategyName, strategyTeam, pid, onOpenReport }: {
  language: Lang
  overview?: HydraSimulationOverview
  heroes: SimulationHero[]
  heads: SimHead[]
  effects: SimEffect[]
  request: Request
  draft: unknown
  strategyId: string
  strategyName: string
  strategyTeam: number[]
  pid?: number
  onOpenReport: (id: string) => void
}) {
  const { t } = useI18n()
  const job = overview?.job ?? null
  const { sources, capture, captureId, teamSource, setTeamSource, teamOptions, teamReady } = useSimulationInputs(
    language, strategyId, overview?.strategyId && overview.strategyId !== strategyId ? [] : overview?.captures ?? [], overview?.teamSources, strategyTeam)
  const [runs, setRuns] = useState(5)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [stopSent, setStopSent] = useState(false)
  const [origins, setOrigins] = useState<Record<string, string>>({})
  const actionPending = useRef(false)
  const running = job?.status === 'running'
  const latestId = job && job.status !== 'running' ? job.id : overview?.recent?.[0]?.id
  const resource = useSimulationResource<Summary>(request, latestId ? `/api/hydra-simulation?id=${encodeURIComponent(latestId)}` : null)
  const summary = resource.data
  // The base battle's difficulty plays as saved; any other is built from the game's data.
  const baseDifficulty = capture?.difficulty
  const [chosenDifficulty, setDifficulty] = useRunDifficulty('hydra', baseDifficulty, DIFFICULTIES)
  const rebuilt = baseDifficulty !== undefined && chosenDifficulty !== undefined && chosenDifficulty !== baseDifficulty
  const inputSignature = simulationInputSignature({ config: draft, strategyId, captureId, runs, teamSource, difficulty: chosenDifficulty,
    teamSavedAt: teamSource === 'author' ? sources?.author?.savedAt : teamSource === 'strategy' ? sources?.strategy?.savedAt : undefined })
  const inputsChanged = Boolean(summary && (origins[summary.id] ? origins[summary.id] !== inputSignature
    : simulationResultParametersChanged(summary.capture, { captureId, difficulty: chosenDifficulty, teamSource,
      teamSavedAt: teamSource === 'author' ? sources?.author?.savedAt : teamSource === 'strategy' ? sources?.strategy?.savedAt : undefined })))
  useEffect(() => { setStopSent(false) }, [job?.id, running])
  const start = async () => {
    if (actionPending.current) return
    actionPending.current = true
    setStarting(true)
    setError('')
    try {
      const result = await request<{ job?: SimulationJob }>('/api/hydra-simulation/start', { method: 'POST', body: JSON.stringify({
        config: draft, strategyId, strategyName, captureId, runs, pid, teamSource, ...(rebuilt ? { difficulty: chosenDifficulty } : {}) }) })
      if (result.job?.id) setOrigins((current) => ({ ...current, [result.job!.id]: inputSignature }))
    } catch (reason) {
      setError(toolText(language, reason instanceof Error ? reason.message : String(reason)))
    } finally { setStarting(false); actionPending.current = false }
  }
  const stop = async () => {
    if (actionPending.current || stopSent) return
    actionPending.current = true
    setStopping(true)
    setError('')
    try {
      await request('/api/hydra-simulation/stop', { method: 'POST', body: '{}' })
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
        ? t('hydraSim.medianDamage', { median: damageText(aggregate.damage.median) }) : t('sim.noValidSamples') })
      : capture ? t('chimeraSim.ready') : t('hydraSim.needsAHydraBattleCapture')
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} rebuilt={summary?.capture.openingDifficulty !== undefined}
      heroes={heroes} heads={heads} effects={effects}>
      <CollapsiblePanel id="hydra:simulation" title={t('chimeraSim.strategySimulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!capture ? <p className="muted">{t('hydraSim.noHydraBattleHasBeen')}</p> : <>
            <p className="muted">{t('hydraSim.simulatesOnDifficulty')}</p>
            <div className="field team-source-field"><span>{t('chimeraSim.team')}</span>
              <TeamSourcePicker options={teamOptions} value={teamSource} onChange={setTeamSource} disabled={blocked} /></div>
            <p className="team-source-check">{teamCheckText(language, sources?.check)}</p>
            <SimulationBase capture={capture} difficultyText={(difficulty) => difficultyText(language, difficulty)}>
              {capture.headTypeIds?.length ? <span className="simulation-base-icons">{capture.headTypeIds.map((typeId, index) =>
                <span key={`${typeId}-${index}`} className={index >= 4 ? 'reserve' : ''}><HeadIcon typeId={typeId} size="xs" /></span>)}</span> : null}
            </SimulationBase>
            <div className="simulation-controls">
              <label className="field"><span>{t('sim.runDifficulty')}</span>
                <select value={chosenDifficulty} onChange={(event) => setDifficulty(Number(event.target.value))} disabled={blocked}>
                  {DIFFICULTIES.map((value) => <option key={value} value={value}>{difficultyText(language, value)}{value === baseDifficulty ? t('sim.openingsDifficulty') : ''}</option>)}
                </select></label>
              <label className="field"><span>{t('chimeraSim.runs')}</span><select value={runs} onChange={(event) => setRuns(Number(event.target.value))} disabled={blocked}>
                {RUN_CHOICES.map((value) => <option key={value} value={value}>{value === 1 ? t('chimeraSim.originalSeedOnly') : t('chimeraSim.runs2', { value })}</option>)}
              </select></label>
              {running
                ? <button className="button ghost" onClick={() => void stop()} disabled={stopping || stopSent}><CircleStop size={16} />{stopping ? t('sim.stopping') : t('chimeraSim.stop')}</button>
                : <button className="button primary" onClick={() => void start()} disabled={starting || !captureId || !teamReady}><CirclePlay size={16} />{starting ? t('sim.starting') : t('chimeraSim.simulateCurrentRules')}</button>}
            </div>
            {rebuilt && <p className="muted">{t('hydraSim.otherDifficultyHint', { difficulty: difficultyText(language, chosenDifficulty) })}</p>}
            {stopSent && running && <p className="muted simulation-request-status" role="status">{t('sim.stopSent')}</p>}
            {error && <p className="simulation-warning" role="alert"><AlertTriangle size={14} />{error}</p>}
            {job && job.status !== 'complete' && <div className="simulation-progress">
              <div className="bar"><span style={{ width: `${Math.round(((job.finishedRuns ?? 0) / Math.max(job.runs, 1)) * 100)}%` }} /></div>
              <span>{jobText(language, job)}</span>
            </div>}
            {job?.status === 'failed' && <AdviceNote language={language} advice={job.advice} />}
            <SimulationReadFeedback resource={resource} />
            {summary && <div className="simulation-result-source"><strong>{t('sim.lastSimulation')}</strong><span>{t('sim.resultInputs', { difficulty: difficultyText(language, summary.capture.difficulty), source: teamSourceLabel(language, summary.capture.teamSource), runs: aggregate?.runs ?? summary.runs?.length ?? 0, capture: summary.capture.id })}</span>
              {inputsChanged && <p className="simulation-warning" role="status">{t('sim.inputsChanged')}</p>}</div>}
            {aggregate && summary && <Digest summary={summary} onOpen={() => onOpenReport(summary.id)} />}
            {(overview?.recent?.length ?? 0) > 1 && <div className="simulation-history">
              <small>{t('chimeraSim.recentSimulations')}</small>
              {overview!.recent.slice(0, 6).map((item) => (
                <button key={item.id} className="simulation-history-row" onClick={() => onOpenReport(item.id)}>
                  <span>{item.createdAt?.slice(5, 16)}</span><strong>{item.strategyName || t('chimeraSim.unnamedStrategy')}</strong>
                  <em>{item.status === 'complete'
                    ? t('hydraSim.median', { damageMedian: damageText(item.damageMedian) })
                      + (item.regroupRuns ? t('hydraSim.regroup', { regroupRuns: item.regroupRuns }) : '')
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

function MarkChips({ marks }: { marks: Aggregate['marks'] }) {
  const { t } = useSim()
  return <span className="inline-heroes">{marks.map((mark) => (
    <HoverCard key={mark.heroTypeId} content={<span>{t('hydraSim.markedInRunsPerRun', { runs: mark.runs, marksPerRun: mark.marksPerRun, firstTurnMedian: mark.firstTurnMedian ?? '?' })}</span>}>
      <span className="mark-chip"><HeroIcon typeId={mark.heroTypeId} size="xs" title={false} />×{mark.marksPerRun}</span>
    </HoverCard>
  ))}</span>
}

function Digest({ summary, onOpen }: { summary: Summary; onOpen: () => void }) {
  const { t } = useSim()
  const aggregate = summary.aggregate!
  return (
    <div className="simulation-digest">
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('chimeraSim.unnamedStrategy')}</strong><span>{summary.createdAt}</span></div>
      <SimulationSampleNote aggregate={aggregate} runs={summary.runs} />
      <div className="simulation-facts">
        <span>{t('chimeraSim.medianDamage')} {simulationSamples(aggregate, summary.runs).hasValid ? damageText(aggregate.damage.median) : '—'}{simulationSamples(aggregate, summary.runs).hasValid && aggregate.minimumDamage > 0 && aggregate.minimumDamageRuns !== null
          ? t('hydraSim.goalMetIn', { minimumDamageRuns: aggregate.minimumDamageRuns, battleEndRuns: aggregate.battleEndRuns }) : ''}</span>
        {aggregate.minimumDamage <= 0 && <span>{t('sim.goalNotSet')}</span>}
        {aggregate.marks.length > 0 && <span>{t('hydraSim.devourMarks')}<MarkChips marks={aggregate.marks} /></span>}
        {aggregate.deaths.length > 0 && <span className="inline-heroes">{t('chimeraSim.deaths')}{aggregate.deaths.map((death) => <span key={death.heroTypeId}><HeroIcon typeId={death.heroTypeId} size="xs" />{t('chimeraSim.text3', { runs: death.runs })}</span>)}</span>}
        {aggregate.regroupRuns > 0 && <span className="bad">{t('hydraSim.runsWouldRegroup', { regroupRuns: aggregate.regroupRuns })}</span>}
        {aggregate.stuckRuns.length > 0 && <span className="bad">{t('chimeraSim.runsStoppedByTheRules', { stuckRunsCount: aggregate.stuckRuns.length })}</span>}
      </div>
      <button className="button ghost" onClick={onOpen}><ListOrdered size={15} />{t('chimeraSim.openFullReport')}</button>
    </div>
  )
}

type Tab = 'overview' | 'rules' | 'log'

export function HydraSimulationReport({ language, simulationId, onClose, heroes, heads, effects, request, onJumpToRule }: {
  language: Lang
  simulationId: string | null
  onClose: () => void
  heroes: SimulationHero[]
  heads: SimHead[]
  effects: SimEffect[]
  request: Request
  onJumpToRule: (index: number) => void
}) {
  const { t } = useI18n()
  const resource = useSimulationResource<Summary>(request, simulationId ? `/api/hydra-simulation?id=${encodeURIComponent(simulationId)}` : null)
  const summary = resource.data
  const [tab, setTab] = useState<Tab>('overview')
  const [runIndex, setRunIndex] = useState(1)
  useEffect(() => {
    setTab('overview')
    setRunIndex(1)
  }, [simulationId])
  const aggregate = summary?.aggregate
  const rebuilt = summary?.capture.openingDifficulty !== undefined
  const description = summary
    ? t('sim.reportSummary', { createdAt: summary.createdAt, difficulty: difficultyText(language, summary.capture.difficulty), calculated: aggregate?.finishedRuns ?? 0, total: aggregate?.runs ?? 0 })
    : t('sim.loading')
  const tabs: [Tab, string][] = [['overview', t('chimeraSim.overview')], ['rules', t('chimeraSim.rules')], ['log', t('sim.actionLog')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} rebuilt={rebuilt} heroes={heroes} heads={heads} effects={effects}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading simulation-report-heading">
              <div className="simulation-report-title"><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('hydraSim.openingForecastReport') : t('hydraSim.hydraStrategySimulationReport')} · {summary?.strategy.name || '…'}</Dialog.Title>
                <Dialog.Close className="icon-button" aria-label={t('chimeraSim.closeSimulationReport')}><X size={19} /></Dialog.Close></div>
              <div className="simulation-report-meta"><Dialog.Description>{description}</Dialog.Description>
              {rebuilt && <em className="tag team-source-tag" title={t('sim.rebuiltHint')}>
                {t('sim.rebuiltFrom', { opening: difficultyText(language, summary?.capture.openingDifficulty) })}</em>}
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
              {aggregate && tab === 'rules' && <>
                <p className="muted">{t('hydraSim.everyRuleInStrategyOrder')}</p>
                <RulesTable rules={aggregate.rules} finishedRuns={aggregate.finishedRuns} withTrials={false} onJump={jump} /></>}
              {summary && tab === 'log' && <LogTab summary={summary} runIndex={runIndex} setRunIndex={setRunIndex} request={request} onJump={jump} />}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </SimProvider>
  )
}

function OverviewTab({ summary, onRun, onJump }: { summary: Summary; onRun: (index: number) => void; onJump: (index: number) => void }) {
  const { lang, t, swapped } = useSim()
  const aggregate = summary.aggregate!
  const lines = useFindings(aggregate, summary.runs)
  const samples = simulationSamples(aggregate, summary.runs)
  const hasGoals = aggregate.minimumDamage > 0 || aggregate.devourConditions > 0
  return <>
    <SimulationSampleNote aggregate={aggregate} runs={summary.runs} />
    <div className="simulation-metrics">
      <div><small>{t('chimeraSim.damageMedianMinMax')}</small><strong>{samples.hasValid ? damageText(aggregate.damage.median) : '—'}</strong><span>{samples.hasValid ? `${damageText(aggregate.damage.min)} – ${damageText(aggregate.damage.max)}` : '—'}</span></div>
      <div><small>{t('hydraSim.minimumDamageMet')}</small><strong>{aggregate.minimumDamage > 0 && samples.hasValid ? t('hydraSim.runs', { minimumDamageRuns: aggregate.minimumDamageRuns ?? 0, battleEndRuns: aggregate.battleEndRuns }) : '—'}</strong><span>{aggregate.minimumDamage > 0 ? <>{t('hydraSim.goal')}{damageText(aggregate.minimumDamage)}</> : t('sim.goalNotSet')}</span></div>
      <div><small>{t('hydraSim.liveOpeningForecastWouldRegroup')}</small><strong className={aggregate.regroupRuns ? 'bad' : ''}>{t('hydraSim.runs2', { regroupRuns: aggregate.regroupRuns, finishedRuns: aggregate.finishedRuns })}</strong></div>
      <div><small>{t('hydraSim.marksHeadsKilledPerRun')}</small><strong>{aggregate.marksPerRunMedian ?? '—'} · {aggregate.headKillsPerRunMedian ?? '—'}</strong></div>
      <div><small>{t('chimeraSim.stoppedByTheRules')}</small><strong className={aggregate.stuckRuns.length ? 'bad' : ''}>{t('chimeraSim.runs5', { stuckRunsCount: aggregate.stuckRuns.length, finishedRuns: aggregate.finishedRuns })}</strong></div>
    </div>
    {aggregate.marks.length > 0 && <p className="simulation-marks">{t('hydraSim.championsReceivingDevourMarks')}<MarkChips marks={aggregate.marks} /></p>}
    {summary.verdict?.conclusion && (summary.verdict.verdict === 'retry' || samples.hasValid && hasGoals) && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}>{toolText(lang, summary.verdict.conclusion)}</p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t('hydraSim.turn', { turn: run.stuck!.turn ?? '?' })} onJump={onJump} onLog={() => onRun(run.index)} />)}
    <div className="simulation-scroll" tabIndex={0} aria-label={t('chimeraSim.overview')}><table className="simulation-table">
      <thead><tr><th>{t('chimeraSim.run2')}</th><th>{t('chimeraSim.seed')}</th><th>{t('chimeraSim.result')}</th><th>{t('chimeraSim.damage')}</th><th>{t('hydraSim.devourMarkOrder')}</th><th>{t('chimeraSim.deaths2')}</th><th>{t('hydraSim.headsKilled')}</th><th>{t('hydraSim.liveVerdict')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => {
          const kills = Object.values(run.headKills).reduce((total, value) => total + value, 0)
          return <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t('hydraSim.stoppedTurn', { turn: run.stuck.turn ?? '?' })}</em>
              : run.status === 'complete' ? t('hydraSim.finishedTurns', { turn: run.turn ?? '?' }) : run.status === 'partial' ? t('sim.partialCalculation') : toolText(lang, run.reason ?? run.status)}</td>
            <td>{damageText(run.damage)}</td>
            <td><span className="mark-order">{run.marks.map((mark) => <HoverCard key={mark.markIndex} content={<span>{t('hydraSim.markTurn', { markIndex: mark.markIndex, applyTurn: mark.applyTurn })}{mark.swallowed === false
                ? t('hydraSim.notSwallowedDiedFirstOr') : ''}</span>}>
              <span className={mark.swallowed === false ? 'mark-not-swallowed' : undefined}><HeroIcon typeId={mark.heroTypeId} size="xs" title={false} /></span></HoverCard>)}</span></td>
            <td>{run.deaths.length ? <HeroList typeIds={run.deaths.map((death) => death.heroTypeId)} /> : '—'}</td>
            <td>{kills}</td>
            <td>{run.verdict.verdict === 'retry'
              ? <HoverCard content={<div className="hover-list">{(run.verdict.violations ?? []).map((violation, index) => <span key={index}>{violationText(lang, violation)}</span>)}</div>}><em className="tag bad">{t('hydraSim.regroup2')}</em></HoverCard>
              : run.verdict.verdict === 'continue' && hasGoals && run.status === 'complete' ? <em className="tag">{t('hydraSim.continue')}</em> : <span className="muted">—</span>}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('sim.actionLog')}</button></td>
          </tr>
        })}
      </tbody>
    </table></div>
  </>
}

function LogTab({ summary, runIndex, setRunIndex, request, onJump }: { summary: Summary; runIndex: number; setRunIndex: (index: number) => void; request: Request; onJump: (index: number) => void }) {
  const { t, lang, heads } = useSim()
  // A response for another report or run never replaces the one asked for last.
  const resource = useSimulationResource<RunDetail>(request, `/api/hydra-simulation?id=${encodeURIComponent(summary.id)}&run=${runIndex}`)
  const detail = resource.data
  const actors = useMemo(() => detail?.actors ?? [], [detail])
  const byId = useMemo(() => new Map<number, ActorInfo>(actors.map((actor) => [actor.actorId, actor])), [actors])
  // A head type to look at: its own actions and those aimed at it (kept across runs too).
  const [head, setHead] = useState<number | null>(null)
  const kind = useCallback((typeId: number) => {
    const known = findHead(heads, typeId)
    return known?.canonicalTypeId ?? known?.typeId ?? typeId
  }, [heads])
  const headOptions = useMemo(() => {
    const options = new Map<number, { key: number; icon: ReactNode; name: string }>()
    for (const actor of actors) {
      if (!actor.player && !options.has(kind(actor.heroTypeId)))
        options.set(kind(actor.heroTypeId), { key: kind(actor.heroTypeId), icon: <HeadIcon typeId={actor.heroTypeId} size="xs" />,
          name: headName(lang, heads, actor.heroTypeId) })
    }
    return [...options.values()]
  }, [actors, kind, lang, heads])
  const scope = useMemo(() => head === null ? null : (row: HydraRow) => [byId.get(row.actorId), byId.get(row.targetId)]
    .some((actor) => actor !== undefined && !actor.player && kind(actor.heroTypeId) === head), [head, byId, kind])
  const filters = useMemo(() => [
    { key: 'marks', label: t('hydraSim.onlyActionsWithADevour'), test: (row: HydraRow) => row.marked.length > 0 || row.deaths.some((id) => id >= 0) },
    { key: 'chain', label: t('chimeraSim.onlyActionsWithAllyAttacks'), test: (row: HydraRow) => hasChainedSkill(row.uses) },
    { key: 'reserved', label: t('chimeraSim.onlyActionsThatUsedA'), test: (row: HydraRow) => row.reservationReleased === true },
  ], [t])
  const runs = (summary.runs ?? []).map((run) => run.index).sort((a, b) => a - b)
  return <ActionLog<HydraRow>
    rows={detail?.timeline ?? null}
    bossSkills={detail?.bossSkills}
    actors={actors}
    runs={runs}
    runIndex={runIndex}
    setRunIndex={setRunIndex}
    groupOf={(row) => Math.floor(Math.max(row.turn - 1, 0) / TURNS_PER_GROUP)}
    groupTitle={(block, rows) => {
      const damage = rows.reduce((total, row) => total + row.damage, 0)
      const marks = rows.reduce((total, row) => total + row.marked.length, 0)
      const deaths = rows.reduce((total, row) => total + row.deaths.filter((id) => id >= 0 && byId.get(id)?.player).length, 0)
      const first = block * TURNS_PER_GROUP + 1
      return <><strong>{t('hydraSim.turns', { first, value: first + TURNS_PER_GROUP - 1 })}</strong><span>{t('hydraSim.actionsDamage', { rowsCount: rows.length, damage: damageText(damage), marks, deaths })}</span></>
    }}
    openByDefault={(_, rows) => rows.some((row) => row.marked.length > 0)}
    filters={filters}
    toolbar={<FocusPicker label={t('sim.hydraHead')} value={head} onChange={setHead} options={headOptions} />}
    scope={scope}
    enemyToggle={t('hydraSim.showTheHeadsActions')}
    rowWhen={(row) => t('hydraSim.turn', { turn: row.turn })}
    extras={(row) => row.marked.map((actorId) => {
      const actor = byId.get(actorId)
      return <em key={`m${actorId}`} className="bad mark">{t('hydraSim.devourMark')}{actor ? <HeroIcon typeId={actor.heroTypeId} size="xs" /> : `#${actorId}`}</em>
    })}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t('hydraSim.turn', { turn: detail.stuck.turn ?? '?' })} onJump={onJump} /> : undefined}
    failure={resource.status === 'error' ? <SimulationReadFeedback resource={resource} log /> : undefined}
    stopped={Boolean(detail?.stuck)}
    onResetFilters={() => setHead(null)}
    unavailable={detail && head !== null && !headOptions.some((option) => option.key === head) ? [headName(lang, heads, head)] : []}
  />
}

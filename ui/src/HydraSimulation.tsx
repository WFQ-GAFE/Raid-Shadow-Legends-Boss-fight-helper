import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { translate, useI18n } from './i18n'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { CollapsiblePanel } from './CollapsiblePanel'
import { HoverCard } from './HoverCard'
import { jobText, type SimulationJob } from './ChimeraSimulation'
import {
  ActionLog, AdviceNote, CapturePicker, HeroIcon, RulesTable, SimProvider, SimulationTeamButton, StuckCard, TeamSourcePicker,
  bossDifficultyText, damageText, hasChainedSkill, originalRunLabel, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel, toolText, useSim, useSimulationInputs,
  type ActorInfo, type BossSkill, type Lang, type LogRow, type RuleAggregate, type SimEffect, type SimHead, type SimulationHero, type StuckDetail,
  type SimulationTeam, type TeamSources,
} from './SimulationShared'

// Hydra strategy simulation: a captured Hydra opening replayed in the
// isolated original engine with the rules being edited, up to the game's turn
// limit. See tools/hydra_simulation*.py.

type Request = <T>(path: string, init?: RequestInit) => Promise<T>

type HydraCapture = { id: string; capturedAt?: string; stageId?: number; difficulty?: number; seed?: number; teamHeroTypeIds: number[]; strategyName?: string | null }
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
  capture: { id: string; stageId?: number; seed?: number; teamHeroTypeIds?: number[]; difficulty?: number; teamSource?: string; teamSavedAt?: string }
  // The team the simulation ran with (reports from 1.1.1 on).
  team?: SimulationTeam | null
  runs?: RunSummary[]
  aggregate?: Aggregate
}

type HydraRow = LogRow & { hydraTurns: number; marked: number[] }
type RunDetail = { index: number; status: string; reason?: string | null; stuck?: StuckDetail | null; seed: number; actors: (ActorInfo & { dead: boolean })[]; timeline: HydraRow[]; bossSkills?: Record<string, BossSkill> }

const RUN_CHOICES = [1, 5, 10, 20, 100]
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

function useFindings(aggregate: Aggregate): Finding[] {
  const { lang, t } = useSim()
  const finished = Math.max(aggregate.finishedRuns, 1)
  const lines: Finding[] = []
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
  if (aggregate.reservationReleasesPerRun > 0) {
    lines.push({ tone: 'warn', text: translate(lang, 'hydraSim.onAverageActionsPerRun', { reservationReleasesPerRun: aggregate.reservationReleasesPerRun }) })
  }
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
  const { captures, sources, capture, captureId, setCaptureId, teamSource, setTeamSource, teamOptions, teamReady } = useSimulationInputs(
    language, strategyId, overview?.strategyId && overview.strategyId !== strategyId ? [] : overview?.captures ?? [], overview?.teamSources, strategyTeam)
  const [runs, setRuns] = useState(5)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<Summary | null>(null)
  const running = job?.status === 'running'
  const latestId = job && job.status !== 'running' ? job.id : overview?.recent?.[0]?.id
  useEffect(() => {
    if (!latestId || summary?.id === latestId) return
    let cancelled = false
    request<Summary>(`/api/hydra-simulation?id=${encodeURIComponent(latestId)}`)
      .then((value) => { if (!cancelled) setSummary(value) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [latestId, summary?.id, request])
  const swapped = teamSource !== 'battle'
  const teamDiffers = !swapped && capture && strategyTeam.length > 0 && !capture.teamHeroTypeIds.every((typeId) => strategyTeam.includes(typeId))
  const start = async () => {
    setError('')
    try {
      await request('/api/hydra-simulation/start', { method: 'POST', body: JSON.stringify({ config: draft, strategyId, strategyName, captureId, runs, pid, teamSource }) })
    } catch (reason) {
      setError(toolText(language, reason instanceof Error ? reason.message : String(reason)))
    }
  }
  const stop = () => { void request('/api/hydra-simulation/stop', { method: 'POST', body: '{}' }).catch(() => undefined) }
  const aggregate = summary?.aggregate
  const hint = running
    ? t('chimeraSim.running', { finishedRuns: job?.finishedRuns ?? 0, runs: job?.runs ?? 0 })
    : aggregate
      ? t('hydraSim.medianDamage', { median: damageText(aggregate.damage.median) })
      : captures.length ? t('chimeraSim.ready') : t('hydraSim.needsAHydraBattleCapture')
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} heroes={heroes} heads={heads} effects={effects}>
      <CollapsiblePanel id="hydra:simulation" title={t('chimeraSim.strategySimulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!captures.length ? <p className="muted">{t('hydraSim.noHydraBattleHasBeen')}</p> : <>
            <p className="muted">{t(capture?.id.startsWith('strategy-package:') && teamSource === 'author' && sources?.author?.offlineReady
              ? 'sim.replaysSavedStrategyPackage' : 'hydraSim.replaysASavedHydraBattle')}</p>
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
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('chimeraSim.unnamedStrategy')}</strong><span>{summary.createdAt} · {t('chimeraSim.runs3', { finishedRuns: aggregate.finishedRuns })}</span></div>
      <div className="simulation-facts">
        <span>{t('chimeraSim.medianDamage')} {damageText(aggregate.damage.median)}{aggregate.minimumDamage > 0 && aggregate.minimumDamageRuns !== null
          ? t('hydraSim.goalMetIn', { minimumDamageRuns: aggregate.minimumDamageRuns, battleEndRuns: aggregate.battleEndRuns }) : ''}</span>
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
  const [summary, setSummary] = useState<Summary | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [runIndex, setRunIndex] = useState(1)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!simulationId) return
    setSummary(null)
    setTab('overview')
    setRunIndex(1)
    setError('')
    request<Summary>(`/api/hydra-simulation?id=${encodeURIComponent(simulationId)}`)
      .then(setSummary).catch((reason) => setError(toolText(language, reason instanceof Error ? reason.message : String(reason))))
  }, [simulationId, request, language])
  const aggregate = summary?.aggregate
  const description = summary
    ? t('chimeraSim.runsSimulated', { createdAt: summary.createdAt, difficulty: difficultyText(language, summary.capture.difficulty), finishedRuns: aggregate?.finishedRuns ?? 0, runs: aggregate?.runs ?? 0, seed: summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? 'original' : 'replay' })
    : t('sim.loading')
  const tabs: [Tab, string][] = [['overview', t('chimeraSim.overview')], ['rules', t('chimeraSim.rules')], ['log', t('sim.actionLog')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} heroes={heroes} heads={heads} effects={effects}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading">
              <span><Dialog.Title><FlaskConical size={17} /> {summary?.kind === 'battle' ? t('hydraSim.openingForecastReport') : t('hydraSim.hydraStrategySimulationReport')} · {summary?.strategy.name || '…'}</Dialog.Title>
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
  const lines = useFindings(aggregate)
  return <>
    <div className="simulation-metrics">
      <div><small>{t('chimeraSim.damageMedianMinMax')}</small><strong>{damageText(aggregate.damage.median)}</strong><span>{damageText(aggregate.damage.min)} – {damageText(aggregate.damage.max)}</span></div>
      {aggregate.minimumDamage > 0 && <div><small>{t('hydraSim.minimumDamageMet')}</small><strong>{t('hydraSim.runs', { minimumDamageRuns: aggregate.minimumDamageRuns ?? 0, battleEndRuns: aggregate.battleEndRuns })}</strong><span>{t('hydraSim.goal')}{damageText(aggregate.minimumDamage)}</span></div>}
      <div><small>{t('hydraSim.liveOpeningForecastWouldRegroup')}</small><strong className={aggregate.regroupRuns ? 'bad' : ''}>{t('hydraSim.runs2', { regroupRuns: aggregate.regroupRuns, finishedRuns: aggregate.finishedRuns })}</strong></div>
      <div><small>{t('hydraSim.marksHeadsKilledPerRun')}</small><strong>{aggregate.marksPerRunMedian ?? '—'} · {aggregate.headKillsPerRunMedian ?? '—'}</strong></div>
      <div><small>{t('chimeraSim.stoppedByTheRules')}</small><strong className={aggregate.stuckRuns.length ? 'bad' : ''}>{t('chimeraSim.runs5', { stuckRunsCount: aggregate.stuckRuns.length, finishedRuns: aggregate.finishedRuns })}</strong></div>
    </div>
    {aggregate.marks.length > 0 && <p className="simulation-marks">{t('hydraSim.championsReceivingDevourMarks')}<MarkChips marks={aggregate.marks} /></p>}
    {summary.verdict?.conclusion && <p className={`simulation-verdict ${summary.verdict.verdict ?? ''}`}>{toolText(lang, summary.verdict.conclusion)}</p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t('hydraSim.turn', { turn: run.stuck!.turn ?? '?' })} onJump={onJump} onLog={() => onRun(run.index)} />)}
    <table className="simulation-table">
      <thead><tr><th>{t('chimeraSim.run2')}</th><th>{t('chimeraSim.seed')}</th><th>{t('chimeraSim.result')}</th><th>{t('chimeraSim.damage')}</th><th>{t('hydraSim.devourMarkOrder')}</th><th>{t('chimeraSim.deaths2')}</th><th>{t('hydraSim.headsKilled')}</th><th>{t('hydraSim.liveVerdict')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => {
          const kills = Object.values(run.headKills).reduce((total, value) => total + value, 0)
          return <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t('hydraSim.stoppedTurn', { turn: run.stuck.turn ?? '?' })}</em>
              : run.status === 'complete' ? t('hydraSim.finishedTurns', { turn: run.turn ?? '?' }) : toolText(lang, run.reason ?? run.status)}</td>
            <td>{damageText(run.damage)}</td>
            <td><span className="mark-order">{run.marks.map((mark) => <HoverCard key={mark.markIndex} content={<span>{t('hydraSim.markTurn', { markIndex: mark.markIndex, applyTurn: mark.applyTurn })}{mark.swallowed === false
                ? t('hydraSim.notSwallowedDiedFirstOr') : ''}</span>}>
              <span className={mark.swallowed === false ? 'mark-not-swallowed' : undefined}><HeroIcon typeId={mark.heroTypeId} size="xs" title={false} /></span></HoverCard>)}</span></td>
            <td>{run.deaths.length ? <HeroList typeIds={run.deaths.map((death) => death.heroTypeId)} /> : '—'}</td>
            <td>{kills}</td>
            <td>{run.verdict.verdict === 'retry'
              ? <HoverCard content={<div className="hover-list">{(run.verdict.violations ?? []).map((violation, index) => <span key={index}>{violationText(lang, violation)}</span>)}</div>}><em className="tag bad">{t('hydraSim.regroup2')}</em></HoverCard>
              : run.verdict.verdict === 'continue' ? <em className="tag">{t('hydraSim.continue')}</em> : <span className="muted">—</span>}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('sim.actionLog')}</button></td>
          </tr>
        })}
      </tbody>
    </table>
  </>
}

function LogTab({ summary, runIndex, setRunIndex, request, onJump }: { summary: Summary; runIndex: number; setRunIndex: (index: number) => void; request: Request; onJump: (index: number) => void }) {
  const { t } = useSim()
  const [detail, setDetail] = useState<RunDetail | null>(null)
  useEffect(() => {
    setDetail(null)
    request<RunDetail>(`/api/hydra-simulation?id=${encodeURIComponent(summary.id)}&run=${runIndex}`).then(setDetail).catch(() => undefined)
  }, [summary.id, runIndex, request])
  const actors = useMemo(() => detail?.actors ?? [], [detail])
  const byId = useMemo(() => new Map<number, ActorInfo>(actors.map((actor) => [actor.actorId, actor])), [actors])
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
    enemyToggle={t('hydraSim.showTheHeadsActions')}
    extras={(row) => row.marked.map((actorId) => {
      const actor = byId.get(actorId)
      return <em key={`m${actorId}`} className="bad mark">{t('hydraSim.devourMark')}{actor ? <HeroIcon typeId={actor.heroTypeId} size="xs" /> : `#${actorId}`}</em>
    })}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t('hydraSim.turn', { turn: detail.stuck.turn ?? '?' })} onJump={onJump} /> : undefined}
  />
}

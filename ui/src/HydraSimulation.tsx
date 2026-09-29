import { useEffect, useMemo, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, CheckCircle2, CirclePlay, CircleStop, FlaskConical, ListOrdered, X } from 'lucide-react'
import { CollapsiblePanel } from './CollapsiblePanel'
import { HoverCard } from './HoverCard'
import { jobText, type SimulationJob } from './ChimeraSimulation'
import {
  ActionLog, AdviceNote, CapturePicker, HeroIcon, RulesTable, SimProvider, SimulationTeamButton, StuckCard, TeamSourcePicker,
  bossDifficultyText, damageText, hasChainedSkill, originalRunLabel, ruleLabel, stuckReasonText, teamCheckText, teamSourceLabel, teamSourceOptions, toolText, useSim,
  type ActorInfo, type BossSkill, type Lang, type LogRow, type RuleAggregate, type SimEffect, type SimHead, type SimulationHero, type StuckDetail,
  type SimulationTeam, type TeamSource, type TeamSources,
} from './SimulationShared'

// Hydra strategy simulation: a captured Hydra opening replayed in the
// isolated original engine with the rules being edited, up to the game's turn
// limit. See tools/hydra_simulation*.py.

type Request = <T>(path: string, init?: RequestInit) => Promise<T>

type HydraCapture = { id: string; capturedAt?: string; stageId?: number; difficulty?: number; seed?: number; teamHeroTypeIds: number[]; strategyName?: string | null }
type HydraRecent = { id: string; createdAt?: string; strategyName?: string; status?: string; runs?: number; finishedRuns?: number; damageMedian?: number | null; regroupRuns?: number; stuckRuns?: number }
export type HydraSimulationOverview = { captures: HydraCapture[]; job?: SimulationJob | null; recent: HydraRecent[]; teamSources?: TeamSources }

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
  const en = lang === 'en'
  if (violation.cause === 'damage') return en
    ? `damage ${damageText(violation.predictedDamage)} below the goal ${damageText(violation.minimumDamage)}`
    : `伤害 ${damageText(violation.predictedDamage)} 低于目标 ${damageText(violation.minimumDamage)}`
  const who = <HeroList typeIds={[violation.actualHeroTypeId ?? 0]} />
  if (violation.relation === 'neverMarked' && violation.swallowed) return <>{who}{en
    ? ` swallowed (mark #${violation.markIndex}, turn ${violation.applyTurn})`
    : ` 被吞下（第 ${violation.markIndex} 个标记，第 ${violation.applyTurn} 回合）`}</>
  if (violation.relation === 'neverMarked') return <>{en ? 'devour mark on ' : '吞噬标记落在 '}{who}{en ? ` (turn ${violation.applyTurn})` : `（第 ${violation.applyTurn} 回合）`}</>
  return <>{en ? `mark #${violation.markIndex} on ` : `第 ${violation.markIndex} 个吞噬标记落在 `}{who}{en ? ` (turn ${violation.applyTurn})` : `（第 ${violation.applyTurn} 回合）`}</>
}

type Finding = { tone: 'bad' | 'warn' | 'good'; text: ReactNode }

function useFindings(aggregate: Aggregate): Finding[] {
  const { lang, t } = useSim()
  const en = lang === 'en'
  const finished = Math.max(aggregate.finishedRuns, 1)
  const lines: Finding[] = []
  const groups = new Map<string, StuckRun[]>()
  for (const run of aggregate.stuckRuns) groups.set(`${run.activeHeroTypeId}:${run.reason}`, [...(groups.get(`${run.activeHeroTypeId}:${run.reason}`) ?? []), run])
  for (const runs of groups.values()) {
    const turns = runs.map((run) => run.turn ?? '?').join(en ? ', ' : '、')
    lines.push({ tone: 'bad', text: <>{t(`${runs.length}/${finished} 场模拟中断：`, `${runs.length}/${finished} runs stopped: `)}<HeroList typeIds={[runs[0].activeHeroTypeId ?? 0]} />{en
      ? ` ${stuckReasonText(lang, runs[0].reason)} (turn ${turns}). A live takeover would stall there; open the run for the champion's skills and every rule checked.`
      : `${stuckReasonText(lang, runs[0].reason)}（第 ${turns} 回合）。实战接管会在这里卡住；打开该场可查看当时的技能状态和逐条规则检查。`}</> })
  }
  if (aggregate.regroupRuns > 0) {
    lines.push({ tone: 'bad', text: en
      ? `${aggregate.regroupRuns}/${finished} runs would make the live opening forecast regroup (${aggregate.regroupCauses.devour} for the devour-order conditions, ${aggregate.regroupCauses.damage} for the minimum damage).`
      : `${aggregate.regroupRuns}/${finished} 场会让实战的开局推演免费重整（吞噬顺序条件 ${aggregate.regroupCauses.devour} 场，最低伤害 ${aggregate.regroupCauses.damage} 场）。` })
  }
  if (aggregate.minimumDamage > 0 && aggregate.minimumDamageRuns !== null && aggregate.minimumDamageRuns < aggregate.battleEndRuns) {
    lines.push({ tone: 'bad', text: en
      ? `Only ${aggregate.minimumDamageRuns}/${aggregate.battleEndRuns} runs reached the minimum damage of ${damageText(aggregate.minimumDamage)}.`
      : `只有 ${aggregate.minimumDamageRuns}/${aggregate.battleEndRuns} 场达到最低伤害 ${damageText(aggregate.minimumDamage)}。` })
  }
  for (const death of aggregate.deaths) {
    lines.push({ tone: 'warn', text: <><HeroList typeIds={[death.heroTypeId]} />{en
      ? ` died in ${death.runs}/${finished} runs (median turn ${death.firstTurnMedian ?? '?'}).`
      : ` 在 ${death.runs}/${finished} 场阵亡（中位第 ${death.firstTurnMedian ?? '?'} 回合）。`}</> })
  }
  const unused = aggregate.rules.filter((rule) => rule.ruleIndex !== null && rule.uses === 0)
  if (unused.length) {
    const names = unused.slice(0, 5).map((rule) => `#${rule.ruleIndex} ${ruleLabel(lang, rule.rule)}`).join(en ? ', ' : '、')
    lines.push({ tone: 'warn', text: en
      ? `${unused.length} rules were never used in any run: ${names}${unused.length > 5 ? ' …' : ''}`
      : `${unused.length} 条规则在所有模拟中都没有被用到：${names}${unused.length > 5 ? ' …' : ''}` })
  }
  if (aggregate.reservationReleasesPerRun > 0) {
    lines.push({ tone: 'warn', text: en
      ? `On average ${aggregate.reservationReleasesPerRun} actions per run had to use a skill reserved for a strict rule, because nothing else was usable.`
      : `平均每场 ${aggregate.reservationReleasesPerRun} 次行动只能动用为严格规则保留的技能（没有其他可用技能）。` })
  }
  if (!lines.length) lines.push({ tone: 'good', text: en
    ? 'Every run finished without stopping, met the damage goal and kept the devour-order conditions.'
    : '每一场都顺利打完，达到伤害目标，也没有违反吞噬顺序条件。' })
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
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const captures = overview?.captures ?? []
  const job = overview?.job ?? null
  const [captureId, setCaptureId] = useState('')
  const [teamSource, setTeamSource] = useState<TeamSource>('battle')
  const [runs, setRuns] = useState(5)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<Summary | null>(null)
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
    request<Summary>(`/api/hydra-simulation?id=${encodeURIComponent(latestId)}`)
      .then((value) => { if (!cancelled) setSummary(value) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [latestId, summary?.id, request])
  const capture = captures.find((item) => item.id === captureId)
  const teamOptions = teamSourceOptions(language, overview?.teamSources, capture?.teamHeroTypeIds ?? [], strategyTeam)
  const teamReady = teamOptions.find((option) => option.source === teamSource)?.ready ?? false
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
    ? t(`模拟中 ${job?.finishedRuns ?? 0}/${job?.runs ?? 0}`, `Running ${job?.finishedRuns ?? 0}/${job?.runs ?? 0}`)
    : aggregate
      ? t(`伤害中位 ${damageText(aggregate.damage.median)}`, `Median damage ${damageText(aggregate.damage.median)}`)
      : captures.length ? t('可以模拟', 'Ready') : t('需要一场六头蛇开局数据', 'Needs a Hydra battle capture')
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} heroes={heroes} heads={heads} effects={effects}>
      <CollapsiblePanel id="hydra:simulation" title={t('策略模拟', 'Strategy Simulation')} hint={hint}>
        <section className="card simulation-card" data-i18n-skip>
          {!captures.length ? <p className="muted">{t(
            '还没有六头蛇开局数据。从开局接管打一场六头蛇后（1.1.1 起每场都会保存），就能用那场战斗的队伍、装备和蛇头模拟当前规则。',
            'No Hydra battle has been captured yet. Take over one Hydra battle from its start (1.1.1 saves every such battle); its team, gear and heads can then be used to simulate your current rules.')}</p> : <>
            <p className="muted">{t(
              '在离线原版引擎里重打一场已保存的六头蛇，直到游戏的回合上限：第 1 场用原战斗的随机种子（完全复现那场战斗），其余场次每次模拟都抽新的随机种子，检验伤害和吞噬顺序是否稳定。使用的是编辑器里当前的规则（包括未保存的修改）；每场约 5–10 秒，最多 5 场同时进行。',
              "Replays a saved Hydra battle in an offline copy of the game's own engine up to the game's turn limit. Run 1 uses the original battle seed (an exact replay); the other runs draw fresh random seeds every time to test how stable damage and devour order are. Uses the rules currently in the editor, including unsaved changes; about 5–10 s per run, up to five at a time.")}</p>
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
            {aggregate && summary && <Digest summary={summary} onOpen={() => onOpenReport(summary.id)} />}
            {(overview?.recent?.length ?? 0) > 1 && <div className="simulation-history">
              <small>{t('最近的模拟', 'Recent simulations')}</small>
              {overview!.recent.slice(0, 6).map((item) => (
                <button key={item.id} className="simulation-history-row" onClick={() => onOpenReport(item.id)}>
                  <span>{item.createdAt?.slice(5, 16)}</span><strong>{item.strategyName || t('未命名策略', 'Unnamed strategy')}</strong>
                  <em>{item.status === 'complete'
                    ? t(`伤害中位 ${damageText(item.damageMedian)}`, `Median ${damageText(item.damageMedian)}`)
                      + (item.regroupRuns ? t(` · 会重整 ${item.regroupRuns}`, ` · ${item.regroupRuns} regroup`) : '')
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

function MarkChips({ marks }: { marks: Aggregate['marks'] }) {
  const { t } = useSim()
  return <span className="inline-heroes">{marks.map((mark) => (
    <HoverCard key={mark.heroTypeId} content={<span>{t(`${mark.runs} 场被吞噬标记 · 平均每场 ${mark.marksPerRun} 次 · 首次中位第 ${mark.firstTurnMedian ?? '?'} 回合`,
      `Marked in ${mark.runs} runs · ${mark.marksPerRun} per run · first around turn ${mark.firstTurnMedian ?? '?'}`)}</span>}>
      <span className="mark-chip"><HeroIcon typeId={mark.heroTypeId} size="xs" title={false} />×{mark.marksPerRun}</span>
    </HoverCard>
  ))}</span>
}

function Digest({ summary, onOpen }: { summary: Summary; onOpen: () => void }) {
  const { t } = useSim()
  const aggregate = summary.aggregate!
  return (
    <div className="simulation-digest">
      <div className="simulation-digest-head"><strong>{summary.strategy.name || t('未命名策略', 'Unnamed strategy')}</strong><span>{summary.createdAt} · {t(`${aggregate.finishedRuns} 场`, `${aggregate.finishedRuns} runs`)}</span></div>
      <div className="simulation-facts">
        <span>{t('伤害中位', 'Median damage')} {damageText(aggregate.damage.median)}{aggregate.minimumDamage > 0 && aggregate.minimumDamageRuns !== null
          ? t(`（达到目标 ${aggregate.minimumDamageRuns}/${aggregate.battleEndRuns} 场）`, ` (goal met in ${aggregate.minimumDamageRuns}/${aggregate.battleEndRuns})`) : ''}</span>
        {aggregate.marks.length > 0 && <span>{t('吞噬标记：', 'Devour marks: ')}<MarkChips marks={aggregate.marks} /></span>}
        {aggregate.deaths.length > 0 && <span className="inline-heroes">{t('阵亡：', 'Deaths: ')}{aggregate.deaths.map((death) => <span key={death.heroTypeId}><HeroIcon typeId={death.heroTypeId} size="xs" />{t(`${death.runs}场`, `${death.runs}`)}</span>)}</span>}
        {aggregate.regroupRuns > 0 && <span className="bad">{t(`实战会重整 ${aggregate.regroupRuns} 场`, `${aggregate.regroupRuns} runs would regroup`)}</span>}
        {aggregate.stuckRuns.length > 0 && <span className="bad">{t(`规则卡住中断 ${aggregate.stuckRuns.length} 场`, `${aggregate.stuckRuns.length} runs stopped by the rules`)}</span>}
      </div>
      <button className="button ghost" onClick={onOpen}><ListOrdered size={15} />{t('查看完整报告', 'Open full report')}</button>
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
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
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
    ? t(`${summary.createdAt} · ${difficultyText(language, summary.capture.difficulty)} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} 场完成模拟（${summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? '第 1 场用原战斗的随机种子' : '第 1 场为原战斗复现'}）`,
        `${summary.createdAt} · ${difficultyText(language, summary.capture.difficulty)} · ${aggregate?.finishedRuns ?? 0}/${aggregate?.runs ?? 0} runs simulated (${summary.capture.teamSource && summary.capture.teamSource !== 'battle' ? 'run 1 uses the original battle seed' : 'run 1 replays the original battle'})`)
    : t('读取中…', 'Loading…')
  const tabs: [Tab, string][] = [['overview', t('总览', 'Overview')], ['rules', t('规则', 'Rules')], ['log', t('出手记录', 'Action log')]]
  const jump = (index: number) => { onClose(); onJumpToRule(index) }
  return (
    <SimProvider lang={language} boss="hydra" teamSource={summary?.capture.teamSource} heroes={heroes} heads={heads} effects={effects}>
      <Dialog.Root open={Boolean(simulationId)} onOpenChange={(open) => { if (!open) onClose() }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content log-dialog simulation-report" data-i18n-skip>
            <div className="dialog-heading">
              <span><Dialog.Title><FlaskConical size={17} /> {t('六头蛇策略模拟报告', 'Hydra strategy simulation report')} · {summary?.strategy.name || '…'}</Dialog.Title>
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
              {aggregate && tab === 'rules' && <>
                <p className="muted">{t('按策略中的顺序列出每条规则在模拟里的使用情况和伤害占比。', 'Every rule in strategy order with how it was used in the simulations and its share of the damage.')}</p>
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
      <div><small>{t('伤害（中位 · 最低–最高）', 'Damage (median · min–max)')}</small><strong>{damageText(aggregate.damage.median)}</strong><span>{damageText(aggregate.damage.min)} – {damageText(aggregate.damage.max)}</span></div>
      {aggregate.minimumDamage > 0 && <div><small>{t('达到最低伤害', 'Minimum damage met')}</small><strong>{t(`${aggregate.minimumDamageRuns ?? 0}/${aggregate.battleEndRuns} 场`, `${aggregate.minimumDamageRuns ?? 0}/${aggregate.battleEndRuns} runs`)}</strong><span>{t('目标 ', 'Goal ')}{damageText(aggregate.minimumDamage)}</span></div>}
      <div><small>{t('实战开局推演会重整', 'Live opening forecast would regroup')}</small><strong className={aggregate.regroupRuns ? 'bad' : ''}>{t(`${aggregate.regroupRuns}/${aggregate.finishedRuns} 场`, `${aggregate.regroupRuns}/${aggregate.finishedRuns} runs`)}</strong></div>
      <div><small>{t('每场吞噬标记 · 斩杀蛇头（中位）', 'Marks · heads killed per run (median)')}</small><strong>{aggregate.marksPerRunMedian ?? '—'} · {aggregate.headKillsPerRunMedian ?? '—'}</strong></div>
      <div><small>{t('规则卡住中断', 'Stopped by the rules')}</small><strong className={aggregate.stuckRuns.length ? 'bad' : ''}>{t(`${aggregate.stuckRuns.length}/${aggregate.finishedRuns} 场`, `${aggregate.stuckRuns.length}/${aggregate.finishedRuns} runs`)}</strong></div>
    </div>
    {aggregate.marks.length > 0 && <p className="simulation-marks">{t('被吞噬标记的英雄：', 'Champions receiving devour marks: ')}<MarkChips marks={aggregate.marks} /></p>}
    <ul className="simulation-findings">
      {lines.map((line, index) => <li key={index} className={line.tone}>{line.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}<span>{line.text}</span></li>)}
    </ul>
    {(summary.runs ?? []).filter((run) => run.stuck).map((run) => <StuckCard key={run.index} run={run} stuck={run.stuck!}
      where={t(`第 ${run.stuck!.turn ?? '?'} 回合`, `turn ${run.stuck!.turn ?? '?'}`)} onJump={onJump} onLog={() => onRun(run.index)} />)}
    <table className="simulation-table">
      <thead><tr><th>{t('场次', 'Run')}</th><th>{t('随机种子', 'Seed')}</th><th>{t('结果', 'Result')}</th><th>{t('伤害', 'Damage')}</th><th>{t('吞噬标记顺序', 'Devour mark order')}</th><th>{t('阵亡', 'Deaths')}</th><th>{t('斩杀蛇头', 'Heads killed')}</th><th>{t('实战判定', 'Live verdict')}</th><th /></tr></thead>
      <tbody>
        {(summary.runs ?? []).slice().sort((a, b) => a.index - b.index).map((run) => {
          const kills = Object.values(run.headKills).reduce((total, value) => total + value, 0)
          return <tr key={run.index} className={run.stuck ? 'stuck' : ''}>
            <td>{run.index}{run.exact && <em className="tag">{originalRunLabel(lang, swapped)}</em>}</td>
            <td>{run.seed}</td>
            <td>{run.stuck
              ? <em className="tag bad">{t(`中断 · 第 ${run.stuck.turn ?? '?'} 回合`, `Stopped · turn ${run.stuck.turn ?? '?'}`)}</em>
              : run.status === 'complete' ? t(`打完 · ${run.turn ?? '?'} 回合`, `Finished · ${run.turn ?? '?'} turns`) : toolText(lang, run.reason ?? run.status)}</td>
            <td>{damageText(run.damage)}</td>
            <td><span className="mark-order">{run.marks.map((mark) => <HoverCard key={mark.markIndex} content={<span>{t(`第 ${mark.markIndex} 个标记 · 第 ${mark.applyTurn} 回合`, `Mark ${mark.markIndex} · turn ${mark.applyTurn}`)}{mark.swallowed === false
                ? t(' · 没有被吞下（先阵亡或战斗已结束）', ' · not swallowed (died first or the battle ended)') : ''}</span>}>
              <span className={mark.swallowed === false ? 'mark-not-swallowed' : undefined}><HeroIcon typeId={mark.heroTypeId} size="xs" title={false} /></span></HoverCard>)}</span></td>
            <td>{run.deaths.length ? <HeroList typeIds={run.deaths.map((death) => death.heroTypeId)} /> : '—'}</td>
            <td>{kills}</td>
            <td>{run.verdict.verdict === 'retry'
              ? <HoverCard content={<div className="hover-list">{(run.verdict.violations ?? []).map((violation, index) => <span key={index}>{violationText(lang, violation)}</span>)}</div>}><em className="tag bad">{t('会重整', 'Regroup')}</em></HoverCard>
              : run.verdict.verdict === 'continue' ? <em className="tag">{t('继续', 'Continue')}</em> : <span className="muted">—</span>}</td>
            <td><button className="link-button" onClick={() => onRun(run.index)}>{t('出手记录', 'Action log')}</button></td>
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
    { key: 'marks', label: t('只看出现吞噬标记或阵亡的行动', 'Only actions with a devour mark or a death'), test: (row: HydraRow) => row.marked.length > 0 || row.deaths.some((id) => id >= 0) },
    { key: 'chain', label: t('只看有组队攻击、反击等连带行动的出手', 'Only actions with ally attacks, counterattacks and the like'), test: (row: HydraRow) => hasChainedSkill(row.uses) },
    { key: 'reserved', label: t('只看动用保留技能的出手', 'Only actions that used a reserved skill'), test: (row: HydraRow) => row.reservationReleased === true },
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
      return <><strong>{t(`第 ${first}–${first + TURNS_PER_GROUP - 1} 回合`, `Turns ${first}–${first + TURNS_PER_GROUP - 1}`)}</strong><span>{t(
        `${rows.length} 次行动 · 伤害 ${damageText(damage)}${marks ? ` · 吞噬标记 ${marks}` : ''}${deaths ? ` · 阵亡 ${deaths}` : ''}`,
        `${rows.length} actions · damage ${damageText(damage)}${marks ? ` · ${marks} devour marks` : ''}${deaths ? ` · ${deaths} deaths` : ''}`)}</span></>
    }}
    openByDefault={(_, rows) => rows.some((row) => row.marked.length > 0)}
    filters={filters}
    enemyToggle={t('显示蛇头的行动', "Show the heads' actions")}
    extras={(row) => row.marked.map((actorId) => {
      const actor = byId.get(actorId)
      return <em key={`m${actorId}`} className="bad mark">{t('吞噬标记 → ', 'Devour mark → ')}{actor ? <HeroIcon typeId={actor.heroTypeId} size="xs" /> : `#${actorId}`}</em>
    })}
    footer={detail?.stuck ? <StuckCard run={{ index: detail.index, exact: detail.index === 1 && summary.runs?.find((run) => run.index === 1)?.exact }} stuck={detail.stuck}
      where={t(`第 ${detail.stuck.turn ?? '?'} 回合`, `turn ${detail.stuck.turn ?? '?'}`)} onJump={onJump} /> : undefined}
  />
}

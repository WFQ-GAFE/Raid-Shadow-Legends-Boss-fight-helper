import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { effectName } from './effectNames'
import { AlertTriangle, Skull, Sparkles, Users, Waves, X, Zap } from 'lucide-react'
import { HoverCard } from './HoverCard'
import { EffectGlyph, EffectSearch, type SearchOption } from './EffectSearch'
import { backendText, hasMessage, translate, type MessageKey, type Translate, type UiLanguage, useI18n } from './i18n'
import { TeamPreviewDialog, type TeamSnapshot } from './TeamPreview'
import { TargetNote, TargetTag } from './TargetNote'
import type { TargetReach } from './ruleTargets'

// Pieces shared by the Chimera and Hydra strategy simulation reports:
// icons with hover cards for champions, heads, skills, effects and trials,
// the per-decision battle state, the capture picker, stuck cards, the rules
// table and the action log. See tools/simulation_common.py for the data.

export type Lang = UiLanguage

export type SimulationRequest = <T>(path: string, init?: RequestInit) => Promise<T>
export type SimulationLoadState<T> = { status: 'idle' | 'loading' | 'error' | 'ready'; key: string | null; data: T | null; error: string }

// Abort saves work when the transport supports it. The generation also protects
// against transports which ignore abort, including responses already in flight.
export function createSimulationReader<T>(publish: (state: SimulationLoadState<T>) => void) {
  let generation = 0
  let controller: AbortController | undefined
  const cancel = () => { generation += 1; controller?.abort(); controller = undefined }
  const load = async (request: SimulationRequest, key: string) => {
    cancel()
    const current = generation
    controller = new AbortController()
    publish({ status: 'loading', key, data: null, error: '' })
    try {
      const data = await request<T>(key, { signal: controller.signal })
      if (generation === current) publish({ status: 'ready', key, data, error: '' })
    } catch (reason) {
      if (generation === current) publish({ status: 'error', key, data: null, error: reason instanceof Error ? reason.message : String(reason) })
    }
  }
  return { load, cancel }
}

export function useSimulationResource<T>(request: SimulationRequest, key: string | null) {
  const [state, setState] = useState<SimulationLoadState<T>>({ status: 'idle', key: null, data: null, error: '' })
  const [retryIndex, setRetryIndex] = useState(0)
  const [reader] = useState(() => createSimulationReader<T>(setState))
  useEffect(() => {
    if (key) void reader.load(request, key)
    return reader.cancel
  }, [request, key, retryIndex, reader])
  const retry = useCallback(() => setRetryIndex((value) => value + 1), [])
  // Hide old report/run data immediately when selection changes, before effects
  // run. Cancelling only in the effect would allow one frame of mismatched data.
  return { ...(state.key === key ? state : { status: key ? 'loading' as const : 'idle' as const, key, data: null, error: '' }), retry }
}

export function simulationInputSignature(value: unknown): string {
  const canonical = (item: unknown): unknown => Array.isArray(item) ? item.map(canonical)
    : item && typeof item === 'object' ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => [key, canonical(value)])) : item
  return JSON.stringify(canonical(value))
}

export function simulationResultParametersChanged(saved: { id: string; difficulty?: number; teamSource?: string; teamSavedAt?: string; bossHealthPercent?: number },
  selected: { captureId: string; difficulty?: number; teamSource: string; teamSavedAt?: string; bossHealthPercent?: number }) {
  return saved.id !== selected.captureId
    || saved.difficulty !== undefined && selected.difficulty !== undefined && saved.difficulty !== selected.difficulty
    || Boolean(saved.teamSource && saved.teamSource !== selected.teamSource)
    || Boolean(saved.teamSavedAt && selected.teamSavedAt && saved.teamSavedAt !== selected.teamSavedAt)
    || saved.bossHealthPercent !== undefined && selected.bossHealthPercent !== undefined && Math.abs(saved.bossHealthPercent - selected.bossHealthPercent) > 0.001
}

export function simulationSamples(aggregate: { runs: number; finishedRuns: number; battleEndRuns?: number }, runs?: { status: string }[]) {
  const valid = aggregate.battleEndRuns ?? runs?.filter((run) => run.status === 'complete').length ?? 0
  return { total: aggregate.runs, calculated: aggregate.finishedRuns, valid, hasValid: valid > 0 }
}

export function simulationHealthInput(value: string | null, defaultValue: number | undefined) {
  const empty = value === null || value.trim() === ''
  const parsed = empty ? undefined : Number(value)
  const invalid = !empty && (parsed === undefined || !Number.isFinite(parsed) || parsed <= 0 || parsed > 100)
  return { empty, invalid, value: invalid ? undefined : parsed, defaultValue }
}

export function SimulationReadFeedback({ resource, log = false }: { resource: { status: SimulationLoadState<unknown>['status']; error: string; retry: () => void }; log?: boolean }) {
  const { lang, t } = useSim()
  if (resource.status === 'loading') return <p className="muted simulation-request-status" role="status">{t('sim.loading')}</p>
  if (resource.status !== 'error') return null
  return <div className="simulation-warning" role="alert"><AlertTriangle size={14} /><span>{t(log ? 'sim.logReadFailed' : 'sim.readFailed', { reason: toolText(lang, resource.error) })}</span>
    <button type="button" className="button ghost" onClick={resource.retry}>{t('sim.retry')}</button></div>
}

export function SimulationSampleNote({ aggregate, runs }: { aggregate: { runs: number; finishedRuns: number; battleEndRuns?: number }; runs?: { status: string }[] }) {
  const { t } = useSim()
  const samples = simulationSamples(aggregate, runs)
  return <div className="simulation-sample-note"><p className="muted">{t('sim.sampleCounts', samples)}</p>
    <p className={samples.hasValid ? 'muted' : 'simulation-warning'}>{t(samples.hasValid ? 'sim.damageSamples' : 'sim.noValidSamples', samples)}</p></div>
}
export type SimSkill = { typeId?: number; slot: number; name?: string; description?: string; defaultCooldown?: number }
export type SimulationHero = { typeId: number; name: string; runtimeTypeIds?: number[]; skills: SimSkill[] }
export type SimulationTrial = {
  id: number
  form?: string
  formId?: number
  part?: string
  partId?: number
  difficulty?: string
  difficultyId?: number
  description?: string
  effects?: { id?: number; name?: string }[]
}
export type SimEffect = { token: string; icon: string; label: string; labelEn?: string; group: string; iconReady?: boolean }
export type SimHead = { typeId: number; canonicalTypeId?: number; resourceKind?: string; name: string }
export type BossKind = 'chimera' | 'hydra'
// A boss skill from the game's static data, by skill type id (tools/boss_skills.py).
export type BossSkill = { name?: string; description?: string; defaultCooldown?: number }

// One actor in a decision snapshot: [effectTypeId, turnsLeft, stacks] and [skillTypeId, cooldown].
// What is left of an absorbing buff (shields, golden armor, stone skin, life barrier):
// v = value left, i = initial; a hit counter shield has n = hits left instead.
export type Absorb = { v?: number; i?: number; n?: number }
export type SnapshotEffect = [typeId: number, turns: number, count: number, absorb?: Absorb]
// mh: max health, only next to an absorbing buff.
export type SnapshotActor = { id: number; t: number; s: 'a' | 'e'; hp: number | null; fx: SnapshotEffect[]; d?: 1; f?: number; cd?: [number, number][]; dv?: number; neck?: 1; mh?: number }
export type BattleSnapshot = { actors: SnapshotActor[] }
export type ActorInfo = { actorId: number; heroTypeId: number; player: boolean }

type SimData = {
  lang: Lang
  boss: BossKind
  heroes: SimulationHero[]
  heads: SimHead[]
  effects: Map<string, SimEffect>
  trialById: Map<number, SimulationTrial>
  // Another team was put into the saved opening, or it ran on another difficulty: run 1 keeps only its seed.
  swapped: boolean
  // The skills the bosses used in the shown battle.
  bossSkills: Map<number, BossSkill>
}

const SimContext = createContext<SimData>({ lang: 'zh-CN', boss: 'chimera', heroes: [], heads: [], effects: new Map(), trialById: new Map(), swapped: false, bossSkills: new Map() })

export function SimProvider({ lang, boss, heroes, heads = [], effects = [], trialById = new Map(), teamSource, rebuilt = false, children }: {
  lang: Lang; boss: BossKind; heroes: SimulationHero[]; heads?: SimHead[]; effects?: SimEffect[]; trialById?: Map<number, SimulationTrial>
  teamSource?: string | null; rebuilt?: boolean; children: ReactNode
}) {
  const effectMap = useMemo(() => new Map(effects.map((effect) => [effect.token, effect])), [effects])
  const swapped = Boolean(teamSource && teamSource !== 'battle') || rebuilt
  const value = useMemo(() => ({ lang, boss, heroes, heads, effects: effectMap, trialById, swapped, bossSkills: new Map<number, BossSkill>() }),
    [lang, boss, heroes, heads, effectMap, trialById, swapped])
  return <SimContext.Provider value={value}>{children}</SimContext.Provider>
}

export function useSim() {
  const data = useContext(SimContext)
  const { t } = useI18n()
  return { ...data, t }
}

export function toolText(lang: Lang, value: string) {
  return backendText(value, lang)
}

// Game descriptions carry Unity rich-text colour tags.
export function plainText(value?: string) {
  return (value ?? '').replace(/<[^>]+>/g, '').trim()
}

export function percent(value: number | null | undefined, digits = 0) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`
}

// Damage in the game's own K/M/B notation, in both languages.
export function damageText(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const size = Math.abs(value)
  if (size >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (size >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (size >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return String(Math.round(value))
}

// --- Champions, heads and skills ---

// Battles report rank-specific hero type ids (base id + 1..6); the catalog
// lists them as runtimeTypeIds.
export function findHero(heroes: SimulationHero[], typeId: number | undefined) {
  if (typeof typeId !== 'number') return undefined
  const ids = (hero: SimulationHero) => (hero.runtimeTypeIds?.length ? hero.runtimeTypeIds : [hero.typeId])
  const exact = heroes.find((hero) => hero.typeId === typeId || ids(hero).includes(typeId))
  if (exact) return exact
  const rank = typeId % 10
  return rank >= 1 && rank <= 6 ? heroes.find((hero) => hero.typeId === typeId - rank || ids(hero).includes(typeId - rank)) : undefined
}

export function heroName(lang: Lang, heroes: SimulationHero[], typeId: number | undefined) {
  return findHero(heroes, typeId)?.name ?? (typeId ? translate(lang, 'sim.champion', { typeId }) : '—')
}

// Battle head type ids add a difficulty step to the catalog's base id (a multiple of 40).
export function findHead(heads: SimHead[], typeId: number | undefined) {
  if (typeof typeId !== 'number') return undefined
  return heads.find((head) => head.typeId === typeId || head.canonicalTypeId === typeId)
    ?? heads.find((head) => head.typeId === Math.floor(typeId / 40) * 40 || head.canonicalTypeId === Math.floor(typeId / 40) * 40)
}

export function headName(lang: Lang, heads: SimHead[], typeId: number | undefined) {
  const head = findHead(heads, typeId)
  if (!head) return translate(lang, 'sim.hydraHead')
  // In Chinese the game's own names come first (the client is Chinese); before the
  // game has named the heads the catalog holds "蛇头 <id> · <kind>".
  if (lang === 'zh-CN' && !/^蛇头\s*\d/.test(head.name)) return head.name
  const key = `hydra.head.${head.resourceKind ?? ''}`
  return hasMessage(key) ? translate(lang, key) : head.name
}

function skillOf(heroes: SimulationHero[], heroTypeId: number | undefined, skillTypeId: number) {
  return findHero(heroes, heroTypeId)?.skills.find((item) => item.typeId === skillTypeId)
}

export function skillName(lang: Lang, heroes: SimulationHero[], heroTypeId: number | undefined, skillTypeId: number) {
  return skillOf(heroes, heroTypeId, skillTypeId)?.name ?? translate(lang, 'sim.skill', { value: skillTypeId % 100 })
}

function AssetImage({ sources, fallback, className, hideWithoutImage = false }: { sources: string[]; fallback: ReactNode; className: string; hideWithoutImage?: boolean }) {
  const [index, setIndex] = useState(0)
  const key = sources.join('|')
  useEffect(() => setIndex(0), [key])
  if (hideWithoutImage && index >= sources.length) return null
  return <span className={className}>{index < sources.length
    ? <img src={sources[index]} alt="" loading="lazy" onError={() => setIndex((value) => value + 1)} />
    : fallback}</span>
}

export function HeroIcon({ typeId, size = 'sm', title = true }: { typeId: number | undefined; size?: 'xs' | 'sm' | 'md'; title?: boolean }) {
  const { lang, heroes } = useSim()
  const hero = findHero(heroes, typeId)
  const canonical = hero?.typeId ?? typeId
  const icon = <AssetImage className={`sim-icon hero ${size}`} sources={canonical ? [`/api/asset/hero/${canonical}`, `/hero-fallbacks/${canonical}.png`] : []}
    fallback={<b>{hero?.name?.slice(0, 1) ?? '?'}</b>} />
  return title ? <HoverCard content={<strong>{heroName(lang, heroes, typeId)}</strong>}>{icon}</HoverCard> : icon
}

export function HeadIcon({ typeId, size = 'sm' }: { typeId: number | undefined; size?: 'xs' | 'sm' | 'md' }) {
  const { lang, heads } = useSim()
  const head = findHead(heads, typeId)
  const identity = head?.canonicalTypeId ?? head?.typeId ?? typeId
  return <HoverCard content={<strong>{headName(lang, heads, typeId)}</strong>}>
    <AssetImage className={`sim-icon head ${size}`} sources={identity ? [`/api/asset/head/${identity}`] : []} fallback={<Waves size={13} />} />
  </HoverCard>
}

export function ChimeraIcon({ size = 'sm', form }: { size?: 'xs' | 'sm' | 'md'; form?: number }) {
  const { lang, t } = useSim()
  return <HoverCard content={<strong>{t('sim.chimera')}{form !== undefined ? ` · ${formName(lang, form)}` : ''}</strong>}>
    <span className={`sim-icon boss ${size}`}><Skull size={size === 'md' ? 18 : 13} /></span>
  </HoverCard>
}

// A champion, a Hydra head or the Chimera.
export function ActorIcon({ typeId, player, size = 'sm', form }: { typeId: number | undefined; player: boolean; size?: 'xs' | 'sm' | 'md'; form?: number }) {
  const { boss } = useSim()
  if (player) return <HeroIcon typeId={typeId} size={size} />
  return boss === 'hydra' ? <HeadIcon typeId={typeId} size={size} /> : <ChimeraIcon size={size} form={form} />
}

export function SkillIcon({ heroTypeId, skillTypeId, player = true, cooldown, size = 'sm', hideWithoutIcon = false, form }: {
  heroTypeId: number | undefined; skillTypeId: number; player?: boolean; cooldown?: number; size?: 'xs' | 'sm' | 'md'
  // The Chimera's form, for an enemy skill.
  form?: number
  // Skills hidden in the game have no sprite (and no place in the battle view).
  hideWithoutIcon?: boolean
}) {
  const [missing, setMissing] = useState(false)
  const { lang, heroes, t } = useSim()
  const skill = player ? skillOf(heroes, heroTypeId, skillTypeId) : undefined
  const hero = findHero(heroes, heroTypeId)
  const name = player ? skillName(lang, heroes, heroTypeId, skillTypeId) : ''
  const sources = player && heroTypeId ? [`/api/asset/skill/${hero?.typeId ?? heroTypeId}/${skillTypeId}`] : []
  const card = player ? <div className="hover-skill">
    <strong>{name}</strong>
    {typeof cooldown === 'number' && cooldown >= 0 && <em>{cooldown > 0 ? t('sim.cooldown', { cooldown }) : t('sim.ready')}</em>}
    {skill?.defaultCooldown ? <small>{t('sim.baseCooldown', { defaultCooldown: skill.defaultCooldown })}</small> : null}
    {skill?.description && <p>{plainText(skill.description)}</p>}
  </div> : <BossSkillCard actorTypeId={heroTypeId} skillTypeId={skillTypeId} cooldown={cooldown} form={form} />
  if (hideWithoutIcon && (missing || !sources.length)) return null
  return <HoverCard content={card}>
    <span className={`sim-skill ${size} ${typeof cooldown === 'number' && cooldown > 0 ? 'cooling' : ''}`}>
      {hideWithoutIcon
        ? <span className="sim-skill-image"><img src={sources[0]} alt="" loading="lazy" onError={() => setMissing(true)} /></span>
        : <AssetImage className="sim-skill-image" sources={sources} fallback={<Zap size={12} />} />}
      {typeof cooldown === 'number' && cooldown > 0 && <em>{cooldown}</em>}
    </span>
  </HoverCard>
}

export function bossSkillName(lang: Lang, skills: Map<number, BossSkill>, skillTypeId: number) {
  // The heads' shared mark, swallow and exposed-neck skills: the game only has
  // untranslated working names for them ("Feast", "Ravening [P]").
  const shared = `boss.skill.${skillTypeId}`
  if (hasMessage(shared)) return translate(lang, shared)
  const known = skills.get(skillTypeId)?.name
  if (known) return known
  // Shown next to the head or Chimera form that used it.
  return translate(lang, 'sim.skill', { value: skillTypeId % 100 })
}

// The game's stand-in for a missing description ("Skill 6666618 description").
const PLACEHOLDER_DESCRIPTION = /^Skill \d+ description$/i

// "Head of Decay", or "Chimera · Ram form".
export function bossActorLabel(lang: Lang, boss: BossKind, heads: SimHead[], typeId: number | undefined, form?: number) {
  if (boss === 'hydra') return headName(lang, heads, typeId)
  const shape = formName(lang, form)
  if (!shape) return translate(lang, 'sim.chimera')
  return translate(lang, 'sim.chimeraForm', { shape })
}

export function BossSkillCard({ actorTypeId, skillTypeId, cooldown, form }: { actorTypeId: number | undefined; skillTypeId: number; cooldown?: number; form?: number }) {
  const { lang, boss, heads, bossSkills, t } = useSim()
  const skill = bossSkills.get(skillTypeId)
  return <div className="hover-skill">
    <strong>{bossSkillName(lang, bossSkills, skillTypeId)}</strong>
    <span>{bossActorLabel(lang, boss, heads, actorTypeId, form)}</span>
    {typeof cooldown === 'number' && cooldown >= 0 && <em>{cooldown > 0 ? t('sim.cooldown', { cooldown }) : t('sim.ready')}</em>}
    {skill?.defaultCooldown ? <small>{t('sim.baseCooldown', { defaultCooldown: skill.defaultCooldown })}</small> : null}
    {skill?.description && PLACEHOLDER_DESCRIPTION.test(plainText(skill.description))
      ? <small>{t('sim.noGameSkillDescription')}</small>
      : skill?.description
        ? <p>{plainText(skill.description)}</p>
        : <small>{t('sim.theDescriptionIsReadOnce')}</small>}
  </div>
}

// Who acted and with what, for an enemy row of the action log.
function EnemyAction({ row }: { row: LogRow }) {
  const { lang, boss, heads, bossSkills } = useSim()
  return <HoverCard content={<BossSkillCard actorTypeId={row.actorTypeId} skillTypeId={row.skillTypeId} form={row.form} />}>
    <span className="enemy-action"><b>{bossActorLabel(lang, boss, heads, row.actorTypeId, row.form)}</b> · <span className="skill-name">{bossSkillName(lang, bossSkills, row.skillTypeId)}</span></span>
  </HoverCard>
}

const EFFECT_GROUP: Record<string, MessageKey> = { 增益: 'effect.group.buff', 减益: 'effect.group.debuff', 特殊: 'effect.group.special', 奇美拉: 'effect.group.chimera' }

// The hover lines for an absorbing buff: what is left, of how much, against max health.
function absorbLines(t: Translate, lang: Lang, absorb: Absorb | undefined, maxHp: number | undefined) {
  if (!absorb) return []
  const number = (value: number) => value.toLocaleString(lang)
  if (typeof absorb.n === 'number') return [t('sim.hitsLeft', { hits: absorb.n, initial: absorb.i ?? '?' })]
  if (typeof absorb.v !== 'number') return []
  return [t('sim.absorbLeft', { value: number(absorb.v) }),
    typeof absorb.i === 'number' ? t('sim.absorbInitial', { value: number(absorb.i) }) : '',
    maxHp ? t('sim.absorbOfMaxHp', { percent: percent(absorb.v / maxHp) }) : ''].filter(Boolean)
}

export function EffectBadge({ effect: [typeId, turns, count, absorb], maxHp }: { effect: SnapshotEffect; maxHp?: number }) {
  const { lang, effects, t } = useSim()
  const option = effects.get(String(typeId))
  const label = option ? effectName(option, lang) : t('sim.effect', { typeId })
  const group = option ? (EFFECT_GROUP[option.group] ? t(EFFECT_GROUP[option.group]) : option.group) : ''
  const absorbing = absorbLines(t, lang, absorb, maxHp)
  const card = <div className="hover-effect">
    <strong>{label}</strong>
    <span>{[group, turns > 0 ? t('sim.turnsLeft', { turns }) : turns === 0 ? t('sim.endsThisTurn') : t('sim.lasting'),
      count > 1 ? t('sim.stacks', { count }) : ''].filter(Boolean).join(' · ')}</span>
    {absorbing.length > 0 && <span className="hover-absorb">{absorbing.join(' · ')}</span>}
  </div>
  return <HoverCard content={card}>
    <span className={`sim-effect ${option?.group === '增益' ? 'buff' : option?.group === '减益' ? 'debuff' : ''}`}>
      {option && option.iconReady !== false
        ? <img src={`/api/asset/effect/${encodeURIComponent(option.icon)}`} alt="" loading="lazy" />
        : <Sparkles size={11} />}
      {turns > 0 && <em>{turns}</em>}
      {count > 1 && <i>×{count}</i>}
    </span>
    {absorbing.length > 0 && <b className="sim-absorb">{typeof absorb?.n === 'number' ? absorb.n : damageText(absorb?.v)}</b>}
  </HoverCard>
}

// --- Chimera trials: "Ram · Trial 1 · Easy", as the game groups them ---

const FORM_KEYS: MessageKey[] = ['chimera.form.0', 'chimera.form.1', 'chimera.form.2', 'chimera.form.3']
export const FORM_INDEX: Record<string, number> = { Ultimate: 0, Ram: 1, Lion: 2, Snake: 3, Viper: 3 }
// Boss difficulties as the game names them: Chimera stages 1-6 are Easy..Ultra-Nightmare,
// Hydra stages 1-4 are Normal..Nightmare.
const BOSS_DIFFICULTY_KEYS: MessageKey[] = [
  'boss.difficulty.0', 'boss.difficulty.1', 'boss.difficulty.2', 'boss.difficulty.3', 'boss.difficulty.4', 'boss.difficulty.5',
]

export function bossDifficultyText(lang: Lang, boss: BossKind, difficulty?: number) {
  const index = typeof difficulty === 'number' ? (boss === 'hydra' ? difficulty : difficulty - 1) : -1
  return (BOSS_DIFFICULTY_KEYS[index] ? translate(lang, BOSS_DIFFICULTY_KEYS[index]) : undefined)
    ?? translate(lang, 'sim.difficulty', { difficulty: difficulty ?? '?' })
}

const LEVEL_KEYS: MessageKey[] = ['trial.level.1', 'trial.level.2', 'trial.level.3']
const LEVEL_INDEX: Record<string, number> = { Easy: 1, Normal: 2, Hard: 3 }
const PART_INDEX: Record<string, number> = { Wing: 1, Tail: 2, Paw: 3 }

export function formName(lang: Lang, index: number | undefined) {
  if (index === undefined || index < 0 || index > 3) return ''
  return translate(lang, FORM_KEYS[index])
}

export function formNameByKey(lang: Lang, form: string | null | undefined) {
  return form ? formName(lang, FORM_INDEX[form]) || form : ''
}

// Trial ids are 8000000 + difficulty × 100 + n, n = 1..27 in form, trial, level order.
export function trialIdentity(trial: SimulationTrial | undefined, id: number) {
  const n = ((id - 8000000) % 100 + 100) % 100
  const derived = n >= 1 && n <= 27
    ? { form: Math.floor((n - 1) / 9) + 1, part: Math.floor(((n - 1) % 9) / 3) + 1, level: ((n - 1) % 3) + 1 }
    : undefined
  return {
    form: trial?.formId ?? (trial?.form !== undefined ? FORM_INDEX[trial.form] : undefined) ?? derived?.form,
    part: trial?.partId ?? (trial?.part ? PART_INDEX[trial.part] : undefined) ?? derived?.part,
    level: (trial?.difficulty ? LEVEL_INDEX[trial.difficulty] : undefined) ?? trial?.difficultyId ?? derived?.level,
  }
}

export function trialShortLabel(lang: Lang, trial: SimulationTrial | undefined, id: number) {
  const { form, part, level } = trialIdentity(trial, id)
  const pieces = [
    form !== undefined ? formName(lang, form) : '',
    part ? translate(lang, 'sim.trial', { part }) : translate(lang, 'chimeraSim.trial'),
    level ? translate(lang, LEVEL_KEYS[level - 1]) : '',
  ].filter(Boolean)
  return pieces.join(' · ')
}

export function TrialCard({ id }: { id: number }) {
  const { lang, trialById, effects, t } = useSim()
  const trial = trialById.get(id)
  const description = plainText(trial?.description)
  const trialEffects = (trial?.effects ?? []).filter((effect) => typeof effect.id === 'number')
  return <div className="hover-trial">
    <strong>{trialShortLabel(lang, trial, id)}</strong>
    <p>{description || t('sim.theTrialTextAppearsOnce')}</p>
    {trialEffects.length > 0 && <div className="hover-trial-effects">{trialEffects.map((effect) => {
      const option = effects.get(String(effect.id))
      return <span key={effect.id}>{option && option.iconReady !== false
        ? <img src={`/api/asset/effect/${encodeURIComponent(option.icon)}`} alt="" />
        : <Sparkles size={11} />}{option ? effectName(option, lang) : effect.name}</span>
    })}</div>}
  </div>
}

// The turns a Chimera trial can progress in, until it was completed: those of its form,
// and the last Boss turn before each of its windows (actions there already count for
// the coming form's trials). Null when the trial's form is unknown.
export function trialTurnScope<Row extends { form?: number; bossTurns: number }>(rows: Row[], trial: SimulationTrial | undefined,
                                                                              id: number, completedAt: number | undefined) {
  const form = trialIdentity(trial, id).form
  if (form === undefined) return null
  const lead = new Set<number>()
  rows.forEach((row, index) => {
    if (row.form !== form && rows[index + 1]?.form === form) lead.add(row.bossTurns)
  })
  return (row: Row) => (row.form === form || lead.has(row.bossTurns))
    && (typeof completedAt !== 'number' || row.bossTurns <= completedAt)
}

// Trials have no art of their own: the buff or debuff a trial asks for stands in for it.
export function TrialGlyph({ id }: { id: number }) {
  const { trialById, effects } = useSim()
  const effect = (trialById.get(id)?.effects ?? []).find((item) => typeof item.id === 'number')
  const option = effect ? effects.get(String(effect.id)) : undefined
  return <EffectGlyph icon={option?.icon} ready={Boolean(option) && option?.iconReady !== false} />
}

// One of several things to look at (a Chimera form, a Hydra head type), or all of them.
export function FocusPicker<Key extends number>({ label, options, value, onChange }: {
  label: string; options: { key: Key; icon: ReactNode; name: string }[]; value: Key | null; onChange: (key: Key | null) => void
}) {
  const { t } = useSim()
  if (options.length < 2 && value === null) return null
  return <div className="focus-picker">
    <span>{label}</span>
    <button type="button" className={value === null ? 'active' : ''} onClick={() => onChange(null)}>{t('sim.all')}</button>
    {options.map((option) => <button type="button" key={option.key} className={value === option.key ? 'active' : ''}
      aria-pressed={value === option.key} onClick={() => onChange(value === option.key ? null : option.key)}>
      {option.icon}<span>{option.name}</span>
    </button>)}
  </div>
}

// A trial to pick (icon, name, a short note), with its card on hover.
export function TrialButton({ id, note, tone = '', active, mandatory, onClick }: {
  id: number; note?: ReactNode; tone?: string; active: boolean; mandatory?: boolean; onClick: () => void
}) {
  const { lang, trialById, t } = useSim()
  return <HoverCard content={<TrialCard id={id} />} focusable={false} className="trial-button-anchor">
    <button type="button" className={`trial-button ${tone}${active ? ' active' : ''}`} aria-pressed={active} onClick={onClick}>
      <TrialGlyph id={id} /><span>{trialShortLabel(lang, trialById.get(id), id)}</span>
      {mandatory && <b>{t('chimeraSim.mandatory2')}</b>}{note && <em>{note}</em>}
    </button>
  </HoverCard>
}

export function TrialTag({ id, children, className = '' }: { id: number; children?: ReactNode; className?: string }) {
  const { lang, trialById } = useSim()
  return <HoverCard content={<TrialCard id={id} />} className={`trial-tag ${className}`}>
    {trialShortLabel(lang, trialById.get(id), id)}{children}
  </HoverCard>
}

// Controller and simulation texts name trials by id: show those as trial tags.
export function TextWithTrials({ text }: { text: string }) {
  const { lang } = useSim()
  const translated = toolText(lang, text)
  const parts = translated.split(/(?<![\d])(800\d{4})(?![\d])/)
  return <>{parts.map((part, index) => (index % 2 === 1 ? <TrialTag key={index} id={Number(part)} /> : part))}</>
}

// --- Battle state at one decision ---

export function StateStrip({ state, actorId, targetId }: { state: BattleSnapshot; actorId?: number; targetId?: number }) {
  const { t } = useSim()
  const allies = state.actors.filter((actor) => actor.s === 'a')
  const enemies = state.actors.filter((actor) => actor.s === 'e')
  const row = (actor: SnapshotActor) => (
    <div key={actor.id} className={`state-actor ${actor.d ? 'dead' : ''} ${actor.id === actorId ? 'acting' : ''} ${actor.id === targetId ? 'targeted' : ''}`}>
      <div className="state-portrait">
        <ActorIcon typeId={actor.t} player={actor.s === 'a'} form={actor.f} size="md" />
        <span className="state-hp"><span style={{ width: `${Math.max(0, Math.min(100, actor.hp ?? 0))}%` }} /></span>
        <small>{actor.d ? t('sim.dead') : actor.hp === null ? '' : `${Math.round(actor.hp)}%`}</small>
      </div>
      <div className="state-details">
        {actor.fx.length > 0 ? <div className="state-effects">{actor.fx.map((effect) => <EffectBadge key={effect[0]} effect={effect} maxHp={actor.mh} />)}</div>
          : <span className="state-none">{t('sim.noEffects')}</span>}
        {actor.cd && actor.cd.length > 0 && <div className="state-skills">{actor.cd.map(([skill, cooldown]) =>
          <SkillIcon key={skill} heroTypeId={actor.t} skillTypeId={skill} cooldown={cooldown} size="xs" hideWithoutIcon />)}</div>}
        {actor.neck && <em className="tag">{t('sim.exposedNeck')}</em>}
        {typeof actor.dv === 'number' && <em className="tag warn">{t('sim.devouring')}</em>}
      </div>
    </div>
  )
  return <div className="state-strip">
    <div className="state-side">{allies.map(row)}</div>
    <div className="state-side enemies">{enemies.map(row)}</div>
  </div>
}

// --- Why a forecast or simulation could not conclude, and what to do ---

// explain and action are message tokens; records from 1.1.1 hold Chinese
// with an English copy (explainEn, actionEn).
export type FailureAdvice = { code: string; explain: string; action: string; explainEn?: string; actionEn?: string }

function adviceText(language: Lang, text: string, english?: string) {
  return english !== undefined && language !== 'zh-CN' ? english : backendText(text, language)
}

export function AdviceNote({ language, advice }: { language: Lang; advice?: FailureAdvice | null }) {
  if (!advice) return null
  return <div className="advice-note" data-i18n-skip>
    <p><strong>{translate(language, 'sim.why')}</strong>{adviceText(language, advice.explain, advice.explainEn)}</p>
    <p><strong>{translate(language, 'sim.whatToDo')}</strong>{adviceText(language, advice.action, advice.actionEn)}</p>
  </div>
}

export function adviceTitle(language: Lang, advice?: FailureAdvice | null) {
  if (!advice) return undefined
  return `${adviceText(language, advice.explain, advice.explainEn)}\n${adviceText(language, advice.action, advice.actionEn)}`
}

// --- The battle a simulation builds on ---

// A battle saved by the tool (every battle it plays from the first turn), or the one saved with
// the strategy group. Its boss is rebuilt from the game's data on the chosen difficulty and its team
// replaced by the strategy's; it still gives the week's rotation, the game version's battle
// settings and the seed of run 1.
export type CaptureOption = { id: string; capturedAt?: string; difficulty?: number; teamHeroTypeIds: number[]; strategyName?: string | null }

export function SimulationBase({ capture, difficultyText, children }: {
  capture: CaptureOption; difficultyText: (difficulty?: number) => string; children?: ReactNode
}) {
  const { t } = useSim()
  const when = capture.id.startsWith('strategy-package:') ? t('sim.baseStrategyPackage')
    : t('sim.baseBattleWhen', { when: capture.capturedAt?.slice(5) ?? '' })
  return <div className="simulation-base">
    <span className="simulation-base-line"><strong>{t('sim.baseBattle')}</strong><span>{when} · {difficultyText(capture.difficulty)}</span>{children}</span>
    <small>{t('sim.baseBattleHint')}</small>
  </div>
}

// The difficulty to simulate: the last one chosen for the boss, else the base battle's.
export function useRunDifficulty(boss: BossKind, base: number | undefined, choices: number[]) {
  const key = `studio:sim:difficulty:${boss}`
  const [chosen, setChosen] = useState<number | null>(() => {
    try {
      const saved = Number(window.localStorage.getItem(key))
      return choices.includes(saved) ? saved : null
    } catch { return null }
  })
  const choose = (value: number) => {
    setChosen(value)
    try { window.localStorage.setItem(key, String(value)) } catch { /* optional preference */ }
  }
  return [chosen ?? base, choose] as const
}

// --- Team source: whose heroes and gear the simulation uses ---

export type TeamSource = 'battle' | 'current' | 'strategy' | 'author'
export type TeamCheck = { checkedAt?: string; compared?: number; matched?: number; orderMatched?: number; rule?: string | null; area?: string | null }
export type TeamSources = {
  strategyId?: string
  // The game is open: heroes and the account's academy, building and area bonuses can be read.
  accountReadable?: boolean
  strategyTeam: number[]
  // The strategy's heroes as they are now; `bound` once the team names this account's heroes.
  current?: { heroTypeIds: number[]; bound: boolean } | null
  // The snapshot taken when the strategy group was saved; `offlineReady` with the account bonuses saved too.
  strategy?: { heroTypeIds: number[]; savedAt?: string; matches: boolean; offlineReady?: boolean } | null
  // An imported strategy's author's team.
  author?: { heroTypeIds: number[]; savedAt?: string; matches?: boolean; offlineReady?: boolean } | null
  check?: TeamCheck | null
}

export function teamSourceLabel(lang: Lang, source?: string | null) {
  switch (source) {
    case 'current': return translate(lang, 'sim.strategySTeamNow')
    case 'strategy': return translate(lang, 'sim.teamWhenTheStrategyWas')
    case 'author': return translate(lang, 'sim.theAuthorSTeam')
    case 'preparation': return translate(lang, 'sim.preparationScreenTeam')  // reports before 1.1.1's final build
    default: return translate(lang, 'sim.theBattleSOwnTeam')
  }
}

// The team a simulation ran with (summary.team: set-up and each hero's battle
// stats when the battle started), in the team view.
export type SimulationTeam = TeamSnapshot & { source?: string; savedAt?: string | null }

export function SimulationTeamButton({ team }: { team?: SimulationTeam | null }) {
  const { lang, heroes, t } = useSim()
  const [open, setOpen] = useState(false)
  if (!team || !(team.heroes?.length)) return null
  const saved = team.savedAt ? t('sim.saved', { savedAt: team.savedAt }) : ''
  return <>
    <button type="button" className="button ghost team-setup-button" onClick={() => setOpen(true)}><Users size={15} />{t('sim.teamSetup')}</button>
    <TeamPreviewDialog language={lang} snapshot={open ? team : null}
      title={t('sim.teamSetupOfThisSimulation')}
      description={t('sim.statsAreTheBattleStats', { source: teamSourceLabel(lang, team.source), saved })}
      onClose={() => setOpen(false)}
      heroName={(typeId) => heroName(lang, heroes, typeId)}
      heroAvatar={(typeId) => <HeroIcon typeId={typeId} size="md" title={false} />}
      skillName={(heroTypeId, skillTypeId) => skillName(lang, heroes, heroTypeId, skillTypeId)} />
  </>
}

export function originalRunLabel(lang: Lang, swapped: boolean) {
  return swapped ? translate(lang, 'sim.originalSeed') : translate(lang, 'sim.original')
}

type TeamSourceOption = { source: TeamSource; team: number[]; ready: boolean; note: string }

export function teamSourceOptions(lang: Lang, sources: TeamSources | undefined, strategyTeam: number[]): TeamSourceOption[] {
  const t: Translate = (key, params) => translate(lang, key, params)
  const hasTeam = strategyTeam.length > 0
  const gameOpen = sources?.accountReadable !== false
  const noTeam = t('sim.thisStrategyHasNoTeam')
  const bonusesNote = t('sim.academyBuildingAndAreaBonuses')
  const current = sources?.current
  const saved = sources?.strategy
  const author = sources?.author
  const options: TeamSourceOption[] = [
    { source: 'current', team: strategyTeam, ready: hasTeam && Boolean(current?.bound) && gameOpen,
      note: !hasTeam ? noTeam
        : !current?.bound ? t('sim.notBoundToYourChampions')
        : !gameOpen ? t('sim.openTheGameTheChampions')
        : t('sim.theChampionsCurrentGearAnd') },
    { source: 'strategy', team: saved?.heroTypeIds ?? strategyTeam,
      ready: hasTeam && Boolean(saved?.matches) && (gameOpen || Boolean(saved?.offlineReady)),
      note: !hasTeam ? noTeam
        : !saved ? t('sim.noSnapshotYetSaveThe')
        : !saved.matches ? t('sim.theStrategySTeamHas')
        : !gameOpen && !saved.offlineReady ? t('sim.openTheGame', { bonusesNote })
        : !gameOpen ? t('sim.savedTeamAndBonuses', { value: saved.savedAt?.slice(5, 16) ?? '' })
        : t('sim.championsAndGearWhenThe', { value: saved.savedAt?.slice(5, 16) ?? '', bonusesNote }) },
  ]
  if (author) options.push({ source: 'author', team: author.heroTypeIds,
    ready: author.heroTypeIds.length > 0 && author.matches !== false && (Boolean(author.offlineReady) || gameOpen),
    note: author.matches === false ? t('sim.theStrategySTeamHas')
      : author.offlineReady ? t('sim.theAuthorsSavedSimulationTeam', { value: author.savedAt?.slice(5, 16) ?? '' })
      : !gameOpen ? t('sim.openTheGame', { bonusesNote })
      : t('sim.theAuthorSChampionsAnd', { value: author.savedAt?.slice(5, 16) ?? '', bonusesNote }) })
  return options
}

export type SimulationPackageStatus = {
  status: 'complete' | 'partial' | 'unavailable'
  reason?: string
  warning?: string
  team: boolean
  opening: boolean
  accountBonuses: boolean
}

export function simulationSaveText(lang: Lang, packaged: SimulationPackageStatus | undefined, fallback: string) {
  if (!packaged) return toolText(lang, fallback)
  if (packaged.status === 'complete') return translate(lang, 'app.strategySavedWithSimulationPackage')
    + (packaged.warning ? ` · ${toolText(lang, packaged.warning)}` : '')
  return translate(lang, 'app.strategySavedWithPartialSimulationPackage', {
    reason: packaged.reason ? toolText(lang, packaged.reason) : translate(lang, 'sim.simulationPackageIncomplete'),
  })
}

// These selections belong to one strategy. The simulation builds on the newest battle the tool
// saved; the battle saved with the strategy group stands in when there is none (an imported strategy
// on a new computer). Its author's team is ready as soon as an imported package arrives in the poll.
export function simulationInputs<Capture extends CaptureOption>(lang: Lang, strategyId: string,
  allCaptures: Capture[], allSources: TeamSources | undefined, strategyTeam: number[],
  preferred?: { teamSource: TeamSource }) {
  const sources = allSources?.strategyId === strategyId ? allSources : undefined
  const capture = allCaptures.find((item) => !item.id.startsWith('strategy-package:'))
    ?? allCaptures.find((item) => item.id === `strategy-package:${strategyId}`)
  const teamOptions = teamSourceOptions(lang, sources, strategyTeam)
  const preferredSource = teamOptions.find((option) => option.source === preferred?.teamSource && option.ready)
  const author = teamOptions.find((option) => option.source === 'author' && option.ready)
  const teamSource = preferredSource?.source ?? author?.source ?? teamOptions.find((option) => option.ready)?.source ?? 'current'
  return { sources, capture, captureId: capture?.id ?? '', teamOptions, teamSource,
    teamReady: Boolean(teamOptions.find((option) => option.source === teamSource)?.ready) }
}

export function useSimulationInputs<Capture extends CaptureOption>(lang: Lang, strategyId: string,
  captures: Capture[], sources: TeamSources | undefined, strategyTeam: number[]) {
  const [choice, setChoice] = useState<{ strategyId: string; teamSource: TeamSource }>()
  const inputs = simulationInputs(lang, strategyId, captures, sources, strategyTeam, choice?.strategyId === strategyId ? choice : undefined)
  const setTeamSource = (teamSource: TeamSource) => setChoice({ strategyId, teamSource })
  return { ...inputs, setTeamSource }
}

export function teamCheckText(lang: Lang, check?: TeamCheck | null) {
  const t: Translate = (key, params) => translate(lang, key, params)
  if (!check || !check.compared) return t('sim.gearDataNotYetChecked')
  return t('sim.gearDataCheckedAgainstReal', { matched: check.matched ?? 0, compared: check.compared, value: check.checkedAt?.slice(5, 16) ?? '' })
}

export function TeamSourcePicker({ options, value, onChange, disabled }: {
  options: TeamSourceOption[]; value: TeamSource; onChange: (source: TeamSource) => void; disabled?: boolean
}) {
  const { lang } = useSim()
  return <div className="team-source-picker" role="radiogroup">
    {options.map((option) => (
      <button key={option.source} type="button" role="radio" aria-checked={option.source === value} disabled={disabled}
        className={`team-source-option ${option.source === value ? 'selected' : ''} ${option.ready ? '' : 'unavailable'}`}
        onClick={() => onChange(option.source)}>
        <strong>{teamSourceLabel(lang, option.source)}</strong>
        <span className="team-source-team">{option.team.map((typeId, index) => <HeroIcon key={`${typeId}-${index}`} typeId={typeId} size="sm" />)}</span>
        <small>{option.note}</small>
      </button>
    ))}
  </div>
}

// --- Why a run stopped ---

export type StuckRule = { ruleIndex: number; name: string; actionType?: string; code: string; expected?: unknown; actual?: unknown; reason?: string; targetMiss?: TargetMiss }

// A rule skipped because its skill was ready but its target could not be chosen
// (chimera_controller.target_miss; per rule in reports, per action in the log).
export type TargetMiss = { ruleIndex?: number; rule?: string; heroTypeId?: number; skillTypeId?: number; target: string; position?: number; reach: TargetReach }

// A target of a kind the skill never takes is invalid, as the rule editor says; otherwise none was there then.
export const missKind = (miss: TargetMiss) => miss.reach === 'absent' || miss.reach === 'none' ? 'unavailable' : 'never'

export function MissNote({ miss, extra, icon }: { miss: TargetMiss; extra?: ReactNode; icon?: boolean }) {
  return <TargetNote kind={missKind(miss)} reach={miss.reach} target={miss.target} position={miss.position} extra={extra} icon={icon}
    skill={typeof miss.skillTypeId === 'number' ? <SkillIcon heroTypeId={miss.heroTypeId} skillTypeId={miss.skillTypeId} size="xs" /> : undefined} />
}

// Report findings: the rules whose target could not be chosen, with how often per run.
// Invalid targets come first; one that was just not there then is a warning.
export function targetMissFindings(lang: Lang, rules: RuleAggregate[]): { tone: 'bad' | 'warn'; text: ReactNode }[] {
  const wrongKind = (rule: RuleAggregate) => missKind(rule.targetMiss!) === 'never'
  return rules.filter((rule) => rule.ruleIndex !== null && rule.targetMisses && rule.targetMiss)
    .sort((left, right) => Number(wrongKind(right)) - Number(wrongKind(left)))
    .map((rule) => ({ tone: wrongKind(rule) ? 'bad' : 'warn', text: <>
      <b>#{rule.ruleIndex} {ruleLabel(lang, rule.rule)}</b>{' '}
      <MissNote miss={rule.targetMiss!} icon={false} extra={translate(lang, 'target.perRun', { count: rule.targetMisses })} />
    </> }))
}
export type StuckSkill = { typeId: number; slot?: number; ready: boolean; cooldown?: number; defaultCooldown?: number; validTargets: number; reserved: boolean }
export type StuckDetail = {
  reason: string
  turn?: number
  bossTurns?: number
  hydraTurns?: number
  form?: string | null
  activeHeroId?: number
  activeHeroTypeId?: number
  activeHeroFormIndex?: number
  skills?: StuckSkill[]
  rules?: StuckRule[]
  detail?: string | null
  rule?: string | null
  snapshot?: BattleSnapshot | null
}

export function stuckReasonText(lang: Lang, reason: string) {
  const key = `stuck.${reason}`
  return hasMessage(key) ? translate(lang, key) : reason
}

export function ruleLabel(lang: Lang, rule: string | null | undefined) {
  return rule ? toolText(lang, rule) : ''
}

function listText(value: unknown) {
  return Array.isArray(value) ? value.join('/') : String(value ?? '?')
}

// Why one rule of the stuck champion did not act (codes from no_decision_report).
function ruleCheckText(lang: Lang, rule: StuckRule) {
  switch (rule.code) {
    case 'hero_form_mismatch': return translate(lang, 'sim.needsChampionFormCurrent', { expected: listText(rule.expected), actual: listText(rule.actual) })
    case 'chimera_form_mismatch': {
      const expected = Array.isArray(rule.expected) ? rule.expected.map((form) => formNameByKey(lang, String(form))).join('/') : listText(rule.expected)
      const actual = formNameByKey(lang, String(rule.actual ?? '')) || '?'
      return translate(lang, 'sim.onlyInFormCurrent', { expected, actual })
    }
    case 'conditions_not_met': return translate(lang, 'sim.conditionsNotMet')
    case 'default_no_ready_skill': return translate(lang, 'sim.noSkillInItsPriority')
    case 'trial_no_action': return translate(lang, 'sim.noSafeTrialActionOr')
    case 'skill_unavailable': return translate(lang, 'sim.conditionsMetButTheSkill')
    default: return toolText(lang, rule.reason ?? rule.code)
  }
}

// One stopped run: the champion's skills at that moment and why each of its rules did not act.
export function StuckCard({ run, stuck, where, context, onJump, onLog }: {
  run: { index: number; exact?: boolean }; stuck: StuckDetail; where: string; context?: string; onJump: (index: number) => void; onLog?: () => void
}) {
  const { lang, t, swapped } = useSim()
  const heroTypeId = stuck.activeHeroTypeId
  const [showState, setShowState] = useState(false)
  return (
    <article className="stuck-card">
      <header>
        <AlertTriangle size={15} />
        <strong>{t('sim.runStoppedOn', { index: run.index, exact: Boolean(run.exact), original: originalRunLabel(lang, swapped), where })}</strong>
        {stuck.snapshot && <button className="link-button" onClick={() => setShowState((value) => !value)}>{t('sim.battleStateThen')}</button>}
        {onLog && <button className="link-button" onClick={onLog}>{t('sim.actionLog')}</button>}
      </header>
      <p className="stuck-hero"><HeroIcon typeId={heroTypeId} size="md" /><span>{t('sim.hadToActALive', { context: context ?? '', reason: stuckReasonText(lang, stuck.reason) })}</span></p>
      {stuck.rule && <p className="muted">{t('sim.rule')}{ruleLabel(lang, stuck.rule)}</p>}
      {(stuck.skills?.length ?? 0) > 0 && <div className="stuck-skills">
        {stuck.skills!.map((skill) => (
          <span key={skill.typeId} className={`stuck-skill ${skill.ready ? 'ready' : ''}`}>
            <SkillIcon heroTypeId={heroTypeId} skillTypeId={skill.typeId} cooldown={skill.cooldown} />
            <em>{skill.ready ? t('sim.ready2') : skill.cooldown ? t('sim.cooldown2', { cooldown: skill.cooldown }) : t('sim.unavailable')}</em>
            {skill.reserved && <em className="warn">{t('sim.reservedByARule')}</em>}
            {skill.ready && skill.validTargets === 0 && <em className="warn">{t('sim.noLegalTarget')}</em>}
          </span>
        ))}
      </div>}
      {showState && stuck.snapshot && <StateStrip state={stuck.snapshot} actorId={stuck.activeHeroId} />}
      {(stuck.rules?.length ?? 0) > 0 ? <ul className="stuck-rules">
        {stuck.rules!.map((rule) => (
          <li key={rule.ruleIndex}>
            <button className="link-button" onClick={() => onJump(rule.ruleIndex)}>#{rule.ruleIndex} {ruleLabel(lang, rule.name)}</button>
            {rule.code === 'target_unavailable' && rule.targetMiss ? <MissNote miss={rule.targetMiss} /> : <span>{ruleCheckText(lang, rule)}</span>}
          </li>
        ))}
      </ul> : stuck.reason === 'no_matching_rule' && <p className="muted">{t('sim.thisChampionHasNoRules')}</p>}
    </article>
  )
}

// --- Rules table ---

export type RuleAggregate = {
  ruleIndex: number | null
  rule: string
  runsUsed: number
  uses: number
  usesPerRun: number
  damageShare: number
  trialGains: Record<string, number>
  auto?: boolean
  // Decisions per run where the rule's target could not be chosen, in how many runs, and one of them.
  targetMisses?: number
  targetMissRuns?: number
  targetMiss?: TargetMiss
}

export function RulesTable({ rules, finishedRuns, withTrials, onJump }: { rules: RuleAggregate[]; finishedRuns: number; withTrials: boolean; onJump: (index: number) => void }) {
  const { lang, t } = useSim()
  return <div className="simulation-scroll">
    <table className="simulation-table rules">
      <thead><tr><th>#</th><th>{t('sim.rule2')}</th><th>{t('sim.usesPerRun')}</th><th>{t('sim.runsUsed')}</th><th>{t('sim.damageShare')}</th>{withTrials && <th>{t('sim.trialContribution')}</th>}</tr></thead>
      <tbody>
        {rules.map((rule, index) => {
          const label = rule.auto ? t('sim.noUsableRuleAutoBattle') : ruleLabel(lang, rule.rule)
          return <tr key={`${rule.ruleIndex ?? 'x'}-${index}`} className={rule.ruleIndex !== null && rule.uses === 0 ? 'unused' : rule.auto ? 'auto' : ''}>
            <td>{rule.ruleIndex ?? '—'}</td>
            <td>{rule.ruleIndex !== null ? <button className="link-button" onClick={() => onJump(rule.ruleIndex!)}>{label}</button> : label}{rule.ruleIndex !== null && rule.uses === 0 && <em className="tag warn">{t('sim.unused')}</em>}
              {rule.targetMisses && rule.targetMiss ? <TargetTag kind={missKind(rule.targetMiss)}
                label={<>{t(missKind(rule.targetMiss) === 'never' ? 'target.never' : 'target.unavailable')} · {t('target.perRun', { count: rule.targetMisses })}</>}
                notes={<MissNote miss={rule.targetMiss} />} /> : null}</td>
            <td>{rule.usesPerRun}</td>
            <td>{rule.runsUsed}/{finishedRuns}</td>
            <td><span className="cell-bar"><span style={{ width: `${Math.min(100, rule.damageShare * 100)}%` }} /><em>{percent(rule.damageShare, 1)}</em></span></td>
            {withTrials && <td>{Object.entries(rule.trialGains).filter(([, gain]) => gain >= 0.005).sort(([, a], [, b]) => b - a).map(([trial, gain]) =>
              <TrialTag key={trial} id={Number(trial)} className="trial-chip"> +{percent(gain)}</TrialTag>)}</td>}
          </tr>
        })}
      </tbody>
    </table>
  </div>
}

// --- Action log ---

export type LogRow = {
  turn: number
  actorId: number
  actorTypeId: number
  source: 'policy' | 'auto' | 'enemy'
  skillTypeId: number
  targetId: number
  damage: number
  deaths: number[]
  rule?: string | null
  ruleIndex?: number | null
  reservationReleased?: boolean
  autoDetail?: string | null
  state?: BattleSnapshot
  form?: number
  uses?: ActionUse[]
  // Rules before this one whose target could not be chosen.
  targetMisses?: TargetMiss[]
}

export type LogFilter<Row> = { key: string; label: string; test: (row: Row) => boolean }

// --- Effect filter: actions taken while someone had given buffs or debuffs ---

// Whose effects count: the acting champion's, the target's, any living ally's or enemy's.
export type EffectScope = 'actor' | 'target' | 'allies' | 'enemies'
export type EffectCondition = { group: string; scope: EffectScope }
const EFFECT_SCOPES: EffectScope[] = ['actor', 'target', 'allies', 'enemies']
const STRENGTH = /\s*(?:[（(]\s*\d+(?:[.,]\d+)?%\s*[）)]|\d+(?:[.,]\d+)?%)$/

// Strength variants of one effect ("Increase ATK (25%)", "(50%)") form one group: the name without the strength.
export function effectGroup(effects: Map<string, SimEffect>, lang: Lang, typeId: number) {
  const option = effects.get(String(typeId))
  return option ? effectName(option, lang).replace(STRENGTH, '').trim() : `#${typeId}`
}

// Buffs are looked for on the acting champion, debuffs on its target.
export function defaultScope(effects: Map<string, SimEffect>, typeId: number): EffectScope {
  return effects.get(String(typeId))?.group === '减益' ? 'target' : 'actor'
}

function effectHolders(row: LogRow, scope: EffectScope) {
  const actors = row.state?.actors ?? []
  if (scope === 'actor') return actors.filter((actor) => actor.id === row.actorId)
  if (scope === 'target') return actors.filter((actor) => actor.id === row.targetId)
  return actors.filter((actor) => actor.s === (scope === 'allies' ? 'a' : 'e') && !actor.d)
}

// The damage of the action's own skill (not ally attacks or counterattacks it set off).
function ownSkillDamage(row: LogRow) {
  const own = (row.uses ?? []).filter((use) => use.trigger === 'input' && use.actorId === row.actorId)
  return own.length ? own.reduce((total, use) => total + use.damage, 0) : row.damage
}

// --- What one action set off (tools/simulation_common.action_uses) ---

export type UseTrigger = 'input' | 'team' | 'counter' | 'provoke' | 'activate' | 'effect' | 'passive' | 'other'
// hits: the actors its damage reached, when known (a skill may pick its own target).
export type ActionUse = { actorId: number; skillTypeId: number; targetId: number; trigger: UseTrigger; damage: number; hits?: number[] }

// Who a boss's skill actually hit, when the game picked someone else than the
// command named (the Head of Mischief's steal hits the enemy with the most buffs).
// The team's own commands name their real target; an area attack hitting others is normal.
export function retargetedHits(row: LogRow) {
  if (row.source !== 'enemy') return null
  const own = (row.uses ?? []).find((use) => use.trigger === 'input' && use.actorId === row.actorId)
  return own?.hits?.length && !own.hits.includes(row.targetId) ? own.hits : null
}

// Whether a boss-side skill in the action reached one of the given champions: its damage
// hit them or, with no damage to go by, the command or trigger named them. Counterattacks
// and other boss skills set off on the team's turns count too.
export function enemyReached(row: LogRow, ids: Set<number>, byId: Map<number, ActorInfo>) {
  if (!ids.size) return false
  if (!row.uses?.length) return row.source === 'enemy' && ids.has(row.targetId)
  return row.uses.some((use) => use.actorId >= 0 && byId.get(use.actorId)?.player !== true
    && (use.hits?.length ? use.hits.some((id) => ids.has(id)) : ids.has(use.targetId)))
}

// The skill uses worth a line under the action: everything besides the action's own skill
// (the bosses' only when they act, their damage to the team is not shown), plus the own
// skill's damage when there is anything else to compare it with.
export function chainUses(uses: ActionUse[] | undefined, byId: Map<number, ActorInfo>) {
  const player = (use: ActionUse) => byId.get(use.actorId)?.player === true
  const extras = (uses ?? []).filter((use) => use.trigger !== 'input' && (
    use.trigger === 'other' ? (use.actorId < 0 || player(use)) && use.damage > 0
      : use.trigger === 'passive' ? player(use) && use.damage > 0 : true))
  if (!extras.length) return []
  return [...(uses ?? []).filter((use) => use.trigger === 'input' && player(use) && use.damage > 0), ...extras]
}

// An ally attack, counterattack, provoked attack or other skill the action set off (the filter).
export function hasChainedSkill(uses: ActionUse[] | undefined) {
  return (uses ?? []).some((use) => use.trigger !== 'input' && use.trigger !== 'other')
}

function UseChip({ use, actor, form }: { use: ActionUse; actor: ActorInfo | undefined; form?: number }) {
  const { t } = useSim()
  const player = actor?.player === true
  const label = use.trigger === 'other' && use.actorId < 0 ? t('sim.otherDamage') : t(`chain.${use.trigger}`)
  const hint = use.trigger === 'other' && use.actorId < 0
    ? t('sim.damageWithNoChampionAs')
    : t(`chain.${use.trigger}.hint`)
  return <span className={`use-chip ${use.trigger} ${actor && !player ? 'enemy' : ''}`}>
    <HoverCard content={<div className="hover-list"><strong>{label}</strong><span>{hint}</span></div>}><em>{label}</em></HoverCard>
    {actor && <ActorIcon typeId={actor.heroTypeId} player={player} size="xs" form={form} />}
    {actor && use.skillTypeId > 0 && <SkillIcon heroTypeId={actor.heroTypeId} skillTypeId={use.skillTypeId} player={player} size="xs" form={form} />}
    {(player || use.actorId < 0) && <b>{damageText(use.damage)}</b>}
  </span>
}

const PAGE_GROUPS = 8

type ActionLogProps<Row> = {
  rows: Row[] | null
  actors: ActorInfo[]
  runs: number[]
  runIndex: number
  setRunIndex: (index: number) => void
  groupOf: (row: Row) => number
  groupTitle: (key: number, rows: Row[]) => ReactNode
  filters: LogFilter<Row>[]
  enemyToggle: string
  // When a row happened, in the words the log's groups use (Chimera boss turns, Hydra turns).
  rowWhen?: (row: Row) => string
  openByDefault?: (key: number, rows: Row[]) => boolean
  extras?: (row: Row) => ReactNode
  footer?: ReactNode
  // A row under the filters (a Chimera form or Hydra head picker).
  toolbar?: ReactNode
  // Shown under the filters (the Chimera log's trial list).
  header?: ReactNode
  // Rows outside it are hidden (a picked trial's turns); counts as a filter for the summary.
  scope?: ((row: Row) => boolean) | null
  // Shown instead of the loading line when the run could not be read.
  failure?: ReactNode
  // The run stopped at a rule (the footer is its stuck card): the jump goes there.
  stopped?: boolean
  // Clears the caller's own filters (form, head, trial) with "reset all filters".
  onResetFilters?: () => void
  // The caller's filters that this run does not have (a head or form it never shows).
  unavailable?: string[]
}

// The bosses' skills (names, descriptions) come with each battle's log.
export function ActionLog<Row extends LogRow>(props: ActionLogProps<Row> & { bossSkills?: Record<string, BossSkill> }) {
  const data = useContext(SimContext)
  const { bossSkills, ...rest } = props
  const value = useMemo(() => ({
    ...data, bossSkills: new Map(Object.entries(bossSkills ?? {}).map(([key, skill]) => [Number(key), skill])),
  }), [data, bossSkills])
  return <SimContext.Provider value={value}><ActionLogView<Row> {...rest} /></SimContext.Provider>
}

function ActionLogView<Row extends LogRow>({ rows, actors, runs, runIndex, setRunIndex, groupOf, groupTitle, filters, enemyToggle, rowWhen, openByDefault, extras, footer, toolbar, header, scope,
  failure, stopped, onResetFilters, unavailable }: ActionLogProps<Row>) {
  const { lang, t, swapped, effects, heroes } = useSim()
  const [hero, setHero] = useState(0)
  const [active, setActive] = useState<Record<string, boolean>>({})
  const [showEnemy, setShowEnemy] = useState(true)
  // Kept when another run is shown, so runs compare under the same conditions.
  const [conditions, setConditions] = useState<EffectCondition[]>([])
  // Trials ask for effects together ("A and B") or as alternatives ("A, B or C").
  const [matchAny, setMatchAny] = useState(false)
  const [pages, setPages] = useState(1)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  // "Jump to the end / stopped point": every group shown, the last one open, then scrolled to.
  const [jump, setJump] = useState<{ version: number; group: number } | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { setPages(1); setExpanded(new Set()); setJump(null) }, [rows])
  useEffect(() => { if (jump) endRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }) }, [jump])
  const byId = useMemo(() => new Map(actors.map((actor) => [actor.actorId, actor])), [actors])
  // The effects anyone had in this run, by group, for the search box.
  const effectOptions = useMemo(() => {
    const found = new Map<string, { typeId: number; option?: SimEffect }>()
    for (const row of rows ?? []) {
      for (const actor of row.state?.actors ?? []) {
        for (const [typeId] of actor.fx) {
          const group = effectGroup(effects, lang, typeId)
          if (!found.has(group)) found.set(group, { typeId, option: effects.get(String(typeId)) })
        }
      }
    }
    return found
  }, [rows, effects, lang])
  const searchOptions = useMemo<SearchOption[]>(() => [...effectOptions.entries()].map(([group, { option }]) => ({
    key: group, label: group, glyph: true, icon: option?.icon, iconReady: option?.iconReady !== false,
    kind: option && EFFECT_GROUP[option.group] ? t(EFFECT_GROUP[option.group]) : option?.group ?? '',
  })).sort((left, right) => left.kind.localeCompare(right.kind, lang) || left.label.localeCompare(right.label, lang)), [effectOptions, t, lang])
  const holds = (row: LogRow, condition: EffectCondition) => effectHolders(row, condition.scope)
    .some((actor) => actor.fx.some(([typeId]) => effectGroup(effects, lang, typeId) === condition.group))
  // A picked champion's own actions and the boss actions that reached it (a champion may come twice).
  const heroIds = useMemo(() => new Set(actors.filter((actor) => actor.player && actor.heroTypeId === hero)
    .map((actor) => actor.actorId)), [actors, hero])
  const own = (row: Row) => row.source !== 'enemy' && (!hero || row.actorTypeId === hero)
  const attacked = (row: Row) => hero !== 0 && !own(row) && enemyReached(row, heroIds, byId)
  const visibleRows = useMemo(() => (rows ?? []).filter((row) =>
    (showEnemy || row.source !== 'enemy') && (!hero || row.actorTypeId === hero || enemyReached(row, heroIds, byId)) && (!scope || scope(row))
    && filters.every((filter) => !active[filter.key] || filter.test(row))
    && (!conditions.length || (row.state !== undefined
      && (matchAny ? conditions.some((condition) => holds(row, condition)) : conditions.every((condition) => holds(row, condition)))))),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [rows, hero, heroIds, byId, active, showEnemy, filters, conditions, matchAny, effects, lang, scope])
  const groups = useMemo(() => {
    const map = new Map<number, Row[]>()
    for (const row of visibleRows) map.set(groupOf(row), [...(map.get(groupOf(row)) ?? []), row])
    return [...map.entries()]
  }, [visibleRows, groupOf])
  // What the matching actions did: how often, the strongest single skill, the damage in all.
  const matched = visibleRows.filter(own)
  const strongest = matched.reduce<Row | null>((best, row) => !best || ownSkillDamage(row) > ownSkillDamage(best) ? row : best, null)
  const summary = (hero || conditions.length || scope) && rows ? {
    count: matched.length,
    best: strongest ? ownSkillDamage(strongest) : 0,
    total: matched.reduce((total, row) => total + row.damage, 0),
    attacked: hero ? visibleRows.filter(attacked).length : 0,
  } : null
  const addCondition = (group: string) => {
    const typeId = effectOptions.get(group)?.typeId
    setConditions((current) => current.some((item) => item.group === group) ? current
      : [...current, { group, scope: typeId === undefined ? 'actor' : defaultScope(effects, typeId) }])
  }
  const team = [...new Set(actors.filter((actor) => actor.player).map((actor) => actor.heroTypeId))]
  const remaining = groups.length - pages * PAGE_GROUPS
  const target = (id: number, form?: number) => {
    const actor = byId.get(id)
    if (!actor) return <span className="muted">—</span>
    return <ActorIcon typeId={actor.heroTypeId} player={actor.player} form={form} />
  }
  // Filters carried over from another run that this one cannot match (an effect nobody had, a champion not in it).
  const missing = rows ? [
    ...conditions.filter((condition) => !effectOptions.has(condition.group)).map((condition) => condition.group),
    ...(hero && actors.length && !team.includes(hero) ? [heroName(lang, heroes, hero)] : []),
    ...(unavailable ?? []),
  ] : []
  const filtered = Boolean(hero || conditions.length || !showEnemy || scope || Object.values(active).some(Boolean))
  const resetFilters = () => {
    setHero(0); setActive({}); setShowEnemy(true); setConditions([]); setMatchAny(false)
    onResetFilters?.()
  }
  const position = runs.indexOf(runIndex)
  const runLabel = (index: number) => index === 1 ? `1 ${originalRunLabel(lang, swapped)}` : String(index)
  const jumpToEnd = () => {
    setPages(Math.max(1, Math.ceil(groups.length / PAGE_GROUPS)))
    setJump((current) => ({ version: (current?.version ?? 0) + 1, group: groups.at(-1)?.[0] ?? -1 }))
  }
  return <>
    <div className="simulation-log-filters">
      <div className="run-picker">
        <button type="button" disabled={position <= 0} onClick={() => setRunIndex(runs[position - 1])}>{t('sim.previousRun')}</button>
        <label><span className="sr-only">{t('sim.runSelect')}</span>
          <select value={runIndex} aria-label={t('sim.runSelect')} onChange={(event) => setRunIndex(Number(event.target.value))}>
            {runs.map((index) => <option key={index} value={index}>{t('sim.runSelect')} {runLabel(index)}</option>)}
          </select></label>
        <button type="button" disabled={position < 0 || position >= runs.length - 1} onClick={() => setRunIndex(runs[position + 1])}>{t('sim.nextRun')}</button>
      </div>
      <div className="hero-filter">
        <button className={hero === 0 ? 'active' : ''} onClick={() => setHero(0)}>{t('sim.all')}</button>
        {team.map((typeId) => <button key={typeId} className={hero === typeId ? 'active' : ''} onClick={() => setHero(hero === typeId ? 0 : typeId)}><HeroIcon typeId={typeId} size="xs" /></button>)}
      </div>
      {filters.map((filter) => <label key={filter.key}><input type="checkbox" checked={Boolean(active[filter.key])} onChange={(event) => setActive((current) => ({ ...current, [filter.key]: event.target.checked }))} />{filter.label}</label>)}
      <label><input type="checkbox" checked={showEnemy} onChange={(event) => setShowEnemy(event.target.checked)} />{enemyToggle}</label>
    </div>
    {toolbar}
    <div className="simulation-effect-filter">
      <EffectSearch options={searchOptions} chosen={conditions.map((condition) => condition.group)} placeholder={t('sim.filterByEffect')}
        empty={t('sim.noSuchEffectInRun')} onPick={addCondition} />
      {conditions.length > 1 && <select value={matchAny ? 'any' : 'all'} aria-label={t('sim.conditionsMatch')}
        onChange={(event) => setMatchAny(event.target.value === 'any')}>
        <option value="all">{t('sim.matchAll')}</option>
        <option value="any">{t('sim.matchAny')}</option>
      </select>}
      {conditions.map((condition) => {
        const option = effectOptions.get(condition.group)?.option
        return <span key={condition.group} className={`effect-condition ${option?.group === '减益' ? 'debuff' : option?.group === '增益' ? 'buff' : ''}`}>
          <EffectGlyph icon={option?.icon} ready={option?.iconReady !== false && Boolean(option)} />
          <b>{condition.group}</b>
          <select value={condition.scope} aria-label={t('sim.effectScope')} onChange={(event) => setConditions((current) => current.map((item) =>
            item.group === condition.group ? { ...item, scope: event.target.value as EffectScope } : item))}>
            {EFFECT_SCOPES.map((scope) => <option key={scope} value={scope}>{t(`sim.effectScope.${scope}` as MessageKey)}</option>)}
          </select>
          <button type="button" className="icon-button" aria-label={t('sim.removeCondition')} onClick={() => setConditions((current) =>
            current.filter((item) => item.group !== condition.group))}><X size={13} /></button>
        </span>
      })}
      {conditions.length > 0 && <button type="button" className="link-button" onClick={() => setConditions([])}>{t('sim.clearConditions')}</button>}
    </div>
    <div className="simulation-log-jump">
      {rows && groups.length > 0 && <button type="button" className="button ghost" onClick={jumpToEnd}>{stopped ? t('sim.jumpToStuck') : t('sim.jumpToEnd')}</button>}
      {filtered && <button type="button" className="button ghost" onClick={resetFilters}>{t('sim.resetFilters')}</button>}
    </div>
    {missing.length > 0 && <p className="simulation-filter-warning" role="status">{t('sim.unavailableFilters', { filters: missing.join(translate(lang, 'common.listSeparator')) })}</p>}
    {header}
    {summary && <p className="simulation-log-summary">{t('sim.matchingActions', { count: summary.count, best: damageText(summary.best), total: damageText(summary.total) })}
      {strongest && summary.best > 0 && <span className="simulation-log-best">
        <ActorIcon typeId={strongest.actorTypeId} player form={strongest.form} size="xs" />
        <SkillIcon heroTypeId={strongest.actorTypeId} skillTypeId={strongest.skillTypeId} player form={strongest.form} size="xs" />
        {rowWhen ? rowWhen(strongest) : t('sim.strongestOnTurn', { turn: strongest.turn })}</span>}
      {hero !== 0 && showEnemy && <span className="simulation-log-attacked">{t('sim.attackedTimes', { count: summary.attacked })}</span>}
      {conditions.length > 0 && <small>{t('sim.effectsBeforeAction')}</small>}</p>}
    {!rows ? failure ?? <p className="muted">{t('sim.loading')}</p> : <div className="simulation-log">
      {groups.slice(0, pages * PAGE_GROUPS).map(([key, groupRows]) => {
        const opening = groupRows.find((row) => row.state)?.state
        return <details key={key} open={(openByDefault?.(key, groupRows) ?? false) || jump?.group === key}>
          <summary>{groupTitle(key, groupRows)}</summary>
          {opening && <><div className="log-state-label">{t('sim.beforeFirstActionState')}</div><StateStrip state={opening} /></>}
          {groupRows.map((row, index) => {
            const rowKey = `${key}-${index}`
            const hits = retargetedHits(row)
            // Shown for the picked champion when the row's own target does not say it was hit (area skills, counterattacks).
            const reached = !hits && attacked(row) && !heroIds.has(row.targetId)
              ? [...heroIds].filter((id) => enemyReached(row, new Set([id]), byId)) : []
            const open = expanded.has(rowKey)
            return <div key={rowKey} className="log-entry">
              <div className={`log-row ${row.source}`}>
                <span className="log-turn" title={rowWhen?.(row)}>{t('sim.actionTurn', { turn: row.turn })}</span>
                <span className="who"><ActorIcon typeId={row.actorTypeId} player={row.source !== 'enemy'} form={row.form} /></span>
                <span className="what"><SkillIcon heroTypeId={row.actorTypeId} skillTypeId={row.skillTypeId} player={row.source !== 'enemy'} form={row.form} /><span className="arrow">→</span>{target(hits ? hits[0] : row.targetId, row.form)}</span>
                <span className="why">{hits && <HoverCard content={<div className="hover-list"><strong>{t('sim.actualTarget')}</strong>
                    <span>{t('sim.retargetedHint')}</span><span className="hover-inline">{t('sim.commandTarget')}{target(row.targetId, row.form)}</span></div>}>
                    <em className="tag warn retargeted">{t('sim.actualTarget')}{hits.map((id) => <span key={id}>{target(id, row.form)}</span>)}</em>
                  </HoverCard>}
                  {reached.length > 0 && <em className="tag reached">{t('sim.alsoHit')}{reached.map((id) => <span key={id}>{target(id)}</span>)}</em>}
                  {row.source === 'policy' ? `#${row.ruleIndex ?? '—'} ${ruleLabel(lang, row.rule)}`
                  : row.source === 'auto' ? `${t('sim.auto')}${row.autoDetail ? toolText(lang, row.autoDetail) : t('sim.noUsableRule')}`
                  : <EnemyAction row={row} />}
                  {row.reservationReleased && <em className="tag warn">{t('sim.reservedSkillUsed')}</em>}
                  {row.targetMisses?.map((miss, missIndex) => <TargetTag key={missIndex} kind={missKind(miss)}
                    label={`#${miss.ruleIndex ?? '—'} ${t(missKind(miss) === 'never' ? 'target.never' : 'target.unavailable')}`}
                    notes={<><strong>#{miss.ruleIndex ?? '—'} {ruleLabel(lang, miss.rule)}</strong><MissNote miss={miss} /></>} />)}</span>
                <span className="dmg">{row.damage ? damageText(row.damage) : ''}</span>
                <span className="trials">{extras?.(row)}{row.deaths.filter((id) => id >= 0).map((id) => <em key={`d${id}`} className="bad death">{target(id)}{t('sim.died')}</em>)}</span>
                {row.state ? <button className="state-toggle" aria-expanded={open} onClick={() => setExpanded((current) => {
                  const next = new Set(current)
                  if (next.has(rowKey)) next.delete(rowKey); else next.add(rowKey)
                  return next
                })}>{open ? t('sim.hide') : t('sim.state')}</button> : <span />}
              </div>
              {(() => {
                const chain = chainUses(row.uses, byId)
                return chain.length > 0 && <div className="log-uses">{chain.map((use, useIndex) =>
                  <UseChip key={useIndex} use={use} actor={byId.get(use.actorId)} form={row.form} />)}</div>
              })()}
              {open && row.state && <><div className="log-state-label">{t('sim.beforeActionState')}</div>
                <StateStrip state={row.state} actorId={row.actorId} targetId={row.targetId} /></>}
            </div>
          })}
        </details>
      })}
      <div ref={endRef} />
      {remaining <= 0 && footer}
      {remaining > 0 && <button className="button ghost" onClick={() => setPages((value) => value + 1)}>{t('sim.showLaterTurnsMoreGroups', { remaining })}</button>}
      {!groups.length && <p className="muted">{t('sim.noActionsMatchTheFilters')}</p>}
    </div>}
  </>
}

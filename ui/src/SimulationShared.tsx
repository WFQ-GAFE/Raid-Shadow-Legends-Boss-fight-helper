import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, Skull, Sparkles, Users, Waves, Zap } from 'lucide-react'
import { HoverCard } from './HoverCard'
import { translateToolText, type UiLanguage } from './i18n'
import { TeamPreviewDialog, type TeamSnapshot } from './TeamPreview'

// Pieces shared by the Chimera and Hydra strategy simulation reports:
// icons with hover cards for champions, heads, skills, effects and trials,
// the per-decision battle state, the capture picker, stuck cards, the rules
// table and the action log. See tools/simulation_common.py for the data.

export type Lang = UiLanguage
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
export type SnapshotActor = { id: number; t: number; s: 'a' | 'e'; hp: number | null; fx: [number, number, number][]; d?: 1; f?: number; cd?: [number, number][]; dv?: number; neck?: 1 }
export type BattleSnapshot = { actors: SnapshotActor[] }
export type ActorInfo = { actorId: number; heroTypeId: number; player: boolean }

type SimData = {
  lang: Lang
  boss: BossKind
  heroes: SimulationHero[]
  heads: SimHead[]
  effects: Map<string, SimEffect>
  trialById: Map<number, SimulationTrial>
  // Another team was put into the saved opening: run 1 keeps only its seed.
  swapped: boolean
  // The skills the bosses used in the shown battle.
  bossSkills: Map<number, BossSkill>
}

const SimContext = createContext<SimData>({ lang: 'zh-CN', boss: 'chimera', heroes: [], heads: [], effects: new Map(), trialById: new Map(), swapped: false, bossSkills: new Map() })

export function SimProvider({ lang, boss, heroes, heads = [], effects = [], trialById = new Map(), teamSource, children }: {
  lang: Lang; boss: BossKind; heroes: SimulationHero[]; heads?: SimHead[]; effects?: SimEffect[]; trialById?: Map<number, SimulationTrial>
  teamSource?: string | null; children: ReactNode
}) {
  const effectMap = useMemo(() => new Map(effects.map((effect) => [effect.token, effect])), [effects])
  const swapped = Boolean(teamSource && teamSource !== 'battle')
  const value = useMemo(() => ({ lang, boss, heroes, heads, effects: effectMap, trialById, swapped, bossSkills: new Map<number, BossSkill>() }),
    [lang, boss, heroes, heads, effectMap, trialById, swapped])
  return <SimContext.Provider value={value}>{children}</SimContext.Provider>
}

export function useSim() {
  const data = useContext(SimContext)
  return { ...data, en: data.lang === 'en', t: (zh: string, en: string) => (data.lang === 'en' ? en : zh) }
}

export function toolText(lang: Lang, value: string) {
  return lang === 'en' ? translateToolText(value) : value
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
  return findHero(heroes, typeId)?.name ?? (typeId ? (lang === 'en' ? `Champion ${typeId}` : `英雄 ${typeId}`) : '—')
}

// Battle head type ids add a difficulty step to the catalog's base id (a multiple of 40).
export function findHead(heads: SimHead[], typeId: number | undefined) {
  if (typeof typeId !== 'number') return undefined
  return heads.find((head) => head.typeId === typeId || head.canonicalTypeId === typeId)
    ?? heads.find((head) => head.typeId === Math.floor(typeId / 40) * 40 || head.canonicalTypeId === Math.floor(typeId / 40) * 40)
}

const HEAD_NAMES_EN: Record<string, string> = {
  Support: 'Head of Decay', Ghost: 'Head of Torment', Poison: 'Head of Blight', Tank: 'Head of Suffering', Thief: 'Head of Mischief', Berserk: 'Head of Wrath',
}
const HEAD_NAMES_ZH: Record<string, string> = {
  Support: '腐朽之头', Ghost: '煎熬之头', Poison: '枯萎之头', Tank: '苦痛之头', Thief: '灾祸之头', Berserk: '愤怒之头',
}

export function headName(lang: Lang, heads: SimHead[], typeId: number | undefined) {
  const head = findHead(heads, typeId)
  if (!head) return lang === 'en' ? 'Hydra head' : '蛇头'
  if (lang === 'en') return HEAD_NAMES_EN[head.resourceKind ?? ''] ?? head.name
  // Before the game has named the heads the catalog holds "蛇头 <id> · <kind>".
  return /^蛇头\s*\d/.test(head.name) ? HEAD_NAMES_ZH[head.resourceKind ?? ''] ?? head.name : head.name
}

function skillOf(heroes: SimulationHero[], heroTypeId: number | undefined, skillTypeId: number) {
  return findHero(heroes, heroTypeId)?.skills.find((item) => item.typeId === skillTypeId)
}

export function skillName(lang: Lang, heroes: SimulationHero[], heroTypeId: number | undefined, skillTypeId: number) {
  return skillOf(heroes, heroTypeId, skillTypeId)?.name ?? (lang === 'en' ? `Skill ${skillTypeId % 100}` : `技能 ${skillTypeId % 100}`)
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
  return <HoverCard content={<strong>{t('奇美拉', 'Chimera')}{form !== undefined ? ` · ${formName(lang, form)}` : ''}</strong>}>
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
    {typeof cooldown === 'number' && cooldown >= 0 && <em>{cooldown > 0 ? t(`冷却 ${cooldown} 回合`, `Cooldown ${cooldown}`) : t('已就绪', 'Ready')}</em>}
    {skill?.defaultCooldown ? <small>{t(`默认冷却 ${skill.defaultCooldown}`, `Base cooldown ${skill.defaultCooldown}`)}</small> : null}
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

// The heads' shared mark, swallow and exposed-neck skills, until the game's static data has named them.
const BOSS_SKILL_FALLBACK: Record<number, [string, string]> = {
  260006: ['吞噬标记', 'Devour mark'],
  260007: ['吞下', 'Swallow'],
  260009: ['蛇颈技能', 'Neck skill'],
  260010: ['蛇颈技能', 'Neck skill'],
  260011: ['蛇颈技能', 'Neck skill'],
}

export function bossSkillName(lang: Lang, skills: Map<number, BossSkill>, skillTypeId: number) {
  const known = skills.get(skillTypeId)?.name
  if (known) return known
  const fallback = BOSS_SKILL_FALLBACK[skillTypeId]
  if (fallback) return lang === 'en' ? fallback[1] : fallback[0]
  // Shown next to the head or Chimera form that used it.
  return lang === 'en' ? `Skill ${skillTypeId % 100}` : `技能 ${skillTypeId % 100}`
}

// "Head of Decay", or "Chimera · Ram form".
export function bossActorLabel(lang: Lang, boss: BossKind, heads: SimHead[], typeId: number | undefined, form?: number) {
  if (boss === 'hydra') return headName(lang, heads, typeId)
  const shape = formName(lang, form)
  if (!shape) return lang === 'en' ? 'Chimera' : '奇美拉'
  return lang === 'en' ? `Chimera · ${shape} form` : `奇美拉 · ${shape}形态`
}

export function BossSkillCard({ actorTypeId, skillTypeId, cooldown, form }: { actorTypeId: number | undefined; skillTypeId: number; cooldown?: number; form?: number }) {
  const { lang, boss, heads, bossSkills, t } = useSim()
  const skill = bossSkills.get(skillTypeId)
  return <div className="hover-skill">
    <strong>{bossSkillName(lang, bossSkills, skillTypeId)}</strong>
    <span>{bossActorLabel(lang, boss, heads, actorTypeId, form)}</span>
    {typeof cooldown === 'number' && cooldown >= 0 && <em>{cooldown > 0 ? t(`冷却 ${cooldown} 回合`, `Cooldown ${cooldown}`) : t('已就绪', 'Ready')}</em>}
    {skill?.defaultCooldown ? <small>{t(`默认冷却 ${skill.defaultCooldown}`, `Base cooldown ${skill.defaultCooldown}`)}</small> : null}
    {skill?.description
      ? <p>{plainText(skill.description)}</p>
      : <small>{t('技能说明会在游戏运行时读取一次并保存，之后再打开报告即可看到。', 'The description is read once while the game is running and kept; open the report again to see it.')}</small>}
  </div>
}

// Who acted and with what, for an enemy row of the action log.
function EnemyAction({ row }: { row: LogRow }) {
  const { lang, boss, heads, bossSkills } = useSim()
  return <HoverCard content={<BossSkillCard actorTypeId={row.actorTypeId} skillTypeId={row.skillTypeId} form={row.form} />}>
    <span className="enemy-action"><b>{bossActorLabel(lang, boss, heads, row.actorTypeId, row.form)}</b> · <span className="skill-name">{bossSkillName(lang, bossSkills, row.skillTypeId)}</span></span>
  </HoverCard>
}

const EFFECT_GROUP_EN: Record<string, string> = { 增益: 'Buff', 减益: 'Debuff', 特殊: 'Special', 奇美拉: 'Chimera' }

export function EffectBadge({ effect: [typeId, turns, count] }: { effect: [number, number, number] }) {
  const { en, effects, t } = useSim()
  const option = effects.get(String(typeId))
  const label = option ? (en ? option.labelEn ?? option.label : option.label) : t(`效果 ${typeId}`, `Effect ${typeId}`)
  const group = option ? (en ? EFFECT_GROUP_EN[option.group] ?? option.group : option.group) : ''
  const card = <div className="hover-effect">
    <strong>{label}</strong>
    <span>{[group, turns > 0 ? t(`剩余 ${turns} 回合`, `${turns} turns left`) : turns === 0 ? t('本回合结束', 'Ends this turn') : t('持续', 'Lasting'),
      count > 1 ? t(`${count} 层`, `${count} stacks`) : ''].filter(Boolean).join(' · ')}</span>
  </div>
  return <HoverCard content={card}>
    <span className={`sim-effect ${option?.group === '增益' ? 'buff' : option?.group === '减益' ? 'debuff' : ''}`}>
      {option && option.iconReady !== false
        ? <img src={`/api/asset/effect/${encodeURIComponent(option.icon)}`} alt="" loading="lazy" />
        : <Sparkles size={11} />}
      {turns > 0 && <em>{turns}</em>}
      {count > 1 && <i>×{count}</i>}
    </span>
  </HoverCard>
}

// --- Chimera trials: "Ram · Trial 1 · Easy", as the game groups them ---

const FORM_ZH = ['终极', '公羊', '狮子', '毒蛇']
const FORM_EN = ['Ultimate', 'Ram', 'Lion', 'Viper']
export const FORM_INDEX: Record<string, number> = { Ultimate: 0, Ram: 1, Lion: 2, Snake: 3, Viper: 3 }
// Boss difficulties as the game names them: Chimera stages 1-6 are Easy..Ultra-Nightmare,
// Hydra stages 1-4 are Normal..Nightmare.
const BOSS_DIFFICULTY_ZH = ['简单', '普通', '困难', '地狱', '噩梦', '终极噩梦']
const BOSS_DIFFICULTY_EN = ['Easy', 'Normal', 'Hard', 'Brutal', 'Nightmare', 'Ultra-Nightmare']

export function bossDifficultyText(lang: Lang, boss: BossKind, difficulty?: number) {
  const index = typeof difficulty === 'number' ? (boss === 'hydra' ? difficulty : difficulty - 1) : -1
  return (lang === 'en' ? BOSS_DIFFICULTY_EN : BOSS_DIFFICULTY_ZH)[index]
    ?? (lang === 'en' ? `Difficulty ${difficulty ?? '?'}` : `难度 ${difficulty ?? '?'}`)
}

const LEVEL_ZH = ['简单', '普通', '困难']
const LEVEL_EN = ['Easy', 'Normal', 'Hard']
const LEVEL_INDEX: Record<string, number> = { Easy: 1, Normal: 2, Hard: 3 }
const PART_INDEX: Record<string, number> = { Wing: 1, Tail: 2, Paw: 3 }

export function formName(lang: Lang, index: number | undefined) {
  if (index === undefined || index < 0 || index > 3) return ''
  return lang === 'en' ? FORM_EN[index] : FORM_ZH[index]
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
  const en = lang === 'en'
  const pieces = [
    form !== undefined ? formName(lang, form) : '',
    part ? (en ? `Trial ${part}` : `试炼${part}`) : (en ? 'Trial' : '试炼'),
    level ? (en ? LEVEL_EN[level - 1] : LEVEL_ZH[level - 1]) : '',
  ].filter(Boolean)
  return pieces.join(' · ')
}

export function TrialCard({ id }: { id: number }) {
  const { lang, trialById, effects, en, t } = useSim()
  const trial = trialById.get(id)
  const description = plainText(trial?.description)
  const trialEffects = (trial?.effects ?? []).filter((effect) => typeof effect.id === 'number')
  return <div className="hover-trial">
    <strong>{trialShortLabel(lang, trial, id)}</strong>
    <p>{description || t('试炼内容会在游戏读取试炼目录后显示。', 'The trial text appears once the game has provided the trial catalog.')}</p>
    {trialEffects.length > 0 && <div className="hover-trial-effects">{trialEffects.map((effect) => {
      const option = effects.get(String(effect.id))
      return <span key={effect.id}>{option && option.iconReady !== false
        ? <img src={`/api/asset/effect/${encodeURIComponent(option.icon)}`} alt="" />
        : <Sparkles size={11} />}{option ? (en ? option.labelEn ?? option.label : option.label) : effect.name}</span>
    })}</div>}
  </div>
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
        <small>{actor.d ? t('阵亡', 'Dead') : actor.hp === null ? '' : `${Math.round(actor.hp)}%`}</small>
      </div>
      <div className="state-details">
        {actor.fx.length > 0 ? <div className="state-effects">{actor.fx.map((effect) => <EffectBadge key={effect[0]} effect={effect} />)}</div>
          : <span className="state-none">{t('无效果', 'No effects')}</span>}
        {actor.cd && actor.cd.length > 0 && <div className="state-skills">{actor.cd.map(([skill, cooldown]) =>
          <SkillIcon key={skill} heroTypeId={actor.t} skillTypeId={skill} cooldown={cooldown} size="xs" hideWithoutIcon />)}</div>}
        {actor.neck && <em className="tag">{t('暴露蛇颈', 'Exposed neck')}</em>}
        {typeof actor.dv === 'number' && <em className="tag warn">{t('正在吞噬', 'Devouring')}</em>}
      </div>
    </div>
  )
  return <div className="state-strip">
    <div className="state-side">{allies.map(row)}</div>
    <div className="state-side enemies">{enemies.map(row)}</div>
  </div>
}

// --- Why a forecast or simulation could not conclude, and what to do ---

export type FailureAdvice = { code: string; explain: string; action: string; explainEn: string; actionEn: string }

export function AdviceNote({ language, advice }: { language: Lang; advice?: FailureAdvice | null }) {
  if (!advice) return null
  const en = language === 'en'
  return <div className="advice-note" data-i18n-skip>
    <p><strong>{en ? 'Why: ' : '原因：'}</strong>{en ? advice.explainEn : advice.explain}</p>
    <p><strong>{en ? 'What to do: ' : '建议：'}</strong>{en ? advice.actionEn : advice.action}</p>
  </div>
}

export function adviceTitle(language: Lang, advice?: FailureAdvice | null) {
  if (!advice) return undefined
  return language === 'en' ? `${advice.explainEn}\n${advice.actionEn}` : `${advice.explain}\n${advice.action}`
}

// --- Capture picker: the team as icons ---

export type CaptureOption = { id: string; capturedAt?: string; difficulty?: number; teamHeroTypeIds: number[]; strategyName?: string | null }

export function CapturePicker({ captures, value, onChange, disabled, difficultyText }: {
  captures: CaptureOption[]; value: string; onChange: (id: string) => void; disabled?: boolean; difficultyText: (difficulty?: number) => string
}) {
  return <div className="capture-picker" role="radiogroup">
    {captures.map((item) => (
      <button key={item.id} type="button" role="radio" aria-checked={item.id === value} disabled={disabled}
        className={`capture-option ${item.id === value ? 'selected' : ''}`} onClick={() => onChange(item.id)}>
        <span className="capture-meta"><strong>{item.capturedAt?.slice(5) ?? ''}</strong><small>{difficultyText(item.difficulty)}</small></span>
        <span className="capture-team">{item.teamHeroTypeIds.map((typeId, index) => <HeroIcon key={`${typeId}-${index}`} typeId={typeId} size="sm" />)}</span>
        {item.strategyName && <em>{item.strategyName}</em>}
      </button>
    ))}
  </div>
}

// --- Team source: whose heroes and gear go into the saved opening ---

export type TeamSource = 'battle' | 'current' | 'strategy' | 'author'
export type TeamCheck = { checkedAt?: string; compared?: number; matched?: number; orderMatched?: number; rule?: string | null; area?: string | null }
export type TeamSources = {
  strategyId?: string
  // The game is open: heroes and the account's academy, building and area bonuses can be read.
  accountReadable?: boolean
  strategyTeam: number[]
  // The strategy's heroes as they are now; `bound` once the team names this account's heroes.
  current?: { heroTypeIds: number[]; bound: boolean } | null
  // The snapshot taken when the strategy group was saved.
  strategy?: { heroTypeIds: number[]; savedAt?: string; matches: boolean } | null
  // An imported strategy's author's team.
  author?: { heroTypeIds: number[]; savedAt?: string } | null
  check?: TeamCheck | null
}

export function teamSourceLabel(lang: Lang, source?: string | null) {
  const en = lang === 'en'
  switch (source) {
    case 'current': return en ? "Strategy's team now" : '策略组当前队伍'
    case 'strategy': return en ? 'Team when the strategy was saved' : '策略组保存时的队伍'
    case 'author': return en ? "The author's team" : '作者的队伍'
    case 'preparation': return en ? 'Preparation screen team' : '准备界面当前队伍'  // reports before 1.1.1's final build
    default: return en ? "The battle's own team" : '战斗原队伍'
  }
}

// The team a simulation ran with (summary.team: set-up and each hero's battle
// stats when the battle started), in the team view.
export type SimulationTeam = TeamSnapshot & { source?: string; savedAt?: string | null }

export function SimulationTeamButton({ team }: { team?: SimulationTeam | null }) {
  const { lang, heroes, t } = useSim()
  const [open, setOpen] = useState(false)
  if (!team || !(team.heroes?.length)) return null
  const saved = team.savedAt ? t(` · 保存于 ${team.savedAt}`, ` · saved ${team.savedAt}`) : ''
  return <>
    <button type="button" className="button ghost team-setup-button" onClick={() => setOpen(true)}><Users size={15} />{t('队伍配置', 'Team setup')}</button>
    <TeamPreviewDialog language={lang} snapshot={open ? team : null}
      title={t('模拟用的队伍配置', 'Team setup of this simulation')}
      description={t(`${teamSourceLabel(lang, team.source)}${saved}。属性是战斗开始时的战斗属性，已计入区域、建筑、学院等全部加成。`,
        `${teamSourceLabel(lang, team.source)}${saved}. Stats are the battle stats when the battle started, every bonus (area, buildings, academy) included.`)}
      onClose={() => setOpen(false)}
      heroName={(typeId) => heroName(lang, heroes, typeId)}
      heroAvatar={(typeId) => <HeroIcon typeId={typeId} size="md" title={false} />}
      skillName={(heroTypeId, skillTypeId) => skillName(lang, heroes, heroTypeId, skillTypeId)} />
  </>
}

export function originalRunLabel(lang: Lang, swapped: boolean) {
  return swapped ? (lang === 'en' ? 'Original seed' : '原种子') : (lang === 'en' ? 'Original' : '原战斗')
}

type TeamSourceOption = { source: TeamSource; team: number[]; ready: boolean; note: string }

export function teamSourceOptions(lang: Lang, sources: TeamSources | undefined, captureTeam: number[], strategyTeam: number[]): TeamSourceOption[] {
  const t = (zh: string, en: string) => (lang === 'en' ? en : zh)
  const hasTeam = strategyTeam.length > 0
  const gameOpen = sources?.accountReadable !== false
  const noTeam = t('当前策略组还没有设定队伍：在“策略组队伍”卡片上选择英雄', "This strategy has no team yet: choose its champions on the strategy's team card")
  const bonusesNote = t('学院、建筑和区域加成按当前账号读取', 'academy, building and area bonuses from the current account')
  const current = sources?.current
  const saved = sources?.strategy
  const author = sources?.author
  const options: TeamSourceOption[] = [
    { source: 'battle', team: captureTeam, ready: captureTeam.length > 0,
      note: t('这场战斗里的英雄和当时的装备', 'The champions of this battle with the gear they had then') },
    { source: 'current', team: strategyTeam, ready: hasTeam && Boolean(current?.bound) && gameOpen,
      note: !hasTeam ? noTeam
        : !current?.bound ? t('还没有绑定你账号里的英雄（导入的策略）：在“策略组队伍”卡片上选择英雄', "Not bound to your champions yet (imported strategy): choose them on the strategy's team card")
        : !gameOpen ? t('需要打开游戏：模拟时读取这些英雄现在的装备和账号加成', 'Open the game: the champions\' current gear and the account bonuses are read when simulating')
        : t('模拟时从游戏读取这些英雄现在的装备，以及学院、建筑和区域加成', 'The champions\' current gear and the academy, building and area bonuses, read from the game when simulating') },
    { source: 'strategy', team: saved?.heroTypeIds ?? strategyTeam, ready: hasTeam && Boolean(saved?.matches) && gameOpen,
      note: !hasTeam ? noTeam
        : !saved ? t('还没有快照：打开游戏后保存一次策略组，就会记下当时的英雄和装备', 'No snapshot yet: save the strategy once with the game open to record its champions and gear')
        : !saved.matches ? t('策略组的队伍已更改：打开游戏后重新保存一次策略组', "The strategy's team has changed: save it again with the game open")
        : !gameOpen ? t(`需要打开游戏：${bonusesNote}`, `Open the game: ${bonusesNote}`)
        : t(`保存策略组时的英雄和装备（${saved.savedAt?.slice(5, 16) ?? ''}）；${bonusesNote}`, `Champions and gear when the strategy was saved (${saved.savedAt?.slice(5, 16) ?? ''}); ${bonusesNote}`) },
  ]
  if (author) options.push({ source: 'author', team: author.heroTypeIds, ready: gameOpen,
    note: !gameOpen ? t(`需要打开游戏：${bonusesNote}`, `Open the game: ${bonusesNote}`)
      : t(`策略作者保存时的英雄和装备（${author.savedAt?.slice(5, 16) ?? ''}）；${bonusesNote}`, `The author's champions and gear when saved (${author.savedAt?.slice(5, 16) ?? ''}); ${bonusesNote}`) })
  return options
}

export function teamCheckText(lang: Lang, check?: TeamCheck | null) {
  const t = (zh: string, en: string) => (lang === 'en' ? en : zh)
  if (!check || !check.compared) return t(
    '装备数据还没有和实战核对：队伍里有打过这个 Boss 的英雄时，读取队伍（保存策略组、模拟当前队伍或打开准备界面）后会自动核对。',
    'Gear data not yet checked against a real battle: when the team has champions that fought this boss, it is checked automatically whenever the team is read (saving the strategy, simulating its current team or opening the preparation screen).')
  return t(`装备数据已和实战核对：${check.matched ?? 0}/${check.compared} 名英雄与服务器数据完全一致（${check.checkedAt?.slice(5, 16) ?? ''}）`,
    `Gear data checked against real battles: ${check.matched ?? 0}/${check.compared} champions identical to the server's (${check.checkedAt?.slice(5, 16) ?? ''})`)
}

export function TeamSourcePicker({ options, value, onChange, disabled }: {
  options: TeamSourceOption[]; value: TeamSource; onChange: (source: TeamSource) => void; disabled?: boolean
}) {
  const { lang } = useSim()
  return <div className={`team-source-picker${options.length === 4 ? ' four' : ''}`} role="radiogroup">
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

export type StuckRule = { ruleIndex: number; name: string; actionType?: string; code: string; expected?: unknown; actual?: unknown; reason?: string }
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

const STUCK_REASON: Record<string, [string, string]> = {
  no_matching_rule: ['没有匹配且可执行的规则', 'no rule matched with a usable skill'],
  rule_command_not_legal: ['规则选出的技能或目标不合法', 'the rule chose a skill or target that is not legal'],
  no_progress: ['同一回合反复决策而战斗没有推进', 'kept deciding on one turn without the battle advancing'],
  engine_rejected_command: ['游戏引擎拒绝了规则给出的指令', "the game engine rejected the rule's command"],
}

export function stuckReasonText(lang: Lang, reason: string) {
  const text = STUCK_REASON[reason]
  return text ? (lang === 'en' ? text[1] : text[0]) : reason
}

export function ruleLabel(lang: Lang, rule: string | null | undefined) {
  return rule ? toolText(lang, rule) : ''
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
        <strong>{t(`第 ${run.index} 场${run.exact ? `（${originalRunLabel(lang, swapped)}）` : ''}在${where}中断`, `Run ${run.index}${run.exact ? ` (${originalRunLabel(lang, swapped)})` : ''} stopped on ${where}`)}</strong>
        {stuck.snapshot && <button className="link-button" onClick={() => setShowState((value) => !value)}>{t('当时的战场状态', 'Battle state then')}</button>}
        {onLog && <button className="link-button" onClick={onLog}>{t('出手记录', 'Action log')}</button>}
      </header>
      <p className="stuck-hero"><HeroIcon typeId={heroTypeId} size="md" /><span>{t(
        `轮到出手${context ?? ''}：${stuckReasonText(lang, stuck.reason)}。实战接管会在这里停下等待。`,
        `had to act${context ?? ''}: ${stuckReasonText(lang, stuck.reason)}. A live takeover would stop and wait here.`)}</span></p>
      {stuck.rule && <p className="muted">{t('规则：', 'Rule: ')}{ruleLabel(lang, stuck.rule)}</p>}
      {(stuck.skills?.length ?? 0) > 0 && <div className="stuck-skills">
        {stuck.skills!.map((skill) => (
          <span key={skill.typeId} className={`stuck-skill ${skill.ready ? 'ready' : ''}`}>
            <SkillIcon heroTypeId={heroTypeId} skillTypeId={skill.typeId} cooldown={skill.cooldown} />
            <em>{skill.ready ? t('就绪', 'ready') : skill.cooldown ? t(`冷却 ${skill.cooldown}`, `cooldown ${skill.cooldown}`) : t('不可用', 'unavailable')}</em>
            {skill.reserved && <em className="warn">{t('被规则保留', 'reserved by a rule')}</em>}
            {skill.ready && skill.validTargets === 0 && <em className="warn">{t('无合法目标', 'no legal target')}</em>}
          </span>
        ))}
      </div>}
      {showState && stuck.snapshot && <StateStrip state={stuck.snapshot} actorId={stuck.activeHeroId} />}
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
}

export function RulesTable({ rules, finishedRuns, withTrials, onJump }: { rules: RuleAggregate[]; finishedRuns: number; withTrials: boolean; onJump: (index: number) => void }) {
  const { lang, t } = useSim()
  return <div className="simulation-scroll">
    <table className="simulation-table rules">
      <thead><tr><th>#</th><th>{t('规则', 'Rule')}</th><th>{t('每场使用', 'Uses per run')}</th><th>{t('用到的场次', 'Runs used')}</th><th>{t('伤害占比', 'Damage share')}</th>{withTrials && <th>{t('试炼贡献', 'Trial contribution')}</th>}</tr></thead>
      <tbody>
        {rules.map((rule, index) => {
          const label = rule.auto ? t('（无可用规则，自动战斗）', '(no usable rule, auto battle)') : ruleLabel(lang, rule.rule)
          return <tr key={`${rule.ruleIndex ?? 'x'}-${index}`} className={rule.ruleIndex !== null && rule.uses === 0 ? 'unused' : rule.auto ? 'auto' : ''}>
            <td>{rule.ruleIndex ?? '—'}</td>
            <td>{rule.ruleIndex !== null ? <button className="link-button" onClick={() => onJump(rule.ruleIndex!)}>{label}</button> : label}{rule.ruleIndex !== null && rule.uses === 0 && <em className="tag warn">{t('未使用', 'Unused')}</em>}</td>
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
}

export type LogFilter<Row> = { key: string; label: string; test: (row: Row) => boolean }

// --- What one action set off (tools/simulation_common.action_uses) ---

export type UseTrigger = 'input' | 'team' | 'counter' | 'provoke' | 'activate' | 'effect' | 'passive' | 'other'
export type ActionUse = { actorId: number; skillTypeId: number; targetId: number; trigger: UseTrigger; damage: number }

const TRIGGER_TEXT: Record<UseTrigger, { zh: string; en: string; hintZh: string; hintEn: string }> = {
  input: { zh: '本技能', en: 'Own skill', hintZh: '这次出手所用技能本身的伤害。', hintEn: 'Damage of the skill this action used.' },
  team: { zh: '组队攻击', en: 'Ally Attack', hintZh: '被组队攻击叫来一起攻击的英雄（用其默认技能）和各自的伤害。', hintEn: 'A champion called in by an Ally Attack (with their default skill) and their damage.' },
  counter: { zh: '反击', en: 'Counterattack', hintZh: '被攻击后用默认技能反击。', hintEn: 'Hit back with the default skill after being attacked.' },
  provoke: { zh: '激怒攻击', en: 'Provoked attack', hintZh: '受【激怒】影响，立即攻击施放者。', hintEn: 'Provoked into attacking the champion or head that placed it.' },
  activate: { zh: '技能触发', en: 'Activated skill', hintZh: '被其他技能或效果直接触发的技能。', hintEn: 'A skill activated by another skill or effect.' },
  effect: { zh: '效果触发', en: 'Triggered skill', hintZh: '由某个效果触发的技能。', hintEn: 'A skill triggered by an effect.' },
  passive: { zh: '被动', en: 'Passive', hintZh: '被动技能造成的伤害。', hintEn: 'Damage dealt by a passive skill.' },
  other: { zh: '效果伤害', en: 'Effect damage', hintZh: '不属于某次技能使用的伤害，例如回合开始时的持续伤害，或这名英雄施放的效果在别人出手时造成的伤害；按施放效果的英雄统计。', hintEn: 'Damage outside any skill use, such as damage over time at the start of a turn or an effect this champion placed going off during another action; counted for the champion who placed it.' },
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
  const { en, t } = useSim()
  const text = TRIGGER_TEXT[use.trigger]
  const player = actor?.player === true
  const label = use.trigger === 'other' && use.actorId < 0 ? t('其他伤害', 'Other damage') : en ? text.en : text.zh
  const hint = use.trigger === 'other' && use.actorId < 0
    ? t('找不到出手英雄的伤害，例如反伤。', 'Damage with no champion as its dealer, such as reflected damage.')
    : en ? text.hintEn : text.hintZh
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
  openByDefault?: (key: number, rows: Row[]) => boolean
  extras?: (row: Row) => ReactNode
  footer?: ReactNode
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

function ActionLogView<Row extends LogRow>({ rows, actors, runs, runIndex, setRunIndex, groupOf, groupTitle, filters, enemyToggle, openByDefault, extras, footer }: ActionLogProps<Row>) {
  const { lang, t, swapped } = useSim()
  const [hero, setHero] = useState(0)
  const [active, setActive] = useState<Record<string, boolean>>({})
  const [showEnemy, setShowEnemy] = useState(true)
  const [pages, setPages] = useState(1)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  useEffect(() => { setPages(1); setExpanded(new Set()) }, [rows])
  const byId = useMemo(() => new Map(actors.map((actor) => [actor.actorId, actor])), [actors])
  const groups = useMemo(() => {
    const visible = (rows ?? []).filter((row) =>
      (showEnemy || row.source !== 'enemy') && (!hero || row.actorTypeId === hero)
      && filters.every((filter) => !active[filter.key] || filter.test(row)))
    const map = new Map<number, Row[]>()
    for (const row of visible) map.set(groupOf(row), [...(map.get(groupOf(row)) ?? []), row])
    return [...map.entries()]
  }, [rows, hero, active, showEnemy, filters, groupOf])
  const team = [...new Set(actors.filter((actor) => actor.player).map((actor) => actor.heroTypeId))]
  const remaining = groups.length - pages * PAGE_GROUPS
  const target = (id: number, form?: number) => {
    const actor = byId.get(id)
    if (!actor) return <span className="muted">—</span>
    return <ActorIcon typeId={actor.heroTypeId} player={actor.player} form={form} />
  }
  return <>
    <div className="simulation-log-filters">
      <div className="run-picker">{runs.map((index) => (
        <button key={index} className={index === runIndex ? 'active' : ''} onClick={() => setRunIndex(index)}>{index === 1 ? `1 ${originalRunLabel(lang, swapped)}` : index}</button>
      ))}</div>
      <div className="hero-filter">
        <button className={hero === 0 ? 'active' : ''} onClick={() => setHero(0)}>{t('全部', 'All')}</button>
        {team.map((typeId) => <button key={typeId} className={hero === typeId ? 'active' : ''} onClick={() => setHero(hero === typeId ? 0 : typeId)}><HeroIcon typeId={typeId} size="xs" /></button>)}
      </div>
      {filters.map((filter) => <label key={filter.key}><input type="checkbox" checked={Boolean(active[filter.key])} onChange={(event) => setActive((current) => ({ ...current, [filter.key]: event.target.checked }))} />{filter.label}</label>)}
      <label><input type="checkbox" checked={showEnemy} onChange={(event) => setShowEnemy(event.target.checked)} />{enemyToggle}</label>
    </div>
    {!rows ? <p className="muted">{t('读取中…', 'Loading…')}</p> : <div className="simulation-log">
      {groups.slice(0, pages * PAGE_GROUPS).map(([key, groupRows]) => {
        const opening = groupRows.find((row) => row.state)?.state
        return <details key={key} open={openByDefault?.(key, groupRows) ?? false}>
          <summary>{groupTitle(key, groupRows)}</summary>
          {opening && <StateStrip state={opening} />}
          {groupRows.map((row, index) => {
            const rowKey = `${key}-${index}`
            const open = expanded.has(rowKey)
            return <div key={rowKey} className="log-entry">
              <div className={`log-row ${row.source}`}>
                <span className="who"><ActorIcon typeId={row.actorTypeId} player={row.source !== 'enemy'} form={row.form} /></span>
                <span className="what"><SkillIcon heroTypeId={row.actorTypeId} skillTypeId={row.skillTypeId} player={row.source !== 'enemy'} form={row.form} /><span className="arrow">→</span>{target(row.targetId, row.form)}</span>
                <span className="why">{row.source === 'policy' ? `#${row.ruleIndex ?? '—'} ${ruleLabel(lang, row.rule)}`
                  : row.source === 'auto' ? `${t('自动：', 'Auto: ')}${row.autoDetail ? toolText(lang, row.autoDetail) : t('无可用规则', 'no usable rule')}`
                  : <EnemyAction row={row} />}
                  {row.reservationReleased && <em className="tag warn">{t('动用保留技能', 'reserved skill used')}</em>}</span>
                <span className="dmg">{row.damage ? damageText(row.damage) : ''}</span>
                <span className="trials">{extras?.(row)}{row.deaths.filter((id) => id >= 0).map((id) => <em key={`d${id}`} className="bad death">{target(id)}{t('阵亡', 'died')}</em>)}</span>
                {row.state ? <button className="state-toggle" aria-expanded={open} onClick={() => setExpanded((current) => {
                  const next = new Set(current)
                  if (next.has(rowKey)) next.delete(rowKey); else next.add(rowKey)
                  return next
                })}>{open ? t('收起', 'Hide') : t('状态', 'State')}</button> : <span />}
              </div>
              {(() => {
                const chain = chainUses(row.uses, byId)
                return chain.length > 0 && <div className="log-uses">{chain.map((use, useIndex) =>
                  <UseChip key={useIndex} use={use} actor={byId.get(use.actorId)} form={row.form} />)}</div>
              })()}
              {open && row.state && <StateStrip state={row.state} actorId={row.actorId} targetId={row.targetId} />}
            </div>
          })}
        </details>
      })}
      {remaining <= 0 && footer}
      {remaining > 0 && <button className="button ghost" onClick={() => setPages((value) => value + 1)}>{t(`显示后面的回合（还有 ${remaining} 组）`, `Show later turns (${remaining} more groups)`)}</button>}
      {!groups.length && <p className="muted">{t('没有符合筛选条件的行动。', 'No actions match the filters.')}</p>}
    </div>}
  </>
}

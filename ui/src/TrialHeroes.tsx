import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ChevronDown, Users, X } from 'lucide-react'
import { translate, type MessageKey, type UiLanguage } from './i18n'
import { HeroAvatar } from './HeroAvatar'
import { EffectIcon, SkillImage } from './HeroProfile'
import { effectName } from './effectNames'
import { championId, effectInfo, withoutStrength, type EffectLike, type HeroData, type RosterHero } from './heroData'
import { labeler } from './heroLabels'
import { sourceTrait, trialHeroReference, type ProviderSkill, type TrialNeed, type TrialProvider, type TrialRecipe, type TrialReference,
  type TrialSource, type TrialThreshold } from './trialHeroMatch'

// The required trials' effects (or, for trials that name none, the recipe's sources)
// and the account's champions whose skills give them (trialHeroMatch.ts). Each trial
// folds away; each champion shows the full text of those skills.

const SHOWN_PROVIDERS = 6
const NEED_KEYS: Record<Exclude<TrialNeed['on'], 'any'>, MessageKey> = { boss: 'trialHeroes.onBoss', actor: 'trialHeroes.onActor', ally: 'trialHeroes.onAlly' }
// Special effects that come from passive skills (hero_data.SPECIAL_KINDS: PassiveCounterattack...).
const PASSIVE_SPECIALS = new Set(['Counterattack', 'ReflectDamage', 'BlockDebuff', 'ShareDamage'])
// The game's untranslated placeholders ("Skill 100605 name", "Skill 8000628 description").
const PLACEHOLDER = /^Skill \d+ (name|description)/
// "No buffs on the Chimera" is left out: it says nothing about which champion to bring.
const THRESHOLD_KEYS: Partial<Record<TrialThreshold, MessageKey>> = {
  minimumBossDebuffs: 'trialHeroes.minimumBossDebuffs', minimumActiveHeroBuffs: 'trialHeroes.minimumActiveHeroBuffs',
  minimumLivingAllies: 'trialHeroes.minimumLivingAllies',
}

type LoadState = { status: 'idle' | 'loading' | 'ready' | 'error'; roster: RosterHero[]; error?: string }
type Shared = { data: HeroData; effects: Map<string, EffectLike>; language: UiLanguage }

// An effect's icon and name. Kinds the game names alike (Shield, Magma Shield) take
// the catalog's own name when they stand side by side.
function effectLabel(typeId: number, others: number[], { data, effects, language }: Shared) {
  const info = effectInfo(effects, data, typeId, language)
  const option = effects.get(String(typeId))
  const clash = others.some((other) => other !== typeId && effectInfo(effects, data, other, language).label === info.label)
  return { ...info, label: clash && option ? withoutStrength(effectName(option, language)) : info.label }
}

// One per kind: strengths of one kind (Poison 5% / 2.5%: ContinuousDamage5p / 025p) read as
// one effect; other kinds stay apart even when named alike (Shield, MagmaShield) or of one
// family (Fear, True Fear).
function kinds(typeIds: number[], { data, effects, language }: Shared) {
  const seen = new Set<string>()
  return typeIds.filter((typeId) => {
    const info = effectInfo(effects, data, typeId, language)
    const native = data.statusEffects[String(typeId)]?.native
      ?? (effects.get(String(typeId)) as { nativeName?: string } | undefined)?.nativeName ?? String(typeId)
    const key = `${native.replace(/\d+p?$/, '')}|${info.groupLabel}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function EffectTag({ typeId, others = [], kind = false, ...shared }: { typeId: number; others?: number[]; kind?: boolean } & Shared) {
  const info = effectLabel(typeId, others, shared)
  const clash = others.some((other) => other !== typeId && effectInfo(shared.effects, shared.data, other, shared.language).groupLabel === info.groupLabel)
  return <span className="trial-heroes-effect" title={info.description.replace(/<[^>]+>/g, '') || undefined}>
    <EffectIcon icon={info.icon} ready={info.iconReady} />{kind && !clash ? info.groupLabel : info.label}</span>
}

// A special effect or damage basis: "Revive", "Counterattack (passive)", "Damage based on enemy max HP".
function traitLabel(trait: string, { data, language }: Shared) {
  const [group, name] = trait.split(':')
  const labels = labeler(language, data)
  if (group === 'scaling') return translate(language, 'trialHeroes.scalingDamage', { stat: labels.scaling(name) })
  return PASSIVE_SPECIALS.has(name) ? translate(language, 'trialHeroes.passiveTrait', { name: labels.special(name) }) : labels.special(name)
}

function Joined({ items, word, language }: { items: ReactNode[]; word: MessageKey; language: UiLanguage }) {
  return <>{items.map((item, index) => <Fragment key={index}>
    {index > 0 && <em className="trial-heroes-joiner">{translate(language, word)}</em>}{item}</Fragment>)}</>
}

// Effects joined by "or" (one need accepts any of them).
function EffectList({ typeIds, ...shared }: { typeIds: number[] } & Shared) {
  const shown = kinds(typeIds, shared)
  return <Joined language={shared.language} word="trialHeroes.or"
    items={shown.map((typeId) => <EffectTag key={typeId} typeId={typeId} others={shown} kind {...shared} />)} />
}

// The recipe's sources: an effect (its first variant stands for the kind) or a trait.
function SourceList({ sources, ...shared }: { sources: TrialSource[] } & Shared) {
  const firsts = sources.map((source) => (source.debuff ?? source.buff ?? [])[0]).filter((typeId): typeId is number => typeId !== undefined)
  return <Joined language={shared.language} word="trialHeroes.or" items={sources.map((source, index) => {
    const typeId = (source.debuff ?? source.buff ?? [])[0]
    return typeId !== undefined ? <EffectTag key={index} typeId={typeId} others={firsts} kind {...shared} />
      : <span key={index} className="trial-heroes-effect trait">{traitLabel(sourceTrait(source), shared)}</span>
  })} />
}

// What the trial asks for, in one place: the effects by whom they must be on, counts, what must be absent.
function TrialNeeds({ reference, ...shared }: { reference: TrialReference } & Shared) {
  const { language } = shared
  const groups = (['boss', 'actor', 'ally'] as const).map((on) => ({ on, needs: reference.needs.filter((need) => need.on === on) }))
    .filter((group) => group.needs.length > 0)
  const counts = (Object.entries(reference.thresholds) as [TrialThreshold, number][]).filter(([key]) => THRESHOLD_KEYS[key])
    .map(([key, count]) => translate(language, THRESHOLD_KEYS[key]!, { count }))
  if (!groups.length && !counts.length && !reference.forbidden.length && !reference.sourceList.length) return null
  return <div className="trial-heroes-needs">
    {groups.map(({ on, needs }) => <div key={on}><span className="trial-heroes-needs-label">{translate(language, NEED_KEYS[on])}</span>
      {needs.map((need, index) => <Fragment key={index}>
        {index > 0 && <em className="trial-heroes-joiner">{translate(language, 'trialHeroes.and')}</em>}
        <EffectList typeIds={need.effectTypeIds} {...shared} /></Fragment>)}</div>)}
    {reference.sourceList.length > 0 && <div><span className="trial-heroes-needs-label">{translate(language, 'trialHeroes.anyOf')}</span>
      <SourceList sources={reference.sourceList} {...shared} /></div>}
    {counts.map((text) => <div key={text}><span>{text}</span></div>)}
    {reference.forbidden.length > 0 && <div><span className="trial-heroes-needs-label">{translate(language, 'trialHeroes.forbidden')}</span>
      <EffectList typeIds={reference.forbidden} {...shared} /></div>}
  </div>
}

// One of a champion's skills: as the champion profile shows it, with the wanted effects it applies.
function SkillBlock({ heroTypeId, skillTypeId, applied, wanted, ...shared }: {
  heroTypeId: number; skillTypeId: number; applied: ProviderSkill[]; wanted: number[] } & Shared) {
  const { data, language } = shared
  const labels = labeler(language, data)
  const skill = data.skills[String(skillTypeId)]
  const name = skill?.name && !PLACEHOLDER.test(skill.name) ? skill.name : translate(language, 'trialHeroes.innerSkill')
  const raw = skill?.desc && !PLACEHOLDER.test(skill.desc) ? skill.desc : ''
  const description = raw.replace(/<[^>]+>/g, '').replace(/\s*\n\s*/g, '\n').trim()
  return <article className="hero-profile-skill trial-heroes-skill">
    <SkillImage typeId={heroTypeId} skillId={skillTypeId} />
    <div>
      <header>
        <strong>{name}</strong>
        {skill?.passive && <em>{translate(language, 'profile.passive')}</em>}
        {skill?.cd ? <em>{translate(language, 'profile.cooldown', { cd: skill.cd })}</em> : null}
      </header>
      {description && <p>{description}</p>}
      <span className="profile-effects">{applied.map((item) => {
        if (item.trait) return <span key={item.trait} className="profile-effect trait">{traitLabel(item.trait, shared)}</span>
        const info = effectLabel(item.effectTypeId, wanted, shared)
        const details = [
          item.turns > 0 ? translate(language, 'profile.turn', { turns: item.turns }) : '',
          item.chance !== null ? `${item.chance}%` : '',
          labels.scope(item.scope),
          item.conditional ? translate(language, 'profile.conditional') : '',
        ].filter(Boolean).join(' · ')
        return <span key={item.effectTypeId} className="profile-effect"><EffectIcon icon={info.icon} ready={info.iconReady} />{info.label}<small>{details}</small></span>
      })}</span>
    </div>
  </article>
}

// A champion once per trial, with every wanted effect its skills apply; covers: how many of the trial's needs.
type Candidate = TrialProvider & { covers: number }

function candidates(needs: TrialNeed[]): Candidate[] {
  const byHero = new Map<number, Candidate>()
  for (const need of needs) for (const provider of need.providers) {
    const entry = byHero.get(provider.hero.id) ?? { ...provider, skills: [], covers: 0 }
    entry.covers += 1
    for (const skill of provider.skills)
      if (!entry.skills.some((item) => item.skillTypeId === skill.skillTypeId && item.effectTypeId === skill.effectTypeId && item.trait === skill.trait)) entry.skills.push(skill)
    byHero.set(provider.hero.id, entry)
  }
  // Those covering more of the trial first, then the reference's order: team, outside the vaults, roster.
  const stored = (provider: Candidate) => Number(provider.hero.vault || provider.hero.reserve)
  return [...byHero.values()].sort((left, right) => right.covers - left.covers
    || Number(right.inTeam) - Number(left.inTeam) || stored(left) - stored(right))
}

function HeroRow({ provider, wanted, coversAll, ...shared }: { provider: TrialProvider; wanted: number[]; coversAll: boolean } & Shared) {
  const { data, language } = shared
  const { hero, inTeam, skills } = provider
  const name = data.heroes[String(championId(hero.typeId))]?.name ?? String(hero.typeId)
  const bySkill = new Map<number, ProviderSkill[]>()
  for (const skill of skills) {
    const group = bySkill.get(skill.skillTypeId) ?? []
    if (!group.some((item) => item.effectTypeId === skill.effectTypeId && item.trait === skill.trait)) bySkill.set(skill.skillTypeId, [...group, skill])
  }
  return <li className={`trial-hero-row${inTeam ? ' in-team' : ''}`}>
    <div className="trial-heroes-hero"><HeroAvatar typeId={hero.typeId} name={name} size="md" />
      <span><strong>{name}</strong><small>{translate(language, 'trialHeroes.heroLevel', { grade: hero.grade, level: hero.level })}</small>
        <span className="trial-heroes-tags">
          {coversAll && <em className="tag strong">{translate(language, 'trialHeroes.coversAll')}</em>}
          {inTeam && <em className="tag good">{translate(language, 'trialHeroes.inTeam')}</em>}
          {hero.vault && <em className="tag">{translate(language, 'picker.place.vault')}</em>}
          {hero.reserve && <em className="tag">{translate(language, 'picker.place.reserve')}</em>}</span></span></div>
    <div className="trial-heroes-skills">{[...bySkill.entries()].map(([skillTypeId, applied]) =>
      <SkillBlock key={skillTypeId} heroTypeId={hero.typeId} skillTypeId={skillTypeId} applied={applied} wanted={wanted} {...shared} />)}</div>
  </li>
}

function CandidateList({ needs, ...shared }: { needs: TrialNeed[] } & Shared) {
  const { language } = shared
  const [all, setAll] = useState(false)
  const list = useMemo(() => candidates(needs), [needs])
  const wanted = [...new Set(needs.flatMap((need) => need.effectTypeIds))]
  const shown = all ? list : list.slice(0, SHOWN_PROVIDERS)
  if (!list.length) return <p className="muted">{translate(language, 'trialHeroes.noProviders')}</p>
  return <>
    <ul className="trial-heroes-list">
      {shown.map((provider) => <HeroRow key={provider.hero.id} provider={provider} wanted={wanted}
        coversAll={needs.length > 1 && provider.covers === needs.length} {...shared} />)}
    </ul>
    {list.length > SHOWN_PROVIDERS && <button type="button" className="link-button trial-heroes-more" onClick={() => setAll((value) => !value)}>
      <ChevronDown size={14} className={all ? 'open' : undefined} />
      {all ? translate(language, 'trialHeroes.showFewer') : translate(language, 'trialHeroes.showAll', { count: list.length })}</button>}
  </>
}

function TrialBlock({ reference, title, open, onToggle, ...shared }: {
  reference: TrialReference; title: ReactNode; open: boolean; onToggle: () => void } & Shared) {
  const { language } = shared
  const champions = new Set([...reference.needs, ...(reference.sources ? [reference.sources] : [])]
    .flatMap((need) => need.providers.map((provider) => provider.hero.id))).size
  return <section className={`trial-heroes-trial${open ? '' : ' collapsed'}`}>
    <button type="button" className="trial-heroes-trial-head" aria-expanded={open} onClick={onToggle}>
      <ChevronDown size={17} />
      <span className="trial-heroes-trial-title">{title}</span>
      {champions > 0 && <span className="trial-heroes-trial-count">{translate(language, 'trialHeroes.count', { count: champions })}</span>}
    </button>
    {open && <div className="trial-heroes-trial-body">
      <TrialNeeds reference={reference} {...shared} />
      {!reference.recipeFound ? <p className="muted">{translate(language, 'trialHeroes.noRecipe')}</p>
        : reference.needs.length || reference.sources ? <CandidateList needs={reference.needs.length ? reference.needs : [reference.sources!]} {...shared} />
        : <p className="muted">{translate(language, reference.thresholds.minimumBossDebuffs !== undefined
          || reference.thresholds.minimumActiveHeroBuffs !== undefined ? 'trialHeroes.noEffects' : 'trialHeroes.noTraits')}</p>}
    </div>}
  </section>
}

export function TrialHeroesDialog({ open, onOpenChange, language, trialIds, trialTitle, recipes, data, effects, team, loadRoster }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  language: UiLanguage
  trialIds: number[]
  trialTitle: (trialId: number) => ReactNode
  recipes: TrialRecipe[]
  data: HeroData | null
  effects: EffectLike[]
  team: { heroIds: number[]; typeIds: number[] }
  loadRoster: () => Promise<RosterHero[]>
}) {
  const [state, setState] = useState<LoadState>({ status: 'idle', roster: [] })
  // Trials folded away in this window; all open each time it opens.
  const [folded, setFolded] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!open) return
    let current = true
    setFolded(new Set())
    setState((previous) => ({ ...previous, status: 'loading', error: undefined }))
    loadRoster().then((roster) => { if (current) setState({ status: 'ready', roster }) })
      .catch((reason) => { if (current) setState({ status: 'error', roster: [], error: reason instanceof Error ? reason.message : String(reason) }) })
    return () => { current = false }
  // The roster is read once each time the dialog opens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const effectMap = useMemo(() => new Map(effects.map((effect) => [effect.token, effect])), [effects])
  const references = useMemo(() => data && state.status === 'ready'
    ? trialHeroReference(trialIds, recipes, data, state.roster, team) : [], [data, state, trialIds, recipes, team])
  const allFolded = references.length > 0 && references.every((reference) => folded.has(reference.trialId))
  const toggle = (trialId: number) => setFolded((previous) => {
    const next = new Set(previous)
    if (!next.delete(trialId)) next.add(trialId)
    return next
  })
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog-content trial-heroes-dialog" aria-describedby={undefined}>
        <div className="dialog-heading">
          <Dialog.Title><Users size={17} /> {translate(language, 'trialHeroes.title')}</Dialog.Title>
          <span className="trial-heroes-heading-actions">
            {references.length > 1 && <button type="button" className="button ghost"
              onClick={() => setFolded(allFolded ? new Set() : new Set(references.map((reference) => reference.trialId)))}>
              <ChevronDown size={15} className={allFolded ? undefined : 'open'} />
              {translate(language, allFolded ? 'trialHeroes.expandAll' : 'trialHeroes.collapseAll')}</button>}
            <Dialog.Close className="icon-button" aria-label={translate(language, 'app.close')}><X size={19} /></Dialog.Close>
          </span>
        </div>
        <div className="trial-heroes-body">
          {!trialIds.length ? <p className="muted">{translate(language, 'trialHeroes.noTrials')}</p>
            : !data ? <p className="muted">{translate(language, 'trialHeroes.noHeroData')}</p>
            : state.status === 'error' ? <p className="simulation-warning" role="alert">{state.error}</p>
            : state.status !== 'ready' ? <p className="muted">{translate(language, 'trialHeroes.loading')}</p>
            : references.map((reference) => <TrialBlock key={reference.trialId} reference={reference} title={trialTitle(reference.trialId)}
              open={!folded.has(reference.trialId)} onToggle={() => toggle(reference.trialId)} data={data} effects={effectMap} language={language} />)}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}

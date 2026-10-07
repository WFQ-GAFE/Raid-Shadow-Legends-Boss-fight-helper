import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowLeft, ArrowRight, Crown, Info, Search, Users, X } from 'lucide-react'
import { backendText, translate, type Locale, type MessageKey, type UiLanguage, useI18n } from './i18n'
import { championId, championTags, effectInfo, type EffectLike, type HeroData, type RosterHero } from './heroData'
import { SCALING_KEYS, SPECIAL_KEYS, STAT_ORDER, auraApplies, auraText, labeler } from './heroLabels'
import { EffectGlyph, EffectSearch, type SearchOption } from './EffectSearch'
import { HeroProfile } from './HeroProfile'
import { compactSlots } from './pickerUi'

// Choosing a strategy group's team from the account's champions without the
// preparation screen (agent roster request: basic data only; gear is read when
// the strategy is saved or simulated). Champions are clicked or dragged into
// the team; the team's order is the battle order and slot 1 is the leader.

export type { RosterHero } from './heroData'
export type ChosenTeam = { heroTypeIds: number[]; heroInstanceIds: number[] }

type Place = 'champions' | 'vault' | 'reserve'
type Slots = (RosterHero | null)[]
type DragSource = { kind: 'roster'; hero: RosterHero } | { kind: 'slot'; index: number }
// HeroType.Rarity: 1 Common .. 6 Mythical.
const RARITIES: [number, MessageKey][] = [
  [6, 'hero.rarity.Mythical'], [5, 'hero.rarity.Legendary'], [4, 'hero.rarity.Epic'],
  [3, 'hero.rarity.Rare'], [2, 'hero.rarity.Uncommon'], [1, 'hero.rarity.Common'],
]
const PLACES: [Place, MessageKey][] = [['champions', 'picker.place.champions'], ['vault', 'picker.place.vault'], ['reserve', 'picker.place.reserve']]
const ELEMENTS = ['Magic', 'Force', 'Spirit', 'Void']
const ROLES = ['Attack', 'Defense', 'Health', 'Support']
const placeOf = (hero: RosterHero): Place => (hero.reserve ? 'reserve' : hero.vault ? 'vault' : 'champions')
// Cards are added a page at a time as the list scrolls, so a large roster opens
// and filters without building hundreds of cards at once.
const PAGE = 60

function toggled<T>(values: Set<T>, value: T) {
  const next = new Set(values)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

function rarityName(locale: Locale, rarity: number) {
  const entry = RARITIES.find(([value]) => value === rarity)
  return entry ? translate(locale, entry[1]) : ''
}

function PickerAvatar({ hero, heroAvatar }: { hero: RosterHero; heroAvatar: (typeId: number) => ReactNode }) {
  return <span className={`picker-avatar rarity-${hero.rarity}`}>{heroAvatar(hero.typeId)}</span>
}

// Only cards whose own state changed render again when a champion is chosen.
// replaceable: not in the team, which is full (or holds another copy of the champion):
// the card stays readable, since dragging it onto a team slot replaces that slot.
const RosterCard = memo(function RosterCard({ language, hero, name, element, slot, replaceable, duplicatePosition, heroAvatar, onClick, onOpen, onDragStart, onDragEnd }: {
  language: UiLanguage
  hero: RosterHero
  name: string
  element?: string
  slot?: number
  replaceable: boolean
  duplicatePosition?: number
  heroAvatar: (typeId: number) => ReactNode
  onClick: (hero: RosterHero, event: MouseEvent) => void
  onOpen: (hero: RosterHero, fromDoubleClick: boolean) => void
  onDragStart: (event: DragEvent, source: DragSource) => void
  onDragEnd: () => void
}) {
  const { t } = useI18n()
  const place = placeOf(hero)
  return (
    <div className={`picker-card${slot ? ' active' : ''}${replaceable ? ' replaceable' : ''}${duplicatePosition ? ' duplicate' : ''}`} draggable
      onDragStart={(event) => onDragStart(event, { kind: 'roster', hero })} onDragEnd={onDragEnd}>
      <button type="button" className={`head-type-option ${slot ? 'active' : ''}`} onClick={(event) => onClick(hero, event)}
        onDoubleClick={() => onOpen(hero, true)}
        aria-pressed={Boolean(slot)}
        title={slot ? t('picker.clickToRemoveDoubleClick') : t('picker.clickToAddOrDrag')}>
        <PickerAvatar hero={hero} heroAvatar={heroAvatar} />
        <span><strong>{element && <i className={`element-dot element-${element}`} />}{name}</strong>
          <small>{`${hero.grade}★ · ${rarityName(language, hero.rarity)} · ${t('picker.lv', { level: hero.level })}${hero.empower ? ` · +${hero.empower}` : ''}`}</small>
          <small>{hero.power ? t('picker.power', { power: Math.round(hero.power) }) : ''}
            {place !== 'champions' && <b className="team-picker-place"> · {t(place === 'vault' ? 'picker.place.vault' : 'picker.place.reserve')}</b>}</small></span>
        {slot && <em>{slot}</em>}
      </button>
      {duplicatePosition && <small className="picker-duplicate-note">{t('picker.duplicateConflict', { position: duplicatePosition })}</small>}
      <button type="button" className="picker-card-info" aria-label={t('picker.profile', { name })} title={t('picker.profile2')}
        onClick={() => onOpen(hero, false)}><Info size={14} /></button>
    </div>
  )
})

export function TeamPicker({ language, open, teamSize, bossMode, initial, loadRoster, loadHeroData, effects, heroName, heroAvatar, onClose, onApply }: {
  language: UiLanguage
  open: boolean
  teamSize: number
  bossMode: 'chimera' | 'hydra'
  initial: ChosenTeam
  loadRoster: () => Promise<RosterHero[]>
  loadHeroData: () => Promise<HeroData | null>
  effects: EffectLike[]
  heroName: (typeId: number) => string
  heroAvatar: (typeId: number) => ReactNode
  onClose: () => void
  onApply: (team: ChosenTeam) => void
}) {
  const { t } = useI18n()
  const [roster, setRoster] = useState<RosterHero[] | null>(null)
  const [error, setError] = useState('')
  const [slots, setSlots] = useState<Slots>(() => Array(teamSize).fill(null))
  const [query, setQuery] = useState('')
  const [grades, setGrades] = useState<Set<number>>(new Set([6, 5]))
  const [rarities, setRarities] = useState<Set<number>>(new Set())
  const [places, setPlaces] = useState<Set<Place>>(new Set(['champions']))
  const [elements, setElements] = useState<Set<string>>(new Set())
  const [roles, setRoles] = useState<Set<string>>(new Set())
  const [factions, setFactions] = useState<Set<string>>(new Set())
  // Skill conditions, all of which a champion must meet: "scaling:DEF",
  // "aura:Speed", "aoe", "special:Heal", "buff:<group>", "debuff:<group>".
  const [conditions, setConditions] = useState<string[]>([])
  const [heroData, setHeroData] = useState<HeroData | null>(null)
  const [dataState, setDataState] = useState<'loading' | 'ready' | 'unavailable'>('loading')
  const [profile, setProfile] = useState<RosterHero | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  const [dragging, setDragging] = useState<DragSource['kind'] | null>(null)
  const [hint, setHint] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const listRef = useRef<HTMLDivElement | null>(null)
  const moreRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragSource | null>(null)
  const slotsRef = useRef(slots)
  slotsRef.current = slots
  const updateSlots = useCallback((next: Slots | ((current: Slots) => Slots)) => {
    const compact = compactSlots(typeof next === 'function' ? next(slotsRef.current) : next, teamSize)
    slotsRef.current = compact
    setSlots(compact)
  }, [teamSize])
  const clickSnapshot = useRef<{ heroId: number; slots: Slots } | null>(null)
  // Typing stays responsive; the list follows once the input has updated.
  const search = useDeferredValue(query)
  const labels = useMemo(() => labeler(language, heroData), [language, heroData])
  const effectMap = useMemo(() => new Map(effects.map((effect) => [effect.token, effect])), [effects])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setRoster(null)
    setError('')
    setQuery('')
    setProfile(null)
    setHint('')
    setDataState('loading')
    setHeroData(null)
    updateSlots(Array(teamSize).fill(null))
    loadRoster().then((heroes) => {
      if (cancelled) return
      setRoster(heroes)
      const byId = new Map(heroes.map((hero) => [hero.id, hero]))
      const chosen = initial.heroInstanceIds.map((id) => byId.get(id) ?? null).slice(0, teamSize)
      updateSlots(chosen)
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason))
    })
    loadHeroData().then((data) => {
      if (cancelled) return
      setHeroData(data)
      setDataState(data ? 'ready' : 'unavailable')
    }).catch(() => { if (!cancelled) setDataState('unavailable') })
    return () => { cancelled = true }
    // The roster is read afresh each time the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!hint) return
    const timer = setTimeout(() => setHint(''), 3500)
    return () => clearTimeout(timer)
  }, [hint])

  // Each champion's name and affinity are looked up once per roster.
  const names = useMemo(() => new Map((roster ?? []).map((hero) => [hero.typeId, heroName(hero.typeId)])), [roster, heroName])
  const nameOf = (typeId: number) => names.get(typeId) ?? heroName(typeId)
  const elementOf = useMemo(() => new Map((roster ?? []).map((hero) =>
    [hero.typeId, heroData?.heroes[String(championId(hero.typeId))]?.forms[0]?.element])), [roster, heroData])

  // Buff and debuff search options: one per effect, strengths together.
  const effectOptions = useMemo(() => {
    if (!heroData) return null
    const groups = { buff: new Map<string, { key: string; label: string; icon?: string; iconReady: boolean; ids: number[] }>(),
      debuff: new Map<string, { key: string; label: string; icon?: string; iconReady: boolean; ids: number[] }>() }
    for (const skill of Object.values(heroData.skills)) {
      for (const kind of ['buff', 'debuff'] as const) {
        for (const [typeId] of (kind === 'buff' ? skill.buffs : skill.debuffs) ?? []) {
          const info = effectInfo(effectMap, heroData, typeId, language)
          const group = groups[kind].get(info.group)
          if (group) { if (!group.ids.includes(typeId)) group.ids.push(typeId) }
          else groups[kind].set(info.group, { key: info.group, label: info.groupLabel, icon: info.icon, iconReady: info.iconReady, ids: [typeId] })
        }
      }
    }
    const sorted = (map: typeof groups.buff) => [...map.values()].sort((left, right) => left.label.localeCompare(right.label, language))
    const special = new Set(Object.values(heroData.skills).flatMap((skill) => skill.special ?? []))
    const factions = [...new Set(Object.values(heroData.heroes).map((hero) => hero.faction))].filter(Boolean).sort()
    return { buff: sorted(groups.buff), debuff: sorted(groups.debuff),
      special: SPECIAL_KEYS.filter((name) => special.has(name)), factions }
  }, [heroData, effectMap, language])

  const conditionLabel = useCallback((condition: string) => {
    const [kind, value] = [condition.slice(0, condition.indexOf(':') < 0 ? condition.length : condition.indexOf(':')), condition.slice(condition.indexOf(':') + 1)]
    if (condition === 'aoe') return t('picker.hitsAllEnemies')
    if (kind === 'scaling') return t('picker.damageBasedOn', { value: labels.scaling(value) })
    if (kind === 'aura') return t('picker.aura', { value: labels.stat(value) })
    if (kind === 'special') return labels.special(value)
    const group = effectOptions?.[kind as 'buff' | 'debuff']?.find((item) => item.key === value)
    return group ? `${kind === 'buff' ? t('picker.buff') : t('picker.debuff')}：${group.label}` : value
  }, [labels, effectOptions, t])

  const searchOptions = useMemo<SearchOption[]>(() => {
    if (!effectOptions) return []
    return [
      ...effectOptions.buff.map((option) => ({ key: `buff:${option.key}`, label: option.label, kind: t('picker.buff'), icon: option.icon, iconReady: option.iconReady, glyph: true })),
      ...effectOptions.debuff.map((option) => ({ key: `debuff:${option.key}`, label: option.label, kind: t('picker.debuff'), icon: option.icon, iconReady: option.iconReady, glyph: true })),
      ...effectOptions.special.map((name) => ({ key: `special:${name}`, label: labels.special(name), kind: t('picker.effect') })),
      { key: 'aoe', label: t('picker.hitsAllEnemies'), kind: t('chimeraSim.damage') },
      ...SCALING_KEYS.map((value) => ({ key: `scaling:${value}`, label: t('picker.damageBasedOn', { value: labels.scaling(value) }), kind: t('chimeraSim.damage') })),
      ...STAT_ORDER.slice(0, 7).map((value) => ({ key: `aura:${value}`, label: t('picker.aura', { value: labels.stat(value) }), kind: t('picker.leaderAura') })),
    ]
  }, [effectOptions, labels, t])

  const tests = useMemo(() => conditions.map((condition) => {
    const kind = condition.split(':')[0]
    if (kind === 'buff' || kind === 'debuff') {
      const ids = effectOptions?.[kind].find((item) => item.key === condition.slice(kind.length + 1))?.ids ?? []
      return (tags: Set<string>) => ids.some((id) => tags.has(`${kind}:${id}`))
    }
    return (tags: Set<string>) => tags.has(condition)
  }), [conditions, effectOptions])

  const shown = useMemo(() => {
    const text = search.trim().toLowerCase()
    const usesData = heroData && (elements.size || roles.size || factions.size || tests.length)
    return (roster ?? []).filter((hero) => {
      if ((grades.size && !grades.has(hero.grade)) || (rarities.size && !rarities.has(hero.rarity)) || !places.has(placeOf(hero))) return false
      if (text && !(names.get(hero.typeId) ?? '').toLowerCase().includes(text)) return false
      if (!usesData) return true
      const tags = championTags(heroData!, hero.typeId)
      if (elements.size && ![...elements].some((value) => tags.has(`element:${value}`))) return false
      if (roles.size && ![...roles].some((value) => tags.has(`role:${value}`))) return false
      if (factions.size && ![...factions].some((value) => tags.has(`faction:${value}`))) return false
      return tests.every((test) => test(tags))
    })
  }, [roster, search, grades, rarities, places, names, heroData, elements, roles, factions, tests])

  // New filters start again from the top of the list.
  useEffect(() => {
    setLimit(PAGE)
    if (listRef.current) listRef.current.scrollTop = 0
  }, [roster, search, grades, rarities, places, elements, roles, factions, tests])

  // Next page when the end of the list comes near. The observer is made again
  // after each page, so a window tall enough to show the end keeps filling.
  useEffect(() => {
    const sentinel = moreRef.current
    if (!sentinel || limit >= shown.length) return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setLimit((current) => current + PAGE)
    }, { root: listRef.current, rootMargin: '0px 0px 480px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [limit, shown.length, roster])

  const slotOf = new Map(slots.flatMap((hero, index) => (hero ? [[hero.id, index + 1] as const] : [])))
  const chosenChampions = new Set(slots.flatMap((hero) => (hero ? [championId(hero.typeId)] : [])))
  const chosenCount = slotOf.size
  const full = chosenCount >= teamSize

  // Puts a champion in a slot (replacing whoever is there). A champion already
  // in the team moves there; the same champion twice is refused.
  const place = useCallback((index: number, hero: RosterHero) => {
    const current = slotsRef.current
    const next = [...current]
    const existing = next.findIndex((item) => item?.id === hero.id)
    if (existing >= 0) {
      [next[existing], next[index]] = [next[index], next[existing]]
    } else {
      if (next.some((item, at) => at !== index && item && championId(item.typeId) === championId(hero.typeId))) {
        setHint(t('picker.thisChampionIsAlreadyIn'))
        return
      }
      next[index] = hero
    }
    updateSlots(next)
  }, [t, updateSlots])

  const toggle = useCallback((hero: RosterHero) => {
    const current = slotsRef.current
    const at = current.findIndex((item) => item?.id === hero.id)
    if (at >= 0) {
      updateSlots(current.map((item, index) => (index === at ? null : item)))
      return
    }
    const empty = current.findIndex((item) => !item)
    // A full team: dragging a champion onto a slot replaces it.
    if (empty < 0) {
      setHint(t('picker.theTeamIsFullDrag'))
      return
    }
    place(empty, hero)
  }, [place, updateSlots, t])

  // A double click opens the profile and is not a pick: its first click is undone.
  const clickCard = useCallback((hero: RosterHero, event: MouseEvent) => {
    if (event.detail > 1) return
    clickSnapshot.current = { heroId: hero.id, slots: slotsRef.current }
    toggle(hero)
  }, [toggle])
  const openProfile = useCallback((hero: RosterHero, fromDoubleClick: boolean) => {
    const snapshot = clickSnapshot.current
    if (fromDoubleClick && snapshot?.heroId === hero.id) updateSlots(snapshot.slots)
    clickSnapshot.current = null
    setProfile(hero)
  }, [updateSlots])

  // The drag shows once it has really begun: a listener after this one may
  // still cancel it, and a cancelled drag never sends dragend.
  const startDrag = useCallback((event: DragEvent, source: DragSource) => {
    dragRef.current = source
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', source.kind === 'roster' ? `champion:${source.hero.id}` : `slot:${source.index}`)
    const native = event.nativeEvent
    setTimeout(() => {
      if (dragRef.current !== source) return
      if (native.defaultPrevented) dragRef.current = null
      else setDragging(source.kind)
    })
  }, [])
  const endDrag = useCallback(() => {
    dragRef.current = null
    setDragging(null)
    setDropIndex(null)
  }, [])
  const overSlot = (event: DragEvent, index: number) => {
    if (!dragRef.current) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    if (dropIndex !== index) setDropIndex(index)
  }
  const dropOnSlot = (event: DragEvent, index: number) => {
    event.preventDefault()
    const source = dragRef.current
    endDrag()
    if (!source) return
    if (source.kind === 'roster') place(index, source.hero)
    else if (source.index !== index) {
      const next = [...slotsRef.current]
      ;[next[source.index], next[index]] = [next[index], next[source.index]]
      updateSlots(next)
    }
  }
  const move = (index: number, step: number) => {
    const target = index + step
    if (target < 0 || target >= chosenCount) return
    const next = [...slots]
    ;[next[index], next[target]] = [next[target], next[index]]
    updateSlots(next)
  }

  const leader = slots[0]
  const leaderAuras = leader && heroData ? heroData.heroes[String(championId(leader.typeId))]?.aura ?? [] : []
  const visible = shown.length > limit ? shown.slice(0, limit) : shown
  const team = slots.filter((hero): hero is RosterHero => Boolean(hero))
  const needsData = dataState !== 'ready'
  const dataNote = dataState === 'loading' ? t('picker.readingChampionData')
    : t('picker.championDataUnavailableOpenThe')
  const toggleCondition = (condition: string) => setConditions((current) =>
    current.includes(condition) ? current.filter((item) => item !== condition) : [...current, condition])
  const chip = (active: boolean, label: ReactNode, onClick: () => void, key: string, extra = '') =>
    <button type="button" key={key} aria-pressed={active} className={`chip ${extra} ${active ? 'active' : ''}`} onClick={onClick}>{label}</button>
  const resetFilters = () => {
    setQuery(''); setGrades(new Set()); setRarities(new Set()); setPlaces(new Set(PLACES.map(([value]) => value)))
    setConditions([]); setElements(new Set()); setRoles(new Set()); setFactions(new Set())
  }
  const filterGroups = [
    { key: 'grades', label: t('picker.stars'), all: false, entries: [...grades].map((value) => ({ key: String(value), label: `${value}★`, remove: () => setGrades((current) => toggled(current, value)) })) },
    { key: 'rarities', label: t('picker.rarity'), all: false, entries: [...rarities].map((value) => ({ key: String(value), label: rarityName(language, value), remove: () => setRarities((current) => toggled(current, value)) })) },
    { key: 'places', label: t('picker.location'), all: false, entries: places.size === PLACES.length ? [] : PLACES.filter(([value]) => places.has(value)).map(([value, key]) => ({ key: value, label: t(key), remove: () => setPlaces((current) => toggled(current, value)) })) },
    { key: 'name', label: t('picker.nameFilter'), all: false, entries: query.trim() ? [{ key: 'query', label: query.trim(), remove: () => setQuery('') }] : [] },
    { key: 'elements', label: t('picker.affinity'), all: false, entries: [...elements].map((value) => ({ key: value, label: labels.element(value), remove: () => setElements((current) => toggled(current, value)) })) },
    { key: 'roles', label: t('picker.role'), all: false, entries: [...roles].map((value) => ({ key: value, label: labels.role(value), remove: () => setRoles((current) => toggled(current, value)) })) },
    { key: 'factions', label: t('picker.faction'), all: false, entries: [...factions].map((value) => ({ key: value, label: labels.faction(value), remove: () => setFactions((current) => toggled(current, value)) })) },
    { key: 'conditions', label: t('picker.skillConditions'), all: true, entries: conditions.map((value) => ({ key: value, label: conditionLabel(value), remove: () => toggleCondition(value) })) },
  ].filter((group) => group.entries.length)

  return (
    <Dialog.Root open={open} onOpenChange={(value) => { if (!value) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content team-picker-dialog" data-i18n-skip>
          <div className="dialog-heading">
            <span><Dialog.Title><Users size={17} /> {t('picker.chooseTheStrategySTeam')}</Dialog.Title>
              <Dialog.Description>{t('picker.clickOrDragChampionsInto', { teamSize })}</Dialog.Description></span>
            <Dialog.Close className="icon-button" aria-label={t('picker.close')}><X size={19} /></Dialog.Close>
          </div>

          <div className="picker-team">
            <div className={`picker-slots picker-slots-${teamSize}`}>
              {slots.map((hero, index) => (
                <div key={hero ? `hero-${hero.id}` : `empty-${index}`}
                  className={`picker-slot${hero ? '' : ' empty'}${index === 0 ? ' leader' : ''}${dropIndex === index ? ' drop-target' : ''}${dragging ? ' dragging' : ''}`}
                  draggable={Boolean(hero)} onDragStart={(event) => startDrag(event, { kind: 'slot', index })} onDragEnd={endDrag}
                  onDragOver={(event) => overSlot(event, index)} onDragLeave={() => setDropIndex((current) => (current === index ? null : current))}
                  onDrop={(event) => dropOnSlot(event, index)}
                  title={hero ? t('picker.dragToReorderDoubleClick') : t('picker.dropAChampionHere')}>
                  <span className="picker-slot-index">{index + 1}</span>
                  {index === 0 && <span className="picker-slot-leader"><Crown size={11} />{t('picker.leader')}</span>}
                  {hero ? <>
                    <button type="button" className="picker-slot-profile" onClick={() => setProfile(hero)}
                      aria-label={t('picker.openPositionProfile', { name: nameOf(hero.typeId), position: index + 1 })}>
                      <PickerAvatar hero={hero} heroAvatar={heroAvatar} />
                      <small>{nameOf(hero.typeId)}</small>
                    </button>
                    <span className="picker-slot-actions">
                      <button type="button" title={t('picker.moveLeft')} aria-label={t('picker.moveChampionLeft', { name: nameOf(hero.typeId), position: index + 1 })} disabled={index === 0} onClick={() => move(index, -1)}><ArrowLeft size={13} /></button>
                      <button type="button" title={t('picker.remove')} aria-label={t('picker.removeChampion', { name: nameOf(hero.typeId), position: index + 1 })} onClick={() => toggle(hero)}><X size={13} /></button>
                      <button type="button" title={t('picker.moveRight')} aria-label={t('picker.moveChampionRight', { name: nameOf(hero.typeId), position: index + 1 })} disabled={index === chosenCount - 1} onClick={() => move(index, 1)}><ArrowRight size={13} /></button>
                    </span>
                  </> : <span className="picker-slot-empty">{t('picker.dropHere')}</span>}
                </div>
              ))}
            </div>
            <p className={`picker-aura${leaderAuras.some((aura) => !auraApplies(aura, bossMode)) ? ' inactive' : ''}`}>
              <Crown size={13} />
              {!leader ? t('picker.slotIsTheLeaderIts')
                : needsData ? dataNote
                  : leaderAuras.length ? leaderAuras.map((aura) => `${auraText(aura, labels, language)}${auraApplies(aura, bossMode) ? ''
                    : t('picker.noEffectAgainstThe', { boss: bossMode })}`).join(t('picker.text'))
                    : t('picker.hasNoLeaderAura', { typeId: nameOf(leader.typeId) })}
            </p>
          </div>

          <div className="picker-body">
            <aside className="picker-sidebar">
              <section><h4>{t('picker.stars')}</h4><div className="chip-group">
                {chip(grades.size === 0, t('sim.all'), () => setGrades(new Set()), 'all')}
                {[6, 5, 4, 3, 2, 1].map((grade) => chip(grades.has(grade), `${grade}★`, () => setGrades((current) => toggled(current, grade)), String(grade)))}
              </div></section>
              <section><h4>{t('picker.rarity')}</h4><div className="chip-group">
                {chip(rarities.size === 0, t('sim.all'), () => setRarities(new Set()), 'all')}
                {RARITIES.map(([rarity, key]) => chip(rarities.has(rarity), t(key), () => setRarities((current) => toggled(current, rarity)), String(rarity), `rarity-chip rarity-${rarity}`))}
              </div></section>
              <section><h4>{t('picker.location')}</h4><div className="chip-group">
                {PLACES.map(([value, key]) => chip(places.has(value), t(key), () => setPlaces((current) => toggled(current, value)), value))}
              </div></section>
              {needsData ? <p className="team-picker-note">{dataNote}</p> : <>
                <section><h4>{t('picker.affinity')}</h4><div className="chip-group">
                  {ELEMENTS.map((value) => chip(elements.has(value), <><i className={`element-dot element-${value}`} />{labels.element(value)}</>, () => setElements((current) => toggled(current, value)), value))}
                </div></section>
                <section><h4>{t('picker.role')}</h4><div className="chip-group">
                  {ROLES.map((value) => chip(roles.has(value), labels.role(value), () => setRoles((current) => toggled(current, value)), value))}
                </div></section>
                <details className="picker-details">
                  <summary>{t('picker.faction')}{factions.size > 0 && <em>{factions.size}</em>}</summary>
                  <div className="chip-group">
                    {(effectOptions?.factions ?? []).map((value) => chip(factions.has(value), labels.faction(value), () => setFactions((current) => toggled(current, value)), value))}
                  </div>
                </details>
                <section><h4>{t('picker.damageBasedOn2')}</h4><div className="chip-group">
                  {chip(conditions.includes('aoe'), t('picker.allEnemies'), () => toggleCondition('aoe'), 'aoe')}
                  {SCALING_KEYS.map((value) => chip(conditions.includes(`scaling:${value}`), labels.scaling(value), () => toggleCondition(`scaling:${value}`), value))}
                </div></section>
                <section><h4>{t('picker.leaderAura')}</h4><div className="chip-group">
                  {STAT_ORDER.slice(0, 7).map((value) => chip(conditions.includes(`aura:${value}`), labels.stat(value), () => toggleCondition(`aura:${value}`), value))}
                </div></section>
                {([['buff', t('picker.buffs')], ['debuff', t('picker.debuffs')]] as const).map(([kind, title]) => {
                  const selected = conditions.filter((condition) => condition.startsWith(`${kind}:`)).length
                  return <details className="picker-details" key={kind}>
                    <summary>{title}{selected > 0 && <em>{selected}</em>}</summary>
                    <div className="chip-group">
                      {(effectOptions?.[kind] ?? []).map((option) => chip(conditions.includes(`${kind}:${option.key}`), <><EffectGlyph icon={option.icon} ready={option.iconReady} />{option.label}</>,
                        () => toggleCondition(`${kind}:${option.key}`), option.key, 'effect-chip'))}
                    </div>
                  </details>
                })}
                <details className="picker-details">
                  <summary>{t('picker.otherEffects')}{conditions.some((condition) => condition.startsWith('special:')) && <em>{conditions.filter((condition) => condition.startsWith('special:')).length}</em>}</summary>
                  <div className="chip-group">
                    {(effectOptions?.special ?? []).map((name) => chip(conditions.includes(`special:${name}`), labels.special(name), () => toggleCondition(`special:${name}`), name))}
                  </div>
                </details>
              </>}
            </aside>

            <div className="picker-main">
              <div className="picker-searches">
                <label className="search-box"><Search size={16} /><input aria-label={t('picker.searchByName')} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('picker.searchByName')} /></label>
                <EffectSearch options={searchOptions} chosen={conditions} onPick={(key) => setConditions((current) => [...current, key])}
                  placeholder={needsData ? dataNote : t('picker.searchSkillEffectsBuffsDebuffs')}
                  empty={t('picker.noSuchEffect')} />
              </div>
              <div className="picker-filter-summary">
                <div className="picker-filter-summary-heading">
                  <span>{t('picker.filterLogic')}</span>
                  <button type="button" className="button ghost picker-clear" onClick={resetFilters}>{t('picker.resetAllFilters')}</button>
                </div>
                {filterGroups.length > 0 && <div className="picker-conditions">
                  {filterGroups.map((group) => <span key={group.key} className="picker-filter-group">
                    <strong>{group.label}</strong><small>{t(group.all ? 'picker.filterAll' : 'picker.filterAny')}</small>
                    {group.entries.map((entry) => <button type="button" key={entry.key} className="chip active"
                      aria-label={`${t('picker.remove')}: ${group.label} · ${entry.label}`} onClick={entry.remove}>{entry.label}<X size={12} /></button>)}
                  </span>)}
                </div>}
                <span className="picker-match-count" role="status">{roster && t('picker.matches', { shownCount: shown.length, rosterCount: roster.length })}</span>
              </div>
              <div className={`team-picker-list${dragging === 'slot' ? ' remove-target' : ''}`} ref={listRef}
                aria-busy={roster === null}
                onDragOver={(event) => { if (dragRef.current?.kind === 'slot') { event.preventDefault(); event.dataTransfer.dropEffect = 'move' } }}
                onDrop={(event) => {
                  const source = dragRef.current
                  if (source?.kind !== 'slot') return
                  event.preventDefault()
                  endDrag()
                  updateSlots((current) => current.map((item, index) => (index === source.index ? null : item)))
                }}>
                {error ? <p className="team-picker-note error">{backendText(error, language)}</p>
                  : roster === null ? <p className="team-picker-note">{t('picker.readingTheAccountSChampions')}</p>
                    : shown.length === 0 ? <div className="picker-empty-results" role="status">
                      <p className="team-picker-note">{roster.length === 0 ? t('picker.emptyRoster') : places.size === 0 ? t('picker.emptyLocations') : t('picker.noChampionsMatchExplanation')}</p>
                      {roster.length > 0 && <button type="button" className="button ghost picker-clear" onClick={resetFilters}>{t('picker.resetAllFilters')}</button>}
                    </div>
                      : <>
                        {visible.map((hero) => {
                          const slot = slotOf.get(hero.id)
                          const duplicate = !slot ? slots.findIndex((current) => current && championId(current.typeId) === championId(hero.typeId)) : -1
                          return <RosterCard key={hero.id} language={language} hero={hero} name={nameOf(hero.typeId)} element={elementOf.get(hero.typeId)} slot={slot}
                            replaceable={!slot && (full || duplicate >= 0)}
                            duplicatePosition={duplicate >= 0 ? duplicate + 1 : undefined} heroAvatar={heroAvatar}
                            onClick={clickCard} onOpen={openProfile} onDragStart={startDrag} onDragEnd={endDrag} />
                        })}
                        {visible.length < shown.length && <div ref={moreRef} className="team-picker-more">
                          {t('picker.scrollDownForMore', { visibleCount: visible.length, shownCount: shown.length })}</div>}
                      </>}
              </div>
            </div>
          </div>

          <div className="dialog-footer">
            <span className="picker-apply-note">
              <span className={hint ? 'picker-hint' : ''} role="status">{hint || (roster ? t('picker.showingChampionsChosen', { shownCount: shown.length, rosterCount: roster.length, chosenCount, teamSize }) : '')}</span>
              <small>{t('picker.draftNotice')}</small>
            </span>
            <div>
              <button type="button" className="button ghost" onClick={onClose}>{t('picker.cancel')}</button>
              <button type="button" className="button primary" disabled={team.length === 0}
                onClick={() => onApply({ heroTypeIds: team.map((hero) => hero.typeId), heroInstanceIds: team.map((hero) => hero.id) })}>
                {t('picker.setAsTheStrategyS')}</button>
            </div>
          </div>
          <HeroProfile language={language} data={heroData} dataState={dataState} effects={effectMap} typeId={profile?.typeId ?? null}
            roster={profile} name={profile ? nameOf(profile.typeId) : ''} bossMode={bossMode}
            inTeam={profile ? slotOf.has(profile.id) : false}
            canAdd={profile ? !full && !chosenChampions.has(championId(profile.typeId)) : false}
            heroAvatar={heroAvatar} onToggle={profile ? () => toggle(profile) : undefined} onClose={() => setProfile(null)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

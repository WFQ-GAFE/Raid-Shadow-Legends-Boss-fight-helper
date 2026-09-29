import { useEffect, useMemo, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Search, Users, X } from 'lucide-react'
import type { UiLanguage } from './i18n'

// Choosing a strategy group's team from the account's champions without the
// preparation screen (agent roster request: basic data only; gear is read when
// the strategy is saved or simulated).

export type RosterHero = {
  id: number
  typeId: number
  rarity: number    // 1 Common .. 6 Mythical
  grade: number     // stars
  level: number
  empower: number
  power?: number | null
  vault: boolean    // Master Vault (主仓库)
  reserve: boolean  // Reserve Vault (储备仓库)
}
export type ChosenTeam = { heroTypeIds: number[]; heroInstanceIds: number[] }

type Place = 'champions' | 'vault' | 'reserve'
const RARITIES: [number, string, string][] = [
  [6, '神话', 'Mythical'], [5, '传奇', 'Legendary'], [4, '史诗', 'Epic'],
  [3, '稀有', 'Rare'], [2, '罕见', 'Uncommon'], [1, '普通', 'Common'],
]
const PLACES: [Place, string, string][] = [['champions', '斗士库', 'Champions'], ['vault', '主仓库', 'Master Vault'], ['reserve', '储备仓库', 'Reserve Vault']]
const placeOf = (hero: RosterHero): Place => (hero.reserve ? 'reserve' : hero.vault ? 'vault' : 'champions')

function toggled<T>(values: Set<T>, value: T) {
  const next = new Set(values)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

export function TeamPicker({ language, open, teamSize, initial, loadRoster, heroName, heroAvatar, onClose, onApply }: {
  language: UiLanguage
  open: boolean
  teamSize: number
  initial: ChosenTeam
  loadRoster: () => Promise<RosterHero[]>
  heroName: (typeId: number) => string
  heroAvatar: (typeId: number) => ReactNode
  onClose: () => void
  onApply: (team: ChosenTeam) => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const [roster, setRoster] = useState<RosterHero[] | null>(null)
  const [error, setError] = useState('')
  const [chosen, setChosen] = useState<RosterHero[]>([])
  const [query, setQuery] = useState('')
  const [grades, setGrades] = useState<Set<number>>(new Set([6, 5]))
  const [rarities, setRarities] = useState<Set<number>>(new Set())
  const [places, setPlaces] = useState<Set<Place>>(new Set(['champions']))

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setRoster(null)
    setError('')
    setQuery('')
    loadRoster().then((heroes) => {
      if (cancelled) return
      setRoster(heroes)
      const byId = new Map(heroes.map((hero) => [hero.id, hero]))
      setChosen(initial.heroInstanceIds.map((id) => byId.get(id)).filter((hero): hero is RosterHero => Boolean(hero)))
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason))
    })
    return () => { cancelled = true }
    // The roster is read afresh each time the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const shown = useMemo(() => {
    const text = query.trim().toLowerCase()
    return (roster ?? []).filter((hero) =>
      (grades.size === 0 || grades.has(hero.grade)) && (rarities.size === 0 || rarities.has(hero.rarity))
      && places.has(placeOf(hero)) && (!text || heroName(hero.typeId).toLowerCase().includes(text)))
  }, [roster, query, grades, rarities, places, heroName])

  const slotOf = new Map(chosen.map((hero, index) => [hero.id, index + 1]))
  const chosenTypes = new Set(chosen.map((hero) => hero.typeId))
  function toggle(hero: RosterHero) {
    setChosen((current) => current.some((item) => item.id === hero.id)
      ? current.filter((item) => item.id !== hero.id)
      : current.length >= teamSize || current.some((item) => item.typeId === hero.typeId) ? current : [...current, hero])
  }
  const avatar = (hero: RosterHero) => <span className={`picker-avatar rarity-${hero.rarity}`}>{heroAvatar(hero.typeId)}</span>
  const rarityName = (rarity: number) => {
    const entry = RARITIES.find(([value]) => value === rarity)
    return entry ? t(entry[1], entry[2]) : ''
  }

  return (
    <Dialog.Root open={open} onOpenChange={(value) => { if (!value) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content team-picker-dialog" data-i18n-skip>
          <div className="dialog-heading">
            <span><Dialog.Title><Users size={17} /> {t('选择策略组队伍', "Choose the strategy's team")}</Dialog.Title>
              <Dialog.Description>{t(`从当前账号的英雄里选 ${teamSize} 名，点击已选的英雄可以移出；保存策略组时读取他们的装备。`, `Pick ${teamSize} of this account's champions; click a chosen one to remove it. Their gear is read when the strategy is saved.`)}</Dialog.Description></span>
            <Dialog.Close className="icon-button" aria-label={t('关闭', 'Close')}><X size={19} /></Dialog.Close>
          </div>
          <div className={`team-row team-${teamSize} team-picker-slots`}>
            {Array.from({ length: teamSize }, (_, index) => {
              const hero = chosen[index]
              return hero
                ? <button type="button" key={hero.id} className="team-member" title={t('点击移出队伍', 'Click to remove')} onClick={() => toggle(hero)}>
                    {avatar(hero)}<small>{heroName(hero.typeId)}</small></button>
                : <div key={`empty-${index}`} className="team-member empty"><span className="team-picker-empty">{index + 1}</span><small>{t('空位', 'Empty')}</small></div>
            })}
          </div>
          <div className="team-picker-filters">
            <label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('搜索英雄名字', 'Search by name')} /></label>
            <div className="team-picker-filter"><span>{t('星级', 'Stars')}</span><div className="chip-group">
              <button type="button" className={`chip ${grades.size === 0 ? 'active' : ''}`} onClick={() => setGrades(new Set())}>{t('全部', 'All')}</button>
              {[6, 5, 4, 3, 2, 1].map((grade) => <button type="button" key={grade} className={`chip ${grades.has(grade) ? 'active' : ''}`} onClick={() => setGrades((current) => toggled(current, grade))}>{grade}★</button>)}
            </div></div>
            <div className="team-picker-filter"><span>{t('稀有度', 'Rarity')}</span><div className="chip-group">
              <button type="button" className={`chip ${rarities.size === 0 ? 'active' : ''}`} onClick={() => setRarities(new Set())}>{t('全部', 'All')}</button>
              {RARITIES.map(([rarity, zh, en]) => <button type="button" key={rarity} className={`chip rarity-chip rarity-${rarity} ${rarities.has(rarity) ? 'active' : ''}`} onClick={() => setRarities((current) => toggled(current, rarity))}>{t(zh, en)}</button>)}
            </div></div>
            <div className="team-picker-filter"><span>{t('位置', 'Location')}</span><div className="chip-group">
              {PLACES.map(([place, zh, en]) => <button type="button" key={place} className={`chip ${places.has(place) ? 'active' : ''}`} onClick={() => setPlaces((current) => toggled(current, place))}>{t(zh, en)}</button>)}
            </div></div>
          </div>
          <div className="team-picker-list">
            {error ? <p className="team-picker-note error">{error}</p>
              : roster === null ? <p className="team-picker-note">{t('正在读取账号的英雄…', "Reading the account's champions…")}</p>
                : shown.length === 0 ? <p className="team-picker-note">{t('没有符合筛选条件的英雄', 'No champions match the filters')}</p>
                  : shown.map((hero) => {
                    const slot = slotOf.get(hero.id)
                    const blocked = !slot && (chosen.length >= teamSize || chosenTypes.has(hero.typeId))
                    const place = placeOf(hero)
                    return <button type="button" key={hero.id} className={`head-type-option ${slot ? 'active' : ''}`} disabled={blocked} onClick={() => toggle(hero)}>
                      {avatar(hero)}
                      <span><strong>{heroName(hero.typeId)}</strong>
                        <small>{`${hero.grade}★ · ${rarityName(hero.rarity)} · ${t(`${hero.level} 级`, `Lv ${hero.level}`)}${hero.empower ? ` · +${hero.empower}` : ''}`}</small>
                        <small>{hero.power ? t(`战力 ${Math.round(hero.power).toLocaleString()}`, `Power ${Math.round(hero.power).toLocaleString()}`) : ''}
                          {place !== 'champions' && <b className="team-picker-place"> · {t(place === 'vault' ? '主仓库' : '储备仓库', place === 'vault' ? 'Master Vault' : 'Reserve Vault')}</b>}</small></span>
                      {slot && <em>{slot}</em>}
                    </button>
                  })}
          </div>
          <div className="dialog-footer">
            <span>{roster ? t(`显示 ${shown.length} / ${roster.length} 名英雄 · 已选 ${chosen.length}/${teamSize}`, `Showing ${shown.length} / ${roster.length} champions · chosen ${chosen.length}/${teamSize}`) : ''}</span>
            <div>
              <button type="button" className="button ghost" onClick={onClose}>{t('取消', 'Cancel')}</button>
              <button type="button" className="button primary" disabled={chosen.length === 0}
                onClick={() => onApply({ heroTypeIds: chosen.map((hero) => hero.typeId), heroInstanceIds: chosen.map((hero) => hero.id) })}>
                {t('设为策略组队伍', "Set as the strategy's team")}</button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

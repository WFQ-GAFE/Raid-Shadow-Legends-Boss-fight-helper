import { useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Gem, Users, X } from 'lucide-react'
import { backendText, type MessageKey, type UiLanguage, useI18n } from './i18n'

// The prepared team as the hero screen shows it (tools/team_preview.py):
// overall stats (base + bonus), equipped sets, masteries, blessing and relic.
// The same view shows an imported strategy's author team (referenceTeam).

type SnapshotHero = {
  heroId?: number
  typeId: number
  level: number
  grade: number
  empower: number
  awakened?: number
  power?: number
  skills: { typeId: number; level: number }[]
  masteries: number[]
  blessing?: number
  relic?: { typeId: number | null; rank: number; level: number }
  sets?: { set: number; pieces: number }[]
  equipped?: number
  // A simulation report has only the total: the battle stats when the battle started.
  stats?: { base?: number[]; bonus?: number[]; total: number[] }
  statsReason?: string
}
export type TeamSnapshot = {
  schema?: number
  status?: string
  reason?: string
  bossMode?: string
  heroes?: SnapshotHero[]
  names?: { sets?: Record<string, string>; blessings?: Record<string, string>; relics?: Record<string, string>; masteries?: Record<string, string> }
  icons?: { sets?: Record<string, string>; blessings?: Record<string, string> }
  capturedAt?: string
  // 1.1.1 exports: when the author saved the strategy, and the battle data to simulate the team.
  savedAt?: string
  simulation?: { format: string; schema: number; data: string }
}
export type TeamPreviewSummaryState = { revision: string; status?: string; bossMode?: string; heroes?: number }

type Lang = UiLanguage
// StatKindId 1..8 in the hero screen's order.
// Battle stat ids 1..8 in the order the hero screen shows them.
const STATS: [number, MessageKey][] = [
  [1, 'hero.stat.Health'], [2, 'hero.stat.Attack'], [3, 'hero.stat.Defence'], [4, 'hero.stat.Speed'],
  [7, 'hero.stat.CriticalChance'], [8, 'hero.stat.CriticalDamage'], [5, 'hero.stat.Resistance'], [6, 'hero.stat.Accuracy'],
]
// Mastery ids 5001xx / 5002xx / 5003xx are the three trees.
const TREES: [number, MessageKey][] = [[1, 'mastery.tree.1'], [2, 'mastery.tree.2'], [3, 'mastery.tree.3']]

function statText(stat: number, value: number | undefined) {
  if (value === undefined) return '—'
  return stat >= 7 ? `${Math.round(value * 100)}%` : Math.round(value).toLocaleString()
}

function icon(kind: 'set' | 'blessing' | 'mastery' | 'relic', name: string | number | null | undefined) {
  return name === undefined || name === null || name === '' ? undefined : `/api/asset/${kind}/${encodeURIComponent(String(name))}`
}

function GameIcon({ src, title, size = 34 }: { src?: string; title: string; size?: number }) {
  return src
    ? <img className="game-icon" src={src} alt={title} title={title} width={size} height={size} loading="lazy" onError={(event) => { event.currentTarget.style.visibility = 'hidden' }} />
    : <span className="game-icon empty" style={{ width: size, height: size }} title={title} />
}

// A game icon with a small corner number (pieces, relic or skill level); names
// only in the tooltip, so the row reads the same in every language.
function IconBadge({ src, title, badge, shape = 'square', hideWithoutIcon = false }: {
  src?: string
  title: string
  badge?: string
  shape?: 'square' | 'round' | 'card'
  hideWithoutIcon?: boolean
}) {
  const [failed, setFailed] = useState(false)
  if ((failed || !src) && hideWithoutIcon) return null
  return (
    <span className={`icon-badge ${shape}`} title={title}>
      {src && !failed ? <img src={src} alt={title} loading="lazy" onError={() => setFailed(true)} /> : <span className="icon-badge-empty" />}
      {badge && <em>{badge}</em>}
    </span>
  )
}

export function TeamPreviewSummary({ preview, reference, onOpenPreview, onOpenReference }: {
  language: Lang
  preview: TeamSnapshot | null
  reference?: TeamSnapshot | null
  onOpenPreview: () => void
  onOpenReference: () => void
}) {
  const { t } = useI18n()
  const ready = preview?.status === 'captured' && (preview.heroes?.length ?? 0) > 0
  const note = !preview
    ? t('teamPreview.openTheBossPreparationScreen')
    : ready
      ? t('teamPreview.statsSetsMasteriesAndBlessings', { heroesCount: preview.heroes!.length })
      : preview.reason === 'agent_outdated'
        ? t('teamPreview.theInGameReaderIs')
        : preview.status === 'captured' || preview.reason === 'heroes_not_found'
          ? t('teamPreview.theSelectedChampionsCouldNot')
          : t('teamPreview.teamSetupUnavailable', { reason: backendText(preview.reason ?? preview.status) })
  return (
    <div className="team-preview-summary" data-i18n-skip>
      <span>{note}</span>
      <div>
        {ready && <button type="button" className="button ghost" onClick={onOpenPreview}><Gem size={15} />{t('sim.teamSetup')}</button>}
        {reference && (reference.heroes?.length ?? 0) > 0 && <button type="button" className="button ghost" onClick={onOpenReference}><Users size={15} />{t('teamPreview.authorSTeam')}</button>}
      </div>
    </div>
  )
}

export function TeamPreviewDialog({ snapshot, title, description, onClose, heroName, heroAvatar, skillName }: {
  language: Lang
  snapshot: TeamSnapshot | null
  title: string
  description: string
  onClose: () => void
  heroName: (typeId: number) => string
  heroAvatar: (typeId: number) => ReactNode
  skillName: (heroTypeId: number, skillTypeId: number) => string
}) {
  const { t } = useI18n()
  const names = snapshot?.names ?? {}
  const icons = snapshot?.icons ?? {}
  return (
    <Dialog.Root open={Boolean(snapshot)} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content log-dialog team-preview-dialog" data-i18n-skip>
          <div className="dialog-heading">
            <span><Dialog.Title><Gem size={17} /> {title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description></span>
            <Dialog.Close className="icon-button" aria-label={t('picker.close')}><X size={19} /></Dialog.Close>
          </div>
          <div className="team-preview-body">
            {(snapshot?.heroes ?? []).map((hero, index) => {
              const blessingName = hero.blessing ? names.blessings?.[String(hero.blessing)] ?? `#${hero.blessing}` : undefined
              const relicName = hero.relic?.typeId ? names.relics?.[String(hero.relic.typeId)] ?? `#${hero.relic.typeId}` : undefined
              return (
                <article key={`${hero.typeId}-${index}`} className="team-preview-hero">
                  <header>
                    {heroAvatar(hero.typeId)}
                    <div>
                      <strong>{heroName(hero.typeId)}</strong>
                      <span>{t('teamPreview.level', { grade: hero.grade, level: hero.level })}
                        {hero.awakened ? t('teamPreview.awakened', { awakened: hero.awakened }) : ''}
                        {t('teamPreview.empower', { empower: hero.empower })}
                        {hero.power ? t('teamPreview.power', { power: Math.round(hero.power) }) : ''}</span>
                      <span className="muted">{t('teamPreview.masteriesEquipped', { masteriesCount: hero.masteries.length, equipped: hero.equipped ?? 0 })}</span>
                    </div>
                  </header>
                  {hero.stats ? <div className="team-preview-stats">
                    {STATS.map(([stat, key]) => (
                      <div key={stat}><small>{t(key)}</small><strong>{statText(stat, hero.stats!.total[stat - 1])}</strong>
                        {hero.stats!.base && hero.stats!.bonus && <em>{t('teamPreview.base')} {statText(stat, hero.stats!.base[stat - 1])} | {t('teamPreview.bonus')} +{statText(stat, hero.stats!.bonus[stat - 1])}</em>}</div>
                    ))}
                  </div> : <p className="muted">{t('teamPreview.overallStatsUnavailable', { reason: hero.statsReason ? backendText(hero.statsReason) : t('teamPreview.unknown') })}</p>}
                  <div className="team-preview-kit">
                    <div>
                      <small>{t('teamPreview.sets')}</small>
                      <div>{(hero.sets ?? []).map((item) => {
                        const name = names.sets?.[String(item.set)] ?? t('teamPreview.set', { set: item.set })
                        return <IconBadge key={item.set} src={icon('set', icons.sets?.[String(item.set)])} title={`${name} ×${item.pieces}`} badge={`×${item.pieces}`} />
                      })}{!(hero.sets ?? []).length && <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('teamPreview.blessing')}</small>
                      <div>{blessingName
                        ? <IconBadge shape="card" src={icon('blessing', icons.blessings?.[String(hero.blessing)])} title={blessingName} />
                        : <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('teamPreview.relic')}</small>
                      <div>{relicName
                        ? <IconBadge src={icon('relic', hero.relic?.typeId)} title={t('teamPreview.level2', { relicName, level: hero.relic!.level })} badge={String(hero.relic!.level)} />
                        : <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('teamPreview.skills')}</small>
                      <div>{hero.skills.map((skill) => {
                        // The skill catalog comes from battle skill lists, which leave out passives.
                        const name = skillName(hero.typeId, skill.typeId)
                        const label = name && name !== String(skill.typeId) ? name : t('teamPreview.passiveSkill')
                        return <IconBadge key={skill.typeId} shape="round" hideWithoutIcon src={`/api/asset/skill/${hero.typeId}/${skill.typeId}`}
                          title={`${label} · Lv${skill.level}`} badge={String(skill.level)} />
                      })}</div>
                    </div>
                  </div>
                  <div className="team-preview-masteries">
                    {TREES.map(([tree, key]) => {
                      const owned = hero.masteries.filter((id) => Math.floor(id / 100) % 10 === tree)
                      if (!owned.length) return null
                      return <div key={tree}><small>{t(key)} ({owned.length})</small>
                        <div>{owned.map((id) => <GameIcon key={id} src={icon('mastery', id)} title={names.masteries?.[String(id)] ?? String(id)} size={28} />)}</div></div>
                    })}
                  </div>
                </article>
              )
            })}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

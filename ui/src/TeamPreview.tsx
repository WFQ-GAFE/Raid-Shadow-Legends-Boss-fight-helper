import { useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Gem, Users, X } from 'lucide-react'
import type { UiLanguage } from './i18n'

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
  stats?: { base: number[]; bonus: number[]; total: number[] }
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
}
export type TeamPreviewSummaryState = { revision: string; status?: string; bossMode?: string; heroes?: number }

type Lang = UiLanguage
// StatKindId 1..8 in the hero screen's order.
const STATS: [number, string, string][] = [
  [1, '生命值', 'HP'], [2, '攻击', 'ATK'], [3, '防御', 'DEF'], [4, '速度', 'SPD'],
  [7, '暴击率', 'C.RATE'], [8, '暴击伤害', 'C.DMG'], [5, '抗性', 'RES'], [6, '精准', 'ACC'],
]
// Mastery ids 5001xx / 5002xx / 5003xx are the three trees.
const TREES: [number, string, string][] = [[1, '进攻', 'Offense'], [2, '防御', 'Defense'], [3, '辅助', 'Support']]

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

export function TeamPreviewSummary({ language, preview, reference, onOpenPreview, onOpenReference }: {
  language: Lang
  preview: TeamSnapshot | null
  reference?: TeamSnapshot | null
  onOpenPreview: () => void
  onOpenReference: () => void
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const ready = preview?.status === 'captured' && (preview.heroes?.length ?? 0) > 0
  const note = !preview
    ? t('进入 Boss 准备界面后读取英雄属性与套装', 'Open the boss preparation screen to read champion stats and sets')
    : ready
      ? t(`已读取 ${preview.heroes!.length} 名英雄的属性、套装、专精与祝福`, `Stats, sets, masteries and blessings of ${preview.heroes!.length} champions`)
      : preview.reason === 'agent_outdated'
        ? t('游戏中的读取组件是旧版，请重启游戏后再读取', 'The in-game reader is outdated; restart the game to read the team')
        : preview.status === 'captured' || preview.reason === 'heroes_not_found'
          ? t('没有读到所选英雄的数据；请重启游戏后回到准备界面重新选择队伍', 'The selected champions could not be read; restart the game and select the team again')
          : t(`未能读取队伍配置（${preview.reason ?? preview.status}）`, `Team setup unavailable (${preview.reason ?? preview.status})`)
  return (
    <div className="team-preview-summary" data-i18n-skip>
      <span>{note}</span>
      <div>
        {ready && <button type="button" className="button ghost" onClick={onOpenPreview}><Gem size={15} />{t('队伍配置', 'Team setup')}</button>}
        {reference && (reference.heroes?.length ?? 0) > 0 && <button type="button" className="button ghost" onClick={onOpenReference}><Users size={15} />{t('作者的队伍', "Author's team")}</button>}
      </div>
    </div>
  )
}

export function TeamPreviewDialog({ language, snapshot, title, description, onClose, heroName, heroAvatar, skillName }: {
  language: Lang
  snapshot: TeamSnapshot | null
  title: string
  description: string
  onClose: () => void
  heroName: (typeId: number) => string
  heroAvatar: (typeId: number) => ReactNode
  skillName: (heroTypeId: number, skillTypeId: number) => string
}) {
  const t = (zh: string, en: string) => (language === 'en' ? en : zh)
  const names = snapshot?.names ?? {}
  const icons = snapshot?.icons ?? {}
  return (
    <Dialog.Root open={Boolean(snapshot)} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content log-dialog team-preview-dialog" data-i18n-skip>
          <div className="dialog-heading">
            <span><Dialog.Title><Gem size={17} /> {title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description></span>
            <Dialog.Close className="icon-button" aria-label={t('关闭', 'Close')}><X size={19} /></Dialog.Close>
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
                      <span>{t(`${hero.grade}★ · ${hero.level} 级`, `${hero.grade}★ · level ${hero.level}`)}
                        {hero.awakened ? t(` · 觉醒 ${hero.awakened}★`, ` · awakened ${hero.awakened}★`) : ''}
                        {t(` · 强化 ${hero.empower}`, ` · empower ${hero.empower}`)}
                        {hero.power ? t(` · 战力 ${hero.power.toLocaleString()}`, ` · power ${hero.power.toLocaleString()}`) : ''}</span>
                      <span className="muted">{t(`专精 ${hero.masteries.length} · 装备 ${hero.equipped ?? 0}/9`, `Masteries ${hero.masteries.length} · equipped ${hero.equipped ?? 0}/9`)}</span>
                    </div>
                  </header>
                  {hero.stats ? <div className="team-preview-stats">
                    {STATS.map(([stat, zh, en]) => (
                      <div key={stat}><small>{t(zh, en)}</small><strong>{statText(stat, hero.stats!.total[stat - 1])}</strong>
                        <em>{t('基础', 'Base')} {statText(stat, hero.stats!.base[stat - 1])} | {t('加成', 'Bonus')} +{statText(stat, hero.stats!.bonus[stat - 1])}</em></div>
                    ))}
                  </div> : <p className="muted">{t(`没有读到整体属性（${hero.statsReason ?? '未知'}）`, `Overall stats unavailable (${hero.statsReason ?? 'unknown'})`)}</p>}
                  <div className="team-preview-kit">
                    <div>
                      <small>{t('套装', 'Sets')}</small>
                      <div>{(hero.sets ?? []).map((item) => {
                        const name = names.sets?.[String(item.set)] ?? t(`套装 ${item.set}`, `Set ${item.set}`)
                        return <IconBadge key={item.set} src={icon('set', icons.sets?.[String(item.set)])} title={`${name} ×${item.pieces}`} badge={`×${item.pieces}`} />
                      })}{!(hero.sets ?? []).length && <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('祝福', 'Blessing')}</small>
                      <div>{blessingName
                        ? <IconBadge shape="card" src={icon('blessing', icons.blessings?.[String(hero.blessing)])} title={blessingName} />
                        : <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('圣物', 'Relic')}</small>
                      <div>{relicName
                        ? <IconBadge src={icon('relic', hero.relic?.typeId)} title={t(`${relicName} · 等级 ${hero.relic!.level}`, `${relicName} · level ${hero.relic!.level}`)} badge={String(hero.relic!.level)} />
                        : <span className="muted">—</span>}</div>
                    </div>
                    <div>
                      <small>{t('技能', 'Skills')}</small>
                      <div>{hero.skills.map((skill) => {
                        // The skill catalog comes from battle skill lists, which leave out passives.
                        const name = skillName(hero.typeId, skill.typeId)
                        const label = name && name !== String(skill.typeId) ? name : t('被动技能', 'Passive skill')
                        return <IconBadge key={skill.typeId} shape="round" hideWithoutIcon src={`/api/asset/skill/${hero.typeId}/${skill.typeId}`}
                          title={`${label} · Lv${skill.level}`} badge={String(skill.level)} />
                      })}</div>
                    </div>
                  </div>
                  <div className="team-preview-masteries">
                    {TREES.map(([tree, zh, en]) => {
                      const owned = hero.masteries.filter((id) => Math.floor(id / 100) % 10 === tree)
                      if (!owned.length) return null
                      return <div key={tree}><small>{t(zh, en)} ({owned.length})</small>
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

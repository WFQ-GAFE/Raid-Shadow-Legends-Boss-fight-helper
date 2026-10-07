import { useMemo, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Crown, Sparkles, X, Zap } from 'lucide-react'
import { translate, type UiLanguage, useI18n } from './i18n'
import { championKit, effectInfo, type AppliedEffect, type EffectLike, type HeroData, type RosterHero } from './heroData'
import { STAT_ORDER, auraApplies, auraText, labeler, readableName, type Labeler } from './heroLabels'
import { describeFormula, type FormulaContext } from './formula'

// A champion's profile from the game's static data: affinity, faction, role,
// stats at 6 stars level 60 for its ascension level, leader aura and every
// skill with what it does (damage basis, buffs, debuffs, other effects, books).

function SkillImage({ typeId, skillId }: { typeId: number; skillId: number }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className="skill-icon">
      {failed ? <Zap size={17} /> : <img src={`/api/asset/skill/${typeId}/${skillId}`} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />}
    </span>
  )
}

function EffectIcon({ icon, ready }: { icon?: string; ready: boolean }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className="effect-icon">
      {icon && ready && !failed ? <img src={`/api/asset/effect/${encodeURIComponent(icon)}`} alt="" loading="lazy" onError={() => setFailed(true)} /> : <Sparkles size={13} />}
    </span>
  )
}

function AppliedEffects({ rows, data, effects, labels, language }: {
  rows: AppliedEffect[]
  data: HeroData
  effects: Map<string, EffectLike>
  labels: Labeler
  language: UiLanguage
}) {
  return (
    <span className="profile-effects">
      {/* A skill that applies the same effect on several hits lists it once. */}
      {rows.filter((row, index) => rows.findIndex((other) => other.join() === row.join()) === index).map(([typeId, turns, chance, scope, conditional], index) => {
        const info = effectInfo(effects, data, typeId, language)
        const details = [
          turns > 0 ? translate(language, 'profile.turn', { turns }) : '',
          chance !== null ? `${chance}%` : '',
          labels.scope(scope),
          conditional ? translate(language, 'profile.conditional') : '',
        ].filter(Boolean).join(' · ')
        return <span className="profile-effect" key={`${typeId}-${index}`} title={info.description.replace(/<[^>]+>/g, '') || undefined}>
          <EffectIcon icon={info.icon} ready={info.iconReady} />{info.label}<small>{details}</small></span>
      })}
    </span>
  )
}

// Each distinct damage formula once, with whom it hits.
function damageLines(formulas: string[], scopes?: string[]) {
  const lines: { formula: string; scope: string }[] = []
  formulas.forEach((formula, index) => {
    const scope = scopes?.[index] ?? 'target'
    if (!lines.some((line) => line.formula === formula && line.scope === scope)) lines.push({ formula, scope })
  })
  return lines
}

export function HeroProfile({ language, data, dataState, effects, typeId, roster, name, bossMode, inTeam, canAdd, heroAvatar, onToggle, onClose }: {
  language: UiLanguage
  data: HeroData | null
  dataState: 'loading' | 'ready' | 'unavailable'
  effects: Map<string, EffectLike>
  typeId: number | null
  roster?: RosterHero | null
  name: string
  bossMode: 'chimera' | 'hydra'
  inTeam: boolean
  canAdd: boolean
  heroAvatar: (typeId: number) => ReactNode
  onToggle?: () => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const labels = useMemo(() => labeler(language, data), [language, data])
  const kit = useMemo(() => (data && typeId ? championKit(data, typeId) : null), [data, typeId])
  // Damage formulas name effect kinds (Poison, HP Burn, Hex) as the game does.
  const formulaContext = useMemo<FormulaContext>(() => {
    const names = new Map<string, string>()
    for (const [id, entry] of Object.entries(data?.statusEffects ?? {}))
      if (data && entry.kind && !names.has(entry.kind)) names.set(entry.kind, effectInfo(effects, data, Number(id), language).groupLabel)
    return { locale: language, t: (key, params) => translate(language, key, params), kindName: (kind) => names.get(kind) ?? readableName(kind) }
  }, [data, effects, language])
  // Skills gained by ascending, with the first level that adds them.
  const unlocks = useMemo(() => {
    const levels = new Map<number, number>()
    if (!kit) return levels
    for (const [level, entry] of Object.entries(kit.hero.ascension ?? {}))
      for (const skills of entry.skills) for (const skill of skills)
        if (!levels.has(skill) || levels.get(skill)! > Number(level)) levels.set(skill, Number(level))
    return levels
  }, [kit])
  const place = roster ? (roster.reserve ? t('profile.reserveVault') : roster.vault ? t('profile.masterVault') : t('profile.champions')) : ''

  const formSkills = (formIndex: number) => {
    if (!kit || !data) return []
    const form = kit.forms[formIndex]
    const base = kit.hero.forms[formIndex]?.skills ?? []
    // Skills of higher ascension levels the champion has not reached yet.
    const later = [...unlocks.keys()].filter((skill) => !form.skills.includes(skill)
      && (kit.hero.ascension?.[String(unlocks.get(skill))]?.skills[formIndex] ?? []).includes(skill))
    return [...form.skills, ...later].filter((skill) => data.skills[String(skill)] && !data.skills[String(skill)].hidden)
      .map((skill) => ({ id: skill, skill: data.skills[String(skill)], unlock: base.includes(skill) ? 0 : unlocks.get(skill) ?? 0 }))
  }

  return (
    <Dialog.Root open={typeId !== null} onOpenChange={(value) => { if (!value) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay hero-profile-overlay" />
        <Dialog.Content className="dialog-content hero-profile-dialog" data-i18n-skip>
          <div className="dialog-heading">
            <span className="hero-profile-title">
              {typeId !== null && <span className={`picker-avatar rarity-${roster?.rarity ?? 0}`}>{heroAvatar(typeId)}</span>}
              <span>
                <Dialog.Title>{name}</Dialog.Title>
                <Dialog.Description asChild>
                  <span className="hero-profile-tags">
                    {kit && <>
                      <em>{labels.rarity(kit.hero.rarity)}</em>
                      <em>{labels.faction(kit.hero.faction)}</em>
                      {[...new Set(kit.forms.map((form) => form.element))].map((element) => <em key={element} className={`element-tag element-${element}`}>{labels.element(element)}</em>)}
                      {[...new Set(kit.forms.map((form) => form.role))].map((role) => <em key={role}>{labels.role(role)}</em>)}
                    </>}
                    {roster && <small>{[`${roster.grade}★`, t('picker.lv', { level: roster.level }),
                      kit?.ascension ? t('profile.ascension', { ascension: kit.ascension }) : '',
                      roster.empower ? t('profile.empowered', { empower: roster.empower }) : '',
                      roster.power ? t('picker.power', { power: Math.round(roster.power) }) : '',
                      place].filter(Boolean).join(' · ')}</small>}
                  </span>
                </Dialog.Description>
              </span>
            </span>
            <Dialog.Close className="icon-button" aria-label={t('picker.close')}><X size={19} /></Dialog.Close>
          </div>
          <div className="hero-profile-body">
            {!kit || !data ? <p className="team-picker-note">{dataState === 'loading'
              ? t('profile.readingChampionDataFromThe')
              : t('profile.championDataIsUnavailableOpen')}</p>
              : <>
                <section className="hero-profile-section">
                  <h4>{t('profile.baseStatsLevelWithoutGear', { ascension: kit.ascension ?? 0 })}</h4>
                  <div className="hero-profile-stats">
                    {STAT_ORDER.map((stat, index) => <span key={stat}><small>{labels.stat(stat)}</small>
                      <strong>{index >= 6 ? `${kit.forms[0].stats[index]}%` : kit.forms[0].stats[index].toLocaleString()}</strong></span>)}
                  </div>
                </section>
                <section className="hero-profile-section">
                  <h4><Crown size={14} /> {t('picker.leaderAura')}</h4>
                  {kit.hero.aura?.length
                    ? kit.hero.aura.map((aura, index) => <p key={index} className={auraApplies(aura, bossMode) ? 'aura-line' : 'aura-line inactive'}>
                        {auraText(aura, labels, language)}
                        {!auraApplies(aura, bossMode) && <small>{t('profile.noEffectAgainstThe', { boss: bossMode })}</small>}
                      </p>)
                    : <p className="aura-line inactive">{t('profile.noLeaderAura')}</p>}
                </section>
                {kit.forms.map((form, formIndex) => (
                  <section className="hero-profile-section" key={formIndex}>
                    <h4>{kit.forms.length > 1
                      ? `${formIndex === 0 ? t('profile.baseForm') : t('profile.alternateForm')} · ${labels.element(form.element)} · ${labels.role(form.role)}`
                      : t('teamPreview.skills')}</h4>
                    <div className="hero-profile-skills">
                      {formSkills(formIndex).map(({ id, skill, unlock }) => {
                        const locked = unlock > 0 && unlock > (kit.ascension || 0)
                        const books = Object.entries(skill.books ?? {}).map(([kind, value]) =>
                          kind === 'CooltimeTurn' ? `${labels.book(kind)} −${value}` : `${labels.book(kind)} +${value}%`).join(t('profile.text'))
                        return (
                          <article className={`hero-profile-skill${locked ? ' locked' : ''}`} key={id}>
                            <SkillImage typeId={typeId ?? kit.id} skillId={id} />
                            <div>
                              <header>
                                <strong>{skill.name || t('profile.skill', { id })}</strong>
                                {skill.passive && <em>{t('profile.passive')}</em>}
                                {skill.cd ? <em>{t('profile.cooldown', { cd: skill.cd })}</em> : !skill.passive ? <em>{t('profile.noCooldown')}</em> : null}
                                {unlock > 0 && <em className="unlock">{t('profile.ascension2', { unlock })}</em>}
                              </header>
                              {skill.desc && <p>{skill.desc.replace(/<[^>]+>/g, '').trim()}</p>}
                              <dl>
                                {skill.damage?.length ? <><dt>{t('chimeraSim.damage')}</dt>
                                  <dd className="formula-lines">{damageLines(skill.damage, skill.damageScopes).map(({ formula, scope }) => (
                                    <span key={`${formula}-${scope}`} title={formula}>
                                      {scope === 'all' || scope === 'random' ? <em className="formula-scope">{scope === 'all' ? t('profile.allEnemies') : t('profile.randomEnemy')}</em> : null}{' '}
                                      {describeFormula(formula, formulaContext)}</span>))}</dd></> : null}
                                {skill.debuffs?.length ? <><dt>{t('picker.debuffs')}</dt><dd><AppliedEffects rows={skill.debuffs} data={data} effects={effects} labels={labels} language={language} /></dd></> : null}
                                {skill.buffs?.length ? <><dt>{t('picker.buffs')}</dt><dd><AppliedEffects rows={skill.buffs} data={data} effects={effects} labels={labels} language={language} /></dd></> : null}
                                {skill.special?.length ? <><dt>{t('profile.effects')}</dt><dd>{skill.special.map((name) => labels.special(name)).join(t('profile.text2'))}</dd></> : null}
                                {books ? <><dt>{t('profile.books')}</dt><dd>{books}</dd></> : null}
                              </dl>
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  </section>
                ))}
              </>}
          </div>
          <div className="dialog-footer">
            <span>{t('profile.fromTheGameSLocal')}</span>
            <div>
              <Dialog.Close className="button ghost">{t('picker.close')}</Dialog.Close>
              {onToggle && (inTeam || canAdd) && <button type="button" className={`button ${inTeam ? 'ghost discard-button' : 'primary'}`} onClick={onToggle}>
                {inTeam ? t('profile.removeFromTeam') : t('profile.addToTeam')}</button>}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

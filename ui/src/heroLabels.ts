import type { HeroAura, HeroData } from './heroData'
import { hasMessage, translate, type Locale } from './i18n'

// Names for the champion data's enum values, from the message catalogs
// (hero.<group>.<value>). In Chinese the game client's own labels
// (HeroData.labels, read in the client's language) come first.

export const STAT_ORDER = ['Health', 'Attack', 'Defence', 'Speed', 'Resistance', 'Accuracy', 'CriticalChance', 'CriticalDamage']
export const SCALING_KEYS = ['ATK', 'DEF', 'HP', 'SPD', 'ACC', 'RES', 'TRG_HP']
// Special effects the champion filters offer (catalog hero.special.<key>).
export const SPECIAL_KEYS = [
  'Revive', 'Heal', 'IncreaseStamina', 'ReduceStamina', 'ExtraTurn', 'ReduceCooldown', 'IncreaseCooldown', 'RemoveDebuff',
  'RemoveBuff', 'StealBuff', 'TransferDebuff', 'IncreaseDebuffLifetime', 'ReduceDebuffLifetime', 'IncreaseBuffLifetime',
  'ReduceBuffLifetime', 'DestroyHp', 'DestroyStats', 'TeamAttack', 'ActivateSkill', 'Detonate', 'DetonateContinuousDamage',
  'ForceStatusEffectTick', 'MultiplyDebuff', 'Counterattack', 'BlockDebuff', 'ReflectDamage', 'ShareDamage', 'SwapHealth',
  'EvenStamina', 'Polymorph', 'IgnoreBuffs', 'IgnoreDefence',
]

export function readableName(name: string) {
  return name.replace(/(?<=[a-z0-9])(?=[A-Z])/g, ' ')
}

export function labeler(locale: Locale, data: HeroData | null) {
  const game = (group: string, key: string) => (locale === 'zh-CN' ? data?.labels?.[group]?.[key] : undefined)
  const pick = (group: string, gameGroup: string | null, key: string | undefined) => {
    if (!key) return ''
    const catalogKey = `hero.${group}.${key}`
    return (gameGroup && game(gameGroup, key)) || (hasMessage(catalogKey) ? translate(locale, catalogKey) : readableName(key))
  }
  return {
    element: (key?: string) => pick('element', 'element', key),
    role: (key?: string) => pick('role', 'role', key),
    rarity: (key?: string) => pick('rarity', 'rarity', key),
    faction: (key?: string) => pick('faction', 'faction', key),
    stat: (key?: string) => pick('stat', 'stat', key),
    area: (key?: string) => pick('area', 'area', key),
    scaling: (key?: string) => pick('scaling', null, key),
    special: (key?: string) => pick('special', null, key),
    book: (key?: string) => pick('book', null, key),
    scope: (key?: string) => pick('scope', null, key),
  }
}
export type Labeler = ReturnType<typeof labeler>

// "DEF +30%", "ACC +70"; restricted auras name the affinity or faction.
export function auraText(aura: HeroAura, labels: Labeler, locale: Locale) {
  const amount = `${labels.stat(aura.stat)} +${aura.value}${aura.absolute ? '' : '%'}`
  const who = aura.element ? labels.element(aura.element) : aura.faction ? labels.faction(aura.faction) : ''
  const where = aura.area ? labels.area(aura.area) : translate(locale, 'hero.area.all')
  return who ? translate(locale, 'hero.auraFor', { who, amount, where }) : translate(locale, 'hero.aura', { amount, where })
}

// An aura limited to another area does nothing in this boss fight.
export function auraApplies(aura: HeroAura, bossMode: 'chimera' | 'hydra') {
  return !aura.area || aura.area === (bossMode === 'hydra' ? 'Hydra' : 'Chimera')
}

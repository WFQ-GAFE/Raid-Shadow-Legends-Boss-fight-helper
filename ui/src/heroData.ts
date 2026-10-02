import { effectName } from './effectNames'
import type { Locale } from './i18n'

// Champion data from the game's static data (tools/hero_data.py): what the
// team picker searches and what a champion profile shows.

export type AppliedEffect = [typeId: number, turns: number, chance: number | null, scope: string, conditional: boolean]
export type HeroDataSkill = {
  name: string
  desc: string
  cd?: number
  passive?: boolean
  hidden?: boolean
  damage?: string[]
  // Who each damage formula hits when not only the target: "all", "random", ...
  damageScopes?: string[]
  scaling?: string[]
  aoe?: boolean
  buffs?: AppliedEffect[]
  debuffs?: AppliedEffect[]
  special?: string[]
  books?: Record<string, number>
}
export type HeroDataForm = { element: string; role: string; stats: number[]; skills: number[] }
export type HeroAura = { stat: string; value: number; absolute: boolean; area?: string; element?: string; faction?: string }
export type HeroDataHero = {
  name: string
  faction: string
  rarity: string
  forms: HeroDataForm[]
  aura?: HeroAura[]
  ascension?: Record<string, { stats: number[][]; skills: number[][] }>
}
// A buff or debuff type: the game's kind, name and description (client
// language), and its strength when the kind comes in several (25% / 50%).
export type StatusEffectData = { native?: string; kind?: string; name?: string; desc?: string; strength?: number }
export type HeroData = {
  schema: number
  heroes: Record<string, HeroDataHero>
  skills: Record<string, HeroDataSkill>
  statusEffects: Record<string, StatusEffectData>
  // The game client's names: faction, rarity, element, role, stat, area.
  labels?: Record<string, Record<string, string>>
}
// One of the account's champions (agent roster request: basic data only).
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
export type EffectLike = { token: string; icon: string; iconReady?: boolean; label: string; labelEn?: string; group?: string }

// A runtime hero type id is the champion's base id plus its ascension level (1-6).
export function ascensionOf(typeId: number) {
  const level = typeId % 10
  return level >= 1 && level <= 6 ? level : 0
}
export const championId = (typeId: number) => typeId - ascensionOf(typeId)

export type ChampionKit = { id: number; hero: HeroDataHero; ascension: number; forms: HeroDataForm[]; newSkills: Set<number> }

// The champion's forms with the stats and skills of its ascension level; skills
// gained by ascending are listed in newSkills.
export function championKit(data: HeroData, typeId: number): ChampionKit | null {
  const id = championId(typeId)
  const hero = data.heroes[String(id)]
  if (!hero) return null
  const ascension = ascensionOf(typeId)
  const level = ascension ? hero.ascension?.[String(ascension)] : undefined
  const newSkills = new Set<number>()
  const forms = hero.forms.map((form, index) => {
    const added = level?.skills[index] ?? []
    added.forEach((skill) => newSkills.add(skill))
    return { ...form, stats: level?.stats[index] ?? form.stats, skills: [...form.skills, ...added.filter((skill) => !form.skills.includes(skill))] }
  })
  return { id, hero, ascension, forms, newSkills }
}

// Search tags of a champion at its ascension level, built once per data set.
const tagCache = new WeakMap<HeroData, Map<number, Set<string>>>()
export function championTags(data: HeroData, typeId: number): Set<string> {
  let byType = tagCache.get(data)
  if (!byType) tagCache.set(data, byType = new Map())
  const cached = byType.get(typeId)
  if (cached) return cached
  const tags = new Set<string>()
  const kit = championKit(data, typeId)
  if (kit) {
    tags.add(`faction:${kit.hero.faction}`)
    for (const aura of kit.hero.aura ?? []) tags.add(`aura:${aura.stat}`)
    for (const form of kit.forms) {
      tags.add(`element:${form.element}`)
      tags.add(`role:${form.role}`)
      for (const id of form.skills) {
        const skill = data.skills[String(id)]
        if (!skill) continue
        for (const scaling of skill.scaling ?? []) tags.add(`scaling:${scaling}`)
        if (skill.aoe) tags.add('aoe')
        for (const [effect] of skill.buffs ?? []) tags.add(`buff:${effect}`)
        for (const [effect] of skill.debuffs ?? []) tags.add(`debuff:${effect}`)
        for (const name of skill.special ?? []) tags.add(`special:${name}`)
      }
    }
  }
  byType.set(typeId, tags)
  return tags
}

// "Decrease ATK 25%" and "Decrease ATK 50%" are one search option.
export const withoutStrength = (text: string) => text.replace(/\s*\d+(?:[.,]\d+)?%/g, '').replace(/\s*[（(]\s*[）)]/g, '').trim()

export function readableEnum(name: string) {
  return name.replace(/(?<=[a-z0-9])(?=[A-Z])/g, ' ')
}

// A status effect's name, search group, description and icon. Chinese names
// are the game's own; other languages come from the message catalog; the
// icon from the backend's effect list, else the game's sprite named after the kind.
export function effectInfo(effects: Map<string, EffectLike>, data: HeroData, typeId: number, locale: Locale) {
  const option = effects.get(String(typeId))
  const game = data.statusEffects[String(typeId)] ?? {}
  const native = game.native ?? `Effect ${typeId}`
  const strength = game.strength ? ` ${game.strength}%` : ''
  const name = locale !== 'zh-CN'
    ? option ? withoutStrength(effectName(option, locale)) : readableEnum(game.kind ?? native.replace(/\d+p?$/, ''))
    : game.name ?? (option ? withoutStrength(option.label) : readableEnum(native))
  // Strengths of one kind search together; differently named variants (Fear,
  // True Fear) stay apart.
  const group = game.name ?? (option ? withoutStrength(option.label) : native.replace(/\d+p?$/, ''))
  const icon = option?.icon ?? game.kind
  return { label: `${name}${strength}`, group, groupLabel: name, description: game.desc ?? '',
    icon, iconReady: option ? option.iconReady !== false : Boolean(icon) }
}

let pending: Promise<HeroData | null> | null = null
let loaded: HeroData | null = null

// The champion data, fetched once per app session; while the offline engine
// is still reading it the request is repeated, and null means unavailable.
export function loadHeroData(fetchSnapshot: () => Promise<{ status: string; data?: HeroData; reason?: string }>,
                             onWaiting?: () => void): Promise<HeroData | null> {
  if (loaded) return Promise.resolve(loaded)
  if (!pending) {
    pending = (async () => {
      for (let attempt = 0; attempt < 120; attempt++) {
        const snapshot = await fetchSnapshot()
        if (snapshot.status === 'ready' && snapshot.data) return (loaded = snapshot.data)
        if (snapshot.status !== 'building') return null
        onWaiting?.()
        await new Promise((resolve) => setTimeout(resolve, 1500))
      }
      return null
    })().finally(() => { pending = null })
  }
  return pending
}

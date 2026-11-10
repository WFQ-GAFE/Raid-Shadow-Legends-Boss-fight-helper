import { championId, ascensionOf, type HeroData, type HeroDataSkill, type RosterHero } from './heroData'

// Which of the account's champions can put on the effects a Chimera trial asks
// for: a reference only. A champion is listed when a skill at its ascension level
// applies one of them (the game's static data); whether that lands at the right
// moment, often enough, only a simulation can tell.

type EffectSelector = { effectTypeId: number }
// One way to progress a trial named by its recipe's "sources" (any of them helps): skills
// that apply one of these buffs or debuffs, have a special effect (Revive, a passive
// Counterattack...), or deal damage based on a stat (TRG_HP: the enemy's max HP).
export type TrialSource = { buff?: number[]; debuff?: number[]; special?: string; scaling?: string }
// A trial's recipe (data/chimera-trial-recipes.json, sent with the catalog): who must carry which effects.
export type TrialRecipe = {
  trialId: number
  actionGoal?: string
  requirements?: { scope: string; effect?: EffectSelector; anyOf?: EffectSelector[] }[]
  sources?: TrialSource[]
  minimumBossDebuffs?: number
  minimumActiveHeroBuffs?: number
  maximumBossBuffs?: number
  minimumLivingAllies?: number
  forbiddenBossEffectTypeIds?: number[]
  difficultyOverrides?: Record<string, Partial<Record<TrialThreshold, number>>>
}
export type TrialThreshold = 'minimumBossDebuffs' | 'minimumActiveHeroBuffs' | 'maximumBossBuffs' | 'minimumLivingAllies'
const THRESHOLDS: TrialThreshold[] = ['minimumBossDebuffs', 'minimumActiveHeroBuffs', 'maximumBossBuffs', 'minimumLivingAllies']

// One skill that applies a wanted effect, as the static data lists it; or, with trait
// ("special:Revive", "scaling:TRG_HP"), one that has a wanted property (effectTypeId 0).
export type ProviderSkill = { skillTypeId: number; effectTypeId: number; turns: number; chance: number | null; scope: string
  conditional: boolean; cooldown?: number; passive: boolean; trait?: string }
export type TrialProvider = { hero: RosterHero; inTeam: boolean; skills: ProviderSkill[] }
// on: boss = a debuff on the Chimera; actor = a buff on the champion who acts; ally = a buff on any ally;
// any = one of the trial's sources (effects and traits).
export type TrialNeed = { on: 'boss' | 'actor' | 'ally' | 'any'; effectTypeIds: number[]; traits?: string[]; providers: TrialProvider[] }
export type TrialReference = { trialId: number; recipeFound: boolean; goal?: string; needs: TrialNeed[]
  sources: TrialNeed | null; sourceList: TrialSource[]; thresholds: Partial<Record<TrialThreshold, number>>; forbidden: number[] }

// Recipes are written once (Ultra-Nightmare ids, 8000500 + n); other difficulties change only thresholds.
const CANONICAL_DIFFICULTY = 5

export function trialRecipe(recipes: TrialRecipe[], trialId: number) {
  const offset = trialId - 8_000_000
  const difficulty = Math.floor(offset / 100)
  const ordinal = offset % 100
  const canonical = difficulty >= 1 && difficulty <= 6 && ordinal >= 1 && ordinal <= 27
    ? 8_000_000 + CANONICAL_DIFFICULTY * 100 + ordinal : trialId
  const recipe = recipes.find((item) => item.trialId === canonical)
  if (!recipe) return null
  const thresholds: Partial<Record<TrialThreshold, number>> = {}
  for (const key of THRESHOLDS) {
    const value = recipe.difficultyOverrides?.[String(difficulty)]?.[key] ?? recipe[key]
    if (typeof value === 'number') thresholds[key] = value
  }
  return { recipe, thresholds }
}

// Every skill a champion has at its ascension level, in all its forms.
export function championSkills(data: HeroData, typeId: number): number[] {
  const hero = data.heroes[String(championId(typeId))]
  if (!hero) return []
  const forms = hero.catalogForms?.[String(ascensionOf(typeId))] ?? hero.catalogForms?.['0'] ?? hero.forms.map((form) => form.skills)
  return [...new Set(forms.flat())]
}

// A hidden part of a skill is listed under the skill the game shows for it.
const shownSkill = (skillTypeId: number, skill: HeroDataSkill | undefined) => skill?.hidden && skill.levelOf ? skill.levelOf : skillTypeId

// Special effects named like a status effect in the game's texts (the client's language).
const SPECIAL_EFFECT_NAMES: Record<string, number> = { Counterattack: 50, ReflectDamage: 410 }

// How the game's descriptions say a passive part uses the skill itself ("Whenever an enemy
// attacks..., uses this skill to attack that enemy"), in the client languages players use.
const SELF_REFERENCES = ['使用此技能', 'use this skill', 'uses this skill', 'esta habilidade', 'esta Habilidade']

// A hidden part that names no skill (levelOf): the champion's one visible skill whose
// description names the effect, else the one that says it uses itself (Lydia's A1).
function ownerSkill(data: HeroData, typeId: number, skillTypeId: number, effectTypeId: number) {
  const skill = data.skills[String(skillTypeId)]
  if (!skill?.hidden || skill.levelOf) return shownSkill(skillTypeId, skill)
  const visible = championSkills(data, typeId).filter((id) => !data.skills[String(id)]?.hidden)
  const text = (id: number) => (data.skills[String(id)]?.desc ?? '').replace(/<[^>]+>/g, '')
  const name = data.statusEffects[String(effectTypeId)]?.name
  for (const words of [name ? [name] : [], SELF_REFERENCES]) {
    const owners = visible.filter((id) => words.some((word) => text(id).includes(word)))
    if (owners.length === 1) return owners[0]
  }
  return skillTypeId
}

function applied(data: HeroData, typeId: number, rawSkillTypeId: number, wanted: Set<number>, kind: 'buffs' | 'debuffs') {
  const skill = data.skills[String(rawSkillTypeId)]
  const found: ProviderSkill[] = []
  for (const [effectTypeId, turns, chance, scope, conditional] of skill?.[kind] ?? []) {
    // A debuff counts when it can land on the Chimera, not only on the caster.
    if (!wanted.has(effectTypeId) || (kind === 'debuffs' && scope === 'self')) continue
    if (found.some((item) => item.effectTypeId === effectTypeId)) continue
    found.push({ skillTypeId: ownerSkill(data, typeId, rawSkillTypeId, effectTypeId), effectTypeId, turns, chance, scope, conditional,
      cooldown: skill?.cd, passive: skill?.passive === true })
  }
  return found
}

function providerSkills(data: HeroData, typeId: number, wanted: Set<number>, kind: 'buffs' | 'debuffs') {
  return championSkills(data, typeId).flatMap((skillTypeId) => applied(data, typeId, skillTypeId, wanted, kind))
}

export const sourceTrait = (source: TrialSource) => source.special ? `special:${source.special}`
  : source.scaling ? `scaling:${source.scaling}` : ''

// The skills of a champion that give one of the trial's sources.
function sourceSkills(data: HeroData, typeId: number, sources: TrialSource[]) {
  const buffs = new Set(sources.flatMap((source) => source.buff ?? []))
  const debuffs = new Set(sources.flatMap((source) => source.debuff ?? []))
  const traits = sources.filter((source) => source.special || source.scaling)
  const found: ProviderSkill[] = []
  for (const skillTypeId of championSkills(data, typeId)) {
    const skill = data.skills[String(skillTypeId)]
    found.push(...applied(data, typeId, skillTypeId, buffs, 'buffs'), ...applied(data, typeId, skillTypeId, debuffs, 'debuffs'))
    for (const source of traits) {
      const has = source.special
        ? (skill?.special ?? []).includes(source.special)
        : (skill?.scaling ?? []).includes(source.scaling!)
      if (!has) continue
      const trait = sourceTrait(source)
      const shown = ownerSkill(data, typeId, skillTypeId, SPECIAL_EFFECT_NAMES[source.special ?? ''] ?? 0)
      if (found.some((item) => item.trait === trait && item.skillTypeId === shown)) continue
      found.push({ skillTypeId: shown, effectTypeId: 0, turns: 0, chance: null, scope: '', conditional: false,
        cooldown: skill?.cd, passive: skill?.passive === true, trait })
    }
  }
  return found
}

// Champions in the strategy's team first, then those outside the vaults, strongest first (the roster's order).
function rank(left: TrialProvider, right: TrialProvider, order: Map<number, number>) {
  return Number(right.inTeam) - Number(left.inTeam)
    || Number(left.hero.vault || left.hero.reserve) - Number(right.hero.vault || right.hero.reserve)
    || (order.get(left.hero.id) ?? 0) - (order.get(right.hero.id) ?? 0)
}

export function trialHeroReference(trialIds: number[], recipes: TrialRecipe[], data: HeroData, roster: RosterHero[],
                                   team: { heroIds: number[]; typeIds: number[] }): TrialReference[] {
  const order = new Map(roster.map((hero, index) => [hero.id, index]))
  const inTeam = (hero: RosterHero) => team.heroIds.length ? team.heroIds.includes(hero.id)
    : team.typeIds.some((typeId) => championId(typeId) === championId(hero.typeId))
  const providers = (skillsOf: (hero: RosterHero) => ProviderSkill[]) => roster
    .map((hero) => ({ hero, inTeam: inTeam(hero), skills: skillsOf(hero) }))
    .filter((provider) => provider.skills.length > 0)
    .sort((left, right) => rank(left, right, order))
  return trialIds.map((trialId) => {
    const found = trialRecipe(recipes, trialId)
    if (!found) return { trialId, recipeFound: false, needs: [], sources: null, sourceList: [], thresholds: {}, forbidden: [] }
    const needs: TrialNeed[] = (found.recipe.requirements ?? []).map((requirement) => {
      const effectTypeIds = [...(requirement.effect ? [requirement.effect] : []), ...(requirement.anyOf ?? [])]
        .map((selector) => selector.effectTypeId).filter((value) => typeof value === 'number')
      const on: TrialNeed['on'] = requirement.scope === 'boss' ? 'boss' : requirement.scope === 'activeHero' ? 'actor' : 'ally'
      const wanted = new Set(effectTypeIds)
      return { on, effectTypeIds, providers: providers((hero) => providerSkills(data, hero.typeId, wanted, on === 'boss' ? 'debuffs' : 'buffs')) }
    })
    const list = found.recipe.sources ?? []
    const sources: TrialNeed | null = list.length ? {
      on: 'any',
      effectTypeIds: list.flatMap((source) => [...(source.debuff ?? []), ...(source.buff ?? [])]),
      traits: list.map(sourceTrait).filter(Boolean),
      providers: providers((hero) => sourceSkills(data, hero.typeId, list)),
    } : null
    return { trialId, recipeFound: true, goal: found.recipe.actionGoal, needs, sources, sourceList: list, thresholds: found.thresholds,
      forbidden: found.recipe.forbiddenBossEffectTypeIds ?? [] }
  })
}

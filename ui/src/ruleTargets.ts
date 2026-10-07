import type { HeroData } from './heroData'
import type { MessageKey } from './i18n'

// Rule targets a skill can never be cast on. The game's SkillTargets
// (tools/hero_data.py keeps them per skill) say who a skill reaches; the
// controller's select_target then picks among those, so a rule aiming an
// enemy-only skill at the champion itself never acts. The kinds are the
// controller's target_reach ones and the target.reach.* messages; "absent"
// (reports only) is a target of a kind the skill takes that was not there then.
export type TargetReach = 'enemy' | 'ally' | 'otherAlly' | 'self' | 'deadAlly' | 'any' | 'none' | 'absent'

const REACH_BY_TARGETS: Record<string, TargetReach> = {
  Producer: 'self',
  AliveAllies: 'ally', AllAllies: 'ally',
  AliveAlliesExceptProducer: 'otherAlly', AllAlliesExceptProducer: 'otherAlly',
  AliveEnemies: 'enemy', AllEnemies: 'enemy', DeadEnemies: 'enemy', AliveEnemiesIncludeInvisible: 'enemy',
  DeadAllies: 'deadAlly', DeadAlliesIncludeBlockRevive: 'deadAlly',
  AliveHeroes: 'any',
}

const ENEMY_TARGETS = new Set(['boss', 'lowestHpBoss', 'lowestDefenseBoss', 'hydraHeadPriority', 'hydraHeadSlot', 'devouringHead', 'exposedNeck'])
// Rule targets that name one ally (a team slot or a champion): whether that is the caster decides some cases.
const NAMED_ALLY_TARGETS = new Set(['allyPosition', 'allyHeroTypeId'])

// The rule editor's names for the targets (the controller's TARGET_LABEL_KEYS).
export const TARGET_LABEL_KEYS: Record<string, MessageKey> = {
  auto: 'app.automaticLegalTarget', self: 'app.self', lowestHpAlly: 'app.lowestHpAlly', allyPosition: 'app.allyInSlot',
  allyHeroTypeId: 'app.specifiedAlly', boss: 'app.chimeraBoss', lowestHpBoss: 'app.lowestHpHead',
  lowestDefenseBoss: 'app.lowestDefHead', hydraHeadPriority: 'app.headTypePriorityLowestHp',
  hydraHeadSlot: 'app.lowestHpHeadOldSlot', devouringHead: 'app.devouringHead', exposedNeck: 'app.exposedNeck',
}

export function skillReach(data: HeroData | null | undefined, skillTypeId: number | undefined): TargetReach | undefined {
  if (!data || typeof skillTypeId !== 'number') return undefined
  const targets = data.skills[String(skillTypeId)]?.targets
  return targets ? REACH_BY_TARGETS[targets] : undefined
}

// Whether the rule target can ever be chosen for a skill reaching `reach`.
// atCaster: the named ally (slot or champion) is the caster itself; undefined
// when the team is not known, and then a named ally is not judged.
export function targetFits(reach: TargetReach, target: string, atCaster?: boolean): boolean {
  if (target === 'auto' || reach === 'any' || reach === 'none' || reach === 'absent' || !(target in TARGET_LABEL_KEYS)) return true
  if (reach === 'enemy') return ENEMY_TARGETS.has(target)
  if (ENEMY_TARGETS.has(target) || reach === 'deadAlly') return false
  if (reach === 'ally') return true
  if (reach === 'self') return target === 'self' || target === 'lowestHpAlly' || (NAMED_ALLY_TARGETS.has(target) && atCaster !== false)
  // Other allies only.
  return target === 'lowestHpAlly' || (NAMED_ALLY_TARGETS.has(target) && atCaster !== true)
}

// A rule target the skill can never take: what the warnings show.
export type TargetMismatch = {
  skillTypeId: number; reach: TargetReach; target: string; position?: number
  // The caster, and the champion an allyHeroTypeId target names.
  heroTypeId?: number; targetHeroTypeId?: number
}

type Selector = { type?: unknown; position?: unknown; heroTypeId?: unknown }
type RuleLike = { when?: Record<string, unknown>; action?: Record<string, unknown> }

function selectorOf(raw: unknown): Selector {
  return typeof raw === 'string' ? { type: raw } : raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Selector : {}
}

function mismatch(data: HeroData, skillTypeId: unknown, raw: unknown, atCaster: (selector: Selector) => boolean | undefined,
                  heroTypeId?: number): TargetMismatch | null {
  if (typeof skillTypeId !== 'number') return null
  const selector = selectorOf(raw)
  const target = typeof selector.type === 'string' ? selector.type : 'boss'
  const reach = skillReach(data, skillTypeId)
  if (!reach || targetFits(reach, target, atCaster(selector))) return null
  return { skillTypeId, reach, target, heroTypeId, ...(typeof selector.position === 'number' ? { position: selector.position } : {}),
    ...(typeof selector.heroTypeId === 'number' ? { targetHeroTypeId: selector.heroTypeId } : {}) }
}

// A rule's targets that its skills can never take: the cast skill's, or each
// skill's in a default skill order (those fall back to the automatic target).
// team: the team's champion type ids by slot; isCaster: whether a type id is
// the rule's champion.
export function ruleTargetMismatches(data: HeroData | null | undefined, rule: RuleLike, team: number[],
                                     isCaster: (typeId: number) => boolean, heroTypeId?: number): TargetMismatch[] {
  const action = rule.action ?? {}
  if (!data) return []
  const atCaster = (selector: Selector) => {
    if (selector.type === 'allyPosition' && typeof selector.position === 'number') {
      const typeId = team[selector.position - 1]
      return typeId ? isCaster(typeId) : undefined
    }
    if (selector.type === 'allyHeroTypeId' && typeof selector.heroTypeId === 'number') return isCaster(selector.heroTypeId)
    return undefined
  }
  if (action.type === 'cast' || action.type === undefined) {
    const found = mismatch(data, action.skillTypeId, action.target, atCaster, heroTypeId)
    return found ? [found] : []
  }
  if (action.type !== 'defaultSkillPriority') return []
  const policies = action.formPolicies && typeof action.formPolicies === 'object' && !Array.isArray(action.formPolicies)
    ? Object.values(action.formPolicies as Record<string, unknown>) : [action]
  const found = new Map<string, TargetMismatch>()
  for (const policy of policies) {
    const { prioritySkills, firstTurnSkill } = policy && typeof policy === 'object' ? policy as Record<string, unknown> : {}
    for (const entry of [...(Array.isArray(prioritySkills) ? prioritySkills : []), firstTurnSkill]) {
      if (!entry || typeof entry !== 'object' || (entry as Record<string, unknown>).isTransform) continue
      const { skillTypeId, target } = entry as Record<string, unknown>
      const item = mismatch(data, skillTypeId, target ?? 'auto', atCaster, heroTypeId)
      if (item) found.set(`${item.skillTypeId}:${item.target}:${item.position ?? ''}`, item)
    }
  }
  return [...found.values()]
}

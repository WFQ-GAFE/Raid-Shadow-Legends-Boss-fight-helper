// Rules shown and kept per champion (the rules card in App.tsx).
//
// The controller checks the acting champion's strict rules in list order, then
// trial automation, then default skill orders (chimera_controller.evaluate, both
// bosses), and each rule names its one champion. So the order between champions
// changes nothing: a strategy can be kept grouped by champion, in team order, with
// each champion's rules in the order they are checked.

export type RuleLike = { when?: Record<string, unknown>; action?: Record<string, unknown> }
export type RuleKind = 'strict' | 'trial' | 'default'
export type RuleEntry<R> = { rule: R; index: number }
// champion: the base hero type id; null for rules of any acting champion (or none).
export type RuleGroup<R> = { champion: number | null; inTeam: boolean; entries: RuleEntry<R>[] }

const KIND_RANK: Record<RuleKind, number> = { strict: 0, trial: 1, default: 2 }

export function ruleKind(rule: RuleLike): RuleKind {
  const type = rule.action?.type
  return type === 'defaultSkillPriority' ? 'default' : type === 'executeTrialRecipe' ? 'trial' : 'strict'
}

// A runtime hero type id is the champion's base id plus its ascension level (1-6).
export const championBase = (typeId: number) => typeId - (typeId % 10 >= 1 && typeId % 10 <= 6 ? typeId % 10 : 0)

export function ruleChampion(rule: RuleLike): number | null {
  const raw = rule.when?.activeHeroTypeId
  const ids = (Array.isArray(raw) ? raw : [raw]).filter((value): value is number => typeof value === 'number' && value > 0)
  const bases = new Set(ids.map(championBase))
  return bases.size === 1 ? [...bases][0] : null
}

// The team's champions in team order (each once), each with its rules in the order they are
// checked; then champions outside the team, as they first appear; rules of any champion first.
export function groupRules<R extends RuleLike>(rules: R[], team: number[]): RuleGroup<R>[] {
  const groups = new Map<number | null, RuleGroup<R>>()
  for (const typeId of team) {
    if (typeId > 0 && !groups.has(championBase(typeId))) groups.set(championBase(typeId), { champion: championBase(typeId), inTeam: true, entries: [] })
  }
  rules.forEach((rule, index) => {
    const champion = ruleChampion(rule)
    if (!groups.has(champion)) groups.set(champion, { champion, inTeam: false, entries: [] })
    groups.get(champion)!.entries.push({ rule, index })
  })
  for (const group of groups.values()) {
    group.entries.sort((left, right) => KIND_RANK[ruleKind(left.rule)] - KIND_RANK[ruleKind(right.rule)] || left.index - right.index)
  }
  const list = [...groups.values()]
  return [...list.filter((group) => group.champion === null), ...list.filter((group) => group.champion !== null)]
}

// The rules grouped as groupRules shows them (the same array when already so). A rule of
// any acting champion is checked between the others by its place, so then nothing moves.
export function normalizeRuleOrder<R extends RuleLike>(rules: R[], team: number[]): R[] {
  if (rules.some((rule) => ruleChampion(rule) === null)) return rules
  const ordered = groupRules(rules, team).flatMap((group) => group.entries.map((entry) => entry.rule))
  return ordered.every((rule, index) => rule === rules[index]) ? rules : ordered
}

// Swap a rule with the previous / next rule of the same champion and kind; null at either end.
export function moveInGroup<R extends RuleLike>(rules: R[], index: number, direction: -1 | 1, team: number[]): R[] | null {
  const rule = rules[index]
  if (!rule) return null
  const group = groupRules(rules, team).find((item) => item.entries.some((entry) => entry.index === index))
  const peers = group?.entries.filter((entry) => ruleKind(entry.rule) === ruleKind(rule)) ?? []
  const at = peers.findIndex((entry) => entry.index === index)
  const other = peers[at + direction]
  if (at < 0 || !other) return null
  const next = [...rules]
  ;[next[index], next[other.index]] = [next[other.index], next[index]]
  return next
}

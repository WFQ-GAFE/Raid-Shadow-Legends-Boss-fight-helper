// UI descriptions only: preserve every saved leaf, quantifier and negation.
// Resolvers return undefined for absent catalog entries; identities stay visible
// instead of guessing a champion, skill, effect or target.
type ObjectValue = Record<string, unknown>
export type DescriptionText = (key: string, params?: Record<string, string | number>) => string
export type RuleDescriptionContext = {
  text: DescriptionText
  heroName?: (id: number) => string | undefined
  skillName?: (heroId: number | undefined, skillId: number) => string | undefined
  effectName?: (token: string) => string | undefined
  trialName?: (id: number) => string | undefined
  formName?: (form: string) => string | undefined
  // The boss's forms; a saved list naming all of them restricts nothing.
  allForms?: string[]
}

function object(value: unknown): ObjectValue | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : undefined
}
function number(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}
function values(value: unknown): unknown[] { return Array.isArray(value) ? value : [value] }
function literal(value: unknown): string { return value === undefined ? '?' : JSON.stringify(value) ?? String(value) }

function hero(value: unknown, position: unknown, ctx: RuleDescriptionContext): string {
  const id = number(value)
  const name = id === undefined ? value === undefined ? ctx.text('rules.subjectUnspecified') : ctx.text('rules.savedChampionSelector', { value: literal(value) })
    : ctx.heroName?.(id) ?? ctx.text('rules.championId', { id })
  return number(position) === undefined ? name : ctx.text('rules.heroAtPosition', { name, position: Number(position) })
}
function subject(node: ObjectValue, ctx: RuleDescriptionContext): string {
  if (node.target === 'ally') return hero(node.heroTypeId, node.teamPosition, ctx)
  const keys: Record<string, string> = {
    boss: 'rules.boss', bossPriority: 'rules.priorityHead', bossAny: 'rules.anyHead', bossAll: 'rules.allHeads',
  }
  return typeof node.target === 'string' && keys[node.target] ? ctx.text(keys[node.target])
    : ctx.text('rules.unknownTarget', { target: literal(node.target) })
}
function range(min: unknown, max: unknown, ctx: RuleDescriptionContext): string {
  if (min !== undefined && max !== undefined && min === max) return ctx.text('rules.rangeExact', { value: literal(min) })
  if (min !== undefined && max !== undefined) return ctx.text('rules.rangeBetween', { min: literal(min), max: literal(max) })
  if (min !== undefined) return ctx.text('rules.rangeAtLeast', { value: literal(min) })
  if (max !== undefined) return ctx.text('rules.rangeAtMost', { value: literal(max) })
  return ctx.text('rules.rangeUnspecified')
}
function effect(value: unknown, ctx: RuleDescriptionContext): string {
  const selector = object(value)
  const rawToken = selector ? selector.effectTypeId ?? selector.kind : value
  const token = rawToken === undefined ? '' : String(rawToken)
  let label = token ? ctx.effectName?.(token) ?? ctx.text('rules.effectId', { token }) : ctx.text('rules.effectUnspecified')
  if (selector?.turnsAtLeast !== undefined || selector?.turnsAtMost !== undefined) {
    label = ctx.text('rules.effectWithTurns', { effect: label, range: range(selector.turnsAtLeast, selector.turnsAtMost, ctx) })
  }
  const extra = selector ? Object.keys(selector).filter(key => !['effectTypeId', 'kind', 'turnsAtLeast', 'turnsAtMost'].includes(key)) : []
  if (selector && extra.length) label += ` ${ctx.text('rules.savedDetails', { value: literal(Object.fromEntries(extra.map(key => [key, selector[key]]))) })}`
  return label
}
// Items joined by "and" / "or", each named once (a champion's rank variants share one name).
// Brackets only where they are needed to read the logic: around a group of several items
// inside another sentence.
function join(children: string[], operator: unknown, ctx: RuleDescriptionContext, bracket = false): string {
  const unique = [...new Set(children)]
  if (unique.length <= 1) return unique[0] ?? ''
  const text = unique.join(` ${ctx.text(operator === 'any' ? 'rules.or' : operator === 'list' ? 'rules.listAnd' : 'rules.and')} `)
  return bracket ? `(${text})` : text
}

// A group's own children, with nested groups of the same operator merged into it.
function groupChildren(node: ObjectValue): unknown[] {
  return (node.children as unknown[]).flatMap((child) => {
    const inner = object(child)
    return inner?.type === 'group' && inner.operator === node.operator && inner.negate !== true
      && Array.isArray(inner.children) && inner.children.length ? groupChildren(inner) : [child]
  })
}

// A plain "has" for one subject, which merges with the group's others of that subject.
function presentEffect(raw: unknown, ctx: RuleDescriptionContext): { subject: string; effect: string } | undefined {
  const node = object(raw)
  if (node?.type !== 'effect' || node.presence !== 'has' || node.negate === true) return undefined
  return { subject: subject(node, ctx), effect: effect(node.effect, ctx) }
}

// The phrases of a group's children: "Boss has A or B" instead of "Boss has A or
// Boss has B". Inside a sentence (nested) a child group of the other operator is bracketed.
function groupLabels(node: ObjectValue, ctx: RuleDescriptionContext, nested: boolean): string[] {
  const merged = new Map<string, string[]>()
  const labels: (string | { subject: string })[] = []
  for (const child of groupChildren(node)) {
    const present = presentEffect(child, ctx)
    if (!present) labels.push(describeConditionTree(child, ctx, nested))
    else {
      if (!merged.has(present.subject)) { merged.set(present.subject, []); labels.push({ subject: present.subject }) }
      merged.get(present.subject)?.push(present.effect)
    }
  }
  return labels.map(item => typeof item === 'string' ? item : ctx.text('rules.effectPresent',
    { subject: item.subject, effect: join(merged.get(item.subject) ?? [], node.operator === 'any' ? 'any' : 'list', ctx) }))
}

export function describeConditionTree(raw: unknown, ctx: RuleDescriptionContext, nested = false): string {
  const node = object(raw)
  if (!node) return ctx.text('rules.unknownCondition', { value: literal(raw) })
  let label: string
  if (node.type === 'group') {
    if (!['all', 'any'].includes(String(node.operator)) || !Array.isArray(node.children) || !node.children.length) {
      return ctx.text('rules.unknownCondition', { value: literal(raw) })
    }
    const children = groupChildren(node)
    const only = object(children[0])
    if (children.length === 1 && only) return describeConditionTree(node.negate === true ? { ...only, negate: only.negate !== true } : only, ctx, nested)
    // NOT already brackets what it negates. Several children merged into one
    // phrase ("Boss has A or B") still need brackets inside another sentence.
    const labels = groupLabels(node, ctx, true)
    const bracket = nested && node.negate !== true
    label = labels.length === 1 && bracket ? `(${labels[0]})` : join(labels, node.operator, ctx, bracket)
  } else if (node.type === 'heroState') {
    // A negated state reads as its opposite ("not dead" = "alive").
    const dead = node.state === 'dead' ? node.negate !== true : node.state === 'alive' ? node.negate === true : undefined
    return dead === undefined ? ctx.text('rules.unknownCondition', { value: literal(raw) })
      : ctx.text(dead ? 'rules.heroDead' : 'rules.heroAlive', { subject: hero(node.heroTypeId, node.teamPosition, ctx) })
  } else if (node.type === 'skillCooldown') {
    const heroId = number(node.heroTypeId)
    const skillId = number(node.skillTypeId)
    const skill = skillId === undefined ? ctx.text('rules.skillUnspecified')
      : ctx.skillName?.(heroId, skillId) ?? ctx.text('rules.skillId', { id: skillId })
    label = ctx.text('rules.skillCooldown', { subject: hero(node.heroTypeId, node.teamPosition, ctx), skill,
      range: range(node.turnsAtLeast, node.turnsAtMost, ctx) })
  } else if (node.type === 'effectCount') {
    const key = node.polarity === 'buff' ? 'rules.buffCount' : node.polarity === 'debuff' ? 'rules.debuffCount' : node.polarity === 'all' ? 'rules.effectCount' : undefined
    label = key ? ctx.text(key, { subject: subject(node, ctx), range: range(node.countAtLeast, node.countAtMost, ctx) })
      : ctx.text('rules.unknownCondition', { value: literal(raw) })
  } else if (node.type === 'effect') {
    if (node.presence !== 'has' && node.presence !== 'missing') return ctx.text('rules.unknownCondition', { value: literal(raw) })
    // For one subject a negated "has" reads as "does not have" and the other way round;
    // "any head" / "all heads" keep the NOT, since flipping them changes the meaning.
    const flip = node.negate === true && !['bossAny', 'bossAll'].includes(String(node.target))
    const missing = (node.presence === 'missing') !== flip
    label = ctx.text(missing ? 'rules.effectMissing' : 'rules.effectPresent', { subject: subject(node, ctx), effect: effect(node.effect, ctx) })
    if (flip) return label
  } else {
    return ctx.text('rules.unknownCondition', { value: literal(raw) })
  }
  return node.negate === true ? ctx.text('rules.not', { condition: label }) : label
}

// The controller's trial-set predicates (tools/chimera_controller.py) and how each reads.
// "All" lists read "A and B", "any" lists "A or B".
const TRIAL_KEYS: Record<string, [string, 'list' | 'any']> = {
  completedTrialsAll: ['rules.trialsCompleted', 'list'], completedTrialsAny: ['rules.trialsCompleted', 'any'],
  incompleteTrialsAll: ['rules.trialsIncomplete', 'list'],
  startedTrialsAll: ['rules.trialsStarted', 'list'], startedTrialsAny: ['rules.trialsStarted', 'any'],
  activeTrialsAll: ['rules.trialsActive', 'list'], activeTrialsAny: ['rules.trialsActive', 'any'],
  eligibleTrialsAll: ['rules.trialsEligible', 'list'], eligibleTrialsAny: ['rules.trialsEligible', 'any'],
  lockedTrialsAny: ['rules.trialsLocked', 'any'],
  possibleTrialsAll: ['rules.trialsPossible', 'list'], impossibleTrialsAny: ['rules.trialsImpossible', 'any'],
}

// Ordinary predicates outside the tree are ANDed by the controller. Advanced
// predicates without a UI phrase are retained as their exact saved key/value.
export function describeRuleConditions(when: ObjectValue, ctx: RuleDescriptionContext, includeScope = false): { full: string; summary: string } {
  const parts: string[] = []
  const scope = new Set(['activeHeroTypeId', 'form', 'activeHeroFormIndex', 'activeHeroIsMetamorph'])
  const addRaw = (key: string, value: unknown) => parts.push(ctx.text('rules.savedCondition', { key, value: literal(value) }))
  const trial = (id: unknown) => typeof id === 'number' ? ctx.trialName?.(id) ?? ctx.text('rules.trialId', { id }) : literal(id)
  const comparisons: Record<string, [string, string]> = {
    chimeraTurnAtLeast: ['rules.bossTurn', '≥'], chimeraTurnAtMost: ['rules.bossTurn', '≤'],
    turnAtLeast: ['rules.battleTurn', '≥'], turnAtMost: ['rules.battleTurn', '≤'],
    turnsUntilFormChangeAtLeast: ['rules.turnsToFormChange', '≥'], turnsUntilFormChangeAtMost: ['rules.turnsToFormChange', '≤'],
    currentDamageAtLeast: ['rules.currentDamage', '≥'], currentDamageBelow: ['rules.currentDamage', '<'],
    activeHeroHpPctBelow: ['rules.actingHeroHp', '<'], bossHpPctBelow: ['rules.bossHp', '<'],
  }
  // An "all" group's children are clauses of their own; any other node is one clause.
  const addTree = (tree: unknown) => {
    const node = object(tree)
    if (node?.type === 'group' && node.operator === 'all' && node.negate !== true && Array.isArray(node.children) && node.children.length) {
      parts.push(...groupLabels(node, ctx, false))
    } else parts.push(describeConditionTree(tree, ctx))
  }
  for (const [key, value] of Object.entries(when)) {
    if (scope.has(key) && !includeScope) continue
    if (key === 'conditionTree') addTree(value)
    else if (key === 'effectConditions' || key === 'skillCooldownConditions') {
      const modeKey = `${key}Mode`
      if (!Array.isArray(value) || !value.length || (when[modeKey] !== undefined && !['all', 'any'].includes(String(when[modeKey])))) addRaw(key, value)
      else addTree({ type: 'group', operator: when[modeKey] === 'any' ? 'any' : 'all', children: value.map(item => object(item)
        ? { ...object(item), type: key === 'effectConditions' ? 'effect' : 'skillCooldown' } : item) })
    } else if (key === 'effectConditionsMode' || key === 'skillCooldownConditionsMode') {
      if (!(key.replace(/Mode$/, '') in when) || !['all', 'any'].includes(String(value))) addRaw(key, value)
    } else if (TRIAL_KEYS[key]) {
      const [labelKey, operator] = TRIAL_KEYS[key]
      const trials = values(value)
      if (!trials.length) addRaw(key, value)
      else parts.push(ctx.text(labelKey, { trials: join(trials.map(trial), operator, ctx) }))
    } else if (key === 'activeHeroTypeId') {
      parts.push(ctx.text('rules.actingHeroes', { heroes: join(values(value).map(id => hero(id, undefined, ctx)), 'any', ctx) }))
    } else if (key === 'form' || key === 'nextForm') {
      const saved = values(value)
      // Every form listed restricts nothing; say nothing about it.
      if (ctx.allForms?.length && ctx.allForms.every(form => saved.includes(form))) continue
      const forms = saved.map(form => typeof form === 'string' ? ctx.formName?.(form) ?? form : literal(form))
      parts.push(ctx.text(key === 'form' ? 'rules.forms' : 'rules.nextForm', { forms: join(forms, 'any', ctx) }))
    } else if (key === 'activeHeroFormIndex') {
      parts.push(ctx.text('rules.actingHeroForm', { form: value === 0 ? ctx.text('rules.originalForm') : value === 1 ? ctx.text('rules.alternateForm') : literal(value) }))
    } else if (comparisons[key]) {
      const [labelKey, operator] = comparisons[key]
      parts.push(ctx.text('rules.comparison', { subject: ctx.text(labelKey), operator, value: literal(value) }))
    } else if (['bossHasEffects', 'bossMissingEffects', 'activeHeroHasEffects', 'activeHeroMissingEffects', 'bossHasEffect', 'bossMissingEffect', 'activeHeroHasEffect', 'activeHeroMissingEffect'].includes(key)) {
      const subjectName = ctx.text(key.startsWith('boss') ? 'rules.boss' : 'rules.actingHero')
      const effects = values(value).map(item => effect(item, ctx))
      if (key.includes('Missing')) parts.push(...effects.map(name => ctx.text('rules.effectMissing', { subject: subjectName, effect: name })))
      else parts.push(ctx.text('rules.effectPresent', { subject: subjectName, effect: join(effects, 'list', ctx) }))
    } else addRaw(key, value)
  }
  // Clauses are separated more strongly than the "and" / "or" inside one, so an
  // "A or B" clause never needs brackets to show where it ends.
  const clauses = [...new Set(parts)]
  const full = clauses.length ? clauses.join(ctx.text('rules.clauseAnd')) : ctx.text('rules.noExtraConditions')
  // Up to three clauses read in full; past that the first two and a count.
  const summary = clauses.length > 3 ? ctx.text('rules.shortSummary', { first: clauses.slice(0, 2).join(ctx.text('rules.clauseAnd')), count: clauses.length - 2 }) : full
  return { full, summary }
}

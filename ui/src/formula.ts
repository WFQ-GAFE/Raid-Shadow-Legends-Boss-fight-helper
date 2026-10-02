// The game's damage formulas ("3.9*DEF+0.035*TRG_HP", "(targetIsBoss*...)+...")
// as text a player can read in the window's language. A formula is parsed with
// C-like precedence; terms multiplied by conditions become cases ("vs bosses:
// ..."), "x*(1+c*N)" becomes "x, +c% per N", and the game's variables get names.
// The words come from the message catalog (formula.*) through context.t.
import type { MessageKey, MessageParams } from './i18n'

export type FormulaContext = {
  locale: string
  t: (key: MessageKey, params?: MessageParams) => string
  kindName: (kind: string) => string
}
type Node =
  | { t: 'num'; v: number }
  | { t: 'var'; name: string }
  | { t: 'call'; name: string; args: Node[] }
  | { t: 'not'; a: Node }
  | { t: 'neg'; a: Node }
  | { t: 'bin'; op: string; l: Node; r: Node }

const STATS = new Set(['ATK', 'DEF', 'HP', 'SPD', 'ACC', 'RES'])
// The game's variables with a name (formula.var.<name>).
const VARIABLES = new Set(['ATK', 'DEF', 'HP', 'SPD', 'ACC', 'RES', 'B_ATK', 'B_DEF', 'B_HP', 'CUR_HP', 'HP_PERC',
  'TRG_HP', 'REL_TRG_HP', 'TRG_B_HP', 'TRG_CUR_HP', 'TRG_HP_PERC', 'TRG_ATK', 'TRG_DEF', 'TRG_B_ATK', 'TRG_RES', 'REL_TRG_RES',
  'TRG_STAMINA', 'TRG_DEBUFF_COUNT', 'REL_TRG_DEBUFF_COUNT', 'TRG_BUFF_COUNT', 'REL_TRG_BUFF_COUNT', 'BUFF_COUNT', 'DEALT_DMG',
  'EXCESSIVE_DAMAGE', 'EXCESSIVE_HEAL',
  // The hit this damage follows, e.g. "the second hit deals 30% of the first".
  'CALCULATED_DMG',
  'DMG_MUL', 'aliveAlliesCount', 'deadAlliesCount', 'aliveEnemiesCount', 'CHANGED_STAMINA_AMOUNT', 'SKILL_USED_COUNT',
  'CurrentHealMultiplier', 'damageAbsorbedByShield', 'SHIELDS_SUM_VALUE', 'TRG_SHIELDS_SUM_VALUE', 'removedEffectsCount',
  'transferredEffectsCountByRelatedEffect',
  // Unapplied effects are effects a skill removed (the engine's "unapply").
  'unappliedStatusEffectsCountByCurrentSkill', 'sumOfShieldRemovedByUnappliedEffect', 'totalIncreasedTurnsCountBySkill',
  'AppliedDebuffsCountEnemyTeamCurrentTurn'])
// "+10% per debuff on the target" for the counts a bonus grows with (formula.per.<name>).
const PER = new Set(['TRG_DEBUFF_COUNT', 'REL_TRG_DEBUFF_COUNT', 'TRG_BUFF_COUNT', 'REL_TRG_BUFF_COUNT', 'BUFF_COUNT',
  'aliveAlliesCount', 'deadAlliesCount', 'aliveEnemiesCount', 'SKILL_USED_COUNT', 'transferredEffectsCountByRelatedEffect',
  'AppliedDebuffsCountEnemyTeamCurrentTurn', 'unappliedStatusEffectsCountByCurrentSkill', 'removedEffectsCount',
  'totalIncreasedTurnsCountBySkill', 'CHANGED_STAMINA_AMOUNT'])
// Counts in calls: per stack, per effect of a kind, per turn left on effects of a kind.
const COUNT_CALLS = /^(HeroCounterWithId|EffectsAppliedOn(Relation)?TargetCountOfKind|EnemyTeamAppliedEffectsTotalCountOfKind|EnemyTeamEffectsOfKindLifetimeSum)$/
// A count compared with 0: "the target has buffs" / "has none" (formula.has.<name>, formula.hasNone.<name>).
const HAS = new Set(['TRG_BUFF_COUNT', 'REL_TRG_BUFF_COUNT', 'TRG_DEBUFF_COUNT', 'REL_TRG_DEBUFF_COUNT', 'BUFF_COUNT'])
const SELF_STATS = new Set(['ATK', 'DEF', 'HP', 'SPD', 'ACC', 'RES', 'B_ATK', 'B_DEF', 'B_HP'])
// Conditions on what the target is; the relation target is the same hit's target.
const TARGET_KINDS: Record<string, string> = {
  targetIsBoss: 'boss', relationTargetIsBoss: 'boss', targetIsMinion: 'minion', relationTargetIsMinion: 'minion',
  targetIsChest: 'chest', relationTargetIsChest: 'chest',
}
// Kinds of target: formula.kinds.<kind> in case labels ("vs bosses"), formula.kind.<kind> in conditions.
const KINDS = ['boss', 'minion', 'chest', 'specific']
// Flags and their negation (formula.flag.<name>, formula.notFlag.<name>); roles (formula.role.<name>).
const FLAGS = new Set(['producerIsDead', 'targetHasControlDebuff'])
const ROLES = new Set(['Attack_Role', 'Defense_Role', 'Health_Role', 'Support_Role'])
const BOOLEAN_CALLS = new Set(['RelationTargetHasEffectOfKind', 'TargetHasEffectOfKind', 'ProducerHasEffectOfKind'])

// Every message the tables above name, for the catalog test.
export const FORMULA_MESSAGE_KEYS = [
  ...[...VARIABLES].map((name) => `formula.var.${name}`), ...[...PER].map((name) => `formula.per.${name}`),
  ...[...HAS].flatMap((name) => [`formula.has.${name}`, `formula.hasNone.${name}`]),
  ...KINDS.flatMap((kind) => [`formula.kinds.${kind}`, `formula.kind.${kind}`]),
  ...[...FLAGS].flatMap((name) => [`formula.flag.${name}`, `formula.notFlag.${name}`]),
  ...[...ROLES].map((name) => `formula.role.${name}`),
]
// A message whose key is built from a table name.
const named = (context: FormulaContext, key: string, params?: MessageParams) => context.t(key as MessageKey, params)

// ---- Parsing ----

function tokenize(text: string) {
  const tokens: string[] = []
  const pattern = /\s*(\d+\.\d*|\.\d+|\d+|[A-Za-z_][A-Za-z0-9_]*|&&|\|\||==|!=|>=|<=|[-+*/()<>!,])/y
  let index = 0
  while (index < text.length && !/^\s*$/.test(text.slice(index))) {
    pattern.lastIndex = index
    const match = pattern.exec(text)
    if (!match) throw new Error(`Unexpected "${text.slice(index, index + 10)}"`)
    tokens.push(match[1])
    index = pattern.lastIndex
  }
  return tokens
}

export function parseFormula(text: string): Node {
  const tokens = tokenize(text)
  let at = 0
  const peek = () => tokens[at]
  const take = (expected?: string) => {
    const token = tokens[at++]
    if (token === undefined || (expected && token !== expected)) throw new Error(`Expected ${expected ?? 'a value'}`)
    return token
  }
  const binary = (next: () => Node, ops: string[]) => () => {
    let left = next()
    while (ops.includes(peek())) {
      const op = take()
      left = { t: 'bin', op, l: left, r: next() }
    }
    return left
  }
  const primary = (): Node => {
    const token = take()
    if (token === '(') {
      const inner = or()
      take(')')
      return inner
    }
    if (token === '!') return { t: 'not', a: primary() }
    if (token === '-') return { t: 'neg', a: primary() }
    if (/^[\d.]/.test(token)) return { t: 'num', v: Number(token) }
    if (!/^[A-Za-z_]/.test(token)) throw new Error(`Unexpected ${token}`)
    if (peek() === '(') {
      take('(')
      const args: Node[] = []
      if (peek() !== ')') {
        args.push(or())
        while (peek() === ',') { take(','); args.push(or()) }
      }
      take(')')
      return { t: 'call', name: token, args }
    }
    return { t: 'var', name: token }
  }
  const mul = binary(primary, ['*', '/'])
  const add = binary(mul, ['+', '-'])
  const rel = binary(add, ['<', '>', '<=', '>='])
  const eq = binary(rel, ['==', '!='])
  const and = binary(eq, ['&&'])
  const or: () => Node = binary(and, ['||'])
  const tree = or()
  if (at !== tokens.length) throw new Error(`Unexpected ${tokens[at]}`)
  return tree
}

// ---- Helpers ----

const isBoolean = (node: Node): boolean =>
  node.t === 'not' || (node.t === 'bin' && ['&&', '||', '==', '!=', '<', '>', '<=', '>='].includes(node.op))
  || (node.t === 'var' && (node.name in TARGET_KINDS || FLAGS.has(node.name))) || (node.t === 'call' && BOOLEAN_CALLS.has(node.name))

function flatten(node: Node, op: string): Node[] {
  return node.t === 'bin' && node.op === op ? [...flatten(node.l, op), ...flatten(node.r, op)] : [node]
}
const product = (factors: Node[]): Node => factors.length
  ? factors.reduce((left, right) => ({ t: 'bin', op: '*', l: left, r: right }))
  : { t: 'num', v: 1 }
const number = (value: number) => Number(value.toFixed(6))
// A worked-out coefficient or percentage: four significant digits.
const derived = (value: number) => Number(value.toPrecision(4))
// A number as the window's language writes it ("0.035", "0,035"), without grouping.
const decimal = (value: number, context: FormulaContext) =>
  new Intl.NumberFormat(context.locale, { useGrouping: false, maximumFractionDigits: 10 }).format(value)
const same = (left: Node, right: Node) => JSON.stringify(left) === JSON.stringify(right)

// The numeric coefficient and other factors of a product; "x/100" counts as x*0.01.
function terms(node: Node): { c: number; rest: Node[] } {
  let c = 1
  const rest: Node[] = []
  for (const factor of flatten(node, '*')) {
    if (factor.t === 'num') c *= factor.v
    else if (factor.t === 'bin' && factor.op === '/' && factor.r.t === 'num' && factor.r.v !== 0) {
      const inner = terms(factor.l)
      c *= inner.c / factor.r.v
      rest.push(...inner.rest)
    } else rest.push(factor)
  }
  return { c, rest }
}

// What a target condition says about the target: one of kinds, or none of them.
type KindCondition = { positive: boolean; kinds: string[] }
function kindCondition(node: Node): KindCondition | null {
  if (node.t === 'var' && node.name in TARGET_KINDS) return { positive: true, kinds: [TARGET_KINDS[node.name]] }
  if (node.t === 'not') {
    const inner = kindCondition(node.a)
    return inner && inner.kinds.length === 1 ? { positive: !inner.positive, kinds: inner.kinds } : null
  }
  if (node.t === 'bin' && (node.op === '==' || node.op === '!=') && node.l.t === 'var'
      && /BaseTypeId$/.test(node.l.name) && node.r.t === 'num')
    return { positive: node.op === '==', kinds: ['specific'] }
  if (node.t === 'bin' && (node.op === '||' || node.op === '&&')) {
    const left = kindCondition(node.l)
    const right = kindCondition(node.r)
    // "A or B" lists kinds the target is; "not A and not B" kinds it is not.
    const positive = node.op === '||'
    if (left && right && left.positive === positive && right.positive === positive)
      return { positive, kinds: [...new Set([...left.kinds, ...right.kinds])] }
  }
  return null
}

function listText(items: string[], context: FormulaContext, joiner: 'formula.or' | 'formula.listAnd') {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(context.t('formula.listSeparator'))}${context.t(joiner)}${items[items.length - 1]}`
}

function kindOf(node: Node, context: FormulaContext) {
  return node.t === 'var' && node.name.endsWith('_KindId') ? context.kindName(node.name.slice(0, -'_KindId'.length)) : null
}

// ---- Values ----

// The share of HP (0..1) a formula reads, lost ("1-HP_PERC") or left.
function share(node: Node): { lost: boolean; target: boolean } | null {
  const variable = (item: Node) => item.t === 'var' && (item.name === 'HP_PERC' || item.name === 'TRG_HP_PERC') ? item.name : null
  if (variable(node)) return { lost: false, target: variable(node) === 'TRG_HP_PERC' }
  if (node.t === 'bin' && node.op === '-' && node.l.t === 'num' && node.l.v === 1 && variable(node.r))
    return { lost: true, target: variable(node.r) === 'TRG_HP_PERC' }
  return null
}

const isCount = (node: Node) => (node.t === 'var' && PER.has(node.name)) || (node.t === 'call' && COUNT_CALLS.test(node.name)) || share(node) !== null

// The one count among a product's factors and the factors left; a stat (per
// point of SPD) only when nothing else counts.
function splitCount(factors: Node[]): { count: Node; rest: Node[] } | null {
  let at = factors.findIndex(isCount)
  if (at < 0) at = factors.findIndex((factor) => factor.t === 'var' && STATS.has(factor.name))
  if (at < 0 || factors.filter(isCount).length > 1) return null
  return { count: factors[at], rest: factors.filter((_, index) => index !== at) }
}

function valueName(node: Node, context: FormulaContext): string | null {
  const { t } = context
  if (node.t === 'var') {
    if (VARIABLES.has(node.name)) return named(context, `formula.var.${node.name}`)
    if (ROLES.has(node.name)) return named(context, `formula.role.${node.name}`)
    return kindOf(node, context)
  }
  if (node.t === 'call') {
    // What a counter counts differs per champion; the skill description says.
    if (node.name === 'HeroCounterWithId') return t('formula.stacks')
    const kind = node.args[0] ? kindOf(node.args[0], context) : null
    if (kind && /^EffectsAppliedOn(Relation)?TargetCountOfKind$/.test(node.name)) return t('formula.kindOnTarget', { kind })
    if (kind && node.name === 'EnemyTeamAppliedEffectsTotalCountOfKind') return t('formula.kindOnEnemies', { kind })
    if (kind && node.name === 'EnemyTeamEffectsOfKindLifetimeSum') return t('formula.kindTurnsOnEnemies', { kind })
  }
  return null
}

function perText(node: Node, context: FormulaContext): string {
  const { t } = context
  const portion = share(node)
  if (portion) return t('formula.perHpShare', { whose: portion.target ? 'target' : 'self', state: portion.lost ? 'lost' : 'left' })
  if (node.t === 'var' && PER.has(node.name)) return named(context, `formula.per.${node.name}`)
  if (node.t === 'var' && STATS.has(node.name)) return t('formula.perStatPoint', { stat: named(context, `formula.var.${node.name}`) })
  if (node.t === 'call' && node.name === 'HeroCounterWithId') return t('formula.perStack')
  if (node.t === 'call' && node.args[0]) {
    const kind = kindOf(node.args[0], context)
    if (kind && /^EffectsAppliedOn(Relation)?TargetCountOfKind$/.test(node.name)) return t('formula.perKindOnTarget', { kind })
    if (kind && node.name === 'EnemyTeamAppliedEffectsTotalCountOfKind') return t('formula.perKindOnEnemies', { kind })
    if (kind && node.name === 'EnemyTeamEffectsOfKindLifetimeSum') return t('formula.perKindTurnOnEnemies', { kind })
  }
  return t('formula.perValue', { name: render(node, context) })
}

// Bonuses that grow with a count: "x*(1+c*N)", "(k+c*N)*x", "x + y*N + z*M".
// Relative ("+10% per debuff") when a bonus is a share of x itself, else
// absolute ("plus 0.1×ATK per stack"). A share of HP counts per 1%.
type Bonus = { count: Node; ratio?: number; extra?: Node }

function bonusText(bonus: Bonus, context: FormulaContext) {
  const perPercent = share(bonus.count) ? 0.01 : 1
  if (bonus.ratio !== undefined) {
    const percent = decimal(derived(bonus.ratio * perPercent * 100), context)
    return context.t('formula.relativeBonus', { percent, per: perText(bonus.count, context) })
  }
  const extra = terms(bonus.extra!)
  const coefficient = derived(extra.c * perPercent)
  // "plus 1×DEF", not "plus DEF".
  const value = `${coefficient === 1 && extra.rest.length ? '1×' : ''}${render(product([{ t: 'num', v: coefficient }, ...extra.rest]), context, true)}`
  return context.t('formula.absoluteBonus', { value, per: perText(bonus.count, context) })
}

function withBonuses(base: Node, bonuses: Bonus[], context: FormulaContext) {
  return [render(base, context, true), ...bonuses.map((bonus) => bonusText(bonus, context))].join(context.t('formula.clauseSeparator'))
}

function sameFactors(left: Node[], right: Node[]) {
  if (left.length !== right.length) return false
  const remaining = [...right]
  return left.every((factor) => {
    const at = remaining.findIndex((item) => same(item, factor))
    if (at < 0) return false
    remaining.splice(at, 1)
    return true
  })
}

function growth(node: Node, context: FormulaContext): string | null {
  if (node.t === 'bin' && node.op === '*') {
    const factors = flatten(node, '*')
    for (const [index, factor] of factors.entries()) {
      if (factor.t !== 'bin' || factor.op !== '+' || factor.l.t !== 'num') continue
      const others = factors.filter((_, at) => at !== index)
      const bonus = terms(factor.r)
      const split = splitCount(bonus.rest)
      if (!others.length || !split || split.rest.length || others.some(isCount)) continue
      const k = factor.l.v
      const baseOthers = product(others)
      if (k === 1) return withBonuses(baseOthers, [{ count: split.count, ratio: bonus.c }], context)
      const base = product([factor.l, ...others])
      if (flatten(factor.r, '*').some((item) => item.t === 'num' && item.v === k))
        return withBonuses(base, [{ count: split.count, ratio: bonus.c / k }], context)
      return withBonuses(base, [{ count: split.count, extra: product([{ t: 'num', v: bonus.c }, ...others]) }], context)
    }
  }
  if (node.t === 'bin' && node.op === '+') {
    const parts = flatten(node, '+')
    const base = terms(parts[0])
    if (!base.rest.length || base.rest.some((factor) => isBoolean(factor) || isCount(factor))) return null
    const bonuses: Bonus[] = []
    for (const part of parts.slice(1)) {
      const extra = terms(part)
      const split = splitCount(extra.rest)
      if (!split || !split.rest.length || split.rest.some(isBoolean)) return null
      const relative = sameFactors(split.rest, base.rest)
        && (base.c === 1 || flatten(part, '*').some((item) => item.t === 'num' && item.v === base.c))
      bonuses.push(relative ? { count: split.count, ratio: extra.c / base.c } : { count: split.count, extra: product([{ t: 'num', v: extra.c }, ...split.rest]) })
    }
    return withBonuses(parts[0], bonuses, context)
  }
  return null
}

// "0.1×target max HP per buff on the target": a product of one count and a value.
function perCount(node: Node, context: FormulaContext): string | null {
  if (node.t !== 'bin' || node.op !== '*') return null
  const factors = terms(node)
  const split = splitCount(factors.rest)
  if (!split || !split.rest.length || !isCount(split.count) || split.rest.some((factor) => isBoolean(factor) || isCount(factor))) return null
  const perPercent = share(split.count) ? 0.01 : 1
  const value = render(product([{ t: 'num', v: derived(factors.c * perPercent) }, ...split.rest]), context, true)
  return context.t('formula.perCount', { value, per: perText(split.count, context) })
}

function precedence(node: Node) {
  if (node.t !== 'bin' || isBoolean(node)) return 9
  return { '||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4, '+': 5, '-': 5, '*': 6, '/': 6 }[node.op] ?? 9
}

// A value; "top" leaves out the brackets a bonus phrase gets inside a larger expression.
function render(node: Node, context: FormulaContext, top = false): string {
  const { t } = context
  if (isBoolean(node)) return t('formula.oneIf', { condition: conditionText(node, context) })
  if (node.t === 'num') return decimal(number(node.v), context)
  if (node.t === 'neg') return `−${render(node.a, context)}`
  if (node.t === 'var' || node.t === 'call') {
    return valueName(node, context) ?? (node.t === 'call' ? `${node.name}(${node.args.map((arg) => render(arg, context)).join(', ')})` : node.name)
  }
  if (node.t !== 'bin') return ''
  // 1 − HP share: the share of HP lost.
  if (node.op === '-' && node.l.t === 'num' && node.l.v === 1 && node.r.t === 'var' && (node.r.name === 'HP_PERC' || node.r.name === 'TRG_HP_PERC'))
    return t(node.r.name === 'HP_PERC' ? 'formula.hpLostShare' : 'formula.targetHpLostShare')
  const grown = growth(node, context) ?? (top ? perCount(node, context) : null)
  if (grown) return top ? grown : t('formula.bracket', { text: grown })
  const wrap = (child: Node, strict: boolean) => {
    const text = render(child, context)
    const inner = precedence(child)
    return inner < precedence(node) || (strict && inner === precedence(node)) ? `(${text})` : text
  }
  if (node.op === '*') {
    // Numbers first and folded into one: "0.2*6*ATK" is "1.2×ATK".
    const factors = flatten(node, '*')
    const numbers = factors.filter((factor) => factor.t === 'num') as { t: 'num'; v: number }[]
    const rest = factors.filter((factor) => factor.t !== 'num')
    const coefficient = numbers.reduce((value, factor) => value * factor.v, 1)
    return [...(numbers.length && (coefficient !== 1 || !rest.length) ? [decimal(number(coefficient), context)] : []), ...rest.map((factor) => wrap(factor, false))].join('×')
  }
  // x/(aliveAlliesCount − !producerIsDead): shared among the living allies other than this champion.
  if (node.op === '/' && node.r.t === 'bin' && node.r.op === '-' && node.r.l.t === 'var' && node.r.l.name === 'aliveAlliesCount'
      && node.r.r.t === 'not' && node.r.r.a.t === 'var' && node.r.r.a.name === 'producerIsDead')
    return t('formula.sharedAmongAllies', { value: render(node.l, context) })
  const symbol = { '+': ' + ', '-': ' − ', '/': '/' }[node.op] ?? ` ${node.op} `
  return `${wrap(node.l, false)}${symbol}${wrap(node.r, node.op !== '+')}`
}

// ---- Conditions ----

function conditionText(node: Node, context: FormulaContext): string {
  const { t } = context
  const kinds = kindCondition(node)
  if (kinds) {
    const names = listText(kinds.kinds.map((kind) => named(context, `formula.kind.${kind}`)), context, 'formula.or')
    return t(kinds.positive ? 'formula.targetIs' : 'formula.targetIsNot', { names })
  }
  if (node.t === 'var' && FLAGS.has(node.name)) return named(context, `formula.flag.${node.name}`)
  if (node.t === 'not' && node.a.t === 'var' && FLAGS.has(node.a.name)) return named(context, `formula.notFlag.${node.a.name}`)
  if (node.t === 'not') return t('formula.not', { condition: conditionText(node.a, context) })
  if (node.t === 'call' && node.args[0]) {
    const kind = kindOf(node.args[0], context)
    if (kind && node.name === 'ProducerHasEffectOfKind') return t('formula.selfHasKind', { kind })
    if (kind) return t('formula.targetHasKind', { kind })
  }
  const role = (part: Node) => part.t === 'bin' && part.op === '==' && part.l.t === 'var' && part.l.name === 'targetRole'
    && part.r.t === 'var' && ROLES.has(part.r.name) ? named(context, `formula.role.${part.r.name}`) : null
  if (node.t === 'bin' && (node.op === '&&' || node.op === '||')) {
    const parts = flatten(node, node.op)
    // "the target's role is HP or Support"
    const roles = parts.map(role)
    if (node.op === '||' && roles.every(Boolean)) return t('formula.targetRole', { roles: listText(roles as string[], context, 'formula.or') })
    return parts.map((part) => conditionText(part, context)).join(t(node.op === '&&' ? 'formula.and' : 'formula.or'))
  }
  if (role(node)) return t('formula.targetRole', { roles: role(node) })
  if (node.t === 'bin') {
    // A count against 0: "the target has buffs" / "has no buffs".
    const has = node.l.t === 'var' && HAS.has(node.l.name) ? node.l.name : undefined
    if (has && node.r.t === 'num' && node.r.v === 0 && ['>', '==', '!='].includes(node.op))
      return named(context, node.op === '==' ? `formula.hasNone.${has}` : `formula.has.${has}`)
    // The champion's own stats next to the target's: "target ATK ≥ own ATK".
    const side = (part: Node) => part.t === 'var' && SELF_STATS.has(part.name)
      ? t('formula.ownStat', { stat: named(context, `formula.var.${part.name}`) }) : render(part, context)
    const symbol = { '==': '=', '!=': '≠', '>=': '≥', '<=': '≤' }[node.op] ?? node.op
    return `${side(node.l)} ${symbol} ${side(node.r)}`
  }
  return render(node, context)
}

// ---- Cases ----

type Case = { conditions: Node[]; value: Node; kinds?: KindCondition }

function hasCondition(node: Node): boolean {
  if (node.t !== 'bin') return false
  if (node.op === '*') return flatten(node, '*').some((factor) => isBoolean(factor) || hasCondition(factor))
  if (node.op === '+') return hasCondition(node.l) || hasCondition(node.r)
  return false
}

// Terms multiplied by conditions: "(c1)*x + (c2)*y" is the case list [c1: x, c2: y].
function cases(node: Node, conditions: Node[] = []): Case[] {
  if (node.t === 'bin' && node.op === '+' && hasCondition(node))
    return [...cases(node.l, conditions), ...cases(node.r, conditions)]
  if (node.t === 'bin' && node.op === '*') {
    const factors = flatten(node, '*')
    const tests = factors.filter(isBoolean)
    const values = factors.filter((factor) => !isBoolean(factor))
    const nested = values.filter(hasCondition)
    if (nested.length === 1) {
      // A common factor around a case list: x*((c1)*a + (c2)*b).
      const others = values.filter((value) => value !== nested[0])
      return cases(nested[0], [...conditions, ...tests]).map((item) => ({ ...item, value: product([...others, item.value]) }))
    }
    if (tests.length) return [{ conditions: [...conditions, ...tests], value: product(values) }]
  }
  return [{ conditions, value: node }]
}

function caseLabel(item: Case, context: FormulaContext) {
  const { t } = context
  const kindLabel = (kinds: KindCondition) => {
    const names = listText(kinds.kinds.map((kind) => named(context, `formula.kinds.${kind}`)), context, kinds.positive ? 'formula.or' : 'formula.listAnd')
    return t(kinds.positive ? 'formula.versus' : 'formula.versusOthers', { names })
  }
  if (item.kinds) return kindLabel(item.kinds)
  return item.conditions.map((condition) => {
    const kinds = kindCondition(condition)
    if (kinds) return kindLabel(kinds)
    return t('formula.if', { condition: conditionText(condition, context) })
  }).join(t('formula.clauseSeparator'))
}

// The readable formula; one that cannot be parsed keeps its text with known names.
export function describeFormula(formula: string, context: FormulaContext): string {
  const { t } = context
  let tree: Node
  try {
    tree = parseFormula(formula)
  } catch {
    return formula.replace(/[A-Za-z_][A-Za-z0-9_]*/g, (name) => VARIABLES.has(name) ? named(context, `formula.var.${name}`) : name).replace(/\*/g, '×')
  }
  const list = cases(tree)
  if (list.length === 1 && !list[0].conditions.length) return render(list[0].value, context, true)
  const merged: Case[] = []
  for (const item of list) {
    const previous = merged.find((other) => same(other.value, item.value))
    if (previous) {
      // The same value for two kinds of target: "vs bosses or minions".
      const a = previous.kinds ?? (previous.conditions.length === 1 ? kindCondition(previous.conditions[0]) : null)
      const b = item.conditions.length === 1 ? kindCondition(item.conditions[0]) : null
      if (a && b && a.positive && b.positive) {
        previous.kinds = { positive: true, kinds: [...new Set([...a.kinds, ...b.kinds])] }
        continue
      }
      // Conditions that differ in one place only: "role is HP or Support".
      const differ = previous.conditions.length === item.conditions.length
        ? previous.conditions.map((condition, index) => (same(condition, item.conditions[index]) ? -1 : index)).filter((index) => index >= 0)
        : []
      if (!previous.kinds && differ.length === 1) {
        const index = differ[0]
        previous.conditions[index] = { t: 'bin', op: '||', l: previous.conditions[index], r: item.conditions[index] }
        continue
      }
    }
    merged.push({ conditions: [...item.conditions], value: item.value })
  }
  return merged.map((item) => item.conditions.length || item.kinds
    ? `${caseLabel(item, context)}${t('formula.caseColon')}${render(item.value, context, true)}`
    : render(item.value, context, true)).join(t('formula.caseSeparator'))
}

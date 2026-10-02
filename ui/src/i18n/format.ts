// A small subset of ICU MessageFormat, enough for the tool's messages:
//   {name}                                   the parameter as written
//   {count, number}                          a number in the language's format
//   {count, plural, =0 {…} one {…} other {…}}  # inside a branch is the number
//   {kind, select, boss {…} other {…}}
// Branches nest. Apostrophes are ordinary text (no ICU quoting), and messages
// contain no literal braces.

export type MessageParams = Record<string, string | number | boolean | null | undefined>

type Node =
  | { type: 'text'; value: string }
  | { type: 'hash' }
  | { type: 'argument'; name: string; format: 'none' | 'number' }
  | { type: 'plural'; name: string; branches: Map<string, Node[]> }
  | { type: 'select'; name: string; branches: Map<string, Node[]> }

const parsed = new Map<string, Node[]>()

function parse(pattern: string): Node[] {
  let index = 0
  function nodes(inPlural: boolean): Node[] {
    const result: Node[] = []
    let text = ''
    const flush = () => {
      if (text) result.push({ type: 'text', value: text })
      text = ''
    }
    while (index < pattern.length) {
      const char = pattern[index]
      if (char === '}') break
      if (char === '#' && inPlural) {
        flush()
        result.push({ type: 'hash' })
        index++
        continue
      }
      if (char !== '{') {
        text += char
        index++
        continue
      }
      flush()
      index++
      const close = pattern.indexOf('}', index)
      const comma = pattern.indexOf(',', index)
      if (comma === -1 || (close !== -1 && close < comma)) {
        result.push({ type: 'argument', name: pattern.slice(index, close).trim(), format: 'none' })
        index = close + 1
        continue
      }
      const name = pattern.slice(index, comma).trim()
      index = comma + 1
      const kindEnd = pattern.slice(index).search(/[,}]/) + index
      const kind = pattern.slice(index, kindEnd).trim()
      index = kindEnd
      if (kind === 'number') {
        index = pattern.indexOf('}', index) + 1
        result.push({ type: 'argument', name, format: 'number' })
        continue
      }
      if (kind !== 'plural' && kind !== 'select') throw new Error(`Unsupported message format "${kind}" in: ${pattern}`)
      index++ // the comma after the kind
      const branches = new Map<string, Node[]>()
      while (index < pattern.length) {
        while (/\s/.test(pattern[index])) index++
        if (pattern[index] === '}') break
        const open = pattern.indexOf('{', index)
        const selector = pattern.slice(index, open).trim()
        index = open + 1
        branches.set(selector, nodes(kind === 'plural'))
        index++ // the branch's closing brace
      }
      index++ // the argument's closing brace
      result.push({ type: kind, name, branches })
    }
    flush()
    return result
  }
  return nodes(false)
}

const numberFormats = new Map<string, Intl.NumberFormat>()
const pluralRules = new Map<string, Intl.PluralRules>()

function formatNumber(locale: string, value: number): string {
  let format = numberFormats.get(locale)
  if (!format) numberFormats.set(locale, format = new Intl.NumberFormat(locale))
  return format.format(value)
}

function plural(locale: string, value: number): string {
  let rules = pluralRules.get(locale)
  if (!rules) pluralRules.set(locale, rules = new Intl.PluralRules(locale))
  return rules.select(value)
}

function render(locale: string, nodes: Node[], params: MessageParams, hash: number | null): string {
  let out = ''
  for (const node of nodes) {
    if (node.type === 'text') out += node.value
    else if (node.type === 'hash') out += hash === null ? '#' : formatNumber(locale, hash)
    else if (node.type === 'argument') {
      const value = params[node.name]
      out += value === null || value === undefined ? '' : node.format === 'number' && typeof value === 'number'
        ? formatNumber(locale, value) : String(value)
    } else if (node.type === 'plural') {
      const value = Number(params[node.name] ?? 0)
      const branch = node.branches.get(`=${value}`) ?? node.branches.get(plural(locale, value)) ?? node.branches.get('other') ?? []
      out += render(locale, branch, params, value)
    } else {
      const value = String(params[node.name] ?? 'other')
      out += render(locale, node.branches.get(value) ?? node.branches.get('other') ?? [], params, hash)
    }
  }
  return out
}

export function formatMessage(locale: string, pattern: string, params: MessageParams = {}): string {
  let nodes = parsed.get(pattern)
  if (!nodes) parsed.set(pattern, nodes = parse(pattern))
  return render(locale, nodes, params, null)
}

// The argument names a message uses (catalog checks compare them across languages).
export function messageArguments(pattern: string): string[] {
  const names = new Set<string>()
  const walk = (nodes: Node[]) => {
    for (const node of nodes) {
      if (node.type === 'argument') names.add(node.name)
      if (node.type === 'plural' || node.type === 'select') {
        names.add(node.name)
        node.branches.forEach(walk)
      }
    }
  }
  walk(parse(pattern))
  return [...names].sort()
}

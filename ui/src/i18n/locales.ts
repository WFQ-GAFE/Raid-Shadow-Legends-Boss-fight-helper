// The languages the tool offers. Adding one takes a messages/<code>.json
// catalog, a line here and its import in index.tsx; keys missing from a
// catalog fall back along `fallback`, then to English.
export const LOCALES = [
  { code: 'en', name: 'English', fallback: null },
  { code: 'zh-CN', name: '简体中文', fallback: 'en' },
  { code: 'pt-BR', name: 'Português (Brasil)', fallback: 'en' },
] as const

export type Locale = (typeof LOCALES)[number]['code']

export const DEFAULT_LOCALE: Locale = 'en'

export function isLocale(value: unknown): value is Locale {
  return LOCALES.some((locale) => locale.code === value)
}

// The best offered language for the system's preferred ones (navigator.languages):
// an exact tag first, then the same base language (pt-PT -> pt-BR, zh-TW -> zh-CN).
export function matchLocale(tags: readonly string[]): Locale {
  for (const tag of tags) {
    const exact = LOCALES.find((locale) => locale.code.toLowerCase() === tag.toLowerCase())
    if (exact) return exact.code
    const base = tag.split('-')[0].toLowerCase()
    const sameBase = LOCALES.find((locale) => locale.code.split('-')[0] === base)
    if (sameBase) return sameBase.code
  }
  return DEFAULT_LOCALE
}

export function fallbackChain(locale: Locale): Locale[] {
  const chain: Locale[] = []
  let current: Locale | null = locale
  while (current && !chain.includes(current)) {
    chain.push(current)
    current = LOCALES.find((entry) => entry.code === current)?.fallback ?? null
  }
  if (!chain.includes(DEFAULT_LOCALE)) chain.push(DEFAULT_LOCALE)
  return chain
}

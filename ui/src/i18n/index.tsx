import { createContext, useContext, useMemo, type ReactNode } from 'react'
import en from './messages/en.json'
import { backendDataValue } from './backendData'
import { formatMessage, type MessageParams } from './format'
import { stripGameTextMarkers, translateToolText } from './legacy'
import { DEFAULT_LOCALE, fallbackChain, isLocale, LOCALES, matchLocale, type Locale } from './locales'

// The tool's own texts live in messages/<locale>.json under stable keys, with
// ICU-style arguments (see format.ts); English is the reference catalog, so a
// key typo fails to compile. Game-owned text (champion, skill and trial names
// and descriptions) is not translated: it comes in the game client's language.
// A language is its catalog file plus its line in locales.ts; the backend reads
// the same files (tools/ui_text.py).
export { LOCALES, isLocale, type Locale } from './locales'
export type { MessageParams } from './format'
export { gameText, translateToolText } from './legacy'

export type MessageKey = keyof typeof en
// Kept for the modules that predate the catalogs.
export type UiLanguage = Locale

const FILES = import.meta.glob<Partial<Record<MessageKey, string>>>('./messages/*.json', { eager: true, import: 'default' })
const CATALOGS = Object.fromEntries(LOCALES.map(({ code }) => [code, FILES[`./messages/${code}.json`] ?? {}])) as
  Record<Locale, Partial<Record<MessageKey, string>>>

export function translate(locale: Locale, key: MessageKey, params?: MessageParams): string {
  for (const candidate of fallbackChain(locale)) {
    const pattern = CATALOGS[candidate][key]
    if (pattern !== undefined) return formatMessage(candidate, pattern, params)
  }
  return key
}

// For keys built from data (an enum value appended to a group); the catalog
// test checks that every such group is complete.
export function hasMessage(key: string): key is MessageKey {
  return Object.hasOwn(en, key)
}

// Text from the backend: message tokens ⸨["key", {params}]⸩ inside ordinary
// strings (tools/ui_text.py), shown in the window's language; the text around
// them (game names, numbers) stays. String params can hold tokens too. Text
// with no tokens predates them and is Chinese: other languages get the 1.1.1
// phrase table.
const TOKEN = /⸨(.*?)⸩/gs

function decodeToken(raw: string, payload: string, locale: Locale): string {
  try {
    const [key, params = {}] = JSON.parse(payload) as [unknown, Record<string, unknown>?]
    if (typeof key !== 'string') return raw
    if (!hasMessage(key)) return key
    const values: MessageParams = {}
    for (const [name, value] of Object.entries(params ?? {})) {
      values[name] = typeof value === 'string' ? backendText(value, locale, false)
        : typeof value === 'number' || typeof value === 'boolean' || value === null ? value : String(value)
    }
    return translate(locale, key, values)
  } catch {
    return raw
  }
}

// Names and labels the backend keeps in Chinese (backendData.ts).
const dataValue = (text: string, locale: Locale) => backendDataValue(text, locale, translate)

/** A name that may be one the backend gave (default strategy, rule N); other names are the user's, as written. */
export function dataName(value: string, locale: Locale = activeLocale): string {
  return dataValue(value, locale) ?? value
}

export function backendText(value: unknown, locale: Locale = activeLocale, oldRecords = true): string {
  const text = value === null || value === undefined ? '' : String(value)
  const plain = (part: string) => dataValue(part, locale)
    ?? (!oldRecords ? part : locale === 'zh-CN' ? stripGameTextMarkers(part) : translateToolText(part))
  if (!text.includes('⸨')) return plain(text)
  let out = ''
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    out += plain(text.slice(last, match.index)) + decodeToken(match[0], match[1], locale)
    last = (match.index ?? 0) + match[0].length
  }
  return out + plain(text.slice(last))
}

export type Translate = (key: MessageKey, params?: MessageParams) => string

type I18n = { locale: Locale; t: Translate }

const I18nContext = createContext<I18n>({ locale: DEFAULT_LOCALE, t: (key, params) => translate(DEFAULT_LOCALE, key, params) })

// The window's language for text built outside components (helpers called while
// rendering, notices, errors). The root component sets it on every render, so it
// always matches the tree being rendered.
let activeLocale: Locale = DEFAULT_LOCALE

export function setActiveLocale(locale: Locale) {
  activeLocale = locale
}

export function activeLanguage(): Locale {
  return activeLocale
}

/** translate() in the window's current language. */
export const tr: Translate = (key, params) => translate(activeLocale, key, params)

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  activeLocale = locale
  const value = useMemo<I18n>(() => ({ locale, t: (key, params) => translate(locale, key, params) }), [locale])
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18n {
  return useContext(I18nContext)
}

const STORAGE_KEY = 'raid-boss-tool-language'

// The saved choice, else the system's preferred language when the tool offers it.
export function getInitialLocale(): Locale {
  let saved: string | null = null
  try {
    saved = window.localStorage.getItem(STORAGE_KEY)
  } catch {
    // Local storage can be unavailable in hardened embedded-browser sessions.
  }
  const locale = isLocale(saved) ? saved : matchLocale(navigator.languages ?? [navigator.language])
  document.documentElement.lang = locale
  return locale
}

export function saveLocale(locale: Locale) {
  document.documentElement.lang = locale
  try {
    window.localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // The selected language still applies for this session.
  }
}

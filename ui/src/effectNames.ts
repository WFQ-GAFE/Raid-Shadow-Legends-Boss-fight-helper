import { activeLanguage, hasMessage, translate, type Locale } from './i18n'

// What the backend sends for a status effect: its type id (or a saved kind
// name), icon, the game's status name, and its Chinese and English labels.
export type NamedEffect = { token: string; icon: string; label: string; labelEn?: string; nativeName?: string }

export function readableIdentifier(value: string) {
  return value.replace(/^Status/, '').replace(/(?<=[a-z0-9])(?=[A-Z])/g, ' ').replace(/(?<=[A-Za-z])(?=\d)/g, ' ').trim()
}

/** An effect's name: the catalog by type id, then by the game's status name;
 *  otherwise the backend's Chinese label, or its English one in other languages. */
export function effectName(effect: NamedEffect, locale: Locale = activeLanguage()): string {
  for (const key of [`effect.${effect.token}`, effect.nativeName ? `effect.status.${effect.nativeName}` : '']) {
    if (key && hasMessage(key)) return translate(locale, key)
  }
  return locale === 'zh-CN' ? effect.label : effect.labelEn ?? readableIdentifier(effect.icon)
}

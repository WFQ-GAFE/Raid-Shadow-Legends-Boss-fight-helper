import { Languages } from 'lucide-react'
import { LOCALES, type Locale } from './i18n'

// Every offered language under its own name, so the menu can be found whatever is selected.
export function LanguagePicker({ locale, onChange, className = '' }: { locale: Locale; onChange: (locale: Locale) => void; className?: string }) {
  return <label className={`language-switcher ${className}`} data-i18n-skip>
    <Languages size={18} />
    <span>Language</span>
    <select value={locale} onChange={(event) => onChange(event.target.value as Locale)} aria-label="Language / 语言 / Idioma">
      {LOCALES.map((entry) => <option key={entry.code} value={entry.code}>{entry.name}</option>)}
    </select>
  </label>
}

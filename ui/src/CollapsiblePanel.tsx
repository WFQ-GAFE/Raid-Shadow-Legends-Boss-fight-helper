import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { panelPreference } from './ruleNavigation'

export function CollapsiblePanel({ id, title, hint, children, rules = false, defaultOpen = true, revealVersion = 0 }: {
  id: string; title: string; hint?: ReactNode; children: ReactNode; rules?: boolean; defaultOpen?: boolean; revealVersion?: number
}) {
  const [open, setOpen] = useState(() => {
    try { return panelPreference(window.localStorage, id, defaultOpen) } catch { return defaultOpen }
  })
  const [lastReveal, setLastReveal] = useState(revealVersion)
  if (revealVersion !== lastReveal) { setLastReveal(revealVersion); setOpen(true) }
  return <details id={`panel-${id}`} className={`collapsible-card${rules ? ' rules-panel' : ''}`} open={open} onToggle={(event) => {
    const next = event.currentTarget.open
    setOpen(next)
    try { window.localStorage.setItem(`studio:panel:${id}`, next ? 'open' : 'closed') } catch { /* optional preference */ }
  }}>
    <summary className="panel-toggle"><strong>{title}</strong><span data-i18n-skip>{hint}</span><ChevronDown size={16} /></summary>
    {children}
  </details>
}

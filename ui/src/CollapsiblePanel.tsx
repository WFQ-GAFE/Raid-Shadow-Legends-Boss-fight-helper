import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

export function CollapsiblePanel({ id, title, hint, children, rules = false }: {
  id: string; title: string; hint?: ReactNode; children: ReactNode; rules?: boolean
}) {
  const [open, setOpen] = useState(() => {
    try { return window.localStorage.getItem(`studio:panel:${id}`) !== 'closed' } catch { return true }
  })
  return <details className={`collapsible-card${rules ? ' rules-panel' : ''}`} open={open} onToggle={(event) => {
    const next = event.currentTarget.open
    setOpen(next)
    try { window.localStorage.setItem(`studio:panel:${id}`, next ? 'open' : 'closed') } catch { /* optional preference */ }
  }}>
    <summary className="panel-toggle"><strong>{title}</strong><span data-i18n-skip>{hint}</span><ChevronDown size={16} /></summary>
    {children}
  </details>
}

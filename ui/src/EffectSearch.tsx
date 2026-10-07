import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { clampActiveOption } from './pickerUi'

// The search box that picks buffs, debuffs and other skill effects (the
// champion picker's conditions, the simulation log's effect filter).

// Loaded at once (not lazily): menus open with their icons, which players know effects by.
export function EffectGlyph({ icon, ready }: { icon?: string; ready: boolean }) {
  const [failed, setFailed] = useState(false)
  return icon && ready && !failed
    ? <img className="effect-glyph" src={`/api/asset/effect/${encodeURIComponent(icon)}`} alt="" onError={() => setFailed(true)} />
    : <Sparkles size={12} />
}

export type SearchOption = { key: string; label: string; kind: string; icon?: string; iconReady?: boolean; glyph?: boolean }

// Type to find an option; picking one hands its key over.
// Arrow keys move, Enter picks, Escape closes the list.
export function EffectSearch({ options, chosen, placeholder, empty, onPick }: {
  options: SearchOption[]
  chosen: string[]
  placeholder: string
  empty: string
  onPick: (key: string) => void
}) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const id = useId()
  const box = useRef<HTMLDivElement | null>(null)
  const menu = useRef<HTMLDivElement | null>(null)
  // A click anywhere else closes the list (focus may not move, e.g. onto a card).
  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false) }
    // Dialog libraries listen to Escape on document capture. Consume the first
    // Escape on window capture, so only the expanded suggestions close. Once
    // closed, the next Escape reaches the containing dialog as usual.
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !box.current?.contains(event.target as Node)) return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
    document.addEventListener('pointerdown', close, true)
    window.addEventListener('keydown', escape, true)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      window.removeEventListener('keydown', escape, true)
    }
  }, [open])
  const matches = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return options.filter((option) => !chosen.includes(option.key)
      && (!needle || option.label.toLowerCase().includes(needle) || option.kind.toLowerCase().includes(needle))).slice(0, 60)
  }, [options, chosen, text])
  const activeIndex = clampActiveOption(active, matches.length)
  useEffect(() => setActive((index) => clampActiveOption(index, matches.length)), [matches.length])
  useEffect(() => {
    if (!open) return
    const option = menu.current?.querySelector<HTMLElement>(`[data-option-index="${activeIndex}"]`)
    option?.scrollIntoView({ block: 'nearest' })
  }, [open, activeIndex, matches])
  const pick = (key: string) => {
    onPick(key)
    setText('')
    setActive(0)
  }
  return (
    <div className="effect-search" data-suggestions-open={open} ref={box} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false) }}>
      <label className="search-box"><Sparkles size={15} />
        <input value={text} placeholder={placeholder} aria-label={placeholder} role="combobox" aria-expanded={open} aria-autocomplete="list"
          aria-controls={`${id}-list`} aria-activedescendant={open && matches[activeIndex] ? `${id}-option-${activeIndex}` : undefined}
          onFocus={() => setOpen(true)} onChange={(event) => { setText(event.target.value); setOpen(true); setActive(0) }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); setActive((index) => open ? clampActiveOption(index + 1, matches.length) : 0) }
            else if (event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); setActive((index) => open ? clampActiveOption(index - 1, matches.length) : clampActiveOption(matches.length - 1, matches.length)) }
            else if (event.key === 'Enter' && open && matches[activeIndex]) { event.preventDefault(); pick(matches[activeIndex].key) }
          }} /></label>
      {open && <div className="effect-search-menu" role="listbox" id={`${id}-list`} aria-label={placeholder} ref={menu}>
        {matches.length ? matches.map((option, index) => (
          <button type="button" role="option" id={`${id}-option-${index}`} data-option-index={index} tabIndex={-1}
            aria-selected={index === activeIndex} key={option.key} className={index === activeIndex ? 'active' : ''}
            onMouseDown={(event) => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => pick(option.key)}>
            {option.glyph ? <EffectGlyph icon={option.icon} ready={Boolean(option.iconReady)} /> : <span className="effect-glyph-space" />}
            <span>{option.label}</span><small>{option.kind}</small>
          </button>)) : <p>{empty}</p>}
      </div>}
    </div>
  )
}

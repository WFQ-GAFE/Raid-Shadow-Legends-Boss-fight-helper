import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { hoverCardPosition } from './pickerUi'

// A small card shown while the pointer (or keyboard focus) is on its anchor.
// Rendered into <body> so scrolling panels and dialogs never clip it.
export function HoverCard({ content, children, className = '', inline = true, focusable = true }: {
  content: ReactNode
  children: ReactNode
  className?: string
  inline?: boolean
  // False when the anchor wraps controls that take focus themselves, or would add
  // a tab stop to every row of a list.
  focusable?: boolean
}) {
  const anchor = useRef<HTMLSpanElement | null>(null)
  const card = useRef<HTMLDivElement | null>(null)
  const id = useId()
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pointerOnAnchor = useRef(false)
  const pointerOnCard = useRef(false)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const cancelClose = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    closeTimer.current = null
  }, [])
  const show = useCallback(() => { cancelClose(); setOpen(true) }, [cancelClose])
  const hide = useCallback(() => { cancelClose(); pointerOnCard.current = false; setOpen(false); setPosition(null) }, [cancelClose])
  const scheduleClose = useCallback(() => {
    cancelClose()
    // Leave enough time to cross the small gap between the anchor and card.
    closeTimer.current = setTimeout(() => {
      const focused = document.activeElement
      if (!pointerOnAnchor.current && !pointerOnCard.current
        && !anchor.current?.contains(focused) && !card.current?.contains(focused)) hide()
    }, 180)
  }, [cancelClose, hide])
  useEffect(() => cancelClose, [cancelClose])
  useEffect(() => {
    if (!open || !content) return
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      hide()
    }
    // Consume Escape before a containing modal's document listener.
    window.addEventListener('keydown', escape, true)
    return () => window.removeEventListener('keydown', escape, true)
  }, [open, content, hide])
  useLayoutEffect(() => {
    if (!open || !anchor.current || !card.current) return
    const reposition = () => {
      if (!anchor.current || !card.current) return
      const next = hoverCardPosition(anchor.current.getBoundingClientRect(), card.current.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight })
      setPosition((current) => current?.left === next.left && current.top === next.top ? current : next)
    }
    reposition()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(reposition)
    observer?.observe(anchor.current)
    observer?.observe(card.current)
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
      observer?.disconnect()
    }
  }, [open, content])
  const scrollCard = (event: KeyboardEvent) => {
    if (!open || !card.current || card.current.scrollHeight <= card.current.clientHeight) return
    if ((event.target as HTMLElement).closest('input, textarea, select, [role="combobox"]')) return
    const distance = event.key === 'ArrowDown' ? 40 : event.key === 'ArrowUp' ? -40
      : event.key === 'PageDown' ? card.current.clientHeight * .8 : event.key === 'PageUp' ? -card.current.clientHeight * .8 : 0
    if (!distance) return
    event.preventDefault()
    card.current.scrollBy({ top: distance })
  }
  const Tag = inline ? 'span' : 'div'
  return (
    <>
      <Tag ref={anchor as never} className={`hover-anchor ${className}`} aria-describedby={open && content ? id : undefined}
        onMouseEnter={() => { pointerOnAnchor.current = true; show() }} onMouseLeave={() => { pointerOnAnchor.current = false; scheduleClose() }}
        onFocus={show} onBlur={scheduleClose} onKeyDown={scrollCard} tabIndex={focusable && content ? 0 : undefined}>
        {children}
      </Tag>
      {open && content && createPortal(
        <div ref={card} id={id} role="tooltip" className="hover-card" data-i18n-skip
          onMouseEnter={() => { pointerOnCard.current = true; show() }} onMouseLeave={() => { pointerOnCard.current = false; scheduleClose() }}
          onFocus={show} onBlur={scheduleClose}
          style={{ ...(position ?? { left: -9999, top: -9999 }), pointerEvents: 'auto', maxWidth: 'min(380px, calc(100vw - 16px))', maxHeight: 'calc(100vh - 16px)', overflowY: 'auto' }}>
          {content}
        </div>, document.body)}
    </>
  )
}

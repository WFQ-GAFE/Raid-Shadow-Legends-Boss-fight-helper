import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// A small card shown while the pointer (or keyboard focus) is on its anchor.
// Rendered into <body> so scrolling panels and dialogs never clip it.
export function HoverCard({ content, children, className = '', inline = true }: {
  content: ReactNode
  children: ReactNode
  className?: string
  inline?: boolean
}) {
  const anchor = useRef<HTMLSpanElement | null>(null)
  const card = useRef<HTMLDivElement | null>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const show = useCallback(() => setOpen(true), [])
  const hide = useCallback(() => { setOpen(false); setPosition(null) }, [])
  useLayoutEffect(() => {
    if (!open || !anchor.current || !card.current) return
    const box = anchor.current.getBoundingClientRect()
    const size = card.current.getBoundingClientRect()
    const margin = 8
    const below = box.bottom + margin + size.height <= window.innerHeight
    const top = below ? box.bottom + margin : Math.max(margin, box.top - margin - size.height)
    const left = Math.min(Math.max(margin, box.left + box.width / 2 - size.width / 2), window.innerWidth - size.width - margin)
    setPosition({ left, top })
  }, [open, content])
  const Tag = inline ? 'span' : 'div'
  return (
    <>
      <Tag ref={anchor as never} className={`hover-anchor ${className}`} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide} tabIndex={0}>
        {children}
      </Tag>
      {open && content && createPortal(
        <div ref={card} role="tooltip" className="hover-card" data-i18n-skip
          style={position ? { left: position.left, top: position.top } : { left: -9999, top: -9999 }}>
          {content}
        </div>, document.body)}
    </>
  )
}

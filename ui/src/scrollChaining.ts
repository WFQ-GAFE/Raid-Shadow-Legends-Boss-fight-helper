// A card's own scroll area scrolls first. At its end the column scrolls on
// only while that side of the card is still hidden, and only as far as needed
// to show it; a card that is fully in view keeps the column still.
// (Native chaining is off: styles.css sets overscroll-behavior: contain.)

function scrollsVertically(element: Element): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false
  const overflow = getComputedStyle(element).overflowY
  return (overflow === 'auto' || overflow === 'scroll') && element.scrollHeight > element.clientHeight + 1
}

function scrollParent(start: Element | null): HTMLElement | null {
  for (let node = start; node && node !== document.body; node = node.parentElement) {
    if (scrollsVertically(node)) return node
  }
  return null
}

function pixels(event: WheelEvent, page: number) {
  if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) return event.deltaY * 16
  if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) return event.deltaY * page
  return event.deltaY
}

export function installScrollChaining(): () => void {
  const onWheel = (event: WheelEvent) => {
    if (event.defaultPrevented || event.ctrlKey || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return
    const inner = scrollParent(event.target instanceof Element ? event.target : null)
    const outer = inner ? scrollParent(inner.parentElement) : null
    if (!inner || !outer) return
    const delta = pixels(event, inner.clientHeight)
    if (delta === 0) return
    const down = delta > 0
    const atEnd = down ? inner.scrollTop + inner.clientHeight >= inner.scrollHeight - 1 : inner.scrollTop <= 0
    if (!atEnd) return // the card's own area scrolls
    const card = inner.closest('.card') ?? inner
    const box = card.getBoundingClientRect()
    const view = outer.getBoundingClientRect()
    const hidden = down ? box.bottom - view.bottom : view.top - box.top
    event.preventDefault()
    if (hidden > 1) outer.scrollBy({ top: down ? Math.min(delta, hidden) : Math.max(delta, -hidden) })
  }
  document.addEventListener('wheel', onWheel, { passive: false, capture: true })
  return () => document.removeEventListener('wheel', onWheel, { capture: true })
}

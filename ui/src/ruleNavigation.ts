export function canMoveRule(index: number, direction: number, total: number, filter: string): boolean {
  return !filter.trim() && index >= 0 && index < total && index + direction >= 0 && index + direction < total
}

// Called from a layout effect after clearing the filter and opening the panel.
// It retries on animation frames only while the committed row is absent.
export function revealRuleAfterRender(index: number, find: (id: string) => HTMLElement | null,
  schedule: (callback: FrameRequestCallback) => number, cancel: (id: number) => void): () => void {
  let frame: number | undefined
  let attempts = 0
  let cancelled = false
  const reveal = () => {
    if (cancelled) return
    const row = find(`rule-row-${index}`)
    if (row) { row.scrollIntoView({ block: 'center', behavior: 'smooth' }); row.focus({ preventScroll: true }); return }
    if (++attempts < 20) frame = schedule(reveal)
  }
  frame = schedule(reveal)
  return () => { cancelled = true; if (frame !== undefined) cancel(frame) }
}

export function panelPreference(storage: Pick<Storage, 'getItem'> | undefined, id: string, defaultOpen = true): boolean {
  try {
    const saved = storage?.getItem(`studio:panel:${id}`)
    return saved === 'open' ? true : saved === 'closed' ? false : defaultOpen
  } catch { return defaultOpen }
}

export function logDrawerPreference(storage: Pick<Storage, 'getItem'> | undefined, boss: string): boolean {
  try { return storage?.getItem(`studio:logs:${boss}`) === 'open' } catch { return false }
}

// The picker previews exactly the compact team it applies. Keeping this
// normalization in one place also makes removal, dragging and keyboard moves
// agree about who occupies position 1 (the leader).
export function compactSlots<T>(slots: readonly (T | null)[], size: number): (T | null)[] {
  const heroes = slots.filter((item): item is T => item !== null).slice(0, size)
  return [...heroes, ...Array(Math.max(0, size - heroes.length)).fill(null)]
}

export function clampActiveOption(index: number, count: number): number {
  return count > 0 ? Math.max(0, Math.min(index, count - 1)) : 0
}

export type Rectangle = { left: number; top: number; right: number; bottom: number; width: number; height: number }

export function hoverCardPosition(anchor: Rectangle, card: { width: number; height: number }, viewport: { width: number; height: number }, margin = 8) {
  const maxLeft = Math.max(margin, viewport.width - card.width - margin)
  const maxTop = Math.max(margin, viewport.height - card.height - margin)
  const below = anchor.bottom + margin + card.height <= viewport.height - margin
  return {
    left: Math.min(Math.max(margin, anchor.left + anchor.width / 2 - card.width / 2), maxLeft),
    top: Math.min(Math.max(margin, below ? anchor.bottom + margin : anchor.top - margin - card.height), maxTop),
  }
}

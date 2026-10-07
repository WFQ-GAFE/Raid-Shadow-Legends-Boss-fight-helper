export type LogReadingSnapshot = { visible: string[]; following: boolean; unread: number }

function parsedCursor(value?: string): { epoch: string; position: number } | null {
  if (!value) return null
  const separator = value.lastIndexOf(':')
  const position = Number(value.slice(separator + 1))
  return separator >= 0 && Number.isSafeInteger(position) && position >= 0
    ? { epoch: value.slice(0, separator), position } : null
}

// Cursor positions count new entries even after a rolling buffer has trimmed
// older rows, including repeated identical lines. The overlap fallback is for
// old backends that do not publish a cursor.
export function addedLogEntries(previous: string[], next: string[], before?: string, after?: string): number {
  const first = parsedCursor(before), last = parsedCursor(after)
  if (first && last) return first.epoch === last.epoch
    ? Math.max(0, last.position - first.position) : next.length
  for (let overlap = Math.min(previous.length, next.length); overlap > 0; overlap--) {
    if (previous.slice(-overlap).every((line, index) => line === next[index])) return next.length - overlap
  }
  return next.length
}

export class LogReadingState {
  visible: string[] = []
  latest: string[] = []
  following = true
  unread = 0
  scrollTop = 0
  private cursor?: string

  update(logs: string[], cursor?: string): LogReadingSnapshot {
    const added = addedLogEntries(this.latest, logs, this.cursor, cursor)
    this.latest = logs.slice()
    this.cursor = cursor
    if (!logs.length) {
      this.following = true
      this.unread = 0
      this.scrollTop = 0
      this.visible = []
    } else if (this.following) {
      this.visible = this.latest
      this.unread = 0
    } else {
      this.unread += added
    }
    return this.snapshot()
  }

  follow(value: boolean): LogReadingSnapshot {
    this.following = value
    if (value) { this.visible = this.latest; this.unread = 0 }
    return this.snapshot()
  }

  snapshot(): LogReadingSnapshot {
    return { visible: this.visible, following: this.following, unread: this.unread }
  }
}

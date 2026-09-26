export type Draft<T> = { value: T; saved: T; revision: string; generation: number }

export class DraftStore<T> {
  private entries = new Map<string, Draft<T>>()
  constructor(private storage?: Pick<Storage, 'getItem' | 'setItem'>, private storageKey = 'raid-strategy-drafts-v1') {
    try {
      const entries: unknown = JSON.parse(storage?.getItem(storageKey) ?? '[]')
      if (Array.isArray(entries)) for (const pair of entries) {
        if (Array.isArray(pair) && typeof pair[0] === 'string' && pair[1] && typeof pair[1].generation === 'number'
          && typeof pair[1].revision === 'string' && pair[1].value && pair[1].saved) this.entries.set(pair[0], pair[1])
      }
    } catch { /* In-memory drafts still protect navigation when storage is unavailable. */ }
  }
  get(key: string) { return this.entries.get(key) }
  dirty(key: string) { const entry = this.get(key); return !!entry && JSON.stringify(entry.value) !== JSON.stringify(entry.saved) }
  open(key: string, saved: T, revision: string): Draft<T> {
    if (this.dirty(key)) return this.get(key)!
    const entry = { value: saved, saved, revision, generation: this.get(key)?.generation ?? 0 }
    this.entries.set(key, entry)
    this.persist()
    return entry
  }
  edit(key: string, value: T) {
    const entry = this.get(key)
    if (!entry) return
    this.entries.set(key, { ...entry, value, generation: entry.generation + 1 })
    this.persist()
  }
  saved(key: string, generation: number, saved: T, revision: string) {
    const current = this.get(key)
    if (!current) return
    const entry = { ...current, saved, revision, value: current.generation === generation ? saved : current.value }
    this.entries.set(key, entry)
    this.persist()
    return entry
  }
  forget(key: string) { this.entries.delete(key); this.persist() }
  hasUnsaved() { return [...this.entries.keys()].some(key => this.dirty(key)) }
  private persist() {
    try { this.storage?.setItem(this.storageKey, JSON.stringify([...this.entries].filter(([key]) => this.dirty(key)))) } catch { /* Preserve in memory. */ }
  }
}

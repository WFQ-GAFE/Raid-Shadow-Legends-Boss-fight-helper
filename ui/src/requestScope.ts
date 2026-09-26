export class RequestScope {
  private epoch = 0
  private controllers = new Set<AbortController>()
  invalidate() {
    this.epoch++
    for (const controller of this.controllers) controller.abort()
    this.controllers.clear()
  }
  begin(timeoutMs = 15000) {
    const epoch = this.epoch
    const controller = new AbortController()
    this.controllers.add(controller)
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    return {
      signal: controller.signal,
      current: () => epoch === this.epoch,
      finish: () => { clearTimeout(timer); this.controllers.delete(controller) },
    }
  }
}

export function mergeLogDelta<T extends { logs: string[]; logCursor?: string; logsReset?: boolean }>(previous: T, incoming: T): T {
  if (incoming.logsReset !== false) return incoming
  if (previous.logCursor === incoming.logCursor) return { ...incoming, logs: previous.logs }
  return { ...incoming, logs: [...previous.logs, ...incoming.logs].slice(-800) }
}

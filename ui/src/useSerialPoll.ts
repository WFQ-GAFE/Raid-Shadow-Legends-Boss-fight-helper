import { useEffect } from 'react'
import { RequestScope } from './requestScope'

export function useSerialPoll<T>(enabled: boolean, key: string, scope: RequestScope,
  request: (signal: AbortSignal) => Promise<T>, receive: (value: T) => void, failed: (reason: unknown) => void) {
  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      const ticket = scope.begin()
      try {
        const next = await request(ticket.signal)
        if (!stopped && ticket.current()) receive(next)
      } catch (reason) {
        if (!stopped && ticket.current()) failed(reason)
      } finally {
        ticket.finish()
        if (!stopped && ticket.current()) timer = setTimeout(poll, 1200)
      }
    }
    timer = setTimeout(poll, 1200)
    return () => { stopped = true; clearTimeout(timer); scope.invalidate() }
  }, [enabled, key, scope, request, receive, failed])
}

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { backendText, tr, useI18n } from './i18n'
import { LogReadingState } from './logReading'

// Keep reading snapshots in memory across closing/reopening the drawer or
// report. Never persist log contents into preferences or localStorage.
const readers = new Map<string, LogReadingState>()

// Log lines are backend text: message tokens, or Chinese from before 1.1.2.
export function LogView({ logs, cursor, mode, bufferHint }: {
  logs: string[]; cursor?: string; mode?: string; bufferHint?: ReactNode
}) {
  const { locale } = useI18n()
  const element = useRef<HTMLPreElement>(null)
  const [reader] = useState(() => {
    const key = `${mode ?? 'default'}:${bufferHint ? 'drawer' : 'full'}`
    let value = readers.get(key)
    if (!value) { value = new LogReadingState(); readers.set(key, value) }
    return value
  })
  const [view, setView] = useState(() => reader.snapshot())

  useLayoutEffect(() => {
    setView(reader.update(logs, cursor))
  }, [logs, cursor, reader])

  useLayoutEffect(() => {
    if (element.current) element.current.scrollTop = view.following
      ? element.current.scrollHeight : reader.scrollTop
  }, [view.visible, view.following, reader])

  return <div className="log-view">
    <div className="log-read-toolbar">
      <span role="status" aria-live="polite">{tr(view.following ? 'log.followingLatest' : 'log.readingPaused')}
        {view.unread > 0 && <strong>{tr('log.newEntries', { count: view.unread })}</strong>}</span>
      {bufferHint && <small>{bufferHint}</small>}
      <button type="button" className="button ghost" aria-pressed={view.following}
        onClick={() => setView(reader.follow(!view.following))}>{tr(view.following ? 'log.pauseFollowing' : 'log.followLatest')}</button>
      {!view.following && <button type="button" className="button ghost" onClick={() => setView(reader.follow(true))}>{tr('log.returnLatest')}</button>}
    </div>
    <pre ref={element} tabIndex={0} aria-label={tr('log.entries')} onScroll={(event) => {
    const view = event.currentTarget
    reader.scrollTop = view.scrollTop
    const following = view.scrollHeight - view.clientHeight - view.scrollTop <= 4
    if (following !== reader.following) setView(reader.follow(following))
  }}>{view.visible.length ? view.visible.map((line) => backendText(line, locale)).join('\n') : tr('log.noRunLogYet')}</pre>
  </div>
}

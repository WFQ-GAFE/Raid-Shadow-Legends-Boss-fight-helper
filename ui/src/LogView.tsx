import { useLayoutEffect, useRef, useState } from 'react'
import { backendText, tr, useI18n } from './i18n'

// Log lines are backend text: message tokens, or Chinese from before 1.1.2.
export function LogView({ logs }: { logs: string[] }) {
  const { locale } = useI18n()
  const element = useRef<HTMLPreElement>(null)
  const following = useRef(true)
  const [visible, setVisible] = useState(logs)

  useLayoutEffect(() => {
    if (!logs.length) following.current = true
    // Keep a reading snapshot: trimming the live 800-row buffer must not
    // remove text from under the reader either.
    if (following.current) setVisible(logs)
  }, [logs])

  useLayoutEffect(() => {
    if (following.current && element.current) {
      element.current.scrollTop = element.current.scrollHeight
    }
  }, [visible])

  return <pre ref={element} tabIndex={0} onScroll={(event) => {
    const view = event.currentTarget
    following.current = view.scrollHeight - view.clientHeight - view.scrollTop <= 4
    if (following.current) setVisible(logs)
  }}>{visible.length ? visible.map((line) => backendText(line, locale)).join('\n') : tr('log.noRunLogYet')}</pre>
}

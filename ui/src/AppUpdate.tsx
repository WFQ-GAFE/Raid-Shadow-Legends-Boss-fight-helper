import { useEffect, useRef, useState, type ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, ArrowUpCircle, CheckCircle2, Download, ExternalLink, RefreshCw, X } from 'lucide-react'
import { backendText, useI18n, type Locale } from './i18n'

// Newer releases from GitHub (tools/app_update.py). The button beside the version
// checks and opens the dialog, or names a newer version once one is known. "Update now"
// downloads and checks the file, then restarts the tool into it once nothing is running.

export type UpdateRelease = {
  version: string
  page: string
  publishedAt: string
  notes: Record<string, string>
  download: { name: string; size: number; sha256: string; url: string } | null
}
export type UpdateInstalled = { from: string; to: string | null; at: number; ok: boolean; error?: string; backup?: string; notes?: Record<string, string> }
// The short status every state poll carries.
export type UpdateSummary = {
  current: string
  status: 'idle' | 'checking' | 'current' | 'available' | 'downloading' | 'ready' | 'installing' | 'failed'
  version: string | null
  canInstall: boolean
  progress: [number, number] | null
  error: string | null
  failedStep: 'check' | 'download' | null
  checkedAt: number | null
  installed: UpdateInstalled | null
}
// What the dialog reads from /api/update.
export type UpdateDetails = UpdateSummary & { release: UpdateRelease | null; page: string; backup: string | null; busy: string | null }
export type UpdateAction = 'check' | 'download' | 'install' | 'open' | 'dismiss'

const ACTIVE = new Set<UpdateSummary['status']>(['checking', 'downloading', 'installing'])
const megabytes = (bytes: number) => `${(bytes / 1048576).toFixed(1)} MB`

// The version under the app name and the update button beside it: "Check for updates"
// (``onOpen(true)``: the dialog checks again), or "Update to x.y.z" once one is known.
export function UpdateVersion({ version, update, onOpen }: { version: string; update?: UpdateSummary | null; onOpen: (check: boolean) => void }) {
  const { t } = useI18n()
  const newer = update?.version ?? null
  const title = !newer ? t('update.versionTitle') : update?.status === 'ready' ? t('update.badgeReadyTitle', { version: newer }) : t('update.badgeTitle', { version: newer })
  return <span className="version-row">
    <small>{version}</small>
    <button type="button" className={`update-button${newer ? ' available' : ''}`} title={title} onClick={() => onOpen(!newer)}>
      {newer ? <ArrowUpCircle size={13} /> : <RefreshCw size={12} className={update?.status === 'checking' ? 'spin' : undefined} />}
      {newer ? t('update.availableButton', { version: newer }) : t('update.versionTitle')}
    </button>
  </span>
}

// The release notes are short Markdown: paragraphs, "- " lists (two spaces per level), **bold**, `code`.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong>
      : part.startsWith('`') && part.endsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part)
}

export function releaseNotesText(notes: Record<string, string> | undefined, locale: Locale): string {
  if (!notes) return ''
  return notes[locale] ?? notes.en ?? notes[''] ?? Object.values(notes)[0] ?? ''
}

function ReleaseNotes({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  text.split('\n').forEach((line, index) => {
    const item = /^(\s*)[-*]\s+(.*)$/.exec(line)
    if (item) {
      const level = Math.min(3, Math.floor(item[1].length / 2))
      blocks.push(<div key={index} className="update-notes-item" style={{ marginInlineStart: `${level * 16}px` }}>{inline(item[2])}</div>)
    } else if (line.trim()) {
      blocks.push(<p key={index}>{inline(line.trim())}</p>)
    }
  })
  return <div className="update-notes">{blocks}</div>
}

export function UpdateDialog({ open, onOpenChange, request, beforeRestart, checkOnOpen = false }: {
  open: boolean
  // Opened with "Check for updates": ask GitHub again unless a newer version is already known.
  checkOnOpen?: boolean
  onOpenChange: (open: boolean) => void
  // GET /api/update without an action, POST with one.
  request: (action?: UpdateAction) => Promise<UpdateDetails>
  // Settles unsaved strategy changes; false when the user cancels.
  beforeRestart: () => Promise<boolean>
}) {
  const { t, locale } = useI18n()
  const [details, setDetails] = useState<UpdateDetails | null>(null)
  const [error, setError] = useState('')
  const [autoRestart, setAutoRestart] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const requestRef = useRef(request)
  requestRef.current = request
  const message = (reason: unknown) => backendText(reason instanceof Error ? reason.message : String(reason))

  useEffect(() => {
    if (!open) { setAutoRestart(false); return }
    let alive = true
    setError('')
    // Also when never checked in this launch (running from source).
    requestRef.current().then((value) => value.status === 'idle' || (checkOnOpen && !value.version && !ACTIVE.has(value.status))
      ? requestRef.current('check') : value)
      .then((value) => { if (alive) setDetails(value) }).catch((reason) => { if (alive) setError(message(reason)) })
    return () => { alive = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Follow a check, a download or the restart while the dialog is open.
  const status = details?.status
  useEffect(() => {
    if (!open || !status || !ACTIVE.has(status)) return
    const timer = window.setInterval(() => { requestRef.current().then(setDetails).catch(() => undefined) }, 600)
    return () => window.clearInterval(timer)
  }, [open, status])

  async function act(action: UpdateAction) {
    setError('')
    try { setDetails(await requestRef.current(action)) } catch (reason) { setError(message(reason)) }
  }

  async function restart() {
    setError('')
    if (!(await beforeRestart())) return
    setRestarting(true)
    try { setDetails(await requestRef.current('install')) } catch (reason) { setRestarting(false); setError(message(reason)) }
  }

  // "Update now" goes on to the restart once the file is checked, unless something runs.
  useEffect(() => {
    if (!autoRestart) return
    if (status === 'failed') setAutoRestart(false)
    if (status === 'ready' && !details?.busy) { setAutoRestart(false); void restart() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRestart, status, details?.busy])

  function change(next: boolean) {
    if (!next && details?.installed) void act('dismiss')
    onOpenChange(next)
  }

  const release = details?.release ?? null
  const installed = details?.installed ?? null
  const busy = details?.busy ? backendText(details.busy) : ''
  const progress = details?.progress
  const checkedAt = details?.checkedAt ? new Date(details.checkedAt).toLocaleString(locale) : ''
  const failedCheck = status === 'failed' && details?.failedStep === 'check'
  const offerCheck = !release && (status === 'current' || status === 'idle' || failedCheck)
  const installing = restarting || status === 'installing'

  return <Dialog.Root open={open} onOpenChange={change}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog-content update-dialog">
        <div className="dialog-heading">
          <div><Dialog.Title><ArrowUpCircle size={17} /> {t('update.title')}</Dialog.Title>
            <Dialog.Description>{t('update.currentVersion', { version: details?.current ?? __APP_VERSION__ })}</Dialog.Description></div>
          <Dialog.Close className="icon-button" aria-label={t('app.close')}><X size={19} /></Dialog.Close>
        </div>
        <div className="update-body">
          {installed && (installed.ok
            ? <div className="update-installed good"><CheckCircle2 size={17} /><div>
                <strong>{t('update.installedOk', { version: installed.to ?? details?.current ?? '' })}</strong>
                {installed.backup && <small>{t('update.installedBackup', { backup: installed.backup })}</small>}
                {releaseNotesText(installed.notes, locale) && <ReleaseNotes text={releaseNotesText(installed.notes, locale)} />}
              </div></div>
            : <div className="update-installed bad" role="alert"><AlertTriangle size={17} /><div>
                <strong>{t('update.installedFailed', { version: installed.from, error: backendText(installed.error ?? '') })}</strong>
              </div></div>)}
          {!details && !error && <p className="muted">{t('update.checking')}</p>}
          {status === 'checking' && <p className="update-line"><RefreshCw size={15} className="spin" />{t('update.checking')}</p>}
          {status === 'current' && !release && <p className="update-line"><CheckCircle2 size={15} className="update-good" />{t('update.upToDate')}
            {checkedAt && <small>{t('update.lastChecked', { time: checkedAt })}</small>}</p>}
          {failedCheck && <div className="update-problem" role="alert"><p>{backendText(details?.error ?? '')}</p><small>{t('update.checkHint')}</small></div>}
          {release && <section className="update-release">
            <header><strong>{t('update.available', { version: release.version })}</strong>
              {release.publishedAt && <small>{t('update.published', { date: new Date(release.publishedAt).toLocaleDateString(locale) })}</small>}</header>
            {status === 'downloading' && progress && <div className="simulation-progress">
              <span>{t('update.downloading', { done: megabytes(progress[0]), total: megabytes(progress[1]) })}</span>
              <div className="bar"><span style={{ width: `${progress[1] ? Math.round(progress[0] / progress[1] * 100) : 0}%` }} /></div>
            </div>}
            {(status === 'ready' || status === 'installing') && <p className="update-line"><CheckCircle2 size={15} className="update-good" />{t('update.verified')}</p>}
            {status === 'failed' && details?.failedStep === 'download' && <p className="simulation-warning" role="alert">{backendText(details.error ?? '')}</p>}
            {details?.canInstall && (status === 'ready' || autoRestart) && busy && <p className="simulation-warning">{busy}</p>}
            <h4>{t('update.notes')}</h4>
            <ReleaseNotes text={releaseNotesText(release.notes, locale)} />
            {details?.canInstall && details.backup && <small className="update-note">{t('update.restartNote', { backup: details.backup })}</small>}
            {!details?.canInstall && <small className="update-note">{release.download ? t('update.sourceOnly') : t('update.manualOnly')}</small>}
          </section>}
          {error && <p className="simulation-warning" role="alert">{error}</p>}
        </div>
        <div className="dialog-footer actions-only">
          <div>
            <button type="button" className="button ghost" onClick={() => void act('open')}><ExternalLink size={16} />{t('update.openPage')}</button>
            {offerCheck && <button type="button" className="button ghost" onClick={() => void act('check')}>
              <RefreshCw size={16} />{failedCheck ? t('update.retry') : t('update.checkNow')}</button>}
            {installed && !release && <button type="button" className="button primary" onClick={() => change(false)}>{t('update.dismiss')}</button>}
            {release && details?.canInstall && (status === 'available' || (status === 'failed' && details.failedStep === 'download')) &&
              <button type="button" className="button primary" onClick={() => { setAutoRestart(true); void act('download') }}>
                <Download size={16} />{release.download ? t('update.oneClick', { size: megabytes(release.download.size) }) : t('update.retry')}</button>}
            {release && details?.canInstall && status === 'downloading' &&
              <button type="button" className="button primary" disabled><RefreshCw size={16} className="spin" />{progress?.[1] ? Math.floor(progress[0] / progress[1] * 100) : 0}%</button>}
            {release && details?.canInstall && (status === 'ready' || status === 'installing') &&
              <button type="button" className="button primary" disabled={installing || Boolean(busy)} onClick={() => void restart()}>
                {installing ? <><RefreshCw size={16} className="spin" />{t('update.installing')}</> : <><ArrowUpCircle size={16} />{t('update.restartNow')}</>}</button>}
          </div>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}

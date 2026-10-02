import { Component, type ErrorInfo, type ReactNode } from 'react'
import { tr } from './i18n'

let reports = 0
let reportWindow = 0
export function reportUiError(kind: string, error: unknown, componentStack = '') {
  if (Date.now() - reportWindow > 60_000) { reports = 0; reportWindow = Date.now() }
  if (reports++ >= 5) return
  const token = new URLSearchParams(window.location.search).get('token') ?? ''
  void fetch('/api/ui-error', {
    method: 'POST', keepalive: true,
    headers: { 'Content-Type': 'application/json', 'X-Chimera-Token': token },
    body: JSON.stringify({ kind, message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack?.slice(0, 6000) : '', componentStack: componentStack.slice(0, 3000) }),
  }).catch(() => undefined)
}

export class UiErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean; status: string }> {
  state = { failed: false, status: '' }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    document.getElementById('startup-shell')?.remove()
    document.documentElement.dataset.appMounted = 'true'
    reportUiError('react_render', error, info.componentStack ?? '')
  }
  async pause() {
    try {
      const token = new URLSearchParams(window.location.search).get('token') ?? ''
      const response = await fetch('/api/stop', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Chimera-Token': token }, body: '{}' })
      if (!response.ok) throw new Error(tr('errorBoundary.thePauseRequestFailedRestore'))
      this.setState({ status: tr('errorBoundary.takeoverPauseRequested') })
    } catch (error) { this.setState({ status: error instanceof Error ? error.message : String(error) }) }
  }
  render() {
    if (!this.state.failed) return this.props.children
    return <main style={{ padding: 40, color: '#e9f5ff', background: '#07101b', height: '100%', overflow: 'auto' }}>
      <h2>{tr('errorBoundary.theInterfaceHitADisplay')}</h2>
      <p>{tr('errorBoundary.theTakeoverMayStillBe')}</p>
      <button className="button primary" onClick={() => window.location.reload()}>{tr('errorBoundary.restoreInterface')}</button>{' '}
      <button className="button ghost" onClick={() => void this.pause()}>{tr('errorBoundary.pauseTakeover')}</button>
      <p>{this.state.status}</p>
    </main>
  }
}

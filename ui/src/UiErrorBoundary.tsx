import { Component, type ErrorInfo, type ReactNode } from 'react'

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
      if (!response.ok) throw new Error('暂停请求失败，请恢复界面后检查运行状态。')
      this.setState({ status: '已请求暂停接管。' })
    } catch (error) { this.setState({ status: error instanceof Error ? error.message : String(error) }) }
  }
  render() {
    if (!this.state.failed) return this.props.children
    return <main style={{ padding: 40, color: '#e9f5ff', background: '#07101b', height: '100%', overflow: 'auto' }}>
      <h2>界面显示出现异常</h2>
      <p>后台接管可能仍在运行。可以恢复界面，或先请求暂停接管。</p>
      <button className="button primary" onClick={() => window.location.reload()}>恢复界面</button>{' '}
      <button className="button ghost" onClick={() => void this.pause()}>暂停接管</button>
      <p>{this.state.status}</p>
    </main>
  }
}

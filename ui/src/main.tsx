import { StrictMode, useLayoutEffect } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import './rulePanelScroll.css'
import { UiErrorBoundary, reportUiError } from './UiErrorBoundary'

window.addEventListener('error', (event) => {
  if (event.message) reportUiError('javascript', event.error ?? event.message)
})
window.addEventListener('unhandledrejection', (event) => reportUiError('promise', event.reason))

function MountedApp() {
  useLayoutEffect(() => {
    document.documentElement.dataset.appMounted = 'true'
    document.getElementById('startup-shell')?.remove()
  }, [])

  return <App />
}

const root = document.getElementById('root')
if (!root) {
  throw new Error('Application root element is missing')
}

createRoot(root).render(
  <StrictMode>
    <UiErrorBoundary><MountedApp /></UiErrorBoundary>
  </StrictMode>,
)

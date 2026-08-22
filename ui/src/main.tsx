import { StrictMode, useLayoutEffect } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

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
    <MountedApp />
  </StrictMode>,
)

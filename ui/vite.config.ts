import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import packageInfo from './package.json'

export default defineConfig({
  plugins: [react()],
  base: '/',
  // Shown next to the name; VERSION, package.json and the EXE metadata move together.
  define: { __APP_VERSION__: JSON.stringify(packageInfo.version) },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})

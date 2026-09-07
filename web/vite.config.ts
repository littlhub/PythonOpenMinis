import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend (FastAPI) defaults to 127.0.0.1:8765 — see openminis.server.main.
const BACKEND = process.env.MINIS_BACKEND ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // Don't let vite rm -rf our dist before writing — the bulk-delete shim
    // on some Windows setups rejects that, and we don't want to depend on
    // an empty outDir for correct builds anyway.
    emptyOutDir: false,
    sourcemap: true,
  },
})

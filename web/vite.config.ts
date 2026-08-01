import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxy = { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } }

// `preview` does not inherit `server.proxy`, so both need it or the built
// bundle 404s on every API call.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
})

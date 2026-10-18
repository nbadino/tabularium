import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Il proxy /api -> backend evita CORS in sviluppo. `TABULARIUM_API` sposta il
// backend quando la 8787 è occupata da altro.
const apiTarget = process.env.TABULARIUM_API ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // Il canale live dello Studio è un WebSocket sullo stesso prefisso:
        // senza `ws` il proxy di sviluppo risponderebbe 426 al handshake.
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})

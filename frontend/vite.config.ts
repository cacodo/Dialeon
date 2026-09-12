import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Servido pelo FastAPI sob /app (Decision Delta secao 3/4) -- o build
// precisa referenciar seus proprios assets como /app/assets/..., nao /assets/...
const BASE_PATH = '/app/'

// FastAPI/uvicorn roda em 8000 por padrao neste projeto (Settings nao
// define porta propria -- e o default do uvicorn). O proxy evita CORS
// em desenvolvimento (Decision Delta secao 30): o frontend chama
// /providers, /runs, etc. como se fossem parte do proprio dev server.
const API_PROXY_TARGET = 'http://127.0.0.1:8000'

export default defineConfig({
  base: BASE_PATH,
  plugins: [react()],
  server: {
    proxy: {
      '/providers': API_PROXY_TARGET,
      '/runs': API_PROXY_TARGET,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})

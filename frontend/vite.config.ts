import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

const backend = process.env.SATLAB_BACKEND_URL ?? 'http://localhost:8000'

// The app lives under https://<domain>/satlab/ (page, assets, REST and WebSocket).
const BASE = '/satlab/'
const stripBase = (path: string) => path.slice(BASE.length - 1)

const UMAMI_SRC = 'https://cloud.umami.is/script.js'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Umami Cloud tracker, injected into production builds only when a website ID is configured. */
function umami(websiteId: string | undefined): Plugin {
  if (websiteId && !UUID.test(websiteId)) {
    throw new Error('NEXT_UMAMI_ID must be an Umami website ID (a UUID).')
  }
  return {
    name: 'satlab-umami',
    apply: 'build',
    transformIndexHtml: () =>
      websiteId
        ? [{ tag: 'script', attrs: { defer: true, src: UMAMI_SRC, 'data-website-id': websiteId }, injectTo: 'head' }]
        : [],
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Only NEXT_UMAMI_ID is read here; envPrefix stays VITE_ so no other variable reaches the bundle.
  const umamiId = loadEnv(mode, process.cwd(), '').NEXT_UMAMI_ID?.trim() || undefined

  return {
    base: BASE,
    plugins: [react(), tailwindcss(), umami(umamiId)],
    server: {
      port: 5173,
      // Same-origin in dev: REST and WebSocket are proxied to the FastAPI backend, base prefix stripped.
      proxy: {
        [`${BASE}api`]: { target: backend, rewrite: stripBase },
        [`${BASE}ws`]: { target: backend.replace(/^http/, 'ws'), ws: true, rewrite: stripBase },
      },
    },
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts'],
    },
  }
})

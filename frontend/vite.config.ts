import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { cpSync, createReadStream, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, extname, join, resolve, sep } from 'node:path'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

const backend = process.env.SATLAB_BACKEND_URL ?? 'http://localhost:8000'

// The app lives under https://<domain>/satlab/ (page, assets, REST and WebSocket).
const BASE = '/satlab/'
const stripBase = (path: string) => path.slice(BASE.length - 1)

const UMAMI_SRC = 'https://cloud.umami.is/script.js'
// The tracker stays silent anywhere else (the production image run locally, a preview).
const UMAMI_DOMAINS = 'satlab.darshitp.dev'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const GIT_SHA = /^[0-9a-f]{7,40}$/

/**
 * Umami Cloud tracker, injected into production builds only when a website ID is configured: page
 * views, the custom events of src/lib/analytics.ts, Core Web Vitals and uncaught errors (tagged
 * with the release, the deployed commit, when given).
 */
function umami(websiteId: string | undefined, release: string | undefined): Plugin {
  if (websiteId && !UUID.test(websiteId)) {
    throw new Error('NEXT_UMAMI_ID must be an Umami website ID (a UUID).')
  }
  if (release && !GIT_SHA.test(release)) {
    throw new Error('SATLAB_RELEASE must be a git commit SHA.')
  }
  const attrs = {
    defer: true,
    src: UMAMI_SRC,
    'data-website-id': websiteId,
    'data-domains': UMAMI_DOMAINS,
    'data-performance': 'true',
    'data-errors': 'true',
    ...(release ? { 'data-release': release } : {}),
  }
  return {
    name: 'satlab-umami',
    apply: 'build',
    transformIndexHtml: () => (websiteId ? [{ tag: 'script', attrs, injectTo: 'head' }] : []),
  }
}

// Cesium's web workers and static assets (globe imagery, ellipsoid data), served from our own
// origin at <base>cesium/ (CESIUM_BASE_URL, src/views/Globe/cesiumBase.ts). ThirdParty (wasm
// decoders for glTF / 3D tiles) is left out: the globe doesn't use them.
const CESIUM_PATH = 'cesium/'
const CESIUM_DIRS = ['Workers', 'Assets']
const CESIUM_TYPES: Record<string, string> = {
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.xml': 'application/xml',
  '.jpg': 'image/jpeg',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
}

function cesiumAssets(): Plugin {
  const root = join(dirname(createRequire(import.meta.url).resolve('@cesium/engine/package.json')), 'Build')
  const allowed = CESIUM_DIRS.map((d) => join(root, d) + sep)
  return {
    name: 'satlab-cesium-assets',
    configureServer(server) {
      server.middlewares.use(`${BASE}${CESIUM_PATH}`, (req, res, next) => {
        let file: string
        try {
          file = resolve(root, '.' + decodeURIComponent((req.url ?? '').split('?')[0]!))
        } catch {
          return next()
        }
        if (!allowed.some((dir) => file.startsWith(dir))) return next() // no escaping Build/<dir>
        try {
          if (!statSync(file).isFile()) return next()
        } catch {
          return next()
        }
        res.setHeader('Content-Type', CESIUM_TYPES[extname(file)] ?? 'application/octet-stream')
        createReadStream(file).pipe(res)
      })
    },
    writeBundle(options) {
      const out = options.dir ?? 'dist'
      for (const dir of CESIUM_DIRS) cpSync(join(root, dir), join(out, CESIUM_PATH, dir), { recursive: true })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Only NEXT_UMAMI_ID and SATLAB_RELEASE are read here (both public, they end up in the page);
  // envPrefix stays VITE_ so no other variable reaches the bundle.
  const env = loadEnv(mode, process.cwd(), '')
  const umamiId = env.NEXT_UMAMI_ID?.trim() || undefined
  const release = env.SATLAB_RELEASE?.trim().toLowerCase() || undefined

  return {
    base: BASE,
    plugins: [react(), tailwindcss(), umami(umamiId, release), cesiumAssets()],
    resolve: {
      // Cesium's meshopt decoder compiles WebAssembly on import, which the CSP refuses; the globe
      // never decodes meshopt data (src/views/Globe/meshoptStub.ts).
      alias: [{ find: /^meshoptimizer$/, replacement: resolve('src/views/Globe/meshoptStub.ts') }],
    },
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

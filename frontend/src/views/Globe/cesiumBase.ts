import { appUrl } from '../../lib/basePath'

// Cesium loads its web workers and assets (imagery, ellipsoid data) from CESIUM_BASE_URL: our own
// origin, where the build copies them (vite.config.ts), so no CDN or Cesium ion is involved and
// the CSP needs no new host. Imported ahead of @cesium/engine, which reads it on first use.
;(globalThis as { CESIUM_BASE_URL?: string }).CESIUM_BASE_URL = appUrl('/cesium/')

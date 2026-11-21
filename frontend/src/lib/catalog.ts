// The catalog's freshness in words, from GET /api/catalog/status. The server decides when to
// refresh (Celestrak updates every 2 h and refuses a repeat download), so there's no refresh button.

import type { CatalogStatus } from '../types/api'
import { formatAge } from './time'

export interface CatalogStatusText {
  text: string
  /** Why the last refresh didn't happen, shown beside a catalog that still works; null if none. */
  warning: string | null
}

/** "Celestrak “active”, fetched 3 h ago · refreshed every 12 h", or why there's no catalog yet. */
export function catalogStatusText(status: CatalogStatus | null, nowMs: number): CatalogStatusText {
  if (status === null) return { text: 'Connecting to catalog…', warning: null }
  if (status.satellite_count === 0) {
    return {
      text: status.last_error ? `Catalog unavailable: ${status.last_error}` : 'Loading TLEs from Celestrak…',
      warning: null,
    }
  }
  const fetched = status.last_fetched_at ? Date.parse(status.last_fetched_at) : NaN
  const parts = [
    `Celestrak “${status.group}”${Number.isFinite(fetched) ? `, fetched ${formatAge(Math.max(nowMs - fetched, 0))} ago` : ''}`,
  ]
  if (status.refreshing) parts.push('refreshing…')
  else if (typeof status.max_age_hours === 'number' && status.max_age_hours > 0) {
    parts.push(`refreshed every ${Number(status.max_age_hours.toFixed(1))} h`)
  }
  return { text: parts.join(' · '), warning: status.last_error }
}

import { describe, expect, it } from 'vitest'
import type { CatalogStatus } from '../types/api'
import { catalogStatusText } from './catalog'

const NOW = Date.parse('2026-11-21T12:00:00Z')
const STATUS: CatalogStatus = {
  satellite_count: 15855,
  group: 'active',
  last_fetched_at: '2026-11-21T09:00:00Z',
  refreshing: false,
  last_error: null,
  max_age_hours: 12,
}

describe('catalogStatusText', () => {
  it('says how old the catalog is and how often it refreshes', () => {
    expect(catalogStatusText(STATUS, NOW)).toEqual({
      text: 'Celestrak “active”, fetched 3 h ago · refreshed every 12 h',
      warning: null,
    })
  })

  it('says when a refresh is running, and keeps a failed one visible beside a working catalog', () => {
    expect(catalogStatusText({ ...STATUS, refreshing: true }, NOW).text).toBe(
      'Celestrak “active”, fetched 3 h ago · refreshing…',
    )
    const failed = catalogStatusText({ ...STATUS, last_error: 'Celestrak fetch failed; using the cached catalog' }, NOW)
    expect(failed.warning).toBe('Celestrak fetch failed; using the cached catalog')
  })

  it('reads an older server (no max age) and an unknown fetch time', () => {
    const { max_age_hours: _, ...older } = STATUS
    expect(catalogStatusText(older, NOW).text).toBe('Celestrak “active”, fetched 3 h ago')
    expect(catalogStatusText({ ...STATUS, last_fetched_at: null }, NOW).text).toBe('Celestrak “active” · refreshed every 12 h')
  })

  it('explains an empty catalog', () => {
    expect(catalogStatusText(null, NOW).text).toBe('Connecting to catalog…')
    expect(catalogStatusText({ ...STATUS, satellite_count: 0 }, NOW).text).toBe('Loading TLEs from Celestrak…')
    expect(catalogStatusText({ ...STATUS, satellite_count: 0, last_error: 'Celestrak fetch failed' }, NOW).text).toBe(
      'Catalog unavailable: Celestrak fetch failed',
    )
  })
})

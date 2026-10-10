import { describe, expect, it } from 'vitest'
import { DEFAULT_FILTERS, sanitizeFilters } from './filters'

describe('sanitizeFilters', () => {
  it('falls back to defaults for junk', () => {
    expect(sanitizeFilters(null)).toEqual(DEFAULT_FILTERS)
    expect(sanitizeFilters('weather')).toEqual(DEFAULT_FILTERS)
    expect(sanitizeFilters({})).toEqual(DEFAULT_FILTERS)
  })

  it('keeps valid values, drops unknown types and clamps ranges', () => {
    expect(
      sanitizeFilters({
        types: ['sar', 'spaceship', 'weather', 42],
        sar_only: 'yes',
        min_elevation_deg: 400,
        rising_within_min: 17.6,
        extra: '<script>',
      }),
    ).toEqual({ types: ['weather', 'sar'], sar_only: false, min_elevation_deg: 90, rising_within_min: 18 })
    expect(sanitizeFilters({ rising_within_min: 1e9, min_elevation_deg: -5 })).toMatchObject({
      rising_within_min: 60,
      min_elevation_deg: 0,
    })
  })

  it('allows an empty type selection', () => {
    expect(sanitizeFilters({ types: [] }).types).toEqual([])
  })
})

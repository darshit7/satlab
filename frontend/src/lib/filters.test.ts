import { describe, expect, it } from 'vitest'
import { DEFAULT_FILTERS, filterSummary, sanitizeFilters, TYPE_LABELS } from './filters'

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
    ).toEqual({
      types: ['weather', 'sar'],
      sar_only: false,
      link_ok_only: false,
      comms_only: false,
      min_elevation_deg: 90,
      rising_within_min: 18,
    })
    expect(sanitizeFilters({ link_ok_only: true }).link_ok_only).toBe(true)
    expect(sanitizeFilters({ link_ok_only: 1 }).link_ok_only).toBe(false)
    expect(sanitizeFilters({ comms_only: true }).comms_only).toBe(true)
    expect(sanitizeFilters({ comms_only: 'true' }).comms_only).toBe(false)
    expect(sanitizeFilters({ rising_within_min: 1e9, min_elevation_deg: -5 })).toMatchObject({
      rising_within_min: 60,
      min_elevation_deg: 0,
    })
  })

  it('allows an empty type selection', () => {
    expect(sanitizeFilters({ types: [] }).types).toEqual([])
  })
})

describe('filterSummary', () => {
  it('describes the defaults', () => {
    expect(filterSummary(DEFAULT_FILTERS, 5)).toEqual(['All types', 'El ≥ mask (5°)', 'Rising ≤ 30 min'])
    expect(filterSummary(DEFAULT_FILTERS, null)).toEqual(['All types', 'El ≥ mask', 'Rising ≤ 30 min'])
  })

  it('names one or two types, counts more, and says when none are left', () => {
    expect(filterSummary({ ...DEFAULT_FILTERS, types: ['sar', 'weather'] }, 5)[0]).toBe('Weather, SAR')
    expect(filterSummary({ ...DEFAULT_FILTERS, types: ['sar', 'weather', 'comms'] }, 5)[0]).toBe('3 types')
    expect(filterSummary({ ...DEFAULT_FILTERS, types: [] }, 5)[0]).toBe('No types')
  })

  it('calls communications satellites Telecom, apart from the Communications filter and section', () => {
    expect(TYPE_LABELS.comms).toBe('Telecom')
    expect(filterSummary({ ...DEFAULT_FILTERS, types: ['comms'] }, 5)[0]).toBe('Telecom')
  })

  it('lists the toggles, the effective elevation and the rising window', () => {
    expect(
      filterSummary(
        { ...DEFAULT_FILTERS, sar_only: true, comms_only: true, link_ok_only: true, min_elevation_deg: 20, rising_within_min: 0 },
        5,
      ),
    ).toEqual(['All types', 'SAR-capable', 'Communications', 'Link OK', 'El ≥ 20°', 'Up now only'])
    // at or under the mask, the mask is what applies
    expect(filterSummary({ ...DEFAULT_FILTERS, min_elevation_deg: 10 }, 10)[1]).toBe('El ≥ mask (10°)')
  })
})

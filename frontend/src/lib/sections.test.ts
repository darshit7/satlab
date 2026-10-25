import { describe, expect, it } from 'vitest'
import { DEFAULT_SECTIONS, sanitizeSections } from './sections'

describe('sanitizeSections', () => {
  it('opens everything for junk', () => {
    expect(sanitizeSections(null)).toEqual(DEFAULT_SECTIONS)
    expect(sanitizeSections('closed')).toEqual(DEFAULT_SECTIONS)
  })

  it('closes only what was stored as false', () => {
    expect(sanitizeSections({ sky: false, systems: 0, comms: 'false', extra: false })).toEqual({
      sky: false,
      systems: true,
      comms: true,
    })
  })
})

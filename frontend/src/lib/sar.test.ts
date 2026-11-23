import { describe, expect, it } from 'vitest'
import {
  colorizeChange,
  datesProblem,
  daysApart,
  decodeChange,
  defaultDates,
  draftSquare,
  fitBox,
  fitView,
  formatArea,
  formatDb,
  hexToRgb,
  imageToCanvas,
  isRunning,
  pairTitle,
  regionsToGeoJSON,
  splitFromKey,
  splitFromPointer,
  statusLabel,
  zoomAt,
} from './sar'
import type { SarRegion } from '../types/api'

describe('draftSquare', () => {
  it('is a closed square of the requested size', () => {
    const ring = draftSquare([-0.425, 39.415], 10)
    expect(ring).toHaveLength(5)
    expect(ring[0]).toEqual(ring[4])
    const [west, south] = ring[0]!
    const [east, north] = ring[2]!
    const kmPerDegLat = 111.2
    expect((north - south) * kmPerDegLat).toBeCloseTo(10, 0)
    expect((east - west) * kmPerDegLat * Math.cos((39.415 * Math.PI) / 180)).toBeCloseTo(10, 0)
  })
})

describe('dates', () => {
  it('defaults to two repeats back from today', () => {
    expect(defaultDates(Date.parse('2026-11-22T10:00:00Z'))).toEqual({ before: '2026-10-29', after: '2026-11-22' })
  })
  it('explains what is wrong', () => {
    expect(datesProblem('2024-10-20', '2024-11-01', '2014-10-10', '2026-11-22')).toBeNull()
    expect(datesProblem('', '2024-11-01', '2014-10-10', '2026-11-22')).toMatch(/both/)
    expect(datesProblem('2013-01-01', '2024-11-01', '2014-10-10', '2026-11-22')).toMatch(/starts/)
    expect(datesProblem('2024-10-20', '2027-01-01', '2014-10-10', '2026-11-22')).toMatch(/future/)
    expect(datesProblem('2024-11-01', '2024-11-01', '2014-10-10', '2026-11-22')).toMatch(/earlier/)
  })
  it('counts days and titles a pair', () => {
    expect(daysApart('2024-10-20T06:02:00Z', '2024-11-01T06:02:00Z')).toBe(12)
    expect(pairTitle('2024-10-20T06:02:00Z', '2024-11-01T06:02:00Z')).toBe('20 Oct → 1 Nov 2024')
    expect(pairTitle('2024-12-30T06:02:00Z', '2025-01-11T06:02:00Z')).toBe('30 Dec 2024 → 11 Jan 2025')
  })
})

describe('formatting', () => {
  it('formats areas and dB', () => {
    expect(formatArea(5000)).toBe('5,000 m²')
    expect(formatArea(150_000)).toBe('15.0 ha')
    expect(formatArea(2_500_000)).toBe('2.50 km²')
    expect(formatDb(6.31)).toBe('+6.3 dB')
    expect(formatDb(-7)).toBe('−7.0 dB')
  })
  it('words statuses', () => {
    expect(statusLabel({ status: 'queued', queue_position: 2 })).toBe('Queued (2nd)')
    expect(statusLabel({ status: 'expired' })).toBe('Images expired')
    expect(isRunning({ status: 'reading' })).toBe(true)
    expect(isRunning({ status: 'ready' })).toBe(false)
  })
})

describe('change codes', () => {
  it('decodes the bands', () => {
    expect(decodeChange(0)).toBeNull()
    expect(decodeChange(1)).toBeNull()
    expect(decodeChange(128)).toBeNull()
    expect(decodeChange(2)).toEqual({ sign: 'decrease', t: 0 })
    expect(decodeChange(127)).toEqual({ sign: 'decrease', t: 1 })
    expect(decodeChange(254)).toEqual({ sign: 'increase', t: 1 })
  })
  it('colours only flagged pixels', () => {
    const src = new Uint8ClampedArray([0, 0, 0, 255, 1, 0, 0, 255, 2, 0, 0, 255, 254, 0, 0, 255])
    const dst = new Uint8ClampedArray(src.length)
    colorizeChange(src, dst, [255, 0, 255], [0, 255, 255], 1)
    expect(dst[3]).toBe(0)
    expect(dst[7]).toBe(0)
    expect([...dst.slice(8, 11)]).toEqual([0, 255, 255])
    expect(dst[11]).toBe(Math.round(255 * 0.35))
    expect([...dst.slice(12, 15)]).toEqual([255, 0, 255])
    expect(dst[15]).toBe(Math.round(255 * 0.9))
  })
  it('reads hex colours', () => {
    expect(hexToRgb('#a21caf')).toEqual([162, 28, 175])
    expect(hexToRgb('nonsense')).toEqual([128, 128, 128])
  })
})

describe('map overlays', () => {
  it('keeps the server rings and marks the selection', () => {
    const region: SarRegion = {
      id: 3,
      sign: 'decrease',
      area_m2: 1,
      mean_db: -5,
      max_abs_db: 8,
      polygon: [
        [0, 0],
        [1, 0],
        [1, 1],
        [0, 0],
      ],
      bbox: [0, 0, 1, 1],
      centroid: [0.5, 0.5],
      px: [0, 0, 10, 10],
    }
    const fc = regionsToGeoJSON([region], 3)
    expect(fc.features[0]!.properties).toEqual({ id: 3, sign: 'decrease', selected: true })
  })
})

describe('swipe and view math', () => {
  it('fits and zooms around a fixed point', () => {
    const view = fitView(1000, 1000, 500, 250)
    expect(view.scale).toBe(0.25)
    expect(imageToCanvas(view, 500, 500)).toEqual([250, 125])
    const zoomed = zoomAt(view, 2, 100, 50, view.scale)
    const before = [view.x + 100 / view.scale, view.y + 50 / view.scale]
    const after = [zoomed.x + 100 / zoomed.scale, zoomed.y + 50 / zoomed.scale]
    expect(after[0]).toBeCloseTo(before[0]!)
    expect(after[1]).toBeCloseTo(before[1]!)
    expect(zoomAt(view, 0.01, 0, 0, view.scale).scale).toBe(view.scale)
  })
  it('frames a box', () => {
    const view = fitBox([100, 100, 200, 200], 400, 400, 0.1)
    expect(imageToCanvas(view, 150, 150)).toEqual([200, 200])
  })
  it('moves the split by pointer and keys', () => {
    expect(splitFromPointer(150, 100, 200)).toBe(25)
    expect(splitFromPointer(0, 100, 200)).toBe(0)
    expect(splitFromKey(50, 'ArrowRight')).toBe(52)
    expect(splitFromKey(5, 'PageDown')).toBe(0)
    expect(splitFromKey(50, 'End')).toBe(100)
    expect(splitFromKey(50, 'a')).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { lerpLonLat, positionOnTrack, splitAntimeridian, wrapLon, type LonLat } from './geo'

describe('wrapLon', () => {
  it('normalises into [-180, 180)', () => {
    expect(wrapLon(190)).toBe(-170)
    expect(wrapLon(-190)).toBe(170)
    expect(wrapLon(180)).toBe(-180)
    expect(wrapLon(45)).toBe(45)
  })
})

describe('lerpLonLat', () => {
  it('takes the short way across the antimeridian', () => {
    const [lon, lat] = lerpLonLat([170, 0], [-170, 10], 0.5)
    expect(Math.abs(lon)).toBeCloseTo(180)
    expect(lat).toBeCloseTo(5)
  })
})

describe('splitAntimeridian', () => {
  it('leaves a non-crossing line intact', () => {
    const pts: LonLat[] = [[0, 0], [10, 5], [20, 10]]
    expect(splitAntimeridian(pts)).toEqual([pts])
  })

  it('splits eastward crossings with an interpolated edge point', () => {
    const segments = splitAntimeridian([[170, 0], [-170, 10], [-160, 12]])
    expect(segments).toHaveLength(2)
    expect(segments[0]!.at(-1)).toEqual([180, 5])
    expect(segments[1]![0]).toEqual([-180, 5])
  })

  it('splits westward crossings', () => {
    const segments = splitAntimeridian([[-170, 0], [170, -10]])
    expect(segments[0]!.at(-1)).toEqual([-180, -5])
    expect(segments[1]![0]).toEqual([180, -5])
  })
})

describe('positionOnTrack', () => {
  const track = { startMs: 0, endMs: 2000, points: [[0, 0], [10, 10], [20, 0]] as LonLat[] }

  it('interpolates between evenly time-spaced samples', () => {
    expect(positionOnTrack(track, 500)).toEqual([5, 5])
    expect(positionOnTrack(track, 2000)).toEqual([20, 0])
  })

  it('returns null outside the sampled window', () => {
    expect(positionOnTrack(track, -1)).toBeNull()
    expect(positionOnTrack(track, 2001)).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { altitudeOnTrack, inViewRuns, lerpLonLat, positionOnTrack, splitAntimeridian, wrapLon, type LonLat } from './geo'

describe('altitudeOnTrack', () => {
  const track = { startMs: 0, endMs: 2000, points: [[0, 0], [1, 0], [2, 0]] as LonLat[], alt_m: [800, 900, 700] }

  it('interpolates the server heights at the sim time', () => {
    expect(altitudeOnTrack(track, 500)).toBeCloseTo(850)
    expect(altitudeOnTrack(track, 1500)).toBeCloseTo(800)
  })

  it('is null outside the track, or without (aligned) heights', () => {
    expect(altitudeOnTrack(track, 2500)).toBeNull()
    expect(altitudeOnTrack({ ...track, alt_m: undefined }, 500)).toBeNull()
    expect(altitudeOnTrack({ ...track, alt_m: [800] }, 500)).toBeNull()
  })
})

describe('inViewRuns', () => {
  it('splits at each change, sharing the boundary point', () => {
    expect(inViewRuns([false, false, true, true, false], 5)).toEqual([
      { inView: false, start: 0, end: 1 },
      { inView: true, start: 1, end: 3 },
      { inView: false, start: 3, end: 4 },
    ])
  })

  it('is one run when nothing changes', () => {
    expect(inViewRuns([true, true, true], 3)).toEqual([{ inView: true, start: 0, end: 2 }])
  })

  it('is empty without usable flags', () => {
    expect(inViewRuns(null, 3)).toEqual([])
    expect(inViewRuns([true, false], 3)).toEqual([])
  })
})

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

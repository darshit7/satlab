import { describe, expect, it } from 'vitest'
import {
  altitudeOnTrack,
  destination,
  inViewRuns,
  lerpLonLat,
  nightPolygon,
  positionOnTrack,
  smallCircle,
  splitAntimeridian,
  subsolarAt,
  terminatorLine,
  unitVector,
  wrapLon,
  type LonLat,
} from './geo'

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

/** Great-circle distance in degrees (haversine). */
function distanceDeg(a: LonLat, b: LonLat): number {
  const r = Math.PI / 180
  const h =
    Math.sin(((b[1] - a[1]) * r) / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(((b[0] - a[0]) * r) / 2) ** 2
  return (2 * Math.asin(Math.sqrt(h))) / r
}

const dot = (a: number[], b: number[]) => a.reduce((sum, x, i) => sum + x * b[i]!, 0)

describe('smallCircle', () => {
  it('puts every point at the radius, closed, with unwrapped longitudes', () => {
    const center: LonLat = [-75, 38]
    const ring = smallCircle(center, 23, 64)
    expect(ring).toHaveLength(65)
    for (const p of ring) expect(distanceDeg(center, p)).toBeCloseTo(23, 6)
    expect(ring[0]![0]).toBeCloseTo(ring.at(-1)![0], 6)
    for (let i = 1; i < ring.length; i++) expect(Math.abs(ring[i]![0] - ring[i - 1]![0])).toBeLessThan(180)
  })

  it('splits at the antimeridian on a flat map', () => {
    const ring = smallCircle([179, 10], 20).map(([lon, lat]): LonLat => [wrapLon(lon), lat])
    expect(splitAntimeridian(ring).length).toBeGreaterThan(1)
  })

  it('goes once round the pole when it encloses it', () => {
    const ring = smallCircle([0, 80], 23)
    expect(Math.abs(ring.at(-1)![0] - ring[0]![0])).toBeCloseTo(360, 6)
    const flat = ring.map(([lon, lat]): LonLat => [wrapLon(lon), lat])
    expect(splitAntimeridian(flat)).toHaveLength(2)
  })

  it('heads the right way', () => {
    expect(destination([0, 0], 10, 0)).toEqual([0, 10])
    const [lon, lat] = destination([0, 0], 10, 90)
    expect(lon).toBeCloseTo(10, 9)
    expect(lat).toBeCloseTo(0, 9)
  })
})

describe('terminatorLine and nightPolygon', () => {
  it('runs where the Sun is on the horizon', () => {
    const sun: LonLat = [-100, 13.5]
    const s = unitVector(sun)
    const line = terminatorLine(sun)
    expect(line[0]![0]).toBe(-180)
    expect(line.at(-1)![0]).toBe(180)
    for (const p of line) expect(dot(unitVector(p), s)).toBeCloseTo(0, 9)
  })

  it('closes through the pole in darkness', () => {
    const northernSummer = nightPolygon([0, 23.4])
    expect(northernSummer.at(-2)).toEqual([-180, -90])
    expect(northernSummer[0]).toEqual(northernSummer.at(-1))
    expect(nightPolygon([0, -23.4]).at(-2)).toEqual([-180, 90])
  })

  it('has the antisolar point inside and the subsolar point outside', () => {
    // at the antisolar longitude the terminator is at its farthest from the dark pole
    const line = terminatorLine([30, 20])
    const at = (lon: number) => line.find((p) => p[0] === lon)![1]
    expect(-20).toBeLessThan(at(-150)) // the antisolar point (-150, -20) is below the line: night
    expect(20).toBeGreaterThan(at(30)) // the subsolar point is on the day side
  })

  it('stays finite at an equinox', () => {
    for (const [, lat] of nightPolygon([45, 0])) expect(Number.isFinite(lat)).toBe(true)
  })
})

describe('subsolarAt', () => {
  const sun = { subsolar_lat_deg: 10, subsolar_lon_deg: -179.9, lat_rate_deg_s: 1e-6, lon_rate_deg_s: -360 / 86_400 }

  it('advances by the server rates, across the antimeridian', () => {
    const [lon, lat] = subsolarAt(sun, 0, 60_000) // a minute of sim time
    expect(lon).toBeCloseTo(179.85, 6)
    expect(lat).toBeCloseTo(10.00006, 6)
  })

  it('stays put at the sample time (a paused clock)', () => {
    const [lon, lat] = subsolarAt(sun, 5_000, 5_000)
    expect(lon).toBeCloseTo(-179.9, 9)
    expect(lat).toBe(10)
  })
})

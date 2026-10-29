import { describe, expect, it } from 'vitest'
import type { GroundTrackMessage } from '../../types/ws'
import { orbitRuns } from './orbit'

// Five samples a minute apart along the equator; in view for the middle three.
const base: GroundTrackMessage = {
  type: 'ground_track',
  norad_id: 1,
  start_time: '2026-10-10T00:00:00Z',
  end_time: '2026-10-10T00:04:00Z',
  period_s: 6000,
  points: [
    [0, 0],
    [1, 0],
    [2, 0],
    [3, 0],
    [4, 0],
  ],
  alt_m: [800, 800, 800, 800, 800],
  above_mask: [false, true, true, true, false],
}

const crossings: GroundTrackMessage['crossings'] = [
  { time: '2026-10-10T00:00:30Z', kind: 'aos', lon_deg: 0.5, lat_deg: 0, alt_m: 800 },
  { time: '2026-10-10T00:03:45Z', kind: 'los', lon_deg: 3.75, lat_deg: 0, alt_m: 800 },
]

const lons = (points: number[][]) => points.map((p) => p[0])

describe('orbitRuns', () => {
  it('starts and ends the in-view run on the exact crossings', () => {
    const runs = orbitRuns({ ...base, crossings }, 0, false)
    expect(runs.map((r) => r.state)).toEqual(['out', 'in', 'out'])
    expect(lons(runs[0]!.points)).toEqual([0, 0.5])
    expect(lons(runs[1]!.points)).toEqual([0.5, 1, 2, 3, 3.75])
    expect(lons(runs[2]!.points)).toEqual([3.75, 4])
  })

  it('splits the in-view part by link state halfway between samples', () => {
    const runs = orbitRuns({ ...base, crossings, rag: ['red', 'amber', 'green', 'green', 'red'] }, 0, true)
    expect(runs.map((r) => r.state)).toEqual(['out', 'amber', 'green', 'out'])
    expect(lons(runs[1]!.points)).toEqual([0.5, 1, 1.5])
    expect(lons(runs[2]!.points)).toEqual([1.5, 2, 3, 3.75])
  })

  it('ignores the link state unless asked, or when misaligned', () => {
    const rag = ['red', 'amber', 'green', 'green', 'red'] as const
    expect(orbitRuns({ ...base, crossings, rag: [...rag] }, 0, false).map((r) => r.state)).toEqual(['out', 'in', 'out'])
    expect(orbitRuns({ ...base, crossings, rag: ['green'] }, 0, true).map((r) => r.state)).toEqual(['out', 'in', 'out'])
  })

  it('falls back to the samples without crossings, as before', () => {
    const runs = orbitRuns(base, 0, false)
    // as inViewRuns: a run starts on the last sample before the change
    expect(runs.map((r) => [r.state, lons(r.points)])).toEqual([
      ['in', [0, 1, 2, 3]],
      ['out', [3, 4]],
    ])
  })

  it('is one faded run without in-view flags, at the fallback height', () => {
    const runs = orbitRuns({ ...base, above_mask: null, alt_m: null }, 500, true)
    expect(runs).toHaveLength(1)
    expect(runs[0]!.state).toBe('out')
    expect(runs[0]!.points[0]).toEqual([0, 0, 500])
  })
})

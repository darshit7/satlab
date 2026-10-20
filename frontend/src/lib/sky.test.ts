import { describe, expect, it } from 'vitest'
import {
  arcRuns,
  arcSegment,
  nearest,
  positionOnArc,
  project,
  proximityRadius,
  radialFraction,
  ringRadius,
  type SkyProjection,
  type TimedArc,
} from './sky'

const polar: SkyProjection = { cx: 100, cy: 100, radius: 100, eastLeft: false, kind: 'equidistant' }
const allSky: SkyProjection = { ...polar, eastLeft: true, kind: 'equisolid' }

const close = (a: number[], b: number[], digits = 6) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, digits))

describe('project', () => {
  it('puts the zenith at the centre and the horizon on the rim', () => {
    close(project(123, 90, polar), [100, 100])
    close(project(0, 0, polar), [100, 0]) // north up
    close(project(180, 0, polar), [100, 200])
  })

  it('draws east on the right for the polar plot and on the left looking up', () => {
    close(project(90, 0, polar), [200, 100])
    close(project(90, 0, allSky), [0, 100])
    close(project(270, 0, allSky), [200, 100])
  })

  it('is linear in zenith angle for the polar plot, fisheye for the all-sky view', () => {
    expect(radialFraction(45, 'equidistant')).toBeCloseTo(0.5)
    expect(radialFraction(0, 'equisolid')).toBeCloseTo(1)
    expect(radialFraction(45, 'equisolid')).toBeCloseTo(Math.sin(Math.PI / 8) / Math.SQRT1_2)
    expect(radialFraction(-3, 'equidistant')).toBeCloseTo(1) // below the horizon clamps to the rim
  })
})

// A pass from the north-west horizon through the zenith to the south-east horizon.
const overhead: TimedArc = {
  startMs: 0,
  endMs: 4000,
  points: [
    [315, 0],
    [315, 45],
    [135, 90],
    [135, 45],
    [135, 0],
  ],
}

describe('positionOnArc', () => {
  it('returns null outside the arc', () => {
    expect(positionOnArc(overhead, -1, polar)).toBeNull()
    expect(positionOnArc(overhead, 4001, polar)).toBeNull()
  })

  it('hits the samples and interpolates through the zenith in x/y, not az/el', () => {
    close(positionOnArc(overhead, 2000, polar)!, [100, 100])
    // halfway between 45° up in the NW and the zenith: a straight line on the plot
    const nw45 = project(315, 45, polar)
    close(positionOnArc(overhead, 1500, polar)!, [(nw45[0] + 100) / 2, (nw45[1] + 100) / 2])
  })
})

describe('arcSegment', () => {
  it('clips to the requested interval with interpolated ends', () => {
    const seg = arcSegment(overhead, 1500, 2500, polar)
    expect(seg).toHaveLength(3) // start, the zenith sample, end
    close(seg[0]!, positionOnArc(overhead, 1500, polar)!)
    close(seg[1]!, [100, 100])
    close(seg[2]!, positionOnArc(overhead, 2500, polar)!)
  })

  it('is empty when the interval misses the arc', () => {
    expect(arcSegment(overhead, 5000, 6000, polar)).toEqual([])
    expect(arcSegment(overhead, 3000, 3000, polar)).toEqual([])
  })

  it('covers the whole arc when asked for more', () => {
    expect(arcSegment(overhead, -10_000, 10_000, polar)).toHaveLength(5)
  })
})

describe('arcRuns', () => {
  const rag = ['red', 'amber', 'green', 'amber', 'red'] as const

  it('splits halfway between samples whose state differs', () => {
    const runs = arcRuns(overhead, rag, -Infinity, Infinity, polar)
    expect(runs.map((r) => r.state)).toEqual(['red', 'amber', 'green', 'amber', 'red'])
    // red holds until halfway to the second sample (t = 500), where amber takes over
    close(runs[0]!.points.at(-1)!, positionOnArc(overhead, 500, polar)!)
    close(runs[1]!.points[0]!, positionOnArc(overhead, 500, polar)!)
    // the green run is centred on the zenith sample
    close(runs[2]!.points[0]!, positionOnArc(overhead, 1500, polar)!)
    close(runs[2]!.points.at(-1)!, positionOnArc(overhead, 2500, polar)!)
  })

  it('merges equal neighbours and clips to the interval', () => {
    const runs = arcRuns(overhead, ['amber', 'amber', 'green', 'green', 'green'], 1000, 4000, polar)
    expect(runs.map((r) => r.state)).toEqual(['amber', 'green'])
    close(runs[0]!.points[0]!, positionOnArc(overhead, 1000, polar)!)
    close(runs[1]!.points.at(-1)!, project(135, 0, polar))
  })

  it('needs one state per sample', () => {
    expect(arcRuns(overhead, ['green'], 0, 4000, polar)).toEqual([])
  })
})

describe('ringRadius', () => {
  it('matches the projection', () => {
    expect(ringRadius(90, polar)).toBeCloseTo(0)
    expect(ringRadius(45, polar)).toBeCloseTo(50)
    expect(ringRadius(0, allSky)).toBeCloseTo(100)
  })
})

describe('nearest', () => {
  const pts = [
    { id: 1, x: 10, y: 10 },
    { id: 2, x: 14, y: 10 },
  ]
  it('picks the closest point within reach', () => {
    expect(nearest(pts, 13, 10, 8)).toBe(2)
    expect(nearest(pts, 50, 50, 8)).toBeNull()
  })
})

describe('proximityRadius', () => {
  it('shrinks with range and stays within bounds', () => {
    expect(proximityRadius(400_000)).toBeCloseTo(6)
    expect(proximityRadius(36_000_000)).toBeLessThan(2)
    expect(proximityRadius(1_000)).toBe(6)
    expect(proximityRadius(1_000_000)).toBeGreaterThan(proximityRadius(2_000_000))
  })
})

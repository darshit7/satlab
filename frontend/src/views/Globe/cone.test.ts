import { describe, expect, it } from 'vitest'
import { coneShapes, slantRangeM } from './cone'

const R = 6_371_000 // station radius (m)
const LEO = R + 830_000 // NOAA 20's shell

describe('slantRangeM', () => {
  it('is the height straight up', () => {
    expect(slantRangeM(90, R, LEO)).toBeCloseTo(830_000, 3)
  })

  it('is longest at the horizon, by Pythagoras on the tangent', () => {
    expect(slantRangeM(0, R, LEO)).toBeCloseTo(Math.sqrt(LEO * LEO - R * R), 3)
    expect(slantRangeM(5, R, LEO)).toBeLessThan(slantRangeM(0, R, LEO))
    expect(slantRangeM(5, R, LEO)).toBeGreaterThan(slantRangeM(45, R, LEO))
  })
})

describe('coneShapes', () => {
  it('ends every cone on the satellite shell', () => {
    for (const s of coneShapes(5, [[22.5, 90]], R, LEO)) {
      // rim point in the station's local frame, measured from the Earth's centre
      const fromCentre = Math.hypot(s.rimRadiusM, R + s.heightM)
      expect(fromCentre).toBeCloseTo(LEO, 0)
    }
  })

  it('draws the mask cone, then one surface per band edge below the zenith', () => {
    const shapes = coneShapes(5, [[22.5, 90]], R, LEO)
    expect(shapes.map((s) => [s.kind, s.elevationDeg])).toEqual([
      ['mask', 5],
      ['strong', 22.5],
    ])
    // an isoflux pattern can leave a band that stops short of the zenith: both edges
    expect(coneShapes(5, [[10, 40]], R, LEO).map((s) => s.elevationDeg)).toEqual([5, 10, 40])
  })

  it('lets a green-from-the-mask link take over the mask surface', () => {
    // FengYun 3D over Wallops: green from 5.0°, so the whole in-view cone is the strong one
    expect(coneShapes(5, [[5, 90]], R, LEO).map((s) => [s.kind, s.elevationDeg])).toEqual([['strong', 5]])
  })

  it('has nothing to draw for an impossible shell', () => {
    expect(coneShapes(5, [], R, R - 1)).toEqual([])
    expect(coneShapes(5, [], 0, LEO)).toEqual([])
  })

  it('keeps what lies under the mask out', () => {
    expect(coneShapes(10, [[2, 8]], R, LEO).map((s) => s.kind)).toEqual(['mask'])
    // a band reaching under the mask is green from the mask up
    expect(coneShapes(10, [[5, 90]], R, LEO).map((s) => [s.kind, s.elevationDeg])).toEqual([['strong', 10]])
  })
})

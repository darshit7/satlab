import { describe, expect, it } from 'vitest'
import { coneShapes, coneSurface, insideCone, slantRangeM } from './cone'

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

describe('coneSurface', () => {
  const [mask] = coneShapes(5, [], R, LEO)

  it('fans from the apex to the rim, with no lid', () => {
    const surface = coneSurface(mask!, 0.06, 0, 8)
    expect(surface.positions.length).toBe(9 * 3)
    expect(surface.indices.length).toBe(8 * 3)
    expect([...surface.positions.slice(0, 3)]).toEqual([0, 0, 0])
    for (let i = 1; i <= 8; i++) {
      const [x, y, z] = surface.positions.slice(i * 3, i * 3 + 3)
      expect(Math.hypot(x!, y!)).toBeCloseTo(mask!.rimRadiusM, 3)
      expect(z).toBeCloseTo(mask!.heightM, 6)
    }
    // every triangle has the apex: no rim-only (lid) triangle
    for (let t = 0; t < 8; t++) expect(surface.indices[t * 3]).toBe(0)
  })

  it('fades from the apex to the rim', () => {
    const { alphas } = coneSurface(mask!, 0.3, 0.05, 8)
    expect(alphas[0]).toBeCloseTo(0.3)
    expect([...alphas.slice(1)].every((a) => Math.abs(a - 0.05) < 1e-6)).toBe(true)
    expect(coneSurface(mask!, 0.3, undefined, 8).alphas[1]).toBe(0)
  })
})

describe('insideCone', () => {
  const [mask] = coneShapes(5, [], R, LEO)

  it('is inside above the cone surface and below the rim', () => {
    expect(insideCone([0, 0, 1000], mask!)).toBe(true) // straight up
    expect(insideCone([10_000, 0, 10_000], mask!)).toBe(true) // 45°
  })

  it('is outside below the surface, under the ground, or above the rim', () => {
    expect(insideCone([100_000, 0, 1000], mask!)).toBe(false) // ~0.6° up: under the 5° surface
    expect(insideCone([0, 0, -10], mask!)).toBe(false)
    expect(insideCone([0, 0, mask!.heightM + 1], mask!)).toBe(false)
  })
})

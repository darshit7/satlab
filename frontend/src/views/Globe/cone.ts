// The visibility "V" cone, as shapes in the station's local east-north-up frame (design §5.4).
// Presentation only: the angles come from the server (the horizon mask, and the selected
// satellite's strong-link elevation bands from its link budget) and the height from its streamed
// position; nothing here decides a link state.

/** Above the cone surfaces the satellite is in view (mask), in the green-margin region (strong), or
 *  above the Overhead filter's minimum elevation, when that is higher than the mask (shown). */
export type ConeKind = 'mask' | 'strong' | 'shown'

export interface ConeShape {
  kind: ConeKind
  /** Elevation of the cone's surface above the local horizon. */
  elevationDeg: number
  /** Along local up, from the station's apex to the rim. */
  heightM: number
  /** Radius of the rim. */
  rimRadiusM: number
}

/** Below this, a band edge counts as the zenith: no cone surface to draw there. */
const ZENITH_EDGE_DEG = 89.9
/** A band edge this close to the mask is the mask's own surface. */
const SAME_SURFACE_DEG = 0.05

/**
 * Slant range from a station `stationRadiusM` from the Earth's centre to a satellite at
 * `satRadiusM`, seen at elevation `elevDeg`: the same spherical geometry as the backend's
 * strong-link bands. NaN if the satellite isn't above the station.
 */
export function slantRangeM(elevDeg: number, stationRadiusM: number, satRadiusM: number): number {
  const e = (elevDeg * Math.PI) / 180
  const cos = stationRadiusM * Math.cos(e)
  return -stationRadiusM * Math.sin(e) + Math.sqrt(satRadiusM * satRadiusM - cos * cos)
}

function shape(kind: ConeKind, elevationDeg: number, stationRadiusM: number, satRadiusM: number): ConeShape {
  const slant = slantRangeM(elevationDeg, stationRadiusM, satRadiusM)
  const e = (elevationDeg * Math.PI) / 180
  return { kind, elevationDeg, heightM: slant * Math.sin(e), rimRadiusM: slant * Math.cos(e) }
}

/**
 * The outer cone (above the horizon mask), the inner strong-link cone surfaces (one per band edge
 * below the zenith) and, when the Overhead filter shows only satellites above a higher elevation
 * (`shownDeg`), that floor's cone; each ends where it meets the satellite's altitude shell: the rim
 * is where a satellite at that height crosses the boundary (AOS / LOS for the outer one). When the
 * link is green right from the mask, the strong-link surface is the mask's and replaces it.
 */
export function coneShapes(
  maskDeg: number,
  strongBandsDeg: readonly (readonly [number, number])[],
  stationRadiusM: number,
  satRadiusM: number,
  shownDeg?: number | null,
): ConeShape[] {
  if (!(satRadiusM > stationRadiusM && stationRadiusM > 0)) return []
  const mask = Math.max(0, maskDeg)
  // only what lies above the mask: a band reaching under it is green from the mask up
  const edges = strongBandsDeg
    .filter(([, hi]) => hi > mask)
    .flatMap(([lo, hi]) => [Math.max(lo, mask), hi])
    .filter((edge) => edge < ZENITH_EDGE_DEG)
  const shapes = [
    ...(edges.some((edge) => edge - mask < SAME_SURFACE_DEG) ? [] : [shape('mask', mask, stationRadiusM, satRadiusM)]),
    ...edges.map((edge) => shape('strong', edge, stationRadiusM, satRadiusM)),
    ...(shownDeg != null && shownDeg - mask >= SAME_SURFACE_DEG && shownDeg < ZENITH_EDGE_DEG
      ? [shape('shown', shownDeg, stationRadiusM, satRadiusM)]
      : []),
  ]
  return shapes.filter((s) => Number.isFinite(s.heightM) && Number.isFinite(s.rimRadiusM))
}

export interface ConeSurface {
  /** East-north-up metres from the station: the apex, then the rim ring. */
  positions: Float64Array
  /** Triangles (apex, rim i, rim i + 1). */
  indices: Uint16Array
  /** Per vertex, 0–1: `apexAlpha` at the apex fading to `rimAlpha` at the rim. */
  alphas: Float32Array
}

/**
 * The side of a cone, open at the rim (no lid), as one fan from the apex: the translucent cone,
 * fading from the station upwards.
 */
export function coneSurface(shape: ConeShape, apexAlpha: number, rimAlpha = 0, slices = 96): ConeSurface {
  const positions = new Float64Array((slices + 1) * 3)
  const alphas = new Float32Array(slices + 1).fill(rimAlpha)
  alphas[0] = apexAlpha
  for (let i = 0; i < slices; i++) {
    const a = (2 * Math.PI * i) / slices
    positions.set([shape.rimRadiusM * Math.sin(a), shape.rimRadiusM * Math.cos(a), shape.heightM], (i + 1) * 3)
  }
  const indices = new Uint16Array(slices * 3)
  for (let i = 0; i < slices; i++) indices.set([0, i + 1, ((i + 1) % slices) + 1], i * 3)
  return { positions, indices, alphas }
}

/** Whether a point (east-north-up metres from the station) is inside the cone, below its rim:
 *  there, a fill all around the camera would tint the whole view. */
export function insideCone([east, north, up]: readonly [number, number, number], shape: ConeShape): boolean {
  if (!(up > 0 && up < shape.heightM)) return false
  return Math.atan2(up, Math.hypot(east, north)) > (shape.elevationDeg * Math.PI) / 180
}

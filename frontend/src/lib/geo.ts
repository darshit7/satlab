// Presentation-only geometry helpers. Nothing here derives orbits: positions come from the
// backend (snapshots and ground-track samples); these functions only draw and tween them.

export type LonLat = [lon: number, lat: number]

/** Normalise a longitude to [-180, 180). */
export function wrapLon(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180
}

/** Interpolate between two points along the shorter way around in longitude. */
export function lerpLonLat(a: LonLat, b: LonLat, t: number): LonLat {
  const dLon = wrapLon(b[0] - a[0])
  return [wrapLon(a[0] + dLon * t), a[1] + (b[1] - a[1]) * t]
}

/**
 * Split a polyline where it crosses the antimeridian so the map doesn't draw a line across the
 * whole world. A crossing point at ±180° is inserted on both sides of each split.
 */
export function splitAntimeridian(points: LonLat[]): LonLat[][] {
  const segments: LonLat[][] = []
  let current: LonLat[] = []
  for (let i = 0; i < points.length; i++) {
    const p = points[i]!
    const prev = points[i - 1]
    if (prev && Math.abs(p[0] - prev[0]) > 180) {
      const edge = prev[0] > 0 ? 180 : -180
      const dLon = wrapLon(p[0] - prev[0])
      const t = dLon === 0 ? 0 : (edge - prev[0]) / dLon
      const lat = prev[1] + (p[1] - prev[1]) * t
      current.push([edge, lat])
      segments.push(current)
      current = [[-edge, lat]]
    }
    current.push(p)
  }
  if (current.length > 0) segments.push(current)
  return segments.filter((s) => s.length > 1)
}

export interface TimedTrack {
  startMs: number
  endMs: number
  points: LonLat[] // evenly spaced in time from startMs to endMs
}

/** Sub-point at `timeMs` by interpolating a server-sampled ground track, or null if outside it. */
export function positionOnTrack(track: TimedTrack, timeMs: number): LonLat | null {
  const n = track.points.length
  if (n < 2 || timeMs < track.startMs || timeMs > track.endMs) return null
  const f = ((timeMs - track.startMs) / (track.endMs - track.startMs)) * (n - 1)
  const i = Math.min(Math.floor(f), n - 2)
  return lerpLonLat(track.points[i]!, track.points[i + 1]!, f - i)
}

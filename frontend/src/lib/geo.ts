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
  /** Height above the ellipsoid (m) per point, when the server sent it. */
  alt_m?: number[]
}

/** The segment `timeMs` falls in and how far along it, or null outside the track. */
function locate(track: TimedTrack, timeMs: number): [i: number, t: number] | null {
  const n = track.points.length
  if (n < 2 || timeMs < track.startMs || timeMs > track.endMs) return null
  const f = ((timeMs - track.startMs) / (track.endMs - track.startMs)) * (n - 1)
  const i = Math.min(Math.floor(f), n - 2)
  return [i, f - i]
}

/** Sub-point at `timeMs` by interpolating a server-sampled ground track, or null if outside it. */
export function positionOnTrack(track: TimedTrack, timeMs: number): LonLat | null {
  const at = locate(track, timeMs)
  return at && lerpLonLat(track.points[at[0]]!, track.points[at[0] + 1]!, at[1])
}

/** Height at `timeMs` along the track, or null outside it (or without server heights). */
export function altitudeOnTrack(track: TimedTrack, timeMs: number): number | null {
  const alt = track.alt_m
  const at = locate(track, timeMs)
  if (!at || !alt || alt.length !== track.points.length) return null
  const [i, t] = at
  return alt[i]! + (alt[i + 1]! - alt[i]!) * t
}

export interface Run {
  inView: boolean
  /** First and last point index, inclusive; consecutive runs share their boundary point. */
  start: number
  end: number
}

/** Split a track into runs by the server's per-point above-the-mask flags, so the in-view part of
 *  an orbit can be drawn bright and the rest faded. Empty without flags (or misaligned ones). */
export function inViewRuns(aboveMask: readonly boolean[] | null | undefined, n: number): Run[] {
  if (!aboveMask || aboveMask.length !== n || n < 2) return []
  const runs: Run[] = []
  let start = 0
  for (let i = 1; i <= n; i++) {
    if (i < n && aboveMask[i] === aboveMask[start]) continue
    runs.push({ inView: aboveMask[start]!, start: Math.max(0, start - 1), end: i - 1 })
    start = i
  }
  // only a first run of one point has no segment of its own (later runs start at the boundary)
  return runs.filter((r) => r.end > r.start)
}

const RAD = Math.PI / 180

/** The point `distDeg` of great-circle distance from `center` towards `bearingDeg` (spherical). */
export function destination(center: LonLat, distDeg: number, bearingDeg: number): LonLat {
  const [lon, lat] = [center[0] * RAD, center[1] * RAD]
  const d = distDeg * RAD
  const b = bearingDeg * RAD
  const sinLat = Math.sin(lat) * Math.cos(d) + Math.cos(lat) * Math.sin(d) * Math.cos(b)
  const lat2 = Math.asin(Math.max(-1, Math.min(1, sinLat)))
  const lon2 = lon + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(lat), Math.cos(d) - Math.sin(lat) * sinLat)
  return [lon2 / RAD, lat2 / RAD]
}

/**
 * A closed ring of points `radiusDeg` (great-circle) around `center`, e.g. a satellite's footprint
 * from its server-given radius. Longitudes are unwrapped (each within 180° of the previous one), so
 * a ring around a pole spans 360°; wrap and split it for a flat map.
 */
export function smallCircle(center: LonLat, radiusDeg: number, n = 128): LonLat[] {
  const ring: LonLat[] = []
  for (let i = 0; i <= n; i++) {
    const p = destination(center, radiusDeg, (360 * i) / n)
    const prev = ring[i - 1]
    ring.push(prev ? [prev[0] + wrapLon(p[0] - prev[0]), p[1]] : [wrapLon(p[0]), p[1]])
  }
  return ring
}

/** The subsolar point, closer than this to the equator, counts as this far off it: at an equinox
 *  the terminator runs through the poles and the formula below has no slope to give. */
const MIN_SUN_LAT_DEG = 0.01

/**
 * The day/night line on a spherical Earth: where the Sun (overhead at `subsolar`) is on the horizon,
 * sampled every `stepDeg` of longitude from -180 to 180 (tan φ = -cos(λ - λs) / tan δ).
 */
export function terminatorLine(subsolar: LonLat, stepDeg = 1): LonLat[] {
  const lat = subsolar[1]
  const dec = (Math.abs(lat) < MIN_SUN_LAT_DEG ? (lat < 0 ? -MIN_SUN_LAT_DEG : MIN_SUN_LAT_DEG) : lat) * RAD
  const line: LonLat[] = []
  const n = Math.round(360 / stepDeg)
  for (let i = 0; i <= n; i++) {
    const lon = -180 + (360 * i) / n
    line.push([lon, Math.atan(-Math.cos((lon - subsolar[0]) * RAD) / Math.tan(dec)) / RAD])
  }
  return line
}

/** The night side as one polygon ring: the terminator closed through the pole in darkness. */
export function nightPolygon(subsolar: LonLat, stepDeg = 1): LonLat[] {
  const darkPole = subsolar[1] >= 0 ? -90 : 90
  const line = terminatorLine(subsolar, stepDeg)
  return [...line, [180, darkPole], [-180, darkPole], line[0]!]
}

export interface SunSample {
  subsolar_lat_deg: number
  subsolar_lon_deg: number
  lat_rate_deg_s: number
  lon_rate_deg_s: number
}

/** The server's subsolar point advanced by its server-given rates to `simMs` (from the snapshot's
 *  `sampleSimMs`): tweening between snapshots, like markers along a ground track. */
export function subsolarAt(sun: SunSample, sampleSimMs: number, simMs: number): LonLat {
  const dt = (simMs - sampleSimMs) / 1000
  const lat = Math.max(-90, Math.min(90, sun.subsolar_lat_deg + sun.lat_rate_deg_s * dt))
  return [wrapLon(sun.subsolar_lon_deg + sun.lon_rate_deg_s * dt), lat]
}

/** Unit vector from the Earth's centre towards a point given by longitude/latitude (spherical). */
export function unitVector([lon, lat]: LonLat): [x: number, y: number, z: number] {
  const cosLat = Math.cos(lat * RAD)
  return [cosLat * Math.cos(lon * RAD), cosLat * Math.sin(lon * RAD), Math.sin(lat * RAD)]
}

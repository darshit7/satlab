// Presentation-only sky geometry. Nothing here derives positions: azimuth/elevation come from the
// backend (snapshots and server-sampled pass arcs); these functions only project, tween and
// hit-test them for the polar plot and the all-sky view.

export type AzEl = [az_deg: number, el_deg: number]
export type XY = [x: number, y: number]

export interface SkyProjection {
  cx: number
  cy: number
  /** Radius of the horizon circle, px. */
  radius: number
  /**
   * East on the left is the observer's view looking up (all-sky). East on the right is the
   * operations polar plot, read like a map from above.
   */
  eastLeft: boolean
  /**
   * equidistant: radius linear in zenith angle (the standard az/el polar plot).
   * equisolid: the fisheye lens of an all-sky camera.
   */
  kind: 'equidistant' | 'equisolid'
}

const DEG = Math.PI / 180
const SIN_45 = Math.SQRT1_2 // sin(45°)

/** Fraction of the horizon radius at which an elevation is drawn (0 = zenith, 1 = horizon). */
export function radialFraction(el_deg: number, kind: SkyProjection['kind']): number {
  const zenith = Math.min(90, Math.max(0, 90 - el_deg)) // below the horizon: on the rim
  return kind === 'equidistant' ? zenith / 90 : Math.sin((zenith * DEG) / 2) / SIN_45
}

export function project(az_deg: number, el_deg: number, p: SkyProjection): XY {
  const r = radialFraction(el_deg, p.kind) * p.radius
  const a = az_deg * DEG
  return [p.cx + r * Math.sin(a) * (p.eastLeft ? -1 : 1), p.cy - r * Math.cos(a)]
}

export interface TimedArc {
  startMs: number
  endMs: number
  points: AzEl[] // evenly spaced in time from startMs to endMs
}

/** Position at `timeMs`, interpolated in projected x/y (well-behaved through the zenith and across
 *  north, where azimuth wraps), or null outside the arc's time span. */
export function positionOnArc(arc: TimedArc, timeMs: number, p: SkyProjection): XY | null {
  const n = arc.points.length
  if (n < 2 || timeMs < arc.startMs || timeMs > arc.endMs || arc.endMs <= arc.startMs) return null
  const f = ((timeMs - arc.startMs) / (arc.endMs - arc.startMs)) * (n - 1)
  const i = Math.min(Math.floor(f), n - 2)
  const a = project(...arc.points[i]!, p)
  const b = project(...arc.points[i + 1]!, p)
  const t = f - i
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
}

/** The arc between two instants (clamped to the arc), as a projected polyline with interpolated
 *  ends. Used for "remaining pass" (now → end) and motion trails (now − Δ → now). */
export function arcSegment(arc: TimedArc, fromMs: number, toMs: number, p: SkyProjection): XY[] {
  const from = Math.max(fromMs, arc.startMs)
  const to = Math.min(toMs, arc.endMs)
  const n = arc.points.length
  if (n < 2 || to <= from) return []
  const step = (arc.endMs - arc.startMs) / (n - 1)
  const out: XY[] = []
  const first = positionOnArc(arc, from, p)
  if (first) out.push(first)
  for (let i = Math.ceil((from - arc.startMs) / step); i < n; i++) {
    const t = arc.startMs + i * step
    if (t <= from) continue
    if (t >= to) break
    out.push(project(...arc.points[i]!, p))
  }
  const last = positionOnArc(arc, to, p)
  if (last) out.push(last)
  return out
}

export interface Hit {
  id: number
  x: number
  y: number
}

/** The nearest point within `maxDistance` px, or null. */
export function nearest(points: readonly Hit[], x: number, y: number, maxDistance: number): number | null {
  let best: number | null = null
  let bestD2 = maxDistance * maxDistance
  for (const pt of points) {
    const d2 = (pt.x - x) ** 2 + (pt.y - y) ** 2
    if (d2 <= bestD2) {
      bestD2 = d2
      best = pt.id
    }
  }
  return best
}

/** Dot radius encoding proximity on the all-sky view: overhead LEO is large, GEO is a speck. */
export function proximityRadius(range_m: number, minPx = 1.75, maxPx = 6): number {
  const near = Math.log10(400_000)
  const far = Math.log10(40_000_000)
  const f = (Math.log10(Math.max(range_m, 1)) - near) / (far - near)
  return maxPx - Math.min(1, Math.max(0, f)) * (maxPx - minPx)
}

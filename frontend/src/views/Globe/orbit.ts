// An orbit split for drawing: the in-view part (bright, coloured by the server's link state for
// the selection) and the rest (faded). Presentation only: in-view flags, the exact mask crossings
// and the per-point link state all come from the server's ground track.

import { lerpLonLat, type LonLat } from '../../lib/geo'
import type { GroundTrackMessage, Rag, TrackCrossing } from '../../types/ws'

export type OrbitState = 'out' | 'in' | Rag
export type OrbitPoint = [lon: number, lat: number, alt: number]

export interface OrbitRun {
  state: OrbitState
  points: OrbitPoint[]
}

/** Where a run starts or ends: a sample, a crossing between two samples, or a point between them. */
interface Joint {
  /** In sample steps from the first point (fractional between samples). */
  at: number
  point: OrbitPoint
}

/**
 * Runs of equal state along the track. In/out boundaries sit on the server's exact crossings when
 * it sent them (else on the last sample before the change, as before); a change of link state
 * holds halfway between samples, as on the sky views' arcs. `byLink` colours in-view runs by
 * `track.rag` (when aligned); otherwise they are just `in`. Without in-view flags the whole orbit
 * is one faded run.
 */
export function orbitRuns(track: GroundTrackMessage, fallbackAlt: number, byLink: boolean): OrbitRun[] {
  const n = track.points.length
  const alt = (i: number) => track.alt_m?.[i] ?? fallbackAlt
  const sample = (i: number): OrbitPoint => [track.points[i]![0], track.points[i]![1], alt(i)]
  if (n < 2) return []
  const above = track.above_mask
  if (!above || above.length !== n) return [{ state: 'out', points: track.points.map((_, i) => sample(i)) }]

  const rag = byLink && track.rag && track.rag.length === n ? track.rag : null
  const state = (i: number): OrbitState => (above[i] ? (rag ? rag[i]! : 'in') : 'out')
  const startMs = Date.parse(track.start_time)
  const stepMs = (Date.parse(track.end_time) - startMs) / (n - 1)
  const crossings = (track.crossings ?? []).map((c) => ({ c, at: (Date.parse(c.time) - startMs) / stepMs }))
  const between = (i: number, rising: boolean): TrackCrossing | undefined =>
    crossings.find(({ c, at }) => at > i - 1 && at < i && (c.kind === 'aos') === rising)?.c

  const joints: Joint[] = [{ at: 0, point: sample(0) }]
  const states: OrbitState[] = [state(0)]
  for (let i = 1; i < n; i++) {
    if (state(i) === state(i - 1)) continue
    let joint: Joint
    if (above[i] !== above[i - 1]) {
      const c = between(i, above[i]!)
      joint = c
        ? { at: (Date.parse(c.time) - startMs) / stepMs, point: [c.lon_deg, c.lat_deg, c.alt_m] }
        : { at: i - 1, point: sample(i - 1) }
    } else {
      const [lon, lat] = lerpLonLat(track.points[i - 1]! as LonLat, track.points[i]! as LonLat, 0.5)
      joint = { at: i - 0.5, point: [lon, lat, (alt(i - 1) + alt(i)) / 2] }
    }
    joints.push(joint)
    states.push(state(i))
  }
  joints.push({ at: n - 1, point: sample(n - 1) })

  const runs: OrbitRun[] = []
  for (let k = 0; k < states.length; k++) {
    const from = joints[k]!
    const to = joints[k + 1]!
    const points = [from.point]
    for (let i = Math.floor(from.at) + 1; i < to.at; i++) points.push(sample(i))
    if (to.at > from.at) points.push(to.point)
    if (points.length >= 2) runs.push({ state: states[k]!, points })
  }
  return runs
}

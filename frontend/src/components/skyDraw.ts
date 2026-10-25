// Canvas drawing for the sky views (polar plot, all-sky). Presentation only: every position comes
// from the backend's snapshots and server-sampled pass arcs.

import { positionOnArc, project, ringRadius, type Hit, type SkyProjection, type TimedArc, type XY } from '../lib/sky'
import type { ResolvedTheme } from '../lib/theme'
import { token } from '../lib/tokens'
import type { PassArcMessage, Rag, SatelliteState, Snapshot } from '../types/ws'

const CARDINALS: [string, number][] = [
  ['N', 0],
  ['E', 90],
  ['S', 180],
  ['W', 270],
]

/** Sky colors per theme, from the same tokens as the UI and the map.
 *
 *  In the sky views a dot's fill is its link state (the server's RAG; grey = no RF data), so the
 *  emerald there always means "green link". The selection is marked by shape: an ink ring, a
 *  larger dot and an ink label. */
export function palette(theme: ResolvedTheme) {
  const light = theme === 'light'
  return {
    // the console's raised surface: gray-50 on light, white/5 on dark
    disc: token(light ? 'gray-50' : 'white'),
    discAlpha: light ? 1 : 0.05,
    grid: token(light ? 'gray-200' : 'gray-700'),
    mask: token(light ? 'gray-400' : 'gray-500'),
    label: token(light ? 'gray-500' : 'gray-400'),
    sat: token(light ? 'gray-500' : 'gray-400'),
    tracked: token(light ? 'gray-800' : 'gray-200'),
    faint: token(light ? 'gray-300' : 'gray-600'),
    accent: token(light ? 'primary-600' : 'primary-400'),
    ground: token(light ? 'white' : 'dark'),
    /** The selection's ring, label and (without RF data) its pass. */
    ink: token(light ? 'gray-900' : 'gray-100'),
    rag: {
      green: token(light ? 'primary-600' : 'primary-400'),
      amber: token(light ? 'amber-700' : 'amber-400'),
      red: token(light ? 'red-700' : 'red-400'),
    } satisfies Record<Rag, string>,
  }
}
export type SkyPalette = ReturnType<typeof palette>

export interface SkyFrame {
  ctx: CanvasRenderingContext2D
  p: SkyProjection
  c: SkyPalette
  simMs: number
  mask_deg: number
  snapshot: Snapshot
  selectedId: number | null
  trackedIds: Set<number>
  /** The server-sampled arc of a satellite, ready to tween along. */
  arcOf: (norad_id: number) => SkyArc | undefined
  /** Renderers push every dot they draw, so clicks can select it. */
  hits: Hit[]
  font: string
  /** A small view: no name tags (they'd clip at its edge; the inspector names the selection). */
  compact: boolean
}

/** An arc plus whether its ends are the pass's real AOS / LOS (not the window's clip). */
export interface SkyArc extends TimedArc {
  hasAos: boolean
  hasLos: boolean
  /** Server link state at each point; null without a link profile. */
  rag: Rag[] | null
}

const timedArcs = new WeakMap<PassArcMessage, SkyArc>()
export function timed(arc: PassArcMessage): SkyArc {
  let t = timedArcs.get(arc)
  if (!t) {
    t = {
      startMs: Date.parse(arc.start_time),
      endMs: Date.parse(arc.end_time),
      points: arc.points,
      hasAos: arc.aos_time !== null,
      hasLos: arc.los_time !== null,
      rag: arc.rag && arc.rag.length === arc.points.length ? arc.rag : null,
    }
    timedArcs.set(arc, t)
  }
  return t
}

/** Above the horizon (pinned satellites can be below it; they aren't drawn in the sky). */
export const inSky = (sat: SatelliteState) => sat.elevation_deg >= 0

export function drawGrid(
  ctx: CanvasRenderingContext2D,
  p: SkyProjection,
  c: SkyPalette,
  mask_deg: number,
  ringLabels: boolean,
  font: string,
) {
  const ring = (el: number) => Math.hypot(...sub(project(0, el, p), [p.cx, p.cy]))
  ctx.save()
  ctx.globalAlpha = c.discAlpha
  ctx.fillStyle = c.disc
  ctx.beginPath()
  ctx.arc(p.cx, p.cy, p.radius, 0, 2 * Math.PI)
  ctx.fill()
  ctx.globalAlpha = 1

  ctx.strokeStyle = c.grid
  ctx.lineWidth = 1
  for (const el of [0, 30, 60]) {
    ctx.beginPath()
    ctx.arc(p.cx, p.cy, ring(el), 0, 2 * Math.PI)
    ctx.stroke()
  }
  for (let az = 0; az < 360; az += 30) {
    const [x, y] = project(az, 0, p)
    ctx.beginPath()
    ctx.moveTo(p.cx, p.cy)
    ctx.lineTo(x, y)
    ctx.stroke()
  }
  if (mask_deg > 0) {
    ctx.strokeStyle = c.mask
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.arc(p.cx, p.cy, ring(mask_deg), 0, 2 * Math.PI)
    ctx.stroke()
    ctx.setLineDash([])
  }

  ctx.fillStyle = c.label
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `600 12px ${font}`
  for (const [name, az] of CARDINALS) {
    const [x, y] = project(az, 0, { ...p, radius: p.radius + 10 })
    ctx.fillText(name, x, y)
  }
  if (ringLabels) {
    ctx.font = `11px ${font}`
    ctx.textAlign = 'left'
    for (const el of [30, 60]) ctx.fillText(`${el}°`, p.cx + 3, p.cy - ring(el) + 8)
  }
  ctx.restore()
}

const sub = (a: [number, number], b: [number, number]): [number, number] => [a[0] - b[0], a[1] - b[1]]

/** Stroke a projected polyline. */
export function strokePath(ctx: CanvasRenderingContext2D, points: XY[]) {
  if (points.length < 2) return
  ctx.beginPath()
  ctx.moveTo(...points[0]!)
  for (const pt of points.slice(1)) ctx.lineTo(...pt)
  ctx.stroke()
}

/** A dot. */
export function dot(ctx: CanvasRenderingContext2D, [x, y]: XY, radius: number, fill: string) {
  ctx.fillStyle = fill
  ctx.beginPath()
  ctx.arc(x, y, radius, 0, 2 * Math.PI)
  ctx.fill()
}

/** The selection: a ring in ink around its (link-coloured) dot, on a ground-coloured gap. */
export function selectionRing(ctx: CanvasRenderingContext2D, [x, y]: XY, radius: number, c: SkyPalette) {
  ctx.lineWidth = 3
  ctx.strokeStyle = c.ground
  ctx.beginPath()
  ctx.arc(x, y, radius + 3, 0, 2 * Math.PI)
  ctx.stroke()
  ctx.lineWidth = 1.5
  ctx.strokeStyle = c.ink
  ctx.beginPath()
  ctx.arc(x, y, radius + 4, 0, 2 * Math.PI)
  ctx.stroke()
}

/** Fill of a satellite's dot: its link state, or grey without RF data. */
export function linkColor(c: SkyPalette, sat: SatelliteState, fallback: string = c.sat): string {
  return sat.link ? c.rag[sat.link.rag] : fallback
}

/** The inner "strong-link" region: the elevation bands where the selection's margin is green. */
export function strongLinkRegion(f: SkyFrame, bands: [number, number][]) {
  const { ctx, p, c } = f
  ctx.save()
  for (const [lo, hi] of bands) {
    const outer = ringRadius(lo, p)
    const inner = ringRadius(hi, p)
    ctx.globalAlpha = 0.1
    ctx.fillStyle = c.rag.green
    ctx.beginPath()
    ctx.arc(p.cx, p.cy, outer, 0, 2 * Math.PI)
    if (inner > 0.5) ctx.arc(p.cx, p.cy, inner, 0, 2 * Math.PI, true)
    ctx.fill()
    ctx.globalAlpha = 0.6
    ctx.strokeStyle = c.rag.green
    ctx.lineWidth = 1
    ctx.setLineDash([2, 3])
    for (const r of [outer, inner]) {
      if (r <= 0.5) continue
      ctx.beginPath()
      ctx.arc(p.cx, p.cy, r, 0, 2 * Math.PI)
      ctx.stroke()
    }
    ctx.setLineDash([])
  }
  ctx.restore()
}

/** A name next to a dot, haloed in the ground color so it reads over grid lines and other dots. */
export function nameTag(f: SkyFrame, [x, y]: XY, text: string, color: string) {
  const { ctx, c, p } = f
  ctx.font = `600 12px ${f.font}`
  ctx.textBaseline = 'middle'
  const right = x < p.cx + p.radius * 0.4
  ctx.textAlign = right ? 'left' : 'right'
  const tx = x + (right ? 10 : -10)
  ctx.lineWidth = 3
  ctx.strokeStyle = c.ground
  ctx.strokeText(text, tx, y)
  ctx.fillStyle = color
  ctx.fillText(text, tx, y)
}

/** Where a satellite is drawn: tweened along its arc, else its last snapshot position. */
export function skyPosition(f: SkyFrame, sat: SatelliteState): XY {
  const arc = f.arcOf(sat.norad_id)
  return (arc && positionOnArc(arc, f.simMs, f.p)) ?? project(sat.azimuth_deg, sat.elevation_deg, f.p)
}

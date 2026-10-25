import type { SkyViewSpec } from '../../components/SkyCanvas'
import {
  dot,
  inSky,
  linkColor,
  nameTag,
  selectionRing,
  skyPosition,
  strokePath,
  strongLinkRegion,
  type SkyFrame,
} from '../../components/skyDraw'
import { arcRuns, arcSegment, project } from '../../lib/sky'

/** With this few satellites shown, every remaining-pass arc is drawn (faintly). */
const ALL_ARCS_MAX = 50

/**
 * The operations polar plot: north up, east right, elevation linear from the zenith (centre) to
 * the horizon (rim), dashed ring at the horizon mask. Dots tween along server-computed pass arcs
 * and are coloured by their link state (grey without RF data). The selection is ringed; its
 * remaining pass is coloured by the link state along it, over its strong-link region.
 */
function drawPolar(f: SkyFrame) {
  const { ctx, p, c, simMs, selectedId, trackedIds, hits } = f
  const sats = f.snapshot.satellites.filter(inSky)
  const selectedSat = selectedId !== null ? f.snapshot.satellites.find((s) => s.norad_id === selectedId) : undefined
  const remaining = (id: number) => {
    const arc = f.arcOf(id)
    return arc ? arcSegment(arc, simMs, arc.endMs, p) : []
  }
  if (selectedSat?.link?.strong_link_elev_bands_deg.length) {
    strongLinkRegion(f, selectedSat.link.strong_link_elev_bands_deg)
  }
  ctx.save()
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'

  if (sats.length <= ALL_ARCS_MAX) {
    ctx.strokeStyle = c.faint
    ctx.lineWidth = 1
    for (const sat of sats) {
      if (sat.norad_id !== selectedId && !trackedIds.has(sat.norad_id)) strokePath(ctx, remaining(sat.norad_id))
    }
  }
  ctx.strokeStyle = c.tracked
  ctx.lineWidth = 1.25
  for (const id of trackedIds) if (id !== selectedId) strokePath(ctx, remaining(id))

  // The selection: its whole pass, past part dashed, remaining part solid (coloured by the link
  // state along it, ink without RF data), AOS/LOS ticks.
  const arc = selectedId !== null ? f.arcOf(selectedId) : undefined
  if (arc) {
    ctx.strokeStyle = c.ink
    ctx.globalAlpha = 0.6
    ctx.lineWidth = 1.25
    ctx.setLineDash([3, 4])
    strokePath(ctx, arcSegment(arc, arc.startMs, Math.min(simMs, arc.endMs), p))
    ctx.globalAlpha = 1
    ctx.setLineDash(simMs < arc.startMs ? [6, 4] : []) // a pass still to come is dashed throughout
    ctx.lineWidth = 2.25
    if (arc.rag) {
      for (const run of arcRuns(arc, arc.rag, simMs, arc.endMs, p)) {
        ctx.strokeStyle = c.rag[run.state]
        strokePath(ctx, run.points)
      }
    } else {
      ctx.strokeStyle = c.ink
      strokePath(ctx, arcSegment(arc, simMs, arc.endMs, p))
    }
    ctx.setLineDash([])
    const ends: [boolean, [number, number]][] = [
      [arc.hasAos, arc.points[0]!],
      [arc.hasLos, arc.points.at(-1)!],
    ]
    for (const [real, [az, el]] of ends) {
      if (!real) continue
      ctx.strokeStyle = c.ink
      ctx.fillStyle = c.ground
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(...project(az, el, p), 3.5, 0, 2 * Math.PI)
      ctx.fill()
      ctx.stroke()
    }
  }

  let selectedAt: [number, number] | null = null
  for (const sat of sats) {
    const at = skyPosition(f, sat)
    hits.push({ id: sat.norad_id, x: at[0], y: at[1] })
    if (sat.norad_id === selectedId) {
      selectedAt = at
      continue
    }
    const tracked = trackedIds.has(sat.norad_id)
    ctx.globalAlpha = sat.above_mask ? 1 : 0.45
    dot(ctx, at, tracked || sat.link ? 3.5 : 2.5, linkColor(c, sat, tracked ? c.tracked : c.sat))
    ctx.globalAlpha = 1
    if (tracked && !f.compact) nameTag(f, at, sat.name, c.tracked)
  }
  if (selectedAt && selectedSat) {
    dot(ctx, selectedAt, 4.5, linkColor(c, selectedSat, c.ink))
    selectionRing(ctx, selectedAt, 4.5, c)
    if (!f.compact) nameTag(f, selectedAt, selectedSat.name, c.ink)
  }
  ctx.restore()
}

/** The polar plot, as the sky dock and the sky dialog show it. */
export const POLAR_VIEW: SkyViewSpec = {
  title: 'Sky plot',
  caption: 'az / el · E right',
  label: 'Polar sky plot',
  kind: 'equidistant',
  eastLeft: false,
  ringLabels: true,
  draw: drawPolar,
}

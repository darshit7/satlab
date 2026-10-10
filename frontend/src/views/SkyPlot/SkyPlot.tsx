import { SkyCanvas } from '../../components/SkyCanvas'
import { dot, inSky, nameTag, skyPosition, strokePath, type SkyFrame } from '../../components/skyDraw'
import { Card } from '../../components/ui/Card'
import { arcSegment, project } from '../../lib/sky'

/** With this few satellites shown, every remaining-pass arc is drawn (faintly). */
const ALL_ARCS_MAX = 50

/**
 * The operations polar plot: north up, east right, elevation linear from the zenith (centre) to
 * the horizon (rim), dashed ring at the horizon mask. Dots tween along server-computed pass arcs;
 * the selected satellite's remaining pass is drawn in the accent, tracked ones in grey.
 */
function drawPolar(f: SkyFrame) {
  const { ctx, p, c, simMs, selectedId, trackedIds, hits } = f
  const sats = f.snapshot.satellites.filter(inSky)
  const remaining = (id: number) => {
    const arc = f.arcOf(id)
    return arc ? arcSegment(arc, simMs, arc.endMs, p) : []
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

  // The selection: its whole pass, past part dashed, remaining part solid, AOS/LOS ticks.
  const arc = selectedId !== null ? f.arcOf(selectedId) : undefined
  if (arc) {
    ctx.strokeStyle = c.accent
    ctx.lineWidth = 1.25
    ctx.setLineDash([3, 4])
    strokePath(ctx, arcSegment(arc, arc.startMs, Math.min(simMs, arc.endMs), p))
    ctx.setLineDash(simMs < arc.startMs ? [6, 4] : []) // a pass still to come is dashed throughout
    ctx.lineWidth = 2.25
    strokePath(ctx, arcSegment(arc, simMs, arc.endMs, p))
    ctx.setLineDash([])
    const ends: [boolean, [number, number]][] = [
      [arc.hasAos, arc.points[0]!],
      [arc.hasLos, arc.points.at(-1)!],
    ]
    for (const [real, [az, el]] of ends) {
      if (!real) continue
      ctx.strokeStyle = c.accent
      ctx.fillStyle = c.ground
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(...project(az, el, p), 3.5, 0, 2 * Math.PI)
      ctx.fill()
      ctx.stroke()
    }
  }

  let selectedAt: [number, number] | null = null
  let selectedName = ''
  for (const sat of sats) {
    const at = skyPosition(f, sat)
    hits.push({ id: sat.norad_id, x: at[0], y: at[1] })
    if (sat.norad_id === selectedId) {
      selectedAt = at
      selectedName = sat.name
      continue
    }
    const tracked = trackedIds.has(sat.norad_id)
    ctx.globalAlpha = sat.above_mask ? 1 : 0.45
    dot(ctx, at, tracked ? 3.5 : 2.5, tracked ? c.tracked : c.sat)
    ctx.globalAlpha = 1
    if (tracked) nameTag(f, at, sat.name, c.tracked)
  }
  if (selectedAt) {
    dot(ctx, selectedAt, 4.5, c.accent, c.accent)
    nameTag(f, selectedAt, selectedName, c.accent)
  }
  ctx.restore()
}

export function SkyPlot() {
  return (
    <Card as="div" title="Sky plot" aside={<span className="text-sm text-gray-600 dark:text-gray-400">az / el · E right</span>}>
      <SkyCanvas label="Polar sky plot" kind="equidistant" eastLeft={false} ringLabels draw={drawPolar} />
    </Card>
  )
}

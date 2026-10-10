import { SkyCanvas } from '../../components/SkyCanvas'
import { dot, inSky, nameTag, skyPosition, strokePath, type SkyFrame } from '../../components/skyDraw'
import { Card } from '../../components/ui/Card'
import { arcSegment, proximityRadius } from '../../lib/sky'

/** Length of the motion trail behind each satellite, in sim time. */
const TRAIL_MS = 60_000
const TRAIL_STEPS = 3

/**
 * The observer's view, looking straight up through a fisheye lens: zenith at the centre, horizon
 * at the rim and therefore east on the LEFT. Dot size encodes proximity (overhead = closest =
 * largest), a fading trail shows direction and speed. Positions are geometric, not naked-eye.
 */
function drawAllSky(f: SkyFrame) {
  const { ctx, p, c, simMs, selectedId, trackedIds, hits } = f
  const sats = f.snapshot.satellites.filter(inSky)
  ctx.save()
  ctx.lineCap = 'round'

  // Trails: older slices fainter.
  for (const sat of sats) {
    const arc = f.arcOf(sat.norad_id)
    if (!arc) continue
    const selected = sat.norad_id === selectedId
    const emphasis = selected || trackedIds.has(sat.norad_id)
    ctx.strokeStyle = selected ? c.accent : emphasis ? c.tracked : c.sat
    ctx.lineWidth = selected ? 2 : 1
    for (let i = 0; i < TRAIL_STEPS; i++) {
      const from = simMs - TRAIL_MS * (1 - i / TRAIL_STEPS)
      const to = simMs - TRAIL_MS * (1 - (i + 1) / TRAIL_STEPS)
      ctx.globalAlpha = ((emphasis ? 0.9 : 0.4) * (i + 1)) / TRAIL_STEPS
      strokePath(ctx, arcSegment(arc, from, to, p))
    }
  }
  ctx.globalAlpha = 1

  // Where the selection goes next.
  const arc = selectedId !== null ? f.arcOf(selectedId) : undefined
  if (arc) {
    ctx.strokeStyle = c.accent
    ctx.lineWidth = 1.25
    ctx.setLineDash([3, 5])
    strokePath(ctx, arcSegment(arc, simMs, arc.endMs, p))
    ctx.setLineDash([])
  }

  // Far satellites first, so near (large) ones sit on top.
  const ordered = [...sats].sort((a, b) => b.range_m - a.range_m)
  let selected: { at: [number, number]; r: number; name: string } | null = null
  for (const sat of ordered) {
    const at = skyPosition(f, sat)
    const r = proximityRadius(sat.range_m, 1.25, 4.5)
    hits.push({ id: sat.norad_id, x: at[0], y: at[1] })
    if (sat.norad_id === selectedId) {
      selected = { at, r, name: sat.name }
      continue
    }
    const tracked = trackedIds.has(sat.norad_id)
    ctx.globalAlpha = sat.above_mask ? 1 : 0.45
    dot(ctx, at, r, tracked ? c.tracked : c.sat)
    ctx.globalAlpha = 1
    if (tracked) nameTag(f, at, sat.name, c.tracked)
  }
  if (selected) {
    dot(ctx, selected.at, selected.r + 1, c.accent, c.accent)
    nameTag(f, selected.at, selected.name, c.accent)
  }
  ctx.restore()
}

export function AllSky() {
  return (
    <Card as="div" title="All-sky" aside={<span className="text-sm text-gray-600 dark:text-gray-400">looking up · E left</span>}>
      <SkyCanvas label="All-sky view" kind="equisolid" eastLeft ringLabels={false} draw={drawAllSky} />
    </Card>
  )
}

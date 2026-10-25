import { useEffect, useRef } from 'react'
import { nearest, type Hit, type SkyProjection } from '../lib/sky'
import { currentResolvedTheme, onThemeChange } from '../lib/theme'
import { estimateSimMs } from '../lib/time'
import { useMission } from '../store/mission'
import { drawGrid, palette, timed, type SkyFrame } from './skyDraw'

const FRAME_INTERVAL_MS = 50
const HIT_RADIUS_PX = 10

interface Props {
  /** Accessible name prefix, e.g. "Polar sky plot". */
  label: string
  kind: SkyProjection['kind']
  eastLeft: boolean
  /** Label the 30° / 60° elevation rings (never in a compact view). */
  ringLabels: boolean
  /** A small view: no ring labels and no name tags. */
  compact?: boolean
  /** Draws the view's satellites; keep it stable (module-level) — it restarts the frame loop. */
  draw: (frame: SkyFrame) => void
}

/** One sky view: its canvas settings plus the words around it. */
export interface SkyViewSpec extends Omit<Props, 'compact'> {
  title: string
  /** Orientation, e.g. "az / el · E right". */
  caption: string
}

/** A square canvas showing the sky over the station: grid, horizon mask and the view's satellites. */
export function SkyCanvas({ label, kind, eastLeft, ringLabels, compact = false, draw }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const visible = useMission((s) => s.snapshot?.satellites.filter((sat) => sat.above_mask).length ?? null)
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg ?? null)

  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!container || !canvas || !ctx) return
    let c = palette(currentResolvedTheme())
    let font = getComputedStyle(document.body).fontFamily
    let size = 0
    let frame = 0
    let lastFrameAt = 0
    let hits: Hit[] = []

    const resize = () => {
      size = Math.floor(container.clientWidth)
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.round(size * dpr)
      canvas.height = Math.round(size * dpr)
      canvas.style.width = `${size}px`
      canvas.style.height = `${size}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    resize()

    const render = (now: number) => {
      frame = requestAnimationFrame(render)
      if (now - lastFrameAt < FRAME_INTERVAL_MS || size === 0) return
      lastFrameAt = now
      const { snapshot, clockSample, arcs, selectedId, tracked } = useMission.getState()
      ctx.clearRect(0, 0, size, size)
      const margin = compact ? 14 : 18
      const p: SkyProjection = { cx: size / 2, cy: size / 2, radius: size / 2 - margin, eastLeft, kind }
      const mask_deg = snapshot?.observer.horizon_mask_deg ?? 0
      drawGrid(ctx, p, c, mask_deg, ringLabels && !compact, font)
      hits = []
      if (!snapshot || !clockSample) return
      draw({
        ctx,
        p,
        c,
        simMs: estimateSimMs(clockSample, now),
        mask_deg,
        snapshot,
        selectedId,
        trackedIds: new Set(tracked.map((t) => t.norad_id)),
        arcOf: (id) => {
          const arc = arcs[id]
          return arc ? timed(arc) : undefined
        },
        hits,
        font,
        compact,
      })
    }
    frame = requestAnimationFrame(render)

    const onClick = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const id = nearest(hits, e.clientX - rect.left, e.clientY - rect.top, HIT_RADIUS_PX)
      if (id !== null) useMission.getState().select(id)
    }
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const id = nearest(hits, e.clientX - rect.left, e.clientY - rect.top, HIT_RADIUS_PX)
      canvas.style.cursor = id === null ? '' : 'pointer'
    }
    canvas.addEventListener('click', onClick)
    canvas.addEventListener('mousemove', onMove)
    const offTheme = onThemeChange(() => {
      c = palette(currentResolvedTheme())
      font = getComputedStyle(document.body).fontFamily
    })

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      canvas.removeEventListener('click', onClick)
      canvas.removeEventListener('mousemove', onMove)
      offTheme()
    }
  }, [draw, eastLeft, kind, ringLabels, compact])

  const description =
    visible === null
      ? `${label}: no session yet`
      : `${label}: ${visible} satellite${visible === 1 ? '' : 's'} shown above the ${mask ?? 0}° horizon mask`

  return (
    <div ref={containerRef} className="relative aspect-square w-full">
      <canvas ref={canvasRef} role="img" aria-label={description} className="absolute inset-0" />
    </div>
  )
}

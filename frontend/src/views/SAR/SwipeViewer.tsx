import { useEffect, useRef, useState } from 'react'
import { focusRing } from '../../components/ui/buttonClasses'
import { fitBox, fitView, imageToCanvas, splitFromKey, splitFromPointer, zoomAt, type View } from '../../lib/sar'
import { currentResolvedTheme, onThemeChange } from '../../lib/theme'
import { token } from '../../lib/tokens'
import type { SarRegion } from '../../types/api'
import type { ComparisonImages } from './images'

export type ViewMode = 'swipe' | 'before' | 'after'

interface Props {
  images: ComparisonImages
  mode: ViewMode
  heatmap: boolean
  /** Heatmap opacity, 0–1. */
  opacity: number
  regions: SarRegion[]
  selected: number | null
  onSelect: (id: number | null) => void
  /** Accessible summary, e.g. the dates and the region count. */
  label: string
}

/**
 * The before/after radar images on one canvas: before left of the split and after right of it
 * (or one of them), the change heatmap over both, flagged regions outlined and numbered. Drag the
 * divider (or use the arrow keys on it) to swipe; wheel to zoom; drag to pan when zoomed.
 */
export function SwipeViewer({ images, mode, heatmap, opacity, regions, selected, onSelect, label }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [split, setSplit] = useState(50)
  const state = useRef({ view: null as View | null, minScale: 1, width: 0, height: 0, drag: null as null | 'split' | { x: number; y: number; view: View } })
  const props = useRef({ images, mode, heatmap, opacity, regions, selected, split })
  const [, redraw] = useState(0)

  // draw on every change
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !container || !ctx) return
    let frame = 0
    const st = state.current

    const draw = () => {
      frame = 0
      const { images: img, mode: m, heatmap: hm, opacity: op, regions: rs, selected: sel, split: sp } = props.current
      const { width, height } = st
      if (!st.view || width === 0) return
      const v = st.view
      const ink = token(currentResolvedTheme() === 'dark' ? 'gray-100' : 'gray-900')
      ctx.clearRect(0, 0, width, height)
      ctx.imageSmoothingEnabled = v.scale < 1
      const drawImage = (bitmap: ImageBitmap) => ctx.drawImage(bitmap, -v.x * v.scale, -v.y * v.scale, bitmap.width * v.scale, bitmap.height * v.scale)
      const cut = m === 'before' ? width : m === 'after' ? 0 : (sp / 100) * width
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, cut, height)
      ctx.clip()
      drawImage(img.before)
      ctx.restore()
      ctx.save()
      ctx.beginPath()
      ctx.rect(cut, 0, width - cut, height)
      ctx.clip()
      drawImage(img.after)
      ctx.restore()
      if (hm) {
        ctx.globalAlpha = op
        drawImage(img.change)
        ctx.globalAlpha = 1
      }
      const increase = token('fuchsia-400')
      const decrease = token('cyan-400')
      ctx.font = '600 11px system-ui, sans-serif'
      for (const r of rs) {
        const [c0, r0, c1, r1] = r.px
        const [x0, y0] = imageToCanvas(v, c0, r0)
        const [x1, y1] = imageToCanvas(v, c1, r1)
        const isSel = r.id === sel
        ctx.lineWidth = isSel ? 3 : 1.5
        ctx.setLineDash(r.sign === 'decrease' ? [4, 3] : [])
        ctx.strokeStyle = 'rgba(17, 24, 39, 0.6)'
        ctx.strokeRect(x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2)
        ctx.strokeStyle = isSel ? token('gray-50') : r.sign === 'increase' ? increase : decrease
        ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
        ctx.setLineDash([])
        if (x1 - x0 > 10 || isSel) {
          ctx.fillStyle = 'rgba(17, 24, 39, 0.7)'
          ctx.fillRect(x0, y0 - 14, 18, 14)
          ctx.fillStyle = token('gray-50')
          ctx.fillText(String(r.id), x0 + 3, y0 - 3)
        }
      }
      if (m === 'swipe') {
        ctx.fillStyle = ink
        ctx.fillRect(cut - 1, 0, 2, height)
      }
    }
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(draw)
    }

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      const width = Math.floor(container.clientWidth)
      const height = Math.floor(container.clientHeight)
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const firstFit = st.width === 0
      st.width = width
      st.height = height
      const img = props.current.images.before
      const fit = fitView(img.width, img.height, width, height)
      st.minScale = fit.scale
      if (firstFit || !st.view) st.view = fit
      schedule()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    resize()
    const offTheme = onThemeChange(schedule)
    schedule()
    const unsubscribe = () => {
      observer.disconnect()
      offTheme()
      if (frame) cancelAnimationFrame(frame)
    }
    ;(canvas as HTMLCanvasElement & { __redraw?: () => void }).__redraw = schedule
    return unsubscribe
  }, [])

  // redraw when the inputs change
  useEffect(() => {
    props.current = { images, mode, heatmap, opacity, regions, selected, split }
    ;(canvasRef.current as (HTMLCanvasElement & { __redraw?: () => void }) | null)?.__redraw?.()
  }, [images, mode, heatmap, opacity, regions, selected, split])

  // frame the selected region
  useEffect(() => {
    const st = state.current
    const region = regions.find((r) => r.id === selected)
    if (!region || st.width === 0) return
    st.view = fitBox(region.px, st.width, st.height, st.minScale)
    redraw((n) => n + 1)
    ;(canvasRef.current as (HTMLCanvasElement & { __redraw?: () => void }) | null)?.__redraw?.()
  }, [selected, regions])

  const zoom = (factor: number) => {
    const st = state.current
    if (!st.view) return
    st.view = zoomAt(st.view, factor, st.width / 2, st.height / 2, st.minScale)
    ;(canvasRef.current as (HTMLCanvasElement & { __redraw?: () => void }) | null)?.__redraw?.()
  }
  const fit = () => {
    const st = state.current
    st.view = fitView(images.before.width, images.before.height, st.width, st.height)
    ;(canvasRef.current as (HTMLCanvasElement & { __redraw?: () => void }) | null)?.__redraw?.()
  }

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const st = state.current
    const rect = e.currentTarget.getBoundingClientRect()
    const nearDivider = mode === 'swipe' && Math.abs(e.clientX - rect.left - (split / 100) * rect.width) < 12
    if (nearDivider) st.drag = 'split'
    else if (st.view && st.view.scale > st.minScale * 1.001) st.drag = { x: e.clientX, y: e.clientY, view: st.view }
    else {
      // a click picks the region under it
      const view = st.view
      if (view) {
        const ix = view.x + (e.clientX - rect.left) / view.scale
        const iy = view.y + (e.clientY - rect.top) / view.scale
        const hit = regions.find((r) => ix >= r.px[0] && ix <= r.px[2] && iy >= r.px[1] && iy <= r.px[3])
        onSelect(hit ? hit.id : null)
      }
      return
    }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const st = state.current
    const rect = e.currentTarget.getBoundingClientRect()
    if (st.drag === 'split') setSplit(splitFromPointer(e.clientX, rect.left, rect.width))
    else if (st.drag && typeof st.drag === 'object') {
      const d = st.drag
      st.view = { ...d.view, x: d.view.x - (e.clientX - d.x) / d.view.scale, y: d.view.y - (e.clientY - d.y) / d.view.scale }
      ;(canvasRef.current as (HTMLCanvasElement & { __redraw?: () => void }) | null)?.__redraw?.()
    } else {
      const near = mode === 'swipe' && Math.abs(e.clientX - rect.left - (split / 100) * rect.width) < 12
      e.currentTarget.style.cursor = near ? 'ew-resize' : st.view && st.view.scale > st.minScale * 1.001 ? 'grab' : 'default'
    }
  }
  const onPointerUp = () => {
    state.current.drag = null
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const st = state.current
      if (!st.view) return
      const rect = canvas.getBoundingClientRect()
      st.view = zoomAt(st.view, e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - rect.left, e.clientY - rect.top, st.minScale)
      ;(canvas as HTMLCanvasElement & { __redraw?: () => void }).__redraw?.()
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  }, [])

  return (
    <div className="relative">
      <div ref={containerRef} className="relative h-[min(60vh,40rem)] min-h-64 overflow-hidden rounded-lg bg-gray-900">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={label}
          className="absolute inset-0 touch-none"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        />
        {mode === 'swipe' && (
          <div
            role="slider"
            tabIndex={0}
            aria-label="Swipe between before and after"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(split)}
            aria-valuetext={`${Math.round(split)}% before, ${Math.round(100 - split)}% after`}
            className={`absolute top-1/2 h-10 w-5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full bg-white/90 ring-1 ring-gray-900/20 ${focusRing}`}
            style={{ left: `${split}%` }}
            onKeyDown={(e) => {
              const next = splitFromKey(split, e.key)
              if (next !== null) {
                e.preventDefault()
                setSplit(next)
              }
            }}
          />
        )}
        {mode === 'swipe' && (
          <>
            <span className="pointer-events-none absolute top-2 left-2 rounded-md bg-gray-900/70 px-1.5 py-0.5 text-xs text-gray-50">Before</span>
            <span className="pointer-events-none absolute top-2 right-2 rounded-md bg-gray-900/70 px-1.5 py-0.5 text-xs text-gray-50">After</span>
          </>
        )}
      </div>
      <div className="mt-2 flex items-center gap-1" role="group" aria-label="Zoom">
        <ZoomButton label="Zoom out" onClick={() => zoom(1 / 1.5)}>
          −
        </ZoomButton>
        <ZoomButton label="Zoom in" onClick={() => zoom(1.5)}>
          +
        </ZoomButton>
        <ZoomButton label="Fit the whole area" onClick={fit}>
          Fit
        </ZoomButton>
      </div>
    </div>
  )
}

function ZoomButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`rounded-lg px-2 py-0.5 font-mono text-xs ring-1 ring-gray-900/10 hover:bg-gray-50 dark:ring-white/10 dark:hover:bg-white/5 ${focusRing}`}
    >
      {children}
    </button>
  )
}

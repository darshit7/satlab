// Canvas images for the globe's billboards, so it loads no image URL: the station's icon and the
// selection's halo.

/** lucide's "radio-tower" glyph on its 24-unit grid (lucide-react 1.55, ISC licence). */
const RADIO_TOWER = [
  'M4.9 16.1C1 12.2 1 5.8 4.9 1.9',
  'M7.8 4.7a6.14 6.14 0 0 0-.8 7.5',
  'M16.2 4.8c2 2 2.26 5.11.8 7.47',
  'M19.1 1.9a9.96 9.96 0 0 1 0 14.1',
  'M9.5 18h5',
  'm8 22 4-11 4 11',
]

function canvas(sizePx: number): [HTMLCanvasElement, CanvasRenderingContext2D, number] {
  const ratio = Math.max(1, Math.round(window.devicePixelRatio || 1))
  const el = document.createElement('canvas')
  el.width = el.height = sizePx * ratio
  const ctx = el.getContext('2d')!
  return [el, ctx, ratio]
}

/** The station: the radio-tower glyph in ink over a ground-coloured outline, `sizePx` square, its
 *  base at the bottom centre (anchor it there, on the station). */
export function stationIcon(ink: string, ground: string, sizePx = 26): HTMLCanvasElement {
  const [el, ctx, ratio] = canvas(sizePx)
  // a unit of padding around the 24-unit glyph, for the outline
  const unit = (sizePx * ratio) / 26
  ctx.setTransform(unit, 0, 0, unit, unit, unit * 0.5)
  const glyph = new Path2D()
  for (const d of RADIO_TOWER) glyph.addPath(new Path2D(d))
  glyph.moveTo(14, 9)
  glyph.arc(12, 9, 2, 0, 2 * Math.PI)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.strokeStyle = ground
  ctx.lineWidth = 5
  ctx.stroke(glyph)
  ctx.strokeStyle = ink
  ctx.lineWidth = 2
  ctx.stroke(glyph)
  return el
}

/** A soft ring, white so the billboard's colour tints it (ink per theme), `sizePx` square. */
export function haloImage(sizePx = 30): HTMLCanvasElement {
  const [el, ctx, ratio] = canvas(sizePx)
  const r = (sizePx * ratio) / 2
  const gradient = ctx.createRadialGradient(r, r, 0, r, r, r)
  gradient.addColorStop(0, 'rgba(255,255,255,0)')
  gradient.addColorStop(0.45, 'rgba(255,255,255,0)')
  gradient.addColorStop(0.7, 'rgba(255,255,255,1)')
  gradient.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, r * 2, r * 2)
  return el
}

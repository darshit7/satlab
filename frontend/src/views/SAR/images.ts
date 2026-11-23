// A comparison's three images as bitmaps: fetched with the session token (an <img> can't send
// it), decoded without colour management, and the change codes coloured with the design tokens.

import { api } from '../../api/client'
import { colorizeChange, hexToRgb } from '../../lib/sar'
import { token } from '../../lib/tokens'
import type { MissionSessionCreated } from '../../types/api'

export interface ComparisonImages {
  before: ImageBitmap
  after: ImageBitmap
  change: ImageBitmap
}

const cache = new Map<string, ComparisonImages>()
/** Bitmaps kept: two comparisons' worth. */
const MAX_ENTRIES = 2

const decode = (blob: Blob) => createImageBitmap(blob, { colorSpaceConversion: 'none', premultiplyAlpha: 'none' })

async function colourChange(codes: ImageBitmap): Promise<ImageBitmap> {
  const canvas = new OffscreenCanvas(codes.width, codes.height)
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) throw new Error('no canvas')
  ctx.drawImage(codes, 0, 0)
  const src = ctx.getImageData(0, 0, codes.width, codes.height)
  const out = new ImageData(codes.width, codes.height)
  // On the radar image the 400 steps read in both themes (the image doesn't change with the theme).
  colorizeChange(src.data, out.data, hexToRgb(token('fuchsia-400')), hexToRgb(token('cyan-400')), 1)
  codes.close()
  return createImageBitmap(out)
}

/** The images of one run of a comparison (cached; the key changes with the generation). */
export async function loadImages(session: Pick<MissionSessionCreated, 'id' | 'token'>, id: number, generation: number, signal: AbortSignal): Promise<ComparisonImages> {
  const key = `${session.id}:${id}:${generation}`
  const hit = cache.get(key)
  if (hit) return hit
  const [before, after, change] = await Promise.all(
    (['before', 'after', 'change'] as const).map(async (kind) => decode(await api.sarImage(session, id, kind, signal))),
  )
  const images = { before: before!, after: after!, change: await colourChange(change!) }
  cache.set(key, images)
  while (cache.size > MAX_ENTRIES) {
    const oldest = cache.keys().next().value as string
    const gone = cache.get(oldest)
    cache.delete(oldest)
    gone?.before.close()
    gone?.after.close()
    gone?.change.close()
  }
  return images
}

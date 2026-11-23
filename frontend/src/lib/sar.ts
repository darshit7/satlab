// Presentation helpers for SAR archive imagery. The server finds the scenes, grids the area,
// detects the changes and draws the images; nothing here judges a change. These functions label,
// colour and lay out what it returned.

import type { LonLat } from './geo'
import { destination } from './geo'
import type { SarAoi, SarComparison, SarPass, SarRegion, SarStatus, SarTrack } from '../types/api'

const EARTH_RADIUS_KM = 6371.0088
const DAY_MS = 86_400_000

/** A square of `sizeKm` around the centre, as a closed ring: the draft area before a search
 * returns the server's exact UTM square. */
export function draftSquare(center: LonLat, sizeKm: number): LonLat[] {
  const halfDeg = (sizeKm / 2 / EARTH_RADIUS_KM) * (180 / Math.PI)
  const north = destination(center, halfDeg, 0)[1]
  const south = destination(center, halfDeg, 180)[1]
  const east = destination(center, halfDeg, 90)[0]
  const west = destination(center, halfDeg, 270)[0]
  return [
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ]
}

/** `YYYY-MM-DD` of a UTC instant. */
export function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

/** Default dates: after = today, before = 24 days earlier (two Sentinel-1 repeats). */
export function defaultDates(todayMs: number): { before: string; after: string } {
  return { before: isoDay(todayMs - 24 * DAY_MS), after: isoDay(todayMs) }
}

/** Why two dates can't be searched, or null if they can. */
export function datesProblem(before: string, after: string, archiveStart: string, today: string): string | null {
  const ok = /^\d{4}-\d{2}-\d{2}$/
  if (!ok.test(before) || !ok.test(after)) return 'Pick both dates.'
  if (before < archiveStart) return `The archive starts on ${archiveStart}.`
  if (after > today) return 'The after date can’t be in the future.'
  if (before >= after) return 'The before date must be earlier than the after date.'
  return null
}

/** Whole days between two ISO instants (rounded). */
export function daysApart(a: string, b: string): number {
  return Math.round(Math.abs(Date.parse(b) - Date.parse(a)) / DAY_MS)
}

/** An area: m² below 0.01 km², else km² (2 decimals) or ha. */
export function formatArea(m2: number): string {
  if (m2 < 10_000) return `${Math.round(m2).toLocaleString('en')} m²`
  const km2 = m2 / 1e6
  return km2 < 1 ? `${(m2 / 10_000).toFixed(1)} ha` : `${km2.toFixed(2)} km²`
}

/** A signed change in dB (U+2212 for minus). */
export function formatDb(db: number, digits = 1): string {
  const text = Math.abs(db).toFixed(digits)
  return db > 0 ? `+${text} dB` : db < 0 ? `−${text} dB` : `${text} dB`
}

/** A share as a whole percent. */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

/** `110 · descending`. */
export function trackLabel(track: Pick<SarTrack, 'relative_orbit' | 'orbit_state'>): string {
  return `Rel. orbit ${track.relative_orbit} · ${track.orbit_state}`
}

/** `2024-10-20 · 06:02 UTC · SENTINEL-1A · covers 100%`. */
export function passLabel(pass: SarPass): string {
  const when = new Date(pass.acquired_at)
  const time = when.toISOString().slice(11, 16)
  return `${isoDay(when.getTime())} · ${time} UTC · ${pass.satellite_name} · covers ${formatPercent(pass.coverage)}`
}

/** A short status word for a comparison. */
export function statusLabel(c: Pick<SarComparison, 'status' | 'queue_position'>): string {
  const words: Record<SarStatus, string> = {
    queued: c.queue_position ? `Queued (${ordinal(c.queue_position)})` : 'Queued',
    reading: 'Reading scenes…',
    detecting: 'Detecting changes…',
    ready: 'Ready',
    failed: 'Failed',
    expired: 'Images expired',
  }
  return words[c.status]
}

function ordinal(n: number): string {
  const suffix = n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th'
  return `${n}${suffix}`
}

/** Whether a comparison still has work ahead (the client polls while any does). */
export function isRunning(c: Pick<SarComparison, 'status'>): boolean {
  return c.status === 'queued' || c.status === 'reading' || c.status === 'detecting'
}

/** "20 Oct → 1 Nov 2024" for a comparison's two passes. */
export function pairTitle(before: string, after: string): string {
  const fmt = (iso: string, withYear: boolean) =>
    new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}), timeZone: 'UTC' })
  const sameYear = before.slice(0, 4) === after.slice(0, 4)
  return `${fmt(before, !sameYear)} → ${fmt(after, true)}`
}

// ---------- change codes (change.png) ----------

/** Code of a pixel valid on both dates but not flagged. */
export const CODE_UNFLAGGED = 1

/** Decode a change code: null for nodata/unflagged, else the sign and the strength 0–1. */
export function decodeChange(code: number): { sign: 'increase' | 'decrease'; t: number } | null {
  if (code >= 2 && code <= 127) return { sign: 'decrease', t: (code - 2) / 125 }
  if (code >= 129 && code <= 254) return { sign: 'increase', t: (code - 129) / 125 }
  return null
}

export type Rgb = [number, number, number]

/** `#rrggbb` → [r, g, b]. */
export function hexToRgb(hex: string): Rgb {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!m) return [128, 128, 128]
  return [parseInt(m[1]!, 16), parseInt(m[2]!, 16), parseInt(m[3]!, 16)]
}

/**
 * Colour a decoded change.png (RGBA bytes, the code in the red channel) into `dst`: flagged
 * pixels in the sign's colour, alpha rising with strength; everything else transparent.
 */
export function colorizeChange(src: Uint8ClampedArray, dst: Uint8ClampedArray, increase: Rgb, decrease: Rgb, opacity: number): void {
  for (let i = 0; i < src.length; i += 4) {
    const change = decodeChange(src[i]!)
    if (change === null) {
      dst[i + 3] = 0
      continue
    }
    const rgb = change.sign === 'increase' ? increase : decrease
    dst[i] = rgb[0]
    dst[i + 1] = rgb[1]
    dst[i + 2] = rgb[2]
    dst[i + 3] = Math.round(255 * opacity * (0.35 + 0.55 * change.t))
  }
}

// ---------- map overlays ----------

/** The area and the comparison's regions as GeoJSON for the 2D map (rings straight from the server). */
export function regionsToGeoJSON(regions: SarRegion[], selected: number | null): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: regions.map((r) => ({
      type: 'Feature',
      properties: { id: r.id, sign: r.sign, selected: r.id === selected },
      geometry: { type: 'Polygon', coordinates: [r.polygon] },
    })),
  }
}

/** The area outline as GeoJSON; `draft` marks a square drawn before the server's grid. */
export function aoiToGeoJSON(ring: LonLat[] | null, draft: boolean): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: ring ? [{ type: 'Feature', properties: { draft }, geometry: { type: 'Polygon', coordinates: [ring] } }] : [],
  }
}

/** The area's ring: the server's exact square once known, else a draft square. */
export function aoiRing(aoi: SarAoi, serverRing: LonLat[] | null): { ring: LonLat[]; draft: boolean } {
  return serverRing ? { ring: serverRing, draft: false } : { ring: draftSquare([aoi.lon_deg, aoi.lat_deg], aoi.size_km), draft: true }
}

/** Lon/lat envelope [west, south, east, north] of a ring. */
export function ringBounds(ring: LonLat[]): [number, number, number, number] {
  const lons = ring.map((p) => p[0])
  const lats = ring.map((p) => p[1])
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
}

// ---------- swipe viewer ----------

/** A view of the image: scale (canvas px per image px) and the image point at the canvas origin. */
export interface View {
  scale: number
  x: number
  y: number
}

/** The view fitting the whole image in the canvas, centred. */
export function fitView(imageW: number, imageH: number, canvasW: number, canvasH: number): View {
  const scale = Math.min(canvasW / imageW, canvasH / imageH)
  return { scale, x: -(canvasW / scale - imageW) / 2, y: -(canvasH / scale - imageH) / 2 }
}

/** Zoom by `factor` keeping the image point under (cx, cy) fixed; scale bounded. */
export function zoomAt(view: View, factor: number, cx: number, cy: number, minScale: number, maxScale = 16): View {
  const scale = Math.min(Math.max(view.scale * factor, minScale), maxScale)
  const ix = view.x + cx / view.scale
  const iy = view.y + cy / view.scale
  return { scale, x: ix - cx / scale, y: iy - cy / scale }
}

/** The view framing an image box [col0, row0, col1, row1] with a margin. */
export function fitBox(box: [number, number, number, number], canvasW: number, canvasH: number, minScale: number, margin = 0.3): View {
  const [c0, r0, c1, r1] = box
  const w = Math.max(c1 - c0, 1) * (1 + 2 * margin)
  const h = Math.max(r1 - r0, 1) * (1 + 2 * margin)
  const scale = Math.min(Math.max(Math.min(canvasW / w, canvasH / h), minScale), 16)
  const cx = (c0 + c1) / 2
  const cy = (r0 + r1) / 2
  return { scale, x: cx - canvasW / scale / 2, y: cy - canvasH / scale / 2 }
}

/** Image point → canvas point. */
export function imageToCanvas(view: View, ix: number, iy: number): [number, number] {
  return [(ix - view.x) * view.scale, (iy - view.y) * view.scale]
}

/** The split (0–100, % of the canvas width) under a pointer. */
export function splitFromPointer(clientX: number, left: number, width: number): number {
  if (width <= 0) return 50
  return Math.min(100, Math.max(0, ((clientX - left) / width) * 100))
}

/** The split after a key press, or null for keys the slider doesn't handle. */
export function splitFromKey(split: number, key: string): number | null {
  const steps: Record<string, number> = { ArrowLeft: -2, ArrowDown: -2, ArrowRight: 2, ArrowUp: 2, PageDown: -10, PageUp: 10 }
  if (key === 'Home') return 0
  if (key === 'End') return 100
  const step = steps[key]
  return step === undefined ? null : Math.min(100, Math.max(0, split + step))
}

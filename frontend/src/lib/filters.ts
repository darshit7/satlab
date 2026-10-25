import { MAX_RISING_WITHIN_MIN, SATELLITE_TYPES, type SatelliteType, type VisibilityFilter } from '../types/ws'

export const TYPE_LABELS: Record<SatelliteType, string> = {
  station: 'Stations',
  weather: 'Weather',
  comms: 'Telecom', // communications satellites (Starlink, OneWeb…): not the Communications section
  navigation: 'Navigation',
  sar: 'SAR',
  other: 'Other',
}

/** Minimum-elevation choices; 0 means "the horizon mask" (the server applies max(mask, value)). */
export const MIN_ELEVATION_OPTIONS = [0, 10, 20, 30, 45, 60] as const
export const RISING_WITHIN_OPTIONS = [0, 15, 30, 60] as const

export const DEFAULT_FILTERS: VisibilityFilter = {
  types: [...SATELLITE_TYPES],
  sar_only: false,
  link_ok_only: false,
  comms_only: false,
  min_elevation_deg: 0,
  rising_within_min: 30,
}

/** Rebuild filters from untrusted input (localStorage): unknown types dropped, ranges clamped. */
export function sanitizeFilters(raw: unknown): VisibilityFilter {
  if (typeof raw !== 'object' || raw === null) return { ...DEFAULT_FILTERS, types: [...DEFAULT_FILTERS.types] }
  const r = raw as Record<string, unknown>
  const types = Array.isArray(r.types)
    ? SATELLITE_TYPES.filter((t) => (r.types as unknown[]).includes(t))
    : [...DEFAULT_FILTERS.types]
  const num = (v: unknown, fallback: number, min: number, max: number) =>
    typeof v === 'number' && Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : fallback
  return {
    types,
    sar_only: r.sar_only === true,
    link_ok_only: r.link_ok_only === true,
    comms_only: r.comms_only === true,
    min_elevation_deg: num(r.min_elevation_deg, DEFAULT_FILTERS.min_elevation_deg, 0, 90),
    rising_within_min: Math.round(num(r.rising_within_min, DEFAULT_FILTERS.rising_within_min, 0, MAX_RISING_WITHIN_MIN)),
  }
}

/**
 * The active filters in words, for the summary that is always on screen: what the map, the sky
 * views and the Overhead lists show. The server applies max(mask, min elevation), so a value at or
 * under the mask reads as the mask.
 */
export function filterSummary(filters: VisibilityFilter, maskDeg: number | null): string[] {
  const types = SATELLITE_TYPES.filter((t) => filters.types.includes(t))
  const parts = [
    types.length === SATELLITE_TYPES.length
      ? 'All types'
      : types.length === 0
        ? 'No types'
        : types.length <= 2
          ? types.map((t) => TYPE_LABELS[t]).join(', ')
          : `${types.length} types`,
  ]
  if (filters.sar_only) parts.push('SAR-capable')
  if (filters.comms_only) parts.push('Communications')
  if (filters.link_ok_only) parts.push('Link OK')
  parts.push(
    filters.min_elevation_deg > (maskDeg ?? 0)
      ? `El ≥ ${filters.min_elevation_deg}°`
      : maskDeg !== null
        ? `El ≥ mask (${maskDeg}°)`
        : 'El ≥ mask',
  )
  parts.push(filters.rising_within_min > 0 ? `Rising ≤ ${filters.rising_within_min} min` : 'Up now only')
  return parts
}

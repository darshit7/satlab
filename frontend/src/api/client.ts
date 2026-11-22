import type {
  CatalogStatus,
  GroundStation,
  LinkProfiles,
  MissionSessionCreate,
  MissionSessionCreated,
  PacketTemplates,
  PassPrediction,
  SarComparison,
  SarComparisonCreate,
  SarComparisonList,
  SarPolarization,
  SarSearch,
  SarSource,
  SatelliteSearchResult,
} from '../types/api'
import { appUrl } from '../lib/basePath'

/** The session's capability token, sent on every session-scoped request. */
type SessionRef = Pick<MissionSessionCreated, 'id' | 'token'>
const owner = (session: SessionRef) => ({ 'X-Session-Token': session.token })

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(appUrl(path), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // non-JSON error body
    }
    throw new Error(`${response.status}: ${detail}`)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

/** Largest image accepted from the API (a 20 km comparison's PNGs are a few MB). */
const MAX_IMAGE_BYTES = 32 * 1024 * 1024

async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await fetch(appUrl(path), init)
  if (!response.ok) throw new Error(`${response.status}: ${response.statusText}`)
  if (response.headers.get('Content-Type') !== 'image/png') throw new Error('not an image')
  const blob = await response.blob()
  if (blob.size > MAX_IMAGE_BYTES) throw new Error('image too large')
  return blob
}

export interface SarSearchQuery {
  lat_deg: number
  lon_deg: number
  size_km: number
  before: string
  after: string
  polarization: SarPolarization
  platform: string | null
}

export const api = {
  listStations: () => request<GroundStation[]>('/api/stations'),
  linkProfiles: () => request<LinkProfiles>('/api/link-profiles'),
  packetTemplates: () => request<PacketTemplates>('/api/packet-templates'),
  searchSatellites: (q: string, remote = false, signal?: AbortSignal) =>
    request<SatelliteSearchResult>(
      `/api/satellites?${new URLSearchParams({ q, limit: '25', remote: String(remote) })}`,
      { signal },
    ),
  catalogStatus: () => request<CatalogStatus>('/api/catalog/status'),
  createSession: (body: MissionSessionCreate) =>
    request<MissionSessionCreated>('/api/sessions', { method: 'POST', body: JSON.stringify(body) }),
  endSession: (session: SessionRef) =>
    request<void>(`/api/sessions/${session.id}/end`, { method: 'POST', headers: owner(session) }),
  passes: (session: SessionRef, noradId: number, start: string, hours = 24, signal?: AbortSignal) =>
    request<PassPrediction>(
      `/api/sessions/${session.id}/passes?${new URLSearchParams({ norad_id: String(noradId), start, hours: String(hours) })}`,
      { signal, headers: owner(session) },
    ),
  sarSource: () => request<SarSource>('/api/sar/source'),
  sarScenes: (session: SessionRef, q: SarSearchQuery, signal?: AbortSignal) => {
    const params = new URLSearchParams({
      lat_deg: q.lat_deg.toFixed(5),
      lon_deg: q.lon_deg.toFixed(5),
      size_km: String(q.size_km),
      before: q.before,
      after: q.after,
      polarization: q.polarization,
    })
    if (q.platform) params.set('platform', q.platform)
    return request<SarSearch>(`/api/sessions/${session.id}/sar/scenes?${params}`, { signal, headers: owner(session) })
  },
  sarCompare: (session: SessionRef, body: SarComparisonCreate) =>
    request<SarComparison>(`/api/sessions/${session.id}/sar/comparisons`, {
      method: 'POST',
      body: JSON.stringify(body),
      headers: owner(session),
    }),
  sarComparisons: (session: SessionRef, signal?: AbortSignal) =>
    request<SarComparisonList>(`/api/sessions/${session.id}/sar/comparisons`, { signal, headers: owner(session) }),
  sarRecompute: (session: SessionRef, id: number) =>
    request<SarComparison>(`/api/sessions/${session.id}/sar/comparisons/${id}/recompute`, {
      method: 'POST',
      headers: owner(session),
    }),
  sarRemove: (session: SessionRef, id: number) =>
    request<void>(`/api/sessions/${session.id}/sar/comparisons/${id}`, { method: 'DELETE', headers: owner(session) }),
  sarImage: (session: SessionRef, id: number, kind: 'before' | 'after' | 'change', signal?: AbortSignal) =>
    requestBlob(`/api/sessions/${session.id}/sar/comparisons/${id}/images/${kind}.png`, {
      signal,
      headers: owner(session),
    }),
}

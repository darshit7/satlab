import type {
  CatalogStatus,
  GroundStation,
  MissionSessionCreate,
  MissionSessionCreated,
  PassPrediction,
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

export const api = {
  listStations: () => request<GroundStation[]>('/api/stations'),
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
}

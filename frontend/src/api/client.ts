import type {
  CatalogStatus,
  GroundStation,
  GroundStationCreate,
  MissionSession,
  MissionSessionCreate,
  PassPrediction,
  SatelliteSearchResult,
} from '../types/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
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
  createStation: (body: GroundStationCreate) =>
    request<GroundStation>('/api/stations', { method: 'POST', body: JSON.stringify(body) }),
  searchSatellites: (q: string, remote = false, signal?: AbortSignal) =>
    request<SatelliteSearchResult>(
      `/api/satellites?${new URLSearchParams({ q, limit: '25', remote: String(remote) })}`,
      { signal },
    ),
  catalogStatus: () => request<CatalogStatus>('/api/catalog/status'),
  createSession: (body: MissionSessionCreate) =>
    request<MissionSession>('/api/sessions', { method: 'POST', body: JSON.stringify(body) }),
  endSession: (id: number) => request<void>(`/api/sessions/${id}/end`, { method: 'POST' }),
  passes: (sessionId: number, noradId: number, start: string, hours = 24, signal?: AbortSignal) =>
    request<PassPrediction>(
      `/api/sessions/${sessionId}/passes?${new URLSearchParams({ norad_id: String(noradId), start, hours: String(hours) })}`,
      { signal },
    ),
}

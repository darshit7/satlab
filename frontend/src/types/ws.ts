// Mirrors backend/app/models/ws.py — change both in lockstep.

export const SATELLITE_TYPES = ['station', 'weather', 'comms', 'navigation', 'sar', 'other'] as const
export type SatelliteType = (typeof SATELLITE_TYPES)[number]

/** Longest "rising within" the server accepts (MAX_RISING_WITHIN_MIN). */
export const MAX_RISING_WITHIN_MIN = 60

// ---------- server → client ----------

export interface ClockState {
  time_scale: number
  paused: boolean
}

export interface ObserverState {
  /** Null for a custom (session-only) location. */
  station_id: number | null
  name: string
  lat_deg: number
  lon_deg: number
  alt_m: number
  horizon_mask_deg: number
}

export interface SatelliteState {
  norad_id: number
  name: string
  type: SatelliteType
  is_sar_capable: boolean
  tle_epoch: string
  lat_deg: number
  lon_deg: number
  alt_m: number
  azimuth_deg: number
  elevation_deg: number
  range_m: number
  range_rate_m_s: number
  /** Elevation ≥ the horizon mask: "in view" (geometric, not naked-eye). */
  above_mask: boolean
}

/** Streamed satellites = (above the mask ∩ the session filter) ∪ tracked ∪ selected. */
export interface Snapshot {
  type: 'snapshot'
  session_id: number
  tick: number
  sim_time: string
  wall_time: string
  clock: ClockState
  observer: ObserverState
  /** Satellites above the mask before the session filter. */
  visible_count: number
  satellites: SatelliteState[]
}

export interface GroundTrackMessage {
  type: 'ground_track'
  norad_id: number
  start_time: string
  end_time: string
  period_s: number
  points: [lon_deg: number, lat_deg: number][]
}

/** The current (or next) pass in the sky, sampled evenly in time from start_time to end_time. */
export interface PassArcMessage {
  type: 'pass_arc'
  norad_id: number
  /** Null when the pass starts / ends outside the arc's window (e.g. geostationary). */
  aos_time: string | null
  los_time: string | null
  max_elev_deg: number
  start_time: string
  end_time: string
  points: [azimuth_deg: number, elevation_deg: number][]
}

export interface UpcomingPass {
  norad_id: number
  name: string
  type: SatelliteType
  is_sar_capable: boolean
  aos_time: string
  aos_az_deg: number
  max_elev_deg: number
}

export interface UpcomingMessage {
  type: 'upcoming'
  /** computing: first sweep not done yet · paused: the clock is too fast for the sweep. */
  status: 'computing' | 'ready' | 'paused'
  window_start: string | null
  window_end: string | null
  /** Matching satellites before the `passes` cap. */
  total: number
  passes: UpcomingPass[]
}

export interface ErrorMessage {
  type: 'error'
  message: string
}

export type ServerMessage = Snapshot | GroundTrackMessage | PassArcMessage | UpcomingMessage | ErrorMessage

// ---------- client → server ----------

export interface SetTracked {
  type: 'set_tracked'
  norad_ids: number[]
}

export interface SetSelected {
  type: 'set_selected'
  norad_id: number | null
}

export interface VisibilityFilter {
  types: SatelliteType[]
  sar_only: boolean
  /** Effective threshold is max(horizon mask, this). */
  min_elevation_deg: number
  rising_within_min: number
}

export interface SetFilter extends VisibilityFilter {
  type: 'set_filter'
}

export interface SetClock {
  type: 'set_clock'
  time_scale?: number
  paused?: boolean
  sim_time?: string
}

export type ClientMessage = SetTracked | SetSelected | SetFilter | SetClock

/** WebSocket close code: session not found or already ended (backend/app/main.py). */
export const WS_CLOSE_SESSION_UNAVAILABLE = 4404

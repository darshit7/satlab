// Mirrors backend/app/models/api.py — change both in lockstep.
// Datetimes are ISO-8601 UTC strings.

import type { SatelliteType } from './ws'

export interface GroundStationCreate {
  name: string
  lat_deg: number
  lon_deg: number
  alt_m: number
}

export interface GroundStation extends GroundStationCreate {
  id: number
  created_at: string
}

export interface SatelliteSummary {
  norad_id: number
  name: string
  type: SatelliteType | null
  is_sar_capable: boolean
  tle_epoch: string | null
  fetched_at: string | null
}

export interface SatelliteSearchResult {
  query: string
  remote_searched: boolean
  results: SatelliteSummary[]
}

export interface CatalogStatus {
  satellite_count: number
  group: string
  last_fetched_at: string | null
  refreshing: boolean
  last_error: string | null
}

export interface MissionSessionCreate {
  station_id: number
  name?: string | null
  sim_start_time?: string | null
  time_scale?: number
}

export interface MissionSession {
  id: number
  station_id: number
  name: string | null
  started_at: string
  ended_at: string | null
  sim_start_time: string
  time_scale: number
  station: GroundStation
}

export interface PassWindow {
  aos_time: string
  los_time: string
  max_time: string
  max_elev_deg: number
  aos_az_deg: number
  los_az_deg: number
  duration_s: number
}

export interface PassPrediction {
  session_id: number
  station_id: number
  norad_id: number
  name: string
  tle_epoch: string
  horizon_mask_deg: number
  start: string
  end: string
  /** Above the mask for the whole window (e.g. geostationary). */
  always_visible: boolean
  passes: PassWindow[]
}

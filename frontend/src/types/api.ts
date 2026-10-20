// Mirrors backend/app/models/api.py — change both in lockstep.
// Datetimes are ISO-8601 UTC strings.

import type { SatelliteType } from './ws'

/** An observer location. A custom one is used for a single session, never saved as a station. */
export interface LocationInput {
  lat_deg: number
  lon_deg: number
  alt_m: number
}

/** A preset station (seed data). */
export interface GroundStation extends LocationInput {
  id: number
  name: string
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

/** Where a session observes from: a preset station or a custom location (exactly one). */
export type SessionSite = { station_id: number; location?: never } | { location: LocationInput; station_id?: never }

export type MissionSessionCreate = SessionSite & {
  name?: string | null
  sim_start_time?: string | null
  time_scale?: number
}

export interface MissionSession extends LocationInput {
  id: number
  /** The preset the location came from; null for a custom location. */
  station_id: number | null
  location_name: string
  name: string | null
  started_at: string
  ended_at: string | null
  sim_start_time: string
  time_scale: number
}

/** Returned once by POST /api/sessions. `token` proves ownership: keep it in memory only. */
export interface MissionSessionCreated extends MissionSession {
  token: string
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
  station_id: number | null
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

export interface LinkSource {
  title: string
  url: string
}

/** A satellite's published downlink budget, as the link state is computed from it. */
export interface LinkProfile {
  norad_id: number
  satellite_name: string
  label: string
  frequency_hz: number
  eirp_dbw: number
  station_gt_db_k: number
  system_loss_db: number
  /** At atmospheric_ref_elev_deg, scaled along the slant path. */
  atmospheric_loss_db: number
  atmospheric_ref_elev_deg: number
  required_ebn0_db: number
  data_rate_bps: number
  coverage_half_angle_deg: number | null
  sources: LinkSource[]
}

export interface LinkProfiles {
  /** Green strictly above. */
  green_margin_db: number
  /** Amber from here up to green; red below (or below the mask). */
  amber_margin_db: number
  profiles: LinkProfile[]
}

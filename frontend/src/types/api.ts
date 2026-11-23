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
  /** When Celestrak last served (or confirmed) the group. */
  last_fetched_at: string | null
  refreshing: boolean
  last_error: string | null
  /** The group is fetched again once it's this old (absent from older servers). */
  max_age_hours?: number | null
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

export interface CommandParam {
  name: string
  label: string
  type: 'int' | 'text'
  min: number | null
  max: number | null
  max_length: number
  charset: 'printable' | 'callsign'
}

export interface TelemetryTemplate {
  name: string
  label: string
  framing: 'ax25' | 'ccsds'
  /** Null: sent only in answer to a command. */
  interval_s: number | null
  downlink: 'simulated' | 'satnogs'
  /** False: an illustrative layout. */
  layout_sourced: boolean
  sources: LinkSource[]
}

export interface CommandTemplate {
  name: string
  label: string
  framing: 'ax25' | 'ccsds'
  params: CommandParam[]
  response: 'ack' | 'telemetry' | 'digipeat'
  layout_sourced: boolean
  sources: LinkSource[]
}

export interface SatelliteComms {
  norad_id: number
  satellite_name: string
  telemetry: TelemetryTemplate[]
  /** Empty: receive-only (no published command set). */
  commands: CommandTemplate[]
  /** Seeded downlink frequency; null for satellites with a link profile (the profile's frequency). */
  downlink_frequency_hz?: number | null
  downlink_sources?: LinkSource[]
}

/** The generic packet templates every satellite without its own uses (debris and rocket bodies
 *  excepted): illustrative layout, modelled values. */
export interface GenericComms {
  telemetry: TelemetryTemplate[]
  commands: CommandTemplate[]
  /** The TT&C antenna coverage it assumes, off nadir (90: omnidirectional); null: the model default. */
  antenna_half_angle_deg: number | null
}

/** Every satellite with packet templates of its own, and the generic set for the rest (reference
 *  data, like the link profiles). */
export interface PacketTemplates {
  /** Whether real frames can be replayed at all on this server. */
  satnogs_configured: boolean
  /** The satellites with their own formats. */
  satellites: SatelliteComms[]
  /** Null or absent (older servers): only the listed satellites can be contacted. */
  generic?: GenericComms | null
}

// ---------- SAR imagery ----------

export type SarPolarization = 'VV' | 'VH'
export type AoiSizeKm = 5 | 10 | 20
export type SarStatus = 'queued' | 'reading' | 'detecting' | 'ready' | 'failed' | 'expired'
export type LonLat = [lon_deg: number, lat_deg: number]
export type Bbox = [min_lon: number, min_lat: number, max_lon: number, max_lat: number]

export interface SarAoi {
  lat_deg: number
  lon_deg: number
  size_km: AoiSizeKm
}

export interface SarAoiOut extends SarAoi {
  bbox: Bbox
  /** The exact UTM square, a closed lon/lat ring. */
  polygon: LonLat[]
  epsg: number
  width_px: number
  height_px: number
  resolution_m: number
}

/** One satellite pass over the area: one or more scene frames, mosaicked. */
export interface SarPass {
  id: string
  frames: string[]
  platform: string
  satellite_name: string
  norad_id: number | null
  acquired_at: string
  orbit_state: 'ascending' | 'descending'
  relative_orbit: number
  polarizations: string[]
  /** Share of the area covered (0–1); estimated from footprints in a search. */
  coverage: number
}

export interface SarTrack {
  key: string
  relative_orbit: number
  orbit_state: 'ascending' | 'descending'
  before: SarPass[]
  after: SarPass[]
  proposed_before: string | null
  proposed_after: string | null
}

export interface SarSearch {
  aoi: SarAoiOut
  polarization: SarPolarization
  platform: string | null
  before_window: [string, string]
  after_window: [string, string]
  tracks: SarTrack[]
  proposed_track: string | null
  skipped?: number
  truncated?: boolean
}

export interface SarComparisonCreate {
  aoi: SarAoi
  polarization: SarPolarization
  before: string[]
  after: string[]
  requested_before: string
  requested_after: string
}

export interface SarRegion {
  id: number
  sign: 'increase' | 'decrease'
  area_m2: number
  mean_db: number
  max_abs_db: number
  polygon: LonLat[]
  bbox: Bbox
  centroid: LonLat
  /** col0, row0, col1, row1 on the comparison's grid (the images' pixels). */
  px: [number, number, number, number]
}

export interface SarDisplay {
  db_min: number
  db_max: number
  heatmap_max_db: number
}

export interface SarComparison {
  id: number
  status: SarStatus
  queue_position?: number | null
  error?: string | null
  aoi: SarAoiOut
  polarization: SarPolarization
  before: SarPass
  after: SarPass
  requested_before: string
  requested_after: string
  method: 'log_ratio'
  params_version: number
  /** Run number; images change with it. */
  generation: number
  coverage?: number | null
  otsu_db?: number | null
  threshold_db?: number | null
  floor_db: number
  changed_fraction?: number | null
  increase_area_m2?: number | null
  decrease_area_m2?: number | null
  regions?: SarRegion[]
  regions_truncated?: boolean
  widespread?: boolean
  display: SarDisplay
  attribution: string
  sim_time: string
  created_at: string
  finished_at?: string | null
}

export interface SarComparisonList {
  comparisons: SarComparison[]
  max_per_session: number
  running: number
}

export interface SarPlatform {
  platform: string
  satellite_name: string
  norad_id: number
}

export interface SarSource {
  enabled: boolean
  status: 'ok' | 'unavailable' | 'unknown'
  collection: string
  title: string
  license: string
  license_url: string
  dataset_url: string
  attribution: string[]
  archive_start: string
  search_window_days: number
  resolution_m: number
  polarizations: string[]
  params_version: number
  speckle_window_px: number
  min_abs_db: number
  min_area_m2: number
  display: Record<string, SarDisplay>
  limits: { searches_per_min: number; comparisons_per_hour: number; max_per_session: number; aoi_sizes_km: number[] }
  platforms: SarPlatform[]
}

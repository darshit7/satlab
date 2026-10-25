// Mirrors backend/app/models/ws.py — change both in lockstep.

export const SATELLITE_TYPES = ['station', 'weather', 'comms', 'navigation', 'sar', 'other'] as const
export type SatelliteType = (typeof SATELLITE_TYPES)[number]

/** Longest "rising within" the server accepts (MAX_RISING_WITHIN_MIN). */
export const MAX_RISING_WITHIN_MIN = 60

/** Link and subsystem health: green > 6 dB margin, amber 0–6 dB, red below (AGENTS.md §7). */
export type Rag = 'green' | 'amber' | 'red'

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

/** The satellite's published downlink, received at this station (one link-budget computation). */
export interface LinkState {
  frequency_hz: number
  data_rate_bps: number
  /** Null below the horizon: no line of sight. */
  ebn0_db: number | null
  /** Eb/N0 minus the required Eb/N0. */
  margin_db: number | null
  /** Received minus transmitted frequency; positive while approaching. */
  doppler_hz: number
  /** Red below the horizon mask, whatever the margin. */
  rag: Rag
  /** Elevations where the margin would be green at the current altitude; empty when it never is. */
  strong_link_elev_bands_deg: [from_deg: number, to_deg: number][]
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
  /** Null (or absent from an older server): no published RF parameters for this satellite. */
  link?: LinkState | null
}

export type Illumination = 'sunlit' | 'penumbra' | 'eclipse'

export interface PowerHealth {
  rag: Rag
  soc_pct: number
  sunlit_fraction: number
  illumination: Illumination
  modelled: boolean
}

export interface ThermalHealth {
  rag: Rag
  temperature_c: number
  band_min_c: number
  band_max_c: number
  modelled: boolean
}

export interface StorageHealth {
  /** Null without a link profile: nothing to downlink at. */
  rag: Rag | null
  fill_pct: number | null
  activity: 'filling' | 'downlinking' | 'full' | null
  modelled: boolean
}

export interface AttitudeHealth {
  /** Null below the horizon mask. */
  rag: Rag | null
  off_nadir_deg: number | null
  half_beamwidth_deg: number
  modelled: boolean
}

export interface RfHealth {
  /** Null: no published RF parameters. */
  rag: Rag | null
  margin_db: number | null
  modelled: boolean
}

/** Subsystem health of the selected satellite. Rollup: red if any is red, else null if any has no
 *  data, else the worse of amber and green. */
export interface HealthState {
  norad_id: number
  model_version: number
  rollup: Rag | null
  power: PowerHealth
  thermal: ThermalHealth
  storage: StorageHealth
  attitude: AttitudeHealth
  rf: RfHealth
}

export type ContactStatus = 'active' | 'below_mask' | 'no_template' | 'too_fast'
export type ArchiveStatus = 'ok' | 'empty' | 'not_configured' | 'unavailable'

/** The station's contact with the selected satellite: the antenna follows the selection. */
export interface ContactState {
  norad_id: number
  state: ContactStatus
  /** Null without packet templates. */
  downlink: 'simulated' | 'satnogs' | null
  /** A published link budget drives bit errors. */
  channel_modelled: boolean
  /** Null below the horizon. */
  one_way_light_time_s: number | null
  /** The SatNOGS replay, for satellites whose downlink is real archived frames. */
  archive?: ArchiveStatus | null
}

/** Pass automation holds the antenna: from shortly before a booked pass's AOS until its LOS the
 *  station follows that satellite (the server selects it), and at AOS it checks the systems. */
export interface AutomationState {
  norad_id: number
  aos_time: string
  los_time: string
  max_elev_deg: number
  /** Booked for this pass (before the check), or released / held by it (after). */
  commands: number
  checked: boolean
  /** The clock ran faster than comms can follow and was dropped to real time. */
  clock_slowed?: boolean
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
  /** Of the selected satellite. */
  health?: HealthState | null
  /** With the selected satellite (absent from an older server). */
  contact?: ContactState | null
  /** While a booked pass holds the antenna (absent from an older server). */
  automation?: AutomationState | null
}

/** The orbit around the sim time, sampled evenly in time: drawn on the map and the globe. */
export interface GroundTrackMessage {
  type: 'ground_track'
  norad_id: number
  start_time: string
  end_time: string
  period_s: number
  points: [lon_deg: number, lat_deg: number][]
  /** Per point: height above the WGS84 ellipsoid (absent from an older server). */
  alt_m?: number[] | null
  /** Per point: above this station's horizon mask, the in-view part of the orbit. */
  above_mask?: boolean[] | null
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
  /** Link state at each point; null without a link profile. */
  rag?: Rag[] | null
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

export interface DecodedField {
  name: string
  value: number | string | null
  unit?: string | null
}

/** Where a replayed frame really came from (ids for the public SatNOGS pages). */
export interface SatnogsOrigin {
  observation_id: number | null
  station_id: number | null
  received_at: string | null
}

export type PacketKind = 'telemetry' | 'command' | 'ack' | 'response' | 'digipeat'

/** One frame on the simulated link. Uplink is never transmitted; downlink is simulated or a real
 *  SatNOGS frame replayed. `delivered` false: no line of sight. */
export interface PacketMessage {
  type: 'packet'
  seq: number
  norad_id: number
  direction: 'up' | 'down'
  kind: PacketKind
  template: string
  label: string
  framing: 'ax25' | 'ccsds'
  source: 'simulated' | 'satnogs'
  sim_time: string
  size_bytes: number
  raw_hex: string
  delivered: boolean
  /** Null when nothing readable arrived. */
  crc_ok: boolean | null
  /** Null: the channel isn't modelled (no published link budget). */
  bit_errors: number | null
  ber: number | null
  margin_db: number | null
  one_way_light_time_s: number | null
  /** Null when the check failed or nothing arrived. */
  decoded: DecodedField[] | null
  /** False: an illustrative layout. */
  layout_sourced: boolean
  command_id?: number | null
  origin?: SatnogsOrigin | null
}

/** scheduled: waiting for its pass · held: the system check at AOS found a subsystem red. */
export type CommandStatus = 'scheduled' | 'queued' | 'sent' | 'acked' | 'failed' | 'aborted' | 'held'
export type Subsystem = 'power' | 'thermal' | 'storage' | 'attitude' | 'rf'

/** The pass a command was booked for (predicted by the server). */
export interface ScheduledPass {
  aos_time: string
  los_time: string
  max_elev_deg: number
}

/** The automatic check at AOS: the satellite's subsystem health as the inspector shows it. `go`: no
 *  subsystem red (amber passes; null = no data, not checked). */
export interface SystemCheck {
  sim_time: string
  norad_id: number
  rollup: Rag | null
  go: boolean
  subsystems: Record<Subsystem, Rag | null>
}

export interface CommandStatusMessage {
  type: 'command_status'
  command_id: number
  norad_id: number
  command: string
  status: CommandStatus
  reason?: string | null
  sim_time: string
  /** Booked for a pass rather than sent live. */
  scheduled_pass?: ScheduledPass | null
  /** The check at AOS that released or held it. */
  check?: SystemCheck | null
}

export type AlarmSeverity = 'info' | 'warning' | 'critical'

/** An alarm raised by pass automation (sent again when acknowledged). */
export interface AlarmMessage {
  type: 'alarm'
  /** Numbers the session's alarms from 1. */
  alarm_id: number
  severity: AlarmSeverity
  subsystem: Subsystem | null
  norad_id: number | null
  message: string
  sim_time: string
  acknowledged: boolean
}

export interface ErrorMessage {
  type: 'error'
  message: string
}

export type ServerMessage =
  | Snapshot
  | GroundTrackMessage
  | PassArcMessage
  | UpcomingMessage
  | PacketMessage
  | CommandStatusMessage
  | AlarmMessage
  | ErrorMessage

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
  /** Only satellites with a link profile; of those in view, only with a usable margin. */
  link_ok_only: boolean
  /** Only satellites with packet templates: the ones comms works with (some are receive-only). */
  comms_only: boolean
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

/** A command for the satellite in contact (simulated uplink; checked against its template). */
export interface SendCommand {
  type: 'send_command'
  norad_id: number
  command: string
  params: Record<string, number | string>
}

/** Book a command for a future pass; `aos_time` names the pass as the server's schedule listed it.
 *  At AOS the system check sends it unless a subsystem is red. */
export interface ScheduleCommand {
  type: 'schedule_command'
  norad_id: number
  command: string
  params: Record<string, number | string>
  aos_time: string
}

/** Cancel a command still waiting for its pass. */
export interface CancelCommand {
  type: 'cancel_command'
  command_id: number
}

export interface AckAlarm {
  type: 'ack_alarm'
  alarm_id: number
}

export type ClientMessage =
  | SetTracked
  | SetSelected
  | SetFilter
  | SetClock
  | SendCommand
  | ScheduleCommand
  | CancelCommand
  | AckAlarm

/** First message on every connection: the session's token (MissionSessionCreated.token). */
export interface AuthMessage {
  type: 'auth'
  token: string
}

// WebSocket close codes (backend/app/main.py).
/** No valid `auth` message in time. */
export const WS_CLOSE_AUTH_REQUIRED = 4401
/** Session not found, already ended, or not this token's session. */
export const WS_CLOSE_SESSION_UNAVAILABLE = 4404
/** The server is running its maximum number of sessions; retry later. */
export const WS_CLOSE_SERVER_BUSY = 4503

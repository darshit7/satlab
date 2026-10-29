"""WebSocket contract. Mirrored in frontend/src/types/ws.ts — change both in lockstep.

Server → client: ``snapshot`` (every tick; link state per satellite, subsystem health and comms
contact of the selection, pass automation), ``ground_track`` and ``pass_arc`` (when (re)computed),
``upcoming`` (when the rising-soon list changes), ``packet`` (each frame up or down),
``command_status``, ``alarm``, ``error``.
Client → server: ``auth`` (first message on every connection), then ``set_tracked``,
``set_selected``, ``set_filter``, ``set_clock``, ``send_command``, ``schedule_command``,
``cancel_command``, ``ack_alarm``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StrictInt, StrictStr, TypeAdapter

SatelliteType = Literal["station", "weather", "comms", "navigation", "sar", "other"]
SATELLITE_TYPES: tuple[SatelliteType, ...] = (
    "station",
    "weather",
    "comms",
    "navigation",
    "sar",
    "other",
)

# Bound on any client-sent NORAD list (the handler applies the configured, smaller limit).
MAX_CLIENT_NORAD_IDS = 100
# Longest "rising within" the filter accepts; the rising-soon sweep window is sized from it.
MAX_RISING_WITHIN_MIN = 60
NoradId = Annotated[int, Field(ge=1, le=999_999_999)]

# Link and subsystem health (AGENTS.md §7): green > 6 dB margin, amber 0-6 dB, red below.
Rag = Literal["green", "amber", "red"]

# ---------- server → client ----------


class ClockState(BaseModel):
    time_scale: float
    paused: bool


class ObserverState(BaseModel):
    station_id: int | None  # null for a custom (session-only) location
    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    horizon_mask_deg: float


class LinkState(BaseModel):
    """The satellite's published downlink, received at this station (one link-budget computation
    drives the readout, the sky-view colours and the RF subsystem)."""

    frequency_hz: float
    data_rate_bps: float
    ebn0_db: float | None  # null below the horizon: no line of sight
    margin_db: float | None  # Eb/N0 minus the required Eb/N0
    doppler_hz: float  # received minus transmitted frequency; positive while approaching
    rag: Rag  # red below the horizon mask, whatever the margin
    # Elevations where the margin would be green at the satellite's current altitude: the inner
    # "strong-link" region of the sky. Empty when it never is.
    strong_link_elev_bands_deg: list[tuple[float, float]]


class SatelliteState(BaseModel):
    norad_id: int
    name: str
    type: SatelliteType
    is_sar_capable: bool
    tle_epoch: datetime
    # sub-satellite point (WGS84)
    lat_deg: float
    lon_deg: float
    alt_m: float
    # topocentric look angles from the observer
    azimuth_deg: float
    elevation_deg: float
    range_m: float
    range_rate_m_s: float
    above_mask: bool  # elevation ≥ the horizon mask: "in view" (geometric, not naked-eye)
    link: LinkState | None = None  # null: no published RF parameters for this satellite
    # The selection only (null otherwise): Earth central angle from the sub-point to the rim of
    # the ground area from which the satellite is above the horizon mask (its footprint).
    footprint_radius_deg: float | None = None


Illumination = Literal["sunlit", "penumbra", "eclipse"]


class PowerHealth(BaseModel):
    rag: Rag
    soc_pct: float  # battery state of charge
    sunlit_fraction: float  # of the solar disc, now (real geometry)
    illumination: Illumination
    modelled: bool = True


class ThermalHealth(BaseModel):
    rag: Rag
    temperature_c: float
    band_min_c: float  # operating band
    band_max_c: float
    modelled: bool = True


class StorageHealth(BaseModel):
    rag: Rag | None  # null without a link profile: nothing to downlink at
    fill_pct: float | None
    activity: Literal["filling", "downlinking", "full"] | None
    modelled: bool = True


class AttitudeHealth(BaseModel):
    rag: Rag | None  # null below the horizon mask: the station isn't in view to point at
    off_nadir_deg: float | None  # the station's angle off the satellite's nadir
    half_beamwidth_deg: float  # antenna coverage (the link profile's, when there is one)
    modelled: bool = True


class RfHealth(BaseModel):
    rag: Rag | None  # null: no published RF parameters
    margin_db: float | None
    modelled: bool = False  # from the published link budget


class HealthState(BaseModel):
    """Subsystem health of one satellite. Power, thermal, storage and attitude are modelled from
    generic parameters (driven by real eclipse and pass geometry); RF is the link budget.

    ``rollup``: red if any subsystem is red; otherwise null if any has no data; otherwise the
    worse of amber and green.
    """

    norad_id: int
    model_version: int  # of app/data/seed/subsystem_models.json
    rollup: Rag | None
    power: PowerHealth
    thermal: ThermalHealth
    storage: StorageHealth
    attitude: AttitudeHealth
    rf: RfHealth


ContactStatus = Literal["active", "below_mask", "no_template", "too_fast"]
ArchiveStatus = Literal["ok", "empty", "not_configured", "unavailable"]


class AutomationState(BaseModel):
    """Pass automation holds the antenna: from shortly before a booked pass's AOS until its LOS the
    station follows that satellite (the server selects it), and at AOS it checks the systems.

    ``commands``: booked for this pass (before the check) or released / held by it (after).
    ``clock_slowed``: the clock ran faster than comms can follow and was dropped to real time.
    """

    norad_id: int
    aos_time: datetime
    los_time: datetime
    max_elev_deg: float
    commands: int
    checked: bool
    clock_slowed: bool = False


class ContactState(BaseModel):
    """The station's contact with the selected satellite: the antenna follows the selection.

    ``active``: in view, with packet templates, at a clock rate comms can follow; frames flow and
    commands can be sent. ``archive`` reports the SatNOGS replay for satellites whose downlink is
    real archived frames (null otherwise).
    """

    norad_id: int
    state: ContactStatus
    downlink: Literal["simulated", "satnogs"] | None  # null without templates
    channel_modelled: bool  # a published link budget drives bit errors
    one_way_light_time_s: float | None  # null below the horizon
    archive: ArchiveStatus | None = None
    # The downlink the station listens on (the link profile's, else the seed's) and its Doppler
    # shift, received minus transmitted; null when the satellite has neither.
    downlink_frequency_hz: float | None = None
    doppler_hz: float | None = None


class SunState(BaseModel):
    """Where the Sun is overhead (WGS84), for day/night shading, and how fast that point moves in
    sim time: clients advance it by the estimated sim time between snapshots."""

    subsolar_lat_deg: float
    subsolar_lon_deg: float
    lat_rate_deg_s: float
    lon_rate_deg_s: float


class Snapshot(BaseModel):
    """Streamed satellites = (above the mask ∩ the session filter) + tracked + selected."""

    type: Literal["snapshot"] = "snapshot"
    session_id: int
    tick: int
    sim_time: datetime
    wall_time: datetime
    clock: ClockState
    observer: ObserverState
    visible_count: int  # satellites above the mask before the session filter
    satellites: list[SatelliteState]
    health: HealthState | None = None  # of the selected satellite
    contact: ContactState | None = None  # with the selected satellite
    automation: AutomationState | None = None  # while a booked pass holds the antenna
    sun: SunState | None = None


class TrackCrossing(BaseModel):
    """Where the orbit crosses this station's horizon mask, refined like a pass's AOS / LOS."""

    time: datetime
    kind: Literal["aos", "los"]
    lon_deg: float
    lat_deg: float
    alt_m: float


class GroundTrackMessage(BaseModel):
    """The orbit around the sim time, sampled evenly in time: drawn on the map and the globe."""

    type: Literal["ground_track"] = "ground_track"
    norad_id: int
    start_time: datetime
    end_time: datetime
    period_s: float
    points: list[tuple[float, float]]  # (lon_deg, lat_deg)
    # Aligned with ``points``: height above the WGS84 ellipsoid, and whether this station sees the
    # satellite above its horizon mask there (the in-view part of the orbit, drawn bright).
    alt_m: list[float] | None = None
    above_mask: list[bool] | None = None
    rag: list[Rag] | None = None  # link state at each point; null without a link profile
    # Exact mask crossings, each between the two samples whose ``above_mask`` differ; null when
    # SGP4 dropped a sample (the points are then not evenly spaced).
    crossings: list[TrackCrossing] | None = None


class PassArcMessage(BaseModel):
    """The current pass in the sky, sampled evenly in time from ``start_time`` to ``end_time``.

    The arc covers the pass clipped to a window around the sim time; ``aos_time`` / ``los_time``
    are null when the pass starts / ends outside that window (e.g. a geostationary satellite).
    """

    type: Literal["pass_arc"] = "pass_arc"
    norad_id: int
    aos_time: datetime | None
    los_time: datetime | None
    max_elev_deg: float
    start_time: datetime
    end_time: datetime
    points: list[tuple[float, float]]  # (azimuth_deg, elevation_deg)
    rag: list[Rag] | None = None  # link state at each point; null without a link profile


class UpcomingPass(BaseModel):
    norad_id: int
    name: str
    type: SatelliteType
    is_sar_capable: bool
    aos_time: datetime
    aos_az_deg: float
    max_elev_deg: float  # approximate: from the sweep's coarse time grid


class UpcomingMessage(BaseModel):
    """Satellites rising soon, filtered by the session filter, soonest first.

    ``computing``: the first sweep after a reset hasn't finished; ``paused``: the clock is faster
    than the sweep can follow, so the list is withheld rather than shown stale.
    """

    type: Literal["upcoming"] = "upcoming"
    status: Literal["computing", "ready", "paused"]
    window_start: datetime | None
    window_end: datetime | None
    total: int  # matching satellites before the ``passes`` cap
    passes: list[UpcomingPass]


class DecodedFieldOut(BaseModel):
    name: str
    value: int | float | str | None
    unit: str | None = None


class SatnogsOrigin(BaseModel):
    """Where a replayed frame really came from (ids for the public SatNOGS pages)."""

    observation_id: int | None
    station_id: int | None
    received_at: datetime | None


PacketKind = Literal["telemetry", "command", "ack", "response", "digipeat"]


class PacketMessage(BaseModel):
    """One frame on the simulated link. Uplink is never transmitted; downlink is a simulated frame
    or a real SatNOGS frame replayed. ``delivered`` false: no line of sight, or too damaged to read.
    """

    type: Literal["packet"] = "packet"
    seq: int  # per session
    norad_id: int
    direction: Literal["up", "down"]
    kind: PacketKind
    template: str
    label: str
    framing: Literal["ax25", "ccsds"]
    source: Literal["simulated", "satnogs"]
    sim_time: datetime
    size_bytes: int
    raw_hex: str  # the frame as sent (up) or received (down)
    delivered: bool
    crc_ok: bool | None  # null when nothing readable arrived
    bit_errors: int | None  # null: channel not modelled
    ber: float | None  # modelled bit error rate at this margin
    margin_db: float | None
    one_way_light_time_s: float | None
    decoded: list[DecodedFieldOut] | None  # null when the check failed or nothing arrived
    layout_sourced: bool
    command_id: int | None = None
    origin: SatnogsOrigin | None = None


# scheduled: waiting for its pass; held: the system check at AOS found a subsystem red (not sent).
CommandStatus = Literal["scheduled", "queued", "sent", "acked", "failed", "aborted", "held"]
Subsystem = Literal["power", "thermal", "storage", "attitude", "rf"]
SUBSYSTEMS: tuple[Subsystem, ...] = ("power", "thermal", "storage", "attitude", "rf")


class ScheduledPass(BaseModel):
    """The pass a command was booked for (predicted by the server)."""

    aos_time: datetime
    los_time: datetime
    max_elev_deg: float


class SystemCheck(BaseModel):
    """The automatic check at AOS: the selected satellite's subsystem health, as the inspector
    shows it. ``go``: no subsystem red (amber passes; null = no data, not checked). With no health
    at all every subsystem is null and ``go`` is false."""

    sim_time: datetime
    norad_id: int
    rollup: Rag | None
    go: bool
    subsystems: dict[Subsystem, Rag | None]


class CommandStatusMessage(BaseModel):
    type: Literal["command_status"] = "command_status"
    command_id: int
    norad_id: int
    command: str
    status: CommandStatus
    reason: str | None = None
    sim_time: datetime
    scheduled_pass: ScheduledPass | None = None  # booked for a pass (not sent live)
    check: SystemCheck | None = None  # the check at AOS that released or held it


AlarmSeverity = Literal["info", "warning", "critical"]


class AlarmMessage(BaseModel):
    """An alarm raised by pass automation (sent again when acknowledged). ``alarm_id`` numbers the
    session's alarms from 1."""

    type: Literal["alarm"] = "alarm"
    alarm_id: int
    severity: AlarmSeverity
    subsystem: Subsystem | None
    norad_id: int | None
    message: str
    sim_time: datetime
    acknowledged: bool = False


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


ServerMessage = Annotated[
    Snapshot
    | GroundTrackMessage
    | PassArcMessage
    | UpcomingMessage
    | PacketMessage
    | CommandStatusMessage
    | AlarmMessage
    | ErrorMessage,
    Field(discriminator="type"),
]

# ---------- client → server ----------

MAX_SESSION_TOKEN_LENGTH = 128


class Auth(BaseModel):
    """First message on every connection: the token ``POST /api/sessions`` returned. Sent as a
    message rather than in the URL so it never lands in proxy access logs."""

    type: Literal["auth"] = "auth"
    token: str = Field(min_length=1, max_length=MAX_SESSION_TOKEN_LENGTH)


auth_message_adapter: TypeAdapter[Auth] = TypeAdapter(Auth)


class SetTracked(BaseModel):
    """Replace the set of satellites always streamed to this session (with ground tracks)."""

    type: Literal["set_tracked"] = "set_tracked"
    norad_ids: list[NoradId] = Field(max_length=MAX_CLIENT_NORAD_IDS)


class SetSelected(BaseModel):
    """The satellite the operator has selected: always streamed, with a ground track."""

    type: Literal["set_selected"] = "set_selected"
    norad_id: NoradId | None = None


class SetFilter(BaseModel):
    """Which above-the-mask satellites (and rising-soon passes) to stream."""

    type: Literal["set_filter"] = "set_filter"
    types: list[SatelliteType] = Field(
        default_factory=lambda: list(SATELLITE_TYPES), max_length=len(SATELLITE_TYPES)
    )
    sar_only: bool = False
    # Only satellites with a link profile; of those in view, only with margin ≥ the amber threshold.
    link_ok_only: bool = False
    # Only satellites with packet templates: the ones comms works with (some are receive-only).
    comms_only: bool = False
    # Effective threshold is max(horizon mask, this). For rising-soon passes it applies to the
    # pass's (approximate) maximum elevation.
    min_elevation_deg: float = Field(default=0.0, ge=0.0, le=90.0)
    rising_within_min: int = Field(default=30, ge=0, le=MAX_RISING_WITHIN_MIN)


class SetClock(BaseModel):
    """Adjust the sim clock. Omitted fields are left unchanged."""

    type: Literal["set_clock"] = "set_clock"
    time_scale: float | None = Field(default=None, gt=0)
    paused: bool | None = None
    sim_time: AwareDatetime | None = None


ParamName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
ParamValue = StrictInt | Annotated[StrictStr, Field(max_length=80)]


class _CommandFields(BaseModel):
    """A command and its parameters: the shape is checked here, the parameters themselves against
    the command's template."""

    norad_id: NoradId
    command: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")
    params: dict[ParamName, ParamValue] = Field(default_factory=dict, max_length=8)


class SendCommand(_CommandFields):
    """Send a command to the satellite in contact (simulated uplink: nothing is transmitted)."""

    type: Literal["send_command"] = "send_command"


class ScheduleCommand(_CommandFields):
    """Book a command for a future pass of the satellite: ``aos_time`` names the pass, as the
    server's pass schedule listed it. At AOS the system check sends it unless a subsystem is red."""

    type: Literal["schedule_command"] = "schedule_command"
    aos_time: AwareDatetime


class CancelCommand(BaseModel):
    """Cancel a command still waiting for its pass."""

    type: Literal["cancel_command"] = "cancel_command"
    command_id: int = Field(ge=1)


class AckAlarm(BaseModel):
    type: Literal["ack_alarm"] = "ack_alarm"
    alarm_id: int = Field(ge=1)


ClientCommand = (
    SetTracked
    | SetSelected
    | SetFilter
    | SetClock
    | SendCommand
    | ScheduleCommand
    | CancelCommand
    | AckAlarm
)
ClientMessage = Annotated[ClientCommand, Field(discriminator="type")]
client_message_adapter: TypeAdapter[ClientCommand] = TypeAdapter(ClientMessage)

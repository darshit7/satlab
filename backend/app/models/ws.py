"""WebSocket contract. Mirrored in frontend/src/types/ws.ts — change both in lockstep.

Server → client: ``snapshot`` (every tick), ``ground_track`` and ``pass_arc`` (when (re)computed),
``upcoming`` (when the rising-soon list changes), ``error``.
Client → server: ``set_tracked``, ``set_selected``, ``set_filter``, ``set_clock``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter

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

# ---------- server → client ----------


class ClockState(BaseModel):
    time_scale: float
    paused: bool


class ObserverState(BaseModel):
    station_id: int
    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    horizon_mask_deg: float


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


class GroundTrackMessage(BaseModel):
    type: Literal["ground_track"] = "ground_track"
    norad_id: int
    start_time: datetime
    end_time: datetime
    period_s: float
    points: list[tuple[float, float]]  # (lon_deg, lat_deg)


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


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


ServerMessage = Annotated[
    Snapshot | GroundTrackMessage | PassArcMessage | UpcomingMessage | ErrorMessage,
    Field(discriminator="type"),
]

# ---------- client → server ----------


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


ClientMessage = Annotated[
    SetTracked | SetSelected | SetFilter | SetClock, Field(discriminator="type")
]
client_message_adapter: TypeAdapter[SetTracked | SetSelected | SetFilter | SetClock] = TypeAdapter(
    ClientMessage
)

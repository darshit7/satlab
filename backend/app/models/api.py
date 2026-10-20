"""REST request/response schemas. Mirrored in frontend/src/types/api.ts."""

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class GroundStationOut(BaseModel):
    """A preset station (seed data). Custom locations are per session, never stations."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    created_at: datetime


class LocationIn(BaseModel):
    """A custom observer location, used for one session only."""

    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    alt_m: float = Field(default=0.0, ge=-500, le=9000)


class SatelliteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    norad_id: int
    name: str
    type: str | None
    is_sar_capable: bool
    tle_epoch: datetime | None
    fetched_at: datetime | None


class SatelliteSearchResult(BaseModel):
    query: str
    remote_searched: bool
    results: list[SatelliteOut]


class CatalogStatus(BaseModel):
    satellite_count: int
    group: str
    last_fetched_at: datetime | None
    refreshing: bool
    last_error: str | None


class MissionSessionCreate(BaseModel):
    """Observe from a preset station (``station_id``) or a custom ``location``: exactly one."""

    station_id: int | None = None
    location: LocationIn | None = None
    name: str | None = Field(default=None, max_length=120)
    sim_start_time: AwareDatetime | None = None  # defaults to "now"
    time_scale: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _one_site(self) -> Self:
        if (self.station_id is None) == (self.location is None):
            raise ValueError("give exactly one of station_id or location")
        return self


class MissionSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int | None  # the preset the location came from, if any
    location_name: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    name: str | None
    started_at: datetime
    ended_at: datetime | None
    sim_start_time: datetime
    time_scale: float


class MissionSessionCreated(MissionSessionOut):
    """Returned once, by ``POST /api/sessions``. ``token`` proves ownership of the session: send
    it as ``X-Session-Token`` on the session's REST endpoints and in the WebSocket ``auth``
    message. It is not stored server-side (only its hash) and cannot be fetched again."""

    token: str


class PassWindowOut(BaseModel):
    aos_time: datetime
    los_time: datetime
    max_time: datetime
    max_elev_deg: float
    aos_az_deg: float
    los_az_deg: float
    duration_s: float


class PassPrediction(BaseModel):
    session_id: int
    station_id: int | None
    norad_id: int
    name: str
    tle_epoch: datetime
    horizon_mask_deg: float
    start: datetime
    end: datetime
    always_visible: bool  # above the mask for the whole window (e.g. geostationary)
    passes: list[PassWindowOut]


class LinkSourceOut(BaseModel):
    title: str
    url: str


class LinkProfileOut(BaseModel):
    """A satellite's published downlink budget, as the link state is computed from it."""

    norad_id: int
    satellite_name: str
    label: str
    frequency_hz: float
    eirp_dbw: float
    station_gt_db_k: float
    system_loss_db: float
    atmospheric_loss_db: float  # at atmospheric_ref_elev_deg, scaled by the cosecant law
    atmospheric_ref_elev_deg: float
    required_ebn0_db: float
    data_rate_bps: float
    coverage_half_angle_deg: float | None
    sources: list[LinkSourceOut]


class LinkProfilesOut(BaseModel):
    """Every link profile in use, with the margin thresholds that map margins to RAG."""

    green_margin_db: float  # green strictly above
    amber_margin_db: float  # amber from here up to green; red below (or below the mask)
    profiles: list[LinkProfileOut]

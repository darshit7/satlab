"""REST request/response schemas. Mirrored in frontend/src/types/api.ts."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class GroundStationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    alt_m: float = Field(default=0.0, ge=-500, le=9000)


class GroundStationOut(GroundStationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


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
    station_id: int
    name: str | None = None
    sim_start_time: AwareDatetime | None = None  # defaults to "now"
    time_scale: float = Field(default=1.0, gt=0)


class MissionSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int
    name: str | None
    started_at: datetime
    ended_at: datetime | None
    sim_start_time: datetime
    time_scale: float
    station: GroundStationOut


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
    station_id: int
    norad_id: int
    name: str
    tle_epoch: datetime
    horizon_mask_deg: float
    start: datetime
    end: datetime
    always_visible: bool  # above the mask for the whole window (e.g. geostationary)
    passes: list[PassWindowOut]

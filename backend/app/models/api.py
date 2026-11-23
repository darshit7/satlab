"""REST request/response schemas. Mirrored in frontend/src/types/api.ts."""

from datetime import date, datetime
from typing import Annotated, Literal, Self

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
    """A catalogued satellite: name, type and the epoch of the TLE the simulation uses."""

    model_config = ConfigDict(from_attributes=True)

    norad_id: int
    name: str
    type: str | None
    is_sar_capable: bool
    tle_epoch: datetime | None
    fetched_at: datetime | None


class SatelliteSearchResult(BaseModel):
    """Catalog search results; ``remote_searched`` when Celestrak was asked too (by name or
    NORAD id, only on explicit request)."""

    query: str
    remote_searched: bool
    results: list[SatelliteOut]


class CatalogStatus(BaseModel):
    """The catalog's size and freshness: when the Celestrak group was last fetched, and whether
    a refresh is running or last failed."""

    satellite_count: int
    group: str
    last_fetched_at: datetime | None  #: when Celestrak last served (or confirmed) the group
    refreshing: bool
    last_error: str | None
    max_age_hours: float | None = None  #: the group is fetched again once it's this old


class MissionSessionCreate(BaseModel):
    """Observe from a preset station (``station_id``) or a custom ``location``: exactly one."""

    station_id: int | None = None
    location: LocationIn | None = None
    name: str | None = Field(default=None, max_length=120)
    sim_start_time: AwareDatetime | None = None  #: defaults to "now"
    time_scale: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _one_site(self) -> Self:
        if (self.station_id is None) == (self.location is None):
            raise ValueError("give exactly one of station_id or location")
        return self


class MissionSessionOut(BaseModel):
    """A mission session: where it observes from (its own copy of the location) and its clock."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int | None  #: the preset the location came from, if any
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
    """One pass over the session's location: AOS, culmination and LOS, with their azimuths."""

    aos_time: datetime
    los_time: datetime
    max_time: datetime
    max_elev_deg: float
    aos_az_deg: float
    los_az_deg: float
    duration_s: float


class PassPrediction(BaseModel):
    """A satellite's passes over the session's location in a time window (the 24 h schedule)."""

    session_id: int
    station_id: int | None
    norad_id: int
    name: str
    tle_epoch: datetime
    horizon_mask_deg: float
    start: datetime
    end: datetime
    always_visible: bool  #: above the mask for the whole window (e.g. geostationary)
    passes: list[PassWindowOut]


class LinkSourceOut(BaseModel):
    """A cited source (title and https URL) for seeded reference data."""

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
    atmospheric_loss_db: float  #: at atmospheric_ref_elev_deg, scaled by the cosecant law
    atmospheric_ref_elev_deg: float
    required_ebn0_db: float
    data_rate_bps: float
    coverage_half_angle_deg: float | None
    sources: list[LinkSourceOut]


class LinkProfilesOut(BaseModel):
    """Every link profile in use, with the margin thresholds that map margins to RAG."""

    green_margin_db: float  #: green strictly above
    amber_margin_db: float  #: amber from here up to green; red below (or below the mask)
    profiles: list[LinkProfileOut]


class CommandParamOut(BaseModel):
    """One parameter of a command template, with the bounds the server checks it against."""

    name: str
    label: str
    type: Literal["int", "text"]
    min: int | None
    max: int | None
    max_length: int
    charset: Literal["printable", "callsign"]


class TelemetryTemplateOut(BaseModel):
    """A telemetry frame a satellite sends: its framing, cadence and where its frames come from."""

    name: str
    label: str
    framing: Literal["ax25", "ccsds"]
    interval_s: float | None  #: null: sent only in answer to a command
    downlink: Literal["simulated", "satnogs"]
    layout_sourced: bool  #: false: an illustrative layout
    sources: list[LinkSourceOut]


class CommandTemplateOut(BaseModel):
    """A command the simulated uplink can send, its parameters and what answers it."""

    name: str
    label: str
    framing: Literal["ax25", "ccsds"]
    params: list[CommandParamOut]
    response: Literal["ack", "telemetry", "digipeat"]
    layout_sourced: bool
    sources: list[LinkSourceOut]


class SatelliteCommsOut(BaseModel):
    """A satellite's packet templates: what can be received from it and sent to it."""

    norad_id: int
    satellite_name: str
    telemetry: list[TelemetryTemplateOut]
    commands: list[CommandTemplateOut]  #: empty: receive-only (no published command set)
    #: Seeded downlink frequency; null for satellites with a link profile, whose frequency is the
    #: profile's (/api/link-profiles).
    downlink_frequency_hz: float | None = None
    downlink_sources: list[LinkSourceOut] = []  #: where the seeded frequency comes from


class GenericCommsOut(BaseModel):
    """The generic packet templates: what every satellite without its own uses (debris and
    rocket bodies excepted). Illustrative layout, modelled values."""

    telemetry: list[TelemetryTemplateOut]
    commands: list[CommandTemplateOut]
    #: The TT&C antenna coverage it assumes, off nadir (90: omnidirectional); null: the subsystem
    #: model's default.
    antenna_half_angle_deg: float | None


class PacketTemplatesOut(BaseModel):
    """Every satellite with packet templates of its own, and the generic set for the rest
    (reference data, like the link profiles)."""

    satnogs_configured: bool  #: whether real frames can be replayed at all
    satellites: list[SatelliteCommsOut]  #: the satellites with their own formats
    generic: GenericCommsOut | None = None  #: null: only the listed satellites can be contacted


# ---------- SAR imagery (Phase 7a) ----------

#: The polarizations a comparison can use.
SarPolarization = Literal["VV", "VH"]
#: Sides of the square area offered (km).
AoiSizeKm = Literal[5, 10, 20]
#: Where a comparison is.
SarStatus = Literal["queued", "reading", "detecting", "ready", "failed", "expired"]
#: A STAC item id, as clients may send it.
StacId = Annotated[str, Field(pattern=r"^[_.A-Za-z0-9-]{1,128}$")]
#: A lon/lat pair.
LonLat = tuple[float, float]


class SarAoiIn(BaseModel):
    """The area of interest: a square of ``size_km`` around a centre."""

    lat_deg: float = Field(ge=-80, le=84)  #: centre latitude (UTM's range)
    lon_deg: float = Field(ge=-180, le=180)  #: centre longitude
    size_km: AoiSizeKm = 10  #: side of the square


class SarAoiOut(SarAoiIn):
    """The area as the server gridded it."""

    bbox: tuple[float, float, float, float]  #: min lon, min lat, max lon, max lat
    polygon: list[LonLat]  #: the exact UTM square, a closed lon/lat ring
    epsg: int  #: its UTM zone
    width_px: int  #: grid pixels
    height_px: int  #: grid pixels
    resolution_m: float  #: pixel size


class SarPassOut(BaseModel):
    """One satellite pass over the area: one or more scene frames, mosaicked."""

    id: str  #: the pass's best-covering frame (its STAC id)
    frames: list[str]  #: every frame read for the area, best-covering first
    platform: str  #: sentinel-1a … sentinel-1d
    satellite_name: str  #: e.g. SENTINEL-1C
    norad_id: int | None  #: the catalog satellite, when it is in the catalog
    acquired_at: datetime  #: the pass's first frame
    orbit_state: Literal["ascending", "descending"]  #: direction of the pass
    relative_orbit: int  #: with the direction, the track
    polarizations: list[str]  #: those all its frames have
    coverage: float  #: share of the area covered (0-1): estimated from footprints in a search


class SarTrackOut(BaseModel):
    """The passes of one track around each date (only same-track passes are compared)."""

    key: str  #: e.g. "110-descending"
    relative_orbit: int  #: the relative orbit
    orbit_state: Literal["ascending", "descending"]  #: the direction
    before: list[SarPassOut]  #: around the "before" date, closest first
    after: list[SarPassOut]  #: around the "after" date, closest first
    proposed_before: str | None  #: the proposed pair on this track (pass ids), if any
    proposed_after: str | None


class SarSearchOut(BaseModel):
    """Passes over the area around two dates, by track, with the pair closest to both."""

    aoi: SarAoiOut  #: the gridded area
    polarization: SarPolarization  #: passes listed have it
    platform: str | None  #: only this satellite, when filtered
    before_window: tuple[date, date]  #: days searched around the "before" date
    after_window: tuple[date, date]  #: days searched around the "after" date
    tracks: list[SarTrackOut]  #: the proposed track first
    proposed_track: str | None  #: its key; null: no same-track pair in the windows
    skipped: int = 0  #: STAC items dropped as malformed or unusable
    truncated: bool = False  #: a window hit the result limit: there may be more passes


class SarComparisonCreate(BaseModel):
    """Start a comparison of two passes on the same track."""

    aoi: SarAoiIn  #: the area
    polarization: SarPolarization = "VV"  #: the band compared
    before: list[StacId] = Field(min_length=1, max_length=4)  #: frames of the earlier pass
    after: list[StacId] = Field(min_length=1, max_length=4)  #: frames of the later pass
    requested_before: date  #: the dates asked for (shown with the result)
    requested_after: date

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.requested_before >= self.requested_after:
            raise ValueError("the before date must be earlier than the after date")
        if set(self.before) & set(self.after):
            raise ValueError("the two passes must differ")
        return self


class SarRegionOut(BaseModel):
    """A flagged region: connected pixels whose backscatter changed past the threshold."""

    id: int  #: 1 = largest score
    sign: Literal["increase", "decrease"]  #: brighter or darker after
    area_m2: float  #: flagged area
    mean_db: float  #: mean change (dB)
    max_abs_db: float  #: largest ``|change|`` in it (dB)
    polygon: list[LonLat]  #: its bounding box as a lon/lat ring
    bbox: tuple[float, float, float, float]  #: min lon, min lat, max lon, max lat
    centroid: LonLat  #: centre of mass
    px: tuple[int, int, int, int]  #: col0, row0, col1, row1 on the grid (images' pixels)


class SarDisplayOut(BaseModel):
    """How the images were drawn."""

    db_min: float  #: black in before/after
    db_max: float  #: white
    heatmap_max_db: float  #: ``|change|`` at full heatmap strength


class SarComparisonOut(BaseModel):
    """A comparison, its status and, once ready, its result."""

    id: int
    status: SarStatus
    queue_position: int | None = None  #: 1 = next; null when not queued
    error: str | None = None  #: why it failed (a fixed message)
    aoi: SarAoiOut
    polarization: SarPolarization
    before: SarPassOut
    after: SarPassOut
    requested_before: date
    requested_after: date
    method: Literal["log_ratio"]
    params_version: int  #: of the change-detection parameters
    generation: int  #: run number; image URLs change with it
    coverage: float | None = None  #: valid on both dates (0-1), measured
    otsu_db: float | None = None  #: Otsu's threshold on ``|change|``
    threshold_db: float | None = None  #: applied: max(Otsu, floor)
    floor_db: float  #: the floor
    changed_fraction: float | None = None  #: flagged share of valid pixels
    increase_area_m2: float | None = None
    decrease_area_m2: float | None = None
    regions: list[SarRegionOut] = []
    regions_truncated: bool = False  #: more regions than listed
    widespread: bool = False  #: a large share changed: read with caution
    display: SarDisplayOut
    attribution: str  #: e.g. "Contains modified Copernicus Sentinel data 2024"
    sim_time: datetime  #: the session's clock when it was asked for
    created_at: datetime
    finished_at: datetime | None = None


class SarComparisonList(BaseModel):
    """A session's comparisons, newest first."""

    comparisons: list[SarComparisonOut]
    max_per_session: int  #: how many a session can keep
    running: int  #: queued or running, all sessions


class SarPlatformOut(BaseModel):
    """A Sentinel-1 satellite present in the catalog."""

    platform: str  #: sentinel-1c
    satellite_name: str  #: SENTINEL-1C
    norad_id: int


class SarLimitsOut(BaseModel):
    """Budgets a session works within."""

    searches_per_min: int
    comparisons_per_hour: int
    max_per_session: int
    aoi_sizes_km: list[int]


class SarSourceOut(BaseModel):
    """The archive, its terms and the method (reference data; no upstream call)."""

    enabled: bool  #: false: archive search is off on this server
    status: Literal["ok", "unavailable", "unknown"]  #: the last upstream outcome
    collection: str
    title: str
    license: str
    license_url: str
    dataset_url: str
    attribution: list[str]  #: lines to show with any image
    archive_start: date
    search_window_days: int  #: ± days searched around each date
    resolution_m: float
    polarizations: list[str]
    params_version: int
    speckle_window_px: int
    min_abs_db: float  #: the threshold's floor
    min_area_m2: float
    display: dict[str, SarDisplayOut]  #: per polarization
    limits: SarLimitsOut
    platforms: list[SarPlatformOut]  #: Sentinel-1 satellites in the catalog

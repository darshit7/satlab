"""Application settings, loaded from environment / `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
VAR_DIR = BACKEND_DIR / "var"

_POSTGRES_SCHEMES = ("postgres://", "postgresql://")


def normalize_database_url(url: str) -> str:
    """Route bare Postgres URLs (as Neon hands them out) to the installed psycopg 3 driver."""
    for scheme in _POSTGRES_SCHEMES:
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url.removeprefix(scheme)
    return url


class Settings(BaseSettings):
    """Every setting, read from ``SATLAB_*`` environment variables or a ``.env`` file.

    Group comments (``# Title. Description.``) and the ``#:`` doc-comment on each field are the
    text of the settings reference in the docs, which is generated from this class.
    """

    model_config = SettingsConfigDict(
        env_prefix="SATLAB_",
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # Database and runtime files.
    #: The app's database: SQLite by default (zero setup); Postgres (Neon) for persistent and
    #: production use, as the least-privilege role on the pooled host.
    database_url: str = f"sqlite:///{VAR_DIR / 'satlab.db'}"
    #: Alembic only: the schema owner, on Neon's direct (non-pooled) host. Defaults to database_url.
    migration_database_url: str | None = None
    auto_migrate: bool = True  #: apply Alembic migrations at startup (off in production)
    cache_dir: Path = VAR_DIR / "cache"  #: Celestrak and SatNOGS downloads, kept between runs

    # HTTP surface.
    #: Browser origins allowed to call the API and open WebSockets (CORS + the WS Origin check).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    docs_enabled: bool = True  #: FastAPI's /docs, /redoc, /openapi.json; off in production

    # Public-exposure limits. What one client can make the server do (AGENTS.md §12.3).
    max_active_sessions: int = Field(default=10, ge=1)  #: each one propagates the whole catalog
    #: How long a new WebSocket has to send its ``auth`` message before it's closed (4401).
    ws_auth_timeout_s: float = Field(default=5.0, gt=0, le=60)
    ws_messages_per_s: float = Field(default=20.0, gt=0)  #: per connection, sustained
    ws_message_burst: int = Field(default=40, ge=1)  #: per connection, before the rate applies
    #: Celestrak searches and single-satellite fetches, all clients together.
    remote_search_per_min: int = Field(default=10, ge=1)

    # Celestrak catalog. GP data, https://celestrak.org/NORAD/documentation/gp-data-formats.php;
    # it updates every 2 hours and refuses a repeat download before then (HTTP 403).
    celestrak_base_url: str = "https://celestrak.org/NORAD/elements/gp.php"  #: the GP endpoint
    catalog_group: str = "active"  #: the group bulk-loaded into the catalog
    catalog_autoload: bool = True  #: keep the catalog fresh in the background (off: DB only)
    #: Refetch the group once its copy is older than this; at least Celestrak's 2-hour update
    #: interval (an earlier download is refused).
    catalog_max_age_hours: float = Field(default=12.0, ge=2.0)
    #: How often the catalog's age is checked.
    catalog_refresh_check_minutes: float = Field(default=30.0, ge=5.0)
    #: The largest Celestrak response read (streamed; the "active" group is about 2.4 MB).
    celestrak_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024 * 1024)
    http_timeout_s: float = 30.0  #: for every upstream request (Celestrak, SatNOGS, STAC)

    # Simulation loop. One loop per session; these bound its work per tick.
    tick_hz: float = 2.0  #: snapshots per second of wall time
    max_tracked: int = 25  #: satellites a session can track at once (beyond the ones in view)
    max_time_scale: float = 3600.0  #: fastest the sim clock may run, as a multiple of real time
    ground_track_points: int = 240  #: samples per ground track (half an orbit back, one ahead)
    session_idle_grace_s: float = 60.0  #: a session with no subscriber ends after this

    # Visibility and passes. The mask defines "in view", AOS and LOS for every station.
    horizon_mask_deg: float = Field(default=5.0, ge=0.0, le=45.0)  #: elevation of the mask
    #: Coarse elevation grid of the pass search, before culmination and AOS/LOS are refined.
    pass_search_step_s: float = Field(default=30.0, gt=0, le=120)
    pass_arc_points: int = Field(default=48, ge=8, le=256)  #: samples per sky arc
    max_arcs_per_tick: int = Field(default=100, ge=1)  #: sky arcs (re)computed per tick at most

    # Rising-soon sweep. Over the whole catalog, sliced into chunks (sgp4 holds the GIL).
    upcoming_window_min: float = Field(default=90.0, ge=70.0, le=360.0)  #: how far ahead it looks
    upcoming_step_s: float = Field(default=60.0, gt=0, le=300)  #: its elevation grid
    upcoming_chunk_size: int = Field(default=500, ge=1)  #: satellites swept per tick
    upcoming_max_time_scale: float = 60.0  #: above this the sweep can't keep up: list is paused
    upcoming_limit: int = Field(default=100, ge=1, le=500)  #: soonest passes sent to the client

    # Link budget and health. Link margin → RAG (AGENTS.md §7): green above the green margin,
    # amber from the amber margin up to it, red below (or below the horizon mask).
    link_green_margin_db: float = Field(default=6.0, ge=0.0, le=30.0)  #: green above this margin
    link_amber_margin_db: float = Field(default=0.0, ge=-10.0, le=10.0)  #: amber from this up
    #: Subsystem health is integrated over this much sim time, from a fixed start state.
    subsystem_lookback_h: float = Field(default=24.0, ge=1.0, le=48.0)
    subsystem_step_s: float = Field(default=60.0, ge=5.0, le=300.0)  #: the integration grid

    # Communications. Simulated uplink; downlink simulated or replayed from SatNOGS.
    comms_max_time_scale: float = Field(default=60.0, gt=0)  #: above this the contact pauses
    comms_max_packets_per_tick: int = Field(default=4, ge=1, le=20)  #: downlink frames per tick
    commands_per_min: int = Field(default=30, ge=1, le=600)  #: per session
    #: Stored commands per session (the public deploy's storage bound).
    max_commands_per_session: int = Field(default=500, ge=1)
    #: Stored frames per session: past the cap, frames still stream but aren't stored.
    max_telemetry_frames_per_session: int = Field(default=5000, ge=0)
    #: Comms rows and SAR comparisons of a session are deleted this long after it ended.
    comms_retention_days: float = Field(default=7.0, gt=0)

    # Pass automation. Commands booked for a future pass, sent at AOS if no subsystem is red.
    schedule_lead_s: float = Field(default=60.0, ge=10.0, le=600.0)  #: antenna moves before AOS
    schedule_horizon_h: float = Field(default=24.0, gt=0, le=72.0)  #: how far ahead to book
    max_scheduled_commands: int = Field(default=20, ge=1, le=200)  #: waiting, per session

    # SatNOGS DB. Real archived frames; without a token, replay is off and the UI says so.
    satnogs_db_url: str = "https://db.satnogs.org/api/telemetry/"  #: the telemetry endpoint
    #: SatNOGS DB API token (sent only as ``Authorization: Token``); unset disables replay.
    satnogs_api_token: SecretStr | None = None
    satnogs_refresh_hours: float = Field(default=6.0, ge=1.0)  #: refetch a satellite's frames
    satnogs_max_frames: int = Field(default=200, ge=1, le=1000)  #: kept per satellite
    satnogs_max_pages: int = Field(default=5, ge=1, le=20)  #: per refresh and satellite
    satnogs_requests_per_min: int = Field(default=6, ge=1, le=60)  #: all satellites together

    # SAR imagery. Sentinel-1 RTC scenes from Microsoft Planetary Computer: found over STAC, read
    # as bounded windows by one worker process and compared on the server (AGENTS.md §12.4).
    sar_enabled: bool = True  #: off: no archive search, and the Imagery panel says so
    #: The STAC API root (https, on Planetary Computer's host only).
    sar_stac_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    #: The SAS token endpoint; the collection id is appended.
    sar_token_url: str = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
    #: Optional Planetary Computer subscription key (``Ocp-Apim-Subscription-Key``): only relaxes
    #: rate limits; anonymous tokens read the collection too.
    sar_subscription_key: SecretStr | None = None
    #: The only Azure storage account scene files are read from.
    sar_blob_account: str = Field(default="sentinel1euwestrtc", pattern=r"^[a-z0-9]{3,24}$")
    #: The only container scene files are read from.
    sar_blob_container: str = Field(
        default="sentinel1-grd-rtc", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
    )
    #: Scene crops and comparison images: an ephemeral cache, recomputed when missing.
    sar_storage_dir: Path = VAR_DIR / "sar"
    #: Past this, the least recently used crops and comparison images are deleted.
    sar_storage_max_bytes: int = Field(default=1024**3, ge=64 * 1024**2)
    #: The largest STAC or token response read (streamed).
    sar_stac_max_bytes: int = Field(default=4 * 1024**2, ge=256 * 1024, le=64 * 1024**2)
    #: STAC and token requests, all clients together.
    sar_upstream_requests_per_min: int = Field(default=30, ge=1, le=600)
    #: A search window's results are reused for this long.
    sar_search_cache_minutes: float = Field(default=15.0, ge=1.0, le=1440.0)
    sar_searches_per_min: int = Field(default=6, ge=1, le=60)  #: per session
    #: Comparisons started per session and hour, recomputes included.
    sar_comparisons_per_hour: int = Field(default=6, ge=1, le=120)
    #: Comparisons started per hour, all sessions together.
    sar_comparisons_per_hour_total: int = Field(default=30, ge=1, le=1000)
    #: Comparisons kept in a session's list.
    sar_max_comparisons_per_session: int = Field(default=12, ge=1, le=100)
    #: Comparisons waiting for the worker, all sessions together.
    sar_queue_max: int = Field(default=4, ge=1, le=50)
    #: One comparison, scene reads included; past it the worker is stopped.
    sar_job_timeout_s: float = Field(default=240.0, ge=30.0, le=1800.0)
    sar_read_timeout_s: float = Field(default=30.0, ge=5.0, le=120.0)  #: each GDAL HTTP request
    sar_worker_nice: int = Field(
        default=10, ge=0, le=19
    )  #: the worker's CPU priority below the API
    sar_gdal_cache_mb: int = Field(default=64, ge=16, le=512)  #: GDAL's block cache in the worker

    @model_validator(mode="after")
    def _check_link_and_health(self) -> Self:
        if self.link_green_margin_db <= self.link_amber_margin_db:
            raise ValueError("link_green_margin_db must be above link_amber_margin_db")
        if self.subsystem_lookback_h * 3600 / self.subsystem_step_s > 20_000:
            raise ValueError("subsystem_lookback_h / subsystem_step_s: too many grid points")
        for url in (self.sar_stac_url, self.sar_token_url):
            if not url.startswith("https://"):
                raise ValueError("SAR upstream URLs must be https")
        if self.sar_storage_dir.resolve().is_relative_to(BACKEND_DIR / "app"):
            raise ValueError("sar_storage_dir must not be inside the application code")
        return self

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _normalize_database_url(cls, url: str | None) -> str | None:
        return normalize_database_url(url) if url is not None else None


@lru_cache
def get_settings() -> Settings:
    """The process's settings, read once (from the environment and ``.env``) and cached."""
    return Settings()

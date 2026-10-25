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
    model_config = SettingsConfigDict(
        env_prefix="SATLAB_",
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # SQLite by default (zero setup); Postgres (Neon) for persistent / production use.
    database_url: str = f"sqlite:///{VAR_DIR / 'satlab.db'}"
    # Alembic only: the schema owner, on Neon's direct (non-pooled) host. Defaults to database_url.
    migration_database_url: str | None = None
    auto_migrate: bool = True
    cache_dir: Path = VAR_DIR / "cache"

    # Browser origins allowed to call the API and open WebSockets (CORS + the WS Origin check).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    docs_enabled: bool = True  # /docs, /redoc, /openapi.json; off in production

    # Public-exposure limits (AGENTS.md §12.3).
    max_active_sessions: int = Field(default=10, ge=1)  # each one propagates the whole catalog
    ws_auth_timeout_s: float = Field(default=5.0, gt=0, le=60)
    ws_messages_per_s: float = Field(default=20.0, gt=0)
    ws_message_burst: int = Field(default=40, ge=1)
    remote_search_per_min: int = Field(default=10, ge=1)  # Celestrak queries, all clients together

    # Celestrak GP data (https://celestrak.org/NORAD/documentation/gp-data-formats.php).
    # Celestrak asks clients not to re-download a group more than once every ~2 hours.
    celestrak_base_url: str = "https://celestrak.org/NORAD/elements/gp.php"
    catalog_group: str = "active"
    catalog_autoload: bool = True
    catalog_max_age_hours: float = 12.0
    catalog_refresh_check_minutes: float = 30.0
    http_timeout_s: float = 30.0

    # Simulation loop
    tick_hz: float = 2.0
    max_tracked: int = 25
    max_time_scale: float = 3600.0
    ground_track_points: int = 240
    session_idle_grace_s: float = 60.0

    # Visibility & passes. The mask defines "in view", AOS and LOS for every station.
    horizon_mask_deg: float = Field(default=5.0, ge=0.0, le=45.0)
    pass_search_step_s: float = Field(default=30.0, gt=0, le=120)
    pass_arc_points: int = Field(default=48, ge=8, le=256)
    max_arcs_per_tick: int = Field(default=100, ge=1)
    # Rising-soon sweep over the whole catalog, sliced into chunks (sgp4 holds the GIL).
    upcoming_window_min: float = Field(default=90.0, ge=70.0, le=360.0)
    upcoming_step_s: float = Field(default=60.0, gt=0, le=300)
    upcoming_chunk_size: int = Field(default=500, ge=1)
    upcoming_max_time_scale: float = 60.0  # above this the sweep can't keep up: list is paused
    upcoming_limit: int = Field(default=100, ge=1, le=500)

    # Link budget → RAG (AGENTS.md §7): green above `green`, amber from `amber` up to it, red below
    # (or below the horizon mask).
    link_green_margin_db: float = Field(default=6.0, ge=0.0, le=30.0)
    link_amber_margin_db: float = Field(default=0.0, ge=-10.0, le=10.0)
    # Subsystem health is integrated over this much sim time, on this grid, from a fixed start.
    subsystem_lookback_h: float = Field(default=24.0, ge=1.0, le=48.0)
    subsystem_step_s: float = Field(default=60.0, ge=5.0, le=300.0)

    # Communications (simulated uplink; downlink simulated or replayed from SatNOGS).
    comms_max_time_scale: float = Field(default=60.0, gt=0)  # above this the contact pauses
    comms_max_packets_per_tick: int = Field(default=4, ge=1, le=20)
    commands_per_min: int = Field(default=30, ge=1, le=600)  # per session
    # Storage bounds on the public deploy: past the cap, frames still stream but aren't stored.
    max_commands_per_session: int = Field(default=500, ge=1)
    max_telemetry_frames_per_session: int = Field(default=5000, ge=0)
    comms_retention_days: float = Field(default=7.0, gt=0)  # after the session ended

    # Pass automation: commands booked for a future pass, sent at AOS if no subsystem is red.
    schedule_lead_s: float = Field(default=60.0, ge=10.0, le=600.0)  # antenna moves before AOS
    schedule_horizon_h: float = Field(default=24.0, gt=0, le=72.0)  # how far ahead a pass is booked
    max_scheduled_commands: int = Field(default=20, ge=1, le=200)  # waiting, per session

    # SatNOGS DB (real archived frames). Without a token, replay is off and the UI says so.
    satnogs_db_url: str = "https://db.satnogs.org/api/telemetry/"
    satnogs_api_token: SecretStr | None = None
    satnogs_refresh_hours: float = Field(default=6.0, ge=1.0)
    satnogs_max_frames: int = Field(default=200, ge=1, le=1000)  # kept per satellite
    satnogs_max_pages: int = Field(default=5, ge=1, le=20)  # per refresh and satellite
    satnogs_requests_per_min: int = Field(default=6, ge=1, le=60)  # all satellites together

    @model_validator(mode="after")
    def _check_link_and_health(self) -> Self:
        if self.link_green_margin_db <= self.link_amber_margin_db:
            raise ValueError("link_green_margin_db must be above link_amber_margin_db")
        if self.subsystem_lookback_h * 3600 / self.subsystem_step_s > 20_000:
            raise ValueError("subsystem_lookback_h / subsystem_step_s: too many grid points")
        return self

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _normalize_database_url(cls, url: str | None) -> str | None:
        return normalize_database_url(url) if url is not None else None


@lru_cache
def get_settings() -> Settings:
    return Settings()

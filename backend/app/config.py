"""Application settings, loaded from environment / `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
VAR_DIR = BACKEND_DIR / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SATLAB_",
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    database_url: str = f"sqlite:///{VAR_DIR / 'satlab.db'}"
    auto_migrate: bool = True
    cache_dir: Path = VAR_DIR / "cache"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

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


@lru_cache
def get_settings() -> Settings:
    return Settings()

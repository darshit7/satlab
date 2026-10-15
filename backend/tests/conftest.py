from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import BACKEND_DIR, get_settings, normalize_database_url
from app.data.celestrak import TleRecord
from app.db.base import get_engine, get_sessionmaker


class _TestDatabase(BaseSettings):
    """The Postgres the suite runs on: the Neon *test* branch's direct URL (the pooler rejects the
    search_path option). From the shell or a gitignored `.env.test`, never `.env`, so dev
    credentials can't drive the tests. Unset: throwaway SQLite."""

    model_config = SettingsConfigDict(
        env_prefix="SATLAB_TEST_",
        env_file=(BACKEND_DIR.parent / ".env.test", BACKEND_DIR / ".env.test"),
        extra="ignore",
    )

    postgres_url: str | None = None


TEST_POSTGRES_URL = _TestDatabase().postgres_url or None


def pytest_configure(config: pytest.Config) -> None:
    # Neon's pooler (PgBouncer) rejects the search_path startup option every test relies on.
    host = make_url(normalize_database_url(TEST_POSTGRES_URL)).host if TEST_POSTGRES_URL else None
    if host and "-pooler." in host:
        raise pytest.UsageError(
            f"SATLAB_TEST_POSTGRES_URL uses Neon's pooled host ({host}). The tests need the "
            "direct host: remove '-pooler' from the hostname (see deploy/neon.md)."
        )


def pytest_report_header() -> str:
    if TEST_POSTGRES_URL:
        url = make_url(normalize_database_url(TEST_POSTGRES_URL))
        return f"database: Postgres at {url.host} (one throwaway schema per test)"
    return "database: SQLite (throwaway file per test)"


# ISS element set and reference observation from the skyfield documentation
# (https://rhodesmill.org/skyfield/earth-satellites.html).
ISS_NAME = "ISS (ZARYA)"
ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
ISS_RECORD = TleRecord(25544, ISS_NAME, ISS_LINE1, ISS_LINE2)
BLUFFTON = (40.8939, -83.8917)  # lat_deg, lon_deg
REFERENCE_TIME = datetime(2014, 1, 23, 11, 18, 7, tzinfo=UTC)


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@contextmanager
def _postgres_schema(base_url: str) -> Iterator[str]:
    """A throwaway schema on the test Postgres; the yielded URL sees only it (search_path)."""
    url = make_url(normalize_database_url(base_url))
    schema = f"satlab_test_{uuid4().hex[:12]}"
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
    try:
        scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
        yield scoped.render_as_string(hide_password=False)
    finally:
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@contextmanager
def _test_database(tmp_path: Path) -> Iterator[str]:
    if TEST_POSTGRES_URL:
        with _postgres_schema(TEST_POSTGRES_URL) as url:
            yield url
    else:
        yield f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app at a throwaway DB and disable network catalog loading.

    SQLite in ``tmp_path`` by default; a fresh Postgres schema when SATLAB_TEST_POSTGRES_URL is
    set. Every DB setting is overridden here, so a developer's ``.env`` never reaches a real DB.
    """
    with _test_database(tmp_path) as database_url:
        monkeypatch.setenv("SATLAB_DATABASE_URL", database_url)
        monkeypatch.setenv("SATLAB_MIGRATION_DATABASE_URL", database_url)
        monkeypatch.setenv("SATLAB_AUTO_MIGRATE", "true")
        monkeypatch.setenv("SATLAB_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("SATLAB_CATALOG_AUTOLOAD", "false")
        monkeypatch.setenv("SATLAB_TICK_HZ", "20")
        _clear_caches()
        try:
            yield tmp_path
        finally:
            get_engine().dispose()
            _clear_caches()


@pytest.fixture
def client(app_env: Path) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# A small real fleet (Celestrak "active", epochs 2026-10-09/10) for multi-satellite tests:
# a station, weather, comms, SAR, navigation (MEO) and a geostationary satellite.
FLEET_2026_TLE = """ISS (ZARYA)
1 25544U 98067A   26282.95044904  .00006711  00000+0  13074-3 0  9997
2 25544  51.6315  92.1575 0006672 244.0714 115.9587 15.48795780589546
METOP-B
1 38771U 12049A   26283.27789507  .00000078  00000+0  55223-4 0  9993
2 38771  98.6372 332.4070 0002682  38.8468 321.2902 14.21463758729612
NOAA 20 (JPSS-1)
1 43013U 17073A   26283.24580957  .00000038  00000+0  39019-4 0  9996
2 43013  98.7859 221.7198 0000509  32.8413 327.2795 14.19529954460794
STARLINK-1008
1 44714U 19074B   26283.11040687  .00048877  00000+0  46308-3 0  9996
2 44714  53.1454 230.8706 0002980 107.0376 253.0967 15.66471108382142
GOES 16
1 41866U 16071A   26283.24722488 -.00000099  00000+0  00000+0 0  9995
2 41866   0.6208  83.9145 0002066 148.6588 130.5957  1.00272252 36254
SENTINEL-1C
1 62261U 24235A   26283.26405850  .00000071  00000+0  24770-4 0  9995
2 62261  98.1824 288.9381 0001349  89.4993 270.6362 14.59199816 98195
CSS (TIANHE)
1 48274U 21035A   26283.18867924  .00021388  00000+0  25541-3 0  9993
2 48274  41.4702 343.5575 0000800   8.2160 351.8692 15.60516346311121
ICEYE-X25
1 56963U 23084AH  26283.26577697  .00040012  00000+0  36680-3 0  9998
2 56963  97.5985  78.2550 0002738 222.9201 137.1846 15.67665187185960
NAVSTAR 81 (USA 319)
1 48859U 21054A   26283.19234053 -.00000082  00000+0  00000+0 0  9993
2 48859  55.1111 327.8297 0029120 232.0725 136.4773  2.00562241 39064
"""
FLEET_2026_TIME = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
WALLOPS = (37.9402, -75.4664, 10.0)  # lat_deg, lon_deg, alt_m — GOES 16 sits high in its sky

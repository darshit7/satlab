"""CatalogService against a mocked Celestrak: ingestion, caching, offline degradation."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.data.celestrak import CelestrakClient
from app.db.base import get_sessionmaker
from app.services.catalog import CatalogService, upsert_tle_records
from tests.conftest import ISS_LINE1, ISS_LINE2, ISS_RECORD

ISS_TEXT = f"ISS (ZARYA)\r\n{ISS_LINE1}\r\n{ISS_LINE2}\r\n"


def make_service(handler) -> tuple[CatalogService, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    settings = get_settings()
    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    client = CelestrakClient(settings.celestrak_base_url, 5, client=http)
    return CatalogService(settings, get_sessionmaker(), client), calls


@pytest.fixture
def migrated(client: TestClient, app_env: Path) -> Path:
    return app_env  # the TestClient lifespan has run migrations


async def test_group_refresh_ingests_and_caches(migrated: Path) -> None:
    service, calls = make_service(lambda r: httpx.Response(200, text=ISS_TEXT))
    await service.refresh_if_stale()
    assert calls[0].url.params["GROUP"] == "active"
    assert await service.status_count() == 1
    assert service.cache_file.exists()

    await service.refresh_if_stale()  # fresh cache → no second network call
    assert len(calls) == 1


async def test_offline_falls_back_to_cache_file(migrated: Path) -> None:
    settings = get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cache = settings.cache_dir / f"celestrak_{settings.catalog_group}.tle"
    cache.write_text(ISS_TEXT)
    old = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(cache, (old, old))

    def offline(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    service, _ = make_service(offline)
    await service.refresh_if_stale()
    assert "fetch failed" in (service.last_error or "")
    assert await service.status_count() == 1  # loaded from stale cache file


async def test_resolve_refreshes_stale_elements(migrated: Path) -> None:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, [ISS_RECORD], datetime.now(UTC) - timedelta(days=3))
    service, calls = make_service(lambda r: httpx.Response(200, text=ISS_TEXT))
    elements = await service.resolve(25544)
    assert elements is not None and elements.norad_id == 25544
    assert calls[0].url.params["CATNR"] == "25544"
    assert service.registry.get(25544) is elements


async def test_resolve_unknown_returns_none(migrated: Path) -> None:
    service, _ = make_service(lambda r: httpx.Response(200, text="No GP data found"))
    assert await service.resolve(99999) is None

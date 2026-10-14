from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.data.celestrak import CelestrakClient, parse_tle_text
from app.db.base import get_sessionmaker
from app.db.models import PassWindow
from app.services.catalog import upsert_tle_records
from tests.conftest import BLUFFTON, ISS_LINE1, ISS_LINE2, ISS_RECORD, REFERENCE_TIME

# A second, real element set (NOAA 19, Celestrak 2026-10-10) so search ranking has data to rank.
NOAA19 = """NOAA 19
1 33591U 09005A   26283.29081468  .00000021  00000+0  35045-4 0  9997
2 33591  98.9414 354.1841 0014666  66.9123 293.3594 14.13488617910743
"""


@pytest.fixture
def catalog(client: TestClient) -> TestClient:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, [ISS_RECORD, *parse_tle_text(NOAA19)], datetime.now(UTC))
    return client


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_stations_are_seeded_presets_only(client: TestClient) -> None:
    seeded = client.get("/api/stations").json()
    assert any("Bengaluru" in s["name"] for s in seeded)
    assert seeded[0]["created_at"].endswith("Z") or "+00:00" in seeded[0]["created_at"]

    # Custom locations belong to a session; they can't be saved as stations.
    home = {"name": "Home", "lat_deg": 12.97, "lon_deg": 77.59, "alt_m": 900}
    assert client.post("/api/stations", json=home).status_code == 405


def test_satellite_search(catalog: TestClient) -> None:
    by_name = catalog.get("/api/satellites", params={"q": "noaa"}).json()
    assert [s["norad_id"] for s in by_name["results"]] == [33591]
    assert by_name["remote_searched"] is False

    by_id = catalog.get("/api/satellites", params={"q": "25544"}).json()
    assert by_id["results"][0]["name"] == "ISS (ZARYA)"

    detail = catalog.get("/api/satellites/25544").json()
    assert detail["tle_epoch"].startswith("2014-01-20")
    assert catalog.get("/api/satellites/1").status_code == 404


def test_upsert_updates_existing(catalog: TestClient) -> None:
    renamed = parse_tle_text(f"ISS RENAMED\n{ISS_LINE1}\n{ISS_LINE2}\n")
    with get_sessionmaker()() as db:
        upsert_tle_records(db, renamed, datetime.now(UTC))
    assert catalog.get("/api/satellites/25544").json()["name"] == "ISS RENAMED"


def test_catalog_status(catalog: TestClient) -> None:
    status = catalog.get("/api/catalog/status").json()
    assert status["satellite_count"] == 2
    assert status["group"] == "active"


def test_session_lifecycle(client: TestClient) -> None:
    station_id = client.get("/api/stations").json()[0]["id"]
    created = client.post("/api/sessions", json={"station_id": station_id, "time_scale": 10})
    assert created.status_code == 201
    session = created.json()
    station = client.get("/api/stations").json()[0]
    assert session["station_id"] == station_id
    assert session["location_name"] == station["name"]
    assert (session["lat_deg"], session["lon_deg"]) == (station["lat_deg"], station["lon_deg"])
    assert session["ended_at"] is None

    assert client.post(f"/api/sessions/{session['id']}/end").status_code == 204
    assert client.get(f"/api/sessions/{session['id']}").json()["ended_at"] is not None

    assert client.post("/api/sessions", json={"station_id": 9999}).status_code == 404


def test_session_at_custom_location_saves_no_station(client: TestClient) -> None:
    stations_before = client.get("/api/stations").json()
    location = {"lat_deg": 12.97, "lon_deg": 77.59, "alt_m": 900}
    created = client.post("/api/sessions", json={"location": location})
    assert created.status_code == 201
    session = created.json()
    assert session["station_id"] is None
    assert session["location_name"] == "Custom location"
    assert (session["lat_deg"], session["lon_deg"], session["alt_m"]) == (12.97, 77.59, 900)
    assert client.get("/api/stations").json() == stations_before

    station_id = stations_before[0]["id"]
    both = {"station_id": station_id, "location": location}
    assert client.post("/api/sessions", json=both).status_code == 422
    assert client.post("/api/sessions", json={}).status_code == 422
    off_earth = {"location": {"lat_deg": 91, "lon_deg": 0}}
    assert client.post("/api/sessions", json=off_earth).status_code == 422


@pytest.fixture
def bluffton_session(catalog: TestClient) -> int:
    # Celestrak is mocked: an unknown NORAD ID must come back "not found" without a network call.
    app_catalog = catalog.app.state.catalog
    app_catalog._client = CelestrakClient(
        app_catalog.settings.celestrak_base_url,
        5,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="No GP data found"))
        ),
    )
    session = catalog.post(
        "/api/sessions",
        json={
            "location": {"lat_deg": BLUFFTON[0], "lon_deg": BLUFFTON[1]},
            "sim_start_time": REFERENCE_TIME.isoformat(),
        },
    ).json()
    return session["id"]


def test_pass_schedule(catalog: TestClient, bluffton_session: int) -> None:
    url = f"/api/sessions/{bluffton_session}/passes"
    params = {"norad_id": 25544, "hours": 24, "start": REFERENCE_TIME.isoformat()}
    body = catalog.get(url, params=params).json()
    assert body["name"] == "ISS (ZARYA)"
    assert body["horizon_mask_deg"] == 5.0
    assert body["always_visible"] is False
    passes = body["passes"]
    # Seven passes above 5° in the day; the first was already in progress at the start.
    assert [round(p["max_elev_deg"]) for p in passes] == [19, 86, 11, 30, 34, 14, 16]
    assert passes[0]["aos_time"] < body["start"] < passes[0]["los_time"]
    for p in passes:
        assert p["aos_time"] < p["max_time"] < p["los_time"]
        assert 0 <= p["aos_az_deg"] < 360 and 0 <= p["los_az_deg"] < 360
        assert 60 < p["duration_s"] < 15 * 60

    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(PassWindow)) == 7
    catalog.get(url, params=params)  # recomputing replaces rows rather than duplicating them
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(PassWindow)) == 7


def test_pass_schedule_errors(catalog: TestClient, bluffton_session: int) -> None:
    url = f"/api/sessions/{bluffton_session}/passes"
    assert catalog.get(url, params={"norad_id": 99999}).status_code == 404
    assert catalog.get(url, params={"norad_id": 99999}).json()["detail"] == "satellite not found"
    missing = catalog.get("/api/sessions/424242/passes", params={"norad_id": 25544})
    assert missing.status_code == 404
    for bad in (
        {"norad_id": 0},
        {"norad_id": 25544, "hours": 0},
        {"norad_id": 25544, "hours": 999},
    ):
        assert catalog.get(url, params=bad).status_code == 422
    naive = {"norad_id": 25544, "start": "2014-01-23T11:18:07"}  # no timezone: ambiguous
    assert catalog.get(url, params=naive).status_code == 422

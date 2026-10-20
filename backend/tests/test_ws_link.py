"""End-to-end link state and subsystem health on the stream, during a real NOAA 20 pass."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.data.celestrak import parse_tle_text
from app.db.base import get_sessionmaker
from app.services.catalog import upsert_tle_records
from tests.conftest import FLEET_2026_TLE, WALLOPS
from tests.test_ws import Stream, open_stream, paused

NOAA_20, GOES_16 = 43013, 41866
# NOAA 20 rises over Wallops at 17:49 UTC and culminates at 73° around 17:55.
DURING_PASS = datetime(2026, 10, 10, 17, 52, 0, tzinfo=UTC)


@pytest.fixture
def wallops(client: TestClient) -> dict:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, parse_tle_text(FLEET_2026_TLE), datetime.now(UTC))
    client.portal.call(client.app.state.catalog.rebuild_fleet)
    return client.post(
        "/api/sessions",
        json={
            "location": {"lat_deg": WALLOPS[0], "lon_deg": WALLOPS[1], "alt_m": WALLOPS[2]},
            "sim_start_time": DURING_PASS.isoformat(),
        },
    ).json()


def _paused_at(ws: Stream) -> dict:
    ws.send(type="set_clock", paused=True, sim_time=DURING_PASS.isoformat())
    at = DURING_PASS.isoformat()[:16]
    return ws.until("snapshot", lambda m: paused(m) and m["sim_time"].startswith(at))


def _sat(snapshot: dict, norad_id: int) -> dict:
    return next(s for s in snapshot["satellites"] if s["norad_id"] == norad_id)


def test_link_state_and_health(client: TestClient, wallops: dict) -> None:
    thresholds = client.get("/api/link-profiles").json()
    with open_stream(client, wallops) as ws:
        _paused_at(ws)
        ws.send(type="set_selected", norad_id=NOAA_20)
        snap = ws.until("snapshot", lambda m: m["health"] is not None)

        noaa = _sat(snap, NOAA_20)
        assert noaa["above_mask"]
        link = noaa["link"]
        assert link["frequency_hz"] == 7.812e9
        margin = link["margin_db"]
        expected = (
            "green"
            if margin > thresholds["green_margin_db"]
            else "amber"
            if margin >= thresholds["amber_margin_db"]
            else "red"
        )
        assert link["rag"] == expected
        assert link["ebn0_db"] == pytest.approx(margin + 4.4, abs=0.02)
        # rising: approaching, so the received frequency is high
        assert noaa["range_rate_m_s"] < 0 < link["doppler_hz"]
        assert isinstance(link["strong_link_elev_bands_deg"], list)

        goes = _sat(snap, GOES_16)  # in view, but no published RF profile
        assert goes["link"] is None

        health = snap["health"]
        assert health["norad_id"] == NOAA_20
        assert health["rf"] == {"rag": link["rag"], "margin_db": margin, "modelled": False}
        assert health["power"]["modelled"] and health["thermal"]["modelled"]
        assert health["attitude"]["off_nadir_deg"] is not None  # in view: pointing is checked
        assert health["storage"]["activity"] in {"downlinking", "filling", "full"}
        assert health["rollup"] in {"green", "amber", "red"}

        arc = ws.find("pass_arc", lambda m: m["norad_id"] == NOAA_20)
        assert arc["rag"] is not None and len(arc["rag"]) == len(arc["points"])
        assert ws.find("pass_arc", lambda m: m["norad_id"] == GOES_16)["rag"] is None


def test_link_filter(client: TestClient, wallops: dict) -> None:
    with open_stream(client, wallops) as ws:
        snap = _paused_at(ws)
        assert len(snap["satellites"]) > 1

        ws.send(
            type="set_filter",
            types=["station", "weather", "comms", "navigation", "sar", "other"],
            link_ok_only=True,
        )
        snap = ws.until("snapshot", lambda m: len(m["satellites"]) == 1)
        [only] = snap["satellites"]
        assert only["norad_id"] == NOAA_20 and only["link"]["rag"] != "red"

        ws.send(type="set_selected", norad_id=GOES_16)  # the selection is always streamed
        snap = ws.until("snapshot", lambda m: len(m["satellites"]) == 2)
        assert {s["norad_id"] for s in snap["satellites"]} == {NOAA_20, GOES_16}
        assert snap["health"]["norad_id"] == GOES_16
        assert snap["health"]["rf"]["rag"] is None
        assert snap["health"]["storage"]["fill_pct"] is None


def test_link_profiles_endpoint(client: TestClient, wallops: dict) -> None:
    body = client.get("/api/link-profiles").json()
    assert body["green_margin_db"] == 6.0 and body["amber_margin_db"] == 0.0
    [noaa] = body["profiles"]  # only catalogued satellites have a stored profile
    assert noaa["norad_id"] == NOAA_20
    assert noaa["satellite_name"] == "NOAA 20 (JPSS-1)"
    assert noaa["sources"] and noaa["sources"][0]["url"].startswith("https://")

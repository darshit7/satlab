"""Comms end to end, over the WebSocket, during a real NOAA 20 pass over Wallops: the contact,
housekeeping down, a command up and its acknowledgement, what's refused, and what's stored."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.data.celestrak import parse_tle_text
from app.db.base import get_sessionmaker
from app.db.models import Command, PacketTemplate, TelemetryFrame
from app.services.catalog import upsert_tle_records
from tests.conftest import FLEET_2026_TLE, WALLOPS
from tests.test_ws import Stream, open_stream, paused

NOAA_20, GOES_16, ISS = 43013, 41866, 25544
DURING_PASS = datetime(2026, 10, 10, 17, 52, 0, tzinfo=UTC)
LIMIT = 600  # messages to read while waiting (snapshots stream at 20 Hz in tests)


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


def _in_contact(ws: Stream, norad_id: int = NOAA_20, at: datetime = DURING_PASS) -> dict:
    ws.send(type="set_clock", paused=True, sim_time=at.isoformat())
    ws.send(type="set_selected", norad_id=norad_id)
    return ws.until(
        "snapshot",
        lambda m: paused(m) and m["contact"] is not None and m["contact"]["norad_id"] == norad_id,
        LIMIT,
    )


def _error(ws: Stream) -> str:
    return ws.until("error", limit=LIMIT)["message"]


def test_contact_housekeeping_and_ping(client: TestClient, wallops: dict) -> None:
    with open_stream(client, wallops) as ws:
        snap = _in_contact(ws)
        contact = snap["contact"]
        assert contact["state"] == "active" and contact["downlink"] == "simulated"
        assert contact["channel_modelled"] and 0.002 < contact["one_way_light_time_s"] < 0.012

        ws.send(type="set_clock", paused=False, time_scale=10)
        hk = ws.until("packet", lambda m: m["template"] == "hk", LIMIT)
        assert hk["direction"] == "down" and hk["framing"] == "ccsds" and not hk["layout_sourced"]
        assert hk["margin_db"] is not None and hk["ber"] is not None
        if hk["crc_ok"]:
            fields = {f["name"]: f["value"] for f in hk["decoded"]}
            health = ws.find("snapshot")["health"]
            assert abs(fields["soc_pct"] - health["power"]["soc_pct"]) <= 0.5

        ws.send(type="send_command", norad_id=NOAA_20, command="PING")
        queued = ws.until("command_status", lambda m: m["status"] == "queued", LIMIT)
        command_id = queued["command_id"]
        up = ws.until("packet", lambda m: m["direction"] == "up", LIMIT)
        assert up["command_id"] == command_id and up["kind"] == "command"
        final = ws.until(
            "command_status",
            lambda m: m["command_id"] == command_id and m["status"] in ("acked", "failed"),
            LIMIT,
        )
        assert final["status"] == "acked"  # NOAA 20 is high in the sky: a clean link
        ack = ws.find("packet", lambda m: m["kind"] == "ack")
        assert ack["command_id"] == command_id

    client.portal.call(client.app.state.writer.flush)
    with get_sessionmaker()() as db:
        row = db.get(Command, command_id)
        assert row is not None and row.status == "acked" and row.params == {}
        template = db.get(PacketTemplate, row.template_id)
        assert template is not None and template.name == "PING"
        frames = list(
            db.scalars(select(TelemetryFrame).where(TelemetryFrame.command_id == command_id))
        )
        assert len(frames) == 1 and frames[0].source == "simulated" and frames[0].crc_ok


def test_commands_are_refused_without_contact(client: TestClient, wallops: dict) -> None:
    with open_stream(client, wallops) as ws:
        _in_contact(ws, at=DURING_PASS - timedelta(hours=1))  # NOAA 20 below the horizon
        assert ws.find("snapshot")["contact"]["state"] == "below_mask"
        ws.send(type="send_command", norad_id=NOAA_20, command="PING")
        assert _error(ws).startswith("No contact")

        _in_contact(ws, GOES_16)  # in view, but no templates
        assert ws.find("snapshot")["contact"]["state"] == "no_template"
        ws.send(type="send_command", norad_id=GOES_16, command="PING")
        assert "isn't a command" in _error(ws)

        _in_contact(ws)
        ws.send(type="set_clock", paused=False, time_scale=600)
        ws.until("snapshot", lambda m: m["contact"] and m["contact"]["state"] == "too_fast", LIMIT)
        ws.send(type="send_command", norad_id=NOAA_20, command="PING")
        assert _error(ws).startswith("No contact")


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({"command": "SET_BEACON_INTERVAL", "params": {"interval_s": 5}}, "between 10 and 120"),
        ({"command": "SET_BEACON_INTERVAL", "params": {}}, "missing parameter"),
        ({"command": "PING", "params": {"extra": 1}}, "unknown parameter"),
        ({"command": "SELF_DESTRUCT"}, "isn't a command"),
        ({"command": "PING", "params": {"x": True}}, "invalid message"),  # strict types
        ({"command": "ping"}, "invalid message"),
    ],
)
def test_bad_commands(client: TestClient, wallops: dict, message: dict, error: str) -> None:
    with open_stream(client, wallops) as ws:
        _in_contact(ws)
        ws.send(type="send_command", norad_id=NOAA_20, **message)
        assert error in _error(ws)


def test_comms_filter_keeps_satellites_with_templates(client: TestClient, wallops: dict) -> None:
    with_comms = {NOAA_20, ISS}  # the catalogued ones with templates
    with open_stream(client, wallops) as ws:
        ws.send(type="set_clock", paused=True, sim_time=DURING_PASS.isoformat())
        everything = ws.until("snapshot", lambda m: paused(m) and m["satellites"], LIMIT)
        assert {s["norad_id"] for s in everything["satellites"]} - with_comms  # others in view

        ws.send(type="set_filter", comms_only=True, rising_within_min=60)
        snap = ws.until(
            "snapshot",
            lambda m: {s["norad_id"] for s in m["satellites"]} <= with_comms,
            LIMIT,
        )
        assert NOAA_20 in {s["norad_id"] for s in snap["satellites"]}  # in its pass
        upcoming = ws.until("upcoming", lambda m: m["status"] == "ready", LIMIT)
        assert {p["norad_id"] for p in upcoming["passes"]} <= with_comms


def test_packet_templates_endpoint(client: TestClient, wallops: dict) -> None:
    body = client.get("/api/packet-templates").json()
    assert body["satnogs_configured"] is False  # tests never carry a token
    sats = {s["norad_id"]: s for s in body["satellites"]}
    assert set(sats) == {NOAA_20, ISS}  # the catalogued ones with templates
    noaa = sats[NOAA_20]
    assert {c["name"] for c in noaa["commands"]} == {"PING", "REQUEST_HK", "SET_BEACON_INTERVAL"}
    interval = next(c for c in noaa["commands"] if c["name"] == "SET_BEACON_INTERVAL")
    assert interval["params"][0] == {
        "name": "interval_s",
        "label": "Interval (s)",
        "type": "int",
        "min": 10,
        "max": 120,
        "max_length": 32,
        "charset": "printable",
    }
    iss = sats[ISS]
    assert iss["telemetry"][0]["downlink"] == "satnogs" and iss["telemetry"][0]["layout_sourced"]
    assert all(s["url"].startswith("https://") for s in iss["telemetry"][0]["sources"])

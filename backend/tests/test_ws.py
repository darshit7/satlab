"""End-to-end: REST session → WebSocket stream → visible-by-default snapshots, arcs, filters."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db.base import get_sessionmaker
from app.main import (
    WS_CLOSE_AUTH_REQUIRED,
    WS_CLOSE_POLICY_VIOLATION,
    WS_CLOSE_SERVER_BUSY,
    WS_CLOSE_SESSION_UNAVAILABLE,
)
from app.services.catalog import upsert_tle_records
from tests.conftest import BLUFFTON, ISS_RECORD, REFERENCE_TIME


@pytest.fixture
def session(client: TestClient) -> dict:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, [ISS_RECORD], datetime.now(UTC))
    client.portal.call(client.app.state.catalog.rebuild_fleet)
    return _new_session(client)


def _new_session(client: TestClient) -> dict:
    return client.post(
        "/api/sessions",
        json={
            "location": {"lat_deg": BLUFFTON[0], "lon_deg": BLUFFTON[1]},
            "sim_start_time": REFERENCE_TIME.isoformat(),
        },
    ).json()


class Stream:
    """The server's messages, remembered so geometry sent just before a snapshot can be found."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.seen: list[dict] = []

    def until(self, msg_type: str, predicate=lambda m: True, limit: int = 80) -> dict:
        for _ in range(limit):
            message = json.loads(self.ws.receive_text())
            self.seen.append(message)
            if message["type"] == msg_type and predicate(message):
                return message
        raise AssertionError(f"no {msg_type!r} message received")

    def find(self, msg_type: str, predicate=lambda m: True) -> dict:
        """The latest such message already received, else the next one."""
        for message in reversed(self.seen):
            if message["type"] == msg_type and predicate(message):
                return message
        return self.until(msg_type, predicate)

    def send(self, **message) -> None:
        self.ws.send_text(json.dumps(message))

    def pause_at_reference(self) -> None:
        self.send(type="set_clock", paused=True, sim_time=REFERENCE_TIME.isoformat())


def paused(m: dict) -> bool:
    return m["clock"]["paused"]


@contextmanager
def open_stream(client: TestClient, session: dict, **headers: str) -> Iterator[Stream]:
    """A WebSocket to the session, authenticated with its token like the frontend does."""
    with client.websocket_connect(f"/ws/sessions/{session['id']}", headers=headers) as ws:
        stream = Stream(ws)
        stream.send(type="auth", token=session["token"])
        yield stream


def test_visible_satellite_streams_without_tracking(client: TestClient, session: dict) -> None:
    with open_stream(client, session) as ws:
        first = ws.until("snapshot")
        assert first["observer"]["name"] == "Custom location"
        assert first["observer"]["station_id"] is None
        assert first["observer"]["horizon_mask_deg"] == 5.0
        ws.pause_at_reference()

        # The ISS is 16° up at the reference time: streamed with no set_tracked at all.
        snap = ws.until("snapshot", lambda m: paused(m) and m["satellites"])
        assert snap["visible_count"] == 1
        [iss] = snap["satellites"]
        assert iss["name"] == "ISS (ZARYA)"
        assert iss["type"] == "station"
        assert iss["is_sar_capable"] is False
        assert iss["above_mask"] is True
        # published skyfield reference: altitude 16°16'32.6", azimuth 350°15'20.4", 1168.66 km
        assert iss["elevation_deg"] == pytest.approx(16.2757, abs=1e-3)
        assert iss["azimuth_deg"] == pytest.approx(350.2557, abs=1e-3)
        assert iss["range_m"] == pytest.approx(1_168_660, abs=50)
        assert snap["sim_time"].startswith("2014-01-23T11:18:07")

        arc = ws.find("pass_arc", lambda m: m["norad_id"] == 25544)
        assert arc["aos_time"] < snap["sim_time"] < arc["los_time"]
        assert (arc["start_time"], arc["end_time"]) == (arc["aos_time"], arc["los_time"])
        assert len(arc["points"]) == 48
        assert 16 < arc["max_elev_deg"] < 30

        # Visible but neither tracked nor selected: no ground track is computed for it.
        assert not any(m["type"] == "ground_track" for m in ws.seen)
        upcoming = ws.find("upcoming", lambda m: m["status"] == "ready")
        assert upcoming["total"] == len(upcoming["passes"]) == 0  # the only satellite is up


def test_filter_hides_unless_tracked_or_selected(client: TestClient, session: dict) -> None:
    with open_stream(client, session) as ws:
        ws.pause_at_reference()
        ws.until("snapshot", lambda m: paused(m) and m["satellites"])

        ws.send(type="set_filter", types=["weather", "sar"])
        snap = ws.until("snapshot", lambda m: paused(m) and not m["satellites"])
        assert snap["visible_count"] == 1  # still counted, just not streamed

        ws.send(type="set_selected", norad_id=25544)
        snap = ws.until("snapshot", lambda m: m["satellites"])
        assert snap["satellites"][0]["norad_id"] == 25544
        track = ws.find("ground_track")
        assert track["norad_id"] == 25544

        ws.send(type="set_selected", norad_id=None)
        ws.until("snapshot", lambda m: not m["satellites"])
        ws.send(type="set_tracked", norad_ids=[25544])
        ws.until("snapshot", lambda m: m["satellites"])

        ws.send(type="set_tracked", norad_ids=[])
        ws.send(type="set_filter", min_elevation_deg=20)  # the ISS is at 16°
        ws.until("snapshot", lambda m: not m["satellites"])
        ws.send(type="set_filter")  # defaults: everything above the mask
        ws.until("snapshot", lambda m: m["satellites"])


@pytest.mark.parametrize(
    "message",
    [
        {"type": "set_clock", "time_scale": -5},
        {"type": "set_filter", "min_elevation_deg": 120},
        {"type": "set_filter", "types": ["spaceship"]},
        {"type": "set_filter", "rising_within_min": 61},
        {"type": "set_filter", "comms_only": "maybe"},
        {"type": "set_selected", "norad_id": 0},
        {"type": "set_tracked", "norad_ids": list(range(1, 102))},
        {"type": "schedule_command", "norad_id": 43013, "command": "PING"},  # which pass?
        {
            "type": "schedule_command",
            "norad_id": 43013,
            "command": "PING",
            "aos_time": "2026-10-10T19:31:50",  # no time zone
        },
        {"type": "cancel_command", "command_id": 0},
        {"type": "ack_alarm", "alarm_id": 0},
        {"type": "no_such_message"},
    ],
)
def test_invalid_message_reports_error(client: TestClient, session: dict, message) -> None:
    with open_stream(client, session) as ws:
        ws.send(**message)
        error = ws.until("error")
        assert "invalid message" in error["message"]


def _closed_with(stream: Stream, code: int) -> dict:
    """The error sent before the server closed the socket with ``code``."""
    error = stream.until("error")
    with pytest.raises(WebSocketDisconnect) as exc:
        stream.ws.receive_text()
    assert exc.value.code == code
    return error


def test_unknown_session_is_rejected(client: TestClient, session: dict) -> None:
    unknown = {"id": 424242, "token": session["token"]}
    with open_stream(client, unknown) as ws:
        assert "not found" in _closed_with(ws, WS_CLOSE_SESSION_UNAVAILABLE)["message"]


def test_wrong_token_looks_like_unknown_session(client: TestClient, session: dict) -> None:
    other = _new_session(client)
    with open_stream(client, {"id": session["id"], "token": other["token"]}) as ws:
        assert "not found" in _closed_with(ws, WS_CLOSE_SESSION_UNAVAILABLE)["message"]


@pytest.mark.parametrize("first", [{"type": "set_tracked", "norad_ids": []}, {"type": "auth"}])
def test_first_message_must_be_auth(client: TestClient, session: dict, first: dict) -> None:
    with client.websocket_connect(f"/ws/sessions/{session['id']}") as raw:
        ws = Stream(raw)
        ws.send(**first)
        error = _closed_with(ws, WS_CLOSE_AUTH_REQUIRED)
        assert error["message"] == "authentication required"


def test_auth_times_out(client: TestClient, session: dict, monkeypatch) -> None:
    monkeypatch.setattr(client.app.state.sessions.settings, "ws_auth_timeout_s", 0.05)
    with client.websocket_connect(f"/ws/sessions/{session['id']}") as raw:
        _closed_with(Stream(raw), WS_CLOSE_AUTH_REQUIRED)


def test_foreign_origin_is_refused(client: TestClient, session: dict) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        open_stream(client, session, origin="https://evil.example"),
    ):
        pass
    assert exc.value.code == WS_CLOSE_POLICY_VIOLATION


def test_allowed_origin_is_accepted(client: TestClient, session: dict) -> None:
    with open_stream(client, session, origin="http://localhost:5173") as ws:
        ws.until("snapshot")


def test_active_session_cap(client: TestClient, session: dict, monkeypatch) -> None:
    monkeypatch.setattr(client.app.state.sessions.settings, "max_active_sessions", 1)
    with open_stream(client, session) as first:
        first.until("snapshot")
        with open_stream(client, _new_session(client)) as second:
            assert "capacity" in _closed_with(second, WS_CLOSE_SERVER_BUSY)["message"]
        # A second connection to an already-running session doesn't count against the cap.
        with open_stream(client, session) as again:
            again.until("snapshot")


def test_flooding_client_is_disconnected(client: TestClient, session: dict, monkeypatch) -> None:
    settings = client.app.state.sessions.settings
    monkeypatch.setattr(settings, "ws_messages_per_s", 0.001)
    monkeypatch.setattr(settings, "ws_message_burst", 3)
    with open_stream(client, session) as ws:
        for _ in range(4):
            ws.send(type="set_clock", paused=True)
        with pytest.raises(WebSocketDisconnect) as exc:
            for _ in range(200):
                ws.ws.receive_text()
        assert exc.value.code == WS_CLOSE_POLICY_VIOLATION

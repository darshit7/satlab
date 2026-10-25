"""Pass automation end to end, over the WebSocket, from Wallops: a command booked for NOAA 20's next
pass goes out at AOS (the clock slowed and the antenna turned for it), one for the ISS is held by
its red attitude with an alarm, what's refused, cancelling, restoring after a restart and ending
the session."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.db.models import Alarm, Command
from tests.test_ws import Stream, open_stream
from tests.test_ws_comms import DURING_PASS, ISS, LIMIT, NOAA_20, wallops  # noqa: F401

HELLO = {"addressee": "W1AW", "text": "hello"}
AFTER_PASS = DURING_PASS + timedelta(minutes=30)  # NOAA 20's 17:52 pass is over by then


def _passes(client: TestClient, session: dict, norad_id: int, start: datetime) -> list[dict]:
    response = client.get(
        f"/api/sessions/{session['id']}/passes",
        params={"norad_id": norad_id, "start": start.isoformat(), "hours": 24},
        headers={"X-Session-Token": session["token"]},
    )
    assert response.status_code == 200
    return [p for p in response.json()["passes"] if p["aos_time"] > start.isoformat()]


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _near(t: str | datetime, expected: datetime) -> bool:
    """Same pass: predictions searched from different starts agree to well under a second."""
    return abs(((_at(t) if isinstance(t, str) else t) - expected).total_seconds()) < 1


def _status(ws: Stream, command_id: int | None, *statuses: str) -> dict:
    return ws.until(
        "command_status",
        lambda m: (command_id is None or m["command_id"] == command_id) and m["status"] in statuses,
        LIMIT,
    )


def _error(ws: Stream) -> str:
    return ws.until("error", limit=LIMIT)["message"]


def _schedule(ws: Stream, norad_id: int, command: str, aos: str, **params) -> None:
    ws.send(
        type="schedule_command", norad_id=norad_id, command=command, params=params, aos_time=aos
    )


def test_booked_command_goes_out_at_aos(client: TestClient, wallops: dict) -> None:  # noqa: F811
    nxt = _passes(client, wallops, NOAA_20, AFTER_PASS)[0]
    aos, los = _at(nxt["aos_time"]), _at(nxt["los_time"])
    with open_stream(client, wallops) as ws:
        ws.send(type="set_clock", paused=True, sim_time=(aos - timedelta(minutes=20)).isoformat())
        ws.send(type="set_selected", norad_id=ISS)  # the antenna is elsewhere to begin with
        _schedule(ws, NOAA_20, "PING", nxt["aos_time"])
        booked = _status(ws, None, "scheduled")
        command_id = booked["command_id"]
        assert _near(booked["scheduled_pass"]["aos_time"], aos)

        # Far too fast for comms: the clock lands on the antenna time, at real time.
        ws.send(type="set_clock", paused=False, time_scale=600)
        snap = ws.until("snapshot", lambda m: m["automation"] is not None, LIMIT)
        automation = snap["automation"]
        assert automation["norad_id"] == NOAA_20 and automation["clock_slowed"]
        assert automation["commands"] == 1 and not automation["checked"]
        assert snap["clock"]["time_scale"] == 1.0
        assert abs((_at(snap["sim_time"]) - (aos - timedelta(seconds=60))).total_seconds()) < 5
        assert snap["contact"]["norad_id"] == NOAA_20  # the antenna turned to it

        # Into the pass (higher up, for a cleaner link) at a pace comms follows.
        ws.send(type="set_clock", sim_time=(aos + timedelta(minutes=3)).isoformat(), time_scale=10)
        queued = _status(ws, command_id, "queued")
        check = queued["check"]
        assert check["go"] and check["subsystems"]["rf"] == "amber"  # NOAA 20 never goes green
        assert aos <= _at(check["sim_time"]) < los
        final = _status(ws, command_id, "acked", "failed")
        assert final["scheduled_pass"] is not None and final["check"] == check
        snap = ws.until("snapshot", lambda m: m["automation"] and m["automation"]["checked"], LIMIT)
        assert snap["automation"]["commands"] == 1

    client.portal.call(client.app.state.writer.flush)
    with get_sessionmaker()() as db:
        row = db.get(Command, command_id)
        assert row is not None and row.status == final["status"]
        assert row.scheduled_aos is not None and _near(row.scheduled_aos, aos)
        assert row.check_result == check


def test_red_at_aos_holds_with_an_alarm(client: TestClient, wallops: dict) -> None:  # noqa: F811
    nxt = _passes(client, wallops, ISS, AFTER_PASS)[0]
    aos = _at(nxt["aos_time"])
    with open_stream(client, wallops) as ws:
        ws.send(type="set_clock", paused=True, sim_time=(aos - timedelta(minutes=5)).isoformat())
        _schedule(ws, ISS, "APRS_MESSAGE", nxt["aos_time"], **HELLO)
        command_id = _status(ws, None, "scheduled")["command_id"]

        ws.send(type="set_clock", sim_time=(aos - timedelta(seconds=20)).isoformat(), paused=False)
        ws.send(type="set_clock", time_scale=10)
        held = _status(ws, command_id, "held")
        # The modelled antenna (a generic ±62° beam) doesn't reach the station this low.
        assert held["reason"] == "system check at AOS: attitude red"
        assert held["check"]["subsystems"]["attitude"] == "red" and not held["check"]["go"]
        alarm = ws.until("alarm", limit=LIMIT)
        assert alarm["severity"] == "critical" and alarm["subsystem"] == "attitude"
        assert alarm["norad_id"] == ISS and "held at AOS" in alarm["message"]
        assert not alarm["acknowledged"]

        ws.send(type="ack_alarm", alarm_id=alarm["alarm_id"])
        acked = ws.until("alarm", lambda m: m["acknowledged"], LIMIT)
        assert acked["alarm_id"] == alarm["alarm_id"]
        packets = [m for m in ws.seen if m["type"] == "packet" and m["direction"] == "up"]
        assert packets == []  # held: nothing went up

    client.portal.call(client.app.state.writer.flush)
    with get_sessionmaker()() as db:
        row = db.get(Command, command_id)
        assert row is not None and row.status == "held" and row.check_result is not None
        [stored] = db.scalars(select(Alarm).where(Alarm.session_id == wallops["id"])).all()
        assert stored.seq == alarm["alarm_id"] and stored.acknowledged
        assert stored.severity == "critical" and stored.subsystem == "attitude"
        assert stored.norad_id == ISS


def test_what_cant_be_booked(
    client: TestClient,
    wallops: dict,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes = _passes(client, wallops, NOAA_20, AFTER_PASS)
    with open_stream(client, wallops) as ws:
        ws.send(type="set_clock", paused=True, sim_time=AFTER_PASS.isoformat())
        _schedule(ws, NOAA_20, "PING", (AFTER_PASS - timedelta(minutes=1)).isoformat())
        assert "already started" in _error(ws)
        _schedule(ws, NOAA_20, "PING", (AFTER_PASS + timedelta(hours=30)).isoformat())
        assert "up to 24 hours ahead" in _error(ws)
        between = _at(passes[0]["los_time"]) + timedelta(minutes=20)
        _schedule(ws, NOAA_20, "PING", between.isoformat())
        assert _error(ws).startswith("No pass of NOAA 20 (JPSS-1) at that time")
        _schedule(ws, NOAA_20, "SELF_DESTRUCT", passes[0]["aos_time"])
        assert "isn't a command" in _error(ws)
        _schedule(ws, NOAA_20, "SET_BEACON_INTERVAL", passes[0]["aos_time"], interval_s=5)
        assert "between 10 and 120" in _error(ws)

        monkeypatch.setattr(client.app.state.sessions.settings, "max_scheduled_commands", 1)
        _schedule(ws, NOAA_20, "PING", passes[0]["aos_time"])
        _status(ws, None, "scheduled")
        _schedule(ws, NOAA_20, "PING", passes[1]["aos_time"])
        assert "At most 1 commands" in _error(ws)

        ws.send(type="cancel_command", command_id=999)
        assert _error(ws).startswith("Only a command waiting for its pass")
        ws.send(type="ack_alarm", alarm_id=999)
        assert _error(ws) == "No such alarm."


def test_cancel_restore_and_end(client: TestClient, wallops: dict) -> None:  # noqa: F811
    passes = _passes(client, wallops, NOAA_20, AFTER_PASS)
    with open_stream(client, wallops) as ws:
        ws.send(type="set_clock", paused=True, sim_time=AFTER_PASS.isoformat())
        _schedule(ws, NOAA_20, "PING", passes[0]["aos_time"])
        first = _status(ws, None, "scheduled")["command_id"]
        ws.send(type="cancel_command", command_id=first)
        cancelled = _status(ws, first, "aborted")
        assert cancelled["reason"] == "cancelled by the operator"
        ws.send(type="cancel_command", command_id=first)
        assert _error(ws).startswith("Only a command waiting")

        _schedule(ws, NOAA_20, "REQUEST_HK", passes[1]["aos_time"])
        kept = _status(ws, None, "scheduled")["command_id"]

    # A restart: the session's runtime is rebuilt from the database and re-books the command.
    client.portal.call(client.app.state.sessions.shutdown)
    with open_stream(client, wallops) as ws:
        restored = _status(ws, kept, "scheduled")
        assert restored["command"] == "REQUEST_HK"
        assert _near(restored["scheduled_pass"]["aos_time"], _at(passes[1]["aos_time"]))

    headers = {"X-Session-Token": wallops["token"]}
    assert client.post(f"/api/sessions/{wallops['id']}/end", headers=headers).status_code == 204
    client.portal.call(client.app.state.writer.flush)
    with get_sessionmaker()() as db:
        assert db.get(Command, first).status == "aborted"  # type: ignore[union-attr]
        assert db.get(Command, kept).status == "aborted"  # type: ignore[union-attr]

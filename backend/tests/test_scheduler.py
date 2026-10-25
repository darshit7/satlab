"""Pass automation without a server: the system check, the fast-clock catch, the antenna, the check
at AOS, missed passes, the operator's choices, conflicts and alarms."""

from datetime import UTC, datetime, timedelta

from app.data.celestrak import parse_tle_text
from app.models.ws import (
    AlarmMessage,
    AttitudeHealth,
    CommandStatusMessage,
    ContactState,
    HealthState,
    PowerHealth,
    RfHealth,
    StorageHealth,
    ThermalHealth,
)
from app.sim.comms import StatusRecord
from app.sim.geometry import Observer
from app.sim.passes import Span
from app.sim.propagate import elements_from_tle
from app.sim.scheduler import (
    AlarmAckRecord,
    AlarmRecord,
    BookedPass,
    PassAutomation,
    ScheduledCommand,
    nearest_pass,
    search_passes,
    system_check,
)
from tests.conftest import FLEET_2026_TLE, WALLOPS
from tests.test_comms import TABLE

NOAA_20, ISS = 43013, 25544
T0 = datetime(2026, 10, 10, 16, 0, 0, tzinfo=UTC)
AOS = T0 + timedelta(hours=1)
LOS = AOS + timedelta(minutes=10)
LEAD = timedelta(seconds=60)
TICK_S = 0.5


def _health(norad_id: int = NOAA_20, **rags: str | None) -> HealthState:
    rag = {"power": "green", "thermal": "green", "storage": "green", "attitude": "green"}
    rag |= {"rf": "amber"} | rags
    return HealthState(
        norad_id=norad_id,
        model_version=1,
        rollup="red" if "red" in rag.values() else "amber",
        power=PowerHealth(
            rag=rag["power"], soc_pct=42.0, sunlit_fraction=1.0, illumination="sunlit"
        ),
        thermal=ThermalHealth(
            rag=rag["thermal"], temperature_c=20.0, band_min_c=-10, band_max_c=45
        ),
        storage=StorageHealth(rag=rag["storage"], fill_pct=30.0, activity="filling"),
        attitude=AttitudeHealth(rag=rag["attitude"], off_nadir_deg=69.2, half_beamwidth_deg=62),
        rf=RfHealth(rag=rag["rf"], margin_db=3.1),
    )


def _contact(norad_id: int = NOAA_20, state: str = "active") -> ContactState:
    return ContactState(
        norad_id=norad_id,
        state=state,  # type: ignore[arg-type]
        downlink="simulated",
        channel_modelled=True,
        one_way_light_time_s=0.004,
    )


def _booked(norad_id: int = NOAA_20, aos: datetime = AOS, los: datetime = LOS) -> BookedPass:
    name = {NOAA_20: "NOAA 20", ISS: "ISS (ZARYA)"}[norad_id]
    return BookedPass(norad_id, name, aos, los, 62.0)


def _command(
    command_id: int = 1, name: str = "PING", booked: BookedPass | None = None
) -> ScheduledCommand:
    booked = booked or _booked()
    found = TABLE.by_norad[booked.norad_id].command_named(name)
    assert found is not None
    return ScheduledCommand(command_id, booked.norad_id, found[1], {}, T0, booked)


def _automation() -> tuple[PassAutomation, list]:
    records: list = []
    return PassAutomation(1, records.append, lead_s=60, max_time_scale=60), records


def _statuses(messages) -> list[tuple[int, str]]:
    return [(m.command_id, m.status) for m in messages if isinstance(m, CommandStatusMessage)]


def _alarms(messages) -> list[AlarmMessage]:
    return [m for m in messages if isinstance(m, AlarmMessage)]


# ---------- the check ----------


def test_system_check_is_no_red() -> None:
    go = system_check(NOAA_20, _health(), AOS)
    assert go.go and go.rollup == "amber" and go.subsystems["rf"] == "amber"

    unchecked = system_check(NOAA_20, _health(storage=None, rf=None), AOS)
    assert unchecked.go and unchecked.subsystems["storage"] is None  # no data: not checked

    red = system_check(NOAA_20, _health(attitude="red"), AOS)
    assert not red.go and red.subsystems["attitude"] == "red"

    for health in (None, _health(ISS)):  # no health for this satellite: nothing to check
        blind = system_check(NOAA_20, health, AOS)
        assert not blind.go and set(blind.subsystems.values()) == {None}


# ---------- passes ----------


def _elements(record):
    return elements_from_tle(record.name, record.line1, record.line2)


def test_search_and_match_passes() -> None:
    records = {r.norad_id: r for r in parse_tle_text(FLEET_2026_TLE)}
    station = Observer(*WALLOPS)
    noaa = _elements(records[NOAA_20]).satellite.model
    search = search_passes(noaa, station, 5.0, T0, 24 * 3600, 30.0)
    assert not search.always_visible and len(search.passes) >= 4
    first = search.passes[0]
    assert first.aos is not None
    assert nearest_pass(search.passes, first.aos + timedelta(seconds=90)) is first
    assert nearest_pass(search.passes, first.aos + timedelta(seconds=150)) is None

    goes = _elements(records[41866]).satellite.model  # geostationary, in view
    assert search_passes(goes, station, 5.0, T0, 24 * 3600, 30.0).always_visible


def test_nearest_pass_ignores_open_spans() -> None:
    open_span = Span(None, LOS, AOS, 40.0, None, 10.0)
    assert nearest_pass([open_span], AOS) is None


# ---------- the clock and the antenna ----------


def test_a_fast_clock_lands_on_the_antenna_time() -> None:
    auto, _ = _automation()
    assert (
        auto.clock_catch(T0, time_scale=600, paused=False, tick_s=TICK_S) is None
    )  # nothing booked
    auto.book(_command())

    far = auto.clock_catch(T0, time_scale=600, paused=False, tick_s=TICK_S)
    assert far is None  # 300 s a tick: an hour away is several ticks off

    near = AOS - LEAD - timedelta(seconds=500)
    catch = auto.clock_catch(near, time_scale=600, paused=False, tick_s=TICK_S)
    assert catch is not None and catch.jump_to == AOS - LEAD

    for slow in (60, 10):  # comms follows these: ticks land inside the lead anyway
        assert auto.clock_catch(near, time_scale=slow, paused=False, tick_s=TICK_S) is None
    assert auto.clock_catch(near, time_scale=600, paused=True, tick_s=TICK_S) is None

    inside = auto.clock_catch(
        AOS - timedelta(seconds=10), time_scale=600, paused=False, tick_s=TICK_S
    )
    assert inside is not None and inside.jump_to is None  # already there: only slow down


def test_automation_takes_the_antenna_before_aos() -> None:
    auto, _ = _automation()
    auto.book(_command())
    assert auto.prepare(AOS - LEAD - timedelta(seconds=1)) == (None, [])
    assert auto.state(AOS - LEAD - timedelta(seconds=1)) is None

    antenna, messages = auto.prepare(AOS - LEAD)
    assert antenna == NOAA_20 and messages == []
    state = auto.state(AOS - LEAD)
    assert state is not None and state.commands == 1 and not state.checked
    assert not state.clock_slowed
    assert auto.prepare(AOS - LEAD + timedelta(seconds=1)) == (None, [])  # taken once


def test_clock_slowed_is_reported_for_its_pass() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.clock_catch(
        AOS - LEAD - timedelta(seconds=100), time_scale=600, paused=False, tick_s=TICK_S
    )
    auto.prepare(AOS - LEAD)
    state = auto.state(AOS - LEAD)
    assert state is not None and state.clock_slowed


# ---------- the check at AOS ----------


def test_go_releases_the_pass_commands() -> None:
    auto, records = _automation()
    auto.book(_command(1, "PING"))
    auto.book(_command(2, "REQUEST_HK"))
    auto.prepare(AOS - LEAD)
    assert auto.step(AOS - timedelta(seconds=1), _health(), _contact()) == ([], [])  # not yet

    at = AOS + timedelta(seconds=1)
    auto.prepare(at)
    messages, released = auto.step(at, _health(), _contact())
    assert messages == []
    assert [c.command_id for c in released] == [1, 2]
    assert all(c.issued_at == at and c.check is not None and c.check.go for c in released)
    assert released[0].scheduled_pass is not None and released[0].scheduled_pass.aos_time == AOS
    queued = [r for r in records if isinstance(r, StatusRecord)]
    assert [(r.command_id, r.status) for r in queued] == [(1, "queued"), (2, "queued")]
    assert queued[0].check is not None and queued[0].check["subsystems"]["rf"] == "amber"

    state = auto.state(at)
    assert state is not None and state.checked and state.commands == 2
    assert auto.step(at + timedelta(seconds=1), _health(), _contact()) == ([], [])  # once a pass
    assert auto.pending_count == 0


def test_red_holds_the_commands_and_raises_an_alarm() -> None:
    auto, records = _automation()
    auto.book(_command(1, "APRS_MESSAGE", booked=_booked(ISS)))
    auto.prepare(AOS - LEAD)
    auto.prepare(AOS)
    messages, released = auto.step(AOS, _health(ISS, attitude="red", rf=None), _contact(ISS))
    assert released == []
    assert _statuses(messages) == [(1, "held")]
    held = messages[0]
    assert isinstance(held, CommandStatusMessage)
    assert held.reason == "system check at AOS: attitude red"
    assert held.check is not None and not held.check.go
    [alarm] = _alarms(messages)
    assert alarm.severity == "critical" and alarm.subsystem == "attitude" and alarm.alarm_id == 1
    assert "APRS_MESSAGE for ISS (ZARYA) held at AOS" in alarm.message
    assert "attitude red (station 69° off nadir, beam ±62°)" in alarm.message
    assert [type(r) for r in records] == [StatusRecord, AlarmRecord]
    assert records[0].status == "held" and records[0].check is not None


def test_no_health_holds_with_a_warning() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    messages, released = auto.step(AOS, None, _contact())
    assert released == [] and _statuses(messages) == [(1, "held")]
    [alarm] = _alarms(messages)
    assert alarm.severity == "warning" and alarm.subsystem is None


def test_the_check_waits_for_contact() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    for contact in (None, _contact(state="too_fast"), _contact(ISS)):
        assert auto.step(AOS, _health(), contact) == ([], [])
    _, released = auto.step(AOS + timedelta(seconds=30), _health(), _contact())
    assert len(released) == 1


# ---------- what stops a booked command ----------


def test_a_pass_jumped_past_is_missed() -> None:
    auto, records = _automation()
    auto.book(_command(1))
    auto.book(_command(2, "REQUEST_HK"))
    antenna, messages = auto.prepare(LOS + timedelta(minutes=5))
    assert antenna is None
    assert _statuses(messages) == [(1, "aborted"), (2, "aborted")]
    assert messages[0].reason == "missed its pass: the clock jumped past it"
    [alarm] = _alarms(messages)  # one per pass
    assert alarm.severity == "warning"
    assert alarm.message.startswith("PING, REQUEST_HK for NOAA 20 missed its 17:00 UTC pass")
    assert sum(isinstance(r, AlarmRecord) for r in records) == 1


def test_a_pass_without_contact_is_missed() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    auto.prepare(AOS + timedelta(minutes=1))
    auto.step(AOS + timedelta(minutes=1), _health(), _contact(state="too_fast"))
    _, messages = auto.prepare(LOS)
    assert _statuses(messages) == [(1, "aborted")]
    assert messages[0].reason == "no contact during its pass"
    assert "faster than 60 times real time" in _alarms(messages)[0].message
    assert auto.state(LOS) is None


def test_moving_the_clock_back_before_booking_aborts() -> None:
    auto, _ = _automation()
    auto.book(_command())
    _, messages = auto.prepare(T0 - timedelta(minutes=1))
    assert _statuses(messages) == [(1, "aborted")]
    assert messages[0].reason == "the clock was moved back"
    assert _alarms(messages) == []


def test_moving_the_clock_back_releases_the_antenna_but_keeps_the_booking() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    assert auto.prepare(T0 + timedelta(minutes=1)) == (None, [])
    assert auto.state(T0 + timedelta(minutes=1)) is None and auto.pending_count == 1
    assert auto.prepare(AOS - LEAD)[0] == NOAA_20  # taken again


def test_the_operator_taking_the_antenna_aborts_the_pass() -> None:
    auto, records = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    assert auto.operator_selected(NOAA_20, AOS - LEAD) == []  # the same satellite: no change
    messages = auto.operator_selected(ISS, AOS - timedelta(seconds=20))
    assert _statuses(messages) == [(1, "aborted")]
    assert messages[0].reason == "the operator took the antenna before AOS"
    assert auto.state(AOS) is None and auto.pending_count == 0
    assert not any(isinstance(r, AlarmRecord) for r in records)


def test_after_the_check_the_operator_only_releases_the_antenna() -> None:
    auto, _ = _automation()
    auto.book(_command())
    auto.prepare(AOS - LEAD)
    auto.step(AOS, _health(), _contact())
    assert auto.operator_selected(None, AOS + timedelta(seconds=5)) == []
    assert auto.state(AOS + timedelta(seconds=5)) is None


def test_cancel_only_while_waiting() -> None:
    auto, records = _automation()
    auto.book(_command())
    message = auto.cancel(1, T0)
    assert message is not None and message.status == "aborted"
    assert message.reason == "cancelled by the operator"
    assert records[-1] == StatusRecord(1, "aborted")
    assert auto.cancel(1, T0) is None


# ---------- the antenna's calendar ----------


def test_conflicts_are_other_satellites_overlapping_with_the_lead() -> None:
    auto, _ = _automation()
    auto.book(_command())
    assert auto.conflict(_booked()) is None  # the same pass: shares the check
    clash = _booked(ISS, LOS + timedelta(seconds=30), LOS + timedelta(minutes=8))
    assert auto.conflict(clash) == _booked()  # its antenna time starts before NOAA 20's LOS
    later = _booked(ISS, LOS + LEAD + timedelta(seconds=1), LOS + timedelta(minutes=12))
    assert auto.conflict(later) is None

    auto.prepare(AOS - LEAD)
    auto.step(AOS, _health(), _contact())  # checked: the window still holds the antenna
    assert auto.conflict(clash) == _booked()


# ---------- alarms ----------


def test_alarms_are_numbered_kept_and_acknowledged() -> None:
    auto, records = _automation()
    auto.restore([], last_seq=4)
    auto.book(_command())
    auto.prepare(LOS + timedelta(minutes=1))
    [alarm] = auto.alarms
    assert alarm.alarm_id == 5 and not alarm.acknowledged

    acked = auto.ack(5)
    assert acked is not None and acked.acknowledged and auto.alarms[0].acknowledged
    assert records[-1] == AlarmAckRecord(1, 5)
    count = len(records)
    assert auto.ack(5) is not None and len(records) == count  # once
    assert auto.ack(99) is None


def test_pending_statuses_for_a_client_that_connects() -> None:
    auto, _ = _automation()
    auto.book(_command(1))
    auto.book(
        _command(2, booked=_booked(aos=AOS + timedelta(hours=2), los=LOS + timedelta(hours=2)))
    )
    statuses = auto.pending_statuses()
    assert [(s.command_id, s.status) for s in statuses] == [(1, "scheduled"), (2, "scheduled")]
    assert statuses[1].scheduled_pass is not None
    assert statuses[1].scheduled_pass.aos_time == AOS + timedelta(hours=2)

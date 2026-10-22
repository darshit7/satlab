"""The comms runtime, without a server: contact, beacons on the grid, the channel, commands,
replayed frames and what goes to the database."""

from datetime import UTC, datetime, timedelta

import pytest

from app.data.seed import packet_template_specs
from app.models.ws import (
    AttitudeHealth,
    CommandStatusMessage,
    HealthState,
    PacketMessage,
    PowerHealth,
    RfHealth,
    StorageHealth,
    ThermalHealth,
)
from app.packets.ax25 import Address, UiFrame, encode_ui
from app.packets.table import CommsTable
from app.sim.comms import (
    ArchivedFrame,
    CommsConfig,
    CommsRuntime,
    ContactLook,
    FrameRecord,
    NoArchive,
    OutgoingCommand,
    StatusRecord,
)

T0 = datetime(2026, 10, 10, 17, 52, 0, tzinfo=UTC)
NOAA_20, ISS, CROCUBE, GOES_16 = 43013, 25544, 62394, 41866


def _table() -> CommsTable:
    rows = []
    for norad_id, templates in packet_template_specs().templates().items():
        rows += [(1000 * norad_id + i, norad_id, t) for i, t in enumerate(templates)]
    return CommsTable.build(rows)


TABLE = _table()

HEALTH = HealthState(
    norad_id=NOAA_20,
    model_version=1,
    rollup="green",
    power=PowerHealth(rag="green", soc_pct=88.0, sunlit_fraction=1.0, illumination="sunlit"),
    thermal=ThermalHealth(rag="green", temperature_c=21.37, band_min_c=-10, band_max_c=45),
    storage=StorageHealth(rag="green", fill_pct=40.0, activity="downlinking"),
    attitude=AttitudeHealth(rag="green", off_nadir_deg=56.4, half_beamwidth_deg=62),
    rf=RfHealth(rag="green", margin_db=8.0),
)


def _look(
    norad_id: int = NOAA_20, *, margin: float | None = 8.0, above: bool = True
) -> ContactLook:
    return ContactLook(
        norad_id=norad_id,
        elevation_deg=40.0 if above else -10.0,
        above_mask=above,
        range_m=1_200_000.0,
        margin_db=margin if above else None,
        has_link=norad_id == NOAA_20,
    )


class Recorder:
    def __init__(self) -> None:
        self.records: list[FrameRecord | StatusRecord] = []

    def __call__(self, record: FrameRecord | StatusRecord) -> None:
        self.records.append(record)


def _runtime(archive=None, **config) -> tuple[CommsRuntime, Recorder]:
    sink = Recorder()
    return CommsRuntime(1, CommsConfig(**config), archive or NoArchive(), sink), sink


def _step(rt: CommsRuntime, at: datetime, look: ContactLook | None, *, scale: float = 1.0):
    return rt.step(at, look, HEALTH, TABLE, time_scale=scale, paused=False)


def _packets(messages) -> list[PacketMessage]:
    return [m for m in messages if isinstance(m, PacketMessage)]


def _statuses(messages) -> list[str]:
    return [m.status for m in messages if isinstance(m, CommandStatusMessage)]


def _values(packet: PacketMessage) -> dict:
    assert packet.decoded is not None
    return {f.name: f.value for f in packet.decoded}


# ---------- contact ----------


def test_contact_states() -> None:
    rt, _ = _runtime()
    _step(rt, T0, _look())
    assert rt.contact is not None and rt.contact.state == "active"
    assert rt.contact.downlink == "simulated" and rt.contact.channel_modelled
    assert rt.contact.one_way_light_time_s == pytest.approx(0.004, abs=1e-4)

    _step(rt, T0, _look(above=False))
    assert rt.contact is not None and rt.contact.state == "below_mask"
    assert rt.contact.one_way_light_time_s is None  # below the horizon

    _step(rt, T0, _look(), scale=600)
    assert rt.contact is not None and rt.contact.state == "too_fast"

    _step(rt, T0, _look(GOES_16, margin=None))
    assert rt.contact is not None and rt.contact.state == "no_template"

    _step(rt, T0, _look(CROCUBE, margin=None))
    assert rt.contact is not None and rt.contact.downlink == "satnogs"
    assert rt.contact.archive == "not_configured" and not rt.contact.channel_modelled

    _step(rt, T0, None)
    assert rt.contact is None


# ---------- beacons ----------


def test_housekeeping_on_the_grid() -> None:
    rt, sink = _runtime()
    assert _packets(_step(rt, T0, _look())) == []  # a new contact starts now: no backlog
    packets = _packets(_step(rt, T0 + timedelta(seconds=25), _look()))
    # hk every 10 s on the absolute grid: :10 and :20 after 17:52:00
    assert [p.sim_time for p in packets] == [T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)]
    hk = packets[0]
    assert hk.direction == "down" and hk.template == "hk" and hk.framing == "ccsds"
    assert hk.delivered and hk.crc_ok and hk.bit_errors == 0 and not hk.layout_sourced
    values = _values(hk)
    assert values["soc_pct"] == 88.0 and values["temperature_c"] == 21.37
    assert values["illumination"] == "sunlit" and values["beacon_interval_s"] == 10
    assert [type(r) for r in sink.records] == [FrameRecord, FrameRecord]


def test_time_jumps_dont_flood() -> None:
    rt, _ = _runtime(max_packets_per_tick=4)
    _step(rt, T0, _look())
    assert len(_packets(_step(rt, T0 + timedelta(hours=1), _look()))) == 4
    # backwards: start again, no packets
    assert _packets(_step(rt, T0, _look())) == []


def test_weak_link_corrupts_frames() -> None:
    def received(margin: float) -> list[PacketMessage]:
        rt, _ = _runtime(max_packets_per_tick=20)
        _step(rt, T0, _look(margin=margin))
        packets = []
        for k in range(1, 21):  # 20 ticks of 10 s: one hk each
            packets += _packets(_step(rt, T0 + timedelta(seconds=10 * k), _look(margin=margin)))
        return packets

    strong = received(8.0)
    assert len(strong) == 20 and all(p.crc_ok for p in strong)
    weak = received(-4.0)
    bad = [p for p in weak if not p.crc_ok]
    assert len(bad) > 5
    assert all(p.decoded is None and p.bit_errors for p in bad)  # nothing trusted from a bad frame
    assert received(-4.0) == weak  # deterministic: the same packets get the same errors


# ---------- commands ----------


def _command(
    name: str, norad_id: int = NOAA_20, params=None, command_id: int = 7
) -> OutgoingCommand:
    sat = TABLE.by_norad[norad_id]
    found = sat.command_named(name)
    assert found is not None
    return OutgoingCommand(command_id, norad_id, found[1], params or {}, issued_at=T0)


def test_ping_is_acknowledged() -> None:
    rt, sink = _runtime()
    _step(rt, T0, _look())
    assert rt.can_command(NOAA_20) and not rt.can_command(ISS)
    assert rt.submit(_command("PING")).status == "queued"

    sent = _step(rt, T0 + timedelta(milliseconds=500), _look())
    assert _statuses(sent) == ["sent"]
    [up] = _packets(sent)
    assert up.direction == "up" and up.kind == "command" and up.command_id == 7
    assert _values(up)["command"] == "PING"

    answered = _step(rt, T0 + timedelta(seconds=2), _look())
    assert _statuses(answered) == ["acked"]
    [ack] = [p for p in _packets(answered) if p.kind == "ack"]
    assert _values(ack)["command_opcode"] == "0x01" and _values(ack)["status"] == "accepted"
    assert ack.command_id == 7
    assert [r.status for r in sink.records if isinstance(r, StatusRecord)] == ["sent", "acked"]


def test_beacon_interval_command_changes_the_cadence() -> None:
    rt, _ = _runtime()
    _step(rt, T0, _look())
    rt.submit(_command("SET_BEACON_INTERVAL", params={"interval_s": 30}))
    _step(rt, T0 + timedelta(milliseconds=500), _look())
    _step(rt, T0 + timedelta(seconds=2), _look())
    packets = _packets(_step(rt, T0 + timedelta(seconds=61), _look()))
    hk = [p for p in packets if p.template == "hk"]
    assert [p.sim_time.second for p in hk] == [30, 0]  # every 30 s now
    assert _values(hk[-1])["beacon_interval_s"] == 30
    assert _values(hk[-1])["commands_received"] == 1


def test_contact_loss_aborts_or_fails() -> None:
    rt, _ = _runtime()
    _step(rt, T0, _look())
    rt.submit(_command("PING", command_id=1))
    out = _step(rt, T0 + timedelta(seconds=1), _look(above=False))
    assert _statuses(out) == ["aborted"]

    _step(rt, T0, _look())
    rt.submit(_command("PING", command_id=2))
    _step(rt, T0 + timedelta(milliseconds=100), _look())  # sent
    out = _step(rt, T0 + timedelta(seconds=1), _look(above=False))
    assert _statuses(out) == ["failed"]


def test_aprs_message_comes_back_digipeated() -> None:
    rt, _ = _runtime()
    _step(rt, T0, _look(ISS, margin=None))
    rt.submit(_command("APRS_MESSAGE", ISS, {"addressee": "W1AW", "text": "hello"}))
    _step(rt, T0 + timedelta(milliseconds=100), _look(ISS, margin=None))
    out = _step(rt, T0 + timedelta(milliseconds=500), _look(ISS, margin=None))
    assert _statuses(out) == ["acked"]
    [echo] = _packets(out)
    assert echo.kind == "digipeat" and echo.bit_errors is None  # no RF data: not modelled
    values = _values(echo)
    assert values["path"] == "ARISS*" and values["message"] == "hello"


# ---------- replayed frames ----------


class FakeArchive:
    def __init__(self, frames: dict[str, list[ArchivedFrame]]) -> None:
        self._frames = frames

    def frames(self, norad_id: int, template: str):
        return self._frames.get(template, []) if norad_id == CROCUBE else []

    def status(self, norad_id: int):
        return "ok"


def _crocube(info: str, observation: int) -> ArchivedFrame:
    raw = encode_ui(UiFrame(Address("CQ"), Address("9A0CC"), (), info.encode()), fcs=False)
    return ArchivedFrame(raw, "psu", observation, 42, T0 - timedelta(days=1))


def test_satnogs_frames_are_replayed_in_turn() -> None:
    archive = FakeArchive(
        {
            "psu": [
                _crocube("PSU,1,2,3,8000,2500,2400,1,1,7d,1,9", 11),
                _crocube("PSU,1,2,3,8100,2500,2400,1,1,7d,1,9", 12),
            ]
        }
    )
    rt, sink = _runtime(archive, max_packets_per_tick=10)
    _step(rt, T0, _look(CROCUBE, margin=None))
    seen = []
    for k in range(1, 4):  # PSU every 90 s
        seen += [
            p
            for p in _packets(
                _step(rt, T0 + timedelta(seconds=90 * k), _look(CROCUBE, margin=None))
            )
            if p.template == "psu"
        ]
    assert [p.origin.observation_id for p in seen if p.origin] == [11, 12, 11]
    assert [_values(p)["battery_mv"] for p in seen] == [8000, 8100, 8000]
    assert all(p.source == "satnogs" and p.bit_errors is None for p in seen)
    frames = [r for r in sink.records if isinstance(r, FrameRecord)]
    assert frames[0].source == "satnogs" and frames[0].origin["observation_id"] == 11

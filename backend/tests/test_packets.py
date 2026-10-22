"""Packet codecs and templates: CRC check values, AX.25 and CCSDS framing (well-formed and hostile
input), and the seed's templates decoding the frames their sources document."""

import struct
from datetime import UTC, datetime

import pytest

from app.data.seed import packet_template_specs
from app.packets.ax25 import Address, UiFrame, decode_ui, encode_ui
from app.packets.ccsds import SpacePacket, decode_space_packet, encode_space_packet
from app.packets.crc import crc16_ccitt_false, crc16_x25
from app.packets.errors import DecodeError
from app.packets.templates import (
    CommandTemplate,
    TelemetryTemplate,
    digipeat,
    frame_command,
    frame_telemetry,
    identify,
    read_telemetry,
    validate_params,
)

T0 = datetime(2026, 10, 10, 17, 52, 0, tzinfo=UTC)
CROCUBE, UWE4, ISS, NOAA_20 = 62394, 43880, 25544, 43013


def _templates(norad_id: int) -> list[TelemetryTemplate | CommandTemplate]:
    return packet_template_specs().templates()[norad_id]


def _telemetry(norad_id: int) -> list[TelemetryTemplate]:
    return [t for t in _templates(norad_id) if isinstance(t, TelemetryTemplate)]


def _command(norad_id: int, name: str) -> CommandTemplate:
    return next(
        t for t in _templates(norad_id) if isinstance(t, CommandTemplate) and t.name == name
    )


def _fields(fields) -> dict:
    return {f.name: f.value for f in fields}


# ---------- CRCs ----------


def test_crc_check_values() -> None:
    assert crc16_x25(b"123456789") == 0x906E
    assert crc16_ccitt_false(b"123456789") == 0x29B1


# ---------- AX.25 ----------


def _ui(info: bytes = b"hello", path: tuple[Address, ...] = ()) -> UiFrame:
    return UiFrame(Address("CQ"), Address("N0CALL", 7), path, info)


def test_ax25_address_encoding() -> None:
    raw = encode_ui(_ui(), fcs=False)
    # each callsign character shifted left by one, padded with spaces
    assert raw[:7] == bytes([0x86, 0xA2, 0x40, 0x40, 0x40, 0x40, 0x60])  # "CQ    ", SSID 0
    assert raw[7:13] == bytes([0x9C, 0x60, 0x86, 0x82, 0x98, 0x98])  # "N0CALL"
    assert raw[13] == 0x60 | (7 << 1) | 1  # SSID 7, last address
    assert raw[14:16] == bytes([0x03, 0xF0]) and raw[16:] == b"hello"


@pytest.mark.parametrize("fcs", [False, True])
def test_ax25_roundtrip(fcs: bool) -> None:
    frame = _ui(b"APRS text", (Address("ARISS"), Address("WIDE2", 2, flag=True)))
    decoded, ok = decode_ui(encode_ui(frame, fcs=fcs), fcs=fcs)
    assert ok and decoded == frame
    assert decoded.path == "ARISS,WIDE2-2*"


def test_ax25_fcs_detects_corruption() -> None:
    raw = bytearray(encode_ui(_ui(), fcs=True))
    raw[-4] ^= 0x01
    _, ok = decode_ui(bytes(raw), fcs=True)
    assert not ok


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x86\xa2\x40\x40\x40\x40\x60",  # destination only
        encode_ui(_ui(), fcs=False)[:15],  # cut before the PID
        # no address ever sets the extension bit: runs past the 10-address limit
        bytes([0x86, 0xA2, 0x40, 0x40, 0x40, 0x40, 0x60]) * 12 + b"\x03\xf0",
        encode_ui(_ui(), fcs=False)[:14] + b"\x00\xf0hello",  # an I frame, not UI
        bytes([0x87]) + encode_ui(_ui(), fcs=False)[1:],  # extension bit inside a callsign
        encode_ui(_ui(), fcs=False) + b"x" * 300,  # information field over N1
    ],
)
def test_ax25_rejects_malformed(raw: bytes) -> None:
    with pytest.raises(DecodeError):
        decode_ui(raw, fcs=False)


# ---------- CCSDS ----------


def test_space_packet_header() -> None:
    packet = SpacePacket(
        apid=0x100, packet_type="tm", sequence_count=5, time=None, user_data=b"\x01"
    )
    raw = encode_space_packet(packet, error_control=True)
    assert raw[:6] == bytes([0x01, 0x00, 0xC0, 0x05, 0x00, 0x02])  # 1 octet + CRC = 3 → 2
    decoded, ok = decode_space_packet(raw, error_control=True, max_bytes=512)
    assert ok and decoded == packet


def test_space_packet_time_and_crc() -> None:
    packet = SpacePacket(apid=7, packet_type="tc", sequence_count=0x3FFF, time=T0, user_data=b"ab")
    raw = bytearray(encode_space_packet(packet, error_control=True))
    decoded, ok = decode_space_packet(bytes(raw), error_control=True, max_bytes=512)
    assert ok and decoded.time == T0 and decoded.packet_type == "tc"
    raw[-3] ^= 0x40
    _, ok = decode_space_packet(bytes(raw), error_control=True, max_bytes=512)
    assert not ok


@pytest.mark.parametrize(
    "raw",
    [
        b"\x01\x00\xc0\x05\x00",  # header only
        b"\x01\x00\xc0\x05\x00\x09\x01",  # declares 10 octets, has 1
        b"\x21\x00\xc0\x05\x00\x00\x01",  # version 1
        b"\x09\x00\xc0\x05\x00\x00\x01",  # secondary header flag but no room for the time
    ],
)
def test_space_packet_rejects_malformed(raw: bytes) -> None:
    with pytest.raises(DecodeError):
        decode_space_packet(raw, error_control=False, max_bytes=512)


def test_space_packet_length_cap() -> None:
    raw = encode_space_packet(SpacePacket(1, "tm", 0, None, b"x" * 600), error_control=False)
    with pytest.raises(DecodeError):
        decode_space_packet(raw, error_control=False, max_bytes=512)


# ---------- templates (the seed) ----------


def _crocube_frame(info: str) -> bytes:
    return encode_ui(UiFrame(Address("CQ"), Address("9A0CC"), (), info.encode()), fcs=False)


def test_crocube_documented_beacons() -> None:
    """The examples in CroCube's amateur radio document decode to its stated meanings."""
    templates = _telemetry(CROCUBE)
    psu = identify(templates, _crocube_frame("PSU,26,678,1688514,8197,2771,2520,210,113,7d,1,95"))
    assert psu is not None
    template, fields = psu
    values = _fields(fields)
    assert template.name == "psu" and values["source"] == "9A0CC"
    assert values["battery_mv"] == 8197
    assert values["system_temp_c"] == 27.71 and values["battery_temp_c"] == 25.2
    assert values["channel_status"] == "0x7D" and values["system_state"] == "okay"
    assert values["ground_watchdog_h"] == 95

    trx = identify(templates, _crocube_frame("U,659,1746404,74,0,2854,3008,2787,0,,0,87,95,0"))
    assert trx is not None and trx[0].name == "trx"
    values = _fields(trx[1])
    assert values["mcu_temp_c"] == 28.54 and values["last_digi_user"] is None
    assert values["rssi_dbm"] == -86.5  # (95 / 2) - 134

    obc = identify(templates, _crocube_frame("OBC,140,653,20942,8251,2749,6024"))
    assert obc is not None and _fields(obc[1])["mcu_temp_c"] == 27.49


def test_crocube_frames_from_others_are_not_crocube() -> None:
    other = encode_ui(UiFrame(Address("CQ"), Address("N0CALL"), (), b"PSU,1,2"), fcs=False)
    assert identify(_telemetry(CROCUBE), other) is None


def test_csv_tolerates_junk_readings() -> None:
    fields = identify(_telemetry(CROCUBE), _crocube_frame("OBC,1_0,nan,inf,,9" + "9" * 30))
    assert fields is not None
    values = _fields(fields[1])
    assert values["boot_count"] is None and values["uptime_s"] is None
    assert values["uptime_total_s"] is None and values["battery_mv"] is None


def _uwe4_info(api: int = 14) -> bytes:
    header = struct.pack("<BBHBBBBBB", 0x01, 0x02, 77, 2, 1, 1, 0, api, 46)
    payload = struct.pack(
        "<BHH6sIHIHbbbbhhhhhbbbbbbbHH",
        5, 1, 0, (1_760_000_000).to_bytes(6, "little"), 30 * 1024, 0, 86_400, 0x00FF,
        12, 95, 11, 94, -150, 4100, -140, 4090, 1200, 18, -5, 30, 2, 25, 40, -20, 37_500, 0xBEEF,
    )  # fmt: skip
    return header + payload


def test_uwe4_housekeeping() -> None:
    frame = encode_ui(UiFrame(Address("DP0UWH"), Address("DP0UWH"), (), _uwe4_info()), fcs=False)
    found = identify(_telemetry(UWE4), frame)
    assert found is not None
    values = _fields(found[1])
    assert "api" not in values  # identifying constants aren't readings
    assert values["batt_a_soc_pct"] == 95 and values["batt_a_voltage_mv"] == 4100
    assert values["beacon_period_s"] == 30.0 and values["uptime_s"] == 86_400
    assert values["frequency_khz"] == 437_500
    assert values["timestamp"].startswith("2025-10-09")
    assert values["checksum"] == "0xBEEF"
    # another message type (an RF text message, API 103) isn't housekeeping
    other = encode_ui(UiFrame(Address("DP0UWH"), Address("DP0UWH"), (), _uwe4_info(103)), fcs=False)
    assert identify(_telemetry(UWE4), other) is None


def test_iss_aprs_message() -> None:
    raw = encode_ui(
        UiFrame(
            Address("APRS"),
            Address("W1AW"),
            (Address("RS0ISS", flag=True),),
            b":N0CALL   :hi there{12",
        ),
        fcs=False,
    )
    found = identify(_telemetry(ISS), raw)
    assert found is not None
    values = _fields(found[1])
    assert values["path"] == "RS0ISS*" and values["aprs_type"] == "message"
    assert values["addressee"] == "N0CALL" and values["message"] == "hi there"


def test_aprs_text_is_sanitised() -> None:
    raw = encode_ui(
        UiFrame(Address("APRS"), Address("W1AW"), (), b">status\x00\x1b[31m"), fcs=False
    )
    found = identify(_telemetry(ISS), raw)
    assert found is not None and _fields(found[1])["text"] == ">status..[31m"


def test_simulated_housekeeping_roundtrip() -> None:
    hk = next(t for t in _telemetry(NOAA_20) if t.name == "hk")
    values = {
        "power.soc_pct": 87.4,
        "power.illumination": 2,
        "power.sunlit_fraction": 0.0,
        "thermal.temperature_c": -3.27,
        "storage.fill_pct": 41.0,
        "attitude.off_nadir_deg": 56.43,
        "comms.beacon_interval_s": 10,
        "comms.commands_received": 3,
    }
    raw = frame_telemetry(hk, values, sequence=42, sim_time=T0)
    fields, ok = read_telemetry(hk, raw)
    assert ok and fields is not None
    decoded = _fields(fields)
    assert decoded["sequence"] == 42 and decoded["time"] == T0.isoformat(timespec="milliseconds")
    assert decoded["soc_pct"] == 87.5  # 0.5 % steps
    assert decoded["illumination"] == "eclipse"
    assert decoded["temperature_c"] == -3.27 and decoded["off_nadir_deg"] == 56.43
    # the ack template's APID differs: an hk frame isn't an ack
    ack = next(t for t in _telemetry(NOAA_20) if t.name == "ack")
    with pytest.raises(DecodeError):
        read_telemetry(ack, raw)


def test_out_of_range_values_clamp() -> None:
    hk = next(t for t in _telemetry(NOAA_20) if t.name == "hk")
    raw = frame_telemetry(
        hk, {"power.soc_pct": 1e9, "thermal.temperature_c": float("nan")}, sequence=0, sim_time=T0
    )
    fields, ok = read_telemetry(hk, raw)
    assert ok and fields is not None
    assert _fields(fields)["soc_pct"] == 127.5 and _fields(fields)["temperature_c"] == 0.0


# ---------- commands ----------


def test_command_params_are_validated() -> None:
    interval = _command(NOAA_20, "SET_BEACON_INTERVAL")
    assert validate_params(interval, {"interval_s": 30}) == {"interval_s": 30}
    for bad in (
        {},
        {"interval_s": 5},
        {"interval_s": "30"},
        {"interval_s": True},
        {"interval_s": 30, "x": 1},
    ):
        with pytest.raises(ValueError):
            validate_params(interval, bad)

    aprs = _command(ISS, "APRS_MESSAGE")
    assert validate_params(aprs, {"addressee": "W1AW-7", "text": "hello"})
    for bad in (
        {"addressee": "w1aw", "text": "x"},
        {"addressee": "W1AW", "text": "café"},
        {"addressee": "W1AW", "text": "x" * 68},
    ):
        with pytest.raises(ValueError):
            validate_params(aprs, bad)


def test_ccsds_telecommand_frame() -> None:
    raw = frame_command(
        _command(NOAA_20, "SET_BEACON_INTERVAL"), {"interval_s": 30}, sequence=9, sim_time=T0
    )
    packet, ok = decode_space_packet(raw, error_control=True, max_bytes=512)
    assert ok and packet.packet_type == "tc" and packet.apid == 384 and packet.time is None
    assert packet.user_data == bytes([3, 0, 30])  # opcode 3, interval 30 (u16)


def test_aprs_message_is_digipeated_by_the_iss() -> None:
    command = _command(ISS, "APRS_MESSAGE")
    uplink = frame_command(
        command, {"addressee": "W1AW", "text": "73 from satlab"}, sequence=1, sim_time=T0
    )
    frame, _ = decode_ui(uplink, fcs=False)
    assert str(frame.source) == "N0CALL" and frame.path == "ARISS"
    echo = digipeat(uplink, fcs=False)
    found = identify(_telemetry(ISS), echo)
    assert found is not None
    values = _fields(found[1])
    assert values["path"] == "ARISS*" and values["message"] == "73 from satlab"

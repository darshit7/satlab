"""CCSDS Space Packets (CCSDS 133.0-B-2), encoded and decoded with bounds checks.

A packet is a 6-byte primary header (version, type, secondary-header flag, APID, sequence flags and
count, data length) and a packet data field: an optional secondary header (here a CUC time code),
the user data and, when the mission uses one, a 16-bit packet error control (CRC-16/CCITT-FALSE).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.packets.crc import crc16_ccitt_false
from app.packets.errors import DecodeError

PRIMARY_HEADER_BYTES = 6
MAX_APID = 0x7FE  # 0x7FF is the idle packet
MAX_SEQUENCE_COUNT = 0x3FFF
UNSEGMENTED = 0b11
CUC_BYTES = 6  # 4 octets of seconds, 2 of binary fraction

PacketType = Literal["tm", "tc"]


@dataclass(frozen=True)
class SpacePacket:
    apid: int
    packet_type: PacketType
    sequence_count: int
    # Secondary header time (CUC, seconds since the Unix epoch: an agency-defined epoch, as
    # CCSDS 301.0 allows); None without a secondary header.
    time: datetime | None
    user_data: bytes


def encode_space_packet(packet: SpacePacket, *, error_control: bool) -> bytes:
    if not 0 <= packet.apid <= MAX_APID or not 0 <= packet.sequence_count <= MAX_SEQUENCE_COUNT:
        raise ValueError("APID or sequence count out of range")
    data = b""
    if packet.time is not None:
        seconds = packet.time.timestamp()
        whole = int(seconds)
        data += whole.to_bytes(4, "big") + int((seconds - whole) * 65536).to_bytes(2, "big")
    data += packet.user_data
    length = len(data) + (2 if error_control else 0)
    if length == 0 or length > 0x10000:
        raise ValueError("packet data field must hold 1 to 65536 octets")
    first = (int(packet.packet_type == "tc") << 12) | (int(packet.time is not None) << 11)
    header = (
        (first | packet.apid).to_bytes(2, "big")
        + ((UNSEGMENTED << 14) | packet.sequence_count).to_bytes(2, "big")
        + (length - 1).to_bytes(2, "big")
    )
    raw = header + data
    if error_control:
        raw += crc16_ccitt_false(raw).to_bytes(2, "big")
    return raw


def decode_space_packet(
    raw: bytes, *, error_control: bool, max_bytes: int
) -> tuple[SpacePacket, bool]:
    """Parse a packet. Returns it and whether its error control checked (True without one).

    The declared length must match the bytes present exactly and stay within ``max_bytes``.
    """
    if len(raw) > max_bytes:
        raise DecodeError("packet longer than this template allows")
    if len(raw) < PRIMARY_HEADER_BYTES + 1:
        raise DecodeError("packet too short")
    word0 = int.from_bytes(raw[0:2], "big")
    word1 = int.from_bytes(raw[2:4], "big")
    declared = int.from_bytes(raw[4:6], "big") + 1
    if word0 >> 13 != 0:
        raise DecodeError("not a version-1 space packet")
    if declared != len(raw) - PRIMARY_HEADER_BYTES:
        raise DecodeError("declared length doesn't match the packet")
    has_time = bool(word0 & 0x0800)
    data = raw[PRIMARY_HEADER_BYTES:]
    crc_ok = True
    if error_control:
        if len(data) < 2:
            raise DecodeError("packet too short for its error control")
        crc_ok = crc16_ccitt_false(raw[:-2]) == int.from_bytes(raw[-2:], "big")
        data = data[:-2]
    time = None
    if has_time:
        if len(data) < CUC_BYTES:
            raise DecodeError("packet too short for its time code")
        seconds = int.from_bytes(data[0:4], "big") + int.from_bytes(data[4:6], "big") / 65536
        time = datetime.fromtimestamp(seconds, UTC)
        data = data[CUC_BYTES:]
    packet = SpacePacket(
        apid=word0 & 0x7FF,
        packet_type="tc" if word0 & 0x1000 else "tm",
        sequence_count=word1 & MAX_SEQUENCE_COUNT,
        time=time,
        user_data=data,
    )
    return packet, crc_ok

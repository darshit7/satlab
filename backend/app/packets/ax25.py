"""AX.25 v2.2 UI frames (connectionless: beacons, APRS), encoded and decoded with bounds checks.

A frame is an address field (destination, source, up to 8 digipeaters; 7 bytes each), a control
byte, a protocol identifier and the information field, optionally followed by the 16-bit frame
check sequence. Flags and bit stuffing belong to the HDLC layer below and are not modelled: frames
archived by SatNOGS are stored without them (and usually without the FCS).

Reference: AX.25 Link Access Protocol for Amateur Packet Radio, v2.2 (TAPR), §3 and §6.
"""

import re
from dataclasses import dataclass

from app.packets.crc import crc16_x25
from app.packets.errors import DecodeError

MAX_DIGIPEATERS = 8
MAX_INFO_BYTES = 256  # N1, the default maximum information field
UI_CONTROL = 0x03
NO_LAYER_3 = 0xF0

_ADDRESS_BYTES = 7
_CALLSIGN = re.compile(r"^[A-Z0-9]{1,6}$")
_MIN_FRAME = 2 * _ADDRESS_BYTES + 2  # destination, source, control, PID


@dataclass(frozen=True)
class Address:
    callsign: str
    ssid: int = 0
    # Digipeaters: "has been repeated" (H). Destination/source: the command/response bit (C).
    flag: bool = False

    def __post_init__(self) -> None:
        if not _CALLSIGN.match(self.callsign) or not 0 <= self.ssid <= 15:
            raise ValueError(f"invalid AX.25 address {self.callsign}-{self.ssid}")

    def __str__(self) -> str:
        return self.callsign if self.ssid == 0 else f"{self.callsign}-{self.ssid}"


@dataclass(frozen=True)
class UiFrame:
    destination: Address
    source: Address
    digipeaters: tuple[Address, ...]
    info: bytes
    pid: int = NO_LAYER_3

    @property
    def path(self) -> str:
        """The digipeater path as APRS shows it: "ARISS*" once that hop has repeated it."""
        return ",".join(f"{d}{'*' if d.flag else ''}" for d in self.digipeaters)


def _encode_address(address: Address, last: bool) -> bytes:
    shifted = bytes(ord(c) << 1 for c in address.callsign.ljust(6))
    ssid = 0x60 | (address.ssid << 1) | (0x80 if address.flag else 0) | (1 if last else 0)
    return shifted + bytes([ssid])


def encode_ui(frame: UiFrame, *, fcs: bool) -> bytes:
    if len(frame.digipeaters) > MAX_DIGIPEATERS:
        raise ValueError("too many digipeaters")
    if len(frame.info) > MAX_INFO_BYTES:
        raise ValueError("information field too long")
    addresses = [frame.destination, frame.source, *frame.digipeaters]
    body = b"".join(_encode_address(a, i == len(addresses) - 1) for i, a in enumerate(addresses))
    body += bytes([UI_CONTROL, frame.pid]) + frame.info
    if fcs:
        body += crc16_x25(body).to_bytes(2, "little")
    return body


def _decode_address(chunk: bytes) -> Address:
    if any(b & 1 for b in chunk[:6]):
        raise DecodeError("address character with the extension bit set")
    callsign = bytes(b >> 1 for b in chunk[:6]).decode("ascii", errors="replace").rstrip(" ")
    ssid_byte = chunk[6]
    try:
        return Address(callsign, (ssid_byte >> 1) & 0x0F, bool(ssid_byte & 0x80))
    except ValueError:
        raise DecodeError("invalid callsign") from None


def decode_ui(data: bytes, *, fcs: bool) -> tuple[UiFrame, bool]:
    """Parse a UI frame. Returns the frame and whether its FCS checked (True when there is none).

    Raises ``DecodeError`` for anything that isn't a well-formed UI frame within the protocol's
    limits; every length is checked against the bytes present before it is used.
    """
    if fcs:
        if len(data) < _MIN_FRAME + 2:
            raise DecodeError("frame too short")
        body, check = data[:-2], int.from_bytes(data[-2:], "little")
        fcs_ok = crc16_x25(body) == check
    else:
        body, fcs_ok = data, True
    if len(body) < _MIN_FRAME:
        raise DecodeError("frame too short")

    addresses: list[Address] = []
    offset = 0
    while True:
        if len(addresses) == 2 + MAX_DIGIPEATERS:
            raise DecodeError("too many addresses")
        chunk = body[offset : offset + _ADDRESS_BYTES]
        if len(chunk) < _ADDRESS_BYTES:
            raise DecodeError("address field runs past the end of the frame")
        addresses.append(_decode_address(chunk))
        offset += _ADDRESS_BYTES
        if chunk[6] & 1:  # extension bit: last address
            break
    if len(addresses) < 2:
        raise DecodeError("missing source address")
    if len(body) < offset + 2:
        raise DecodeError("missing control or PID")
    control, pid = body[offset], body[offset + 1]
    if control & ~0x10 != UI_CONTROL:  # the poll/final bit may be set
        raise DecodeError("not a UI frame")
    info = body[offset + 2 :]
    if len(info) > MAX_INFO_BYTES:
        raise DecodeError("information field too long")
    destination, source, *digipeaters = addresses
    return UiFrame(destination, source, tuple(digipeaters), info, pid), fcs_ok

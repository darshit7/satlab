"""Per-satellite packet templates: how a satellite frames its telemetry and commands, and the named
fields inside them.

Templates are validated from ``app/data/seed/packet_templates.json`` (bounded like any other input)
and synced to ``packet_template``. Both ends of the simulated link use the same template: the
satellite encodes a frame, the channel may corrupt it, and the ground station decodes what arrives.
Real frames archived by SatNOGS go through the same decoders, which bound every length.

Payload encodings: ``binary`` (fixed fields at fixed offsets), ``csv`` (comma-separated ASCII after
a type tag, as CroCube sends) and ``aprs`` (APRS text in an AX.25 UI frame).
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.packets.ax25 import MAX_DIGIPEATERS, MAX_INFO_BYTES, Address, UiFrame, decode_ui, encode_ui
from app.packets.ccsds import MAX_APID, SpacePacket, decode_space_packet, encode_space_packet
from app.packets.errors import DecodeError

MAX_FRAME_BYTES = 512  # largest frame any template may produce or accept
MAX_FIELDS = 64
APRS_MAX_TEXT = 67  # APRS message text, without the {id

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
CommandName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]
Label = Annotated[str, Field(min_length=1, max_length=200)]
Unit = Annotated[str, Field(min_length=1, max_length=16)]
EnumLabels = Annotated[dict[str, Annotated[str, Field(max_length=32)]], Field(max_length=32)]

# Modelled values a simulated satellite can put in a field (``comms`` and ``command`` values come
# from the comms runtime, the rest from the subsystem health of ``sim/subsystems.py``).
SimSource = Literal[
    "power.soc_pct",
    "power.sunlit_fraction",
    "power.illumination",
    "thermal.temperature_c",
    "storage.fill_pct",
    "attitude.off_nadir_deg",
    "comms.sequence",
    "comms.beacon_interval_s",
    "comms.commands_received",
    "command.opcode",
    "command.sequence",
    "command.status",
]

DecodedValue = int | float | str | None


class _Strict(BaseModel):
    """Unknown keys are errors: a typo in the seed must not silently drop a setting."""

    model_config = ConfigDict(extra="forbid")


class SourceRef(_Strict):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https://[^\s<>\"']+$", max_length=500)


# ---------- framing ----------


class Callsign(_Strict):
    model_config = ConfigDict(frozen=True)

    callsign: str = Field(pattern=r"^[A-Z0-9]{1,6}$")
    ssid: int = Field(default=0, ge=0, le=15)

    def address(self, flag: bool = False) -> Address:
        return Address(self.callsign, self.ssid, flag)


class Ax25Framing(_Strict):
    type: Literal["ax25"] = "ax25"
    # Telemetry: the satellite's callsign (None: frames from anyone, e.g. through a digipeater).
    # Commands: the operator's (a placeholder; nothing is transmitted).
    source: Callsign | None = None
    destination: Callsign | None = None
    path: list[Callsign] = Field(default_factory=list, max_length=MAX_DIGIPEATERS)
    fcs: bool = False  # frames carry the FCS (SatNOGS archives frames without it)


class CcsdsFraming(_Strict):
    type: Literal["ccsds"] = "ccsds"
    apid: int = Field(ge=0, le=MAX_APID)
    time_code: bool = True  # CUC secondary header
    error_control: bool = True  # CRC-16 packet error control


Framing = Annotated[Ax25Framing | CcsdsFraming, Field(discriminator="type")]

# ---------- payloads ----------

_BINARY = {
    "u8": (1, False),
    "s8": (1, True),
    "u16": (2, False),
    "s16": (2, True),
    "u32": (4, False),
    "s32": (4, True),
    "u48": (6, False),
}


class _Scaled(_Strict):
    """value = raw x scale + offset; shown with ``unit`` (or an ``enum`` label, or as hex/time)."""

    name: Name
    label: Label | None = None
    scale: float = Field(default=1.0, allow_inf_nan=False)
    offset: float = Field(default=0.0, allow_inf_nan=False)
    unit: Unit | None = None
    enum: EnumLabels | None = None

    @model_validator(mode="after")
    def _nonzero_scale(self) -> Self:
        if self.scale == 0:
            raise ValueError("scale must not be zero")
        return self

    def value(self, raw: int) -> DecodedValue:
        if self.enum is not None:
            return self.enum.get(str(raw), f"unknown ({raw})")
        value = raw * self.scale + self.offset
        if float(self.scale).is_integer() and float(self.offset).is_integer():
            return int(value)
        return round(value, min(4, max(0, math.ceil(-math.log10(abs(self.scale))))))

    def raw(self, value: float) -> int:
        return round((value - self.offset) / self.scale)


class BinaryField(_Scaled):
    type: Literal["u8", "s8", "u16", "s16", "u32", "s32", "u48"]
    format: Literal["number", "hex", "unix_time"] = "number"
    match: int | None = None  # fixed value that identifies this message
    source: SimSource | None = None  # what a simulated satellite encodes here

    @property
    def size(self) -> int:
        return _BINARY[self.type][0]

    def bounds(self) -> tuple[int, int]:
        size, signed = _BINARY[self.type]
        return (
            (-(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1)
            if signed
            else (0, (1 << (8 * size)) - 1)
        )

    def shown(self, raw: int) -> DecodedValue:
        if self.format == "hex":
            return f"0x{raw:0{2 * self.size}X}"
        if self.format == "unix_time":
            # 2000-01-01 .. 2100-01-01; anything else is shown as the raw number
            if 946_684_800 <= raw < 4_102_444_800:
                return datetime.fromtimestamp(raw, UTC).isoformat()
            return raw
        return self.value(raw)


class BinaryPayload(_Strict):
    encoding: Literal["binary"] = "binary"
    endian: Literal["le", "be"]
    fields: list[BinaryField] = Field(min_length=1, max_length=MAX_FIELDS)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.fields)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _unique([f.name for f in self.fields], "field")
        if self.size > MAX_INFO_BYTES:
            raise ValueError("binary payload too large")
        for f in self.fields:
            lo, hi = f.bounds()
            if f.match is not None and not lo <= f.match <= hi:
                raise ValueError(f"match value of {f.name} doesn't fit {f.type}")
        return self


class CsvField(_Scaled):
    kind: Literal["int", "float", "hex", "text"]


class CsvPayload(_Strict):
    encoding: Literal["csv"] = "csv"
    tags: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,8}$")]] = Field(
        min_length=1, max_length=4
    )
    fields: list[CsvField] = Field(min_length=1, max_length=MAX_FIELDS)  # after the tag

    @model_validator(mode="after")
    def _check(self) -> Self:
        _unique([f.name for f in self.fields], "field")
        return self


class AprsPayload(_Strict):
    encoding: Literal["aprs"] = "aprs"


Payload = Annotated[BinaryPayload | CsvPayload | AprsPayload, Field(discriminator="encoding")]

# ---------- templates ----------


class TelemetryTemplate(_Strict):
    kind: Literal["telemetry"] = "telemetry"
    name: Name
    label: Label
    framing: Framing
    payload: Payload
    # How often the satellite sends it (from the source, or modelled); replayed frames keep it.
    # None: sent only in response to a command (an acknowledgement).
    interval_s: float | None = Field(default=None, ge=5.0, le=3600.0)
    downlink: Literal["simulated", "satnogs"]
    layout_sourced: bool  # false: an illustrative layout, labelled as such
    sources: list[SourceRef] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.downlink == "satnogs" and self.interval_s is None:
            raise ValueError("replayed telemetry needs an interval")
        if self.downlink == "simulated":
            if not isinstance(self.payload, BinaryPayload):
                raise ValueError("simulated telemetry needs a binary payload")
            for f in self.payload.fields:
                if f.match is None and f.source is None:
                    raise ValueError(f"simulated field {f.name} needs a source or a match value")
            if isinstance(self.framing, Ax25Framing) and not (
                self.framing.source and self.framing.destination
            ):
                raise ValueError("simulated AX.25 telemetry needs source and destination")
        return self


class CommandParam(_Strict):
    name: Name
    label: Label
    type: Literal["int", "text"]
    min: int | None = Field(default=None, ge=0, le=0xFFFF)
    max: int | None = Field(default=None, ge=0, le=0xFFFF)
    max_length: int = Field(default=32, ge=1, le=80)
    charset: Literal["printable", "callsign"] = "printable"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.type == "int" and (self.min is None or self.max is None or self.min > self.max):
            raise ValueError(f"int parameter {self.name} needs min <= max")
        return self


class CommandTemplate(_Strict):
    kind: Literal["command"] = "command"
    name: CommandName
    label: Label
    framing: Framing
    opcode: int | None = Field(default=None, ge=0, le=255)  # CCSDS telecommands
    params: list[CommandParam] = Field(default_factory=list, max_length=8)
    # What comes back down: an acknowledgement, a telemetry packet, or (through a digipeater)
    # the command frame itself, repeated.
    response: Literal["ack", "telemetry", "digipeat"]
    response_template: Name | None = None
    effect: Literal["beacon_interval"] | None = None  # changes this session's beacon cadence
    response_delay_s: float = Field(default=1.0, ge=0.0, le=10.0)  # on-board processing, modelled
    layout_sourced: bool
    sources: list[SourceRef] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _unique([p.name for p in self.params], "parameter")
        if isinstance(self.framing, CcsdsFraming) and self.opcode is None:
            raise ValueError("CCSDS commands need an opcode")
        if isinstance(self.framing, Ax25Framing):
            if not (self.framing.source and self.framing.destination):
                raise ValueError("AX.25 commands need source and destination")
            if [p.name for p in self.params] != ["addressee", "text"]:
                raise ValueError("AX.25 commands are APRS messages: addressee and text")
        if (self.response in ("ack", "telemetry")) != (self.response_template is not None):
            raise ValueError("ack and telemetry responses name their response template")
        if self.effect == "beacon_interval" and [p.name for p in self.params] != ["interval_s"]:
            raise ValueError("a beacon_interval command takes exactly one interval_s parameter")
        return self


def _unique(names: Sequence[str], what: str) -> None:
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate {what} names")


# ---------- decoded values ----------


@dataclass(frozen=True)
class DecodedField:
    name: str
    value: DecodedValue
    unit: str | None = None

    def as_json(self) -> dict[str, DecodedValue]:
        return {"name": self.name, "value": self.value, "unit": self.unit}


_PRINTABLE = re.compile(rb"^[\x20-\x7e]*$")


def _ascii(data: bytes) -> str:
    """Printable ASCII only: anything else is replaced, so text from a frame can't smuggle in
    control characters."""
    return "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in data)


def _decode_binary(payload: BinaryPayload, data: bytes) -> list[DecodedField]:
    if len(data) < payload.size:
        raise DecodeError("payload shorter than its template")
    fields: list[DecodedField] = []
    offset = 0
    for f in payload.fields:
        raw = int.from_bytes(
            data[offset : offset + f.size],
            "little" if payload.endian == "le" else "big",
            signed=_BINARY[f.type][1],
        )
        offset += f.size
        if f.match is not None:
            if raw != f.match:
                raise DecodeError("not this message")
            continue  # identifies the message; not a reading
        fields.append(DecodedField(f.name, f.shown(raw), f.unit if f.format == "number" else None))
    return fields


def _decode_csv(payload: CsvPayload, data: bytes) -> list[DecodedField]:
    if not _PRINTABLE.match(data):
        raise DecodeError("not a text beacon")
    tag, *values = data.decode("ascii").split(",")
    if tag not in payload.tags:
        raise DecodeError("not this message")
    fields: list[DecodedField] = []
    for i, f in enumerate(payload.fields):
        text = values[i].strip() if i < len(values) else ""
        # an unreadable (or missing) reading is None, not an unreadable frame
        value: DecodedValue = None
        if f.kind == "text":
            value = text[:32] or None
        elif f.kind == "hex" and _CSV_HEX.match(text):
            value = f"0x{int(text, 16):X}"
        elif f.kind == "int" and _CSV_INT.match(text):
            value = f.value(int(text))
        elif f.kind == "float" and _CSV_FLOAT.match(text):
            value = round(float(text) * f.scale + f.offset, 4)
        fields.append(DecodedField(f.name, value, f.unit if f.kind in ("int", "float") else None))
    return fields


_CSV_INT = re.compile(r"^-?[0-9]{1,12}$")
_CSV_HEX = re.compile(r"^[0-9A-Fa-f]{1,8}$")
_CSV_FLOAT = re.compile(r"^-?[0-9]{1,12}(\.[0-9]{1,9})?$")


_APRS_TYPES = {
    "!": "position",
    "=": "position",
    "/": "position",
    "@": "position",
    ":": "message",
    ">": "status",
    "`": "mic-e",
    "'": "mic-e",
    "T": "telemetry",
    ";": "object",
    ")": "item",
    "}": "third-party",
}


def _decode_aprs(data: bytes) -> list[DecodedField]:
    text = _ascii(data)
    kind = _APRS_TYPES.get(text[:1], "other")
    fields = [DecodedField("aprs_type", kind)]
    if kind == "message" and len(text) >= 11 and text[10] == ":":
        fields += [
            DecodedField("addressee", text[1:10].strip()),
            DecodedField("message", text[11:].split("{", 1)[0]),
        ]
    else:
        fields.append(DecodedField("text", text))
    return fields


def decode_payload(
    payload: BinaryPayload | CsvPayload | AprsPayload, data: bytes
) -> list[DecodedField]:
    match payload:
        case BinaryPayload():
            return _decode_binary(payload, data)
        case CsvPayload():
            return _decode_csv(payload, data)
        case AprsPayload():
            return _decode_aprs(data)


def encode_binary(payload: BinaryPayload, values: Mapping[str, float | None]) -> bytes:
    """Encode the fields from simulated ``values`` (keyed by source); out-of-range values clamp."""
    out = b""
    for f in payload.fields:
        if f.match is not None:
            raw = f.match
        else:
            value = values.get(f.source or "")
            raw = 0 if value is None or not math.isfinite(value) else f.raw(value)
        lo, hi = f.bounds()
        out += min(max(raw, lo), hi).to_bytes(
            f.size, "little" if payload.endian == "le" else "big", signed=_BINARY[f.type][1]
        )
    return out


# ---------- frames ----------


def frame_telemetry(
    template: TelemetryTemplate,
    values: Mapping[str, float | None],
    *,
    sequence: int,
    sim_time: datetime,
) -> bytes:
    """A simulated satellite's telemetry frame."""
    assert isinstance(template.payload, BinaryPayload)  # validated for simulated templates
    data = encode_binary(template.payload, values)
    return _frame(template.framing, data, sequence=sequence, sim_time=sim_time, packet_type="tm")


def _frame(
    framing: Ax25Framing | CcsdsFraming,
    data: bytes,
    *,
    sequence: int,
    sim_time: datetime,
    packet_type: Literal["tm", "tc"],
) -> bytes:
    match framing:
        case CcsdsFraming():
            packet = SpacePacket(
                apid=framing.apid,
                packet_type=packet_type,
                sequence_count=sequence % 0x4000,
                time=sim_time if framing.time_code else None,
                user_data=data,
            )
            return encode_space_packet(packet, error_control=framing.error_control)
        case Ax25Framing():
            assert framing.source is not None and framing.destination is not None
            frame = UiFrame(
                destination=framing.destination.address(),
                source=framing.source.address(),
                digipeaters=tuple(c.address() for c in framing.path),
                info=data,
            )
            return encode_ui(frame, fcs=framing.fcs)


def read_telemetry(
    template: TelemetryTemplate, raw: bytes
) -> tuple[list[DecodedField] | None, bool]:
    """Decode a received frame with ``template``: (fields, check passed).

    A frame whose CRC fails returns no fields: its contents can't be trusted. ``DecodeError`` means
    the bytes aren't this template's frame at all (or were too damaged to tell).
    """
    if len(raw) > MAX_FRAME_BYTES:
        raise DecodeError("frame too long")
    framing = template.framing
    header: list[DecodedField]
    match framing:
        case CcsdsFraming():
            packet, ok = decode_space_packet(
                raw, error_control=framing.error_control, max_bytes=MAX_FRAME_BYTES
            )
            if packet.apid != framing.apid:
                raise DecodeError("not this APID")
            data = packet.user_data
            header = [DecodedField("sequence", packet.sequence_count)]
            if packet.time is not None:
                header.append(DecodedField("time", packet.time.isoformat(timespec="milliseconds")))
        case Ax25Framing():
            frame, ok = decode_ui(raw, fcs=framing.fcs)
            if framing.source is not None and (frame.source.callsign != framing.source.callsign):
                raise DecodeError("not this satellite")
            data = frame.info
            header = [
                DecodedField("source", str(frame.source)),
                DecodedField("destination", str(frame.destination)),
            ]
            if frame.digipeaters:
                header.append(DecodedField("path", frame.path))
    if not ok:
        return None, False
    return header + decode_payload(template.payload, data), True


def identify(
    templates: Sequence[TelemetryTemplate], raw: bytes
) -> tuple[TelemetryTemplate, list[DecodedField]] | None:
    """The first template that decodes a (real, archived) frame cleanly, with its fields."""
    for template in templates:
        try:
            fields, ok = read_telemetry(template, raw)
        except DecodeError:
            continue
        if ok and fields is not None:
            return template, fields
    return None


# ---------- commands ----------

CommandParams = dict[str, int | str]


def validate_params(template: CommandTemplate, params: Mapping[str, object]) -> CommandParams:
    """The command's parameters, checked against its template. Raises ``ValueError`` (with a
    message safe to show the client) for unknown, missing or out-of-range values."""
    unknown = set(params) - {p.name for p in template.params}
    if unknown:
        raise ValueError(f"unknown parameter {sorted(unknown)[0]!r}")
    clean: CommandParams = {}
    for p in template.params:
        if p.name not in params:
            raise ValueError(f"missing parameter {p.name!r}")
        value = params[p.name]
        if p.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{p.name} must be a whole number")
            assert p.min is not None and p.max is not None
            if not p.min <= value <= p.max:
                raise ValueError(f"{p.name} must be between {p.min} and {p.max}")
            clean[p.name] = value
        else:
            if not isinstance(value, str) or not 1 <= len(value) <= p.max_length:
                raise ValueError(f"{p.name} must be 1 to {p.max_length} characters")
            pattern = (
                r"^[A-Z0-9]{1,6}(-[0-9]{1,2})?$" if p.charset == "callsign" else r"^[\x20-\x7e]+$"
            )
            if not re.match(pattern, value):
                raise ValueError(f"{p.name} has characters that can't be sent")
            clean[p.name] = value
    return clean


def frame_command(
    template: CommandTemplate, params: CommandParams, *, sequence: int, sim_time: datetime
) -> bytes:
    """The uplink frame for a command (simulated: it is never transmitted)."""
    match template.framing:
        case CcsdsFraming():
            assert template.opcode is not None
            data = bytes([template.opcode])
            for p in template.params:
                value = params[p.name]
                data += (
                    value.to_bytes(2, "big")
                    if isinstance(value, int)
                    else (bytes([len(value)]) + value.encode("ascii"))
                )
            return _frame(
                template.framing, data, sequence=sequence, sim_time=sim_time, packet_type="tc"
            )
        case Ax25Framing():
            # An APRS message: ":ADDRESSEE:text" (addressee padded to nine characters).
            info = f":{str(params.get('addressee', '')).ljust(9)[:9]}:{params.get('text', '')}"
            return _frame(
                template.framing,
                info.encode("ascii"),
                sequence=sequence,
                sim_time=sim_time,
                packet_type="tc",
            )


def digipeat(raw: bytes, *, fcs: bool) -> bytes:
    """The frame as a digipeater repeats it: the first unrepeated hop of its path marked done."""
    frame, _ = decode_ui(raw, fcs=fcs)
    hops = list(frame.digipeaters)
    for i, hop in enumerate(hops):
        if not hop.flag:
            hops[i] = Address(hop.callsign, hop.ssid, True)
            break
    return encode_ui(
        UiFrame(frame.destination, frame.source, tuple(hops), frame.info, frame.pid), fcs=fcs
    )

"""Communications with the satellite in contact: frames down, commands up (simulated uplink).

One ``CommsRuntime`` per session, stepped by the sim tick from in-memory data only (no I/O). The
station's antenna follows the selection, so the contact is the selected satellite while it is above
the mask, has packet templates and the clock runs no faster than comms can follow.

Downlink, per periodic template, on an absolute sim-time grid (multiples of its interval since the
Unix epoch, like the subsystem model), at most ``max_packets_per_tick`` per tick:
* simulated satellites encode housekeeping from the modelled subsystem health; the frame goes
  through the channel (bit errors from the real link margin) and the station decodes what arrives;
* SatNOGS-fed satellites replay real archived frames, cycling through the archive.

Commands: queued by the session manager (already validated and stored) or released by pass
automation at AOS, sent on the next tick in contact, answered after twice the light time plus the
template's on-board delay. The answer comes down through the same channel, so a weak link can lose
an acknowledgement.

Everything worth keeping goes to ``sink`` (frames, status changes), which must not block.
"""

import math
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from app.models.ws import (
    ArchiveStatus,
    CommandStatus,
    CommandStatusMessage,
    ContactState,
    DecodedFieldOut,
    HealthState,
    PacketKind,
    PacketMessage,
    SatnogsOrigin,
    ScheduledPass,
    SystemCheck,
)
from app.packets.errors import DecodeError
from app.packets.table import CommsTable, SatelliteTemplates
from app.packets.templates import (
    CommandParams,
    CommandTemplate,
    DecodedField,
    TelemetryTemplate,
    digipeat,
    frame_command,
    frame_telemetry,
    identify,
    read_telemetry,
)
from app.sim.channel import downlink, light_time_s, packet_rng

ILLUMINATION_CODE = {"sunlit": 0, "penumbra": 1, "eclipse": 2}


# ---------- what the runtime reads ----------


@dataclass(frozen=True)
class ArchivedFrame:
    """A real frame from the SatNOGS archive, already identified against a template."""

    raw: bytes
    template: str
    observation_id: int | None
    station_id: int | None
    received_at: datetime | None


class FrameArchive(Protocol):
    def frames(self, norad_id: int, template: str) -> Sequence[ArchivedFrame]: ...

    def status(self, norad_id: int) -> ArchiveStatus: ...


class NoArchive:
    def frames(self, norad_id: int, template: str) -> Sequence[ArchivedFrame]:
        return ()

    def status(self, norad_id: int) -> ArchiveStatus:
        return "not_configured"


@dataclass(frozen=True)
class ContactLook:
    """The selected satellite this tick, as comms needs it."""

    norad_id: int
    elevation_deg: float
    above_mask: bool
    range_m: float
    margin_db: float | None  # None without a link profile, or below the horizon
    has_link: bool  # a published link budget: the channel is modelled


# ---------- what the runtime writes ----------


@dataclass(frozen=True)
class FrameRecord:
    session_id: int
    norad_id: int
    template_id: int | None
    command_id: int | None
    sim_time: datetime
    raw: bytes | None
    decoded: list[dict[str, object]] | None
    source: str
    crc_ok: bool
    origin: dict[str, object] | None


@dataclass(frozen=True)
class StatusRecord:
    command_id: int
    status: CommandStatus
    check: dict[str, object] | None = None  # the system check at AOS, for a booked command


Record = FrameRecord | StatusRecord


@dataclass
class OutgoingCommand:
    """A command accepted for the satellite in contact (its row already stored as ``queued``)."""

    command_id: int
    norad_id: int
    template: CommandTemplate
    params: CommandParams
    issued_at: datetime
    sequence: int | None = None
    sent_at: datetime | None = None
    uplink: bytes | None = None
    response_due: datetime | None = None
    # released by pass automation: the pass it was booked for and the check that let it go
    scheduled_pass: ScheduledPass | None = None
    check: SystemCheck | None = None


@dataclass(frozen=True)
class CommsConfig:
    max_time_scale: float = 60.0
    max_packets_per_tick: int = 4
    recent_packets: int = 50


def _fields_out(fields: Sequence[DecodedField] | None) -> list[DecodedFieldOut] | None:
    return None if fields is None else [DecodedFieldOut(**f.as_json()) for f in fields]


class CommsRuntime:
    def __init__(
        self,
        session_id: int,
        config: CommsConfig,
        archive: FrameArchive,
        sink: Callable[[Record], None],
    ) -> None:
        self.session_id = session_id
        self.config = config
        self._archive = archive
        self._sink = sink
        self.contact: ContactState | None = None
        self._seq = 0
        self._beacons_from: tuple[int, datetime] | None = None  # (NORAD ID, last emitted up to)
        self._intervals: dict[int, float] = {}  # SET_BEACON_INTERVAL, per satellite
        self._commands_received: dict[int, int] = {}
        self._cursor: dict[tuple[int, str], int] = {}  # replay position per template
        self._outgoing: list[OutgoingCommand] = []
        self._recent: deque[PacketMessage] = deque(maxlen=config.recent_packets)

    @property
    def recent(self) -> list[PacketMessage]:
        """The latest packets, for a client that (re)connects."""
        return list(self._recent)

    def can_command(self, norad_id: int) -> bool:
        return (
            self.contact is not None
            and self.contact.norad_id == norad_id
            and self.contact.state == "active"
        )

    def submit(self, command: OutgoingCommand) -> CommandStatusMessage:
        self._outgoing.append(command)
        return self._status_message(command, "queued", None, command.issued_at)

    # ----- the tick -----

    def step(
        self,
        sim_time: datetime,
        look: ContactLook | None,
        health: HealthState | None,
        table: CommsTable,
        *,
        time_scale: float,
        paused: bool,
    ) -> list[BaseModel]:
        sat = table.by_norad.get(look.norad_id) if look is not None else None
        self.contact = self._contact(look, sat, time_scale=time_scale, paused=paused)
        active = self.contact is not None and self.contact.state == "active"
        out: list[BaseModel] = []
        if active and look is not None and sat is not None:
            out += self._commands(sim_time, look, health, sat)
            out += self._beacons(sim_time, look, health, sat)
        else:
            self._beacons_from = None
            out += self._drop_commands(sim_time)
        return out

    def _contact(
        self,
        look: ContactLook | None,
        sat: SatelliteTemplates | None,
        *,
        time_scale: float,
        paused: bool,
    ) -> ContactState | None:
        if look is None:
            return None
        light = light_time_s(look.range_m) if look.elevation_deg >= 0 else None
        if sat is None:
            return ContactState(
                norad_id=look.norad_id,
                state="no_template",
                downlink=None,
                channel_modelled=look.has_link,
                one_way_light_time_s=light,
            )
        replayed = bool(sat.replayed)
        state = (
            "below_mask"
            if not look.above_mask
            else "too_fast"
            if not paused and time_scale > self.config.max_time_scale
            else "active"
        )
        return ContactState(
            norad_id=look.norad_id,
            state=state,
            downlink="satnogs" if replayed else "simulated",
            channel_modelled=look.has_link and not replayed,
            one_way_light_time_s=light,
            archive=self._archive.status(look.norad_id) if replayed else None,
        )

    # ----- commands -----

    def _drop_commands(self, sim_time: datetime) -> list[BaseModel]:
        """No contact: queued commands are aborted, sent ones lose their answer."""
        out: list[BaseModel] = []
        for command in self._outgoing:
            if command.sent_at is None:
                out.append(
                    self._finish(command, "aborted", "contact lost before it was sent", sim_time)
                )
            else:
                out.append(
                    self._finish(command, "failed", "contact lost before the answer", sim_time)
                )
        self._outgoing.clear()
        return out

    def _commands(
        self,
        sim_time: datetime,
        look: ContactLook,
        health: HealthState | None,
        sat: SatelliteTemplates,
    ) -> list[BaseModel]:
        out: list[BaseModel] = []
        for command in list(self._outgoing):
            if command.norad_id != look.norad_id:
                self._outgoing.remove(command)
                out.append(
                    self._finish(command, "aborted", "another satellite is in contact", sim_time)
                )
                continue
            if sim_time < command.issued_at:
                self._outgoing.remove(command)
                out.append(self._finish(command, "aborted", "the clock was moved back", sim_time))
                continue
            if command.sent_at is None:
                out += self._send(command, sim_time, look)
            elif command.response_due is not None and command.response_due <= sim_time:
                self._outgoing.remove(command)
                out += self._answer(command, sim_time, look, health, sat)
        return out

    def _send(
        self, command: OutgoingCommand, sim_time: datetime, look: ContactLook
    ) -> list[BaseModel]:
        template = command.template
        seq = self._next_seq()
        raw = frame_command(template, command.params, sequence=seq, sim_time=sim_time)
        light = light_time_s(look.range_m)
        command.sequence, command.sent_at, command.uplink = seq, sim_time, raw
        command.response_due = sim_time + timedelta(seconds=2 * light + template.response_delay_s)
        self._commands_received[command.norad_id] = (
            self._commands_received.get(command.norad_id, 0) + 1
        )
        decoded = [DecodedFieldOut(name="command", value=template.name)] + [
            DecodedFieldOut(name=k, value=v) for k, v in command.params.items()
        ]
        packet = PacketMessage(
            seq=seq,
            norad_id=command.norad_id,
            direction="up",
            kind="command",
            template=template.name,
            label=template.label,
            framing=template.framing.type,
            source="simulated",
            sim_time=sim_time,
            size_bytes=len(raw),
            raw_hex=raw.hex(),
            delivered=True,
            crc_ok=True,
            bit_errors=None,  # no published uplink budget: the uplink channel isn't modelled
            ber=None,
            margin_db=None,
            one_way_light_time_s=round(light, 6),
            decoded=decoded,
            layout_sourced=template.layout_sourced,
            command_id=command.command_id,
        )
        self._recent.append(packet)
        return [packet, self._finish(command, "sent", None, sim_time)]

    def _answer(
        self,
        command: OutgoingCommand,
        sim_time: datetime,
        look: ContactLook,
        health: HealthState | None,
        sat: SatelliteTemplates,
    ) -> list[BaseModel]:
        template = command.template
        if template.effect == "beacon_interval":
            self._intervals[command.norad_id] = float(command.params["interval_s"])
        assert command.uplink is not None and command.sequence is not None
        seq = self._next_seq()
        found: tuple[int | None, TelemetryTemplate] | None
        kind: PacketKind
        if template.response == "digipeat":
            fcs = template.framing.type == "ax25" and template.framing.fcs
            raw = digipeat(command.uplink, fcs=fcs)
            match = identify([t for _, t in sat.telemetry], raw)
            found = (
                None if match is None else (sat.telemetry_named(match[0].name) or (None, match[0]))
            )
            kind = "digipeat"
        else:
            assert template.response_template is not None
            found = sat.telemetry_named(template.response_template)
            values = self._values(health, command.norad_id, sat) | {
                "command.opcode": template.opcode,
                "command.sequence": command.sequence,
                "command.status": 0,
            }
            raw = b""
            if found is not None:
                raw = frame_telemetry(found[1], values, sequence=seq, sim_time=sim_time)
            kind = "ack" if template.response == "ack" else "response"
        if found is None:
            return [self._finish(command, "failed", "no template for the answer", sim_time)]
        template_id, telemetry = found
        packet = self._downlink(
            seq,
            raw,
            telemetry,
            template_id,
            kind,
            look,
            sim_time,
            source="simulated",
            command_id=command.command_id,
        )
        if packet.delivered and packet.crc_ok:
            return [packet, self._finish(command, "acked", None, sim_time)]
        reason = (
            "answer lost: no line of sight"
            if not packet.delivered
            else "answer corrupted on the way down"
        )
        return [packet, self._finish(command, "failed", reason, sim_time)]

    def _finish(
        self,
        command: OutgoingCommand,
        status: CommandStatus,
        reason: str | None,
        sim_time: datetime,
    ) -> CommandStatusMessage:
        self._sink(StatusRecord(command.command_id, status))
        return self._status_message(command, status, reason, sim_time)

    @staticmethod
    def _status_message(
        command: OutgoingCommand, status: CommandStatus, reason: str | None, sim_time: datetime
    ) -> CommandStatusMessage:
        return CommandStatusMessage(
            command_id=command.command_id,
            norad_id=command.norad_id,
            command=command.template.name,
            status=status,
            reason=reason,
            sim_time=sim_time,
            scheduled_pass=command.scheduled_pass,
            check=command.check,
        )

    # ----- downlink -----

    def _beacons(
        self,
        sim_time: datetime,
        look: ContactLook,
        health: HealthState | None,
        sat: SatelliteTemplates,
    ) -> list[BaseModel]:
        since = self._beacons_from
        if since is None or since[0] != look.norad_id or sim_time < since[1]:
            # a new contact (or a jump back) starts now: no backlog of past beacons
            self._beacons_from = (look.norad_id, sim_time)
            return []
        start, end = since[1].timestamp(), sim_time.timestamp()
        due: list[tuple[float, int, TelemetryTemplate]] = []
        for template_id, template in sat.periodic:
            interval = self._interval(look.norad_id, template)
            first, last = math.floor(start / interval) + 1, math.floor(end / interval)
            first = max(first, last - self.config.max_packets_per_tick + 1)
            due += [(k * interval, template_id, template) for k in range(first, last + 1)]
        due.sort(key=lambda d: d[0])
        self._beacons_from = (look.norad_id, sim_time)
        out: list[BaseModel] = []
        for instant, template_id, template in due[-self.config.max_packets_per_tick :]:
            at = datetime.fromtimestamp(instant, UTC)
            if template.downlink == "simulated":
                if health is None:
                    continue
                seq = self._next_seq()
                values = self._values(health, look.norad_id, sat)
                raw = frame_telemetry(template, values, sequence=seq, sim_time=at)
                out.append(
                    self._downlink(
                        seq, raw, template, template_id, "telemetry", look, at, source="simulated"
                    )
                )
            else:
                frames = self._archive.frames(look.norad_id, template.name)
                if not frames:
                    continue
                key = (look.norad_id, template.name)
                frame = frames[self._cursor.get(key, 0) % len(frames)]
                self._cursor[key] = self._cursor.get(key, 0) + 1
                origin = SatnogsOrigin(
                    observation_id=frame.observation_id,
                    station_id=frame.station_id,
                    received_at=frame.received_at,
                )
                out.append(
                    self._downlink(
                        self._next_seq(),
                        frame.raw,
                        template,
                        template_id,
                        "telemetry",
                        look,
                        at,
                        source="satnogs",
                        origin=origin,
                    )
                )
        return out

    def _interval(self, norad_id: int, template: TelemetryTemplate) -> float:
        assert template.interval_s is not None
        if template.downlink == "simulated":
            return self._intervals.get(norad_id, template.interval_s)
        return template.interval_s

    def _values(
        self, health: HealthState | None, norad_id: int, sat: SatelliteTemplates
    ) -> dict[str, float | None]:
        hk = next((t for _, t in sat.periodic if t.downlink == "simulated"), None)
        values: dict[str, float | None] = {
            "comms.beacon_interval_s": self._interval(norad_id, hk) if hk is not None else None,
            "comms.commands_received": self._commands_received.get(norad_id, 0),
        }
        if health is not None:
            values |= {
                "power.soc_pct": health.power.soc_pct,
                "power.sunlit_fraction": health.power.sunlit_fraction,
                "power.illumination": ILLUMINATION_CODE[health.power.illumination],
                "thermal.temperature_c": health.thermal.temperature_c,
                "storage.fill_pct": health.storage.fill_pct,
                "attitude.off_nadir_deg": health.attitude.off_nadir_deg,
            }
        return values

    def _downlink(
        self,
        seq: int,
        raw: bytes,
        template: TelemetryTemplate,
        template_id: int | None,
        kind: PacketKind,
        look: ContactLook,
        sim_time: datetime,
        *,
        source: str,
        origin: SatnogsOrigin | None = None,
        command_id: int | None = None,
    ) -> PacketMessage:
        modelled = look.has_link and source == "simulated"
        delivery = downlink(
            raw, look.margin_db, modelled=modelled, rng=packet_rng(self.session_id, seq)
        )
        fields: list[DecodedField] | None = None
        crc_ok: bool | None = None
        if delivery.delivered:
            try:
                fields, crc_ok = read_telemetry(template, delivery.frame)
            except DecodeError:  # too damaged to tell what it was
                fields, crc_ok = None, False
        packet = PacketMessage(
            seq=seq,
            norad_id=look.norad_id,
            direction="down",
            kind=kind,
            template=template.name,
            label=template.label,
            framing=template.framing.type,
            source="satnogs" if source == "satnogs" else "simulated",
            sim_time=sim_time,
            size_bytes=len(delivery.frame),
            raw_hex=delivery.frame.hex(),
            delivered=delivery.delivered,
            crc_ok=crc_ok,
            bit_errors=delivery.bit_errors,
            ber=delivery.ber,
            margin_db=look.margin_db if modelled else None,
            one_way_light_time_s=round(light_time_s(look.range_m), 6),
            decoded=_fields_out(fields),
            layout_sourced=template.layout_sourced,
            command_id=command_id,
            origin=origin,
        )
        self._recent.append(packet)
        if delivery.delivered:
            self._sink(
                FrameRecord(
                    session_id=self.session_id,
                    norad_id=look.norad_id,
                    template_id=template_id,
                    command_id=command_id,
                    sim_time=sim_time,
                    raw=delivery.frame,
                    decoded=None if fields is None else [f.as_json() for f in fields],
                    source=packet.source,
                    crc_ok=bool(crc_ok),
                    origin=None if origin is None else origin.model_dump(mode="json"),
                )
            )
        return packet

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

"""Pass automation: commands booked for a future pass, sent at AOS unless a subsystem is red.

One ``PassAutomation`` per session, stepped by the sim tick from in-memory data only (no I/O). The
station has one antenna, and it follows the selection, so automation takes it:

* ``lead_s`` before a booked pass's AOS the station turns to that satellite (the session's selection
  becomes it, and the client follows ``Snapshot.automation``) and keeps it until LOS. If the clock
  runs faster than comms can follow, it jumps forward to that moment and drops to real time, so the
  pass isn't skipped between two ticks.
* At the first tick in contact after AOS it checks the systems once: the selected satellite's
  ``HealthState``, the same one the inspector shows. No subsystem red (amber passes; a subsystem
  without data isn't checked): the pass's commands are released to comms, which sends them. Any
  red: they are held (not sent) and a critical alarm is raised.
* A pass the clock jumps past, or one with no contact at all (the clock sped up again), is missed:
  its commands are aborted with a warning alarm. Selecting another satellite before the check
  hands the antenna back and aborts them; moving the clock back before a command was booked
  aborts it too.

Every status change and alarm goes to ``sink``, which must not block.
"""

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sgp4.api import Satrec

from app.models.ws import (
    SUBSYSTEMS,
    AlarmMessage,
    AlarmSeverity,
    AutomationState,
    CommandStatus,
    CommandStatusMessage,
    ContactState,
    HealthState,
    Rag,
    ScheduledPass,
    Subsystem,
    SystemCheck,
)
from app.packets.templates import CommandParams, CommandTemplate
from app.sim.comms import OutgoingCommand, StatusRecord
from app.sim.geometry import Observer
from app.sim.passes import Span, find_spans

# A requested AOS matches a predicted pass this close to it (the client lists the server's own
# predictions, so this only absorbs a TLE refresh in between).
MATCH_TOLERANCE_S = 120.0
# Alarms kept for clients that (re)connect.
KEPT_ALARMS = 50


# ---------- passes ----------


@dataclass(frozen=True)
class PassSearch:
    passes: list[Span]  # complete passes (AOS and LOS both found), in time order
    always_visible: bool  # above the mask throughout: no AOS to wait for


def search_passes(
    satrec: Satrec,
    observer: Observer,
    mask_deg: float,
    start: datetime,
    duration_s: float,
    step_s: float,
) -> PassSearch:
    [spans] = find_spans([satrec], observer, start, duration_s, step_s, mask_deg)
    return PassSearch(
        passes=[s for s in spans if s.aos is not None and s.los is not None],
        always_visible=len(spans) == 1 and spans[0].aos is None and spans[0].los is None,
    )


def nearest_pass(
    passes: Sequence[Span], aos: datetime, tolerance_s: float = MATCH_TOLERANCE_S
) -> Span | None:
    """The pass whose AOS is nearest ``aos``, if within the tolerance."""
    best: Span | None = None
    best_s = tolerance_s
    for candidate in passes:
        if candidate.aos is None:
            continue
        offset_s = abs((candidate.aos - aos).total_seconds())
        if offset_s <= best_s:
            best, best_s = candidate, offset_s
    return best


# ---------- the check ----------


def system_check(norad_id: int, health: HealthState | None, sim_time: datetime) -> SystemCheck:
    """Go unless a subsystem is red. Without health for this satellite nothing can be checked:
    no go."""
    if health is None or health.norad_id != norad_id:
        return SystemCheck(
            sim_time=sim_time,
            norad_id=norad_id,
            rollup=None,
            go=False,
            subsystems=dict.fromkeys(SUBSYSTEMS),
        )
    rags: dict[Subsystem, Rag | None] = {
        "power": health.power.rag,
        "thermal": health.thermal.rag,
        "storage": health.storage.rag,
        "attitude": health.attitude.rag,
        "rf": health.rf.rag,
    }
    return SystemCheck(
        sim_time=sim_time,
        norad_id=norad_id,
        rollup=health.rollup,
        go="red" not in rags.values(),
        subsystems=rags,
    )


def _red_details(health: HealthState, red: Sequence[Subsystem]) -> list[str]:
    """Each red subsystem with the value that made it red, in words."""
    words: dict[Subsystem, str] = {
        "power": f"power red (battery {health.power.soc_pct:.0f}%)",
        "thermal": f"thermal red ({health.thermal.temperature_c:.0f} °C)",
        "storage": (
            f"storage red ({health.storage.fill_pct:.0f}% full)"
            if health.storage.fill_pct is not None
            else "storage red"
        ),
        "attitude": (
            f"attitude red (station {health.attitude.off_nadir_deg:.0f}° off nadir, beam "
            f"±{health.attitude.half_beamwidth_deg:.0f}°)"
            if health.attitude.off_nadir_deg is not None
            else "attitude red"
        ),
        "rf": (
            f"RF red (margin {health.rf.margin_db:.1f} dB)"
            if health.rf.margin_db is not None
            else "RF red"
        ),
    }
    return [words[s] for s in red]


# ---------- what the automation holds ----------


@dataclass(frozen=True)
class BookedPass:
    norad_id: int
    satellite_name: str
    aos: datetime
    los: datetime
    max_elev_deg: float

    @property
    def key(self) -> tuple[int, datetime]:
        return (self.norad_id, self.aos)

    def out(self) -> ScheduledPass:
        return ScheduledPass(aos_time=self.aos, los_time=self.los, max_elev_deg=self.max_elev_deg)


@dataclass
class ScheduledCommand:
    """A command booked for a pass (its row already stored as ``scheduled``)."""

    command_id: int
    norad_id: int
    template: CommandTemplate
    params: CommandParams
    issued_at: datetime
    booked: BookedPass


@dataclass
class _Window:
    """The pass automation holds the antenna for."""

    booked: BookedPass
    checked: bool = False
    released: int = 0  # commands the check released or held
    saw_aos: bool = False  # a tick fell inside the pass


@dataclass(frozen=True)
class ClockCatch:
    """Drop the clock to real time, after jumping forward to ``jump_to`` (when set)."""

    jump_to: datetime | None


@dataclass(frozen=True)
class AlarmRecord:
    session_id: int
    seq: int
    sim_time: datetime
    severity: AlarmSeverity
    subsystem: Subsystem | None
    norad_id: int | None
    message: str


@dataclass(frozen=True)
class AlarmAckRecord:
    session_id: int
    seq: int


AutomationRecord = StatusRecord | AlarmRecord | AlarmAckRecord


def _hm(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%H:%M")


def _names(commands: Sequence[ScheduledCommand]) -> str:
    return ", ".join(dict.fromkeys(c.template.name for c in commands))


class PassAutomation:
    def __init__(
        self,
        session_id: int,
        sink: Callable[[AutomationRecord], None],
        *,
        lead_s: float = 60.0,
        max_time_scale: float = 60.0,  # comms can't follow a faster clock
    ) -> None:
        self.session_id = session_id
        self.sink = sink
        self.lead = timedelta(seconds=lead_s)
        self.max_time_scale = max_time_scale
        self._booked: list[ScheduledCommand] = []  # waiting for their pass, by AOS
        self._window: _Window | None = None
        self._slowed_for: tuple[int, datetime] | None = None  # the pass the clock was slowed for
        self._alarm_seq = 0
        self._alarms: deque[AlarmMessage] = deque(maxlen=KEPT_ALARMS)

    @property
    def pending_count(self) -> int:
        return len(self._booked)

    @property
    def alarms(self) -> list[AlarmMessage]:
        return list(self._alarms)

    def pending_statuses(self) -> list[CommandStatusMessage]:
        """The latest status of every command waiting for its pass, for a client that connects."""
        return [self._status(c, "scheduled", None, c.issued_at) for c in self._booked]

    def restore(self, alarms: Sequence[AlarmMessage], last_seq: int) -> None:
        """After a restart: the session's recent alarms and where their numbering got to."""
        self._alarms.extend(alarms)
        self._alarm_seq = max(self._alarm_seq, last_seq)

    # ----- booking (from the session manager) -----

    def conflict(self, booked: BookedPass) -> BookedPass | None:
        """Another satellite's pass that needs the antenna at the same time, if any."""
        others = [c.booked for c in self._booked]
        if self._window is not None:
            others.append(self._window.booked)
        start = booked.aos - self.lead
        for other in others:
            if other.norad_id != booked.norad_id and (
                start < other.los and other.aos - self.lead < booked.los
            ):
                return other
        return None

    def book(self, command: ScheduledCommand) -> CommandStatusMessage:
        # a command for the pass automation already holds the antenna for is checked with it
        self._booked.append(command)
        self._booked.sort(key=lambda c: (c.booked.aos, c.command_id))
        return self._status(command, "scheduled", None, command.issued_at)

    def cancel(self, command_id: int, sim_time: datetime) -> CommandStatusMessage | None:
        command = next((c for c in self._booked if c.command_id == command_id), None)
        if command is None:
            return None
        self._booked.remove(command)
        return self._finish(command, "aborted", "cancelled by the operator", sim_time)

    def ack(self, seq: int) -> AlarmMessage | None:
        for i, alarm in enumerate(self._alarms):
            if alarm.alarm_id == seq:
                if not alarm.acknowledged:
                    self._alarms[i] = alarm = alarm.model_copy(update={"acknowledged": True})
                    self.sink(AlarmAckRecord(self.session_id, seq))
                return alarm
        return None

    def operator_selected(self, norad_id: int | None, sim_time: datetime) -> list[BaseModel]:
        """The operator picked a satellite: away from the one automation holds the antenna for,
        that hands the antenna back (and aborts the pass's commands if not checked yet)."""
        window = self._window
        if window is None or norad_id == window.booked.norad_id:
            return []
        self._window = None
        if window.checked:
            return []  # released commands are comms' now: it aborts them out of contact
        out: list[BaseModel] = []
        reason = "the operator took the antenna before AOS"
        for command in self._take(window.booked):
            out.append(self._finish(command, "aborted", reason, sim_time))
        return out

    # ----- the tick -----

    def clock_catch(
        self, sim_time: datetime, *, time_scale: float, paused: bool, tick_s: float
    ) -> ClockCatch | None:
        """Too fast for comms with a booked pass coming: land on its antenna time at real time."""
        if paused or time_scale <= self.max_time_scale or not self._booked:
            return None
        nxt = next((c.booked for c in self._booked if c.booked.los > sim_time), None)
        if nxt is None:
            return None
        start = nxt.aos - self.lead
        if sim_time >= start:  # already inside (a manual jump, or a late tick): just slow down
            self._slowed_for = nxt.key
            return ClockCatch(jump_to=None)
        # the next tick would land past it (twice the nominal advance: ticks can run late)
        if sim_time + timedelta(seconds=2 * time_scale * tick_s) >= start:
            self._slowed_for = nxt.key
            return ClockCatch(jump_to=start)
        return None

    def prepare(self, sim_time: datetime) -> tuple[int | None, list[BaseModel]]:
        """At the start of a tick: drop what can no longer go, and find the pass that holds the
        antenna now. Returns the satellite to turn to (when automation newly takes the antenna)."""
        out: list[BaseModel] = []
        for command in [c for c in self._booked if sim_time < c.issued_at]:
            self._booked.remove(command)
            out.append(self._finish(command, "aborted", "the clock was moved back", sim_time))

        window = self._window
        if window is not None and not (
            window.booked.aos - self.lead <= sim_time < window.booked.los
        ):
            self._window = None  # past LOS, or the clock moved back before the antenna time

        out += self._missed(sim_time, window)

        if self._window is None:
            due = next(
                (
                    c.booked
                    for c in self._booked
                    if c.booked.aos - self.lead <= sim_time < c.booked.los
                ),
                None,
            )
            if due is not None:
                self._window = _Window(due)
                return due.norad_id, out
        return None, out

    def _missed(self, sim_time: datetime, window: _Window | None) -> list[BaseModel]:
        """Commands whose pass ended before they could be checked."""
        out: list[BaseModel] = []
        passes: dict[tuple[int, datetime], list[ScheduledCommand]] = {}
        for command in self._booked:
            if command.booked.los <= sim_time:
                passes.setdefault(command.booked.key, []).append(command)
        for key, commands in passes.items():
            booked = commands[0].booked
            in_contact_window = window is not None and window.booked.key == key and window.saw_aos
            reason = (
                "no contact during its pass"
                if in_contact_window
                else "missed its pass: the clock jumped past it"
            )
            detail = (
                "there was no contact during the pass (communications can't follow a clock faster "
                f"than {self.max_time_scale:g} times real time)"
                if in_contact_window
                else "the clock jumped past it"
            )
            for command in commands:
                self._booked.remove(command)
                out.append(self._finish(command, "aborted", reason, sim_time))
            out.append(
                self._alarm(
                    sim_time,
                    "warning",
                    None,
                    booked.norad_id,
                    f"{_names(commands)} for {booked.satellite_name} missed its {_hm(booked.aos)} "
                    f"UTC pass: {detail}.",
                )
            )
        return out

    def step(
        self, sim_time: datetime, health: HealthState | None, contact: ContactState | None
    ) -> tuple[list[BaseModel], list[OutgoingCommand]]:
        """After comms has looked at the contact: the system check, once per pass, at the first
        tick in contact after AOS. Returns messages and the commands released to comms."""
        window = self._window
        if (
            window is None
            or window.checked
            or not (window.booked.aos <= sim_time < window.booked.los)
        ):
            return [], []
        window.saw_aos = True
        booked = window.booked
        if contact is None or contact.norad_id != booked.norad_id or contact.state != "active":
            return [], []

        check = system_check(booked.norad_id, health, sim_time)
        commands = self._take(booked)
        window.checked, window.released = True, len(commands)
        out: list[BaseModel] = []
        released: list[OutgoingCommand] = []
        if check.go:
            for command in commands:
                self.sink(StatusRecord(command.command_id, "queued", check.model_dump(mode="json")))
                released.append(
                    OutgoingCommand(
                        command_id=command.command_id,
                        norad_id=command.norad_id,
                        template=command.template,
                        params=command.params,
                        issued_at=sim_time,
                        scheduled_pass=booked.out(),
                        check=check,
                    )
                )
            return out, released

        red = [s for s in SUBSYSTEMS if check.subsystems[s] == "red"]
        if health is not None and red:
            reason = "system check at AOS: " + ", ".join(f"{s} red" for s in red)
            detail = ", ".join(_red_details(health, red))
            severity: AlarmSeverity = "critical"
        else:
            reason = "system check at AOS: no system data"
            detail = "no system data to check"
            severity = "warning"
        for command in commands:
            out.append(self._finish(command, "held", reason, sim_time, check))
        out.append(
            self._alarm(
                sim_time,
                severity,
                red[0] if red else None,
                booked.norad_id,
                f"{_names(commands)} for {booked.satellite_name} held at AOS "
                f"({_hm(sim_time)} UTC): {detail}.",
            )
        )
        return out, released

    def state(self, sim_time: datetime) -> AutomationState | None:
        window = self._window
        if window is None or not (window.booked.aos - self.lead <= sim_time < window.booked.los):
            return None
        booked = window.booked
        return AutomationState(
            norad_id=booked.norad_id,
            aos_time=booked.aos,
            los_time=booked.los,
            max_elev_deg=round(booked.max_elev_deg, 3),
            commands=(
                window.released
                if window.checked
                else sum(1 for c in self._booked if c.booked.key == booked.key)
            ),
            checked=window.checked,
            clock_slowed=self._slowed_for == booked.key,
        )

    # ----- helpers -----

    def _take(self, booked: BookedPass) -> list[ScheduledCommand]:
        commands = [c for c in self._booked if c.booked.key == booked.key]
        self._booked = [c for c in self._booked if c.booked.key != booked.key]
        return commands

    def _finish(
        self,
        command: ScheduledCommand,
        status: CommandStatus,
        reason: str | None,
        sim_time: datetime,
        check: SystemCheck | None = None,
    ) -> CommandStatusMessage:
        self.sink(
            StatusRecord(
                command.command_id,
                status,
                check.model_dump(mode="json") if check is not None else None,
            )
        )
        return self._status(command, status, reason, sim_time, check)

    @staticmethod
    def _status(
        command: ScheduledCommand,
        status: CommandStatus,
        reason: str | None,
        sim_time: datetime,
        check: SystemCheck | None = None,
    ) -> CommandStatusMessage:
        return CommandStatusMessage(
            command_id=command.command_id,
            norad_id=command.norad_id,
            command=command.template.name,
            status=status,
            reason=reason,
            sim_time=sim_time,
            scheduled_pass=command.booked.out(),
            check=check,
        )

    def _alarm(
        self,
        sim_time: datetime,
        severity: AlarmSeverity,
        subsystem: Subsystem | None,
        norad_id: int | None,
        message: str,
    ) -> AlarmMessage:
        self._alarm_seq += 1
        alarm = AlarmMessage(
            alarm_id=self._alarm_seq,
            severity=severity,
            subsystem=subsystem,
            norad_id=norad_id,
            message=message,
            sim_time=sim_time,
        )
        self._alarms.append(alarm)
        self.sink(
            AlarmRecord(
                self.session_id, self._alarm_seq, sim_time, severity, subsystem, norad_id, message
            )
        )
        return alarm

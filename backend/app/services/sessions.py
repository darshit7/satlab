"""Mission-session orchestration: owns live ``SessionRuntime``s and applies client commands."""

import asyncio
import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Alarm, Command, MissionSession, PacketTemplate
from app.models.ws import (
    AckAlarm,
    AlarmMessage,
    CancelCommand,
    ClientCommand,
    CommandStatusMessage,
    ObserverState,
    ScheduleCommand,
    SendCommand,
    SetClock,
    SetFilter,
    SetSelected,
    SetTracked,
)
from app.packets.templates import CommandParams, CommandTemplate, validate_params
from app.services.catalog import CatalogService
from app.services.ratelimit import TokenBucket
from app.sim.clock import SimClock
from app.sim.comms import FrameArchive, OutgoingCommand, Record, StatusRecord
from app.sim.loop import RuntimeConfig, SessionRuntime, Subscriber
from app.sim.passes import Span
from app.sim.propagate import Elements
from app.sim.scheduler import (
    KEPT_ALARMS,
    MATCH_TOLERANCE_S,
    AutomationRecord,
    BookedPass,
    ScheduledCommand,
    nearest_pass,
    search_passes,
)

log = logging.getLogger(__name__)


class SessionUnavailable(Exception):
    pass


class ServerBusy(Exception):
    """Already running the configured maximum of live sessions."""


@dataclass(frozen=True)
class _SavedCommand:
    command_id: int
    norad_id: int
    template_id: int | None
    name: str  # the command's template name
    params: CommandParams
    scheduled_aos: datetime
    sim_time: datetime


@dataclass(frozen=True)
class _SavedAutomation:
    """What a session's pass automation had booked and raised, from the DB (after a restart)."""

    commands: list[_SavedCommand]
    alarms: list[AlarmMessage]
    last_alarm_seq: int


def new_session_token() -> tuple[str, str]:
    """A capability token for one session: (token for the client, SHA-256 hex to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_session_token(token)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_token_matches(session: MissionSession, token: str | None) -> bool:
    """Constant-time check of a client's token. Sessions without a stored hash never match."""
    if not token or session.token_hash is None:
        return False
    return hmac.compare_digest(session.token_hash, hash_session_token(token))


class SessionManager:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        catalog: CatalogService,
        archive: FrameArchive | None = None,
        sink: Callable[[Record | AutomationRecord], None] = lambda record: None,
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._catalog = catalog
        self._archive = archive
        self._sink = sink
        self._runtime_config = RuntimeConfig.from_settings(settings)
        self._runtimes: dict[int, SessionRuntime] = {}
        self._idle_timers: dict[int, asyncio.Task[None]] = {}
        self._command_budgets: dict[int, TokenBucket] = {}
        self._lock = asyncio.Lock()

    def _load(self, session_id: int) -> MissionSession | None:
        with self._sessions() as db:
            return db.get(MissionSession, session_id)

    async def owned(self, session_id: int, token: str | None) -> MissionSession | None:
        """The session, if it exists and ``token`` is its capability token; else None.

        Unknown session and wrong token look the same to the caller, so IDs can't be probed.
        """
        row = await asyncio.to_thread(self._load, session_id)
        return row if row is not None and session_token_matches(row, token) else None

    async def attach(self, session_id: int, token: str) -> tuple[SessionRuntime, Subscriber]:
        async with self._lock:
            row = await self.owned(session_id, token)
            if row is None:
                raise SessionUnavailable(f"mission session {session_id} not found")
            if row.ended_at is not None:
                raise SessionUnavailable(f"mission session {session_id} has ended")
            if (timer := self._idle_timers.pop(session_id, None)) is not None:
                timer.cancel()
            runtime = self._runtimes.get(session_id)
            restored: list[CommandStatusMessage] = []
            if runtime is None:
                if len(self._runtimes) >= self.settings.max_active_sessions:
                    raise ServerBusy
                runtime = self._create_runtime(row)
                restored = await self._restore_automation(runtime)
                self._runtimes[session_id] = runtime
                runtime.start()
            sub = runtime.subscribe()
            for message in restored:
                sub.offer(message)
            return runtime, sub

    def _create_runtime(self, row: MissionSession) -> SessionRuntime:
        # Resume where the session's clock would be had it run uninterrupted at its initial rate.
        elapsed_s = (datetime.now(UTC) - row.started_at).total_seconds()
        sim_now = row.sim_start_time + timedelta(seconds=max(elapsed_s, 0.0) * row.time_scale)
        return SessionRuntime(
            session_id=row.id,
            observer=ObserverState(
                station_id=row.station_id,
                name=row.location_name,
                lat_deg=row.lat_deg,
                lon_deg=row.lon_deg,
                alt_m=row.alt_m,
                horizon_mask_deg=self.settings.horizon_mask_deg,
            ),
            clock=SimClock(sim_anchor=sim_now, time_scale=row.time_scale),
            elements_lookup=self._catalog.registry.get,
            fleet_source=lambda: self._catalog.fleet,
            config=self._runtime_config,
            archive=self._archive,
            sink=self._sink,
        )

    async def _restore_automation(self, runtime: SessionRuntime) -> list[CommandStatusMessage]:
        """Re-book what the session had waiting for a pass (and its alarms) after a restart.
        Returns the statuses of commands that couldn't be booked again."""
        saved = await asyncio.to_thread(self._load_automation, runtime.session_id)
        runtime.automation.restore(saved.alarms, saved.last_alarm_seq)
        lost: list[CommandStatusMessage] = []
        for command in saved.commands:
            booked = await self._rebook(runtime, command)
            if booked is not None:
                runtime.schedule_command(booked)
                continue
            self._sink(StatusRecord(command.command_id, "aborted"))
            lost.append(
                CommandStatusMessage(
                    command_id=command.command_id,
                    norad_id=command.norad_id,
                    command=command.name,
                    status="aborted",
                    reason="its pass could not be found again after a server restart",
                    sim_time=runtime.clock.now(),
                )
            )
        return lost

    def _load_automation(self, session_id: int) -> _SavedAutomation:
        with self._sessions() as db:
            commands = db.execute(
                select(Command, PacketTemplate.name)
                .outerjoin(PacketTemplate, PacketTemplate.id == Command.template_id)
                .where(Command.session_id == session_id, Command.status == "scheduled")
                .order_by(Command.id)
            ).all()
            alarms = db.scalars(
                select(Alarm)
                .where(Alarm.session_id == session_id)
                .order_by(Alarm.seq.desc())
                .limit(KEPT_ALARMS)
            ).all()
            return _SavedAutomation(
                commands=[
                    _SavedCommand(
                        c.id,
                        c.norad_id,
                        c.template_id,
                        name or "command",
                        dict(c.params),
                        c.scheduled_aos,
                        c.sim_time,
                    )
                    for c, name in commands
                    if c.scheduled_aos is not None
                ],
                alarms=[
                    AlarmMessage(
                        alarm_id=a.seq,
                        severity=a.severity,  # type: ignore[arg-type]
                        subsystem=a.subsystem,  # type: ignore[arg-type]
                        norad_id=a.norad_id,
                        message=a.message,
                        sim_time=a.sim_time,
                        acknowledged=a.acknowledged,
                    )
                    for a in reversed(alarms)
                ],
                last_alarm_seq=alarms[0].seq if alarms else 0,
            )

    async def _rebook(
        self, runtime: SessionRuntime, saved: _SavedCommand
    ) -> ScheduledCommand | None:
        """The saved command booked again for its pass, or None if that can't be done."""
        sat = self._catalog.fleet.comms.by_norad.get(saved.norad_id)
        template = next(
            (t for i, t in (sat.commands if sat else ()) if i == saved.template_id), None
        )
        elements = await self._elements(saved.norad_id)
        if template is None or elements is None:
            return None
        window = timedelta(seconds=MATCH_TOLERANCE_S) + timedelta(hours=2)
        search = await asyncio.to_thread(
            search_passes,
            elements.satellite.model,
            runtime.station,
            self.settings.horizon_mask_deg,
            saved.scheduled_aos - window,
            2 * window.total_seconds(),
            self.settings.pass_search_step_s,
        )
        found = nearest_pass(search.passes, saved.scheduled_aos)
        if found is None:
            return None
        return self._scheduled(
            saved.command_id, elements, template, saved.params, saved.sim_time, found
        )

    @staticmethod
    def _scheduled(
        command_id: int,
        elements: Elements,
        template: CommandTemplate,
        params: CommandParams,
        issued_at: datetime,
        found: Span,
    ) -> ScheduledCommand:
        assert found.aos is not None and found.los is not None
        return ScheduledCommand(
            command_id=command_id,
            norad_id=elements.norad_id,
            template=template,
            params=params,
            issued_at=issued_at,
            booked=BookedPass(
                norad_id=elements.norad_id,
                satellite_name=elements.name,
                aos=found.aos,
                los=found.los,
                max_elev_deg=found.max_elev_deg,
            ),
        )

    async def _elements(self, norad_id: int) -> Elements | None:
        try:
            return await self._catalog.resolve(norad_id)
        except Exception:
            log.exception("could not resolve NORAD %d", norad_id)
            return None

    def sim_now(self, session_id: int) -> datetime | None:
        """The live session's sim time, or None if it isn't running."""
        runtime = self._runtimes.get(session_id)
        return runtime.clock.now() if runtime is not None else None

    async def detach(self, runtime: SessionRuntime, sub: Subscriber) -> None:
        runtime.unsubscribe(sub)
        if runtime.subscriber_count == 0:
            self._idle_timers[runtime.session_id] = asyncio.create_task(
                self._end_when_idle(runtime.session_id)
            )

    async def _end_when_idle(self, session_id: int) -> None:
        await asyncio.sleep(self.settings.session_idle_grace_s)
        runtime = self._runtimes.get(session_id)
        if runtime is not None and runtime.subscriber_count == 0:
            log.info("session %d idle; ending", session_id)
            self._idle_timers.pop(session_id, None)
            await self.end(session_id)

    async def end(self, session_id: int) -> None:
        self._command_budgets.pop(session_id, None)
        if (runtime := self._runtimes.pop(session_id, None)) is not None:
            await runtime.stop()

        def mark_ended() -> None:
            with self._sessions() as db:
                row = db.get(MissionSession, session_id)
                if row is not None and row.ended_at is None:
                    row.ended_at = datetime.now(UTC)
                    # nothing will be there to send what was waiting for a pass
                    db.execute(
                        update(Command)
                        .where(Command.session_id == session_id, Command.status == "scheduled")
                        .values(status="aborted")
                    )
                    db.commit()

        await asyncio.to_thread(mark_ended)

    async def shutdown(self) -> None:
        """Stop loops without ending sessions, so clients can resume after a restart."""
        for timer in self._idle_timers.values():
            timer.cancel()
        for runtime in self._runtimes.values():
            await runtime.stop()
        self._runtimes.clear()

    # ----- client commands -----

    async def handle(self, runtime: SessionRuntime, message: ClientCommand) -> None:
        match message:
            case SetTracked():
                await self._set_tracked(runtime, message)
            case SetSelected():
                await self._set_selected(runtime, message)
            case SetFilter():
                runtime.set_filter(message)
            case SetClock():
                self._set_clock(runtime, message)
            case SendCommand():
                await self._send_command(runtime, message)
            case ScheduleCommand():
                await self._schedule_command(runtime, message)
            case CancelCommand():
                if not runtime.cancel_command(message.command_id):
                    runtime.publish_error("Only a command waiting for its pass can be cancelled.")
            case AckAlarm():
                if not runtime.ack_alarm(message.alarm_id):
                    runtime.publish_error("No such alarm.")
        runtime.step()  # reflect the change immediately rather than on the next tick

    def _checked_command(
        self, runtime: SessionRuntime, message: SendCommand | ScheduleCommand
    ) -> tuple[int, CommandTemplate, CommandParams] | None:
        """The command's template and parameters, within the session's command budget; otherwise
        the reason is published and the result is None."""
        per_min = self.settings.commands_per_min
        budget = self._command_budgets.setdefault(
            runtime.session_id, TokenBucket(rate=per_min / 60, burst=min(per_min, 10))
        )
        if not budget.take():
            runtime.publish_error("Too many commands; wait a moment.")
            return None
        sat = self._catalog.fleet.comms.by_norad.get(message.norad_id)
        found = sat.command_named(message.command) if sat is not None else None
        if found is None:
            runtime.publish_error(f"{message.command} isn't a command this satellite takes.")
            return None
        template_id, template = found
        try:
            params = validate_params(template, message.params)
        except ValueError as exc:  # messages built from our own templates: safe to show
            runtime.publish_error(f"{message.command}: {exc}.")
            return None
        return template_id, template, params

    async def _send_command(self, runtime: SessionRuntime, message: SendCommand) -> None:
        """Validate a command against its template and the contact, store it, queue it."""
        checked = self._checked_command(runtime, message)
        if checked is None:
            return
        template_id, template, params = checked
        if not runtime.comms.can_command(message.norad_id):
            fastest = self.settings.comms_max_time_scale
            runtime.publish_error(
                "No contact: commands go out while the selected satellite is in view "
                f"(and the clock runs at most {fastest:g} times real time)."
            )
            return
        sim_time = runtime.clock.now()
        command_id = await asyncio.to_thread(
            self._store_command, runtime.session_id, message.norad_id, template_id, params, sim_time
        )
        if command_id is None:
            limit = self.settings.max_commands_per_session
            runtime.publish_error(f"This session has sent its limit of {limit} commands.")
            return
        runtime.submit_command(
            OutgoingCommand(
                command_id=command_id,
                norad_id=message.norad_id,
                template=template,
                params=params,
                issued_at=sim_time,
            )
        )

    async def _schedule_command(self, runtime: SessionRuntime, message: ScheduleCommand) -> None:
        """Validate a command, find the pass it names, check the antenna is free, store it, book
        it. Pass automation sends it at AOS unless a subsystem is red."""
        checked = self._checked_command(runtime, message)
        if checked is None:
            return
        template_id, template, params = checked
        now = runtime.clock.now()
        requested = message.aos_time.astimezone(UTC)
        horizon = timedelta(hours=self.settings.schedule_horizon_h)
        if requested <= now:
            runtime.publish_error(
                "That pass has already started: send the command during the pass instead."
            )
            return
        if requested > now + horizon + timedelta(seconds=MATCH_TOLERANCE_S):
            hours = self.settings.schedule_horizon_h
            runtime.publish_error(f"Commands can be booked for passes up to {hours:g} hours ahead.")
            return
        elements = await self._elements(message.norad_id)
        if elements is None:
            runtime.publish_error(f"No usable orbital elements for NORAD {message.norad_id}.")
            return
        search = await asyncio.to_thread(
            search_passes,
            elements.satellite.model,
            runtime.station,
            self.settings.horizon_mask_deg,
            now,
            (horizon + timedelta(hours=2)).total_seconds(),  # past the end, for the last LOS
            self.settings.pass_search_step_s,
        )
        if search.always_visible:
            runtime.publish_error(
                f"{elements.name} stays above the horizon mask from here: there is no AOS to "
                "wait for, so send the command now."
            )
            return
        found = nearest_pass([p for p in search.passes if p.aos and p.aos > now], requested)
        if found is None:
            runtime.publish_error(
                f"No pass of {elements.name} at that time: pick one from its pass schedule."
            )
            return
        command = self._scheduled(0, elements, template, params, now, found)
        if (other := runtime.automation.conflict(command.booked)) is not None:
            runtime.publish_error(
                f"The antenna is booked for {other.satellite_name} from {other.aos:%H:%M} to "
                f"{other.los:%H:%M} UTC."
            )
            return
        if runtime.automation.pending_count >= self.settings.max_scheduled_commands:
            limit = self.settings.max_scheduled_commands
            runtime.publish_error(f"At most {limit} commands can wait for a pass.")
            return
        command_id = await asyncio.to_thread(
            self._store_command,
            runtime.session_id,
            message.norad_id,
            template_id,
            params,
            now,
            "scheduled",
            command.booked.aos,
        )
        if command_id is None:
            limit = self.settings.max_commands_per_session
            runtime.publish_error(f"This session has sent its limit of {limit} commands.")
            return
        command.command_id = command_id
        runtime.schedule_command(command)

    def _store_command(
        self,
        session_id: int,
        norad_id: int,
        template_id: int,
        params: CommandParams,
        sim_time: datetime,
        status: str = "queued",
        scheduled_aos: datetime | None = None,
    ) -> int | None:
        """The new command's id, or None once the session has used its allowance."""
        with self._sessions() as db:
            sent = db.scalar(
                select(func.count()).select_from(Command).where(Command.session_id == session_id)
            )
            if (sent or 0) >= self.settings.max_commands_per_session:
                return None
            row = Command(
                session_id=session_id,
                norad_id=norad_id,
                template_id=template_id,
                params=params,
                scheduled_aos=scheduled_aos,
                status=status,
                sim_time=sim_time,
            )
            db.add(row)
            db.commit()
            return row.id

    async def _set_tracked(self, runtime: SessionRuntime, message: SetTracked) -> None:
        requested = list(dict.fromkeys(message.norad_ids))
        limit = self.settings.max_tracked
        if len(requested) > limit:
            runtime.publish_error(f"Tracking is limited to {limit} satellites; extra ignored.")
            requested = requested[:limit]
        results = await asyncio.gather(
            *(self._catalog.resolve(n) for n in requested), return_exceptions=True
        )
        usable = [n for n, r in zip(requested, results, strict=True) if isinstance(r, Elements)]
        missing = [n for n in requested if n not in usable]
        if missing:
            runtime.publish_error(f"No usable orbital elements for NORAD {missing}.")
        runtime.set_tracked(usable)

    async def _set_selected(self, runtime: SessionRuntime, message: SetSelected) -> None:
        norad_id = message.norad_id
        if norad_id is not None:
            try:
                elements = await self._catalog.resolve(norad_id)
            except Exception:
                log.exception("could not resolve NORAD %d", norad_id)
                elements = None
            if elements is None and norad_id not in self._catalog.fleet.index:
                runtime.publish_error(f"No usable orbital elements for NORAD {norad_id}.")
                norad_id = None
        runtime.set_selected(norad_id)

    def _set_clock(self, runtime: SessionRuntime, message: SetClock) -> None:
        clock = runtime.clock
        if message.sim_time is not None:
            clock.jump_to(message.sim_time)
        if message.time_scale is not None:
            clock.set_time_scale(min(message.time_scale, self.settings.max_time_scale))
        if message.paused is True:
            clock.pause()
        elif message.paused is False:
            clock.resume()

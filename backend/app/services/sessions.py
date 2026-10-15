"""Mission-session orchestration: owns live ``SessionRuntime``s and applies client commands."""

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import MissionSession
from app.models.ws import ObserverState, SetClock, SetFilter, SetSelected, SetTracked
from app.services.catalog import CatalogService
from app.sim.clock import SimClock
from app.sim.loop import RuntimeConfig, SessionRuntime, Subscriber
from app.sim.propagate import Elements

log = logging.getLogger(__name__)


class SessionUnavailable(Exception):
    pass


class ServerBusy(Exception):
    """Already running the configured maximum of live sessions."""


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
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._catalog = catalog
        self._runtime_config = RuntimeConfig.from_settings(settings)
        self._runtimes: dict[int, SessionRuntime] = {}
        self._idle_timers: dict[int, asyncio.Task[None]] = {}
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
            if runtime is None:
                if len(self._runtimes) >= self.settings.max_active_sessions:
                    raise ServerBusy
                runtime = self._create_runtime(row)
                self._runtimes[session_id] = runtime
                runtime.start()
            return runtime, runtime.subscribe()

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
        )

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
        if (runtime := self._runtimes.pop(session_id, None)) is not None:
            await runtime.stop()

        def mark_ended() -> None:
            with self._sessions() as db:
                row = db.get(MissionSession, session_id)
                if row is not None and row.ended_at is None:
                    row.ended_at = datetime.now(UTC)
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

    async def handle(
        self, runtime: SessionRuntime, message: SetTracked | SetSelected | SetFilter | SetClock
    ) -> None:
        match message:
            case SetTracked():
                await self._set_tracked(runtime, message)
            case SetSelected():
                await self._set_selected(runtime, message)
            case SetFilter():
                runtime.set_filter(message)
            case SetClock():
                self._set_clock(runtime, message)
        runtime.step()  # reflect the change immediately rather than on the next tick

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

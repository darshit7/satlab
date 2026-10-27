"""Comms persistence, off the sim loop: received frames, command status changes (with the system
check at AOS for booked commands) and pass-automation alarms are queued by the session runtimes
(``offer`` never blocks) and written in batches on a worker thread.

Bounded for a public deploy: the queue drops (and logs once) when full, each session stores at most
``max_telemetry_frames_per_session`` frames, and the comms rows (frames, commands, alarms) of
sessions that ended more than ``comms_retention_days`` ago are purged hourly.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Alarm, Command, MissionSession, TelemetryFrame
from app.sim.comms import FrameRecord, Record, StatusRecord
from app.sim.scheduler import AlarmAckRecord, AlarmRecord, AutomationRecord

log = logging.getLogger(__name__)

_QUEUE_SIZE = 2000
_BATCH = 200
_PURGE_EVERY_S = 3600.0


class CommsWriter:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._queue: asyncio.Queue[Record | AutomationRecord] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._stored: dict[int, int] = {}  # frames stored per session (counted from the DB once)
        self._capped: set[int] = set()
        self._dropping = False
        self._task: asyncio.Task[None] | None = None
        # One batch at a time: a flush waits for the batch in flight, so after it returns every
        # record offered before it is committed.
        self._writing = asyncio.Lock()
        # A record the worker took off the queue but hasn't written yet: whoever writes next
        # (the worker, or a flush that got the lock first) writes it.
        self._taken: list[Record | AutomationRecord] = []

    # ----- producer side (the sim loop) -----

    def offer(self, record: Record | AutomationRecord) -> None:
        try:
            self._queue.put_nowait(record)
            self._dropping = False
        except asyncio.QueueFull:
            if not self._dropping:
                log.warning("comms writer: queue full; dropping records until it drains")
            self._dropping = True

    # ----- lifecycle -----

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="comms-writer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()

    async def flush(self) -> None:
        """Write whatever is queued now, after any batch in flight (shutdown, tests)."""
        async with self._writing:
            batch = self._take_all()
            if batch:
                await asyncio.to_thread(self._write, batch)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_purge = loop.time()
        while True:
            try:
                timeout = max(0.0, next_purge - loop.time())
                try:
                    record = await asyncio.wait_for(self._queue.get(), timeout)
                    # appended after the await: a flush meanwhile replaces ``_taken``
                    self._taken.append(record)
                    async with self._writing:
                        batch = self._take_all(_BATCH)
                        if batch:
                            await asyncio.to_thread(self._write, batch)
                except TimeoutError:
                    pass
                if loop.time() >= next_purge:
                    next_purge = loop.time() + _PURGE_EVERY_S
                    await asyncio.to_thread(self.purge)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the writer die; the next batch may succeed
                log.exception("comms writer: batch failed")

    def _take_all(self, limit: int = _QUEUE_SIZE) -> list[Record | AutomationRecord]:
        batch, self._taken = self._taken[:limit], self._taken[limit:]
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    # ----- worker thread -----

    def _write(self, records: list[Record | AutomationRecord]) -> None:
        cap = self.settings.max_telemetry_frames_per_session
        with self._sessions() as db:
            for record in records:
                match record:
                    case StatusRecord():
                        values: dict[str, object] = {"status": record.status}
                        if record.check is not None:
                            values["check_result"] = record.check
                        db.execute(
                            update(Command).where(Command.id == record.command_id).values(**values)
                        )
                    case AlarmRecord():
                        db.add(
                            Alarm(
                                session_id=record.session_id,
                                seq=record.seq,
                                norad_id=record.norad_id,
                                sim_time=record.sim_time,
                                severity=record.severity,
                                subsystem=record.subsystem,
                                message=record.message,
                            )
                        )
                        db.flush()  # before an acknowledgement in the same batch
                    case AlarmAckRecord():
                        db.execute(
                            update(Alarm)
                            .where(Alarm.session_id == record.session_id, Alarm.seq == record.seq)
                            .values(acknowledged=True)
                        )
                    case FrameRecord():
                        stored = self._stored_count(db, record.session_id)
                        if stored >= cap:
                            if record.session_id not in self._capped:
                                self._capped.add(record.session_id)
                                log.info(
                                    "session %d: telemetry storage cap reached", record.session_id
                                )
                            continue
                        db.add(
                            TelemetryFrame(
                                session_id=record.session_id,
                                norad_id=record.norad_id,
                                template_id=record.template_id,
                                command_id=record.command_id,
                                sim_time=record.sim_time,
                                raw=record.raw,
                                decoded=record.decoded,
                                source=record.source,
                                crc_ok=record.crc_ok,
                                origin=record.origin,
                            )
                        )
                        self._stored[record.session_id] = stored + 1
            db.commit()

    def _stored_count(self, db: Session, session_id: int) -> int:
        if session_id not in self._stored:
            self._stored[session_id] = (
                db.scalar(
                    select(func.count())
                    .select_from(TelemetryFrame)
                    .where(TelemetryFrame.session_id == session_id)
                )
                or 0
            )
        return self._stored[session_id]

    def purge(self) -> int:
        """Delete the comms rows of sessions that ended more than the retention period ago."""
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.comms_retention_days)
        ended = select(MissionSession.id).where(
            MissionSession.ended_at.is_not(None), MissionSession.ended_at < cutoff
        )
        with self._sessions() as db:
            frames = db.execute(delete(TelemetryFrame).where(TelemetryFrame.session_id.in_(ended)))
            commands = db.execute(delete(Command).where(Command.session_id.in_(ended)))
            alarms = db.execute(delete(Alarm).where(Alarm.session_id.in_(ended)))
            db.commit()
        removed = sum(r.rowcount or 0 for r in (frames, commands, alarms))  # type: ignore[attr-defined]
        if removed:
            log.info("comms retention: removed %d rows of ended sessions", removed)
        return removed

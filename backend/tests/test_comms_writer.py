"""The comms writer: alarms and their acknowledgement, the system check on a command, and a flush
that really waits for every record offered before it."""

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Alarm, Command, MissionSession, Satellite
from app.main import run_migrations
from app.services.comms import CommsWriter
from app.sim.comms import StatusRecord
from app.sim.scheduler import AlarmAckRecord, AlarmRecord

T0 = datetime(2026, 10, 10, 17, 52, tzinfo=UTC)


def _session_with_command() -> tuple[int, int]:
    with get_sessionmaker()() as db:
        session = MissionSession(location_name="x", lat_deg=0.0, lon_deg=0.0, sim_start_time=T0)
        db.add_all([session, Satellite(norad_id=43013, name="NOAA 20")])
        db.flush()
        command = Command(
            session_id=session.id, norad_id=43013, params={}, status="scheduled", sim_time=T0
        )
        db.add(command)
        db.commit()
        return session.id, command.id


def test_alarms_checks_and_acknowledgements(app_env: Path) -> None:
    run_migrations()
    session_id, command_id = _session_with_command()

    async def run() -> None:
        writer = CommsWriter(get_settings(), get_sessionmaker())
        writer.offer(StatusRecord(command_id, "held", {"go": False}))
        writer.offer(AlarmRecord(session_id, 1, T0, "critical", "attitude", None, "held at AOS"))
        writer.offer(AlarmAckRecord(session_id, 1))  # in the same batch as the alarm
        await writer.flush()

    asyncio.run(run())
    with get_sessionmaker()() as db:
        command = db.get(Command, command_id)
        assert command is not None and command.status == "held"
        assert command.check_result == {"go": False}
        [alarm] = db.scalars(select(Alarm)).all()
        assert (alarm.seq, alarm.severity, alarm.subsystem) == (1, "critical", "attitude")
        assert alarm.acknowledged and alarm.wall_time is not None


def test_flush_writes_what_the_worker_took(app_env: Path) -> None:
    """While a batch is being written, a flush queues for the lock and another record arrives:
    the worker takes that record off the queue as soon as its batch is done, ahead of the flush.
    The flush must still write it before returning."""
    run_migrations()
    session_id, _ = _session_with_command()

    async def run() -> None:
        writer = CommsWriter(get_settings(), get_sessionmaker())
        gate, writes = threading.Event(), []
        write = writer._write

        def slow_first_write(batch: list) -> None:
            writes.append(len(batch))
            if len(writes) == 1:
                gate.wait(5)
            write(batch)

        writer._write = slow_first_write  # type: ignore[method-assign]
        writer.start()
        for _ in range(500):  # until the worker, past its first purge, waits on the queue
            if writer._queue._getters:  # type: ignore[attr-defined]
                break
            await asyncio.sleep(0.01)
        writer.offer(AlarmRecord(session_id, 1, T0, "warning", None, None, "first"))
        for _ in range(500):  # until that batch is being written
            if writes:
                break
            await asyncio.sleep(0.01)
        writer.offer(AlarmRecord(session_id, 2, T0, "warning", None, None, "second"))
        flush = asyncio.create_task(writer.flush())
        for _ in range(5):
            await asyncio.sleep(0)  # the flush queues for the lock
        gate.set()
        await flush
        with get_sessionmaker()() as db:
            assert sorted(a.seq for a in db.scalars(select(Alarm))) == [1, 2]
        await writer.stop()

    asyncio.run(run())

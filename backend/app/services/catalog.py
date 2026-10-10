"""Satellite catalog: Celestrak ingestion, search, the elements registry and the fleet.

All network and DB work happens here, off the sim loop. The loop only reads in-memory objects:
``Registry.get`` (a dict lookup of already-built ``Elements``) and ``fleet`` (every catalogued
satellite, rebuilt after each ingest and swapped in with one assignment).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.data.celestrak import CelestrakClient, CelestrakNotice, TleRecord, parse_tle_text
from app.data.classify import classify
from app.db.models import Satellite
from app.models.ws import SATELLITE_TYPES
from app.sim.fleet import Fleet, FleetRow
from app.sim.propagate import Elements, elements_from_tle

log = logging.getLogger(__name__)

_UPSERT_CHUNK = 500
_REMOTE_QUERY_TTL_S = 2 * 3600  # Celestrak etiquette: don't repeat identical queries for ~2 h


_UPSERT_COLUMNS = (
    "name",
    "type",
    "is_sar_capable",
    "tle_line1",
    "tle_line2",
    "tle_epoch",
    "fetched_at",
)


def upsert_tle_records(db: Session, records: Iterable[TleRecord], fetched_at: datetime) -> int:
    """Insert or update satellites: name, TLE, and type/SAR flags from the classification rules."""
    rows = []
    for rec in records:
        try:
            epoch = elements_from_tle(rec.name, rec.line1, rec.line2).epoch
        except ValueError:
            continue
        kind = classify(rec.name, rec.norad_id)
        rows.append(
            {
                "norad_id": rec.norad_id,
                "name": rec.name,
                "type": kind.type,
                "is_sar_capable": kind.is_sar_capable,
                "tle_line1": rec.line1,
                "tle_line2": rec.line2,
                "tle_epoch": epoch,
                "fetched_at": fetched_at,
            }
        )
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    for start in range(0, len(rows), _UPSERT_CHUNK):
        stmt = insert(Satellite).values(rows[start : start + _UPSERT_CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=[Satellite.norad_id],
            set_={col: stmt.excluded[col] for col in _UPSERT_COLUMNS},
        )
        db.execute(stmt)
    db.commit()
    return len(rows)


def load_fleet_rows(db: Session) -> list[FleetRow]:
    stmt = select(
        Satellite.norad_id,
        Satellite.name,
        Satellite.type,
        Satellite.is_sar_capable,
        Satellite.tle_line1,
        Satellite.tle_line2,
    ).where(Satellite.tle_line1.is_not(None), Satellite.tle_line2.is_not(None))
    rows = []
    for norad_id, name, kind, sar, line1, line2 in db.execute(stmt):
        if kind not in SATELLITE_TYPES:  # row predates (this version of) the classification
            c = classify(name, norad_id)
            kind, sar = c.type, c.is_sar_capable
        rows.append(FleetRow(norad_id, name, kind, bool(sar), line1, line2))
    return rows


def search_local(db: Session, query: str, limit: int) -> list[Satellite]:
    q = query.strip()
    if not q:
        return []
    name_lower = func.lower(Satellite.name)
    q_lower = q.lower()
    norad_id = int(q) if q.isdigit() else -1
    condition = name_lower.contains(q_lower, autoescape=True) | (Satellite.norad_id == norad_id)
    rank = case(
        (Satellite.norad_id == norad_id, 0),
        (name_lower == q_lower, 1),
        (name_lower.startswith(q_lower, autoescape=True), 2),
        else_=3,
    )
    stmt = (
        select(Satellite)
        .where(condition)
        .order_by(rank, func.length(Satellite.name), Satellite.name)
        .limit(limit)
    )
    return list(db.scalars(stmt))


class Registry:
    """norad_id → propagatable Elements, for satellites the sim is actively using."""

    def __init__(self) -> None:
        self._elements: dict[int, Elements] = {}

    def get(self, norad_id: int) -> Elements | None:
        return self._elements.get(norad_id)

    def put(self, elements: Elements) -> None:
        self._elements[elements.norad_id] = elements

    def ids(self) -> set[int]:
        return set(self._elements)


class CatalogService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        client: CelestrakClient | None = None,
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._client = client or CelestrakClient(
            settings.celestrak_base_url, settings.http_timeout_s
        )
        self.registry = Registry()
        self.fleet = Fleet.empty()
        self.refreshing = False
        self.last_error: str | None = None
        self._refresh_lock = asyncio.Lock()
        self._fleet_lock = asyncio.Lock()
        self._remote_queries: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None

    # ----- lifecycle -----

    @property
    def cache_file(self) -> Path:
        return self.settings.cache_dir / f"celestrak_{self.settings.catalog_group}.tle"

    def last_group_fetch(self) -> datetime | None:
        if not self.cache_file.exists():
            return None
        return datetime.fromtimestamp(self.cache_file.stat().st_mtime, UTC)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="catalog")

    async def _run(self) -> None:
        try:
            await self.rebuild_fleet()  # whatever the DB already holds: instant, works offline
        except Exception:
            log.exception("could not build the fleet from the local catalog")
        if self.settings.catalog_autoload:
            await self._refresh_forever()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()

    async def _refresh_forever(self) -> None:
        while True:
            try:
                await self.refresh_if_stale()
            except Exception:  # never let the background task die
                log.exception("catalog refresh failed")
            await asyncio.sleep(self.settings.catalog_refresh_check_minutes * 60)

    # ----- group ingestion -----

    async def refresh_if_stale(self) -> None:
        last = self.last_group_fetch()
        max_age = timedelta(hours=self.settings.catalog_max_age_hours)
        if last is None or datetime.now(UTC) - last > max_age:
            await self.refresh_group()
        if await asyncio.to_thread(self._count) == 0 and (cached_at := self.last_group_fetch()):
            # Empty DB (e.g. deleted, or offline first run) but a cached copy exists: load it.
            await self._ingest_text(self.cache_file.read_text(), cached_at)

    async def refresh_group(self) -> None:
        async with self._refresh_lock:
            self.refreshing = True
            try:
                await self._refresh_group()
            finally:
                self.refreshing = False

    async def _refresh_group(self) -> None:
        group = self.settings.catalog_group
        try:
            log.info("fetching Celestrak group %r", group)
            records = await self._client.fetch_group(group)
        except CelestrakNotice as notice:
            if "not updated" in str(notice).lower() and self.cache_file.exists():
                self.cache_file.touch()  # our copy is current
                self.last_error = None
            else:
                self.last_error = f"Celestrak: {notice}"
                log.warning("Celestrak notice for group %r: %s", group, notice)
            return
        except Exception as exc:
            self.last_error = f"Celestrak fetch failed: {exc}"
            log.warning("Celestrak fetch failed (%s); using cached catalog", exc)
            return

        fetched_at = datetime.now(UTC)
        text = "\n".join(f"{r.name}\n{r.line1}\n{r.line2}" for r in records) + "\n"
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(text)
        await self._ingest(records, fetched_at)
        self.last_error = None

    async def _ingest_text(self, text: str, fetched_at: datetime) -> None:
        await self._ingest(parse_tle_text(text), fetched_at)

    async def _ingest(self, records: list[TleRecord], fetched_at: datetime) -> None:
        def work() -> int:
            with self._sessions() as db:
                return upsert_tle_records(db, records, fetched_at)

        count = await asyncio.to_thread(work)
        log.info("catalog: upserted %d satellites", count)
        self._refresh_registry(records)
        await self.rebuild_fleet()

    async def rebuild_fleet(self) -> None:
        """Rebuild the fleet from the DB off the event loop, then swap it in."""

        def build() -> Fleet:
            with self._sessions() as db:
                return Fleet.build(load_fleet_rows(db))

        async with self._fleet_lock:
            fleet = await asyncio.to_thread(build)
            self.fleet = fleet
        log.info("fleet: %d satellites ready for propagation", len(fleet))

    def _refresh_registry(self, records: Iterable[TleRecord]) -> None:
        """Swap in new elements for satellites currently in use by the sim."""
        in_use = self.registry.ids()
        for rec in records:
            if rec.norad_id in in_use:
                try:
                    self.registry.put(elements_from_tle(rec.name, rec.line1, rec.line2))
                except ValueError:
                    log.warning("ignoring unusable TLE for %d", rec.norad_id)

    # ----- queries -----

    def _count(self) -> int:
        with self._sessions() as db:
            return db.scalar(select(func.count()).select_from(Satellite)) or 0

    async def status_count(self) -> int:
        return await asyncio.to_thread(self._count)

    async def search(self, query: str, limit: int, remote: bool) -> tuple[list[Satellite], bool]:
        """Search the local catalog; with ``remote`` also query Celestrak's full GP catalog."""
        remote_searched = False
        if remote:
            remote_searched = await self._remote_search(query.strip())

        def work() -> list[Satellite]:
            with self._sessions() as db:
                return search_local(db, query, limit)

        return await asyncio.to_thread(work), remote_searched

    async def _remote_search(self, q: str) -> bool:
        if not (q.isdigit() or len(q) >= 3):
            return False
        key = q.lower()
        now = time.monotonic()
        if now - self._remote_queries.get(key, -_REMOTE_QUERY_TTL_S) < _REMOTE_QUERY_TTL_S:
            return True  # already searched recently; results are in the DB
        try:
            if q.isdigit():
                records = await self._client.fetch_by_catnr(int(q))
            else:
                records = await self._client.search_by_name(q)
        except CelestrakNotice:
            records = []
        except Exception as exc:
            log.warning("Celestrak remote search failed: %s", exc)
            return False
        self._remote_queries[key] = now
        if records:
            await self._ingest(records, datetime.now(UTC))
        return True

    async def resolve(self, norad_id: int) -> Elements | None:
        """Load (and if stale, refresh) a satellite's elements into the registry."""
        if (cached := self.registry.get(norad_id)) is not None:
            return cached

        def load() -> Satellite | None:
            with self._sessions() as db:
                return db.get(Satellite, norad_id)

        sat = await asyncio.to_thread(load)
        max_age = timedelta(hours=self.settings.catalog_max_age_hours)
        stale = (
            sat is None
            or sat.tle_line1 is None
            or sat.fetched_at is None
            or datetime.now(UTC) - sat.fetched_at > max_age
        )
        if stale:
            try:
                records = await self._client.fetch_by_catnr(norad_id)
                await self._ingest(records, datetime.now(UTC))
                sat = await asyncio.to_thread(load)
            except Exception as exc:  # degrade to whatever we have
                log.info("could not refresh TLE for %d: %s", norad_id, exc)
        if sat is None or sat.tle_line1 is None or sat.tle_line2 is None:
            return None
        elements = elements_from_tle(sat.name, sat.tle_line1, sat.tle_line2)
        self.registry.put(elements)
        return elements

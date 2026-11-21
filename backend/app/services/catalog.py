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
from app.data.celestrak import (
    CelestrakClient,
    CelestrakNotice,
    CelestrakNotUpdated,
    TleRecord,
    parse_tle_text,
)
from app.data.classify import classify, has_no_radio
from app.data.seed import (
    load_downlinks,
    load_generic_antenna_deg,
    load_link_profiles,
    load_packet_templates,
    sync_link_profiles,
    sync_packet_templates,
)
from app.db.models import Satellite
from app.models.ws import SATELLITE_TYPES
from app.services.ratelimit import TokenBucket
from app.sim.fleet import Fleet, FleetRow
from app.sim.propagate import Elements, elements_from_tle

log = logging.getLogger(__name__)

_UPSERT_CHUNK = 500
#: Celestrak updates its data every 2 hours and refuses a repeat download before then: no query
#: (search, single satellite, group) is sent again within this.
_REMOTE_QUERY_TTL_S = 2 * 3600
_MAX_REMEMBERED_QUERIES = 2000  #: past this, expired query times are dropped
_CELESTRAK_UPDATE = timedelta(hours=2)  #: how often Celestrak's GP data changes


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
    """Every satellite with a TLE, as fleet rows (classifying any row older than the rules)."""
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
    """Name substring or exact NORAD id, ranked: id match, exact name, prefix, then the rest
    (shorter names first). User input only ever reaches SQL as escaped bound parameters."""
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
        """The satellite's elements, if they are loaded (a dict lookup: safe in the tick)."""
        return self._elements.get(norad_id)

    def put(self, elements: Elements) -> None:
        """Load (or replace) a satellite's elements."""
        self._elements[elements.norad_id] = elements

    def ids(self) -> set[int]:
        """The satellites with loaded elements."""
        return set(self._elements)


class CatalogService:
    """The satellite catalog and everything built from it.

    Keeps the ``satellite`` table fresh from Celestrak (in the background, throttled, with an
    on-disk cache that also works offline), answers searches, resolves elements into the registry
    and rebuilds the fleet, with its link and comms tables, after each ingest.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        client: CelestrakClient | None = None,
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._client = client or CelestrakClient(
            settings.celestrak_base_url,
            settings.http_timeout_s,
            max_bytes=settings.celestrak_max_bytes,
        )
        self.registry = Registry()
        self.fleet = Fleet.empty()
        self.refreshing = False
        self.last_error: str | None = None
        self._refresh_lock = asyncio.Lock()
        self._fleet_lock = asyncio.Lock()
        self._remote_queries: dict[str, float] = {}  #: search → monotonic time it was sent
        self._catnr_fetches: dict[int, float] = {}  #: NORAD id → monotonic time it was fetched
        #: When the group we hold was downloaded, or confirmed current by a refused repeat
        #: download (the cache file's mtime says the same, but a container restart loses it).
        self._group_fetched_at: datetime | None = None
        #: Celestrak's next update after a refused download: no group request before it.
        self._group_retry_at: datetime | None = None
        # One budget shared by every client, so nobody can make us hammer Celestrak.
        per_min = settings.remote_search_per_min
        self._remote_budget = TokenBucket(rate=per_min / 60, burst=per_min)
        self._task: asyncio.Task[None] | None = None

    # ----- lifecycle -----

    @property
    def cache_file(self) -> Path:
        """The last Celestrak group download (its mtime is when it was fetched)."""
        return self.settings.cache_dir / f"celestrak_{self.settings.catalog_group}.tle"

    def _cache_mtime(self) -> datetime | None:
        if not self.cache_file.exists():
            return None
        return datetime.fromtimestamp(self.cache_file.stat().st_mtime, UTC)

    def last_group_fetch(self) -> datetime | None:
        """When the group was last fetched (or confirmed current), if ever: by this process, or
        the cache file's mtime from an earlier one."""
        known = [t for t in (self._group_fetched_at, self._cache_mtime()) if t is not None]
        return max(known) if known else None

    def start(self) -> None:
        """Build the fleet from the DB, then keep the catalog fresh (a background task)."""
        self._task = asyncio.create_task(self._run(), name="catalog")

    async def _run(self) -> None:
        try:
            await self.rebuild_fleet()  # whatever the DB already holds: instant, works offline
        except Exception:
            log.exception("could not build the fleet from the local catalog")
        if self.settings.catalog_autoload:
            await self._refresh_forever()

    async def stop(self) -> None:
        """Cancel the background task and close the Celestrak client."""
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
        """Refetch the group if our copy is older than ``catalog_max_age_hours`` (never before
        Celestrak's next update after a refused download); load the cache into an empty table."""
        last = self.last_group_fetch()
        now = datetime.now(UTC)
        max_age = timedelta(hours=self.settings.catalog_max_age_hours)
        waiting = self._group_retry_at is not None and now < self._group_retry_at
        if (last is None or now - last > max_age) and not waiting:
            await self.refresh_group()
        if await asyncio.to_thread(self._count) == 0 and (cached_at := self._cache_mtime()):
            # Empty DB (e.g. deleted, or offline first run) but a cached copy exists: load it.
            text = await asyncio.to_thread(self.cache_file.read_text)
            await self._ingest_text(text, cached_at)

    async def refresh_group(self) -> None:
        """Fetch and ingest the Celestrak group now (one refresh at a time)."""
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
        except CelestrakNotUpdated as notice:
            await self._not_updated(notice)
            return
        except CelestrakNotice as notice:
            self.last_error = "Celestrak returned no data; using the cached catalog"
            log.warning("Celestrak notice for group %r: %s", group, notice)
            return
        except Exception as exc:
            # Clients see a generic message; the upstream details stay in the server log.
            self.last_error = "Celestrak fetch failed; using the cached catalog"
            log.warning("Celestrak fetch failed (%s); using cached catalog", exc)
            return

        fetched_at = datetime.now(UTC)
        text = "\n".join(f"{r.name}\n{r.line1}\n{r.line2}" for r in records) + "\n"

        def write_cache() -> None:
            self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(text)

        await asyncio.to_thread(write_cache)
        await self._ingest(records, fetched_at)
        self._group_fetched_at = fetched_at
        self._group_retry_at = None
        self.last_error = None

    async def _not_updated(self, notice: CelestrakNotUpdated) -> None:
        """Celestrak refused the download: this address got the current data less than 2 h ago.

        Nothing is asked again before Celestrak's next update (a repeat is refused again and
        counts toward its error limit). Our copy is that data if it is our own download (the cache
        file is from then), or if the cache was lost with a container restart while the database
        kept the catalog; otherwise someone else on this address fetched it, and ours is older.
        """
        now = datetime.now(UTC)
        since = notice.since or now
        self._group_retry_at = since + _CELESTRAK_UPDATE
        cached = self._cache_mtime()
        ours = (
            cached >= since - timedelta(minutes=10)
            if cached is not None
            else await asyncio.to_thread(self._count) > 0
        )
        if ours:
            self._group_fetched_at = max(self.last_group_fetch() or since, since)
            self.last_error = None
            log.info("Celestrak group unchanged since %s; our copy is current", since.isoformat())
        else:
            self.last_error = "Celestrak refused a repeat download; retrying after its next update"
            log.warning("Celestrak refused the group (served to this address at %s)", since)

    async def _ingest_text(self, text: str, fetched_at: datetime) -> None:
        await self._ingest(await asyncio.to_thread(parse_tle_text, text), fetched_at)

    async def _ingest(self, records: list[TleRecord], fetched_at: datetime) -> None:
        def work() -> int:
            with self._sessions() as db:
                return upsert_tle_records(db, records, fetched_at)

        count = await asyncio.to_thread(work)
        log.info("catalog: upserted %d satellites", count)
        self._refresh_registry(records)
        await self.rebuild_fleet()

    async def rebuild_fleet(self) -> None:
        """Rebuild the fleet, its link profiles and packet templates from the DB off the event
        loop; swap them in.

        Link profiles and packet templates are synced from the seed here first: either can only be
        stored once its satellite is catalogued, and every ingest ends up here.
        """

        def build() -> Fleet:
            with self._sessions() as db:
                sync_link_profiles(db)
                sync_packet_templates(db)
                rows = load_fleet_rows(db)
                return Fleet.build(
                    rows,
                    load_link_profiles(db),
                    load_packet_templates(db),
                    load_downlinks(),
                    generic_antenna_deg=load_generic_antenna_deg(),
                    no_radio=[r.norad_id for r in rows if has_no_radio(r.name, r.norad_id)],
                )

        async with self._fleet_lock:
            fleet = await asyncio.to_thread(build)
            self.fleet = fleet
        log.info(
            "fleet: %d satellites ready for propagation, %d with a link profile, %d with their own"
            " packet templates, generic set %s (%d without a radio)",
            len(fleet),
            len(fleet.links.rows),
            len(fleet.comms),
            "loaded" if fleet.comms.generic is not None else "missing",
            len(fleet.comms.no_radio),
        )

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
        """How many satellites the table holds."""
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
        if not self._remote_budget.take():
            log.info("Celestrak remote search skipped: global rate limit reached")
            return False
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
        self._remember(self._remote_queries, key, now)
        if records:
            await self._ingest(records, datetime.now(UTC))
        return True

    @staticmethod
    def _remember[K](queries: dict[K, float], key: K, now: float) -> None:
        """Note when a query was sent, dropping expired ones once there are many."""
        if len(queries) >= _MAX_REMEMBERED_QUERIES:
            for old in [k for k, t in queries.items() if now - t >= _REMOTE_QUERY_TTL_S]:
                del queries[old]
        queries[key] = now

    def _may_fetch_catnr(self, norad_id: int) -> bool:
        """Whether a stale satellite may be fetched by catalog number now: not if it was within
        Celestrak's update interval, nor once the budget all clients share is spent."""
        now = time.monotonic()
        last = self._catnr_fetches.get(norad_id, -_REMOTE_QUERY_TTL_S)
        if now - last < _REMOTE_QUERY_TTL_S:
            return False
        if not self._remote_budget.take():
            log.info("Celestrak fetch of %d skipped: global rate limit reached", norad_id)
            return False
        self._remember(self._catnr_fetches, norad_id, now)
        return True

    async def resolve(self, norad_id: int) -> Elements | None:
        """Load (and if stale, refresh) a satellite's elements into the registry.

        A stale or unknown satellite is fetched from Celestrak by catalog number at most once per
        Celestrak update and within the budget remote searches use; otherwise (and when that
        fails) whatever the database holds is used.
        """
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
        if stale and self._may_fetch_catnr(norad_id):
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

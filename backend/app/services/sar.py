"""SAR archive explore: search Sentinel-1 RTC passes, run comparisons, serve their results.

The service between the REST routes, the STAC/token clients, the job queue and the database:

* **Searches** go to Planetary Computer through one global budget (and a back-off after a 429),
  cached per area and window; nothing is written to the database.
* **Comparisons** belong to a session (the routes check its token; the session must be live, so
  per-session budgets can't be dodged by opening sessions). Their scenes are stored as a shared
  cache of public metadata. Each runs once in the worker process; its images live in the
  ephemeral cache, and a comparison whose images are gone reads as expired.
* **Retention**: comparisons of sessions ended past ``comms_retention_days`` are deleted with
  their files; scene rows nothing uses are deleted too.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.data.http import UpstreamError, UpstreamRateLimited, new_client
from app.db.models import MissionSession, SarComparison, SarScene, utcnow
from app.models.api import (
    SarAoiIn,
    SarAoiOut,
    SarComparisonCreate,
    SarComparisonList,
    SarComparisonOut,
    SarDisplayOut,
    SarLimitsOut,
    SarPassOut,
    SarPlatformOut,
    SarRegionOut,
    SarSearchOut,
    SarSourceOut,
    SarTrackOut,
)
from app.sar.assets import blob_ref, vsiaz_path
from app.sar.grid import (
    AoiGrid,
    AoiOutOfRange,
    aoi_grid,
    bbox_lonlat,
    lattice_lonlat,
    polygon_lonlat,
)
from app.sar.jobs import JobRunner, QueueFull
from app.sar.pairs import (
    AOI_SIZES_KM,
    ARCHIVE_START,
    MIN_COVERAGE_EST,
    PASS_GAP_S,
    Pass,
    build_tracks,
    group_passes,
    propose,
    search_window,
)
from app.sar.params import change_detection_params
from app.sar.protocol import (
    ERROR_MESSAGES,
    SourceLimits,
    Stage,
    WorkerFrame,
    WorkerJob,
    WorkerResult,
    gdal_options,
)
from app.sar.stac import COLLECTION, POLARIZATIONS, SarItem, SearchPage, StacClient
from app.sar.storage import ProductKind, SarStorage
from app.sar.token import SasTokenProvider
from app.services.ratelimit import TokenBucket

log = logging.getLogger(__name__)

#: Statuses of a comparison that still has work ahead.
ACTIVE_STATUSES = ("queued", "reading", "detecting")
#: The dataset's page (linked in the UI).
DATASET_URL = "https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc"
#: The licence of the RTC collection.
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
#: Most search windows kept in the cache.
_WINDOW_CACHE_MAX = 500
#: Most scene items kept in memory for resolving a comparison's frames.
_ITEM_CACHE_MAX = 5000
#: How often retention and the cache cap are enforced.
_MAINTENANCE_EVERY_S = 3600.0
_PLATFORM_NAME = re.compile(r"^SENTINEL-1[A-Z]$")


class SarError(Exception):
    """A request the service refuses; ``status`` is the HTTP code, the message is shown as is."""

    status = 400

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SarNotFound(SarError):
    """No such comparison in this session."""

    status = 404


class SarConflict(SarError):
    """Not possible in the comparison's or the session's current state."""

    status = 409


class SarInvalid(SarError):
    """The request is well-formed but can't be served (dates, area, scenes)."""

    status = 422


class SarRateLimited(SarError):
    """A budget is spent; try again later."""

    status = 429


class SarUnavailable(SarError):
    """The archive or the worker can't take it now."""

    status = 503


class SarService:
    """Searches, comparisons, their images and their retention (see the module docstring)."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        catalog: Any,
        sessions: Any,
        *,
        http: httpx.AsyncClient | None = None,
        executor_factory: Any = None,
        source_path: Any = None,
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._catalog = catalog
        self._session_manager = sessions
        self._own_http = http is None
        self._http = http or new_client(settings.http_timeout_s)
        key = settings.sar_subscription_key
        key_value = key.get_secret_value() if key is not None else None
        self._stac = StacClient(
            settings.sar_stac_url,
            http=self._http,
            max_bytes=settings.sar_stac_max_bytes,
            account=settings.sar_blob_account,
            container=settings.sar_blob_container,
            subscription_key=key_value,
        )
        self._tokens = SasTokenProvider(
            settings.sar_token_url,
            COLLECTION,
            http=self._http,
            max_bytes=settings.sar_stac_max_bytes,
            subscription_key=key_value,
        )
        self.storage = SarStorage(settings.sar_storage_dir, settings.sar_storage_max_bytes)
        self._runner = JobRunner(
            queue_max=settings.sar_queue_max,
            timeout_s=settings.sar_job_timeout_s,
            nice=settings.sar_worker_nice,
            process=self._process,
            executor_factory=executor_factory,
        )
        self._source_path = source_path or self._vsiaz_path
        per_min = settings.sar_upstream_requests_per_min
        self._upstream = TokenBucket(rate=per_min / 60, burst=min(per_min, 10))
        total = settings.sar_comparisons_per_hour_total
        self._global_compares = TokenBucket(rate=total / 3600, burst=min(total, 10))
        self._search_budgets: dict[int, TokenBucket] = {}
        self._compare_budgets: dict[int, TokenBucket] = {}
        self._windows: OrderedDict[tuple, tuple[float, SearchPage]] = OrderedDict()
        self._items: OrderedDict[str, SarItem] = OrderedDict()
        self._backoff_until = 0.0
        self._status = "unknown"
        self._platforms: tuple[int, dict[str, SarPlatformOut]] | None = None
        self._maintenance: asyncio.Task | None = None
        self._params = change_detection_params()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        """Reconcile rows and files left by a previous process, then start the queue."""
        await asyncio.to_thread(self._reconcile)
        self._runner.start()
        self._session_manager.add_end_listener(self.session_ended)
        self._maintenance = asyncio.create_task(self._maintain(), name="sar-maintenance")

    async def stop(self) -> None:
        """Stop the queue (a running worker is killed) and the maintenance task."""
        await self._runner.stop()
        if self._maintenance is not None:
            self._maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance
        if self._own_http:
            await self._http.aclose()

    def _reconcile(self) -> None:
        with self._sessions() as db:
            db.execute(
                update(SarComparison)
                .where(SarComparison.status.in_(ACTIVE_STATUSES))
                .values(status="failed", error="interrupted", finished_at=utcnow())
            )
            db.commit()
            known = set(db.scalars(select(SarComparison.id)))
        self.storage.remove_tmp()
        self.storage.remove_orphans(known)

    async def _maintain(self) -> None:
        while True:
            await asyncio.sleep(_MAINTENANCE_EVERY_S)
            try:
                await asyncio.to_thread(self.purge)
                await asyncio.to_thread(self._enforce_cap)
            except Exception:
                log.exception("SAR maintenance failed")

    def purge(self) -> int:
        """Delete comparisons and files of sessions ended past the retention, and unused scenes."""
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.comms_retention_days)
        ended = select(MissionSession.id).where(
            MissionSession.ended_at.is_not(None), MissionSession.ended_at < cutoff
        )
        with self._sessions() as db:
            ids = list(
                db.scalars(select(SarComparison.id).where(SarComparison.session_id.in_(ended)))
            )
            if ids:
                db.execute(delete(SarComparison).where(SarComparison.id.in_(ids)))
            used = select(SarComparison.scene_before_id).union(select(SarComparison.scene_after_id))
            scenes = db.execute(
                delete(SarScene).where(SarScene.fetched_at < cutoff, SarScene.id.not_in(used))
            )
            db.commit()
        for cid in ids:
            self.storage.remove_comparison(cid)
        removed = len(ids) + (scenes.rowcount or 0)  # type: ignore[attr-defined]
        if removed:
            log.info(
                "SAR retention: removed %d comparisons and %d scenes", len(ids), removed - len(ids)
            )
        return removed

    def _enforce_cap(self) -> None:
        evicted = self.storage.evict()
        if evicted:
            self._mark_expired(evicted)

    def _mark_expired(self, ids: set[int]) -> None:
        with self._sessions() as db:
            db.execute(
                update(SarComparison)
                .where(SarComparison.id.in_(ids), SarComparison.status == "ready")
                .values(status="expired")
            )
            db.commit()

    async def session_ended(self, session_id: int) -> None:
        """Cancel a session's queued or running comparisons and forget its budgets."""
        self._search_budgets.pop(session_id, None)
        self._compare_budgets.pop(session_id, None)
        dropped = self._runner.cancel_session(session_id)
        if dropped:
            await asyncio.to_thread(self._fail, dropped, "session_ended")

    def _fail(self, ids: list[int], code: str) -> None:
        with self._sessions() as db:
            db.execute(
                update(SarComparison)
                .where(SarComparison.id.in_(ids), SarComparison.status.in_(ACTIVE_STATUSES))
                .values(status="failed", error=code, finished_at=utcnow())
            )
            db.commit()

    # ---------- reference ----------

    def platforms(self) -> dict[str, SarPlatformOut]:
        """Sentinel-1 satellites in the catalog, by normalised platform name."""
        fleet = self._catalog.fleet
        if self._platforms is not None and self._platforms[0] == id(fleet):
            return self._platforms[1]
        found: dict[str, SarPlatformOut] = {}
        for name, norad_id in zip(fleet.names, fleet.norad_ids, strict=True):
            if _PLATFORM_NAME.match(name):
                found[name.lower()] = SarPlatformOut(
                    platform=name.lower(), satellite_name=name, norad_id=int(norad_id)
                )
        self._platforms = (id(fleet), found)
        return found

    def source(self) -> SarSourceOut:
        """The archive, its terms, the method and the limits (no upstream call)."""
        p = self._params
        s = self.settings
        return SarSourceOut(
            enabled=s.sar_enabled,
            status=self._status,  # type: ignore[arg-type]
            collection=COLLECTION,
            title="Sentinel-1 Radiometrically Terrain Corrected (RTC)",
            license="CC BY 4.0",
            license_url=LICENSE_URL,
            dataset_url=DATASET_URL,
            attribution=[
                "Contains modified Copernicus Sentinel data",
                "RTC processing by Catalyst; hosted by Microsoft Planetary Computer",
                "CC BY 4.0",
            ],
            archive_start=ARCHIVE_START,
            search_window_days=12,
            resolution_m=10.0,
            polarizations=list(POLARIZATIONS),
            params_version=p.version,
            speckle_window_px=p.speckle.window_px,
            min_abs_db=p.threshold.min_abs_db,
            min_area_m2=p.regions.min_area_m2,
            display={pol: self._display(pol) for pol in POLARIZATIONS},
            limits=SarLimitsOut(
                searches_per_min=s.sar_searches_per_min,
                comparisons_per_hour=s.sar_comparisons_per_hour,
                max_per_session=s.sar_max_comparisons_per_session,
                aoi_sizes_km=list(AOI_SIZES_KM),
            ),
            platforms=sorted(self.platforms().values(), key=lambda p: p.platform),
        )

    def _display(self, polarization: str) -> SarDisplayOut:
        stretch = self._params.stretch(polarization)
        return SarDisplayOut(
            db_min=stretch.db_min,
            db_max=stretch.db_max,
            heatmap_max_db=self._params.display.heatmap_max_db,
        )

    # ---------- checks ----------

    def _require_live(self, session_id: int) -> None:
        if not self.settings.sar_enabled:
            raise SarUnavailable("Archive imagery is off on this server.")
        if not self._session_manager.is_live(session_id):
            raise SarConflict("The session isn't running; reload the page to start a new one.")

    @staticmethod
    def _take(budgets: dict[int, TokenBucket], session_id: int, per: float, burst: int) -> bool:
        bucket = budgets.setdefault(session_id, TokenBucket(rate=per, burst=burst))
        return bucket.take()

    def _grid(self, aoi: SarAoiIn) -> AoiGrid:
        try:
            return aoi_grid(aoi.lat_deg, aoi.lon_deg, aoi.size_km)
        except AoiOutOfRange as exc:
            raise SarInvalid(str(exc)) from None

    @staticmethod
    def _check_dates(before: date, after: date) -> date:
        today = datetime.now(UTC).date()
        if not ARCHIVE_START <= before < after <= today:
            raise SarInvalid(
                f"Pick two dates between {ARCHIVE_START.isoformat()} and today, the first earlier."
            )
        return today

    # ---------- search ----------

    async def search(
        self,
        session_id: int,
        aoi: SarAoiIn,
        before: date,
        after: date,
        polarization: str,
        platform: str | None,
    ) -> SarSearchOut:
        """Passes over the area around both dates, grouped by track, with the proposed pair."""
        self._require_live(session_id)
        per_min = self.settings.sar_searches_per_min
        if not self._take(self._search_budgets, session_id, per_min / 60, min(per_min, 3)):
            raise SarRateLimited("Too many searches; wait a moment.", retry_after_s=10)
        grid = self._grid(aoi)
        today = self._check_dates(before, after)
        days = 12
        b_window = search_window(before, days, today)
        a_window = search_window(after, days, today)
        bbox = bbox_lonlat(grid)
        page_b, page_a = await asyncio.gather(
            self._window(grid, bbox, *b_window), self._window(grid, bbox, *a_window)
        )
        points = np.array(lattice_lonlat(grid))
        passes_b = group_passes(page_b.items, points)
        passes_a = group_passes(page_a.items, points)
        tracks = build_tracks(
            passes_b,
            passes_a,
            before_date=before,
            after_date=after,
            polarization=polarization,
            platform=platform,
        )
        proposal = propose(tracks, before_date=before, after_date=after)
        if proposal is not None:
            tracks.sort(key=lambda t: t.key != proposal.track)
        return SarSearchOut(
            aoi=self._aoi_out(aoi, grid),
            polarization=polarization,  # type: ignore[arg-type]
            platform=platform,
            before_window=(b_window[0].date(), b_window[1].date()),
            after_window=(a_window[0].date(), a_window[1].date()),
            tracks=[
                SarTrackOut(
                    key=t.key,
                    relative_orbit=t.relative_orbit,
                    orbit_state=t.orbit_state,  # type: ignore[arg-type]
                    before=[self._pass_out(p) for p in t.before],
                    after=[self._pass_out(p) for p in t.after],
                    proposed_before=proposal.before.key
                    if proposal and proposal.track == t.key
                    else None,
                    proposed_after=proposal.after.key
                    if proposal and proposal.track == t.key
                    else None,
                )
                for t in tracks
            ],
            proposed_track=proposal.track if proposal else None,
            skipped=page_b.skipped + page_a.skipped,
            truncated=page_b.truncated or page_a.truncated,
        )

    def _upstream_slot(self) -> None:
        if time.monotonic() < self._backoff_until:
            raise SarUnavailable(
                "The archive asked us to slow down; try again in a minute.",
                retry_after_s=max(self._backoff_until - time.monotonic(), 1),
            )
        if not self._upstream.take():
            raise SarUnavailable(
                "The archive is busy for everyone right now; try again shortly.", 10
            )

    async def _upstream_call(self, call: Any) -> Any:
        self._upstream_slot()
        try:
            result = await call
        except UpstreamRateLimited as exc:
            self._backoff_until = time.monotonic() + exc.retry_after_s
            self._status = "unavailable"
            raise SarUnavailable(
                "The archive asked us to slow down; try again in a minute.", exc.retry_after_s
            ) from None
        except UpstreamError as exc:
            self._status = "unavailable"
            log.warning("SAR upstream failed: %s", exc)
            raise SarUnavailable(
                "The archive can't be reached right now; try again later.", 30
            ) from None
        self._status = "ok"
        return result

    async def _window(
        self, grid: AoiGrid, bbox: tuple[float, float, float, float], start: datetime, end: datetime
    ) -> SearchPage:
        key = (grid.key(), start.isoformat(), end.isoformat())
        now = time.monotonic()
        cached = self._windows.get(key)
        if cached is not None and cached[0] > now:
            self._windows.move_to_end(key)
            return cached[1]
        page: SearchPage = await self._upstream_call(self._stac.search(bbox, start, end))
        self._windows[key] = (now + self.settings.sar_search_cache_minutes * 60, page)
        while len(self._windows) > _WINDOW_CACHE_MAX:
            self._windows.popitem(last=False)
        for item in page.items:
            self._items[item.stac_id] = item
            self._items.move_to_end(item.stac_id)
        while len(self._items) > _ITEM_CACHE_MAX:
            self._items.popitem(last=False)
        return page

    async def _resolve(self, stac_id: str) -> SarItem:
        item = self._items.get(stac_id)
        if item is None:
            item = await self._upstream_call(self._stac.item(stac_id))
            if item is None:
                raise SarInvalid("A chosen scene isn't in the archive.")
            self._items[stac_id] = item
        return item

    # ---------- comparisons ----------

    async def create(self, session_id: int, body: SarComparisonCreate) -> SarComparisonOut:
        """Check and queue a comparison of two same-track passes."""
        self._require_live(session_id)
        per_hour = self.settings.sar_comparisons_per_hour
        if not self._take(self._compare_budgets, session_id, per_hour / 3600, min(per_hour, 3)):
            raise SarRateLimited("Too many comparisons this hour; wait a while.", retry_after_s=600)
        self._check_dates(body.requested_before, body.requested_after)
        grid = self._grid(body.aoi)
        stored = await asyncio.to_thread(self._count, session_id)
        if stored >= self.settings.sar_max_comparisons_per_session:
            raise SarConflict("This session keeps at most a few comparisons; remove one first.")
        before = [await self._resolve(i) for i in body.before]
        after = [await self._resolve(i) for i in body.after]
        points = np.array(lattice_lonlat(grid))
        pass_b = self._as_pass(before, points, body.polarization)
        pass_a = self._as_pass(after, points, body.polarization)
        if pass_b.track != pass_a.track:
            raise SarInvalid("Both passes must be on the same track.")
        if pass_b.acquired_at >= pass_a.acquired_at:
            raise SarInvalid("The before pass must be earlier than the after pass.")
        if not self._global_compares.take():
            raise SarUnavailable("The imagery worker is fully booked this hour; try later.", 300)
        sim_now = self._session_manager.sim_now(session_id) or datetime.now(UTC)
        row_id = await asyncio.to_thread(
            self._insert, session_id, body, grid, pass_b, pass_a, sim_now
        )
        try:
            self._runner.submit(row_id, session_id)
        except QueueFull:
            await asyncio.to_thread(self._delete, row_id)
            raise SarUnavailable("The imagery worker is busy; try again in a minute.", 60) from None
        return await self.get(session_id, row_id)

    @staticmethod
    def _as_pass(frames: list[SarItem], points: np.ndarray, polarization: str) -> Pass:
        tracks = {(f.platform, f.relative_orbit, f.orbit_state) for f in frames}
        times = sorted(f.acquired_at for f in frames)
        if len(tracks) != 1 or (times[-1] - times[0]).total_seconds() > PASS_GAP_S * len(frames):
            raise SarInvalid("The frames of a pass must belong to one overflight.")
        passes = group_passes(frames, points)
        if len(passes) != 1:
            raise SarInvalid("The chosen pass doesn't cover the area.")
        chosen = passes[0]
        if polarization not in chosen.polarizations:
            raise SarInvalid(f"The chosen pass has no {polarization} band.")
        if chosen.coverage < MIN_COVERAGE_EST:
            raise SarInvalid("The chosen pass covers too little of the area.")
        return chosen

    def _count(self, session_id: int) -> int:
        with self._sessions() as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(SarComparison)
                    .where(SarComparison.session_id == session_id)
                )
                or 0
            )

    def _upsert_scenes(self, db: Session, frames: tuple[SarItem, ...]) -> list[int]:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        ids = []
        for f in frames:
            values = {
                "source": "sentinel1",
                "collection": COLLECTION,
                "stac_scene_id": f.stac_id,
                "platform": f.platform,
                "orbit_state": f.orbit_state,
                "relative_orbit": f.relative_orbit,
                "polarizations": list(f.polarizations),
                "acquired_at": f.acquired_at,
                "min_lon_deg": f.bbox[0],
                "min_lat_deg": f.bbox[1],
                "max_lon_deg": f.bbox[2],
                "max_lat_deg": f.bbox[3],
                "footprint": f.footprint,
                "epsg": f.epsg,
                "assets": dict(f.hrefs),
                "fetched_at": utcnow(),
            }
            stmt = insert(SarScene).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[SarScene.collection, SarScene.stac_scene_id],
                set_={
                    k: stmt.excluded[k] for k in values if k not in ("collection", "stac_scene_id")
                },
            )
            db.execute(stmt)
            ids.append(
                db.scalar(
                    select(SarScene.id).where(
                        SarScene.collection == COLLECTION, SarScene.stac_scene_id == f.stac_id
                    )
                )
            )
        return ids

    def _insert(
        self,
        session_id: int,
        body: SarComparisonCreate,
        grid: AoiGrid,
        pass_b: Pass,
        pass_a: Pass,
        sim_now: datetime,
    ) -> int:
        bbox = bbox_lonlat(grid)
        with self._sessions() as db:
            before_ids = self._upsert_scenes(db, pass_b.frames)
            after_ids = self._upsert_scenes(db, pass_a.frames)
            row = SarComparison(
                session_id=session_id,
                scene_before_id=before_ids[0],
                scene_after_id=after_ids[0],
                frames={
                    "before": before_ids,
                    "after": after_ids,
                    "coverage": {"before": pass_b.coverage, "after": pass_a.coverage},
                },
                status="queued",
                polarization=body.polarization,
                aoi_lat_deg=body.aoi.lat_deg,
                aoi_lon_deg=body.aoi.lon_deg,
                aoi_size_km=body.aoi.size_km,
                min_lon_deg=bbox[0],
                min_lat_deg=bbox[1],
                max_lon_deg=bbox[2],
                max_lat_deg=bbox[3],
                aoi_geojson={"type": "Polygon", "coordinates": [polygon_lonlat(grid)]},
                grid=grid.to_dict(),
                requested_before=body.requested_before,
                requested_after=body.requested_after,
                method="log_ratio",
                params_version=self._params.version,
                generation=1,
                sim_time=sim_now,
            )
            db.add(row)
            db.commit()
            return row.id

    def _delete(self, comparison_id: int) -> None:
        with self._sessions() as db:
            db.execute(delete(SarComparison).where(SarComparison.id == comparison_id))
            db.commit()
        self.storage.remove_comparison(comparison_id)

    def _load(
        self, session_id: int | None, comparison_id: int
    ) -> tuple[SarComparison, dict[int, SarScene]] | None:
        with self._sessions() as db:
            row = db.get(SarComparison, comparison_id)
            if row is None or (session_id is not None and row.session_id != session_id):
                return None
            ids = set(row.frames.get("before", [])) | set(row.frames.get("after", []))
            scenes = {s.id: s for s in db.scalars(select(SarScene).where(SarScene.id.in_(ids)))}
            db.expunge_all()
            return row, scenes

    def _expire_if_gone(self, row: SarComparison) -> None:
        if row.status == "ready" and not self.storage.has_products(row.id, row.generation):
            self._mark_expired({row.id})
            row.status = "expired"

    async def get(self, session_id: int, comparison_id: int) -> SarComparisonOut:
        """One of the session's comparisons (an unknown or foreign id is a 404)."""
        loaded = await asyncio.to_thread(self._load, session_id, comparison_id)
        if loaded is None:
            raise SarNotFound(f"comparison {comparison_id} not found")
        row, scenes = loaded
        await asyncio.to_thread(self._expire_if_gone, row)
        return self._out(row, scenes)

    async def comparisons(self, session_id: int) -> SarComparisonList:
        """The session's comparisons, newest first."""

        def load() -> list[tuple[SarComparison, dict[int, SarScene]]]:
            with self._sessions() as db:
                ids = list(
                    db.scalars(
                        select(SarComparison.id)
                        .where(SarComparison.session_id == session_id)
                        .order_by(SarComparison.id.desc())
                    )
                )
            out = []
            for cid in ids:
                loaded = self._load(session_id, cid)
                if loaded is not None:
                    self._expire_if_gone(loaded[0])
                    out.append(loaded)
            return out

        rows = await asyncio.to_thread(load)
        return SarComparisonList(
            comparisons=[self._out(row, scenes) for row, scenes in rows],
            max_per_session=self.settings.sar_max_comparisons_per_session,
            running=self._runner.busy,
        )

    async def recompute(self, session_id: int, comparison_id: int) -> SarComparisonOut:
        """Run a failed or expired comparison again (a new generation of images)."""
        self._require_live(session_id)
        loaded = await asyncio.to_thread(self._load, session_id, comparison_id)
        if loaded is None:
            raise SarNotFound(f"comparison {comparison_id} not found")
        row, _ = loaded
        await asyncio.to_thread(self._expire_if_gone, row)
        if row.status not in ("failed", "expired"):
            raise SarConflict("Only a failed or expired comparison can be recomputed.")
        per_hour = self.settings.sar_comparisons_per_hour
        if not self._take(self._compare_budgets, session_id, per_hour / 3600, min(per_hour, 3)):
            raise SarRateLimited("Too many comparisons this hour; wait a while.", retry_after_s=600)
        if not self._global_compares.take():
            raise SarUnavailable("The imagery worker is fully booked this hour; try later.", 300)

        def requeue() -> None:
            with self._sessions() as db:
                db.execute(
                    update(SarComparison)
                    .where(SarComparison.id == comparison_id)
                    .values(
                        status="queued",
                        error=None,
                        generation=SarComparison.generation + 1,
                        finished_at=None,
                    )
                )
                db.commit()

        await asyncio.to_thread(requeue)
        try:
            self._runner.submit(comparison_id, session_id)
        except QueueFull:
            await asyncio.to_thread(self._fail, [comparison_id], "interrupted")
            raise SarUnavailable("The imagery worker is busy; try again in a minute.", 60) from None
        return await self.get(session_id, comparison_id)

    async def remove(self, session_id: int, comparison_id: int) -> None:
        """Delete a comparison and its images (cancelled if it is still running)."""
        loaded = await asyncio.to_thread(self._load, session_id, comparison_id)
        if loaded is None:
            raise SarNotFound(f"comparison {comparison_id} not found")
        self._runner.cancel(comparison_id)
        await asyncio.to_thread(self._delete, comparison_id)

    async def image(self, session_id: int, comparison_id: int, kind: ProductKind) -> Path:
        """The path of one image of a ready comparison (404 if not ready or expired)."""
        loaded = await asyncio.to_thread(self._load, session_id, comparison_id)
        if loaded is None:
            raise SarNotFound(f"comparison {comparison_id} not found")
        row, _ = loaded
        await asyncio.to_thread(self._expire_if_gone, row)
        if row.status != "ready":
            raise SarNotFound("images not available")
        path = self.storage.product(row.id, row.generation, kind)
        self.storage.touch(path)
        return path

    # ---------- the worker ----------

    def _vsiaz_path(self, href: str) -> str:
        ref = blob_ref(
            href, account=self.settings.sar_blob_account, container=self.settings.sar_blob_container
        )
        return vsiaz_path(ref)

    def _frames(
        self, ids: list[int], scenes: dict[int, SarScene], polarization: str, grid: AoiGrid
    ) -> tuple[WorkerFrame, ...]:
        frames = []
        for sid in ids:
            scene = scenes[sid]
            href = scene.assets[polarization]
            frames.append(
                WorkerFrame(
                    stac_id=scene.stac_scene_id,
                    path=self._source_path(href),
                    expected_epsg=scene.epsg,
                    crop_path=str(
                        self.storage.crop_path(scene.stac_scene_id, polarization, grid.key())
                    ),
                )
            )
        return tuple(frames)

    async def _set_status(self, comparison_id: int, status: str) -> None:
        def write() -> None:
            with self._sessions() as db:
                db.execute(
                    update(SarComparison)
                    .where(
                        SarComparison.id == comparison_id, SarComparison.status.in_(ACTIVE_STATUSES)
                    )
                    .values(status=status)
                )
                db.commit()

        await asyncio.to_thread(write)

    async def _process(self, comparison_id: int) -> None:
        loaded = await asyncio.to_thread(self._load, None, comparison_id)
        if loaded is None:
            return
        row, scenes = loaded
        if row.status != "queued":
            return
        if not self._session_manager.is_live(row.session_id):
            await asyncio.to_thread(self._fail, [comparison_id], "session_ended")
            return
        await self._set_status(comparison_id, "reading")
        grid = AoiGrid.from_dict(row.grid)
        try:
            before = self._frames(row.frames["before"], scenes, row.polarization, grid)
            after = self._frames(row.frames["after"], scenes, row.polarization, grid)
        except (KeyError, ValueError):
            await asyncio.to_thread(self._fail, [comparison_id], "invalid_raster")
            return
        products = self.storage.products_dir(row.id, row.generation)
        frames = before + after
        missing = [f for f in frames if not Path(f.crop_path).is_file()]
        options: dict = {}
        if missing:
            try:
                token = await self._upstream_call(
                    self._tokens.get(min_valid_s=self.settings.sar_job_timeout_s + 60)
                )
            except SarError:
                await asyncio.to_thread(self._fail, [comparison_id], "token_failed")
                return
            options = gdal_options(
                account=self.settings.sar_blob_account,
                sas_token=token.get_secret_value(),
                read_timeout_s=self.settings.sar_read_timeout_s,
                cache_mb=self.settings.sar_gdal_cache_mb,
            )
        crop_bytes = grid.width * grid.height * 4
        pinned = {Path(f.crop_path) for f in frames} | {products}
        evicted = await asyncio.to_thread(
            self.storage.evict, crop_bytes * (len(missing) + 1), pinned
        )
        if evicted:
            await asyncio.to_thread(self._mark_expired, evicted)
        job = WorkerJob(
            comparison_id=row.id,
            generation=row.generation,
            grid=grid.to_dict(),
            polarization=row.polarization,
            before=before,
            after=after,
            products_dir=str(products),
            storage_root=str(self.storage.root),
            params=self._params.model_dump(by_alias=True),
            limits=SourceLimits(max_window_px=int(max(grid.width, grid.height) * 1.25) + 16),
            gdal=options,
        )

        async def on_stage(stage: Stage) -> None:
            await self._set_status(
                comparison_id, "reading" if stage == Stage.READING else "detecting"
            )

        started = time.monotonic()
        result = await self._runner.execute(job, on_stage)
        if result.ok and not self._session_manager.is_live(row.session_id):
            result = WorkerResult(ok=False, error_code="session_ended")
        await asyncio.to_thread(self._finish, comparison_id, result)
        log.info(
            "SAR comparison %d: %s in %.1fs (coverage %s, %d regions, peak %s MB, %s)",
            comparison_id,
            "ready" if result.ok else result.error_code,
            time.monotonic() - started,
            result.coverage,
            len(result.regions),
            result.peak_rss_mb,
            result.timings_s,
        )

    def _finish(self, comparison_id: int, result: WorkerResult) -> None:
        with self._sessions() as db:
            row = db.get(SarComparison, comparison_id)
            if row is None or row.status not in ACTIVE_STATUSES:
                return
            row.finished_at = utcnow()
            if result.ok:
                row.status = "ready"
                row.error = None
                row.threshold_db = result.threshold_db
                row.score = result.changed_fraction
                row.regions = result.regions
                row.stats = {
                    "coverage": result.coverage,
                    "otsu_db": result.otsu_db,
                    "increase_area_m2": result.increase_area_m2,
                    "decrease_area_m2": result.decrease_area_m2,
                    "regions_truncated": result.regions_truncated,
                    "widespread": result.widespread,
                    "timings_s": result.timings_s,
                }
            else:
                row.status = "failed"
                row.error = (
                    result.error_code if result.error_code in ERROR_MESSAGES else "read_failed"
                )
                row.stats = {"coverage": result.coverage} if result.coverage is not None else None
            db.commit()

    # ---------- output ----------

    def _aoi_out(self, aoi: SarAoiIn, grid: AoiGrid) -> SarAoiOut:
        return SarAoiOut(
            lat_deg=aoi.lat_deg,
            lon_deg=aoi.lon_deg,
            size_km=aoi.size_km,
            bbox=bbox_lonlat(grid),
            polygon=polygon_lonlat(grid),
            epsg=grid.epsg,
            width_px=grid.width,
            height_px=grid.height,
            resolution_m=grid.resolution_m,
        )

    def _pass_out(self, p: Pass) -> SarPassOut:
        known = self.platforms().get(p.platform)
        return SarPassOut(
            id=p.key,
            frames=[f.stac_id for f in p.frames],
            platform=p.platform,
            satellite_name=p.platform.upper(),
            norad_id=known.norad_id if known else None,
            acquired_at=p.acquired_at,
            orbit_state=p.frames[0].orbit_state,  # type: ignore[arg-type]
            relative_orbit=p.frames[0].relative_orbit,
            polarizations=list(p.polarizations),
            coverage=round(p.coverage, 3),
        )

    def _stored_pass(
        self, ids: list[int], scenes: dict[int, SarScene], coverage: float
    ) -> SarPassOut:
        frames = [scenes[i] for i in ids if i in scenes]
        first = frames[0]
        known = self.platforms().get(first.platform)
        common = set(first.polarizations)
        for f in frames[1:]:
            common &= set(f.polarizations)
        return SarPassOut(
            id=first.stac_scene_id,
            frames=[f.stac_scene_id for f in frames],
            platform=first.platform,
            satellite_name=first.platform.upper(),
            norad_id=known.norad_id if known else None,
            acquired_at=min(f.acquired_at for f in frames),
            orbit_state=first.orbit_state,  # type: ignore[arg-type]
            relative_orbit=first.relative_orbit,
            polarizations=sorted(common),
            coverage=round(coverage, 3),
        )

    def _out(self, row: SarComparison, scenes: dict[int, SarScene]) -> SarComparisonOut:
        grid = AoiGrid.from_dict(row.grid)
        stats = row.stats or {}
        coverage = row.frames.get("coverage", {})
        before = self._stored_pass(row.frames["before"], scenes, coverage.get("before", 0.0))
        after = self._stored_pass(row.frames["after"], scenes, coverage.get("after", 0.0))
        years = sorted({before.acquired_at.year, after.acquired_at.year})
        aoi = SarAoiIn(
            lat_deg=row.aoi_lat_deg, lon_deg=row.aoi_lon_deg, size_km=int(row.aoi_size_km)
        )
        return SarComparisonOut(
            id=row.id,
            status=row.status,  # type: ignore[arg-type]
            queue_position=self._runner.position(row.id) if row.status == "queued" else None,
            error=ERROR_MESSAGES.get(row.error) if row.error else None,
            aoi=self._aoi_out(aoi, grid),
            polarization=row.polarization,  # type: ignore[arg-type]
            before=before,
            after=after,
            requested_before=row.requested_before,
            requested_after=row.requested_after,
            method="log_ratio",
            params_version=row.params_version,
            generation=row.generation,
            coverage=stats.get("coverage"),
            otsu_db=stats.get("otsu_db"),
            threshold_db=row.threshold_db,
            floor_db=self._params.threshold.min_abs_db,
            changed_fraction=row.score,
            increase_area_m2=stats.get("increase_area_m2"),
            decrease_area_m2=stats.get("decrease_area_m2"),
            regions=[SarRegionOut.model_validate(r) for r in (row.regions or [])],
            regions_truncated=bool(stats.get("regions_truncated", False)),
            widespread=bool(stats.get("widespread", False)),
            display=self._display(row.polarization),
            attribution="Contains modified Copernicus Sentinel data "
            + "\u2013".join(str(y) for y in years),
            sim_time=row.sim_time,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

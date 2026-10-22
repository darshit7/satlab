"""The SatNOGS frame archive: real frames per satellite, identified against its packet templates.

Runs off the sim loop. For every satellite whose downlink is replayed it fetches recent frames at
most every ``satnogs_refresh_hours`` within one global request budget (SatNOGS rate-limits hard; a
429 backs everyone off for its Retry-After), keeps those that decode cleanly with one of the
satellite's templates, and swaps them in per satellite with one assignment. Raw frames are cached
on disk, so a restart doesn't refetch; the cache is our own file but is validated like any input.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.data.satnogs import Frame, SatnogsClient, SatnogsRateLimited, SatnogsUnavailable
from app.models.ws import ArchiveStatus
from app.packets.table import CommsTable
from app.packets.templates import MAX_FRAME_BYTES, identify
from app.services.catalog import CatalogService
from app.services.ratelimit import TokenBucket
from app.sim.comms import ArchivedFrame

log = logging.getLogger(__name__)

_CHECK_EVERY_S = 300.0


class _CachedFrame(BaseModel):
    hex: str = Field(max_length=2 * MAX_FRAME_BYTES, pattern=r"^(?:[0-9a-f]{2})+$")
    observation_id: int | None = None
    station_id: int | None = None
    received_at: datetime | None = None


class _CacheFile(BaseModel):
    norad_id: int
    fetched_at: float  # Unix seconds
    frames: list[_CachedFrame] = Field(max_length=1000)


class SatnogsArchive:
    def __init__(
        self, settings: Settings, catalog: CatalogService, client: SatnogsClient | None = None
    ) -> None:
        self.settings = settings
        self._catalog = catalog
        self._client = client or SatnogsClient(
            settings.satnogs_db_url, settings.satnogs_api_token, settings.http_timeout_s
        )
        self._dir = settings.cache_dir / "satnogs"
        self._raw: dict[int, tuple[float, list[Frame]]] = {}  # NORAD ID → (fetched at, frames)
        self._frames: dict[int, dict[str, tuple[ArchivedFrame, ...]]] = {}
        self._failed: set[int] = set()
        self._identified_with: CommsTable | None = None
        per_min = settings.satnogs_requests_per_min
        self._budget = TokenBucket(rate=per_min / 60, burst=per_min)
        self._backoff_until = 0.0
        self._task: asyncio.Task[None] | None = None

    # ----- FrameArchive (read by the sim loop) -----

    def frames(self, norad_id: int, template: str) -> Sequence[ArchivedFrame]:
        return self._frames.get(norad_id, {}).get(template, ())

    def status(self, norad_id: int) -> ArchiveStatus:
        if not self._client.configured:
            return "not_configured"
        if self._frames.get(norad_id):
            return "ok"
        return "unavailable" if norad_id in self._failed else "empty"

    # ----- lifecycle -----

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="satnogs-archive")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()

    async def _run(self) -> None:
        await asyncio.to_thread(self._load_cache)
        while True:
            try:
                self._reidentify_if_templates_changed()
                if self._client.configured:
                    await self.refresh_stale()
            except Exception:  # never let the background task die
                log.exception("SatNOGS archive: refresh failed")
            await asyncio.sleep(_CHECK_EVERY_S)

    # ----- refresh -----

    async def refresh_stale(self) -> None:
        max_age_s = self.settings.satnogs_refresh_hours * 3600
        for norad_id in self._catalog.fleet.comms.replayed_ids:
            fetched_at = self._raw.get(norad_id, (0.0, []))[0]
            if time.time() - fetched_at < max_age_s:
                continue
            if time.monotonic() < self._backoff_until or not self._budget.take():
                return  # out of budget: the rest wait for the next check
            await self.refresh(norad_id)

    async def refresh(self, norad_id: int) -> None:
        try:
            frames = await self._client.frames(
                norad_id,
                max_pages=self.settings.satnogs_max_pages,
                limit=self.settings.satnogs_max_frames,
            )
        except SatnogsRateLimited as exc:
            self._backoff_until = time.monotonic() + exc.retry_after_s
            log.warning("SatNOGS: rate limited; backing off %.0f s", exc.retry_after_s)
            return
        except SatnogsUnavailable as exc:
            self._failed.add(norad_id)
            log.warning("SatNOGS: frames for %d unavailable (%s)", norad_id, exc)
            return
        self._failed.discard(norad_id)
        fetched_at = time.time()
        self._raw[norad_id] = (fetched_at, frames)
        self._identify(norad_id, self._catalog.fleet.comms)
        await asyncio.to_thread(self._save_cache, norad_id, fetched_at, frames)
        log.info(
            "SatNOGS: %d frames for %d, %d usable",
            len(frames),
            norad_id,
            sum(len(v) for v in self._frames.get(norad_id, {}).values()),
        )

    def _identify(self, norad_id: int, table: CommsTable) -> None:
        sat = table.by_norad.get(norad_id)
        frames = self._raw.get(norad_id, (0.0, []))[1]
        by_template: dict[str, list[ArchivedFrame]] = {}
        if sat is not None:
            templates = sat.replayed
            for frame in frames:
                found = identify(templates, frame.raw)
                if found is not None:
                    by_template.setdefault(found[0].name, []).append(
                        ArchivedFrame(
                            raw=frame.raw,
                            template=found[0].name,
                            observation_id=frame.observation_id,
                            station_id=frame.station_id,
                            received_at=frame.received_at,
                        )
                    )
        # oldest first, so a pass replays them in the order they were received
        self._frames[norad_id] = {k: tuple(reversed(v)) for k, v in by_template.items()}

    def _reidentify_if_templates_changed(self) -> None:
        table = self._catalog.fleet.comms
        if table is not self._identified_with:
            self._identified_with = table
            for norad_id in list(self._raw):
                self._identify(norad_id, table)

    # ----- disk cache -----

    def _cache_path(self, norad_id: int) -> Path:
        return self._dir / f"{int(norad_id)}.json"

    def _save_cache(self, norad_id: int, fetched_at: float, frames: list[Frame]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        cache = _CacheFile(
            norad_id=norad_id,
            fetched_at=fetched_at,
            frames=[
                _CachedFrame(
                    hex=f.raw.hex(),
                    observation_id=f.observation_id,
                    station_id=f.station_id,
                    received_at=f.received_at,
                )
                for f in frames
            ],
        )
        self._cache_path(norad_id).write_text(cache.model_dump_json())

    def _load_cache(self) -> None:
        if not self._dir.is_dir():
            return
        for path in self._dir.glob("*.json"):
            try:
                cache = _CacheFile.model_validate(json.loads(path.read_text()))
            except (OSError, ValueError, ValidationError):
                log.warning("SatNOGS: ignoring unreadable cache file %s", path.name)
                continue
            frames = [
                Frame(bytes.fromhex(f.hex), f.observation_id, f.station_id, f.received_at)
                for f in cache.frames
            ]
            self._raw[cache.norad_id] = (cache.fetched_at, frames)

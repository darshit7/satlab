"""Pass schedule for one satellite over a session's location, persisted to ``pass_window``."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import MissionSession, PassWindow
from app.models.api import PassPrediction, PassWindowOut
from app.services.catalog import CatalogService
from app.services.sessions import SessionManager
from app.sim.geometry import Observer
from app.sim.passes import Span, find_spans


class NotFound(Exception):
    pass


class PassService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        catalog: CatalogService,
        sessions: SessionManager,
    ) -> None:
        self.settings = settings
        self._sessions = session_factory
        self._catalog = catalog
        self._live = sessions

    async def predict(
        self, session_id: int, norad_id: int, start: datetime | None, hours: float
    ) -> PassPrediction:
        def load() -> MissionSession | None:
            with self._sessions() as db:
                return db.get(MissionSession, session_id)

        session = await asyncio.to_thread(load)
        if session is None:
            raise NotFound("mission session not found")
        elements = await self._catalog.resolve(norad_id)
        if elements is None:
            raise NotFound("satellite not found")

        start = (start or self._live.sim_now(session_id) or datetime.now(UTC)).astimezone(UTC)
        end = start + timedelta(hours=hours)
        # Search past both ends so passes in progress at either end get their true AOS / LOS.
        pad = timedelta(seconds=min(max(elements.period_s, 3600.0), 12 * 3600.0))
        observer = Observer(session.lat_deg, session.lon_deg, session.alt_m)
        mask = self.settings.horizon_mask_deg
        [spans] = await asyncio.to_thread(
            find_spans,
            [elements.satellite.model],
            observer,
            start - pad,
            (end - start + 2 * pad).total_seconds(),
            self.settings.pass_search_step_s,
            mask,
        )
        always_visible = len(spans) == 1 and spans[0].aos is None and spans[0].los is None
        passes = [s for s in spans if _overlaps(s, start, end)]

        def persist() -> None:
            with self._sessions() as db:
                db.execute(
                    delete(PassWindow).where(
                        PassWindow.session_id == session_id,
                        PassWindow.norad_id == norad_id,
                        PassWindow.aos_time < end,
                        PassWindow.los_time > start,
                    )
                )
                db.add_all(
                    PassWindow(
                        session_id=session_id,
                        norad_id=norad_id,
                        station_id=session.station_id,
                        aos_time=p.aos,
                        los_time=p.los,
                        max_elev_deg=p.max_elev_deg,
                        aos_az_deg=p.aos_az_deg,
                        los_az_deg=p.los_az_deg,
                    )
                    for p in passes
                )
                db.commit()

        await asyncio.to_thread(persist)
        return PassPrediction(
            session_id=session_id,
            station_id=session.station_id,
            norad_id=norad_id,
            name=elements.name,
            tle_epoch=elements.epoch,
            horizon_mask_deg=mask,
            start=start,
            end=end,
            always_visible=always_visible,
            passes=[_out(p) for p in passes],
        )


def _overlaps(span: Span, start: datetime, end: datetime) -> bool:
    """A complete pass (AOS and LOS both found) that overlaps [start, end]."""
    return span.aos is not None and span.los is not None and span.los > start and span.aos < end


def _out(span: Span) -> PassWindowOut:
    assert span.aos is not None and span.los is not None
    assert span.aos_az_deg is not None and span.los_az_deg is not None
    return PassWindowOut(
        aos_time=span.aos,
        los_time=span.los,
        max_time=span.max_time,
        max_elev_deg=round(span.max_elev_deg, 3),
        aos_az_deg=round(span.aos_az_deg, 2),
        los_az_deg=round(span.los_az_deg, 2),
        duration_s=round((span.los - span.aos).total_seconds(), 1),
    )

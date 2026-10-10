"""FastAPI application: REST endpoints + the per-session WebSocket stream."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings
from app.data.classify import reclassify_all
from app.data.seed import seed_ground_stations
from app.db.base import get_db, get_sessionmaker
from app.db.models import GroundStation, MissionSession, Satellite
from app.models.api import (
    CatalogStatus,
    GroundStationCreate,
    GroundStationOut,
    MissionSessionCreate,
    MissionSessionOut,
    PassPrediction,
    SatelliteOut,
    SatelliteSearchResult,
)
from app.models.ws import ErrorMessage, NoradId, client_message_adapter
from app.services.catalog import CatalogService
from app.services.passes import NotFound, PassService
from app.services.sessions import SessionManager, SessionUnavailable
from app.sim.loop import Subscriber

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)-9s %(name)s: %(message)s")

WS_CLOSE_SESSION_UNAVAILABLE = 4404


def run_migrations() -> None:
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_migrate:
        await asyncio.to_thread(run_migrations)
    session_factory = get_sessionmaker()
    with session_factory() as db:
        seed_ground_stations(db)

    def reclassify() -> None:
        with session_factory() as db:
            if changed := reclassify_all(db):
                log.info("classification: updated %d satellites", changed)

    await asyncio.to_thread(reclassify)  # before the fleet is built from these rows

    catalog = CatalogService(settings, session_factory)
    sessions = SessionManager(settings, session_factory, catalog)
    app.state.catalog = catalog
    app.state.sessions = sessions
    app.state.passes = PassService(settings, session_factory, catalog, sessions)
    catalog.start()  # background: never blocks startup or the sim loop
    try:
        yield
    finally:
        await sessions.shutdown()
        await catalog.stop()


app = FastAPI(title="Satellite Mission Control Simulator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

Db = Annotated[Session, Depends(get_db)]


def get_catalog(request: Request) -> CatalogService:
    return request.app.state.catalog


def get_sessions(request: Request) -> SessionManager:
    return request.app.state.sessions


def get_passes(request: Request) -> PassService:
    return request.app.state.passes


Catalog = Annotated[CatalogService, Depends(get_catalog)]
Sessions = Annotated[SessionManager, Depends(get_sessions)]
Passes = Annotated[PassService, Depends(get_passes)]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- ground stations ----------


@app.get("/api/stations", response_model=list[GroundStationOut])
def list_stations(db: Db) -> list[GroundStation]:
    return list(db.scalars(select(GroundStation).order_by(GroundStation.id)))


@app.post("/api/stations", response_model=GroundStationOut, status_code=201)
def create_station(body: GroundStationCreate, db: Db) -> GroundStation:
    station = GroundStation(**body.model_dump())
    db.add(station)
    db.commit()
    return station


# ---------- satellite catalog ----------


@app.get("/api/satellites", response_model=SatelliteSearchResult)
async def search_satellites(
    catalog: Catalog,
    q: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    remote: bool = False,
) -> SatelliteSearchResult:
    results, remote_searched = await catalog.search(q, limit, remote)
    return SatelliteSearchResult(
        query=q,
        remote_searched=remote_searched,
        results=[SatelliteOut.model_validate(s) for s in results],
    )


@app.get("/api/satellites/{norad_id}", response_model=SatelliteOut)
def get_satellite(norad_id: int, db: Db) -> Satellite:
    sat = db.get(Satellite, norad_id)
    if sat is None:
        raise HTTPException(404, f"satellite {norad_id} not in local catalog")
    return sat


@app.get("/api/catalog/status", response_model=CatalogStatus)
async def catalog_status(catalog: Catalog) -> CatalogStatus:
    return CatalogStatus(
        satellite_count=await catalog.status_count(),
        group=catalog.settings.catalog_group,
        last_fetched_at=catalog.last_group_fetch(),
        refreshing=catalog.refreshing,
        last_error=catalog.last_error,
    )


# ---------- mission sessions ----------


@app.post("/api/sessions", response_model=MissionSessionOut, status_code=201)
def create_session(body: MissionSessionCreate, db: Db) -> MissionSession:
    if db.get(GroundStation, body.station_id) is None:
        raise HTTPException(404, f"ground station {body.station_id} not found")
    time_scale = min(body.time_scale, get_settings().max_time_scale)
    session = MissionSession(
        station_id=body.station_id,
        name=body.name,
        sim_start_time=body.sim_start_time or datetime.now(UTC),
        time_scale=time_scale,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.get("/api/sessions/{session_id}", response_model=MissionSessionOut)
def get_session(session_id: int, db: Db) -> MissionSession:
    session = db.get(MissionSession, session_id)
    if session is None:
        raise HTTPException(404, f"mission session {session_id} not found")
    return session


@app.post("/api/sessions/{session_id}/end", status_code=204)
async def end_session(session_id: int, sessions: Sessions) -> None:
    await sessions.end(session_id)


@app.get("/api/sessions/{session_id}/passes", response_model=PassPrediction)
async def predict_passes(
    session_id: int,
    passes: Passes,
    norad_id: Annotated[NoradId, Query()],
    hours: Annotated[float, Query(ge=1, le=72)] = 24,
    start: Annotated[AwareDatetime | None, Query()] = None,
) -> PassPrediction:
    """Upcoming passes of one satellite over the session's station (default: from sim time)."""
    try:
        return await passes.predict(session_id, norad_id, start, hours)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from None


# ---------- live stream ----------


async def _pump(ws: WebSocket, sub: Subscriber) -> None:
    while True:
        for message in await sub.drain():
            await ws.send_text(message.model_dump_json())


@app.websocket("/ws/sessions/{session_id}")
async def session_stream(ws: WebSocket, session_id: int) -> None:
    await ws.accept()
    manager: SessionManager = ws.app.state.sessions
    try:
        runtime, sub = await manager.attach(session_id)
    except SessionUnavailable as exc:
        await ws.send_text(ErrorMessage(message=str(exc)).model_dump_json())
        await ws.close(code=WS_CLOSE_SESSION_UNAVAILABLE, reason="session unavailable")
        return

    sender = asyncio.create_task(_pump(ws, sub))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                message = client_message_adapter.validate_json(raw)
            except ValidationError as exc:
                sub.offer(ErrorMessage(message=f"invalid message: {exc.errors()[0]['msg']}"))
                continue
            try:
                await manager.handle(runtime, message)
            except Exception:
                log.exception("session %d: failed to handle %s", session_id, message.type)
                sub.offer(ErrorMessage(message=f"server error handling {message.type}"))
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        await manager.detach(runtime, sub)

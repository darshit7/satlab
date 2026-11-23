"""FastAPI application: REST endpoints + the per-session WebSocket stream."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings
from app.data.classify import reclassify_all
from app.data.seed import seed_ground_stations
from app.db.base import get_db, get_sessionmaker
from app.db.models import GroundStation, LinkProfile, MissionSession, Satellite
from app.models.api import (
    CatalogStatus,
    CommandParamOut,
    CommandTemplateOut,
    GenericCommsOut,
    GroundStationOut,
    LinkProfileOut,
    LinkProfilesOut,
    LinkSourceOut,
    MissionSessionCreate,
    MissionSessionCreated,
    MissionSessionOut,
    PacketTemplatesOut,
    PassPrediction,
    SarAoiIn,
    SarComparisonCreate,
    SarComparisonList,
    SarComparisonOut,
    SarSearchOut,
    SarSourceOut,
    SatelliteCommsOut,
    SatelliteOut,
    SatelliteSearchResult,
    TelemetryTemplateOut,
)
from app.models.ws import (
    MAX_SESSION_TOKEN_LENGTH,
    ErrorMessage,
    NoradId,
    auth_message_adapter,
    client_message_adapter,
)
from app.packets.templates import CommandTemplate, TelemetryTemplate
from app.services.catalog import CatalogService
from app.services.comms import CommsWriter
from app.services.passes import NotFound, PassService
from app.services.ratelimit import TokenBucket
from app.services.sar import SarError, SarService
from app.services.satnogs import SatnogsArchive
from app.services.sessions import (
    ServerBusy,
    SessionManager,
    SessionUnavailable,
    new_session_token,
    session_token_matches,
)
from app.sim.loop import Subscriber

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)-9s %(name)s: %(message)s")

WS_CLOSE_POLICY_VIOLATION = 1008  #: foreign Origin, or flooding
WS_CLOSE_AUTH_REQUIRED = 4401  #: no valid ``auth`` message in time
WS_CLOSE_SESSION_UNAVAILABLE = 4404  #: unknown, ended, or not this token's session
WS_CLOSE_SERVER_BUSY = 4503  #: at max_active_sessions; the client retries with backoff
CUSTOM_LOCATION_NAME = "Custom location"


def run_migrations() -> None:
    """Upgrade the database to the latest Alembic revision (``SATLAB_AUTO_MIGRATE``)."""
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start-up and shutdown of the API process.

    Migrates (if enabled), seeds the stations, reclassifies the catalog, then builds the
    long-lived services and starts their background tasks (catalog refresh, SatNOGS archive,
    comms writer). On shutdown the sessions end first, then the services stop.
    """
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
    archive = SatnogsArchive(settings, catalog)
    writer = CommsWriter(settings, session_factory)
    sessions = SessionManager(settings, session_factory, catalog, archive, writer.offer)
    app.state.catalog = catalog
    app.state.archive = archive
    app.state.writer = writer
    app.state.sessions = sessions
    app.state.passes = PassService(settings, session_factory, catalog, sessions)
    sar = SarService(settings, session_factory, catalog, sessions)
    app.state.sar = sar
    # background: never block startup or the sim loop
    catalog.start()
    archive.start()
    writer.start()
    await sar.start()
    try:
        yield
    finally:
        await sessions.shutdown()
        await sar.stop()
        await writer.stop()
        await archive.stop()
        await catalog.stop()


_docs = get_settings().docs_enabled
app = FastAPI(
    title="Satellite Mission Control Simulator",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

Db = Annotated[Session, Depends(get_db)]
# Capability token of a mission session (see MissionSessionCreated).
SessionToken = Annotated[
    str | None, Header(alias="X-Session-Token", max_length=MAX_SESSION_TOKEN_LENGTH)
]


def get_catalog(request: Request) -> CatalogService:
    """FastAPI dependency: the process's catalog service."""
    return request.app.state.catalog


def get_sessions(request: Request) -> SessionManager:
    """FastAPI dependency: the process's session manager."""
    return request.app.state.sessions


def get_sar(request: Request) -> SarService:
    """FastAPI dependency: the process's SAR imagery service."""
    return request.app.state.sar


def get_passes(request: Request) -> PassService:
    """FastAPI dependency: the process's pass-prediction service."""
    return request.app.state.passes


Catalog = Annotated[CatalogService, Depends(get_catalog)]
Sessions = Annotated[SessionManager, Depends(get_sessions)]
Passes = Annotated[PassService, Depends(get_passes)]
Sar = Annotated[SarService, Depends(get_sar)]


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check (used by the container platform and the smoke test)."""
    return {"status": "ok"}


# ---------- ground stations ----------


@app.get("/api/stations", response_model=list[GroundStationOut])
def list_stations(db: Db) -> list[GroundStation]:
    """The preset ground stations, in picker order."""
    return list(db.scalars(select(GroundStation).order_by(GroundStation.id)))


# ---------- link profiles ----------


@app.get("/api/link-profiles", response_model=LinkProfilesOut)
def list_link_profiles(db: Db) -> LinkProfilesOut:
    """Published downlink budgets in use (reference data, like the stations)."""
    settings = get_settings()
    rows = db.execute(
        select(LinkProfile, Satellite.name)
        .join(Satellite, Satellite.norad_id == LinkProfile.norad_id)
        .order_by(Satellite.name)
    )
    return LinkProfilesOut(
        green_margin_db=settings.link_green_margin_db,
        amber_margin_db=settings.link_amber_margin_db,
        profiles=[
            LinkProfileOut(
                norad_id=profile.norad_id,
                satellite_name=name,
                label=profile.label,
                frequency_hz=profile.frequency_hz,
                eirp_dbw=profile.eirp_dbw,
                station_gt_db_k=profile.station_gt_db_k,
                system_loss_db=profile.system_loss_db,
                atmospheric_loss_db=profile.atmospheric_loss_db,
                atmospheric_ref_elev_deg=profile.atmospheric_ref_elev_deg,
                required_ebn0_db=profile.required_ebn0_db,
                data_rate_bps=profile.data_rate_bps,
                coverage_half_angle_deg=profile.coverage_half_angle_deg,
                sources=[LinkSourceOut.model_validate(src) for src in profile.sources],
            )
            for profile, name in rows
        ],
    )


# ---------- packet templates ----------


@app.get("/api/packet-templates", response_model=PacketTemplatesOut)
def list_packet_templates(request: Request, catalog: Catalog) -> PacketTemplatesOut:
    """Satellites with packet templates of their own (what they send, and the commands they
    take), and the generic set every other satellite uses."""
    fleet = catalog.fleet
    archive: SatnogsArchive = request.app.state.archive

    def sources(items: list) -> list[LinkSourceOut]:
        return [LinkSourceOut(title=s.title, url=s.url) for s in items]

    def telemetry(items: tuple[tuple[int, TelemetryTemplate], ...]) -> list[TelemetryTemplateOut]:
        return [
            TelemetryTemplateOut(
                name=t.name,
                label=t.label,
                framing=t.framing.type,
                interval_s=t.interval_s,
                downlink=t.downlink,
                layout_sourced=t.layout_sourced,
                sources=sources(t.sources),
            )
            for _, t in items
        ]

    def commands(items: tuple[tuple[int, CommandTemplate], ...]) -> list[CommandTemplateOut]:
        return [
            CommandTemplateOut(
                name=c.name,
                label=c.label,
                framing=c.framing.type,
                params=[CommandParamOut.model_validate(p.model_dump()) for p in c.params],
                response=c.response,
                layout_sourced=c.layout_sourced,
                sources=sources(c.sources),
            )
            for _, c in items
        ]

    satellites = []
    for norad_id, sat in sorted(fleet.comms.by_norad.items()):
        row = fleet.index.get(norad_id)
        satellites.append(
            SatelliteCommsOut(
                norad_id=norad_id,
                satellite_name=fleet.names[row] if row is not None else str(norad_id),
                telemetry=telemetry(sat.telemetry),
                commands=commands(sat.commands),
                downlink_frequency_hz=sat.downlink.frequency_hz if sat.downlink else None,
                downlink_sources=sources(list(sat.downlink.sources)) if sat.downlink else [],
            )
        )
    generic = fleet.comms.generic
    return PacketTemplatesOut(
        satnogs_configured=archive.status(0) != "not_configured",
        satellites=satellites,
        generic=GenericCommsOut(
            telemetry=telemetry(generic.telemetry),
            commands=commands(generic.commands),
            antenna_half_angle_deg=generic.antenna_half_angle_deg,
        )
        if generic is not None
        else None,
    )


# ---------- satellite catalog ----------


@app.get("/api/satellites", response_model=SatelliteSearchResult)
async def search_satellites(
    catalog: Catalog,
    q: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    remote: bool = False,
) -> SatelliteSearchResult:
    """Search the local catalog by name or NORAD id.

    With ``remote=true`` Celestrak is asked too (rate-limited across all clients); its results
    are added to the catalog.
    """
    results, remote_searched = await catalog.search(q, limit, remote)
    return SatelliteSearchResult(
        query=q,
        remote_searched=remote_searched,
        results=[SatelliteOut.model_validate(s) for s in results],
    )


@app.get("/api/satellites/{norad_id}", response_model=SatelliteOut)
def get_satellite(norad_id: int, db: Db) -> Satellite:
    """One satellite of the local catalog."""
    sat = db.get(Satellite, norad_id)
    if sat is None:
        raise HTTPException(404, f"satellite {norad_id} not in local catalog")
    return sat


@app.get("/api/catalog/status", response_model=CatalogStatus)
async def catalog_status(catalog: Catalog) -> CatalogStatus:
    """How many satellites the catalog holds and when it was last refreshed."""
    return CatalogStatus(
        satellite_count=await catalog.status_count(),
        group=catalog.settings.catalog_group,
        last_fetched_at=catalog.last_group_fetch(),
        refreshing=catalog.refreshing,
        last_error=catalog.last_error,
        max_age_hours=catalog.settings.catalog_max_age_hours,
    )


# ---------- mission sessions ----------


@app.post("/api/sessions", response_model=MissionSessionCreated, status_code=201)
def create_session(body: MissionSessionCreate, db: Db) -> MissionSessionCreated:
    """Start a session at a preset station or at a custom location (kept on the session only)."""
    if body.station_id is not None:
        station = db.get(GroundStation, body.station_id)
        if station is None:
            raise HTTPException(404, "ground station not found")
        site = {
            "station_id": station.id,
            "location_name": station.name,
            "lat_deg": station.lat_deg,
            "lon_deg": station.lon_deg,
            "alt_m": station.alt_m,
        }
    else:
        assert body.location is not None  # the schema requires exactly one
        site = {
            "station_id": None,
            "location_name": CUSTOM_LOCATION_NAME,
            **body.location.model_dump(),
        }
    time_scale = min(body.time_scale, get_settings().max_time_scale)
    token, token_hash = new_session_token()
    session = MissionSession(
        **site,
        name=body.name,
        sim_start_time=body.sim_start_time or datetime.now(UTC),
        time_scale=time_scale,
        token_hash=token_hash,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return MissionSessionCreated(
        **MissionSessionOut.model_validate(session).model_dump(), token=token
    )


def _session_not_found(session_id: int) -> HTTPException:
    # The same answer for "no such session" and "not your session": IDs can't be probed.
    return HTTPException(404, f"mission session {session_id} not found")


@app.get("/api/sessions/{session_id}", response_model=MissionSessionOut)
def get_session(session_id: int, db: Db, token: SessionToken = None) -> MissionSession:
    """A mission session (needs its ``X-Session-Token``)."""
    session = db.get(MissionSession, session_id)
    if session is None or not session_token_matches(session, token):
        raise _session_not_found(session_id)
    return session


@app.post("/api/sessions/{session_id}/end", status_code=204)
async def end_session(session_id: int, sessions: Sessions, token: SessionToken = None) -> None:
    """End a mission session: its loop stops and its waiting commands are aborted."""
    if await sessions.owned(session_id, token) is None:
        raise _session_not_found(session_id)
    await sessions.end(session_id)


@app.get("/api/sessions/{session_id}/passes", response_model=PassPrediction)
async def predict_passes(
    session_id: int,
    passes: Passes,
    sessions: Sessions,
    norad_id: Annotated[NoradId, Query()],
    hours: Annotated[float, Query(ge=1, le=72)] = 24,
    start: Annotated[AwareDatetime | None, Query()] = None,
    token: SessionToken = None,
) -> PassPrediction:
    """Upcoming passes of one satellite over the session's location (default: from sim time)."""
    if await sessions.owned(session_id, token) is None:
        raise _session_not_found(session_id)
    try:
        return await passes.predict(session_id, norad_id, start, hours)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from None


# ---------- SAR imagery ----------


def _sar_error(exc: SarError) -> HTTPException:
    headers = (
        {"Retry-After": str(int(max(exc.retry_after_s, 1)))}
        if exc.retry_after_s is not None
        else None
    )
    return HTTPException(exc.status, str(exc), headers=headers)


async def _owned_session(sessions: SessionManager, session_id: int, token: str | None) -> None:
    if await sessions.owned(session_id, token) is None:
        raise _session_not_found(session_id)


@app.get("/api/sar/source", response_model=SarSourceOut)
def sar_source(sar: Sar) -> SarSourceOut:
    """The SAR archive, its licence and attribution, the method and the limits."""
    return sar.source()


@app.get("/api/sessions/{session_id}/sar/scenes", response_model=SarSearchOut)
async def sar_search(
    session_id: int,
    sar: Sar,
    sessions: Sessions,
    lat_deg: Annotated[float, Query(ge=-80, le=84)],
    lon_deg: Annotated[float, Query(ge=-180, le=180)],
    before: Annotated[date, Query()],
    after: Annotated[date, Query()],
    size_km: Annotated[int, Query(description="5, 10 or 20")] = 10,
    polarization: Annotated[Literal["VV", "VH"], Query()] = "VV",
    platform: Annotated[str | None, Query(pattern=r"^sentinel-1[a-z]$")] = None,
    token: SessionToken = None,
) -> SarSearchOut:
    """Sentinel-1 passes over an area around two dates, by track, with the proposed pair."""
    await _owned_session(sessions, session_id, token)
    try:
        aoi = SarAoiIn(lat_deg=lat_deg, lon_deg=lon_deg, size_km=size_km)  # type: ignore[arg-type]
    except ValidationError:
        raise HTTPException(422, "size_km must be 5, 10 or 20") from None
    try:
        return await sar.search(session_id, aoi, before, after, polarization, platform)
    except SarError as exc:
        raise _sar_error(exc) from None


@app.post(
    "/api/sessions/{session_id}/sar/comparisons", response_model=SarComparisonOut, status_code=202
)
async def sar_compare(
    session_id: int,
    body: SarComparisonCreate,
    sar: Sar,
    sessions: Sessions,
    token: SessionToken = None,
) -> SarComparisonOut:
    """Queue a comparison of two passes on the same track."""
    await _owned_session(sessions, session_id, token)
    try:
        return await sar.create(session_id, body)
    except SarError as exc:
        raise _sar_error(exc) from None


@app.get("/api/sessions/{session_id}/sar/comparisons", response_model=SarComparisonList)
async def sar_comparisons(
    session_id: int, sar: Sar, sessions: Sessions, token: SessionToken = None
) -> SarComparisonList:
    """The session's comparisons, newest first (poll this while any is running)."""
    await _owned_session(sessions, session_id, token)
    return await sar.comparisons(session_id)


@app.get(
    "/api/sessions/{session_id}/sar/comparisons/{comparison_id}", response_model=SarComparisonOut
)
async def sar_comparison(
    session_id: int, comparison_id: int, sar: Sar, sessions: Sessions, token: SessionToken = None
) -> SarComparisonOut:
    """One comparison of the session."""
    await _owned_session(sessions, session_id, token)
    try:
        return await sar.get(session_id, comparison_id)
    except SarError as exc:
        raise _sar_error(exc) from None


@app.post(
    "/api/sessions/{session_id}/sar/comparisons/{comparison_id}/recompute",
    response_model=SarComparisonOut,
    status_code=202,
)
async def sar_recompute(
    session_id: int, comparison_id: int, sar: Sar, sessions: Sessions, token: SessionToken = None
) -> SarComparisonOut:
    """Run a failed or expired comparison again."""
    await _owned_session(sessions, session_id, token)
    try:
        return await sar.recompute(session_id, comparison_id)
    except SarError as exc:
        raise _sar_error(exc) from None


@app.delete("/api/sessions/{session_id}/sar/comparisons/{comparison_id}", status_code=204)
async def sar_remove(
    session_id: int, comparison_id: int, sar: Sar, sessions: Sessions, token: SessionToken = None
) -> None:
    """Delete a comparison and its images."""
    await _owned_session(sessions, session_id, token)
    try:
        await sar.remove(session_id, comparison_id)
    except SarError as exc:
        raise _sar_error(exc) from None


@app.get(
    "/api/sessions/{session_id}/sar/comparisons/{comparison_id}/images/{kind}.png",
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}, "description": "The image."}},
)
async def sar_image(
    session_id: int,
    comparison_id: int,
    kind: Literal["before", "after", "change"],
    sar: Sar,
    sessions: Sessions,
    token: SessionToken = None,
) -> FileResponse:
    """One image of a ready comparison: before, after, or the change codes (see the docs)."""
    await _owned_session(sessions, session_id, token)
    try:
        path = await sar.image(session_id, comparison_id, kind)
    except SarError as exc:
        raise _sar_error(exc) from None
    if not path.is_file():
        raise HTTPException(404, "images not available")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


# ---------- live stream ----------


async def _pump(ws: WebSocket, sub: Subscriber) -> None:
    while True:
        for message in await sub.drain():
            await ws.send_text(message.model_dump_json())


async def _close_with_error(ws: WebSocket, message: str, code: int, reason: str) -> None:
    await ws.send_text(ErrorMessage(message=message).model_dump_json())
    await ws.close(code=code, reason=reason)


async def _receive_token(ws: WebSocket, timeout_s: float) -> str | None:
    """The token from the connection's first message, if it is a valid ``auth`` in time."""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout_s)
        return auth_message_adapter.validate_json(raw).token
    except (TimeoutError, ValidationError):
        return None


@app.websocket("/ws/sessions/{session_id}")
async def session_stream(ws: WebSocket, session_id: int) -> None:
    """The live socket of one mission session.

    Checks the browser ``Origin``, then waits for the ``auth`` message; attaches to the session
    and pumps its messages out while it validates, rate-limits and handles what the client
    sends. Close codes: ``WS_CLOSE_*``.
    """
    settings = get_settings()
    # CORS doesn't cover WebSockets: any page can open one, so check where a browser came from.
    # Non-browser clients send no Origin; they gain nothing a browser page could exploit.
    origin = ws.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        log.warning("session %d: rejected WebSocket from origin %r", session_id, origin[:100])
        await ws.close(code=WS_CLOSE_POLICY_VIOLATION)  # before accept: an HTTP 403
        return
    await ws.accept()
    manager: SessionManager = ws.app.state.sessions
    try:
        token = await _receive_token(ws, settings.ws_auth_timeout_s)
    except WebSocketDisconnect:
        return
    if token is None:
        await _close_with_error(
            ws, "authentication required", WS_CLOSE_AUTH_REQUIRED, "authentication required"
        )
        return
    try:
        runtime, sub = await manager.attach(session_id, token)
    except SessionUnavailable as exc:
        await _close_with_error(ws, str(exc), WS_CLOSE_SESSION_UNAVAILABLE, "session unavailable")
        return
    except ServerBusy:
        await _close_with_error(
            ws, "mission control is at capacity; retrying", WS_CLOSE_SERVER_BUSY, "server busy"
        )
        return

    sender = asyncio.create_task(_pump(ws, sub))
    budget = TokenBucket(settings.ws_messages_per_s, settings.ws_message_burst)
    try:
        while True:
            raw = await ws.receive_text()
            if not budget.take():  # a flooding client is disconnected, not queued
                log.warning("session %d: client over the message rate limit", session_id)
                await ws.close(code=WS_CLOSE_POLICY_VIOLATION, reason="too many messages")
                break
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

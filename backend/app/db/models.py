"""SQLAlchemy ORM models — the source of truth for the schema (AGENTS.md §8).

Phase 1 tables: ground_station, satellite, mission_session. Phase 2: pass_window. Phase 3:
link_profile. Phase 4b: packet_template, command, telemetry_frame. Phase 5: alarm (and
command.check_result). Generic comms: packet_template rows without a satellite. Phase 7a:
sar_scene, sar_comparison.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime


def utcnow() -> datetime:
    """The current wall-clock time, timezone-aware UTC (the default of the ``*_at`` columns)."""
    return datetime.now(UTC)


# ========== REFERENCE / CONFIG ==========


class GroundStation(Base):
    """A preset ground station, seeded from ``app/data/seed/ground_stations.json`` (read-only:
    there is no API to create one; a custom location belongs to its session)."""

    __tablename__ = "ground_station"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    alt_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Satellite(Base):
    """A catalogued satellite, by NORAD id: its name, classified type and current TLE.

    Bulk-loaded from Celestrak's ``active`` group and refreshed when stale; the simulation reads
    the in-memory fleet built from these rows, never the table itself.
    """

    __tablename__ = "satellite"

    norad_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: station | weather | comms | navigation | sar | other (from data/seed/satellite_types.json)
    type: Mapped[str | None] = mapped_column(String(16))
    is_sar_capable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    tle_line1: Mapped[str | None] = mapped_column(Text)
    tle_line2: Mapped[str | None] = mapped_column(Text)
    tle_epoch: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LinkProfile(Base):
    """A satellite's published downlink budget (seed: ``app/data/seed/link_profiles.json``).

    Synced from the seed for satellites already in the catalog, so the foreign key always holds.
    ``station_gt_db_k`` (the reference station's G/T) stands in for AGENTS.md's
    ``station_gain_dbi``: published budgets quote G/T, and Eb/N0 needs the noise temperature too.
    """

    __tablename__ = "link_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    norad_id: Mapped[int] = mapped_column(
        ForeignKey("satellite.norad_id"), nullable=False, unique=True
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    frequency_hz: Mapped[float] = mapped_column(Float, nullable=False)
    eirp_dbw: Mapped[float] = mapped_column(Float, nullable=False)
    #: [[off_nadir_deg, offset_db], ...] or null for the same EIRP in every direction
    eirp_pattern: Mapped[list[Any] | None] = mapped_column(JSON)
    coverage_half_angle_deg: Mapped[float | None] = mapped_column(Float)
    station_gt_db_k: Mapped[float] = mapped_column(Float, nullable=False)
    system_loss_db: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    #: atmospheric + rain loss as quoted at one elevation; scaled along the slant path (cosecant)
    atmospheric_loss_db: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    atmospheric_ref_elev_deg: Mapped[float] = mapped_column(
        Float, nullable=False, default=90.0, server_default="90"
    )
    required_ebn0_db: Mapped[float] = mapped_column(Float, nullable=False)
    data_rate_bps: Mapped[float] = mapped_column(Float, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, nullable=False)  #: [{title, url}, ...]


class PacketTemplate(Base):
    """One telemetry or command template of a satellite, or of the generic set (seed:
    ``packet_templates.json``).

    Synced by natural key (satellite, kind, name); the generic set's rows have no satellite and
    are shared by every satellite without its own templates, keyed by (kind, name). A template the
    seed drops is marked inactive rather than deleted, so commands that used it keep their foreign
    key.
    """

    __tablename__ = "packet_template"
    __table_args__ = (
        UniqueConstraint("norad_id", "kind", "name"),
        # NULLs never conflict in the constraint above, so the generic rows get their own unique
        # index: the sync's ON CONFLICT target.
        Index(
            "uq_packet_template_generic",
            "kind",
            "name",
            unique=True,
            sqlite_where=text("norad_id IS NULL"),
            postgresql_where=text("norad_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The satellite; NULL for the generic set, shared by every satellite without its own.
    norad_id: Mapped[int | None] = mapped_column(ForeignKey("satellite.norad_id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  #: telemetry | command
    framing: Mapped[str] = mapped_column(String(16), nullable=False)  #: ax25 | ccsds
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  #: the validated template
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")


# ========== OPERATIONAL / SESSION ==========


class MissionSession(Base):
    """A mission session observes from its own location: a preset station's, or a custom one.

    Custom locations (geolocation, map click, typed lat/lon) live only on the session; they are
    never saved as ``ground_station`` rows. ``station_id`` is set only when a preset was picked.
    """

    __tablename__ = "mission_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("ground_station.id"))
    location_name: Mapped[str] = mapped_column(Text, nullable=False)
    lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    alt_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    name: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sim_start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    time_scale: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0"
    )
    #: SHA-256 (hex) of the session's capability token; the token itself is never stored.
    token_hash: Mapped[str | None] = mapped_column(String(64))


class PassWindow(Base):
    """A predicted AOS → LOS pass of a satellite over a session's location (above the mask)."""

    __tablename__ = "pass_window"
    __table_args__ = (Index("ix_pass_sat_aos", "norad_id", "aos_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("mission_session.id"))
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("ground_station.id"))
    aos_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    los_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    max_elev_deg: Mapped[float] = mapped_column(Float, nullable=False)
    aos_az_deg: Mapped[float | None] = mapped_column(Float)
    los_az_deg: Mapped[float | None] = mapped_column(Float)


class Command(Base):
    """A command an operator sent in a session (simulated uplink: nothing is transmitted), live or
    booked for a pass (``scheduled_aos``)."""

    __tablename__ = "command"
    __table_args__ = (Index("ix_command_session_status", "session_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("packet_template.id"))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scheduled_aos: Mapped[datetime | None] = mapped_column(UTCDateTime)  #: the booked pass's AOS
    #: scheduled | queued | sent | acked | failed | aborted | held
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  #: when it was issued
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: the system check at AOS that released or held a booked command (a ``SystemCheck``)
    check_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class TelemetryFrame(Base):
    """A frame received in a session: simulated, or a real SatNOGS frame replayed."""

    __tablename__ = "telemetry_frame"
    __table_args__ = (Index("ix_tlm_session_simtime", "session_id", "sim_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("packet_template.id"))
    command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))  #: its response
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    wall_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    raw: Mapped[bytes | None] = mapped_column(LargeBinary)  #: the frame as received (≤ 512 bytes)
    decoded: Mapped[list[Any] | None] = mapped_column(JSON)  #: [{name, value, unit}], null if bad
    source: Mapped[str] = mapped_column(String(16), nullable=False)  #: satnogs | simulated
    crc_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON)  #: SatNOGS provenance


class Alarm(Base):
    """An alarm raised by pass automation in a session. ``seq`` numbers the session's alarms from 1
    (the client acknowledges by it)."""

    __tablename__ = "alarm"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    norad_id: Mapped[int | None] = mapped_column(ForeignKey("satellite.norad_id"))
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    wall_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  #: info | warning | critical
    #: power | thermal | storage | attitude | rf
    subsystem: Mapped[str | None] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )


# ========== SAR ==========


class SarScene(Base):
    """A Sentinel-1 RTC scene (one frame of a pass) used by a comparison: a shared cache of public
    STAC metadata, like ``satellite``. Rows no comparison uses are purged with the retention."""

    __tablename__ = "sar_scene"
    __table_args__ = (UniqueConstraint("collection", "stac_scene_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  #: sentinel1 (umbra: Phase 7b)
    collection: Mapped[str] = mapped_column(String(64), nullable=False)  #: STAC collection id
    stac_scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  #: sentinel-1a … sentinel-1d
    orbit_state: Mapped[str] = mapped_column(String(16), nullable=False)  #: ascending | descending
    #: 1-175; with ``orbit_state``, the track.
    relative_orbit: Mapped[int] = mapped_column(Integer, nullable=False)
    polarizations: Mapped[list[str]] = mapped_column(JSON, nullable=False)  #: e.g. ["VH", "VV"]
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    min_lon_deg: Mapped[float] = mapped_column(Float, nullable=False)  #: item bbox (PostGIS: Ph. 8)
    min_lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    footprint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  #: GeoJSON polygon(s)
    epsg: Mapped[int] = mapped_column(Integer, nullable=False)  #: the scene's UTM zone
    #: {polarization: href}: unsigned, checked against the allowed storage account.
    assets: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class SarComparison(Base):
    """A before/after comparison of one area, owned by a session (it lasts for the page visit;
    purged with ``SATLAB_COMMS_RETENTION_DAYS``, rows and files). Its image paths derive from
    ``id`` and ``generation``; nothing here is a filesystem path."""

    __tablename__ = "sar_comparison"
    __table_args__ = (
        Index("ix_sar_comparison_session", "session_id", "id"),
        Index("ix_sar_comparison_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    #: The best-covering frame of each pass; ``frames`` lists all of them.
    scene_before_id: Mapped[int] = mapped_column(ForeignKey("sar_scene.id"), nullable=False)
    scene_after_id: Mapped[int] = mapped_column(ForeignKey("sar_scene.id"), nullable=False)
    #: {"before": [sar_scene ids], "after": [...]}, best-covering first.
    frames: Mapped[dict[str, list[int]]] = mapped_column(JSON, nullable=False)
    #: queued | reading | detecting | ready | failed | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(32))  #: an error code (app.sar.protocol)
    polarization: Mapped[str] = mapped_column(String(4), nullable=False)  #: VV | VH
    aoi_lat_deg: Mapped[float] = mapped_column(Float, nullable=False)  #: the area's centre
    aoi_lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    aoi_size_km: Mapped[float] = mapped_column(Float, nullable=False)  #: side of the square
    min_lon_deg: Mapped[float] = mapped_column(Float, nullable=False)  #: envelope of aoi_geojson
    min_lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    #: The exact UTM square as a lon/lat GeoJSON Polygon (PostGIS geometry: Phase 8).
    aoi_geojson: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    grid: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  #: app.sar.grid.AoiGrid
    requested_before: Mapped[date] = mapped_column(Date, nullable=False)  #: as asked (UTC day)
    requested_after: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)  #: log_ratio
    params_version: Mapped[int] = mapped_column(Integer, nullable=False)  #: of the detection seed
    #: Run number, bumped by recompute (it names the image directory).
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    threshold_db: Mapped[float | None] = mapped_column(Float)  #: applied: max(Otsu, floor)
    score: Mapped[float | None] = mapped_column(Float)  #: changed fraction of valid pixels (0-1)
    #: coverage, otsu_db, areas, widespread, regions_truncated, timings.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    regions: Mapped[list[Any] | None] = mapped_column(JSON)  #: flagged regions (≤ 50)
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  #: session clock
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

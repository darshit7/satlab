"""SQLAlchemy ORM models — the source of truth for the schema (AGENTS.md §8).

Phase 1 tables: ground_station, satellite, mission_session. Phase 2: pass_window. Phase 3:
link_profile. Phase 4b: packet_template, command, telemetry_frame.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


# ========== REFERENCE / CONFIG ==========


class GroundStation(Base):
    __tablename__ = "ground_station"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lat_deg: Mapped[float] = mapped_column(Float, nullable=False)
    lon_deg: Mapped[float] = mapped_column(Float, nullable=False)
    alt_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Satellite(Base):
    __tablename__ = "satellite"

    norad_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # station | weather | comms | navigation | sar | other (from data/seed/satellite_types.json)
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
    # [[off_nadir_deg, offset_db], ...] or null for the same EIRP in every direction
    eirp_pattern: Mapped[list[Any] | None] = mapped_column(JSON)
    coverage_half_angle_deg: Mapped[float | None] = mapped_column(Float)
    station_gt_db_k: Mapped[float] = mapped_column(Float, nullable=False)
    system_loss_db: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    # atmospheric + rain loss as quoted at one elevation; scaled along the slant path (cosecant)
    atmospheric_loss_db: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    atmospheric_ref_elev_deg: Mapped[float] = mapped_column(
        Float, nullable=False, default=90.0, server_default="90"
    )
    required_ebn0_db: Mapped[float] = mapped_column(Float, nullable=False)
    data_rate_bps: Mapped[float] = mapped_column(Float, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, nullable=False)  # [{title, url}, ...]


class PacketTemplate(Base):
    """One telemetry or command template of a satellite (seed: ``packet_templates.json``).

    Synced by natural key (satellite, kind, name). A template the seed drops is marked inactive
    rather than deleted, so commands that used it keep their foreign key.
    """

    __tablename__ = "packet_template"
    __table_args__ = (UniqueConstraint("norad_id", "kind", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # telemetry | command
    framing: Mapped[str] = mapped_column(String(16), nullable=False)  # ax25 | ccsds
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # the validated template
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
    # SHA-256 (hex) of the session's capability token; the token itself is never stored.
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
    """A command an operator sent in a session (simulated uplink: nothing is transmitted)."""

    __tablename__ = "command"
    __table_args__ = (Index("ix_command_session_status", "session_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("packet_template.id"))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scheduled_aos: Mapped[datetime | None] = mapped_column(UTCDateTime)  # Phase 5
    # queued | sent | acked | failed | aborted
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # when it was issued
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class TelemetryFrame(Base):
    """A frame received in a session: simulated, or a real SatNOGS frame replayed."""

    __tablename__ = "telemetry_frame"
    __table_args__ = (Index("ix_tlm_session_simtime", "session_id", "sim_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mission_session.id"), nullable=False)
    norad_id: Mapped[int] = mapped_column(ForeignKey("satellite.norad_id"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("packet_template.id"))
    command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))  # its response
    sim_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    wall_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    raw: Mapped[bytes | None] = mapped_column(LargeBinary)  # the frame as received (≤ 512 bytes)
    decoded: Mapped[list[Any] | None] = mapped_column(JSON)  # [{name, value, unit}], null if bad
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # satnogs | simulated
    crc_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # SatNOGS provenance

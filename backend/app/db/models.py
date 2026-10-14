"""SQLAlchemy ORM models — the source of truth for the schema (AGENTS.md §8).

Phase 1 tables: ground_station, satellite, mission_session. Phase 2: pass_window.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
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

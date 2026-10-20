"""Idempotent seeding of reference data from versioned JSON under ``app/data/seed/``."""

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import GroundStation, LinkProfile, Satellite
from app.sim.linkbudget import LinkProfile as LinkProfileModel

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).parent / "seed"


def seed_ground_stations(db: Session) -> int:
    data = json.loads((SEED_DIR / "ground_stations.json").read_text(encoding="utf-8"))
    existing = set(db.scalars(select(GroundStation.name)))
    added = 0
    for entry in data["ground_stations"]:
        if entry["name"] not in existing:
            db.add(GroundStation(**entry))
            added += 1
    db.commit()
    return added


# ---------- link profiles ----------

OffNadirDeg = Annotated[float, Field(ge=0.0, le=90.0)]
OffsetDb = Annotated[float, Field(ge=-40.0, le=40.0)]


class LinkSource(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https://[^\s<>\"']+$", max_length=500)


class LinkCheck(BaseModel):
    """A margin the source itself worked out, for the tests to reproduce."""

    range_km: float = Field(gt=0, le=100_000)
    elevation_deg: float = Field(ge=0.0, le=90.0)
    off_nadir_deg: OffNadirDeg
    margin_db: float
    where: str = Field(max_length=200)


class LinkProfileSpec(BaseModel):
    norad_id: int = Field(ge=1, le=999_999_999)
    satellite_name: str = Field(max_length=100)
    label: str = Field(min_length=1, max_length=200)
    frequency_hz: float = Field(ge=1e6, le=1e11)
    eirp_dbw: float = Field(ge=-40.0, le=80.0)
    eirp_pattern: list[tuple[OffNadirDeg, OffsetDb]] | None = Field(default=None, max_length=90)
    coverage_half_angle_deg: float | None = Field(default=None, gt=0.0, le=90.0)
    station_gt_db_k: float = Field(ge=-40.0, le=60.0)
    system_loss_db: float = Field(ge=0.0, le=30.0)
    atmospheric_loss_db: float = Field(default=0.0, ge=0.0, le=30.0)
    atmospheric_ref_elev_deg: float = Field(default=90.0, ge=5.0, le=90.0)
    required_ebn0_db: float = Field(ge=-5.0, le=30.0)
    data_rate_bps: float = Field(ge=1.0, le=1e10)
    sources: list[LinkSource] = Field(min_length=1, max_length=10)
    derivations: dict[str, str] = Field(default_factory=dict)
    checks: list[LinkCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sorted_pattern(self) -> Self:
        if self.eirp_pattern is not None:
            angles = [a for a, _ in self.eirp_pattern]
            if len(angles) < 2 or angles != sorted(set(angles)):
                raise ValueError("eirp_pattern needs at least two points in ascending angle")
        return self

    def to_model(self) -> LinkProfileModel:
        return LinkProfileModel(
            norad_id=self.norad_id,
            label=self.label,
            frequency_hz=self.frequency_hz,
            eirp_dbw=self.eirp_dbw,
            station_gt_db_k=self.station_gt_db_k,
            system_loss_db=self.system_loss_db,
            required_ebn0_db=self.required_ebn0_db,
            data_rate_bps=self.data_rate_bps,
            eirp_pattern=tuple(self.eirp_pattern or ()),
            coverage_half_angle_deg=self.coverage_half_angle_deg,
            atmospheric_loss_db=self.atmospheric_loss_db,
            atmospheric_ref_elev_deg=self.atmospheric_ref_elev_deg,
        )


class LinkProfilesFile(BaseModel):
    version: int = Field(ge=1)
    verified_on: date
    profiles: list[LinkProfileSpec] = Field(max_length=200)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [p.norad_id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("one link profile per satellite")
        return self


LINK_PROFILES_FILE = SEED_DIR / "link_profiles.json"


@lru_cache
def link_profile_specs(path: Path = LINK_PROFILES_FILE) -> LinkProfilesFile:
    return LinkProfilesFile.model_validate_json(path.read_bytes())


_LINK_COLUMNS = (
    "label",
    "frequency_hz",
    "eirp_dbw",
    "eirp_pattern",
    "coverage_half_angle_deg",
    "station_gt_db_k",
    "system_loss_db",
    "atmospheric_loss_db",
    "atmospheric_ref_elev_deg",
    "required_ebn0_db",
    "data_rate_bps",
    "sources",
)


def sync_link_profiles(db: Session, path: Path = LINK_PROFILES_FILE) -> int:
    """Make ``link_profile`` match the seed for satellites already in the catalog.

    The seed is authoritative (sourced values get corrected), so rows it no longer lists are
    deleted. A profile whose satellite isn't catalogued yet waits for a later sync: this runs on
    every fleet rebuild. An unreadable seed is logged and leaves the table as it is.
    """
    try:
        specs = link_profile_specs(path).profiles
    except (OSError, ValueError, ValidationError):
        log.exception("link profiles: could not read %s; keeping the current table", path.name)
        return 0
    wanted = {s.norad_id: s for s in specs}
    present = set(
        db.scalars(select(Satellite.norad_id).where(Satellite.norad_id.in_(list(wanted))))
    )
    rows = [
        {
            "norad_id": s.norad_id,
            "label": s.label,
            "frequency_hz": s.frequency_hz,
            "eirp_dbw": s.eirp_dbw,
            "eirp_pattern": [list(p) for p in s.eirp_pattern] if s.eirp_pattern else None,
            "coverage_half_angle_deg": s.coverage_half_angle_deg,
            "station_gt_db_k": s.station_gt_db_k,
            "system_loss_db": s.system_loss_db,
            "atmospheric_loss_db": s.atmospheric_loss_db,
            "atmospheric_ref_elev_deg": s.atmospheric_ref_elev_deg,
            "required_ebn0_db": s.required_ebn0_db,
            "data_rate_bps": s.data_rate_bps,
            "sources": [src.model_dump() for src in s.sources],
        }
        for s in specs
        if s.norad_id in present
    ]
    db.execute(delete(LinkProfile).where(LinkProfile.norad_id.not_in(list(wanted))))
    if rows:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        stmt = insert(LinkProfile).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LinkProfile.norad_id],
            set_={col: stmt.excluded[col] for col in _LINK_COLUMNS},
        )
        db.execute(stmt)
    db.commit()
    return len(rows)


def load_link_profiles(db: Session) -> list[LinkProfileModel]:
    """The synced profiles, as the sim's immutable models (rows that don't validate are skipped)."""
    profiles = []
    for row in db.scalars(select(LinkProfile).order_by(LinkProfile.norad_id)):
        try:
            pattern = tuple((float(a), float(o)) for a, o in row.eirp_pattern or ())
            profiles.append(
                LinkProfileModel(
                    norad_id=row.norad_id,
                    label=row.label,
                    frequency_hz=row.frequency_hz,
                    eirp_dbw=row.eirp_dbw,
                    station_gt_db_k=row.station_gt_db_k,
                    system_loss_db=row.system_loss_db,
                    required_ebn0_db=row.required_ebn0_db,
                    data_rate_bps=row.data_rate_bps,
                    eirp_pattern=pattern,
                    coverage_half_angle_deg=row.coverage_half_angle_deg,
                    atmospheric_loss_db=row.atmospheric_loss_db,
                    atmospheric_ref_elev_deg=row.atmospheric_ref_elev_deg,
                )
            )
        except (TypeError, ValueError):
            log.warning("link profile for %d is malformed; skipped", row.norad_id)
    return profiles

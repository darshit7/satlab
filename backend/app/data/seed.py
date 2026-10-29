"""Idempotent seeding of reference data from versioned JSON under ``app/data/seed/``."""

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import GroundStation, LinkProfile, PacketTemplate, Satellite
from app.packets.table import Downlink
from app.packets.templates import CommandTemplate, Name, SourceRef, TelemetryTemplate
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


# ---------- packet templates ----------

Template = Annotated[TelemetryTemplate | CommandTemplate, Field(discriminator="kind")]
_template_adapter: TypeAdapter[TelemetryTemplate | CommandTemplate] = TypeAdapter(Template)


class TemplateSet(BaseModel):
    telemetry: list[TelemetryTemplate] = Field(min_length=1, max_length=16)
    commands: list[CommandTemplate] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        names = [t.name for t in self.telemetry]
        if len(names) != len(set(names)) or len(self.commands) != len(
            {c.name for c in self.commands}
        ):
            raise ValueError("template names must be unique within a set")
        for command in self.commands:
            if command.response_template is not None and command.response_template not in names:
                raise ValueError(
                    f"{command.name}: no telemetry template {command.response_template}"
                )
        return self


class SatelliteTemplatesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    norad_id: int = Field(ge=1, le=999_999_999)
    satellite_name: str = Field(max_length=100)
    template_set: Name
    # The downlink its contact's Doppler shift is computed for, when it has no link profile (a
    # profile's frequency is that satellite's). Cited like everything else in the seed.
    downlink_frequency_hz: float | None = Field(default=None, gt=1e6, lt=1e11)
    downlink_sources: list[SourceRef] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _cited(self) -> Self:
        if self.downlink_frequency_hz is not None and not self.downlink_sources:
            raise ValueError(f"{self.satellite_name}: a downlink frequency needs a source")
        return self


class PacketTemplatesFile(BaseModel):
    version: int = Field(ge=1)
    verified_on: date
    template_sets: dict[Name, TemplateSet] = Field(max_length=32)
    satellites: list[SatelliteTemplatesSpec] = Field(max_length=200)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [s.norad_id for s in self.satellites]
        if len(ids) != len(set(ids)):
            raise ValueError("one template set per satellite")
        for s in self.satellites:
            if s.template_set not in self.template_sets:
                raise ValueError(f"unknown template set {s.template_set}")
        return self

    def templates(self) -> dict[int, list[TelemetryTemplate | CommandTemplate]]:
        """Per satellite, its set's templates."""
        result: dict[int, list[TelemetryTemplate | CommandTemplate]] = {}
        for s in self.satellites:
            chosen = self.template_sets[s.template_set]
            result[s.norad_id] = [*chosen.telemetry, *chosen.commands]
        return result

    def downlinks(self) -> dict[int, Downlink]:
        """The satellites the seed gives a downlink frequency."""
        return {
            s.norad_id: Downlink(s.downlink_frequency_hz, tuple(s.downlink_sources))
            for s in self.satellites
            if s.downlink_frequency_hz is not None
        }


PACKET_TEMPLATES_FILE = SEED_DIR / "packet_templates.json"
_TEMPLATE_COLUMNS = ("framing", "spec", "version", "active")


@lru_cache
def packet_template_specs(path: Path = PACKET_TEMPLATES_FILE) -> PacketTemplatesFile:
    return PacketTemplatesFile.model_validate_json(path.read_bytes())


def sync_packet_templates(db: Session, path: Path = PACKET_TEMPLATES_FILE) -> int:
    """Make ``packet_template`` match the seed for satellites already in the catalog.

    Upserts by (satellite, kind, name). Rows the seed no longer lists are deactivated, not deleted,
    so commands that used them keep their foreign key. An unreadable seed is logged and leaves the
    table as it is.
    """
    try:
        specs = packet_template_specs(path)
    except (OSError, ValueError, ValidationError):
        log.exception("packet templates: could not read %s; keeping the current table", path.name)
        return 0
    per_satellite = specs.templates()
    present = set(
        db.scalars(select(Satellite.norad_id).where(Satellite.norad_id.in_(list(per_satellite))))
    )
    wanted = {
        (norad_id, t.kind, t.name): t
        for norad_id, templates in per_satellite.items()
        if norad_id in present
        for t in templates
    }
    # One upsert, not insert-if-absent: two syncs can overlap (the old and new revision during a
    # deploy) and both see the rows missing; ON CONFLICT lets both succeed.
    if wanted:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        stmt = insert(PacketTemplate).values(
            [
                {
                    "norad_id": norad_id,
                    "kind": kind,
                    "name": name,
                    "framing": template.framing.type,
                    "spec": template.model_dump(mode="json"),
                    "version": specs.version,
                    "active": True,
                }
                for (norad_id, kind, name), template in wanted.items()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PacketTemplate.norad_id, PacketTemplate.kind, PacketTemplate.name],
            set_={col: stmt.excluded[col] for col in _TEMPLATE_COLUMNS},
        )
        db.execute(stmt)
    active = db.execute(
        select(
            PacketTemplate.id, PacketTemplate.norad_id, PacketTemplate.kind, PacketTemplate.name
        ).where(PacketTemplate.active)
    )
    dropped = [row.id for row in active if (row.norad_id, row.kind, row.name) not in wanted]
    if dropped:
        db.execute(
            update(PacketTemplate).where(PacketTemplate.id.in_(dropped)).values(active=False)
        )
    db.commit()
    return len(wanted)


def load_downlinks(path: Path = PACKET_TEMPLATES_FILE) -> dict[int, Downlink]:
    """Seeded downlink frequencies by NORAD ID; none (logged) when the seed doesn't read."""
    try:
        return packet_template_specs(path).downlinks()
    except (OSError, ValueError, ValidationError):
        log.exception("packet templates: could not read %s; no downlink frequencies", path.name)
        return {}


def load_packet_templates(
    db: Session,
) -> list[tuple[int, int, TelemetryTemplate | CommandTemplate]]:
    """The active templates as (id, NORAD ID, template); rows that don't validate are skipped."""
    templates = []
    for row in db.scalars(
        select(PacketTemplate).where(PacketTemplate.active).order_by(PacketTemplate.id)
    ):
        try:
            templates.append((row.id, row.norad_id, _template_adapter.validate_python(row.spec)))
        except ValidationError:
            log.warning("packet template %d (%s) is malformed; skipped", row.id, row.name)
    return templates

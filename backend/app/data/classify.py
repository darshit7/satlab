"""Satellite type classification from versioned rules in ``app/data/seed/satellite_types.json``."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data.seed import SEED_DIR
from app.db.models import Satellite
from app.models.ws import SatelliteType


class _RuleSpec(BaseModel):
    type: SatelliteType
    is_sar_capable: bool = False
    patterns: list[str] = Field(default_factory=list)
    norad_ids: list[int] = Field(default_factory=list)


class _RulesFile(BaseModel):
    rules: list[_RuleSpec]


@dataclass(frozen=True)
class Classification:
    type: SatelliteType
    is_sar_capable: bool


@dataclass(frozen=True)
class _Rule:
    result: Classification
    pattern: re.Pattern[str] | None
    norad_ids: frozenset[int]

    def matches(self, name: str, norad_id: int) -> bool:
        return norad_id in self.norad_ids or (
            self.pattern is not None and self.pattern.search(name) is not None
        )


OTHER = Classification("other", False)


@lru_cache
def _rules() -> tuple[_Rule, ...]:
    spec = _RulesFile.model_validate(
        json.loads((SEED_DIR / "satellite_types.json").read_text(encoding="utf-8"))
    )
    rules = []
    for rule in spec.rules:
        pattern = (
            re.compile("|".join(f"(?:{p})" for p in rule.patterns), re.IGNORECASE)
            if rule.patterns
            else None
        )
        rules.append(
            _Rule(
                Classification(rule.type, rule.is_sar_capable), pattern, frozenset(rule.norad_ids)
            )
        )
    return tuple(rules)


def classify(name: str, norad_id: int) -> Classification:
    name = name.strip()
    for rule in _rules():
        if rule.matches(name, norad_id):
            return rule.result
    return OTHER


def reclassify_all(db: Session) -> int:
    """Re-apply the rules to every satellite; returns how many rows changed."""
    changes = []
    for norad_id, name, current_type, current_sar in db.execute(
        select(Satellite.norad_id, Satellite.name, Satellite.type, Satellite.is_sar_capable)
    ):
        c = classify(name, norad_id)
        if (c.type, c.is_sar_capable) != (current_type, current_sar):
            changes.append(
                {"norad_id": norad_id, "type": c.type, "is_sar_capable": c.is_sar_capable}
            )
    if changes:
        db.execute(update(Satellite), changes)
        db.commit()
    return len(changes)

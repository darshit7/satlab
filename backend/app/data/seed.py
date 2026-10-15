"""Idempotent seeding of reference data from versioned JSON under ``app/data/seed/``."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GroundStation

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

"""Link profiles are synced from the seed for catalogued satellites only, idempotently."""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.data.celestrak import parse_tle_text
from app.data.seed import LINK_PROFILES_FILE, sync_link_profiles
from app.db.base import get_sessionmaker
from app.db.models import LinkProfile
from app.services.catalog import upsert_tle_records
from tests.conftest import FLEET_2026_TLE

NOAA_20 = 43013


def _profiles() -> dict[int, LinkProfile]:
    with get_sessionmaker()() as db:
        return {p.norad_id: p for p in db.scalars(select(LinkProfile))}


def _sync(path: Path = LINK_PROFILES_FILE) -> int:
    with get_sessionmaker()() as db:
        return sync_link_profiles(db, path)


def _seed_copy(tmp_path: Path, edit) -> Path:
    """An edited copy of the seed, under its own name (parsed seeds are cached by path)."""
    data = json.loads(LINK_PROFILES_FILE.read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / f"link_profiles_{edit.__name__}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_sync_waits_for_the_satellite(client: TestClient, tmp_path: Path) -> None:
    assert _sync() == 0  # nothing catalogued yet: the foreign key can't be satisfied
    assert _profiles() == {}

    with get_sessionmaker()() as db:  # NOAA 20 is the only profiled satellite in this fleet
        upsert_tle_records(db, parse_tle_text(FLEET_2026_TLE), datetime.now(UTC))
    assert _sync() == 1
    first = _profiles()
    assert set(first) == {NOAA_20}
    assert first[NOAA_20].sources[0]["url"].startswith("https://")
    assert first[NOAA_20].eirp_pattern is not None

    assert _sync() == 1  # idempotent: the same row, updated in place
    assert _profiles()[NOAA_20].id == first[NOAA_20].id


def test_seed_is_authoritative(client: TestClient, tmp_path: Path) -> None:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, parse_tle_text(FLEET_2026_TLE), datetime.now(UTC))
    _sync()

    def relabel(data: dict) -> None:
        for p in data["profiles"]:
            p["label"] = "corrected"

    _sync(_seed_copy(tmp_path, relabel))
    assert _profiles()[NOAA_20].label == "corrected"

    def drop_noaa_20(data: dict) -> None:
        data["profiles"] = [p for p in data["profiles"] if p["norad_id"] != NOAA_20]

    _sync(_seed_copy(tmp_path, drop_noaa_20))
    assert _profiles() == {}


def test_bad_seed_leaves_the_table_alone(client: TestClient, tmp_path: Path) -> None:
    with get_sessionmaker()() as db:
        upsert_tle_records(db, parse_tle_text(FLEET_2026_TLE), datetime.now(UTC))
    _sync()

    def break_it(data: dict) -> None:
        data["profiles"][0]["frequency_hz"] = -1  # out of bounds

    assert _sync(_seed_copy(tmp_path, break_it)) == 0
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(LinkProfile)) == 1

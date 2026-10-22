"""Packet templates are synced from the seed for catalogued satellites; dropped ones are kept,
inactive, so commands that used them keep their foreign key; a bad seed changes nothing."""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.data.celestrak import parse_tle_text
from app.data.seed import PACKET_TEMPLATES_FILE, load_packet_templates, sync_packet_templates
from app.db.base import get_sessionmaker
from app.db.models import PacketTemplate
from app.services.catalog import upsert_tle_records
from tests.conftest import FLEET_2026_TLE

NOAA_20, ISS = 43013, 25544


def _rows() -> dict[tuple[int, str, str], PacketTemplate]:
    with get_sessionmaker()() as db:
        return {(r.norad_id, r.kind, r.name): r for r in db.scalars(select(PacketTemplate))}


def _sync(path: Path = PACKET_TEMPLATES_FILE) -> int:
    with get_sessionmaker()() as db:
        return sync_packet_templates(db, path)


def _seed_copy(tmp_path: Path, edit) -> Path:
    data = json.loads(PACKET_TEMPLATES_FILE.read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / f"packet_templates_{edit.__name__}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _catalogue() -> None:
    with get_sessionmaker()() as db:  # NOAA 20 and the ISS have templates in this fleet
        upsert_tle_records(db, parse_tle_text(FLEET_2026_TLE), datetime.now(UTC))


def test_sync_waits_for_the_satellite(client: TestClient) -> None:
    assert _sync() == 0
    _catalogue()
    assert _sync() == 7  # NOAA 20: hk, ack + 3 commands; ISS: aprs + APRS_MESSAGE
    rows = _rows()
    assert {k[0] for k in rows} == {NOAA_20, ISS}
    ping = rows[(NOAA_20, "command", "PING")]
    assert ping.framing == "ccsds" and ping.spec["opcode"] == 1 and ping.active

    assert _sync() == 7  # idempotent: same rows
    assert _rows()[(NOAA_20, "command", "PING")].id == ping.id

    with get_sessionmaker()() as db:
        loaded = load_packet_templates(db)
    assert {(n, t.name) for _, n, t in loaded} == {(n, name) for n, _, name in rows}


def test_dropped_templates_are_deactivated(client: TestClient, tmp_path: Path) -> None:
    _catalogue()
    _sync()

    def drop_ping(data: dict) -> None:
        bus = data["template_sets"]["ccsds_bus"]
        bus["commands"] = [c for c in bus["commands"] if c["name"] != "PING"]

    assert _sync(_seed_copy(tmp_path, drop_ping)) == 6
    assert not _rows()[(NOAA_20, "command", "PING")].active
    with get_sessionmaker()() as db:
        assert "PING" not in {t.name for _, _, t in load_packet_templates(db)}


def test_bad_seed_changes_nothing(client: TestClient, tmp_path: Path) -> None:
    _catalogue()
    _sync()

    def typo(data: dict) -> None:
        data["template_sets"]["ccsds_bus"]["telemetry"][0]["intervall_s"] = 10  # unknown key

    assert _sync(_seed_copy(tmp_path, typo)) == 0
    assert all(r.active for r in _rows().values())

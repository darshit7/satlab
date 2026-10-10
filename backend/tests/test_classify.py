"""Satellite type classification rules (app/data/seed/satellite_types.json)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.data.celestrak import parse_tle_text
from app.data.classify import classify, reclassify_all
from app.db.base import get_sessionmaker
from app.db.models import Satellite
from app.services.catalog import upsert_tle_records
from app.sim.fleet import Fleet, FleetRow
from tests.conftest import FLEET_2026_TLE


@pytest.mark.parametrize(
    ("name", "expected", "sar"),
    [
        ("ISS (ZARYA)", "station", False),
        ("CSS (TIANHE)", "station", False),
        ("ISS OBJECT YM", "other", False),  # deployed from the ISS, not the station
        ("NOAA 20 (JPSS-1)", "weather", False),
        ("METOP-B", "weather", False),
        ("EWS-G2 (GOES 15)", "weather", False),
        ("GEO-KOMPSAT-2A", "weather", False),
        ("FENGYUN 3D", "weather", False),
        ("FENGYUN 1C DEB", "other", False),  # debris of a weather satellite
        ("CZ-4C R/B", "other", False),
        ("STARLINK-1008", "comms", False),
        ("ONEWEB-0060", "comms", False),
        ("GSAT-14", "comms", False),  # ISRO communications
        ("GSAT0101 (GALILEO-PFM)", "navigation", False),  # Galileo
        ("COSMOS 2433 [GLONASS-M]", "navigation", False),
        ("NAVSTAR 81 (USA 319)", "navigation", False),
        ("SENTINEL-1C", "sar", True),
        ("SENTINEL-2A", "other", False),  # optical
        ("SENTINEL-6A", "other", False),
        ("UMBRA-07", "sar", True),
        ("ICEYE-X25", "sar", True),
        ("CAPELLA-14 (ACADIA-4)", "sar", True),
        ("ARIRANG-5 (KOMPSAT-5)", "sar", True),
        ("ARIRANG-3 (KOMPSAT-3)", "other", False),  # optical
        ("SAR-LUPE 2", "sar", True),
        ("SARAL", "other", False),  # an altimeter, not SAR
        ("LASARSAT", "other", False),
        ("COSPAS-SARSAT TEST", "other", False),  # search and rescue, not SAR
        ("SOMETHING UNKNOWN", "other", False),
    ],
)
def test_classify(name: str, expected: str, sar: bool) -> None:
    c = classify(name, 1)
    assert (c.type, c.is_sar_capable) == (expected, sar)


def test_upsert_classifies_and_reclassify_repairs(client) -> None:
    records = parse_tle_text(FLEET_2026_TLE)
    with get_sessionmaker()() as db:
        upsert_tle_records(db, records, datetime.now(UTC))
        assert db.get(Satellite, 62261).type == "sar"
        assert db.get(Satellite, 62261).is_sar_capable

        db.execute(update(Satellite).values(type=None, is_sar_capable=False))
        db.commit()
        assert reclassify_all(db) == len(records)
        assert db.get(Satellite, 38771).type == "weather"
        assert reclassify_all(db) == 0  # idempotent


def test_fleet_build_skips_bad_and_duplicate_rows() -> None:
    [rec, *_] = parse_tle_text(FLEET_2026_TLE)
    good = FleetRow(rec.norad_id, rec.name, "station", False, rec.line1, rec.line2)
    bad = FleetRow(2, "BROKEN", "other", False, "1 garbage", "2 garbage")
    fleet = Fleet.build([good, good, bad])
    assert len(fleet) == 1
    assert fleet.index == {rec.norad_id: 0}
    assert fleet.type_of(0) == "station"
    assert fleet.tle_epochs[0].year == 2026
    assert Fleet.empty().array is None

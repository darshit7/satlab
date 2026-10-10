"""Geometry checked against the published skyfield ISS example (Bluffton, OH, 2014-01-23)."""

from datetime import timedelta

import pytest

from app.sim.geometry import Observer, look_angles, sub_point
from app.sim.propagate import elements_from_tle, ground_track, to_time
from tests.conftest import BLUFFTON, ISS_LINE1, ISS_LINE2, ISS_NAME, REFERENCE_TIME


@pytest.fixture
def iss():
    return elements_from_tle(ISS_NAME, ISS_LINE1, ISS_LINE2)


@pytest.fixture
def bluffton():
    return Observer(*BLUFFTON).to_skyfield()


def test_elements(iss) -> None:
    assert iss.norad_id == 25544
    assert iss.epoch.year == 2014
    assert iss.period_s / 60 == pytest.approx(92.9, abs=0.1)


def test_look_angles_match_reference(iss, bluffton) -> None:
    # Reference: altitude 16°16'32.6", azimuth 350°15'20.4", distance 1168.66 km
    look = look_angles(iss.satellite, bluffton, to_time(REFERENCE_TIME))
    assert look.elevation_deg == pytest.approx(16 + 16 / 60 + 32.6 / 3600, abs=1e-3)
    assert look.azimuth_deg == pytest.approx(350 + 15 / 60 + 20.4 / 3600, abs=1e-3)
    assert look.range_m == pytest.approx(1_168_660, abs=50)


def test_sub_point_matches_reference(iss) -> None:
    # Reference: latitude 50°14'37.4", longitude -86°23'23.3"
    sp = sub_point(iss.satellite, to_time(REFERENCE_TIME))
    assert sp.lat_deg == pytest.approx(50 + 14 / 60 + 37.4 / 3600, abs=1e-3)
    assert sp.lon_deg == pytest.approx(-(86 + 23 / 60 + 23.3 / 3600), abs=1e-3)
    assert 400_000 < sp.alt_m < 440_000


def test_range_rate_matches_finite_difference(iss, bluffton) -> None:
    dt_s = 0.5
    before = look_angles(iss.satellite, bluffton, to_time(REFERENCE_TIME - timedelta(seconds=dt_s)))
    after = look_angles(iss.satellite, bluffton, to_time(REFERENCE_TIME + timedelta(seconds=dt_s)))
    now = look_angles(iss.satellite, bluffton, to_time(REFERENCE_TIME))
    numeric = (after.range_m - before.range_m) / (2 * dt_s)
    assert now.range_rate_m_s == pytest.approx(numeric, abs=1.0)


def test_ground_track(iss) -> None:
    track = ground_track(iss, REFERENCE_TIME, n_points=120)
    assert len(track.points) == 120
    assert track.end - track.start == timedelta(seconds=iss.period_s * 1.5)
    for lon, lat in track.points:
        assert -180 <= lon <= 180
        # geodetic latitude peaks slightly above the 51.65° inclination (geocentric bound)
        assert abs(lat) <= 51.65 + 0.2

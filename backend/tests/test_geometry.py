"""Geometry checked against the published skyfield ISS example (Bluffton, OH, 2014-01-23)."""

import math
from datetime import timedelta

import numpy as np
import pytest
from sgp4.api import SatrecArray

from app.data.celestrak import parse_tle_text
from app.sim.geometry import (
    WGS84_F,
    Observer,
    ground_track,
    look_angles,
    mask_footprint_deg,
    sub_point,
    teme_to_itrf,
    topocentric,
)
from app.sim.propagate import elements_from_tle, epochs, sgp4_teme, to_time
from tests.conftest import (
    BLUFFTON,
    FLEET_2026_TIME,
    FLEET_2026_TLE,
    ISS_LINE1,
    ISS_LINE2,
    ISS_NAME,
    REFERENCE_TIME,
)


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


def test_ground_track_matches_skyfield(iss, bluffton) -> None:
    """Every sample of the vectorized track agrees with skyfield's one-at-a-time reference."""
    observer = Observer(*BLUFFTON)
    n = 60
    track = ground_track(iss, REFERENCE_TIME, n, observer, mask_deg=5.0)
    assert len(track.points) == len(track.alt_m) == len(track.above_mask) == n
    step = (track.end - track.start) / (n - 1)
    for i, ((lon, lat), alt, above) in enumerate(
        zip(track.points, track.alt_m, track.above_mask, strict=True)
    ):
        t = to_time(track.start + step * i)
        ref = sub_point(iss.satellite, t)
        assert lat == pytest.approx(ref.lat_deg, abs=1e-4)  # points are rounded to 1e-4°
        assert lon == pytest.approx(ref.lon_deg, abs=1e-4)
        assert alt == pytest.approx(ref.alt_m, abs=1.0)
        assert above == (look_angles(iss.satellite, bluffton, t).elevation_deg >= 5.0)
    assert any(track.above_mask) and not all(track.above_mask)


def test_ground_track(iss) -> None:
    track = ground_track(iss, REFERENCE_TIME, 120, Observer(*BLUFFTON), mask_deg=5.0)
    assert len(track.points) == 120
    assert track.end - track.start == timedelta(seconds=iss.period_s * 1.5)
    for lon, lat in track.points:
        assert -180 <= lon <= 180
        # geodetic latitude peaks slightly above the 51.65° inclination (geocentric bound)
        assert abs(lat) <= 51.65 + 0.2


def _ground_point(r_itrf_m: np.ndarray, angle_deg: float, bearing_deg: float) -> Observer:
    """The point ``angle_deg`` of Earth central angle from the satellite's geocentric sub-point,
    towards ``bearing_deg``, on the WGS84 surface."""
    u = r_itrf_m / np.linalg.norm(r_itrf_m)
    east = np.cross([0.0, 0.0, 1.0], u)
    east /= np.linalg.norm(east)
    north = np.cross(u, east)
    a, b = math.radians(angle_deg), math.radians(bearing_deg)
    p = math.cos(a) * u + math.sin(a) * (math.cos(b) * north + math.sin(b) * east)
    geocentric = math.atan2(p[2], math.hypot(p[0], p[1]))
    geodetic_lat = math.atan(math.tan(geocentric) / (1 - WGS84_F) ** 2)  # surface point
    return Observer(math.degrees(geodetic_lat), math.degrees(math.atan2(p[1], p[0])))


@pytest.mark.parametrize("mask_deg", [0.0, 5.0, 20.0])
@pytest.mark.parametrize("which", ["iss", "goes"])
def test_mask_footprint_rim(iss, which: str, mask_deg: float) -> None:
    """Ground points just inside the footprint see the satellite above the mask; just outside,
    below it — whichever way from the sub-point."""
    if which == "iss":
        elements = iss
        when = REFERENCE_TIME
    else:
        [goes] = [r for r in parse_tle_text(FLEET_2026_TLE) if r.name == "GOES 16"]
        elements = elements_from_tle(goes.name, goes.line1, goes.line2)
        when = FLEET_2026_TIME
    at = epochs(when)
    r_teme, v_teme, _ = sgp4_teme(SatrecArray([elements.satellite.model]), at)
    r, v = teme_to_itrf(r_teme, v_teme, at)
    r_sat, v_sat = r[0, 0], v[0, 0]
    radius = float(mask_footprint_deg(r_sat, mask_deg))
    if which == "iss" and mask_deg == 0.0:
        assert radius == pytest.approx(20.0, abs=1.0)  # ~420 km up: about 20°
    for bearing in (0.0, 90.0, 180.0, 270.0):
        for offset, inside in ((-0.5, True), (0.5, False)):
            observer = _ground_point(r_sat, radius + offset, bearing)
            _, el, _, _ = topocentric(r_sat, v_sat, observer)
            assert (float(el) > mask_deg) == inside, (bearing, offset, float(el))


def test_ground_track_link_geometry(iss) -> None:
    observer = Observer(*BLUFFTON)
    track = ground_track(iss, REFERENCE_TIME, 120, observer, mask_deg=5.0)
    assert track.complete
    n = len(track.points)
    for values in (
        track.offsets_s,
        track.elevation_deg,
        track.range_m,
        track.range_rate_m_s,
        track.off_nadir_deg,
    ):
        assert len(values) == n
    assert track.offsets_s[0] == 0.0
    assert track.offsets_s[-1] == pytest.approx((track.end - track.start).total_seconds())
    assert [bool(e >= 5.0) for e in track.elevation_deg] == track.above_mask

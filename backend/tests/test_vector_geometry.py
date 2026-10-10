"""The vectorized geometry the sim loop runs, checked against skyfield's one-at-a-time reference."""

from datetime import timedelta

import numpy as np
import pytest
from sgp4.api import SatrecArray

from app.data.celestrak import parse_tle_text
from app.sim.geometry import (
    Observer,
    elevations,
    geodetic,
    look_angles,
    observe,
    observe_pairs,
    sub_point,
)
from app.sim.propagate import elements_from_tle, epochs, to_time
from tests.conftest import (
    BLUFFTON,
    FLEET_2026_TIME,
    FLEET_2026_TLE,
    ISS_LINE1,
    ISS_LINE2,
    ISS_NAME,
    REFERENCE_TIME,
    WALLOPS,
)

OFFSETS_S = [0.0, 600.0, 3600.0, 86_400.0]


def _assert_matches_skyfield(elements, observer: Observer, start, offsets_s) -> None:
    ep = epochs(start, offsets_s)
    obs = observe(SatrecArray([elements.satellite.model]), ep, observer)
    lat, lon, alt = geodetic(obs.r_itrf_m[0])
    for i in range(len(offsets_s)):
        t = to_time(ep.at(i))
        ref = look_angles(elements.satellite, observer.to_skyfield(), t)
        sp = sub_point(elements.satellite, t)
        assert obs.ok[0, i]
        assert obs.elevation_deg[0, i] == pytest.approx(ref.elevation_deg, abs=1e-4)
        az_err = (obs.azimuth_deg[0, i] - ref.azimuth_deg + 180) % 360 - 180
        assert az_err == pytest.approx(0, abs=1e-4)
        assert obs.range_m[0, i] == pytest.approx(ref.range_m, abs=1.0)
        assert obs.range_rate_m_s[0, i] == pytest.approx(ref.range_rate_m_s, abs=1e-3)
        assert lat[i] == pytest.approx(sp.lat_deg, abs=1e-5)
        assert lon[i] == pytest.approx(sp.lon_deg, abs=1e-5)
        assert alt[i] == pytest.approx(sp.alt_m, abs=0.01)


def test_iss_matches_skyfield_reference() -> None:
    iss = elements_from_tle(ISS_NAME, ISS_LINE1, ISS_LINE2)
    _assert_matches_skyfield(iss, Observer(*BLUFFTON), REFERENCE_TIME, OFFSETS_S)


@pytest.mark.parametrize("record", parse_tle_text(FLEET_2026_TLE), ids=lambda r: r.name)
def test_fleet_matches_skyfield(record) -> None:
    elements = elements_from_tle(record.name, record.line1, record.line2)
    _assert_matches_skyfield(elements, Observer(*WALLOPS), FLEET_2026_TIME, OFFSETS_S)


def test_published_reference_values() -> None:
    # skyfield docs: altitude 16°16'32.6", azimuth 350°15'20.4", distance 1168.66 km
    iss = elements_from_tle(ISS_NAME, ISS_LINE1, ISS_LINE2)
    obs = observe(SatrecArray([iss.satellite.model]), epochs(REFERENCE_TIME), Observer(*BLUFFTON))
    assert obs.elevation_deg[0, 0] == pytest.approx(16 + 16 / 60 + 32.6 / 3600, abs=1e-3)
    assert obs.azimuth_deg[0, 0] == pytest.approx(350 + 15 / 60 + 20.4 / 3600, abs=1e-3)
    assert obs.range_m[0, 0] == pytest.approx(1_168_660, abs=50)


def test_many_satellites_many_times_and_cheap_paths_agree() -> None:
    sats = [
        elements_from_tle(r.name, r.line1, r.line2).satellite.model
        for r in parse_tle_text(FLEET_2026_TLE)
    ]
    observer = Observer(*WALLOPS)
    ep = epochs(FLEET_2026_TIME, np.arange(0, 7200, 300.0))
    full = observe(SatrecArray(sats), ep, observer)
    assert full.elevation_deg.shape == (len(sats), len(ep))
    np.testing.assert_allclose(elevations(SatrecArray(sats), ep, observer), full.elevation_deg)

    # pairwise: satellite i at epoch i, including repeated satellites (grouped internally)
    sat_idx = np.array([0, 0, 0, 3, 5, 5, 8])
    t_idx = np.array([0, 4, 9, 2, 1, 7, 20])
    pairs = observe_pairs(
        [sats[i] for i in sat_idx], epochs(FLEET_2026_TIME, ep.offsets_s[t_idx]), observer
    )
    np.testing.assert_allclose(pairs.elevation_deg, full.elevation_deg[sat_idx, t_idx])
    np.testing.assert_allclose(pairs.azimuth_deg, full.azimuth_deg[sat_idx, t_idx])


def test_geostationary_barely_moves() -> None:
    [goes] = [r for r in parse_tle_text(FLEET_2026_TLE) if r.name == "GOES 16"]
    sat = elements_from_tle(goes.name, goes.line1, goes.line2).satellite.model
    ep = epochs(FLEET_2026_TIME, [0.0, timedelta(hours=6).total_seconds()])
    obs = observe(SatrecArray([sat]), ep, Observer(*WALLOPS))
    assert obs.elevation_deg[0, 0] > 30
    assert abs(obs.elevation_deg[0, 1] - obs.elevation_deg[0, 0]) < 1.0

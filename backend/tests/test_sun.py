"""Sun position and subsolar point against published values and JPL kernels; the Earth's shadow
geometry."""

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import skyfield
from skyfield.api import load, load_file, wgs84

from app.sim.geometry import Observer, teme_to_itrf, topocentric
from app.sim.loop import sun_state
from app.sim.propagate import epochs
from app.sim.sun import AU_KM, EARTH_RADIUS_KM, subsolar_point, sun_position_km, sunlit_fraction


def _ra_dec_r(when: datetime) -> tuple[float, float, float]:
    x, y, z = sun_position_km(epochs(when))[0]
    r = math.sqrt(x * x + y * y + z * z)
    return math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(z / r)), r / AU_KM


def test_meeus_example_25a() -> None:
    # Meeus, Astronomical Algorithms, Ex. 25.a: 1992 Oct 13.0 TD
    ra, dec, r = _ra_dec_r(datetime(1992, 10, 13, tzinfo=UTC))
    assert ra == pytest.approx(198.38083, abs=0.01)
    assert dec == pytest.approx(-7.78507, abs=0.01)
    assert r == pytest.approx(0.99766, abs=1e-4)


def test_equinox_and_solstice_2026() -> None:
    _, dec, _ = _ra_dec_r(datetime(2026, 3, 20, 14, 46, tzinfo=UTC))
    assert dec == pytest.approx(0.0, abs=0.02)
    _, dec, _ = _ra_dec_r(datetime(2026, 6, 21, 8, 24, tzinfo=UTC))
    assert dec == pytest.approx(23.44, abs=0.02)


def test_shadow_geometry() -> None:
    sun = np.array([AU_KM, 0.0, 0.0])
    assert sunlit_fraction(np.array([7000.0, 0.0, 0.0]), sun) == 1.0  # day side
    assert sunlit_fraction(np.array([-7000.0, 0.0, 0.0]), sun) == 0.0  # behind the Earth
    assert sunlit_fraction(np.array([0.0, 7000.0, 0.0]), sun) == 1.0  # beside it

    # Crossing the shadow's edge behind the Earth: umbra, then a thin penumbra, then sunlight.
    y = np.arange(6300.0, 6460.0, 0.5)
    sat = np.stack((np.full_like(y, -7000.0), y, np.zeros_like(y)), axis=-1)
    f = sunlit_fraction(sat, sun)
    assert f[0] == 0.0 and f[-1] == 1.0
    assert np.all(np.diff(f) >= -1e-12)
    partial = (f > 0) & (f < 1)
    assert partial.any()
    # the penumbra straddles the Earth's radius; 7000 km behind the terminator the Sun's
    # 0.267° radius opens it to 2 * 7000 * tan(0.267°) = 65 km
    assert abs(float(np.median(y[partial])) - EARTH_RADIUS_KM) < 40
    assert float(np.ptp(y[partial])) == pytest.approx(65.0, abs=3.0)


# ---------- subsolar point ----------

_KERNELS = Path(skyfield.__file__).parent / "tests" / "data"


@pytest.mark.parametrize(
    ("kernel", "when"),
    [
        ("de430-2015-03-02.bsp", datetime(2015, 2, 27, 6, 0, tzinfo=UTC)),
        ("de430-2015-03-02.bsp", datetime(2015, 3, 4, 18, 30, tzinfo=UTC)),
        ("de441-1969.bsp", datetime(1969, 7, 26, 0, 0, tzinfo=UTC)),
        ("de441-1969.bsp", datetime(1969, 8, 1, 13, 45, tzinfo=UTC)),
    ],
)
def test_subsolar_point_matches_skyfield(kernel: str, when: datetime) -> None:
    """Against a JPL ephemeris (the small kernels skyfield ships for its own tests)."""
    path = _KERNELS / kernel
    if not path.exists():
        pytest.skip(f"{kernel} not shipped with this skyfield")
    eph = load_file(str(path))
    t = load.timescale().from_datetime(when)
    sun = eph["earth"].at(t).observe(eph["sun"]).apparent()
    ref = wgs84.subpoint_of(sun)
    lat, lon = subsolar_point(epochs(when))
    assert float(lat[0]) == pytest.approx(ref.latitude.degrees, abs=0.05)
    assert abs((float(lon[0]) - ref.longitude.degrees + 180) % 360 - 180) < 0.05


def test_subsolar_point_meeus_example_25a() -> None:
    when = datetime(1992, 10, 13, tzinfo=UTC)
    lat, lon = subsolar_point(epochs(when))
    gast_deg = load.timescale().from_datetime(when).gast * 15
    assert float(lat[0]) == pytest.approx(-7.78507, abs=0.01)
    assert abs((float(lon[0]) - (198.38083 - gast_deg) + 180) % 360 - 180) < 0.05


def test_sun_overhead_at_the_subsolar_point() -> None:
    when = datetime(2026, 10, 10, 17, 52, tzinfo=UTC)
    at = epochs(when)
    lat, lon = (float(v[0]) for v in subsolar_point(at))
    r_sun = sun_position_km(at)
    r_sun_m, _ = teme_to_itrf(r_sun, np.zeros_like(r_sun), at)
    _, el, _, _ = topocentric(r_sun_m[0], np.zeros(3), Observer(lat, lon))
    assert float(el) == pytest.approx(90.0, abs=0.01)
    for side in (-90.0, 90.0):  # on the terminator: the equator a quarter-turn away
        _, el, _, _ = topocentric(r_sun_m[0], np.zeros(3), Observer(0.0, lon + side))
        assert float(el) == pytest.approx(0.0, abs=0.01)


def test_sun_state_rates() -> None:
    state = sun_state(datetime(2026, 10, 10, 17, 52, tzinfo=UTC))
    assert -23.5 < state.subsolar_lat_deg < 23.5
    # The Earth turns under the Sun once a (solar) day, westwards.
    assert state.lon_rate_deg_s == pytest.approx(-360 / 86_400, rel=0.01)
    assert abs(state.lat_rate_deg_s) < 0.4 / 86_400 * 1.1  # at most ~0.4° a day
    # rates carry the point across the antimeridian without a jump
    across = sun_state(datetime(2026, 10, 10, 0, 0, tzinfo=UTC))
    assert abs(across.lon_rate_deg_s) < 0.01

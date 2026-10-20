"""Sun position against published values; the Earth's shadow geometry."""

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from app.sim.propagate import epochs
from app.sim.sun import AU_KM, EARTH_RADIUS_KM, sun_position_km, sunlit_fraction


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

"""The Sun's position and the Earth's shadow: eclipse-driven subsystem models and day/night.

Pure numpy, no ephemeris file (the runtime never downloads one):

* ``sun_position_km`` — the Astronomical Almanac's low-precision solar coordinates (good to 0.01°
  between 1950 and 2050), equatorial of date. TEME differs from that by the equation of the
  equinoxes (well under 0.01°), so it is used directly against SGP4 positions.
* ``subsolar_point`` — where the Sun is overhead: the same position rotated into the Earth-fixed
  frame by GMST, as SGP4 positions are. The globe and map shade night from it.
* ``sunlit_fraction`` — the conical Earth-shadow model with umbra and penumbra (Montenbruck & Gill,
  *Satellite Orbits*, §3.4.2): the fraction of the solar disc a satellite sees.
"""

import numpy as np
from numpy.typing import NDArray

from app.sim.geometry import teme_to_itrf
from app.sim.propagate import Epochs

AU_KM = 149_597_870.7
SUN_RADIUS_KM = 696_000.0
EARTH_RADIUS_KM = 6378.137
_J2000_JD = 2_451_545.0

Floats = NDArray[np.float64]


def sun_position_km(when: Epochs) -> Floats:
    """Geocentric Sun position (km), equatorial of date, ``[n_times, 3]``."""
    n = (when.jd - _J2000_JD) + when.fr  # days from J2000.0 (UTC; TT - UTC is negligible here)
    mean_lon = np.radians((280.460 + 0.9856474 * n) % 360.0)
    g = np.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_lon = mean_lon + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    obliquity = np.radians(23.439 - 0.0000004 * n)
    r_km = (1.00014 - 0.01671 * np.cos(g) - 0.00014 * np.cos(2 * g)) * AU_KM
    return np.stack(
        (
            r_km * np.cos(ecl_lon),
            r_km * np.cos(obliquity) * np.sin(ecl_lon),
            r_km * np.sin(obliquity) * np.sin(ecl_lon),
        ),
        axis=-1,
    )


def subsolar_point(when: Epochs) -> tuple[Floats, Floats]:
    """Where the Sun is at the zenith: WGS84 (latitude°, longitude°), ``[n_times]`` each.

    The geodetic latitude whose ellipsoid normal points at the Sun is the declination of the
    Earth-fixed Sun direction (solar parallax, 9", is ignored).
    """
    r_sun = sun_position_km(when)
    r_itrf, _ = teme_to_itrf(r_sun, np.zeros_like(r_sun), when)
    x, y, z = r_itrf[..., 0], r_itrf[..., 1], r_itrf[..., 2]
    return np.degrees(np.arctan2(z, np.hypot(x, y))), np.degrees(np.arctan2(y, x))


def sunlit_fraction(r_sat_km: Floats, r_sun_km: Floats) -> Floats:
    """Fraction of the solar disc visible from each satellite position (1 sunlit, 0 umbra).

    ``r_sat_km`` and ``r_sun_km`` are geocentric, in one frame, broadcastable ``[..., 3]``.
    """
    to_sun = r_sun_km - r_sat_km
    d_sat = np.linalg.norm(r_sat_km, axis=-1)
    d_sun = np.linalg.norm(to_sun, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        a = np.arcsin(np.clip(SUN_RADIUS_KM / d_sun, -1, 1))  # apparent radius of the Sun
        b = np.arcsin(np.clip(EARTH_RADIUS_KM / d_sat, -1, 1))  # apparent radius of the Earth
        cos_c = np.einsum("...i,...i->...", -r_sat_km, to_sun) / (d_sat * d_sun)
        c = np.arccos(np.clip(cos_c, -1, 1))  # apparent separation of their centres

        # partial overlap of two discs (radii a, b, centres c apart)
        x = (c * c + a * a - b * b) / (2 * c)
        y = np.sqrt(np.maximum(a * a - x * x, 0.0))
        overlap = (
            a * a * np.arccos(np.clip(x / a, -1, 1))
            + b * b * np.arccos(np.clip((c - x) / b, -1, 1))
            - c * y
        )
        partial = 1.0 - overlap / (np.pi * a * a)

    fraction = np.where(
        c >= a + b,
        1.0,  # no occultation
        np.where(
            c <= b - a,
            0.0,  # the Earth covers the whole Sun: umbra
            np.where(c <= a - b, 1.0 - (b * b) / (a * a), partial),  # annular (far out) | penumbra
        ),
    )
    return np.clip(np.where(np.isfinite(fraction), fraction, 1.0), 0.0, 1.0)

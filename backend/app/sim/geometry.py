"""Observer geometry: satellite position → sub-point and topocentric look angles.

Two implementations of the same frame chain:

* ``look_angles`` / ``sub_point`` — skyfield, one satellite at a time (TEME → GCRS → ITRS → observer
  horizon, with precession, nutation and Earth rotation). Kept as the reference the vectorized path
  is tested against.
* ``observe`` / ``geodetic`` — numpy over many satellites and times at once. SGP4's TEME frame is
  rotated into the Earth-fixed frame by GMST 1982 (Vallado's TEME → PEF; no polar motion, as in
  skyfield's default), then into the observer's local east-north-up frame. This is what the sim
  loop runs: it agrees with skyfield to ~1e-6° and well under a metre.

Units: degrees and metres at the boundary, radians inside the math.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property

import numpy as np
from numpy.typing import NDArray
from sgp4.api import Satrec, SatrecArray
from skyfield.api import EarthSatellite, wgs84
from skyfield.timelib import Time
from skyfield.toposlib import GeographicPosition

from app.sim.propagate import Elements, Epochs, linspace_epochs, sgp4_teme, sgp4_teme_pairs

WGS84_A_M = 6_378_137.0
WGS84_F = 1 / 298.257223563
_WGS84_E2 = WGS84_F * (2 - WGS84_F)

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Observer:
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0

    def to_skyfield(self) -> GeographicPosition:
        return wgs84.latlon(self.lat_deg, self.lon_deg, elevation_m=self.alt_m)

    @cached_property
    def itrf_m(self) -> Floats:
        """Earth-fixed position of the observer (WGS84), metres."""
        return np.asarray(self.to_skyfield().itrs_xyz.m, dtype=np.float64)

    @cached_property
    def enu_matrix(self) -> Floats:
        """Rotation from Earth-fixed axes to the observer's local east / north / up axes."""
        lat, lon = math.radians(self.lat_deg), math.radians(self.lon_deg)
        sin_lat, cos_lat = math.sin(lat), math.cos(lat)
        sin_lon, cos_lon = math.sin(lon), math.cos(lon)
        return np.array(
            [
                [-sin_lon, cos_lon, 0.0],
                [-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat],
                [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat],
            ]
        )


@dataclass(frozen=True)
class SubPoint:
    lat_deg: float
    lon_deg: float
    alt_m: float


@dataclass(frozen=True)
class LookAngles:
    azimuth_deg: float  # clockwise from true north, [0, 360)
    elevation_deg: float  # above the geometric horizon; negative = below
    range_m: float  # slant range observer → satellite
    range_rate_m_s: float  # d(range)/dt; negative = approaching


def sub_point(satellite: EarthSatellite, t: Time) -> SubPoint:
    position = wgs84.geographic_position_of(satellite.at(t))
    return SubPoint(
        lat_deg=float(position.latitude.degrees),
        lon_deg=float(position.longitude.degrees),
        alt_m=float(position.elevation.m),
    )


def look_angles(satellite: EarthSatellite, observer: GeographicPosition, t: Time) -> LookAngles:
    topocentric = (satellite - observer).at(t)
    elevation, azimuth, distance = topocentric.altaz()
    *_, range_rate = topocentric.frame_latlon_and_rates(observer)
    return LookAngles(
        azimuth_deg=float(azimuth.degrees) % 360.0,
        elevation_deg=float(elevation.degrees),
        range_m=float(distance.m),
        range_rate_m_s=float(range_rate.km_per_s) * 1000.0,
    )


# ---------- vectorized ----------


def teme_to_itrf(r_teme_km: Floats, v_teme_km_s: Floats, epochs: Epochs) -> tuple[Floats, Floats]:
    """Rotate SGP4 TEME vectors (``[..., n_times, 3]``) into the Earth-fixed frame, in metres."""
    cos_t, sin_t = np.cos(epochs.gmst_rad), np.sin(epochs.gmst_rad)
    x, y, z = r_teme_km[..., 0], r_teme_km[..., 1], r_teme_km[..., 2]
    vx, vy, vz = v_teme_km_s[..., 0], v_teme_km_s[..., 1], v_teme_km_s[..., 2]
    xe, ye = cos_t * x + sin_t * y, -sin_t * x + cos_t * y
    w = epochs.gmst_rate_rad_s  # the frame rotates under the satellite: v_ecef = R·v - ω x r_ecef
    vxe = cos_t * vx + sin_t * vy + w * ye
    vye = -sin_t * vx + cos_t * vy - w * xe
    r = np.stack((xe, ye, z), axis=-1) * 1000.0
    v = np.stack((vxe, vye, vz), axis=-1) * 1000.0
    return r, v


@dataclass(frozen=True)
class Observation:
    """Look angles for ``n_sats x n_times``; entries where ``ok`` is False are NaN."""

    azimuth_deg: Floats
    elevation_deg: Floats
    range_m: Floats
    range_rate_m_s: Floats
    r_itrf_m: Floats  # [n_sats, n_times, 3]
    ok: NDArray[np.bool_]


def topocentric(
    r_itrf_m: Floats, v_itrf_m_s: Floats, observer: Observer
) -> tuple[Floats, Floats, Floats, Floats]:
    """Earth-fixed position/velocity → (azimuth°, elevation°, range m, range-rate m/s)."""
    d = r_itrf_m - observer.itrf_m
    enu = d @ observer.enu_matrix.T
    range_m = np.linalg.norm(d, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        elevation = np.degrees(np.arcsin(np.clip(enu[..., 2] / range_m, -1.0, 1.0)))
        azimuth = np.degrees(np.arctan2(enu[..., 0], enu[..., 1])) % 360.0
        range_rate = np.einsum("...i,...i->...", d, v_itrf_m_s) / range_m
    return azimuth, elevation, range_m, range_rate


def _observation(
    r_teme: Floats, v_teme: Floats, ok: NDArray[np.bool_], epochs: Epochs, observer: Observer
) -> Observation:
    r, v = teme_to_itrf(r_teme, v_teme, epochs)
    azimuth, elevation, range_m, range_rate = topocentric(r, v, observer)
    for values in (azimuth, elevation, range_m, range_rate):
        values[~ok] = np.nan
    return Observation(azimuth, elevation, range_m, range_rate, r, ok)


def observe(satrecs: SatrecArray, epochs: Epochs, observer: Observer) -> Observation:
    """Propagate every satellite in ``satrecs`` at every epoch and look at it from ``observer``."""
    return _observation(*sgp4_teme(satrecs, epochs), epochs, observer)


def observe_pairs(satrecs: Sequence[Satrec], epochs: Epochs, observer: Observer) -> Observation:
    """Look at ``satrecs[i]`` at epoch ``i`` (arrays are 1-D: one value per pair)."""
    return _observation(*sgp4_teme_pairs(satrecs, epochs), epochs, observer)


def elevations(satrecs: SatrecArray, epochs: Epochs, observer: Observer) -> Floats:
    """Elevation only (°, NaN where SGP4 fails), ``[n_sats, n_times]``.

    The same chain as ``observe`` without velocities or azimuths: the cheap path for the coarse
    grids that pass searches scan.
    """
    r_teme, _, ok = sgp4_teme(satrecs, epochs)
    cos_t, sin_t = np.cos(epochs.gmst_rad), np.sin(epochs.gmst_rad)
    x, y = r_teme[..., 0] * 1000.0, r_teme[..., 1] * 1000.0
    ox, oy, oz = observer.itrf_m
    dx = cos_t * x + sin_t * y - ox
    dy = -sin_t * x + cos_t * y - oy
    dz = r_teme[..., 2] * 1000.0 - oz
    ux, uy, uz = observer.enu_matrix[2]
    with np.errstate(invalid="ignore", divide="ignore"):
        sin_el = (dx * ux + dy * uy + dz * uz) / np.sqrt(dx * dx + dy * dy + dz * dz)
        elevation = np.degrees(np.arcsin(np.clip(sin_el, -1.0, 1.0)))
    elevation[~ok] = np.nan
    return elevation


def off_nadir_deg(r_sat_m: Floats, r_station_m: Floats) -> Floats:
    """Angle at the satellite between its nadir and the line of sight to the station (°).

    Positions in one Earth-centred frame, ``[..., 3]``. This is where the station sits in the
    satellite's antenna pattern, assuming the satellite points its antenna at the nadir.
    """
    to_station = r_station_m - r_sat_m
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.einsum("...i,...i->...", -r_sat_m, to_station) / (
            np.linalg.norm(r_sat_m, axis=-1) * np.linalg.norm(to_station, axis=-1)
        )
        return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def geodetic(r_itrf_m: Floats) -> tuple[Floats, Floats, Floats]:
    """Earth-fixed position → WGS84 (latitude°, longitude°, height m)."""
    x, y, z = r_itrf_m[..., 0], r_itrf_m[..., 1], r_itrf_m[..., 2]
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - _WGS84_E2))
    for _ in range(5):  # converges to well under a millimetre from LEO to GEO
        sin_lat = np.sin(lat)
        n = WGS84_A_M / np.sqrt(1 - _WGS84_E2 * sin_lat**2)
        lat = np.arctan2(z + _WGS84_E2 * n * sin_lat, p)
    sin_lat = np.sin(lat)
    n = WGS84_A_M / np.sqrt(1 - _WGS84_E2 * sin_lat**2)
    height = p * np.cos(lat) + z * sin_lat - n * (1 - _WGS84_E2 * sin_lat**2)
    return np.degrees(lat), np.degrees(np.arctan2(y, x)), height


@dataclass(frozen=True)
class GroundTrack:
    """Orbit samples, evenly spaced in time from ``start`` to ``end`` (minus any SGP4 dropped)."""

    start: datetime
    end: datetime
    points: list[tuple[float, float]]  # (lon_deg, lat_deg)
    alt_m: list[float]  # height above the WGS84 ellipsoid, per point
    above_mask: list[bool]  # per point: above the observer's horizon mask (the in-view part)


def ground_track(
    elements: Elements,
    center: datetime,
    n_points: int,
    observer: Observer,
    mask_deg: float,
    orbits_before: float = 0.5,
    orbits_after: float = 1.0,
) -> GroundTrack:
    """Sample the orbit from ``orbits_before`` to ``orbits_after`` around ``center``: sub-point,
    height and whether ``observer`` sees it above the mask, through the tick's vectorized chain."""
    start = center - timedelta(seconds=elements.period_s * orbits_before)
    end = center + timedelta(seconds=elements.period_s * orbits_after)
    obs = observe(
        SatrecArray([elements.satellite.model]), linspace_epochs(start, end, n_points), observer
    )
    lat, lon, alt = geodetic(obs.r_itrf_m[0])
    points: list[tuple[float, float]] = []
    alt_m: list[float] = []
    above_mask: list[bool] = []
    for i in np.flatnonzero(obs.ok[0]):  # SGP4 yields nothing past decay
        points.append((round(float(lon[i]), 4), round(float(lat[i]), 4)))
        alt_m.append(round(float(alt[i]), 1))
        above_mask.append(bool(obs.elevation_deg[0, i] >= mask_deg))
    return GroundTrack(start=start, end=end, points=points, alt_m=alt_m, above_mask=above_mask)

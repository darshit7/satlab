"""SGP4 propagation.

Wraps TLEs into skyfield ``EarthSatellite`` objects (TEME → GCRS handled by skyfield) and samples
sub-satellite ground tracks. For many satellites at once, ``sgp4_teme`` runs the sgp4 library's
vectorized ``SatrecArray`` over ``Epochs`` prepared with skyfield's time conventions.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray
from sgp4.api import Satrec, SatrecArray
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.sgp4lib import theta_GMST1982
from skyfield.timelib import Time, Timescale

# Builtin leap-second / delta-T tables: no network download at import time.
TS: Timescale = load.timescale(builtin=True)

DAY_S = 86_400.0
_UNIX_EPOCH_JD = 2_440_587.5


@dataclass(frozen=True)
class Elements:
    """A satellite's orbital elements, ready to propagate."""

    norad_id: int
    name: str
    line1: str
    line2: str
    satellite: EarthSatellite
    epoch: datetime
    period_s: float


def elements_from_tle(name: str, line1: str, line2: str) -> Elements:
    satellite = EarthSatellite(line1, line2, name, TS)
    mean_motion_rad_min = satellite.model.no_kozai
    if mean_motion_rad_min <= 0:
        raise ValueError(f"TLE for {name!r} has non-positive mean motion")
    return Elements(
        norad_id=satellite.model.satnum,
        name=name,
        line1=line1,
        line2=line2,
        satellite=satellite,
        epoch=satellite.epoch.utc_datetime().astimezone(UTC),
        period_s=2 * math.pi / mean_motion_rad_min * 60.0,
    )


def to_time(when: datetime) -> Time:
    return TS.from_datetime(when.astimezone(UTC))


def jd_to_datetime(jd: float, fraction: float = 0.0) -> datetime:
    """UTC Julian date (as SGP4 stores TLE epochs) → aware datetime."""
    seconds = ((jd - _UNIX_EPOCH_JD) + fraction) * DAY_S
    return datetime.fromtimestamp(round(seconds, 6), UTC)


@dataclass(frozen=True)
class Epochs:
    """Instants prepared for vectorized SGP4 and the TEME → Earth-fixed rotation.

    Follows skyfield: TLE times are UTC (so SGP4 gets a UTC Julian date, split into whole days and
    a fraction for precision), while the GMST 1982 rotation runs on UT1.
    """

    start: datetime
    offsets_s: NDArray[np.float64]
    jd: NDArray[np.float64]
    fr: NDArray[np.float64]
    gmst_rad: NDArray[np.float64]
    gmst_rate_rad_s: float

    def __len__(self) -> int:
        return len(self.offsets_s)

    def at(self, i: int) -> datetime:
        return self.start + timedelta(seconds=float(self.offsets_s[i]))


@lru_cache(maxsize=256)
def _dut1_s(start: datetime) -> float:
    """UT1 - UTC in seconds; constant (to µs) over any window we sample from ``start``."""
    return float(to_time(start).dut1)


def epochs(start: datetime, offsets_s: Sequence[float] | NDArray[np.float64] = (0.0,)) -> Epochs:
    start = start.astimezone(UTC)
    offsets = np.atleast_1d(np.asarray(offsets_s, dtype=np.float64))
    if offsets.size == 0:
        raise ValueError("epochs() needs at least one offset")
    days = start.timestamp() / DAY_S
    whole = math.floor(days)
    fr = (days - whole) + offsets / DAY_S
    jd = np.full_like(fr, _UNIX_EPOCH_JD + whole)
    dut1_s = _dut1_s(start)
    gmst, gmst_rate_per_day = theta_GMST1982(jd, fr + dut1_s / DAY_S)
    return Epochs(
        start=start,
        offsets_s=offsets,
        jd=jd,
        fr=fr,
        gmst_rad=np.asarray(gmst, dtype=np.float64),
        gmst_rate_rad_s=float(np.ravel(gmst_rate_per_day)[0]) / DAY_S,  # constant to 1e-12
    )


def linspace_epochs(start: datetime, end: datetime, n_points: int) -> Epochs:
    return epochs(start, np.linspace(0.0, (end - start).total_seconds(), n_points))


def sgp4_teme(
    satrecs: SatrecArray, when: Epochs
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    """TEME position (km) and velocity (km/s), ``[n_sats, n_times, 3]``, plus a validity mask.

    Note: the sgp4 C extension holds the GIL for the whole call, so callers keep
    ``n_sats x n_times`` small enough not to stall the event loop.
    """
    errors, r, v = satrecs.sgp4(when.jd, when.fr)
    ok = (errors == 0) & np.isfinite(r).all(axis=-1)
    return r, v, ok


def sgp4_teme_pairs(
    satrecs: Sequence[Satrec], when: Epochs
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    """Like ``sgp4_teme`` but pairwise: ``satrecs[i]`` at epoch ``i``; arrays are ``[n, 3]``.

    For refinement steps where every satellite needs its own instant, which a ``SatrecArray``
    (every satellite x every time) would compute quadratically. Consecutive pairs of the same
    satellite go through one ``sgp4_array`` call.
    """
    n = len(satrecs)
    r = np.empty((n, 3))
    v = np.empty((n, 3))
    errors = np.empty(n, dtype=np.int8)
    i = 0
    while i < n:
        satrec = satrecs[i]
        j = i + 1
        while j < n and satrecs[j] is satrec:
            j += 1
        if j - i == 1:  # the scalar call has far less wrapper overhead
            errors[i], r[i], v[i] = satrec.sgp4(when.jd[i], when.fr[i])
        else:
            errors[i:j], r[i:j], v[i:j] = satrec.sgp4_array(when.jd[i:j], when.fr[i:j])
        i = j
    ok = (errors == 0) & np.isfinite(r).all(axis=-1)
    return r, v, ok


@dataclass(frozen=True)
class GroundTrack:
    start: datetime
    end: datetime
    points: list[tuple[float, float]]  # (lon_deg, lat_deg)


def ground_track(
    elements: Elements,
    center: datetime,
    n_points: int,
    orbits_before: float = 0.5,
    orbits_after: float = 1.0,
) -> GroundTrack:
    """Sample sub-satellite points from `orbits_before` to `orbits_after` around `center`."""
    start = center - timedelta(seconds=elements.period_s * orbits_before)
    end = center + timedelta(seconds=elements.period_s * orbits_after)
    times = TS.linspace(to_time(start), to_time(end), n_points)
    lat, lon = wgs84.latlon_of(elements.satellite.at(times))
    points = [
        (round(float(lo), 4), round(float(la), 4))
        for lo, la in zip(lon.degrees, lat.degrees, strict=True)
        if math.isfinite(lo) and math.isfinite(la)  # SGP4 yields NaN past decay
    ]
    return GroundTrack(start=start, end=end, points=points)

"""Subsystem models: real eclipse geometry, deterministic integration, RAG and the rollup."""

import math
from datetime import timedelta

import numpy as np
import pytest
from sgp4.api import Satrec, SatrecArray

from app.data.celestrak import parse_tle_text
from app.data.seed import link_profile_specs
from app.sim.fleet import Fleet, FleetRow
from app.sim.geometry import Observer
from app.sim.linkbudget import RagThresholds
from app.sim.propagate import epochs, sgp4_teme
from app.sim.subsystems import (
    MU_EARTH_KM3_S2,
    HealthTracker,
    battery_rates,
    integrate,
    rollup,
    subsystem_models,
)
from app.sim.sun import EARTH_RADIUS_KM, sun_position_km, sunlit_fraction
from tests.conftest import FLEET_2026_TIME, FLEET_2026_TLE, WALLOPS

RECORDS = {r.norad_id: r for r in parse_tle_text(FLEET_2026_TLE)}
NOAA_20, ISS = 43013, 25544


def _fleet() -> Fleet:
    rows = [
        FleetRow(r.norad_id, r.name, "weather", False, r.line1, r.line2) for r in RECORDS.values()
    ]
    return Fleet.build(rows, [s.to_model() for s in link_profile_specs().profiles])


def _satrec(norad_id: int) -> Satrec:
    r = RECORDS[norad_id]
    return Satrec.twoline2rv(r.line1, r.line2)


def _tracker() -> HealthTracker:
    return HealthTracker(
        Observer(*WALLOPS), 5.0, RagThresholds(), lookback_s=24 * 3600, step_s=60.0
    )


def _state(tracker: HealthTracker, fleet: Fleet, norad_id: int, when, **kw):
    return tracker.state(
        norad_id=norad_id,
        satrec=_satrec(norad_id),
        kind="weather",
        links=fleet.links,
        sim_time=when,
        elevation_deg=kw.get("elevation_deg", -10.0),
        off_nadir=kw.get("off_nadir", 80.0),
        link=None,
    )


def test_iss_eclipse_matches_the_cylindrical_shadow() -> None:
    sat = _satrec(ISS)
    period_s = 2 * math.pi / sat.no_kozai * 60
    when = epochs(FLEET_2026_TIME, np.arange(0.0, period_s, 10.0))
    r, v, ok = sgp4_teme(SatrecArray([sat]), when)
    assert ok.all()
    sun = sun_position_km(when)
    f = sunlit_fraction(r[0], sun)
    shadow_s = float((f < 0.5).sum()) * 10.0

    h = np.cross(r[0, 0], v[0, 0])
    s = sun[len(sun) // 2]
    beta = math.asin(np.dot(h / np.linalg.norm(h), s / np.linalg.norm(s)))
    a = float(np.linalg.norm(r[0], axis=-1).mean())
    expected_s = (period_s / math.pi) * math.acos(
        min(1.0, math.sqrt(a * a - EARTH_RADIUS_KM**2) / (a * math.cos(beta)))
    )
    assert expected_s > 20 * 60  # this epoch has a real eclipse to compare
    assert shadow_s == pytest.approx(expected_s, abs=60)


def test_battery_is_sized_for_the_longest_eclipse() -> None:
    model = subsystem_models().default.power
    a_km = EARTH_RADIUS_KM + 420
    period_s = 2 * math.pi * math.sqrt(a_km**3 / MU_EARTH_KM3_S2)
    charge, load = battery_rates(model, period_s)
    eclipse_h = math.asin(EARTH_RADIUS_KM / a_km) / math.pi * period_s / 3600
    assert load * eclipse_h == pytest.approx(model.design_dod_pct)
    assert charge > load


def test_storage_fills_without_contact_and_empties_with_one() -> None:
    model = subsystem_models().default
    sunlit = np.ones(1440)
    kw = {"step_s": 60.0, "tail_s": 0.0, "period_s": 6000.0}
    assert integrate(model, sunlit, np.zeros(1440, bool), **kw).fill_pct == 100.0
    assert integrate(model, sunlit, np.ones(1440, bool), **kw).fill_pct == 0.0
    assert integrate(model, sunlit, None, **kw).fill_pct is None
    # constant sunlight: full battery, temperature at the sunlit equilibrium
    core = integrate(model, sunlit, None, **kw)
    assert core.soc_pct == 100.0
    assert core.temperature_c == pytest.approx(model.thermal.sunlit_eq_c)


def test_health_is_deterministic_in_sim_time() -> None:
    fleet = _fleet()
    t = FLEET_2026_TIME + timedelta(hours=6, minutes=7, seconds=13)

    fresh = _state(_tracker(), fleet, NOAA_20, t)

    stepped = _tracker()
    for minutes in range(-180, 0, 7):  # walk up to it, as a running clock would
        _state(stepped, fleet, NOAA_20, t + timedelta(minutes=minutes))
    assert _state(stepped, fleet, NOAA_20, t) == fresh

    jumped = _tracker()
    _state(jumped, fleet, NOAA_20, t + timedelta(hours=2))  # a jump forward, then back
    assert _state(jumped, fleet, NOAA_20, t) == fresh

    assert fresh is not None
    assert fresh.model_version == subsystem_models().version
    assert fresh.storage.fill_pct is not None  # NOAA 20 has a link profile: storage is modelled
    assert fresh.rf.rag is None  # no link state passed in


def test_battery_drains_in_eclipse() -> None:
    fleet = _fleet()
    sat = _satrec(ISS)
    when = epochs(FLEET_2026_TIME, np.arange(0.0, 6000.0, 10.0))
    r, _, _ = sgp4_teme(SatrecArray([sat]), when)
    f = sunlit_fraction(r[0], sun_position_km(when))
    entry = int(np.flatnonzero(f == 0.0)[0])  # first instant in the umbra
    t0 = when.at(entry)

    tracker = _tracker()
    start = _state(tracker, fleet, ISS, t0)
    later = _state(tracker, fleet, ISS, t0 + timedelta(minutes=20))
    assert start is not None and later is not None
    assert later.power.illumination == "eclipse"
    assert later.power.sunlit_fraction == 0.0
    assert later.power.soc_pct < start.power.soc_pct
    assert later.thermal.temperature_c < start.thermal.temperature_c
    assert later.storage.rag is None  # the ISS has no link profile: nothing to downlink
    assert later.attitude.rag is None  # below the horizon here


@pytest.mark.parametrize(
    ("rags", "expected"),
    [
        (["green"] * 5, "green"),
        (["green", "amber", "green", "green", "green"], "amber"),
        (["green", "amber", None, "green", "red"], "red"),  # red wins over missing data
        (["green", "green", None, "green", "green"], None),  # missing data blocks a verdict
        (["amber", "amber", "amber", None, None], None),
    ],
)
def test_rollup(rags, expected) -> None:
    assert rollup(rags) == expected

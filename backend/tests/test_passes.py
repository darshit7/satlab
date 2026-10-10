"""Pass prediction vs skyfield's ``find_events``, plus sky arcs and the rising-soon sweep."""

from datetime import timedelta

import numpy as np
import pytest

from app.data.celestrak import parse_tle_text
from app.data.classify import classify
from app.sim.fleet import Fleet, FleetRow
from app.sim.geometry import Observer
from app.sim.passes import LIVE, UpcomingSweep, find_spans, pass_arcs
from app.sim.propagate import elements_from_tle, to_time
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

DAY_S = 86_400.0

# skyfield's find_events (the reference here) trips a numpy 2.5 deprecation internally.
pytestmark = pytest.mark.filterwarnings("ignore:Setting the dtype:DeprecationWarning")


@pytest.fixture
def iss():
    return elements_from_tle(ISS_NAME, ISS_LINE1, ISS_LINE2)


def skyfield_passes(elements, observer: Observer, start, duration_s: float, mask_deg: float):
    """Complete (rise, culminate, set) triples from skyfield, with az at rise/set and max alt."""
    topos = observer.to_skyfield()
    t, events = elements.satellite.find_events(
        topos, to_time(start), to_time(start + timedelta(seconds=duration_s)), mask_deg
    )
    out = []
    for i in range(len(events) - 2):
        if list(events[i : i + 3]) == [0, 1, 2]:
            rise, peak, set_ = t[i], t[i + 1], t[i + 2]
            look = (elements.satellite - topos).at
            out.append(
                {
                    "aos": rise.utc_datetime(),
                    "los": set_.utc_datetime(),
                    "max_elev": look(peak).altaz()[0].degrees,
                    "aos_az": look(rise).altaz()[1].degrees,
                    "los_az": look(set_).altaz()[1].degrees,
                }
            )
    return out


def az_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@pytest.mark.parametrize("mask_deg", [0.0, 5.0, 30.0])
def test_find_spans_matches_skyfield(iss, mask_deg: float) -> None:
    observer = Observer(*BLUFFTON)
    [spans] = find_spans([iss.satellite.model], observer, REFERENCE_TIME, DAY_S, 30.0, mask_deg)
    complete = [s for s in spans if s.aos and s.los]
    reference = skyfield_passes(iss, observer, REFERENCE_TIME, DAY_S, mask_deg)
    assert len(complete) == len(reference) > 0
    for ours, ref in zip(complete, reference, strict=True):
        assert abs((ours.aos - ref["aos"]).total_seconds()) < 1.0
        assert abs((ours.los - ref["los"]).total_seconds()) < 1.0
        assert ours.max_elev_deg == pytest.approx(ref["max_elev"], abs=0.01)
        # A pass that barely clears the mask rises near culmination, where azimuth sweeps fast:
        # a fraction of a second between the two solvers is then a tenth of a degree.
        az_tol = 0.05 if ours.max_elev_deg - mask_deg > 1.0 else 0.5
        assert az_diff(ours.aos_az_deg, ref["aos_az"]) < az_tol
        assert az_diff(ours.los_az_deg, ref["los_az"]) < az_tol
        assert ours.aos < ours.max_time < ours.los


def test_in_progress_pass_is_open_ended(iss) -> None:
    # The ISS is at 16° at the reference time: the first span began before the search started.
    [spans] = find_spans([iss.satellite.model], Observer(*BLUFFTON), REFERENCE_TIME, DAY_S, 30, 5)
    assert spans[0].aos is None and spans[0].aos_az_deg is None
    assert spans[0].los is not None
    assert all(s.aos is not None for s in spans[1:])


def test_short_grazing_pass_is_found(iss) -> None:
    # At a 0° mask there is a pass peaking at ~0.6°, far shorter than its neighbours.
    [spans] = find_spans([iss.satellite.model], Observer(*BLUFFTON), REFERENCE_TIME, DAY_S, 60, 0)
    grazes = [s for s in spans if s.max_elev_deg < 1.0]
    assert len(grazes) == 1
    assert grazes[0].aos is not None and grazes[0].los is not None


def test_geostationary_is_up_all_window() -> None:
    [goes] = [r for r in parse_tle_text(FLEET_2026_TLE) if r.name == "GOES 16"]
    sat = elements_from_tle(goes.name, goes.line1, goes.line2).satellite.model
    [spans] = find_spans([sat], Observer(*WALLOPS), FLEET_2026_TIME, DAY_S, 30.0, 5.0)
    assert len(spans) == 1
    assert spans[0].aos is None and spans[0].los is None
    assert spans[0].max_elev_deg > 30


def test_arc_of_pass_in_progress(iss) -> None:
    observer = Observer(*BLUFFTON)
    [arc] = pass_arcs(
        [iss.satellite.model],
        observer,
        REFERENCE_TIME,
        5.0,
        lookback_s=1800,
        lookahead_s=3600,
        step_s=30,
        n_points=48,
    )
    assert arc is not None
    [spans] = find_spans(
        [iss.satellite.model],
        observer,
        REFERENCE_TIME - timedelta(seconds=1800),
        5400,
        30.0,
        5.0,
    )
    ref = next(s for s in spans if s.aos and s.los and s.aos <= REFERENCE_TIME <= s.los)
    assert abs((arc.aos - ref.aos).total_seconds()) < 2 * LIVE.crossing_s
    assert abs((arc.los - ref.los).total_seconds()) < 2 * LIVE.crossing_s
    assert (arc.start, arc.end) == (arc.aos, arc.los)
    assert arc.start < REFERENCE_TIME < arc.end
    assert len(arc.points) == 48
    assert arc.points[0][1] == pytest.approx(5.0, abs=0.05)  # starts and ends on the mask
    assert arc.points[-1][1] == pytest.approx(5.0, abs=0.05)
    assert max(el for _, el in arc.points) == pytest.approx(ref.max_elev_deg, abs=0.5)


def test_arc_of_next_pass_when_below(iss) -> None:
    observer = Observer(*BLUFFTON)
    [spans] = find_spans([iss.satellite.model], observer, REFERENCE_TIME, DAY_S, 30.0, 5.0)
    after_first = spans[0].los + timedelta(minutes=1)
    [arc] = pass_arcs(
        [iss.satellite.model],
        observer,
        after_first,
        5.0,
        lookback_s=1800,
        lookahead_s=3 * 3600,
        step_s=30,
        n_points=16,
    )
    assert arc is not None and arc.aos is not None
    assert arc.aos > after_first
    assert abs((arc.aos - spans[1].aos).total_seconds()) < 1.0


def _fleet() -> Fleet:
    rows = []
    for rec in parse_tle_text(FLEET_2026_TLE):
        c = classify(rec.name, rec.norad_id)
        rows.append(
            FleetRow(rec.norad_id, rec.name, c.type, c.is_sar_capable, rec.line1, rec.line2)
        )
    return Fleet.build(rows)


def _sweep(chunk_size: int) -> UpcomingSweep:
    return UpcomingSweep(
        Observer(*WALLOPS),
        5.0,
        window_s=5400,
        step_s=60,
        chunk_size=chunk_size,
        max_within_s=3600,
    )


def test_sweep_chunked_equals_unchunked() -> None:
    fleet = _fleet()
    whole, sliced = _sweep(100), _sweep(2)
    assert whole.advance(fleet, FLEET_2026_TIME, 0.5)
    ticks = 1
    while not sliced.advance(fleet, FLEET_2026_TIME, 0.5):
        ticks += 1
    assert ticks == 5  # 9 satellites, 2 per tick
    a, b = whole.result, sliced.result
    assert a is not None and b is not None
    np.testing.assert_array_equal(a.rows, b.rows)
    np.testing.assert_allclose(a.aos_s, b.aos_s)
    assert whole.valid_for(fleet, FLEET_2026_TIME)
    assert not whole.valid_for(_fleet(), FLEET_2026_TIME)  # rebuilt catalog: stale


def test_sweep_matches_find_spans_and_query_filters() -> None:
    fleet = _fleet()
    sweep = _sweep(100)
    sweep.advance(fleet, FLEET_2026_TIME, 0.5)
    spans = find_spans(list(fleet.satrecs), Observer(*WALLOPS), FLEET_2026_TIME, 5400, 30, 5.0)
    expected = {
        int(fleet.norad_ids[k]): s.aos
        for k, per_sat in enumerate(spans)
        for s in per_sat[::-1]  # keep each satellite's first rise
        if s.aos is not None and s.aos > FLEET_2026_TIME
    }
    everyone = np.ones(len(fleet), dtype=bool)
    nobody = np.zeros(len(fleet), dtype=bool)
    rises, total = sweep.query(
        FLEET_2026_TIME,
        allowed_rows=everyone,
        exclude_rows=nobody,
        within_s=3600,
        min_max_elev_deg=5.0,
        limit=100,
    )
    got = {int(fleet.norad_ids[r.row]): r.aos for r in rises}
    assert total == len(rises) == len(expected) > 0
    assert got.keys() == expected.keys()
    for norad_id, aos in got.items():
        assert abs((aos - expected[norad_id]).total_seconds()) < 1.0
    assert [r.aos for r in rises] == sorted(r.aos for r in rises)

    # type filter, exclusion of what's already up, and the cap
    only_sar = fleet.type_mask(["sar"])
    sar_rises, _ = sweep.query(
        FLEET_2026_TIME,
        allowed_rows=only_sar,
        exclude_rows=nobody,
        within_s=3600,
        min_max_elev_deg=5.0,
        limit=100,
    )
    assert all(fleet.type_of(r.row) == "sar" for r in sar_rises)
    first = rises[0].row
    excluded = nobody.copy()
    excluded[first] = True
    rest, _ = sweep.query(
        FLEET_2026_TIME,
        allowed_rows=everyone,
        exclude_rows=excluded,
        within_s=3600,
        min_max_elev_deg=5.0,
        limit=1,
    )
    assert len(rest) == 1 and rest[0].row != first


def test_sweep_restarts_after_clock_jump() -> None:
    fleet = _fleet()
    sweep = _sweep(100)
    assert sweep.advance(fleet, FLEET_2026_TIME, 0.5)
    later = FLEET_2026_TIME + timedelta(hours=3)
    assert not sweep.valid_for(fleet, later)
    assert sweep.advance(fleet, later, 0.5)
    assert sweep.result is not None and sweep.result.start == later

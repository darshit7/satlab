"""Pass prediction: AOS, culmination and LOS of satellites above a station's horizon mask.

One algorithm serves the pass-schedule endpoint, the per-satellite sky arcs and the rising-soon
sweep:

1. Sample elevation on a coarse time grid, vectorized over satellites x times.
2. Find the runs of samples above the mask, plus local maxima just below it: a short grazing pass
   can peak between two samples.
3. Refine each culmination by golden-section search and each AOS / LOS by bisection. Refinement is
   batched across satellites as (satellite, instant) pairs, so it costs one cheap SGP4 evaluation
   per pair per iteration.

Times inside the module are float seconds from the search start. Verified against skyfield's
``EarthSatellite.find_events`` (tests/test_passes.py).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from sgp4.api import Satrec, SatrecArray

from app.sim.fleet import Fleet
from app.sim.geometry import Observer, elevations, observe_pairs
from app.sim.propagate import epochs

# A pass can peak between two grid samples; local maxima this far below the mask get refined too.
GRAZE_MARGIN_DEG = 2.0


@dataclass(frozen=True)
class Precision:
    """Refinement tolerances: how close AOS/LOS and the culmination time are pinned down."""

    crossing_s: float = 0.05
    culmination_s: float = 0.01


SCHEDULE = Precision()  # the pass-schedule endpoint
LIVE = Precision(crossing_s=0.25, culmination_s=0.5)  # sky arcs and the rising-soon sweep
_GOLDEN = (math.sqrt(5) - 1) / 2
_BELOW = -90.0  # elevation used where SGP4 fails (e.g. decayed elements): never "in view"

Floats = NDArray[np.float64]
Ints = NDArray[np.intp]


@dataclass(frozen=True)
class Span:
    """One pass of one satellite above the mask.

    ``aos`` / ``los`` (and their azimuths) are None when the pass is already in progress at the
    start of the search / still in progress at its end.
    """

    aos: datetime | None
    los: datetime | None
    max_time: datetime
    max_elev_deg: float
    aos_az_deg: float | None
    los_az_deg: float | None


def _grid_offsets(duration_s: float, step_s: float) -> Floats:
    offsets = np.arange(0.0, duration_s, step_s)
    if offsets.size == 0 or offsets[-1] < duration_s:
        offsets = np.append(offsets, duration_s)
    return offsets


class _Pairs:
    """Evaluates (satellite, instant) pairs relative to one search start."""

    def __init__(
        self,
        satrecs: Sequence[Satrec],
        start: datetime,
        observer: Observer,
        precision: Precision = SCHEDULE,
    ) -> None:
        self.satrecs = satrecs
        self.start = start
        self.observer = observer
        self.precision = precision

    def look(self, sats: Ints, offsets_s: Floats) -> tuple[Floats, Floats]:
        """(azimuth°, elevation°) of ``satrecs[sats[i]]`` at ``start + offsets_s[i]``."""
        if len(sats) == 0:
            return np.empty(0), np.empty(0)
        obs = observe_pairs(
            [self.satrecs[int(i)] for i in sats], epochs(self.start, offsets_s), self.observer
        )
        return obs.azimuth_deg, np.where(obs.ok, obs.elevation_deg, _BELOW)

    def bisect(self, sats: Ints, lo: Floats, hi: Floats, mask_deg: float, rising: bool) -> Floats:
        """Crossing instants bracketed by [lo, hi] (rising: below at lo, above at hi)."""
        if len(sats) == 0:
            return lo
        tol = self.precision.crossing_s
        width = float(np.max(hi - lo))
        for _ in range(max(1, math.ceil(math.log2(max(width, tol) / tol)))):
            mid = (lo + hi) / 2
            _, el = self.look(sats, mid)
            crossing_before_mid = (el >= mask_deg) == rising
            hi = np.where(crossing_before_mid, mid, hi)
            lo = np.where(crossing_before_mid, lo, mid)
        return (lo + hi) / 2

    def culminate(self, sats: Ints, lo: Floats, hi: Floats) -> tuple[Floats, Floats]:
        """Golden-section search for the elevation maximum inside each [lo, hi]."""
        if len(sats) == 0:
            return lo, lo
        n = len(sats)
        tol = self.precision.culmination_s
        width = float(np.max(hi - lo))
        iterations = math.ceil(math.log(tol / max(width, tol), _GOLDEN))
        for _ in range(max(1, iterations)):
            c = hi - _GOLDEN * (hi - lo)
            d = lo + _GOLDEN * (hi - lo)
            _, el = self.look(np.concatenate((sats, sats)), np.concatenate((c, d)))
            left = el[:n] >= el[n:]
            hi = np.where(left, d, hi)
            lo = np.where(left, lo, c)
        t = (lo + hi) / 2
        _, el = self.look(sats, t)
        return t, el


def find_spans(
    satrecs: Sequence[Satrec],
    observer: Observer,
    start: datetime,
    duration_s: float,
    step_s: float,
    mask_deg: float,
    array: SatrecArray | None = None,
    precision: Precision = SCHEDULE,
) -> list[list[Span]]:
    """Every pass of every satellite in [start, start + duration_s], in time order per satellite.

    ``array`` may pass a prebuilt ``SatrecArray`` of ``satrecs`` (e.g. a fleet subset).
    """
    k_sats = len(satrecs)
    result: list[list[Span]] = [[] for _ in range(k_sats)]
    if k_sats == 0:
        return result
    offsets = _grid_offsets(duration_s, step_s)
    el = elevations(array or SatrecArray(list(satrecs)), epochs(start, offsets), observer)
    el = np.where(np.isnan(el), _BELOW, el)
    m = len(offsets)
    above = el >= mask_deg

    # Runs of consecutive above-mask samples (row-major order keeps starts and ends aligned).
    before = np.zeros_like(above)
    before[:, 1:] = above[:, :-1]
    after = np.zeros_like(above)
    after[:, :-1] = above[:, 1:]
    run_k, run_j0 = np.nonzero(above & ~before)
    _, run_j1 = np.nonzero(above & ~after)
    run_peak = np.array(
        [
            j0 + int(np.argmax(el[k, j0 : j1 + 1]))
            for k, j0, j1 in zip(run_k, run_j0, run_j1, strict=True)
        ],
        dtype=np.intp,
    )

    # Grazes: an interior local maximum just below the mask may hide a peak above it.
    mid = el[:, 1:-1]
    graze = (
        (mid >= el[:, :-2])
        & (mid > el[:, 2:])
        & (mid < mask_deg)
        & (mid >= mask_deg - GRAZE_MARGIN_DEG)
    )
    graze_k, graze_j = np.nonzero(graze)
    graze_j = graze_j + 1

    # 1. Culminations.
    pairs = _Pairs(satrecs, start, observer, precision)
    peak_k = np.concatenate((run_k, graze_k)).astype(np.intp)
    peak_j = np.concatenate((run_peak, graze_j)).astype(np.intp)
    t_max, el_max = pairs.culminate(
        peak_k, offsets[np.maximum(peak_j - 1, 0)], offsets[np.minimum(peak_j + 1, m - 1)]
    )
    n_runs = len(run_k)
    graze_ok = el_max[n_runs:] >= mask_deg
    graze_k, graze_j = graze_k[graze_ok], graze_j[graze_ok]
    graze_t, graze_el = t_max[n_runs:][graze_ok], el_max[n_runs:][graze_ok]

    # 2. Crossings. Each candidate pass: (k, aos bracket | None, los bracket | None, t_max, el_max)
    cand_k = np.concatenate((run_k, graze_k)).astype(np.intp)
    cand_t = np.concatenate((t_max[:n_runs], graze_t))
    cand_el = np.concatenate((el_max[:n_runs], graze_el))
    has_aos = np.concatenate((run_j0 > 0, np.ones(len(graze_k), dtype=bool)))
    has_los = np.concatenate((run_j1 < m - 1, np.ones(len(graze_k), dtype=bool)))
    aos_lo = np.concatenate((offsets[np.maximum(run_j0 - 1, 0)], offsets[graze_j - 1]))
    aos_hi = np.concatenate((offsets[run_j0], graze_t))
    los_lo = np.concatenate((offsets[run_j1], graze_t))
    los_hi = np.concatenate((offsets[np.minimum(run_j1 + 1, m - 1)], offsets[graze_j + 1]))

    aos_t = np.full(len(cand_k), np.nan)
    los_t = np.full(len(cand_k), np.nan)
    aos_t[has_aos] = pairs.bisect(
        cand_k[has_aos], aos_lo[has_aos], aos_hi[has_aos], mask_deg, rising=True
    )
    los_t[has_los] = pairs.bisect(
        cand_k[has_los], los_lo[has_los], los_hi[has_los], mask_deg, rising=False
    )
    aos_az = np.full(len(cand_k), np.nan)
    los_az = np.full(len(cand_k), np.nan)
    az, _ = pairs.look(
        np.concatenate((cand_k[has_aos], cand_k[has_los])),
        np.concatenate((aos_t[has_aos], los_t[has_los])),
    )
    aos_az[has_aos] = az[: int(has_aos.sum())]
    los_az[has_los] = az[int(has_aos.sum()) :]

    def at(offset_s: float) -> datetime:
        return start + timedelta(seconds=float(offset_s))

    def opt(value: float) -> float | None:
        return None if math.isnan(value) else float(value)

    for i in np.argsort(cand_t, kind="stable"):
        result[int(cand_k[i])].append(
            Span(
                aos=at(aos_t[i]) if has_aos[i] else None,
                los=at(los_t[i]) if has_los[i] else None,
                max_time=at(cand_t[i]),
                max_elev_deg=float(cand_el[i]),
                aos_az_deg=opt(aos_az[i]),
                los_az_deg=opt(los_az[i]),
            )
        )
    return result


# ---------- sky arcs ----------


@dataclass(frozen=True)
class Arc:
    """A pass sampled for drawing: ``points`` are (azimuth°, elevation°) evenly spaced in time."""

    aos: datetime | None
    los: datetime | None
    max_elev_deg: float
    start: datetime
    end: datetime
    points: list[tuple[float, float]]


def pass_arcs(
    satrecs: Sequence[Satrec],
    observer: Observer,
    now: datetime,
    mask_deg: float,
    *,
    lookback_s: float,
    lookahead_s: float,
    step_s: float,
    n_points: int,
) -> list[Arc | None]:
    """For each satellite: the pass in progress at ``now``, else the next one starting within
    ``lookahead_s``; clipped to [now - lookback_s, now + lookahead_s]. None if there is none."""
    window_start = now - timedelta(seconds=lookback_s)
    window_s = lookback_s + lookahead_s
    spans = find_spans(satrecs, observer, window_start, window_s, step_s, mask_deg, precision=LIVE)
    window_end = window_start + timedelta(seconds=window_s)

    chosen: list[Span | None] = []
    for per_sat in spans:
        chosen.append(next((s for s in per_sat if (s.los or window_end) >= now), None))

    picked = [k for k, s in enumerate(chosen) if s is not None]
    arcs: list[Arc | None] = [None] * len(satrecs)
    if not picked:
        return arcs
    bounds = []
    for k in picked:
        span = chosen[k]
        assert span is not None
        bounds.append((span.aos or window_start, span.los or window_end))
    fractions = np.linspace(0.0, 1.0, n_points)
    offsets = np.concatenate(
        [
            (a - window_start).total_seconds() + fractions * (b - a).total_seconds()
            for a, b in bounds
        ]
    )
    sats = np.repeat(np.asarray(picked, dtype=np.intp), n_points)
    az, el = _Pairs(satrecs, window_start, observer).look(sats, offsets)
    points = np.stack((np.round(az, 3), np.round(el, 3)), axis=-1).reshape(-1, n_points, 2).tolist()
    for row, (k, (a, b)) in enumerate(zip(picked, bounds, strict=True)):
        span = chosen[k]
        assert span is not None
        arcs[k] = Arc(
            aos=span.aos,
            los=span.los,
            max_elev_deg=span.max_elev_deg,
            start=a,
            end=b,
            points=[(x, y) for x, y in points[row]],
        )
    return arcs


# ---------- rising-soon sweep ----------


@dataclass(frozen=True)
class UpcomingResult:
    fleet: Fleet
    start: datetime
    rows: Ints  # fleet row of each rise
    aos_s: Floats  # seconds from ``start``
    aos_az_deg: Floats
    max_elev_deg: Floats  # within the sweep window (a pass running past its end is cut short)


@dataclass(frozen=True)
class UpcomingRise:
    row: int
    aos: datetime
    aos_az_deg: float
    max_elev_deg: float


class UpcomingSweep:
    """Finds every rise above the mask in [S, S + window] across the whole fleet, a chunk at a time.

    The sgp4 extension holds the GIL, so the sweep is sliced into ``advance()`` calls the sim loop
    makes once per tick. The last completed result keeps serving queries while the next sweep runs,
    and the chunk size adapts: full ``chunk_size`` while there is nothing to show yet, otherwise
    just enough to finish (with a 2x margin) before the current result expires at this clock rate.
    """

    def __init__(
        self,
        observer: Observer,
        mask_deg: float,
        *,
        window_s: float,
        step_s: float,
        chunk_size: int,
        max_within_s: float,
    ) -> None:
        self.observer = observer
        self.mask_deg = mask_deg
        self.window_s = window_s
        self.step_s = step_s
        self.chunk_size = chunk_size
        self.min_chunk_size = max(1, chunk_size // 5)
        # A result answers "rising within max_within_s" until now passes start + valid_s; the next
        # sweep starts a third of the way in so it lands before the current one expires.
        self.valid_s = window_s - max_within_s
        self.restart_after_s = self.valid_s / 3
        self.result: UpcomingResult | None = None
        self._running: tuple[Fleet, datetime] | None = None
        self._cursor = 0
        self._parts: list[tuple[Ints, Floats, Floats, Floats]] = []

    def valid_for(self, fleet: Fleet, now: datetime) -> bool:
        r = self.result
        if r is None or r.fleet is not fleet:
            return False
        elapsed = (now - r.start).total_seconds()
        return 0 <= elapsed <= self.valid_s

    def _should_start(self, fleet: Fleet, now: datetime) -> bool:
        if self._running is not None:
            run_fleet, run_start = self._running
            elapsed = (now - run_start).total_seconds()
            # restart a sweep made stale by a catalog refresh or a clock jump
            return run_fleet is not fleet or not 0 <= elapsed <= self.valid_s
        if not self.valid_for(fleet, now):
            return True
        assert self.result is not None
        return (now - self.result.start).total_seconds() >= self.restart_after_s

    def _chunk(self, fleet: Fleet, now: datetime, sim_s_per_tick: float) -> int:
        remaining = len(fleet) - self._cursor
        if not self.valid_for(fleet, now) or self.result is None:
            return self.chunk_size  # nothing to show yet: go as fast as allowed
        expires_in_s = (self.result.start - now).total_seconds() + self.valid_s
        if sim_s_per_tick <= 0:
            return self.min_chunk_size  # paused: the current result never expires
        ticks_left = max(1.0, expires_in_s / sim_s_per_tick / 2)
        return int(
            min(self.chunk_size, max(self.min_chunk_size, math.ceil(remaining / ticks_left)))
        )

    def advance(self, fleet: Fleet, now: datetime, sim_s_per_tick: float) -> bool:
        """Process one chunk. Returns True when a sweep has just completed."""
        if self._should_start(fleet, now):
            self._running = (fleet, now)
            self._cursor = 0
            self._parts = []
        if self._running is None:
            return False
        run_fleet, run_start = self._running
        chunk = self._chunk(run_fleet, now, sim_s_per_tick)
        lo, hi = self._cursor, min(self._cursor + chunk, len(run_fleet))
        if hi > lo:
            rows = np.arange(lo, hi, dtype=np.intp)
            spans = find_spans(
                run_fleet.satrecs[lo:hi],
                self.observer,
                run_start,
                self.window_s,
                self.step_s,
                self.mask_deg,
                array=run_fleet.subset(rows),
                precision=LIVE,
            )
            found = [
                (lo + k, (s.aos - run_start).total_seconds(), s.aos_az_deg, s.max_elev_deg)
                for k, per_sat in enumerate(spans)
                for s in per_sat
                if s.aos is not None and s.aos_az_deg is not None
            ]
            if found:
                r, t, az, el = zip(*found, strict=True)
                self._parts.append(
                    (np.array(r, dtype=np.intp), np.array(t), np.array(az), np.array(el))
                )
        self._cursor = hi
        if hi < len(run_fleet):
            return False
        parts = self._parts or [(np.empty(0, np.intp), np.empty(0), np.empty(0), np.empty(0))]
        self.result = UpcomingResult(
            fleet=run_fleet,
            start=run_start,
            rows=np.concatenate([p[0] for p in parts]),
            aos_s=np.concatenate([p[1] for p in parts]),
            aos_az_deg=np.concatenate([p[2] for p in parts]),
            max_elev_deg=np.concatenate([p[3] for p in parts]),
        )
        self._running, self._parts = None, []
        return True

    def query(
        self,
        now: datetime,
        *,
        allowed_rows: NDArray[np.bool_],
        exclude_rows: NDArray[np.bool_],
        within_s: float,
        min_max_elev_deg: float,
        limit: int,
    ) -> tuple[list[UpcomingRise], int]:
        """Each satellite's soonest rise in (now, now + within_s]: (the first ``limit``, total)."""
        r = self.result
        if r is None or len(r.rows) == 0:
            return [], 0
        t = (now - r.start).total_seconds()
        keep = (
            (r.aos_s > t)
            & (r.aos_s <= t + within_s)
            & (r.max_elev_deg >= min_max_elev_deg)
            & allowed_rows[r.rows]
            & ~exclude_rows[r.rows]
        )
        idx = np.flatnonzero(keep)
        idx = idx[np.argsort(r.aos_s[idx], kind="stable")]
        _, first = np.unique(r.rows[idx], return_index=True)  # each satellite's soonest rise
        idx = idx[np.sort(first)]
        rises = [
            UpcomingRise(
                row=int(r.rows[i]),
                aos=r.start + timedelta(seconds=float(r.aos_s[i])),
                aos_az_deg=round(float(r.aos_az_deg[i]), 2),
                max_elev_deg=round(float(r.max_elev_deg[i]), 2),
            )
            for i in idx[:limit]
        ]
        return rises, len(idx)

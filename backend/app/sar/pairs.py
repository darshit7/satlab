"""From search results to comparable passes, tracks and a proposed before/after pair (pure).

* A **pass** is one satellite's overflight: consecutive frames (STAC items) of the same platform,
  relative orbit and direction, seconds apart. An area can straddle two frames, so a comparison
  reads every frame of a pass that touches it and mosaics them onto the grid.
* A **track** is a relative orbit plus a direction. Only passes on the same track see the area
  from the same geometry, so only they are paired (Sentinel-1C and -1D share the tracks, 6 days
  apart). Cross-track pairs are never formed: they would flag geometry, not change.
* Coverage is estimated from the footprints on a lattice of points over the area; the worker
  measures the real valid fraction after reading.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import numpy as np

from app.sar.stac import SarItem

#: The first day of the Sentinel-1 RTC archive.
ARCHIVE_START = date(2014, 10, 10)
#: The area sizes offered (km, square).
AOI_SIZES_KM = (5, 10, 20)
#: Frames of one pass start at most this far apart.
PASS_GAP_S = 120.0
#: Most frames read for one pass.
MAX_FRAMES_PER_PASS = 4
#: Passes estimated to cover less than this of the area are not offered.
MIN_COVERAGE_EST = 0.3


def coverage_estimate(footprint: dict, points: np.ndarray) -> np.ndarray:
    """Which of the lon/lat ``points`` (n x 2) fall inside a GeoJSON (Multi)Polygon footprint."""
    polygons = (
        [footprint["coordinates"]] if footprint["type"] == "Polygon" else footprint["coordinates"]
    )
    inside = np.zeros(len(points), dtype=bool)
    x = points[:, 0]
    y = points[:, 1]
    for polygon in polygons:
        ring = np.asarray(polygon[0], dtype=float)[:, :2]  # exterior ring; holes are ignored
        x0, y0 = ring[:-1, 0], ring[:-1, 1]
        x1, y1 = ring[1:, 0], ring[1:, 1]
        crosses = (y0[None, :] > y[:, None]) != (y1[None, :] > y[:, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = (
                x0[None, :] + (y[:, None] - y0[None, :]) * (x1 - x0)[None, :] / (y1 - y0)[None, :]
            )
        hits = crosses & (x[:, None] < x_at)
        inside |= (hits.sum(axis=1) % 2) == 1
    return inside


@dataclass(frozen=True)
class Pass:
    """One overflight: the frames that touch the area, and how much of it they cover."""

    frames: tuple[SarItem, ...]  #: frames touching the area, best-covering first
    coverage: float  #: estimated fraction of the area covered (0-1)

    @property
    def key(self) -> str:
        """The id of the pass: its first (best-covering) frame's STAC id."""
        return self.frames[0].stac_id

    @property
    def acquired_at(self) -> datetime:
        """When the pass's earliest frame was acquired."""
        return min(f.acquired_at for f in self.frames)

    @property
    def platform(self) -> str:
        """The satellite (normalised platform name)."""
        return self.frames[0].platform

    @property
    def track(self) -> tuple[int, str]:
        """(relative orbit, orbit direction)."""
        return self.frames[0].relative_orbit, self.frames[0].orbit_state

    @property
    def polarizations(self) -> tuple[str, ...]:
        """Polarizations every frame of the pass has."""
        common = set(self.frames[0].polarizations)
        for frame in self.frames[1:]:
            common &= set(frame.polarizations)
        return tuple(sorted(common))


def group_passes(items: list[SarItem], points: np.ndarray) -> list[Pass]:
    """The passes in ``items``: frames grouped, those not touching the area dropped."""
    by_track: dict[tuple[str, int, str], list[SarItem]] = {}
    for item in sorted(items, key=lambda i: i.acquired_at):
        by_track.setdefault((item.platform, item.relative_orbit, item.orbit_state), []).append(item)
    passes: list[Pass] = []
    for frames in by_track.values():
        run: list[SarItem] = []
        for frame in frames:
            if run and (frame.acquired_at - run[-1].acquired_at).total_seconds() > PASS_GAP_S:
                passes.extend(_as_pass(run, points))
                run = []
            run.append(frame)
        passes.extend(_as_pass(run, points))
    passes.sort(key=lambda p: p.acquired_at)
    return passes


def _as_pass(frames: list[SarItem], points: np.ndarray) -> list[Pass]:
    masks = [(frame, coverage_estimate(frame.footprint, points)) for frame in frames]
    touching = [(frame, mask) for frame, mask in masks if mask.any()]
    if not touching:
        return []
    touching.sort(key=lambda fm: -int(fm[1].sum()))
    touching = touching[:MAX_FRAMES_PER_PASS]
    union = np.zeros(len(points), dtype=bool)
    for _, mask in touching:
        union |= mask
    return [Pass(frames=tuple(f for f, _ in touching), coverage=float(union.mean()))]


@dataclass
class Track:
    """The passes of one track around each of the two dates."""

    relative_orbit: int  #: the relative orbit
    orbit_state: str  #: ascending or descending
    before: list[Pass] = field(default_factory=list)  #: around the "before" date, closest first
    after: list[Pass] = field(default_factory=list)  #: around the "after" date, closest first

    @property
    def key(self) -> str:
        """``"110-descending"``."""
        return f"{self.relative_orbit}-{self.orbit_state}"


@dataclass(frozen=True)
class Proposal:
    """The pair closest to the two dates, on one track."""

    track: str  #: the track's key
    before: Pass  #: the earlier pass
    after: Pass  #: the later pass


def _noon(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=UTC)


def build_tracks(
    before: list[Pass],
    after: list[Pass],
    *,
    before_date: date,
    after_date: date,
    polarization: str,
    platform: str | None,
) -> list[Track]:
    """Tracks with usable passes around both dates (polarization, platform, coverage filtered)."""

    def usable(p: Pass) -> bool:
        return (
            polarization in p.polarizations
            and p.coverage >= MIN_COVERAGE_EST
            and (platform is None or p.platform == platform)
        )

    tracks: dict[tuple[int, str], Track] = {}
    for p in filter(usable, before):
        tracks.setdefault(p.track, Track(*p.track)).before.append(p)
    for p in filter(usable, after):
        if p.track in tracks:
            tracks[p.track].after.append(p)
    result = [t for t in tracks.values() if t.after]
    for track in result:
        track.before.sort(key=lambda p: abs(p.acquired_at - _noon(before_date)))
        track.after.sort(key=lambda p: abs(p.acquired_at - _noon(after_date)))
    return result


def propose(tracks: list[Track], *, before_date: date, after_date: date) -> Proposal | None:
    """The pair (earlier, later) on one track closest to both dates, ties to better coverage."""
    best: tuple[float, Proposal] | None = None
    target_b, target_a = _noon(before_date), _noon(after_date)
    for track in tracks:
        for b in track.before:
            for a in track.after:
                if b.acquired_at >= a.acquired_at - timedelta(hours=1):
                    continue
                days = (abs(b.acquired_at - target_b) + abs(a.acquired_at - target_a)) / timedelta(
                    days=1
                )
                score = days + 0.5 * (2.0 - b.coverage - a.coverage)
                if best is None or score < best[0]:
                    best = (score, Proposal(track=track.key, before=b, after=a))
    return best[1] if best else None


def search_window(day: date, days: int, today: date) -> tuple[datetime, datetime]:
    """``day`` ± ``days``, clipped to the archive's start and to today (UTC datetimes)."""
    start = max(day - timedelta(days=days), ARCHIVE_START)
    end = min(day + timedelta(days=days), today)
    return (
        datetime.combine(start, time(0), tzinfo=UTC),
        datetime.combine(end, time(23, 59, 59), tzinfo=UTC),
    )

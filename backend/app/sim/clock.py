"""Accelerable simulation clock.

Simulated time advances at ``time_scale`` times wall time and can be paused or jumped. Every change
re-anchors the clock at the current wall instant, so sim time is continuous across rate changes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


def wall_now() -> datetime:
    """The wall clock, aware UTC (the default source; tests inject their own)."""
    return datetime.now(UTC)


@dataclass
class SimClock:
    """Sim time = ``sim_anchor`` + (wall time since ``wall_anchor``) * ``time_scale``.

    One per session. Every change re-anchors at the current wall instant, so rate changes never
    make sim time jump; ``jump_to`` is the one deliberate jump.
    """

    sim_anchor: datetime
    time_scale: float = 1.0
    paused: bool = False
    wall_source: Callable[[], datetime] = wall_now
    wall_anchor: datetime = field(init=False)

    def __post_init__(self) -> None:
        if self.sim_anchor.tzinfo is None:
            raise ValueError("sim_anchor must be timezone-aware (UTC)")
        self.wall_anchor = self.wall_source()

    def now(self) -> datetime:
        """Current simulated time (aware UTC)."""
        if self.paused:
            return self.sim_anchor
        elapsed_s = (self.wall_source() - self.wall_anchor).total_seconds()
        return self.sim_anchor + timedelta(seconds=elapsed_s * self.time_scale)

    def _reanchor(self) -> None:
        self.sim_anchor = self.now()
        self.wall_anchor = self.wall_source()

    def set_time_scale(self, time_scale: float) -> None:
        """Run at ``time_scale`` times real time from now on (positive; pause to stop)."""
        if time_scale <= 0:
            raise ValueError("time_scale must be positive; use pause() to stop the clock")
        self._reanchor()
        self.time_scale = time_scale

    def pause(self) -> None:
        """Freeze sim time at its current value."""
        self._reanchor()
        self.paused = True

    def resume(self) -> None:
        """Run again from where it was paused."""
        self._reanchor()
        self.paused = False

    def jump_to(self, sim_time: datetime) -> None:
        """Set sim time to ``sim_time`` (aware) and keep running from there."""
        if sim_time.tzinfo is None:
            raise ValueError("sim_time must be timezone-aware (UTC)")
        self.sim_anchor = sim_time.astimezone(UTC)
        self.wall_anchor = self.wall_source()

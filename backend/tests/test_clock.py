from datetime import UTC, datetime, timedelta

import pytest

from app.sim.clock import SimClock

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeWall:
    def __init__(self) -> None:
        self.now = datetime(2030, 6, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def wall() -> FakeWall:
    return FakeWall()


def test_realtime(wall: FakeWall) -> None:
    clock = SimClock(T0, wall_source=wall)
    wall.advance(10)
    assert clock.now() == T0 + timedelta(seconds=10)


def test_scaled_and_continuous_across_rate_change(wall: FakeWall) -> None:
    clock = SimClock(T0, time_scale=60, wall_source=wall)
    wall.advance(1)
    assert clock.now() == T0 + timedelta(minutes=1)
    clock.set_time_scale(1)
    assert clock.now() == T0 + timedelta(minutes=1)  # no jump at the rate change
    wall.advance(1)
    assert clock.now() == T0 + timedelta(minutes=1, seconds=1)


def test_pause_resume(wall: FakeWall) -> None:
    clock = SimClock(T0, time_scale=10, wall_source=wall)
    wall.advance(1)
    clock.pause()
    wall.advance(100)
    assert clock.now() == T0 + timedelta(seconds=10)
    clock.resume()
    wall.advance(1)
    assert clock.now() == T0 + timedelta(seconds=20)


def test_jump(wall: FakeWall) -> None:
    clock = SimClock(T0, time_scale=2, wall_source=wall)
    target = datetime(2026, 5, 5, 12, tzinfo=UTC)
    clock.jump_to(target)
    wall.advance(3)
    assert clock.now() == target + timedelta(seconds=6)


def test_rejects_naive_and_nonpositive(wall: FakeWall) -> None:
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 1, 1), wall_source=wall)
    clock = SimClock(T0, wall_source=wall)
    with pytest.raises(ValueError):
        clock.set_time_scale(0)

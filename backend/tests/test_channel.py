"""The downlink channel model: anchored, monotonic, deterministic per packet."""

import pytest

from app.sim.channel import (
    E_REF_DB,
    REFERENCE_BER,
    ber_for_margin,
    downlink,
    light_time_s,
    packet_rng,
)

FRAME = bytes(range(64))  # 512 bits


def test_ber_is_anchored_at_zero_margin() -> None:
    assert round(E_REF_DB, 2) == 9.59  # uncoded BPSK at 1e-5
    assert ber_for_margin(0.0) == pytest.approx(REFERENCE_BER, rel=1e-6)


def test_ber_falls_as_the_margin_grows() -> None:
    margins = [-6, -3, 0, 3, 6]
    bers = [ber_for_margin(m) for m in margins]
    assert bers == sorted(bers, reverse=True)
    assert ber_for_margin(6.0) < 1e-12  # green: effectively clean
    assert ber_for_margin(-3.0) > 1e-3


def test_light_time() -> None:
    assert light_time_s(1_000_000) == pytest.approx(3.336e-3, rel=1e-3)  # LEO, ~3 ms
    assert light_time_s(35_786_000) == pytest.approx(0.1194, rel=1e-3)  # GEO, ~120 ms


def test_same_packet_same_corruption() -> None:
    a = downlink(FRAME, -4.0, modelled=True, rng=packet_rng(7, 42))
    b = downlink(FRAME, -4.0, modelled=True, rng=packet_rng(7, 42))
    c = downlink(FRAME, -4.0, modelled=True, rng=packet_rng(7, 43))
    assert a == b
    assert a.bit_errors is not None and a.bit_errors > 0
    assert sum(bin(x ^ y).count("1") for x, y in zip(a.frame, FRAME, strict=True)) == a.bit_errors
    assert c.frame != a.frame


def test_corruption_follows_the_margin() -> None:
    def corrupted(margin: float) -> int:
        return sum(
            downlink(FRAME, margin, modelled=True, rng=packet_rng(1, i)).bit_errors != 0
            for i in range(200)
        )

    assert corrupted(8.0) == 0
    assert corrupted(-4.0) > 150


def test_no_line_of_sight_and_unmodelled() -> None:
    lost = downlink(FRAME, None, modelled=True, rng=packet_rng(1, 1))
    assert not lost.delivered
    clean = downlink(FRAME, -20.0, modelled=False, rng=packet_rng(1, 1))
    assert clean.delivered and clean.frame == FRAME and clean.bit_errors is None

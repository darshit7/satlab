"""The downlink channel: what happens to a frame between the satellite and this station.

Real: whether there is line of sight, and the one-way light time (slant range / c). Modelled, and
labelled so: bit errors from the link margin. Only satellites with a published link budget have a
margin; for the others the channel isn't modelled and frames arrive as sent (a replayed SatNOGS
frame was already decoded by a real station).

    BER(margin) = Q(√(2 · Eb/N0)),  Eb/N0 = E_ref + margin  (dB)

the uncoded-BPSK curve, anchored so that a margin of 0 dB, the published requirement, gives
``REFERENCE_BER``. Real coded links fall off a steeper cliff below their threshold; this gentler
curve makes the degradation near the horizon visible instead of abrupt. Bit flips are drawn from a
generator seeded by (session, packet), so the same packet is corrupted the same way on replay.
"""

import math
from dataclasses import dataclass

import numpy as np

from app.sim.linkbudget import C_M_S

REFERENCE_BER = 1e-5


def _eb_n0_for(ber: float) -> float:
    """Eb/N0 (dB) at which uncoded BPSK reaches ``ber`` (bisection; monotonic)."""
    lo, hi = -10.0, 30.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if bpsk_ber(mid) > ber:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bpsk_ber(eb_n0_db: float) -> float:
    """Q(√(2·Eb/N0)) = ½·erfc(√(Eb/N0))."""
    return 0.5 * math.erfc(math.sqrt(10 ** (eb_n0_db / 10)))


E_REF_DB = _eb_n0_for(REFERENCE_BER)  # ≈ 9.59 dB


def ber_for_margin(margin_db: float) -> float:
    return bpsk_ber(E_REF_DB + margin_db)


def light_time_s(range_m: float) -> float:
    return range_m / C_M_S


def packet_rng(session_id: int, sequence: int) -> np.random.Generator:
    return np.random.default_rng([session_id, sequence])


@dataclass(frozen=True)
class Delivery:
    delivered: bool  # False: no line of sight
    frame: bytes  # as received (possibly corrupted)
    bit_errors: int | None  # None: channel not modelled
    ber: float | None


def downlink(
    frame: bytes, margin_db: float | None, *, modelled: bool, rng: np.random.Generator
) -> Delivery:
    """Send ``frame`` down. ``margin_db`` None means no line of sight (below the horizon)."""
    if not modelled:
        return Delivery(True, frame, None, None)
    if margin_db is None or not math.isfinite(margin_db):
        return Delivery(False, b"", None, None)
    ber = ber_for_margin(margin_db)
    n_bits = 8 * len(frame)
    flips = int(rng.binomial(n_bits, ber)) if n_bits else 0
    if flips == 0:
        return Delivery(True, frame, 0, ber)
    data = bytearray(frame)
    for bit in rng.choice(n_bits, size=flips, replace=False):
        data[int(bit) // 8] ^= 0x80 >> (int(bit) % 8)
    return Delivery(True, bytes(data), flips, ber)

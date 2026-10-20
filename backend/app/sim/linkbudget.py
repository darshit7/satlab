"""Link budget: geometry → received Eb/N0, margin over the required value, Doppler and RAG.

One computation serves every view (AGENTS.md golden rule 4): the snapshot's ``link``, the per-point
colours of a pass arc and the RF subsystem all come from ``LinkTable.evaluate``.

    Eb/N0 = EIRP(θ) - FSPL(d, f) - L_atm(el) - L_sys + G/T - 10·log10(k) - 10·log10(R)
    margin = Eb/N0 - required Eb/N0

``θ`` is the station's angle off the satellite's nadir (where it sits in the antenna pattern,
assuming a nadir-pointing satellite), ``d`` the slant range, ``L_atm`` the atmospheric and rain
loss the source quotes at one elevation, scaled along the slant path by the cosecant law (ITU-R
P.618's slant-path form), ``L_sys`` every other loss the source budget lists, ``G/T`` the
reference receiving station's figure of merit and ``R`` the bit rate the source's Eb/N0 refers to.
Only satellites with a published budget have a profile (``app/data/seed/link_profiles.json``);
nothing here invents RF parameters.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

C_M_S = 299_792_458.0
BOLTZMANN_DBW_HZ_K = 10 * math.log10(1.380649e-23)  # -228.60 dBW/(Hz·K)

GREEN, AMBER, RED = 0, 1, 2
RAG_NAMES = ("green", "amber", "red")

Floats = NDArray[np.float64]
Ints = NDArray[np.intp]

# Strong-link bands are found on this elevation grid (°).
_BAND_STEP_DEG = 0.25
# The cosecant law is held below this elevation (between the horizon and a low mask).
_MIN_ATM_ELEV_DEG = 2.0


@dataclass(frozen=True)
class RagThresholds:
    """Margin (dB) → RAG: green strictly above ``green_above_db``, amber from ``amber_from_db``."""

    green_above_db: float = 6.0
    amber_from_db: float = 0.0


@dataclass(frozen=True)
class LinkProfile:
    """A satellite's downlink as its published budget describes it."""

    norad_id: int
    label: str
    frequency_hz: float
    eirp_dbw: float  # with a pattern: EIRP where the pattern offset is 0 dB
    station_gt_db_k: float
    system_loss_db: float
    required_ebn0_db: float
    data_rate_bps: float
    # (off-nadir °, EIRP offset dB), ascending; empty = the same EIRP in every direction
    eirp_pattern: tuple[tuple[float, float], ...] = ()
    coverage_half_angle_deg: float | None = None
    # quoted at ``atmospheric_ref_elev_deg``; scaled by sin(ref) / sin(el)
    atmospheric_loss_db: float = 0.0
    atmospheric_ref_elev_deg: float = 90.0

    @property
    def fixed_db(self) -> float:
        """Every term of the margin except the pattern offset and the free-space loss."""
        return (
            self.eirp_dbw
            - self.system_loss_db
            + self.station_gt_db_k
            - BOLTZMANN_DBW_HZ_K
            - 10 * math.log10(self.data_rate_bps)
            - self.required_ebn0_db
        )

    def pattern_db(self, off_nadir_deg: Floats) -> Floats:
        if not self.eirp_pattern:
            return np.zeros_like(off_nadir_deg)
        angles, offsets = zip(*self.eirp_pattern, strict=True)
        # beyond the last sample the pattern is held (clamped), as np.interp does
        return np.interp(off_nadir_deg, angles, offsets)

    def atmospheric_db(self, elevation_deg: Floats) -> Floats:
        el = np.radians(np.maximum(np.asarray(elevation_deg, dtype=np.float64), _MIN_ATM_ELEV_DEG))
        scale = math.sin(math.radians(self.atmospheric_ref_elev_deg)) / np.sin(el)
        return self.atmospheric_loss_db * scale

    def margin_db(self, range_m: Floats, off_nadir_deg: Floats, elevation_deg: Floats) -> Floats:
        return (
            self.fixed_db
            + self.pattern_db(off_nadir_deg)
            - fspl_db(range_m, self.frequency_hz)
            - self.atmospheric_db(elevation_deg)
        )


def fspl_db(range_m: Floats | float, frequency_hz: float) -> Floats:
    """Free-space path loss, 20·log10(4π·d·f / c)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20 * np.log10(4 * math.pi * np.asarray(range_m) * frequency_hz / C_M_S)


def doppler_hz(range_rate_m_s: Floats | float, frequency_hz: float) -> Floats:
    """Received minus transmitted frequency: positive while the satellite approaches."""
    return -np.asarray(range_rate_m_s) * frequency_hz / C_M_S


def rag_codes(
    margin_db: Floats, elevation_deg: Floats, mask_deg: float, thresholds: RagThresholds
) -> NDArray[np.int8]:
    """Canonical mapping: red below the mask (or with no margin), green above the green
    threshold, amber in between (inclusive at both ends)."""
    margin = np.asarray(margin_db, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        rag = np.where(margin > thresholds.green_above_db, GREEN, AMBER)
        rag = np.where(
            ~np.isfinite(margin)
            | (margin < thresholds.amber_from_db)
            | ~(np.asarray(elevation_deg) >= mask_deg),
            RED,
            rag,
        )
    return rag.astype(np.int8)


@dataclass(frozen=True)
class LinkEval:
    """Per-sample results; ``ebn0_db`` / ``margin_db`` are NaN without line of sight."""

    ebn0_db: Floats
    margin_db: Floats
    doppler_hz: Floats
    rag: NDArray[np.int8]


def strong_link_bands(
    profile: LinkProfile,
    sat_radius_m: float,
    station_radius_m: float,
    mask_deg: float,
    thresholds: RagThresholds,
) -> list[tuple[float, float]]:
    """Elevation bands (°) where the margin is green for a satellite at this distance from the
    Earth's centre: the inner "strong-link" region of the sky.

    Spherical geometry around the station's geocentric radius, on a 0.25° grid with linearly
    interpolated edges. An omni-directional link gives one band reaching the zenith; an isoflux
    pattern can give several, or none.
    """
    if not (math.isfinite(sat_radius_m) and sat_radius_m > station_radius_m > 0):
        return []
    el = np.arange(max(mask_deg, 0.0), 90.0 + 1e-9, _BAND_STEP_DEG)
    el_rad = np.radians(el)
    cos_el = np.cos(el_rad)
    range_m = -station_radius_m * np.sin(el_rad) + np.sqrt(
        sat_radius_m**2 - (station_radius_m * cos_el) ** 2
    )
    off_nadir = np.degrees(np.arcsin(np.clip(station_radius_m * cos_el / sat_radius_m, -1, 1)))
    excess = profile.margin_db(range_m, off_nadir, el) - thresholds.green_above_db
    good = excess > 0
    if not good.any():
        return []

    def edge(i: int) -> float:
        """Elevation of the zero crossing between samples i and i + 1."""
        a, b = excess[i], excess[i + 1]
        return float(el[i] + (el[i + 1] - el[i]) * a / (a - b))

    bands: list[tuple[float, float]] = []
    start: float | None = float(el[0]) if good[0] else None
    for i in range(len(el) - 1):
        if good[i] != good[i + 1]:
            if good[i + 1]:
                start = edge(i)
            else:
                assert start is not None
                bands.append((start, edge(i)))
                start = None
    if start is not None:
        bands.append((start, float(el[-1])))
    return [(round(lo, 1), round(hi, 1)) for lo, hi in bands]


@dataclass(frozen=True, eq=False)
class LinkTable:
    """Every link profile, indexed by NORAD ID and aligned with the fleet's rows.

    Immutable: rebuilt with the fleet after each catalog ingest and swapped in with it.
    """

    profiles: tuple[LinkProfile, ...]
    by_norad: Mapping[int, int]
    version: int
    # per fleet row: index into ``profiles`` or -1
    profile_of_row: Ints = field(repr=False)

    @classmethod
    def build(
        cls, profiles: Iterable[LinkProfile], fleet_index: Mapping[int, int], fleet_size: int
    ) -> "LinkTable":
        kept = tuple(profiles)
        by_norad = {p.norad_id: i for i, p in enumerate(kept)}
        profile_of_row = np.full(fleet_size, -1, dtype=np.intp)
        for norad_id, i in by_norad.items():
            row = fleet_index.get(norad_id)
            if row is not None:
                profile_of_row[row] = i
        version = hash(kept)  # same profiles → same version, so caches keyed on it survive
        return cls(kept, by_norad, version, profile_of_row)

    @classmethod
    def empty(cls, fleet_size: int = 0) -> "LinkTable":
        return cls.build((), {}, fleet_size)

    def __len__(self) -> int:
        return len(self.profiles)

    @property
    def rows(self) -> Ints:
        """Fleet rows that have a profile."""
        return np.flatnonzero(self.profile_of_row >= 0)

    @property
    def has_profile(self) -> NDArray[np.bool_]:
        """Per fleet row."""
        return self.profile_of_row >= 0

    def evaluate(
        self,
        idx: Sequence[int] | Ints,
        *,
        range_m: Floats,
        range_rate_m_s: Floats,
        elevation_deg: Floats,
        off_nadir_deg: Floats,
        mask_deg: float,
        thresholds: RagThresholds,
    ) -> LinkEval:
        """Evaluate sample ``i`` with profile ``idx[i]`` (all arrays 1-D and the same length)."""
        idx = np.asarray(idx, dtype=np.intp)
        range_m = np.asarray(range_m, dtype=np.float64)
        n = len(idx)
        margin = np.full(n, np.nan)
        doppler = np.full(n, np.nan)
        offset = np.full(n, np.nan)  # Eb/N0 - margin = required Eb/N0
        for p in np.unique(idx):
            profile = self.profiles[int(p)]
            sel = idx == p
            margin[sel] = profile.margin_db(
                range_m[sel],
                np.asarray(off_nadir_deg)[sel],
                np.asarray(elevation_deg, dtype=np.float64)[sel],
            )
            doppler[sel] = doppler_hz(np.asarray(range_rate_m_s)[sel], profile.frequency_hz)
            offset[sel] = profile.required_ebn0_db
        el = np.asarray(elevation_deg, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            margin = np.where(el >= 0, margin, np.nan)  # no line of sight below the horizon
        return LinkEval(
            ebn0_db=margin + offset,
            margin_db=margin,
            doppler_hz=doppler,
            rag=rag_codes(margin, el, mask_deg, thresholds),
        )

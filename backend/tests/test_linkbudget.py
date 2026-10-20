"""Link budget against known values and the sources' own worked budgets; RAG thresholds."""

import math

import numpy as np
import pytest

from app.data.seed import LINK_PROFILES_FILE, LinkProfilesFile, link_profile_specs
from app.sim.linkbudget import (
    AMBER,
    GREEN,
    RAG_NAMES,
    RED,
    LinkProfile,
    LinkTable,
    RagThresholds,
    doppler_hz,
    fspl_db,
    rag_codes,
    strong_link_bands,
)

EARTH_M = 6_378_137.0
THRESHOLDS = RagThresholds(green_above_db=6.0, amber_from_db=0.0)


def _omni(fixed_db: float = 0.0, **overrides: float) -> LinkProfile:
    """An omni-directional UHF link whose margin is ``fixed_db`` minus the path loss."""
    profile = LinkProfile(
        norad_id=1,
        label="test",
        frequency_hz=437e6,
        eirp_dbw=0.0,
        station_gt_db_k=0.0,
        system_loss_db=0.0,
        required_ebn0_db=0.0,
        data_rate_bps=1.0,
    )
    # shift the EIRP so that fixed_db comes out as asked
    return LinkProfile(**{**profile.__dict__, "eirp_dbw": fixed_db - profile.fixed_db, **overrides})


def test_free_space_path_loss() -> None:
    assert fspl_db(1_000_000.0, 145.8e6) == pytest.approx(135.72, abs=0.01)
    assert fspl_db(35_786_000.0, 1694.1e6) == pytest.approx(188.10, abs=0.01)
    # +6 dB per doubling of distance
    assert fspl_db(2e6, 1e9) - fspl_db(1e6, 1e9) == pytest.approx(20 * math.log10(2))


def test_doppler_sign_and_size() -> None:
    # approaching at 7 km/s on 145.8 MHz: the received frequency is 3.4 kHz high
    assert doppler_hz(-7000.0, 145.8e6) == pytest.approx(3404.4, abs=0.1)
    assert doppler_hz(7000.0, 145.8e6) == pytest.approx(-3404.4, abs=0.1)
    assert doppler_hz(0.0, 145.8e6) == 0


@pytest.mark.parametrize(
    ("margin_db", "elevation_deg", "expected"),
    [
        (10.0, 30.0, "green"),
        (6.01, 30.0, "green"),
        (6.0, 30.0, "amber"),  # green is strictly above the threshold
        (3.0, 30.0, "amber"),
        (0.0, 30.0, "amber"),  # amber includes its lower edge
        (-0.01, 30.0, "red"),
        (10.0, 4.99, "red"),  # below the horizon mask, whatever the margin
        (math.nan, 30.0, "red"),  # no margin (no line of sight)
    ],
)
def test_rag_thresholds(margin_db: float, elevation_deg: float, expected: str) -> None:
    [code] = rag_codes(np.array([margin_db]), np.array([elevation_deg]), 5.0, THRESHOLDS)
    assert RAG_NAMES[code] == expected


def test_atmospheric_loss_scales_with_slant_path() -> None:
    p = _omni(atmospheric_loss_db=3.0, atmospheric_ref_elev_deg=5.0)
    assert p.atmospheric_db(np.array([5.0]))[0] == pytest.approx(3.0)
    assert p.atmospheric_db(np.array([90.0]))[0] == pytest.approx(3.0 * math.sin(math.radians(5)))
    # held below 2° so the loss stays finite between the horizon and a low mask
    assert p.atmospheric_db(np.array([0.0]))[0] == p.atmospheric_db(np.array([2.0]))[0]


def test_strong_link_bands_omni() -> None:
    sat_r = EARTH_M + 500_000
    # FSPL at 437 MHz: ~139.2 dB at 500 km (zenith) to ~153 dB at the horizon
    profile = _omni(fixed_db=150.0)
    [(lo, hi)] = strong_link_bands(profile, sat_r, EARTH_M, 5.0, THRESHOLDS)
    assert hi == 90.0
    assert 5.0 < lo < 90.0
    # at the band edge the margin is the green threshold
    el = math.radians(lo)
    rng = -EARTH_M * math.sin(el) + math.sqrt(sat_r**2 - (EARTH_M * math.cos(el)) ** 2)
    margin = profile.margin_db(np.array([rng]), np.array([0.0]), np.array([lo]))[0]
    assert margin == pytest.approx(6.0, abs=0.05)

    assert strong_link_bands(_omni(fixed_db=100.0), sat_r, EARTH_M, 5.0, THRESHOLDS) == []
    assert strong_link_bands(_omni(fixed_db=200.0), sat_r, EARTH_M, 5.0, THRESHOLDS) == [
        (5.0, 90.0)
    ]


def test_strong_link_bands_follow_the_pattern() -> None:
    # A pattern strong only near the edge of coverage (low elevations) gives a band that stops
    # short of the zenith.
    profile = _omni(fixed_db=145.0, eirp_pattern=((0.0, -20.0), (50.0, -20.0), (66.0, 15.0)))
    bands = strong_link_bands(profile, EARTH_M + 500_000, EARTH_M, 5.0, THRESHOLDS)
    assert bands and bands[0][0] == 5.0 and bands[-1][1] < 90.0


def test_link_table_aligns_with_fleet_rows() -> None:
    a, b = _omni(10.0), LinkProfile(**{**_omni(20.0).__dict__, "norad_id": 2})
    table = LinkTable.build([a, b], {2: 0, 99: 1, 1: 3}, fleet_size=4)
    assert table.by_norad == {1: 0, 2: 1}
    assert table.profile_of_row.tolist() == [1, -1, -1, 0]
    assert table.rows.tolist() == [0, 3]
    assert table.has_profile.tolist() == [True, False, False, True]
    assert LinkTable.build([a, b], {}, 0).version == table.version  # same profiles, same version

    ev = table.evaluate(
        [0, 1, 0],
        range_m=np.array([1e6, 1e6, 1e6]),
        range_rate_m_s=np.array([-1000.0, 0.0, 1000.0]),
        elevation_deg=np.array([30.0, 30.0, -1.0]),
        off_nadir_deg=np.array([10.0, 10.0, 10.0]),
        mask_deg=5.0,
        thresholds=THRESHOLDS,
    )
    fspl = float(fspl_db(1e6, 437e6))
    assert ev.margin_db[0] == pytest.approx(10.0 - fspl)
    assert ev.margin_db[1] == pytest.approx(20.0 - fspl)
    assert math.isnan(ev.margin_db[2])  # below the horizon: no line of sight
    assert ev.rag[2] == RED
    assert ev.doppler_hz[0] > 0 > ev.doppler_hz[2]


def test_rag_codes_constants() -> None:
    assert (GREEN, AMBER, RED) == (0, 1, 2)
    assert RAG_NAMES == ("green", "amber", "red")


# ---------- the shipped profiles ----------


def test_shipped_profiles_are_valid_and_sourced() -> None:
    spec = LinkProfilesFile.model_validate_json(LINK_PROFILES_FILE.read_bytes())
    assert spec.profiles, "at least one published profile"
    for p in spec.profiles:
        assert p.sources and all(s.url.startswith("https://") for s in p.sources)
        assert p.checks, f"{p.satellite_name}: no worked margin from its source to check against"


@pytest.mark.parametrize(
    "spec", link_profile_specs().profiles, ids=lambda s: s.satellite_name.replace(" ", "_")
)
def test_profile_reproduces_its_source(spec) -> None:
    profile = spec.to_model()
    for check in spec.checks:
        margin = profile.margin_db(
            np.array([check.range_km * 1000]),
            np.array([check.off_nadir_deg]),
            np.array([check.elevation_deg]),
        )[0]
        assert margin == pytest.approx(check.margin_db, abs=0.1), check.where

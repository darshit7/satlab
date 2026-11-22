"""The SAR area grid (UTM), pass grouping, track pairing and the file cache."""

import os
import time
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.sar.grid import (
    AoiGrid,
    AoiOutOfRange,
    aoi_grid,
    bbox_lonlat,
    from_utm,
    lattice_lonlat,
    polygon_lonlat,
    to_utm,
    utm_epsg,
)
from app.sar.pairs import (
    build_tracks,
    coverage_estimate,
    group_passes,
    propose,
    search_window,
)
from app.sar.stac import parse_items
from app.sar.storage import SarStorage
from tests.sar_helpers import ACCOUNT, CONTAINER, collection, item, square

VALENCIA = (39.415, -0.425)

# ---------- grid ----------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(0.0, 3.0), VALENCIA, (60.0, 25.0), (-45.0, 170.0), (39.4, -5.999), (-33.9, 18.4)],
)
def test_utm_matches_proj(lat, lon):
    rasterio_warp = pytest.importorskip("rasterio.warp")
    epsg = utm_epsg(lat, lon)
    x, y = to_utm(lat, lon, epsg)
    (xs,), (ys,) = rasterio_warp.transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    assert abs(x - xs) < 1e-3 and abs(y - ys) < 1e-3
    back_lat, back_lon = from_utm(x, y, epsg)
    assert abs(back_lat - lat) < 1e-9 and abs(back_lon - lon) < 1e-9


def test_utm_zones():
    assert utm_epsg(*VALENCIA) == 32630
    assert utm_epsg(-33.9, 18.4) == 32734
    assert utm_epsg(10.0, 180.0) == 32660


def test_aoi_grid_is_deterministic_and_snapped():
    grid = aoi_grid(*VALENCIA, 10)
    assert grid == aoi_grid(*VALENCIA, 10)
    assert (grid.width, grid.height) == (1000, 1000)
    assert grid.x_min_m % 10 == 0 and grid.y_max_m % 10 == 0
    assert aoi_grid(*VALENCIA, 20).width == 2000
    assert AoiGrid.from_dict(grid.to_dict()) == grid
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat(grid)
    assert min_lon < VALENCIA[1] < max_lon and min_lat < VALENCIA[0] < max_lat
    ring = polygon_lonlat(grid)
    assert ring[0] == ring[-1] and len(ring) == 33
    assert len(lattice_lonlat(grid)) == 64


@pytest.mark.parametrize(
    ("lat", "lon"), [(85.0, 0.0), (-81.0, 0.0), (0.0, 179.99), (10.0, -179.99)]
)
def test_aoi_grid_refuses(lat, lon):
    with pytest.raises(AoiOutOfRange):
        aoi_grid(lat, lon, 20)


# ---------- passes and pairs ----------

T_BEFORE = datetime(2024, 10, 20, 6, 2, 7, tzinfo=UTC)
T_AFTER = datetime(2024, 11, 1, 6, 2, 7, tzinfo=UTC)


def _items(*raw):
    items, skipped = parse_items(collection(*raw), account=ACCOUNT, container=CONTAINER)
    assert skipped == 0
    return items


def _points():
    return np.array(lattice_lonlat(aoi_grid(*VALENCIA, 10)))


def test_coverage_estimate():
    points = _points()
    assert coverage_estimate(square(-0.425, 39.415, 1.0), points).mean() == 1.0
    assert coverage_estimate(square(10.0, 10.0, 1.0), points).mean() == 0.0
    half = {
        "type": "Polygon",
        "coordinates": [[[-0.425, 38], [2, 38], [2, 41], [-0.425, 41], [-0.425, 38]]],
    }
    assert 0.3 < coverage_estimate(half, points).mean() < 0.7


def test_frames_of_one_pass_are_mosaicked():
    north = square(-0.425, 40.0, 0.6)  # covers the AOI's north edge only
    south = square(-0.425, 39.2, 0.25)  # covers its south part
    far = square(-0.425, 42.0, 0.3)  # misses the AOI
    items = _items(
        item("S1A_N", T_AFTER, footprint=north),
        item("S1A_S", T_AFTER + timedelta(seconds=25), footprint=south),
        item("S1A_FAR", T_AFTER + timedelta(seconds=50), footprint=far),
        item("S1A_LATER", T_AFTER + timedelta(days=6)),
    )
    passes = group_passes(items, _points())
    assert len(passes) == 2
    first = passes[0]
    assert {f.stac_id for f in first.frames} == {"S1A_N", "S1A_S"}
    assert first.coverage > max(
        coverage_estimate(north, _points()).mean(), coverage_estimate(south, _points()).mean()
    )


def _valencia_tracks(platform=None, polarization="VV"):
    before = _items(
        item("S1A_8_B", datetime(2024, 10, 13, 6, 10, tzinfo=UTC), orbit=8),
        item("S1A_30_B", datetime(2024, 10, 14, 17, 55, tzinfo=UTC), orbit=30, state="ascending"),
        item("S1A_103_B", datetime(2024, 10, 19, 18, 3, tzinfo=UTC), orbit=103, state="ascending"),
        item("S1A_110_B", datetime(2024, 10, 20, 6, 2, tzinfo=UTC)),
        item("S1A_8_B2", datetime(2024, 10, 25, 6, 10, tzinfo=UTC), orbit=8),
        item("S1A_30_B2", datetime(2024, 10, 26, 17, 55, tzinfo=UTC), orbit=30, state="ascending"),
    )
    after = _items(
        item("S1A_8_A0", datetime(2024, 10, 25, 6, 10, tzinfo=UTC), orbit=8),
        item("S1A_30_A0", datetime(2024, 10, 26, 17, 55, tzinfo=UTC), orbit=30, state="ascending"),
        item("S1A_110_A", datetime(2024, 11, 1, 6, 2, tzinfo=UTC)),
        item("S1A_8_A", datetime(2024, 11, 6, 6, 10, tzinfo=UTC), orbit=8),
        item("S1A_30_A", datetime(2024, 11, 7, 17, 55, tzinfo=UTC), orbit=30, state="ascending"),
    )
    points = _points()
    return build_tracks(
        group_passes(before, points),
        group_passes(after, points),
        before_date=date(2024, 10, 20),
        after_date=date(2024, 11, 1),
        polarization=polarization,
        platform=platform,
    )


def test_proposal_is_the_same_track_pair_closest_to_both_dates():
    tracks = _valencia_tracks()
    assert {t.key for t in tracks} == {"8-descending", "30-ascending", "110-descending"}
    proposal = propose(tracks, before_date=date(2024, 10, 20), after_date=date(2024, 11, 1))
    assert proposal is not None
    assert proposal.track == "110-descending"
    assert (proposal.before.key, proposal.after.key) == ("S1A_110_B", "S1A_110_A")
    for track in tracks:
        assert all(
            p.track == (track.relative_orbit, track.orbit_state) for p in track.before + track.after
        )


def test_pairs_need_before_earlier_than_after():
    tracks = _valencia_tracks()
    eight = next(t for t in tracks if t.key == "8-descending")
    proposal = propose([eight], before_date=date(2024, 10, 20), after_date=date(2024, 11, 1))
    assert proposal is not None and proposal.before.acquired_at < proposal.after.acquired_at


def test_platform_and_polarization_filters():
    assert _valencia_tracks(platform="sentinel-1d") == []
    assert _valencia_tracks(polarization="HH") == []


def test_cross_platform_pairs_on_one_track():
    before = _items(item("S1C_B", T_BEFORE, platform="sentinel-1c"))
    after = _items(item("S1D_A", T_AFTER, platform="sentinel-1d"))
    points = _points()
    tracks = build_tracks(
        group_passes(before, points),
        group_passes(after, points),
        before_date=T_BEFORE.date(),
        after_date=T_AFTER.date(),
        polarization="VV",
        platform=None,
    )
    proposal = propose(tracks, before_date=T_BEFORE.date(), after_date=T_AFTER.date())
    assert proposal is not None and proposal.before.platform != proposal.after.platform


def test_search_window_is_clipped():
    start, end = search_window(date(2014, 10, 12), 12, date(2026, 11, 22))
    assert start.date() == date(2014, 10, 10)
    start, end = search_window(date(2026, 11, 20), 12, date(2026, 11, 22))
    assert end.date() == date(2026, 11, 22)


# ---------- storage ----------


def test_storage_paths_and_guard(tmp_path):
    storage = SarStorage(tmp_path / "sar", 10_000)
    crop = storage.crop_path("S1A_X", "VV", "32630:1:2:10:10x10")
    assert crop.parent == storage.root / "crops" and crop.suffix == ".tif"
    assert (
        storage.product(7, 2, "change").relative_to(storage.root).as_posix()
        == "comparisons/7/2/change.png"
    )
    with pytest.raises(ValueError):
        storage.product(7, 2, "../../etc")  # type: ignore[arg-type]
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage.root / "crops" / "link.tif"
    link.symlink_to(outside / "x.tif")
    with pytest.raises(ValueError):
        storage.guard(link)


def test_storage_lru_eviction(tmp_path):
    storage = SarStorage(tmp_path / "sar", 3000)
    now = time.time()
    for cid, age in ((1, 300), (2, 200), (3, 100)):
        run = storage.products_dir(cid, 1)
        run.mkdir(parents=True)
        for kind in ("before", "after", "change"):
            (run / f"{kind}.png").write_bytes(b"x" * 400)
            os.utime(run / f"{kind}.png", (now - age, now - age))
        os.utime(run, (now - age, now - age))
    assert storage.usage() == 3600
    assert storage.has_products(1, 1)
    pinned = {storage.products_dir(1, 1)}
    evicted = storage.evict(pinned=pinned)
    assert evicted == {2}
    assert storage.has_products(1, 1) and not storage.has_products(2, 1)
    assert storage.remove_orphans({1}) == 2  # 2's empty parent, and 3
    (storage.root / "crops" / "x.tif.tmp.tif").write_bytes(b"half")
    storage.remove_tmp()
    assert not list(storage.root.rglob("*.tmp*"))

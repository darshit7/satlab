"""v1 change detection, rendering and bounded raster reads (worker-side modules)."""

import numpy as np
import pytest

pytest.importorskip("scipy")
rasterio = pytest.importorskip("rasterio")

from app.sar.detect import (  # noqa: E402
    NoCoverage,
    detect_changes,
    lee_filter,
    otsu_threshold,
    to_db,
)
from app.sar.grid import aoi_grid  # noqa: E402
from app.sar.params import DetectionParams, change_detection_params  # noqa: E402
from app.sar.protocol import SourceLimits, gdal_options  # noqa: E402
from app.sar.raster import InvalidRaster, read_crop, read_into_grid, write_crop  # noqa: E402
from app.sar.render import change_codes, stretch_u8, write_png  # noqa: E402
from app.sar.worker import redact  # noqa: E402
from tests.sar_helpers import speckle, write_cog  # noqa: E402

PARAMS = change_detection_params()
SHAPE = (300, 300)
SIGMA0 = 0.05  # -13 dB, typical of fields in VV
PIXEL_M2 = 100.0

# ---------- Otsu and Lee ----------


def test_otsu_splits_two_equal_modes_at_the_midpoint():
    rng = np.random.default_rng(1)
    values = np.concatenate([rng.normal(2.0, 0.4, 50_000), rng.normal(8.0, 0.4, 50_000)])
    t = otsu_threshold(values, 256, (0.0, 10.0))
    assert t is not None and abs(t - 5.0) < 0.3


def test_otsu_with_unequal_weights_stays_between_modes():
    rng = np.random.default_rng(2)
    values = np.concatenate([rng.normal(1.0, 0.3, 90_000), rng.normal(7.0, 0.3, 10_000)])
    t = otsu_threshold(values, 256, (0.0, 10.0))
    assert t is not None and 2.0 < t < 6.0


def test_otsu_edge_cases():
    assert otsu_threshold(np.array([]), 256, (0.0, 10.0)) is None
    assert otsu_threshold(np.array([np.nan, np.nan]), 256, (0.0, 10.0)) is None
    assert otsu_threshold(np.full(100, 3.0), 256, (0.0, 10.0)) == 3.0


def test_lee_reduces_speckle_and_keeps_nan_local():
    image = speckle(SHAPE, SIGMA0, 4.4, seed=3)
    filtered = lee_filter(image, 7, 4.4)
    assert abs(np.nanmean(filtered) / SIGMA0 - 1.0) < 0.02
    assert np.nanvar(filtered) < np.var(image) / 4
    holed = image.copy()
    holed[100:110, 100:110] = np.nan
    out = lee_filter(holed, 7, 4.4)
    assert np.isnan(out[100:110, 100:110]).all()
    assert np.isfinite(out[95:100, 100:110]).all()  # neighbours aren't poisoned


def test_to_db():
    out = to_db(np.array([1.0, 0.1, 0.0, -1.0, np.nan], dtype=np.float32))
    assert out[0] == 0.0 and abs(out[1] + 10.0) < 1e-5 and np.isnan(out[2:]).all()


# ---------- detection ----------


@pytest.mark.parametrize("seed", range(5))
def test_no_change_flags_nothing(seed):
    before = speckle(SHAPE, SIGMA0, 4.4, seed=seed)
    after = speckle(SHAPE, SIGMA0, 4.4, seed=seed + 100)
    result = detect_changes(before, after, PARAMS, PIXEL_M2)
    assert result.regions == []
    assert result.threshold_db >= PARAMS.threshold.min_abs_db
    assert result.changed_fraction == 0.0 and not result.widespread


def test_planted_changes_are_found_with_their_sign():
    field = np.full(SHAPE, SIGMA0, dtype=np.float32)
    changed = field.copy()
    changed[40:80, 40:80] *= 10 ** (8 / 10)  # +8 dB block: new structure
    changed[150:180, 100:160] *= 10 ** (-7 / 10)  # -7 dB block: flooding
    before = speckle(SHAPE, field, 4.4, seed=7)
    after = speckle(SHAPE, changed, 4.4, seed=8)
    result = detect_changes(before, after, PARAMS, PIXEL_M2)
    assert len(result.regions) == 2
    signs = {r.sign: r for r in result.regions}
    up, down = signs["increase"], signs["decrease"]
    assert abs(up.mean_db - 8.0) < 1.5 and abs(down.mean_db + 7.0) < 1.5
    assert abs(up.area_m2 - 40 * 40 * PIXEL_M2) / (40 * 40 * PIXEL_M2) < 0.2
    assert abs(down.area_m2 - 30 * 60 * PIXEL_M2) / (30 * 60 * PIXEL_M2) < 0.2
    assert 35 <= up.col0 <= 45 and 75 <= up.col1 <= 85
    assert result.kept_increase[60, 60] and result.kept_decrease[165, 130]
    assert not result.kept_increase[250, 250]


def test_floor_beats_a_low_otsu():
    before = speckle(SHAPE, SIGMA0, 4.4, seed=11)
    changed = np.full(SHAPE, SIGMA0, dtype=np.float32)
    changed[:, :150] *= 10 ** (1.5 / 10)  # a 1.5 dB shift: under the 3 dB floor
    after = speckle(SHAPE, changed, 4.4, seed=12)
    result = detect_changes(before, after, PARAMS, PIXEL_M2)
    assert result.threshold_db >= 3.0
    assert result.changed_fraction < 0.01


def test_widespread_and_truncation():
    field = np.full(SHAPE, SIGMA0, dtype=np.float32)
    after_field = field.copy()
    for i in range(6):
        for j in range(6):
            after_field[10 + i * 48 : 40 + i * 48, 10 + j * 48 : 40 + j * 48] *= 10.0
    before = speckle(SHAPE, field, 4.4, seed=21)
    after = speckle(SHAPE, after_field, 4.4, seed=22)
    params = PARAMS.model_copy(
        update={"regions": PARAMS.regions.model_copy(update={"max_regions": 10})}
    )
    result = detect_changes(before, after, params, PIXEL_M2)
    assert len(result.regions) == 10 and result.regions_truncated
    assert result.widespread


def test_no_coverage():
    before = speckle(SHAPE, SIGMA0, 4.4, seed=31)
    after = before.copy()
    after[:, :200] = np.nan
    with pytest.raises(NoCoverage) as caught:
        detect_changes(before, after, PARAMS, PIXEL_M2)
    assert caught.value.coverage < 0.5


def test_params_are_strict():
    raw = PARAMS.model_dump(by_alias=True)
    raw["speckle"]["window_px"] = 6
    with pytest.raises(ValueError):
        DetectionParams.model_validate(raw)
    raw = PARAMS.model_dump(by_alias=True)
    raw["surprise"] = 1
    with pytest.raises(ValueError):
        DetectionParams.model_validate(raw)


# ---------- rendering ----------


def test_stretch_comes_from_params_only():
    a = np.array([[-25.0, -10.0], [5.0, np.nan]], dtype=np.float32)
    grey, alpha = stretch_u8(a, -25.0, 5.0)
    assert grey.tolist() == [[1, 128], [255, 0]] and alpha.tolist() == [[255, 255], [255, 0]]
    grey2, _ = stretch_u8(np.where(np.isnan(a), -1.0, a), -25.0, 5.0)
    assert grey2[0, 0] == grey[0, 0]  # the same value draws the same, whatever else is in the image


def test_change_codes():
    delta = np.array([[np.nan, 0.5, -3.0, 10.0, 20.0]], dtype=np.float32)
    inc = np.array([[False, False, False, True, True]])
    dec = np.array([[False, False, True, False, False]])
    codes = change_codes(delta, inc, dec, threshold_db=3.0, max_db=10.0)
    assert codes.tolist() == [[0, 1, 2, 254, 254]]


def test_png_round_trip_without_sidecars(tmp_path):
    band = (np.arange(64, dtype=np.uint8).reshape(8, 8) * 3).astype(np.uint8)
    path = tmp_path / "change.png"
    write_png(path, [band])
    with rasterio.open(path) as src:
        assert src.driver == "PNG" and np.array_equal(src.read(1), band)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["change.png"]


# ---------- raster reads ----------

GRID = aoi_grid(39.415, -0.425, 5)  # 500 x 500 px in EPSG:32630
LIMITS = SourceLimits(max_window_px=int(500 * 1.25) + 16)


def _scene(tmp_path, name="scene.tif", epsg=32630, offset_px=(50, 50), size=800, **kw):
    data = speckle((size, size), SIGMA0, 4.4, seed=5)
    data[:20, :] = -32768.0  # a nodata strip
    x_min = GRID.x_min_m - offset_px[0] * 10
    y_max = GRID.y_max_m + offset_px[1] * 10
    if epsg != 32630:
        from rasterio.warp import transform

        (x_min,), (y_max,) = transform("EPSG:32630", f"EPSG:{epsg}", [x_min], [y_max])
    path = tmp_path / name
    write_cog(path, data, epsg=epsg, x_min=x_min, y_max=y_max, **kw)
    return path, data


def test_window_read_onto_the_same_grid(tmp_path):
    path, data = _scene(tmp_path)
    out = read_into_grid(str(path), GRID, expected_epsg=32630, limits=LIMITS)
    assert out.shape == (500, 500) and out.dtype == np.float32
    assert np.allclose(out[100:400, 100:400], data[150:450, 150:450], rtol=1e-4)


def test_nodata_and_partial_overlap_are_nan(tmp_path):
    path, _ = _scene(tmp_path, offset_px=(-300, 50))  # the scene starts 300 px into the grid
    out = read_into_grid(str(path), GRID, expected_epsg=32630, limits=LIMITS)
    assert np.isnan(out[:, :290]).all() and np.isfinite(out[100:200, 320:400]).all()
    path2, _ = _scene(tmp_path, name="strip.tif", offset_px=(50, 0))
    out2 = read_into_grid(str(path2), GRID, expected_epsg=32630, limits=LIMITS)
    assert np.isnan(out2[:18, :]).all()


def test_a_neighbouring_zone_is_reprojected(tmp_path):
    path, _ = _scene(tmp_path, name="z31.tif", epsg=32631)
    out = read_into_grid(str(path), GRID, expected_epsg=32631, limits=LIMITS)
    assert np.isfinite(out).mean() > 0.8
    assert abs(np.nanmean(out) / SIGMA0 - 1.0) < 0.05


@pytest.mark.parametrize(
    ("kwargs", "expected_epsg", "limits"),
    [
        ({"dtype": "int16"}, 32630, LIMITS),
        ({"count": 2}, 32630, LIMITS),
        ({}, 32631, LIMITS),
        ({}, 32630, SourceLimits(max_window_px=100)),
        ({"res": 20.0}, 32630, LIMITS),
    ],
)
def test_unexpected_files_are_refused(tmp_path, kwargs, expected_epsg, limits):
    path, _ = _scene(tmp_path, **kwargs)
    with pytest.raises(InvalidRaster):
        read_into_grid(str(path), GRID, expected_epsg=expected_epsg, limits=limits)


def test_only_the_gtiff_driver_opens(tmp_path):
    path = tmp_path / "x.png"
    write_png(path, [np.zeros((8, 8), dtype=np.uint8)])
    with pytest.raises(rasterio.errors.RasterioIOError):
        read_into_grid(str(path), GRID, expected_epsg=32630, limits=LIMITS)


def test_crop_round_trip_and_tag_mismatch(tmp_path):
    data = speckle((GRID.height, GRID.width), SIGMA0, 4.4, seed=9)
    data[0, 0] = np.nan
    path = tmp_path / "crop.tif"
    tags = {"stac_id": "S1A_X", "polarization": "VV", "grid": GRID.key()}
    write_crop(path, data, GRID, tags)
    back = read_crop(path, GRID, tags)
    assert back is not None and np.allclose(back[1:], data[1:]) and np.isnan(back[0, 0])
    assert read_crop(path, GRID, {**tags, "stac_id": "S1A_Y"}) is None
    assert read_crop(path, aoi_grid(39.5, -0.425, 5), tags) is None
    assert read_crop(tmp_path / "missing.tif", GRID, tags) is None


def test_gdal_options_and_redaction():
    options = gdal_options(
        account="acc", sas_token="st=1&se=2&sig=SECRET", read_timeout_s=30, cache_mb=64
    )
    assert options["AZURE_STORAGE_SAS_TOKEN"] == "st=1&se=2&sig=SECRET"
    assert options["CPL_DEBUG"] == "OFF" and options["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
    text = "GET https://acc.blob.core.windows.net/c/a.tiff?st=1&se=2&sig=SECRET failed; sig=SECRET"
    assert "SECRET" not in redact(text)

"""Bounded reads of a Sentinel-1 RTC scene onto the area's grid, and the crop cache (worker only).

The amended raster rule (AGENTS.md §12.4): GDAL opens only paths the server built
(``/vsiaz/<container>/<blob>`` on the allow-listed account, or a crop under the storage root),
with the GTiff driver only. Before any pixel is read the file is checked (one float32 band, the
CRS STAC announced, ~10 m pixels, sane dimensions and tiles), and the source window for the area
is computed and bounded. That window is read, reprojected onto the grid and saved as a crop;
everything after opens the crop from storage.

The SAS token travels only in the GDAL options (:func:`app.sar.protocol.gdal_options`), never in
a path.
"""

import math
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

from app.sar.grid import AoiGrid
from app.sar.protocol import SourceLimits

#: Bumped when the crop format changes (stored as a tag; a mismatch is a cache miss).
READER_VERSION = "1"
#: The products' nodata value in the source files.
SOURCE_NODATA = -32768.0


class InvalidRaster(Exception):
    """The file isn't what a Sentinel-1 RTC band should be, or the window is too large."""


def _check_source(src: rasterio.io.DatasetReader, expected_epsg: int, limits: SourceLimits) -> None:
    if src.count != 1 or src.dtypes[0] != "float32":
        raise InvalidRaster("not a single float32 band")
    if max(src.width, src.height) > limits.max_dataset_px:
        raise InvalidRaster("dataset too large")
    if src.crs is None or src.crs.to_epsg() != expected_epsg:
        raise InvalidRaster("unexpected CRS")
    res_x, res_y = src.res
    if abs(res_x - 10.0) > 0.5 or abs(res_y - 10.0) > 0.5:
        raise InvalidRaster("unexpected pixel size")
    if src.nodata is not None and src.nodata != SOURCE_NODATA:
        raise InvalidRaster("unexpected nodata")
    block_h, block_w = src.block_shapes[0]
    if max(block_h, block_w) > limits.max_block_px:
        raise InvalidRaster("unexpected tiling")


def read_into_grid(
    path: str, grid: AoiGrid, *, expected_epsg: int, limits: SourceLimits
) -> np.ndarray:
    """The scene's linear backscatter on ``grid`` (float32; NaN where no data or outside).

    Call inside ``rasterio.Env(**gdal_options(...))`` for remote paths.
    """
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    grid_crs = f"EPSG:{grid.epsg}"
    x_min = grid.x_min_m
    y_max = grid.y_max_m
    x_max = x_min + grid.width * grid.resolution_m
    y_min = y_max - grid.height * grid.resolution_m
    with rasterio.open(path, driver="GTiff") as src:
        _check_source(src, expected_epsg, limits)
        left, bottom, right, top = transform_bounds(
            grid_crs, src.crs, x_min, y_min, x_max, y_max, densify_pts=21
        )
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        col0 = max(math.floor(window.col_off) - 2, 0)
        row0 = max(math.floor(window.row_off) - 2, 0)
        col1 = min(math.ceil(window.col_off + window.width) + 2, src.width)
        row1 = min(math.ceil(window.row_off + window.height) + 2, src.height)
        if col1 <= col0 or row1 <= row0:
            return out
        if max(col1 - col0, row1 - row0) > limits.max_window_px:
            raise InvalidRaster("source window too large")
        window = Window(col0, row0, col1 - col0, row1 - row0)
        data = src.read(1, window=window, out_dtype="float32")
        source_transform = src.window_transform(window)
        source_crs = src.crs
    data[(data == SOURCE_NODATA) | ~np.isfinite(data) | (data <= 0)] = np.nan
    reproject(
        source=data,
        destination=out,
        src_transform=source_transform,
        src_crs=source_crs,
        src_nodata=np.nan,
        dst_transform=rasterio.Affine(*grid.transform),
        dst_crs=grid_crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return out


def write_crop(path: Path, data: np.ndarray, grid: AoiGrid, tags: dict[str, str]) -> None:
    """Save a crop (tiled, compressed GTiff, NaN nodata), atomically."""
    tmp = path.with_name(path.name + ".tmp.tif")
    with (
        rasterio.Env(GDAL_PAM_ENABLED="NO"),
        rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=1,
            dtype="float32",
            crs=f"EPSG:{grid.epsg}",
            transform=rasterio.Affine(*grid.transform),
            nodata=np.nan,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="DEFLATE",
            predictor=3,
        ) as dst,
    ):
        dst.write(data.astype(np.float32), 1)
        dst.update_tags(reader_version=READER_VERSION, **tags)
    os.replace(tmp, path)


def read_crop(path: Path, grid: AoiGrid, tags: dict[str, str]) -> np.ndarray | None:
    """A cached crop, if it exists and matches the grid and tags; else None (a cache miss)."""
    if not path.is_file():
        return None
    try:
        with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(path, driver="GTiff") as src:
            stored = src.tags()
            if (
                src.count != 1
                or src.dtypes[0] != "float32"
                or (src.height, src.width) != (grid.height, grid.width)
                or src.crs is None
                or src.crs.to_epsg() != grid.epsg
                or tuple(round(v, 6) for v in tuple(src.transform)[:6])
                != tuple(round(v, 6) for v in grid.transform)
                or stored.get("reader_version") != READER_VERSION
                or any(stored.get(k) != v for k, v in tags.items())
            ):
                return None
            return src.read(1, out_dtype="float32")
    except rasterio.errors.RasterioError:
        return None

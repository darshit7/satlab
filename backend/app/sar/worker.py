"""The SAR worker process: reads both passes onto the grid, detects changes, writes the images.

It runs in a separate, lower-priority process (spawned per comparison), so raster I/O and the
numpy/scipy work never hold the GIL of the process running the simulation loop, and its memory
goes back to the system when the job ends. Only stdlib and :mod:`app.sar.protocol` are imported
at module level; rasterio, scipy and numpy load inside :func:`run_comparison`.

Secrets: the SAS token arrives in ``job.gdal`` and lives only in GDAL's config. Inherited
``AZURE_*``/``GDAL_*``/``CPL_*``/``AWS_*`` variables are removed first, log records are redacted,
and failures go back as error codes, never as exception text.
"""

import contextlib
import logging
import os
import re
import resource
import time
from pathlib import Path
from typing import Any

from app.sar.protocol import Stage, WorkerFrame, WorkerJob, WorkerResult

log = logging.getLogger(__name__)

#: SAS query parameters masked in any log line.
_SECRET_RE = re.compile(r"\b(sig|se|st|skoid|sktid|skt|ske|sks|skv|sv|sp|sr)=[^&\s'\"]+")
#: A blob URL's query string, masked as a whole.
_BLOB_QUERY_RE = re.compile(r"(blob\.core\.windows\.net/[^?\s'\"]*)\?[^\s'\"]+")
#: Variable prefixes removed from the worker's environment before anything loads.
_SCRUBBED_PREFIXES = ("AZURE_", "GDAL_", "CPL_", "AWS_", "CURL_", "VSI_")


def redact(text: str) -> str:
    """``text`` with SAS parameters and blob query strings masked."""
    return _SECRET_RE.sub(r"\1=…", _BLOB_QUERY_RE.sub(r"\1?…", text))


class _RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class _NoControl:
    """Stand-in for the shared values when the job runs in a thread (tests)."""

    class _Value:
        value = 0

    stage = _Value()
    pid = _Value()


_control: Any = _NoControl()


def init_worker(nice: int, control: Any) -> None:
    """Initializer of the worker process: lower priority, clean environment, redacted logs."""
    global _control
    with contextlib.suppress(OSError):
        os.nice(nice)
    for name in list(os.environ):
        if name.startswith(_SCRUBBED_PREFIXES):
            del os.environ[name]
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    logging.getLogger().addFilter(_RedactSecrets())
    logging.getLogger("rasterio").setLevel(logging.WARNING)
    _control = control


def _set_stage(stage: Stage) -> None:
    _control.stage.value = int(stage)


def _inside(path: str, root: Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("path outside the SAR storage root")
    return resolved


def _read_pass(frames: tuple[WorkerFrame, ...], job: WorkerJob, grid: Any, root: Path) -> Any:
    import numpy as np
    import rasterio

    from app.sar.raster import read_crop, read_into_grid, write_crop

    mosaic = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    for frame in frames:
        crop_path = _inside(frame.crop_path, root)
        tags = {"stac_id": frame.stac_id, "polarization": job.polarization, "grid": grid.key()}
        data = read_crop(crop_path, grid, tags)
        if data is None:
            if frame.path.startswith("/vsiaz/"):
                source = frame.path
            else:
                source = str(_inside(frame.path, root))
            with rasterio.Env(**job.gdal):
                data = read_into_grid(
                    source, grid, expected_epsg=frame.expected_epsg, limits=job.limits
                )
            write_crop(crop_path, data, grid, tags)
        mosaic = np.where(np.isnan(mosaic), data, mosaic)
    return mosaic


def run_comparison(job: WorkerJob) -> WorkerResult:
    """Run one comparison end to end; every failure comes back as an error code."""
    from app.sar.detect import NoCoverage, detect_changes
    from app.sar.grid import AoiGrid, box_polygon
    from app.sar.params import DetectionParams
    from app.sar.raster import InvalidRaster
    from app.sar.render import change_codes, stretch_u8, write_png

    _control.pid.value = os.getpid()
    timings: dict[str, float] = {}
    started = time.monotonic()
    try:
        root = Path(job.storage_root).resolve()
        grid = AoiGrid.from_dict(job.grid)
        params = DetectionParams.model_validate(job.params)
        _set_stage(Stage.READING)
        before = _read_pass(job.before, job, grid, root)
        after = _read_pass(job.after, job, grid, root)
        timings["read"] = round(time.monotonic() - started, 2)
        _set_stage(Stage.DETECTING)
        t0 = time.monotonic()
        pixel_area = grid.resolution_m**2
        detection = detect_changes(before, after, params, pixel_area)
        del before, after
        timings["detect"] = round(time.monotonic() - t0, 2)
        _set_stage(Stage.RENDERING)
        t0 = time.monotonic()
        final = _inside(job.products_dir, root)
        tmp = final.with_name(final.name + ".tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        stretch = params.stretch(job.polarization)
        write_png(
            tmp / "before.png",
            list(stretch_u8(detection.before_db, stretch.db_min, stretch.db_max)),
        )
        write_png(
            tmp / "after.png", list(stretch_u8(detection.after_db, stretch.db_min, stretch.db_max))
        )
        codes = change_codes(
            detection.delta_db,
            detection.kept_increase,
            detection.kept_decrease,
            detection.threshold_db,
            params.display.heatmap_max_db,
        )
        write_png(tmp / "change.png", [codes])
        os.replace(tmp, final)
        timings["render"] = round(time.monotonic() - t0, 2)
        regions = []
        for i, r in enumerate(detection.regions, start=1):
            polygon = box_polygon(grid, r.col0, r.row0, r.col1, r.row1, per_side=2)
            lons = [p[0] for p in polygon]
            lats = [p[1] for p in polygon]
            centroid = grid.lonlat(r.centroid_col + 0.5, r.centroid_row + 0.5)
            regions.append(
                {
                    "id": i,
                    "sign": r.sign,
                    "area_m2": round(r.area_m2, 1),
                    "mean_db": round(r.mean_db, 2),
                    "max_abs_db": round(r.max_abs_db, 2),
                    "polygon": polygon,
                    "bbox": (min(lons), min(lats), max(lons), max(lats)),
                    "centroid": (round(centroid[0], 6), round(centroid[1], 6)),
                    "px": (r.col0, r.row0, r.col1, r.row1),
                }
            )
        return WorkerResult(
            ok=True,
            coverage=round(detection.coverage, 4),
            otsu_db=None if detection.otsu_db is None else round(detection.otsu_db, 2),
            threshold_db=round(detection.threshold_db, 2),
            changed_fraction=round(detection.changed_fraction, 5),
            increase_area_m2=round(detection.increase_area_m2, 1),
            decrease_area_m2=round(detection.decrease_area_m2, 1),
            regions=regions,
            regions_truncated=detection.regions_truncated,
            widespread=detection.widespread,
            timings_s=timings,
            peak_rss_mb=_peak_rss_mb(),
        )
    except NoCoverage as exc:
        return WorkerResult(ok=False, error_code="no_coverage", coverage=round(exc.coverage, 4))
    except InvalidRaster as exc:
        log.warning("comparison %d: invalid raster (%s)", job.comparison_id, redact(str(exc)))
        return WorkerResult(ok=False, error_code="invalid_raster", timings_s=timings)
    except Exception as exc:  # GDAL/HTTP failures; never echoed to the client
        log.warning(
            "comparison %d: read failed (%s: %s)",
            job.comparison_id,
            type(exc).__name__,
            redact(str(exc))[:200],
        )
        return WorkerResult(ok=False, error_code="read_failed", timings_s=timings)
    finally:
        _set_stage(Stage.IDLE)


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)

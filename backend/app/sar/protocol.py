"""What the API process and the SAR worker process exchange (stdlib only, imported by both).

A :class:`WorkerJob` goes to the worker, a :class:`WorkerResult` comes back; both are pickled
across the process boundary. Errors come back as a closed set of codes with fixed messages, never
as exception text, so nothing from GDAL (which could hold a URL) reaches a client.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

#: Why a comparison failed (a closed set; the client sees :data:`ERROR_MESSAGES`).
ErrorCode = Literal[
    "no_coverage",
    "read_failed",
    "invalid_raster",
    "timeout",
    "worker_crashed",
    "session_ended",
    "interrupted",
    "token_failed",
]

#: The message shown for each error code.
ERROR_MESSAGES: dict[str, str] = {
    "no_coverage": "The scenes don't cover enough of the area on both dates. "
    "Try another track or move the area.",
    "read_failed": "The archive couldn't be read just now. Try again in a few minutes.",
    "invalid_raster": "A scene file wasn't in the expected format, so it wasn't read.",
    "timeout": "Reading the scenes took too long. Try a smaller area, or again later.",
    "worker_crashed": "The imagery worker stopped unexpectedly. Try again; "
    "a smaller area needs less memory.",
    "session_ended": "Cancelled: the session ended.",
    "interrupted": "Interrupted by a server restart. Recompute to run it again.",
    "token_failed": "Couldn't get access to the archive just now. Try again in a few minutes.",
}


class Stage(IntEnum):
    """Where a running comparison is (shared with the parent via a ``multiprocessing.Value``)."""

    IDLE = 0  #: not started
    READING = 1  #: reading scene windows (or the cache)
    DETECTING = 2  #: filtering, ratio, threshold, regions
    RENDERING = 3  #: writing the images


@dataclass(frozen=True)
class SourceLimits:
    """Bounds checked on a scene file before any pixel is read (AGENTS.md §12.4)."""

    max_dataset_px: int = 60_000  #: either side of the whole scene
    max_window_px: int = 2_600  #: either side of the source window read for the area
    max_block_px: int = 1_024  #: either side of a COG tile


@dataclass(frozen=True)
class WorkerFrame:
    """One scene file of a pass to read (or take from the crop cache)."""

    stac_id: str  #: the STAC item id
    path: str  #: ``/vsiaz/<container>/<blob>``, or a local file under the storage root (tests)
    expected_epsg: int  #: the scene's CRS as STAC says; the file must agree
    crop_path: str  #: where its crop for this grid is cached


@dataclass
class WorkerJob:
    """Everything the worker needs for one comparison."""

    comparison_id: int  #: the comparison
    generation: int  #: its run number (product directory)
    grid: dict  #: :meth:`app.sar.grid.AoiGrid.to_dict`
    polarization: str  #: VV or VH
    before: tuple[WorkerFrame, ...]  #: frames of the earlier pass
    after: tuple[WorkerFrame, ...]  #: frames of the later pass
    products_dir: str  #: where the three images go
    storage_root: str  #: every path must be inside this
    params: dict  #: :class:`app.sar.params.DetectionParams` as a dict
    limits: SourceLimits = field(default_factory=SourceLimits)  #: source checks
    gdal: dict = field(default_factory=dict, repr=False)  #: GDAL options; holds the SAS token


@dataclass
class WorkerResult:
    """What a comparison produced (or why it didn't)."""

    ok: bool  #: products written
    error_code: str | None = None  #: one of :data:`ErrorCode` when not ok
    coverage: float | None = None  #: valid on both dates (0-1)
    otsu_db: float | None = None  #: Otsu's threshold on ``|Δ dB|``
    threshold_db: float | None = None  #: the threshold applied: max(Otsu, floor)
    changed_fraction: float | None = None  #: flagged share of the valid pixels
    increase_area_m2: float | None = None  #: flagged brighter area
    decrease_area_m2: float | None = None  #: flagged darker area
    regions: list[dict] = field(default_factory=list)  #: :class:`app.models.api.SarRegionOut` dicts
    regions_truncated: bool = False  #: more regions than listed
    widespread: bool = False  #: changed fraction above the caution level
    timings_s: dict = field(default_factory=dict)  #: seconds per stage
    peak_rss_mb: float | None = None  #: the worker's peak memory


def gdal_options(
    *, account: str, sas_token: str, read_timeout_s: float, cache_mb: int
) -> dict[str, str | int]:
    """GDAL config for reading scene files: the account and token here, never in a path."""
    options: dict[str, str | int] = {
        "AZURE_STORAGE_ACCOUNT": account,
        "AZURE_STORAGE_SAS_TOKEN": sas_token,
        "AZURE_NO_SIGN_REQUEST": "NO",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        "GDAL_HTTP_TIMEOUT": str(int(read_timeout_s)),
        "GDAL_HTTP_CONNECTTIMEOUT": "10",
        "GDAL_HTTP_MAX_RETRY": "2",
        "GDAL_HTTP_RETRY_DELAY": "1",
        "GDAL_HTTP_MULTIPLEX": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "VSI_CACHE": "TRUE",
        "GDAL_CACHEMAX": int(cache_mb),
        "GDAL_NUM_THREADS": "1",
        "CPL_DEBUG": "OFF",
        "CPL_CURL_VERBOSE": "NO",
        "GDAL_PAM_ENABLED": "NO",
    }
    try:
        import certifi

        options["CURL_CA_BUNDLE"] = certifi.where()
    except ImportError:  # pragma: no cover - certifi comes with rasterio
        pass
    return options

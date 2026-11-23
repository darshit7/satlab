"""v1 change detection between two co-registered SAR images (numpy + scipy.ndimage; worker only).

Explainable on purpose (design doc §9.4):

1. Lee-filter each date's linear backscatter (speckle reduction), ignoring nodata.
2. Log-ratio in dB: Δ = 10·log10(after / before).
3. Threshold ``|Δ|`` with Otsu's method, never below a floor (``min_abs_db``), so a pair
   without real change doesn't get its noise split in two and flagged.
4. Open and close each sign's mask (isolated pixels out, pinholes in).
5. Label 8-connected regions, drop those below ``min_area_m2``, rank by area x ``|mean Δ|``.

The result marks where the radar return changed, not what changed: soil moisture, crops, snow or
wind on water change it too. The honesty copy says so.
"""

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from app.sar.params import DetectionParams

#: 8-connectivity for labelling and morphology.
_STRUCTURE = np.ones((3, 3), dtype=bool)


class NoCoverage(Exception):
    """Too little of the area is valid on both dates."""

    def __init__(self, coverage: float) -> None:
        super().__init__(f"coverage {coverage:.2f}")
        self.coverage = coverage


def lee_filter(x: np.ndarray, window_px: int, enl: float) -> np.ndarray:
    """Lee-filtered linear backscatter; NaN (and ≤ 0) stays NaN, not bleeding nearby."""
    valid = np.isfinite(x) & (x > 0)
    values = np.where(valid, x, 0.0).astype(np.float32)
    weight = valid.astype(np.float32)
    count = ndimage.uniform_filter(weight, size=window_px, mode="reflect")
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = ndimage.uniform_filter(values, size=window_px, mode="reflect") / count
        mean_sq = ndimage.uniform_filter(values * values, size=window_px, mode="reflect") / count
        variance = np.maximum(mean_sq - mean * mean, 0.0)
        cu2 = 1.0 / enl
        k = (variance - mean * mean * cu2) / (variance * (1.0 + cu2))
    k = np.clip(np.nan_to_num(k, nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0)
    out = mean + k * (values - mean)
    return np.where(valid, out, np.nan).astype(np.float32)


def to_db(x: np.ndarray) -> np.ndarray:
    """10·log10(x); NaN where x is not finite or not positive."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10.0 * np.log10(x)
    return np.where(np.isfinite(out), out, np.nan).astype(np.float32)


def otsu_threshold(values: np.ndarray, bins: int, value_range: tuple[float, float]) -> float | None:
    """Otsu's threshold of ``values`` over ``value_range``: the split maximising between-class
    variance. None for no values; the value itself when they are all equal."""
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    hist, edges = np.histogram(values, bins=bins, range=value_range)
    centers = (edges[:-1] + edges[1:]) / 2
    weight0 = np.cumsum(hist).astype(np.float64)
    weight1 = weight0[-1] - weight0
    moment = np.cumsum(hist * centers)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean0 = moment / weight0
        mean1 = (moment[-1] - moment) / weight1
        between = weight0 * weight1 * (mean0 - mean1) ** 2
    between[~np.isfinite(between)] = -1.0
    best = between.max()
    if best <= 0:
        return float(values.mean())
    # Well-separated classes leave a plateau across the empty gap: take its middle, not its edge.
    plateau = np.flatnonzero(between >= best * (1 - 1e-9))
    return float(edges[int(plateau[len(plateau) // 2]) + 1])


@dataclass
class Region:
    """One flagged region, in grid pixels."""

    sign: str  #: "increase" or "decrease"
    pixels: int  #: flagged pixels
    area_m2: float  #: their area
    mean_db: float  #: mean Δ over the region
    max_abs_db: float  #: largest ``|Δ|`` in it
    col0: int  #: left edge (pixel)
    row0: int  #: top edge
    col1: int  #: right edge (exclusive)
    row1: int  #: bottom edge (exclusive)
    centroid_col: float  #: centre of mass (pixel)
    centroid_row: float  #: centre of mass (pixel)


@dataclass
class Detection:
    """The detector's output for one pair."""

    before_db: np.ndarray  #: filtered before image, dB (for display)
    after_db: np.ndarray  #: filtered after image, dB
    delta_db: np.ndarray  #: Δ dB, NaN where not valid on both
    kept_increase: np.ndarray  #: flagged brighter pixels (in a reported-size region)
    kept_decrease: np.ndarray  #: flagged darker pixels
    coverage: float  #: valid on both dates (0-1)
    otsu_db: float | None  #: Otsu's threshold on ``|Δ|``
    threshold_db: float  #: the applied threshold
    changed_fraction: float  #: flagged share of valid pixels
    increase_area_m2: float  #: flagged brighter area
    decrease_area_m2: float  #: flagged darker area
    regions: list[Region]  #: largest-scoring first, at most ``max_regions``
    regions_truncated: bool  #: more regions than listed
    widespread: bool  #: changed fraction above the caution level


def _clean(mask: np.ndarray, valid: np.ndarray, params: DetectionParams) -> np.ndarray:
    m = params.morphology
    if m.opening_iter:
        mask = ndimage.binary_opening(mask, structure=_STRUCTURE, iterations=m.opening_iter)
    if m.closing_iter:
        mask = ndimage.binary_closing(mask, structure=_STRUCTURE, iterations=m.closing_iter)
    return mask & valid


def _regions(
    mask: np.ndarray, delta: np.ndarray, sign: str, min_px: int, pixel_area_m2: float
) -> tuple[np.ndarray, list[Region]]:
    labels, count = ndimage.label(mask, structure=_STRUCTURE)
    if count == 0:
        return np.zeros_like(mask), []
    sizes = np.bincount(labels.ravel())
    keep_ids = [i for i in range(1, count + 1) if sizes[i] >= min_px]
    if not keep_ids:
        return np.zeros_like(mask), []
    kept = np.isin(labels, keep_ids)
    slices = ndimage.find_objects(labels)
    means = ndimage.mean(delta, labels, keep_ids)
    maxima = ndimage.maximum(np.abs(np.nan_to_num(delta)), labels, keep_ids)
    centres = ndimage.center_of_mass(mask, labels, keep_ids)
    regions = []
    for i, label_id in enumerate(keep_ids):
        rows, cols = slices[label_id - 1]
        regions.append(
            Region(
                sign=sign,
                pixels=int(sizes[label_id]),
                area_m2=float(sizes[label_id] * pixel_area_m2),
                mean_db=float(means[i]),
                max_abs_db=float(maxima[i]),
                col0=int(cols.start),
                row0=int(rows.start),
                col1=int(cols.stop),
                row1=int(rows.stop),
                centroid_col=float(centres[i][1]),
                centroid_row=float(centres[i][0]),
            )
        )
    return kept, regions


def detect_changes(
    before_lin: np.ndarray, after_lin: np.ndarray, params: DetectionParams, pixel_area_m2: float
) -> Detection:
    """Flag changed regions between two linear-backscatter images on the same grid."""
    valid = np.isfinite(before_lin) & (before_lin > 0) & np.isfinite(after_lin) & (after_lin > 0)
    coverage = float(valid.mean()) if valid.size else 0.0
    if coverage < params.coverage.min_valid_fraction:
        raise NoCoverage(coverage)
    speckle = params.speckle
    before_db = to_db(lee_filter(before_lin, speckle.window_px, speckle.enl))
    after_db = to_db(lee_filter(after_lin, speckle.window_px, speckle.enl))
    delta = np.where(valid, after_db - before_db, np.nan).astype(np.float32)
    t = params.threshold
    magnitude = np.minimum(np.abs(delta[valid]), t.clip_db)
    otsu = otsu_threshold(magnitude, t.bins, (0.0, t.clip_db))
    threshold = max(otsu if otsu is not None else 0.0, t.min_abs_db)
    with np.errstate(invalid="ignore"):
        increase = _clean(valid & (delta >= threshold), valid, params)
        decrease = _clean(valid & (delta <= -threshold), valid, params)
    min_px = max(int(np.ceil(params.regions.min_area_m2 / pixel_area_m2)), 1)
    kept_inc, regions_inc = _regions(increase, delta, "increase", min_px, pixel_area_m2)
    kept_dec, regions_dec = _regions(decrease, delta, "decrease", min_px, pixel_area_m2)
    regions = sorted(
        regions_inc + regions_dec, key=lambda r: r.area_m2 * abs(r.mean_db), reverse=True
    )
    valid_count = int(valid.sum())
    changed = int(kept_inc.sum() + kept_dec.sum())
    changed_fraction = changed / valid_count if valid_count else 0.0
    max_regions = params.regions.max_regions
    return Detection(
        before_db=before_db,
        after_db=after_db,
        delta_db=delta,
        kept_increase=kept_inc,
        kept_decrease=kept_dec,
        coverage=coverage,
        otsu_db=otsu,
        threshold_db=float(threshold),
        changed_fraction=changed_fraction,
        increase_area_m2=float(kept_inc.sum() * pixel_area_m2),
        decrease_area_m2=float(kept_dec.sum() * pixel_area_m2),
        regions=regions[:max_regions],
        regions_truncated=len(regions) > max_regions,
        widespread=changed_fraction > params.coverage.widespread_fraction,
    )

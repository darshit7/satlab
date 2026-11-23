"""The change-detection parameters, from ``app/data/seed/sar_change_detection.json``.

Thresholds are configuration, not code (AGENTS.md §6): the seed file is validated strictly
(unknown keys are errors) and carries a version, stored with every comparison it produced.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.seed import SEED_DIR

#: The seed file.
CHANGE_DETECTION_FILE = SEED_DIR / "sar_change_detection.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpeckleParams(_Strict):
    """Speckle filter applied to each date before the ratio."""

    method: Literal["lee"]  #: the Lee filter
    window_px: int = Field(ge=3, le=15)  #: square window, odd
    enl: float = Field(ge=1.0, le=50.0)  #: equivalent number of looks of the product

    @model_validator(mode="after")
    def _odd(self) -> "SpeckleParams":
        if self.window_px % 2 == 0:
            raise ValueError("speckle.window_px must be odd")
        return self


class ThresholdParams(_Strict):
    """How a pixel's ``|Δ dB|`` is judged changed."""

    method: Literal["otsu_abs_log_ratio"]  #: Otsu's threshold on ``|Δ dB|``
    bins: int = Field(ge=64, le=1024)  #: histogram bins
    clip_db: float = Field(ge=5.0, le=40.0)  #: ``|Δ|`` above this counts as this, for the histogram
    min_abs_db: float = Field(ge=0.5, le=10.0)  #: the floor: never flag below this


class MorphologyParams(_Strict):
    """Cleanup of the changed-pixel masks."""

    opening_iter: int = Field(ge=0, le=3)  #: removes isolated pixels
    closing_iter: int = Field(ge=0, le=3)  #: fills pinholes


class RegionParams(_Strict):
    """Which connected regions are reported."""

    min_area_m2: float = Field(ge=100.0)  #: smaller regions are dropped
    max_regions: int = Field(ge=1, le=200)  #: the largest-scoring ones are listed


class CoverageParams(_Strict):
    """How much of the area must be valid, and when a change is called widespread."""

    min_valid_fraction: float = Field(gt=0.0, le=1.0)  #: valid on both dates, else no result
    widespread_fraction: float = Field(gt=0.0, le=1.0)  #: past this, a caution is shown


class StretchParams(_Strict):
    """Display range of one polarization (the same for both dates)."""

    db_min: float  #: black
    db_max: float  #: white

    @model_validator(mode="after")
    def _ordered(self) -> "StretchParams":
        if self.db_max <= self.db_min:
            raise ValueError("display: db_max must be above db_min")
        return self


class DisplayParams(_Strict):
    """How the images are drawn."""

    VV: StretchParams  #: stretch of VV images
    VH: StretchParams  #: stretch of VH images
    heatmap_max_db: float = Field(gt=0.0, le=40.0)  #: ``|Δ|`` at full heatmap strength


class DetectionParams(_Strict):
    """Every parameter of the v1 change detector."""

    comment: list[str] = Field(default_factory=list, alias="_comment")  #: provenance notes
    version: int = Field(ge=1)  #: bumped on any change; stored with each comparison
    speckle: SpeckleParams  #: the speckle filter
    threshold: ThresholdParams  #: the change threshold
    morphology: MorphologyParams  #: mask cleanup
    regions: RegionParams  #: reported regions
    coverage: CoverageParams  #: coverage rules
    display: DisplayParams  #: image stretch and heatmap scale

    def stretch(self, polarization: str) -> StretchParams:
        """The display stretch of ``VV`` or ``VH``."""
        return self.display.VH if polarization == "VH" else self.display.VV


@lru_cache
def change_detection_params(path: Path = CHANGE_DETECTION_FILE) -> DetectionParams:
    """The validated parameters (read once)."""
    return DetectionParams.model_validate_json(path.read_text())

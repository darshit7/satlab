"""The comparison's three images, written as PNG with rasterio's PNG driver (worker only).

* ``before.png`` / ``after.png``: grey + alpha. The dB stretch comes from the parameters, never
  from the data, so both dates share it and a brighter pixel is really a stronger return.
* ``change.png``: one band of codes, coloured in the browser from the design tokens (so no colour
  values live in Python): 0 = no data, 1 = valid and unflagged, 2-127 = flagged decrease and
  129-254 = flagged increase, the magnitude from the threshold up to ``heatmap_max_db``.
"""

import os
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning

#: Change code of a pixel that is valid on both dates but not flagged.
CODE_UNFLAGGED = 1
#: Lowest code of a flagged decrease (the threshold); the highest is 127.
CODE_DECREASE_MIN = 2
#: Lowest code of a flagged increase (the threshold); the highest is 254.
CODE_INCREASE_MIN = 129
#: Codes per sign above the minimum.
_STEPS = 125


def stretch_u8(db: np.ndarray, db_min: float, db_max: float) -> tuple[np.ndarray, np.ndarray]:
    """Grey (1-255 for valid pixels) and alpha (255 valid, 0 NaN) bands of a dB image."""
    valid = np.isfinite(db)
    scaled = (np.nan_to_num(db, nan=db_min) - db_min) / (db_max - db_min)
    grey = (1 + np.clip(scaled, 0.0, 1.0) * 254).round().astype(np.uint8)
    grey[~valid] = 0
    alpha = np.where(valid, 255, 0).astype(np.uint8)
    return grey, alpha


def change_codes(
    delta_db: np.ndarray,
    kept_increase: np.ndarray,
    kept_decrease: np.ndarray,
    threshold_db: float,
    max_db: float,
) -> np.ndarray:
    """The ``change.png`` band (see the module docstring)."""
    codes = np.where(np.isfinite(delta_db), CODE_UNFLAGGED, 0).astype(np.uint8)
    span = max(max_db - threshold_db, 1e-6)
    level = np.clip((np.abs(np.nan_to_num(delta_db)) - threshold_db) / span, 0.0, 1.0)
    steps = (level * _STEPS).round().astype(np.uint8)
    codes[kept_decrease] = CODE_DECREASE_MIN + steps[kept_decrease]
    codes[kept_increase] = CODE_INCREASE_MIN + steps[kept_increase]
    return codes


def write_png(path: Path, bands: list[np.ndarray]) -> None:
    """Write 8-bit bands as a PNG, atomically (temporary file, then rename)."""
    height, width = bands[0].shape
    tmp = path.with_name(path.name + ".tmp.png")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with (
            rasterio.Env(GDAL_PAM_ENABLED="NO"),
            rasterio.open(
                tmp,
                "w",
                driver="PNG",
                width=width,
                height=height,
                count=len(bands),
                dtype="uint8",
            ) as dst,
        ):
            for i, band in enumerate(bands, start=1):
                dst.write(band, i)
    os.replace(tmp, path)

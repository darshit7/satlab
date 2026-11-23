"""Builders for SAR tests: STAC items like Planetary Computer's, and synthetic RTC COGs."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ACCOUNT = "sentinel1euwestrtc"
CONTAINER = "sentinel1-grd-rtc"


def href(stac_id: str, pol: str = "vv") -> str:
    """An asset href in the shape MPC uses."""
    return (
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/GRD/2024/11/1/IW/DV/"
        f"{stac_id}/measurement/iw-{pol}.rtc.tiff"
    )


def square(lon: float, lat: float, half_deg: float) -> dict:
    """A GeoJSON square footprint."""
    ring = [
        [lon - half_deg, lat - half_deg],
        [lon + half_deg, lat - half_deg],
        [lon + half_deg, lat + half_deg],
        [lon - half_deg, lat + half_deg],
        [lon - half_deg, lat - half_deg],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def item(
    stac_id: str,
    when: datetime,
    *,
    platform: str = "SENTINEL-1A",
    orbit: int = 110,
    state: str = "descending",
    footprint: dict | None = None,
    pols: tuple[str, ...] = ("VV", "VH"),
    epsg: int = 32630,
    href_for=href,
) -> dict:
    """A STAC item as the search returns it."""
    footprint = footprint or square(-0.425, 39.415, 1.0)
    ring = footprint["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return {
        "type": "Feature",
        "id": stac_id,
        "collection": "sentinel-1-rtc",
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "geometry": footprint,
        "properties": {
            "datetime": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "platform": platform,
            "sat:orbit_state": state,
            "sat:relative_orbit": orbit,
            "sar:polarizations": list(pols),
            "proj:epsg": epsg,
        },
        "assets": {p.lower(): {"href": href_for(stac_id, p.lower())} for p in pols},
    }


def collection(*items: dict) -> dict:
    """A FeatureCollection of items."""
    return {"type": "FeatureCollection", "features": list(items)}


def write_cog(
    path: Path,
    data: np.ndarray,
    *,
    epsg: int,
    x_min: float,
    y_max: float,
    res: float = 10.0,
    dtype: str = "float32",
    count: int = 1,
    driver: str = "GTiff",
) -> None:
    """A tiled GeoTIFF like an RTC band (float32, nodata -32768)."""
    import rasterio

    kwargs = {}
    if driver == "GTiff":
        kwargs = {"tiled": True, "blockxsize": 256, "blockysize": 256, "nodata": -32768.0}
    with rasterio.open(
        path,
        "w",
        driver=driver,
        width=data.shape[1],
        height=data.shape[0],
        count=count,
        dtype=dtype,
        crs=f"EPSG:{epsg}",
        transform=rasterio.Affine(res, 0, x_min, 0, -res, y_max),
        **kwargs,
    ) as dst:
        for band in range(1, count + 1):
            dst.write(data.astype(dtype), band)


def speckle(
    shape: tuple[int, int], sigma0: np.ndarray | float, enl: float, seed: int
) -> np.ndarray:
    """Gamma-distributed speckle around a linear backscatter field (mean ``sigma0``)."""
    rng = np.random.default_rng(seed)
    return (np.asarray(sigma0) * rng.gamma(enl, 1.0 / enl, size=shape)).astype(np.float32)

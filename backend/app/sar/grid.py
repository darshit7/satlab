"""The area of interest as a square pixel grid in its UTM zone (pure math, no GDAL).

Both dates of a comparison are resampled onto the same grid, so their pixels line up: a square
of ``size_km`` around the chosen centre, in the standard UTM zone of that centre, with its
origin snapped to the pixel size. The same request always gives the same grid, which is what
lets cached scene crops be reused.

UTM uses Krüger's series to fourth order in the third flattening (sub-millimetre within a zone);
the tests compare it with PROJ through rasterio. Only the standard 6° zones are used (none of the
Norway/Svalbard exceptions): the grid only has to be consistent, not the official zone.
"""

import math
from dataclasses import dataclass

#: WGS 84 semi-major axis.
_A_M = 6_378_137.0
#: WGS 84 flattening.
_F = 1 / 298.257223563
#: UTM scale on the central meridian.
_K0 = 0.9996
#: UTM false easting.
_E0_M = 500_000.0
#: UTM false northing in the southern hemisphere.
_N0_SOUTH_M = 10_000_000.0

_N = _F / (2 - _F)
_RECT_A = _A_M / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (
    _N / 2 - 2 / 3 * _N**2 + 5 / 16 * _N**3 + 41 / 180 * _N**4,
    13 / 48 * _N**2 - 3 / 5 * _N**3 + 557 / 1440 * _N**4,
    61 / 240 * _N**3 - 103 / 140 * _N**4,
    49561 / 161280 * _N**4,
)
_BETA = (
    _N / 2 - 2 / 3 * _N**2 + 37 / 96 * _N**3 - 1 / 360 * _N**4,
    1 / 48 * _N**2 + 1 / 15 * _N**3 - 437 / 1440 * _N**4,
    17 / 480 * _N**3 - 37 / 840 * _N**4,
    4397 / 161280 * _N**4,
)
_DELTA = (
    2 * _N - 2 / 3 * _N**2 - 2 * _N**3 + 116 / 45 * _N**4,
    7 / 3 * _N**2 - 8 / 5 * _N**3 - 227 / 45 * _N**4,
    56 / 15 * _N**3 - 136 / 35 * _N**4,
    4279 / 630 * _N**4,
)
_ECC_TERM = 2 * math.sqrt(_N) / (1 + _N)

#: Southernmost centre accepted (UTM's own limit).
MIN_LAT_DEG = -80.0
#: Northernmost centre accepted (UTM's own limit).
MAX_LAT_DEG = 84.0


class AoiOutOfRange(ValueError):
    """The area can't be put on a UTM grid (too far north or south, or across the antimeridian)."""


def utm_epsg(lat_deg: float, lon_deg: float) -> int:
    """EPSG code of the standard UTM zone holding the point (326xx north, 327xx south)."""
    zone = min(int((lon_deg + 180.0) // 6.0) + 1, 60)
    return (32600 if lat_deg >= 0 else 32700) + zone


def _zone_parts(epsg: int) -> tuple[float, float]:
    """Central meridian (degrees) and false northing (m) of a UTM EPSG code."""
    if not (32601 <= epsg <= 32660 or 32701 <= epsg <= 32760):
        raise ValueError(f"not a UTM zone: EPSG {epsg}")
    zone = epsg % 100
    return zone * 6.0 - 183.0, (_N0_SOUTH_M if epsg >= 32700 else 0.0)


def to_utm(lat_deg: float, lon_deg: float, epsg: int) -> tuple[float, float]:
    """Easting and northing (m) of a WGS 84 point in the given UTM zone."""
    lon0_deg, n0_m = _zone_parts(epsg)
    phi = math.radians(lat_deg)
    dlam = math.radians(lon_deg - lon0_deg)
    t = math.sinh(math.atanh(math.sin(phi)) - _ECC_TERM * math.atanh(_ECC_TERM * math.sin(phi)))
    xi = math.atan2(t, math.cos(dlam))
    eta = math.atanh(math.sin(dlam) / math.sqrt(1 + t * t))
    easting = eta + sum(
        a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1)
    )
    northing = xi + sum(
        a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1)
    )
    return _E0_M + _K0 * _RECT_A * easting, n0_m + _K0 * _RECT_A * northing


def from_utm(x_m: float, y_m: float, epsg: int) -> tuple[float, float]:
    """Latitude and longitude (degrees) of a point given in a UTM zone."""
    lon0_deg, n0_m = _zone_parts(epsg)
    xi = (y_m - n0_m) / (_K0 * _RECT_A)
    eta = (x_m - _E0_M) / (_K0 * _RECT_A)
    xi_p = xi - sum(
        b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1)
    )
    eta_p = eta - sum(
        b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1)
    )
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(phi), lon0_deg + math.degrees(lam)


@dataclass(frozen=True)
class AoiGrid:
    """A square pixel grid in a UTM zone: the frame both dates are resampled onto."""

    epsg: int  #: the UTM zone (EPSG code)
    x_min_m: float  #: easting of the west edge
    y_max_m: float  #: northing of the north edge
    resolution_m: float  #: pixel size
    width: int  #: pixels, west to east
    height: int  #: pixels, north to south

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """GDAL-style affine coefficients (a, b, c, d, e, f): x = a·col + c, y = e·row + f."""
        return (self.resolution_m, 0.0, self.x_min_m, 0.0, -self.resolution_m, self.y_max_m)

    def key(self) -> str:
        """A canonical text form, for cache keys."""
        return (
            f"{self.epsg}:{self.x_min_m:.1f}:{self.y_max_m:.1f}:"
            f"{self.resolution_m:g}:{self.width}x{self.height}"
        )

    def to_dict(self) -> dict[str, float | int]:
        """The grid as plain JSON values (stored with a comparison)."""
        return {
            "epsg": self.epsg,
            "x_min_m": self.x_min_m,
            "y_max_m": self.y_max_m,
            "resolution_m": self.resolution_m,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, float | int]) -> "AoiGrid":
        """The grid back from :meth:`to_dict`."""
        return cls(
            epsg=int(data["epsg"]),
            x_min_m=float(data["x_min_m"]),
            y_max_m=float(data["y_max_m"]),
            resolution_m=float(data["resolution_m"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )

    def xy(self, col: float, row: float) -> tuple[float, float]:
        """UTM easting/northing of a pixel position (corners at whole numbers)."""
        return self.x_min_m + col * self.resolution_m, self.y_max_m - row * self.resolution_m

    def lonlat(self, col: float, row: float) -> tuple[float, float]:
        """Longitude/latitude of a pixel position (corners at whole numbers)."""
        lat, lon = from_utm(*self.xy(col, row), self.epsg)
        return lon, lat


def aoi_grid(lat_deg: float, lon_deg: float, size_km: float, resolution_m: float = 10.0) -> AoiGrid:
    """The grid of a ``size_km`` square centred on the point, in the point's UTM zone.

    Raises ``AoiOutOfRange`` beyond UTM's latitudes or when the square crosses the antimeridian.
    """
    if not MIN_LAT_DEG <= lat_deg <= MAX_LAT_DEG:
        raise AoiOutOfRange("the area must be between 80° S and 84° N")
    epsg = utm_epsg(lat_deg, lon_deg)
    x_m, y_m = to_utm(lat_deg, lon_deg, epsg)
    side_px = round(size_km * 1000.0 / resolution_m)
    half_m = side_px * resolution_m / 2
    grid = AoiGrid(
        epsg=epsg,
        x_min_m=round((x_m - half_m) / resolution_m) * resolution_m,
        y_max_m=round((y_m + half_m) / resolution_m) * resolution_m,
        resolution_m=resolution_m,
        width=side_px,
        height=side_px,
    )
    lons = [lon for lon, _ in polygon_lonlat(grid, per_side=2)]
    # from_utm doesn't wrap: a corner past the antimeridian comes back beyond ±180°
    if max(lons) - min(lons) > 180.0 or any(not -180.0 <= lon <= 180.0 for lon in lons):
        raise AoiOutOfRange("the area must not cross the antimeridian")
    return grid


def box_polygon(
    grid: AoiGrid, col0: float, row0: float, col1: float, row1: float, per_side: int = 8
) -> list[tuple[float, float]]:
    """A closed lon/lat ring around a pixel box, ``per_side`` points per edge (edges bend)."""
    ring: list[tuple[float, float]] = []
    edges = (
        ((col0, row0), (col1, row0)),
        ((col1, row0), (col1, row1)),
        ((col1, row1), (col0, row1)),
        ((col0, row1), (col0, row0)),
    )
    for (c_a, r_a), (c_b, r_b) in edges:
        for i in range(per_side):
            t = i / per_side
            ring.append(grid.lonlat(c_a + (c_b - c_a) * t, r_a + (r_b - r_a) * t))
    ring.append(ring[0])
    return [(round(lon, 6), round(lat, 6)) for lon, lat in ring]


def polygon_lonlat(grid: AoiGrid, per_side: int = 8) -> list[tuple[float, float]]:
    """The grid's outline as a closed lon/lat ring."""
    return box_polygon(grid, 0, 0, grid.width, grid.height, per_side)


def bbox_lonlat(grid: AoiGrid) -> tuple[float, float, float, float]:
    """The lon/lat envelope (min lon, min lat, max lon, max lat) of the grid."""
    ring = polygon_lonlat(grid)
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def lattice_lonlat(grid: AoiGrid, per_side: int = 8) -> list[tuple[float, float]]:
    """``per_side`` squared lon/lat points spread over the grid (cell centres), for coverage."""
    step_col = grid.width / per_side
    step_row = grid.height / per_side
    return [
        grid.lonlat((i + 0.5) * step_col, (j + 0.5) * step_row)
        for j in range(per_side)
        for i in range(per_side)
    ]

"""Search the Sentinel-1 RTC collection on Microsoft Planetary Computer's STAC API.

STAC JSON is untrusted input (AGENTS.md §12.4). Items are validated by small pydantic models of
just the fields used, one bad item is skipped (and counted) rather than failing the search, and
an item whose asset href fails :func:`app.sar.assets.blob_ref` is dropped at this boundary, so
no unchecked URL gets further. Platform names are normalised (the archive mixes ``SENTINEL-1A``
and ``sentinel-1d``).
"""

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from app.data.http import UpstreamError, checked_url, fetch_json
from app.sar.assets import blob_ref

#: The collection searched.
COLLECTION = "sentinel-1-rtc"
#: The only STAC host the client talks to.
STAC_HOSTS = frozenset({"planetarycomputer.microsoft.com"})
#: A STAC item id as accepted from clients and in URLs.
STAC_ID_PATTERN = r"^[_.A-Za-z0-9-]{1,128}$"
#: The polarizations offered (the collection also has HH/HV over polar regions).
POLARIZATIONS = ("VV", "VH")
#: Most items read from one search response.
MAX_FEATURES = 200
#: Most polygons and vertices accepted in a footprint.
_MAX_POLYGONS = 4
_MAX_VERTICES = 2000

_STAC_ID_RE = re.compile(STAC_ID_PATTERN)
_PLATFORM_RE = re.compile(r"^sentinel-1[a-z]$")


def normalise_platform(raw: str) -> str | None:
    """``sentinel-1a`` … from any casing, or None if it isn't a Sentinel-1 platform name."""
    value = raw.strip().lower()
    return value if _PLATFORM_RE.match(value) else None


class _Asset(BaseModel):
    model_config = ConfigDict(extra="ignore")
    href: str = Field(max_length=1024)


class _Geometry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list

    @field_validator("coordinates")
    @classmethod
    def _bounded(cls, value: list, info: ValidationInfo) -> list:
        polygons = [value] if info.data.get("type") == "Polygon" else value
        if not 1 <= len(polygons) <= _MAX_POLYGONS:
            raise ValueError("footprint: too many polygons")
        vertices = 0
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("footprint: bad polygon")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4:
                    raise ValueError("footprint: bad ring")
                for point in ring:
                    if (
                        not isinstance(point, list)
                        or len(point) < 2
                        or not all(isinstance(v, int | float) and math.isfinite(v) for v in point)
                        or not -180.0 <= point[0] <= 180.0
                        or not -90.0 <= point[1] <= 90.0
                    ):
                        raise ValueError("footprint: bad point")
                vertices += len(ring)
        if vertices > _MAX_VERTICES:
            raise ValueError("footprint: too many vertices")
        return value


class _Props(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    datetime: AwareDatetime
    platform: str = Field(max_length=32)
    orbit_state: Literal["ascending", "descending"] = Field(alias="sat:orbit_state")
    relative_orbit: int = Field(alias="sat:relative_orbit", ge=1, le=175)
    polarizations: list[str] = Field(alias="sar:polarizations", max_length=4)
    epsg: int | None = Field(default=None, alias="proj:epsg", ge=1024, le=999_999)
    code: str | None = Field(default=None, alias="proj:code", max_length=32)


class _ItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=STAC_ID_PATTERN)
    bbox: list[float] = Field(min_length=4, max_length=4)
    geometry: _Geometry
    properties: _Props
    assets: dict[str, _Asset] = Field(max_length=40)


@dataclass(frozen=True)
class SarItem:
    """One Sentinel-1 RTC scene (a frame of a pass), as far as comparisons need it."""

    stac_id: str  #: the STAC item id
    platform: str  #: normalised, ``sentinel-1a`` … ``sentinel-1d``
    orbit_state: str  #: ``ascending`` or ``descending``
    relative_orbit: int  #: with ``orbit_state``, the track
    polarizations: tuple[str, ...]  #: upper case, e.g. ("VV", "VH")
    acquired_at: datetime  #: the item's datetime (UTC)
    bbox: tuple[float, float, float, float]  #: min lon, min lat, max lon, max lat
    footprint: dict = field(hash=False, compare=False)  #: GeoJSON Polygon or MultiPolygon
    epsg: int  #: the scene's UTM zone
    hrefs: dict[str, str] = field(hash=False, compare=False)  #: polarization → checked asset href


@dataclass(frozen=True)
class SearchPage:
    """One search's validated items, how many were skipped, and whether ``limit`` was reached."""

    items: list[SarItem]  #: valid items, oldest first
    skipped: int  #: items dropped as malformed or unusable
    truncated: bool  #: the response held ``limit`` items: there may be more


def parse_item(raw: object, *, account: str, container: str) -> SarItem | None:
    """A validated :class:`SarItem`, or None if the item is malformed or has no usable asset."""
    try:
        item = _ItemIn.model_validate(raw)
    except ValidationError:
        return None
    props = item.properties
    platform = normalise_platform(props.platform)
    epsg = props.epsg
    if epsg is None and props.code and props.code.upper().startswith("EPSG:"):
        digits = props.code[5:]
        epsg = int(digits) if digits.isdigit() else None
    if platform is None or epsg is None:
        return None
    polarizations = tuple(sorted({p.upper() for p in props.polarizations} & set(POLARIZATIONS)))
    hrefs: dict[str, str] = {}
    for pol in polarizations:
        asset = item.assets.get(pol.lower())
        if asset is None:
            continue
        try:
            blob_ref(asset.href, account=account, container=container)
        except ValueError:
            continue
        hrefs[pol] = asset.href
    if not hrefs:
        return None
    min_lon, min_lat, max_lon, max_lat = item.bbox
    return SarItem(
        stac_id=item.id,
        platform=platform,
        orbit_state=props.orbit_state,
        relative_orbit=props.relative_orbit,
        polarizations=tuple(sorted(hrefs)),
        acquired_at=props.datetime,
        bbox=(min_lon, min_lat, max_lon, max_lat),
        footprint=item.geometry.model_dump(),
        epsg=epsg,
        hrefs=hrefs,
    )


def parse_items(payload: object, *, account: str, container: str) -> tuple[list[SarItem], int]:
    """The valid items of a STAC ``FeatureCollection`` and how many were skipped."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("not a STAC item collection")
    items: list[SarItem] = []
    skipped = 0
    for raw in features[:MAX_FEATURES]:
        item = parse_item(raw, account=account, container=container)
        if item is None:
            skipped += 1
        else:
            items.append(item)
    skipped += max(len(features) - MAX_FEATURES, 0)
    items.sort(key=lambda i: i.acquired_at)
    return items, skipped


class StacClient:
    """The STAC search and item endpoints, through :func:`app.data.http.fetch_json`.

    Throttling is the caller's job (:class:`app.services.sar.SarService`).
    """

    def __init__(
        self,
        url: str,
        *,
        http: httpx.AsyncClient,
        max_bytes: int,
        account: str,
        container: str,
        subscription_key: str | None = None,
    ) -> None:
        self._url = checked_url(url.rstrip("/"), STAC_HOSTS)
        self._http = http
        self._max_bytes = max_bytes
        self._account = account
        self._container = container
        self._headers = {"Ocp-Apim-Subscription-Key": subscription_key} if subscription_key else {}

    async def search(
        self,
        bbox: tuple[float, float, float, float],
        start: datetime,
        end: datetime,
        *,
        limit: int = 100,
    ) -> SearchPage:
        """Items of the collection intersecting ``bbox`` between ``start`` and ``end``."""
        body = {
            "collections": [COLLECTION],
            "bbox": [round(v, 6) for v in bbox],
            "datetime": f"{_iso(start)}/{_iso(end)}",
            "limit": limit,
            "sortby": [{"field": "datetime", "direction": "asc"}],
        }
        payload = await fetch_json(
            self._http,
            "POST",
            f"{self._url}/search",
            hosts=STAC_HOSTS,
            max_bytes=self._max_bytes,
            json_body=body,
            headers=self._headers,
        )
        try:
            items, skipped = parse_items(payload, account=self._account, container=self._container)
        except ValueError:
            raise UpstreamError("not a STAC item collection") from None
        returned = len(payload.get("features", [])) if isinstance(payload, dict) else 0
        return SearchPage(items=items, skipped=skipped, truncated=returned >= limit)

    async def item(self, stac_id: str) -> SarItem | None:
        """One item of the collection by id (None if it doesn't validate)."""
        if not _STAC_ID_RE.match(stac_id):
            return None
        try:
            payload = await fetch_json(
                self._http,
                "GET",
                f"{self._url}/collections/{COLLECTION}/items/{stac_id}",
                hosts=STAC_HOSTS,
                max_bytes=self._max_bytes,
                headers=self._headers,
            )
        except UpstreamError as exc:
            if exc.status == 404:
                return None
            raise
        return parse_item(payload, account=self._account, container=self._container)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

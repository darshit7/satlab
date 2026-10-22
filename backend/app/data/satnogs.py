"""SatNOGS DB telemetry client: real frames received by SatNOGS stations, fetched off the sim loop.

API: ``GET /api/telemetry/?satellite=<NORAD>`` with ``Authorization: Token <key>``, paginated
by cursor (``next`` is a full URL). Everything that comes back is untrusted: responses are
streamed with a byte cap, each record is validated on its own (bad ones skipped), and a ``next``
URL is followed only if it points at the configured endpoint, so the API can't steer our requests
(or our token) elsewhere. Redirects are not followed for the same reason.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.packets.templates import MAX_FRAME_BYTES

MAX_RESPONSE_BYTES = 2_000_000
MAX_RECORDS_PER_PAGE = 500


class SatnogsUnavailable(Exception):
    """The archive couldn't be read (network, HTTP status, malformed response)."""


class SatnogsRateLimited(SatnogsUnavailable):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__("rate limited")
        self.retry_after_s = retry_after_s


class TelemetryRecord(BaseModel):
    """One archived frame, as far as we use it."""

    model_config = ConfigDict(extra="ignore")

    frame: str = Field(
        min_length=2, max_length=2 * MAX_FRAME_BYTES, pattern=r"^(?:[0-9A-Fa-f]{2})+$"
    )
    observation_id: int | None = Field(default=None, ge=0, le=2**31 - 1)
    station_id: int | None = Field(default=None, ge=0, le=2**31 - 1)
    timestamp: AwareDatetime | None = None


@dataclass(frozen=True)
class Frame:
    raw: bytes
    observation_id: int | None
    station_id: int | None
    received_at: datetime | None


class SatnogsClient:
    def __init__(
        self,
        url: str,
        token: SecretStr | None,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("the SatNOGS DB URL must be https")
        self._url = url
        self._origin = (parts.scheme, parts.hostname, parts.port, parts.path)
        self._token = token
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "satlab-mission-control-sim/0.1 (educational)"},
            follow_redirects=False,
        )

    @property
    def configured(self) -> bool:
        return self._token is not None and bool(self._token.get_secret_value())

    async def aclose(self) -> None:
        await self._client.aclose()

    def _same_endpoint(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.scheme, parts.hostname, parts.port, parts.path) == self._origin

    async def frames(self, norad_id: int, *, max_pages: int, limit: int) -> list[Frame]:
        """Up to ``limit`` of the satellite's most recent frames (the API lists newest first)."""
        if not self.configured:
            raise SatnogsUnavailable("no token configured")
        url: str | None = self._url
        params: dict[str, str | int] | None = {"satellite": norad_id, "format": "json"}
        frames: list[Frame] = []
        for _ in range(max_pages):
            if url is None or len(frames) >= limit:
                break
            page = await self._get_json(url, params)
            results = page.get("results") if isinstance(page, dict) else None
            if not isinstance(results, list):
                raise SatnogsUnavailable("unexpected response shape")
            for item in results[:MAX_RECORDS_PER_PAGE]:
                try:
                    record = TelemetryRecord.model_validate(item)
                except ValidationError:
                    continue  # one bad record doesn't spoil the page
                frames.append(
                    Frame(
                        bytes.fromhex(record.frame),
                        record.observation_id,
                        record.station_id,
                        record.timestamp,
                    )
                )
            next_url = page.get("next")
            url = next_url if isinstance(next_url, str) and self._same_endpoint(next_url) else None
            params = None  # the cursor is in the next URL
        return frames[:limit]

    async def _get_json(self, url: str, params: dict[str, str | int] | None) -> object:
        assert self._token is not None
        headers = {"Authorization": f"Token {self._token.get_secret_value()}"}
        try:
            async with self._client.stream("GET", url, params=params, headers=headers) as response:
                if response.status_code == 429:
                    raise SatnogsRateLimited(_retry_after(response.headers.get("Retry-After")))
                if response.status_code != 200:
                    raise SatnogsUnavailable(f"HTTP {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise SatnogsUnavailable("response too large")
        except httpx.HTTPError as exc:
            raise SatnogsUnavailable(type(exc).__name__) from None
        try:
            return json.loads(body)
        except ValueError:
            raise SatnogsUnavailable("response is not JSON") from None


def _retry_after(value: str | None) -> float:
    """Seconds to wait, from a Retry-After header (seconds only), within [60 s, 6 h]."""
    try:
        seconds = float(value) if value is not None else 600.0
    except ValueError:
        seconds = 600.0
    return min(max(seconds, 60.0), 6 * 3600.0)

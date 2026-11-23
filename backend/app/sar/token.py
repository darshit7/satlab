"""Planetary Computer SAS tokens: short-lived read access to a collection's storage container.

Anonymous tokens work for ``sentinel-1-rtc`` (no account needed since 2024); a subscription key,
if configured, only relaxes rate limits. A token lasts about 45 minutes. It is kept in memory as
a ``SecretStr``, refreshed before a job could outlive it, and never logged, stored or returned
(AGENTS.md §12.6).
"""

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC
from urllib.parse import parse_qs

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.data.http import UpstreamError, checked_url, fetch_json
from app.sar.stac import STAC_HOSTS

#: Characters a SAS query string may hold.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9%=&_.\-:+/]{16,2048}$")
#: A token whose expiry is further out than this is refused as implausible.
_MAX_LIFETIME_S = 2 * 24 * 3600.0


class _TokenOut(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    token: str = Field(max_length=2048)
    expiry: AwareDatetime = Field(alias="msft:expiry")


class SasTokenProvider:
    """One collection's SAS token, fetched when needed and shared by concurrent callers."""

    def __init__(
        self,
        token_url: str,
        collection: str,
        *,
        http: httpx.AsyncClient,
        max_bytes: int,
        subscription_key: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._url = checked_url(f"{token_url.rstrip('/')}/{collection}", STAC_HOSTS)
        self._http = http
        self._max_bytes = max_bytes
        self._headers = {"Ocp-Apim-Subscription-Key": subscription_key} if subscription_key else {}
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: SecretStr | None = None
        self._expires_at = 0.0

    def __repr__(self) -> str:
        return f"SasTokenProvider(valid_for={max(self._expires_at - self._clock(), 0):.0f}s)"

    async def get(self, min_valid_s: float) -> SecretStr:
        """A token valid for at least ``min_valid_s`` more seconds (fetched if needed)."""
        async with self._lock:
            if self._token is None or self._expires_at - self._clock() < min_valid_s:
                self._token, self._expires_at = await self._fetch()
            if self._expires_at - self._clock() < min_valid_s:
                raise UpstreamError("token too short-lived")
            return self._token

    async def _fetch(self) -> tuple[SecretStr, float]:
        payload = await fetch_json(
            self._http,
            "GET",
            self._url,
            hosts=STAC_HOSTS,
            max_bytes=self._max_bytes,
            headers=self._headers,
        )
        try:
            out = _TokenOut.model_validate(payload)
        except ValidationError:
            raise UpstreamError("bad token response") from None
        query = parse_qs(out.token)
        if not _TOKEN_RE.match(out.token) or "sig" not in query or "se" not in query:
            raise UpstreamError("bad token response")
        expires_at = out.expiry.astimezone(UTC).timestamp()
        now = self._clock()
        if not now + 60.0 < expires_at < now + _MAX_LIFETIME_S:
            raise UpstreamError("bad token expiry")
        return SecretStr(out.token), expires_at

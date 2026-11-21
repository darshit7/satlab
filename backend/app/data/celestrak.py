"""Celestrak GP (TLE) client and parser.

Endpoint docs: https://celestrak.org/NORAD/documentation/gp-data-formats.php
Celestrak answers with plain-text notices instead of TLEs when nothing matches ("No GP data found");
those are surfaced as ``CelestrakNotice``. Its data updates every 2 hours, and a repeat download
before then is refused with HTTP 403 ("GP data has not updated since your last successful
download ..."): that is ``CelestrakNotUpdated``, which means our copy is current. Repeating a
refused request is pointless and counts toward Celestrak's error limit (50 errors in 2 hours gets
an IP firewalled), so callers must not retry it.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sgp4.api import Satrec

DEFAULT_MAX_BYTES = 16 * 1024 * 1024  #: the "active" group is about 2.4 MB
_SINCE = re.compile(r"\bat (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\b")


@dataclass(frozen=True)
class TleRecord:
    """One element set as Celestrak sent it: its NORAD id, name and the two TLE lines."""

    norad_id: int
    name: str
    line1: str
    line2: str


class CelestrakNotice(Exception):
    """Celestrak returned a text notice instead of element sets."""


class CelestrakNotUpdated(CelestrakNotice):
    """Celestrak refused a repeat download (HTTP 403): nothing changed since our last one.

    ``since`` is that last successful download, when the notice says (validated: within the last
    day, not in the future); None otherwise.
    """

    def __init__(self, notice: str, since: datetime | None) -> None:
        super().__init__(notice)
        self.since = since


class CelestrakTooLarge(Exception):
    """A response was larger than the client's byte cap; it was not read further."""


def not_updated_since(text: str, now: datetime) -> datetime | None:
    """The "last successful download ... at YYYY-MM-DD HH:MM:SS UTC" time of a 403 notice, if
    it parses and falls within the last day."""
    match = _SINCE.search(text[:2000])
    if match is None:
        return None
    try:
        since = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return since if now - timedelta(days=1) <= since <= now else None


def _checksum_ok(line: str) -> bool:
    if len(line) < 69 or not line[68].isdigit():
        return False
    total = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return total % 10 == int(line[68])


def parse_tle_text(text: str) -> list[TleRecord]:
    """Parse 3-line (name + two element lines) TLE text. Malformed sets are skipped."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    records: list[TleRecord] = []
    i = 0
    while i + 2 < len(lines):
        name, line1, line2 = lines[i], lines[i + 1], lines[i + 2]
        if line1.startswith("1 ") and line2.startswith("2 "):
            if _checksum_ok(line1) and _checksum_ok(line2):
                try:
                    satnum = Satrec.twoline2rv(line1, line2).satnum
                except ValueError:
                    satnum = None
                if satnum is not None:
                    records.append(TleRecord(satnum, name.strip(), line1, line2))
            i += 3
        else:
            i += 1  # resynchronise on the next name line
    return records


class CelestrakClient:
    """Async client for Celestrak's GP endpoint, always in TLE format.

    Every fetch raises ``CelestrakNotice`` instead of returning nothing (``CelestrakNotUpdated``
    for a refused repeat download) and lets HTTP errors through (``httpx.HTTPStatusError``).
    Responses are streamed with a byte cap (``CelestrakTooLarge``) and redirects are never
    followed. Throttling is the caller's job (``CatalogService``).
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self._base_url = base_url
        self._max_bytes = max_bytes
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "satlab-mission-control-sim/0.1 (educational)"},
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _get(self, **params: str | int) -> list[TleRecord]:
        async with self._client.stream(
            "GET", self._base_url, params={**params, "FORMAT": "tle"}, follow_redirects=False
        ) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                raise CelestrakTooLarge(f"{declared} bytes declared")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise CelestrakTooLarge(f"over {self._max_bytes} bytes")
        text = body.decode("utf-8", errors="replace")
        if response.status_code == 403 and "has not updated" in text[:2000].lower():
            first = text.strip().splitlines()[0] if text.strip() else "not updated"
            raise CelestrakNotUpdated(first[:200], not_updated_since(text, datetime.now(UTC)))
        response.raise_for_status()  # anything else that isn't a 2xx, redirects included
        # ~16k checksums and SGP4 initialisations: off the event loop the sessions tick on.
        records = await asyncio.to_thread(parse_tle_text, text)
        if not records:
            notice = text.strip().splitlines()[0] if text.strip() else "empty"
            raise CelestrakNotice(notice[:200])
        return records

    async def fetch_group(self, group: str) -> list[TleRecord]:
        """Every element set of a Celestrak group (e.g. ``active``)."""
        return await self._get(GROUP=group)

    async def fetch_by_catnr(self, norad_id: int) -> list[TleRecord]:
        """The element set of one satellite, by NORAD catalog number."""
        return await self._get(CATNR=norad_id)

    async def search_by_name(self, name: str) -> list[TleRecord]:
        """Element sets whose name contains ``name`` (Celestrak's own matching)."""
        return await self._get(NAME=name)

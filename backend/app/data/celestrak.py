"""Celestrak GP (TLE) client and parser.

Endpoint docs: https://celestrak.org/NORAD/documentation/gp-data-formats.php
Celestrak answers with plain-text notices instead of TLEs when nothing matches ("No GP data found")
or when a group was re-requested too soon; those are surfaced as ``CelestrakNotice``.
"""

from dataclasses import dataclass

import httpx
from sgp4.api import Satrec


@dataclass(frozen=True)
class TleRecord:
    norad_id: int
    name: str
    line1: str
    line2: str


class CelestrakNotice(Exception):
    """Celestrak returned a text notice instead of element sets."""


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
    def __init__(self, base_url: str, timeout_s: float, client: httpx.AsyncClient | None = None):
        self._base_url = base_url
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "satlab-mission-control-sim/0.1 (educational)"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, **params: str | int) -> list[TleRecord]:
        response = await self._client.get(self._base_url, params={**params, "FORMAT": "tle"})
        response.raise_for_status()
        records = parse_tle_text(response.text)
        if not records:
            notice = response.text.strip().splitlines()[0] if response.text.strip() else "empty"
            raise CelestrakNotice(notice[:200])
        return records

    async def fetch_group(self, group: str) -> list[TleRecord]:
        return await self._get(GROUP=group)

    async def fetch_by_catnr(self, norad_id: int) -> list[TleRecord]:
        return await self._get(CATNR=norad_id)

    async def search_by_name(self, name: str) -> list[TleRecord]:
        return await self._get(NAME=name)

"""Shared helpers for talking to an upstream HTTP API safely (AGENTS.md §12.4).

Every request goes to an allow-listed https host, never follows a redirect, and reads at most a
fixed number of bytes, refusing early when ``Content-Length`` already says more. Errors carry a
generic message (an HTTP status or an exception class name), never a URL, header or body, so
they can be logged and shown without leaking anything.

Used by the SAR imagery clients (:mod:`app.sar.stac`, :mod:`app.sar.token`). Celestrak and
SatNOGS still own their clients (AGENTS.md §12.11).
"""

import asyncio
import json
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

#: Sent with every request, as the other upstream clients do.
USER_AGENT = "satlab-mission-control-sim/0.1 (educational)"

#: Bodies above this are parsed off the event loop.
_PARSE_IN_THREAD_BYTES = 64 * 1024


class UpstreamError(Exception):
    """An upstream request failed; the message is generic (``"HTTP 503"``, ``"ConnectTimeout"``)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  #: the HTTP status, when the upstream answered


class UpstreamRateLimited(UpstreamError):
    """The upstream answered 429; wait ``retry_after_s`` before the next request."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__("HTTP 429", status=429)
        self.retry_after_s = retry_after_s


class UpstreamTooLarge(UpstreamError):
    """The response was larger than the byte cap."""


def new_client(timeout_s: float) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` with the timeout, our User-Agent and redirects off."""
    return httpx.AsyncClient(
        timeout=timeout_s, headers={"User-Agent": USER_AGENT}, follow_redirects=False
    )


def checked_url(url: str, hosts: frozenset[str]) -> str:
    """``url`` itself, if it is https on one of ``hosts``; else ``ValueError``.

    No user info, no port other than 443, no fragment, and the host compared exactly (lower
    case), so ``planetarycomputer.microsoft.com.evil.example`` is not a match.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        raise ValueError("not an allowed URL")
    if parts.hostname is None or parts.hostname.lower() not in hosts:
        raise ValueError("not an allowed host")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("not an allowed URL") from None
    if port not in (None, 443) or parts.fragment:
        raise ValueError("not an allowed URL")
    return url


def retry_after_s(value: str | None, *, default: float, low: float, high: float) -> float:
    """Seconds to wait from a ``Retry-After`` header (seconds form only), within [low, high]."""
    try:
        seconds = float(value) if value is not None else default
    except ValueError:
        seconds = default
    return min(max(seconds, low), high)


async def fetch_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    hosts: frozenset[str],
    max_bytes: int,
    params: Mapping[str, str] | None = None,
    json_body: object = None,
    headers: Mapping[str, str] | None = None,
    retry_low_s: float = 10.0,
    retry_high_s: float = 900.0,
) -> object:
    """The parsed JSON body of a 200 response, read with a byte cap.

    Raises ``UpstreamRateLimited`` on 429, ``UpstreamTooLarge`` past ``max_bytes`` and
    ``UpstreamError`` for anything else that isn't a 200 with JSON (redirects included).
    """
    checked_url(url, hosts)
    try:
        async with client.stream(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            follow_redirects=False,
        ) as response:
            if response.status_code == 429:
                raise UpstreamRateLimited(
                    retry_after_s(
                        response.headers.get("Retry-After"),
                        default=60.0,
                        low=retry_low_s,
                        high=retry_high_s,
                    )
                )
            if response.status_code != 200:
                raise UpstreamError(f"HTTP {response.status_code}", status=response.status_code)
            declared = response.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise UpstreamTooLarge("response too large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise UpstreamTooLarge("response too large")
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__) from None
    try:
        if len(body) > _PARSE_IN_THREAD_BYTES:
            return await asyncio.to_thread(json.loads, bytes(body))
        return json.loads(bytes(body))
    except ValueError:
        raise UpstreamError("not JSON") from None

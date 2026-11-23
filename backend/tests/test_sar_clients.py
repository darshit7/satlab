"""The SAR upstream clients: the shared HTTP helper, STAC parsing and search, SAS tokens, hrefs."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.data.http import (
    UpstreamError,
    UpstreamRateLimited,
    UpstreamTooLarge,
    checked_url,
    fetch_json,
)
from app.sar.assets import blob_ref, vsiaz_path
from app.sar.stac import StacClient, normalise_platform, parse_items
from app.sar.token import SasTokenProvider
from tests.sar_helpers import ACCOUNT, CONTAINER, collection, href, item

HOSTS = frozenset({"planetarycomputer.microsoft.com"})
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
T0 = datetime(2024, 11, 1, 6, 2, 7, tzinfo=UTC)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


# ---------- shared helper ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://planetarycomputer.microsoft.com/x",
        "https://planetarycomputer.microsoft.com.evil.example/x",
        "https://user@planetarycomputer.microsoft.com/x",
        "https://planetarycomputer.microsoft.com:8443/x",
        "https://planetarycomputer.microsoft.com/x#frag",
        "https://example.com/x",
    ],
)
def test_checked_url_refuses(url):
    with pytest.raises(ValueError):
        checked_url(url, HOSTS)


def test_checked_url_accepts():
    assert checked_url("https://PlanetaryComputer.microsoft.com/api", HOSTS)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), UpstreamError),
        (httpx.Response(500, text="boom"), UpstreamError),
        (httpx.Response(200, text="<html>"), UpstreamError),
        (httpx.Response(200, content=b"x" * 2000), UpstreamTooLarge),
        (httpx.Response(200, headers={"Content-Length": "99999"}, content=b"{}"), UpstreamTooLarge),
    ],
)
async def test_fetch_json_failures_are_generic(response, error):
    async with _client(lambda request: response) as client:
        with pytest.raises(error) as caught:
            await fetch_json(client, "GET", STAC, hosts=HOSTS, max_bytes=1000)
    assert "evil" not in str(caught.value) and "boom" not in str(caught.value)


async def test_fetch_json_rate_limited_is_clamped():
    async with _client(lambda r: httpx.Response(429, headers={"Retry-After": "99999"})) as client:
        with pytest.raises(UpstreamRateLimited) as caught:
            await fetch_json(client, "GET", STAC, hosts=HOSTS, max_bytes=1000)
    assert caught.value.retry_after_s == 900.0


async def test_fetch_json_transport_error_is_generic():
    def handler(request):
        raise httpx.ConnectError("offline at https://secret.example/?sig=abc")

    async with _client(handler) as client:
        with pytest.raises(UpstreamError) as caught:
            await fetch_json(client, "GET", STAC, hosts=HOSTS, max_bytes=1000)
    assert str(caught.value) == "ConnectError"


# ---------- STAC ----------


def test_normalise_platform():
    assert normalise_platform("SENTINEL-1A") == "sentinel-1a"
    assert normalise_platform(" sentinel-1d ") == "sentinel-1d"
    assert normalise_platform("SENTINEL-2A") is None
    assert normalise_platform("sentinel-1") is None


def test_parse_items_skips_bad_ones():
    good = item("S1A_GOOD", T0)
    hostile = item("S1A_HOSTILE", T0, href_for=lambda i, p: "https://evil.example/x.tif")
    no_proj = item("S1A_NOPROJ", T0)
    del no_proj["properties"]["proj:epsg"]
    bad_orbit = item("S1A_BADORBIT", T0, orbit=999)
    bad_geometry = item("S1A_BADGEOM", T0)
    bad_geometry["geometry"]["coordinates"][0][0] = [500, 0]
    lowercase = item("S1D_NEW", T0 + timedelta(days=6), platform="sentinel-1d")
    coded = item("S1C_CODE", T0)
    del coded["properties"]["proj:epsg"]
    coded["properties"]["proj:code"] = "EPSG:32630"
    items, skipped = parse_items(
        collection(good, hostile, no_proj, bad_orbit, bad_geometry, lowercase, coded, {"x": 1}),
        account=ACCOUNT,
        container=CONTAINER,
    )
    assert [i.stac_id for i in items] == ["S1A_GOOD", "S1C_CODE", "S1D_NEW"]
    assert skipped == 5
    assert items[0].platform == "sentinel-1a" and items[2].platform == "sentinel-1d"
    assert items[0].polarizations == ("VH", "VV")
    assert items[1].epsg == 32630


def test_parse_items_refuses_a_non_collection():
    with pytest.raises(ValueError):
        parse_items({"features": "nope"}, account=ACCOUNT, container=CONTAINER)


async def test_stac_search_body_and_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("Ocp-Apim-Subscription-Key")
        return httpx.Response(200, json=collection(item("S1A_A", T0), item("S1A_B", T0)))

    async with _client(handler) as client:
        stac = StacClient(
            STAC,
            http=client,
            max_bytes=10**6,
            account=ACCOUNT,
            container=CONTAINER,
            subscription_key="k123",
        )
        page = await stac.search((-1.0, 39.0, 0.0, 40.0), T0, T0 + timedelta(days=1), limit=2)
    assert seen["url"] == f"{STAC}/search"
    assert seen["body"]["collections"] == ["sentinel-1-rtc"]
    assert seen["body"]["datetime"] == "2024-11-01T06:02:07Z/2024-11-02T06:02:07Z"
    assert seen["key"] == "k123"
    assert page.truncated and len(page.items) == 2


async def test_stac_item_refuses_bad_ids_without_a_request():
    calls = []
    async with _client(lambda r: calls.append(r) or httpx.Response(404)) as client:
        stac = StacClient(STAC, http=client, max_bytes=10**6, account=ACCOUNT, container=CONTAINER)
        assert await stac.item("../../etc/passwd") is None
    assert calls == []


def test_stac_client_refuses_other_hosts():
    with pytest.raises(ValueError):
        StacClient(
            "https://example.com/stac", http=None, max_bytes=1, account=ACCOUNT, container=CONTAINER
        )  # type: ignore[arg-type]


# ---------- tokens ----------


def _token_payload(
    expiry: datetime, token: str = "st=2026&se=2026&sp=rl&sv=2025&sr=c&sig=abc%3D"
) -> dict:
    return {"msft:expiry": expiry.isoformat().replace("+00:00", "Z"), "token": token}


async def test_token_is_cached_and_refreshed(caplog):
    now = [1_800_000_000.0]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        expiry = datetime.fromtimestamp(now[0] + 2700, UTC)
        return httpx.Response(200, json=_token_payload(expiry))

    caplog.set_level(logging.DEBUG)
    async with _client(handler) as client:
        tokens = SasTokenProvider(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token",
            "sentinel-1-rtc",
            http=client,
            max_bytes=10_000,
            clock=lambda: now[0],
        )
        first = await tokens.get(min_valid_s=300)
        again = await tokens.get(min_valid_s=300)
        now[0] += 2500  # 200 s left: below what's needed
        await tokens.get(min_valid_s=300)
    assert first is again
    assert len(calls) == 2
    assert str(calls[0].url).endswith("/token/sentinel-1-rtc")
    assert "sig=" not in repr(tokens) and "sig=" not in str(first) and "sig=" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"token": "x"},
        _token_payload(datetime.fromtimestamp(1_800_000_000 + 2700, UTC), token="st=1&se=2&sp=rl"),
        _token_payload(datetime.fromtimestamp(1_800_000_000 + 10, UTC)),
        _token_payload(datetime.fromtimestamp(1_800_000_000 + 30 * 86400, UTC)),
        _token_payload(datetime.fromtimestamp(1_800_000_000 + 2700, UTC), token="se=1&sig=a b<c>"),
    ],
)
async def test_token_rejects_bad_responses(payload):
    async with _client(lambda r: httpx.Response(200, json=payload)) as client:
        tokens = SasTokenProvider(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token",
            "sentinel-1-rtc",
            http=client,
            max_bytes=10_000,
            clock=lambda: 1_800_000_000.0,
        )
        with pytest.raises(UpstreamError):
            await tokens.get(min_valid_s=60)


# ---------- asset hrefs ----------


def test_blob_ref_of_a_real_href():
    ref = blob_ref(href("S1A_X"), account=ACCOUNT, container=CONTAINER)
    assert (
        vsiaz_path(ref)
        == f"/vsiaz/{CONTAINER}/GRD/2024/11/1/IW/DV/S1A_X/measurement/iw-vv.rtc.tiff"
    )
    assert "?" not in vsiaz_path(ref)


@pytest.mark.parametrize(
    "bad",
    [
        f"http://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a.tiff",
        f"https://other.blob.core.windows.net/{CONTAINER}/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net.evil.com/{CONTAINER}/a.tiff",
        f"https://u@{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net:444/{CONTAINER}/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a.tiff?sig=x",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a.tiff#x",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/../x/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/%2e%2e/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}//a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a\\b.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/other/a.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/a.png",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/é.tiff",
        f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/{'a' * 600}.tiff",
    ],
)
def test_blob_ref_refuses(bad):
    with pytest.raises(ValueError):
        blob_ref(bad, account=ACCOUNT, container=CONTAINER)

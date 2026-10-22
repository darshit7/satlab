"""SatNOGS DB client and frame archive, against a mocked API (the network is never used)."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.data.satnogs import SatnogsClient, SatnogsRateLimited, SatnogsUnavailable
from app.packets.ax25 import Address, UiFrame, encode_ui
from app.packets.table import CommsTable
from app.services.satnogs import SatnogsArchive
from tests.test_comms import TABLE

URL = "https://db.satnogs.org/api/telemetry/"
CROCUBE = 62394


def _frame(info: str, source: str = "9A0CC") -> str:
    return (
        encode_ui(UiFrame(Address("CQ"), Address(source), (), info.encode()), fcs=False)
        .hex()
        .upper()
    )


PSU = _frame("PSU,26,678,1688514,8197,2771,2520,210,113,7d,1,95")
OBC = _frame("OBC,140,653,20942,8251,2749,6024")


def _record(frame: str, observation: int = 1) -> dict:
    return {
        "sat_id": "XXXX",
        "norad_cat_id": CROCUBE,
        "frame": frame,
        "observation_id": observation,
        "station_id": 7,
        "timestamp": "2026-10-20T10:00:00Z",
        "decoded": "",
    }


def _client(handler, token: str | None = "secret-token") -> SatnogsClient:
    transport = httpx.MockTransport(handler)
    return SatnogsClient(
        URL,
        SecretStr(token) if token is not None else None,
        5.0,
        client=httpx.AsyncClient(transport=transport, follow_redirects=False),
    )


async def test_frames_with_token_and_paging() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "cursor" not in str(request.url):
            body = {"next": URL + "?cursor=abc", "results": [_record(PSU, 1), _record("zz", 2)]}
        else:
            body = {
                "next": None,
                "results": [_record(OBC, 3), {"frame": PSU, "observation_id": -5}],
            }
        return httpx.Response(200, json=body)

    frames = await _client(handler).frames(CROCUBE, max_pages=5, limit=100)
    assert [f.observation_id for f in frames] == [1, 3]  # non-hex and negative ids skipped
    assert frames[0].raw == bytes.fromhex(PSU) and frames[0].station_id == 7
    assert all(r.headers["Authorization"] == "Token secret-token" for r in seen)
    assert seen[0].url.params["satellite"] == str(CROCUBE)


async def test_next_url_elsewhere_is_not_followed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"next": "https://evil.example/steal", "results": [_record(PSU)]}
        )

    frames = await _client(handler).frames(CROCUBE, max_pages=5, limit=100)
    assert calls == 1 and len(frames) == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429, headers={"Retry-After": "5"}), SatnogsRateLimited),
        (httpx.Response(500), SatnogsUnavailable),
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), SatnogsUnavailable),
        (httpx.Response(200, content=b"<html>"), SatnogsUnavailable),
        (httpx.Response(200, content=b"[" + b"1," * 1_100_000 + b"1]"), SatnogsUnavailable),
        (httpx.Response(200, json={"results": "nope"}), SatnogsUnavailable),
    ],
)
async def test_failures_are_generic(response: httpx.Response, error: type[Exception]) -> None:
    with pytest.raises(error):
        await _client(lambda request: response).frames(CROCUBE, max_pages=1, limit=10)


async def test_rate_limit_wait_is_bounded() -> None:
    with pytest.raises(SatnogsRateLimited) as caught:
        await _client(lambda r: httpx.Response(429, headers={"Retry-After": "5"})).frames(
            CROCUBE, max_pages=1, limit=10
        )
    assert caught.value.retry_after_s == 60.0  # never hammer sooner than a minute


async def test_no_token_no_request() -> None:
    client = _client(lambda r: pytest.fail("no request without a token"), token=None)
    assert not client.configured
    with pytest.raises(SatnogsUnavailable):
        await client.frames(CROCUBE, max_pages=1, limit=10)


def test_only_https() -> None:
    with pytest.raises(ValueError):
        SatnogsClient("http://db.satnogs.org/api/telemetry/", SecretStr("t"), 5.0)


# ---------- the archive ----------


def _archive(
    tmp_path: Path, handler, token: str | None = "t", table: CommsTable = TABLE
) -> SatnogsArchive:
    settings = Settings(_env_file=None, cache_dir=tmp_path)  # type: ignore[call-arg]
    catalog = SimpleNamespace(fleet=SimpleNamespace(comms=table))
    return SatnogsArchive(settings, catalog, _client(handler, token))  # type: ignore[arg-type]


async def test_archive_identifies_and_caches(tmp_path: Path) -> None:
    other = _frame("PSU,1,2", source="N0CALL")  # someone else's frame: not CroCube's
    archive = _archive(
        tmp_path,
        lambda r: httpx.Response(
            200,
            json={"next": None, "results": [_record(PSU, 2), _record(OBC, 1), _record(other, 0)]},
        ),
    )
    assert archive.status(CROCUBE) == "empty"
    await archive.refresh(CROCUBE)
    assert archive.status(CROCUBE) == "ok"
    assert [f.observation_id for f in archive.frames(CROCUBE, "psu")] == [2]
    assert [f.observation_id for f in archive.frames(CROCUBE, "obc")] == [1]
    assert archive.frames(CROCUBE, "trx") == ()

    cached = json.loads((tmp_path / "satnogs" / f"{CROCUBE}.json").read_text())
    assert len(cached["frames"]) == 3  # raw frames are cached; identification redone on load

    reloaded = _archive(tmp_path, lambda r: pytest.fail("cached: no fetch"))
    reloaded._load_cache()
    reloaded._reidentify_if_templates_changed()
    assert [f.observation_id for f in reloaded.frames(CROCUBE, "psu")] == [2]


async def test_archive_status_without_token_and_on_failure(tmp_path: Path) -> None:
    assert (
        _archive(tmp_path, lambda r: httpx.Response(200), token=None).status(CROCUBE)
        == "not_configured"
    )
    failing = _archive(tmp_path, lambda r: httpx.Response(503))
    await failing.refresh(CROCUBE)
    assert failing.status(CROCUBE) == "unavailable"


async def test_archive_ignores_a_tampered_cache(tmp_path: Path) -> None:
    (tmp_path / "satnogs").mkdir()
    (tmp_path / "satnogs" / "62394.json").write_text(
        '{"norad_id": 62394, "fetched_at": 0, "frames": [{"hex": "zz"}]}'
    )
    archive = _archive(tmp_path, lambda r: httpx.Response(200))
    archive._load_cache()
    archive._reidentify_if_templates_changed()
    assert archive.frames(CROCUBE, "psu") == ()

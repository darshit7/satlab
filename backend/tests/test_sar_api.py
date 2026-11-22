"""SAR archive explore end to end: REST routes, the service, the job queue and the worker.

Planetary Computer is mocked (httpx.MockTransport); scene files are synthetic COGs under the
storage root, read through the same worker code (in a thread, plus one real spawned process).
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("rasterio")

from app.config import get_settings
from app.db.base import get_sessionmaker
from app.sar.grid import aoi_grid
from app.sar.jobs import JobRunner, QueueFull
from app.sar.params import change_detection_params
from app.sar.protocol import Stage, WorkerFrame, WorkerJob, WorkerResult
from app.services.sar import SarService
from tests.sar_helpers import collection, item, speckle, square, write_cog

LAT, LON = 39.415, -0.425
BEFORE = datetime(2024, 10, 20, 6, 2, 7, tzinfo=UTC)
AFTER = datetime(2024, 11, 1, 6, 2, 7, tzinfo=UTC)
FOOT = square(LON, LAT, 0.6)
ITEMS = [
    item("S1A_110_B", BEFORE, footprint=FOOT),
    item("S1A_110_A", AFTER, footprint=FOOT),
    item(
        "S1A_30_B",
        BEFORE + timedelta(days=6, hours=12),
        orbit=30,
        state="ascending",
        footprint=FOOT,
    ),
    item(
        "S1A_30_A", AFTER + timedelta(days=6, hours=12), orbit=30, state="ascending", footprint=FOOT
    ),
]


def _upstream(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            import json

            body = json.loads(request.content)
            start, end = (
                datetime.fromisoformat(v.replace("Z", "+00:00"))
                for v in body["datetime"].split("/")
            )
            chosen = [
                i
                for i in ITEMS
                if start
                <= datetime.fromisoformat(i["properties"]["datetime"].replace("Z", "+00:00"))
                <= end
            ]
            return httpx.Response(200, json=collection(*chosen))
        if "/items/" in request.url.path:
            wanted = request.url.path.rsplit("/", 1)[-1]
            found = next((i for i in ITEMS if i["id"] == wanted), None)
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        if "/token/" in request.url.path:
            expiry = (datetime.now(UTC) + timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200, json={"msft:expiry": expiry, "token": "st=1&se=2&sp=rl&sr=c&sig=abc"}
            )
        return httpx.Response(404)

    return handler


def _write_sources(root: Path) -> None:
    """Synthetic scenes covering the 5 km AOI: a -7 dB block appears between the dates."""
    grid = aoi_grid(LAT, LON, 5)
    size = grid.width + 100
    x_min, y_max = grid.x_min_m - 500, grid.y_max_m + 500
    field = np.full((size, size), 0.05, dtype=np.float32)
    flooded = field.copy()
    flooded[200:300, 250:400] *= 10 ** (-7 / 10)
    (root / "src").mkdir(parents=True, exist_ok=True)
    for stac_id, sigma, seed in (
        ("S1A_110_B", field, 1),
        ("S1A_110_A", flooded, 2),
        ("S1A_30_B", field, 3),
        ("S1A_30_A", field, 4),
    ):
        write_cog(
            root / "src" / f"{stac_id}.tif",
            speckle(field.shape, sigma, 4.4, seed),
            epsg=32630,
            x_min=x_min,
            y_max=y_max,
        )


@pytest.fixture
def sar(client, monkeypatch):
    """The app's SAR service rebuilt on mocked upstream and local scene files."""
    app = client.app
    client.portal.call(app.state.sar.stop)
    settings = get_settings()
    root = settings.sar_storage_dir.resolve()
    _write_sources(root)
    calls: list[str] = []
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(_upstream(calls)), follow_redirects=False
    )

    def source_path(asset_href: str) -> str:
        stac_id = asset_href.split("/")[-3]
        return str(root / "src" / f"{stac_id}.tif")

    sessions = app.state.sessions
    live: set[int] = set()
    monkeypatch.setattr(sessions, "is_live", lambda sid: sid in live)
    service = SarService(
        settings,
        get_sessionmaker(),
        app.state.catalog,
        sessions,
        http=http,
        executor_factory=lambda: ThreadPoolExecutor(max_workers=1),
        source_path=source_path,
    )
    client.portal.call(service.start)
    app.state.sar = service
    service.test_calls = calls  # type: ignore[attr-defined]
    service.test_live = live  # type: ignore[attr-defined]
    yield service
    client.portal.call(service.stop)


def _session(client, sar) -> tuple[int, dict[str, str]]:
    body = client.post("/api/sessions", json={"station_id": 1}).json()
    sar.test_live.add(body["id"])
    return body["id"], {"X-Session-Token": body["token"]}


def _search(client, sid, headers, **params):
    query = {
        "lat_deg": LAT,
        "lon_deg": LON,
        "size_km": 5,
        "before": "2024-10-20",
        "after": "2024-11-01",
    }
    query.update(params)
    return client.get(f"/api/sessions/{sid}/sar/scenes", params=query, headers=headers)


def _wait_ready(client, sid, headers, cid, timeout_s=60.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = client.get(f"/api/sessions/{sid}/sar/comparisons/{cid}", headers=headers).json()
        if out["status"] in ("ready", "failed", "expired"):
            return out
        time.sleep(0.2)
    raise AssertionError("comparison didn't finish")


def _compare(client, sid, headers, before=("S1A_110_B",), after=("S1A_110_A",)):
    return client.post(
        f"/api/sessions/{sid}/sar/comparisons",
        headers=headers,
        json={
            "aoi": {"lat_deg": LAT, "lon_deg": LON, "size_km": 5},
            "polarization": "VV",
            "before": list(before),
            "after": list(after),
            "requested_before": "2024-10-20",
            "requested_after": "2024-11-01",
        },
    )


def test_source_reference(client, sar):
    out = client.get("/api/sar/source").json()
    assert out["collection"] == "sentinel-1-rtc" and out["license"] == "CC BY 4.0"
    assert any("Copernicus" in line for line in out["attribution"])
    assert out["min_abs_db"] == change_detection_params().threshold.min_abs_db
    assert set(out["display"]) == {"VV", "VH"}


def test_routes_need_the_session_token(client, sar):
    sid, headers = _session(client, sar)
    for path in (f"/api/sessions/{sid}/sar/comparisons", f"/api/sessions/{sid}/sar/comparisons/1"):
        assert client.get(path).status_code == 404
        assert client.get(path, headers={"X-Session-Token": "wrong"}).status_code == 404
    assert _search(client, sid, {"X-Session-Token": "wrong"}).status_code == 404
    assert _search(client, 9999, headers).status_code == 404


def test_search_proposes_the_same_track_pair(client, sar):
    sid, headers = _session(client, sar)
    out = _search(client, sid, headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["proposed_track"] == "110-descending"
    first = body["tracks"][0]
    assert first["key"] == "110-descending"
    assert (first["proposed_before"], first["proposed_after"]) == ("S1A_110_B", "S1A_110_A")
    assert {t["key"] for t in body["tracks"]} == {"110-descending", "30-ascending"}
    assert body["aoi"]["epsg"] == 32630 and body["aoi"]["width_px"] == 500
    # the second identical search comes from the cache
    searches = sar.test_calls.count("/api/stac/v1/search")
    _search(client, sid, headers)
    assert sar.test_calls.count("/api/stac/v1/search") == searches


@pytest.mark.parametrize(
    "params",
    [
        {"before": "2024-11-01", "after": "2024-10-20"},
        {"before": "2013-01-01"},
        {"size_km": 7},
        {"lat_deg": 85},
        {"platform": "landsat-9"},
        {"polarization": "HH"},
    ],
)
def test_search_validation(client, sar, params):
    sid, headers = _session(client, sar)
    assert _search(client, sid, headers, **params).status_code == 422


def test_search_needs_a_live_session(client, sar):
    sid, headers = _session(client, sar)
    sar.test_live.discard(sid)
    assert _search(client, sid, headers).status_code == 409


def test_search_budget(client, sar, monkeypatch):
    sid, headers = _session(client, sar)
    codes = [_search(client, sid, headers, size_km=s).status_code for s in (5, 10, 20, 5, 10)]
    assert codes.count(429) >= 1
    limited = _search(client, sid, headers)
    if limited.status_code == 429:
        assert "Retry-After" in limited.headers


def test_compare_flags_the_planted_flood(client, sar):
    sid, headers = _session(client, sar)
    _search(client, sid, headers)
    created = _compare(client, sid, headers)
    assert created.status_code == 202, created.text
    cid = created.json()["id"]
    out = _wait_ready(client, sid, headers, cid)
    assert out["status"] == "ready", out
    assert out["coverage"] > 0.95
    decrease = [r for r in out["regions"] if r["sign"] == "decrease"]
    assert len(decrease) == 1 and abs(decrease[0]["mean_db"] + 7.0) < 1.5
    assert decrease[0]["area_m2"] == pytest.approx(100 * 150 * 100.0, rel=0.25)
    assert out["attribution"] == "Contains modified Copernicus Sentinel data 2024"
    assert out["before"]["satellite_name"] == "SENTINEL-1A"
    for kind in ("before", "after", "change"):
        image = client.get(
            f"/api/sessions/{sid}/sar/comparisons/{cid}/images/{kind}.png", headers=headers
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.headers["cache-control"] == "private, no-store"
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    # another session can't see it
    other, other_headers = _session(client, sar)
    assert (
        client.get(
            f"/api/sessions/{other}/sar/comparisons/{cid}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/sessions/{other}/sar/comparisons/{cid}/images/change.png", headers=other_headers
        ).status_code
        == 404
    )
    listed = client.get(f"/api/sessions/{sid}/sar/comparisons", headers=headers).json()
    assert [c["id"] for c in listed["comparisons"]] == [cid]


def test_expired_then_recomputed(client, sar):
    sid, headers = _session(client, sar)
    cid = _compare(client, sid, headers).json()["id"]
    assert _wait_ready(client, sid, headers, cid)["status"] == "ready"
    sar.storage.remove_comparison(cid)
    out = client.get(f"/api/sessions/{sid}/sar/comparisons/{cid}", headers=headers).json()
    assert out["status"] == "expired"
    assert (
        client.get(
            f"/api/sessions/{sid}/sar/comparisons/{cid}/images/before.png", headers=headers
        ).status_code
        == 404
    )
    again = client.post(f"/api/sessions/{sid}/sar/comparisons/{cid}/recompute", headers=headers)
    assert again.status_code == 202
    out = _wait_ready(client, sid, headers, cid)
    assert out["status"] == "ready" and out["generation"] == 2
    assert (
        client.post(
            f"/api/sessions/{sid}/sar/comparisons/{cid}/recompute", headers=headers
        ).status_code
        == 409
    )


def test_compare_validation(client, sar):
    sid, headers = _session(client, sar)
    cross_track = _compare(client, sid, headers, before=("S1A_110_B",), after=("S1A_30_A",))
    assert cross_track.status_code == 422
    reversed_pair = _compare(client, sid, headers, before=("S1A_110_A",), after=("S1A_110_B",))
    assert reversed_pair.status_code == 422
    unknown = _compare(client, sid, headers, before=("S1A_NOPE",), after=("S1A_110_A",))
    assert unknown.status_code == 422
    hostile = _compare(client, sid, headers, before=("../etc",), after=("S1A_110_A",))
    assert hostile.status_code == 422


def test_delete_and_session_cap(client, sar, monkeypatch):
    sid, headers = _session(client, sar)
    monkeypatch.setattr(sar.settings, "sar_max_comparisons_per_session", 1)
    cid = _compare(client, sid, headers).json()["id"]
    _wait_ready(client, sid, headers, cid)
    assert _compare(client, sid, headers).status_code == 409
    assert (
        client.delete(f"/api/sessions/{sid}/sar/comparisons/{cid}", headers=headers).status_code
        == 204
    )
    assert not sar.storage.products_dir(cid, 1).exists()
    assert (
        client.get(f"/api/sessions/{sid}/sar/comparisons/{cid}", headers=headers).status_code == 404
    )


def test_session_end_fails_waiting_comparisons_and_purge_removes_them(client, sar, monkeypatch):
    sid, headers = _session(client, sar)
    cid = _compare(client, sid, headers).json()["id"]
    _wait_ready(client, sid, headers, cid)
    assert client.post(f"/api/sessions/{sid}/end", headers=headers).status_code == 204
    from app.db.models import MissionSession

    with get_sessionmaker()() as db:
        session = db.get(MissionSession, sid)
        session.ended_at = datetime.now(UTC) - timedelta(days=30)
        db.commit()
    assert sar.purge() >= 1
    assert not sar.storage.products_dir(cid, 1).exists()


def test_errors_carry_no_secrets(client, sar):
    sid, headers = _session(client, sar)
    out = _compare(client, sid, headers, before=("S1A_NOPE",), after=("S1A_110_A",)).text
    assert "sig=" not in out and "blob.core" not in out


# ---------- the queue ----------


def _job(tmp_path: Path, cid: int) -> WorkerJob:
    return WorkerJob(
        comparison_id=cid,
        generation=1,
        grid=aoi_grid(LAT, LON, 5).to_dict(),
        polarization="VV",
        before=(),
        after=(),
        products_dir=str(tmp_path / str(cid)),
        storage_root=str(tmp_path),
        params={},
    )


async def test_runner_is_fifo_with_a_cap():
    order: list[int] = []

    async def process(cid: int) -> None:
        order.append(cid)
        await asyncio.sleep(0.01)

    runner = JobRunner(queue_max=2, timeout_s=5, nice=0, process=process)
    runner.submit(1, 10)
    runner.submit(2, 11)
    with pytest.raises(QueueFull):
        runner.submit(3, 12)
    assert runner.position(2) == 2
    runner.start()
    await asyncio.sleep(0.1)
    assert order == [1, 2]
    runner.submit(4, 10)
    runner.submit(5, 11)
    dropped = runner.cancel_session(10)
    assert 4 in dropped or order[-1] == 4
    await runner.stop()


async def test_runner_timeout(tmp_path, monkeypatch):
    import app.sar.jobs as jobs

    def slow(job):
        time.sleep(1.0)
        return WorkerResult(ok=True)

    monkeypatch.setattr(jobs, "run_comparison", slow)

    async def on_stage(stage: Stage) -> None:
        pass

    runner = JobRunner(
        queue_max=2,
        timeout_s=0.2,
        nice=0,
        process=lambda cid: asyncio.sleep(0),
        executor_factory=lambda: ThreadPoolExecutor(max_workers=1),
    )
    result = await runner.execute(_job(tmp_path, 1), on_stage)
    assert result.error_code == "timeout"


async def test_real_worker_process_is_spawned_and_niced(tmp_path):
    """One real spawn: the worker reads a synthetic pass and reports through the shared values."""
    _write_sources(tmp_path)
    grid = aoi_grid(LAT, LON, 5)

    def frame(stac_id: str) -> WorkerFrame:
        return WorkerFrame(
            stac_id=stac_id,
            path=str(tmp_path / "src" / f"{stac_id}.tif"),
            expected_epsg=32630,
            crop_path=str(tmp_path / f"{stac_id}.crop.tif"),
        )

    job = WorkerJob(
        comparison_id=1,
        generation=1,
        grid=grid.to_dict(),
        polarization="VV",
        before=(frame("S1A_110_B"),),
        after=(frame("S1A_110_A"),),
        products_dir=str(tmp_path / "out"),
        storage_root=str(tmp_path),
        params=change_detection_params().model_dump(by_alias=True),
    )
    stages: list[Stage] = []

    async def on_stage(stage: Stage) -> None:
        stages.append(stage)

    runner = JobRunner(queue_max=1, timeout_s=120, nice=5, process=lambda cid: asyncio.sleep(0))
    result = await runner.execute(job, on_stage)
    assert result.ok, result
    assert result.peak_rss_mb and result.peak_rss_mb > 0
    assert (tmp_path / "out" / "change.png").is_file()
    assert any(r["sign"] == "decrease" for r in result.regions)
    assert os.getpriority(os.PRIO_PROCESS, 0) == 0  # this process kept its priority

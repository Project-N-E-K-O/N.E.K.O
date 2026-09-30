from __future__ import annotations

import asyncio

import httpx
import pytest

from plugin.server.routes import market_bridge

pytestmark = pytest.mark.plugin_unit


def payload(mode="install"):
    return market_bridge.MarketInstallRequest(
        plugin_id="42", version="1.0.0", channel="stable", mode=mode,
        package_url="https://proxy.example/package.neko-plugin",
        canonical_package_url="https://github.com/example/plugin/releases/download/v1/package.neko-plugin",
        package_sha256="a" * 64,
        published_at="old client timestamp", payload_hash="old client metadata",
    )


def catalog(monkeypatch, releases, status=200):
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=releases)

    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(
        market_bridge.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


def release():
    return dict(
        version="1.0.0", channel="stable", package_sha256="A" * 64,
        yanked_at=None, verification_status="unverified",
    )


@pytest.mark.asyncio
async def test_catalog_hash_authorizes_proxy_and_legacy_release(monkeypatch):
    requests = catalog(monkeypatch, [release()])
    request = payload()
    bound = await market_bridge._bind_market_package_hash(request)
    assert bound.package_sha256 == "a" * 64
    assert bound.package_url == request.package_url
    assert bound.canonical_package_url == request.canonical_package_url
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/plugins/42/versions"
    assert requests[0].url.params["include_yanked"] == "false"


@pytest.mark.parametrize("changes", [
    {"package_sha256": "b" * 64}, {"package_sha256": None},
    {"package_sha256": 123}, {"package_sha256": "0" * 64},
    {"version": "2.0.0"}, {"channel": "beta"},
    {"yanked_at": "2026-09-30T00:00:00Z"},
])
@pytest.mark.asyncio
async def test_unlisted_or_mismatched_release_rejected(monkeypatch, changes):
    entry = release()
    entry.update(changes)
    catalog(monkeypatch, [entry])
    with pytest.raises(market_bridge._TaskError, match="market_release_mismatch"):
        await market_bridge._bind_market_package_hash(payload())


@pytest.mark.parametrize("status,body,code", [
    (404, [], "market_release_mismatch"),
    (503, [], "market_catalog_unavailable"),
    (302, [], "market_catalog_unavailable"),
    (200, {}, "market_catalog_unavailable"),
    (200, [], "market_release_mismatch"),
])
@pytest.mark.asyncio
async def test_catalog_errors_fail_closed(monkeypatch, status, body, code):
    catalog(monkeypatch, body, status)
    with pytest.raises(market_bridge._TaskError, match=code):
        await market_bridge._bind_market_package_hash(payload())


@pytest.mark.asyncio
async def test_total_timeout_releases_other_async_work(monkeypatch):
    started = asyncio.Event()
    closed = asyncio.Event()
    other_work_ran = asyncio.Event()
    original_client = httpx.AsyncClient

    async def stalled(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def other_work():
        await started.wait()
        other_work_ran.set()

    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge, "_MARKET_RELEASE_CHECK_TIMEOUT", 0.05)
    monkeypatch.setattr(market_bridge.httpx, "AsyncClient", lambda **kwargs:
        original_client(transport=httpx.MockTransport(stalled), **kwargs))
    concurrent = asyncio.create_task(other_work())
    try:
        with pytest.raises(market_bridge._TaskError, match="market_catalog_unavailable"):
            await market_bridge._bind_market_package_hash(payload())
        assert other_work_ran.is_set()
        assert closed.is_set()
    finally:
        await concurrent


@pytest.mark.parametrize("mode", ["install", "upgrade", "reinstall", "override_builtin"])
@pytest.mark.asyncio
async def test_every_market_mode_rejects_before_download_or_install(monkeypatch, mode):
    entry = release()
    entry["package_sha256"] = "b" * 64
    catalog(monkeypatch, [entry])

    async def forbidden(*args, **kwargs):
        pytest.fail("untrusted task reached download/install")

    monkeypatch.setattr(market_bridge, "_do_install", forbidden)
    monkeypatch.setattr(market_bridge, "_do_upgrade", forbidden)
    task = {"cancel_requested": False}
    monkeypatch.setattr(market_bridge, "_tasks", {"test": task})
    await market_bridge._execute_install("test", payload(mode))
    assert task["status"] == "failed"
    assert task["error_code"] == "market_release_mismatch"


@pytest.mark.parametrize("mode", ["install", "upgrade", "reinstall", "override_builtin"])
@pytest.mark.asyncio
async def test_valid_catalog_hash_reaches_each_mode(monkeypatch, mode):
    catalog(monkeypatch, [release()])
    seen = []

    async def install(task, bound, log_ctx, **kwargs):
        seen.append(bound)

    async def report(*args):
        pass

    monkeypatch.setattr(market_bridge, "_do_install", install)
    monkeypatch.setattr(market_bridge, "_do_upgrade", install)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    task = {"cancel_requested": False}
    monkeypatch.setattr(market_bridge, "_tasks", {"test": task})
    await market_bridge._execute_install("test", payload(mode))
    assert task["status"] == "completed"
    assert len(seen) == 1
    assert seen[0].package_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_endpoint_returns_task_before_catalog_and_cancellation_prevents_install(monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocked_catalog(request):
        started.set()
        await finish.wait()
        return request

    async def forbidden(*args, **kwargs):
        pytest.fail("canceled task reached install")

    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_verify_token", lambda token: None)
    monkeypatch.setattr(market_bridge, "_bind_market_package_hash", blocked_catalog)
    monkeypatch.setattr(market_bridge, "_do_install", forbidden)
    response = await market_bridge.market_install(payload(), token="test")
    worker = market_bridge._task_workers[response.task_id]
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert not worker.done()
        assert market_bridge._tasks[response.task_id]["stage"] == "pending"
        market_bridge._tasks[response.task_id]["cancel_requested"] = True
        finish.set()
        await worker
        assert market_bridge._tasks[response.task_id]["status"] == "canceled"
    finally:
        finish.set()
        await worker

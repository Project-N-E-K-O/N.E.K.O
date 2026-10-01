from __future__ import annotations

import asyncio

import httpx
import pytest

from plugin.server.routes import market_bridge

pytestmark = pytest.mark.plugin_unit


CANONICAL_URL = "https://github.com/example/plugin/releases/download/v1/package.neko-plugin"
CATALOG_PAYLOAD_HASH = "c" * 64
CATALOG_CREATED_AT = "2026-09-01T00:00:00Z"


def payload(mode="install", **changes):
    fields = dict(
        plugin_id="42", version="1.0.0", channel="stable", mode=mode,
        package_url="https://proxy.example/package.neko-plugin",
        canonical_package_url=CANONICAL_URL,
        package_sha256="a" * 64,
        published_at="stale client timestamp", payload_hash=CATALOG_PAYLOAD_HASH.upper(),
    )
    fields.update(changes)
    return market_bridge.MarketInstallRequest(**fields)


def catalog(monkeypatch, releases, status=200):
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        channel = request.url.params.get("channel")
        body = releases
        if isinstance(releases, list) and channel is not None:
            body = [r for r in releases if str(r.get("channel") or "stable").strip() == channel]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(
        market_bridge.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


def release():
    return dict(
        version="1.0.0", channel="stable", package_sha256="A" * 64,
        package_url=CANONICAL_URL, payload_hash=CATALOG_PAYLOAD_HASH,
        created_at=CATALOG_CREATED_AT,
        yanked_at=None, verification_status="unverified",
    )


@pytest.mark.asyncio
async def test_catalog_hash_authorizes_proxy_and_legacy_release(monkeypatch):
    requests = catalog(monkeypatch, [release()])
    request = payload()
    bound, row = await market_bridge._bind_market_package_hash(request)
    assert row["package_url"] == CANONICAL_URL
    assert bound.package_sha256 == "a" * 64
    assert bound.package_url == request.package_url
    assert bound.canonical_package_url == CANONICAL_URL
    assert bound.payload_hash == CATALOG_PAYLOAD_HASH
    assert bound.published_at == CATALOG_CREATED_AT
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/plugins/42/versions"
    assert requests[0].url.params["include_yanked"] == "false"


@pytest.mark.asyncio
async def test_catalog_fills_provenance_the_caller_omitted(monkeypatch):
    catalog(monkeypatch, [release()])
    bound, _ = await market_bridge._bind_market_package_hash(payload(
        channel=None, canonical_package_url=None, payload_hash=None, published_at=None,
    ))
    assert bound.channel == "stable"
    assert bound.canonical_package_url == CANONICAL_URL
    assert bound.payload_hash == CATALOG_PAYLOAD_HASH
    assert bound.published_at == CATALOG_CREATED_AT


@pytest.mark.asyncio
async def test_caller_payload_hash_must_match_catalog(monkeypatch):
    catalog(monkeypatch, [release()])
    with pytest.raises(market_bridge._TaskError, match="market_release_mismatch"):
        await market_bridge._bind_market_package_hash(payload(payload_hash="d" * 64))


@pytest.mark.asyncio
async def test_stale_caller_canonical_url_is_replaced_by_catalog(monkeypatch):
    # The catalogue may move a release to a new URL while the page still
    # holds the old one; the hash already binds the bytes.
    catalog(monkeypatch, [release()])
    bound, _ = await market_bridge._bind_market_package_hash(payload(
        canonical_package_url="https://github.com/example/old-name/releases/download/v1/package.neko-plugin",
    ))
    assert bound.canonical_package_url == CANONICAL_URL


@pytest.mark.parametrize("changes", [{"payload_hash": None}, {"package_url": " "}])
@pytest.mark.asyncio
async def test_catalog_row_missing_provenance_rejected(monkeypatch, changes):
    entry = release()
    entry.update(changes)
    catalog(monkeypatch, [entry])
    with pytest.raises(market_bridge._TaskError, match="market_release_mismatch"):
        await market_bridge._bind_market_package_hash(payload())


def _override_payload(**changes):
    fields = dict(published_at=CATALOG_CREATED_AT, payload_hash=CATALOG_PAYLOAD_HASH)
    fields.update(changes)
    return payload("override_builtin", **fields)


@pytest.mark.parametrize("changes,accepted", [
    ({}, True),
    ({"channel": None}, True),
    ({"version": " 1.0.0 ", "channel": " stable "}, True),
    ({"yanked_at": "2026-09-30T00:00:00Z"}, False),
])
@pytest.mark.asyncio
async def test_override_preflight_and_task_select_the_same_row(monkeypatch, changes, accepted):
    entry = release()
    entry.update(changes)
    catalog(monkeypatch, [entry])
    request = _override_payload()
    if accepted:
        await market_bridge._fetch_authoritative_market_override_release(request)
        bound, row = await market_bridge._bind_market_package_hash(request)
        assert bound.version == "1.0.0"
        assert bound.channel == "stable"
        # The task-side upgrade path checks the bound payload against the same row.
        market_bridge._market_override_release_evidence(bound, row)
    else:
        with pytest.raises(market_bridge.HTTPException) as exc_info:
            await market_bridge._fetch_authoritative_market_override_release(request)
        assert exc_info.value.detail["code"] == "market_release_mismatch"
        with pytest.raises(market_bridge._TaskError, match="market_release_mismatch"):
            await market_bridge._bind_market_package_hash(request)


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
    (404, {"detail": "插件不存在"}, "market_release_mismatch"),
    (422, {"detail": [{"loc": ["path", "plugin_id"]}]}, "market_release_mismatch"),
    # A wrong base path or an old Market without the route.
    (404, {"detail": "Not Found"}, "market_catalog_unavailable"),
    (404, [], "market_catalog_unavailable"),
    (200, "<!doctype html>", "market_catalog_unavailable"),
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
        return request, release()

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


@pytest.mark.parametrize("channel,expected", [(None, "beta"), ("", "beta"), ("beta", "beta")])
@pytest.mark.asyncio
async def test_missing_channel_matches_release_on_either_channel(monkeypatch, channel, expected):
    beta = release()
    beta.update(version="1.1.0b1", channel="beta")
    requests = catalog(monkeypatch, [release(), beta])
    bound, _ = await market_bridge._bind_market_package_hash(
        payload(version="1.1.0b1", channel=channel),
    )
    assert bound.channel == expected
    assert requests[0].url.params.get("channel") == (channel or None)


@pytest.mark.asyncio
async def test_missing_channel_prefers_row_with_requested_hash(monkeypatch):
    stable = release()
    beta = release()
    beta.update(channel="beta", package_sha256="b" * 64)
    catalog(monkeypatch, [stable, beta])
    bound, _ = await market_bridge._bind_market_package_hash(
        payload(channel=None, package_sha256="b" * 64),
    )
    assert bound.channel == "beta"
    assert bound.package_sha256 == "b" * 64


@pytest.mark.parametrize("changes", [
    {"plugin_id": None}, {"plugin_id": " "}, {"version": ""}, {"channel": "nightly"},
])
@pytest.mark.asyncio
async def test_install_endpoint_rejects_unbindable_request_before_task(monkeypatch, changes):
    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_verify_token", lambda token: None)
    with pytest.raises(market_bridge.HTTPException) as exc_info:
        await market_bridge.market_install(payload(**changes), token="test")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "market_release_mismatch"
    assert market_bridge._tasks == {}


@pytest.mark.parametrize("mode", ["upgrade", "reinstall"])
@pytest.mark.asyncio
async def test_task_passes_bound_release_to_upgrade(monkeypatch, mode):
    requests = catalog(monkeypatch, [release()])
    seen = []

    async def upgrade(task, bound, log_ctx, **kwargs):
        seen.append(kwargs["market_release"])

    async def report(*args):
        pass

    monkeypatch.setattr(market_bridge, "_do_upgrade", upgrade)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    task = {"cancel_requested": False}
    monkeypatch.setattr(market_bridge, "_tasks", {"test": task})
    await market_bridge._execute_install("test", payload(mode))
    assert task["status"] == "completed"
    assert seen == [release()]
    assert len(requests) == 1

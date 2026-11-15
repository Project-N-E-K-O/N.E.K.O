"""Successful plugin-center downloads count on Market without a login."""

from __future__ import annotations

import asyncio
import errno
from inspect import CORO_CLOSED, getcoroutinestate
from types import SimpleNamespace
from uuid import UUID

import pytest

from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _clear_download_count_tasks():
    market_bridge._download_count_tasks.clear()
    yield
    market_bridge._download_count_tasks.clear()


def _download_params(**extra):
    params = {
        "source": "client",
        "version": "1.0.0",
        "package_sha256": "ab" * 32,
    }
    params.update(extra)
    return params


def _payload(**overrides):
    body = {
        "package_url": "https://example.test/demo.neko-plugin",
        "package_sha256": "ab" * 32,
        "plugin_id": "21",
        "version": "1.0.0",
    }
    body.update(overrides)
    return market_bridge.MarketInstallRequest(**body)


def _install_market_client(monkeypatch, post):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, params=None):
            return await post(url, params)

    monkeypatch.setattr(market_bridge, "_market_catalog_client", _Client)
    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "https://market.test/")


async def _drain_download_tasks() -> None:
    pending = list(market_bridge._download_count_tasks)
    if pending:
        await asyncio.gather(*pending)


@pytest.mark.parametrize(
    ("mode", "operation"),
    [
        ("install", "install"),
        ("upgrade", "upgrade"),
        ("reinstall", "reinstall"),
        ("override_builtin", "install"),
    ],
)
async def test_successful_client_operation_counts_one_download(
    monkeypatch, mode, operation
) -> None:
    calls = []

    async def post(url, params=None):
        calls.append((url, params))

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(_payload(mode=mode), "task-21")
    await _drain_download_tasks()

    [(url, params)] = calls
    assert url == "https://market.test/api/v1/plugins/21/download"
    event_id = params.pop("event_id")
    assert UUID(event_id).version == 5
    assert params == _download_params(operation=operation)

    calls.clear()
    market_bridge._record_market_download_best_effort(_payload(mode=mode), "task-21")
    await _drain_download_tasks()
    assert calls == [(url, _download_params(operation=operation, event_id=event_id))]


def test_unpaired_surrogate_pairing_code_is_rejected() -> None:
    assert market_bridge._consume_one_time_code("\ud800") is False


async def test_unicode_digits_are_not_a_market_plugin_id(monkeypatch) -> None:
    calls = []

    async def post(url, params=None):
        calls.append(url)

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(_payload(plugin_id="²1"), "task-21")
    await _drain_download_tasks()

    assert calls == []


async def test_slow_download_count_stops_at_the_total_timeout(monkeypatch) -> None:
    monkeypatch.setattr(market_bridge, "_MARKET_RELEASE_CHECK_TIMEOUT", 0.05)

    async def post(url, params=None):
        await asyncio.sleep(5)

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(_payload(plugin_id="24"), "task-24")
    await asyncio.wait_for(_drain_download_tasks(), timeout=1)
    assert market_bridge._download_count_tasks == set()


async def test_non_market_plugin_id_is_not_counted(monkeypatch) -> None:
    calls = []

    async def post(url, params=None):
        calls.append(url)

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(
        _payload(plugin_id="war-thunder-mod"), "task-non-market"
    )
    await _drain_download_tasks()

    assert calls == []


async def test_market_rejection_or_outage_does_not_fail_the_count_helper(
    monkeypatch,
) -> None:
    calls = []

    async def reject(url, params=None):
        calls.append(url)

        class Response:
            status_code = 429

        return Response()

    _install_market_client(monkeypatch, reject)
    market_bridge._record_market_download_best_effort(_payload(plugin_id="22"), "task-22")
    await _drain_download_tasks()

    async def offline(url, params=None):
        calls.append("offline")
        raise ConnectionError("market down")

    _install_market_client(monkeypatch, offline)
    market_bridge._record_market_download_best_effort(_payload(plugin_id="23"), "task-23")
    await _drain_download_tasks()

    assert calls == [
        "https://market.test/api/v1/plugins/22/download",
        "offline",
    ]
    assert market_bridge._download_count_tasks == set()


@pytest.mark.parametrize(
    ("mode", "handler"),
    [
        ("install", "_do_install"),
        ("upgrade", "_do_upgrade"),
        ("reinstall", "_do_upgrade"),
        ("override_builtin", "_do_install"),
    ],
)
async def test_failed_or_cancelled_operation_does_not_count(
    monkeypatch, mode, handler
) -> None:
    counted = []

    async def bind(payload):
        return payload, {}

    async def fail(_task, _payload, _log_ctx, **_kwargs):
        raise market_bridge._TaskError(code="verify_failed", message="bad package")

    def count(payload, _task_id):
        counted.append(("count", payload.plugin_id))

    async def report(_payload, _task):
        return None

    monkeypatch.setattr(market_bridge, "_bind_market_package_hash", bind)
    monkeypatch.setattr(market_bridge, handler, fail)
    monkeypatch.setattr(market_bridge, "_record_market_download_best_effort", count)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    market_bridge._tasks["task-fail"] = {}
    try:
        await market_bridge._execute_install("task-fail", _payload(mode=mode))
        assert counted == []
        assert market_bridge._tasks["task-fail"]["status"] == "failed"
    finally:
        market_bridge._tasks.pop("task-fail", None)

    async def cancel(_task, _payload, _log_ctx, **_kwargs):
        raise market_bridge._TaskCancelled(code="cancelled", message="stopped")

    monkeypatch.setattr(market_bridge, handler, cancel)
    market_bridge._tasks["task-cancel"] = {}
    try:
        await market_bridge._execute_install("task-cancel", _payload(mode=mode))
        assert counted == []
        assert market_bridge._tasks["task-cancel"]["status"] == "canceled"
    finally:
        market_bridge._tasks.pop("task-cancel", None)


@pytest.mark.parametrize(
    ("mode", "handler"),
    [
        ("install", "_do_install"),
        ("upgrade", "_do_upgrade"),
        ("reinstall", "_do_upgrade"),
        ("override_builtin", "_do_install"),
    ],
)
@pytest.mark.parametrize("download_scheduling_fails", [False, True])
async def test_successful_task_counts_before_the_login_report(
    monkeypatch, mode, handler, download_scheduling_fails
) -> None:
    order = []
    unscheduled = []
    create_task = asyncio.create_task

    async def bind(payload):
        return payload, {}

    async def install_ok(task, _payload, _log_ctx, **_kwargs):
        task["message"] = "installed"
        task["result"] = {"install": {}}

    async def post(*_args, **_kwargs):
        return None

    def schedule(coro, *, name=None):
        if name == "market-download-count":
            assert market_bridge._tasks["task-ok"]["status"] == "completed"
            order.append("count")
            if download_scheduling_fails:
                unscheduled.append(coro)
                raise RuntimeError("could not schedule download report")
        return create_task(coro, name=name)

    async def report(_payload, task):
        assert task["status"] == "completed"
        order.append("report")

    monkeypatch.setattr(market_bridge, "_bind_market_package_hash", bind)
    monkeypatch.setattr(market_bridge, handler, install_ok)
    monkeypatch.setattr(market_bridge, "_post_market_download_count", post)
    monkeypatch.setattr(asyncio, "create_task", schedule)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    market_bridge._tasks["task-ok"] = {}
    try:
        await market_bridge._execute_install("task-ok", _payload(mode=mode))
        await _drain_download_tasks()
        assert order == ["count", "report"]
        assert market_bridge._tasks["task-ok"]["status"] == "completed"
        if download_scheduling_fails:
            [coro] = unscheduled
            assert getcoroutinestate(coro) == CORO_CLOSED
    finally:
        market_bridge._tasks.pop("task-ok", None)


@pytest.mark.parametrize("failure_point", ["_ensure_valid_oauth_token", "ensure_httpx"])
async def test_install_report_handles_preparation_errors(monkeypatch, failure_point) -> None:
    messages = []

    def warn(message, *args):
        messages.append(message.format(*args))

    async def token():
        return {"access_token": "test-token"}

    async def fail():
        raise PermissionError(errno.EACCES, "could not read credentials", "/private/auth.json")

    monkeypatch.setattr(market_bridge, "logger", SimpleNamespace(warning=warn))
    monkeypatch.setattr(market_bridge, "_ensure_valid_oauth_token", token)
    monkeypatch.setattr(market_bridge, failure_point, fail)

    await market_bridge._report_market_install_best_effort(_payload(), {})

    assert messages == [
        f"[market-install-report] unexpected failure error=PermissionError errno={errno.EACCES}"
    ]

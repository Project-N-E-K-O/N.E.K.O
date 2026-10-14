"""A client first install counts a Market download without a login."""

from __future__ import annotations

import asyncio

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


async def test_successful_client_install_counts_one_download(monkeypatch) -> None:
    calls = []

    async def post(url, params=None):
        calls.append((url, params))

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(_payload())
    await _drain_download_tasks()

    assert calls == [
        ("https://market.test/api/v1/plugins/21/download", _download_params())
    ]


@pytest.mark.parametrize("mode", ["upgrade", "reinstall", "override_builtin"])
async def test_replacing_an_installed_plugin_is_not_counted(
    monkeypatch, mode
) -> None:
    calls = []

    async def post(url, params=None):
        calls.append(url)

        class Response:
            status_code = 200

        return Response()

    _install_market_client(monkeypatch, post)
    market_bridge._record_market_download_best_effort(_payload(mode=mode))
    await _drain_download_tasks()

    assert calls == []


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
    market_bridge._record_market_download_best_effort(_payload(plugin_id="²1"))
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
    market_bridge._record_market_download_best_effort(_payload(plugin_id="24"))
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
        _payload(plugin_id="war-thunder-mod")
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
    market_bridge._record_market_download_best_effort(_payload(plugin_id="22"))
    await _drain_download_tasks()

    async def offline(url, params=None):
        calls.append("offline")
        raise ConnectionError("market down")

    _install_market_client(monkeypatch, offline)
    market_bridge._record_market_download_best_effort(_payload(plugin_id="23"))
    await _drain_download_tasks()

    assert calls == [
        "https://market.test/api/v1/plugins/22/download",
        "offline",
    ]
    assert market_bridge._download_count_tasks == set()


async def test_failed_or_cancelled_install_does_not_count(monkeypatch) -> None:
    counted = []

    async def bind(payload):
        return payload, {}

    async def fail(_task, _payload, _log_ctx):
        raise market_bridge._TaskError(code="verify_failed", message="bad package")

    def count(payload):
        counted.append(("count", payload.plugin_id))

    async def report(_payload, _task):
        return None

    monkeypatch.setattr(market_bridge, "_bind_market_package_hash", bind)
    monkeypatch.setattr(market_bridge, "_do_install", fail)
    monkeypatch.setattr(market_bridge, "_record_market_download_best_effort", count)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    market_bridge._tasks["task-fail"] = {}
    try:
        await market_bridge._execute_install("task-fail", _payload())
        assert counted == []
        assert market_bridge._tasks["task-fail"]["status"] == "failed"
    finally:
        market_bridge._tasks.pop("task-fail", None)

    async def cancel(_task, _payload, _log_ctx):
        raise market_bridge._TaskCancelled(code="cancelled", message="stopped")

    monkeypatch.setattr(market_bridge, "_do_install", cancel)
    market_bridge._tasks["task-cancel"] = {}
    try:
        await market_bridge._execute_install("task-cancel", _payload())
        assert counted == []
        assert market_bridge._tasks["task-cancel"]["status"] == "canceled"
    finally:
        market_bridge._tasks.pop("task-cancel", None)


async def test_successful_install_task_counts_before_the_login_report(
    monkeypatch,
) -> None:
    order = []

    async def bind(payload):
        return payload, {}

    async def install_ok(task, _payload, _log_ctx):
        task["message"] = "installed"
        task["result"] = {"install": {}}

    def count(payload):
        order.append(f"count:{payload.plugin_id}")

    async def report(_payload, _task):
        order.append("report")

    monkeypatch.setattr(market_bridge, "_bind_market_package_hash", bind)
    monkeypatch.setattr(market_bridge, "_do_install", install_ok)
    monkeypatch.setattr(market_bridge, "_record_market_download_best_effort", count)
    monkeypatch.setattr(market_bridge, "_report_market_install_best_effort", report)
    market_bridge._tasks["task-ok"] = {}
    try:
        await market_bridge._execute_install("task-ok", _payload())
        assert order == ["count:21", "report"]
        assert market_bridge._tasks["task-ok"]["status"] == "completed"
    finally:
        market_bridge._tasks.pop("task-ok", None)

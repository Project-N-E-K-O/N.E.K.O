"""The blocking bridge from worker threads to the market's package transfer.

Requests go to an httpx mock transport routed by host; the preference file
and route statistics live in a temporary directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from plugin.server.infrastructure import package_download as download_io
from plugin.server.infrastructure import package_fetch as fetch
from plugin.server.infrastructure import package_sources as sources
from plugin.server.infrastructure import package_transfer as transfer
from plugin.server.infrastructure.package_download import PackageDownloadDeadline

pytestmark = pytest.mark.plugin_unit

ASSET = "https://github.com/astral-sh/uv/releases/download/0.13.6/uv-x86_64-unknown-linux-gnu.tar.gz"
BODY = bytes(range(256)) * 64  # 16 KiB
MIRROR_HOSTS = {
    "gh-proxy-com": "gh-proxy.com",
    "gh-proxy-org": "gh-proxy.org",
    "cdn-gh-proxy-org": "cdn.gh-proxy.org",
    "edgeone-gh-proxy-org": "edgeone.gh-proxy.org",
}


class Stalled(httpx.AsyncByteStream):
    """A body that sends nothing until it is closed."""

    async def __aiter__(self):
        await asyncio.sleep(3600)
        yield b""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "preferences_path", lambda: tmp_path / "market-download.json")
    sources._routes.clear()
    monkeypatch.setattr(sources, "_loaded", True)
    monkeypatch.setattr(fetch, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(download_io, "_package_loop", None)
    yield
    sources._routes.clear()


@pytest.fixture
def network(monkeypatch):
    """Route requests by host; unknown hosts refuse the connection."""
    handlers: dict[str, object] = {}
    requests: list[httpx.Request] = []
    threads: list[threading.Thread] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        threads.append(threading.current_thread())
        handler = handlers.get(request.url.host)
        if handler is None:
            raise httpx.ConnectError("connection refused", request=request)
        return handler(request)

    original = httpx.AsyncClient
    module = SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
        Timeout=httpx.Timeout, Limits=httpx.Limits, URL=httpx.URL, HTTPError=httpx.HTTPError,
    )

    async def ensure():
        return module

    monkeypatch.setattr(download_io, "ensure_httpx", ensure)
    monkeypatch.setattr(transfer, "ensure_httpx", ensure)
    return SimpleNamespace(handlers=handlers, requests=requests, threads=threads)


def ok(body: bytes = BODY):
    return lambda _request: httpx.Response(200, headers={"content-length": str(len(body))}, content=body)


def preferences(tmp_path: Path, mode: str, source_id: str = "gh-proxy-com") -> None:
    (tmp_path / "market-download.json").write_text(
        json.dumps({"mode": mode, "specified_source_id": source_id}), encoding="utf-8",
    )


def get(tmp_path: Path, **kwargs) -> fetch.FetchedAsset:
    kwargs.setdefault("deadline", time.monotonic() + 10)
    kwargs.setdefault("check_stopped", lambda: None)
    kwargs.setdefault("progress", lambda _received, _total: None)
    return fetch.fetch_release_asset(ASSET, tmp_path / "downloads", maximum_bytes=1024 * 1024, **kwargs)


def hosts(network) -> list[str]:
    return [request.url.host for request in network.requests]


def test_direct_preference_uses_github_only(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    asset = get(tmp_path)
    assert asset.source_id == "github-direct"
    assert asset.path.read_bytes() == BODY
    assert hosts(network) == ["github.com"]
    assert str(network.requests[0].url) == ASSET


def test_specified_preference_uses_that_mirror_only(tmp_path, network):
    preferences(tmp_path, "specified", "cdn-gh-proxy-org")
    network.handlers["cdn.gh-proxy.org"] = ok()
    asset = get(tmp_path)
    assert asset.source_id == "cdn-gh-proxy-org"
    assert hosts(network) == ["cdn.gh-proxy.org"]
    assert str(network.requests[0].url) == "https://cdn.gh-proxy.org/" + ASSET


def test_retired_mirror_preference_falls_back_to_the_default_mirror(tmp_path, network):
    preferences(tmp_path, "specified", "hk-gh-proxy-org")
    network.handlers["gh-proxy.com"] = ok()
    assert get(tmp_path).source_id == "gh-proxy-com"
    assert hosts(network) == ["gh-proxy.com"]


def test_auto_preference_is_the_default_and_follows_the_shared_route_history(tmp_path, network):
    for host in ("github.com", *MIRROR_HOSTS.values()):
        network.handlers[host] = ok()
    # Market downloads made gh-proxy.org look fastest and GitHub slow.
    for _ in range(3):
        sources._record(sources.RouteObservation("gh-proxy-org", latency=0.05), time.time())
        sources._record(sources.RouteObservation("github-direct", latency=5.0), time.time())
    asset = get(tmp_path)
    assert asset.source_id == "gh-proxy-org"
    assert hosts(network)[0] == "gh-proxy.org"
    # The transfer's own observation lands in the same history the market reads.
    assert sources._routes["gh-proxy-org"].samples == 4


def test_failing_mirror_falls_back_and_is_penalized(tmp_path, network):
    preferences(tmp_path, "auto")
    network.handlers["github.com"] = ok()
    network.handlers["gh-proxy.com"] = lambda _request: httpx.Response(502)
    # Ranked first, so it is tried and fails.
    for _ in range(3):
        sources._record(sources.RouteObservation("gh-proxy-com", latency=0.01), time.time())
    before = sources._routes["gh-proxy-com"].failures
    asset = get(tmp_path)
    assert asset.source_id != "gh-proxy-com"
    assert asset.path.read_bytes() == BODY
    assert sources._routes["gh-proxy-com"].failures > before
    assert json.loads((tmp_path / "market-download-routes.json").read_text())["routes"]["gh-proxy-com"]["failures"]


def test_excluded_routes_are_skipped_and_none_left_is_reported(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    with pytest.raises(fetch.NoRoutesLeft):
        get(tmp_path, exclude=["github-direct"])
    assert network.requests == []


def test_recorded_route_failure_penalizes_the_route_in_the_shared_history(tmp_path, network):
    preferences(tmp_path, "specified", "gh-proxy-org")
    network.handlers["gh-proxy.org"] = ok(b"not the pinned bytes")
    asset = get(tmp_path)
    assert hashlib.sha256(asset.path.read_bytes()).hexdigest() != hashlib.sha256(BODY).hexdigest()
    before = sources._routes["gh-proxy-org"].failures
    fetch.record_route_failure(asset)
    assert sources._routes["gh-proxy-org"].failures == before + 1
    assert sources.estimate_route("gh-proxy-org").latency > sources.estimate_route("gh-proxy-com").latency


def test_progress_is_reported_on_the_calling_thread(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    seen: list[tuple[int, int | None, threading.Thread]] = []
    get(tmp_path, progress=lambda received, total: seen.append((received, total, threading.current_thread())))
    assert seen[-1][:2] == (len(BODY), len(BODY))
    assert {thread for *_values, thread in seen} == {threading.current_thread()}


class Stop(Exception):
    pass


@pytest.mark.parametrize("mode", ["auto", "specified"])
def test_cancel_while_every_route_stalls_stops_promptly_and_cleans_up(tmp_path, network, mode):
    preferences(tmp_path, mode)
    stalled = lambda _request: httpx.Response(200, headers={"content-length": "100"}, stream=Stalled())  # noqa: E731
    for host in ("github.com", *MIRROR_HOSTS.values()):
        network.handlers[host] = stalled
    began = time.monotonic()

    def check():
        if time.monotonic() - began > 0.3:
            raise Stop

    with pytest.raises(Stop):
        get(tmp_path, check_stopped=check)
    assert time.monotonic() - began < 5
    directory = tmp_path / "downloads"
    assert not directory.exists() or list(directory.iterdir()) == []


def test_deadline_bounds_a_transfer_rotating_over_failing_routes(tmp_path, network):
    preferences(tmp_path, "auto")
    stalled = lambda _request: httpx.Response(200, headers={"content-length": "100"}, stream=Stalled())  # noqa: E731
    for host in ("github.com", *MIRROR_HOSTS.values()):
        network.handlers[host] = stalled
    began = time.monotonic()
    with pytest.raises(PackageDownloadDeadline):
        get(tmp_path, deadline=began + 0.5)
    assert time.monotonic() - began < 5


def test_deadline_already_past_downloads_nothing(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    with pytest.raises(PackageDownloadDeadline):
        get(tmp_path, deadline=time.monotonic() - 1)
    assert network.requests == []


def test_transfer_runs_on_the_bound_server_loop(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        download_io.bind_package_loop(loop)
        while download_io.package_loop() is None:
            time.sleep(0.01)
        get(tmp_path)
        assert network.threads == [thread]
        # The shared client stays with the server loop for the market to reuse.
        assert download_io._shared_client is not None and download_io._shared_client[0] is loop
    finally:
        asyncio.run_coroutine_threadsafe(download_io.aclose_package_http_client(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def test_without_a_bound_loop_a_private_loop_is_used_and_closed(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    get(tmp_path)
    assert network.threads and network.threads[0] is not threading.current_thread()
    assert download_io._shared_client is None
    assert not network.threads[0].is_alive()


def test_a_stopped_bound_loop_is_not_used(tmp_path, network):
    preferences(tmp_path, "direct")
    network.handlers["github.com"] = ok()
    loop = asyncio.new_event_loop()
    download_io.bind_package_loop(loop)  # never run
    try:
        assert download_io.package_loop() is None
        assert get(tmp_path).path.read_bytes() == BODY
    finally:
        loop.close()


async def test_calling_from_an_event_loop_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="worker thread"):
        get(tmp_path)

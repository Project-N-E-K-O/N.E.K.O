"""The package transfer: hedged opening race, failover, resume and route statistics."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from plugin.server.infrastructure import package_download as download_io
from plugin.server.infrastructure import package_sources as sources
from plugin.server.infrastructure import package_transfer as transfer
from plugin.server.infrastructure.package_download import PackageDownloadDeadline, PackageSizeExceeded

pytestmark = pytest.mark.plugin_unit
BODY = bytes(range(256)) * 1024  # 256 KiB


class Body(httpx.AsyncByteStream):
    """A response body that can stall, or fail after some bytes."""

    def __init__(self, data: bytes, *, fail_after: int | None = None, delay: float = 0.0):
        self.data = data
        self.fail_after = fail_after
        self.delay = delay

    async def __aiter__(self):
        for offset in range(0, len(self.data), 16384):
            if self.fail_after is not None and offset >= self.fail_after:
                raise httpx.ReadError("connection dropped")
            if self.delay:
                await asyncio.sleep(self.delay)
            yield self.data[offset:offset + 16384]


def serve(body: bytes = BODY, *, ranges: bool = True, **stream):
    """A response for ``request`` honouring an open-ended Range when ``ranges``."""

    def respond(request: httpx.Request) -> httpx.Response:
        match = re.fullmatch(r"bytes=(\d+)-", request.headers.get("range", ""))
        if match and ranges:
            start = int(match[1])
            return httpx.Response(206, headers={
                "content-range": f"bytes {start}-{len(body) - 1}/{len(body)}",
                "content-length": str(len(body) - start),
            }, stream=Body(body[start:], **stream))
        return httpx.Response(200, headers={"content-length": str(len(body))}, stream=Body(body, **stream))

    return respond


@pytest.fixture(autouse=True)
def isolated_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "route_stats_path", lambda: tmp_path / "routes.json")
    sources._routes.clear()
    monkeypatch.setattr(sources, "_loaded", True)
    yield
    sources._routes.clear()


@pytest.fixture
async def network(monkeypatch):
    """Route requests by host to handlers; every request is recorded."""
    handlers: dict[str, object] = {}
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        handler = handlers[request.url.host]
        result = handler(request)
        return await result if asyncio.iscoroutine(result) else result

    original = httpx.AsyncClient
    module = SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
        Timeout=httpx.Timeout, Limits=httpx.Limits, URL=httpx.URL, HTTPError=httpx.HTTPError,
    )

    async def ensure():
        return module

    monkeypatch.setattr(download_io, "ensure_httpx", ensure)
    monkeypatch.setattr(transfer, "ensure_httpx", ensure)
    yield SimpleNamespace(handlers=handlers, requests=requests)
    await download_io.aclose_package_http_client()


async def download(tmp_path, routes, **kwargs):
    used: list[str] = []
    progress: list[tuple[int, int | None]] = []
    path = await transfer.download_package(
        routes, tmp_path / "downloads",
        maximum_bytes=kwargs.pop("maximum_bytes", 8 * 1024 * 1024),
        total_timeout=kwargs.pop("total_timeout", 5),
        report_progress=lambda received, total: progress.append((received, total)),
        report_route=used.append, check_cancelled=kwargs.pop("check_cancelled", lambda: None),
    )
    return path, used, progress


def leftovers(tmp_path):
    directory = tmp_path / "downloads"
    return list(directory.iterdir()) if directory.exists() else []


def trusted(source_id: str, latency: float = 0.05) -> None:
    """Give a route enough history that the race waits for it before opening the next."""
    for _ in range(3):
        sources._record(sources.RouteObservation(source_id, latency=latency), now=__import__("time").time())


async def test_single_route_downloads_exact_bytes(tmp_path, network):
    network.handlers["a.test"] = serve()
    path, used, progress = await download(tmp_path, [("a", "https://a.test/pkg")])
    assert path.read_bytes() == BODY
    assert used == ["a"]
    assert progress[-1] == (len(BODY), len(BODY))


async def test_first_route_to_answer_carries_the_body(tmp_path, network):
    async def slow(request):
        await asyncio.sleep(2)
        return serve()(request)

    network.handlers["slow.test"] = slow
    network.handlers["fast.test"] = serve()
    path, used, _ = await download(tmp_path, [("slow", "https://slow.test/pkg"), ("fast", "https://fast.test/pkg")])
    assert path.read_bytes() == BODY
    assert used == ["fast"]


async def test_a_trusted_route_is_given_its_latency_before_others_open(tmp_path, network):
    trusted("a")
    network.handlers["a.test"] = serve()
    network.handlers["b.test"] = serve()
    await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert [request.url.host for request in network.requests] == ["a.test"]


async def test_a_failing_route_does_not_end_the_race(tmp_path, network):
    network.handlers["down.test"] = lambda request: httpx.Response(503)
    network.handlers["html.test"] = lambda request: httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>")
    network.handlers["ok.test"] = serve()
    routes = [("down", "https://down.test/pkg"), ("html", "https://html.test/pkg"), ("ok", "https://ok.test/pkg")]
    path, used, _ = await download(tmp_path, routes)
    # An HTML page is accepted as bytes; the caller's SHA-256 rejects it. Here
    # it only matters that the 503 did not stop the race.
    assert used[0] in {"html", "ok"}
    assert path.exists()


async def test_every_route_failing_raises_the_last_error(tmp_path, network):
    network.handlers["a.test"] = lambda request: httpx.Response(503)
    network.handlers["b.test"] = lambda request: httpx.Response(404)
    with pytest.raises(httpx.HTTPStatusError):
        await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert leftovers(tmp_path) == []


async def test_mid_stream_failure_resumes_on_another_route(tmp_path, network):
    network.handlers["a.test"] = serve(fail_after=len(BODY) // 2)
    network.handlers["b.test"] = serve()
    trusted("a")
    trusted("b")
    path, used, _ = await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert path.read_bytes() == BODY
    assert used == ["a", "b"]
    resumed = [request for request in network.requests if request.url.host == "b.test"]
    assert resumed[-1].headers["range"] == f"bytes={len(BODY) // 2}-"


async def test_a_single_route_that_drops_is_retried_from_where_it_stopped(tmp_path, network):
    calls = []

    def flaky(request):
        calls.append(request.headers.get("range"))
        return serve(fail_after=len(BODY) // 2 if len(calls) == 1 else None)(request)

    network.handlers["a.test"] = flaky
    path, _, _ = await download(tmp_path, [("a", "https://a.test/pkg")])
    assert path.read_bytes() == BODY
    assert calls == [None, f"bytes={len(BODY) // 2}-"]


def answering_after(seconds: float, **stream):
    async def respond(request):
        await asyncio.sleep(seconds)
        return serve(**stream)(request)

    return respond


async def test_a_route_still_connecting_when_the_race_ended_can_carry_the_rest(tmp_path, network):
    # Both open together; the winner drops before its first byte, so only the
    # route it beat while that one was still connecting is left.
    network.handlers["a.test"] = answering_after(0.05, fail_after=0)
    network.handlers["b.test"] = answering_after(0.3)
    path, used, _ = await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert path.read_bytes() == BODY
    assert used == ["a", "b"]
    assert [request.url.host for request in network.requests] == ["a.test", "b.test", "b.test"]


class DropsFirstBody(Body):
    """A body that fails before its first byte if no other body has been read yet."""

    read: list[bool] = []

    async def __aiter__(self):
        if not self.read:
            self.read.append(True)
            raise httpx.ReadError("connection dropped")
        async for chunk in super().__aiter__():
            yield chunk


async def test_a_route_that_lost_the_race_can_carry_the_rest(tmp_path, network, monkeypatch):
    monkeypatch.setattr(DropsFirstBody, "read", [])
    arrived: list[str] = []
    both = asyncio.Event()

    async def together(request):
        arrived.append(request.url.host)
        if len(arrived) == 2:
            both.set()
        await both.wait()
        return httpx.Response(200, headers={"content-length": str(len(BODY))}, stream=DropsFirstBody(BODY))

    # Both answer in the same round, so one wins and the other loses having
    # answered. The winner drops its body without a byte; only the loser is left.
    network.handlers["a.test"] = together
    network.handlers["b.test"] = together
    path, used, _ = await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert path.read_bytes() == BODY
    assert len(network.requests) == 3
    assert len(set(used)) == 2


async def test_a_route_ignoring_range_restarts_the_file(tmp_path, network):
    network.handlers["a.test"] = serve(fail_after=len(BODY) // 2)
    network.handlers["b.test"] = serve(ranges=False)
    trusted("a")
    path, _, _ = await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    assert path.read_bytes() == BODY


async def test_a_route_declaring_another_size_is_skipped(tmp_path, network):
    calls = []

    def flaky(request):
        calls.append(request.headers.get("range"))
        return serve(fail_after=len(BODY) // 2 if len(calls) == 1 else None)(request)

    network.handlers["a.test"] = flaky
    network.handlers["b.test"] = serve(BODY + b"extra")
    trusted("a")
    trusted("b")
    path, used, _ = await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")])
    # b was asked to resume but disagreed on the size, so a resumed instead.
    assert path.read_bytes() == BODY
    assert used == ["a", "a"]
    assert sources._routes["b"].failures == 1.0


async def test_an_expired_asset_url_is_resolved_again(tmp_path, network):
    asset_calls = []

    def release(request):
        return httpx.Response(302, headers={"location": "https://assets.test/signed"})

    def asset(request):
        asset_calls.append(request.headers.get("range"))
        if len(asset_calls) == 2:
            return httpx.Response(403)
        return serve(fail_after=len(BODY) // 2 if len(asset_calls) == 1 else None)(request)

    network.handlers["release.test"] = release
    network.handlers["assets.test"] = asset
    path, _, _ = await download(tmp_path, [("direct", "https://release.test/pkg")])
    assert path.read_bytes() == BODY
    hosts = [request.url.host for request in network.requests]
    # Resolve, remembered URL expired, resolve again, resume.
    assert hosts == ["release.test", "assets.test", "assets.test", "release.test", "assets.test"]
    assert asset_calls[-1] == f"bytes={len(BODY) // 2}-"


async def test_declared_size_over_the_limit_is_not_retried(tmp_path, network):
    network.handlers["a.test"] = serve()
    network.handlers["b.test"] = serve()
    trusted("a")
    with pytest.raises(PackageSizeExceeded) as raised:
        await download(tmp_path, [("a", "https://a.test/pkg"), ("b", "https://b.test/pkg")], maximum_bytes=1024)
    assert raised.value.source == "content_length"
    assert [request.url.host for request in network.requests] == ["a.test"]
    assert leftovers(tmp_path) == []


async def test_undeclared_size_over_the_limit_stops_the_body(tmp_path, network):
    network.handlers["a.test"] = lambda request: httpx.Response(200, stream=Body(BODY))
    with pytest.raises(PackageSizeExceeded) as raised:
        await download(tmp_path, [("a", "https://a.test/pkg")], maximum_bytes=20000)
    assert raised.value.source == "received_bytes"
    assert leftovers(tmp_path) == []


async def test_cancellation_removes_the_partial_file(tmp_path, network):
    class Cancelled(Exception):
        pass

    checks = []

    def check():
        checks.append(1)
        if len(checks) > 3:
            raise Cancelled

    network.handlers["a.test"] = serve()
    with pytest.raises(Cancelled):
        await download(tmp_path, [("a", "https://a.test/pkg")], check_cancelled=check)
    assert leftovers(tmp_path) == []


async def test_total_deadline_bounds_a_trickling_route(tmp_path, network):
    network.handlers["a.test"] = serve(delay=0.2)
    with pytest.raises(PackageDownloadDeadline):
        await download(tmp_path, [("a", "https://a.test/pkg")], total_timeout=0.5)
    assert leftovers(tmp_path) == []


async def test_observations_are_persisted_after_success_and_failure(tmp_path, network):
    network.handlers["down.test"] = lambda request: httpx.Response(503)
    network.handlers["ok.test"] = serve()
    await download(tmp_path, [("down", "https://down.test/pkg"), ("ok", "https://ok.test/pkg")])
    saved = json.loads(sources.route_stats_path().read_text(encoding="utf-8"))["routes"]
    assert saved["down"]["failures"] == 1.0
    # A mock round trip can round to 0 s on Windows' clock; it is still a sample.
    assert saved["ok"]["latency"] is not None and saved["ok"]["failures"] == 0.0


async def test_a_reset_during_a_transfer_is_not_undone(tmp_path, network):
    async def reset_then_serve(request):
        await sources.reset_transfer_history()
        return serve()(request)

    network.handlers["a.test"] = reset_then_serve
    await download(tmp_path, [("a", "https://a.test/pkg")])
    assert not sources.route_stats_path().exists()
    assert sources._routes == {}

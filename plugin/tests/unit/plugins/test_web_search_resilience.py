"""Burst-control tests for the web_search plugin."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from plugin.plugins import web_search
from plugin.plugins.web_search import _resilience as resilience
from tests.fake_clock import patch_module_clock

pytestmark = pytest.mark.plugin_unit


def _response(status: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("GET", "https://example.com/search"),
    )


class _PluginStub:
    _is_cn = False
    _backend = "duckduckgo"
    _user_agent = "test-agent"
    logger = type("Logger", (), {"warning": lambda *_args: None})()

    def __init__(self, *, total_timeout: float = 1.0) -> None:
        self._coordinator = resilience.SearchCoordinator()
        self._total_timeout = total_timeout

    @staticmethod
    def _get_client() -> object:
        return object()

    def _defaults(self) -> dict[str, float | int]:
        return {
            "retry_attempts": 2,
            "retry_base_delay": 0.0,
            "ddg_retry_base_delay": 0.0,
            "ddg_fallback_delay": 0.0,
            "ddg_min_interval": 0.0,
            "total_timeout": self._total_timeout,
        }


def test_retry_after_supports_seconds_and_http_dates() -> None:
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)
    assert resilience.retry_after_seconds({"Retry-After": "2.5"}, now=now) == 2.5
    assert resilience.retry_after_seconds(
        {"Retry-After": "Wed, 12 Aug 2026 10:00:03 GMT"}, now=now
    ) == 3.0
    assert resilience.retry_after_seconds({"Retry-After": "invalid"}, now=now) is None


def test_rate_limit_and_retry_after_skip_endpoint_fallback() -> None:
    for response in (
        _response(429),
        _response(503, headers={"Retry-After": "2"}),
    ):
        error = httpx.HTTPStatusError(
            "upstream cooldown",
            request=response.request,
            response=response,
        )
        assert resilience.should_skip_fallback(error) is True

    ordinary_response = _response(500)
    ordinary_error = httpx.HTTPStatusError(
        "ordinary failure",
        request=ordinary_response.request,
        response=ordinary_response,
    )
    assert resilience.should_skip_fallback(ordinary_error) is False


@pytest.mark.parametrize(
    ("configured", "country", "expected"),
    [
        ("auto", "CN", "anysearch"),
        ("auto", "JP", "anysearch"),
        ("auto", None, "anysearch"),
        ("invalid", None, "anysearch"),
        ("baidu", "JP", "baidu"),
        ("duckduckgo", "CN", "duckduckgo"),
    ],
)
def test_backend_selection_has_safe_fallback(
    configured: str,
    country: str | None,
    expected: str,
) -> None:
    assert web_search._select_backend(configured, country) == expected


def test_geoip_providers_are_https() -> None:
    assert web_search._GEOIP_PROVIDERS
    assert all(url.startswith("https://") for url, _field in web_search._GEOIP_PROVIDERS)


@pytest.mark.asyncio
async def test_geoip_stalled_provider_does_not_starve_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, str]:
            return {web_search._GEOIP_PROVIDERS[1][1]: "US"}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, **_kwargs: object) -> _Response:
            calls.append(url)
            if url == web_search._GEOIP_PROVIDERS[0][0]:
                await asyncio.Event().wait()
            return _Response()

    monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **_kwargs: _Client())
    assert await web_search._detect_country(timeout=0.2) == "US"
    assert calls == [url for url, _field in web_search._GEOIP_PROVIDERS]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["baidu", "duckduckgo"])
async def test_startup_detects_geoip_for_explicit_backend_override(
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Config:
        async def dump(self, **_kwargs: object) -> dict[str, dict[str, str]]:
            return {"search": {"backend": backend}}

    plugin = object.__new__(web_search.WebSearchPlugin)
    plugin.config = _Config()
    plugin.logger = type("Logger", (), {"info": lambda *_args: None})()

    async def detected_geoip() -> str:
        return "US"

    monkeypatch.setattr(web_search, "_detect_country", detected_geoip)
    await web_search.WebSearchPlugin.startup(plugin)
    assert plugin._backend == backend
    assert plugin._country == "US"
    assert plugin._anysearch_zone == "intl"


@pytest.mark.asyncio
async def test_configured_anysearch_does_not_cross_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _PluginStub()
    plugin._configured_backend = "anysearch"
    plugin._backend = "anysearch"
    plugin._country = "CN"
    plugin._anysearch_zone = "cn"
    plugin._anysearch_api_key = ""
    baidu_calls = 0

    async def failing_anysearch(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        raise web_search.SearchResponseError("AnySearch failed")

    async def unexpected_baidu(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        nonlocal baidu_calls
        baidu_calls += 1
        return []

    monkeypatch.setattr(web_search, "_search_anysearch", failing_anysearch)
    monkeypatch.setattr(web_search, "_search_baidu", unexpected_baidu)

    with pytest.raises(web_search.SearchResponseError, match="AnySearch failed"):
        await web_search.WebSearchPlugin._do_text_search(
            plugin,
            "neko",
            3,
            1.0,
            backend="auto",
        )

    assert baidu_calls == 0


def test_duckduckgo_defaults_are_more_conservative_than_baidu() -> None:
    plugin = object.__new__(web_search.WebSearchPlugin)
    plugin._cfg = {}

    defaults = plugin._defaults()

    assert defaults["ddg_min_interval"] == 3.0
    assert defaults["ddg_min_interval"] > defaults["min_interval"]
    assert defaults["ddg_retry_base_delay"] == 2.0
    assert defaults["ddg_retry_base_delay"] > defaults["retry_base_delay"]
    assert defaults["ddg_fallback_delay"] == defaults["ddg_min_interval"]
    assert defaults["cooldown"] == 60.0
    assert defaults["ddg_cooldown"] == 300.0
    assert defaults["ddg_max_cooldown"] == 3600.0


def test_duckduckgo_rate_defaults_are_configurable_and_bounded() -> None:
    plugin = object.__new__(web_search.WebSearchPlugin)
    plugin._cfg = {
        "duckduckgo_min_interval_seconds": 99,
        "duckduckgo_retry_base_delay_seconds": 0,
        "duckduckgo_fallback_delay_seconds": 99,
        "cooldown_seconds": 1,
        "duckduckgo_cooldown_seconds": 1,
        "duckduckgo_max_cooldown_seconds": 99999,
    }

    defaults = plugin._defaults()

    assert defaults["ddg_min_interval"] == 15.0
    assert defaults["ddg_retry_base_delay"] == 0.5
    assert defaults["ddg_fallback_delay"] == 15.0
    assert defaults["cooldown"] == 1.0
    assert defaults["ddg_cooldown"] == 60.0
    assert defaults["ddg_max_cooldown"] == 86400.0


def test_duckduckgo_uses_an_honest_fixed_user_agent() -> None:
    assert web_search._UA.startswith("N.E.K.O-WebSearch/")
    assert "Mozilla" not in web_search._UA
    assert "Chrome" not in web_search._UA
    assert web_search._ddg_headers()["User-Agent"] == web_search._UA
    assert "Referer" not in web_search._ddg_headers()


@pytest.mark.asyncio
async def test_request_never_retries_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    async def request() -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(429, headers={"Retry-After": "0"})

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(resilience.asyncio, "sleep", sleep)
    monkeypatch.setattr(resilience.random, "uniform", lambda _a, _b: 0.0)

    with pytest.raises(httpx.HTTPStatusError):
        await resilience.request_with_retry(request, max_attempts=3)

    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_request_does_not_retry_non_transient_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def request() -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(404)

    with pytest.raises(httpx.HTTPStatusError):
        await resilience.request_with_retry(request, max_attempts=3)

    assert calls == 1


@pytest.mark.asyncio
async def test_long_retry_after_is_not_retried_early() -> None:
    calls = 0

    async def request() -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(429, headers={"Retry-After": "120"})

    with pytest.raises(httpx.HTTPStatusError):
        await resilience.request_with_retry(request, max_attempts=2, max_delay=4)

    assert calls == 1


@pytest.mark.asyncio
async def test_same_query_burst_is_coalesced_and_cached() -> None:
    coordinator = resilience.SearchCoordinator(ttl_seconds=60, stale_seconds=60)
    calls = 0
    release = asyncio.Event()

    async def fetch() -> resilience.SearchResults:
        nonlocal calls
        calls += 1
        await release.wait()
        return [{"title": "NEKO", "url": "https://example.com", "snippet": "ok"}]

    tasks = [asyncio.create_task(coordinator.run(("ddg", "neko", 3), fetch)) for _ in range(20)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(result[0]["title"] == "NEKO" for result in results)

    # Cache values are defensive copies: a caller cannot corrupt later hits.
    results[0][0]["title"] = "changed"
    cached = await coordinator.run(("ddg", "neko", 3), fetch)
    assert calls == 1
    assert cached[0]["title"] == "NEKO"


@pytest.mark.asyncio
async def test_last_cancelled_waiter_cancels_upstream_fetch() -> None:
    coordinator = resilience.SearchCoordinator()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch() -> resilience.SearchResults:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return []

    caller = asyncio.create_task(coordinator.run(("ddg", "neko", 3), fetch))
    await started.wait()
    caller.cancel()

    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.wait_for(cancelled.wait(), timeout=1)


@pytest.mark.asyncio
async def test_shared_fetch_survives_until_last_waiter_leaves() -> None:
    coordinator = resilience.SearchCoordinator()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch() -> resilience.SearchResults:
        started.set()
        await release.wait()
        return [{"title": "ok", "url": "https://example.com", "snippet": ""}]

    first = asyncio.create_task(coordinator.run(("ddg", "neko", 3), fetch))
    second = asyncio.create_task(coordinator.run(("ddg", "neko", 3), fetch))
    await started.wait()
    await asyncio.sleep(0)
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()

    release.set()
    assert (await second)[0]["title"] == "ok"


@pytest.mark.asyncio
async def test_different_queries_are_serialized_per_backend() -> None:
    coordinator = resilience.SearchCoordinator(min_interval_seconds=0)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first() -> resilience.SearchResults:
        first_started.set()
        await release_first.wait()
        return [{"title": "first", "url": "https://example.com/1", "snippet": ""}]

    async def second() -> resilience.SearchResults:
        second_started.set()
        return [{"title": "second", "url": "https://example.com/2", "snippet": ""}]

    first_task = asyncio.create_task(coordinator.run(("ddg", "first"), first))
    await first_started.wait()
    second_task = asyncio.create_task(coordinator.run(("ddg", "second"), second))
    await asyncio.sleep(0)
    assert not second_started.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_started.is_set()


@pytest.mark.asyncio
async def test_different_query_queue_wait_is_bounded() -> None:
    coordinator = resilience.SearchCoordinator(
        min_interval_seconds=0,
        queue_wait_seconds=0.01,
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> resilience.SearchResults:
        first_started.set()
        await release_first.wait()
        return [{"title": "first", "url": "https://example.com/1", "snippet": ""}]

    async def second() -> resilience.SearchResults:
        return [{"title": "second", "url": "https://example.com/2", "snippet": ""}]

    first_task = asyncio.create_task(coordinator.run(("ddg", "first"), first))
    await first_started.wait()
    with pytest.raises(resilience.SearchBusyError):
        await coordinator.run(("ddg", "second"), second)
    release_first.set()
    await first_task


@pytest.mark.asyncio
async def test_block_starts_backend_cooldown() -> None:
    coordinator = resilience.SearchCoordinator(
        min_interval_seconds=0,
        cooldown_seconds=30,
    )
    second_calls = 0

    async def blocked() -> resilience.SearchResults:
        raise web_search.SearchBlockedError("challenge", retry_after_seconds=10)

    async def second() -> resilience.SearchResults:
        nonlocal second_calls
        second_calls += 1
        return []

    with pytest.raises(web_search.SearchBlockedError):
        await coordinator.run(("ddg", "first"), blocked)
    with pytest.raises(resilience.SearchCooldownError):
        await coordinator.run(("ddg", "second"), second)
    assert second_calls == 0


def test_repeated_blocks_increase_cooldown_and_respect_server_delay() -> None:
    coordinator = resilience.SearchCoordinator(
        cooldown_seconds=300,
        max_cooldown_seconds=1200,
    )
    state = resilience._BackendState(asyncio.Lock())

    challenge = web_search.SearchBlockedError("challenge")
    assert coordinator._cooldown_for_error(state, challenge) == 300
    assert coordinator._cooldown_for_error(state, challenge) == 600
    assert coordinator._cooldown_for_error(state, challenge) == 1200
    assert coordinator._cooldown_for_error(state, challenge) == 1200

    response = _response(429, headers={"Retry-After": "1800"})
    limited = httpx.HTTPStatusError(
        "rate limited",
        request=response.request,
        response=response,
    )
    assert coordinator._cooldown_for_error(state, limited) == 1800


def test_block_without_retry_after_uses_configured_cooldown() -> None:
    coordinator = resilience.SearchCoordinator(
        cooldown_seconds=10,
        max_cooldown_seconds=10,
    )
    state = resilience._BackendState(asyncio.Lock())

    assert (
        coordinator._cooldown_for_error(
            state, web_search.SearchBlockedError("challenge")
        )
        == 10
    )


@pytest.mark.asyncio
async def test_minimum_interval_starts_after_request_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    sleeps: list[float] = []
    patch_module_clock(monkeypatch, resilience, monotonic=lambda: now)

    async def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    monkeypatch.setattr(resilience.asyncio, "sleep", sleep)
    coordinator = resilience.SearchCoordinator(min_interval_seconds=3)

    async def first() -> resilience.SearchResults:
        nonlocal now
        now += 5
        return [{"title": "first", "url": "https://example.com/1", "snippet": ""}]

    async def second() -> resilience.SearchResults:
        return [{"title": "second", "url": "https://example.com/2", "snippet": ""}]

    await coordinator.run(("ddg", "first"), first)
    await coordinator.run(("ddg", "second"), second)

    assert sleeps == [3.0]


@pytest.mark.asyncio
async def test_empty_results_are_not_cached() -> None:
    coordinator = resilience.SearchCoordinator(min_interval_seconds=0)
    calls = 0

    async def empty() -> resilience.SearchResults:
        nonlocal calls
        calls += 1
        return []

    assert await coordinator.run(("ddg", "empty"), empty) == []
    assert await coordinator.run(("ddg", "empty"), empty) == []
    assert calls == 2


@pytest.mark.asyncio
async def test_stale_cache_is_used_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    patch_module_clock(monkeypatch, resilience, monotonic=lambda: now)
    coordinator = resilience.SearchCoordinator(ttl_seconds=5, stale_seconds=30)

    async def first() -> resilience.SearchResults:
        return [{"title": "cached", "url": "https://example.com", "snippet": ""}]

    assert (await coordinator.run("key", first))[0]["title"] == "cached"
    now = 106.0

    async def failing() -> resilience.SearchResults:
        raise httpx.ConnectTimeout("temporary")

    assert (await coordinator.run("key", failing))[0]["title"] == "cached"


@pytest.mark.asyncio
async def test_failure_without_cache_is_not_hidden() -> None:
    coordinator = resilience.SearchCoordinator(ttl_seconds=5, stale_seconds=30)

    async def failing() -> resilience.SearchResults:
        raise httpx.ConnectTimeout("temporary")

    with pytest.raises(httpx.ConnectTimeout):
        await coordinator.run("key", failing)


@pytest.mark.asyncio
async def test_ddg_rate_limit_does_not_fall_back_to_lite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_calls = 0
    lite_calls = 0

    async def html(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        nonlocal html_calls
        html_calls += 1
        response = _response(429, headers={"Retry-After": "120"})
        raise httpx.HTTPStatusError(
            "rate limited",
            request=response.request,
            response=response,
        )

    async def lite(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        nonlocal lite_calls
        lite_calls += 1
        return []

    monkeypatch.setattr(web_search, "_search_ddg_html", html)
    monkeypatch.setattr(web_search, "_search_ddg_lite", lite)

    with pytest.raises(httpx.HTTPStatusError):
        await web_search.WebSearchPlugin._do_text_search(_PluginStub(), "neko", 3, 1.0)

    assert html_calls == 1
    assert lite_calls == 0


@pytest.mark.asyncio
async def test_ddg_endpoint_fallback_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def html(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        raise web_search.SearchResponseError("unparseable")

    async def lite(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        return [{"title": "ok", "url": "https://example.com", "snippet": ""}]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    plugin = _PluginStub()
    plugin._defaults = lambda: {
        "retry_attempts": 2,
        "retry_base_delay": 0.0,
        "ddg_retry_base_delay": 0.0,
        "ddg_fallback_delay": 3.0,
        "ddg_min_interval": 5.0,
        "total_timeout": 10.0,
    }
    monkeypatch.setattr(web_search, "_search_ddg_html", html)
    monkeypatch.setattr(web_search, "_search_ddg_lite", lite)
    monkeypatch.setattr(web_search.asyncio, "sleep", sleep)

    results = await web_search.WebSearchPlugin._do_text_search(plugin, "neko", 3, 1.0)

    assert results[0]["title"] == "ok"
    assert sleeps == [5.0]


@pytest.mark.asyncio
async def test_ddg_202_is_reported_as_blocked() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            202,
            request=request,
            headers={"Retry-After": "1800"},
            content=b"challenge",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(web_search.SearchBlockedError) as caught:
            await web_search._search_ddg_html(
                client,
                "neko",
                retry_attempts=3,
            )
    assert calls == 1
    assert caught.value.retry_after_seconds == 1800


@pytest.mark.asyncio
async def test_ddg_403_is_reported_as_blocked_without_fallback() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, request=request, content=b"forbidden")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(web_search.SearchBlockedError) as caught:
            await web_search._search_ddg_html(
                client,
                "neko",
                retry_attempts=3,
            )

    assert calls == 1
    assert caught.value.retry_after_seconds == 300


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content"),
    [
        (403, b"forbidden"),
        (200, b"<html><form id=anomaly-modal>challenge</form></html>"),
    ],
)
async def test_ddg_block_prevents_lite_and_next_business_request(
    status: int,
    content: bytes,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, request=request, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        plugin = _PluginStub()
        plugin._get_client = lambda: client

        with pytest.raises(web_search.SearchBlockedError):
            await web_search.WebSearchPlugin._do_text_search(plugin, "first", 3, 1.0)
        with pytest.raises(resilience.SearchCooldownError):
            await web_search.WebSearchPlugin._do_text_search(plugin, "second", 3, 1.0)

    assert calls == 1


@pytest.mark.asyncio
async def test_ddg_429_is_reported_as_blocked_without_retry() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            request=request,
            headers={"Retry-After": "900"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(web_search.SearchBlockedError) as caught:
            await web_search._search_ddg_html(
                client,
                "neko",
                retry_attempts=3,
            )

    assert calls == 1
    assert caught.value.retry_after_seconds == 900


@pytest.mark.asyncio
async def test_ddg_unparseable_200_is_not_a_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"<html></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(web_search.SearchResponseError):
            await web_search._search_ddg_lite(
                client,
                "neko",
                retry_attempts=1,
            )


@pytest.mark.asyncio
async def test_ddg_explicit_no_results_page_returns_empty_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b'<html><div class="no-results">No results.</div></html>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await web_search._search_ddg_lite(
            client,
            "rare exact query",
            retry_attempts=1,
        ) == []


@pytest.mark.asyncio
async def test_baidu_explicit_no_results_page_returns_empty_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=(
                '<html><div id="content_left"><div class="nors">'
                "抱歉，没有找到与查询相关的结果"
                "</div></div></html>"
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("BAIDUID", "test")
        assert await web_search._search_baidu(
            client, "rare exact query", retry_attempts=1
        ) == []


@pytest.mark.asyncio
async def test_baidu_unparseable_200_is_not_a_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"<html></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("BAIDUID", "test")
        with pytest.raises(web_search.SearchResponseError):
            await web_search._search_baidu(client, "neko", retry_attempts=1)


@pytest.mark.asyncio
async def test_complete_search_has_one_total_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    async def slow_html(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return []

    monkeypatch.setattr(web_search, "_search_ddg_html", slow_html)

    with pytest.raises(TimeoutError):
        await web_search.WebSearchPlugin._do_text_search(
            _PluginStub(total_timeout=0.01), "neko", 3, 15.0
        )

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_total_timeout_includes_coordinator_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_called = False
    coordinator = resilience.SearchCoordinator(
        min_interval_seconds=1.0,
        queue_wait_seconds=1.0,
    )
    coordinator._loop = asyncio.get_running_loop()
    coordinator._backends["duckduckgo"] = resilience._BackendState(
        asyncio.Lock(),
        next_allowed=resilience.time.monotonic() + 60.0,
    )

    async def html(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        nonlocal fetch_called
        fetch_called = True
        return []

    plugin = _PluginStub(total_timeout=0.01)
    plugin._coordinator = coordinator
    monkeypatch.setattr(web_search, "_search_ddg_html", html)

    with pytest.raises(TimeoutError):
        await web_search.WebSearchPlugin._do_text_search(plugin, "neko", 3, 15.0)

    assert fetch_called is False


@pytest.mark.asyncio
async def test_total_timeout_returns_retained_stale_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = ("duckduckgo", "neko", 3)
    coordinator = resilience.SearchCoordinator(ttl_seconds=0, stale_seconds=60)
    coordinator._store(
        key,
        [{"title": "stale", "url": "https://example.com", "snippet": ""}],
    )

    async def slow_html(*_args: object, **_kwargs: object) -> resilience.SearchResults:
        await asyncio.Event().wait()
        return []

    plugin = _PluginStub(total_timeout=0.01)
    plugin._coordinator = coordinator
    monkeypatch.setattr(web_search, "_search_ddg_html", slow_html)

    results = await web_search.WebSearchPlugin._do_text_search(plugin, "neko", 3, 15.0)

    assert results[0]["title"] == "stale"


def test_search_entries_allow_internal_timeout_to_finish() -> None:
    search_meta = web_search.WebSearchPlugin.search.__neko_event_meta__
    summary_meta = web_search.WebSearchPlugin.search_summary.__neko_event_meta__
    assert search_meta.timeout == 30.0
    assert summary_meta.timeout == 30.0

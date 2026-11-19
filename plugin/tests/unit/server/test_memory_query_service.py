from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from plugin.sdk.shared.contracts.memory import MemoryTarget
from plugin.server.application import memory as adapter
from plugin.server.application.messages import memory_query_service as module
from plugin.server.domain.errors import ServerDomainError
from plugin.utils.http_imports import load_httpx


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    httpx_module = load_httpx()
    real_client = httpx_module.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _client(**kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        return real_client(transport=httpx_module.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", _client)
    return seen


def _hit(text: str) -> dict[str, object]:
    return {"id": "f1", "text": text, "tier": "fact", "entity": "master", "score": 0.9}


def _router(
    *,
    query_status: int = 200,
    query_json: object | None = None,
    locale: str = "en",
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/query_memory/"):
            return httpx.Response(query_status, json=query_json)
        if request.url.path.startswith("/prompt-locale/"):
            return httpx.Response(200, json={"success": True, "effective_language": locale})
        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_posts_to_query_memory_and_renders_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_transport(
        monkeypatch,
        _router(query_json={"results": [_hit("Likes green tea")], "query": "tea"}),
    )

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko/1", query="  tea  ", timeout=5
    )

    query_request = seen[0]
    assert query_request.method == "POST"
    assert query_request.url.raw_path == b"/query_memory/Neko%2F1"
    body = json.loads(query_request.content)
    assert body["query"] == "tea"
    assert body["budget_ms"] == 2000
    # Plugins never send subjects (legacy private recall) or the plugin
    # process locale.
    assert "subjects" not in body
    assert "language" not in body
    assert all("/search_for_memory/" not in str(r.url) for r in seen)

    text = result["result"]
    assert isinstance(text, str)
    assert "Found 1 relevant memories:" in text
    assert "1. " in text
    assert "Likes green tea" in text


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_renders_in_memory_server_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_json={"results": [_hit("喜欢绿茶")]}, locale="zh-CN"),
    )

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="茶", timeout=5
    )

    assert result["result"].startswith("找到 1 条相关记忆：")


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_returns_empty_text_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_transport(monkeypatch, _router(query_json={"results": []}))

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=5
    )

    assert result == {"result": ""}
    # No hits, nothing to render: the locale lookup is skipped.
    assert [r.url.path for r in seen] == ["/query_memory/Neko"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_budget_shrinks_with_short_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_transport(monkeypatch, _router(query_json={"results": []}))

    await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=1.0
    )

    assert json.loads(seen[0].content)["budget_ms"] == 500


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_surfaces_recall_failure_reported_with_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_json={"results": [], "error_code": "hybrid_recall_failed"}),
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_UPSTREAM_ERROR"
    assert exc_info.value.details == {"upstream_code": "hybrid_recall_failed"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_rejects_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(monkeypatch, _router(query_json={"unexpected": True}))

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_UPSTREAM_ERROR"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_rejects_non_object_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed hit is an upstream fault, not an ordinary "no memories"."""
    _install_transport(
        monkeypatch,
        _router(query_json={"results": [_hit("Likes green tea"), "invalid"]}),
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_UPSTREAM_ERROR"
    assert exc_info.value.status_code == 502
    assert exc_info.value.details == {"upstream_code": "invalid_response"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_maps_truncated_json_to_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"results": [',
            headers={"content-type": "application/json"},
        )

    _install_transport(monkeypatch, handler)

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_UPSTREAM_ERROR"
    assert exc_info.value.status_code == 502
    assert exc_info.value.details == {"upstream_code": "invalid_json"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_fails_fast_with_busy_when_slots_are_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_transport(monkeypatch, _router(query_json={"results": []}))
    monkeypatch.setattr(adapter, "_active_memory_requests", adapter._MAX_MEMORY_HTTP_REQUESTS)

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "BUSY"
    assert exc_info.value.status_code == 429
    assert seen == []
    # A rejected call never took a slot, so it must not give one back.
    assert adapter._active_memory_requests == adapter._MAX_MEMORY_HTTP_REQUESTS


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_shares_the_slot_cap_with_memory_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query_memory call in flight counts against MemoryClient.search's cap."""
    monkeypatch.setattr(adapter, "_MAX_MEMORY_HTTP_REQUESTS", 1)
    arrived = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        arrived.set()
        await release.wait()
        return httpx.Response(200, json={"results": []})

    _install_transport(monkeypatch, handler)
    task = asyncio.create_task(
        module.MemoryQueryService().query_memory(lanlan_name="Neko", query="tea", timeout=5)
    )
    try:
        await asyncio.wait_for(arrived.wait(), 5)

        class _Ctx:
            @staticmethod
            def remaining() -> float:
                return 5.0

        with pytest.raises(ServerDomainError) as exc_info:
            await adapter.search(
                _Ctx(),
                {"target": MemoryTarget.private("Neko").to_wire(), "query": "tea"},
            )
        assert exc_info.value.code == "BUSY"
    finally:
        release.set()
        result = await task

    assert result == {"result": ""}
    assert adapter._active_memory_requests == 0


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_releases_its_slot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_status=503, query_json={"detail": "not initialized"}),
    )

    with pytest.raises(ServerDomainError):
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert adapter._active_memory_requests == 0


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_maps_503_to_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_status=503, query_json={"detail": "not initialized"}),
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_NOT_READY"
    assert exc_info.value.status_code == 503


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_maps_other_errors_to_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_status=422, query_json={"detail": "bad"}),
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=5
        )

    assert exc_info.value.code == "MEMORY_QUERY_UPSTREAM_ERROR"
    assert exc_info.value.details == {"upstream_status": 422}


def _install_stalling_transport(
    monkeypatch: pytest.MonkeyPatch, *, stall_prefix: str,
) -> list[str]:
    """Answer normally, except requests under ``stall_prefix`` never answer.

    The stalled request waits on an Event nobody sets, and MockTransport
    applies no per-request timeout, so only the service's own limits can end
    the wait. Tests built on this assert state and order, never elapsed time.
    """
    httpx_module = load_httpx()
    real_client = httpx_module.AsyncClient
    paths: list[str] = []
    never = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.startswith(stall_prefix):
            await never.wait()
        if request.url.path.startswith("/query_memory/"):
            return httpx.Response(200, json={"results": [_hit("Likes green tea")]})
        return httpx.Response(200, json={"effective_language": "zh"})

    def _client(**kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        return real_client(transport=httpx_module.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", _client)
    return paths


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_keeps_hits_when_locale_lookup_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled locale lookup ends on its own limit, inside the deadline.

    The hits already recalled are rendered in the default locale instead of
    the whole call running into the deadline and returning nothing.
    """
    paths = _install_stalling_transport(monkeypatch, stall_prefix="/prompt-locale/")

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=0.5
    )

    assert paths == ["/query_memory/Neko", "/prompt-locale/Neko"]
    assert "Likes green tea" in result["result"]
    # Default locale, not the "zh" the stalled lookup would have returned.
    assert result["result"].startswith("Found 1 relevant memories:")


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_stalled_recall_is_ended_by_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _install_stalling_transport(monkeypatch, stall_prefix="/query_memory/")

    with pytest.raises(ServerDomainError) as exc_info:
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="tea", timeout=0.5
        )

    assert exc_info.value.code == "MEMORY_QUERY_TIMEOUT"
    assert exc_info.value.status_code == 504
    assert paths == ["/query_memory/Neko"]


@pytest.mark.plugin_unit
def test_locale_lookup_keeps_time_for_rendering() -> None:
    assert module._locale_lookup_seconds(2.0) == pytest.approx(1.0)
    assert module._locale_lookup_seconds(-1.0) == 0.0


@pytest.mark.plugin_unit
def test_deadline_leaves_room_for_the_reply() -> None:
    assert module._deadline_seconds(5.0) == pytest.approx(4.75)
    assert module._deadline_seconds(0.5) == pytest.approx(0.45)
    assert 0 < module._deadline_seconds(0.01) < 0.01


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_keeps_hits_when_locale_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/query_memory/"):
            return httpx.Response(200, json={"results": [_hit("Likes green tea")]})
        return httpx.Response(500, json={"detail": "locale sidecar unreadable"})

    _install_transport(monkeypatch, handler)

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=5
    )

    # Falls back to the memory-prompt default locale instead of failing.
    assert "Likes green tea" in result["result"]
    assert result["result"].startswith("Found 1 relevant memories:")


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_keeps_hits_when_locale_lookup_cannot_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/query_memory/"):
            return httpx.Response(200, json={"results": [_hit("Likes green tea")]})
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, handler)

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=5
    )

    assert "Likes green tea" in result["result"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_returns_empty_text_when_no_hit_has_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_json={"results": [_hit(""), _hit("   ")]}),
    )

    result = await module.MemoryQueryService().query_memory(
        lanlan_name="Neko", query="tea", timeout=5
    )

    assert result == {"result": ""}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_memory_never_logs_raw_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        _router(query_json={"results": [], "error_code": "hybrid_recall_failed"}),
    )
    logged: list[tuple[object, ...]] = []

    class _Logger:
        def warning(self, *args, **_kw):
            logged.append(args)

        error = warning

    monkeypatch.setattr(module, "logger", _Logger())

    with pytest.raises(ServerDomainError):
        await module.MemoryQueryService().query_memory(
            lanlan_name="Neko", query="secret-query-text", timeout=5
        )

    assert logged
    assert all("secret-query-text" not in str(arg) for args in logged for arg in args)

"""Main side of public knowledge: the HTTP tool and the management proxy.

Main never imports the ``knowledge`` package; these tests stand in for the
Memory Server with an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from main_logic import public_knowledge
from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.tool_calling import ToolRegistry
from main_routers import public_knowledge_router
from main_routers.system_router import _shared as system_router_shared


pytestmark = pytest.mark.unit

_AUTH = {"Origin": "http://testserver", "X-CSRF-Token": "test-csrf-token"}


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    public_knowledge.reset_for_tests()
    # Treat the flag as fresh so registration never schedules a real request.
    monkeypatch.setattr(public_knowledge, "_next_check_at", float("inf"))
    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf-token")
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    yield
    public_knowledge.reset_for_tests()


class _Client:
    """Stand-in for the shared internal client, backed by a handler."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append(request)
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(record))

    async def get(self, url, **kwargs):
        return await self._client.get(url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._client.post(url, **kwargs)


@pytest.fixture
def memory_server(monkeypatch):
    def install(handler):
        client = _Client(handler)
        monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: client)
        return client

    return install


def _manager() -> ToolCallingMixin:
    manager = object.__new__(ToolCallingMixin)
    manager.user_language = "zh"
    manager.memory_server_port = 48912
    manager.tool_registry = ToolRegistry()
    manager.fired: list = []
    manager._fire_task = lambda coro: (manager.fired.append(coro), coro.close())
    return manager


# ── tool registration ───────────────────────────────────────────────


def test_tool_is_registered_only_while_knowledge_is_available():
    manager = _manager()
    manager._register_builtin_tools()
    assert manager.tool_registry.get("recall_memory") is not None
    assert manager.tool_registry.get(public_knowledge.TOOL_NAME) is None

    public_knowledge.note_availability({"tool_available": True})
    tool = manager.tool_registry.get(public_knowledge.TOOL_NAME)
    assert tool is not None
    assert "本地公共知识包" in tool.description
    assert len(manager.fired) == 1  # pushed to live sessions

    public_knowledge.note_availability({"tool_available": False})
    assert manager.tool_registry.get(public_knowledge.TOOL_NAME) is None
    assert len(manager.fired) == 2


def test_unchanged_availability_does_not_resync():
    manager = _manager()
    manager._register_builtin_tools()
    public_knowledge.note_availability({"tool_available": False})
    public_knowledge.note_availability({"no": "flag"})
    assert manager.fired == []


# ── tool handler ────────────────────────────────────────────────────


async def test_tool_returns_the_fenced_context_on_a_match(memory_server):
    client = memory_server(
        lambda request: httpx.Response(
            200,
            json={"ok": True, "result": "matched", "context": "BLOCK", "hits": [{}], "tool_available": True},
        )
    )
    output = await public_knowledge.query_public_knowledge(
        {"query": "  kotatsu ", "limit": 9, "material_type": "bogus"},
        memory_server_port=48912,
        language="en",
    )
    assert output == "BLOCK"
    sent = json.loads(client.requests[0].content)
    assert sent == {
        "query": "kotatsu",
        "mode": "lookup",
        "material_type": "auto",
        "limit": 3,
        "budget_ms": public_knowledge.QUERY_BUDGET_MS,
        "language": "en",
    }
    assert str(client.requests[0].url).endswith("/internal/knowledge/query")


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(200, json={"ok": True, "result": "timeout", "context": ""}),
        lambda request: httpx.Response(503, json={"ok": False}),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
async def test_tool_failures_read_as_no_result(memory_server, handler):
    memory_server(handler)
    output = await public_knowledge.query_public_knowledge(
        {"query": "kotatsu"}, memory_server_port=48912, language="zh"
    )
    assert output == "本地公共知识库里没有找到相关资料。"


async def test_empty_query_never_calls_the_memory_server(memory_server):
    client = memory_server(lambda request: httpx.Response(500))
    output = await public_knowledge.query_public_knowledge({}, memory_server_port=1, language="en")
    assert output.startswith("No relevant material")
    assert client.requests == []


# ── management proxy ────────────────────────────────────────────────


@pytest.fixture
async def proxy():
    app = FastAPI()
    app.include_router(public_knowledge_router.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http


async def test_reads_are_forwarded_and_refresh_the_tool_flag(proxy, memory_server):
    client = memory_server(lambda request: httpx.Response(200, json={"ok": True, "items": [], "tool_available": True}))
    response = await proxy.get("/api/public-knowledge/entries", params={"query": "猫", "limit": 5})
    assert response.status_code == 200
    forwarded = client.requests[0]
    assert forwarded.url.path == "/internal/knowledge/entries"
    assert forwarded.url.params["query"] == "猫"
    assert public_knowledge.tool_available() is True


async def test_unknown_paths_are_not_forwarded(proxy, memory_server):
    client = memory_server(lambda request: httpx.Response(200, json={}))
    assert (await proxy.get("/api/public-knowledge/query")).status_code == 404
    assert (await proxy.post("/api/public-knowledge/query", headers=_AUTH, json={})).status_code == 404
    assert (await proxy.get("/api/public-knowledge/../availability")).status_code == 404
    assert client.requests == []


async def test_writes_require_csrf_and_origin(proxy, memory_server):
    client = memory_server(lambda request: httpx.Response(200, json={"ok": True}))
    no_token = await proxy.post(
        "/api/public-knowledge/packs/remove", headers={"Origin": "http://testserver"}, json={"pack_id": "a"}
    )
    foreign = await proxy.post(
        "/api/public-knowledge/packs/remove",
        headers={"Origin": "http://evil.example", "X-CSRF-Token": "test-csrf-token"},
        json={"pack_id": "a"},
    )
    assert no_token.status_code == 403
    assert foreign.status_code == 403
    assert client.requests == []


async def test_write_bodies_are_streamed_through_unparsed(proxy, memory_server):
    client = memory_server(lambda request: httpx.Response(200, json={"ok": True, "job_id": "j"}))
    files = {"pack": ("pack.json", b'{"schema_version": 1}', "application/json")}
    response = await proxy.post("/api/public-knowledge/packs/import", headers=_AUTH, files=files)
    assert response.json() == {"ok": True, "job_id": "j"}
    forwarded = client.requests[0]
    assert forwarded.url.path == "/internal/knowledge/packs/import"
    assert forwarded.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'{"schema_version": 1}' in forwarded.content


async def test_oversized_bodies_are_refused(proxy, memory_server, monkeypatch):
    client = memory_server(lambda request: httpx.Response(200, json={"ok": True}))
    monkeypatch.setitem(public_knowledge_router._WRITE_PATHS, "packs/remove", 16)
    declared = await proxy.post("/api/public-knowledge/packs/remove", headers=_AUTH, content=b"x" * 32)
    assert declared.status_code == 413

    async def chunks():
        yield b"x" * 10
        yield b"x" * 10

    streamed = await proxy.post("/api/public-knowledge/packs/remove", headers=_AUTH, content=chunks())
    assert streamed.status_code == 413
    assert client.requests == []


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (httpx.ConnectError("down"), 503, "knowledge_unavailable"),
        (httpx.ReadTimeout("slow"), 504, "knowledge_timeout"),
    ],
)
async def test_memory_server_failures_map_to_stable_reasons(proxy, memory_server, error, status, reason):
    def fail(request):
        raise error

    memory_server(fail)
    response = await proxy.get("/api/public-knowledge/status")
    assert response.status_code == status
    assert response.json() == {"ok": False, "reason": reason}

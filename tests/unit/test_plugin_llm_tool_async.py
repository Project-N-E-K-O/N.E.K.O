# -*- coding: utf-8 -*-
"""``register_llm_tool_async`` / ``unregister_llm_tool_async`` report what
main_server actually answered, and keep the SDK's local ``_llm_tools`` in
step with the host's ``_plugin_tools`` (issue #3321).

Three layers:

* SDK only -- a fake host request function; checks result shapes and the
  local commit / rollback rules.
* Host handlers -- ``plugin/server/messaging/handlers/llm_tools.py`` with a
  fake main_server HTTP client; checks the per-role outcome is passed back,
  the plugin id comes from the connection, and host tracking.
* End to end -- real ``PluginContext`` + ``RpcPeer`` + ``RpcDispatcher`` +
  host handlers, fake main_server only; covers the host timeout path.

No real HTTP, no real plugin process, no real main_server.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


PLUGIN_ID = "demo_plugin"


def _make_plugin_ctx():
    from plugin.core.context import PluginContext

    return PluginContext(
        plugin_id=PLUGIN_ID,
        config_path=Path("plugin.toml"),
        logger=logging.getLogger("test"),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )


def _make_plugin(ctx=None):
    from plugin.sdk.plugin import NekoPluginBase, neko_plugin

    @neko_plugin
    class P(NekoPluginBase):
        pass

    ctx = ctx or _make_plugin_ctx()
    return P(ctx), ctx


def _drain(ctx) -> list[dict]:
    msgs: list[dict] = []
    while not ctx.message_queue.empty():
        msgs.append(ctx.message_queue.get_nowait())
    return msgs


async def _handler(**kwargs):
    return kwargs


def _register_kwargs(name: str = "tool_a", **extra):
    kwargs = dict(
        name=name,
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        timeout=12.0,
        role="Lanlan",
    )
    kwargs.update(extra)
    return kwargs


class _FakeHost:
    """Stands in for ``PluginContext._send_request_and_wait_async``."""

    def __init__(self, reply=None, exc: BaseException | None = None, delay: float = 0.0):
        self.reply = reply
        self.exc = exc
        self.delay = delay
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.reply


def _install_host(plugin, host) -> None:
    # The SDK reads the request function off the raw host context.
    plugin._host_ctx._send_request_and_wait_async = host


def _entry_registered(plugin, name: str) -> bool:
    return f"__llm_tool__{name}" in plugin._dynamic_entries


# ---------------------------------------------------------------------------
# SDK: register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_async_success_keeps_local_and_reports_roles():
    plugin, ctx = _make_plugin()
    host = _FakeHost(reply={
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
    })
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs(reply_tail=True), request_timeout=3.0)

    assert result == {
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
    }
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")
    (call,) = host.calls
    assert call["request_type"] == "LLM_TOOL_REGISTER_REQUEST"
    assert call["timeout"] == 3.0
    assert call["wrap_result"] is False
    payload = call["request_data"]
    assert payload["name"] == "tool_a"
    assert payload["role"] == "Lanlan"
    assert payload["timeout_seconds"] == 12.0
    assert payload["reply_tail"] is True
    # The async path must not also emit the fire-and-forget notification,
    # or the host would register the tool twice.
    assert not [m for m in _drain(ctx) if m.get("type") == "LLM_TOOL_REGISTER"]


@pytest.mark.asyncio
async def test_register_async_partial_role_failure_is_ok_and_kept():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": True,
        "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "RuntimeError: sync failed"}],
        "error": None,
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs(role=None))

    assert result["ok"] is True
    assert result["affected_roles"] == ["A"]
    assert result["failed_roles"] == [{"role": "B", "error": "RuntimeError: sync failed"}]
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


@pytest.mark.asyncio
async def test_register_async_foreign_owner_rolls_back_and_allows_retry():
    plugin, ctx = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": False,
        "affected_roles": [],
        "failed_roles": [{"role": "*", "error": "cross-source ledger overwrite refused"}],
        "error": "rejected",
        "message": "main_server rejected register",
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "rejected"
    assert result["failed_roles"] == [{"role": "*", "error": "cross-source ledger overwrite refused"}]
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")
    # The host was told the dynamic entry is gone again.
    updates = [m for m in _drain(ctx) if m.get("type") == "ENTRY_UPDATE"]
    assert [u["action"] for u in updates] == ["register", "unregister"]
    # Rolled back cleanly: the same name can be registered again.
    _install_host(plugin, _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}))
    retry = await plugin.register_llm_tool_async(**_register_kwargs())
    assert retry["ok"] is True
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


@pytest.mark.asyncio
async def test_register_async_main_server_unavailable_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": False, "affected_roles": [], "failed_roles": [],
        "error": "main_server_unavailable", "message": "ConnectError: refused",
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    TimeoutError("late"),
    "DEADLINE_EXCEEDED",
    "OPERATION_TIMEOUT",
    "TRANSPORT_TIMEOUT",
])
async def test_register_async_host_timeout_rolls_back(exc):
    from plugin.core.rpc import RpcRemoteError

    if isinstance(exc, str):
        exc = RpcRemoteError({"code": exc, "message": "x"})
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=exc))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_register_async_host_unavailable_rolls_back():
    from plugin.core.rpc import RpcRemoteError

    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=RpcRemoteError({"code": "BUSY", "message": "busy"})))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert "BUSY" in (result["message"] or "")
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_without_host_request_support_rolls_back():
    plugin, _ = _make_plugin()
    plugin._host_ctx = object()  # a host context with no request function

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_malformed_host_reply_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply=["not", "a", "dict"]))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_cancelled_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={"ok": True}, delay=10.0))

    task = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]  # claimed while in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_register_async_duplicate_name_raises_like_sync_api():
    from plugin.sdk.shared.models.exceptions import EntryConflictError

    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)
    plugin.register_llm_tool(**_register_kwargs())

    with pytest.raises(EntryConflictError):
        await plugin.register_llm_tool_async(**_register_kwargs())
    assert host.calls == []
    # The existing registration is untouched.
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, "5"])
async def test_register_async_rejects_bad_request_timeout_before_claiming(bad):
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=bad)
    assert host.calls == []
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_failure_does_not_drop_a_newer_registration():
    """While the request is in flight the plugin unregisters and registers
    the same name again (sync API). The late failure must not remove the
    newer registration."""
    plugin, _ = _make_plugin()
    gate = asyncio.Event()

    async def host(**kwargs):
        await gate.wait()
        return {"ok": False, "error": "rejected"}

    _install_host(plugin, host)
    task = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    assert plugin.unregister_llm_tool("tool_a") is True
    plugin.register_llm_tool(**_register_kwargs(description="newer"))
    gate.set()
    result = await task

    assert result["ok"] is False
    assert [t["description"] for t in plugin.list_llm_tools()] == ["newer"]
    assert _entry_registered(plugin, "tool_a")


# ---------------------------------------------------------------------------
# SDK: unregister
# ---------------------------------------------------------------------------


async def _plugin_with_tool():
    plugin, ctx = _make_plugin()
    _install_host(plugin, _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}))
    result = await plugin.register_llm_tool_async(**_register_kwargs())
    assert result["ok"] is True
    _drain(ctx)
    return plugin, ctx


@pytest.mark.asyncio
async def test_unregister_async_success_drops_local():
    plugin, ctx = await _plugin_with_tool()
    host = _FakeHost(reply={
        "ok": True, "removed": True, "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [], "error": None, "message": None,
    })
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a", request_timeout=4.0)

    assert result == {
        "ok": True, "removed": True, "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [], "error": None, "message": None,
    }
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")
    (call,) = host.calls
    assert call["request_type"] == "LLM_TOOL_UNREGISTER_REQUEST"
    assert call["timeout"] == 4.0
    assert call["request_data"]["name"] == "tool_a"
    assert call["request_data"]["role"] == "Lanlan"
    assert not [m for m in _drain(ctx) if m.get("type") == "LLM_TOOL_UNREGISTER"]


@pytest.mark.asyncio
async def test_unregister_async_partial_failure_keeps_local():
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply={
        "ok": False, "removed": True, "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "boom"}], "refused_roles": [],
        "error": "partial_failure",
    }))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False
    assert result["removed"] is True
    assert result["error"] == "partial_failure"
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    # Role B still has the tool, so its calls must still reach a handler.
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_unregister_async_foreign_owner_is_reported_and_drops_local():
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply={
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:other"}],
    }))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Lanlan", "owned_by": "plugin:other"}]
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,exc,error", [
    (None, TimeoutError("late"), "timeout"),
    (None, RuntimeError("Plugin context is closed"), "host_unavailable"),
    ({"ok": False, "error": "main_server_unavailable"}, None, "main_server_unavailable"),
])
async def test_unregister_async_failure_keeps_local(reply, exc, error):
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply=reply, exc=exc))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False
    assert result["error"] == error
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_unregister_async_unknown_name_does_not_contact_host():
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("nope")

    assert result["ok"] is False
    assert result["error"] == "not_registered"
    assert result["removed"] is False
    assert host.calls == []


# ---------------------------------------------------------------------------
# Host handlers (fake main_server)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = str(body)

    def json(self):
        return self._body


class _FakeMainServer:
    def __init__(self, body=None, status_code: int = 200, exc: BaseException | None = None, delay: float = 0.0):
        self.body = body
        self.status_code = status_code
        self.exc = exc
        self.delay = delay
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, json=None, **_):
        self.posts.append((url, json))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return _FakeResp(self.body, self.status_code)


@pytest.fixture
def registry(monkeypatch):
    from plugin.server.messaging import llm_tool_registry

    llm_tool_registry._plugin_tools.pop(PLUGIN_ID, None)
    yield llm_tool_registry
    llm_tool_registry._plugin_tools.pop(PLUGIN_ID, None)


def _use_main_server(monkeypatch, registry, server: _FakeMainServer) -> _FakeMainServer:
    monkeypatch.setattr(registry, "_get_http_client", lambda: server)
    return server


def _rpc_ctx(from_plugin: str = PLUGIN_ID):
    import time

    from plugin.core.rpc import RpcContext

    return RpcContext(
        from_plugin=from_plugin, request_id="r1", deadline=time.monotonic() + 5, trace_id="r1", depth=0,
    )


_REGISTER_REQUEST = {
    "type": "LLM_TOOL_REGISTER_REQUEST",
    "plugin_id": "someone_else",  # untrusted; the connection identity wins
    "name": "tool_a",
    "description": "d",
    "parameters": {"type": "object", "properties": {}},
    "timeout_seconds": 12.0,
    "role": "Lanlan",
}


@pytest.mark.asyncio
async def test_host_register_success_tracks_and_uses_connection_identity(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "registered": "tool_a", "affected_roles": ["Lanlan"], "failed_roles": [],
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result == {
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
    }
    ((url, payload),) = server.posts
    assert url.endswith("/api/tools/register")
    assert payload["source"] == f"plugin:{PLUGIN_ID}"
    assert f"/callback/{PLUGIN_ID}/tool_a" in payload["callback_url"]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert not registry.has_plugin_tool("someone_else", "tool_a")


@pytest.mark.asyncio
async def test_host_register_partial_role_failure(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "affected_roles": ["A"], "failed_roles": [{"role": "B", "error": "boom"}],
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is True
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_foreign_owner_rejected(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    refusal = {"role": "Lanlan", "error": "tool 'tool_a' already owned by source 'plugin:other'"}
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": False, "affected_roles": [], "failed_roles": [refusal],
        "error": "no role accepted the registration",
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "rejected"
    assert result["failed_roles"] == [refusal]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_main_server_unavailable(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert result["affected_roles"] == [] and result["failed_roles"] == []
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_main_server_http_error(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer({"detail": "unknown role: Lanlan"}, status_code=404))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_error"
    assert "404" in (result["message"] or "")
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_missing_name_is_invalid_request(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))

    result = await handle_llm_tool_register(_rpc_ctx(), {"type": "LLM_TOOL_REGISTER_REQUEST"})

    assert result["ok"] is False
    assert result["error"] == "invalid_request"
    assert server.posts == []


async def _seed_tracking(registry):
    async with registry._lock:
        registry._plugin_tools[PLUGIN_ID]["tool_a"] = {"timeout_seconds": 12.0}


_UNREGISTER_REQUEST = {
    "type": "LLM_TOOL_UNREGISTER_REQUEST", "plugin_id": "someone_else", "name": "tool_a", "role": "Lanlan",
}


@pytest.mark.asyncio
async def test_host_unregister_success_sends_expected_source_and_drops_tracking(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "removed": True, "name": "tool_a", "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result == {
        "ok": True, "removed": True, "affected_roles": ["Lanlan"], "failed_roles": [],
        "refused_roles": [], "error": None, "message": None,
    }
    ((url, payload),) = server.posts
    assert url.endswith("/api/tools/unregister")
    assert payload["expected_source"] == f"plugin:{PLUGIN_ID}"
    assert payload["name"] == "tool_a" and payload["role"] == "Lanlan"
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_partial_failure_keeps_tracking(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": False, "removed": True, "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "boom"}], "refused_roles": [],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "partial_failure"
    assert result["removed"] is True
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_foreign_owner_reported(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:other"}],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is True
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Lanlan", "owned_by": "plugin:other"}]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_main_server_unavailable_keeps_tracking(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


def test_request_handlers_include_llm_tool_operations():
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )
    from plugin.server.messaging.handlers.registry import build_request_handlers

    handlers = build_request_handlers()
    assert handlers["LLM_TOOL_REGISTER_REQUEST"] is handle_llm_tool_register
    assert handlers["LLM_TOOL_UNREGISTER_REQUEST"] is handle_llm_tool_unregister


# ---------------------------------------------------------------------------
# End to end: PluginContext -> RpcPeer -> RpcDispatcher -> host handler
# ---------------------------------------------------------------------------


def _wire_rpc(ctx):
    """Connect the plugin context's RPC peer straight to a host dispatcher
    running the real request handlers (no ZMQ)."""
    from plugin.core.rpc import RpcDispatcher, RpcPeer
    from plugin.server.messaging.handlers.registry import build_request_handlers

    dispatcher = RpcDispatcher(build_request_handlers())

    async def reply(response):
        peer.receive(response["request_id"], result=response.get("result"), error=response.get("error"))

    async def send(request, _timeout):
        assert dispatcher.submit(dict(request, from_plugin=ctx.plugin_id), reply)

    async def cancel(request):
        dispatcher.submit({"type": "RPC_CANCEL", "from_plugin": ctx.plugin_id,
                           "request_id": request["request_id"]}, reply)

    peer = RpcPeer(send, cancel=cancel)
    ctx._rpc_peer = peer
    return dispatcher


@pytest.mark.asyncio
async def test_end_to_end_register_then_unregister(monkeypatch, registry):
    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [],
    }))
    try:
        registered = await plugin.register_llm_tool_async(**_register_kwargs())
        assert registered["ok"] is True and registered["affected_roles"] == ["Lanlan"]
        assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")

        server.body = {"ok": True, "removed": True, "affected_roles": ["Lanlan"],
                       "failed_roles": [], "refused_roles": []}
        unregistered = await plugin.unregister_llm_tool_async("tool_a")
        assert unregistered["ok"] is True and unregistered["removed"] is True
        assert server.posts[-1][1]["expected_source"] == f"plugin:{PLUGIN_ID}"
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
        assert plugin.list_llm_tools() == []
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_end_to_end_host_timeout_rolls_back_both_sides(monkeypatch, registry):
    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    # main_server hangs past the request budget.
    _use_main_server(monkeypatch, registry, _FakeMainServer(
        {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}, delay=5.0,
    ))
    try:
        result = await asyncio.wait_for(
            plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=0.2), timeout=3.0,
        )
        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert plugin.list_llm_tools() == []
        # The host gave up too; it never recorded the tool.
        await asyncio.sleep(0.05)
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_end_to_end_main_server_unavailable(monkeypatch, registry):
    import httpx

    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))
    try:
        result = await plugin.register_llm_tool_async(**_register_kwargs())
        assert result["ok"] is False
        assert result["error"] == "main_server_unavailable"
        assert plugin.list_llm_tools() == []
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    finally:
        await dispatcher.close()

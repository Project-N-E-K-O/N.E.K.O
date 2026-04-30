# -*- coding: utf-8 -*-
"""Unit tests for the plugin SDK ``@llm_tool`` helper.

Covers the in-process SDK contract — decorator metadata, name
validation, auto-registration in :class:`NekoPluginBase.__init__`,
imperative register/unregister, IPC payload shape, and the lifecycle
service's clear-on-stop hook.

No HTTP, no real plugin process — we mock both layers and verify the
SDK side produces the right IPC notifications and the host-side helper
is invoked with the right arguments.
"""
from __future__ import annotations

import queue
import sys
from pathlib import Path

import pytest

# Make the project importable from anywhere (mirrors other tests/unit/*).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Decorator + metadata
# ---------------------------------------------------------------------------


def test_decorator_attaches_metadata():
    from plugin.sdk.plugin import llm_tool
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR, LlmToolMeta

    @llm_tool(
        name="get_weather",
        description="Get weather",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        timeout=15.0,
    )
    async def get_weather(self, *, city):
        return {"city": city}

    meta = getattr(get_weather, LLM_TOOL_META_ATTR)
    assert isinstance(meta, LlmToolMeta)
    assert meta.name == "get_weather"
    assert meta.description == "Get weather"
    assert meta.timeout_seconds == 15.0
    assert meta.role is None


def test_decorator_defaults_to_method_name():
    from plugin.sdk.plugin import llm_tool
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR

    @llm_tool(description="d")
    def my_tool(self):
        return None

    meta = getattr(my_tool, LLM_TOOL_META_ATTR)
    assert meta.name == "my_tool"


def test_decorator_rejects_invalid_name():
    from plugin.sdk.plugin import llm_tool

    with pytest.raises(ValueError):
        @llm_tool(name="has spaces")
        def f(self):
            pass

    with pytest.raises(ValueError):
        @llm_tool(name="a" * 65)
        def g(self):
            pass


def test_decorator_rejects_non_dict_parameters():
    from plugin.sdk.plugin import llm_tool

    with pytest.raises(TypeError):
        @llm_tool(name="t", parameters=[1, 2, 3])  # type: ignore[arg-type]
        def f(self):
            pass


def test_decorator_rejects_zero_timeout():
    from plugin.sdk.plugin import llm_tool

    with pytest.raises(ValueError):
        @llm_tool(name="t", timeout=0)
        def f(self):
            pass


def test_ipc_payload_shape():
    from plugin.sdk.plugin.llm_tool import LlmToolMeta

    meta = LlmToolMeta(
        name="tool_a",
        description="desc",
        parameters={"type": "object", "properties": {}},
        timeout_seconds=20.0,
        role="Lanlan",
    )
    payload = meta.to_ipc_payload(plugin_id="demo")
    assert payload == {
        "type": "LLM_TOOL_REGISTER",
        "plugin_id": "demo",
        "name": "tool_a",
        "description": "desc",
        "parameters": {"type": "object", "properties": {}},
        "timeout_seconds": 20.0,
        "role": "Lanlan",
    }


def test_collect_methods_finds_decorated_only():
    from plugin.sdk.plugin import llm_tool
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods

    class P:
        @llm_tool(name="a", description="da")
        def a(self):
            return 1

        @llm_tool(name="b", description="db")
        def b(self):
            return 2

        def regular(self):
            return 3

    found = collect_llm_tool_methods(P())
    names = sorted(m.name for m, _ in found)
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# NekoPluginBase auto-registration
# ---------------------------------------------------------------------------


def _make_plugin_ctx():
    from plugin.core.context import PluginContext
    import logging

    return PluginContext(
        plugin_id="demo_plugin",
        config_path=Path("plugin.toml"),
        logger=logging.getLogger("test"),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )


def test_base_auto_registers_decorated_methods():
    from plugin.sdk.plugin import NekoPluginBase, neko_plugin, llm_tool

    @neko_plugin
    class P(NekoPluginBase):
        @llm_tool(name="alpha", description="A")
        async def alpha(self, **_):
            return "a"

        @llm_tool(name="beta", description="B", parameters={"type": "object", "properties": {"q": {"type": "string"}}})
        async def beta(self, **_):
            return "b"

    ctx = _make_plugin_ctx()
    instance = P(ctx)

    # Drain IPC queue and group by message type
    msgs: list[dict] = []
    while True:
        try:
            msgs.append(ctx.message_queue.get_nowait())
        except queue.Empty:
            break

    register_msgs = [m for m in msgs if m.get("type") == "LLM_TOOL_REGISTER"]
    entry_msgs = [m for m in msgs if m.get("type") == "ENTRY_UPDATE"]

    register_names = sorted(m["name"] for m in register_msgs)
    assert register_names == ["alpha", "beta"]
    # Each registered tool should also have produced a dynamic entry
    # under the reserved __llm_tool__{name} id.
    entry_ids = sorted(m["entry_id"] for m in entry_msgs)
    assert entry_ids == ["__llm_tool__alpha", "__llm_tool__beta"]

    # list_llm_tools surface
    listed = sorted(t["name"] for t in instance.list_llm_tools())
    assert listed == ["alpha", "beta"]


def test_imperative_register_then_unregister():
    from plugin.sdk.plugin import NekoPluginBase, neko_plugin

    @neko_plugin
    class P(NekoPluginBase):
        pass

    ctx = _make_plugin_ctx()
    p = P(ctx)
    # Drain initial messages
    while not ctx.message_queue.empty():
        ctx.message_queue.get_nowait()

    async def runtime_handler(**kwargs):
        return kwargs

    ok = p.register_llm_tool(
        name="runtime",
        description="r",
        parameters={"type": "object", "properties": {}},
        handler=runtime_handler,
        timeout=10.0,
    )
    assert ok is True

    msgs: list[dict] = []
    while not ctx.message_queue.empty():
        msgs.append(ctx.message_queue.get_nowait())
    register_msgs = [m for m in msgs if m.get("type") == "LLM_TOOL_REGISTER"]
    assert len(register_msgs) == 1
    assert register_msgs[0]["name"] == "runtime"

    removed = p.unregister_llm_tool("runtime")
    assert removed is True
    assert p.unregister_llm_tool("does_not_exist") is False

    msgs2: list[dict] = []
    while not ctx.message_queue.empty():
        msgs2.append(ctx.message_queue.get_nowait())
    unregister_msgs = [m for m in msgs2 if m.get("type") == "LLM_TOOL_UNREGISTER"]
    assert len(unregister_msgs) == 1
    assert unregister_msgs[0]["name"] == "runtime"


def test_imperative_register_rejects_duplicate():
    from plugin.sdk.plugin import NekoPluginBase, neko_plugin, llm_tool
    from plugin.sdk.shared.models.exceptions import EntryConflictError

    @neko_plugin
    class P(NekoPluginBase):
        @llm_tool(name="dup", description="d")
        async def dup(self, **_):
            return None

    ctx = _make_plugin_ctx()
    p = P(ctx)

    with pytest.raises(EntryConflictError):
        p.register_llm_tool(
            name="dup",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=lambda **_: None,
        )


# ---------------------------------------------------------------------------
# Host-side registry helpers (URL building, source tagging)
# ---------------------------------------------------------------------------


def test_build_callback_url():
    from plugin.server.messaging import llm_tool_registry

    url = llm_tool_registry.build_callback_url("my_plugin", "get_weather")
    # We don't pin the port because env vars may rewrite it; just check
    # the structural pieces are correct.
    assert url.startswith("http://127.0.0.1:")
    assert url.endswith("/api/llm-tools/callback/my_plugin/get_weather")


def test_source_tag_format():
    from plugin.server.messaging.llm_tool_registry import _source_tag

    assert _source_tag("my_plugin") == "plugin:my_plugin"


def test_callback_url_uses_env_override(monkeypatch):
    from plugin.server.messaging import llm_tool_registry

    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "59999")
    url = llm_tool_registry.build_callback_url("p", "t")
    assert url == "http://127.0.0.1:59999/api/llm-tools/callback/p/t"


# ---------------------------------------------------------------------------
# Callback route logic (without an actual main_server in the loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_route_returns_tool_not_registered():
    from plugin.server.routes.llm_tools import llm_tool_callback

    body = {"name": "ghost", "arguments": {"x": 1}, "call_id": "c1", "raw_arguments": '{"x":1}'}
    out = await llm_tool_callback(plugin_id="never_registered", tool_name="ghost", body=body)
    assert out["is_error"] is True
    assert out["error"] == "TOOL_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_callback_route_returns_plugin_not_running(monkeypatch):
    from plugin.server.messaging import llm_tool_registry
    from plugin.server.routes.llm_tools import llm_tool_callback

    # Pretend the plugin registered "x" but the host died.
    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["dead_plugin"]["x"] = {"timeout_seconds": 30.0}

    try:
        body = {"name": "x", "arguments": {}, "call_id": "c1", "raw_arguments": "{}"}
        out = await llm_tool_callback(plugin_id="dead_plugin", tool_name="x", body=body)
        assert out["is_error"] is True
        assert out["error"] == "PLUGIN_NOT_RUNNING"
    finally:
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("dead_plugin", None)


@pytest.mark.asyncio
async def test_callback_route_invokes_host_trigger():
    from plugin.core.state import state
    from plugin.server.messaging import llm_tool_registry
    from plugin.server.routes.llm_tools import llm_tool_callback

    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["live_plugin"]["ping"] = {"timeout_seconds": 90.0}

    captured: dict = {}

    class FakeHost:
        async def trigger(self, entry_id, args, timeout):
            captured["entry_id"] = entry_id
            captured["args"] = args
            captured["timeout"] = timeout
            return {"pong": True}

    state.plugin_hosts["live_plugin"] = FakeHost()
    try:
        body = {"name": "ping", "arguments": {"foo": "bar"}, "call_id": "c1", "raw_arguments": '{"foo":"bar"}'}
        out = await llm_tool_callback(plugin_id="live_plugin", tool_name="ping", body=body)
    finally:
        state.plugin_hosts.pop("live_plugin", None)
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("live_plugin", None)

    assert captured["entry_id"] == "__llm_tool__ping"
    assert captured["args"] == {"foo": "bar"}
    # The route should hand the per-tool timeout to ``host.trigger`` —
    # not the hard-coded 30s default — so a long-running tool doesn't
    # get cut off on the plugin side while main_server is still
    # waiting for the HTTP response.
    assert captured["timeout"] == 90.0
    assert out == {"output": {"pong": True}, "is_error": False}


@pytest.mark.asyncio
async def test_callback_route_falls_back_to_default_timeout_when_unknown():
    """If a desync leaves the registry in a weird state where
    ``has_plugin_tool`` returns True but the timeout is missing, the
    route falls back to the 30s default rather than 5xx-ing."""
    from plugin.core.state import state
    from plugin.server.messaging import llm_tool_registry
    from plugin.server.routes.llm_tools import _DEFAULT_TOOL_TIMEOUT_SECONDS, llm_tool_callback

    async with llm_tool_registry._lock:
        # Empty inner dict — present but no timeout recorded.
        llm_tool_registry._plugin_tools["p"]["t"] = {}

    captured: dict = {}

    class FakeHost:
        async def trigger(self, entry_id, args, timeout):
            captured["timeout"] = timeout
            return {}

    state.plugin_hosts["p"] = FakeHost()
    try:
        await llm_tool_callback(
            plugin_id="p", tool_name="t",
            body={"name": "t", "arguments": {}, "call_id": "c", "raw_arguments": "{}"},
        )
    finally:
        state.plugin_hosts.pop("p", None)
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("p", None)

    assert captured["timeout"] == _DEFAULT_TOOL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_callback_route_passes_through_error_shape():
    """Handler can return the wrapped error shape directly and we
    forward it verbatim instead of wrapping again."""
    from plugin.core.state import state
    from plugin.server.messaging import llm_tool_registry
    from plugin.server.routes.llm_tools import llm_tool_callback

    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["p"]["t"] = {"timeout_seconds": 30.0}

    class FakeHost:
        async def trigger(self, entry_id, args, timeout):
            return {"output": {"reason": "no_data"}, "is_error": True, "error": "NO_DATA"}

    state.plugin_hosts["p"] = FakeHost()
    try:
        out = await llm_tool_callback(
            plugin_id="p", tool_name="t",
            body={"name": "t", "arguments": {}, "call_id": "c", "raw_arguments": "{}"},
        )
    finally:
        state.plugin_hosts.pop("p", None)
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("p", None)

    assert out == {"output": {"reason": "no_data"}, "is_error": True, "error": "NO_DATA"}


@pytest.mark.asyncio
async def test_register_remote_tool_skips_local_tracking_on_ok_false(monkeypatch):
    """``main_server`` can return HTTP 200 with body ``{"ok": false}``
    when no role accepted the registration. The helper must not write
    a local tracking entry in that case — otherwise ``has_plugin_tool``
    lies and we'd dispatch a tool main_server doesn't actually have."""
    from plugin.server.messaging import llm_tool_registry

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""
        def json(self):
            return {
                "ok": False,
                "registered": "ghost",
                "affected_roles": [],
                "failed_roles": [{"role": "Lanlan", "error": "x"}],
                "error": "no role accepted the registration",
            }

    class FakeClient:
        async def post(self, url, json=None):
            return FakeResp()

    monkeypatch.setattr(llm_tool_registry, "_get_http_client", lambda: FakeClient())

    with pytest.raises(RuntimeError, match="rejected register"):
        await llm_tool_registry.register_remote_tool(
            plugin_id="px",
            name="ghost",
            description="d",
            parameters={"type": "object", "properties": {}},
            timeout_seconds=30.0,
        )

    # Most importantly: nothing leaked into local tracking.
    assert not llm_tool_registry.has_plugin_tool("px", "ghost")


@pytest.mark.asyncio
async def test_unregister_remote_tool_keeps_local_on_partial_failure(monkeypatch):
    """``/api/tools/unregister`` returns 200 with ``failed_roles`` when
    only some roles failed to unregister. Local tracking must stay
    intact so the shutdown ``clear`` can still try to wipe the
    stragglers."""
    from plugin.server.messaging import llm_tool_registry

    # Pre-seed local tracking as if registration had succeeded.
    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["px"]["t"] = {"timeout_seconds": 30.0}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""
        def json(self):
            return {
                "ok": False,
                "removed": True,
                "name": "t",
                "affected_roles": ["A"],
                "failed_roles": [{"role": "B", "error": "boom"}],
            }

    class FakeClient:
        async def post(self, url, json=None):
            return FakeResp()

    monkeypatch.setattr(llm_tool_registry, "_get_http_client", lambda: FakeClient())

    try:
        with pytest.raises(RuntimeError, match="failed_roles"):
            await llm_tool_registry.unregister_remote_tool(plugin_id="px", name="t")
        # Critical: local entry preserved so clear_plugin_tools can
        # later attempt to wipe role B.
        assert llm_tool_registry.has_plugin_tool("px", "t")
    finally:
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("px", None)


@pytest.mark.asyncio
async def test_unregister_remote_tool_drops_local_on_full_success(monkeypatch):
    from plugin.server.messaging import llm_tool_registry

    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["px"]["t"] = {"timeout_seconds": 30.0}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""
        def json(self):
            return {
                "ok": True,
                "removed": True,
                "name": "t",
                "affected_roles": ["A"],
                "failed_roles": [],
            }

    class FakeClient:
        async def post(self, url, json=None):
            return FakeResp()

    monkeypatch.setattr(llm_tool_registry, "_get_http_client", lambda: FakeClient())

    try:
        out = await llm_tool_registry.unregister_remote_tool(plugin_id="px", name="t")
        assert out.get("ok") is True
        assert not llm_tool_registry.has_plugin_tool("px", "t")
    finally:
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("px", None)


@pytest.mark.asyncio
async def test_callback_route_handles_timeout():
    from plugin.core.state import state
    from plugin.server.messaging import llm_tool_registry
    from plugin.server.routes.llm_tools import llm_tool_callback

    async with llm_tool_registry._lock:
        llm_tool_registry._plugin_tools["p"]["slow"] = {"timeout_seconds": 30.0}

    class FakeHost:
        async def trigger(self, entry_id, args, timeout):
            raise TimeoutError("boom")

    state.plugin_hosts["p"] = FakeHost()
    try:
        out = await llm_tool_callback(
            plugin_id="p", tool_name="slow",
            body={"name": "slow", "arguments": {}, "call_id": "c", "raw_arguments": "{}"},
        )
    finally:
        state.plugin_hosts.pop("p", None)
        async with llm_tool_registry._lock:
            llm_tool_registry._plugin_tools.pop("p", None)

    assert out["is_error"] is True
    assert out["error"] == "TOOL_TIMEOUT"

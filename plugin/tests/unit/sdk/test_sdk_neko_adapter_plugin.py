from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.sdk.adapter.neko_adapter import NekoAdapterPlugin
from plugin.sdk.adapter.types import AdapterMessage, Protocol, RouteRule, RouteTarget


class _Logger:
    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


class _Ctx:
    def __init__(self, root: Path):
        self.plugin_id = "adapter_demo"
        self.config_path = root / "plugin.toml"
        self.config_path.write_text("[plugin]\nid='adapter_demo'\n", encoding="utf-8")
        self.logger = _Logger()
        self.message_queue = SimpleNamespace(put_nowait=lambda payload: None)
        self._entry_map: dict[str, object] = {}

    async def get_own_config(self, timeout: float = 5.0):
        return {
            "config": {
                "plugin": {"store": {"enabled": False}, "database": {"enabled": False}},
                "adapter": {"mode": "hybrid", "priority": 1},
            }
        }

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return await self.get_own_config(timeout=timeout)

    async def get_own_base_config(self, timeout: float = 5.0):
        return await self.get_own_config(timeout=timeout)

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"data": {"config_profiles": {}}}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"data": {"config": {}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
        return {"config": updates}

    async def trigger_plugin_event_async(self, **kwargs):
        return {"ok": True, **kwargs}

    def trigger_plugin_event(self, **kwargs):
        return {"ok": True, **kwargs}

    def query_plugins(self, filters: dict[str, object], timeout: float = 5.0):
        return {"plugins": [{"plugin_id": "adapter_demo"}]}


class _AdapterPlugin(NekoAdapterPlugin):
    pass


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_neko_adapter_plugin_startup_and_registration(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    plugin = _AdapterPlugin(ctx)

    await plugin.adapter_startup()
    assert plugin.adapter_id == "adapter_demo"
    assert plugin.adapter_mode.value == "hybrid"

    plugin.register_adapter_tool("echo", lambda text=None, **kwargs: {"text": text})
    plugin.register_adapter_resource("res://x", lambda **kwargs: {"ok": True})
    assert "echo" in plugin.list_adapter_tools()
    assert "res://x" in plugin.list_adapter_resources()
    assert plugin.get_adapter_tool("echo") is not None
    assert plugin.get_adapter_resource("res://x") is not None

    await plugin.register_adapter_tool_as_entry("dyn_tool", lambda **kwargs: {"ok": True})
    assert plugin.get_adapter_tool("dyn_tool") is not None
    assert await plugin.unregister_adapter_tool_entry("dyn_tool") is True

    route = RouteRule(protocol="mcp", action="tool_call", pattern="echo", target=RouteTarget.SELF, priority=10)
    plugin.add_adapter_route(route)

    msg = AdapterMessage(
        id="1",
        protocol=Protocol.MCP,
        action="tool_call",
        payload={"name": "echo", "arguments": {"text": "hello"}},
    )
    resp = await plugin.handle_adapter_message(msg)
    assert resp is not None
    assert resp.success is True

    forward_route = RouteRule(protocol="mcp", action="tool_call", pattern="fwd", target=RouteTarget.PLUGIN, plugin_id="p1", entry="run")
    plugin.add_adapter_route(forward_route)
    fwd_msg = AdapterMessage(id="2", protocol=Protocol.MCP, action="tool_call", payload={"name": "fwd", "arguments": {"x": 1}})
    fwd_resp = await plugin.handle_adapter_message(fwd_msg)
    assert fwd_resp is not None
    assert fwd_resp.success is True

    await plugin.adapter_shutdown()
    assert plugin.list_adapter_tools() == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_neko_adapter_plugin_invalid_route_returns_error(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    plugin = _AdapterPlugin(ctx)
    await plugin.adapter_startup()

    bad_route = RouteRule(protocol="mcp", action="tool_call", pattern="bad", target=RouteTarget.PLUGIN)
    plugin.add_adapter_route(bad_route)

    msg = AdapterMessage(id="x", protocol=Protocol.MCP, action="tool_call", payload={"name": "bad"})
    resp = await plugin.handle_adapter_message(msg)
    assert resp is not None
    assert resp.success is False

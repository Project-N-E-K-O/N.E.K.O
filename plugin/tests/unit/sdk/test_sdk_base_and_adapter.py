from __future__ import annotations

import asyncio
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

from plugin.sdk.adapter.base import AdapterBase, AdapterConfig, AdapterContext
from plugin.sdk.adapter.decorators import (
    ADAPTER_EVENT_META,
    ADAPTER_LIFECYCLE_META,
    on_adapter_event,
    on_adapter_shutdown,
    on_adapter_startup,
    on_mcp_tool,
    on_nonebot_message,
)
from plugin.sdk.adapter.types import AdapterMessage, Protocol, RouteRule, RouteTarget
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import plugin_entry
from plugin.sdk.router import PluginRouter


class _Logger:
    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


class _Ctx:
    def __init__(self, root: Path):
        self.plugin_id = "demo"
        self.config_path = root / "plugin.toml"
        self.config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
        self.logger = _Logger()
        self.message_queue: Queue[dict[str, object]] = Queue()
        self._entry_map: dict[str, object] = {}

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": {"plugin": {"store": {"enabled": False}, "database": {"enabled": False}}, "runtime": {"enabled": True}}}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return {"config": {"plugin": {"store": {"enabled": False}, "database": {"enabled": False}}}}

    async def get_own_base_config(self, timeout: float = 5.0):
        return await self.get_own_config(timeout)

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"data": {"config_profiles": {}}}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"data": {"config": {}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
        return {"config": updates}

    def trigger_plugin_event(self, **kwargs):
        return {"ok": True, **kwargs}

    def query_plugins(self, filters: dict[str, object], timeout: float = 5.0):
        return {"plugins": [{"plugin_id": "demo"}]}

    def update_status(self, status: dict[str, object]) -> None:
        self._last_status = status


class _MyRouter(PluginRouter):
    @plugin_entry(id="r_hello")
    def hello(self, **kwargs):
        return {"ok": True}


class _MyPlugin(NekoPluginBase):
    @plugin_entry(id="hello")
    def hello(self, **kwargs):
        return {"ok": True}


class _DummyAdapter(AdapterBase):
    async def on_startup(self) -> None:
        return None

    async def on_shutdown(self) -> None:
        return None


@pytest.mark.plugin_unit
def test_neko_plugin_base_router_dynamic_entries_and_status(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    plugin = _MyPlugin(ctx)

    assert plugin.get_input_schema() == {}

    router = _MyRouter(prefix="x_")
    plugin.include_router(router)
    assert "x_r_hello" in plugin.collect_entries(wrap_with_hooks=False)
    assert plugin.get_router(router.name) is router
    assert router.name in plugin.list_routers()

    async def dyn(**kwargs):
        return {"ok": True}

    asyncio.run(plugin.register_dynamic_entry("dyn", dyn, name="Dyn"))
    assert plugin.is_entry_enabled("dyn") is True
    asyncio.run(plugin.disable_entry("dyn"))
    assert plugin.is_entry_enabled("dyn") is False
    asyncio.run(plugin.enable_entry("dyn"))
    assert plugin.is_entry_enabled("dyn") is True

    entries = plugin.list_entries(include_disabled=True)
    assert any(item["id"] == "dyn" for item in entries)

    assert asyncio.run(plugin.unregister_dynamic_entry("dyn")) is True
    assert asyncio.run(plugin.unregister_dynamic_entry("missing")) is False

    plugin.report_status({"alive": True})
    assert getattr(ctx, "_last_status", {}).get("alive") is True

    assert plugin.exclude_router(router) is True
    assert plugin.exclude_router("missing") is False


@pytest.mark.plugin_unit
def test_neko_plugin_static_ui_registration_and_file_logger(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    plugin = _MyPlugin(ctx)

    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    assert plugin.register_static_ui("static") is True
    cfg = plugin.get_static_ui_config()
    assert isinstance(cfg, dict)
    assert cfg["enabled"] is True

    file_logger = plugin.enable_file_logging(log_level="INFO", max_bytes=1024, backup_count=1, max_files=2)
    assert file_logger is not None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_adapter_types_decorators_context_and_base(tmp_path: Path) -> None:
    msg = AdapterMessage(id="1", protocol=Protocol.MCP, action="tool_call", payload={"name": "hello"})
    ok_resp = msg.reply({"x": 1})
    err_resp = msg.error("bad", code="E")
    assert ok_resp.to_dict()["success"] is True
    assert err_resp.to_dict()["success"] is False

    rule = RouteRule(protocol="mcp", action="tool_*", pattern="he*", target=RouteTarget.SELF)
    assert rule.matches(msg) is True

    @on_adapter_event(protocol="mcp", action="tool_*", pattern="he*", priority=1)
    async def h1():
        return None

    @on_adapter_startup
    async def s1():
        return None

    @on_adapter_shutdown(priority=2)
    async def s2():
        return None

    @on_mcp_tool("he*")
    async def h2():
        return None

    @on_nonebot_message("group")
    async def h3():
        return None

    assert getattr(h1, ADAPTER_EVENT_META).matches("mcp", "tool_call", "hello") is True
    assert getattr(s1, ADAPTER_LIFECYCLE_META)["type"] == "startup"
    assert getattr(s2, ADAPTER_LIFECYCLE_META)["priority"] == 2
    assert getattr(h2, ADAPTER_EVENT_META).protocol == "mcp"
    assert "message." in getattr(h3, ADAPTER_EVENT_META).action

    log = _Logger()
    cfg = AdapterConfig.from_dict({"mode": "gateway", "protocols": {"mcp": {}}, "routes": [], "priority": "3"})
    assert cfg.priority == 3

    plugin_ctx = SimpleNamespace(
        trigger_plugin_event_async=lambda **kwargs: asyncio.sleep(0, result={"ok": True, **kwargs})
    )
    actx = AdapterContext(adapter_id="a1", config=cfg, logger=log, plugin_ctx=plugin_ctx)
    actx.register_event_handler("k", lambda: 1)
    assert len(actx.get_event_handlers("k")) == 1
    call_result = await actx.call_plugin("p", "e", {"x": 1})
    assert call_result["ok"] is True

    with pytest.raises(NotImplementedError):
        await actx.broadcast_event("evt", {"x": 1})

    adapter = _DummyAdapter(config=cfg, ctx=actx)
    assert adapter.adapter_id == "a1"
    assert adapter.mode.value == "gateway"

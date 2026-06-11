from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class _Config:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def dump(self) -> dict[str, object]:
        return self.payload


@pytest.fixture()
def mcp_adapter_plugin(monkeypatch):
    markdownify_stub = types.ModuleType("markdownify")
    markdownify_stub.markdownify = lambda html, **_kwargs: str(html)
    bs4_stub = types.ModuleType("bs4")
    bs4_stub.BeautifulSoup = lambda html, *_args, **_kwargs: SimpleNamespace(body=None, __str__=lambda: str(html))
    monkeypatch.setitem(sys.modules, "markdownify", markdownify_stub)
    monkeypatch.setitem(sys.modules, "bs4", bs4_stub)
    from plugin.plugins.mcp_adapter import MCPAdapterPlugin

    return MCPAdapterPlugin


def _plugin_stub(MCPAdapterPlugin):
    plugin = object.__new__(MCPAdapterPlugin)
    plugin._shutdown = False
    plugin._clients = {}
    plugin._server_states = {}
    plugin._connect_tasks = {}
    plugin._pending_auto_connect = {}
    plugin._reconnect_tasks = {}
    plugin._servers_config = {}
    plugin.config = _Config({})
    plugin.ctx = SimpleNamespace(logger=_Logger())
    return plugin


def test_remove_cancels_pending_and_active_connect_task(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        started = asyncio.Event()

        async def fake_connect(server_name: str, server_cfg: dict[str, object], timeout: float) -> bool:
            started.set()
            await asyncio.sleep(60)
            return True

        plugin._connect_server = fake_connect  # type: ignore[method-assign]
        plugin._pending_auto_connect["example"] = ({"transport": "stdio"}, 30.0)

        assert plugin._schedule_connect_server("example", {"transport": "stdio"}, 30.0)
        await started.wait()

        assert plugin._cancel_connect_task("example")
        await asyncio.sleep(0)

        assert "example" not in plugin._connect_tasks
        assert "example" not in plugin._pending_auto_connect

    asyncio.run(scenario())


def test_connect_server_skips_removed_server_before_start(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        plugin._servers_config = {}

        connected = await mcp_adapter_plugin._connect_server(
            plugin,
            "removed",
            {"transport": "stdio", "command": "never-run"},
            1.0,
        )

        assert connected is False
        assert "removed" not in plugin._clients

    asyncio.run(scenario())


def test_pending_auto_connect_is_scheduled_lazily(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        started = asyncio.Event()

        async def fake_connect(server_name: str, server_cfg: dict[str, object], timeout: float) -> bool:
            started.set()
            await asyncio.sleep(60)
            return True

        plugin._connect_server = fake_connect  # type: ignore[method-assign]
        plugin._pending_auto_connect["example"] = ({"transport": "stdio"}, 30.0)

        mcp_adapter_plugin._schedule_pending_auto_connects(plugin)

        await started.wait()
        assert "example" not in plugin._pending_auto_connect
        assert "example" in plugin._connect_tasks

        assert plugin._cancel_connect_task("example")

    asyncio.run(scenario())


def test_command_loop_start_schedules_pending_auto_connects(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        started = asyncio.Event()

        async def fake_connect(server_name: str, server_cfg: dict[str, object], timeout: float) -> bool:
            started.set()
            await asyncio.sleep(60)
            return True

        plugin._connect_server = fake_connect  # type: ignore[method-assign]
        plugin._pending_auto_connect["example"] = ({"transport": "stdio"}, 30.0)

        await plugin._on_command_loop_start()

        await started.wait()
        assert "example" not in plugin._pending_auto_connect
        assert "example" in plugin._connect_tasks
        assert plugin._cancel_connect_task("example")

    asyncio.run(scenario())


def test_remove_servers_cancels_connect_task_and_persists_without_server(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        plugin._servers_config = {"example": {"transport": "stdio", "command": "uvx"}}
        plugin.config = _Config({"mcp_servers": {"example": {"transport": "stdio", "command": "uvx"}}})
        persisted: dict[str, object] = {}
        started = asyncio.Event()

        async def fake_connect(server_name: str, server_cfg: dict[str, object], timeout: float) -> bool:
            started.set()
            await asyncio.sleep(60)
            return True

        async def fake_persist(servers_config: dict[str, object]) -> None:
            persisted.update(servers_config)

        plugin._connect_server = fake_connect  # type: ignore[method-assign]
        plugin._persist_servers_config = fake_persist  # type: ignore[method-assign]

        assert plugin._schedule_connect_server("example", {"transport": "stdio", "command": "uvx"}, 30.0)
        await started.wait()

        result = await mcp_adapter_plugin.remove_servers(plugin, ["example"])

        assert result.value["removed"] == ["example"]
        assert persisted == {}
        assert "example" not in plugin._connect_tasks
        assert "example" not in plugin._servers_config
        assert "example" not in plugin._server_states

    asyncio.run(scenario())


def test_cancelled_connect_cleans_half_initialized_client(mcp_adapter_plugin) -> None:
    async def scenario() -> None:
        plugin = _plugin_stub(mcp_adapter_plugin)
        plugin._servers_config = {"example": {"transport": "stdio", "command": "uvx"}}
        disconnected = asyncio.Event()

        class FakeClient:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def set_disconnect_callback(self, _callback) -> None:
                pass

            async def connect(self, timeout: float) -> bool:
                raise asyncio.CancelledError()

            async def disconnect(self) -> None:
                disconnected.set()

        original_client = mcp_adapter_plugin.__init__.__globals__["MCPClient"]
        mcp_adapter_plugin.__init__.__globals__["MCPClient"] = FakeClient
        try:
            task = asyncio.create_task(mcp_adapter_plugin._connect_server(plugin, "example", {"transport": "stdio", "command": "uvx"}, 30.0))
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            mcp_adapter_plugin.__init__.__globals__["MCPClient"] = original_client

        assert disconnected.is_set()

    asyncio.run(scenario())

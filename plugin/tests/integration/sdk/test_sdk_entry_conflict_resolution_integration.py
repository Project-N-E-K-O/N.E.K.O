from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import plugin_entry
from plugin.sdk.plugins import PluginCallError, Plugins
from plugin.sdk.router import PluginRouter


class _Logger:
    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _MsgQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put_nowait(self, item: dict[str, Any]) -> None:
        self.items.append(item)


class _Runtime:
    def __init__(self) -> None:
        self.plugins: dict[str, NekoPluginBase] = {}

    async def dispatch(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Any:
        _ = timeout
        if event_type != "plugin_entry":
            raise PluginCallError(f"Unsupported event_type: {event_type}")
        plugin = self.plugins.get(target_plugin_id)
        if plugin is None:
            raise PluginCallError(f"Target plugin not found: {target_plugin_id}")
        handler = plugin.collect_entries(wrap_with_hooks=True).get(event_id)
        if handler is None:
            raise PluginCallError(f"Entry not found: {target_plugin_id}:{event_id}")
        out = handler.handler(**params)
        if inspect.isawaitable(out):
            out = await out
        return out


@dataclass
class _Ctx:
    plugin_id: str
    runtime: _Runtime
    config_path: Path
    logger: _Logger
    message_queue: _MsgQueue

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": {"plugin": {"store": {"enabled": False}, "database": {"enabled": False}}}}

    async def trigger_plugin_event_async(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Any:
        return await self.runtime.dispatch(
            target_plugin_id=target_plugin_id,
            event_type=event_type,
            event_id=event_id,
            params=params,
            timeout=timeout,
        )

    async def query_plugins_async(self, filters: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        _ = (filters, timeout)
        return {"plugins": [{"plugin_id": k} for k in sorted(self.runtime.plugins.keys())]}


class _RouterA(PluginRouter):
    @plugin_entry(id="dup", name="router_a_dup")
    async def dup(self, **_kwargs: Any):
        return {"source": "router_a"}


class _RouterB(PluginRouter):
    @plugin_entry(id="dup", name="router_b_dup")
    async def dup(self, **_kwargs: Any):
        return {"source": "router_b"}


class _DirectRouter(PluginRouter):
    @plugin_entry(id="dup", name="router_direct_dup")
    async def dup(self, **_kwargs: Any):
        return {"source": "router_direct"}


class _HostPlugin(NekoPluginBase):
    @plugin_entry(id="dup", name="plugin_dup")
    async def own_dup(self, **_kwargs: Any):
        return {"source": "plugin"}


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_router_router_entry_id_conflict_last_included_wins(tmp_path: Path) -> None:
    runtime = _Runtime()
    logger = _Logger()
    mq = _MsgQueue()

    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _HostPlugin(caller_ctx)

    host.include_router(_RouterA(prefix="r_"))
    host.include_router(_RouterB(prefix="r_"))

    plugins = Plugins(ctx=caller_ctx)
    out = await plugins.call_entry_async("host:r_dup", {}, timeout=2.0)
    assert out["source"] == "router_b"

    entries = {e["id"]: e for e in host.list_entries(include_disabled=True)}
    assert entries["r_dup"]["name"] == "router_b_dup"


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_plugin_router_entry_id_conflict_router_overrides_plugin(tmp_path: Path) -> None:
    runtime = _Runtime()
    logger = _Logger()
    mq = _MsgQueue()

    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _HostPlugin(caller_ctx)

    # router entry uses the same id as plugin entry: "dup"
    host.include_router(_DirectRouter())

    plugins = Plugins(ctx=caller_ctx)
    out = await plugins.call_entry_async("host:dup", {}, timeout=2.0)
    assert out["source"] == "router_direct"

    entries = [e for e in host.list_entries(include_disabled=True) if e["id"] == "dup"]
    assert len(entries) == 1
    assert entries[0]["name"] == "router_direct_dup"

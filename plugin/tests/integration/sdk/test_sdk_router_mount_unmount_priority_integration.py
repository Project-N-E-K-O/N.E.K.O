from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import hook, plugin_entry
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

        entries = plugin.collect_entries(wrap_with_hooks=True)
        event_handler = entries.get(event_id)
        if event_handler is None:
            raise PluginCallError(f"Entry not found: {target_plugin_id}:{event_id}")

        out = event_handler.handler(**params)
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


class _TargetRouter(PluginRouter):
    @plugin_entry(id="work")
    async def work(self, x: int = 0, trace: list[str] | None = None, **_kwargs: Any):
        t = list(trace or [])
        t.append("entry")
        return {"ok": True, "x": x, "trace": t}


class _HighPriorityHooksRouter(PluginRouter):
    @hook(target="r_work", timing="before", priority=20)
    async def before_high(self, entry_id: str, params: dict[str, Any], **_kwargs: Any):
        assert entry_id == "r_work"
        out = dict(params)
        out["x"] = int(out.get("x", 0)) + 10
        tr = list(out.get("trace", []))
        tr.append("before_high")
        out["trace"] = tr
        return out

    @hook(target="r_work", timing="after", priority=20)
    async def after_high(
        self,
        entry_id: str,
        params: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ):
        assert entry_id == "r_work"
        out = dict(result)
        tr = list(out.get("trace", []))
        tr.append("after_high")
        out["trace"] = tr
        out["seen_x_high"] = params.get("x")
        return out


class _LowPriorityHooksRouter(PluginRouter):
    @hook(target="r_work", timing="before", priority=5)
    async def before_low(self, entry_id: str, params: dict[str, Any], **_kwargs: Any):
        assert entry_id == "r_work"
        out = dict(params)
        out["x"] = int(out.get("x", 0)) * 2
        tr = list(out.get("trace", []))
        tr.append("before_low")
        out["trace"] = tr
        return out

    @hook(target="r_work", timing="after", priority=1)
    async def after_low(
        self,
        entry_id: str,
        params: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ):
        assert entry_id == "r_work"
        out = dict(result)
        tr = list(out.get("trace", []))
        tr.append("after_low")
        out["trace"] = tr
        out["seen_x_low"] = params.get("x")
        return out


class _HostPlugin(NekoPluginBase):
    pass


class _CallerPlugin(NekoPluginBase):
    pass


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_router_include_exclude_with_hook_priority_and_plugins_call(tmp_path: Path) -> None:
    runtime = _Runtime()
    logger = _Logger()
    mq = _MsgQueue()

    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    _caller = _CallerPlugin(caller_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _caller

    target_router = _TargetRouter(prefix="r_")
    high_router = _HighPriorityHooksRouter()
    low_router = _LowPriorityHooksRouter()
    host.include_router(target_router)
    host.include_router(high_router)
    host.include_router(low_router)

    plugins = Plugins(ctx=caller_ctx)

    # with both hook routers:
    # before_high: x=1 -> 11
    # before_low: x=11 -> 22
    first = await plugins.call_entry_async("host:r_work", {"x": 1}, timeout=2.0)
    assert first["ok"] is True
    assert first["x"] == 22
    assert first["trace"] == ["before_high", "before_low", "entry", "after_high", "after_low"]
    assert first["seen_x_high"] == 22
    assert first["seen_x_low"] == 22

    # exclude high-priority hook router -> only low hooks remain
    assert host.exclude_router(high_router) is True
    second = await plugins.call_entry_async("host:r_work", {"x": 1}, timeout=2.0)
    assert second["x"] == 2
    assert second["trace"] == ["before_low", "entry", "after_low"]
    assert "seen_x_high" not in second
    assert second["seen_x_low"] == 2

    # exclude target router -> entry removed
    assert host.exclude_router(target_router) is True
    with pytest.raises(PluginCallError):
        await plugins.call_entry_async("host:r_work", {"x": 1}, timeout=2.0)

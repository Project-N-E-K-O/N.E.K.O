from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import hook
from plugin.sdk.plugins import PluginCallError, Plugins
from plugin.sdk.responses import fail


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
        if params.get("force_timeout") is True:
            raise TimeoutError(f"simulated timeout after {timeout}s")
        if event_type != "plugin_entry":
            raise PluginCallError(f"Unsupported event_type: {event_type}")

        plugin = self.plugins.get(target_plugin_id)
        if plugin is None:
            raise PluginCallError(f"Target plugin not found: {target_plugin_id}")

        entries = plugin.collect_entries(wrap_with_hooks=True)
        event_handler = entries.get(event_id)
        if event_handler is None:
            raise PluginCallError(f"Entry not found: {target_plugin_id}:{event_id}")
        if getattr(event_handler.meta, "enabled", True) is False:
            raise PluginCallError(f"Entry disabled: {target_plugin_id}:{event_id}")

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


class _HostPlugin(NekoPluginBase):
    @hook(target="dyn", timing="before", priority=10)
    async def before_dyn(self, entry_id: str, params: dict[str, Any], **_kwargs: Any):
        assert entry_id == "dyn"
        if params.get("block") is True:
            return fail("BLOCKED", "blocked by before hook")
        out = dict(params)
        out["x"] = int(out.get("x", 0)) + 1
        return out

    @hook(target="dyn", timing="after")
    async def after_dyn(
        self,
        entry_id: str,
        params: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ):
        assert entry_id == "dyn"
        out = dict(result)
        out["after"] = True
        out["seen_x"] = params.get("x")
        return out


class _CallerPlugin(NekoPluginBase):
    pass


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_dynamic_entry_enable_disable_unregister_with_plugins_call_entry_async(tmp_path: Path) -> None:
    runtime = _Runtime()
    mq = _MsgQueue()
    logger = _Logger()

    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    _caller = _CallerPlugin(caller_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _caller

    async def _dyn_handler(**kwargs: Any):
        return {"ok": True, "x": kwargs.get("x", 0)}

    assert await host.register_dynamic_entry("dyn", _dyn_handler, name="dyn") is True
    assert any(item.get("action") == "register" and item.get("entry_id") == "dyn" for item in mq.items)

    plugins = Plugins(ctx=caller_ctx)

    # normal success path (+ before/after hooks)
    ok_res = await plugins.call_entry_async("host:dyn", {"x": 1}, timeout=2.0)
    assert ok_res["ok"] is True
    assert ok_res["x"] == 2
    assert ok_res["after"] is True
    assert ok_res["seen_x"] == 2

    # early return by before hook (error envelope)
    blocked = await plugins.call_entry_async("host:dyn", {"x": 1, "block": True}, timeout=2.0)
    assert blocked["success"] is False
    assert blocked["error"]["code"] == "BLOCKED"
    assert "blocked" in blocked["error"]["message"]

    # timeout propagation
    with pytest.raises(TimeoutError):
        await plugins.call_entry_async("host:dyn", {"force_timeout": True}, timeout=0.01)

    # disable -> call should fail
    assert await host.disable_entry("dyn") is True
    with pytest.raises(PluginCallError):
        await plugins.call_entry_async("host:dyn", {"x": 1}, timeout=2.0)

    # re-enable -> call recovers
    assert await host.enable_entry("dyn") is True
    ok_res2 = await plugins.call_entry_async("host:dyn", {"x": 2}, timeout=2.0)
    assert ok_res2["x"] == 3

    # unregister -> call fails
    assert await host.unregister_dynamic_entry("dyn") is True
    with pytest.raises(PluginCallError):
        await plugins.call_entry_async("host:dyn", {"x": 1}, timeout=2.0)

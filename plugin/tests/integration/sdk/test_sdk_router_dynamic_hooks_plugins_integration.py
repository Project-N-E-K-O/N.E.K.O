from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import hook
from plugin.sdk.plugins import PluginCallError, Plugins
from plugin.sdk.router import PluginRouter


class _Logger:
    def debug(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - test logger
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - test logger
        return None

    def warning(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - test logger
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - test logger
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
            raise PluginCallError(f"Unsupported event_type in test runtime: {event_type}")
        plugin = self.plugins.get(target_plugin_id)
        if plugin is None:
            raise PluginCallError(f"Target plugin not found: {target_plugin_id}")

        entries = plugin.collect_entries(wrap_with_hooks=True)
        event_handler = entries.get(event_id)
        if event_handler is None:
            raise PluginCallError(f"Entry not found: {target_plugin_id}:{event_id}")
        if getattr(event_handler.meta, "enabled", True) is False:
            raise PluginCallError(f"Entry disabled: {target_plugin_id}:{event_id}")

        handler = event_handler.handler
        result = handler(**params)
        if inspect.isawaitable(result):
            result = await result
        return result


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


class _HostRouter(PluginRouter):
    @hook(target="r_dyn", timing="before", priority=10)
    async def before_dyn(self, entry_id: str, params: dict[str, Any], **_kwargs: Any):
        assert entry_id == "r_dyn"
        out = dict(params)
        out["value"] = int(out.get("value", 0)) + 1
        return out

    @hook(target="r_dyn", timing="after")
    async def after_dyn(
        self,
        entry_id: str,
        params: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ):
        assert entry_id == "r_dyn"
        out = dict(result)
        out["after_hook"] = True
        out["seen_value"] = params.get("value")
        return out


class _HostPlugin(NekoPluginBase):
    pass


class _CallerPlugin(NekoPluginBase):
    pass


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_router_dynamic_entry_hooks_plugins_call_entry_async_full_flow(tmp_path: Path) -> None:
    runtime = _Runtime()
    mq = _MsgQueue()
    logger = _Logger()

    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)

    host = _HostPlugin(host_ctx)
    caller = _CallerPlugin(caller_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = caller

    router = _HostRouter(prefix="r_")
    host.include_router(router)

    async def _dynamic_handler(**kwargs: Any):
        return {"ok": True, "value": kwargs.get("value", 0)}

    added = await router.add_entry("dyn", _dynamic_handler, name="dyn")
    assert added is True
    assert any(item.get("action") == "register" and item.get("entry_id") == "r_dyn" for item in mq.items)

    plugins = Plugins(ctx=caller_ctx)
    result = await plugins.call_entry_async("host:r_dyn", {"value": 1}, timeout=2.0)
    assert result["ok"] is True
    assert result["value"] == 2  # before hook increments
    assert result["after_hook"] is True
    assert result["seen_value"] == 2

    removed = await router.remove_entry("dyn")
    assert removed is True
    with pytest.raises(PluginCallError):
        await plugins.call_entry_async("host:r_dyn", {"value": 1}, timeout=2.0)

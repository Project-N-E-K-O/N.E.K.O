from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.plugins import PluginCallError, Plugins


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
    pass


class _CallerPlugin(NekoPluginBase):
    pass


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_concurrent_enable_disable_with_call_entry_async(tmp_path: Path) -> None:
    runtime = _Runtime()
    logger = _Logger()
    mq = _MsgQueue()
    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    _caller = _CallerPlugin(caller_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _caller

    async def _dyn(**kwargs: Any):
        # Force scheduling points so control/call tasks can interleave.
        await asyncio.sleep(0)
        return {"ok": True, "v": kwargs.get("v", 0)}

    assert await host.register_dynamic_entry("dyn", _dyn, name="dyn") is True
    plugins = Plugins(ctx=caller_ctx)

    results: list[str] = []

    async def _toggle() -> None:
        for _ in range(25):
            await host.disable_entry("dyn")
            await asyncio.sleep(0.001)  # keep disabled window
            await host.enable_entry("dyn")
            await asyncio.sleep(0.001)  # keep enabled window

    async def _caller_task() -> None:
        for _ in range(220):
            try:
                out = await plugins.call_entry_async("host:dyn", {"v": 1}, timeout=1.0)
                if isinstance(out, dict) and out.get("ok") is True:
                    results.append("ok")
                else:
                    results.append("bad_payload")
            except PluginCallError:
                # expected when disabled window hits
                results.append("disabled")
            await asyncio.sleep(0)

    await asyncio.gather(_toggle(), _caller_task())

    assert "ok" in results
    assert "bad_payload" not in results
    assert "disabled" in results
    # only expected outcomes
    assert set(results).issubset({"ok", "disabled"})


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_concurrent_register_unregister_with_call_entry_async(tmp_path: Path) -> None:
    runtime = _Runtime()
    logger = _Logger()
    mq = _MsgQueue()
    host_ctx = _Ctx("host", runtime, tmp_path / "host.toml", logger, mq)
    caller_ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", logger, mq)
    host = _HostPlugin(host_ctx)
    _caller = _CallerPlugin(caller_ctx)
    runtime.plugins["host"] = host
    runtime.plugins["caller"] = _caller

    async def _dyn(**kwargs: Any):
        await asyncio.sleep(0)
        return {"ok": True, "v": kwargs.get("v", 0)}

    plugins = Plugins(ctx=caller_ctx)
    outcomes: list[str] = []

    async def _mutator() -> None:
        for _ in range(30):
            await host.register_dynamic_entry("dyn", _dyn, name="dyn")
            await asyncio.sleep(0.001)  # keep registered window
            await host.unregister_dynamic_entry("dyn")
            await asyncio.sleep(0.001)  # keep unregistered window

    async def _invoker() -> None:
        for _ in range(260):
            try:
                out = await plugins.call_entry_async("host:dyn", {"v": 2}, timeout=1.0)
                if isinstance(out, dict) and out.get("ok") is True:
                    outcomes.append("ok")
                else:
                    outcomes.append("bad_payload")
            except PluginCallError:
                outcomes.append("missing")
            await asyncio.sleep(0)

    await asyncio.gather(_mutator(), _invoker())

    assert "ok" in outcomes
    assert "missing" in outcomes
    assert "bad_payload" not in outcomes
    assert set(outcomes).issubset({"ok", "missing"})

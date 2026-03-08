from __future__ import annotations

import asyncio
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugin.core.context import PluginContext
from plugin.core.state import state
from plugin.sdk.bus.bus_list import BusListWatcherCore
from plugin.sdk.bus.rev import dispatch_bus_change
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.config import PluginConfig
from plugin.sdk.decorators import hook, plugin_entry
from plugin.sdk.plugins import Plugins
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

    def exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass
class _ContextSet:
    caller: PluginContext
    host: PluginContext
    req_queue: "queue.Queue[dict[str, Any]]"


class _HostPlugin(NekoPluginBase):
    @hook(target="sum", timing="before", priority=10)
    async def before_sum(self, entry_id: str, params: dict[str, Any], **_kwargs: Any):
        assert entry_id == "sum"
        out = dict(params)
        out["a"] = int(out.get("a", 0)) + 1
        return out

    @hook(target="sum", timing="after")
    async def after_sum(
        self,
        entry_id: str,
        params: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ):
        assert entry_id == "sum"
        out = dict(result)
        out["after"] = True
        out["seen_a"] = params.get("a")
        return out

    @plugin_entry(id="sum")
    async def sum(self, a: int = 0, b: int = 0, **_kwargs: Any):
        return {"value": int(a) + int(b)}

    @plugin_entry(id="slow")
    async def slow(self, sleep_s: float = 0.0, **_kwargs: Any):
        await asyncio.sleep(float(sleep_s))
        return {"ok": True}


class _HostBridge:
    """Minimal in-process host bridge using real PluginContext wire protocol."""

    def __init__(
        self,
        *,
        request_queue: "queue.Queue[dict[str, Any]]",
        response_queues: dict[str, "asyncio.Queue[dict[str, Any]]"],
        plugins: dict[str, NekoPluginBase],
        configs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._request_queue = request_queue
        self._response_queues = response_queues
        self._plugins = plugins
        self._configs = configs or {}
        self._stop = False
        self._task: asyncio.Task[None] | None = None
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._sub_seq = 0
        self._subs: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._serve())

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            await self._task
        if self._active_tasks:
            await asyncio.gather(*list(self._active_tasks), return_exceptions=True)

    async def _serve(self) -> None:
        while not self._stop:
            try:
                req = await asyncio.to_thread(self._request_queue.get, True, 0.05)
            except queue.Empty:
                continue
            task = asyncio.create_task(self._handle(req))
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _handle(self, req: dict[str, Any]) -> None:
        rid = str(req.get("request_id", "")).strip()
        from_plugin = str(req.get("from_plugin", "")).strip()
        timeout = float(req.get("timeout", 1.0) or 1.0)
        req_type = str(req.get("type", "")).strip()

        if not rid:
            return

        result: Any = None
        error: dict[str, Any] | None = None

        try:
            if req_type == "PLUGIN_QUERY":
                result = {"plugins": [{"plugin_id": pid} for pid in sorted(self._plugins.keys())]}
            elif req_type == "PLUGIN_TO_PLUGIN":
                to_plugin = str(req.get("to_plugin", "")).strip()
                event_type = str(req.get("event_type", "")).strip()
                event_id = str(req.get("event_id", "")).strip()
                args = req.get("args", {})
                if not isinstance(args, dict):
                    args = {}

                # test-only fault injection for timeout/orphan scenarios
                if args.get("__drop_response__") is True:
                    return

                plugin = self._plugins.get(to_plugin)
                if plugin is None:
                    raise RuntimeError(f"Target plugin not found: {to_plugin}")
                if event_type != "plugin_entry":
                    raise RuntimeError(f"Unsupported event_type: {event_type}")

                event_handler = plugin.collect_entries(wrap_with_hooks=True).get(event_id)
                if event_handler is None:
                    raise RuntimeError(f"Entry not found: {to_plugin}:{event_id}")

                out = event_handler.handler(**args)
                if asyncio.iscoroutine(out):
                    out = await out
                result = out
            elif req_type == "PLUGIN_CONFIG_GET":
                pid = str(req.get("plugin_id", "")).strip()
                result = {"plugin_id": pid, "config": dict(self._configs.get(pid, {}))}
            elif req_type == "PLUGIN_CONFIG_BASE_GET":
                pid = str(req.get("plugin_id", "")).strip()
                result = {"plugin_id": pid, "config": {"base": True, **dict(self._configs.get(pid, {}))}}
            elif req_type == "PLUGIN_CONFIG_PROFILES_GET":
                result = {"config_profiles": {"active": "dev", "files": {"dev": "dev.toml"}}}
            elif req_type == "PLUGIN_CONFIG_PROFILE_GET":
                pname = str(req.get("profile_name", "")).strip() or "default"
                result = {"plugin_id": req.get("plugin_id"), "config": {"runtime": {"profile": pname}}}
            elif req_type == "PLUGIN_CONFIG_EFFECTIVE_GET":
                pname = req.get("profile_name")
                if isinstance(pname, str) and pname.strip():
                    result = {"plugin_id": req.get("plugin_id"), "config": {"runtime": {"effective": pname.strip()}}}
                else:
                    pid = str(req.get("plugin_id", "")).strip()
                    result = {"plugin_id": pid, "config": dict(self._configs.get(pid, {}))}
            elif req_type == "PLUGIN_CONFIG_UPDATE":
                pid = str(req.get("plugin_id", "")).strip()
                updates = req.get("updates")
                if not isinstance(updates, dict):
                    raise RuntimeError("updates must be dict")
                base = dict(self._configs.get(pid, {}))
                for k, v in updates.items():
                    if isinstance(base.get(k), dict) and isinstance(v, dict):
                        base[k] = {**base[k], **v}
                    else:
                        base[k] = v
                self._configs[pid] = base
                result = {"plugin_id": pid, "config": dict(base)}
            elif req_type == "BUS_SUBSCRIBE":
                bus = str(req.get("bus", "")).strip()
                if not bus:
                    raise RuntimeError("bus is required")
                self._sub_seq += 1
                sub_id = f"sub-{self._sub_seq}"
                self._subs[sub_id] = {"bus": bus, "from_plugin": from_plugin}
                result = {"sub_id": sub_id, "rev": 0}
            elif req_type == "BUS_UNSUBSCRIBE":
                sub_id = str(req.get("sub_id", "")).strip()
                self._subs.pop(sub_id, None)
                result = {"ok": True}
            else:
                raise RuntimeError(f"Unsupported request type: {req_type}")
        except Exception as e:
            error = {"code": "HOST_ERROR", "message": str(e)}

        response = {"request_id": rid, "result": result, "error": error}
        state.set_plugin_response(rid, response, timeout=timeout)
        response_queue = self._response_queues.get(from_plugin)
        if response_queue is not None:
            response_queue.put_nowait(response)

    def emit_bus_change(self, *, bus: str, op: str, delta: dict[str, Any]) -> None:
        for sid, info in list(self._subs.items()):
            if info.get("bus") != bus:
                continue
            dispatch_bus_change(sub_id=sid, bus=bus, op=op, delta=delta)


def _write_minimal_plugin_toml(path: Path, plugin_id: str) -> None:
    path.write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "test"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _build_contexts(tmp_path: Path) -> _ContextSet:
    req_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
    caller_res_q: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    host_res_q: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()

    caller_toml = tmp_path / "caller.toml"
    host_toml = tmp_path / "host.toml"
    _write_minimal_plugin_toml(caller_toml, "caller")
    _write_minimal_plugin_toml(host_toml, "host")

    caller_ctx = PluginContext(
        plugin_id="caller",
        config_path=caller_toml,
        logger=_Logger(),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
        _plugin_comm_queue=req_queue,
        _response_queue=caller_res_q,
    )
    host_ctx = PluginContext(
        plugin_id="host",
        config_path=host_toml,
        logger=_Logger(),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
        _plugin_comm_queue=req_queue,
        _response_queue=host_res_q,
    )
    return _ContextSet(caller=caller_ctx, host=host_ctx, req_queue=req_queue)


class _ProbeWatcher(BusListWatcherCore):
    def __init__(self, ctx: PluginContext, bus: str = "messages") -> None:
        import threading

        self._callbacks: list[tuple[Any, tuple[str, ...]]] = []
        self._lock = threading.Lock()
        self._unsub = None
        self._sub_id = None
        self._ctx = ctx
        self._bus = bus
        self._list = type("_List", (), {"trace_tree_dump": lambda self: {}})()
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def _watcher_set(self, sub_id: str) -> None:
        self._sub_id = sub_id
        from plugin.sdk.bus.rev import _watcher_set

        _watcher_set(sub_id, self)

    def _watcher_pop(self, sub_id: str) -> None:
        from plugin.sdk.bus.rev import _watcher_pop

        _watcher_pop(sub_id)
        if self._sub_id == sub_id:
            self._sub_id = None

    def _schedule_tick(self, op: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append((op, payload))

    def _on_remote_change(self, *, bus: str, op: str, delta: dict[str, Any]) -> None:
        _ = bus
        self._schedule_tick(op, delta)


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_plugins_call_entry_and_require(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    host_plugin = _HostPlugin(contexts.host)

    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"host": host_plugin, "caller": _HostPlugin(contexts.caller)},
    )
    await bridge.start()
    try:
        plugins = Plugins(ctx=contexts.caller)
        listed = await plugins.list_async()
        assert any(p.get("plugin_id") == "host" for p in listed.get("plugins", []))
        await plugins.require_async("host")

        out = await plugins.call_entry_async("host:sum", {"a": 1, "b": 2}, timeout=2.0)
        assert out["value"] == 4  # before hook modifies a=1->2
        assert out["after"] is True
        assert out["seen_a"] == 2
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_router_dynamic_entry_full_flow(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    host_plugin = _HostPlugin(contexts.host)
    router = PluginRouter(prefix="r_")
    host_plugin.include_router(router)

    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"host": host_plugin, "caller": _HostPlugin(contexts.caller)},
    )
    await bridge.start()
    try:
        async def _mul(**kwargs: Any):
            return {"value": int(kwargs.get("x", 0)) * int(kwargs.get("y", 0))}

        assert await router.add_entry("mul", _mul, name="mul") is True
        plugins = Plugins(ctx=contexts.caller)
        out = await plugins.call_entry_async("host:r_mul", {"x": 3, "y": 4}, timeout=2.0)
        assert out["value"] == 12

        assert await router.remove_entry("mul") is True
        with pytest.raises(RuntimeError):
            await plugins.call_entry_async("host:r_mul", {"x": 1, "y": 2}, timeout=1.0)
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_timeout_path(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    host_plugin = _HostPlugin(contexts.host)
    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"host": host_plugin, "caller": _HostPlugin(contexts.caller)},
    )
    await bridge.start()
    try:
        plugins = Plugins(ctx=contexts.caller)
        with pytest.raises(TimeoutError):
            await plugins.call_entry_async(
                "host:slow",
                {"sleep_s": 0.0, "__drop_response__": True},
                timeout=0.05,
            )
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_call_entry_cancellation(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    host_plugin = _HostPlugin(contexts.host)
    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"host": host_plugin, "caller": _HostPlugin(contexts.caller)},
    )
    await bridge.start()
    try:
        plugins = Plugins(ctx=contexts.caller)
        task = asyncio.create_task(
            plugins.call_entry_async("host:slow", {"sleep_s": 5.0}, timeout=20.0)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # cancelled request should not poison following calls
        out = await plugins.call_entry_async("host:sum", {"a": 1, "b": 1}, timeout=2.0)
        assert out["value"] == 3
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_plugin_config_over_comm_queue(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"caller": _HostPlugin(contexts.caller), "host": _HostPlugin(contexts.host)},
        configs={"caller": {"runtime": {"enabled": True, "level": 1}}},
    )
    await bridge.start()
    try:
        cfg = PluginConfig(contexts.caller)
        assert (await cfg.dump())["runtime"]["enabled"] is True
        assert await cfg.get("runtime.level") == 1
        await cfg.set("runtime.level", 2)
        assert await cfg.get("runtime.level") == 2
        await cfg.update({"runtime": {"tag": "x"}})
        assert (await cfg.get_section("runtime"))["tag"] == "x"
        assert (await cfg.dump_base())["base"] is True
        assert (await cfg.get_profiles_state())["config_profiles"]["active"] == "dev"
        assert (await cfg.get_profile("prod"))["runtime"]["profile"] == "prod"
        assert (await cfg.dump_effective("stage"))["runtime"]["effective"] == "stage"
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_real_context_bus_subscribe_and_dispatch(tmp_path: Path) -> None:
    contexts = _build_contexts(tmp_path)
    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"caller": _HostPlugin(contexts.caller), "host": _HostPlugin(contexts.host)},
    )
    await bridge.start()
    try:
        watcher = _ProbeWatcher(contexts.caller, bus="messages")
        await watcher.start_async()
        assert isinstance(watcher._sub_id, str) and watcher._sub_id

        bridge.emit_bus_change(bus="messages", op="add", delta={"message_id": "m1", "rev": 1})
        await asyncio.sleep(0.01)
        assert watcher.events and watcher.events[-1][0] == "add"
        assert watcher.events[-1][1]["message_id"] == "m1"

        sid = watcher._sub_id
        await watcher.stop_async()
        assert watcher._sub_id is None
        assert sid not in bridge._subs
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()

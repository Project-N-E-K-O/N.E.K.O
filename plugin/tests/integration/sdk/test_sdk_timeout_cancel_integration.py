from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import ormsgpack
import pytest

from plugin.sdk.bus.bus_list import BusListWatcherCore
from plugin.sdk.message_plane_transport import MessagePlaneRpcClient
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
        self.plugins: dict[str, Any] = {}

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
        handler = plugin.get(event_id)
        if handler is None:
            raise PluginCallError(f"Entry not found: {target_plugin_id}:{event_id}")
        out = handler(**params)
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


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_plugins_call_entry_async_timeout_and_cancellation(tmp_path: Path) -> None:
    runtime = _Runtime()
    ctx = _Ctx("caller", runtime, tmp_path / "caller.toml", _Logger(), _MsgQueue())

    async def _slow(**kwargs: Any):
        if kwargs.get("force_timeout") is True:
            raise TimeoutError("simulated timeout")
        await asyncio.sleep(float(kwargs.get("sleep", 0.0)))
        return {"ok": True}

    runtime.plugins["target"] = {"work": _slow}
    plugins = Plugins(ctx=ctx)

    with pytest.raises(TimeoutError):
        await plugins.call_entry_async("target:work", {"force_timeout": True}, timeout=0.01)

    task = asyncio.create_task(plugins.call_entry_async("target:work", {"sleep": 10.0}, timeout=20.0))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # cancellation should not poison subsequent calls
    ok_res = await plugins.call_entry_async("target:work", {"sleep": 0.0}, timeout=1.0)
    assert ok_res["ok"] is True


class _BlockingWatcher(BusListWatcherCore):
    def __init__(self, gate: threading.Event) -> None:
        self._callbacks = []
        self._lock = threading.Lock()
        self._unsub = None
        self._sub_id = None
        self._ctx = SimpleNamespace()
        self._bus = "messages"
        self._list = SimpleNamespace(trace_tree_dump=lambda: {})
        self._gate = gate
        self._stop_called = False

    def _watcher_set(self, sub_id: str) -> None:
        self._sub_id = sub_id

    def _watcher_pop(self, sub_id: str) -> None:
        if self._sub_id == sub_id:
            self._sub_id = None

    def _schedule_tick(self, op: str, payload: dict[str, Any] | None = None) -> None:
        _ = (op, payload)
        return None

    def start(self) -> Any:
        self._gate.wait(timeout=2.0)
        self._sub_id = "sid-blocking"
        return self

    def stop(self) -> None:
        self._stop_called = True
        self._sub_id = None


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_watcher_start_async_cancel_then_stop_async() -> None:
    gate = threading.Event()
    watcher = _BlockingWatcher(gate)

    task = asyncio.create_task(watcher.start_async())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # allow background thread to finish and then ensure stop still works
    gate.set()
    await asyncio.sleep(0.02)
    await watcher.stop_async()
    assert watcher._stop_called is True
    assert watcher._sub_id is None


class _FakeAsyncSock:
    def __init__(self) -> None:
        self._responses = [
            ormsgpack.packb({"v": 1, "req_id": "rid-1", "ok": True, "result": {"x": 1}}),
            ormsgpack.packb({"v": 1, "req_id": "not-requested", "ok": True, "result": {"x": 9}}),
            ormsgpack.packb({"v": 1, "req_id": "rid-3", "ok": False, "error": {"code": "E", "message": "bad"}}),
        ]

    async def send(self, raw: bytes, flags: int = 0, copy: bool = False, track: bool = False) -> None:
        _ = (raw, flags, copy, track)
        return None

    async def poll(self, timeout: int = 0, flags: int = 0) -> int:
        _ = (timeout, flags)
        return 1 if self._responses else 0

    async def recv(self, flags: int = 0, copy: bool = False) -> bytes:
        _ = (flags, copy)
        if not self._responses:
            await asyncio.sleep(0)
            return b""
        return self._responses.pop(0)


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_message_plane_batch_request_async_partial_aggregation(monkeypatch: pytest.MonkeyPatch) -> None:
    import plugin.sdk.message_plane_transport as transport_module

    if transport_module.zmq is None:
        pytest.skip("pyzmq unavailable")
    try:
        import zmq.asyncio  # noqa: F401
    except Exception:
        pytest.skip("zmq.asyncio unavailable")

    client = MessagePlaneRpcClient(plugin_id="p", endpoint="inproc://test")
    sock = _FakeAsyncSock()
    monkeypatch.setattr(client, "_get_async_sock", lambda: asyncio.sleep(0, result=sock))
    req_ids = ["rid-1", "rid-2", "rid-3"]
    monkeypatch.setattr(client, "_next_req_id", lambda: req_ids.pop(0))

    out = await client.batch_request_async(
        [{"op": "a", "args": {}}, {"op": "b", "args": {}}, {"op": "c", "args": {}}],
        timeout=0.05,
    )
    assert len(out) == 3
    assert isinstance(out[0], dict) and out[0]["req_id"] == "rid-1"
    assert out[1] is None  # never received
    assert isinstance(out[2], dict) and out[2]["req_id"] == "rid-3"

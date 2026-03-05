from __future__ import annotations

import asyncio
from queue import Empty

import pytest

import plugin.server.messaging.request_router as request_router_module


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_request_from_queue_none_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    router = request_router_module.PluginRouter()

    class _StateNone:
        plugin_comm_queue = None

    monkeypatch.setattr(request_router_module, "state", _StateNone())
    assert await router._get_request_from_queue() is None

    class _Q:
        def get(self, timeout: float = 0.1) -> object:
            _ = timeout
            raise Empty

    class _StateEmpty:
        plugin_comm_queue = _Q()

    monkeypatch.setattr(request_router_module, "state", _StateEmpty())
    assert await router._get_request_from_queue() is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_request_from_queue_normalize_and_handle_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    router = request_router_module.PluginRouter()

    class _Q:
        def get(self, timeout: float = 0.1) -> object:
            _ = timeout
            return {"type": "X", "from_plugin": "p1", "request_id": "r1", 1: "drop"}

    class _State:
        plugin_comm_queue = _Q()

    monkeypatch.setattr(request_router_module, "state", _State())
    req = await router._get_request_from_queue()
    assert isinstance(req, dict)
    assert 1 not in req

    called: dict[str, object] = {}

    async def _h(request: dict[str, object], send_response) -> None:  # type: ignore[no-untyped-def]
        called["request"] = request
        send_response("p1", "r1", {"ok": True}, None, timeout=1.0)

    router._handlers = {"X": _h}
    await router._handle_request(req or {})
    assert isinstance(called.get("request"), dict)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_and_stop_without_zmq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    monkeypatch.setattr(request_router_module, "PLUGIN_ZMQ_IPC_ENABLED", False)
    router = request_router_module.PluginRouter()

    async def _quick_loop() -> None:
        event = router._ensure_shutdown_event()
        while not event.is_set():
            await asyncio.sleep(0)

    monkeypatch.setattr(router, "_router_loop", _quick_loop)

    await router.start()
    assert router._router_task is not None
    await router.stop()
    assert router._router_task is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_with_zmq_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    monkeypatch.setattr(request_router_module, "PLUGIN_ZMQ_IPC_ENABLED", True)
    router = request_router_module.PluginRouter()

    async def _quick_loop() -> None:
        event = router._ensure_shutdown_event()
        while not event.is_set():
            await asyncio.sleep(0)

    monkeypatch.setattr(router, "_router_loop", _quick_loop)

    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):  # type: ignore[no-untyped-def]
        if name == "plugin.utils.zeromq_ipc":
            raise ImportError("missing zmq")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    await router.start()
    # import failure should not crash start
    assert router._router_task is not None
    assert router._zmq_task is None
    await router.stop()

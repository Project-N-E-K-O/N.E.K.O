from __future__ import annotations

import pytest

import plugin.server.messaging.request_router as request_router_module


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_handle_zmq_request_validation_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    router = request_router_module.PluginRouter()

    missing_from = await router._handle_zmq_request({"type": "X", "request_id": "r1"})
    assert missing_from["error"] == "missing from_plugin"

    missing_req_id = await router._handle_zmq_request({"type": "X", "from_plugin": "p1"})
    assert missing_req_id["error"] == "missing request_id"

    unknown = await router._handle_zmq_request({"type": "UNKNOWN", "from_plugin": "p1", "request_id": "r1"})
    assert str(unknown["error"]).startswith("unknown request type")


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_handle_zmq_request_success_error_and_no_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    router = request_router_module.PluginRouter()

    async def _ok_handler(request: dict[str, object], send_response) -> None:  # type: ignore[no-untyped-def]
        send_response(request["from_plugin"], request["request_id"], {"ok": True}, None, timeout=1.0)

    async def _fail_handler(_: dict[str, object], __) -> None:  # type: ignore[no-untyped-def]
        raise KeyError("boom")

    async def _no_response_handler(_: dict[str, object], __) -> None:  # type: ignore[no-untyped-def]
        return

    router._handlers = {"OK": _ok_handler, "FAIL": _fail_handler, "NONE": _no_response_handler}

    ok = await router._handle_zmq_request({"type": "OK", "from_plugin": "p1", "request_id": "r1"})
    assert ok["result"] == {"ok": True}
    assert ok["error"] is None

    fail = await router._handle_zmq_request({"type": "FAIL", "from_plugin": "p1", "request_id": "r2"})
    assert fail["result"] is None
    assert "boom" in str(fail["error"])

    none = await router._handle_zmq_request({"type": "NONE", "from_plugin": "p1", "request_id": "r3"})
    assert none["result"] is None
    assert none["error"] == "no response"


@pytest.mark.plugin_unit
def test_send_response_prefers_queue_then_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_router_module, "build_request_handlers", lambda: {})
    router = request_router_module.PluginRouter()

    class _Queue:
        def __init__(self) -> None:
            self.items: list[dict[str, object]] = []

        def put(self, item: dict[str, object], block: bool = True, timeout: float | None = None) -> None:
            _ = block, timeout
            self.items.append(item)

    q = _Queue()
    monkeypatch.setattr(request_router_module.state, "get_plugin_response_queue", lambda _: q, raising=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        request_router_module.state,
        "set_plugin_response",
        lambda request_id, response, timeout=10.0: captured.update(  # type: ignore[no-untyped-def]
            {"request_id": request_id, "response": response, "timeout": timeout}
        ),
    )

    router._send_response("p1", "r1", {"x": 1}, None, timeout=2.0)
    assert len(q.items) == 1
    assert captured == {}

    monkeypatch.setattr(request_router_module.state, "get_plugin_response_queue", lambda _: None, raising=False)
    router._send_response("p1", "r2", {"x": 2}, "err", timeout=3.0)
    assert captured["request_id"] == "r2"
    assert captured["timeout"] == 3.0



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

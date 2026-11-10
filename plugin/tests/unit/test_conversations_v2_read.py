from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from plugin.core.bus.conversations import ConversationV2Client
from plugin.message_plane.protocol import ConversationGetArgs, RpcEnvelope
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.stores import build_default_store_registry
from plugin.sdk.shared.core.bus_context import SdkBusContext


def test_plane_filters_before_window_and_reads_stable_id():
    stores = build_default_store_registry(maxlen=100, frames_maxlen=2)
    store = stores.get("conversations")
    for n, character, kind, voice in [(1, "YUI", "user_message", False), (2, "OTHER", "user_message", False), (3, "YUI", "user_message", True), (4, "YUI", "assistant_message", None), (5, "OTHER", "user_message", True)]:
        store.publish("all", {"id": f"m{n}", "timestamp": float(n), "content": str(n), "occurred_at": 10.0 if n == 3 else None, "metadata": {"lanlan_name": character, "turn_type": kind, "is_voice": voice, "ts": 99.0}})
    server = object.__new__(MessagePlaneRpcServer)
    server._stores = stores
    def call(op, args):
        return server._handle({"v": 1, "req_id": "r", "op": op, "args": args})
    items = call("bus.conversations.get", {"character": "YUI", "role": "user", "max_count": 2, "since_ts": 0.0})["result"]["items"]
    assert [x["id"] for x in items] == ["m1", "m3"]
    assert [x["input_source"] for x in items] == ["text", "voice"]
    assert items[0]["occurred_at"] is None and items[1]["occurred_at"] == 10.0
    assert [x["id"] for x in call("bus.conversations.get", {"character": "YUI", "max_count": 2, "order": "desc"})["result"]["items"]] == ["m4", "m3"]
    assert call("bus.conversations.get_message", {"message_id": "m3"})["result"] == {k: v for k, v in items[1].items() if k != "_revision"}
    assert call("bus.conversations.get_message", {"message_id": "missing"})["result"] is None
    assert not call("bus.conversations.get", {"store": "events"})["ok"]
    assert not call("bus.conversations.get", {"max_count": 0})["ok"]
    assert RpcEnvelope.model_validate({"v": 1, "op": "bus.conversations.get", "req_id": "r", "args": {}}).v == 1
    with pytest.raises(ValidationError):
        ConversationGetArgs(since_ts=float("nan"))


def test_sdk_and_core_use_sync_transport_and_decode():
    class Rpc:
        def request_sync(self, *, op, args, timeout):
            assert timeout > 0
            item = {"id": "id", "character": "YUI", "kind": "user_message", "role": "user", "text": "hi", "input_id": None, "reply_id": None, "input_source": "text", "origin": None, "observed_at": 1.0, "occurred_at": None}
            return {"ok": True, "result": {"items": [item]} if op.endswith(".get") else item}
    async def run():
        core = ConversationV2Client(SimpleNamespace(_mp_rpc_client=Rpc()))
        sdk = SdkBusContext(SimpleNamespace(conversations_v2=core), host_ctx=object()).conversations_v2
        result = await sdk.get()
        assert result[0].id == "id" and result[0].key() == "id"
        assert (await sdk.get_message("id")).text == "hi"
        with pytest.raises(RuntimeError, match="not available"):
            await SdkBusContext(object(), host_ctx=object()).conversations_v2.get()
    asyncio.run(run())


def test_response_extensions_do_not_break_existing_message_decoder():
    from plugin.sdk.shared.contracts.conversations import ConversationMessage

    value = {
        "id": "m", "character": "YUI", "kind": "future_kind", "role": "unknown",
        "text": "visible", "input_id": None, "reply_id": None,
        "input_source": "unknown", "observed_at": 1.0, "occurred_at": None,
        "origin": {"kind": "future_source", "is_proactive": None, "plugin_id": None,
                   "task_id": None, "additional_evidence": "new host field"},
        "additional_metadata": "new host field",
    }
    decoded = ConversationMessage.from_payload(value)
    assert decoded.kind == "future_kind" and decoded.origin.kind == "future_source"
    assert "additional_metadata" not in decoded.dump()
    with pytest.raises(ValueError):
        ConversationMessage.from_payload({k: v for k, v in value.items() if k != "id"})


def test_new_capability_is_not_resolved_when_old_bus_is_wrapped():
    class Bus:
        resolutions = 0

        @property
        def conversations_v2(self):
            self.resolutions += 1
            return object()

    raw = Bus()
    wrapped = SdkBusContext(raw, host_ctx=object())
    assert raw.resolutions == 0
    first = wrapped.conversations_v2
    second = wrapped.conversations_v2
    assert first is second
    assert raw.resolutions == 1


@pytest.mark.parametrize("invalid", [{"metadata": {"is_voice": "yes"}}, {"origin": {"kind": ""}}])
def test_bad_legacy_record_does_not_poison_structured_reads(invalid):
    stores = build_default_store_registry(maxlen=100, frames_maxlen=2)
    store = stores.get("conversations")
    for ident, fields in [("valid", {}), ("invalid", invalid)]:
        store.publish("all", {"id": ident, "timestamp": 1.0, "content": ident,
                              **fields, "metadata": {"lanlan_name": "YUI", "turn_type": "user_message", **fields.get("metadata", {})}})
    items, _ = store.conversation_messages(filters={}, limit=50, order="asc")
    assert [item["id"] for item in items] == ["valid"]
    assert store.conversation_message("valid")["text"] == "valid"
    assert store.conversation_message("invalid") is None


def test_new_read_size_limit_is_explicit_without_restricting_legacy_reads(monkeypatch):
    import plugin.message_plane.rpc_server as rpc_module

    stores = build_default_store_registry(maxlen=10, frames_maxlen=2)
    stores.get("conversations").publish("all", {
        "id": "large", "timestamp": 1.0, "content": "x" * 1000,
        "metadata": {"turn_type": "assistant_message", "lanlan_name": "YUI"},
    })
    server = object.__new__(MessagePlaneRpcServer)
    server._stores = stores
    monkeypatch.setattr(rpc_module, "_CONVERSATION_READ_MAX_BYTES", 512)
    for op, args in (
        ("bus.conversations.get", {}),
        ("bus.conversations.get_message", {"message_id": "large"}),
    ):
        response = server._handle({"v": 1, "req_id": "r", "op": op, "args": args})
        assert response["ok"] is False
        assert response["error"]["code"] == "RESPONSE_TOO_LARGE"
    response = server._handle({"v": 1, "req_id": "old", "op": "bus.get_recent",
                               "args": {"store": "conversations", "topic": "all", "limit": 10}})
    assert response["ok"] is True
    assert response["result"]["items"][0]["payload"]["content"] == "x" * 1000


def test_parallel_executor_sockets_have_unique_identity_even_with_same_clock(monkeypatch):
    import concurrent.futures
    import threading
    import zmq
    from plugin.core import message_plane_transport as transport

    monkeypatch.setattr(transport, "time", SimpleNamespace(time=lambda: 1000.0))
    client = transport.MessagePlaneRpcClient(plugin_id="same-plugin", endpoint="inproc://identity-test")
    ready = threading.Barrier(8)

    def connect():
        ready.wait(timeout=3)
        sock = client._get_sock()
        try:
            return sock.getsockopt(zmq.IDENTITY)
        finally:
            sock.close(linger=0)
            client._tls.ctx.term()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        identities = list(executor.map(lambda _: connect(), range(8)))
    assert len(set(identities)) == 8


def test_closing_context_only_invalidates_its_own_watcher(tmp_path):
    import logging
    from plugin.core.context import PluginContext
    from plugin.core.bus.rev import _watcher_get

    class Sink:
        closed = 0

        def _on_host_closed(self):
            self.closed += 1

    first = PluginContext("first", tmp_path / "first.toml", logging.getLogger(__name__), None)
    second = PluginContext("second", tmp_path / "second.toml", logging.getLogger(__name__), None)
    a, b = Sink(), Sink()
    try:
        first.bus.conversations_v2.register_watcher("first-owned", a)
        second.bus.conversations_v2.register_watcher("second-owned", b)
        first.close()
        assert a.closed == 1 and b.closed == 0
        assert _watcher_get("first-owned") is None
        assert _watcher_get("second-owned") is b
        second.close()
        assert b.closed == 1 and _watcher_get("second-owned") is None
    finally:
        first.close()
        second.close()


def test_deadline_includes_executor_queue():
    import concurrent.futures
    import threading
    import time

    entered = threading.Event()
    release = threading.Event()
    calls = []
    class Rpc:
        def request_sync(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "result": {"items": []}}
    async def run():
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        def block():
            entered.set()
            release.wait(2)
        blocker = loop.run_in_executor(None, block)
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError):
                await ConversationV2Client(SimpleNamespace(_mp_rpc_client=Rpc())).get(timeout=0.03)
            release.set()
            await blocker
            await asyncio.sleep(0.05)
            assert calls == []
        finally:
            release.set()
            executor.shutdown(wait=True)
    asyncio.run(run())


def test_slow_message_plane_reads_leave_executor_room_for_control_requests():
    import concurrent.futures
    import queue
    import threading

    from plugin.core.context import PluginContext
    from plugin.sdk.shared.models.exceptions import TransportError

    async def run():
        loop = asyncio.get_running_loop()
        # Python 3.11's standard default executor has at least five workers.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        loop.set_default_executor(executor)
        release = threading.Event()
        entered = threading.Event()
        count_lock = threading.Lock()
        count = 0

        class SlowRpc:
            def request_sync(self, **kwargs):
                nonlocal count
                with count_lock:
                    count += 1
                    if count >= 4:
                        entered.set()
                release.wait(3)
                return {"ok": True, "result": None}

        client = ConversationV2Client(SimpleNamespace(_mp_rpc_client=SlowRpc()))
        ctx = SimpleNamespace(plugin_id="legacy", _plugin_comm_queue=queue.Queue())
        reads = [asyncio.create_task(client._request("probe", {}, 3)) for _ in range(5)]
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.01)
            await asyncio.wait_for(
                PluginContext._send_rpc_request(ctx, {"type": "CONTROL"}, timeout=0.5),
                timeout=0.5,
            )
            assert ctx._plugin_comm_queue.get_nowait() == {
                "type": "CONTROL", "from_plugin": "legacy",
            }
        finally:
            release.set()
            results = await asyncio.gather(*reads, return_exceptions=True)
            executor.shutdown(wait=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        assert len(errors) == 1
        assert isinstance(errors[0], TransportError) and errors[0].code == "BUSY"

    asyncio.run(run())

import asyncio
import pytest

from plugin.sdk.shared.conversations.client import SdkConversationsV2Bus
from plugin.sdk.shared.conversations import watcher as watcher_module


def message(id, text="x"):
    return {
        "id": id,
        "character": "YUI",
        "kind": "user_message",
        "role": "user",
        "text": text,
        "input_id": None,
        "reply_id": None,
        "input_source": "voice",
        "origin": None,
        "observed_at": 1.0,
        "occurred_at": None,
        "_revision": int(id) + 1
        if id.isdigit()
        else {"a": 1, "b": 3, "c": 4}.get(id, 1),
    }


class Host:
    def __init__(self):
        self.requests = []

    async def _send_request_and_wait_async(self, **kwargs):
        self.requests.append(kwargs)
        return {"sub_id": "sub-1"} if kwargs["request_type"] == "BUS_SUBSCRIBE" else {}


class Adapter:
    def __init__(self):
        self.sink = None
        self.rows = []
        self.gets = 0
        self.ready = None

    def register_watcher(self, sub_id, sink):
        self.sink = sink

    def unregister_watcher(self, sub_id):
        self.sink = None

    def activate_watcher(self, sink):
        if self.ready:
            self.ready.call_soon(sink._on_receiver_ready)

    async def get_snapshot(self, **kwargs):
        self.gets += 1
        return {
            "items": self.rows.copy(),
            "snapshot_seq": 10,
            "effective_limit": kwargs["max_count"],
        }

    def send(self, op, delta=None):
        self.sink._on_remote_change(bus="conversations", op=op, delta=delta or {})


@pytest.mark.asyncio
async def test_event_only_no_history_and_stop():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch(
        role="user", input_source="voice"
    )
    events = []
    watch.subscribe(on="record")(events.append)
    await watch.start()
    assert host.requests[0]["request_data"] == {
        "bus": "conversations",
        "contract": "conversations_v2",
        "deliver": "delta",
        "rules": ["record"],
        "filters": {"role": "user", "input_source": "voice"},
        "view": False,
    }
    adapter.send("record", message("1"))
    await asyncio.sleep(0)
    assert [m.id for m in events] == ["1"] and adapter.gets == 0
    sink = adapter.sink
    await watch.stop()
    sink._on_remote_change(bus="conversations", op="record", delta=message("2"))
    await asyncio.sleep(0)
    assert len(events) == 1 and adapter.sink is None


@pytest.mark.asyncio
async def test_initial_merge_and_invalidation():
    host, adapter = Host(), Adapter()
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed_get(**kwargs):
        adapter.gets += 1
        started.set()
        await finish.wait()
        return {
            "items": [message("a", "old")],
            "snapshot_seq": 1,
            "effective_limit": kwargs["max_count"],
        }

    adapter.get_snapshot = delayed_get
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch(view_limit=2)
    deltas, events = [], []
    watch.subscribe(on="change")(deltas.append)
    watch.subscribe(on="record")(events.append)
    await watch.start()
    watch._on_receiver_ready()
    await started.wait()
    updated = message("a", "new")
    updated["_revision"] = 2
    adapter.send("message.updated", updated)
    adapter.send("record", message("b"))
    await asyncio.sleep(0)
    assert [m.id for m in events] == ["b"]
    finish.set()
    await asyncio.sleep(0.01)
    assert deltas[0].reason == "initial"
    assert [(m.id, m.text) for m in deltas[0].current] == [("a", "new"), ("b", "x")]
    adapter.rows = [message("b"), message("c")]

    async def fresh(**kwargs):
        adapter.gets += 1
        return {
            "items": adapter.rows.copy(),
            "snapshot_seq": 4,
            "effective_limit": kwargs["max_count"],
        }

    adapter.get_snapshot = fresh
    adapter.send("view.invalidated")
    await asyncio.sleep(0.01)
    assert [m.id for m in deltas[-1].current] == ["b", "c"]
    assert [m.id for m in deltas[-1].removed] == ["a"]
    assert [m.id for m in events] == ["b"]
    await watch.stop()


@pytest.mark.asyncio
async def test_callbacks_serial_and_independent_with_bounded_queue(monkeypatch):
    monkeypatch.setattr(watcher_module, "_MAX_PENDING", 3)
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    release, entered = asyncio.Event(), asyncio.Event()
    seen, fast = [], []

    async def slow(m):
        seen.append(m.id)
        entered.set()
        await release.wait()

    watch.subscribe(on="record")(slow)
    watch.subscribe(on="record")(lambda m: fast.append(m.id))
    await watch.start()
    adapter.send("record", message("0"))
    await entered.wait()
    for i in range(1, 8):
        adapter.send("record", message(str(i)))
        await asyncio.sleep(0)
    assert fast and seen == ["0"] and watch.dropped > 0
    release.set()
    await asyncio.sleep(0.02)
    assert seen[-1] == "7" and len(seen) <= 4
    await watch.stop()


@pytest.mark.asyncio
async def test_stop_cancels_and_invalid_filter():
    host, adapter = Host(), Adapter()
    with pytest.raises(ValueError):
        SdkConversationsV2Bus(adapter, host_ctx=host).watch(
            role="assistant", input_source="voice"
        ).subscribe(on="record")
    with pytest.raises(ValueError):
        SdkConversationsV2Bus(adapter, host_ctx=host).watch(view_limit=0)
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(m):
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    watch.subscribe(on="record")(slow)
    await watch.start()
    adapter.send("record", message("1"))
    await entered.wait()
    await watch.stop()
    await asyncio.sleep(0)
    assert cancelled.is_set()


def test_startup_loop_does_not_bind_initial_view():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    deltas = []
    watch.subscribe(on="change")(deltas.append)
    asyncio.run(watch.start())
    assert adapter.gets == 0

    async def run():
        watch._on_receiver_ready()
        await asyncio.sleep(0.01)
        assert (
            len(deltas) == 1 and deltas[0].reason == "initial" and not deltas[0].current
        )
        await watch.stop()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_input_stage_filter_and_no_history():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch(
        role="user", input_source="text"
    )
    events = []
    watch.subscribe(on="input.submitted")(events.append)
    await watch.start()
    adapter.send(
        "input.submitted",
        {
            "submission": {
                "id": "i",
                "character": "YUI",
                "text": "hi",
                "input_source": "text",
                "observed_at": 1.0,
            }
        },
    )
    await asyncio.sleep(0)
    assert events[0].submission.id == "i" and adapter.gets == 0
    await watch.stop()
    with pytest.raises(ValueError):
        SdkConversationsV2Bus(adapter, host_ctx=host).watch(
            kind="user_message"
        ).subscribe(on="input.submitted")


@pytest.mark.asyncio
async def test_failed_refresh_waits_for_new_signal():
    host, adapter = Host(), Adapter()

    async def failed(**kwargs):
        adapter.gets += 1
        raise RuntimeError("offline")

    adapter.get_snapshot = failed
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    watch.subscribe(on="change")(lambda x: None)
    await watch.start()
    watch._on_receiver_ready()
    await asyncio.sleep(0.02)
    assert adapter.gets == 1
    adapter.send("view.invalidated")
    await asyncio.sleep(0.02)
    assert adapter.gets == 2
    await watch.stop()


@pytest.mark.asyncio
async def test_snapshot_watermark_prevents_older_update_overwriting_newer():
    host, adapter = Host(), Adapter()
    started, release = asyncio.Event(), asyncio.Event()

    async def get_snapshot(**kwargs):
        started.set()
        await release.wait()
        newest = message("a", "newest")
        newest["_revision"] = 3
        return {
            "items": [newest],
            "snapshot_seq": 3,
            "effective_limit": kwargs["max_count"],
        }

    adapter.get_snapshot = get_snapshot
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    seen = []
    watch.subscribe(on="change")(seen.append)
    await watch.start()
    watch._on_receiver_ready()
    await started.wait()
    old = message("a", "stale")
    old["_revision"] = 2
    adapter.send("message.updated", old)
    release.set()
    await asyncio.sleep(0.01)
    assert seen[0].current[0].text == "newest"
    adapter.send("message.updated", old)
    await asyncio.sleep(0)
    assert len(seen) == 1
    await watch.stop()


@pytest.mark.asyncio
async def test_reply_and_playback_callbacks():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch(character="YUI")
    seen = []
    watch.subscribe(on="reply.started")(seen.append)
    watch.subscribe(on="playback.stopped")(seen.append)
    await watch.start()
    adapter.send(
        "reply.started",
        {
            "reply": {
                "id": "r",
                "character": "YUI",
                "input_id": None,
                "origin": None,
                "text": "hello",
                "text_state": "started",
                "close_reason": None,
                "audio_expected": None,
                "started_at": 1.0,
                "closed_at": None,
                "playbacks": [],
            }
        },
    )
    adapter.send(
        "playback.stopped",
        {
            "reply_id": "r",
            "turn_id": None,
            "presentation_id": None,
            "presenter_id": None,
            "state": "stopped",
            "stop_reason": "unknown",
        },
    )
    await asyncio.sleep(0)
    assert seen[0].reply.id == "r" and seen[1].stop_reason == "unknown"
    await watch.stop()
    with pytest.raises(ValueError):
        SdkConversationsV2Bus(adapter, host_ctx=host).watch(role="user").subscribe(
            on="reply.closed"
        )
    with pytest.raises(ValueError):
        SdkConversationsV2Bus(adapter, host_ctx=host).watch(
            input_source="voice"
        ).subscribe(on="playback.stopped")


@pytest.mark.asyncio
async def test_stop_called_from_callback_is_safe():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    stopped = asyncio.Event()

    @watch.subscribe(on="record")
    async def stop_self(message):
        await watch.stop()
        stopped.set()

    await watch.start()
    adapter.send("record", message("1"))
    await asyncio.wait_for(stopped.wait(), 1)
    assert adapter.sink is None


@pytest.mark.asyncio
async def test_known_subscription_closed_invalidates_sink():
    host, adapter = Host(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    received = []
    watch.subscribe(on="record")(received.append)
    await watch.start()
    sink = adapter.sink
    adapter.send("subscription.closed")
    sink._on_remote_change(bus="conversations", op="record", delta=message("1"))
    await asyncio.sleep(0)
    assert not received and not watch._active and adapter.sink is None


@pytest.mark.asyncio
async def test_initial_merge_buffer_has_byte_limit_and_marks_refresh(monkeypatch):
    monkeypatch.setattr(watcher_module, "_MAX_BUFFER_BYTES", 700)
    host, adapter = Host(), Adapter()
    begun, release = asyncio.Event(), asyncio.Event()

    async def snapshot(**kwargs):
        adapter.gets += 1
        begun.set()
        await release.wait()
        if adapter.gets == 1:
            return {
                "items": [],
                "snapshot_seq": 0,
                "effective_limit": kwargs["max_count"],
            }
        return {
            "items": [message(str(i), "x" * 300) for i in range(8)],
            "snapshot_seq": 8,
            "effective_limit": kwargs["max_count"],
        }

    adapter.get_snapshot = snapshot
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    changes = []
    watch.subscribe(on="change")(changes.append)
    await watch.start()
    watch._on_receiver_ready()
    await begun.wait()
    for i in range(8):
        adapter.send("record", message(str(i), "x" * 300))
    assert watch._buffer_bytes <= 700
    assert watch._buffer_overflow
    release.set()
    async with asyncio.timeout(1):
        while not changes or len(changes[-1].current) != 8:
            await asyncio.sleep(0.01)
    assert adapter.gets == 2 and not watch._refresh_needed
    assert [m.id for m in changes[-1].current] == [str(i) for i in range(8)]
    await watch.stop()


@pytest.mark.asyncio
async def test_stop_while_starting_cleans_late_subscription():
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedHost(Host):
        async def _send_request_and_wait_async(self, **kwargs):
            self.requests.append(kwargs)
            if kwargs["request_type"] == "BUS_SUBSCRIBE":
                entered.set()
                await release.wait()
                return {"sub_id": "late"}
            return {}

    host, adapter = DelayedHost(), Adapter()
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    watch.subscribe(on="record")(lambda _: None)
    delayed_decorator = watch.subscribe(on="change")
    start = asyncio.create_task(watch.start())
    await entered.wait()
    with pytest.raises(RuntimeError):
        watch.subscribe(on="change")(lambda _: None)
    with pytest.raises(RuntimeError):
        delayed_decorator(lambda _: None)
    with pytest.raises(Exception) as concurrent:
        await watch.start()
    assert concurrent.value.code == "CONVERSATIONS_NOT_READY"
    await watch.stop()
    release.set()
    with pytest.raises(Exception) as late:
        await start
    assert late.value.code == "CONVERSATIONS_NOT_READY"
    assert adapter.sink is None and not watch._active
    assert [item["request_type"] for item in host.requests] == [
        "BUS_SUBSCRIBE",
        "BUS_UNSUBSCRIBE",
    ]


@pytest.mark.asyncio
async def test_repeated_overflow_does_not_poll_forever(monkeypatch):
    monkeypatch.setattr(watcher_module, "_MAX_BUFFER_BYTES", 700)
    host, adapter = Host(), Adapter()
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]

    async def snapshot(**kwargs):
        index = adapter.gets
        adapter.gets += 1
        started[index].set()
        await release[index].wait()
        return {"items": [], "snapshot_seq": 0, "effective_limit": kwargs["max_count"]}

    adapter.get_snapshot = snapshot
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    watch.subscribe(on="change")(lambda _: None)
    await watch.start()
    watch._on_receiver_ready()
    for batch in range(2):
        await started[batch].wait()
        for i in range(8):
            adapter.send("record", message(str(batch * 8 + i), "x" * 300))
        release[batch].set()
    await asyncio.sleep(0.03)
    assert adapter.gets == 2 and watch._refresh_needed
    await watch.stop()


@pytest.mark.asyncio
async def test_five_watchers_recover_from_core_capacity_on_success(monkeypatch):
    import threading
    from types import SimpleNamespace
    from plugin.core.bus import conversations as core
    from plugin.core.bus import rev

    gate = threading.Event()
    lock = threading.Lock()
    calls = []

    class BlockingRpc:
        def request_sync(self, **kwargs):
            with lock:
                calls.append(kwargs)
            if not gate.wait(3):
                raise TimeoutError("test gate timed out")
            return {
                "ok": True,
                "result": {"items": [], "snapshot_seq": 0, "effective_limit": 50},
            }

    class UniqueHost(Host):
        async def _send_request_and_wait_async(self, **kwargs):
            self.requests.append(kwargs)
            return (
                {"sub_id": f"capacity-{len(self.requests)}"}
                if kwargs["request_type"] == "BUS_SUBSCRIBE"
                else {}
            )

    host = UniqueHost()
    raw = core.ConversationV2Client(
        SimpleNamespace(
            _mp_rpc_client=BlockingRpc(),
            _register_conversation_watcher=rev._watcher_set,
            _unregister_conversation_watcher=rev._watcher_pop,
        )
    )
    bus = SdkConversationsV2Bus(raw, host_ctx=host)
    watchers, received = [], []
    monkeypatch.setattr(rev, "_RECEIVER_LOOP", None)
    monkeypatch.setattr(rev, "_READ_CAPACITY_NOTIFY_PENDING", False)
    rev.notify_receiver_ready()
    try:
        for index in range(5):
            watcher = bus.watch(character=f"probe-{index}")
            watcher.subscribe(on="change")(
                lambda value, i=index: received.append((i, value.reason))
            )
            await watcher.start()
            watchers.append(watcher)
        async with asyncio.timeout(2):
            while len(calls) < 4 or not any(w._capacity_blocked for w in watchers):
                await asyncio.sleep(0.01)
        assert len(calls) == 4 and received == []
        gate.set()
        async with asyncio.timeout(2):
            while len(received) != 5:
                await asyncio.sleep(0.01)
        assert sorted(received) == [(i, "initial") for i in range(5)]
        await asyncio.sleep(0.1)
        assert len(calls) == 5
    finally:
        gate.set()
        for watcher in watchers:
            await watcher.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["protocol", "timeout"])
async def test_capacity_signal_does_not_retry_unrelated_failure(failure):
    host, adapter = Host(), Adapter()

    async def fail(**kwargs):
        adapter.gets += 1
        if failure == "timeout":
            raise TimeoutError("offline")
        return {"items": "malformed"}

    adapter.get_snapshot = fail
    watch = SdkConversationsV2Bus(adapter, host_ctx=host).watch()
    watch.subscribe(on="change")(lambda _: None)
    await watch.start()
    watch._on_receiver_ready()
    async with asyncio.timeout(1):
        while watch._refresh_task is not None:
            await asyncio.sleep(0)
    assert watch._refresh_needed and not watch._capacity_blocked
    watch._on_read_capacity_available()
    await asyncio.sleep(0.03)
    assert adapter.gets == 1
    await watch.stop()


@pytest.mark.asyncio
async def test_core_success_with_malformed_snapshot_does_not_self_retry(monkeypatch):
    from types import SimpleNamespace
    from plugin.core.bus import conversations as core
    from plugin.core.bus import rev

    calls = []

    class MalformedRpc:
        def request_sync(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "result": {"items": "malformed"}}

    raw = core.ConversationV2Client(
        SimpleNamespace(
            _mp_rpc_client=MalformedRpc(),
            _register_conversation_watcher=rev._watcher_set,
            _unregister_conversation_watcher=rev._watcher_pop,
        )
    )
    host = Host()
    watcher = SdkConversationsV2Bus(raw, host_ctx=host).watch()
    watcher.subscribe(on="change")(lambda _: None)
    monkeypatch.setattr(rev, "_RECEIVER_LOOP", None)
    monkeypatch.setattr(rev, "_READ_CAPACITY_NOTIFY_PENDING", False)
    rev.notify_receiver_ready()
    try:
        await watcher.start()
        async with asyncio.timeout(2):
            while not calls or watcher._refresh_task is not None:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        assert (
            len(calls) == 1
            and watcher._refresh_needed
            and not watcher._capacity_blocked
        )
    finally:
        await watcher.stop()

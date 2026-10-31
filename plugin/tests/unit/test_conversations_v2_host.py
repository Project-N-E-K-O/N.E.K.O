from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from plugin.message_plane.stores import TopicStore
from plugin.server.application.bus.subscription_service import BusSubscriptionService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging import bus_subscriptions as bus_module
from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager


class FakeState:
    def __init__(self):
        self.subs = {}
        self.hosts = {}
        self.bus_change_hub = SimpleNamespace(subscribe=lambda *_: lambda: None)

    def add_bus_subscription(self, bus, sid, info):
        self.subs[sid] = info

    def remove_bus_subscription(self, bus, sid):
        return self.subs.pop(sid, None) is not None

    def get_bus_rev(self, bus):
        return 0

    def get_bus_subscriptions(self, bus):
        return dict(self.subs) if bus == "conversations" else {}

    def get_plugin_hosts_snapshot_cached(self, timeout):
        return self.hosts


def record(id, character="YUI", text="hi"):
    store = TopicStore("conversations", 10)
    event, revision = store.publish_with_revision("all", {
        "id": id, "timestamp": 1.0, "content": text,
        "metadata": {"turn_type": "user_message", "lanlan_name": character, "is_voice": False},
    })
    return {**event, "_revision": revision}


def test_register_filters_and_reject_inapplicable(monkeypatch):
    from plugin.server.application.bus import subscription_service as module
    state = FakeState()
    monkeypatch.setattr(module, "state", state)
    service = BusSubscriptionService()
    kwargs = dict(from_plugin="p", bus="conversations", deliver="delta", debounce_ms=0, timeout=1,
                  contract="conversations_v2", filters={"role": "user"}, view=True)
    result = service.subscribe(rules=["record", "message.updated"], **kwargs)
    assert state.subs[result["sub_id"]]["filters"] == {"role": "user"}
    with pytest.raises(ServerDomainError):
        service.subscribe(rules=["playback.stopped"], **kwargs)
    with pytest.raises(ServerDomainError):
        service.subscribe(rules=["record"], **{**kwargs, "filters": {"bad": 1}})


@pytest.mark.asyncio
async def test_threaded_ingress_filters_and_slow_plugin_isolation(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    slow_started = asyncio.Event()
    release = asyncio.Event()
    delivered = []
    class Host:
        def __init__(self, slow):
            self.slow = slow
        async def push_bus_change(self, **kwargs):
            if self.slow:
                slow_started.set()
                await release.wait()
            delivered.append(kwargs)
    state.hosts = {"slow": Host(True), "fast": Host(False)}
    for plugin in state.hosts:
        state.subs[plugin] = {"from_plugin": plugin, "contract": "conversations_v2", "rules": ["record"], "filters": {"character": "YUI"}, "view": True}
    manager = BusSubscriptionManager()
    await manager.start()
    try:
        thread = threading.Thread(target=lambda: (manager.accept_conversation_record(record("a")), manager.accept_conversation_record(record("b", "OTHER"))))
        thread.start()
        thread.join()
        await asyncio.wait_for(slow_started.wait(), 1)
        for _ in range(30):
            if delivered:
                break
            await asyncio.sleep(0.01)
        assert [d["delta"]["id"] for d in delivered] == ["a"]
        assert delivered[0]["sub_id"] == "fast"
    finally:
        release.set()
        await manager.stop()
    assert not manager._conversation_workers


@pytest.mark.asyncio
async def test_correction_preserves_sequence_and_invalidates_view(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    changes = []
    class Host:
        async def push_bus_change(self, **kwargs):
            changes.append(kwargs)
    state.hosts = {"p": Host()}
    state.subs = {"sub": {"from_plugin": "p", "contract": "conversations_v2", "rules": ["message.updated"], "filters": {"character": "YUI"}, "view": True}}
    store = TopicStore("conversations", 10)
    original = store.publish("all", record("m")["payload"])
    replacement = dict(original["payload"], content="new")
    updated = store.update_conversation("m", replacement)
    assert updated["seq"] == original["seq"]
    assert updated["index"]["timestamp"] == original["index"]["timestamp"]
    assert store.conversation_message("m")["text"] == "new"
    manager = BusSubscriptionManager()
    await manager.start()
    try:
        manager.accept_conversation_record({**updated, "conversation_op": "message.updated"})
        for _ in range(30):
            if len(changes) == 2:
                break
            await asyncio.sleep(0.01)
        assert {x["op"] for x in changes} == {"message.updated", "view.invalidated"}
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_slow_subscriber_queue_drops_old_and_coalesces_invalidation(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    entered = asyncio.Event()
    release = asyncio.Event()
    changes = []
    invalidated = asyncio.Event()
    class Host:
        async def push_bus_change(self, **kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            changes.append(kwargs)
            if kwargs["op"] == "view.invalidated":
                invalidated.set()
    state.hosts = {"p": Host()}
    state.subs = {"sub": {"from_plugin": "p", "contract": "conversations_v2", "rules": ["record"], "filters": {}, "view": True}}
    manager = BusSubscriptionManager()
    manager._push_timeout_s = 5
    await manager.start()
    try:
        manager.accept_conversation_record(record("0"))
        await asyncio.wait_for(entered.wait(), 1)
        for i in range(1, 180):
            manager.accept_conversation_record(record(str(i)))
        await asyncio.sleep(0.05)
        assert len(manager._conversation_queues["p"]) <= 128
        release.set()
        await asyncio.wait_for(invalidated.wait(), 2)
        assert sum(x["op"] == "view.invalidated" for x in changes) == 1
        ids = [x["delta"]["id"] for x in changes if x["op"] == "record"]
        assert ids[0] == "0" and ids[-1] == "179" and "1" not in ids
    finally:
        release.set()
        await manager.stop()


def test_snapshot_revision_and_patch_are_atomic_and_keep_legacy_wire():
    from plugin.message_plane.stores import build_default_store_registry
    from plugin.message_plane.runner import MessagePlaneEndpoints, PythonMessagePlaneRunner
    registry = build_default_store_registry(maxlen=5, frames_maxlen=2)
    store = registry.get("conversations")
    original, first = store.publish_with_revision("all", record("m")["payload"])
    runner = PythonMessagePlaneRunner(endpoints=MessagePlaneEndpoints("", "", ""), auth_token="test")
    runner._stores = registry
    notifications = []
    runner._on_record_accepted = notifications.append
    before, watermark = store.conversation_messages(filters={}, limit=10, order="asc")
    assert watermark == first == before[0]["_revision"]
    result = runner.update_conversation_message("m", {"text": "corrected"})
    after, watermark_after = store.conversation_messages(filters={}, limit=10, order="asc")
    assert result["text"] == "corrected"
    assert after[0]["_revision"] == watermark_after > watermark
    assert notifications[0]["_revision"] == watermark_after
    assert notifications[0]["seq"] == original["seq"]
    assert "_revision" not in store.get_recent("all", 1)[0]
    assert runner.get_conversation_message("m")["text"] == "corrected"
    assert runner.update_conversation_message("missing", {"text": "x"}) is None
    with pytest.raises(ValueError):
        runner.update_conversation_message("m", {"text": 42})


@pytest.mark.asyncio
async def test_plane_retirement_closes_observers_and_drains_pending(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    events = []
    class Host:
        async def push_bus_change(self, **kwargs):
            events.append(kwargs)
    state.hosts = {"p": Host()}
    state.subs = {"s": {"from_plugin": "p", "contract": "conversations_v2", "rules": ["record"], "filters": {}, "view": True}}
    manager = BusSubscriptionManager()
    await manager.start()
    try:
        await manager.invalidate_conversations("plane reset")
        assert state.subs == {}
        assert [(x["op"], x["delta"]) for x in events] == [("subscription.closed", {"reason": "plane reset"})]
        manager.accept_conversation_record(record("late"))
        await asyncio.sleep(0.05)
        assert len(events) == 1
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_invalidation_survives_send_failures_until_next_delivery(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    sent = []
    failed = asyncio.Event()
    recovered = asyncio.Event()
    class Host:
        attempts = 0
        async def push_bus_change(self, **kwargs):
            if kwargs["op"] == "view.invalidated":
                self.attempts += 1
                if self.attempts <= 2:
                    if self.attempts == 2:
                        failed.set()
                    raise OSError("temporarily full")
                recovered.set()
            sent.append(kwargs)
    host = Host()
    state.hosts = {"p": host}
    state.subs = {"s": {"from_plugin": "p", "contract": "conversations_v2", "rules": ["record"], "filters": {}, "view": True}}
    manager = BusSubscriptionManager()
    await manager.start()
    try:
        manager._conversation_invalidate("s", state.subs["s"])
        await asyncio.wait_for(failed.wait(), 2)
        await asyncio.sleep(0)
        assert manager._conversation_pending_invalidations["p"] == {"s"}
        manager.accept_conversation_record(record("new"))
        await asyncio.wait_for(recovered.wait(), 2)
        assert sum(item["op"] == "view.invalidated" for item in sent) == 1
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_observers_run_after_bounded_ingress_on_owner_loop(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(bus_module, "state", state)
    processed = asyncio.Event()
    observed = []
    loop_thread = threading.get_ident()
    manager = BusSubscriptionManager()
    def on_record(event):
        observed.append(("record", threading.get_ident()))
    def on_fact(op, payload):
        observed.append((op, threading.get_ident()))
        processed.set()
        return None
    manager.set_conversation_observers(on_record, on_fact)
    await manager.start()
    try:
        thread = threading.Thread(target=lambda: (manager.accept_conversation_record(record("r")), manager.accept_raw_conversation_fact("input.submitted", {"text": "x"})))
        thread.start()
        thread.join()
        await asyncio.wait_for(processed.wait(), 1)
        assert observed == [("record", loop_thread), ("input.submitted", loop_thread)]
    finally:
        await manager.stop()

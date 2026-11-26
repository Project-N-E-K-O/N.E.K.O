from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from plugin.core.state import GlobalState
from plugin.message_plane.ingest_server import MessagePlaneIngestServer
from plugin.message_plane.stores import build_default_store_registry
from plugin.server.application.messages.query_service import MessageQueryService


def _new_state() -> GlobalState:
    gs = GlobalState()
    gs._message_store.clear()
    gs._event_store.clear()
    gs._lifecycle_store.clear()
    gs._deleted_message_ids.clear()
    gs._deleted_message_ids_order.clear()
    gs._deleted_event_ids.clear()
    gs._deleted_event_ids_order.clear()
    gs._deleted_lifecycle_ids.clear()
    gs._deleted_lifecycle_ids_order.clear()
    gs._pending_event_deletes.clear()
    gs._pending_lifecycle_deletes.clear()
    gs._pending_event_overflow = False
    gs._pending_lifecycle_overflow = False
    return gs


def test_refresh_messages_cache_skips_deleted_tombstones_across_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gs = _new_state()
    gs.append_message_record({"message_id": "m1", "content": "one"})
    gs.append_message_record({"message_id": "m2", "content": "two"})
    assert gs.delete_message("m1") is True

    plane_items = [
        {"message_id": "m1", "content": "one"},
        {"payload": {"message_id": "m1", "content": "one-env"}, "index": {"id": "m1"}},
        {"index": {"id": "m1"}, "content": "one-idx"},
        {"message_id": "m2", "content": "two"},
        {"payload": {"message_id": "m3", "content": "three"}, "index": {"id": "m3"}},
    ]
    monkeypatch.setattr(
        gs,
        "_message_plane_rpc_get_recent_messages",
        lambda **_kwargs: list(plane_items),
    )

    kept = gs.refresh_messages_cache_from_message_plane(
        limit=50, timeout=1.0, ttl_seconds=0.0, force=True
    )

    assert kept == 2
    records = gs.list_message_records()
    assert len(records) == 2
    assert [gs._extract_message_id(r) for r in records] == ["m2", "m3"]


@pytest.mark.asyncio
async def test_message_query_service_does_not_resurrect_deleted_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gs = _new_state()
    gs.append_message_record({"message_id": "m-keep", "plugin_id": "demo", "content": "keep"})
    gs.append_message_record({"message_id": "m-del", "plugin_id": "demo", "content": "del"})
    assert gs.delete_message("m-del") is True

    monkeypatch.setattr(
        gs,
        "_message_plane_rpc_get_recent_messages",
        lambda **_kwargs: [
            {"message_id": "m-keep", "plugin_id": "demo", "content": "keep"},
            {"message_id": "m-del", "plugin_id": "demo", "content": "del"},
        ],
    )
    monkeypatch.setattr(
        "plugin.server.application.messages.query_service.state",
        gs,
    )

    svc = MessageQueryService()
    payload = await svc.get_plugin_messages(plugin_id=None, max_count=50, priority_min=None)
    ids = [item.get("message_id") for item in payload["messages"]]
    assert ids == ["m-keep"]


def test_delete_event_and_lifecycle_sync_message_plane_only_when_removed() -> None:
    gs = _new_state()
    assert isinstance(gs._bus_store_lock, type(threading.RLock()))

    with patch("plugin.server.messaging.plane_bridge.publish_record") as pub_rec, patch(
        "plugin.server.messaging.plane_bridge.publish_snapshot"
    ) as pub_snap:
        gs.append_event_record({"event_id": "e1", "type": "t1"})
        gs.append_event_record({"event_id": "e2", "type": "t2"})
        gs.append_lifecycle_record({"lifecycle_id": "l1", "type": "start"})
        gs.append_lifecycle_record({"lifecycle_id": "l2", "type": "stop"})
        assert pub_rec.call_count == 4

        assert gs.delete_event("missing") is False
        assert gs.delete_lifecycle("missing") is False
        pub_snap.assert_not_called()

        assert gs.delete_event("e1") is True
        pub_snap.assert_called_once_with(
            store="events",
            records=[{"event_id": "e1", "trace_id": "e1"}],
            topic="all",
            mode="delete",
        )
        pub_snap.reset_mock()

        assert gs.delete_lifecycle("l1") is True
        pub_snap.assert_called_once_with(
            store="lifecycle",
            records=[{"lifecycle_id": "l1", "trace_id": "l1"}],
            topic="all",
            mode="delete",
        )


def test_event_delete_replays_cleanly_into_topic_store() -> None:
    gs = _new_state()
    registry = build_default_store_registry(maxlen=100, frames_maxlen=10)
    srv = MessagePlaneIngestServer(
        endpoint=f"inproc://test_bus_delete_{uuid.uuid4().hex}",
        stores=registry,
        pub_server=None,
        auth_token="test-token",
    )
    events_store = registry.get("events")
    lifecycle_store = registry.get("lifecycle")
    assert events_store is not None and lifecycle_store is not None
    # Remote records present only on message_plane must survive local delete_event/delete_lifecycle.
    events_store.publish("all", {"event_id": "remote-e1", "plugin_id": "remote", "type": "r"})
    lifecycle_store.publish("all", {"lifecycle_id": "remote-l1", "plugin_id": "remote", "type": "r"})
    ops: list[tuple[str, str, str, object]] = []

    def _rec(*, store: str, record: dict, topic: str = "all") -> None:
        ops.append(("delta", store, topic, dict(record)))

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> None:
        ops.append(("snapshot", store, topic, (mode, [dict(r) for r in records])))

    try:
        with patch("plugin.server.messaging.plane_bridge.publish_record", side_effect=_rec), patch(
            "plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap
        ):
            gs.append_event_record({"event_id": "e1", "plugin_id": "p", "type": "a"})
            gs.append_event_record({"event_id": "e2", "plugin_id": "p", "type": "b"})
            gs.append_lifecycle_record({"lifecycle_id": "l1", "plugin_id": "p", "type": "start"})
            gs.append_lifecycle_record({"lifecycle_id": "l2", "plugin_id": "p", "type": "stop"})
            assert gs.delete_event("e1") is True
            assert gs.delete_lifecycle("l1") is True

        for kind, store_name, topic, arg in ops:
            if kind == "delta":
                st = registry.get(store_name)
                assert st is not None
                st.publish(topic=topic, payload=arg)  # type: ignore[arg-type]
            else:
                mode, recs = arg  # type: ignore[misc]
                assert mode == "delete"
                srv._ingest_snapshot(
                    {"store": store_name, "topic": topic, "mode": mode, "items": recs}
                )

        assert [item["payload"]["event_id"] for item in events_store.get_recent("all", 10)] == [
            "remote-e1",
            "e2",
        ]
        assert [
            item["payload"]["lifecycle_id"] for item in lifecycle_store.get_recent("all", 10)
        ] == ["remote-l1", "l2"]
    finally:
        srv.close()


def test_append_and_extend_filter_tombstoned_nested_message_ids() -> None:
    gs = _new_state()
    with patch("plugin.server.messaging.plane_bridge.publish_snapshot"):
        gs.append_message_record({"payload": {"message_id": "nested-1"}, "source": "s"})
        gs.append_message_record({"index": {"id": "index-1"}, "source": "s"})
        assert gs.delete_message("nested-1") is True
        assert gs.delete_message("index-1") is True

    gs.append_message_record({"payload": {"message_id": "nested-1"}, "source": "s"})
    assert gs.extend_message_records([{"index": {"id": "index-1"}, "source": "s"}]) == 0
    assert gs.extend_message_records_coalesced(
        [
            {"payload": {"message_id": "nested-1"}, "source": "s"},
            {"index": {"id": "index-1"}, "source": "s"},
            {"message_id": "keep-1", "source": "s"},
        ]
    ) == 1
    assert [gs._extract_message_id(m) for m in gs.list_message_records()] == ["keep-1"]


def test_topic_store_delete_records_prefers_primary_id_over_generic_index() -> None:
    registry = build_default_store_registry(maxlen=50, frames_maxlen=10)
    events = registry.get("events")
    lifecycle = registry.get("lifecycle")
    assert events is not None and lifecycle is not None

    events.publish("all", {"event_id": "e1", "type": "a"})
    # Payload has primary event_id="e2" plus a conflicting message_id="e1" copied into index.id.
    events.publish("all", {"event_id": "e2", "message_id": "e1", "type": "b"})
    removed_ev = events.delete_records("all", ids={"e1"}, id_fields=("event_id", "trace_id"))
    assert removed_ev == 1
    assert [item["payload"]["event_id"] for item in events.get_recent("all", 10)] == ["e2"]

    lifecycle.publish("all", {"lifecycle_id": "l1", "type": "start"})
    lifecycle.publish("all", {"lifecycle_id": "l2", "message_id": "l1", "type": "stop"})
    removed_lc = lifecycle.delete_records("all", ids={"l1"}, id_fields=("lifecycle_id", "trace_id"))
    assert removed_lc == 1
    assert [item["payload"]["lifecycle_id"] for item in lifecycle.get_recent("all", 10)] == ["l2"]


def test_failed_event_delete_is_queued_and_flushed_later() -> None:
    gs = _new_state()
    calls: list[tuple[str, str, list[dict]]] = []
    bridge_ready = {"ok": False}

    def _rec(*, store: str, record: dict, topic: str = "all") -> bool:
        return True

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> bool:
        calls.append((store, mode, [dict(r) for r in records]))
        return bridge_ready["ok"]

    with patch("plugin.server.messaging.plane_bridge.publish_record", side_effect=_rec), patch(
        "plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap
    ):
        gs.append_event_record({"event_id": "e1", "type": "a"})
        assert gs.delete_event("e1") is True
        assert calls[-1] == ("events", "delete", [{"event_id": "e1", "trace_id": "e1"}])
        assert "e1" in gs._pending_event_deletes

        # Bridge recovers; appending next record or retrying delete flushes pending deletes.
        bridge_ready["ok"] = True
        gs.append_event_record({"event_id": "e2", "type": "b"})
        assert gs._pending_event_deletes == {}
        assert calls[-1] == ("events", "delete", [{"event_id": "e1", "trace_id": "e1"}])


def test_sync_message_plane_holds_bus_store_lock_and_clears_pending_deletes() -> None:
    gs = _new_state()
    gs._pending_event_deletes["e1"] = {"event_id": "e1", "trace_id": "e1"}
    gs._pending_lifecycle_deletes["l1"] = {"lifecycle_id": "l1", "trace_id": "l1"}

    saw_lock_held: list[bool] = []

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> bool:
        # Release check from another thread proves the caller holds _bus_store_lock.
        acquired = [False]

        def _probe() -> None:
            got = gs._bus_store_lock.acquire(blocking=False)
            acquired[0] = got
            if got:
                gs._bus_store_lock.release()

        t = threading.Thread(target=_probe)
        t.start()
        t.join(timeout=2.0)
        saw_lock_held.append(not acquired[0])
        return True

    with patch("plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap):
        gs.sync_message_plane_events()
        gs.sync_message_plane_lifecycle()

    assert saw_lock_held == [True, True]
    assert gs._pending_event_deletes == {}
    assert gs._pending_lifecycle_deletes == {}


def test_bridge_retries_failed_delete_snapshot_in_order_until_zmq_send_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ormsgpack
    import zmq
    from plugin.server.messaging import plane_bridge as plane_bridge_mod

    sent_msgs: list[dict] = []
    first_delete_failed = threading.Event()
    allow_delete_send = threading.Event()

    class _FakeSocket:
        linger = 0

        def connect(self, _ep: str) -> None:
            pass

        def close(self, _linger: int = 0) -> None:
            pass

        def send(self, raw: bytes, flags: int = 0) -> None:
            msg = ormsgpack.unpackb(raw)
            if msg.get("mode") == "delete" and not allow_delete_send.is_set():
                first_delete_failed.set()
                raise zmq.Again()
            sent_msgs.append(msg)

    class _FakeCtx:
        def socket(self, _kind: int) -> _FakeSocket:
            return _FakeSocket()

    monkeypatch.setattr(plane_bridge_mod.zmq.Context, "instance", staticmethod(lambda: _FakeCtx()))

    bridge = plane_bridge_mod._Bridge()
    bridge._enabled = True
    bridge._endpoint = f"inproc://retry_bridge_{uuid.uuid4().hex}"
    bridge.start()
    try:
        assert bridge.enqueue_snapshot(
            store="events",
            topic="all",
            items=[{"event_id": "e1", "trace_id": "e1"}],
            mode="delete",
        ) is True
        assert first_delete_failed.wait(timeout=3.0) is True
        # Queue a subsequent delta while delete is awaiting retry; delete must still go out first.
        assert bridge.enqueue_delta(
            store="events",
            topic="all",
            payload={"event_id": "e2", "type": "after"},
        ) is True
        allow_delete_send.set()
        deadline = __import__("time").monotonic() + 3.0
        while len(sent_msgs) < 2 and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.01)
    finally:
        allow_delete_send.set()
        bridge.stop()

    assert len(sent_msgs) == 2
    assert sent_msgs[0]["kind"] == "snapshot"
    assert sent_msgs[0]["mode"] == "delete"
    assert sent_msgs[1]["kind"] == "delta_batch"
    assert sent_msgs[1]["items"][0]["payload"]["event_id"] == "e2"


def test_idle_flush_hook_and_overflow_fallback_recover_without_subsequent_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from plugin.server.messaging import plane_bridge as plane_bridge_mod

    gs = _new_state()
    calls: list[tuple[str, str, list[dict]]] = []
    bridge_ready = {"ok": False}

    def _rec(*, store: str, record: dict, topic: str = "all") -> bool:
        return True

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> bool:
        calls.append((store, mode, [dict(r) for r in records]))
        return bridge_ready["ok"]

    with patch("plugin.server.messaging.plane_bridge.publish_record", side_effect=_rec), patch(
        "plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap
    ):
        gs.append_event_record({"event_id": "e1", "type": "a"})
        assert gs.delete_event("e1") is True
        assert "e1" in gs._pending_event_deletes

        # Bridge idle hook runs after recovery with no subsequent write -> flushes pending delete.
        bridge_ready["ok"] = True
        plane_bridge_mod._bridge._run_flush_hooks()
        assert gs._pending_event_deletes == {}
        assert calls[-1] == ("events", "delete", [{"event_id": "e1", "trace_id": "e1"}])

        # Sustained outage past MAX_DELETED_BUS_IDS marks overflow and falls back to full replace.
        calls.clear()
        bridge_ready["ok"] = False
        state_module = sys.modules["plugin.core.state"]
        monkeypatch.setattr(state_module, "MAX_DELETED_BUS_IDS", 3)

        gs.append_event_record({"event_id": "keep", "type": "alive"})
        for idx in range(4):
            gs.append_event_record({"event_id": f"ov-{idx}", "type": "ov"})
            assert gs.delete_event(f"ov-{idx}") is True
        assert gs._pending_event_overflow is True

        bridge_ready["ok"] = True
        plane_bridge_mod._bridge._run_flush_hooks()
        assert gs._pending_event_overflow is False
        assert gs._pending_event_deletes == {}
        assert calls[-1] == ("events", "replace", [{"event_id": "keep", "type": "alive"}])


def test_startup_import_order_registers_plane_bridge_flush_hook_without_circular_import() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    script = """
from plugin.core.state import state

assert state._plane_bridge_flush_hook_registered is False

from plugin.server.messaging import plane_bridge

assert state._plane_bridge_flush_hook_registered is True
assert len(plane_bridge._bridge._flush_hooks) == 1

calls = []
ready = {"ok": False}

def _rec(*, store, record, topic="all"):
    return True

def _snap(*, store, records, topic="all", mode="replace"):
    calls.append((store, mode, [dict(r) for r in records]))
    return ready["ok"]

plane_bridge.publish_record = _rec
plane_bridge.publish_snapshot = _snap

state.append_event_record({"event_id": "startup-e1", "type": "a"})
assert state.delete_event("startup-e1") is True
assert "startup-e1" in state._pending_event_deletes

ready["ok"] = True
plane_bridge._bridge._run_flush_hooks()
assert state._pending_event_deletes == {}
assert calls[-1] == ("events", "delete", [{"event_id": "startup-e1", "trace_id": "startup-e1"}])
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_lazy_plane_bridge_flush_hook_registration_retries_after_initial_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.messaging import plane_bridge as plane_bridge_mod

    monkeypatch.setitem(sys.modules, "plugin.server.messaging.plane_bridge", None)
    gs = _new_state()
    assert gs._plane_bridge_flush_hook_registered is False
    assert gs._ensure_plane_bridge_flush_hook_registered() is False
    assert gs._plane_bridge_flush_hook_registered is False

    monkeypatch.setitem(sys.modules, "plugin.server.messaging.plane_bridge", plane_bridge_mod)

    calls: list[tuple[str, str, list[dict]]] = []
    bridge_ready = {"ok": False}

    def _rec(*, store: str, record: dict, topic: str = "all") -> bool:
        return True

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> bool:
        calls.append((store, mode, [dict(r) for r in records]))
        return bridge_ready["ok"]

    with patch("plugin.server.messaging.plane_bridge.publish_record", side_effect=_rec), patch(
        "plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap
    ):
        gs.append_event_record({"event_id": "e1", "type": "a"})
        assert gs.delete_event("e1") is True
        assert gs._plane_bridge_flush_hook_registered is True
        assert "e1" in gs._pending_event_deletes

        bridge_ready["ok"] = True
        plane_bridge_mod._bridge._run_flush_hooks()
        assert gs._pending_event_deletes == {}
        assert calls[-1] == ("events", "delete", [{"event_id": "e1", "trace_id": "e1"}])


def test_bridge_retries_failed_replace_snapshot_in_order_until_zmq_send_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ormsgpack
    import zmq
    from plugin.server.messaging import plane_bridge as plane_bridge_mod

    sent_msgs: list[dict] = []
    first_replace_failed = threading.Event()
    allow_replace_send = threading.Event()

    class _FakeSocket:
        linger = 0

        def connect(self, _ep: str) -> None:
            pass

        def close(self, _linger: int = 0) -> None:
            pass

        def send(self, raw: bytes, flags: int = 0) -> None:
            msg = ormsgpack.unpackb(raw)
            if msg.get("mode") == "replace" and not allow_replace_send.is_set():
                first_replace_failed.set()
                raise zmq.Again()
            sent_msgs.append(msg)

    class _FakeCtx:
        def socket(self, _kind: int) -> _FakeSocket:
            return _FakeSocket()

    monkeypatch.setattr(plane_bridge_mod.zmq.Context, "instance", staticmethod(lambda: _FakeCtx()))

    bridge = plane_bridge_mod._Bridge()
    bridge._enabled = True
    bridge._endpoint = f"inproc://retry_replace_bridge_{uuid.uuid4().hex}"
    bridge.start()
    try:
        assert bridge.enqueue_snapshot(
            store="events",
            topic="all",
            items=[{"event_id": "keep-1", "type": "alive"}],
            mode="replace",
        ) is True
        assert first_replace_failed.wait(timeout=3.0) is True
        assert bridge.enqueue_delta(
            store="events",
            topic="all",
            payload={"event_id": "e2", "type": "after"},
        ) is True
        allow_replace_send.set()
        deadline = time.monotonic() + 3.0
        while len(sent_msgs) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        allow_replace_send.set()
        bridge.stop()

    assert len(sent_msgs) == 2
    assert sent_msgs[0]["kind"] == "snapshot"
    assert sent_msgs[0]["mode"] == "replace"
    assert sent_msgs[0]["items"] == [{"event_id": "keep-1", "type": "alive"}]
    assert sent_msgs[1]["kind"] == "delta_batch"
    assert sent_msgs[1]["items"][0]["payload"]["event_id"] == "e2"

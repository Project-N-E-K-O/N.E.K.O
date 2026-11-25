from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from plugin.core.state import GlobalState
from plugin.message_plane.stores import TopicStore
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
            records=[{"event_id": "e2", "type": "t2"}],
            topic="all",
            mode="replace",
        )
        pub_snap.reset_mock()

        assert gs.delete_lifecycle("l1") is True
        pub_snap.assert_called_once_with(
            store="lifecycle",
            records=[{"lifecycle_id": "l2", "type": "stop"}],
            topic="all",
            mode="replace",
        )


def test_event_delete_replays_cleanly_into_topic_store() -> None:
    gs = _new_state()
    store = TopicStore(name="events", maxlen=100)
    ops: list[tuple[str, object]] = []

    def _rec(*, store: str, record: dict, topic: str = "all") -> None:
        if store == "events":
            ops.append(("delta", (topic, dict(record))))

    def _snap(*, store: str, records: list, topic: str = "all", mode: str = "replace") -> None:
        if store == "events":
            ops.append(("snapshot", (topic, [dict(r) for r in records], mode)))

    with patch("plugin.server.messaging.plane_bridge.publish_record", side_effect=_rec), patch(
        "plugin.server.messaging.plane_bridge.publish_snapshot", side_effect=_snap
    ):
        gs.append_event_record({"event_id": "e1", "plugin_id": "p", "type": "a"})
        gs.append_event_record({"event_id": "e2", "plugin_id": "p", "type": "b"})
        assert gs.delete_event("e1") is True

    for kind, arg in ops:
        if kind == "delta":
            topic, rec = arg  # type: ignore[misc]
            store.publish(topic=topic, payload=rec)
        else:
            topic, recs, mode = arg  # type: ignore[misc]
            assert mode == "replace"
            store.replace_topic(topic=topic, records=recs)

    recent = store.get_recent("all", 10)
    assert [item["payload"]["event_id"] for item in recent] == ["e2"]


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

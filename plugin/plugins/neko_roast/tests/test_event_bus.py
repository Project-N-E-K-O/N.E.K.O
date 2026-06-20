"""EventBus 真订阅分发骨架单测（P2.5 完整版地基 / 分发给其他开发者的核心契约）。

锁住：① subscribe/publish 按 type 路由；② 无订阅者静默丢弃（不抛不记）；③ 单订阅者 handler
抛错被隔离——其余订阅者照常收到 + 记 ``event_handler_failed`` audit（带 owner/event_type）；
④ async handler 返回的协程被调度为隔离 task，其异常同样进 audit；⑤ unsubscribe 生效；
⑥ subscriber_count；⑦ emit/on 向后兼容别名；⑧ LiveEvent 信封 to_dict 不含 raw。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.neko_roast.core.contracts import LiveEvent
from plugin.plugins.neko_roast.core.event_bus import EventBus
from plugin.plugins.neko_roast.modules.bili_live_ingest import BiliLiveIngestModule


class _Audit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, op, message="", level="info", detail=None) -> None:
        self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})


def test_publish_routes_only_to_subscribers_of_that_type():
    bus = EventBus()
    got: list = []
    bus.subscribe("danmaku", lambda e: got.append(("d", e)), owner="a")
    bus.subscribe("gift", lambda e: got.append(("g", e)), owner="b")

    bus.publish("danmaku", {"x": 1})
    assert got == [("d", {"x": 1})]
    assert bus.subscriber_count("danmaku") == 1


def test_publish_to_unsubscribed_type_is_silent_noop():
    bus = EventBus()
    bus.publish("gift", {"x": 1})  # 无订阅者：不抛、不记
    assert bus.subscriber_count("gift") == 0


def test_sync_handler_failure_is_isolated_and_audited():
    audit = _Audit()
    bus = EventBus(audit)
    seen: list = []

    def boom(_e):
        raise RuntimeError("boom")

    bus.subscribe("danmaku", boom, owner="bad")
    bus.subscribe("danmaku", lambda e: seen.append(e), owner="good")

    bus.publish("danmaku", {"x": 1})

    assert seen == [{"x": 1}]  # 坏订阅者不波及好订阅者
    rec = [r for r in audit.records if r["op"] == "event_handler_failed"]
    assert rec and rec[0]["detail"]["owner"] == "bad" and rec[0]["detail"]["event_type"] == "danmaku"


async def test_async_handler_runs_in_isolated_task():
    bus = EventBus()
    done: list = []

    async def handler(e):
        done.append(e)

    bus.subscribe("gift", handler, owner="g")
    bus.publish("gift", {"x": 1})
    await asyncio.gather(*list(bus._tasks))
    assert done == [{"x": 1}]


async def test_async_handler_failure_is_isolated_and_audited():
    audit = _Audit()
    bus = EventBus(audit)

    async def boom(_e):
        raise RuntimeError("async-boom")

    bus.subscribe("gift", boom, owner="gift_mod")
    bus.publish("gift", {"x": 1})
    await asyncio.gather(*list(bus._tasks))

    rec = [r for r in audit.records if r["op"] == "event_handler_failed"]
    assert rec and rec[0]["detail"]["owner"] == "gift_mod" and rec[0]["detail"]["event_type"] == "gift"


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    got: list = []
    unsub = bus.subscribe("danmaku", lambda e: got.append(e), owner="a")
    bus.publish("danmaku", 1)
    unsub()
    bus.publish("danmaku", 2)
    assert got == [1]
    assert bus.subscriber_count("danmaku") == 0


def test_emit_and_on_aliases_still_work_for_observability():
    bus = EventBus()
    got: list = []
    bus.on("result", lambda p: got.append(p))
    bus.emit("result", {"ok": True})
    assert got == [{"ok": True}]


def test_live_event_to_dict_excludes_raw():
    ev = LiveEvent(type="danmaku", uid="42", payload={"text": "hi"}, raw=object())
    data = ev.to_dict()
    assert data["type"] == "danmaku"
    assert data["uid"] == "42"
    assert data["payload"] == {"text": "hi"}
    assert "raw" not in data


def test_super_chat_jpn_routes_to_super_chat_bus_key():
    module = BiliLiveIngestModule()
    event = SimpleNamespace(uid=42, nickname="SCUser", text="こんにちは", room_id=100, guard_level=0)

    live_event = module._to_live_event("SUPER_CHAT_MESSAGE_JPN", event)

    assert live_event.type == "super_chat"
    assert live_event.uid == "42"
    assert live_event.payload["raw_type"] == "SUPER_CHAT_MESSAGE_JPN"

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
from plugin.plugins.neko_roast.modules.bili_live_ingest.danmaku_core import DanmakuListener


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


def test_avatar_roast_module_imports_with_its_prompt_dependencies():
    from plugin.plugins.neko_roast.modules.avatar_roast import AvatarRoastModule

    assert AvatarRoastModule.id == "avatar_roast"


def test_support_dedupe_keeps_send_gift_and_combo_send_distinct():
    module = BiliLiveIngestModule()
    common = {
        "gift_name": "小心心",
        "gift_count": 1,
        "gift_value": 100,
    }
    send = LiveEvent(type="gift", uid="9", payload={**common, "cmd": "SEND_GIFT"}, ts=100.0)
    combo = LiveEvent(type="gift", uid="9", payload={**common, "cmd": "COMBO_SEND"}, ts=100.1)
    duplicate_combo = LiveEvent(type="gift", uid="9", payload={**common, "cmd": "COMBO_SEND"}, ts=100.2)

    assert module._is_duplicate_support_event(send) is False
    assert module._is_duplicate_support_event(combo) is False
    assert module._is_duplicate_support_event(duplicate_combo) is True


def test_support_dedupe_matches_lightweight_and_rich_super_chat():
    module = BiliLiveIngestModule()
    lightweight = module._to_live_event(
        "SUPER_CHAT_MESSAGE",
        {"user_id": 9, "user_name": "SCUser", "message": "hello", "price": 30},
    )
    rich = module._to_live_event(
        "SUPER_CHAT_MESSAGE",
        SimpleNamespace(uid=9, nickname="SCUser", text="hello", room_id=1),
    )
    rich.ts = lightweight.ts + 0.1

    assert module._is_duplicate_support_event(lightweight) is False
    assert module._is_duplicate_support_event(rich) is True


def test_support_dedupe_duplicate_does_not_extend_window():
    module = BiliLiveIngestModule()
    payload = {"gift_name": "small heart", "gift_count": 1, "cmd": "SEND_GIFT"}
    first = LiveEvent(type="gift", uid="9", payload=payload, ts=100.0)
    duplicate = LiveEvent(type="gift", uid="9", payload=payload, ts=100.2)
    after_window = LiveEvent(type="gift", uid="9", payload=payload, ts=100.4)

    assert module._is_duplicate_support_event(first) is False
    assert module._is_duplicate_support_event(duplicate) is True
    assert module._is_duplicate_support_event(after_window) is False


def test_publish_updates_status_for_runtime_health_rows():
    bus = EventBus()

    bus.publish("danmaku", LiveEvent(type="danmaku", uid="1"))

    status = bus.status()
    assert status["publish_count"] == 1
    assert status["last_event_type"] == "danmaku"
    assert status["last_publish_at"] > 0


def test_observability_emit_does_not_overwrite_live_event_bus_health_status():
    bus = EventBus()

    bus.publish("danmaku", LiveEvent(type="danmaku", uid="1"))
    bus.emit("result", {"status": "dry_run"})

    status = bus.status()
    assert status["publish_count"] == 1
    assert status["last_event_type"] == "danmaku"


def test_live_ingest_status_tracks_last_published_event_for_health_rows():
    audit = _Audit()
    bus = EventBus(audit)
    module = BiliLiveIngestModule()
    module.ctx = SimpleNamespace(audit=audit, event_bus=bus)
    module._room_id = 100
    event = SimpleNamespace(uid=42, nickname="User", text="hi", room_id=100, guard_level=0)

    module._on_live_event("DANMU_MSG", event)

    status = module.status()
    assert status["last_event_type"] == "danmaku"
    assert status["last_event_at"] > 0


def test_bili_lightweight_gift_event_dict_publishes_safe_gift_event():
    module = BiliLiveIngestModule()
    module._room_id = 100

    event = module._to_live_event(
        "SEND_GIFT",
        {
            "uid": 42,
            "nickname": "GiftUser",
            "danmaku_text": "gift Small Heart",
            "gift_name": "Small Heart",
            "gift_count": 3,
            "gift_coin_type": "gold",
            "gift_value": 900,
        },
    )

    assert event.type == "gift"
    assert event.uid == "42"
    assert event.raw["event_type"] == "gift"
    assert event.raw["gift_name"] == "Small Heart"
    assert event.raw["gift_count"] == 3
    assert event.raw["gift_value"] == 900
    assert event.raw["danmaku_text"] == "gift Small Heart"


def test_bili_gift_cmd_suffix_routes_to_gift_bus_key():
    module = BiliLiveIngestModule()
    module._room_id = 100

    event = module._to_live_event(
        "SEND_GIFT:0",
        {
            "uid": 42,
            "nickname": "GiftUser",
            "danmaku_text": "gift Small Heart",
        },
    )

    assert event.type == "gift"
    assert event.payload["raw_type"] == "SEND_GIFT"


def test_bili_lightweight_and_rich_gift_callbacks_dedupe_same_event():
    class _Bus:
        def __init__(self):
            self.events = []

        def publish(self, event_type, event):
            self.events.append((event_type, event))

    bus = _Bus()
    module = BiliLiveIngestModule()
    module.ctx = SimpleNamespace(event_bus=bus, audit=_Audit())
    module._room_id = 100

    module._on_gift_event(
        {
            "user_id": 42,
            "user_name": "GiftUser",
            "gift_name": "Small Heart",
            "num": 3,
            "total_coin": 900,
        }
    )
    module._on_live_event(
        "SEND_GIFT",
        SimpleNamespace(
            uid=42,
            nickname="GiftUser",
            text="gift Small Heart",
            room_id=100,
            guard_level=0,
            gift=SimpleNamespace(gift_name="Small Heart", num=3, total_coin=900),
        ),
    )

    assert [event_type for event_type, _ in bus.events] == ["gift"]


def test_bili_combo_send_routes_to_gift_bus_key():
    module = BiliLiveIngestModule()
    module._room_id = 100

    event = SimpleNamespace(
        uid=42,
        nickname="GiftUser",
        text="连击 3 个 小花花",
        room_id=100,
        guard_level=0,
    )

    live_event = module._to_live_event("COMBO_SEND", event)

    assert live_event.type == "gift"
    assert live_event.uid == "42"
    assert live_event.payload["raw_type"] == "COMBO_SEND"
    assert live_event.payload["event_label"] == "连击 3 个 小花花"


async def test_bili_unknown_official_fans_medal_packet_routes_to_gift_event():
    got: list[tuple[str, object]] = []
    listener = DanmakuListener(room_id=100, callbacks={"on_event": lambda cmd, event: got.append((cmd, event))})

    await listener._dispatch_message(
        "USER_TOAST_MSG",
        {
            "cmd": "USER_TOAST_MSG",
            "data": {
                "uid": 42,
                "uname": "GiftUser",
                "toast_msg": "GiftUser 赠送 1 个 粉丝团灯牌",
                "gift_info": {"gift_name": "粉丝团灯牌", "num": 1, "price": 0},
            },
        },
    )

    assert got == [
        (
            "SEND_GIFT",
            {
                "uid": 42,
                "nickname": "GiftUser",
                "danmaku_text": "gift 粉丝团灯牌",
                "gift_name": "粉丝团灯牌",
                "gift_count": 1,
                "gift_value": 0,
                "room_id": 0,
                "raw_cmd": "USER_TOAST_MSG",
            },
        )
    ]


async def test_bili_plain_danmaku_claiming_fans_medal_does_not_route_to_gift_event():
    got: list[tuple[str, object]] = []
    listener = DanmakuListener(room_id=100, callbacks={"on_event": lambda cmd, event: got.append((cmd, event))})

    await listener._dispatch_message(
        "DANMU_MSG",
        {
            "cmd": "DANMU_MSG",
            "info": [
                [0, 0, 0, 0, 0],
                "我投喂了一个粉丝团灯牌",
                [42, "GiftUser"],
                [],
            ],
        },
    )

    assert [cmd for cmd, _ in got] == ["DANMU_MSG"]


def test_bili_lightweight_super_chat_event_dict_publishes_safe_super_chat_event():
    module = BiliLiveIngestModule()
    module._room_id = 100

    event = module._to_live_event(
        "SUPER_CHAT_MESSAGE",
        {
            "uid": 43,
            "nickname": "SCUser",
            "danmaku_text": "hello neko",
            "gift_name": "Super Chat",
            "gift_value": 30000,
        },
    )

    assert event.type == "super_chat"
    assert event.uid == "43"
    assert event.raw["event_type"] == "super_chat"
    assert event.raw["danmaku_text"] == "hello neko"
    assert event.raw["gift_name"] == "Super Chat"
    assert event.raw["gift_value"] == 30000
    assert event.raw["room_id"] == 100


async def test_event_bus_close_drains_handlers_and_rejects_late_publish():
    bus = EventBus()
    release = asyncio.Event()
    received: list[str] = []

    async def handler(value: str) -> None:
        await release.wait()
        received.append(value)

    bus.subscribe("chat", handler, owner="test")
    bus.publish("chat", "before-close")
    closing = asyncio.create_task(bus.close(timeout=1.0))
    await asyncio.sleep(0)
    bus.publish("chat", "after-close")
    release.set()
    await closing

    assert received == ["before-close"]
    assert bus.status()["accepting_events"] is False
    assert bus.status()["pending_tasks"] == 0

"""LiveEvent 中枢（P2.5 slice 1）单测。

锁住：① 空闲态首条弹幕即时锐评（保留 DoD）；② 冷却期开窗缓冲、按 get_score 择优、整窗只投
1 条；③ 高价值礼物/SC/上舰也参与冷却窗口择优；④ 中枢本地冷却挡住紧接着到的事件，
不并发双锐评；⑤ 空 uid / 空文本丢弃；⑥ reset 取消开窗；⑦ safety_guard 冷却助手时序。

中枢的 pipeline 投递走桩 ctx.handle_live_payload，开窗 sleep 注入成 no-op，做确定性验证。
"""

from __future__ import annotations

import asyncio

from plugin.plugins.neko_roast.core.contracts import LiveEvent, RoastConfig
from plugin.plugins.neko_roast.core.event_bus import EventBus
from plugin.plugins.neko_roast.modules.bili_live_ingest.livedanmaku import LiveDanmaku
from plugin.plugins.neko_roast.modules.live_events import LiveEventsModule


def _danmaku(uid: str, text: str = "hi", guard: int = 0, user_level: int = 0, room_id: int = 1) -> LiveDanmaku:
    # info[2]=用户数组, info[4]=用户等级数组, info[7]=大航海等级(int)。
    info = [[], text, [int(uid), f"u{uid}"], [], [user_level], 0, 0, int(guard)]
    return LiveDanmaku.from_danmaku({"info": info, "room_id": room_id})


def _gift(uid: str, gift_name: str = "小心心", total_coin: int = 0, room_id: int = 1) -> LiveDanmaku:
    return LiveDanmaku.from_gift({
        "data": {
            "uid": int(uid),
            "uname": f"u{uid}",
            "giftName": gift_name,
            "num": 1,
            "total_coin": total_coin,
            "room_id": room_id,
        }
    })


class _FakeSafety:
    def __init__(self, remaining: float = 0.0) -> None:
        self.remaining = remaining

    def output_cooldown_remaining(self, now: float | None = None) -> float:
        return self.remaining


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, op, message="", level="info", detail=None) -> None:
        self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})


class _FakeCtx:
    def __init__(self, remaining: float = 0.0, rate_limit: int = 20) -> None:
        self.safety_guard = _FakeSafety(remaining)
        self.audit = _FakeAudit()
        self.event_bus = EventBus(self.audit)
        self.config = RoastConfig(rate_limit_seconds=rate_limit)
        self.payloads: list[dict] = []

    async def handle_live_payload(self, payload: dict):
        self.payloads.append(payload)
        return None


async def _make_hub(ctx: _FakeCtx) -> LiveEventsModule:
    hub = LiveEventsModule()
    await hub.setup(ctx)

    async def _nosleep(_delay: float) -> None:  # 单测不真的等冷却窗口
        return None

    hub._sleep = _nosleep
    return hub


async def _drain(hub: LiveEventsModule) -> None:
    """跑完中枢 spawn 的所有后台 task（即时 roast / 开窗 flush）。"""
    for _ in range(5):
        tasks = [t for t in list(hub._tasks) if not t.done()]
        if not tasks:
            break
        await asyncio.gather(*tasks)


async def test_idle_first_danmaku_roasts_immediately():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="初见"))
    assert hub._flush_task is None  # 即时分支，未开窗

    await _drain(hub)
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "初见"


async def test_cooldown_window_picks_highest_score():
    ctx = _FakeCtx(remaining=5.0)  # 冷却中 -> 缓冲择优
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="路人甲"))             # guard 0
    hub.submit(_danmaku("2", text="总督驾到", guard=1))   # 总督 +3000
    hub.submit(_danmaku("3", text="8888"))              # guard 0
    assert hub._flush_task is not None
    assert hub._buffered_count == 3

    await _drain(hub)
    assert len(ctx.payloads) == 1            # 整窗只投 1 条
    assert ctx.payloads[0]["uid"] == "2"     # 分最高的总督胜出
    assert any(
        r["op"] == "live_event_selected" and r["detail"]["candidates"] == 3
        for r in ctx.audit.records
    )


async def test_cooldown_window_allows_high_value_gift_to_beat_danmaku():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="普通弹幕"))
    hub.submit(_gift("9", gift_name="醒目礼物", total_coin=200000))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "9"
    assert ctx.payloads[0]["danmaku_text"] == "赠送 1 个 醒目礼物"
    assert any(
        r["op"] == "live_event_selected" and r["detail"]["event_type"] == "gift"
        for r in ctx.audit.records
    )


async def test_event_bus_routes_gift_events_into_live_event_window():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    gift = _gift("9", gift_name="醒目礼物", total_coin=200000)
    ctx.event_bus.publish("gift", LiveEvent(type="gift", uid="9", payload={}, raw=gift))
    await _drain(hub)

    assert ctx.event_bus.subscriber_count("gift") == 1
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "9"


async def test_local_cooldown_blocks_second_concurrent_roast():
    # safety_guard 始终说可投（remaining=0）；靠中枢本地刚投递冷却挡住紧接着到的第二条。
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="first"))    # 即时
    assert hub._flush_task is None
    hub.submit(_danmaku("2", text="second"))   # 本地冷却挡住 -> 开窗缓冲，而非第二次即时
    assert hub._flush_task is not None

    await _drain(hub)
    assert len(ctx.payloads) == 2
    assert sorted(p["uid"] for p in ctx.payloads) == ["1", "2"]


async def test_blank_uid_or_text_is_dropped():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("0", text="uid 为 0"))  # uid 0 丢弃
    hub.submit(_danmaku("5", text="   "))        # 空文本丢弃
    await _drain(hub)

    assert ctx.payloads == []


async def test_reset_cancels_open_window():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="缓冲中"))
    assert hub._flush_task is not None

    hub.reset()
    assert hub._flush_task is None
    assert hub._best is None
    assert hub._buffered_count == 0


async def test_flush_exception_clears_window_state():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    async def _boom(_delay: float) -> None:
        raise RuntimeError("sleep failed")

    hub._sleep = _boom
    hub.submit(_danmaku("1", text="缓冲中"))
    await _drain(hub)

    assert hub._flush_task is None
    assert hub._best is None
    assert hub._best_score == 0.0
    assert hub._buffered_count == 0
    assert any(r["op"] == "live_event_flush_failed" for r in ctx.audit.records)


async def test_external_flush_cancel_clears_task_reference():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)
    blocker = asyncio.Event()

    async def _blocked(_delay: float) -> None:
        await blocker.wait()

    hub._sleep = _blocked
    hub.submit(_danmaku("1", text="缓冲中"))
    task = hub._flush_task
    assert task is not None

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert hub._flush_task is None

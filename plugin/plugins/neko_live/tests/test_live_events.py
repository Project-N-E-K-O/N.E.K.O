"""LiveEvent 中枢（P2.5 slice 1）单测。

锁住：① 空闲态首条弹幕即时锐评（保留 DoD）；② 冷却期开窗缓冲、按 get_score 择优、整窗只投
1 条；③ 高价值礼物/SC/上舰也参与冷却窗口择优；④ 中枢本地冷却挡住紧接着到的事件，
不并发双锐评；⑤ 空 uid / 空文本丢弃；⑥ reset 取消开窗；⑦ safety_guard 冷却助手时序。

中枢的 pipeline 投递走桩 ctx.handle_live_payload，开窗 sleep 注入成 no-op，做确定性验证。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.core.contracts import (
    LiveEvent,
    LiveConfig,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_live.core.event_bus import EventBus
from plugin.plugins.neko_live.core.safety_guard import SafetyGuard
from plugin.plugins.neko_live.modules.bili_live_ingest.livedanmaku import LiveDanmaku, MessageType
from plugin.plugins.neko_live.modules.danmaku_response import DanmakuResponseModule
from plugin.plugins.neko_live.modules.live_events import LiveEventsModule
from plugin.plugins.neko_live.modules.live_events.module import (
    LIVE_CONTEXT_PROMPT_MAX_CHARS,
)
from plugin.plugins.neko_live.modules.live_events.ritual_memory import RitualPrompt
from plugin.plugins.neko_live.modules.live_events.room_topic import RoomTopicContext
from plugin.plugins.neko_live.modules.live_support_events import LiveSupportEventsModule
from plugin.plugins.neko_live.modules.live_events.provider_event import (
    event_avatar_url,
    event_guard_level,
    event_nickname,
    event_prompt_text,
    event_room_id,
    event_room_ref,
    event_score,
    event_signal_fields,
    event_text,
    event_type,
    event_uid,
    public_text,
    safe_public_url,
)
from plugin.plugins.neko_live.modules.live_events.recent_chat import RecentChatBuffer
from plugin.plugins.neko_live.modules.live_events.recent_chat_relevance import (
    clean_relevance_query,
    relevance_score,
)


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
    def __init__(
        self,
        remaining: float = 0.0,
        rate_limit: int = 20,
        activity_level: str = "standard",
        queue_limit: int = 5,
        viewer_count: int = 0,
        live_mode: str = "solo_stream",
    ) -> None:
        self.safety_guard = _FakeSafety(remaining)
        self.audit = _FakeAudit()
        self.event_bus = EventBus(self.audit)
        self.live_provider = SimpleNamespace(listener_state=lambda: {"viewer_count": viewer_count})
        self.config = LiveConfig(
            live_mode=live_mode,  # type: ignore[arg-type]
            rate_limit_seconds=rate_limit,
            activity_level=activity_level,  # type: ignore[arg-type]
            queue_limit=queue_limit,
        )
        self.recent_results: list[dict] = []
        self.payloads: list[dict] = []

    async def handle_live_payload(self, payload: dict):
        self.payloads.append(payload)
        return None

    def _iso_age_sec(self, created_at: str) -> float:
        if created_at.startswith("age:"):
            return float(created_at.split(":", 1)[1])
        return 0.0


class _FakeAmbientDispatcher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def output_channel_status(self) -> dict[str, object]:
        return {"ready": True}

    async def push_ambient_room_context(
        self,
        text: str,
        *,
        session_key: str,
        expired: bool = False,
    ) -> str:
        self.messages.append(
            {
                "text": text,
                "session_key": session_key,
                "expired": expired,
            }
        )
        return "queued"


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


async def _drain_support(module: LiveSupportEventsModule) -> None:
    if module._scheduler is not None:
        await module._scheduler.wait_idle()
    for _ in range(5):
        tasks = [task for task in list(module._tasks) if not task.done()]
        if not tasks:
            break
        await asyncio.gather(*tasks)


async def test_co_stream_danmaku_keeps_snapshot_and_selects_bounded_candidates():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)
    ctx.live_events = hub

    for uid in ("1", "2", "3", "4"):
        hub.submit(_danmaku(uid, text=f"message-{uid}"))

    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    assert 1 <= len(ctx.payloads) <= 2
    assert all(payload["event_type"] == "danmaku" for payload in ctx.payloads)
    assert all(
        "co_stream_deferred_candidate" not in payload
        for payload in ctx.payloads
    )
    assert len(ctx.dispatcher.messages) == 1
    snapshot = ctx.dispatcher.messages[0]
    assert snapshot["expired"] is False
    assert "message-4" in snapshot["text"]
    assert "message-3" in snapshot["text"]
    for payload in ctx.payloads:
        if payload["danmaku_text"] in snapshot["text"]:
            assert f"{payload['danmaku_text']}｜已回复" in snapshot["text"]
    assert "message-1" not in snapshot["text"]
    assert hub.status()["ambient_publish_count"] == 1
    await hub.teardown()


async def test_co_stream_worthy_question_uses_existing_active_path_and_dedupes_redelivery():
    ctx = _FakeCtx(
        remaining=0.0,
        activity_level="quiet",
        live_mode="co_stream",
    )
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)
    first = _danmaku("42", text="猫猫觉得这个结局为什么好笑？")
    first.provider_event_id = "event-worthy-42"
    redelivery = _danmaku("42", text="猫猫觉得这个结局为什么好笑？")
    redelivery.provider_event_id = "event-worthy-42"

    hub.submit(first)
    hub.submit(redelivery)
    await _drain(hub)

    # Content value keeps one active request alive even in quiet mode. The
    # existing host pipeline owns speech collision safety; transport
    # redelivery is removed locally before it can create a second request.
    assert [payload["danmaku_text"] for payload in ctx.payloads] == [
        "猫猫觉得这个结局为什么好笑？"
    ]
    assert hub.status()["recent_chat_duplicate_delivery_count"] == 1
    assert not any(
        key in ctx.payloads[0]
        for key in ("vad", "voice_activity", "playback_state", "audio_done")
    )
    await hub.teardown()


async def test_co_stream_output_policy_off_keeps_context_but_queues_no_candidate():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="context only"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh
    await _drain(hub)

    assert ctx.payloads == []
    assert len(ctx.dispatcher.messages) == 1
    assert hub.status()["last_skip_reason"] == "co_stream.output_policy_off"
    await hub.teardown()


async def test_co_stream_does_not_publish_an_unchanged_snapshot_twice():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="same-snapshot"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    assert await hub._publish_ambient_context() is False
    assert len(ctx.dispatcher.messages) == 1
    assert hub.status()["ambient_publish_count"] == 1
    assert hub.status()["ambient_publish_last_reason"] == "unchanged"
    await hub.teardown()


async def test_co_stream_empty_session_publishes_authoritative_absence():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.schedule_session_context_refresh()
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    assert len(ctx.dispatcher.messages) == 1
    marker = ctx.dispatcher.messages[0]
    assert marker["expired"] is False
    assert "当前无权威弹幕行" in marker["text"]
    assert "必须明确说无法确认" in marker["text"]
    assert "历史对话" in marker["text"]
    assert hub.status()["ambient_publish_count"] == 1
    await hub.teardown()


async def test_co_stream_selected_danmaku_keeps_latest_position_as_fact_only():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)
    release_refresh = asyncio.Event()

    async def wait_for_selection(_delay: float) -> None:
        await release_refresh.wait()

    hub._ambient_sleep = wait_for_selection
    previous = _danmaku("41", text="still-previous")
    latest = _danmaku("42", text="active-only")
    hub.observe_danmaku(previous)
    latest_seq = hub.observe_danmaku(latest)
    await asyncio.sleep(0)
    await hub._roast(
        latest,
        count=1,
        recent_chat_seq=latest_seq,
    )
    release_refresh.set()
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert len(ctx.dispatcher.messages) == 1
    assert ctx.dispatcher.messages[0]["expired"] is False
    assert "权威｜最新｜昵称=u42｜弹幕=active-only｜已回复" in (
        ctx.dispatcher.messages[0]["text"]
    )
    assert "权威｜上一条｜昵称=u41｜弹幕=still-previous" in (
        ctx.dispatcher.messages[0]["text"]
    )
    assert "接梗候选：上一条" in ctx.dispatcher.messages[0]["text"]
    assert "接梗候选：最新" not in ctx.dispatcher.messages[0]["text"]
    assert hub.status()["recent_chat_unselected_count"] == 1
    assert hub.status()["ambient_publish_last_reason"] == "submitted_unconfirmed"
    await hub.teardown()


async def test_co_stream_selection_marks_existing_authoritative_snapshot_replied():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    async def no_sleep(_delay: float) -> None:
        return None

    hub._ambient_sleep = no_sleep
    event = _danmaku("42", text="queued-before-selection")
    seq = hub.observe_danmaku(event)
    await _drain(hub)

    assert [item["expired"] for item in ctx.dispatcher.messages] == [False]

    await hub._roast(
        event,
        count=1,
        recent_chat_seq=seq,
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert [item["expired"] for item in ctx.dispatcher.messages] == [False, False]
    assert ctx.dispatcher.messages[-1]["session_key"] == (
        ctx.dispatcher.messages[0]["session_key"]
    )
    assert "queued-before-selection｜已回复" in ctx.dispatcher.messages[-1]["text"]
    assert hub.status()["ambient_expiry_count"] == 0
    assert hub.status()["ambient_publish_count"] == 2
    assert hub.status()["ambient_publish_last_reason"] == "submitted_unconfirmed"
    await hub.teardown()


async def test_co_stream_passive_snapshot_selects_one_continuing_topic_hook():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("41", text="刚才那个纸箱像猫猫的新房子"))
    hub.submit(_danmaku("42", text="猫猫钻纸箱的时候把自己卡住了"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    snapshot = ctx.dispatcher.messages[-1]["text"]
    assert "接梗候选：最新｜类型=连续话题" in snapshot
    assert "动作=沿共同话题或笑点推进一拍，不解释、不复述" in snapshot
    assert "正文优先" in snapshot
    assert "禁止复用上一轮完整回答" in snapshot
    status = hub.status()
    assert status["ambient_hook_candidate_reads"] == 1
    assert status["ambient_hook_candidate_hits"] == 1
    assert status["ambient_hook_last_reason"] == "selected.continuity"
    assert status["ambient_hook_last_score"] > 0
    assert status["ambient_hook_last_candidate_count"] == 2
    status_text = repr(status)
    assert "刚才那个纸箱像猫猫的新房子" not in status_text
    assert "猫猫钻纸箱的时候把自己卡住了" not in status_text
    assert "u41" not in status_text
    assert "u42" not in status_text
    await hub.teardown()


async def test_co_stream_passive_snapshot_keeps_distinct_viewer_chorus_as_one_hook():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="猫猫钻纸箱真的太好笑了"))
    hub.submit(_danmaku("2", text="猫猫钻纸箱真的太好笑了"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    snapshot = ctx.dispatcher.messages[-1]["text"]
    assert snapshot.count("接梗候选：") == 1
    assert "类型=多人接梗" in snapshot
    assert hub.status()["ambient_hook_last_reason"] == "selected.chorus"
    await hub.teardown()


async def test_co_stream_passive_snapshot_keeps_noise_as_fact_but_not_hook():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("41", text="😂😂😂"))
    hub.submit(_danmaku("42", text="哈哈哈"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    snapshot = ctx.dispatcher.messages[-1]["text"]
    assert "权威｜最新｜昵称=u42｜弹幕=哈哈哈" in snapshot
    assert "\n接梗候选：" not in snapshot
    assert "\n接梗候选（" not in snapshot
    status = hub.status()
    assert status["ambient_hook_candidate_reads"] == 1
    assert status["ambient_hook_candidate_hits"] == 0
    assert status["ambient_hook_last_reason"] == "low_value"
    assert status["ambient_hook_last_score"] == 0
    await hub.teardown()


async def test_co_stream_refresh_waits_again_before_a_follow_up_publish():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    dispatcher = _FakeAmbientDispatcher()
    ctx.dispatcher = dispatcher
    hub = await _make_hub(ctx)
    second_delay_started = asyncio.Event()
    release_second_delay = asyncio.Event()
    debounce_calls = 0

    async def controlled_sleep(delay: float) -> None:
        nonlocal debounce_calls
        if delay > 1.0:
            await asyncio.Event().wait()
            return
        debounce_calls += 1
        if debounce_calls == 1:
            return
        second_delay_started.set()
        await release_second_delay.wait()

    original_push = dispatcher.push_ambient_room_context

    async def push_and_add_one_more(
        text: str,
        *,
        session_key: str,
        expired: bool = False,
    ) -> str:
        result = await original_push(
            text,
            session_key=session_key,
            expired=expired,
        )
        if len(dispatcher.messages) == 1:
            hub.submit(_danmaku("43", text="follow-up"))
        return result

    dispatcher.push_ambient_room_context = push_and_add_one_more  # type: ignore[method-assign]
    hub._ambient_sleep = controlled_sleep
    hub.submit(_danmaku("42", text="first"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None

    await asyncio.wait_for(second_delay_started.wait(), timeout=1.0)
    assert len(dispatcher.messages) == 1

    release_second_delay.set()
    await refresh

    assert len(dispatcher.messages) == 2
    assert debounce_calls == 2
    assert dispatcher.messages[0]["session_key"] == (
        dispatcher.messages[1]["session_key"]
    )
    assert "权威｜最新｜昵称=u43｜弹幕=follow-up" in (
        dispatcher.messages[1]["text"]
    )
    await hub.teardown()


async def test_observe_danmaku_records_context_without_selecting_a_reply():
    ctx = _FakeCtx(remaining=0.0, live_mode="solo_stream")
    ctx.config.live_enabled = True
    hub = await _make_hub(ctx)

    seq = hub.observe_danmaku(_danmaku("42", text="offline sandbox message"))

    assert seq == 1
    assert ctx.payloads == []
    assert hub.recent_chat_snapshot(limit=1) == [
        {
            "seq": 1,
            "uid": "42",
            "nickname": "u42",
            "text": "offline sandbox message",
            "seconds_ago": 0.0,
            "selected": False,
            "within_fresh_window": True,
        }
    ]
    await hub.teardown()


async def test_co_stream_passive_snapshot_survives_delivery_delay():
    """The host delivers a passive snapshot at the next natural hot swap, which
    can be minutes later (owner decision in main_logic/core/lifecycle.py). The
    snapshot must therefore contain nothing that decays: positional labels
    only, no elapsed time, and no claim about whether anything was spoken."""
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    async def no_sleep(_delay: float) -> None:
        return None

    hub._ambient_sleep = no_sleep
    ctx.config.co_stream_output_policy = "off"
    hub.submit(_danmaku("42", text="喵喵喵"))
    await _drain(hub)

    text = ctx.dispatcher.messages[0]["text"]
    assert [item["expired"] for item in ctx.dispatcher.messages] == [False]
    assert "最新｜昵称=u42｜弹幕=喵喵喵" in text
    assert "秒前" not in text
    assert "已排队一次主动感谢" not in text
    assert hub.status()["ambient_publish_count"] == 1
    await hub.teardown()


async def test_co_stream_passive_snapshot_has_no_expiry_timer():
    """The 45-second freshness timer is gone: it fought the swap cadence
    (a 45s window against a 2-3 minute delivery interval measured on real
    devices), so a snapshot expired before it could ever be delivered.
    Retirement now happens only at a session boundary."""
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    async def no_sleep(_delay: float) -> None:
        return None

    hub._ambient_sleep = no_sleep
    assert hub.remember_support_context(
        {
            "event_type": "gift",
            "nickname": "alice",
            "gift_name": "小心心",
            "support_verified": True,
        },
        tier="light",
    )
    await _drain(hub)

    # One publish, and no follow-up expiry push retiring it behind the
    # host's back.
    assert [item["expired"] for item in ctx.dispatcher.messages] == [False]
    assert "alice" in ctx.dispatcher.messages[0]["text"]
    assert not hasattr(hub, "_ambient_expiry_task")
    await hub.teardown()


async def test_co_stream_session_reset_clears_the_previous_session_key():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx._live_session_generation = 7
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="旧直播间弹幕"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh
    assert ctx.dispatcher.messages[-1]["session_key"].startswith("7:")

    ctx._live_session_generation = 8
    hub.reset()
    pending = list(hub._ambient_clear_tasks)
    assert pending
    await asyncio.gather(*pending)

    assert ctx.dispatcher.messages[-1]["expired"] is True
    assert ctx.dispatcher.messages[-1]["session_key"].startswith("7:")
    assert "旧直播间弹幕" not in ctx.dispatcher.messages[-1]["text"]

    hub.schedule_session_context_refresh()
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh

    assert ctx.dispatcher.messages[-1]["expired"] is False
    assert ctx.dispatcher.messages[-1]["session_key"].startswith("8:")
    assert "当前无权威弹幕行" in ctx.dispatcher.messages[-1]["text"]
    assert "旧直播间弹幕" not in ctx.dispatcher.messages[-1]["text"]
    assert "接梗候选" not in ctx.dispatcher.messages[-1]["text"]
    assert hub.status()["ambient_hook_last_reason"] == "no_candidates"
    await hub.teardown()


async def test_co_stream_new_snapshot_waits_for_previous_session_clear():
    class OrderedDispatcher(_FakeAmbientDispatcher):
        def __init__(self) -> None:
            super().__init__()
            self.clear_started = asyncio.Event()
            self.release_clear = asyncio.Event()

        async def push_ambient_room_context(
            self,
            text: str,
            *,
            session_key: str,
            expired: bool = False,
        ) -> str:
            if expired:
                self.clear_started.set()
                await self.release_clear.wait()
            return await super().push_ambient_room_context(
                text,
                session_key=session_key,
                expired=expired,
            )

    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx._live_session_generation = 7
    dispatcher = OrderedDispatcher()
    ctx.dispatcher = dispatcher
    hub = await _make_hub(ctx)

    async def no_sleep(_delay: float) -> None:
        return None

    hub._ambient_sleep = no_sleep
    hub.submit(_danmaku("42", text="old-session"))
    await hub._ambient_refresh_task

    ctx._live_session_generation = 8
    hub.reset()
    await dispatcher.clear_started.wait()
    hub.schedule_session_context_refresh()
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await asyncio.sleep(0)
    assert [item["expired"] for item in dispatcher.messages] == [False]

    dispatcher.release_clear.set()
    await refresh

    assert [item["expired"] for item in dispatcher.messages] == [False, True, False]
    assert dispatcher.messages[-1]["session_key"].startswith("8:")
    await hub.teardown()


async def test_co_stream_teardown_clears_the_replaceable_snapshot():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.config.co_stream_output_policy = "off"
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="shutdown-safe"))
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh
    assert len(ctx.dispatcher.messages) == 1

    await hub.teardown()

    assert len(ctx.dispatcher.messages) == 2
    assert ctx.dispatcher.messages[-1]["expired"] is True
    assert ctx.dispatcher.messages[-1]["session_key"] == ctx.dispatcher.messages[0]["session_key"]


async def test_co_stream_support_routes_verified_gifts_to_active_ack_and_passive_context():
    ctx = _FakeCtx(remaining=0.0, live_mode="co_stream")
    ctx.config.live_enabled = True
    ctx.dispatcher = _FakeAmbientDispatcher()
    hub = await _make_hub(ctx)
    ctx.live_events = hub
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    def publish(event_id: str, *, value: int) -> None:
        ctx.event_bus.publish(
            "gift",
            LiveEvent(
                type="gift",
                uid="9",
                payload={
                    "nickname": "alice",
                    "gift_name": "小心心" if value < 10_000 else "醒目礼物",
                    "gift_value": value,
                    "coin_type": "gold",
                    "support_verified": True,
                    "support_evidence": "manual_live_simulation",
                    "provider_event_id": event_id,
                    "provider_event_type": "SEND_GIFT",
                },
            ),
        )

    publish("light-1", value=10)
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh
    await _drain_support(support)
    assert [payload["provider_event_id"] for payload in ctx.payloads] == ["light-1"]
    # Support also remains available as a passive room fact. The snapshot
    # never claims the acknowledgement was delivered (ledger CSL-007).
    assert "最近一笔支持" in ctx.dispatcher.messages[-1]["text"]
    assert "仅被动记住" not in ctx.dispatcher.messages[-1]["text"]

    publish("high-1", value=10_000)
    refresh = hub._ambient_refresh_task
    assert refresh is not None
    await refresh
    await _drain_support(support)

    assert [payload["provider_event_id"] for payload in ctx.payloads] == ["light-1", "high-1"]
    assert "co_stream_deferred_candidate" not in ctx.payloads[0]
    # High-value support still takes the active path (asserted above via
    # ctx.payloads); the passive shadow must NOT assert it was spoken.
    assert "已排队一次主动感谢" not in ctx.dispatcher.messages[-1]["text"]
    strategy = [
        item for item in ctx.audit.records
        if item["op"] == "support.co_stream_strategy"
    ]
    assert [
        item["detail"]["active_attempt_requested"]
        for item in strategy
    ] == [True, True]
    await support.teardown()
    await hub.teardown()


def test_co_stream_high_support_builds_active_acknowledgement():
    ctx = _FakeCtx(live_mode="co_stream")
    support = LiveSupportEventsModule()
    support.ctx = ctx
    event = ViewerEvent(
        uid="9",
        nickname="alice",
        danmaku_text="加油",
        source="live_danmaku",
        live_mode="co_stream",
        raw={
            "event_type": "super_chat",
            "support_verified": True,
        },
    )
    identity = ViewerIdentity(uid="9", nickname="alice")
    profile = ViewerProfile(uid="9", nickname="alice")

    request = support.build_request(event, identity, profile)

    assert request.metadata["support_event_type"] == "super_chat"
    assert "delivery_intent" not in request.metadata
    assert "candidate_ttl_seconds" not in request.metadata

    event.live_mode = "solo_stream"
    solo_request = support.build_request(event, identity, profile)
    assert "delivery_intent" not in solo_request.metadata


def test_support_prompt_includes_current_stream_theme():
    ctx = _FakeCtx(live_mode="co_stream")
    ctx.config.stream_theme = "play Strinova"
    support = LiveSupportEventsModule()
    support.ctx = ctx
    event = ViewerEvent(
        uid="9",
        nickname="alice",
        danmaku_text="Heart",
        source="live_danmaku",
        live_mode="co_stream",
        raw={
            "event_type": "gift",
            "gift_name": "Heart",
            "support_verified": True,
        },
    )

    request = support.build_request(
        event,
        ViewerIdentity(uid="9", nickname="alice"),
        ViewerProfile(uid="9", nickname="alice"),
    )

    assert "human_theme: play Strinova" in request.prompt_text
    assert "The first clause must explicitly thank" in request.prompt_text
    assert "Do not answer a recent danmaku" in request.prompt_text


async def test_idle_first_danmaku_roasts_immediately():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    hub.submit(_danmaku("42", text="初见"))
    assert hub._flush_task is None  # 即时分支，未开窗

    await _drain(hub)
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "初见"
    assert hub.recent_chat_snapshot(limit=1) == [
        {
            "seq": 1,
            "uid": "42",
            "nickname": "u42",
            "text": "初见",
            "seconds_ago": 0.0,
            "selected": True,
            "within_fresh_window": True,
        }
    ]


@pytest.mark.parametrize("raw_kind", ["dict", "provider"])
async def test_event_bus_preserves_envelope_session_generation(raw_kind: str):
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    raw_event = (
        {
            "uid": "42",
            "nickname": "viewer",
            "text": "please explain this live topic",
            "event_type": "danmaku",
        }
        if raw_kind == "dict"
        else _danmaku("42", text="please explain this live topic")
    )

    ctx.event_bus.publish(
        "danmaku",
        LiveEvent(
            type="danmaku",
            uid="42",
            raw=raw_event,
            session_generation=23,
        ),
    )
    await _drain(hub)

    assert ctx.payloads[0]["_live_session_generation"] == 23


async def test_low_value_danmaku_skips_reply_but_updates_room_context():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="666"))
    await _drain(hub)

    assert ctx.payloads == []
    assert hub._flush_task is None
    assert hub.status()["recent_danmaku_candidates"] == 1
    record = next(item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped")
    assert record["message"] == "selection.low_value_danmaku"
    assert "uid" not in record["detail"]
    assert record["detail"]["skip_reason"] == "selection.low_value_danmaku"
    assert hub.status()["reply_selection_policy"] == "selected"
    assert hub.status()["last_skip_reason"] == "selection.low_value_danmaku"
    assert "text" not in record["detail"]
    assert hub.recent_chat_snapshot(limit=1)[0]["selected"] is False


async def test_recent_chat_keeps_anonymous_received_danmaku_without_dispatching():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        {
            "event_type": "danmaku",
            "uid": "",
            "nickname": "路过观众",
            "text": "猫猫刚刚好可爱",
        }
    )
    await _drain(hub)

    assert ctx.payloads == []
    assert hub.recent_chat_snapshot(limit=1)[0] == {
        "seq": 1,
        "uid": "",
        "nickname": "路过观众",
        "text": "猫猫刚刚好可爱",
        "seconds_ago": 0.0,
        "selected": False,
        "within_fresh_window": True,
    }


def test_recent_chat_buffer_is_bounded_expires_and_marks_duplicate_exactly():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0], max_entries=2, window_seconds=30.0)

    first = buffer.remember(uid="1", nickname="同名", text="喵喵喵")
    second = buffer.remember(uid="1", nickname="同名", text="喵喵喵")
    buffer.remember(uid="2", nickname="后来者", text="最新一条")

    assert buffer.mark_selected(first) is False  # 已被容量上限淘汰。
    assert buffer.mark_selected(second) is True
    rows = buffer.snapshot(limit=5)
    assert [row["text"] for row in rows] == ["最新一条", "喵喵喵"]
    assert [row["selected"] for row in rows] == [False, True]

    clock[0] = 131.0
    assert buffer.snapshot(limit=5) == []
    tail = buffer.session_tail_snapshot(limit=5)
    assert [row["text"] for row in tail] == ["最新一条", "喵喵喵"]
    assert [row["within_fresh_window"] for row in tail] == [False, False]


def test_recent_chat_deduplicates_only_stable_delivery_ids():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0])

    first = buffer.remember(
        uid="1",
        nickname="viewer",
        text="same words",
        provider_event_id="event-1",
    )
    duplicate = buffer.remember(
        uid="1",
        nickname="viewer",
        text="same words",
        provider_event_id="event-1",
    )
    repeated_by_viewer = buffer.remember(
        uid="1",
        nickname="viewer",
        text="same words",
        provider_event_id="event-2",
    )

    assert first == 1
    assert duplicate == 0
    assert repeated_by_viewer == 2
    assert [row["text"] for row in buffer.session_tail_snapshot(limit=3)] == [
        "same words",
        "same words",
    ]
    assert buffer.status()["recent_chat_delivery_id_count"] == 2

    buffer.reset()
    assert buffer.remember(
        uid="1",
        nickname="viewer",
        text="new session",
        provider_event_id="event-1",
    ) == 1
    assert buffer.remember(
        uid="1",
        nickname="viewer",
        text="unsafe ids do not dedupe",
        provider_event_id="token.secret",
    ) == 2
    assert buffer.remember(
        uid="1",
        nickname="viewer",
        text="unsafe ids do not dedupe",
        provider_event_id="token.secret",
    ) == 3


@pytest.mark.asyncio
async def test_recent_chat_suppresses_duplicate_provider_delivery_before_reply():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    first = _danmaku("42", text="one delivery")
    first.provider_event_id = "event-42"
    duplicate = _danmaku("42", text="one delivery")
    duplicate.provider_event_id = "event-42"

    hub.submit(first)
    await _drain(hub)
    hub.submit(duplicate)
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert len(hub.recent_chat_snapshot(limit=3)) == 1
    assert hub.status()["recent_chat_duplicate_delivery_count"] == 1


def test_recent_chat_keeps_a_smaller_ambient_view_without_extending_latest_query():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0])
    buffer.remember(uid="1", nickname="viewer", text="a useful room joke")

    clock[0] = 131.0

    assert buffer.snapshot(limit=5) == []
    assert buffer.snapshot(
        limit=5,
        max_age_seconds=120.0,
        selected=False,
    )[0]["text"] == "a useful room joke"
    status = buffer.status()
    assert status["recent_chat_capacity"] == 12
    assert status["recent_chat_count"] == 0
    assert status["recent_chat_retained_count"] == 1
    assert status["recent_chat_unselected_count"] == 1
    assert status["recent_chat_session_tail_count"] == 1
    assert status["recent_chat_session_tail_capacity"] == 3


def test_recent_chat_session_tail_keeps_only_three_until_session_reset():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0])
    for index in range(5):
        buffer.remember(
            uid=str(index),
            nickname=f"viewer-{index}",
            text=f"message-{index}",
        )

    clock[0] = 1000.0
    rows = buffer.session_tail_snapshot(limit=99)

    assert [row["text"] for row in rows] == [
        "message-4",
        "message-3",
        "message-2",
    ]
    assert all(row["within_fresh_window"] is False for row in rows)
    assert buffer.ambient_snapshot(limit=5, max_age_seconds=120.0) == []

    buffer.reset()
    assert buffer.session_tail_snapshot(limit=3) == []


def test_recent_chat_hardens_text_time_and_clock_rollback_inputs():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0])

    assert buffer.remember(uid="1", nickname="viewer", text={"unsafe": True}) == 0
    seq = buffer.remember(
        uid={"unsafe": True},
        nickname=["unsafe"],
        text="  safe\nroom remark  ",
        observed_at=float("nan"),
    )
    clock[0] = 90.0

    assert buffer.snapshot(limit="invalid") == [
        {
            "seq": seq,
            "uid": "",
            "nickname": "",
            "text": "safe room remark",
            "seconds_ago": 0.0,
            "selected": False,
        }
    ]


def test_ambient_chat_collapses_duplicate_records_and_consumes_the_whole_repeat():
    clock = [100.0]
    buffer = RecentChatBuffer(now=lambda: clock[0])
    first = buffer.remember(uid="1", nickname="viewer", text="same room joke")
    second = buffer.remember(uid="1", nickname="viewer", text="same room joke")
    buffer.remember(uid="2", nickname="other", text="different room joke")

    assert [row["seq"] for row in buffer.ambient_snapshot(limit=5)] == [3, second]
    assert buffer.mark_ambient_used(second) is True
    assert [row["seq"] for row in buffer.ambient_snapshot(limit=5)] == [3]
    assert buffer.mark_ambient_used(first) is False


def test_recent_chat_relevance_ignores_query_filler_and_matches_local_topic():
    assert clean_relevance_query("刚才最新弹幕说了什么") == ""
    assert clean_relevance_query("token=must-not-leak") == ""
    assert relevance_score("零食", "今晚的小零食我选薯片") > 0
    assert relevance_score("键盘", "今晚的小零食我选薯片") == 0


async def test_ambient_chat_only_exposes_unselected_danmaku_at_low_pressure():
    ctx = _FakeCtx(remaining=0.0)
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="please answer this selected message"))
    await _drain(hub)
    hub.submit(_danmaku("2", text="666"))
    await _drain(hub)

    rows = hub.ambient_chat_snapshot(limit=3)

    assert [row["uid"] for row in rows] == ["2"]
    assert rows[0]["selected"] is False


async def test_ambient_chat_is_hidden_when_the_output_queue_is_under_pressure():
    ctx = _FakeCtx(remaining=0.0, queue_limit=5)
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)
    hub.submit(_danmaku("2", text="666"))
    await _drain(hub)
    ctx.safety_guard.queue_size = 4

    assert hub.ambient_chat_snapshot(limit=3) == []
    assert hub.status()["ambient_chat_suppressed_reason"] == "safety_queue_pressure"


async def test_ambient_chat_is_hidden_during_a_same_viewer_danmaku_burst():
    ctx = _FakeCtx(remaining=0.0)
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)
    for _ in range(5):
        hub.submit(_danmaku("2", text="666"))
    await _drain(hub)

    assert hub.ambient_chat_snapshot(limit=3) == []
    assert hub.status()["ambient_chat_suppressed_reason"] == "danmaku_burst"


async def test_relevant_chat_returns_one_local_match_and_reserves_it_once():
    ctx = _FakeCtx(remaining=0.0)
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)
    hub._recent_chat.remember(
        uid="1",
        nickname="viewer",
        text="今晚的小零食我选薯片",
    )
    hub._recent_chat.remember(
        uid="2",
        nickname="other",
        text="键盘声音像小雨",
    )

    rows = hub.relevant_chat_snapshot(query="零食", limit=5)

    assert len(rows) == 1
    assert rows[0]["uid"] == "1"
    assert hub.relevant_chat_snapshot(query="零食") == []
    status = hub.status()
    assert status["recent_chat_relevant_requests"] == 2
    assert status["recent_chat_relevant_hits"] == 1
    assert status["ambient_chat_used_count"] == 1
    assert status["ambient_chat_suppressed_reason"] == "no_relevant_match"


async def test_recent_chat_reset_discards_previous_live_session():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    hub.submit(_danmaku("42", text="上一场弹幕"))
    await _drain(hub)

    hub.reset()

    assert hub.recent_chat_snapshot(limit=5) == []
    assert hub.status()["recent_chat_count"] == 0
    assert hub.status()["recent_chat_query_requests"] == 1


async def test_meaningful_single_cjk_danmaku_follows_low_pressure_reply_gate():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="\u5c0f"))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "\u5c0f"
    assert not any(item["op"] == "live_event_reply_skipped" for item in ctx.audit.records)


async def test_single_cjk_danmaku_is_dropped_under_roast_like_pressure_gate():
    ctx = _FakeCtx(remaining=0.0, viewer_count=200)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="\u5c0f"))
    await _drain(hub)

    assert ctx.payloads == []
    record = next(item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped")
    assert record["detail"]["skip_reason"] == "selection.low_value_danmaku"


async def test_active_activity_still_skips_low_value_danmaku():
    ctx = _FakeCtx(remaining=0.0, activity_level="active")
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="666"))
    await _drain(hub)

    assert ctx.payloads == []
    record = next(item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped")
    assert record["detail"]["skip_reason"] == "selection.low_value_danmaku"
    assert hub.status()["reply_selection_policy"] == "selected"


async def test_active_hook_one_letter_answer_bypasses_low_value_filter():
    ctx = _FakeCtx(remaining=0.0)
    ctx.recent_results = [
        {
            "status": "pushed",
            "event": {
                "source": "active_engagement",
                "topic_shape": "either_or",
                "topic_reply_affordance": "viewer can answer with one side",
            },
        }
    ]
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="A"))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "A"
    assert not any(item["op"] == "live_event_reply_skipped" for item in ctx.audit.records)


async def test_active_hook_numeric_answer_bypasses_low_value_filter_from_exposed_metadata():
    ctx = _FakeCtx(remaining=0.0)
    ctx.recent_results = [
        {
            "status": "pushed",
            "event": {
                "source": "active_engagement",
            },
            "topic_shape": "micro_poll",
            "topic_reply_affordance": "viewer can answer with one character",
        }
    ]
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="1"))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "1"
    assert not any(item["op"] == "live_event_reply_skipped" for item in ctx.audit.records)


async def test_quiet_activity_skips_low_priority_plain_danmaku():
    ctx = _FakeCtx(remaining=0.0, activity_level="quiet")
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="plain"))
    await _drain(hub)

    assert ctx.payloads == []
    record = next(item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped")
    assert record["message"] == "selection.quiet_low_priority"
    assert record["detail"]["skip_reason"] == "selection.quiet_low_priority"
    assert hub.status()["reply_selection_policy"] == "quiet"
    assert hub.status()["last_skip_reason"] == "selection.quiet_low_priority"


async def test_quiet_activity_allows_question_danmaku():
    ctx = _FakeCtx(remaining=0.0, activity_level="quiet")
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="怎么配置？"))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "怎么配置？"


async def test_reply_queue_limit_drops_plain_danmaku_under_pressure():
    ctx = _FakeCtx(remaining=0.0, queue_limit=2)
    ctx.recent_results = [
        {
            "status": "pushed",
            "created_at": "age:10",
            "response_module": "danmaku_response",
            "event": {"source": "live_danmaku"},
        },
        {
            "status": "pushed",
            "created_at": "age:20",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku"},
        },
    ]
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="plain"))
    await _drain(hub)

    assert ctx.payloads == []
    record = next(item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped")
    assert record["message"] == "selection.queue_limit"
    assert record["detail"]["skip_reason"] == "selection.queue_limit"
    assert hub.status()["reply_queue_limit"] == 2
    assert hub.status()["reply_pressure_count"] == 2


async def test_reply_queue_limit_keeps_question_and_active_hook_answers():
    ctx = _FakeCtx(remaining=0.0, queue_limit=1)
    ctx.recent_results = [
        {
            "status": "pushed",
            "created_at": "age:10",
            "response_module": "danmaku_response",
            "event": {"source": "live_danmaku"},
        },
        {
            "status": "pushed",
            "event": {
                "source": "active_engagement",
                "topic_shape": "either_or",
                "topic_reply_affordance": "viewer can answer with one side",
            },
        },
    ]
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="what are we playing?"))
    await _drain(hub)
    hub.submit(_danmaku("43", text="A"))
    await _drain(hub)

    assert [payload["uid"] for payload in ctx.payloads] == ["42", "43"]
    assert not any(item["op"] == "live_event_reply_skipped" for item in ctx.audit.records)


async def test_new_viewer_burst_does_not_coerce_low_value_danmaku_into_batch_welcome():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    for uid in ("101", "102", "103", "104", "105"):
        hub.submit(_danmaku(uid, text="666"))
    await _drain(hub)

    assert ctx.payloads == []
    assert hub.new_viewer_burst_active()
    assert hub.batch_welcome_available()
    assert hub.new_viewer_burst_count() == 5


async def test_new_viewer_batch_welcome_has_cooldown():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    for uid in ("101", "102", "103", "104", "105"):
        hub.submit(_danmaku(uid, text="666"))
    await _drain(hub)
    assert ctx.payloads == []
    assert hub.batch_welcome_available()

    hub.reserve_batch_welcome()
    assert not hub.batch_welcome_available()

    hub.submit(_danmaku("106", text="666"))
    await _drain(hub)

    assert ctx.payloads == []
    skipped = [item for item in ctx.audit.records if item["op"] == "live_event_reply_skipped"]
    assert skipped[-1]["detail"]["skip_reason"] == "selection.low_value_danmaku"


async def test_guard_low_value_danmaku_still_roasts():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("42", text="666", guard=1))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "666"


async def test_provider_neutral_danmaku_event_roasts_without_bili_message_type():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="42",
            nickname="u42",
            text="hello",
            avatar_url="https://example.test/avatar.png",
            room_id=7,
            score=3,
        )
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["event_type"] == "danmaku"
    assert ctx.payloads[0]["room_id"] == 7


async def test_provider_neutral_dict_event_roasts_without_object_wrapper():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        {
            "type": "danmaku",
            "uid": "douyin:user_42",
            "nickname": "u42",
            "text": "hello from dict",
            "avatar_url": "https://example.test/avatar.png",
            "room_ref": "room-42",
            "room_id": 7,
            "score": 3,
        }
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "douyin:user_42"
    assert ctx.payloads[0]["event_type"] == "danmaku"
    assert ctx.payloads[0]["danmaku_text"] == "hello from dict"
    assert ctx.payloads[0]["room_ref"] == "room-42"


async def test_provider_neutral_dict_chat_alias_roasts_as_danmaku():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        {
            "type": "chat",
            "uid": "douyin:user_42",
            "nickname": "u42",
            "text": "hello alias",
            "score": 3,
        }
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["event_type"] == "danmaku"
    assert ctx.payloads[0]["danmaku_text"] == "hello alias"


def test_provider_event_type_normalizes_common_aliases():
    assert event_type({"type": "chat"}) == "danmaku"
    assert event_type({"event_type": "danmu"}) == "danmaku"
    assert event_type({"event_type": "superchat"}) == "super_chat"
    assert event_type({"raw": {"type": "sc"}}) == "super_chat"
    assert event_type({"event_type": "member"}) == "member"


def test_provider_event_type_does_not_stringify_explicit_object_fields():
    class _LooksLikeChat:
        def __str__(self) -> str:
            return "chat"

    assert event_type({"event_type": _LooksLikeChat()}) == "unknown"
    assert event_type({"raw": {"type": _LooksLikeChat()}}) == "unknown"
    assert event_type(SimpleNamespace(event_type=_LooksLikeChat())) == "unknown"
    assert event_type(SimpleNamespace(event_type=_LooksLikeChat(), msg_type=MessageType.MSG_GIFT)) == "gift"


def test_provider_event_room_ref_accepts_only_public_token_shape():
    assert event_room_ref(SimpleNamespace(room_ref="123456")) == "123456"
    assert event_room_ref(SimpleNamespace(room_ref=123456)) == "123456"
    assert event_room_ref(SimpleNamespace(room_ref="room-42")) == "room-42"
    assert event_room_ref(SimpleNamespace(room_ref="room:42")) == "room:42"

    assert event_room_ref(SimpleNamespace(room_ref="https://live.douyin.com/123456")) == ""
    assert event_room_ref(SimpleNamespace(room_ref="123456?token=must-not-leak")) == ""
    assert event_room_ref(SimpleNamespace(room_ref="123456#cookie=must-not-leak")) == ""
    assert event_room_ref(SimpleNamespace(room_ref="room-42 signature=must-not-leak")) == ""


def test_provider_event_room_ref_does_not_stringify_objects():
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "room-42"

    assert event_room_ref(SimpleNamespace(room_ref=_LooksLikeRoomRef())) == ""


def test_provider_event_numeric_fields_are_non_negative_and_finite():
    class _LooksLikeInt:
        def __int__(self) -> int:
            return 7

    class _LooksLikeFloat:
        def __float__(self) -> float:
            return 5.5

    assert event_room_id(SimpleNamespace(room_id="7")) == 7
    assert event_room_id(SimpleNamespace(room_id=7)) == 7
    assert event_room_id(SimpleNamespace(room_id="-1")) == 0
    assert event_room_id(SimpleNamespace(room_id=True)) == 0
    assert event_room_id(SimpleNamespace(room_id={"raw": 7})) == 0
    assert event_room_id(SimpleNamespace(room_id=_LooksLikeInt())) == 0

    assert event_guard_level(SimpleNamespace(guard_level="3")) == 3
    assert event_guard_level(SimpleNamespace(guard_level="-2")) == 0
    assert event_guard_level(SimpleNamespace(guard_level=_LooksLikeInt())) == 0

    assert event_score(SimpleNamespace(score="5.5")) == 5.5
    assert event_score(SimpleNamespace(score=5.5)) == 5.5
    assert event_score(SimpleNamespace(score="-5")) == 0.0
    assert event_score(SimpleNamespace(score=True)) == 0.0
    assert event_score(SimpleNamespace(score="nan")) == 0.0
    assert event_score(SimpleNamespace(score=float("inf"))) == 0.0
    assert event_score(SimpleNamespace(score=_LooksLikeFloat())) == 0.0
    assert event_score(SimpleNamespace(get_score=lambda: float("-inf"))) == 0.0
    assert event_score(SimpleNamespace(get_score=lambda: _LooksLikeFloat())) == 0.0


def test_provider_event_uid_accepts_only_public_token_shape():
    assert event_uid(SimpleNamespace(uid="42")) == "42"
    assert event_uid(SimpleNamespace(uid=42)) == "42"
    assert event_uid(SimpleNamespace(uid="douyin:user_42")) == "douyin:user_42"
    assert event_uid(SimpleNamespace(uid="bilibili:123456")) == "bilibili:123456"

    assert event_uid(SimpleNamespace(uid="https://live.douyin.com/user/42")) == ""
    assert event_uid(SimpleNamespace(uid="42?token=must-not-leak")) == ""
    assert event_uid(SimpleNamespace(uid="cookie=must-not-leak")) == ""


def test_provider_event_uid_does_not_stringify_objects():
    class _LooksLikeUid:
        def __str__(self) -> str:
            return "42"

    assert event_uid(SimpleNamespace(uid=_LooksLikeUid())) == ""


def test_provider_event_public_prompt_fields_redact_sensitive_fragments():
    event = SimpleNamespace(
        nickname="alice token=must-not-leak",
        text="how configure plugin signature=must-not-leak?",
    )

    assert event_nickname(event) == "alice [redacted]"
    assert event_prompt_text(event) == "how configure plugin [redacted]"


def test_provider_event_text_redacts_credentials_without_dropping_token_word():
    event = SimpleNamespace(text="talk about token budget; signature=must-not-leak")

    assert event_text(event) == "talk about token budget; [redacted]"


def test_provider_event_public_text_does_not_stringify_objects():
    class _LooksLikeText:
        def __str__(self) -> str:
            return "token=must-not-leak"

    event = SimpleNamespace(nickname=_LooksLikeText(), text=_LooksLikeText(), danmaku_text=_LooksLikeText())

    assert public_text(_LooksLikeText()) == ""
    assert event_nickname(event) == ""
    assert event_text(event) == ""
    assert event_prompt_text(event) == ""


def test_provider_event_text_bounds_pipeline_payload_length():
    event = SimpleNamespace(text="x" * 700)

    assert event_text(event) == "x" * 512


def test_provider_event_avatar_url_uses_public_url_projection():
    assert (
        event_avatar_url(SimpleNamespace(avatar_url="https://example.test/avatar.png?token=must-not-leak#signature=must-not-leak"))
        == "https://example.test/avatar.png"
    )
    assert event_avatar_url(SimpleNamespace(avatar_url="data:image/png;base64,must-not-leak")) == ""
    assert event_avatar_url(SimpleNamespace(avatar_url="http://localhost/avatar.png")) == ""
    assert event_avatar_url(SimpleNamespace(avatar_url="http://127.0.0.1/avatar.png")) == ""
    assert event_avatar_url(SimpleNamespace(avatar_url="https://user:pass@example.test/avatar.png")) == ""
    assert safe_public_url("https://example.test/avatar_token.png") == "https://example.test/avatar_token.png"


def test_provider_event_avatar_url_does_not_stringify_objects():
    class _LooksLikeUrl:
        def __str__(self) -> str:
            return "https://example.test/avatar.png"

    assert event_avatar_url(SimpleNamespace(avatar_url=_LooksLikeUrl())) == ""
    assert safe_public_url(_LooksLikeUrl()) == ""


def test_provider_event_signal_fields_redact_public_text():
    payload = event_signal_fields(
        SimpleNamespace(
            gift_name="gift signature=must-not-leak",
            gift_count=1,
            gift_value=2,
        )
    )

    assert payload["gift_name"] == "gift [redacted]"
    assert "must-not-leak" not in payload["gift_name"]
    assert payload["gift_count"] == 1
    assert payload["gift_value"] == 2

    auth_payload = event_signal_fields(SimpleNamespace(gift_name="Authorization: Bearer secret-token"))
    assert auth_payload["gift_name"] == "[redacted]"


def test_provider_event_signal_fields_drop_negative_or_invalid_numbers():
    class _LooksLikeInt:
        def __int__(self) -> int:
            return 9

    payload = event_signal_fields(
        {
            "type": "gift",
            "gift_name": "gift",
            "gift_count": "-1",
            "gift_value": {"raw": 2},
            "gift": {"num": 3, "price": 4, "total_coin": _LooksLikeInt()},
        }
    )

    assert payload == {"gift_name": "gift", "gift_count": 3, "gift_value": 4}

    object_payload = event_signal_fields(
        {
            "type": "gift",
            "gift_name": "gift",
            "gift_count": _LooksLikeInt(),
            "gift_value": _LooksLikeInt(),
        }
    )

    assert object_payload == {"gift_name": "gift"}


def test_provider_event_signal_fields_bounds_public_text_length():
    payload = event_signal_fields(SimpleNamespace(gift_name="x" * 120))

    assert payload["gift_name"] == "x" * 80


async def test_cooldown_window_picks_highest_score():
    ctx = _FakeCtx(remaining=5.0)  # 冷却中 -> 缓冲择优
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="路人甲"))             # guard 0
    hub.submit(_danmaku("2", text="总督驾到", guard=1))   # 总督 +3000
    hub.submit(_danmaku("3", text="8888"))              # guard 0
    assert hub._flush_task is not None
    assert hub._buffered_count == 2

    await _drain(hub)
    assert len(ctx.payloads) == 1            # 整窗只投 1 条
    assert ctx.payloads[0]["uid"] == "2"     # 分最高的总督胜出
    assert any(
        r["op"] == "live_event_selected" and r["detail"]["candidates"] == 2
        for r in ctx.audit.records
    )
    assert any(
        r["op"] == "live_event_reply_skipped"
        and r["detail"]["skip_reason"] == "selection.low_value_danmaku"
        for r in ctx.audit.records
    )


async def test_cooldown_window_prefers_explicit_viewer_hook_over_long_plain_text():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="这是一条很长但只是陈述的普通弹幕" * 8))
    hub.submit(_danmaku("2", text="猫猫觉得这个要怎么选？"))

    await _drain(hub)

    assert [payload["uid"] for payload in ctx.payloads] == ["2"]
    selected = next(r for r in ctx.audit.records if r["op"] == "live_event_selected")
    assert selected["detail"]["selected"]["score"] > 120


async def test_cooldown_window_bounds_candidate_summaries_without_losing_best():
    ctx = _FakeCtx(remaining=5.0, queue_limit=3)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="问题一怎么处理？"))
    hub.submit(_danmaku("2", text="总督的问题怎么处理？", guard=1))
    hub.submit(_danmaku("3", text="问题三怎么处理？"))
    hub.submit(_danmaku("4", text="问题四怎么处理？"))
    hub.submit(_danmaku("5", text="问题五怎么处理？"))

    assert hub._buffered_count == 5
    assert len(hub._candidate_summaries) == 3
    assert {item["order"] for item in hub._candidate_summaries} == {1, 2, 3}
    status = hub.status()
    assert status["buffered_candidate_summaries"] == 3
    assert status["buffered_candidate_summary_limit"] == 3

    await _drain(hub)

    assert [payload["uid"] for payload in ctx.payloads] == ["2"]
    selected = next(r for r in ctx.audit.records if r["op"] == "live_event_selected")
    assert selected["detail"]["candidates"] == 5
    assert selected["detail"]["selected"]["order"] == 2
    assert len(selected["detail"]["dropped_candidates"]) == 2


async def test_selection_audit_records_winner_and_dropped_candidates():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="plain"))
    hub.submit(_danmaku("2", text="captain", guard=1))
    hub.submit(_danmaku("3", text="short"))
    await _drain(hub)

    selected = next(r for r in ctx.audit.records if r["op"] == "live_event_selected")
    assert selected["detail"]["selected"]["order"] == 2
    assert selected["detail"]["selected"]["event_type"] == "danmaku"
    dropped = selected["detail"]["dropped_candidates"]
    assert [item["order"] for item in dropped] == [1, 3]
    assert {item["skip_reason"] for item in dropped} == {"selection.lower_score"}
    assert all("text" not in item for item in dropped)


async def test_selection_audit_omits_raw_viewer_identifiers():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="synthetic-private-viewer",
            nickname="synthetic",
            text="synthetic question?",
            score=3,
        )
    )
    await _drain(hub)

    selection_records = [
        record
        for record in ctx.audit.records
        if record["op"] in {"live_event_selected", "live_event_reply_skipped"}
    ]
    assert selection_records
    assert "synthetic-private-viewer" not in repr(selection_records)


async def test_unsafe_provider_uid_is_dropped_before_payload_or_audit():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(SimpleNamespace(event_type="danmaku", uid="42?token=must-not-leak", text="hello", score=3))
    await _drain(hub)

    assert ctx.payloads == []
    assert not any(record["op"] == "live_event_selected" for record in ctx.audit.records)


async def test_selection_status_tracks_last_decision_for_health_rows():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="plain"))
    hub.submit(_danmaku("2", text="captain", guard=1))
    await _drain(hub)

    status = hub.status()
    assert status["last_decision_at"] > 0
    assert status["last_selected_type"] == "danmaku"
    assert status["last_candidate_count"] == 2


async def test_support_gift_uses_priority_lane_without_stealing_danmaku_window():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="普通弹幕"))
    hub.submit(_gift("9", gift_name="醒目礼物", total_coin=200000))
    await _drain(hub)

    assert len(ctx.payloads) == 2
    payloads_by_type = {payload["event_type"]: payload for payload in ctx.payloads}
    assert payloads_by_type["gift"]["uid"] == "9"
    assert payloads_by_type["danmaku"]["uid"] == "1"
    selected = [r for r in ctx.audit.records if r["op"] == "live_event_selected"]
    selected_by_type = {item["detail"]["event_type"]: item for item in selected}
    assert "uid" not in selected_by_type["gift"]["detail"]["selected"]
    assert "uid" not in selected_by_type["danmaku"]["detail"]["selected"]


async def test_event_bus_routes_gift_events_to_support_lane_without_selection_window():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    gift = _gift("9", gift_name="醒目礼物", total_coin=200000)
    ctx.event_bus.publish(
        "gift",
        LiveEvent(
            type="gift",
            uid="9",
            payload={
                "support_verified": True,
                "support_evidence": "manual_live_simulation",
                "provider_event_type": "SEND_GIFT",
            },
            raw=gift,
        ),
    )
    await _drain(hub)
    await _drain_support(support)

    assert ctx.event_bus.subscriber_count("gift") == 1
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "9"
    assert ctx.payloads[0]["event_type"] == "gift"
    assert hub._flush_task is None
    assert support.status()["last_event_type"] == "gift"
    assert not [r for r in ctx.audit.records if r["op"] == "live_event_selected"]
    await support.teardown()


async def test_dict_gift_event_routes_as_safe_support_event():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        {
            "type": "gift",
            "uid": "douyin:user_9",
            "nickname": "u9",
            "gift": {
                "giftName": "gift signature=must-not-leak",
                "num": 2,
                "total_coin": 30,
            },
            "room_id": 1,
        }
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["event_type"] == "gift"
    assert ctx.payloads[0]["gift_name"] == "gift [redacted]"
    assert ctx.payloads[0]["gift_count"] == 2
    assert ctx.payloads[0]["gift_value"] == 30
    assert ctx.payloads[0]["gift_num"] == 2
    assert ctx.payloads[0]["gift_total_coin"] == 30
    assert any(r["op"] == "live_event_selected" for r in ctx.audit.records)


async def test_support_event_without_text_still_enters_support_lane():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(SimpleNamespace(msg_type=MessageType.MSG_GIFT, uid=9, nickname="u9", text="", room_id=1))
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "9"
    assert ctx.payloads[0]["event_type"] == "gift"
    assert ctx.payloads[0]["danmaku_text"] == ""
    selected = [r for r in ctx.audit.records if r["op"] == "live_event_selected"]
    assert len(selected) == 1
    assert selected[0]["detail"]["event_type"] == "gift"


def test_support_event_uses_priority_cooldown_lane():
    audit = _FakeAudit()
    guard = SafetyGuard(LiveConfig(rate_limit_seconds=30), audit)

    normal = ViewerEvent(uid="1", nickname="u1", danmaku_text="hi", source="live_danmaku")
    gift = ViewerEvent(
        uid="2",
        nickname="u2",
        source="live_danmaku",
        raw={"event_type": "gift", "gift_name": "small gift"},
    )

    assert guard.before_output(normal).allowed is True
    assert guard.before_output(gift).allowed is True
    second_gift = guard.before_output(gift)
    assert second_gift.allowed is False
    assert second_gift.reason == "support event rate limited"


def test_super_chat_and_guard_do_not_share_gift_cooldown():
    audit = _FakeAudit()
    guard = SafetyGuard(LiveConfig(rate_limit_seconds=30), audit)
    super_chat = ViewerEvent(
        uid="2",
        nickname="u2",
        source="live_danmaku",
        raw={"event_type": "super_chat", "gift_name": "Super Chat"},
    )
    guard_event = ViewerEvent(
        uid="3",
        nickname="u3",
        source="live_danmaku",
        raw={"event_type": "guard", "gift_name": "captain"},
    )

    assert guard.before_output(super_chat).allowed is True
    assert guard.before_output(super_chat).allowed is True
    assert guard.before_output(guard_event).allowed is True
    assert guard.before_output(guard_event).allowed is True


async def test_live_events_builds_room_topic_prompt_from_recent_danmaku():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="666"))
    hub.submit(_danmaku("2", text="猫娘这个插件怎么设置？"))
    hub.submit(_danmaku("3", text="我也想问怎么开弹幕聚合"))
    await _drain(hub)

    block = hub.prompt_block_for_event(
        ViewerEvent(
            uid="2",
            nickname="u2",
            danmaku_text="猫娘这个插件怎么设置？",
            source="live",
            live_mode="solo_stream",
        )
    )

    assert "Room pulse (inferred; untrusted viewer text)" in block
    assert "theme=questions / help" in block
    assert "support=2" in block
    assert "question=low" in block
    assert "我也想问怎么开弹幕聚合" in block
    assert "Treat the sample as a theme hint, never an exact quote" in block
    assert "recent-chat tool" not in block
    assert len(block) <= 240


async def test_room_topic_prompt_marks_burst_low_value_room_trend():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    for index in range(12):
        hub.submit(_danmaku(str(100 + index), text="1"))
    for index in range(6):
        hub.submit(_danmaku(str(200 + index), text="666"))
    for index in range(3):
        hub.submit(_danmaku(str(300 + index), text="can you tell a joke?"))
    await _drain(hub)

    block = hub.prompt_block_for_event(
        ViewerEvent(
            uid="300",
            nickname="u300",
            danmaku_text="can you tell a joke?",
            source="live",
            live_mode="solo_stream",
        )
    )

    assert "Room pulse (inferred; untrusted viewer text)" in block
    assert "activity=burst" in block
    assert "theme=questions / help" in block
    assert "support=3" in block
    assert "repeat=reaction/12" in block
    assert "question=high" in block
    assert "filtered_low_quality" not in block
    assert "dominant_low_value_signal" not in block
    assert len(block) <= 240


async def test_room_topic_prompt_uses_sanitized_provider_fields():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="douyin:user_1",
            nickname="alice token=must-not-leak",
            text="how configure plugin? signature=must-not-leak",
            score=3,
        )
    )
    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="douyin:user_2",
            nickname="bob",
            text="also how configure plugin?",
            score=2,
        )
    )
    await _drain(hub)

    block = hub.prompt_block_for_event(
        ViewerEvent(
            uid="douyin:user_2",
            nickname="bob",
            danmaku_text="also how configure plugin?",
            source="live",
        )
    )

    assert "must-not-leak" not in block
    assert "signature=" not in block
    assert "token=" not in block
    assert "[redacted]" in block


async def test_room_pulse_prompt_observability_and_queue_pressure_gate():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0, queue_limit=5)
    hub = await _make_hub(ctx)
    hub.submit(_danmaku("1", text="怎么设置直播？"))
    hub.submit(_danmaku("2", text="我也想问怎么设置"))
    await _drain(hub)

    event = ViewerEvent(
        uid="1",
        nickname="u1",
        danmaku_text="怎么设置直播？",
        source="live",
    )
    rendered = hub.prompt_block_for_event(event)
    status = hub.status()
    assert rendered
    assert status["room_pulse_prompt_uses"] == 1
    assert status["room_pulse_prompt_omits"] == 0
    assert status["room_pulse_prompt_last_chars"] == len(rendered)
    assert status["room_pulse_prompt_last_reason"] == "rendered"

    ctx.safety_guard.queue_size = 4
    assert hub.prompt_block_for_event(event) == ""
    pressured = hub.status()
    assert pressured["room_pulse_prompt_uses"] == 1
    assert pressured["room_pulse_prompt_omits"] == 1
    assert pressured["room_pulse_prompt_last_chars"] == 0
    assert pressured["room_pulse_prompt_last_reason"] == "safety_queue_pressure"

    hub.reset()
    reset = hub.status()
    assert reset["room_pulse_prompt_uses"] == 0
    assert reset["room_pulse_prompt_omits"] == 0
    assert reset["room_pulse_prompt_last_reason"] == ""


async def test_ritual_use_starts_only_when_block_survives_prompt_budget(monkeypatch):
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)
    event = ViewerEvent(
        uid="1",
        nickname="u1",
        danmaku_text="hello",
        source="live",
    )
    monkeypatch.setattr(
        hub._room_topic,
        "prompt_projection_for_event",
        lambda _event: SimpleNamespace(text="", reason="empty"),
    )
    monkeypatch.setattr(
        hub._scene_state,
        "prompt_for_event",
        lambda _event: SimpleNamespace(text=""),
    )
    ritual = RitualPrompt(text="[room ritual] callback\n", reason="offered", key="bit")
    monkeypatch.setattr(
        hub,
        "_ritual_offer_for_event",
        lambda _event: (ritual, "new_context"),
    )
    uses: list[tuple[str, str]] = []
    monkeypatch.setattr(
        hub._ritual_memory,
        "mark_used",
        lambda key, context: uses.append((key, context)),
    )

    oversized_verdict = "v" * (LIVE_CONTEXT_PROMPT_MAX_CHARS - 1)
    monkeypatch.setattr(
        hub,
        "_verdict_block_for_event",
        lambda _event: oversized_verdict,
    )
    assert hub.prompt_block_for_event(event) == oversized_verdict
    assert uses == []

    monkeypatch.setattr(hub, "_verdict_block_for_event", lambda _event: "")
    assert hub.prompt_block_for_event(event) == ritual.text
    assert uses == [("bit", "new_context")]


async def test_live_events_scene_state_observes_successful_solo_results():
    ctx = _FakeCtx()
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)
    ctx.event_bus.emit(
        "result",
        {
            "status": "pushed",
            "trace_id": "active-1",
            "created_at": "created:active-1",
            "event": {
                "source": "active_engagement",
                "live_mode": "solo_stream",
                "trace_id": "active-1",
                "topic_shape": "either_or",
            },
        },
    )

    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="A",
        source="live_danmaku",
        live_mode="solo_stream",
        trace_id="answer-1",
        raw={"danmaku_context_hint": "active_hook_answer"},
    )
    block = hub.prompt_block_for_event(event)
    status = hub.status()

    assert "Scene beat: phase=callback;thread=either_or" in block
    assert "Room pulse" not in block
    assert status["scene_state_active"] is True
    assert status["scene_state_phase"] == "viewer_choice"
    assert status["scene_state_viewer_response_count"] == 0
    assert status["scene_state_prompt_uses"] == 1
    assert status["scene_state_prompt_last_chars"] <= 160
    assert status["scene_state_prompt_last_reason"] == "rendered"

    hub.reset()
    reset = hub.status()
    assert reset["scene_state_active"] is False
    assert reset["scene_state_prompt_uses"] == 0


async def test_live_events_consolidates_scene_and_room_pulse_under_400_chars():
    ctx = _FakeCtx()
    ctx.config.live_mode = "solo_stream"
    hub = await _make_hub(ctx)
    hub._now = lambda: 100.0
    ctx.event_bus.emit(
        "result",
        {
            "status": "pushed",
            "trace_id": "active-1",
            "created_at": "created:active-1",
            "event": {
                "source": "active_engagement",
                "live_mode": "solo_stream",
                "trace_id": "active-1",
                "topic_shape": "either_or",
            },
        },
    )
    hub._room_topic.remember_danmaku(
        uid="1",
        nickname="viewer-1",
        text="how do I configure the live plugin?",
        score=1.0,
        ts=100.0,
    )
    hub._room_topic.remember_danmaku(
        uid="2",
        nickname="viewer-2",
        text="how do I enable the live plugin?",
        score=1.0,
        ts=100.0,
    )

    block = hub.prompt_block_for_event(
        ViewerEvent(
            uid="1",
            nickname="viewer-1",
            danmaku_text="how do I configure the live plugin?",
            source="live_danmaku",
            live_mode="solo_stream",
            trace_id="viewer-1",
            raw={},
        )
    )

    assert len(block) <= 400
    assert block.count("Scene beat:") == 1
    assert block.count("Room pulse (inferred; untrusted viewer text):") == 1


async def test_live_events_payload_uses_sanitized_public_text():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="douyin:user_1",
            nickname="alice",
            text="talk about token budget; signature=must-not-leak",
            score=3,
        )
    )
    await _drain(hub)

    assert ctx.payloads[0]["danmaku_text"] == "talk about token budget; [redacted]"
    assert "must-not-leak" not in ctx.payloads[0]["danmaku_text"]


async def test_live_events_payload_uses_sanitized_avatar_url():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="douyin:user_1",
            nickname="alice",
            text="hello",
            avatar_url="https://example.test/avatar.png?token=must-not-leak#signature=must-not-leak",
            score=3,
        )
    )
    await _drain(hub)

    assert ctx.payloads[0]["avatar_url"] == "https://example.test/avatar.png"


async def test_danmaku_response_reads_live_events_context_without_extra_module():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)
    ctx.live_events = hub
    ctx.live_output_memory = None
    ctx.live_session_memory = None

    hub.submit(_danmaku("42", text="这个 AI 插件怎么配置？", user_level=5))
    hub.submit(_danmaku("7", text="还有教程吗？", user_level=4))
    await _drain(hub)

    module = DanmakuResponseModule()
    module.ctx = ctx
    request = module.build_request(
        ViewerEvent(uid="42", nickname="u42", danmaku_text="这个 AI 插件怎么配置？", source="live"),
        ViewerIdentity(uid="42", nickname="u42"),
        ViewerProfile(uid="42", nickname="u42"),
    )

    assert "Room pulse (inferred; untrusted viewer text)" in request.prompt_text
    assert "theme=questions / help" in request.prompt_text
    assert "Recent room danmaku context" not in request.prompt_text


async def test_danmaku_response_marks_new_viewer_batch_welcome_as_group_reply():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    module = DanmakuResponseModule()
    module.ctx = ctx
    event = ViewerEvent(
        uid="105",
        nickname="u105",
        danmaku_text="直播间一下热闹起来了，需要自然和冒泡的大家打个招呼。",
        source="live_danmaku",
        raw={"live_batch_welcome": "new_viewer_burst", "suppress_avatar_roast": True},
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="105", nickname="u105"),
        ViewerProfile(uid="105", nickname="u105"),
    )

    assert request.metadata["danmaku_profile"] == "batch_welcome"
    assert request.metadata["danmaku_reply_target"] == "surfaced_viewers_as_group"
    assert "danmaku_profile: batch_welcome" in request.prompt_text
    assert "busy-room batch welcome replacing a single viewer avatar/ID roast" in request.prompt_text
    assert "do not reuse a fixed welcome phrase" in request.prompt_text
    assert "Do not name, roast, rank, or describe any individual viewer." in request.prompt_text


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


def test_room_topic_accepts_dict_candidates_with_public_nickname():
    topic = RoomTopicContext(now=lambda: 1.0)

    context = topic._build_context(
        [{"uid": "42", "nickname": "alice", "text": "怎么配置？", "score": 3.0}]
    )

    assert context["total_candidates"] == 1
    assert context["themes"][0]["examples"][0]["nickname"] == "alice"


@pytest.mark.asyncio
async def test_rawless_live_event_reads_danmaku_fields_from_payload():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)

    hub.submit(
        LiveEvent(
            type="danmaku",
            uid="42",
            payload={"nickname": "alice", "text": "hello envelope", "room_id": 7},
        )
    )
    await _drain(hub)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "hello envelope"
    assert ctx.payloads[0]["room_id"] == 7


@pytest.mark.asyncio
async def test_rawless_live_event_reads_support_fields_from_payload():
    ctx = _FakeCtx(remaining=0.0)
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    ctx.event_bus.publish(
        "gift",
        LiveEvent(
            type="gift",
            uid="9",
            payload={
                "nickname": "alice",
                "gift_name": "小心心",
                "gift_count": 2,
                "gift_value": 30,
                "support_verified": True,
                "support_evidence": "manual_live_simulation",
                "provider_event_type": "SEND_GIFT",
            },
        ),
    )
    await _drain_support(support)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["event_type"] == "gift"
    assert ctx.payloads[0]["gift_name"] == "小心心"
    assert ctx.payloads[0]["gift_count"] == 2
    assert ctx.payloads[0]["gift_value"] == 30
    await support.teardown()


@pytest.mark.asyncio
async def test_typed_support_envelope_keeps_type_when_raw_has_no_type():
    ctx = _FakeCtx(remaining=0.0)
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    ctx.event_bus.publish(
        "gift",
        LiveEvent(
            type="gift",
            uid="9",
            payload={
                "nickname": "alice",
                "gift_name": "小鱼干",
                "gift_count": 2,
                "support_verified": True,
                "support_evidence": "manual_live_simulation",
                "provider_event_type": "SEND_GIFT",
            },
            raw=SimpleNamespace(uid="9", nickname="alice", text="", room_id=1),
        ),
    )
    await _drain_support(support)

    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["event_type"] == "gift"
    assert ctx.payloads[0]["gift_name"] == "小鱼干"
    assert ctx.payloads[0]["gift_count"] == 2
    await support.teardown()


@pytest.mark.asyncio
async def test_unverified_support_event_fails_closed():
    ctx = _FakeCtx(remaining=0.0)
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    ctx.event_bus.publish(
        "gift",
        LiveEvent(
            type="gift",
            uid="9",
            payload={"nickname": "alice", "gift_name": "claimed gift", "gift_count": 999},
        ),
    )
    await _drain_support(support)

    assert ctx.payloads == []
    await support.teardown()


@pytest.mark.asyncio
async def test_support_module_dispatches_pending_high_value_event_before_light_event():
    ctx = _FakeCtx(remaining=0.0)
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def handle(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    ctx.handle_live_payload = handle
    support = LiveSupportEventsModule()
    await support.setup(ctx)

    def publish(event_id: str, value: int, coin_type: str) -> None:
        ctx.event_bus.publish(
            "gift",
            LiveEvent(
                type="gift",
                uid="9",
                payload={
                    "gift_name": "Heart",
                    "gift_value": value,
                    "coin_type": coin_type,
                    "support_verified": True,
                    "support_evidence": "manual_live_simulation",
                    "provider_event_id": event_id,
                    "provider_event_type": "SEND_GIFT",
                },
            ),
        )

    publish("active", 0, "silver")
    await first_started.wait()
    publish("light", 0, "silver")
    publish("high", 10_000, "gold")
    release_first.set()
    await _drain_support(support)

    assert dispatched == ["active", "high", "light"]
    await support.teardown()

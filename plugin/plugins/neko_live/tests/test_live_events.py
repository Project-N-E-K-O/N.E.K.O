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

from plugin.plugins.neko_roast.core.contracts import (
    LiveEvent,
    RoastConfig,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.core.event_bus import EventBus
from plugin.plugins.neko_roast.core.safety_guard import SafetyGuard
from plugin.plugins.neko_roast.modules.bili_live_ingest.livedanmaku import LiveDanmaku, MessageType
from plugin.plugins.neko_roast.modules.danmaku_response import DanmakuResponseModule
from plugin.plugins.neko_roast.modules.live_events import LiveEventsModule
from plugin.plugins.neko_roast.modules.live_events.room_topic import RoomTopicContext
from plugin.plugins.neko_roast.modules.live_support_events import LiveSupportEventsModule
from plugin.plugins.neko_roast.modules.live_events.provider_event import (
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
    ) -> None:
        self.safety_guard = _FakeSafety(remaining)
        self.audit = _FakeAudit()
        self.event_bus = EventBus(self.audit)
        self.live_provider = SimpleNamespace(listener_state=lambda: {"viewer_count": viewer_count})
        self.config = RoastConfig(
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


async def test_idle_first_danmaku_roasts_immediately():
    ctx = _FakeCtx(remaining=0.0)
    hub = await _make_hub(ctx)
    hub.submit(_danmaku("42", text="初见"))
    assert hub._flush_task is None  # 即时分支，未开窗

    await _drain(hub)
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "42"
    assert ctx.payloads[0]["danmaku_text"] == "初见"


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
    assert record["detail"]["uid"] == "42"
    assert record["detail"]["skip_reason"] == "selection.low_value_danmaku"
    assert hub.status()["reply_selection_policy"] == "selected"
    assert hub.status()["last_skip_reason"] == "selection.low_value_danmaku"
    assert "text" not in record["detail"]


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
        and r["detail"]["uid"] == "3"
        and r["detail"]["skip_reason"] == "selection.low_value_danmaku"
        for r in ctx.audit.records
    )


async def test_selection_audit_records_winner_and_dropped_candidates():
    ctx = _FakeCtx(remaining=5.0)
    hub = await _make_hub(ctx)

    hub.submit(_danmaku("1", text="plain"))
    hub.submit(_danmaku("2", text="captain", guard=1))
    hub.submit(_danmaku("3", text="short"))
    await _drain(hub)

    selected = next(r for r in ctx.audit.records if r["op"] == "live_event_selected")
    assert selected["detail"]["selected"]["uid"] == "2"
    assert selected["detail"]["selected"]["event_type"] == "danmaku"
    dropped = selected["detail"]["dropped_candidates"]
    assert [item["uid"] for item in dropped] == ["1", "3"]
    assert {item["skip_reason"] for item in dropped} == {"selection.lower_score"}
    assert all("text" not in item for item in dropped)


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
    assert selected_by_type["gift"]["detail"]["selected"]["uid"] == "9"
    assert selected_by_type["danmaku"]["detail"]["selected"]["uid"] == "1"


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
    guard = SafetyGuard(RoastConfig(rate_limit_seconds=30), audit)

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
    guard = SafetyGuard(RoastConfig(rate_limit_seconds=30), audit)
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

    assert "Live event room-topic context" in block
    assert "filtered_low_quality: 1" in block
    assert "theme: questions / help" in block
    assert "reply to the room theme instead of one-by-one" in block
    assert "reply_tactic: answer_then_hook" in block
    assert "likes tech/AI" in block


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

    assert "Live event room-topic context" in block
    assert "observed_candidates:" in block
    assert "filtered_low_quality: 18" in block
    assert "burst_mode: true" in block
    assert "dominant_low_value_signal: 1 (12 messages)" in block
    assert "dominant_low_value_signal: 666 (6 messages)" in block
    assert "burst_reply_rule: answer the selected representative message" in block
    assert "theme: questions / help" in block


async def test_room_topic_prompt_uses_sanitized_provider_fields():
    ctx = _FakeCtx(remaining=0.0, rate_limit=0)
    hub = await _make_hub(ctx)

    hub.submit(
        SimpleNamespace(
            event_type="danmaku",
            uid="douyin:user_1",
            nickname="alice token=must-not-leak",
            text="how configure plugin signature=must-not-leak?",
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

    assert "Live event room-topic context" in request.prompt_text
    assert "theme: questions / help" in request.prompt_text


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

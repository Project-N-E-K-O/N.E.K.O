"""LiveEvent 中枢：provider-neutral 富模型事件的窗口择优消费者（P2.5 slice 1）。

职责（做什么）：
- 订阅 live provider 发布到 ``EventBus`` 的富模型直播事件，统一通过 ``provider_event``
  helpers 读取 UID、文本、房间、事件类型和打分。
- 爆量房间冷却期内**缓冲**候选弹幕、按 ``get_score()`` 打分，冷却结束**择优**（粉丝牌、
  用户等级、长文本优先）取分最高者投 ``pipeline``；空闲态首条弹幕**即时**锐评
  （保留已真机验证的「首评观众即开口」DoD）。
- 把限流从「冷却期 skip 掉所有人、冷却后第一个到达即选中」升级为「冷却期缓冲、到点择优」。
  每个窗口只有 1 条进 pipeline，顺带缓解 ``queue_limit`` 溢出。

不做什么（当前边界）：
- 只处理普通弹幕。礼物/SC/上舰由 ``live_support_events`` 的独立有界调度器处理，
  不与普通弹幕争用本窗口；进场等事件仍交给各自 handler。
- 普通弹幕里的“假礼物”仍由 danmaku_response 侧识别为未验证 claim。
- 不生成最终开口 prompt、不直接调 ``push_message`` / ``store.set``：胜者经 ``handle_live_payload``
  走既有 ``normalize -> pipeline -> safety_guard -> avatar_roast -> dispatcher`` 全链路；
  房间主题只作为 advisory prompt context 供下游 prompt builder 使用，
  四条不变量（唯一出口 / 唯一档案写入 / 唯一审计 / 安全门必经）原样保持。

数据流：``provider event -> EventBus -> submit() -> (即时 | 开窗择优) -> handle_live_payload()``。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .._base import BaseModule
from ...core.active_hook_answers import is_active_hook_answer_event
from ...core.contracts import ViewerEvent
from ...core.runtime_timeline import record_payload_timeline
from .provider_event import (
    event_avatar_url,
    event_guard_level,
    event_nickname,
    event_room_id,
    event_room_ref,
    event_score,
    event_session_generation,
    event_signal_fields,
    event_text,
    event_type,
    event_uid,
    is_routable,
    is_signal_only,
)
from .room_topic import RoomTopicContext


LOW_REPLY_VALUE_SCORE_BYPASS = 1000.0
QUIET_REPLY_SCORE_BYPASS = 80.0
LIVE_REPLY_PRESSURE_WINDOW_SECONDS = 60.0
LIVE_REPLY_QUEUE_LIMIT_FLOOR = 1
NEW_VIEWER_BURST_WINDOW_SECONDS = 45.0
NEW_VIEWER_BURST_UNIQUE_THRESHOLD = 5
NEW_VIEWER_BATCH_WELCOME_COOLDOWN_SECONDS = 90.0
SINGLE_CHAR_REPLY_VIEWER_COUNT_LIMIT = 200
_SINGLE_CHAR_REACTION_TOKENS = {
    "哈",
    "草",
    "啊",
    "嗯",
    "哦",
    "额",
    "呃",
    "喵",
}
REPLY_WORTHY_TEXT_MARKERS = (
    "?",
    "？",
    "吗",
    "呢",
    "怎么",
    "为什么",
    "如何",
    "请问",
    "有没有",
    "能不能",
    "可以吗",
    "讲讲",
    "说说",
    "笑话",
    "解释",
    "展开",
    "起外号",
    "你好",
    "晚上好",
)
REPLY_WORTHY_TEXT_WORDS = {"hello", "hi"}


class LiveEventsModule(BaseModule):
    """直播事件中枢。``submit()`` 是富模型事件入口，同步、非阻塞（只缓冲/打分，pipeline
    在后台 task 里跑，不拖慢弹幕接收循环）。"""

    id = "live_events"
    title = "直播事件"

    def __init__(self) -> None:
        super().__init__()
        self._best: Any = None
        self._best_score: float = 0.0
        self._best_order: int = 0
        self._buffered_count: int = 0
        self._candidate_summaries: list[dict[str, Any]] = []
        self._flush_task: "asyncio.Task[Any] | None" = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # 中枢本地「刚投递」时间戳：同步更新，确保紧接着到的事件不会因 safety_guard 的
        # _last_output_at 尚未被 before_output 写入而误走即时分支造成并发双锐评。
        self._last_dispatch_at: float = 0.0
        # 可注入：单测里替换成确定性的 sleep / 时钟。
        self._sleep = asyncio.sleep
        self._now = time.time
        self._last_decision_at: float = 0.0
        self._last_selected_type: str = ""
        self._last_candidate_count: int = 0
        self._last_skip_reason: str = ""
        self._recent_viewer_uids: dict[str, float] = {}
        self._last_new_viewer_batch_welcome_at: float = 0.0
        # EventBus 订阅句柄（fake ctx 无 event_bus 时保持空列表）。
        self._unsubscribes: list[Any] = []
        self._room_topic = RoomTopicContext(now=lambda: self._now())

    async def setup(self, ctx: Any) -> None:
        """注册到 ``EventBus`` 的高价值互动事件。中枢负责同一冷却窗口内的择优；其它
        事件族 handler 仍照此在自己 setup 里 ``bus.subscribe(type, ...)``，零碰接入层。"""
        await super().setup(ctx)
        bus = getattr(ctx, "event_bus", None)
        if bus is not None:
            for event_type in ("danmaku",):
                self._unsubscribes.append(bus.subscribe(event_type, self._on_bus_event, owner=self.id))

    def _on_bus_event(self, event: Any) -> None:
        """EventBus 订阅回调：解包信封取富模型，复用既有窗口择优 ``submit()``（签名不变）。"""
        raw = getattr(event, "raw", None)
        if raw is not None:
            self.submit(raw)
        else:
            self.submit(event)

    async def teardown(self) -> None:
        for unsubscribe in self._unsubscribes:
            if callable(unsubscribe):
                unsubscribe()
        self._unsubscribes = []
        self.reset()
        pending = [task for task in list(self._tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        await super().teardown()

    def _clear_window(self) -> None:
        self._flush_task = None
        self._best = None
        self._best_score = 0.0
        self._best_order = 0
        self._buffered_count = 0
        self._candidate_summaries = []

    def _track_flush_task(self, task: "asyncio.Task[Any]") -> "asyncio.Task[Any]":
        self._flush_task = task

        def _clear_if_current(done_task: "asyncio.Task[Any]") -> None:
            if self._flush_task is done_task:
                self._flush_task = None

        task.add_done_callback(_clear_if_current)
        return task

    def reset(self) -> None:
        """清空缓冲并取消待触发的窗口。断开直播间时调用，避免迟到的择优在断开后误投。"""
        flush_task = self._flush_task
        if flush_task is not None and not flush_task.done():
            flush_task.cancel()
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._clear_window()
        self._last_dispatch_at = 0.0
        self._last_decision_at = 0.0
        self._last_selected_type = ""
        self._last_candidate_count = 0
        self._last_skip_reason = ""
        self._recent_viewer_uids = {}
        self._last_new_viewer_batch_welcome_at = 0.0
        self._room_topic.reset()

    def status(self) -> dict[str, Any]:
        status = {
            "enabled": self.enabled,
            "buffered": self._buffered_count,
            "window_open": self._flush_task is not None,
            "last_decision_at": self._last_decision_at,
            "last_selected_type": self._last_selected_type,
            "last_candidate_count": self._last_candidate_count,
            "last_skip_reason": self._last_skip_reason,
            "reply_selection_policy": self._reply_selection_policy(),
            "reply_queue_limit": self._reply_queue_limit(),
            "reply_pressure_count": self._recent_live_reply_count(),
            "new_viewer_burst_count": self._recent_viewer_count(),
        }
        status.update(self._room_topic.status())
        return status

    def submit(self, event: Any) -> None:
        """富模型直播事件入口（由 live provider 事件或 EventBus 回调驱动）。"""
        if not self.enabled or self.ctx is None:
            return
        if not is_routable(event):
            return  # 进场等事件留给各自 P3 handler；无 handler 类型保持静默。
        uid = event_uid(event)
        if not uid or uid == "0":
            return  # 无 uid，无从记录 / 锐评
        if is_signal_only(event):
            score = self._safe_score(event)
            self._mark_dispatch()
            self._spawn(
                self._roast(
                    event,
                    count=1,
                    candidates=[self._candidate_summary(event, score, 1)],
                    winner_order=1,
                )
            )
            return
        text = event_text(event)
        if not text:
            return  # 无文本，无从锐评
        self._remember_recent_viewer(uid)
        score = self._safe_score(event)
        self._room_topic.remember_live_event(event, score=score)
        skip_reason = self._reply_skip_reason(event, text=text, score=score)
        if skip_reason:
            self._record_reply_skip(
                event,
                reason=skip_reason,
                score=score,
            )
            return
        remaining = self._cooldown_remaining()
        if remaining <= 0 and self._flush_task is None:
            # 空闲态：首条即时锐评，保留已验证 DoD。
            self._mark_dispatch()
            self._spawn(self._roast(event, count=1, candidates=[self._candidate_summary(event, score, 1)], winner_order=1))
            return
        # 冷却期：缓冲择优，只保留当前分最高者（O(1) 内存，无需保留整批）。
        order = self._buffered_count + 1
        self._candidate_summaries.append(self._candidate_summary(event, score, order))
        if self._best is None or score > self._best_score:
            self._best = event
            self._best_score = score
            self._best_order = order
        self._buffered_count += 1
        if self._flush_task is None:
            self._track_flush_task(self._spawn(self._flush_after(remaining)))

    def _cooldown_remaining(self) -> float:
        """到下一次允许投递还剩多少秒：取安全门限流冷却与中枢本地冷却的较大值。"""
        try:
            sg = float(self.ctx.safety_guard.output_cooldown_remaining())
        except Exception:
            sg = 0.0
        rate = int(getattr(self.ctx.config, "rate_limit_seconds", 0) or 0)
        local = 0.0
        if rate > 0:
            local = rate - (self._now() - self._last_dispatch_at)
            if local < 0:
                local = 0.0
        return sg if sg > local else local

    def _mark_dispatch(self) -> None:
        self._last_dispatch_at = self._now()

    @staticmethod
    def _safe_score(event: Any) -> float:
        return event_score(event)

    def _reply_selection_policy(self) -> str:
        activity_level = getattr(getattr(self.ctx, "config", None), "activity_level", "standard")
        return "quiet" if activity_level == "quiet" else "selected"

    def _reply_skip_reason(self, event: Any, *, text: str, score: float) -> str:
        policy = self._reply_selection_policy()
        if event_type(event) != "danmaku":
            return ""
        if event_guard_level(event) > 0:
            return ""
        if float(score or 0.0) >= LOW_REPLY_VALUE_SCORE_BYPASS:
            return ""
        if self._looks_like_active_hook_answer(event, text=text):
            return ""
        if self._room_topic.is_low_reply_value(text) and not self._single_char_reply_allowed(
            event,
            text=text,
        ):
            return "selection.low_value_danmaku"
        if self._reply_queue_full() and float(score or 0.0) < LOW_REPLY_VALUE_SCORE_BYPASS:
            if not _looks_reply_worthy_text(text):
                return "selection.queue_limit"
        if policy == "quiet" and float(score or 0.0) < QUIET_REPLY_SCORE_BYPASS and not _looks_reply_worthy_text(text):
            return "selection.quiet_low_priority"
        return ""

    def _reply_queue_limit(self) -> int:
        config = getattr(self.ctx, "config", None)
        raw_limit = getattr(config, "queue_limit", 0)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 0
        return max(LIVE_REPLY_QUEUE_LIMIT_FLOOR, limit)

    def _reply_queue_full(self) -> bool:
        return self._buffered_count + self._recent_live_reply_count() >= self._reply_queue_limit()

    def _single_char_reply_allowed(self, event: Any, *, text: str) -> bool:
        if event_type(event) != "danmaku":
            return False
        dense = self._room_topic._dense_text(text)
        if len(dense) != 1:
            return False
        char = dense[0]
        if not ("\u4e00" <= char <= "\u9fff"):
            return False
        if char in _SINGLE_CHAR_REACTION_TOKENS:
            return False
        if self._new_viewer_burst_active():
            return False
        if self._live_viewer_count() >= SINGLE_CHAR_REPLY_VIEWER_COUNT_LIMIT:
            return False
        if self._safety_queue_near_limit():
            return False
        if self._reply_queue_full():
            return False
        return True

    def _safety_queue_near_limit(self) -> bool:
        if self.ctx is None:
            return False
        guard = getattr(self.ctx, "safety_guard", None)
        config = getattr(self.ctx, "config", None)
        try:
            queue_size = int(getattr(guard, "queue_size", 0) or 0)
            queue_limit = int(getattr(config, "queue_limit", 0) or 0)
        except (TypeError, ValueError):
            return False
        if queue_limit <= 0:
            return False
        return queue_size >= max(1, queue_limit - 1)

    def _live_viewer_count(self) -> int:
        if self.ctx is None:
            return 0
        provider = getattr(self.ctx, "live_provider", None)
        state = {}
        listener_state = getattr(provider, "listener_state", None)
        if callable(listener_state):
            try:
                state = listener_state()
            except Exception:
                state = {}
        value = state.get("viewer_count") if isinstance(state, dict) else 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _recent_live_reply_count(self) -> int:
        if self.ctx is None:
            return 0
        recent_results = getattr(self.ctx, "recent_results", []) or []
        count = 0
        for result in reversed(list(recent_results)):
            if not isinstance(result, dict):
                continue
            age = self._recent_result_age_sec(result)
            if age is not None and age > LIVE_REPLY_PRESSURE_WINDOW_SECONDS:
                break
            status = str(result.get("status") or "")
            if status not in {"pushed", "dry_run"}:
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            source = str(event.get("source") or "")
            if source != "live_danmaku":
                continue
            response_module = str(result.get("response_module") or "")
            if response_module and response_module not in {"danmaku_response", "avatar_roast"}:
                continue
            count += 1
        return count

    def _remember_recent_viewer(self, uid: str) -> None:
        now = self._now()
        cutoff = now - NEW_VIEWER_BURST_WINDOW_SECONDS
        self._recent_viewer_uids = {
            key: ts for key, ts in self._recent_viewer_uids.items() if ts >= cutoff
        }
        if uid:
            self._recent_viewer_uids[str(uid)] = now

    def _recent_viewer_count(self) -> int:
        now = self._now()
        cutoff = now - NEW_VIEWER_BURST_WINDOW_SECONDS
        return sum(1 for ts in self._recent_viewer_uids.values() if ts >= cutoff)

    def _new_viewer_burst_active(self) -> bool:
        return self._recent_viewer_count() >= NEW_VIEWER_BURST_UNIQUE_THRESHOLD

    def new_viewer_burst_active(self) -> bool:
        return self._new_viewer_burst_active()

    def new_viewer_burst_count(self) -> int:
        return self._recent_viewer_count()

    def batch_welcome_available(self) -> bool:
        if not self._new_viewer_burst_active():
            return False
        return (self._now() - self._last_new_viewer_batch_welcome_at) >= NEW_VIEWER_BATCH_WELCOME_COOLDOWN_SECONDS

    def reserve_batch_welcome(self) -> None:
        self._last_new_viewer_batch_welcome_at = self._now()

    def _recent_result_age_sec(self, result: dict[str, Any]) -> float | None:
        created_at = result.get("created_at")
        if not created_at:
            return None
        age_fn = getattr(self.ctx, "_iso_age_sec", None)
        if not callable(age_fn):
            return None
        try:
            age = age_fn(created_at)
        except Exception:
            return None
        try:
            value = float(age)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _looks_like_active_hook_answer(self, event: Any, *, text: str) -> bool:
        if self.ctx is None:
            return False
        config = getattr(self.ctx, "config", None)
        live_mode = str(getattr(config, "live_mode", "solo_stream") or "solo_stream")
        probe = ViewerEvent(
            uid=event_uid(event),
            nickname=event_nickname(event),
            danmaku_text=text,
            source="live_danmaku",
            live_mode=live_mode,
        )
        return is_active_hook_answer_event(getattr(self.ctx, "recent_results", []), probe)

    def _record_reply_skip(self, event: Any, *, reason: str, score: float) -> None:
        if self.ctx is None:
            return
        self._last_decision_at = self._now()
        self._last_selected_type = "danmaku.skipped"
        self._last_candidate_count = 1
        self._last_skip_reason = reason
        self.ctx.audit.record(
            "live_event_reply_skipped",
            reason,
            detail={
                "uid": event_uid(event),
                "event_type": event_type(event),
                "score": round(score, 1),
                "guard_level": event_guard_level(event),
                "skip_reason": reason,
            },
        )

    def _candidate_summary(self, event: Any, score: float, order: int) -> dict[str, Any]:
        return {
            "order": order,
            "uid": event_uid(event),
            "event_type": event_type(event),
            "score": round(score, 1),
            "guard_level": event_guard_level(event),
            "text_length": len(event_text(event)),
        }

    def _payload_for_event(self, event: Any, event_type: str) -> dict[str, Any]:
        payload = {
            "uid": event_uid(event),
            "nickname": event_nickname(event),
            "danmaku_text": event_text(event),
            "avatar_url": event_avatar_url(event),
            "room_id": event_room_id(event),
            "event_type": event_type,
        }
        session_generation = event_session_generation(event)
        if session_generation:
            payload["_live_session_generation"] = session_generation
        room_ref = event_room_ref(event)
        if room_ref:
            payload["room_ref"] = room_ref
        payload.update(event_signal_fields(event))
        if "gift_count" in payload and "gift_num" not in payload:
            payload["gift_num"] = payload["gift_count"]
        if "gift_value" in payload and "gift_total_coin" not in payload:
            payload["gift_total_coin"] = payload["gift_value"]
        return payload

    def prompt_block_for_event(self, event: Any) -> str:
        """Build advisory room-topic context for prompt modules.

        This is intentionally owned by live_events: the same module that sees
        the danmaku stream also filters low-value messages and summarizes the
        current room topic. It does not route output or persist viewer data.
        """
        if not self.enabled:
            return ""
        return self._room_topic.prompt_block_for_event(event)

    def _spawn(self, coro: Any) -> "asyncio.Task[Any]":
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _flush_after(self, delay: float) -> None:
        try:
            if delay > 0:
                await self._sleep(delay)
            event = self._best
            count = self._buffered_count
            candidates = list(self._candidate_summaries)
            winner_order = self._best_order
            # 取出胜者并复位窗口；同步段无 await，不会与 submit 交错（asyncio 单线程）。
            self._clear_window()
            if event is not None and self.ctx is not None and self.enabled:
                self._mark_dispatch()
                await self._roast(event, count=count, candidates=candidates, winner_order=winner_order)
        except asyncio.CancelledError:
            if self._flush_task is asyncio.current_task():
                self._clear_window()
            raise
        except Exception as exc:
            self._clear_window()
            if self.ctx is not None:
                self.ctx.audit.record("live_event_flush_failed", type(exc).__name__, level="warning")

    async def _roast(self, event: Any, count: int, candidates: list[dict[str, Any]] | None = None, winner_order: int = 0) -> None:
        if self.ctx is None:
            return
        uid = event_uid(event)
        score = self._safe_score(event)
        # 弹幕不含头像 URL，礼物/SC 可能带 face_url；下游仍会按既有身份解析兜底。
        selected_event_type = event_type(event)
        payload = self._payload_for_event(event, selected_event_type)
        record_payload_timeline(
            self.ctx,
            payload,
            stage="live_events.select",
            status="ok",
            reason=f"selected {selected_event_type}",
            route=selected_event_type,
        )
        selected = next((item for item in (candidates or []) if item.get("order") == winner_order), None)
        if selected is None:
            selected = self._candidate_summary(event, score, winner_order or 1)
        self._last_decision_at = self._now()
        self._last_selected_type = selected_event_type
        self._last_candidate_count = count
        self._last_skip_reason = ""
        dropped_candidates = []
        for item in candidates or []:
            if item.get("order") == selected.get("order"):
                continue
            dropped = dict(item)
            dropped["skip_reason"] = "selection.lower_score"
            dropped_candidates.append(dropped)
        self.ctx.audit.record(
            "live_event_selected",
            f"selected {selected_event_type} from {count} candidate(s)",
            detail={
                "uid": uid,
                "event_type": selected_event_type,
                "candidates": count,
                "score": round(score, 1),
                "guard_level": event_guard_level(event),
                "selected": selected,
                "dropped_candidates": dropped_candidates,
            },
        )
        try:
            await self.ctx.handle_live_payload(payload)
        except Exception as exc:
            self.ctx.audit.record("live_event_roast_failed", type(exc).__name__, level="warning")

    async def _record_signal_only(self, event: Any) -> None:
        if self.ctx is None:
            return
        selected_event_type = event_type(event)
        payload = self._payload_for_event(event, selected_event_type)
        record_payload_timeline(
            self.ctx,
            payload,
            stage="live_events.signal",
            status="skipped",
            reason=f"signal_only.{selected_event_type}",
            route=selected_event_type,
        )
        try:
            await self.ctx.handle_live_payload(payload)
        except Exception as exc:
            self.ctx.audit.record("live_event_signal_failed", type(exc).__name__, level="warning")


def _looks_reply_worthy_text(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    stripped = lowered.strip(" \t\r\n,.!?;:，。！？；：~～")
    if stripped in REPLY_WORTHY_TEXT_WORDS:
        return True
    return any(marker in lowered for marker in REPLY_WORTHY_TEXT_MARKERS)

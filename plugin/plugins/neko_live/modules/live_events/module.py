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
from ...core.runtime_live_input import mark_recent_chat_observed
from ...core.runtime_timeline import record_payload_timeline
from .ambient_context import AmbientRoomContext
from .ambient_hook import AMBIENT_HOOK_SCAN_LIMIT, select_ambient_hook
from .provider_event import (
    event_avatar_url,
    event_guard_level,
    event_is_current_session,
    event_nickname,
    event_provider_event_id,
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
from .recent_chat import RecentChatBuffer
from .room_topic import RoomTopicContext
from .ritual_memory import RitualMemory, RitualPrompt
from .room_verdict import RoomVerdict
from .scene_state import SceneState


# Hard ceiling for the combined live-context prompt block (room verdict +
# scene state + room pulse + room ritual). Each collaborator caps itself, but
# they can co-occur, so the total is bounded here too: prompt length, not
# bounded local CPU, is the material cost measured in
# docs/modules/live_room_context.md.
LIVE_CONTEXT_PROMPT_MAX_CHARS = 520
LOW_REPLY_VALUE_SCORE_BYPASS = 1000.0
QUIET_REPLY_SCORE_BYPASS = 80.0
LIVE_REPLY_PRESSURE_WINDOW_SECONDS = 60.0
LIVE_REPLY_QUEUE_LIMIT_FLOOR = 1
NEW_VIEWER_BURST_WINDOW_SECONDS = 45.0
NEW_VIEWER_BURST_UNIQUE_THRESHOLD = 5
NEW_VIEWER_BATCH_WELCOME_COOLDOWN_SECONDS = 90.0
AMBIENT_CHAT_RETENTION_SECONDS = 120.0
AMBIENT_CHAT_BURST_WINDOW_SECONDS = 10.0
AMBIENT_CHAT_BURST_LIMIT = 4
AMBIENT_READ_DEBOUNCE_SECONDS = 1.0
AMBIENT_READ_CHAT_LIMIT = 3
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
REPLY_WORTHY_SELECTION_BONUS = 120.0


class _SessionBoundProviderEvent:
    __slots__ = ("_event", "session_generation")

    def __init__(self, event: Any, session_generation: int) -> None:
        self._event = event
        self.session_generation = session_generation

    def __getattr__(self, name: str) -> Any:
        if isinstance(self._event, dict):
            try:
                return self._event[name]
            except KeyError:
                raise AttributeError(name) from None
        return getattr(self._event, name)


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
        self._best_recent_chat_seq: int = 0
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
        self._recent_chat = RecentChatBuffer(now=lambda: self._now())
        self._ambient_context = AmbientRoomContext(now=lambda: self._now())
        self._scene_state = SceneState(now=lambda: self._now())
        self._ritual_memory = RitualMemory(now=lambda: self._now())
        self._room_verdict = RoomVerdict(now=lambda: self._now())
        self._ambient_sleep = asyncio.sleep
        self._ambient_refresh_task: asyncio.Task[Any] | None = None
        self._ambient_refresh_requested_revision = 0
        self._ambient_clear_tasks: set[asyncio.Task[Any]] = set()
        self._ambient_active_session_key = ""
        self._ambient_last_published_text = ""
        self._ambient_publish_count = 0
        self._ambient_expiry_count = 0
        self._ambient_publish_suppressed_count = 0
        self._ambient_publish_last_reason = ""
        self._ambient_hook_candidate_reads = 0
        self._ambient_hook_candidate_hits = 0
        self._ambient_hook_last_reason = ""
        self._ambient_hook_last_score = 0
        self._ambient_hook_last_candidate_count = 0
        self._ambient_chat_suppressed_reason = ""
        self._recent_chat_query_requests = 0
        self._recent_chat_query_hits = 0
        self._recent_chat_relevant_requests = 0
        self._recent_chat_relevant_hits = 0
        self._recent_chat_duplicate_delivery_count = 0
        self._ambient_chat_candidate_reads = 0
        self._ambient_chat_candidate_hits = 0
        self._ambient_chat_used_count = 0
        self._ambient_chat_suppressed_count = 0
        self._room_pulse_prompt_uses = 0
        self._room_pulse_prompt_omits = 0
        self._room_pulse_prompt_last_chars = 0
        self._room_pulse_prompt_last_reason = ""

    async def setup(self, ctx: Any) -> None:
        """注册到 ``EventBus`` 的高价值互动事件。中枢负责同一冷却窗口内的择优；其它
        事件族 handler 仍照此在自己 setup 里 ``bus.subscribe(type, ...)``，零碰接入层。"""
        await super().setup(ctx)
        bus = getattr(ctx, "event_bus", None)
        if bus is not None:
            for event_type in ("danmaku",):
                self._unsubscribes.append(bus.subscribe(event_type, self._on_bus_event, owner=self.id))
            self._unsubscribes.append(
                bus.subscribe("result", self._on_result, owner=self.id)
            )

    def _on_bus_event(self, event: Any) -> None:
        """EventBus 订阅回调：解包信封取富模型，复用既有窗口择优 ``submit()``（签名不变）。"""
        raw = getattr(event, "raw", None)
        if raw is not None:
            session_generation = event_session_generation(event)
            self.submit(
                _SessionBoundProviderEvent(raw, session_generation)
                if session_generation
                else raw
            )
        else:
            self.submit(event)

    def _on_result(self, result: Any) -> None:
        """Observe privacy-safe successful results without owning the result path."""

        self._scene_state.observe_result(result)
        self._room_verdict.observe_result(result)

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
        clear_tasks = [
            task for task in list(self._ambient_clear_tasks) if not task.done()
        ]
        if clear_tasks:
            await asyncio.gather(*clear_tasks, return_exceptions=True)
        self._ambient_clear_tasks.clear()
        await super().teardown()

    def _clear_window(self) -> None:
        self._flush_task = None
        self._best = None
        self._best_score = 0.0
        self._best_order = 0
        self._best_recent_chat_seq = 0
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
        self._schedule_ambient_context_clear()
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
        self._recent_chat.reset()
        self._ambient_context.reset()
        self._scene_state.reset()
        self._ritual_memory.reset()
        self._room_verdict.reset()
        self._ambient_refresh_task = None
        self._ambient_refresh_requested_revision = 0
        self._ambient_active_session_key = ""
        self._ambient_last_published_text = ""
        self._ambient_publish_count = 0
        self._ambient_expiry_count = 0
        self._ambient_publish_suppressed_count = 0
        self._ambient_publish_last_reason = ""
        self._ambient_hook_candidate_reads = 0
        self._ambient_hook_candidate_hits = 0
        self._ambient_hook_last_reason = ""
        self._ambient_hook_last_score = 0
        self._ambient_hook_last_candidate_count = 0
        self._ambient_chat_suppressed_reason = ""
        self._recent_chat_query_requests = 0
        self._recent_chat_query_hits = 0
        self._recent_chat_relevant_requests = 0
        self._recent_chat_relevant_hits = 0
        self._recent_chat_duplicate_delivery_count = 0
        self._ambient_chat_candidate_reads = 0
        self._ambient_chat_candidate_hits = 0
        self._ambient_chat_used_count = 0
        self._ambient_chat_suppressed_count = 0
        self._room_pulse_prompt_uses = 0
        self._room_pulse_prompt_omits = 0
        self._room_pulse_prompt_last_chars = 0
        self._room_pulse_prompt_last_reason = ""

    def status(self) -> dict[str, Any]:
        status = {
            "enabled": self.enabled,
            "buffered": self._buffered_count,
            "buffered_candidate_summaries": len(self._candidate_summaries),
            "buffered_candidate_summary_limit": self._candidate_summary_limit(),
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
        status.update(self._recent_chat.status())
        status.update(self._ambient_context.status())
        config = getattr(self.ctx, "config", None) if self.ctx is not None else None
        status.update(
            self._scene_state.status(
                live_mode=str(getattr(config, "live_mode", "") or "")
            )
        )
        status["ambient_chat_suppressed_reason"] = self._ambient_chat_suppressed_reason
        status.update(
            {
                "recent_chat_query_requests": self._recent_chat_query_requests,
                "recent_chat_query_hits": self._recent_chat_query_hits,
                "recent_chat_relevant_requests": self._recent_chat_relevant_requests,
                "recent_chat_relevant_hits": self._recent_chat_relevant_hits,
                "recent_chat_duplicate_delivery_count": (
                    self._recent_chat_duplicate_delivery_count
                ),
                "ambient_chat_candidate_reads": self._ambient_chat_candidate_reads,
                "ambient_chat_candidate_hits": self._ambient_chat_candidate_hits,
                "ambient_chat_used_count": self._ambient_chat_used_count,
                "ambient_chat_suppressed_count": self._ambient_chat_suppressed_count,
                "room_pulse_prompt_uses": self._room_pulse_prompt_uses,
                "room_pulse_prompt_omits": self._room_pulse_prompt_omits,
                "room_pulse_prompt_last_chars": self._room_pulse_prompt_last_chars,
                "room_pulse_prompt_last_reason": self._room_pulse_prompt_last_reason,
                "ambient_publish_count": self._ambient_publish_count,
                "ambient_expiry_count": self._ambient_expiry_count,
                "ambient_publish_suppressed_count": (
                    self._ambient_publish_suppressed_count
                ),
                "ambient_publish_last_reason": self._ambient_publish_last_reason,
                "ambient_hook_candidate_reads": self._ambient_hook_candidate_reads,
                "ambient_hook_candidate_hits": self._ambient_hook_candidate_hits,
                "ambient_hook_last_reason": self._ambient_hook_last_reason,
                "ambient_hook_last_score": self._ambient_hook_last_score,
                "ambient_hook_last_candidate_count": (
                    self._ambient_hook_last_candidate_count
                ),
            }
        )
        status.update(self._ritual_memory.status())
        status.update(self._room_verdict.status())
        return status

    def is_confirmed_room_ritual(self, phrase: str) -> bool:
        """True when a phrase is an established room ritual that has not retired.

        Anti-repeat consumers use this to tell a callback apart from drift: an
        established ritual returning after its gap is the payoff the recent
        material windows must not suppress.
        """
        return self._ritual_memory.is_confirmed_ritual(phrase)

    def recent_chat_snapshot(self, *, limit: int = 1) -> list[dict[str, object]]:
        """Return the bounded session tail for exact positional chat questions."""

        self._recent_chat_query_requests += 1
        rows = self._recent_chat.session_tail_snapshot(limit=limit)
        if rows:
            self._recent_chat_query_hits += 1
        return rows

    def relevant_chat_snapshot(
        self,
        *,
        query: object,
        limit: int = 1,
    ) -> list[dict[str, object]]:
        """Return one locally matched unselected remark for an ordinary chat turn."""

        self._recent_chat_relevant_requests += 1
        reason = self._ambient_chat_suppression_reason()
        if reason:
            self._record_ambient_suppression(reason)
            return []
        rows = self._recent_chat.relevant_snapshot(
            query=query,
            limit=1,
            max_age_seconds=AMBIENT_CHAT_RETENTION_SECONDS,
        )
        if not rows:
            self._ambient_chat_suppressed_reason = "no_relevant_match"
            return []
        self._recent_chat_relevant_hits += 1
        self._ambient_chat_suppressed_reason = ""
        seq = rows[0].get("seq")
        try:
            clean_seq = int(seq) if not isinstance(seq, bool) else 0
        except (TypeError, ValueError, OverflowError):
            clean_seq = 0
        if self._recent_chat.mark_ambient_used(clean_seq):
            self._ambient_chat_used_count += 1
        return rows

    def ambient_chat_snapshot(self, *, limit: int = 3) -> list[dict[str, object]]:
        """Return low-pressure, unselected danmaku for existing hosting turns."""

        self._ambient_chat_candidate_reads += 1
        reason = self._ambient_chat_suppression_reason()
        if reason:
            self._record_ambient_suppression(reason)
            return []
        rows = self._recent_chat.ambient_snapshot(
            limit=limit,
            max_age_seconds=AMBIENT_CHAT_RETENTION_SECONDS,
        )
        self._ambient_chat_suppressed_reason = "" if rows else "empty"
        if rows:
            self._ambient_chat_candidate_hits += 1
        return rows

    def mark_ambient_chat_used(self, seq: int) -> bool:
        used = self._recent_chat.mark_ambient_used(seq)
        if used:
            self._ambient_chat_used_count += 1
        return used

    def _record_ambient_suppression(self, reason: str) -> None:
        self._ambient_chat_suppressed_reason = reason
        self._ambient_chat_suppressed_count += 1

    def _ambient_chat_suppression_reason(self) -> str:
        if not self.enabled or self.ctx is None:
            return "inactive"
        if str(getattr(self.ctx.config, "live_mode", "solo_stream")) != "solo_stream":
            return "not_solo_stream"
        guard = getattr(self.ctx, "safety_guard", None)
        status = getattr(guard, "status", None)
        if callable(status):
            try:
                safety_status = str(status() or "")
            except Exception:
                safety_status = ""
            if safety_status and safety_status != "running":
                return f"safety_{safety_status}"
        if self._safety_queue_near_limit():
            return "safety_queue_pressure"
        if self._reply_queue_full():
            return "reply_pressure"
        if self._new_viewer_burst_active():
            return "new_viewer_burst"
        burst_count = self._recent_chat.count(
            max_age_seconds=AMBIENT_CHAT_BURST_WINDOW_SECONDS,
            selected=False,
        )
        if burst_count > AMBIENT_CHAT_BURST_LIMIT:
            return "danmaku_burst"
        return ""

    def remember_support_context(
        self,
        payload: dict[str, Any],
        *,
        tier: str,
    ) -> bool:
        """Remember one provider-verified support fact for a later natural turn."""

        if (
            not self.enabled
            or self.ctx is None
            or not self._is_co_stream()
            or payload.get("support_verified") is not True
        ):
            return False
        remembered = self._ambient_context.remember_support(
            payload,
            tier=tier,
        )
        if remembered:
            self._schedule_ambient_context_refresh()
        return remembered

    def _is_co_stream(self) -> bool:
        if self.ctx is None:
            return False
        return str(getattr(self.ctx.config, "live_mode", "")) == "co_stream"

    def _schedule_ambient_context_refresh(self) -> None:
        if not self._is_co_stream():
            return
        self._ambient_refresh_requested_revision += 1
        task = self._ambient_refresh_task
        if task is not None and not task.done():
            return
        task = self._spawn(self._publish_ambient_context_after_delay())
        self._ambient_refresh_task = task

        def _clear(done_task: asyncio.Task[Any]) -> None:
            if self._ambient_refresh_task is done_task:
                self._ambient_refresh_task = None

        task.add_done_callback(_clear)

    def schedule_session_context_refresh(self) -> None:
        """Queue the current session's authoritative facts or explicit absence."""

        self._schedule_ambient_context_refresh()

    async def _publish_ambient_context_after_delay(self) -> None:
        try:
            while True:
                await self._ambient_sleep(AMBIENT_READ_DEBOUNCE_SECONDS)
                requested_revision = self._ambient_refresh_requested_revision
                await self._publish_ambient_context()
                if requested_revision == self._ambient_refresh_requested_revision:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_ambient_publish_suppression(
                f"publish_failed.{type(exc).__name__}"
            )

    async def _publish_ambient_context(self) -> bool:
        reason = self._ambient_context_suppression_reason()
        if reason:
            self._record_ambient_publish_suppression(reason)
            return False
        # Session reset publishes an expiry marker on the SAME stable host
        # coalesce key used by the next session.  Wait for those older clears
        # before submitting the fresh snapshot, otherwise a delayed clear can
        # overwrite the new session's facts.  The tasks are bounded by session
        # transitions and remove themselves from the set when complete.
        clear_tasks = [
            task for task in list(self._ambient_clear_tasks) if not task.done()
        ]
        if clear_tasks:
            await asyncio.gather(*clear_tasks, return_exceptions=True)
            reason = self._ambient_context_suppression_reason()
            if reason:
                self._record_ambient_publish_suppression(reason)
                return False
        # The three-position session tail is the authoritative answer for
        # "latest / previous / the one before that".  Unlike the wider
        # relevance store it is session-bound rather than time-expired, and is
        # replaced only when a new danmaku arrives or the live session resets.
        rows = self._recent_chat.session_tail_snapshot(
            limit=AMBIENT_READ_CHAT_LIMIT,
        )
        hook_selection = select_ambient_hook(
            self._recent_chat.snapshot(
                limit=AMBIENT_HOOK_SCAN_LIMIT,
                max_age_seconds=AMBIENT_CHAT_RETENTION_SECONDS,
            )
        )
        self._record_ambient_hook_selection(hook_selection)
        text = (
            self._ambient_context.build_snapshot(
                rows,
                hook_row=hook_selection.row,
                hook_reason=hook_selection.reason,
            )
            or self._ambient_context.empty_snapshot()
        )
        dispatcher = getattr(self.ctx, "dispatcher", None)
        push = getattr(dispatcher, "push_ambient_room_context", None)
        session_key = self._ambient_session_key()
        if (
            session_key == self._ambient_active_session_key
            and text == self._ambient_last_published_text
        ):
            self._record_ambient_publish_suppression("unchanged")
            return False
        await push(
            text,
            session_key=session_key,
            expired=False,
        )
        self._ambient_active_session_key = session_key
        self._ambient_last_published_text = text
        self._ambient_publish_count += 1
        # The SDK boundary is fire-and-forget: this means submitted to the
        # local transport, not acknowledged by the host callback queue.
        self._ambient_publish_last_reason = "submitted_unconfirmed"
        return True

    def _ambient_context_suppression_reason(self) -> str:
        if not self.enabled or self.ctx is None:
            return "inactive"
        if not self._is_co_stream():
            return "not_co_stream"
        config = self.ctx.config
        if not bool(getattr(config, "live_enabled", False)):
            return "live_disabled"
        if bool(getattr(config, "dry_run", False)):
            return "dry_run"
        if hasattr(self.ctx, "_accepting_live_events") and not bool(
            getattr(self.ctx, "_accepting_live_events", False)
        ):
            return "not_accepting_live_events"
        guard = getattr(self.ctx, "safety_guard", None)
        status = getattr(guard, "status", None)
        if callable(status):
            try:
                safety_status = str(status() or "")
            except Exception:
                safety_status = ""
            if safety_status and safety_status != "running":
                return f"safety_{safety_status}"
        dispatcher = getattr(self.ctx, "dispatcher", None)
        push = getattr(dispatcher, "push_ambient_room_context", None)
        if not callable(push):
            return "dispatcher_unavailable"
        channel_status = getattr(dispatcher, "output_channel_status", None)
        if callable(channel_status):
            try:
                output = channel_status()
            except Exception:
                return "output_channel_unavailable"
            if isinstance(output, dict) and not bool(output.get("ready", False)):
                return str(output.get("reason") or "output_channel_unavailable")
        return ""

    def _ambient_session_key(self) -> str:
        if self.ctx is None:
            return "default"
        generation = int(
            getattr(self.ctx, "_live_session_generation", 0) or 0
        )
        room = str(
            getattr(self.ctx.config, "live_room_ref", "")
            or getattr(self.ctx.config, "live_room_id", 0)
            or "room"
        )[:48]
        return f"{generation}:{room}"

    def _schedule_ambient_context_clear(self) -> None:
        session_key = self._ambient_active_session_key
        if not session_key or self.ctx is None:
            return
        dispatcher = getattr(self.ctx, "dispatcher", None)
        push = getattr(dispatcher, "push_ambient_room_context", None)
        if not callable(push):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ambient_active_session_key = ""
        task = loop.create_task(
            self._clear_ambient_context(push, session_key=session_key)
        )
        self._ambient_clear_tasks.add(task)
        task.add_done_callback(self._ambient_clear_tasks.discard)

    async def _clear_ambient_context(
        self,
        push: Any,
        *,
        session_key: str,
    ) -> None:
        try:
            await push(
                self._ambient_context.expiry_marker(),
                session_key=session_key,
                expired=True,
            )
            # Session-boundary clears are now the only thing that retires a
            # snapshot: the 45-second freshness timer is gone, because the
            # host delivers passive context at the next natural hot swap and
            # the snapshot is written to stay true under that delay.
            self._ambient_expiry_count += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.ctx is not None:
                self.ctx.audit.record(
                    "ambient_context_clear_failed",
                    type(exc).__name__,
                    level="warning",
                )

    def _record_ambient_publish_suppression(self, reason: str) -> None:
        self._ambient_publish_suppressed_count += 1
        self._ambient_publish_last_reason = str(reason or "unknown")[:64]

    def _record_ambient_hook_selection(self, selection: Any) -> None:
        self._ambient_hook_candidate_reads += 1
        self._ambient_hook_last_reason = str(
            getattr(selection, "reason", "no_suitable") or "no_suitable"
        )[:48]
        self._ambient_hook_last_score = max(
            0,
            int(getattr(selection, "score", 0) or 0),
        )
        self._ambient_hook_last_candidate_count = max(
            0,
            int(getattr(selection, "candidate_count", 0) or 0),
        )
        if getattr(selection, "row", None) is not None:
            self._ambient_hook_candidate_hits += 1

    def submit(self, event: Any) -> None:
        """富模型直播事件入口（由 live provider 事件或 EventBus 回调驱动）。"""
        if not self.enabled or self.ctx is None:
            return
        if not is_routable(event) or not event_is_current_session(event, self.ctx):
            return  # 进场等事件留给各自 P3 handler；无 handler 类型保持静默。
        uid = event_uid(event)
        if is_signal_only(event):
            if not uid or uid == "0":
                return  # 无 uid，无从记录 / 锐评
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
        recent_chat_seq = self.observe_danmaku(event)
        if recent_chat_seq <= 0:
            return
        text = event_text(event)
        if not uid or uid == "0":
            return  # 事实缓存允许匿名消息；既有锐评链路仍要求稳定 uid。
        score = self._safe_score(event)
        selection_score = score + _reply_value_bonus(text)
        if (
            self._is_co_stream()
            and str(
                getattr(self.ctx.config, "co_stream_output_policy", "")
                or ""
            ).strip()
            != "auto_low_interrupt"
        ):
            self._last_decision_at = self._now()
            self._last_selected_type = ""
            self._last_candidate_count = 1
            self._last_skip_reason = "co_stream.output_policy_off"
            return
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
            self._spawn(
                self._roast(
                    event,
                    count=1,
                    candidates=[self._candidate_summary(event, selection_score, 1)],
                    winner_order=1,
                    recent_chat_seq=recent_chat_seq,
                )
            )
            return
        # 冷却期：缓冲择优，只保留当前分最高者（O(1) 内存，无需保留整批）。
        # 这里的冷却只控制既有入口的节奏，不判断 VAD / 播放 / 话权；
        # 内容值得回应时仍保留候选，技术性语音碰撞由宿主安全入口处理。
        order = self._buffered_count + 1
        self._remember_candidate_summary(
            self._candidate_summary(event, selection_score, order)
        )
        if self._best is None or selection_score > self._best_score:
            self._best = event
            self._best_score = selection_score
            self._best_order = order
            self._best_recent_chat_seq = recent_chat_seq
        self._buffered_count += 1
        if self._flush_task is None:
            self._track_flush_task(self._spawn(self._flush_after(remaining)))

    def observe_danmaku(self, event: Any) -> int:
        """Record one danmaku without selecting or dispatching a reply.

        Provider callbacks and developer simulation both use this observation
        path so the recent-chat tail, room topic and passive-context refresh
        cannot drift apart.  Reply selection remains owned by :meth:`submit`.
        """

        if not self.enabled or self.ctx is None:
            return 0
        if not is_routable(event) or is_signal_only(event):
            return 0
        text = event_text(event)
        if not text or not event_is_current_session(event, self.ctx):
            return 0
        uid = event_uid(event)
        nickname = event_nickname(event)
        observed_at = self._now()
        provider_event_id = event_provider_event_id(event)
        recent_chat_seq = self._remember_recent_chat(
            uid=uid,
            nickname=nickname,
            text=text,
            observed_at=observed_at,
            provider_event_id=provider_event_id,
        )
        if provider_event_id and recent_chat_seq <= 0:
            self._recent_chat_duplicate_delivery_count += 1
            return 0
        if recent_chat_seq <= 0:
            return 0
        self._schedule_ambient_context_refresh()
        if not uid or uid == "0":
            return recent_chat_seq
        self._remember_recent_viewer(uid)
        score = self._safe_score(event)
        provider_ts = getattr(event, "ts", 0.0)
        try:
            topic_ts = float(provider_ts or observed_at)
        except (TypeError, ValueError):
            topic_ts = observed_at
        self._room_topic.remember_danmaku(
            uid=uid,
            nickname=nickname,
            text=text,
            score=score,
            ts=topic_ts,
        )
        # Ballot tally runs on EVERY danmaku, not just the one selected for a
        # reply: the room's verdict is what the whole room said, and selection
        # only ever picks one message.
        self._room_verdict.observe_answer(uid=uid, text=text)
        return recent_chat_seq

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

    def _candidate_summary_limit(self) -> int:
        return self._reply_queue_limit()

    def _remember_candidate_summary(self, summary: dict[str, Any]) -> None:
        """Keep only the highest-ranked privacy-safe window summaries."""

        candidates = [*self._candidate_summaries, summary]
        candidates.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                int(item.get("order") or 0),
            )
        )
        retained = candidates[: self._candidate_summary_limit()]
        retained.sort(key=lambda item: int(item.get("order") or 0))
        self._candidate_summaries = retained

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
                "event_type": event_type(event),
                "score": round(score, 1),
                "guard_level": event_guard_level(event),
                "skip_reason": reason,
            },
        )

    def _candidate_summary(self, event: Any, score: float, order: int) -> dict[str, Any]:
        return {
            "order": order,
            "event_type": event_type(event),
            "score": round(score, 1),
            "guard_level": event_guard_level(event),
            "text_length": len(event_text(event)),
        }

    def _remember_recent_chat(
        self,
        *,
        uid: str,
        nickname: str,
        text: str,
        observed_at: float,
        provider_event_id: str,
    ) -> int:
        return self._recent_chat.remember(
            uid=uid,
            nickname=nickname,
            text=text,
            observed_at=observed_at,
            provider_event_id=provider_event_id,
        )

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
        provider_event_id = event_provider_event_id(event)
        if provider_event_id:
            payload["provider_event_id"] = provider_event_id
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
        """Build compact advisory room context for an already-scheduled turn.

        This is intentionally owned by live_events: the same module that sees
        the danmaku stream also filters low-value messages and summarizes the
        current room topic. It does not route output or persist viewer data.
        """
        suppression_reason = self._room_pulse_prompt_suppression_reason()
        if suppression_reason:
            self._record_room_pulse_prompt("", suppression_reason)
            self._scene_state.suppress_prompt(suppression_reason)
            return ""
        projection = self._room_topic.prompt_projection_for_event(event)
        self._record_room_pulse_prompt(projection.text, projection.reason)
        scene = self._scene_state.prompt_for_event(event)
        verdict = self._verdict_block_for_event(event)
        ritual, ritual_context = self._ritual_offer_for_event(event)
        # Four independent blocks can co-occur, so the combined size is capped
        # rather than left to grow with each new collaborator. Blocks are added
        # in descending time-sensitivity and a block that would not fit is
        # DROPPED, never truncated: half an instruction is worse than none.
        blocks = (verdict, scene.text, projection.text, ritual.text)
        ritual_index = len(blocks) - 1
        rendered, kept_indexes = self._fit_context_blocks_with_indexes(blocks)
        if ritual.text and ritual_index in kept_indexes:
            # An offer becomes a use only after its complete block survived the
            # shared prompt budget. A dropped block must not consume one of the
            # ritual's bounded payoffs or start its cooldown.
            self._ritual_memory.mark_used(ritual.key, ritual_context)
        return rendered

    @staticmethod
    def _fit_context_blocks(blocks: tuple[str, ...]) -> str:
        return LiveEventsModule._fit_context_blocks_with_indexes(blocks)[0]

    @staticmethod
    def _fit_context_blocks_with_indexes(
        blocks: tuple[str, ...],
    ) -> tuple[str, frozenset[int]]:
        kept: list[str] = []
        kept_indexes: set[int] = set()
        used = 0
        for index, block in enumerate(blocks):
            if not block:
                continue
            if used + len(block) > LIVE_CONTEXT_PROMPT_MAX_CHARS:
                continue
            kept.append(block)
            kept_indexes.add(index)
            used += len(block)
        return "".join(kept), frozenset(kept_indexes)

    def _verdict_block_for_event(self, event: Any) -> str:
        """Announce the room's collective answer once, and offer the winning
        token to RitualMemory.

        A phrase the whole room converged on is the strongest ritual candidate
        a stream produces, so the winner is fed in with the ballot's distinct
        voter count as its support — the same "several distinct viewers" bar
        the repeated-signal path uses.
        """
        if event_type(event) in {"gift", "super_chat", "guard"}:
            return ""
        winner = self._room_verdict.winning_answer()
        if winner:
            self._ritual_memory.observe_repeated_signal(
                kind="content",
                support=self._room_verdict.status()["room_verdict_current_voters"],
                phrase=winner,
            )
        return self._room_verdict.verdict_prompt().text

    def _ritual_offer_for_event(self, event: Any) -> tuple[RitualPrompt, str]:
        """Observe the room's repeated signal and, when one has matured into a
        ritual, offer a single callback line.

        Observation reuses the classification pass `prompt_projection_for_event`
        just ran, so this adds no extra sweep. Support events never promote or
        pay off a ritual: a gift is not something the room made together.
        """
        if event_type(event) in {"gift", "super_chat", "guard"}:
            return RitualPrompt(reason="support_event"), ""
        kind, support, key = self._room_topic.last_repeated_signal()
        if key:
            self._ritual_memory.observe_repeated_signal(
                kind=kind, support=support, phrase=key
            )
        context_key = str(self._room_topic.dominant_theme_key() or "")
        offer = self._ritual_memory.callback_for_context(context_key)
        return offer, context_key

    def _room_pulse_prompt_suppression_reason(self) -> str:
        if not self.enabled or self.ctx is None:
            return "inactive"
        guard = getattr(self.ctx, "safety_guard", None)
        status = getattr(guard, "status", None)
        if callable(status):
            try:
                safety_status = str(status() or "")
            except Exception:
                safety_status = ""
            if safety_status and safety_status != "running":
                return "safety_not_running"
        if self._safety_queue_near_limit():
            return "safety_queue_pressure"
        return ""

    def _record_room_pulse_prompt(self, text: str, reason: str) -> None:
        self._room_pulse_prompt_last_chars = min(len(text), 240)
        self._room_pulse_prompt_last_reason = str(reason or "unknown")[:48]
        if text:
            self._room_pulse_prompt_uses += 1
        else:
            self._room_pulse_prompt_omits += 1

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
            recent_chat_seq = self._best_recent_chat_seq
            # 取出胜者并复位窗口；同步段无 await，不会与 submit 交错（asyncio 单线程）。
            self._clear_window()
            if event is not None and self.ctx is not None and self.enabled:
                self._mark_dispatch()
                await self._roast(
                    event,
                    count=count,
                    candidates=candidates,
                    winner_order=winner_order,
                    recent_chat_seq=recent_chat_seq,
                )
        except asyncio.CancelledError:
            if self._flush_task is asyncio.current_task():
                self._clear_window()
            raise
        except Exception as exc:
            self._clear_window()
            if self.ctx is not None:
                self.ctx.audit.record("live_event_flush_failed", type(exc).__name__, level="warning")

    async def _roast(
        self,
        event: Any,
        count: int,
        candidates: list[dict[str, Any]] | None = None,
        winner_order: int = 0,
        recent_chat_seq: int = 0,
    ) -> None:
        if self.ctx is None:
            return
        score = self._safe_score(event)
        # 弹幕不含头像 URL，礼物/SC 可能带 face_url；下游仍会按既有身份解析兜底。
        selected_event_type = event_type(event)
        if self._recent_chat.mark_selected(recent_chat_seq):
            # The active ``respond`` path already represents this danmaku.
            # Refresh the passive room snapshot so a later natural turn
            # cannot consume the same public event a second time.
            self._schedule_ambient_context_refresh()
        payload = self._payload_for_event(event, selected_event_type)
        if selected_event_type == "danmaku" and recent_chat_seq > 0:
            # submit() already recorded this exact provider event before the
            # selected payload re-enters the shared runtime pipeline.
            payload = mark_recent_chat_observed(payload)
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


def _reply_value_bonus(text: str) -> float:
    """Prefer explicit questions/hooks inside a cooldown window without another model call."""

    return REPLY_WORTHY_SELECTION_BONUS if _looks_reply_worthy_text(text) else 0.0

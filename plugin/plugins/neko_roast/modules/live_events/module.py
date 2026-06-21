"""LiveEvent 中枢：富模型 ``on_event`` 的窗口择优消费者（P2.5 slice 1）。

职责（做什么）：
- 订阅 ``bili_live_ingest`` 转发来的富模型直播事件（``LiveDanmaku``，由 ``danmaku_core``
  的 ``on_event`` 产出，带 ``get_score()`` 打分）。
- 爆量房间冷却期内**缓冲**候选弹幕、按 ``get_score()`` 打分，冷却结束**择优**（舰长/总督/
  SC、礼物、上舰、粉丝牌、用户等级、长文本优先）取分最高者投 ``pipeline``；空闲态首条弹幕**即时**锐评
  （保留已真机验证的「首评观众即开口」DoD）。
- 把限流从「冷却期 skip 掉所有人、冷却后第一个到达即选中」升级为「冷却期缓冲、到点择优」。
  每个窗口只有 1 条进 pipeline，顺带缓解 ``queue_limit`` 溢出。

不做什么（当前边界）：
- 只把弹幕/礼物/SC/上舰放进同一窗口择优。进场等事件仍交给各自 P3 handler。
- 礼物/SC/上舰先复用现有 pipeline 产出端；专属致谢/朗读 prompt 留待 P3 事件族 handler。
- 不自己拼 prompt、不直接调 ``push_message`` / ``store.set``：胜者经 ``handle_live_payload``
  走既有 ``normalize -> pipeline -> safety_guard -> avatar_roast -> dispatcher`` 全链路，
  四条不变量（唯一出口 / 唯一档案写入 / 唯一审计 / 安全门必经）原样保持。

数据流：``on_event(LiveDanmaku) -> submit() -> (即时 | 开窗择优) -> handle_live_payload()``。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .._base import BaseModule
from ..bili_live_ingest.livedanmaku import MessageType


_ROUTABLE_MESSAGE_TYPES = {
    MessageType.MSG_DANMAKU,
    MessageType.MSG_GIFT,
    MessageType.MSG_SUPER_CHAT,
    MessageType.MSG_GUARD_BUY,
}

_MESSAGE_TYPE_LABELS = {
    MessageType.MSG_DANMAKU: "danmaku",
    MessageType.MSG_GIFT: "gift",
    MessageType.MSG_SUPER_CHAT: "super_chat",
    MessageType.MSG_GUARD_BUY: "guard",
}


class LiveEventsModule(BaseModule):
    """直播事件中枢。``submit()`` 是富模型事件入口，同步、非阻塞（只缓冲/打分，pipeline
    在后台 task 里跑，不拖慢弹幕接收循环）。"""

    id = "live_events"
    title = "直播事件"

    def __init__(self) -> None:
        super().__init__()
        self._best: Any = None
        self._best_score: float = 0.0
        self._buffered_count: int = 0
        self._flush_task: "asyncio.Task[Any] | None" = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # 中枢本地「刚投递」时间戳：同步更新，确保紧接着到的事件不会因 safety_guard 的
        # _last_output_at 尚未被 before_output 写入而误走即时分支造成并发双锐评。
        self._last_dispatch_at: float = 0.0
        # 可注入：单测里替换成确定性的 sleep / 时钟。
        self._sleep = asyncio.sleep
        self._now = time.time
        # EventBus 订阅句柄（fake ctx 无 event_bus 时保持空列表）。
        self._unsubscribes: list[Any] = []

    async def setup(self, ctx: Any) -> None:
        """注册到 ``EventBus`` 的高价值互动事件。中枢负责同一冷却窗口内的择优；其它
        事件族 handler 仍照此在自己 setup 里 ``bus.subscribe(type, ...)``，零碰接入层。"""
        await super().setup(ctx)
        bus = getattr(ctx, "event_bus", None)
        if bus is not None:
            for event_type in ("danmaku", "gift", "super_chat", "guard"):
                self._unsubscribes.append(bus.subscribe(event_type, self._on_bus_event, owner=self.id))

    def _on_bus_event(self, event: Any) -> None:
        """EventBus 订阅回调：解包信封取富模型，复用既有窗口择优 ``submit()``（签名不变）。"""
        raw = getattr(event, "raw", None)
        if raw is not None:
            self.submit(raw)

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
        self._buffered_count = 0

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
        self._clear_window()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "buffered": self._buffered_count,
            "window_open": self._flush_task is not None,
        }

    def submit(self, event: Any) -> None:
        """富模型直播事件入口（由 ``bili_live_ingest`` 的 ``on_event`` 回调驱动）。"""
        if not self.enabled or self.ctx is None:
            return
        if getattr(event, "msg_type", None) not in _ROUTABLE_MESSAGE_TYPES:
            return  # 进场等事件留给各自 P3 handler；无 handler 类型保持静默。
        uid = str(getattr(event, "uid", "") or "").strip()
        text = str(getattr(event, "text", "") or "").strip()
        if not uid or uid == "0" or not text:
            return  # 无 uid / 无文本，无从锐评
        remaining = self._cooldown_remaining()
        if remaining <= 0 and self._flush_task is None:
            # 空闲态：首条即时锐评，保留已验证 DoD。
            self._mark_dispatch()
            self._spawn(self._roast(event, count=1))
            return
        # 冷却期：缓冲择优，只保留当前分最高者（O(1) 内存，无需保留整批）。
        score = self._safe_score(event)
        if self._best is None or score > self._best_score:
            self._best = event
            self._best_score = score
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
        try:
            return float(event.get_score())
        except Exception:
            return 0.0

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
            # 取出胜者并复位窗口；同步段无 await，不会与 submit 交错（asyncio 单线程）。
            self._clear_window()
            if event is not None and self.ctx is not None and self.enabled:
                self._mark_dispatch()
                await self._roast(event, count=count)
        except asyncio.CancelledError:
            if self._flush_task is asyncio.current_task():
                self._clear_window()
            raise
        except Exception as exc:
            self._clear_window()
            if self.ctx is not None:
                self.ctx.audit.record("live_event_flush_failed", type(exc).__name__, level="warning")

    async def _roast(self, event: Any, count: int) -> None:
        if self.ctx is None:
            return
        uid = str(getattr(event, "uid", "") or "")
        score = self._safe_score(event)
        # 弹幕不含头像 URL，礼物/SC 可能带 face_url；下游仍会按既有身份解析兜底。
        msg_type = getattr(event, "msg_type", None)
        event_type = _MESSAGE_TYPE_LABELS.get(msg_type, str(msg_type or "unknown"))
        payload = {
            "uid": uid,
            "nickname": str(getattr(event, "nickname", "") or ""),
            "danmaku_text": str(getattr(event, "text", "") or ""),
            "avatar_url": str(getattr(event, "face_url", "") or ""),
            "room_id": getattr(event, "room_id", 0),
            "event_type": event_type,
        }
        self.ctx.audit.record(
            "live_event_selected",
            f"selected {event_type} from {count} candidate(s)",
            detail={
                "uid": uid,
                "event_type": event_type,
                "candidates": count,
                "score": round(score, 1),
                "guard_level": getattr(event, "guard_level", 0),
            },
        )
        try:
            await self.ctx.handle_live_payload(payload)
        except Exception as exc:
            self.ctx.audit.record("live_event_roast_failed", type(exc).__name__, level="warning")

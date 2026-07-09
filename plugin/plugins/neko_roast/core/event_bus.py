"""直播事件中枢的发布 / 订阅骨架（EventBus）。

P2.5 完整版地基：把「谁产出事件」与「谁处理事件」解耦——接入侧（``bili_live_ingest``）只管
把直播事件包成 ``LiveEvent`` 发布到总线；各 handler 模块在 ``setup`` 里按事件类型订阅，彼此
互不感知。这是「把插件分发给其他开发者、各写各事件 handler」的核心契约：

    加一个事件族 handler = 写个模块 + 在它的 setup 里
        self._unsub = ctx.event_bus.subscribe("gift", self._on_gift, owner=self.id)
    零改外壳、零碰接入层、与其它模块并行无冲突。

三条保证（LIVE 场景可靠性第一）：
- **隔离**：一个订阅者的 handler 抛错（含其 async 任务抛错）只记 audit，绝不波及其余订阅者
  或发布方。
- **归属**：每个订阅带 ``owner``（模块 id）；失败 audit 带 ``owner`` + ``event_type``，能定位
  是谁炸的。
- **静默丢弃**：发布到无人订阅的事件类型 = no-op（任意模块子集都能安全运行，见
  docs/ui-architecture.md §4 层②）。

handler 可同步可异步：同步 handler 内联调用（沿用插件既有「同步回调 + 内部 fire-and-forget」
节奏，不拖慢弹幕接收循环）；返回协程则调度为隔离后台 task，其异常同样收敛进 audit。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any


class _Subscription:
    __slots__ = ("event_type", "handler", "owner")

    def __init__(self, event_type: str, handler: Callable[[Any], Any], owner: str) -> None:
        self.event_type = event_type
        self.handler = handler
        self.owner = owner


class EventBus:
    def __init__(self, audit: Any = None) -> None:
        self._subs: dict[str, list[_Subscription]] = {}
        self._audit = audit
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_publish_at: float = 0.0
        self._last_event_type: str = ""
        self._publish_count: int = 0

    def subscribe(self, event_type: str, handler: Callable[[Any], Any], *, owner: str = "") -> Callable[[], None]:
        """订阅某类直播事件。``owner``=归属模块 id（失败 audit 用）。返回取消订阅的句柄。"""
        sub = _Subscription(str(event_type), handler, str(owner))
        self._subs.setdefault(sub.event_type, []).append(sub)

        def _unsubscribe() -> None:
            bucket = self._subs.get(sub.event_type)
            if bucket and sub in bucket:
                bucket.remove(sub)

        return _unsubscribe

    def subscriber_count(self, event_type: str) -> int:
        return len(self._subs.get(str(event_type), []))

    def publish(self, event_type: str, event: Any) -> None:
        """按类型逐订阅者隔离派发。无订阅者 = 静默丢弃。"""
        event_type = str(event_type)
        if getattr(event, "schema_version", None) is not None and getattr(event, "type", ""):
            self._last_publish_at = time.time()
            self._last_event_type = event_type
            self._publish_count += 1
        for sub in list(self._subs.get(event_type, [])):
            try:
                result = sub.handler(event)
            except Exception as exc:  # noqa: BLE001 — 单订阅者失败隔离，不波及其余
                self._record_error(sub.owner, str(event_type), exc)
                continue
            if asyncio.iscoroutine(result):
                self._spawn_isolated(result, sub.owner, event_type)

    def status(self) -> dict[str, Any]:
        return {
            "last_publish_at": self._last_publish_at,
            "last_event_type": self._last_event_type,
            "publish_count": self._publish_count,
        }

    # —— 向后兼容的观测别名（runtime 的 sandbox_result / result 仍走这俩；无订阅者即 no-op）——
    def on(self, event: str, listener: Callable[[Any], Any]) -> Callable[[], None]:
        return self.subscribe(event, listener)

    def emit(self, event: str, payload: Any) -> None:
        self.publish(event, payload)

    def _spawn_isolated(self, coro: Any, owner: str, event_type: str) -> None:
        async def _runner() -> None:
            try:
                await coro
            except Exception as exc:  # noqa: BLE001 — async handler 失败同样隔离进 audit
                self._record_error(owner, event_type, exc)

        task = asyncio.create_task(_runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _record_error(self, owner: str, event_type: str, exc: Exception) -> None:
        if self._audit is None:
            return
        record = getattr(self._audit, "record", None)
        if not callable(record):
            return
        try:
            record(
                "event_handler_failed",
                f"{owner or '?'} / {event_type}: {type(exc).__name__}",
                level="warning",
                detail={"owner": owner, "event_type": event_type},
            )
        except Exception:  # noqa: BLE001 — 连记录都失败也不能反过来炸发布方
            pass

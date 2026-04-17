"""Per-session event-driven state machine.

此模块把散落在 ``LLMSessionManager`` / ``OmniOfflineClient`` 中的若干"谁在占用
当前轮次"信号（``current_speech_id`` 轮换、``session._is_responding``、
``_proactive_expected_sid`` contextvar、``last_user_activity_time``）收拢到一个
单点状态机。目的：

1. 主动搭话（proactive）流水线的 phase1/phase2 可以**零成本**（O(1) 读，无锁）
   频繁询问 ``is_proactive_preempted(claim_token)`` —— 即使在每个 LLM chunk
   之间 check，也不会引入可观测开销。
2. 把 "用户接管" / "AI 开始回复" 等信号以事件形式发布，各消费者（TTS worker、
   日志、前端 sync）可以订阅而不必直读多个字段。
3. per-catgirl（未来可扩展为 per-catgirl+user）独立实例，状态互不干扰。

本模块在 Stage 1 引入时作为 facade：事件发射点写在既有的 sid 轮换 / proactive
生命周期处，旧字段（``current_speech_id`` / ``_is_responding``）继续存在，
消费者逐步迁移（见 Stage 2）。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union


class TurnOwner(Enum):
    """谁在占用当前 turn。"""

    NONE = "none"
    USER = "user"          # 用户输入中 / AI 正在为用户回复
    PROACTIVE = "proactive"  # 主动搭话流水线持有


class ProactivePhase(Enum):
    """主动搭话流水线当前阶段（text mode；voice fudge 不走这里）。"""

    IDLE = "idle"
    PHASE1 = "phase1"            # fetch + unified LLM
    PHASE2 = "phase2"            # astream → TTS
    COMMITTING = "committing"    # finish_proactive_delivery 内


class SessionEvent(Enum):
    """写路径事件。发射方通过 ``fire()``，读路径只读 state 字段。"""

    USER_INPUT = "user_input"                  # 用户新一轮输入触发的 sid 轮换
    USER_ACTIVITY = "user_activity"            # 不轮换 sid 的用户活动（transcript 等）
    PROACTIVE_START = "proactive_start"        # 进入 phase1
    PROACTIVE_CLAIM = "proactive_claim"        # prepare 生成 sid，正式持有 turn
    PROACTIVE_PHASE2 = "proactive_phase2"      # 进入流式 TTS
    PROACTIVE_COMMITTING = "proactive_committing"  # 进入 finish_proactive_delivery
    PROACTIVE_DONE = "proactive_done"          # 主动搭话退出（成功 / pass / abort）


Subscriber = Callable[[SessionEvent, dict], Union[None, Awaitable[None]]]


@dataclass
class SessionStateMachine:
    """单个 ``(lanlan_name, user)`` 会话的事件驱动状态机。

    写路径（``fire``）持 ``_write_lock``；读路径（``is_proactive_preempted``、
    ``can_start_proactive``、``snapshot``）只读字段，无锁无 await。发射方可以
    在任意 async 上下文里 fire，订阅回调会在 apply 之后异步派发，不阻塞事件流。
    """

    lanlan_name: str
    owner: TurnOwner = TurnOwner.NONE
    phase: ProactivePhase = ProactivePhase.IDLE
    proactive_sid: Optional[str] = None
    user_sid: Optional[str] = None
    last_user_activity: float = 0.0
    _preempted: bool = False
    _subscribers: "dict[Union[SessionEvent, str], list[Subscriber]]" = field(
        default_factory=lambda: defaultdict(list)
    )
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── 读路径（O(1)，热路径）─────────────────────────────────────────
    def is_proactive_preempted(self, claim_token: Optional[str] = None) -> bool:
        """主动搭话路径是否应立刻 abort。

        Args:
            claim_token: 调用段起点 snapshot 的 sid。
                - phase1 前还没 claim 时传 ``None``，此时仅看 sticky preempt flag。
                - phase2 起传 ``prepare_proactive_delivery`` 发出的 sid。

        规则：
            1) ``_preempted`` sticky flag —— 任何 proactive 阶段期间 USER_INPUT
               事件被记下时立刻翻为 True，直到 ``PROACTIVE_DONE`` 清零。
            2) ``claim_token`` 非 None 且与当前 ``proactive_sid`` 不一致 ——
               防御性兜底，正常情况 sticky flag 已先触发。
        """
        if self._preempted:
            return True
        if claim_token is not None:
            # proactive_sid 可能为 None（start 但尚未 claim），此时不判 mismatch
            if self.proactive_sid is not None and self.proactive_sid != claim_token:
                return True
        return False

    def mark_user_input_preempt(self) -> None:
        """同步翻起 ``_preempted`` sticky flag（仅当 proactive 处于活动阶段）。

        用于 ``handle_new_message`` / ``stream_text`` 等 sid 轮换路径在持有
        ``self.lock``（保护 ``current_speech_id`` 的锁）的同一临界区内原子地
        翻起抢占标记。否则以下 race 成立喵：

        T1 持 self.lock 写新 user sid → 释放 lock → 去 await fire(USER_INPUT) …
        T2 正好这个窗口里拿到 self.lock（走 prepare_proactive_delivery 的 lock
           内 preempt 复查），看到 ``_preempted=False`` 继续往下，把刚写好的
           user sid 再覆盖成 proactive sid，用户这轮回复的 chunk/TTS 全带错 sid。

        本方法不走 ``_write_lock``：同步布尔写 + 只读判定无需跨协程同步；并且允许
        调用方在 ``self.lock`` 内无 await 完成"写 sid + 翻 flag"两步合一。完整的
        ``SessionEvent.USER_INPUT`` 仍然需要在锁外 fire，以更新 owner/user_sid/
        last_user_activity 并派发订阅者。
        """
        if self.phase in _PROACTIVE_ACTIVE_PHASES:
            self._preempted = True

    async def reset(self) -> None:
        """Teardown hook：把 SM 复位到初始态，清掉可能泄漏的 proactive 残留。

        ``LLMSessionManager`` 跨多次 ``start_session()`` / ``end_session()`` 复用
        同一个 SM 实例。若上一轮 proactive 在 PHASE1/PHASE2 时 WebSocket 意外
        断开、``PROACTIVE_DONE`` 来不及 fire，``phase`` 和 ``_preempted`` 会
        粘到下一轮会话，导致 ``can_start_proactive`` 永远返回 False。本方法
        由 ``_init_renew_status`` 调用，保证新会话拿到干净 SM。

        保留 ``_subscribers`` 和 ``last_user_activity`` —— 前者是应用层注册的
        钩子不该无故吹飞，后者跨会话单调递增有诊断价值。

        注意：当 proactive 流水线正处于活动阶段（PHASE1/PHASE2/COMMITTING）时，
        本方法是 **no-op**。这是因为 ``prepare_proactive_delivery`` 在没有现成
        session 时会 auto-start，该路径进入 ``start_session → _init_renew_status``，
        若此处无脑复位就会把 proactive 刚翻起的 phase/sticky flag 全吹掉，使后续
        phase1/phase2 的 ``is_proactive_preempted`` 彻底失效。
        """
        async with self._write_lock:
            if self.phase in _PROACTIVE_ACTIVE_PHASES:
                # 活动中的 proactive 自身负责 PROACTIVE_DONE 清理；此处跳过
                return
            self.owner = TurnOwner.NONE
            self.phase = ProactivePhase.IDLE
            self.proactive_sid = None
            self.user_sid = None
            self._preempted = False

    def can_start_proactive(self, session: Any = None) -> bool:
        """能否发起新一轮主动搭话（入口 409 前置判定用）。

        Args:
            session: 可选，当前 session（OmniOfflineClient / OmniRealtimeClient）。
                若传入且 ``_is_responding == True``，说明 AI 正在为用户回复，
                应拒绝 proactive。这一步把原来散在 router 里直读 session 字段
                的检查收拢到 SM。

        返回 False 的两种情况：
            - phase != IDLE（另一轮 proactive 在跑 / 正在 commit）
            - session._is_responding == True（AI 正在为用户回复）

        注意：**不**基于 ``owner == USER`` 判拒。USER_INPUT 事件把 owner 翻到
        USER 之后没有 AI_RESPONSE_END 事件将其复位（Stage 2 未做该项迁移），
        若此处卡 owner == USER 会导致用户发第一条消息后所有 proactive 都被
        永久 409 掉。owner 目前只用于 sticky preempt 的语义，不用于 gating。
        """
        if self.phase is not ProactivePhase.IDLE:
            return False
        if session is not None and getattr(session, "_is_responding", False):
            return False
        return True

    async def try_start_proactive(self, session: Any = None) -> bool:
        """原子地"检查 + 占坑"：仅当 ``can_start_proactive`` 返回 True 时才翻
        ``IDLE → PHASE1``，避免两次无锁检查之间的 TOCTOU 窗口导致两路并发 proactive
        同时进入 PHASE1。

        返回 True 表示本次调用抢到了 turn 所有权（已翻 PHASE1，订阅者已收到
        ``PROACTIVE_START``）；返回 False 表示另一路 proactive 抢先或 AI 正在响应，
        调用方应直接返回 409（不需要再 fire ``PROACTIVE_DONE``，因为 ``PROACTIVE_START``
        没发出）。
        """
        async with self._write_lock:
            if self.phase is not ProactivePhase.IDLE:
                return False
            if session is not None and getattr(session, "_is_responding", False):
                return False
            self._apply(SessionEvent.PROACTIVE_START, {})
            snap_subs = list(self._subscribers.get(SessionEvent.PROACTIVE_START, ())) + list(
                self._subscribers.get(_WILDCARD, ())
            )

        _dispatch_subscribers(snap_subs, SessionEvent.PROACTIVE_START, {})
        return True

    def snapshot(self) -> dict:
        """供日志 / 诊断使用的一致性快照。"""
        return {
            "lanlan_name": self.lanlan_name,
            "owner": self.owner.value,
            "phase": self.phase.value,
            "proactive_sid": self.proactive_sid,
            "user_sid": self.user_sid,
            "preempted": self._preempted,
            "last_user_activity": self.last_user_activity,
        }

    # ── 写路径 ──────────────────────────────────────────────────────
    async def fire(self, event: SessionEvent, **payload: Any) -> None:
        """发射事件；在 ``_write_lock`` 内 apply，随后在锁外异步派发订阅。

        订阅者在 apply 之后观察到的状态必然是"事件生效后"的状态。
        """
        async with self._write_lock:
            self._apply(event, payload)
            snap_subs = list(self._subscribers.get(event, ())) + list(
                self._subscribers.get(_WILDCARD, ())
            )

        _dispatch_subscribers(snap_subs, event, payload)

    def _apply(self, event: SessionEvent, payload: dict) -> None:
        """内部状态转移。调用方已持 ``_write_lock``。"""
        if event is SessionEvent.USER_INPUT:
            # 任何 proactive 阶段遇到 USER_INPUT 都 sticky preempt
            if self.phase in _PROACTIVE_ACTIVE_PHASES:
                self._preempted = True
            self.owner = TurnOwner.USER
            self.user_sid = payload.get("sid")
            self.last_user_activity = time.time()

        elif event is SessionEvent.USER_ACTIVITY:
            self.last_user_activity = time.time()
            # 不轮换 owner / sid（voice transcript 等静默信号）

        elif event is SessionEvent.PROACTIVE_START:
            # 进入 phase1：清 sticky flag，owner 翻到 proactive
            self._preempted = False
            self.owner = TurnOwner.PROACTIVE
            self.phase = ProactivePhase.PHASE1
            self.proactive_sid = None

        elif event is SessionEvent.PROACTIVE_CLAIM:
            # 只在 proactive 路径存活时 claim sid；否则丢弃（已被抢）
            sid = payload.get("sid")
            if self.phase is ProactivePhase.PHASE1 and not self._preempted:
                self.proactive_sid = sid

        elif event is SessionEvent.PROACTIVE_PHASE2:
            if self.phase is ProactivePhase.PHASE1:
                self.phase = ProactivePhase.PHASE2

        elif event is SessionEvent.PROACTIVE_COMMITTING:
            if self.phase is ProactivePhase.PHASE2:
                self.phase = ProactivePhase.COMMITTING

        elif event is SessionEvent.PROACTIVE_DONE:
            # 清 proactive 半边；owner 由最近事件决定 —— 若中途被 USER_INPUT
            # 抢占，owner 已是 USER，本事件只清 phase，不覆盖 owner。
            self.phase = ProactivePhase.IDLE
            self.proactive_sid = None
            self._preempted = False
            if self.owner is TurnOwner.PROACTIVE:
                self.owner = TurnOwner.NONE

    # ── 订阅 ────────────────────────────────────────────────────────
    def subscribe(self, event: Optional[SessionEvent], cb: Subscriber) -> None:
        """订阅事件；``event=None`` 表示订阅全部。"""
        key = _WILDCARD if event is None else event
        self._subscribers[key].append(cb)

    def unsubscribe(self, event: Optional[SessionEvent], cb: Subscriber) -> None:
        key = _WILDCARD if event is None else event
        try:
            self._subscribers[key].remove(cb)
        except (KeyError, ValueError):
            # idempotent unsubscribe：重复取消或取消未注册的 cb 视为 no-op
            return


def _dispatch_subscribers(
    subs: "list[Subscriber]",
    event: SessionEvent,
    payload: dict,
) -> None:
    """统一派发订阅者：同步抛异常静默；返回 awaitable（包括 coroutine、Task、
    Future、自定义 awaitable）统一 ``ensure_future`` 包装并挂 done-callback，
    避免 Task/Future 类型的异常绕过吞噬逻辑泄漏到事件循环。
    """
    for cb in subs:
        try:
            res = cb(event, payload)
        except Exception:
            # 订阅者同步异常不能影响事件流；订阅者自行日志/上报
            continue
        if res is None:
            continue
        if asyncio.iscoroutine(res) or asyncio.isfuture(res) or hasattr(res, "__await__"):
            # ensure_future 对 coroutine 包成 Task；对 Future 原样返回；对自定义
            # awaitable 通过 __await__ 包成 Task —— 三类都能挂 done-callback。
            try:
                fut = asyncio.ensure_future(res)
            except Exception:
                continue
            fut.add_done_callback(_swallow_subscriber_exc)


def _swallow_subscriber_exc(task: "asyncio.Task") -> None:
    """异步订阅者抛异常时静默（只取一次结果避免 warning），由订阅者自行负责上报。

    ``asyncio.CancelledError`` 是 ``BaseException`` 子类，不被 ``except Exception``
    捕获；进程关停 / 任务 cancel 路径下若不单独捕获会绕过 done-callback 继续冒泡
    触发 "Task exception was never retrieved" warning。
    """
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        # 故意吞：避免订阅者异常冒泡污染事件流，也避免 "Task exception was
        # never retrieved" 刷屏。订阅者自己负责在 callback 内部落日志。
        return


# 不对外导出 —— 内部哨兵，用于 ``subscribe(None, ...)``
_WILDCARD = "__wildcard__"

# phase 判定"proactive 正在干活、应被 USER_INPUT 抢占"的集合
_PROACTIVE_ACTIVE_PHASES = frozenset(
    {ProactivePhase.PHASE1, ProactivePhase.PHASE2, ProactivePhase.COMMITTING}
)


__all__ = [
    "ProactivePhase",
    "SessionEvent",
    "SessionStateMachine",
    "TurnOwner",
]

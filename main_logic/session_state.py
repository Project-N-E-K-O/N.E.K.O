# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Per-session event-driven state machine.

This module gathers the various "who owns the current turn" signals previously
scattered across ``LLMSessionManager`` / ``OmniOfflineClient``
(``current_speech_id`` rotation, ``session._is_responding``,
``_proactive_expected_sid`` contextvar, ``last_user_activity_time``) into a
single-point state machine. Goals:

1. The proactive-chat pipeline's phase1/phase2 can poll
   ``is_proactive_preempted(claim_token)`` at **zero cost** (O(1) read, no lock)
   — even checking between every LLM chunk introduces no observable overhead.
2. Signals like "user takes over" / "AI starts replying" are published as
   events; consumers (TTS worker, logging, frontend sync) can subscribe instead
   of reading multiple fields directly.
3. Independent per-catgirl instances (extensible to per-catgirl+user later),
   with no state interference between them.

Introduced in Stage 1 as a facade: event emission points sit at the existing
sid rotation / proactive lifecycle spots, the old fields
(``current_speech_id`` / ``_is_responding``) remain, and consumers migrate
incrementally (see Stage 2).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union


class TurnOwner(Enum):
    """Who owns the current turn."""

    NONE = "none"
    USER = "user"          # 用户输入中 / AI 正在为用户回复
    PROACTIVE = "proactive"  # 主动搭话流水线持有


class ProactivePhase(Enum):
    """Current phase of the proactive-chat pipeline (text mode; voice fudge does not go through here)."""

    IDLE = "idle"
    PHASE1 = "phase1"            # fetch + unified LLM
    PHASE2 = "phase2"            # astream → TTS
    COMMITTING = "committing"    # finish_proactive_delivery 内


class CognitionMode(Enum):
    """How hard the companion is thinking *right now* (docs/design/focus-truename-mode.md).

    Orthogonal to ``TurnOwner`` / ``ProactivePhase`` (which track *who owns
    the turn*). ``REGULAR`` is the everyday 90% light-hearted baseline
    (thinking globally disabled). ``FOCUS`` is the signal-triggered,
    user-invisible "thinking-on + stronger model" state that delivers the
    10% "arrival" moment. ``TRUE_NAME`` is the v2 destructive tier
    (persona/memory rewrite + sub-model dispatch) — declared here for
    boundary clarity but not driven by any v1 transition.
    """

    REGULAR = "regular"
    FOCUS = "focus"
    TRUE_NAME = "true_name"   # v2 — no v1 transition reaches this


class SessionEvent(Enum):
    """Write-path events. Emitters go through ``fire()``; the read path only reads state fields."""

    USER_INPUT = "user_input"                  # 用户新一轮输入触发的 sid 轮换
    USER_ACTIVITY = "user_activity"            # 不轮换 sid 的用户活动（transcript 等）
    PROACTIVE_START = "proactive_start"        # 进入 phase1
    PROACTIVE_CLAIM = "proactive_claim"        # prepare 生成 sid，正式持有 turn
    PROACTIVE_PHASE2 = "proactive_phase2"      # 进入流式 TTS
    PROACTIVE_COMMITTING = "proactive_committing"  # 进入 finish_proactive_delivery
    PROACTIVE_DONE = "proactive_done"          # 主动搭话退出（成功 / pass / abort）
    FOCUS_ENTER = "focus_enter"                # REGULAR → FOCUS（凝神进入）
    FOCUS_EXIT = "focus_exit"                  # FOCUS → REGULAR（凝神退出，附带 episode 切片定位）


Subscriber = Callable[[SessionEvent, dict], Union[None, Awaitable[None]]]


@dataclass
class SessionStateMachine:
    """Event-driven state machine for a single ``(lanlan_name, user)`` session.

    The write path (``fire``) holds ``_write_lock``; the read path
    (``is_proactive_preempted``, ``can_start_proactive``, ``snapshot``) only reads
    fields, with no locks and no awaits. Emitters may fire from any async context;
    subscriber callbacks are dispatched asynchronously after apply, without blocking
    the event flow.
    """

    lanlan_name: str
    owner: TurnOwner = TurnOwner.NONE
    phase: ProactivePhase = ProactivePhase.IDLE
    proactive_sid: Optional[str] = None
    user_sid: Optional[str] = None
    last_user_activity: float = 0.0
    _preempted: bool = False
    # ── Focus mode 凝神（docs/design/focus-truename-mode.md）─────────────
    # ``mode`` is read on the O(1) lock-free path (callers building the LLM
    # check ``sm.mode is CognitionMode.FOCUS`` before deciding thinking-on).
    # The hysteresis counters are mutated only inside ``update_focus`` under
    # ``_write_lock``.
    mode: CognitionMode = CognitionMode.REGULAR
    _focus_episode_id: Optional[str] = None
    _focus_episode_started_at: float = 0.0
    _focus_turn_count: int = 0
    _focus_charge: float = 0.0  # leaky accumulator: charge*retention + score each scored turn
    _subscribers: "dict[Union[SessionEvent, str], list[Subscriber]]" = field(
        default_factory=lambda: defaultdict(list)
    )
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── 读路径（O(1)，热路径）─────────────────────────────────────────
    def is_proactive_preempted(self, claim_token: Optional[str] = None) -> bool:
        """Whether the proactive-chat path should abort immediately.

        Args:
            claim_token: the sid snapshotted at the start of the calling section.
                - Pass ``None`` before phase1 has claimed; only the sticky preempt
                  flag is checked then.
                - From phase2 onward, pass the sid issued by
                  ``prepare_proactive_delivery``.

        Rules:
            1) ``_preempted`` sticky flag — flipped to True as soon as a USER_INPUT
               event is recorded during any proactive phase, until ``PROACTIVE_DONE``
               clears it.
            2) ``claim_token`` is non-None and differs from the current
               ``proactive_sid`` — defensive fallback; normally the sticky flag has
               already fired first.
        """
        if self._preempted:
            return True
        if claim_token is not None:
            # proactive_sid 可能为 None（start 但尚未 claim），此时不判 mismatch
            if self.proactive_sid is not None and self.proactive_sid != claim_token:
                return True
        return False

    def mark_user_input_preempt(self) -> None:
        """Synchronously flip the ``_preempted`` sticky flag (only while proactive is in an active phase).

        Used by sid-rotation paths such as ``handle_new_message`` / ``stream_text`` to
        atomically raise the preempt flag inside the same critical section that holds
        ``self.lock`` (the lock protecting ``current_speech_id``). Otherwise the
        following race holds:

        T1 holds self.lock, writes the new user sid → releases the lock → goes to
           await fire(USER_INPUT) …
        T2 grabs self.lock exactly in this window (via the in-lock preempt re-check
           of prepare_proactive_delivery), sees ``_preempted=False``, proceeds, and
           overwrites the freshly written user sid with the proactive sid — every
           chunk/TTS of the user's reply this turn now carries the wrong sid.

        This method does not take ``_write_lock``: a synchronous boolean write plus a
        read-only check needs no cross-coroutine synchronization, and this lets the
        caller complete "write sid + flip flag" as one step inside ``self.lock``
        without awaiting. The full ``SessionEvent.USER_INPUT`` must still be fired
        outside the lock to update owner/user_sid/last_user_activity and dispatch
        subscribers.
        """
        if self.phase in _PROACTIVE_ACTIVE_PHASES:
            self._preempted = True

    async def reset(self, *, force: bool = False) -> None:
        """Teardown hook: reset the SM to its initial state, clearing any leaked proactive residue.

        ``LLMSessionManager`` reuses the same SM instance across multiple
        ``start_session()`` / ``end_session()`` calls. If the previous proactive run
        was in PHASE1/PHASE2 when the WebSocket dropped unexpectedly and
        ``PROACTIVE_DONE`` never fired, ``phase`` and ``_preempted`` would stick into
        the next session, making ``can_start_proactive`` return False forever. This
        method is called by ``_init_renew_status`` to guarantee a clean SM for the
        new session.

        ``_subscribers`` and ``last_user_activity`` are preserved — the former are
        application-level hooks that should not be blown away for no reason, and the
        latter is monotonically increasing across sessions and has diagnostic value.

        Args:
            force: if ``False`` (default), this method is a **no-op** while proactive
                is in an active phase (PHASE1/PHASE2/COMMITTING); the active proactive
                run is responsible for its own ``PROACTIVE_DONE`` cleanup — this
                protects ``prepare_proactive_delivery`` from being wrongly cleared in
                certain concurrent scenarios (e.g. sporadic error recovery during
                auto-start). If ``True``, **forcibly** reset everything regardless of
                the current phase. Real session teardown (WS disconnect,
                ``end_session``) must use ``force=True`` — otherwise phase/preempt
                leaks from a stuck active proactive would block the next round's
                ``can_start_proactive``.
        """
        async with self._write_lock:
            if not force and self.phase in _PROACTIVE_ACTIVE_PHASES:
                # 活动中的 proactive 自身负责 PROACTIVE_DONE 清理；此处跳过
                return
            self.owner = TurnOwner.NONE
            self.phase = ProactivePhase.IDLE
            self.proactive_sid = None
            self.user_sid = None
            self._preempted = False
            # Focus is session-scoped: a new session always starts REGULAR
            # (restart / teardown never restores an elevated mode).
            self._clear_focus_state()

    def can_start_proactive(self, session: Any = None) -> bool:
        """Whether a new proactive-chat round can start (used as the entry-point 409 pre-check).

        Args:
            session: optional, the current session (OmniOfflineClient /
                OmniRealtimeClient). If provided and ``_is_responding == True``, the
                AI is currently replying to the user and proactive should be refused.
                This consolidates the checks that used to read session fields directly
                in the router into the SM.

        Returns False in two cases:
            - phase != IDLE (another proactive round is running / committing)
            - session._is_responding == True (the AI is replying to the user)

        Note: we do **not** reject based on ``owner == USER``. After USER_INPUT flips
        owner to USER there is no AI_RESPONSE_END event to reset it (that migration
        was not done in Stage 2); gating on owner == USER here would permanently 409
        every proactive after the user's first message. owner is currently only used
        for sticky-preempt semantics, not for gating.
        """
        if self.phase is not ProactivePhase.IDLE:
            return False
        if session is not None and getattr(session, "_is_responding", False):
            return False
        return True

    async def try_start_proactive(self, session: Any = None) -> bool:
        """Atomic "check + claim": only flips ``IDLE → PHASE1`` when ``can_start_proactive``
        returns True, avoiding the TOCTOU window between two lock-free checks that
        would let two concurrent proactive paths enter PHASE1 simultaneously.

        Returns True if this call won turn ownership (PHASE1 is set and subscribers
        have received ``PROACTIVE_START``); returns False if another proactive path
        got there first or the AI is responding, in which case the caller should
        return 409 directly (no need to fire ``PROACTIVE_DONE``, since
        ``PROACTIVE_START`` was never emitted).
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

    # ── Focus mode 凝神 ─────────────────────────────────────────────
    async def update_focus(
        self, score: float, *, topic_changed: bool = False,
        retention_override: float | None = None,
        count_turn: bool = True,
    ) -> CognitionMode:
        """Apply one Focus hysteresis tick for a just-scored turn; return the resulting mode.

        Called once per turn by BOTH trigger paths. The INLINE path
        (``stream_text``, a real user message) feeds the ``FocusScorer`` score
        ∈ [0, 1] and is the ONLY path that can raise the charge / enter Focus.
        The IDLE path (``proactive_chat``) feeds ``score=0`` with
        ``retention_override`` set to the slower idle retention: a proactive
        turn never props Focus up, it only lets an active episode cool down a
        notch. Drives the ``REGULAR ⇄ FOCUS`` transition with asymmetric
        thresholds + a hard turn cap (``config.FOCUS_*``). ``topic_changed=True``
        forces an immediate exit regardless of score.

        ``retention_override`` (when not None) replaces ``FOCUS_CHARGE_RETENTION``
        for this tick only — used by the idle cooldown so proactive turns decay
        the charge more slowly than inline turns.

        The transition itself is a pure function (``_focus_decide``); this
        method only wires config thresholds, mutates state under
        ``_write_lock``, and dispatches ``FOCUS_ENTER`` / ``FOCUS_EXIT``
        outside the lock. Returns the post-tick ``mode`` so the caller can
        immediately build the LLM thinking-on (FOCUS) or off (REGULAR).

        ``count_turn=False`` (idle cooldown) decays the charge without spending
        a ``FOCUS_HARD_CAP_TURNS`` slot — the cap bounds real inline turns, not
        proactive cooldown ticks.

        ``FOCUS_EXIT`` carries ``episode_id`` + ``episode_started_at`` so a
        future memory-side subscriber can slice the emotional episode out of
        recent history and run the additive maintenance batch (deferred; see
        docs/design/focus-truename-mode.md).
        """
        th = _focus_thresholds_from_config()
        if retention_override is not None:
            th = replace(th, retention=float(retention_override))
        emit_event: Optional[SessionEvent] = None
        emit_payload: dict = {}
        snap_subs: "list[Subscriber]" = []
        async with self._write_lock:
            if not th.enabled:
                # Master switch off: clear ALL focus residue unconditionally —
                # not just when mode==FOCUS. The leaky accumulator can sit in
                # REGULAR with _focus_charge just under the enter bar; if the
                # flag is flipped off there, leaving the charge frozen would let
                # an unrelated mild cue enter Focus on stale pre-disable evidence
                # once re-enabled. _clear_focus_state is idempotent when already
                # clean.
                self._clear_focus_state()
                return self.mode

            decision = _focus_decide(
                mode=self.mode,
                focus_turn_count=self._focus_turn_count,
                charge=self._focus_charge,
                score=score,
                topic_changed=topic_changed,
                th=th,
                count_turn=count_turn,
            )
            if decision.action is _FocusAction.ENTER:
                self.mode = CognitionMode.FOCUS
                self._focus_episode_id = f"{self.lanlan_name}-{int(time.time() * 1000)}"
                self._focus_episode_started_at = time.time()
                self._focus_turn_count = decision.turn_count
                self._focus_charge = decision.charge
                emit_event = SessionEvent.FOCUS_ENTER
                emit_payload = {"episode_id": self._focus_episode_id, "score": score,
                                "charge": decision.charge}
            elif decision.action is _FocusAction.EXIT:
                emit_event = SessionEvent.FOCUS_EXIT
                emit_payload = {
                    "episode_id": self._focus_episode_id,
                    "episode_started_at": self._focus_episode_started_at,
                    "reason": decision.reason,
                    "turns": self._focus_turn_count,
                }
                self._clear_focus_state()
            else:  # STAY — update accumulator only, no transition event
                self._focus_turn_count = decision.turn_count
                self._focus_charge = decision.charge

            if emit_event is not None:
                snap_subs = list(self._subscribers.get(emit_event, ())) + list(
                    self._subscribers.get(_WILDCARD, ())
                )
            result_mode = self.mode

        if emit_event is not None:
            _dispatch_subscribers(snap_subs, emit_event, emit_payload)
        return result_mode

    async def clear_focus(self) -> None:
        """Drop all Focus state to the REGULAR baseline WITHOUT emitting FOCUS_EXIT.

        For lifecycle resets where the conversation context itself is wiped
        (e.g. repetition recovery clears ``_conversation_history``): the
        emotional episode's evidence is gone, so charge / mode / turn count must
        not carry into the emptied conversation. No ``FOCUS_EXIT`` is fired
        because there is no coherent episode to hand to memory synthesis — this
        mirrors ``reset``, which also clears Focus silently. (The disabled /
        privacy gates use ``update_focus`` instead, which self-clears too.)
        """
        async with self._write_lock:
            self._clear_focus_state()

    def _clear_focus_state(self) -> None:
        """Reset all Focus fields to the REGULAR baseline. Caller holds ``_write_lock``."""
        self.mode = CognitionMode.REGULAR
        self._focus_episode_id = None
        self._focus_episode_started_at = 0.0
        self._focus_turn_count = 0
        self._focus_charge = 0.0

    def snapshot(self) -> dict:
        """Consistent snapshot for logging / diagnostics."""
        return {
            "lanlan_name": self.lanlan_name,
            "owner": self.owner.value,
            "phase": self.phase.value,
            "proactive_sid": self.proactive_sid,
            "user_sid": self.user_sid,
            "preempted": self._preempted,
            "last_user_activity": self.last_user_activity,
            "mode": self.mode.value,
            "focus_turn_count": self._focus_turn_count,
            "focus_charge": round(self._focus_charge, 3),
            "focus_episode_id": self._focus_episode_id,
        }

    # ── 写路径 ──────────────────────────────────────────────────────
    async def fire(self, event: SessionEvent, **payload: Any) -> None:
        """Fire an event; applied inside ``_write_lock``, then dispatched to subscribers asynchronously outside the lock.

        The state subscribers observe after apply is guaranteed to be the
        "post-event" state.
        """
        async with self._write_lock:
            self._apply(event, payload)
            snap_subs = list(self._subscribers.get(event, ())) + list(
                self._subscribers.get(_WILDCARD, ())
            )

        _dispatch_subscribers(snap_subs, event, payload)

    def _apply(self, event: SessionEvent, payload: dict) -> None:
        """Internal state transition. Caller already holds ``_write_lock``."""
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
        """Subscribe to events; ``event=None`` means subscribe to all."""
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
    """Unified subscriber dispatch: sync exceptions are swallowed silently; any returned
    awaitable (coroutine, Task, Future, custom awaitable) is wrapped via
    ``ensure_future`` with a done-callback attached, so Task/Future-type exceptions
    cannot bypass the swallowing logic and leak into the event loop.
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
    """Silently swallow async subscriber exceptions (retrieve the result once to avoid the warning); subscribers are responsible for their own reporting.

    ``asyncio.CancelledError`` is a ``BaseException`` subclass and is not caught by
    ``except Exception``; on process shutdown / task-cancel paths, failing to catch it
    separately would let it bypass the done-callback and bubble up, triggering the
    "Task exception was never retrieved" warning.
    """
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        # 故意吞：避免订阅者异常冒泡污染事件流，也避免 "Task exception was
        # never retrieved" 刷屏。订阅者自己负责在 callback 内部落日志。
        return


# ── Focus hysteresis: pure transition core ──────────────────────────
# Kept as a free function (no SM state, no lock, no config import) so the
# REGULAR ⇄ FOCUS logic is unit-testable in isolation with explicit
# thresholds. ``SessionStateMachine.update_focus`` wires config + locking
# + event dispatch around it.

class _FocusAction(Enum):
    STAY = "stay"
    ENTER = "enter"
    EXIT = "exit"


@dataclass(frozen=True)
class FocusThresholds:
    """Snapshot of the Focus tuning knobs for one decision (from ``config.FOCUS_*``)."""

    enabled: bool
    retention: float       # leaky-accumulator retention per turn (0..1)
    enter: float           # charge >= enter ⇒ REGULAR → FOCUS
    exit: float            # charge < exit (while FOCUS) ⇒ FOCUS → REGULAR
    hard_cap_turns: int


@dataclass(frozen=True)
class _FocusDecision:
    action: _FocusAction
    turn_count: int        # focus_turn_count to store if this decision is applied
    charge: float          # post-tick charge to store if this decision is applied
    reason: str = ""       # exit cause: "charge"/"topic_switch"/"hard_cap"/"decayed"


def _focus_decide(
    *,
    mode: CognitionMode,
    focus_turn_count: int,
    charge: float,
    score: float,
    topic_changed: bool,
    th: FocusThresholds,
    count_turn: bool = True,
) -> _FocusDecision:
    """Pure leaky-accumulator transition for one scored turn.

    Each turn integrates the score into a leaky charge:
    ``new_charge = charge * retention + score`` (capped at ``enter`` so a long
    heavy episode can't build an over-long decay tail). Entry: ``REGULAR`` +
    ``new_charge >= enter`` ⇒ ENTER — so scattered mild cues accumulate to the
    bar over several turns, while one strong message (score ≈ enter) enters at
    once. Exit while ``FOCUS``: an explicit topic switch, the hard turn cap, or
    ``new_charge < exit`` (the charge has leaked away as the signal faded).
    Otherwise STAY. ``TRUE_NAME`` (v2) is inert here.

    The leak replaces the old "K consecutive low turns" streak, which a single
    noisy mid-score turn could reset and thereby stick focus on indefinitely.
    """
    if topic_changed:
        # An explicit subject switch ends the OLD emotional thread — no charge
        # leaks across the pivot. But the new topic may itself be vulnerable
        # ("对了，我撑不住了" / "btw I'm overwhelmed"), so the current turn's
        # score still seeds the new topic from a clean slate (a topic-switch
        # opener must not make Focus deaf to the new topic's own evidence).
        if mode is CognitionMode.FOCUS:
            # Pivot away from the active episode ends it; the new topic
            # re-accumulates from REGULAR starting next turn.
            return _FocusDecision(_FocusAction.EXIT, turn_count=0, charge=0.0, reason="topic_switch")
        new_charge = min(score, th.enter)
        if new_charge >= th.enter:
            return _FocusDecision(_FocusAction.ENTER, turn_count=1, charge=new_charge, reason="charge")
        return _FocusDecision(_FocusAction.STAY, turn_count=focus_turn_count, charge=new_charge)

    new_charge = min(charge * th.retention + score, th.enter)

    if mode is CognitionMode.REGULAR:
        if new_charge >= th.enter:
            return _FocusDecision(_FocusAction.ENTER, turn_count=1, charge=new_charge, reason="charge")
        return _FocusDecision(_FocusAction.STAY, turn_count=focus_turn_count, charge=new_charge)

    if mode is CognitionMode.FOCUS:
        if focus_turn_count >= th.hard_cap_turns:
            return _FocusDecision(_FocusAction.EXIT, turn_count=0, charge=0.0, reason="hard_cap")
        if new_charge < th.exit:
            return _FocusDecision(_FocusAction.EXIT, turn_count=0, charge=0.0, reason="decayed")
        # ``count_turn=False`` (idle cooldown) decays the charge WITHOUT spending
        # a hard-cap turn slot — the cap bounds real inline conversation turns,
        # not proactive cooldown ticks (a silent scheduler poll must not age the
        # episode toward the hard cap).
        return _FocusDecision(
            _FocusAction.STAY,
            turn_count=focus_turn_count + (1 if count_turn else 0),
            charge=new_charge,
        )

    # TRUE_NAME (v2) or any future mode — focus machine does not act.
    return _FocusDecision(_FocusAction.STAY, turn_count=focus_turn_count, charge=charge)


def _focus_thresholds_from_config() -> FocusThresholds:
    """Read the live Focus knobs from ``config`` (call-time read so tests can monkeypatch)."""
    import config
    return FocusThresholds(
        enabled=bool(config.FOCUS_MODE_ENABLED),
        retention=float(config.FOCUS_CHARGE_RETENTION),
        enter=float(config.FOCUS_CHARGE_ENTER),
        exit=float(config.FOCUS_CHARGE_EXIT),
        hard_cap_turns=int(config.FOCUS_HARD_CAP_TURNS),
    )


# 不对外导出 —— 内部哨兵，用于 ``subscribe(None, ...)``
_WILDCARD = "__wildcard__"

# phase 判定"proactive 正在干活、应被 USER_INPUT 抢占"的集合
_PROACTIVE_ACTIVE_PHASES = frozenset(
    {ProactivePhase.PHASE1, ProactivePhase.PHASE2, ProactivePhase.COMMITTING}
)


__all__ = [
    "CognitionMode",
    "FocusThresholds",
    "ProactivePhase",
    "SessionEvent",
    "SessionStateMachine",
    "TurnOwner",
]

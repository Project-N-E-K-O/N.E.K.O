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

"""Per-character user-activity tracker.

Combines the process-wide ``SystemSignalCollector`` with session-scoped
hooks (user/AI message timestamps, voice mode + RMS) and asks the
``ActivityStateMachine`` to emit a snapshot.

One ``UserActivityTracker`` exists per ``LLMSessionManager`` (so per
character). The collector singleton is shared. Tracker instances are
cheap — ~a few KB of buffers — so spinning one up for every active
character is fine.

Hook contract
-------------

Callers (mostly ``main_logic/core.py``) invoke these short, synchronous
methods at the points where signals occur:

  * ``on_user_message()``  — when the user submits text or finalises voice
  * ``on_ai_message()``    — when the AI's reply turn ends
  * ``on_voice_mode(active=True/False)``  — when entering / leaving voice
  * ``on_voice_rms()``     — when RMS / VAD detects user is speaking
  * ``on_screenshot()``    — placeholder for v2 (vision-described frames)

System signals (window, idle, CPU) are pulled at snapshot time from the
collector — there's no separate update path for those.

Snapshot consumer
-----------------

Only the proactive-chat code path calls ``get_snapshot()``. It runs on
the order of seconds (not milliseconds), so the small per-call cost of
running the state-machine classifier is irrelevant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import replace as dc_replace

from main_logic.activity.snapshot import (
    ActivitySnapshot, ActivityState, AntiSlackPending, WorkBreakPending,
)
from main_logic.activity.state_machine import (
    ActivityStateMachine, observation_from_system,
)
from main_logic.activity.system_signals import (
    SystemSignalCollector, SystemSnapshot, get_system_signal_collector,
)
from utils.activity_config import get_activity_preferences

logger = logging.getLogger(__name__)


# Conversation buffers: small enough to keep prompt sizes tight, large
# enough to give the emotion-tier LLM real recent context.
_CONV_BUFFER_MAXLEN = 12

# How often the activity_guess background loop wakes up. The user
# specifically asked for 20s polling. The loop itself short-circuits
# when the state signature hasn't changed, so the LLM cost only adds
# up when activity is actually shifting.
_ACTIVITY_GUESS_TICK_SECONDS = 20.0

# After computing activity_guess, suppress recompute for at least this
# long even if signature changes — protects against thrashing during
# rapid window flicker (a 30s minimum interval between LLM calls).
_ACTIVITY_GUESS_MIN_REFRESH_SECONDS = 30.0

# Frontend-pushed external signals are considered fresh for this many
# seconds. After that the tracker falls back to the local collector
# (which on remote deployments will be in degraded mode) — better to
# advertise "no signal" than to keep using stale window data.
#
# 15s = 3× the 5s heartbeat. The push pipeline stacks two unsynchronised
# 5s timers — the NEKO-PC bridge sampler (reads OS signals) and the
# renderer heartbeat (reads the bridge's cached snapshot + POSTs) — so
# worst-case data age can already approach ~10-12s before any loss. 15s
# therefore tolerates ~2 consecutive dropped pushes before falling back.
# Shorter (e.g. 10s) would thrash between fresh/degraded on a single
# drop over a lossy remote link; 30s keeps trusting a stale "user
# active" snapshot for too long after the heartbeat dies. 15s balances
# faster stale-detection against fallback thrash.
_EXTERNAL_SIGNAL_TTL_SECONDS = 15.0

# Minimum interval between accepted external-signal pushes for a given
# lanlan_name. Tuned together with the frontend heartbeat: the Electron
# preload pushes every ~5s, so anything more frequent is either a buggy
# client (re-entering the heartbeat) or spam. Enforced by the
# ``/api/activity_signal`` endpoint, not the tracker itself — the
# tracker is happily idempotent and just overwrites the last push.
#
# Pairs with TTL above: TTL is the "data freshness" window, this is the
# "request frequency" cap. TTL is 3× this interval, so the tracker
# tolerates ~2 consecutive rate-limited/dropped pushes and still has
# data within the freshness window.
_EXTERNAL_SIGNAL_MIN_INTERVAL = 5.0


# ── Break-reminder defaults ─────────────────────────────────────────
# Override per-character via ``user_preferences.json::__global_conversation__::activity::thresholds``.
# All values are minutes / seconds; the probability has its own dedicated
# field on ``ActivityPreferences`` because 0 is meaningful (disabled) and
# the threshold parser rejects ≤0.

# Cumulative focused_work minutes that arms the water-break reminder.
# Once armed, the next proactive_chat round in focused_work fires the
# minimal-Phase-2 nudge and resets the accumulator.
_WORK_BREAK_MINUTES = 30

# How long a queued ``WorkBreakPending`` stays valid if proactive_chat
# never fires. After this window the pending is dropped (the user has
# clearly stopped focusing or the moment passed); the accumulator stays
# at its current value so the next focused_work stretch can re-arm.
_WORK_BREAK_PENDING_WINDOW_SECONDS = 5 * 60

# Minimum focused_work session length before exiting it can fire an
# anti-slack reminder. Below this, the user probably opened the wrong
# window by accident — don't lecture them.
_ANTI_SLACK_MIN_FOCUS_MINUTES = 5

# Per-character cooldown after a successful anti-slack delivery. Decoupled
# from the water-break / mini-game cooldowns so the two reminder types
# don't accidentally throttle each other.
_ANTI_SLACK_COOLDOWN_MINUTES = 15

# How long a queued ``AntiSlackPending`` stays valid. The transition is
# the trigger; if proactive doesn't fire within this window the moment
# is gone (user has already settled into the new activity for a while).
_ANTI_SLACK_PENDING_WINDOW_SECONDS = 5 * 60

# Probability that a fired water-break reminder pivots into a "rest +
# mini-game invite" branch instead of the regular drink/stretch nudge.
# Falls through to ActivityPreferences override (0 disables).
_WORK_BREAK_GAME_INVITE_PROBABILITY = 0.5

# Cap on per-tick accumulator advance. Filters clock jumps / process
# suspends / first-call jitter so a long gap between ticks doesn't
# silently credit minutes the user didn't actually spend focused.
_BREAK_REMINDER_TICK_MAX_DELTA_SECONDS = 30.0

# States that count as "leisure" for anti-slack transition detection.
# Idle is intentionally excluded — sitting at the desk staring is often
# thinking, not slacking — as are voice_engaged / chatting (they are
# producing). transitioning is excluded because it's not an end state.
_ANTI_SLACK_LEISURE_STATES: frozenset[str] = frozenset({
    'casual_browsing', 'gaming',
})


def _privacy_mode_active() -> bool:
    """Whether the user has enabled privacy mode. The whole tracker should short-circuit when on.

    Stored as the inverse of the frontend ``proactiveVisionEnabled`` (see utils.preferences).
    The exception path is fail-closed: any read error is treated as "privacy mode on" —
    better to lose the tracker briefly than to let "can't read the setting" be equivalent
    to "user didn't enable privacy". The normal "user didn't enable privacy" path goes
    through ``is_privacy_mode_enabled`` returning False and never enters the except branch.
    """
    try:
        from utils.preferences import is_privacy_mode_enabled
        return is_privacy_mode_enabled()
    except Exception as e:
        logger.warning(
            'privacy mode check failed, defaulting to enabled (fail-closed): %s', e,
        )
        return True


def _proactive_chat_enabled() -> bool:
    """Whether the proactive-chat master switch is on.

    The emotion-tier LLM narration from ``activity_guess`` only feeds the state_section of
    proactive Phase 2 — there is no other consumer, so computing it while proactive chat is
    off is pure waste. The loop uses this to skip the LLM part, so when "the experiment
    group kicks the loop for context prompts but the user hasn't enabled proactive chat",
    only the cheap rule polling + context prompt detection remain, with zero LLM cost.

    Fail-open: a missing key *or* a read error both return True. Misjudging "off" would
    swallow the activity narration a proactive-on user should get (hurts a user-visible
    feature), while misjudging "on" merely computes one extra narration (small cost), so
    we prefer "compute too much". Users who explicitly turned proactive chat off have
    key=false (the frontend always syncs this key) and still take the skip branch, so the
    cost fix keeps working for the mainstream case; only brand-new sessions that never
    synced settings fall into the narrow missing→True path, which is negligible.
    """
    try:
        from utils.preferences import load_global_conversation_settings
        return bool(load_global_conversation_settings().get('proactiveChatEnabled', True))
    except Exception as e:
        logger.debug('proactive_chat_enabled check failed, defaulting to True: %s', e)
        return True


class UserActivityTracker:
    """Per-character activity inference engine.

    Lifecycle: created when ``LLMSessionManager`` is constructed; lives
    as long as that manager does. The shared system collector is
    started lazily on first ``get_snapshot()`` call so unit tests that
    only construct a tracker don't spin up a poller.
    """

    def __init__(
        self,
        lanlan_name: str,
        *,
        collector: SystemSignalCollector | None = None,
    ) -> None:
        """
        Parameters
        ----------
        lanlan_name:
            Character handle this tracker is bound to. Used for log
            attribution; the tracker itself doesn't reach into character
            state.
        collector:
            Optional collector injection — defaults to the process
            singleton. Kept overridable so tests can pass a fake.
        """
        self.lanlan_name = lanlan_name
        self._sm = ActivityStateMachine()
        self._collector = collector or get_system_signal_collector()
        self._collector_started = False

        # Conversation buffers for emotion-tier LLM enrichment input.
        # Tuples of (timestamp, text). User-side captures whatever the
        # voice transcript / text message handler passes through;
        # AI-side mirrors the per-turn buffer at turn-end time.
        self._user_msg_buffer: deque[tuple[float, str]] = deque(maxlen=_CONV_BUFFER_MAXLEN)
        self._ai_msg_buffer: deque[tuple[float, str]] = deque(maxlen=_CONV_BUFFER_MAXLEN)

        # open_threads cache. ``_conv_seq`` increments on EITHER side of
        # the conversation moving (``on_user_message`` OR ``on_ai_message``)
        # — open threads can be opened by AI promises and abandoned
        # mid-sentences from either party, not just user replies.
        # ``_open_threads_computed_at_seq`` records the seq at the
        # moment of the last successful compute. When seqs match, the
        # cache is fresh; mismatch → kickoff is allowed to spawn a new
        # compute.
        self._conv_seq: int = 0
        self._open_threads_cache: list[str] = []
        self._open_threads_computed_at_seq: int = -1
        self._open_threads_task: asyncio.Task | None = None

        # activity_guess cache. Stale check uses a state-signature tuple
        # (state, active app, idle bucket) — when unchanged for a tick
        # AND we recently computed, the loop short-circuits before
        # paying the LLM cost.
        self._activity_scores_cache: dict[str, float] = {}
        self._activity_guess_cache: str = ''
        self._activity_guess_state_sig: tuple | None = None
        self._activity_guess_at: float = 0.0
        self._activity_guess_loop_task: asyncio.Task | None = None
        self._topic_candidate_task: asyncio.Task | None = None

        # Frontend-pushed system signal (for remote deployments where the
        # backend's local OS APIs see only the server, not the user).
        # When fresh (<= _EXTERNAL_SIGNAL_TTL_SECONDS), this overrides
        # the local collector entirely. Stale → fall back to collector
        # (which on a remote backend reports os_signals_available=False
        # and the state machine's snapshot makes that explicit).
        self._external_system_snap: SystemSnapshot | None = None

        # ── Break-reminder accumulator + transition tracking ────────
        # Single timestamp drives both: each tick computes
        # ``now - _break_tick_last_at`` and credits the delta (capped) to
        # the appropriate timer based on the current state. 0.0 = "first
        # tick, no delta to credit yet".
        self._break_tick_last_at: float = 0.0

        # Cumulative focused_work seconds. Accumulates while state ==
        # focused_work, OR state == transitioning AND accumulator > 0
        # (transitioning extends an in-progress focus session — quick
        # window flicks don't break the timer). Any other state resets
        # to 0 immediately. See ``_tick_break_reminders``.
        self._work_acc_seconds: float = 0.0

        # State + session bookkeeping. ``_last_known_state`` lets us
        # detect transitions across ticks; ``_focused_work_session_*``
        # let us report how long the just-ended focused_work session
        # was when an anti-slack transition fires.
        self._last_known_state: ActivityState | None = None
        self._focused_work_session_started_at: float | None = None
        self._focused_work_session_app: str | None = None

        # Anti-slack cooldown: epoch time of the last successful
        # delivery. Until ``now - this >= cooldown_seconds`` no new
        # AntiSlackPending is emitted (the cooldown gate runs at
        # transition-detection time, not at delivery — symmetrical with
        # the mini-game invite cooldown shape).
        self._anti_slack_last_fired_at: float = 0.0

        # Pending payloads. Lifecycle:
        #   * Set when conditions trigger (water: focused_work +
        #     accumulator past threshold; anti-slack: focused_work →
        #     leisure transition past min focus + cooldown OK).
        #   * Cleared by ``mark_work_break_used`` /
        #     ``mark_anti_slack_used`` after successful delivery.
        #   * Auto-cleared at tick time when the validity window
        #     (``_WORK_BREAK_PENDING_WINDOW_SECONDS`` / ``_ANTI_SLACK_*``)
        #     expires or the state changes to one that invalidates the
        #     pending (e.g. anti-slack pending dies if user returns
        #     to focused_work).
        # Stored as dicts (not frozen dataclasses) so we can stamp a
        # ``set_at`` timestamp for window-expiry checks; the snapshot
        # builds the frozen WorkBreakPending / AntiSlackPending from
        # these.
        self._work_break_pending: dict | None = None
        self._anti_slack_pending: dict | None = None

        # ── 情境弹窗（A/B 实验组前端用）──────────────────────────
        # 当用户「进入」游戏/娱乐 或「进入」专注工作时，给前端推一次性信号，让前端
        # （仅实验组、每会话每类一次）弹窗问要不要开/关屏幕分享来源。后端只负责检测
        # 「进入」这一刻并推送，分组判定 + 去重都在前端。
        #   * ``_context_prompt_pending``：一次性槽位，由 ``_tick_break_reminders``
        #     在检测到进入目标状态时 set（同步安全，只置 dict），由异步
        #     ``_activity_guess_loop`` 心跳 drain 后 await 推送回调。后写覆盖前写
        #     （两次 drain 间多次切换只推最新那次，够用）。
        #   * ``_on_context_prompt``：core.py 注入的 async 回调，签名 ``(context: str)``
        #     —— context 取 'play'（游戏/娱乐）或 'work'（专注工作）。未注入则不推。
        self._context_prompt_pending: dict | None = None
        self._on_context_prompt: Callable[[str], Awaitable[None]] | None = None
        # 情境弹窗专属的「上一状态」基线，独立于 break-reminder 的 _last_known_state
        # ——这样可以在每个 session 开始时单独清掉（reset_context_prompt_baseline），让
        # 「跨 session 仍在同一状态」也能重新算作一次「进入」并再弹（前端按 app 会话去
        # 重），同时不扰动 break/anti-slack 的状态机。
        self._context_prompt_last_state: ActivityState | None = None

    # ── hooks (called from core.py and friends) ─────────────────

    def on_user_message(self, *, text: str | None = None, now: float | None = None) -> None:
        """Stamp a "user said something" event.

        Drives the focused_work `recent_input` heuristic, the
        ``seconds_since_user_msg`` field, and (when ``text`` is given)
        the conversation buffer the emotion-tier LLM enrichment reads
        from. Also bumps ``_conv_seq`` so the next
        ``kickoff_open_threads_compute`` call knows the cache is stale.
        """
        ts = now if now is not None else time.time()
        self._sm.update_user_message(now=ts)
        self._conv_seq += 1
        # 隐私模式：让用户消息文本直接不进 buffer，避免在切回非隐私模式时
        # 旧数据被 enrichment LLM 二次曝光。state machine 的时间戳还要更新
        # （下游 idle / focused_work 判定依赖），文本扔了即可。
        if text and not _privacy_mode_active():
            cleaned = text.strip()[:1000]
            self._user_msg_buffer.append((ts, cleaned))

    def on_ai_message(self, *, text: str | None = None, now: float | None = None) -> None:
        """Stamp an "AI just spoke" event.

        ``text`` is optional. When provided, the state machine runs the
        question heuristic over it: if the AI's reply trips the heuristic
        (ends with ``?`` / ``？`` / a CN sentence-final question particle),
        an unfinished-thread record opens — Phase 2 will be allowed up to
        ``UNFINISHED_THREAD_MAX_FOLLOWUPS`` (default 1) follow-up within
        the 5-minute window even in restricted_screen_only states.

        Text is also appended to the AI conversation buffer so the
        emotion-tier LLM enrichment has recent context to reason over.
        """
        ts = now if now is not None else time.time()
        self._sm.update_ai_message(text=text, now=ts)
        if text and not _privacy_mode_active():
            cleaned = text.strip()[:1000]
            self._ai_msg_buffer.append((ts, cleaned))
            # AI also opens threads (promises, abandoned mid-sentences) →
            # bump _conv_seq so kickoff_open_threads_compute will recompute.
            # Empty / no-text turns (errors / silenced) skip the bump,
            # since nothing in the buffer changed.
            self._conv_seq += 1

    def is_private_activity_active(self) -> bool:
        """Whether the latest activity state is private/redacted."""
        try:
            return self._sm._current_state == 'private'
        except Exception:
            return True

    def mark_unfinished_thread_used(self) -> None:
        """Record that a proactive emission just used the override slot.

        Called by ``main_routers/system_router.py`` after a successful
        proactive turn whenever the snapshot's ``unfinished_thread`` was
        active going in. Increments the per-thread follow-up counter;
        once the cap is hit, the state machine drops the thread record
        and the override is no longer offered to the prompt.
        """
        self._sm.mark_unfinished_thread_used()

    def on_voice_mode(self, active: bool) -> None:
        """Toggle voice-mode flag.

        Called when ``LLMSessionManager`` starts/stops a voice session.
        Without this, ``voice_engaged`` cannot fire — the state machine
        treats voice mode as a hard prerequisite.
        """
        self._sm.update_voice_mode(active)

    def on_voice_rms(self, *, now: float | None = None) -> None:
        """Mark user voice activity (RMS / VAD over threshold).

        Called whenever the audio capture path detects the user is
        speaking. Tracker only stores the most recent timestamp;
        ``VOICE_ACTIVE_WINDOW_SECONDS`` decides what counts as "current".
        """
        self._sm.update_voice_rms(now=now)

    def on_screenshot(self, *, now: float | None = None) -> None:
        """Hook for vision-described screenshots.

        v1: no-op. v2 will feed a brief description into a side buffer
        so the state-machine reasons can quote what's on screen. Left
        as a method so the integration sites in core.py can be wired
        now and start emitting events.
        """
        # Intentionally empty — v1 keeps this rules-only.
        return None

    def push_external_system_signal(
        self,
        *,
        window_title: str | None = None,
        process_name: str | None = None,
        idle_seconds: float | None = None,
        cpu_avg_30s: float | None = None,
        gpu_utilization: float | None = None,
        now: float | None = None,
    ) -> None:
        """Inject OS signals from outside the backend (frontend push).

        For remote-deployment scenarios where the Python backend isn't
        running on the user's machine: ``GetForegroundWindow`` and
        friends would report the *server's* state, useless for tracking
        the user. The expected pattern is:

          1. The frontend (Electron / browser / mobile shell) reads its
             local-OS signals — active window title + owning process,
             system idle seconds, GPU utilisation.
          2. It POSTs them to the backend on a heartbeat (~5-10s).
          3. The endpoint calls this method.

        Each push refreshes the timestamp; staleness past
        ``_EXTERNAL_SIGNAL_TTL_SECONDS`` causes the tracker to fall
        back to the local collector (which on remote backends will
        report ``os_signals_available=False`` so the prompt can adapt).

        All fields are optional — pass whatever the frontend can read.
        Missing fields fall through to neutral defaults; ``window_title``
        and ``process_name`` being None means "no foreground window
        right now" (legitimate — e.g., desktop visible).
        """
        ts = now if now is not None else time.time()
        self._external_system_snap = SystemSnapshot(
            timestamp=ts,
            idle_seconds=idle_seconds if idle_seconds is not None else 0.0,
            cpu_avg_30s=cpu_avg_30s if cpu_avg_30s is not None else 0.0,
            cpu_instant=cpu_avg_30s if cpu_avg_30s is not None else 0.0,
            window_title=window_title,
            process_name=process_name,
            gpu_utilization=gpu_utilization,
            os_signals_available=True,
        )

    # ── snapshot ────────────────────────────────────────────────

    async def get_snapshot(self, *, now: float | None = None) -> ActivitySnapshot:
        """Pull system signals and emit a fresh snapshot.

        Async because it ensures the system collector has been started
        (a one-shot ``await`` on first call). Subsequent calls are
        effectively synchronous. The returned snapshot has cached
        emotion-tier enrichment fields (``activity_scores``,
        ``activity_guess``, ``open_threads``) merged in — except when
        the resolved state is ``private``, in which case enrichment
        is suppressed (LLM input + cached output both bypassed) so the
        user's secret context never reaches the model.
        """
        await self._ensure_collector_started()
        self._refresh_prefs()

        ts = now if now is not None else time.time()

        sys_snap = self._select_system_snapshot(ts)
        self._sm.update_system(sys_snap)
        self._sm.update_window(
            observation_from_system(sys_snap, self._sm._prefs),
            now=ts,
        )

        snap = self._sm.get_snapshot(now=ts)
        # Tick break-reminder accumulator + transition detection BEFORE
        # building pending fields. Done after sm.get_snapshot so we have
        # the resolved state (focused_work / leisure / etc) to drive
        # accumulator and transition logic.
        self._tick_break_reminders(snap, now=ts)
        if snap.state == 'private':
            # Privacy lockdown — explicitly empty enrichment fields rather
            # than splicing in caches built from earlier (non-private)
            # state. Even though state machine drops the title/process
            # at update_window, the cached enrichment narrative might
            # still reference what the user was doing 30s ago, which
            # could leak intent ("master is logging into bank...").
            # Pending break-reminder fields also dropped — no proactive
            # interrupt while a sensitive app is foreground.
            return dc_replace(
                snap,
                activity_scores={},
                activity_guess='',
                open_threads=[],
                work_break_pending=None,
                anti_slack_pending=None,
            )
        # Patch in emotion-tier enrichment caches. ``snap`` is a frozen
        # dataclass; ``replace`` returns a new instance without mutating
        # the original. Callers always get a self-consistent snapshot.
        return dc_replace(
            snap,
            activity_scores=dict(self._activity_scores_cache),
            activity_guess=self._activity_guess_cache,
            open_threads=list(self._open_threads_cache),
            work_break_pending=self._build_work_break_pending(),
            anti_slack_pending=self._build_anti_slack_pending(),
        )

    def get_snapshot_sync(self, *, now: float | None = None) -> ActivitySnapshot:
        """Synchronous variant for callers outside an event loop.

        Useful for unit tests and any sync-context debug logging. Skips
        the collector-start guard — callers must ensure collection is
        running, or accept that ``SystemSnapshot`` defaults will be in
        play. Enrichment caches are merged in the same way as
        ``get_snapshot``, with the same private-state suppression.
        """
        self._refresh_prefs()
        ts = now if now is not None else time.time()
        # Use _select_system_snapshot to honour frontend-pushed signals
        # exactly like the async path — otherwise remote deployments
        # would silently fall back to the local (server-side) collector
        # in sync callers.
        sys_snap = self._select_system_snapshot(ts)
        self._sm.update_system(sys_snap)
        self._sm.update_window(
            observation_from_system(sys_snap, self._sm._prefs),
            now=ts,
        )
        snap = self._sm.get_snapshot(now=ts)
        self._tick_break_reminders(snap, now=ts)
        if snap.state == 'private':
            return dc_replace(
                snap,
                activity_scores={},
                activity_guess='',
                open_threads=[],
                work_break_pending=None,
                anti_slack_pending=None,
            )
        return dc_replace(
            snap,
            activity_scores=dict(self._activity_scores_cache),
            activity_guess=self._activity_guess_cache,
            open_threads=list(self._open_threads_cache),
            work_break_pending=self._build_work_break_pending(),
            anti_slack_pending=self._build_anti_slack_pending(),
        )

    # ── break-reminder accumulator + transition detection ──────────

    def _tick_break_reminders(self, snap: ActivitySnapshot, *, now: float) -> None:
        """Advance the focused_work accumulator and detect leisure transitions.

        Idempotent and tolerant of arbitrary call frequency: per-call
        delta is bounded by ``_BREAK_REMINDER_TICK_MAX_DELTA_SECONDS``,
        so a long gap between calls (process suspend, idle deployment,
        first-call bootstrap) doesn't silently credit minutes the user
        didn't actually spend focused.

        Called from ``get_snapshot``, ``get_snapshot_sync``, and the 20s
        ``_activity_guess_loop`` — the latter ensures state transitions
        are caught even when no proactive_chat round queries the tracker.

        Reads thresholds via ``self._sm._prefs.thresholds`` so user
        edits to ``user_preferences.json`` take effect on the next
        cache reload tick (mirrors how the state machine handles
        live-edit user overrides).
        """
        thresholds = self._sm._prefs.thresholds

        # Resolve thresholds with code-default fallbacks. Live-edit safe:
        # _refresh_prefs runs on every get_snapshot path; threshold
        # constants reload via the activity_config 30s cache.
        work_break_seconds = float(
            thresholds.get('work_break_minutes', _WORK_BREAK_MINUTES)
        ) * 60.0
        work_break_window = float(
            thresholds.get('work_break_pending_window_seconds', _WORK_BREAK_PENDING_WINDOW_SECONDS)
        )
        anti_slack_min_focus_seconds = float(
            thresholds.get('anti_slack_min_focus_minutes', _ANTI_SLACK_MIN_FOCUS_MINUTES)
        ) * 60.0
        anti_slack_cooldown_seconds = float(
            thresholds.get('anti_slack_cooldown_minutes', _ANTI_SLACK_COOLDOWN_MINUTES)
        ) * 60.0
        anti_slack_window = float(
            thresholds.get('anti_slack_pending_window_seconds', _ANTI_SLACK_PENDING_WINDOW_SECONDS)
        )

        state = snap.state

        # Capture accumulator BEFORE advance/reset. Used as the
        # authoritative session length when the anti-slack branch fires
        # below — wall-clock ``now - session_started_at`` would inflate
        # after a long process suspend / sleep / stall (the gap discard
        # in the advance block prevents the accumulator from ticking
        # through the dead window, but ``session_started_at`` still
        # points at pre-suspend time). Codex P1 review: PR #1226.
        session_acc_at_start = self._work_acc_seconds

        # ── Accumulator advance ─────────────────────────────────
        # First tick has no delta to credit — record now and exit. The
        # next call computes a real delta against this point.
        if self._break_tick_last_at == 0.0:
            self._break_tick_last_at = now
        else:
            raw_delta = now - self._break_tick_last_at
            self._break_tick_last_at = now
            # Ignore zero / negative (clock jump) and overlong (suspended
            # process / forgot-to-tick) gaps. Either bucket means the
            # accumulator can't safely advance: we don't actually know
            # what state the user was in during that gap.
            if 0 < raw_delta <= _BREAK_REMINDER_TICK_MAX_DELTA_SECONDS:
                if state == 'focused_work':
                    self._work_acc_seconds += raw_delta
                elif state == 'transitioning' and self._work_acc_seconds > 0:
                    # Transitioning during a real focus session = quick
                    # IDE↔terminal↔browser-docs flick. Don't break the
                    # streak. (When acc=0 we never started a session, so
                    # transitioning by itself can't kick one off.)
                    self._work_acc_seconds += raw_delta
                else:
                    # Any other state immediately resets — per user spec.
                    self._work_acc_seconds = 0.0
            else:
                # Unsafe delta — two buckets, same conservative cleanup:
                #   * ``raw_delta > cap`` — long gap (process suspend /
                #     sleep / forgot-to-tick). Don't know what state
                #     the user was in during the gap.
                #   * ``raw_delta <= 0`` — non-monotonic clock (NTP
                #     rollback, manual time change, duplicate ts). Can't
                #     credit; pre-rollback focus also can't be trusted
                #     to extend through the inverted segment.
                # In both cases, allowing the in-range branch above to
                # not run means the "any other state immediately resets"
                # rule never fires for non-focus ticks — pre-transition
                # focus minutes leak forward into post-gap focused_work
                # and trip water_break_pending earlier than 30 min of
                # genuine post-gap focus warrants. Codex P2 reviews:
                # PR #1226 (long-gap and non-positive-delta findings).
                #
                # Conservative reset of everything that could carry
                # stale pre-event context:
                #   * accumulator → 0
                #   * _last_known_state → None forces the bookkeeping
                #     below to treat any post-event focused_work as a
                #     fresh session entry, AND prevents anti-slack from
                #     firing on a focused_work → leisure transition
                #     observed across the unsafe-delta tick.
                #   * Pending dicts cleared since the snapshot they
                #     reference is now ancient.
                self._work_acc_seconds = 0.0
                self._last_known_state = None
                self._context_prompt_last_state = None
                self._work_break_pending = None
                self._anti_slack_pending = None

        # ── Focused_work session bookkeeping (for anti-slack transition) ─
        # Track entry/exit so we can report the just-ended session length
        # and app name when the user pivots to leisure.
        prev_known = self._last_known_state
        active_window = snap.active_window
        active_canonical = (
            active_window.canonical if active_window and active_window.canonical
            else (active_window.title if active_window and active_window.title else None)
        )
        if state == 'focused_work':
            if prev_known != 'focused_work':
                # Entering focused_work. Clear any anti-slack pending —
                # user is back at it, no need to nag.
                self._focused_work_session_started_at = now
                self._focused_work_session_app = active_canonical
                self._anti_slack_pending = None
            elif self._focused_work_session_app is None and active_canonical:
                # Late-arriving canonical (the state already became
                # focused_work but the active_window was None at entry).
                self._focused_work_session_app = active_canonical
        elif state == 'transitioning' and prev_known == 'focused_work':
            # Mid-flick to a sibling work window — keep session timer
            # running, don't reset (mirrors the accumulator rule).
            pass
        elif prev_known == 'focused_work' or (
            prev_known == 'transitioning' and self._focused_work_session_started_at is not None
        ):
            # Just left focused_work (possibly via transitioning). Capture
            # the session length and app, then evaluate anti-slack.
            session_started = self._focused_work_session_started_at
            session_app = self._focused_work_session_app
            self._focused_work_session_started_at = None
            self._focused_work_session_app = None
            if (
                session_started is not None
                and state in _ANTI_SLACK_LEISURE_STATES
            ):
                # Use the accumulator value captured at tick start
                # (before reset) — it honors the long-gap discard rule,
                # while ``now - session_started`` would credit the user
                # with sleep/suspend time as if they'd been working.
                session_seconds = session_acc_at_start
                cooldown_ok = (
                    self._anti_slack_last_fired_at == 0.0
                    or (now - self._anti_slack_last_fired_at) >= anti_slack_cooldown_seconds
                )
                if (
                    session_seconds >= anti_slack_min_focus_seconds
                    and cooldown_ok
                ):
                    new_canonical = active_canonical or ''
                    self._anti_slack_pending = {
                        'set_at': now,
                        'minutes': max(1, int(session_seconds / 60)),
                        'prev_app': session_app or '',
                        'new_app': new_canonical,
                    }

        # Anti-slack pending invalidation: state moved out of leisure
        # before we got to deliver — the moment is gone.
        if (
            self._anti_slack_pending is not None
            and state not in _ANTI_SLACK_LEISURE_STATES
        ):
            self._anti_slack_pending = None
        # Anti-slack pending window expiry.
        if (
            self._anti_slack_pending is not None
            and (now - self._anti_slack_pending['set_at']) > anti_slack_window
        ):
            self._anti_slack_pending = None

        # ── 情境弹窗一次性检测（A/B 实验组前端用）────────────────
        # 只在「进入」目标状态那一刻置 pending（state != 上一状态），状态保持期间不重复
        # 触发，避免 20s 心跳刷屏。分组判定 + 每会话去重都在前端。
        #   gaming / casual_browsing（=娱乐，进游戏/看番/视频）→ 'play'
        #   focused_work（进专注工作）                       → 'work'
        # 用 _context_prompt_last_state（情境弹窗专属基线）而非 _last_known_state：后者
        # 跨 session 长存，会让「上个 session 结束时在游戏、新 session 仍在游戏」检测不到
        # 进入、漏弹；专属基线在每个 session 开始时被 reset_context_prompt_baseline 清成
        # None，于是当前状态重新算作一次「进入」。为 None（首启 / 不安全 delta 重置 /
        # 新 session）都算「进入」，前端按 app 会话去重兜住重复。
        ctx_prev = self._context_prompt_last_state
        _CONTEXT_PROMPT_TARGET_STATES = ('gaming', 'casual_browsing', 'focused_work')
        if state != ctx_prev:
            if state in ('gaming', 'casual_browsing'):
                self._context_prompt_pending = {'context': 'play', 'set_at': now}
            elif state == 'focused_work':
                self._context_prompt_pending = {'context': 'work', 'set_at': now}
        # 离开目标状态（进 idle/away/chatting/transitioning/private 等非目标态）时，清掉
        # 还没 drain 的过期 pending：pending 可能是 get_snapshot 路径（实验组 kick）置的、
        # 还没等到 loop drain，用户就离开了游戏/工作；若不清，loop 会把「已经离开的场景」
        # 推成过期弹窗，甚至据此翻错设置。目标态之间切换（gaming↔casual_browsing↔
        # focused_work）由上面的 overwrite 处理，不受影响。
        if state not in _CONTEXT_PROMPT_TARGET_STATES:
            self._context_prompt_pending = None
        self._context_prompt_last_state = state

        self._last_known_state = state

        # ── Water-break pending ─────────────────────────────────
        # Armed when accumulator crosses threshold AND state is currently
        # focused_work. Stays armed across ticks (no time pin) — the next
        # proactive_chat round in focused_work fires it. If the user
        # leaves focused_work, accumulator resets to 0 (above) which
        # naturally clears the arming condition; we also drop the pending
        # explicitly here for cleanliness.
        if (
            state == 'focused_work'
            and self._work_acc_seconds >= work_break_seconds
        ):
            if self._work_break_pending is None:
                self._work_break_pending = {
                    'set_at': now,
                    'minutes': max(1, int(self._work_acc_seconds / 60)),
                    'app': active_canonical or '',
                }
            else:
                # Refresh minutes (accumulator keeps growing) and app
                # (window may have shifted to a different work app).
                self._work_break_pending['minutes'] = max(
                    1, int(self._work_acc_seconds / 60),
                )
                if active_canonical:
                    self._work_break_pending['app'] = active_canonical
        elif state != 'focused_work' and state != 'transitioning':
            # User left focused work entirely (and isn't mid-flick).
            # Drop pending — accumulator was already reset above.
            self._work_break_pending = None
        # Window expiry as a defense-in-depth: if proactive doesn't fire
        # and the user keeps grinding, the pending stays valid (intent
        # of must-fire) — but if the snapshot pipeline is wedged for
        # >window_seconds and the moment is conceptually gone, reset.
        # ``set_at`` is captured once on first arming and NOT refreshed
        # by the minutes-update branch above, so this expiry check
        # actually bites for any state that holds the pending — most
        # commonly ``transitioning`` lingering past the window. The
        # ``state != 'focused_work'`` gate keeps focused_work itself
        # exempt: as long as the user is actively focused, the pending
        # is canonical and shouldn't time out. CodeRabbit nitpick: PR #1226.
        if (
            self._work_break_pending is not None
            and state != 'focused_work'
            and (now - self._work_break_pending['set_at']) > work_break_window
        ):
            self._work_break_pending = None

    def _build_work_break_pending(self) -> WorkBreakPending | None:
        """Project the internal pending dict into the frozen snapshot type."""
        if self._work_break_pending is None:
            return None
        return WorkBreakPending(
            minutes=self._work_break_pending['minutes'],
            app=self._work_break_pending['app'],
        )

    def _build_anti_slack_pending(self) -> AntiSlackPending | None:
        if self._anti_slack_pending is None:
            return None
        return AntiSlackPending(
            minutes=self._anti_slack_pending['minutes'],
            prev_app=self._anti_slack_pending['prev_app'],
            new_app=self._anti_slack_pending['new_app'],
        )

    def mark_work_break_used(self, *, now: float | None = None) -> None:
        """Reset the water-break accumulator + clear pending after delivery.

        Called from ``main_routers/system_router.py`` once the minimal
        Phase 2 delivery (regular drink/stretch nudge OR the 50% rest+
        game-invite branch) commits successfully. Resets the accumulator
        so the next break is at least ``work_break_minutes`` of
        focused_work away, mirroring the unfinished-thread "used"
        contract.
        """
        # ``now`` accepted for symmetry with other tracker hooks; not
        # actually needed (we just zero out — no timestamp recorded).
        del now
        self._work_acc_seconds = 0.0
        self._work_break_pending = None

    def mark_anti_slack_used(self, *, now: float | None = None) -> None:
        """Stamp anti-slack delivery and start its cooldown.

        Independent of the water-break + mini-game cooldowns so the
        three reminder paths don't accidentally throttle each other.
        """
        ts = now if now is not None else time.time()
        self._anti_slack_last_fired_at = ts
        self._anti_slack_pending = None

    # ── 情境弹窗（A/B 实验组前端用）──────────────────────────────

    def reset_context_prompt_baseline(self) -> None:
        """Clear the context prompt's "previous state" baseline so the next tick counts the current state as a fresh "entry".

        Called at the start of every session (in core.py's experiment-group kick). The
        tracker outlives sessions; without clearing, "gaming when the last session ended,
        still gaming in the new session" would never be detected as an entry and the
        prompt would be missed. Only touches the context-prompt baseline, not the
        break/anti-slack ``_last_known_state``.

        Also clears any leftover pending: if the previous session set a pending but never
        drained it (session ended before the loop ticked), the leftover would be pushed
        as a stale prompt by the new session's first tick. After clearing, the kick's
        following get_snapshot re-sets pending based on the new session's current state.
        """
        self._context_prompt_last_state = None
        self._context_prompt_pending = None

    def set_context_prompt_callback(
        self, callback: Callable[[str], Awaitable[None]] | None
    ) -> None:
        """Inject the async callback that pushes "entered gaming/entertainment or focused work" signals to the frontend.

        Called by core.py after the tracker is built; the callback forwards the signal to
        the frontend over WebSocket. ``callback(context)`` receives 'play'
        (gaming/entertainment) or 'work' (focused work). Pass None to remove the
        callback (e.g. when the session ends).
        """
        self._on_context_prompt = callback

    async def _drain_context_prompt(self) -> None:
        """Push the one-shot context signals accumulated by ``_tick_break_reminders`` to the frontend.

        Only called from the async heartbeat (awaiting the callback requires an async
        context). Consumes one slot per call; push failures are swallowed silently —
        these are telemetry-style prompts, and losing one must not crash the heartbeat.
        """
        pending = self._context_prompt_pending
        if pending is None:
            return
        self._context_prompt_pending = None
        callback = self._on_context_prompt
        if callback is None:
            return
        try:
            await callback(pending['context'])
        except Exception as e:  # noqa: BLE001 — 推送失败不能让心跳挂掉
            logger.debug(
                '[%s] context prompt push failed (%s): %s',
                self.lanlan_name, pending.get('context'), e,
            )

    # ── enrichment kickoff ──────────────────────────────────────

    def kickoff_open_threads_compute(self, lang: str = 'zh') -> None:
        """Spawn an emotion-tier compute of ``open_threads`` if stale.

        Intended call site: top of ``proactive_chat`` Phase 1, in
        parallel with the source-fetch tasks. Returns immediately;
        the result populates the cache by the time Phase 2 reads
        ``get_snapshot``. If the LLM is slow / fails, the cache stays
        on its previous value (potentially empty), which the prompt
        formatter renders or omits accordingly.

        Idempotent in four useful ways:
          * If the rule state is currently ``private`` → skip (no LLM
            calls during privacy lockdown — even the conversation
            buffer might reference sensitive context that was just
            mentioned).
          * If the cache seq matches the current user-message seq, no
            new user has spoken since last compute → skip.
          * If a previous task is still running → skip (don't queue).
          * If conversation buffers are empty → skip (nothing to score).
        """
        # Two privacy gates, OR'd: user-toggled "privacy mode" disables
        # the entire tracker (PR #1024); static-DB ``private`` state means
        # a sensitive app (KeePass etc) is foreground right now even while
        # the user has the tracker on. Either condition skips enrichment
        # — cheap O(1) checks, safe under sync callers.
        if _privacy_mode_active() or self._sm._current_state == 'private':
            return
        if self._open_threads_computed_at_seq == self._conv_seq:
            return
        if self._open_threads_task is not None and not self._open_threads_task.done():
            return
        if not self._user_msg_buffer and not self._ai_msg_buffer:
            return
        self._open_threads_task = asyncio.create_task(
            self._do_open_threads_compute(lang),
            name=f'open_threads_{self.lanlan_name}',
        )

    async def _do_open_threads_compute(self, lang: str) -> None:
        """One-shot LLM call. Updates cache only on parse success.

        In-flight guard: capture ``_conv_seq`` before the LLM call;
        re-check on completion. If new conversation events arrived
        while we were waiting (rev advanced), the result was computed
        from a stale buffer view — discard it. ``_open_threads_computed_at_seq``
        stays at its previous value, so the next ``kickoff`` will see
        the seq mismatch and trigger a fresh compute against the
        current buffer.
        """
        from main_logic.activity.llm_enrichment import call_open_threads
        seen_seq = self._conv_seq
        try:
            result = await call_open_threads(
                user_msgs=list(self._user_msg_buffer),
                ai_msgs=list(self._ai_msg_buffer),
                lang=lang,
            )
        except Exception as e:
            logger.debug('[%s] open_threads compute failed: %s', self.lanlan_name, e)
            return
        if result is None:
            # LLM/parse failure — keep old cache intact, don't bump seq
            # so the next kickoff retries.
            return
        if self._conv_seq != seen_seq:
            # New user/AI message arrived during the LLM call. Our
            # result reflects pre-message state — discard rather than
            # let it shadow the up-to-date buffer until the next tick.
            logger.debug(
                '[%s] open_threads result discarded: seq advanced from %d to %d during LLM call',
                self.lanlan_name, seen_seq, self._conv_seq,
            )
            return
        self._open_threads_cache = result
        self._open_threads_computed_at_seq = seen_seq

    # ── activity_guess background loop ──────────────────────────

    async def _activity_guess_loop(self) -> None:
        """20s tick. Recomputes activity_guess on state change.

        Skip rules (in order of cheapness):
          1. State signature unchanged AND user hasn't said anything
             new since last compute → skip.
          2. ``state == 'away'`` → no point describing absence.
          3. Last LLM call < ``_ACTIVITY_GUESS_MIN_REFRESH_SECONDS`` ago
             → skip even if signature changed (anti-thrash).

        Failures are silent — the previous cache stays in place until
        the next tick succeeds.
        """
        last_conv_seq = -1
        while True:
            try:
                await asyncio.sleep(_ACTIVITY_GUESS_TICK_SECONDS)
            except asyncio.CancelledError:
                return

            # 隐私模式：本 tick 不读窗口/进程，也不调 activity LLM。
            # Deep-topic candidate is intentionally independent from privacy
            # mode now; it consumes the persisted conversation signal window
            # and owns its own readiness/pending checks.
            if _privacy_mode_active():
                try:
                    from utils.language_utils import get_global_language, get_global_language_full
                    topic_lang = get_global_language_full() or get_global_language() or 'en'
                except Exception as exc:
                    logger.debug(
                        "[%s] topic language resolve failed in privacy mode: %s",
                        self.lanlan_name,
                        exc,
                    )
                    topic_lang = 'en'
                self._process_topic_candidates_if_ready(
                    lang=topic_lang,
                    now=time.time(),
                )
                continue

            try:
                # Pull a fresh snapshot to compare against.
                ts = time.time()
                sys_snap = self._select_system_snapshot(ts)
                self._sm.update_system(sys_snap)
                self._sm.update_window(
                    observation_from_system(sys_snap, self._sm._prefs),
                    now=ts,
                )
                rule_snap = self._sm.get_snapshot(now=ts)

                # Tick break-reminder accumulator + transition detection
                # every loop iteration regardless of the activity_guess
                # short-circuits below. Without this the accumulator
                # would only advance when proactive_chat queries the
                # snapshot — and a state transition (focused_work →
                # casual_browsing) between two proactive rounds 30 min
                # apart would credit the user with 30 min of focus they
                # weren't actually doing. This is the canonical heartbeat
                # for the break-reminder timers.
                self._tick_break_reminders(rule_snap, now=ts)

                # Drain 情境弹窗 pending 并推送（必须在下面 away/private 的 bail 之前，
                # 否则进 away/private 那一 tick 设的 pending 会被 continue 跳过永不推；
                # 实际进游戏/娱乐/工作都不会是 away/private，这里只是防御性早 drain）。
                # 这是唯一的 drain 点：心跳每 tick 都跑、且是 async 上下文，能 await 回调。
                # get_snapshot 路径也调 _tick_break_reminders 设 pending，但 _last_known_state
                # 已被它更新，本 drain 把那次 pending 一并发出，不会漏也不会重。
                await self._drain_context_prompt()

                from utils.language_utils import get_global_language, get_global_language_full
                activity_lang = get_global_language() or 'en'
                topic_lang = get_global_language_full() or activity_lang
                self._process_topic_candidates_if_ready(lang=topic_lang, now=ts)

                # Bail on private — explicitly do NOT send sensitive app
                # context (or even surrounding conversation) to the
                # emotion-tier LLM. Existing cached values stay frozen
                # until the user leaves the private app; on resume the
                # state-signature dedup will refresh naturally. Topic
                # candidate processing has already been kicked above.
                if rule_snap.state == 'private':
                    continue

                # 主动搭话关时跳过 activity_guess 的 LLM 叙述：它只喂 proactive Phase 2，
                # 没开主动搭话就没有消费方。上面的 _tick_break_reminders + 情境弹窗 drain
                # 是纯规则、已经跑过，所以「进游戏/工作」检测照常工作。这样实验组为弹窗
                # kick 起的 loop 在用户没开主动搭话时只有廉价规则轮询、零 LLM 开销。
                if not _proactive_chat_enabled():
                    continue

                # Bail on away — nothing useful to narrate.
                if rule_snap.state == 'away':
                    continue

                # Anti-thrash: respect the minimum refresh interval.
                if (
                    self._activity_guess_at
                    and ts - self._activity_guess_at < _ACTIVITY_GUESS_MIN_REFRESH_SECONDS
                ):
                    continue

                # State signature: which "kind of activity" the rule
                # machine sees right now, plus whether the user has
                # said something new. Quantize idle to coarse buckets
                # so minor jitter doesn't trigger recompute.
                idle_bucket = int((rule_snap.system_idle_seconds or 0) // 30)
                sig = (
                    rule_snap.state,
                    (rule_snap.active_window.canonical
                        if rule_snap.active_window else None),
                    (rule_snap.active_window.subcategory
                        if rule_snap.active_window else None),
                    idle_bucket,
                )
                if sig == self._activity_guess_state_sig and self._conv_seq == last_conv_seq:
                    continue

                # In-flight guard — capture conv_seq + buffer snapshots
                # before the LLM call. Same pattern as
                # ``_do_open_threads_compute``: if a new user/AI message
                # arrives during the await, the result reflects pre-message
                # state and must not overwrite caches built on the newer
                # buffer. Discarding here lets the next tick recompute
                # against the up-to-date state.
                seen_conv_seq = self._conv_seq
                user_msgs_snapshot = list(self._user_msg_buffer)
                ai_msgs_snapshot = list(self._ai_msg_buffer)
                signals = self._snapshot_signals_for_llm(rule_snap)
                from main_logic.activity.llm_enrichment import call_activity_guess
                result = await call_activity_guess(
                    snapshot_signals=signals,
                    rule_state=rule_snap.state,
                    user_msgs=user_msgs_snapshot,
                    ai_msgs=ai_msgs_snapshot,
                    lang=activity_lang,
                )
                if result is None:
                    continue
                if self._conv_seq != seen_conv_seq:
                    logger.debug(
                        '[%s] activity_guess result discarded: conv_seq advanced from %d to %d during LLM call',
                        self.lanlan_name, seen_conv_seq, self._conv_seq,
                    )
                    continue
                self._activity_scores_cache = result.get('scores', {}) or {}
                self._activity_guess_cache = result.get('guess', '') or ''
                self._activity_guess_state_sig = sig
                self._activity_guess_at = ts
                last_conv_seq = seen_conv_seq
            except asyncio.CancelledError:
                return
            except Exception as e:
                # Stay alive — one bad tick shouldn't kill the loop.
                logger.debug('[%s] activity_guess loop tick failed: %s', self.lanlan_name, e)

    def _process_topic_candidates_if_ready(self, *, lang: str, now: float) -> None:
        """Let the topic pool piggyback on the activity heartbeat.

        Candidate analysis has its own dirty/ready/pending checks inside the
        pool. This hook merely supplies the existing 20s cadence, then
        returns immediately so a slow topic analyzer cannot stall the activity
        heartbeat.
        """
        if self._topic_candidate_task is not None and not self._topic_candidate_task.done():
            return
        self._topic_candidate_task = asyncio.create_task(
            self._run_topic_candidates_if_ready(lang=lang, now=now),
            name=f"topic-candidates-{self.lanlan_name}",
        )

    async def _run_topic_candidates_if_ready(self, *, lang: str, now: float) -> None:
        try:
            from main_logic.topic.pipeline import get_topic_hook_pool
            await get_topic_hook_pool().process_ready_topics(
                lanlan_name=self.lanlan_name,
                lang=lang,
                now=now,
            )
        except Exception as exc:
            logger.debug("[%s] topic candidate heartbeat failed: %s", self.lanlan_name, exc)

    async def _purge_topic_candidates_for_privacy(self, *, all_characters: bool = False) -> None:
        """Deprecated compatibility no-op.

        Privacy mode no longer purges or gates the deep-topic signal store.
        """
        del all_characters
        return

    def _refresh_prefs(self) -> None:
        """Pick up live edits to ``user_preferences.json::activity``.

        The preferences loader has its own mtime-based 30s cache, so this
        is cheap (one lock acquisition + identity compare in the common
        path). When the cache reloads, we swap the new prefs into the
        state machine so override lookups (``user_app_overrides`` /
        ``user_title_overrides`` / ``user_game_overrides`` /
        ``skip_probability_overrides``) reflect the user's current
        config without requiring a session restart.

        Threshold values (``away_idle_seconds``, etc.) are intentionally
        NOT re-derived: those are read into instance attributes at
        ``ActivityStateMachine.__init__`` and stay frozen for the
        session. Treating thresholds as session-stable while overrides
        are live is the deliberate split — thresholds are tuning
        constants people set once, overrides are how users react to a
        misclassification right now.
        """
        fresh = get_activity_preferences()
        if fresh is not self._sm._prefs:
            self._sm._prefs = fresh

    def _select_system_snapshot(self, now: float) -> SystemSnapshot:
        """Pick external (frontend-pushed) snapshot when fresh, else local.

        Frontend pushes are expected on a heartbeat — when the heartbeat
        stops (network blip, frontend crash), the cached push goes stale
        and we fall back to the local collector. On remote backends the
        collector reports ``os_signals_available=False``, which the
        state machine and formatter then surface to the prompt as a
        degraded-mode marker.
        """
        ext = self._external_system_snap
        if ext is not None and (now - ext.timestamp) <= _EXTERNAL_SIGNAL_TTL_SECONDS:
            return ext
        return self._collector.snapshot()

    @staticmethod
    def _snapshot_signals_for_llm(snap: ActivitySnapshot) -> dict:
        """Pick the structured fields worth feeding into the prompt.

        Trimmed deliberately — full snapshot has many cross-references
        and timing fields the LLM doesn't need. We pass what's
        observably "what is the user doing on screen + how recent is
        activity", and leave the rest as state-machine internals.
        """
        win = snap.active_window
        return {
            'rule_state': snap.state,
            'active_window': (
                f'{win.canonical} ({win.category}/{win.subcategory})'
                if win and win.canonical else None
            ),
            'window_title': win.title if win else None,
            'system_idle_seconds': int(snap.system_idle_seconds),
            'cpu_avg_30s': round(snap.cpu_avg_30s, 1),
            'window_switch_rate_5min': snap.window_switch_rate_5min,
            'voice_mode_active': snap.voice_mode_active,
            'time_period': snap.period,
        }

    # ── internals ──────────────────────────────────────────────

    async def _ensure_collector_started(self) -> None:
        if self._collector_started:
            return
        await self._collector.start()
        self._collector_started = True
        # Spin up the activity_guess background loop on first snapshot
        # request. The loop self-throttles (state-signature dedup +
        # anti-thrash interval), so starting it eagerly is cheap.
        self.ensure_activity_guess_loop_started()
        logger.info(
            '[%s] UserActivityTracker started (shared system collector + guess loop)',
            self.lanlan_name,
        )

    def ensure_activity_guess_loop_started(self) -> None:
        """Start the 20s heartbeat without starting OS signal collection."""
        if self._activity_guess_loop_task is None or self._activity_guess_loop_task.done():
            self._activity_guess_loop_task = asyncio.create_task(
                self._activity_guess_loop(),
                name=f'activity_guess_loop_{self.lanlan_name}',
            )

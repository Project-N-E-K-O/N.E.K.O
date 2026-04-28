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
from dataclasses import replace as dc_replace

from main_logic.activity.snapshot import ActivitySnapshot
from main_logic.activity.state_machine import (
    ActivityStateMachine, observation_from_system,
)
from main_logic.activity.system_signals import (
    SystemSignalCollector, SystemSnapshot, get_system_signal_collector,
)

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
_EXTERNAL_SIGNAL_TTL_SECONDS = 30.0


def _privacy_mode_active() -> bool:
    """用户是否开启了隐私模式。开启时整个 tracker 应当短路。

    存储在前端 ``proactiveVisionEnabled`` 的反面（详见 utils.preferences）。
    异常路径 fail-closed：任何读取异常一律按"隐私模式开启"处理，宁可
    短期内 tracker 不可用，也不能让"读不出来"等价于"用户没开隐私"。
    正常的"用户没开隐私"路径走 ``is_privacy_mode_enabled`` 返回 False，
    不进 except 分支。
    """
    try:
        from utils.preferences import is_privacy_mode_enabled
        return is_privacy_mode_enabled()
    except Exception as e:
        logger.warning(
            'privacy mode check failed, defaulting to enabled (fail-closed): %s', e,
        )
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

        # Frontend-pushed system signal (for remote deployments where the
        # backend's local OS APIs see only the server, not the user).
        # When fresh (<= _EXTERNAL_SIGNAL_TTL_SECONDS), this overrides
        # the local collector entirely. Stale → fall back to collector
        # (which on a remote backend reports os_signals_available=False
        # and the state machine's snapshot makes that explicit).
        self._external_system_snap: SystemSnapshot | None = None

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
            self._user_msg_buffer.append((ts, text.strip()[:1000]))

    def on_ai_message(self, *, text: str | None = None, now: float | None = None) -> None:
        """Stamp an "AI just spoke" event.

        ``text`` is optional. When provided, the state machine runs the
        question heuristic over it: if the AI's reply trips the heuristic
        (ends with ``?`` / ``？`` / a CN sentence-final question particle),
        an unfinished-thread record opens — Phase 2 will be allowed up to
        ``UNFINISHED_THREAD_MAX_FOLLOWUPS`` (default 2) follow-ups within
        the 5-minute window even in restricted_screen_only states.

        Text is also appended to the AI conversation buffer so the
        emotion-tier LLM enrichment has recent context to reason over.
        """
        ts = now if now is not None else time.time()
        self._sm.update_ai_message(text=text, now=ts)
        if text and not _privacy_mode_active():
            self._ai_msg_buffer.append((ts, text.strip()[:1000]))
            # AI also opens threads (promises, abandoned mid-sentences) →
            # bump _conv_seq so kickoff_open_threads_compute will recompute.
            # Empty / no-text turns (errors / silenced) skip the bump,
            # since nothing in the buffer changed.
            self._conv_seq += 1

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
        ``activity_guess``, ``open_threads``) merged in.
        """
        await self._ensure_collector_started()

        ts = now if now is not None else time.time()

        sys_snap = self._select_system_snapshot(ts)
        self._sm.update_system(sys_snap)
        self._sm.update_window(observation_from_system(sys_snap), now=ts)

        snap = self._sm.get_snapshot(now=ts)
        # Patch in emotion-tier enrichment caches. ``snap`` is a frozen
        # dataclass; ``replace`` returns a new instance without mutating
        # the original. Callers always get a self-consistent snapshot.
        return dc_replace(
            snap,
            activity_scores=dict(self._activity_scores_cache),
            activity_guess=self._activity_guess_cache,
            open_threads=list(self._open_threads_cache),
        )

    def get_snapshot_sync(self, *, now: float | None = None) -> ActivitySnapshot:
        """Synchronous variant for callers outside an event loop.

        Useful for unit tests and any sync-context debug logging. Skips
        the collector-start guard — callers must ensure collection is
        running, or accept that ``SystemSnapshot`` defaults will be in
        play. Enrichment caches are merged in the same way as
        ``get_snapshot``.
        """
        ts = now if now is not None else time.time()
        # Use _select_system_snapshot to honour frontend-pushed signals
        # exactly like the async path — otherwise remote deployments
        # would silently fall back to the local (server-side) collector
        # in sync callers.
        sys_snap = self._select_system_snapshot(ts)
        self._sm.update_system(sys_snap)
        self._sm.update_window(observation_from_system(sys_snap), now=ts)
        snap = self._sm.get_snapshot(now=ts)
        return dc_replace(
            snap,
            activity_scores=dict(self._activity_scores_cache),
            activity_guess=self._activity_guess_cache,
            open_threads=list(self._open_threads_cache),
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

        Idempotent in three useful ways:
          * If the cache seq matches the current user-message seq, no
            new user has spoken since last compute → skip.
          * If a previous task is still running → skip (don't queue).
          * If conversation buffers are empty → skip (nothing to score).
        """
        # 隐私模式下整个 enrichment 通路都关掉。
        if _privacy_mode_active():
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

            # 隐私模式：本 tick 不读窗口/进程，也不调 LLM，直接进入下一轮。
            # 缓存自然衰减（保留最后一次值，proactive_chat 那边 snapshot 已被
            # gating 成 None，缓存不会被消费）。
            if _privacy_mode_active():
                continue

            try:
                # Pull a fresh snapshot to compare against.
                ts = time.time()
                sys_snap = self._select_system_snapshot(ts)
                self._sm.update_system(sys_snap)
                self._sm.update_window(observation_from_system(sys_snap), now=ts)
                rule_snap = self._sm.get_snapshot(now=ts)

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

                from utils.language_utils import get_global_language
                lang = get_global_language() or 'zh'

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
                    lang=lang,
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
        if self._activity_guess_loop_task is None:
            self._activity_guess_loop_task = asyncio.create_task(
                self._activity_guess_loop(),
                name=f'activity_guess_loop_{self.lanlan_name}',
            )
        logger.info(
            '[%s] UserActivityTracker started (shared system collector + guess loop)',
            self.lanlan_name,
        )

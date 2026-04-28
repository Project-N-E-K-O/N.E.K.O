"""User-activity state machine.

Pure-rules engine: takes signals (system snapshot, window observations,
voice events, conversation timestamps) and emits an ``ActivitySnapshot``
describing the inferred user state and a propensity directive for the
proactive-chat prompt.

No LLM, no external calls. Every decision is keyword/threshold driven so
behaviour is auditable and cheap. The ``open_threads`` field on the
returned snapshot is intentionally a placeholder for v1 — populated by
a future emotion-tier enhancement once the keyword path is fully tuned.

Design choices
--------------

Why dwell-time over EMA for categorical signals: an EMA of "what category
is the active window" would have to be encoded numerically (one-hot per
category, smoothed independently) — clunky, and the natural quantity we
care about ("how long has this category dominated?") is just dwell time.
Numerical signals (CPU, idle) skip EMA too: the system collector already
maintains a 30s rolling avg, which is sufficient for our single
"high CPU helps confirm gaming/work" check.

Why "transitioning" still allows screen-based chat: the user explicitly
clarified — screen channel is the floor, available in nearly every
state. Transitioning only suppresses external sources (web/news/music),
which is the source-weight layer's responsibility, not ours.

Stale-recovery sticky flag: when the state goes ``away → anything``,
we set ``_stale_returning_until = now + STALE_RECOVERY_SECONDS``. Any
classifier read inside that window emits ``stale_returning`` instead of
the underlying state, ensuring the greeting opportunity gets a chance
even if the user's first action was opening their IDE.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from config.activity_keywords import (
    classify_browser_title, classify_window_title, is_browser_process,
)

from main_logic.activity.snapshot import (
    ActivitySnapshot, ActivityState, UnfinishedThread, WindowObservation,
    state_to_propensity,
)
from main_logic.activity.system_signals import SystemSnapshot


# ── Tunables ────────────────────────────────────────────────────────
# Kept inline rather than in config/__init__.py so tracker tweaks stay
# self-contained. Promote to config later if user-facing knobs become
# necessary.

# Time after which absent input means the user has stepped away. 15min
# is the same threshold used by the existing greeting logic in core.py
# (``trigger_greeting`` uses 15min as the "long enough to warrant a
# hello"). Keeping them aligned avoids ping-pong.
AWAY_IDLE_SECONDS = 15 * 60

# Once we exit ``away``, hold ``stale_returning`` this long so the
# proactive-chat prompt has a window in which to mix in 1d+ reminisce.
STALE_RECOVERY_SECONDS = 60.0

# Voice-engaged window: voice mode + RMS-active observation within this
# many seconds counts as "currently in voice exchange".
VOICE_ACTIVE_WINDOW_SECONDS = 8.0

# focused_work needs sustained dwell on a work-category window. Below this
# the user is just glancing — likely transitioning. 90s is conservative
# enough to reject "VS Code briefly raised to copy a snippet" while still
# recognising real focus sessions.
FOCUSED_WORK_MIN_DWELL_SECONDS = 90.0

# Recent user activity helps separate focused_work (active work) from
# leaving the IDE in foreground while doing something else. ``recent``
# here is generous — the user might think for minutes between keystrokes.
FOCUSED_WORK_RECENT_INPUT_SECONDS = 5 * 60

# Casual-browsing dwell — entertainment windows that flash for a moment
# shouldn't flip state. 30s is enough to filter notifications/popups but
# fast enough to react to genuine browsing.
CASUAL_BROWSING_MIN_DWELL_SECONDS = 30.0

# transitioning: # of distinct window observations in the lookback that
# signals the user is rapidly task-switching. The lookback is
# ``WINDOW_HISTORY_LOOKBACK_SECONDS``. Tuned for "5 windows in 5 min" —
# normal users don't switch this much during steady-state work.
WINDOW_SWITCH_TRANSITION_THRESHOLD = 5
WINDOW_HISTORY_LOOKBACK_SECONDS = 300

# How long a state has to remain "new" to be considered ``transitioned_recently``.
TRANSITION_RECENT_WINDOW_SECONDS = 30.0

# Bound on the window-observation buffer size. With a 5s poll and 5min
# lookback we expect ~60 entries; 200 leaves headroom for faster polling.
WINDOW_BUFFER_MAXLEN = 200

# GPU-fallback gaming detection. When the active window is in the
# ``unknown`` category (small / indie / new game whose title isn't in our
# keyword DB) AND GPU is sustained high AND the user is interacting,
# treat as gaming. The threshold is set conservatively to avoid
# false-positive on video editing or ML training (those typically run
# inside ``work``-classified windows and skip this check).
GAMING_GPU_THRESHOLD_PERCENT = 60.0
# Idle ceiling for the fallback: if the user hasn't touched the keyboard
# or mouse in this many seconds, it's probably background rendering or a
# detached AFK game — don't flag as active gaming.
GAMING_GPU_MAX_IDLE_SECONDS = 60.0

# ── Unfinished thread mechanics ─────────────────────────────────────
# When the AI's last reply contained a question and the user hasn't
# responded, we open a 5-minute window in which proactive chat is
# allowed to follow up — even in restricted_screen_only states. A hard
# cap on follow-ups prevents the AI from harassing the user about the
# same hanging question.
UNFINISHED_THREAD_WINDOW_SECONDS = 5 * 60
UNFINISHED_THREAD_MAX_FOLLOWUPS = 2

# Question-detection heuristic: only the last N chars of the AI message
# are scanned. Mid-sentence question marks (e.g. "你说『你好吗』我没听清")
# don't count as the message itself ending with a question.
_QUESTION_TAIL_LEN = 60
_QUESTION_MARKS: tuple[str, ...] = ('?', '？')
# Sentence-final particles in CJK that imply a question even without a
# punctuation mark. Checked against the trailing few chars (after
# stripping trailing whitespace and one optional punctuation).
_CN_QUESTION_PARTICLES: tuple[str, ...] = ('吗', '呢', '么', '吧')


def _strip_emotion_tags(text: str) -> str:
    """Drop ``<emotion>`` decoration so it doesn't count toward tail.

    AI replies sometimes end with ``<happy>`` style tags that the TTS
    pipeline already strips elsewhere; we apply the same scrub here so
    question detection doesn't get tripped up by them.
    """
    if not text or '<' not in text:
        return text
    out: list[str] = []
    in_tag = False
    for ch in text:
        if ch == '<':
            in_tag = True
            continue
        if ch == '>' and in_tag:
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return ''.join(out)


def _text_has_open_question(text: str | None) -> bool:
    """Heuristic: did the AI just ask something the user hasn't answered?

    True when:
      * Last ``_QUESTION_TAIL_LEN`` chars contain ``?`` or ``？``, OR
      * Trailing chars (after stripping whitespace + one trailing
        punctuation) end with a CN sentence-final question particle.

    False positives are tolerable — they at most enable one extra
    follow-up window. False negatives just mean we miss a thread.
    """
    if not text:
        return False
    cleaned = _strip_emotion_tags(text).strip()
    if not cleaned:
        return False
    tail = cleaned[-_QUESTION_TAIL_LEN:]
    if any(m in tail for m in _QUESTION_MARKS):
        return True
    # CJK particle check: strip up to one trailing punctuation.
    trail = cleaned.rstrip(' 。、，,.!！~～…')
    if not trail:
        return False
    if trail[-1] in _CN_QUESTION_PARTICLES:
        return True
    return False


# ── Helpers ─────────────────────────────────────────────────────────

def _hour_to_period(hour: int) -> str:
    """Coarse time-of-day buckets used in prompt context."""
    if 5 <= hour < 12:
        return 'morning'
    if 12 <= hour < 18:
        return 'afternoon'
    if 18 <= hour < 23:
        return 'evening'
    return 'night'


def observation_from_system(sys_snap: SystemSnapshot) -> WindowObservation | None:
    """Build a ``WindowObservation`` from a raw ``SystemSnapshot``.

    Browser windows are routed to the domain table first (page URL/title
    is more telling than the bare browser name) with title-table
    fallback for branded SaaS apps where the title surfaces the app name
    rather than the domain (e.g. "Notion").
    """
    if sys_snap.window_title is None and sys_snap.process_name is None:
        return None

    is_browser = is_browser_process(sys_snap.process_name)
    if is_browser:
        result = classify_browser_title(sys_snap.window_title)
        if result.category == 'unknown':
            # Fallback: title-only classification (Notion, Figma, etc.)
            result = classify_window_title(sys_snap.window_title)
    else:
        # Non-browser: try title first, then process name as fallback.
        result = classify_window_title(sys_snap.window_title)
        if result.category == 'unknown':
            from config.activity_keywords import classify_process_name
            result = classify_process_name(sys_snap.process_name)

    return WindowObservation(
        process_name=sys_snap.process_name,
        title=sys_snap.window_title,
        category=result.category,
        subcategory=result.subcategory,
        canonical=result.canonical,
        is_browser=is_browser,
    )


# ── Window history entry ────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class _WindowEntry:
    """One observed window-state event (a category change), with its start time."""
    timestamp: float
    observation: WindowObservation


# ── State machine ──────────────────────────────────────────────────

class ActivityStateMachine:
    """Stateful classifier — one instance per ``UserActivityTracker``.

    All update methods are synchronous (no awaits) so they're safe to
    call from any coroutine without locking. ``get_snapshot`` reads
    instance state and returns a frozen dataclass.

    Expected call pattern:

        sm.update_window(observation_from_system(collector.snapshot()))
        sm.update_system(collector.snapshot())
        sm.update_user_message(time.time())
        ...
        snap = sm.get_snapshot(now=time.time())
    """

    def __init__(self) -> None:
        self._current_state: ActivityState = 'idle'
        self._previous_state: ActivityState | None = None
        self._state_started_at: float = time.time()

        # Window tracking — keyed by category change. We collapse identical
        # consecutive observations to one entry to keep the buffer dense
        # with actually meaningful changes, not poll repeats.
        self._window_history: deque[_WindowEntry] = deque(maxlen=WINDOW_BUFFER_MAXLEN)
        self._current_window: WindowObservation | None = None
        self._current_window_started_at: float = 0.0

        # System (from singleton collector)
        self._latest_system: SystemSnapshot | None = None

        # Voice
        self._voice_mode_active: bool = False
        self._voice_last_rms_at: float = 0.0

        # Conversation
        self._last_user_msg_at: float | None = None
        self._last_ai_msg_at: float | None = None

        # Sticky flags
        self._stale_returning_until: float = 0.0

        # Unfinished thread tracking. dict shape:
        #   {'tail': str, 'started_at': float, 'follow_up_count': int}
        # None when no thread is currently hanging. Set by update_ai_message
        # when the AI's text trips the question heuristic; cleared by
        # update_user_message (user responded) or when the snapshot read
        # detects the 5-minute window has expired.
        self._unfinished_thread: dict | None = None

    # ── update inputs ────────────────────────────────────────────

    def update_window(self, obs: WindowObservation | None, *, now: float | None = None) -> None:
        """Record an observed window state.

        Identical-category consecutive observations are collapsed —
        the dwell timer keeps running. A category change (e.g.
        work → entertainment) starts a fresh dwell timer.
        """
        if obs is None:
            return
        ts = now if now is not None else time.time()

        prev = self._current_window
        same = (
            prev is not None
            and prev.category == obs.category
            and prev.subcategory == obs.subcategory
            and (prev.canonical or '') == (obs.canonical or '')
        )
        if not same:
            self._current_window = obs
            self._current_window_started_at = ts
            self._window_history.append(_WindowEntry(timestamp=ts, observation=obs))

    def update_system(self, sys_snap: SystemSnapshot) -> None:
        """Cache the latest system snapshot (idle / CPU)."""
        self._latest_system = sys_snap

    def update_voice_mode(self, active: bool) -> None:
        """Toggle voice mode flag. Driven by session start/stop events."""
        self._voice_mode_active = active

    def update_voice_rms(self, *, now: float | None = None) -> None:
        """Mark a voice-RMS-active observation. Called when VAD detects speech."""
        self._voice_last_rms_at = now if now is not None else time.time()

    def update_user_message(self, *, now: float | None = None) -> None:
        """Stamp a user-message-arrived event.

        Also clears any pending ``_unfinished_thread``: a user reply is
        an implicit acknowledgement of the prior open question, even if
        the reply text doesn't directly answer it.
        """
        self._last_user_msg_at = now if now is not None else time.time()
        self._unfinished_thread = None

    def update_ai_message(self, *, text: str | None = None, now: float | None = None) -> None:
        """Stamp an AI-reply-emitted event.

        Optional ``text`` lets us decide whether this turn opened an
        unfinished thread (AI ended with a question). If the heuristic
        fires, an unfinished-thread record is created. If it doesn't
        fire, any previously open thread is left alone — non-question
        AI utterances don't close prior questions.
        """
        ts = now if now is not None else time.time()
        self._last_ai_msg_at = ts
        if text and _text_has_open_question(text):
            cleaned = _strip_emotion_tags(text).strip()
            tail = cleaned[-120:] if len(cleaned) > 120 else cleaned
            self._unfinished_thread = {
                'tail': tail,
                'started_at': ts,
                'follow_up_count': 0,
            }

    def mark_unfinished_thread_used(self) -> None:
        """Increment the follow-up counter on the currently open thread.

        Called by the proactive chat path after a successful emission
        when the snapshot's ``unfinished_thread`` was active. Once the
        counter reaches ``UNFINISHED_THREAD_MAX_FOLLOWUPS``, subsequent
        snapshots will hide the thread from the prompt — so the AI gets
        at most that many follow-up attempts without a user reply.
        """
        if self._unfinished_thread is None:
            return
        self._unfinished_thread['follow_up_count'] += 1
        if self._unfinished_thread['follow_up_count'] >= UNFINISHED_THREAD_MAX_FOLLOWUPS:
            # Cap reached — drop entirely so we don't keep allocating
            # state for a thread that can no longer be surfaced.
            self._unfinished_thread = None

    # ── snapshot ─────────────────────────────────────────────────

    def get_snapshot(self, *, now: float | None = None) -> ActivitySnapshot:
        """Compute current state and emit a frozen ``ActivitySnapshot``.

        Side effect: this is also where the state-transition bookkeeping
        runs (so ``previous_state`` / ``state_started_at`` /
        ``stale_returning_until`` advance). Callers should treat
        ``get_snapshot`` as the canonical "tick" of the state machine.
        """
        ts = now if now is not None else time.time()

        new_state = self._classify_state(ts)

        # Transition bookkeeping
        if new_state != self._current_state:
            self._previous_state = self._current_state
            # Stale-recovery trigger: leaving 'away' for anything else.
            if self._current_state == 'away' and new_state != 'away':
                self._stale_returning_until = ts + STALE_RECOVERY_SECONDS
            self._current_state = new_state
            self._state_started_at = ts

        # Apply stale_returning override AFTER the bookkeeping so the
        # underlying state still advances (we want to know what state
        # they "really" entered, just expose the stale window for prompt)
        effective_state: ActivityState = self._current_state
        if ts < self._stale_returning_until and self._current_state != 'away':
            effective_state = 'stale_returning'

        propensity = state_to_propensity(effective_state)
        reasons = self._build_propensity_reasons(effective_state, ts)

        # Window observation summary
        active_window = self._current_window

        # Switch rate over the lookback window
        switch_rate = sum(
            1 for entry in self._window_history
            if ts - entry.timestamp <= WINDOW_HISTORY_LOOKBACK_SECONDS
        )

        sys_snap = self._latest_system
        idle_seconds = sys_snap.idle_seconds if sys_snap else 0.0
        cpu_avg = sys_snap.cpu_avg_30s if sys_snap else 0.0
        cpu_now = sys_snap.cpu_instant if sys_snap else 0.0

        secs_since_user = (
            ts - self._last_user_msg_at if self._last_user_msg_at else None
        )
        secs_since_ai = (
            ts - self._last_ai_msg_at if self._last_ai_msg_at else None
        )

        voice_recent = (
            self._voice_mode_active
            and (ts - self._voice_last_rms_at) < VOICE_ACTIVE_WINDOW_SECONDS
        )

        # Time context
        local = datetime.fromtimestamp(ts)
        period = _hour_to_period(local.hour)

        transitioned_recently = (
            ts - self._state_started_at <= TRANSITION_RECENT_WINDOW_SECONDS
            and self._previous_state is not None
        )

        # Unfinished thread: surface it if still within the 5-min window
        # and under the follow-up cap. Past the window, retire the record
        # so future ticks don't keep evaluating the same expired data.
        unfinished = None
        if self._unfinished_thread is not None:
            age = ts - self._unfinished_thread['started_at']
            if age > UNFINISHED_THREAD_WINDOW_SECONDS:
                self._unfinished_thread = None
            elif self._unfinished_thread['follow_up_count'] < UNFINISHED_THREAD_MAX_FOLLOWUPS:
                unfinished = UnfinishedThread(
                    text=self._unfinished_thread['tail'],
                    age_seconds=age,
                    follow_up_count=self._unfinished_thread['follow_up_count'],
                    max_follow_ups=UNFINISHED_THREAD_MAX_FOLLOWUPS,
                )

        return ActivitySnapshot(
            state=effective_state,
            state_age_seconds=ts - self._state_started_at,
            previous_state=self._previous_state,
            transitioned_recently=transitioned_recently,
            stale_returning=(ts < self._stale_returning_until and self._current_state != 'away'),
            propensity=propensity,
            propensity_reasons=reasons,
            system_idle_seconds=idle_seconds,
            cpu_avg_30s=cpu_avg,
            cpu_instant=cpu_now,
            active_window=active_window,
            window_switch_rate_5min=switch_rate,
            os_signals_available=(sys_snap.os_signals_available if sys_snap is not None else False),
            seconds_since_user_msg=secs_since_user,
            seconds_since_ai_msg=secs_since_ai,
            voice_recent_rms_active=voice_recent,
            voice_mode_active=self._voice_mode_active,
            hour=local.hour,
            weekday=local.weekday(),
            period=period,
            unfinished_thread=unfinished,
            open_threads=[],  # placeholder for v2 enhancement
        )

    # ── classifier ───────────────────────────────────────────────

    def _classify_state(self, now: float) -> ActivityState:
        sys_snap = self._latest_system

        # 1. away — system-wide input idle dominates everything else.
        # OS idle is the only signal that survives the user walking away.
        if sys_snap is not None and sys_snap.idle_seconds >= AWAY_IDLE_SECONDS:
            return 'away'

        # 2. voice_engaged — voice mode + recent RMS activity is the
        # strongest "in active conversation" signal we have.
        if (
            self._voice_mode_active
            and (now - self._voice_last_rms_at) < VOICE_ACTIVE_WINDOW_SECONDS
        ):
            return 'voice_engaged'

        # 3. gaming — actual game (subcategory='game') in foreground.
        # Launchers ('subcategory'='launcher') are intentionally NOT here:
        # browsing the Steam store doesn't mean "playing".
        win = self._current_window
        if win is not None and win.category == 'gaming' and win.subcategory == 'game':
            return 'gaming'

        # 3b. gaming-by-GPU fallback. Catches small / indie / new titles
        # not yet in the keyword DB. Gates:
        #   - active window category MUST be 'unknown' — never override
        #     work/communication/entertainment classifications, those are
        #     surer signals than raw GPU load (ML / video / browser games).
        #   - GPU sustained high.
        #   - User actually present (input within last minute) — long-idle
        #     high GPU is usually background rendering or AFK farming, not
        #     active engagement we should hesitate to interrupt.
        if (
            win is not None and win.category == 'unknown'
            and sys_snap is not None and sys_snap.gpu_utilization is not None
            and sys_snap.gpu_utilization >= GAMING_GPU_THRESHOLD_PERCENT
            and sys_snap.idle_seconds <= GAMING_GPU_MAX_IDLE_SECONDS
        ):
            return 'gaming'

        # 4. focused_work — work-category window with sustained dwell AND
        # recent input. The combo is what filters out "left VS Code open
        # while watching YouTube in another monitor" cases.
        if win is not None and win.category == 'work':
            dwell = now - self._current_window_started_at
            recent_input = (
                self._last_user_msg_at is not None
                and (now - self._last_user_msg_at) <= FOCUSED_WORK_RECENT_INPUT_SECONDS
            )
            recent_system_active = (
                sys_snap is not None
                and sys_snap.idle_seconds < FOCUSED_WORK_RECENT_INPUT_SECONDS
            )
            if dwell >= FOCUSED_WORK_MIN_DWELL_SECONDS and (recent_input or recent_system_active):
                return 'focused_work'

        # 5. casual_browsing — entertainment dominates with reasonable dwell.
        if win is not None and win.category == 'entertainment':
            dwell = now - self._current_window_started_at
            if dwell >= CASUAL_BROWSING_MIN_DWELL_SECONDS:
                return 'casual_browsing'

        # 6. chatting — communication app in foreground. We deliberately
        # do NOT gate on "low CPU" per the user's instruction (signal
        # too unreliable). A short dwell is fine — chat windows are
        # small, often briefly raised to read a message.
        if win is not None and win.category == 'communication':
            return 'chatting'

        # 7. transitioning — no clear category dominates AND there's been
        # a flurry of window switches recently. Note this still produces
        # ``open`` propensity (per user clarification — screen channel
        # always allowed); only the source-weight layer should care.
        switches = sum(
            1 for entry in self._window_history
            if now - entry.timestamp <= WINDOW_HISTORY_LOOKBACK_SECONDS
        )
        if switches >= WINDOW_SWITCH_TRANSITION_THRESHOLD:
            return 'transitioning'

        # 8. idle — at the computer (not away) but no clear bucket.
        return 'idle'

    # ── reason strings (for prompt + debugging) ──────────────────

    def _build_propensity_reasons(
        self, state: ActivityState, now: float,
    ) -> list[tuple[str, dict]]:
        """Build structured reasons for the chosen state.

        Each reason is ``(code, params)`` — the code maps to a localized
        template inside ``snapshot._REASON_TEMPLATES``, and the params
        are interpolated at format time. State-machine code stays
        language-agnostic; the prompt formatter renders.
        """
        reasons: list[tuple[str, dict]] = []
        win = self._current_window
        sys_snap = self._latest_system

        if state == 'away':
            reasons.append((
                'state_away',
                {'idle_seconds': int(sys_snap.idle_seconds) if sys_snap else 0},
            ))
        elif state == 'stale_returning':
            reasons.append(('state_stale_returning', {}))
        elif state == 'voice_engaged':
            reasons.append(('state_voice_engaged', {}))
        elif state == 'gaming':
            # ``app`` is best-effort — when the GPU-fallback rule fires we
            # may not have a canonical game name. Use the window title or
            # a neutral placeholder so the template still renders.
            name = (
                (win.canonical if win and win.canonical else None)
                or (win.title if win and win.title else None)
                or '?'
            )
            reasons.append(('state_gaming', {'app': name}))
            # If gaming was inferred from sustained GPU load rather than
            # from a keyword hit, surface that explicitly so the prompt
            # knows the identification is heuristic.
            if win is not None and win.category != 'gaming':
                reasons.append(('gaming_by_gpu', {}))
        elif state == 'focused_work':
            name = (win.canonical if win and win.canonical else None) or '?'
            dwell = int(now - self._current_window_started_at)
            reasons.append(('state_focused_work', {'app': name, 'dwell_seconds': dwell}))
        elif state == 'casual_browsing':
            name = (win.canonical if win and win.canonical else None) or '?'
            reasons.append(('state_casual_browsing', {'app': name}))
        elif state == 'chatting':
            name = (win.canonical if win and win.canonical else None) or '?'
            reasons.append(('state_chatting', {'app': name}))
        elif state == 'transitioning':
            reasons.append(('state_transitioning', {}))
        elif state == 'idle':
            reasons.append(('state_idle', {}))

        # CPU / GPU augmentations — appended only when notably high so
        # we don't add noise to the typical case.
        if sys_snap and sys_snap.cpu_avg_30s > 70:
            reasons.append(('high_cpu', {'cpu_percent': int(sys_snap.cpu_avg_30s)}))
        if sys_snap and sys_snap.gpu_utilization is not None and sys_snap.gpu_utilization > 60:
            reasons.append(('high_gpu', {'gpu_percent': int(sys_snap.gpu_utilization)}))

        return reasons

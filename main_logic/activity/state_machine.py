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
    ClassifyResult, classify_browser_title, classify_window_title,
    classify_process_name, is_browser_process,
)

from main_logic.activity.snapshot import (
    ActivitySnapshot, ActivityState, GameGenre, GameIntensity, UnfinishedThread,
    WindowObservation, derive_propensity, derive_skip_probability, derive_tone,
)
from main_logic.activity.system_signals import SystemSnapshot
from utils.activity_config import ActivityPreferences, get_activity_preferences


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


def _apply_user_overrides(
    result: ClassifyResult,
    sys_snap: SystemSnapshot,
    prefs: ActivityPreferences,
) -> ClassifyResult:
    """Patch a base classifier result with user-supplied overrides.

    Override priority (highest → lowest):
      1. ``user_app_overrides`` — process-name match (case-insensitive)
      2. ``user_title_overrides`` — title-substring match (case-insensitive)
      3. Static keyword DB result (already in ``result``)
      4. ``user_game_overrides`` — patches intensity/genre on the
         resolved canonical (only when category ends up as 'gaming')

    App / title overrides REPLACE the keyword classification (user wins
    over static DB). Game overrides MERGE on top — they don't change
    category/subcategory/canonical, only intensity/genre.

    Privacy and own_app are special: they always come from the static DB
    (the user shouldn't be able to mark KeePass as "work" — that defeats
    the privacy guarantee). When the static DB result is already
    ``private`` or ``own_app``, app/title overrides are SKIPPED so a
    user override of "work" on KeePass.exe doesn't downgrade the privacy
    classification. Game intensity/genre overrides are still applied
    (those don't change category, only refine within gaming).
    """
    # Privacy + own-app guarantee — skip app/title overrides if the
    # static DB already nailed this as one of those special categories.
    static_locked = result.category in ('private', 'own_app')

    # User app overrides — keyed by lowercased process name
    if not static_locked and prefs.user_app_overrides and sys_snap.process_name:
        key = sys_snap.process_name.lower()
        # Try exact basename match (handles full paths)
        basename = key.replace('\\', '/').rsplit('/', 1)[-1]
        ov = prefs.user_app_overrides.get(basename) or prefs.user_app_overrides.get(key)
        if ov is not None:
            result = ClassifyResult(
                category=ov.category,
                subcategory=ov.subcategory,
                canonical=ov.canonical or sys_snap.process_name,
            )

    # User title overrides — keyed by lowercased title substring.
    # Same locked-categories guarantee.
    if (
        result.category == 'unknown'
        and not static_locked
        and prefs.user_title_overrides
        and sys_snap.window_title
    ):
        title_low = sys_snap.window_title.lower()
        for needle, ov in prefs.user_title_overrides.items():
            if needle in title_low:
                result = ClassifyResult(
                    category=ov.category,
                    subcategory=ov.subcategory,
                    canonical=ov.canonical or sys_snap.window_title,
                )
                break

    # Game intensity/genre override — patches on top of an existing
    # gaming classification. Keyed by canonical name (case-sensitive)
    # to match the keyword DB.
    if (
        result.category == 'gaming'
        and result.subcategory == 'game'
        and result.canonical
        and prefs.user_game_overrides
    ):
        ov = prefs.user_game_overrides.get(result.canonical)
        if ov is not None:
            result = ClassifyResult(
                category=result.category,
                subcategory=result.subcategory,
                canonical=result.canonical,
                intensity=ov.intensity if ov.intensity is not None else result.intensity,
                genre=ov.genre if ov.genre is not None else result.genre,
            )

    return result


def observation_from_system(
    sys_snap: SystemSnapshot,
    prefs: ActivityPreferences | None = None,
) -> WindowObservation | None:
    """Build a ``WindowObservation`` from a raw ``SystemSnapshot``.

    Browser windows are routed to the domain table first (page URL/title
    is more telling than the bare browser name) with title-table
    fallback for branded SaaS apps where the title surfaces the app name
    rather than the domain (e.g. "Notion"). User overrides from
    ``ActivityPreferences`` apply on top of the static keyword DB.
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
            result = classify_process_name(sys_snap.process_name)

    if prefs is not None:
        result = _apply_user_overrides(result, sys_snap, prefs)

    return WindowObservation(
        process_name=sys_snap.process_name,
        title=sys_snap.window_title,
        category=result.category,
        subcategory=result.subcategory,
        canonical=result.canonical,
        is_browser=is_browser,
        intensity=result.intensity,
        genre=result.genre,
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

    def __init__(self, *, prefs: ActivityPreferences | None = None) -> None:
        # Resolve preferences once at construction. Threshold overrides are
        # applied as instance attributes; user override dicts stay live on
        # ``self._prefs`` for runtime lookup (the loader has its own
        # mtime-based cache, so re-fetching from prefs every call is fine).
        if prefs is None:
            prefs = get_activity_preferences()
        self._prefs = prefs
        self._away_idle_seconds = prefs.thresholds.get(
            'away_idle_seconds', AWAY_IDLE_SECONDS,
        )
        self._stale_recovery_seconds = prefs.thresholds.get(
            'stale_recovery_seconds', STALE_RECOVERY_SECONDS,
        )
        self._voice_active_window_seconds = prefs.thresholds.get(
            'voice_active_window_seconds', VOICE_ACTIVE_WINDOW_SECONDS,
        )
        self._focused_work_min_dwell_seconds = prefs.thresholds.get(
            'focused_work_min_dwell_seconds', FOCUSED_WORK_MIN_DWELL_SECONDS,
        )
        self._focused_work_recent_input_seconds = prefs.thresholds.get(
            'focused_work_recent_input_seconds', FOCUSED_WORK_RECENT_INPUT_SECONDS,
        )
        self._casual_browsing_min_dwell_seconds = prefs.thresholds.get(
            'casual_browsing_min_dwell_seconds', CASUAL_BROWSING_MIN_DWELL_SECONDS,
        )
        self._window_switch_transition_threshold = int(prefs.thresholds.get(
            'window_switch_transition_threshold', WINDOW_SWITCH_TRANSITION_THRESHOLD,
        ))
        self._window_history_lookback_seconds = prefs.thresholds.get(
            'window_history_lookback_seconds', WINDOW_HISTORY_LOOKBACK_SECONDS,
        )
        self._transition_recent_window_seconds = prefs.thresholds.get(
            'transition_recent_window_seconds', TRANSITION_RECENT_WINDOW_SECONDS,
        )
        self._unfinished_thread_window_seconds = prefs.thresholds.get(
            'unfinished_thread_window_seconds', UNFINISHED_THREAD_WINDOW_SECONDS,
        )
        self._unfinished_thread_max_followups = int(prefs.thresholds.get(
            'unfinished_thread_max_followups', UNFINISHED_THREAD_MAX_FOLLOWUPS,
        ))
        self._gaming_gpu_threshold_percent = prefs.thresholds.get(
            'gaming_gpu_threshold_percent', GAMING_GPU_THRESHOLD_PERCENT,
        )
        self._gaming_gpu_max_idle_seconds = prefs.thresholds.get(
            'gaming_gpu_max_idle_seconds', GAMING_GPU_MAX_IDLE_SECONDS,
        )

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

        Special handling:
          * ``own_app`` (catgirl app itself in foreground) — observation
            is discarded entirely. Window tracking pretends nothing
            happened so dwell on the previous app keeps accumulating
            and GPU fallback gaming doesn't trip on the catgirl's own
            Live2D / VRM rendering.
          * ``private`` — observation IS recorded (state classifier
            needs to see it to emit state='private'), but title /
            process_name fields are scrubbed before storing so the
            sensitive content never reaches downstream consumers
            (prompt rendering, LLM enrichment, conversation buffers).
        """
        if obs is None:
            return
        ts = now if now is not None else time.time()

        if obs.category == 'own_app':
            return  # transparent — no window update, no history entry

        if obs.category == 'private':
            # Sanitize: keep category + canonical (user/AI sees just
            # "private app foreground"), drop title/process — those are
            # the leaky bits.
            obs = WindowObservation(
                process_name=None,
                title=None,
                category='private',
                subcategory=None,
                canonical=obs.canonical or '[private]',
                is_browser=False,
                intensity=None,
                genre=None,
            )

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
                self._stale_returning_until = ts + self._stale_recovery_seconds
            self._current_state = new_state
            self._state_started_at = ts

        # Apply stale_returning override AFTER the bookkeeping so the
        # underlying state still advances (we want to know what state
        # they "really" entered, just expose the stale window for prompt)
        effective_state: ActivityState = self._current_state
        if ts < self._stale_returning_until and self._current_state != 'away':
            effective_state = 'stale_returning'

        # Game tag pass-through is needed to resolve propensity for gaming
        # subtypes (casual → open). Compute it here BEFORE the propensity
        # lookup; the full assignment to game_intensity / game_genre
        # comes after active_window resolution below.
        _pre_intensity: GameIntensity | None = None
        _pre_genre: GameGenre | None = None
        if (
            effective_state == 'gaming'
            and self._current_window is not None
        ):
            _pre_intensity = self._current_window.intensity  # type: ignore[assignment]
            _pre_genre = self._current_window.genre  # type: ignore[assignment]

        propensity = derive_propensity(
            effective_state,
            game_intensity=_pre_intensity,
            game_genre=_pre_genre,
        )
        reasons = self._build_propensity_reasons(effective_state, ts)

        # Window observation summary. For ``private`` state we redact
        # active_window entirely — even the sanitized canonical we kept
        # internally for state-machine bookkeeping shouldn't ride out
        # to the prompt or LLM enrichment.
        active_window = self._current_window
        if effective_state == 'private':
            active_window = None

        # Switch rate over the lookback window
        switch_rate = sum(
            1 for entry in self._window_history
            if ts - entry.timestamp <= self._window_history_lookback_seconds
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
            and (ts - self._voice_last_rms_at) < self._voice_active_window_seconds
        )

        # Time context
        local = datetime.fromtimestamp(ts)
        period = _hour_to_period(local.hour)

        transitioned_recently = (
            ts - self._state_started_at <= self._transition_recent_window_seconds
            and self._previous_state is not None
        )

        # Unfinished thread: surface it if still within the window
        # and under the follow-up cap. Past the window, retire the record
        # so future ticks don't keep evaluating the same expired data.
        unfinished = None
        if self._unfinished_thread is not None:
            age = ts - self._unfinished_thread['started_at']
            if age > self._unfinished_thread_window_seconds:
                self._unfinished_thread = None
            elif self._unfinished_thread['follow_up_count'] < self._unfinished_thread_max_followups:
                unfinished = UnfinishedThread(
                    text=self._unfinished_thread['tail'],
                    age_seconds=age,
                    follow_up_count=self._unfinished_thread['follow_up_count'],
                    max_follow_ups=self._unfinished_thread_max_followups,
                )

        # Game tags are resolved earlier (pre-propensity); reuse them
        # here for the snapshot fields. Active_window may have been
        # redacted by the private branch — but private and gaming are
        # mutually exclusive states, so nothing's lost.
        game_intensity: GameIntensity | None = _pre_intensity
        game_genre: GameGenre | None = _pre_genre

        # Tone + skip probability — pure derivation from above.
        tone = derive_tone(
            effective_state,
            game_intensity=game_intensity,
            game_genre=game_genre,
        )
        skip_probability = derive_skip_probability(
            effective_state,
            game_intensity=game_intensity,
            game_genre=game_genre,
            overrides=self._prefs.skip_probability_overrides or None,
        )

        return ActivitySnapshot(
            state=effective_state,
            state_age_seconds=ts - self._state_started_at,
            previous_state=self._previous_state,
            transitioned_recently=transitioned_recently,
            stale_returning=(ts < self._stale_returning_until and self._current_state != 'away'),
            propensity=propensity,
            propensity_reasons=reasons,
            skip_probability=skip_probability,
            tone=tone,
            game_intensity=game_intensity,
            game_genre=game_genre,
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
        # ``private`` deliberately does NOT win here: a privacy-app
        # window left open while the user walked away is just an idle
        # situation; nobody's looking at the secrets right now.
        if sys_snap is not None and sys_snap.idle_seconds >= self._away_idle_seconds:
            return 'away'

        # 2. private — sensitive app foreground, user actively present.
        # Wins above voice/gaming/work/etc so the AI never proactively
        # speaks while the password manager / banking app is up.
        win = self._current_window
        if win is not None and win.category == 'private':
            return 'private'

        # 3. voice_engaged — voice mode + recent RMS activity is the
        # strongest "in active conversation" signal we have.
        if (
            self._voice_mode_active
            and (now - self._voice_last_rms_at) < self._voice_active_window_seconds
        ):
            return 'voice_engaged'

        # 4. gaming — actual game (subcategory='game') in foreground.
        # Launchers ('subcategory'='launcher') are intentionally NOT here:
        # browsing the Steam store doesn't mean "playing".
        if win is not None and win.category == 'gaming' and win.subcategory == 'game':
            return 'gaming'

        # 4b. gaming-by-GPU fallback. Catches small / indie / new titles
        # not yet in the keyword DB. Gates:
        #   - active window category MUST be 'unknown' — never override
        #     work/communication/entertainment classifications, those are
        #     surer signals than raw GPU load (ML / video / browser games).
        #   - GPU sustained high.
        #   - User actually present (input within last minute) — long-idle
        #     high GPU is usually background rendering or AFK farming, not
        #     active engagement we should hesitate to interrupt.
        # Own-app foreground was already filtered upstream in update_window
        # (no observation recorded), so the catgirl's own GPU usage doesn't
        # reach this branch.
        if (
            win is not None and win.category == 'unknown'
            and sys_snap is not None and sys_snap.gpu_utilization is not None
            and sys_snap.gpu_utilization >= self._gaming_gpu_threshold_percent
            and sys_snap.idle_seconds <= self._gaming_gpu_max_idle_seconds
        ):
            return 'gaming'

        # 5. focused_work — work-category window with sustained dwell AND
        # recent input. The combo is what filters out "left VS Code open
        # while watching YouTube in another monitor" cases.
        if win is not None and win.category == 'work':
            dwell = now - self._current_window_started_at
            recent_input = (
                self._last_user_msg_at is not None
                and (now - self._last_user_msg_at) <= self._focused_work_recent_input_seconds
            )
            recent_system_active = (
                sys_snap is not None
                and sys_snap.idle_seconds < self._focused_work_recent_input_seconds
            )
            if dwell >= self._focused_work_min_dwell_seconds and (recent_input or recent_system_active):
                return 'focused_work'

        # 6. casual_browsing — entertainment dominates with reasonable dwell.
        if win is not None and win.category == 'entertainment':
            dwell = now - self._current_window_started_at
            if dwell >= self._casual_browsing_min_dwell_seconds:
                return 'casual_browsing'

        # 7. chatting — communication app in foreground. We deliberately
        # do NOT gate on "low CPU" per the user's instruction (signal
        # too unreliable). A short dwell is fine — chat windows are
        # small, often briefly raised to read a message.
        if win is not None and win.category == 'communication':
            return 'chatting'

        # 8. transitioning — no clear category dominates AND there's been
        # a flurry of window switches recently. Note this still produces
        # ``open`` propensity (per user clarification — screen channel
        # always allowed); only the source-weight layer should care.
        switches = sum(
            1 for entry in self._window_history
            if now - entry.timestamp <= self._window_history_lookback_seconds
        )
        if switches >= self._window_switch_transition_threshold:
            return 'transitioning'

        # 9. idle — at the computer (not away) but no clear bucket.
        return 'idle'

    # ── reason strings (for prompt + debugging) ──────────────────

    def _build_propensity_reasons(
        self, state: ActivityState, now: float,
    ) -> list[tuple[str, dict]]:
        """Build structured reasons for the chosen state.

        Each reason is ``(code, params)`` — the code maps to a localized
        template inside ``config.prompts_activity.ACTIVITY_REASON_TEMPLATES``,
        and the params are interpolated at format time. State-machine
        code stays language-agnostic; the prompt formatter renders.
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
        elif state == 'private':
            reasons.append(('state_private', {}))

        # CPU / GPU augmentations — appended only when notably high so
        # we don't add noise to the typical case.
        if sys_snap and sys_snap.cpu_avg_30s > 70:
            reasons.append(('high_cpu', {'cpu_percent': int(sys_snap.cpu_avg_30s)}))
        if sys_snap and sys_snap.gpu_utilization is not None and sys_snap.gpu_utilization > 60:
            reasons.append(('high_gpu', {'gpu_percent': int(sys_snap.gpu_utilization)}))

        return reasons

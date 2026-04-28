"""Activity snapshot types.

The structured output of ``UserActivityTracker.get_snapshot()`` and the
shared vocabulary used between system-signal collection, the state
machine, and the proactive-chat prompt builder.

Design notes
------------

State and propensity are deliberately separate:
  * ``state`` is the inferred user mode (gaming / focused_work / casual_browsing
    / chatting / voice_engaged / idle / transitioning / stale_returning / away).
  * ``propensity`` collapses the state down to a directive the prompt
    builder can act on (closed / restricted_screen_only / open / greeting_window).

Multiple states map to the same propensity — gaming and focused_work
both produce ``restricted_screen_only`` because the prompt's behaviour
is identical for those, even though the upstream cause differs.

The snapshot is always returned by value; callers must not mutate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Type aliases ────────────────────────────────────────────────────

ActivityState = Literal[
    'away',                # No activity for >= AWAY_IDLE_SECONDS
    'stale_returning',     # Just back from away (≤ STALE_RECOVERY_SECONDS)
    'gaming',              # Game window in foreground / known game process
    'focused_work',        # IDE / Office / PDF / etc. + sustained input
    'casual_browsing',     # Entertainment domains/clients dominate
    'chatting',            # IM/email/meeting in foreground + active text
    'voice_engaged',       # Voice mode and recent RMS / VAD activity
    'idle',                # At the computer but no clear activity bucket
    'transitioning',       # Recent rapid window switches / mode change
]


Propensity = Literal[
    'closed',                   # Reserved; not currently emitted (away no longer = PASS)
    'restricted_screen_only',   # Only allow screen-derived chatter; no externals/no reminisce
    'open',                     # Default: any channel allowed
    'greeting_window',          # Stale-returning / first-contact: encourage reminiscence
]


# ── Snapshot dataclass ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WindowObservation:
    """One observed (process, title) pair at a timestamp.

    Held inside ``ActivitySnapshot`` only as the most recent observation;
    the rolling history lives in ``UserActivityTracker``'s buffer.
    """
    process_name: str | None
    title: str | None
    category: str            # 'gaming' | 'work' | 'entertainment' | 'communication' | 'unknown'
    subcategory: str | None  # e.g. 'ide' / 'video' / 'im' / 'game'
    canonical: str | None    # e.g. 'VS Code'
    is_browser: bool


@dataclass(frozen=True, slots=True)
class UnfinishedThread:
    """An open conversation thread the AI may follow up on.

    Set when the AI's last reply contained a question marker (``?`` /
    ``？`` or a sentence-final CN particle like ``吗`` / ``呢`` / ``么``)
    and the user hasn't responded yet. Cleared on user message arrival
    or when the 5-minute window expires.

    Surfaces in ``ActivitySnapshot.unfinished_thread`` so the proactive
    chat prompt can grant a special "thread continuation" allowance —
    even in ``restricted_screen_only`` states (gaming / focused_work)
    where external sources and reminiscence are otherwise forbidden.
    A capped follow-up count (default 2) prevents the AI from harassing
    the user about the same hanging question.
    """
    text: str                 # Short tail of the AI message that opened the thread
    age_seconds: float        # How long the thread has been hanging
    follow_up_count: int      # Times the AI has been allowed to follow up so far
    max_follow_ups: int       # Hard cap (default UNFINISHED_THREAD_MAX_FOLLOWUPS)


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """Inferred user activity at one point in time.

    All fields are presented to the prompt builder. Reasons are intended
    for both human debugging and LLM context — they explain why the
    state machine landed where it did.
    """

    # --- Inferred state ---
    state: ActivityState
    state_age_seconds: float                 # How long the state has held (current run)
    previous_state: ActivityState | None
    transitioned_recently: bool              # True for ~30s after any state flip
    stale_returning: bool                    # True for STALE_RECOVERY_SECONDS after away→active

    # --- Propensity (prompt directive) ---
    propensity: Propensity
    # Structured reasons: each entry is ``(code, params)`` where ``code``
    # is a reason key looked up in ``_REASON_TEMPLATES`` and ``params`` is
    # a dict substituted into that template at format time. Keeps the
    # snapshot language-agnostic — localization happens in
    # ``format_activity_state_section``.
    propensity_reasons: list[tuple[str, dict]] = field(default_factory=list)

    # --- System signals (the raw evidence) ---
    system_idle_seconds: float = 0.0         # GetLastInputInfo, system-wide
    cpu_avg_30s: float = 0.0                 # psutil rolling avg
    cpu_instant: float = 0.0                 # last poll
    active_window: WindowObservation | None = None
    window_switch_rate_5min: int = 0         # unique titles seen in last 5min
    os_signals_available: bool = True
    """False in degraded / remote-deployment mode — see ``SystemSnapshot``.

    When False, ``system_idle_seconds``, ``cpu_*``, and ``active_window``
    don't reflect the user's actual machine, so the prompt formatter
    skips/marks them and the proactive AI knows not to over-trust
    OS-derived states.
    """

    # --- Per-session signals ---
    seconds_since_user_msg: float | None = None
    seconds_since_ai_msg: float | None = None
    voice_recent_rms_active: bool = False    # Is voice RMS / VAD high in last 8s
    voice_mode_active: bool = False

    # --- Time context ---
    hour: int = 0                             # 0-23 local
    weekday: int = 0                          # 0=Mon
    period: str = 'day'                       # 'morning' | 'afternoon' | 'evening' | 'night'

    # --- Unfinished thread (5-min window, max 2 follow-ups) ---
    # Set when the AI's last reply contained a question and the user
    # hasn't responded. Phase 2 prompt is allowed to follow up on this
    # thread regardless of state — including gaming / focused_work where
    # external sources and reminiscence are otherwise forbidden.
    unfinished_thread: UnfinishedThread | None = None

    # --- Emotion-tier LLM enrichment (cached, advisory) ---
    # Soft scores across behavioural states (0.0-1.0 each, not
    # normalised — independent probabilities). Populated by the
    # ``activity_guess`` 20s background loop on the tracker. Empty when
    # the loop hasn't run yet or the LLM call failed.
    #
    # The HARD ``state`` field (rule-derived, above) remains the
    # authority for propensity / source filtering. ``activity_scores``
    # is advisory context for the proactive AI to reconcile against —
    # if the LLM disagrees with the rules, the prompt sees both and
    # picks the angle.
    activity_scores: dict[str, float] = field(default_factory=dict)
    # One-sentence narrative description from the same LLM pass —
    # gives the proactive AI a richer picture than the structured
    # signals alone. Empty until first computed.
    activity_guess: str = ''

    # --- Semantic open-thread detection (LLM-based, lazy) ---
    # Populated by ``kickoff_open_threads_compute`` (typically run in
    # parallel with proactive Phase 1). Each entry is a short phrase
    # describing a topic that was raised but not closed — covers cases
    # the question-mark heuristic misses (AI promises, abandoned user
    # threads, etc.). Cache invalidates on the next user message.
    open_threads: list[str] = field(default_factory=list)


# ── State → propensity mapping ──────────────────────────────────────

_STATE_TO_PROPENSITY: dict[ActivityState, Propensity] = {
    'away':             'open',                    # User said: away does not auto-PASS;
                                                   # frontend backoff naturally throttles
    'stale_returning':  'greeting_window',
    'gaming':           'restricted_screen_only',
    'focused_work':     'restricted_screen_only',
    'casual_browsing':  'open',
    'chatting':         'open',
    'voice_engaged':    'open',
    'idle':             'open',
    'transitioning':    'open',                    # User said: transitioning still allows screen;
                                                   # external sources just get a small weight cut
                                                   # (handled in source-weight layer, not propensity)
}


def state_to_propensity(state: ActivityState) -> Propensity:
    """Map an ``ActivityState`` to its prompt-level propensity directive."""
    return _STATE_TO_PROPENSITY.get(state, 'open')


# ── Localized strings for prompt injection ─────────────────────────
# Imported from config/prompts_activity.py — multi-language dicts must
# live there per the project's I18N_NOT_IN_CONFIG lint convention.
# config/ does not import from main_logic/, so only pure string→string
# mappings live there; snapshot.py imports them and uses them below.
from config.prompts_activity import (  # noqa: E402
    OS_DEGRADED_MARKER as _OS_DEGRADED_MARKER,
    PROPENSITY_DIRECTIVES as _PROPENSITY_DIRECTIVES,
    REASON_TEMPLATES as _REASON_TEMPLATES,
    STATE_LABELS as _STATE_LABELS,
    STATE_SECTION_LABELS as _STATE_SECTION_LABELS,
)


def _render_reason(reason: tuple[str, dict], lang_key: str) -> str:
    """Render one structured reason via the per-language template table.

    Falls back to English template if the locale entry is missing,
    and to the raw reason code if even English is missing (defensive —
    keeps ``state_section`` printable when a new code is added but the
    table hasn't been updated yet).
    """
    code, params = reason
    table = _REASON_TEMPLATES.get(lang_key, _REASON_TEMPLATES['en'])
    template = table.get(code) or _REASON_TEMPLATES['en'].get(code) or code
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return code


def _normalize_lang(lang: str) -> str:
    if not lang:
        return 'zh'
    low = lang.lower()
    if low.startswith('zh'):
        return 'zh'
    if low.startswith('ja'):
        return 'ja'
    if low.startswith('ko'):
        return 'ko'
    if low.startswith('ru'):
        return 'ru'
    return 'en'


def _format_seconds_ago(seconds: float | None, labels: dict[str, str]) -> str:
    if seconds is None:
        return labels['never']
    if seconds < 90:
        return labels['seconds_ago_fmt'].format(seconds=seconds)
    if seconds < 3600:
        return labels['minutes_ago_fmt'].format(minutes=seconds / 60)
    return labels['hours_ago_fmt'].format(hours=seconds / 3600)


def format_activity_state_section(snap: 'ActivitySnapshot', lang: str = 'zh') -> str:
    """Render an ``ActivitySnapshot`` into a localized prompt section.

    The result is a multi-line string ready to be substituted into the
    Phase 2 generate prompt's ``{state_section}`` placeholder. Falls
    back to English if ``lang`` isn't in the supported set.

    Layout (zh example, compact):

        ===活动状态===
        focused_work（专注工作中）→ 只就屏幕内容轻聊一句
        专注 VS Code 已 200s; CPU 30s 75%
        18:00 傍晚 | 用户 30s前 | AI 2min前
        未收尾话题:「…你今天准备几点出发?」(60s前,已跟进 0/2)
        评估: focused_work 0.7 · chatting 0.2 · idle 0.1
        叙述: 主人在 VS Code 里调试，刚发了求助
        开放话题:
        - AI 答应等会帮看测试还没看
        - 主人提到 phase 1 跳过逻辑没说完
        ===状态结束===

    Conditional rendering — empty / default fields are omitted entirely:
      * "user/AI msg" line: only includes sides that have a value;
        when both are None, line dropped.
      * Activity scores: only entries with score >= 0.05, top 3.
      * Active-window line dropped — its info already appears in the
        rule-reason line ("专注 VS Code 已 200s" carries the canonical
        name), so re-stating wastes tokens.
    """
    if snap is None:
        return ''
    L = _normalize_lang(lang)
    labels = _STATE_SECTION_LABELS.get(L, _STATE_SECTION_LABELS['en'])
    state_label = _STATE_LABELS.get(L, _STATE_LABELS['en']).get(snap.state, snap.state)
    propensity_directive = _PROPENSITY_DIRECTIVES.get(L, _PROPENSITY_DIRECTIVES['en']).get(
        snap.propensity, snap.propensity,
    )

    period_key = f'period_{snap.period}'
    period_label = labels.get(period_key, snap.period)

    # Header: append a degraded marker when OS signals aren't available.
    # Tells the proactive AI not to over-trust window/idle-derived state.
    header = labels['header']
    if not snap.os_signals_available:
        header = header + ' ' + _OS_DEGRADED_MARKER.get(L, _OS_DEGRADED_MARKER['en'])
    lines: list[str] = [header]

    # Line 1: state + propensity directive on a single line.

    lines: list[str] = [header]

    # Line 1: state + propensity directive on a single line.
    lines.append(f"{snap.state}（{state_label}）→ {propensity_directive}")

    # Line 2: rule reasons (skip if empty — happens for unknown states).
    if snap.propensity_reasons:
        rendered_reasons = [_render_reason(r, L) for r in snap.propensity_reasons]
        lines.append('; '.join(rendered_reasons))

    # Line 3: time + msg recency. Compact form, side(s) omitted when no data.
    time_str = labels['time_fmt'].format(hour=snap.hour, period=period_label)
    user_str = (
        _format_seconds_ago(snap.seconds_since_user_msg, labels)
        if snap.seconds_since_user_msg is not None else None
    )
    ai_str = (
        _format_seconds_ago(snap.seconds_since_ai_msg, labels)
        if snap.seconds_since_ai_msg is not None else None
    )
    if user_str and ai_str:
        lines.append(labels['time_user_ai_fmt'].format(time=time_str, user=user_str, ai=ai_str))
    elif user_str:
        lines.append(labels['time_user_only_fmt'].format(time=time_str, user=user_str))
    elif ai_str:
        # Rare (AI spoke but no user msg yet) — fall back on time only.
        lines.append(labels['time_only_fmt'].format(time=time_str))
    else:
        lines.append(labels['time_only_fmt'].format(time=time_str))

    # Unfinished thread: single compact line.
    if snap.unfinished_thread is not None:
        thread = snap.unfinished_thread
        age_str = _format_seconds_ago(thread.age_seconds, labels)
        tail = thread.text.strip().replace('\n', ' ')
        if len(tail) > 40:
            tail = tail[-40:]
        lines.append(labels['unfinished_thread_fmt'].format(
            tail=tail, age=age_str,
            used=thread.follow_up_count, cap=thread.max_follow_ups,
        ))

    # LLM enrichment — populated only when the emotion-tier loop has run
    # and returned successfully; otherwise quietly omitted.
    if snap.activity_scores:
        # Drop near-zero entries (< 0.05) and keep top 3 — anything more
        # is noise the proactive prompt won't usefully act on.
        ordered = sorted(
            (kv for kv in snap.activity_scores.items() if kv[1] >= 0.05),
            key=lambda kv: -kv[1],
        )[:3]
        if ordered:
            score_str = ' · '.join(f'{name} {score:.1f}' for name, score in ordered)
            lines.append(f"{labels['activity_scores_label']}: {score_str}")
    if snap.activity_guess:
        lines.append(f"{labels['activity_guess_label']}: {snap.activity_guess}")
    if snap.open_threads:
        lines.append(f"{labels['open_threads_label']}:")
        for thread_text in snap.open_threads[:3]:
            lines.append(f'- {thread_text}')

    lines.append(labels['footer'])
    return '\n'.join(lines)

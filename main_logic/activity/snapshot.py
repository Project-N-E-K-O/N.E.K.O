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
    'private',             # Sensitive app foreground — DO NOT classify or
                           # cache. Propensity hard-pinned to ``closed`` so
                           # proactive chat skips the round entirely. The
                           # tracker also bypasses LLM enrichment + buffers
                           # so the user's secret never leaves the process.
]


Propensity = Literal[
    'closed',                   # Hard skip — do not surface anything. Currently
                                # emitted only by ``private`` state (privacy
                                # blacklist hit). All other states stay open
                                # in some form so the AI keeps a baseline of
                                # presence; ``closed`` is reserved for
                                # "user's screen is showing something we
                                # promised not to look at".
    'restricted_screen_only',   # Only allow screen-derived chatter; no externals/no reminisce
    'open',                     # Default: any channel allowed
    'greeting_window',          # Stale-returning / first-contact: encourage reminiscence
]


# Tone modifier — a style hint orthogonal to propensity. Propensity says
# *what kind of source* the AI may draw from; tone says *how to deliver
# it*. Phase 2 prompt renders the tone hint as a single line so the AI
# can adapt voice without changing source filtering.
#
# Six tones, deliberately vivid (one-line prompt hint each, see
# ``ACTIVITY_TONE_HINTS`` in ``config/prompts_activity.py``):
#   * ``terse``   — competitive games, rhythm games: short, low-intrusion
#   * ``hushed``  — horror games: deliberately quiet, atmospheric
#   * ``mellow``  — immersive RPG / story-driven: relaxed in-the-moment
#   * ``playful`` — casual gaming, casual_browsing: light, joke-friendly
#   * ``warm``    — voice / chatting / stale_returning: conversational
#   * ``concise`` — focused_work / idle / default: short, professional
#
# ``silent`` is intentionally not a tone — silencing the AI is the
# ``skip_probability`` mechanism's job (probabilistic gate before any
# tone matters), and conflating "voice" with "presence" muddies both.
ActivityTone = Literal[
    'terse',
    'hushed',
    'mellow',
    'playful',
    'warm',
    'concise',
]


# Game intensity / genre tags (only meaningful when state == 'gaming').
# ``intensity`` drives propensity + skip_probability defaults; ``genre``
# refines tone selection (horror → hushed, sim → playful, etc.).
GameIntensity = Literal[
    'competitive',  # Multi-player adversarial; interruption causes mistakes
    'casual',       # Pause-anytime, no momentum cost (sim, idle, party)
    'immersive',    # Single-player narrative; interruption breaks flow
    'varied',       # Default — unclassified, indie, mod-heavy, gray zone
]


# Genre tags are a free-form-ish string but with a recommended vocabulary
# below. Code never branches on the exact value; only ``horror`` is
# called out for its tone override. New genres can be added in
# ``activity_keywords`` without state-machine changes.
GameGenre = Literal[
    'fps', 'moba', 'rpg', 'sim', 'horror', 'racing', 'rhythm',
    'strategy', 'sports', 'party', 'action', 'misc',
]


# ── Snapshot dataclass ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WindowObservation:
    """One observed (process, title) pair at a timestamp.

    Held inside ``ActivitySnapshot`` only as the most recent observation;
    the rolling history lives in ``UserActivityTracker``'s buffer.

    ``intensity`` / ``genre`` are populated only for games that were
    tagged in the keyword DB or via ``user_game_overrides``. Both
    ``None`` for non-games and untagged games — state machine treats
    those as ``intensity='varied' / genre='misc'`` by convention
    (conservative fallback, same behaviour as PR #1015).
    """
    process_name: str | None
    title: str | None
    category: str            # 'gaming' | 'work' | 'entertainment' | 'communication' | 'unknown' | 'private' | 'own_app'
    subcategory: str | None  # e.g. 'ide' / 'video' / 'im' / 'game'
    canonical: str | None    # e.g. 'VS Code'
    is_browser: bool
    intensity: str | None = None  # GameIntensity-shaped, only when category='gaming' subcategory='game'
    genre: str | None = None      # GameGenre-shaped, same gating


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

    # --- Skip probability (independent gate before any source filtering) ---
    # ``skip_probability`` is rolled by the proactive_chat router at Phase 1
    # entry — if random() < skip_probability AND no unfinished_thread is
    # active, the round is skipped entirely (no LLM call, no source fetch).
    # Default 0 means "always proceed". Defaults are derived from
    # (state, intensity, genre) by the state machine; user overrides via
    # ``user_preferences.json::__global_conversation__::activity::skip_probability_overrides`` can tune
    # them per-combo. Setting 1.0 = fully silent for that combo (user
    # opt-in "don't talk at all during X"); 0.0 = no skip, just rely on
    # propensity.
    #
    # The unfinished_thread guard means the AI's open questions still get
    # follow-up windows even in high-skip states — interrupting yourself
    # mid-thread is rude even when you're trying to be quiet.
    skip_probability: float = 0.0

    # --- Tone modifier (style hint, orthogonal to propensity) ---
    # See ``ActivityTone`` doc for the six values. Default ``concise``
    # is the safe fallback — short, professional, won't surprise.
    tone: ActivityTone = 'concise'

    # --- Game classification (only meaningful when state == 'gaming') ---
    # Both None for non-gaming states. ``intensity`` drives propensity
    # + skip_probability; ``genre`` refines tone (horror → hushed). User
    # can override per-game via ``user_game_overrides`` in preferences.
    game_intensity: GameIntensity | None = None
    game_genre: GameGenre | None = None
    # Structured reasons: each entry is ``(code, params)`` where ``code``
    # is a reason key looked up in ``ACTIVITY_REASON_TEMPLATES`` (in
    # ``config/prompts_activity.py``) and ``params`` is a dict
    # substituted into that template at format time. Keeps the snapshot
    # language-agnostic — localization happens in
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
    'private':          'closed',                  # Sensitive app foreground — hard skip.
}


def state_to_propensity(state: ActivityState) -> Propensity:
    """Map an ``ActivityState`` to its prompt-level propensity directive."""
    return _STATE_TO_PROPENSITY.get(state, 'open')


def derive_propensity(
    state: ActivityState,
    *,
    game_intensity: GameIntensity | None = None,
    game_genre: GameGenre | None = None,
) -> Propensity:
    """Pick the propensity, allowing game intensity to override the default.

    The base mapping in ``_STATE_TO_PROPENSITY`` collapses all gaming
    states to ``restricted_screen_only`` (PR #1015 behaviour). When the
    state machine has more information (intensity tagged in the keyword
    DB or via user override), this function refines:

      * ``casual`` gaming → ``open``  (animal crossing / stardew —
                                       chatting while playing is fine)
      * ``competitive`` / ``immersive`` / ``varied`` / untagged →
        keep the default (``restricted_screen_only``); skip_probability
        and tone do the further differentiation.

    Genre is currently a tone-axis only (no genre flips propensity);
    parameter is accepted for symmetry with ``derive_tone`` /
    ``derive_skip_probability`` and forward compatibility.
    """
    if state == 'gaming' and game_intensity == 'casual':
        return 'open'
    return state_to_propensity(state)


# ── State + game tag → tone derivation ─────────────────────────────
#
# Tone is a single-axis style hint, derived from the combination of
# state and (when gaming) intensity/genre. Kept here rather than in the
# state machine because it's a pure mapping — no time/state evolution.
# Tests can pin the table directly.
#
# Override priority (highest → lowest):
#   1. state == 'voice_engaged'    → 'warm'  (voice flow trumps everything)
#   2. state == 'private'          → 'concise' (won't be rendered anyway —
#                                              propensity=closed gates first)
#   3. state == 'gaming':
#       intensity=competitive       → 'terse'
#       intensity=immersive,
#         genre=horror              → 'hushed'
#       intensity=immersive,
#         genre=other               → 'mellow'
#       intensity=casual            → 'playful'
#       intensity=varied / None     → 'concise' (conservative fallback)
#   4. state == 'casual_browsing'  → 'playful'
#   5. state == 'chatting'         → 'warm'
#   6. state == 'stale_returning'  → 'warm' (greeting moment)
#   7. state == 'focused_work'     → 'concise'
#   8. state in {idle, transitioning, away} → 'concise' (default)
def derive_tone(
    state: ActivityState,
    *,
    game_intensity: GameIntensity | None = None,
    game_genre: GameGenre | None = None,
) -> ActivityTone:
    """Pick the tone hint for the current activity context.

    Pure function of inputs — no I/O, no time. Called from the state
    machine on every snapshot construction, and from tests directly.
    """
    if state == 'voice_engaged':
        return 'warm'
    if state == 'private':
        # Won't be rendered (closed propensity skips proactive entirely),
        # but we return a value to keep the type total.
        return 'concise'
    if state == 'gaming':
        if game_intensity == 'competitive':
            return 'terse'
        if game_intensity == 'immersive':
            if game_genre == 'horror':
                return 'hushed'
            return 'mellow'
        if game_intensity == 'casual':
            return 'playful'
        # game_intensity in {'varied', None}
        return 'concise'
    if state in ('casual_browsing',):
        return 'playful'
    if state in ('chatting', 'stale_returning'):
        return 'warm'
    # focused_work / idle / transitioning / away
    return 'concise'


# ── Default skip_probability for gaming subtypes ───────────────────
#
# Source: design doc + user direction (concise: competitive, immersive
# horror are the only two non-zero defaults; casual/immersive_rpg/varied
# all stay at 0). User can shift via ``skip_probability_overrides``
# in preferences. Keys are gaming-only; non-gaming states always have
# default 0 (skip is not a propensity-strength choice for non-game).
_DEFAULT_GAMING_SKIP_PROB: dict[tuple[GameIntensity, str | None], float] = {
    ('competitive', None):     0.3,   # Any competitive game (genre doesn't refine further)
    ('immersive',   'horror'): 0.3,   # Atmospheric tension — interruption breaks immersion
    ('immersive',   None):     0.0,   # RPG / story — propensity already restricts to screen
    ('casual',      None):     0.0,   # Pause-anytime
    ('varied',      None):     0.0,   # Unknown — don't make conservative guess any noisier
}


def derive_skip_probability(
    state: ActivityState,
    *,
    game_intensity: GameIntensity | None = None,
    game_genre: GameGenre | None = None,
    overrides: dict[str, float] | None = None,
) -> float:
    """Pick the skip probability for the current activity context.

    ``overrides`` is the user's per-combo dict from
    ``user_preferences.json::__global_conversation__::activity::skip_probability_overrides``. Keys are
    string combos like ``'competitive'`` (any genre), ``'immersive_horror'``
    (intensity_genre with underscore), or ``'casual'``. Override values
    in [0, 1] take precedence over defaults.
    """
    if state != 'gaming' or game_intensity is None:
        return 0.0

    # User override lookup — try most specific (intensity_genre) first
    if overrides:
        if game_genre and game_intensity != 'varied':
            key_specific = f'{game_intensity}_{game_genre}'
            if key_specific in overrides:
                v = overrides[key_specific]
                return max(0.0, min(1.0, float(v)))
        if game_intensity in overrides:
            v = overrides[game_intensity]
            return max(0.0, min(1.0, float(v)))

    # Default lookup — try genre-specific first, then intensity-only.
    if game_genre and game_intensity != 'varied':
        key = (game_intensity, game_genre)
        if key in _DEFAULT_GAMING_SKIP_PROB:
            return _DEFAULT_GAMING_SKIP_PROB[key]
    return _DEFAULT_GAMING_SKIP_PROB.get((game_intensity, None), 0.0)


# ── Localized strings for prompt injection ─────────────────────────
#
# All multi-language string tables live in ``config/prompts_activity``
# per the project i18n convention: every translatable string must sit
# under ``config/prompts_*`` so that adding a new language is a single
# pass over that directory. The prompt-hygiene linter only catches
# *flat* ``{lang_code: str}`` dicts, but the rule applies to nested
# ``{lang: {key: str}}`` tables too — they just have to be moved by
# hand. ``snapshot.py`` keeps only the formatter; the strings are
# imported below.
from config.prompts_activity import (
    ACTIVITY_PROPENSITY_DIRECTIVES,
    ACTIVITY_REASON_TEMPLATES,
    ACTIVITY_STATE_LABELS,
    ACTIVITY_STATE_SECTION_LABELS,
    ACTIVITY_TONE_HINTS,
    OS_DEGRADED_MARKER,
)


def _render_reason(reason: tuple[str, dict], lang_key: str) -> str:
    """Render one structured reason via the per-language template table.

    Falls back to English template if the locale entry is missing,
    and to the raw reason code if even English is missing (defensive —
    keeps ``state_section`` printable when a new code is added but the
    table hasn't been updated yet).
    """
    code, params = reason
    table = ACTIVITY_REASON_TEMPLATES.get(lang_key, ACTIVITY_REASON_TEMPLATES['en'])
    template = table.get(code) or ACTIVITY_REASON_TEMPLATES['en'].get(code) or code
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

        ======以下为活动状态======
        focused_work（专注工作中）→ 只就屏幕内容轻聊一句
        专注 VS Code 已 200s; CPU 30s 75%
        18:00 傍晚 | 用户 30s前 | AI 2min前
        未收尾话题:「…你今天准备几点出发?」(60s前,已跟进 0/2)
        评估: focused_work 0.7 · chatting 0.2 · idle 0.1
        叙述: 主人在 VS Code 里调试，刚发了求助
        开放话题:
        - AI 答应等会帮看测试还没看
        - 主人提到 phase 1 跳过逻辑没说完
        ======以上为活动状态======

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
    # Defense in depth: when propensity is ``closed`` (currently emitted
    # only by ``private`` state) emit nothing. Proactive chat already
    # short-circuits on ``closed`` before reaching this formatter, but
    # other consumers (debug logging, future side panels, prompt
    # snapshotters) might pass a closed snapshot through. Rendering
    # state name / reason templates / msg-recency / cached enrichment
    # for a private snapshot would leak the fact that the user just
    # opened a sensitive app — and potentially residual cached context
    # from before that app opened. Empty string everywhere is the only
    # leak-free contract.
    if snap.propensity == 'closed':
        return ''
    L = _normalize_lang(lang)
    labels = ACTIVITY_STATE_SECTION_LABELS.get(L, ACTIVITY_STATE_SECTION_LABELS['en'])
    state_label = ACTIVITY_STATE_LABELS.get(L, ACTIVITY_STATE_LABELS['en']).get(
        snap.state, snap.state,
    )
    propensity_directive = ACTIVITY_PROPENSITY_DIRECTIVES.get(
        L, ACTIVITY_PROPENSITY_DIRECTIVES['en'],
    ).get(snap.propensity, snap.propensity)

    period_key = f'period_{snap.period}'
    period_label = labels.get(period_key, snap.period)

    # Header: append a degraded marker when OS signals aren't available.
    # Tells the proactive AI not to over-trust window/idle-derived state.
    header = labels['header']
    if not snap.os_signals_available:
        header = header + ' ' + OS_DEGRADED_MARKER.get(L, OS_DEGRADED_MARKER['en'])
    lines: list[str] = [header]

    # Line 1: state + propensity directive on a single line.
    lines.append(f"{snap.state}（{state_label}）→ {propensity_directive}")

    # Line 1.5: tone hint (style modifier orthogonal to propensity).
    # Skip rendering when the state would already be silenced (closed)
    # or when tone is the safe default ``concise`` — saves a token line
    # in the common case where there's nothing notable to say about
    # voice. Renders as a single line: "口吻：{hint}".
    if snap.propensity != 'closed' and snap.tone != 'concise':
        tone_hints = ACTIVITY_TONE_HINTS.get(L, ACTIVITY_TONE_HINTS['en'])
        tone_text = tone_hints.get(snap.tone)
        if tone_text:
            tone_label = labels.get('tone_label', 'tone')
            lines.append(f"{tone_label}: {tone_text}")

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

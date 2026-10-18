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
"""Shared module-level constants and helpers for the ``main_logic.core`` package.

Split out of the former single-file ``main_logic/core.py`` as a pure move (no
behavior change). The package ``__init__`` re-exports every name defined here,
so existing ``main_logic.core.<name>`` imports and test monkeypatches keep
working unchanged.
"""
import contextvars
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.language_utils import normalize_language_code, get_global_language_full
from utils.logger_config import get_module_logger


# Sentinel for `send_lanlan_response(request_id=...)` so we can tell apart
# "caller didn't pass it (use shared field as fallback)" from "caller
# explicitly passed None to mean 'no request id'". A normal default of
# None collapses both into the same code path and would let recovery /
# proactive paths accidentally bind their messages to a newer request_id.
_REQUEST_ID_UNSET: Any = object()
_HANDSHAKE_OVERRIDE_UNSET: Any = object()
_MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX = 64
_VOICE_PROACTIVE_ACK_GRACE_S = 0.05
_PASSIVE_MEDIA_SESSION_UPDATE_ACK_TIMEOUT_S = 5.0
_TEXT_SESSION_INPUT_TYPES = frozenset({"text", "avatar_drop_image", "user_image"})
_IMAGE_INPUT_TYPES = frozenset({"screen", "camera", "avatar_drop_image", "user_image"})
_LIVE_VISION_STREAM_INPUT_TYPES = frozenset({"screen", "camera"})
_CONTEXT_APPEND_DEDUP_TTL_SECONDS = 120.0
_CONTEXT_APPEND_DEDUP_MAX_ENTRIES = 256
_CONTEXT_APPEND_READY_FLUSH_MAX_PASSES = 8
_CONTEXT_APPEND_DEFAULT_MAX_TOKENS = 1000
_CONTEXT_APPEND_SOURCE_MAX_TOKENS = {
    "game.icebreaker": 500,
    "game.scripted": 1000,
    "game.realtime_context": 1000,
    "game.postgame": 1500,
    "proactive.context": 1000,
    "proactive.callback": 1000,
    "topic.hook": 1000,
    "topic.material": 1000,
    "realtime.prime": 1000,
    # User ban-topic directive block: ordinary corpora fall well below the default 1000 tokens (20 real Chinese/Japanese
    # entries measured only 419-449), but the theoretical maximum does not. Maximum term length 40
    # (_TERM_MAX_LEN) times 20 active entries (USER_DIRECTIVE_MAX_ACTIVE) reaches 1727 with
    # high-token-density kana.
    # Exceeding the limit is worse than losing a few entries: request_id derives from the complete term set,
    # so truncated retries either deduplicate or append the same truncated payload again, permanently excluding
    # omitted directives (codex). Register a budget covering the theoretical maximum to close this latent path.
    # tests/unit/test_user_directives_midsession_inject.py guards that
    # worst-case rendered blocks fit this budget; raising either constant above makes it fail.
    "user_directives": 2000,
}
# This table applies only to prime_context's fallback path, reachable only for
# lifetime in {"current_session", "session_family"}. Registering a pure
# next_session source such as user_directives here creates dead configuration,
# misleadingly suggesting delivery through realtime instructions; do not add it.
_CONTEXT_APPEND_BARE_PRIME_SOURCES = frozenset({
    "game.realtime_context",
    "game.postgame",
})


_VOICE_ECHO_LOOKBACK_SECONDS = 20.0
_VOICE_ECHO_LOOKBACK_CHARS = 1200
_VOICE_ECHO_MIN_NORMALIZED_CHARS = 6
_VOICE_ECHO_MIN_WINDOW_CHARS = 10
_VOICE_ECHO_SIMILARITY_THRESHOLD = 0.88
_VOICE_ECHO_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)


def _normalize_voice_echo_text(text: str) -> str:
    return _VOICE_ECHO_NORMALIZE_RE.sub("", str(text or "").casefold())


def _looks_like_recent_ai_echo(transcript: str, recent_ai_text: str) -> bool:
    """Return True when STT text is probably the assistant's own recent audio.

    This intentionally requires a close text match. Voice barge-in during AI
    playback should keep flowing unless it resembles the AI text that was just
    rendered/spoken.
    """
    transcript_norm = _normalize_voice_echo_text(transcript)
    if len(transcript_norm) < _VOICE_ECHO_MIN_NORMALIZED_CHARS:
        return False
    recent_norm = _normalize_voice_echo_text(recent_ai_text)
    if len(recent_norm) < _VOICE_ECHO_MIN_NORMALIZED_CHARS:
        return False
    if len(transcript_norm) > len(recent_norm):
        return SequenceMatcher(None, transcript_norm, recent_norm).ratio() >= _VOICE_ECHO_SIMILARITY_THRESHOLD
    if len(transcript_norm) < _VOICE_ECHO_MIN_WINDOW_CHARS:
        return False
    if transcript_norm in recent_norm:
        return True

    window_len = len(transcript_norm)
    step = max(1, window_len // 3)
    best = 0.0
    last_start = len(recent_norm) - window_len
    starts = list(range(0, last_start + 1, step))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    for start in starts:
        candidate = recent_norm[start:start + window_len]
        best = max(best, SequenceMatcher(None, transcript_norm, candidate).ratio())
        if best >= _VOICE_ECHO_SIMILARITY_THRESHOLD:
            return True
    return False


# Logger for the whole package. Bound to the literal package name rather than
# ``__name__`` so every submodule keeps logging under the exact pre-split
# logger name "N.E.K.O.Main.main_logic.core" (tests and log routing key off it).
logger = get_module_logger("main_logic.core", "Main")


# After this user-silence threshold, the background loop proactively ends the session, making the next message
# start_session(new=False) fetch /new_dialog with fresh time, long-gap hints, and holiday
# context, avoiding long-idle sessions stuck on the previous evening.
# Intentionally check much more often than the threshold, about every minute, avoiding a full extra interval
# after 30:01 of silence.
IDLE_SESSION_RESET_THRESHOLD_SECONDS = 1800
IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS = 60

# Frontend text-session hard timeout awaiting session_started (static/app/app-buttons.js
# setTimeout(..., 15000)). Bound start_session deduplication waits for in-flight startup before sending this
# request's ack by that timeout. A later session_started is meaningless because the frontend
# already rejected and sent end_session; it naturally bounds the useful acknowledgment window.
FRONTEND_START_SESSION_TIMEOUT_SECONDS = 15.0

# Bound in-flight completion waiting for cross-mode restarts well below frontend timeout: target-mode startup
# still needs seconds afterward, including up to about 12 s for TTS readiness. Spending most of 15 s
# waiting would deliver session_started after the frontend deadline, causing timeout or orphan sessions
# acknowledged after reset (Codex P2). Use half the frontend timeout, leaving about 7.5 s for restart;
# abandon if in-flight startup misses this window, retaining baseline frontend timeout without orphans. Normal
# text startup takes 1-3 s. Combined worst-case cold TTS and near-deadline in-flight completion
# can still exceed 15 s; start_session's final connection/abandonment checks and restart guards handle that.
CROSS_MODE_RESTART_WAIT_SECONDS = FRONTEND_START_SESSION_TIMEOUT_SECONDS / 2

# Expected sid for proactive calls to prompt_ephemeral.
# prompt_ephemeral enqueues TTS through on_text_delta=handle_text_data callbacks, potentially preempted by
# user input clearing queues and changing current_speech_id in stream_text.
# handle_text_data / handle_output_transcript inspect this contextvar: if set and different from
# current_speech_id, these chunks no longer belong to the current turn and must be discarded,
# preventing proactive text from receiving the user's new sid and entering reply TTS.
# contextvars are task-local and cannot leak into the separate user stream_text task.
_proactive_expected_sid: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_proactive_expected_sid', default=None,
)

# Startup greeting text that crossed the real frontend publish boundary in the
# current proactive task.  ``prompt_ephemeral`` accumulates model output before
# awaiting its delta callback, so its final committed text can contain a suffix
# that was dropped after user preemption.  The per-task list lets the greeting
# flow persist only chunks that ``send_lanlan_response`` actually published.
_proactive_published_text_chunks: contextvars.ContextVar[list[str] | None] = (
    contextvars.ContextVar('_proactive_published_text_chunks', default=None)
)

# Unrecoverable TTS errors forbid respawn: insufficient balance, invalid API keys, or free-service allow/blocklist denial.
NO_RETRY_TTS_CODES = {'API_ARREARS', 'API_KEY_REJECTED', 'TTS_CONFIG_INVALID', 'API_ACCESS_DENIED'}
# TTS errors reported immediately, bypassing the third-error threshold; quota errors have no timed retry, but reply-triggered implicit retries remain.
IMMEDIATE_REPORT_TTS_CODES = NO_RETRY_TTS_CODES | {'API_QUOTA_TIME'}
# Timed respawn interval after TTS-worker unavailability; double per API_RATE_LIMIT up to the next constant's cap.
TTS_RESPAWN_DELAY_SECONDS = 13
TTS_RATE_LIMIT_MAX_RESPAWN_DELAY_SECONDS = 300
# Rate-limit deadline tolerance only for timed respawn: asyncio timers can wake early by clock resolution and must not reject themselves.
TTS_RATE_LIMIT_DEADLINE_SLACK_SECONDS = 1.0


_STATIC_LOCALES_DIR = Path(__file__).resolve().parents[2] / "static" / "locales"


@lru_cache(maxsize=16)
def _load_locale_messages(locale_code: str) -> dict:
    try:
        with (_STATIC_LOCALES_DIR / f"{locale_code}.json").open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_chat_locale_text(language: str | None, key: str, fallback: str) -> str:
    return get_locale_text(language, 'chat.' + key, fallback)


def get_locale_text(language: str | None, dotted_key: str, fallback: str) -> str:
    """Look up ``dotted_key`` (e.g. ``visit.debrief.question``) in ``static/locales``; ``fallback`` when absent.

    Language chain: explicit ``language`` (else the global full code), its
    short code, ``en``, ``zh-CN`` (``zh-TW`` is kept, issue #2500).
    """
    # Fallback must use full locale. format='full' below preserves existing orthography but cannot recover
    # lost distinctions: get_global_language() returns short codes, collapsing traditional Chinese to
    # zh before entry, then expanding to zh-CN so static/locales/zh-TW.json is never read
    # (issue #2500). Explicit language arguments still take precedence.
    raw_lang = language or get_global_language_full()
    try:
        lang_full = normalize_language_code(raw_lang, format='full')
    except Exception:
        lang_full = raw_lang or 'en'
    try:
        lang_short = normalize_language_code(raw_lang, format='short')
    except Exception:
        lang_short = 'en'

    candidates: list[str] = []
    for candidate in (lang_full, lang_short, 'en', 'zh-CN'):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for locale_code in candidates:
        cursor = _load_locale_messages(locale_code)
        for part in dotted_key.split('.'):
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(part)
        if isinstance(cursor, str) and cursor.strip():
            return cursor
    return fallback


# Sentinel returned by _start_session_start_llm when CAS detects a concurrent start
# already promoted its own session.  Returning a sentinel (instead of raising)
# keeps the loser out of the generic error path — that path calls cleanup()
# without an expected_session guard and would otherwise tear down the winner's
# session/websocket while also inflating session_start_failure_count.
_START_LLM_CONCURRENT_ABORTED = object()

# Retain strong references: event loops hold tasks weakly, so detached reapers waiting for old listeners
# to exit before closing old sessions after cancellation-timeout fail-close could be garbage-collected
# mid-cleanup. Use add plus done_callback(discard).
_ORPHAN_SESSION_REAPER_TASKS: set = set()


@dataclass(frozen=True)
class ContextAppendResult:
    appended: bool
    deduped: bool = False
    targets: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class FreshScreenshot:
    """One Phase-2 screenshot fetch, tagged with where the image came from.

    ``source`` is the caller's only way to tell apart two situations that a plain
    base64 return conflated:

    - ``'websocket'``: the frontend answered. ``avatar_position`` is its verdict
      about THIS image. ``None`` means the frontend deliberately decided the image
      must not be annotated (window capture, camera, avatar collapsed, multi-monitor);
      the caller must not substitute a position from anywhere else.
    - ``'backend_fallback'``: the frontend never answered and the backend grabbed
      the screen itself. The frontend had no opinion about this image, so the
      caller MAY fall back to the position that came with the original request.
    - ``''``: nothing was captured.

    ``declined`` distinguishes a revoked/limited capture from a technical failure;
    callers must not revive a Phase-1 image after a decline.
    """

    b64: str = ""
    source: str = ""
    avatar_position: dict | None = None
    declined: bool = False


@dataclass(eq=False)
class _ReplyTurn:
    """The host turn one Offline reply was started for.

    Core opens one when it hands a reply to the Offline client and binds that
    reply's completion (and, on the text path, its discard) callback to it. A
    callback can run long after its reply started: ``close()`` retires the
    generation, but a reply parked in a slow tool call or a retry backoff only
    unwinds when that await returns, and its completion still runs then
    because nothing else closes a turn cut by a close. The shared per-turn
    fields (``_active_text_request_id``, ``_pending_turn_meta``) may belong to
    a newer turn by that time, so the callbacks act from this snapshot
    instead.

    ``speech_id`` is the host turn token the reply speaks under. It is mutable
    only so that the rotations that start no turn (the hot-swap promotion, a
    truncation recovery) can carry an open reply along (``_carry_reply_turn``).
    ``session`` is the client the reply was handed to, attached right before
    the hand-over. Identity only (``eq=False``): the manager compares its open
    reply by ``is``.

    ``turn_ended`` is set once the reply's turn end has gone out. A final
    discard (RESPONSE_TOO_LONG, or a RESPONSE_LENGTH_TRUNCATED recovery) ends
    the turn itself, and the completion that runs after it must not end it a
    second time. Only a turn end actually sent counts: a recovery that stood
    down before its turn end leaves the completion to close the turn.

    ``taken_over`` is set once an interrupter or a displacing reply took the
    reply's close over (``TurnMixin._close_taken_over_offline_reply``),
    whether or not that close sent a turn end. A discard recovery still
    running for the reply reads it: it no longer owns the shared output, and
    its wrap-up is owed rather than run on the spot.
    """

    speech_id: str | None
    request_id: str | None = None
    meta: dict | None = None
    session: Any = None
    turn_ended: bool = False
    taken_over: bool = False
    reply_tail: Any = None


def _taken_over_reply_turn(kind) -> _ReplyTurn | None:
    """The snapshot a taken-over Offline reply was handed over with
    (``InterruptedReply.owner``), or None for an unbound reply."""
    owner = getattr(kind, "owner", None)
    return owner if isinstance(owner, _ReplyTurn) else None


def _purge_closed_tool_calls(history: list, *, start: int = 0) -> int:
    """Remove every CLOSED tool-call pair from the conversation history: an
    assistant message (role=assistant, carrying tool_calls) plus the tool-result
    messages immediately following it whose tool_call_id matches. Any
    reasoning_content (the thinking model's chain, parked on that assistant
    message for provider replay) is dropped together with it — deleting the
    whole pair, NOT just the field, since a thinking endpoint rejects a
    tool_calls turn whose reasoning_content went missing on replay.

    Only assistant messages at index >= ``start`` are considered, so a Focus
    exit scopes the purge to the episode's history suffix (recorded when Focus
    was entered) and leaves closed tool calls from regular turns BEFORE Focus
    began intact. ``start`` is clamped to [0, len].

    "Closed" = every tool_call id on the assistant message is answered by a
    following contiguous tool message. Unclosed calls (a call with no result —
    an interrupted / in-flight turn) are kept so live state is never corrupted.
    Plain Human / AI / System (BaseMessage) entries are never touched. Returns
    the number of messages deleted.
    """
    if not history:
        return 0
    n = len(history)
    start = max(0, min(int(start or 0), n))
    remove: set[int] = set()
    for i in range(start, n):
        msg = history[i]
        if not (isinstance(msg, dict) and msg.get("role") == "assistant"):
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        call_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
        if not call_ids:
            continue
        # execute atomically appends assistant followed by all tool results, making closed result groups
        # contiguous; stop collecting an assistant's results at the first nontool message.
        result_idx: list[int] = []
        covered: set = set()
        for j in range(i + 1, n):
            rj = history[j]
            if isinstance(rj, dict) and rj.get("role") == "tool":
                result_idx.append(j)
                covered.add(rj.get("tool_call_id"))
            else:
                break
        if call_ids <= covered:  # Every call has a result -> closed; delete the whole pair.
            remove.add(i)
            remove.update(result_idx)
    for idx in sorted(remove, reverse=True):
        del history[idx]
    return len(remove)

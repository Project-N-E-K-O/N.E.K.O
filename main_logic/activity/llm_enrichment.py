"""Emotion-tier LLM enrichment for ActivitySnapshot.

Two functions, both calling the small ``emotion`` model tier with strict
JSON output formats:

  * ``call_activity_guess`` — given the structured snapshot signals plus
    a few recent conversation turns, returns soft scores across the
    behavioural states (0.0-1.0 each, *independent* not normalised) plus
    a one-sentence narrative description. Lets the proactive prompt see
    "user is mostly focused-work but with some chat happening on the
    side" instead of a single hard label.

  * ``call_open_threads`` — given recent conversation turns, returns up
    to a few short phrases describing topics that were raised but not
    closed (AI promises, abandoned user threads, etc.). Covers cases
    the question-mark heuristic in the rule-based ``unfinished_thread``
    misses.

Both calls are advisory — the rule-based state machine remains
authoritative for propensity / source filtering. The emotion-tier LLM
just enriches the prompt context. Failures (LLM down, parse error,
timeout) silently return None / [] and the snapshot's pre-existing
cache stays in place.

Why a separate module: keeps prompt strings + JSON parsing isolated
from the tracker's orchestration logic. Easier to swap implementations
or add new enrichment passes later without touching tracker.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from config.prompts_activity import ACTIVITY_GUESS_PROMPTS, OPEN_THREADS_PROMPTS
from utils.file_utils import robust_json_loads

logger = logging.getLogger(__name__)


# Input cap: the emotion tier is small and cheap, but we still don't
# want pathological prompt sizes from a long-running session. 8 turns
# of each side covers the realistic "what's hanging" window without
# ballooning latency.
_MAX_CONV_TURNS_PER_SIDE = 8
_MAX_CONV_CHARS_PER_TURN = 200

# Soft-score keys the LLM is asked to fill. Skipping ``transitioning``,
# ``away``, ``stale_returning`` because those are purely temporal /
# rule-derived — there's nothing for the LLM to add. Everything below
# is something an outside observer could reasonably score from window /
# conversation context.
_SCORED_STATES: tuple[str, ...] = (
    'gaming',
    'focused_work',
    'casual_browsing',
    'chatting',
    'voice_engaged',
    'idle',
)


# Prompt templates moved to config/prompts_activity.py per the project's
# i18n convention (multi-language str→str dicts must live in config/prompts_*).


# ── Helpers ─────────────────────────────────────────────────────────

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


def _format_conversation(
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
) -> str:
    """Interleave user / AI messages by timestamp, render as plain lines.

    Each side is capped to ``_MAX_CONV_TURNS_PER_SIDE`` (most recent),
    each text truncated to ``_MAX_CONV_CHARS_PER_TURN``. Empty input
    returns a placeholder so the prompt still parses.
    """
    items: list[tuple[float, str, str]] = []
    for ts, text in user_msgs[-_MAX_CONV_TURNS_PER_SIDE:]:
        items.append((ts, 'user', text))
    for ts, text in ai_msgs[-_MAX_CONV_TURNS_PER_SIDE:]:
        items.append((ts, 'ai', text))
    items.sort(key=lambda x: x[0])
    if not items:
        return '(no conversation yet)'

    now = time.time()
    out_lines: list[str] = []
    for ts, who, text in items:
        age = max(0.0, now - ts)
        if age < 90:
            age_str = f'{int(age)}s ago'
        elif age < 3600:
            age_str = f'{int(age / 60)}min ago'
        else:
            age_str = f'{int(age / 3600)}h ago'
        clip = text.strip()
        if len(clip) > _MAX_CONV_CHARS_PER_TURN:
            clip = clip[:_MAX_CONV_CHARS_PER_TURN] + '…'
        out_lines.append(f'[{age_str}] {who}: {clip}')
    return '\n'.join(out_lines)


def _format_signals(snapshot_view: dict[str, Any]) -> str:
    """Render a structured-signals dict as compact ``key: value`` lines."""
    return '\n'.join(f'{k}: {v}' for k, v in snapshot_view.items() if v is not None)


def _strip_json_fences(text: str) -> str:
    """Strip ``\\`\\`\\`json`` / ``\\`\\`\\``` fences if the model emitted them
    despite being asked not to."""
    s = text.strip()
    if s.startswith('```'):
        m = re.match(r'^```[a-zA-Z]*\s*(.+?)\s*```\s*$', s, flags=re.S)
        if m:
            return m.group(1).strip()
    return s


# ── Public API ──────────────────────────────────────────────────────

async def call_activity_guess(
    *,
    snapshot_signals: dict[str, Any],
    rule_state: str,
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
    lang: str,
    timeout: float = 8.0,
) -> dict | None:
    """Run the emotion-tier model to score states + generate a narrative.

    Returns ``{'scores': dict[str, float], 'guess': str}`` on success, or
    ``None`` on any failure (LLM down, parse error, timeout). The caller
    keeps any prior cached value when ``None`` comes back.

    Parameters
    ----------
    snapshot_signals:
        Dict of structured-signal lines to render in the prompt
        (window title, dwell, CPU, GPU, idle, etc). Caller chooses what
        to include — this function just renders ``key: value``.
    rule_state:
        The rule machine's current pick (e.g. ``"focused_work"``) so the
        LLM can choose to confirm or override.
    """
    lang_key = _normalize_lang(lang)
    template = ACTIVITY_GUESS_PROMPTS.get(lang_key, ACTIVITY_GUESS_PROMPTS['en'])

    prompt = template.format(
        signals=_format_signals(snapshot_signals),
        conversation=_format_conversation(user_msgs, ai_msgs),
        rule_state=rule_state,
        state_keys=', '.join(_SCORED_STATES),
    )

    raw = await _invoke_emotion_tier(prompt, timeout=timeout, label='activity_guess')
    if raw is None:
        return None

    parsed = _safe_parse_json(raw)
    if not isinstance(parsed, dict):
        logger.debug('activity_guess: LLM did not return a JSON object: %r', raw[:200])
        return None

    raw_scores = parsed.get('scores')
    guess = parsed.get('guess', '') or ''
    if not isinstance(raw_scores, dict) or not isinstance(guess, str):
        logger.debug('activity_guess: malformed JSON shape: %r', parsed)
        return None

    # Sanitise: keep only allowed state keys and clamp to [0, 1].
    scores: dict[str, float] = {}
    for key, value in raw_scores.items():
        if key not in _SCORED_STATES:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        scores[key] = max(0.0, min(1.0, f))

    return {'scores': scores, 'guess': guess.strip()}


async def call_open_threads(
    *,
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
    lang: str,
    timeout: float = 6.0,
) -> list[str] | None:
    """Run the emotion-tier model to detect semantically open threads.

    Returns a list of short phrases on success (possibly empty), or
    ``None`` on failure. Caller distinguishes "LLM said nothing's
    hanging" (``[]``) from "LLM call failed" (``None``).
    """
    lang_key = _normalize_lang(lang)
    template = OPEN_THREADS_PROMPTS.get(lang_key, OPEN_THREADS_PROMPTS['en'])

    if not user_msgs and not ai_msgs:
        return []

    prompt = template.format(conversation=_format_conversation(user_msgs, ai_msgs))

    raw = await _invoke_emotion_tier(prompt, timeout=timeout, label='open_threads')
    if raw is None:
        return None

    parsed = _safe_parse_json(raw)
    if not isinstance(parsed, dict):
        logger.debug('open_threads: LLM did not return a JSON object: %r', raw[:200])
        return None

    threads = parsed.get('open_threads')
    if not isinstance(threads, list):
        return None
    cleaned: list[str] = []
    for entry in threads[:5]:
        if isinstance(entry, str) and entry.strip():
            cleaned.append(entry.strip())
    return cleaned


# ── Internal LLM driver ─────────────────────────────────────────────

async def _invoke_emotion_tier(prompt: str, *, timeout: float, label: str) -> str | None:
    """Single-shot emotion-tier call. Returns raw response text or None.

    Imports are deferred so importing this module doesn't pull in the
    full LLM stack — useful for tests that exercise prompt formatting
    without a live model.
    """
    from langchain_core.messages import HumanMessage
    from utils.config_manager import get_config_manager
    from utils.llm_client import create_chat_llm
    from utils.token_tracker import set_call_type

    try:
        cfg_mgr = get_config_manager()
        cfg = cfg_mgr.get_model_api_config('emotion')
    except Exception as e:
        logger.debug('emotion config fetch failed: %s', e)
        return None
    model = cfg.get('model')
    api_key = cfg.get('api_key')
    base_url = cfg.get('base_url')
    if not model or not api_key:
        logger.debug('emotion tier model/api_key missing — enrichment disabled')
        return None

    set_call_type('activity_enrichment')
    try:
        llm = create_chat_llm(
            model, base_url, api_key,
            temperature=0.4,
            max_completion_tokens=512,
        )
    except Exception as e:
        logger.debug('emotion-tier llm init failed: %s', e)
        return None

    try:
        async with llm:
            resp = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)]),
                timeout=timeout,
            )
        return getattr(resp, 'content', '') or ''
    except asyncio.TimeoutError:
        logger.debug('emotion-tier %s call timed out (%ss)', label, timeout)
        return None
    except Exception as e:
        logger.debug('emotion-tier %s call failed: %s', label, e)
        return None


def _safe_parse_json(raw: str) -> Any:
    """Parse JSON, tolerating markdown fences and minor LLM noise."""
    if not raw:
        return None
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Fallback to project-local robust parser (handles trailing
        # commas, single quotes, etc — common LLM output blemishes).
        try:
            return robust_json_loads(cleaned)
        except Exception:
            return None

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

"""Faster avatar emotion for the outward-emotion endpoint (owner decisions D20-D22).

Two ways to get the emotion before the reply's emoji is known:

* **Decision question** (vendors with a native decision model, D21): one
  five-way question on the reply, read with neutral as the baseline. When the
  endpoint then gives the reply a reaction, a short chat call picks its emoji.
* **First-token reading** (Doubao, D22): the original chat prompt, with the
  answer prefilled up to the emotion value and streamed with token
  probabilities. The emotion is read from the first token; the rest of the
  stream supplies the model's emoji.

Either way the endpoint answers as soon as the emotion is known, so the avatar
changes at once; the emoji follows through :func:`register_pending_reaction` and
``POST /api/emotion/reaction``.
"""

from __future__ import annotations

import asyncio
import math
import secrets
import time
import urllib.parse
from typing import Any, Awaitable, Callable, Mapping, Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

LABELS = ("happy", "sad", "angry", "surprised", "neutral")
# The most likely non-neutral emotion e wins when p_e / (p_e + p_neutral) reaches this.
NEUTRAL_BASELINE_TAU = 0.7
DECISION_TIMEOUT_SECONDS = 2.5
FIRST_TOKEN_TIMEOUT_SECONDS = 2.5
EMOJI_CHAT_MAX_TOKENS = 16
EMOJI_CHAT_TIMEOUT_SECONDS = 8.0
FIRST_TOKEN_TOP_LOGPROBS = 10
# The opening of the JSON answer up to the emotion value.
EMOTION_PREFILL = '{"emotion": "'
# Chat endpoints that continue an assistant prefill and stream token
# probabilities, tested for this reading (Doubao, 2026-11-27). DeepSeek and Kimi
# split sad and surprised into the same first token, so they keep the chat.
_FIRST_TOKEN_CHAT_ENDPOINTS: dict[str, frozenset[str]] = {
    "ark.cn-beijing.volces.com": frozenset({"/api/v3"}),
}
# How long a computed reaction waits for the frontend to collect it.
PENDING_REACTION_TTL_SECONDS = 60.0
PENDING_REACTION_WAIT_SECONDS = 8.0
_MAX_PENDING_REACTIONS = 64


def first_token_reading_usable(chat_config: Mapping[str, Any] | None) -> bool:
    if not isinstance(chat_config, Mapping) or not chat_config.get("model"):
        return False
    if str(chat_config.get("provider_type") or "").lower() in ("anthropic", "chatgpt", "websocket"):
        return False
    parts = urllib.parse.urlsplit(str(chat_config.get("base_url") or "").strip())
    host = (parts.hostname or "").lower()
    path = (parts.path or "").rstrip("/").lower()
    return path in _FIRST_TOKEN_CHAT_ENDPOINTS.get(host, ())


def read_against_neutral(probs: Mapping[str, float]) -> tuple[str, float]:
    """The emotion and its confidence, with neutral as the baseline.

    The confidence is p_e / (p_e + p_neutral) for the emotion that wins, and
    1 minus that ratio when neutral wins.
    """
    clean = {}
    for k, v in probs.items():
        if k in LABELS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                clean[k] = f
    p_neutral = clean.get("neutral", 0.0)
    others = {k: v for k, v in clean.items() if k != "neutral"}
    if not others:
        return "neutral", p_neutral
    best = max(others, key=others.get)
    total = others[best] + p_neutral
    ratio = others[best] / total if total > 0 else 0.0
    return (best, ratio) if ratio >= NEUTRAL_BASELINE_TAU else ("neutral", 1.0 - ratio)


def _label_of_token(token: str) -> Optional[str]:
    """The emotion a first token starts, if it starts exactly one (``sur`` -> surprised)."""
    t = (token or "").strip().strip('"').strip().lower()
    if not t:
        return None
    for label in LABELS:
        if t.startswith(label):
            return label
    starts = [label for label in LABELS if label.startswith(t)]
    return starts[0] if len(starts) == 1 else None


def label_probabilities(candidates) -> dict[str, float]:
    """Emotion probabilities from the first token's candidates, merged by label."""
    probs: dict[str, float] = {}
    for token, logprob in candidates or []:
        label = _label_of_token(token)
        if label is None:
            continue
        try:
            p = math.exp(float(logprob))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(p):
            probs[label] = probs.get(label, 0.0) + p
    return probs


# ── Decision question ────────────────────────────────────────────────


async def decide_emotion(text: str, *, chat_config, config_manager) -> Optional[tuple[str, float]]:
    """``(emotion, confidence)`` from the decision question, or ``None`` to use the chat prompt."""
    from config.prompts.prompts_emotion import (
        OUTWARD_EMOTION_DECISION_CRITERIA,
        OUTWARD_EMOTION_DECISION_INSTRUCTIONS,
    )
    from utils.decision_client import ChoiceQuestion, ask_site_questions
    from utils.decision_client.routing import SITE_EMOTION_DISPLAY

    answers = await ask_site_questions(
        SITE_EMOTION_DISPLAY,
        state=text,
        questions={
            "emotion": ChoiceQuestion(
                instructions=OUTWARD_EMOTION_DECISION_INSTRUCTIONS,
                criteria=dict(OUTWARD_EMOTION_DECISION_CRITERIA),
            ),
        },
        chat_config=chat_config,
        config_manager=config_manager,
        rehost=False,  # chat vendors keep the original prompt (D27)
        timeout=DECISION_TIMEOUT_SECONDS,
        call_type="emotion",
    )
    if not answers:
        return None
    answer = answers.get("emotion")
    probs = dict(getattr(answer, "probabilities", None) or {})
    if not probs:
        value = getattr(answer, "value", None)
        if value not in LABELS:
            return None
        probs = {value: 1.0}
    return read_against_neutral(probs)


async def pick_emoji(text: str, *, chat_config) -> Optional[str]:
    """One short chat call for the reaction emoji; its raw answer, or ``None`` on a miss."""
    from config import MESSAGE_REACTION_EMOJIS_BY_EMOTION
    from config.prompts.prompts_emotion import OUTWARD_EMOTION_EMOJI_CHAT_PROMPT
    from utils.llm_client.warm_pool import get_warm_chat_llm
    from utils.token_tracker import llm_call_context
    from utils.tokenize import truncate_to_tokens

    from config import DECISION_STATE_MAX_TOKENS

    pool = " ".join(dict.fromkeys(g for cands in MESSAGE_REACTION_EMOJIS_BY_EMOTION.values() for g in cands))
    messages = [
        {"role": "system", "content": OUTWARD_EMOTION_EMOJI_CHAT_PROMPT.replace("{pool}", pool)},
        # The same bound the decision question puts on the reply.
        {"role": "user", "content": truncate_to_tokens(text, DECISION_STATE_MAX_TOKENS)},
    ]
    try:
        llm = await get_warm_chat_llm(
            str(chat_config.get("model") or ""), str(chat_config.get("base_url") or ""),
            str(chat_config.get("api_key") or ""), provider_type=chat_config.get("provider_type"),
            max_retries=0,
        )
        with llm_call_context("emotion"):
            resp = await asyncio.wait_for(
                llm.ainvoke(messages, max_completion_tokens=EMOJI_CHAT_MAX_TOKENS,
                            timeout=EMOJI_CHAT_TIMEOUT_SECONDS),
                timeout=EMOJI_CHAT_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        logger.info("[Emotion] emoji chat failed (%s); picking by emotion", type(exc).__name__)
        return None
    return (getattr(resp, "content", "") or "").strip()[:16] or None


# ── First-token reading (Doubao) ──────────────────────────────────────


class FirstTokenReading:
    """A streamed chat answer whose first token gave the emotion; the rest is read later."""

    def __init__(self, probs: dict[str, float], first_text: str, rest: "asyncio.Task[str]") -> None:
        self.probs = probs
        self._first_text = first_text
        self._rest = rest

    async def full_text(self) -> str:
        """The whole answer, prefill included, once the stream ends."""
        return EMOTION_PREFILL + self._first_text + await self._rest

    def cancel(self) -> None:
        self._rest.cancel()


async def start_first_token_reading(messages: list[dict], *, chat_config) -> Optional[FirstTokenReading]:
    """Stream the chat prompt with the emotion prefill; ``None`` when no emotion comes first."""
    from config import EMOTION_ANALYSIS_MAX_TOKENS
    from utils.llm_client.warm_pool import get_warm_chat_llm
    from utils.token_tracker import llm_call_context

    llm = await get_warm_chat_llm(
        str(chat_config.get("model") or ""), str(chat_config.get("base_url") or ""),
        str(chat_config.get("api_key") or ""), provider_type=chat_config.get("provider_type"),
        max_retries=0,
    )
    prefilled = list(messages) + [{"role": "assistant", "content": EMOTION_PREFILL}]
    first: asyncio.Future = asyncio.get_running_loop().create_future()

    async def _run() -> str:
        parts: list[str] = []
        try:
            with llm_call_context("emotion"):
                stream = llm.astream(  # noqa: LLM_INPUT_BUDGET  # the same messages as the endpoint's chat prompt call.
                    prefilled,
                    max_completion_tokens=EMOTION_ANALYSIS_MAX_TOKENS,
                    logprobs=True,
                    top_logprobs=FIRST_TOKEN_TOP_LOGPROBS,
                    timeout=30,
                )
                async for chunk in stream:
                    if not first.done() and (chunk.content or chunk.top_logprobs):
                        cands = (chunk.top_logprobs or [[]])[0]
                        first.set_result((label_probabilities(cands), chunk.content or ""))
                        continue
                    if chunk.content:
                        parts.append(chunk.content)
        except BaseException as exc:
            if not first.done():
                first.set_exception(exc if isinstance(exc, Exception) else asyncio.CancelledError())
            raise
        if not first.done():
            first.set_result(({}, ""))
        return "".join(parts)

    rest = asyncio.ensure_future(_run())
    try:
        probs, first_text = await asyncio.wait_for(asyncio.shield(first), FIRST_TOKEN_TIMEOUT_SECONDS)
    except BaseException:
        rest.cancel()
        raise
    return FirstTokenReading(probs, first_text, rest)


# ── Pending reactions ──────────────────────────────────────────────────

_pending: dict[str, tuple["asyncio.Task[Any]", float]] = {}


def register_pending_reaction(make: Callable[[], Awaitable[Optional[dict]]]) -> str:
    """Start computing a reaction in the background; the token the frontend collects it by."""
    now = time.monotonic()
    for token, (task, made_at) in list(_pending.items()):
        if now - made_at > PENDING_REACTION_TTL_SECONDS:
            task.cancel()
            _pending.pop(token, None)
    while len(_pending) >= _MAX_PENDING_REACTIONS:
        oldest = min(_pending, key=lambda t: _pending[t][1])
        _pending.pop(oldest)[0].cancel()
    token = secrets.token_urlsafe(12)
    _pending[token] = (asyncio.ensure_future(make()), now)
    return token


async def collect_pending_reaction(token: str) -> Optional[dict]:
    """The reaction for *token* (``None`` when unknown, failed or not ready in time); one collection per token."""
    entry = _pending.pop(str(token or ""), None)
    if entry is None:
        return None
    task, _made_at = entry
    try:
        return await asyncio.wait_for(task, PENDING_REACTION_WAIT_SECONDS)
    except Exception as exc:
        logger.info("[Emotion] pending reaction dropped (%s)", type(exc).__name__)
        return None


def clear_pending_reactions() -> None:
    for task, _made_at in _pending.values():
        task.cancel()
    _pending.clear()

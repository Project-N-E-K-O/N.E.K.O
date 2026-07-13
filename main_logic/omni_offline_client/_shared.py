# -- coding: utf-8 --
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

import asyncio  # noqa: F401 - compatibility export and sibling dependency

import json  # noqa: F401 - compatibility export and sibling dependency

import re  # noqa: F401 - compatibility export and sibling dependency

import time  # noqa: F401 - compatibility export and sibling dependency

from typing import Optional, Callable, Dict, Any, Awaitable, List  # noqa: F401

from utils.llm_client import (  # noqa: F401
    SystemMessage,
    HumanMessage,
    AIMessage,
    LLMStreamChunk,
    ThinkingStreamStripper,
    chat_retry_error_types,
    strip_thinking_segments,
    create_chat_llm,
    create_chat_llm_async,
)

from utils.frontend_utils import calculate_text_similarity  # noqa: F401

from utils.tokenize import count_tokens, truncate_to_tokens  # noqa: F401

from config import (  # noqa: F401
    OMNI_RECENT_RESPONSES_MAX,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    FOCUS_THINKING_EXTRA_TOKENS,
)

from main_logic.tool_calling import (  # noqa: F401
    OnToolCallCallback,
    ToolCall,
    ToolDefinition,
    ToolResult,
    parse_arguments_json,
)

from utils.llm_tool_leak_filter import ToolLeakFilter, log_tool_leak_filtered  # noqa: F401

_LLM_RETRY_ERROR_TYPES: tuple[type[BaseException], ...] | None = None

def _llm_retry_error_types() -> tuple[type[BaseException], ...]:
    global _LLM_RETRY_ERROR_TYPES
    if _LLM_RETRY_ERROR_TYPES is None:
        from openai import AuthenticationError

        _LLM_RETRY_ERROR_TYPES = (
            AuthenticationError,
            *chat_retry_error_types(),
        )
    return _LLM_RETRY_ERROR_TYPES

_GENAI_NATIVE_BASE_URL_HINTS = (
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
)

_GENAI_NATIVE_MODEL_HINTS = ("gemini",)

_SENTENCE_END_CHARS = '.!?。！？…'

_SUMMARY_TERMINATOR_CHARS = _SENTENCE_END_CHARS + ',，;；:：'

_SUMMARY_LATE_FINISH_SLACK = 25

_SUMMARY_GIBBERISH_RECHECK_TOKENS = 100

_SUMMARY_HARD_TOKEN_CAP = 50

_SUMMARY_API_BUDGET_FLOOR = 3000

_API_KEY_REJECTED_KEYWORDS = (
    "incorrect api key",
    "incorect api key",
    "invalid_api_key",
    "invalid api key",
    "invalid key",
    "api key is invalid",
)

_SAFETY_VIOLATION_KEYWORDS = (
    "safety",
    "content_filter",
    "content filter",
    "policy violation",
    "policy_violation",
    "blocklist",
    "prohibited",
    "prohibited_content",
    "recitation",
    "spii",
    "language",
    "image_safety",
    "image_prohibited_content",
    "image_recitation",
    "responsibleaipolicyviolation",
    "responsible ai policy",
)

def _is_api_key_rejected_error(error: BaseException | str) -> bool:
    """Return True when an upstream error clearly means the API key was rejected."""
    status_code = getattr(error, "status_code", None)
    text = f"{type(error).__name__}: {error}".lower()
    has_api_key_indicator = any(keyword in text for keyword in _API_KEY_REJECTED_KEYWORDS)
    if status_code == 401:
        return True
    if status_code == 403:
        return has_api_key_indicator
    # Fallback: when the error object carries no status_code attribute, fall
    # back to substring matching against the error message text.
    if "401" in text:
        return True
    if "403" in text:
        return has_api_key_indicator
    if has_api_key_indicator:
        return True
    return (
        ("authenticationerror" in text or "authentication" in text or "unauthorized" in text)
        and "api key" in text
    )

def _is_safety_violation_signal(*values: object) -> bool:
    """Return True when provider diagnostics point to safety/policy blocking."""
    text = " ".join(str(value) for value in values if value).lower()
    if not text:
        return False
    return any(keyword in text for keyword in _SAFETY_VIOLATION_KEYWORDS)

def _truncate_to_last_sentence_end(text: str) -> str:
    """Return the prefix of ``text`` up to and including the last
    sentence-terminating punctuation mark. Returns ``""`` if no sentence
    terminator is present (caller should fall through to the
    too-long-and-discarded UX in that case)."""
    last = max((text.rfind(ch) for ch in _SENTENCE_END_CHARS), default=-1)
    if last < 0:
        return ""
    return text[:last + 1]

_GIBBERISH_MIN_LEN = 30        # Below this we don't bother judging.

_GIBBERISH_PS_RATIO_FLOOR = 0.015  # < 1.5% punct/symbol → BPE-loop / wall-of-chars

_GIBBERISH_PS_RATIO_CEIL = 0.25    # > 25% punct/symbol → emoji/mark spam

_MAX_TOKENS_SLACK = 20

_UNLIMITED_BUDGET = 999999  # sentinel set when user picks the slider's "无限制"

_PROACTIVE_SCREENSHOT_TTL_SECONDS = 60.0

def _budget_to_max_tokens(budget: int, summary_mode: bool = False) -> int | None:
    """Convert ``max_response_length`` budget into the LLM API's
    ``max_completion_tokens``. ``None`` for the unlimited sentinel so the
    request omits the field entirely (large fixed values get rejected as
    out-of-range by some providers).

    When ``summary_mode`` is True the API-side ceiling is lifted to at
    least ``_SUMMARY_API_BUDGET_FLOOR`` (or the caller's budget+slack if
    that is larger — never CAPS to the floor, just raises the small
    defaults). The Python-side guard then decides per-response whether
    to abandon, summarize, or pass through the overshoot.
    """
    if budget >= _UNLIMITED_BUDGET:
        return None
    if summary_mode:
        return max(budget + _MAX_TOKENS_SLACK, _SUMMARY_API_BUDGET_FLOOR)
    return budget + _MAX_TOKENS_SLACK

def _find_summary_terminator(text: str) -> int:
    """Return the offset of the FIRST pause-causing punctuation char in
    ``text`` (one of ``_SUMMARY_TERMINATOR_CHARS``), or ``-1`` if none.

    Used in summary-mode cutover: once the response crosses the budget
    we want TTS to stop at the next natural breath rather than mid-word.
    Caller treats ``offset + 1`` as the inclusive boundary.
    """
    best = -1
    for ch in _SUMMARY_TERMINATOR_CHARS:
        pos = text.find(ch)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    return best

def _is_gibberish_response(text: str) -> bool:
    """Heuristic: is ``text`` a runaway / gibberish model output?

    Based on the density of Unicode punctuation (Pc/Pd/Pe/Pf/Pi/Po/Ps) plus
    symbols (Sc/Sk/Sm/So — i.e. emoji, math marks, kaomoji components):

    - density < 1.5% → almost certainly a tight repetition loop (a single
      character or short n-gram repeated past the token cap), no real
      sentences to recover.
    - density > 25% → almost certainly an emoji / kaomoji / mark spam mode.

    Either way the right thing to do is filter the response entirely (let
    `handle_response_discarded` show the locale "fault" placeholder and write
    that placeholder — not the gibberish — into history) rather than try to
    cut a sentence out of garbage. Short responses (< 30 chars) skip the
    judgement; the guard only fires after we've blown past the token cap, so
    in practice ``text`` is always long here.
    """
    import unicodedata
    n = len(text)
    if n < _GIBBERISH_MIN_LEN:
        return False
    n_marks = sum(
        1 for c in text
        if unicodedata.category(c)[0] in ("P", "S")
    )
    ratio = n_marks / n
    return ratio < _GIBBERISH_PS_RATIO_FLOOR or ratio > _GIBBERISH_PS_RATIO_CEIL

from utils.logger_config import get_module_logger

from utils.token_tracker import set_call_type

logger = get_module_logger(__name__, "Main")

_NONVERBAL_DIRECTIVE_PATTERN = re.compile(r"\[play_music:[^\]]*(?:\]|$)", re.IGNORECASE)

def _strip_nonverbal_directives(text: str) -> str:
    if not text:
        return ""
    return _NONVERBAL_DIRECTIVE_PATTERN.sub("", text)

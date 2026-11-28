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

"""Model adapter: the loop's ``ModelProvider`` on top of ``utils.llm_client``.

The loop speaks provider-neutral messages and events; ``utils.llm_client``
speaks OpenAI-format message dicts and yields ``LLMStreamChunk``. This module
translates both ways so every provider quirk stays in the one model layer the
rest of the application uses, including ``http://`` endpoints of local models.

Two provider-owned values have to survive a round trip unchanged or the next
request is rejected:

* ``reasoning_content`` of a turn that calls tools (thinking models). It is
  kept as ``ThinkingContent`` and sent back on that assistant message.
* ``extra_content`` on a tool call (Gemini's thought signature). It is kept as
  JSON text in ``ToolCall.thought_signature`` and parsed back when sending.

Anthropic thinking blocks never reach this module: ``ChatAnthropic`` remembers
and replays them itself.

Initial HTTP transport retries (429 / 5xx / connection errors) live in the
underlying model clients (``max_retries``); this layer adds 5-state mid-stream
drop recovery, stream idle watchdog timeouts, HTTP 529 overloaded backoff, and
one-shot non-streaming fallback when a streaming endpoint returns HTTP 404.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from nekopower.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    BashExecutionMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_text,
)
from nekopower.runtime.loop.provider import CancellationToken
from nekopower.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from nekopower.runtime.loop.tools import AgentTool
from nekopower.runtime.protocol import ModelConfig
from nekopower.runtime.tools.base import INVALID_ARGUMENTS_KEY

logger = logging.getLogger(__name__)

# Backstop on a single text part of a request. Context management keeps the
# conversation as a whole within the window; this bounds one part that it
# cannot shrink, such as an enormous message from the user.
WIRE_TEXT_PART_MAX_TOKENS = 64_000
_CANCEL_POLL_SECONDS = 0.1
_IMAGE_NOT_ACCEPTED = "[image omitted: the model does not accept images]"
_IMAGE_WITHHELD = "[image omitted: the provider refused this picture]"
# Marks thinking that arrived in the provider's ``reasoning_content`` field, the
# only kind that is sent back. Reasoning a model wrote inside ``<think>`` tags
# of its content is kept for the record but never replayed.
REASONING_FIELD_SIGNATURE = "reasoning_content"

_OVERFLOW_PATTERNS = re.compile(
    "|".join(
        [
            r"context[_ ]length[_ ]exceeded",
            r"maximum context length",
            r"context window",
            r"prompt is too long",
            r"too many tokens",
            r"exceeds? the (?:model'?s? )?(?:maximum|max)(?: number of)? (?:input )?tokens",
            r"input (?:is )?too long",
            r"range of input length",
            r"request too large",
            r"reduce the length of the (?:messages|prompt|input)",
            r"input token count .* exceeds",
            # llama.cpp's server ("exceed_context_size_error") and LM Studio.
            r"exceed_context_size",
            r"available context size",
            r"context length of only",
            # Anthropic combined input + max_tokens rejection ("exceed context limit").
            r"exceeds? context limit",
        ]
    ),
    re.IGNORECASE,
)

# Where a provider states its window when it refuses a request as too long.
_STATED_WINDOW_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # OpenAI, DeepSeek, vLLM, OpenRouter: "maximum context length is 32768 tokens".
        r"maximum context length (?:is|of) ([\d,]+)",
        # llama.cpp's server names it in the error object: 'n_ctx': 8192.
        r"['\"]n_ctx['\"]\s*:\s*([\d,]+)",
        # LM Studio: "loaded with context length of only 4096 tokens".
        r"context length of only ([\d,]+)",
        # Anthropic: "prompt is too long: 210000 tokens > 200000 maximum".
        r">\s*([\d,]+) maximum",
        # DashScope: "Range of input length should be [1, 30720]".
        r"range of input length should be \[\s*\d+\s*,\s*([\d,]+)\s*\]",
        # Gemini: "exceeds the maximum number of tokens allowed (1048576)".
        r"maximum number of tokens allowed \(([\d,]+)\)",
    )
)
_STATED_WINDOW_RANGE = (1_024, 10_000_000)


class ContextOverflowError(Exception):
    """The provider refused the request because the input exceeds its window."""


class RequestRefusedError(Exception):
    """The provider refused the request outright, in words that say nothing recognized about its length.

    With a status a request that is too long is refused with
    (``OVERFLOW_STATUSES``), and before any output. Whether it was too long
    is for the session to judge, which knows how long it was.
    """


class ModelRequestError(Exception):
    """A one-shot model request (see ``complete``) failed."""


# The statuses a provider refuses a request that is too long with.
OVERFLOW_STATUSES = (400, 413, 422)


def is_context_overflow(error: BaseException) -> bool:
    """Recognize a provider's "input too long" rejection from its message."""
    status = getattr(error, "status_code", None)
    if status is not None and status not in OVERFLOW_STATUSES:
        return False
    return says_context_overflow(str(error))


def says_context_overflow(said: str) -> bool:
    """Whether what a provider answered a refused request with says that its input was too long."""
    return bool(_OVERFLOW_PATTERNS.search(said)) or parse_combined_overflow(said) is not None


# What a failed model request says when the provider refused its API key: the
# SDK's error class, the HTTP status as the SDKs write it into the message,
# or the provider's own words.
_KEY_REFUSED_PATTERNS = re.compile(
    r"authenticationerror|permissiondeniederror|invalid[_ ]api[_ ]key|incorrect api key|"
    r"invalid subscription key|api key (?:is )?(?:invalid|not valid|expired)|\b401 unauthorized\b|"
    r"(?:error code|status code)[:= ]*(?:401|403)\b|\bhttp (?:401|403)\b",
    re.IGNORECASE,
)
# A 403 (as opposed to a 401) may be a refusal of where the request came from
# rather than of the key: told apart by the providers' own wording for that.
_FORBIDDEN_PATTERNS = re.compile(r"\b403\b|permissiondeniederror", re.IGNORECASE)
# What it says when the account behind the key has no quota or balance left:
# words that only an account with nothing left is answered with.
_ACCOUNT_EMPTY_PATTERNS = re.compile(
    r"insufficient[_ ](?:quota|balance|credits?|funds)|exceeded_current_quota|"
    r"out of credits?|credit balance is too low|"
    r"\barrearage\b|\boverdue\b|余额不足|欠费",
    re.IGNORECASE,
)
# Wordings that may be either: a quota used up, or one of the short-term
# limits below. Taken for an account with nothing left only without them.
# ("You exceeded your current quota" among them: OpenAI says it with
# ``insufficient_quota`` when the account is empty, Gemini with a wait when
# a per-minute limit was reached.)
_QUOTA_WORDS_PATTERNS = re.compile(
    r"quota (?:has been )?exceeded|exceeded (?:your |the )?(?:current )?quota|out of quota|额度(?:已)?(?:用尽|用完|不足)",
    re.IGNORECASE,
)
# A limit that waiting lifts: per second, minute or hour, a rate limit, a
# wait the provider names. Not a reason to pay or to change the API.
_SHORT_TERM_LIMIT_PATTERNS = re.compile(
    r"per (?:second|minute|hour)|for this (?:second|minute|hour)|\b(?:rpm|tpm|rps)\b|"
    r"rate[_ ]limit|try again|retry (?:after|later|in)|please retry|稍后(?:再试|重试)|每秒|每分钟|每小时|限流|频率",
    re.IGNORECASE,
)

# A 403 for where the request came from is not about the key. Only the
# wordings for that: "regional endpoint" and the like say nothing of it.
_REGION_REFUSAL_PATTERNS = re.compile(
    r"unsupported_country_region_territory|"
    r"\b(?:unsupported|not supported|not available)\b.{0,40}\b(?:country|countries|region|regions|territory|territories|location)\b|"
    r"\b(?:country|region|territory|location)\b.{0,40}\b(?:not supported|unsupported|is not available)\b",
    re.IGNORECASE,
)

# The kinds ``model_failure_kind`` returns.
MODEL_KEY_REFUSED = "model_key"
MODEL_QUOTA_USED_UP = "model_quota"


def model_failure_kind(said: str) -> str | None:
    """Return why a model request failed when it is one the user has to fix, from what the failure said.

    ``MODEL_KEY_REFUSED``: the provider refused the API key (wrong, expired,
    or not allowed to use the model). ``MODEL_QUOTA_USED_UP``: the account
    has no quota or balance left. None for anything else, a rate limit among
    them. Read from the text, as the SDKs fold the HTTP status into it and
    the text is all that reaches the host.
    """
    text = str(said or "")
    if _ACCOUNT_EMPTY_PATTERNS.search(text):
        return MODEL_QUOTA_USED_UP
    if _QUOTA_WORDS_PATTERNS.search(text):
        # A quota that is used up, or one that waiting lifts: either way not
        # the key, whatever status it came with.
        return None if _SHORT_TERM_LIMIT_PATTERNS.search(text) else MODEL_QUOTA_USED_UP
    if _KEY_REFUSED_PATTERNS.search(text):
        if _FORBIDDEN_PATTERNS.search(text) and _REGION_REFUSAL_PATTERNS.search(text):
            return None
        return MODEL_KEY_REFUSED
    return None


def window_from_overflow(said: str) -> int | None:
    """Return the context window a provider stated when it refused a request as too long, if it stated one.

    Read from the wordings the common providers use; a refusal in other
    words, or one that names no number, teaches nothing. Only asked of what
    ``is_context_overflow`` recognized. A number that cannot be a window is
    not taken for one.
    """
    low, high = _STATED_WINDOW_RANGE
    for pattern in _STATED_WINDOW_PATTERNS:
        found = pattern.search(said)
        if found is None:
            continue
        try:
            stated = int(found.group(1).replace(",", ""))
        except ValueError:
            continue
        if low <= stated <= high:
            return stated
    return None


# Combined "input + max_tokens > limit" overflow messages where lowering
# max_tokens avoids unnecessary compaction.
_COMBINED_OVERFLOW_PATTERNS = (
    # Anthropic: "input length and max_tokens exceed context limit: 195000 + 8192 > 200000"
    (re.compile(r"(\d[\d,]*)\s*\+\s*(\d[\d,]*)\s*>\s*(\d[\d,]*)", re.IGNORECASE), (1, 3)),
    # OpenAI / vLLM / OpenRouter:
    # "maximum context length is 128000 tokens. However, you requested 130000 tokens
    #  (122000 in the messages, 8000 in the completion)"
    (
        re.compile(
            r"maximum context length (?:is|of) ([\d,]+)[^\n]*?\(\s*([\d,]+)\s+in the messages",
            re.IGNORECASE,
        ),
        (2, 1),
    ),
    # Generic: "input tokens (195000) + max_tokens (8192) exceeds 200000"
    (
        re.compile(
            r"input(?: token)?s?[^0-9\n]{0,20}([\d,]+)[^0-9\n]{0,20}\+[^0-9\n]{0,20}"
            r"(?:max_tokens|max_completion_tokens|output|completion)[^0-9\n]{0,20}([\d,]+)"
            r"[^0-9\n]{0,30}(?:exceeds?|limit|maximum|>)[^0-9\n]{0,20}([\d,]+)",
            re.IGNORECASE,
        ),
        (1, 3),
    ),
)


def parse_combined_overflow(said: str) -> tuple[int, int] | None:
    """Return ``(input_tokens, limit)`` when an overflow comes from ``input + max_tokens > limit``.

    Only returns a pair when ``0 < input_tokens < limit`` so lowering
    ``max_tokens`` can actually let the request fit without compaction.
    """
    low, high = _STATED_WINDOW_RANGE
    for pattern, (input_group, limit_group) in _COMBINED_OVERFLOW_PATTERNS:
        found = pattern.search(said)
        if found is None:
            continue
        try:
            input_tokens = int(found.group(input_group).replace(",", ""))
            limit = int(found.group(limit_group).replace(",", ""))
        except ValueError:
            continue
        if 0 < input_tokens < limit and low <= limit <= high:
            return input_tokens, limit
    return None


class _StreamIdleTimeoutError(TimeoutError):
    """Raised when an open model stream yields no chunk for the idle timeout."""


_TRANSIENT_STREAM_ERROR_NAMES = frozenset(
    {
        "RemoteProtocolError",
        "ReadError",
        "ReadTimeout",
        "ConnectError",
        "ConnectTimeout",
        "WriteError",
        "WriteTimeout",
        "CloseError",
        "ProtocolError",
        "APIConnectionError",
        "APITimeoutError",
        "IncompleteRead",
    }
)
_TRANSIENT_STREAM_DROP_PATTERNS = re.compile(
    r"incomplete chunked read|peer closed connection|unexpected eof|stream stalled|"
    r"stream closed unexpectedly|connection reset|broken pipe|server disconnected",
    re.IGNORECASE,
)
_OVERLOADED_529_PATTERNS = re.compile(r"\b529\b|overloaded_error", re.IGNORECASE)
_RETRY_AFTER_PATTERNS = re.compile(
    r"retry[- ]after[:=\s]+([\d.]+)|(?:try again|retry) in\s+([\d.]+)\s*s(?:ec(?:ond)?s?)?",
    re.IGNORECASE,
)


def _is_transient_stream_drop(exc: BaseException) -> bool:
    """Whether ``exc`` indicates a transport drop or stall mid-stream."""
    if isinstance(exc, (_StreamIdleTimeoutError, ConnectionError, TimeoutError, asyncio.TimeoutError, EOFError)):
        return True
    for cls in type(exc).__mro__:
        if cls.__name__ in _TRANSIENT_STREAM_ERROR_NAMES:
            return True
    return bool(_TRANSIENT_STREAM_DROP_PATTERNS.search(str(exc)))


def _is_overloaded_529(exc: BaseException) -> bool:
    """Whether ``exc`` is an HTTP 529 overloaded refusal."""
    if getattr(exc, "status_code", None) == 529:
        return True
    return bool(_OVERLOADED_529_PATTERNS.search(str(exc)))


def _parse_retry_after(exc: BaseException, *, cap: float) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = None
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is not None:
        try:
            seconds = float(str(raw).strip())
            if seconds >= 0:
                return min(seconds, cap)
        except ValueError:
            pass
    found = _RETRY_AFTER_PATTERNS.search(str(exc))
    if found is not None:
        raw_match = found.group(1) or found.group(2)
        if raw_match:
            try:
                seconds = float(raw_match)
                if seconds >= 0:
                    return min(seconds, cap)
            except ValueError:
                pass
    return None


def _retry_delay(attempt: int, exc: BaseException) -> float:
    from config.nekopower_settings import (
        TASK_AGENT_STREAM_BACKOFF_BASE_SECONDS,
        TASK_AGENT_STREAM_BACKOFF_MAX_SECONDS,
    )

    cap = max(0.0, float(TASK_AGENT_STREAM_BACKOFF_MAX_SECONDS))
    hinted = _parse_retry_after(exc, cap=cap)
    if hinted is not None:
        return hinted
    base = max(0.0, float(TASK_AGENT_STREAM_BACKOFF_BASE_SECONDS))
    delay = min(cap, base * (2 ** attempt))
    return delay * random.uniform(0.75, 1.25)


async def _sleep_cancellable(seconds: float, signal: CancellationToken | None) -> None:
    if seconds <= 0:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        if signal is not None and signal.is_cancelled():
            raise _Aborted
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(_CANCEL_POLL_SECONDS, remaining))


# What a provider says when a request carries a picture its model cannot take.
# Each alternative names the picture and the refusal together, so an error
# that merely mentions an image (a failed download, a size limit) is not
# taken for one.
_IMAGE_REFUSAL_PATTERNS = re.compile(
    "|".join(
        [
            r"image_url[^.\n]{0,80}(?:only supported|not supported|unsupported|unknown variant|unexpected)",
            r"unknown variant [`'\"]?image_url",
            r"(?:does not|doesn't|do not|cannot|can't|not) support[^.\n]{0,40}(?:image|vision|multimodal|multi-modal)",
            r"(?:image|vision|multimodal|multi-modal)[^.\n]{0,40}(?:is|are) not supported",
            r"(?:image|vision)[^.\n]{0,20}input[^.\n]{0,40}(?:not supported|unsupported|not enabled|not available)",
            # A router with no upstream that takes pictures for this model (HTTP 404).
            r"no endpoints? found that supports?[^.\n]{0,20}(?:image|vision|multimodal|multi-modal)",
            r"invalid content type[^.\n]{0,40}image",
            r"expected [`'\"]?text[`'\"]?[^.\n]{0,40}image_url",
            r"不支持[^。\n]{0,12}(?:图片|图像|视觉|多模态)",
            r"(?:图片|图像|视觉|多模态)[^。\n]{0,12}不支持",
        ]
    ),
    re.IGNORECASE,
)


# An error that speaks of pictures at all.
_MENTIONS_PICTURES = re.compile(r"image|vision|multimodal|multi-modal|picture|图片|图像|视觉|多模态", re.IGNORECASE)

# What makes a refusal one of *these* pictures and not of pictures as such:
# their format, their size, that they could not be read. A model that takes
# pictures says such things too, so nothing is concluded about the model from
# them. These words are about pictures wherever in the error they stand.
_ABOUT_THESE_PICTURES = re.compile(
    "|".join(
        [
            r"format|mime|media type|file type|extension",
            r"size|large|big|dimension|resolution|pixel|megabyte|\bMB\b|\bKB\b|bytes|payload|entity",
            r"decod|base64|corrupt|invalid image|malformed|could not (?:be )?process|unable to process|cannot (?:be )?process",
            r"download|fetch|\burl\b|expired|not found at",
            r"格式|尺寸|过大|太大|大小|分辨率|像素|下载|解码|损坏|无法处理",
        ]
    ),
    re.IGNORECASE,
)

# The same of how many pictures there are and where they stand in the
# request. Words like these turn up in what a gateway appends to every error
# as well ("Max Retries: 2", a rate limit), so they only count in a sentence
# that speaks of pictures.
_LIMITS_ON_PICTURES = re.compile(
    "|".join(
        [
            r"too many|multiple|more than|at most|maximum|\bmax\b|limit|exceed|count|number of",
            r"system (?:message|prompt)|\brole\b|assistant message",
            r"数量|多张|张数|超过|超出|上限|系统消息",
        ]
    ),
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.\n。；;!?！？]")

# "At most 0 images" is no limit on pictures: it is a model that takes none
# (what vLLM answers for a model without vision).
_NO_PICTURE_ALLOWED = re.compile(
    r"\b(?:at most|a maximum of|maximum(?: of)?|max(?: of)?|up to|limit(?:ed)? (?:of|to)) (?:0|zero|no) (?:image|picture)",
    re.IGNORECASE,
)


def _about_these_pictures(text: str) -> bool:
    if _ABOUT_THESE_PICTURES.search(text):
        return True
    return any(
        _MENTIONS_PICTURES.search(sentence) and _LIMITS_ON_PICTURES.search(sentence)
        for sentence in _SENTENCE_END.split(text)
    )

REFUSAL_OF_THE_MODEL = "model"
REFUSAL_OF_THESE_PICTURES = "pictures"
# A provider that has refused the pictures of this many requests of a session
# is sent no more pictures in that session: each refusal costs a request, and
# a tool that takes a screenshot at every step would pay it at every step.
PICTURE_REFUSALS_BEFORE_GOING_WITHOUT = 2


def image_refusal(error: BaseException) -> str | None:
    """Say whether a provider's rejection is about the pictures a request carried, and of which kind.

    ``REFUSAL_OF_THE_MODEL``: the model takes no pictures at all; worth
    remembering. ``REFUSAL_OF_THESE_PICTURES``: it would not take these (a
    format, a size, too many of them); the request can go once more without
    them, and nothing follows for the model. None: not about pictures.
    Only asked of a request that carried pictures.
    """
    if is_context_overflow(error):
        # The input is too long, pictures or not ("... tokens (including
        # image tokens)"): the loop makes the context shorter. Taken for a
        # refusal of pictures, two of these would end the pictures of the
        # session, and with them the tools that work from pictures.
        return None
    status = getattr(error, "status_code", None)
    if status == 413:
        # Too large a request: the pictures are what weighs.
        return REFUSAL_OF_THESE_PICTURES
    if status is not None and status not in (400, 404, 415, 422):
        return None
    text = str(error)
    if not _MENTIONS_PICTURES.search(text):
        return None
    if _NO_PICTURE_ALLOWED.search(text):
        return REFUSAL_OF_THE_MODEL
    if _IMAGE_REFUSAL_PATTERNS.search(text) and not _about_these_pictures(text):
        return REFUSAL_OF_THE_MODEL
    if status is None:
        # No status to go by: only what is plainly a refusal counts.
        return REFUSAL_OF_THESE_PICTURES if _IMAGE_REFUSAL_PATTERNS.search(text) else None
    return REFUSAL_OF_THESE_PICTURES


def is_image_refusal(error: BaseException) -> bool:
    """Recognize a provider's "this model takes no pictures" rejection from its message."""
    return image_refusal(error) == REFUSAL_OF_THE_MODEL


def _image_key(image: ImageContent) -> str:
    """Return what a picture is known by, to leave it out of later requests."""
    return hashlib.sha1(image.data.encode("ascii", errors="replace"), usedforsecurity=False).hexdigest()


def _image_keys(messages: Sequence[AgentMessage]) -> set[str]:
    keys: set[str] = set()
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            keys.update(_image_key(block) for block in content if isinstance(block, ImageContent))
    return keys


# ---------------------------------------------------------------------------
# Loop messages -> OpenAI-format messages
# ---------------------------------------------------------------------------


def _bounded(text: str, part_max_tokens: int) -> str:
    """Head/tail-truncate one text part that exceeds the per-part token cap."""
    # A token is at least one byte, so a short text cannot exceed the cap and
    # is not tokenized at all.
    if len(text.encode("utf-8", errors="replace")) <= part_max_tokens:
        return text
    from utils.tokenize import truncate_head_tail_tokens

    half = part_max_tokens // 2
    return truncate_head_tail_tokens(
        text, half, part_max_tokens - half, separator="\n[... middle omitted ...]\n"
    )


def _image_part(image: ImageContent) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.mime_type};base64,{image.data}"},
    }


def _user_content(content: str | list[Any], *, supports_images: bool, cap: int, withheld: frozenset[str]) -> Any:
    if isinstance(content, str):
        return _bounded(content, cap)
    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": _bounded(block.text, cap)})
        elif isinstance(block, ImageContent):
            if not supports_images:
                parts.append({"type": "text", "text": _IMAGE_NOT_ACCEPTED})
            elif withheld and _image_key(block) in withheld:
                parts.append({"type": "text", "text": _IMAGE_WITHHELD})
            else:
                parts.append(_image_part(block))
    if all(part["type"] == "text" for part in parts):
        return "\n".join(part["text"] for part in parts)
    return parts


def _tool_call_wire(call: ToolCall) -> dict[str, Any]:
    arguments = call.arguments
    if INVALID_ARGUMENTS_KEY in arguments:
        # Send back what the model actually wrote, so its history stays its own.
        raw = str(arguments[INVALID_ARGUMENTS_KEY])
        try:
            json.loads(raw)
            text = raw
        except ValueError:
            text = json.dumps({INVALID_ARGUMENTS_KEY: raw}, ensure_ascii=False)
    else:
        text = json.dumps(arguments, ensure_ascii=False)
    entry: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": text},
    }
    if call.thought_signature:
        try:
            extra = json.loads(call.thought_signature)
        except ValueError:
            extra = None
        if isinstance(extra, dict) and extra:
            entry["extra_content"] = extra
    return entry


def to_wire_messages(
    system: str,
    messages: Sequence[AgentMessage],
    *,
    anthropic: bool,
    supports_images: bool,
    part_max_tokens: int,
    withheld: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Convert a loop transcript into OpenAI-format message dicts.

    ``anthropic`` selects how tool results carry images: the Anthropic client
    accepts them inside the tool message, while OpenAI-compatible endpoints
    only take text there, so images follow in a user message placed after the
    tool results of that turn. ``withheld`` names pictures (``_image_key``)
    the provider refused: a line of text stands in their place.
    """
    wire: list[dict[str, Any]] = []
    if system:
        wire.append({"role": "system", "content": system})
    deferred_images: list[dict[str, Any]] = []

    def flush_images() -> None:
        if deferred_images:
            wire.append({"role": "user", "content": list(deferred_images)})
            deferred_images.clear()

    for message in messages:
        if not isinstance(message, ToolResultMessage):
            flush_images()
        if isinstance(message, UserMessage):
            wire.append(
                {
                    "role": "user",
                    "content": _user_content(
                        message.content, supports_images=supports_images, cap=part_max_tokens, withheld=withheld
                    ),
                }
            )
        elif isinstance(message, AssistantMessage):
            text = message.text
            calls = [_tool_call_wire(call) for call in message.tool_calls]
            if not text and not calls:
                continue
            entry: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                entry["tool_calls"] = calls
                thinking = "".join(
                    block.thinking
                    for block in message.content
                    if isinstance(block, ThinkingContent)
                    and block.thinking_signature == REASONING_FIELD_SIGNATURE
                )
                if thinking:
                    entry["reasoning_content"] = thinking
            wire.append(entry)
        elif isinstance(message, ToolResultMessage):
            text = _bounded(message.text, part_max_tokens) or "(empty result)"
            if message.is_error and not text.startswith("Error: "):
                text = f"Error: {text}"
            images = [block for block in message.content if isinstance(block, ImageContent)]
            content: Any = text
            if images and supports_images and withheld:
                kept = [image for image in images if _image_key(image) not in withheld]
                if len(kept) < len(images):
                    text = f"{text}\n{_IMAGE_WITHHELD}"
                    content = text
                images = kept
            if images and not supports_images:
                content = f"{text}\n{_IMAGE_NOT_ACCEPTED}"
            elif images and anthropic:
                content = [{"type": "text", "text": text}, *(_image_part(i) for i in images)]
            elif images:
                deferred_images.append(
                    {
                        "type": "text",
                        "text": f"Image returned by the {message.tool_name} call {message.tool_call_id}:",
                    }
                )
                deferred_images.extend(_image_part(image) for image in images)
            wire.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": content})
        elif isinstance(message, BashExecutionMessage):
            if not message.exclude_from_context:
                wire.append({"role": "user", "content": _bounded(message.output, part_max_tokens)})
        else:
            wire.append({"role": "user", "content": _bounded(message_text(message), part_max_tokens)})
    flush_images()
    return wire


def to_wire_tools(tools: Sequence[AgentTool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            },
        }
        for tool in tools
    ]


# ---------------------------------------------------------------------------
# Stream chunks -> loop events
# ---------------------------------------------------------------------------


def usage_from_metadata(metadata: dict[str, Any]) -> Usage:
    """Map the model layer's usage dict onto the loop's ``Usage``."""

    def number(*keys: str) -> int:
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    output = number("completion_tokens", "output_tokens")
    if "input_tokens" in metadata:
        # Anthropic shape: input excludes the cached portions.
        fresh = number("input_tokens")
        cache_read = number("cache_read_input_tokens")
        cache_write = number("cache_creation_input_tokens")
    else:
        details = metadata.get("prompt_tokens_details")
        cache_read = 0
        if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
            cache_read = int(details["cached_tokens"])
        fresh = max(0, number("prompt_tokens") - cache_read)
        cache_write = 0
    reasoning: int | None = None
    completion_details = metadata.get("completion_tokens_details")
    if isinstance(completion_details, dict) and isinstance(
        completion_details.get("reasoning_tokens"), int
    ):
        reasoning = int(completion_details["reasoning_tokens"])
    total = number("total_tokens") or fresh + cache_read + cache_write + output
    return Usage(
        input=fresh,
        output=output,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning=reasoning,
        total_tokens=total,
    )


class _ThinkTagSplitter:
    """Routes a leading ``<think>...</think>`` span of the text stream to thinking.

    Some local reasoning models write their reasoning into the content stream
    inside ``<think>`` tags. Only a span that opens the response is treated
    this way; text that merely mentions the tag later is left alone.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._state = "probe"
        self._buffer = ""

    def feed(self, text: str) -> tuple[str, str]:
        """Return ``(thinking, visible)`` for one content fragment."""
        if self._state == "text":
            return "", text
        self._buffer += text
        if self._state == "probe":
            stripped = self._buffer.lstrip()
            if stripped.startswith(self._OPEN):
                self._state = "thinking"
                self._buffer = stripped[len(self._OPEN):]
            elif self._OPEN.startswith(stripped) and len(stripped) < len(self._OPEN):
                return "", ""
            else:
                self._state = "text"
                visible, self._buffer = self._buffer, ""
                return "", visible
        end = self._buffer.find(self._CLOSE)
        if end != -1:
            thinking = self._buffer[:end]
            visible = self._buffer[end + len(self._CLOSE):].lstrip("\n")
            self._buffer = ""
            self._state = "text"
            return thinking, visible
        # Hold back a tail that could be the start of the closing tag.
        keep = 0
        for size in range(min(len(self._CLOSE) - 1, len(self._buffer)), 0, -1):
            if self._CLOSE.startswith(self._buffer[-size:]):
                keep = size
                break
        thinking = self._buffer[: len(self._buffer) - keep]
        self._buffer = self._buffer[len(self._buffer) - keep:]
        return thinking, ""

    def flush(self) -> tuple[str, str]:
        rest, self._buffer = self._buffer, ""
        if self._state == "thinking":
            return rest, ""
        return "", rest


class _Aborted(Exception):
    pass


class _ResponseBuilder:
    """Accumulates one streamed response and produces the loop's events."""

    def __init__(self, model: str, provider: str) -> None:
        self.partial = AssistantMessage(model=model, provider=provider, api="chat-completions")
        self.finish_reason: str | None = None
        self.emitted_output = False
        self.dropped_truncated_tools: list[str] = []
        self.dropped_unnamed_tool_calls = False
        self._splitter = _ThinkTagSplitter()
        self._open: str | None = None
        self._tool_slots: dict[int, dict[str, Any]] = {}

    # -- text and thinking ----------------------------------------------------

    def _close_open_block(self) -> list[AssistantMessageEvent]:
        if self._open is None:
            return []
        index = len(self.partial.content) - 1
        block = self.partial.content[index]
        kind, self._open = self._open, None
        if kind == "text" and isinstance(block, TextContent):
            return [TextEndEvent(content_index=index, content=block.text, partial=self.partial)]
        if kind != "text" and isinstance(block, ThinkingContent):
            return [
                ThinkingEndEvent(content_index=index, content=block.thinking, partial=self.partial)
            ]
        return []

    def _append(self, kind: str, delta: str) -> list[AssistantMessageEvent]:
        if not delta:
            return []
        events: list[AssistantMessageEvent] = []
        if self._open != kind:
            events.extend(self._close_open_block())
            if kind == "text":
                self.partial.content.append(TextContent(text=""))
                events.append(
                    TextStartEvent(content_index=len(self.partial.content) - 1, partial=self.partial)
                )
            else:
                signature = REASONING_FIELD_SIGNATURE if kind == "reasoning" else None
                self.partial.content.append(
                    ThinkingContent(thinking="", thinking_signature=signature)
                )
                events.append(
                    ThinkingStartEvent(
                        content_index=len(self.partial.content) - 1, partial=self.partial
                    )
                )
            self._open = kind
        index = len(self.partial.content) - 1
        block = self.partial.content[index]
        self.emitted_output = True
        if isinstance(block, TextContent):
            block.text += delta
            events.append(TextDeltaEvent(content_index=index, delta=delta, partial=self.partial))
        elif isinstance(block, ThinkingContent):
            block.thinking += delta
            events.append(
                ThinkingDeltaEvent(content_index=index, delta=delta, partial=self.partial)
            )
        return events

    # -- chunks ---------------------------------------------------------------

    def feed(self, chunk: Any) -> list[AssistantMessageEvent]:
        events: list[AssistantMessageEvent] = []
        reasoning = getattr(chunk, "reasoning_content", None)
        if reasoning:
            events.extend(self._append("reasoning", str(reasoning)))
        content = getattr(chunk, "content", "") or ""
        if content:
            thinking, visible = self._splitter.feed(str(content))
            events.extend(self._append("tagged", thinking))
            events.extend(self._append("text", visible))
        for fragment in getattr(chunk, "tool_call_deltas", None) or []:
            events.extend(self._tool_fragment(fragment))
        finish = getattr(chunk, "finish_reason", None)
        if finish:
            self.finish_reason = str(finish)
        usage = getattr(chunk, "usage_metadata", None)
        if isinstance(usage, dict) and usage:
            self.partial.usage = usage_from_metadata(usage)
        return events

    def _tool_fragment(self, fragment: dict[str, Any]) -> list[AssistantMessageEvent]:
        index = int(fragment.get("index", 0) or 0)
        slot = self._tool_slots.get(index)
        events: list[AssistantMessageEvent] = []
        if slot is None:
            events.extend(self._close_open_block())
            slot = {"id": "", "name": "", "arguments": "", "extra_content": None}
            self._tool_slots[index] = slot
            self.emitted_output = True
            events.append(
                ToolCallStartEvent(content_index=len(self.partial.content), partial=self.partial)
            )
        if fragment.get("id"):
            slot["id"] = str(fragment["id"])
        function = fragment.get("function") or {}
        if function.get("name"):
            slot["name"] = str(function["name"])
        arguments = function.get("arguments")
        if arguments:
            slot["arguments"] += str(arguments)
            events.append(
                ToolCallDeltaEvent(
                    content_index=len(self.partial.content),
                    delta=str(arguments),
                    partial=self.partial,
                )
            )
        extra = fragment.get("extra_content")
        if isinstance(extra, dict) and extra:
            slot["extra_content"] = _merge(slot["extra_content"], extra)
        return events

    def has_complete_tool_calls(self) -> bool:
        """Whether every streamed tool call slot has a name and valid JSON object arguments."""
        if not self._tool_slots:
            return False
        found = False
        for slot in self._tool_slots.values():
            if not slot["name"]:
                return False
            raw = slot["arguments"]
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except ValueError:
                return False
            if not isinstance(parsed, dict):
                return False
            found = True
        return found

    def has_salvageable_text(self) -> bool:
        """Whether visible assistant text was emitted and no tool call is left half-streamed."""
        if self._tool_slots:
            return False
        if self.partial.text.strip():
            return True
        return self._splitter._state != "thinking" and bool(self._splitter._buffer.strip())

    def feed_raw_response(self, raw: Any) -> list[AssistantMessageEvent]:
        """Convert a non-streaming ``ainvoke_raw`` response into builder events."""
        from utils.llm_client.messages import LLMStreamChunk

        events: list[AssistantMessageEvent] = []
        choices = getattr(raw, "choices", None)
        if choices:
            choice = choices[0]
            message = getattr(choice, "message", None)
            reasoning = getattr(message, "reasoning_content", None) if message is not None else None
            content = getattr(message, "content", None) if message is not None else None
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            deltas: list[dict[str, Any]] = []
            for idx, call in enumerate(getattr(message, "tool_calls", None) or []):
                fn = getattr(call, "function", None)
                entry: dict[str, Any] = {
                    "index": idx,
                    "id": str(getattr(call, "id", "") or ""),
                    "type": "function",
                    "function": {
                        "name": str(getattr(fn, "name", "") or ""),
                        "arguments": str(getattr(fn, "arguments", "") or ""),
                    },
                }
                extra = getattr(call, "extra_content", None)
                if extra is None and hasattr(call, "model_extra") and isinstance(call.model_extra, dict):
                    extra = call.model_extra.get("extra_content")
                if isinstance(extra, dict) and extra:
                    entry["extra_content"] = extra
                deltas.append(entry)
            usage_obj = getattr(raw, "usage", None)
            usage_meta: dict[str, Any] | None = None
            if usage_obj is not None:
                if isinstance(usage_obj, dict):
                    usage_meta = usage_obj
                elif hasattr(usage_obj, "model_dump"):
                    usage_meta = usage_obj.model_dump()
            events.extend(
                self.feed(
                    LLMStreamChunk(
                        content=str(content or ""),
                        reasoning_content=str(reasoning) if reasoning else None,
                        tool_call_deltas=deltas,
                        finish_reason=getattr(choice, "finish_reason", None),
                        usage_metadata=usage_meta,
                    )
                )
            )
            return events

        # Anthropic Message shape (content blocks list).
        blocks = getattr(raw, "content", None)
        if isinstance(blocks, list):
            tool_idx = 0
            for block in blocks:
                btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if btype == "text":
                    text = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
                    if text:
                        events.extend(self.feed(LLMStreamChunk(content=str(text))))
                elif btype == "tool_use":
                    raw_input = getattr(block, "input", None) if not isinstance(block, dict) else block.get("input")
                    call_id = getattr(block, "id", "") if not isinstance(block, dict) else block.get("id", "")
                    name = getattr(block, "name", "") if not isinstance(block, dict) else block.get("name", "")
                    events.extend(
                        self.feed(
                            LLMStreamChunk(
                                content="",
                                tool_call_deltas=[
                                    {
                                        "index": tool_idx,
                                        "id": str(call_id or ""),
                                        "type": "function",
                                        "function": {
                                            "name": str(name or ""),
                                            "arguments": json.dumps(raw_input or {}, ensure_ascii=False),
                                        },
                                    }
                                ],
                            )
                        )
                    )
                    tool_idx += 1
            stop_reason = getattr(raw, "stop_reason", None)
            mapped_stop = {
                "end_turn": "stop",
                "stop_sequence": "stop",
                "max_tokens": "length",
                "tool_use": "tool_calls",
            }.get(str(stop_reason or ""), stop_reason)
            usage_obj = getattr(raw, "usage", None)
            usage_meta = None
            if usage_obj is not None:
                if isinstance(usage_obj, dict):
                    usage_meta = usage_obj
                elif hasattr(usage_obj, "model_dump"):
                    usage_meta = usage_obj.model_dump()
            events.extend(
                self.feed(
                    LLMStreamChunk(
                        content="",
                        finish_reason=str(mapped_stop) if mapped_stop else None,
                        usage_metadata=usage_meta,
                    )
                )
            )
        return events

    # -- completion -----------------------------------------------------------

    def finish(self) -> list[AssistantMessageEvent]:
        """Close open blocks, assemble the tool calls and emit the terminal event."""
        events: list[AssistantMessageEvent] = []
        thinking, visible = self._splitter.flush()
        events.extend(self._append("tagged", thinking))
        events.extend(self._append("text", visible))
        events.extend(self._close_open_block())
        truncated = self.finish_reason == "length"
        for position, index in enumerate(sorted(self._tool_slots)):
            slot = self._tool_slots[index]
            if not slot["name"]:
                # A fragment without a name is a streaming glitch; sending it back
                # would make the provider reject the whole history.
                self.dropped_unnamed_tool_calls = True
                logger.warning("dropping a streamed tool call without a name (index %d)", index)
                continue
            raw = slot["arguments"]
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                if truncated:
                    # The output limit cut the call short; running it would act on
                    # half an argument list.
                    self.dropped_truncated_tools.append(slot["name"])
                    continue
                parsed = {INVALID_ARGUMENTS_KEY: raw}
            extra = slot["extra_content"]
            call = ToolCall(
                id=slot["id"] or f"call_{position}",
                name=slot["name"],
                arguments=parsed,
                thought_signature=json.dumps(extra, ensure_ascii=False) if extra else None,
            )
            self.partial.content.append(call)
            events.append(
                ToolCallEndEvent(
                    content_index=len(self.partial.content) - 1,
                    tool_call=call,
                    partial=self.partial,
                )
            )
        if self.partial.tool_calls:
            reason = "toolUse"
        elif truncated:
            reason = "length"
        else:
            reason = "stop"
        self.partial.stop_reason = reason  # type: ignore[assignment]
        events.append(AssistantDoneEvent(reason=reason, message=self.partial))  # type: ignore[arg-type]
        return events

    def fail(self, reason: str, message: str) -> AssistantErrorEvent:
        self.partial.stop_reason = reason  # type: ignore[assignment]
        self.partial.error_message = message
        return AssistantErrorEvent(reason=reason, error=self.partial)  # type: ignore[arg-type]


def _merge(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    if not old:
        return dict(new)
    merged = dict(old)
    for key, value in new.items():
        previous = merged.get(key)
        merged[key] = (
            _merge(previous, value)
            if isinstance(previous, dict) and isinstance(value, dict)
            else value
        )
    return merged


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

ClientFactory = Callable[[], Awaitable[Any]]


class LLMClientProvider:
    """``ModelProvider`` backed by ``utils.llm_client``.

    The client is created on first use. ``client_factory`` replaces the real
    client in tests; it must return an object with an ``astream(messages,
    **overrides)`` async generator of ``LLMStreamChunk``-like objects.

    Whether the model can see pictures is what the configuration says until
    the provider says otherwise. A request with pictures that is refused for
    carrying them is sent once more with a line of text in their place, and
    then it depends on what the provider said (``image_refusal``):

    * the model takes no pictures: every later request of this provider goes
      without them, and ``on_images_refused`` is called once, with what the
      provider said, so whoever configured the model can remember it;
    * it would not take *these* pictures (their format, their size, how many
      there are): they are not sent again, later pictures are. Should that
      happen ``PICTURE_REFUSALS_BEFORE_GOING_WITHOUT`` times, the rest of the
      session goes without pictures and ``on_images_refused`` is called with
      ``about_model`` False: said for this session, not about the model.
    """

    def __init__(
        self,
        config: ModelConfig,
        *,
        client_factory: ClientFactory | None = None,
        on_images_refused: Callable[[str, bool], None] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._client: Any = None
        self._client_lock = asyncio.Lock()
        self._supports_images = bool(config.supports_images)
        self.on_images_refused = on_images_refused
        # Pictures the provider would not take, by ``_image_key``.
        self._withheld: set[str] = set()
        self._picture_refusals = 0
        # Per-request diagnostics inspected by ``TaskAgentSession._finish_turn``
        # for targeted cutoff and malformed-tool-call nudges.
        self.last_dropped_truncated_tools: tuple[str, ...] = ()
        self.last_dropped_unnamed_tool_calls = False

    @property
    def config(self) -> ModelConfig:
        return self._config

    @property
    def supports_images(self) -> bool:
        """Whether requests carry pictures: the configured value, until the provider refused one."""
        return self._supports_images

    def _images_refused(self, said: str, *, about_model: bool) -> None:
        self._supports_images = False
        callback = self.on_images_refused
        if callback is None:
            return
        try:
            callback(said, about_model)
        except Exception:  # noqa: BLE001 - remembering it is best effort
            logger.debug("reporting that the model takes no pictures failed", exc_info=True)

    async def _get_client(self) -> Any:
        async with self._client_lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = await self._client_factory()
                else:
                    self._client = await self._create_client()
            return self._client

    async def _create_client(self) -> Any:
        from utils.http.pool_utils import DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
        from utils.llm_client import create_chat_llm_async

        config = self._config
        extra: dict[str, Any] = {}
        if config.extra_body is not None:
            extra["extra_body"] = config.extra_body
        elif not config.auto_extra_body:
            extra["extra_body"] = None
        return await create_chat_llm_async(
            config.model,
            config.base_url,
            config.api_key,
            max_completion_tokens=config.max_output_tokens,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retries,
            keepalive_expiry=DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            provider_type=config.provider_type,
            streaming=True,
            **extra,
        )

    async def aclose(self) -> None:
        client, self._client = self._client, None
        close = getattr(client, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - closing a client must not fail a shutdown
                logger.debug("closing the model client failed", exc_info=True)

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        # ``max_tokens``: the output to ask for, when that is less than the
        # model is configured for (a session in a small window).
        del session_id
        return self._stream(model, system, messages, tools, signal, max_tokens)

    async def complete(
        self,
        *,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        max_tokens: int,
        signal: CancellationToken | None = None,
        skip_cache_write: bool = False,
        strip_images: bool = False,
    ) -> AssistantMessage:
        """Run one request to completion and return the assistant message.

        Raises ``ContextOverflowError`` or ``ModelRequestError`` on failure.
        """
        final: AssistantMessage | None = None
        # Closed here rather than whenever the generator is collected: leaving
        # on a failed or aborted response must end the HTTP stream right away.
        async with contextlib.aclosing(
            self._stream(
                self._config.model,
                system,
                messages,
                tools,
                signal,
                max_tokens,
                skip_cache_write=skip_cache_write,
                strip_images=strip_images,
            )
        ) as events:
            async for event in events:
                if isinstance(event, AssistantDoneEvent):
                    final = event.message
                elif isinstance(event, AssistantErrorEvent):
                    raise ModelRequestError(event.error.error_message or event.reason)
        # (A request refused outright raises from the stream itself, as one
        # that is too long does: ``RequestRefusedError``, ``ContextOverflowError``.)
        if final is None:
            raise ModelRequestError("the model returned no response")
        return final

    async def _stream(
        self,
        model: str,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentTool],
        signal: CancellationToken | None,
        max_tokens: int | None,
        *,
        skip_cache_write: bool = False,
        strip_images: bool = False,
    ) -> AsyncIterator[AssistantMessageEvent]:
        from config.nekopower_settings import (
            TASK_AGENT_STREAM_IDLE_TIMEOUT_SECONDS,
            TASK_AGENT_STREAM_MAX_RETRIES,
        )

        config = self._config
        self.last_dropped_truncated_tools = ()
        self.last_dropped_unnamed_tool_calls = False
        builder = _ResponseBuilder(model, config.provider_type or "openai-compatible")
        yield AssistantStartEvent(partial=builder.partial)
        iterator: Any = None
        pending: asyncio.Future[Any] | None = None

        async def _close_stream() -> None:
            nonlocal iterator, pending
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            pending = None
            close = getattr(iterator, "aclose", None)
            iterator = None
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 - best effort; the request is over either way
                    logger.debug("closing the model stream failed", exc_info=True)

        try:
            client = await self._get_client()
            anthropic = (
                getattr(client, "wire_dialect", None) == "anthropic"
                or type(client).__name__ == "ChatAnthropic"
            )
            part_cap = min(WIRE_TEXT_PART_MAX_TOKENS, max(1_000, config.context_window // 2))
            overrides: dict[str, Any] = {"tools": to_wire_tools(tools) or None}
            if max_tokens is not None:
                overrides["max_completion_tokens"] = max_tokens
            if skip_cache_write:
                overrides["skip_cache_write"] = True
            stream_retries = 0
            max_stream_retries = max(0, int(TASK_AGENT_STREAM_MAX_RETRIES))
            idle_timeout = float(TASK_AGENT_STREAM_IDLE_TIMEOUT_SECONDS)
            used_nonstream_fallback = False
            loop = asyncio.get_running_loop()
            while True:
                effective_supports_images = self._supports_images and not strip_images
                # The pictures this request carries, as far as they are sent at all.
                carried = (
                    await asyncio.to_thread(_image_keys, list(messages)) - self._withheld
                    if effective_supports_images
                    else set()
                )
                with_images = bool(carried)
                wire = await asyncio.to_thread(
                    to_wire_messages,
                    system,
                    list(messages),
                    anthropic=anthropic,
                    supports_images=effective_supports_images,
                    part_max_tokens=part_cap,
                    withheld=frozenset(self._withheld),
                )
                if signal is not None and signal.is_cancelled():
                    raise _Aborted
                iterator = client.astream(wire, **overrides).__aiter__()
                try:
                    last_activity = loop.time()
                    while True:
                        pending = asyncio.ensure_future(iterator.__anext__())
                        while True:
                            done, _waiting = await asyncio.wait({pending}, timeout=_CANCEL_POLL_SECONDS)
                            if done:
                                break
                            if signal is not None and signal.is_cancelled():
                                raise _Aborted
                            if idle_timeout > 0 and (loop.time() - last_activity) >= idle_timeout:
                                pending.cancel()
                                await asyncio.gather(pending, return_exceptions=True)
                                pending = None
                                raise _StreamIdleTimeoutError(
                                    f"model stream stalled for {idle_timeout:.0f}s with no chunks"
                                )
                        try:
                            chunk = pending.result()
                        except StopAsyncIteration:
                            break
                        finally:
                            pending = None
                        last_activity = loop.time()
                        for event in builder.feed(chunk):
                            yield event
                        if signal is not None and signal.is_cancelled():
                            raise _Aborted
                    break
                except _Aborted:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified below; re-raised unless handled
                    kind = image_refusal(exc) if with_images and not builder.emitted_output else None
                    if kind is not None:
                        if kind == REFUSAL_OF_THE_MODEL:
                            # The model takes no pictures. Sent once more without
                            # them, and without them from here on.
                            logger.warning("the model takes no pictures; sending the request without them: %s", exc)
                            self._images_refused(str(exc), about_model=True)
                        else:
                            # It would not take these. Sent once more without
                            # them; they are not sent again, later ones are.
                            logger.warning("the provider refused the pictures of a request; sending it without them: %s", exc)
                            self._withheld |= carried
                            self._picture_refusals += 1
                            if self._picture_refusals >= PICTURE_REFUSALS_BEFORE_GOING_WITHOUT:
                                self._images_refused(str(exc), about_model=False)
                        await _close_stream()
                        continue

                    # Streaming endpoint returned HTTP 404 before any output: try
                    # one-shot non-streaming fallback if the client supports it.
                    if (
                        not builder.emitted_output
                        and not used_nonstream_fallback
                        and getattr(exc, "status_code", None) == 404
                        and callable(getattr(client, "ainvoke_raw", None))
                    ):
                        used_nonstream_fallback = True
                        logger.warning("streaming endpoint returned 404; falling back once to non-streaming call")
                        await _close_stream()
                        if signal is not None and signal.is_cancelled():
                            raise _Aborted
                        raw_resp = await client.ainvoke_raw(wire, **overrides)
                        for event in builder.feed_raw_response(raw_resp):
                            yield event
                        break

                    # Pre-output HTTP 529 overloaded or idle stream timeout: retry with backoff.
                    if (
                        not builder.emitted_output
                        and (_is_overloaded_529(exc) or isinstance(exc, _StreamIdleTimeoutError))
                        and stream_retries < max_stream_retries
                    ):
                        delay = _retry_delay(stream_retries, exc)
                        stream_retries += 1
                        logger.warning(
                            "transient pre-output stream failure (%s); retrying %d/%d in %.2fs",
                            exc,
                            stream_retries,
                            max_stream_retries,
                            delay,
                        )
                        await _close_stream()
                        await _sleep_cancellable(delay, signal)
                        continue

                    # 5-state mid-stream recovery on transient drop or stall.
                    if builder.emitted_output and _is_transient_stream_drop(exc):
                        await _close_stream()
                        if builder.has_complete_tool_calls():
                            logger.warning(
                                "stream dropped after complete tool call(s); salvaging tool turn: %s", exc
                            )
                            break
                        if builder.has_salvageable_text():
                            logger.warning(
                                "stream dropped after partial visible text; salvaging as length cutoff: %s", exc
                            )
                            builder.finish_reason = "length"
                            break
                        if stream_retries < max_stream_retries:
                            delay = _retry_delay(stream_retries, exc)
                            stream_retries += 1
                            logger.warning(
                                "stream dropped mid-reasoning/incomplete-tool (%s); resetting and retrying %d/%d in %.2fs",
                                exc,
                                stream_retries,
                                max_stream_retries,
                                delay,
                            )
                            builder = _ResponseBuilder(model, config.provider_type or "openai-compatible")
                            await _sleep_cancellable(delay, signal)
                            continue
                    raise
            finished = builder.finish()
            self.last_dropped_truncated_tools = tuple(builder.dropped_truncated_tools)
            self.last_dropped_unnamed_tool_calls = builder.dropped_unnamed_tool_calls
            for event in finished:
                yield event
        except _Aborted:
            yield builder.fail("aborted", "Operation aborted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the loop as a failed response
            if not builder.emitted_output and is_context_overflow(exc):
                raise ContextOverflowError(str(exc)) from exc
            if not builder.emitted_output and getattr(exc, "status_code", None) in OVERFLOW_STATUSES:
                # Told as it would be reported, for whoever does report it.
                raise RequestRefusedError(f"{type(exc).__name__}: {exc}") from exc
            logger.warning("model request failed: %s", exc)
            yield builder.fail("error", f"{type(exc).__name__}: {exc}")
        finally:
            await _close_stream()

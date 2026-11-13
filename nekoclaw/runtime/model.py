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

Retries are those of the model layer (the SDK's ``max_retries``); nothing is
retried here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from nekoclaw.runtime.loop.messages import (
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
from nekoclaw.runtime.loop.provider import CancellationToken
from nekoclaw.runtime.loop.provider_events import (
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
from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime.tools.base import INVALID_ARGUMENTS_KEY

logger = logging.getLogger(__name__)

# Backstop on a single text part of a request. Context management keeps the
# conversation as a whole within the window; this bounds one part that it
# cannot shrink, such as an enormous message from the user.
WIRE_TEXT_PART_MAX_TOKENS = 64_000
_CANCEL_POLL_SECONDS = 0.1
_IMAGE_NOT_ACCEPTED = "[image omitted: the model does not accept images]"
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
        ]
    ),
    re.IGNORECASE,
)


class ContextOverflowError(Exception):
    """The provider refused the request because the input exceeds its window."""


class ModelRequestError(Exception):
    """A one-shot model request (see ``complete``) failed."""


def is_context_overflow(error: BaseException) -> bool:
    """Recognize a provider's "input too long" rejection from its message."""
    status = getattr(error, "status_code", None)
    if status is not None and status not in (400, 413, 422):
        return False
    return bool(_OVERFLOW_PATTERNS.search(str(error)))


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


def is_image_refusal(error: BaseException) -> bool:
    """Recognize a provider's "this model takes no pictures" rejection from its message."""
    status = getattr(error, "status_code", None)
    if status is not None and status not in (400, 404, 415, 422):
        return False
    return bool(_IMAGE_REFUSAL_PATTERNS.search(str(error)))


def _carries_images(messages: Sequence[AgentMessage]) -> bool:
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, list) and any(isinstance(block, ImageContent) for block in content):
            return True
    return False


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


def _user_content(content: str | list[Any], *, supports_images: bool, cap: int) -> Any:
    if isinstance(content, str):
        return _bounded(content, cap)
    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": _bounded(block.text, cap)})
        elif isinstance(block, ImageContent):
            parts.append(
                _image_part(block)
                if supports_images
                else {"type": "text", "text": _IMAGE_NOT_ACCEPTED}
            )
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
) -> list[dict[str, Any]]:
    """Convert a loop transcript into OpenAI-format message dicts.

    ``anthropic`` selects how tool results carry images: the Anthropic client
    accepts them inside the tool message, while OpenAI-compatible endpoints
    only take text there, so images follow in a user message placed after the
    tool results of that turn.
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
                        message.content, supports_images=supports_images, cap=part_max_tokens
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
    the provider says otherwise: a request with pictures that is refused for
    carrying them is sent once more with a placeholder in their place, every
    later request of this provider goes without them, and
    ``on_images_refused`` is called once so whoever configured the model can
    remember it.
    """

    def __init__(
        self,
        config: ModelConfig,
        *,
        client_factory: ClientFactory | None = None,
        on_images_refused: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._client: Any = None
        self._client_lock = asyncio.Lock()
        self._supports_images = bool(config.supports_images)
        self.on_images_refused = on_images_refused

    @property
    def config(self) -> ModelConfig:
        return self._config

    @property
    def supports_images(self) -> bool:
        """Whether requests carry pictures: the configured value, until the provider refused one."""
        return self._supports_images

    def _images_refused(self) -> None:
        self._supports_images = False
        callback = self.on_images_refused
        if callback is None:
            return
        try:
            callback()
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
    ) -> AsyncIterator[AssistantMessageEvent]:
        del session_id
        return self._stream(model, system, messages, tools, signal, None)

    async def complete(
        self,
        *,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        max_tokens: int,
        signal: CancellationToken | None = None,
    ) -> AssistantMessage:
        """Run one request to completion and return the assistant message.

        Raises ``ContextOverflowError`` or ``ModelRequestError`` on failure.
        """
        final: AssistantMessage | None = None
        # Closed here rather than whenever the generator is collected: leaving
        # on a failed or aborted response must end the HTTP stream right away.
        async with contextlib.aclosing(
            self._stream(self._config.model, system, messages, tools, signal, max_tokens)
        ) as events:
            async for event in events:
                if isinstance(event, AssistantDoneEvent):
                    final = event.message
                elif isinstance(event, AssistantErrorEvent):
                    raise ModelRequestError(event.error.error_message or event.reason)
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
    ) -> AsyncIterator[AssistantMessageEvent]:
        config = self._config
        builder = _ResponseBuilder(model, config.provider_type or "openai-compatible")
        yield AssistantStartEvent(partial=builder.partial)
        iterator: Any = None
        pending: asyncio.Future[Any] | None = None
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
            while True:
                with_images = self._supports_images and _carries_images(messages)
                wire = await asyncio.to_thread(
                    to_wire_messages,
                    system,
                    list(messages),
                    anthropic=anthropic,
                    supports_images=self._supports_images,
                    part_max_tokens=part_cap,
                )
                if signal is not None and signal.is_cancelled():
                    raise _Aborted
                iterator = client.astream(wire, **overrides).__aiter__()
                try:
                    while True:
                        pending = asyncio.ensure_future(iterator.__anext__())
                        while True:
                            done, _waiting = await asyncio.wait({pending}, timeout=_CANCEL_POLL_SECONDS)
                            if done:
                                break
                            if signal is not None and signal.is_cancelled():
                                raise _Aborted
                        try:
                            chunk = pending.result()
                        except StopAsyncIteration:
                            break
                        finally:
                            pending = None
                        for event in builder.feed(chunk):
                            yield event
                        if signal is not None and signal.is_cancelled():
                            raise _Aborted
                    break
                except Exception as exc:  # noqa: BLE001 - looked at here, raised again unless it is the one case
                    if not (with_images and not builder.emitted_output and is_image_refusal(exc)):
                        raise
                    # The model takes no pictures. Sent once more without
                    # them, and without them from here on.
                    logger.warning("the model refused a request for its pictures; sending it without them: %s", exc)
                    self._images_refused()
                    close = getattr(iterator, "aclose", None)
                    iterator = None
                    if close is not None:
                        try:
                            await close()
                        except Exception:  # noqa: BLE001 - the refused request is over either way
                            logger.debug("closing the refused model stream failed", exc_info=True)
            for event in builder.finish():
                yield event
        except _Aborted:
            yield builder.fail("aborted", "Operation aborted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the loop as a failed response
            if not builder.emitted_output and is_context_overflow(exc):
                raise ContextOverflowError(str(exc)) from exc
            logger.warning("model request failed: %s", exc)
            yield builder.fail("error", f"{type(exc).__name__}: {exc}")
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            close = getattr(iterator, "aclose", None)
            if close is not None:
                # Closing the generator closes the HTTP stream underneath it.
                try:
                    await close()
                except Exception:  # noqa: BLE001 - best effort; the request is over either way
                    logger.debug("closing the model stream failed", exc_info=True)

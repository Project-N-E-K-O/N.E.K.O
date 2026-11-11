# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file tests/pi_event_helpers.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see nekoclaw/runtime/loop/LICENSE.
# Adapted to this repository: pytest-asyncio auto mode instead of anyio marks,
# imports point at nekoclaw.runtime.loop, and the fake provider lives in
# tests/unit/nekoclaw/loop_event_helpers.py.

"""Canonical Pi event constructors used by the test suite."""

from collections.abc import AsyncIterator, Iterable

from nekoclaw.runtime.loop.messages import AgentMessage, AssistantMessage, ThinkingContent, ToolCall
from nekoclaw.runtime.loop.provider import CancellationToken
from nekoclaw.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEndEvent,
)
from nekoclaw.runtime.loop.tools import AgentTool


class FakeProvider:
    """A provider that replays predefined assistant event streams.

    Ported from the same upstream commit, file src/tau_ai/fake.py: the upstream
    tests import it from the model layer, which this repository does not port.
    """

    def __init__(self, streams: Iterable[Iterable[AssistantMessageEvent]]) -> None:
        self._streams = [list(stream) for stream in streams]
        self.calls: list[tuple[str, str, list[AgentMessage], list[AgentTool]]] = []
        self.session_ids: list[str | None] = []

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
        self.calls.append((model, system, list(messages), list(tools)))
        self.session_ids.append(session_id)
        stream = self._streams.pop(0) if self._streams else []

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            for event in stream:
                if signal is not None and signal.is_cancelled():
                    return
                yield event

        return iterator()


def assistant_start(model: str = "fake") -> AssistantStartEvent:
    return AssistantStartEvent(partial=AssistantMessage(model=model))


def text_delta(delta: str) -> TextDeltaEvent:
    return TextDeltaEvent(content_index=0, delta=delta, partial=AssistantMessage(content=delta))


def thinking_delta(delta: str) -> ThinkingDeltaEvent:
    return ThinkingDeltaEvent(
        content_index=0,
        delta=delta,
        partial=AssistantMessage(content=[ThinkingContent(thinking=delta)]),
    )


def tool_call_end(tool_call: ToolCall) -> ToolCallEndEvent:
    return ToolCallEndEvent(
        content_index=0,
        tool_call=tool_call,
        partial=AssistantMessage(content=[tool_call]),
    )


def assistant_done(
    message: AssistantMessage | dict[str, object],
    finish_reason: str | None = None,
) -> AssistantDoneEvent:
    final = (
        message
        if isinstance(message, AssistantMessage)
        else AssistantMessage.model_validate(message)
    )
    if final.tool_calls or finish_reason in {"tool_calls", "tool_use", "toolUse"}:
        reason = "toolUse"
    elif finish_reason in {"length", "max_tokens", "MAX_TOKENS", "incomplete"}:
        reason = "length"
    else:
        reason = "stop"
    final.stop_reason = reason
    return AssistantDoneEvent(reason=reason, message=final)


def assistant_error(message: str, data: object = None) -> AssistantErrorEvent:
    del data
    error = AssistantMessage(stop_reason="error", error_message=message)
    return AssistantErrorEvent(reason="error", error=error)

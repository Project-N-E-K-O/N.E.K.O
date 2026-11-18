# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/provider_events.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Pi-compatible assistant stream events owned by the portable agent layer."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from nekoclaw.runtime.loop.messages import AssistantMessage, ToolCall, WireModel


class AssistantStartEvent(WireModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class TextStartEvent(WireModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(WireModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class TextEndEvent(WireModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ThinkingStartEvent(WireModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(WireModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(WireModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ToolCallStartEvent(WireModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(WireModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ToolCallEndEvent(WireModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


DoneReason = Literal["stop", "length", "toolUse"]
ErrorReason = Literal["aborted", "error"]


class AssistantDoneEvent(WireModel):
    type: Literal["done"] = "done"
    reason: DoneReason
    message: AssistantMessage


class AssistantErrorEvent(WireModel):
    type: Literal["error"] = "error"
    reason: ErrorReason
    error: AssistantMessage


AssistantMessageEvent = Annotated[
    AssistantStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | AssistantDoneEvent
    | AssistantErrorEvent,
    Field(discriminator="type"),
]

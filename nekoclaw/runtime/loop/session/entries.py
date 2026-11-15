# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/session/entries.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Append-only session entry models."""

from __future__ import annotations

from time import time
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from nekoclaw.runtime.loop.messages import AgentMessage, Usage, UserContent
from nekoclaw.runtime.loop.types import JSONValue


def new_entry_id() -> str:
    """Return a unique session entry id."""
    return uuid4().hex


def current_timestamp() -> float:
    """Return the current Unix timestamp."""
    return time()


class BaseSessionEntry(BaseModel):
    """Common fields shared by all append-only session entries."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_entry_id)
    parent_id: str | None = None
    timestamp: float = Field(default_factory=current_timestamp)


class MessageEntry(BaseSessionEntry):
    """A transcript message entry."""

    type: Literal["message"] = "message"
    message: AgentMessage


class CustomMessageEntry(BaseSessionEntry):
    """An extension-injected message that participates in model context."""

    type: Literal["custom_message"] = "custom_message"
    custom_type: str
    content: UserContent
    display: bool = True
    details: JSONValue = None


class ModelChangeEntry(BaseSessionEntry):
    """A model selection change entry.

    ``provider`` was added after Tau's first session format.  It remains
    optional so older transcripts continue to load; new entries always carry
    the provider selected by the host.
    """

    type: Literal["model_change"] = "model_change"
    model: str
    provider: str | None = None


class ThinkingLevelChangeEntry(BaseSessionEntry):
    """A thinking/reasoning level change entry."""

    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: str | None = None


class CompactionEntry(BaseSessionEntry):
    """A context summary that replaces older message entries during replay."""

    type: Literal["compaction"] = "compaction"
    summary: str
    # Legacy Tau sessions stored every replaced id. Keep accepting and replaying
    # that shape, but omit the empty compatibility field from new JSONL records.
    replaces_entry_ids: list[str] = Field(default_factory=list, exclude_if=lambda ids: not ids)
    first_kept_entry_id: str | None = None
    tokens_before: int | None = None
    usage: Usage | None = None
    provider: str | None = None
    model: str | None = None
    response_provider: str | None = None


class BranchSummaryEntry(BaseSessionEntry):
    """A future branch summary entry."""

    type: Literal["branch_summary"] = "branch_summary"
    summary: str
    branch_root_id: str | None = None
    usage: Usage | None = None
    provider: str | None = None
    model: str | None = None
    response_provider: str | None = None


class LabelEntry(BaseSessionEntry):
    """A bookmark change attached to one session entry."""

    type: Literal["label"] = "label"
    target_id: str
    label: str | None = None


class LeafEntry(BaseSessionEntry):
    """A legacy active-tip pointer retained only for deserialization."""

    type: Literal["leaf"] = "leaf"
    entry_id: str | None = None


class SessionInfoEntry(BaseSessionEntry):
    """Basic session metadata entry."""

    type: Literal["session_info"] = "session_info"
    created_at: float = Field(default_factory=current_timestamp)
    cwd: str | None = None
    title: str | None = None


class CustomEntry(BaseSessionEntry):
    """Extension/application-owned session data."""

    type: Literal["custom"] = "custom"
    namespace: str
    data: dict[str, JSONValue] = Field(default_factory=dict)


SessionEntry = Annotated[
    MessageEntry
    | CustomMessageEntry
    | ModelChangeEntry
    | ThinkingLevelChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | LabelEntry
    | LeafEntry
    | SessionInfoEntry
    | CustomEntry,
    Field(discriminator="type"),
]

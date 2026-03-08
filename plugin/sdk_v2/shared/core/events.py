"""Event contracts for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared.constants import EVENT_META_ATTR
from .types import InputSchema, Metadata


@dataclass(slots=True)
class EventMeta:
    event_type: str
    id: str
    name: str = ""
    description: str = ""
    input_schema: InputSchema | None = None
    auto_start: bool = False
    metadata: Metadata | None = None
    extra: Metadata | None = None


@dataclass(slots=True)
class EventHandler:
    meta: EventMeta
    handler: object


__all__ = ["EVENT_META_ATTR", "EventMeta", "EventHandler"]

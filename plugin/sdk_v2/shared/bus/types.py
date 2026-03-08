"""Bus contract models for SDK v2 shared bus."""

from __future__ import annotations

from dataclasses import dataclass, field

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue


@dataclass(slots=True)
class BusConversation:
    id: str
    topic: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class BusMessage:
    id: str
    conversation_id: str
    role: str
    content: JsonValue
    timestamp: float | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class BusEvent:
    id: str
    event_type: str
    payload: JsonObject = field(default_factory=dict)
    timestamp: float | None = None


@dataclass(slots=True)
class BusRecord:
    id: str
    namespace: str
    payload: JsonObject = field(default_factory=dict)
    rev: int = 0


__all__ = ["BusConversation", "BusMessage", "BusEvent", "BusRecord"]

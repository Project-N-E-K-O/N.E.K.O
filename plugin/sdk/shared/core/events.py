"""Event contracts for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass, field

from plugin.sdk.shared.constants import EVENT_META_ATTR
from .types import InputSchema, JsonValue


@dataclass(slots=True)
class EventMeta:
    event_type: str
    id: str
    name: object = ""
    description: object = ""
    input_schema: InputSchema | None = None
    kind: str = "action"
    auto_start: bool = False
    persist: bool | None = None
    params: type | None = None
    model_validate: bool = True
    timeout: float | None = None
    llm_result_fields: list[str] | None = None
    llm_result_schema: InputSchema | None = None
    llm_result_model: type | None = None
    extra: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class EventHandler:
    meta: EventMeta
    handler: object


__all__ = ["EVENT_META_ATTR", "EventMeta", "EventHandler"]

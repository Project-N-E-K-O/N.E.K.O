"""Event contracts for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass, field

from plugin.sdk.shared.constants import EVENT_META_ATTR
from .types import InputSchema, JsonValue


@dataclass(slots=True)
class QuickActionConfig:
    """Quick-action configuration (typed, rather than an unstructured dict)."""
    icon: str | None = None
    priority: int = 0


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
    quick_action: bool = False
    quick_action_config: QuickActionConfig = field(default_factory=QuickActionConfig)
    extra: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    # Appended last so positional construction keeps working.
    # Output contract, independent of the AI-visible llm_result_* fields and
    # symmetric with inputs: ``output_schema`` is published; ``output_model``
    # (inferred from a Pydantic return annotation) is what returned data is
    # validated with at runtime, like ``params``. An explicit
    # ``output_schema`` alone is published only. ``strict_output`` turns a
    # mismatch into an execution failure instead of a warning.
    output_schema: InputSchema | None = None
    output_model: type | None = None
    strict_output: bool = False


@dataclass(slots=True)
class EventHandler:
    meta: EventMeta
    handler: object


__all__ = ["EVENT_META_ATTR", "EventMeta", "EventHandler", "QuickActionConfig"]

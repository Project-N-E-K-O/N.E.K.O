"""
Plugin event-system module.

Defines event metadata and event handlers.
"""
from dataclasses import dataclass
from typing import Dict, Any, Callable, Literal, get_args

# Event metadata attribute name, used to mark event handlers.
EVENT_META_ATTR = "__neko_event_meta__"

# Standard event types.
StandardEventType = Literal[
    "plugin_entry",  # Public callable plugin_entry entry points are implemented.
    "lifecycle",     # Lifecycle events (on_startup / on_shutdown).
    "message",       # Future message events (e.g. on_message).
    "timer",         # Future scheduled events.
]

# Standard event-type set, automatically synchronized with StandardEventType.
STANDARD_EVENT_TYPES: tuple[str, ...] = get_args(StandardEventType)

# Event type: standard type or custom string.
EventType = str

@dataclass
class EventMeta:
    """Event metadata."""
    event_type: EventType  # Support standard types and custom strings.
    id: str                     # Event ID within this plugin, e.g. "open" / "close" / "startup".
    name: str                   # Display name.
    description: str = ""
    input_schema: Dict[str, Any] | None = None

    # Fields below are mainly for plugin_entry / lifecycle.
    kind: Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer"] = "action"
    auto_start: bool = False    # Available for event_type == "lifecycle" or "plugin_entry".
    # Dynamic-entry support.
    enabled: bool = True        # Enabled by default.
    dynamic: bool = False       # Whether this entry was created dynamically.
    # Reserve additional fields for future extension.
    metadata: Dict[str, Any] | None = None
    
    def is_custom_event(self) -> bool:
        """Determine whether an event type is custom."""
        return self.event_type not in STANDARD_EVENT_TYPES


@dataclass
class EventHandler:
    """Event handler."""
    meta: EventMeta
    handler: Callable  # Concrete function/method to call.


__all__ = [
    "EVENT_META_ATTR",
    "StandardEventType",
    "EventType",
    "EventMeta",
    "EventHandler",
]

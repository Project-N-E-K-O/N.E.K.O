from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plugin.sdk_v2.shared.bus.types import BusConversation, BusEvent, BusMessage, BusRecord
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Ok, Result


class _InMemoryBusTransport:
    async def request(self, channel: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        return Err(RuntimeError(f"bus request is not available: {channel}"))

    async def publish(self, channel: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        return Ok(None)


@dataclass(slots=True)
class _Watcher:
    id: str
    channel: str
    handler: Any
    queue: list[BusEvent] = field(default_factory=list)


@dataclass(slots=True)
class _BusState:
    conversations: dict[str, BusConversation] = field(default_factory=dict)
    messages: dict[str, BusMessage] = field(default_factory=dict)
    events: list[BusEvent] = field(default_factory=list)
    records: dict[tuple[str, str], BusRecord] = field(default_factory=dict)
    revisions: dict[tuple[str, str], int] = field(default_factory=dict)
    memory: dict[str, list[JsonObject]] = field(default_factory=dict)
    watchers: dict[str, _Watcher] = field(default_factory=dict)
    next_ids: dict[str, int] = field(default_factory=lambda: {"conversation": 0, "message": 0, "event": 0, "watcher": 0})

    def next_id(self, namespace: str) -> str:
        current = self.next_ids.get(namespace, 0) + 1
        self.next_ids[namespace] = current
        return f"{namespace}:{current}"


def ensure_transport(transport: Any | None):
    return transport if transport is not None else _InMemoryBusTransport()


def ensure_state(transport: Any) -> _BusState:
    state = getattr(transport, "_sdk_v2_bus_state", None)
    if state is None:
        state = _BusState()
        try:
            setattr(transport, "_sdk_v2_bus_state", state)
        except Exception:
            pass
    return state


__all__ = ["_BusState", "_Watcher", "ensure_transport", "ensure_state"]

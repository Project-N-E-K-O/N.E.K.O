"""Shared event types for Douyin live transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .event_model import safe_payload
from .public_projection import safe_public_float, safe_public_text, safe_room_ref


@dataclass(frozen=True, slots=True)
class DouyinTransportEvent:
    """Already-decoded provider event emitted by the transport."""

    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def safe_payload(self) -> dict[str, Any]:
        return safe_payload(self.payload)


def safe_transport_event_time(value: Any) -> float:
    return safe_public_float(value)


@dataclass(frozen=True, slots=True)
class DouyinTransportState:
    """Private bridge state reduced before module public projection."""

    state: str = "unsupported"
    last_error: str = ""
    last_event_at: float = 0.0
    last_event_type: str = ""

    def safe_state(self) -> str:
        value = self.state if isinstance(self.state, str) else ""
        text = value.strip().lower()
        allowed = {"disconnected", "connecting", "connected", "receiving", "reconnecting", "unsupported"}
        return text if text in allowed else "unknown"

    def safe_error(self) -> str:
        return safe_public_text(self.last_error, limit=160)


@dataclass(frozen=True, slots=True)
class DouyinTransportStartRequest:
    """Private inputs used by the connector without exposing them publicly."""

    room_ref: str
    cookie: str = field(repr=False)
    connection_plan: Any
    emit: Callable[[DouyinTransportEvent], Any] | None = None
    on_state: Callable[[DouyinTransportState], Any] | None = None

    def safe_room_ref(self) -> str:
        return safe_room_ref(self.room_ref)

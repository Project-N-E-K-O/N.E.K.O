"""Safety and live-room status contracts for NEKO Live integrations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts_types import SafetyStatus


@dataclass
class SafetyDecision:
    allowed: bool
    status: SafetyStatus = "running"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveRoomStatus:
    room_id: int
    ok: bool
    title: str = ""
    anchor_name: str = ""
    live_status: str = "unknown"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

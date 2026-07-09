"""Public bridge connection projection for Douyin live ingest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .public_projection import safe_public_bool, safe_public_text, safe_room_ref, safe_webcast_room_id

_PUBLIC_MISSING = {"bridge_executable", "bridge_runtime"}


@dataclass(frozen=True, slots=True)
class DouyinBridgeConnectionPlan:
    """Safe public status for the bundled local Douyin bridge path."""

    ready: bool = False
    room_ref: str = ""
    webcast_room_id: str = ""
    missing: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ready": safe_public_bool(self.ready),
            "room_ref": safe_room_ref(self.room_ref),
            "webcast_room_id": safe_webcast_room_id(self.webcast_room_id),
            "endpoint": "",
            "params": {},
            "missing": [item for item in self.missing if item in _PUBLIC_MISSING],
            "message": safe_public_text(self.message, limit=160),
        }

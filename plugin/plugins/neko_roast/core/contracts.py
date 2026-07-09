"""Compatibility facade for NEKO Live shared contracts.

Concrete contract groups live in focused ``contracts_*`` modules. Keep importing
from ``core.contracts`` in feature code unless a module needs a narrower owner.
"""

from __future__ import annotations

from .contracts_config import RoastConfig, normalize_live_platform, parse_room_id
from .contracts_events import LiveEvent, ViewerEvent
from .contracts_interaction import InteractionRequest, InteractionResult, PipelineStep
from .contracts_safety import LiveRoomStatus, SafetyDecision
from .contracts_types import (
    ActivityLevel,
    LiveMode,
    RoastStrength,
    SafetyStatus,
    TriggerSource,
    _response_latency_ms,
    utc_now_iso,
)
from .contracts_viewer import ViewerIdentity, ViewerProfile

__all__ = [
    "ActivityLevel",
    "InteractionRequest",
    "InteractionResult",
    "LiveEvent",
    "LiveMode",
    "LiveRoomStatus",
    "PipelineStep",
    "RoastConfig",
    "RoastStrength",
    "SafetyDecision",
    "SafetyStatus",
    "TriggerSource",
    "ViewerEvent",
    "ViewerIdentity",
    "ViewerProfile",
    "_response_latency_ms",
    "normalize_live_platform",
    "parse_room_id",
    "utc_now_iso",
]

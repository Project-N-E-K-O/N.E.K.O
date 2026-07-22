"""Compatibility facade for NEKO Live shared contracts.

Concrete contract groups live in focused ``contracts_*`` modules. Keep importing
from ``core.contracts`` in feature code unless a module needs a narrower owner.
"""

from __future__ import annotations

from .contracts_config import LiveConfig, normalize_live_platform, parse_room_id
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
from .co_stream_capabilities import CoStreamCapability
from .host_turn import HostTurnSignal
from .live_interaction_policy import LiveInteractionCandidate, LiveInteractionDecision

__all__ = [
    "ActivityLevel",
    "CoStreamCapability",
    "InteractionRequest",
    "InteractionResult",
    "HostTurnSignal",
    "LiveEvent",
    "LiveInteractionCandidate",
    "LiveInteractionDecision",
    "LiveMode",
    "LiveRoomStatus",
    "PipelineStep",
    "LiveConfig",
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

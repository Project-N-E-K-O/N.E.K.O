"""Read-only runtime wiring for co-stream participation policy."""

from __future__ import annotations

import math
from typing import Any

from .co_stream_capabilities import (
    CoStreamCapability,
    auto_enforcement_confirmed,
    configured_activation,
    registered_co_stream_capabilities,
    requested_activation,
)
from .host_turn import HostTurnSignal, HostTurnSignalStore
from .live_interaction_policy import LiveInteractionCandidate, LiveInteractionPolicy


def initialize_co_stream_policy(runtime: Any) -> None:
    """Attach read-only collaborators without producing or scheduling output."""

    runtime.host_turn_signal_provider = HostTurnSignalStore()
    runtime.live_interaction_policy = LiveInteractionPolicy()


def co_stream_participation_snapshot(runtime: Any) -> dict[str, Any]:
    """Project bounded policy decisions without dispatching or scheduling."""

    live_mode = getattr(runtime.config, "live_mode", "co_stream")
    if live_mode not in {"co_stream", "solo_stream"}:
        live_mode = "co_stream"
    host_turn = runtime.host_turn_signal_provider.current()
    capabilities = [
        _capability_snapshot(runtime, capability, live_mode, host_turn)
        for capability in registered_co_stream_capabilities()
    ]
    return {
        "live_mode": live_mode,
        "read_only": True,
        "enforced": False,
        "host_turn": _host_turn_snapshot(host_turn),
        "capabilities": capabilities,
    }


def _capability_snapshot(
    runtime: Any,
    capability: CoStreamCapability,
    live_mode: str,
    host_turn: HostTurnSignal,
) -> dict[str, Any]:
    requested = requested_activation(runtime.config, capability)
    activation = configured_activation(runtime.config, capability)
    decision = runtime.live_interaction_policy.decide(
        LiveInteractionCandidate(
            live_mode=live_mode,  # type: ignore[arg-type]
            capability_id=capability.id,
            activation=activation,
            requested_level=capability.requested_level,
            priority=capability.priority,
        ),
        host_turn,
    )
    return {
        "id": capability.id,
        "activation": requested,
        "effective_activation": activation,
        "auto_enforcement_confirmed": auto_enforcement_confirmed(
            runtime.config,
            capability,
        ),
        "requested_level": capability.requested_level,
        "effective_level": decision.participation_level,
        "priority": decision.priority,
        "decision": decision.decision,
        "reason_code": decision.reason_code,
    }


def _host_turn_snapshot(signal: HostTurnSignal) -> dict[str, Any]:
    confidence = signal.confidence
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
    ):
        confidence = 0.0
    return {
        "state": signal.state
        if signal.state in {"speaking", "likely_holding", "yielded", "unknown"}
        else "unknown",
        "reliability": signal.reliability
        if signal.reliability in {"reliable", "degraded", "unavailable"}
        else "unavailable",
        "confidence": max(0.0, min(1.0, float(confidence))),
        "source": signal.source
        if signal.source in {"host_runtime", "platform", "fallback"}
        else "fallback",
    }


__all__ = [
    "co_stream_participation_snapshot",
    "initialize_co_stream_policy",
]

"""Resolved transport and endpoint policy for one independent ASR session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ._registry_meta import ASR_PROVIDER_REGISTRY, AsrEndpointingMode


AsrTransport = Literal["streaming", "segmented"]
AsrEndpointAuthority = Literal["provider", "smart_turn"]
AsrReplayPolicy = Literal["none", "preconnect_only", "provider_managed"]


@dataclass(frozen=True, slots=True)
class AsrProviderPolicy:
    transport: AsrTransport
    endpoint_authority: AsrEndpointAuthority
    smart_turn_required: bool
    max_segment_ms: int | None
    warm_transport_ms: int
    replay_policy: AsrReplayPolicy
    provider_final_timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        if self.max_segment_ms is not None and self.max_segment_ms <= 0:
            raise ValueError("max_segment_ms must be positive")
        if self.warm_transport_ms < 0:
            raise ValueError("warm_transport_ms must not be negative")
        if self.transport == "segmented" and not self.smart_turn_required:
            raise ValueError("segmented ASR must require SmartTurn")
        if self.provider_final_timeout_ms <= 0:
            raise ValueError("provider_final_timeout_ms must be positive")


def resolve_provider_policy(
    provider_key: str,
    endpointing_mode: AsrEndpointingMode,
) -> AsrProviderPolicy:
    normalized_provider = str(provider_key or "").strip().lower()
    meta = ASR_PROVIDER_REGISTRY.get(normalized_provider)
    if meta is None:
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {normalized_provider}")
    if endpointing_mode not in meta.supported_endpointing_modes:
        raise RuntimeError(
            "ASR_ENDPOINTING_NOT_SUPPORTED: "
            f"{normalized_provider} does not support {endpointing_mode}"
        )

    transport: AsrTransport = (
        "segmented" if meta.category in {"dummy", "segmented_request"} else "streaming"
    )
    # Provider-native endpoints are physical-segment hints only. SmartTurn is
    # the sole logical-turn authority for every independent ASR route.
    endpoint_authority: AsrEndpointAuthority = "smart_turn"
    smart_turn_required = True
    return AsrProviderPolicy(
        transport=transport,
        endpoint_authority=endpoint_authority,
        smart_turn_required=smart_turn_required,
        max_segment_ms=meta.max_segment_ms if transport == "segmented" else None,
        warm_transport_ms=meta.warm_transport_ms if transport == "streaming" else 0,
        replay_policy=meta.replay_policy,
    )

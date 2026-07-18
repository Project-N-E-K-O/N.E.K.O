"""Static capability registration for opt-in co-stream participation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .live_interaction_policy import ActivationMode, ParticipationLevel


@dataclass(frozen=True)
class CoStreamCapability:
    """Describe one policy candidate without coupling it to runtime output."""

    id: str
    config_key: str
    requested_level: ParticipationLevel
    priority: int
    default_activation: ActivationMode = "off"
    auto_consent_key: str = ""
    required_auto_consent_version: int = 0


_CAPABILITIES = (
    CoStreamCapability(
        id="host_pause_fill",
        config_key="co_stream_host_pause_fill_activation",
        requested_level="L3",
        priority=5,
        auto_consent_key="co_stream_host_pause_fill_auto_consent_version",
        required_auto_consent_version=1,
    ),
)


def registered_co_stream_capabilities() -> tuple[CoStreamCapability, ...]:
    """Return the stable, ordered capability catalog."""

    return _CAPABILITIES


def normalize_activation_mode(
    value: Any,
    *,
    default: ActivationMode = "off",
) -> ActivationMode:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized in {"off", "conditional_auto"}:
        return normalized  # type: ignore[return-value]
    return default


def configured_activation(config: Any, capability: CoStreamCapability) -> ActivationMode:
    activation = normalize_activation_mode(
        getattr(config, capability.config_key, capability.default_activation),
        default=capability.default_activation,
    )
    if activation != "conditional_auto":
        return activation
    if not capability.auto_consent_key or capability.required_auto_consent_version <= 0:
        return "off"
    raw_version = getattr(config, capability.auto_consent_key, 0)
    if isinstance(raw_version, bool):
        return "off"
    if isinstance(raw_version, int):
        consent_version = raw_version
    elif isinstance(raw_version, str) and raw_version.strip().isdigit():
        consent_version = int(raw_version.strip())
    else:
        return "off"
    return activation if consent_version == capability.required_auto_consent_version else "off"


def requested_activation(config: Any, capability: CoStreamCapability) -> ActivationMode:
    """Return the saved choice without treating a preview choice as consent."""

    return normalize_activation_mode(
        getattr(config, capability.config_key, capability.default_activation),
        default=capability.default_activation,
    )


def auto_enforcement_confirmed(config: Any, capability: CoStreamCapability) -> bool:
    return requested_activation(config, capability) == configured_activation(config, capability) == "conditional_auto"


__all__ = [
    "CoStreamCapability",
    "auto_enforcement_confirmed",
    "configured_activation",
    "normalize_activation_mode",
    "registered_co_stream_capabilities",
    "requested_activation",
]

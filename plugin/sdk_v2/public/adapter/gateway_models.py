"""Compatibility alias for adapter gateway models."""

from __future__ import annotations

from plugin.sdk_v2.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
    RouteMode,
)

__all__ = [
    "ExternalEnvelope",
    "GatewayAction",
    "GatewayRequest",
    "GatewayError",
    "GatewayErrorException",
    "GatewayResponse",
    "RouteDecision",
    "RouteMode",
]

"""Compatibility alias for adapter gateway protocols."""

from __future__ import annotations

from plugin.sdk_v2.adapter.gateway_contracts import (
    LoggerLike,
    PluginInvoker,
    PolicyEngine,
    RequestNormalizer,
    ResponseSerializer,
    RouteEngine,
    TransportAdapter,
)

__all__ = [
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
]

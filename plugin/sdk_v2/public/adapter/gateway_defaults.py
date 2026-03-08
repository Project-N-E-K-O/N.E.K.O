"""Compatibility alias for adapter gateway default components."""

from __future__ import annotations

from plugin.sdk_v2.adapter.gateway_defaults import (
    CallablePluginInvoker,
    DefaultPolicyEngine,
    DefaultRequestNormalizer,
    DefaultResponseSerializer,
    DefaultRouteEngine,
)

__all__ = [
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
]

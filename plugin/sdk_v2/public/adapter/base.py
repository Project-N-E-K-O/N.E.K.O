"""Compatibility alias for adapter base contracts."""

from __future__ import annotations

from plugin.sdk_v2.adapter.base import AdapterBase, AdapterConfig, AdapterContext, AdapterMode

__all__ = ["AdapterMode", "AdapterConfig", "AdapterContext", "AdapterBase"]

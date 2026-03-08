"""Compatibility alias for adapter decorators."""

from __future__ import annotations

from plugin.sdk_v2.adapter.decorators import (
    ADAPTER_EVENT_META,
    ADAPTER_LIFECYCLE_META,
    AdapterEventMeta,
    on_adapter_event,
    on_adapter_shutdown,
    on_adapter_startup,
    on_mcp_resource,
    on_mcp_tool,
    on_nonebot_message,
)

__all__ = [
    "ADAPTER_EVENT_META",
    "ADAPTER_LIFECYCLE_META",
    "AdapterEventMeta",
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
    "on_mcp_tool",
    "on_mcp_resource",
    "on_nonebot_message",
]

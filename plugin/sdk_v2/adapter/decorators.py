"""Adapter decorators for SDK v2."""

from plugin.sdk_v2.public.adapter.decorators import (
    ADAPTER_EVENT_META,
    ADAPTER_LIFECYCLE_META,
    AdapterEventMeta,
    _not_impl,
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
    "_not_impl",
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
    "on_mcp_tool",
    "on_mcp_resource",
    "on_nonebot_message",
]

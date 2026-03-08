"""SDK v2 extension surface (contract-only)."""

from .base import ExtensionMeta, NekoExtensionBase
from .decorators import extension_entry, extension_hook
from .runtime import (
    ExtensionRuntime,
    MessagePlaneTransport,
    PluginConfig,
    PluginRouter,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)

__all__ = [
    "ExtensionMeta",
    "NekoExtensionBase",
    "extension_entry",
    "extension_hook",
    "ExtensionRuntime",
    "PluginConfig",
    "PluginRouter",
    "MessagePlaneTransport",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]

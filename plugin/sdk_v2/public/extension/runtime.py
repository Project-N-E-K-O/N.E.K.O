"""Internal extension runtime building blocks.

The extension internal surface intentionally stays narrower than the plugin
internal runtime surface.
"""

from plugin.sdk_v2.shared.core.config import ConfigPathError, ConfigValidationError, PluginConfig, PluginConfigError
from plugin.sdk_v2.shared.core.router import EntryConflictError, PluginRouter, PluginRouterError, RouteHandler
from plugin.sdk_v2.shared.runtime.call_chain import (
    AsyncCallChain,
    CallChain,
    CallChainTooDeepError,
    CircularCallError,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)
from plugin.sdk_v2.shared.transport.message_plane import MessagePlaneTransport

__all__ = [
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigValidationError",
    "PluginRouter",
    "PluginRouterError",
    "EntryConflictError",
    "RouteHandler",
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
    "MessagePlaneTransport",
]

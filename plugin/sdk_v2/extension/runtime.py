"""Extension runtime contracts for SDK v2.

This runtime surface stays narrower than `plugin.runtime`, but follows the same
layout: common SDK-wide runtime exports first, extension-specific contracts
second.
"""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared import runtime_common as _common_runtime
from plugin.sdk_v2.shared.core.config import (
    ConfigPathError,
    ConfigProfileError,
    ConfigValidationError,
    PluginConfig,
    PluginConfigBaseView,
    PluginConfigError,
    PluginConfigProfiles,
)
from plugin.sdk_v2.shared.core.router import EntryConflictError, PluginRouter, PluginRouterError, RouteHandler
from plugin.sdk_v2.shared.logging import get_extension_logger
from plugin.sdk_v2.shared.transport.message_plane import MessagePlaneTransport

for _name in _common_runtime.__all__:
    globals()[_name] = getattr(_common_runtime, _name)

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
EXTENSION_RUNTIME_EXPORTS = [
    "get_extension_logger",
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "ConfigValidationError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "PluginRouter",
    "PluginRouterError",
    "EntryConflictError",
    "RouteHandler",
    "MessagePlaneTransport",
    "ExtensionRuntime",
]


@dataclass(slots=True)
class ExtensionRuntime:
    config: PluginConfig
    router: PluginRouter
    transport: MessagePlaneTransport

    async def health(self) -> Result[dict[str, str], Exception]:
        raise NotImplementedError("sdk_v2 contract-only facade: extension.runtime not implemented")


__all__ = [*COMMON_RUNTIME_EXPORTS, *EXTENSION_RUNTIME_EXPORTS]

del _name

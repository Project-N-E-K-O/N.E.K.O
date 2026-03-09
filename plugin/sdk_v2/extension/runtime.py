"""Extension runtime contracts for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared import runtime_common as _common_runtime
from plugin.sdk_v2.shared.core.config import (  # noqa: F401
    ConfigPathError,
    ConfigProfileError,
    ConfigValidationError,
    PluginConfig,
    PluginConfigBaseView,
    PluginConfigError,
    PluginConfigProfiles,
)
from plugin.sdk_v2.shared.core.router import EntryConflictError, PluginRouter, PluginRouterError, RouteHandler  # noqa: F401
from plugin.sdk_v2.shared.logging import get_extension_logger  # noqa: F401
from plugin.sdk_v2.shared.models import Ok, Result
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
    "ConfigValidationError",
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
        return Ok({"status": "ok", "router": self.router.name(), "transport": self.transport.__class__.__name__})


__all__ = [*COMMON_RUNTIME_EXPORTS, *EXTENSION_RUNTIME_EXPORTS]

del _name

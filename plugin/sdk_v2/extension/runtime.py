"""Extension runtime contracts for SDK v2.

This module intentionally exposes a smaller runtime surface than full plugins.
The facade stays local to `extension`, while shared implementations remain the
single lower dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared.core.config import PluginConfig
from plugin.sdk_v2.shared.core.router import PluginRouter
from plugin.sdk_v2.shared.models import Result
from plugin.sdk_v2.shared.runtime.call_chain import get_call_chain, get_call_depth, is_in_call_chain
from plugin.sdk_v2.shared.transport.message_plane import MessagePlaneTransport


@dataclass(slots=True)
class ExtensionRuntime:
    config: PluginConfig
    router: PluginRouter
    transport: MessagePlaneTransport

    async def health(self) -> Result[dict[str, str], Exception]:
        raise NotImplementedError("sdk_v2 contract-only facade: extension.runtime not implemented")


__all__ = [
    "ExtensionRuntime",
    "PluginConfig",
    "PluginRouter",
    "MessagePlaneTransport",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]

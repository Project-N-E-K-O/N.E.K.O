"""OneBot connector — plugin-agnostic transport library.

This package owns the transport layer: the OneBot v11 WebSocket client (forward
+ reverse), the QQ Open Platform WS gateway, the NapCat process manager, and the
send/normalize surface. Message enrichment (reply/forward/voice/file + VLM/STT)
lives in the plugin. It is imported by plugins and instantiated in-process; it
never imports a plugin.

``create_onebot_connection`` (from :mod:`utils.connection.onebot.factory`) is the single
entry point that builds the right concrete connection from transport settings.
"""

from __future__ import annotations

from .factory import OneBotConnector, create_onebot_connection
from .onebot_client import OneBotClient
from .onebot_connection import OneBotConnectionBase
from .qq_open_plat import QQOpenPlatformConnection

__all__ = [
    "create_onebot_connection",
    "OneBotConnector",
    "OneBotConnectionBase",
    "OneBotClient",
    "QQOpenPlatformConnection",
]

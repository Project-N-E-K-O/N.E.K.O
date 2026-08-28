"""QQ connector — plugin-agnostic transport library.

This package owns the QQ *transport*: OneBot v11 WebSocket client (forward +
reverse), the QQ Open Platform WS gateway, the NapCat process manager, the
message-chain model hierarchy, and the reference/forward/voice/file enrichment
pipeline with VLM/STT describer mounting. It is imported by plugins and
instantiated in-process; it never imports a plugin.

``create_qq_connection`` (from :mod:`utils.connection.qq.factory`) is the single
entry point that builds the right concrete connection from transport settings.
"""

from __future__ import annotations

from .factory import QQConnector, create_qq_connection
from .message_chain import (
    At,
    Emoji,
    File,
    Forward,
    Image,
    JsonCard,
    MessageChain,
    Notice,
    Poke,
    Record,
    Reply,
    Sticker,
    Text,
)
from .napcat_service import QQNapcatService
from .qq_client import QQClient
from .qq_connection import QQConnectionBase
from .qq_open_plat import QQOpenPlatformConnection

__all__ = [
    "create_qq_connection",
    "QQConnector",
    "QQConnectionBase",
    "QQClient",
    "QQOpenPlatformConnection",
    "QQNapcatService",
    "MessageChain",
    "Text",
    "Image",
    "At",
    "Reply",
    "Forward",
    "Emoji",
    "Sticker",
    "Record",
    "Notice",
    "Poke",
    "File",
    "JsonCard",
]

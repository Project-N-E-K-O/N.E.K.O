"""QQ-specific connectors.

- :class:`QQOpenPlatformConnection`: the official QQ Open Platform Bot API
  connection (REST + WS gateway). Not OneBot.
- :func:`create_qq_connection`: builds either that or a
  :class:`utils.connection.onebot.OneBotClient` (NapCat and other OneBot v11
  implementations) from the QQ settings keys.
"""

from __future__ import annotations

from .factory import create_qq_connection
from .open_platform import QQOpenPlatformConnection

__all__ = [
    "create_qq_connection",
    "QQOpenPlatformConnection",
]

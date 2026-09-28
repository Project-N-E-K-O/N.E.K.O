"""Compatibility module: the pre-split home of the factory and the Protocol.

``create_onebot_connection`` is :func:`utils.connection.qq.create_qq_connection`
and ``OneBotConnector`` is :class:`utils.connection.base.ChatConnector` (the same
objects, not copies). Kept so existing imports and the qq_auto_reply plugin's
connector lookup keep working; new code should import from those modules.
"""

from __future__ import annotations

from ..base import ChatConnector as OneBotConnector
from ..qq.factory import create_qq_connection as create_onebot_connection

__all__ = [
    "create_onebot_connection",
    "OneBotConnector",
]

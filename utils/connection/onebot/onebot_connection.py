"""Compatibility module: the pre-split home of the connection base class.

``OneBotConnectionBase`` is :class:`utils.connection.base.ConnectionBase` (the
same object). Kept so existing imports keep working; new code should import
from :mod:`utils.connection.base`.
"""

from __future__ import annotations

from ..base import ConnectionBase as OneBotConnectionBase

__all__ = [
    "OneBotConnectionBase",
]

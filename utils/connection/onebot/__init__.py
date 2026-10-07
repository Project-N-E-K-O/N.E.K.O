"""OneBot v11 connector — plugin-agnostic transport library.

This package owns the OneBot v11 transport: the WebSocket client (forward +
reverse), echo-correlated actions, and normalization of v11 events into
:class:`utils.connection.base.InboundMessage`. NapCat / go-cqhttp extension
actions live in :mod:`.napcat_actions`. It does not manage the NapCat process,
and message enrichment (reply/forward/voice/file + VLM/STT) lives in the
consuming plugin. It is imported by plugins and instantiated in-process; it
never imports a plugin.

Inbound messages are pulled, not pushed: the consumer drives a loop over
``receive_message()``. A sink registered with ``set_inbound_sink()`` is called
from inside ``receive_message()``, so it only fires while that loop runs.

Layout of :mod:`utils.connection`:

- :mod:`utils.connection.base` — platform-neutral base class, message shape,
  connector Protocol;
- :mod:`utils.connection.onebot` — this package (OneBot v11);
- :mod:`utils.connection.qq` — QQ Open Platform connection and the factory that
  reads the QQ settings keys.

Compatibility: the names this package exported before that split
(``OneBotConnectionBase``, ``OneBotConnector``, ``QQOpenPlatformConnection``,
``create_onebot_connection``, and the ``factory`` / ``onebot_connection`` /
``qq_open_plat`` submodules) are still available here and are the same objects
as their new homes. The qq_auto_reply plugin looks the connector up through
exactly these names, so they must stay. The QQ-side ones are resolved lazily:
:mod:`utils.connection.qq` imports this package, so importing it eagerly here
would be circular.
"""

from __future__ import annotations

import importlib
from typing import Any

from ..base import ChatConnector as OneBotConnector
from ..base import ConnectionBase as OneBotConnectionBase
from . import onebot_client, onebot_connection
from .napcat_actions import NapCatActionsMixin
from .onebot_client import OneBotClient

__all__ = [
    "create_onebot_connection",
    "NapCatActionsMixin",
    "OneBotConnector",
    "OneBotConnectionBase",
    "OneBotClient",
    "QQOpenPlatformConnection",
]


def __getattr__(name: str) -> Any:
    # Lazy compat names (PEP 562). ``hasattr`` goes through here too, which is
    # how the plugin's connector lookup sees them.
    if name == "create_onebot_connection":
        from ..qq.factory import create_qq_connection

        return create_qq_connection
    if name == "QQOpenPlatformConnection":
        from ..qq.open_platform import QQOpenPlatformConnection

        return QQOpenPlatformConnection
    if name == "factory":
        # Not ``from . import factory``: that probes ``hasattr(package, "factory")``
        # first, which lands back in this ``__getattr__`` and recurses.
        return importlib.import_module(f"{__name__}.factory")
    if name == "qq_open_plat":
        from ..qq import open_platform

        return open_platform
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

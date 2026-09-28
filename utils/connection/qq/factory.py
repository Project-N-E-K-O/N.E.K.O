"""Factory for QQ connections: reads the QQ settings keys and builds the transport.

This is the only place that knows the QQ settings schema (``qq_connection_mode``,
``onebot_url``, ``token``, ``qq_open_*``). The same function is also exported as
``utils.connection.onebot.create_onebot_connection`` for existing callers.
"""

from __future__ import annotations

from typing import Any

from ..base import ConnectionBase
from ..onebot.onebot_client import OneBotClient
from .open_platform import QQOpenPlatformConnection


def create_qq_connection(
    settings_or_reader: Any,
    *,
    logger: Any = None,
    emit_log: Any = None,
) -> ConnectionBase:
    """Build the concrete QQ connection from the QQ transport settings.

    ``qq_connection_mode`` picks the transport: ``open_platform`` builds a
    :class:`QQOpenPlatformConnection`; ``napcat_forward`` builds a forward-WS
    :class:`OneBotClient`; anything else (including the default ``napcat``)
    builds a reverse-WS :class:`OneBotClient`.

    ``settings_or_reader`` is either a settings dict or a zero-arg callable that
    returns the live settings dict. Passing a callable keeps the open-platform
    ``identity_probe`` reading live settings per event (a toggle flip takes effect
    without a reconnect), matching the plugin's historical behavior. The sandbox
    toggle is read the same way but sampled once per ``connect()``, so changing it
    takes effect on the next reconnect.

    Everything else (mode, credentials, ``onebot_url``, ``token``) is read once
    here; changing it means building a new connection.
    """
    get_settings = settings_or_reader if callable(settings_or_reader) else (lambda: settings_or_reader)
    settings = get_settings() or {}
    mode = str(settings.get("qq_connection_mode", "napcat") or "napcat").strip()

    if mode == "open_platform":
        return QQOpenPlatformConnection(
            app_id=str(settings.get("qq_open_app_id") or "").strip(),
            client_secret=str(settings.get("qq_open_client_secret") or "").strip(),
            logger=logger,
            identity_probe=lambda: bool(
                get_settings().get("qq_open_identity_probe_enabled", False)
            ),
            emit_log=emit_log,
            # Sandbox environment: an unpublished bot is only reachable on the sandbox
            # domain. Passed as a callable, but the connection samples it once per
            # connect() and pins it, so a settings change applies on the next reconnect.
            sandbox=lambda: bool(
                get_settings().get("qq_open_sandbox_enabled", False)
            ),
        )

    return OneBotClient(
        onebot_url=str(settings.get("onebot_url") or "ws://0.0.0.0:6199"),
        token=str(settings.get("token") or ""),
        logger=logger,
        emit_log=emit_log,
        # napcat_forward = forward WS client (dials out to the OneBot implementation's WS server);
        # everything else (incl. the default) uses the reverse WS server.
        direction="forward" if mode == "napcat_forward" else "reverse",
    )

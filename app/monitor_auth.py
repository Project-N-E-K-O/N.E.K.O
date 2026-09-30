# -*- coding: utf-8 -*-
"""Authentication primitives for the optional Monitor service.

The Monitor deliberately keeps authentication opt-in so existing LAN viewers
continue to work.  Callers are responsible for deciding which routes require
authentication and for returning the appropriate HTTP/WebSocket response.
"""

from __future__ import annotations

import hmac
import logging
import re
from typing import Any

from config import MONITOR_TOKEN


class MonitorQueryLogFilter(logging.Filter):
    """Remove query strings from Uvicorn request paths before formatting.

    Both HTTP access and WebSocket handshake records can contain query tokens.
    Keep the path and status useful while omitting the complete query string.
    """

    @staticmethod
    def _redact(value: object) -> object:
        if not isinstance(value, str):
            return value
        return re.sub(r"\?[^\s\"']*", "", value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True


def install_monitor_log_redaction() -> None:
    """Install idempotent query redaction on Monitor's Uvicorn loggers."""

    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, MonitorQueryLogFilter) for item in logger.filters):
            logger.addFilter(MonitorQueryLogFilter())


def monitor_auth_enabled() -> bool:
    """Return whether Monitor token authentication is configured."""

    return bool(MONITOR_TOKEN)


def verify_monitor_token(token: str | None) -> bool:
    """Constant-time verification of a candidate Monitor token.

    An unconfigured token keeps the historical open-service behavior.  Empty
    or missing candidates are rejected once authentication is enabled.
    """

    if not MONITOR_TOKEN:
        return True
    if token is None:
        return False
    return hmac.compare_digest(token.encode("utf-8"), MONITOR_TOKEN.encode("utf-8"))


def _header_value(headers: Any, name: str) -> str | None:
    """Read a header from Starlette headers or a plain mapping."""

    value = headers.get(name) if headers is not None else None
    if value is None and headers is not None:
        value = headers.get(name.lower())
    if value is None and headers is not None and hasattr(headers, "items"):
        wanted = name.lower()
        value = next(
            (candidate for key, candidate in headers.items()
             if str(key).lower() == wanted),
            None,
        )
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    return value.strip() if isinstance(value, str) else None


def extract_monitor_token(
    *,
    headers: Any = None,
    query_token: str | None = None,
    cookie_token: str | None = None,
) -> str | None:
    """Extract a token using the supported HTTP/WebSocket transport forms.

    ``Authorization: Bearer`` is preferred, then ``X-Monitor-Token``, then the
    browser-compatible ``?token=`` query parameter.
    """

    authorization = _header_value(headers, "authorization")
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and value:
            return value.strip()
    header_token = _header_value(headers, "x-monitor-token")
    if header_token:
        return header_token
    if isinstance(query_token, str) and query_token:
        return query_token
    if isinstance(cookie_token, str) and cookie_token:
        return cookie_token
    return None


def authenticate_monitor_request(
    *,
    headers: Any = None,
    query_token: str | None = None,
    cookie_token: str | None = None,
) -> bool:
    """Authenticate an HTTP request or WebSocket handshake."""

    return verify_monitor_token(
        extract_monitor_token(
            headers=headers,
            query_token=query_token,
            cookie_token=cookie_token,
        )
    )

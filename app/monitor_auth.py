# -*- coding: utf-8 -*-
"""Authentication for the optional Monitor service.

Authentication stays opt-in (empty ``MONITOR_TOKEN``) so existing LAN viewers
keep working.  Once a token is configured, ``MonitorAuthMiddleware`` gates
every HTTP and WebSocket route except the public static asset mounts, so a
newly added route is protected by default instead of relying on each handler
to remember an auth call.

Credentials (``Authorization: Bearer``, ``X-Monitor-Token`` or ``?token=``):

* ``MONITOR_TOKEN`` -- full access, including the ``/sync*`` producer routes
  the main server writes to;
* optional ``MONITOR_VIEWER_TOKEN`` -- viewer routes only, so a shared viewer
  link does not hand out write access to ``/sync*``;
* a viewer session cookie, issued in exchange for a ``?token=`` page load with
  either token.  It carries an expiring HMAC instead of a token, is named per
  Monitor port, and only grants viewer routes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from urllib.parse import urlencode, urlsplit

from starlette._utils import get_route_path
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocket

from config import MONITOR_SERVER_PORT, MONITOR_TOKEN, MONITOR_VIEWER_TOKEN

# Cookies are scoped by host, not by port: include the port so that services
# sharing the host neither collide with nor overwrite each other's session.
VIEWER_SESSION_COOKIE = f"neko_monitor_session_{MONITOR_SERVER_PORT}"
VIEWER_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

_PUBLIC_PATH_PREFIXES = ("/static/", "/user_live2d/", "/user_live2d_local/", "/workshop/")
_PRODUCER_PATH_PREFIXES = ("/sync/", "/sync_binary/")
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class MonitorQueryLogFilter(logging.Filter):
    """Remove query strings from Uvicorn request paths before formatting.

    Both HTTP access and WebSocket handshake records can contain query tokens.
    Keep the path and status useful while omitting the complete query string.
    """

    # Only a query string that follows a path ("/x?..."), so unrelated text
    # containing "?" is left alone.  The whole query up to whitespace goes:
    # quotes are legal query characters and must not end the redaction early.
    _PATH_QUERY = re.compile(r"(/[^\s?]*)\?\S*")

    @classmethod
    def _redact(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return cls._PATH_QUERY.sub(r"\1", value)

    def filter(self, record: logging.LogRecord) -> bool:
        # Never rewrite a format template that still has args to apply: a
        # "?%s" would lose its placeholder and break formatting.
        if not record.args:
            record.msg = self._redact(record.msg)
        elif isinstance(record.args, tuple):
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


def _matches(candidate: str, secret: str) -> bool:
    return bool(secret) and hmac.compare_digest(candidate.encode("utf-8"), secret.encode("utf-8"))


def monitor_token_scope(token: str | None) -> str | None:
    """Return ``"full"``, ``"viewer"`` or ``None`` (rejected) for a candidate token.

    An unconfigured ``MONITOR_TOKEN`` keeps the historical open service, so
    every candidate gets full access.  Empty or missing candidates are
    rejected once authentication is enabled.
    """

    if not MONITOR_TOKEN:
        return "full"
    if not token:
        return None
    if _matches(token, MONITOR_TOKEN):
        return "full"
    if _matches(token, MONITOR_VIEWER_TOKEN):
        return "viewer"
    return None


def verify_monitor_token(token: str | None) -> bool:
    """Whether ``token`` grants full (producer) access."""

    return monitor_token_scope(token) == "full"


def extract_monitor_token(conn: HTTPConnection) -> tuple[str | None, str | None]:
    """Return ``(token, source)`` for the explicit token transports.

    ``Authorization: Bearer`` is preferred, then ``X-Monitor-Token``, then the
    browser-compatible ``?token=`` query parameter.  ``source`` is ``"header"``
    or ``"query"``; both are ``None`` when no explicit token was sent.
    """

    authorization = conn.headers.get("authorization", "").strip()
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip(), "header"
    header_token = conn.headers.get("x-monitor-token", "").strip()
    if header_token:
        return header_token, "header"
    query_token = conn.query_params.get("token")
    if query_token:
        return query_token, "query"
    return None, None


def _viewer_session_signature(expires_at: int) -> str:
    # Bind the viewer token too: rotating either token invalidates every session.
    viewer_digest = hashlib.sha256(MONITOR_VIEWER_TOKEN.encode("utf-8")).hexdigest()
    return hmac.new(
        MONITOR_TOKEN.encode("utf-8"),
        f"neko-monitor-viewer:{expires_at}:{viewer_digest}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def issue_viewer_session(now: float | None = None) -> str:
    """Mint a viewer session value; it never contains the token itself."""

    expires_at = int(now if now is not None else time.time()) + VIEWER_SESSION_TTL_SECONDS
    return f"{expires_at}.{_viewer_session_signature(expires_at)}"


def verify_viewer_session(value: str | None, now: float | None = None) -> bool:
    """Check signature and expiry; rotating a token invalidates all sessions."""

    if not MONITOR_TOKEN or not value:
        return False
    expires_text, _, signature = value.partition(".")
    # Cookies arrive latin-1 decoded and attacker-controlled: "²".isdigit() is
    # True yet int() rejects it, and compare_digest raises on non-ASCII str.
    # Any malformed value must be a clean rejection, never a 500.
    if not (expires_text.isascii() and expires_text.isdigit()) or len(expires_text) > 12:
        return False
    if not signature.isascii():
        return False
    expires_at = int(expires_text)
    if expires_at <= (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(signature.encode("ascii"), _viewer_session_signature(expires_at).encode("ascii"))


def _same_origin(conn: HTTPConnection) -> bool:
    """Reject cross-origin browser handshakes that ride on the session cookie.

    SameSite=Lax still sends the cookie on a same-site WebSocket handshake from
    another port of the same host.  Only host:port is compared: behind a
    TLS-terminating proxy the browser Origin is ``https`` while the upstream
    scheme may be ``ws``.  Non-browser clients send no Origin and are allowed.
    """

    origin = conn.headers.get("origin")
    if not origin:
        return True
    host = conn.headers.get("host", "")
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def _exchange_query_token(conn: HTTPConnection) -> RedirectResponse:
    """Swap a ``?token=`` page load for a session cookie and drop the token from the URL."""

    remaining = [(key, value) for key, value in conn.query_params.multi_items() if key != "token"]
    # Use the still-encoded raw path: ``url.path`` truncates at a decoded
    # "#"/"?" in a character name.  Collapse leading slashes so "//evil.example"
    # cannot become a protocol-relative open redirect.
    raw_path = conn.scope.get("raw_path") or conn.scope.get("path", "").encode("utf-8")
    path = "/" + raw_path.decode("latin-1").lstrip("/\\")
    location = path + (f"?{urlencode(remaining)}" if remaining else "")
    response = RedirectResponse(location, status_code=303, headers=_NO_STORE_HEADERS)
    response.set_cookie(
        VIEWER_SESSION_COOKIE,
        issue_viewer_session(),
        max_age=VIEWER_SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        # Uvicorn applies X-Forwarded-Proto from FORWARDED_ALLOW_IPS proxies,
        # so a TLS-terminating trusted proxy yields https here.
        secure=conn.url.scheme == "https",
    )
    return response


class MonitorAuthMiddleware:
    """Default-deny ASGI gate for every Monitor route once a token is configured."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not monitor_auth_enabled():
            await self.app(scope, receive, send)
            return
        # Decide from the path the router actually matches: root_path stripped
        # (a proxy mount would otherwise hide /sync/* behind a prefix), and
        # never conn.url, which starlette 0.46 rebuilds from the unvalidated
        # Host header (a Host containing "#", "?" or "/" would hide it too).
        path = get_route_path(scope)
        if path.startswith(_PUBLIC_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        conn = HTTPConnection(scope)
        producer_route = path.startswith(_PRODUCER_PATH_PREFIXES)
        token, source = extract_monitor_token(conn)
        if token is not None:
            scope_granted = monitor_token_scope(token)
            authorized = scope_granted == "full" or (scope_granted == "viewer" and not producer_route)
        else:
            authorized = (
                not producer_route
                and verify_viewer_session(conn.cookies.get(VIEWER_SESSION_COOKIE))
                and (scope["type"] == "http" or _same_origin(conn))
            )

        if not authorized:
            if scope["type"] == "websocket":
                # Closing before accept makes the server answer the handshake
                # with 403; the sync connector then warns once and slows down.
                await WebSocket(scope, receive, send).close(code=1008)
            else:
                response = JSONResponse(
                    {"detail": "Monitor authentication required"},
                    status_code=401,
                    headers=_NO_STORE_HEADERS,
                )
                await response(scope, receive, send)
            return

        if (
            source == "query"
            and scope["type"] == "http"
            and scope.get("method") == "GET"
            and not path.startswith("/api/")
        ):
            await _exchange_query_token(conn)(scope, receive, send)
            return
        await self.app(scope, receive, send)

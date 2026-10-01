"""Local mutation authentication for the user-plugin server.

The plugin manager is a browser client of a loopback HTTP service. CORS does
not prevent a simple cross-origin POST from executing, so lifecycle mutations
require both a trusted Origin and the instance CSRF token.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute

from config.network import (
    AUTOSTART_ALLOWED_ORIGINS,
    AUTOSTART_CSRF_TOKEN,
    MAIN_SERVER_PORT,
    USER_PLUGIN_SERVER_PORT,
)

logger = logging.getLogger(__name__)
_CSRF_HEADER = "X-CSRF-Token"
_ERROR_CODE = "csrf_validation_failed"


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)


def _normalize_origin(raw: str | None) -> str:
    """Return a canonical origin, rejecting credentials and URL components."""
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw.strip())
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        return ""
    hostname = hostname.lower().rstrip(".")
    host_text = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    effective_port = (443 if parsed.scheme == "https" else 80) if port is None else port
    return f"{parsed.scheme.lower()}://{host_text}:{effective_port}"


def _origin_for_host_port(host: str, port: int, *, scheme: str = "http") -> str:
    host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{host_text}:{int(port)}"


def _read_runtime_port(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(fallback)
    try:
        port = int(raw)
    except ValueError:
        return int(fallback)
    return port if 1 <= port <= 65535 else int(fallback)


def _configured_origins() -> frozenset[str]:
    origins: set[str] = set()
    plugin_port = _read_runtime_port("NEKO_USER_PLUGIN_SERVER_PORT", USER_PLUGIN_SERVER_PORT)
    for port in (MAIN_SERVER_PORT, USER_PLUGIN_SERVER_PORT, plugin_port, 5173):
        for host in ("127.0.0.1", "localhost", "::1"):
            origins.add(_origin_for_host_port(host, port))
    for value in AUTOSTART_ALLOWED_ORIGINS:
        normalized = _normalize_origin(value)
        if normalized:
            origins.add(normalized)
    for value in os.getenv("NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS", "").split(","):
        normalized = _normalize_origin(value.strip())
        if normalized:
            origins.add(normalized)
    return frozenset(origins)


def _local_request(request: Request) -> bool:
    return bool(
        request.client is not None
        and _is_loopback(request.client.host)
        and _is_loopback(request.url.hostname)
    )


def _has_browser_metadata(request: Request) -> bool:
    return any(
        request.headers.get(name)
        for name in ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user")
    )


def _valid_token(request: Request) -> bool:
    token = request.headers.get(_CSRF_HEADER, "")
    return bool(token and AUTOSTART_CSRF_TOKEN and secrets.compare_digest(token, AUTOSTART_CSRF_TOKEN))


def _deny() -> None:
    raise HTTPException(
        status_code=403,
        detail={
            "error_code": _ERROR_CODE,
            "detail": "Request could not be verified",
        },
        headers={"X-Error-Code": _ERROR_CODE},
    )


def require_plugin_mutation_access(request: Request) -> None:
    """Authorize a plugin lifecycle mutation before any route side effect."""
    if not _local_request(request):
        _deny()
    origin_header = request.headers.get("origin")
    origin = _normalize_origin(origin_header)
    if origin_header is not None:
        if not origin or origin not in _configured_origins() or not _valid_token(request):
            _deny()
        return
    # Native/local callers may omit Origin, but browser metadata or a Referer
    # must never silently enter this compatibility path.
    if request.headers.get("referer") or _has_browser_metadata(request):
        _deny()
    logger.info("Accepted originless local plugin mutation: path=%s", request.url.path)


class PluginMutationGuardedRoute(APIRoute):
    """Run the mutation guard before FastAPI parses a request body.

    FastAPI resolves body parameters before route dependencies.  Applying the
    guard in a dependency therefore still spools rejected JSON/multipart
    uploads.  This route wrapper checks request headers first, while retaining
    the exact same authorization contract as ``require_plugin_mutation_access``.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded_handler(request: Request) -> Response:
            require_plugin_mutation_access(request)
            return await handler(request)

        return guarded_handler


def require_plugin_token_bootstrap_access(request: Request) -> None:
    """Authorize token bootstrap without exposing it through arbitrary CORS."""
    if not _local_request(request):
        _deny()
    origin_header = request.headers.get("origin")
    if origin_header is not None:
        origin = _normalize_origin(origin_header)
        if not origin or origin not in _configured_origins():
            _deny()
        return
    referer = request.headers.get("referer")
    if referer and _normalize_origin(referer) not in _configured_origins():
        _deny()
    if _has_browser_metadata(request) and request.headers.get("sec-fetch-site") not in {"same-origin", "same-site", "none"}:
        _deny()


def csrf_token() -> str:
    """Return the configured instance token without logging its value."""
    return AUTOSTART_CSRF_TOKEN

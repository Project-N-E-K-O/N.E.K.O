"""Local mutation authentication for the user-plugin server.

The plugin manager may use desktop loopback or NAS/Docker same-origin access.
CORS does not prevent simple cross-origin POSTs from executing, so browser
lifecycle mutations require both trusted provenance and the instance token.
HostOriginGuard rejects DNS-rebinding hosts before these route dependencies.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from utils.host_origin_guard import _canonicalize_hostname

from config.network import (
    AUTOSTART_ALLOWED_ORIGINS,
    AUTOSTART_CSRF_TOKEN,
    MAIN_SERVER_PORT,
    USER_PLUGIN_SERVER_PORT,
    resolve_user_plugin_base,
)

logger = logging.getLogger(__name__)
_CSRF_HEADER = "X-CSRF-Token"
_ERROR_CODE = "csrf_validation_failed"
# Embedded and standalone servers share the same trusted proxy boundary.
TRUSTED_PROXY_IPS = "127.0.0.1,::1"


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
    # Use the same IPv6/IDNA hostname rules as the outer rebinding guard.
    canonical = _canonicalize_hostname(hostname)
    if canonical is None or (port is not None and not 1 <= port <= 65535):
        return ""
    hostname = canonical[0]
    host_text = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    effective_port = (443 if parsed.scheme == "https" else 80) if port is None else port
    return f"{parsed.scheme.lower()}://{host_text}:{effective_port}"


def _origin_from_referer(raw: str | None) -> str:
    """Extract only the origin from a document Referer URL."""
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw.strip())
        if parsed.username or parsed.password or not parsed.scheme or not parsed.netloc:
            return ""
        return _normalize_origin(f"{parsed.scheme}://{parsed.netloc}")
    except (TypeError, ValueError):
        return ""


def _origin_for_host_port(host: str, port: int, *, scheme: str = "http") -> str:
    host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{host_text}:{int(port)}"


def _configured_origins() -> frozenset[str]:
    origins: set[str] = set()
    plugin_port = urlsplit(resolve_user_plugin_base()).port or USER_PLUGIN_SERVER_PORT
    for port in (MAIN_SERVER_PORT, USER_PLUGIN_SERVER_PORT, plugin_port):
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


def _trusted_origin(request: Request, origin: str) -> bool:
    """Match the external origin or an explicitly allowed frontend origin.

    Official Nginx preserves Host (including the published port). Uvicorn
    supplies the external scheme only from trusted proxy peers; do not read
    X-Forwarded-* directly here. Peer IP need not be loopback for NAS access.
    NAS hosts also permit hostname-only matching for outer TLS termination
    and port mapping. This deliberately trusts other ports on the same NAS;
    loopback desktop frontends retain their explicit origin allowlist.
    """
    target = _normalize_origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
    if not origin or not target:
        return False
    nas_hostname_match = (
        not _is_loopback(request.url.hostname)
        and urlsplit(origin).hostname == urlsplit(target).hostname
    )
    return origin == target or nas_hostname_match or (
        _is_loopback(request.url.hostname) and origin in _configured_origins()
    )


def _has_browser_metadata(request: Request) -> bool:
    return any(
        request.headers.get(name)
        for name in ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user")
    )


def _valid_token(request: Request) -> bool:
    token = request.headers.get(_CSRF_HEADER, "")
    try:
        return bool(token and AUTOSTART_CSRF_TOKEN and secrets.compare_digest(token, AUTOSTART_CSRF_TOKEN))
    except (TypeError, UnicodeError):
        # Header values are decoded from raw HTTP bytes. Reject malformed or
        # non-ASCII values as an ordinary failed credential instead of leaking
        # a 500 from compare_digest.
        return False


def _deny(*, token_invalid: bool = False) -> None:
    raise HTTPException(
        status_code=403,
        detail={
            "error_code": _ERROR_CODE,
            "csrf_failure": "token" if token_invalid else "origin",
            "detail": "Request could not be verified",
        },
        # Keep the public error code stable; only token failures are retryable.
        headers={"X-Error-Code": _ERROR_CODE, "X-CSRF-Failure": "token" if token_invalid else "origin"},
    )


def require_plugin_mutation_access(request: Request) -> None:
    """Authorize a plugin lifecycle mutation before any route side effect."""
    origin_header = request.headers.get("origin")
    origin = _normalize_origin(origin_header)
    if origin_header is not None:
        if not _trusted_origin(request, origin):
            _deny()
        if not _valid_token(request):
            _deny(token_invalid=True)
        return
    # Native/local callers may omit Origin, but browser metadata or a Referer
    # must never silently enter this compatibility path.
    if not _local_request(request) or request.headers.get("referer") or _has_browser_metadata(request):
        _deny()
    # Keep tokenless native scripts compatible, but never ignore a supplied
    # invalid credential. This is not authentication against local processes.
    if _CSRF_HEADER.lower() in request.headers and not _valid_token(request):
        _deny(token_invalid=True)
    logger.info("Accepted originless local plugin mutation: path=%s", request.url.path)


def require_plugin_token_bootstrap_access(request: Request) -> None:
    """Authorize token bootstrap without exposing it through arbitrary CORS."""
    origin_header = request.headers.get("origin")
    if origin_header is not None:
        origin = _normalize_origin(origin_header)
        if not _trusted_origin(request, origin):
            _deny()
        return
    referer = request.headers.get("referer")
    if referer:
        if not _trusted_origin(request, _origin_from_referer(referer)):
            _deny()
        return
    # Browsers with a suppressed Referer may still fetch their same-origin
    # token. same-site is insufficient: another service on the NAS is a
    # different origin even when browsers classify it as the same site.
    if _has_browser_metadata(request):
        if request.headers.get("sec-fetch-site") != "same-origin":
            _deny()
    elif not _local_request(request):
        _deny()


def csrf_token() -> str:
    """Return the configured instance token without logging its value."""
    return AUTOSTART_CSRF_TOKEN

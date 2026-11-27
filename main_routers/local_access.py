"""Access checks for APIs that expose resources on the backend machine."""

import os

from fastapi import Request

from main_logic.activity.system_signals import is_remote_backend_deployment
from utils.deployment import is_behind_proxy
from utils.host_origin_guard import (
    TRUSTED_ORIGINS_ENV,
    _origins_match,
    _parse_origin,
    _parse_referer_origin,
    _read_trusted_origins,
)
from utils.instance_access import _same_origin
from utils.loopback import is_direct_loopback_peer, is_loopback_peer_host

_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def _header_values(request: Request, name: str) -> list[str]:
    headers = getattr(request, "headers", None)
    if headers is None:
        return []
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        return list(getlist(name))
    value = headers.get(name) if hasattr(headers, "get") else None
    return [value] if isinstance(value, str) else []


def _format_origin_url(origin) -> str:
    host = (
        f"[{origin.authority.hostname}]"
        if ":" in origin.authority.hostname
        else origin.authority.hostname
    )
    port = f":{origin.authority.port}" if origin.authority.port is not None else ""
    return f"{origin.scheme}://{host}{port}"


def is_loopback_request(request: Request) -> bool:
    """Check the request peer address without imposing deployment policy."""
    client_host = request.client.host if request.client else ""
    return is_loopback_peer_host(client_host)


def is_local_oauth_status_request(request: Request) -> bool:
    """Protect account metadata in proxy and remote deployments.

    Proxy mode can replace the peer address with an untrusted forwarded value.
    TCP tunnels cannot be detected from HTTP metadata; remote deployments must
    explicitly enable NEKO_BEHIND_PROXY or NEKO_ACTIVITY_TRACKER_REMOTE.
    In desktop mode loopback proxies may forward a client address; Uvicorn
    preserves external peers so a local HTTP tunnel cannot acquire this grant.
    """
    if is_behind_proxy():
        return False
    return not is_remote_backend_deployment() and is_loopback_request(request)


def is_direct_loopback_request(request: Request) -> bool:
    """Allow direct local resource calls without trusting proxy-rewritten peers.

    In proxy mode Uvicorn can replace client.host from X-Forwarded-For. Reject
    forwarded calls to local resources, while allowing backend processes to
    call localhost directly without forwarding metadata. Desktop mode accepts
    local debugging proxies when their processed client address is loopback,
    while external addresses forwarded by a loopback proxy remain remote.
    """
    client_host = request.client.host if request.client else ""
    # Test doubles may omit headers; they are only consulted in proxy mode.
    return is_direct_loopback_peer(client_host, getattr(request, "headers", None) or {})


def is_local_or_instance_authorized_request(request: Request) -> bool:
    """Allow authenticated remote/proxy calls, direct loopback calls, and TestClient."""
    scope = getattr(request, "scope", None) or {}
    if scope.get("neko.instance_identity"):
        return True
    if is_direct_loopback_request(request):
        return True
    host_headers = _header_values(request, "host")
    client = scope.get("client")
    return (
        len(host_headers) == 1
        and host_headers[0].strip().lower() == "testserver"
        and isinstance(client, (tuple, list))
        and len(client) >= 1
        and client[0] == "testclient"
    )


def is_same_origin_browser_request(request: Request) -> bool:
    """Reject cross-site, opaque, malformed, or cross-port browser requests."""
    headers = getattr(request, "headers", None) or {}
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in _ALLOWED_FETCH_SITES:
        return False

    origin_headers = _header_values(request, "origin")
    referer_headers = _header_values(request, "referer")
    if len(origin_headers) > 1 or len(referer_headers) > 1:
        return False

    if origin_headers:
        parsed = _parse_origin(origin_headers[0])
    elif referer_headers:
        parsed = _parse_referer_origin(referer_headers[0])
    else:
        return True

    if parsed is None:
        return False
    if _same_origin(request, _format_origin_url(parsed)):
        return True
    trusted = _read_trusted_origins(os.getenv(TRUSTED_ORIGINS_ENV))
    return any(_origins_match(parsed, entry) for entry in trusted)


def validate_local_browser_request(
    request: Request,
    *,
    require_json: bool = False,
) -> tuple[int, str, str] | None:
    """Validate local/instance access, same-origin browser metadata, and JSON content type."""
    if not is_local_or_instance_authorized_request(request):
        return (403, "local_access_required", "Forbidden: local access only")
    if not is_same_origin_browser_request(request):
        return (403, "cross_origin_request_rejected", "Request origin not allowed")
    if require_json:
        headers = getattr(request, "headers", None) or {}
        content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return (415, "unsupported_content_type", "Content-Type must be application/json")
    return None

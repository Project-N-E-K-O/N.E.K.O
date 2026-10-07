"""Access checks for APIs that expose resources on the backend machine."""

import ipaddress

from fastapi import Request

from main_logic.activity.system_signals import is_remote_backend_deployment
from utils.deployment import has_forwarding_metadata, is_behind_proxy


def is_loopback_request(request: Request) -> bool:
    """Check the request peer address without imposing deployment policy."""
    client_host = request.client.host if request.client else ""
    if client_host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(client_host or ""))
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


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
    if is_behind_proxy():
        if has_forwarding_metadata(request.headers):
            return False
    return is_loopback_request(request)

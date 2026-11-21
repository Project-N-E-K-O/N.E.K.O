"""Shared loopback checks for socket peer addresses (``client.host``)."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

from utils.deployment import has_forwarding_metadata, is_behind_proxy


def is_loopback_peer_host(host: Any) -> bool:
    """True when ``host`` names this machine: ``localhost`` or a loopback IP.

    IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``, seen on dual-stack
    listeners) are unwrapped before the check. Anything else that does not
    parse as an IP address (``testclient``, ``""``, ``None``) is not loopback.
    """
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(host or ""))
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def is_direct_loopback_peer(host: Any, headers: Mapping[str, str]) -> bool:
    """Loopback peer whose address was not rewritten by a trusted proxy.

    In proxy mode Uvicorn can replace ``client.host`` from X-Forwarded-For, so
    forwarded connections are rejected. Desktop mode accepts local debugging
    proxies when their processed peer address is loopback.
    """
    if is_behind_proxy() and has_forwarding_metadata(headers):
        return False
    return is_loopback_peer_host(host)

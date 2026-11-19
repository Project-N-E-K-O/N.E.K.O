"""Shared loopback check for socket peer addresses (``client.host``)."""

from __future__ import annotations

import ipaddress
from typing import Any


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

"""Deployment flags shared by server startup and request authorization."""

import os
from collections.abc import Mapping


def is_behind_proxy() -> bool:
    """Use the same proxy flag semantics at startup and at access boundaries."""
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def uvicorn_proxy_options() -> dict:
    """Preserve local proxy compatibility without hiding forwarded remote peers."""
    return {
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1,::1",
    }


def has_forwarding_metadata(headers: Mapping[str, str]) -> bool:
    """Identify forwarded requests before granting native-only local access."""
    return any(name in headers for name in ("x-forwarded-for", "x-real-ip", "forwarded"))

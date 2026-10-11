"""Deployment flags shared by server startup and request authorization."""

import os
from collections.abc import Mapping


def _env_flag(name: str) -> bool:
    """Shared truthiness rule for the opt-in flags read in this module."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_behind_proxy() -> bool:
    """Use the same proxy flag semantics at startup and at access boundaries."""
    # Deliberately not _env_flag: utils/local_ws_guard.py and agent_router parse
    # this flag without "on", and proxy trust must not differ between guards.
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def requires_https() -> bool:
    """Opt into refusing plaintext remote pairing; HTTP stays usable by default.

    Many self-hosted deployments (home broadband, LAN/NAS access by IP, regions
    where a public certificate needs a registered domain) cannot obtain HTTPS.
    """
    return _env_flag("NEKO_REQUIRE_HTTPS")


def is_remote_backend_deployment() -> bool:
    """Share remote flag semantics between OS features and instance access."""
    return any(_env_flag(name) for name in ("NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"))


def uvicorn_proxy_options() -> dict:
    """Preserve local proxy compatibility without hiding forwarded remote peers."""
    return {
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1,::1",
    }


def has_forwarding_metadata(headers: Mapping[str, str]) -> bool:
    """Identify forwarded requests before granting native-only local access."""
    return any(name.lower() in {"x-forwarded", "x-real-ip", "forwarded"}
               or name.lower().startswith("x-forwarded-") for name in headers)

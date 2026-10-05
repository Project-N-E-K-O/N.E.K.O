"""Deployment flags shared by server startup and request authorization."""

import os
from collections.abc import Mapping


def is_behind_proxy() -> bool:
    """Use the same proxy flag semantics at startup and at access boundaries."""
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def requires_https() -> bool:
    """Opt into refusing plaintext remote pairing; HTTP stays usable by default.

    Many self-hosted deployments (home broadband, LAN/NAS access by IP, regions
    where a public certificate needs a registered domain) cannot obtain HTTPS.
    """
    return os.environ.get("NEKO_REQUIRE_HTTPS", "").strip().lower() in ("1", "true", "yes", "on")


def is_remote_backend_deployment() -> bool:
    """Share remote flag semantics between OS features and instance access."""
    return any(os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")
               for name in ("NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"))


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

"""Authentication contract for privileged loopback service control calls."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from typing import Any

from utils.host_origin_guard import is_http_browser_origin_allowed

INTERNAL_HTTP_AUTH_HEADER = "X-Neko-Internal-Control-Token"
_INTERNAL_HTTP_AUTH_TOKEN = secrets.token_urlsafe(32)


def get_internal_http_auth_token() -> str:
    """Return this process' privileged control credential."""
    return _INTERNAL_HTTP_AUTH_TOKEN


def install_internal_http_auth_token(token: str) -> None:
    """Install the credential explicitly delivered to a launcher-owned server."""
    normalized = str(token or "").strip()
    if len(normalized) < 32:
        raise ValueError("internal control token is missing or too short")
    global _INTERNAL_HTTP_AUTH_TOKEN
    _INTERNAL_HTTP_AUTH_TOKEN = normalized


def rotate_internal_http_auth_token() -> str:
    """Create a fresh launcher/process-local credential."""
    token = secrets.token_urlsafe(32)
    install_internal_http_auth_token(token)
    return token


def _isolate_forked_child_internal_http_auth_token() -> None:
    # A server can host third-party workers.  A forked descendant inherits all
    # Python memory, so replace the server credential before child code runs.
    rotate_internal_http_auth_token()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_isolate_forked_child_internal_http_auth_token)


def internal_http_auth_headers() -> dict[str, str]:
    """Return the per-launch credential required by internal control routes."""
    return {INTERNAL_HTTP_AUTH_HEADER: get_internal_http_auth_token()}


def is_internal_http_request_authorized(
    scope: Mapping[str, Any],
    headers: Mapping[str, str],
) -> bool:
    """Accept native loopback clients or trusted browser origins with the launch token."""
    provided = str(headers.get(INTERNAL_HTTP_AUTH_HEADER) or "")
    expected = get_internal_http_auth_token()
    return bool(
        provided
        and expected
        and secrets.compare_digest(provided, expected)
        and is_http_browser_origin_allowed(scope)
    )

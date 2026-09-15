"""Authentication contract for privileged loopback service control calls."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from config import AUTOSTART_CSRF_TOKEN
from utils.host_origin_guard import is_http_browser_origin_allowed

INTERNAL_HTTP_AUTH_HEADER = "X-CSRF-Token"


def internal_http_auth_headers() -> dict[str, str]:
    """Return the per-launch credential required by internal control routes."""
    return {INTERNAL_HTTP_AUTH_HEADER: AUTOSTART_CSRF_TOKEN}


def is_internal_http_request_authorized(
    scope: Mapping[str, Any],
    headers: Mapping[str, str],
) -> bool:
    """Accept native loopback clients or trusted browser origins with the launch token."""
    provided = str(headers.get(INTERNAL_HTTP_AUTH_HEADER) or "")
    return bool(
        provided
        and AUTOSTART_CSRF_TOKEN
        and secrets.compare_digest(provided, AUTOSTART_CSRF_TOKEN)
        and is_http_browser_origin_allowed(scope)
    )

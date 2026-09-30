# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Shared resolvers for the configured N.E.K.O community and auth origins."""

from __future__ import annotations

import functools
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


DEFAULT_SOCIAL_BASE_URL = "https://community.project-neko.cn"
DEFAULT_AUTH_URL = "https://auth.project-neko.cn"


def validate_http_url(value: str, *, name: str, allow_empty: bool = False) -> str:
    """Return ``value`` stripped, or raise ``ValueError`` unless it is an http(s) base URL.

    Callers append paths (``/oauth2/auth``, ``/api/v1/...``) to these values, so a
    query or fragment would swallow the appended path and is rejected as well.
    """

    value = value.strip()
    if allow_empty and not value:
        return value
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be a valid http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not include credentials")
    return value


def configured_social_base_url() -> str | None:
    """Return the explicitly configured community origin, if present."""

    raw = (os.environ.get("NEKO_SOCIAL_BASE_URL", "") or "").strip().rstrip("/")
    return raw or None


def social_base_url() -> str:
    """Return the configured community origin or the production fallback."""

    return configured_social_base_url() or DEFAULT_SOCIAL_BASE_URL


def auth_public_url() -> str:
    """Return the configured IdP origin or the production fallback.

    Single source for every caller: OAuth issuance and the logout SSO-cookie
    purge must agree on this origin.
    """

    return _resolve_auth_public_url(os.environ.get("NEKO_AUTH_URL", "") or "")


@functools.lru_cache(maxsize=8)
def _resolve_auth_public_url(raw: str) -> str:
    # Called on every OAuth request and social-config poll; cache per raw value
    # so a misconfiguration is reported once instead of on every request.
    value = raw.strip().rstrip("/")
    try:
        # Same rule the plugin settings enforce, so both see one origin.
        value = validate_http_url(value, name="NEKO_AUTH_URL", allow_empty=True)
    except ValueError as exc:
        logger.error(
            "%s; OAuth and logout will use the production IdP %s instead",
            exc,
            DEFAULT_AUTH_URL,
        )
        return DEFAULT_AUTH_URL
    return value or DEFAULT_AUTH_URL

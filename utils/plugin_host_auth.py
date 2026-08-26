from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request

PLUGIN_HOST_TOKEN_ENV = "NEKO_PLUGIN_HOST_API_TOKEN"
PLUGIN_HOST_TOKEN_HEADER = "X-NEKO-Plugin-Host-Token"
LIVE_FRAME_TOKEN_HEADER = "X-NEKO-Live-Frame-Token"


def require_plugin_host_token() -> str:
    token = os.environ.get(PLUGIN_HOST_TOKEN_ENV, "")
    if not token.strip():
        raise RuntimeError(
            f"{PLUGIN_HOST_TOKEN_ENV} must be set to the same non-empty value "
            "for main_server and agent_server"
        )
    return token


def plugin_host_auth_headers() -> dict[str, str]:
    token = require_plugin_host_token()
    return {PLUGIN_HOST_TOKEN_HEADER: token}


def require_plugin_host_access(request: Request) -> None:
    expected = os.environ.get(PLUGIN_HOST_TOKEN_ENV, "")
    supplied = request.headers.get(PLUGIN_HOST_TOKEN_HEADER, "")
    if (
        not expected.strip()
        or not supplied
        or not secrets.compare_digest(
            supplied.encode("utf-8"),
            expected.encode("utf-8"),
        )
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

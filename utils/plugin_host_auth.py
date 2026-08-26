from __future__ import annotations

import ctypes
import os
import secrets
import sys

from fastapi import HTTPException, Request

PLUGIN_HOST_TOKEN_ENV = "NEKO_PLUGIN_HOST_API_TOKEN"
PLUGIN_HOST_TOKEN_HEADER = "X-NEKO-Plugin-Host-Token"
LIVE_FRAME_TOKEN_HEADER = "X-NEKO-Live-Frame-Token"
_plugin_host_token = ""


def _scrub_process_environment_value(name: str, value: str) -> None:
    """Best-effort removal of a secret from Linux's original env memory."""
    if not sys.platform.startswith("linux") or not value:
        return
    try:
        libc = ctypes.CDLL(None)
        environ = ctypes.POINTER(ctypes.c_char_p).in_dll(libc, "environ")
        addresses = ctypes.cast(environ, ctypes.POINTER(ctypes.c_void_p))
        prefix = f"{name}=".encode()
        index = 0
        while environ[index] is not None:
            entry = environ[index]
            if entry.startswith(prefix):
                address = addresses[index]
                if address:
                    ctypes.memset(
                        address + len(prefix),
                        0,
                        max(0, len(entry) - len(prefix)),
                    )
            index += 1
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def configure_plugin_host_token(token: str | None = None) -> str:
    """Move the shared host credential into process memory and out of env."""
    global _plugin_host_token

    environment_value = os.environ.get(PLUGIN_HOST_TOKEN_ENV, "")
    selected = environment_value if token is None else str(token)
    if environment_value:
        _scrub_process_environment_value(
            PLUGIN_HOST_TOKEN_ENV,
            environment_value,
        )
    os.environ.pop(PLUGIN_HOST_TOKEN_ENV, None)
    _plugin_host_token = selected.strip()
    return _plugin_host_token


def require_plugin_host_token() -> str:
    environment_value = os.environ.get(PLUGIN_HOST_TOKEN_ENV, "")
    token = (
        configure_plugin_host_token()
        if environment_value
        else _plugin_host_token
    )
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
    try:
        expected = require_plugin_host_token()
    except RuntimeError:
        expected = ""
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

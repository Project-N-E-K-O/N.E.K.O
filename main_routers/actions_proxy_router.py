# -*- coding: utf-8 -*-
"""
Actions Proxy Router

Proxies Command Palette requests from the main server to the user plugin
server, which owns the actual action providers.

URL convention: routes declared WITHOUT trailing slash. See
``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` for the project-wide convention.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from config import USER_PLUGIN_BASE
from utils.logger_config import get_module_logger

router = APIRouter(tags=["actions-proxy"])
logger = get_module_logger(__name__, "Main")

_USER_PLUGIN_DEFAULT_BASE = "http://127.0.0.1:48916"
_USER_PLUGIN_BASE_CACHE: tuple[str, float] = ("", 0.0)


def _proxy_response(resp: httpx.Response) -> JSONResponse:
    """Return the plugin server response with its original status code."""
    try:
        content = resp.json()
    except ValueError:
        content = {"detail": resp.text}
    return JSONResponse(status_code=resp.status_code, content=content)


def _empty_actions_payload() -> dict[str, Any]:
    return {"actions": [], "preferences": {"pinned": [], "hidden": [], "recent": []}}


def _empty_preferences_payload() -> dict[str, Any]:
    return {"pinned": [], "hidden": [], "recent": []}


async def _resolve_user_plugin_base() -> str:
    """Resolve the active user plugin server base URL.

    The configured base is normally correct. The fallback mirrors the agent
    dashboard route so dev setups that still use the historical default port
    remain reachable.
    """
    global _USER_PLUGIN_BASE_CACHE
    cached_base, cached_at = _USER_PLUGIN_BASE_CACHE
    now = time.monotonic()
    if cached_base and now - cached_at < 5.0:
        return cached_base

    candidates: list[str] = []
    for value in (USER_PLUGIN_BASE, _USER_PLUGIN_DEFAULT_BASE):
        normalized = str(value or "").rstrip("/")
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    async with httpx.AsyncClient(timeout=0.45, proxy=None, trust_env=False) as client:
        for base in candidates:
            try:
                response = await client.get(f"{base}/available")
                if response.is_success:
                    _USER_PLUGIN_BASE_CACHE = (base, now)
                    return base
            except Exception:
                continue

    fallback = str(USER_PLUGIN_BASE or _USER_PLUGIN_DEFAULT_BASE).rstrip("/")
    _USER_PLUGIN_BASE_CACHE = (fallback, now)
    return fallback


@router.get("/chat/actions", response_model=None)
async def proxy_chat_actions(
    plugin_id: str | None = Query(default=None),
) -> Any:
    """Proxy GET /chat/actions to the user plugin server."""
    params: dict[str, str] = {}
    if plugin_id:
        params["plugin_id"] = plugin_id
    try:
        base = await _resolve_user_plugin_base()
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.get(f"{base}/chat/actions", params=params)
            return _proxy_response(resp)
    except Exception:
        logger.debug("Failed to proxy GET /chat/actions", exc_info=True)
        return _empty_actions_payload()


# Preferences routes must be registered before the {action_id:path} route.


@router.get("/chat/actions/preferences", response_model=None)
async def proxy_get_preferences() -> Any:
    """Proxy GET /chat/actions/preferences to the user plugin server."""
    try:
        base = await _resolve_user_plugin_base()
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.get(f"{base}/chat/actions/preferences")
            return _proxy_response(resp)
    except Exception:
        logger.debug("Failed to proxy GET /chat/actions/preferences", exc_info=True)
        return _empty_preferences_payload()


@router.post("/chat/actions/preferences", response_model=None)
async def proxy_save_preferences(request: Request) -> JSONResponse:
    """Proxy POST /chat/actions/preferences to the user plugin server."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        base = await _resolve_user_plugin_base()
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.post(f"{base}/chat/actions/preferences", json=body)
            return _proxy_response(resp)
    except Exception as exc:
        logger.warning("Failed to proxy POST /chat/actions/preferences: %s", exc)
        return JSONResponse(status_code=502, content=_empty_preferences_payload())


@router.post("/chat/actions/{action_id:path}/execute", response_model=None)
async def proxy_chat_action_execute(
    action_id: str,
    request: Request,
) -> JSONResponse:
    """Proxy POST /chat/actions/{action_id}/execute to the user plugin server."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        base = await _resolve_user_plugin_base()
        async with httpx.AsyncClient(timeout=10.0, proxy=None, trust_env=False) as client:
            resp = await client.post(f"{base}/chat/actions/{action_id}/execute", json=body)
            return _proxy_response(resp)
    except Exception as exc:
        logger.warning("Failed to proxy POST /chat/actions/%s/execute: %s", action_id, exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "action": None, "message": str(exc)},
        )

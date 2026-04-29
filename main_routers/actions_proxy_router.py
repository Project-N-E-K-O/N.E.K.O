# -*- coding: utf-8 -*-
"""
Actions Proxy Router

Proxies Command Palette requests from the main server to the
user plugin server, which owns the actual action providers.

GET  /chat/actions                       → plugin server
GET  /chat/actions/preferences           → plugin server
POST /chat/actions/preferences           → plugin server
POST /chat/actions/{action_id}/execute   → plugin server
"""

from typing import Optional

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from config import USER_PLUGIN_SERVER_PORT
from utils.logger_config import get_module_logger

logger = get_module_logger("actions_proxy")

router = APIRouter(tags=["actions-proxy"])

_PLUGIN_BASE = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"


def _proxy_response(resp: httpx.Response):
    """Return upstream response with its original status code."""
    try:
        content = resp.json()
    except ValueError:
        content = {"detail": resp.text}
    return JSONResponse(status_code=resp.status_code, content=content)


@router.get("/chat/actions")
async def proxy_chat_actions(
    plugin_id: Optional[str] = Query(default=None),
):
    """Proxy GET /chat/actions to the user plugin server."""
    url = f"{_PLUGIN_BASE}/chat/actions"
    params = {}
    if plugin_id:
        params["plugin_id"] = plugin_id
    try:
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.get(url, params=params)
            return _proxy_response(resp)
    except Exception:
        logger.debug("Failed to proxy GET /chat/actions", exc_info=True)
        return {"actions": [], "preferences": {"pinned": [], "hidden": [], "recent": []}}


# ── Preferences routes MUST be registered before the {action_id:path}
#    route below, otherwise FastAPI would match "preferences" as an action_id.

@router.get("/chat/actions/preferences")
async def proxy_get_preferences():
    """Proxy GET /chat/actions/preferences to the user plugin server."""
    url = f"{_PLUGIN_BASE}/chat/actions/preferences"
    try:
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.get(url)
            return _proxy_response(resp)
    except Exception:
        logger.debug("Failed to proxy GET /chat/actions/preferences", exc_info=True)
        return {"pinned": [], "hidden": [], "recent": []}


@router.post("/chat/actions/preferences")
async def proxy_save_preferences(request: Request):
    """Proxy POST /chat/actions/preferences to the user plugin server."""
    url = f"{_PLUGIN_BASE}/chat/actions/preferences"
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
            resp = await client.post(url, json=body)
            return _proxy_response(resp)
    except Exception as exc:
        logger.warning("Failed to proxy POST /chat/actions/preferences: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"pinned": [], "hidden": [], "recent": []},
        )


# ── Execute route uses {action_id:path} — must come last.

@router.post("/chat/actions/{action_id:path}/execute")
async def proxy_chat_action_execute(
    action_id: str,
    request: Request,
):
    """Proxy POST /chat/actions/{action_id}/execute to the user plugin server."""
    url = f"{_PLUGIN_BASE}/chat/actions/{action_id}/execute"
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10.0, proxy=None, trust_env=False) as client:
            resp = await client.post(url, json=body)
            return _proxy_response(resp)
    except Exception as exc:
        logger.warning("Failed to proxy POST /chat/actions/%s/execute: %s", action_id, exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "action": None, "message": str(exc)},
        )

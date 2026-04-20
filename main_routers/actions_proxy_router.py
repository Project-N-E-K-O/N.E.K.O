# -*- coding: utf-8 -*-
"""
Actions Proxy Router

Proxies Quick Actions Panel requests from the main server to the
user plugin server, which owns the actual action providers.

GET  /chat/actions                       → plugin server
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
            return resp.json()
    except Exception:
        logger.debug("Failed to proxy GET /chat/actions", exc_info=True)
        return {"actions": [], "categories": []}


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
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to proxy POST /chat/actions/%s/execute: %s", action_id, exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "action": None, "message": str(exc)},
        )

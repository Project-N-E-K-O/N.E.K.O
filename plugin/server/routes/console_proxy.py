"""Pass what the console pages ask of the task agents on to the agent process.

The console is served by this server and talks to it only. What it reads
and changes of the task agents (which runtime, permissions, the model's
window, the task agent space) lives in the agent process, whose routes for
it are ``/console/...`` (``app/agent_server/api_console.py``). This router
answers the same paths here and hands each request over, with the token of
this launch that those routes ask for.

Who may ask is decided here, where it is decided for the rest of the
console: a request that changes something passes
``require_plugin_mutation_access``; one that reads passes the check the
token bootstrap uses, so a page of another site cannot read the user's
rules or settings through this server. With the backend deployed away from
the user nothing is changed (as ``main_routers/agent_router``): the agent
there acts on the server's machine.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from plugin.server.infrastructure.mutation_auth import (
    csrf_token,
    require_plugin_mutation_access,
    require_plugin_token_bootstrap_access,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/console", tags=["console"])

TOKEN_HEADER = "X-CSRF-Token"
# Removing dsh, or adding up what the directories hold, takes a moment.
_TIMEOUT = httpx.Timeout(30.0, connect=3.0)
_client: httpx.AsyncClient | None = None


def _agent_base() -> str:
    from config import TOOL_SERVER_PORT

    return f"http://127.0.0.1:{TOOL_SERVER_PORT}"


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # Loopback only: no proxy of the environment is to be asked.
        _client = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False)
    return _client


async def close_console_proxy() -> None:
    """Close the connection pool to the agent process; called when this server shuts down."""
    global _client
    client, _client = _client, None
    if client is not None and not client.is_closed:
        await client.aclose()


def _remote() -> bool:
    from utils.deployment import is_remote_backend_deployment

    return is_remote_backend_deployment()


async def _forward(request: Request, path: str) -> JSONResponse:
    options: dict[str, Any] = {"params": list(request.query_params.multi_items()), "headers": {TOKEN_HEADER: csrf_token()}}
    if request.method != "GET":
        body = await request.body()
        if body:
            options["content"] = body
            options["headers"]["Content-Type"] = "application/json"
    try:
        answer = await _http().request(request.method, f"{_agent_base()}/console/{path}", **options)
    except httpx.HTTPError as exc:
        logger.warning("the agent process did not answer a console request (exc_type=%s)", type(exc).__name__)
        raise HTTPException(502, {"code": "agent_unreachable"}) from None
    try:
        content = answer.json()
    except ValueError:
        raise HTTPException(502, {"code": "agent_unreachable"}) from None
    return JSONResponse(content, status_code=answer.status_code)


def _main_base() -> str:
    from config import MAIN_SERVER_PORT

    return f"http://127.0.0.1:{MAIN_SERVER_PORT}"


# The keys of the switches a console page may set besides the one for all of
# it. The plugins' switch is not among them: it follows the one for all.
_SWITCH_KEYS = frozenset({"computer_use_enabled", "browser_use_enabled"})


@router.post("/switch", dependencies=[Depends(require_plugin_mutation_access)])
async def set_switch(request: Request) -> JSONResponse:
    """Switch NekoClaw on or off, or one of the two device switches.

    ``{"enabled": bool}`` for all of it; ``{"key": ..., "value": bool}``
    for the keyboard and mouse or the browser. Passed to the main server's
    command route, which keeps its sessions in step and passes it on to the
    agent process; nothing else of that route is reachable from here.
    """
    if _remote():
        raise HTTPException(501, {"code": "remote_backend"})
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(400, {"code": "bad_switch"})
    # One or the other, and nothing beside it: what is sent on is built here.
    if set(payload) == {"enabled"} and isinstance(payload["enabled"], bool):
        command: dict[str, Any] = {"command": "set_agent_enabled", "enabled": payload["enabled"]}
    elif set(payload) == {"key", "value"} and payload["key"] in _SWITCH_KEYS and isinstance(payload["value"], bool):
        command = {"command": "set_flag", "key": payload["key"], "value": payload["value"]}
    else:
        raise HTTPException(400, {"code": "bad_switch"})
    try:
        answer = await _http().post(f"{_main_base()}/api/agent/command", json=command)
        content = answer.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("the main server did not answer a switch (exc_type=%s)", type(exc).__name__)
        raise HTTPException(502, {"code": "agent_unreachable"}) from None
    if not answer.is_success or not isinstance(content, dict) or content.get("success") is not True:
        raise HTTPException(502, {"code": "switch_failed"})
    return JSONResponse({"success": True})


@router.get("/{path:path}", dependencies=[Depends(require_plugin_token_bootstrap_access)])
async def read_console(request: Request, path: str) -> JSONResponse:
    """Read something of the task agents for a console page."""
    return await _forward(request, path)


@router.api_route(
    "/{path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def change_console(request: Request, path: str) -> JSONResponse:
    """Change something of the task agents for a console page."""
    if _remote():
        raise HTTPException(501, {"code": "remote_backend"})
    return await _forward(request, path)

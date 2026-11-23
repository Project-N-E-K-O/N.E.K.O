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
from urllib.parse import quote

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


# Routes that wait for the user or for a model: the folder dialog is open
# for up to two minutes (``utils.native_directory``), a draft is given 45
# seconds (``nekoclaw.host.drafts``).
_SLOW = {
    "space/pick-directory": httpx.Timeout(135.0, connect=3.0),
    "familiars/draft": httpx.Timeout(60.0, connect=3.0),
}


def _console_url(path: str) -> str:
    """Return the agent process's address of a console route; 404 for a path that is none.

    ``path`` arrives decoded, so ``..%2F`` is ``../`` by now, and what is
    sent on is folded by the client: a segment that climbs out would
    reach any other route of the agent process with this server's token.
    Each segment is sent on encoded, and none of them is empty, ``.`` or
    ``..`` or holds a backslash or a control character.
    """
    segments = path.split("/")
    if any(
        segment in ("", ".", "..") or "\\" in segment or any(ord(char) < 0x20 for char in segment)
        for segment in segments
    ):
        raise HTTPException(404, {"code": "not_found"})
    url = httpx.URL(f"{_agent_base()}/console/" + "/".join(quote(segment, safe="") for segment in segments))
    if not url.path.startswith("/console/"):
        raise HTTPException(404, {"code": "not_found"})
    return str(url)


async def _forward(request: Request, path: str) -> JSONResponse:
    url = _console_url(path)
    options: dict[str, Any] = {"params": list(request.query_params.multi_items()), "headers": {TOKEN_HEADER: csrf_token()}}
    if path in _SLOW:
        options["timeout"] = _SLOW[path]
    if request.method != "GET":
        body = await request.body()
        if body:
            options["content"] = body
            options["headers"]["Content-Type"] = "application/json"
    try:
        answer = await _http().request(request.method, url, **options)
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
# Not a capability: whether the main page shows the task HUD (``app.agent_task_hud``).
_TASK_HUD_KEY = "task_hud_visible"


@router.post("/switch", dependencies=[Depends(require_plugin_mutation_access)])
async def set_switch(request: Request) -> JSONResponse:
    """Switch NekoClaw on or off, one of the two device switches, or the task HUD.

    ``{"enabled": bool}`` for all of it; ``{"key": ..., "value": bool}``
    for the keyboard and mouse, the browser, or whether the main page shows
    the task HUD (``task_hud_visible``). Passed to the main server's
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
    elif (
        set(payload) == {"key", "value"}
        and isinstance(payload["key"], str)
        and payload["key"] in _SWITCH_KEYS | {_TASK_HUD_KEY}
        and isinstance(payload["value"], bool)
    ):
        if payload["key"] == _TASK_HUD_KEY:
            command = {"command": "set_task_hud_visible", "value": payload["value"]}
        else:
            command = {"command": "set_flag", "key": payload["key"], "value": payload["value"]}
    else:
        raise HTTPException(400, {"code": "bad_switch"})
    try:
        main_base = _main_base()
        answer = await _http().post(
            f"{main_base}/api/agent/command",
            json=command,
            headers={
                TOKEN_HEADER: csrf_token(),
                "Origin": main_base,
            },
        )
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

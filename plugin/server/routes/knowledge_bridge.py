# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Same-origin bridge from the plugin-manager page to Main's knowledge proxy.

The management page is served next to ``/market/*``. Its knowledge calls go
``/market/knowledge/<path>`` (bridge token, loopback only) -> Main
``/api/public-knowledge/<path>`` (CSRF + Origin) -> Memory Server
``/internal/knowledge/<path>``. Only allowlisted paths pass; bodies are
size-capped and streamed, never parsed here.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from plugin.logging_config import get_logger
from plugin.server.routes.market_bridge import (
    _main_server_port,
    _require_local_bridge_token_access,
    _verify_token,
)
from plugin.utils.http_imports import ensure_httpx


router = APIRouter(prefix="/market/knowledge", tags=["market-knowledge"])
logger = get_logger("server.routes.knowledge_bridge")

_JSON_BODY_MAX_BYTES = 64 * 1024
_PACK_BODY_MAX_BYTES = 10 * 1024 * 1024 + 64 * 1024
_READ_TIMEOUT_SECONDS = 15.0
_WRITE_TIMEOUT_SECONDS = 45.0
_READ_PATHS = frozenset(
    {"status", "entries", "entry", "packs", "packs/jobs", "diagnostics/recent"}
)
_WRITE_PATHS = {
    "settings": _JSON_BODY_MAX_BYTES,
    "entry/disabled": _JSON_BODY_MAX_BYTES,
    "packs/import": _PACK_BODY_MAX_BYTES,
    "packs/jobs/cancel": _JSON_BODY_MAX_BYTES,
    "packs/jobs/discard": _JSON_BODY_MAX_BYTES,
    "packs/auto-context": _JSON_BODY_MAX_BYTES,
    "packs/index-policy": _JSON_BODY_MAX_BYTES,
    "packs/material-type": _JSON_BODY_MAX_BYTES,
    "packs/remove": _JSON_BODY_MAX_BYTES,
}


class _BodyTooLarge(Exception):
    pass


def _failure(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def public_knowledge_bridge(
    path: str,
    request: Request,
    token: str = Query(..., description="Bridge token"),
):
    _require_local_bridge_token_access(request)
    _verify_token(token)
    path = path.strip("/")
    if request.method == "GET":
        if path not in _READ_PATHS:
            raise HTTPException(status_code=404, detail="knowledge endpoint not found")
        max_bytes = 0
    else:
        if path not in _WRITE_PATHS:
            raise HTTPException(status_code=404, detail="knowledge endpoint not found")
        max_bytes = _WRITE_PATHS[path]

    port = _main_server_port()
    target = f"http://127.0.0.1:{port}/api/public-knowledge/{path}"
    params = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
    headers = {"Accept": "application/json"}
    content: AsyncIterator[bytes] | None = None
    if request.method == "POST":
        import config

        declared = request.headers.get("content-length")
        try:
            if declared is not None and int(declared) > max_bytes:
                return _failure("payload_too_large", 413)
        except ValueError:
            return _failure("invalid_request", 400)
        headers.update(
            {
                "Content-Type": request.headers.get("content-type", "application/json"),
                "Origin": f"http://127.0.0.1:{port}",
                "X-CSRF-Token": str(config.AUTOSTART_CSRF_TOKEN),
            }
        )
        if declared is not None:
            headers["Content-Length"] = declared

        async def body() -> AsyncIterator[bytes]:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > max_bytes:
                    raise _BodyTooLarge()
                yield chunk

        content = body()

    httpx = await ensure_httpx()
    timeout = _WRITE_TIMEOUT_SECONDS if request.method == "POST" else _READ_TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=2.0), trust_env=False
        ) as client:
            response = await client.request(
                request.method, target, params=params, content=content, headers=headers
            )
    except _BodyTooLarge:
        return _failure("payload_too_large", 413)
    except httpx.TimeoutException:
        return _failure("knowledge_timeout", 504)
    except httpx.HTTPError as exc:
        if isinstance(exc.__context__, _BodyTooLarge):
            return _failure("payload_too_large", 413)
        logger.warning("knowledge bridge failed: {}", type(exc).__name__)
        return _failure("main_server_unavailable", 502)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={"Content-Type": response.headers.get("content-type", "application/json")},
    )

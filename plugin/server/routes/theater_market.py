"""Paired Market pages may hand one bounded script to Main's theater importer."""

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

from plugin.server.routes.market_bridge import _verify_token, _main_server_port
from plugin.server.routes.knowledge_bridge import main_write_headers
from plugin.settings import MARKET_ORIGINS
from utils.http.knowledge_proxy import capped_body, BodyTooLarge, is_body_too_large


router = APIRouter(prefix="/market/theater", tags=["market-theater"])
_MAX_BYTES = 8 * 1024 * 1024


@router.api_route("/launch/{request_id}", methods=["GET"])
@router.api_route("/launch/{request_id}/{action}", methods=["POST"])
async def theater_market(request_id: str, request: Request, action: str = ""):
    _verify_token(authorization=request.headers.get("authorization"))
    origin = request.headers.get("origin")
    if origin and origin not in MARKET_ORIGINS:
        # Local launchers do not use this paired external API.
        raise HTTPException(403, "market origin forbidden")
    if action not in {"", "import"}:
        raise HTTPException(404, "theater operation not found")
    if not request_id or len(request_id) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in request_id):
        raise HTTPException(400, "invalid request id")
    limit = _MAX_BYTES if action == "import" else 4096
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "payload too large")
    path = f"/api/theater-market/launch/{request_id}" + (f"/{action}" if action else "")
    headers = main_write_headers(_main_server_port())
    headers["Content-Type"] = request.headers.get("content-type", "application/json")
    try:
        async with httpx.AsyncClient(timeout=25.0, proxy=None, trust_env=False) as client:
            response = await client.request(
                request.method, f"http://127.0.0.1:{_main_server_port()}{path}",
                params=dict(request.query_params), headers=headers,
                content=capped_body(request, limit) if request.method == "POST" else None,
            )
    except BodyTooLarge:
        raise HTTPException(413, "payload too large")
    except httpx.HTTPError as error:
        if is_body_too_large(error):
            raise HTTPException(413, "payload too large")
        raise HTTPException(502, "theater service unavailable")
    return Response(response.content, status_code=response.status_code, media_type="application/json")

"""Bounded desktop community API relay for remote Electron instances.

Remote clients use an instance session, never a Linux credential file or cloud
refresh token. Only the existing notification and credit endpoints are relayed.
"""

import asyncio
import re
import time

import anyio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from main_routers import card_drop_router as C, community_oauth as O

router = APIRouter(tags=["community-remote-proxy"])


async def _relay(request: Request):
    if await O._account_request_identity(request) is None or not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "instance_authorization_required"}, status_code=403)
    status = await O.resolve_saved_oauth_status()
    snapshot = status.get("snapshot") or {}
    access = snapshot.get("access_token")
    if not access or status.get("rejected"):
        return JSONResponse({"detail": "community_login_required"}, status_code=401)
    path = request.url.path
    stream = path == "/api/notifications/stream"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30), follow_redirects=False)
    try:
        # Query/body fields cannot change the fixed upstream origin or endpoint.
        upstream = client.build_request(
            request.method, C._social_base_url().rstrip("/") + path,
            params=request.query_params,
            content=await request.body(),
            headers={"Authorization": f"Bearer {access}",
                     "Accept": "text/event-stream" if stream else "application/json",
                     "Content-Type": request.headers.get("content-type", "application/json")},
        )
        response = await client.send(upstream, stream=True)
        if stream:
            upstream.extensions["timeout"]["read"] = None
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)

    async def chunks():
        current = snapshot
        next_account_check = 0
        try:
            async for chunk in response.aiter_bytes():
                if time.monotonic() >= next_account_check:
                    current = await asyncio.to_thread(C._desktop_session_snapshot)
                    next_account_check = time.monotonic() + 1
                same_account = bool(current) and (
                    current.get("local_user_id") == snapshot.get("local_user_id")
                    if snapshot.get("local_user_id")
                    else current.get("access_token") == access
                )
                if not same_account:
                    break
                yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()
                await client.aclose()

    return StreamingResponse(chunks(), status_code=response.status_code,
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                             media_type="text/event-stream" if stream else "application/json")


@router.get("/api/notifications/stream")
async def notifications(request: Request):
    return await _relay(request)


@router.get("/api/forge/credits")
async def credits(request: Request):
    return await _relay(request)


@router.post("/api/forge/credits/grant")
@router.post("/api/forge/credits/drop-events/claim")
async def credit_change(request: Request):
    return await _relay(request)


@router.post("/api/forge/credits/drop-events/{event_id}/ack")
async def credit_ack(request: Request, event_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
        return JSONResponse({"detail": "invalid_drop_event"}, status_code=400)
    return await _relay(request)

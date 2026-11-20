"""Paired model commands and local-only, desktop-authenticated package reads.

Remote pages can select one published model in a Main-owned context. They
cannot ask this server to fetch a URL, read a local path, or receive desktop
OAuth credentials. Large package bodies never traverse the browser bridge.
"""

from __future__ import annotations

import asyncio
import json

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from plugin.server.routes import market_bridge as auth
from plugin.server.routes.knowledge_bridge import main_write_headers
from plugin.settings import MARKET_ORIGINS
from services.model_workshop.bundle import BundleLimits
from services.model_workshop.market import HASH, REQUEST
from utils.http.knowledge_proxy import BodyTooLarge, capped_body, is_body_too_large

router = APIRouter(prefix="/market/models", tags=["market-models"])
_slots = asyncio.Semaphore(2)
_JSON_MAX = BundleLimits().manifest_bytes + 64 * 1024


def _client():
    return httpx.AsyncClient(timeout=httpx.Timeout(20, connect=3),
                             follow_redirects=False, trust_env=False, proxy=None)


def _failure(code="model_market_unavailable", status=502):
    return HTTPException(status, detail={"code": code})


async def _open(client, url):
    """Use existing refresh/CAS machinery, once before any bytes are forwarded."""
    token = await auth._authenticated_market_token()
    for attempt in range(2):
        response = await client.send(client.build_request("GET", url, headers={
            "Authorization": "Bearer " + token["access_token"],
            "Accept-Encoding": "identity",
        }), stream=True)
        if response.status_code != 401 or attempt:
            if response.status_code != 200:
                await response.aclose()
                raise _failure("market_auth_required" if response.status_code == 401
                               else "model_market_unavailable", 409 if response.status_code == 401 else 502)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                await response.aclose()
                raise _failure()
            return response
        await response.aclose()
        token = await auth._ensure_valid_oauth_token(
            force_refresh=True, rejected_access_token=token["access_token"])
        if not token or not token.get("access_token") or not auth._oauth_subject_is_verified(token):
            raise _failure("market_auth_required", 409)


async def _descriptor(client, model_id):
    base = auth._normalized_base_url(auth.MARKET_API_URL)
    if not base:
        raise _failure()
    response = await _open(client, f"{base}/api/v1/models/{model_id}")
    try:
        if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise _failure()
        raw = bytearray()
        async for chunk in response.aiter_raw(64 * 1024):
            if len(raw) + len(chunk) > _JSON_MAX:
                raise _failure()
            raw.extend(chunk)
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            raise _failure() from None
        if (not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] != model_id
                or value.get("status") != "published"
                or not isinstance(value.get("capabilities"), dict)
                or value["capabilities"].get("can_download") is not True
                or value.get("model_format") not in {"live2d", "vrm", "mmd"}
                or type(value.get("byte_size")) is not int
                or not 0 < value["byte_size"] <= BundleLimits().archive_bytes
                or not isinstance(value.get("file_sha256"), str)
                or not HASH.fullmatch(value["file_sha256"])):
            raise _failure("model_market_package_changed", 409)
        # Exclude filenames, signed URLs, author text and all private projections.
        return {key: value[key] for key in ("id", "byte_size", "file_sha256", "model_format")}
    finally:
        await response.aclose()


class _PackageResponse(StreamingResponse):
    """Bound total time and retain the upstream/slot even if header sending fails."""

    def __init__(self, client, upstream, size):
        self.client, self.upstream = client, upstream
        super().__init__(self.chunks(size), media_type="application/zip", headers={
            "Content-Length": str(size), "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    async def chunks(self, size):
        received = 0
        async for chunk in self.upstream.aiter_raw(64 * 1024):
            received += len(chunk)
            if received > size:
                raise OSError("model package length mismatch")
            yield chunk
        if received != size:
            raise OSError("model package length mismatch")

    async def __call__(self, scope, receive, send):
        try:
            async with asyncio.timeout(600):
                await super().__call__(scope, receive, send)
        finally:
            # AnyIO disconnect cancellation must not interrupt resource release.
            with anyio.CancelScope(shield=True):
                try:
                    await self.upstream.aclose()
                finally:
                    try:
                        await self.client.aclose()
                    finally:
                        _slots.release()


@router.get("/descriptor/{model_id:int}")
@router.get("/package/{model_id:int}")
async def local_model_file(
    model_id: int, request: Request,
    expected_sha256: str = Query(..., pattern=r"^[0-9a-f]{64}$"),
    byte_size: int = Query(..., ge=1, le=BundleLimits().archive_bytes),
):
    """Only Main's local hop may use this credentialed, fixed-target reader."""
    auth._require_local_bridge_token_access(request)
    auth._verify_token(authorization=request.headers.get("authorization"))
    if not 0 < model_id < 2**63:
        raise _failure("model_market_invalid_selection", 400)
    try:
        await asyncio.wait_for(_slots.acquire(), .05)
    except TimeoutError:
        raise _failure("model_market_busy", 429) from None
    client, handed_off = None, False
    try:
        client = _client()
        # Metadata/refresh waits also have a total bound, independent of chunks.
        async with asyncio.timeout(25):
            descriptor = await _descriptor(client, model_id)
            if (descriptor["file_sha256"], descriptor["byte_size"]) != (expected_sha256, byte_size):
                raise _failure("model_market_package_changed", 409)
            if "/descriptor/" in request.url.path:
                return JSONResponse(descriptor, headers={"Cache-Control": "no-store"})
            base = auth._normalized_base_url(auth.MARKET_API_URL)
            upstream = await _open(client, f"{base}/api/v1/models/{model_id}/download")
        if (upstream.headers.get("content-type", "").split(";", 1)[0] != "application/zip"
                or upstream.headers.get("content-length") != str(byte_size)):
            await upstream.aclose()
            raise _failure("model_market_package_changed", 409)
        result = _PackageResponse(client, upstream, byte_size)
        handed_off = True
        return result
    except (httpx.HTTPError, TimeoutError):
        raise _failure() from None
    finally:
        if not handed_off:
            with anyio.CancelScope(shield=True):
                try:
                    if client is not None:
                        await client.aclose()
                finally:
                    _slots.release()


@router.get("/launch/{request_id}")
@router.post("/launch/{request_id}/{action}")
async def paired_model_command(request_id: str, request: Request, action: str = ""):
    """Relay only small selection/cancel commands; never desktop credentials."""
    auth._verify_token(authorization=request.headers.get("authorization"))
    if request.headers.get("origin") not in MARKET_ORIGINS:
        raise HTTPException(403, "market origin forbidden")
    if not REQUEST.fullmatch(request_id) or action not in {"", "select", "cancel"}:
        raise HTTPException(404, "model operation not found")
    headers = main_write_headers(auth._main_server_port())
    headers["Content-Type"] = "application/json"
    headers["Accept-Encoding"] = "identity"
    target = f"http://127.0.0.1:{auth._main_server_port()}/api/model-market/launch/{request_id}"
    if action:
        target += "/" + action
    try:
        async with asyncio.timeout(25), _client() as client:
            async with client.stream(request.method, target, headers=headers,
                    content=capped_body(request, 4096) if request.method == "POST" else None) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise _failure()
                raw = bytearray()
                async for chunk in response.aiter_raw(4096):
                    if len(raw) + len(chunk) > 128 * 1024:
                        raise _failure()
                    raw.extend(chunk)
                status = response.status_code
    except (BodyTooLarge, httpx.HTTPError, TimeoutError) as error:
        if is_body_too_large(error):
            raise HTTPException(413, "model command too large") from None
        raise _failure() from None
    return Response(bytes(raw), status_code=status,
                    media_type="application/json", headers={"Cache-Control": "no-store"})

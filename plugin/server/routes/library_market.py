"""Credentialed, bounded network transport for Main-owned personal-library jobs."""

from __future__ import annotations

import hashlib
import json
import secrets
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx

from plugin.server.routes import market_bridge as account
from utils.market_config import get_market_api_url, get_market_web_url


MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024


def _local(request: Request) -> None:
    """Reject browsers outside the local bridge before reading any upload body."""
    account._require_local_bridge_token_access(request)
    account._verify_token(authorization=request.headers.get("authorization"))


router = APIRouter(prefix="/market/library", tags=["library-market"], dependencies=[Depends(_local)])


def _url(path: str) -> str:
    """Only configured Market endpoints receive credentials, never an artifact URL."""
    base = get_market_api_url()
    if not base:
        raise HTTPException(503, detail={"code": "market_unavailable"})
    return base.rstrip("/") + "/api/v1/" + path


async def _json(response: httpx.Response) -> dict:
    """Bound upstream success and error bodies; no arbitrary server text is logged."""
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > MAX_JSON_BYTES:
            raise HTTPException(502, detail={"code": "market_invalid_response"})
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(502, detail={"code": "market_invalid_response"}) from exc
    if not isinstance(value, dict):
        raise HTTPException(502, detail={"code": "market_invalid_response"})
    if not response.is_success:
        # Refresh a rejected token once, then let Main replay from its immutable
        # artifact with the original idempotency UUID. A consumed body is not replayable here.
        if response.status_code == 401:
            rejected = response.request.headers.get("authorization", "").removeprefix("Bearer ")
            await account._ensure_valid_oauth_token(force_refresh=True, rejected_access_token=rejected)
            raise HTTPException(409, detail={"code": "market_auth_retry"})
        detail = value.get("detail")
        code = detail.get("code") if isinstance(detail, dict) else value.get("code")
        raise HTTPException(response.status_code, detail={"code": code or "market_request_failed"})
    return value


async def _request_json(method: str, path: str, token: str, **kwargs) -> dict:
    """Use async streaming even for JSON, so response buffering is explicitly bounded."""
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, _url(path), headers={"Authorization": "Bearer " + token}, **kwargs) as response:
                return await _json(response)
    except httpx.HTTPError as exc:
        raise HTTPException(503, detail={"code": "market_unavailable"}) from exc


async def _publisher_identity(token: dict) -> dict:
    """Resolve shares' actual author using the exact bearer used for publication."""
    user = await _request_json("GET", "auth/me", token["access_token"])
    if type(user.get("id")) is not int or user["id"] < 1:
        raise HTTPException(502, detail={"code": "market_invalid_response"})
    return {"publisher_id": user["id"], "market_api_url": get_market_api_url().rstrip("/")}


def _share(value: dict, *, published: bool = False) -> dict:
    """Expose the frozen share identity and whitelisted package preview only."""
    if value.get("kind") != "knowledge" or type(value.get("id")) is not int:
        raise HTTPException(409, detail={"code": "market_not_knowledge_package"})
    if published and value.get("status") != "published":
        raise HTTPException(409, detail={"code": "market_not_published"})
    preview = value.get("preview")
    if (not isinstance(preview, dict) or preview.get("format") != "neko.personal-knowledge-package"
            or preview.get("format_version") != 1):
        raise HTTPException(409, detail={"code": "market_not_knowledge_package"})
    return {key: value.get(key) for key in (
        "id", "kind", "title", "description", "status", "revision", "filename", "byte_size", "file_sha256", "preview",
    )}


@router.get("/status")
async def status(request: Request):
    """Reuse the running account process's OAuth state without returning its tokens."""
    result = await account.market_oauth_status(token=None, authorization=request.headers.get("authorization"))
    return {"ok": True, "authenticated": result.authenticated, "market_url": get_market_web_url()}


@router.get("/publisher")
async def publisher():
    """Supply an author snapshot for Main's durable publication task only."""
    token = await account._authenticated_market_token()
    return {"ok": True, **await _publisher_identity(token)}


@router.post("/login")
async def login(request: Request):
    """Start the existing PKCE flow; verifiers and access tokens stay server-side."""
    result = await account.market_oauth_start(request, token=None, authorization=request.headers.get("authorization"))
    return {"ok": True, "auth_url": result.auth_url}


@router.post("/browse")
async def browse(request: Request):
    """Open the existing knowledge market using its established one-time pairing."""
    result = await account.market_pair_code(request)
    query = urlencode({"neko_pair": result.one_time_code, "neko_port": result.port})
    return {"ok": True, "url": get_market_web_url().rstrip("/") + "/#/knowledge?" + query}


@router.get("/shares/{share_id}")
async def detail(share_id: int):
    """Read one authenticated share's actual status and frozen artifact identity."""
    if share_id < 1:
        raise HTTPException(400, detail={"code": "invalid_share"})
    token = await account._authenticated_market_token()
    return {"ok": True, "share": _share(await _request_json("GET", f"shares/{share_id}", token["access_token"]))}


async def _upload_metadata(request: Request) -> tuple[dict, object]:
    """Read one bounded JSON line, then retain streaming ownership of the ZIP body.

    Descriptions belong in the bounded body, not HTTP headers: a valid 5,000
    character CJK description can exceed a server's ordinary header limits.
    """
    stream = request.stream().__aiter__()
    raw, remainder = bytearray(), b""
    async for chunk in stream:
        prefix, separator, remainder = chunk.partition(b"\n")
        raw.extend(prefix)
        if len(raw) > 32 * 1024:
            raise HTTPException(400, detail={"code": "invalid_publish_request"})
        if separator:
            break
    else:
        raise HTTPException(400, detail={"code": "invalid_publish_request"})

    async def remaining():
        if remainder:
            yield remainder
        async for chunk in stream:
            yield chunk

    try:
        value = json.loads(raw)
        UUID(value["client_request_id"])
        size, sha = value["byte_size"], value["artifact_id"]
        if type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise ValueError
        if not isinstance(sha, str) or len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError
        if not isinstance(value["title"], str) or not 0 < len(value["title"].strip()) <= 120:
            raise ValueError
        if not isinstance(value.get("description", ""), str) or len(value.get("description", "")) > 5000:
            raise ValueError
        if value.get("share_id") is not None and (type(value["share_id"]) is not int or value["share_id"] < 1
                or type(value.get("revision")) is not int or value["revision"] < 1):
            raise ValueError
        if (type(value.get("publisher_id")) is not int or value["publisher_id"] < 1
                or not isinstance(value.get("market_api_url"), str) or not value["market_api_url"]):
            raise ValueError
        return value, remaining()
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, detail={"code": "invalid_publish_request"}) from exc


@router.post("/publish")
async def publish(request: Request):
    """Stream Main's immutable artifact into the real shares multipart contract."""
    metadata, incoming = await _upload_metadata(request)
    token = await account._authenticated_market_token()
    # Verify with the same token snapshot later sent to shares. A local login
    # switch after this await cannot substitute its new token into this upload.
    # A 401 refresh returns to Main; its replay must pass this check again.
    actor = await _publisher_identity(token)
    if any(metadata[key] != value for key, value in actor.items()):
        raise HTTPException(409, detail={"code": "market_publisher_changed"})
    boundary = "neko-library-" + secrets.token_hex(16)
    fields = ({"revision": str(metadata["revision"])} if metadata.get("share_id") else {
        "kind": "knowledge", "title": metadata["title"], "description": metadata.get("description", ""),
        "client_request_id": metadata["client_request_id"],
    })
    prefix = b"".join(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
        for key, value in fields.items()
    ) + (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{metadata["artifact_id"]}.neko-knowledge-pack.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()

    async def body():
        yield prefix
        received, hashed = 0, hashlib.sha256()
        async for chunk in incoming:
            received += len(chunk)
            if received > metadata["byte_size"]:
                raise HTTPException(413, detail={"code": "package_too_large"})
            hashed.update(chunk)
            yield chunk
        if received != metadata["byte_size"] or hashed.hexdigest() != metadata["artifact_id"]:
            raise HTTPException(400, detail={"code": "package_artifact_hash_mismatch"})
        yield suffix

    path = f"shares/{metadata['share_id']}/file" if metadata.get("share_id") else "shares"
    headers = {"Authorization": "Bearer " + token["access_token"],
               "Content-Type": f"multipart/form-data; boundary={boundary}",
               "Content-Length": str(len(prefix) + metadata["byte_size"] + len(suffix))}
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False, trust_env=False) as client:
            async with client.stream("PUT" if metadata.get("share_id") else "POST", _url(path),
                                     content=body(), headers=headers) as response:
                share = _share(await _json(response))
    except httpx.HTTPError as exc:
        raise HTTPException(503, detail={"code": "market_unavailable"}) from exc
    if share["file_sha256"] != metadata["artifact_id"] or share["byte_size"] != metadata["byte_size"]:
        raise HTTPException(409, detail={"code": "market_artifact_mismatch"})
    return {"ok": True, "share": share}


class _DownloadResponse(StreamingResponse):
    """Close upstream resources even when sending downstream headers fails first."""

    def __init__(self, stream, client: httpx.AsyncClient, response: httpx.Response, **kwargs):
        self.client = client
        self.response = response
        super().__init__(stream, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.response.aclose()
            await self.client.aclose()


@router.get("/shares/{share_id}/download")
async def download(share_id: int, revision: int, file_sha256: str):
    """Download only the selected published revision; a changed file fails closed."""
    token = await account._authenticated_market_token()
    selected = _share(await _request_json("GET", f"shares/{share_id}", token["access_token"]), published=True)
    if selected["revision"] != revision or selected["file_sha256"] != file_sha256:
        raise HTTPException(409, detail={"code": "market_revision_changed"})
    size = selected["byte_size"]
    if type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES:
        raise HTTPException(413, detail={"code": "package_too_large"})
    client = httpx.AsyncClient(timeout=180.0, follow_redirects=False, trust_env=False)
    try:
        response = await client.send(client.build_request("GET", _url(f"shares/{share_id}/download"),
                                                        params={"revision": revision, "file_sha256": file_sha256},
                                                        headers={"Authorization": "Bearer " + token["access_token"]}), stream=True)
    except BaseException:
        await client.aclose()
        raise
    if not response.is_success:
        try:
            await _json(response)
        finally:
            await response.aclose()
            await client.aclose()

    async def chunks():
        received, hashed = 0, hashlib.sha256()
        try:
            async for chunk in response.aiter_bytes(64 * 1024):
                received += len(chunk)
                if received > size:
                    raise RuntimeError("market_artifact_mismatch")
                hashed.update(chunk)
                yield chunk
            if received != size or hashed.hexdigest() != file_sha256:
                raise RuntimeError("market_artifact_mismatch")
        finally:
            await response.aclose()
            await client.aclose()

    return _DownloadResponse(chunks(), client, response, media_type="application/zip", headers={"Content-Length": str(size)})

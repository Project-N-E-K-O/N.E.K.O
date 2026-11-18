"""Publish a confirmed unified artifact using the existing market account."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from plugin.server.routes import market_bridge as account
from utils.http.knowledge_proxy import BodyTooLarge, capped_body
from utils.knowledge_package import DEFAULT_LIMITS, PackageError, validate_package
from utils.market_config import get_market_api_url

router = APIRouter(prefix="/market/knowledge", tags=["knowledge-market"])
_HASH = re.compile(r"[0-9a-f]{64}")


def _failure(reason: str, status: int) -> JSONResponse:
    """Return only known machine reasons, never upstream bodies or credentials."""
    return JSONResponse({"ok": False, "reason": reason}, status_code=status)


async def _payload(request: Request) -> dict:
    """Bound metadata before any account lookup or private artifact read."""
    raw = bytearray()
    async for block in capped_body(request, 32 * 1024):
        raw.extend(block)
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"pack_id", "artifact_id", "publish_confirmed", "client_request_id", "title", "description"}:
        raise ValueError("invalid_request")
    if value["publish_confirmed"] is not True:
        raise ValueError("market_publication_not_confirmed")
    if not isinstance(value["artifact_id"], str) or not _HASH.fullmatch(value["artifact_id"]):
        raise ValueError("invalid_request")
    if not isinstance(value["client_request_id"], str):
        raise ValueError("invalid_request")
    UUID(value["client_request_id"])
    if (not isinstance(value["pack_id"], str) or not isinstance(value["title"], str)
            or not 0 < len(value["title"].strip()) <= 120 or not isinstance(value["description"], str)
            or len(value["description"]) > 5000):
        raise ValueError("invalid_request")
    return value


async def _json(response: httpx.Response) -> dict:
    """Bound market metadata without persisting any source or server error text."""
    raw = bytearray()
    async for block in response.aiter_bytes(64 * 1024):
        raw.extend(block)
        if len(raw) > 256 * 1024:
            raise ValueError("market_publication_failed")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("market_publication_failed")
    return value


async def _download(client: httpx.AsyncClient, target: Path, artifact_id: str):
    """Spool the immutable local artifact and verify its complete public scope."""
    port = account._main_server_port()
    url = f"http://127.0.0.1:{port}/api/public-knowledge/packs/artifacts"
    handle = await asyncio.to_thread(target.open, "xb")
    try:
        size, digest = 0, hashlib.sha256()
        async with client.stream("GET", url, params={"artifact_id": artifact_id}) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes(64 * 1024):
                size += len(block)
                if size > DEFAULT_LIMITS.max_archive_bytes:
                    raise ValueError("package_too_large")
                digest.update(block)
                await asyncio.to_thread(handle.write, block)
        if digest.hexdigest() != artifact_id:
            raise ValueError("package_artifact_hash_mismatch")
    finally:
        await asyncio.to_thread(handle.close)
    return await asyncio.to_thread(validate_package, target, expected_sha256=artifact_id)


@router.post("/publish")
async def publish_knowledge(request: Request, token: str = Query(...)):
    """Expose one explicit, idempotent public action inside the account process."""
    account._require_local_bridge_token_access(request)
    account._verify_token(token)
    try:
        data = await _payload(request)
    except (ValueError, TypeError, KeyError, RecursionError, BodyTooLarge) as error:
        reason = str(error) if str(error) == "market_publication_not_confirmed" else "invalid_request"
        return _failure(reason, 400)
    from fastapi import HTTPException

    try:
        credentials = await account._authenticated_market_token()
    except HTTPException:
        return _failure("market_auth_required", 401)
    base = get_market_api_url().rstrip("/")
    if not base:
        return _failure("market_not_connected", 503)
    directory = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="neko-knowledge-publish-"))
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False, trust_env=False) as client:
            path = directory / "artifact.zip"
            checked = await _download(client, path, data["artifact_id"])
            if checked.manifest["package_id"] != data["pack_id"]:
                return _failure("market_identity_mismatch", 409)
            boundary = "neko-knowledge-" + secrets.token_hex(16)
            fields = {"kind": "knowledge", "title": data["title"], "description": data["description"],
                      "client_request_id": data["client_request_id"]}
            prefix = b"".join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                              for key, value in fields.items())
            prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{data["artifact_id"]}.neko-knowledge.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()
            suffix = f"\r\n--{boundary}--\r\n".encode()

            async def body():
                yield prefix
                stream = await asyncio.to_thread(path.open, "rb")
                try:
                    while block := await asyncio.to_thread(stream.read, 64 * 1024):
                        yield block
                finally:
                    await asyncio.to_thread(stream.close)
                yield suffix

            # Credentials only go to the configured market and are never
            # attached to local artifact requests or followed through redirects.
            headers = {"Authorization": "Bearer " + credentials["access_token"],
                       "Content-Type": f"multipart/form-data; boundary={boundary}",
                       "Content-Length": str(len(prefix) + checked.size + len(suffix))}
            async with client.stream("POST", base + "/api/v1/shares", content=body(), headers=headers) as response:
                result = await _json(response)
                if response.status_code == 401:
                    return _failure("market_auth_required", 401)
                if not response.is_success:
                    return _failure("market_publication_failed", response.status_code)
            if (result.get("kind") != "knowledge" or type(result.get("id")) is not int
                    or result.get("file_sha256") != data["artifact_id"]):
                return _failure("market_publication_failed", 502)
            return {"ok": True, "share": {key: result.get(key) for key in ("id", "status", "revision", "file_sha256")}}
    except PackageError as error:
        return _failure(error.reason, 422)
    except (httpx.HTTPError, ValueError, OSError):
        return _failure("market_publication_failed", 503)
    finally:
        # Publication may already be committed upstream; local scratch cleanup
        # must never turn that result into an apparent failed or repeat action.
        with suppress(OSError):
            await asyncio.to_thread(shutil.rmtree, directory)

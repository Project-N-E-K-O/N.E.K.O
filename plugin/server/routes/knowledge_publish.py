"""Publish a confirmed unified artifact using the existing market account."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, suppress
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from uuid import UUID
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from plugin.server.routes import market_bridge as account
from utils.http.knowledge_proxy import BodyTooLarge, capped_body
from utils.knowledge_package import DEFAULT_LIMITS, PackageError, validate_package
from utils.market_config import get_market_api_url

router = APIRouter(prefix="/market/knowledge", tags=["knowledge-market"])
_HASH = re.compile(r"[0-9a-f]{64}")
_PUBLICATION_TIMEOUT_SECONDS = 240.0
_publication_owners: set[asyncio.Task] = set()


def _publication_finished(task: asyncio.Task) -> None:
    """Keep timed-out file owners tracked until cleanup ends, consuming errors."""
    _publication_owners.discard(task)
    if not task.cancelled():
        task.exception()


async def _drain(task):
    """Keep native file owners alive until repeated caller cancellation settles."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _thread(call, *args, _cleanup=None, **kwargs):
    """Drain a cancelled native operation before closing its files or directory."""
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Resource creation can finish after cancellation too. Its result has
        # not yet been assigned to the owner, so close/reclaim it here.
        try:
            result = await _drain(task)
            if _cleanup is not None:
                await _drain(asyncio.create_task(asyncio.to_thread(_cleanup, result)))
        except Exception:
            pass
        raise


def _safe_market_base(base: str) -> bool:
    """Only HTTPS or explicit loopback HTTP may receive credentials and text."""
    try:
        parsed = urlparse(base)
        return bool(parsed.hostname and not parsed.username and not parsed.password
                    and (parsed.port is None or 1 <= parsed.port <= 65535)
                    and not parsed.query and not parsed.fragment
                    and (parsed.scheme == "https" or parsed.scheme == "http"
                         and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))
    except ValueError:
        return False


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
    handle = await _thread(target.open, "xb", _cleanup=lambda stream: stream.close())
    try:
        size, digest = 0, hashlib.sha256()
        async with client.stream("GET", url, params={"artifact_id": artifact_id}) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes(64 * 1024):
                size += len(block)
                if size > DEFAULT_LIMITS.max_archive_bytes:
                    raise ValueError("package_too_large")
                digest.update(block)
                await _thread(handle.write, block)
        if digest.hexdigest() != artifact_id:
            raise ValueError("package_artifact_hash_mismatch")
    finally:
        await _thread(handle.close)
    return await _thread(validate_package, target, expected_sha256=artifact_id)


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
    # Auth refresh shares the operation deadline. A timed-out native validator
    # keeps its tracked file owner until it drains, without delaying the 503.
    owner = asyncio.create_task(_publish_owned(data))
    _publication_owners.add(owner)
    owner.add_done_callback(_publication_finished)
    try:
        async with asyncio.timeout(_PUBLICATION_TIMEOUT_SECONDS):
            return await asyncio.shield(owner)
    except TimeoutError:
        owner.cancel()
        return _failure("market_publication_failed", 503)
    except asyncio.CancelledError:
        owner.cancel()
        with suppress(Exception, asyncio.CancelledError):
            await _drain(owner)
        raise


async def _publish_owned(data: dict):
    """One task owns all scratch files until every native operation has ended."""
    directory = None
    try:
        from fastapi import HTTPException

        base = get_market_api_url().rstrip("/")
        if not _safe_market_base(base):
            return _failure("market_not_connected", 503)
        try:
            credentials = await account._authenticated_market_token()
        except HTTPException:
            return _failure("market_auth_required", 401)
        directory = Path(await _thread(tempfile.mkdtemp, prefix="neko-knowledge-publish-",
                                     _cleanup=lambda folder: shutil.rmtree(folder, ignore_errors=True)))
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
                stream = await _thread(path.open, "rb", _cleanup=lambda stream: stream.close())
                try:
                    while block := await _thread(stream.read, 64 * 1024):
                        yield block
                finally:
                    await _thread(stream.close)
                yield suffix

            # Credentials only go to the configured market and are never
            # attached to local artifact requests or followed through redirects.
            headers = {"Authorization": "Bearer " + credentials["access_token"],
                       "Content-Type": f"multipart/form-data; boundary={boundary}",
                       "Content-Length": str(len(prefix) + checked.size + len(suffix))}
            # An early upstream response may leave the body iterator suspended
            # with an open file. Close it explicitly before reclaiming scratch.
            # Remote HTTPS follows normal environment proxy/NO_PROXY rules.
            # Explicit loopback HTTP markets stay direct, just like the local
            # artifact download, even when NO_PROXY is not configured.
            async with (
                aclosing(body()) as upload,
                httpx.AsyncClient(timeout=180.0, follow_redirects=False,
                                  trust_env=urlparse(base).scheme == "https") as market_client,
            ):
                async with market_client.stream("POST", base + "/api/v1/shares", content=upload, headers=headers) as response:
                    if response.status_code == 401:
                        return _failure("market_auth_required", 401)
                    if not response.is_success:
                        return _failure("market_publication_failed", response.status_code)
                    result = await _json(response)
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
        if directory is not None:
            with suppress(OSError):
                await _thread(shutil.rmtree, directory)

"""Verified local bridge for PluginMarket knowledge subscriptions."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from knowledge.api import (
    MAX_PACK_BYTES,
    SUBSCRIPTION_PROTOCOL_VERSION,
    load_canonical_pack_artifact,
    validate_knowledge_identifier,
)
from plugin.logging_config import get_logger
from plugin.server.routes.market_bridge import (
    _ensure_valid_oauth_token,
    _main_server_port,
    get_bridge_token,
)
from plugin.settings import MARKET_API_URL, NEKO_AUTH_CLIENT_ID


router = APIRouter(prefix="/market/knowledge", tags=["market-knowledge"])
logger = get_logger("server.routes.knowledge_market")
_tasks: dict[str, dict[str, Any]] = {}
# Keep a strong reference to every in-flight background subscription; the
# event loop only holds weak references and would otherwise collect the task
# mid-run, leaving the task permanently "pending".
_background_tasks: set[asyncio.Task[None]] = set()
_TASK_TTL_SECONDS = 60 * 60
_TASK_MAX_ENTRIES = 200
_MAX_REDIRECTS = 5
_ALLOWED_ARTIFACT_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
KnowledgeTaskStatus = Literal[
    "pending",
    "downloading",
    "verifying",
    "installing",
    "completed",
    "failed",
]


def _knowledge_identifier(value: object) -> str:
    try:
        return validate_knowledge_identifier(value)
    except ValueError as exc:
        raise ValueError("invalid knowledge identifier") from exc


class KnowledgeSubscribeRequest(BaseModel):
    package_id: int = Field(gt=0)
    remote_id: str = Field(min_length=11, max_length=74)
    pack_id: str
    version: str = Field(min_length=1, max_length=100)
    channel: Literal["stable", "beta"] = "stable"
    artifact_url: str = Field(min_length=1, max_length=1_000)
    artifact_sha256: str

    @field_validator("remote_id", mode="before")
    @classmethod
    def validate_remote_id(cls, value: object) -> str:
        remote_id = str(value or "").strip()
        if not remote_id.startswith("knowledge/"):
            raise ValueError("remote_id must identify a knowledge package")
        _knowledge_identifier(remote_id.removeprefix("knowledge/"))
        return remote_id

    @field_validator("pack_id", mode="before")
    @classmethod
    def validate_pack_id(cls, value: object) -> str:
        return _knowledge_identifier(value)

    @field_validator("artifact_sha256", mode="before")
    @classmethod
    def validate_sha256(cls, value: object) -> str:
        digest = str(value or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("artifact_sha256 must be a SHA-256 digest")
        return digest


class KnowledgeUnsubscribeRequest(BaseModel):
    package_id: int = Field(gt=0)
    collection: str
    pack_id: str

    @field_validator("collection", "pack_id", mode="before")
    @classmethod
    def validate_identifier(cls, value: object) -> str:
        return _knowledge_identifier(value)


class KnowledgeTaskResponse(BaseModel):
    task_id: str
    status: KnowledgeTaskStatus
    stage: KnowledgeTaskStatus
    progress: float
    message: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None


@router.post("/subscribe")
async def subscribe_knowledge_package(
    payload: KnowledgeSubscribeRequest,
    authorization: str | None = Header(None, alias="Authorization"),
):
    _verify_bridge_token(authorization)
    _validate_artifact_url(payload.artifact_url, require_suffix=True)
    _cleanup_tasks()
    if len(_tasks) >= _TASK_MAX_ENTRIES:
        raise HTTPException(status_code=429, detail="too many knowledge tasks")
    task_id = secrets.token_urlsafe(16)
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "Knowledge subscription task created",
        "result": None,
        "error": None,
        "error_code": None,
        "created_at": time.time(),
        "completed_at": None,
    }
    background = asyncio.create_task(
        _execute_subscription(task_id, payload),
        name=f"market-knowledge-{task_id}",
    )
    _background_tasks.add(background)
    background.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "pending"}


@router.get("/tasks/{task_id}", response_model=KnowledgeTaskResponse)
async def get_knowledge_task(
    task_id: str,
    authorization: str | None = Header(None, alias="Authorization"),
):
    _verify_bridge_token(authorization)
    _cleanup_tasks()
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="knowledge task not found")
    return task


@router.get("/subscriptions")
async def list_local_knowledge_subscriptions(
    authorization: str | None = Header(None, alias="Authorization"),
):
    _verify_bridge_token(authorization)
    collections = await _main_request("GET", "collections")
    if collections.get("ok") is not True:
        raise HTTPException(status_code=503, detail="local knowledge unavailable")
    items: list[dict[str, Any]] = []
    for collection in collections.get("collections", []):
        collection_id = str(collection.get("collection_id") or "")
        if not collection_id:
            continue
        payload = await _main_request(
            "GET",
            "packs",
            params={"collection": collection_id},
        )
        for pack in payload.get("packs", []):
            if isinstance(pack, dict) and isinstance(pack.get("subscription"), dict):
                items.append({"collection": collection_id, **pack})
    return {"ok": True, "subscriptions": items}


@router.post("/unsubscribe")
async def unsubscribe_knowledge_package(
    payload: KnowledgeUnsubscribeRequest,
    authorization: str | None = Header(None, alias="Authorization"),
):
    _verify_bridge_token(authorization)
    result = await _main_request(
        "POST",
        "packs/remove",
        json={"collection": payload.collection, "pack_id": payload.pack_id},
    )
    if result.get("ok") is not True:
        raise HTTPException(status_code=409, detail=_first_issue_code(result))
    try:
        await _report_unsubscribe_best_effort(payload.package_id)
    except Exception as exc:
        logger.warning("knowledge unsubscribe report failed: {}", type(exc).__name__)
    return result


async def _execute_subscription(
    task_id: str,
    payload: KnowledgeSubscribeRequest,
) -> None:
    task = _tasks[task_id]
    try:
        _stage(task, "downloading", 0.15, "Downloading knowledge package")
        raw = await _download_artifact(payload.artifact_url)
        _stage(task, "verifying", 0.55, "Verifying knowledge package")
        digest = hashlib.sha256(raw).hexdigest()
        if not secrets.compare_digest(digest, payload.artifact_sha256):
            raise _KnowledgeTaskError(
                "artifact_hash_mismatch",
                "Knowledge artifact hash mismatch",
            )
        try:
            pack_payload = load_canonical_pack_artifact(raw)
        except ValueError as exc:
            raise _KnowledgeTaskError(
                "invalid_artifact",
                "Knowledge artifact is invalid",
            ) from exc
        if pack_payload.get("pack_id") != payload.pack_id:
            raise _KnowledgeTaskError(
                "package_identity_mismatch",
                "Market record and knowledge package identity differ",
            )
        _stage(task, "installing", 0.75, "Installing local knowledge package")
        result = await _main_request(
            "POST",
            "subscriptions/apply",
            json={
                "protocol_version": SUBSCRIPTION_PROTOCOL_VERSION,
                "subscription": {
                    "provider": "plugin-market",
                    "remote_id": payload.remote_id,
                    "version": payload.version,
                    "channel": payload.channel,
                    "artifact_sha256": payload.artifact_sha256,
                },
                "pack": pack_payload,
            },
            timeout=30.0,
        )
        if result.get("ok") is not True:
            raise _KnowledgeTaskError(
                _first_issue_code(result),
                "Local knowledge service rejected the package",
            )
        task.update(
            result=result,
            status="completed",
            stage="completed",
            progress=1.0,
            message="Knowledge subscription completed",
            completed_at=time.time(),
        )
        try:
            await _report_subscription_best_effort(payload, result)
        except Exception as exc:
            logger.warning("knowledge subscription report failed: {}", type(exc).__name__)
    except _KnowledgeTaskError as exc:
        _fail_task(task, exc.code, exc.message)
    except Exception as exc:
        logger.exception("knowledge subscription task failed: {}", type(exc).__name__)
        _fail_task(task, "internal_error", "Knowledge subscription failed")


async def _download_artifact(url: str) -> bytes:
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    current_url = url
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                _validate_artifact_url(current_url, require_suffix=False)
                async with client.stream(
                    "GET",
                    current_url,
                    headers=headers,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect_count >= _MAX_REDIRECTS:
                            raise _KnowledgeTaskError(
                                "unsafe_artifact_redirect",
                                "Knowledge artifact redirect was rejected",
                            )
                        next_url = urljoin(str(response.url), location)
                        try:
                            _validate_artifact_url(next_url, require_suffix=False)
                        except HTTPException as exc:
                            raise _KnowledgeTaskError(
                                "unsafe_artifact_redirect",
                                "Knowledge artifact redirect was rejected",
                            ) from exc
                        current_url = next_url
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > MAX_PACK_BYTES:
                                raise _KnowledgeTaskError(
                                    "artifact_too_large",
                                    "Knowledge artifact exceeds 10 MiB",
                                )
                        except ValueError as exc:
                            raise _KnowledgeTaskError(
                                "download_failed",
                                "Knowledge artifact response is invalid",
                            ) from exc
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > MAX_PACK_BYTES:
                            raise _KnowledgeTaskError(
                                "artifact_too_large",
                                "Knowledge artifact exceeds 10 MiB",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
    except _KnowledgeTaskError:
        raise
    except httpx.HTTPError as exc:
        raise _KnowledgeTaskError(
            "download_failed",
            "Knowledge artifact download failed",
        ) from exc
    raise _KnowledgeTaskError(
        "unsafe_artifact_redirect",
        "Knowledge artifact redirect was rejected",
    )


async def _main_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    import config

    port = _main_server_port()
    headers = {"Accept": "application/json"}
    if method == "POST":
        headers.update(
            {
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-CSRF-Token": str(config.AUTOSTART_CSRF_TOKEN),
            }
        )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=2.0),
            trust_env=False,
        ) as client:
            response = await client.request(
                method,
                f"http://127.0.0.1:{port}/api/public-knowledge/{path}",
                params=params,
                json=json,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _KnowledgeTaskError(
            "main_server_unavailable",
            "Main Server is unavailable",
        ) from exc
    return payload if isinstance(payload, dict) else {}


async def _report_subscription_best_effort(
    request: KnowledgeSubscribeRequest,
    result: dict[str, Any],
) -> None:
    try:
        token_data = await _ensure_valid_oauth_token()
        if not token_data or not token_data.get("access_token"):
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/me/knowledge-subscriptions",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                json={
                    "package_id": request.package_id,
                    "version": request.version,
                    "channel": request.channel,
                    "artifact_sha256": request.artifact_sha256,
                    "installed_pack_id": result.get("pack_id"),
                    "client_id": NEKO_AUTH_CLIENT_ID,
                },
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("knowledge subscription report failed: {}", type(exc).__name__)


async def _report_unsubscribe_best_effort(package_id: int) -> None:
    try:
        token_data = await _ensure_valid_oauth_token()
        if not token_data or not token_data.get("access_token"):
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/me/knowledge-subscriptions/{package_id}",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("knowledge unsubscribe report failed: {}", type(exc).__name__)


def _verify_bridge_token(authorization: str | None) -> None:
    parts = (authorization or "").split(None, 1)
    candidate = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not candidate or not secrets.compare_digest(
        candidate.encode("utf-8", "surrogatepass"),
        get_bridge_token().encode("utf-8", "surrogatepass"),
    ):
        raise HTTPException(status_code=403, detail="invalid bridge token")


def _validate_artifact_url(url: str, *, require_suffix: bool) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="artifact URL is not allowed") from exc
    if (
        parsed.scheme != "https"
        or port not in {None, 443}
        or parsed.username
        or parsed.password
        or (parsed.hostname or "").lower() not in _ALLOWED_ARTIFACT_HOSTS
        or parsed.fragment
    ):
        raise HTTPException(status_code=400, detail="artifact URL is not allowed")
    if require_suffix and not parsed.path.lower().endswith(".neko-knowledge.json"):
        raise HTTPException(status_code=400, detail="invalid knowledge artifact suffix")


def _first_issue_code(payload: dict[str, Any]) -> str:
    issues = payload.get("issues")
    if isinstance(issues, list) and issues and isinstance(issues[0], dict):
        code = str(issues[0].get("code") or "").strip()
        if code:
            return code[:100]
    return "operation_failed"


def _stage(
    task: dict[str, Any],
    stage: KnowledgeTaskStatus,
    progress: float,
    message: str,
) -> None:
    task.update(status=stage, stage=stage, progress=progress, message=message)


def _fail_task(task: dict[str, Any], code: str, message: str) -> None:
    task.update(
        status="failed",
        stage="failed",
        error=message,
        error_code=code,
        message=message,
        completed_at=time.time(),
    )


def _cleanup_tasks() -> None:
    now = time.time()
    expired = [
        task_id
        for task_id, task in _tasks.items()
        if task.get("completed_at")
        and now - float(task["completed_at"]) > _TASK_TTL_SECONDS
    ]
    for task_id in expired:
        _tasks.pop(task_id, None)


class _KnowledgeTaskError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

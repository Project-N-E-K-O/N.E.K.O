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

"""Knowledge-pack subscriptions from the plugin market.

The Market page (paired through ``/market/pair-code`` + ``/token-exchange``,
or the local manager) asks this server to subscribe to a market package. A
background worker then:

1. reads the version descriptor from the market API,
2. downloads the raw pack artifact (allowlisted hosts, size and SHA-256
   checked; v1 ships no prebuilt vectors, they are computed locally),
3. hands it to Main ``/api/public-knowledge/subscriptions/apply``, which
   streams it to the Memory Server's knowledge subsystem,
4. waits until the staged import is active.

This server never talks to the Memory Server directly: every knowledge read
and write goes through Main's proxy, with Main's CSRF token.

Bounds (kept from the #2951 review):

* At most ``_MAX_ACTIVE_SUBSCRIPTIONS`` workers download or install at once.
  A worker gives its slot back once the pack is staged; the number of live
  workers, including those only waiting for activation, is capped separately
  by ``_MAX_TRACKED_SUBSCRIPTIONS``.
* Terminal import jobs are pruned on the Memory Server. A job that disappears
  while being polled is checked against the installed registry (every field
  of the subscription: package, pack, release and artifact) rather than
  reported as failed.
* "Busy" answers (the write lock, import slots, Main or the Memory Server
  starting) are retried, and if they persist they are reported as a
  retryable ``knowledge_busy``, never as an invalid pack.
* An unsubscribe whose removal outcome is not known within the request
  budget keeps confirming in the background with exponential backoff, while
  the request itself polls at a short fixed interval.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from plugin.logging_config import get_logger
from plugin.server.routes.knowledge_bridge import _client, main_write_headers
from plugin.server.routes.market_bridge import (
    _ensure_valid_oauth_token,
    _main_server_port,
    _verify_token,
)
from plugin.settings import MARKET_API_URL, NEKO_AUTH_CLIENT_ID
from plugin.utils.http_imports import ensure_httpx


router = APIRouter(prefix="/market/knowledge", tags=["market-knowledge"])
logger = get_logger("server.routes.knowledge_market")

MARKET_PROVIDER = "plugin-market"
SUBSCRIPTION_PROTOCOL_VERSION = 1
# Equal to knowledge.models.MAX_PACK_BYTES (the plugin server does not import
# the knowledge package).
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
_PACKAGE_ID_MAX = 9_999_999_999_999_999_999

_MAX_ACTIVE_SUBSCRIPTIONS = 4
_MAX_TRACKED_SUBSCRIPTIONS = 32
_TASK_TTL_SECONDS = 60 * 60
_TASK_MAX_ENTRIES = 200

_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS = 180.0
_MAX_ARTIFACT_REDIRECTS = 5
_ARTIFACT_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_ALLOWED_ARTIFACT_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
_ARTIFACT_SUFFIX = ".neko-knowledge.json"

_MAIN_READ_TIMEOUT_SECONDS = 15.0
_MAIN_WRITE_TIMEOUT_SECONDS = 45.0
_CONNECT_TIMEOUT_SECONDS = 2.0

# Busy answers are retried while the worker keeps its slot (it still holds the
# artifact in memory), backing off up to the cap, for at most this long.
_BUSY_RETRY_FIRST_SECONDS = 2.0
_BUSY_RETRY_MAX_SECONDS = 30.0
_BUSY_RETRY_WINDOW_SECONDS = 10 * 60
# Reasons that mean "try again later", not "this pack is invalid".
_BUSY_REASONS = frozenset(
    {
        "knowledge_busy",
        "job_in_progress",
        "knowledge_starting",
        "knowledge_stopping",
        "knowledge_unavailable",
        "knowledge_timeout",
        "main_server_unavailable",
        "memory_server_unreachable",
    }
)
# Failures of a hop in front of the Memory Server (no answer from Main, or
# Main's proxy timing out or not reaching it): a write's outcome is unknown.
# A refusal the Memory Server itself sent (any other reason) is definite.
_OUTCOME_UNKNOWN_REASONS = frozenset({"main_server_unavailable", "memory_server_unreachable"})
# What Main's /api/public-knowledge proxy answers when its own hop failed;
# the Memory Server sends these reasons with 200, never with these statuses.
_MAIN_HOP_FAILURES = frozenset({(503, "knowledge_unavailable"), (504, "knowledge_timeout")})

# Activation is the full-text index only (vectors are computed afterwards in
# the background), so it is minutes at most even behind a queue of imports.
_JOB_POLL_FIRST_SECONDS = 1.0
_JOB_POLL_MAX_SECONDS = 5.0
_JOB_WAIT_TIMEOUT_SECONDS = 60 * 60

# An unsubscribe is answered within this budget: the Market page reaches this
# server through Main's /market proxy, which gives up after 30 seconds.
_UNSUBSCRIBE_BUDGET_SECONDS = 20.0
_REMOVAL_STATUS_POLL_SECONDS = 0.25
_REMOVAL_DRAIN_MAX_POLL_SECONDS = 30.0
_REMOVAL_DRAIN_SECONDS = 60 * 60
# A removal still "unknown" this long after it was sent never reached the
# Memory Server (its request would have timed out long before).
_REMOVAL_UNKNOWN_GIVE_UP_SECONDS = 2 * 60

_PACK_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeSubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int = Field(gt=0, le=_PACKAGE_ID_MAX)
    version: str = Field(pattern=_VERSION_PATTERN)
    channel: Literal["stable", "beta"] = "stable"
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)


class KnowledgeUnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int = Field(gt=0, le=_PACKAGE_ID_MAX)
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)


class KnowledgeArtifactDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(min_length=1, max_length=1_000)
    sha256: str
    bytes: int = Field(gt=0)

    @field_validator("sha256", mode="before")
    @classmethod
    def _digest(cls, value: object) -> str:
        digest = str(value or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must be a SHA-256 digest")
        return digest


class KnowledgeArtifactSet(BaseModel):
    # Prebuilt index artifacts a descriptor may list are ignored: v1 installs
    # raw text only and computes vectors locally.
    model_config = ConfigDict(extra="ignore")

    knowledge: KnowledgeArtifactDescriptor


class KnowledgeVersionDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol_version: Literal[1]
    package_id: int = Field(gt=0, le=_PACKAGE_ID_MAX)
    remote_id: str = Field(pattern=r"^knowledge/[a-z0-9][a-z0-9._-]{1,99}$")
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)
    material_type: Literal["knowledge", "corpus"]
    version: str = Field(pattern=_VERSION_PATTERN)
    channel: Literal["stable", "beta"]
    artifacts: KnowledgeArtifactSet


class KnowledgeTaskResponse(BaseModel):
    task_id: str
    status: str
    stage: str
    progress: float
    message: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


class _TaskError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ── state ───────────────────────────────────────────────────────────

_tasks: dict[str, dict[str, Any]] = {}
_task_workers: dict[str, asyncio.Task[None]] = {}
# The install request of a task, shielded from the worker's cancellation: a
# request that reached Main may already have staged an import, so whoever
# cancels must learn its result rather than guess.
_installations: dict[str, asyncio.Task[dict[str, Any]]] = {}
_active_package_tasks: dict[int, str] = {}
_unsubscribing: dict[int, asyncio.Task[Any]] = {}
# Background reports to the market (held so they are not collected mid-flight).
_background: set[asyncio.Task[Any]] = set()
# Every wait between polls and retries goes through here (tests record them).
_sleep = asyncio.sleep


# ── Main's knowledge proxy ──────────────────────────────────────────


def _main_url(path: str) -> str:
    return f"http://127.0.0.1:{_main_server_port()}/api/public-knowledge/{path}"


def _write_headers() -> dict[str, str]:
    return {"Accept": "application/json", **main_write_headers(_main_server_port())}


async def _main_call(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """One call to Main's knowledge proxy; the decoded reply, whatever its status.

    Raises ``_TaskError("main_server_unavailable", retryable=True)`` when no
    usable reply arrived (so the outcome of a write is unknown).
    """
    httpx = await ensure_httpx()
    if timeout is None:
        timeout = _MAIN_WRITE_TIMEOUT_SECONDS if method == "POST" else _MAIN_READ_TIMEOUT_SECONDS
    headers = _write_headers() if method == "POST" else {"Accept": "application/json"}
    try:
        response = await _client().request(
            method,
            _main_url(path),
            params=params,
            json=json_body,
            data=data,
            files=files,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout)),
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _TaskError("main_server_unavailable", "Main Server 不可用", retryable=True) from exc
    if not isinstance(payload, dict):
        raise _TaskError("main_server_unavailable", "Main Server 返回了无效响应", retryable=True)
    if response.status_code == 409 and payload.get("error_code") == "storage_startup_blocked":
        # Main or the Memory Server is waiting on storage (startup, migration):
        # the same as starting, so retried rather than taken as a refusal.
        payload = {**payload, "ok": False, "reason": "knowledge_starting"}
    if response.status_code >= 400 and "reason" not in payload:
        # A refusal by Main itself (e.g. CSRF) rather than a knowledge answer.
        payload = {"ok": False, "reason": f"http_{response.status_code}"}
    if (response.status_code, payload.get("reason")) in _MAIN_HOP_FAILURES:
        # Main never got an answer from the Memory Server: not a refusal.
        payload = {**payload, "reason": "memory_server_unreachable"}
    return payload


# ── routes ──────────────────────────────────────────────────────────


@router.post("/subscribe")
async def subscribe_knowledge_package(payload: KnowledgeSubscribeRequest, token: str = Query(...)):
    _verify_token(token)
    _cleanup_tasks()
    if payload.package_id in _unsubscribing:
        raise HTTPException(status_code=409, detail={"code": "knowledge_subscription_conflict"})
    existing_id = _active_package_tasks.get(payload.package_id)
    if existing_id:
        existing = _tasks.get(existing_id) or {}
        if (
            existing.get("version") == payload.version
            and existing.get("channel") == payload.channel
            and existing.get("requested_pack_id") == payload.pack_id
        ):
            return {"task_id": existing_id, "status": existing.get("status", "pending")}
        raise HTTPException(status_code=409, detail={"code": "knowledge_subscription_conflict"})
    if (
        _subscription_slots_in_use() >= _MAX_ACTIVE_SUBSCRIPTIONS
        or len(_task_workers) >= _MAX_TRACKED_SUBSCRIPTIONS
    ):
        raise HTTPException(
            status_code=429,
            detail={"code": "knowledge_subscription_busy"},
            headers={"Retry-After": "5"},
        )
    task_id = secrets.token_urlsafe(16)
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "知识包订阅任务已创建",
        "result": None,
        "error": None,
        "error_code": None,
        "retryable": False,
        "created_at": time.time(),
        "completed_at": None,
        "package_id": payload.package_id,
        "requested_pack_id": payload.pack_id,
        "version": payload.version,
        "channel": payload.channel,
    }
    worker = asyncio.create_task(
        _execute_subscription(task_id, payload), name=f"market-knowledge-{task_id}"
    )
    _task_workers[task_id] = worker
    _active_package_tasks[payload.package_id] = task_id
    worker.add_done_callback(
        lambda done, *, task_id=task_id, package_id=payload.package_id: _subscription_done(
            task_id, package_id, done
        )
    )
    return {"task_id": task_id, "status": "pending"}


@router.get("/tasks/{task_id}", response_model=KnowledgeTaskResponse)
async def get_knowledge_task(task_id: str, token: str = Query(...)):
    _verify_token(token)
    _cleanup_tasks()
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="knowledge task not found")
    return task


@router.get("/subscriptions")
async def list_knowledge_subscriptions(token: str = Query(...)):
    _verify_token(token)
    try:
        payload = await _main_call("GET", "packs")
    except _TaskError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    if payload.get("ok") is not True:
        raise HTTPException(status_code=503, detail={"code": str(payload.get("reason") or "knowledge_unavailable")})
    return {
        "ok": True,
        "subscriptions": [
            pack
            for pack in payload.get("packs", ())
            if isinstance(pack, dict) and _market_subscription(pack) is not None
        ],
    }


@router.post("/unsubscribe")
async def unsubscribe_knowledge_package(payload: KnowledgeUnsubscribeRequest, token: str = Query(...)):
    _verify_token(token)
    if payload.package_id in _unsubscribing:
        raise HTTPException(status_code=409, detail={"code": "knowledge_subscription_conflict"})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _UNSUBSCRIBE_BUDGET_SECONDS
    # Shielded: the caller going away must not leave a removal half-settled.
    settlement = asyncio.create_task(
        _settle_unsubscribe(payload, deadline), name=f"market-knowledge-unsubscribe-{payload.package_id}"
    )
    _unsubscribing[payload.package_id] = settlement
    settlement.add_done_callback(
        lambda done, *, package_id=payload.package_id: _unsubscribe_done(package_id, done)
    )
    return await asyncio.shield(settlement)


# ── subscribe worker ────────────────────────────────────────────────


def _subscription_slots_in_use() -> int:
    """Workers still downloading or installing; ones waiting for activation do not count."""
    return sum(1 for task_id in _task_workers if not _tasks.get(task_id, {}).get("slot_released"))


def _subscription_done(task_id: str, package_id: int, done: asyncio.Task[None]) -> None:
    task = _tasks.get(task_id)
    if done.cancelled() and task is not None and task.get("completed_at") is None:
        _mark_cancelled(task)
    if _task_workers.get(task_id) is done:
        _task_workers.pop(task_id, None)
    if _active_package_tasks.get(package_id) == task_id:
        _active_package_tasks.pop(package_id, None)
    if not done.cancelled() and done.exception() is not None:
        logger.warning("knowledge subscription worker ended with {}", type(done.exception()).__name__)
    _cleanup_tasks()


def _stage(task: dict[str, Any], stage: str, progress: float, message: str) -> None:
    task.update(status=stage, stage=stage, progress=progress, message=message)


def _mark_cancelled(task: dict[str, Any]) -> None:
    task.update(
        status="cancelled",
        stage="cancelled",
        message="知识包订阅已取消",
        error="知识包订阅已取消",
        error_code="cancelled_by_unsubscribe",
        completed_at=time.time(),
    )


def _fail(task: dict[str, Any], code: str, message: str, *, retryable: bool) -> None:
    task.update(
        status="failed",
        stage="failed",
        message=message,
        error=message,
        error_code=code,
        retryable=retryable,
        completed_at=time.time(),
    )


async def _execute_subscription(task_id: str, payload: KnowledgeSubscribeRequest) -> None:
    task = _tasks[task_id]
    try:
        _stage(task, "resolving", 0.05, "正在读取市场版本信息")
        descriptor = await _fetch_version_descriptor(payload)
        task["resolved_pack_id"] = descriptor.pack_id
        task["resolved_remote_id"] = descriptor.remote_id
        _stage(task, "downloading", 0.15, "正在下载知识包")
        raw = await _download_verified_artifact(descriptor.artifacts.knowledge)
        subscription = {
            "provider": MARKET_PROVIDER,
            "provider_package_id": str(descriptor.package_id),
            "remote_id": descriptor.remote_id,
            "pack_id": descriptor.pack_id,
            "version": descriptor.version,
            "channel": descriptor.channel,
            "artifact_sha256": descriptor.artifacts.knowledge.sha256,
            "material_type": descriptor.material_type,
        }
        _stage(task, "installing", 0.6, "正在写入本地知识库")
        result = await _install_with_retry(task, subscription, raw)
        del raw  # the artifact lives in the staged job now; do not hold it while waiting
        job_id = str(result.get("job_id") or "")
        if job_id and result.get("state") != "active":
            # Staged: the bytes live in the Memory Server's job now, so
            # neither they nor a download slot outlive the activation wait.
            task["job_id"] = job_id
            task["slot_released"] = True
            activation = await _wait_for_job(task, job_id=job_id, subscription=subscription)
            result = {**result, "activation": activation}
        task.update(
            result=result,
            status="completed",
            stage="completed",
            progress=1.0,
            message="知识包订阅完成",
            completed_at=time.time(),
        )
        await _report_subscription_best_effort(descriptor, result)
    except asyncio.CancelledError:
        # Cancelled after completing (while reporting to the market, or at
        # shutdown): the subscription did land, so the record stays completed.
        if task.get("completed_at") is None:
            _mark_cancelled(task)
        raise
    except _TaskError as exc:
        _fail(task, exc.code, exc.message, retryable=exc.retryable)
    except Exception as exc:
        logger.exception("knowledge subscription task failed: {}", type(exc).__name__)
        _fail(task, "internal_error", "知识包订阅失败", retryable=False)


async def _install_with_retry(
    task: dict[str, Any], subscription: dict[str, str], raw: bytes
) -> dict[str, Any]:
    """Send the artifact to Main; retry busy answers within the retry window."""
    loop = asyncio.get_running_loop()
    give_up_at = loop.time() + _BUSY_RETRY_WINDOW_SECONDS
    delay = _BUSY_RETRY_FIRST_SECONDS
    while True:
        result = await _shielded_install(task["task_id"], subscription, raw)
        if result.get("ok") is True:
            if result.get("state") == "cancelled":
                # A removal of the pack overtook this import.
                raise _TaskError("job_cancelled", "知识包安装被取消")
            return result
        reason = str(result.get("reason") or "install_failed")
        if reason not in _BUSY_REASONS:
            raise _TaskError(reason, "本地知识库拒绝了该知识包")
        if loop.time() + delay > give_up_at:
            raise _TaskError("knowledge_busy", "本地知识库正忙，请稍后重试", retryable=True)
        task["message"] = "本地知识库正忙，稍后自动重试"
        await _sleep(delay)
        delay = min(delay * 2, _BUSY_RETRY_MAX_SECONDS)


async def _shielded_install(task_id: str, subscription: dict[str, str], raw: bytes) -> dict[str, Any]:
    installation = asyncio.create_task(
        _apply_subscription(subscription, raw), name=f"market-knowledge-install-{task_id}"
    )
    _installations[task_id] = installation

    def settled(done: asyncio.Task[dict[str, Any]], *, task_id: str = task_id) -> None:
        if _installations.get(task_id) is done:
            _installations.pop(task_id, None)
        if not done.cancelled():
            done.exception()

    installation.add_done_callback(settled)
    return await asyncio.shield(installation)


async def _apply_subscription(subscription: dict[str, str], raw: bytes) -> dict[str, Any]:
    try:
        return await _main_call(
            "POST",
            "subscriptions/apply",
            data={
                "protocol_version": str(SUBSCRIPTION_PROTOCOL_VERSION),
                "subscription": json.dumps(subscription, separators=(",", ":"), sort_keys=True),
            },
            files={"pack": ("pack" + _ARTIFACT_SUFFIX, raw, "application/json")},
        )
    except _TaskError as exc:
        return {"ok": False, "reason": exc.code}


async def _wait_for_job(task: dict[str, Any], *, job_id: str, subscription: dict[str, str]) -> dict[str, Any]:
    """Keep the subscription pending until the staged import is active."""
    _stage(task, "indexing", 0.8, "正在建立知识库索引")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _JOB_WAIT_TIMEOUT_SECONDS
    delay = _JOB_POLL_FIRST_SECONDS
    while loop.time() < deadline:
        try:
            payload = await _main_call("GET", "packs/jobs")
        except _TaskError:
            payload = {}
        if payload.get("ok") is True:
            job = next(
                (
                    item
                    for item in payload.get("jobs", ())
                    if isinstance(item, dict) and item.get("job_id") == job_id
                ),
                None,
            )
            if job is None:
                # Terminal jobs are pruned, so an activated one can vanish
                # between two polls. The installed registry is the durable
                # record: accept it only for this exact package, pack and artifact.
                matched = await _installed_pack_matches(subscription)
                if matched:
                    return {"job_id": job_id, "state": "active", "reconciled": "registry"}
                if matched is False:
                    raise _TaskError("job_not_found", "知识包导入任务已不存在")
                # The registry could not be read: no answer yet, keep polling.
                await _sleep(delay)
                delay = min(delay * 2, _JOB_POLL_MAX_SECONDS)
                continue
            state = str(job.get("state") or "")
            if state == "active":
                return job
            if state in ("failed", "cancelled"):
                reason = str(job.get("reason") or "")
                if reason in _BUSY_REASONS:
                    raise _TaskError("knowledge_busy", "本地知识库正忙，请稍后重试", retryable=True)
                raise _TaskError(reason or f"job_{state}", "知识包导入未完成")
        await _sleep(delay)
        delay = min(delay * 2, _JOB_POLL_MAX_SECONDS)
    raise _TaskError("job_timeout", "知识包导入等待超时", retryable=True)


def _market_subscription(pack: dict[str, Any]) -> dict[str, Any] | None:
    subscription = pack.get("subscription")
    if isinstance(subscription, dict) and subscription.get("provider") == MARKET_PROVIDER:
        return subscription
    return None


async def _installed_packs(timeout: float | None = None) -> list[dict[str, Any]] | None:
    """The installed packs, or None when the registry could not be read."""
    try:
        payload = await _main_call("GET", "packs", timeout=timeout)
    except _TaskError:
        return None
    if payload.get("ok") is not True:
        return None
    return [pack for pack in payload.get("packs", ()) if isinstance(pack, dict)]


async def _installed_pack_matches(expected: dict[str, str]) -> bool | None:
    """Whether the installed registry holds exactly this subscription, usable.

    Every field must match: the pack id and bytes alone are not enough (after
    a removal another package could have installed the same bytes under the
    same pack id, and an update that republishes the same bytes as a new
    version or channel would otherwise match the record it was meant to
    replace). A matching pack marked broken does not count: a failed reinstall
    of the same release leaves exactly that record behind.
    None when the registry could not be read: not an answer either way.
    """
    packs = await _installed_packs()
    if packs is None:
        return None
    for pack in packs:
        if pack.get("pack_id") == expected["pack_id"]:
            installed = _market_subscription(pack)
            return (
                installed is not None
                and not pack.get("broken")
                and all(installed.get(field) == value for field, value in expected.items())
            )
    return False


# ── market API and artifact download ────────────────────────────────


async def _fetch_version_descriptor(request: KnowledgeSubscribeRequest) -> KnowledgeVersionDescriptor:
    httpx = await ensure_httpx()
    headers = {"Accept": "application/json"}
    token_data = await _ensure_valid_oauth_token()
    if token_data and token_data.get("access_token"):
        headers["Authorization"] = f"Bearer {token_data['access_token']}"
    url = (
        f"{MARKET_API_URL.rstrip('/')}/api/v1/knowledge/packages/"
        f"{request.package_id}/versions/{quote(request.version, safe='')}"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.get(url, params={"channel": request.channel}, headers=headers)
            response.raise_for_status()
            descriptor = KnowledgeVersionDescriptor.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise _TaskError("catalog_resolution_failed", "无法读取市场版本信息", retryable=True) from exc
    if (
        descriptor.package_id != request.package_id
        or descriptor.pack_id != request.pack_id
        or descriptor.version != request.version
        or descriptor.channel != request.channel
    ):
        raise _TaskError("catalog_identity_mismatch", "市场版本身份不一致")
    return descriptor


def _artifact_url_allowed(url: str, *, final: bool) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or (parsed.hostname or "").lower() not in _ALLOWED_ARTIFACT_HOSTS
    ):
        return False
    # Only the published URL names the file; redirect targets are CDN paths.
    return not final or parsed.path.lower().endswith(_ARTIFACT_SUFFIX)


async def _download_verified_artifact(descriptor: KnowledgeArtifactDescriptor) -> bytes:
    if descriptor.bytes > MAX_ARTIFACT_BYTES:
        raise _TaskError("artifact_too_large", "知识包超过大小限制")
    if not _artifact_url_allowed(descriptor.url, final=True):
        raise _TaskError("unsafe_artifact_url", "知识包地址不受信任")
    httpx = await ensure_httpx()
    try:
        async with asyncio.timeout(_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS):
            raw = await _download(descriptor.url, httpx)
    except TimeoutError as exc:
        raise _TaskError("download_timeout", "知识包下载超时", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise _TaskError("download_failed", "知识包下载失败", retryable=True) from exc
    if len(raw) != descriptor.bytes:
        raise _TaskError("artifact_size_mismatch", "知识包大小与市场登记不一致")
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), descriptor.sha256):
        raise _TaskError("artifact_hash_mismatch", "知识包摘要校验失败")
    return raw


async def _download(url: str, httpx: Any) -> bytes:
    """GET ``url``, following only redirects to allowlisted hosts, at most the size cap."""
    current = url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False
    ) as client:
        for _redirect in range(_MAX_ARTIFACT_REDIRECTS + 1):
            async with client.stream(
                "GET", current, headers={"Accept": "*/*", "Accept-Encoding": "identity"}
            ) as response:
                if response.status_code in _ARTIFACT_REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    target = urljoin(str(response.url), location) if location else ""
                    if not target or not _artifact_url_allowed(target, final=False):
                        raise _TaskError("unsafe_artifact_redirect", "知识包下载发生了不安全的重定向")
                    current = target
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_ARTIFACT_BYTES:
                    raise _TaskError("artifact_too_large", "知识包超过大小限制")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES:
                        raise _TaskError("artifact_too_large", "知识包超过大小限制")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise _TaskError("unsafe_artifact_redirect", "知识包下载重定向次数过多")


async def _report_subscription_best_effort(
    descriptor: KnowledgeVersionDescriptor, result: dict[str, Any]
) -> None:
    try:
        token_data = await _ensure_valid_oauth_token()
        if not token_data or not token_data.get("access_token"):
            return
        httpx = await ensure_httpx()
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/me/knowledge-subscriptions",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                json={
                    "package_id": descriptor.package_id,
                    "version": descriptor.version,
                    "channel": descriptor.channel,
                    "artifact_sha256": descriptor.artifacts.knowledge.sha256,
                    "installed_pack_id": result.get("pack_id") or descriptor.pack_id,
                    "client_id": NEKO_AUTH_CLIENT_ID,
                },
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("knowledge subscription report failed: {}", type(exc).__name__)


async def _report_unsubscribe_best_effort(package_id: int, *, timeout: float = 10.0) -> bool:
    try:
        token_data = await _ensure_valid_oauth_token()
        if not token_data or not token_data.get("access_token"):
            return False
        httpx = await ensure_httpx()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout))
        ) as client:
            response = await client.delete(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/me/knowledge-subscriptions/{package_id}",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("knowledge unsubscribe report failed: {}", type(exc).__name__)
        return False


# ── unsubscribe ─────────────────────────────────────────────────────


def _unsubscribe_done(package_id: int, done: asyncio.Task[Any]) -> None:
    if _unsubscribing.get(package_id) is done:
        _unsubscribing.pop(package_id, None)
    if not done.cancelled():
        done.exception()


def _remaining(deadline: float) -> float:
    return max(deadline - asyncio.get_running_loop().time(), 0.0)


def _unsubscribe_error(code: str, status_code: int = 409, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **extra})


def _cancelled_result() -> dict[str, Any]:
    return {"ok": True, "cancelled": True, "removed": False, "removed_entries": 0, "remote_report": "scheduled"}


def _report_unsubscribe_later(package_id: int) -> None:
    """Tell the market in the background, with the report's own timeout.

    Not out of the request's budget: whatever cancelling and removing left of
    it can be too little for a public TLS request, and once the pack is gone a
    later unsubscribe can no longer resolve it, so the report gets no second
    chance. Not awaited either, so the answer is not delayed past Main's proxy.
    """
    report = asyncio.create_task(
        _report_unsubscribe_best_effort(package_id), name=f"market-knowledge-unsubscribe-report-{package_id}"
    )
    _background.add(report)
    report.add_done_callback(_background.discard)
    # The report takes over the package's "unsubscribing" mark: a subscribe
    # sent right after could otherwise report to the market before this
    # delayed unsubscribe report lands, leaving the market believing the
    # reinstalled package was unsubscribed. Refused (409) until it settles.
    current = asyncio.current_task()
    if current is not None and _unsubscribing.get(package_id) is current:
        _unsubscribing[package_id] = report
        report.add_done_callback(lambda done, *, package_id=package_id: _unsubscribe_done(package_id, done))


async def _settle_unsubscribe(payload: KnowledgeUnsubscribeRequest, deadline: float) -> dict[str, Any]:
    stopped = await _stop_active_subscription(payload, deadline)
    # Always, not only after stopping a worker this process remembers: after a
    # restart the Memory Server may still hold an import staged before it.
    cancelled = await _cancel_package_imports(payload.package_id, payload.pack_id, deadline) or stopped
    pack_id, remote_id = await _resolve_owned_pack(payload, deadline, cancelled=cancelled)
    if not pack_id:
        # Only an install that never landed was stopped; there is nothing to remove.
        _report_unsubscribe_later(payload.package_id)
        return _cancelled_result()
    if _remaining(deadline) <= 0:
        raise _unsubscribe_error("knowledge_busy", 503, retryable=True)
    operation_id = secrets.token_urlsafe(24)
    expected = {"pack_id": pack_id, "provider_package_id": str(payload.package_id), "remote_id": remote_id}
    request = {
        "pack_id": pack_id,
        "expected_subscription": {
            "provider": MARKET_PROVIDER,
            "provider_package_id": str(payload.package_id),
            "remote_id": remote_id,
        },
        "operation_id": operation_id,
    }
    sent_at = asyncio.get_running_loop().time()
    try:
        result = await _main_call(
            "POST", "packs/remove", json_body=request, timeout=max(_remaining(deadline), 0.5)
        )
    except _TaskError:
        result = {"ok": False, "reason": "main_server_unavailable"}
    if result.get("ok") is not True and result.get("reason") in _OUTCOME_UNKNOWN_REASONS:
        # The request or its answer was lost on the way; the removal may
        # still run (the Memory Server finishes it once it arrived). Ask.
        result = await _confirm_removal(operation_id, expected, deadline=deadline)
        if result.get("operation_status") not in ("committed", "failed"):
            _start_removal_drain(payload.package_id, operation_id, expected, sent_at)
            raise _unsubscribe_error("removal_pending", 503, operation_id=operation_id, retryable=True)
    return await _finish_removal(payload.package_id, result)


async def _finish_removal(package_id: int, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("ok") is not True:
        reason = str(result.get("reason") or "subscription_removal_failed")
        if reason in _BUSY_REASONS:
            raise _unsubscribe_error("knowledge_busy", 503, retryable=True)
        raise _unsubscribe_error(reason)
    _report_unsubscribe_later(package_id)
    return {**result, "removed": True, "remote_report": "scheduled"}


async def _stop_active_subscription(payload: KnowledgeUnsubscribeRequest, deadline: float) -> bool:
    """Stop an in-flight subscription of the package; whether one was stopped.

    A worker is cancelled outright, but an install request it already sent is
    waited for (bounded); the import it may have staged is cancelled by the
    caller (``_cancel_package_imports``), so nothing lands after the
    unsubscribe answered.
    """
    task_id = _active_package_tasks.get(payload.package_id)
    task = _tasks.get(task_id) if task_id else None
    if task is None:
        return False
    trusted_pack_id = str(task.get("resolved_pack_id") or task.get("requested_pack_id") or "")
    if trusted_pack_id != payload.pack_id:
        raise _unsubscribe_error("subscription_identity_mismatch")
    worker = _task_workers.get(task_id)
    if worker is not None and not worker.done():
        worker.cancel()
        try:
            async with asyncio.timeout(_remaining(deadline)):
                await asyncio.gather(worker, return_exceptions=True)
        except TimeoutError as exc:
            raise _unsubscribe_error("knowledge_busy", 503, retryable=True) from exc
    installation = _installations.get(task_id)
    if installation is not None:
        try:
            async with asyncio.timeout(_remaining(deadline)):
                await asyncio.gather(asyncio.shield(installation), return_exceptions=True)
        except TimeoutError as exc:
            raise _unsubscribe_error("knowledge_busy", 503, retryable=True) from exc
    return True


async def _cancel_package_imports(package_id: int, pack_id: str, deadline: float) -> bool:
    """Cancel every staged import of this package; wait for any past its commit point to land.

    Found by the package id each job carries, not by a remembered job id: an
    install request whose answer was lost, or one sent before this process
    restarted, may still have staged one. Returns whether there was any.
    """
    cancelled: set[str] = set()
    saw_admission = False
    while _remaining(deadline) > 0:
        try:
            payload = await _main_call("GET", "packs/jobs", timeout=max(_remaining(deadline), 0.5))
        except _TaskError:
            payload = {}
        if payload.get("ok") is True:
            active = [
                job
                for job in payload.get("jobs", ())
                if isinstance(job, dict)
                and job.get("state") in ("queued", "building")
                and isinstance(job.get("subscription"), dict)
                and job["subscription"].get("provider_package_id") == str(package_id)
            ]
            # Arrived but not a job yet (parsed or being staged): cannot be
            # cancelled until it is one, and may still become one, so wait.
            admitting = [
                item
                for item in payload.get("admitting", ())
                if isinstance(item, dict) and item.get("provider_package_id") == str(package_id)
            ]
            if not active and not admitting:
                # None left: the registry now says whether any of them landed.
                return bool(cancelled) or saw_admission
            if any(job["subscription"].get("pack_id") != pack_id for job in active) or any(
                item.get("pack_id") != pack_id for item in admitting
            ):
                raise _unsubscribe_error("subscription_identity_mismatch")
            # Whatever an admission becomes is this unsubscribe's to settle.
            saw_admission = saw_admission or bool(admitting)
            for job in active:
                job_id = str(job.get("job_id") or "")
                if job_id and job_id not in cancelled:
                    cancelled.add(job_id)
                    try:
                        await _main_call(
                            "POST",
                            "packs/jobs/cancel",
                            json_body={"job_id": job_id},
                            timeout=max(_remaining(deadline), 0.5),
                        )
                    except _TaskError:
                        cancelled.discard(job_id)
        await _sleep(min(_REMOVAL_STATUS_POLL_SECONDS, _remaining(deadline)))
    raise _unsubscribe_error("knowledge_busy", 503, retryable=True)


async def _resolve_owned_pack(
    payload: KnowledgeUnsubscribeRequest, deadline: float, *, cancelled: bool
) -> tuple[str, str]:
    """(pack id, remote id) of the installed pack this package owns, or ("", "") after a cancel."""
    try:
        response = await _main_call("GET", "packs", timeout=max(_remaining(deadline), 0.5))
    except _TaskError as exc:
        raise _unsubscribe_error("knowledge_busy", 503, retryable=True) from exc
    if response.get("ok") is not True:
        reason = str(response.get("reason") or "knowledge_unavailable")
        if reason in _BUSY_REASONS:
            raise _unsubscribe_error("knowledge_busy", 503, retryable=True)
        raise _unsubscribe_error(reason)
    matches = [
        pack
        for pack in response.get("packs", ())
        if isinstance(pack, dict)
        and (subscription := _market_subscription(pack)) is not None
        and subscription.get("provider_package_id") == str(payload.package_id)
    ]
    if len(matches) > 1:
        raise _unsubscribe_error("subscription_ownership_unverifiable")
    if not matches:
        if cancelled:
            return "", ""
        raise _unsubscribe_error("subscription_not_found", 404)
    pack = matches[0]
    if pack.get("pack_id") != payload.pack_id:
        raise _unsubscribe_error("subscription_identity_mismatch")
    return str(pack["pack_id"]), str(pack["subscription"].get("remote_id") or "")


async def _removal_status(operation_id: str, expected: dict[str, str], timeout: float) -> dict[str, Any]:
    """One reading of a tracked removal's outcome.

    ``operation_status`` is ``committed`` / ``failed`` (decided), ``pending``,
    ``unknown`` (the Memory Server answered that it does not know the id, and
    the subscription is still installed) or ``unreachable`` (no answer at all,
    which says nothing about the removal).

    An id the Memory Server does not know either never reached it or was
    forgotten when it restarted (outcomes are kept in memory), so the durable
    registry decides: a subscription that is no longer installed was removed.
    """
    try:
        reply = await _main_call(
            "GET", "packs/remove/status", params={"operation_id": operation_id}, timeout=timeout
        )
    except _TaskError:
        return {"ok": False, "operation_status": "unreachable"}
    status = reply.get("operation_status")
    if status not in ("committed", "failed", "pending", "unknown"):
        # Main's hop failed, or the Memory Server is starting: no answer.
        return {**reply, "ok": False, "operation_status": "unreachable"}
    if status != "unknown":
        return reply
    packs = await _installed_packs(timeout=timeout)
    if packs is None:
        return {"ok": False, "operation_status": "unreachable"}
    still_installed = any(
        pack.get("pack_id") == expected["pack_id"]
        and (installed := _market_subscription(pack)) is not None
        and installed.get("provider_package_id") == expected["provider_package_id"]
        and installed.get("remote_id") == expected["remote_id"]
        for pack in packs
    )
    if not still_installed:
        return {"ok": True, "pack_id": expected["pack_id"], "operation_status": "committed", "reconciled": "registry"}
    return reply


async def _confirm_removal(operation_id: str, expected: dict[str, str], *, deadline: float) -> dict[str, Any]:
    """Poll a removal's outcome at a short fixed interval while the request waits."""
    last: dict[str, Any] = {"ok": False, "operation_status": "unreachable"}
    while _remaining(deadline) > 0:
        last = await _removal_status(
            operation_id, expected, timeout=max(min(_MAIN_READ_TIMEOUT_SECONDS, _remaining(deadline)), 0.5)
        )
        if last.get("operation_status") in ("committed", "failed"):
            return last
        await _sleep(min(_REMOVAL_STATUS_POLL_SECONDS, _remaining(deadline)))
    return last


def _start_removal_drain(package_id: int, operation_id: str, expected: dict[str, str], sent_at: float) -> None:
    """Keep confirming a removal after the request answered; report it once committed."""
    current = asyncio.current_task()
    if current is None or _unsubscribing.get(package_id) is not current:
        return
    drain = asyncio.create_task(
        _drain_removal(package_id, operation_id, expected, sent_at),
        name=f"market-knowledge-remove-drain-{package_id}",
    )
    # Hand the package's "unsubscribing" mark to the drain, so a new subscribe
    # cannot race a removal that may still commit.
    _unsubscribing[package_id] = drain
    drain.add_done_callback(lambda done, *, package_id=package_id: _unsubscribe_done(package_id, done))


async def _drain_removal(
    package_id: int, operation_id: str, expected: dict[str, str], sent_at: float
) -> dict[str, Any]:
    """Confirm in the background, backing off, until the outcome is decided.

    Only a definite answer ends it early: committed or failed, or the Memory
    Server still not knowing the removal (with the subscription still
    installed) well after any request carrying it would have timed out.
    Unanswered readings do not count toward that; only the overall bound
    ends a drain that never gets an answer.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REMOVAL_DRAIN_SECONDS
    unknown_until = sent_at + _REMOVAL_UNKNOWN_GIVE_UP_SECONDS
    poll = _REMOVAL_STATUS_POLL_SECONDS
    result: dict[str, Any] = {}
    while loop.time() < deadline:
        result = await _removal_status(operation_id, expected, timeout=_MAIN_READ_TIMEOUT_SECONDS)
        status = result.get("operation_status")
        if status in ("committed", "failed"):
            break
        if status == "unknown" and loop.time() >= unknown_until:
            logger.info("knowledge removal {} never reached the Memory Server", operation_id)
            break
        await _sleep(poll)
        poll = min(poll * 2, _REMOVAL_DRAIN_MAX_POLL_SECONDS)
    else:
        logger.warning("knowledge removal {} got no definite answer before giving up", operation_id)
    if result.get("operation_status") == "committed" and result.get("ok") is True:
        # Once the pack is gone from the registry a later unsubscribe can no
        # longer resolve it, so this is the only chance to tell the market.
        await _report_unsubscribe_best_effort(package_id)
    return result


def _cleanup_tasks() -> None:
    now = time.time()
    for task_id in [
        task_id
        for task_id, task in _tasks.items()
        if task_id not in _task_workers
        and task.get("completed_at")
        and now - float(task["completed_at"]) > _TASK_TTL_SECONDS
    ]:
        _tasks.pop(task_id, None)
    overflow = len(_tasks) - _TASK_MAX_ENTRIES
    if overflow <= 0:
        return
    terminal = sorted(
        (
            (task_id, task)
            for task_id, task in _tasks.items()
            if task_id not in _task_workers and task.get("completed_at") is not None
        ),
        key=lambda item: (float(item[1]["completed_at"]), item[0]),
    )
    for task_id, _task in terminal[:overflow]:
        _tasks.pop(task_id, None)

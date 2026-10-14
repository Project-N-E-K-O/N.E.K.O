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
2. downloads the raw pack artifact (allowlisted hosts and mirrors, size and
   SHA-256 checked; v1 ships no prebuilt vectors, they are computed locally),
3. hands it to Main ``/api/public-knowledge/subscriptions/apply``, which
   streams it to the Memory Server's knowledge subsystem.

The worker ends there. Once the Memory Server has staged the import, the
subscription belongs to it: activation, a failure after staging, removal. A
task therefore ends at ``stage: "staged"``; whether the pack became usable
is read from ``GET /market/knowledge/subscriptions`` (installed packs with
their ``broken`` flag, imports under way, failed imports and their reasons),
which the market page reconciles against. Reports to the market API are
best effort only.

An unsubscribe is one request to Main ``subscriptions/remove`` naming the
pack, the subscription identity and every release of the package known when
it starts (installed, on the way, or being sent by this server). The Memory
Server settles it under its write lock, and repeating it is safe: an answer
that is lost or "busy" is met by sending the same request again, a bounded
number of times within the request. While it runs, a new subscription of the
package is refused (409). This server only cancels its own worker while that
is still resolving or downloading; an apply already sent is waited for, so
the removal reaches the Memory Server after it.

This server never talks to the Memory Server directly: every knowledge read
and write goes through Main's proxy, with Main's CSRF token.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import quote, urljoin, urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from plugin.logging_config import get_logger
from plugin.server.routes.knowledge_bridge import _client, main_write_headers
from plugin.server.routes.market_bridge import (
    _GITHUB_PROXY_SOURCES,
    _ensure_valid_oauth_token,
    _measure_github_proxy_sources,
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

# Workers alive at once (each may hold a downloaded artifact in memory).
_MAX_ACTIVE_SUBSCRIPTIONS = 4
_TASK_TTL_SECONDS = 60 * 60
_TASK_MAX_ENTRIES = 200

_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS = 180.0
_MAX_ARTIFACT_REDIRECTS = 5
_ARTIFACT_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
# GitHub Release mirrors the plugin install path already uses (same list, so
# one place decides which third parties are trusted to relay bytes; the bytes
# are checked against the market's size and SHA-256 either way).
_GITHUB_MIRROR_PREFIXES = tuple(
    base_url for source_id, base_url in _GITHUB_PROXY_SOURCES if source_id != "github-direct"
)
_ALLOWED_ARTIFACT_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    | {(urlparse(prefix).hostname or "").lower() for prefix in _GITHUB_MIRROR_PREFIXES}
)
_ARTIFACT_SUFFIX = ".neko-knowledge.json"
# Each download source gets at most this long; the whole download keeps
# _ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS.
_ARTIFACT_SOURCE_TIMEOUT_SECONDS = 60.0
# How long a mirror speed test stays good for ordering download sources.
_MIRROR_MEASURE_TTL_SECONDS = 10 * 60

_MAIN_READ_TIMEOUT_SECONDS = 15.0
_MAIN_WRITE_TIMEOUT_SECONDS = 45.0
_CONNECT_TIMEOUT_SECONDS = 2.0

# An apply answered "busy" (or not answered) is sent again after each of these
# pauses, within the window; then the task fails as a retryable "busy" and the
# market page may subscribe again. Resending is safe: the Memory Server answers
# a repeat of an apply it already staged with that import.
_APPLY_RETRY_DELAYS = (2.0, 4.0, 8.0, 16.0)
_APPLY_RETRY_WINDOW_SECONDS = 2 * 60
# An unsubscribe is answered within this budget: the Market page reaches this
# server through Main's /market proxy, which gives up after 30 seconds. Its
# removal is sent again after each of these pauses while the budget lasts.
_UNSUBSCRIBE_BUDGET_SECONDS = 20.0
_REMOVE_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
# Reasons that mean "send the same request again later", not "this was
# refused": the Memory Server busy or starting, or no answer at all (a hop in
# front of it failed, so the outcome is unknown; both requests are safe to
# repeat).
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
# What Main's /api/public-knowledge proxy answers when its own hop failed;
# the Memory Server sends these reasons with 200, never with these statuses.
_MAIN_HOP_FAILURES = frozenset({(503, "knowledge_unavailable"), (504, "knowledge_timeout")})
# Import states that may still install a release.
_UNDERWAY_STATES = frozenset({"admitting", "queued", "building"})

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
# A task's apply request, shielded from the worker's cancellation: once sent
# it may stage an import whatever happens here, so an unsubscribe waits for
# it, and its removal then reaches the Memory Server after it.
_applies: dict[str, asyncio.Task[dict[str, Any]]] = {}
# The task holding each package: until its worker and its apply have both
# ended (an apply outlives a cancelled worker; until it settles, a subscribe
# of the package is refused and an unsubscribe still waits for it).
_active_package_tasks: dict[int, str] = {}
_unsubscribing: dict[int, asyncio.Task[Any]] = {}
# Background reports to the market (held so they are not collected mid-flight).
_background: set[asyncio.Task[Any]] = set()
# The latest report to the market per package (see _report_in_order).
_reports: dict[int, asyncio.Task[Any]] = {}
# Every pause between retries goes through here (tests record them).
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
            and existing_id in _task_workers
        ):
            return {"task_id": existing_id, "status": existing.get("status", "pending")}
        raise HTTPException(status_code=409, detail={"code": "knowledge_subscription_conflict"})
    # An apply left running by a cancelled worker still holds its artifact.
    if len(_task_workers.keys() | _applies.keys()) >= _MAX_ACTIVE_SUBSCRIPTIONS:
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
    worker.add_done_callback(lambda done, *, task_id=task_id: _subscription_done(task_id, done))
    return {"task_id": task_id, "status": "pending"}


@router.get("/tasks/{task_id}", response_model=KnowledgeTaskResponse)
async def get_knowledge_task(task_id: str, token: str = Query(...)):
    """One subscribe task; it ends once the Memory Server staged the import.

    ``stage: "staged"`` is not "installed": activation is reconciled through
    ``GET /subscriptions``.
    """
    _verify_token(token)
    _cleanup_tasks()
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="knowledge task not found")
    return task


@router.get("/subscriptions")
async def list_knowledge_subscriptions(token: str = Query(...)):
    """What the market page reconciles against.

    ``subscriptions``: installed market packs (``broken`` ones are not served).
    ``imports``: the Memory Server's subscription imports, under way or
    finished (``failed`` / ``cancelled`` with their ``reason``).
    ``tasks``: this server's subscribe tasks, including those that failed
    before handing the artifact over (resolving, downloading).
    """
    _verify_token(token)
    _cleanup_tasks()
    try:
        payload = await _main_call("GET", "subscriptions")
    except _TaskError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    if payload.get("ok") is not True:
        raise HTTPException(status_code=503, detail={"code": str(payload.get("reason") or "knowledge_unavailable")})
    return {
        "ok": True,
        "subscriptions": [
            pack
            for pack in payload.get("installed", ())
            if isinstance(pack, dict) and _market_subscription(pack) is not None
        ],
        "imports": [
            item
            for item in payload.get("imports", ())
            if isinstance(item, dict) and _market_subscription(item) is not None
        ],
        "tasks": [
            _task_summary(task)
            for task in sorted(_tasks.values(), key=lambda item: item["created_at"], reverse=True)
        ],
    }


@router.post("/unsubscribe")
async def unsubscribe_knowledge_package(payload: KnowledgeUnsubscribeRequest, token: str = Query(...)):
    _verify_token(token)
    if payload.package_id in _unsubscribing:
        raise HTTPException(status_code=409, detail={"code": "knowledge_subscription_conflict"})
    deadline = asyncio.get_running_loop().time() + _UNSUBSCRIBE_BUDGET_SECONDS
    # Its own task, so the caller going away does not end it half-way; while
    # it runs, a subscribe of this package is refused.
    settlement = asyncio.create_task(
        _settle_unsubscribe(payload, deadline), name=f"market-knowledge-unsubscribe-{payload.package_id}"
    )
    _unsubscribing[payload.package_id] = settlement
    settlement.add_done_callback(
        lambda done, *, package_id=payload.package_id: _unsubscribe_done(package_id, done)
    )
    return await asyncio.shield(settlement)


# ── subscribe worker ────────────────────────────────────────────────


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "task_id", "package_id", "requested_pack_id", "version", "channel", "status", "stage",
            "error_code", "retryable", "created_at", "completed_at",
        )
    }


def _subscription_done(task_id: str, done: asyncio.Task[None]) -> None:
    task = _tasks.get(task_id)
    if done.cancelled() and task is not None and task.get("completed_at") is None:
        _mark_cancelled(task)
    if _task_workers.get(task_id) is done:
        _task_workers.pop(task_id, None)
    _release_package(task_id)
    if not done.cancelled() and done.exception() is not None:
        logger.warning("knowledge subscription worker ended with {}", type(done.exception()).__name__)
    _cleanup_tasks()


def _release_package(task_id: str) -> None:
    """Free the task's package once neither its worker nor its apply is running."""
    if task_id in _task_workers or task_id in _applies:
        return
    package_id = (_tasks.get(task_id) or {}).get("package_id")
    if package_id is not None and _active_package_tasks.get(package_id) == task_id:
        _active_package_tasks.pop(package_id, None)


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


def _in_background(coro: Any, name: str) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def _report_in_order(package_id: int, report: Callable[[], Awaitable[Any]], *, name: str) -> None:
    """Report to the market in the background, after the package's previous report.

    A subscribe report that is slow (refreshing the OAuth token, say) must not
    land after the unsubscribe that followed it, or the market would keep a
    subscription that is gone. Each report has its own timeout, so the wait
    is bounded.
    """
    previous = _reports.get(package_id)

    async def run() -> None:
        if previous is not None and not previous.done():
            await asyncio.wait({previous})
        await report()

    task = _in_background(run(), name=name)
    _reports[package_id] = task
    task.add_done_callback(
        lambda done, *, package_id=package_id: _reports.pop(package_id, None)
        if _reports.get(package_id) is done
        else None
    )


async def _execute_subscription(task_id: str, payload: KnowledgeSubscribeRequest) -> None:
    task = _tasks[task_id]
    try:
        _stage(task, "resolving", 0.05, "正在读取市场版本信息")
        descriptor = await _fetch_version_descriptor(payload)
        task["resolved_pack_id"] = descriptor.pack_id
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
        # From here on an apply may reach the Memory Server: an unsubscribe
        # names this release too.
        task["subscription"] = subscription
        _stage(task, "installing", 0.6, "正在交给本地知识库")
        result = await _apply_with_retry(task, subscription, raw)
        task.update(
            result={key: result[key] for key in ("pack_id", "job_id", "state", "unchanged") if key in result},
            status="completed",
            stage="staged",
            progress=1.0,
            message="知识包已交给本地知识库，正在导入",
            completed_at=time.time(),
        )
        _report_in_order(
            descriptor.package_id,
            lambda: _report_subscription_best_effort(descriptor, result),
            name=f"market-knowledge-report-{task_id}",
        )
    except asyncio.CancelledError:
        if task.get("completed_at") is None:
            _mark_cancelled(task)
        raise
    except _TaskError as exc:
        _fail(task, exc.code, exc.message, retryable=exc.retryable)
    except Exception as exc:
        logger.exception("knowledge subscription task failed: {}", type(exc).__name__)
        _fail(task, "internal_error", "知识包订阅失败", retryable=False)


async def _apply_with_retry(
    task: dict[str, Any], subscription: dict[str, str], raw: bytes
) -> dict[str, Any]:
    """Send the artifact to Main; send it again on busy or unanswered, a bounded number of times."""
    loop = asyncio.get_running_loop()
    give_up_at = loop.time() + _APPLY_RETRY_WINDOW_SECONDS
    delays = iter(_APPLY_RETRY_DELAYS)
    while True:
        result = await _shielded_apply(task["task_id"], subscription, raw)
        if result.get("ok") is True:
            if result.get("state") == "cancelled":
                # A removal of the pack overtook this import.
                raise _TaskError("job_cancelled", "知识包安装被取消")
            return result
        reason = str(result.get("reason") or "install_failed")
        if reason not in _BUSY_REASONS:
            raise _TaskError(reason, "本地知识库拒绝了该知识包")
        delay = next(delays, None)
        if delay is None or loop.time() + delay > give_up_at:
            raise _TaskError("knowledge_busy", "本地知识库正忙，请稍后重试", retryable=True)
        task["message"] = "本地知识库正忙，稍后自动重试"
        await _sleep(delay)


async def _shielded_apply(task_id: str, subscription: dict[str, str], raw: bytes) -> dict[str, Any]:
    apply = asyncio.create_task(
        _apply_subscription(subscription, raw), name=f"market-knowledge-apply-{task_id}"
    )
    _applies[task_id] = apply

    def settled(done: asyncio.Task[dict[str, Any]], *, task_id: str = task_id) -> None:
        if _applies.get(task_id) is done:
            _applies.pop(task_id, None)
            _release_package(task_id)
        if not done.cancelled():
            done.exception()

    apply.add_done_callback(settled)
    return await asyncio.shield(apply)


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


def _market_subscription(item: dict[str, Any]) -> dict[str, Any] | None:
    subscription = item.get("subscription")
    if isinstance(subscription, dict) and subscription.get("provider") == MARKET_PROVIDER:
        return subscription
    return None


# ── market API and artifact download ────────────────────────────────


async def _fetch_version_descriptor(request: KnowledgeSubscribeRequest) -> KnowledgeVersionDescriptor:
    """Read the version descriptor, signed in when a market login is saved.

    Like the other market calls: a 401 for a saved token (revoked before its
    recorded expiry) refreshes it once and asks again; if no fresh token is
    to be had, it asks without one, so a public package never fails on a
    stale login. A 401 even then means the package needs a login.
    """
    httpx = await ensure_httpx()
    token_data = await _ensure_valid_oauth_token()
    access_token = str(token_data.get("access_token") or "") if token_data else ""
    url = (
        f"{MARKET_API_URL.rstrip('/')}/api/v1/knowledge/packages/"
        f"{request.package_id}/versions/{quote(request.version, safe='')}"
    )

    async def get(client: Any, token: str) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return await client.get(url, params={"channel": request.channel}, headers=headers)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await get(client, access_token)
            if response.status_code == 401 and access_token:
                try:
                    refreshed = await _ensure_valid_oauth_token(
                        force_refresh=True, rejected_access_token=access_token
                    )
                except HTTPException:
                    # The Auth service failing (unreachable, 5xx) is no fresh
                    # token either: ask without one.
                    refreshed = None
                fresh = str(refreshed.get("access_token") or "") if refreshed else ""
                response = await get(client, fresh if fresh != access_token else "")
            if response.status_code == 401:
                raise _TaskError("market_auth_required", "该知识包需要登录插件市场", retryable=True)
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


# "at" / "sources": the latest result; "task": the measurement in flight.
_mirror_cache: dict[str, Any] = {}


async def _measured_github_sources() -> tuple[dict[str, object], ...]:
    """Latest mirror speed test (shared with the plugin install path), reused for a while.

    Workers that find no fresh result share one measurement instead of each
    probing every mirror.
    """
    loop = asyncio.get_running_loop()
    at = _mirror_cache.get("at")
    if at is not None and loop.time() - at < _MIRROR_MEASURE_TTL_SECONDS:
        return _mirror_cache["sources"]
    task = _mirror_cache.get("task")
    if task is None or task.done():
        task = asyncio.create_task(_measure_github_proxy_sources(), name="knowledge-mirror-measure")
        _mirror_cache["task"] = task
    measured = await asyncio.shield(task)
    if _mirror_cache.get("task") is task:
        _mirror_cache.update(at=loop.time(), sources=measured, task=None)
    return measured


def _is_github_release_asset(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "github.com" and "/releases/download/" in parsed.path


async def _artifact_sources(url: str) -> list[str]:
    """Where to try downloading ``url`` from, best first.

    A GitHub Release asset is also tried through the mirrors the plugin install
    path uses (GitHub is often unreachable from mainland China), ordered by the
    latest speed test, with GitHub itself among them and, failing a usable
    test, last. Any other URL is downloaded as published.
    """
    if not _is_github_release_asset(url):
        return [url]
    try:
        measured = await _measured_github_sources()
    except Exception as exc:
        logger.info("mirror speed test failed: {}", type(exc).__name__)
        measured = ()
    usable = sorted(
        (item for item in measured if item.get("available") and isinstance(item.get("latency_ms"), int)),
        key=lambda item: item["latency_ms"],
    )
    sources: list[str] = []
    for item in usable:
        base = str(item.get("url") or "")
        source = url if item.get("id") == "github-direct" else f"{base}{url}"
        if (base in _GITHUB_MIRROR_PREFIXES or item.get("id") == "github-direct") and source not in sources:
            sources.append(source)
    mirrors = [f"{prefix}{url}" for prefix in _GITHUB_MIRROR_PREFIXES]
    # Without a usable test, GitHub itself comes right after the first mirror:
    # each source may take its whole timeout, so last could mean never tried.
    fallback = [*mirrors, url] if usable else [*mirrors[:1], url, *mirrors[1:]]
    for source in fallback:
        if source not in sources:
            sources.append(source)
    return sources


async def _download_verified_artifact(descriptor: KnowledgeArtifactDescriptor) -> bytes:
    """Download the artifact from the first source that delivers exactly its bytes.

    A source that fails, redirects off the allowlist, or serves other bytes
    (size or SHA-256 differing from the market's record) is skipped for the
    next one; a mirror can therefore never substitute content.
    """
    if descriptor.bytes > MAX_ARTIFACT_BYTES:
        raise _TaskError("artifact_too_large", "知识包超过大小限制")
    if not _artifact_url_allowed(descriptor.url, final=True):
        raise _TaskError("unsafe_artifact_url", "知识包地址不受信任")
    httpx = await ensure_httpx()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS
    last = _TaskError("download_failed", "知识包下载失败", retryable=True)
    for source in await _artifact_sources(descriptor.url):
        remaining = deadline - loop.time()
        if remaining <= 0:
            last = _TaskError("download_timeout", "知识包下载超时", retryable=True)
            break
        try:
            async with asyncio.timeout(min(remaining, _ARTIFACT_SOURCE_TIMEOUT_SECONDS)):
                raw = await _download(source, httpx)
        except TimeoutError:
            last = _TaskError("download_timeout", "知识包下载超时", retryable=True)
            continue
        except httpx.HTTPError:
            last = _TaskError("download_failed", "知识包下载失败", retryable=True)
            continue
        except _TaskError as exc:
            last = exc
            continue
        if len(raw) != descriptor.bytes:
            last = _TaskError("artifact_size_mismatch", "知识包大小与市场登记不一致")
            continue
        if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), descriptor.sha256):
            last = _TaskError("artifact_hash_mismatch", "知识包摘要校验失败")
            continue
        return raw
    raise last


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


def _busy() -> HTTPException:
    return _unsubscribe_error("knowledge_busy", 503, retryable=True)


def _report_unsubscribe_later(package_id: int) -> None:
    """Tell the market in the background, with the report's own timeout.

    Not out of the request's budget, which may be nearly spent by now, and not
    awaited, so the answer is not delayed past Main's proxy. The market page
    reconciles anyway; this only saves it a round.
    """
    _report_in_order(
        package_id,
        lambda: _report_unsubscribe_best_effort(package_id),
        name=f"market-knowledge-unsubscribe-report-{package_id}",
    )


async def _settle_unsubscribe(payload: KnowledgeUnsubscribeRequest, deadline: float) -> dict[str, Any]:
    sending = await _stop_worker(payload, deadline)
    request = _removal_request(payload, await _subscription_state(deadline), sending)
    if request is None:
        # Nothing of this package here (never installed, or removed locally,
        # which the management page does not tell the market): the market's
        # record must go all the same.
        _report_unsubscribe_later(payload.package_id)
        return {"ok": True, "pack_id": payload.pack_id, "removed": False, "kept": "not_installed",
                "removed_entries": 0, "remote_report": "scheduled"}
    result = await _remove_with_retry(request, deadline)
    kept = str(result.get("kept") or "")
    if kept != "other_release":
        # Removed, or nothing left to remove. A release this request did not
        # name is a later subscription: the market keeps that one.
        _report_unsubscribe_later(payload.package_id)
    return {
        "ok": True,
        "pack_id": payload.pack_id,
        "removed": result.get("removed") is True,
        "kept": kept or None,
        "removed_entries": int(result.get("removed_entries") or 0),
        "remote_report": "scheduled" if kept != "other_release" else "skipped",
    }


async def _stop_worker(payload: KnowledgeUnsubscribeRequest, deadline: float) -> dict[str, str] | None:
    """Stop this server's worker for the package; the subscription it may have sent, if any.

    Cancelling ends it while it resolves or downloads. An apply it already
    sent is waited for (bounded): what that apply staged is then visible to
    the Memory Server, and the removal sent after it covers it.
    """
    task_id = _active_package_tasks.get(payload.package_id)
    task = _tasks.get(task_id) if task_id else None
    if task_id is None or task is None:
        return None
    trusted_pack_id = str(task.get("resolved_pack_id") or task.get("requested_pack_id") or "")
    if trusted_pack_id != payload.pack_id:
        raise _unsubscribe_error("subscription_identity_mismatch")
    pending = [
        item
        for item in (_task_workers.get(task_id), _applies.get(task_id))
        if item is not None and not item.done()
    ]
    worker = _task_workers.get(task_id)
    if worker is not None:
        worker.cancel()
    if pending:
        _done, still = await asyncio.wait(pending, timeout=_remaining(deadline))
        if still:
            raise _busy()
    return task.get("subscription")


async def _subscription_state(deadline: float) -> dict[str, Any]:
    try:
        reply = await _main_call(
            "GET", "subscriptions", timeout=max(min(_MAIN_READ_TIMEOUT_SECONDS, _remaining(deadline)), 0.5)
        )
    except _TaskError as exc:
        raise _busy() from exc
    if reply.get("ok") is not True:
        reason = str(reply.get("reason") or "knowledge_unavailable")
        if reason in _BUSY_REASONS:
            raise _busy()
        raise _unsubscribe_error(reason)
    return reply


def _removal_request(
    payload: KnowledgeUnsubscribeRequest, state: dict[str, Any], sending: dict[str, str] | None
) -> dict[str, Any] | None:
    """The removal of every release of the package known now, or None if there is none.

    Known releases: the installed one, imports still under way, and the one
    this server's worker was sending. The request is built once and sent
    as-is on every retry, so a retry never covers a later subscription.
    """
    package_id = str(payload.package_id)
    found = [
        subscription
        for subscription in (
            *(_market_subscription(pack) for pack in state.get("installed", ()) if isinstance(pack, dict)),
            *(
                _market_subscription(item)
                for item in state.get("imports", ())
                if isinstance(item, dict) and item.get("state") in _UNDERWAY_STATES
            ),
            sending,
        )
        if subscription is not None and subscription.get("provider_package_id") == package_id
    ]
    if not found:
        return None
    remote_ids = {str(item.get("remote_id") or "") for item in found}
    if any(item.get("pack_id") != payload.pack_id for item in found) or len(remote_ids) != 1:
        raise _unsubscribe_error("subscription_identity_mismatch")
    releases = sorted({(str(item.get("version") or ""), str(item.get("artifact_sha256") or "")) for item in found})
    return {
        "protocol_version": SUBSCRIPTION_PROTOCOL_VERSION,
        "pack_id": payload.pack_id,
        "expected_subscription": {
            "provider": MARKET_PROVIDER,
            "provider_package_id": package_id,
            "remote_id": remote_ids.pop(),
        },
        "releases": [{"version": version, "artifact_sha256": sha} for version, sha in releases],
    }


async def _remove_with_retry(request: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Send the removal; send the same request again on busy or unanswered, within the budget."""
    delays = iter(_REMOVE_RETRY_DELAYS)
    while True:
        try:
            result = await _main_call(
                "POST", "subscriptions/remove", json_body=request, timeout=max(_remaining(deadline), 0.5)
            )
        except _TaskError:
            result = {"ok": False, "reason": "main_server_unavailable"}
        if result.get("ok") is True:
            return result
        reason = str(result.get("reason") or "subscription_removal_failed")
        if reason not in _BUSY_REASONS:
            raise _unsubscribe_error(reason)
        delay = next(delays, None)
        if delay is None or _remaining(deadline) <= delay:
            raise _busy()
        await _sleep(delay)


def _cleanup_tasks() -> None:
    now = time.time()
    for task_id in [
        task_id
        for task_id, task in _tasks.items()
        if task_id not in _task_workers
        and task_id not in _applies
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
            # An apply left running by a cancelled worker still needs its task
            # (its package is released from it when the apply settles).
            if task_id not in _task_workers and task_id not in _applies and task.get("completed_at") is not None
        ),
        key=lambda item: (float(item[1]["completed_at"]), item[0]),
    )
    for task_id, _task in terminal[:overflow]:
        _tasks.pop(task_id, None)

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import time
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge.subscriptions import (
    PROVIDER_PACKAGE_ID_MAX,
    SUBSCRIPTION_PROTOCOL_VERSION,
    load_canonical_pack_artifact,
)
from knowledge.limits import MAX_PACK_BYTES
from knowledge.timeouts import KNOWLEDGE_PLUGIN_TO_MAIN_MUTATION_TIMEOUT_SECONDS
from plugin.logging_config import get_logger
from plugin.settings import MARKET_API_URL, NEKO_AUTH_CLIENT_ID
from plugin.server.routes.market_bridge import (
    KNOWLEDGE_GET_TIMEOUT_SECONDS,
    KNOWLEDGE_POST_TIMEOUT_SECONDS,
    _ensure_valid_oauth_token,
    _main_server_port,
    get_bridge_token,
    invalid_bridge_token_error,
)


router = APIRouter(prefix="/market/knowledge", tags=["market-knowledge"])

# The Main Server proxy wraps this whole request in
# KNOWLEDGE_MAIN_TO_PLUGIN_MUTATION_TIMEOUT_SECONDS. Settlement runs shielded, so
# without a budget of its own it can keep going after the proxy has already
# returned 504 and still remove the pack — the user sees a failed operation whose
# durable result changes afterwards. Stay strictly under the proxy so the caller
# always learns the real outcome.
_UNSUBSCRIBE_TOTAL_BUDGET_SECONDS = KNOWLEDGE_PLUGIN_TO_MAIN_MUTATION_TIMEOUT_SECONDS
_CONNECT_TIMEOUT_SECONDS = 2.0


def _remaining_budget(deadline: float) -> float:
    return max(deadline - asyncio.get_running_loop().time(), 0.0)
logger = get_logger("server.routes.knowledge_market")
_tasks: dict[str, dict[str, Any]] = {}
_task_workers: dict[str, asyncio.Task[None]] = {}
_installation_mutations: dict[str, asyncio.Task[dict[str, Any]]] = {}
_active_package_tasks: dict[int, str] = {}
_unsubscribing_package_ids: set[int] = set()
_unsubscribe_settlements: dict[int, asyncio.Task[dict[str, Any]]] = {}
_TASK_TTL_SECONDS = 60 * 60
_TASK_MAX_ENTRIES = 200
_MAX_ACTIVE_SUBSCRIPTIONS = 4
_JOB_POLL_SECONDS = 5.0
_JOB_WAIT_TIMEOUT_SECONDS = 24 * 60 * 60
_MAX_INDEX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_VECTOR_BYTES = 5_000 * 256 * 2
_MAX_ARTIFACT_REDIRECTS = 5
_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS = 180.0
_ARTIFACT_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_ALLOWED_ARTIFACT_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class KnowledgeSubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int = Field(gt=0, le=PROVIDER_PACKAGE_ID_MAX)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
    channel: Literal["stable", "beta"] = "stable"
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")


class KnowledgeArtifactDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=1_000)
    sha256: str
    bytes: int = Field(gt=0)

    @field_validator("sha256", mode="before")
    @classmethod
    def validate_sha256(cls, value: object) -> str:
        digest = str(value or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("artifact_sha256 must be a SHA-256 digest")
        return digest


class KnowledgeArtifactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge: KnowledgeArtifactDescriptor
    index_manifest: KnowledgeArtifactDescriptor | None = None
    vectors: KnowledgeArtifactDescriptor | None = None


class KnowledgeVersionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1]
    package_id: int = Field(gt=0, le=PROVIDER_PACKAGE_ID_MAX)
    remote_id: str = Field(pattern=r"^knowledge/[a-z0-9][a-z0-9._-]{1,99}$")
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    material_type: Literal["knowledge", "corpus"]
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
    channel: Literal["stable", "beta"]
    artifacts: KnowledgeArtifactSet


class KnowledgeUnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int = Field(gt=0, le=PROVIDER_PACKAGE_ID_MAX)
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")


class KnowledgeTaskResponse(BaseModel):
    task_id: str
    status: str
    stage: str
    progress: float
    message: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None


@router.post("/subscribe")
async def subscribe_knowledge_package(
    payload: KnowledgeSubscribeRequest,
    token: str = Query(...),
):
    _verify_bridge_token(token)
    _cleanup_tasks()
    if payload.package_id in _unsubscribing_package_ids:
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_subscription_conflict"},
        )
    existing_id = _active_package_tasks.get(payload.package_id)
    if existing_id:
        existing = _tasks.get(existing_id)
        if existing and (
            existing.get("version") == payload.version
            and existing.get("channel") == payload.channel
        ):
            return {"task_id": existing_id, "status": existing.get("status", "pending")}
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_subscription_conflict"},
        )
    if len(_task_workers) >= _MAX_ACTIVE_SUBSCRIPTIONS:
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
        "created_at": time.time(),
        "completed_at": None,
        "package_id": payload.package_id,
        "requested_pack_id": payload.pack_id,
        "version": payload.version,
        "channel": payload.channel,
    }
    worker = asyncio.create_task(
        _execute_subscription(task_id, payload),
        name=f"market-knowledge-{task_id}",
    )
    _task_workers[task_id] = worker
    _active_package_tasks[payload.package_id] = task_id
    worker.add_done_callback(
        lambda completed, *, task_id=task_id, package_id=payload.package_id:
        _subscription_done(task_id, package_id, completed)
    )
    _cleanup_tasks()
    return {"task_id": task_id, "status": "pending"}


def _subscription_done(
    task_id: str,
    package_id: int,
    completed: asyncio.Task[None],
) -> None:
    if completed.cancelled():
        task = _tasks.get(task_id)
        if task is not None and task.get("completed_at") is None:
            _mark_subscription_cancelled(task)
    if _task_workers.get(task_id) is completed:
        _task_workers.pop(task_id, None)
    if _active_package_tasks.get(package_id) == task_id:
        _active_package_tasks.pop(package_id, None)
    if not completed.cancelled():
        try:
            completed.exception()
        except Exception:
            logger.exception("failed to consume knowledge subscription task result")
    _cleanup_tasks()


def _installation_outcome_of(
    completed: asyncio.Task[dict[str, Any]],
) -> Literal["accepted", "rejected", "failed", "cancelled"]:
    if completed.cancelled():
        return "cancelled"
    failure = completed.exception()
    if failure is not None:
        return "failed"
    result = completed.result()
    return (
        "accepted"
        if isinstance(result, dict) and result.get("ok") is True
        else "rejected"
    )


def _installation_mutation_done(
    task_id: str,
    completed: asyncio.Task[dict[str, Any]],
) -> None:
    if _installation_mutations.get(task_id) is completed:
        _installation_mutations.pop(task_id, None)
    outcome = _installation_outcome_of(completed)
    task = _tasks.get(task_id)
    if task is not None:
        task["_installation_outcome"] = outcome
    if outcome == "failed":
        failure = completed.exception()
        assert failure is not None
        logger.error(
            "knowledge installation mutation failed",
            exc_info=(type(failure), failure, failure.__traceback__),
        )


def _mark_subscription_cancelled(task: dict[str, Any]) -> None:
    task.update(
        status="cancelled",
        stage="cancelled",
        message="知识包订阅已取消",
        error="知识包订阅已取消",
        error_code="cancelled_by_unsubscribe",
        completed_at=time.time(),
    )


@router.get("/tasks/{task_id}", response_model=KnowledgeTaskResponse)
async def get_knowledge_task(task_id: str, token: str = Query(...)):
    _verify_bridge_token(token)
    _cleanup_tasks()
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="knowledge task not found")
    return task


@router.get("/subscriptions")
async def list_local_knowledge_subscriptions(token: str = Query(...)):
    _verify_bridge_token(token)
    payload = await _main_request("GET", "packs")
    items = [
        pack
        for pack in payload.get("packs", [])
        if isinstance(pack, dict) and isinstance(pack.get("subscription"), dict)
    ]
    return {"ok": True, "subscriptions": items}


@router.post("/unsubscribe")
async def unsubscribe_knowledge_package(
    payload: KnowledgeUnsubscribeRequest,
    token: str = Query(...),
):
    _verify_bridge_token(token)
    if payload.package_id in _unsubscribing_package_ids:
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_subscription_conflict"},
        )
    _unsubscribing_package_ids.add(payload.package_id)
    try:
        deadline = (
            asyncio.get_running_loop().time() + _UNSUBSCRIBE_TOTAL_BUDGET_SECONDS
        )
        settlement = asyncio.create_task(
            _settle_knowledge_unsubscribe(payload, deadline=deadline),
            name=f"market-knowledge-unsubscribe-{payload.package_id}",
        )
    except Exception:
        _unsubscribing_package_ids.discard(payload.package_id)
        raise
    _unsubscribe_settlements[payload.package_id] = settlement
    settlement.add_done_callback(
        lambda completed, *, package_id=payload.package_id:
        _unsubscribe_settlement_done(package_id, completed)
    )
    return await asyncio.shield(settlement)


def _unsubscribe_settlement_done(
    package_id: int,
    completed: asyncio.Task[dict[str, Any]],
) -> None:
    if _unsubscribe_settlements.get(package_id) is completed:
        _unsubscribe_settlements.pop(package_id, None)
    _unsubscribing_package_ids.discard(package_id)
    if not completed.cancelled():
        completed.exception()


async def _settle_knowledge_unsubscribe(
    payload: KnowledgeUnsubscribeRequest,
    *,
    deadline: float,
) -> dict[str, Any]:
    active_task = await _cancel_active_subscription(
        payload.package_id,
        claimed_pack_id=payload.pack_id,
        deadline=deadline,
    )
    if active_task is not None and active_task.get("preinstall_cancelled") is True:
        await _report_unsubscribe_best_effort(payload.package_id)
        return _preinstall_cancellation_result()
    pack_id, remote_id = await _resolve_owned_subscription(
        package_id=payload.package_id,
        claimed_pack_id=payload.pack_id,
        active_task=active_task,
    )
    removal_budget = _remaining_budget(deadline)
    if removal_budget <= 0:
        # Waiting on the in-flight install ate the whole budget. Sending a request
        # that cannot finish would just surface as a confusing transport error.
        raise HTTPException(
            status_code=503,
            detail={"code": "knowledge_installation_busy"},
        )
    try:
        result = await _main_request(
            "POST",
            "packs/remove",
            timeout=removal_budget,
            json={
                "pack_id": pack_id,
                "expected_provider": "plugin-market",
                "expected_provider_package_id": str(payload.package_id),
                "expected_remote_id": remote_id,
            },
        )
    except _KnowledgeTaskError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code},
        ) from exc
    if result.get("ok") is not True:
        code = str(result.get("reason") or "subscription_not_found")
        if (
            code == "not_found"
            and active_task is not None
            and active_task.get("_installation_settled_for_unsubscribe") is True
        ):
            active_task["preinstall_cancelled"] = True
            await _report_unsubscribe_best_effort(payload.package_id)
            return _preinstall_cancellation_result()
        raise HTTPException(status_code=409, detail={"code": code})
    await _report_unsubscribe_best_effort(payload.package_id)
    return result


async def _cancel_active_subscription(
    package_id: int,
    *,
    claimed_pack_id: str,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    task_id = _active_package_tasks.get(package_id)
    task = _tasks.get(task_id) if task_id else None
    if task is None:
        retained_tasks = tuple(
            candidate
            for candidate in _tasks.values()
            if candidate.get("package_id") == package_id
        )
        if not retained_tasks:
            return None
        created_at_values = tuple(
            candidate.get("created_at") for candidate in retained_tasks
        )
        if any(
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or not math.isfinite(float(created_at))
            for created_at in created_at_values
        ) or any(
            float(earlier) > float(later)
            for earlier, later in zip(created_at_values, created_at_values[1:])
        ):
            # Retained task ordering is only trusted when its timestamp is
            # structurally valid. Fall back to the durable pack registry.
            return None
        latest_task = retained_tasks[-1]
        if latest_task.get("preinstall_cancelled") is True:
            trusted_pack_id = str(
                latest_task.get("resolved_pack_id")
                or latest_task.get("requested_pack_id")
                or ""
            )
            if trusted_pack_id != claimed_pack_id:
                _raise_unsubscribe_error("subscription_identity_mismatch")
        return latest_task
    resolved_pack_id = str((task or {}).get("resolved_pack_id") or "")
    requested_pack_id = str((task or {}).get("requested_pack_id") or "")
    trusted_pack_id = resolved_pack_id or requested_pack_id
    if trusted_pack_id and trusted_pack_id != claimed_pack_id:
        _raise_unsubscribe_error("subscription_identity_mismatch")
    stage = str((task or {}).get("stage") or "")
    preinstall = stage in {"pending", "resolving", "downloading", "verifying"}
    if preinstall and not trusted_pack_id:
        _raise_unsubscribe_error("subscription_ownership_unverifiable")
    worker = _task_workers.get(task_id) if task_id else None
    installation_mutation = (
        _installation_mutations.get(task_id) if task_id else None
    )
    if worker is not None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    if installation_mutation is not None:
        # Shielded on purpose: a running installation must finish rather than be
        # torn in half. Bounded on purpose too: if it outlives our budget we stop
        # waiting and report busy, instead of pushing the caller past the proxy
        # timeout and settling behind its back.
        waiter = asyncio.gather(
            asyncio.shield(installation_mutation),
            return_exceptions=True,
        )
        if deadline is None:
            await waiter
        else:
            try:
                await asyncio.wait_for(waiter, timeout=_remaining_budget(deadline))
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "knowledge_installation_busy"},
                ) from exc
        installation_outcome = _installation_outcome_of(installation_mutation)
        if task is not None:
            task["_installation_outcome"] = installation_outcome
    else:
        installation_outcome = str((task or {}).get("_installation_outcome") or "")
    if task is not None and stage == "installing" and installation_outcome:
        task["_installation_settled_for_unsubscribe"] = True
        if installation_outcome == "rejected":
            task["preinstall_cancelled"] = True
    if preinstall and task is not None:
        task["preinstall_cancelled"] = True
    return task


def _preinstall_cancellation_result() -> dict[str, object]:
    return {
        "ok": True,
        "cancelled": True,
        "removed": False,
        "removed_pack": False,
        "removed_entries": 0,
        "cancelled_jobs": 0,
    }


async def _resolve_owned_subscription(
    *,
    package_id: int,
    claimed_pack_id: str,
    active_task: dict[str, Any] | None,
) -> tuple[str, str]:
    if active_task is not None:
        resolved_pack_id = str(active_task.get("resolved_pack_id") or "")
        if resolved_pack_id:
            if resolved_pack_id != claimed_pack_id:
                _raise_unsubscribe_error("subscription_identity_mismatch")
            resolved_remote_id = str(active_task.get("resolved_remote_id") or "")
            if not resolved_remote_id:
                _raise_unsubscribe_error("subscription_ownership_unverifiable")
            return resolved_pack_id, resolved_remote_id

    try:
        response = await _main_request("GET", "packs")
    except _KnowledgeTaskError:
        _raise_unsubscribe_error("subscription_ownership_unverifiable")
    packs = tuple(
        item for item in response.get("packs", ()) if isinstance(item, dict)
    )
    package_key = str(package_id)
    matches = tuple(
        item
        for item in packs
        if isinstance(item.get("subscription"), dict)
        and item["subscription"].get("provider") == "plugin-market"
        and str(item["subscription"].get("provider_package_id") or "")
        == package_key
    )
    if len(matches) > 1:
        _raise_unsubscribe_error("subscription_ownership_unverifiable")
    if matches:
        resolved_pack_id = str(matches[0].get("pack_id") or "")
        if not resolved_pack_id or resolved_pack_id != claimed_pack_id:
            _raise_unsubscribe_error("subscription_identity_mismatch")
        return await _revalidate_owned_market_subscription(
            package_id=package_id,
            claimed_pack_id=claimed_pack_id,
            subscription=matches[0]["subscription"],
        )

    legacy = next(
        (
            item
            for item in packs
            if str(item.get("pack_id") or "") == claimed_pack_id
            and isinstance(item.get("subscription"), dict)
            and item["subscription"].get("provider") == "plugin-market"
            and not item["subscription"].get("provider_package_id")
        ),
        None,
    )
    if legacy is None:
        _raise_unsubscribe_error("subscription_not_found", status_code=404)
    return await _revalidate_owned_market_subscription(
        package_id=package_id,
        claimed_pack_id=claimed_pack_id,
        subscription=legacy["subscription"],
    )


async def _revalidate_owned_market_subscription(
    *,
    package_id: int,
    claimed_pack_id: str,
    subscription: dict[str, Any],
) -> tuple[str, str]:
    version = str(subscription.get("version") or "")
    channel = str(subscription.get("channel") or "")
    remote_id = str(subscription.get("remote_id") or "")
    material_type = str(subscription.get("material_type") or "")
    artifact_sha256 = str(subscription.get("artifact_sha256") or "")
    try:
        descriptor = await _fetch_version_descriptor(
            KnowledgeSubscribeRequest(
                package_id=package_id,
                version=version,
                channel=channel,
                pack_id=claimed_pack_id,
            )
        )
    except (ValueError, _KnowledgeTaskError):
        _raise_unsubscribe_error("subscription_ownership_unverifiable")
    if (
        descriptor.package_id != package_id
        or descriptor.pack_id != claimed_pack_id
        or descriptor.remote_id != remote_id
        or descriptor.version != version
        or descriptor.channel != channel
        or descriptor.material_type != material_type
        or descriptor.artifacts.knowledge.sha256 != artifact_sha256
    ):
        _raise_unsubscribe_error("subscription_ownership_unverifiable")
    return descriptor.pack_id, descriptor.remote_id


def _raise_unsubscribe_error(code: str, *, status_code: int = 409) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code})


async def _execute_subscription(
    task_id: str, payload: KnowledgeSubscribeRequest
) -> None:
    task = _tasks[task_id]
    try:
        _stage(task, "resolving", 0.05, "正在读取可信市场版本信息")
        descriptor = await _fetch_version_descriptor(payload)
        task["resolved_pack_id"] = descriptor.pack_id
        task["resolved_remote_id"] = descriptor.remote_id
        _stage(task, "downloading", 0.15, "正在下载知识包")
        raw = await _download_verified_artifact(
            descriptor.artifacts.knowledge,
            max_bytes=MAX_PACK_BYTES,
            required_suffix=".neko-knowledge.json",
        )
        _stage(task, "verifying", 0.55, "正在校验知识包")
        try:
            pack_payload = load_canonical_pack_artifact(raw)
        except ValueError as exc:
            raise _KnowledgeTaskError("invalid_artifact", str(exc)) from exc
        if not isinstance(pack_payload, dict):
            raise _KnowledgeTaskError("invalid_artifact", "知识包根必须是对象")
        if pack_payload.get("pack_id") != descriptor.pack_id:
            raise _KnowledgeTaskError(
                "package_identity_mismatch", "市场条目与知识包身份不一致"
            )
        if pack_payload.get("material_type") != descriptor.material_type:
            raise _KnowledgeTaskError(
                "material_type_mismatch", "市场登记用途与知识包内容不一致"
            )

        manifest_raw: bytes | None = None
        vectors_raw: bytes | None = None
        index_fallback_reason = ""
        manifest_descriptor = descriptor.artifacts.index_manifest
        vector_descriptor = descriptor.artifacts.vectors
        if bool(manifest_descriptor) != bool(vector_descriptor):
            index_fallback_reason = "incomplete_index_descriptor"
        elif manifest_descriptor is not None and vector_descriptor is not None:
            try:
                manifest_raw = await _download_verified_artifact(
                    manifest_descriptor,
                    max_bytes=_MAX_INDEX_MANIFEST_BYTES,
                    required_suffix=".neko-knowledge.index.json",
                )
                vectors_raw = await _download_verified_artifact(
                    vector_descriptor,
                    max_bytes=_MAX_VECTOR_BYTES,
                    required_suffix=".neko-knowledge.vectors.f16",
                )
            except _KnowledgeTaskError as exc:
                manifest_raw = None
                vectors_raw = None
                index_fallback_reason = exc.code
        _stage(task, "installing", 0.75, "正在写入本地知识库")
        installation_mutation = asyncio.create_task(
            _main_subscription_request(
                subscription={
                    "provider": "plugin-market",
                    "provider_package_id": str(descriptor.package_id),
                    "remote_id": descriptor.remote_id,
                    "version": descriptor.version,
                    "channel": descriptor.channel,
                    "artifact_sha256": descriptor.artifacts.knowledge.sha256,
                    "material_type": descriptor.material_type,
                    "index_manifest_sha256": (
                        manifest_descriptor.sha256 if manifest_raw is not None else ""
                    ),
                    "vectors_sha256": (
                        vector_descriptor.sha256 if vectors_raw is not None else ""
                    ),
                    "trust": "trusted_market",
                },
                pack_raw=raw,
                manifest_raw=manifest_raw,
                vectors_raw=vectors_raw,
                index_fallback_reason=index_fallback_reason,
            ),
            name=f"market-knowledge-install-{task_id}",
        )
        _installation_mutations[task_id] = installation_mutation
        installation_mutation.add_done_callback(
            lambda completed, *, task_id=task_id: _installation_mutation_done(
                task_id,
                completed,
            )
        )
        result = await asyncio.shield(installation_mutation)
        if result.get("ok") is not True:
            raise _KnowledgeTaskError(
                str(result.get("reason") or "install_failed"),
                "本地知识库拒绝了该知识包",
            )
        job_id = str(result.get("job_id") or "")
        if job_id:
            activated = await _wait_for_pack_job(task, job_id=job_id)
            result = {**result, "activation": activated}
        task["result"] = result
        task["status"] = "completed"
        task["stage"] = "completed"
        task["progress"] = 1.0
        task["message"] = "知识包订阅完成"
        task["completed_at"] = time.time()
        await _report_subscription_best_effort(descriptor, result)
    except asyncio.CancelledError:
        _mark_subscription_cancelled(task)
        raise
    except _KnowledgeTaskError as exc:
        task["status"] = "failed"
        task["stage"] = "failed"
        task["error"] = exc.message
        task["error_code"] = exc.code
        task["message"] = exc.message
        task["completed_at"] = time.time()
    except Exception as exc:
        logger.exception("knowledge subscription task failed: {}", type(exc).__name__)
        task["status"] = "failed"
        task["stage"] = "failed"
        task["error"] = "知识包订阅失败"
        task["error_code"] = "internal_error"
        task["message"] = "知识包订阅失败"
        task["completed_at"] = time.time()


async def _fetch_version_descriptor(
    request: KnowledgeSubscribeRequest,
) -> KnowledgeVersionDescriptor:
    headers = {"Accept": "application/json"}
    token_data = await _ensure_valid_oauth_token()
    if token_data and token_data.get("access_token"):
        headers["Authorization"] = f"Bearer {token_data['access_token']}"
    url = (
        f"{MARKET_API_URL.rstrip('/')}/api/v1/knowledge/packages/"
        f"{request.package_id}/versions/{quote(request.version, safe='')}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            trust_env=False,
        ) as client:
            response = await client.get(
                url, params={"channel": request.channel}, headers=headers
            )
            response.raise_for_status()
            descriptor = KnowledgeVersionDescriptor.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise _KnowledgeTaskError(
            "catalog_resolution_failed",
            "无法读取可信市场版本信息",
        ) from exc
    if (
        descriptor.package_id != request.package_id
        or descriptor.pack_id != request.pack_id
        or descriptor.version != request.version
        or descriptor.channel != request.channel
    ):
        raise _KnowledgeTaskError(
            "catalog_identity_mismatch",
            "市场版本身份不一致",
        )
    return descriptor


async def _download_verified_artifact(
    descriptor: KnowledgeArtifactDescriptor,
    *,
    max_bytes: int,
    required_suffix: str,
) -> bytes:
    if descriptor.bytes > max_bytes:
        raise _KnowledgeTaskError("artifact_too_large", "知识制品超过大小限制")
    _require_artifact_url(
        descriptor.url,
        code="unsafe_artifact_url",
        message="知识制品地址不受信任",
        required_suffix=required_suffix,
    )
    raw = await _download_artifact(descriptor.url, max_bytes=max_bytes)
    if len(raw) != descriptor.bytes:
        raise _KnowledgeTaskError("artifact_size_mismatch", "知识制品大小不一致")
    digest = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(digest, descriptor.sha256):
        raise _KnowledgeTaskError("artifact_hash_mismatch", "知识制品摘要校验失败")
    return raw


async def _download_artifact(url: str, *, max_bytes: int = MAX_PACK_BYTES) -> bytes:
    try:
        async with asyncio.timeout(_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS):
            return await _download_artifact_with_redirects(url, max_bytes=max_bytes)
    except TimeoutError as exc:
        raise _KnowledgeTaskError(
            "download_timeout",
            "知识包下载超过总时间限制",
        ) from exc
    except httpx.HTTPError as exc:
        raise _KnowledgeTaskError("download_failed", "知识包下载失败") from exc


async def _download_artifact_with_redirects(
    url: str,
    *,
    max_bytes: int,
) -> bytes:
    headers = {"Accept": "*/*", "Accept-Encoding": "identity"}
    current_url = url
    redirects = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        while True:
            _require_artifact_url(
                current_url,
                code="unsafe_artifact_redirect",
                message="知识包下载发生了不安全的重定向",
            )
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in _ARTIFACT_REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location or redirects >= _MAX_ARTIFACT_REDIRECTS:
                        raise _KnowledgeTaskError(
                            code="unsafe_artifact_redirect",
                            message="知识包下载重定向无效或次数过多",
                        )
                    next_url = urljoin(str(response.url), location)
                    _require_artifact_url(
                        next_url,
                        code="unsafe_artifact_redirect",
                        message="知识包下载发生了不安全的重定向",
                    )
                    current_url = next_url
                    redirects += 1
                    continue
                if 300 <= response.status_code < 400:
                    raise _KnowledgeTaskError(
                        "unsafe_artifact_redirect",
                        "知识包下载返回了不支持的重定向",
                    )
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise _KnowledgeTaskError(
                            "invalid_artifact_response",
                            "知识制品响应长度无效",
                        ) from exc
                    if declared_size > max_bytes:
                        raise _KnowledgeTaskError(
                            "artifact_too_large", "知识制品超过大小限制"
                        )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise _KnowledgeTaskError(
                            "artifact_too_large", "知识制品超过大小限制"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)


async def _main_subscription_request(
    *,
    subscription: dict[str, str],
    pack_raw: bytes,
    manifest_raw: bytes | None,
    vectors_raw: bytes | None,
    index_fallback_reason: str,
) -> dict[str, Any]:
    import config

    port = _main_server_port()
    headers = {
        "Accept": "application/json",
        "Origin": f"http://127.0.0.1:{port}",
        "X-CSRF-Token": str(config.AUTOSTART_CSRF_TOKEN),
    }
    data = {
        "protocol_version": str(SUBSCRIPTION_PROTOCOL_VERSION),
        "subscription": json.dumps(subscription, separators=(",", ":"), sort_keys=True),
        "index_fallback_reason": index_fallback_reason,
    }
    files: dict[str, tuple[str, bytes, str]] = {
        "pack": ("pack.neko-knowledge.json", pack_raw, "application/json"),
    }
    if manifest_raw is not None and vectors_raw is not None:
        files["index_manifest"] = (
            "pack.neko-knowledge.index.json",
            manifest_raw,
            "application/json",
        )
        files["vectors"] = (
            "pack.neko-knowledge.vectors.f16",
            vectors_raw,
            "application/octet-stream",
        )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(KNOWLEDGE_POST_TIMEOUT_SECONDS, connect=2.0),
            trust_env=False,
        ) as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/api/public-knowledge/subscriptions/apply",
                data=data,
                files=files,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _KnowledgeTaskError(
            "main_server_unavailable", "Main Server 不可用"
        ) from exc
    return payload if isinstance(payload, dict) else {}


async def _main_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float | None = None,
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
    request_timeout = timeout if timeout is not None else (
        KNOWLEDGE_POST_TIMEOUT_SECONDS
        if method == "POST"
        else KNOWLEDGE_GET_TIMEOUT_SECONDS
    )
    # httpx treats connect/read/write/pool independently, so a fixed connect
    # budget silently outlives a shorter overall timeout — with a deadline-derived
    # timeout the request could still spend 2s connecting after the budget was
    # already spent. Clamp it into whatever the caller actually has left.
    connect_timeout = min(_CONNECT_TIMEOUT_SECONDS, request_timeout)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(request_timeout, connect=connect_timeout),
            trust_env=False,
        ) as client:
            response = await client.request(
                method,
                f"http://127.0.0.1:{port}/api/public-knowledge/{path}",
                params=params,
                json=json,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise _KnowledgeTaskError(
            "main_server_unavailable", "Main Server 不可用"
        ) from exc
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if response.status_code >= 500:
            raise _KnowledgeTaskError(
                "main_server_unavailable", "Main Server 不可用"
            ) from exc
        raise _KnowledgeTaskError(
            "main_server_rejected", "Main Server 拒绝了请求"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise _KnowledgeTaskError(
            "main_server_invalid_response", "Main Server 返回了无效响应"
        ) from exc
    if not isinstance(payload, dict):
        raise _KnowledgeTaskError(
            "main_server_invalid_response", "Main Server 返回了无效响应"
        )
    return payload


async def _wait_for_pack_job(
    task: dict[str, Any],
    *,
    job_id: str,
) -> dict[str, Any]:
    """Keep marketplace install pending until the staged pack is truly active."""
    deadline = time.monotonic() + _JOB_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            payload = await _main_request("GET", "packs/jobs")
        except _KnowledgeTaskError as exc:
            if exc.code != "main_server_unavailable":
                raise
            await asyncio.sleep(_JOB_POLL_SECONDS)
            continue
        job = next(
            (
                item
                for item in payload.get("jobs", [])
                if isinstance(item, dict) and item.get("job_id") == job_id
            ),
            None,
        )
        if job is None:
            raise _KnowledgeTaskError("job_not_found", "knowledge job not found")
        state = str(job.get("state") or "")
        if state == "active":
            return job
        if state in {"cancelled", "failed", "degraded"}:
            raise _KnowledgeTaskError(
                f"job_{state}",
                "knowledge job did not complete",
            )
        percent = max(0.0, min(float(job.get("indexed_percent") or 0.0), 100.0))
        task["stage"] = "indexing"
        task["progress"] = 0.8 + percent * 0.0019
        task["message"] = "Knowledge pack indexing in the background"
        await asyncio.sleep(_JOB_POLL_SECONDS)
    raise _KnowledgeTaskError("job_timeout", "knowledge job timed out")


async def _report_subscription_best_effort(
    descriptor: KnowledgeVersionDescriptor,
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
                    "package_id": descriptor.package_id,
                    "version": descriptor.version,
                    "channel": descriptor.channel,
                    "artifact_sha256": descriptor.artifacts.knowledge.sha256,
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


def _verify_bridge_token(token: str) -> None:
    if not secrets.compare_digest(token, get_bridge_token()):
        raise invalid_bridge_token_error()


def _validate_artifact_url(url: str, *, required_suffix: str = "") -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or (parsed.hostname or "").lower() not in _ALLOWED_ARTIFACT_HOSTS
    ):
        raise HTTPException(status_code=400, detail="artifact URL is not allowed")
    if required_suffix and not parsed.path.lower().endswith(required_suffix):
        raise HTTPException(status_code=400, detail="invalid knowledge artifact suffix")


def _require_artifact_url(
    url: str,
    *,
    code: str,
    message: str,
    required_suffix: str = "",
) -> None:
    try:
        _validate_artifact_url(url, required_suffix=required_suffix)
    except HTTPException as exc:
        raise _KnowledgeTaskError(code, message) from exc


def _stage(task: dict[str, Any], stage: str, progress: float, message: str) -> None:
    task["status"] = stage
    task["stage"] = stage
    task["progress"] = progress
    task["message"] = message


def _cleanup_tasks() -> None:
    now = time.time()
    expired = [
        task_id
        for task_id, task in _tasks.items()
        if task_id not in _task_workers
        and task.get("completed_at")
        and now - float(task["completed_at"]) > _TASK_TTL_SECONDS
    ]
    for task_id in expired:
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
        key=lambda item: (
            float(item[1].get("completed_at") or 0),
            float(item[1].get("created_at") or 0),
            item[0],
        ),
    )
    for task_id, _task in terminal[:overflow]:
        _tasks.pop(task_id, None)


class _KnowledgeTaskError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

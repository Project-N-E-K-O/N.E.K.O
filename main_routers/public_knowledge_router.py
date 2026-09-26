"""Generic, local-only management API for the unified public knowledge store."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from knowledge.api import open_knowledge
from knowledge.diagnostics import (
    list_recent_knowledge_index_batches,
    list_recent_knowledge_queries,
    list_recent_knowledge_routes,
)
from knowledge.catalog_overrides import (
    CatalogOverrideError,
    entry_key,
    get_catalog_override_path,
    load_disabled_entries,
)
from knowledge.pack_jobs import KnowledgeJobRegistryError
from knowledge.mutation_runtime import (
    KnowledgeMutationAdmissionClosed,
    run_knowledge_writer,
)
from knowledge.source_registry import get_source, get_sources
from knowledge.store import KnowledgeStoreError
from knowledge.packs import (
    MAX_PACK_BYTES,
    KnowledgePackRecoveryRequiredError,
    validate_pack,
)
from knowledge.prebuilt_index import (
    MAX_PREBUILT_MANIFEST_BYTES,
    MAX_PREBUILT_VECTOR_BYTES,
    validate_prebuilt_index,
)
from knowledge.removal_operations import (
    KnowledgeRemovalOperationError,
    begin_removal_operation,
    complete_removal_operation,
    get_removal_operation,
    validate_removal_operation_id,
)
from knowledge.subscriptions import (
    SUBSCRIPTION_PROTOCOL_VERSION,
    canonical_pack_bytes,
    load_canonical_pack_artifact,
    normalize_provider_package_id,
    validate_subscription,
)
from main_routers.shared_state import get_config_manager


router = APIRouter(prefix="/api/public-knowledge", tags=["public-knowledge"])
_pack_removal_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_PACK_ENVELOPE_OVERHEAD_BYTES = 64 * 1024


def _validate_local_pack_payload(pack_payload):
    if len(canonical_pack_bytes(pack_payload)) > MAX_PACK_BYTES:
        return None
    return validate_pack(pack_payload)


def _validate_subscription_payload(subscription: str):
    return validate_subscription(json.loads(subscription))


def _validate_subscription_pack(pack_raw: bytes, validated_subscription):
    pack_payload = load_canonical_pack_artifact(pack_raw)
    if hashlib.sha256(pack_raw).hexdigest() != validated_subscription.artifact_sha256:
        return None, "artifact_hash_mismatch"
    pack = validate_pack(pack_payload)
    if pack.material_type != validated_subscription.material_type:
        return None, "material_type_mismatch"
    return pack, ""


async def _read_upload_limited(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("uploaded knowledge artifact exceeds the size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _service():
    try:
        return open_knowledge(get_config_manager().knowledge_dir)
    except (KnowledgeStoreError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_unavailable",
                "error_type": type(exc).__name__,
            },
        ) from exc


async def _service_async():
    """Construct/migrate the knowledge service away from the event loop."""
    return await asyncio.to_thread(_service)


def _source_tag(value: str) -> str:
    value = str(value or "").strip()
    return value if not value or value.startswith("source:") else f"source:{value}"


def _content_preview(value: str, *, max_chars: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _entry_payload(
    service,
    entry,
    *,
    detail: bool,
    score=None,
    disabled_entries=None,
    source_cache=None,
):
    database_path = service.database_path()
    if source_cache is None:
        source_cache = {}
    source = source_cache.get(entry.source_tag)
    if source is None:
        source = get_source(entry.source_tag)
        source_cache[entry.source_tag] = source
    if disabled_entries is None:
        disabled_entries = load_disabled_entries(
            get_catalog_override_path(database_path)
        )
    disabled = entry_key(entry) in disabled_entries
    content_preview = _content_preview(entry.content)
    payload = {
        "title": entry.title,
        "terms": {role: list(values) for role, values in entry.terms.items()},
        "tags": list(entry.tags),
        "summary": entry.summary or content_preview,
        "content_preview": content_preview,
        "source": {
            "tag": source.tag,
            "name": source.name,
            "homepage": source.homepage,
            "license": source.license,
        },
        "disabled": disabled,
    }
    if detail:
        payload["content"] = entry.content
    if score is not None:
        payload["score"] = score
    return payload


async def _source_cache_for_entries(service, entries):
    tags = tuple(dict.fromkeys(entry.source_tag for entry in entries))
    return await asyncio.to_thread(
        get_sources,
        tags,
        database_path=service.database_path(),
    )


def _validate_mutation(request: Request, payload: dict):
    from .system_router import _validate_local_mutation_request

    return _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )


@router.get("/status")
async def get_public_knowledge_status():
    try:
        service = await _service_async()
    except HTTPException as exc:
        return {
            "ok": False,
            "status": {
                "name": "Public Knowledge",
                "status": "degraded",
                "available": False,
                "integrity_ok": False,
                "migration_state": "failed",
                "error_code": "knowledge_unavailable",
                "error_type": str(exc.detail.get("error_type") or "")
                if isinstance(exc.detail, dict)
                else "",
            },
        }
    status = await asyncio.to_thread(service.get_status)
    state = "ready" if status.get("integrity_ok") is True else "degraded"
    return {"ok": True, "status": {"status": state, **status}}


@router.get("/entries")
async def list_public_knowledge_entries(
    query: str = Query(default="", max_length=200),
    source: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
):
    service = await _service_async()
    source_tag = _source_tag(source)
    database_path = service.database_path()
    try:
        disabled_entries = await asyncio.to_thread(
            load_disabled_entries,
            get_catalog_override_path(database_path),
        )
    except CatalogOverrideError:
        return {"ok": False, "reason": "catalog_override_invalid"}
    if query.strip():
        page = await asyncio.to_thread(
            service.search_page,
            query.strip(),
            source_tag=source_tag,
            limit=limit,
            offset=offset,
            include_disabled=True,
        )
        has_more = len(page) > limit
        page_entries = [hit.entry for hit in page[:limit]]
        source_cache = await _source_cache_for_entries(service, page_entries)
        items = [
            _entry_payload(
                service,
                hit.entry,
                detail=False,
                score=hit.score,
                disabled_entries=disabled_entries,
                source_cache=source_cache,
            )
            for hit in page[:limit]
        ]
        total = None
    else:
        total = await asyncio.to_thread(
            service.count_entries,
            source_tag=source_tag,
        )
        entries = await asyncio.to_thread(
            service.list_entries,
            source_tag=source_tag,
            limit=limit,
            offset=offset,
        )
        has_more = total > offset + len(entries)
        source_cache = await _source_cache_for_entries(service, entries)
        items = [
            _entry_payload(
                service,
                entry,
                detail=False,
                disabled_entries=disabled_entries,
                source_cache=source_cache,
            )
            for entry in entries
        ]
    return {
        "ok": True,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "items": items,
    }


@router.get("/entry")
async def get_public_knowledge_entry(
    source: str = Query(..., min_length=1, max_length=100),
    title: str = Query(..., min_length=1, max_length=500),
):
    service = await _service_async()
    entry = await asyncio.to_thread(
        service.get_entry,
        source_tag=_source_tag(source),
        title=title.strip(),
    )
    if entry is None:
        return {"ok": False, "reason": "not_found"}
    try:
        disabled_entries = await asyncio.to_thread(
            load_disabled_entries,
            get_catalog_override_path(service.database_path()),
        )
    except CatalogOverrideError:
        return {"ok": False, "reason": "catalog_override_invalid"}
    source_cache = await _source_cache_for_entries(service, (entry,))
    return {
        "ok": True,
        "entry": _entry_payload(
            service,
            entry,
            detail=True,
            disabled_entries=disabled_entries,
            source_cache=source_cache,
        ),
    }


@router.post("/entry/disabled")
async def set_public_knowledge_entry_disabled(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    source_tag = _source_tag(str(payload.get("source") or ""))
    title = str(payload.get("title") or "").strip()
    disabled = payload.get("disabled")
    if not source_tag or not title or not isinstance(disabled, bool):
        return {"ok": False, "reason": "invalid_request"}
    service = await _service_async()
    entry = await asyncio.to_thread(
        service.get_entry,
        source_tag=source_tag,
        title=title,
    )
    if entry is None:
        return {"ok": False, "reason": "not_found"}
    try:
        count = await run_knowledge_writer(
            service.knowledge_root,
            service.set_entry_disabled,
            source_tag=source_tag,
            title=title,
            disabled=disabled,
        )
    except CatalogOverrideError:
        return {"ok": False, "reason": "catalog_override_invalid"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    return {"ok": True, "disabled": disabled, "disabled_entries": count}


@router.get("/packs")
async def list_public_knowledge_packs():
    service = await _service_async()
    packs = await asyncio.to_thread(service.list_packs)
    return {"ok": True, "packs": list(packs)}


@router.get("/packs/jobs")
async def list_public_knowledge_pack_jobs():
    service = await _service_async()
    jobs = await asyncio.to_thread(service.list_pack_jobs)
    return {"ok": True, "jobs": list(jobs)}


@router.post("/packs/import")
async def import_public_knowledge_pack(request: Request):
    payload, too_large = await _bounded_json_payload(
        request,
        max_bytes=MAX_PACK_BYTES + _PACK_ENVELOPE_OVERHEAD_BYTES,
    )
    if too_large:
        return {"ok": False, "reason": "pack_too_large"}
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    try:
        pack_payload = payload.get("pack")
        pack = await asyncio.to_thread(_validate_local_pack_payload, pack_payload)
        if pack is None:
            return {"ok": False, "reason": "pack_too_large"}
        service = await _service_async()
        result = await run_knowledge_writer(
            service.knowledge_root,
            service.stage_pack,
            pack,
        )
    except KnowledgeJobRegistryError:
        return {"ok": False, "reason": "knowledge_job_registry_invalid"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "invalid_pack", "error_type": type(exc).__name__}
    return {
        "ok": True,
        "source_tag": pack.source_tag,
        "entries": result["entries_total"],
        **result,
    }


@router.post("/subscriptions/apply")
async def apply_public_knowledge_subscription(
    request: Request,
    protocol_version: int = Form(...),
    subscription: str = Form(...),
    index_fallback_reason: str = Form(default=""),
    pack: UploadFile = File(...),
    index_manifest: UploadFile | None = File(default=None),
    vectors: UploadFile | None = File(default=None),
):
    """Accept trusted-market raw knowledge plus an optional verified cache."""
    rejected = _validate_mutation(request, {})
    if rejected is not None:
        return rejected
    if protocol_version != SUBSCRIPTION_PROTOCOL_VERSION:
        return {"ok": False, "reason": "unsupported_protocol"}
    try:
        validated_subscription = await asyncio.to_thread(
            _validate_subscription_payload,
            subscription,
        )
        if validated_subscription.provider != "plugin-market":
            return {"ok": False, "reason": "untrusted_provider"}
        if not validated_subscription.provider_package_id:
            return {"ok": False, "reason": "invalid_subscription_identity"}
        pack_raw = await _read_upload_limited(pack, max_bytes=MAX_PACK_BYTES)
        knowledge_pack, rejection = await asyncio.to_thread(
            _validate_subscription_pack,
            pack_raw,
            validated_subscription,
        )
        if rejection:
            return {"ok": False, "reason": rejection}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "invalid_pack", "error_type": type(exc).__name__}

    manifest_raw: bytes | None = None
    vectors_raw: bytes | None = None
    fallback_reason = str(index_fallback_reason or "")[:80]
    has_manifest = index_manifest is not None
    has_vectors = vectors is not None
    if has_manifest and has_vectors:
        try:
            manifest_raw = await _read_upload_limited(
                index_manifest,
                max_bytes=MAX_PREBUILT_MANIFEST_BYTES,
            )
            vectors_raw = await _read_upload_limited(
                vectors,
                max_bytes=MAX_PREBUILT_VECTOR_BYTES,
            )
            await asyncio.to_thread(
                validate_prebuilt_index,
                pack_raw,
                manifest_raw,
                vectors_raw,
                expected_pack_sha256=validated_subscription.artifact_sha256,
                expected_manifest_sha256=validated_subscription.index_manifest_sha256,
                expected_vectors_sha256=validated_subscription.vectors_sha256,
            )
            fallback_reason = ""
        except (OSError, ValueError):
            manifest_raw = None
            vectors_raw = None
            fallback_reason = "prebuilt_index_rejected"
    elif has_manifest or has_vectors:
        fallback_reason = "incomplete_index_upload"
    elif validated_subscription.index_manifest_sha256:
        fallback_reason = fallback_reason or "index_artifact_missing"

    try:
        service = await _service_async()
        result = await run_knowledge_writer(
            service.knowledge_root,
            service.stage_pack,
            knowledge_pack,
            subscription=validated_subscription.to_dict(),
            index_manifest=manifest_raw,
            vectors=vectors_raw,
            index_fallback_reason=fallback_reason,
        )
    except KnowledgeJobRegistryError:
        return {"ok": False, "reason": "knowledge_job_registry_invalid"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "invalid_pack", "error_type": type(exc).__name__}
    return {
        "ok": True,
        "protocol_version": SUBSCRIPTION_PROTOCOL_VERSION,
        "provider": validated_subscription.provider,
        "remote_id": validated_subscription.remote_id,
        "source_tag": knowledge_pack.source_tag,
        "entries": result["entries_total"],
        **result,
    }


@router.post("/packs/jobs/cancel")
async def cancel_public_knowledge_pack_job(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return {"ok": False, "reason": "invalid_request"}
    service = await _service_async()
    try:
        cancelled = await run_knowledge_writer(
            service.knowledge_root,
            service.cancel_pack_job,
            job_id,
        )
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    return {"ok": cancelled, "reason": "" if cancelled else "not_found"}


@router.post("/packs/jobs/discard")
async def discard_degraded_public_knowledge_pack_job(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return {"ok": False, "reason": "invalid_request"}
    service = await _service_async()
    try:
        discarded = await run_knowledge_writer(
            service.knowledge_root,
            service.discard_degraded_pack_job,
            job_id,
        )
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    return {"ok": discarded, "reason": "" if discarded else "not_found"}


@router.post("/packs/auto-context")
async def set_public_knowledge_pack_auto_context(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    pack_id = str(payload.get("pack_id") or "").strip()
    enabled = payload.get("enabled")
    if not pack_id or not isinstance(enabled, bool):
        return {"ok": False, "reason": "invalid_request"}
    try:
        service = await _service_async()
        await run_knowledge_writer(
            service.knowledge_root,
            service.set_pack_auto_context,
            pack_id,
            enabled=enabled,
        )
    except KnowledgePackRecoveryRequiredError:
        return {"ok": False, "reason": "pack_registry_recovery_required"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    except ValueError:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "auto_context": enabled}


@router.post("/packs/index-policy")
async def set_public_knowledge_pack_index_policy(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    pack_id = str(payload.get("pack_id") or "").strip()
    enabled = payload.get("local_embedding_enabled")
    if not pack_id or not isinstance(enabled, bool):
        return {"ok": False, "reason": "invalid_request"}
    try:
        service = await _service_async()
        await run_knowledge_writer(
            service.knowledge_root,
            service.set_pack_index_policy,
            pack_id,
            local_embedding_enabled=enabled,
        )
    except KnowledgePackRecoveryRequiredError:
        return {"ok": False, "reason": "pack_registry_recovery_required"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    except ValueError:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "local_embedding_enabled": enabled}


@router.post("/packs/material-type")
async def set_public_knowledge_pack_material_type(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    pack_id = str(payload.get("pack_id") or "").strip()
    raw_material_type = payload.get("material_type")
    material_type = (
        None if raw_material_type is None else str(raw_material_type).strip()
    )
    if not pack_id or material_type not in {None, "knowledge", "corpus"}:
        return {"ok": False, "reason": "invalid_request"}
    try:
        service = await _service_async()
        await run_knowledge_writer(
            service.knowledge_root,
            service.set_pack_material_type_override,
            pack_id,
            material_type=material_type,
        )
    except KnowledgePackRecoveryRequiredError:
        return {"ok": False, "reason": "pack_registry_recovery_required"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    except ValueError:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "material_type_override": material_type}


@router.post("/packs/remove")
async def remove_public_knowledge_pack(request: Request):
    payload = await _json_payload(request)
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    pack_id = str(payload.get("pack_id") or "").strip()
    if not pack_id:
        return {"ok": False, "reason": "invalid_request"}
    expected_provider = str(payload.get("expected_provider") or "").strip()
    expected_provider_package_id = str(
        payload.get("expected_provider_package_id") or ""
    ).strip()
    expected_remote_id = str(payload.get("expected_remote_id") or "").strip()
    removal_operation_id = str(payload.get("removal_operation_id") or "").strip()
    if expected_provider:
        try:
            normalized_package_id = normalize_provider_package_id(
                expected_provider_package_id
            )
        except ValueError:
            return {"ok": False, "reason": "invalid_request"}
        if expected_provider != "plugin-market" or not expected_remote_id:
            return {"ok": False, "reason": "invalid_request"}
        expected_provider_package_id = normalized_package_id
    operation_request = {
        "pack_id": pack_id,
        "expected_provider": expected_provider,
        "expected_provider_package_id": expected_provider_package_id,
        "expected_remote_id": expected_remote_id,
    }
    if removal_operation_id:
        try:
            removal_operation_id = validate_removal_operation_id(removal_operation_id)
        except ValueError:
            return {"ok": False, "reason": "invalid_request"}
        service = await _service_async()
        task = _pack_removal_tasks.get(removal_operation_id)
        if task is None:
            task = asyncio.create_task(
                _execute_pack_removal_operation(
                    service,
                    removal_operation_id,
                    operation_request,
                ),
                name=f"knowledge-pack-remove:{removal_operation_id}",
            )
            _pack_removal_tasks[removal_operation_id] = task
            task.add_done_callback(
                lambda completed, *, operation_id=removal_operation_id:
                _pack_removal_operation_done(operation_id, completed)
            )
        return await asyncio.shield(task)
    return await _remove_pack_once(await _service_async(), operation_request)


@router.get("/packs/remove/status")
async def get_public_knowledge_pack_removal_status(
    operation_id: str = Query(..., min_length=16, max_length=128),
):
    try:
        operation_id = validate_removal_operation_id(operation_id)
        service = await _service_async()
        record = await asyncio.to_thread(
            get_removal_operation,
            service.knowledge_root,
            operation_id,
        )
    except ValueError:
        return {"ok": False, "status": "unknown", "reason": "invalid_request"}
    except KnowledgeRemovalOperationError as exc:
        return {"ok": False, "status": "unknown", "reason": str(exc)}
    if record is None:
        return {"ok": False, "status": "unknown", "operation_id": operation_id}
    if record["status"] == "pending" and operation_id not in _pack_removal_tasks:
        task = asyncio.create_task(
            _execute_pack_removal_operation(
                service,
                operation_id,
                dict(record["request"]),
            ),
            name=f"knowledge-pack-remove-recovery:{operation_id}",
        )
        _pack_removal_tasks[operation_id] = task
        task.add_done_callback(
            lambda completed, *, operation_id=operation_id:
            _pack_removal_operation_done(operation_id, completed)
        )
    return _removal_operation_response(record)


def _pack_removal_operation_done(
    operation_id: str,
    completed: asyncio.Task[dict[str, Any]],
) -> None:
    if _pack_removal_tasks.get(operation_id) is completed:
        _pack_removal_tasks.pop(operation_id, None)
    if not completed.cancelled():
        completed.exception()


async def _execute_pack_removal_operation(
    service,
    operation_id: str,
    operation_request: dict[str, str],
) -> dict[str, Any]:
    try:
        record = await asyncio.to_thread(
            begin_removal_operation,
            service.knowledge_root,
            operation_id,
            operation_request,
        )
    except (KnowledgeRemovalOperationError, ValueError) as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "operation_id": operation_id,
            "operation_status": "failed",
        }
    if record["status"] != "pending":
        return _removal_operation_response(record)
    result = await _remove_pack_once(
        service,
        operation_request,
        resumed=int(record.get("attempts") or 1) > 1,
    )
    status = "committed" if result.get("ok") is True else "failed"
    try:
        record = await asyncio.to_thread(
            complete_removal_operation,
            service.knowledge_root,
            operation_id,
            status=status,
            result=result,
        )
    except KnowledgeRemovalOperationError as exc:
        return {
            **result,
            "reason": str(exc),
            "operation_id": operation_id,
            "operation_status": "unknown",
        }
    return _removal_operation_response(record)


def _removal_operation_response(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result")
    response = (
        dict(result)
        if isinstance(result, dict)
        else {"ok": False, "reason": "removal_in_progress"}
    )
    return {
        **response,
        "operation_id": str(record["operation_id"]),
        "operation_status": str(record["status"]),
    }


async def _remove_pack_once(
    service,
    operation_request: dict[str, str],
    *,
    resumed: bool = False,
) -> dict[str, Any]:
    try:
        result = await run_knowledge_writer(
            service.knowledge_root,
            service.cancel_and_remove_pack,
            operation_request["pack_id"],
            expected_provider=operation_request["expected_provider"],
            expected_provider_package_id=operation_request[
                "expected_provider_package_id"
            ],
            expected_remote_id=operation_request["expected_remote_id"],
        )
    except PermissionError:
        return {"ok": False, "reason": "subscription_identity_mismatch"}
    except KnowledgeStoreError:
        return {"ok": False, "reason": "knowledge_root_untrusted"}
    except KnowledgePackRecoveryRequiredError:
        return {"ok": False, "reason": "pack_registry_recovery_required"}
    except ValueError:
        if resumed:
            return {
                "ok": True,
                "removed_pack": False,
                "removed_entries": 0,
                "cancelled_jobs": 0,
                "idempotent_recovery": True,
            }
        return {"ok": False, "reason": "not_found"}
    except KnowledgeMutationAdmissionClosed:
        return _knowledge_mutation_stopping()
    return {"ok": True, **result}


def _knowledge_mutation_stopping() -> dict[str, Any]:
    return {"ok": False, "reason": "knowledge_mutation_stopping"}


@router.get("/diagnostics/recent")
async def get_recent_public_knowledge_diagnostics():
    return {
        "ok": True,
        "items": list(list_recent_knowledge_routes()),
        "queries": list(list_recent_knowledge_queries()),
        "index_batches": list(list_recent_knowledge_index_batches()),
    }


async def _json_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _bounded_json_payload(
    request: Request,
    *,
    max_bytes: int,
) -> tuple[dict, bool]:
    """Decode a local request without buffering an oversized upload."""
    try:
        if int(request.headers.get("content-length", "0")) > max_bytes:
            return {}, True
    except ValueError:
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > max_bytes:
            return {}, True
        raw.extend(chunk)
    return await asyncio.to_thread(_decode_json_object, raw), False


def _decode_json_object(raw: bytes | bytearray) -> dict:
    """Decode a bounded JSON object away from the request event loop."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

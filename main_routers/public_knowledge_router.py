"""Bounded, local-only management API for public knowledge."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from knowledge.api import (
    MAX_PACK_BYTES,
    SUBSCRIPTION_PROTOCOL_VERSION,
    KnowledgePackValidationError,
    canonical_pack_bytes,
    validate_knowledge_identifier,
    validate_pack,
    validate_subscription,
)
from knowledge.builtin import open_builtin_knowledge
from knowledge.diagnostics import list_recent_knowledge_routes
from main_routers.shared_state import get_config_manager


router = APIRouter(prefix="/api/public-knowledge", tags=["public-knowledge"])
_PACK_ENVELOPE_OVERHEAD_BYTES = 64 * 1024
_SMALL_BODY_MAX_BYTES = 128 * 1024


def _issue(code: str, message: str, path: str = "$") -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _failure(code: str, message: str, path: str = "$") -> dict[str, Any]:
    return {"ok": False, "issues": [_issue(code, message, path)]}


def _pack_failure(exc: KnowledgePackValidationError) -> dict[str, Any]:
    return {
        "ok": False,
        "issues": [
            {"path": issue.path, "code": issue.code, "message": issue.message}
            for issue in exc.issues
        ],
    }


def _service():
    return open_builtin_knowledge(get_config_manager().knowledge_dir)


def _source_tag(value: object) -> str:
    source = str(value or "").strip()
    if not source:
        return ""
    return source if source.startswith("source:") else f"source:{source}"


def _identifier(value: object, path: str) -> tuple[str, dict[str, Any] | None]:
    try:
        return validate_knowledge_identifier(value), None
    except ValueError:
        return "", _failure(
            "invalid_identifier",
            "must be a valid knowledge identifier",
            path,
        )


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    path: str,
) -> tuple[int, dict[str, Any] | None]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default, _failure("invalid_integer", "must be an integer", path)
    if parsed < minimum or parsed > maximum:
        return default, _failure(
            "out_of_range",
            f"must be between {minimum} and {maximum}",
            path,
        )
    return parsed, None


def _entry_payload(
    service,
    collection_id: str,
    entry,
    *,
    detail: bool,
    disabled_entries: frozenset[tuple[str, str]],
    source_cache: dict[str, dict],
    score: float | None = None,
) -> dict[str, Any]:
    source = source_cache.get(entry.source_tag)
    if source is None:
        source = service.get_source_metadata(collection_id, entry.source_tag)
        source_cache[entry.source_tag] = source
    payload: dict[str, Any] = {
        "collection_id": collection_id,
        "title": entry.title,
        "terms": {role: list(values) for role, values in entry.terms.items()},
        "tags": list(entry.tags),
        "summary": entry.summary,
        "source": source,
        "disabled": (entry.source_tag, entry.title) in disabled_entries,
    }
    if detail:
        payload["content"] = entry.content
    if score is not None:
        payload["score"] = score
    return payload


async def _read_json_object(
    request: Request,
    *,
    max_bytes: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    content_length = request.headers.get("content-length", "")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                return None, _failure("body_too_large", "request body is too large")
        except ValueError:
            return None, _failure("invalid_content_length", "invalid Content-Length")
    raw = await request.body()
    if len(raw) > max_bytes:
        return None, _failure("body_too_large", "request body is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _failure("invalid_json", "request body must be UTF-8 JSON")
    if not isinstance(payload, dict):
        return None, _failure("invalid_object", "request body must be an object")
    return payload, None


def _validate_mutation(request: Request, payload: dict[str, Any]):
    from main_routers.system_router import _validate_local_mutation_request

    return _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={
            "ok": False,
            "issues": [
                _issue(
                    "csrf_validation_failed",
                    "local Origin and CSRF validation failed",
                )
            ],
        },
    )


async def _run_service(
    operation: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(lambda: operation(_service()))
    except ValueError:
        return _failure("operation_failed", "knowledge operation could not be completed")
    except OSError:
        return _failure("storage_error", "knowledge storage is unavailable")
    except Exception:
        return _failure("internal_error", "knowledge operation failed")


@router.get("/collections")
async def list_public_knowledge_collections():
    return await _run_service(
        lambda service: {
            "ok": True,
            "collections": list(service.list_collections()),
        }
    )


@router.get("/entries")
async def list_public_knowledge_entries(
    collection: str = "",
    query: str = "",
    source: str = "",
    limit: str = "50",
    offset: str = "0",
):
    collection_id, error = _identifier(collection, "collection")
    if error:
        return error
    page_limit, error = _bounded_integer(
        limit,
        default=50,
        minimum=1,
        maximum=100,
        path="limit",
    )
    if error:
        return error
    page_offset, error = _bounded_integer(
        offset,
        default=0,
        minimum=0,
        maximum=10_000,
        path="offset",
    )
    if error:
        return error
    query_text = str(query or "").strip()
    if len(query_text) > 200:
        return _failure("too_long", "query exceeds 200 characters", "query")
    source_tag = _source_tag(source)
    if len(source_tag) > 100:
        return _failure("too_long", "source exceeds 100 characters", "source")

    def _list(service):
        disabled = service.list_disabled_entries(collection_id)
        source_cache: dict[str, dict] = {}
        if query_text:
            page = service.search_page(
                collection_id,
                query_text,
                source_tag=source_tag,
                limit=page_limit,
                offset=page_offset,
                include_disabled=True,
            )
            items = [
                _entry_payload(
                    service,
                    collection_id,
                    hit.entry,
                    detail=False,
                    score=hit.score,
                    disabled_entries=disabled,
                    source_cache=source_cache,
                )
                for hit in page[:page_limit]
            ]
            total = None
            has_more = len(page) > page_limit
        else:
            total = service.count_entries(collection_id, source_tag=source_tag)
            entries = service.list_entries(
                collection_id,
                source_tag=source_tag,
                limit=page_limit,
                offset=page_offset,
            )
            items = [
                _entry_payload(
                    service,
                    collection_id,
                    entry,
                    detail=False,
                    disabled_entries=disabled,
                    source_cache=source_cache,
                )
                for entry in entries
            ]
            has_more = total > page_offset + len(entries)
        return {
            "ok": True,
            "collection": collection_id,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": has_more,
            "items": items,
        }

    return await _run_service(_list)


@router.get("/entry")
async def get_public_knowledge_entry(
    collection: str = "",
    source: str = "",
    title: str = "",
):
    collection_id, error = _identifier(collection, "collection")
    if error:
        return error
    source_tag = _source_tag(source)
    title_text = str(title or "").strip()
    if not source_tag or len(source_tag) > 100:
        return _failure("invalid_source", "source is required", "source")
    if not title_text or len(title_text) > 500:
        return _failure("invalid_title", "title is required", "title")

    def _get(service):
        entry = service.get_entry(
            collection_id,
            source_tag=source_tag,
            title=title_text,
        )
        if entry is None:
            return _failure("not_found", "knowledge entry was not found")
        return {
            "ok": True,
            "entry": _entry_payload(
                service,
                collection_id,
                entry,
                detail=True,
                disabled_entries=service.list_disabled_entries(collection_id),
                source_cache={},
            ),
        }

    return await _run_service(_get)


@router.post("/entry/disabled")
async def set_public_knowledge_entry_disabled(request: Request):
    payload, error = await _read_json_object(request, max_bytes=_SMALL_BODY_MAX_BYTES)
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    collection_id, error = _identifier(payload.get("collection"), "collection")
    if error:
        return error
    source_tag = _source_tag(payload.get("source"))
    title = str(payload.get("title") or "").strip()
    disabled = payload.get("disabled")
    if not source_tag or len(source_tag) > 100:
        return _failure("invalid_source", "source is required", "source")
    if not title or len(title) > 500:
        return _failure("invalid_title", "title is required", "title")
    if not isinstance(disabled, bool):
        return _failure("invalid_boolean", "disabled must be a boolean", "disabled")

    def _set(service):
        if service.get_entry(
            collection_id,
            source_tag=source_tag,
            title=title,
        ) is None:
            return _failure("not_found", "knowledge entry was not found")
        count = service.set_entry_disabled(
            collection_id,
            source_tag=source_tag,
            title=title,
            disabled=disabled,
        )
        return {"ok": True, "disabled": disabled, "disabled_entries": count}

    return await _run_service(_set)


@router.post("/collection/auto-context")
async def set_public_knowledge_collection_auto_context(request: Request):
    payload, error = await _read_json_object(request, max_bytes=_SMALL_BODY_MAX_BYTES)
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    collection_id, error = _identifier(payload.get("collection"), "collection")
    if error:
        return error
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return _failure("invalid_boolean", "enabled must be a boolean", "enabled")

    def _set(service):
        service.set_collection_auto_context(collection_id, enabled=enabled)
        return {"ok": True, "collection": collection_id, "auto_context": enabled}

    return await _run_service(_set)


@router.get("/packs")
async def list_public_knowledge_packs(collection: str = ""):
    collection_id, error = _identifier(collection, "collection")
    if error:
        return error
    return await _run_service(
        lambda service: {
            "ok": True,
            "collection": collection_id,
            "packs": list(service.list_packs(collection_id)),
        }
    )


@router.post("/packs/import")
async def import_public_knowledge_pack(request: Request):
    payload, error = await _read_json_object(
        request,
        max_bytes=MAX_PACK_BYTES + _PACK_ENVELOPE_OVERHEAD_BYTES,
    )
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    try:
        pack_payload = payload.get("pack")
        if len(canonical_pack_bytes(pack_payload)) > MAX_PACK_BYTES:
            return _failure("pack_too_large", "knowledge pack exceeds 10 MiB", "pack")
        pack = validate_pack(pack_payload)
    except KnowledgePackValidationError as exc:
        return _pack_failure(exc)
    except (TypeError, ValueError):
        return _failure("invalid_json", "knowledge pack is not valid JSON", "pack")

    return await _run_service(
        lambda service: _install_pack_response(service.install_pack(pack))
    )


def _install_pack_response(result) -> dict[str, Any]:
    return {
        "ok": True,
        "pack_id": result.pack_id,
        "collection": result.collection_id,
        "source_tag": result.source_tag,
        "entries": result.entries,
    }


@router.post("/subscriptions/apply")
async def apply_public_knowledge_subscription(request: Request):
    payload, error = await _read_json_object(
        request,
        max_bytes=MAX_PACK_BYTES + _PACK_ENVELOPE_OVERHEAD_BYTES,
    )
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    if payload.get("protocol_version") != SUBSCRIPTION_PROTOCOL_VERSION:
        return _failure(
            "unsupported_protocol",
            "subscription protocol version is unsupported",
            "protocol_version",
        )
    try:
        subscription = validate_subscription(payload.get("subscription"))
    except ValueError:
        return _failure(
            "invalid_subscription",
            "subscription metadata is invalid",
            "subscription",
        )
    try:
        pack_payload = payload.get("pack")
        pack_bytes = canonical_pack_bytes(pack_payload)
        if len(pack_bytes) > MAX_PACK_BYTES:
            return _failure("pack_too_large", "knowledge pack exceeds 10 MiB", "pack")
        if hashlib.sha256(pack_bytes).hexdigest() != subscription.artifact_sha256:
            return _failure(
                "artifact_hash_mismatch",
                "knowledge artifact hash does not match",
                "subscription.artifact_sha256",
            )
        pack = validate_pack(pack_payload)
    except KnowledgePackValidationError as exc:
        return _pack_failure(exc)
    except (TypeError, ValueError):
        return _failure("invalid_json", "knowledge pack is not valid JSON", "pack")

    def _install(service):
        result = service.install_pack(pack, subscription=subscription.to_dict())
        response = _install_pack_response(result)
        response.update(
            protocol_version=SUBSCRIPTION_PROTOCOL_VERSION,
            provider=subscription.provider,
            remote_id=subscription.remote_id,
        )
        return response

    return await _run_service(_install)


@router.post("/packs/auto-context")
async def set_public_knowledge_pack_auto_context(request: Request):
    payload, error = await _read_json_object(request, max_bytes=_SMALL_BODY_MAX_BYTES)
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    collection_id, error = _identifier(payload.get("collection"), "collection")
    if error:
        return error
    pack_id, error = _identifier(payload.get("pack_id"), "pack_id")
    if error:
        return error
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return _failure("invalid_boolean", "enabled must be a boolean", "enabled")

    def _set(service):
        service.set_pack_auto_context(collection_id, pack_id, enabled=enabled)
        return {"ok": True, "auto_context": enabled}

    return await _run_service(_set)


@router.post("/packs/remove")
async def remove_public_knowledge_pack(request: Request):
    payload, error = await _read_json_object(request, max_bytes=_SMALL_BODY_MAX_BYTES)
    if error:
        return error
    rejected = _validate_mutation(request, payload)
    if rejected is not None:
        return rejected
    collection_id, error = _identifier(payload.get("collection"), "collection")
    if error:
        return error
    pack_id, error = _identifier(payload.get("pack_id"), "pack_id")
    if error:
        return error

    return await _run_service(
        lambda service: {
            "ok": True,
            "removed_entries": service.remove_pack(collection_id, pack_id),
        }
    )


@router.get("/diagnostics/recent")
async def get_recent_public_knowledge_diagnostics():
    items = await asyncio.to_thread(list_recent_knowledge_routes)
    return {"ok": True, "items": list(items)}

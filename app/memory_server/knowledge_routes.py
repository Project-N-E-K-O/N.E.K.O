# -*- coding: utf-8 -*-
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

"""``/internal/knowledge/*``: the public-knowledge subsystem's HTTP surface.

Data isolation, process co-location: the subsystem lives in the Memory Server
process but owns its own root (``<app docs>/knowledge``), its own database, its
own router and its own background tasks, and never touches memory data. It
shares exactly one thing with memory: the process-wide EmbeddingService, which
it uses only once memory's warmup worker has made it ready.

The routes sit on ``runtime.app`` and so inherit everything that guards the
rest of the Memory Server: ``HostOriginGuardMiddleware``,
``InboundBodySizeLimitMiddleware`` and the storage startup gate (requests are
refused with 409 while storage is limited). Browser-facing authentication,
CSRF and Origin checks happen in Main's ``/api/public-knowledge`` proxy, the
only caller besides Main's own tool.

Every handler converts failures into ``{"ok": false, "reason": ...}``; nothing
raised by knowledge code reaches the memory request pipeline.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from knowledge.models import MAX_PACK_BYTES
from knowledge.service import KnowledgeService, KnowledgeUnavailable

from ._shared import logger


router = APIRouter(prefix="/internal/knowledge", tags=["knowledge"])

_JSON_BODY_MAX_BYTES = 64 * 1024
_PACK_BODY_MAX_BYTES = MAX_PACK_BYTES + 64 * 1024
_service: KnowledgeService | None = None
_start_task: asyncio.Task[None] | None = None


class _SharedEmbedder:
    """Adapter over memory's EmbeddingService, resolved once off the loop.

    The adapter never asks the service to load: warming the model is the
    memory warmup worker's decision (it waits for cold start to finish).
    """

    def __init__(self, service: Any) -> None:
        self._service = service

    def state(self) -> str:
        service = self._service
        if service is None:
            return "unavailable"
        if service.is_available():
            return "ready"
        if service.is_disabled():
            return "disabled"
        return "loading"

    def model_id(self) -> str | None:
        return self._service.model_id() if self._service is not None else None

    async def embed(self, text: str) -> list[float] | None:
        return await self._service.embed(text) if self._service is not None else None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if self._service is None:
            return [None] * len(texts)
        return await self._service.embed_batch(texts)


def _resolve_embedding_service() -> Any:
    try:
        from memory.embeddings import get_embedding_service
    except ImportError:
        # Same quarantine fallback as memory.embedding_worker.
        try:
            from memory.embeddings_fallback import get_embedding_service
        except ImportError:
            return None
    try:
        return get_embedding_service()
    except Exception as exc:
        logger.warning("[Knowledge] embedding service unavailable: %s", type(exc).__name__)
        return None


async def start_knowledge_runtime(knowledge_root: Path) -> None:
    """Create and start the subsystem; failures leave it unavailable."""
    global _service
    if _service is not None:
        return
    try:
        embedding_service = await asyncio.to_thread(_resolve_embedding_service)
        service = KnowledgeService(Path(knowledge_root), embedder=_SharedEmbedder(embedding_service))
        _service = service
        await service.start()
    except Exception as exc:
        logger.warning("[Knowledge] runtime start failed: %s", type(exc).__name__, exc_info=True)


def spawn_knowledge_runtime(knowledge_root: Path) -> None:
    """Start in the background so the Memory Server never waits on knowledge."""
    global _start_task
    if _start_task is not None:
        return
    _start_task = asyncio.create_task(
        start_knowledge_runtime(knowledge_root), name="knowledge-runtime-start"
    )


async def stop_knowledge_runtime() -> None:
    """Stop background work before the shared EmbeddingService is released."""
    global _service, _start_task
    if _start_task is not None and not _start_task.done():
        _start_task.cancel()
        await asyncio.wait({_start_task}, timeout=2.0)
    service, _service, _start_task = _service, None, None
    if service is not None:
        try:
            await service.stop()
        except Exception as exc:
            logger.warning("[Knowledge] stop failed: %s", type(exc).__name__)


def get_knowledge_service() -> KnowledgeService | None:
    return _service


# ── helpers ─────────────────────────────────────────────────────────


def _failure(reason: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


async def _call(
    func: Callable[[KnowledgeService], Awaitable[dict[str, Any]]],
) -> JSONResponse | dict[str, Any]:
    service = _service
    if service is None:
        return _failure("knowledge_starting", 503)
    try:
        payload = await func(service)
    except KnowledgeUnavailable as exc:
        return {"ok": False, "reason": exc.reason, **service.availability()}
    except Exception as exc:
        logger.warning("[Knowledge] request failed: %s", type(exc).__name__, exc_info=True)
        return _failure("knowledge_error", 500)
    # ``payload`` may carry its own ``ok`` (a refused cancel, a rejected pack).
    return {"ok": True, **payload, **service.availability()}


async def _read_body(request: Request, *, max_bytes: int) -> bytes | None:
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > max_bytes:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            return None
    return bytes(body)


async def _json_object(request: Request) -> dict[str, Any] | None:
    raw = await _read_body(request, max_bytes=_JSON_BODY_MAX_BYTES)
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _pack_id(payload: dict[str, Any]) -> str:
    value = payload.get("pack_id")
    return value.strip() if isinstance(value, str) else ""


# ── session side ────────────────────────────────────────────────────


@router.post("/query")
async def query_knowledge(request: Request):
    payload = await _json_object(request)
    if payload is None:
        return _failure("invalid_request", 400)
    query = payload.get("query")
    if not isinstance(query, str):
        return _failure("invalid_request", 400)
    limit = payload.get("limit", 3)
    budget_ms = payload.get("budget_ms", 1_500)
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = 3
    if not isinstance(budget_ms, int) or isinstance(budget_ms, bool):
        budget_ms = 1_500
    language = payload.get("language")
    return await _call(
        lambda service: service.query(
            query=query,
            mode=str(payload.get("mode") or "lookup"),
            material_type=str(payload.get("material_type") or "auto"),
            limit=limit,
            budget_ms=budget_ms,
            language=language if isinstance(language, str) else None,
        )
    )


@router.get("/availability")
async def knowledge_availability():
    service = _service
    if service is None:
        return {"ok": True, "ready": False, "enabled": False, "tool_available": False}
    return {"ok": True, **service.availability()}


# ── management: reads ───────────────────────────────────────────────


@router.get("/status")
async def knowledge_status():
    service = _service
    if service is None:
        return {
            "ok": True,
            "status": {"state": "starting", "error_code": "", "ready": False,
                       "enabled": False, "tool_available": False},
            "ready": False,
            "enabled": False,
            "tool_available": False,
        }

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"status": await svc.status()}

    return await _call(run)


@router.get("/entries")
async def knowledge_entries(
    query: str = Query(default="", max_length=200),
    pack_id: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=20_000),
):
    return await _call(
        lambda svc: svc.list_entries(query=query, pack_id=pack_id, limit=limit, offset=offset)
    )


@router.get("/entry")
async def knowledge_entry(
    pack_id: str = Query(..., min_length=2, max_length=64),
    title: str = Query(..., min_length=1, max_length=500),
):
    return await _call(lambda svc: svc.get_entry(pack_id, title))


@router.get("/packs")
async def knowledge_packs():
    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"packs": await svc.list_packs()}

    return await _call(run)


@router.get("/packs/jobs")
async def knowledge_pack_jobs():
    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"jobs": svc.list_jobs()}

    return await _call(run)


@router.get("/diagnostics/recent")
async def knowledge_diagnostics():
    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return svc.diagnostics.snapshot()

    return await _call(run)


# ── management: writes ──────────────────────────────────────────────


@router.post("/settings")
async def knowledge_settings(request: Request):
    payload = await _json_object(request)
    if payload is None or not isinstance(payload.get("enabled"), bool):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.set_enabled(payload["enabled"]))


@router.post("/packs/import")
async def knowledge_import_pack(request: Request):
    raw = await _read_body(request, max_bytes=_PACK_BODY_MAX_BYTES)
    if raw is None:
        return _failure("pack_too_large", 413)
    return await _call(lambda svc: svc.import_pack(raw))


@router.post("/packs/jobs/cancel")
async def knowledge_cancel_job(request: Request):
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"ok": svc.cancel_job(job_id), "job_id": job_id}

    return await _call(run)


@router.post("/packs/jobs/discard")
async def knowledge_discard_job(request: Request):
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"ok": svc.discard_job(job_id), "job_id": job_id}

    return await _call(run)


@router.post("/entry/disabled")
async def knowledge_entry_disabled(request: Request):
    payload = await _json_object(request)
    if (
        payload is None
        or not _pack_id(payload)
        or not isinstance(payload.get("title"), str)
        or not isinstance(payload.get("disabled"), bool)
    ):
        return _failure("invalid_request", 400)
    return await _call(
        lambda svc: svc.set_entry_disabled(_pack_id(payload), payload["title"], payload["disabled"])
    )


@router.post("/packs/auto-context")
async def knowledge_pack_auto_context(request: Request):
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload) or not isinstance(payload.get("enabled"), bool):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.set_pack_auto_context(_pack_id(payload), payload["enabled"]))


@router.post("/packs/index-policy")
async def knowledge_pack_index_policy(request: Request):
    payload = await _json_object(request)
    enabled = payload.get("local_embedding_enabled") if payload else None
    if payload is None or not _pack_id(payload) or not isinstance(enabled, bool):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.set_pack_local_embedding(_pack_id(payload), enabled))


@router.post("/packs/material-type")
async def knowledge_pack_material_type(request: Request):
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload):
        return _failure("invalid_request", 400)
    material_type = payload.get("material_type")
    if material_type is not None and material_type not in ("knowledge", "corpus"):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.set_pack_material_type(_pack_id(payload), material_type))


@router.post("/packs/remove")
async def knowledge_pack_remove(request: Request):
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.remove_pack(_pack_id(payload)))

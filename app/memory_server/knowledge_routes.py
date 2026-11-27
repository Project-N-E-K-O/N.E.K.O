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
refused with 409 while storage is limited). Browser-facing authentication and
CSRF checks happen in Main's ``/api/public-knowledge`` proxy; the callers here
(Main and the plugin server) send neither ``Origin`` nor ``Referer``. Loopback
traffic needs no token on this server and the guard checks only the Host of
HTTP requests, so every route refuses any request that carries a browser
origin (this server serves no pages, so not even a loopback origin is a
legitimate caller), and the JSON routes accept only ``application/json``
bodies: otherwise a web page, or a page another local app serves, could
import, remove or switch off knowledge with a CORS-simple POST.

Every handler converts failures into ``{"ok": false, "reason": ...}``; nothing
raised by knowledge code reaches the memory request pipeline.
"""

from __future__ import annotations

import asyncio
import json
import contextlib
import re
import uuid
from pathlib import Path
from urllib.parse import unquote
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect
from starlette.formparsers import MultiPartParser, MultiPartException

from knowledge.service import MAX_PENDING_IMPORTS, KnowledgeService, KnowledgeUnavailable
from knowledge.models import KnowledgePackError
from knowledge.subscriptions import (
    SUBSCRIPTION_PROTOCOL_VERSION,
    SubscriptionError,
    parse_expected_identity,
    parse_releases,
    parse_subscription_json,
)
from utils.http.knowledge_proxy import JSON_BODY_MAX_BYTES, PACK_BODY_MAX_BYTES, SOURCE_BODY_MAX_BYTES, capped_body, BodyTooLarge

from ._shared import logger


async def _refuse_browsers(request: Request) -> None:
    """Refuse any request a browser sent, before its body is read."""
    if "origin" in request.headers or "referer" in request.headers:
        raise HTTPException(status_code=403, detail={"ok": False, "reason": "untrusted_origin"})


router = APIRouter(
    prefix="/internal/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(_refuse_browsers)],
)

# The same limits the browser-facing proxies apply.
_JSON_BODY_MAX_BYTES = JSON_BODY_MAX_BYTES
# Cards one session can still carry (the resident knowledge budget holds far
# fewer); the rest of an oversized list is ignored.
_TURN_CONTEXT_MAX_EXCLUDED = 256
_PACK_BODY_MAX_BYTES = PACK_BODY_MAX_BYTES
_service: KnowledgeService | None = None
# Import requests allowed to read their body at once (see knowledge_import_pack).
_import_slots = asyncio.Semaphore(MAX_PENDING_IMPORTS)
_start_task: asyncio.Task[None] | None = None


class _SharedEmbedder:
    """Adapter over memory's EmbeddingService, resolved once off the loop.

    The adapter never asks the service to load: warming the model is the
    memory warmup worker's decision (it waits for cold start to finish).
    Everything knowledge needs beyond memory's public ``embed`` /
    ``embed_batch`` lives here, so memory's own paths stay untouched:
    index batches run one at a time (the indexer also pauses between them),
    and the model fingerprint / token counts are computed on demand.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        # Only knowledge's background indexing is serialized; memory's own
        # callers and knowledge's interactive queries never wait on it.
        self._index_lock = asyncio.Lock()

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

    def spec(self) -> dict | None:
        """Fingerprint the local model files; blocking IO, callers run it off-loop.

        A failure only affects knowledge packs and never disables vectors.
        """
        if self._service is None:
            return None
        try:
            from memory._embeddings.spec import specification
            from memory.embeddings import DEFAULT_VECTORS_MAX_LENGTH
            return specification(self._service, max_length=DEFAULT_VECTORS_MAX_LENGTH)
        except (ImportError, AttributeError):
            return None

    def count_tokens(self, texts: list[str]) -> list[int] | None:
        """Count complete inputs with an untruncated tokenizer; callers run it off-loop."""
        if self._service is None:
            return None
        try:
            from memory._embeddings.spec import count_tokens
            from memory.embeddings import DEFAULT_VECTORS_MAX_LENGTH
            return count_tokens(self._service, texts, max_length=DEFAULT_VECTORS_MAX_LENGTH)
        except (ImportError, AttributeError):
            return None

    # Knowledge only borrows the loaded model: an inference error on its
    # (third-party) text fails that call alone and never disables vectors
    # for memory; the indexer's own attempt count handles retries.
    async def embed(self, text: str) -> list[float] | None:
        if self._service is None:
            return None
        return await self._service.embed(text, sticky_failure=False)

    async def embed_query(self, text: str) -> list[float] | None:
        """Interactive lookup goes straight to the shared model, never queued."""
        return await self.embed(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        """Index knowledge sources one batch at a time."""
        async with self._index_lock:
            return await self.embed_batch(texts)

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if self._service is None:
            return [None] * len(texts)
        vectors = await self._service.embed_batch(texts, sticky_failure=False)
        if len(texts) > 1 and all(v is None for v in vectors) and self._service.is_available():
            # The whole batch failed while the model is fine: one bad text
            # (a non-sticky failure) would otherwise cost its neighbours an
            # attempt each. Retry every one of them alone, so only texts that
            # really fail are counted as failed. A general fault costs one
            # such pass per round: the indexer ends a round that stored none.
            vectors = [await self.embed(text) for text in texts]
        return vectors


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
        logger.warning("[Knowledge] runtime start failed: %s", type(exc).__name__)


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
    except KnowledgePackError as exc:
        return {"ok": False, "reason": exc.reason, **service.availability()}
    except Exception as exc:
        # Parser/storage exceptions can carry private text or filenames in
        # their message and chained cause; diagnostics retain only the type.
        logger.warning("[Knowledge] request failed: %s", type(exc).__name__)
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


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


async def _json_object(request: Request) -> dict[str, Any] | None:
    # Only JSON: a browser cannot send this type cross-site without a CORS
    # preflight, which this server never grants.
    if _media_type(request) != "application/json":
        return None
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


@router.post("/turn_context")
async def knowledge_turn_context(request: Request):
    payload = await _json_object(request)
    if payload is None:
        return _failure("invalid_request", 400)
    text = payload.get("text")
    if not isinstance(text, str):
        return _failure("invalid_request", 400)
    budget_ms = payload.get("budget_ms", 1_000)
    if not isinstance(budget_ms, int) or isinstance(budget_ms, bool):
        budget_ms = 1_000
    excluded = payload.get("exclude_card_ids")
    exclude_card_ids = (
        [card_id for card_id in excluded if isinstance(card_id, str)][:_TURN_CONTEXT_MAX_EXCLUDED]
        if isinstance(excluded, list)
        else []
    )
    language = payload.get("language")
    return await _call(
        lambda service: service.turn_context(
            text=text,
            exclude_card_ids=exclude_card_ids,
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
    request: Request,
    pack_id: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=20_000),
):
    # The service bounds decoded search text; private input never belongs in URLs.
    try:
        query = unquote(request.headers.get("x-knowledge-query", ""), errors="strict")
    except UnicodeError:
        return _failure("invalid_request", 400)
    return await _call(
        lambda svc: svc.list_entries(query=query, pack_id=pack_id, limit=limit, offset=offset)
    )


@router.get("/entry")
async def knowledge_entry(
    request: Request,
    pack_id: str = Query(..., min_length=2, max_length=64),
):
    # Titles include source filenames, so they arrive only in the explicit header.
    try:
        title = unquote(request.headers.get("x-knowledge-title", ""), errors="strict")
    except UnicodeError:
        return _failure("invalid_request", 400)
    if not 1 <= len(title) <= 500:
        return _failure("invalid_request", 400)
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


async def _streamed_pack_import(request: Request, service: KnowledgeService, *, subscribed: bool = False):
    """Spool bounded input to disk and retain ownership through cancellation.

    The browser-facing proxies already stream bytes. Multipart files use
    Starlette's disk spool; a bounded copy gives the import worker a stable
    pathname, rather than materializing a 64 MiB ZIP in a bytes object.
    """
    async def owned():
        folder = service.root / ".staging"
        path = folder / (uuid.uuid4().hex + ".upload")
        form = None
        try:
            await service._thread(folder.mkdir, parents=True, exist_ok=True)
            subscription = None
            if _media_type(request) == "multipart/form-data":
                parser = MultiPartParser(
                    request.headers, capped_body(request, _PACK_BODY_MAX_BYTES),
                    max_files=1, max_fields=8, max_part_size=_JSON_BODY_MAX_BYTES,
                )
                try:
                    form = await parser.parse()
                except BaseException:
                    # Starlette closes these only for its own parser errors;
                    # a capped stream/disconnect can abort before FormData exists.
                    def close_partial_files():
                        for handle in parser._files_to_close_on_error:
                            handle.close()
                    await service._thread(close_partial_files)
                    raise
                uploads = [value for value in form.values() if isinstance(value, UploadFile)]
                upload = form.get("pack") if isinstance(form.get("pack"), UploadFile) else None
                # Keep the subscription transport's explicit pack field;
                # only ordinary local imports accept a sole unnamed file.
                if not subscribed:
                    upload = upload or (uploads[0] if len(uploads) == 1 else None)
                if upload is None:
                    return _failure("invalid_request", 400)
                if subscribed:
                    if form.get("protocol_version") != str(SUBSCRIPTION_PROTOCOL_VERSION):
                        return _failure("unsupported_protocol", 400)
                    metadata = form.get("subscription")
                    if not isinstance(metadata, str):
                        return _failure("invalid_request", 400)
                    try:
                        subscription = parse_subscription_json(metadata)
                    except SubscriptionError as error:
                        return _failure(error.reason, 400)

                async def blocks():
                    while chunk := await upload.read(64 * 1024):
                        yield chunk
            else:
                blocks = lambda: capped_body(request, _PACK_BODY_MAX_BYTES)
            handle = await service._thread(path.open, "xb")
            try:
                async for chunk in blocks():
                    await service._thread(handle.write, chunk)
            finally:
                await service._thread(handle.close)
            # Local JSON/ZIP can contain private personal material just like
            # source documents. Only verified market subscriptions retain the
            # existing opt-in policy of the subscription transport.
            options = None if subscribed else {"origin": "imported", "enabled": False}
            return await _call(lambda svc: svc.import_pack(path, subscription=subscription, _options=options))
        except BodyTooLarge:
            return _failure("pack_too_large", 413)
        except ConnectionError:
            # A reset or broken client stream is a request failure, not a
            # storage fault, even though Python models it as an OSError.
            return _failure("invalid_request", 400)
        except OSError as exc:
            # Scratch I/O failures are server faults; never expose a private
            # path from the exception or misreport a valid file as bad input.
            logger.warning("[Knowledge] pack upload storage failed: %s", type(exc).__name__)
            return _failure("knowledge_error", 500)
        except (ValueError, MultiPartException, ClientDisconnect):
            return _failure("invalid_request", 400)
        finally:
            if form is not None:
                await form.close()
            with contextlib.suppress(OSError):
                await service._thread(path.unlink, missing_ok=True)

    # A disconnected caller cannot delete the spool while a native parser or
    # admission thread still reads it. Drain the actual owner before cleanup.
    return await _await_file_owner(owned())


async def _await_file_owner(operation):
    """Drain a request's file owner before disconnect can release its scratch."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


@router.post("/packs/import")
async def knowledge_import_pack(request: Request):
    """Import unchanged v1 JSON or a bounded v2 ZIP through the same pack path."""
    service = _service
    if service is None:
        return _failure("knowledge_starting", 503)
    if _media_type(request) not in {"application/json", "application/zip", "application/octet-stream", "multipart/form-data"}:
        return _failure("invalid_request", 400)
    if _import_slots.locked() or service.import_busy():
        return {"ok": False, "reason": "knowledge_busy", **service.availability()}
    async with _import_slots:
        return await _streamed_pack_import(request, service)


@router.post("/packs/jobs/cancel")
async def knowledge_cancel_job(request: Request):
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"ok": await svc.cancel_job(job_id), "job_id": job_id}

    return await _call(run)


@router.post("/packs/jobs/discard")
async def knowledge_discard_job(request: Request):
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return {"ok": await svc.discard_job(job_id), "job_id": job_id}

    return await _call(run)


@router.post("/packs/jobs/retry")
async def knowledge_retry_job(request: Request):
    """Retry the retained task input; the client cannot substitute source text."""
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.retry_job(job_id))


@router.post("/packs/exports/delete")
async def knowledge_delete_export(request: Request):
    """Delete a completed local export through the existing mutation boundary."""
    payload = await _json_object(request)
    job_id = payload.get("job_id") if payload else None
    if payload is None or set(payload) != {"job_id"} or not isinstance(job_id, str) or not job_id:
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.delete_export(job_id))


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


@router.post("/packs/enabled")
async def knowledge_pack_enabled(request: Request):
    """Apply participation policy to every pack origin, independently of indexing."""
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload) or not isinstance(payload.get("enabled"), bool):
        return _failure("invalid_request", 400)
    # Browser-facing enable requests must bind their disclosure to a body;
    # disabling remains available without a previously observed content hash.
    expected = payload.get("expected_pack_sha256") if payload["enabled"] else None
    if payload["enabled"] and (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.set_pack_enabled(
        _pack_id(payload), payload["enabled"], expected_pack_sha256=expected,
    ))


@router.get("/sources")
async def knowledge_sources(pack_id: str = Query(...)):
    return await _call(lambda svc: svc.list_sources(pack_id))


@router.get("/sources/versions")
async def knowledge_source_versions(pack_id: str = Query(...), doc_id: str = Query(...)):
    return await _call(lambda svc: svc.source_versions(pack_id, doc_id))


@router.post("/sources/preview")
async def knowledge_source_preview(request: Request, pack_id: str = "", doc_id: str = ""):
    """Keep private names in headers rather than ordinary HTTP access-log URLs."""
    if _media_type(request) != "application/octet-stream":
        return _failure("invalid_request", 400)
    if _import_slots.locked():
        return _failure("knowledge_busy", 429)
    try:
        filename = unquote(request.headers.get("x-knowledge-filename", ""), errors="strict")
        name = unquote(request.headers.get("x-knowledge-name", ""), errors="strict")
    except UnicodeError:
        return _failure("invalid_request", 400)
    if not filename:
        return _failure("invalid_request", 400)
    service = _service
    if service is None:
        return _failure("knowledge_starting", 503)

    async def owned():
        folder = service.root / ".staging"
        path = folder / (uuid.uuid4().hex + ".source")
        try:
            await service._thread(folder.mkdir, parents=True, exist_ok=True)
            handle = await service._thread(path.open, "xb")
            try:
                async for block in capped_body(request, SOURCE_BODY_MAX_BYTES):
                    await service._thread(handle.write, block)
            finally:
                await service._thread(handle.close)
            # The parser receives a bounded spool, not another complete HTTP
            # bytearray/bytes copy; keep it alive through native parsing.
            return await _call(lambda svc: svc.preview_source(filename, path, pack_id=pack_id, doc_id=doc_id, name=name))
        except BodyTooLarge:
            return _failure("document_too_large", 413)
        except ConnectionError:
            return _failure("invalid_request", 400)
        except OSError as exc:
            # Match the package spool's storage-error and privacy boundary.
            logger.warning("[Knowledge] source upload storage failed: %s", type(exc).__name__)
            return _failure("knowledge_error", 500)
        except (ValueError, ClientDisconnect):
            return _failure("invalid_request", 400)
        finally:
            with contextlib.suppress(OSError):
                await service._thread(path.unlink, missing_ok=True)

    async with _import_slots:
        return await _await_file_owner(owned())


@router.post("/sources/commit")
async def knowledge_source_commit(request: Request):
    payload = await _json_object(request)
    if (payload is None or not isinstance(payload.get("preview_id"), str)
            or not isinstance(payload.get("sha256"), str) or type(payload.get("enabled", False)) is not bool
            or type(payload.get("enabled_confirmed", False)) is not bool):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.commit_source(payload["preview_id"], payload["sha256"], enabled=payload.get("enabled", False),
                                                       enabled_confirmed=payload.get("enabled_confirmed", False)))


@router.post("/sources/preview/discard")
async def knowledge_source_preview_discard(request: Request):
    payload = await _json_object(request)
    if payload is None or not isinstance(payload.get("preview_id"), str):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.discard_preview(payload["preview_id"]))


@router.post("/sources/remove")
async def knowledge_source_remove(request: Request):
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload) or not isinstance(payload.get("doc_id"), str):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.remove_source(_pack_id(payload), payload["doc_id"]))


@router.post("/sources/restore")
async def knowledge_source_restore(request: Request):
    payload = await _json_object(request)
    if (payload is None or not _pack_id(payload) or not isinstance(payload.get("doc_id"), str)
            or type(payload.get("version")) is not int
            or type(payload.get("enabled_confirmed", False)) is not bool):
        return _failure("invalid_request", 400)
    # Restoration carries the same explicit disclosure acknowledgement as a
    # source commit; omitted acknowledgement stays false, never truthy-coerced.
    return await _call(lambda svc: svc.restore_source(
        _pack_id(payload), payload["doc_id"], payload["version"],
        enabled_confirmed=payload.get("enabled_confirmed", False),
    ))


@router.post("/packs/export")
async def knowledge_pack_export(request: Request):
    payload = await _json_object(request)
    # Both the ZIP format and release-version policy belong to the server.
    # Refuse caller-supplied versions instead of silently ignoring manual input.
    if (payload is None or not _pack_id(payload) or type(payload.get("format_version", 2)) is not int
            or payload.get("format_version", 2) != 2 or "version" in payload):
        return _failure("invalid_request", 400)
    return await _call(lambda svc: svc.export_pack(_pack_id(payload), format_version=payload.get("format_version", 2)))


@router.get("/packs/artifacts")
async def knowledge_pack_artifact(artifact_id: str = Query(...)):
    service = _service
    if service is None:
        return _failure("knowledge_starting", 503)
    try:
        path, filename = await service.artifact_download(artifact_id)
    except KnowledgePackError as error:
        return _failure(error.reason, 404)
    return FileResponse(path, filename=filename, media_type="application/zip")


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


@router.post("/subscriptions/apply")
async def knowledge_apply_subscription(request: Request):
    """Install the market's hash-bound artifact without buffering a whole ZIP."""
    service = _service
    if service is None:
        return _failure("knowledge_starting", 503)
    if _media_type(request) != "multipart/form-data":
        return _failure("invalid_request", 400)
    if _import_slots.locked():
        return {"ok": False, "reason": "knowledge_busy", **service.availability()}
    async with _import_slots:
        return await _streamed_pack_import(request, service, subscribed=True)


@router.get("/subscriptions")
async def knowledge_subscriptions():
    """Installed subscriptions and their imports, for the market page to reconcile against."""

    async def run(svc: KnowledgeService) -> dict[str, Any]:
        return svc.subscription_state()

    return await _call(run)


@router.post("/subscriptions/remove")
async def knowledge_remove_subscription(request: Request):
    """Unsubscribe: remove the named releases of a market package, installed or on the way.

    Reached only through Main, like ``subscriptions/apply``. The body names the
    pack, the subscription identity and the releases to remove; repeating it
    is safe (see ``KnowledgeService.remove_subscription``).
    """
    payload = await _json_object(request)
    if payload is None or not _pack_id(payload):
        return _failure("invalid_request", 400)
    version = payload.get("protocol_version")
    if type(version) is not int or version != SUBSCRIPTION_PROTOCOL_VERSION:
        return _failure("unsupported_protocol", 400)
    try:
        identity = parse_expected_identity(payload.get("expected_subscription"))
        releases = parse_releases(payload.get("releases"))
    except SubscriptionError as exc:
        return _failure(exc.reason, 400)
    return await _call(lambda svc: svc.remove_subscription(_pack_id(payload), identity, releases))

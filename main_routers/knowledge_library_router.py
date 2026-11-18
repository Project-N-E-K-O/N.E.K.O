"""Thin, size-bounded HTTP surface for the built-in personal library."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from services.knowledge_library.host import LibraryHost
from services.knowledge_library.jobs import run_sync
from services.knowledge_library.models import LibraryError, checked_id, digest, new_id
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_install import get_artifact, install_package, list_packages, package_scope
from services.knowledge_library.package_validate import DEFAULT_LIMITS, PACKAGE_SUFFIX, canonical_json, validate_specification
from services.knowledge_library.paths import library_root
from utils.http.knowledge_proxy import BodyTooLarge, capped_body


def _failure(reason: str, status: int) -> JSONResponse:
    """Keep failures machine-readable without leaking private source material."""
    return JSONResponse({"ok": False, "reason": reason}, status_code=status,
                        headers={"Cache-Control": "no-store"})


class LibraryRoute(APIRoute):
    """Authenticate mutations before reading bodies and isolate domain errors."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                refused = _validate_local_mutation_request(
                    request, error_defaults={"ok": False, "reason": "csrf_validation_failed"},
                )
                if refused is not None:
                    return refused
            try:
                is_upload = request.method == "POST" and (
                    self.path.endswith("/documents") or self.path.endswith("/packages/install")
                )
                if is_upload:
                    host = await get_host(request)
                    if host.upload_slots.locked():
                        return _failure("jobs_busy", 429)
                    async with host.upload_slots:
                        service = await host.service()
                        if sum(job["state"] in {"queued", "running"} for job in service.jobs.records.values()) >= service.limits.max_pending_jobs:
                            return _failure("jobs_busy", 429)
                        response = await handler(request)
                else:
                    response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except BodyTooLarge:
                return _failure("payload_too_large", 413)
            except LibraryError as error:
                reason = error.code
                status = 404 if reason in {"not_found", "document_not_found", "job_not_found", "package_not_found"} else 400
                if reason in {"jobs_busy", "preview_capacity"}:
                    status = 429
                elif reason in {"storage_unavailable", "storage_busy", "service_unavailable", "embedding_unavailable"}:
                    status = 503
                elif "conflict" in reason or reason in {"preview_changed", "storage_root_changed", "job_already_committed"}:
                    status = 409
                return _failure(reason, status)
            except OSError:
                return _failure("storage_unavailable", 503)

        return guarded


router = APIRouter(prefix="/api/knowledge-library", tags=["knowledge-library"], route_class=LibraryRoute)


async def get_host(request: Request) -> LibraryHost:
    """Keep one domain owner per app, replacing it only after old jobs drain."""
    import asyncio

    state = request.app.state
    if not hasattr(state, "knowledge_library_lock"):
        state.knowledge_library_lock = asyncio.Lock()
    async with state.knowledge_library_lock:
        config = get_config_manager()
        root = await run_sync(lambda: library_root(config).resolve())
        host = getattr(state, "knowledge_library_host", None)
        if host is None or host.root != root:
            if host is not None:
                await host.close()
            host = await run_sync(LibraryHost, config)
            state.knowledge_library_host = host
        return host


async def _service(request: Request):
    return await (await get_host(request)).service()


async def _json(request: Request, fields: set[str]) -> dict:
    """Bound JSON before decoding; field names are a deliberately small contract."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise LibraryError("invalid_request")
    body = bytearray()
    async for block in capped_body(request, 64 * 1024):
        body.extend(block)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LibraryError("invalid_request") from error
    if not isinstance(value, dict) or set(value) - fields:
        raise LibraryError("invalid_request")
    return value


class _UploadTooLarge(MultiPartException):
    """Use the parser's cleanup path when the streaming cap is exceeded."""


@asynccontextmanager
async def _upload(request: Request, limit: int, fields: set[str]):
    """Spool one file under a total stream cap, closing it on every exit path."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
        raise LibraryError("invalid_request")

    async def stream():
        try:
            async for block in capped_body(request, limit + 64 * 1024):
                yield block
        except BodyTooLarge as error:
            raise _UploadTooLarge("payload_too_large") from error

    try:
        form = await MultiPartParser(request.headers, stream(), max_files=1, max_fields=2,
                                    max_part_size=16 * 1024).parse()
    except _UploadTooLarge as error:
        raise BodyTooLarge() from error
    except MultiPartException as error:
        raise LibraryError("invalid_request") from error
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or set(form) - fields - {"file"} or len(form.getlist("file")) != 1:
            raise LibraryError("invalid_request")
        if file.size is not None and file.size > limit:
            raise BodyTooLarge()
        yield file, form
    finally:
        await form.close()


@router.get("/libraries")
async def libraries(request: Request):
    """List personal libraries without returning document bodies or vectors."""
    return {"ok": True, "libraries": await (await _service(request)).list_libraries()}


@router.post("/libraries")
async def create_library(request: Request):
    """Create a draft from a display name, never a client filesystem path."""
    payload = await _json(request, {"name"})
    return {"ok": True, "library": await (await _service(request)).create_library(payload.get("name"))}


@router.get("/libraries/{library_id}")
async def read_library(library_id: str, request: Request):
    """Return one library's metadata and committed document summaries."""
    result = await (await _service(request)).get_library(library_id)
    # An artifact's managed path is internal bookkeeping, never a frontend ID.
    result["library"].get("installation", {}).pop("artifact_path", None)
    return {"ok": True, **result}


@router.patch("/libraries/{library_id}")
async def rename_library(library_id: str, request: Request):
    """Change display metadata without reindexing any source document."""
    payload = await _json(request, {"name"})
    return {"ok": True, "library": await (await _service(request)).rename_library(library_id, payload.get("name"))}


@router.delete("/libraries/{library_id}")
async def delete_library(library_id: str, request: Request):
    """Invalidate owned work and delete only the managed library authority."""
    await (await _service(request)).delete_library(library_id)
    return {"ok": True}


@router.post("/libraries/{library_id}/documents")
async def upload_document(library_id: str, request: Request):
    """Persist a bounded original and return its server-owned preview task."""
    service = await _service(request)
    async with _upload(request, service.limits.max_document_bytes, {"document_id"}) as (file, form):
        data = await file.read(service.limits.max_document_bytes + 1)
        job = await service.preview_document(library_id, file.filename or "", data, form.get("document_id") or None)
    return {"ok": True, "job": job}


@router.get("/previews/{preview_id}")
async def read_preview(preview_id: str, request: Request):
    """Read a complete managed preview before the user confirms admission."""
    return {"ok": True, "preview": await (await _service(request)).get_preview(preview_id)}


@router.post("/previews/{preview_id}/confirm")
async def confirm_preview(preview_id: str, request: Request):
    """Confirm the saved original digest instead of accepting browser text."""
    payload = await _json(request, {"sha256"})
    return {"ok": True, "job": await (await _service(request)).confirm_preview(preview_id, payload.get("sha256"))}


@router.delete("/previews/{preview_id}")
async def discard_preview(preview_id: str, request: Request):
    """Reclaim an abandoned preview without cancelling a real parser thread."""
    await (await _service(request)).discard_preview(preview_id)
    return {"ok": True}


@router.get("/libraries/{library_id}/documents/{document_id}")
async def read_document(library_id: str, document_id: str, request: Request):
    """Return source text, not internal vector matrices or storage metadata."""
    document = await (await _service(request)).get_document(library_id, document_id)
    public = {key: value for key, value in document.items() if key in {
        "document_id", "version_id", "filename", "sha256", "size", "format", "content", "sections", "semantic_state",
    }}
    return {"ok": True, "document": public}


@router.delete("/libraries/{library_id}/documents/{document_id}")
async def delete_document(library_id: str, document_id: str, request: Request):
    """Remove a document from active search and invalidate dependent work."""
    await (await _service(request)).delete_document(library_id, document_id)
    return {"ok": True}


@router.post("/libraries/{library_id}/search")
async def search(library_id: str, request: Request):
    """Search only the explicit library and return original source excerpts."""
    payload = await _json(request, {"query", "limit"})
    return {"ok": True, **await (await _service(request)).search(library_id, payload.get("query"), payload.get("limit", 10))}


@router.get("/jobs")
async def jobs(request: Request):
    """Restore visible task facts without exposing frozen internal inputs."""
    return {"ok": True, "jobs": await (await _service(request)).list_jobs()}


@router.get("/jobs/{job_id}")
async def read_job(job_id: str, request: Request):
    """Return the server's actual state for an opaque task identity."""
    return {"ok": True, "job": await (await _service(request)).get_job(job_id)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Request cancellation without claiming a native worker already exited."""
    return {"ok": True, "job": await (await _service(request)).cancel_job(job_id)}


def _build_runner(service, host: LibraryHost, frozen: dict):
    """Restore only the frozen selection, including after an explicit retry."""
    async def run(context):
        snapshot = await context.run_sync(service.store.read, host.root / "jobs/snapshots" / f"{checked_id(frozen['snapshot_id'])}.json")
        # Frozen snapshots include document vectors. Encoding their identity
        # can be substantial CPU work and must not stall the shared event loop.
        actual_digest = await context.run_sync(lambda: digest(canonical_json(snapshot)))
        if actual_digest != frozen["snapshot_sha256"]:
            raise LibraryError("package_snapshot_changed")
        if not isinstance(frozen.get("embedding_spec"), dict):
            # Legacy task records have no recoverable first encoder identity;
            # requiring a new explicit build is safer than silently changing it.
            raise LibraryError("package_embedding_identity_changed")
        result = await build_package(snapshot, host.root / "artifacts" / "build.part", host.embedder,
                                     expected_spec=frozen["embedding_spec"],
                                     check_cancelled=context.check_cancelled, commit_gate=context.commit_gate,
                                     write_guard=host.write_guard)
        result.pop("artifact_path", None)
        return result
    return run


def _install_runner(service, host: LibraryHost, frozen: dict):
    """Install verified stored vectors; never queue document embedding work."""
    async def run(context):
        path = host.root / "staging/imports" / (checked_id(frozen["upload_id"]) + PACKAGE_SUFFIX)
        # A previous attempt may have committed and reclaimed its upload before
        # the final job record reached disk. Retry from the verified artifact,
        # keeping the original digest instead of requesting new source bytes.
        def original_source():
            return path if path.is_file() else get_artifact(host.root, frozen["sha256"])
        source = await context.run_sync(original_source)
        try:
            spec = await host.embedder.specification()
        except LibraryError:
            spec = None  # Complete bytes remain usable for honest keyword-only browsing.
        prepared = await install_package(source, host.root, expected_spec=spec,
                                         expected_sha256=frozen["sha256"],
                                         check_cancelled=context.check_cancelled, write_guard=host.write_guard)
        library = await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"],
                                                library_id=frozen.get("library_id"), context=context)
        def reclaim_upload():
            with host.write_guard():
                path.unlink(missing_ok=True)
        try:
            await run_sync(reclaim_upload)
        except (OSError, LibraryError):
            pass  # The committed installation stays successful; startup retries cleanup.
        result = {"library_id": library["library_id"], "artifact_id": prepared["package"]["artifact_id"],
                  "semantic_available": prepared["semantic_available"]}
        # Preserve a post-commit durability warning through the thin task API;
        # the installation is active even when directory sync is unconfirmed.
        if library.get("warnings"):
            result["warnings"] = library["warnings"]
        return result
    return run


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    """Restart known operations from their persisted inputs, never latest drafts."""
    host = await get_host(request)
    service = await host.service()
    record = service.jobs.records.get(checked_id(job_id))
    if not record or record["state"] not in {"interrupted", "failed", "cancelled"}:
        raise LibraryError("job_not_retryable")
    if record["kind"] in {"build", "install"}:
        frozen = record["frozen_input"]
        runner = (_build_runner if record["kind"] == "build" else _install_runner)(service, host, frozen)
        job = await service.submit_job(record["kind"], record["library_id"], runner, frozen_input=frozen)
    elif record["kind"] in {"market_publish", "market_install"}:
        # The market adapter reconstructs the same frozen revision/hash or
        # submission UUID; retries cannot accidentally publish another work.
        from main_routers.knowledge_library_market_router import retry_market_job

        job = await retry_market_job(service, host, record)
    else:
        job = await service.retry_job(job_id)
    return {"ok": True, "job": job}


@router.get("/packages")
async def packages(request: Request):
    """List committed artifact metadata, including offline-installed products."""
    host = await get_host(request)
    await host.service()
    return {"ok": True, "packages": await run_sync(list_packages, host.root)}


@router.post("/libraries/{library_id}/packages")
async def build(library_id: str, request: Request):
    """Freeze the explicit document selection and enqueue a complete build."""
    host = await get_host(request)
    service = await host.service()
    payload = await _json(request, {"version", "source", "document_ids"})
    identifiers = payload.get("document_ids")
    if not isinstance(identifiers, list) or not identifiers or any(not isinstance(value, str) for value in identifiers):
        raise LibraryError("invalid_document_selection")
    # Bind the first requested encoding identity before durable registration,
    # so queued execution and explicit retries cannot select a new auto tier.
    spec = validate_specification(await host.embedder.specification())
    snapshot = await service.freeze_library(library_id, version=payload.get("version"),
                                            source=payload.get("source"), document_ids=identifiers)
    snapshot_digest = await run_sync(lambda: digest(canonical_json(snapshot)))
    frozen = {"snapshot_id": snapshot["snapshot_id"], "snapshot_sha256": snapshot_digest,
              "document_ids": identifiers, "embedding_spec": spec}
    return {"ok": True, "job": await service.submit_job("build", library_id, _build_runner(service, host, frozen), frozen_input=frozen)}


@router.get("/packages/{artifact_id}/download")
async def download(artifact_id: str, request: Request):
    """Export a hash-verified immutable artifact with a local safe filename."""
    host = await get_host(request)
    path = await run_sync(get_artifact, host.root, artifact_id)
    return FileResponse(path, media_type="application/zip", filename=artifact_id + PACKAGE_SUFFIX,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/packages/{artifact_id}/preview")
async def preview_package(artifact_id: str, request: Request):
    """Review private source scope from validated bytes before export/publication."""
    host = await get_host(request)
    if host.upload_slots.locked():
        return _failure("jobs_busy", 429)
    # Complete validation can expand a large package. Reuse bounded admission
    # and hold it until the real IO worker exits, even if this request is cancelled.
    async with host.upload_slots:
        result = await run_sync(package_scope, host.root, artifact_id)
    # Filenames and source declarations are private until publication. Never
    # put this response into browser/proxy caches or the market's public preview.
    return JSONResponse({"ok": True, "package": result}, headers={"Cache-Control": "no-store"})


@router.post("/packages/install")
async def install(request: Request):
    """Save one bounded offline artifact and enqueue inference-free admission."""
    host = await get_host(request)
    service = await host.service()
    async with _upload(request, DEFAULT_LIMITS.max_archive_bytes, {"library_id"}) as (file, form):
        if not (file.filename or "").endswith(PACKAGE_SUFFIX):
            raise LibraryError("package_invalid_format")
        upload_id = new_id()
        library_id = form.get("library_id") or None
        if library_id is not None:
            checked_id(library_id)
            await service.get_library(library_id)
        path = host.root / "staging/imports" / (upload_id + PACKAGE_SUFFIX)

        def save():
            # A bounded file copy runs off-loop; the uploaded handle stays open
            # until it really exits, even if the browser abandons the request.
            with host.write_guard():
                path.parent.mkdir(parents=True, exist_ok=True)
                hasher, size = hashlib.sha256(), 0
                with path.open("xb") as destination:
                    while block := file.file.read(64 * 1024):
                        size += len(block)
                        if size > DEFAULT_LIMITS.max_archive_bytes:
                            raise LibraryError("package_too_large")
                        hasher.update(block)
                        destination.write(block)
                return hasher.hexdigest()

        sha256 = await run_sync(save)
    frozen = {"upload_id": upload_id, "sha256": sha256, "library_id": library_id}
    job = await service.submit_job("install", library_id, _install_runner(service, host, frozen), frozen_input=frozen)
    return {"ok": True, "job": job}

"""Local package import and bounded, paired Market download commands."""

from __future__ import annotations

import asyncio
import json
import portalocker

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from main_routers.local_access import is_direct_loopback_request
from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import (
    _is_loopback_request,
    _validate_local_mutation_request,
)
from services.model_workshop.bundle import BundleError, BundleLimits
from services.model_workshop.catalog import public_result as _public_result
from services.model_workshop.download import ConnectedDownloads
from services.model_workshop.paths import WorkshopError, WorkshopPaths, write_all
from services.model_workshop.registry import Registry
from services.model_workshop.runtime import get_service, maintain
from services.model_workshop.tasks import _drain_sync
from utils.asgi_body_limit import InboundBodySizeLimitMiddleware
from utils.cloudsave_runtime import MaintenanceModeError


def _require_local_access(request: Request):
    """Use the shared direct-peer policy without importing credential-management state."""
    if not is_direct_loopback_request(request):
        raise HTTPException(status_code=403, detail="Local access required")


router = APIRouter(
    prefix="/api/model-market",
    tags=["model-market"],
    dependencies=[Depends(_require_local_access)],
)
MAX_IMPORT_BODY = BundleLimits().archive_bytes + 1024 * 1024
# Construction is inert: no network, writer or recovery is started at import.
connected_downloads = ConnectedDownloads()


def _preflight(scope):
    request = Request(scope)
    if not _is_loopback_request(request):
        return JSONResponse({"error_code": "local_access_required"}, status_code=403)
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        return rejected
    if scope.get("path", "").rstrip("/") == "/api/model-market/imports":
        # Reject unsupported encodings without consuming even one body chunk.
        content_type = request.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
            return JSONResponse(
                {"error_code": "model_import_multipart_required"}, status_code=415
            )
        if len(content_type) > 16 * 1024:
            return JSONResponse(
                {"error_code": "model_import_incomplete"}, status_code=400
            )
    return None


def _error(exc):
    code = (
        str(exc)
        if isinstance(exc, (WorkshopError, BundleError))
        else "model_workshop_io_error"
    )
    status = 422 if isinstance(exc, BundleError) else 503
    if code in {"model_workshop_task_not_found", "model_market_expired", "model_market_operation_unknown"}:
        status = 404
    elif code.endswith("too_large"):
        status = 413
    elif code in (
        "model_workshop_busy",
        "model_workshop_install_conflict",
        "model_workshop_closed",
    ):
        status = 409
    elif code.startswith("model_market_") or code.startswith("market_auth_"):
        status = 502 if code == "model_market_unavailable" else 409
    if isinstance(exc, MaintenanceModeError):
        code, status = "model_workshop_storage_blocked", 409
    return JSONResponse(
        {"success": False, "error_code": code},
        status_code=status,
        headers={"Cache-Control": "no-store", "Retry-After": "1"}
        if status in (409, 503)
        else {"Cache-Control": "no-store"},
    )


async def _command(request, keys):
    """Authenticate before consuming even a small selection body; reject extras."""
    rejected = _preflight(request.scope)
    if rejected is not None:
        raise HTTPException(rejected.status_code, detail="local mutation refused")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(415, detail="JSON model command required")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            raise HTTPException(413, detail="model command too large")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise HTTPException(400, detail="invalid model command") from None
    if not isinstance(value, dict) or set(value) != keys:
        raise HTTPException(400, detail="invalid model command fields")
    return value


@router.post("/launch")
async def launch_market(request: Request, response: Response):
    await _command(request, set())
    # Pairing URLs and role/task results must not survive in HTTP caches.
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **await connected_downloads.launch(
            get_config_manager(), str(request.base_url).rstrip("/"))}
    except (WorkshopError, OSError, MaintenanceModeError) as error:
        return _error(error)


@router.get("/launch/{request_id}")
async def connected_status(request_id: str, response: Response, operation_id: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **connected_downloads.status(get_config_manager(), request_id, operation_id)}
    except (WorkshopError, OSError) as error:
        return _error(error)


@router.post("/launch/{request_id}/select", status_code=202)
async def select_market_model(request_id: str, request: Request, response: Response):
    value = await _command(request, {"model_id", "expected_sha256", "byte_size", "operation_id"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **await connected_downloads.select(get_config_manager(), request_id,
            value["model_id"], value["expected_sha256"], value["byte_size"], value["operation_id"])}
    except (WorkshopError, OSError, MaintenanceModeError) as error:
        return _error(error)


@router.post("/launch/{request_id}/cancel")
async def cancel_connected_download(request_id: str, request: Request, response: Response):
    value = await _command(request, {"operation_id"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **connected_downloads.cancel(request_id, value["operation_id"])}
    except WorkshopError as error:
        return _error(error)


@router.post("/launch/{request_id}/apply")
async def apply_connected_model(request_id: str, request: Request, response: Response):
    # Deliberately absent from the remote bridge allowlist: only the native
    # manager can authorize this step after checking its own unsaved edits.
    value = await _command(request, {"operation_id"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **await connected_downloads.apply(get_config_manager(), request_id, value["operation_id"])}
    except (WorkshopError, OSError, MaintenanceModeError) as error:
        return _error(error)


@router.post("/launch/{request_id}/ready")
async def ready_connected_model(request_id: str, request: Request, response: Response):
    # Like apply, this permission is local-only and absent from every remote allowlist.
    value = await _command(request, {"operation_id"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **await connected_downloads.ready(get_config_manager(), request_id, value["operation_id"])}
    except (WorkshopError, OSError, MaintenanceModeError) as error:
        return _error(error)


@router.post("/launch/{request_id}/invalidate")
async def invalidate_model_connection(request_id: str, request: Request, response: Response):
    # Unsaved local edits revoke the pairing without granting the Market a save capability.
    await _command(request, set())
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, **connected_downloads.invalidate(request_id)}
    except WorkshopError as error:
        return _error(error)


class ModelImportBodyGuard:
    """Authorize, acquire ownership and bound simultaneous parsers before reading any body.

    All unrelated paths bypass this adapter and keep the existing global, Avatar
    Tool and external-memory policies. Main's InstanceAccess/Host guards wrap it.
    """

    def __init__(self, app, config_getter=get_config_manager):
        self.app, self.config_getter = app, config_getter
        self._slots = asyncio.Semaphore(2)
        self._bounded = InboundBodySizeLimitMiddleware(
            app,
            multipart_path_prefix="/api/model-market/imports",
            multipart_methods=("POST",),
            max_multipart_body_bytes=MAX_IMPORT_BODY,
        )

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != "/api/model-market/imports"
        ):
            return await self.app(scope, receive, send)
        rejected = _preflight(scope)
        if rejected is not None:
            return await rejected(scope, receive, send)
        if self._slots.locked():
            return await _error(WorkshopError("model_workshop_busy"))(
                scope, receive, send
            )
        await self._slots.acquire()
        upload_owner = None

        def admit():
            nonlocal upload_owner
            service = get_service(self.config_getter())
            service.begin_upload()
            # Transfer this lease inside the worker: cancellation may discard
            # the await result after admission has already succeeded.
            upload_owner = service

        try:
            try:
                await _drain_sync(admit)
            except (
                WorkshopError,
                OSError,
                MaintenanceModeError,
                portalocker.exceptions.LockException,
            ) as exc:
                return await _error(exc)(scope, receive, send)
            await self._bounded(scope, receive, send)
        finally:
            try:
                if upload_owner is not None:
                    await _drain_sync(upload_owner.finish_upload)
            finally:
                self._slots.release()


class _OwnedUpload(UploadFile):
    """Do not close a spool while a cancelled parser's write/seek still runs."""

    async def write(self, data):
        if self.size is not None:
            self.size += len(data)
        await _drain_sync(lambda: write_all(self.file, data))

    async def seek(self, offset):
        await _drain_sync(lambda: self.file.seek(offset))

    async def close(self):
        await _drain_sync(self.file.close)


class _PackageFormParser(MultiPartParser):
    """One file, no fields, bounded headers, and mandatory final multipart boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, max_files=1, max_fields=0, **kwargs)
        self.completed = False
        self._header_bytes = 0

    def on_part_begin(self):
        self._header_bytes = 0
        super().on_part_begin()

    def on_header_value(self, data, start, end):
        self._header_bytes += end - start
        if self._header_bytes > 16 * 1024:
            raise MultiPartException("Model import headers are too large")
        super().on_header_value(data, start, end)

    def on_header_field(self, data, start, end):
        self._header_bytes += end - start
        if self._header_bytes > 16 * 1024:
            raise MultiPartException("Model import headers are too large")
        super().on_header_field(data, start, end)

    def on_headers_finished(self):
        super().on_headers_finished()
        current = self._current_part.file
        if current is not None:
            self._current_part.file = _OwnedUpload(
                current.file,
                size=current.size,
                filename=current.filename,
                headers=current.headers,
            )

    def on_end(self):
        self.completed = True
        super().on_end()

    async def parse(self):
        try:
            form = await super().parse()
            if not self.completed:
                raise MultiPartException("Incomplete model import")
            return form
        except BaseException:
            for spool in self._files_to_close_on_error:
                await _drain_sync(spool.close)
            raise


def _public_task(config, data):
    return dict(
        task_id=data["task_id"],
        phase=data["phase"],
        error_code=data["error"],
        result=_public_result(config, data["result"]) if data["result"] else None,
    )


@router.post("/imports", status_code=202)
async def import_package(request: Request):
    rejected = _preflight(request.scope)
    if rejected is not None:
        return rejected
    config = get_config_manager()

    # Bound individual parser callback chunks even for an unusual ASGI producer.
    async def chunks():
        async for chunk in request.stream():
            for start in range(0, len(chunk), 64 * 1024):
                yield chunk[start : start + 64 * 1024]

    form = None
    try:
        parser = _PackageFormParser(request.headers, chunks())
        form = await parser.parse()
        items = form.multi_items()
        if (
            len(items) != 1
            or items[0][0] != "file"
            or not isinstance(items[0][1], UploadFile)
        ):
            raise MultiPartException("One model package is required")
        source = items[0][1].file
        # The parser owns source until this drained copy has finished. Request
        # cancellation cannot close it under a late thread; N2 owns accepted bytes.
        attempt = await _drain_sync(lambda: get_service(config).receive(source))
        return {"success": True, "task_id": attempt}
    except (MultiPartException, MultipartParseError, ClientDisconnect):
        return JSONResponse(
            {"success": False, "error_code": "model_import_incomplete"}, status_code=400
        )
    except (
        BundleError,
        WorkshopError,
        OSError,
        MaintenanceModeError,
        portalocker.exceptions.LockException,
    ) as exc:
        return _error(exc)
    finally:
        if form is not None:
            await form.close()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    config = get_config_manager()
    try:
        service = await _drain_sync(lambda: get_service(config))
        return await _drain_sync(lambda: _public_task(config, service.status(task_id)))
    except (
        WorkshopError,
        OSError,
        MaintenanceModeError,
        portalocker.exceptions.LockException,
    ) as exc:
        return _error(exc)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    rejected = _preflight(request.scope)
    if rejected is not None:
        return rejected
    config = get_config_manager()
    try:
        service = await _drain_sync(lambda: get_service(config))
        data = await service.acancel(task_id)
        return await _drain_sync(lambda: _public_task(config, data))
    except (
        WorkshopError,
        OSError,
        MaintenanceModeError,
        portalocker.exceptions.LockException,
    ) as exc:
        return _error(exc)


@router.get("/installed")
async def get_installed():
    config = get_config_manager()
    try:
        await _drain_sync(lambda: maintain(config))

        def result():
            rows = Registry(WorkshopPaths.capture(config)).installed()
            return {
                "success": True,
                "items": [_public_result(config, row) for row in rows],
            }

        return await _drain_sync(result)
    except (
        WorkshopError,
        OSError,
        MaintenanceModeError,
        portalocker.exceptions.LockException,
    ) as exc:
        return _error(exc)

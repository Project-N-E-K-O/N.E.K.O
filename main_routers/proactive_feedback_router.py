"""Local diagnostics and lifecycle for community proactive-evidence uploads."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore
from services.proactive_feedback import host
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime, SyncFailure, drain_settings, pending_settings
from services.proactive_feedback import capture
from services.proactive_feedback.runtime_v2 import ProactiveFeedbackRuntimeV2

router = APIRouter(prefix="/api/proactive/feedback", tags=["proactive-feedback"])
_runtime = None
_bootstrap_task = None
_closing = False


class ResumeBlockedBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch_id: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
    batch_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def _create_store():
    from utils.config_manager import get_config_manager
    manager = get_config_manager()
    version = 2 if os.environ.get("NEKO_PROACTIVE_FEEDBACK_PROTOCOL", "1") == "2" else 1
    return RecommendationStore(Path(manager.get_runtime_config_path("recommendation_feedback.sqlite3")),
                               protocol_version=version)


async def create_runtime():
    # Composition owns the concrete store; services receive a neutral instance.
    # A cancelled worker-thread read may complete late, but owns no HTTP pool.
    store = await asyncio.to_thread(_create_store)
    paused = os.environ.get("NEKO_PROACTIVE_FEEDBACK_UPLOAD_PAUSED", "").strip().lower() in {"1", "true", "yes", "on"}
    runtime_type = ProactiveFeedbackRuntimeV2 if store.protocol_version == 2 else ProactiveFeedbackRuntime
    options = {}
    if store.protocol_version == 2:
        from utils.preferences import reserve_current_settings_snapshot
        options["current_settings_provider"] = lambda owner: reserve_current_settings_snapshot(owner, capture.get_control_snapshot)
    return runtime_type(store, settings_drain=drain_settings, settings_pending_provider=pending_settings,
                        upload_paused=paused, **options)


@router.on_event("startup")
async def start_runtime():
    global _closing
    _closing = False
    from utils.preferences import install_settings_attribution_context
    # Pure in-memory assembly must finish in this task. An abandoned thread
    # could otherwise install an obsolete provider after shutdown resets it.
    install_settings_attribution_context(host.attribution_context)
    # The main application starts storage-backed work only after its selection
    # and initialization barrier. This hook installs attribution without opening
    # or pinning a database in an obsolete storage root.


def schedule_start():
    global _bootstrap_task
    if _closing or _runtime is not None or (_bootstrap_task is not None and not _bootstrap_task.done()):
        return

    async def bootstrap():
        global _runtime
        try:
            runtime = await create_runtime()
            if _closing or _bootstrap_task is not asyncio.current_task():
                await runtime.close()
                return
            _runtime = runtime
            if isinstance(runtime, ProactiveFeedbackRuntimeV2):
                from main_routers.recommendation_router import install_store_protocol
                from utils.preferences import install_settings_capture_provider
                install_store_protocol(2)
                capture.install_control_provider(runtime.control_snapshot)
                install_settings_capture_provider(capture.get_control_snapshot, protocol_version=2)
            runtime.start()
        except (OSError, sqlite3.Error, RuntimeError, ValueError):
            # Optional feedback synchronization cannot prevent normal chat boot.
            pass

    _bootstrap_task = asyncio.create_task(bootstrap(), name="proactive-feedback-bootstrap")


@router.on_event("shutdown")
async def close_runtime():
    global _runtime, _bootstrap_task, _closing
    _closing = True
    bootstrap, _bootstrap_task = _bootstrap_task, None
    runtime, _runtime = _runtime, None
    # These setters do no IO. Retire this lifecycle before the first await;
    # an overlapping startup may then install its own bindings, which this
    # old closer must never reset after joining an old task or HTTP pool.
    capture.install_control_provider(None)
    from main_routers.recommendation_router import install_store_protocol
    install_store_protocol(1)
    from utils.preferences import install_settings_capture_provider, install_settings_attribution_context
    install_settings_capture_provider(None, protocol_version=1)
    install_settings_attribution_context(None)
    if bootstrap is not None:
        bootstrap.cancel()
        try:
            await bootstrap
        except asyncio.CancelledError:
            pass
    if runtime is not None:
        await runtime.close()


def _validate(request):
    from main_routers.system_router._shared import _validate_local_mutation_request
    return _validate_local_mutation_request(request)


async def _operate(request, *, synchronize):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    if _runtime is None:
        return JSONResponse({"success": False, "error_code": "feedback_not_started"}, status_code=503)
    try:
        result = await _runtime.sync_once() if synchronize else await _runtime.status()
    except (OSError, sqlite3.Error, RuntimeError, SyncFailure, ValueError):
        return JSONResponse({"success": False, "error_code": "feedback_unavailable"}, status_code=503)
    return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})


@router.get("/status")
async def feedback_status(request: Request):
    return await _operate(request, synchronize=False)


@router.post("/sync")
async def feedback_sync(request: Request):
    return await _operate(request, synchronize=True)


class CaptureContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    clock_session_id: str = Field(min_length=32, max_length=36)


class ActivateDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    activation_context: str | None = Field(default=None, min_length=1, max_length=2048)
    replace_registration_id: str | None = Field(default=None, max_length=36)
    expected_replace_registration_version: int | None = Field(default=None, ge=1, le=2**53 - 1)
    display_name: str | None = Field(default=None, max_length=64)
    expected_owner_key: str | None = Field(default=None, min_length=1, max_length=128)


class RemoveDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_registration_version: int = Field(ge=1)
    expected_owner_key: str | None = Field(default=None, min_length=1, max_length=128)


def _control_failure(error):
    code = error.code if isinstance(error, SyncFailure) else "feedback_control_unavailable"
    status = 426 if code == "upgrade_required" else 409 if code == "cloud_conflict" else 503
    return JSONResponse({"success": False, "error_code": code}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def _control_command(request, model, operation, *, max_body_bytes=2048):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    runtime = _runtime
    if not isinstance(runtime, ProactiveFeedbackRuntimeV2):
        return JSONResponse({"success": False, "error_code": "feedback_protocol_unavailable"}, status_code=503)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        return JSONResponse({"success": False, "error_code": "invalid_feedback_control"}, status_code=415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > max_body_bytes:
            return JSONResponse({"success": False, "error_code": "invalid_feedback_control"}, status_code=413)
        raw.extend(chunk)
    try:
        command = model.model_validate(json.loads(raw))
        result = await operation(runtime, command)
    except (ValueError, UnicodeError, ValidationError, RecursionError):
        return JSONResponse({"success": False, "error_code": "invalid_feedback_control"}, status_code=422)
    except (SyncFailure, OSError, sqlite3.Error, httpx.HTTPError, TimeoutError) as error:
        return _control_failure(error)
    return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})


@router.post("/capture-context")
async def feedback_capture_context(request: Request):
    async def run(runtime, command):
        return {"context": await runtime.capture_context(command.clock_session_id)}
    return await _control_command(request, CaptureContextRequest, run)


@router.post("/devices/activate")
async def feedback_activate_device(request: Request):
    async def run(runtime, command):
        return await runtime.activate_device(command.replace_registration_id, command.display_name, command.expected_owner_key,
            expected_replace_registration_version=command.expected_replace_registration_version,
            activation_context=command.activation_context)
    # Allow the 2048-character signed ASCII proof plus optional fields, even
    # when JSON escapes each character. Other control commands keep 2 KiB.
    return await _control_command(request, ActivateDeviceRequest, run, max_body_bytes=16 * 1024)


@router.get("/devices")
async def feedback_devices(request: Request):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    if not isinstance(_runtime, ProactiveFeedbackRuntimeV2):
        return JSONResponse({"success": False, "error_code": "feedback_protocol_unavailable"}, status_code=503)
    try:
        result = await _runtime.list_devices()
    except (SyncFailure, OSError, sqlite3.Error, httpx.HTTPError, TimeoutError) as error:
        return _control_failure(error)
    return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})


@router.post("/devices/{registration_id}/remove")
async def feedback_remove_device(request: Request, registration_id: str):
    async def run(runtime, command):
        return await runtime.remove_device(registration_id, command.expected_registration_version, command.expected_owner_key)
    return await _control_command(request, RemoveDeviceRequest, run)


@router.get("/manage")
async def feedback_management_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "templates" / "feedback_devices.html",
                        headers={"Cache-Control": "no-store"})


@router.post("/resume")
async def feedback_resume(request: Request):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    runtime = _runtime
    if runtime is None:
        return JSONResponse({"success": False, "error_code": "feedback_not_started"}, status_code=503)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        return JSONResponse({"success": False, "error_code": "invalid_feedback_resume"}, status_code=415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 2048:
            return JSONResponse({"success": False, "error_code": "invalid_feedback_resume"}, status_code=413)
        raw.extend(chunk)
    try:
        command = ResumeBlockedBatch.model_validate(json.loads(raw))
    except (ValueError, UnicodeError, ValidationError, RecursionError):
        return JSONResponse({"success": False, "error_code": "invalid_feedback_resume"}, status_code=422)
    try:
        result = await runtime.resume_blocked_batch(**command.model_dump())
    except FeedbackConflict:
        return JSONResponse({"success": False, "error_code": "feedback_resume_conflict"}, status_code=409)
    except SyncFailure as error:
        return JSONResponse({"success": False, "error_code": error.code}, status_code=409,
                            headers={"Cache-Control": "no-store"})
    except (OSError, sqlite3.Error, RuntimeError, TimeoutError, ValueError, httpx.HTTPError):
        return JSONResponse({"success": False, "error_code": "feedback_unavailable"}, status_code=503)
    return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})

"""Local diagnostics and lifecycle for community proactive-evidence uploads."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore
from services.proactive_feedback import host
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime, SyncFailure, drain_settings, pending_settings

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
    return RecommendationStore(Path(manager.get_runtime_config_path("recommendation_feedback.sqlite3")))


async def create_runtime():
    # Composition owns the concrete store; services receive a neutral instance.
    # A cancelled worker-thread read may complete late, but owns no HTTP pool.
    store = await asyncio.to_thread(_create_store)
    paused = os.environ.get("NEKO_PROACTIVE_FEEDBACK_UPLOAD_PAUSED", "").strip().lower() in {"1", "true", "yes", "on"}
    return ProactiveFeedbackRuntime(store, settings_drain=drain_settings, settings_pending_provider=pending_settings,
                                    upload_paused=paused)


@router.on_event("startup")
async def start_runtime():
    global _closing
    _closing = False
    from utils.preferences import install_settings_attribution_context
    await asyncio.to_thread(install_settings_attribution_context, host.attribution_context)
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
            if _closing:
                await runtime.close()
                return
            _runtime = runtime
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
    if bootstrap is not None:
        bootstrap.cancel()
        try:
            await bootstrap
        except asyncio.CancelledError:
            pass
    runtime, _runtime = _runtime, None
    if runtime is not None:
        await runtime.close()
    from utils.preferences import install_settings_attribution_context
    await asyncio.to_thread(install_settings_attribution_context, None)


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

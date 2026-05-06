from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from plugin._types.models import RunCreateRequest
from plugin.logging_config import get_logger
from plugin.plugins.galgame_plugin.store import GalgameStore
from plugin.plugins.galgame_plugin.install_tasks import (
    INSTALL_TERMINAL_STATUSES,
    build_install_task_state,
    load_install_task_state,
    load_latest_install_task_state,
    update_install_task_state,
)
from plugin.sdk.shared.storage import PluginStore
from plugin.server.application.runs import RunService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter(tags=["galgame-install"])
logger = get_logger("galgame.install_routes")
run_service = RunService()

_INSTALL_PLUGIN_IDS = {"galgame_plugin"}
_STALE_INSTALL_STATUS = "failed"
_STALE_INSTALL_PHASE = "failed"
_UI_I18N_DIR = Path(__file__).resolve().parent / "i18n" / "ui"
_ALLOWED_UI_LOCALES = {"zh-CN", "en", "ja", "ru", "ko"}


class InstallStartPayload(BaseModel):
    force: bool = False


@router.get("/plugin/{plugin_id}/ui-api/locale")
async def get_galgame_ui_locale(plugin_id: str) -> JSONResponse:
    _ensure_has_install(plugin_id)
    try:
        from utils.language_utils import get_global_language_full

        locale = _normalize_ui_locale(str(get_global_language_full() or "zh-CN"))
    except Exception:
        locale = "zh-CN"
    return JSONResponse({"locale": locale})


@router.get("/plugin/{plugin_id}/ui-api/i18n/ui/{locale}.json")
async def get_galgame_ui_i18n(plugin_id: str, locale: str) -> Response:
    _ensure_has_install(plugin_id)
    normalized = str(locale or "").strip()
    if ".." in normalized or "/" in normalized or "\\" in normalized:
        return Response(status_code=404)
    if normalized not in _ALLOWED_UI_LOCALES:
        return Response(status_code=404)
    file = _UI_I18N_DIR / f"{normalized}.json"
    if not file.is_file():
        return Response(status_code=404)
    return FileResponse(file)


def _normalize_ui_locale(locale: str) -> str:
    normalized = str(locale or "").strip().replace("_", "-").lower()
    if normalized == "zh" or normalized.startswith("zh-"):
        return "zh-CN"
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ja"):
        return "ja"
    if normalized.startswith("ru"):
        return "ru"
    if normalized.startswith("ko"):
        return "ko"
    return "zh-CN"


def _get_install_kind_spec(kind: str) -> dict[str, str]:
    normalized = str(kind or "").strip().lower()
    # rapidocr + dxcam used to live here as runtime-pip-install entries; both are
    # now bundled into the main program (see pyproject.toml [dependency-groups]
    # galgame), so only textractor + tesseract still need the install machinery.
    mapping = {
        "textractor": {
            "kind": "textractor",
            "entry_id": "galgame_install_textractor",
            "label": "Textractor",
            "queued_message": "Textractor install queued",
        },
        "tesseract": {
            "kind": "tesseract",
            "entry_id": "galgame_install_tesseract",
            "label": "Tesseract",
            "queued_message": "Tesseract install queued",
        },
    }
    spec = mapping.get(normalized)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unsupported install kind: {kind!r}")
    return spec


def _ensure_has_install(plugin_id: str) -> None:
    if plugin_id not in _INSTALL_PLUGIN_IDS:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no install API")


def _run_to_install_status(run_status: str) -> str:
    mapping = {
        "queued": "queued",
        "running": "running",
        "cancel_requested": "canceled",
        "canceled": "canceled",
        "succeeded": "completed",
        "failed": "failed",
        "timeout": "failed",
    }
    return mapping.get(run_status, "queued")


def _install_state_from_run(run_record, *, kind: str) -> dict[str, object]:
    metrics = dict(getattr(run_record, "metrics", {}) or {})
    status = _run_to_install_status(str(getattr(run_record, "status", "") or "queued"))
    phase = str(getattr(run_record, "stage", "") or status)
    message = str(getattr(run_record, "message", "") or "")
    progress = getattr(run_record, "progress", None)
    run_error = getattr(run_record, "error", None)
    error_message = ""
    if run_error is not None:
        error_message = str(getattr(run_error, "message", "") or "")
    payload = build_install_task_state(
        task_id=str(getattr(run_record, "task_id", None) or getattr(run_record, "run_id")),
        run_id=str(getattr(run_record, "run_id")),
        kind=kind,
        status=status,
        phase=phase,
        message=message,
        progress=float(progress) if isinstance(progress, (int, float)) else 0.0,
        downloaded_bytes=int(metrics.get("downloaded_bytes") or 0),
        total_bytes=int(metrics.get("total_bytes") or 0),
        resume_from=int(metrics.get("resume_from") or 0),
        release_name=str(metrics.get("release_name") or ""),
        asset_name=str(metrics.get("asset_name") or ""),
        target_dir=str(metrics.get("target_dir") or ""),
        detected_path=str(metrics.get("detected_path") or ""),
        error=error_message,
    )
    payload["started_at"] = getattr(run_record, "started_at", None) or payload["started_at"]
    payload["updated_at"] = getattr(run_record, "updated_at", None) or payload["updated_at"]
    payload["completed_at"] = getattr(run_record, "finished_at", None) or payload.get("completed_at")
    return payload


def _persist_install_payload(task_id: str, *, kind: str, payload: dict[str, object]) -> dict[str, object]:
    return update_install_task_state(
        task_id,
        kind=kind,
        run_id=str(payload.get("run_id") or task_id),
        status=str(payload.get("status") or "queued"),
        phase=str(payload.get("phase") or payload.get("status") or "queued"),
        message=str(payload.get("message") or ""),
        progress=float(payload.get("progress") or 0.0),
        downloaded_bytes=int(payload.get("downloaded_bytes") or 0),
        total_bytes=int(payload.get("total_bytes") or 0),
        resume_from=int(payload.get("resume_from") or 0),
        release_name=str(payload.get("release_name") or ""),
        asset_name=str(payload.get("asset_name") or ""),
        target_dir=str(payload.get("target_dir") or ""),
        detected_path=str(payload.get("detected_path") or ""),
        error=str(payload.get("error") or ""),
    )


def _mark_stale_install_task(
    task_id: str,
    *,
    kind: str,
    label: str,
    payload: dict[str, object],
) -> dict[str, object]:
    previous_phase = str(payload.get("phase") or payload.get("status") or "queued")
    error_message = (
        f"{label} 安装任务在完成前被中断，对应的后台运行记录已经不存在。"
        f"上一次阶段：{previous_phase}。请直接重新发起安装。"
    )
    logger.warning(
        "marking stale {} install task as failed: task_id={}, previous_phase={}",
        kind,
        task_id,
        previous_phase,
    )
    stale_payload = dict(payload)
    stale_payload.update(
        {
            "task_id": task_id,
            "run_id": str(payload.get("run_id") or task_id),
            "kind": kind,
            "status": _STALE_INSTALL_STATUS,
            "phase": _STALE_INSTALL_PHASE,
            "message": error_message,
            "error": error_message,
            "completed_at": time.time(),
        }
    )
    return _persist_install_payload(task_id, kind=kind, payload=stale_payload)


def _resolve_install_task_payload(task_id: str, *, kind: str, label: str) -> dict[str, object]:
    task_id = (task_id or "").strip()
    if not task_id or ".." in task_id or "/" in task_id or "\\" in task_id:
        raise HTTPException(status_code=400, detail=f"Invalid {label} install task_id")
    state_payload = load_install_task_state(task_id, kind=kind)

    # Short-circuit: persisted terminal states don't need a live run lookup.
    if state_payload is not None:
        state_status = str(state_payload.get("status") or "")
        if state_status in INSTALL_TERMINAL_STATUSES:
            return dict(state_payload)

    run_missing = False
    try:
        run_record = run_service.get_run(task_id)
    except ServerDomainError as error:
        if error.code == "RUN_NOT_FOUND":
            run_record = None
            run_missing = True
        else:
            raise_http_from_domain(error, logger=logger)

    if state_payload is None and run_record is None:
        raise HTTPException(status_code=404, detail=f"{label} install task '{task_id}' not found")

    if state_payload is None and run_record is not None:
        run_payload = _install_state_from_run(run_record, kind=kind)
        if str(run_payload.get("status") or "") in INSTALL_TERMINAL_STATUSES:
            return _persist_install_payload(task_id, kind=kind, payload=run_payload)
        return run_payload

    payload = dict(state_payload or {})
    if run_record is None:
        state_status = str(payload.get("status") or "")
        if run_missing and state_status not in INSTALL_TERMINAL_STATUSES:
            return _mark_stale_install_task(task_id, kind=kind, label=label, payload=payload)
        return payload

    run_payload = _install_state_from_run(run_record, kind=kind)
    payload["run_id"] = str(payload.get("run_id") or run_payload.get("run_id") or task_id)
    payload["task_id"] = str(payload.get("task_id") or task_id)

    state_status = str(payload.get("status") or "")
    run_status = str(run_payload.get("status") or "")
    if state_status in INSTALL_TERMINAL_STATUSES:
        return payload
    if run_status in INSTALL_TERMINAL_STATUSES:
        payload["status"] = run_status
        payload["phase"] = str(run_payload.get("phase") or run_status)
        payload["message"] = str(run_payload.get("message") or payload.get("message") or "")
        payload["progress"] = float(run_payload.get("progress") or payload.get("progress") or 0.0)
        payload["error"] = str(run_payload.get("error") or payload.get("error") or "")
        payload["release_name"] = str(run_payload.get("release_name") or payload.get("release_name") or "")
        payload["asset_name"] = str(run_payload.get("asset_name") or payload.get("asset_name") or "")
        payload["target_dir"] = str(run_payload.get("target_dir") or payload.get("target_dir") or "")
        payload["detected_path"] = str(run_payload.get("detected_path") or payload.get("detected_path") or "")
        payload["updated_at"] = run_payload.get("updated_at")
        payload["completed_at"] = run_payload.get("completed_at")
        return _persist_install_payload(task_id, kind=kind, payload=payload)

    payload["status"] = run_status or state_status
    if run_payload.get("phase"):
        payload["phase"] = run_payload["phase"]
    if run_payload.get("message"):
        payload["message"] = run_payload["message"]
    if isinstance(run_payload.get("progress"), (int, float)):
        payload["progress"] = float(run_payload["progress"])
    metrics = dict(getattr(run_record, "metrics", {}) or {})
    if not payload.get("downloaded_bytes") and metrics.get("downloaded_bytes") is not None:
        payload["downloaded_bytes"] = int(metrics.get("downloaded_bytes") or 0)
    if not payload.get("total_bytes") and metrics.get("total_bytes") is not None:
        payload["total_bytes"] = int(metrics.get("total_bytes") or 0)
    if not payload.get("resume_from") and metrics.get("resume_from") is not None:
        payload["resume_from"] = int(metrics.get("resume_from") or 0)
    payload["updated_at"] = getattr(run_record, "updated_at", None) or payload.get("updated_at")
    return payload


async def _start_install_task(
    *,
    plugin_id: str,
    kind: str,
    payload: InstallStartPayload,
    request: Request,
) -> JSONResponse:
    _ensure_has_install(plugin_id)
    spec = _get_install_kind_spec(kind)
    try:
        client_host = request.client.host if request.client is not None else None
        created = await run_service.create_run(
            RunCreateRequest(
                plugin_id=plugin_id,
                entry_id=spec["entry_id"],
                args={"force": bool(payload.force)},
            ),
            client_host=client_host,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)

    local_save_failed = False
    try:
        state_payload = update_install_task_state(
            created.run_id,
            kind=spec["kind"],
            run_id=created.run_id,
            status="queued",
            phase="queued",
            message=spec["queued_message"],
            progress=0.0,
        )
    except OSError:
        logger.exception(
            "failed to persist local install state for run=%s kind=%s",
            created.run_id,
            spec["kind"],
        )
        local_save_failed = True
        state_payload = {}
    return JSONResponse(
        {
            "task_id": created.run_id,
            "run_id": created.run_id,
            "status": created.status,
            "state": state_payload,
            "local_save_failed": local_save_failed,
        }
    )


def _latest_install_task_payload(*, plugin_id: str, kind: str) -> JSONResponse:
    _ensure_has_install(plugin_id)
    spec = _get_install_kind_spec(kind)
    payload = load_latest_install_task_state(kind=spec["kind"])
    if payload is None:
        raise HTTPException(status_code=404, detail=f"No {spec['label']} install task found")
    task_id = str(payload.get("task_id") or "").strip()
    return JSONResponse(
        _resolve_install_task_payload(task_id, kind=spec["kind"], label=spec["label"])
    )


def _get_install_task_payload(*, plugin_id: str, kind: str, task_id: str) -> JSONResponse:
    _ensure_has_install(plugin_id)
    spec = _get_install_kind_spec(kind)
    return JSONResponse(
        _resolve_install_task_payload(task_id, kind=spec["kind"], label=spec["label"])
    )


def _install_stream_response(*, plugin_id: str, kind: str, task_id: str, request: Request) -> StreamingResponse:
    _ensure_has_install(plugin_id)
    spec = _get_install_kind_spec(kind)
    _resolve_install_task_payload(task_id, kind=spec["kind"], label=spec["label"])

    async def _event_stream():
        last_payload = ""
        idle_cycles = 0
        while True:
            if await request.is_disconnected():
                break
            payload = _resolve_install_task_payload(
                task_id,
                kind=spec["kind"],
                label=spec["label"],
            )
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if serialized != last_payload:
                last_payload = serialized
                idle_cycles = 0
                yield f"data: {serialized}\n\n"
                if str(payload.get("status") or "") in INSTALL_TERMINAL_STATUSES:
                    break
            else:
                idle_cycles += 1
                if idle_cycles % 20 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/plugin/{plugin_id}/ui-api/textractor/install")
async def galgame_plugin_start_textractor_install(
    plugin_id: str,
    payload: InstallStartPayload,
    request: Request,
):
    return await _start_install_task(
        plugin_id=plugin_id,
        kind="textractor",
        payload=payload,
        request=request,
    )


@router.post("/plugin/{plugin_id}/ui-api/tesseract/install")
async def galgame_plugin_start_tesseract_install(
    plugin_id: str,
    payload: InstallStartPayload,
    request: Request,
):
    return await _start_install_task(
        plugin_id=plugin_id,
        kind="tesseract",
        payload=payload,
        request=request,
    )


@router.get("/plugin/{plugin_id}/ui-api/textractor/install/latest")
async def galgame_plugin_latest_textractor_install(plugin_id: str):
    return _latest_install_task_payload(plugin_id=plugin_id, kind="textractor")


@router.get("/plugin/{plugin_id}/ui-api/tesseract/install/latest")
async def galgame_plugin_latest_tesseract_install(plugin_id: str):
    return _latest_install_task_payload(plugin_id=plugin_id, kind="tesseract")


@router.get("/plugin/{plugin_id}/ui-api/textractor/install/{task_id}")
async def galgame_plugin_get_textractor_install(plugin_id: str, task_id: str):
    return _get_install_task_payload(plugin_id=plugin_id, kind="textractor", task_id=task_id)


@router.get("/plugin/{plugin_id}/ui-api/tesseract/install/{task_id}")
async def galgame_plugin_get_tesseract_install(plugin_id: str, task_id: str):
    return _get_install_task_payload(plugin_id=plugin_id, kind="tesseract", task_id=task_id)


@router.get("/plugin/{plugin_id}/ui-api/textractor/install/{task_id}/stream")
async def galgame_plugin_stream_textractor_install(
    plugin_id: str,
    task_id: str,
    request: Request,
):
    return _install_stream_response(
        plugin_id=plugin_id,
        kind="textractor",
        task_id=task_id,
        request=request,
    )


@router.get("/plugin/{plugin_id}/ui-api/tesseract/install/{task_id}/stream")
async def galgame_plugin_stream_tesseract_install(
    plugin_id: str,
    task_id: str,
    request: Request,
):
    return _install_stream_response(
        plugin_id=plugin_id,
        kind="tesseract",
        task_id=task_id,
        request=request,
    )


# ====== Tutorial progress endpoints ======

_TUTORIAL_DEFAULTS = {
    "completed": False,
    "skipped": False,
    "last_step_index": 0,
    "started_at": 0.0,
    "completed_at": 0.0,
}
_tutorial_store_instance: GalgameStore | None = None


def _tutorial_store() -> GalgameStore:
    global _tutorial_store_instance
    if _tutorial_store_instance is not None:
        return _tutorial_store_instance
    plugin_dir = Path(__file__).resolve().parent
    plugin_store = PluginStore(
        plugin_id="galgame_plugin",
        plugin_dir=plugin_dir,
        logger=logger,
        enabled=True,
    )
    _tutorial_store_instance = GalgameStore(plugin_store, logger)
    return _tutorial_store_instance


class TutorialProgressPayload(BaseModel):
    completed: bool = False
    skipped: bool = False
    last_step_index: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0


def _read_tutorial_progress() -> dict[str, Any] | None:
    try:
        return _tutorial_store().load_tutorial_progress()
    except Exception:
        logger.warning("tutorial progress read failed", exc_info=True)
        return None


def _write_tutorial_progress(progress: dict[str, Any]) -> None:
    try:
        _tutorial_store().save_tutorial_progress(progress)
    except Exception:
        logger.warning("tutorial progress write failed", exc_info=True)
        raise


def _normalize_tutorial_progress(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(_TUTORIAL_DEFAULTS)

    result = dict(_TUTORIAL_DEFAULTS)
    for key in _TUTORIAL_DEFAULTS:
        if key in raw:
            result[key] = raw[key]
    result["completed"] = bool(result["completed"])
    result["skipped"] = bool(result["skipped"])
    try:
        result["last_step_index"] = max(0, int(result["last_step_index"] or 0))
    except (TypeError, ValueError):
        result["last_step_index"] = 0
    try:
        result["started_at"] = max(0.0, float(result["started_at"] or 0.0))
    except (TypeError, ValueError):
        result["started_at"] = 0.0
    try:
        result["completed_at"] = max(0.0, float(result["completed_at"] or 0.0))
    except (TypeError, ValueError):
        result["completed_at"] = 0.0
    return result


@router.get("/plugin/{plugin_id}/ui-api/tutorial/status")
async def get_tutorial_status(plugin_id: str) -> JSONResponse:
    _ensure_has_install(plugin_id)
    raw = _read_tutorial_progress()
    return JSONResponse({"ok": True, "progress": _normalize_tutorial_progress(raw)})


@router.post("/plugin/{plugin_id}/ui-api/tutorial/progress")
async def save_tutorial_progress(
    plugin_id: str,
    body: TutorialProgressPayload,
) -> JSONResponse:
    _ensure_has_install(plugin_id)
    payload = (
        body.model_dump(exclude_unset=True)
        if hasattr(body, "model_dump")
        else body.dict(exclude_unset=True)
    )
    current = _normalize_tutorial_progress(_read_tutorial_progress())
    normalized_payload = _normalize_tutorial_progress(payload)
    current.update(
        {
            key: normalized_payload[key]
            for key in payload
            if key in _TUTORIAL_DEFAULTS
        }
    )
    try:
        _write_tutorial_progress(current)
    except Exception:
        logger.warning("tutorial progress save failed", exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "Internal server error", "progress": current},
            status_code=500,
        )
    return JSONResponse({"ok": True, "progress": current})

"""
插件 UI 静态文件代理路由

允许插件注入自定义前端界面，通过 iframe 嵌入到主应用中。

插件目录结构：
    my_plugin/
    ├── __init__.py
    ├── plugin.toml
    └── static/           # 静态文件目录
        ├── index.html    # 入口文件
        ├── main.js
        └── style.css

访问路径：
    GET /plugin/{plugin_id}/ui/          -> static/index.html
    GET /plugin/{plugin_id}/ui/main.js   -> static/main.js
    GET /plugin/{plugin_id}/ui/style.css -> static/style.css
"""
import asyncio
import json
import mimetypes
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from plugin._types.models import RunCreateRequest
from plugin.logging_config import get_logger
from plugin.plugins.galgame_plugin.install_tasks import (
    INSTALL_TERMINAL_STATUSES,
    build_install_task_state,
    load_install_task_state,
    load_latest_install_task_state,
    update_install_task_state,
)
from plugin.server.application.runs import RunService
from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter(tags=["plugin-ui"])
logger = get_logger("server.routes.plugin_ui")
plugin_ui_query_service = PluginUiQueryService()
run_service = RunService()

_STALE_INSTALL_STATUS = "failed"
_STALE_INSTALL_PHASE = "failed"


class InstallStartPayload(BaseModel):
    force: bool = False


class HostedUiActionRequest(BaseModel):
    args: dict[str, object] = Field(default_factory=dict)
    kind: str = "panel"
    surface_id: str = "main"


def _get_install_kind_spec(kind: str) -> dict[str, str]:
    normalized = str(kind or "").strip().lower()
    mapping = {
        "rapidocr": {
            "kind": "rapidocr",
            "entry_id": "galgame_install_rapidocr",
            "label": "RapidOCR",
            "queued_message": "RapidOCR install queued",
        },
        "dxcam": {
            "kind": "dxcam",
            "entry_id": "galgame_install_dxcam",
            "label": "DXcam",
            "queued_message": "DXcam install queued",
        },
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
        raise HTTPException(status_code=404, detail=f"Unsupported galgame install kind: {kind!r}")
    return spec

async def _get_plugin_static_dir(plugin_id: str) -> Path | None:
    """获取插件的静态文件目录
    
    只有插件显式调用 register_static_ui() 后才会返回静态目录。
    
    Args:
        plugin_id: 插件 ID
    
    Returns:
        静态文件目录路径，如果未注册或不存在则返回 None
    """
    return await plugin_ui_query_service.get_static_dir(plugin_id)


async def _get_static_ui_config(plugin_id: str) -> dict[str, object] | None:
    """获取插件的静态 UI 配置"""
    return await plugin_ui_query_service.get_static_ui_config(plugin_id)


def _ensure_galgame_plugin(plugin_id: str) -> None:
    if plugin_id != "galgame_plugin":
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no galgame UI API")


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
    _ensure_galgame_plugin(plugin_id)
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

    state_payload = update_install_task_state(
        created.run_id,
        kind=spec["kind"],
        run_id=created.run_id,
        status="queued",
        phase="queued",
        message=spec["queued_message"],
        progress=0.0,
    )
    return JSONResponse(
        {
            "task_id": created.run_id,
            "run_id": created.run_id,
            "status": created.status,
            "state": state_payload,
        }
    )


def _latest_install_task_payload(*, plugin_id: str, kind: str) -> JSONResponse:
    _ensure_galgame_plugin(plugin_id)
    spec = _get_install_kind_spec(kind)
    payload = load_latest_install_task_state(kind=spec["kind"])
    if payload is None:
        raise HTTPException(status_code=404, detail=f"No {spec['label']} install task found")
    task_id = str(payload.get("task_id") or "").strip()
    return JSONResponse(
        _resolve_install_task_payload(task_id, kind=spec["kind"], label=spec["label"])
    )


def _get_install_task_payload(*, plugin_id: str, kind: str, task_id: str) -> JSONResponse:
    _ensure_galgame_plugin(plugin_id)
    spec = _get_install_kind_spec(kind)
    return JSONResponse(
        _resolve_install_task_payload(task_id, kind=spec["kind"], label=spec["label"])
    )


def _install_stream_response(*, plugin_id: str, kind: str, task_id: str, request: Request) -> StreamingResponse:
    _ensure_galgame_plugin(plugin_id)
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


def _get_mime_type(file_path: Path) -> str:
    """获取文件的 MIME 类型"""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        return mime_type
    
    # 默认类型映射
    suffix = file_path.suffix.lower()
    mime_map = {
        ".html": "text/html",
        ".htm": "text/html",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
    }
    return mime_map.get(suffix, "application/octet-stream")


@router.get("/plugin/{plugin_id}/ui")
@router.get("/plugin/{plugin_id}/ui/")
async def plugin_ui_index(plugin_id: str):
    """获取插件 UI 入口页面"""
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    index_file = static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' has no index.html in static directory"
        )
    
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui/{file_path:path}")
async def plugin_ui_file(plugin_id: str, file_path: str):
    """获取插件 UI 静态文件"""
    if not file_path:
        # 重定向到 index
        return await plugin_ui_index(plugin_id)
    
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    # 解析文件路径
    target_file = (static_dir / file_path).resolve()
    
    # 安全检查：确保文件在 static 目录内
    try:
        target_file.relative_to(static_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access denied: path traversal detected"
        )
    
    if not target_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path}"
        )
    
    if not target_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Not a file: {file_path}"
        )
    
    mime_type = _get_mime_type(target_file)
    
    # 获取缓存控制配置
    try:
        ui_config = await _get_static_ui_config(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    cache_control = "public, max-age=3600"
    if ui_config is not None:
        cache_control_obj = ui_config.get("cache_control")
        if isinstance(cache_control_obj, str) and cache_control_obj:
            cache_control = cache_control_obj
    
    return FileResponse(
        str(target_file),
        media_type=mime_type,
        headers={
            "Cache-Control": cache_control,
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui-info")
async def plugin_ui_info(plugin_id: str):
    """获取插件 UI 信息
    
    返回插件是否有 UI、UI 入口路径等信息。
    """
    try:
        ui_info = await plugin_ui_query_service.get_ui_info(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(ui_info)


@router.get("/plugin/{plugin_id}/surfaces")
async def plugin_ui_surfaces(plugin_id: str):
    """获取插件统一 UI Surface 列表。

    LEGACY_STATIC_UI_COMPAT:
    Existing static UI is normalized as a mode="static" panel surface.
    """
    try:
        surfaces = await plugin_ui_query_service.get_surfaces(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(surfaces)


@router.get("/plugin/{plugin_id}/hosted-ui/source")
async def plugin_hosted_ui_source(plugin_id: str, kind: str = "panel", id: str = "main"):
    """读取 hosted surface 源码。

    当前仅用于 hosted-tsx / markdown 的只读 source MVP。
    """
    try:
        source = await plugin_ui_query_service.get_surface_source(
            plugin_id,
            kind=kind,
            surface_id=id,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(source)


@router.get("/plugin/{plugin_id}/hosted-ui/context")
async def plugin_hosted_ui_context(plugin_id: str, kind: str = "panel", id: str = "main", locale: str | None = None):
    """获取 hosted surface 只读上下文。"""
    try:
        context = await plugin_ui_query_service.get_surface_context(
            plugin_id,
            kind=kind,
            surface_id=id,
            locale=locale,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(context)


@router.post("/plugin/{plugin_id}/hosted-ui/action/{action_id}")
async def plugin_hosted_ui_action(plugin_id: str, action_id: str, request: HostedUiActionRequest):
    """执行 hosted surface 动作；第一版复用本插件 plugin_entry。"""
    try:
        result = await plugin_ui_query_service.call_surface_action(
            plugin_id,
            action_id=action_id,
            args=request.args,
            kind=request.kind,
            surface_id=request.surface_id,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(result)


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


@router.post("/plugin/{plugin_id}/ui-api/rapidocr/install")
async def galgame_plugin_start_rapidocr_install(
    plugin_id: str,
    payload: InstallStartPayload,
    request: Request,
):
    return await _start_install_task(
        plugin_id=plugin_id,
        kind="rapidocr",
        payload=payload,
        request=request,
    )


@router.post("/plugin/{plugin_id}/ui-api/dxcam/install")
async def galgame_plugin_start_dxcam_install(
    plugin_id: str,
    payload: InstallStartPayload,
    request: Request,
):
    return await _start_install_task(
        plugin_id=plugin_id,
        kind="dxcam",
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


@router.get("/plugin/{plugin_id}/ui-api/rapidocr/install/latest")
async def galgame_plugin_latest_rapidocr_install(plugin_id: str):
    return _latest_install_task_payload(plugin_id=plugin_id, kind="rapidocr")


@router.get("/plugin/{plugin_id}/ui-api/dxcam/install/latest")
async def galgame_plugin_latest_dxcam_install(plugin_id: str):
    return _latest_install_task_payload(plugin_id=plugin_id, kind="dxcam")


@router.get("/plugin/{plugin_id}/ui-api/tesseract/install/latest")
async def galgame_plugin_latest_tesseract_install(plugin_id: str):
    return _latest_install_task_payload(plugin_id=plugin_id, kind="tesseract")


@router.get("/plugin/{plugin_id}/ui-api/textractor/install/{task_id}")
async def galgame_plugin_get_textractor_install(plugin_id: str, task_id: str):
    return _get_install_task_payload(plugin_id=plugin_id, kind="textractor", task_id=task_id)


@router.get("/plugin/{plugin_id}/ui-api/rapidocr/install/{task_id}")
async def galgame_plugin_get_rapidocr_install(plugin_id: str, task_id: str):
    return _get_install_task_payload(plugin_id=plugin_id, kind="rapidocr", task_id=task_id)


@router.get("/plugin/{plugin_id}/ui-api/dxcam/install/{task_id}")
async def galgame_plugin_get_dxcam_install(plugin_id: str, task_id: str):
    return _get_install_task_payload(plugin_id=plugin_id, kind="dxcam", task_id=task_id)


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


@router.get("/plugin/{plugin_id}/ui-api/rapidocr/install/{task_id}/stream")
async def galgame_plugin_stream_rapidocr_install(
    plugin_id: str,
    task_id: str,
    request: Request,
):
    return _install_stream_response(
        plugin_id=plugin_id,
        kind="rapidocr",
        task_id=task_id,
        request=request,
    )


@router.get("/plugin/{plugin_id}/ui-api/dxcam/install/{task_id}/stream")
async def galgame_plugin_stream_dxcam_install(
    plugin_id: str,
    task_id: str,
    request: Request,
):
    return _install_stream_response(
        plugin_id=plugin_id,
        kind="dxcam",
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

# -*- coding: utf-8 -*-
"""
Cloudsave Router

Provides cloudsave summary, single-character upload/download APIs,
and safety checks around runtime reload.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager, get_initialize_character_data, get_session_manager
from .characters_router import (
    notify_memory_server_reload,
    release_memory_server_character,
    send_reload_page_notice,
)
from .workshop_router import get_subscribed_workshop_items, get_workshop_item_details
from utils.cloudsave_runtime import (
    CloudsaveOperationError,
    build_cloudsave_character_detail,
    build_cloudsave_summary,
    export_cloudsave_character_unit,
    import_cloudsave_character_unit,
    is_cloudsave_provider_available,
    restore_cloudsave_operation_backup,
)


router = APIRouter(prefix="/api/cloudsave", tags=["cloudsave"])


def _decode_json_response_payload(response: JSONResponse) -> dict:
    try:
        return json.loads(response.body.decode("utf-8"))
    except Exception:
        return {}


def _default_workshop_status_payload(item_id: str, status: str = "") -> dict:
    return {
        "item_id": str(item_id or ""),
        "status": str(status or ""),
        "title": "",
        "author_name": "",
    }


def _derive_workshop_status_payload(item_id: str, item_info: dict | None) -> dict:
    item_info = item_info if isinstance(item_info, dict) else {}
    state = item_info.get("state") if isinstance(item_info.get("state"), dict) else {}
    installed = bool(state.get("installed"))
    subscribed = bool(state.get("subscribed"))

    if installed and subscribed:
        status = "installed_and_subscribed"
    elif installed:
        status = "installed_but_unsubscribed"
    elif subscribed:
        status = "subscribed_not_installed"
    else:
        status = "available_needs_resubscribe"

    return {
        "item_id": str(item_id or ""),
        "status": status,
        "title": str(item_info.get("title") or ""),
        "author_name": str(item_info.get("authorName") or ""),
    }


def _collect_workshop_item_ids(items: list[dict]) -> list[str]:
    item_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for scope in ("local", "cloud"):
            for source_prefix in (f"{scope}_asset", f"{scope}_origin"):
                if str(item.get(f"{source_prefix}_source") or "") != "steam_workshop":
                    continue
                source_id = str(item.get(f"{source_prefix}_source_id") or "").strip()
                if source_id:
                    item_ids.add(source_id)
    return sorted(item_ids)


async def _fetch_workshop_status_payload(item_id: str) -> dict:
    detail = await get_workshop_item_details(item_id)
    if isinstance(detail, JSONResponse):
        if detail.status_code == 404:
            return _default_workshop_status_payload(item_id, "unavailable")
        if detail.status_code == 503:
            return _default_workshop_status_payload(item_id, "steam_unavailable")
        return _default_workshop_status_payload(item_id, "unknown")

    if isinstance(detail, dict) and detail.get("success"):
        return _derive_workshop_status_payload(item_id, detail.get("item"))
    return _default_workshop_status_payload(item_id, "unknown")


async def _build_workshop_status_map(items: list[dict]) -> dict[str, dict]:
    item_ids = _collect_workshop_item_ids(items)
    if not item_ids:
        return {}

    status_map: dict[str, dict] = {}
    subscribed_lookup: dict[str, dict] = {}

    subscribed_items_result = await get_subscribed_workshop_items()
    if isinstance(subscribed_items_result, JSONResponse):
        if subscribed_items_result.status_code == 503:
            return {
                item_id: _default_workshop_status_payload(item_id, "steam_unavailable")
                for item_id in item_ids
            }
    elif isinstance(subscribed_items_result, dict) and subscribed_items_result.get("success"):
        for item_info in subscribed_items_result.get("items") or []:
            if not isinstance(item_info, dict):
                continue
            published_file_id = str(item_info.get("publishedFileId") or "").strip()
            if published_file_id:
                subscribed_lookup[published_file_id] = item_info

    for item_id in item_ids:
        if item_id in subscribed_lookup:
            status_map[item_id] = _derive_workshop_status_payload(item_id, subscribed_lookup[item_id])
        else:
            status_map[item_id] = await _fetch_workshop_status_payload(item_id)
    return status_map


def _apply_workshop_status_to_item(item: dict, workshop_status_map: dict[str, dict]) -> None:
    if not isinstance(item, dict):
        return

    for scope, source_prefix in (
        ("local", "local_asset"),
        ("cloud", "cloud_asset"),
        ("local_origin", "local_origin"),
        ("cloud_origin", "cloud_origin"),
    ):
        item[f"{scope}_workshop_status"] = ""
        item[f"{scope}_workshop_title"] = ""
        item[f"{scope}_workshop_author_name"] = ""

        if str(item.get(f"{source_prefix}_source") or "") != "steam_workshop":
            continue

        source_id = str(item.get(f"{source_prefix}_source_id") or "").strip()
        if not source_id:
            item[f"{scope}_workshop_status"] = "unknown"
            continue

        payload = workshop_status_map.get(source_id) or _default_workshop_status_payload(source_id, "unknown")
        item[f"{scope}_workshop_status"] = str(payload.get("status") or "")
        item[f"{scope}_workshop_title"] = str(payload.get("title") or "")
        item[f"{scope}_workshop_author_name"] = str(payload.get("author_name") or "")


async def _enrich_cloudsave_payload_with_workshop_status(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return payload

    items: list[dict] = []
    if isinstance(payload.get("items"), list):
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    elif isinstance(payload.get("item"), dict):
        items = [payload["item"]]

    if not items:
        return payload

    workshop_status_map = await _build_workshop_status_map(items)
    for item in items:
        _apply_workshop_status_to_item(item, workshop_status_map)
    return payload


def _cloudsave_error_response(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    character_name: str = "",
    extra: dict | None = None,
):
    payload = {
        "success": False,
        "error": code,
        "code": code,
        "message": message,
    }
    if character_name:
        payload["character_name"] = character_name
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


def _active_session_block_reason(character_name: str) -> str:
    session_manager = get_session_manager()
    mgr = session_manager.get(character_name)
    if mgr is None or not getattr(mgr, "is_active", False):
        return ""
    return "角色存在活跃会话，暂不允许云端下载覆盖，请先停止会话后重试"


def _local_character_exists(config_manager, character_name: str) -> bool:
    characters_payload = config_manager.load_characters()
    return character_name in (characters_payload.get("猫娘") or {})


def _operation_error_status_code(exc: CloudsaveOperationError, *, action: str) -> int:
    if exc.code in {"LOCAL_CHARACTER_NOT_FOUND", "CLOUD_CHARACTER_NOT_FOUND"}:
        return 404
    if exc.code in {"LOCAL_CHARACTER_EXISTS", "CLOUD_CHARACTER_EXISTS", "CLOUDSAVE_WRITE_FENCE_ACTIVE"}:
        return 409
    if exc.code == "NAME_AUDIT_FAILED":
        return 400
    if action in {"upload", "download"}:
        return 400
    return 400


async def _reload_after_character_download(character_name: str) -> tuple[bool, str]:
    initialize_character_data = get_initialize_character_data()
    await initialize_character_data()
    memory_server_reloaded = await notify_memory_server_reload(
        reason=f"云存档下载角色: {character_name}",
    )
    if not memory_server_reloaded:
        return False, "memory_server reload failed"

    session_manager = get_session_manager()
    mgr = session_manager.get(character_name)
    if mgr is not None and getattr(mgr, "websocket", None):
        await send_reload_page_notice(mgr, "云存档角色已更新，页面即将刷新")
    return True, ""


@router.get("/summary")
async def get_cloudsave_summary():
    config_manager = get_config_manager()
    summary = build_cloudsave_summary(config_manager)
    return await _enrich_cloudsave_payload_with_workshop_status(summary)


@router.get("/character/{name}")
async def get_cloudsave_character_detail(name: str):
    config_manager = get_config_manager()
    detail = build_cloudsave_character_detail(config_manager, name)
    if detail is None:
        return _cloudsave_error_response(
            "CLOUDSAVE_CHARACTER_NOT_FOUND",
            f"cloudsave character not found: {name}",
            status_code=404,
            character_name=name,
        )
    return await _enrich_cloudsave_payload_with_workshop_status(detail)


@router.post("/character/{name}/upload")
async def post_cloudsave_character_upload(name: str, request: Request):
    config_manager = get_config_manager()
    if not is_cloudsave_provider_available(config_manager):
        return _cloudsave_error_response(
            "CLOUDSAVE_PROVIDER_UNAVAILABLE",
            "云存档提供方当前不可用，已阻止上传操作",
            status_code=503,
            character_name=name,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    overwrite = bool((body or {}).get("overwrite", False))

    try:
        result = export_cloudsave_character_unit(config_manager, name, overwrite=overwrite)
    except CloudsaveOperationError as exc:
        return _cloudsave_error_response(
            exc.code,
            str(exc),
            status_code=_operation_error_status_code(exc, action="upload"),
            character_name=name,
        )
    except Exception as exc:
        return _cloudsave_error_response(
            "CLOUDSAVE_UPLOAD_FAILED",
            str(exc),
            status_code=500,
            character_name=name,
        )

    return {
        "success": True,
        "character_name": name,
        "detail": await _enrich_cloudsave_payload_with_workshop_status(result.get("detail")),
        "meta": result.get("meta"),
        "sequence_number": result.get("sequence_number"),
    }


@router.post("/character/{name}/download")
async def post_cloudsave_character_download(name: str, request: Request):
    config_manager = get_config_manager()
    if not is_cloudsave_provider_available(config_manager):
        return _cloudsave_error_response(
            "CLOUDSAVE_PROVIDER_UNAVAILABLE",
            "云存档提供方当前不可用，已阻止下载操作",
            status_code=503,
            character_name=name,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    overwrite = bool((body or {}).get("overwrite", False))
    backup_before_overwrite = bool((body or {}).get("backup_before_overwrite", True))

    block_reason = _active_session_block_reason(name)
    if block_reason:
        return _cloudsave_error_response(
            "ACTIVE_SESSION_BLOCKED",
            block_reason,
            status_code=409,
            character_name=name,
        )

    local_exists = _local_character_exists(config_manager, name)
    if local_exists and not overwrite:
        cloud_detail = build_cloudsave_character_detail(config_manager, name)
        if cloud_detail is None:
            return _cloudsave_error_response(
                "CLOUD_CHARACTER_NOT_FOUND",
                f"cloud character not found: {name}",
                status_code=404,
                character_name=name,
            )
        return _cloudsave_error_response(
            "LOCAL_CHARACTER_EXISTS",
            f"local character already exists: {name}",
            status_code=409,
            character_name=name,
        )

    if local_exists and overwrite:
        released_memory_handle = await release_memory_server_character(
            name,
            reason=f"云存档下载前释放 SQLite 句柄: {name}",
        )
        if not released_memory_handle:
            return _cloudsave_error_response(
                "MEMORY_SERVER_RELEASE_FAILED",
                "释放本地角色记忆句柄失败，已阻止下载覆盖，请稍后重试",
                status_code=503,
                character_name=name,
            )

    try:
        result = import_cloudsave_character_unit(
            config_manager,
            name,
            overwrite=overwrite,
            backup_before_overwrite=backup_before_overwrite,
        )
    except CloudsaveOperationError as exc:
        return _cloudsave_error_response(
            exc.code,
            str(exc),
            status_code=_operation_error_status_code(exc, action="download"),
            character_name=name,
        )
    except Exception as exc:
        return _cloudsave_error_response(
            "CLOUDSAVE_DOWNLOAD_FAILED",
            str(exc),
            status_code=500,
            character_name=name,
        )

    backup_path = str(result.get("backup_path") or "")
    try:
        reload_ok, reload_error = await _reload_after_character_download(name)
        if not reload_ok:
            raise RuntimeError(reload_error or "reload failed")
    except Exception as exc:
        rollback_attempted = False
        rollback_error = ""
        rollback_notify_ok = False
        try:
            if backup_path:
                rollback_attempted = True
                restore_cloudsave_operation_backup(config_manager, backup_path)
                initialize_character_data = get_initialize_character_data()
                await initialize_character_data()
                rollback_notify_ok = await notify_memory_server_reload(reason=f"云存档下载回滚: {name}")
                if not rollback_notify_ok:
                    rollback_error = "notify_memory_server_reload returned False"
        except Exception as rollback_exc:
            rollback_error = str(rollback_exc)
        return _cloudsave_error_response(
            "LOCAL_RELOAD_FAILED_ROLLED_BACK",
            f"下载已应用，但本地重载失败: {exc}",
            status_code=500,
            character_name=name,
            extra={
                "rolled_back": rollback_attempted and rollback_error == "" and rollback_notify_ok,
                "rollback_error": rollback_error,
            },
        )

    refreshed_detail = build_cloudsave_character_detail(config_manager, name) or result.get("detail")
    return {
        "success": True,
        "character_name": name,
        "detail": await _enrich_cloudsave_payload_with_workshop_status(refreshed_detail),
        "backup_path": backup_path,
    }

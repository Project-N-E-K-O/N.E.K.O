# -*- coding: utf-8 -*-
"""
Cloudsave Router

Provides cloudsave summary, single-character upload/download APIs,
and safety checks around runtime reload.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager, get_initialize_character_data, get_role_state, get_session_manager, get_steamworks
from .characters_router import (
    notify_memory_server_reload,
    release_memory_server_character,
    send_reload_page_notice,
)
from .workshop_router import get_subscribed_workshop_items, get_workshop_item_details
from utils.cloudsave_autocloud import (
    STEAM_AUTO_CLOUD_SYNC_BACKEND,
    build_steam_autocloud_status,
)
from utils.cloudsave_runtime import (
    CloudsaveOperationError,
    MaintenanceModeError,
    build_cloudsave_character_detail,
    build_cloudsave_summary,
    export_cloudsave_character_unit,
    import_cloudsave_character_unit,
    is_cloudsave_provider_available,
    restore_cloudsave_operation_backup,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cloudsave", tags=["cloudsave"])


CLOUDSAVE_ERROR_I18N_KEYS = {
    "CLOUDSAVE_PROVIDER_UNAVAILABLE": "cloudsave.error.providerUnavailable",
    "ACTIVE_SESSION_BLOCKED": "cloudsave.error.activeSessionBlocked",
    "SESSION_TERMINATE_FAILED": "cloudsave.error.sessionTerminateFailed",
    "MEMORY_SERVER_RELEASE_FAILED": "cloudsave.error.memoryServerReleaseFailed",
    "LOCAL_CHARACTER_NOT_FOUND": "cloudsave.error.localCharacterNotFound",
    "CLOUD_CHARACTER_NOT_FOUND": "cloudsave.error.cloudCharacterNotFound",
    "CLOUDSAVE_CHARACTER_NOT_FOUND": "cloudsave.error.cloudCharacterNotFound",
    "LOCAL_CHARACTER_EXISTS": "cloudsave.error.localCharacterExists",
    "CLOUD_CHARACTER_EXISTS": "cloudsave.error.cloudCharacterExists",
    "CLOUDSAVE_WRITE_FENCE_ACTIVE": "cloudsave.error.writeFenceActive",
    "NAME_AUDIT_FAILED": "cloudsave.error.nameAuditFailed",
    "CLOUDSAVE_UPLOAD_FAILED": "cloudsave.error.uploadFailed",
    "CLOUDSAVE_DOWNLOAD_FAILED": "cloudsave.error.downloadFailed",
    "LOCAL_RELOAD_FAILED_ROLLED_BACK": "cloudsave.error.localReloadFailedRolledBack",
    "INVALID_JSON_BODY": "cloudsave.error.invalidJsonBody",
}


def _build_steam_autocloud_payload(config_manager) -> dict:
    return build_steam_autocloud_status(
        config_manager,
        steamworks=get_steamworks(),
    )


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

    missing_item_ids: list[str] = []
    for item_id in item_ids:
        if item_id in subscribed_lookup:
            status_map[item_id] = _derive_workshop_status_payload(item_id, subscribed_lookup[item_id])
        else:
            missing_item_ids.append(item_id)

    if missing_item_ids:
        results = await asyncio.gather(
            *(_fetch_workshop_status_payload(item_id) for item_id in missing_item_ids),
            return_exceptions=True,
        )
        for item_id, result in zip(missing_item_ids, results, strict=True):
            if isinstance(result, Exception):
                status_map[item_id] = _default_workshop_status_payload(item_id, "unknown")
            else:
                status_map[item_id] = result
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
    message_key: str = "",
    message_params: dict | None = None,
    extra: dict | None = None,
):
    payload = {
        "success": False,
        "error": code,
        "code": code,
        "message": message,
        "message_key": message_key or CLOUDSAVE_ERROR_I18N_KEYS.get(code, ""),
        "message_params": message_params or {},
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
    return "This character has an active session. Stop the session before downloading."


async def _force_terminate_session(character_name: str) -> tuple[bool, str]:
    session_manager = get_session_manager()
    mgr = session_manager.get(character_name)
    if mgr is None or not getattr(mgr, "is_active", False):
        return True, ""

    try:
        await mgr.disconnected_by_server()
        role_state = get_role_state()
        rs = role_state.get(character_name)
        if rs is not None:
            rs.session_manager = None
        return True, ""
    except Exception as exc:
        logger.warning("强制终止角色 %s 会话失败: %s", character_name, exc)
        return False, str(exc)


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


def _maintenance_mode_error_response(exc: MaintenanceModeError, *, character_name: str = ""):
    return _cloudsave_error_response(
        getattr(exc, "code", "CLOUDSAVE_WRITE_FENCE_ACTIVE"),
        str(exc),
        status_code=409,
        character_name=character_name,
    )


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
    summary["sync_backend"] = STEAM_AUTO_CLOUD_SYNC_BACKEND
    summary["steam_autocloud"] = _build_steam_autocloud_payload(config_manager)
    return await _enrich_cloudsave_payload_with_workshop_status(summary)


@router.get("/steam-autocloud-config")
async def get_steam_autocloud_config():
    config_manager = get_config_manager()
    return {
        "success": True,
        "sync_backend": STEAM_AUTO_CLOUD_SYNC_BACKEND,
        "steam_autocloud": _build_steam_autocloud_payload(config_manager),
    }


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
    detail["sync_backend"] = STEAM_AUTO_CLOUD_SYNC_BACKEND
    detail["steam_autocloud"] = _build_steam_autocloud_payload(config_manager)
    return await _enrich_cloudsave_payload_with_workshop_status(detail)


@router.post("/character/{name}/upload")
async def post_cloudsave_character_upload(name: str, request: Request):
    config_manager = get_config_manager()
    if not is_cloudsave_provider_available(config_manager):
        return _cloudsave_error_response(
            "CLOUDSAVE_PROVIDER_UNAVAILABLE",
            "Cloud save provider is currently unavailable.",
            status_code=503,
            character_name=name,
        )
    try:
        body = await request.json()
    except Exception:
        return _cloudsave_error_response(
            "INVALID_JSON_BODY",
            "Invalid JSON request body.",
            status_code=400,
            character_name=name,
        )
    overwrite_val = (body or {}).get("overwrite", False)
    if not isinstance(overwrite_val, bool):
        return _cloudsave_error_response(
            "INVALID_PARAMETER",
            "Invalid parameter: overwrite must be boolean.",
            status_code=400,
            character_name=name,
            message_key="cloudsave.error.invalidBooleanParameter",
            message_params={"parameter": "overwrite"},
        )
    overwrite = overwrite_val

    try:
        result = export_cloudsave_character_unit(config_manager, name, overwrite=overwrite)
    except MaintenanceModeError as exc:
        return _maintenance_mode_error_response(exc, character_name=name)
    except CloudsaveOperationError as exc:
        return _cloudsave_error_response(
            exc.code,
            str(exc),
            status_code=_operation_error_status_code(exc, action="upload"),
            character_name=name,
        )
    except Exception as exc:
        logger.exception("云存档上传失败: %s", name)
        return _cloudsave_error_response(
            "CLOUDSAVE_UPLOAD_FAILED",
            "Upload failed. Please try again later.",
            status_code=500,
            character_name=name,
        )

    return {
        "success": True,
        "character_name": name,
        "detail": await _enrich_cloudsave_payload_with_workshop_status(result.get("detail")),
        "meta": result.get("meta"),
        "sequence_number": result.get("sequence_number"),
        "sync_backend": STEAM_AUTO_CLOUD_SYNC_BACKEND,
        "steam_autocloud": _build_steam_autocloud_payload(config_manager),
    }


@router.post("/character/{name}/download")
async def post_cloudsave_character_download(name: str, request: Request):
    config_manager = get_config_manager()
    if not is_cloudsave_provider_available(config_manager):
        return _cloudsave_error_response(
            "CLOUDSAVE_PROVIDER_UNAVAILABLE",
            "Cloud save provider is currently unavailable.",
            status_code=503,
            character_name=name,
        )
    try:
        body = await request.json()
    except Exception:
        return _cloudsave_error_response(
            "INVALID_JSON_BODY",
            "Invalid JSON request body.",
            status_code=400,
            character_name=name,
        )
    overwrite_val = (body or {}).get("overwrite", False)
    backup_val = (body or {}).get("backup_before_overwrite", True)
    if not isinstance(overwrite_val, bool):
        return _cloudsave_error_response(
            "INVALID_PARAMETER",
            "Invalid parameter: overwrite must be boolean.",
            status_code=400,
            character_name=name,
            message_key="cloudsave.error.invalidBooleanParameter",
            message_params={"parameter": "overwrite"},
        )
    if "backup_before_overwrite" in (body or {}) and not isinstance(backup_val, bool):
        return _cloudsave_error_response(
            "INVALID_PARAMETER",
            "Invalid parameter: backup_before_overwrite must be boolean.",
            status_code=400,
            character_name=name,
            message_key="cloudsave.error.invalidBooleanParameter",
            message_params={"parameter": "backup_before_overwrite"},
        )
    overwrite = overwrite_val
    backup_before_overwrite = backup_val
    force_val = (body or {}).get("force", False)

    block_reason = _active_session_block_reason(name)
    if block_reason:
        if not isinstance(force_val, bool) or not force_val:
            return _cloudsave_error_response(
                "ACTIVE_SESSION_BLOCKED",
                block_reason,
                status_code=409,
                character_name=name,
                extra={"can_force": True},
            )
        terminated_ok, terminate_msg = await _force_terminate_session(name)
        if not terminated_ok:
            return _cloudsave_error_response(
                "SESSION_TERMINATE_FAILED",
                f"Failed to terminate active session: {terminate_msg}",
                status_code=503,
                character_name=name,
                message_params={"message": terminate_msg},
            )
        released_memory_handle = await release_memory_server_character(
            name,
            reason=f"云存档强制下载前释放 SQLite 句柄: {name}",
        )
        if not released_memory_handle:
            return _cloudsave_error_response(
                "MEMORY_SERVER_RELEASE_FAILED",
                "Failed to release the local memory handle before overwrite. Please try again later.",
                status_code=503,
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

    if local_exists and overwrite and not force_val:
        released_memory_handle = await release_memory_server_character(
            name,
            reason=f"云存档下载前释放 SQLite 句柄: {name}",
        )
        if not released_memory_handle:
            return _cloudsave_error_response(
                "MEMORY_SERVER_RELEASE_FAILED",
                "Failed to release the local memory handle before overwrite. Please try again later.",
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
    except MaintenanceModeError as exc:
        return _maintenance_mode_error_response(exc, character_name=name)
    except CloudsaveOperationError as exc:
        return _cloudsave_error_response(
            exc.code,
            str(exc),
            status_code=_operation_error_status_code(exc, action="download"),
            character_name=name,
        )
    except Exception as exc:
        logger.exception("云存档下载失败: %s", name)
        return _cloudsave_error_response(
            "CLOUDSAVE_DOWNLOAD_FAILED",
            "Download failed. Please try again later.",
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
            f"The download was applied, but local reload failed: {exc}",
            status_code=500,
            character_name=name,
            message_params={"message": str(exc)},
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
        "sync_backend": STEAM_AUTO_CLOUD_SYNC_BACKEND,
        "steam_autocloud": _build_steam_autocloud_payload(config_manager),
    }

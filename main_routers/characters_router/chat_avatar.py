"""UID-scoped chat display avatars. Model portraits and card faces stay separate."""

import asyncio
import json
from pathlib import Path

from fastapi import Request
from starlette.datastructures import UploadFile

from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from utils.asyncio_retirement import await_retirement
from utils.character_memory import character_config_mutation_lock
from utils.chat_avatar_connections import notify_chat_avatar_changed
from utils.chat_avatar_store import (
    ChatAvatarError, NORMALIZED_MAX_BYTES, limits, normalize_png,
    read_record, validate_operation, validate_uid, write_record,
)
from utils.cloudsave_runtime import (
    MaintenanceModeError, cloudsave_writable_transaction, maintenance_error_payload,
)
from utils.config_manager.reserved_schema import get_character_uid
from utils.config_manager.storage_roots import chat_avatar_directory

from ._shared import _json_no_store_response, logger, router

_MAX_MULTIPART_BYTES = NORMALIZED_MAX_BYTES + 64 * 1024


def _error_response(exc):
    if isinstance(exc, MaintenanceModeError):
        return _json_no_store_response(maintenance_error_payload(exc), status_code=409)
    return _json_no_store_response({"success": False, "code": exc.code, "error": exc.code},
                                   status_code=exc.status)


def _response(record):
    return _json_no_store_response({**record, "limits": limits()})


def _assert_character(config_manager, uid):
    """Return every live character UID once ``uid`` is confirmed among them."""
    try:
        characters = config_manager.load_characters(require_authoritative=True)
    except MaintenanceModeError:
        raise
    except (OSError, ValueError) as exc:
        raise ChatAvatarError("chat_avatar_character_read_failed", 503) from exc
    live_uids = {get_character_uid(value) for value in characters.get("猫娘", {}).values()}
    if uid not in live_uids:
        raise ChatAvatarError("chat_avatar_character_not_found", 404)
    return live_uids


def _check_root(config_manager, expected_root):
    if Path(config_manager.app_docs_dir) != expected_root:
        raise ChatAvatarError("chat_avatar_storage_changed", 409)


def _read(config_manager, uid, expected_root):
    _check_root(config_manager, expected_root)
    _assert_character(config_manager, uid)
    record = read_record(chat_avatar_directory(config_manager), uid)
    _check_root(config_manager, expected_root)
    return record


def _commit(config_manager, uid, expected_root, data_url, base_revision, operation_id):
    try:
        with cloudsave_writable_transaction(config_manager, operation="chat_avatar", target=uid):
            _check_root(config_manager, expected_root)
            live_uids = _assert_character(config_manager, uid)
            return write_record(chat_avatar_directory(config_manager), uid, data_url, base_revision,
                                operation_id, live_uids=live_uids)
    except OSError as exc:
        raise ChatAvatarError("chat_avatar_write_failed", 503) from exc


async def _bounded_body(request, max_bytes):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise ChatAvatarError("chat_avatar_too_large", 413)
        body.extend(chunk)
    return bytes(body)


async def _parse_upload(request):
    body = await _bounded_body(request, _MAX_MULTIPART_BYTES)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    replay = Request(request.scope, receive)
    try:
        async with replay.form(max_files=1, max_fields=2, max_part_size=4096) as form:
            if sorted(form.keys()) != ["base_revision", "image", "operation_id"] or len(form.multi_items()) != 3:
                raise ChatAvatarError("chat_avatar_invalid_request", 400)
            image = form["image"]
            if not isinstance(image, UploadFile):
                raise ChatAvatarError("chat_avatar_invalid_request", 400)
            base_revision, operation_id = validate_operation(form["base_revision"], form["operation_id"])
            payload = await image.read(NORMALIZED_MAX_BYTES + 1)
    except ChatAvatarError:
        raise
    except Exception as exc:
        raise ChatAvatarError("chat_avatar_invalid_request", 400) from exc
    return await asyncio.to_thread(normalize_png, payload), base_revision, operation_id


async def _save(config_manager, uid, expected_root, data_url, base_revision, operation_id):
    async with character_config_mutation_lock:
        # Caller cancellation must not release either fence while its worker writes.
        record = await await_retirement(asyncio.to_thread(
            _commit, config_manager, uid, expected_root, data_url, base_revision, operation_id,
        ))
    try:
        await notify_chat_avatar_changed(uid, record["revision"])
    except Exception as exc:
        logger.warning("Chat avatar persisted; invalidation notification failed: %s", type(exc).__name__)
    return _response(record)


@router.get("/by-uid/{uid}/chat-avatar")
async def get_chat_avatar(uid: str):
    try:
        validate_uid(uid)
        config_manager = get_config_manager()
        expected_root = Path(config_manager.app_docs_dir)
        async with character_config_mutation_lock:
            record = await await_retirement(asyncio.to_thread(_read, config_manager, uid, expected_root))
        return _response(record)
    except (ChatAvatarError, MaintenanceModeError) as exc:
        return _error_response(exc)


@router.put("/by-uid/{uid}/chat-avatar")
async def put_chat_avatar(uid: str, request: Request):
    security_error = _validate_local_mutation_request(request)
    if security_error is not None:
        return security_error
    try:
        validate_uid(uid)
        config_manager = get_config_manager()
        expected_root = Path(config_manager.app_docs_dir)
        data_url, base_revision, operation_id = await _parse_upload(request)
        return await _save(config_manager, uid, expected_root, data_url, base_revision, operation_id)
    except (ChatAvatarError, MaintenanceModeError) as exc:
        return _error_response(exc)


@router.delete("/by-uid/{uid}/chat-avatar")
async def delete_chat_avatar(uid: str, request: Request):
    security_error = _validate_local_mutation_request(request)
    if security_error is not None:
        return security_error
    try:
        validate_uid(uid)
        config_manager = get_config_manager()
        expected_root = Path(config_manager.app_docs_dir)
        try:
            data = json.loads(await _bounded_body(request, 4096))
        except (ValueError, RecursionError) as exc:
            raise ChatAvatarError("chat_avatar_invalid_request", 400) from exc
        if not isinstance(data, dict) or set(data) != {"base_revision", "operation_id"}:
            raise ChatAvatarError("chat_avatar_invalid_request", 400)
        base_revision, operation_id = validate_operation(data["base_revision"], data["operation_id"])
        return await _save(config_manager, uid, expected_root, None, base_revision, operation_id)
    except (ChatAvatarError, MaintenanceModeError) as exc:
        return _error_response(exc)

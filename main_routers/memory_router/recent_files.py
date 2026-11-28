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
"""Recent conversation file browsing and editing endpoints for memory_router."""

import asyncio
import hashlib
import json
from contextlib import suppress
from pathlib import Path
from fastapi import Request
from utils.cloudsave_runtime import MaintenanceModeError
from utils.recent_file import (
    RecentFileDeletedError,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_access,
    set_recent_pending_unlocked,
    write_recent_payload_unlocked,
)
from fastapi.responses import JSONResponse

from main_routers import memory_router as _mr
from ._shared import (
    PATH_ERROR_NOT_FOUND,
    _RECENT_BROWSER_ROLE_TYPES,
    logger,
    router,
)
from .paths import (
    extract_catgirl_name_from_recent_filename,
    iter_recent_memory_files,
    path_error_status_code,
    resolve_recent_file_path,
    validate_catgirl_name,
    validate_chat_payload,
    validate_recent_filename,
)

async def _await_browser_save_transaction(coro):
    """Finish a committed browser save before propagating request cancellation."""
    operation = asyncio.create_task(coro)
    try:
        return await asyncio.shield(operation), False
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        try:
            result = operation.result()
        except BaseException as exc:
            raise asyncio.CancelledError from exc
        return result, True


def _recent_browser_fingerprint(content: str) -> str:
    """Return the optimistic-concurrency token for one browser snapshot."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _recent_browser_identity_token(path: Path) -> str:
    """Return an opaque token binding an editor snapshot to one path identity."""
    key, generation = _mr.capture_recent_generation(path)
    material = f"{key}\0{generation}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _read_recent_browser_text_unlocked(path: Path) -> str:
    """Build one editable disk-plus-pending snapshot while its lock is held."""
    content = read_recent_text_unlocked(path)
    pending = get_recent_pending_unlocked(path)
    if not pending:
        return content
    payload = json.loads(content)
    if not isinstance(payload, list):
        raise ValueError(f"recent history is not a list: {path}")
    if all(isinstance(message, dict) for message in pending):
        pending_payload = list(pending)
    else:
        from utils.llm_client import messages_to_dict

        pending_payload = messages_to_dict(pending)
    return json.dumps(payload + pending_payload, ensure_ascii=False, indent=2)


def _read_recent_browser_text(path: Path) -> str:
    """Read one editable disk-plus-pending snapshot under the recent lock."""
    with recent_file_access(path) as resolved_path:
        return _read_recent_browser_text_unlocked(resolved_path)


def _read_recent_browser_snapshot(path: Path) -> tuple[str, str]:
    """Read editable content and its opaque identity while holding one lock."""
    with recent_file_access(path) as resolved_path:
        return (
            _read_recent_browser_text_unlocked(resolved_path),
            _recent_browser_identity_token(Path(resolved_path)),
        )


class _RecentBrowserTheaterEditRejected(Exception):
    """A browser save may carry an edited theater capsule as ordinary text."""


def _recent_payload_type_and_content(item: object) -> tuple[object, object]:
    """Return the ``(type, content)`` pair of one on-disk or role-style message."""
    if not isinstance(item, dict):
        return None, None
    data = item.get("data")
    if isinstance(data, dict):
        return item.get("type"), data.get("content")
    return (
        item.get("type") or _RECENT_BROWSER_ROLE_TYPES.get(item.get("role")),
        item.get("content"),
    )


def _merge_browser_payload_with_theater(
    current_text: str,
    payload: list[dict],
    browser_chat: list | None,
) -> list[dict]:
    """Carry theater messages over verbatim when the browser rewrites recent history.

    The browser posts only ``{role, text}`` (plus an optional ``source_index``
    pointing into the snapshot it loaded), which would strip the theater
    metadata and turn a capsule into ordinary memory. Every theater message of
    the locked snapshot is therefore copied back untouched at its original
    position relative to the surviving ordinary messages, and posted items that
    stand for a theater message are dropped. Without theater messages the
    payload is returned unchanged.
    """
    from memory.message_sources import is_theater_memory_message

    try:
        current = json.loads(current_text)
    except (TypeError, ValueError):
        return payload
    if not isinstance(current, list):
        return payload
    theater_indexes = [
        index
        for index, message in enumerate(current)
        if is_theater_memory_message(message)
    ]
    if not theater_indexes:
        return payload
    theater_index_set = set(theater_indexes)
    chat = browser_chat if isinstance(browser_chat, list) else []

    merged: list[dict] = []
    next_theater = 0
    cursor = 0
    has_unresolved_item = False
    matched_theater: set[int] = set()
    for position, item in enumerate(payload):
        chat_item = chat[position] if position < len(chat) else {}
        if not isinstance(chat_item, dict):
            chat_item = {}
        source = chat_item.get("source_index")
        if not (
            isinstance(source, int)
            and not isinstance(source, bool)
            and cursor <= source < len(current)
        ):
            # Legacy clients return only {role, text}: align forward by type and original text; edited entries will not match.
            source = None
            item_key = _recent_payload_type_and_content(item)
            for index in range(cursor, len(current)):
                if _recent_payload_type_and_content(current[index]) == item_key:
                    source = index
                    break
        if source is None:
            if chat_item.get("theater"):
                # Browser-labeled theater entries must never be saved as ordinary text; restore originals from snapshots.
                continue
            has_unresolved_item = True
            merged.append(item)
            continue
        cursor = source + 1
        while (
            next_theater < len(theater_indexes)
            and theater_indexes[next_theater] <= source
        ):
            merged.append(current[theater_indexes[next_theater]])
            next_theater += 1
        if source in theater_index_set:
            matched_theater.add(source)
            continue
        if chat_item.get("theater"):
            continue
        merged.append(item)
    merged.extend(current[index] for index in theater_indexes[next_theater:])
    if has_unresolved_item and len(matched_theater) < len(theater_indexes):
        # Unmatched theater capsules alongside entries differing from original text may be capsules edited by old pages;
        # writing them back turns theater fiction into everyday memory. Reject and require reload instead.
        raise _RecentBrowserTheaterEditRejected()
    return merged


def _write_recent_browser_payload(
    path: Path,
    payload: list[dict],
    *,
    expected_fingerprint: str | None,
    expected_identity_token: str | None,
    expected_generation: tuple[str, int],
    browser_chat: list | None = None,
) -> tuple[bool, str, str]:
    """Replace a browser snapshot unless disk or pending state changed since read."""
    with recent_file_access(
        path, expected_generation=expected_generation,
    ) as resolved_path:
        current_identity_token = _recent_browser_identity_token(Path(resolved_path))
        current_text = _read_recent_browser_text_unlocked(resolved_path)
        current_fingerprint = _recent_browser_fingerprint(current_text)
        if (
            expected_identity_token is not None
            and expected_identity_token != current_identity_token
        ) or (
            expected_fingerprint is not None
            and expected_fingerprint != current_fingerprint
        ):
            return False, current_fingerprint, current_identity_token
        # Retain projection identity through edits without accepting IDs supplied by the browser.
        from memory.recent_sync import ID_KEY, TIME_KEY
        from utils.llm_client import message_metadata
        try:
            current_rows = json.loads(current_text)
        except (TypeError, ValueError):
            # A damaged file stays repairable by saving over it; it has no IDs to keep.
            current_rows = []
        if not isinstance(current_rows, list):
            current_rows = []
        used_sources = set()
        for item, edit in zip(payload, browser_chat or []):
            source = edit.get("source_index") if isinstance(edit, dict) else None
            if (type(source) is int and 0 <= source < len(current_rows)
                    and source not in used_sources and isinstance(current_rows[source], dict)):
                key = message_metadata(current_rows[source]).get(ID_KEY)
                source_type = current_rows[source].get('type') or _RECENT_BROWSER_ROLE_TYPES.get(current_rows[source].get('role'))
                if key and item.get("type") == source_type:
                    used_sources.add(source)
                    item["data"].setdefault("metadata", {})[ID_KEY] = key
                    item["data"]["metadata"][TIME_KEY] = message_metadata(current_rows[source]).get(TIME_KEY)
        # Maintain theater capsules only through /cache and /theater/forget; browser saves preserve them unchanged.
        payload = _merge_browser_payload_with_theater(
            current_text, payload, browser_chat,
        )
        write_recent_payload_unlocked(resolved_path, payload)
        set_recent_pending_unlocked(resolved_path, [])
        saved_text = json.dumps(payload, ensure_ascii=False, indent=2)
        return (
            True,
            _recent_browser_fingerprint(saved_text),
            current_identity_token,
        )


def _read_recent_browser_conflict_tokens(
    path: Path,
) -> tuple[str | None, str | None]:
    """Best-effort current tokens for a generation-race conflict response."""
    try:
        content, identity_token = _mr._read_recent_browser_snapshot(path)
    except Exception:
        return None, None
    return _recent_browser_fingerprint(content), identity_token


def _recent_browser_conflict_response(
    fingerprint: str | None,
    identity_token: str | None,
) -> JSONResponse:
    """Return the browser editor's uniform optimistic-concurrency conflict."""
    return JSONResponse(
        {
            "success": False,
            "code": "RECENT_FILE_CONFLICT",
            "error": "近期记忆已在其他任务中更新，请重新加载并合并后再保存",
            "fingerprint": fingerprint,
            "identity_token": identity_token,
        },
        status_code=409,
    )


@router.get('/recent_files')
async def get_recent_files():
    """List all recent*.json filenames under the memory directory."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    file_names: list[str] = []
    seen: set[str] = set()

    for base_dir in (Path(cm.memory_dir), Path(cm.project_memory_dir)):
        for logical_name in iter_recent_memory_files(base_dir):
            if logical_name in seen:
                continue
            seen.add(logical_name)
            file_names.append(logical_name)

    return {"files": sorted(file_names)}


@router.get('/recent_file')
async def get_recent_file(filename: str):
    """Get the content of the specified recent*.json file."""
    # Reject path traversal attempts
    if '/' in filename or '\\' in filename or '..' in filename:
        return JSONResponse({"success": False, "error": "文件名不能包含路径分隔符或目录遍历字符"}, status_code=400)
    
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()

    resolved_path, path_error, path_error_code, _catgirl_name = resolve_recent_file_path(cm, filename)
    if resolved_path is None:
        status_code = path_error_status_code(path_error_code)
        return JSONResponse({"success": False, "error": path_error}, status_code=status_code)
    
    # Offload synchronous reads: recent.json can reach several megabytes.
    # Hold the file lock: on Windows even a bare open() can make concurrent os.replace raise PermissionError.
    try:
        content, identity_token = await asyncio.to_thread(
            _mr._read_recent_browser_snapshot, resolved_path,
        )
    except RecentFileDeletedError:
        return JSONResponse(
            {"success": False, "error": "文件不存在"},
            status_code=path_error_status_code(PATH_ERROR_NOT_FOUND),
        )
    return {
        "content": content,
        "fingerprint": _recent_browser_fingerprint(content),
        "identity_token": identity_token,
    }


@router.post('/recent_file/save')
async def save_recent_file(request: Request):
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    snapshot_fingerprint = data.get('fingerprint')
    snapshot_identity_token = data.get('identity_token')
    
    # Validate filename
    is_valid, error_msg = validate_recent_filename(filename)
    if not is_valid:
        logger.warning(f"Invalid filename rejected: {filename!r} - {error_msg}")
        return JSONResponse({"success": False, "error": error_msg}, status_code=400)
    
    # Validate chat payload
    is_valid, error_msg = validate_chat_payload(chat)
    if not is_valid:
        logger.warning(f"Invalid chat payload rejected: {error_msg}")
        return JSONResponse({"success": False, "error": error_msg}, status_code=400)
    if snapshot_fingerprint is not None and not isinstance(snapshot_fingerprint, str):
        return JSONResponse(
            {"success": False, "error": "文件快照指纹格式不合法"},
            status_code=400,
        )
    if snapshot_identity_token is not None and not isinstance(snapshot_identity_token, str):
        return JSONResponse(
            {"success": False, "error": "文件身份令牌格式不合法"},
            status_code=400,
        )
    if snapshot_fingerprint is None or snapshot_identity_token is None:
        return JSONResponse(
            {"success": False, "error": "文件身份令牌缺失，请重新加载后再保存"},
            status_code=409,
        )
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    catgirl_name = extract_catgirl_name_from_recent_filename(filename)
    if catgirl_name is None:
        logger.warning(f"Failed to extract neko name from filename: {filename!r}")
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)

    # Save into the same layout reads resolve; never silently move legacy flat/project files into nonexistent
    # runtime nested paths, or CAS comparisons lose their target.
    resolved_path, _path_error, path_error_code, _ = resolve_recent_file_path(
        cm, filename,
    )
    if resolved_path is None:
        if path_error_code != PATH_ERROR_NOT_FOUND:
            return JSONResponse(
                {"success": False, "error": _path_error},
                status_code=path_error_status_code(path_error_code),
            )
        resolved_path = Path(cm.memory_dir) / catgirl_name / 'recent.json'
    admission_generation = _mr.capture_recent_generation(resolved_path)
    _mr.assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{catgirl_name}/recent.json",
    )

    arr = []
    for msg in chat:
        t = msg.get('role')
        text = msg.get('text', '')
        arr.append({
            "type": t,
            "data": {
                "content": text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": t,
                "name": None,
                "id": None,
                "example": False,
                **({"tool_calls": [], "invalid_tool_calls": [], "usage_metadata": None} if t == "ai" else {})
            }
        })
    async def _commit_browser_save():
        try:
            saved, saved_fingerprint, saved_identity_token = await asyncio.to_thread(
                _mr._write_recent_browser_payload,
                resolved_path,
                arr,
                expected_fingerprint=snapshot_fingerprint,
                expected_identity_token=snapshot_identity_token,
                expected_generation=admission_generation,
                browser_chat=chat,
            )
        except _RecentBrowserTheaterEditRejected:
            return JSONResponse(
                {
                    "success": False,
                    "code": "RECENT_FILE_THEATER_READONLY",
                    "error": "小剧场记忆不能在记忆浏览器中编辑，请重新加载后再保存",
                },
                status_code=409,
            )
        except RecentFileDeletedError:
            saved_fingerprint, saved_identity_token = await asyncio.to_thread(
                _read_recent_browser_conflict_tokens,
                resolved_path,
            )
            return _recent_browser_conflict_response(
                saved_fingerprint,
                saved_identity_token,
            )
        if not saved:
            return _recent_browser_conflict_response(
                saved_fingerprint,
                saved_identity_token,
            )
        
        if catgirl_name:
            # Interrupt memory_server review tasks.
            import httpx
            from config import MEMORY_SERVER_PORT
            # Per-call AsyncClient: cold path triggered by manually saving recent conversations.
            try:
                from utils.internal_http_client import internal_service_event_hooks

                async with httpx.AsyncClient(
                    proxy=None,
                    trust_env=False,
                    event_hooks=internal_service_event_hooks(),
                ) as client:
                    await client.post(
                        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cancel_correction/{catgirl_name}",
                        timeout=2.0
                    )
                    logger.info(f"已发送取消 {catgirl_name} 记忆整理任务的请求")
            except Exception as e:
                logger.warning(f"Failed to cancel correction task: {e}")
        
        # Return success with a context-refresh hint.
        return {
            "success": True,
            "need_refresh": True,
            "catgirl_name": catgirl_name,
            "fingerprint": saved_fingerprint,
            "identity_token": saved_identity_token,
        }

    try:
        result, save_cancelled = await _await_browser_save_transaction(
            _commit_browser_save()
        )
        if save_cancelled:
            raise asyncio.CancelledError
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"Failed to save recent file: {e}")
        return {"success": False, "error": str(e)}


@router.post('/update_catgirl_name')
async def update_catgirl_name(request: Request):
    """
    Update the neko name in memory files.
    1. Rename the memory files
    2. Update name references inside the file contents
    """
    data = await request.json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not old_name or not new_name:
        return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
    
    # Validate old_name (allow dots for historical names during migration)
    is_valid, error_msg = validate_catgirl_name(old_name, allow_dots=True, reject_reserved_route=False)
    if not is_valid:
        logger.warning(f"Invalid old_name rejected: {old_name!r} - {error_msg}")
        return JSONResponse({"success": False, "error": f"旧名称无效: {error_msg}"}, status_code=400)

    # Validate new_name (strict — no dots allowed)
    is_valid, error_msg = validate_catgirl_name(new_name, reject_reserved_route=True)
    if not is_valid:
        logger.warning(f"Invalid new_name rejected: {new_name!r} - {error_msg}")
        return JSONResponse({"success": False, "error": f"新名称无效: {error_msg}"}, status_code=400)
    
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        characters = await cm.aload_characters()
        catgirls = characters.get('猫娘', {}) if isinstance(characters, dict) else {}

        # Idempotent compatibility for old clients calling this endpoint after canonical rename succeeds.
        if old_name not in catgirls and new_name in catgirls:
            if _mr.character_memory_exists(cm, old_name):
                return JSONResponse(
                    {
                        "success": False,
                        "error": "角色配置已改名但旧记忆仍存在，请通过角色管理接口修复",
                    },
                    status_code=409,
                )
            return {
                "success": True,
                "changed": False,
                "exists_after": _mr.character_memory_exists(cm, new_name),
                "already_renamed": True,
            }

        # Moving memory separately bypasses rename-transaction task draining, configuration publication, and rollback.
        # Delegate to the canonical route so old derived tasks cannot follow recent redirects into a new character.
        from ..characters_router.crud import rename_catgirl

        return await rename_catgirl(old_name, request)
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.exception("更新猫娘名称失败")
        return {"success": False, "error": str(exc)}

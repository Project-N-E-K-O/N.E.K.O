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

"""Character CRUD and lifecycle: list/add/update/rename/delete,
current-catgirl switching, master rename, rollback/snapshot helpers and
reload notifications.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import (
    CHARACTER_RESERVED_FIELD_SET,
    _json_no_store_response,
    _profile_name_contains_path_separator,
    _validate_existing_character_path_name,
    _validate_profile_name,
    logger,
    router,
)
from .notify import notify_memory_server_reload, release_memory_server_character, send_reload_page_notice
from .voice_registry import _is_current_catgirl_voice_session_starting, _voice_session_starting_response

import json
import shutil
import asyncio
import copy
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import (
    get_config_manager,
    get_session_manager,
    get_initialize_character_data,
    get_switch_current_catgirl_fast,
    get_init_one_catgirl,
    get_remove_one_catgirl,
)
from ..workshop_router import _ugc_sync_lock
from ..agent_router import force_disable_agent_for_character_switch
from utils.character_memory import (
    delete_character_memory_storage,
    list_character_memory_paths,
    rename_character_memory_storage,
)
from utils.config_manager import (
    flatten_reserved,
    get_reserved,
    set_reserved,
)
from utils.voice_config import read_legacy_voice_id
from utils.file_utils import atomic_write_json_async
from utils.language_utils import normalize_language_code
from utils.new_character_greeting_state import (
    mark_pending as mark_new_character_greeting_pending,
    remove_pending as remove_new_character_greeting_pending,
    rename_pending as rename_new_character_greeting_pending,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable, is_cloudsave_disabled


DEFAULT_NEW_CATGIRL_FREE_VOICE_ID = "voice-tone-PGLiyZt65w"


def _get_new_catgirl_default_voice_id() -> str:
    """Get the default voice for a newly created character, tolerating legacy/custom configs missing free_voices."""
    from utils.api_config_loader import get_free_voices

    free_voices = get_free_voices() or {}
    return (
        free_voices.get('cuteGirl')
        or next((voice_id for voice_id in free_voices.values() if voice_id), '')
        or DEFAULT_NEW_CATGIRL_FREE_VOICE_ID
    )


async def _mark_new_character_greeting_pending_safe(config_manager, character_name: str, source: str) -> tuple[bool, str]:
    try:
        await mark_new_character_greeting_pending(config_manager, character_name, source=source)
        return True, ""
    except Exception as exc:
        logger.exception("mark new character greeting pending failed: %s", character_name)
        return False, str(exc)


def _build_profile_rename_event(old_name: str, new_name: str) -> dict:
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    return {
        "type": "profile_rename",
        "old_name": old_name,
        "new_name": new_name,
        "renamed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _append_profile_rename_event(character_payload: dict, old_name: str, new_name: str) -> None:
    """Write the rename event into the hidden AI context; the character manager page does not render `_reserved` as a regular field."""
    if not isinstance(character_payload, dict):
        return

    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if old_name == new_name:
        return

    existing = get_reserved(
        character_payload,
        "ai_context",
        "rename_events",
        default=[],
    )
    events = [event for event in existing if isinstance(event, dict)] if isinstance(existing, list) else []
    new_event = _build_profile_rename_event(old_name, new_name)

    # 防止同一次请求重放时连续写入完全相同的改名事件。
    if events:
        last = events[-1]
        if (
            last.get("type") == new_event["type"]
            and str(last.get("old_name") or "") == new_event["old_name"]
            and str(last.get("new_name") or "") == new_event["new_name"]
        ):
            return

    events.append(new_event)
    set_reserved(character_payload, "ai_context", "rename_events", events[-20:])


async def _clear_character_recent_history(config_manager, character_name: str) -> None:
    recent_path = Path(config_manager.memory_dir) / character_name / "recent.json"
    assert_cloudsave_writable(
        config_manager,
        operation="save",
        target=f"memory/{character_name}/recent.json",
    )
    await asyncio.to_thread(recent_path.parent.mkdir, parents=True, exist_ok=True)
    await atomic_write_json_async(recent_path, [], ensure_ascii=False, indent=2)


def _normalize_prompt_synced_field_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, list):
        if not value:
            return None
        return '、'.join(str(item) for item in value)
    if isinstance(value, (dict, set, tuple)):
        return None
    return str(value)


def _prompt_synced_catgirl_fields(catgirl_payload: dict) -> dict:
    if not isinstance(catgirl_payload, dict):
        return {}
    result = {}
    for key, value in catgirl_payload.items():
        if key in CHARACTER_RESERVED_FIELD_SET:
            continue
        normalized = _normalize_prompt_synced_field_value(value)
        if normalized is not None:
            result[key] = normalized
    return result


def _catgirl_prompt_fields_changed(previous_payload: dict, current_payload: dict) -> bool:
    return _prompt_synced_catgirl_fields(previous_payload) != _prompt_synced_catgirl_fields(current_payload)


async def _refresh_catgirl_context_after_profile_change(
    config_manager,
    name: str,
    characters: dict,
    *,
    is_new: bool = False,
    reload_message: str = "角色设定已更新，页面即将刷新",
) -> dict:
    result = {
        "context_refreshed": True,
        "recent_history_cleared": False,
        "reload_notified": False,
        "session_restarted": False,
    }

    try:
        await _clear_character_recent_history(config_manager, name)
        result["recent_history_cleared"] = True
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.warning("清理角色近期上下文失败: name=%s err=%s", name, exc, exc_info=True)
        result.update({
            "success": False,
            "partial_success": True,
            "context_refreshed": False,
            "context_refresh_failed": True,
            "recent_history_clear_failed": True,
            "recent_history_clear_error": str(exc),
            "recent_history_clear_error_type": type(exc).__name__,
            "recent_history_clear_target": f"memory/{name}/recent.json",
            "session_reset_skipped": True,
            "init_skipped": True,
            "error": "角色设定已保存，但近期上下文清理失败，设定未完全刷新",
        })
        return result

    session_manager = get_session_manager()
    is_current_catgirl = name == (characters or {}).get('当前猫娘', '')
    mgr = session_manager.get(name) if is_current_catgirl and session_manager else None
    expected_session = getattr(mgr, "session", None) if mgr and getattr(mgr, "is_active", False) else None

    if expected_session is not None:
        result["reload_notified"] = await send_reload_page_notice(mgr, reload_message)
        try:
            await mgr.end_session(by_server=True, expected_session=expected_session)
            result["session_restarted"] = True
        except Exception as exc:
            logger.error("角色设定更新后结束 session 失败: name=%s err=%s", name, exc)
        reset_circuit = getattr(mgr, "reset_session_start_circuit", None)
        if callable(reset_circuit):
            reset_circuit()

    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(name, is_new=is_new)
    return result


def _filter_mutable_catgirl_fields(data: dict) -> dict:
    """Filter out reserved fields that the generic character edit API must not write."""
    if not isinstance(data, dict):
        logger.warning(
            "_filter_mutable_catgirl_fields expected dict, got %s: %r",
            type(data).__name__,
            data,
        )
        return {}
    return {
        key: value
        for key, value in data.items()
        if key not in CHARACTER_RESERVED_FIELD_SET
    }


def _normalize_catgirl_field_order(order, available_fields: list[str]) -> list[str]:
    """Order regular profile fields by the explicit order, appending omitted fields in their current stored order."""
    available = {str(key) for key in available_fields}
    result: list[str] = []
    seen: set[str] = set()

    if isinstance(order, list):
        for raw_key in order:
            key = str(raw_key or "").strip()
            if not key or key in seen or key not in available:
                continue
            result.append(key)
            seen.add(key)

    for raw_key in available_fields:
        key = str(raw_key or "").strip()
        if key and key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _extract_catgirl_field_order_payload(raw_data: dict) -> list[str] | None:
    """Read the field order submitted by the frontend; returns None when no explicit order is given."""
    if not isinstance(raw_data, dict):
        return None
    raw_order = raw_data.get("_field_order")
    if isinstance(raw_order, list):
        return [str(item or "").strip() for item in raw_order]
    reserved = raw_data.get("_reserved")
    if isinstance(reserved, dict) and isinstance(reserved.get("field_order"), list):
        return [str(item or "").strip() for item in reserved["field_order"]]
    return None


def _sync_catgirl_field_order(catgirl_data: dict, requested_order: list[str] | None = None) -> None:
    """Maintain the creation order of regular profile fields, preventing numeric keys from being reordered first by JS enumeration rules."""
    if not isinstance(catgirl_data, dict):
        return
    available_fields = [
        str(key)
        for key in catgirl_data.keys()
        if key not in CHARACTER_RESERVED_FIELD_SET
    ]
    if requested_order is None:
        # 也认顶层 _field_order：工坊上传卡的顺序存在顶层（上传时 _reserved 被剥离），
        # 只读 _reserved.field_order 会漏掉它而退回 JSON key 枚举顺序（数字 key 被提前）。
        requested_order = _extract_catgirl_field_order_payload(catgirl_data)
    field_order = _normalize_catgirl_field_order(requested_order, available_fields)
    set_reserved(catgirl_data, "field_order", field_order)


def _flatten_catgirl_for_response(catgirl_data: dict) -> dict:
    """Prepend the field order before flattening reserved fields, so the frontend renders in creation order."""
    if not isinstance(catgirl_data, dict):
        return catgirl_data
    data = copy.deepcopy(catgirl_data)
    _sync_catgirl_field_order(data)
    return flatten_reserved(data)


def _snapshot_existing_paths(targets: list[Path], backup_root: Path):
    records = []
    seen: set[str] = set()

    for index, target_path in enumerate(sorted(targets, key=lambda item: (len(item.parts), str(item)))):
        normalized_path = str(target_path)
        if normalized_path in seen:
            continue
        seen.add(normalized_path)

        backup_path = None
        if target_path.exists():
            backup_path = backup_root / f"{index:02d}" / target_path.name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.is_dir():
                shutil.copytree(target_path, backup_path, dirs_exist_ok=True)
            else:
                shutil.copy2(target_path, backup_path)

        records.append({
            "target": target_path,
            "backup": backup_path,
        })

    return records


def _create_character_operation_backup_dir(config_manager, prefix: str):
    backup_root = Path(getattr(config_manager, "app_docs_dir", "")) / ".rollback_tmp"
    backup_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(backup_root))


def _restore_snapshot_paths(records) -> None:
    for record in sorted(records, key=lambda item: len(item["target"].parts), reverse=True):
        target_path = record["target"]
        backup_path = record.get("backup")

        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()

        if backup_path is None or not backup_path.exists():
            continue

        if backup_path.is_dir():
            shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, target_path)


def _build_character_tombstones_state(config_manager, character_name: str) -> dict:
    if is_cloudsave_disabled():
        return config_manager.build_default_character_tombstones_state()

    cloud_state = config_manager.load_cloudsave_local_state()
    sequence_number = max(1, int(cloud_state.get("next_sequence_number") or 1))
    tombstone_state = config_manager.load_character_tombstones_state()
    normalized_entries = {}
    for entry in tombstone_state.get("tombstones") or []:
        if not isinstance(entry, dict):
            continue
        existing_name = str(entry.get("character_name") or "").strip()
        if not existing_name:
            continue
        normalized_entries[existing_name] = entry

    normalized_entries[character_name] = {
        "character_name": character_name,
        "deleted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sequence_number": sequence_number,
    }
    return {
        "version": config_manager.CHARACTER_TOMBSTONES_STATE_VERSION,
        "tombstones": [
            normalized_entries[existing_name]
            for existing_name in sorted(normalized_entries)
        ],
    }


async def _rollback_character_operation(
    config_manager,
    *,
    characters_snapshot: dict,
    memory_snapshot_records,
    tombstone_snapshot: dict | None = None,
    reason: str,
) -> str:
    rollback_errors: list[str] = []

    try:
        await asyncio.to_thread(_restore_snapshot_paths, memory_snapshot_records)
    except Exception as exc:
        rollback_errors.append(f"memory restore failed: {exc}")

    try:
        await asyncio.to_thread(
            config_manager.save_characters,
            characters_snapshot,
            bypass_write_fence=True,
        )
    except Exception as exc:
        rollback_errors.append(f"characters restore failed: {exc}")

    if tombstone_snapshot is not None:
        try:
            await asyncio.to_thread(
                config_manager.save_character_tombstones_state, tombstone_snapshot
            )
        except Exception as exc:
            rollback_errors.append(f"tombstones restore failed: {exc}")

    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
    except Exception as exc:
        rollback_errors.append(f"initialize_character_data failed: {exc}")

    try:
        reload_notified = await notify_memory_server_reload(reason=reason)
        if not reload_notified:
            rollback_errors.append("notify_memory_server_reload failed: returned False")
    except Exception as exc:
        rollback_errors.append(f"notify_memory_server_reload failed: {exc}")

    return "; ".join(rollback_errors)


@router.get('')
async def get_characters(request: Request):
    """Get character data, with persona auto-translation based on the user language."""
    _config_manager = get_config_manager()
    # 创建深拷贝，避免修改原始配置数据
    characters_data = copy.deepcopy(await _config_manager.aload_characters())
    if isinstance(characters_data.get('猫娘'), dict):
        # COMPAT(v1->v2): 前端仍依赖旧平铺字段，接口层按需展开。
        for cat_name, cat_data in list(characters_data['猫娘'].items()):
            if isinstance(cat_data, dict):
                characters_data['猫娘'][cat_name] = _flatten_catgirl_for_response(cat_data)

    # 尝试从请求参数或请求头获取用户语言
    user_language = request.query_params.get('language')
    if not user_language:
        accept_lang = request.headers.get('Accept-Language', 'zh-CN')
        # Accept-Language 可能包含多个语言，取第一个
        user_language = accept_lang.split(',')[0].split(';')[0].strip()
    # 使用公共函数归一化语言代码
    user_language = normalize_language_code(user_language, format='full')

    # 如果语言是中文，不需要翻译
    if user_language == 'zh-CN':
        return _json_no_store_response(characters_data)

    # 需要翻译：翻译人设数据（在深拷贝上进行，不影响原始配置）
    try:
        from utils.language_utils import get_translation_service
        translation_service = get_translation_service(_config_manager)

        # 翻译主人数据
        if '主人' in characters_data and isinstance(characters_data['主人'], dict):
            characters_data['主人'] = await translation_service.translate_dict(
                characters_data['主人'],
                user_language,
                fields_to_translate=['昵称']
            )

        # 翻译猫娘数据（并行翻译以提升性能）
        if '猫娘' in characters_data and isinstance(characters_data['猫娘'], dict):
            async def translate_catgirl(name, data):
                if isinstance(data, dict):
                    return name, await translation_service.translate_dict(
                        data, user_language,
                        fields_to_translate=['昵称', '性别']  # 注意：不翻译档案名和 system_prompt
                    )
                return name, data

            results = await asyncio.gather(*[
                translate_catgirl(name, data)
                for name, data in characters_data['猫娘'].items()
            ])
            characters_data['猫娘'] = dict(results)

        return _json_no_store_response(characters_data)
    except Exception as e:
        logger.error(f"翻译人设数据失败: {e}，返回原始数据")
        return _json_no_store_response(characters_data)


@router.post('/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析猫娘重命名请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)

    new_name = str(new_name).strip()
    err = _validate_profile_name(new_name)
    if err:
        return JSONResponse({'success': False, 'error': err.replace('档案名', '新档案名')}, status_code=400)
    characters = await _config_manager.aload_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)

    # 如果当前猫娘是被重命名的猫娘，先缓存 WebSocket，
    # 只有在持久化和重载全部成功后才发送通知，避免前端先切换到未提交状态。
    is_current_catgirl = characters.get('当前猫娘') == old_name
    rename_notification_ws = None
    rename_notification_message = None

    # 检查当前角色是否有活跃的语音session
    if is_current_catgirl and old_name in session_manager:
        mgr = session_manager[old_name]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)

            if is_voice_mode:
                return JSONResponse({
                    'success': False,
                    'error': '语音状态下无法修改角色名称，请先停止语音对话后再修改'
                }, status_code=400)
    if is_current_catgirl and old_name in session_manager:
        rename_notification_ws = session_manager[old_name].websocket
        if rename_notification_ws:
            rename_notification_message = json.dumps({
                "type": "catgirl_switched",
                "new_catgirl": new_name,
                "old_catgirl": old_name
            })

    assert_cloudsave_writable(
        _config_manager,
        operation="rename",
        target=f"characters/{old_name} -> {new_name}",
    )

    released_memory_handle = await release_memory_server_character(
        old_name,
        reason=f"角色重命名前释放 SQLite 句柄: {old_name} -> {new_name}",
    )
    if not released_memory_handle:
        logger.warning("角色重命名前释放记忆服务器句柄失败，已阻止重命名: %s -> %s", old_name, new_name)
        return JSONResponse(
            {
                "success": False,
                "code": "MEMORY_SERVER_RELEASE_FAILED",
                "error": "释放角色记忆句柄失败，已阻止重命名，请稍后重试",
                "memory_server_released": False,
            },
            status_code=503,
        )

    characters_snapshot = copy.deepcopy(characters)
    memory_targets = list_character_memory_paths(_config_manager, old_name)
    memory_targets.extend(list_character_memory_paths(_config_manager, new_name))
    memory_targets.append(Path(_config_manager.memory_dir) / new_name)
    # 卡面文件纳入 snapshot，使迁移失败也能回滚
    old_face = _config_manager.card_faces_dir / f"{old_name}.png"
    new_face = _config_manager.card_faces_dir / f"{new_name}.png"
    old_meta = _config_manager.card_face_meta_path(old_name)
    new_meta = _config_manager.card_face_meta_path(new_name)
    memory_targets.append(old_face)
    memory_targets.append(new_face)
    memory_targets.append(old_meta)
    memory_targets.append(new_meta)
    memory_server_reloaded = False

    with _create_character_operation_backup_dir(_config_manager, "neko-rename-character-") as temp_dir:
        memory_snapshot_records = await asyncio.to_thread(
            _snapshot_existing_paths, memory_targets, Path(temp_dir)
        )
        try:
            rename_character_memory_storage(_config_manager, old_name, new_name)

            # 重命名角色真源
            characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
            _append_profile_rename_event(characters['猫娘'][new_name], old_name, new_name)
            # 如果当前猫娘是被重命名的猫娘，也需要更新
            if is_current_catgirl:
                characters['当前猫娘'] = new_name
            await _config_manager.asave_characters(characters)

            # Fast path：移除旧名 + 以新名启动一个 catgirl slot。
            # 等价于"删除旧 + 新增新"，不遍历其它 N-1 个。
            remove_one_catgirl = get_remove_one_catgirl()
            init_one_catgirl = get_init_one_catgirl()
            await remove_one_catgirl(old_name)
            await init_one_catgirl(new_name, is_new=True)

            # 迁移卡面 PNG 与 sidecar JSON（纳入同一事务）
            from datetime import datetime as _dt
            _ts = _dt.now().strftime('%Y%m%d%H%M%S')
            if old_face.exists():
                if new_face.exists():
                    backup_face = _config_manager.card_faces_dir / f"{new_name}.png.conflict-{_ts}.bak"
                    await asyncio.to_thread(new_face.rename, backup_face)
                    logger.info(f"[重命名卡面] 冲突备份: {new_face} -> {backup_face}")
                await asyncio.to_thread(old_face.rename, new_face)
                logger.info(f"[重命名卡面] 已迁移: {old_face} -> {new_face}")
            if old_meta.exists():
                if new_meta.exists():
                    backup_meta = _config_manager.card_face_meta_path(f"{new_name}.conflict-{_ts}.bak")
                    await asyncio.to_thread(new_meta.rename, backup_meta)
                    logger.info(f"[重命名卡面元数据] 冲突备份: {new_meta} -> {backup_meta}")
                await asyncio.to_thread(old_meta.rename, new_meta)
                logger.info(f"[重命名卡面元数据] 已迁移: {old_meta} -> {new_meta}")

            memory_server_reloaded = await notify_memory_server_reload(
                reason=f"角色重命名: {old_name} -> {new_name}",
            )
            if not memory_server_reloaded:
                rollback_error = await _rollback_character_operation(
                    _config_manager,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=memory_snapshot_records,
                    reason=f"角色重命名回滚（memory_server 重载失败）: {old_name} -> {new_name}",
                )
                logger.error(
                    "重命名角色后 notify_memory_server_reload 返回 False，已尝试回滚: %s -> %s",
                    old_name,
                    new_name,
                )
                error_message = "重命名角色失败: notify_memory_server_reload returned False"
                if rollback_error:
                    error_message = f"{error_message}; 回滚失败: {rollback_error}"
                return JSONResponse(
                    {
                        "success": False,
                        "error": error_message,
                    },
                    status_code=500,
                )

        except MaintenanceModeError as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                reason=f"维护模式：角色重命名回滚 {old_name} -> {new_name}",
            )
            if rollback_error:
                raise exc from RuntimeError(rollback_error)
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                reason=f"角色重命名回滚: {old_name} -> {new_name}",
            )
            logger.exception("重命名角色失败，已尝试回滚: %s -> %s", old_name, new_name)
            error_message = f"重命名角色失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse({"success": False, "error": error_message}, status_code=500)

    if is_current_catgirl:
        try:
            cleared = await _clear_theater_session_after_current_catgirl_rename(_config_manager, old_name)
            if cleared:
                logger.info("角色重命名：已结束旧名称 %s 下的小剧场 session", old_name)
        except Exception as exc:
            # 角色与记忆重命名已经提交，不能因附属 Theater 清理失败回滚另一套事务。
            logger.warning("角色重命名后清理小剧场 session 失败: lanlan=%s err=%s", old_name, exc)

    # 数据更新+重载+卡面迁移完成后再通知前端
    if memory_server_reloaded and rename_notification_ws and rename_notification_message:
        try:
            await rename_notification_ws.send_text(rename_notification_message)
            logger.info(f"已向 {old_name} 发送重命名通知")
        except Exception as e:
            logger.warning(f"发送重命名通知给 {old_name} 失败: {e}")

    pending_rename_ok = True
    pending_rename_error = ""
    try:
        await rename_new_character_greeting_pending(_config_manager, old_name, new_name)
    except Exception as exc:
        pending_rename_ok = False
        pending_rename_error = str(exc)
        logger.exception("rename new character greeting pending failed: %s -> %s", old_name, new_name)

    result = {
        "success": True,
        "memory_renamed": True,
        "memory_server_reloaded": memory_server_reloaded,
    }
    if not pending_rename_ok:
        result["partial_success"] = True
        result["pending_rename_ok"] = False
        result["pending_rename_failed"] = True
        result["pending_rename_error"] = pending_rename_error
    return result


@router.get('/current_catgirl')
async def get_current_catgirl():
    """Get the name of the currently active catgirl."""
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return _json_no_store_response({'current_catgirl': current_catgirl})


@router.post('/current_catgirl')
async def set_current_catgirl(request: Request):
    """Set the currently active catgirl."""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''

    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    if _validate_existing_character_path_name(catgirl_name):
        return JSONResponse({'success': False, 'error': '猫娘名称无效'}, status_code=400)

    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)

    old_catgirl = characters.get('当前猫娘', '')

    # 检查当前角色是否有活跃的语音session
    if old_catgirl and old_catgirl in session_manager:
        mgr = session_manager[old_catgirl]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)

            if is_voice_mode:
                return JSONResponse({
                    'success': False,
                    'error': '语音状态下无法切换角色，请先停止语音对话后再切换'
                }, status_code=400)
    async def _publish_current_catgirl() -> None:
        """只发布当前猫娘配置；小剧场事务负责决定它与旧演出的原子顺序。"""  # noqa: DOCSTRING_CJK
        # 等待小剧场角色锁期间配置可能被其他请求更新；发布前重读，避免旧快照覆盖并发新增或修改。
        latest_characters = await _config_manager.aload_characters()
        latest_characters['当前猫娘'] = catgirl_name
        await _config_manager.asave_characters(latest_characters)

    theater_transition = None
    if old_catgirl and old_catgirl != catgirl_name:
        theater_transition = await _publish_character_switch_with_theater_boundary(
            _config_manager,
            old_catgirl,
            _publish_current_catgirl,
        )
    else:
        await _publish_current_catgirl()
    # Fast path：切换只改变 `当前猫娘` 字段，per-k 的 prompt / voice_id / thread 都不变，
    # 只需刷新 globals 即可。N=20 只猫娘时从 O(N) 降到 O(1)。
    switch_current_catgirl_fast = get_switch_current_catgirl_fast()
    await switch_current_catgirl_fast()

    # 角色卡切换会复用同一个前端猫爪面板和工具服务全局状态。
    # 这里先把旧状态归零，避免新角色刷新后继承上一张卡的开关状态。
    if old_catgirl != catgirl_name:
        await force_disable_agent_for_character_switch(catgirl_name, old_catgirl)

    # B8: if the previous character had an active game route, finalize it
    # immediately. Otherwise the heartbeat-based timeout (10-60s) would
    # leave a stale ``OmniOfflineClient`` consuming game events under the
    # outgoing character's name and keep the SessionManager takeover
    # muting the incoming character's ordinary chat output.
    if old_catgirl and old_catgirl != catgirl_name:
        try:
            from main_routers.game_router import finalize_game_routes_for_character
            finalized = await finalize_game_routes_for_character(old_catgirl)
            if finalized:
                logger.info(
                    "角色切换：已收尾 %d 个旧角色 %s 的游戏路由",
                    finalized,
                    old_catgirl,
                )
        except Exception as exc:
            # Swallow — character switch must not fail because of
            # game-route cleanup; the heartbeat sweep will eventually
            # clean up if this hook misses.
            logger.warning("角色切换游戏路由收尾失败: lanlan=%s err=%s", old_catgirl, exc)

        if theater_transition and theater_transition.get("cleared"):
            logger.info("角色切换：已结束旧角色 %s 的小剧场 session", old_catgirl)
        elif theater_transition and theater_transition.get("cleanup_error"):
            # 配置已经在角色锁内发布，清理失败不能重复保存；后续 TTS 仍会按当前猫娘校验拒绝旧对白。
            logger.warning(
                "角色切换小剧场 session 清理失败: lanlan=%s err=%s",
                old_catgirl,
                theater_transition["cleanup_error"],
            )

    # 通过WebSocket通知所有连接的客户端
    # 使用session_manager中的websocket，但需要确保websocket已设置
    notification_count = 0
    logger.info(f"开始通知WebSocket客户端：猫娘从 {old_catgirl} 切换到 {catgirl_name}")

    message = json.dumps({
        "type": "catgirl_switched",
        "new_catgirl": catgirl_name,
        "old_catgirl": old_catgirl
    })

    # 并行通知所有 session_manager —— 每个 send_text 独立，per-mgr 失败时只清自己的 ws，
    # 串行版本里一个慢/卡的 ws 会拖累后面的通知。
    snapshot = list(session_manager.items())
    for lanlan_name, mgr in snapshot:
        logger.info(f"检查 {lanlan_name} 的WebSocket: websocket存在={mgr.websocket is not None}")

    async def _notify_one(lanlan_name, mgr):
        ws = mgr.websocket
        if not ws:
            return False
        try:
            await ws.send_text(message)
            logger.info(f"✅ 已通过WebSocket通知 {lanlan_name} 的连接：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
            return True
        except Exception as e:
            logger.warning(f"❌ 通知 {lanlan_name} 的连接失败: {e}")
            # 如果发送失败，可能是连接已断开，清空websocket引用
            if mgr.websocket == ws:
                mgr.websocket = None
            return False

    _notify_results = await asyncio.gather(
        *(_notify_one(n, m) for n, m in snapshot),
        return_exceptions=True,
    )
    notification_count = sum(1 for r in _notify_results if r is True)

    if notification_count > 0:
        logger.info(f"✅ 已通过WebSocket通知 {notification_count} 个连接的客户端：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
    else:
        logger.warning("⚠️ 没有找到任何活跃的WebSocket连接来通知猫娘切换")
        logger.warning("提示：请确保前端页面已打开并建立了WebSocket连接，且已调用start_session")

    return {"success": True}


def _theater_root_for_config_manager(config_manager) -> Path:
    """根据当前配置管理器解析小剧场私有运行目录。"""  # noqa: DOCSTRING_CJK
    app_docs_dir = getattr(config_manager, "app_docs_dir", None)
    if app_docs_dir:
        return Path(app_docs_dir) / "theater"
    config_dir = getattr(config_manager, "config_dir", None)
    if config_dir:
        return Path(config_dir).parent / "theater"
    return Path("data") / "theater"


async def _publish_character_switch_with_theater_boundary(config_manager, old_catgirl: str, publish) -> dict:
    """让当前猫娘发布与旧剧场 TTS 共享角色边界，不改变其他角色切换步骤。"""  # noqa: DOCSTRING_CJK
    # 延迟导入避免角色 Router 在应用启动阶段反向依赖小剧场运行时。
    from services.theater import runtime as theater_runtime

    return await theater_runtime.publish_character_switch(
        _theater_root_for_config_manager(config_manager),
        old_lanlan_name=old_catgirl,
        publish=publish,
    )


async def _clear_theater_session_after_current_catgirl_rename(config_manager, old_catgirl: str) -> bool:
    """当前猫娘改名成功后结束旧 owner 的演出，避免留下不可恢复的 active key。"""  # noqa: DOCSTRING_CJK
    # 延迟导入保持角色 Router 的启动依赖方向不变；清理失败由调用方记录但不回滚角色事务。
    from services.theater import runtime as theater_runtime

    result = await theater_runtime.clear_character_session(
        _theater_root_for_config_manager(config_manager),
        lanlan_name=old_catgirl,
    )
    return bool(result.get("cleared"))


@router.post('/reload')
async def reload_character_config():
    """Reload the character config (hot reload)."""
    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        return {"success": True, "message": "角色配置已重新加载"}
    except Exception as e:
        logger.error(f"重新加载角色配置失败: {e}")
        return JSONResponse(
            {'success': False, 'error': f'重新加载失败: {str(e)}'},
            status_code=500
        )


@router.post('/master')
async def update_master(request: Request):
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析主人更新请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'success': False, 'error': '请求体必须是JSON对象'}, status_code=400)
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    characters = await _config_manager.aload_characters()
    previous_master = characters.get('主人') if isinstance(characters.get('主人'), dict) else {}
    previous_profile_name = ""
    if isinstance(previous_master, dict):
        previous_profile_name = str(previous_master.get('档案名') or '').strip()
    requested_profile_name = str(data.get('档案名') or '').strip()
    profile_name = previous_profile_name or requested_profile_name
    renamed_via_body_fallback = False
    if (
        previous_profile_name
        and requested_profile_name
        and requested_profile_name != previous_profile_name
        and _profile_name_contains_path_separator(previous_profile_name)
    ):
        profile_name = requested_profile_name
        renamed_via_body_fallback = True
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    next_master = {
        k: v
        for k, v in data.items()
        if v and k not in CHARACTER_RESERVED_FIELD_SET and k != '档案名'
    }
    next_master['档案名'] = profile_name
    if isinstance(previous_master, dict) and isinstance(previous_master.get('_reserved'), dict):
        next_master['_reserved'] = copy.deepcopy(previous_master['_reserved'])
    if renamed_via_body_fallback:
        _append_profile_rename_event(next_master, previous_profile_name, profile_name)
    characters['主人'] = next_master
    await _config_manager.asave_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}


@router.post('/master/{old_name}/rename')
async def rename_master(old_name: str, request: Request):
    """Rename the master profile."""
    _config_manager = get_config_manager()
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析主人重命名请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)

    new_name = str(new_name).strip()
    err = _validate_profile_name(new_name)
    if err:
        return JSONResponse({'success': False, 'error': err.replace('档案名', '新档案名')}, status_code=400)

    async with _ugc_sync_lock:
        characters = await _config_manager.aload_characters()
        if '主人' not in characters or not characters['主人']:
            return JSONResponse({'success': False, 'error': '我的档案不存在'}, status_code=404)

        current_master = characters['主人'].get('档案名', '')
        if current_master != old_name:
            return JSONResponse({'success': False, 'error': '原档案名不匹配'}, status_code=400)

        characters['主人']['档案名'] = new_name
        _append_profile_rename_event(characters['主人'], old_name, new_name)
        await _config_manager.asave_characters(characters)

    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
    except Exception as e:
        logger.error(f"重命名后重新加载配置失败: {e}")
        return JSONResponse({
            'success': True,
            'partial_success': True,
            'renamed': True,
            'reload_error': str(e)
        }, status_code=200)

    return {"success": True}


@router.post('/catgirl')
async def add_catgirl(request: Request):
    try:
        raw_data = await request.json()
    except Exception as e:
        logger.warning(f"解析添加猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not raw_data:
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)

    profile_name = raw_data.get('档案名')
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    data = _filter_mutable_catgirl_fields(raw_data)
    requested_field_order = _extract_catgirl_field_order_payload(raw_data)
    data['档案名'] = str(profile_name).strip()

    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    key = data['档案名']

    # 检查是否已存在同名角色，使用 Windows 风格的命名 (x)
    if key in characters.get('猫娘', {}):
        base_name = key
        counter = 1
        while f"{base_name}({counter})" in characters.get('猫娘', {}):
            counter += 1
        key = f"{base_name}({counter})"
        data['档案名'] = key
        logger.info(f'猫娘名称冲突，已重命名为: {key}')

    if '猫娘' not in characters:
        characters['猫娘'] = {}

    # 创建猫娘数据，只保存非空字段
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名':
            if v:  # 只保存非空字段
                catgirl_data[k] = v

    characters['猫娘'][key] = catgirl_data
    _sync_catgirl_field_order(catgirl_data, requested_field_order)
    # 默认走 free preset：非 free / 非 lanlan.tech 通道由 LLMSessionManager 现有 gate 清空 self.voice_id，不会泄漏给其他 TTS provider。
    # 从 free_voices['cuteGirl'] 读以避免硬编码漂移；缺失时回退到首个非空预设，再回退到旧版默认值。
    default_free_voice_id = _get_new_catgirl_default_voice_id()
    set_reserved(catgirl_data, 'voice_id', default_free_voice_id)
    await _config_manager.asave_characters(characters)
    pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, key, "create")

    # Fast path：新增只需为 `key` 这一个 catgirl 分配资源 + 启动线程，不影响其它角色。
    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(key, is_new=True)

    memory_server_reloaded = await notify_memory_server_reload(reason=f"新角色: {key}")

    response: dict = {
        "success": True,
        "character_name": key,
        "memory_server_reloaded": memory_server_reloaded,
    }
    if not pending_mark_ok:
        response["partial_success"] = True
        response["pending_mark_ok"] = False
        response["pending_mark_failed"] = True
        response["pending_mark_error"] = pending_mark_error
    return response


@router.put('/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    try:
        raw_data = await request.json()
    except Exception as e:
        logger.warning(f"解析更新猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not raw_data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)

    # COMPAT(v1->v2): 兼容旧客户端仍通过通用接口提交 voice_id。
    # 通用字段仍按保留字段规则过滤，voice_id 走独立检测与应用逻辑。
    voice_id_in_payload = 'voice_id' in raw_data
    requested_voice_id = ''
    if voice_id_in_payload:
        requested_voice_id = str(raw_data.get('voice_id') or '').strip()

    # 兼容前端自动修复：允许通过通用接口修改 model_type 保留字段。
    model_type_in_payload = 'model_type' in raw_data
    requested_model_type = ''
    if model_type_in_payload:
        requested_model_type = str(raw_data.get('model_type') or '').strip().lower()
        if requested_model_type == 'vrm':
            requested_model_type = 'live3d'
        if requested_model_type and requested_model_type not in ('live2d', 'live3d', 'pngtuber'):
            return JSONResponse(
                {'success': False, 'error': f'无效的模型类型: {requested_model_type}，只允许 live2d、live3d 或 pngtuber'},
                status_code=400,
            )

    data = _filter_mutable_catgirl_fields(raw_data)
    requested_field_order = _extract_catgirl_field_order_payload(raw_data)
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    previous_catgirl_data = copy.deepcopy(characters['猫娘'][name])

    old_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
    voice_id_will_change = voice_id_in_payload and old_voice_id != requested_voice_id
    if voice_id_will_change:
        session_manager = get_session_manager()
        if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
            return _voice_session_starting_response()

    if voice_id_in_payload and requested_voice_id:
        # 验证 voice_id 是否在 voice_storage 中
        if not _config_manager.validate_voice_id(requested_voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False,
                'error': f'voice_id "{requested_voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)

    # 只更新前端传来的普通字段，未传字段删除；保留字段始终交由专用接口管理
    removed_fields = []
    for k in characters['猫娘'][name]:
        if k not in data and k not in CHARACTER_RESERVED_FIELD_SET:
            removed_fields.append(k)
    for k in removed_fields:
        characters['猫娘'][name].pop(k)

    # 更新普通字段
    for k, v in data.items():
        if k != '档案名' and v:
            characters['猫娘'][name][k] = v

    # 兼容旧接口：若请求中带有 voice_id，则同步写入保留字段（惰性迁移成结构对象）。
    if voice_id_in_payload:
        set_reserved(characters['猫娘'][name], 'voice_id', _config_manager.voice_id_to_storage_value(requested_voice_id))

    # 兼容前端自动修复：若请求中带有 model_type，则同步写入保留字段。
    if model_type_in_payload and requested_model_type:
        set_reserved(characters['猫娘'][name], 'avatar', 'model_type', requested_model_type)

    _sync_catgirl_field_order(characters['猫娘'][name], requested_field_order)

    await _config_manager.asave_characters(characters)

    new_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
    voice_id_changed = voice_id_in_payload and old_voice_id != new_voice_id
    prompt_fields_changed = _catgirl_prompt_fields_changed(previous_catgirl_data, characters['猫娘'][name])

    # 显式记录被过滤的保留字段，避免“被吞掉”无感知。
    ignored_reserved_fields = sorted(
        (set(raw_data.keys()) & CHARACTER_RESERVED_FIELD_SET) - {'voice_id', 'model_type'}
    )
    if ignored_reserved_fields:
        logger.info(
            "update_catgirl ignored reserved fields for %s: %s",
            name,
            ", ".join(ignored_reserved_fields),
        )

    session_ended = False
    context_refresh_result = {
        "context_refreshed": False,
        "recent_history_cleared": False,
        "reload_notified": False,
        "session_restarted": False,
    }
    if prompt_fields_changed:
        context_refresh_result = await _refresh_catgirl_context_after_profile_change(
            _config_manager,
            name,
            characters,
            is_new=False,
        )
        session_ended = context_refresh_result["session_restarted"]
    elif voice_id_changed:
        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))

        # 如果是当前活跃的猫娘，只结束当前语音会话；voice_id 会在下方刷新到 session_manager。
        if is_current_catgirl and name in session_manager and session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已变更（{old_voice_id} -> {new_voice_id}），准备结束当前语音会话...")
            notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
            if callable(notify_session_ended):
                await notify_session_ended()
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
            # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
            # 否则下一次 start_session 会被旧熔断静默拦截。
            session_manager[name].reset_session_start_circuit()

        if is_current_catgirl:
            # Fast path：只刷新被编辑角色的 session_manager（prompt/voice_id），
            # 其它 N-1 个 catgirl 不动。
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(name, is_new=False)
            logger.info("配置已重新加载，新的voice_id已生效")
        else:
            # 非当前猫娘：原来靠下次 switch 的全量 init 顺带 rescue。切换改走 fast path
            # 后 rescue 不再发生，所以这里必须显式刷 session_manager[name]。
            # init_one_catgirl 只写 session_manager[name] 的 prompt/voice_id，不碰当前 session。
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(name, is_new=False)
            logger.info(f"非当前猫娘 {name} 的音色已更新并同步到 session_manager")
    else:
        # Fast path：普通字段编辑，只刷新被编辑角色。
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)

    return {
        "success": True,
        **context_refresh_result,
        "voice_id_changed": voice_id_changed,
        "session_restarted": session_ended,
        "ignored_reserved_fields": ignored_reserved_fields,
    }


@router.post('/catgirl/delete')
async def delete_catgirl_by_body(request: Request):
    """Delete a character by JSON body.

    This is the rescue path for historical unsafe names such as "." that cannot
    be represented safely as a URL path segment.
    """
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析删除猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    name = str((data or {}).get('name') or '').strip()
    if not name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    return await _delete_catgirl_by_name(name)


@router.delete('/catgirl/{name}')
async def delete_catgirl(name: str):
    return await _delete_catgirl_by_name(name)


async def _delete_catgirl_by_name(name: str):
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 检查是否是当前正在使用的猫娘
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！请先切换到其他猫娘后再删除。'}, status_code=400)

    safe_path_name = _validate_existing_character_path_name(name) is None
    assert_cloudsave_writable(
        _config_manager,
        operation="delete",
        target=f"characters/{name}",
    )

    if not safe_path_name:
        logger.warning("正在执行历史非法角色名救援删除，仅移除配置，不触碰角色文件路径: %s", name)
        characters_snapshot = copy.deepcopy(characters)
        try:
            del characters['猫娘'][name]
            await _config_manager.asave_characters(characters)

            remove_one_catgirl = get_remove_one_catgirl()
            await remove_one_catgirl(name)

            memory_server_reloaded = await notify_memory_server_reload(reason=f"救援删除非法角色名: {name}")
            if not memory_server_reloaded:
                rollback_error = await _rollback_character_operation(
                    _config_manager,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=[],
                    reason=f"救援删除非法角色名回滚: {name}",
                )
                error_message = "救援删除非法角色名失败: notify_memory_server_reload returned False"
                if rollback_error:
                    error_message = f"{error_message}; 回滚失败: {rollback_error}"
                return JSONResponse({"success": False, "error": error_message}, status_code=500)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=[],
                reason=f"救援删除非法角色名回滚: {name}",
            )
            logger.exception("救援删除非法角色名失败，已尝试回滚: %s", name)
            error_message = f"救援删除非法角色名失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse({"success": False, "error": error_message}, status_code=500)

        return {
            "success": True,
            "unsafe_name_rescue": True,
            "memory_deleted": False,
            "card_face_deleted": False,
            "memory_server_reloaded": memory_server_reloaded,
        }

    released_memory_handle = await release_memory_server_character(
        name,
        reason=f"角色删除前释放 SQLite 句柄: {name}",
    )
    if not released_memory_handle:
        logger.warning("角色删除前释放记忆服务器句柄失败，已阻止删除: %s", name)
        return JSONResponse(
            {
                "success": False,
                "code": "MEMORY_SERVER_RELEASE_FAILED",
                "error": "释放角色记忆句柄失败，已阻止删除，请稍后重试",
                "memory_server_released": False,
            },
            status_code=503,
        )

    characters_snapshot = copy.deepcopy(characters)
    memory_targets = list_character_memory_paths(_config_manager, name)
    face_path = _config_manager.card_faces_dir / f"{name}.png"
    meta_path = _config_manager.card_face_meta_path(name)
    memory_targets.append(face_path)
    memory_targets.append(meta_path)

    with _create_character_operation_backup_dir(_config_manager, "neko-delete-character-") as temp_dir:
        memory_snapshot_records = await asyncio.to_thread(
            _snapshot_existing_paths, memory_targets, Path(temp_dir)
        )
        tombstone_snapshot = None
        memory_server_reloaded = False
        try:
            if not is_cloudsave_disabled():
                tombstone_snapshot = copy.deepcopy(_config_manager.load_character_tombstones_state())

            removed_memory_paths = await asyncio.to_thread(
                delete_character_memory_storage, _config_manager, name
            )
            for entry_path in removed_memory_paths:
                logger.info(f"已删除: {entry_path}")

            # 同步删除卡面 PNG 与 sidecar JSON（纳入同一事务以便回滚）
            if face_path.exists():
                await asyncio.to_thread(face_path.unlink)
            if meta_path.exists():
                await asyncio.to_thread(meta_path.unlink)

            if not is_cloudsave_disabled():
                await asyncio.to_thread(
                    _config_manager.save_character_tombstones_state,
                    _build_character_tombstones_state(_config_manager, name),
                )

            # 删除角色配置
            del characters['猫娘'][name]
            await _config_manager.asave_characters(characters)
            # Fast path：只停该角色的线程 + 清 dict + 刷 globals，不遍历其它 N-1 个。
            remove_one_catgirl = get_remove_one_catgirl()
            await remove_one_catgirl(name)
            memory_server_reloaded = await notify_memory_server_reload(reason=f"删除角色: {name}")
            if not memory_server_reloaded:
                raise RuntimeError("notify_memory_server_reload returned False")
            if is_cloudsave_disabled():
                try:
                    from main_routers.workshop_router import mark_session_deleted_character_name

                    mark_session_deleted_character_name(name)
                except Exception as exc:
                    logger.warning("记录本会话工坊删除标记失败: %s", exc)
        except MaintenanceModeError as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                tombstone_snapshot=tombstone_snapshot,
                reason=f"维护模式：删除角色回滚 {name}",
            )
            if rollback_error:
                raise exc from RuntimeError(rollback_error)
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                tombstone_snapshot=tombstone_snapshot,
                reason=f"删除角色回滚: {name}",
            )
            logger.exception("删除角色失败，已尝试回滚: %s", name)
            error_message = f"删除角色失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse(
                {
                    "success": False,
                    "error": error_message,
                    "memory_server_released": released_memory_handle,
                },
                status_code=500,
            )

    pending_remove_ok = True
    pending_remove_error = ""
    try:
        await remove_new_character_greeting_pending(_config_manager, name)
    except Exception as exc:
        pending_remove_ok = False
        pending_remove_error = str(exc)
        logger.exception("remove new character greeting pending failed: %s", name)

    result = {"success": True, "memory_server_reloaded": memory_server_reloaded}
    if not pending_remove_ok:
        result["partial_success"] = True
        result["pending_remove_ok"] = False
        result["pending_remove_failed"] = True
        result["pending_remove_error"] = pending_remove_error
    return result


@router.post('/set_microphone')
async def set_microphone(request: Request):
    try:
        data = await request.json()
        microphone_id = data.get('microphone_id')

        # 使用标准的load/save函数
        _config_manager = get_config_manager()
        characters_data = await _config_manager.aload_characters()

        # 添加或更新麦克风选择
        characters_data['当前麦克风'] = microphone_id

        # 保存配置
        await _config_manager.asave_characters(characters_data)
        # 麦克风 ID 是纯前端读取的字段（仅 get_microphone 读），不影响任何 catgirl
        # 的 prompt / voice_id / session_manager，无需触发任何 init。

        return {"success": True}
    except Exception as e:
        logger.error(f"保存麦克风选择失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/get_microphone')
async def get_microphone():
    try:
        _config_manager = get_config_manager()
        # 使用配置管理器加载角色配置
        characters_data = await _config_manager.aload_characters()

        # 获取保存的麦克风选择
        microphone_id = characters_data.get('当前麦克风')

        return {"microphone_id": microphone_id}
    except Exception as e:
        logger.error(f"获取麦克风选择失败: {e}")
        return {"microphone_id": None}

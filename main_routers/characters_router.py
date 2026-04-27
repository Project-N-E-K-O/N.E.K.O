# -*- coding: utf-8 -*-
"""
Characters Router

Handles character (catgirl) management endpoints including:
- Character CRUD operations
- Voice settings
- Microphone settings
"""
import re
import json
import io
import os
import shutil
import asyncio
import copy
import base64
import hashlib
import struct
import tempfile
import zlib
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse, Response
import httpx
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

from .shared_state import (
    get_config_manager,
    get_session_manager,
    get_initialize_character_data,
    get_switch_current_catgirl_fast,
    get_init_one_catgirl,
    get_remove_one_catgirl,
)
from .workshop_router import _ugc_sync_lock
from main_logic.tts_client import get_custom_tts_voices, CustomTTSVoiceFetchError
from utils.character_memory import (
    delete_character_memory_storage,
    list_character_memory_paths,
    rename_character_memory_storage,
)
from utils.config_manager import get_reserved, set_reserved, flatten_reserved
from utils.audio import normalize_voice_clone_api_audio, validate_audio_file
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.voice_clone import (
    MinimaxVoiceCloneClient,
    MinimaxVoiceCloneError,
    minimax_normalize_language,
    sanitize_minimax_voice_prefix,
    MINIMAX_VOICE_STORAGE_KEY,
    MINIMAX_INTL_VOICE_STORAGE_KEY,
    MINIMAX_PREFIX_MAX_LENGTH,
    get_minimax_base_url,
    get_minimax_storage_prefix,
    QwenVoiceCloneClient,
    QwenVoiceCloneError,
    qwen_language_hints,
)
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.frontend_utils import find_models, find_model_directory, is_user_imported_model
from utils.language_utils import normalize_language_code
from utils.logger_config import get_module_logger
from utils.url_utils import encode_url_path
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from config import MEMORY_SERVER_PORT, TFLINK_UPLOAD_URL, CHARACTER_RESERVED_FIELDS

router = APIRouter(prefix="/api/characters", tags=["characters"])
logger = get_module_logger(__name__, "Main")


CHARACTER_RESERVED_FIELD_SET = set(CHARACTER_RESERVED_FIELDS)


def _json_no_store_response(content, *, status_code: int = 200):
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _derive_live2d_model_name(model_ref: str) -> str:
    raw_ref = str(model_ref or "").strip()
    if not raw_ref:
        return ""
    parsed_ref = urlparse(raw_ref)
    is_http_url = parsed_ref.scheme in {"http", "https"} and bool(parsed_ref.netloc)
    model_ref_source = parsed_ref.path if is_http_url and parsed_ref.path else raw_ref
    normalized_ref = model_ref_source.strip().replace("\\", "/")
    if not normalized_ref:
        return ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            return parts[-2]
        filename = parts[-1] if parts else normalized_ref
        return filename[:-len(".model3.json")]
    return normalized_ref.rsplit("/", 1)[-1]


def _normalize_live2d_catalog_path(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if not normalized_path:
        return ""
    if normalized_path.startswith("/workshop/"):
        parts = [part for part in normalized_path.split("/") if part]
        return "/".join(parts[2:]) if len(parts) >= 3 else ""
    for prefix in ("/user_live2d/", "/user_live2d_local/", "/static/"):
        if normalized_path.startswith(prefix):
            return normalized_path[len(prefix):]
    return normalized_path.lstrip("/")


def _is_same_live2d_catalog_model_path(candidate_path: str, target_path: str) -> bool:
    candidate_normalized = _normalize_live2d_catalog_path(candidate_path)
    target_normalized = _normalize_live2d_catalog_path(target_path)
    if not candidate_normalized or not target_normalized:
        return False
    if candidate_normalized == target_normalized:
        return True
    candidate_tail = "/".join(candidate_normalized.split("/")[-2:])
    target_tail = "/".join(target_normalized.split("/")[-2:])
    return bool(candidate_tail and candidate_tail == target_tail)


def _derive_live2d_asset_source(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external"
    if normalized_path.startswith("/workshop/"):
        return "steam_workshop"
    if normalized_path.startswith("/static/"):
        return "builtin"
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/")):
        return "local_imported"
    return ""


def _derive_model_asset_binding(model_path: str, *, item_id: str = "") -> tuple[str, str]:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()

    if not normalized_item_id and normalized_path.startswith("/workshop/"):
        parts = normalized_path.split("/")
        if len(parts) >= 3:
            normalized_item_id = str(parts[2] or "").strip()

    if normalized_item_id or normalized_path.startswith("/workshop/"):
        return "steam_workshop", normalized_item_id
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/")):
        return "local_imported", ""
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external", ""
    if normalized_path.startswith("/static/") or (normalized_path and not normalized_path.startswith("/")):
        return "builtin", ""
    return "", ""


def _find_live2d_model_catalog_entry(
    all_models: list[dict],
    *,
    model_name: str = "",
    model_path: str = "",
    asset_source: str = "",
    item_id: str = "",
):
    normalized_name = str(model_name or "").strip()
    normalized_path = _normalize_live2d_catalog_path(model_path)
    normalized_source = str(asset_source or "").strip().lower()
    normalized_item_id = str(item_id or "").strip()

    if normalized_item_id:
        item_matches = [
            model
            for model in all_models
            if str(model.get("item_id") or "").strip() == normalized_item_id
        ]
        item_name_matches = item_matches
        if normalized_name:
            item_name_matches = [
                model
                for model in item_matches
                if str(model.get("name") or "").strip() == normalized_name
            ]

        if normalized_path:
            strict_candidates = item_name_matches if item_name_matches else item_matches
            strict_item_match = next(
                (
                    model
                    for model in strict_candidates
                    if _is_same_live2d_catalog_model_path(
                        str(model.get("path") or "").strip().replace("\\", "/"),
                        normalized_path,
                    )
                ),
                None,
            )
            if strict_item_match is not None:
                return strict_item_match

        if len(item_name_matches) == 1:
            return item_name_matches[0]
        if len(item_matches) == 1:
            return item_matches[0]

    if normalized_path:
        expected_prefixes: tuple[str, ...] = ()
        if normalized_source == "builtin":
            expected_prefixes = ("/static/",)
        elif normalized_source in {"local", "local_imported"}:
            expected_prefixes = ("/user_live2d/", "/user_live2d_local/")
        elif normalized_source == "steam_workshop":
            expected_prefixes = ("/workshop/",)

        for model in all_models:
            candidate_path = str(model.get("path") or "").strip().replace("\\", "/")
            if expected_prefixes and not candidate_path.startswith(expected_prefixes):
                continue
            if _is_same_live2d_catalog_model_path(candidate_path, normalized_path):
                return model

    if normalized_name:
        return next(
            (model for model in all_models if str(model.get("name") or "").strip() == normalized_name),
            None,
        )

    return None


def _resolve_live2d_model_binding(model_identifier: str, *, item_id: str = "") -> tuple[str, str, str]:
    normalized_model = str(model_identifier or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()
    live2d_name = _derive_live2d_model_name(normalized_model)

    resolved_model_path = _normalize_live2d_catalog_path(normalized_model)
    if not resolved_model_path and live2d_name:
        resolved_model_path = f"{live2d_name}/{live2d_name}.model3.json"

    resolved_source = "steam_workshop" if normalized_item_id else (_derive_live2d_asset_source(normalized_model) or "local_imported")
    resolved_source_id = normalized_item_id

    # 外部链接保持原始绑定，不回绑到本地目录/创意工坊目录。
    if resolved_source == "manual_external":
        return normalized_model or resolved_model_path, resolved_source_id, resolved_source

    try:
        all_models = find_models()
        matching_model = _find_live2d_model_catalog_entry(
            all_models,
            model_name=live2d_name,
            model_path=normalized_model,
            asset_source=resolved_source,
            item_id=normalized_item_id,
        )
        if matching_model is not None:
            matched_path = str(matching_model.get("path") or "").strip().replace("\\", "/")
            resolved_model_path = _normalize_live2d_catalog_path(matched_path) or resolved_model_path
            resolved_source = _derive_live2d_asset_source(matched_path) or resolved_source
            resolved_source_id = normalized_item_id or str(matching_model.get("item_id") or "").strip()
    except Exception as exc:
        logger.debug("解析 Live2D 模型绑定时查找模型目录失败: %s", exc)

    return resolved_model_path, resolved_source_id, resolved_source


def _embed_zip_in_png_chunk(png_data: bytes, zip_data: bytes) -> bytes:
    """将 ZIP 数据嵌入 PNG 的 ancillary private chunk（neKo 块），插在 IEND 之前。

    生成的文件仍是合法 PNG，任何图片查看器 / Electron 都可以正常预览。
    """
    # PNG IEND 块固定 12 字节: 00 00 00 00  49 45 4E 44  AE 42 60 82
    if len(png_data) < 12 or png_data[-12:-4] != b'\x00\x00\x00\x00IEND':
        raise ValueError("Invalid PNG: IEND chunk not found at end of file")

    iend = png_data[-12:]
    before_iend = png_data[:-12]

    # 构建 neKo 块: length(4B, big-endian) + type(4B) + data + CRC32(4B)
    chunk_type = b'neKo'
    chunk_length = struct.pack('>I', len(zip_data))
    chunk_crc = struct.pack('>I', zlib.crc32(chunk_type + zip_data) & 0xFFFFFFFF)

    neko_chunk = chunk_length + chunk_type + zip_data + chunk_crc
    return before_iend + neko_chunk + iend


def _profile_name_units(name: str) -> int:
    # 计数规则与前端保持一致：ASCII(<=0x7F) 计 1，其它字符计 2
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in name)


def _validate_profile_name(name: str) -> str | None:
    result = validate_character_name(name, max_units=PROFILE_NAME_MAX_UNITS)
    if result.code == 'empty':
        return '档案名为必填项'
    if result.code == 'contains_path_separator':
        return '档案名不能包含路径分隔符(/或\\)'
    if result.code == 'contains_dot':
        return '档案名不能包含点号(.)'
    if result.code == 'reserved_device_name':
        return '档案名不能使用 Windows 保留设备名'
    if result.code == 'reserved_route_name':
        return '此名称是系统保留的路由名称，不能用作档案名'
    if result.code == 'invalid_character':
        return '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号'
    if result.code == 'too_long_units':
        return f'档案名长度不能超过{PROFILE_NAME_MAX_UNITS}单位（ASCII=1，其他=2；PROFILE_NAME_MAX_UNITS={PROFILE_NAME_MAX_UNITS}）'
    return None


def _filter_mutable_catgirl_fields(data: dict) -> dict:
    """过滤掉角色通用编辑接口不允许写入的保留字段。"""
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


def _build_minimax_request_prefix(prefix: str, provider_label: str) -> tuple[str, str]:
    """将用户输入的前缀规范化为 MiniMax 可接受的安全前缀。"""
    import uuid

    original_prefix = str(prefix or '').strip()
    safe_prefix = sanitize_minimax_voice_prefix(
        original_prefix,
        max_length=MINIMAX_PREFIX_MAX_LENGTH,
    )
    if safe_prefix != original_prefix:
        logger.info(
            "%s 音色前缀已规范化: %r -> %r",
            provider_label,
            original_prefix,
            safe_prefix,
        )
    return original_prefix, f"{safe_prefix}{uuid.uuid4().hex[:8]}"


async def send_reload_page_notice(session, message_text: str = "语音已更新，页面即将刷新"):
    """
    发送页面刷新通知给前端（通过 WebSocket）
    
    Args:
        session: LLMSessionManager 实例
        message_text: 要发送的消息文本（会被自动翻译）
    
    Returns:
        bool: 是否成功发送
    """
    if not session or not session.websocket:
        return False
    
    # 检查 WebSocket 连接状态
    if not hasattr(session.websocket, 'client_state') or session.websocket.client_state != session.websocket.client_state.CONNECTED:
        return False
    
    try:
        await session.websocket.send_text(json.dumps({
            "type": "reload_page",
            "message": json.dumps({"code": "RELOAD_PAGE", "details": {"message": message_text}})
        }))
        logger.info("已通知前端刷新页面")
        return True
    except Exception as e:
        logger.warning(f"通知前端刷新页面失败: {e}")
        return False


async def notify_memory_server_reload(*, reason: str = "") -> bool:
    try:
        async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload",
                timeout=5.0,
            )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 记忆服务器重新加载失败，status=%s, reason=%s",
                response.status_code,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已通知记忆服务器重新加载配置（%s）", reason or "角色数据更新")
            return True

        logger.warning(
            "⚠️ 记忆服务器重新加载返回非成功状态，payload=%s, reason=%s",
            payload,
            reason,
        )
    except Exception as exc:
        logger.warning("⚠️ 通知记忆服务器重新加载配置时出错: %s（reason=%s）", exc, reason)
    return False


async def release_memory_server_character(character_name: str, *, reason: str = "") -> bool:
    from urllib.parse import quote
    from utils.internal_http_client import get_internal_http_client

    try:
        encoded_name = quote(character_name, safe="")
        # 复用进程级单例避免 per-call SSLContext 冷启动（实测 ~1.1s/次）。
        # 单例在 on_shutdown 末尾由 aclose_internal_http_client 统一关闭，
        # release/upload 阶段之前都可安全共享；无需 async with。
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/release_character/{encoded_name}",
            timeout=5.0,
        )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 释放记忆服务器角色句柄失败，status=%s, character=%s, reason=%s",
                response.status_code,
                character_name,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已释放角色 %s 的记忆服务器句柄（%s）", character_name, reason or "角色文件操作前")
            return True

        logger.warning(
            "⚠️ 释放记忆服务器角色句柄返回非成功状态，payload=%s, character=%s, reason=%s",
            payload,
            character_name,
            reason,
        )
    except Exception as exc:
        logger.warning(
            "⚠️ 调用记忆服务器释放角色句柄时出错: %s（character=%s, reason=%s）",
            exc,
            character_name,
            reason,
        )
    return False


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
    """获取角色数据，支持根据用户语言自动翻译人设"""
    _config_manager = get_config_manager()
    # 创建深拷贝，避免修改原始配置数据
    characters_data = copy.deepcopy(await _config_manager.aload_characters())
    if isinstance(characters_data.get('猫娘'), dict):
        # COMPAT(v1->v2): 前端仍依赖旧平铺字段，接口层按需展开。
        for cat_name, cat_data in list(characters_data['猫娘'].items()):
            if isinstance(cat_data, dict):
                characters_data['猫娘'][cat_name] = flatten_reserved(cat_data)
    
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
                fields_to_translate=['档案名', '昵称']
            )
        
        # 翻译猫娘数据（并行翻译以提升性能）
        if '猫娘' in characters_data and isinstance(characters_data['猫娘'], dict):
            async def translate_catgirl(name, data):
                if isinstance(data, dict):
                    return name, await translation_service.translate_dict(
                        data, user_language,
                        fields_to_translate=['档案名', '昵称', '性别']  # 注意：不翻译 system_prompt
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


@router.get('/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """获取指定角色或当前角色的Live2D模型信息
    
    Args:
        catgirl_name: 角色名称
        item_id: 可选的物品ID，用于直接指定模型
    """
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        
        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')
        
        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        saved_model_path = ""
        saved_asset_source = ""
        saved_item_id = ""
        
        # 首先尝试通过item_id查找模型
        if item_id:
            try:
                logger.debug(f"尝试通过item_id {item_id} 查找模型")
                # 获取所有模型
                all_models = find_models()
                # 查找匹配item_id的模型
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)
                
                if matching_model:
                    logger.debug(f"通过item_id找到模型: {matching_model['name']}")
                    # 复制模型信息
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")
        
        # 如果没有通过item_id找到模型，再通过角色名称查找
        if not model_info and catgirl_name:
            # 在猫娘列表中查找
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                saved_model_path = get_reserved(
                    catgirl_data,
                    'avatar',
                    'live2d',
                    'model_path',
                    default='',
                    legacy_keys=('live2d',),
                )
                live2d_model_name = _derive_live2d_model_name(saved_model_path)
                saved_asset_source = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source',
                    default='',
                )
                
                # 检查是否有保存的item_id
                saved_item_id = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source_id',
                    default='',
                    legacy_keys=('live2d_item_id', 'item_id'),
                )
                if saved_item_id:
                    logger.debug(f"发现角色 {catgirl_name} 保存的item_id: {saved_item_id}")
                    try:
                        # 尝试通过保存的item_id查找模型
                        all_models = find_models()
                        matching_model = _find_live2d_model_catalog_entry(
                            all_models,
                            model_name=live2d_model_name,
                            model_path=saved_model_path,
                            asset_source=saved_asset_source,
                            item_id=saved_item_id,
                        )
                        if matching_model:
                            logger.debug(f"通过保存的item_id找到模型: {matching_model['name']}")
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")
        
        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 先从完整的模型列表中查找，这样可以获取到item_id等完整信息
                all_models = find_models()
                
                # 同时获取工坊模型列表，确保能找到工坊模型
                try:
                    from .workshop_router import get_subscribed_workshop_items
                    workshop_result = await get_subscribed_workshop_items()
                    if isinstance(workshop_result, dict) and workshop_result.get('success', False):
                        for item in workshop_result.get('items', []):
                            installed_folder = item.get('installedFolder')
                            workshop_item_id = item.get('publishedFileId')
                            if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and workshop_item_id:
                                # 检查安装目录下是否有.model3.json文件
                                for filename in os.listdir(installed_folder):
                                    if filename.endswith('.model3.json'):
                                        model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                        if model_name not in [m['name'] for m in all_models]:
                                            all_models.append({
                                                'name': model_name,
                                                'path': f'/workshop/{workshop_item_id}/{filename}',
                                                'source': 'steam_workshop',
                                                'item_id': workshop_item_id
                                            })
                                # 检查子目录
                                for subdir in os.listdir(installed_folder):
                                    subdir_path = os.path.join(installed_folder, subdir)
                                    if os.path.isdir(subdir_path):
                                        model_name = subdir
                                        model3_files = [f for f in os.listdir(subdir_path) if f.endswith('.model3.json')]
                                        if model3_files:
                                            model_file = model3_files[0]
                                            if model_name not in [m['name'] for m in all_models]:
                                                all_models.append({
                                                    'name': model_name,
                                                    'path': encode_url_path(f'/workshop/{workshop_item_id}/{model_name}/{model_file}'),
                                                    'source': 'steam_workshop',
                                                    'item_id': workshop_item_id
                                                })
                except Exception as we:
                    logger.debug(f"获取工坊模型列表时出错（非关键）: {we}")
                
                matching_model = model_info.copy() if model_info else None
                if matching_model is None:
                    # 保留前面已命中的 item_id 结果；仅在没有现成匹配时再做目录级回退查找。
                    matching_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id=saved_item_id,
                    )
                elif not item_id and not saved_item_id:
                    fallback_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id='',
                    )
                    if fallback_model is not None:
                        matching_model = fallback_model
                
                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if model_dir and os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            
                            # 使用保存的item_id构建model_path，从之前的逻辑中获取saved_item_id
                            saved_item_id = (
                                get_reserved(
                                    catgirl_data,
                                    'avatar',
                                    'asset_source_id',
                                    default='',
                                    legacy_keys=('live2d_item_id', 'item_id'),
                                ) if 'catgirl_data' in locals() else ''
                            )
                            
                            # 如果有保存的item_id，使用它构建路径
                            if saved_item_id:
                                if url_prefix == '/workshop':
                                    model_subdir = os.path.basename(model_dir.rstrip('/\\'))
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_subdir}/{model_file}')
                                else:
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_file}')
                                logger.debug(f"使用保存的item_id构建模型路径: {model_path}")
                            else:
                                # 原始路径构建逻辑
                                model_path = encode_url_path(f'{url_prefix}/{live2d_model_name}/{model_file}')
                                logger.debug(f"使用模型名称构建路径: {model_path}")
                            
                            model_info = {
                                'name': live2d_model_name,
                                'item_id': saved_item_id,
                                'path': model_path
                            }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")
        
        # 回退机制：如果没有找到模型，使用默认的mao_pro
        if not live2d_model_name or not model_info:
            logger.info(f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 mao_pro")
            live2d_model_name = 'mao_pro'
            try:
                # 先从完整的模型列表中查找mao_pro
                all_models = find_models()
                matching_model = next((m for m in all_models if m['name'] == 'mao_pro'), None)
                
                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
                else:
                    # 如果找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory('mao_pro')
                    if model_dir and os.path.exists(model_dir):
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            model_path = f'{url_prefix}/mao_pro/{model_file}'
                            model_info = {
                                'name': 'mao_pro',
                                'path': model_path,
                                'is_fallback': True  # 标记这是回退模型
                            }
            except Exception as e:
                logger.error(f"获取默认模型mao_pro失败: {e}")
        
        if model_info and isinstance(model_info.get('path'), str):
            model_info['path'] = encode_url_path(model_info['path'])

        return JSONResponse(content={
            'success': True,
            'catgirl_name': catgirl_name,
            'model_name': live2d_model_name,
            'model_info': model_info
        })
        
    except Exception as e:
        logger.error(f"获取角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@router.put('/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """更新指定猫娘的模型设置（支持Live2D和VRM）"""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        vrm_model = data.get('vrm')
        mmd_model = data.get('mmd')
        model_type = data.get('model_type', 'live2d')  # 默认为live2d以保持兼容性
        item_id = data.get('item_id')  # 获取可选的item_id
        vrm_animation = data.get('vrm_animation')  # 获取可选的VRM动作
        idle_animation = data.get('idle_animation')  # 获取可选的VRM待机动作
        mmd_animation = data.get('mmd_animation')  # 获取可选的MMD动作
        mmd_idle_animation = data.get('mmd_idle_animation')  # 获取可选的MMD待机动作

        # 根据model_type检查相应的模型字段
        model_type_str = str(model_type).lower() if model_type else 'live2d'
        
        # 【修复】model_type 只允许 {live2d, vrm, live3d}，否则 400
        if model_type_str not in ['live2d', 'vrm', 'live3d']:
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'无效的模型类型: {model_type}，只允许 live2d、vrm 或 live3d'
                },
                status_code=400
            )
        
        # 归一化：旧客户端发送的 'vrm' 统一为 'live3d'（走 Live3D VRM 子分支处理）
        if model_type_str == 'vrm':
            model_type_str = 'live3d'
        
        if model_type_str == 'live3d':
            # Live3D 模式：接受 VRM 或 MMD 模型
            if vrm_model and mmd_model:
                return JSONResponse(content={'success': False, 'error': '不能同时提供VRM和MMD模型，请选择其中一个'}, status_code=400)
            if vrm_model:
                # 验证 VRM 路径
                vrm_model_str = str(vrm_model).strip()
                if '://' in vrm_model_str or vrm_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含URL方案'}, status_code=400)
                if '..' in vrm_model_str:
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_prefixes = ['/user_vrm/', '/static/vrm/', '/workshop/']
                if not any(vrm_model_str.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径必须以 /user_vrm/、/static/vrm/ 或 /workshop/ 开头'}, status_code=400)
                vrm_model = vrm_model_str
            elif mmd_model:
                # 验证 MMD 路径
                mmd_model_str = str(mmd_model).strip()
                if '://' in mmd_model_str or mmd_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含URL方案'}, status_code=400)
                if '..' in mmd_model_str:
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_mmd_prefixes = ['/user_mmd/', '/static/mmd/', '/workshop/']
                if not any(mmd_model_str.startswith(prefix) for prefix in allowed_mmd_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径必须以 /user_mmd/、/static/mmd/ 或 /workshop/ 开头'}, status_code=400)
                mmd_model = mmd_model_str
            else:
                return JSONResponse(content={'success': False, 'error': '未提供VRM或MMD模型路径'}, status_code=400)
        else:
            if not live2d_model:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '未提供Live2D模型名称'
                    },
                    status_code=400
                )
        
        # 加载当前角色配置
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        
        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            return JSONResponse(
                {'success': False, 'error': '猫娘不存在'}, 
                status_code=404
            )
        
        # 切换模型类型时保留非当前模型配置，避免来回切换后丢失待机动作/光照等设置
        if model_type_str == 'live3d':
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live3d')
            active_model_binding_path = ""
            
            if vrm_model:
                # Live3D + VRM：更新当前激活的 VRM 配置，保留 MMD 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'vrm')
                set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'model_path', vrm_model)
                active_model_binding_path = vrm_model

                # 处理 VRM 动画（复用同样的验证逻辑）
                if 'vrm_animation' in data:
                    if vrm_animation is None or vrm_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', None)
                    else:
                        vrm_animation_str = str(vrm_animation).strip()
                        if '://' in vrm_animation_str or vrm_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含URL方案'}, status_code=400)
                        if '..' in vrm_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        if not any(vrm_animation_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', vrm_animation_str)
                
                if 'idle_animation' in data:
                    if idle_animation is None or idle_animation == '' or idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [])
                    elif isinstance(idle_animation, str):
                        idle_list = [idle_animation]
                    elif isinstance(idle_animation, list):
                        idle_list = idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(idle_animation, (str, list)) and idle_animation:
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        for item in idle_list:
                            item_str = str(item).strip()
                            if '://' in item_str or item_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in item_str:
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(item_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [str(x).strip() for x in idle_list])
                
                logger.debug(f"已保存角色 {name} 的Live3D(VRM)模型 {vrm_model}")
            elif mmd_model:
                # Live3D + MMD：更新当前激活的 MMD 配置，保留 VRM 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'mmd')
                set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'model_path', mmd_model)
                active_model_binding_path = mmd_model

                # 处理 MMD 动画
                if 'mmd_animation' in data:
                    if mmd_animation is None or mmd_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', None)
                    else:
                        mmd_animation_str = str(mmd_animation).strip()
                        if '://' in mmd_animation_str or mmd_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含URL方案'}, status_code=400)
                        if '..' in mmd_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        if not any(mmd_animation_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', mmd_animation_str)
                
                if 'mmd_idle_animation' in data:
                    if mmd_idle_animation is None or mmd_idle_animation == '' or mmd_idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [])
                    elif isinstance(mmd_idle_animation, str):
                        mmd_idle_list = [mmd_idle_animation]
                    elif isinstance(mmd_idle_animation, list):
                        mmd_idle_list = mmd_idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'mmd_idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(mmd_idle_animation, (str, list)) and mmd_idle_animation:
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        for item in mmd_idle_list:
                            mmd_idle_str = str(item).strip()
                            if '://' in mmd_idle_str or mmd_idle_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in mmd_idle_str:
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(mmd_idle_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [str(x).strip() for x in mmd_idle_list])
                
                logger.debug(f"已保存角色 {name} 的Live3D(MMD)模型 {mmd_model}")

            current_asset_source, current_asset_source_id = _derive_model_asset_binding(
                active_model_binding_path,
                item_id=str(item_id or ""),
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', current_asset_source_id)
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'asset_source',
                current_asset_source or 'local_imported',
            )
        else:
            # 更新Live2D模型设置，同时保存item_id（如果有）
            live2d_model_path, resolved_item_id, resolved_asset_source = _resolve_live2d_model_binding(
                live2d_model,
                item_id=str(item_id or ""),
            )
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'live2d',
                'model_path',
                live2d_model_path,
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live2d')

            if 'live2d_idle_animation' in data:
                live2d_idle_animation = data.get('live2d_idle_animation')
                logger.info(f"[Live2D Save] 收到 live2d_idle_animation 请求: {live2d_idle_animation}")

                if live2d_idle_animation is None:
                    set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                    logger.info(f"[Live2D Save] 已清空 idle_animation")
                elif isinstance(live2d_idle_animation, str):
                    live2d_idle_str = live2d_idle_animation.strip()
                    if not live2d_idle_str:
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                        logger.info(f"[Live2D Save] 已清空 idle_animation")
                    else:
                        if '://' in live2d_idle_str or live2d_idle_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含URL方案'}, status_code=400)
                        if '..' in live2d_idle_str:
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含路径遍历（..）'}, status_code=400)
                        if live2d_idle_str.startswith('/') or live2d_idle_str.startswith('\\') or re.match(r'^[A-Za-z]:', live2d_idle_str):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径必须是相对路径，不能是绝对路径'}, status_code=400)
                        if not live2d_idle_str.lower().endswith('.motion3.json'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作必须是 .motion3.json 文件'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', live2d_idle_str)
                        logger.info(f"[Live2D Save] 已保存 idle_animation: {live2d_idle_str}")
                else:
                    return JSONResponse(content={'success': False, 'error': 'live2d_idle_animation 必须是字符串或 null'}, status_code=400)
            else:
                logger.info(f"[Live2D Save] 请求中未包含 live2d_idle_animation 字段, data keys: {list(data.keys())}")

            if resolved_item_id:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', str(resolved_item_id))
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', 'steam_workshop')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model} 和item_id {resolved_item_id}")
            else:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', '')
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', resolved_asset_source or 'local_imported')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model}，asset_source={resolved_asset_source or 'local_imported'}")
        
        # 保存配置
        await _config_manager.asave_characters(characters)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        
        if model_type_str == 'live3d':
            active_model = vrm_model or mmd_model
            sub_type = 'VRM' if vrm_model else 'MMD'
            message = f'已更新角色 {name} 的Live3D({sub_type})模型为 {active_model}'
        else:
            message = f'已更新角色 {name} 的Live2D模型为 {live2d_model}'
        
        return JSONResponse(content={
            'success': True,
            'message': message
        })
        
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception("更新角色模型设置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


@router.patch('/catgirl/{name}/touch_set')
async def update_catgirl_touch_set(name: str, request: Request):
    """全量更新指定猫娘当前模型的触摸动画配置
    
    请求体格式:
    {
        "model_name": "模型名称",
        "touch_set": {
            "default": {"motions": [], "expressions": []},
            "HitArea1": {"motions": ["motion1"], "expressions": ["exp1"]}
        }
    }
    """
    try:
        data = await request.json()
        
        model_name = data.get('model_name')
        touch_set_data = data.get('touch_set')

        if not isinstance(model_name, str) or not model_name.strip():
            return JSONResponse(
                content={'success': False, 'error': 'model_name 必须是非空字符串'},
                status_code=400
            )
        model_name = model_name.strip()
        
        if touch_set_data is None:
            return JSONResponse(
                content={'success': False, 'error': '缺少 touch_set 参数'},
                status_code=400
            )
        
        if not isinstance(touch_set_data, dict):
            return JSONResponse(
                content={'success': False, 'error': 'touch_set 必须是对象'},
                status_code=400
            )
        
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        
        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(
                content={'success': False, 'error': '角色不存在'},
                status_code=404
            )
        
        existing_touch_set = get_reserved(characters['猫娘'][name], 'touch_set', default={})
        
        if not existing_touch_set:
            existing_touch_set = {}
        
        existing_touch_set[model_name] = touch_set_data
        
        set_reserved(characters['猫娘'][name], 'touch_set', existing_touch_set)
        await _config_manager.asave_characters(characters)
        
        initialize_character_data = get_initialize_character_data()
        if initialize_character_data:
            await initialize_character_data()
        
        logger.debug(f"已更新角色 {name} 模型 {model_name} 的触摸配置")
        
        return JSONResponse(content={
            'success': True,
            'message': f'已更新角色 {name} 的触摸配置',
            'touch_set': existing_touch_set
        })
        
    except Exception as e:
        logger.exception("更新触摸配置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/lighting')
async def update_catgirl_lighting(name: str, request: Request):
    """更新指定猫娘的VRM打光配置
    
    Args:
        name: 角色名称
        request: 请求体包含 lighting (dict) 和可选的 apply_runtime (bool)
                 apply_runtime 也可通过 query param 传递,query param 优先级更高
    """
    try:
        data = await request.json()
        lighting = data.get('lighting')
        
        apply_runtime = data.get('apply_runtime', False)
        query_params = request.query_params
        if 'apply_runtime' in query_params:
            apply_runtime = query_params.get('apply_runtime', '').lower() in ('true', '1', 'yes')

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        model_type = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'model_type',
            default='live2d',
            legacy_keys=('model_type',),
        )
        # 统一做 .lower() 处理，避免大小写/空值导致误判
        model_type_normalized = str(model_type).lower() if model_type else 'live2d'
        if model_type_normalized not in ('vrm', 'live3d'):
            logger.warning(f"角色 {name} 不是VRM/Live3D模型，但仍保存打光配置")
        
        from config import get_default_vrm_lighting
        existing_lighting = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            default=None,
            legacy_keys=('lighting',),
        )
        if isinstance(existing_lighting, dict):
            base_lighting = existing_lighting
        else:
            base_lighting = get_default_vrm_lighting()
        
        if not isinstance(lighting, dict):
            return JSONResponse(content={
                'success': False,
                'error': 'lighting 必须是对象'
            }, status_code=400)
        
        lighting = {**base_lighting, **lighting}

        from config import VRM_LIGHTING_RANGES
        lighting_ranges = VRM_LIGHTING_RANGES

        for key, (min_val, max_val) in lighting_ranges.items():
            if key not in lighting:
                return JSONResponse(content={
                    'success': False,
                    'error': f'缺少打光参数: {key}'
                }, status_code=400)

            val = lighting[key]
            if not isinstance(val, (int, float)) or not (min_val <= val <= max_val):
                return JSONResponse(content={
                    'success': False,
                    'error': f'打光参数 {key} 超出范围 ({min_val}-{max_val})'
                }, status_code=400)

        
        set_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            {key: float(lighting[key]) for key in lighting_ranges.keys()},
        )



        logger.info(
            "已保存角色 %s 的打光配置: %s",
            name,
            get_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'lighting', default=None),
        )

        await _config_manager.asave_characters(characters)
        
        if apply_runtime:
            initialize_character_data = get_initialize_character_data()
            if initialize_character_data:
                await initialize_character_data()
                logger.info(f"已执行完整配置重载（角色 {name} 的打光配置）")
        else:
            logger.debug("跳过完整配置重载（apply_runtime=False），配置已保存到磁盘，需要刷新页面或调用重载才能生效")

        if apply_runtime:
            message = f'已保存角色 {name} 的打光配置并已应用到运行时'
        else:
            message = f'已保存角色 {name} 的打光配置到磁盘（需要刷新页面或调用重载才能生效）'

        return JSONResponse(content={
            'success': True,
            'message': message,
            'applied_runtime': apply_runtime,
            'needs_reload': not apply_runtime
        })

    except Exception as e:
        logger.error(f"保存打光配置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/mmd_settings')
async def update_catgirl_mmd_settings(name: str, request: Request):
    """更新指定角色的MMD模型设置（光照、渲染、物理、鼠标跟踪）"""
    def _to_bool(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ('true', '1', 'yes')
        return bool(val)

    try:
        data = await request.json()

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import (
            get_default_mmd_settings,
            MMD_LIGHTING_RANGES,
            MMD_RENDERING_RANGES,
            MMD_PHYSICS_RANGES,
            MMD_CURSOR_FOLLOW_RANGES,
        )

        defaults = get_default_mmd_settings()

        # --- 光照 ---
        if 'lighting' in data and isinstance(data['lighting'], dict):
            lighting = {**defaults['lighting'], **data['lighting']}
            for key, (min_val, max_val) in MMD_LIGHTING_RANGES.items():
                if key in lighting:
                    val = lighting[key]
                    if isinstance(val, (int, float)):
                        lighting[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', lighting)

        # --- 渲染 ---
        if 'rendering' in data and isinstance(data['rendering'], dict):
            rendering = {**defaults['rendering'], **data['rendering']}
            for key, (min_val, max_val) in MMD_RENDERING_RANGES.items():
                if key in rendering:
                    val = rendering[key]
                    if isinstance(val, (int, float)):
                        rendering[key] = max(min_val, min(max_val, float(val)))
            if 'outline' in rendering:
                rendering['outline'] = _to_bool(rendering['outline'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', rendering)

        # --- 物理 ---
        if 'physics' in data and isinstance(data['physics'], dict):
            physics = {**defaults['physics'], **data['physics']}
            if 'enabled' in physics:
                physics['enabled'] = _to_bool(physics['enabled'])
            for key, (min_val, max_val) in MMD_PHYSICS_RANGES.items():
                if key in physics:
                    val = physics[key]
                    if isinstance(val, (int, float)):
                        physics[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', physics)

        # --- 鼠标跟踪 ---
        # 前端发送 camelCase（cursorFollow），兼容 snake_case（cursor_follow）
        cursor_follow_data = data.get('cursorFollow') or data.get('cursor_follow')
        if cursor_follow_data and isinstance(cursor_follow_data, dict):
            cursor_follow = {**defaults['cursor_follow'], **cursor_follow_data}
            for key, (min_val, max_val) in MMD_CURSOR_FOLLOW_RANGES.items():
                if key in cursor_follow:
                    val = cursor_follow[key]
                    if isinstance(val, (int, float)):
                        cursor_follow[key] = max(min_val, min(max_val, float(val)))
            if 'enabled' in cursor_follow:
                cursor_follow['enabled'] = _to_bool(cursor_follow['enabled'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', cursor_follow)

        await _config_manager.asave_characters(characters)

        logger.info("已保存角色 %s 的MMD模型设置", name)
        return JSONResponse(content={
            'success': True,
            'message': f'已保存角色 {name} 的MMD模型设置'
        })

    except Exception as e:
        logger.error(f"保存MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.get('/catgirl/{name}/mmd_settings')
async def get_catgirl_mmd_settings(name: str):
    """获取指定角色的MMD模型设置"""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import get_default_mmd_settings
        defaults = get_default_mmd_settings()

        lighting = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', default=None)
        rendering = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', default=None)
        physics = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', default=None)
        cursor_follow = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', default=None)

        return JSONResponse(content={
            'success': True,
            'settings': {
                'lighting': lighting if isinstance(lighting, dict) else defaults['lighting'],
                'rendering': rendering if isinstance(rendering, dict) else defaults['rendering'],
                'physics': physics if isinstance(physics, dict) else defaults['physics'],
                # 使用 camelCase 与前端保持一致
                'cursorFollow': cursor_follow if isinstance(cursor_follow, dict) else defaults['cursor_follow'],
            }
        })

    except Exception as e:
        logger.error(f"获取MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    if 'voice_id' not in data:
        logger.debug("猫娘 %s 的 voice_id 更新请求缺少字段，按无变更处理", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    voice_id = str(data.get('voice_id') or '').strip()
    old_voice_id = str(get_reserved(
        characters['猫娘'][name],
        'voice_id',
        default='',
        legacy_keys=('voice_id',)
    ) or '').strip()

    # 幂等保护：提交同值时直接返回，避免无实际变更触发 reload_page。
    if old_voice_id == voice_id:
        logger.info("猫娘 %s 的 voice_id 未变化，跳过刷新流程", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}

    # 验证voice_id是否在voice_storage中
    if not _config_manager.validate_voice_id(voice_id):
        voices = _config_manager.get_voices_for_current_api()
        available_voices = list(voices.keys())
        return JSONResponse({
            'success': False,
            'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
            'available_voices': available_voices
        }, status_code=400)

    set_reserved(characters['猫娘'][name], 'voice_id', voice_id)
    await _config_manager.asave_characters(characters)
    
    # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新（{old_voice_id} -> {voice_id}），准备刷新...")
            
            # 1. 先发送刷新消息（WebSocket还连着）
            await send_reload_page_notice(session_manager[name])
            
            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    # 方案3：条件性重新加载 - 只有当前猫娘才重新加载配置
    if is_current_catgirl:
        # 3. 重新加载配置，让新的voice_id生效
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        logger.info("配置已重新加载，新的voice_id已生效")
    else:
        # 不是当前猫娘，跳过重新加载，避免影响当前猫娘的session
        logger.info(f"切换的是其他猫娘 {name} 的音色，跳过重新加载以避免影响当前猫娘的session")
    
    return {"success": True, "session_restarted": session_ended, "voice_id_changed": True}

@router.get('/catgirl/{name}/voice_mode_status')
async def get_catgirl_voice_mode_status(name: str):
    """检查指定角色是否在语音模式下"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    is_current = characters.get('当前猫娘') == name
    
    if name not in session_manager:
        return JSONResponse({'is_voice_mode': False, 'is_current': is_current, 'is_active': False})
    
    mgr = session_manager[name]
    is_active = mgr.is_active if mgr else False
    
    is_voice_mode = False
    if is_active and mgr:
        # 检查是否是语音模式（通过session类型判断）
        from main_logic.omni_realtime_client import OmniRealtimeClient
        is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
    
    return JSONResponse({
        'is_voice_mode': is_voice_mode,
        'is_current': is_current,
        'is_active': is_active
    })


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
            # 如果当前猫娘是被重命名的猫娘，也需要更新
            if is_current_catgirl:
                characters['当前猫娘'] = new_name
            await _config_manager.asave_characters(characters)

            # 自动重新加载配置
            initialize_character_data = get_initialize_character_data()
            await initialize_character_data()

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

    # 数据更新+重载+卡面迁移完成后再通知前端
    if memory_server_reloaded and rename_notification_ws and rename_notification_message:
        try:
            await rename_notification_ws.send_text(rename_notification_message)
            logger.info(f"已向 {old_name} 发送重命名通知")
        except Exception as e:
            logger.warning(f"发送重命名通知给 {old_name} 失败: {e}")

    return {
        "success": True,
        "memory_renamed": True,
        "memory_server_reloaded": memory_server_reloaded,
    }


@router.post('/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """解除猫娘的声音注册"""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        characters = await _config_manager.aload_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
        
        # 检查是否已有voice_id
        old_voice_id = get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))
        if not old_voice_id:
            return JSONResponse({'success': False, 'error': 'TTS_VOICE_NOT_REGISTERED', 'code': 'TTS_VOICE_NOT_REGISTERED'}, status_code=400)
        
        # COMPAT(v1->v2): 统一落到 _reserved.voice_id，旧平铺 voice_id 不再写入/删除。
        set_reserved(characters['猫娘'][name], 'voice_id', '')
        await _config_manager.asave_characters(characters)

        # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        session_ended = False

        if is_current_catgirl and name in session_manager:
            if session_manager[name].is_active:
                logger.info(f"检测到 {name} 的voice_id已清空（{old_voice_id} -> ''），准备刷新...")
                await send_reload_page_notice(session_manager[name], "音色已清除，页面即将刷新")
                try:
                    await session_manager[name].end_session(by_server=True)
                    session_ended = True
                    logger.info(f"{name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束session时出错: {e}")

        # 自动重新加载配置
        if is_current_catgirl:
            initialize_character_data = get_initialize_character_data()
            await initialize_character_data()
        
        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除", "session_restarted": session_ended, "voice_id_changed": True}
        
    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)

@router.get('/current_catgirl')
async def get_current_catgirl():
    """获取当前使用的猫娘名称"""
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return _json_no_store_response({'current_catgirl': current_catgirl})

@router.post('/current_catgirl')
async def set_current_catgirl(request: Request):
    """设置当前使用的猫娘"""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''
    
    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    
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
    characters['当前猫娘'] = catgirl_name
    await _config_manager.asave_characters(characters)
    # Fast path：切换只改变 `当前猫娘` 字段，per-k 的 prompt / voice_id / thread 都不变，
    # 只需刷新 globals 即可。N=20 只猫娘时从 O(N) 降到 O(1)。
    switch_current_catgirl_fast = get_switch_current_catgirl_fast()
    await switch_current_catgirl_fast()
    
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


@router.post('/reload')
async def reload_character_config():
    """重新加载角色配置（热重载）"""
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
    if not data:
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    profile_name = data.get('档案名')
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    data['档案名'] = str(profile_name).strip()
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    characters = await _config_manager.aload_characters()
    characters['主人'] = {k: v for k, v in data.items() if v}
    await _config_manager.asave_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}


@router.post('/master/{old_name}/rename')
async def rename_master(old_name: str, request: Request):
    """重命名主人档案"""
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
            return JSONResponse({'success': False, 'error': '主人档案不存在'}, status_code=404)

        current_master = characters['主人'].get('档案名', '')
        if current_master != old_name:
            return JSONResponse({'success': False, 'error': '原主人档案名不匹配'}, status_code=400)

        if new_name in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '新档案名与已有猫娘名称冲突'}, status_code=400)

        characters['主人']['档案名'] = new_name
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
    await _config_manager.asave_characters(characters)
    # Fast path：新增只需为 `key` 这一个 catgirl 分配资源 + 启动线程，不影响其它角色。
    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(key, is_new=True)

    memory_server_reloaded = await notify_memory_server_reload(reason=f"新角色: {key}")

    return {
        "success": True,
        "character_name": key,
        "memory_server_reloaded": memory_server_reloaded,
    }


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
        if requested_model_type and requested_model_type not in ('live2d', 'live3d'):
            return JSONResponse(
                {'success': False, 'error': f'无效的模型类型: {requested_model_type}，只允许 live2d 或 live3d'},
                status_code=400,
            )

    data = _filter_mutable_catgirl_fields(raw_data)
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    old_voice_id = get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))

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

    # 兼容旧接口：若请求中带有 voice_id，则同步写入保留字段。
    if voice_id_in_payload:
        set_reserved(characters['猫娘'][name], 'voice_id', requested_voice_id)

    # 兼容前端自动修复：若请求中带有 model_type，则同步写入保留字段。
    if model_type_in_payload and requested_model_type:
        set_reserved(characters['猫娘'][name], 'avatar', 'model_type', requested_model_type)

    await _config_manager.asave_characters(characters)

    new_voice_id = get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))
    voice_id_changed = voice_id_in_payload and old_voice_id != new_voice_id

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
    if voice_id_changed:
        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))

        # 如果是当前活跃的猫娘，需要先通知前端，再关闭 session
        if is_current_catgirl and name in session_manager and session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已变更（{old_voice_id} -> {new_voice_id}），准备刷新...")
            await send_reload_page_notice(session_manager[name])
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")

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
        "voice_id_changed": voice_id_changed,
        "session_restarted": session_ended,
        "ignored_reserved_fields": ignored_reserved_fields
    }


@router.delete('/catgirl/{name}')
async def delete_catgirl(name: str):
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 检查是否是当前正在使用的猫娘
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！请先切换到其他猫娘后再删除。'}, status_code=400)

    assert_cloudsave_writable(
        _config_manager,
        operation="delete",
        target=f"characters/{name}",
    )

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

    return {"success": True, "memory_server_reloaded": memory_server_reloaded}

@router.post('/clear_voice_ids')
async def clear_voice_ids():
    """清除所有角色的本地Voice ID记录"""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        cleared_count = 0
        
        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)):
                    set_reserved(characters['猫娘'][name], 'voice_id', '')
                    cleared_count += 1
        
        await _config_manager.asave_characters(characters)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        return JSONResponse({
            'success': True, 
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False, 
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)


@router.get('/custom_tts_voices')
async def list_custom_tts_voices_for_characters():
    """获取自定义 TTS 可用声音列表（用于角色管理页面的音色选择）。

    当前由适配层处理 GPT-SoVITS provider 的路径映射与 voice_id 前缀规则。
    """
    try:
        _config_manager = get_config_manager()
        
        # 使用与 gptsovits_tts_worker 相同的配置解析路径，确保 URL 一致
        tts_config = _config_manager.get_model_api_config('tts_custom')
        base_url = (tts_config.get('base_url') or '').rstrip('/')
        if not base_url or not (base_url.startswith('http://') or base_url.startswith('https://')):
            return JSONResponse({
                'success': False,
                'error': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'code': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'voices': []
            }, status_code=400)
        
        # SSRF 防护：GPT-SoVITS 仅限 localhost
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(base_url)
        host = parsed.hostname or ''
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)
        except ValueError:
            if host not in ('localhost',):
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)
        
        # 通过适配层获取并标准化自定义 TTS voices
        voices = await get_custom_tts_voices(base_url, provider='gptsovits')
        
        return JSONResponse({
            'success': True,
            'voices': voices,
            'api_url': base_url
        })
    except (CustomTTSVoiceFetchError, ValueError) as e:
        return JSONResponse({
            'success': False,
            'error': f'连接 GPT-SoVITS API 失败: {str(e)}',
            'voices': []
        }, status_code=502)
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'获取 GPT-SoVITS 声音列表失败: {str(e)}',
            'voices': []
        }, status_code=500)


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
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
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


@router.get('/voices')
async def get_voices():
    """获取当前API key对应的所有已注册音色"""
    _config_manager = get_config_manager()
    result = {"voices": _config_manager.get_voices_for_current_api()}
    
    core_config = await _config_manager.aget_core_config()
    if core_config.get('IS_FREE_VERSION'):
        core_url = core_config.get('CORE_URL', '')
        openrouter_url = core_config.get('OPENROUTER_URL', '')
        if 'lanlan.tech' in core_url or 'lanlan.tech' in openrouter_url:
            from utils.api_config_loader import get_free_voices
            free_voices = get_free_voices()
            if free_voices:
                result["free_voices"] = free_voices
    
    # 构建 voice_id → 使用该音色的角色名列表，用于前端显示
    characters = await _config_manager.aload_characters()
    voice_owners = {}
    for catgirl_name, catgirl_config in characters.get('猫娘', {}).items():
        if not isinstance(catgirl_config, dict):
            logger.warning(f"角色配置格式异常，已跳过 voice_owners 统计: {catgirl_name}")
            continue
        vid = get_reserved(catgirl_config, 'voice_id', default='', legacy_keys=('voice_id',))
        if vid:
            voice_owners.setdefault(vid, []).append(catgirl_name)
    result["voice_owners"] = voice_owners
    
    return result


@router.get('/voice_preview')
async def get_voice_preview(voice_id: str):
    """获取音色预览音频"""
    try:
        _config_manager = get_config_manager()
        voices = _config_manager.get_voices_for_current_api()
        voice_data = voices.get(voice_id) if isinstance(voices, dict) else None
        provider = (voice_data or {}).get('provider', '')

        # 优先尝试从 tts_custom 获取 API Key
        try:
            tts_custom_config = _config_manager.get_model_api_config('tts_custom')
            audio_api_key = tts_custom_config.get('api_key', '')
        except Exception:
            audio_api_key = ''

        # 如果没有，则回退到核心配置
        # Codex review: 原先这里顶上还有一个 `core_config = ...`，从未被读取（死代码）。
        # 全仓 async 化时把死读也改成了 await，反而白跑一次 IO，删。
        if not audio_api_key:
            core_config = await _config_manager.aget_core_config()
            audio_api_key = core_config.get('AUDIO_API_KEY', '')

        logger.info(f"正在为音色 {voice_id} 生成预览音频...")
        
        text = "喵喵喵～这里是neko～很高兴见到你～"
        if provider in ('minimax', 'minimax_intl'):
            minimax_api_key = _config_manager.get_tts_api_key(provider)
            if not minimax_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'MINIMAX_API_KEY_MISSING',
                    'code': 'MINIMAX_API_KEY_MISSING'
                }, status_code=400)

            minimax_base_url = (voice_data or {}).get('minimax_base_url') or get_minimax_base_url(provider)
            provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

            try:
                minimax_client = MinimaxVoiceCloneClient(api_key=minimax_api_key, base_url=minimax_base_url)
                audio_data = await minimax_client.synthesize_preview(voice_id=voice_id, text=text)
                logger.info(f"{provider_label} 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                }
            except MinimaxVoiceCloneError as e:
                logger.error(f"{provider_label} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'{provider_label}预览生成失败: {str(e)}'
                }, status_code=500)

        if not audio_api_key:
            return JSONResponse({'success': False, 'error': 'TTS_AUDIO_API_KEY_MISSING', 'code': 'TTS_AUDIO_API_KEY_MISSING'}, status_code=400)

        # 生成音频
        dashscope.api_key = audio_api_key
        try:
            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = (voice_data or {}).get('clone_model') or get_cosyvoice_clone_model()
            synthesizer = SpeechSynthesizer(model=clone_model, voice=voice_id)
            # 使用 asyncio.to_thread 包装同步阻塞调用
            audio_data = await asyncio.to_thread(lambda: synthesizer.call(text))
            
            if not audio_data:
                request_id = getattr(synthesizer, 'get_last_request_id', lambda: 'unknown')()
                logger.error(f"生成音频失败: audio_data 为空. Request ID: {request_id}")
                return JSONResponse({
                    'success': False, 
                    'error': f'生成音频失败 (Request ID: {request_id})。请检查 API Key 额度或音色 ID 是否有效。'
                }, status_code=500)
                
            logger.info(f"音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                
            # 将音频数据转换为 Base64 字符串
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                
            return {
                "success": True, 
                "audio": audio_base64,
                "mime_type": "audio/mpeg"
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"SpeechSynthesizer 调用异常: {error_msg}")
            return JSONResponse({
                'success': False, 
                'error': f'语音合成异常: {error_msg}'
            }, status_code=500)
    except Exception as e:
        logger.error(f"生成音色预览失败: {e}")
        return JSONResponse({'success': False, 'error': f'系统错误: {str(e)}'}, status_code=500)


@router.post('/voices')
async def register_voice(request: Request):
    """注册新音色"""
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')
        
        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': 'TTS_VOICE_REGISTER_MISSING_PARAMS',
                'code': 'TTS_VOICE_REGISTER_MISSING_PARAMS'
            }, status_code=400)
        
        # 准备音色数据
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }
        
        try:
            _config_manager = get_config_manager()
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            logger.warning(f"保存音色配置失败: {e}")
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)
            
        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.delete('/voices/{voice_id}')
async def delete_voice(voice_id: str):
    """删除指定音色"""
    try:
        _config_manager = get_config_manager()
        deleted = _config_manager.delete_voice_for_current_api(voice_id)
        
        if deleted:
            # 清理所有角色中使用该音色的引用
            _config_manager = get_config_manager()
            session_manager = get_session_manager()
            characters = await _config_manager.aload_characters()
            cleaned_count = 0
            affected_active_names = []
            
            if '猫娘' in characters:
                for name in characters['猫娘']:
                    if get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)) == voice_id:
                        set_reserved(characters['猫娘'][name], 'voice_id', '')
                        cleaned_count += 1
                        
                        # 检查该角色是否是当前活跃的 session
                        if name in session_manager and session_manager[name].is_active:
                            affected_active_names.append(name)
            
            if cleaned_count > 0:
                await _config_manager.asave_characters(characters)
                
                # 对于受影响的活跃角色，并行通知 + 结束 session（每个 end_session ≈ 1s）
                async def _refresh_one(name):
                    logger.info(f"检测到活跃角色 {name} 的 voice_id 已被删除，准备刷新...")
                    # 1. 发送刷新通知
                    await send_reload_page_notice(session_manager[name], "音色已删除，页面即将刷新")
                    # 2. 结束 session
                    try:
                        await session_manager[name].end_session(by_server=True)
                        logger.info(f"已结束受影响角色 {name} 的 session")
                    except Exception as e:
                        logger.error(f"结束受影响角色 {name} 的 session 时出错: {e}")

                if affected_active_names:
                    await asyncio.gather(
                        *(_refresh_one(name) for name in affected_active_names),
                        return_exceptions=True,
                    )

                # 自动重新加载配置
                initialize_character_data = get_initialize_character_data()
                await initialize_character_data()
            
            logger.info(f"已删除音色 '{voice_id}'，并清理了 {cleaned_count} 个角色的引用")
            return {
                "success": True,
                "message": f"音色已删除，已清理 {cleaned_count} 个角色的引用"
            }
        else:
            return JSONResponse({
                'success': False,
                'error': '音色不存在或删除失败'
            }, status_code=404)
    except Exception as e:
        logger.error(f"删除音色时出错: {e}")
        return JSONResponse({
            'success': False,
            'error': f'删除音色失败: {str(e)}'
        }, status_code=500)


# ==================== 智能静音移除 ====================
# 用于存储裁剪任务状态的全局字典
_trim_tasks: dict[str, dict] = {}

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_CARD_FACE_SIZE = 10 * 1024 * 1024  # 10 MB


class _UploadTooLargeError(Exception):
    """上传文件大小超过限制"""


async def _read_limited_stream(stream: UploadFile, max_size: int) -> io.BytesIO:
    """读取上传文件并检查大小限制，返回 BytesIO (positioned at 0)。

    Raises:
        _UploadTooLargeError: 文件大小超过 max_size。
    """
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise _UploadTooLargeError(
                f'文件大小超过限制 ({max_size // (1024 * 1024)} MB)'
            )
        buf.write(chunk)
    buf.seek(0)
    return buf


@router.post('/audio/analyze_silence')
async def analyze_silence(file: UploadFile = File(...)):
    """
    分析上传音频中的静音段落。

    返回:
        - original_duration / original_duration_ms: 原始音频总时长
        - silence_duration / silence_duration_ms: 检测到的静音总时长 (total_silence_ms)
        - removable_silence / removable_silence_ms: 实际可移除的静音时长
        - estimated_duration / estimated_duration_ms: 处理后预计剩余时长
        - saving_percentage: 节省百分比 (基于实际可移除量)
        - silence_segments: 静音段列表 [{start_ms, end_ms, duration_ms}]
        - has_silence: 是否检测到可移除静音
    """
    from utils.audio_silence_remover import (
        detect_silence, convert_to_wav_if_needed, format_duration_mmss
    )

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # 转换为 WAV（如果需要）— 阻塞操作，放到线程中执行
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # 执行静音检测
        analysis = await asyncio.to_thread(detect_silence, wav_buffer)

        return JSONResponse({
            'success': True,
            'original_duration': format_duration_mmss(analysis.original_duration_ms),
            'original_duration_ms': round(analysis.original_duration_ms, 1),
            'silence_duration': format_duration_mmss(analysis.total_silence_ms),
            'silence_duration_ms': round(analysis.total_silence_ms, 1),
            'removable_silence': format_duration_mmss(analysis.removable_silence_ms),
            'removable_silence_ms': round(analysis.removable_silence_ms, 1),
            'estimated_duration': format_duration_mmss(analysis.estimated_duration_ms),
            'estimated_duration_ms': round(analysis.estimated_duration_ms, 1),
            'saving_percentage': analysis.saving_percentage,
            'silence_segments': [
                {
                    'start_ms': round(s.start_ms, 1),
                    'end_ms': round(s.end_ms, 1),
                    'duration_ms': round(s.duration_ms, 1),
                }
                for s in analysis.silence_segments
            ],
            'has_silence': len(analysis.silence_segments) > 0,
            'sample_rate': analysis.sample_rate,
            'sample_width': analysis.sample_width,
            'channels': analysis.channels,
        })
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"静音分析失败: {e}")
        return JSONResponse({'error': f'静音分析失败: {str(e)}'}, status_code=500)


@router.post('/audio/trim_silence')
async def trim_silence_endpoint(file: UploadFile = File(...), task_id: str | None = Form(default=None)):
    """
    执行静音裁剪并返回处理后的音频。

    先分析静音段，然后将超长静音缩减至 200ms（从正中间裁剪）。
    返回处理后的 WAV 文件 (base64 编码) 以及 MD5 校验值。
    """
    import uuid
    import base64 as b64
    from utils.audio_silence_remover import (
        detect_silence, trim_silence, convert_to_wav_if_needed,
        format_duration_mmss, CancelledError
    )

    if task_id:
        try:
            uuid.UUID(task_id)
        except ValueError:
            return JSONResponse({'error': '无效的 task_id 格式'}, status_code=400)
        if task_id in _trim_tasks:
            return JSONResponse({'error': '该 task_id 已存在'}, status_code=409)
    else:
        task_id = str(uuid.uuid4())

    # 立即占位，防止 TOCTOU 竞态
    _trim_tasks[task_id] = {'progress': 0, 'cancelled': False, 'phase': 'queued'}

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # 文件读取完成，切换到分析阶段
        _trim_tasks[task_id]['phase'] = 'analyzing'

        def progress_cb(pct: int):
            task = _trim_tasks.get(task_id)
            if task is None:
                return
            if task.get('phase', 'analyzing') == 'analyzing':
                # 分析阶段占 0-40%
                task['progress'] = int(pct * 0.4)
            else:
                # 裁剪阶段占 40-100%
                task['progress'] = 40 + int(pct * 0.6)

        def cancel_check() -> bool:
            return _trim_tasks.get(task_id, {}).get('cancelled', False)

        # 转换为 WAV — 阻塞操作，放到线程中执行
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # 分析静音
        analysis = await asyncio.to_thread(
            detect_silence, wav_buffer,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        if not analysis.silence_segments:
            # 没有可移除的静音
            _trim_tasks.pop(task_id, None)
            return JSONResponse({
                'success': True,
                'has_changes': False,
                'message': '未检测到可移除的静音段',
                'task_id': task_id,
            })

        # 切换到裁剪阶段
        if task_id in _trim_tasks:
            _trim_tasks[task_id]['phase'] = 'trimming'

        # 执行裁剪
        result = await asyncio.to_thread(
            trim_silence, wav_buffer, analysis,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        # 编码为 base64
        audio_b64 = b64.b64encode(result.audio_data).decode('ascii')

        # 清理任务
        _trim_tasks.pop(task_id, None)

        return JSONResponse({
            'success': True,
            'has_changes': True,
            'task_id': task_id,
            'audio_base64': audio_b64,
            'md5': result.md5,
            'original_duration': format_duration_mmss(result.original_duration_ms),
            'original_duration_ms': round(result.original_duration_ms, 1),
            'trimmed_duration': format_duration_mmss(result.trimmed_duration_ms),
            'trimmed_duration_ms': round(result.trimmed_duration_ms, 1),
            'removed_silence_ms': round(result.removed_silence_ms, 1),
            'sample_rate': result.sample_rate,
            'sample_width': result.sample_width,
            'channels': result.channels,
            'filename': f"trimmed_{file.filename}",
        })

    except CancelledError:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({
            'success': False,
            'cancelled': True,
            'message': '任务已被用户取消',
            'task_id': task_id,
        })
    except ValueError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"静音裁剪失败: {e}")
        return JSONResponse({'error': f'静音裁剪失败: {str(e)}'}, status_code=500)


@router.get('/audio/trim_progress/{task_id}')
async def get_trim_progress(task_id: str):
    """获取裁剪任务进度"""
    task = _trim_tasks.get(task_id)
    if not task:
        return JSONResponse({'exists': False, 'progress': 100, 'phase': 'done'})
    return JSONResponse({
        'exists': True,
        'progress': task.get('progress', 0),
        'phase': task.get('phase', 'unknown'),
        'cancelled': task.get('cancelled', False),
    })


@router.post('/audio/trim_cancel/{task_id}')
async def cancel_trim_task(task_id: str):
    """取消裁剪任务"""
    task = _trim_tasks.get(task_id)
    if task:
        task['cancelled'] = True
        return JSONResponse({'success': True, 'message': '取消请求已发送'})
    return JSONResponse({'success': False, 'message': '任务不存在或已完成'})


@router.post('/voice_clone')
async def voice_clone(
    file: UploadFile = File(...),
    prefix: str = Form(...),
    ref_language: str = Form(default="ch"),
    provider: str = Form(default="cosyvoice"),
):
    """
    语音克隆接口
    
    参数:
        file: 音频文件
        prefix: 音色前缀名
        ref_language: 参考音频的语言，可选值：ch, en, fr, de, ja, ko, ru
                      注意：这是参考音频的语言，不是目标语音的语言
        provider: 服务商，可选值：cosyvoice (阿里云), minimax (国服), minimax_intl (国际服)
    """
    # 流式读取上传文件（带大小限制）并增量计算 MD5
    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取文件到内存失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    audio_md5 = hashlib.md5(file_buffer.getvalue()).hexdigest()
    
    # 提前规范化 provider 和 ref_language
    provider = provider.lower().strip() if provider else 'cosyvoice'
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    ref_language = ref_language.lower().strip() if ref_language else 'ch'
    if ref_language not in valid_languages:
        ref_language = 'ch'
    
    # 检测是否使用本地 TTS（ws/wss 协议）
    _config_manager = get_config_manager()
    tts_config = _config_manager.get_model_api_config('tts_custom')
    base_url = tts_config.get('base_url', '')
    is_local_tts = tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://'))
    
    if is_local_tts:
        # ==================== 本地 TTS 注册流程 ====================
        # MD5 + ref_language 去重：检查是否已有相同音频 + 相同语言注册过的音色
        existing = _config_manager.find_voice_by_audio_md5('__LOCAL_TTS__', audio_md5, ref_language)
        if existing:
            voice_id, voice_data = existing
            logger.info(f"本地 TTS 音频 MD5 命中，复用 voice_id: {voice_id}")
            return JSONResponse({
                'voice_id': voice_id,
                'message': '已复用现有音色，跳过上传',
                'reused': True,
                'is_local': True
            })
        
        # 将 ws(s):// 转换为 http(s):// 用于 REST API 调用
        if base_url.startswith('wss://'):
            http_base = 'https://' + base_url[6:]
        else:
            http_base = 'http://' + base_url[5:]
        
        # 移除可能的 /v1/audio/speech/stream 路径，只保留主机部分
        # 例如: ws://127.0.0.1:50000/v1/audio/speech/stream -> http://127.0.0.1:50000
        if '/v1/' in http_base:
            http_base = http_base.split('/v1/')[0]
        
        register_url = f"{http_base}/v1/speakers/register"
        logger.info(f"使用本地 TTS 注册: {register_url}")
        
        try:
            file_buffer.seek(0)
            
            # 根据用户 demo，API 格式：
            # POST /v1/speakers/register
            # multipart/form-data: speaker_id, prompt_text, prompt_audio
            files = {
                'prompt_audio': (file.filename, file_buffer, 'audio/wav')
            }
            data = {
                'speaker_id': prefix,
                'prompt_text': f"<|{ref_language}|>" if ref_language != 'ch' else "希望你以后能够做的比我还好呦。"
            }

            # per-call AsyncClient: 用户手动上传音色文件触发，冷路径
            async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
                resp = await client.post(register_url, data=data, files=files)
                
                if resp.status_code == 200:
                    result = resp.json()
                    voice_id = prefix  # 本地 TTS 使用 speaker_id 作为 voice_id
                    
                    # 保存到本地音色库（使用特殊的 key 标识本地 TTS）
                    voice_data = {
                        'voice_id': voice_id,
                        'prefix': prefix,
                        'provider': 'local',
                        'is_local': True,
                        'audio_md5': audio_md5,
                        'ref_language': ref_language,
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        local_tts_key = '__LOCAL_TTS__'
                        _config_manager.save_voice_for_api_key(local_tts_key, voice_id, voice_data)
                        logger.info(f"本地 TTS voice_id 已保存: {voice_id}")
                    except Exception as save_error:
                        logger.warning(f"保存 voice_id 到音色库失败（本地 TTS 仍可用）: {save_error}")
                    
                    return JSONResponse({
                        'voice_id': voice_id,
                        'message': result.get('message', '本地音色注册成功'),
                        'is_local': True
                    })
                else:
                    error_text = resp.text
                    logger.error(f"本地 TTS 注册失败: {error_text}")
                    return JSONResponse({
                        'error': f'本地 TTS 注册失败: {error_text[:200]}'
                    }, status_code=resp.status_code)
                    
        except httpx.ConnectError as e:
            logger.error(f"无法连接本地 TTS 服务器: {e}")
            return JSONResponse({
                'error': f'无法连接本地 TTS 服务器: {http_base}，请确保服务器已启动'
            }, status_code=503)
        except Exception as e:
            logger.error(f"本地 TTS 注册时发生错误: {e}")
            return JSONResponse({
                'error': f'本地 TTS 注册失败: {str(e)}'
            }, status_code=500)
    
    # ==================== 云端语音克隆：按 provider 对偶分支 ====================

    # 统一通过 config_manager 获取 API Key
    api_key = _config_manager.get_tts_api_key(provider)

    if provider in ('minimax', 'minimax_intl'):
        # ---------- MiniMax（国服 / 国际服）----------
        if not api_key:
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

    elif provider == 'cosyvoice':
        # ---------- 阿里云 CosyVoice ----------
        if not api_key:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)
        base_url = None
        storage_key = api_key
        provider_label = '阿里云CosyVoice'

    else:
        return JSONResponse({'error': f'不支持的 provider: {provider}'}, status_code=400)

    # ---------- 公共流程：MD5 去重 ----------
    existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
    if existing:
        voice_id, voice_data = existing
        logger.info(f"{provider_label} 音频 MD5 命中，复用 voice_id: {voice_id}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'已复用现有{provider_label}音色，跳过上传',
            'reused': True,
            'provider': provider
        })

    # ---------- 公共流程：音频规范化 ----------
    try:
        if provider == 'cosyvoice':
            mime_type, error_msg = validate_audio_file(file_buffer, file.filename)
            if not mime_type:
                return JSONResponse({'error': error_msg}, status_code=400)
        normalized_buffer, normalized_filename, audio_meta = await asyncio.to_thread(
            normalize_voice_clone_api_audio,
            file_buffer,
            file.filename or 'prompt_audio.wav',
        )
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    logger.info(
        "%s 语音克隆参考音频已规范化: %sHz/%sch -> %sHz/mono",
        provider_label,
        audio_meta['original']['sample_rate'],
        audio_meta['original']['channels'],
        audio_meta['normalized']['sample_rate'],
    )

    # ---------- 按 provider 调用对应克隆 API ----------
    try:
        if provider in ('minimax', 'minimax_intl'):
            original_prefix, minimax_prefix = _build_minimax_request_prefix(prefix, provider_label)

            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)
            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # 保存原始前缀用于显示
                'minimax_prefix': minimax_prefix,  # 保存实际提交给 MiniMax 的安全前缀
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat()
            }

        else:  # cosyvoice
            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = get_cosyvoice_clone_model()
            language_hints = qwen_language_hints(ref_language)
            client = QwenVoiceCloneClient(api_key=api_key, tflink_upload_url=TFLINK_UPLOAD_URL)
            voice_id, tmp_url, _request_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=prefix,
                language_hints=language_hints,
                target_model=clone_model,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': tmp_url,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'cosyvoice',
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat()
            }

        logger.info(f"{provider_label} 音色注册成功，voice_id: {voice_id}")

    except (MinimaxVoiceCloneError, QwenVoiceCloneError) as e:
        logger.error(f"{provider_label} 音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        return JSONResponse({'error': f'{provider_label}音色注册失败: {error_detail}', 'provider': provider}, status_code=500)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"{provider_label} 音色注册时发生错误: {e}")
        return JSONResponse({'error': f'{provider_label}音色注册失败: {str(e)}', 'provider': provider}, status_code=500)

    # ---------- 公共流程：保存到本地音色库 ----------
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} voice_id 到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}音色注册成功并已保存到音色库',
        'provider': provider,
    })


@router.post('/voice_clone_direct')
async def voice_clone_direct(request: Request):
    """
    直链语音克隆接口 - 跳过音频上传步骤，直接使用提供的直链URL注册音色
    
    支持 CosyVoice 和 MiniMax 服务商：
    - CosyVoice: 直接使用直链URL注册音色
    - MiniMax: 先下载音频文件，再上传到MiniMax服务器注册音色
    
    请求体:
        {
            "direct_link": "https://example.com/audio.wav",  // 音频直链URL
            "prefix": "custom_prefix",                        // 音色前缀名
            "ref_language": "ch",                             // 参考音频语言
            "provider": "cosyvoice"                           // 服务商：cosyvoice / minimax / minimax_intl
        }
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({'error': f'请求体解析失败: {e}'}, status_code=400)

    direct_link = data.get('direct_link', '').strip()
    prefix = data.get('prefix', '').strip()
    ref_language = data.get('ref_language', 'ch').lower().strip()
    provider = data.get('provider', 'cosyvoice').lower().strip()

    # 参数验证
    if not direct_link:
        return JSONResponse({'error': '缺少 direct_link 参数'}, status_code=400)
    if not prefix:
        return JSONResponse({'error': '缺少 prefix 参数'}, status_code=400)
    if not direct_link.startswith(('http://', 'https://')):
        return JSONResponse({'error': 'direct_link 必须是有效的HTTP/HTTPS链接'}, status_code=400)

    # SSRF防护：验证直链域名不是内网IP
    try:
        from urllib.parse import urlparse
        import socket
        import ipaddress
        
        parsed_url = urlparse(direct_link)
        hostname = parsed_url.hostname
        
        if not hostname:
            return JSONResponse({'error': '无法解析直链域名'}, status_code=400)
        
        # 解析域名到IP
        try:
            addr_info = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return JSONResponse({'error': '无法解析直链域名'}, status_code=400)
        
        # 检查每个IP是否是内网IP
        for _, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip)
                # 检查是否是内网、回环、链路本地、未指定或多播地址
                if (ip_obj.is_loopback or ip_obj.is_private or 
                    ip_obj.is_link_local or ip_obj.is_unspecified or 
                    ip_obj.is_multicast):
                    return JSONResponse({
                        'error': '直链不能指向内网地址',
                        'code': 'PRIVATE_IP_NOT_ALLOWED'
                    }, status_code=400)
            except ValueError:
                continue
    except Exception as e:
        logger.warning(f"SSRF检查失败: {e}")
        return JSONResponse({'error': '直链安全检查失败'}, status_code=400)

    # 验证语言参数
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    if ref_language not in valid_languages:
        ref_language = 'ch'

    # 验证服务商参数
    valid_providers = ['minimax', 'minimax_intl', 'cosyvoice']
    if provider not in valid_providers:
        return JSONResponse({
            'error': f'无效的服务商: {provider}',
            'code': 'TTS_PROVIDER_INVALID',
            'message': f'支持的服务商: {", ".join(valid_providers)}'
        }, status_code=400)

    # 获取 API Key
    _config_manager = get_config_manager()
    api_key = _config_manager.get_tts_api_key(provider)
    if not api_key:
        if provider in ('minimax', 'minimax_intl'):
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        else:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)

    # 导入所有可能用到的异常类（用于后面的异常捕获）
    from utils.voice_clone import MinimaxVoiceCloneError, QwenVoiceCloneError
    
    # 设置服务商相关参数
    if provider in ('minimax', 'minimax_intl'):
        from utils.voice_clone import (
            MinimaxVoiceCloneClient, 
            minimax_normalize_language,
            get_minimax_base_url,
            get_minimax_storage_prefix
        )
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'
    else:  # cosyvoice
        from utils.voice_clone import QwenVoiceCloneClient, qwen_language_hints
        storage_key = api_key
        provider_label = '阿里云CosyVoice'

    # 验证直链是否可访问（HEAD失败时回退到GET）
    # per-call AsyncClient: 用户粘贴直链触发的一次性克隆流程，冷路径（外部 CDN 主机）
    try:
        async with httpx.AsyncClient(timeout=30, proxy=None, trust_env=False) as client:
            head_resp = await client.head(direct_link, follow_redirects=True)
            if head_resp.status_code >= 400:
                # HEAD失败，尝试GET
                logger.warning(f"HEAD请求失败({head_resp.status_code})，尝试GET请求: {direct_link}")
                get_resp = await client.get(direct_link, follow_redirects=True)
                if get_resp.status_code >= 400:
                    return JSONResponse({
                        'error': f'直链无法访问，状态码: {get_resp.status_code}',
                        'code': 'DIRECT_LINK_INACCESSIBLE'
                    }, status_code=400)
    except Exception as e:
        logger.warning(f"直链验证失败: {e}")
        # 不阻断流程，只是警告

    # 根据服务商类型执行不同的克隆逻辑
    try:
        if provider in ('minimax', 'minimax_intl'):
            # ========== MiniMax 直链克隆流程 ==========
            # 1. 下载音频文件（使用流式读取避免内存问题）
            logger.info(f"开始下载直链音频: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB限制

            # per-call AsyncClient: 用户一次性直链下载，冷路径
            async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
                async with client.stream('GET', direct_link, follow_redirects=True) as download_resp:
                    if download_resp.status_code != 200:
                        return JSONResponse({
                            'error': f'下载音频失败，状态码: {download_resp.status_code}',
                            'code': 'DOWNLOAD_FAILED'
                        }, status_code=400)
                    
                    # 从Content-Disposition或URL推断文件名
                    filename = 'audio.wav'
                    content_disposition = download_resp.headers.get('content-disposition', '')
                    if 'filename=' in content_disposition:
                        import re
                        match = re.search(r'filename=["\']?([^"\';]+)', content_disposition)
                        if match:
                            filename = match.group(1)
                    else:
                        # 从URL路径获取文件名
                        from urllib.parse import urlparse
                        parsed = urlparse(direct_link)
                        path_filename = parsed.path.split('/')[-1]
                        if path_filename and '.' in path_filename:
                            filename = path_filename
                    
                    # 流式读取内容并检查大小
                    audio_buffer = io.BytesIO()
                    total_size = 0
                    async for chunk in download_resp.aiter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > MAX_FILE_SIZE:
                            return JSONResponse({
                                'error': '音频文件超过100MB限制',
                                'code': 'FILE_TOO_LARGE'
                            }, status_code=400)
                        audio_buffer.write(chunk)
                    
                    audio_buffer.seek(0)
                    audio_bytes = audio_buffer.getvalue()
            
            logger.info(f"音频下载完成: {filename}, 大小: {len(audio_bytes)} bytes")
            
            # 2. 计算音频内容的 MD5 用于去重（与文件上传路径保持一致）
            import hashlib
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()
            
            # 3. MD5 去重检查
            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })
            
            # 2. 音频归一化处理（与文件上传路径保持一致）
            from utils.audio import normalize_voice_clone_api_audio
            original_buffer = io.BytesIO(audio_bytes)
            normalized_buffer, normalized_filename, _ = normalize_voice_clone_api_audio(
                original_buffer, filename
            )
            
            original_prefix, minimax_prefix = _build_minimax_request_prefix(prefix, provider_label)

            # 4. 使用 MinimaxVoiceCloneClient 上传并注册音色
            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)
            
            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )
            
            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # 保存原始前缀用于显示
                'minimax_prefix': minimax_prefix,  # 保存实际提交给 MiniMax 的安全前缀
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }
            
            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")
            
        else:  # cosyvoice
            # ========== CosyVoice 直链克隆流程 ==========
            # 1. 下载音频文件以计算内容MD5（使用流式读取避免内存问题）
            logger.info(f"开始下载直链音频用于CosyVoice: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB限制

            # per-call AsyncClient: 用户一次性直链下载，冷路径
            async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
                async with client.stream('GET', direct_link, follow_redirects=True) as download_resp:
                    if download_resp.status_code != 200:
                        return JSONResponse({
                            'error': f'下载音频失败，状态码: {download_resp.status_code}',
                            'code': 'DOWNLOAD_FAILED'
                        }, status_code=400)
                    
                    # 流式读取内容并检查大小
                    audio_buffer = io.BytesIO()
                    total_size = 0
                    async for chunk in download_resp.aiter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > MAX_FILE_SIZE:
                            return JSONResponse({
                                'error': '音频文件超过100MB限制',
                                'code': 'FILE_TOO_LARGE'
                            }, status_code=400)
                        audio_buffer.write(chunk)
                    
                    audio_buffer.seek(0)
                    audio_bytes = audio_buffer.getvalue()
            
            logger.info(f"音频下载完成，大小: {len(audio_bytes)} bytes")
            
            # 2. 计算音频内容的 MD5 用于去重
            import hashlib
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()
            
            # 3. MD5 去重检查
            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })
            
            # 4. 使用直链注册音色
            language_hints = qwen_language_hints(ref_language)
            client = QwenVoiceCloneClient(api_key=api_key, tflink_upload_url=TFLINK_UPLOAD_URL)

            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = get_cosyvoice_clone_model()
            voice_id, _ = await asyncio.to_thread(
                client.create_voice,
                prefix=prefix,
                url=direct_link,
                language_hints=language_hints,
                target_model=clone_model,
            )

            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'cosyvoice',
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

    except (MinimaxVoiceCloneError, QwenVoiceCloneError) as e:
        logger.error(f"{provider_label} 直链音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {error_detail}',
            'provider': provider
        }, status_code=500)
    except Exception as e:
        logger.error(f"{provider_label} 直链音色注册时发生错误: {e}")
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {str(e)}',
            'provider': provider
        }, status_code=500)

    # 保存到本地音色库
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} 直链 voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} 直链 voice_id 到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}直链音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}直链音色注册成功并已保存到音色库',
        'provider': provider,
        'is_direct_link': True
    })


@router.get('/character-card/list')
async def get_character_cards():
    """获取character_cards文件夹中的所有角色卡"""
    try:
        # 获取config_manager实例
        config_mgr = get_config_manager()
        
        # 确保character_cards目录存在
        config_mgr.ensure_chara_directory()
        
        # 遍历 character_cards 目录下的所有 .chara.json 文件，并行读取
        # （角色卡多时串行 await 会 N 次线程切换 + JSON 解析，整条接口延迟线性增长）
        candidate_filenames = [f for f in os.listdir(config_mgr.chara_dir) if f.endswith('.chara.json')]

        async def _read_one_card(filename: str):
            file_path = os.path.join(config_mgr.chara_dir, filename)
            try:
                data = await read_json_async(file_path)
                if data and data.get('name'):
                    return {
                        'id': filename[:-11],  # 去掉 .chara.json 后缀
                        'name': data['name'],
                        'description': data.get('description', ''),
                        'tags': data.get('tags', []),
                        'rawData': data,
                        'path': file_path,
                    }
            except Exception as e:
                logger.error(f"读取角色卡文件 {filename} 时出错: {e}")
            return None

        results = await asyncio.gather(
            *(_read_one_card(fn) for fn in candidate_filenames),
            return_exceptions=False,
        )
        character_cards = [r for r in results if r is not None]

        logger.info(f"已加载 {len(character_cards)} 个角色卡")
        return {"success": True, "character_cards": character_cards}
    except Exception as e:
        logger.error(f"获取角色卡列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/catgirl/save-to-model-folder')
async def save_catgirl_to_model_folder(request: Request):
    """将角色卡保存到模型所在文件夹"""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')  # 接收模型名称而不是路径
        file_name = data.get('fileName')
        
        if not chara_data or not model_name or not file_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
        
        # 使用find_model_directory函数查找模型的实际文件系统路径
        model_folder_path, _ = find_model_directory(model_name)
        
        # 检查模型目录是否存在
        if not model_folder_path:
            return JSONResponse({"success": False, "error": f"无法找到模型目录: {model_name}"}, status_code=404)
        
        # 检查是否是用户导入的模型，只允许写入用户目录的模型，不允许写入 workshop/static
        config_mgr = get_config_manager()
        is_user_model = is_user_imported_model(model_folder_path, config_mgr)
        
        if not is_user_model:
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "只能保存到用户导入的模型目录。请先导入模型到用户模型目录后再保存。"
                }
            )
        
        # 确保模型文件夹存在
        if not os.path.exists(model_folder_path):
            os.makedirs(model_folder_path, exist_ok=True)
            logger.info(f"已创建模型文件夹: {model_folder_path}")
        
        # 防路径穿越：只允许文件名，不允许路径
        safe_name = os.path.basename(file_name)
        if safe_name != file_name or ".." in safe_name or safe_name.startswith(("/", "\\")):
            return JSONResponse({"success": False, "error": "非法文件名"}, status_code=400)
            
        # 保存角色卡到模型文件夹
        file_path = os.path.join(model_folder_path, safe_name)
        await atomic_write_json_async(file_path, chara_data, ensure_ascii=False, indent=2)
        
        logger.info(f"角色卡已成功保存到模型文件夹: {file_path}")
        return {"success": True, "path": file_path, "modelFolderPath": model_folder_path}
    except Exception as e:
        logger.error(f"保存角色卡到模型文件夹失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/character-card/save')
async def save_character_card(request: Request):
    """保存角色卡到characters.json文件"""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        character_card_name = data.get('character_card_name')
        
        if not chara_data or not character_card_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
        
        # 获取config_manager实例
        _config_manager = get_config_manager()
        
        # 加载现有的characters.json
        characters = await _config_manager.aload_characters()
        
        # 确保'猫娘'键存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 获取角色卡名称（档案名）
        # 兼容中英文字段名
        chara_name = chara_data.get('档案名') or chara_data.get('name') or character_card_name
        name_error = _validate_profile_name(chara_name)
        if name_error:
            return JSONResponse({"success": False, "error": f"角色名称无效: {name_error}"}, status_code=400)
        chara_name = str(chara_name).strip()
        filtered_chara_data = _filter_mutable_catgirl_fields(chara_data)
        
        # 创建猫娘数据，只保存非空字段
        catgirl_data = {}
        for k, v in filtered_chara_data.items():
            if k != '档案名' and k != 'name':
                if v:  # 只保存非空字段
                    catgirl_data[k] = v
        
        # 更新或创建猫娘数据
        characters['猫娘'][chara_name] = catgirl_data
        
        # 保存到characters.json
        await _config_manager.asave_characters(characters)
        
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        if initialize_character_data:
            await initialize_character_data()
        
        logger.info(f"角色卡已成功保存到characters.json: {chara_name}")
        return {"success": True, "character_card_name": chara_name}
    except Exception as e:
        logger.error(f"保存角色卡到characters.json失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get('/catgirl/{name}/export')
async def export_catgirl_card(name: str):
    """导出猫娘角色卡为PNG图片（包含模型和设定的压缩包数据）

    导出流程：
    1. 获取猫娘的设定数据
    2. 如果使用了非默认模型，将模型文件打包到压缩包
    3. 将压缩包数据拼接到PNG图片中
    4. 返回PNG图片供下载

    注意：默认模型(mao_pro)不会被包含在导出中
    """
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path
    from urllib.parse import quote

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / 'character_data.zip'

            # 创建压缩包（使用UTF-8编码支持中文文件名）
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # 1. 添加角色设定JSON（包含所有字段，但省略指定字段）
                # 定义要省略的字段
                FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id'}

                def filter_excluded_fields(data):
                    """递归过滤掉指定字段"""
                    if isinstance(data, dict):
                        return {
                            k: filter_excluded_fields(v)
                            for k, v in data.items()
                            if k not in FIELDS_TO_EXCLUDE
                        }
                    elif isinstance(data, list):
                        return [filter_excluded_fields(item) for item in data]
                    else:
                        return data

                chara_json = {
                    '档案名': name,
                    **filter_excluded_fields(catgirl_data)
                }
                zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

                # 2. 检查并添加模型文件
                model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d', legacy_keys=('model_type',))
                model_added = False

                if model_type == 'live2d':
                    # 获取Live2D模型路径
                    live2d_path = get_reserved(
                        catgirl_data,
                        'avatar',
                        'live2d',
                        'model_path',
                        default='',
                        legacy_keys=('live2d',)
                    )

                    if live2d_path and live2d_path.strip():
                        # 解析模型名称
                        live2d_name = live2d_path.replace('\\', '/').rstrip('/')
                        if live2d_name.endswith('.model3.json'):
                            live2d_name = live2d_name.split('/')[-2] if '/' in live2d_name else live2d_name.replace('.model3.json', '')
                        else:
                            live2d_name = live2d_name.split('/')[-1]

                        # 检查是否是默认模型
                        if live2d_name == 'mao_pro':
                            logger.info(f'猫娘 {name} 使用的是默认模型 mao_pro，跳过模型打包')
                        else:
                            # 查找模型目录
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                # 检查是否是用户导入的模型
                                if is_user_imported_model(model_dir, _config_manager):
                                    # 添加模型文件到压缩包
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True
                                else:
                                    logger.warning(f'模型 {live2d_name} 不是用户导入的模型，跳过打包')
                            else:
                                logger.warning(f'找不到模型目录: {live2d_name}')

                elif model_type in ('vrm', 'live3d'):
                    # 处理VRM/MMD模型
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    # 优先处理MMD模型（需要导出整个文件夹）
                    if mmd_path and mmd_path.strip():
                        # 解析MMD模型路径
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                # 对于MMD模型，导出整个文件夹（包含贴图等依赖文件）
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name

                                # 添加整个模型文件夹到压缩包
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True
                            else:
                                logger.warning(f'找不到MMD模型文件: {mmd_path}')

                    # 处理VRM模型（单个文件）
                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True
                            else:
                                logger.warning(f'找不到VRM模型文件: {vrm_path}')

                # 3. 读取卡面元数据 sidecar（作者 / 创建时间）
                _sidecar_meta_path = _config_manager.card_face_meta_path(name)
                _sidecar_existed = await asyncio.to_thread(_sidecar_meta_path.exists)
                _sidecar_meta = await asyncio.to_thread(_read_card_meta, _sidecar_meta_path)
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用已有的创建时间；未设置时使用当前时间并回写 sidecar，
                # 以确保后续导出不会重复刷新。
                _author = str(_sidecar_meta.get('author') or '').strip()[:64]
                _existing_created_at = str(_sidecar_meta.get('created_at') or '').strip()
                _created_at = _existing_created_at or now_iso
                if not _existing_created_at:
                    try:
                        _config_manager.ensure_card_faces_directory()
                        _new_meta = dict(_sidecar_meta)
                        _new_meta['created_at'] = _created_at
                        if not _new_meta.get('updated_at'):
                            _new_meta['updated_at'] = now_iso
                        # 仅当 sidecar 原本不存在且 origin 为默认值（None/空/'self'）时才推断来源，
                        # 避免覆盖已有的 self 默认值或用户设定。
                        if not _sidecar_existed and _new_meta.get('origin') in (None, '', 'self'):
                            _new_meta['origin'] = _detect_card_origin_from_character(catgirl_data or {})
                        await asyncio.to_thread(_write_card_meta, _sidecar_meta_path, _new_meta)
                    except Exception as _meta_persist_err:
                        logger.warning(f"[导出角色卡] 回写创建时间到 sidecar 失败: {_meta_persist_err}")

                # 4. 添加元数据文件
                metadata = {
                    'version': '1.0',
                    'export_time': now_iso,
                    'character_name': name,
                    'author': _author,
                    'created_at': _created_at,
                    'model_included': model_added,
                    'model_type': model_type
                }
                zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

            # 5. 获取卡面图：优先使用保存的 card_faces/{name}.png，
            #    不存在时才回退到合成图。
            from utils.screenshot_utils import _validate_image_data
            saved_face_path = _config_manager.card_faces_dir / f"{name}.png"
            png_data = None
            if saved_face_path.exists():
                try:
                    png_data = await asyncio.to_thread(saved_face_path.read_bytes)
                    validated = await asyncio.to_thread(_validate_image_data, png_data)
                    if validated is None:
                        logger.warning(f"[导出角色卡] 已保存卡面验证失败，回退到合成图")
                        png_data = None
                    elif not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
                        try:
                            from PIL import Image
                            from io import BytesIO
                            img = await asyncio.to_thread(Image.open, BytesIO(png_data))
                            buf = BytesIO()
                            await asyncio.to_thread(img.save, buf, format='PNG')
                            png_data = buf.getvalue()
                        except Exception as _conv_err:
                            logger.warning(f"[导出角色卡] 卡面非 PNG 且重新编码失败，回退到合成图: {_conv_err}")
                            png_data = None
                except Exception as _read_err:
                    logger.warning(f"[导出角色卡] 读取已保存卡面失败，回退到合成图: {_read_err}")
                    png_data = None

            if png_data is None:
                # 回退：合成一张默认长方形角色卡图片
                from PIL import Image, ImageDraw, ImageFont
                width, height = 600, 800
                img = Image.new('RGB', (width, height), color='#E8F4F8')
                draw = ImageDraw.Draw(img)
                header_height = height // 6
                draw.rectangle([0, 0, width, header_height], fill='#40C5F1')

                font_size = 36
                font = None
                font_candidates = [
                    "msyhbd.ttc", "Microsoft YaHei Bold.ttf", "simhei.ttf",
                    "msyh.ttc", "Microsoft YaHei.ttf", "simsun.ttc",
                    "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc"
                ]
                for font_name in font_candidates:
                    try:
                        font = ImageFont.truetype(font_name, font_size)
                        break
                    except (OSError, IOError):
                        continue

                if font is None:
                    font = ImageFont.load_default()
                    logger.warning("[导出角色卡] 未找到支持中文的系统字体，可能会显示为方框")

                text = name
                bbox = draw.textbbox((0, 0), text, font=font)
                text_height = bbox[3] - bbox[1]
                text_x = 30
                text_y = (header_height - text_height) // 2 - bbox[1]
                draw.text((text_x, text_y), text, fill='white', font=font)
                png_path = temp_path / 'character_card.png'
                img.save(png_path, 'PNG')
                with open(png_path, 'rb') as f:
                    png_data = f.read()

            # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
            with open(zip_path, 'rb') as f:
                zip_data = f.read()

            combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

            # 7. 返回文件下载
            from urllib.parse import quote

            safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
            if not safe_name:
                safe_name = "character_card"
            original_filename = f"{safe_name}.png"
            encoded_filename = quote(original_filename, safe='')
            content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
            try:
                ascii_filename = original_filename.encode('ascii').decode('ascii')
            except UnicodeEncodeError:
                ascii_filename = "character_card.png"

            return Response(
                content=combined_data,
                # 用 octet-stream 避免浏览器将响应作为图片在新标签中预览，
                # 配合前端 <a download> 开启下载流程。
                media_type='application/octet-stream',
                headers={
                    'Content-Disposition': content_disposition,
                    'X-Filename': ascii_filename,
                    'Cache-Control': 'no-store',
                }
            )

    except Exception as e:
        logger.exception(f"导出角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.get('/catgirl/{name}/export-settings')
async def export_catgirl_settings_only(name: str):
    """仅导出猫娘设定（加密，不包含模型文件）

    导出流程：
    1. 获取猫娘的设定数据
    2. 过滤掉指定字段
    3. 使用简单的异或加密
    4. 直接返回加密后的JSON文件
    """
    from urllib.parse import quote

    # XOR混淆密钥（仅用于防止意外编辑，非安全加密）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_obfuscate(data: bytes, key: bytes) -> bytes:
        """使用XOR进行简单的数据混淆/还原（仅用于防止意外编辑，非安全加密）

        注意：这不是真正的加密，只是简单的可逆混淆，用于防止用户意外编辑。
        如果需要真正的安全保护，应使用其他加密方案。
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 定义要省略的字段（仅导出设定时不包含模型相关信息）
        FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id', '_reserved'}

        def filter_excluded_fields(data):
            """递归过滤掉指定字段"""
            if isinstance(data, dict):
                return {
                    k: filter_excluded_fields(v)
                    for k, v in data.items()
                    if k not in FIELDS_TO_EXCLUDE
                }
            elif isinstance(data, list):
                return [filter_excluded_fields(item) for item in data]
            else:
                return data

        # 准备角色设定JSON（过滤字段，不包含模型信息）
        chara_json = {
            '档案名': name,
            **filter_excluded_fields(catgirl_data)
        }
        json_data = json.dumps(chara_json, ensure_ascii=False, indent=2).encode('utf-8')

        # 加密JSON数据
        encrypted_data = xor_obfuscate(json_data, XOR_KEY)

        # 构建文件名
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}_设定.nekocfg"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_settings.nekocfg"

        return Response(
            content=encrypted_data,
            media_type='application/octet-stream',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出设定失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.post('/import-card')
async def import_character_card(
    zip_file: UploadFile = File(...),
    card_image: UploadFile = File(None),
):
    """导入角色卡（从PNG图片中提取的ZIP文件）

    可选参数：
      - card_image: 原始载体 PNG。若提供且本地尚未存在同名卡面，则直接存为
        该角色的 card-face，以谙则老角色卡「封面图即卡面」的习惯。
    """
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path

    # XOR混淆密钥（与导出时相同，用于防止意外编辑）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_deobfuscate(data: bytes, key: bytes) -> bytes:
        """使用XOR进行数据还原（与xor_obfuscate相同的操作，用于命名一致性）

        注意：这不是真正的解密，只是简单的可逆混淆还原。
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    temp_dir = None
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'imported.zip'

        # 保存上传的文件（使用流式读取并限制大小）
        try:
            file_buffer = await _read_limited_stream(zip_file, MAX_UPLOAD_SIZE)
            with open(zip_path, 'wb') as f:
                f.write(file_buffer.getvalue())
        except _UploadTooLargeError as e:
            logger.warning(f"[导入角色卡] 文件过大: {e}")
            return JSONResponse({'success': False, 'error': str(e)}, status_code=400)

        # 检查是否是加密的 .nekocfg 文件（直接是加密数据，不是ZIP）
        is_neko_file = zip_file.filename and zip_file.filename.endswith('.nekocfg')

        if is_neko_file:
            # 直接解密 .nekocfg 文件
            with open(zip_path, 'rb') as f:
                encrypted_data = f.read()
            try:
                decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                character_data = json.loads(decrypted_data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"[导入角色卡] 解析 .nekocfg 文件失败: {e}")
                return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
            if not isinstance(character_data, dict):
                return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
            character_data = _filter_mutable_catgirl_fields(character_data)
            character_name = str(character_data.get('档案名', '')).strip()
            character_data['档案名'] = character_name
            name_error = _validate_profile_name(character_name)
            if name_error:
                return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            metadata = {'encrypted': True, 'model_included': False}
        else:
            # 解压ZIP文件（PNG角色卡格式）- 使用安全的解压方式防止 Zip Slip 攻击
            MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024  # 500 MB 总解压大小限制
            MAX_MEMBER_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB 单个文件大小限制
            extract_path = temp_path / 'extracted'
            extract_path.mkdir()

            total_uncompressed_size = 0
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or '..' in member_path.parts or '\\' in member:
                        logger.warning(f"[导入角色卡] 跳过不安全的路径: {member}")
                        continue

                    zip_info = zf.getinfo(member)
                    member_size = zip_info.file_size
                    if total_uncompressed_size + member_size > MAX_TOTAL_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，大小超出总限制: {member}")
                        continue
                    if member_size > MAX_MEMBER_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，单文件大小超限: {member}")
                        continue

                    dest_path = extract_path / member_path
                    try:
                        dest_path.resolve().relative_to(extract_path.resolve())
                    except ValueError:
                        logger.warning(f"[导入角色卡] 跳过路径验证失败: {member}")
                        continue
                    if member.endswith('/'):
                        dest_path.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        total_uncompressed_size += member_size
                        with zf.open(member) as src, open(dest_path, 'wb') as dst:
                            await asyncio.to_thread(shutil.copyfileobj, src, dst, length=8192)

            # 读取角色设定（支持加密和非加密格式）
            character_json_path = extract_path / 'character.json'
            character_json_encrypted_path = extract_path / 'character.json.encrypted'

            if character_json_path.exists():
                # 非加密格式
                try:
                    character_data = await read_json_async(character_json_path)
                except json.JSONDecodeError as e:
                    logger.warning(f"[导入角色卡] 解析 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            elif character_json_encrypted_path.exists():
                # 加密格式，需要解密
                try:
                    with open(character_json_encrypted_path, 'rb') as f:
                        encrypted_data = f.read()
                    decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                    character_data = json.loads(decrypted_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"[导入角色卡] 解析加密 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            else:
                return JSONResponse({'success': False, 'error': '角色卡文件损坏：缺少character.json'}, status_code=400)

            # 读取元数据
            metadata_path = extract_path / 'metadata.json'
            metadata = {}
            if metadata_path.exists():
                metadata = await read_json_async(metadata_path)

        character_name = character_data.get('档案名', '未命名角色')

        _config_manager = get_config_manager()

        async with _ugc_sync_lock:
            characters = await _config_manager.aload_characters()

            # 检查是否已存在同名角色，使用 Windows 风格的命名 (x)
            if character_name in characters.get('猫娘', {}):
                # 生成新名称
                base_name = character_name
                counter = 1
                while f"{base_name}({counter})" in characters.get('猫娘', {}):
                    counter += 1
                character_name = f"{base_name}({counter})"
                character_data['档案名'] = character_name

            # 处理模型文件（仅当不是 .nekocfg 文件时）
            imported_model_info = None  # 记录导入的模型信息，用于自动使用

            def _find_model3_json(directory):
                """递归查找 .model3.json 文件"""
                for item in directory.iterdir():
                    if item.is_file() and item.name.lower().endswith('.model3.json'):
                        return item
                    elif item.is_dir():
                        result = _find_model3_json(item)
                        if result:
                            return result
                return None

            if not is_neko_file:
                model_dir = extract_path / 'model'
                if model_dir.exists() and model_dir.is_dir():
                    model_type = metadata.get('model_type', 'live2d')

                    for model_item in model_dir.iterdir():
                        if model_item.is_dir():
                            # 检查是 Live2D 还是 MMD 模型文件夹
                            # MMD 模型文件夹通常包含 .pmx, .pmd 文件
                            has_mmd_file = any(f.suffix.lower() in ('.pmx', '.pmd') for f in model_item.iterdir() if f.is_file())
                            # Live2D 模型文件夹通常包含 .model3.json 文件（递归搜索）
                            model3_file = _find_model3_json(model_item)
                            has_live2d_file = model3_file is not None

                            if has_mmd_file:
                                # MMD 模型（文件夹形式，包含贴图等依赖文件）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.mmd_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.mmd_dir / model_name
                                    counter += 1

                                # 复制整个模型文件夹
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入MMD模型文件夹: {original_model_name} -> {model_name}')

                                # 查找文件夹中的主模型文件（.pmx 或 .pmd）
                                main_model_file = None
                                for f in target_model_dir.iterdir():
                                    if f.is_file() and f.suffix.lower() in ('.pmx', '.pmd'):
                                        main_model_file = f
                                        break

                                if main_model_file:
                                    imported_model_info = {
                                        'type': 'mmd',
                                        'name': model_name,
                                        'original_name': original_model_name,
                                        'path': f'/user_mmd/{model_name}/{main_model_file.name}'
                                    }
                                else:
                                    logger.warning(f'MMD模型文件夹中没有找到主模型文件: {model_name}')

                            elif has_live2d_file:
                                # Live2D 模型（文件夹形式）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.live2d_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.live2d_dir / model_name
                                    counter += 1

                                # 复制模型文件
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入Live2D模型: {original_model_name} -> {model_name}')

                                # 查找复制后的 .model3.json 文件，保留相对路径
                                model3_file = _find_model3_json(target_model_dir)
                                if model3_file:
                                    model3_filename = str(model3_file.relative_to(target_model_dir))
                                else:
                                    model3_filename = f'{model_name}.model3.json'
                                logger.info(f'找到 Live2D 模型文件: {model3_filename}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'live2d',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'model3_filename': model3_filename
                                }

                        elif model_item.is_file():
                            # VRM 模型（文件形式）
                            model_file = model_item
                            original_model_name = model_file.stem  # 不含扩展名的文件名
                            model_ext = model_file.suffix.lower()

                            if model_ext == '.vrm':
                                # VRM 模型
                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                counter = 1

                                while target_model_path.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                    counter += 1

                                await asyncio.to_thread(shutil.copy2, model_file, target_model_path)
                                logger.info(f'已导入VRM模型: {original_model_name} -> {model_name}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'vrm',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'path': f'/user_vrm/{model_name}{model_ext}'
                                }
                else:
                    logger.warning(f"[导入角色卡] model 目录不存在或不是目录: {model_dir}")

                # 自动给猫娘使用导入的模型
                # 使用 _reserved 字段存储模型配置（这是系统内部使用的字段）
                if imported_model_info:
                    character_data['_reserved'] = character_data.get('_reserved', {})
                    character_data['_reserved']['avatar'] = character_data['_reserved'].get('avatar', {})

                    if imported_model_info['type'] == 'live2d':
                        model_name = imported_model_info['name']
                        model3_filename = imported_model_info.get('model3_filename', f'{model_name}.model3.json')
                        # 保留现有的 live2d 设置，只更新 model_path
                        character_data['_reserved']['avatar']['live2d'] = character_data['_reserved']['avatar'].get('live2d', {})
                        character_data['_reserved']['avatar']['live2d']['model_path'] = f'{model_name}/{model3_filename}'
                        character_data['_reserved']['avatar']['model_type'] = 'live2d'
                        logger.info(f'已自动为角色 {character_name} 设置Live2D模型: {model_name}, 文件: {model3_filename}')

                    elif imported_model_info['type'] == 'vrm':
                        character_data['_reserved']['avatar']['vrm'] = character_data['_reserved']['avatar'].get('vrm', {})
                        character_data['_reserved']['avatar']['vrm']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置VRM模型: {imported_model_info["name"]}')

                    elif imported_model_info['type'] == 'mmd':
                        # 保留现有的 mmd 设置（捏脸、动画等），只更新 model_path
                        character_data['_reserved']['avatar']['mmd'] = character_data['_reserved']['avatar'].get('mmd', {})
                        character_data['_reserved']['avatar']['mmd']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置MMD模型: {imported_model_info["name"]}')
                else:
                    logger.warning("[导入角色卡] 没有找到可导入的模型")

            # 添加角色到characters.json
            if '猫娘' not in characters:
                characters['猫娘'] = {}

            # 移除档案名键（因为已经用作字典键）
            chara_data_to_save = {k: v for k, v in character_data.items() if k != '档案名'}
            characters['猫娘'][character_name] = chara_data_to_save

            # 保存到文件
            await _config_manager.asave_characters(characters)

            # 刷新内存中的角色数据，确保磁盘和内存同步
            initialize_character_data = get_initialize_character_data()
            if initialize_character_data:
                await initialize_character_data()

            # 写入卡面元数据 sidecar（origin=imported）
            try:
                _config_manager.ensure_card_faces_directory()
                meta_path = _config_manager.card_face_meta_path(character_name)
                imported_author = ''
                imported_created_at = ''
                if isinstance(metadata, dict):
                    imported_author = str(metadata.get('author', '') or '').strip()[:64]
                    imported_created_at = str(metadata.get('created_at', '') or '').strip()[:32]
                    if not imported_created_at:
                        imported_created_at = str(metadata.get('export_time', '') or '').strip()[:32]
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用源卡中的创建时间，未提供时才赋为当前时间
                created_at = imported_created_at or now_iso
                meta = {
                    'author': imported_author,
                    'origin': 'imported',
                    'created_at': created_at,
                    'updated_at': now_iso,
                }
                await asyncio.to_thread(_write_card_meta, meta_path, meta)
            except Exception as meta_err:
                logger.warning(f"[导入角色卡] 写入卡面元数据失败: {meta_err}")
                return JSONResponse({
                    "success": True,
                    "error": f"角色数据已导入，但卡面元数据写入失败: {meta_err}",
                    "card_meta_saved": False,
                    "character_name": character_name,
                }, status_code=200)

            # 老角色卡兼容：如果前端上传了载体 PNG，且本地还没有同名卡面，
            # 则直接使用该 PNG 作为卡面（带 neKo chunk 不影响质量）。
            try:
                if card_image is not None and card_image.filename:
                    face_path = _config_manager.card_faces_dir / f"{character_name}.png"
                    if not face_path.exists():
                        try:
                            # 先用更大的上传限制读取载体 PNG（可能嵌入 ZIP）
                            face_buffer = await _read_limited_stream(card_image, MAX_UPLOAD_SIZE)
                            face_bytes = face_buffer.getvalue()
                        except _UploadTooLargeError as e:
                            logger.warning(f"[导入角色卡] 载体 PNG 超过上传限制，跳过保存: {e}")
                            face_bytes = b''
                        if face_bytes:
                            try:
                                from utils.screenshot_utils import _validate_image_data
                                from PIL import Image as PILImage

                                validated = await asyncio.to_thread(_validate_image_data, face_bytes)
                                if validated is None:
                                    logger.warning(f"[导入角色卡] 载体 PNG 验证失败，跳过保存")
                                else:
                                    if validated.mode not in ('RGB', 'RGBA', 'L'):
                                        validated = validated.convert('RGB')
                                    out = io.BytesIO()
                                    validated.save(out, format='PNG')
                                    valid_png = out.getvalue()
                                    if len(valid_png) > MAX_CARD_FACE_SIZE:
                                        logger.warning(f"[导入角色卡] 重编码后的卡面图 ({len(valid_png)} bytes) 超过最大限制 ({MAX_CARD_FACE_SIZE} bytes)，跳过保存")
                                    else:
                                        await asyncio.to_thread(face_path.write_bytes, valid_png)
                                        logger.info(f"[导入角色卡] 已将载体 PNG 存为卡面: {face_path}")
                            except Exception as pil_err:
                                logger.warning(f"[导入角色卡] 卡面图 PNG 处理失败，跳过保存: {pil_err}")
            except Exception as face_err:
                logger.warning(f"[导入角色卡] 保存载体 PNG 为卡面失败: {face_err}")

        return JSONResponse({
            'success': True,
            'character_name': character_name,
            'message': f'角色卡 "{character_name}" 导入成功'
        })

    except zipfile.BadZipFile:
        logger.error("导入角色卡失败：无效的ZIP文件")
        return JSONResponse({'success': False, 'error': '无效的角色卡文件格式'}, status_code=400)
    except Exception as e:
        logger.exception(f"导入角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导入失败: {str(e)}'}, status_code=500)
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)


# ====== 角色卡卡面（Card Face）存储 ======

# 卡面元数据 sidecar 默认结构
def _default_card_meta(origin: str = 'self') -> dict:
    """返回默认卡面元数据。"""
    return {
        'author': '',
        'origin': origin,  # self / imported / steam
        'created_at': None,
        'updated_at': None,
    }


def _read_card_meta(meta_path) -> dict:
    """读取 sidecar JSON，文件不存在或损坏时返回默认值。"""
    try:
        if meta_path.exists():
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"卡面元数据内容无效（非字典）{meta_path}: {type(data).__name__}")
                return _default_card_meta(origin=None)
            # 合并默认字段，保证字段完整
            merged = _default_card_meta()
            merged.update({k: v for k, v in data.items() if k in merged})
            return merged
    except Exception as e:
        logger.warning(f"读取卡面元数据失败 {meta_path}: {e}")
        return _default_card_meta(origin=None)
    return _default_card_meta()


def _write_card_meta(meta_path, meta: dict) -> None:
    """写入 sidecar JSON（原子写入）。调用方需先 ensure_card_faces_directory()。"""
    from utils.file_utils import atomic_write_json
    atomic_write_json(meta_path, meta, ensure_ascii=False, indent=2)


def _detect_card_origin_from_character(catgirl_data: dict) -> str:
    """从猫娘配置推断 origin（用于无 sidecar 时的回退）。
    依据角色卡本身的来源（character_origin.source），而非模型来源（avatar.asset_source），
    确保更换模型不会改变角色卡来源标注。"""
    try:
        char_source = get_reserved(catgirl_data, 'character_origin', 'source', default='')
        if char_source == 'steam_workshop':
            return 'steam'
    except Exception:
        pass
    return 'self'


@router.get('/card-faces')
async def list_card_faces():
    """返回所有已设置自定义卡面的猫娘名列表（用于前端避免无意义的 404 请求）"""
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    names: list[str] = []
    orphans: list[str] = []
    try:
        characters = await _config_manager.aload_characters()
        valid_names = set(characters.get('猫娘', {}).keys())
        if faces_dir.exists():
            for p in await asyncio.to_thread(lambda: list(faces_dir.glob('*.png'))):
                stem = p.stem
                if stem in valid_names:
                    names.append(stem)
                else:
                    orphans.append(stem)
        if orphans:
            logger.info(f"[list_card_faces] 孤儿卡面文件（无对应角色）: {orphans}")
    except Exception:
        logger.exception("list_card_faces failed")
        return JSONResponse({'success': False, 'error': '读取卡面列表失败'}, status_code=500)

    return JSONResponse({'success': True, 'names': names}, status_code=200)


@router.get('/card-metas')
async def list_card_metas():
    """批量返回所有猫娘的卡面元数据。

    对于没有 sidecar JSON 的历史角色卡，会根据猫娘配置推断 origin 后
    返回默认值，保证旧版本升级后前端仍能显示卡面信息。
    """
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    metas: dict = {}
    try:
        # 先加载角色列表，构建有效名称集合
        characters = await _config_manager.aload_characters()
        valid_names = set((characters.get('猫娘', {}) or {}).keys())
        # 只读取属于有效角色的 sidecar
        if faces_dir.exists():
            json_files = await asyncio.to_thread(lambda: list(faces_dir.glob('*.json')))
            for p in json_files:
                if p.stem in valid_names:
                    meta = await asyncio.to_thread(_read_card_meta, p)
                    if meta.get('origin') is None:
                        # sidecar 损坏：当作缺失处理，重新推断 origin
                        meta = _default_card_meta(_detect_card_origin_from_character(characters['猫娘'][p.stem]))
                    metas[p.stem] = meta
        # 补齐缺失 sidecar 的猫娘：按配置推断 origin，返回默认值
        for cname, cdata in (characters.get('猫娘', {}) or {}).items():
            if cname in metas:
                continue
            inferred = _default_card_meta(_detect_card_origin_from_character(cdata or {}))
            metas[cname] = inferred
    except Exception as e:
        logger.warning(f"批量读取卡面元数据失败: {e}")
        return JSONResponse({'success': False, 'error': '批量读取卡面元数据失败', 'details': str(e)}, status_code=500)

    return JSONResponse({'success': True, 'metas': metas})


@router.get('/catgirl/{name}/card-meta')
async def get_card_meta(name: str):
    """获取单个猫娘的卡面元数据。无 sidecar 时根据猫娘配置推断 origin 后返回默认。"""
    _config_manager = get_config_manager()
    safe_name = os.path.basename(name)
    if safe_name != name or not name:
        return JSONResponse({'success': False, 'error': '无效的角色名'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    meta_path = _config_manager.card_face_meta_path(name)
    meta = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or meta.get('origin') is None:
        # 无 sidecar 或读取失败：根据猫娘配置推断 origin
        meta['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])
    return JSONResponse({'success': True, 'meta': meta})


@router.put('/catgirl/{name}/card-meta')
async def put_card_meta(name: str, request: Request):
    """更新卡面元数据（当前仅支持 author 字段，且仅 origin=self 时允许）。"""
    _config_manager = get_config_manager()
    safe_name = os.path.basename(name)
    if safe_name != name or not name:
        return JSONResponse({'success': False, 'error': '无效的角色名'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)

    new_author = data.get('author') if isinstance(data, dict) else None
    if new_author is None:
        return JSONResponse({'success': False, 'error': '缺少 author 字段'}, status_code=400)
    new_author = str(new_author).strip()
    if len(new_author) > 64:
        return JSONResponse({'success': False, 'error': '作者名称过长（最长64字符）'}, status_code=400)

    meta_path = _config_manager.card_face_meta_path(name)
    existing = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or existing.get('origin') is None:
        existing['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])

    if existing.get('origin') != 'self':
        return JSONResponse({'success': False, 'error': '仅本地创作的卡面可修改作者'}, status_code=403)

    existing['author'] = new_author
    now_iso = datetime.now().isoformat(timespec='seconds')
    existing['updated_at'] = now_iso
    if not existing.get('created_at'):
        existing['created_at'] = now_iso

    _config_manager.ensure_card_faces_directory()
    await asyncio.to_thread(_write_card_meta, meta_path, existing)
    return JSONResponse({'success': True, 'meta': existing})


@router.get('/catgirl/{name}/card-face')
async def get_card_face(name: str):
    """获取角色的自定义卡面图片"""
    _config_manager = get_config_manager()
    # 安全检查：防止路径遍历
    safe_name = os.path.basename(name)
    if safe_name != name or not name:
        return JSONResponse({'success': False, 'error': '无效的角色名'}, status_code=400)

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    if not face_path.exists():
        return JSONResponse({'success': False, 'error': '卡面不存在'}, status_code=404)

    image_data = await asyncio.to_thread(face_path.read_bytes)
    return Response(content=image_data, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.put('/catgirl/{name}/card-face')
async def put_card_face(name: str, image: UploadFile = File(...)):
    """保存角色的自定义卡面图片"""
    _config_manager = get_config_manager()
    # 安全检查：防止路径遍历
    safe_name = os.path.basename(name)
    if safe_name != name or not name:
        return JSONResponse({'success': False, 'error': '无效的角色名'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 验证文件类型
    content_type = image.content_type or ''
    if not content_type.startswith('image/'):
        return JSONResponse({'success': False, 'error': '文件类型无效，请上传图片'}, status_code=400)

    # 流式读取并限制大小
    try:
        image_buffer = await _read_limited_stream(image, MAX_CARD_FACE_SIZE)
    except _UploadTooLargeError:
        return JSONResponse({'success': False, 'error': '图片文件过大（最大 10MB）'}, status_code=400)

    # 在线程中验证并重新编码为 PNG
    try:
        from utils.screenshot_utils import _validate_image_data
        from PIL import Image as PILImage

        image_buffer.seek(0)
        validated_img = await asyncio.to_thread(_validate_image_data, image_buffer.getvalue())
        if validated_img is None:
            return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

        def _reencode(img) -> bytes:
            if img.mode not in ('RGB', 'RGBA', 'L'):
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, format='PNG')
            return out.getvalue()

        png_bytes = await asyncio.to_thread(_reencode, validated_img)
    except Exception:
        return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

    # 重编码后再次校验大小（压缩后仍可能超过限制）
    if len(png_bytes) > MAX_CARD_FACE_SIZE:
        return JSONResponse({'success': False, 'error': '文件过大（重编码后超过10MB）'}, status_code=413)

    # 确保目录存在
    _config_manager.ensure_card_faces_directory()

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    await asyncio.to_thread(face_path.write_bytes, png_bytes)

    # 同步更新 sidecar 元数据
    meta_path = _config_manager.card_face_meta_path(name)
    try:
        meta = await asyncio.to_thread(_read_card_meta, meta_path)
        now_iso = datetime.now().isoformat(timespec='seconds')
        # 上传即视为本地创作；若此前是导入的，刷新创建时间
        previous_origin = meta.get('origin')
        meta['origin'] = 'self'
        if previous_origin != 'self' or not meta.get('created_at'):
            meta['created_at'] = now_iso
        meta['updated_at'] = now_iso
        await asyncio.to_thread(_write_card_meta, meta_path, meta)
    except Exception as meta_err:
        logger.warning(f"[上传卡面] 写入 sidecar 元数据失败: {meta_err}")
        return JSONResponse({
            'success': True,
            'partial_success': True,
            'error': f"卡面已保存，但元数据写入失败: {meta_err}",
        }, status_code=200)

    return JSONResponse({'success': True})


class _InvalidPortraitError(ValueError):
    """Raised by the character-card render helper when the user-supplied
    portrait fails PIL's verify() check. Caught at the endpoint to map
    to a 400 response (vs. 500 for genuine render errors)."""


@router.post('/catgirl/{name}/export-with-portrait')
async def export_catgirl_with_portrait(
    name: str,
    portrait: UploadFile = File(...),
    include_model: bool = Form(True)
):
    """导出角色卡（包含立绘图片）

    导出流程：
    1. 接收前端传来的立绘图片
    2. 将立绘合成到角色卡模板上
    3. 打包角色设定和模型文件（可选）
    4. 返回合成的PNG角色卡
    """
    import zipfile
    import tempfile
    from pathlib import Path
    from urllib.parse import quote
    from PIL import Image, ImageDraw, ImageFont

    temp_dir = None
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'character_data.zip'

        # 1. 创建ZIP压缩包（包含角色设定和模型）
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 准备角色设定JSON
            export_data = {'档案名': name, **catgirl_data}

            # 过滤掉运行时字段
            def _filter_export_fields(data, keep_model_paths=False):
                """导出时过滤字段"""
                result = {}
                for key, value in data.items():
                    if key in ('cursor_follow', 'physics', 'voice_id'):
                        continue
                    if key == '_reserved' and isinstance(value, dict):
                        reserved_copy = copy.deepcopy(value)
                        avatar = reserved_copy.get('avatar', {})
                        if not keep_model_paths:
                            for model_type in ('live2d', 'vrm', 'mmd', 'live3d'):
                                if model_type in avatar and isinstance(avatar[model_type], dict):
                                    avatar[model_type].pop('model_path', None)
                        result[key] = _filter_export_fields(reserved_copy, keep_model_paths)
                    elif isinstance(value, dict):
                        result[key] = _filter_export_fields(value, keep_model_paths)
                    elif isinstance(value, list):
                        result[key] = [
                            _filter_export_fields(item, keep_model_paths) if isinstance(item, dict) else item
                            for item in value
                        ]
                    else:
                        result[key] = value
                return result

            chara_json = _filter_export_fields(export_data, keep_model_paths=include_model)
            zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

            # 如果需要包含模型，添加模型文件
            model_added = False
            model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d')

            if include_model:
                if model_type == 'live2d':
                    live2d_path = get_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', default='')
                    if live2d_path and live2d_path.strip():
                        live2d_name = live2d_path.split('/')[0] if '/' in live2d_path else live2d_path.replace('.model3.json', '')
                        if live2d_name and live2d_name != 'mao_pro':
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                if is_user_imported_model(model_dir, _config_manager):
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True

                elif model_type in ('vrm', 'live3d'):
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    if mmd_path and mmd_path.strip():
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True

                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True

            # 添加元数据文件
            metadata = {
                'version': '1.0',
                'export_time': datetime.now().isoformat(),
                'character_name': name,
                'model_included': model_added,
                'model_type': model_type,
                'has_portrait': True
            }
            zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

        # 2. 读取立绘图片（带大小限制和验证）
        MAX_PORTRAIT_SIZE = 50 * 1024 * 1024  # 50 MB
        portrait_data = await portrait.read(MAX_PORTRAIT_SIZE + 1)
        if len(portrait_data) > MAX_PORTRAIT_SIZE:
            return JSONResponse({'success': False, 'error': f'图片大小超过限制 ({MAX_PORTRAIT_SIZE // (1024 * 1024)} MB)'}, status_code=400)

        logger.info(f"[导出角色卡] 接收到立绘图片，大小: {len(portrait_data)} bytes")

        png_path = temp_path / 'character_card.png'

        # 整段 PIL 渲染链（图片校验 + 卡片合成 + 字体扫描 + PNG 编码）放进 worker
        # 线程，避免阻塞事件循环。校验失败用专属异常，让外层回 400 而不是 500。
        def _render_card_png(_portrait_data: bytes, _name: str, _png_path) -> None:
            try:
                Image.MAX_IMAGE_PIXELS = 100_000_000  # 限制最大像素数防止解压炸弹
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.verify()
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.load()  # 强制解码：把截断/损坏的像素错误提前到这里，与 _InvalidPortraitError 一起回 400 而不是后续 resize/save 时回 500
            except Exception as exc:
                raise _InvalidPortraitError(str(exc)) from exc

            logger.info(f"[导出角色卡] 立绘图片尺寸: {portrait_img.size}, 模式: {portrait_img.mode}")

            if portrait_img.mode != 'RGBA':
                portrait_img = portrait_img.convert('RGBA')

            width, height = 600, 800
            card_img = Image.new('RGBA', (width, height), color='#E8F4F8')
            draw = ImageDraw.Draw(card_img)

            header_height = height // 6
            draw.rectangle([0, 0, width, header_height], fill='#40C5F1')

            font_size = 42
            font = None
            font_candidates = [
                ("msyhbd.ttc", font_size),
                ("Microsoft YaHei Bold.ttf", font_size),
                ("simhei.ttf", font_size),
                ("simsun.ttc", font_size),
                ("msyh.ttc", font_size),
                ("Microsoft YaHei.ttf", font_size),
                ("NotoSansCJK-Regular.ttc", font_size),
                ("wqy-microhei.ttc", font_size),
            ]
            for font_name, size in font_candidates:
                try:
                    font = ImageFont.truetype(font_name, size)
                    logger.info(f"[导出角色卡] 使用字体: {font_name}")
                    break
                except Exception as e:
                    logger.warning(f"[导出角色卡] 字体加载失败: {font_name}, 错误: {e}")
                    continue
            if font is None:
                font = ImageFont.load_default()
                logger.warning("[导出角色卡] 使用默认字体")

            bbox = draw.textbbox((0, 0), _name, font=font)
            text_height = bbox[3] - bbox[1]
            text_x = 40
            text_y = (header_height - text_height) // 2 - bbox[1]

            shadow_offset = 2
            draw.text((text_x + shadow_offset, text_y + shadow_offset), _name, fill='#00000040', font=font)
            draw.text((text_x, text_y), _name, fill='white', font=font)

            # 4. 合成立绘到角色卡
            # 立绘区域：紧贴顶部蓝色区域下方到卡片底部，与前端预览一致
            portrait_area_y = header_height
            portrait_area_width = width
            portrait_area_height = height - header_height

            # 前端已按 (width × portrait_area_height) 渲染立绘，直接缩放到目标尺寸后粘贴
            portrait_resized = portrait_img.resize((portrait_area_width, portrait_area_height), Image.Resampling.LANCZOS)
            logger.info(f"[导出角色卡] 立绘调整后尺寸: {portrait_resized.size}, 粘贴位置: (0, {portrait_area_y})")

            # 粘贴立绘（使用alpha通道）
            card_img.paste(portrait_resized, (0, portrait_area_y), portrait_resized)
            logger.info("[导出角色卡] 立绘粘贴完成")

            final_img = Image.new('RGB', (width, height), color='#E8F4F8')
            final_img.paste(card_img, (0, 0), card_img)

            final_img.save(_png_path, 'PNG')

        try:
            await asyncio.to_thread(_render_card_png, portrait_data, name, png_path)
        except _InvalidPortraitError as e:
            logger.warning(f"[导出角色卡] 图片验证失败: {e}")
            return JSONResponse({'success': False, 'error': f'无效的图片文件: {str(e)}'}, status_code=400)

        # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
        with open(png_path, 'rb') as f:
            png_data = f.read()

        with open(zip_path, 'rb') as f:
            zip_data = f.read()

        combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

        # 7. 返回图片文件
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}.png"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_card.png"

        return Response(
            content=combined_data,
            media_type='image/png',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出带立绘的角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)
    finally:
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)

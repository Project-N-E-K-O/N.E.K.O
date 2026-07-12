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

"""Workshop meta files, deleted-character tombstones, model-ref binding
and content hashing.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router

import os
import json
import asyncio
from datetime import datetime
from urllib.parse import unquote
from fastapi.responses import JSONResponse
from ..shared_state import get_config_manager
from utils.cloudsave_runtime import is_cloudsave_disabled
from utils.file_utils import atomic_write_json
import hashlib


_session_deleted_names: set[str] = set()


def mark_session_deleted_character_name(character_name: str) -> bool:
    normalized_name = str(character_name or "").strip()
    if not normalized_name:
        return False
    _session_deleted_names.add(normalized_name)
    return True


def _read_first_line(path: str, encoding: str = 'utf-8') -> str:
    """Synchronously read a file's first line, called via asyncio.to_thread (README.md / README.txt metadata fallback)."""
    with open(path, 'r', encoding=encoding) as f:
        return f.readline()


def _load_deleted_character_names(config_mgr) -> set[str]:
    deleted_names: set[str] = set(_session_deleted_names)
    if is_cloudsave_disabled():
        return deleted_names

    try:
        tombstone_state = config_mgr.load_character_tombstones_state()
    except Exception as exc:
        logger.warning(f"sync_workshop_character_cards: 读取 tombstone 状态失败: {exc}")
        return deleted_names

    for entry in tombstone_state.get("tombstones") or []:
        if not isinstance(entry, dict):
            continue
        character_name = str(entry.get("character_name") or "").strip()
        if character_name:
            deleted_names.add(character_name)
    return deleted_names


def _remove_deleted_character_tombstones(config_mgr, character_names: list[str]) -> list[str]:
    """Remove the tombstones of manually restored characters, so later syncs stop treating them as deleted."""
    target_names = {str(name or "").strip() for name in character_names}
    target_names.discard("")
    if not target_names:
        return []

    session_removed_names = sorted(name for name in target_names if name in _session_deleted_names)
    _session_deleted_names.difference_update(target_names)

    if is_cloudsave_disabled():
        return session_removed_names

    tombstone_state = config_mgr.load_character_tombstones_state()
    original_entries = tombstone_state.get("tombstones") or []
    remaining_entries = []
    removed_names: list[str] = []

    for entry in original_entries:
        if not isinstance(entry, dict):
            remaining_entries.append(entry)
            continue
        character_name = str(entry.get("character_name") or "").strip()
        if character_name in target_names:
            removed_names.append(character_name)
            continue
        remaining_entries.append(entry)

    if not removed_names:
        return session_removed_names

    config_mgr.save_character_tombstones_state({
        "version": getattr(config_mgr, "CHARACTER_TOMBSTONES_STATE_VERSION", 1),
        "tombstones": remaining_entries,
    })
    return sorted(set(session_removed_names) | set(removed_names))


def _write_deleted_character_tombstone(config_mgr, character_name: str, build_tombstone_state) -> bool:
    mark_session_deleted_character_name(character_name)
    if is_cloudsave_disabled():
        return False

    tombstone_state = build_tombstone_state(config_mgr, character_name)
    config_mgr.save_character_tombstones_state(tombstone_state)
    return True


def _derive_workshop_origin_display_name(raw_model_name: str, fallback_name: str) -> str:
    normalized_name = str(raw_model_name or "").strip().replace("\\", "/")
    if not normalized_name:
        return str(fallback_name or "").strip()
    if "/" in normalized_name:
        normalized_name = normalized_name.rsplit("/", 1)[-1]
    lower_name = normalized_name.lower()
    for suffix in (".model3.json", ".vrm", ".pmx", ".pmd"):
        if lower_name.endswith(suffix):
            normalized_name = normalized_name[:-len(suffix)]
            break
    return normalized_name or str(fallback_name or "").strip()


def _normalize_workshop_model_ref(raw_value: str) -> str:
    return str(raw_value or "").strip().replace("\\", "/")


def _sanitize_workshop_ref_segments(segments: list[str]) -> list[str]:
    # 工坊卡的 model 路径来自第三方物品内容，是跨信任边界输入。这里在摄入点
    # 丢弃 '..'/'.'/盘符段，保证拼出的 model_ref 无法解析出 /workshop/{item_id}/ 之外。
    return [
        segment
        for segment in segments
        if segment and segment not in ("..", ".") and ":" not in segment
    ]


def _build_subscriber_workshop_model_ref(item_id: str | int, raw_model_ref: str) -> str:
    normalized_ref = _normalize_workshop_model_ref(raw_model_ref)
    normalized_item_id = str(item_id or "").strip()
    if not normalized_ref or not normalized_item_id:
        return normalized_ref
    if normalized_ref.startswith("/workshop/"):
        parts = [segment for segment in normalized_ref.strip("/").split("/") if segment]
        # /workshop/{old_item_id}/...
        if parts and parts[0] == "workshop":
            tail_parts = _sanitize_workshop_ref_segments(parts[2:] if len(parts) >= 2 else [])
            if tail_parts:
                return f"/workshop/{normalized_item_id}/{'/'.join(tail_parts)}"
            return f"/workshop/{normalized_item_id}"
    relative_parts = _sanitize_workshop_ref_segments(normalized_ref.split("/"))
    if not relative_parts:
        return f"/workshop/{normalized_item_id}"
    return f"/workshop/{normalized_item_id}/{'/'.join(relative_parts)}"


def _derive_workshop_model_binding(chara_data: dict) -> dict[str, str]:
    legacy_live2d_name = _normalize_workshop_model_ref(chara_data.get("live2d"))
    vrm_model_path = _normalize_workshop_model_ref(chara_data.get("vrm"))
    mmd_model_path = _normalize_workshop_model_ref(chara_data.get("mmd"))

    if legacy_live2d_name:
        lower_legacy_model = legacy_live2d_name.lower()
        if not vrm_model_path and lower_legacy_model.endswith(".vrm"):
            vrm_model_path = legacy_live2d_name
            legacy_live2d_name = ""
        elif not mmd_model_path and lower_legacy_model.endswith((".pmx", ".pmd")):
            mmd_model_path = legacy_live2d_name
            legacy_live2d_name = ""

    if mmd_model_path:
        return {
            "binding_model_type": "mmd",
            "stored_model_type": "live3d",
            "model_ref": mmd_model_path,
            "display_name_source": mmd_model_path,
        }

    if vrm_model_path:
        return {
            "binding_model_type": "vrm",
            "stored_model_type": "live3d",
            "model_ref": vrm_model_path,
            "display_name_source": vrm_model_path,
        }

    live2d_model_path = ""
    if legacy_live2d_name:
        if "/" in legacy_live2d_name or legacy_live2d_name.endswith(".model3.json"):
            live2d_model_path = legacy_live2d_name
        else:
            live2d_model_path = f"{legacy_live2d_name}/{legacy_live2d_name}.model3.json"

    return {
        "binding_model_type": "live2d",
        "stored_model_type": "live2d",
        "model_ref": live2d_model_path,
        "display_name_source": legacy_live2d_name or live2d_model_path,
    }


def get_workshop_meta_path(character_card_name: str) -> str:
    """
    Get the path of a character card's .workshop_meta.json file
    
    Args:
        character_card_name: character card name (without the .chara.json suffix)
    
    Returns:
        str: full path of the .workshop_meta.json file
    
    Raises:
        ValueError: if character_card_name contains path traversal characters
    """
    # 防路径穿越:只允许角色卡名称,不允许携带路径或上级目录喵
    if not character_card_name:
        raise ValueError("角色卡名称不能为空")
    
    # 使用 basename 提取纯名称，去除任何路径组件
    safe_name = os.path.basename(character_card_name)
    
    # 验证：检查是否包含路径分隔符、.. 或与原始输入不一致
    if (safe_name != character_card_name or 
        ".." in safe_name or 
        os.path.sep in safe_name or 
        "/" in safe_name or 
        "\\" in safe_name):
        logger.warning(f"检测到非法角色卡名称尝试: {character_card_name}")
        raise ValueError("非法角色卡名称: 不能包含路径分隔符或目录遍历字符")
    
    config_mgr = get_config_manager()
    chara_dir = config_mgr.chara_dir
    
    # 构建文件路径
    meta_file_path = os.path.join(chara_dir, f"{safe_name}.workshop_meta.json")
    
    # 额外安全检查：验证最终路径确实在 chara_dir 内
    try:
        real_meta_path = os.path.realpath(meta_file_path)
        real_chara_dir = os.path.realpath(chara_dir)
        # 使用 commonpath 确保路径在基础目录内
        if os.path.commonpath([real_meta_path, real_chara_dir]) != real_chara_dir:
            logger.warning(f"路径遍历尝试被阻止: {character_card_name} -> {meta_file_path}")
            raise ValueError("路径验证失败: 目标路径不在允许的目录内")
    except (ValueError, OSError) as e:
        logger.warning(f"路径验证失败: {e}")
        raise ValueError("路径验证失败")
    
    return meta_file_path


def read_workshop_meta(character_card_name: str) -> dict:
    """
    Read a character card's .workshop_meta.json file
    
    Args:
        character_card_name: character card name (without the .chara.json suffix)
    
    Returns:
        dict: metadata dict, or None if the file does not exist or validation failed
    """
    try:
        meta_file_path = get_workshop_meta_path(character_card_name)
    except ValueError as e:
        logger.warning(f"角色卡名称验证失败: {e}")
        return None
    
    if os.path.exists(meta_file_path):
        try:
            with open(meta_file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取 .workshop_meta.json 失败: {e}")
            return None
    return None


def write_workshop_meta(character_card_name: str, workshop_item_id: str, content_hash: str = None, uploaded_snapshot: dict = None):
    """
    Write or update a character card's .workshop_meta.json file
    
    Args:
        character_card_name: character card name (without the .chara.json suffix)
        workshop_item_id: Workshop item ID
        content_hash: content hash (optional)
        uploaded_snapshot: snapshot data at upload time (optional), containing description, tags, model_name, character_data
    
    Raises:
        ValueError: if the character card name fails validation
    """
    try:
        meta_file_path = get_workshop_meta_path(character_card_name)
    except ValueError as e:
        logger.error(f"写入 .workshop_meta.json 失败: 角色卡名称验证失败 - {e}")
        raise
    
    # 读取现有数据（如果存在）
    existing_meta = read_workshop_meta(character_card_name) or {}
    
    # 更新数据
    now = datetime.utcnow().isoformat() + 'Z'
    if 'created_at' not in existing_meta:
        existing_meta['created_at'] = now
    existing_meta['workshop_item_id'] = str(workshop_item_id)
    existing_meta['last_update'] = now
    if content_hash:
        existing_meta['content_hash'] = content_hash
    
    # 保存上传快照
    if uploaded_snapshot:
        existing_meta['uploaded_snapshot'] = uploaded_snapshot
    
    # 写入文件
    try:
        atomic_write_json(meta_file_path, existing_meta, ensure_ascii=False, indent=2)
        logger.info(f"已更新 .workshop_meta.json: {meta_file_path}")
    except Exception as e:
        logger.error(f"写入 .workshop_meta.json 失败: {e}")


def calculate_content_hash(content_folder: str) -> str:
    """
    Compute the hash of a content folder
    
    Args:
        content_folder: content folder path
    
    Returns:
        str: SHA256 hash (format: sha256:xxxx)
    """
    sha256_hash = hashlib.sha256()
    
    # 收集所有文件路径并排序（确保一致性）
    file_paths = []
    for root, dirs, files in os.walk(content_folder):
        # 排除 .workshop_meta.json 文件（如果存在）
        if '.workshop_meta.json' in files:
            files.remove('.workshop_meta.json')
        for file in files:
            file_path = os.path.join(root, file)
            file_paths.append(file_path)
    
    file_paths.sort()
    
    # 计算所有文件的哈希值
    for file_path in file_paths:
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    sha256_hash.update(chunk)
        except Exception as e:
            logger.warning(f"计算文件哈希时出错 {file_path}: {e}")
    
    return f"sha256:{sha256_hash.hexdigest()}"


def get_folder_size(folder_path):
    """Get folder size (in bytes)."""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(filepath)
            except (OSError, FileNotFoundError):
                continue
    return total_size


@router.get('/meta/{character_name}')
async def get_workshop_meta(character_name: str):
    """
    Get a character card's Workshop metadata (including upload status and snapshot)
    
    Args:
        character_name: character card name (URL-encoded)
    
    Returns:
        JSON: contains workshop_item_id, uploaded_snapshot, etc.
    """
    try:
        # URL 解码
        decoded_name = unquote(character_name)
        
        # 读取元数据
        meta_data = await asyncio.to_thread(read_workshop_meta, decoded_name)
        
        if meta_data:
            return JSONResponse(content={
                "success": True,
                "has_uploaded": bool(meta_data.get('workshop_item_id')),
                "meta": meta_data
            })
        else:
            return JSONResponse(content={
                "success": True,
                "has_uploaded": False,
                "meta": None
            })
    except ValueError as e:
        logger.warning(f"获取 Workshop 元数据失败: {e}")
        return JSONResponse(content={
            "success": False,
            "error": str(e)
        }, status_code=400)
    except Exception as e:
        logger.error(f"获取 Workshop 元数据时出错: {e}")
        return JSONResponse(content={
            "success": False,
            "error": "内部错误"
        }, status_code=500)

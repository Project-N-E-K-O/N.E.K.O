# -*- coding: utf-8 -*-
"""
Workshop Router

Handles Steam Workshop-related endpoints including:
- Subscribed items management
- Item publishing
- Workshop configuration
- Local items management
"""

import os
import sys
import json
import time
import tempfile
import asyncio
import threading
import mimetypes
import platform
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .shared_state import get_steamworks, get_config_manager, get_initialize_character_data
from utils.cloudsave_runtime import MaintenanceModeError, is_write_fence_active
from utils.file_utils import atomic_write_json, atomic_write_json_async, read_json_async
from utils.workshop_utils import (
    ensure_workshop_folder_exists,
    get_workshop_path,
)
from utils.logger_config import get_module_logger
from utils.config_manager import get_reserved, set_reserved
from config import CHARACTER_RESERVED_FIELDS
import hashlib

router = APIRouter(prefix="/api/steam/workshop", tags=["workshop"])
# 全局互斥锁，用于序列化创意工坊发布操作，防止并发回调混乱
publish_lock = threading.Lock()
logger = get_module_logger(__name__, "Main")

# ─── UGC 查询结果缓存 ──────────────────────────────────────────────────
# Steam 的 k_UGCQueryHandleInvalid = 0xFFFFFFFFFFFFFFFF
_INVALID_UGC_QUERY_HANDLE = 0xFFFFFFFFFFFFFFFF

# 缓存 { publishedFileId(int): { title, description, ..., _cache_ts: float } }
# 每个条目带有独立的 _cache_ts 时间戳，用于按条目粒度判断 TTL
_ugc_details_cache: dict[int, dict] = {}
_UGC_CACHE_TTL = 300  # 缓存有效期 5 分钟
_ugc_warmup_task = None  # 后台预热任务
_ugc_sync_task = None    # 后台角色卡同步任务

# 全局互斥锁，用于序列化角色卡同步的 load_characters -> save_characters 流程
_ugc_sync_lock = asyncio.Lock()

# 全局互斥锁，用于序列化 UGC 批量查询（CreateQuery → SendQuery → 回调），
# 避免并发调用 override_callback=True 导致回调覆盖竞态
_ugc_query_lock = asyncio.Lock()
WORKSHOP_VOICE_MANIFEST_NAME = 'voice_manifest.json'
WORKSHOP_REFERENCE_AUDIO_EXTENSIONS = {'.mp3', '.wav'}
WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES = {
    'audio/mpeg': '.mp3',
    'audio/mp3': '.mp3',
    'audio/wav': '.wav',
    'audio/wave': '.wav',
    'audio/x-wav': '.wav',
    'audio/x-pn-wav': '.wav',
}
WORKSHOP_REFERENCE_LANGUAGES = {'ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru'}
WORKSHOP_REFERENCE_PROVIDER_HINTS = {'cosyvoice', 'minimax', 'minimax_intl'}
WORKSHOP_CARD_FACE_SIZE = (768, 1024)
WORKSHOP_CARD_FACE_PADDING = 48
WORKSHOP_CARD_FACE_RATIO_TOLERANCE = 0.02
WORKSHOP_CARD_FACE_MARKER_KEY = 'neko_workshop_card_face'
WORKSHOP_CARD_FACE_MARKER_VALUE = 'steam_preview_v1'


async def cancel_background_tasks(*, timeout: float = 5.0) -> None:
    for task_attr in ("_ugc_warmup_task", "_ugc_sync_task"):
        task = globals().get(task_attr)
        if task is None:
            continue
        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("workshop %s finished with error during cleanup: %s", task_attr, exc, exc_info=True)
        else:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                logger.debug("workshop %s cancelled", task_attr)
            except asyncio.TimeoutError:
                logger.warning("workshop %s did not stop within %.1fs", task_attr, timeout)
            except Exception as exc:
                logger.debug("workshop %s cleanup failed: %s", task_attr, exc, exc_info=True)
        if globals().get(task_attr) is task:
            globals()[task_attr] = None


def _read_first_line(path: str, encoding: str = 'utf-8') -> str:
    """同步读文件首行，供 asyncio.to_thread 调用（README.md / README.txt 元数据回退）。"""
    with open(path, 'r', encoding=encoding) as f:
        return f.readline()


def _load_deleted_character_names(config_mgr) -> set[str]:
    deleted_names: set[str] = set()
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


def _build_subscriber_workshop_model_ref(item_id: str | int, raw_model_ref: str) -> str:
    normalized_ref = _normalize_workshop_model_ref(raw_model_ref)
    normalized_item_id = str(item_id or "").strip()
    if not normalized_ref or not normalized_item_id:
        return normalized_ref
    if normalized_ref.startswith("/workshop/"):
        parts = [segment for segment in normalized_ref.strip("/").split("/") if segment]
        # /workshop/{old_item_id}/...
        if parts and parts[0] == "workshop":
            tail_parts = parts[2:] if len(parts) >= 2 else []
            if tail_parts:
                return f"/workshop/{normalized_item_id}/{'/'.join(tail_parts)}"
            return f"/workshop/{normalized_item_id}"
    relative_ref = normalized_ref.strip("/")
    if not relative_ref:
        return f"/workshop/{normalized_item_id}"
    return f"/workshop/{normalized_item_id}/{relative_ref}"


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


def _is_item_cache_valid(item_id: int) -> bool:
    """检查单个 UGC 缓存条目是否在有效期内"""
    entry = _ugc_details_cache.get(item_id)
    if not entry:
        return False
    return (time.time() - entry.get('_cache_ts', 0)) < _UGC_CACHE_TTL


def _all_items_cache_valid(item_ids: list[int]) -> bool:
    """检查所有给定物品 ID 的缓存是否均在有效期内"""
    if not _ugc_details_cache:
        return False
    return all(_is_item_cache_valid(iid) for iid in item_ids)


async def _query_ugc_details_batch(steamworks, item_ids: list[int], max_retries: int = 2) -> dict[int, object]:
    """
    批量查询 UGC 物品详情，带重试逻辑。
    
    Args:
        steamworks: Steamworks 实例
        item_ids: 物品 ID 列表（整数）
        max_retries: 最大重试次数
    
    Returns:
        dict: { publishedFileId(int): SteamUGCDetails_t }
    """
    if not item_ids:
        return {}
    
    for attempt in range(max_retries):
        try:
            # 在发送查询前先泵一次回调，清除可能的残留状态
            try:
                steamworks.run_callbacks()
            except Exception as e:
                logger.debug(f"run_callbacks (pre-query pump) 异常: {e}")
            
            # 序列化整个查询流程：CreateQuery → SendQuery(override_callback) → 等待回调 → 读取结果
            # 避免并发调用时 override_callback=True 导致前一次的回调被覆盖
            async with _ugc_query_lock:
                query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest(item_ids)
                
                # 检查无效 handle（0 或 k_UGCQueryHandleInvalid）
                if not query_handle or query_handle == _INVALID_UGC_QUERY_HANDLE:
                    logger.warning(f"UGC 批量查询: CreateQueryUGCDetailsRequest 返回无效 handle "
                                  f"(attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                
                # 回调+轮询机制（每次迭代创建独立的 Event 和 dict，通过默认参数绑定避免闭包晚绑定）
                query_completed = threading.Event()
                query_result_info = {"success": False, "num_results": 0}
                
                def _make_callback(_info=query_result_info, _event=query_completed):
                    def on_query_completed(result):
                        try:
                            _info["success"] = (result.result == 1)
                            _info["num_results"] = int(result.numResultsReturned)
                            logger.info(f"UGC 查询回调: result={result.result}, numResults={result.numResultsReturned}")
                        except Exception as e:
                            logger.warning(f"UGC 查询回调处理出错: {e}")
                        finally:
                            _event.set()
                    return on_query_completed
                
                steamworks.Workshop.SendQueryUGCRequest(
                    query_handle, callback=_make_callback(), override_callback=True
                )
                
                # 轮询等待（10ms 间隔，最多 15 秒）
                start_time = time.time()
                timeout = 15
                while time.time() - start_time < timeout:
                    if query_completed.is_set():
                        break
                    try:
                        steamworks.run_callbacks()
                    except Exception as e:
                        logger.debug(f"run_callbacks (polling) 异常: {e}")
                    await asyncio.sleep(0.01)
            
            if not query_completed.is_set():
                logger.warning(f"UGC 批量查询超时 (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                continue
            
            if not query_result_info["success"]:
                logger.warning(f"UGC 批量查询失败: result_info={query_result_info} "
                              f"(attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            
            # 提取结果
            num_results = query_result_info["num_results"]
            results = {}
            for i in range(num_results):
                try:
                    res = steamworks.Workshop.GetQueryUGCResult(query_handle, i)
                    if res and res.publishedFileId:
                        results[int(res.publishedFileId)] = res
                except Exception as e:
                    logger.warning(f"获取第 {i} 个 UGC 查询结果失败: {e}")
            
            logger.info(f"UGC 批量查询成功: {len(results)}/{len(item_ids)} 个物品 "
                        f"(attempt {attempt + 1})")
            
            # 查询完成后泵一次回调，让 Steam 缓存 persona 数据
            try:
                steamworks.run_callbacks()
            except Exception as e:
                logger.debug(f"run_callbacks (post-query pump) 异常: {e}")
            
            return results
        
        except Exception as e:
            logger.warning(f"UGC 批量查询异常: {e} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    
    logger.error("UGC 批量查询在所有重试后仍失败")
    return {}


def _resolve_author_name(steamworks, owner_id: int) -> str | None:
    """
    将 Steam ID 解析为显示名称。
    
    Returns:
        str | None: 用户名或 None（解析失败时）
    """
    if not owner_id:
        return None
    try:
        persona_name = steamworks.Friends.GetFriendPersonaName(owner_id)
        if persona_name:
            if isinstance(persona_name, bytes):
                persona_name = persona_name.decode('utf-8', errors='replace')
            # 过滤空串和纯数字 ID；保留 [unknown] 作为合法 fallback
            if persona_name and persona_name.strip() and persona_name != str(owner_id):
                return persona_name.strip()
    except Exception as e:
        logger.debug(f"解析 Steam ID {owner_id} 名称失败: {e}")
    return None


def _safe_text(value) -> str:
    """将 bytes/str/None 统一转为安全的 UTF-8 字符串。"""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _extract_ugc_item_details(steamworks, item_id_int: int, result, item_info: dict) -> None:
    """
    从 UGC 查询结果(SteamUGCDetails_t)提取物品详情，填充到 item_info 字典。
    同时更新全局缓存（按条目粒度记录时间戳）。
    """
    global _ugc_details_cache
    
    try:
        if hasattr(result, 'title') and result.title:
            item_info['title'] = _safe_text(result.title)
        if hasattr(result, 'description') and result.description:
            item_info['description'] = _safe_text(result.description)
        # timeAddedToUserList 是用户订阅时间，timeCreated 是物品创建时间，分开存储避免语义混淆
        if hasattr(result, 'timeCreated') and result.timeCreated:
            item_info['timeCreated'] = int(result.timeCreated)
        if hasattr(result, 'timeAddedToUserList') and result.timeAddedToUserList:
            item_info['timeAdded'] = int(result.timeAddedToUserList)
        if hasattr(result, 'timeUpdated') and result.timeUpdated:
            item_info['timeUpdated'] = int(result.timeUpdated)
        if hasattr(result, 'steamIDOwner') and result.steamIDOwner:
            owner_id = int(result.steamIDOwner)
            item_info['steamIDOwner'] = str(owner_id)
            author_name = _resolve_author_name(steamworks, owner_id)
            if author_name:
                item_info['authorName'] = author_name
        if hasattr(result, 'fileSize') and result.fileSize:
            item_info['fileSizeOnDisk'] = int(result.fileSize)
        # 提取标签
        if hasattr(result, 'tags') and result.tags:
            try:
                tags_str = _safe_text(result.tags)
                if tags_str:
                    item_info['tags'] = [t.strip() for t in tags_str.split(',') if t.strip()]
            except Exception as e:
                logger.debug(f"解析 UGC 物品 {item_id_int} 标签失败: {e}")
        
        # 更新缓存
        cache_entry = {}
        for key in ('title', 'description', 'timeCreated', 'timeAdded', 'timeUpdated',
                     'steamIDOwner', 'authorName', 'tags'):
            if key in item_info:
                cache_entry[key] = item_info[key]
        if cache_entry:
            cache_entry['_cache_ts'] = time.time()
            _ugc_details_cache[item_id_int] = cache_entry
        
        logger.debug(f"提取物品 {item_id_int} 详情: title={item_info.get('title', '?')}")
    except Exception as detail_error:
        logger.warning(f"提取物品 {item_id_int} 详情时出错: {detail_error}")


async def warmup_ugc_cache() -> None:
    """
    在服务器启动时后台预热 UGC 缓存。
    
    获取所有订阅物品 ID，执行一次批量 UGC 查询，将结果存入缓存。
    之后前端首次请求 /subscribed-items 时可以直接命中缓存，无需等待 Steam 网络查询。
    """
    global _ugc_warmup_task
    
    steamworks = get_steamworks()
    if steamworks is None:
        return
    
    try:
        num_items = steamworks.Workshop.GetNumSubscribedItems()
        if num_items == 0:
            logger.info("UGC 缓存预热: 没有订阅物品，跳过")
            return
        
        subscribed_ids = steamworks.Workshop.GetSubscribedItems()
        all_item_ids = []
        for sid in subscribed_ids:
            try:
                all_item_ids.append(int(sid))
            except (ValueError, TypeError):
                continue
        
        if not all_item_ids:
            return
        
        logger.info(f"UGC 缓存预热: 开始查询 {len(all_item_ids)} 个物品...")
        ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=3)
        
        if ugc_results:
            # 将结果写入缓存
            for item_id_int, result in ugc_results.items():
                dummy_info = {"publishedFileId": str(item_id_int),
                              "title": f"未知物品_{item_id_int}", "description": ""}
                _extract_ugc_item_details(steamworks, item_id_int, result, dummy_info)
            
            logger.info(f"UGC 缓存预热完成: {len(_ugc_details_cache)} 个物品已缓存")
        else:
            logger.warning("UGC 缓存预热: 批量查询无结果")
    except Exception as e:
        logger.warning(f"UGC 缓存预热失败（不影响正常使用）: {e}")
    finally:
        _ugc_warmup_task = None


def get_workshop_meta_path(character_card_name: str) -> str:
    """
    获取角色卡的 .workshop_meta.json 文件路径
    
    Args:
        character_card_name: 角色卡名称（不含 .chara.json 后缀）
    
    Returns:
        str: .workshop_meta.json 文件的完整路径
    
    Raises:
        ValueError: 如果 character_card_name 包含路径遍历字符
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
    读取角色卡的 .workshop_meta.json 文件
    
    Args:
        character_card_name: 角色卡名称（不含 .chara.json 后缀）
    
    Returns:
        dict: 元数据字典，如果文件不存在或验证失败则返回 None
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
    写入或更新角色卡的 .workshop_meta.json 文件
    
    Args:
        character_card_name: 角色卡名称（不含 .chara.json 后缀）
        workshop_item_id: Workshop 物品 ID
        content_hash: 内容哈希值（可选）
        uploaded_snapshot: 上传时的快照数据（可选），包含 description、tags、model_name、character_data
    
    Raises:
        ValueError: 如果角色卡名称验证失败
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
    计算内容文件夹的哈希值
    
    Args:
        content_folder: 内容文件夹路径
    
    Returns:
        str: SHA256 哈希值（格式：sha256:xxxx）
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
    """获取文件夹大小（字节）"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(filepath)
            except (OSError, FileNotFoundError):
                continue
    return total_size


def find_preview_image_in_folder(folder_path):
    """在文件夹中查找预览图片，只查找指定的8个图片名称"""
    preview_image_names = ['preview.jpg', 'preview.png', 'thumbnail.jpg', 'thumbnail.png', 
                         'icon.jpg', 'icon.png', 'header.jpg', 'header.png']
    
    for image_name in preview_image_names:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            return image_path
    
    return None


def _build_workshop_card_face_meta(item: dict) -> dict:
    workshop_author = ''
    try:
        workshop_author = str(item.get('authorName') or item.get('author') or item.get('creatorName') or '').strip()[:64]
    except Exception:
        workshop_author = ''

    now_iso = datetime.utcnow().isoformat() + 'Z'
    return {
        'author': workshop_author,
        'origin': 'steam',
        'created_at': now_iso,
        'updated_at': now_iso,
    }


def _read_card_face_origin(meta_path: Path) -> str | None:
    """Read the persisted card-face origin marker from the sidecar file."""
    try:
        if not meta_path.exists():
            return None
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        origin = str(data.get('origin', '') or '').strip()
        return origin or None
    except Exception:
        return None


def _is_workshop_card_face_normalized(face_path: Path) -> bool:
    """Return True when the existing face already matches the workshop 3:4 derivative shape."""
    if not face_path.exists():
        return False

    from PIL import Image as PILImage

    try:
        with PILImage.open(face_path) as img:
            width, height = img.size
    except Exception:
        return False

    if width <= 0 or height <= 0:
        return False

    target_ratio = WORKSHOP_CARD_FACE_SIZE[0] / WORKSHOP_CARD_FACE_SIZE[1]
    current_ratio = width / height
    return abs(current_ratio - target_ratio) <= WORKSHOP_CARD_FACE_RATIO_TOLERANCE


def _should_refresh_workshop_card_face(face_path: Path, meta_path: Path) -> bool:
    """Decide whether a workshop preview is allowed to replace the current card face."""
    if not face_path.exists():
        return True

    origin = _read_card_face_origin(meta_path)
    if origin is None:
        # sidecar 缺失时默认保护现有自定义 PNG；但如果卡面带有本地生成的
        # Workshop marker，说明它是渲染中断后留下的孤儿文件，允许后续重试。
        return _has_workshop_card_face_marker(face_path)

    if origin in {'self', 'imported'}:
        return False

    return not _is_workshop_card_face_normalized(face_path)


def _render_workshop_card_face_image(img):
    """Render a workshop preview into the normalized 3:4 in-app card-face layout."""
    from PIL import Image as PILImage, ImageFilter, ImageOps

    resampling = getattr(PILImage, 'Resampling', PILImage)
    lanczos = getattr(resampling, 'LANCZOS', PILImage.BICUBIC)

    working = ImageOps.exif_transpose(img).convert('RGBA')

    canvas = PILImage.new('RGBA', WORKSHOP_CARD_FACE_SIZE, (231, 245, 255, 255))
    background = ImageOps.fit(
        working,
        WORKSHOP_CARD_FACE_SIZE,
        method=lanczos,
        centering=(0.5, 0.5),
    )
    background = background.filter(ImageFilter.GaussianBlur(radius=28))
    canvas = PILImage.blend(canvas, background, 0.82)
    canvas = PILImage.alpha_composite(
        canvas,
        PILImage.new('RGBA', WORKSHOP_CARD_FACE_SIZE, (255, 255, 255, 30)),
    )

    foreground = working.copy()
    foreground.thumbnail(
        (
            max(64, WORKSHOP_CARD_FACE_SIZE[0] - WORKSHOP_CARD_FACE_PADDING * 2),
            max(64, WORKSHOP_CARD_FACE_SIZE[1] - WORKSHOP_CARD_FACE_PADDING * 2),
        ),
        resample=lanczos,
    )
    foreground = ImageOps.expand(foreground, border=8, fill=(255, 255, 255, 28))

    offset_x = (WORKSHOP_CARD_FACE_SIZE[0] - foreground.width) // 2
    offset_y = (WORKSHOP_CARD_FACE_SIZE[1] - foreground.height) // 2
    canvas.alpha_composite(foreground, (offset_x, offset_y))
    return canvas


def _has_workshop_card_face_marker(face_path: Path) -> bool:
    """Detect workshop-generated preview PNGs even if the sidecar is missing."""
    try:
        from PIL import Image as PILImage

        with PILImage.open(face_path) as img:
            return str(img.info.get(WORKSHOP_CARD_FACE_MARKER_KEY, '') or '') == WORKSHOP_CARD_FACE_MARKER_VALUE
    except Exception:
        return False


def _is_matching_workshop_character(catgirl_data: dict, item_id) -> bool:
    if not isinstance(catgirl_data, dict):
        return False

    try:
        source = str(get_reserved(catgirl_data, 'character_origin', 'source', default='') or '').strip()
        if source != 'steam_workshop':
            return False

        current_item_id = str(item_id or '').strip()
        source_id = str(get_reserved(catgirl_data, 'character_origin', 'source_id', default='') or '').strip()
        if not current_item_id or not source_id:
            return False
        return source_id == current_item_id
    except Exception:
        return False


def _ensure_workshop_card_face_from_preview(
    config_mgr,
    chara_name: str,
    preview_image_path: str | None,
    item: dict | None = None,
) -> bool:
    """Create or refresh a workshop-derived card face from the Steam preview image."""
    if not preview_image_path or not os.path.isfile(preview_image_path):
        return False
    if not config_mgr.ensure_card_faces_directory():
        return False

    face_path = config_mgr.card_faces_dir / f"{chara_name}.png"
    meta_path = config_mgr.card_face_meta_path(chara_name)
    if not _should_refresh_workshop_card_face(face_path, meta_path):
        return False

    from PIL import Image as PILImage
    from PIL import PngImagePlugin

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{face_path.name}.",
        suffix=".tmp",
        dir=str(face_path.parent),
    )

    try:
        with os.fdopen(fd, 'w+b') as temp_file:
            with PILImage.open(preview_image_path) as img:
                normalized = _render_workshop_card_face_image(img)
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text(WORKSHOP_CARD_FACE_MARKER_KEY, WORKSHOP_CARD_FACE_MARKER_VALUE)
                normalized.save(temp_file, format='PNG', optimize=True, pnginfo=pnginfo)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, face_path)
        if item and not meta_path.exists():
            atomic_write_json(meta_path, _build_workshop_card_face_meta(item), ensure_ascii=False, indent=2)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise

    return True


def _ensure_workshop_card_face_meta(config_mgr, chara_name: str, item: dict) -> bool:
    """Persist sidecar metadata for workshop-generated card faces when missing."""
    if not config_mgr.ensure_card_faces_directory():
        return False

    face_path = config_mgr.card_faces_dir / f"{chara_name}.png"
    if not face_path.exists() or not _has_workshop_card_face_marker(face_path):
        return False

    meta_path = config_mgr.card_face_meta_path(chara_name)
    if meta_path.exists():
        return False

    atomic_write_json(meta_path, _build_workshop_card_face_meta(item), ensure_ascii=False, indent=2)
    return True


def _sanitize_voice_prefix(prefix: str, default_prefix: str = 'voice') -> str:
    normalized = ''.join(ch for ch in str(prefix or '') if ch.isascii() and ch.isalnum())[:10]
    if normalized:
        return normalized
    fallback = ''.join(ch for ch in str(default_prefix or '') if ch.isascii() and ch.isalnum())[:10]
    return fallback or 'voice'


def _normalize_workshop_voice_manifest(raw_manifest: dict, *, default_prefix: str = 'voice',
                                       default_display_name: str = '') -> dict:
    if not isinstance(raw_manifest, dict):
        raise ValueError('voice_manifest.json 格式无效')

    reference_audio = os.path.basename(str(raw_manifest.get('reference_audio', '')).strip())
    if not reference_audio:
        raise ValueError('voice_manifest.json 缺少 reference_audio')

    audio_ext = os.path.splitext(reference_audio)[1].lower()
    if audio_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
        raise ValueError('参考语音格式只支持 mp3 或 wav')

    prefix = _sanitize_voice_prefix(raw_manifest.get('prefix', ''), default_prefix=default_prefix)

    ref_language = str(raw_manifest.get('ref_language', 'ch') or 'ch').strip().lower()
    if ref_language not in WORKSHOP_REFERENCE_LANGUAGES:
        ref_language = 'ch'

    provider_hint = str(raw_manifest.get('provider_hint', 'cosyvoice') or 'cosyvoice').strip().lower()
    if provider_hint not in WORKSHOP_REFERENCE_PROVIDER_HINTS:
        provider_hint = 'cosyvoice'

    display_name = str(raw_manifest.get('display_name', '') or '').strip()
    if not display_name:
        display_name = str(default_display_name or prefix).strip() or prefix

    version = raw_manifest.get('version', 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1

    return {
        'version': version,
        'reference_audio': reference_audio,
        'prefix': prefix,
        'ref_language': ref_language,
        'display_name': display_name,
        'provider_hint': provider_hint,
    }


def _resolve_workshop_voice_reference(item_dir: str) -> dict | None:
    manifest_path = os.path.join(item_dir, WORKSHOP_VOICE_MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            raw_manifest = json.load(f)
    except Exception as e:
        raise ValueError(f'读取参考语音清单失败: {e}') from e

    manifest = _normalize_workshop_voice_manifest(
        raw_manifest,
        default_prefix=os.path.basename(item_dir),
        default_display_name=os.path.basename(item_dir),
    )
    audio_path = _assert_under_base(os.path.join(item_dir, manifest['reference_audio']), item_dir)
    if not os.path.exists(audio_path) or not os.path.isfile(audio_path):
        raise FileNotFoundError(f'参考语音文件不存在: {manifest["reference_audio"]}')

    return {
        'manifest': manifest,
        'audio_path': audio_path,
        'manifest_path': manifest_path,
    }


def _cleanup_workshop_voice_reference(content_folder: str) -> None:
    manifest_path = os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        return

    try:
        voice_ref = _resolve_workshop_voice_reference(content_folder)
    except Exception as e:
        logger.warning(f'删除旧参考语音时解析 manifest 失败，将仅移除 manifest 文件: {e}')
        voice_ref = None

    if voice_ref:
        audio_path = voice_ref.get('audio_path')
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError as e:
                logger.warning(f'删除旧参考语音文件失败: {audio_path}, {e}')

    try:
        os.remove(manifest_path)
    except OSError as e:
        logger.warning(f'删除旧参考语音清单失败: {manifest_path}, {e}')


def _build_workshop_voice_reference_summary(install_folder: str) -> dict | None:
    try:
        voice_ref = _resolve_workshop_voice_reference(install_folder)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f'解析工坊参考语音失败: {install_folder}, {e}')
        return None

    if not voice_ref:
        return None

    manifest = voice_ref['manifest']
    return {
        'available': True,
        'displayName': manifest['display_name'],
        'prefix': manifest['prefix'],
        'refLanguage': manifest['ref_language'],
        'providerHint': manifest['provider_hint'],
        'referenceAudio': manifest['reference_audio'],
    }


async def _get_subscribed_items_payload() -> dict:
    result = await get_subscribed_workshop_items()
    if isinstance(result, JSONResponse):
        try:
            return json.loads(result.body.decode('utf-8'))
        except Exception:
            return {'success': False, 'error': '无法解析订阅物品响应'}
    if isinstance(result, dict):
        return result
    return {'success': False, 'error': '获取订阅物品响应异常'}


async def _find_subscribed_item_by_id(item_id: str) -> dict | None:
    payload = await _get_subscribed_items_payload()
    if not payload.get('success'):
        error = payload.get('error') or '获取订阅物品失败'
        raise RuntimeError(error)

    for item in payload.get('items', []):
        if str(item.get('publishedFileId')) == str(item_id):
            return item
    return None

@router.post('/upload-preview-image')
async def upload_preview_image(request: Request):
    """
    上传预览图片，将其统一命名为preview.*并保存到指定的内容文件夹（如果提供）
    """
    try:  
        # 接收上传的文件和表单数据
        form = await request.form()
        file = form.get('file')
        content_folder = form.get('content_folder')
        
        if not file:
            return JSONResponse({
                "success": False,
                "error": "没有选择文件",
                "message": "请选择要上传的图片文件"
            }, status_code=400)
        
        # 验证文件类型
        allowed_types = ['image/jpeg', 'image/png', 'image/jpg']
        if file.content_type not in allowed_types:
            return JSONResponse({
                "success": False,
                "error": "文件类型不允许",
                "message": "只允许上传JPEG和PNG格式的图片"
            }, status_code=400)
        
        # 获取文件扩展名
        # 扩展名按 content-type 固定映射，别信 filename
        content_type_to_ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png"}
        file_extension = content_type_to_ext.get(file.content_type)
        if not file_extension:
            return JSONResponse({"success": False, "error": "文件类型不允许"}, status_code=400)
                    
        # 处理内容文件夹路径
        if content_folder:
            # 规范化路径
            import urllib.parse
            content_folder = urllib.parse.unquote(content_folder)
            if os.name == 'nt':
                content_folder = content_folder.replace('/', '\\')
                if content_folder.startswith('\\\\'):
                    content_folder = content_folder[2:]
                else:
                    content_folder = content_folder.replace('\\', '/')
            
            # 验证内容文件夹存在
            if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
                # 如果文件夹不存在，回退到临时目录
                logger.warning(f"指定的内容文件夹不存在: {content_folder}，使用临时目录")
                content_folder = None
        
        # 创建统一命名的预览图路径
        if content_folder:
            # 直接保存到内容文件夹
            preview_image_path = os.path.join(content_folder, f'preview{file_extension}')
        else:
            # 使用临时目录
            import tempfile
            temp_folder = tempfile.gettempdir()
            preview_image_path = os.path.join(temp_folder, f'preview{file_extension}')
        
        # 保存文件到指定路径
        with open(preview_image_path, 'wb') as f:
            f.write(await file.read())
        
        return JSONResponse({
            "success": True,
            "file_path": preview_image_path,
            "message": "文件上传成功"
        })
    except Exception as e:
        logger.error(f"上传预览图片时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "内部错误",
            "message": "文件上传失败"
        }, status_code=500)


@router.post('/upload-reference-audio')
async def upload_reference_audio(request: Request):
    """上传参考语音并在内容目录中生成 voice_manifest.json。"""
    try:
        form = await request.form()
        file = form.get('file')
        content_folder = unquote(str(form.get('content_folder', '') or '').strip())
        workshop_export_dir = os.path.join(get_workshop_path(), 'WorkshopExport')

        if not file:
            return JSONResponse({
                "success": False,
                "error": "没有选择参考语音",
            }, status_code=400)

        if not content_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少内容目录",
            }, status_code=400)

        try:
            content_folder = _assert_under_base(content_folder, workshop_export_dir)
        except PermissionError:
            return JSONResponse({
                "success": False,
                "error": "参考语音只能上传到工坊临时目录",
            }, status_code=403)

        if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
            return JSONResponse({
                "success": False,
                "error": "内容目录不存在",
            }, status_code=404)

        file_name = getattr(file, 'filename', '') or ''
        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
            file_ext = WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES.get(getattr(file, 'content_type', ''), '')

        if file_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
            return JSONResponse({
                "success": False,
                "error": "参考语音格式只支持 mp3 或 wav",
            }, status_code=400)

        prefix = _sanitize_voice_prefix(
            form.get('prefix', ''),
            default_prefix=os.path.basename(content_folder),
        )
        display_name = str(form.get('display_name', '') or '').strip() or prefix
        ref_language = str(form.get('ref_language', 'ch') or 'ch').strip().lower()
        if ref_language not in WORKSHOP_REFERENCE_LANGUAGES:
            ref_language = 'ch'

        provider_hint = str(form.get('provider_hint', 'cosyvoice') or 'cosyvoice').strip().lower()
        if provider_hint not in WORKSHOP_REFERENCE_PROVIDER_HINTS:
            provider_hint = 'cosyvoice'

        _cleanup_workshop_voice_reference(content_folder)

        reference_audio_name = f'voice_sample{file_ext}'
        reference_audio_path = os.path.join(content_folder, reference_audio_name)
        with open(reference_audio_path, 'wb') as f:
            f.write(await file.read())

        manifest = _normalize_workshop_voice_manifest({
            'version': 1,
            'reference_audio': reference_audio_name,
            'prefix': prefix,
            'ref_language': ref_language,
            'display_name': display_name,
            'provider_hint': provider_hint,
        }, default_prefix=prefix, default_display_name=display_name)
        atomic_write_json(
            os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME),
            manifest,
            ensure_ascii=False,
            indent=2,
        )

        return JSONResponse({
            "success": True,
            "manifest": manifest,
            "message": "参考语音已写入工坊内容目录",
        })
    except Exception as e:
        logger.error(f"上传参考语音失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@router.post('/remove-reference-audio')
async def remove_reference_audio(request: Request):
    """删除内容目录中的参考语音和 voice_manifest.json。"""
    try:
        data = await request.json()
        content_folder = unquote(str(data.get('content_folder', '') or '').strip())
        workshop_export_dir = os.path.join(get_workshop_path(), 'WorkshopExport')
        if not content_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少内容目录",
            }, status_code=400)

        try:
            content_folder = _assert_under_base(content_folder, workshop_export_dir)
        except PermissionError:
            return JSONResponse({
                "success": False,
                "error": "内容目录不在允许范围内",
            }, status_code=403)

        if os.path.exists(content_folder) and os.path.isdir(content_folder):
            _cleanup_workshop_voice_reference(content_folder)

        return JSONResponse({
            "success": True,
            "message": "参考语音已清理",
        })
    except Exception as e:
        logger.error(f"删除参考语音失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)

@router.get('/status')
async def get_steam_status():
    """检查 Steamworks 是否已初始化并用于前端页面加载时判断 Steam 状态"""
    steamworks = get_steamworks()
    return JSONResponse({
        "success": True,
        "steamworks_initialized": steamworks is not None
    })

@router.get('/subscribed-items')
async def get_subscribed_workshop_items():
    """
    获取用户订阅的Steam创意工坊物品列表
    返回包含物品ID、基本信息和状态的JSON数据
    """
    steamworks = get_steamworks()
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # 获取订阅物品数量
        num_subscribed_items = steamworks.Workshop.GetNumSubscribedItems()
        
        # 如果没有订阅物品，返回空列表
        if num_subscribed_items == 0:
            return {
                "success": True,
                "items": [],
                "total": 0
            }
        
        # 获取订阅物品ID列表
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        
        # 存储处理后的物品信息
        items_info = []
        
        # 批量查询所有物品的详情（带重试+缓存）
        ugc_results = {}
        try:
            # 转换所有ID为整数
            all_item_ids = []
            for sid in subscribed_items:
                try:
                    all_item_ids.append(int(sid))
                except (ValueError, TypeError):
                    continue
            
            if all_item_ids:
                # 优先使用缓存（如果所有条目都存在且各自在有效期内）
                if _all_items_cache_valid(all_item_ids):
                    logger.debug(f"使用 UGC 缓存（{len(all_item_ids)} 个物品）")
                elif _ugc_warmup_task is not None and not _ugc_warmup_task.done():
                    # 预热任务仍在运行，等待它完成而非发起重复查询
                    logger.info("等待 UGC 缓存预热任务完成...")
                    try:
                        await asyncio.wait_for(asyncio.shield(_ugc_warmup_task), timeout=20)
                    except asyncio.TimeoutError:
                        logger.info("等待 UGC 缓存预热超时（20s），将回退到直接查询")
                    except Exception as e:
                        logger.warning(f"UGC 缓存预热任务异常: {e}", exc_info=True)
                    # 预热完成后按条目粒度检查缓存
                    if not _all_items_cache_valid(all_item_ids):
                        logger.info(f'预热后缓存不完整，重新批量查询 {len(all_item_ids)} 个物品')
                        ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=2)
                else:
                    logger.info(f'批量查询 {len(all_item_ids)} 个物品的详细信息')
                    ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=2)
        except Exception as batch_error:
            logger.warning(f"批量查询物品详情失败: {batch_error}")
        
        # 为每个物品获取基本信息和状态
        for item_id in subscribed_items:
            try:
                # 确保item_id是整数类型
                if isinstance(item_id, str):
                    try:
                        item_id = int(item_id)
                    except ValueError:
                        logger.error(f"无效的物品ID: {item_id}")
                        continue
                
                logger.debug(f'正在处理物品ID: {item_id}')
                
                # 获取物品状态
                item_state = steamworks.Workshop.GetItemState(item_id)
                logger.debug(f'物品 {item_id} 状态: {item_state}')
                
                # 初始化基本物品信息（确保所有字段都有默认值）
                # 确保publishedFileId始终为字符串类型，避免前端toString()错误
                item_info = {
                    "publishedFileId": str(item_id),
                    "title": f"未知物品_{item_id}",
                    "description": "无法获取详细描述",
                    "tags": [],
                    "state": {
                        "subscribed": bool(item_state & 1),  # EItemState.SUBSCRIBED
                        "legacyItem": bool(item_state & 2),
                        "installed": False,
                        "needsUpdate": bool(item_state & 8),  # EItemState.NEEDS_UPDATE
                        "downloading": False,
                        "downloadPending": bool(item_state & 32),  # EItemState.DOWNLOAD_PENDING
                        "isWorkshopItem": bool(item_state & 128)  # EItemState.IS_WORKSHOP_ITEM
                    },
                    "installedFolder": None,
                    "fileSizeOnDisk": 0,
                    "downloadProgress": {
                        "bytesDownloaded": 0,
                        "bytesTotal": 0,
                        "percentage": 0
                    },
                    # 添加额外的时间戳信息 - 使用datetime替代time模块避免命名冲突
                    "timeAdded": int(datetime.now().timestamp()),
                    "timeUpdated": int(datetime.now().timestamp())
                }
                
                # 尝试获取物品安装信息（如果已安装）
                try:
                    logger.debug(f'获取物品 {item_id} 的安装信息')
                    result = steamworks.Workshop.GetItemInstallInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（根据日志显示）
                    # GetItemInstallInfo 即使在物品已被退订后仍可能短暂返回成功，
                    # 必须用 os.path.isdir(folder) 二次确认目录仍存在才能标记
                    # installed=True，否则前端会展示"已安装但目录不存在"的幽灵态。
                    if result and isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 安装信息字典: {result}')

                        raw_folder = result.get('folder', '')
                        folder_path = str(raw_folder) if raw_folder else ''
                        if folder_path and os.path.isdir(folder_path):
                            item_info["state"]["installed"] = True
                            item_info["installedFolder"] = folder_path
                            disk_size = result.get('disk_size', 0)
                            item_info["fileSizeOnDisk"] = (
                                int(disk_size) if isinstance(disk_size, (int, float)) else 0
                            )
                        else:
                            item_info["state"]["installed"] = False
                            item_info["installedFolder"] = None
                            item_info["fileSizeOnDisk"] = 0
                            logger.debug(
                                f'物品 {item_id} Steam 报告已安装但安装目录不存在，'
                                f'按未安装处理: {folder_path!r}'
                            )
                        logger.debug(f'物品 {item_id} 的安装路径: {item_info["installedFolder"]}')
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        installed, folder, size = result
                        logger.debug(f'物品 {item_id} 安装状态: 已安装={installed}, 路径={folder}, 大小={size}')

                        folder_str = (
                            str(folder) if folder and isinstance(folder, (str, bytes)) else ''
                        )
                        folder_ok = bool(folder_str) and os.path.isdir(folder_str)
                        item_info["state"]["installed"] = bool(installed) and folder_ok
                        item_info["installedFolder"] = folder_str if item_info["state"]["installed"] else None

                        if item_info["state"]["installed"] and isinstance(size, (int, float)):
                            item_info["fileSizeOnDisk"] = int(size)
                        else:
                            item_info["fileSizeOnDisk"] = 0
                    else:
                        logger.warning(f'物品 {item_id} 的安装信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["installed"] = False
                except (FileNotFoundError, OSError) as e:
                    # 取消订阅后的短窗内 Steam 仍可能返回该 item，但本地 install
                    # folder 已被删 → 预期的 race，降级为 debug 避免日志噪音。
                    logger.debug(f'获取物品 {item_id} 安装信息失败（可能刚取消订阅）: {e}')
                    item_info["state"]["installed"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 安装信息失败: {e}')
                    item_info["state"]["installed"] = False
                
                # 尝试获取物品下载信息（如果正在下载）
                try:
                    logger.debug(f'获取物品 {item_id} 的下载信息')
                    result = steamworks.Workshop.GetItemDownloadInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（与安装信息保持一致）
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 下载信息字典: {result}')
                        
                        # 使用正确的键名获取下载信息
                        downloaded = result.get('downloaded', 0)
                        total = result.get('total', 0)
                        progress = result.get('progress', 0.0)
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": int(downloaded),
                                "bytesTotal": int(total),
                                "percentage": progress * 100 if isinstance(progress, (int, float)) else 0
                            }
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        # 元组中应该包含下载状态、已下载字节数和总字节数
                        downloaded, total, progress = result if len(result) >= 3 else (0, 0, 0.0)
                        logger.debug(f'物品 {item_id} 下载状态: 已下载={downloaded}, 总计={total}, 进度={progress}')
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            # 处理可能的类型转换
                            try:
                                downloaded_value = int(downloaded.value) if hasattr(downloaded, 'value') else int(downloaded)
                                total_value = int(total.value) if hasattr(total, 'value') else int(total)
                                progress_value = float(progress.value) if hasattr(progress, 'value') else float(progress)
                            except: # noqa
                                downloaded_value, total_value, progress_value = 0, 0, 0.0
                                
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": downloaded_value,
                                "bytesTotal": total_value,
                                "percentage": progress_value * 100
                            }
                    else:
                        logger.warning(f'物品 {item_id} 的下载信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["downloading"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 下载信息失败: {e}')
                    item_info["state"]["downloading"] = False
                
                # 从批量查询结果或缓存中提取物品详情
                item_id_int = int(item_id)
                if item_id_int in ugc_results:
                    _extract_ugc_item_details(steamworks, item_id_int, ugc_results[item_id_int], item_info)
                elif _is_item_cache_valid(item_id_int):
                    # 使用缓存数据填充（仅在该条目 TTL 有效时）
                    cached = _ugc_details_cache[item_id_int]
                    for key in ('title', 'description', 'timeCreated', 'timeAdded', 'timeUpdated',
                                'steamIDOwner', 'authorName', 'tags'):
                        if key in cached:
                            item_info[key] = cached[key]
                    logger.debug(f"从缓存填充物品 {item_id} 详情: title={item_info.get('title', '?')}")
                
                # 作为备选方案，如果本地有安装路径，尝试从本地文件获取信息
                if item_info['title'].startswith('未知物品_') or not item_info['description']:
                    install_folder = item_info.get('installedFolder')
                    if install_folder and os.path.exists(install_folder):
                        logger.debug(f'尝试从安装文件夹获取物品信息: {install_folder}')
                        # 查找可能的配置文件来获取更多信息
                        config_files = [
                            os.path.join(install_folder, "config.json"),
                            os.path.join(install_folder, "package.json"),
                            os.path.join(install_folder, "info.json"),
                            os.path.join(install_folder, "manifest.json"),
                            os.path.join(install_folder, "README.md"),
                            os.path.join(install_folder, "README.txt")
                        ]
                        
                        for config_path in config_files:
                            if os.path.exists(config_path):
                                try:
                                    if config_path.endswith('.json'):
                                        config_data = await read_json_async(config_path)
                                        # 尝试从配置文件中提取标题和描述
                                        if "title" in config_data and config_data["title"]:
                                            item_info["title"] = config_data["title"]
                                        elif "name" in config_data and config_data["name"]:
                                            item_info["title"] = config_data["name"]
                                        # description 作为 title/name 的同级分支，不应嵌在 elif name 下
                                        if "description" in config_data and config_data["description"]:
                                            item_info["description"] = config_data["description"]
                                    else:
                                        # README.md / README.txt：把首行当标题（offload sync IO）
                                        first_line = (await asyncio.to_thread(_read_first_line, config_path)).strip()
                                        if first_line and item_info['title'].startswith('未知物品_'):
                                            item_info['title'] = first_line[:100]  # 限制长度
                                    logger.debug(f"从本地文件 {os.path.basename(config_path)} 成功获取物品 {item_id} 的信息")
                                    break
                                except Exception as file_error:
                                    logger.warning(f"读取配置文件 {config_path} 时出错: {file_error}")
                # 移除了没有对应try块的except语句
                
                # 确保publishedFileId是字符串类型
                item_info['publishedFileId'] = str(item_info['publishedFileId'])
                
                # 尝试获取预览图信息 - 优先从本地文件夹查找
                # 多道防御：先用 isdir 双重检查（比 exists 更明确排除"存在但不是目录"），
                # 再吞 FileNotFoundError（取消订阅后遍历期间目录被删的 race）。
                preview_url = None
                install_folder = item_info.get('installedFolder')
                if install_folder and os.path.isdir(install_folder):
                    try:
                        # 使用辅助函数查找预览图
                        preview_image_path = find_preview_image_in_folder(install_folder)
                        if preview_image_path:
                            # 为前端提供代理访问的路径格式
                            # 需要将路径标准化，确保可以通过proxy-image API访问
                            if os.name == 'nt':
                                # Windows路径处理
                                proxy_path = preview_image_path.replace('\\', '/')
                            else:
                                proxy_path = preview_image_path
                            preview_url = f"/api/steam/proxy-image?image_path={quote(proxy_path)}"
                            logger.debug(f'为物品 {item_id} 找到本地预览图: {preview_url}')
                    except (FileNotFoundError, OSError) as preview_error:
                        logger.debug(
                            f'查找物品 {item_id} 预览图时目录已消失（可能刚取消订阅）: {preview_error}'
                        )
                    except Exception as preview_error:
                        logger.warning(f'查找物品 {item_id} 预览图时出错: {preview_error}')
                
                # 添加预览图URL到物品信息
                if preview_url:
                    item_info['previewUrl'] = preview_url

                voice_reference_summary = None
                if install_folder and os.path.isdir(install_folder):
                    try:
                        voice_reference_summary = await asyncio.to_thread(
                            _build_workshop_voice_reference_summary,
                            install_folder,
                        )
                    except (FileNotFoundError, OSError) as voice_error:
                        logger.debug(
                            f'构建物品 {item_id} voice reference 时目录已消失（可能刚取消订阅）: {voice_error}'
                        )
                    except Exception as voice_error:
                        logger.warning(f'构建物品 {item_id} voice reference 失败: {voice_error}')
                item_info['voiceReferenceAvailable'] = bool(voice_reference_summary)
                if voice_reference_summary:
                    item_info['voiceReference'] = voice_reference_summary
                
                # 添加物品信息到结果列表
                items_info.append(item_info)
                logger.debug(f'物品 {item_id} 信息已添加到结果列表: {item_info["title"]}')
                
            except Exception as item_error:
                logger.error(f"获取物品 {item_id} 信息时出错: {item_error}")
                # 即使出错，也添加一个最基本的物品信息到列表中
                try:
                    basic_item_info = {
                        "publishedFileId": str(item_id),  # 确保是字符串类型
                        "title": f"未知物品_{item_id}",
                        "description": "无法获取详细信息",
                        "state": {
                            "subscribed": True,
                            "installed": False,
                            "downloading": False,
                            "needsUpdate": False,
                            "error": True
                        },
                        "error_message": str(item_error)
                    }
                    items_info.append(basic_item_info)
                    logger.debug(f'已添加物品 {item_id} 的基本信息到结果列表')
                except Exception as basic_error:
                    logger.error(f"添加基本物品信息也失败了: {basic_error}")
                # 继续处理下一个物品
                continue
        
        return {
            "success": True,
            "items": items_info,
            "total": len(items_info)
        }
        
    except Exception as e:
        logger.error(f"获取订阅物品列表时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取订阅物品失败: {str(e)}"
        }, status_code=500)


@router.get('/item/{item_id}/path')
def get_workshop_item_path(item_id: str):
    """
    获取单个Steam创意工坊物品的下载路径
    此API端点专门用于在管理页面中获取物品的安装路径
    """
    steamworks = get_steamworks()
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # 转换item_id为整数
        item_id_int = int(item_id)
        
        # 获取物品安装信息
        install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
        
        if not install_info:
            return JSONResponse({
                "success": False,
                "error": "物品未安装",
                "message": f"物品 {item_id} 尚未安装或安装信息不可用"
            }, status_code=404)
        
        # 提取安装路径，兼容字典和元组两种返回格式
        folder_path = ''
        size_on_disk: int | None = None
        
        if isinstance(install_info, dict):
            folder_path = install_info.get('folder', '') or ''
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                size_on_disk = int(disk_size)
        elif isinstance(install_info, tuple) and len(install_info) >= 3:
            folder, disk_size = install_info[1], install_info[2]
            if isinstance(folder, (str, bytes)):
                folder_path = str(folder)
            if isinstance(disk_size, (int, float)):
                size_on_disk = int(disk_size)
        
        # 构建响应
        response = {
            "success": True,
            "item_id": item_id,
            "installed": True,
            "path": folder_path,
            "full_path": folder_path  # 完整路径，与path保持一致
        }
        
        # 如果有磁盘大小信息，也一并返回
        if size_on_disk is not None:
            response['size_on_disk'] = size_on_disk
        
        return response
        
    except ValueError:
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID",
            "message": "物品ID必须是有效的数字"
        }, status_code=400)
    except Exception as e:
        logger.error(f"获取物品 {item_id} 路径时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "获取路径失败",
            "message": str(e)
        }, status_code=500)


@router.get('/voice-reference/{item_id}')
async def get_workshop_voice_reference(item_id: str):
    """按 publishedFileId 返回订阅工坊物品中的参考语音 manifest。"""
    try:
        item = await _find_subscribed_item_by_id(item_id)
    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=503)

    if not item:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": "未找到对应的订阅工坊物品",
        }, status_code=404)

    install_folder = item.get('installedFolder')
    if not install_folder or not os.path.exists(install_folder):
        return JSONResponse({
            "success": False,
            "available": False,
            "error": "工坊物品尚未安装",
        }, status_code=404)

    try:
        voice_ref = await asyncio.to_thread(_resolve_workshop_voice_reference, install_folder)
    except FileNotFoundError as e:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": str(e),
        }, status_code=404)
    except ValueError as e:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": str(e),
        }, status_code=400)

    if not voice_ref:
        return JSONResponse({
            "success": True,
            "available": False,
            "item_id": str(item_id),
            "title": item.get('title') or '',
        })

    return JSONResponse({
        "success": True,
        "available": True,
        "item_id": str(item_id),
        "title": item.get('title') or '',
        "manifest": voice_ref['manifest'],
    })


@router.get('/voice-reference/{item_id}/audio')
async def get_workshop_voice_reference_audio(item_id: str):
    """返回订阅工坊物品中的参考语音音频流。"""
    try:
        item = await _find_subscribed_item_by_id(item_id)
    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=503)

    if not item:
        return JSONResponse({
            "success": False,
            "error": "未找到对应的订阅工坊物品",
        }, status_code=404)

    install_folder = item.get('installedFolder')
    if not install_folder or not os.path.exists(install_folder):
        return JSONResponse({
            "success": False,
            "error": "工坊物品尚未安装",
        }, status_code=404)

    try:
        voice_ref = await asyncio.to_thread(_resolve_workshop_voice_reference, install_folder)
    except FileNotFoundError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=404)
    except ValueError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=400)

    if not voice_ref:
        return JSONResponse({
            "success": False,
            "error": "该工坊物品没有参考语音",
        }, status_code=404)

    audio_path = voice_ref['audio_path']
    media_type = mimetypes.guess_type(audio_path)[0] or 'application/octet-stream'
    return FileResponse(
        audio_path,
        media_type=media_type,
        filename=os.path.basename(audio_path),
    )


@router.get('/item/{item_id}')
async def get_workshop_item_details(item_id: str):
    """
    获取单个Steam创意工坊物品的详细信息
    """
    steamworks = get_steamworks()
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # 转换item_id为整数
        item_id_int = int(item_id)
        
        # 获取物品状态
        item_state = steamworks.Workshop.GetItemState(item_id_int)
        
        # 使用统一的批量查询辅助函数（带重试）查询单个物品
        ugc_results = await _query_ugc_details_batch(steamworks, [item_id_int], max_retries=2)
        result = ugc_results.get(item_id_int)
        
        # 如果查询失败，尝试使用缓存（按条目粒度检查 TTL）
        if not result and _is_item_cache_valid(item_id_int):
            cached = _ugc_details_cache[item_id_int]
            # 使用缓存数据构建响应
            use_cache = True
        else:
            use_cache = False
            
        if result or use_cache:
            # 获取物品安装信息 - 兼容字典/元组/None 三种返回格式
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
            installed = False
            folder = ''
            size = 0

            if install_info and isinstance(install_info, dict):
                installed = True
                folder = install_info.get('folder', '') or ''
                disk_size = install_info.get('disk_size')
                if isinstance(disk_size, (int, float)):
                    size = int(disk_size)
            elif isinstance(install_info, tuple) and len(install_info) >= 3:
                installed = bool(install_info[0])
                raw_folder = install_info[1]
                if isinstance(raw_folder, (str, bytes)):
                    folder = str(raw_folder)
                raw_size = install_info[2]
                if isinstance(raw_size, (int, float)):
                    size = int(raw_size)
            elif install_info:
                installed = True
            
            # 获取物品下载信息
            download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int)
            downloading = False
            bytes_downloaded = 0
            bytes_total = 0
            
            # 处理下载信息（使用正确的键名：downloaded和total）
            if download_info:
                if isinstance(download_info, dict):
                    downloaded = int(download_info.get("downloaded", 0) or 0)
                    total = int(download_info.get("total", 0) or 0)
                    downloading = downloaded > 0 and downloaded < total
                    bytes_downloaded = downloaded
                    bytes_total = total
                elif isinstance(download_info, tuple) and len(download_info) >= 3:
                    # 兼容元组格式
                    downloading, bytes_downloaded, bytes_total = download_info
            
            if use_cache:
                # 从缓存构建结果
                title = cached.get('title', f'未知物品_{item_id}')
                description = cached.get('description', '')
                owner_id_str = cached.get('steamIDOwner', '')
                author_name = cached.get('authorName')
                time_created = cached.get('timeCreated', 0)
                time_updated = cached.get('timeUpdated', 0)
                file_size = 0
                preview_url = ''
                associated_url = ''
                file_url = ''
                file_id = 0
                preview_file_id = 0
                tags = cached.get('tags', [])
            else:
                # 解码bytes类型的字段为字符串，避免JSON序列化错误
                title = result.title.decode('utf-8', errors='replace') if hasattr(result, 'title') and isinstance(result.title, bytes) else getattr(result, 'title', '')
                description = result.description.decode('utf-8', errors='replace') if hasattr(result, 'description') and isinstance(result.description, bytes) else getattr(result, 'description', '')
                
                # 将 steamIDOwner 解析为实际用户名
                owner_id = int(result.steamIDOwner) if hasattr(result, 'steamIDOwner') and result.steamIDOwner else 0
                owner_id_str = str(owner_id) if owner_id else ''
                author_name = _resolve_author_name(steamworks, owner_id) if owner_id else None
                time_created = getattr(result, 'timeCreated', 0)
                time_updated = getattr(result, 'timeUpdated', 0)
                file_size = getattr(result, 'fileSize', 0)
                # SteamUGCDetails_t.URL (m_rgchURL) 是物品的关联网页 URL，并非预览图。
                # 真正的预览图需通过 ISteamUGC::GetQueryUGCPreviewURL() 获取，
                # 但当前 Steamworks wrapper 未暴露该接口，因此 previewImageUrl 置空，
                # 前端已有 fallback（默认 Steam 图标）。
                # TODO: 在 wrapper 中实现 GetQueryUGCPreviewURL 后填充 preview_url。
                preview_url = ''
                # 解码关联网页 URL 供客户端可选使用
                raw_url = getattr(result, 'URL', b'')
                if isinstance(raw_url, bytes):
                    raw_url = raw_url.decode('utf-8', errors='replace')
                associated_url = raw_url.strip('\x00').strip() if raw_url else ''
                # file handle 和 preview file handle 是 UGC 文件句柄，不是下载 URL
                file_url = ''
                file_id = getattr(result, 'file', 0)
                preview_file_id = getattr(result, 'previewFile', 0)
                tags = []
                if hasattr(result, 'tags') and result.tags:
                    try:
                        tags_str = result.tags.decode('utf-8', errors='replace')
                        if tags_str:
                            tags = [t.strip() for t in tags_str.split(',') if t.strip()]
                    except Exception as e:
                        logger.debug(f"解析物品 {item_id} 标签失败: {e}")
                
                # 更新缓存
                _extract_ugc_item_details(steamworks, item_id_int, result, {
                    "publishedFileId": str(item_id_int),
                    "title": f"未知物品_{item_id}", "description": ""
                })
            
            # 构建详细的物品信息
            item_info = {
                "publishedFileId": item_id_int,
                "title": title,
                "description": description,
                "steamIDOwner": owner_id_str,
                "authorName": author_name,
                "timeCreated": time_created,
                "timeUpdated": time_updated,
                "previewImageUrl": preview_url,
                "associatedUrl": associated_url,
                "fileUrl": file_url,
                "fileSize": file_size,
                "fileId": file_id,
                "previewFileId": preview_file_id,
                "tags": tags,
                "state": {
                    "subscribed": bool(item_state & 1),
                    "legacyItem": bool(item_state & 2),
                    "installed": installed,
                    "needsUpdate": bool(item_state & 8),
                    "downloading": downloading,
                    "downloadPending": bool(item_state & 32),
                    "isWorkshopItem": bool(item_state & 128)
                },
                "installedFolder": folder if installed else None,
                "fileSizeOnDisk": size if installed else 0,
                "downloadProgress": {
                    "bytesDownloaded": bytes_downloaded if downloading else 0,
                    "bytesTotal": bytes_total if downloading else 0,
                    "percentage": (bytes_downloaded / bytes_total * 100) if bytes_total > 0 and downloading else 0
                }
            }
            
            return {
                "success": True,
                "item": item_info
            }

        else:
            # 注意：SteamWorkshop类中不存在ReleaseQueryUGCRequest方法
            return JSONResponse({
                "success": False,
                "error": "获取物品详情失败，未找到物品"
            }, status_code=404)
            
    except ValueError:
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID"
        }, status_code=400)
    except Exception as e:
        logger.error(f"获取物品 {item_id} 详情时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取物品详情失败: {str(e)}"
        }, status_code=500)


def _collect_character_names_by_workshop_item_id(config_mgr, item_id: int) -> list[str]:
    """
    通过 character_origin.source_id 在 characters.json 中反查来源为该
    Workshop 物品的角色名（稳定索引，不依赖磁盘上的 .chara.json）。

    Args:
        config_mgr: ConfigManager 实例
        item_id: Workshop 物品 ID（整数）

    Returns:
        list[str]: 匹配到的角色名列表（可能为空；保持去重后的插入顺序）
    """
    try:
        characters = config_mgr.load_characters()
    except Exception as exc:
        logger.warning(
            f"_collect_character_names_by_workshop_item_id: 加载 characters.json 失败: {exc}"
        )
        return []

    # characters.json 是用户可写文件，根对象或 猫娘 字段被写成 list/string 时
    # 直接 .get() / .items() 会抛异常，把退订流程打成 500。这里受控降级。
    if not isinstance(characters, dict):
        logger.warning(
            "_collect_character_names_by_workshop_item_id: "
            f"characters.json 根对象不是 dict（{type(characters).__name__}），跳过反查"
        )
        return []
    catgirl_map = characters.get('猫娘')
    if not isinstance(catgirl_map, dict):
        if catgirl_map is not None:
            logger.warning(
                "_collect_character_names_by_workshop_item_id: "
                f"characters.json 的 猫娘 字段不是 dict（{type(catgirl_map).__name__}），跳过反查"
            )
        return []

    target_id = str(item_id)
    names: list[str] = []
    seen: set[str] = set()
    for name, payload in catgirl_map.items():
        if not isinstance(payload, dict):
            continue
        source = str(
            get_reserved(payload, 'character_origin', 'source', default='') or ''
        ).strip()
        source_id = str(
            get_reserved(payload, 'character_origin', 'source_id', default='') or ''
        ).strip()
        if source == 'steam_workshop' and source_id == target_id and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _scan_workshop_folder_character_names(item_path: str | None) -> list[str]:
    """
    扫描 Workshop 物品磁盘目录中的 .chara.json，提取角色名（作为反向索引的补充）。
    若目录不存在或扫描出错，返回空列表。
    """
    if not item_path:
        return []
    try:
        normalized_path = os.path.abspath(os.path.normpath(item_path))
    except Exception:
        return []
    if not os.path.isdir(normalized_path):
        return []

    names: list[str] = []
    seen: set[str] = set()
    try:
        for root, _dirs, files in os.walk(normalized_path):
            for file_name in files:
                if not file_name.endswith('.chara.json'):
                    continue
                chara_file_path = os.path.join(root, file_name)
                try:
                    with open(chara_file_path, 'r', encoding='utf-8') as f:
                        chara_data = json.load(f)
                except Exception as exc:
                    logger.warning(
                        f"_scan_workshop_folder_character_names: 读取 {chara_file_path} 失败: {exc}"
                    )
                    continue
                # Workshop 文件属于外部输入，任何畸形（顶层非 dict、档案名为 list/dict
                # 等）不应中断整个 os.walk；校验失败跳过该卡片继续扫描。
                if not isinstance(chara_data, dict):
                    logger.warning(
                        f"_scan_workshop_folder_character_names: {chara_file_path} "
                        f"顶层不是 dict，跳过"
                    )
                    continue
                raw_name = chara_data.get('档案名') or chara_data.get('name')
                if not isinstance(raw_name, str):
                    if raw_name is not None:
                        logger.warning(
                            f"_scan_workshop_folder_character_names: {chara_file_path} "
                            f"档案名/name 不是字符串（{type(raw_name).__name__}），跳过"
                        )
                    continue
                chara_name = raw_name.strip()
                if chara_name and chara_name not in seen:
                    names.append(chara_name)
                    seen.add(chara_name)
    except Exception as exc:
        logger.warning(
            f"_scan_workshop_folder_character_names: 扫描 {normalized_path} 失败: {exc}"
        )
    return names


def _resolve_workshop_item_install_path(steamworks, item_id: int) -> str | None:
    """
    尽力解析 Workshop 物品当前的磁盘安装路径。
    优先 GetItemInstallInfo，回退 find_workshop_item_by_id；失败返回 None。
    """
    item_path: str | None = None
    try:
        if steamworks:
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id)
            if isinstance(install_info, dict):
                folder_path = install_info.get('folder') or ''
                if folder_path:
                    item_path = str(folder_path)
            elif isinstance(install_info, tuple) and len(install_info) >= 2:
                folder = install_info[1]
                if folder:
                    item_path = str(folder)
    except Exception as exc:
        logger.debug(
            f"_resolve_workshop_item_install_path: GetItemInstallInfo({item_id}) 失败: {exc}"
        )

    if not item_path:
        try:
            from utils.frontend_utils import find_workshop_item_by_id
            candidate, _ = find_workshop_item_by_id(str(item_id))
            item_path = candidate or None
        except Exception as exc:
            logger.debug(
                f"_resolve_workshop_item_install_path: find_workshop_item_by_id({item_id}) 失败: {exc}"
            )
            return None

    if not item_path:
        return None
    try:
        return os.path.abspath(os.path.normpath(item_path))
    except Exception:
        return item_path


@router.post('/unsubscribe')
async def unsubscribe_workshop_item(request: Request):
    """
    取消订阅Steam创意工坊物品
    接收包含物品ID的POST请求
    """
    steamworks = get_steamworks()

    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)

    try:
        # 获取请求体中的数据
        data = await request.json()
        item_id = data.get('item_id')

        if not item_id:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数",
                "message": "请求中缺少物品ID"
            }, status_code=400)

        # 转换item_id为整数
        try:
            item_id_int = int(item_id)
        except ValueError:
            return JSONResponse({
                "success": False,
                "error": "无效的物品ID",
                "message": "提供的物品ID不是有效的数字"
            }, status_code=400)

        config_mgr = get_config_manager()

        # 反向索引：优先用 character_origin.source_id 找到来自该 Workshop 物品的角色，
        # 再用磁盘上 .chara.json 的扫描结果兜底合并（文件夹可能已被 Steam 删除）。
        # 三个 helper 都是同步磁盘 / Steamworks 调用（_resolve_workshop_item_install_path
        # 会调 GetItemInstallInfo + 磁盘兜底搜索），必须 offload 避免阻塞事件循环。
        candidate_names = await asyncio.to_thread(
            _collect_character_names_by_workshop_item_id, config_mgr, item_id_int
        )
        pre_item_path = await asyncio.to_thread(
            _resolve_workshop_item_install_path, steamworks, item_id_int
        )
        disk_names = await asyncio.to_thread(
            _scan_workshop_folder_character_names, pre_item_path
        )
        # 跟踪每个候选角色的来源：
        #   "origin" = 从 characters.json 的 character_origin.source_id 反查命中，
        #              配置明确标记来自该 item_id，可放心删除。
        #   "disk"   = 仅来自磁盘 .chara.json 的名字扫描，只是"名字碰撞"，
        #              不能证明这角色就是该 item_id 的；删除前必须对每个
        #              候选在 characters.json 里二次确认 source_id / asset_source_id。
        candidate_sources: dict[str, str] = {name: "origin" for name in candidate_names}
        seen_names: set[str] = set(candidate_names)
        for disk_name in disk_names:
            if disk_name not in seen_names:
                candidate_names.append(disk_name)
                candidate_sources[disk_name] = "disk"
                seen_names.add(disk_name)
        logger.info(
            f"取消订阅 {item_id_int}: 反向索引候选角色 {candidate_names}（磁盘扫描追加 {disk_names}）"
        )

        target_item_id_str = str(item_id_int)

        def _is_confirmed_workshop_character(snapshot, name: str) -> bool:
            """
            判定角色 `name` 在 `snapshot`（characters.json 的快照）里是否**明确绑定**
            到当前 `item_id_int`。判定只看配置里的 character_origin.source_id /
            avatar.asset_source_id，不看磁盘上的 .chara.json。

            用于拦截"磁盘同名 .chara.json 把无辜本地角色卷进候选、进而误挡住当前
            猫娘退订"的场景：只有当前猫娘确实来源于这个 Workshop item 时才阻断。
            """
            if not isinstance(snapshot, dict):
                return False
            cg_map = snapshot.get('猫娘')
            if not isinstance(cg_map, dict):
                return False
            payload = cg_map.get(name)
            if not isinstance(payload, dict):
                return False
            origin_source = str(
                get_reserved(payload, 'character_origin', 'source', default='') or ''
            ).strip()
            origin_source_id = str(
                get_reserved(payload, 'character_origin', 'source_id', default='') or ''
            ).strip()
            asset_source = str(
                get_reserved(payload, 'avatar', 'asset_source', default='') or ''
            ).strip()
            asset_source_id = str(
                get_reserved(payload, 'avatar', 'asset_source_id', default='') or ''
            ).strip()
            return (
                origin_source == 'steam_workshop' and origin_source_id == target_item_id_str
            ) or (
                asset_source == 'steam_workshop' and asset_source_id == target_item_id_str
            )

        # 前置校验：候选角色中若包含当前猫娘，直接阻止取消订阅并提示用户切换。
        try:
            current_characters = await config_mgr.aload_characters()
        except Exception as exc:
            logger.warning(f"取消订阅前读取 characters.json 失败: {exc}")
            current_characters = await asyncio.to_thread(config_mgr.load_characters)
        # characters.json 根对象若被写成 list/string，.get() 会抛 AttributeError；
        # 受控降级为空 dict 并继续，候选角色为空时前置校验自然 no-op。
        if not isinstance(current_characters, dict):
            logger.warning(
                f"取消订阅: characters.json 根对象不是 dict"
                f"（{type(current_characters).__name__}），按空配置处理"
            )
            current_characters = {}
        current_catgirl = str(current_characters.get('当前猫娘', '') or '')
        # 只在当前猫娘**确实绑定该 Workshop item** 时才阻断；仅靠名字匹配的磁盘
        # 候选（如工坊另有同名 .chara.json）不应把无辜的本地猫娘挡住退订。
        if (
            current_catgirl
            and current_catgirl in candidate_names
            and _is_confirmed_workshop_character(current_characters, current_catgirl)
        ):
            logger.warning(
                f"取消订阅被阻止: item_id={item_id_int} 对应角色 {current_catgirl} 正是当前猫娘"
            )
            return JSONResponse({
                "success": False,
                "code": "CURRENT_CATGIRL_IN_USE",
                "error": f"不能取消订阅当前正在使用的猫娘「{current_catgirl}」，请先切换到其他角色后再取消订阅。",
                "character_name": current_catgirl,
            }, status_code=400)

        # 前置尝试释放 memory_server 对候选角色的 SQLite 句柄（best-effort + 并行）。
        # 与 delete_catgirl 不同：取消订阅场景下，memory_server 对非活跃角色
        # 可能本来就没持有句柄，/release_character 会返回 non-success，但此时
        # 也根本不存在文件锁 —— 硬拒绝会导致用户永远无法取消订阅。
        # 真正的安全网是同步清理里的 PermissionError retry；这里只记录 warning。
        #
        # 并行预算：per-call 2.5s，整体 3s（参考 main_server.py 关机阶段做法）。
        # 多候选时耗时从 O(N * RT) 降到 O(max(RT))；单候选表现不变。
        release_warnings: list[str] = []
        if candidate_names:
            try:
                from .characters_router import release_memory_server_character
            except Exception as exc:
                logger.error(
                    f"取消订阅前置 release: 无法 import release_memory_server_character: {exc}"
                )
                return JSONResponse({
                    "success": False,
                    "code": "INTERNAL_IMPORT_ERROR",
                    "error": f"内部组件加载失败: {exc}",
                }, status_code=500)

            async def _release_one(name: str) -> tuple[str, bool, str | None]:
                try:
                    released = await asyncio.wait_for(
                        release_memory_server_character(
                            name,
                            reason=f"取消订阅前释放 SQLite 句柄: {name}（item_id={item_id_int}）",
                        ),
                        timeout=2.5,
                    )
                    return name, bool(released), None
                except Exception as exc:
                    return name, False, str(exc)

            try:
                release_results = await asyncio.wait_for(
                    asyncio.gather(
                        *(_release_one(n) for n in candidate_names),
                        return_exceptions=False,
                    ),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"取消订阅前置 release 总预算 3s 超时（item_id={item_id_int}），"
                    f"视为全部 non-success 继续清理"
                )
                release_results = [(n, False, "overall_timeout") for n in candidate_names]

            for name, ok, err in release_results:
                if ok:
                    continue
                release_warnings.append(name)
                logger.info(
                    f"取消订阅前置 release: {name} 返回 non-success"
                    f"{'（' + err + '）' if err else ''}，继续走清理流程"
                )

        # 同步执行记忆/角色卡/tombstone 清理（与 DELETE /catgirl/{name} 对齐）。
        # 这一步必须在 UnsubscribeItem 之前完成，这样 HTTP 响应就能直接汇报
        # "删了哪些角色、删了哪些记忆路径"，用户能立刻确认结果，不用等 Steam 异步回调。
        # 任意角色子步骤失败都只记录到 cleanup_summary.errors，不中断整体流程
        # （因为 UnsubscribeItem 一旦发出，Steam 端已无法回滚；记忆残留由用户看到错误后重试）。
        #
        # 性能优化：
        #   - 单角色内 delete_memory / tombstone / remove_one_catgirl 三步互相独立，
        #     用 asyncio.gather 并发（return_exceptions=True 各自兜异常）。
        #   - characters.json 的 del 只改内存 dict，循环末尾批量一次写盘
        #     （N 次 atomic_write → 1 次）。
        cleanup_summary: dict = {
            "candidate_characters": list(candidate_names),
            "cleaned_characters": [],
            "removed_memory_paths": [],
            "errors": [],
            # memory_server release 返回 non-success 的角色名（不影响清理流程，
            # 仅用于诊断，一般表示该角色在 memory_server 侧本来就没持有句柄）。
            "release_warnings": list(release_warnings),
        }

        if candidate_names:
            try:
                from .characters_router import (
                    _build_character_tombstones_state,
                    notify_memory_server_reload,
                )
                from utils.character_memory import delete_character_memory_storage
                from .shared_state import get_remove_one_catgirl
            except Exception as exc:
                logger.error(
                    f"取消订阅同步清理: 无法 import 生命周期工具: {exc}"
                )
                return JSONResponse({
                    "success": False,
                    "code": "INTERNAL_IMPORT_ERROR",
                    "error": f"内部组件加载失败: {exc}",
                }, status_code=500)

            characters_mut = await config_mgr.aload_characters()
            # 同步清理会对 characters_mut['猫娘'] 做 del；根对象或 猫娘 字段
            # 结构异常时直接按 LOCAL_CONFIG_CLEANUP_FAILED 中止，避免
            # TypeError/AttributeError 把退订流程打成 500。
            if (
                not isinstance(characters_mut, dict)
                or not isinstance(characters_mut.get('猫娘'), dict)
            ):
                logger.error(
                    f"取消订阅同步清理被阻止: characters.json 结构无效 "
                    f"(root={type(characters_mut).__name__}, "
                    f"猫娘={type(characters_mut.get('猫娘')).__name__ if isinstance(characters_mut, dict) else 'N/A'})"
                )
                return JSONResponse({
                    "success": False,
                    "code": "LOCAL_CONFIG_CLEANUP_FAILED",
                    "error": "本地角色配置结构无效，已取消本次 Steam 退订请求，请修复 characters.json 后重试。",
                    "cleanup_summary": cleanup_summary,
                }, status_code=500)
            current_catgirl_now = str(characters_mut.get('当前猫娘', '') or '')
            # 二次校验：前置校验后、同步清理前用户可能切到候选角色；此时
            # 仅 `continue` 会跳过角色删除但仍执行 UnsubscribeItem + 删除订阅
            # 文件夹，留下指向已删 Workshop 资源的当前猫娘配置，应直接中止。
            # 同样复用 _is_confirmed_workshop_character：只有当前猫娘确实绑定
            # 当前 item_id 才阻断，避免磁盘同名误挡。
            if (
                current_catgirl_now
                and current_catgirl_now in candidate_names
                and _is_confirmed_workshop_character(characters_mut, current_catgirl_now)
            ):
                logger.warning(
                    f"取消订阅同步清理被阻止: item_id={item_id_int} 对应角色 "
                    f"{current_catgirl_now} 已切换为当前猫娘"
                )
                return JSONResponse({
                    "success": False,
                    "code": "CURRENT_CATGIRL_IN_USE",
                    "error": f"不能取消订阅当前正在使用的猫娘「{current_catgirl_now}」，请先切换到其他角色后再取消订阅。",
                    "character_name": current_catgirl_now,
                }, status_code=400)

            async def _delete_memory_with_retry(name: str) -> list:
                """Windows 文件锁 → 300ms 重试一次作为安全网。"""
                try:
                    return list(
                        await asyncio.to_thread(
                            delete_character_memory_storage, config_mgr, name
                        )
                        or []
                    )
                except PermissionError as exc:
                    logger.warning(
                        f"同步清理: delete_character_memory_storage({name}) "
                        f"PermissionError: {exc}，300ms 后重试"
                    )
                    await asyncio.sleep(0.3)
                    return list(
                        await asyncio.to_thread(
                            delete_character_memory_storage, config_mgr, name
                        )
                        or []
                    )

            async def _write_tombstone(name: str) -> None:
                tombstone_state = _build_character_tombstones_state(config_mgr, name)
                await asyncio.to_thread(
                    config_mgr.save_character_tombstones_state, tombstone_state
                )

            async def _remove_one(name: str) -> None:
                fn = get_remove_one_catgirl()
                if fn is not None:
                    await fn(name)

            pending_del_names: list[str] = []
            catgirl_map = characters_mut['猫娘']  # 上面 isinstance 已守卫
            target_item_id_str = str(item_id_int)
            for name in candidate_names:
                if not name:
                    continue
                # 保护性双保险：绝不删当前猫娘（前置校验已覆盖，这里兜底）
                if name == current_catgirl_now:
                    logger.warning(
                        f"取消订阅同步清理: 跳过当前猫娘 '{name}'（保护性双保险）"
                    )
                    continue

                # 磁盘兜底候选必须二次确认来源：名字一致 ≠ 同一 item_id。
                # 如果用户本地已有同名非 Workshop 角色（或同名但来自别的
                # item_id 的 Workshop 角色），按磁盘名字盲删会误删。
                # 反向索引候选（"origin"）已经是在 characters.json 里按
                # source_id 匹配到的，不需要二次校验。
                if candidate_sources.get(name) == "disk":
                    payload = catgirl_map.get(name) if isinstance(catgirl_map, dict) else None
                    origin_source = str(
                        get_reserved(payload, 'character_origin', 'source', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    origin_source_id = str(
                        get_reserved(payload, 'character_origin', 'source_id', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    asset_source = str(
                        get_reserved(payload, 'avatar', 'asset_source', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    asset_source_id = str(
                        get_reserved(payload, 'avatar', 'asset_source_id', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    confirmed_workshop_match = (
                        origin_source == 'steam_workshop' and origin_source_id == target_item_id_str
                    ) or (
                        asset_source == 'steam_workshop' and asset_source_id == target_item_id_str
                    )
                    if not confirmed_workshop_match:
                        logger.warning(
                            f"取消订阅同步清理: 跳过未确认来源的磁盘候选角色 '{name}' "
                            f"(item_id={item_id_int}, origin_source={origin_source!r}, "
                            f"origin_source_id={origin_source_id!r}, "
                            f"asset_source={asset_source!r}, asset_source_id={asset_source_id!r})"
                        )
                        cleanup_summary.setdefault("skipped_unverified_characters", []).append(name)
                        continue

                # 三步独立：并发执行
                results = await asyncio.gather(
                    _delete_memory_with_retry(name),
                    _write_tombstone(name),
                    _remove_one(name),
                    return_exceptions=True,
                )
                rm_paths_or_exc, tombstone_or_exc, remove_or_exc = results

                # delete_memory 结果
                if isinstance(rm_paths_or_exc, Exception):
                    logger.error(
                        f"取消订阅同步清理: delete_memory({name}) 失败: {rm_paths_or_exc}",
                        exc_info=rm_paths_or_exc,
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "delete_memory",
                        "error": str(rm_paths_or_exc),
                    })
                else:
                    for entry_path in rm_paths_or_exc:
                        logger.info(f"取消订阅同步清理: 已删除记忆 {entry_path}")
                        cleanup_summary["removed_memory_paths"].append(str(entry_path))
                    if not rm_paths_or_exc:
                        logger.warning(
                            f"取消订阅同步清理: delete_memory({name}) 未返回任何路径 "
                            f"(memory_dir={getattr(config_mgr, 'memory_dir', None)})"
                        )

                # tombstone 结果
                if isinstance(tombstone_or_exc, Exception):
                    logger.error(
                        f"取消订阅同步清理: tombstone({name}) 失败: {tombstone_or_exc}",
                        exc_info=tombstone_or_exc,
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "tombstone",
                        "error": str(tombstone_or_exc),
                    })
                else:
                    logger.info(f"取消订阅同步清理: 已写入 tombstone -> {name}")

                # remove_one_catgirl 结果
                if isinstance(remove_or_exc, Exception):
                    logger.warning(
                        f"取消订阅同步清理: remove_one_catgirl({name}) 失败: {remove_or_exc}"
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "remove_one_catgirl",
                        "error": str(remove_or_exc),
                    })

                # characters.json 条目仅做内存删除，循环结束一次性批量写盘。
                # 复用前面捕获的 catgirl_map 引用（上面 isinstance 已守卫），
                # 避免每次都走 characters_mut.get('猫娘') or {} 的兜底链路。
                if name in catgirl_map:
                    try:
                        del catgirl_map[name]
                        pending_del_names.append(name)
                    except Exception as exc:
                        logger.error(
                            f"取消订阅同步清理: 内存 del characters[猫娘][{name}] 失败: {exc}",
                            exc_info=True,
                        )
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "delete_config",
                            "error": str(exc),
                        })

            # 本地角色配置写盘失败 / 内存 del 失败 → 绝不能继续发 UnsubscribeItem：
            # Steam 订阅一旦取消，订阅文件夹会被删；但 characters.json 仍保留
            # 该角色，配置会指向不存在的 Workshop 资源，且下次启动可能加载坏卡。
            # 这里 Steam 请求还没发，安全地提前中止并把 summary 返回给前端。
            local_config_cleanup_failed = False

            # 批量写 characters.json（N 个 del → 1 次 atomic write）
            if pending_del_names:
                try:
                    await config_mgr.asave_characters(characters_mut)
                    cleanup_summary["cleaned_characters"] = list(pending_del_names)
                    logger.info(
                        f"取消订阅同步清理: 批量删除 {len(pending_del_names)} 个角色并写入 characters.json: "
                        f"{pending_del_names}"
                    )
                except Exception as exc:
                    local_config_cleanup_failed = True
                    logger.error(
                        f"取消订阅同步清理: 批量 asave_characters 失败: {exc}",
                        exc_info=True,
                    )
                    cleanup_summary["errors"].append({
                        "character": "<batch>",
                        "stage": "delete_config",
                        "error": str(exc),
                    })

            # 若任一本地配置清理失败（per-name del 或批量写盘），立即中止。
            delete_config_failed = any(
                err.get("stage") == "delete_config"
                for err in cleanup_summary.get("errors") or []
            )
            if local_config_cleanup_failed or delete_config_failed:
                logger.error(
                    f"取消订阅同步清理: 本地角色配置清理失败（item_id={item_id_int}），"
                    f"已中止 Steam UnsubscribeItem 请求以避免配置-订阅不一致"
                )
                return JSONResponse({
                    "success": False,
                    "code": "LOCAL_CONFIG_CLEANUP_FAILED",
                    "error": "本地角色配置清理失败，已取消本次 Steam 退订请求，请修复后重试。",
                    "cleanup_summary": cleanup_summary,
                }, status_code=500)

            # 通知 memory_server 重新加载（一次即可）
            try:
                await notify_memory_server_reload(
                    reason=f"取消订阅 item_id={item_id_int}"
                )
            except Exception as exc:
                logger.warning(
                    f"取消订阅同步清理: notify_memory_server_reload 失败: {exc}"
                )

        logger.info(
            f"取消订阅同步清理汇总 item_id={item_id_int}: "
            f"cleaned={cleanup_summary['cleaned_characters']}, "
            f"removed_paths={len(cleanup_summary['removed_memory_paths'])}, "
            f"errors={len(cleanup_summary['errors'])}"
        )

        # 回调与延迟兜底共享的幂等标志（first-winner 模式）。
        # 使用 Lock 保证 check + set 的原子性，避免两线程同时通过闸口。
        #
        # 角色卡/记忆/tombstone 已经在同步路径（上方）处理完毕；perform_cleanup
        # 只负责 Steam 订阅文件夹的磁盘删除兜底。不再需要把 async 任务调回主
        # 事件循环（_run_async_in_main_loop / _purge_character_memory_and_config
        # 已移除），回调线程做的事纯粹是阻塞 IO（shutil.rmtree），可以直接跑。
        cleanup_event = threading.Event()
        cleanup_claim_lock = threading.Lock()

        # cleanup_claim_lock 含义变更：现在只保护 "是否正在执行" 判定，
        # cleanup_event 只在 **确认成功** 后 set，避免删除失败时把 5 秒延迟
        # 兜底门闩锁死（rmtree ignore_errors 吞掉异常 / 目录仍存在 / 抛出
        # 异常的三种失败路径都必须允许后续重试）。
        cleanup_in_progress = threading.Event()
        # Steam 明确返回取消订阅失败时设置：此时用户仍处于订阅状态，
        # 5 秒延迟兜底必须跳过 perform_cleanup，否则会删掉仍在订阅中的
        # 本地 Workshop 文件夹（Steam 下次同步会再下回来）。
        unsubscribe_failed_event = threading.Event()

        def _is_item_still_subscribed(item_id: int) -> bool:
            """
            Fail-closed 订阅状态检查：返回 True 表示仍订阅中（或无法确认）。
            取不到 Steamworks / 查询抛异常时一律按"仍订阅"保守处理，
            避免在不确定状态下误删用户仍在订阅中的本地文件夹。
            """
            try:
                sw = get_steamworks()
                if sw is None:
                    logger.warning(
                        f"perform_cleanup({item_id}): Steamworks 不可用，"
                        f"无法确认订阅状态，按仍订阅处理"
                    )
                    return True
                state = sw.Workshop.GetItemState(item_id)
                return bool(state & 1)  # EItemState.SUBSCRIBED = 1
            except Exception as exc:
                logger.warning(
                    f"perform_cleanup({item_id}): GetItemState 失败，"
                    f"按仍订阅处理: {exc}"
                )
                return True

        def perform_cleanup(item_id: int, *, confirmed_unsubscribed: bool = False):
            """
            回调/延迟兜底共用的订阅文件夹删除。幂等：
              - cleanup_event.is_set() → 已成功过一次，直接跳过
              - cleanup_in_progress 未设 → 抢占执行权，结束后清除
              - cleanup_in_progress 已设 → 另一路径在跑，避免并发 rmtree 同目录
            只有真正确认目录已不存在时才 set(cleanup_event)；失败路径仅清除
            in_progress，让 5 秒延迟兜底仍可重试。

            fail-closed 订阅状态校验：除非 `confirmed_unsubscribed=True`（仅成功
            回调路径传入），进 rmtree 前必须过 `_is_item_still_subscribed()`。
            "5 秒没收到回调" 不能推断为退订成功——Steam 可能延后发失败回调，
            此时删本地文件夹会让仍订阅中的用户丢失内容。
            """
            with cleanup_claim_lock:
                if cleanup_event.is_set():
                    logger.debug(f"perform_cleanup({item_id}): 已成功过，跳过（幂等）")
                    return False
                # 把 unsubscribe_failed_event 的判定也放进临界区。delayed_cleanup
                # 外层的先 check cleanup_event → check unsubscribe_failed_event →
                # 再调 perform_cleanup 两次 check 之间没锁，Steam 失败回调若恰好
                # 落在这个窗口里，rmtree 还是会把仍订阅中的本地工坊目录删掉。
                # 在锁内原子化闭环；成功回调路径本来就不会 set 失败 event，不会误伤。
                if unsubscribe_failed_event.is_set():
                    logger.warning(
                        f"perform_cleanup({item_id}): 已收到 Steam 退订失败信号，"
                        f"跳过订阅文件夹清理（用户仍处于订阅状态）"
                    )
                    return False
                if cleanup_in_progress.is_set():
                    logger.debug(f"perform_cleanup({item_id}): 已有并发清理在跑，跳过")
                    return False
                cleanup_in_progress.set()

            try:
                import shutil
                # Fail-closed: 未明确确认成功时，必须先查 Steam 的订阅位
                # （GetItemState & 1）。仍订阅中就跳过清理，同时 set 失败
                # event 防止后续路径重复发起 rmtree。
                if not confirmed_unsubscribed and _is_item_still_subscribed(item_id):
                    logger.warning(
                        f"perform_cleanup({item_id}): Steam 状态仍显示已订阅，"
                        f"跳过订阅文件夹清理"
                    )
                    unsubscribe_failed_event.set()
                    return False

                # 重新解析一次路径（候选路径可能在取消订阅过程中失效）
                final_item_path = _resolve_workshop_item_install_path(
                    get_steamworks(), item_id
                ) or pre_item_path
                if final_item_path and os.path.isdir(final_item_path):
                    try:
                        shutil.rmtree(final_item_path, ignore_errors=True)
                    except Exception as rmtree_exc:
                        # ignore_errors=True 通常不会外抛，但兜底一下
                        logger.error(
                            f"perform_cleanup({item_id}): rmtree 抛异常: {rmtree_exc}",
                            exc_info=True,
                        )
                    if os.path.exists(final_item_path):
                        logger.warning(
                            f"perform_cleanup({item_id}): 订阅文件夹仍存在（可能被占用）: {final_item_path}"
                        )
                        return False  # 未成功 → 不 set cleanup_event，留给延迟兜底重试
                    logger.info(
                        f"perform_cleanup({item_id}): 已删除订阅文件夹 {final_item_path}"
                    )
                else:
                    logger.debug(
                        f"perform_cleanup({item_id}): 订阅文件夹已不存在，视为成功"
                    )
                # 只有走到这里（目录确认不存在）才锁死 cleanup_event
                cleanup_event.set()
                return True
            except Exception as exc:
                logger.error(
                    f"perform_cleanup({item_id}): 删除订阅文件夹时出错: {exc}",
                    exc_info=True,
                )
                return False
            finally:
                cleanup_in_progress.clear()

        def unsubscribe_callback(result):
            """Steamworks UnsubscribeItem 的回调（在 Steam 回调线程中执行）。"""
            callback_item_id = getattr(
                result, 'publishedFileId', getattr(result, 'published_file_id', None)
            )
            logger.info(
                f"取消订阅回调被触发: 期望item_id={item_id_int}, 回调item_id={callback_item_id}, "
                f"result.result={getattr(result, 'result', None)}"
            )
            # 验证 item_id 是否匹配（防止其他取消订阅操作触发此回调）
            if callback_item_id and int(callback_item_id) != item_id_int:
                logger.warning(
                    f"回调item_id不匹配: 期望{item_id_int}, 实际{callback_item_id}，跳过处理"
                )
                return

            if getattr(result, 'result', None) == 1:  # k_EResultOK
                logger.info(f"取消订阅成功回调: {item_id_int}，开始执行清理")
                # Steam 明确回调 OK，不必再用 GetItemState 二次确认；直接删。
                perform_cleanup(item_id_int, confirmed_unsubscribed=True)
            else:
                # Steam 明确退订失败 → 订阅仍然存在，不能删本地文件夹。
                unsubscribe_failed_event.set()
                logger.warning(
                    f"取消订阅失败回调: {item_id_int}, 错误代码: {getattr(result, 'result', None)}，"
                    f"不执行订阅文件夹清理"
                )

        # 调用 Steamworks 的 UnsubscribeItem 方法，并提供回调函数
        try:
            steamworks.Workshop.UnsubscribeItem(
                item_id_int, callback=unsubscribe_callback, override_callback=True
            )
            logger.info(f"取消订阅请求已发送: {item_id_int}，等待回调...")

            # 延迟兜底：5 秒后若回调仍未触发（cleanup_event 未 set），
            # 在后台线程里直接执行一次 perform_cleanup（幂等）。
            def delayed_cleanup():
                import time as _time
                # noqa: BLOCKING-OK - 只在 daemon 后台线程跑，不阻塞主事件循环。
                _time.sleep(5)
                if cleanup_event.is_set():
                    logger.debug(f"延迟兜底: item_id={item_id_int} 已清理，跳过")
                    return
                if unsubscribe_failed_event.is_set():
                    # 已收到 Steam 明确失败回调，用户仍订阅中 → 不删本地文件夹。
                    logger.warning(
                        f"延迟兜底: item_id={item_id_int} 已收到退订失败回调，"
                        f"跳过订阅文件夹清理"
                    )
                    return
                logger.warning(
                    f"延迟兜底: item_id={item_id_int} 5 秒内未收到回调，执行备用清理"
                )
                perform_cleanup(item_id_int)

            cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
            cleanup_thread.start()

        except Exception as e:
            # UnsubscribeItem 调用失败 = Steam 退订请求根本没发出 / 没被接受。
            # 此时不能再 perform_cleanup：用户仍处于订阅状态，删本地文件夹会
            # 让他保持订阅却丢失本地 Workshop 文件（下次 Steam 会再下载一遍）。
            # 同步阶段已经删了的 characters.json / memory 无法回滚，但至少
            # 订阅-文件夹状态保持一致，由用户手动处理后续。
            logger.error(
                f"调用 UnsubscribeItem 失败: {e}，已保留本地 Workshop 文件夹，"
                f"不执行备用清理",
                exc_info=True,
            )
            return JSONResponse({
                "success": False,
                "code": "STEAM_UNSUBSCRIBE_FAILED",
                "error": f"Steam 退订请求发送失败: {e}",
                "cleanup_summary": cleanup_summary,
            }, status_code=500)

        logger.info(f"取消订阅请求已被接受，正在处理: {item_id_int}")
        return {
            "success": True,
            "status": "accepted",
            "message": "取消订阅请求已被接受，正在处理中。实际结果将在后台异步完成。",
            "candidate_character_count": len(candidate_names),
            # 同步阶段的实际清理结果（记忆/角色卡/tombstone 已删除），
            # 订阅文件夹由 Steam 异步回调或 5 秒延迟兜底负责删除。
            "cleanup_summary": cleanup_summary,
        }

    except Exception as e:
        logger.error(f"取消订阅物品时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "message": f"取消订阅过程中发生错误: {str(e)}"
        }, status_code=500)


@router.get('/meta/{character_name}')
async def get_workshop_meta(character_name: str):
    """
    获取角色卡的 Workshop 元数据（包含上传状态和快照）
    
    Args:
        character_name: 角色卡名称（URL 编码）
    
    Returns:
        JSON: 包含 workshop_item_id、uploaded_snapshot 等信息
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


@router.get('/config')
async def get_workshop_config():
    try:
        from utils.workshop_utils import load_workshop_config
        workshop_config_data = await asyncio.to_thread(load_workshop_config)
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"获取创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}

# 保存创意工坊配置

@router.post('/config')
async def save_workshop_config_api(config_data: dict):
    try:
        # 导入与get_workshop_config相同路径的函数，保持一致性
        from utils.workshop_utils import load_workshop_config, save_workshop_config, ensure_workshop_folder_exists
        
        # 先加载现有配置，避免使用全局变量导致的不一致问题
        workshop_config_data = await asyncio.to_thread(load_workshop_config) or {}
        
        # 更新配置
        if 'default_workshop_folder' in config_data:
            workshop_config_data['default_workshop_folder'] = config_data['default_workshop_folder']
        if 'auto_create_folder' in config_data:
            workshop_config_data['auto_create_folder'] = config_data['auto_create_folder']
        # 支持用户mod路径配置
        if 'user_mod_folder' in config_data:
            workshop_config_data['user_mod_folder'] = config_data['user_mod_folder']
        
        # 保存配置到文件，传递完整的配置数据作为参数
        save_workshop_config(workshop_config_data)
        
        # 如果启用了自动创建文件夹且提供了路径，则确保文件夹存在
        if workshop_config_data.get('auto_create_folder', True):
            # 优先使用user_mod_folder，如果没有则使用default_workshop_folder
            folder_path = workshop_config_data.get('user_mod_folder') or workshop_config_data.get('default_workshop_folder')
            if folder_path:
                ensure_workshop_folder_exists(folder_path)
        
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"保存创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}


@router.get('/check-upload-status')
async def check_upload_status(item_path: str = None):
    try:
        # 验证路径参数
        if not item_path:
            return JSONResponse(content={
                "success": False,
                "error": "未提供物品文件夹路径"
            }, status_code=400)
        
        # 安全检查：使用get_workshop_path()作为基础目录
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 解码并处理Windows路径
            decoded_item_path = unquote(item_path)
            # 替换斜杠为反斜杠，确保Windows路径格式正确
            decoded_item_path = decoded_item_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_item_path.startswith('\\\\'):
                decoded_item_path = decoded_item_path[2:]  # 移除多余的反斜杠前缀
        else:
            decoded_item_path = unquote(item_path)
        
        # 将相对路径转换为基于基础目录的绝对路径
        if not os.path.isabs(decoded_item_path):
            full_path = os.path.join(base_workshop_folder, decoded_item_path)
        else:
            full_path = decoded_item_path
            full_path = os.path.normpath(full_path)
        
        # 安全检查：验证路径是否在基础目录内
        if not full_path.startswith(base_workshop_folder):
            logger.warning(f'路径遍历尝试被拒绝: {item_path}')
            return JSONResponse(content={"success": False, "error": "访问被拒绝: 路径不在允许的范围内"}, status_code=403)
        
        # 验证路径存在性
        if not os.path.exists(full_path) or not os.path.isdir(full_path):
            return JSONResponse(content={
                "success": False,
                "error": "无效的物品文件夹路径"
            }, status_code=400)
        
        # 搜索以steam_workshop_id_开头的txt文件
        import glob
        import re
        
        upload_files = glob.glob(os.path.join(full_path, "steam_workshop_id_*.txt"))
        
        # 提取第一个找到的物品ID
        published_file_id = None
        if upload_files:
            # 获取第一个文件
            first_file = upload_files[0]
            
            # 从文件名提取ID
            match = re.search(r'steam_workshop_id_(\d+)\.txt', os.path.basename(first_file))
            if match:
                published_file_id = match.group(1)
        
        # 返回检查结果
        return JSONResponse(content={
            "success": True,
            "is_published": published_file_id is not None,
            "published_file_id": published_file_id
        })
        
    except Exception as e:
        logger.error(f"检查上传状态失败: {e}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "message": "检查上传状态时发生错误"
        }, status_code=500)


def _assert_under_base(path: str, base: str) -> str:
    full = os.path.realpath(os.path.normpath(path))
    base_full = os.path.realpath(os.path.normpath(base))
    if os.path.commonpath([full, base_full]) != base_full:
        raise PermissionError("path not allowed")
    return full


def _is_workshop_publish_native_crash_risk() -> bool:
    """SteamworksPy on macOS arm64 crashes in CreateItem/SubmitItemUpdate callbacks."""
    return sys.platform == 'darwin' and platform.machine().lower() in {'arm64', 'aarch64'}

@router.get('/read-file')
async def read_workshop_file(path: str):
    """读取创意工坊文件内容"""
    try:
        logger.info(f"读取创意工坊文件请求，路径: {path}")
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        decoded_path = _assert_under_base(decoded_path, get_workshop_path())
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查文件是否存在
        if not os.path.exists(decoded_path) or not os.path.isfile(decoded_path):
            logger.warning(f"文件不存在: {decoded_path}")
            return JSONResponse(content={"success": False, "error": "文件不存在"}, status_code=404)
        
        # 检查文件大小限制（例如5MB）
        MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
        file_size = os.path.getsize(decoded_path)
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"文件过大: {decoded_path} ({file_size / 1024 / 1024:.2f}MB > {MAX_FILE_SIZE / 1024 / 1024}MB)")
            return JSONResponse(content={"success": False, "error": "文件过大"}, status_code=413)
        
        # 尝试判断文件类型并选择合适的读取方式
        file_extension = os.path.splitext(decoded_path)[1].lower()
        is_binary = file_extension in ['.mp3', '.wav', '.png', '.jpg', '.jpeg', '.gif']
        
        if is_binary:
            # 以二进制模式读取文件并进行base64编码
            import base64
            with open(decoded_path, 'rb') as f:
                binary_content = f.read()
            content = base64.b64encode(binary_content).decode('utf-8')
        else:
            # 以文本模式读取文件
            with open(decoded_path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        logger.info(f"成功读取文件: {decoded_path}, 是二进制文件: {is_binary}")
        return JSONResponse(content={"success": True, "content": content, "is_binary": is_binary})
    except Exception as e:
        logger.error(f"读取文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"读取文件失败: {str(e)}"}, status_code=500)


@router.get('/list-chara-files')
async def list_chara_files(directory: str):
    """列出指定目录下所有的.chara.json文件"""
    try:
        logger.info(f"列出创意工坊目录下的角色卡文件请求，目录: {directory}")
        
        # 解码URL编码的路径
        decoded_dir = _assert_under_base(unquote(directory), get_workshop_path())
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # 检查目录是否存在
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # 查找所有.chara.json文件
        chara_files = []
        for filename in os.listdir(decoded_dir):
            if filename.endswith('.chara.json'):
                file_path = os.path.join(decoded_dir, filename)
                if os.path.isfile(file_path):
                    chara_files.append({
                        'name': filename,
                        'path': file_path
                    })
        
        logger.info(f"成功列出目录下的角色卡文件: {decoded_dir}, 找到 {len(chara_files)} 个文件")
        return JSONResponse(content={"success": True, "files": chara_files})
    except Exception as e:
        logger.error(f"列出角色卡文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"列出角色卡文件失败: {str(e)}"}, status_code=500)


@router.get('/list-audio-files')
async def list_audio_files(directory: str):
    """列出指定目录下所有的音频文件(.mp3, .wav)"""
    try:
        logger.info(f"列出创意工坊目录下的音频文件请求，目录: {directory}")
        
        # 解码URL编码的路径并验证是否在workshop目录下
        decoded_dir = _assert_under_base(unquote(directory), get_workshop_path())
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # 检查目录是否存在
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # 查找所有音频文件
        audio_files = []
        for filename in os.listdir(decoded_dir):
            if filename.endswith(('.mp3', '.wav')):
                file_path = os.path.join(decoded_dir, filename)
                if os.path.isfile(file_path):
                    # 提取文件名前缀（不含扩展名）作为prefix
                    prefix = os.path.splitext(filename)[0]
                    audio_files.append({
                        'name': filename,
                        'path': file_path,
                        'prefix': prefix
                    })
        
        logger.info(f"成功列出目录下的音频文件: {decoded_dir}, 找到 {len(audio_files)} 个文件")
        return JSONResponse(content={"success": True, "files": audio_files})
    except Exception as e:
        logger.error(f"列出音频文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"列出音频文件失败: {str(e)}"}, status_code=500)


@router.post('/prepare-upload')
async def prepare_workshop_upload(request: Request):
    """
    准备上传到创意工坊：创建临时目录并复制角色卡和模型文件
    返回临时目录路径，供后续上传使用
    """
    try:
        import shutil
        import uuid
        from utils.frontend_utils import find_model_directory
        
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')
        model_type = data.get('modelType', 'live2d')  # 新增：模型类型 live2d/vrm/mmd
        chara_file_name = data.get('fileName', 'character.chara.json')
        character_card_name = data.get('character_card_name')  # 新增：角色卡名称
        
        if not chara_data or not model_name:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数"
            }, status_code=400)
        
        # 验证 modelType 白名单
        if model_type not in ('live2d', 'vrm', 'mmd'):
            return JSONResponse({
                "success": False,
                "error": f"不支持的模型类型: {model_type}"
            }, status_code=400)
        
        # 防路径穿越:只允许文件名,不允许携带路径或上级目录喵
        safe_chara_name = os.path.basename(chara_file_name)
        if safe_chara_name != chara_file_name or ".." in safe_chara_name or safe_chara_name.startswith(("/", "\\")):
            logger.warning(f"检测到非法文件名尝试: {chara_file_name}")
            return JSONResponse({
                "success": False,
                "error": "非法文件名"
            }, status_code=400)
        
        # 如果没有传递 character_card_name，尝试从文件名提取
        if not character_card_name and safe_chara_name:
            if safe_chara_name.endswith('.chara.json'):
                character_card_name = safe_chara_name[:-11]  # 去掉 .chara.json 后缀
        
        # TODO: 临时阻止重复上传，直到实现创意工坊作者验证机制
        # 未来需要支持：
        # 1. 验证当前用户是否是原上传者
        # 2. 允许原作者更新已上传的内容

        # 检查是否已存在workshop_meta.json文件（防止重复上传）
        if character_card_name:
            meta_data = await asyncio.to_thread(read_workshop_meta, character_card_name)
            if meta_data and meta_data.get('workshop_item_id'):
                workshop_item_id = meta_data.get('workshop_item_id')

                # 返回错误，提示用户该角色卡已上传过
                return JSONResponse({
                    "success": False,
                    "error": "该角色卡已上传到创意工坊",
                    "workshop_item_id": workshop_item_id,
                    "message": f"角色卡 '{character_card_name}' 已经上传过（物品ID: {workshop_item_id}）。如需更新，请使用更新功能。"
                }, status_code=400)
        
        # 获取workshop基础路径
        base_workshop_path = get_workshop_path()
        workshop_export_dir = os.path.join(base_workshop_path, 'WorkshopExport')
        
        # 确保WorkshopExport目录存在
        os.makedirs(workshop_export_dir, exist_ok=True)
        
        # 创建临时目录 item_xxx
        item_id = str(uuid.uuid4())[:8]  # 使用UUID的前8位作为item标识
        temp_item_dir = os.path.join(workshop_export_dir, f'item_{item_id}')
        os.makedirs(temp_item_dir, exist_ok=True)
        
        logger.info(f"创建临时上传目录: {temp_item_dir}")
        
        # 1. 复制角色卡JSON到临时目录(已验证为安全文件名)喵
        chara_file_path = os.path.join(temp_item_dir, safe_chara_name)
        await atomic_write_json_async(chara_file_path, chara_data, ensure_ascii=False, indent=2)
        logger.info(f"角色卡已复制到临时目录: {chara_file_path}")
        
        # 2. 根据模型类型查找并复制模型文件
        if model_type in ('vrm', 'mmd'):
            # VRM/MMD 模型：model_name 是文件路径如 /user_vrm/model.vrm 或 /user_mmd/folder/model.pmx
            model_copied = False
            config_mgr = get_config_manager()
            
            # 安全检查：防止路径穿越
            if '..' in model_name:
                await asyncio.to_thread(shutil.rmtree, temp_item_dir, ignore_errors=True)
                return JSONResponse({
                    "success": False,
                    "error": "非法模型路径"
                }, status_code=400)
            
            if model_type == 'vrm':
                # VRM 模型是单文件，解析实际路径
                from pathlib import Path as PathLib
                vrm_filename = os.path.basename(model_name)
                
                if model_name.startswith('/user_vrm/'):
                    vrm_dir = config_mgr.vrm_dir
                    source_file = vrm_dir / vrm_filename
                elif model_name.startswith('/static/vrm/'):
                    source_file = config_mgr.project_root / "static" / "vrm" / vrm_filename
                elif model_name.startswith('/workshop/'):
                    # Workshop VRM 模型：通过 item_id 查找安装目录
                    source_file = None
                    ws_parts = model_name.lstrip('/').split('/')
                    if len(ws_parts) >= 3:
                        ws_item_id = ws_parts[1]
                        ws_rel_path = '/'.join(ws_parts[2:])
                        workshop_items_result = await get_subscribed_workshop_items()
                        if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                            for item in workshop_items_result.get('items', []):
                                if str(item.get('publishedFileId')) == ws_item_id:
                                    installed_folder = item.get('installedFolder')
                                    if installed_folder:
                                        source_file = PathLib(installed_folder) / ws_rel_path
                                    break
                else:
                    source_file = None
                
                if source_file and source_file.exists():
                    vrm_dest = os.path.join(temp_item_dir, vrm_filename)
                    await asyncio.to_thread(shutil.copy2, str(source_file), vrm_dest)
                    logger.info(f"VRM模型文件已复制到临时目录: {vrm_dest}")
                    model_copied = True
                    
            elif model_type == 'mmd':
                # MMD 模型可能在子目录中（包含PMX+纹理等），复制整个模型目录
                from pathlib import Path as PathLib
                
                # 从路径中提取模型目录名（如 /user_mmd/folder/model.pmx -> folder）
                path_parts = model_name.lstrip('/').split('/')
                
                if model_name.startswith('/user_mmd/') and len(path_parts) >= 3:
                    # 有子目录：/user_mmd/subfolder/model.pmx
                    mmd_dir_name = path_parts[1]  # subfolder
                    mmd_base = getattr(config_mgr, 'mmd_dir', config_mgr.project_root / "user_mmd")
                    source_dir = mmd_base / mmd_dir_name
                    if source_dir.exists() and source_dir.is_dir():
                        model_dest_dir = os.path.join(temp_item_dir, mmd_dir_name)
                        await asyncio.to_thread(shutil.copytree, str(source_dir), model_dest_dir, dirs_exist_ok=True)
                        logger.info(f"MMD模型目录已复制到临时目录: {model_dest_dir}")
                        model_copied = True
                elif model_name.startswith('/user_mmd/') and len(path_parts) == 2:
                    # 直接在 user_mmd 根目录下的文件
                    mmd_filename = path_parts[1]
                    mmd_base = getattr(config_mgr, 'mmd_dir', config_mgr.project_root / "user_mmd")
                    source_file = mmd_base / mmd_filename
                    if source_file.exists():
                        mmd_dest = os.path.join(temp_item_dir, mmd_filename)
                        await asyncio.to_thread(shutil.copy2, str(source_file), mmd_dest)
                        logger.info(f"MMD模型文件已复制到临时目录: {mmd_dest}")
                        model_copied = True
                elif model_name.startswith('/static/mmd/'):
                    # static 目录下的 MMD
                    rel_path = model_name[len('/static/mmd/'):]
                    source_file = config_mgr.project_root / "static" / "mmd" / rel_path
                    if source_file.exists():
                        # 复制包含该文件的目录
                        source_dir = source_file.parent
                        dest_name = source_dir.name
                        model_dest_dir = os.path.join(temp_item_dir, dest_name)
                        await asyncio.to_thread(shutil.copytree, str(source_dir), model_dest_dir, dirs_exist_ok=True)
                        logger.info(f"MMD模型目录已复制到临时目录: {model_dest_dir}")
                        model_copied = True
                elif model_name.startswith('/workshop/'):
                    # Workshop MMD 模型：通过 item_id 查找安装目录，复制模型所在目录
                    ws_parts = model_name.lstrip('/').split('/')
                    if len(ws_parts) >= 3:
                        ws_item_id = ws_parts[1]
                        ws_rel_path = '/'.join(ws_parts[2:])
                        workshop_items_result = await get_subscribed_workshop_items()
                        if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                            for item in workshop_items_result.get('items', []):
                                if str(item.get('publishedFileId')) == ws_item_id:
                                    installed_folder = item.get('installedFolder')
                                    if installed_folder:
                                        source_file = PathLib(installed_folder) / ws_rel_path
                                        if source_file.exists():
                                            # MMD 需要复制整个模型目录（包含纹理等资源）
                                            source_dir = source_file.parent
                                            dest_name = source_dir.name
                                            model_dest_dir = os.path.join(temp_item_dir, dest_name)
                                            await asyncio.to_thread(shutil.copytree, str(source_dir), model_dest_dir, dirs_exist_ok=True)
                                            logger.info(f"Workshop MMD模型目录已复制到临时目录: {model_dest_dir}")
                                            model_copied = True
                                    break
            
            if not model_copied:
                await asyncio.to_thread(shutil.rmtree, temp_item_dir, ignore_errors=True)
                return JSONResponse({
                    "success": False,
                    "error": f"模型文件不存在: {model_name}"
                }, status_code=404)
        else:
            # Live2D 模型：使用原有逻辑
            model_dir, _ = find_model_directory(model_name)
            if not model_dir or not os.path.exists(model_dir):
                # 清理临时目录
                await asyncio.to_thread(shutil.rmtree, temp_item_dir, ignore_errors=True)
                return JSONResponse({
                    "success": False,
                    "error": f"模型目录不存在: {model_name}"
                }, status_code=404)
            
            # 复制整个模型目录到临时目录
            model_dest_dir = os.path.join(temp_item_dir, model_name)
            await asyncio.to_thread(shutil.copytree, model_dir, model_dest_dir, dirs_exist_ok=True)
            logger.info(f"模型文件已复制到临时目录: {model_dest_dir}")
        
        # 如果角色卡已有卡面，则默认复制为 Workshop 预览图；没有卡面时保持原逻辑不变。
        preview_image_path = None
        if character_card_name:
            try:
                config_mgr = get_config_manager()
                face_path = config_mgr.card_faces_dir / f"{character_card_name}.png"
                if face_path.exists() and face_path.is_file():
                    preview_image_path = os.path.join(temp_item_dir, 'preview.png')
                    await asyncio.to_thread(shutil.copy2, str(face_path), preview_image_path)
                    logger.info(f"已使用角色卡卡面作为默认 Workshop 预览图: {preview_image_path}")
            except Exception as preview_error:
                preview_image_path = None
                logger.warning(f"复制角色卡卡面作为默认预览图失败，将保持预览图不变: {preview_error}")
        
        # 读取 .workshop_meta.json（如果存在）
        workshop_item_id = None
        if character_card_name:
            meta_data = await asyncio.to_thread(read_workshop_meta, character_card_name)
            if meta_data and meta_data.get('workshop_item_id'):
                workshop_item_id = meta_data.get('workshop_item_id')
                logger.info(f"检测到已存在的 Workshop 物品 ID: {workshop_item_id}")
        
        response_data = {
            "success": True,
            "temp_folder": temp_item_dir,
            "item_id": item_id,
            "workshop_item_id": workshop_item_id,  # 如果存在，返回已存在的物品ID
            "message": "上传准备完成"
        }
        if preview_image_path:
            response_data["preview_image"] = preview_image_path
        return JSONResponse(response_data)
        
    except Exception as e:
        logger.error(f"准备上传失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.post('/cleanup-temp-folder')
async def cleanup_temp_folder(request: Request):
    """
    清理临时上传目录
    """
    try:
        import shutil
        data = await request.json()
        temp_folder = data.get('temp_folder')
        
        if not temp_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少临时目录路径"
            }, status_code=400)
        
        # 安全检查：确保临时目录在WorkshopExport下
        base_workshop_path = get_workshop_path()
        workshop_export_dir = os.path.join(base_workshop_path, 'WorkshopExport')
        
        # 规范化路径（使用realpath处理符号链接和相对路径）
        temp_folder = os.path.realpath(os.path.normpath(temp_folder))
        workshop_export_dir = os.path.realpath(os.path.normpath(workshop_export_dir))
        
        # 验证临时目录在WorkshopExport下（使用commonpath更可靠）
        try:
            common_path = os.path.commonpath([temp_folder, workshop_export_dir])
            if common_path != workshop_export_dir:
                return JSONResponse({
                    "success": False,
                    "error": f"临时目录路径不在允许的范围内。临时目录: {temp_folder}, 允许路径: {workshop_export_dir}"
                }, status_code=403)
        except ValueError:
            # 如果路径不在同一驱动器上，commonpath会抛出ValueError
            return JSONResponse({
                "success": False,
                "error": "临时目录路径不在允许的范围内（路径验证失败）"
            }, status_code=403)
        
        # 删除临时目录
        if os.path.exists(temp_folder):
            await asyncio.to_thread(shutil.rmtree, temp_folder, ignore_errors=True)
            logger.info(f"临时目录已删除: {temp_folder}")
            return JSONResponse({
                "success": True,
                "message": "临时目录已删除"
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "临时目录不存在"
            }, status_code=404)
            
    except Exception as e:
        logger.error(f"清理临时目录失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.post('/publish')
async def publish_to_workshop(request: Request):
    steamworks = get_steamworks()
    from steamworks.exceptions import SteamNotLoadedException
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        data = await request.json()
        
        # 验证必要的字段
        required_fields = ['title', 'content_folder', 'visibility']
        for field in required_fields:
            if field not in data:
                return JSONResponse(content={"success": False, "error": f"缺少必要字段: {field}"}, status_code=400)
        
        # 提取数据
        title = data['title']
        content_folder = data['content_folder']
        visibility = int(data['visibility'])
        preview_image = data.get('preview_image', '')
        description = data.get('description', '')
        tags = data.get('tags', [])
        change_note = data.get('change_note', '初始发布')
        character_card_name = data.get('character_card_name')  # 新增：角色卡名称
        
        # 规范化路径处理 - 改进版，确保在所有情况下都能正确处理路径
        content_folder = unquote(content_folder)
        # 安全检查：验证content_folder是否在允许的范围内
        try:
            content_folder = _assert_under_base(content_folder, get_workshop_path())
        except PermissionError:
            return JSONResponse(content={
                "success": False,
                "error": "权限错误",
                "message": "指定的内容文件夹不在允许的范围内"
            }, status_code=403)

        # 处理Windows路径，确保使用正确的路径分隔符
        if os.name == 'nt':
            # 将所有路径分隔符统一为反斜杠
            content_folder = content_folder.replace('/', '\\')
            # 清理可能的错误前缀
            if content_folder.startswith('\\\\'):
                content_folder = content_folder[2:]
        else:
            # 非Windows系统使用正斜杠
            content_folder = content_folder.replace('\\', '/')
        
        # 验证内容文件夹存在并是一个目录
        if not os.path.exists(content_folder):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹不存在",
                "message": f"指定的内容文件夹不存在: {content_folder}"
            }, status_code=404)
        
        if not os.path.isdir(content_folder):
            return JSONResponse(content={
                "success": False,
                "error": "不是有效的文件夹",
                "message": f"指定的路径不是有效的文件夹: {content_folder}"
            }, status_code=400)
        
        # 增加内容文件夹检查：确保文件夹中至少有文件，验证文件夹是否包含内容
        if not any(os.scandir(content_folder)):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹为空",
                "message": f"内容文件夹为空，请确保包含要上传的文件: {content_folder}"
            }, status_code=400)
        
        # 检查文件夹权限
        if not os.access(content_folder, os.R_OK):
            return JSONResponse(content={
                "success": False,
                "error": "没有文件夹访问权限",
                "message": f"没有读取内容文件夹的权限: {content_folder}"
            }, status_code=403)
        
        # 处理预览图片路径
        if preview_image:
            preview_image = unquote(preview_image)
            if os.name == 'nt':
                preview_image = preview_image.replace('/', '\\')
                if preview_image.startswith('\\\\'):
                    preview_image = preview_image[2:]
            else:
                preview_image = preview_image.replace('\\', '/')
            
            # 验证预览图片存在
            if not os.path.exists(preview_image):
                # 如果指定的预览图不存在，尝试在内容文件夹中查找默认预览图
                logger.warning(f'指定的预览图片不存在，尝试在内容文件夹中查找: {preview_image}')
                auto_preview = find_preview_image_in_folder(content_folder)
                if auto_preview:
                    logger.info(f'找到自动预览图片: {auto_preview}')
                    preview_image = auto_preview
                else:
                    logger.warning('无法找到预览图片')
                    preview_image = ''
            
            if preview_image and not os.path.isfile(preview_image):
                return JSONResponse(content={
                    "success": False,
                    "error": "预览图片无效",
                    "message": f"预览图片路径不是有效的文件: {preview_image}"
                }, status_code=400)
            
            # 确保预览图片复制到内容文件夹并统一命名为preview.*
            if preview_image:
                # 获取原始文件扩展名
                file_extension = os.path.splitext(preview_image)[1].lower()
                # 在内容文件夹中创建统一命名的预览图片路径
                new_preview_path = os.path.join(content_folder, f'preview{file_extension}')
                
                # 复制预览图片到内容文件夹
                try:
                    import shutil
                    await asyncio.to_thread(shutil.copy2, preview_image, new_preview_path)
                    logger.info(f'预览图片已复制到内容文件夹并统一命名: {new_preview_path}')
                    # 使用新的统一命名的预览图片路径
                    preview_image = new_preview_path
                except Exception as e:
                    logger.error(f'复制预览图片到内容文件夹失败: {e}')
                    # 如果复制失败，继续使用原始路径
                    logger.warning(f'继续使用原始预览图片路径: {preview_image}')
        else:
            # 如果未指定预览图片，尝试自动查找
            auto_preview = find_preview_image_in_folder(content_folder)
            if auto_preview:
                logger.info(f'自动找到预览图片: {auto_preview}')
                preview_image = auto_preview
                
                # 确保自动找到的预览图片也统一命名为preview.*
                if preview_image:
                    # 获取原始文件扩展名
                    file_extension = os.path.splitext(preview_image)[1].lower()
                    # 如果不是统一命名，重命名
                    if not os.path.basename(preview_image).startswith('preview.'):
                        new_preview_path = os.path.join(content_folder, f'preview{file_extension}')
                        try:
                            import shutil
                            await asyncio.to_thread(shutil.copy2, preview_image, new_preview_path)
                            logger.info(f'自动找到的预览图片已统一命名: {new_preview_path}')
                            preview_image = new_preview_path
                        except Exception as e:
                            logger.error(f'重命名自动预览图片失败: {e}')
                            # 如果重命名失败，继续使用原始路径
                            logger.warning(f'继续使用原始预览图片路径: {preview_image}')

        try:
            voice_ref = await asyncio.to_thread(_resolve_workshop_voice_reference, content_folder)
            if voice_ref:
                logger.info(f"检测到参考语音清单: {voice_ref['manifest']['reference_audio']}")
        except (ValueError, FileNotFoundError) as e:
            return JSONResponse(content={
                "success": False,
                "error": "参考语音清单无效",
                "message": str(e)
            }, status_code=400)
        
        # 记录将要上传的内容信息
        logger.info(f"准备发布创意工坊物品: {title}")
        logger.info(f"内容文件夹: {content_folder}")
        logger.info(f"预览图片: {preview_image or '无'}")
        logger.info(f"可见性: {visibility}")
        logger.info(f"标签: {tags}")
        logger.info(f"内容文件夹包含文件数量: {len([f for f in os.listdir(content_folder) if os.path.isfile(os.path.join(content_folder, f))])}")
        logger.info(f"内容文件夹包含子文件夹数量: {len([f for f in os.listdir(content_folder) if os.path.isdir(os.path.join(content_folder, f))])}")

        if _is_workshop_publish_native_crash_risk():
            logger.error(
                "已阻止创意工坊上传：macOS ARM64 上的 SteamworksPy 回调会在 CreateItem/SubmitItemUpdate 阶段触发原生崩溃"
            )
            return JSONResponse(content={
                "success": False,
                "error": "当前平台暂不支持创意工坊上传",
                "message": "macOS Apple Silicon 环境下的 SteamworksPy 上传回调会导致主进程崩溃，请改用 Windows/Linux 环境或等待底层库修复。"
            }, status_code=503)
        
        # 使用线程池执行Steamworks API调用（因为这些是阻塞操作）
        loop = asyncio.get_event_loop()
        published_file_id = await loop.run_in_executor(
            None, 
            lambda: _publish_workshop_item(
                steamworks, title, description, content_folder, 
                preview_image, visibility, tags, change_note, character_card_name
            )
        )
        
        logger.info(f"成功发布创意工坊物品，ID: {published_file_id}")
        
        # 上传成功后，更新 .workshop_meta.json 并保存快照
        if character_card_name and published_file_id:
            try:
                # 计算内容哈希
                content_hash = calculate_content_hash(content_folder)
                
                # 构建上传快照
                uploaded_snapshot = {
                    'description': description,
                    'tags': tags,
                    'title': title,
                    'visibility': visibility
                }
                
                # 尝试从临时文件夹中读取角色卡数据
                try:
                    import glob
                    chara_files = glob.glob(os.path.join(content_folder, "*.chara.json"))
                    if chara_files:
                        chara_data = await read_json_async(chara_files[0])
                        uploaded_snapshot['character_data'] = chara_data
                        logger.info(f"已从临时文件夹读取角色卡数据")
                    
                    # 获取模型名称（从文件夹中查找模型目录）
                    for item in os.listdir(content_folder):
                        item_path = os.path.join(content_folder, item)
                        if os.path.isdir(item_path) and not item.startswith('.'):
                            # 检查是否是 Live2D 模型目录（包含 .model3.json 或 model.json）
                            model_files = glob.glob(os.path.join(item_path, "*.model3.json")) + \
                                         glob.glob(os.path.join(item_path, "*.model.json")) + \
                                         glob.glob(os.path.join(item_path, "model.json"))
                            if model_files:
                                uploaded_snapshot['model_name'] = item
                                logger.info(f"检测到模型目录: {item}")
                                break
                except Exception as read_error:
                    logger.warning(f"读取角色卡数据时出错: {read_error}")
                
                # 写入元数据文件（包含快照）
                await asyncio.to_thread(
                    write_workshop_meta,
                    character_card_name,
                    published_file_id,
                    content_hash,
                    uploaded_snapshot,
                )
                logger.info(f"已更新角色卡 {character_card_name} 的 .workshop_meta.json（包含快照）")
            except Exception as e:
                logger.error(f"更新 .workshop_meta.json 失败: {e}")
                # 不阻止成功响应，只记录错误
        
        return JSONResponse(content={
            "success": True,
            "published_file_id": published_file_id,
            "message": "发布成功"
        })
        
    except ValueError as ve:
        logger.error(f"参数错误: {ve}")
        return JSONResponse(content={"success": False, "error": str(ve)}, status_code=400)
    except SteamNotLoadedException as se:
        logger.error(f"Steamworks API错误: {se}")
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks API错误",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    except Exception as e:
        logger.error(f"发布到创意工坊失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

def _publish_workshop_item(steamworks, title, description, content_folder, preview_image, visibility, tags, change_note, character_card_name=None):
    """
    在单独的线程中执行Steam创意工坊发布操作
    """
    with publish_lock:
        try:
            # 在函数内部添加导入语句，确保枚举在函数作用域内可用
            from steamworks.enums import EWorkshopFileType, ERemoteStoragePublishedFileVisibility, EItemUpdateStatus
    
            # 优先从 .workshop_meta.json 读取物品ID
            item_id = None
            if character_card_name:
                try:
                    # 注意：_publish_workshop_item 是 sync def，在 worker 线程里跑，不能用 await。
                    # 其它 async 调用点已全部走 asyncio.to_thread，lint 已覆盖。
                    meta_data = read_workshop_meta(character_card_name)
                    if meta_data and meta_data.get('workshop_item_id'):
                        item_id = int(meta_data.get('workshop_item_id'))
                        logger.info(f"从 .workshop_meta.json 读取到物品ID: {item_id}")
                except Exception as e:
                    logger.warning(f"从 .workshop_meta.json 读取物品ID失败: {e}")
            
            # 如果 .workshop_meta.json 中没有，尝试从旧标记文件读取（向后兼容）
            if item_id is None:
                try:
                    if os.path.exists(content_folder) and os.path.isdir(content_folder):
                        # 查找以steam_workshop_id_开头的txt文件
                        import glob
                        marker_files = glob.glob(os.path.join(content_folder, "steam_workshop_id_*.txt"))
                        
                        if marker_files:
                            # 使用第一个找到的标记文件
                            marker_file = marker_files[0]
                            
                            # 从文件名中提取物品ID
                            import re
                            match = re.search(r'steam_workshop_id_([0-9]+)\.txt', marker_file)
                            if match:
                                item_id = int(match.group(1))
                                logger.info(f"检测到物品已上传，找到标记文件: {marker_file}，物品ID: {item_id}")
                except Exception as e:
                    logger.error(f"检查上传标记文件时出错: {e}")
            # 即使检查失败，也继续尝试上传，不阻止功能
        
            try:
                # 再次验证内容文件夹，确保在多线程环境中仍然有效
                if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
                    raise Exception(f"内容文件夹不存在或无效: {content_folder}")
            
                # 统计文件夹内容，确保有文件可上传
                file_count = 0
                for root, dirs, files in os.walk(content_folder):
                    file_count += len(files)
            
                if file_count == 0:
                    raise Exception(f"内容文件夹中没有找到可上传的文件: {content_folder}")
            
                logger.info(f"内容文件夹验证通过，包含 {file_count} 个文件")
            
                # 获取当前应用ID
                app_id = steamworks.app_id
                logger.info(f"使用应用ID: {app_id} 进行创意工坊上传")
            
                # 增强的Steam连接状态验证
                # 基础连接状态检查
                is_steam_running = steamworks.IsSteamRunning()
                is_overlay_enabled = steamworks.IsOverlayEnabled()
                is_logged_on = steamworks.Users.LoggedOn()
                steam_id = steamworks.Users.GetSteamID()
            
                # 应用相关权限检查
                app_owned = steamworks.Apps.IsAppInstalled(app_id)
                app_owned_license = steamworks.Apps.IsSubscribedApp(app_id)
                app_subscribed = steamworks.Apps.IsSubscribed()
            
                # 记录详细的连接状态
                logger.info(f"Steam客户端运行状态: {is_steam_running}")
                logger.info(f"Steam覆盖层启用状态: {is_overlay_enabled}")
                logger.info(f"用户登录状态: {is_logged_on}")
                logger.info(f"用户SteamID: {steam_id}")
                logger.info(f"应用ID {app_id} 安装状态: {app_owned}")
                logger.info(f"应用ID {app_id} 订阅许可状态: {app_owned_license}")
                logger.info(f"当前应用订阅状态: {app_subscribed}")
            
                # 预检查连接状态，如果存在问题则提前报错
                if not is_steam_running:
                    raise Exception("Steam客户端未运行，请先启动Steam客户端")
                if not is_logged_on:
                    raise Exception("用户未登录Steam，请确保已登录Steam客户端")
        
            except Exception as e:
                logger.error(f"Steam连接状态验证失败: {e}")
                # 即使验证失败也继续执行，但提供警告
                logger.warning("继续尝试创意工坊上传，但可能会因为Steam连接问题而失败")
        
            # 错误映射表，根据错误码提供更具体的错误信息
            error_codes = {
                1: "成功",
                10: "权限不足 - 可能需要登录Steam客户端或缺少创意工坊上传权限",
                111: "网络连接错误 - 无法连接到Steam网络",
                100: "服务不可用 - Steam创意工坊服务暂时不可用",
                8: "文件已存在 - 相同内容的物品已存在",
                34: "服务器忙 - Steam服务器暂时无法处理请求",
                116: "请求超时 - 与Steam服务器通信超时"
            }
        
            # 如果没有找到现有物品ID，则创建新物品
            if item_id is None:
                # 对于新物品，先创建一个空物品
                # 使用回调来处理创建结果
                created_item_id = [None]
                created_event = threading.Event()
                create_result = [None]  # 用于存储创建结果
            
                def onCreateItem(result):
                    nonlocal created_item_id, create_result
                    create_result[0] = result.result
                    # 直接从结构体读取字段而不是字典
                    if result.result == 1:  # k_EResultOK
                        created_item_id[0] = result.publishedFileId
                        logger.info(f"成功创建创意工坊物品，ID: {created_item_id[0]}")
                        created_event.set()
                    else:
                        error_msg = error_codes.get(result.result, f"未知错误码: {result.result}")
                        logger.error(f"创建创意工坊物品失败，错误码: {result.result} ({error_msg})")
                        created_event.set()
            
                # 设置创建物品回调
                steamworks.Workshop.SetItemCreatedCallback(onCreateItem)
            
                # 创建新的创意工坊物品（使用文件类型枚举表示UGC）
                logger.info(f"开始创建创意工坊物品: {title}")
                logger.info(f"调用SteamWorkshop.CreateItem({app_id}, {EWorkshopFileType.COMMUNITY})")
                steamworks.Workshop.CreateItem(app_id, EWorkshopFileType.COMMUNITY)
            
                # 等待创建完成或超时，增加超时时间并添加调试信息
                logger.info("等待创意工坊物品创建完成...")
                # 使用循环等待，定期调用run_callbacks处理回调
                start_time = time.time()
                timeout = 60  # 超时时间60秒
                while time.time() - start_time < timeout:
                    if created_event.is_set():
                        break
                    # 定期调用run_callbacks处理Steam API回调
                    try:
                        steamworks.run_callbacks()
                    except Exception as e:
                        logger.error(f"执行Steam回调时出错: {str(e)}")
                    # noqa: BLOCKING-OK - _publish_workshop_item 是同步函数，上层通过
                    # loop.run_in_executor(None, lambda: _publish_workshop_item(...)) 调度到线程池，
                    # 因此此处 time.sleep 只阻塞 executor 工作线程，不阻塞主事件循环。
                    time.sleep(0.1)  # 每100毫秒检查一次
            
                if not created_event.is_set():
                    logger.error("创建创意工坊物品超时，可能是网络问题或Steam服务暂时不可用")
                    raise TimeoutError("创建创意工坊物品超时")
            
                if created_item_id[0] is None:
                    # 提供更具体的错误信息
                    error_msg = error_codes.get(create_result[0], f"未知错误码: {create_result[0]}")
                    logger.error(f"创建创意工坊物品失败: {error_msg}")
                
                    # 针对错误码10（权限不足）提供更详细的错误信息和解决方案
                    detailed_error = error_msg
                    if create_result[0] == 10:
                        detailed_error = f"""权限不足 - 请确保:
1. Steam客户端已启动并登录
2. 您的Steam账号拥有应用ID {app_id} 的访问权限
3. Steam创意工坊功能未被禁用
4. 尝试以管理员权限运行应用程序
5. 检查防火墙设置是否阻止了应用程序访问Steam网络
6. 确保steam_appid.txt文件中的应用ID正确
7. 您的Steam账号有权限上传到该应用的创意工坊"""
                    logger.error("创意工坊上传失败 - 详细诊断信息:")
                    logger.error(f"- 应用ID: {app_id}")
                    logger.error(f"- Steam运行状态: {steamworks.IsSteamRunning()}")
                    logger.error(f"- 用户登录状态: {steamworks.Users.LoggedOn()}")
                    logger.error(f"- 应用订阅状态: {steamworks.Apps.IsSubscribedApp(app_id)}")
                    raise Exception(f"创建创意工坊物品失败: {detailed_error} (错误码: {create_result[0]})")
                # 将新创建的物品ID赋值给item_id变量
                item_id = created_item_id[0]
            else:
                logger.info(f"使用现有物品ID进行更新: {item_id}")       
        
            # 开始更新物品
            logger.info(f"开始更新物品内容: {title}")
            update_handle = steamworks.Workshop.StartItemUpdate(app_id, item_id)
        
            # 设置物品属性
            logger.info("设置物品基本属性...")
            steamworks.Workshop.SetItemTitle(update_handle, title)
            if description:
                steamworks.Workshop.SetItemDescription(update_handle, description)
        
            # 设置物品内容 - 这是文件上传的核心步骤
            logger.info(f"设置物品内容文件夹: {content_folder}")
            content_set_result = steamworks.Workshop.SetItemContent(update_handle, content_folder)
            logger.info(f"内容设置结果: {content_set_result}")
            
            # 设置预览图片（如果提供）
            if preview_image:
                logger.info(f"设置预览图片: {preview_image}")
                preview_set_result = steamworks.Workshop.SetItemPreview(update_handle, preview_image)
                logger.info(f"预览图片设置结果: {preview_set_result}")
        
            # 导入枚举类型并将整数值转换为枚举对象
            if visibility == 0:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
            elif visibility == 1:
                visibility_enum = ERemoteStoragePublishedFileVisibility.FRIENDS_ONLY
            elif visibility == 2:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PRIVATE
            else:
                # 默认设为公开
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
                
            # 设置物品可见性
            logger.info(f"设置物品可见性: {visibility_enum}")
            steamworks.Workshop.SetItemVisibility(update_handle, visibility_enum)
            
            # 设置标签（如果有）
            if tags:
                logger.info(f"设置物品标签: {tags}")
                steamworks.Workshop.SetItemTags(update_handle, tags)
            
            # 提交更新，使用回调来处理结果
            updated = [False]
            error_code = [0]
            update_event = threading.Event()
            
            def onSubmitItemUpdate(result):
                nonlocal updated, error_code
                # 直接从结构体读取字段而不是字典
                error_code[0] = result.result
                if result.result == 1:  # k_EResultOK
                    updated[0] = True
                    logger.info(f"物品更新提交成功，结果代码: {result.result}")
                else:
                    logger.error(f"提交创意工坊物品更新失败，错误码: {result.result}")
                update_event.set()
            
            # 设置更新物品回调
            steamworks.Workshop.SetItemUpdatedCallback(onSubmitItemUpdate)
            
            # 提交更新
            logger.info(f"开始提交物品更新，更新说明: {change_note}")
            steamworks.Workshop.SubmitItemUpdate(update_handle, change_note)
            
            # 等待更新完成或超时，增加超时时间并添加调试信息
            logger.info("等待创意工坊物品更新完成...")
            # 使用循环等待，定期调用run_callbacks处理回调
            start_time = time.time()
            timeout = 180  # 超时时间180秒
            last_progress = -1
            
            while time.time() - start_time < timeout:
                if update_event.is_set():
                    break
                # 定期调用run_callbacks处理Steam API回调
                try:
                    steamworks.run_callbacks()
                    # 记录上传进度（更详细的进度报告）
                    if update_handle:
                        progress = steamworks.Workshop.GetItemUpdateProgress(update_handle)
                        if 'status' in progress:
                            status_text = "未知"
                            if progress['status'] == EItemUpdateStatus.UPLOADING_CONTENT:
                                status_text = "上传内容"
                            elif progress['status'] == EItemUpdateStatus.UPLOADING_PREVIEW_FILE:
                                status_text = "上传预览图"
                            elif progress['status'] == EItemUpdateStatus.COMMITTING_CHANGES:
                                status_text = "提交更改"
                            
                            if 'progress' in progress:
                                current_progress = int(progress['progress'] * 100)
                                # 只有进度有明显变化时才记录日志
                                if current_progress != last_progress:
                                    logger.info(f"上传状态: {status_text}, 进度: {current_progress}%")
                                    last_progress = current_progress
                except Exception as e:
                    logger.error(f"执行Steam回调时出错: {str(e)}")
                # noqa: BLOCKING-OK - 同 Site 2，_publish_workshop_item 在 run_in_executor
                # 线程池中运行，此 sleep 只阻塞 executor 工作线程，不阻塞主事件循环。
                time.sleep(0.5)  # 每500毫秒检查一次，减少日志量
            
            if not update_event.is_set():
                logger.error("提交创意工坊物品更新超时，可能是网络问题或Steam服务暂时不可用")
                raise TimeoutError("提交创意工坊物品更新超时")
            
            if not updated[0]:
                # 根据错误码提供更详细的错误信息
                if error_code[0] == 25:  # LIMIT_EXCEEDED
                    error_msg = "提交创意工坊物品更新失败：内容超过Steam限制（错误码25）。请检查内容大小、文件数量或其他限制。"
                else:
                    error_msg = f"提交创意工坊物品更新失败，错误码: {error_code[0]}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            logger.info(f"创意工坊物品上传成功完成！物品ID: {item_id}")
            
            # 在原文件夹创建带物品ID的txt文件，标记为已上传
            # 在原文件夹创建带物品ID的txt文件，标记为已上传
            try:
                marker_file_path = os.path.join(content_folder, f"steam_workshop_id_{item_id}.txt")
                with open(marker_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Steam创意工坊物品ID: {item_id}\n")
                    f.write(f"上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
                    f.write(f"物品标题: {title}\n")
                logger.info(f"已在原文件夹创建上传标记文件: {marker_file_path}")
            except Exception as e:
                logger.error(f"创建上传标记文件失败: {e}")
                # 即使创建标记文件失败，也不影响物品上传的成功返回

            return item_id
        except Exception as e:
            logger.error(f"发布创意工坊物品时出错: {e}")
            raise


# ─── 创意工坊角色卡同步 ────────────────────────────────────────────────

async def sync_workshop_character_cards() -> dict:
    """
    服务端自动扫描所有已订阅且已安装的创意工坊物品，
    将其中的 .chara.json 角色卡同步到系统 characters.json。
    
    与前端 autoScanAndAddWorkshopCharacterCards() 等价，但在后端执行，
    可在服务器启动时直接调用，无需等待用户打开创意工坊管理页面。
    
    Returns:
        dict: {"added": int, "backfilled_faces": int, "skipped": int, "errors": int}
    """
    added_count = 0
    backfilled_face_count = 0
    skipped_count = 0
    error_count = 0
    
    try:
        # 1. 获取所有订阅的创意工坊物品
        items_result = await get_subscribed_workshop_items()
        
        # 兼容 JSONResponse 和普通 dict
        if isinstance(items_result, JSONResponse):
            # JSONResponse — 说明出错了，直接返回
            logger.warning("sync_workshop_character_cards: 获取订阅物品失败（返回了 JSONResponse）")
            return {"added": 0, "backfilled_faces": 0, "skipped": 0, "errors": 1}
        
        if not isinstance(items_result, dict) or not items_result.get('success'):
            logger.warning("sync_workshop_character_cards: 获取订阅物品失败")
            return {"added": 0, "backfilled_faces": 0, "skipped": 0, "errors": 1}
        
        subscribed_items = items_result.get('items', [])
        if not subscribed_items:
            logger.info("sync_workshop_character_cards: 没有订阅物品，跳过同步")
            return {"added": 0, "backfilled_faces": 0, "skipped": 0, "errors": 0}
        
        config_mgr = get_config_manager()

        def _write_fence_blocked_result() -> dict:
            return {
                "added": 0,
                "backfilled_faces": backfilled_face_count,
                "skipped": skipped_count,
                "errors": error_count,
                "blocked_by_write_fence": True,
            }

        def _abort_if_write_fence_active(message: str):
            if not is_write_fence_active(config_mgr):
                return None
            logger.info(message)
            return _write_fence_blocked_result()

        blocked_result = _abort_if_write_fence_active(
            "sync_workshop_character_cards: 检测到维护态写围栏，跳过本轮同步并等待后续重试"
        )
        if blocked_result is not None:
            return blocked_result
        
        # 使用全局锁序列化 load_characters -> save_characters 流程，防止并发覆写
        async with _ugc_sync_lock:
            characters = await config_mgr.aload_characters()
            if '猫娘' not in characters:
                characters['猫娘'] = {}
            deleted_character_names = _load_deleted_character_names(config_mgr)
            
            need_save = False
            
            # 2. 遍历所有已安装的物品
            for item in subscribed_items:
                installed_folder = item.get('installedFolder')
                if not installed_folder or not os.path.isdir(installed_folder):
                    continue
                
                item_id = item.get('publishedFileId', '')
                preview_image_path = find_preview_image_in_folder(installed_folder)
                
                # 3. 扫描 .chara.json 文件（递归遍历子目录）
                try:
                    chara_files = []
                    for root, _dirs, filenames in os.walk(installed_folder):
                        for filename in filenames:
                            if filename.endswith('.chara.json'):
                                chara_files.append(os.path.join(root, filename))
                    
                    for chara_file_path in chara_files:
                        try:
                            chara_data = await read_json_async(chara_file_path)
                            
                            chara_name_raw = chara_data.get('档案名') or chara_data.get('name')
                            if not chara_name_raw:
                                continue
                            chara_name = str(chara_name_raw).strip()
                            if not chara_name or '/' in chara_name or '\\' in chara_name or '..' in chara_name or len(chara_name) > 120:
                                logger.warning(f"sync_workshop_character_cards: 跳过非法角色名 '{chara_name_raw}' (物品 {item_id})")
                                continue

                            if chara_name in deleted_character_names:
                                skipped_count += 1
                                logger.info(
                                    "sync_workshop_character_cards: 跳过已删除角色 '%s'（tombstone 生效，物品 %s）",
                                    chara_name,
                                    item_id,
                                )
                                continue
                            
                            # 已存在则跳过（当前设计：仅填充缺失角色卡，不覆盖已有数据；
                            # 如需支持创意工坊更新覆写本地数据，可添加 allow_workshop_overwrite 配置项）
                            if chara_name in characters['猫娘']:
                                existing_data = characters['猫娘'].get(chara_name) or {}
                                if _is_matching_workshop_character(existing_data, item_id):
                                    try:
                                        blocked_result = _abort_if_write_fence_active(
                                            f"sync_workshop_character_cards: 回填角色卡封面前检测到维护态写围栏，跳过本轮同步并等待后续重试（角色 {chara_name}，物品 {item_id}）"
                                        )
                                        if blocked_result is not None:
                                            return blocked_result
                                        face_created = await asyncio.to_thread(
                                            _ensure_workshop_card_face_from_preview,
                                            config_mgr,
                                            chara_name,
                                            preview_image_path,
                                            item,
                                        )
                                        meta_created = False
                                        if not face_created:
                                            blocked_result = _abort_if_write_fence_active(
                                                f"sync_workshop_character_cards: 回填角色卡封面元数据前检测到维护态写围栏，跳过本轮同步并等待后续重试（角色 {chara_name}，物品 {item_id}）"
                                            )
                                            if blocked_result is not None:
                                                return blocked_result
                                            meta_created = await asyncio.to_thread(
                                                _ensure_workshop_card_face_meta,
                                                config_mgr,
                                                chara_name,
                                                item,
                                            )
                                        if face_created:
                                            backfilled_face_count += 1
                                            logger.info(
                                                "sync_workshop_character_cards: 已同步角色卡封面 '%s' (来自物品 %s)",
                                                chara_name,
                                                item_id,
                                            )
                                        if meta_created:
                                            logger.info(
                                                "sync_workshop_character_cards: 已补写角色卡封面元数据 '%s' (来自物品 %s)",
                                                chara_name,
                                                item_id,
                                            )
                                    except Exception as face_err:
                                        error_count += 1
                                        logger.warning(
                                            "sync_workshop_character_cards: 回填角色卡封面或元数据失败 %s (物品 %s): %s",
                                            chara_name,
                                            item_id,
                                            face_err,
                                        )
                                skipped_count += 1
                                continue
                            
                            # 构建角色数据，过滤保留字段
                            catgirl_data = {}
                            skip_keys = ['档案名', *CHARACTER_RESERVED_FIELDS]
                            for k, v in chara_data.items():
                                if k not in skip_keys and v is not None:
                                    catgirl_data[k] = v

                            # 工坊角色首次导入时强制清空 voice_id（当前工坊 voice_id 尚未适配）。
                            # 仅影响新增角色；已存在角色会在上面的分支直接跳过。
                            set_reserved(catgirl_data, 'voice_id', '')

                            # 角色来源与当前绑定资源来源分离保存：
                            # - character_origin 表示该角色最初来自哪个 Workshop 物品
                            # - avatar.asset_source 表示当前实际绑定的模型来源
                            model_binding = _derive_workshop_model_binding(chara_data)
                            subscriber_model_ref = _build_subscriber_workshop_model_ref(
                                item_id,
                                model_binding.get('model_ref', ''),
                            )
                            origin_display_name = _derive_workshop_origin_display_name(
                                model_binding.get('display_name_source', ''),
                                chara_name,
                            )

                            if item_id:
                                set_reserved(catgirl_data, 'character_origin', 'source', 'steam_workshop')
                                set_reserved(catgirl_data, 'character_origin', 'source_id', str(item_id))
                                set_reserved(
                                    catgirl_data,
                                    'character_origin',
                                    'display_name',
                                    origin_display_name,
                                )
                                set_reserved(
                                    catgirl_data,
                                    'character_origin',
                                    'model_ref',
                                    subscriber_model_ref,
                                )

                            # 如果角色卡带有可识别的模型路径，同时保存当前 avatar 绑定信息
                            # COMPAT(v1->v2): 旧字段 live2d_item_id 已迁移，不再写回平铺 key。
                            if subscriber_model_ref and item_id:
                                set_reserved(catgirl_data, 'avatar', 'asset_source_id', str(item_id))
                                set_reserved(catgirl_data, 'avatar', 'asset_source', 'steam_workshop')
                                set_reserved(
                                    catgirl_data,
                                    'avatar',
                                    'model_type',
                                    model_binding.get('stored_model_type', 'live2d'),
                                )

                                if model_binding.get('binding_model_type') == 'live2d':
                                    set_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', subscriber_model_ref)
                                    set_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', '')
                                    set_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', '')
                                elif model_binding.get('binding_model_type') == 'vrm':
                                    set_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', '')
                                    set_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', subscriber_model_ref)
                                    set_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', '')
                                elif model_binding.get('binding_model_type') == 'mmd':
                                    set_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', '')
                                    set_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', '')
                                    set_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', subscriber_model_ref)
                            
                            characters['猫娘'][chara_name] = catgirl_data
                            need_save = True
                            added_count += 1
                            logger.info(f"sync_workshop_character_cards: 添加角色卡 '{chara_name}' (来自物品 {item_id})")

                            # 同步生成本地卡面和 sidecar，前端封面只认 card_faces/{name}.png
                            try:
                                blocked_result = _abort_if_write_fence_active(
                                    f"sync_workshop_character_cards: 生成角色卡封面前检测到维护态写围栏，跳过本轮同步并等待后续重试（角色 {chara_name}，物品 {item_id}）"
                                )
                                if blocked_result is not None:
                                    return blocked_result
                                face_created = await asyncio.to_thread(
                                    _ensure_workshop_card_face_from_preview,
                                    config_mgr,
                                    chara_name,
                                    preview_image_path,
                                    item,
                                )
                                if face_created:
                                    logger.info(
                                        "sync_workshop_character_cards: 已生成角色卡封面 '%s' (来自物品 %s)",
                                        chara_name,
                                        item_id,
                                    )
                                elif item:
                                    blocked_result = _abort_if_write_fence_active(
                                        f"sync_workshop_character_cards: 补写角色卡封面元数据前检测到维护态写围栏，跳过本轮同步并等待后续重试（角色 {chara_name}，物品 {item_id}）"
                                    )
                                    if blocked_result is not None:
                                        return blocked_result
                                    await asyncio.to_thread(
                                        _ensure_workshop_card_face_meta,
                                        config_mgr,
                                        chara_name,
                                        item,
                                    )
                            except Exception as face_meta_err:
                                error_count += 1
                                logger.warning(
                                    "sync_workshop_character_cards: 补写角色卡封面或元数据失败 %s (物品 %s): %s",
                                    chara_name,
                                    item_id,
                                    face_meta_err,
                                )
                            
                        except Exception as e:
                            logger.warning(f"sync_workshop_character_cards: 处理文件 {chara_file_path} 失败: {e}")
                            error_count += 1
                            
                except Exception as e:
                    logger.warning(f"sync_workshop_character_cards: 扫描文件夹 {installed_folder} 失败: {e}")
                    error_count += 1
            
            # 4. 保存并重新加载角色配置
            if need_save:
                blocked_result = _abort_if_write_fence_active(
                    "sync_workshop_character_cards: 保存前检测到维护态写围栏，跳过本轮同步并等待后续重试"
                )
                if blocked_result is not None:
                    return blocked_result

                try:
                    await config_mgr.asave_characters(characters)
                except MaintenanceModeError:
                    logger.info("sync_workshop_character_cards: 保存时进入维护态写围栏，跳过本轮同步并等待后续重试")
                    return _write_fence_blocked_result()

                logger.info(f"sync_workshop_character_cards: 已保存，新增 {added_count} 个角色卡，回填 {backfilled_face_count} 个封面")
                
                try:
                    initialize_character_data = get_initialize_character_data()
                    if initialize_character_data:
                        await initialize_character_data()
                        logger.info("sync_workshop_character_cards: 已重新加载角色配置")
                except Exception as e:
                    logger.warning(f"sync_workshop_character_cards: 重新加载角色配置失败: {e}")
            else:
                if backfilled_face_count > 0:
                    logger.info(f"sync_workshop_character_cards: 无新增角色卡，但已回填 {backfilled_face_count} 个封面")
                else:
                    logger.info("sync_workshop_character_cards: 无需更新，所有角色卡已存在")
        
    except Exception as e:
        logger.error(f"sync_workshop_character_cards: 同步过程出错: {e}", exc_info=True)
        error_count += 1
    
    return {"added": added_count, "backfilled_faces": backfilled_face_count, "skipped": skipped_count, "errors": error_count}


@router.post('/sync-characters')
async def api_sync_workshop_character_cards():
    """
    手动触发同步创意工坊角色卡到系统。
    扫描所有已安装的订阅物品中的 .chara.json 并添加缺失的角色卡。
    """
    try:
        result = await sync_workshop_character_cards()
        if result.get("blocked_by_write_fence"):
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "WRITE_FENCE_ACTIVE",
                    "error": "当前处于存储维护态，暂时不能同步创意工坊角色卡，请稍后重试。",
                    "added": result.get("added", 0),
                    "backfilled_faces": result.get("backfilled_faces", 0),
                    "skipped": result.get("skipped", 0),
                    "errors": result.get("errors", 0),
                },
            )
        return {
            "success": True,
            "added": result["added"],
            "backfilled_faces": result.get("backfilled_faces", 0),
            "skipped": result["skipped"],
            "errors": result["errors"],
            "message": (
                f"同步完成：新增 {result['added']} 个角色卡，"
                f"回填 {result.get('backfilled_faces', 0)} 个封面，"
                f"跳过 {result['skipped']} 个已存在，{result['errors']} 个错误"
            )
        }
    except Exception as e:
        logger.error(f"API sync-characters 失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)

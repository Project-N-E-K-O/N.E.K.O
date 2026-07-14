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

"""Sync of workshop character cards into the local system and its
API endpoints.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .meta import (
    _build_subscriber_workshop_model_ref,
    _derive_workshop_model_binding,
    _derive_workshop_origin_display_name,
    _load_deleted_character_names,
    _remove_deleted_character_tombstones,
)
from .preview_cards import (
    _ensure_workshop_card_face_from_preview,
    _ensure_workshop_card_face_meta,
    _is_matching_workshop_character,
    find_preview_image_in_folder,
)
from .ugc import get_subscribed_workshop_items

import os
import asyncio
from pathlib import Path
from fastapi.responses import JSONResponse
from ..shared_state import get_config_manager, get_initialize_character_data
from utils.cloudsave_runtime import MaintenanceModeError, is_write_fence_active
from utils.file_utils import read_json_async
from utils.config_manager import set_reserved
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from config import CHARACTER_RESERVED_FIELDS


# 全局互斥锁，用于序列化角色卡同步的 load_characters -> save_characters 流程
_ugc_sync_lock = asyncio.Lock()


# ─── 创意工坊角色卡同步 ────────────────────────────────────────────────

def _find_casefold_conflict_name(existing_names, candidate_name: str) -> str | None:
    candidate = str(candidate_name or "").strip()
    if not candidate:
        return None
    candidate_casefold = candidate.casefold()
    for existing_name in existing_names:
        normalized_existing = str(existing_name or "").strip()
        if not normalized_existing or normalized_existing == candidate:
            continue
        if normalized_existing.casefold() == candidate_casefold:
            return normalized_existing
    return None

async def sync_workshop_character_cards(
    target_item_id: str | int | None = None,
    restore_deleted: bool = False,
) -> dict:
    """
    Server-side auto-scan of all subscribed and installed Workshop items,
    syncing the .chara.json character cards inside them into the system characters.json.
    
    Equivalent to the frontend's autoScanAndAddWorkshopCharacterCards(), but runs
    in the backend and can be called directly at server startup, without waiting
    for the user to open the Workshop management page.
    
    Returns:
        dict: {"added": int, "backfilled_faces": int, "skipped": int, "errors": int}
    """
    # 复用 characters_router 的字段顺序 helper：函数级 lazy import，既避免顶层 router→router 依赖，
    # 又只在每次同步开始导入一次，不在每个 .chara.json 上重复走 import 路径。
    from main_routers.characters_router import (
        _extract_catgirl_field_order_payload as _extract_field_order,
        _sync_catgirl_field_order as _sync_field_order,
    )

    added_count = 0
    backfilled_face_count = 0
    skipped_count = 0
    error_count = 0
    target_item_id_str = str(target_item_id).strip() if target_item_id is not None else ""
    target_found = not bool(target_item_id_str)
    scanned_item_count = 0
    installed_item_count = 0
    found_character_names: list[str] = []
    added_character_names: list[str] = []
    existing_character_names: list[str] = []
    deleted_character_names_seen: list[str] = []
    restored_deleted_names: list[str] = []
    tombstone_cleanup_deferred = False

    def _append_unique(bucket: list[str], name: str) -> None:
        normalized_name = str(name or "").strip()
        if normalized_name and normalized_name not in bucket:
            bucket.append(normalized_name)

    def _sync_result(*, blocked_by_write_fence: bool = False, code: str | None = None) -> dict:
        payload = {
            "added": added_count,
            "backfilled_faces": backfilled_face_count,
            "skipped": skipped_count,
            "errors": error_count,
        }
        if blocked_by_write_fence:
            payload["blocked_by_write_fence"] = True
        if tombstone_cleanup_deferred:
            payload["tombstone_cleanup_deferred"] = True
        if target_item_id_str:
            payload.update({
                "target_item_id": target_item_id_str,
                "target_found": target_found,
                "scanned_items": scanned_item_count,
                "installed_items": installed_item_count,
                "found_character_names": found_character_names,
                "added_character_names": added_character_names,
                "existing_character_names": existing_character_names,
                "deleted_character_names": deleted_character_names_seen,
                "restored_deleted_names": restored_deleted_names,
            })
        if code:
            payload["code"] = code
        return payload
    
    try:
        # 1. 获取所有订阅的创意工坊物品
        items_result = await get_subscribed_workshop_items()
        
        # 兼容 JSONResponse 和普通 dict
        if isinstance(items_result, JSONResponse):
            # JSONResponse — 说明出错了，直接返回
            logger.warning("sync_workshop_character_cards: 获取订阅物品失败（返回了 JSONResponse）")
            error_count += 1
            return _sync_result(code="WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE")
        
        if not isinstance(items_result, dict) or not items_result.get('success'):
            logger.warning("sync_workshop_character_cards: 获取订阅物品失败")
            error_count += 1
            return _sync_result(code="WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE")
        
        subscribed_items = items_result.get('items', [])
        if target_item_id_str:
            subscribed_items = [
                item for item in subscribed_items
                if str(item.get('publishedFileId', '')).strip() == target_item_id_str
            ]
            target_found = bool(subscribed_items)
            if not subscribed_items:
                logger.info(
                    "sync_workshop_character_cards: 未找到目标订阅物品 %s",
                    target_item_id_str,
                )
                return _sync_result(code="WORKSHOP_ITEM_NOT_FOUND")
        elif not subscribed_items:
            logger.info("sync_workshop_character_cards: 没有订阅物品，跳过同步")
            return _sync_result()
        
        config_mgr = get_config_manager()

        def _write_fence_blocked_result() -> dict:
            payload = _sync_result(blocked_by_write_fence=True)
            payload["added"] = 0
            return payload

        def _abort_if_write_fence_active(message: str):
            if not is_write_fence_active(config_mgr):
                return None
            logger.info(message)
            return _write_fence_blocked_result()

        async def _clear_restored_existing_tombstones():
            nonlocal error_count
            restored_existing_candidates = [
                tombstone_name
                for character_name, tombstone_name
                in confirmed_recoverable_existing_names.items()
                if character_name not in restored_deleted_names
            ]
            if not restored_existing_candidates:
                return None

            blocked_result = _abort_if_write_fence_active(
                "sync_workshop_character_cards: 移除已存在恢复角色 tombstone 前检测到维护态写围栏，跳过本轮同步并等待后续重试"
            )
            if blocked_result is not None:
                return blocked_result

            try:
                removed_names = await asyncio.to_thread(
                    _remove_deleted_character_tombstones,
                    config_mgr,
                    restored_existing_candidates,
                )
                removed_casefolds = {name.casefold() for name in removed_names}
                for character_name, tombstone_name in (
                    confirmed_recoverable_existing_names.items()
                ):
                    if tombstone_name.casefold() in removed_casefolds:
                        _append_unique(restored_deleted_names, character_name)
                if removed_names:
                    logger.info(
                        "sync_workshop_character_cards: 已移除已存在恢复角色的 tombstone: %s",
                        ", ".join(removed_names),
                    )
            except Exception as tombstone_err:
                error_count += 1
                logger.warning(
                    "sync_workshop_character_cards: 移除已存在恢复角色 tombstone 失败: %s",
                    tombstone_err,
                )
            return None

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
            pending_added_catgirls = {}
            pending_card_face_writes = {}
            pending_item_ids = {}
            pending_restore_tombstone_names: dict[str, str] = {}
            confirmed_recoverable_existing_names: dict[str, str] = {}
            
            # 2. 遍历所有已安装的物品
            for item in subscribed_items:
                scanned_item_count += 1
                installed_folder = item.get('installedFolder')
                if not installed_folder or not os.path.isdir(installed_folder):
                    continue
                installed_item_count += 1
                
                item_id = item.get('publishedFileId', '')
                
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
                            name_validation = validate_character_name(
                                chara_name_raw,
                                # Workshop 中仍有严格命名规则启用前发布的角色卡（例如
                                # ``N.E.K.O``）。嵌入式点号本身是安全的，现有角色路径、
                                # memory 与 cloudsave 也已兼容；这里只放宽点号，统一校验仍会
                                # 拒绝 ``..``、尾随点号、路径分隔符和其它危险名称。
                                allow_dots=True,
                                max_units=PROFILE_NAME_MAX_UNITS,
                            )
                            chara_name = name_validation.normalized
                            if not name_validation.ok:
                                logger.warning(
                                    "sync_workshop_character_cards: 跳过非法角色名 %r (code=%s, 物品 %s)",
                                    chara_name_raw,
                                    name_validation.code,
                                    item_id,
                                )
                                continue
                            _append_unique(found_character_names, chara_name)

                            deleted_name = (
                                chara_name
                                if chara_name in deleted_character_names
                                else _find_casefold_conflict_name(
                                    deleted_character_names,
                                    chara_name,
                                )
                            )
                            if deleted_name is not None:
                                _append_unique(deleted_character_names_seen, chara_name)
                                if restore_deleted:
                                    pending_restore_tombstone_names[chara_name] = deleted_name
                                else:
                                    skipped_count += 1
                                    logger.info(
                                        "sync_workshop_character_cards: 跳过已删除角色 '%s'（tombstone 生效，物品 %s）",
                                        chara_name,
                                        item_id,
                                    )
                                    continue
                            conflict_name = _find_casefold_conflict_name(
                                characters['猫娘'].keys(),
                                chara_name,
                            )
                            if conflict_name is not None:
                                _append_unique(existing_character_names, conflict_name)
                                skipped_count += 1
                                logger.warning(
                                    "sync_workshop_character_cards: 跳过大小写折叠冲突角色 '%s'（与 '%s' 共用 casefold，物品 %s）",
                                    chara_name,
                                    conflict_name,
                                    item_id,
                                )
                                continue
                            chara_file_stem = Path(chara_file_path).name[:-11]
                            preview_image_path = find_preview_image_in_folder(
                                installed_folder,
                                chara_name,
                                chara_file_stem,
                            )
                            
                            # 已存在则跳过（当前设计：仅填充缺失角色卡，不覆盖已有数据；
                            # 如需支持创意工坊更新覆写本地数据，可添加 allow_workshop_overwrite 配置项）
                            if chara_name in characters['猫娘']:
                                if chara_name in pending_added_catgirls:
                                    # 同一次扫描内的重复同名卡仍处于待合并状态，不能按已存在角色补写封面；
                                    # 最终是否导入要等保存前用最新版 characters.json 再判定。
                                    skipped_count += 1
                                    logger.info(
                                        "sync_workshop_character_cards: 跳过重复待添加角色 '%s'（物品 %s）",
                                        chara_name,
                                        item_id,
                                    )
                                    continue
                                _append_unique(existing_character_names, chara_name)
                                existing_data = characters['猫娘'].get(chara_name) or {}
                                existing_matches_item = _is_matching_workshop_character(existing_data, item_id)
                                if existing_matches_item and restore_deleted and chara_name in pending_restore_tombstone_names:
                                    confirmed_recoverable_existing_names[chara_name] = (
                                        pending_restore_tombstone_names[chara_name]
                                    )
                                if existing_matches_item:
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

                            # 字段创建顺序元数据被当作保留字段过滤掉了，这里把它提回 _reserved.field_order
                            # （helper 在函数开头一次性导入）；否则订阅同步到的工坊卡会丢失显式顺序，
                            # 数字 key 自定义字段会在安装后再次按对象枚举顺序乱序。
                            _sync_field_order(catgirl_data, _extract_field_order(chara_data))

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
                            pending_added_catgirls[chara_name] = catgirl_data
                            pending_card_face_writes[chara_name] = {
                                'preview_image_path': preview_image_path,
                                'item': item,
                            }
                            pending_item_ids[chara_name] = item_id
                            need_save = True
                            added_count += 1
                            logger.info(f"sync_workshop_character_cards: 发现待添加角色卡 '{chara_name}' (来自物品 {item_id})")
                            
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

                characters_to_save = characters
                actually_added_names = []
                if pending_added_catgirls:
                    # 启动期工坊同步是后台任务：扫描可能很慢，期间用户可能已经修改了角色卡
                    # 或完成初始人格选择。保存前必须重新读取最新配置，只把本轮新增角色合入，
                    # 避免用扫描前的旧快照整包覆盖用户刚写入的字段。
                    latest_characters = await config_mgr.aload_characters()
                    if not isinstance(latest_characters, dict):
                        logger.warning(
                            "sync_workshop_character_cards: 保存前检测到 characters.json 根对象结构无效（%s），取消本轮同步保存",
                            type(latest_characters).__name__,
                        )
                        added_count = 0
                        error_count += 1
                        return _sync_result()
                    latest_catgirls = latest_characters.get('猫娘')
                    if not isinstance(latest_catgirls, dict):
                        logger.warning(
                            "sync_workshop_character_cards: 保存前检测到 characters.json 猫娘字段结构无效（%s），取消本轮同步保存",
                            type(latest_catgirls).__name__,
                        )
                        added_count = 0
                        error_count += 1
                        return _sync_result()

                    latest_deleted_character_names = _load_deleted_character_names(config_mgr)
                    actually_added_count = 0
                    skipped_due_to_race_count = 0
                    for pending_name, pending_payload in pending_added_catgirls.items():
                        pending_name_is_deleted = (
                            pending_name in latest_deleted_character_names
                            or _find_casefold_conflict_name(
                                latest_deleted_character_names,
                                pending_name,
                            ) is not None
                        )
                        conflict_name = _find_casefold_conflict_name(
                            latest_catgirls.keys(),
                            pending_name,
                        )
                        if (
                            (pending_name_is_deleted and not restore_deleted)
                            or pending_name in latest_catgirls
                            or conflict_name is not None
                        ):
                            skipped_due_to_race_count += 1
                            if pending_name in latest_catgirls:
                                _append_unique(existing_character_names, pending_name)
                                if (
                                    restore_deleted
                                    and pending_name in pending_restore_tombstone_names
                                    and _is_matching_workshop_character(
                                        latest_catgirls.get(pending_name) or {},
                                        pending_item_ids.get(pending_name, ""),
                                    )
                                ):
                                    confirmed_recoverable_existing_names[pending_name] = (
                                        pending_restore_tombstone_names[pending_name]
                                    )
                            elif conflict_name is not None:
                                _append_unique(existing_character_names, conflict_name)
                                logger.warning(
                                    "sync_workshop_character_cards: 保存前跳过大小写折叠冲突角色 '%s'（与 '%s' 共用 casefold）",
                                    pending_name,
                                    conflict_name,
                                )
                            continue
                        latest_catgirls[pending_name] = pending_payload
                        actually_added_count += 1
                        actually_added_names.append(pending_name)

                    added_count = actually_added_count
                    skipped_count += skipped_due_to_race_count
                    if actually_added_count <= 0:
                        need_save = False
                    else:
                        if not latest_characters.get('当前猫娘') and latest_catgirls:
                            latest_characters['当前猫娘'] = next(iter(latest_catgirls), '')
                        characters_to_save = latest_characters

                if need_save:
                    try:
                        await config_mgr.asave_characters(characters_to_save)
                    except MaintenanceModeError:
                        logger.info("sync_workshop_character_cards: 保存时进入维护态写围栏，跳过本轮同步并等待后续重试")
                        return _write_fence_blocked_result()

                    logger.info(f"sync_workshop_character_cards: 已保存，新增 {added_count} 个角色卡，回填 {backfilled_face_count} 个封面")

                    for added_name in actually_added_names:
                        _append_unique(added_character_names, added_name)

                    if restore_deleted and actually_added_names:
                        restored_candidates = {
                            name: pending_restore_tombstone_names[name]
                            for name in actually_added_names
                            if name in pending_restore_tombstone_names
                        }
                        if restored_candidates:
                            try:
                                removed_names = await asyncio.to_thread(
                                    _remove_deleted_character_tombstones,
                                    config_mgr,
                                    list(restored_candidates.values()),
                                )
                                removed_casefolds = {
                                    name.casefold() for name in removed_names
                                }
                                for character_name, tombstone_name in (
                                    restored_candidates.items()
                                ):
                                    if tombstone_name.casefold() in removed_casefolds:
                                        _append_unique(
                                            restored_deleted_names,
                                            character_name,
                                        )
                                if removed_names:
                                    logger.info(
                                        "sync_workshop_character_cards: 已移除手动恢复角色的 tombstone: %s",
                                        ", ".join(removed_names),
                                    )
                            except Exception as tombstone_err:
                                error_count += 1
                                logger.warning(
                                    "sync_workshop_character_cards: 移除手动恢复角色 tombstone 失败: %s",
                                    tombstone_err,
                                )

                    for added_name in actually_added_names:
                        write_info = pending_card_face_writes.get(added_name) or {}
                        write_item = write_info.get('item') if isinstance(write_info, dict) else None
                        write_item_id = write_item.get('publishedFileId', '') if isinstance(write_item, dict) else ''
                        if is_write_fence_active(config_mgr):
                            logger.info(
                                "sync_workshop_character_cards: 角色已保存，但维护态写围栏已开启，跳过角色卡封面生成（角色 %s）",
                                added_name,
                            )
                            continue
                        try:
                            face_created = await asyncio.to_thread(
                                _ensure_workshop_card_face_from_preview,
                                config_mgr,
                                added_name,
                                write_info.get('preview_image_path') if isinstance(write_info, dict) else None,
                                write_item,
                            )
                            if face_created:
                                logger.info(
                                    "sync_workshop_character_cards: 已生成角色卡封面 '%s' (来自物品 %s)",
                                    added_name,
                                    write_item_id,
                                )
                            elif write_item:
                                if is_write_fence_active(config_mgr):
                                    logger.info(
                                        "sync_workshop_character_cards: 角色已保存，但维护态写围栏已开启，跳过角色卡封面元数据补写（角色 %s）",
                                        added_name,
                                    )
                                    continue
                                await asyncio.to_thread(
                                    _ensure_workshop_card_face_meta,
                                    config_mgr,
                                    added_name,
                                    write_item,
                                )
                        except Exception as face_meta_err:
                            error_count += 1
                            logger.warning(
                                "sync_workshop_character_cards: 补写角色卡封面或元数据失败 %s (物品 %s): %s",
                                added_name,
                                write_item_id,
                                face_meta_err,
                            )

                blocked_result = await _clear_restored_existing_tombstones()
                if blocked_result is not None:
                    tombstone_cleanup_deferred = True
                    logger.warning(
                        "sync_workshop_character_cards: 角色已保存，但 tombstone 清理被维护态写围栏延后"
                    )
                
                try:
                    initialize_character_data = get_initialize_character_data()
                    if initialize_character_data:
                        await initialize_character_data()
                        logger.info("sync_workshop_character_cards: 已重新加载角色配置")
                except Exception as e:
                    logger.warning(f"sync_workshop_character_cards: 重新加载角色配置失败: {e}")
            else:
                blocked_result = await _clear_restored_existing_tombstones()
                if blocked_result is not None:
                    return blocked_result
                if backfilled_face_count > 0:
                    logger.info(f"sync_workshop_character_cards: 无新增角色卡，但已回填 {backfilled_face_count} 个封面")
                else:
                    logger.info("sync_workshop_character_cards: 无需更新，所有角色卡已存在")
        
    except Exception as e:
        # 真实后端异常（磁盘/Steamworks/序列化等）必须显式标记为同步失败，
        # 否则下游 API 只按业务 code 分支，会把它误判成
        # WORKSHOP_CHARACTER_NOT_FOUND / NOT_ADDED，让前端把服务端故障当成
        # “此订阅里没有角色卡”。用专属 code 兜住，区别于逐角色的部分错误。
        logger.error(f"sync_workshop_character_cards: 同步过程出错: {e}", exc_info=True)
        error_count += 1
        return _sync_result(code="WORKSHOP_SYNC_FAILED")

    return _sync_result()


@router.post('/sync-characters')
async def api_sync_workshop_character_cards():
    """
    Manually trigger syncing Workshop character cards into the system.
    Scans the .chara.json in all installed subscribed items and adds the missing character cards.
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
        if result.get("code") == "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE":
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE",
                    "error": "获取订阅物品失败，请确认 Steam 客户端已运行并已登录。",
                    **result,
                },
            )
        if result.get("code") == "WORKSHOP_SYNC_FAILED":
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "code": "WORKSHOP_SYNC_FAILED",
                    "error": "同步创意工坊角色卡时发生内部错误，请稍后重试。",
                    **result,
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


@router.post('/sync-character/{item_id}')
async def api_sync_single_workshop_character_card(item_id: str):
    """
    Manually add character cards from the specified subscribed item.
    Unlike the startup auto-sync, this entry allows users to restore Workshop
    character cards they previously deleted manually.
    """
    try:
        result = await sync_workshop_character_cards(
            target_item_id=item_id,
            restore_deleted=True,
        )
        if result.get("blocked_by_write_fence"):
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "WRITE_FENCE_ACTIVE",
                    "error": "当前处于存储维护态，暂时不能同步创意工坊角色卡，请稍后重试。",
                    **result,
                },
            )

        if result.get("code") == "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE":
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE",
                    "error": "获取订阅物品失败，请确认 Steam 客户端已运行并已登录。",
                    **result,
                },
            )

        if result.get("code") == "WORKSHOP_SYNC_FAILED":
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "code": "WORKSHOP_SYNC_FAILED",
                    "error": "同步创意工坊角色卡时发生内部错误，请稍后重试。",
                    **result,
                },
            )

        if not result.get("target_found"):
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "code": result.get("code") or "WORKSHOP_ITEM_NOT_FOUND",
                    "error": "未找到对应的订阅物品，请刷新订阅列表后重试。",
                    **result,
                },
            )

        restored_names = result.get("restored_deleted_names") or []
        if result.get("added", 0) > 0 or restored_names:
            added_names = result.get("added_character_names") or []
            successful_names = []
            for name in [*added_names, *restored_names]:
                if name and name not in successful_names:
                    successful_names.append(name)
            names_text = "、".join(successful_names) if successful_names else "角色卡"
            # 前端成功提示只读 added_character_names；仅清 tombstone 的恢复成功路径
            # 里它本来是空的，会把恢复角色名丢成“未知角色卡”。把去重后的成功名字
            # 回写过去，同时保留 restored_deleted_names（来自 **result）。
            return {
                "success": True,
                "message": f"已加入角色卡：{names_text}",
                **result,
                "added_character_names": successful_names,
            }

        existing_names = [
            name for name in (result.get("existing_character_names") or [])
            if name not in restored_names
        ]
        if existing_names:
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "code": "WORKSHOP_CHARACTER_ALREADY_EXISTS",
                    "error": "角色卡已存在。",
                    **result,
                },
            )

        if not result.get("found_character_names"):
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "code": "WORKSHOP_CHARACTER_NOT_FOUND",
                    "error": "此订阅内容中未找到可加入的角色卡，请确认内容已下载完成。",
                    **result,
                },
            )

        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "code": "WORKSHOP_CHARACTER_NOT_ADDED",
                "error": "未加入新的角色卡。",
                **result,
            },
        )
    except Exception as e:
        logger.error(f"API sync-character 失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)

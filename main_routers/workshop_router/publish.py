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

"""Workshop publish flow: prepare-upload, publish, cleanup and upload
status (guarded by publish_lock).

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .config_files import _assert_under_base
from .content_gate import (
    CLEANUP_PURPOSE,
    PUBLISH_PURPOSE,
    ContentFolderBusy,
    claim_content_folder,
)
from .meta import calculate_content_hash, read_workshop_meta, write_workshop_meta
from .preview_cards import find_preview_image_in_folder
from .ugc import (
    UnsupportedUGCDetailsError,
    _query_ugc_details_batch,
    get_subscribed_workshop_items,
)
from .voice_manifest import resolve_voice_reference_serialized

import os
import sys
import time
import asyncio
import threading
import platform
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import ensure_steamworks as get_steamworks, get_config_manager
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.workshop_utils import (
    get_workshop_path_async,
)


# Global mutex serializes workshop publication, preventing concurrent callback confusion.
publish_lock = threading.Lock()


async def _validate_existing_workshop_item(steamworks, item_id: int) -> tuple[bool, str]:
    """Guard updates against stale metadata pointing at an unrelated item.

    The character-card sidecar is the local source of truth for update intent,
    but old/copy-pasted sidecars can contain an item ID that no longer belongs
    to the card. When rich UGC details are available, verify the Steam owner
    before entering ``StartItemUpdate``. The Workshop title is intentionally
    not compared here because renaming an existing item is a supported update.
    Older wrappers may not expose the query bridge; preserve the existing
    update path in that case and let Steam perform its normal authorization
    check.
    """
    try:
        details_by_id = await _query_ugc_details_batch(
            steamworks, [int(item_id)], max_retries=1
        )
    except UnsupportedUGCDetailsError:
        logger.info(
            "跳过 Workshop 物品 %s 的更新归属校验：当前 Steamworks wrapper 不支持 UGC 详情查询",
            item_id,
        )
        return True, ''
    except Exception as exc:
        logger.warning("校验 Workshop 物品 %s 的更新归属失败，将交由 Steam 校验: %s", item_id, exc)
        return True, ''

    details = details_by_id.get(int(item_id))
    if details is None:
        # A transient query failure should not turn a valid update into a hard
        # outage. Steam will still reject an invalid or unauthorized update.
        logger.warning("未查询到 Workshop 物品 %s 的详情，将交由 Steam 校验", item_id)
        return True, ''

    try:
        owner_id = int(getattr(details, 'steamIDOwner', 0) or 0)
        current_user_id = int(steamworks.Users.GetSteamID())
    except (TypeError, ValueError, AttributeError):
        owner_id = current_user_id = 0

    if owner_id and current_user_id and owner_id != current_user_id:
        return False, f'Workshop 物品 {item_id} 不属于当前 Steam 账号，已阻止更新'

    return True, ''


@router.get('/check-upload-status')
async def check_upload_status(item_path: str = None):
    try:
        # Validate path parameters.
        if not item_path:
            return JSONResponse(content={
                "success": False,
                "error": "未提供物品文件夹路径"
            }, status_code=400)
        
        # Security: use get_workshop_path() as the base directory.
        base_workshop_folder = os.path.abspath(
            os.path.normpath(await get_workshop_path_async())
        )
        
        # Windows paths: ensure correct separators.
        if os.name == 'nt':  # Windows.
            # Decode and process Windows paths.
            decoded_item_path = unquote(item_path)
            # Replace forward slashes with backslashes for Windows path format.
            decoded_item_path = decoded_item_path.replace('/', '\\')
            # Handle possible double encoding.
            if decoded_item_path.startswith('\\\\'):
                decoded_item_path = decoded_item_path[2:]  # Remove redundant leading backslashes.
        else:
            decoded_item_path = unquote(item_path)
        
        # Resolve relative paths against the base directory.
        if not os.path.isabs(decoded_item_path):
            full_path = os.path.join(base_workshop_folder, decoded_item_path)
        else:
            full_path = decoded_item_path
            full_path = os.path.normpath(full_path)
        
        # Security: verify the path is within the base directory.
        if not full_path.startswith(base_workshop_folder):
            logger.warning(f'路径遍历尝试被拒绝: {item_path}')
            return JSONResponse(content={"success": False, "error": "访问被拒绝: 路径不在允许的范围内"}, status_code=403)
        
        # Verify path existence.
        if not os.path.exists(full_path) or not os.path.isdir(full_path):
            return JSONResponse(content={
                "success": False,
                "error": "无效的物品文件夹路径"
            }, status_code=400)
        
        # Search for txt files starting with steam_workshop_id_.
        import glob
        import re
        
        upload_files = glob.glob(os.path.join(full_path, "steam_workshop_id_*.txt"))
        
        # Extract the first discovered item ID.
        published_file_id = None
        if upload_files:
            # Get the first file.
            first_file = upload_files[0]
            
            # Extract ID from filename.
            match = re.search(r'steam_workshop_id_(\d+)\.txt', os.path.basename(first_file))
            if match:
                published_file_id = match.group(1)
        
        # Return check results.
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


def _is_workshop_publish_native_crash_risk() -> bool:
    """SteamworksPy on macOS arm64 crashes in CreateItem/SubmitItemUpdate callbacks."""
    return sys.platform == 'darwin' and platform.machine().lower() in {'arm64', 'aarch64'}


@router.post('/prepare-upload')
async def prepare_workshop_upload(request: Request):
    """
    Prepare a Workshop upload: create a temp directory and copy the character card and model files into it.
    Returns the temp directory path for the subsequent upload.
    """
    try:
        import shutil
        import uuid
        from utils.frontend_utils import find_model_directory
        
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')
        model_type = data.get('modelType', 'live2d')  # Added: model type live2d/vrm/mmd.
        chara_file_name = data.get('fileName', 'character.chara.json')
        character_card_name = data.get('character_card_name')  # Added: character-card name.
        
        if not chara_data or not model_name:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数"
            }, status_code=400)
        
        # Validate modelType against its whitelist.
        if model_type not in ('live2d', 'vrm', 'mmd'):
            return JSONResponse({
                "success": False,
                "error": f"不支持的模型类型: {model_type}"
            }, status_code=400)
        
        # Prevent path traversal: permit filenames only, with no paths or parent directories.
        safe_chara_name = os.path.basename(chara_file_name)
        if safe_chara_name != chara_file_name or ".." in safe_chara_name or safe_chara_name.startswith(("/", "\\")):
            logger.warning(f"检测到非法文件名尝试: {chara_file_name}")
            return JSONResponse({
                "success": False,
                "error": "非法文件名"
            }, status_code=400)
        
        # If character_card_name is absent, try extracting it from the filename.
        if not character_card_name and safe_chara_name:
            if safe_chara_name.endswith('.chara.json'):
                character_card_name = safe_chara_name[:-11]  # Remove the .chara.json suffix.
        
        # Get the workshop base path.
        base_workshop_path = await get_workshop_path_async()
        workshop_export_dir = os.path.join(base_workshop_path, 'WorkshopExport')
        
        # Ensure WorkshopExport exists.
        os.makedirs(workshop_export_dir, exist_ok=True)
        
        # Create temporary item_xxx directory.
        item_id = str(uuid.uuid4())[:8]  # Identify the item by the UUID's first eight characters.
        temp_item_dir = os.path.join(workshop_export_dir, f'item_{item_id}')
        os.makedirs(temp_item_dir, exist_ok=True)
        
        logger.info(f"创建临时上传目录: {temp_item_dir}")
        
        # 1. Copy character-card JSON into the temporary directory, using the validated safe filename.
        chara_file_path = os.path.join(temp_item_dir, safe_chara_name)
        await atomic_write_json_async(chara_file_path, chara_data, ensure_ascii=False, indent=2)
        logger.info(f"角色卡已复制到临时目录: {chara_file_path}")
        
        # 2. Find and copy model files by model type.
        if model_type in ('vrm', 'mmd'):
            # VRM/MMD: model_name is a file path such as /user_vrm/model.vrm or /user_mmd/folder/model.pmx.
            model_copied = False
            config_mgr = get_config_manager()
            
            # Security: prevent path traversal.
            if '..' in model_name:
                await asyncio.to_thread(shutil.rmtree, temp_item_dir, ignore_errors=True)
                return JSONResponse({
                    "success": False,
                    "error": "非法模型路径"
                }, status_code=400)
            
            if model_type == 'vrm':
                # VRM is a single file; resolve its actual path.
                from pathlib import Path as PathLib
                vrm_filename = os.path.basename(model_name)
                
                if model_name.startswith('/user_vrm/'):
                    vrm_dir = config_mgr.vrm_dir
                    source_file = vrm_dir / vrm_filename
                elif model_name.startswith('/static/vrm/'):
                    source_file = config_mgr.project_root / "static" / "vrm" / vrm_filename
                elif model_name.startswith('/workshop/'):
                    # Workshop VRM: locate the installation directory by item_id.
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
                # MMD can occupy a subdirectory (PMX, textures, etc.); copy the entire model directory.
                from pathlib import Path as PathLib
                
                # Extract the model directory name, e.g. /user_mmd/folder/model.pmx -> folder.
                path_parts = model_name.lstrip('/').split('/')
                
                if model_name.startswith('/user_mmd/') and len(path_parts) >= 3:
                    # Subdirectory present: /user_mmd/subfolder/model.pmx.
                    mmd_dir_name = path_parts[1]  # subfolder
                    mmd_base = getattr(config_mgr, 'mmd_dir', config_mgr.project_root / "user_mmd")
                    source_dir = mmd_base / mmd_dir_name
                    if source_dir.exists() and source_dir.is_dir():
                        model_dest_dir = os.path.join(temp_item_dir, mmd_dir_name)
                        await asyncio.to_thread(shutil.copytree, str(source_dir), model_dest_dir, dirs_exist_ok=True)
                        logger.info(f"MMD模型目录已复制到临时目录: {model_dest_dir}")
                        model_copied = True
                elif model_name.startswith('/user_mmd/') and len(path_parts) == 2:
                    # File directly under user_mmd.
                    mmd_filename = path_parts[1]
                    mmd_base = getattr(config_mgr, 'mmd_dir', config_mgr.project_root / "user_mmd")
                    source_file = mmd_base / mmd_filename
                    if source_file.exists():
                        mmd_dest = os.path.join(temp_item_dir, mmd_filename)
                        await asyncio.to_thread(shutil.copy2, str(source_file), mmd_dest)
                        logger.info(f"MMD模型文件已复制到临时目录: {mmd_dest}")
                        model_copied = True
                elif model_name.startswith('/static/mmd/'):
                    # MMD under static.
                    rel_path = model_name[len('/static/mmd/'):]
                    source_file = config_mgr.project_root / "static" / "mmd" / rel_path
                    if source_file.exists():
                        # Copy the directory containing the file.
                        source_dir = source_file.parent
                        dest_name = source_dir.name
                        model_dest_dir = os.path.join(temp_item_dir, dest_name)
                        await asyncio.to_thread(shutil.copytree, str(source_dir), model_dest_dir, dirs_exist_ok=True)
                        logger.info(f"MMD模型目录已复制到临时目录: {model_dest_dir}")
                        model_copied = True
                elif model_name.startswith('/workshop/'):
                    # Workshop MMD: find the installation by item_id and copy the directory containing the model.
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
                                            # MMD requires its entire model directory, including textures and other resources.
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
            # Live2D: use existing behavior.
            model_dir, _ = find_model_directory(model_name)
            if not model_dir or not os.path.exists(model_dir):
                # Clean up temporary directories.
                await asyncio.to_thread(shutil.rmtree, temp_item_dir, ignore_errors=True)
                return JSONResponse({
                    "success": False,
                    "error": f"模型目录不存在: {model_name}"
                }, status_code=404)
            
            # Copy the entire model directory to the temporary directory.
            model_dest_dir = os.path.join(temp_item_dir, model_name)
            await asyncio.to_thread(shutil.copytree, model_dir, model_dest_dir, dirs_exist_ok=True)
            logger.info(f"模型文件已复制到临时目录: {model_dest_dir}")
        
        # If the character card has cover art, copy it as the workshop preview by default; otherwise retain existing behavior.
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
        
        # Read .workshop_meta.json if present.
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
            "workshop_item_id": workshop_item_id,  # Return an existing item ID if found.
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


def _delete_content_folder(temp_folder: str) -> None:
    """Delete a temp folder only when no publisher or pair writer owns it."""
    import shutil

    with claim_content_folder(temp_folder, purpose=CLEANUP_PURPOSE):
        shutil.rmtree(temp_folder, ignore_errors=True)


@router.post('/cleanup-temp-folder')
async def cleanup_temp_folder(request: Request):
    """
    Clean up the temporary upload directory.
    """
    try:
        data = await request.json()
        temp_folder = data.get('temp_folder')
        
        if not temp_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少临时目录路径"
            }, status_code=400)
        
        # Security: ensure the temporary directory is under WorkshopExport.
        base_workshop_path = await get_workshop_path_async()
        workshop_export_dir = os.path.join(base_workshop_path, 'WorkshopExport')
        
        # Normalize with realpath to resolve symlinks and relative paths.
        temp_folder = os.path.realpath(os.path.normpath(temp_folder))
        workshop_export_dir = os.path.realpath(os.path.normpath(workshop_export_dir))
        
        # Verify containment under WorkshopExport using the more reliable commonpath.
        try:
            common_path = os.path.commonpath([temp_folder, workshop_export_dir])
            if common_path != workshop_export_dir:
                return JSONResponse({
                    "success": False,
                    "error": f"临时目录路径不在允许的范围内。临时目录: {temp_folder}, 允许路径: {workshop_export_dir}"
                }, status_code=403)
        except ValueError:
            # commonpath raises ValueError for paths on different drives.
            return JSONResponse({
                "success": False,
                "error": "临时目录路径不在允许的范围内（路径验证失败）"
            }, status_code=403)
        
        # Delete the temporary directory.
        if os.path.exists(temp_folder):
            await asyncio.to_thread(_delete_content_folder, temp_folder)
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
            
    except ContentFolderBusy as e:
        logger.warning(f"临时目录正被占用，拒绝删除: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=409)
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
    
    # Check successful Steamworks initialization.
    if steamworks is None:
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        data = await request.json()
        
        # Validate required fields.
        required_fields = ['title', 'content_folder', 'visibility']
        for field in required_fields:
            if field not in data:
                return JSONResponse(content={"success": False, "error": f"缺少必要字段: {field}"}, status_code=400)
        
        # Extract data.
        title = data['title']
        content_folder = data['content_folder']
        visibility = int(data['visibility'])
        preview_image = data.get('preview_image', '')
        description = data.get('description', '')
        tags = data.get('tags', [])
        change_note = data.get('change_note', '初始发布')
        character_card_name = data.get('character_card_name')  # Added: character-card name.
        
        # Improved path normalization handles paths correctly in all cases.
        content_folder = unquote(content_folder)
        # Security: verify content_folder is in the allowed area.
        try:
            content_folder = _assert_under_base(
                content_folder, await get_workshop_path_async()
            )
        except PermissionError:
            return JSONResponse(content={
                "success": False,
                "error": "权限错误",
                "message": "指定的内容文件夹不在允许的范围内"
            }, status_code=403)

        # Use correct separators for Windows paths.
        if os.name == 'nt':
            # Normalize all separators to backslashes.
            content_folder = content_folder.replace('/', '\\')
            # Remove potentially invalid prefixes.
            if content_folder.startswith('\\\\'):
                content_folder = content_folder[2:]
        else:
            # Non-Windows systems use forward slashes.
            content_folder = content_folder.replace('\\', '/')
        
        # Verify the content folder exists and is a directory.
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
        
        # Also verify the folder contains at least one file to upload.
        if not any(os.scandir(content_folder)):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹为空",
                "message": f"内容文件夹为空，请确保包含要上传的文件: {content_folder}"
            }, status_code=400)
        
        # Check folder permissions.
        if not os.access(content_folder, os.R_OK):
            return JSONResponse(content={
                "success": False,
                "error": "没有文件夹访问权限",
                "message": f"没有读取内容文件夹的权限: {content_folder}"
            }, status_code=403)

        # Re-uploading an existing card intentionally enters the update path,
        # but do not blindly trust a stale/copied sidecar ID. When the wrapper
        # can query UGC details, verify the target before touching the content
        # folder or starting the native publish operation.
        if character_card_name:
            existing_meta = await asyncio.to_thread(
                read_workshop_meta, character_card_name
            )
            existing_item_id = existing_meta.get('workshop_item_id') if existing_meta else None
            if existing_item_id:
                try:
                    item_id_for_validation = int(existing_item_id)
                except (TypeError, ValueError):
                    item_id_for_validation = 0
                if item_id_for_validation > 0:
                    is_valid, validation_error = await _validate_existing_workshop_item(
                        steamworks, item_id_for_validation
                    )
                    if not is_valid:
                        return JSONResponse(content={
                            "success": False,
                            "error": "Workshop 更新目标校验失败",
                            "message": validation_error,
                            "workshop_item_id": str(existing_item_id),
                        }, status_code=409)
        
        # Process the preview-image path.
        if preview_image:
            preview_image = unquote(preview_image)
            if os.name == 'nt':
                preview_image = preview_image.replace('/', '\\')
                if preview_image.startswith('\\\\'):
                    preview_image = preview_image[2:]
            else:
                preview_image = preview_image.replace('\\', '/')
            
            # Verify preview-image existence.
            if not os.path.exists(preview_image):
                # If the specified preview is missing, search for a default preview in the content folder.
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
            
            # Copy the preview into the content folder with canonical name preview.*.
            if preview_image:
                # Get the original file extension.
                file_extension = os.path.splitext(preview_image)[1].lower()
                # Build the canonical preview path within the content folder.
                new_preview_path = os.path.join(content_folder, f'preview{file_extension}')
                
                # Copy the preview into the content folder.
                try:
                    import shutil
                    await asyncio.to_thread(shutil.copy2, preview_image, new_preview_path)
                    logger.info(f'预览图片已复制到内容文件夹并统一命名: {new_preview_path}')
                    # Use the new canonical preview path.
                    preview_image = new_preview_path
                except Exception as e:
                    logger.error(f'复制预览图片到内容文件夹失败: {e}')
                    # If copying fails, continue with the original path.
                    logger.warning(f'继续使用原始预览图片路径: {preview_image}')
        else:
            # If no preview is specified, try automatic discovery.
            auto_preview = find_preview_image_in_folder(content_folder)
            if auto_preview:
                logger.info(f'自动找到预览图片: {auto_preview}')
                preview_image = auto_preview
                
                # Ensure automatically discovered previews also use preview.*.
                if preview_image:
                    # Get the original file extension.
                    file_extension = os.path.splitext(preview_image)[1].lower()
                    # Rename if not already canonical.
                    if not os.path.basename(preview_image).startswith('preview.'):
                        new_preview_path = os.path.join(content_folder, f'preview{file_extension}')
                        try:
                            import shutil
                            await asyncio.to_thread(shutil.copy2, preview_image, new_preview_path)
                            logger.info(f'自动找到的预览图片已统一命名: {new_preview_path}')
                            preview_image = new_preview_path
                        except Exception as e:
                            logger.error(f'重命名自动预览图片失败: {e}')
                            # If renaming fails, continue with the original path.
                            logger.warning(f'继续使用原始预览图片路径: {preview_image}')

        # Record information about the upload content.
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
        
        # Run blocking Steamworks API calls in the thread pool.
        loop = asyncio.get_event_loop()
        try:
            published_file_id = await loop.run_in_executor(
                None,
                lambda: _preflight_and_publish(
                    steamworks, title, description, content_folder,
                    preview_image, visibility, tags, change_note, character_card_name
                )
            )
        except _VoicePreflightError as e:
            return JSONResponse(content={
                "success": False,
                "error": "参考语音清单无效",
                "message": str(e)
            }, status_code=400)
        except ContentFolderBusy as e:
            return JSONResponse(content={
                "success": False,
                "error": "内容目录正被占用",
                "message": str(e)
            }, status_code=409)
        
        logger.info(f"成功发布创意工坊物品，ID: {published_file_id}")
        
        # After successful upload, update .workshop_meta.json and save a snapshot.
        if character_card_name and published_file_id:
            try:
                # Calculate the content hash.
                content_hash = calculate_content_hash(content_folder)
                
                # Build the upload snapshot.
                uploaded_snapshot = {
                    'description': description,
                    'tags': tags,
                    'title': title,
                    'visibility': visibility
                }
                
                # Try reading character-card data from the temporary folder.
                try:
                    import glob
                    chara_files = glob.glob(os.path.join(content_folder, "*.chara.json"))
                    if chara_files:
                        chara_data = await read_json_async(chara_files[0])
                        uploaded_snapshot['character_data'] = chara_data
                        logger.info(f"已从临时文件夹读取角色卡数据")
                    
                    # Get the model name by finding the model directory.
                    for item in os.listdir(content_folder):
                        item_path = os.path.join(content_folder, item)
                        if os.path.isdir(item_path) and not item.startswith('.'):
                            # Detect Live2D directories containing .model3.json or model.json.
                            model_files = glob.glob(os.path.join(item_path, "*.model3.json")) + \
                                         glob.glob(os.path.join(item_path, "*.model.json")) + \
                                         glob.glob(os.path.join(item_path, "model.json"))
                            if model_files:
                                uploaded_snapshot['model_name'] = item
                                logger.info(f"检测到模型目录: {item}")
                                break
                except Exception as read_error:
                    logger.warning(f"读取角色卡数据时出错: {read_error}")
                
                # Write metadata including the snapshot.
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
                # Log errors without blocking the successful response.
        
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


class _VoicePreflightError(Exception):
    """The reference pair is invalid, so publishing must not start."""


def _preflight_and_publish(
    steamworks,
    title,
    description,
    content_folder,
    preview_image,
    visibility,
    tags,
    change_note,
    character_card_name=None,
):
    """Validate and publish one immutable view of the content directory."""
    with claim_content_folder(content_folder, purpose=PUBLISH_PURPOSE):
        try:
            voice_ref = resolve_voice_reference_serialized(content_folder)
        except (ValueError, FileNotFoundError) as e:
            raise _VoicePreflightError(str(e)) from e
        if voice_ref:
            logger.info(
                f"检测到参考语音清单: {voice_ref['manifest']['reference_audio']}"
            )
        return _publish_workshop_item(
            steamworks,
            title,
            description,
            content_folder,
            preview_image,
            visibility,
            tags,
            change_note,
            character_card_name,
        )


def _publish_workshop_item(steamworks, title, description, content_folder, preview_image, visibility, tags, change_note, character_card_name=None):
    """
    Run the Steam Workshop publish operation in a separate thread.
    """
    with publish_lock:
        try:
            # Import inside the function so enums are available in its scope.
            from steamworks.enums import EWorkshopFileType, ERemoteStoragePublishedFileVisibility, EItemUpdateStatus
    
            # Prefer the item ID from .workshop_meta.json.
            item_id = None
            if character_card_name:
                try:
                    # _publish_workshop_item is synchronous and runs in a worker thread; await is unavailable here.
                    # Other async call sites all use asyncio.to_thread, enforced by lint.
                    meta_data = read_workshop_meta(character_card_name)
                    if meta_data and meta_data.get('workshop_item_id'):
                        item_id = int(meta_data.get('workshop_item_id'))
                        logger.info(f"从 .workshop_meta.json 读取到物品ID: {item_id}")
                except Exception as e:
                    logger.warning(f"从 .workshop_meta.json 读取物品ID失败: {e}")
            
            # If metadata lacks the ID, try legacy marker files for backward compatibility.
            if item_id is None:
                try:
                    if os.path.exists(content_folder) and os.path.isdir(content_folder):
                        # Find txt files starting with steam_workshop_id_.
                        import glob
                        marker_files = glob.glob(os.path.join(content_folder, "steam_workshop_id_*.txt"))
                        
                        if marker_files:
                            # Use the first discovered marker file.
                            marker_file = marker_files[0]
                            
                            # Extract the item ID from the filename.
                            import re
                            match = re.search(r'steam_workshop_id_([0-9]+)\.txt', marker_file)
                            if match:
                                item_id = int(match.group(1))
                                logger.info(f"检测到物品已上传，找到标记文件: {marker_file}，物品ID: {item_id}")
                except Exception as e:
                    logger.error(f"检查上传标记文件时出错: {e}")
            # Continue attempting upload even if checking fails.
        
            try:
                # Revalidate the content folder to ensure validity under multithreading.
                if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
                    raise Exception(f"内容文件夹不存在或无效: {content_folder}")
            
                # Count folder contents to ensure files are available for upload.
                file_count = 0
                for root, dirs, files in os.walk(content_folder):
                    file_count += len(files)
            
                if file_count == 0:
                    raise Exception(f"内容文件夹中没有找到可上传的文件: {content_folder}")
            
                logger.info(f"内容文件夹验证通过，包含 {file_count} 个文件")
            
                # Get the current application ID.
                app_id = steamworks.app_id
                logger.info(f"使用应用ID: {app_id} 进行创意工坊上传")
            
                # Enhanced Steam connection-state validation.
                # Basic connection-state check.
                is_steam_running = steamworks.IsSteamRunning()
                try:
                    is_overlay_enabled = steamworks.IsOverlayEnabled()
                except Exception as overlay_error:
                    is_overlay_enabled = None
                    logger.warning(f"Steam覆盖层启用状态检查不可用: {overlay_error}")
                is_logged_on = steamworks.Users.LoggedOn()
                steam_id = steamworks.Users.GetSteamID()
            
                # Application-related permission check.
                app_owned = steamworks.Apps.IsAppInstalled(app_id)
                app_owned_license = steamworks.Apps.IsSubscribedApp(app_id)
                app_subscribed = steamworks.Apps.IsSubscribed()
            
                # Record detailed connection state.
                logger.info(f"Steam客户端运行状态: {is_steam_running}")
                logger.info(
                    "Steam覆盖层启用状态: "
                    + ("不可用" if is_overlay_enabled is None else str(is_overlay_enabled))
                )
                logger.info(f"用户登录状态: {is_logged_on}")
                logger.info(f"用户SteamID: {steam_id}")
                logger.info(f"应用ID {app_id} 安装状态: {app_owned}")
                logger.info(f"应用ID {app_id} 订阅许可状态: {app_owned_license}")
                logger.info(f"当前应用订阅状态: {app_subscribed}")
            
                # Preflight connection checks report problems early.
                if not is_steam_running:
                    raise Exception("Steam客户端未运行，请先启动Steam客户端")
                if not is_logged_on:
                    raise Exception("用户未登录Steam，请确保已登录Steam客户端")
        
            except Exception as e:
                logger.error(f"Steam连接状态验证失败: {e}")
                # Continue despite failed validation, but warn.
                logger.warning("继续尝试创意工坊上传，但可能会因为Steam连接问题而失败")
        
            # Error-code mapping supplies more specific messages.
            error_codes = {
                1: "成功",
                10: "权限不足 - 可能需要登录Steam客户端或缺少创意工坊上传权限",
                111: "网络连接错误 - 无法连接到Steam网络",
                100: "服务不可用 - Steam创意工坊服务暂时不可用",
                8: "文件已存在 - 相同内容的物品已存在",
                34: "服务器忙 - Steam服务器暂时无法处理请求",
                116: "请求超时 - 与Steam服务器通信超时"
            }
        
            # Create a new item if no existing ID is found.
            if item_id is None:
                # For new items, create an empty item first.
                # Handle creation results with callbacks.
                created_item_id = [None]
                created_event = threading.Event()
                create_result = [None]  # Store creation results.
            
                def onCreateItem(result):
                    nonlocal created_item_id, create_result
                    create_result[0] = result.result
                    # Read struct fields directly, not as a dictionary.
                    if result.result == 1:  # k_EResultOK
                        created_item_id[0] = result.publishedFileId
                        logger.info(f"成功创建创意工坊物品，ID: {created_item_id[0]}")
                        created_event.set()
                    else:
                        error_msg = error_codes.get(result.result, f"未知错误码: {result.result}")
                        logger.error(f"创建创意工坊物品失败，错误码: {result.result} ({error_msg})")
                        created_event.set()
            
                # Set the item-creation callback.
                steamworks.Workshop.SetItemCreatedCallback(onCreateItem)
            
                # Create a workshop item using the UGC file-type enum.
                logger.info(f"开始创建创意工坊物品: {title}")
                logger.info(f"调用SteamWorkshop.CreateItem({app_id}, {EWorkshopFileType.COMMUNITY})")
                steamworks.Workshop.CreateItem(app_id, EWorkshopFileType.COMMUNITY)
            
                # Wait for creation or timeout, with increased timeout and diagnostics.
                logger.info("等待创意工坊物品创建完成...")
                # Wait in a loop, periodically processing callbacks via run_callbacks.
                start_time = time.time()
                timeout = 60  # 60-second timeout.
                while time.time() - start_time < timeout:
                    if created_event.is_set():
                        break
                    # Periodically process Steam API callbacks via run_callbacks.
                    try:
                        steamworks.run_callbacks()
                    except Exception as e:
                        logger.error(f"执行Steam回调时出错: {str(e)}")
                    # noqa: BLOCKING-OK - _publish_workshop_item is synchronous; its caller schedules
                    # loop.run_in_executor(None, lambda: _publish_workshop_item(...)) in the thread pool,
                    # so time.sleep blocks only the executor worker, never the main event loop.
                    time.sleep(0.1)  # Check every 100 milliseconds.
            
                if not created_event.is_set():
                    logger.error("创建创意工坊物品超时，可能是网络问题或Steam服务暂时不可用")
                    raise TimeoutError("创建创意工坊物品超时")
            
                if created_item_id[0] is None:
                    # Supply more specific error messages.
                    error_msg = error_codes.get(create_result[0], f"未知错误码: {create_result[0]}")
                    logger.error(f"创建创意工坊物品失败: {error_msg}")
                
                    # Provide detailed explanations and remedies for error 10 (insufficient permissions).
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
                # Assign the new item ID to item_id.
                item_id = created_item_id[0]
            else:
                logger.info(f"使用现有物品ID进行更新: {item_id}")       
        
            # Begin updating the item.
            logger.info(f"开始更新物品内容: {title}")
            update_handle = steamworks.Workshop.StartItemUpdate(app_id, item_id)
        
            # Set item properties.
            logger.info("设置物品基本属性...")
            steamworks.Workshop.SetItemTitle(update_handle, title)
            if description:
                steamworks.Workshop.SetItemDescription(update_handle, description)
        
            # Set item content, the central file-upload step.
            logger.info(f"设置物品内容文件夹: {content_folder}")
            content_set_result = steamworks.Workshop.SetItemContent(update_handle, content_folder)
            logger.info(f"内容设置结果: {content_set_result}")
            
            # Set the preview image if supplied.
            if preview_image:
                logger.info(f"设置预览图片: {preview_image}")
                preview_set_result = steamworks.Workshop.SetItemPreview(update_handle, preview_image)
                logger.info(f"预览图片设置结果: {preview_set_result}")
        
            # Import enums and convert integer values to enum objects.
            if visibility == 0:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
            elif visibility == 1:
                visibility_enum = ERemoteStoragePublishedFileVisibility.FRIENDS_ONLY
            elif visibility == 2:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PRIVATE
            else:
                # Default to public.
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
                
            # Set item visibility.
            logger.info(f"设置物品可见性: {visibility_enum}")
            steamworks.Workshop.SetItemVisibility(update_handle, visibility_enum)
            
            # Set tags if any.
            if tags:
                logger.info(f"设置物品标签: {tags}")
                steamworks.Workshop.SetItemTags(update_handle, tags)
            
            # Submit updates and handle results via callback.
            updated = [False]
            error_code = [0]
            update_event = threading.Event()
            
            def onSubmitItemUpdate(result):
                nonlocal updated, error_code
                # Read struct fields directly, not as a dictionary.
                error_code[0] = result.result
                if result.result == 1:  # k_EResultOK
                    updated[0] = True
                    logger.info(f"物品更新提交成功，结果代码: {result.result}")
                else:
                    logger.error(f"提交创意工坊物品更新失败，错误码: {result.result}")
                update_event.set()
            
            # Set the item-update callback.
            steamworks.Workshop.SetItemUpdatedCallback(onSubmitItemUpdate)
            
            # Submit the update.
            logger.info(f"开始提交物品更新，更新说明: {change_note}")
            steamworks.Workshop.SubmitItemUpdate(update_handle, change_note)
            
            # Wait for update completion or timeout, with increased timeout and diagnostics.
            logger.info("等待创意工坊物品更新完成...")
            # Wait in a loop, periodically processing callbacks via run_callbacks.
            start_time = time.time()
            timeout = 180  # 180-second timeout.
            last_progress = -1
            
            while time.time() - start_time < timeout:
                if update_event.is_set():
                    break
                # Periodically process Steam API callbacks via run_callbacks.
                try:
                    steamworks.run_callbacks()
                    # Record upload progress with more detailed reporting.
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
                                # Log only substantial progress changes.
                                if current_progress != last_progress:
                                    logger.info(f"上传状态: {status_text}, 进度: {current_progress}%")
                                    last_progress = current_progress
                except Exception as e:
                    logger.error(f"执行Steam回调时出错: {str(e)}")
                # noqa: BLOCKING-OK - As at Site 2, _publish_workshop_item runs in the run_in_executor
                # thread pool, so this sleep blocks only its worker, not the main event loop.
                time.sleep(0.5)  # Check every 500 milliseconds to reduce logs.
            
            if not update_event.is_set():
                logger.error("提交创意工坊物品更新超时，可能是网络问题或Steam服务暂时不可用")
                raise TimeoutError("提交创意工坊物品更新超时")
            
            if not updated[0]:
                # Supply more detailed messages by error code.
                if error_code[0] == 25:  # LIMIT_EXCEEDED
                    error_msg = "提交创意工坊物品更新失败：内容超过Steam限制（错误码25）。请检查内容大小、文件数量或其他限制。"
                else:
                    error_msg = f"提交创意工坊物品更新失败，错误码: {error_code[0]}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            logger.info(f"创意工坊物品上传成功完成！物品ID: {item_id}")
            
            # Create an item-ID txt marker in the original folder to mark successful upload.
            try:
                marker_file_path = os.path.join(content_folder, f"steam_workshop_id_{item_id}.txt")
                with open(marker_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Steam创意工坊物品ID: {item_id}\n")
                    f.write(f"上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
                    f.write(f"物品标题: {title}\n")
                logger.info(f"已在原文件夹创建上传标记文件: {marker_file_path}")
            except Exception as e:
                logger.error(f"创建上传标记文件失败: {e}")
                # Failure to create the marker must not change the successful upload result.

            return item_id
        except Exception as e:
            logger.error(f"发布创意工坊物品时出错: {e}")
            raise

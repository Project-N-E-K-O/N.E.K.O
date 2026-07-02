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
from .meta import calculate_content_hash, read_workshop_meta, write_workshop_meta
from .preview_cards import find_preview_image_in_folder
from .ugc import get_subscribed_workshop_items
from .voice_manifest import _resolve_workshop_voice_reference

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
    get_workshop_path,
)


# 全局互斥锁，用于序列化创意工坊发布操作，防止并发回调混乱
publish_lock = threading.Lock()


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
    Clean up the temporary upload directory.
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
    Run the Steam Workshop publish operation in a separate thread.
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
                try:
                    is_overlay_enabled = steamworks.IsOverlayEnabled()
                except Exception as overlay_error:
                    is_overlay_enabled = None
                    logger.warning(f"Steam覆盖层启用状态检查不可用: {overlay_error}")
                is_logged_on = steamworks.Users.LoggedOn()
                steam_id = steamworks.Users.GetSteamID()
            
                # 应用相关权限检查
                app_owned = steamworks.Apps.IsAppInstalled(app_id)
                app_owned_license = steamworks.Apps.IsSubscribedApp(app_id)
                app_subscribed = steamworks.Apps.IsSubscribed()
            
                # 记录详细的连接状态
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

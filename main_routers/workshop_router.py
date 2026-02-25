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
import json
import time
import asyncio
import threading
from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .shared_state import get_steamworks, get_config_manager, get_initialize_character_data
from utils.workshop_utils import (
    ensure_workshop_folder_exists,
    get_workshop_path,
)
from utils.logger_config import get_module_logger
import hashlib

router = APIRouter(prefix="/api/steam/workshop", tags=["workshop"])
# 全局互斥锁，用于序列化创意工坊发布操作，防止并发回调混乱
publish_lock = threading.Lock()
logger = get_module_logger(__name__, "Main")


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
        with open(meta_file_path, 'w', encoding='utf-8') as f:
            json.dump(existing_meta, f, ensure_ascii=False, indent=2)
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
        logger.info(f"获取到 {num_subscribed_items} 个订阅的创意工坊物品")
        
        # 如果没有订阅物品，返回空列表
        if num_subscribed_items == 0:
            return {
                "success": True,
                "items": [],
                "total": 0
            }
        
        # 获取订阅物品ID列表
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        logger.info(f'获取到 {len(subscribed_items)} 个订阅的创意工坊物品')
        
        # 存储处理后的物品信息
        items_info = []
        
        # 批量查询所有物品的详情（一次查询，避免N次等待）
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
                logger.info(f'批量查询 {len(all_item_ids)} 个物品的详细信息')
                query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest(all_item_ids)
                
                if query_handle:
                    steamworks.Workshop.SendQueryUGCRequest(query_handle)
                    await asyncio.sleep(0.5)  # 只等待一次
                    
                    for i in range(len(all_item_ids)):
                        res = steamworks.Workshop.GetQueryUGCResult(query_handle, i)
                        if res:
                            ugc_results[all_item_ids[i]] = res
                    
                    logger.info(f"批量查询成功获取 {len(ugc_results)} 个物品的详情")
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
                
                logger.info(f'正在处理物品ID: {item_id}')
                
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
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 安装信息字典: {result}')
                        
                        # 从字典中提取信息
                        item_info["state"]["installed"] = True  # 如果返回字典，假设已安装
                        # 获取安装路径 - workshop.py中已经将folder解码为字符串
                        folder_path = result.get('folder', '')
                        item_info["installedFolder"] = str(folder_path) if folder_path else None
                        logger.debug(f'物品 {item_id} 的安装路径: {item_info["installedFolder"]}')
                        
                        # 处理磁盘大小 - GetItemInstallInfo返回的disk_size是普通整数
                        disk_size = result.get('disk_size', 0)
                        item_info["fileSizeOnDisk"] = int(disk_size) if isinstance(disk_size, (int, float)) else 0
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        installed, folder, size = result
                        logger.debug(f'物品 {item_id} 安装状态: 已安装={installed}, 路径={folder}, 大小={size}')
                        
                        # 安全的类型转换
                        item_info["state"]["installed"] = bool(installed)
                        item_info["installedFolder"] = str(folder) if folder and isinstance(folder, (str, bytes)) else None
                        
                        # 处理大小值
                        if isinstance(size, (int, float)):
                            item_info["fileSizeOnDisk"] = int(size)
                        else:
                            item_info["fileSizeOnDisk"] = 0
                    else:
                        logger.warning(f'物品 {item_id} 的安装信息返回格式未知: {type(result)} - {result}')
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
                
                # 从批量查询结果中提取物品详情
                if item_id in ugc_results:
                    result = ugc_results[item_id]
                    try:
                        if hasattr(result, 'title') and result.title:
                            item_info['title'] = result.title.decode('utf-8', errors='replace')
                        if hasattr(result, 'description') and result.description:
                            item_info['description'] = result.description.decode('utf-8', errors='replace')
                        if hasattr(result, 'timeCreated'):
                            item_info['timeAdded'] = int(result.timeCreated)
                        if hasattr(result, 'timeUpdated'):
                            item_info['timeUpdated'] = int(result.timeUpdated)
                        if hasattr(result, 'steamIDOwner'):
                            item_info['steamIDOwner'] = str(result.steamIDOwner)
                        if hasattr(result, 'fileSize'):
                            item_info['fileSizeOnDisk'] = int(result.fileSize)
                        logger.debug(f"从批量查询结果填充物品 {item_id} 详情")
                    except Exception as detail_error:
                        logger.warning(f"提取物品 {item_id} 详情时出错: {detail_error}")
                
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
                                    with open(config_path, 'r', encoding='utf-8') as f:
                                        if config_path.endswith('.json'):
                                            config_data = json.load(f)
                                            # 尝试从配置文件中提取标题和描述
                                            if "title" in config_data and config_data["title"]:
                                                item_info["title"] = config_data["title"]
                                            elif "name" in config_data and config_data["name"]:
                                                item_info["title"] = config_data["name"]
                                            
                                            if "description" in config_data and config_data["description"]:
                                                item_info["description"] = config_data["description"]
                                        else:
                                            # 对于文本文件，将第一行作为标题
                                            first_line = f.readline().strip()
                                            if first_line and item_info['title'].startswith('未知物品_'):
                                                item_info['title'] = first_line[:100]  # 限制长度
                                    logger.info(f"从本地文件 {os.path.basename(config_path)} 成功获取物品 {item_id} 的信息")
                                    break
                                except Exception as file_error:
                                    logger.warning(f"读取配置文件 {config_path} 时出错: {file_error}")
                # 移除了没有对应try块的except语句
                
                # 确保publishedFileId是字符串类型
                item_info['publishedFileId'] = str(item_info['publishedFileId'])
                
                # 尝试获取预览图信息 - 优先从本地文件夹查找
                preview_url = None
                install_folder = item_info.get('installedFolder')
                if install_folder and os.path.exists(install_folder):
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
                    except Exception as preview_error:
                        logger.warning(f'查找物品 {item_id} 预览图时出错: {preview_error}')
                
                # 添加预览图URL到物品信息
                if preview_url:
                    item_info['previewUrl'] = preview_url
                
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
                    logger.info(f'已添加物品 {item_id} 的基本信息到结果列表')
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
        
        # 创建查询请求，传入必要的published_file_ids参数
        query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest([item_id_int])
        
        # 发送查询请求
        # 注意：SendQueryUGCRequest返回None而不是布尔值
        steamworks.Workshop.SendQueryUGCRequest(query_handle)
        await asyncio.sleep(0.5)
        
        # 直接获取查询结果，不检查handle
        result = steamworks.Workshop.GetQueryUGCResult(query_handle, 0)
            
        if result:
            # 获取物品安装信息 - 支持字典格式（根据workshop.py的实现）
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
            installed = bool(install_info)
            folder = install_info.get('folder', '') if installed else ''
            size = 0
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                size = int(disk_size)
            
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
            
            # 解码bytes类型的字段为字符串，避免JSON序列化错误
            title = result.title.decode('utf-8', errors='replace') if hasattr(result, 'title') and isinstance(result.title, bytes) else getattr(result, 'title', '')
            description = result.description.decode('utf-8', errors='replace') if hasattr(result, 'description') and isinstance(result.description, bytes) else getattr(result, 'description', '')
            
            # 构建详细的物品信息
            item_info = {
                "publishedFileId": item_id_int,
                "title": title,
                "description": description,
                "steamIDOwner": result.steamIDOwner,
                "timeCreated": result.timeCreated,
                "timeUpdated": result.timeUpdated,
                "previewImageUrl": result.URL,  # 使用result.URL代替不存在的previewImageUrl
                "fileUrl": result.URL,  # 使用result.URL代替不存在的fileUrl
                "fileSize": result.fileSize,
                "fileId": result.file,  # 使用result.file代替不存在的fileId
                "previewFileId": result.previewFile,  # 使用result.previewFile代替不存在的previewFileId
                # 移除不存在的appID属性
                "tags": [],
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
        
        # 定义一个内部删除函数，可以在回调或备用方案中使用
        def perform_cleanup(item_id: int):
            """执行清理操作：删除文件夹和角色卡"""
            try:
                import shutil
                
                # 获取steamworks实例（在函数内部获取，确保可用）
                current_steamworks = get_steamworks()
                
                # 首先尝试使用Steamworks API获取实际安装路径
                item_path = None
                try:
                    if current_steamworks:
                        install_info = current_steamworks.Workshop.GetItemInstallInfo(item_id)
                        logger.debug(f"GetItemInstallInfo返回: {install_info}, 类型: {type(install_info)}")
                        
                        if isinstance(install_info, dict):
                            folder_path = install_info.get('folder', '')
                            if folder_path:
                                item_path = str(folder_path)
                                logger.info(f"从GetItemInstallInfo获取到安装路径: {item_path}")
                        elif isinstance(install_info, tuple) and len(install_info) >= 2:
                            folder = install_info[1]
                            if folder:
                                item_path = str(folder)
                                logger.info(f"从GetItemInstallInfo(元组)获取到安装路径: {item_path}")
                except Exception as e:
                    logger.warning(f"通过GetItemInstallInfo获取路径失败: {e}，尝试使用find_workshop_item_by_id")
                
                # 如果GetItemInstallInfo失败，回退到使用find_workshop_item_by_id
                if not item_path:
                    from utils.frontend_utils import find_workshop_item_by_id
                    item_path, _ = find_workshop_item_by_id(str(item_id))
                    logger.info(f"通过find_workshop_item_by_id找到路径: {item_path}")
                
                # 检查路径是否存在
                if not item_path:
                    logger.error(f"无法获取物品 {item_id} 的安装路径")
                    return False
                
                # 规范化路径
                item_path = os.path.abspath(os.path.normpath(item_path))
                logger.info(f"规范化后的物品路径: {item_path}")
                
                # 检查路径是否存在
                if not os.path.exists(item_path):
                    logger.warning(f"创意工坊物品路径不存在: {item_path}")
                    return False
                
                if not os.path.isdir(item_path):
                    logger.warning(f"物品路径不是目录: {item_path}")
                    return False
                
                # 扫描文件夹及其子文件夹，查找所有.chara.json文件
                chara_files = []
                chara_names = []  # 存储找到的角色卡名称
                logger.info(f"开始扫描文件夹: {item_path}")
                
                try:
                    for root, dirs, files in os.walk(item_path):
                        logger.debug(f"扫描目录: {root}, 文件数: {len(files)}")
                        for file in files:
                            if file.endswith('.chara.json'):
                                chara_file_path = os.path.join(root, file)
                                chara_files.append(chara_file_path)
                                logger.info(f"找到角色卡文件: {chara_file_path}")
                except Exception as e:
                    logger.error(f"扫描文件夹时出错: {e}")
                
                logger.info(f"共找到 {len(chara_files)} 个角色卡文件")
                
                # 解析.chara.json文件，获取角色卡名称
                for chara_file_path in chara_files:
                    try:
                        with open(chara_file_path, 'r', encoding='utf-8') as f:
                            chara_data = json.load(f)
                        
                        # 获取角色卡名称，兼容中英文字段名
                        chara_name = chara_data.get('档案名') or chara_data.get('name')
                        if chara_name:
                            chara_names.append(chara_name)
                            logger.info(f"解析角色卡文件成功: {chara_file_path} -> {chara_name}")
                        else:
                            logger.warning(f"角色卡文件 {chara_file_path} 缺少名称字段，数据: {chara_data}")
                    except Exception as e:
                        logger.error(f"处理角色卡文件 {chara_file_path} 时出错: {e}", exc_info=True)
                
                logger.info(f"共解析出 {len(chara_names)} 个角色卡名称: {chara_names}")
                
                # 从characters.json中删除角色卡
                if chara_names:
                    config_mgr = get_config_manager()
                    characters = config_mgr.load_characters()
                    
                    # 确保'猫娘'键存在
                    if '猫娘' not in characters:
                        characters['猫娘'] = {}
                    
                    # 删除每个找到的角色卡
                    deleted_count = 0
                    for chara_name in chara_names:
                        if chara_name in characters.get('猫娘', {}):
                            # 检查是否是当前正在使用的猫娘
                            current_catgirl = characters.get('当前猫娘', '')
                            if chara_name == current_catgirl:
                                logger.warning(f"不能删除当前正在使用的猫娘: {chara_name}，跳过删除")
                                continue
                            
                            del characters['猫娘'][chara_name]
                            deleted_count += 1
                            logger.info(f"已从characters.json中删除角色卡: {chara_name}")
                    
                    if deleted_count > 0:
                        # 保存更新后的characters.json
                        config_mgr.save_characters(characters)
                        logger.info(f"已保存更新后的characters.json，删除了 {deleted_count} 个角色卡")
                        
                        # 重新加载配置
                        try:
                            initialize_character_data = get_initialize_character_data()
                            if initialize_character_data:
                                # 尝试获取事件循环并安全地调用异步函数
                                try:
                                    loop = asyncio.get_event_loop()
                                    if loop.is_running():
                                        # 如果事件循环正在运行，使用create_task
                                        # 保存任务引用以防止被垃圾回收器提前回收
                                        task = loop.create_task(initialize_character_data())
                                        # 可选：添加错误处理回调
                                        def task_done_callback(t):
                                            try:
                                                t.result()  # 获取任务结果，如果有异常会抛出
                                            except Exception as e:
                                                logger.error(f"重新加载角色配置时出错: {e}")
                                        task.add_done_callback(task_done_callback)
                                    else:
                                        # 如果事件循环未运行，使用run_until_complete
                                        loop.run_until_complete(initialize_character_data())
                                    logger.info("已重新加载角色配置")
                                except RuntimeError:
                                    # 如果没有事件循环，尝试创建新的
                                    try:
                                        asyncio.run(initialize_character_data())
                                        logger.info("已重新加载角色配置")
                                    except Exception as e:
                                        logger.warning(f"无法重新加载角色配置（可能不在事件循环中）: {e}")
                        except Exception as e:
                            logger.error(f"重新加载角色配置时出错: {e}")
                
                # 删除订阅文件夹
                try:
                    logger.info(f"准备删除订阅文件夹: {item_path}")
                    if os.path.exists(item_path) and os.path.isdir(item_path):
                        # 再次确认路径存在
                        logger.info(f"确认文件夹存在，开始删除: {item_path}")
                        shutil.rmtree(item_path, ignore_errors=True)
                        
                        # 验证删除是否成功
                        if os.path.exists(item_path):
                            logger.warning(f"删除后文件夹仍存在: {item_path}，可能被占用或权限不足")
                        else:
                            logger.info(f"✅ 成功删除订阅文件夹: {item_path}")
                    else:
                        logger.warning(f"订阅文件夹不存在或不是目录: {item_path} (存在: {os.path.exists(item_path)}, 是目录: {os.path.isdir(item_path) if os.path.exists(item_path) else False})")
                except Exception as e:
                    logger.error(f"删除订阅文件夹时出错: {e}", exc_info=True)
                    
            except Exception as e:
                logger.error(f"执行清理操作时出错: {e}", exc_info=True)
                return False
            return True
        
        # 定义一个简单的回调函数来处理取消订阅的结果
        def unsubscribe_callback(result):
            # 记录取消订阅的结果（添加详细日志）
            callback_item_id = getattr(result, 'publishedFileId', getattr(result, 'published_file_id', None))
            logger.info(f"取消订阅回调被触发: 期望item_id={item_id_int}, 回调item_id={callback_item_id}, result.result={result.result}")
            
            # 检查result对象的结构（用于调试）
            logger.debug(f"回调result对象类型: {type(result)}, 属性: {dir(result)}")
            
            # 验证item_id是否匹配（防止其他取消订阅操作触发此回调）
            if callback_item_id and int(callback_item_id) != item_id_int:
                logger.warning(f"回调item_id不匹配: 期望{item_id_int}, 实际{callback_item_id}，跳过处理")
                return
            
            # 记录取消订阅的结果
            if result.result == 1:  # k_EResultOK
                logger.info(f"取消订阅成功回调: {item_id_int}，开始执行删除操作")
                # 调用统一的清理函数
                perform_cleanup(item_id_int)
            else:
                logger.warning(f"取消订阅失败回调: {item_id_int}, 错误代码: {result.result}")
        
        # 调用Steamworks的UnsubscribeItem方法，并提供回调函数
        # 使用override_callback=True确保回调被正确设置
        try:
            steamworks.Workshop.UnsubscribeItem(item_id_int, callback=unsubscribe_callback, override_callback=True)
            logger.info(f"取消订阅请求已发送: {item_id_int}，等待回调...")
            
            # 设置一个延迟的后备清理机制（如果回调在5秒内没有触发）
            def delayed_cleanup():
                import time
                time.sleep(5)  # 等待5秒
                logger.info(f"延迟清理检查: 如果回调未触发，执行备用清理...")
                # 注意：这里不能直接调用，因为无法知道回调是否已执行
                # 更好的方法是检查文件夹是否还存在
                try:
                    install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
                    if install_info:  # 如果还能获取到安装信息，说明可能还没删除
                        logger.warning(f"5秒后仍能获取安装信息，可能回调未触发，执行备用清理")
                        perform_cleanup(item_id_int)
                except Exception as e:
                    # 如果获取失败，可能已经删除了，这是正常情况，记录调试信息即可
                    logger.debug(f"延迟清理检查时获取安装信息失败（可能已删除）: {e}")
            
            # 在后台线程中启动延迟清理（可选）
            import threading
            cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
            cleanup_thread.start()
            
        except Exception as e:
            logger.error(f"调用UnsubscribeItem失败: {e}")
            # 如果设置回调失败，直接执行删除操作
            logger.warning(f"回调设置失败，立即执行删除操作...")
            perform_cleanup(item_id_int)
            raise
        
        # 由于回调是异步的，我们返回请求已被接受处理的状态
        logger.info(f"取消订阅请求已被接受，正在处理: {item_id_int}")
        return {
            "success": True,
            "status": "accepted",
            "message": "取消订阅请求已被接受，正在处理中。实际结果将在后台异步完成。"
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
        meta_data = read_workshop_meta(decoded_name)
        
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
        workshop_config_data = load_workshop_config()
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
        workshop_config_data = load_workshop_config() or {}
        
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


@router.post('/local-items/scan')
async def scan_local_workshop_items(request: Request):
    try:
        logger.info('接收到扫描本地创意工坊物品的API请求')
        
        # 确保配置已加载
        from utils.workshop_utils import load_workshop_config
        workshop_config_data = load_workshop_config()
        logger.info(f'创意工坊配置已加载: {workshop_config_data}')
        
        data = await request.json()
        logger.info(f'请求数据: {data}')
        folder_path = data.get('folder_path')
        
        # 安全检查：始终使用get_workshop_path()作为基础目录
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        # 如果没有提供路径，使用默认路径
        default_path_used = False
        if not folder_path:
            # 优先使用get_workshop_path()函数获取路径
            folder_path = base_workshop_folder
            default_path_used = True
            logger.info(f'未提供文件夹路径，使用默认路径: {folder_path}')
            # 确保默认文件夹存在
            ensure_workshop_folder_exists(folder_path)
        else:
            # 用户提供了路径，标准化处理
            folder_path = os.path.normpath(folder_path)
            
            # 如果是相对路径，基于默认路径解析
            if not os.path.isabs(folder_path):
                folder_path = os.path.normpath(folder_path)
            
            logger.info(f'用户指定路径: {folder_path}')

        try:
            folder_path = _assert_under_base(folder_path, base_workshop_folder)
        except PermissionError:
            logger.warning(f'路径遍历尝试被拒绝: {folder_path}')
            return JSONResponse(content={"success": False, "error": "权限错误：指定的路径不在基础目录下"}, status_code=403)
        
        logger.info(f'最终使用的文件夹路径: {folder_path}, 默认路径使用状态: {default_path_used}')
        
        if not os.path.exists(folder_path):
            logger.warning(f'文件夹不存在: {folder_path}')
            return JSONResponse(content={"success": False, "error": f"指定的文件夹不存在: {folder_path}", "default_path_used": default_path_used}, status_code=404)
        
        if not os.path.isdir(folder_path):
            logger.warning(f'指定的路径不是文件夹: {folder_path}')
            return JSONResponse(content={"success": False, "error": f"指定的路径不是文件夹: {folder_path}", "default_path_used": default_path_used}, status_code=400)
        
        # 扫描本地创意工坊物品
        local_items = []
        published_items = []
        item_id = 1
        item_source = "N.E.K.O./workshop"
        
        # 获取Steam下载的workshop路径，这个路径需要被排除
        steam_workshop_path = get_workshop_path()
        
        # 遍历文件夹，扫描所有子文件夹
        for item_folder in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item_folder)
            if os.path.isdir(item_path):
                    
                # 排除Steam下载的物品目录（WORKSHOP_PATH）
                if os.path.normpath(item_path) == os.path.normpath(steam_workshop_path):
                    logger.info(f"跳过Steam下载的workshop目录: {item_path}")
                    continue
                stat_info = os.stat(item_path)
                
                # 处理预览图路径（如果有）
                preview_image = find_preview_image_in_folder(item_path)
                
                local_items.append({
                    "id": f"local_{item_id}",
                    "source": item_source,
                    "name": item_folder,
                    "path": item_path,  # 返回绝对路径
                    "lastModified": stat_info.st_mtime,
                    "size": get_folder_size(item_path),
                    "tags": ["本地文件"],
                    "previewImage": preview_image  # 返回绝对路径
                })
                item_id += 1
        
        logger.info(f"扫描完成，找到 {len(local_items)} 个本地创意工坊物品")
        
        return JSONResponse(content={
            "success": True,
            "local_items": local_items,
            "published_items": published_items,
            "folder_path": folder_path,  # 返回绝对路径
            "default_path_used": default_path_used
        })
        
    except Exception as e:
        logger.error(f"扫描本地创意工坊物品失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

# 获取创意工坊配置

@router.get('/local-items/{item_id}')
async def get_local_workshop_item(item_id: str, folder_path: str = None):
    try:
        # 这个接口需要从缓存或临时存储中获取物品信息
        # 这里简化实现，实际应用中应该有更完善的缓存机制
        # folder_path 已经通过函数参数获取
        
        if not folder_path:
            return JSONResponse(content={"success": False, "error": "未提供文件夹路径"}, status_code=400)
        
        # 安全检查：始终使用get_workshop_path()作为基础目录
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 解码并处理Windows路径
            decoded_folder_path = unquote(folder_path)
            # 替换斜杠为反斜杠，确保Windows路径格式正确
            decoded_folder_path = decoded_folder_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_folder_path.startswith('\\\\'):
                decoded_folder_path = decoded_folder_path[2:]  # 移除多余的反斜杠前缀
        else:
            decoded_folder_path = unquote(folder_path)
        
        # 关键修复：将相对路径转换为基于基础目录的绝对路径
        # 确保路径是绝对路径，如果不是则视为相对路径
        if not os.path.isabs(decoded_folder_path):
            # 将相对路径转换为基于基础目录的绝对路径
            full_path = os.path.join(base_workshop_folder, decoded_folder_path)
        else:
            # 如果已经是绝对路径，仍然确保它在基础目录内（安全检查）
            full_path = decoded_folder_path
            # 标准化路径
            full_path = os.path.normpath(full_path)
            
        # 安全检查：验证路径是否在基础目录内
        full_path = os.path.realpath(os.path.normpath(full_path))
        if os.path.commonpath([full_path, base_workshop_folder]) != base_workshop_folder:
            logger.warning(f'路径遍历尝试被拒绝: {folder_path}')
            return JSONResponse(content={"success": False, "error": "访问被拒绝: 路径不在允许的范围内"}, status_code=403)
        
        folder_path = full_path
        logger.info(f'处理后的完整路径: {folder_path}')
        
        # 解析本地ID
        if item_id.startswith('local_'):
            index = int(item_id.split('_')[1])
            
            try:
                # 检查folder_path是否已经是项目文件夹路径
                if os.path.isdir(folder_path):
                    # 情况1：folder_path直接指向项目文件夹
                    stat_info = os.stat(folder_path)
                    item_name = os.path.basename(folder_path)
                    
                    item = {
                        "id": item_id,
                        "name": item_name,
                        "path": folder_path,
                        "lastModified": stat_info.st_mtime,
                        "size": get_folder_size(folder_path),
                        "tags": ["模组"],
                        "previewImage": find_preview_image_in_folder(folder_path)
                    }
                    
                    return JSONResponse(content={"success": True, "item": item})
                else:
                    # 情况2：尝试原始逻辑，从folder_path中查找第index个子文件夹
                    items = []
                    for i, item_folder in enumerate(os.listdir(folder_path)):
                        item_path = os.path.join(folder_path, item_folder)
                        if os.path.isdir(item_path) and i + 1 == index:
                            stat_info = os.stat(item_path)
                            items.append({
                                "id": f"local_{i + 1}",
                                "name": item_folder,
                                "path": item_path,
                                "lastModified": stat_info.st_mtime,
                                "size": get_folder_size(item_path),
                                "tags": ["模组"],
                                "previewImage": find_preview_image_in_folder(item_path)
                            })
                            break
                    
                    if items:
                        return JSONResponse(content={"success": True, "item": items[0]})
                    else:
                        return JSONResponse(content={"success": False, "error": "物品不存在"}, status_code=404)
            except Exception as e:
                logger.error(f"处理本地物品路径时出错: {e}")
                return JSONResponse(content={"success": False, "error": f"路径处理错误: {str(e)}"}, status_code=500)
        
        return JSONResponse(content={"success": False, "error": "无效的物品ID格式"}, status_code=400)
        
    except Exception as e:
        logger.error(f"获取本地创意工坊物品失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


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
        chara_file_name = data.get('fileName', 'character.chara.json')
        character_card_name = data.get('character_card_name')  # 新增：角色卡名称
        
        if not chara_data or not model_name:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数"
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
            meta_data = read_workshop_meta(character_card_name)
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
        with open(chara_file_path, 'w', encoding='utf-8') as f:
            json.dump(chara_data, f, ensure_ascii=False, indent=2)
        logger.info(f"角色卡已复制到临时目录: {chara_file_path}")
        
        # 2. 查找模型目录并复制模型文件
        model_dir, _ = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            # 清理临时目录
            shutil.rmtree(temp_item_dir, ignore_errors=True)
            return JSONResponse({
                "success": False,
                "error": f"模型目录不存在: {model_name}"
            }, status_code=404)
        
        # 复制整个模型目录到临时目录
        model_dest_dir = os.path.join(temp_item_dir, model_name)
        shutil.copytree(model_dir, model_dest_dir, dirs_exist_ok=True)
        logger.info(f"模型文件已复制到临时目录: {model_dest_dir}")
        
        # 读取 .workshop_meta.json（如果存在）
        workshop_item_id = None
        if character_card_name:
            meta_data = read_workshop_meta(character_card_name)
            if meta_data and meta_data.get('workshop_item_id'):
                workshop_item_id = meta_data.get('workshop_item_id')
                logger.info(f"检测到已存在的 Workshop 物品 ID: {workshop_item_id}")
        
        return JSONResponse({
            "success": True,
            "temp_folder": temp_item_dir,
            "item_id": item_id,
            "workshop_item_id": workshop_item_id,  # 如果存在，返回已存在的物品ID
            "message": "上传准备完成"
        })
        
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
            shutil.rmtree(temp_folder, ignore_errors=True)
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
                    shutil.copy2(preview_image, new_preview_path)
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
                            shutil.copy2(preview_image, new_preview_path)
                            logger.info(f'自动找到的预览图片已统一命名: {new_preview_path}')
                            preview_image = new_preview_path
                        except Exception as e:
                            logger.error(f'重命名自动预览图片失败: {e}')
                            # 如果重命名失败，继续使用原始路径
                            logger.warning(f'继续使用原始预览图片路径: {preview_image}')
        
        # 记录将要上传的内容信息
        logger.info(f"准备发布创意工坊物品: {title}")
        logger.info(f"内容文件夹: {content_folder}")
        logger.info(f"预览图片: {preview_image or '无'}")
        logger.info(f"可见性: {visibility}")
        logger.info(f"标签: {tags}")
        logger.info(f"内容文件夹包含文件数量: {len([f for f in os.listdir(content_folder) if os.path.isfile(os.path.join(content_folder, f))])}")
        logger.info(f"内容文件夹包含子文件夹数量: {len([f for f in os.listdir(content_folder) if os.path.isdir(os.path.join(content_folder, f))])}")
        
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
                        with open(chara_files[0], 'r', encoding='utf-8') as f:
                            chara_data = json.load(f)
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
                write_workshop_meta(character_card_name, published_file_id, content_hash, uploaded_snapshot)
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
                    time.sleep(0.1)  # 每100毫秒检查一次
            
                if not created_event.is_set():
                    logger.error("创建创意工坊物品超时，可能是网络问题或Steam服务暂时不可用")
                    raise TimeoutError("创建创意工坊物品超时")
            
                if created_item_id[0] is None:
                    # 提供更具体的错误信息
                    error_msg = error_codes.get(create_result[0], f"未知错误码: {create_result[0]}")
                    logger.error(f"创建创意工坊物品失败: {error_msg}")
                
                    # 针对错误码10（权限不足）提供更详细的错误信息和解决方案
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



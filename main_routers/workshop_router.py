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
import re
import json
import glob
import time
import logging
import asyncio
import threading
from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .shared_state import get_steamworks, get_config_manager
from utils.workshop_utils import (
    load_workshop_config,
    save_workshop_config,
    ensure_workshop_folder_exists,
    get_workshop_root,
    get_workshop_path,
)


def is_path_within_base(base: str, candidate: str) -> bool:
    """
    Check if a candidate path is contained within a base path.
    
    Resolves both paths to absolute/real paths (following symlinks),
    normalizes case on Windows using os.path.normcase, then uses
    os.path.commonpath to determine containment.
    
    Args:
        base: The base directory path that should contain the candidate.
        candidate: The candidate path to check.
    
    Returns:
        True if candidate is within base, False otherwise.
    """
    try:
        # Resolve to absolute/real paths (follow symlinks)
        resolved_base = os.path.normcase(os.path.realpath(os.path.abspath(base)))
        resolved_candidate = os.path.normcase(os.path.realpath(os.path.abspath(candidate)))
        
        # Use commonpath to check containment
        common = os.path.commonpath([resolved_base, resolved_candidate])
        return common == resolved_base
    except ValueError:
        # commonpath raises ValueError if paths are on different drives (Windows)
        # or if the list is empty
        return False


router = APIRouter(tags=["workshop"])
logger = logging.getLogger("Main")

# Global lock for thread-safe steamworks operations
# Protects _publish_workshop_item from concurrent access to the steamworks singleton
_steamworks_lock = threading.Lock()


def _get_app_root():
    """获取应用程序根目录"""
    import sys
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()


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


@router.get('/api/steam/workshop/subscribed-items')
async def get_subscribed_workshop_items():
    """
    获取用户订阅的Steam创意工坊物品列表
    返回包含物品ID、基本信息和状态的JSON数据
    """
    steamworks = get_steamworks()
    
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        num_subscribed_items = steamworks.Workshop.GetNumSubscribedItems()
        logger.info(f"获取到 {num_subscribed_items} 个订阅的创意工坊物品")
        
        if num_subscribed_items == 0:
            return {"success": True, "items": [], "total": 0}
        
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        logger.info(f'获取到 {len(subscribed_items)} 个订阅的创意工坊物品')
        
        items_info = []
        
        for item_id in subscribed_items:
            try:
                if isinstance(item_id, str):
                    try:
                        item_id = int(item_id)
                    except ValueError:
                        logger.error(f"无效的物品ID: {item_id}")
                        continue
                
                logger.debug(f'正在处理物品ID: {item_id}')
                
                item_state = steamworks.Workshop.GetItemState(item_id)
                logger.debug(f'物品 {item_id} 状态: {item_state}')
                
                item_info = {
                    "publishedFileId": str(item_id),
                    "title": f"未知物品_{item_id}",
                    "description": "无法获取详细描述",
                    "tags": [],
                    "state": {
                        "subscribed": bool(item_state & 1),
                        "legacyItem": bool(item_state & 2),
                        "installed": False,
                        "needsUpdate": bool(item_state & 8),
                        "downloading": False,
                        "downloadPending": bool(item_state & 32),
                        "isWorkshopItem": bool(item_state & 128)
                    },
                    "installedFolder": None,
                    "fileSizeOnDisk": 0,
                    "downloadProgress": {
                        "bytesDownloaded": 0,
                        "bytesTotal": 0,
                        "percentage": 0
                    },
                    "timeAdded": int(datetime.now().timestamp()),
                    "timeUpdated": int(datetime.now().timestamp())
                }
                
                # 获取安装信息
                try:
                    logger.debug(f'获取物品 {item_id} 的安装信息')
                    result = steamworks.Workshop.GetItemInstallInfo(item_id)
                    
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 安装信息字典: {result}')
                        item_info["state"]["installed"] = True
                        folder_path = result.get('folder', '')
                        item_info["installedFolder"] = str(folder_path) if folder_path else None
                        logger.debug(f'物品 {item_id} 的安装路径: {item_info["installedFolder"]}')
                        disk_size = result.get('disk_size', 0)
                        item_info["fileSizeOnDisk"] = int(disk_size) if isinstance(disk_size, (int, float)) else 0
                    elif isinstance(result, tuple) and len(result) >= 3:
                        installed, folder, size = result
                        logger.debug(f'物品 {item_id} 安装状态: 已安装={installed}, 路径={folder}, 大小={size}')
                        item_info["state"]["installed"] = bool(installed)
                        item_info["installedFolder"] = str(folder) if folder and isinstance(folder, (str, bytes)) else None
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
                
                # 获取下载信息
                try:
                    logger.debug(f'获取物品 {item_id} 的下载信息')
                    result = steamworks.Workshop.GetItemDownloadInfo(item_id)
                    
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 下载信息字典: {result}')
                        downloaded = result.get('downloaded', 0)
                        total = result.get('total', 0)
                        progress = result.get('progress', 0.0)
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        if downloaded > 0 or total > 0:
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": int(downloaded),
                                "bytesTotal": int(total),
                                "percentage": progress * 100 if isinstance(progress, (int, float)) else 0
                            }
                    elif isinstance(result, tuple) and len(result) >= 3:
                        downloaded, total, progress = result if len(result) >= 3 else (0, 0, 0.0)
                        logger.debug(f'物品 {item_id} 下载状态: 已下载={downloaded}, 总计={total}, 进度={progress}')
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        if downloaded > 0 or total > 0:
                            try:
                                downloaded_value = int(downloaded.value) if hasattr(downloaded, 'value') else int(downloaded)
                                total_value = int(total.value) if hasattr(total, 'value') else int(total)
                                progress_value = float(progress.value) if hasattr(progress, 'value') else float(progress)
                            except Exception as e:
                                logger.error(f"获取物品 {item_id} 下载进度时出错: {e}")
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
                
                # 尝试通过UGC API获取详细信息
                try:
                    logger.debug(f'使用官方推荐方法获取物品 {item_id} 的详细信息')
                    query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest([item_id])
                    
                    if query_handle:
                        steamworks.Workshop.SendQueryUGCRequest(query_handle)
                        time.sleep(0.5)  # 等待查询完成
                        
                        try:
                            result = steamworks.Workshop.GetQueryUGCResult(query_handle, 0)
                            if result:
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
                                logger.info(f"成功获取物品 {item_id} 的详情信息")
                        except Exception as query_error:
                            logger.warning(f"获取查询结果时出错: {query_error}")
                except Exception as api_error:
                    logger.warning(f"使用官方API获取物品 {item_id} 详情时出错: {api_error}")
                
                # 从本地文件获取信息（备选方案）
                if item_info['title'].startswith('未知物品_') or not item_info['description']:
                    install_folder = item_info.get('installedFolder')
                    if install_folder and os.path.exists(install_folder):
                        logger.debug(f'尝试从安装文件夹获取物品信息: {install_folder}')
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
                                            if "title" in config_data and config_data["title"]:
                                                item_info["title"] = config_data["title"]
                                            elif "name" in config_data and config_data["name"]:
                                                item_info["title"] = config_data["name"]
                                            if "description" in config_data and config_data["description"]:
                                                item_info["description"] = config_data["description"]
                                        else:
                                            first_line = f.readline().strip()
                                            if first_line and item_info['title'].startswith('未知物品_'):
                                                item_info['title'] = first_line[:100]
                                    logger.info(f"从本地文件 {os.path.basename(config_path)} 成功获取物品 {item_id} 的信息")
                                    break
                                except Exception as file_error:
                                    logger.warning(f"读取配置文件 {config_path} 时出错: {file_error}")
                
                # 确保publishedFileId是字符串类型
                item_info['publishedFileId'] = str(item_info['publishedFileId'])
                
                # 获取预览图
                preview_url = None
                install_folder = item_info.get('installedFolder')
                if install_folder and os.path.exists(install_folder):
                    try:
                        preview_image_path = find_preview_image_in_folder(install_folder)
                        if preview_image_path:
                            if os.name == 'nt':
                                proxy_path = preview_image_path.replace('\\', '/')
                            else:
                                proxy_path = preview_image_path
                            preview_url = f"/api/proxy-image?image_path={quote(proxy_path)}"
                            logger.debug(f'为物品 {item_id} 找到本地预览图: {preview_url}')
                    except Exception as preview_error:
                        logger.warning(f'查找物品 {item_id} 预览图时出错: {preview_error}')
                
                if preview_url:
                    item_info['previewUrl'] = preview_url
                
                items_info.append(item_info)
                logger.debug(f'物品 {item_id} 信息已添加到结果列表: {item_info["title"]}')
                
            except Exception as item_error:
                logger.error(f"获取物品 {item_id} 信息时出错: {item_error}")
                try:
                    basic_item_info = {
                        "publishedFileId": str(item_id),
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
                continue
        
        return {"success": True, "items": items_info, "total": len(items_info)}
        
    except Exception as e:
        logger.error(f"获取订阅物品列表时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取订阅物品失败: {str(e)}"
        }, status_code=500)


@router.get('/api/steam/workshop/item/{item_id}/path')
async def get_workshop_item_path(item_id: str):
    """获取单个Steam创意工坊物品的下载路径"""
    steamworks = get_steamworks()
    
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        item_id_int = int(item_id)
        install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
        
        if not install_info:
            return JSONResponse({
                "success": False,
                "error": "物品未安装",
                "message": f"物品 {item_id} 尚未安装或安装信息不可用"
            }, status_code=404)
        
        folder_path = install_info.get('folder', '')
        
        response = {
            "success": True,
            "item_id": item_id,
            "installed": True,
            "path": folder_path,
            "full_path": folder_path
        }
        
        try:
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                response['size_on_disk'] = int(disk_size)
        except Exception as e:
            logger.info(f"获取物品 {item_id} 磁盘大小失败: {e}")
        
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


@router.get('/api/steam/workshop/item/{item_id}')
async def get_workshop_item_details(item_id: str):
    """获取单个Steam创意工坊物品的详细信息"""
    steamworks = get_steamworks()
    
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        item_id_int = int(item_id)
        item_state = steamworks.Workshop.GetItemState(item_id_int)
        query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest([item_id_int])
        steamworks.Workshop.SendQueryUGCRequest(query_handle)
        result = steamworks.Workshop.GetQueryUGCResult(query_handle, 0)
        
        if result:
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
            installed = bool(install_info)
            folder = install_info.get('folder', '') if installed else ''
            size = 0
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                size = int(disk_size)
            
            download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int)
            downloading = False
            bytes_downloaded = 0
            bytes_total = 0
            
            if download_info:
                if isinstance(download_info, dict):
                    downloaded = int(download_info.get("downloaded", 0) or 0)
                    total = int(download_info.get("total", 0) or 0)
                    downloading = downloaded > 0 and downloaded < total
                    bytes_downloaded = downloaded
                    bytes_total = total
                elif isinstance(download_info, tuple) and len(download_info) >= 3:
                    downloading, bytes_downloaded, bytes_total = download_info
            
            title = result.title.decode('utf-8', errors='replace') if hasattr(result, 'title') and isinstance(result.title, bytes) else getattr(result, 'title', '')
            description = result.description.decode('utf-8', errors='replace') if hasattr(result, 'description') and isinstance(result.description, bytes) else getattr(result, 'description', '')
            
            item_info = {
                "publishedFileId": item_id_int,
                "title": title,
                "description": description,
                "steamIDOwner": result.steamIDOwner,
                "timeCreated": result.timeCreated,
                "timeUpdated": result.timeUpdated,
                "previewImageUrl": result.URL,
                "fileUrl": result.URL,
                "fileSize": result.fileSize,
                "fileId": result.file,
                "previewFileId": result.previewFile,
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
            
            return {"success": True, "item": item_info}
        else:
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


@router.post('/api/steam/workshop/item/unsubscribe')
async def unsubscribe_workshop_item(request: Request):
    """取消订阅Steam创意工坊物品"""
    steamworks = get_steamworks()
    
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        data = await request.json()
        item_id = data.get('item_id')
        
        if not item_id:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数",
                "message": "请求中缺少物品ID"
            }, status_code=400)
        
        try:
            item_id_int = int(item_id)
        except ValueError:
            return JSONResponse({
                "success": False,
                "error": "无效的物品ID",
                "message": "提供的物品ID不是有效的数字"
            }, status_code=400)
        
        def unsubscribe_callback(result):
            if result.result == 1:
                logger.info(f"取消订阅成功回调: {item_id_int}")
            else:
                logger.warning(f"取消订阅失败回调: {item_id_int}, 错误代码: {result.result}")
        
        steamworks.Workshop.UnsubscribeItem(item_id_int, callback=unsubscribe_callback)
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


@router.post('/api/steam/workshop/unsubscribe')
async def unsubscribe_workshop_item_legacy(request: Request):
    """取消订阅Steam创意工坊物品 (兼容旧API)"""
    return await unsubscribe_workshop_item(request)


@router.get('/api/steam/workshop/config')
async def get_workshop_config_api():
    """获取创意工坊配置"""
    try:
        workshop_config_data = load_workshop_config()
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"获取创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}


@router.post('/api/steam/workshop/config')
async def save_workshop_config_api(config_data: dict):
    """保存创意工坊配置"""
    try:
        workshop_config_data = load_workshop_config() or {}
        
        if 'default_workshop_folder' in config_data:
            workshop_config_data['default_workshop_folder'] = config_data['default_workshop_folder']
        if 'auto_create_folder' in config_data:
            workshop_config_data['auto_create_folder'] = config_data['auto_create_folder']
        if 'user_mod_folder' in config_data:
            workshop_config_data['user_mod_folder'] = config_data['user_mod_folder']
        
        save_workshop_config(workshop_config_data)
        
        if workshop_config_data.get('auto_create_folder', True):
            folder_path = workshop_config_data.get('user_mod_folder') or workshop_config_data.get('default_workshop_folder')
            if folder_path:
                ensure_workshop_folder_exists(folder_path)
        
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"保存创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}


@router.post('/api/steam/workshop/local-items/scan')
async def scan_local_workshop_items(request: Request):
    """扫描本地创意工坊物品"""
    try:
        logger.info('接收到扫描本地创意工坊物品的API请求')
        
        workshop_config_data = load_workshop_config()
        logger.info(f'创意工坊配置已加载: {workshop_config_data}')
        
        data = await request.json()
        logger.info(f'请求数据: {data}')
        folder_path = data.get('folder_path')
        
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        default_path_used = False
        if not folder_path:
            folder_path = base_workshop_folder
            default_path_used = True
            logger.info(f'未提供文件夹路径，使用默认路径: {folder_path}')
            ensure_workshop_folder_exists(folder_path)
        else:
            folder_path = os.path.normpath(folder_path)
            if not os.path.isabs(folder_path):
                folder_path = os.path.normpath(folder_path)
            logger.info(f'用户指定路径: {folder_path}')
        
        logger.info(f'最终使用的文件夹路径: {folder_path}, 默认路径使用状态: {default_path_used}')
        
        if not os.path.exists(folder_path):
            logger.warning(f'文件夹不存在: {folder_path}')
            return JSONResponse(content={"success": False, "error": f"指定的文件夹不存在: {folder_path}", "default_path_used": default_path_used}, status_code=404)
        
        if not os.path.isdir(folder_path):
            logger.warning(f'指定的路径不是文件夹: {folder_path}')
            return JSONResponse(content={"success": False, "error": f"指定的路径不是文件夹: {folder_path}", "default_path_used": default_path_used}, status_code=400)
        
        local_items = []
        published_items = []
        item_id = 1
        
        steam_workshop_path = get_workshop_path()
        
        for item_folder in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item_folder)
            if os.path.isdir(item_path):
                if os.path.normpath(item_path) == os.path.normpath(steam_workshop_path):
                    logger.info(f"跳过Steam下载的workshop目录: {item_path}")
                    continue
                stat_info = os.stat(item_path)
                preview_image = find_preview_image_in_folder(item_path)
                
                local_items.append({
                    "id": f"local_{item_id}",
                    "name": item_folder,
                    "path": item_path,
                    "lastModified": stat_info.st_mtime,
                    "size": get_folder_size(item_path),
                    "tags": ["本地文件"],
                    "previewImage": preview_image
                })
                item_id += 1
        
        logger.info(f"扫描完成，找到 {len(local_items)} 个本地创意工坊物品")
        
        return JSONResponse(content={
            "success": True,
            "local_items": local_items,
            "published_items": published_items,
            "folder_path": folder_path,
            "default_path_used": default_path_used
        })
        
    except Exception as e:
        logger.error(f"扫描本地创意工坊物品失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get('/api/steam/workshop/local-items/{item_id}')
async def get_local_workshop_item(item_id: str, folder_path: str = None):
    """获取本地创意工坊物品详情"""
    try:
        if not folder_path:
            return JSONResponse(content={"success": False, "error": "未提供文件夹路径"}, status_code=400)
        
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        if os.name == 'nt':
            decoded_folder_path = unquote(folder_path)
            decoded_folder_path = decoded_folder_path.replace('/', '\\')
            if decoded_folder_path.startswith('\\\\'):
                decoded_folder_path = decoded_folder_path[2:]
        else:
            decoded_folder_path = unquote(folder_path)
        
        if not os.path.isabs(decoded_folder_path):
            full_path = os.path.join(base_workshop_folder, decoded_folder_path)
        else:
            full_path = decoded_folder_path
            full_path = os.path.normpath(full_path)
            
        if not is_path_within_base(base_workshop_folder, full_path):
            logger.warning(f'路径遍历尝试被拒绝: {folder_path}')
            return JSONResponse(content={"success": False, "error": "访问被拒绝: 路径不在允许的范围内"}, status_code=403)
        
        folder_path = full_path
        logger.info(f'处理后的完整路径: {folder_path}')
        
        if item_id.startswith('local_'):
            index = int(item_id.split('_')[1])
            
            try:
                if os.path.isdir(folder_path):
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


@router.get('/api/steam/workshop/check-upload-status')
async def check_upload_status(item_path: str = None):
    """检查物品上传状态"""
    try:
        if not item_path:
            return JSONResponse(content={
                "success": False,
                "error": "未提供物品文件夹路径"
            }, status_code=400)
        
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        if os.name == 'nt':
            decoded_item_path = unquote(item_path)
            decoded_item_path = decoded_item_path.replace('/', '\\')
            if decoded_item_path.startswith('\\\\'):
                decoded_item_path = decoded_item_path[2:]
        else:
            decoded_item_path = unquote(item_path)
        
        if not os.path.isabs(decoded_item_path):
            full_path = os.path.join(base_workshop_folder, decoded_item_path)
        else:
            full_path = decoded_item_path
            full_path = os.path.normpath(full_path)
        
        if not is_path_within_base(base_workshop_folder, full_path):
            logger.warning(f'路径遍历尝试被拒绝: {item_path}')
            return JSONResponse(content={"success": False, "error": "访问被拒绝: 路径不在允许的范围内"}, status_code=403)
        
        if not os.path.exists(full_path) or not os.path.isdir(full_path):
            return JSONResponse(content={
                "success": False,
                "error": "无效的物品文件夹路径"
            }, status_code=400)
        
        upload_files = glob.glob(os.path.join(full_path, "steam_workshop_id_*.txt"))
        
        published_file_id = None
        if upload_files:
            first_file = upload_files[0]
            match = re.search(r'steam_workshop_id_(\d+)\.txt', os.path.basename(first_file))
            if match:
                published_file_id = match.group(1)
        
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


@router.post('/api/steam/workshop/publish')
async def publish_to_workshop(request: Request):
    """发布到Steam创意工坊"""
    from steamworks.enums import EWorkshopFileType, ERemoteStoragePublishedFileVisibility, EItemUpdateStatus
    
    steamworks = get_steamworks()
    
    if steamworks is None:
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        data = await request.json()
        
        required_fields = ['title', 'content_folder', 'visibility']
        for field in required_fields:
            if field not in data:
                return JSONResponse(content={"success": False, "error": f"缺少必要字段: {field}"}, status_code=400)
        
        title = data['title']
        content_folder = data['content_folder']
        visibility = int(data['visibility'])
        preview_image = data.get('preview_image', '')
        description = data.get('description', '')
        tags = data.get('tags', [])
        change_note = data.get('change_note', '初始发布')
        
        content_folder = unquote(content_folder)
        if os.name == 'nt':
            content_folder = content_folder.replace('/', '\\')
            if content_folder.startswith('\\\\'):
                content_folder = content_folder[2:]
        else:
            content_folder = content_folder.replace('\\', '/')
        
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
        
        if not any(os.scandir(content_folder)):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹为空",
                "message": f"内容文件夹为空，请确保包含要上传的文件: {content_folder}"
            }, status_code=400)
        
        if not os.access(content_folder, os.R_OK):
            return JSONResponse(content={
                "success": False,
                "error": "没有文件夹访问权限",
                "message": f"没有读取内容文件夹的权限: {content_folder}"
            }, status_code=403)
        
        if preview_image:
            preview_image = unquote(preview_image)
            if os.name == 'nt':
                preview_image = preview_image.replace('/', '\\')
                if preview_image.startswith('\\\\'):
                    preview_image = preview_image[2:]
            else:
                preview_image = preview_image.replace('\\', '/')
            
            if not os.path.exists(preview_image):
                logger.warning(f'指定的预览图片不存在，尝试在内容文件夹中查找: {preview_image}')
                auto_preview = find_preview_image_in_folder(content_folder)
                if auto_preview:
                    logger.info(f'找到自动预览图片: {auto_preview}')
                    preview_image = auto_preview
                else:
                    logger.warning(f'无法找到预览图片')
                    preview_image = ''
            
            if preview_image and not os.path.isfile(preview_image):
                return JSONResponse(content={
                    "success": False,
                    "error": "预览图片无效",
                    "message": f"预览图片路径不是有效的文件: {preview_image}"
                }, status_code=400)
        else:
            auto_preview = find_preview_image_in_folder(content_folder)
            if auto_preview:
                logger.info(f'自动找到预览图片: {auto_preview}')
                preview_image = auto_preview
        
        logger.info(f"准备发布创意工坊物品: {title}")
        logger.info(f"内容文件夹: {content_folder}")
        logger.info(f"预览图片: {preview_image or '无'}")
        logger.info(f"可见性: {visibility}")
        logger.info(f"标签: {tags}")
        
        loop = asyncio.get_event_loop()
        published_file_id = await loop.run_in_executor(
            None, 
            lambda: _publish_workshop_item(
                steamworks, title, description, content_folder, 
                preview_image, visibility, tags, change_note
            )
        )
        
        logger.info(f"成功发布创意工坊物品，ID: {published_file_id}")
        return JSONResponse(content={
            "success": True,
            "published_file_id": published_file_id,
            "message": "发布成功"
        })
        
    except ValueError as ve:
        logger.error(f"参数错误: {ve}")
        return JSONResponse(content={"success": False, "error": str(ve)}, status_code=400)
    except Exception as e:
        logger.error(f"发布到创意工坊失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


def _publish_workshop_item(steamworks, title, description, content_folder, preview_image, visibility, tags, change_note):
    """在单独的线程中执行Steam创意工坊发布操作"""
    from steamworks.enums import EWorkshopFileType, ERemoteStoragePublishedFileVisibility, EItemUpdateStatus
    
    # 检查是否已存在上传标记
    try:
        if os.path.exists(content_folder) and os.path.isdir(content_folder):
            marker_files = glob.glob(os.path.join(content_folder, "steam_workshop_id_*.txt"))
            if marker_files:
                marker_file = marker_files[0]
                match = re.search(r'steam_workshop_id_([0-9]+)\.txt', marker_file)
                if match:
                    existing_item_id = int(match.group(1))
                    logger.info(f"检测到物品已上传，找到标记文件: {marker_file}，物品ID: {existing_item_id}")
                    return existing_item_id
    except Exception as e:
        logger.error(f"检查上传标记文件时出错: {e}")
    
    # Validate content folder before acquiring lock (no steamworks access needed)
    if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
        raise Exception(f"内容文件夹不存在或无效: {content_folder}")
    
    file_count = 0
    for root, dirs, files in os.walk(content_folder):
        file_count += len(files)
    
    if file_count == 0:
        raise Exception(f"内容文件夹中没有找到可上传的文件: {content_folder}")
    
    logger.info(f"内容文件夹验证通过，包含 {file_count} 个文件")
    
    # Acquire lock for all steamworks operations to ensure thread safety
    with _steamworks_lock:
        try:
            app_id = steamworks.app_id
            logger.info(f"使用应用ID: {app_id} 进行创意工坊上传")
            
            # 创建物品
            created_item_id = [None]
            created_event = threading.Event()
            create_result = [None]
            
            def onCreateItem(result):
                nonlocal created_item_id, create_result
                create_result[0] = result.result
                if result.result == 1:
                    created_item_id[0] = result.publishedFileId
                    logger.info(f"成功创建创意工坊物品，ID: {created_item_id[0]}")
                else:
                    logger.error(f"创建创意工坊物品失败，错误码: {result.result}")
                created_event.set()
            
            steamworks.Workshop.SetItemCreatedCallback(onCreateItem)
            logger.info(f"开始创建创意工坊物品: {title}")
            steamworks.Workshop.CreateItem(app_id, EWorkshopFileType.COMMUNITY)
            
            start_time = time.time()
            timeout = 60
            while time.time() - start_time < timeout:
                if created_event.is_set():
                    break
                try:
                    steamworks.run_callbacks()
                except Exception as e:
                    logger.error(f"执行Steam回调时出错: {str(e)}")
                time.sleep(0.1)
            
            if not created_event.is_set():
                logger.error("创建创意工坊物品超时")
                raise TimeoutError("创建创意工坊物品超时")
            
            if created_item_id[0] is None:
                raise Exception(f"创建创意工坊物品失败 (错误码: {create_result[0]})")
            
            # 更新物品
            logger.info(f"开始更新物品内容: {title}")
            update_handle = steamworks.Workshop.StartItemUpdate(app_id, created_item_id[0])
            
            steamworks.Workshop.SetItemTitle(update_handle, title)
            if description:
                steamworks.Workshop.SetItemDescription(update_handle, description)
            
            logger.info(f"设置物品内容文件夹: {content_folder}")
            steamworks.Workshop.SetItemContent(update_handle, content_folder)
            
            if preview_image:
                logger.info(f"设置预览图片: {preview_image}")
                steamworks.Workshop.SetItemPreview(update_handle, preview_image)
            
            if visibility == 0:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
            elif visibility == 1:
                visibility_enum = ERemoteStoragePublishedFileVisibility.FRIENDS_ONLY
            elif visibility == 2:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PRIVATE
            else:
                visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
                
            steamworks.Workshop.SetItemVisibility(update_handle, visibility_enum)
            
            if tags:
                steamworks.Workshop.SetItemTags(update_handle, tags)
            
            updated = [False]
            error_code = [0]
            update_event = threading.Event()
            
            def onSubmitItemUpdate(result):
                nonlocal updated, error_code
                error_code[0] = result.result
                if result.result == 1:
                    updated[0] = True
                    logger.info(f"物品更新提交成功")
                else:
                    logger.error(f"提交创意工坊物品更新失败，错误码: {result.result}")
                update_event.set()
            
            steamworks.Workshop.SetItemUpdatedCallback(onSubmitItemUpdate)
            
            logger.info(f"开始提交物品更新，更新说明: {change_note}")
            steamworks.Workshop.SubmitItemUpdate(update_handle, change_note)
            
            start_time = time.time()
            timeout = 180
            last_progress = -1
            
            while time.time() - start_time < timeout:
                if update_event.is_set():
                    break
                try:
                    steamworks.run_callbacks()
                    if update_handle:
                        progress = steamworks.Workshop.GetItemUpdateProgress(update_handle)
                        if 'status' in progress and 'progress' in progress:
                            current_progress = int(progress['progress'] * 100)
                            if current_progress != last_progress:
                                logger.info(f"上传进度: {current_progress}%")
                                last_progress = current_progress
                except Exception as e:
                    logger.error(f"执行Steam回调时出错: {str(e)}")
                time.sleep(0.5)
            
            if not update_event.is_set():
                logger.error("提交创意工坊物品更新超时")
                raise TimeoutError("提交创意工坊物品更新超时")
            
            if not updated[0]:
                raise Exception(f"提交创意工坊物品更新失败，错误码: {error_code[0]}")
            
            logger.info(f"创意工坊物品上传成功完成！物品ID: {created_item_id[0]}")
            
            # 创建标记文件
            try:
                marker_file_path = os.path.join(content_folder, f"steam_workshop_id_{created_item_id[0]}.txt")
                with open(marker_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Steam创意工坊物品ID: {created_item_id[0]}\n")
                    f.write(f"上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
                    f.write(f"物品标题: {title}\n")
                logger.info(f"已在原文件夹创建上传标记文件: {marker_file_path}")
            except Exception as e:
                logger.error(f"创建上传标记文件失败: {e}")
            
            return created_item_id[0]
            
        except Exception as e:
            logger.error(f"发布创意工坊物品时出错: {e}")
            raise


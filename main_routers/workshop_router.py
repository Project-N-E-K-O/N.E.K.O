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
import logging
import asyncio
import threading
from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .shared_state import get_steamworks
from utils.workshop_utils import (
    ensure_workshop_folder_exists,
    get_workshop_path,
)

router = APIRouter(prefix="/api/steam/workshop", tags=["workshop"])
logger = logging.getLogger("Main")

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

@router.get('/subscribed-items')
def get_subscribed_workshop_items():
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
                            except:
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
                
                # 尝试获取物品详细信息（标题、描述等）- 使用官方推荐的方式
                try:
                    # 使用官方推荐的CreateQueryUGCDetailsRequest和SendQueryUGCRequest方法
                    logger.debug(f'使用官方推荐方法获取物品 {item_id} 的详细信息')
                    
                    # 创建UGC详情查询请求
                    query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest([item_id])
                    
                    if query_handle:
                        # 设置回调函数
                        details_received = False
                        
                        def query_completed_callback(result):
                            nonlocal details_received
                            details_received = True
                            # 回调结果会在主线程中通过GetQueryUGCResult获取
                            pass
                        
                        # 发送查询请求
                        steamworks.Workshop.SendQueryUGCRequest(query_handle, callback=query_completed_callback, override_callback=True)
                        
                        # 等待查询完成（简单的轮询方式）
                        import time
                        timeout = 2  # 2秒超时
                        start_time = time.time()
                        
                        # 由于这是异步回调，我们简单地等待一小段时间让查询有机会完成
                        time.sleep(0.5)  # 等待0.5秒
                        
                        try:
                            # 尝试获取查询结果
                            result = steamworks.Workshop.GetQueryUGCResult(query_handle, 0)
                            if result:
                                # 从结果中提取信息
                                if hasattr(result, 'title') and result.title:
                                    item_info['title'] = result.title.decode('utf-8', errors='replace')
                                if hasattr(result, 'description') and result.description:
                                    item_info['description'] = result.description.decode('utf-8', errors='replace')
                                # 获取创建和更新时间
                                if hasattr(result, 'timeCreated'):
                                    item_info['timeAdded'] = int(result.timeCreated)
                                if hasattr(result, 'timeUpdated'):
                                    item_info['timeUpdated'] = int(result.timeUpdated)
                                # 获取作者信息
                                if hasattr(result, 'steamIDOwner'):
                                    item_info['steamIDOwner'] = str(result.steamIDOwner)
                                # 获取文件大小信息
                                if hasattr(result, 'fileSize'):
                                    item_info['fileSizeOnDisk'] = int(result.fileSize)
                                
                                logger.info(f"成功获取物品 {item_id} 的详情信息")
                        except Exception as query_error:
                            logger.warning(f"获取查询结果时出错: {query_error}")
                except Exception as api_error:
                    logger.warning(f"使用官方API获取物品 {item_id} 详情时出错: {api_error}")
                
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
        
        # 提取安装路径
        folder_path = install_info.get('folder', '')
        
        # 构建响应
        response = {
            "success": True,
            "item_id": item_id,
            "installed": True,
            "path": folder_path,
            "full_path": folder_path  # 完整路径，与path保持一致
        }
        
        # 如果有磁盘大小信息，也一并返回
        try:
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                response['size_on_disk'] = int(disk_size)
        except:
            pass
        
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
def get_workshop_item_details(item_id: str):
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
        time.sleep(0.5)
        
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
        
        # 定义一个简单的回调函数来处理取消订阅的结果
        def unsubscribe_callback(result):
            # 记录取消订阅的结果
            if result.result == 1:  # k_EResultOK
                logger.info(f"取消订阅成功回调: {item_id_int}")
            else:
                logger.warning(f"取消订阅失败回调: {item_id_int}, 错误代码: {result.result}")
        
        # 调用Steamworks的UnsubscribeItem方法，并提供回调函数
        steamworks.Workshop.UnsubscribeItem(item_id_int, callback=unsubscribe_callback)
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
        
        # 规范化路径处理 - 改进版，确保在所有情况下都能正确处理路径
        content_folder = unquote(content_folder)
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
                    logger.warning(f'无法找到预览图片')
                    preview_image = ''
            
            if preview_image and not os.path.isfile(preview_image):
                return JSONResponse(content={
                    "success": False,
                    "error": "预览图片无效",
                    "message": f"预览图片路径不是有效的文件: {preview_image}"
                }, status_code=400)
        else:
            # 如果未指定预览图片，尝试自动查找
            auto_preview = find_preview_image_in_folder(content_folder)
            if auto_preview:
                logger.info(f'自动找到预览图片: {auto_preview}')
                preview_image = auto_preview
        
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

def _publish_workshop_item(steamworks, title, description, content_folder, preview_image, visibility, tags, change_note):
    """
    在单独的线程中执行Steam创意工坊发布操作
    """
    # 在函数内部添加导入语句，确保枚举在函数作用域内可用
    from steamworks.enums import EWorkshopFileType, ERemoteStoragePublishedFileVisibility, EItemUpdateStatus
    
    # 检查是否存在现有的上传标记文件，避免重复上传
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
                    existing_item_id = int(match.group(1))
                    logger.info(f"检测到物品已上传，找到标记文件: {marker_file}，物品ID: {existing_item_id}")
                    return existing_item_id
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
        try:
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
            logger.warning(f"继续尝试创意工坊上传，但可能会因为Steam连接问题而失败")
        
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
                logger.error(f"创意工坊上传失败 - 详细诊断信息:")
                logger.error(f"- 应用ID: {app_id}")
                logger.error(f"- Steam运行状态: {steamworks.IsSteamRunning()}")
                logger.error(f"- 用户登录状态: {steamworks.Users.LoggedOn()}")
                logger.error(f"- 应用订阅状态: {steamworks.Apps.IsSubscribedApp(app_id)}")
                raise Exception(f"创建创意工坊物品失败: {detailed_error} (错误码: {create_result[0]})")
            else:
                raise Exception(f"创建创意工坊物品失败: {error_msg} (错误码: {create_result[0]})")
        
        # 开始更新物品
        logger.info(f"开始更新物品内容: {title}")
        update_handle = steamworks.Workshop.StartItemUpdate(app_id, created_item_id[0])
        
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
        from steamworks.enums import ERemoteStoragePublishedFileVisibility
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
        
        logger.info(f"创意工坊物品上传成功完成！物品ID: {created_item_id[0]}")
        
        # 在原文件夹创建带物品ID的txt文件，标记为已上传
        try:
            marker_file_path = os.path.join(content_folder, f"steam_workshop_id_{created_item_id[0]}.txt")
            with open(marker_file_path, 'w', encoding='utf-8') as f:
                f.write(f"Steam创意工坊物品ID: {created_item_id[0]}\n")
                f.write(f"上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
                f.write(f"物品标题: {title}\n")
            logger.info(f"已在原文件夹创建上传标记文件: {marker_file_path}")
        except Exception as e:
            logger.error(f"创建上传标记文件失败: {e}")
            # 即使创建标记文件失败，也不影响物品上传的成功返回
        
        return created_item_id[0]
        
    except Exception as e:
        logger.error(f"发布创意工坊物品时出错: {e}")
        raise


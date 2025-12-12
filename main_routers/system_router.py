# -*- coding: utf-8 -*-
"""
System Router

Handles system-related endpoints including:
- Server shutdown
- Emotion analysis
- Steam achievements
- File utilities (file-exists, find-first-image, proxy-image)
"""

import os
import sys
import asyncio
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
import httpx
from openai import AsyncOpenAI

from .shared_state import get_steamworks, get_config_manager
from config import MEMORY_SERVER_PORT, get_extra_body
from config.prompts_sys import emotion_analysis_prompt
from utils.workshop_utils import get_workshop_path

router = APIRouter(tags=["system"])
logger = logging.getLogger("Main")


def _get_app_root():
    """获取应用程序根目录"""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()


def get_start_config():
    """从 app.state 获取启动配置 - 需要从外部设置"""
    # This function needs to be set up by main_server to access app.state
    return {
        "browser_mode_enabled": False,
        "browser_page": "chara_manager",
        'server': None
    }


@router.post('/api/beacon/shutdown')
async def beacon_shutdown():
    """Beacon API for graceful server shutdown"""
    try:
        current_config = get_start_config()
        if current_config['browser_mode_enabled']:
            logger.info("收到beacon信号，准备关闭服务器...")
            task = asyncio.create_task(shutdown_server_async())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            return {"success": True, "message": "服务器关闭信号已接收"}
        return {"success": False, "message": "服务器未在浏览器模式下运行"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}


async def shutdown_server_async():
    """异步关闭服务器"""
    try:
        await asyncio.sleep(0.5)
        logger.info("正在关闭服务器...")
        
        # 向memory_server发送关闭信号
        try:
            shutdown_url = f"http://localhost:{MEMORY_SERVER_PORT}/shutdown"
            async with httpx.AsyncClient(timeout=1) as client:
                response = await client.post(shutdown_url)
                if response.status_code == 200:
                    logger.info("已向memory_server发送关闭信号")
        except Exception as e:
            logger.warning(f"向memory_server发送关闭信号时出错: {e}")
        
        current_config = get_start_config()
        if current_config['server'] is not None:
            current_config['server'].should_exit = True
    except Exception as e:
        logger.error(f"关闭服务器时出错: {e}")


@router.post('/api/emotion/analysis')
async def emotion_analysis(request: Request):
    """情绪分析端点"""
    _config_manager = get_config_manager()
    
    try:
        data = await request.json()
        text = data.get('text', '')
        
        if not text:
            return JSONResponse({"error": "缺少文本内容"}, status_code=400)
        
        # 获取情绪分析模型配置
        emotion_config = _config_manager.get_model_api_config('emotion')
        
        model = emotion_config.get('model')
        base_url = emotion_config.get('base_url')
        api_key = emotion_config.get('api_key')
        
        if not model or not base_url or not api_key:
            return JSONResponse({
                "error": "情绪分析模型配置缺失"
            }, status_code=500)
        
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        extra_body = get_extra_body(model)
        
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": emotion_analysis_prompt},
                {"role": "user", "content": text}
            ],
            extra_body=extra_body if extra_body else None
        )
        
        emotion = response.choices[0].message.content.strip()
        
        return {"success": True, "emotion": emotion}
        
    except Exception as e:
        logger.error(f"情绪分析失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/api/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str):
    """设置Steam成就状态"""
    steamworks = get_steamworks()
    
    if steamworks is not None:
        try:
            # 先请求统计数据并运行回调，确保数据已加载
            steamworks.UserStats.RequestCurrentStats()
            # 运行回调等待数据加载（多次运行以确保接收到响应）
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            achievement_status = steamworks.UserStats.GetAchievement(name)
            logger.info(f"Achievement status: {achievement_status}")
            if not achievement_status:
                result = steamworks.UserStats.SetAchievement(name)
                if result:
                    logger.info(f"成功设置成就: {name}")
                    steamworks.UserStats.StoreStats()
                    steamworks.run_callbacks()
                else:
                    # 第一次失败，等待后重试一次
                    logger.warning(f"设置成就首次尝试失败，正在重试: {name}")
                    await asyncio.sleep(0.5)
                    steamworks.run_callbacks()
                    result = steamworks.UserStats.SetAchievement(name)
                    if result:
                        logger.info(f"成功设置成就（重试后）: {name}")
                        steamworks.UserStats.StoreStats()
                        steamworks.run_callbacks()
                    else:
                        logger.error(f"设置成就失败: {name}，请确认成就ID在Steam后台已配置")
            else:
                logger.info(f"成就已解锁，无需重复设置: {name}")
        except Exception as e:
            logger.error(f"设置成就失败: {e}")


@router.get('/api/steam/list-achievements')
async def list_achievements():
    """列出Steam后台已配置的所有成就（调试用）"""
    steamworks = get_steamworks()
    
    if steamworks is not None:
        try:
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            num_achievements = steamworks.UserStats.GetNumAchievements()
            achievements = []
            for i in range(num_achievements):
                name = steamworks.UserStats.GetAchievementName(i)
                if name:
                    # 如果是bytes类型，解码为字符串
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    status = steamworks.UserStats.GetAchievement(name)
                    achievements.append({"name": name, "unlocked": status})
            
            logger.info(f"Steam后台已配置 {num_achievements} 个成就: {achievements}")
            return JSONResponse(content={"count": num_achievements, "achievements": achievements})
        except Exception as e:
            logger.error(f"获取成就列表失败: {e}")
            return JSONResponse(content={"error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"error": "Steamworks未初始化"}, status_code=500)


@router.get('/api/file-exists')
async def check_file_exists(path: str = None):
    """检查文件是否存在"""
    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
        
        # 规范化路径以防止路径遍历攻击
        real_path = os.path.realpath(decoded_path)
        
        # 检查路径是否在允许的目录内
        if any(real_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
            # 检查文件是否存在
            exists = os.path.exists(real_path) and os.path.isfile(real_path)
        else:
            # 不在允许的目录内，返回文件不存在
            exists = False
        
        return JSONResponse(content={"exists": exists})
        
    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)


@router.get('/api/find-first-image')
async def find_first_image(folder: str = None):
    """
    查找指定文件夹中的预览图片 - 增强版，添加了严格的安全检查
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        logger.warning(f"预览图片查找请求: {folder}")
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 添加"我的文档/Xiao8"目录到允许列表
        if os.name == 'nt':
            documents_path = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8')
            if os.path.exists(documents_path):
                real_doc_path = os.path.realpath(documents_path)
                allowed_dirs.append(real_doc_path)
                logger.info(f"find-first-image: 添加允许的文档目录: {real_doc_path}")
        
        decoded_folder = unquote(folder)
        
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # 安全检查
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        is_allowed = False
        for allowed_dir in allowed_dirs:
            if real_folder.startswith(allowed_dir):
                is_allowed = True
                break
        
        if not is_allowed:
            logger.warning(f"访问被拒绝：路径不在允许的目录内 - {real_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 只查找指定的8个预览图片名称
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    real_image_path = os.path.realpath(image_path)
                    if any(real_image_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                        try:
                            relative_path = os.path.relpath(real_image_path, base_dir)
                            return JSONResponse(content={"success": True, "imagePath": relative_path})
                        except ValueError:
                            return JSONResponse(content={"success": True, "imagePath": image_name})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)


@router.get('/api/proxy-image')
async def proxy_image(image_path: str):
    """代理访问本地图片文件，支持绝对路径和相对路径，特别是Steam创意工坊目录"""
    import re

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径
        decoded_path = unquote(image_path)
        decoded_path = unquote(decoded_path)  # 处理双重编码
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)
        
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 添加默认创意工坊目录
        try:
            workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
            if os.path.exists(workshop_base_dir):
                real_workshop_dir = os.path.realpath(workshop_base_dir)
                if real_workshop_dir not in allowed_dirs:
                    allowed_dirs.append(real_workshop_dir)
                    logger.info(f"添加允许的默认创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")
        
        # 动态添加Steam创意工坊路径
        try:
            if ('steamapps\\workshop' in decoded_path.lower() or 
                'steamapps/workshop' in decoded_path.lower()):
                
                workshop_related_dir = None
                
                if os.path.exists(decoded_path):
                    if os.path.isfile(decoded_path):
                        workshop_related_dir = os.path.dirname(decoded_path)
                    else:
                        workshop_related_dir = decoded_path
                else:
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                if workshop_related_dir and os.path.exists(workshop_related_dir):
                    real_workshop_dir = os.path.realpath(workshop_related_dir)
                    if real_workshop_dir not in allowed_dirs:
                        allowed_dirs.append(real_workshop_dir)
                        logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"动态添加创意工坊路径失败: {str(e)}")
        
        logger.info(f"当前允许的目录列表: {allowed_dirs}")

        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]
        
        final_path = None
        
        # 尝试作为绝对路径
        if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
            real_path = os.path.realpath(decoded_path)
            if any(real_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                final_path = real_path
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                real_path = os.path.realpath(alt_path)
                if any(real_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试相对路径处理 - 相对于static目录
        if final_path is None:
            if decoded_path.startswith('..\\static') or decoded_path.startswith('../static'):
                relative_part = decoded_path.split('static')[1]
                if relative_part.startswith(('\\', '/')):
                    relative_part = relative_part[1:]
                relative_path = os.path.join(allowed_dirs[0], relative_part)
                if os.path.exists(relative_path) and os.path.isfile(relative_path):
                    real_path = os.path.realpath(relative_path)
                    if any(real_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                        final_path = real_path
        
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    if real_path.startswith(workshop_base_dir):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        file_size = os.path.getsize(final_path)
        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        if file_size > MAX_IMAGE_SIZE:
            return JSONResponse(content={"success": False, "error": "图片文件过大"}, status_code=400)

        with open(final_path, 'rb') as f:
            image_data = f.read()
        
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
        
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)

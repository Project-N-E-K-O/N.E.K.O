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
from openai import AsyncOpenAI

from .shared_state import get_steamworks, get_config_manager, get_sync_message_queue
from config import MODELS_WITH_EXTRA_BODY
from config.prompts_sys import emotion_analysis_prompt
from utils.workshop_utils import get_workshop_path

router = APIRouter(tags=["system"])
logger = logging.getLogger("Main")

def _get_app_root():
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()
        
@router.post('/api/emotion/analysis')
async def emotion_analysis(request: Request):
    try:
        _config_manager = get_config_manager()
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置，使用 .get() 安全获取避免 KeyError
        emotion_config = _config_manager.get_model_api_config('emotion')
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        
        # 优先使用请求参数，其次使用配置
        api_key = api_key or emotion_api_key
        model = model or emotion_model
        
        if not api_key:
            return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "情绪分析模型配置缺失: 模型名称未提供且配置中未设置默认模型"}
        
        # 创建异步客户端
        client = AsyncOpenAI(api_key=api_key, base_url=emotion_base_url)
        
        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": emotion_analysis_prompt
            },
            {
                "role": "user", 
                "content": text
            }
        ]
        
        # 异步调用模型
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 100
        }
        
        # 只有在需要时才添加 extra_body
        if model in MODELS_WITH_EXTRA_BODY:
            request_params["extra_body"] = {"enable_thinking": False}
        
        response = await client.chat.completions.create(**request_params)
        
        # 解析响应
        result_text = response.choices[0].message.content.strip()
        
        # 尝试解析JSON响应
        try:
            import json
            result = json.loads(result_text)
            # 获取emotion和confidence
            emotion = result.get("emotion", "neutral")
            confidence = result.get("confidence", 0.5)
            
            # 当confidence小于0.3时，自动将emotion设置为neutral
            if confidence < 0.3:
                emotion = "neutral"
            
            # 获取 lanlan_name 并推送到 monitor
            lanlan_name = data.get('lanlan_name')
            sync_message_queue = get_sync_message_queue()
            if lanlan_name and lanlan_name in sync_message_queue:
                sync_message_queue[lanlan_name].put({
                    "type": "json",
                    "data": {
                        "type": "emotion",
                        "emotion": emotion,
                        "confidence": confidence
                    }
                })
            
            return {
                "emotion": emotion,
                "confidence": confidence
            }
        except json.JSONDecodeError:
            # 如果JSON解析失败，返回简单的情感判断
            return {
                "emotion": "neutral",
                "confidence": 0.5
            }
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }


@router.post('/api/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str):
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
    try:
        # file_path 已经通过函数参数获取
        
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
    
    安全注意事项：
    1. 只允许访问项目内特定的安全目录
    2. 防止路径遍历攻击
    3. 限制返回信息，避免泄露文件系统信息
    4. 记录可疑访问尝试
    5. 只返回小于 1MB 的图片（Steam创意工坊预览图大小限制）
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        # 检查参数有效性
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 安全警告日志记录
        logger.warning(f"预览图片查找请求: {folder}")
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 添加"我的文档/Xiao8"目录到允许列表
        if os.name == 'nt':  # Windows系统
            documents_path = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8')
            if os.path.exists(documents_path):
                real_doc_path = os.path.realpath(documents_path)
                allowed_dirs.append(real_doc_path)
                logger.info(f"find-first-image: 添加允许的文档目录: {real_doc_path}")
        
        # 解码URL编码的路径
        decoded_folder = unquote(folder)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # 额外的安全检查：拒绝包含路径遍历字符的请求
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 规范化路径以防止路径遍历攻击
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 检查路径是否在允许的目录内
        is_allowed = False
        for allowed_dir in allowed_dirs:
            if real_folder.startswith(allowed_dir):
                is_allowed = True
                break
        
        if not is_allowed:
            logger.warning(f"访问被拒绝：路径不在允许的目录内 - {real_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 检查文件夹是否存在
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 只查找指定的8个预览图片名称，按优先级顺序
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                # 检查文件是否存在
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    # 检查文件大小是否小于 1MB
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    # 再次验证图片文件路径是否在允许的目录内
                    real_image_path = os.path.realpath(image_path)
                    if any(real_image_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                        # 只返回相对路径或文件名，不返回完整的文件系统路径，避免信息泄露
                        # 计算相对于base_dir的相对路径
                        try:
                            relative_path = os.path.relpath(real_image_path, base_dir)
                            return JSONResponse(content={"success": True, "imagePath": relative_path})
                        except ValueError:
                            # 如果无法计算相对路径（例如跨驱动器），只返回文件名
                            return JSONResponse(content={"success": True, "imagePath": image_name})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        # 发生异常时不泄露详细信息
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)

# 辅助函数

@router.get('/api/proxy-image')
async def proxy_image(image_path: str):
    """代理访问本地图片文件，支持绝对路径和相对路径，特别是Steam创意工坊目录"""

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径（处理双重编码情况）
        decoded_path = unquote(image_path)
        # 再次解码以处理可能的双重编码
        decoded_path = unquote(decoded_path)
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查是否是远程URL，如果是则直接返回错误（目前只支持本地文件）
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        
        # 添加get_workshop_path()返回的路径作为允许目录，支持相对路径解析
        try:
            workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
            if os.path.exists(workshop_base_dir):
                real_workshop_dir = os.path.realpath(workshop_base_dir)
                if real_workshop_dir not in allowed_dirs:
                    allowed_dirs.append(real_workshop_dir)
                    logger.info(f"添加允许的默认创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")
        
        # 动态添加路径到允许列表：如果请求的路径包含创意工坊相关标识，则允许访问
        try:
            # 检查解码后的路径是否包含创意工坊相关路径标识
            if ('steamapps\\workshop' in decoded_path.lower() or 
                'steamapps/workshop' in decoded_path.lower()):
                
                # 获取创意工坊父目录
                workshop_related_dir = None
                
                # 方法1：如果路径存在，获取文件所在目录或直接使用目录路径
                if os.path.exists(decoded_path):
                    if os.path.isfile(decoded_path):
                        workshop_related_dir = os.path.dirname(decoded_path)
                    else:
                        workshop_related_dir = decoded_path
                else:
                    # 方法2：尝试从路径中提取创意工坊相关部分
                    import re
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                # 方法3：如果是Steam创意工坊内容路径，获取content目录
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                # 如果找到了相关目录，添加到允许列表
                if workshop_related_dir and os.path.exists(workshop_related_dir):
                    real_workshop_dir = os.path.realpath(workshop_related_dir)
                    if real_workshop_dir not in allowed_dirs:
                        allowed_dirs.append(real_workshop_dir)
                        logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"动态添加创意工坊路径失败: {str(e)}")
        
        logger.info(f"当前允许的目录列表: {allowed_dirs}")

        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 替换可能的斜杠为反斜杠，确保Windows路径格式正确
            decoded_path = decoded_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]  # 移除多余的反斜杠前缀
        
        # 尝试解析路径
        final_path = None
        
        # 尝试作为绝对路径
        if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
            # 规范化路径以防止路径遍历攻击
            real_path = os.path.realpath(decoded_path)
            # 检查路径是否在允许的目录内
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
            # 对于以../static开头的相对路径，尝试直接从static目录解析
            if decoded_path.startswith('..\\static') or decoded_path.startswith('../static'):
                # 提取static后面的部分
                relative_part = decoded_path.split('static')[1]
                if relative_part.startswith(('\\', '/')):
                    relative_part = relative_part[1:]
                # 构建完整路径
                relative_path = os.path.join(allowed_dirs[0], relative_part)  # static目录
                if os.path.exists(relative_path) and os.path.isfile(relative_path):
                    real_path = os.path.realpath(relative_path)
                    if any(real_path.startswith(allowed_dir) for allowed_dir in allowed_dirs):
                        final_path = real_path
        
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                
                # 尝试将解码路径作为相对于创意工坊目录的路径
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    # 确保路径在允许的目录内
                    if real_path.startswith(workshop_base_dir):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        
        # 如果仍未找到有效路径，返回错误
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名是否为图片
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        # 读取图片文件
        with open(final_path, 'rb') as f:
            image_data = f.read()
        
        # 根据文件扩展名设置MIME类型
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
        
        # 返回图片数据
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)




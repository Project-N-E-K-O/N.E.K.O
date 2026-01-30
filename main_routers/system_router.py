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
import re
import time
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from openai import AsyncOpenAI
from openai import APIConnectionError, InternalServerError, RateLimitError
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import httpx

from .shared_state import get_steamworks, get_config_manager, get_sync_message_queue, get_session_manager
from config import get_extra_body, MEMORY_SERVER_PORT
from config.prompts_sys import emotion_analysis_prompt, proactive_chat_prompt, proactive_chat_prompt_screenshot, proactive_chat_prompt_window_search, proactive_chat_rewrite_prompt
from utils.workshop_utils import get_workshop_path
from utils.screenshot_utils import analyze_screenshot_from_data_url
from utils.language_utils import detect_language, translate_text, normalize_language_code

router = APIRouter(prefix="/api", tags=["system"])
logger = logging.getLogger("Main")


def _is_path_within_base(base_dir: str, candidate_path: str) -> bool:
    """
    Securely check if candidate_path is inside base_dir using os.path.commonpath.
    Both paths must be absolute and resolved (via os.path.realpath) before calling.
    Returns True if candidate_path is within base_dir, False otherwise.
    """
    try:
        # Normalize both paths for case-insensitivity on Windows
        norm_base = os.path.normcase(os.path.realpath(base_dir))
        norm_candidate = os.path.normcase(os.path.realpath(candidate_path))
        
        # os.path.commonpath raises ValueError if paths are on different drives (Windows)
        common = os.path.commonpath([norm_base, norm_candidate])
        return common == norm_base
    except (ValueError, TypeError):
        # Different drives or invalid paths
        return False

def _get_app_root():
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()
        
@router.post('/emotion/analysis')
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
            # Gemini 模型可能返回 markdown 格式，需要更多 token
            "max_completion_tokens": 40
        }
        
        # 只有在需要时才添加 extra_body
        extra_body = get_extra_body(model)
        if extra_body:
            request_params["extra_body"] = extra_body
        
        response = await client.chat.completions.create(**request_params)
        
        # 解析响应
        result_text = response.choices[0].message.content.strip()

        # 处理 markdown 代码块格式（Gemini 可能返回 ```json {...} ``` 格式）
        # 首先尝试使用正则表达式提取第一个代码块
        code_block_match = re.search(r"```(?:json)?\s*(.+?)\s*```", result_text, flags=re.S)
        if code_block_match:
            result_text = code_block_match.group(1).strip()
        elif result_text.startswith("```"):
            # 回退到原有的行分割逻辑
            lines = result_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # 移除第一行
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # 移除最后一行
            result_text = "\n".join(lines).strip()
        
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


@router.post('/steam/set-achievement-status/{name}')
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
                    return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
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
                        return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
                    else:
                        logger.error(f"设置成就失败: {name}，请确认成就ID在Steam后台已配置")
                        return JSONResponse(content={"success": False, "error": f"设置成就失败: {name}，请确认成就ID在Steam后台已配置"}, status_code=500)
            else:
                logger.info(f"成就已解锁，无需重复设置: {name}")
                return JSONResponse(content={"success": True, "message": f"成就 {name} 处理完成"})
        except Exception as e:
            logger.error(f"设置成就失败: {e}")
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)


@router.post('/steam/update-playtime')
async def update_playtime(request: Request):
    """更新游戏时长统计（PLAY_TIME_SECONDS）"""
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            data = await request.json()
            seconds_to_add = data.get('seconds', 10)

            # 验证 seconds 参数
            try:
                seconds_to_add = int(seconds_to_add)
                if seconds_to_add < 0:
                    return JSONResponse(
                        content={"success": False, "error": "seconds must be non-negative"},
                        status_code=400
                    )
            except (ValueError, TypeError):
                return JSONResponse(
                    content={"success": False, "error": "seconds must be a valid integer"},
                    status_code=400
                )

            # 请求当前统计数据
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(5):
                steamworks.run_callbacks()
                await asyncio.sleep(0.05)

            # 获取当前游戏时长（如果统计不存在，从 0 开始）
            try:
                current_playtime = steamworks.UserStats.GetStatInt('PLAY_TIME_SECONDS')
            except Exception as e:
                logger.warning(f"获取 PLAY_TIME_SECONDS 失败，从 0 开始: {e}")
                current_playtime = 0

            # 增加时长
            new_playtime = current_playtime + seconds_to_add

            # 设置新的时长
            try:
                result = steamworks.UserStats.SetStat('PLAY_TIME_SECONDS', new_playtime)

                if result:
                    # 存储统计数据
                    steamworks.UserStats.StoreStats()
                    steamworks.run_callbacks()

                    logger.debug(f"游戏时长已更新: {current_playtime}s -> {new_playtime}s (+{seconds_to_add}s)")

                    return JSONResponse(content={
                        "success": True,
                        "totalPlayTime": new_playtime,
                        "added": seconds_to_add
                    })
                else:
                    logger.warning("SetStat 返回 False - PLAY_TIME_SECONDS 统计可能未在 Steamworks 后台配置")
                    # 即使失败也返回成功，避免前端报错
                    return JSONResponse(content={
                        "success": True,
                        "totalPlayTime": new_playtime,
                        "added": seconds_to_add,
                        "warning": "Steam stat not configured"
                    })
            except Exception as stat_error:
                logger.warning(f"设置 Steam 统计失败: {stat_error} - 统计可能未在 Steamworks 后台配置")
                # 即使失败也返回成功，避免前端报错
                return JSONResponse(content={
                    "success": True,
                    "totalPlayTime": new_playtime,
                    "added": seconds_to_add,
                    "warning": "Steam stat not configured"
                })

        except Exception as e:
            logger.error(f"更新游戏时长失败: {e}")
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)


@router.get('/steam/list-achievements')
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


@router.get('/file-exists')
async def check_file_exists(path: str = None):
    """
    Check if a file exists at the given path.
    
    Security: Validates against path traversal attacks by:
    - URL-decoding the path
    - Normalizing the path (resolves . and ..)
    - Rejecting any path containing .. components (prevents escaping to parent dirs)
    - Using os.path.realpath to get the canonical path
    
    Note: This endpoint allows access to user Documents and Steam Workshop
    locations, so no whitelist restriction is applied.
    """
    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        
        # Windows路径处理 - normalize slashes
        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
        
        # Security: Reject path traversal attempts
        # Normalize first to catch encoded variants like %2e%2e
        normalized = os.path.normpath(decoded_path)
        
        # After normpath, check if path tries to escape via ..
        # Split and check each component to be thorough
        parts = normalized.split(os.sep)
        if '..' in parts:
            logger.warning(f"Rejected path traversal attempt in file-exists: {decoded_path}")
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # Resolve to canonical absolute path
        real_path = os.path.realpath(normalized)
        
        # Check if the file exists
        exists = os.path.exists(real_path) and os.path.isfile(real_path)
        
        return JSONResponse(content={"exists": exists})
        
    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)


@router.get('/find-first-image')
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
        
        # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
        is_allowed = any(_is_path_within_base(allowed_dir, real_folder) for allowed_dir in allowed_dirs)
        
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
                    
                    # 再次验证图片文件路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                    real_image_path = os.path.realpath(image_path)
                    if any(_is_path_within_base(allowed_dir, real_image_path) for allowed_dir in allowed_dirs):
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

@router.get('/steam/proxy-image')
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
                
                # 方法2：尝试从路径中提取创意工坊相关部分
                if not workshop_related_dir:
                    import re
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                # 方法3：如果是Steam创意工坊内容路径，获取content目录
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                # 方法4：如果是Steam创意工坊内容路径，添加整个steamapps/workshop目录
                if not workshop_related_dir:
                    import re
                    steamapps_match = re.search(r'(.*?steamapps)', decoded_path, re.IGNORECASE)
                    if steamapps_match:
                        workshop_related_dir = os.path.join(steamapps_match.group(1), 'workshop')
                
                # 如果找到了相关目录，添加到允许列表
                if workshop_related_dir:
                    # 确保目录存在
                    if os.path.exists(workshop_related_dir):
                        real_workshop_dir = os.path.realpath(workshop_related_dir)
                        if real_workshop_dir not in allowed_dirs:
                            allowed_dirs.append(real_workshop_dir)
                            logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
                    else:
                        # 如果目录不存在，尝试直接添加steamapps/workshop路径
                        import re
                        workshop_match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                        if workshop_match:
                            potential_dir = workshop_match.group(0)
                            if os.path.exists(potential_dir):
                                real_workshop_dir = os.path.realpath(potential_dir)
                                if real_workshop_dir not in allowed_dirs:
                                    allowed_dirs.append(real_workshop_dir)
                                    logger.info(f"动态添加允许的创意工坊目录: {real_workshop_dir}")
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
        
        # 特殊处理：如果路径包含steamapps/workshop，直接检查文件是否存在
        if ('steamapps\\workshop' in decoded_path.lower() or 'steamapps/workshop' in decoded_path.lower()):
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                final_path = decoded_path
                logger.info(f"直接允许访问创意工坊文件: {final_path}")
        
        # 尝试作为绝对路径
        if final_path is None:
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                # 规范化路径以防止路径遍历攻击
                real_path = os.path.realpath(decoded_path)
                # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                real_path = os.path.realpath(alt_path)
                # 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
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
                    # 使用 commonpath 防止前缀攻击
                    if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
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
                    # 确保路径在允许的目录内 - 使用 commonpath 防止前缀攻击
                    if _is_path_within_base(workshop_base_dir, real_path):
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
        
        # 检查文件大小是否超过50MB限制
        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        file_size = os.path.getsize(final_path)
        if file_size > MAX_IMAGE_SIZE:
            logger.warning(f"图片文件大小超过限制: {final_path} ({file_size / 1024 / 1024:.2f}MB > 50MB)")
            return JSONResponse(content={"success": False, "error": f"图片文件大小超过50MB限制 ({file_size / 1024 / 1024:.2f}MB)"}, status_code=413)
        
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

@router.post('/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：根据模式选择使用图片、首页推荐或窗口搜索，让AI决定是否主动发起对话"""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        from utils.web_scraper import fetch_trending_content, format_trending_content, fetch_window_context_content, format_window_context_content
        
        # 获取当前角色数据
        master_name_current, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse({"success": False, "error": f"角色 {lanlan_name} 不存在"}, status_code=404)
        
        # 检查是否正在响应中（如果正在说话，不打断）
        if mgr.is_active and hasattr(mgr.session, '_is_responding') and mgr.session._is_responding:
            return JSONResponse({
                "success": False, 
                "error": "AI正在响应中，无法主动搭话",
                "message": "请等待当前响应完成"
            }, status_code=409)
        
        logger.info(f"[{lanlan_name}] 开始主动搭话流程...")
        
        # 1. 检查前端是否发送了截图数据
        screenshot_data = data.get('screenshot_data')
        # 防御性检查：确保screenshot_data是字符串类型
        has_screenshot = bool(screenshot_data) and isinstance(screenshot_data, str)
        
        # 检查是否使用窗口搜索模式
        use_window_search = data.get('use_window_search', False)
        
        # 前端已经根据三种模式决定是否使用截图
        use_screenshot = has_screenshot and not use_window_search
        
        if use_window_search:
            logger.info(f"[{lanlan_name}] 前端选择使用窗口搜索进行主动搭话")
        elif use_screenshot:
            
            # 处理前端发送的截图数据
            try:
                # 将DataURL转换为base64数据并分析
                screenshot_content = await analyze_screenshot_from_data_url(screenshot_data)
                if not screenshot_content:
                    logger.warning(f"[{lanlan_name}] 截图分析失败，跳过本次搭话")
                    return JSONResponse({
                        "success": False,
                        "error": "截图分析失败，请检查截图格式是否正确",
                        "action": "pass"
                    }, status_code=500)
                else:
                    logger.info(f"[{lanlan_name}] 成功分析截图内容")
            except (ValueError, TypeError) as e:
                logger.exception(f"[{lanlan_name}] 处理截图数据失败")
                return JSONResponse({
                    "success": False,
                    "error": f"截图处理失败: {str(e)}",
                    "action": "pass"
                }, status_code=500)
        elif not use_window_search:
            logger.info(f"[{lanlan_name}] 前端选择使用首页推荐进行主动搭话")
        
        # 根据不同模式获取内容
        window_context_content = None
        formatted_content = None
        
        if use_window_search:
            # 窗口搜索主动对话
            try:
                window_context_content = await fetch_window_context_content(limit=5)
                
                if not window_context_content['success']:
                    logger.warning(f"[{lanlan_name}] 获取窗口上下文失败: {window_context_content.get('error')}")
                    # 窗口搜索失败时回退到首页推荐
                    logger.info(f"[{lanlan_name}] 回退到首页推荐模式")
                    use_window_search = False
                else:
                    formatted_window_content = format_window_context_content(window_context_content)
                    # 截断窗口标题以避免记录敏感信息
                    raw_title = window_context_content.get('window_title', '')
                    sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
                    
                    # 显示具体获取的搜索结果标题，使用更清晰的分隔
                    search_results = window_context_content.get('search_results', [])
                    if search_results:
                        result_titles = [result.get('title', '') for result in search_results]  # 显示全部搜索结果
                        if result_titles:
                            logger.info(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title}")
                            logger.info(f"搜索结果 (共{len(result_titles)}条):")
                            for title in result_titles:
                                logger.info(f"  - {title}")
                        else:
                            logger.info(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title} - 但未获取到搜索结果")
                    else:
                        logger.info(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title} - 但未获取到搜索结果")
                
            except Exception:
                logger.exception(f"[{lanlan_name}] 获取窗口上下文失败")
                # 回退到首页推荐
                use_window_search = False
        
        if not use_screenshot and not use_window_search:
            # 首页推荐主动对话
            try:
                trending_content = await fetch_trending_content(bilibili_limit=10, weibo_limit=10)
                
                if not trending_content['success']:
                    return JSONResponse({
                        "success": False,
                        "error": "无法获取首页推荐",
                        "detail": trending_content.get('error', '未知错误')
                    }, status_code=500)
                
                formatted_content = format_trending_content(trending_content)
                
                # 显示具体的首页推荐内容详情
                content_details = []
                
                bilibili_data = trending_content.get('bilibili', {})
                if bilibili_data.get('success'):
                    videos = bilibili_data.get('videos', [])
                    bilibili_titles = [video.get('title', '') for video in videos[:5]]  # 只显示前5个
                    if bilibili_titles:
                        content_details.append("B站视频:")
                        for title in bilibili_titles:
                            content_details.append(f"  - {title}")
                
                weibo_data = trending_content.get('weibo', {})
                if weibo_data.get('success'):
                    trending_list = weibo_data.get('trending', [])
                    weibo_words = [item.get('word', '') for item in trending_list[:5]]  # 只显示前5个
                    if weibo_words:
                        content_details.append("微博话题:")
                        for word in weibo_words:
                            content_details.append(f"  - {word}")
                
                if content_details:
                    logger.info(f"[{lanlan_name}] 成功获取首页推荐:")
                    for detail in content_details:
                        logger.info(detail)
                else:
                    logger.info(f"[{lanlan_name}] 成功获取首页推荐 - 但未获取到具体内容")
                
            except Exception:
                logger.exception(f"[{lanlan_name}] 获取首页推荐失败")
                return JSONResponse({
                    "success": False,
                    "error": "爬取首页推荐时出错",
                    "detail": "请检查网络连接或推荐服务状态"
                }, status_code=500)
        
        # 2. 获取new_dialogue prompt
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}", timeout=5.0)
                memory_context = resp.text
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
            memory_context = ""
        
        # 3. 构造提示词（根据选择使用不同的模板）
        if use_screenshot:
            # 截图模板：基于屏幕内容让AI决定是否主动发起对话
            system_prompt = proactive_chat_prompt_screenshot.format(
                lanlan_name=lanlan_name,
                master_name=master_name_current,
                screenshot_content=screenshot_content,
                memory_context=memory_context
            )
            logger.info(f"[{lanlan_name}] 使用图片进行主动对话")
        elif use_window_search:
            # 窗口搜索模板：基于当前活跃窗口和百度搜索结果让AI决定是否主动发起对话
            system_prompt = proactive_chat_prompt_window_search.format(
                lanlan_name=lanlan_name,
                master_name=master_name_current,
                window_context=formatted_window_content,
                memory_context=memory_context
            )
            logger.info(f"[{lanlan_name}] 使用窗口搜索进行主动对话")
        else:
            # 首页推荐模板：基于首页信息流让AI决定是否主动发起对话
            system_prompt = proactive_chat_prompt.format(
                lanlan_name=lanlan_name,
                master_name=master_name_current,
                trending_content=formatted_content,
                memory_context=memory_context
            )
            logger.info(f"[{lanlan_name}] 使用首页推荐进行主动对话")

        # 4. 直接使用langchain ChatOpenAI获取AI回复（不创建临时session）
        try:
            # 使用 get_model_api_config 获取 API 配置
            correction_config = _config_manager.get_model_api_config('correction')
            
            # 安全获取配置项，使用 .get() 避免 KeyError
            correction_model = correction_config.get('model')
            correction_base_url = correction_config.get('base_url')
            correction_api_key = correction_config.get('api_key')
            
            # 验证必需的配置项
            if not correction_model or not correction_api_key:
                logger.error("纠错模型配置缺失: model或api_key未设置")
                return JSONResponse({
                    "success": False,
                    "error": "纠错模型配置缺失",
                    "detail": "请在设置中配置纠错模型的model和api_key"
                }, status_code=500)
            
            llm = ChatOpenAI(
                model=correction_model,
                base_url=correction_base_url,
                api_key=correction_api_key,
                temperature=1.,
                max_completion_tokens=500,
                streaming=False,  # 不需要流式，直接获取完整响应
                extra_body=get_extra_body(correction_model)
            )
            
            # 发送请求获取AI决策 - Retry策略：重试2次，间隔1秒、2秒
            # 如需调试，可在此处使用 logger.debug 并适当截断 system_prompt
            # logger.debug(f"[{lanlan_name}] proactive system_prompt: {system_prompt[:200]}...")
            max_retries = 3
            retry_delays = [1, 2]
            response_text = ""
            
            for attempt in range(max_retries):
                try:
                    response = await asyncio.wait_for(
                        llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content="========请开始========")]),
                        timeout=10.0
                    )
                    response_text = response.content.strip()
                    break  # 成功则退出重试循环
                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    logger.info(f"[INFO] 捕获到 {type(e).__name__} 错误")
                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"[{lanlan_name}] 主动搭话LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                        # 向前端发送状态提示
                        if mgr.websocket:
                            try:
                                await mgr.send_status(f"正在重试中...（第{attempt + 1}次）")
                            except: # noqa
                                pass
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"[{lanlan_name}] 主动搭话LLM调用失败，已达到最大重试次数: {e}")
                        return JSONResponse({
                            "success": False,
                            "error": f"AI调用失败，已重试{max_retries}次",
                            "detail": str(e)
                        }, status_code=503)
            
            logger.info(f"[{lanlan_name}] AI决策结果: {response_text[:100]}...")

            # --- 新增机制：用正则表达式寻找最后一个"主动搭话"后接换行的地方 ---
            match = re.search(r'主动搭话\s*\n', response_text)
            if match:
                # 从最后一个"主动搭话\n"之后截取内容
                # 使用 finditer 来找到所有匹配，取最后一个
                matches = list(re.finditer(r'主动搭话\s*\n', response_text))
                if matches:
                    last_match = matches[-1]
                    response_text = response_text[last_match.end():].strip()
                    logger.info(f"[{lanlan_name}] 截取'主动搭话'后的内容: {response_text[:50]}...")

            # 5. 判断AI是否选择搭话
            if "[PASS]" in response_text:
                return JSONResponse({
                    "success": True,
                    "action": "pass",
                    "message": "AI选择暂时不搭话"
                })

            # --- 新增验证：在继续输出前严格执行响应内容规则 ---
            # 1) 字数限制：按150英文词（空格拆分）或中文字来计算，超过则放弃输出
            text_length = 200
            try:
                # 计算混合长度：中文字符计1，英文单词计1
                def count_words_and_chars(text):
                    count = 0
                    # 用正则分离中文字符和英文单词
                    # 匹配中文字符
                    chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
                    count += len(chinese_chars)
                    # 移除中文字符后，按空格拆分计算英文单词
                    text_without_chinese = re.sub(r'[\u4e00-\u9fff]', ' ', text)
                    english_words = [w for w in text_without_chinese.split() if w.strip()]
                    count += len(english_words)
                    return count
                
                text_length = count_words_and_chars(response_text)
            except Exception:
                logger.exception(f"[{lanlan_name}] 在检查回复长度时发生错误")

            if text_length > 100 or response_text.find("|") != -1 or response_text.find("｜") != -1:
                            # --- 使用改写模型清洁输出 ---
                try:
                    # 使用相同的correction模型进行改写
                    rewrite_llm = ChatOpenAI(
                        model=correction_model,
                        base_url=correction_base_url,
                        api_key=correction_api_key,
                        temperature=0.3,  # 降低温度以获得更稳定的改写结果
                        max_completion_tokens=500,
                        streaming=False,
                        extra_body=get_extra_body(correction_model)
                    )
                    
                    # 构造改写提示
                    rewrite_prompt = proactive_chat_rewrite_prompt.format(raw_output=response_text)
                    
                    # 调用改写模型
                    rewrite_response = await asyncio.wait_for(
                        rewrite_llm.ainvoke([SystemMessage(content=rewrite_prompt), HumanMessage(content="========请开始========")]),
                        timeout=6.0
                    )
                    response_text = rewrite_response.content.strip()
                    logger.debug(f"[{lanlan_name}] 改写后内容: {response_text[:100]}...")

                    if "主动搭话" in response_text or '|' in response_text or "｜" in response_text or '[PASS]' in response_text or count_words_and_chars(response_text) > 100:
                        logger.warning(f"[{lanlan_name}] AI回复经二次改写后仍失败，放弃主动搭话。")
                        return JSONResponse({
                            "success": True,
                            "action": "pass",
                            "message": "AI回复改写失败，已放弃输出"
                        })

                except Exception as e:
                    logger.warning(f"[{lanlan_name}] 改写模型调用失败，错误提示: {e}")
                    return JSONResponse({
                        "success": True,
                        "action": "pass",
                        "message": "AI回复改写失败，已放弃输出"
                    })
            
            # 6. AI选择搭话，需要通过session manager处理
            # 首先检查是否有真实的websocket连接
            if not mgr.websocket:
                return JSONResponse({
                    "success": False,
                    "error": "没有活跃的WebSocket连接，无法主动搭话。请先打开前端页面。"
                }, status_code=400)
            
            # 检查websocket是否连接
            try:
                from starlette.websockets import WebSocketState
                if hasattr(mgr.websocket, 'client_state'):
                    if mgr.websocket.client_state != WebSocketState.CONNECTED:
                        return JSONResponse({
                            "success": False,
                            "error": "WebSocket未连接，无法主动搭话"
                        }, status_code=400)
            except Exception as e:
                logger.warning(f"检查WebSocket状态失败: {e}")
            
            # 检查是否有现有的session，如果没有则创建一个文本session
            session_created = False
            if not mgr.session or not hasattr(mgr.session, '_conversation_history'):
                logger.info(f"[{lanlan_name}] 没有活跃session，创建文本session用于主动搭话")
                # 使用现有的真实websocket启动session
                await mgr.start_session(mgr.websocket, new=True, input_mode='text')
                session_created = True
                logger.info(f"[{lanlan_name}] 文本session已创建")
            
            # 如果是新创建的session，等待TTS准备好
            if session_created and mgr.use_tts:
                logger.info(f"[{lanlan_name}] 等待TTS准备...")
                max_wait = 5  # 最多等待5秒
                wait_step = 0.1
                waited = 0
                while waited < max_wait:
                    async with mgr.tts_cache_lock:
                        if mgr.tts_ready:
                            logger.info(f"[{lanlan_name}] TTS已准备好")
                            break
                    await asyncio.sleep(wait_step)
                    waited += wait_step
                
                if waited >= max_wait:
                    logger.warning(f"[{lanlan_name}] TTS准备超时，继续发送（可能没有语音）")
            
            # 现在可以将AI的话添加到对话历史中
            from langchain_core.messages import AIMessage
            mgr.session._conversation_history.append(AIMessage(content=response_text))
            logger.info(f"[{lanlan_name}] 已将主动搭话添加到对话历史")
            
            # 生成新的speech_id（用于TTS）
            from uuid import uuid4
            async with mgr.lock:
                mgr.current_speech_id = str(uuid4())
            
            # 检查最近30秒内是否有用户活动（语音输入或文本输入）
            # 如果有，则放弃本次主动搭话
            if mgr.last_user_activity_time is not None:
                time_since_last_activity = time.time() - mgr.last_user_activity_time
                if time_since_last_activity < 30:
                    logger.info(f"[{lanlan_name}] 检测到最近 {time_since_last_activity:.1f} 秒内有用户活动，放弃主动搭话")
                    return JSONResponse({
                        "success": True,
                        "action": "pass",
                        "message": f"最近{time_since_last_activity:.1f}秒内有用户活动，放弃主动搭话"
                    })
            
            # 记录开始输出的时间戳，用于检测输出过程中是否有新的用户输入
            output_start_time = time.time()
            
            # 通过handle_text_data处理这段话（触发TTS和前端显示）
            # 分chunk发送以模拟流式效果
            chunks = [response_text[i:i+10] for i in range(0, len(response_text), 10)]
            for i, chunk in enumerate(chunks):
                # 检查输出过程中是否有新的用户输入
                if mgr.last_user_activity_time is not None and mgr.last_user_activity_time > output_start_time:
                    logger.info(f"[{lanlan_name}] 输出过程中检测到用户活动，停止主动搭话输出 (已输出 {i}/{len(chunks)} chunks)")
                    # 调用新消息处理来清空TTS队列
                    await mgr.handle_new_message() # 这里的处理并不严谨，默认了用户输入时不会立即触发其他TTS，没有进行状态锁
                    return JSONResponse({
                        "success": True,
                        "action": "interrupted",
                        "message": "输出过程中检测到用户活动，已停止"
                    })
                
                await mgr.handle_text_data(chunk, is_first_chunk=(i == 0))
                await asyncio.sleep(0.15)  # 小延迟模拟流式
            
            # 发送TTS结束信号，触发TTS的commit（对于Qwen TTS的server_commit模式尤为重要）
            if mgr.use_tts and mgr.tts_thread and mgr.tts_thread.is_alive():
                try:
                    mgr.tts_request_queue.put((None, None))
                except Exception as e:
                    logger.warning(f"[{lanlan_name}] 发送TTS结束信号失败: {e}")
            
            # 发送turn end信号（不调用handle_response_complete以避免触发热重置）
            mgr.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            try:
                if mgr.websocket and hasattr(mgr.websocket, 'client_state') and mgr.websocket.client_state == mgr.websocket.client_state.CONNECTED:
                    await mgr.websocket.send_json({'type': 'system', 'data': 'turn end'})
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 发送turn end失败: {e}")
            
            return JSONResponse({
                "success": True,
                "action": "chat",
                "message": "主动搭话已发送",
                "lanlan_name": lanlan_name
            })
            
        except asyncio.TimeoutError:
            logger.error(f"[{lanlan_name}] AI回复超时")
            return JSONResponse({
                "success": False,
                "error": "AI处理超时"
            }, status_code=504)
        except Exception as e:
            logger.error(f"[{lanlan_name}] AI处理失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "AI处理失败",
                "detail": str(e)
            }, status_code=500)
        
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)


@router.post('/translate')
async def translate_text_api(request: Request):
    """
    翻译文本API（供前端字幕模块使用）
    
    请求格式:
    {
        "text": "要翻译的文本",
        "target_lang": "目标语言代码 ('zh', 'en', 'ja')",
        "source_lang": "源语言代码 (可选，为null时自动检测)"
    }
    
    响应格式:
    {
        "success": true/false,
        "translated_text": "翻译后的文本",
        "source_lang": "检测到的源语言代码",
        "target_lang": "目标语言代码"
    }
    """
    try:
        data = await request.json()
        text = data.get('text', '').strip()
        target_lang = data.get('target_lang', 'zh')
        source_lang = data.get('source_lang')
        
        if not text:
            return {
                "success": False,
                "error": "文本不能为空",
                "translated_text": "",
                "source_lang": "unknown",
                "target_lang": target_lang
            }
        
        # 归一化目标语言代码（复用公共函数）
        target_lang_normalized = normalize_language_code(target_lang, format='short')
        
        # 检测源语言（如果未提供）
        if source_lang is None:
            detected_source_lang = detect_language(text)
        else:
            # 归一化源语言代码（复用公共函数）
            detected_source_lang = normalize_language_code(source_lang, format='short')
        
        # 如果源语言和目标语言相同，不需要翻译
        if detected_source_lang == target_lang_normalized or detected_source_lang == 'unknown':
            return {
                "success": True,
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
        
        # 检查是否跳过 Google 翻译（前端传递的会话级失败标记）
        skip_google = data.get('skip_google', False)
        
        # 调用翻译服务
        try:
            translated, google_failed = await translate_text(
                text, 
                target_lang_normalized, 
                detected_source_lang,
                skip_google=skip_google
            )
            return {
                "success": True,
                "translated_text": translated,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized,
                "google_failed": google_failed  # 告诉前端 Google 翻译是否失败
            }
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            # 翻译失败时返回原文
            return {
                "success": False,
                "error": str(e),
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
            
    except Exception as e:
        logger.error(f"翻译API处理失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "translated_text": "",
            "source_lang": "unknown",
            "target_lang": "zh"
        }



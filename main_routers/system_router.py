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
from collections import deque
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
from config.prompts_sys import emotion_analysis_prompt, get_proactive_chat_prompt, get_proactive_chat_rewrite_prompt
from utils.workshop_utils import get_workshop_path
from utils.screenshot_utils import analyze_screenshot_from_data_url
from utils.language_utils import detect_language, translate_text, normalize_language_code, get_global_language
from utils.frontend_utils import count_words_and_chars

router = APIRouter(prefix="/api", tags=["system"])
logger = logging.getLogger("Main")

# --- 主动搭话近期记录暂存区 ---
# {lanlan_name: deque([(timestamp, message), ...], maxlen=10)}
_proactive_chat_history: dict[str, deque] = {}

_RECENT_CHAT_MAX_AGE_SECONDS = 3600  # 1小时内的搭话记录


def _extract_links_from_raw(mode: str, raw_data: dict) -> list[dict]:
    """从原始 web 数据中提取链接信息列表"""
    links = []
    try:
        if mode == 'news':
            # 微博 / Twitter
            news = raw_data.get('news', {})
            items = news.get('trending', [])
            for item in items[:5]:
                title = item.get('word', '') or item.get('name', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': '微博' if raw_data.get('region', 'china') == 'china' else 'Twitter'})
        
        elif mode == 'video':
            # B站 / Reddit
            video = raw_data.get('video', {})
            items = video.get('videos', []) or video.get('posts', [])
            for item in items[:5]:
                title = item.get('title', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': 'B站' if raw_data.get('region', 'china') == 'china' else 'Reddit'})
        
        elif mode == 'home':
            # 合并首页：bilibili + weibo 或 reddit + twitter
            bilibili = raw_data.get('bilibili', {})
            for v in (bilibili.get('videos', []) or [])[:3]:
                if v.get('title') and v.get('url'):
                    links.append({'title': v['title'], 'url': v['url'], 'source': 'B站'})
            
            weibo = raw_data.get('weibo', {})
            for w in (weibo.get('trending', []) or [])[:3]:
                if w.get('word') and w.get('url'):
                    links.append({'title': w['word'], 'url': w['url'], 'source': '微博'})
            
            reddit = raw_data.get('reddit', {})
            for r in (reddit.get('posts', []) or [])[:3]:
                if r.get('title') and r.get('url'):
                    links.append({'title': r['title'], 'url': r['url'], 'source': 'Reddit'})
            
            twitter = raw_data.get('twitter', {})
            for t in (twitter.get('trending', []) or [])[:3]:
                title = t.get('name', '') or t.get('word', '')
                if title and t.get('url'):
                    links.append({'title': title, 'url': t['url'], 'source': 'Twitter'})
    except Exception as e:
        logger.warning(f"提取链接失败 [{mode}]: {e}")
    return links


def _parse_web_screening_result(text: str) -> dict | None:
    """
    解析 Phase 1 Web 筛选 LLM 的结构化结果，提取链接信息。
    期望格式：
      话题：xxx / Topic: xxx
      来源：xxx / Source: xxx
      链接：xxx / Link: xxx
      简述：xxx / Summary: xxx
    """
    result = {}
    # 多语言 key 匹配
    patterns = {
        'title': r'(?:话题|Topic|話題|주제)\s*[：:]\s*(.+)',
        'source': r'(?:来源|Source|出典|출처)\s*[：:]\s*(.+)',
        'url': r'(?:链接|Link|リンク|링크)\s*[：:]\s*(https?://\S+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result[key] = match.group(1).strip()
    
    if result.get('url'):
        return result
    return None


def _format_recent_proactive_chats(lanlan_name: str, lang: str = 'zh') -> str:
    """将近期搭话记录格式化为可注入prompt的文本段"""
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return ""
    now = time.time()
    recent = [(ts, msg) for ts, msg in history if now - ts < _RECENT_CHAT_MAX_AGE_SECONDS]
    if not recent:
        return ""
    
    headers = {
        'zh': '======以下是你近期的主动搭话记录（不要重复这些内容）======',
        'en': '======Your recent proactive chats (do NOT repeat these)======',
        'ja': '======あなたの最近の自発的発言記録（繰り返さないでください）======',
        'ko': '======최근 주도적 대화 기록 (이 내용을 반복하지 마세요)======',
    }
    footers = {
        'zh': '======以上为近期搭话记录======',
        'en': '======End recent proactive chats======',
        'ja': '======以上、最近の発言記録======',
        'ko': '======이상 최근 대화 기록======',
    }
    
    header = headers.get(lang, headers['zh'])
    footer = footers.get(lang, footers['zh'])
    lines = [f"- {msg}" for _, msg in recent]
    return f"\n{header}\n" + "\n".join(lines) + f"\n{footer}\n"


def _record_proactive_chat(lanlan_name: str, message: str):
    """记录一次成功的主动搭话"""
    if lanlan_name not in _proactive_chat_history:
        _proactive_chat_history[lanlan_name] = deque(maxlen=10)
    _proactive_chat_history[lanlan_name].append((time.time(), message))


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


def _log_news_content(lanlan_name: str, news_content: dict):
    """记录新闻内容获取详情"""
    region = news_content.get('region', 'china')
    news_data = news_content.get('news', {})
    if news_data.get('success'):
        trending_list = news_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            source = "微博热议话题" if region == 'china' else "Twitter热门话题"
            logger.info(f"[{lanlan_name}] 成功获取{source}:")
            for word in words:
                logger.info(f"  - {word}")


def _log_video_content(lanlan_name: str, video_content: dict):
    """记录视频内容获取详情"""
    region = video_content.get('region', 'china')
    video_data = video_content.get('video', {})
    if video_data.get('success'):
        if region == 'china':
            videos = video_data.get('videos', [])
            titles = [video.get('title', '') for video in videos[:5]]
            if titles:
                logger.info(f"[{lanlan_name}] 成功获取B站视频:")
                for title in titles:
                    logger.info(f"  - {title}")
        else:
            posts = video_data.get('posts', [])
            titles = [post.get('title', '') for post in posts[:5]]
            if titles:
                logger.info(f"[{lanlan_name}] 成功获取Reddit热门帖子:")
                for title in titles:
                    logger.info(f"  - {title}")


def _log_trending_content(lanlan_name: str, trending_content: dict):
    """记录首页推荐内容获取详情"""
    content_details = []
    
    bilibili_data = trending_content.get('bilibili', {})
    if bilibili_data.get('success'):
        videos = bilibili_data.get('videos', [])
        titles = [video.get('title', '') for video in videos[:5]]
        if titles:
            content_details.append("B站视频:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    weibo_data = trending_content.get('weibo', {})
    if weibo_data.get('success'):
        trending_list = weibo_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("微博话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    reddit_data = trending_content.get('reddit', {})
    if reddit_data.get('success'):
        posts = reddit_data.get('posts', [])
        titles = [post.get('title', '') for post in posts[:5]]
        if titles:
            content_details.append("Reddit热门帖子:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    twitter_data = trending_content.get('twitter', {})
    if twitter_data.get('success'):
        trending_list = twitter_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("Twitter热门话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    if content_details:
        logger.info(f"[{lanlan_name}] 成功获取首页推荐:")
        for detail in content_details:
            logger.info(detail)
    else:
        logger.info(f"[{lanlan_name}] 成功获取首页推荐 - 但未获取到具体内容")

def _log_personal_dynamics(lanlan_name: str, personal_content: dict):
    """记录个人动态内容获取详情"""
    content_details = []
    
    bilibili_dynamic = personal_content.get('bilibili_dynamic', {})
    if bilibili_dynamic.get('success'):
        dynamics = bilibili_dynamic.get('dynamics', [])
        bilibili_contents = [dynamic.get('content', dynamic.get('title', '')) for dynamic in dynamics[:5]]
        if bilibili_contents:
            content_details.append("B站动态:")
            for content in bilibili_contents:
                content_details.append(f"  - {content}")
    
    weibo_dynamic = personal_content.get('weibo_dynamic', {})
    if weibo_dynamic.get('success'):
        dynamics = weibo_dynamic.get('statuses', [])
        weibo_contents = [dynamic.get('content', '') for dynamic in dynamics[:5]]
        if weibo_contents:
            content_details.append("微博动态:")
            for content in weibo_contents:
                content_details.append(f"  - {content}")
                
    if content_details:
        logger.info(f"[{lanlan_name}] 成功获取个人动态:")
        for detail in content_details:
            logger.info(detail)
    else:
        logger.info(f"[{lanlan_name}] 成功获取个人动态 - 但未获取到具体内容")

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

            # 注意:不需要每次都调用 RequestCurrentStats()
            # RequestCurrentStats() 应该只在应用启动时调用一次
            # 频繁调用可能导致性能问题和同步延迟
            # 这里直接获取和更新统计值即可

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
                    logger.debug("SetStat 返回 False - PLAY_TIME_SECONDS 统计可能未在 Steamworks 后台配置")
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

@router.get('/get_window_title')
async def get_window_title_api():
    """获取当前活跃窗口标题（仅支持Windows）"""
    try:
        from utils.web_scraper import get_active_window_title
        title = get_active_window_title()
        if title:
            return JSONResponse({"success": True, "window_title": title})
        return JSONResponse({"success": False, "window_title": None})
    except Exception as e:
        logger.error(f"获取窗口标题失败: {e}")
        return JSONResponse({"success": False, "window_title": None})


@router.post('/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：两阶段架构 — Phase 1 筛选话题（max 2 并发 LLM），Phase 2 结合人设生成搭话"""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        from utils.web_scraper import (
            fetch_window_context_content, format_window_context_content,
            fetch_video_content, format_video_content,
            fetch_news_content, format_news_content,
            fetch_personal_dynamics, format_personal_dynamics
        )
        from config.prompts_sys import get_proactive_screen_prompt, get_proactive_generate_prompt, get_proactive_chat_rewrite_prompt
        # 获取当前角色数据（包括完整人设）
        master_name_current, her_name_current, _, _, _, lanlan_prompt_map, _, _, _, _ = _config_manager.get_character_data()
        
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
        
        logger.info(f"[{lanlan_name}] 开始主动搭话流程（两阶段架构）...")
        
        # ========== 解析 enabled_modes ==========
        enabled_modes = data.get('enabled_modes', [])
        # 兼容旧版前端
        if not enabled_modes:
            content_type = data.get('content_type', None)
            screenshot_data = data.get('screenshot_data')
            if screenshot_data and isinstance(screenshot_data, str):
                enabled_modes = ['vision']
            elif data.get('use_window_search', False):
                enabled_modes = ['window']
            elif content_type == 'news':
                enabled_modes = ['news']
            elif content_type == 'video':
                enabled_modes = ['video']
            elif data.get('use_personal_dynamic', False):
                enabled_modes = ['personal']
            else:
                enabled_modes = ['home']
        
        logger.info(f"[{lanlan_name}] 启用的搭话模式: {enabled_modes}")
        
        # ========== 0. 并行获取所有信息源内容（无 LLM） ==========
        screenshot_data = data.get('screenshot_data')
        has_screenshot = bool(screenshot_data) and isinstance(screenshot_data, str)
        
        async def _fetch_source(mode: str) -> tuple:
            """获取单个信息源，返回 (mode, content_dict) 或抛出异常"""
            if mode == 'vision':
                if not has_screenshot:
                    raise ValueError("无截图数据")
                screenshot_content = await analyze_screenshot_from_data_url(screenshot_data)
                if not screenshot_content:
                    raise ValueError("截图分析失败")
                logger.info(f"[{lanlan_name}] 成功分析截图内容")
                window_title = data.get('window_title', '')
                return (mode, {'screenshot_content': screenshot_content, 'window_title': window_title})
            
            elif mode == 'news':
                news_content = await fetch_news_content(limit=10)
                if not news_content['success']:
                    raise ValueError(f"获取新闻失败: {news_content.get('error')}")
                formatted = format_news_content(news_content)
                _log_news_content(lanlan_name, news_content)
                # 提取链接信息
                links = _extract_links_from_raw(mode, news_content)
                return (mode, {'formatted_content': formatted, 'raw_data': news_content, 'links': links})
            
            elif mode == 'video':
                video_content = await fetch_video_content(limit=10)
                if not video_content['success']:
                    raise ValueError(f"获取视频失败: {video_content.get('error')}")
                formatted = format_video_content(video_content)
                _log_video_content(lanlan_name, video_content)
                links = _extract_links_from_raw(mode, video_content)
                return (mode, {'formatted_content': formatted, 'raw_data': video_content, 'links': links})
            
            elif mode == 'window':
                window_context_content = await fetch_window_context_content(limit=5)
                if not window_context_content['success']:
                    raise ValueError(f"获取窗口上下文失败: {window_context_content.get('error')}")
                formatted = format_window_context_content(window_context_content)
                raw_title = window_context_content.get('window_title', '')
                sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
                logger.info(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title}")
                return (mode, {'formatted_content': formatted, 'raw_data': window_context_content, 'links': []})
            
            elif mode == 'home':
                trending_content = await fetch_trending_content(bilibili_limit=10, weibo_limit=10)
                if not trending_content['success']:
                    raise ValueError(f"获取首页推荐失败: {trending_content.get('error')}")
                formatted = format_trending_content(trending_content)
                _log_trending_content(lanlan_name, trending_content)
                links = _extract_links_from_raw(mode, trending_content)
                return (mode, {'formatted_content': formatted, 'raw_data': trending_content, 'links': links})

            elif mode == 'personal':
                personal_dynamics = await fetch_personal_dynamics(limit=10)
                if not personal_dynamics['success']:
                    raise ValueError(f"获取个人动态失败: {personal_dynamics.get('error')}")
                formatted = format_personal_dynamics(personal_dynamics)
                _log_personal_dynamics(lanlan_name, personal_dynamics)
                links = _extract_links_from_raw(mode, personal_dynamics)
                return (mode, {'formatted_content': formatted, 'raw_data': personal_dynamics, 'links': links})
            
            else:
                raise ValueError(f"未知模式: {mode}")
        
        # 并行获取所有信息源
        fetch_tasks = [_fetch_source(m) for m in enabled_modes]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # 收集成功的信息源
        sources: dict[str, dict] = {}
        for result in fetch_results:
            if isinstance(result, Exception):
                logger.warning(f"[{lanlan_name}] 信息源获取失败: {result}")
                continue
            mode, content = result
            sources[mode] = content
        
        if not sources:
            return JSONResponse({
                "success": False,
                "error": "所有信息源获取失败",
                "action": "pass"
            }, status_code=500)
        
        logger.info(f"[{lanlan_name}] 成功获取 {len(sources)} 个信息源: {list(sources.keys())}")

        # ========== 1. 获取记忆上下文 (New Dialog) ==========
        # new_dialog 返回格式：
        # ========以下是{name}的内心活动========
        # {内心活动/Settings}...
        # 现在时间...整理了近期发生的事情。
        # Name | Content
        # ...
        
        raw_memory_context = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}", timeout=5.0)
                resp.raise_for_status()  # Check for HTTP errors explicitly
                if resp.status_code == 200:
                    raw_memory_context = resp.text
                else:
                    logger.warning(f"[{lanlan_name}] 记忆服务返回非200状态: {resp.status_code}，使用空上下文")
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
        
        # 解析 new_dialog 响应
        def _parse_new_dialog(text: str) -> tuple[str, str]:
            if not text:
                return "", ""
            # 尝试找到分割线 "整理了近期发生的事情"
            split_keyword = "整理了近期发生的事情"
            if split_keyword in text:
                parts = text.split(split_keyword, 1)
                # part[0] 是内心活动+时间，part[1] 是对话历史
                # 提取内心活动 (去除首尾空白)
                inner_thoughts_part = parts[0].strip()
                # 提取对话历史 (去除首尾空白)
                history_part = parts[1].strip()
                return history_part, inner_thoughts_part
            return text, ""

        memory_context, inner_thoughts = _parse_new_dialog(raw_memory_context)
        
        # ========== 2. 选择语言 ==========
        try:
            request_lang = data.get('language') or data.get('lang') or data.get('i18n_language')
            if request_lang:
                proactive_lang = normalize_language_code(request_lang, format='short')
            else:
                proactive_lang = get_global_language()
        except Exception:
            proactive_lang = 'zh'
        
        # ========== 3. 注入近期搭话记录 ==========
        proactive_chat_history_prompt = _format_recent_proactive_chats(lanlan_name, proactive_lang)

        # ========== 4. 获取 LLM 配置 ==========
        try:
            correction_config = _config_manager.get_model_api_config('correction')
            correction_model = correction_config.get('model')
            correction_base_url = correction_config.get('base_url')
            correction_api_key = correction_config.get('api_key')
            
            if not correction_model or not correction_api_key:
                logger.error("纠错模型配置缺失: model或api_key未设置")
                return JSONResponse({
                    "success": False,
                    "error": "纠错模型配置缺失",
                    "detail": "请在设置中配置纠错模型的model和api_key"
                }, status_code=500)
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "模型配置异常",
                "detail": str(e)
            }, status_code=500)
        
        def _make_llm(temperature: float = 1.0, max_tokens: int = 500):
            return ChatOpenAI(
                model=correction_model,
                base_url=correction_base_url,
                api_key=correction_api_key,
                temperature=temperature,
                max_completion_tokens=max_tokens,
                streaming=False,
                extra_body=get_extra_body(correction_model)
            )
        
        async def _llm_call_with_retry(system_prompt: str, label: str, temperature: float = 1.0, max_tokens: int = 500, timeout: float = 10.0) -> str:
            """带重试的 LLM 调用，返回 response_text"""
            llm = _make_llm(temperature=temperature, max_tokens=max_tokens)
            max_retries = 3
            retry_delays = [1, 2]
            for attempt in range(max_retries):
                try:
                    response = await asyncio.wait_for(
                        llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content="========请开始========")]),
                        timeout=timeout
                    )
                    return response.content.strip()
                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[{lanlan_name}] LLM [{label}] 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(retry_delays[attempt])
                    else:
                        logger.error(f"[{lanlan_name}] LLM [{label}] 调用失败，已达最大重试: {e}")
                        raise
            raise RuntimeError("Unexpected")
        
        # ================================================================
        # Phase 1: 筛选话题
        # - 视觉通道: 不调用 LLM，直接使用截图描述作为 topic
        # - Web 通道: 合并所有文本源（含 URL）→ 1 次 LLM 筛选
        # 总计最多 1 次 LLM 调用
        # ================================================================
        
        vision_content = sources.get('vision')  # 可能为 None
        web_modes = [m for m in sources if m != 'vision']
        
        # 构建带有 URL 的合并 Web 内容
        merged_web_content = ""
        all_web_links: list[dict] = []  # 收集所有 web 源的链接
        if web_modes:
            parts = []
            for m in web_modes:
                src = sources[m]
                label_map = {'news': '热议话题', 'video': '视频推荐', 'home': '首页推荐', 'window': '窗口上下文', 'personal': '个人动态'}
                label = label_map.get(m, m)
                content_text = src.get('formatted_content', '')
                if content_text:
                    # 在格式化文本后附加链接信息
                    links = src.get('links', [])
                    all_web_links.extend(links)
                    link_appendix = ""
                    if links:
                        link_lines = [f"  链接{i+1}: {lk.get('title','')} → {lk.get('url','')}" for i, lk in enumerate(links[:5])]
                        link_appendix = "\n" + "\n".join(link_lines)
                    parts.append(f"--- {label} ---\n{content_text}{link_appendix}")
            merged_web_content = "\n\n".join(parts)
        
        # Phase 1 结果收集
        phase1_topics: list[tuple[str, str]] = []  # [(channel, topic_summary), ...]
        source_links: list[dict] = []  # [{"title": ..., "url": ..., "source": ...}]
        
        # --- 视觉通道: 直接使用截图描述 (无 LLM) ---
        if vision_content:
            screenshot_desc = vision_content['screenshot_content']
            window_title = vision_content.get('window_title', '')
            if window_title:
                topic = f"[窗口: {window_title}]\n{screenshot_desc}"
            else:
                topic = screenshot_desc
            phase1_topics.append(('vision', topic))
            logger.info(f"[{lanlan_name}] Phase 1 视觉通道: 直接使用截图描述 ({len(screenshot_desc)} 字)")
        
        # --- Web 通道: 1 次 LLM 筛选 ---
        if merged_web_content:
            try:
                prompt = get_proactive_screen_prompt('web', proactive_lang).format(
                    memory_context=memory_context + "\n" + proactive_chat_history_prompt,
                    merged_content=merged_web_content
                )
                web_result_text = await _llm_call_with_retry(prompt, "screen_web")
                logger.info(f"[{lanlan_name}] Phase 1 Web 筛选结果: {web_result_text[:120]}")
                
                if "[PASS]" not in web_result_text:
                    # 解析结构化结果提取链接
                    parsed_link = _parse_web_screening_result(web_result_text)
                    if parsed_link:
                        source_links.append(parsed_link)
                    phase1_topics.append(('web', web_result_text.strip()))
                else:
                    logger.info(f"[{lanlan_name}] Phase 1 Web 通道返回 PASS")
            except Exception as e:
                logger.warning(f"[{lanlan_name}] Phase 1 Web 筛选异常: {e}")
        
        if not phase1_topics:
            logger.info(f"[{lanlan_name}] Phase 1 所有通道均无可用话题")
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "所有信息源筛选后均不值得搭话"
            })
        
        # 选择最佳话题（vision 优先）
        best_channel, best_topic = phase1_topics[0]
        for channel, topic in phase1_topics:
            if channel == 'vision':
                best_channel, best_topic = channel, topic
                break
        
        logger.info(f"[{lanlan_name}] Phase 1 最终选择 [{best_channel}] 话题: {best_topic[:80]}")
        
        # ================================================================
        # Phase 2: 结合人设生成搭话 — 1 次 LLM 调用
        # ================================================================
        
        # 获取角色完整人设
        character_prompt = lanlan_prompt_map.get(lanlan_name, '')
        if not character_prompt:
            logger.warning(f"[{lanlan_name}] 未找到角色人设，使用空字符串")
        
        generate_prompt = get_proactive_generate_prompt(proactive_lang).format(
            character_prompt=character_prompt,
            inner_thoughts=inner_thoughts,
            memory_context=memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            topic_summary=best_topic,
            master_name=master_name_current
        )
        
        response_text = await _llm_call_with_retry(generate_prompt, "generate", temperature=1.0, max_tokens=500)
        logger.info(f"[{lanlan_name}] Phase 2 生成结果: {response_text[:100]}...")
        
        # 清理 "主动搭话" 标记
        matches = list(re.finditer(r'主动搭话\s*\n', response_text))
        if matches:
            response_text = response_text[matches[-1].end():].strip()
        
        # 检查 PASS
        if "[PASS]" in response_text:
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "Phase 2 AI选择不搭话"
            })
        
        # ========== 后处理（改写检查） ==========
        text_length = 200
        try:
            text_length = count_words_and_chars(response_text)
        except Exception:
            logger.exception(f"[{lanlan_name}] 在检查回复长度时发生错误")

        if text_length > 100 or response_text.find("|") != -1 or response_text.find("｜") != -1:
            try:
                rewrite_prompt = get_proactive_chat_rewrite_prompt(proactive_lang).format(raw_output=response_text)
                response_text = await _llm_call_with_retry(rewrite_prompt, "rewrite", temperature=0.3, max_tokens=500, timeout=6.0)
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

        # 6. 投递：通过 LLMSessionManager.deliver_text_proactively 统一处理
        delivered = await mgr.deliver_text_proactively(response_text, min_idle_secs=30.0)

        if not delivered:
            # deliver_text_proactively 内部已 log 具体原因
            return JSONResponse({
                "success": True,
                "action": "pass",
                "message": "主动搭话条件未满足（用户近期活跃或语音会话正在进行）"
            })

        # 记录主动搭话（成功投递后）
        _record_proactive_chat(lanlan_name, response_text)

        return JSONResponse({
            "success": True,
            "action": "chat",
            "message": "主动搭话已发送",
            "lanlan_name": lanlan_name,
            "source_mode": best_channel,
            "source_links": source_links
        })
        
    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        return JSONResponse({
            "success": False,
            "error": "AI处理超时"
        }, status_code=504)
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
        "target_lang": "目标语言代码 ('zh', 'en', 'ja', 'ko')",
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

# ========== 个性化内容接口 ==========

@router.post('/personal_dynamics')
async def get_personal_dynamics(request: Request):
    """获取个性化内容数据"""
    from utils.web_scraper import fetch_personal_dynamics, format_personal_dynamics
    try:
        
        data = await request.json()
        limit = data.get('limit', 10)
        
        # 获取个性化内容
        personal_content = await fetch_personal_dynamics(limit=limit)
        
        if not personal_content['success']:
            return JSONResponse({
                "success": False,
                "error": "无法获取个性化内容",
                "detail": personal_content.get('error', '未知错误')
            }, status_code=500)
        
        # 格式化内容用于前端显示
        formatted_content = format_personal_dynamics(personal_content)
        
        return JSONResponse({
            "success": True,
            "data": {
                "raw": personal_content,
                "formatted": formatted_content,
                "platforms": [k for k in personal_content.keys() if k not in ('success', 'error', 'region')]
            }
        })
        
    except Exception as e:
        logger.error(f"获取个性化内容失败: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)
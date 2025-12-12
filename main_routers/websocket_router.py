# -*- coding: utf-8 -*-
"""
WebSocket Router

Handles WebSocket endpoints including:
- Main WebSocket connection for chat
- Proactive chat
- Task notifications
"""

import json
import uuid
import asyncio
import logging
import random
import base64
import tempfile

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
import httpx

from .shared_state import (
    get_session_manager, 
    get_session_id, 
    get_config_manager,
)
from config import MODELS_WITH_EXTRA_BODY, TOOL_SERVER_PORT
from config.prompts_sys import proactive_chat_prompt, proactive_chat_prompt_screenshot
from utils.screenshot_utils import analyze_screenshot_from_data_url

router = APIRouter(tags=["websocket"])
logger = logging.getLogger("Main")

# Lock for session management
_lock = asyncio.Lock()


@router.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    await websocket.accept()
    
    session_manager = get_session_manager()
    session_id = get_session_id()
    
    # 检查角色是否存在
    if lanlan_name not in session_manager:
        logger.warning(f"❌ 角色 {lanlan_name} 不存在")
        current_catgirl = None
        if session_manager:
            current_catgirl = list(session_manager.keys())[0]
        if current_catgirl:
            try:
                await websocket.send_text(json.dumps({
                    "type": "catgirl_switched",
                    "new_catgirl": current_catgirl,
                    "old_catgirl": lanlan_name
                }))
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"通知前端失败: {e}")
        await websocket.close()
        return
    
    this_session_id = uuid.uuid4()
    async with _lock:
        session_id[lanlan_name] = this_session_id
    
    logger.info(f"⭐ WebSocket accepted: {websocket.client}, session: {this_session_id}")
    
    session_manager[lanlan_name].websocket = websocket
    logger.info(f"✅ 已设置 {lanlan_name} 的WebSocket连接")

    try:
        while True:
            data = await websocket.receive_text()
            
            if lanlan_name not in session_id or lanlan_name not in session_manager:
                logger.info(f"角色 {lanlan_name} 已被删除，关闭连接")
                await websocket.close()
                break
            
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status("切换至另一个终端...")
                await websocket.close()
                break
            
            message = json.loads(data)
            action = message.get("action")

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    mode = 'text' if input_type == 'text' else 'audio'
                    asyncio.create_task(session_manager[lanlan_name].start_session(
                        websocket, message.get("new_session", False), mode
                    ))
                else:
                    await session_manager[lanlan_name].send_status(f"Invalid input type: {input_type}")

            elif action == "stream_data":
                asyncio.create_task(session_manager[lanlan_name].stream_data(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            else:
                logger.warning(f"Unknown action: {action}")
                await session_manager[lanlan_name].send_status(f"Unknown action: {action}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"💥 WebSocket error: {e}")
        try:
            if lanlan_name in session_manager:
                await session_manager[lanlan_name].send_status(f"Server error: {e}")
        except:
            pass
    finally:
        logger.info(f"Cleaning up WebSocket: {websocket.client}")
        if lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup()
            if session_manager[lanlan_name].websocket == websocket:
                session_manager[lanlan_name].websocket = None


@router.post('/api/notify_task_result')
async def notify_task_result(request: Request):
    """供工具/任务服务回调：在下一次正常回复之后，插入一条任务完成提示。"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    
    try:
        data = await request.json()
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        text = (data.get('text') or '').strip()
        
        if not text:
            return JSONResponse({"success": False, "error": "text required"}, status_code=400)
        
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        
        mgr.pending_extra_replies.append(text)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/api/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：根据概率选择使用图片或热门内容，让AI决定是否主动发起对话"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    
    try:
        master_name_current, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse({
                "success": False, 
                "error": f"角色 {lanlan_name} 不存在"
            }, status_code=404)
        
        if mgr.is_active and hasattr(mgr.session, '_is_responding') and mgr.session._is_responding:
            return JSONResponse({
                "success": False, 
                "error": "AI正在响应中，无法主动搭话"
            }, status_code=409)
        
        logger.info(f"[{lanlan_name}] 开始主动搭话流程...")
        
        # 获取proactive_chat配置的API
        proactive_config = _config_manager.get_model_api_config('proactive_chat')
        model = proactive_config.get('model')
        base_url = proactive_config.get('base_url')
        api_key = proactive_config.get('api_key')
        
        if not model or not base_url or not api_key:
            return JSONResponse({
                "success": False, 
                "error": "主动搭话模型配置缺失"
            }, status_code=500)
        
        # 概率选择使用截图还是热门内容
        screenshot_data = data.get('screenshot')
        use_screenshot = False
        
        if screenshot_data:
            screenshot_probability = 0.3  # 30%概率使用截图
            if random.random() < screenshot_probability:
                use_screenshot = True
                logger.info(f"[{lanlan_name}] 选择使用截图模式进行主动搭话")
            else:
                logger.info(f"[{lanlan_name}] 虽然有截图但选择使用热门内容模式")
        else:
            logger.info(f"[{lanlan_name}] 没有截图，使用热门内容模式")
        
        # 构建prompt和messages
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        extra_body = {}
        if model in MODELS_WITH_EXTRA_BODY:
            extra_body = {"enable_thinking": False}
        
        if use_screenshot and screenshot_data:
            # 使用截图模式
            analysis_result = analyze_screenshot_from_data_url(screenshot_data)
            
            if analysis_result.get("success"):
                prompt = proactive_chat_prompt_screenshot.format(
                    master_name=master_name_current,
                    lanlan_name=lanlan_name,
                    screenshot_content=analysis_result.get("content", "无法分析截图内容")
                )
                
                if analysis_result.get("image_type") == "screenshot":
                    messages = [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"请分析{master_name_current}的屏幕截图，决定是否主动搭话。"},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": screenshot_data}
                                }
                            ]
                        }
                    ]
                else:
                    messages = [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"屏幕内容分析：\n{analysis_result.get('content', '无内容')}\n\n请根据以上信息决定是否主动搭话。"}
                    ]
            else:
                logger.warning(f"截图分析失败: {analysis_result.get('error')}")
                use_screenshot = False
        
        if not use_screenshot:
            # 使用热门内容模式
            hot_content = ""
            try:
                async with httpx.AsyncClient(timeout=10) as http_client:
                    resp = await http_client.get(f"http://localhost:{TOOL_SERVER_PORT}/api/trending")
                    if resp.status_code == 200:
                        trending_data = resp.json()
                        if trending_data.get("success") and trending_data.get("data"):
                            items = trending_data["data"][:5]  # 取前5条
                            hot_content = "\n".join([f"- {item.get('title', '')}" for item in items])
            except Exception as e:
                logger.warning(f"获取热门内容失败: {e}")
            
            if not hot_content:
                hot_content = "暂无热门内容"
            
            prompt = proactive_chat_prompt.format(
                master_name=master_name_current,
                lanlan_name=lanlan_name,
                hot_content=hot_content
            )
            
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"当前热门话题：\n{hot_content}\n\n请决定是否主动搭话，如果决定搭话，请直接说出搭话内容（不需要任何前缀）。如果决定不搭话，请回复[不搭话]。"}
            ]
        
        # 调用API
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=200,
            extra_body=extra_body if extra_body else None
        )
        
        ai_decision = response.choices[0].message.content.strip()
        
        # 解析AI决定
        if "[不搭话]" in ai_decision or "不搭话" in ai_decision or ai_decision == "":
            logger.info(f"[{lanlan_name}] AI决定不主动搭话")
            return JSONResponse({
                "success": True,
                "should_talk": False,
                "message": "AI决定当前不适合主动搭话"
            })
        else:
            # AI决定主动搭话
            logger.info(f"[{lanlan_name}] AI决定主动搭话: {ai_decision[:50]}...")
            
            # 通过WebSocket发送主动搭话
            if mgr.websocket:
                try:
                    await mgr.websocket.send_text(json.dumps({
                        "type": "proactive_message",
                        "content": ai_decision,
                        "source": "screenshot" if use_screenshot else "trending"
                    }))
                except Exception as e:
                    logger.warning(f"发送主动搭话消息失败: {e}")
            
            return JSONResponse({
                "success": True,
                "should_talk": True,
                "message": ai_decision,
                "source": "screenshot" if use_screenshot else "trending"
            })
        
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)

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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from openai import APIConnectionError, InternalServerError, RateLimitError
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
import httpx

from .shared_state import (
    get_session_manager, 
    get_config_manager,
)
from config import MEMORY_SERVER_PORT
from config.prompts_sys import proactive_chat_prompt, proactive_chat_prompt_screenshot
from utils.screenshot_utils import analyze_screenshot_from_data_url

router = APIRouter(tags=["websocket"])
logger = logging.getLogger("Main")

# Lock for session management
_lock = asyncio.Lock()


@router.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    await websocket.accept()
    
    # 检查角色是否存在，如果不存在则通知前端并关闭连接
    if lanlan_name not in session_manager:
        logger.warning(f"❌ 角色 {lanlan_name} 不存在，当前可用角色: {list(session_manager.keys())}")
        # 获取当前正确的角色名
        current_catgirl = None
        if session_manager:
            current_catgirl = list(session_manager.keys())[0]
        # 通知前端切换到正确的角色
        if current_catgirl:
            try:
                await websocket.send_text(json.dumps({
                    "type": "catgirl_switched",
                    "new_catgirl": current_catgirl,
                    "old_catgirl": lanlan_name
                }))
                logger.info(f"已通知前端切换到正确的角色: {current_catgirl}")
                # 等待一下让客户端有时间处理消息，避免 onclose 在 onmessage 之前触发
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"通知前端失败: {e}")
        await websocket.close()
        return
    
    this_session_id = uuid.uuid4()
    async with _lock:
        global session_id
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐websocketWebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")
    
    # 立即设置websocket到session manager，以支持主动搭话
    # 注意：这里设置后，即使cleanup()被调用，websocket也会在start_session时重新设置
    session_manager[lanlan_name].websocket = websocket
    logger.info(f"✅ 已设置 {lanlan_name} 的WebSocket连接")

    try:
        while True:
            data = await websocket.receive_text()
            # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
            if lanlan_name not in session_id or lanlan_name not in session_manager:
                logger.info(f"角色 {lanlan_name} 已被重命名或删除，关闭旧连接")
                await websocket.close()
                break
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status(f"切换至另一个终端...")
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")
            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    # 传递input_mode参数，告知session manager使用何种模式
                    mode = 'text' if input_type == 'text' else 'audio'
                    asyncio.create_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), mode))
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
                # 心跳保活消息，回复pong
                await websocket.send_text(json.dumps({"type": "pong"}))
                # logger.debug(f"收到心跳ping，已回复pong")

            else:
                logger.warning(f"Unknown action received: {action}")
                await session_manager[lanlan_name].send_status(f"Unknown action: {action}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        error_message = f"WebSocket handler error: {e}"
        logger.error(f"💥 {error_message}")
        try:
            if lanlan_name in session_manager:
                await session_manager[lanlan_name].send_status(f"Server error: {e}")
        except:
            pass
    finally:
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
        if lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup()
            # 注意：cleanup() 会清空 websocket，但只在连接真正断开时调用
            # 如果连接还在，websocket应该保持设置
            if session_manager[lanlan_name].websocket == websocket:
                session_manager[lanlan_name].websocket = None


@router.post('/api/notify_task_result')
async def notify_task_result(request: Request):
    """供工具/任务服务回调：在下一次正常回复之后，插入一条任务完成提示。"""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        data = await request.json()
        # 如果未显式提供，则使用当前默认角色
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        text = (data.get('text') or '').strip()
        if not text:
            return JSONResponse({"success": False, "error": "text required"}, status_code=400)
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # 将提示加入待插入队列
        mgr.pending_extra_replies.append(text)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/api/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：根据概率选择使用图片或热门内容，让AI决定是否主动发起对话"""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        from utils.web_scraper import fetch_trending_content, format_trending_content
        
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
        
        # 前端已经根据三种模式决定是否使用截图
        use_screenshot = has_screenshot
        
        if use_screenshot:
            logger.info(f"[{lanlan_name}] 前端选择使用截图进行主动搭话")
            
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
        else:
            logger.info(f"[{lanlan_name}] 前端选择使用热门内容进行主动搭话")
        
        if not use_screenshot:
            # 热门内容主动对话
            try:
                trending_content = await fetch_trending_content(bilibili_limit=10, weibo_limit=10)
                
                if not trending_content['success']:
                    return JSONResponse({
                        "success": False,
                        "error": "无法获取热门内容",
                        "detail": trending_content.get('error', '未知错误')
                    }, status_code=500)
                
                formatted_content = format_trending_content(trending_content)
                logger.info(f"[{lanlan_name}] 成功获取热门内容")
                
            except Exception:
                logger.exception(f"[{lanlan_name}] 获取热门内容失败")
                return JSONResponse({
                    "success": False,
                    "error": "爬取热门内容时出错",
                    "detail": "请检查网络连接或热门内容服务状态"
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
            logger.info(f"[{lanlan_name}] 使用图片主动对话提示词")
        else:
            # 热门内容模板：基于网络热点让AI决定是否主动发起对话
            system_prompt = proactive_chat_prompt.format(
                lanlan_name=lanlan_name,
                master_name=master_name_current,
                trending_content=formatted_content,
                memory_context=memory_context
            )
            logger.info(f"[{lanlan_name}] 使用热门内容主动对话提示词")

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
                temperature=1.1,
                streaming=False  # 不需要流式，直接获取完整响应
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
                        llm.ainvoke([SystemMessage(content=system_prompt)]),
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
                            except:
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
            
            # 5. 判断AI是否选择搭话
            if "[PASS]" in response_text or not response_text:
                return JSONResponse({
                    "success": True,
                    "action": "pass",
                    "message": "AI选择暂时不搭话"
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
            
            # 通过handle_text_data处理这段话（触发TTS和前端显示）
            # 分chunk发送以模拟流式效果
            chunks = [response_text[i:i+10] for i in range(0, len(response_text), 10)]
            for i, chunk in enumerate(chunks):
                await mgr.handle_text_data(chunk, is_first_chunk=(i == 0))
                await asyncio.sleep(0.05)  # 小延迟模拟流式
            
            # 调用response完成回调
            if hasattr(mgr, 'handle_response_complete'):
                await mgr.handle_response_complete()
            
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



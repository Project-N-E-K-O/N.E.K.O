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

from utils.logger_config import get_module_logger
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .shared_state import (
    get_session_manager, 
    get_config_manager,
    get_session_id,
)

router = APIRouter(tags=["websocket"])
logger = get_module_logger(__name__, "Main")

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
            current_catgirl = next(iter(session_manager))
        # 通知前端切换到正确的角色
        if current_catgirl:
            try:
                # 注意：此时还没有session_manager，无法获取用户语言，使用默认语言
                message = {
                    "type": "catgirl_switched",
                    "new_catgirl": current_catgirl,
                    "old_catgirl": lanlan_name
                }
                await websocket.send_text(json.dumps(message))
                logger.info(f"已通知前端切换到正确的角色: {current_catgirl}")
                # 等待一下让客户端有时间处理消息，避免 onclose 在 onmessage 之前触发
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"通知前端失败: {e}")
        await websocket.close()
        return
    
    this_session_id = uuid.uuid4()
    async with _lock:
        session_id = get_session_id()
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐ WebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")
    
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
                await session_manager[lanlan_name].send_status(f"{lanlan_name}正在前往另一个终端...")
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")
            
            # 处理语言设置（可以在任何消息中携带）
            if "language" in message:
                user_language = message.get("language")
                session_manager[lanlan_name].set_user_language(user_language)
                logger.info(f"收到用户语言设置: {user_language}")
            
            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    # 传递input_mode参数，告知session manager使用何种模式
                    # 注意：音频模块由 main_server 后台预加载，Python import lock 会自动等待首次导入完成
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

            elif action == "screenshot_response":
                raw = message.get("data", "")
                b64 = raw.split(",", 1)[1] if "," in raw else raw
                session_manager[lanlan_name].resolve_screenshot_request(b64)

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
        except: # noqa
            pass
    finally:
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
        async with _lock:
            session_id = get_session_id()
            is_current = session_id.get(lanlan_name) == this_session_id
            if is_current:
                session_id.pop(lanlan_name, None)
        
        if is_current and lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup(expected_websocket=websocket)


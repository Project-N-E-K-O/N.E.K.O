# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reload-page notices and memory-server reload/release notifications
shared by CRUD, voice and persona endpoints.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import logger

import json
import httpx
from config import (
    MEMORY_SERVER_PORT,
)


def _resolve_reload_page_notice_code(message_text: str, message_code: str | None = None) -> str:
    if message_code:
        return message_code

    text = str(message_text or "")
    if "云存档" in text:
        return "RELOAD_PAGE_CLOUDSAVE_CHARACTER"
    if "人格" in text:
        return "RELOAD_PAGE_PERSONA"
    if "角色设定" in text or "设定" in text:
        return "RELOAD_PAGE_CHARACTER_SETTINGS"
    if "音色" in text:
        return "RELOAD_PAGE_VOICE_STYLE"
    if "语音" in text:
        return "RELOAD_PAGE_VOICE"
    return "RELOAD_PAGE"


async def send_reload_page_notice(
    session,
    message_text: str = "语音已更新，页面即将刷新",
    message_code: str | None = None,
):
    """
    Send a page-reload notice to the frontend (via WebSocket).

    Args:
        session: LLMSessionManager instance
        message_text: message text to send (auto-translated)
        message_code: explicit localized message code; inferred from the message text when empty

    Returns:
        bool: whether the notice was sent successfully
    """
    if not session or not session.websocket:
        return False

    # 检查 WebSocket 连接状态
    if not hasattr(session.websocket, 'client_state') or session.websocket.client_state != session.websocket.client_state.CONNECTED:
        return False

    try:
        notice_code = _resolve_reload_page_notice_code(message_text, message_code)
        await session.websocket.send_text(json.dumps({
            "type": "reload_page",
            "message": json.dumps({"code": notice_code, "details": {"message": message_text}})
        }))
        logger.info("已通知前端刷新页面")
        return True
    except Exception as e:
        logger.warning(f"通知前端刷新页面失败: {e}")
        return False


async def notify_memory_server_reload(*, reason: str = "") -> bool:
    try:
        async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload",
                timeout=5.0,
            )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 记忆服务器重新加载失败，status=%s, reason=%s",
                response.status_code,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已通知记忆服务器重新加载配置（%s）", reason or "角色数据更新")
            return True

        logger.warning(
            "⚠️ 记忆服务器重新加载返回非成功状态，payload=%s, reason=%s",
            payload,
            reason,
        )
    except Exception as exc:
        logger.warning("⚠️ 通知记忆服务器重新加载配置时出错: %s（reason=%s）", exc, reason)
    return False


async def release_memory_server_character(character_name: str, *, reason: str = "") -> bool:
    from urllib.parse import quote
    from utils.internal_http_client import get_internal_http_client

    try:
        encoded_name = quote(character_name, safe="")
        # 复用进程级单例避免 per-call SSLContext 冷启动（实测 ~1.1s/次）。
        # 单例在 on_shutdown 末尾由 aclose_internal_http_client 统一关闭，
        # release/upload 阶段之前都可安全共享；无需 async with。
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/release_character/{encoded_name}",
            timeout=5.0,
        )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 释放记忆服务器角色句柄失败，status=%s, character=%s, reason=%s",
                response.status_code,
                character_name,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已释放角色 %s 的记忆服务器句柄（%s）", character_name, reason or "角色文件操作前")
            return True

        logger.warning(
            "⚠️ 释放记忆服务器角色句柄返回非成功状态，payload=%s, character=%s, reason=%s",
            payload,
            character_name,
            reason,
        )
    except Exception as exc:
        logger.warning(
            "⚠️ 调用记忆服务器释放角色句柄时出错: %s（character=%s, reason=%s）",
            exc,
            character_name,
            reason,
        )
    return False

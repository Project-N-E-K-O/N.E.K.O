# -*- coding: utf-8 -*-
"""
Proactive Chat Router

主动搭话（proactive chat）模式与频率的统一 API。

URL convention: 路由声明不带末尾斜杠（与 ``main_routers/config_router.py``
保持一致；由 ``scripts/check_api_trailing_slash.py`` 守门）。

提供四个端点：

* ``GET  /api/proactive/mode``      — 读取当前模式（off / normal / focus / frequent / custom）
* ``POST /api/proactive/mode``      — 套用一组预设
* ``GET  /api/proactive/settings``  — 读取主动搭话相关字段当前值
* ``POST /api/proactive/settings``  — 更新部分主动搭话字段（白名单内）

所有写入复用 ``utils.preferences.save_global_conversation_settings``，
保证白名单/类型校验/原子写入逻辑只在一处维护。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from utils.cloudsave_runtime import MaintenanceModeError
from utils.logger_config import get_module_logger
from utils.preferences import (
    aload_global_conversation_settings,
    save_global_conversation_settings,
)


router = APIRouter(prefix="/api/proactive", tags=["proactive"])
logger = get_module_logger(__name__, "Main")


# 主动搭话所有可调字段（白名单子集；与 utils/preferences 的
# _ALLOWED_CONVERSATION_SETTINGS 保持同步，但只暴露搭话相关字段）。
_PROACTIVE_BOOL_FIELDS = (
    "proactiveChatEnabled",
    "proactiveVisionEnabled",
    "proactiveVisionChatEnabled",
    "proactiveNewsChatEnabled",
    "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled",
    "proactiveMusicEnabled",
    "proactiveMemeEnabled",
    "proactiveMiniGameInviteEnabled",
)
_PROACTIVE_INT_FIELDS = (
    "proactiveChatInterval",
    "proactiveVisionInterval",
)
_PROACTIVE_FIELDS = _PROACTIVE_BOOL_FIELDS + _PROACTIVE_INT_FIELDS


# 预设模式：服务器端定义，避免每个调用方自己维护一份。
# interval 单位与前端 ``app-state.js`` 一致 —— 秒。
PROACTIVE_PRESETS: dict[str, dict[str, Any]] = {
    "off": {
        "proactiveChatEnabled": False,
        "proactiveVisionEnabled": False,
        "proactiveVisionChatEnabled": False,
        "proactiveNewsChatEnabled": False,
        "proactiveVideoChatEnabled": False,
        "proactivePersonalChatEnabled": False,
        "proactiveMusicEnabled": False,
        "proactiveMemeEnabled": False,
        "proactiveMiniGameInviteEnabled": False,
    },
    "normal": {
        "proactiveChatEnabled": True,
        "proactiveVisionEnabled": True,
        "proactiveVisionChatEnabled": True,
        "proactiveNewsChatEnabled": True,
        "proactiveVideoChatEnabled": True,
        "proactivePersonalChatEnabled": True,
        "proactiveMusicEnabled": True,
        "proactiveMemeEnabled": True,
        "proactiveMiniGameInviteEnabled": True,
        "proactiveChatInterval": 15,
        "proactiveVisionInterval": 10,
    },
    # 低打扰：保留搭话与个人动态，关掉新闻/视频/音乐等噪声源，间隔放长。
    "focus": {
        "proactiveChatEnabled": True,
        "proactiveVisionEnabled": False,
        "proactiveVisionChatEnabled": False,
        "proactiveNewsChatEnabled": False,
        "proactiveVideoChatEnabled": False,
        "proactivePersonalChatEnabled": True,
        "proactiveMusicEnabled": False,
        "proactiveMemeEnabled": False,
        "proactiveMiniGameInviteEnabled": False,
        "proactiveChatInterval": 60,
        "proactiveVisionInterval": 60,
    },
    # 高频：全开，间隔最短。
    "frequent": {
        "proactiveChatEnabled": True,
        "proactiveVisionEnabled": True,
        "proactiveVisionChatEnabled": True,
        "proactiveNewsChatEnabled": True,
        "proactiveVideoChatEnabled": True,
        "proactivePersonalChatEnabled": True,
        "proactiveMusicEnabled": True,
        "proactiveMemeEnabled": True,
        "proactiveMiniGameInviteEnabled": True,
        "proactiveChatInterval": 5,
        "proactiveVisionInterval": 5,
    },
}


def _filter_proactive_subset(settings: dict[str, Any]) -> dict[str, Any]:
    """从完整 conversation-settings 中挑出搭话相关字段。"""
    return {k: v for k, v in settings.items() if k in _PROACTIVE_FIELDS}


async def _readback_subset(payload_keys) -> dict[str, Any]:
    """保存后回读 ``aload_global_conversation_settings``，仅返回
    payload 涉及且**真实落盘**的字段。

    ``save_global_conversation_settings`` 会对字段做第二轮类型 + 范围
    过滤（bool 必须是 bool、interval 必须是 1<=int<=3600 等），被丢弃
    的字段不应在 ``applied`` 中出现，否则调用方会误判为生效。
    """
    latest = await aload_global_conversation_settings()
    return {k: latest[k] for k in payload_keys if k in latest}


def _infer_mode(settings: dict[str, Any]) -> str:
    """根据当前持久化的字段反推所属预设；不匹配任何预设则返回 ``custom``。

    比较时仅考察 preset 显式列出的字段，缺失字段视为不匹配。
    """
    for mode_name, preset in PROACTIVE_PRESETS.items():
        if all(settings.get(k) == v for k, v in preset.items()):
            return mode_name
    return "custom"


@router.get("/mode")
async def get_proactive_mode():
    """读取当前模式 + 当前主动搭话相关字段。"""
    try:
        settings = await aload_global_conversation_settings()
        subset = _filter_proactive_subset(settings)
        return {
            "success": True,
            "mode": _infer_mode(subset),
            "available_modes": list(PROACTIVE_PRESETS.keys()),
            "settings": subset,
        }
    except Exception as e:
        logger.exception(f"获取主动搭话模式失败: {e}")
        return {"success": False, "error": "Internal server error", "mode": "custom", "settings": {}}


@router.post("/mode")
async def set_proactive_mode(request: Request):
    """套用预设模式。

    请求体：``{"mode": "off" | "normal" | "focus" | "frequent"}``
    """
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return {"success": False, "error": "请求体必须为对象"}
        mode = data.get("mode")
        if not isinstance(mode, str) or mode not in PROACTIVE_PRESETS:
            return {
                "success": False,
                "error": f"未知模式: {mode!r}；可选值: {list(PROACTIVE_PRESETS.keys())}",
            }

        preset = PROACTIVE_PRESETS[mode]
        if not await asyncio.to_thread(save_global_conversation_settings, dict(preset)):
            return {"success": False, "error": "保存失败"}

        applied = await _readback_subset(preset.keys())
        return {"success": True, "mode": mode, "applied": applied}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"切换主动搭话模式失败: {e}")
        return {"success": False, "error": "Internal server error"}


@router.get("/settings")
async def get_proactive_settings():
    """读取当前主动搭话相关字段（白名单内）。"""
    try:
        settings = await aload_global_conversation_settings()
        return {"success": True, "settings": _filter_proactive_subset(settings)}
    except Exception as e:
        logger.exception(f"获取主动搭话设置失败: {e}")
        return {"success": False, "error": "Internal server error", "settings": {}}


@router.post("/settings")
async def update_proactive_settings(request: Request):
    """部分更新主动搭话字段。请求体仅接受 ``_PROACTIVE_FIELDS`` 内字段；
    其他字段静默忽略。底层 ``save_global_conversation_settings`` 还会再
    做一次类型 + 范围校验。"""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return {"success": False, "error": "请求体必须为对象"}

        payload = {k: v for k, v in data.items() if k in _PROACTIVE_FIELDS}
        if not payload:
            return {"success": False, "error": "没有可识别的主动搭话字段"}

        if not await asyncio.to_thread(save_global_conversation_settings, payload):
            return {"success": False, "error": "保存失败"}

        applied = await _readback_subset(payload.keys())
        rejected = [k for k in payload.keys() if k not in applied]
        result: dict[str, Any] = {"success": True, "applied": applied}
        if rejected:
            # 字段类型/范围不合法时静默被底层丢弃；明确告知调用方避免误判。
            result["rejected"] = rejected
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"更新主动搭话设置失败: {e}")
        return {"success": False, "error": "Internal server error"}

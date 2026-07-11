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

"""User preferences and conversation settings endpoints.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router

import asyncio
from fastapi import Request
from ..shared_state import get_session_manager
from utils.preferences import aload_user_preferences, update_model_preferences, validate_model_preferences, move_model_to_top, aload_global_conversation_settings, save_global_conversation_settings, GLOBAL_CONVERSATION_KEY
from utils.cloudsave_runtime import MaintenanceModeError


def _apply_noise_reduction_to_active_sessions(enabled: bool):
    """Apply noise reduction toggle to all active voice sessions immediately."""
    from main_logic.omni_realtime_client import OmniRealtimeClient
    try:
        session_manager = get_session_manager()
        for _name, mgr in session_manager.items():
            if not mgr.is_active or mgr.session is None:
                continue
            if not isinstance(mgr.session, OmniRealtimeClient):
                continue
            ap = getattr(mgr.session, '_audio_processor', None)
            if ap is not None:
                ap.set_enabled(enabled)
    except Exception as e:
        logger.warning(f"Failed to apply noise reduction to active sessions: {e}")


@router.get("/preferences")
async def get_preferences():
    """Get user preferences."""
    preferences = await aload_user_preferences()
    return preferences


@router.post("/preferences")
async def save_preferences(request: Request):
    """Save user preferences."""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 验证偏好数据
        if not validate_model_preferences(data):
            return {"success": False, "error": "偏好数据格式无效"}
        
        # 防止使用保留的全局对话设置键作为模型路径
        if data.get('model_path') == GLOBAL_CONVERSATION_KEY:
            return {"success": False, "error": "model_path 不能使用保留键"}
        
        # 获取参数（可选）
        parameters = data.get('parameters')
        # 获取显示器信息（可选，用于多屏幕位置恢复）
        display = data.get('display')
        # 获取旋转信息（可选，用于VRM模型朝向）
        rotation = data.get('rotation')
        # 获取视口信息（可选，用于跨分辨率位置和缩放归一化）
        viewport = data.get('viewport')
        # 获取相机位置信息（可选，用于恢复VRM滚轮缩放状态）
        camera_position = data.get('camera_position')

        # 验证和清理 viewport 数据
        if viewport is not None:
            if not isinstance(viewport, dict):
                viewport = None
            else:
                # 验证必需的数值字段
                width = viewport.get('width')
                height = viewport.get('height')
                if not (isinstance(width, (int, float)) and isinstance(height, (int, float)) and
                        width > 0 and height > 0):
                    viewport = None

        # 更新偏好（底层 atomic_write_json 会阻塞事件循环，offload 到线程池）
        ok = await asyncio.to_thread(
            update_model_preferences,
            data['model_path'], data['position'], data['scale'], parameters, display, rotation, viewport, camera_position,
        )
        if ok:
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
    except MaintenanceModeError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/preferences/set-preferred")
async def set_preferred_model(request: Request):
    """Set the preferred model."""
    try:
        data = await request.json()
        if not data or 'model_path' not in data:
            return {"success": False, "error": "无效的数据"}
        
        if move_model_to_top(data['model_path']):
            return {"success": True, "message": "首选模型已更新"}
        else:
            return {"success": False, "error": "模型不存在或更新失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/conversation-settings")
async def get_conversation_settings():
    """Get global conversation settings (read from the user_preferences.json synced backup).

    Also returns the telemetry A/B test branch, so the frontend can pick default
    behavior by branch at first launch, consistent with the branch reported by the
    token tracker — the same device always lands in the same group, preventing
    control/experiment mismatches between client and server.
    """
    try:
        # 先解析 telemetry branch、再 load settings：get_telemetry_branch 可能在 slow
        # path 触发退役实验（proactive_interval_20s）的一次性偏好回滚（20s→15s）。若按
        # 旧顺序先 load，会拿到回滚前的 20s 返回前端；而存量用户没有首启 pending marker、
        # 会直接应用并经 periodic sync 把 20s POST 回来，撤销本次迁移（见 token_tracker
        # ._rollback_retired_proactive_interval）。
        try:
            from utils.token_tracker import get_telemetry_branch
            telemetry_branch = await asyncio.to_thread(get_telemetry_branch)
        except Exception:
            # 故意返回 None：前端只在 telemetryBranch 是字符串时清掉首启 pending
            # marker；如果这里 fallback 到 "main"，瞬时报错会被当成「控制组分流
            # 已决议」永久锁住，下次也不会重试。返 None 让前端保留 pending、
            # 下次 fetch 成功再决议
            logger.exception("解析 telemetry branch 失败，返回 null 让前端保留 pending marker")
            telemetry_branch = None
        settings = await aload_global_conversation_settings()
        return {"success": True, "settings": settings, "telemetryBranch": telemetry_branch}
    except Exception as e:
        logger.exception(f"获取对话设置失败: {e}")
        return {"success": False, "error": "Internal server error", "settings": {}}


@router.post("/conversation-settings")
async def save_conversation_settings(request: Request):
    """Save global conversation settings (synced to the user_preferences.json backup)."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return {"success": False, "error": "请求体必须为对象"}

        if not await asyncio.to_thread(save_global_conversation_settings, data):
            return {"success": False, "error": "保存失败"}

        if 'noiseReductionEnabled' in data:
            _apply_noise_reduction_to_active_sessions(data['noiseReductionEnabled'])

        return {"success": True, "message": "对话设置已保存"}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"保存对话设置失败: {e}")
        return {"success": False, "error": "Internal server error"}

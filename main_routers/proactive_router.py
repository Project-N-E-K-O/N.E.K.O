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

"""
Proactive Chat Router

Unified API for proactive-chat mode and frequency.

URL convention: routes are declared without a trailing slash (consistent with
``main_routers/config_router.py``; enforced by ``scripts/check_api_trailing_slash.py``).

Four endpoints:

* ``GET  /api/proactive/mode``      — read the current mode (off / normal / focus / frequent / custom)
* ``POST /api/proactive/mode``      — apply a preset
* ``GET  /api/proactive/settings``  — read the current values of proactive-chat fields
* ``POST /api/proactive/settings``  — partially update proactive-chat fields (whitelisted)

All writes go through ``utils.preferences.save_global_conversation_settings``
so the whitelist / type validation / atomic-write logic is maintained in one place.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from utils.cloudsave_runtime import MaintenanceModeError
from utils.logger_config import get_module_logger
from utils.preferences import (
    aload_global_conversation_settings,
    save_global_conversation_settings,
)
from .recommendation_controls import recommendation_aware_save


router = APIRouter(prefix="/api/proactive", tags=["proactive"])
logger = get_module_logger(__name__, "Main")


# 用户绝对控制权 —— 插件和预设禁止越权修改的字段。
# ``proactiveVisionEnabled`` 是前端"隐私模式"开关的反面
# (``is_privacy_mode_enabled() == not proactiveVisionEnabled``)，
# 涉及屏幕内容采集，必须由用户本人在 UI 决定，任何 API 写入路径都要拒绝。
_USER_OWNED_FIELDS = frozenset({
    "proactiveTopicRecommendationEnabled",
    "proactiveVisionEnabled",
    # 串门开关与串门记忆开关：让她出门、记不记串门内容都由用户本人决定
    # （visitVoiceEnabled 只管本地出声，不在此列）。
    "visitEnabled",
    "visitMemoryEnabled",
})

# 主动搭话所有可调字段（白名单子集；与 utils/preferences 的
# _ALLOWED_CONVERSATION_SETTINGS 保持同步，但只暴露搭话相关字段）。
# 注：``_PROACTIVE_FIELDS`` 仅用于**读路径**和模式反推，写路径会额外
# 过滤掉 ``_USER_OWNED_FIELDS``。
_PROACTIVE_BOOL_FIELDS = (
    "proactiveChatEnabled",
    "proactiveVisionEnabled",
    "proactiveVisionChatEnabled",
    "proactiveNewsChatEnabled",
    "proactiveCommunityChatEnabled",
    "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled",
    "proactiveMusicEnabled",
    "proactiveMemeEnabled",
    "proactiveMiniGameInviteEnabled",
    "proactiveTopicRecommendationEnabled",
)
_PROACTIVE_INT_FIELDS = (
    "proactiveChatInterval",
    "proactiveVisionInterval",
)
_PROACTIVE_FIELDS = _PROACTIVE_BOOL_FIELDS + _PROACTIVE_INT_FIELDS
# 写路径允许的字段：从全集里剔除用户专有字段。
_PROACTIVE_WRITABLE_FIELDS = frozenset(_PROACTIVE_FIELDS) - _USER_OWNED_FIELDS


# 预设模式：服务器端定义，避免每个调用方自己维护一份。
# interval 单位与前端 ``app-state.js`` 一致 —— 秒。
# 注：预设故意不包含 ``proactiveVisionEnabled``（隐私模式）；切换 mode
# 不会改变用户的隐私选择。
PROACTIVE_PRESETS: dict[str, dict[str, Any]] = {
    "off": {
        "proactiveChatEnabled": False,
        "proactiveVisionChatEnabled": False,
        "proactiveNewsChatEnabled": False,
        "proactiveCommunityChatEnabled": False,
        "proactiveVideoChatEnabled": False,
        "proactivePersonalChatEnabled": False,
        "proactiveMusicEnabled": False,
        "proactiveMemeEnabled": False,
        "proactiveMiniGameInviteEnabled": False,
    },
    "normal": {
        "proactiveChatEnabled": True,
        "proactiveVisionChatEnabled": True,
        "proactiveNewsChatEnabled": True,
        "proactiveCommunityChatEnabled": True,
        "proactiveVideoChatEnabled": True,
        "proactivePersonalChatEnabled": True,
        "proactiveMusicEnabled": True,
        "proactiveMemeEnabled": True,
        "proactiveMiniGameInviteEnabled": True,
        "proactiveChatInterval": 15,
        "proactiveVisionInterval": 10,
    },
    # 低打扰：保留搭话与个人动态，关掉新闻/视频/音乐等噪声源，间隔放长。
    # 不动 vision/隐私开关——是否允许看屏幕由用户自己决定。
    "focus": {
        "proactiveChatEnabled": True,
        "proactiveVisionChatEnabled": False,
        "proactiveNewsChatEnabled": False,
        "proactiveCommunityChatEnabled": False,
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
        "proactiveVisionChatEnabled": True,
        "proactiveNewsChatEnabled": True,
        "proactiveCommunityChatEnabled": True,
        "proactiveVideoChatEnabled": True,
        "proactivePersonalChatEnabled": True,
        "proactiveMusicEnabled": True,
        "proactiveMemeEnabled": True,
        "proactiveMiniGameInviteEnabled": True,
        "proactiveChatInterval": 5,
        "proactiveVisionInterval": 5,
    },
}

# Self-check：预设里不应混入用户绝对控制权字段，也不应有拼写错误/不可写字段。
# 每次模块加载时校验，把"加预设时忘了筛"和"键名打错被静默忽略"这两类回归
# 都挡在导入阶段，而不是用户调 set_mode 才暴露。
for _mode_name, _preset in PROACTIVE_PRESETS.items():
    _leaked = set(_preset.keys()) & _USER_OWNED_FIELDS
    if _leaked:
        raise RuntimeError(
            f"PROACTIVE_PRESETS[{_mode_name!r}] 不应包含用户专有字段: {sorted(_leaked)}"
        )
    _unknown = set(_preset.keys()) - _PROACTIVE_WRITABLE_FIELDS
    if _unknown:
        raise RuntimeError(
            f"PROACTIVE_PRESETS[{_mode_name!r}] 包含未知/不可写字段: {sorted(_unknown)}"
        )


def _filter_proactive_subset(settings: dict[str, Any]) -> dict[str, Any]:
    """Pick the proactive-chat-related fields out of the full conversation settings."""
    return {k: v for k, v in settings.items() if k in _PROACTIVE_FIELDS}


def _value_matches(actual: Any, expected: Any) -> bool:
    """type-aware equality: avoids Python's ``True == 1`` / ``False == 0`` trap.

    The bool-field validation in ``save_global_conversation_settings`` is
    ``isinstance(v, bool)`` and rejects integer ``0/1``; but with plain ``==``,
    ``True`` on disk would still compare equal to an incoming ``1`` and be
    reported as "applied" — the same class of issue Codex pointed out.
    Requiring an exact ``type()`` match cuts this off entirely.
    """
    return type(actual) is type(expected) and actual == expected


async def _readback_persisted(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Read back after saving; returns ``(applied, rejected)``.

    The check is a **strict by-value + by-type comparison**:
    - By value: when ``save_global_conversation_settings`` runs its second-pass
      filter, dropped fields keep their old on-disk values; if we only checked
      key existence, "old value already on disk + new value rejected" would be
      mislabeled as applied.
    - By type: in Python ``True == 1`` / ``False == 0``; passing int ``1`` for a
      bool field gets rejected by the saver, yet the on-disk ``True`` still
      compares ``==`` to the incoming ``1``. ``_value_matches`` enforces an exact
      ``type()`` match to cut off this trap.
    """
    latest = await aload_global_conversation_settings()
    applied: dict[str, Any] = {}
    rejected: list[str] = []
    for k, v in payload.items():
        if k in latest and _value_matches(latest[k], v):
            applied[k] = latest[k]
        else:
            rejected.append(k)
    return applied, rejected


def _infer_mode(settings: dict[str, Any]) -> str:
    """Infer the preset matching every currently effective proactive setting."""

    for mode_name, preset in PROACTIVE_PRESETS.items():
        if all(_value_matches(settings.get(k), v) for k, v in preset.items()):
            return mode_name
    return "custom"


@router.get("/mode")
async def get_proactive_mode():
    """Read the current mode + the current proactive-chat fields."""
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
    """Apply a preset mode.

    Request body: ``{"mode": "off" | "normal" | "focus" | "frequent"}``
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
        if not await recommendation_aware_save(save_global_conversation_settings, dict(preset)):
            return {"success": False, "error": "保存失败"}

        applied, rejected = await _readback_persisted(preset)
        result: dict[str, Any] = {"success": True, "mode": mode, "applied": applied}
        if rejected:
            # 预设里所有字段都应是合法值；若仍出现 rejected，多半是
            # _ALLOWED_CONVERSATION_SETTINGS 漂移，需要 server 端跟进。
            result["rejected"] = rejected
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"切换主动搭话模式失败: {e}")
        return {"success": False, "error": "Internal server error"}


@router.get("/settings")
async def get_proactive_settings():
    """Read the current proactive-chat fields (whitelisted)."""
    try:
        settings = await aload_global_conversation_settings()
        return {"success": True, "settings": _filter_proactive_subset(settings)}
    except Exception as e:
        logger.exception(f"获取主动搭话设置失败: {e}")
        return {"success": False, "error": "Internal server error", "settings": {}}


@router.post("/settings")
async def update_proactive_settings(request: Request):
    """Partially update proactive-chat fields. The request body only accepts fields
    in ``_PROACTIVE_WRITABLE_FIELDS``; user-owned fields (``proactiveVisionEnabled``
    privacy mode) are explicitly rejected and reported via ``rejected_user_owned``,
    while other unrecognized fields are silently ignored. The underlying
    ``save_global_conversation_settings`` performs another round of type + range validation."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return {"success": False, "error": "请求体必须为对象"}

        rejected_user_owned = sorted(set(data.keys()) & _USER_OWNED_FIELDS)
        payload = {k: v for k, v in data.items() if k in _PROACTIVE_WRITABLE_FIELDS}
        if not payload:
            err: dict[str, Any] = {"success": False, "error": "没有可识别的主动搭话字段"}
            if rejected_user_owned:
                err["rejected_user_owned"] = rejected_user_owned
            return err

        if not await recommendation_aware_save(save_global_conversation_settings, payload):
            return {"success": False, "error": "保存失败"}

        applied, rejected = await _readback_persisted(payload)
        result: dict[str, Any] = {"success": True, "applied": applied}
        if rejected:
            # 字段类型/范围不合法被底层丢弃，或磁盘旧值与传入值不符。
            # 明确告知调用方避免误判为生效。
            result["rejected"] = rejected
        if rejected_user_owned:
            # 用户绝对控制权字段被拒：调用方应通过 UI 引导用户自行设置。
            result["rejected_user_owned"] = rejected_user_owned
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"更新主动搭话设置失败: {e}")
        return {"success": False, "error": "Internal server error"}


async def _recommendation_owner(character_id: str):
    from .shared_state import get_config_manager
    from main_logic.topic.recommendation.contracts import RecommendationError
    from main_logic.topic.recommendation.registry import get_recommendation_service
    from utils.config_manager.reserved_schema import get_reserved, normalize_character_id

    if not normalize_character_id(character_id):
        raise RecommendationError('invalid_character_id')
    characters = await asyncio.to_thread(get_config_manager().load_characters, require_authoritative=True)
    identifiers = {normalize_character_id(get_reserved(data, 'character_id', default=''))
                   for data in characters.get('猫娘', {}).values()}
    if character_id not in identifiers:
        raise RecommendationError('character_not_found')
    service = get_recommendation_service()
    if service is None:
        raise RecommendationError('service_unavailable')
    return service


def _recommendation_error_response(exc):
    from main_logic.topic.recommendation.contracts import RecommendationError
    code = exc.code if isinstance(exc, RecommendationError) else 'service_unavailable'
    status = {'invalid_character_id': 400, 'invalid_request': 400,
              'character_not_found': 404, 'epoch_conflict': 409,
              'revision_conflict': 409, 'stale_operation': 409}.get(code, 503)
    return JSONResponse(status_code=status, content={'success': False, 'error_code': code},
                        headers={'Cache-Control': 'no-store'})


@router.get('/recommendation/status')
async def get_topic_recommendation_status(character_id: str):
    from main_logic.topic.recommendation.contracts import RecommendationError
    try:
        service = await _recommendation_owner(character_id)
        generation = service.reset_generation
        result = await service.status(character_id)
        await _verify_recommendation_response_owner(service)
        if generation != service.reset_generation:
            raise RecommendationError('stale_operation')
        return JSONResponse(content={'success': True, 'reset_generation': generation, **result},
                            headers={'Cache-Control': 'no-store'})
    except Exception as exc:
        return _recommendation_error_response(exc)


async def _verify_recommendation_response_owner(service):
    from main_logic.topic.recommendation.contracts import RecommendationError
    from main_logic.topic.recommendation.registry import get_recommendation_service
    generation = service.store.root_generation
    if service is not get_recommendation_service():
        raise RecommendationError('stale_operation')
    if not await service.store.root_ready():
        raise RecommendationError('maintenance')
    if service is not get_recommendation_service() or generation != service.store.root_generation:
        raise RecommendationError('stale_operation')


@router.post('/recommendation/reset')
async def reset_topic_recommendation(request: Request):
    return await _reset_topic_recommendation_context(request, preserve_profile=False)


@router.post('/recommendation/recover')
async def recover_topic_recommendation(request: Request):
    return await _reset_topic_recommendation_context(request, preserve_profile=True)


async def _reset_topic_recommendation_context(request: Request, *, preserve_profile: bool):
    from .system_router import _validate_local_mutation_request
    from main_logic.topic.recommendation.contracts import RecommendationError
    from utils.character_memory import character_config_mutation_lock
    try:
        raw = await request.body()
        if len(raw) > 4096:
            raise RecommendationError('invalid_request')
        import json
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RecommendationError('invalid_request') from None
        if not isinstance(data, dict):
            raise RecommendationError('invalid_request')
        rejected = _validate_local_mutation_request(request, payload=data,
                                                  error_defaults={'success': False})
        if rejected is not None:
            return rejected
        fields = ('character_id', 'expected_epoch', 'request_id', 'expected_reset_generation', 'expected_confirmation')
        if (set(data) - set(fields) - {'_csrf_token'} or
                any(not isinstance(data.get(key), str) or not data[key] or len(data[key]) > 128
                    for key in fields)):
            raise RecommendationError('invalid_request')
        if len(data['expected_confirmation']) != 64 or any(c not in '0123456789abcdef' for c in data['expected_confirmation']):
            raise RecommendationError('invalid_request')
        async with character_config_mutation_lock:
            service = await _recommendation_owner(data['character_id'])
            # A persisted epoch survives restart/restore. The confirmation also
            # belongs to one root owner and uninterrupted control generation.
            if data['expected_reset_generation'] != service.reset_generation:
                raise RecommendationError('stale_operation')
            result = await service.reset(data['character_id'], data['expected_epoch'], data['request_id'],
                expected_confirmation=data['expected_confirmation'],
                **({'preserve_profile': True} if preserve_profile else {}))
            await _verify_recommendation_response_owner(service)
            if data['expected_reset_generation'] != service.reset_generation:
                raise RecommendationError('stale_operation')
        return JSONResponse(content={'success': True, 'reset_generation': data['expected_reset_generation'], **result},
                            headers={'Cache-Control': 'no-store'})
    except Exception as exc:
        return _recommendation_error_response(exc)

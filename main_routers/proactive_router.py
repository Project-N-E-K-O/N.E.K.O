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

from utils.cloudsave_runtime import MaintenanceModeError
from utils.logger_config import get_module_logger
from utils.preferences import (
    aload_global_conversation_settings,
    save_global_conversation_settings,
)


router = APIRouter(prefix="/api/proactive", tags=["proactive"])
logger = get_module_logger(__name__, "Main")


# Fields under exclusive user control: plugins and presets must not modify them.
# proactiveVisionEnabled is the inverse of the frontend privacy-mode switch.
# (``is_privacy_mode_enabled() == not proactiveVisionEnabled``)，
# Screen-content capture requires the user's own UI choice; reject every API write path.
_USER_OWNED_FIELDS = frozenset({
    "proactiveVisionEnabled",
    # Visit and visit-memory switches: users decide whether she visits and whether visits are remembered
    # (visitVoiceEnabled controls only local speech and is outside this list).
    "visitEnabled",
    "visitMemoryEnabled",
})

# All adjustable proactive-chat fields: a whitelist subset synchronized with
# utils/preferences._ALLOWED_CONVERSATION_SETTINGS, exposing proactive-chat fields only.
# _PROACTIVE_FIELDS serves read paths and mode inference only; write paths additionally
# filter _USER_OWNED_FIELDS.
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
)
_PROACTIVE_INT_FIELDS = (
    "proactiveChatInterval",
    "proactiveVisionInterval",
)
_PROACTIVE_FIELDS = _PROACTIVE_BOOL_FIELDS + _PROACTIVE_INT_FIELDS
# Writable fields: exclude user-owned fields from the full set.
_PROACTIVE_WRITABLE_FIELDS = frozenset(_PROACTIVE_FIELDS) - _USER_OWNED_FIELDS


# Server-defined preset modes avoid separate copies in every caller.
# interval is in seconds, matching frontend app-state.js.
# Presets deliberately omit proactiveVisionEnabled (privacy mode); changing modes
# never changes the user's privacy choice.
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
    # Low interruption: retain chat and personal updates, disable news/video/music noise, and lengthen intervals.
    # Leave vision/privacy unchanged: screen access is the user's decision.
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
    # High frequency: enable everything at the shortest interval.
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

# Self-check: presets must contain neither exclusively user-controlled fields nor misspelled/unwritable fields.
# Validate on each module load, catching forgotten filters and silently ignored typo keys
# at import rather than when users call set_mode.
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
        if not await asyncio.to_thread(save_global_conversation_settings, dict(preset)):
            return {"success": False, "error": "保存失败"}

        applied, rejected = await _readback_persisted(preset)
        result: dict[str, Any] = {"success": True, "mode": mode, "applied": applied}
        if rejected:
            # All preset fields should be valid; rejected fields usually indicate drift in
            # _ALLOWED_CONVERSATION_SETTINGS requiring server updates.
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

        if not await asyncio.to_thread(save_global_conversation_settings, payload):
            return {"success": False, "error": "保存失败"}

        applied, rejected = await _readback_persisted(payload)
        result: dict[str, Any] = {"success": True, "applied": applied}
        if rejected:
            # Underlying validation dropped invalid field types/ranges, or old disk values differ from input.
            # Tell callers explicitly so they do not assume application succeeded.
            result["rejected"] = rejected
        if rejected_user_owned:
            # User-owned fields rejected: callers should guide users to configure them through the UI.
            result["rejected_user_owned"] = rejected_user_owned
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"更新主动搭话设置失败: {e}")
        return {"success": False, "error": "Internal server error"}

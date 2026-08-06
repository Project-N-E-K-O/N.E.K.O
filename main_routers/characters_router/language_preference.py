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

"""Per-character language preference for internal conversation templates."""

import asyncio
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse

from config import MEMORY_SERVER_PORT
from utils.internal_http_client import get_internal_http_client
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.preferences import aload_ui_language_override

from ..shared_state import get_config_manager, get_session_manager
from ._shared import _read_json_object_or_400, _validate_existing_character_path_name, logger, router
from .crud import _clear_character_recent_history


_character_language_preference_locks: dict[str, asyncio.Lock] = {}


def _get_character_language_preference_lock(name: str) -> asyncio.Lock:
    """Serialize persistence and live-session side effects per character."""
    lock = _character_language_preference_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _character_language_preference_locks[name] = lock
    return lock


async def _request_memory_prompt_locale(
    method: str,
    name: str,
    *,
    language: str | None = None,
) -> dict:
    client = get_internal_http_client()
    url = (
        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/prompt-locale/"
        f"{quote(name, safe='')}"
    )
    if method == "GET":
        response = await client.get(url, timeout=5.0)
    else:
        response = await client.put(
            url,
            json={"language": language},
            timeout=5.0,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Memory server rejected language preference")
    return payload


async def _load_existing_character(name: str) -> tuple[object, dict]:
    validation_error = _validate_existing_character_path_name(name)
    if validation_error:
        raise ValueError(validation_error)
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    if name not in (characters.get("猫娘") or {}):
        raise LookupError("角色不存在")
    return config_manager, characters


async def apply_character_language_preference(name: str, language: str) -> dict:
    """Persist one template locale and isolate the next turn from old context."""
    if not is_supported_language_code(language):
        raise ValueError("不支持的语言")
    normalized = normalize_language_code(language, format="full")
    config_manager, _characters = await _load_existing_character(name)
    async with _get_character_language_preference_lock(name):
        return await _apply_character_language_preference_serialized(
            name,
            normalized,
            config_manager,
        )


async def _apply_character_language_preference_serialized(
    name: str,
    normalized: str,
    config_manager: object,
) -> dict:
    memory_result = await _request_memory_prompt_locale(
        "PUT",
        name,
        language=normalized,
    )

    result = {
        "success": True,
        "language": normalized,
        "previous_language": memory_result.get("previous_language"),
        "changed": bool(memory_result.get("changed")),
        "recent_history_cleared": False,
        "session_reset": False,
    }
    if not result["changed"]:
        return result

    # This is intentionally a partial reset: recent conversation text can steer
    # language choice, while durable facts/persona must survive the preference
    # change.  The callback may run twice after a connector timeout: once as the
    # immediate fallback and once after a late settlement.  Both writes are
    # intentional, so the late old-session write can never become the final state.
    recent_clear_lock = asyncio.Lock()

    async def clear_recent_after_settlement() -> None:
        async with recent_clear_lock:
            await _clear_character_recent_history(config_manager, name)
            result["recent_history_cleared"] = True

    session_manager = get_session_manager()
    manager = session_manager.get(name) if session_manager else None
    if manager is not None:
        try:
            manager.set_user_language(normalized)
        except Exception as exc:
            logger.warning(
                "刷新当前会话语言失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但当前会话语言刷新失败",
            })
        expected_session = (
            getattr(manager, "session", None)
            if getattr(manager, "is_active", False)
            else None
        )
        if expected_session is not None:
            notify_session_ended = getattr(
                manager,
                "send_session_ended_by_server",
                None,
            )
            if callable(notify_session_ended):
                try:
                    await notify_session_ended()
                except Exception as exc:
                    logger.warning(
                        "语言切换前通知前端结束当前会话失败: name=%s err=%s",
                        name,
                        exc,
                        exc_info=True,
                    )
                    result.update({
                        "success": False,
                        "partial_success": True,
                        "error": "语言偏好已保存，但前端会话状态可能未完整重置",
                    })
        try:
            # Even an already-inactive manager may still have old messages queued
            # in cross_server.  end_session's optional barrier therefore runs for
            # both states and clears recent.json only after that queue is settled.
            await manager.end_session(
                by_server=True,
                expected_session=expected_session,
                after_memory_settlement=clear_recent_after_settlement,
            )
            if expected_session is not None:
                result["session_reset"] = True
        except Exception as exc:
            logger.warning(
                "语言切换后结算并结束当前会话失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            if expected_session is not None and not getattr(manager, "is_active", False):
                result["session_reset"] = True
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但当前会话未能完整重置",
            })
        reset_circuit = getattr(manager, "reset_session_start_circuit", None)
        if callable(reset_circuit):
            try:
                reset_circuit()
            except Exception as exc:
                logger.warning(
                    "重置语言切换后的会话熔断状态失败: name=%s err=%s",
                    name,
                    exc,
                )

    # No manager, a stale-session guard, or an early lifecycle failure can return
    # before the queued callback runs.  Clear immediately in that case; if a
    # barrier was queued, it remains armed and will clear once more after any
    # delayed old-session settlement.
    if not result["recent_history_cleared"]:
        try:
            await clear_recent_after_settlement()
        except Exception as exc:
            logger.warning(
                "清理语言切换前的近期上下文失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但近期上下文清理失败",
            })

    return result


@router.get("/character/{name}/language-preference")
async def get_character_language_preference(name: str):
    try:
        await _load_existing_character(name)
        payload = await _request_memory_prompt_locale("GET", name)
        ui_language = await aload_ui_language_override()
        payload["effective_language"] = (
            payload.get("language")
            or ui_language
            or payload.get("effective_language")
        )
        return payload
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=400,
        )
    except LookupError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=404,
        )
    except Exception as exc:
        logger.warning("读取角色语言偏好失败: name=%s err=%s", name, exc)
        return JSONResponse(
            {"success": False, "error": "读取语言偏好失败"},
            status_code=503,
        )


@router.put("/character/{name}/language-preference")
async def set_character_language_preference(name: str, request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    try:
        result = await apply_character_language_preference(
            name,
            payload.get("language"),
        )
        return JSONResponse(
            result,
            status_code=(
                200
                if result.get("success") or result.get("partial_success")
                else 500
            ),
        )
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=400,
        )
    except LookupError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=404,
        )
    except Exception as exc:
        logger.exception("保存角色语言偏好失败: name=%s", name)
        return JSONResponse(
            {"success": False, "error": "保存语言偏好失败"},
            status_code=503,
        )

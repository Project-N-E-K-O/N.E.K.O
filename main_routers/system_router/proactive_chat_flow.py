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

"""HTTP adapters and compatibility exports for proactive chat."""

import asyncio
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from main_logic.proactive_chat import service as proactive_service
from main_logic.proactive_chat.contracts import (
    PROACTIVE_REASON_ERROR_INTERNAL,
    PROACTIVE_REASON_ERROR_TIMEOUT,
    ProactiveChatCommand,
    _proactive_error_body,
)
from main_logic.proactive_chat.decisions import build_proactive_response
from main_logic.proactive_chat.mini_game_invite import (
    _push_mini_game_invite_resolved,
    _run_mini_game_invite_short_circuit,
)
from main_logic.proactive_chat.music_recommendation import (
    _record_music_played_through,
)

from ..shared_state import get_config_manager, get_session_manager
from ._shared import _validate_local_mutation_request, logger, router

__all__ = [
    "_PHASE1_FETCH_PER_SOURCE",
    "_PHASE1_TOTAL_TOPIC_TARGET",
    "_open_threads_for_activity_state",
    "_meme_proxy_candidate_fetchable",
    "_proactive_llm_retry_error_types",
    "_safe_fire_proactive_done",
    "_push_mini_game_invite_options",
    "_render_followup_topic_hooks",
    "_resolve_proactive_locale",
    "_resolve_topic_hook_locale",
    "build_proactive_response",
    "proactive_chat",
    "proactive_music_played_through",
]


# Transitional helper exports retained for callers/tests that historically
# imported internals from the Router module. The canonical owners now live in
# ``main_logic.proactive_chat``.
_proactive_llm_retry_error_types = proactive_service._proactive_llm_retry_error_types
_PHASE1_FETCH_PER_SOURCE = proactive_service._PHASE1_FETCH_PER_SOURCE
_PHASE1_TOTAL_TOPIC_TARGET = proactive_service._PHASE1_TOTAL_TOPIC_TARGET
_open_threads_for_activity_state = proactive_service._open_threads_for_activity_state
_render_followup_topic_hooks = proactive_service._render_followup_topic_hooks
_resolve_proactive_locale = proactive_service._resolve_proactive_locale
_resolve_topic_hook_locale = proactive_service._resolve_topic_hook_locale

_MEME_PROXY_CANDIDATE_CHECK_LIMIT = 3
_MEME_PROXY_CANDIDATE_TIMEOUT_SECONDS = 6.0


async def _meme_proxy_candidate_fetchable(url: str) -> tuple[bool, str]:
    """Return whether the existing meme proxy can fetch this candidate now."""
    if not url:
        return False, "missing_url"
    try:
        from .meme_proxy import fetch_meme_image_response

        response = await asyncio.wait_for(
            fetch_meme_image_response(url, write_cache=False),
            timeout=_MEME_PROXY_CANDIDATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return False, type(exc).__name__

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code < 200 or status_code >= 300:
        return False, f"proxy_status_{status_code}"
    media_type = str(getattr(response, "media_type", "") or "").lower()
    if not media_type.startswith("image/"):
        return False, f"proxy_media_type:{media_type or 'missing'}"
    if not (getattr(response, "body", b"") or b""):
        return False, "proxy_empty_body"
    return True, media_type


async def _safe_fire_proactive_done(scope: dict) -> None:
    """Preserve the legacy exception-path DONE compatibility helper."""
    mgr = scope.get("mgr")
    state_event = scope.get("_SE")
    emitted = scope.get("_proactive_done_emitted", False)
    if mgr is None or state_event is None or emitted:
        return
    try:
        await mgr.state.fire(state_event.PROACTIVE_DONE)
    except Exception as exc:
        logger.warning("safe_fire_proactive_done 异常: %s", exc)


async def _push_mini_game_invite_options(mgr: Any, payload: dict) -> None:
    """Send invite options at the Router/WebSocket boundary."""
    websocket = mgr.websocket
    if not websocket or not hasattr(websocket, "send_json"):
        return
    client_state = getattr(websocket, "client_state", None)
    if client_state is not None and client_state != client_state.CONNECTED:
        return
    await websocket.send_json(payload)


def _game_route_active_for(lanlan_name: str) -> bool:
    """Resolve the game-route collaborator lazily to avoid router cycles."""
    from main_routers.game_router import is_game_route_active

    return bool(is_game_route_active(lanlan_name))


def _adapt_result(result) -> JSONResponse:
    return JSONResponse(result.body, status_code=result.status_code)


@router.post("/proactive_chat")
async def proactive_chat(request: Request):
    """Validate HTTP input and delegate proactive behavior to the domain service."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        config_manager = get_config_manager()
        session_manager = get_session_manager()
        # Preserve both the legacy exception priority and snapshot timing:
        # character lookup and its exact nine-value unpack happen before the
        # request body is read.
        (
            master_name_current,
            her_name_current,
            character_data_2,
            character_data_3,
            character_data_4,
            lanlan_prompt_map,
            character_data_6,
            character_data_7,
            character_data_8,
        ) = await config_manager.aget_character_data()
        character_data = (
            master_name_current,
            her_name_current,
            character_data_2,
            character_data_3,
            character_data_4,
            lanlan_prompt_map,
            character_data_6,
            character_data_7,
            character_data_8,
        )
        payload = await request.json()
        command = ProactiveChatCommand.from_payload(payload)
        result = await proactive_service.handle_proactive_chat(
            command,
            config_manager=config_manager,
            session_manager=session_manager,
            character_data=character_data,
            game_route_active_for=_game_route_active_for,
            break_config_manager_provider=get_config_manager,
            run_mini_game_invite_short_circuit=(_run_mini_game_invite_short_circuit),
            push_mini_game_invite_options=_push_mini_game_invite_options,
            push_mini_game_invite_resolved=_push_mini_game_invite_resolved,
            meme_proxy_candidate_fetchable=_meme_proxy_candidate_fetchable,
        )
        return _adapt_result(result)
    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_TIMEOUT,
                error="AI处理超时",
            ),
            status_code=504,
        )
    except Exception as exc:
        logger.error("主动搭话接口异常: %s", exc)
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_INTERNAL,
                error="服务器内部错误",
                detail=str(exc),
            ),
            status_code=500,
        )


@router.post("/proactive/music_played_through")
async def proactive_music_played_through(request: Request):
    """Record completion feedback for a recommended song."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        config_manager = get_config_manager()
        (
            _,
            her_name_default,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
        ) = await config_manager.aget_character_data()
    except Exception:
        her_name_default = ""
    lanlan_name = (data.get("lanlan_name") or her_name_default or "").strip()
    if not lanlan_name:
        return JSONResponse(
            {"success": False, "error": "lanlan_name missing"},
            status_code=400,
        )
    cleared = _record_music_played_through(lanlan_name)
    return JSONResponse(
        {"success": True, "cleared": cleared, "lanlan_name": lanlan_name}
    )

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

"""HTTP adapter and compatibility facade for mini-game invites."""

from fastapi import Request
from fastapi.responses import JSONResponse

from ._shared import (
    _read_json_object,
    _set_no_store_headers,
    _validate_local_mutation_request,
    logger,
    router,
)
from ..shared_state import get_config_manager, get_session_manager
from main_logic.proactive_chat.mini_game_invite import (  # noqa: F401
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
    _KEYWORD_PATTERN_CACHE,
    _LETTER_ONLY_KW_RE,
    _apply_mini_game_invite_choice,
    _build_mini_game_invite_options_payload,
    _keyword_matches,
    _match_mini_game_invite_keyword,
    _maybe_apply_mini_game_invite_keyword,
    _maybe_deliver_mini_game_invite,
    _mini_game_invite_advance_response,
    _mini_game_invite_count_post_response_chat,
    _mini_game_invite_get_state,
    _mini_game_invite_in_cooldown,
    _mini_game_invite_record_delivered,
    _mini_game_invite_record_response_cooldown,
    _mini_game_invite_state,
    _mini_game_launch_url,
    _pick_mini_game_type,
    _push_mini_game_invite_resolved,
)


@router.post('/mini_game/invite/respond')
async def mini_game_invite_respond(request: Request):
    """Apply a frontend mini-game invite choice."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error
    data = payload if isinstance(payload, dict) else {}
    try:
        config_manager = get_config_manager()
        _, her_name_default, _, _, _, _, _, _, _ = (
            await config_manager.aget_character_data()
        )
    except Exception:
        her_name_default = ''
    lanlan_name = (data.get('lanlan_name') or her_name_default or '').strip()
    if not lanlan_name:
        return JSONResponse(
            {"success": False, "error": "lanlan_name missing"},
            status_code=400,
        )
    choice = (data.get('choice') or '').strip().lower()
    if choice not in ('accept', 'decline', 'later'):
        return JSONResponse(
            {
                "success": False,
                "error": (
                    "choice must be accept/decline/later, "
                    f"got {choice!r}"
                ),
            },
            status_code=400,
        )
    session_id = (data.get('session_id') or '').strip()
    state = _mini_game_invite_state.get(lanlan_name)
    pending_sid = state.get('pending_session_id') if state else None
    if not session_id or not pending_sid or session_id != pending_sid:
        return JSONResponse(
            {
                "success": True,
                "action": "expired",
                "message": (
                    "invite session expired or missing; "
                    "a newer invite or no pending invite exists"
                ),
            }
        )

    result = _apply_mini_game_invite_choice(
        lanlan_name,
        choice,
        source='button',
    )
    if result['action'] == 'ignored':
        return JSONResponse(
            {
                "success": True,
                "action": "expired",
                "message": result.get('reason') or 'no pending invite',
            }
        )
    try:
        mgr = get_session_manager().get(lanlan_name)
        if mgr is not None:
            await _push_mini_game_invite_resolved(
                mgr,
                session_id=session_id,
                action=result['action'],
            )
    except Exception as exc:
        logger.warning(
            "[%s] mini_game_invite_resolved WS push (button path) failed: %s",
            lanlan_name,
            exc,
        )
    return JSONResponse(
        {"success": True, **result, "lanlan_name": lanlan_name}
    )

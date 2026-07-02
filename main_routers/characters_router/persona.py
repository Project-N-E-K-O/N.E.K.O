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

"""Persona presets, onboarding state and per-character persona
selection endpoints.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import _json_no_store_response, _read_json_object_or_400, logger, router
from .crud import _clear_character_recent_history
from .notify import send_reload_page_notice

import asyncio
import copy
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import (
    get_config_manager,
    get_session_manager,
    get_init_one_catgirl,
)
from utils.config_manager import (
    delete_reserved,
    get_reserved,
    set_reserved,
    strip_generated_persona_selection_prompt,
)
from utils.initial_personality_state import (
    clear_manual_personality_reselect,
    load_initial_personality_state,
    mark_manual_personality_reselect,
    mark_initial_personality_state,
)
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.persona_presets import (
    build_persona_override_payload,
    get_persona_preset,
    list_persona_presets,
)


def _build_persona_selection_payload(character_payload: dict) -> dict:
    override = get_reserved(character_payload, "persona_override", default=None)
    if not isinstance(override, dict):
        return {
            "mode": "default",
            "preset_id": "",
            "source": "",
            "selected_at": "",
            "profile": {},
        }

    profile = override.get("profile")
    return {
        "mode": "override",
        "preset_id": str(override.get("preset_id") or "").strip(),
        "source": str(override.get("source") or "").strip(),
        "selected_at": str(override.get("selected_at") or "").strip(),
        "profile": dict(profile) if isinstance(profile, dict) else {},
    }


def _normalize_persona_request_language(raw_language: object) -> str | None:
    """Normalize the UI language carried by a persona selection request; invalid values stay None so downstream keeps its existing fallback."""
    raw = str(raw_language or "").strip()
    if not raw or not is_supported_language_code(raw):
        return None
    return normalize_language_code(raw, format="full")


def _get_persona_request_language(request: Request) -> str | None:
    """Extract the persona preset language from query params or Accept-Language."""
    language = request.query_params.get("language") or request.query_params.get("i18n_language")
    if language:
        normalized = _normalize_persona_request_language(language)
        if normalized is not None:
            return normalized
    accept_lang = request.headers.get("Accept-Language", "")
    if accept_lang:
        for language_part in accept_lang.split(","):
            normalized = _normalize_persona_request_language(language_part.split(";")[0].strip())
            if normalized is not None:
                return normalized
    return None


def _get_persona_payload_request_language(payload: object, request: Request) -> str | None:
    """Prefer the request-body language; fall back to query params and headers when invalid or missing."""
    body_language = None
    if isinstance(payload, dict):
        body_language = payload.get("i18n_language") or payload.get("language")
    if body_language:
        normalized = _normalize_persona_request_language(body_language)
        if normalized is not None:
            return normalized
    return _get_persona_request_language(request)


def _has_generated_persona_selection_prompt(prompt_text: object) -> bool:
    return isinstance(prompt_text, str) and "<NEKO_PERSONA_SELECTION>" in prompt_text


def _clear_stale_generated_persona_prompt(character_payload: dict) -> None:
    if not isinstance(character_payload, dict):
        return
    stored_prompt = get_reserved(
        character_payload,
        "system_prompt",
        default=None,
        legacy_keys=("system_prompt",),
    )
    if _has_generated_persona_selection_prompt(stored_prompt):
        cleaned_prompt = strip_generated_persona_selection_prompt(stored_prompt)
        if cleaned_prompt:
            set_reserved(character_payload, "system_prompt", cleaned_prompt)
        else:
            delete_reserved(character_payload, "system_prompt")


async def _rollback_character_persona_selection_change(config_manager, previous_characters: dict) -> None:
    await config_manager.asave_characters(previous_characters)


@router.get('/persona-presets')
async def list_persona_presets_route(request: Request):
    return _json_no_store_response({
        "success": True,
        "presets": list_persona_presets(lang=_get_persona_request_language(request)),
    })


@router.get('/persona-onboarding-state')
async def get_persona_onboarding_state():
    config_manager = get_config_manager()
    state = await asyncio.to_thread(load_initial_personality_state, config_manager)
    return _json_no_store_response({
        "success": True,
        "state": state,
    })


@router.post('/persona-onboarding-state')
async def set_persona_onboarding_state(request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    config_manager = get_config_manager()
    status_in = str((payload or {}).get("status") or "").strip()
    state = await asyncio.to_thread(
        mark_initial_personality_state,
        status_in,
        config_manager=config_manager,
    )
    # Telemetry：onboarding 漏斗的关键节点。**用归一化后的 state["status"]**
    # 而非请求体原值 status_in：mark_initial_personality_state 会把状态收敛成
    # 小枚举（pending / completed / skipped 等），客户端可以传任意 status 字符串
    # 但存储 fallback 成 pending。直接用 raw 会让任意输入变成不同的
    # onboarding_step dim，污染 funnel 切片 + 吃 instrument key 预算（同
    # lanlan_name 教训：raw 客户端输入不进 dim）（Codex）。
    _norm_status = (state.get("status") if isinstance(state, dict) else None) or "unknown"
    try:
        from utils.instrument import event as _instr_event, counter as _instr_counter
        _instr_event("onboarding_step", status=str(_norm_status)[:32])
        _instr_counter("onboarding_step", status=str(_norm_status)[:32])
    except Exception:
        # 埋点失败绝不影响 onboarding endpoint —— 一条 telemetry 走丢比让
        # 用户卡在角色选择失败重要多了。日志也省，防 import 故障刷屏。
        pass
    return {
        "success": True,
        "state": state,
    }


@router.post('/persona-reselect-current')
async def request_current_character_persona_reselect():
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    current_character_name = str(characters.get('当前猫娘') or '').strip()
    if not current_character_name:
        return JSONResponse({'success': False, 'error': '当前没有可用角色'}, status_code=400)

    state = await asyncio.to_thread(
        mark_manual_personality_reselect,
        current_character_name,
        config_manager=config_manager,
    )
    return {
        "success": True,
        "state": state,
    }


@router.delete('/persona-reselect-current')
async def clear_current_character_persona_reselect():
    config_manager = get_config_manager()
    state = await asyncio.to_thread(
        clear_manual_personality_reselect,
        config_manager=config_manager,
    )
    return {
        "success": True,
        "state": state,
    }


@router.get('/character/{name}/persona-selection')
async def get_character_persona_selection(name: str):
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    return _json_no_store_response({
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    })


@router.put('/character/{name}/persona-selection')
async def update_character_persona_selection(name: str, request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    preset_id = str((payload or {}).get("preset_id") or "").strip()
    source = str((payload or {}).get("source") or "").strip()
    preset = get_persona_preset(preset_id)
    if preset is None:
        return JSONResponse({'success': False, 'error': '无效的人格预设'}, status_code=400)

    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    selected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    request_language = _get_persona_payload_request_language(payload, request)
    override_payload = build_persona_override_payload(
        preset_id,
        source=source,
        selected_at=selected_at,
        lang=request_language,
    )
    if override_payload is None:
        return JSONResponse({'success': False, 'error': '无效的人格预设'}, status_code=400)

    previous_characters = copy.deepcopy(characters)
    set_reserved(character_payload, "persona_override", override_payload)
    _clear_stale_generated_persona_prompt(character_payload)
    try:
        await config_manager.asave_characters(characters)
        await _clear_character_recent_history(config_manager, name)
        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        mgr = session_manager[name] if is_current_catgirl and name in session_manager else None
        expected_session = getattr(mgr, "session", None) if mgr and mgr.is_active else None
        if expected_session is not None:
            await send_reload_page_notice(mgr, "人格设定已更新，页面即将刷新")
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
            except Exception as e:
                logger.error(f"结束 session 时出错: {e}")

        initialize_one_character = get_init_one_catgirl()
        await initialize_one_character(name, is_new=False)
        if source == "manual_reselect":
            await asyncio.to_thread(
                clear_manual_personality_reselect,
                config_manager=config_manager,
            )
        elif source == "onboarding":
            await asyncio.to_thread(
                mark_initial_personality_state,
                "completed",
                config_manager=config_manager,
            )
    except Exception:
        await _rollback_character_persona_selection_change(config_manager, previous_characters)
        raise

    return {
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    }


@router.delete('/character/{name}/persona-selection')
async def clear_character_persona_selection(name: str):
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    previous_characters = copy.deepcopy(characters)
    delete_reserved(character_payload, "persona_override")
    _clear_stale_generated_persona_prompt(character_payload)
    try:
        await config_manager.asave_characters(characters)
        await _clear_character_recent_history(config_manager, name)

        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        mgr = session_manager[name] if is_current_catgirl and name in session_manager else None
        expected_session = getattr(mgr, "session", None) if mgr and mgr.is_active else None
        if expected_session is not None:
            await send_reload_page_notice(mgr, "人格设定已更新，页面即将刷新")
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
            except Exception as e:
                logger.error(f"结束 session 时出错: {e}")

        initialize_one_character = get_init_one_catgirl()
        await initialize_one_character(name, is_new=False)
    except Exception:
        await _rollback_character_persona_selection_change(config_manager, previous_characters)
        raise

    return {
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    }

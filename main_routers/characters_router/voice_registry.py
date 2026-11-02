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

"""Voice id registration/unregistration endpoints and voice session
state responses.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import _json_no_store_response, _validate_existing_character_path_name, logger, router
from .notify import send_reload_page_notice

import asyncio
from datetime import datetime
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import (
    get_config_manager,
    get_session_manager,
    get_initialize_character_data,
    get_init_one_catgirl,
)
from main_logic.tts_client import (
    get_custom_tts_voices,
    CustomTTSVoiceFetchError,
)
from utils.config_manager import (
    ensure_default_yui_voice_for_free_api,
    get_reserved,
    set_reserved,
)
from utils.voice_config import read_legacy_voice_id


VOICE_SESSION_STARTING_ERROR = "语音会话正在启动，请稍后再切换音色"


def _voice_session_starting_response():
    return JSONResponse(
        {
            "success": False,
            "code": "VOICE_SESSION_STARTING",
            "error": VOICE_SESSION_STARTING_ERROR,
            "retryable": True,
        },
        status_code=409,
    )


def _is_current_catgirl_voice_session_starting(name: str, characters, session_manager) -> bool:
    if name != characters.get("当前猫娘", ""):
        return False
    mgr = session_manager.get(name) if session_manager else None
    if not mgr:
        return False
    return bool(
        getattr(mgr, "is_starting", False)
        and not getattr(mgr, "is_active", False)
        and (getattr(mgr, "starting_input_mode", None) or getattr(mgr, "input_mode", "")) == "audio"
    )


@router.put('/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    if 'voice_id' not in data:
        logger.debug("猫娘 %s 的 voice_id 更新请求缺少字段，按无变更处理", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    voice_id = str(data.get('voice_id') or '').strip()
    old_voice_id = read_legacy_voice_id(get_reserved(
        characters['猫娘'][name],
        'voice_id',
        default='',
        legacy_keys=('voice_id',)
    ))

    # Idempotency guard: return immediately when the submitted value is unchanged, avoiding a session reset with no actual change.
    if old_voice_id == voice_id:
        logger.info("猫娘 %s 的 voice_id 未变化，跳过会话重置流程", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}

    if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
        return _voice_session_starting_response()

    # Check whether voice_id exists in voice_storage.
    if not _config_manager.validate_voice_id(voice_id):
        voices = _config_manager.get_voices_for_current_api()
        available_voices = list(voices.keys())
        return JSONResponse({
            'success': False,
            'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
            'available_voices': available_voices
        }, status_code=400)

    # User-selected voice: lazily migrate this entry to the structured object (migrate each entry only when used; see voice_id_to_storage_value).
    set_reserved(characters['猫娘'][name], 'voice_id', _config_manager.voice_id_to_storage_value(voice_id))
    await _config_manager.asave_characters(characters)

    # If this is the currently active catgirl, notify the frontend first, then close the session.
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False

    if is_current_catgirl and name in session_manager:
        # Check whether there is an active session.
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新（{old_voice_id} -> {voice_id}），准备结束当前语音会话...")

            # 1. Notify the frontend to follow the session-ending path, avoiding a full-page reload in Electron when switching voices.
            notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
            if callable(notify_session_ended):
                await notify_session_ended()

            # 2. Close the session immediately (this disconnects the WebSocket).
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
            # After switching voices, the previous session's accumulated failure count / circuit breaker no longer applies:
            # The user's next start_session should be a fresh attempt; otherwise,
            # this SessionManager instance will still silently block it using the old failure count / circuit breaker.
            session_manager[name].reset_session_start_circuit()

    # Fast path: refresh only the edited character's session_manager (voice_id), without iterating over the other N-1 entries.
    # The non-current-character branch must also explicitly refresh session_manager[name]: it previously relied on the next switch's full initialization to
    # rescue it, but set_current_catgirl has switched to switch_current_catgirl_fast, so that rescue no longer happens;
    # voice_id must be written into session_manager[name] here (init_one_catgirl writes only this key,
    # and does not affect the current session).
    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(name, is_new=False)
    if is_current_catgirl:
        logger.info("配置已重新加载，新的voice_id已生效")
    else:
        logger.info(f"非当前猫娘 {name} 的音色已更新并同步到 session_manager")

    return {"success": True, "session_restarted": session_ended, "voice_id_changed": True}


@router.get('/catgirl/{name}/voice_mode_status')
async def get_catgirl_voice_mode_status(name: str):
    """Check whether the specified character is in voice mode."""
    if _validate_existing_character_path_name(name):
        return _json_no_store_response({
            'is_voice_mode': False,
            'is_current': False,
            'is_active': False,
            'is_starting': False,
            'is_voice_starting': False,
            'invalid_name': True,
        })
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    is_current = characters.get('当前猫娘') == name

    if name not in session_manager:
        return _json_no_store_response({
            'is_voice_mode': False,
            'is_current': is_current,
            'is_active': False,
            'is_starting': False,
            'is_voice_starting': False,
        })

    mgr = session_manager[name]
    is_active = mgr.is_active if mgr else False
    is_starting = bool(getattr(mgr, 'is_starting', False)) if mgr else False
    is_audio_starting = _is_current_catgirl_voice_session_starting(name, characters, session_manager)

    is_voice_mode = is_audio_starting
    if is_active and mgr:
        # Check whether this is voice mode (determined by the session type).
        from main_logic.omni_realtime_client import OmniRealtimeClient
        is_voice_mode = is_voice_mode or bool(
            getattr(mgr, 'input_mode', '') == 'audio'
            or (mgr.session and isinstance(mgr.session, OmniRealtimeClient))
        )

    return _json_no_store_response({
        'is_voice_mode': is_voice_mode,
        'is_current': is_current,
        'is_active': is_active,
        'is_starting': is_starting,
        'is_voice_starting': is_audio_starting,
    })


@router.post('/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """Unregister the catgirl's voice."""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        characters = await _config_manager.aload_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        # Check whether voice_id is already set.
        old_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
        if not old_voice_id:
            return JSONResponse({'success': False, 'error': 'TTS_VOICE_NOT_REGISTERED', 'code': 'TTS_VOICE_NOT_REGISTERED'}, status_code=400)

        if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
            return _voice_session_starting_response()

        # COMPAT(v1->v2): consistently store in _reserved.voice_id; the legacy flat voice_id is no longer written or deleted.
        set_reserved(characters['猫娘'][name], 'voice_id', '')
        await _config_manager.asave_characters(characters)

        # If this is the currently active catgirl, notify the frontend first, then close the session.
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        session_ended = False

        if is_current_catgirl and name in session_manager:
            if session_manager[name].is_active:
                logger.info(f"检测到 {name} 的voice_id已清空（{old_voice_id} -> ''），准备结束当前语音会话...")
                notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
                if callable(notify_session_ended):
                    await notify_session_ended()
                try:
                    await session_manager[name].end_session(by_server=True)
                    session_ended = True
                    logger.info(f"{name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束session时出错: {e}")
                # Symmetric with the set_voice_id path: clear the previous session's failure count / circuit breaker,
                # or the next start_session will be silently blocked by the old circuit breaker.
                session_manager[name].reset_session_start_circuit()

        # Fast path: refresh only the edited character's session_manager (voice_id), without iterating over the other N-1 entries.
        # The non-current-character branch must also call init_one_catgirl: it previously relied on the next switch's full initialization to rescue it,
        # but set_current_catgirl has switched to switch_current_catgirl_fast, so that rescue no longer happens.
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)

        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除", "session_restarted": session_ended, "voice_id_changed": True}

    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)


@router.post('/clear_voice_ids')
async def clear_voice_ids():
    """Clear all characters' local voice ID records."""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        cleared_count = 0

        # Clear voice_id for all catgirls.
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))):
                    set_reserved(characters['猫娘'][name], 'voice_id', '')
                    cleared_count += 1

        await _config_manager.asave_characters(characters)
        await ensure_default_yui_voice_for_free_api(_config_manager)
        # Automatically reload the configuration.
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()

        return JSONResponse({
            'success': True,
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)


@router.get('/custom_tts_voices')
async def list_custom_tts_voices_for_characters(provider: str = ''):
    """Return custom GPT-SoVITS voices for the character UI."""
    try:
        _config_manager = get_config_manager()
        core_config = await _config_manager.aget_core_config()
        tts_config = await _config_manager.aget_model_api_config(
            'tts_custom', core_config=core_config
        )

        base_url = (
            tts_config.get('base_url')
            or tts_config.get('url')
            or core_config.get('ttsModelUrl')
            or core_config.get('TTS_MODEL_URL')
            or ''
        )
        if tts_config.get('is_enabled') is False or core_config.get('GPTSOVITS_ENABLED') is False:
            return JSONResponse({
                'success': False,
                'error': 'GPTSOVITS_NOT_ENABLED',
                'code': 'GPTSOVITS_NOT_ENABLED',
                'voices': []
            }, status_code=400)
        if not tts_config.get('is_custom'):
            return JSONResponse({
                'success': False,
                'error': 'CUSTOM_API_NOT_ENABLED',
                'code': 'CUSTOM_API_NOT_ENABLED',
                'voices': []
            }, status_code=400)
        if not base_url or not (base_url.startswith('http://') or base_url.startswith('https://')):
            return JSONResponse({
                'success': False,
                'error': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'code': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'voices': []
            }, status_code=400)

        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(base_url)
        host = parsed.hostname or ''
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)
        except ValueError:
            if host not in ('localhost',):
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)

        voices = await get_custom_tts_voices(base_url, provider='gptsovits')
        return JSONResponse({
            'success': True,
            'provider': 'gptsovits',
            'voices': voices,
            'api_url': base_url
        })
    except (CustomTTSVoiceFetchError, ValueError) as e:
        error_text = str(e)
        return JSONResponse({
            'success': False,
            'error': f'连接 GPT-SoVITS API 失败: {error_text}',
            'voices': []
        }, status_code=502)
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'获取 GPT-SoVITS 声音列表失败: {str(e)}',
            'voices': []
        }, status_code=500)


@router.post('/voices')
async def register_voice(request: Request):
    """Register a new voice."""
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')

        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': 'TTS_VOICE_REGISTER_MISSING_PARAMS',
                'code': 'TTS_VOICE_REGISTER_MISSING_PARAMS'
            }, status_code=400)

        # Prepare voice data.
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }

        try:
            _config_manager = get_config_manager()
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            logger.warning(f"保存音色配置失败: {e}")
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)

        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.delete('/voices/{voice_id}')
async def delete_voice(voice_id: str):
    """Delete the specified voice."""
    try:
        _config_manager = get_config_manager()
        try:
            deleted = await asyncio.to_thread(_config_manager.delete_voice_for_current_api, voice_id)
        except ValueError as exc:
            if exc.args != ("VOICE_OPERATION_IN_PROGRESS",):
                raise
            return _json_no_store_response({
                "success": False, "code": "OPERATION_IN_PROGRESS", "error": "OPERATION_IN_PROGRESS",
            }, status_code=409)

        if deleted:
            # Remove references to this voice from all characters.
            _config_manager = get_config_manager()
            session_manager = get_session_manager()
            characters = await _config_manager.aload_characters()
            cleaned_count = 0
            affected_active_names = []

            if '猫娘' in characters:
                for name in characters['猫娘']:
                    if read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))) == voice_id:
                        set_reserved(characters['猫娘'][name], 'voice_id', '')
                        cleaned_count += 1

                        # Check whether this character has the currently active session.
                        if name in session_manager and session_manager[name].is_active:
                            affected_active_names.append(name)

            if cleaned_count > 0:
                await _config_manager.asave_characters(characters)

                # For affected active characters, notify and end sessions in parallel (each end_session takes about 1 second).
                async def _refresh_one(name):
                    logger.info(f"检测到活跃角色 {name} 的 voice_id 已被删除，准备结束当前语音会话...")
                    # 1. Notify the frontend to follow the session-ending path, avoiding a full-page reload in Electron when switching voices.
                    notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
                    if callable(notify_session_ended):
                        await notify_session_ended()
                    # 2. End the session.
                    try:
                        await session_manager[name].end_session(by_server=True)
                        logger.info(f"已结束受影响角色 {name} 的 session")
                    except Exception as e:
                        logger.error(f"结束受影响角色 {name} 的 session 时出错: {e}")
                    # Symmetric with the set_voice_id path: clear the previous session's failure count / circuit breaker,
                    # or the next start_session will be silently blocked by the old circuit breaker.
                    session_manager[name].reset_session_start_circuit()

                if affected_active_names:
                    await asyncio.gather(
                        *(_refresh_one(name) for name in affected_active_names),
                        return_exceptions=True,
                    )

                # Automatically reload the configuration.
                initialize_character_data = get_initialize_character_data()
                await initialize_character_data()

            logger.info(f"已删除音色 '{voice_id}'，并清理了 {cleaned_count} 个角色的引用")
            return {
                "success": True,
                "message": f"音色已删除，已清理 {cleaned_count} 个角色的引用"
            }
        else:
            return JSONResponse({
                'success': False,
                'error': '音色不存在或删除失败'
            }, status_code=404)
    except Exception as e:
        logger.error(f"删除音色时出错: {e}")
        return JSONResponse({
            'success': False,
            'error': f'删除音色失败: {str(e)}'
        }, status_code=500)

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

    # 幂等保护：提交同值时直接返回，避免无实际变更触发会话重置。
    if old_voice_id == voice_id:
        logger.info("猫娘 %s 的 voice_id 未变化，跳过会话重置流程", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}

    if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
        return _voice_session_starting_response()

    # 验证voice_id是否在voice_storage中
    if not _config_manager.validate_voice_id(voice_id):
        voices = _config_manager.get_voices_for_current_api()
        available_voices = list(voices.keys())
        return JSONResponse({
            'success': False,
            'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
            'available_voices': available_voices
        }, status_code=400)

    # 用户设音色：惰性迁移这一条到结构对象（用到哪条迁哪条，见 voice_id_to_storage_value）。
    set_reserved(characters['猫娘'][name], 'voice_id', _config_manager.voice_id_to_storage_value(voice_id))
    await _config_manager.asave_characters(characters)

    # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False

    if is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新（{old_voice_id} -> {voice_id}），准备结束当前语音会话...")

            # 1. 通知前端按 session 结束路径收口，避免 Electron 为音色切换整页重载。
            notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
            if callable(notify_session_ended):
                await notify_session_ended()

            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
            # 切音色后，前一会话累计的失败计数 / 熔断不再适用：
            # 用户的下一次 start_session 应是全新尝试，否则
            # 这条 SessionManager 实例还会被旧失败计数 / 熔断继续静默拦截。
            session_manager[name].reset_session_start_circuit()

    # Fast path：只刷新被编辑角色的 session_manager（voice_id），不遍历其它 N-1 个。
    # 非当前角色分支也要显式刷 session_manager[name]：以前靠下次 switch 的全量 init 顺带
    # rescue，但 set_current_catgirl 已切到 switch_current_catgirl_fast，rescue 不再发生，
    # 必须在这里就把 voice_id 写进 session_manager[name]（init_one_catgirl 只写该 key，
    # 不会影响当前 session）。
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
        # 检查是否是语音模式（通过session类型判断）
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

        # 检查是否已有voice_id
        old_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
        if not old_voice_id:
            return JSONResponse({'success': False, 'error': 'TTS_VOICE_NOT_REGISTERED', 'code': 'TTS_VOICE_NOT_REGISTERED'}, status_code=400)

        if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
            return _voice_session_starting_response()

        # COMPAT(v1->v2): 统一落到 _reserved.voice_id，旧平铺 voice_id 不再写入/删除。
        set_reserved(characters['猫娘'][name], 'voice_id', '')
        await _config_manager.asave_characters(characters)

        # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
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
                # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
                # 否则下一次 start_session 会被旧熔断静默拦截。
                session_manager[name].reset_session_start_circuit()

        # Fast path：只刷新被编辑角色的 session_manager（voice_id），不遍历其它 N-1 个。
        # 非当前角色分支也要走 init_one_catgirl：以前靠下次 switch 的全量 init 顺带 rescue，
        # 但 set_current_catgirl 已切到 switch_current_catgirl_fast，rescue 不再发生。
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

        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))):
                    set_reserved(characters['猫娘'][name], 'voice_id', '')
                    cleared_count += 1

        await _config_manager.asave_characters(characters)
        await ensure_default_yui_voice_for_free_api(_config_manager)
        # 自动重新加载配置
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
        tts_config = _config_manager.get_model_api_config('tts_custom')

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

        # 准备音色数据
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
        deleted = _config_manager.delete_voice_for_current_api(voice_id)

        if deleted:
            # 清理所有角色中使用该音色的引用
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

                        # 检查该角色是否是当前活跃的 session
                        if name in session_manager and session_manager[name].is_active:
                            affected_active_names.append(name)

            if cleaned_count > 0:
                await _config_manager.asave_characters(characters)

                # 对于受影响的活跃角色，并行通知 + 结束 session（每个 end_session ≈ 1s）
                async def _refresh_one(name):
                    logger.info(f"检测到活跃角色 {name} 的 voice_id 已被删除，准备结束当前语音会话...")
                    # 1. 通知前端按 session 结束路径收口，避免 Electron 为音色切换整页重载。
                    notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
                    if callable(notify_session_ended):
                        await notify_session_ended()
                    # 2. 结束 session
                    try:
                        await session_manager[name].end_session(by_server=True)
                        logger.info(f"已结束受影响角色 {name} 的 session")
                    except Exception as e:
                        logger.error(f"结束受影响角色 {name} 的 session 时出错: {e}")
                    # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
                    # 否则下一次 start_session 会被旧熔断静默拦截。
                    session_manager[name].reset_session_start_circuit()

                if affected_active_names:
                    await asyncio.gather(
                        *(_refresh_one(name) for name in affected_active_names),
                        return_exceptions=True,
                    )

                # 自动重新加载配置
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

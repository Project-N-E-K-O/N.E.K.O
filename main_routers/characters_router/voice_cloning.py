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

"""Voice Clone endpoints: upload trimming/silence analysis and direct-link clone.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import MAX_UPLOAD_SIZE, _UploadTooLargeError, _read_limited_stream, logger, router
from .direct_link import (
    DirectLinkSecurityError,
    _download_direct_link_audio,
    _request_direct_link_follow_redirects,
    _validate_direct_link_target,
)
from .voice_providers import (
    ElevenLabsUpstreamError,
    _elevenlabs_clone_voice,
    _get_elevenlabs_base_url,
    _is_local_voice_clone_tts_config,
    _local_voice_clone_tts_base_url,
    _raw_elevenlabs_voice_id,
)

import io
import re
import asyncio
import base64
import hashlib
from datetime import datetime
from fastapi import Request, File, UploadFile, Form
from fastapi.responses import JSONResponse
import httpx
from ..shared_state import (
    get_config_manager,
)
from utils.config_manager import _as_bool
from utils.audio import normalize_voice_clone_api_audio, validate_audio_file
from utils.doubao_tts import (
    DOUBAO_TTS_DEFAULT_BASE_URL,
    DOUBAO_TTS_DEFAULT_RESOURCE_ID,
    DOUBAO_VOICE_STORAGE_KEY,
    DoubaoTtsError,
    DoubaoVoiceCloneClient,
)
from utils.glm_tts import (
    GLM_TTS_DEFAULT_BASE_URL,
    GLM_VOICE_CLONE_MAX_AUDIO_BYTES,
    GLM_VOICE_STORAGE_KEY,
    GlmTtsError,
    GlmVoiceCloneClient,
    build_glm_voice_name,
)
from utils.voice_clone import (
    MinimaxVoiceCloneClient,
    MinimaxVoiceCloneError,
    minimax_normalize_language,
    MimoVoiceCloneClient,
    MimoVoiceCloneError,
    QwenVoiceCloneClient,
    QwenVoiceCloneError,
    qwen_language_hints,
)
from utils.tts.providers.minimax import (
    build_minimax_request_voice_id,
    get_minimax_base_url,
    get_minimax_storage_prefix,
)
from utils.tts.providers.mimo import MIMO_VOICE_STORAGE_KEY
from config import (
    TFLINK_UPLOAD_URL,
)


# ==================== Intelligent silence removal ====================
# Global dictionary storing trim-task state.
_trim_tasks: dict[str, dict] = {}


@router.post('/audio/analyze_silence')
async def analyze_silence(file: UploadFile = File(...)):
    """
    Analyze silence segments in the uploaded audio.

    Returns:
        - original_duration / original_duration_ms: total duration of the original audio
        - silence_duration / silence_duration_ms: total detected silence (total_silence_ms)
        - removable_silence / removable_silence_ms: silence that can actually be removed
        - estimated_duration / estimated_duration_ms: estimated remaining duration after processing
        - saving_percentage: savings percentage (based on the actually removable amount)
        - silence_segments: list of silence segments [{start_ms, end_ms, duration_ms}]
        - has_silence: whether removable silence was detected
    """
    from utils.audio_silence_remover import (
        detect_silence, convert_to_wav_if_needed, format_duration_mmss
    )

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # Convert to WAV if needed; offload blocking work to a thread.
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # Detect silence.
        analysis = await asyncio.to_thread(detect_silence, wav_buffer)

        return JSONResponse({
            'success': True,
            'original_duration': format_duration_mmss(analysis.original_duration_ms),
            'original_duration_ms': round(analysis.original_duration_ms, 1),
            'silence_duration': format_duration_mmss(analysis.total_silence_ms),
            'silence_duration_ms': round(analysis.total_silence_ms, 1),
            'removable_silence': format_duration_mmss(analysis.removable_silence_ms),
            'removable_silence_ms': round(analysis.removable_silence_ms, 1),
            'estimated_duration': format_duration_mmss(analysis.estimated_duration_ms),
            'estimated_duration_ms': round(analysis.estimated_duration_ms, 1),
            'saving_percentage': analysis.saving_percentage,
            'silence_segments': [
                {
                    'start_ms': round(s.start_ms, 1),
                    'end_ms': round(s.end_ms, 1),
                    'duration_ms': round(s.duration_ms, 1),
                }
                for s in analysis.silence_segments
            ],
            'has_silence': len(analysis.silence_segments) > 0,
            'sample_rate': analysis.sample_rate,
            'sample_width': analysis.sample_width,
            'channels': analysis.channels,
        })
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"静音分析失败: {e}")
        return JSONResponse({'error': f'静音分析失败: {str(e)}'}, status_code=500)


@router.post('/audio/trim_silence')
async def trim_silence_endpoint(file: UploadFile = File(...), task_id: str | None = Form(default=None)):
    """
    Perform silence trimming and return the processed audio.

    Analyzes silence segments first, then shrinks over-long silences down to 200ms (cut from the middle).
    Returns the processed WAV file (base64-encoded) plus an MD5 checksum.
    """
    import uuid
    import base64 as b64
    from utils.audio_silence_remover import (
        detect_silence, trim_silence, convert_to_wav_if_needed,
        format_duration_mmss, CancelledError
    )

    if task_id:
        try:
            uuid.UUID(task_id)
        except ValueError:
            return JSONResponse({'error': '无效的 task_id 格式'}, status_code=400)
        if task_id in _trim_tasks:
            return JSONResponse({'error': '该 task_id 已存在'}, status_code=409)
    else:
        task_id = str(uuid.uuid4())

    # Reserve immediately to prevent TOCTOU races.
    _trim_tasks[task_id] = {'progress': 0, 'cancelled': False, 'phase': 'queued'}

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # File read complete: enter analysis.
        _trim_tasks[task_id]['phase'] = 'analyzing'

        def progress_cb(pct: int):
            task = _trim_tasks.get(task_id)
            if task is None:
                return
            if task.get('phase', 'analyzing') == 'analyzing':
                # Analysis occupies 0-40% progress.
                task['progress'] = int(pct * 0.4)
            else:
                # Trimming occupies 40-100% progress.
                task['progress'] = 40 + int(pct * 0.6)

        def cancel_check() -> bool:
            return _trim_tasks.get(task_id, {}).get('cancelled', False)

        # Convert to WAV; offload blocking work to a thread.
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # Analyze silence.
        analysis = await asyncio.to_thread(
            detect_silence, wav_buffer,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        if not analysis.silence_segments:
            # No removable silence.
            _trim_tasks.pop(task_id, None)
            return JSONResponse({
                'success': True,
                'has_changes': False,
                'message': '未检测到可移除的静音段',
                'task_id': task_id,
            })

        # Enter trimming.
        if task_id in _trim_tasks:
            _trim_tasks[task_id]['phase'] = 'trimming'

        # Perform trimming.
        result = await asyncio.to_thread(
            trim_silence, wav_buffer, analysis,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        # Encode as base64.
        audio_b64 = b64.b64encode(result.audio_data).decode('ascii')

        # Clean up tasks.
        _trim_tasks.pop(task_id, None)

        return JSONResponse({
            'success': True,
            'has_changes': True,
            'task_id': task_id,
            'audio_base64': audio_b64,
            'md5': result.md5,
            'original_duration': format_duration_mmss(result.original_duration_ms),
            'original_duration_ms': round(result.original_duration_ms, 1),
            'trimmed_duration': format_duration_mmss(result.trimmed_duration_ms),
            'trimmed_duration_ms': round(result.trimmed_duration_ms, 1),
            'removed_silence_ms': round(result.removed_silence_ms, 1),
            'sample_rate': result.sample_rate,
            'sample_width': result.sample_width,
            'channels': result.channels,
            'filename': f"trimmed_{file.filename}",
        })

    except CancelledError:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({
            'success': False,
            'cancelled': True,
            'message': '任务已被用户取消',
            'task_id': task_id,
        })
    except ValueError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"静音裁剪失败: {e}")
        return JSONResponse({'error': f'静音裁剪失败: {str(e)}'}, status_code=500)


@router.get('/audio/trim_progress/{task_id}')
async def get_trim_progress(task_id: str):
    """Get trim task progress."""
    task = _trim_tasks.get(task_id)
    if not task:
        return JSONResponse({'exists': False, 'progress': 100, 'phase': 'done'})
    return JSONResponse({
        'exists': True,
        'progress': task.get('progress', 0),
        'phase': task.get('phase', 'unknown'),
        'cancelled': task.get('cancelled', False),
    })


@router.post('/audio/trim_cancel/{task_id}')
async def cancel_trim_task(task_id: str):
    """Cancel a trim task."""
    task = _trim_tasks.get(task_id)
    if task:
        task['cancelled'] = True
        return JSONResponse({'success': True, 'message': '取消请求已发送'})
    return JSONResponse({'success': False, 'message': '任务不存在或已完成'})


def _normalize_doubao_voice_clone_speaker_id(value: str) -> str:
    speaker_id = str(value or '').strip()
    if not re.fullmatch(r"S_[A-Za-z0-9]+", speaker_id):
        raise ValueError("豆包声音复刻需要填写 S_ 开头的 Speaker ID")
    return speaker_id


@router.post('/voice_clone')
async def voice_clone(
    file: UploadFile = File(...),
    prefix: str = Form(...),
    ref_language: str = Form(default="ch"),
    provider: str = Form(default="cosyvoice"),
    ref_text: str = Form(default=""),
):
    """
    Voice cloning endpoint.

    Parameters:
        file: audio file
        prefix: voice prefix name
        ref_language: language of the reference audio; one of: ch, en, fr, de, ja, ko, ru
                      Note: this is the language of the reference audio, not the target voice
        provider: service provider; one of: cosyvoice (Alibaba Bailian), cosyvoice_intl (Alibaba international), minimax (China), minimax_intl (international), elevenlabs, mimo, vllm_omni, doubao_tts, glm_tts (Zhipu GLM voice clone)
        ref_text: transcript of the reference audio (vLLM-Omni inline clone only; must
                  correspond strictly to the audio content)
    """
    # Stream uploaded files with size limits while incrementally computing MD5.
    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取文件到内存失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    audio_md5 = hashlib.md5(file_buffer.getvalue()).hexdigest()

    # Normalize provider and ref_language early.
    provider = provider.lower().strip() if provider else 'cosyvoice'
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    ref_language = ref_language.lower().strip() if ref_language else 'ch'
    if ref_language not in valid_languages:
        ref_language = 'ch'

    # vLLM-Omni cloning requires ref_text matching ref_audio exactly; missing text
    # causes synthesis failure with server ValueError. Frontend voice_clone.js already validates
    # this at L1484-1491; backend validation prevents direct API bypass.
    vllm_ref_text = ref_text.strip() if ref_text else ''
    if provider == 'vllm_omni':
        if not vllm_ref_text:
            return JSONResponse(
                {'error': 'vLLM-Omni 克隆必须填写参考音频原文（ref_text）', 'provider': provider},
                status_code=400,
            )
        if len(vllm_ref_text) > 100:
            return JSONResponse(
                {'error': 'vLLM-Omni 参考音频原文过长，请控制在 100 字以内', 'provider': provider},
                status_code=400,
            )

    # Detect local TTS through ws/wss protocol.
    _config_manager = get_config_manager()
    # Snapshot core_config first and derive tts_custom from the same snapshot. Independent reads interrupted by
    # /core_api saves can combine old tts_config URLs in _local_voice_clone_tts_base_url with
    # new ttsModelProvider in _is_local_voice_clone_tts_config, skipping local registration
    # or contacting stale endpoints.
    try:
        core_config = await _config_manager.aget_core_config() or {}
    except Exception:
        core_config = {}
    # Without a snapshot, fall back to independent reads for existing fault tolerance; never pass {} as a snapshot.
    tts_config = await (
        _config_manager.aget_model_api_config('tts_custom', core_config=core_config)
        if core_config
        else _config_manager.aget_model_api_config('tts_custom')
    )
    base_url = _local_voice_clone_tts_base_url(tts_config, core_config)
    is_local_tts = _is_local_voice_clone_tts_config(tts_config, core_config)

    # vLLM-Omni uses an inline reference sample and has no /speakers/register
    # endpoint. Even when another active TTS provider is a local WS service,
    # its generic registration route must not consume a vLLM-Omni clone.
    # Same for GLM: a glm_tts clone registers remotely at bigmodel.cn — routing
    # it into the local /v1/speakers/register flow would silently produce a
    # local-speaker clone instead of calling the GLM voice-clone API.
    if is_local_tts and provider not in ('vllm_omni', 'glm_tts'):
        # ==================== Local TTS registration ====================
        # Deduplicate by MD5 + ref_language, finding voices registered with identical audio and language.
        existing = _config_manager.find_voice_by_audio_md5('__LOCAL_TTS__', audio_md5, ref_language)
        if existing:
            voice_id, voice_data = existing
            logger.info(f"本地 TTS 音频 MD5 命中，复用 voice_id: {voice_id}")
            return JSONResponse({
                'voice_id': voice_id,
                'message': '已复用现有音色，跳过上传',
                'reused': True,
                'is_local': True
            })

        # Convert ws(s):// into http(s):// for REST calls.
        if base_url.startswith('wss://'):
            http_base = 'https://' + base_url[6:]
        else:
            http_base = 'http://' + base_url[5:]

        # Remove /v1/audio/speech/stream if present, keeping the host only.
        # Example: ws://127.0.0.1:50000/v1/audio/speech/stream -> http://127.0.0.1:50000.
        if '/v1/' in http_base:
            http_base = http_base.split('/v1/')[0]

        register_url = f"{http_base}/v1/speakers/register"
        logger.info(f"使用本地 TTS 注册: {register_url}")

        try:
            file_buffer.seek(0)

            # API format from the user's demo:
            # POST /v1/speakers/register
            # multipart/form-data: speaker_id, prompt_text, prompt_audio
            files = {
                'prompt_audio': (file.filename, file_buffer, 'audio/wav')
            }
            data = {
                'speaker_id': prefix,
                'prompt_text': f"<|{ref_language}|>" if ref_language != 'ch' else "希望你以后能够做的比我还好呦。"
            }

            # Per-call AsyncClient: cold path triggered by manual voice-file upload.
            async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
                resp = await client.post(register_url, data=data, files=files)

                if resp.status_code == 200:
                    result = resp.json()
                    voice_id = prefix  # Local TTS uses speaker_id as voice_id.

                    # Save to the local voice library with a special local-TTS key.
                    voice_data = {
                        'voice_id': voice_id,
                        'prefix': prefix,
                        'provider': 'local',
                        'is_local': True,
                        'audio_md5': audio_md5,
                        'ref_language': ref_language,
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        local_tts_key = '__LOCAL_TTS__'
                        _config_manager.save_voice_for_api_key(local_tts_key, voice_id, voice_data)
                        logger.info(f"本地 TTS voice_id 已保存: {voice_id}")
                    except Exception as save_error:
                        logger.warning(f"保存 voice_id 到音色库失败（本地 TTS 仍可用）: {save_error}")

                    return JSONResponse({
                        'voice_id': voice_id,
                        'message': result.get('message', '本地音色注册成功'),
                        'is_local': True
                    })
                else:
                    error_text = resp.text
                    logger.error(f"本地 TTS 注册失败: {error_text}")
                    return JSONResponse({
                        'error': f'本地 TTS 注册失败: {error_text[:200]}'
                    }, status_code=resp.status_code)

        except httpx.ConnectError as e:
            logger.error(f"无法连接本地 TTS 服务器: {e}")
            return JSONResponse({
                'error': f'无法连接本地 TTS 服务器: {http_base}，请确保服务器已启动'
            }, status_code=503)
        except Exception as e:
            logger.error(f"本地 TTS 注册时发生错误: {e}")
            return JSONResponse({
                'error': f'本地 TTS 注册失败: {str(e)}'
            }, status_code=500)

    # ==================== Cloud voice cloning: symmetric provider branches ====================

    # Obtain API keys uniformly through config_manager.
    cosyvoice_runtime = None
    api_key = _config_manager.get_tts_api_key(provider)

    if provider in ('minimax', 'minimax_intl'):
        # ---------- MiniMax domestic / international ----------
        if not api_key:
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

    elif provider in ('cosyvoice', 'cosyvoice_intl'):
        # ---------- Alibaba CosyVoice domestic / international ----------
        cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
        api_key = (cosyvoice_runtime.get('api_key') or '').strip()
        if not api_key:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)
        base_url = cosyvoice_runtime.get('base_url', '')
        storage_key = cosyvoice_runtime.get('storage_key') or api_key
        provider_label = cosyvoice_runtime.get('provider_label') or '阿里百炼CosyVoice'

    elif provider == 'elevenlabs':
        if not api_key:
            return JSONResponse({
                'error': 'ELEVENLABS_API_KEY_MISSING',
                'code': 'ELEVENLABS_API_KEY_MISSING',
                'message': '未配置 ElevenLabs API Key，请先在设置中填写'
            }, status_code=400)
        base_url = await _get_elevenlabs_base_url(_config_manager)
        storage_key = f'__ELEVENLABS__{api_key[-8:]}'
        provider_label = 'ElevenLabs'

    elif provider == 'mimo':
        if not api_key:
            return JSONResponse({
                'error': 'MIMO_API_KEY_MISSING',
                'code': 'MIMO_API_KEY_MISSING',
                'message': '未配置 MiMo API Key，请先在设置中填写'
            }, status_code=400)
        # base_url and api_key must share a source: for assistApi=mimo, including Token Plan, get_core_config resolves
        # OPENROUTER_URL to the matching ordinary/token-plan-* endpoint, and get_tts_api_key('mimo')
        # returns its paired key. Otherwise use default xiaomimimo, symmetric with _mimo_resolve base_url rules.
        if str(core_config.get('assistApi') or '').strip().lower() == 'mimo':
            base_url = (core_config.get('OPENROUTER_URL') or '').strip()
        else:
            base_url = ''
        storage_key = f'{MIMO_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = 'MiMo'

    elif provider == 'vllm_omni':
        # vLLM-Omni is self-hosted locally without API keys or remote voice registration. Cloning uses
        # inline reference audio, symmetric with MiMo: store full base64 audio plus ref_text in voice_storage
        # voice_meta, then inline into session.config ref_audio/ref_text for every synthesis. Fixed bucket
        # __VLLM_OMNI__ has no key suffix because local service has no keys. Preserve endpoint snapshots only
        # when vLLM-Omni is actually enabled; while TTS follows assist APIs, snapshotting that URL would
        # pin future clones to non-WebSocket TTS endpoints. Save reference samples normally and resolve
        # the current endpoint when users later select vLLM-Omni.
        vllm_omni_active = (
            _as_bool(core_config.get('ENABLE_CUSTOM_API'), False)
            and str(core_config.get('ttsModelProvider') or '').strip() == 'vllm_omni'
        )
        base_url = (
            (core_config.get('ttsModelUrl') or core_config.get('TTS_MODEL_URL') or '').strip()
            if vllm_omni_active
            else ''
        )
        storage_key = '__VLLM_OMNI__'
        provider_label = 'vLLM-Omni'

    elif provider == 'doubao_tts':
        if not api_key:
            return JSONResponse({
                'error': 'DOUBAO_TTS_API_KEY_MISSING',
                'code': 'DOUBAO_TTS_API_KEY_MISSING',
                'message': '未配置豆包语音 API Key，请先在设置中填写'
            }, status_code=400)
        base_url = DOUBAO_TTS_DEFAULT_BASE_URL
        storage_key = f'{DOUBAO_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = '豆包语音'

    elif provider == 'glm_tts':
        # Zhipu GLM cloning, symmetric with doubao_tts remote registration: keys come from
        # get_tts_api_key('glm_tts') -> ASSIST_API_KEY_GLM, falling back to coreApiKey when core/assist=glm;
        # bucket __GLM_TTS__ appends the key's final eight characters.
        if not api_key:
            return JSONResponse({
                'error': 'GLM_TTS_API_KEY_MISSING',
                'code': 'GLM_TTS_API_KEY_MISSING',
                'message': '未配置智谱 GLM API Key，请先在设置中填写'
            }, status_code=400)
        base_url = GLM_TTS_DEFAULT_BASE_URL
        storage_key = f'{GLM_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = '智谱GLM'

    else:
        return JSONResponse({'error': f'不支持的 provider: {provider}'}, status_code=400)

    # ---------- Shared MD5 deduplication ----------
    if provider in ('cosyvoice', 'cosyvoice_intl'):
        existing = _config_manager.find_cosyvoice_voice_by_audio_md5(provider, audio_md5, ref_language)
    else:
        existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
    if existing:
        voice_id_ex, voice_data_ex = existing
        # vLLM-Omni: identical audio/language with different ref_text is a different voice for transcript correction;
        # bypass deduplication so corrected transcripts can register anew.
        if provider == 'vllm_omni':
            existing_ref_text = str((voice_data_ex or {}).get('clone_ref_text') or '').strip()
            if existing_ref_text != vllm_ref_text:
                # Remove old entries, or insertion-ordered find_voice_by_audio_md5 always returns
                # the oldest match, occupying the slot and causing unlimited duplicate registrations;
                # stale entries also remain selectable and can give users the wrong voice.
                try:
                    _config_manager.delete_voice_for_current_api(voice_id_ex)
                    logger.info(
                        f"vLLM-Omni 克隆音色 {voice_id_ex} ref_text 变更"
                        f"（旧: {existing_ref_text!r} → 新: {vllm_ref_text!r}），已删除旧条目")
                except Exception:
                    logger.warning(
                        "vLLM-Omni 旧条目 %s 删除失败，可能导致下次去重仍命中旧条目",
                        voice_id_ex, exc_info=True)
                existing = None
    if existing:
        voice_id, voice_data = existing
        logger.info(f"{provider_label} 音频 MD5 命中，复用 voice_id: {voice_id}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'已复用现有{provider_label}音色，跳过上传',
            'reused': True,
            'provider': provider
        })

    # ---------- Shared audio normalization ----------
    try:
        if provider in ('cosyvoice', 'cosyvoice_intl'):
            mime_type, error_msg = validate_audio_file(file_buffer, file.filename)
            if not mime_type:
                return JSONResponse({'error': error_msg}, status_code=400)
        normalized_buffer, normalized_filename, audio_meta = await asyncio.to_thread(
            normalize_voice_clone_api_audio,
            file_buffer,
            file.filename or 'prompt_audio.wav',
        )
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    logger.info(
        "%s 语音克隆参考音频已规范化: %sHz/%sch -> %sHz/mono",
        provider_label,
        audio_meta['original']['sample_rate'],
        audio_meta['original']['channels'],
        audio_meta['normalized']['sample_rate'],
    )

    # ---------- Call provider-specific cloning APIs ----------
    try:
        if provider in ('minimax', 'minimax_intl'):
            original_prefix, minimax_prefix = build_minimax_request_voice_id(prefix, provider_label)

            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)
            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # Preserve original prefixes for display.
                'minimax_prefix': minimax_prefix,  # Preserve the safe prefix actually submitted to MiniMax.
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'elevenlabs':
            voice_id = await _elevenlabs_clone_voice(
                api_key=api_key,
                base_url=base_url,
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                name=prefix,
            )
            voice_data = {
                'voice_id': voice_id,
                'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'elevenlabs',
                'source': 'clone',
                'elevenlabs_base_url': base_url,
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'mimo':
            # MiMo has no remote registration API: official docs require inline reference audio on every voiceclone request,
            # with no create-voice/remote voice_id. Symmetric with MiniMax, store the entire clone identity
            # in voice_storage.json voice_meta: MiniMax stores remote voice_id, while MiMo stores
            # reference audio base64. Do not create separate local files; voice_meta cloud-syncs with voice_storage.json,
            # matching MiniMax. Persist only after sample validation.
            client = MimoVoiceCloneClient(api_key=api_key, base_url=base_url or None)
            sample_bytes = normalized_buffer.getvalue()
            await client.validate_sample(sample_bytes, mime_type='audio/wav')
            # voice_id dimensions must match the MD5 deduplication key (storage_key,audio_md5,ref_language):
            # - Include the key's last eight characters so identical audio under different MiMo keys gets distinct IDs,
            #   avoiding cross-__MIMO__-bucket ID deletion by delete_voice_for_current_api (Codex #1851).
            # - Include ref_language: deduplication distinguishes it, so omitting it from IDs would let language changes
            #   bypass deduplication but overwrite previous voice_data under the same ID (CodeRabbit #1851).
            voice_id = f'mimo-clone-{api_key[-8:]}-{ref_language}-{audio_md5[:12]}'
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'mimo',
                'source': 'clone',
                # Clone identity is reference audio base64, symmetric with MiniMax's remote voice_id. Dispatch/preview
                # inline it into voiceclone requests; voice_meta storage automatically cloud-syncs via voice_storage.json.
                'clone_sample_b64': base64.b64encode(sample_bytes).decode('ascii'),
                'clone_sample_mime': 'audio/wav',
                # Store base_url in voice_meta, symmetric with minimax_base_url. Dispatch still resolves current configuration
                # for assistApi=mimo, the sole Token Plan scenario, preserving endpoint/key pairing.
                'mimo_base_url': base_url or '',
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'vllm_omni':
            # vLLM-Omni inline clones, symmetric with MiMo: without remote registration, store full base64 audio + ref_text
            # in voice_storage voice_meta and read them at dispatch into session.config
            # ref_audio/ref_text. There is no remote validation API like MiMo validate_sample;
            # audio is used only at runtime, so skip validation and persist directly here.
            sample_bytes = normalized_buffer.getvalue()
            # Match deduplication dimensions: ref_language avoids same-audio language overwrite; ref_text
            # hashes distinguish corrected transcripts from stale voices with identical audio.
            # No key suffix for keyless local service; __VLLM_OMNI__ is the sole global partition.
            ref_text_hash = hashlib.md5(vllm_ref_text.encode('utf-8')).hexdigest()[:8]
            voice_id = f'vllm-omni-clone-{ref_language}-{audio_md5[:12]}-{ref_text_hash}'
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'vllm_omni',
                'source': 'clone',
                # Clone identity is reference audio base64, symmetric with MiMo clone_sample_b64; dispatch/preview
                # inline it as session.config.ref_audio. Persisting voice_meta also cloud-syncs through voice_storage.
                'clone_sample_b64': base64.b64encode(sample_bytes).decode('ascii'),
                'clone_sample_mime': 'audio/wav',
                # Reference transcript: vLLM-Omni requires ref_text to match audio exactly for session.config.ref_text.
                'clone_ref_text': vllm_ref_text,
                'created_at': datetime.now().isoformat()
            }
            # Snapshot only genuinely active vLLM-Omni endpoints; assist-following configuration must not contaminate clone runtime addresses.
            if base_url:
                voice_data['vllm_omni_base_url'] = base_url

        elif provider == 'doubao_tts':
            try:
                speaker_id = _normalize_doubao_voice_clone_speaker_id(prefix)
            except ValueError as exc:
                return JSONResponse({
                    'error': 'DOUBAO_SPEAKER_ID_REQUIRED',
                    'code': 'DOUBAO_SPEAKER_ID_REQUIRED',
                    'message': str(exc),
                }, status_code=400)
            resource_id = DOUBAO_TTS_DEFAULT_RESOURCE_ID
            client = DoubaoVoiceCloneClient(
                api_key=api_key,
                base_url=base_url,
                resource_id=resource_id,
            )
            voice_id = await client.clone_voice(
                normalized_buffer,
                speaker_id=speaker_id,
                display_name=speaker_id,
                audio_format='wav',
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'doubao_tts',
                'source': 'clone',
                'doubao_base_url': base_url,
                'doubao_resource_id': resource_id,
                'clone_model': resource_id,
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'glm_tts':
            # Two-step GLM cloning, symmetric with doubao: POST /files uploads reference audio (purpose=
            # voice-clone-input), then POST /voice/clone registers the remote voice. Official voice_name must be
            # account-unique; build_glm_voice_name builds neko_prefix_audioMD5fragment,
            # matching MD5 deduplication dimensions. Returned voice is the synthesis ID: /audio/speech's
            # voice explicitly supports clones, and dispatch reuses the cogtts worker.
            # Uploads limit reference audio to 10MB. Normalized WAV can grow after resampling/PCM conversion/
            # mono expansion, so preflight and return 413 above the limit without remote calls or quota consumption.
            normalized_size = len(normalized_buffer.getvalue())
            if normalized_size > GLM_VOICE_CLONE_MAX_AUDIO_BYTES:
                return JSONResponse({
                    'error': f'GLM 示例音频超过 10MB 上限（规范化后 {normalized_size / (1024 * 1024):.1f}MB），请裁剪后重试',
                    'code': 'GLM_TTS_AUDIO_TOO_LARGE',
                    'provider': provider,
                }, status_code=413)
            client = GlmVoiceCloneClient(api_key=api_key, base_url=base_url)
            voice_name = build_glm_voice_name(prefix, audio_md5, ref_language)
            voice_id = await client.clone_voice(
                normalized_buffer,
                voice_name=voice_name,
                filename=normalized_filename,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'glm_tts',
                'source': 'clone',
                'glm_base_url': base_url,
                'glm_voice_name': voice_name,
                'clone_model': 'glm-tts-clone',
                'created_at': datetime.now().isoformat()
            }

        else:  # cosyvoice / cosyvoice_intl
            from utils.api_config_loader import (
                get_cosyvoice_clone_model,
                get_cosyvoice_user_preferred_model,
            )
            # Voice registration binds target_model (cloned voices cannot cross models); prefer the
            # user's cosyvoice-v* model from the TTS endpoint, falling back when absent or from another vendor.
            clone_model = (
                get_cosyvoice_user_preferred_model(provider)
                or get_cosyvoice_clone_model(provider)
            )
            language_hints = qwen_language_hints(ref_language)
            dashscope_base_url = (cosyvoice_runtime or {}).get('base_url', '')
            client = QwenVoiceCloneClient(
                api_key=api_key,
                tflink_upload_url=TFLINK_UPLOAD_URL,
                dashscope_base_url=dashscope_base_url,
            )
            voice_id, tmp_url, _request_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=prefix,
                language_hints=language_hints,
                target_model=clone_model,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': tmp_url,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': provider,
                'dashscope_base_url': dashscope_base_url,
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat()
            }

        logger.info(f"{provider_label} 音色注册成功，voice_id: {voice_id}")

    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs 音色注册上游服务错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
            'provider': provider,
        }, status_code=502)
    except (MinimaxVoiceCloneError, QwenVoiceCloneError, MimoVoiceCloneError, DoubaoTtsError, GlmTtsError) as e:
        logger.error(f"{provider_label} 音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        elif provider == 'glm_tts' and '10MB' in error_detail:
            # Beyond preflight, client-side same-threshold exceptions also return 413 semantics (CodeRabbit).
            return JSONResponse({
                'error': error_detail,
                'code': 'GLM_TTS_AUDIO_TOO_LARGE',
                'provider': provider,
            }, status_code=413)
        return JSONResponse({'error': f'{provider_label}音色注册失败: {error_detail}', 'provider': provider}, status_code=500)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"{provider_label} 音色注册时发生错误: {e}")
        return JSONResponse({'error': f'{provider_label}音色注册失败: {str(e)}', 'provider': provider}, status_code=500)

    # ---------- Shared local voice-library persistence ----------
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} voice_id 到音色库失败: {save_error}")
        # MiMo differs: no remote voice resource exists; identity is sample base64 in voice_meta. Failed saves
        # persist nothing, leaving locally generated voice_id pointing nowhere. A 200+local_save_failed
        # returns a nonexistent ID. MiMo retries cannot duplicate remote resources because validation
        # creates nothing, so retries are safe: return a real failure the client can recognize and retry
        # (Codex #1851, unlike PR #528's remote-created -> 200 partial premise).
        if provider in ('mimo', 'vllm_omni'):
            return JSONResponse({
                'error': f'{provider_label}音色保存失败: {str(save_error)}',
                'code': 'TTS_VOICE_SAVE_FAILED',
                'provider': provider,
            }, status_code=500)
        # Other providers (cosyvoice/minimax/elevenlabs) already created remote voices, so local-save failures still return
        # 200+local_save_failed, preventing retries from duplicating remote resources and wasting quota (PR #528).
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}音色注册成功并已保存到音色库',
        'provider': provider,
    })


@router.post('/voice_clone_direct')
async def voice_clone_direct(request: Request):
    """
    Direct-link voice cloning endpoint — skips the audio upload step and registers the voice directly from the provided direct URL.

    Supports the CosyVoice, MiniMax and ElevenLabs providers:
    - CosyVoice: registers the voice directly with the direct-link URL
    - MiniMax: downloads the audio file first, then uploads it to the MiniMax server to register the voice

    Request body:
        {
            "direct_link": "https://example.com/audio.wav",  // direct audio URL
            "prefix": "custom_prefix",                        // voice prefix name
            "ref_language": "ch",                             // reference audio language
            "provider": "cosyvoice"                           // provider: cosyvoice / cosyvoice_intl / minimax / minimax_intl / elevenlabs
        }
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({'error': f'请求体解析失败: {e}'}, status_code=400)

    direct_link = str(data.get('direct_link') or '').strip()
    prefix = str(data.get('prefix') or '').strip()
    ref_language = str(data.get('ref_language') or 'ch').lower().strip()
    provider = str(data.get('provider') or 'cosyvoice').lower().strip()

    # Validate parameters.
    if not direct_link:
        return JSONResponse({'error': '缺少 direct_link 参数'}, status_code=400)
    if not prefix:
        return JSONResponse({'error': '缺少 prefix 参数'}, status_code=400)
    try:
        await _validate_direct_link_target(direct_link)
    except DirectLinkSecurityError as e:
        return JSONResponse({
            'error': str(e),
            'code': e.code,
        }, status_code=400)
    except Exception as e:
        logger.warning(f"SSRF检查失败: {e}")
        return JSONResponse({'error': '直链安全检查失败'}, status_code=400)

    # Validate language.
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    if ref_language not in valid_languages:
        ref_language = 'ch'

    # Validate provider.
    valid_providers = ['minimax', 'minimax_intl', 'cosyvoice', 'cosyvoice_intl', 'elevenlabs', 'glm_tts']
    if provider not in valid_providers:
        return JSONResponse({
            'error': f'无效的服务商: {provider}',
            'code': 'TTS_PROVIDER_INVALID',
            'message': f'支持的服务商: {", ".join(valid_providers)}',
            'details': {'provider': provider, 'valid_providers': ', '.join(valid_providers)},
        }, status_code=400)

    # Get the API key.
    _config_manager = get_config_manager()
    cosyvoice_runtime = None
    if provider in ('cosyvoice', 'cosyvoice_intl'):
        cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
        api_key = (cosyvoice_runtime.get('api_key') or '').strip()
    else:
        api_key = _config_manager.get_tts_api_key(provider)
    if not api_key:
        if provider in ('minimax', 'minimax_intl'):
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        if provider == 'elevenlabs':
            return JSONResponse({
                'error': 'ELEVENLABS_API_KEY_MISSING',
                'code': 'ELEVENLABS_API_KEY_MISSING',
                'message': '未配置 ElevenLabs API Key，请先在设置中填写'
            }, status_code=400)
        if provider == 'glm_tts':
            return JSONResponse({
                'error': 'GLM_TTS_API_KEY_MISSING',
                'code': 'GLM_TTS_API_KEY_MISSING',
                'message': '未配置智谱 GLM API Key，请先在设置中填写'
            }, status_code=400)
        else:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)

    # Bind shared helpers and errors before dispatching to any provider.
    from utils.audio import normalize_voice_clone_api_audio
    from utils.voice_clone import MinimaxVoiceCloneError, QwenVoiceCloneError

    # Set provider-specific parameters.
    if provider in ('minimax', 'minimax_intl'):
        from utils.voice_clone import (
            MinimaxVoiceCloneClient,
            minimax_normalize_language,
        )
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'
    elif provider == 'elevenlabs':
        base_url = await _get_elevenlabs_base_url(_config_manager)
        storage_key = f'__ELEVENLABS__{api_key[-8:]}'
        provider_label = 'ElevenLabs'
    elif provider == 'glm_tts':
        base_url = GLM_TTS_DEFAULT_BASE_URL
        storage_key = f'{GLM_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = '智谱GLM'
    else:  # cosyvoice / cosyvoice_intl
        from utils.voice_clone import QwenVoiceCloneClient, qwen_language_hints
        base_url = (cosyvoice_runtime or {}).get('base_url', '')
        storage_key = (cosyvoice_runtime or {}).get('storage_key') or api_key
        provider_label = (cosyvoice_runtime or {}).get('provider_label') or '阿里百炼CosyVoice'

    # Check direct-link accessibility, falling back from HEAD to GET.
    # Pin every hop to validated DNS results, preventing rebinding between validation and requests.
    try:
        head_resp = await _request_direct_link_follow_redirects("HEAD", direct_link)
        try:
            if head_resp.status_code >= 400:
                # HEAD failed: try GET.
                logger.warning(f"HEAD请求失败({head_resp.status_code})，尝试GET请求: {direct_link}")
                get_resp = await _request_direct_link_follow_redirects(
                    "GET",
                    direct_link,
                    stream=True,
                    headers={"Range": "bytes=0-0"},
                )
                try:
                    if get_resp.status_code >= 400:
                        return JSONResponse({
                            'error': f'直链无法访问，状态码: {get_resp.status_code}',
                            'code': 'DIRECT_LINK_INACCESSIBLE'
                        }, status_code=400)
                finally:
                    await get_resp.aclose()
        finally:
            await head_resp.aclose()
    except DirectLinkSecurityError as e:
        logger.warning(f"直链安全校验失败: {e}")
        return JSONResponse({
            'error': str(e),
            'code': e.code,
        }, status_code=400)
    except Exception as e:
        logger.warning(f"直链验证失败: {e}")
        # Warn without blocking the flow.

    # Execute provider-specific cloning.
    try:
        if provider in ('minimax', 'minimax_intl'):
            # ========== MiniMax direct-link cloning ==========
            # 1. Download audio with streaming reads to avoid memory issues.
            logger.info(f"开始下载直链音频: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB limit.

            filename, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            logger.info(f"音频下载完成: {filename}, 大小: {len(audio_bytes)} bytes")

            # 2. Compute content MD5 for deduplication, matching file uploads.
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            # 3. MD5 deduplication check.
            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            # 2. Normalize audio, matching file uploads.
            original_buffer = io.BytesIO(audio_bytes)
            normalized_buffer, normalized_filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio,
                original_buffer, filename
            )

            original_prefix, minimax_prefix = build_minimax_request_voice_id(prefix, provider_label)

            # 4. Upload and register through MinimaxVoiceCloneClient.
            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)

            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )

            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # Preserve original prefixes for display.
                'minimax_prefix': minimax_prefix,  # Preserve the safe prefix actually submitted to MiniMax.
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

        elif provider == 'elevenlabs':
            MAX_FILE_SIZE = 100 * 1024 * 1024

            filename, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            normalized_buffer, normalized_filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio,
                io.BytesIO(audio_bytes),
                filename,
            )
            voice_id = await _elevenlabs_clone_voice(
                api_key=api_key,
                base_url=base_url,
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                name=prefix,
            )
            voice_data = {
                'voice_id': voice_id,
                'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
                'prefix': prefix,
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'elevenlabs',
                'elevenlabs_base_url': base_url,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

        elif provider == 'glm_tts':
            # ========== GLM direct-link cloning: MiniMax-style download -> upload -> register ==========
            logger.info(f"开始下载直链音频用于GLM声音复刻: {direct_link}")
            filename, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=GLM_VOICE_CLONE_MAX_AUDIO_BYTES,
            )

            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            normalized_buffer, normalized_filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio,
                io.BytesIO(audio_bytes),
                filename,
            )
            # Match file uploads: normalized audio over 10MB returns 413 without remote API calls.
            normalized_size = len(normalized_buffer.getvalue())
            if normalized_size > GLM_VOICE_CLONE_MAX_AUDIO_BYTES:
                return JSONResponse({
                    'error': f'GLM 示例音频超过 10MB 上限（规范化后 {normalized_size / (1024 * 1024):.1f}MB），请裁剪后重试',
                    'code': 'GLM_TTS_AUDIO_TOO_LARGE',
                    'provider': provider,
                }, status_code=413)

            voice_name = build_glm_voice_name(prefix, audio_md5, ref_language)
            glm_client = GlmVoiceCloneClient(api_key=api_key, base_url=base_url)
            voice_id = await glm_client.clone_voice(
                normalized_buffer,
                voice_name=voice_name,
                filename=normalized_filename,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'glm_tts',
                'glm_base_url': base_url,
                'glm_voice_name': voice_name,
                'clone_model': 'glm-tts-clone',
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

        else:  # cosyvoice / cosyvoice_intl
            # ========== CosyVoice direct-link cloning ==========
            # 1. Stream audio downloads to compute content MD5 without memory issues.
            logger.info(f"开始下载直链音频用于CosyVoice: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB limit.

            _, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            logger.info(f"音频下载完成，大小: {len(audio_bytes)} bytes")

            # 2. Compute content MD5 for deduplication.
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            # 3. MD5 deduplication check.
            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            # 4. Register the voice through the direct link.
            language_hints = qwen_language_hints(ref_language)
            client = QwenVoiceCloneClient(
                api_key=api_key,
                tflink_upload_url=TFLINK_UPLOAD_URL,
                dashscope_base_url=base_url,
            )

            from utils.api_config_loader import (
                get_cosyvoice_clone_model,
                get_cosyvoice_user_preferred_model,
            )
            # As above, registration binds target_model, following the user's configured TTS model.
            clone_model = (
                get_cosyvoice_user_preferred_model(provider)
                or get_cosyvoice_clone_model(provider)
            )
            voice_id, _ = await asyncio.to_thread(
                client.create_voice,
                prefix=prefix,
                url=direct_link,
                language_hints=language_hints,
                target_model=clone_model,
            )

            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': provider,
                'dashscope_base_url': base_url,
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

    except DirectLinkSecurityError as e:
        logger.warning(f"{provider_label} 直链安全校验失败: {e}")
        if provider == 'glm_tts' and e.code == 'FILE_TOO_LARGE':
            # Downloads exceeding GLM's 10MB cap return the same 413 and dedicated error code as post-normalization overflow.
            return JSONResponse({
                'error': str(e),
                'code': 'GLM_TTS_AUDIO_TOO_LARGE',
                'provider': provider,
            }, status_code=413)
        return JSONResponse({
            'error': str(e),
            'code': e.code,
            'provider': provider,
        }, status_code=400)
    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs 直链音色注册上游服务错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
            'provider': provider,
        }, status_code=502)
    except (MinimaxVoiceCloneError, QwenVoiceCloneError, GlmTtsError) as e:
        logger.error(f"{provider_label} 直链音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        elif provider == 'glm_tts' and '10MB' in error_detail:
            return JSONResponse({
                'error': error_detail,
                'code': 'GLM_TTS_AUDIO_TOO_LARGE',
                'provider': provider,
            }, status_code=413)
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {error_detail}',
            'provider': provider
        }, status_code=500)
    except ValueError as e:
        # Like uploads, unparseable/unnormalizable audio is invalid input and returns 400, not a server error.
        logger.warning(f"{provider_label} 直链音频无效: {e}")
        return JSONResponse({'error': str(e), 'provider': provider}, status_code=400)
    except Exception as e:
        logger.error(f"{provider_label} 直链音色注册时发生错误: {e}")
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {str(e)}',
            'provider': provider
        }, status_code=500)

    # Save to the local voice library.
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} 直链 voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} 直链 voice_id 到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}直链音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}直链音色注册成功并已保存到音色库',
        'provider': provider,
        'is_direct_link': True
    })

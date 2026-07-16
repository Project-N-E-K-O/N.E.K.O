# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Voice Design registration endpoints and provider dispatch."""

import asyncio
import re
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from ._shared import logger, router
from .voice_providers import (
    _get_elevenlabs_base_url,
    _raw_elevenlabs_voice_id,
)
from ..shared_state import get_config_manager
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_voice import VOICE_PREVIEW_TEXTS
from utils.tts.providers.minimax import (
    build_minimax_request_voice_id,
    get_minimax_base_url,
    get_minimax_storage_prefix,
)
from utils.tts.providers.mimo import MIMO_VOICE_STORAGE_KEY, new_mimo_design_voice_id
from utils.voice_design import (
    CosyVoiceDesignError,
    ElevenLabsVoiceDesignError,
    ElevenLabsVoiceDesignRequestError,
    MiniMaxVoiceDesignError,
    MimoVoiceDesignClient,
    MimoVoiceDesignError,
    _cosyvoice_design_language_hints,
    _cosyvoice_design_voice,
    _elevenlabs_create_voice_from_preview,
    _elevenlabs_design_previews,
    _minimax_design_voice,
)
from utils.voice_preview_text import normalize_voice_preview_language


def _cosyvoice_design_default_preview_text(ref_language: str) -> str:
    return _loc(
        VOICE_PREVIEW_TEXTS,
        "en" if _cosyvoice_design_language_hints(ref_language) == ["en"] else "zh-CN",
    )


def _voice_design_preview_language(raw_language: object = None, ref_language: object = None) -> str:
    normalized = normalize_voice_preview_language(raw_language)
    if normalized:
        return normalized

    raw_ref = str(ref_language or "").strip().lower()
    if raw_ref in ("ch", "zh", "zh-cn", "zh_hans", "zh-hans"):
        return "zh-CN"
    normalized_ref = normalize_voice_preview_language(raw_ref)
    if normalized_ref:
        return normalized_ref
    return "zh-CN"


def _voice_design_preview_text(raw_language: object = None, ref_language: object = None) -> str:
    return _loc(VOICE_PREVIEW_TEXTS, _voice_design_preview_language(raw_language, ref_language))


def _voice_design_provider(provider: str):
    """Return a design-capable provider and its declarative constraints."""
    from utils.tts import provider_registry

    from main_logic import tts_client  # noqa: F401 - ensures provider registration

    provider_meta = provider_registry.get(provider)
    if provider_meta is None or "design" not in provider_meta.capabilities:
        return None
    return provider_meta


def _provider_supports_voice_design(provider: str) -> bool:
    """Compatibility query backed by the same provider registry entry."""
    return _voice_design_provider(provider) is not None


@router.post('/voice_design')
async def voice_design(request: Request):
    """Create a reusable designed voice from a text description."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)

    provider = str(data.get('provider') or 'cosyvoice').strip().lower()
    prefix = str(data.get('prefix') or '').strip()
    voice_prompt = str(data.get('voice_prompt') or data.get('description') or '').strip()
    ref_language = str(data.get('ref_language') or 'ch').strip().lower()
    request_language = data.get('i18n_language') or data.get('language')

    provider_meta = _voice_design_provider(provider)
    if provider_meta is None:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PROVIDER_UNSUPPORTED',
            'code': 'VOICE_DESIGN_PROVIDER_UNSUPPORTED',
            'details': {'provider': provider},
        }, status_code=400)
    constraints = provider_meta.voice_design
    if not prefix:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PREFIX_REQUIRED',
            'code': 'VOICE_DESIGN_PREFIX_REQUIRED',
        }, status_code=400)
    prefix_max = constraints.prefix_max if constraints is not None else None
    prefix_pattern = constraints.prefix_pattern if constraints is not None else ''
    if (
        (prefix_max is not None and len(prefix) > prefix_max)
        or (prefix_pattern and re.fullmatch(prefix_pattern, prefix) is None)
    ):
        return JSONResponse({
            'error': 'VOICE_DESIGN_PREFIX_INVALID',
            'code': 'VOICE_DESIGN_PREFIX_INVALID',
            'details': {'max': prefix_max, 'pattern': prefix_pattern},
            'message': (
                f'Prefix must be 1-{prefix_max} characters and match {prefix_pattern}. '
                'Underscores and spaces are not allowed.'
            ),
        }, status_code=400)
    if not voice_prompt:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PROMPT_REQUIRED',
            'code': 'VOICE_DESIGN_PROMPT_REQUIRED',
        }, status_code=400)
    prompt_min = constraints.prompt_min if constraints is not None else None
    prompt_max = constraints.prompt_max if constraints is not None else None
    if prompt_min is not None and len(voice_prompt) < prompt_min:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PROMPT_TOO_SHORT',
            'code': 'VOICE_DESIGN_PROMPT_TOO_SHORT',
            'min': prompt_min,
            'details': {'min': prompt_min},
        }, status_code=400)
    if prompt_max is not None and len(voice_prompt) > prompt_max:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PROMPT_TOO_LONG',
            'code': 'VOICE_DESIGN_PROMPT_TOO_LONG',
            'max': prompt_max,
            'details': {'max': prompt_max},
        }, status_code=400)

    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    if ref_language not in valid_languages:
        ref_language = 'ch'
    language_hints = constraints.language_hints if constraints is not None else ()
    if language_hints and ref_language not in language_hints:
        ref_language = language_hints[0]
    preview_text = _voice_design_preview_text(request_language, ref_language)

    config_manager = get_config_manager()
    provider_label = provider
    storage_key = ''
    voice_id = ''
    request_id = None
    voice_data: dict = {
        'prefix': prefix,
        'provider': provider,
        'source': 'design',
        'design_prompt': voice_prompt,
        'preview_text': preview_text,
        'ref_language': ref_language,
        'created_at': datetime.now().isoformat(),
    }

    try:
        if provider == 'cosyvoice':
            from utils.api_config_loader import get_cosyvoice_clone_model

            cosyvoice_runtime = config_manager.get_cosyvoice_clone_runtime(provider)
            api_key = (cosyvoice_runtime.get('api_key') or '').strip()
            if not api_key:
                return JSONResponse({
                    'error': 'TTS_AUDIO_API_KEY_MISSING',
                    'code': 'TTS_AUDIO_API_KEY_MISSING',
                    'provider': provider,
                }, status_code=400)
            provider_label = cosyvoice_runtime.get('provider_label') or '阿里百炼CosyVoice'
            dashscope_base_url = cosyvoice_runtime.get('base_url', '')
            storage_key = cosyvoice_runtime.get('storage_key') or api_key
            design_model = get_cosyvoice_clone_model(provider)
            voice_id, _preview_audio, _preview_media_type, request_id = await _cosyvoice_design_voice(
                api_key=api_key,
                base_url=dashscope_base_url,
                prefix=prefix,
                voice_prompt=voice_prompt,
                preview_text=preview_text,
                ref_language=ref_language,
                target_model=design_model,
            )
            voice_data.update({
                'dashscope_base_url': dashscope_base_url,
                'design_model': design_model,
            })
        elif provider in ('minimax', 'minimax_intl'):
            api_key = (config_manager.get_tts_api_key(provider) or '').strip()
            if not api_key:
                return JSONResponse({
                    'error': 'TTS_AUDIO_API_KEY_MISSING',
                    'code': 'TTS_AUDIO_API_KEY_MISSING',
                    'provider': provider,
                }, status_code=400)
            base_url = get_minimax_base_url(provider)
            if not base_url:
                return JSONResponse({
                    'error': 'MINIMAX_BASE_URL_MISSING',
                    'code': 'MINIMAX_BASE_URL_MISSING',
                    'provider': provider,
                }, status_code=400)
            provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'
            storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
            original_prefix, requested_voice_id = build_minimax_request_voice_id(prefix, provider_label)
            voice_id, request_id = await _minimax_design_voice(
                api_key=api_key,
                base_url=base_url,
                voice_prompt=voice_prompt,
                preview_text=preview_text,
                voice_id=requested_voice_id,
            )
            voice_data.update({
                'original_prefix': original_prefix,
                'minimax_prefix': requested_voice_id,
                'minimax_base_url': base_url,
            })
        elif provider == 'elevenlabs':
            api_key = (config_manager.get_tts_api_key('elevenlabs') or '').strip()
            if not api_key:
                return JSONResponse({
                    'error': 'ELEVENLABS_API_KEY_MISSING',
                    'code': 'ELEVENLABS_API_KEY_MISSING',
                    'provider': provider,
                }, status_code=400)
            base_url = await _get_elevenlabs_base_url(config_manager)
            provider_label = 'ElevenLabs'
            previews = await _elevenlabs_design_previews(
                api_key=api_key,
                base_url=base_url,
                voice_description=voice_prompt,
            )
            generated_voice_id = ''
            for preview in previews:
                generated_voice_id = str((preview or {}).get('generated_voice_id') or '').strip()
                if generated_voice_id:
                    break
            if not generated_voice_id:
                raise ElevenLabsVoiceDesignError(502, "ElevenLabs did not return generated_voice_id")
            voice_id = await _elevenlabs_create_voice_from_preview(
                api_key=api_key,
                base_url=base_url,
                voice_name=prefix or 'NEKO Designed Voice',
                voice_description=voice_prompt,
                generated_voice_id=generated_voice_id,
            )
            storage_key = f'__ELEVENLABS__{api_key[-8:]}'
            voice_data.update({
                'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
                'design_description': voice_prompt,
                'generated_voice_id': generated_voice_id,
                'elevenlabs_base_url': base_url,
            })
        elif provider == 'mimo':
            api_key = (config_manager.get_tts_api_key('mimo') or '').strip()
            if not api_key:
                return JSONResponse({
                    'error': 'TTS_AUDIO_API_KEY_MISSING',
                    'code': 'TTS_AUDIO_API_KEY_MISSING',
                    'provider': provider,
                }, status_code=400)
            provider_label = 'MiMo'
            core_config = await asyncio.to_thread(config_manager.get_core_config)
            assist_api_type = str(core_config.get('assistApi') or '').strip().lower()
            mimo_base_url = (core_config.get('OPENROUTER_URL') or '').strip() if assist_api_type == 'mimo' else ''
            client = MimoVoiceDesignClient(api_key=api_key, base_url=mimo_base_url or None)
            await client.validate_design_prompt(voice_prompt, sample_text=preview_text)

            voice_id = new_mimo_design_voice_id()
            storage_key = f'{MIMO_VOICE_STORAGE_KEY}{api_key[-8:]}'
            voice_data.update({'mimo_base_url': mimo_base_url})
    except CosyVoiceDesignError as exc:
        logger.error(f"{provider_label} voice design failed: {exc}")
        return JSONResponse({
            'error': f'{provider_label} voice design failed: {str(exc)}',
            'code': 'COSYVOICE_VOICE_DESIGN_FAILED',
            'provider': provider,
        }, status_code=502)
    except MiniMaxVoiceDesignError as exc:
        logger.error(f"{provider_label} voice design failed: {exc}")
        return JSONResponse({
            'error': f'{provider_label} voice design failed: {str(exc)}',
            'code': 'MINIMAX_VOICE_DESIGN_FAILED',
            'provider': provider,
        }, status_code=502)
    except ElevenLabsVoiceDesignError as exc:
        logger.error(f"{provider_label} voice design failed: {exc}")
        return JSONResponse({
            'error': f'{provider_label} voice design failed: {str(exc)}',
            'code': 'ELEVENLABS_VOICE_DESIGN_FAILED',
            'provider': provider,
        }, status_code=502)
    except ElevenLabsVoiceDesignRequestError as exc:
        return JSONResponse({
            'error': str(exc),
            'code': 'ELEVENLABS_VOICE_DESIGN_FAILED',
            'provider': provider,
        }, status_code=400)
    except MimoVoiceDesignError as exc:
        logger.error(f"{provider_label} voice design failed: {exc}")
        return JSONResponse({
            'error': f'{provider_label} voice design failed: {str(exc)}',
            'code': 'MIMO_VOICE_DESIGN_FAILED',
            'provider': provider,
        }, status_code=502)
    except Exception as exc:
        error_code = {
            'cosyvoice': 'COSYVOICE_VOICE_DESIGN_FAILED',
            'minimax': 'MINIMAX_VOICE_DESIGN_FAILED',
            'minimax_intl': 'MINIMAX_VOICE_DESIGN_FAILED',
            'elevenlabs': 'ELEVENLABS_VOICE_DESIGN_FAILED',
            'mimo': 'MIMO_VOICE_DESIGN_FAILED',
        }.get(provider, 'VOICE_DESIGN_FAILED')
        logger.error(f"{provider_label} voice design unexpected error: {exc}")
        return JSONResponse({
            'error': f'{provider_label} voice design failed: {str(exc)}',
            'code': error_code,
            'provider': provider,
        }, status_code=500)
    voice_data['voice_id'] = voice_id
    if request_id:
        voice_data['request_id'] = request_id

    try:
        await config_manager.asave_voice_for_api_key(storage_key, voice_id, voice_data)
    except Exception as save_error:
        logger.error(f"保存 {provider_label} voice design 到音色库失败: {save_error}")
        if provider == 'mimo':
            return JSONResponse({
                'error': f'{provider_label} voice design save failed: {save_error}',
                'code': 'TTS_VOICE_SAVE_FAILED',
                'provider': provider,
            }, status_code=500)
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label} voice design succeeded, but local save failed',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
            'source': 'design',
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label} voice design succeeded',
        'provider': provider,
        'source': 'design',
    })


def _validate_voice_design_description(raw: object) -> tuple[str, JSONResponse | None]:
    """Validate a description for the reserved ElevenLabs two-stage routes."""
    description = str(raw or '').strip()
    provider_meta = _voice_design_provider('elevenlabs')
    constraints = provider_meta.voice_design if provider_meta is not None else None
    prompt_min = constraints.prompt_min if constraints is not None else None
    prompt_max = constraints.prompt_max if constraints is not None else None
    if prompt_min is not None and len(description) < prompt_min:
        return description, JSONResponse({
            'error': 'VOICE_DESIGN_DESCRIPTION_TOO_SHORT',
            'code': 'VOICE_DESIGN_DESCRIPTION_TOO_SHORT',
            'min': prompt_min,
        }, status_code=400)
    if prompt_max is not None and len(description) > prompt_max:
        return description, JSONResponse({
            'error': 'VOICE_DESIGN_DESCRIPTION_TOO_LONG',
            'code': 'VOICE_DESIGN_DESCRIPTION_TOO_LONG',
            'max': prompt_max,
        }, status_code=400)
    return description, None


# Reserved ElevenLabs two-stage flow. The current registration UI uses the
# unified /voice_design endpoint, which creates and saves the first usable
# preview in one request. Keep these endpoints for a future workflow where the
# user auditions multiple candidates and explicitly chooses which one to save.
@router.post('/voice_design_preview')
async def voice_design_preview(request: Request):
    """Generate ElevenLabs candidates for a future selection UI."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)

    description, err = _validate_voice_design_description(data.get('description'))
    if err is not None:
        return err

    config_manager = get_config_manager()
    api_key = config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return JSONResponse({
            'error': 'ELEVENLABS_API_KEY_MISSING',
            'code': 'ELEVENLABS_API_KEY_MISSING',
            'message': '未配置 ElevenLabs API Key，请先在设置中填写',
        }, status_code=400)
    base_url = await _get_elevenlabs_base_url(config_manager)

    try:
        previews = await _elevenlabs_design_previews(
            api_key=api_key, base_url=base_url, voice_description=description,
        )
    except ElevenLabsVoiceDesignError as exc:
        logger.error(f"ElevenLabs voice design upstream error ({exc.status_code}): {exc}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(exc)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
        }, status_code=502)
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)
    except Exception as exc:
        logger.error(f"ElevenLabs voice design failed: {exc}")
        return JSONResponse({'error': f'语音设计失败: {str(exc)}'}, status_code=500)

    result_previews = [
        {
            'generated_voice_id': preview.get('generated_voice_id', ''),
            'audio': preview.get('audio_base_64', ''),
            'media_type': preview.get('media_type', 'audio/mpeg'),
            'duration_secs': preview.get('duration_secs'),
        }
        for preview in previews
        if isinstance(preview, dict) and preview.get('generated_voice_id') and preview.get('audio_base_64')
    ]
    if not result_previews:
        return JSONResponse({
            'error': 'ElevenLabs 未返回可试听的语音预览',
            'code': 'ELEVENLABS_PREVIEWS_EMPTY',
        }, status_code=502)
    return JSONResponse({'success': True, 'previews': result_previews})


@router.post('/voice_design_create')
async def voice_design_create(request: Request):
    """Persist a candidate selected by a future ElevenLabs selection UI."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)

    description, err = _validate_voice_design_description(data.get('description'))
    if err is not None:
        return err
    generated_voice_id = str(data.get('generated_voice_id') or '').strip()
    if not generated_voice_id:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PREVIEW_MISSING',
            'code': 'VOICE_DESIGN_PREVIEW_MISSING',
        }, status_code=400)
    name = str(data.get('name') or data.get('prefix') or '').strip()

    config_manager = get_config_manager()
    api_key = config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return JSONResponse({
            'error': 'ELEVENLABS_API_KEY_MISSING',
            'code': 'ELEVENLABS_API_KEY_MISSING',
            'message': '未配置 ElevenLabs API Key，请先在设置中填写',
        }, status_code=400)
    base_url = await _get_elevenlabs_base_url(config_manager)

    try:
        voice_id = await _elevenlabs_create_voice_from_preview(
            api_key=api_key,
            base_url=base_url,
            voice_name=name or 'NEKO Designed Voice',
            voice_description=description,
            generated_voice_id=generated_voice_id,
        )
    except ElevenLabsVoiceDesignError as exc:
        logger.error(f"ElevenLabs voice design create upstream error ({exc.status_code}): {exc}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(exc)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
        }, status_code=502)
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)
    except Exception as exc:
        logger.error(f"ElevenLabs voice design create failed: {exc}")
        return JSONResponse({'error': f'语音设计保存失败: {str(exc)}'}, status_code=500)

    voice_data = {
        'voice_id': voice_id,
        'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
        'prefix': name or 'Designed Voice',
        'provider': 'elevenlabs',
        'source': 'design',
        'design_description': description,
        'elevenlabs_base_url': base_url,
        'created_at': datetime.now().isoformat(),
    }
    storage_key = f'__ELEVENLABS__{api_key[-8:]}'
    try:
        config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
    except Exception as save_error:
        logger.error(f"保存 ElevenLabs 设计音色到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': 'ElevenLabs 设计音色创建成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': 'elevenlabs',
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': 'ElevenLabs 设计音色创建成功并已保存到音色库',
        'provider': 'elevenlabs',
        'source': 'design',
    })

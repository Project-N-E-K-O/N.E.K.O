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

"""Voice list and preview endpoints with per-provider synthesis
fallbacks.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import logger, router
from .voice_providers import ElevenLabsUpstreamError, _elevenlabs_synthesize_preview

import json
import io
import asyncio
import base64
import wave
import inspect
from fastapi import Request
from fastapi.responses import JSONResponse
import httpx
import websockets
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_voice import VOICE_PREVIEW_TEXTS
from ..shared_state import (
    get_config_manager,
)
from utils.tts.providers.elevenlabs import (
    ELEVENLABS_TTS_VOICE_PREFIX,
)
from utils.config_manager import (
    get_reserved,
)
from utils.dashscope_region import (
    DASHSCOPE_GLOBAL_LOCK,
    configure_dashscope_sdk_urls,
    prefer_dashscope_websocket_ipv4,
)
from utils.doubao_tts import (
    DOUBAO_TTS_DEFAULT_BASE_URL,
    DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,
    DOUBAO_VOICE_CLONE_RESOURCE_ID,
    DoubaoTtsError,
    build_doubao_tts_payload,
    doubao_api_headers,
    doubao_tts_url,
    extract_doubao_audio_bytes,
)
from utils.glm_tts import GlmTtsError, GlmVoiceCloneClient
from utils.voice_config import read_legacy_voice_id
from utils.tts.native_voice_registry import (
    get_active_realtime_native_provider_for_ui,
    get_native_voice_catalog_for_ui,
    normalize_native_voice,
    resolve_native_voice_for_routing,
)
from utils.tts import provider_registry as tts_provider_registry
from utils.voice_clone import (
    MinimaxVoiceCloneClient,
    MinimaxVoiceCloneError,
    MimoVoiceCloneClient,
    MimoVoiceCloneError,
)
from utils.tts.providers.minimax import get_minimax_base_url
from utils.voice_design import MimoVoiceDesignClient, MimoVoiceDesignError
from utils.voice_preview_text import normalize_voice_preview_language

# Backward-compatible private alias for existing router-package imports.
_normalize_voice_preview_language = normalize_voice_preview_language


def _get_voice_preview_language(request: Request, language: object = None, i18n_language: object = None) -> str:
    """Pick the preview text by the frontend i18n language; defaults to the legacy Chinese preview."""
    for candidate in (language, i18n_language):
        normalized = _normalize_voice_preview_language(candidate)
        if normalized:
            return normalized

    accept_lang = request.headers.get("Accept-Language", "")
    if accept_lang:
        for language_part in accept_lang.split(","):
            normalized = _normalize_voice_preview_language(language_part.split(";")[0].strip())
            if normalized:
                return normalized

    return "zh-CN"


def _is_free_preset_voice_id(voice_id: object) -> bool:
    """Check whether this is a runtime free preset voice."""
    normalized = str(voice_id or "").strip()
    if not normalized:
        return False
    try:
        from utils.api_config_loader import get_free_voices
        free_voice_ids = set((get_free_voices() or {}).values())
    except Exception:
        return False
    return normalized in free_voice_ids


def _get_active_native_preview_provider(config_manager, voice_id: object) -> str | None:
    """Decide whether the voice_id should take the current realtime provider's native preview path."""
    normalized = str(voice_id or "").strip()
    if not normalized:
        return None
    active_provider = get_active_realtime_native_provider_for_ui(config_manager)
    if not active_provider:
        return None
    _, uses_provider_native_voice = resolve_native_voice_for_routing(
        active_provider,
        normalized,
        config_manager.voice_id_exists_in_any_storage,
    )
    if uses_provider_native_voice:
        return active_provider
    return None


def _is_unpreviewable_selected_preset_voice(config_manager, core_config, voice_id, voice_data) -> bool:
    """Whether voice_id is a built-in preset of the currently selected hosted/local
    provider (e.g. MiMo) that has no dedicated preview path yet — and is NOT a user clone.

    A cloned voice whose id collides with a preset name must still preview through its
    clone path: runtime dispatch selects clone providers (priority 30/40/50) ahead of a
    static-catalog provider like MiMo (60), so the clone wins. Any id present in a voice
    storage bucket is therefore never treated as an unpreviewable preset (dual to the
    native-preview collision guard, which passes voice_id_exists_in_any_storage)."""
    try:
        selected_preset_key = tts_provider_registry.selected_preset_provider_key(
            core_config or {}, config_manager, voice_id
        )
        selected_provider = tts_provider_registry.get(selected_preset_key)
        if selected_provider and selected_provider.configured_preset_voice:
            # The exact configured preset owns preview routing over a stale clone.
            # Exact configured Voice IDs belong to the current Custom API. Even same-name stored
            # clones cannot use legacy preview paths to split preview/runtime identity.
            return True
    except Exception:
        logger.debug("configured preset preview 归属判定失败，按普通冲突规则继续", exc_info=True)

    if voice_data:
        return False
    try:
        if config_manager.voice_id_exists_in_any_storage(voice_id):
            return False
    except Exception:
        # Rare bucket-query I/O errors mean clone ownership is unconfirmed; continue preset checks below
        # without changing conclusions from one failed query. Debug-log traceback as in _grok collision lookup.
        logger.debug("voice_id_exists_in_any_storage 查询失败，按非克隆继续判定", exc_info=True)
    return tts_provider_registry.is_selected_preset_voice(core_config or {}, config_manager, voice_id)


def _read_wav_payload(audio_bytes: bytes) -> tuple[bytes, int, int, int]:
    """Read the WAV returned by upstream; returns PCM plus channel count, sample width and sample rate."""
    with io.BytesIO(audio_bytes) as wav_io:
        with wave.open(wav_io, "rb") as wav_file:
            pcm_data = wav_file.readframes(wav_file.getnframes())
            return (
                pcm_data,
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
            )


def _build_wav_payload(pcm_chunks: list[bytes], channels: int, sample_width: int, sample_rate: int) -> bytes:
    """Wrap multiple PCM chunks into a single WAV playable directly by the frontend Audio element."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(pcm_chunks))
    return out.getvalue()


async def _synthesize_step_voice_preview(
    voice_id: str,
    preview_line: str,
    preview_language: str,
    audio_api_key: str = "",
    *,
    free_mode: bool = False,
) -> bytes:
    """Generate a preview WAV using the StepFun/free TTS WebSocket."""
    import websockets

    from main_logic.tts_client import _adjust_free_tts_url, _build_step_tts_create_data

    tts_url = (
        _adjust_free_tts_url("wss://www.lanlan.tech/tts")
        if free_mode
        else "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
    )
    headers = {"Authorization": f"Bearer {audio_api_key or ''}"}
    lang_hint = "ja" if preview_language == "ja" else None
    is_lanlan_app = "lanlan.app" in tts_url
    session_id = ""
    pcm_chunks: list[bytes] = []
    wav_meta: tuple[int, int, int] | None = None

    async with asyncio.timeout(20):
        async with websockets.connect(tts_url, additional_headers=headers) as ws:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "tts.connection.done":
                    session_id = event.get("data", {}).get("session_id") or ""
                    break
                if event_type == "tts.response.error":
                    raise RuntimeError(str(event.get("data") or event))

            if not session_id:
                raise RuntimeError("TTS 连接未返回 session_id")

            create_data = _build_step_tts_create_data(session_id, voice_id, lang_hint, is_lanlan_app)

            await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
            await ws.send(json.dumps({
                "type": "tts.text.delta",
                "data": {"session_id": session_id, "text": preview_line},
            }))
            await ws.send(json.dumps({
                "type": "tts.text.done",
                "data": {"session_id": session_id},
            }))

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "tts.response.error":
                    raise RuntimeError(str(event.get("data") or event))
                if event_type == "tts.response.audio.delta":
                    audio_b64 = event.get("data", {}).get("audio", "")
                    if audio_b64:
                        pcm_data, channels, sample_width, sample_rate = _read_wav_payload(base64.b64decode(audio_b64))
                        pcm_chunks.append(pcm_data)
                        wav_meta = wav_meta or (channels, sample_width, sample_rate)
                elif event_type in ("tts.response.done", "tts.response.audio.done"):
                    break

    if not pcm_chunks or wav_meta is None:
        raise RuntimeError("TTS 未返回音频")

    channels, sample_width, sample_rate = wav_meta
    return _build_wav_payload(pcm_chunks, channels, sample_width, sample_rate)


async def _synthesize_free_voice_preview(voice_id: str, preview_line: str, preview_language: str, audio_api_key: str = "") -> bytes:
    """Generate a preview WAV for a free preset voice using the free TTS WebSocket."""
    return await _synthesize_step_voice_preview(
        voice_id=voice_id,
        preview_line=preview_line,
        preview_language=preview_language,
        audio_api_key=audio_api_key,
        free_mode=True,
    )


async def _synthesize_gemini_native_voice_preview(voice_id: str, preview_line: str, audio_api_key: str) -> bytes:
    """Generate a preview WAV using Gemini native TTS."""
    from utils.tts.providers.gemini import GEMINI_TTS_MODEL, normalize_gemini_tts_voice

    normalized_voice_id, recognized = normalize_gemini_tts_voice(voice_id)
    if not recognized:
        raise ValueError(f"不支持的 Gemini 原生音色: {voice_id}")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_TTS_MODEL}:generateContent?key={audio_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": f"Say the text with a proper tone, don't omit or add any words:\n\"{preview_line}\""}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": normalized_voice_id}
                }
            },
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(14, connect=10)) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        if parts:
            audio_b64 = parts[0].get("inlineData", {}).get("data")
            if audio_b64:
                return _build_wav_payload([base64.b64decode(audio_b64)], 1, 2, 24000)
    raise RuntimeError("Gemini TTS 未返回音频")


def _build_free_intl_voice_pins(native_catalog: dict, voice_id_exists=None) -> list[dict]:
    """The two pinned voices at the top of the overseas free (free_intl) list.

    - yui: the initial/default character voice; sends the literal "yui" (the
      server maps it to yui's dedicated voice).
    - default: synonymous with Leda, sends "Leda". It still remains a normal
      entry in the long Gemini list (no dedup); this merely pins an extra copy
      on top relabeled as "default".

    Display names are localized by the frontend via i18n_key; this only provides
    voice_id / i18n_key / fallback prefix.

    voice_id_exists: if a pin's voice_id collides with a user-registered/cloned
    voice (e.g. a local-TTS user created a voice whose ID is "yui"/"Leda"), the
    runtime routing prefers the cloned path on collision and no longer treats it
    as native (see the collision branch of NativeVoiceProvider.resolve_for_routing),
    so clicking the pinned entry would never reach Gemini — hide it outright to
    avoid misleading the user.
    """
    def _pin(voice_id: str, i18n_key: str, fallback: str) -> dict | None:
        if callable(voice_id_exists) and voice_id_exists(voice_id):
            return None
        meta = native_catalog.get(voice_id) or {}
        return {
            "voice_id": voice_id,
            "i18n_key": i18n_key,
            "prefix": meta.get("prefix") or fallback,
            "provider": "free_intl",
            "builtin": True,
        }

    pins = [
        _pin("yui", "voice.freeVoice.yui", "Yui"),
        _pin("Leda", "voice.freeVoice.default", "Default"),
    ]
    return [pin for pin in pins if pin is not None]


@router.get('/voices')
async def get_voices():
    """Get all registered voices for the current API key."""
    _config_manager = get_config_manager()
    # Like /voice_preview, this request reads regional classification three times: get_voices_for_current_api,
    # aget_core_config below, and get_active_realtime_native_provider_for_ui. Unsettled
    # readings may disagree, combining overseas free_intl native catalogs with mainland free_voices
    # or vice versa. They are disjoint, so selected voices will fail at runtime.
    # Settle first so the whole catalog shares one result. Already settled costs nothing; custom-API users trigger no probe.
    try:
        if not await _config_manager.aensure_region_resolved():
            logger.warning(
                "[GeoIP] 音色列表开始时区域判定仍未落定；若结论恰在本请求中途到达，"
                "目录可能混用两个区域的音色，刷新即可"
            )
    except Exception:
        logger.debug("[GeoIP] 音色列表区域落定失败，按当前配置继续", exc_info=True)

    from utils.voice_management.service import public_voice_data
    voices = await asyncio.to_thread(_config_manager.get_voices_for_current_api, for_listing=True)
    result = {"voices": {
        ref: public_voice_data(metadata) if isinstance(metadata, dict) and metadata.get("origin") == "import" else metadata
        for ref, metadata in voices.items()
    }}

    core_config = await _config_manager.aget_core_config()
    # First inspect selected providers with static preset catalogs, e.g. hosted MiMo, following dispatch
    # priority. Selected preset_catalog providers override core-native
    # (assistApi=mimo has dispatch priority 60 before native). Earlier-selected providers without
    # static catalogs, such as GPT-SoVITS/vLLM, expose none (preset=None). Reuse
    # native_voices: frontend source-first selection automatically groups entries by provider/provider_label
    # as <Provider> presets without adding source channels.
    # Catalog and dispatch share priorities: determine the registry provider winning current configuration first.
    # - Winner has a static preset catalog, e.g. MiMo: expose it over core-native. Use is not None
    #   so empty catalogs still count as matches and do not wrongly fall back.
    # - Winner lacks a static catalog, e.g. self-configured vLLM-Omni/GPT-SoVITS: expose neither a catalog
    #   nor core-native fallback. Dispatch targets that winner; showing Gemini/native voices would
    #   pass unsupported IDs to it (PR #1848 Codex review).
    # - No registry provider wins: fall back to core-native Gemini/Step/etc.
    # Reuse native_voices; frontend source-first selection groups entries by provider/provider_label.
    # as <Provider> presets without adding source channels.
    winning_provider_key = tts_provider_registry.selected_provider_key(
        core_config or {}, _config_manager
    )
    selected_preset_catalog = (
        tts_provider_registry.preset_catalog_for_ui(winning_provider_key, core_config)
        if winning_provider_key else None
    )
    active_native_provider = (
        get_active_realtime_native_provider_for_ui(_config_manager)
        if winning_provider_key is None else None
    )
    if selected_preset_catalog is not None:
        selected_provider = tts_provider_registry.get(winning_provider_key)
        if selected_provider and selected_provider.configured_preset_voice:
            # Remove colliding clone rows so every frontend keeps the winning owner.
            # Configuration presets explicitly own exact Voice IDs. Remove same-name historical
            # clones from catalogs so frontend deduplication cannot hide the Custom API entry that actually wins.
            # Runtime configured-preset ownership is exact and case-sensitive.
            # Catalog deduplication must match runtime exact-ID semantics; differently cased clones remain selectable.
            configured_ids = {str(voice_id) for voice_id in selected_preset_catalog}
            result["voices"] = {
                voice_id: voice_data
                for voice_id, voice_data in (result["voices"] or {}).items()
                if str(voice_id) not in configured_ids
            }
        result["native_voices"] = selected_preset_catalog
    elif active_native_provider:
        native_catalog = get_native_voice_catalog_for_ui(active_native_provider) or {}
        if active_native_provider == 'free_intl':
            # Overseas free lanlan.app/Gemini: pin yui and default (=Leda) first,
            # followed by the full Gemini catalog. Move yui into pins without duplication;
            # retain Leda as an ordinary long-list entry, the default pin's target.
            # Frontend localizes pin labels through i18n_key.
            voice_exists = getattr(_config_manager, "voice_id_exists_in_any_storage", None)
            result["pinned_voices"] = _build_free_intl_voice_pins(
                native_catalog,
                voice_id_exists=voice_exists,
            )
            # Remove cross-API-key-bucket clone/custom-name collisions from the long list too:
            # runtime routing/preview rejects them as native through any-storage collision checks,
            # so selecting them cannot reach Gemini, symmetric with pin hiding. Frontend deduplicates
            # only current-API voices and misses cross-bucket collisions; enforce the same predicate server-side.
            def _free_intl_keep(voice_id: str) -> bool:
                if voice_id == 'yui':
                    return False
                if callable(voice_exists) and voice_exists(voice_id):
                    return False
                return True
            native_catalog = {
                voice_id: meta
                for voice_id, meta in native_catalog.items()
                if _free_intl_keep(voice_id)
            }
        result["native_voices"] = native_catalog

    # Free preset voices work only with core=free, independent of assist; core_url must still target
    # lanlan.tech free. should_block_free_voice_for_route handles overseas lanlan.app fallback.
    # core_config is already available; read CORE_API_TYPE directly, equivalent to is_free_voice(), avoiding another read.
    # Normalize CORE_URL with or '': .get(k,'') still returns None for present null values,
    # and `in None` would raise TypeError and return /voices 500.
    if core_config.get('CORE_API_TYPE') == 'free' and 'lanlan.tech' in (core_config.get('CORE_URL') or ''):
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if free_voices:
            result["free_voices"] = free_voices

    # Build voice_id -> character-name lists for frontend display.
    characters = await _config_manager.aload_characters()
    voice_owners = {}
    for catgirl_name, catgirl_config in characters.get('猫娘', {}).items():
        if not isinstance(catgirl_config, dict):
            logger.warning(f"角色配置格式异常，已跳过 voice_owners 统计: {catgirl_name}")
            continue
        vid = read_legacy_voice_id(get_reserved(catgirl_config, 'voice_id', default='', legacy_keys=('voice_id',)))
        if vid:
            voice_owners.setdefault(vid, []).append(catgirl_name)
    result["voice_owners"] = voice_owners

    return result


@router.get('/voice_preview')
async def get_voice_preview(
    request: Request,
    voice_id: str,
    language: str | None = None,
    i18n_language: str | None = None,
):
    """Get the voice preview audio."""
    try:
        _config_manager = get_config_manager()
        # This request reads regional classification twice, selecting catalogs then constructing TTS URLs. Unsettled
        # results can disagree, sending mainland-free-normalized voices to lanlan.app's disjoint
        # free_intl catalog and immediately failing preview. Settle first for one request-wide result.
        # Already settled costs nothing; custom-API users trigger no probe.
        # A window remains if waiting expires and classification arrives midrequest: catalogs and
        # TTS URLs may belong to different regions. Avoid threading results through every
        # cross-cutting native-registry function for a complete fix because the cost is one failed
        # preview, fixed by retry once settled. Warn for diagnosis here.
        try:
            if not await _config_manager.aensure_region_resolved():
                logger.warning(
                    "[GeoIP] 音色预览开始时区域判定仍未落定；若结论恰在本请求中途到达，"
                    "预览可能因线路与音色目录不匹配而失败，重试即可"
                )
        except Exception:
            logger.debug("[GeoIP] 音色预览区域落定失败，按当前配置继续", exc_info=True)

        voices = await asyncio.to_thread(_config_manager.get_voices_for_current_api)
        voice_data = voices.get(voice_id) if isinstance(voices, dict) else None
        provider = (voice_data or {}).get('provider', '')
        is_free_preset_voice = _is_free_preset_voice_id(voice_id)

        from utils.voice_config import is_imported_voice_ref
        from .imported_voice_preview import ImportedPreviewConfig, preview_response
        imported_preview = None
        if is_imported_voice_ref(voice_id) and (
            not voice_data or voice_data.get('origin') == 'import'
        ):
            from utils.voice_management.providers import get_adapter
            if not voice_data:
                return JSONResponse({
                    'success': False, 'error': 'IMPORTED_VOICE_UNAVAILABLE',
                    'code': 'IMPORTED_VOICE_UNAVAILABLE',
                }, status_code=409)
            runtime = await asyncio.to_thread(
                get_adapter(provider).resolve_runtime, _config_manager, voice_data=voice_data,
            )
            if runtime.scope_id != voice_data.get('scope_id') or not runtime.api_key:
                return JSONResponse({
                    'success': False, 'error': 'IMPORTED_VOICE_UNAVAILABLE',
                    'code': 'IMPORTED_VOICE_UNAVAILABLE',
                }, status_code=409)
            imported_preview = ImportedPreviewConfig(_config_manager, runtime, voice_data=voice_data)
            _config_manager = imported_preview
            voice_id = voice_data['remote_voice_id']
            is_free_preset_voice = False

        # Prefer API keys from tts_custom.
        try:
            tts_custom_config = await _config_manager.aget_model_api_config('tts_custom')
            audio_api_key = tts_custom_config.get('api_key', '')
        except Exception:
            audio_api_key = ''

        # Otherwise fall back to core configuration.
        # Codex review: an earlier unused core_config read above was dead code.
        # Repository-wide async migration added await to it, wasting I/O; remove it.
        if not audio_api_key:
            core_config = await _config_manager.aget_core_config()
            audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if imported_preview:
            audio_api_key = imported_preview.runtime.api_key

        cosyvoice_base_url = ''
        if provider in ('cosyvoice', 'cosyvoice_intl'):
            cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
            runtime_key = (cosyvoice_runtime.get('api_key') or '').strip()
            if runtime_key:
                audio_api_key = runtime_key
            elif provider == 'cosyvoice_intl':
                # Missing international keys must not reuse domestic keys from tts_custom/AUDIO_API_KEY
                # against international DashScope endpoints: guaranteed 401 obscures the actual missing-key issue.
                # Explicitly report missing keys, matching minimax/native Step/Gemini branches.
                return JSONResponse({
                    'success': False,
                    'error': 'TTS_AUDIO_API_KEY_MISSING',
                    'code': 'TTS_AUDIO_API_KEY_MISSING'
                }, status_code=400)
            cosyvoice_base_url = (
                (voice_data or {}).get('dashscope_base_url')
                or cosyvoice_runtime.get('base_url', '')
            )

        logger.info(f"正在为音色 {voice_id} 生成预览音频...")

        preview_language = _get_voice_preview_language(request, language, i18n_language)
        text = _loc(VOICE_PREVIEW_TEXTS, preview_language)

        # Hosted/local presets such as MiMo surface through native_voices and receive frontend preview buttons,
        # but preview requires their own synthesis path, not yet integrated.
        # Explicitly return unsupported-preview here instead of mistakenly passing provider-specific keys/IDs
        # into generic DashScope/CosyVoice below (PR #1848 Codex review; actual preview is future work).
        # Same-name clones remain allowed: clone providers precede MiMo in dispatch, so clone preview still applies.
        preview_core_config = await _config_manager.aget_core_config()
        if imported_preview and not await imported_preview.is_current():
            return JSONResponse({
                'success': False, 'error': 'IMPORTED_VOICE_UNAVAILABLE',
                'code': 'IMPORTED_VOICE_UNAVAILABLE',
            }, status_code=409)
        if _is_unpreviewable_selected_preset_voice(
            _config_manager, preview_core_config, voice_id, voice_data
        ):
            return JSONResponse({
                'success': False,
                'error': f'当前预制音色暂不支持试听: {voice_id}',
                'code': 'PRESET_VOICE_PREVIEW_UNSUPPORTED',
            }, status_code=400)

        # MiMo clone preview (provider=='mimo'): read base64 samples from voice_meta and synthesize
        # one preview sentence with voiceclone, symmetric with MiniMax clone preview; avoid the generic
        # CosyVoice/DashScope branch misusing mimo-clone-* IDs.
        if provider == 'mimo':
            if (voice_data or {}).get('source') == 'design':
                design_prompt = str((voice_data or {}).get('design_prompt') or '').strip()
                if not design_prompt:
                    return JSONResponse({
                        'success': False,
                        'error': f'MiMo designed voice is missing its description: {voice_id}',
                        'code': 'MIMO_VOICE_DESIGN_PROMPT_MISSING',
                    }, status_code=400)
                mimo_api_key = _config_manager.get_tts_api_key('mimo')
                if not mimo_api_key:
                    return JSONResponse({
                        'success': False,
                        'error': 'MIMO_API_KEY_MISSING',
                        'code': 'MIMO_API_KEY_MISSING',
                    }, status_code=400)
                if str(preview_core_config.get('assistApi') or '').strip().lower() == 'mimo':
                    mimo_base_url = (preview_core_config.get('OPENROUTER_URL') or '').strip()
                else:
                    mimo_base_url = str((voice_data or {}).get('mimo_base_url') or '').strip()
                try:
                    mimo_client = MimoVoiceDesignClient(api_key=mimo_api_key, base_url=mimo_base_url or None)
                    audio_data = await mimo_client.synthesize_design_preview(design_prompt, text=text)
                    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                    logger.info(
                        f"MiMo designed voice {voice_id} preview generated, size: {len(audio_data)} bytes"
                    )
                    return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
                except MimoVoiceDesignError as exc:
                    logger.error(f"MiMo designed voice {voice_id} preview failed: {exc}")
                    return JSONResponse({
                        'success': False,
                        'error': f'MiMo preview generation failed: {str(exc)}',
                        'code': 'MIMO_VOICE_PREVIEW_FAILED',
                    }, status_code=502)
                except Exception as exc:
                    logger.error(f"MiMo designed voice {voice_id} preview raised an error: {exc}")
                    return JSONResponse({
                        'success': False,
                        'error': f'MiMo preview generation failed: {str(exc)}',
                        'code': 'MIMO_VOICE_PREVIEW_FAILED',
                    }, status_code=500)

            sample_b64 = (voice_data or {}).get('clone_sample_b64') or ''
            if not sample_b64:
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 克隆音色缺少参考样本，无法试听: {voice_id}',
                    'code': 'MIMO_VOICE_SAMPLE_MISSING',
                }, status_code=400)
            mimo_api_key = _config_manager.get_tts_api_key('mimo')
            if not mimo_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'MIMO_API_KEY_MISSING',
                    'code': 'MIMO_API_KEY_MISSING',
                }, status_code=400)
            # Match dispatch base_url: assistApi=mimo, the only Token Plan case, uses OPENROUTER_URL;
            # otherwise use voice_meta.mimo_base_url or the default endpoint.
            if str(preview_core_config.get('assistApi') or '').strip().lower() == 'mimo':
                mimo_base_url = (preview_core_config.get('OPENROUTER_URL') or '').strip()
            else:
                mimo_base_url = str((voice_data or {}).get('mimo_base_url') or '').strip()
            try:
                sample_bytes = base64.b64decode(sample_b64)
            except ValueError:  # binascii.Error subclasses ValueError.
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 克隆音色样本损坏，无法试听: {voice_id}',
                    'code': 'MIMO_VOICE_SAMPLE_CORRUPT',
                }, status_code=400)
            try:
                mimo_client = MimoVoiceCloneClient(api_key=mimo_api_key, base_url=mimo_base_url or None)
                audio_data = await mimo_client.synthesize_preview(
                    sample_bytes,
                    (voice_data or {}).get('clone_sample_mime') or 'audio/wav',
                    text=text,
                )
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"MiMo 克隆音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
            except MimoVoiceCloneError as e:
                logger.error(f"MiMo 克隆音色 {voice_id} 预览失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 预览生成失败: {str(e)}',
                    'code': 'MIMO_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"MiMo 克隆音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 预览生成失败: {str(e)}',
                    'code': 'MIMO_VOICE_PREVIEW_FAILED',
                }, status_code=500)

        if provider == 'doubao_tts':
            doubao_api_key = _config_manager.get_tts_api_key('doubao_tts')
            if not doubao_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'DOUBAO_TTS_API_KEY_MISSING',
                    'code': 'DOUBAO_TTS_API_KEY_MISSING',
                }, status_code=400)
            doubao_base_url = (
                (voice_data or {}).get('doubao_base_url')
                or DOUBAO_TTS_DEFAULT_BASE_URL
            )
            doubao_resource_id = (
                (voice_data or {}).get('doubao_resource_id')
                or DOUBAO_VOICE_CLONE_RESOURCE_ID
            )
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        doubao_tts_url(doubao_base_url),
                        headers=doubao_api_headers(doubao_api_key, doubao_resource_id),
                        json=build_doubao_tts_payload(
                            text,
                            voice_id,
                            context_texts=(DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,),
                        ),
                    )
                if resp.status_code != 200:
                    return JSONResponse({
                        'success': False,
                        'error': f'豆包语音预览失败: HTTP {resp.status_code}, {resp.text[:200]}',
                        'code': 'DOUBAO_TTS_PREVIEW_FAILED',
                    }, status_code=502)
                audio_data = extract_doubao_audio_bytes(await resp.aread())
                if not audio_data:
                    return JSONResponse({
                        'success': False,
                        'error': '豆包语音预览未返回音频',
                        'code': 'DOUBAO_TTS_PREVIEW_NO_AUDIO',
                    }, status_code=502)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"豆包语音音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return await preview_response(imported_preview, {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'})
            except DoubaoTtsError as e:
                logger.error(f"豆包语音音色 {voice_id} 预览失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'豆包语音预览失败: {str(e)}',
                    'code': 'DOUBAO_TTS_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"豆包语音音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'豆包语音预览失败: {str(e)}',
                }, status_code=500)

        # GLM clone preview (provider=='glm_tts'): synthesize with the registered remote clone as /audio/speech
        # voice, symmetric with doubao_tts registration-based previews; avoid generic
        # CosyVoice/DashScope synthesis with invalid API keys returning 401.
        if provider == 'glm_tts':
            glm_api_key = _config_manager.get_tts_api_key('glm_tts')
            if not glm_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'GLM_TTS_API_KEY_MISSING',
                    'code': 'GLM_TTS_API_KEY_MISSING',
                }, status_code=400)
            glm_base_url = (voice_data or {}).get('glm_base_url')
            try:
                glm_client = GlmVoiceCloneClient(
                    api_key=glm_api_key,
                    base_url=glm_base_url,
                )
                audio_data = await glm_client.synthesize_preview(voice_id, text)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"GLM 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return await preview_response(imported_preview, {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'})
            except GlmTtsError as e:
                logger.error(f"GLM 音色 {voice_id} 预览失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'GLM 语音预览失败: {str(e)}',
                    'code': 'GLM_TTS_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"GLM 音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'GLM 语音预览失败: {str(e)}',
                    'code': 'GLM_TTS_PREVIEW_FAILED',
                }, status_code=500)

        # vLLM-Omni clone preview (provider=='vllm_omni'): read voice_meta reference audio
        # base64 and synthesize through its WebSocket, symmetric with MiMo; avoid generic
        # CosyVoice/DashScope synthesis with invalid API keys returning 401.
        if provider == 'vllm_omni':
            import numpy as np
            import soxr

            clone_sample_b64 = str((voice_data or {}).get('clone_sample_b64') or '').strip()
            if not clone_sample_b64:
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 克隆音色缺少参考样本，无法试听: {voice_id}',
                    'code': 'VLLM_OMNI_VOICE_SAMPLE_MISSING',
                }, status_code=400)
            # Build data: URIs with worker _build_vllm_omni_clone_data_uri,
            # sharing dispatch packaging, default audio/wav MIME, and empty-base64 guards,
            # avoiding subtle preview/dispatch differences from duplicated construction.
            from main_logic.tts_client.workers.vllm_omni import _build_vllm_omni_clone_data_uri
            from main_logic.tts_client._infra import _resample_audio
            clone_data_uri = _build_vllm_omni_clone_data_uri(voice_data)
            ref_text = str((voice_data or {}).get('clone_ref_text') or '').strip()
            # Prefer preview_core_config ttsModelUrl, then persisted voice_data
            # vllm_omni_base_url, avoiding legacy fixed-private-address fallback.
            base_url = str(
                (preview_core_config or {}).get('ttsModelUrl')
                or (preview_core_config or {}).get('TTS_MODEL_URL')
                or ''
            ).strip()
            if not base_url:
                base_url = str((voice_data or {}).get('vllm_omni_base_url') or '').strip()
            if not base_url:
                return JSONResponse({
                    'success': False,
                    'error': 'vLLM-Omni 服务地址未配置，请先在 TTS 设置中填写端点 URL',
                    'code': 'VLLM_OMNI_URL_MISSING',
                }, status_code=400)
            # Model comes from preview_core_config ttsModelId or default Qwen3-TTS.
            model = str((preview_core_config or {}).get('ttsModelId') or '').strip() or 'Qwen3-TTS'
            # Normalize URLs through worker _vllm_omni_normalize_ws_endpoint: http->ws, /v1 completion,
            # and idempotent endpoint joining. Avoid copied preview code missing protocol conversion and
            # always failing user-configured http:// endpoints, symmetric with vllm_omni_tts_worker.
            from main_logic.tts_client import _vllm_omni_normalize_ws_endpoint
            ws_endpoint = _vllm_omni_normalize_ws_endpoint(base_url)
            # API-key authentication matches worker _connect_and_config's two paths: handshake
            # Authorization and session.config.api_key. Authenticated vLLM clone previews
            # require keys too, otherwise 401 (C6 fix).
            vllm_api_key = str((preview_core_config or {}).get('ttsModelApiKey') or '').strip()
            ws_kwargs = {"max_size": None, "open_timeout": 10, "close_timeout": 5}
            if vllm_api_key:
                # Support old websockets by inspecting parameter names, avoiding broad TypeError catches
                # that swallow genuine communication errors, and avoiding await connect() + async with ws,
                # which reconnects by awaiting self again in websockets 16.0 connect.__aenter__ (C6-1 fix).
                # NOTE: preview inspects parameter names; workers still use try/except TypeError.
                # Strategies differ because preview uses short context-managed connections,
                # while workers manually manage long connections. Worker catches run only around
                # connect(), with send/recv outside, reducing swallowed-error risk.
                try:
                    _ws_sig = inspect.signature(websockets.connect)
                    _header_key = "additional_headers" if "additional_headers" in _ws_sig.parameters else "extra_headers"
                except (ValueError, TypeError):
                    _header_key = "additional_headers"  # Prefer newer versions.
                ws_kwargs[_header_key] = [
                    ("Authorization", f"Bearer {vllm_api_key}"),
                ]
            try:
                # Overall 30s timeout: vLLM-Omni preview has no server timeout, so accepted connections without
                # session.done (half-open/hung) leave async for blocked forever, occupying a worker. Match
                # worker _connect_and_config open_timeout/close_timeout.
                async with asyncio.timeout(30):
                    async with websockets.connect(ws_endpoint, **ws_kwargs) as ws:
                        # Send session.config.
                        config_msg = {
                            "type": "session.config",
                            "model": model,
                            "voice": "default",
                            "response_format": "pcm",
                            "speed": 1.0,
                            "stream_audio": True,
                            "split_granularity": "sentence",
                            "ref_audio": clone_data_uri,
                        }
                        if ref_text:
                            config_msg["ref_text"] = ref_text
                        # Session authentication: some self-hosted servers read api_key from config.
                        if vllm_api_key:
                            config_msg["api_key"] = vllm_api_key
                        await ws.send(json.dumps(config_msg))
                        # Send preview text.
                        await ws.send(json.dumps({"type": "input.text", "text": text}))
                        await ws.send(json.dumps({"type": "input.done"}))
                        # Receive PCM binary frames (24kHz/16bit/mono) and JSON events.
                        pcm_chunks: list[bytes] = []
                        # Streaming resampling preserves filter state across chunks, avoiding boundary noise from stateless
                        # soxr.resample() resets, matching the worker's ResampleStream.
                        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
                        async for message in ws:
                            if isinstance(message, bytes):
                                if len(message) < 2:
                                    continue
                                audio_array = np.frombuffer(message, dtype=np.int16)
                                pcm_chunks.append(_resample_audio(audio_array, 24000, 48000, resampler))
                            else:
                                try:
                                    event = json.loads(message)
                                except json.JSONDecodeError:
                                    continue
                                event_type = event.get("type", "")
                                if event_type == "session.done":
                                    break
                                elif event_type == "error":
                                    error_msg = event.get("message", event.get("error", str(event)))
                                    logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览服务端错误: {error_msg}")
                                    return JSONResponse({
                                        'success': False,
                                        'error': f'vLLM-Omni 预览生成失败: {error_msg}',
                                        'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                                    }, status_code=502)
                # Empty-frame guard: explicit response_format:pcm returns raw PCM without a 44-byte WAV header,
                # added below by _build_wav_payload. If session.done arrives without any PCM frames
                # (server synthesis failure without error events, or all frames shorter than two bytes),
                # _build_wav_payload([]) produces a header-only 44-byte empty WAV.
                # Returning success:True makes preview silently inaudible without error. Ordinary error events
                # catch most failures; this is extra protection.
                if not pcm_chunks:
                    logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览未返回任何音频帧")
                    return JSONResponse({
                        'success': False,
                        'error': 'vLLM-Omni 预览未返回任何音频帧',
                        'code': 'VLLM_OMNI_VOICE_PREVIEW_NO_AUDIO',
                    }, status_code=502)
                # Package WAV by adding a 44-byte header to raw PCM.
                audio_data = _build_wav_payload(pcm_chunks, 1, 2, 48000)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"vLLM-Omni 克隆音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
            except (TimeoutError, asyncio.TimeoutError):
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览超时（30秒）")
                return JSONResponse({
                    'success': False,
                    'error': 'vLLM-Omni 预览生成失败: 服务端响应超时（30秒）',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_TIMEOUT',
                }, status_code=504)
            except websockets.exceptions.ConnectionClosed as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览 WebSocket 连接关闭: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: WebSocket 连接异常',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except (OSError, ConnectionError) as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览连接失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: 连接失败',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: {str(e)}',
                }, status_code=500)

        native_preview_provider = _get_active_native_preview_provider(_config_manager, voice_id)
        if native_preview_provider:
            native_voice_id, _ = normalize_native_voice(native_preview_provider, voice_id)
            try:
                if native_preview_provider in ('step', 'free', 'free_intl'):
                    # Read only tts_default.api_key, matching step_realtime_tts_worker;
                    # never fall back to audio_api_key from tts_custom/AUDIO_API_KEY above, which carries
                    # GPT-SoVITS/CosyVoice or another provider's bearer. Sending it to
                    # api.stepfun.com always returns 401, obscuring explicit missing-key errors.
                    # free_intl overseas Gemini-proxy previews follow free via www.lanlan.app/tts
                    # streaming synthesis, StepFun-shaped with voice_id forwarded to Gemini.
                    try:
                        native_tts_config = await _config_manager.aget_model_api_config('tts_default')
                        native_audio_api_key = native_tts_config.get('api_key', '') or ''
                    except Exception:
                        native_audio_api_key = ''
                    if native_preview_provider == 'step' and not native_audio_api_key:
                        return JSONResponse({
                            'success': False,
                            'error': 'TTS_AUDIO_API_KEY_MISSING',
                            'code': 'TTS_AUDIO_API_KEY_MISSING'
                        }, status_code=400)
                    audio_data = await _synthesize_step_voice_preview(
                        voice_id=native_voice_id,
                        preview_line=text,
                        preview_language=preview_language,
                        audio_api_key=native_audio_api_key,
                        free_mode=(native_preview_provider in ('free', 'free_intl')),
                    )
                elif native_preview_provider == 'gemini':
                    core_config = await _config_manager.aget_core_config()
                    native_audio_api_key = (core_config or {}).get('CORE_API_KEY', '')
                    if not native_audio_api_key:
                        return JSONResponse({
                            'success': False,
                            'error': 'TTS_AUDIO_API_KEY_MISSING',
                            'code': 'TTS_AUDIO_API_KEY_MISSING'
                        }, status_code=400)
                    audio_data = await _synthesize_gemini_native_voice_preview(
                        voice_id=native_voice_id,
                        preview_line=text,
                        audio_api_key=native_audio_api_key,
                    )
                else:
                    return JSONResponse({
                        'success': False,
                        'error': f'当前原生音色暂不支持预览: {native_preview_provider}'
                    }, status_code=400)

                logger.info(f"原生音色 {native_voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/wav'
                }
            except Exception as e:
                logger.error(f"原生音色 {native_voice_id} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'原生音色预览生成失败: {str(e)}'
                }, status_code=500)

        if provider == 'elevenlabs' or voice_id.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
            try:
                # Extract clone-persisted base_url from voice_data for the helper.
                audio_data, error_code = await _elevenlabs_synthesize_preview(
                    _config_manager,
                    voice_id,
                    text,
                    base_url=(voice_data or {}).get('elevenlabs_base_url')
                )
                if error_code:
                    return JSONResponse({
                        'success': False,
                        'error': error_code,
                        'code': error_code
                    }, status_code=400)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"ElevenLabs 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return await preview_response(imported_preview, {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                })
            except ElevenLabsUpstreamError as e:
                logger.error(f"ElevenLabs 预览上游服务错误 ({e.status_code}): {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs 上游服务错误: {str(e)}',
                    'code': 'ELEVENLABS_UPSTREAM_ERROR'
                }, status_code=502)
            except ValueError as e:
                # Add a dedicated 4xx client-error catch.
                logger.error(f"ElevenLabs 预览请求失败 (4xx): {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs 请求参数或验证错误: {str(e)}',
                    'code': 'ELEVENLABS_API_ERROR'
                }, status_code=400)
            except Exception as e:
                logger.error(f"ElevenLabs 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs预览生成失败: {str(e)}'
                }, status_code=500)
        if is_free_preset_voice:
            try:
                audio_data = await _synthesize_free_voice_preview(
                    voice_id=voice_id,
                    preview_line=text,
                    preview_language=preview_language,
                    audio_api_key=audio_api_key or '',
                )
                logger.info(f"免费预设音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/wav'
                }
            except Exception as e:
                logger.error(f"免费预设音色 {voice_id} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'免费预设音色预览生成失败: {str(e)}'
                }, status_code=500)

        if provider in ('minimax', 'minimax_intl'):
            minimax_api_key = _config_manager.get_tts_api_key(provider)
            if not minimax_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'MINIMAX_API_KEY_MISSING',
                    'code': 'MINIMAX_API_KEY_MISSING'
                }, status_code=400)

            minimax_base_url = (voice_data or {}).get('minimax_base_url') or get_minimax_base_url(provider)
            provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

            try:
                minimax_client = MinimaxVoiceCloneClient(api_key=minimax_api_key, base_url=minimax_base_url)
                audio_data = await minimax_client.synthesize_preview(voice_id=voice_id, text=text)
                logger.info(f"{provider_label} 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return await preview_response(imported_preview, {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                })
            except MinimaxVoiceCloneError as e:
                logger.error(f"{provider_label} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'{provider_label}预览生成失败: {str(e)}'
                }, status_code=500)

        if not audio_api_key:
            return JSONResponse({'success': False, 'error': 'TTS_AUDIO_API_KEY_MISSING', 'code': 'TTS_AUDIO_API_KEY_MISSING'}, status_code=400)

        # Generate audio.
        try:
            tts_api_config = await _config_manager.aget_model_api_config('tts_custom')
        except Exception as e:
            logger.warning("DashScope 预览地域 URL 读取失败，回退到默认地域: %s", e, exc_info=True)
            tts_api_config = {}
        if provider == 'cosyvoice_intl':
            preview_base_url = (
                cosyvoice_base_url
                or (tts_api_config or {}).get('ttsModelUrl')
                or (tts_api_config or {}).get('TTS_MODEL_URL')
                or ''
            )
        else:
            preview_base_url = (
                (tts_api_config or {}).get('ttsModelUrl')
                or (tts_api_config or {}).get('TTS_MODEL_URL')
                or cosyvoice_base_url
                or ''
            )

        from utils.api_config_loader import get_cosyvoice_clone_model
        if imported_preview:
            preview_base_url = imported_preview.runtime.base_url
        if imported_preview and not await imported_preview.is_current():
            return JSONResponse({
                'success': False, 'error': 'IMPORTED_VOICE_UNAVAILABLE',
                'code': 'IMPORTED_VOICE_UNAVAILABLE',
            }, status_code=409)
        clone_model = (
            (voice_data or {}).get('design_model')
            or (voice_data or {}).get('clone_model')
            or get_cosyvoice_clone_model(provider)
        )

        def _do_preview_synthesize():
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer
            # Hold DASHSCOPE_GLOBAL_LOCK across module-global writes, SpeechSynthesizer construction,
            # and synthesizer.call: dashscope.api_key/base_*_api_url are shared mutable state
            # across in-process flows; concurrent calls overwrite keys/regions.
            # Lock the whole call because SpeechSynthesizer.call is synchronous and one-shot;
            # the few-second lock does not block the event loop because execution uses to_thread.
            with DASHSCOPE_GLOBAL_LOCK, prefer_dashscope_websocket_ipv4():
                dashscope.api_key = audio_api_key
                try:
                    configure_dashscope_sdk_urls(
                        dashscope,
                        preview_base_url,
                        websocket_path="inference",
                    )
                except Exception as e:
                    logger.warning("DashScope 预览地域 URL 配置失败，已重置为默认地域: %s", e, exc_info=True)
                    configure_dashscope_sdk_urls(dashscope, "", websocket_path="inference")
                synthesizer = SpeechSynthesizer(model=clone_model, voice=voice_id)
                return synthesizer, synthesizer.call(text)

        try:
            synthesizer, audio_data = await asyncio.to_thread(_do_preview_synthesize)

            if not audio_data:
                request_id = getattr(synthesizer, 'get_last_request_id', lambda: 'unknown')()
                logger.error(f"生成音频失败: audio_data 为空. Request ID: {request_id}")
                return JSONResponse({
                    'success': False,
                    'error': f'生成音频失败 (Request ID: {request_id})。请检查 API Key 额度或音色 ID 是否有效。'
                }, status_code=500)

            logger.info(f"音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")

            # Convert audio bytes to base64 text.
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')

            return await preview_response(imported_preview, {
                "success": True,
                "audio": audio_base64,
                "mime_type": "audio/mpeg"
            })
        except Exception as e:
            error_msg = str(e)
            logger.error(f"SpeechSynthesizer 调用异常: {error_msg}")
            return JSONResponse({
                'success': False,
                'error': f'语音合成异常: {error_msg}'
            }, status_code=500)
    except Exception as e:
        logger.error(f"生成音色预览失败: {e}")
        return JSONResponse({'success': False, 'error': f'系统错误: {str(e)}'}, status_code=500)

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
from utils.elevenlabs_tts_voices import (
    ELEVENLABS_TTS_VOICE_PREFIX,
)
from utils.config_manager import (
    get_reserved,
)
from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls
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
from utils.voice_config import read_legacy_voice_id
from utils.native_voice_registry import (
    get_active_realtime_native_provider_for_ui,
    get_native_voice_catalog_for_ui,
    normalize_native_voice,
    resolve_native_voice_for_routing,
)
from utils import tts_provider_registry
from utils.voice_clone import (
    MinimaxVoiceCloneClient,
    MinimaxVoiceCloneError,
    get_minimax_base_url,
    MimoVoiceCloneClient,
    MimoVoiceCloneError,
)
from utils.language_utils import is_supported_language_code, normalize_language_code


def _normalize_voice_preview_language(raw_language: object) -> str | None:
    """Normalize the voice preview language; returns None for invalid values so other sources can be tried."""
    raw = str(raw_language or "").strip()
    if not raw or not is_supported_language_code(raw):
        return None
    normalized = normalize_language_code(raw, format="full")
    if normalized in VOICE_PREVIEW_TEXTS:
        return normalized
    return None


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
    if voice_data:
        return False
    try:
        if config_manager.voice_id_exists_in_any_storage(voice_id):
            return False
    except Exception:
        # 存储桶查询异常（极少见的 IO 错误）：按「无法确认是克隆」继续走下方预制判定，
        # 不因一次查询失败改变结论；留一条带堆栈的 debug 便于排查（同 _grok 撞名查模式）。
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
    from utils.gemini_tts_voices import GEMINI_TTS_MODEL, normalize_gemini_tts_voice

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
    result = {"voices": _config_manager.get_voices_for_current_api(for_listing=True)}

    core_config = await _config_manager.aget_core_config()
    # 先看有没有自带静态预制目录的 provider 被选中（如 MiMo，hosted）。与 dispatch
    # 同一优先级判定：选中的 provider 若有 preset_catalog 就用它，并压过 core-native
    # （assistApi=mimo 在 dispatch 里 priority 60 也先于 native 命中）；GPT-SoVITS /
    # vLLM 这类先命中、无静态目录的 provider 则不出目录（preset 为 None）。复用
    # native_voices 通道——前端 source-first 选声器按 entry 的 provider/provider_label
    # 自动分组成「<Provider> · 预制」，无需新增来源通道。
    # 选声目录与 dispatch 同一优先级：先看哪个注册表 provider 赢得当前配置。
    #  - 赢家有静态预制目录（如 MiMo）→ 出该目录，压过 core-native（用 is not None，
    #    空目录也算命中，不误回退）。
    #  - 赢家无静态目录（vLLM-Omni / GPT-SoVITS，用户自填/自部署音色）→ 既不出目录、
    #    也不回退 core-native：dispatch 会路由到该赢家，露出 gemini 等原生音色会让用户
    #    选中后被误传给赢家触发 unsupported-voice（PR #1848 Codex review）。
    #  - 无注册表 provider 赢 → 回退 core-native（gemini/step/...）。
    # 复用 native_voices 通道——前端 source-first 选声器按 entry 的 provider/provider_label
    # 自动分组成「<Provider> · 预制」，无需新增来源通道。
    winning_provider_key = tts_provider_registry.selected_provider_key(
        core_config or {}, _config_manager
    )
    selected_preset_catalog = (
        tts_provider_registry.preset_catalog_for_ui(winning_provider_key)
        if winning_provider_key else None
    )
    active_native_provider = (
        get_active_realtime_native_provider_for_ui(_config_manager)
        if winning_provider_key is None else None
    )
    if selected_preset_catalog is not None:
        result["native_voices"] = selected_preset_catalog
    elif active_native_provider:
        native_catalog = get_native_voice_catalog_for_ui(active_native_provider) or {}
        if active_native_provider == 'free_intl':
            # 海外免费（lanlan.app/Gemini）：yui + default(=Leda) 两个置顶 pin，
            # 其后是 Gemini 全量目录。yui 从长列表里挪到 pin（不重复展示）；
            # Leda 不去重，仍作为普通条目留在长列表里（= default pin 的目标）。
            # pin 的展示名由前端按 i18n_key 本地化。
            voice_exists = getattr(_config_manager, "voice_id_exists_in_any_storage", None)
            result["pinned_voices"] = _build_free_intl_voice_pins(
                native_catalog,
                voice_id_exists=voice_exists,
            )
            # 撞名（跨 api-key 桶存在同名克隆/自定义音色）的条目也从长列表里去掉：
            # runtime 路由/preview 用 any-storage 撞名判定会拒绝当 native，展示了
            # 点选也到不了 Gemini（与 pin 的撞名隐藏对偶）。前端只按当前 api 的
            # voices 去重，跨桶撞名漏网，故在后端按同一谓词收口。
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

    # 免费预设音色只在 core=free 运行时可用（与 assist 无关）；core_url 仍须指向
    # lanlan.tech 免费端点，海外 lanlan.app 路由由 should_block_free_voice_for_route 兜底。
    # 此处已持有 core_config，直接读 CORE_API_TYPE（等价 is_free_voice()），省一次读取。
    # CORE_URL 用 `or ''` 归一化：key 存在但值为 None 时 `.get(k, '')` 仍返回 None，
    # `in None` 会抛 TypeError 让 /voices 500。
    if core_config.get('CORE_API_TYPE') == 'free' and 'lanlan.tech' in (core_config.get('CORE_URL') or ''):
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if free_voices:
            result["free_voices"] = free_voices

    # 构建 voice_id → 使用该音色的角色名列表，用于前端显示
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
        voices = _config_manager.get_voices_for_current_api()
        voice_data = voices.get(voice_id) if isinstance(voices, dict) else None
        provider = (voice_data or {}).get('provider', '')
        is_free_preset_voice = _is_free_preset_voice_id(voice_id)

        # 优先尝试从 tts_custom 获取 API Key
        try:
            tts_custom_config = _config_manager.get_model_api_config('tts_custom')
            audio_api_key = tts_custom_config.get('api_key', '')
        except Exception:
            audio_api_key = ''

        # 如果没有，则回退到核心配置
        # Codex review: 原先这里顶上还有一个 `core_config = ...`，从未被读取（死代码）。
        # 全仓 async 化时把死读也改成了 await，反而白跑一次 IO，删。
        if not audio_api_key:
            core_config = await _config_manager.aget_core_config()
            audio_api_key = core_config.get('AUDIO_API_KEY', '')

        cosyvoice_base_url = ''
        if provider in ('cosyvoice', 'cosyvoice_intl'):
            cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
            runtime_key = (cosyvoice_runtime.get('api_key') or '').strip()
            if runtime_key:
                audio_api_key = runtime_key
            elif provider == 'cosyvoice_intl':
                # intl key 缺失时不要继续用顶上从 tts_custom/AUDIO_API_KEY 拿到的
                # 国内 key 去打 intl DashScope 端点，必然 401，错误现象比明确缺 key
                # 难排查。和 minimax/native step/gemini 分支一致显式返回缺 key。
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

        # hosted/local provider 的预制音色（如选中 MiMo 时的预制声线）经 native_voices
        # 通道露给前端会渲染试听按钮，但其试听需走该 provider 自己的合成路径（尚未接）。
        # 在此显式拦下返回「暂不支持试听」，避免落到下方 DashScope/CosyVoice 通用分支拿着
        # 该 provider 的 key/voice_id 误合成（PR #1848 Codex review；真试听留作后续）。
        # 与预制同名的克隆音色不拦（dispatch 克隆 provider 先于 MiMo 命中），仍走克隆试听。
        preview_core_config = await _config_manager.aget_core_config()
        if _is_unpreviewable_selected_preset_voice(
            _config_manager, preview_core_config, voice_id, voice_data
        ):
            return JSONResponse({
                'success': False,
                'error': f'当前预制音色暂不支持试听: {voice_id}',
                'code': 'PRESET_VOICE_PREVIEW_UNSUPPORTED',
            }, status_code=400)

        # MiMo 克隆音色（provider=='mimo'）试听：读 voice_meta 里的参考样本 base64，用
        # voiceclone 模型一次性合成预览句（对偶 MiniMax 的克隆试听；避免落到下方
        # CosyVoice/DashScope 通用分支拿着 mimo-clone-* 的 id 误合成）。
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
                    mimo_client = MimoVoiceCloneClient(api_key=mimo_api_key, base_url=mimo_base_url or None)
                    audio_data = await mimo_client.synthesize_design_preview(design_prompt, text=text)
                    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                    logger.info(
                        f"MiMo designed voice {voice_id} preview generated, size: {len(audio_data)} bytes"
                    )
                    return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
                except MimoVoiceCloneError as exc:
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
            # base_url 与 dispatch 同源：assistApi=mimo（Token Plan 唯一场景）用 OPENROUTER_URL，
            # 否则用 voice_meta 存的 mimo_base_url，缺省默认端点。
            if str(preview_core_config.get('assistApi') or '').strip().lower() == 'mimo':
                mimo_base_url = (preview_core_config.get('OPENROUTER_URL') or '').strip()
            else:
                mimo_base_url = str((voice_data or {}).get('mimo_base_url') or '').strip()
            try:
                sample_bytes = base64.b64decode(sample_b64)
            except ValueError:  # binascii.Error 是 ValueError 子类
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
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
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

        # vLLM-Omni 克隆音色（provider=='vllm_omni'）试听：读 voice_meta 里的参考样本
        # base64，通过 vLLM-Omni WebSocket 合成预览句（对偶 MiMo 的克隆试听；避免落到
        # 下方 CosyVoice/DashScope 通用分支拿着无效 API key 误合成报 401）。
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
            # 构建 data: URI：复用 worker 的 _build_vllm_omni_clone_data_uri，
            # 与 dispatch 路径同一封装（含 MIME 默认值 audio/wav 与空 b64 保护），
            # 避免预览/dispatch 两处独立维护拼接逻辑时悄悄产生行为差异。
            from main_logic.tts_client.workers.vllm_omni import _build_vllm_omni_clone_data_uri
            from main_logic.tts_client._infra import _resample_audio
            clone_data_uri = _build_vllm_omni_clone_data_uri(voice_data)
            ref_text = str((voice_data or {}).get('clone_ref_text') or '').strip()
            # 配置优先级：先用 preview_core_config 的 ttsModelUrl，再 fallback 到
            # voice_data 里持久化的 vllm_omni_base_url，避免历史“固定内网地址”回退。
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
            # model：从 preview_core_config 的 ttsModelId 或默认 Qwen3-TTS
            model = str((preview_core_config or {}).get('ttsModelId') or '').strip() or 'Qwen3-TTS'
            # URL 规整复用 worker 的 _vllm_omni_normalize_ws_endpoint（http→ws / 补 /v1 /
            # 幂等 endpoint 拼接）——避免 preview 复制粘贴时漏掉协议转换导致用户配 http://
            # 端点必失败（dual to vllm_omni_tts_worker 的 URL 规整）。
            from main_logic.tts_client import _vllm_omni_normalize_ws_endpoint
            ws_endpoint = _vllm_omni_normalize_ws_endpoint(base_url)
            # API key 鉴权（对齐 worker 的 _connect_and_config 双路径：WS handshake
            # Authorization header + session.config.api_key）。有认证的 vLLM 端点克隆
            # 预览也需要传 key，否则 401 失败（C6 fix）。
            vllm_api_key = str((preview_core_config or {}).get('ttsModelApiKey') or '').strip()
            ws_kwargs = {"max_size": None, "open_timeout": 10, "close_timeout": 5}
            if vllm_api_key:
                # 兼容旧版本 websockets：用 inspect 探测参数名，避免 try/except TypeError
                # 过宽吞掉 WS 通信中的合法 TypeError，也避免 await connect() + async with ws:
                # 导致 websockets 16.0 的 connect.__aenter__ 再次 await self 重连（C6-1 fix）。
                # NOTE: preview 用 inspect 探测参数名，worker 仍用 try/except TypeError。
                # 两条路径策略不同是因为 preview 是短连接（async with 自动管理），
                # worker 是长连接（手动管理）。worker 的 try/except TypeError 在
                # connect() 阶段执行，send/recv 在块外，吞没风险较低。
                try:
                    _ws_sig = inspect.signature(websockets.connect)
                    _header_key = "additional_headers" if "additional_headers" in _ws_sig.parameters else "extra_headers"
                except (ValueError, TypeError):
                    _header_key = "additional_headers"  # 新版本优先
                ws_kwargs[_header_key] = [
                    ("Authorization", f"Bearer {vllm_api_key}"),
                ]
            try:
                # 整体超时 30s：vLLM-Omni 预览无服务端超时，若服务端接受连接但不发
                # session.done（半开/挂起），async for 会永久阻塞占用 worker。open_timeout/
                # close_timeout 与 worker 的 _connect_and_config 对齐。
                async with asyncio.timeout(30):
                    async with websockets.connect(ws_endpoint, **ws_kwargs) as ws:
                        # 发送 session.config
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
                        # session 层鉴权（部分自建服务端从 config 读 api_key）
                        if vllm_api_key:
                            config_msg["api_key"] = vllm_api_key
                        await ws.send(json.dumps(config_msg))
                        # 发送预览文本
                        await ws.send(json.dumps({"type": "input.text", "text": text}))
                        await ws.send(json.dumps({"type": "input.done"}))
                        # 接收 PCM 二进制帧（24kHz/16bit/mono）和 JSON 事件
                        pcm_chunks: list[bytes] = []
                        # 流式重采样器：维护 chunk 边界滤波器状态，避免无状态 soxr.resample()
                        # 在每个分片边界重置导致杂音（与 worker 路径的 ResampleStream 一致）。
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
                # 空帧守卫：vLLM-Omni 在显式送入 response_format: pcm 时，返回的是
                # 裸 PCM 流（本身不含 44 字节 WAV 头，由下面 _build_wav_payload 补头）。
                # 若 session.done 后没收到任何 PCM 帧（服务端合成失败却没发 error 事件，
                # 或帧长 < 2 被全部跳过），_build_wav_payload([]) 只会产出一个仅含 44 字节
                # WAV 头的空文件。直接以 success:True 回前端会让用户点试听毫无声音、无任何
                # 错误提示（哑弹）。正常路径下 error 事件已能拦住多数失败，此处是额外兜底。
                if not pcm_chunks:
                    logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览未返回任何音频帧")
                    return JSONResponse({
                        'success': False,
                        'error': 'vLLM-Omni 预览未返回任何音频帧',
                        'code': 'VLLM_OMNI_VOICE_PREVIEW_NO_AUDIO',
                    }, status_code=502)
                # 封装 WAV（给裸 PCM 补 44 字节头）
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
                    # 只读 tts_default.api_key —— 跟 step_realtime_tts_worker 走的 key 对偶；
                    # 不能回退到 audio_api_key（顶上从 tts_custom / AUDIO_API_KEY 取的，都是
                    # GPT-SoVITS / CosyVoice 这种别家 provider 的 bearer，把它透给
                    # api.stepfun.com 一律 401，错误现象比明确缺 key 难排查。
                    # free_intl（海外免费 Gemini 代理）预览同 free，走 www.lanlan.app/tts
                    # 流式合成（StepFun-shape，proxy 把 voice_id 透传给 Gemini）。
                    try:
                        native_tts_config = _config_manager.get_model_api_config('tts_default')
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
                # 从 voice_data 中提取克隆时持久化的 base_url 传给 helper
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
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                }
            except ElevenLabsUpstreamError as e:
                logger.error(f"ElevenLabs 预览上游服务错误 ({e.status_code}): {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs 上游服务错误: {str(e)}',
                    'code': 'ELEVENLABS_UPSTREAM_ERROR'
                }, status_code=502)
            except ValueError as e:
                # 新增专门的客户端错误 4xx 捕获分支
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
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                }
            except MinimaxVoiceCloneError as e:
                logger.error(f"{provider_label} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'{provider_label}预览生成失败: {str(e)}'
                }, status_code=500)

        if not audio_api_key:
            return JSONResponse({'success': False, 'error': 'TTS_AUDIO_API_KEY_MISSING', 'code': 'TTS_AUDIO_API_KEY_MISSING'}, status_code=400)

        # 生成音频
        try:
            tts_api_config = _config_manager.get_model_api_config('tts_custom')
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
        clone_model = (
            (voice_data or {}).get('design_model')
            or (voice_data or {}).get('clone_model')
            or get_cosyvoice_clone_model(provider)
        )

        def _do_preview_synthesize():
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer
            # 写 module-global + 构造 SpeechSynthesizer + synthesizer.call 全程
            # 拿 DASHSCOPE_GLOBAL_LOCK：dashscope.api_key / base_*_api_url 是
            # 同进程多流程共享的写点，并发跑会互相覆盖、拿别人的 key/地域请求。
            # 这里把整个 call 都圈进锁，因为 SpeechSynthesizer.call 是同步的
            # 一次性请求，锁持续时间 ~ 几秒，不会卡 event loop（在 to_thread 里跑）。
            with DASHSCOPE_GLOBAL_LOCK:
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

            # 将音频数据转换为 Base64 字符串
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')

            return {
                "success": True,
                "audio": audio_base64,
                "mime_type": "audio/mpeg"
            }
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

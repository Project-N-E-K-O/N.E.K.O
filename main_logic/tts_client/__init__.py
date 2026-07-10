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


"""TTS helper package.

Handles TTS speech synthesis, supporting custom voices (Aliyun CosyVoice) and
default voices (each core_api's native TTS).

This package re-exports the full public surface of the legacy ``tts_client``
module so ``from main_logic.tts_client import X`` and ``tts_client.X`` attribute
access keep working unchanged. Dispatch (``get_tts_worker``), the provider
registration block, and the shared globals it looks up (``get_config_manager``,
``_get_voice_meta``, ``websockets``) deliberately live in this namespace so
``monkeypatch.setattr(tts_client, ...)`` in tests keeps hitting the names the
dispatcher actually resolves.
"""
import re
import websockets
from functools import partial

from utils.config_manager import get_config_manager
from utils.native_voice_registry import get_native_tts_worker
from utils.mimo_tts_voices import MIMO_PRESET_CATALOG
from utils import tts_provider_registry as _tts_providers
from utils.logger_config import get_module_logger

# ── shared infrastructure (re-exported for namespace stability) ──────────────
from ._infra import (
    TTS_SHUTDOWN_SENTINEL,
    _resample_audio,
    _parse_env_float,
    _enqueue_error,
    _ws_is_open,
    SentenceBuffer,
    _AudioQueueProxy,
    _non_bistream_tts_main_loop,
    _run_sentence_tts_worker,
)
from ._telemetry import _record_tts_telemetry
from ._registry_meta import TTSProviderMeta, TTS_PROVIDER_REGISTRY

# ── per-provider workers + their selection/resolution adapters ──────────────
from .workers.step import (
    step_realtime_tts_worker,
    _adjust_free_tts_url,
    _get_tts_language_code,
    _build_step_tts_create_data,
)
from .workers.grok import (
    grok_streaming_tts_worker,
    _grok_chunk_text_delta,
    _XAI_TTS_DELTA_CAP,
)
from .workers.qwen import (
    qwen_realtime_tts_worker,
    _resolve_qwen_realtime_tts_url,
    _QWEN_REALTIME_TTS_MODEL,
    _DASHSCOPE_DEFAULT_REALTIME_WS_URL,
)
from .workers.cosyvoice import (
    cosyvoice_vc_tts_worker,
    _cosyvoice_clone_is_selected,
    _cosyvoice_clone_resolve,
)
from .workers.cogtts import cogtts_tts_worker
from .workers.gemini import gemini_tts_worker
from .workers.openai import openai_tts_worker
from .workers.vllm_omni import (
    vllm_omni_tts_worker,
    VLLM_OMNI_DEFAULT_BASE_URL,
    VLLM_OMNI_DEFAULT_MODEL,
    _vllm_omni_normalize_ws_endpoint,
    _vllm_omni_is_selected,
    _vllm_omni_resolve,
    _vllm_omni_clone_is_selected,
    _vllm_omni_clone_resolve,
)
from .workers.mimo import (
    mimo_tts_worker,
    _get_mimo_chat_completions_url,
    _extract_mimo_tts_audio_bytes,
    _mimo_is_selected,
    _mimo_resolve,
)
from .workers.gptsovits import (
    gptsovits_tts_worker,
    get_custom_tts_voices,
    CustomTTSVoiceFetchError,
    _gsv_should_drop_chunk,
    _GSV_ALLOWED_PUNCT,
    _gptsovits_is_selected,
    _gptsovits_resolve,
)
from .workers.minimax import (
    minimax_tts_worker,
    _get_minimax_tts_http_url,
    _minimax_sse_synthesize,
    _minimax_clone_is_selected,
    _minimax_clone_resolve,
)
from .workers.elevenlabs import (
    elevenlabs_tts_worker,
    _resolve_elevenlabs_api_key,
    _normalize_elevenlabs_voice_id,
    _parse_elevenlabs_pcm_sample_rate,
    _is_elevenlabs_pcm_output_format,
    _get_elevenlabs_options,
    _ELEVENLABS_WS_CHUNK_SCHEDULE,
    _elevenlabs_ws_base_url,
    _elevenlabs_clone_is_selected,
    _elevenlabs_clone_resolve,
)
from .workers.local_cosyvoice import local_cosyvoice_worker
from .workers.dummy import dummy_tts_worker

logger = get_module_logger(__name__, "Main")

# Public surface re-exported for ``from main_logic.tts_client import X`` and
# ``tts_client.X`` callers (incl. tests' ``monkeypatch.setattr(tts_client, …)``).
# Kept exhaustive on purpose — this package replaced a single flat module and
# must preserve that module's full namespace.
__all__ = [
    # shared globals / dispatch (live in this namespace for monkeypatch fidelity)
    "websockets", "get_config_manager", "get_native_tts_worker", "_tts_providers",
    "get_tts_worker", "_get_voice_meta", "_grok_voice_id_is_xai_custom",
    "_XAI_CUSTOM_VOICE_PATTERN", "logger",
    # shared infrastructure
    "TTS_SHUTDOWN_SENTINEL", "_resample_audio", "_parse_env_float", "_enqueue_error",
    "_ws_is_open", "SentenceBuffer", "_AudioQueueProxy", "_non_bistream_tts_main_loop",
    "_run_sentence_tts_worker", "_record_tts_telemetry",
    "TTSProviderMeta", "TTS_PROVIDER_REGISTRY",
    # workers
    "step_realtime_tts_worker", "grok_streaming_tts_worker", "qwen_realtime_tts_worker",
    "cosyvoice_vc_tts_worker", "cogtts_tts_worker", "gemini_tts_worker",
    "openai_tts_worker", "vllm_omni_tts_worker", "mimo_tts_worker",
    "gptsovits_tts_worker", "minimax_tts_worker", "elevenlabs_tts_worker",
    "local_cosyvoice_worker", "dummy_tts_worker",
    # provider constants
    "VLLM_OMNI_DEFAULT_BASE_URL", "VLLM_OMNI_DEFAULT_MODEL",
    "_QWEN_REALTIME_TTS_MODEL", "_DASHSCOPE_DEFAULT_REALTIME_WS_URL",
    "_XAI_TTS_DELTA_CAP", "_ELEVENLABS_WS_CHUNK_SCHEDULE", "_GSV_ALLOWED_PUNCT",
    # custom-voice fetch (used by characters_router)
    "get_custom_tts_voices", "CustomTTSVoiceFetchError",
    # step helpers (used by characters_router / language-hint tests)
    "_adjust_free_tts_url", "_get_tts_language_code", "_build_step_tts_create_data",
    # per-provider helpers
    "_resolve_qwen_realtime_tts_url", "_grok_chunk_text_delta",
    "_get_mimo_chat_completions_url", "_extract_mimo_tts_audio_bytes",
    "_get_minimax_tts_http_url", "_minimax_sse_synthesize",
    "_resolve_elevenlabs_api_key", "_normalize_elevenlabs_voice_id",
    "_parse_elevenlabs_pcm_sample_rate", "_is_elevenlabs_pcm_output_format",
    "_get_elevenlabs_options", "_elevenlabs_ws_base_url", "_gsv_should_drop_chunk",
    # provider registry adapters
    "_vllm_omni_is_selected", "_vllm_omni_resolve",
    "_vllm_omni_clone_is_selected", "_vllm_omni_clone_resolve",
    "_vllm_omni_normalize_ws_endpoint",
    "_gptsovits_is_selected", "_gptsovits_resolve",
    "_minimax_clone_is_selected", "_minimax_clone_resolve",
    "_elevenlabs_clone_is_selected", "_elevenlabs_clone_resolve",
    "_cosyvoice_clone_is_selected", "_cosyvoice_clone_resolve",
    "_mimo_is_selected", "_mimo_resolve",
]


def _get_voice_meta(voice_id: str) -> dict | None:
    """Get the voice_data metadata for a voice_id (including the provider field).

    Returns the voice_data dict (containing at least ``provider``), or None if not found.
    """
    if not voice_id:
        return None
    try:
        cm = get_config_manager()
        voices = cm.get_voices_for_current_api()
        vdata = voices.get(voice_id)
        if isinstance(vdata, dict):
            return vdata
    except Exception:
        pass
    return None


_XAI_CUSTOM_VOICE_PATTERN = re.compile(r'^[a-z0-9]{8}$')


def _grok_voice_id_is_xai_custom(voice_id: str) -> bool:
    """Decide whether voice_id is genuinely an xAI custom voice rather than an
    alias-clone collision / a leftover non-xAI id.

    To short-circuit to the grok worker, voice_id must satisfy one of:
      (a) it is in the grok vocabulary (canonical id or alias) and the canonical
          hasn't been cloned in any voice storage slot — take the grok built-in
          voice path;
      (b) it is not in the vocabulary but looks like an xAI custom voice's 8-char
          lowercase alphanumeric id (the format returned by
          POST /v1/custom-voices).

    Case (a) guards against alias collisions: the user names a cloned voice after
    a grok canonical id (e.g. 'leo') and then picks an alias in the UI (e.g.
    'male', which normalizes to 'leo'). ``core._has_custom_tts()`` goes through
    ``resolve_native_voice_for_routing`` using the canonical name as the
    voice_id_exists probe; on hitting the collision it sets has_custom_voice=True
    and lands here, but ``_get_voice_meta(raw_voice_id)`` is None (the user stored
    the canonical 'leo', not the alias 'male'). Routing straight to the grok
    worker would have the worker normalize back to the 'leo' built-in voice,
    silently bypassing the user's clone. The cross-slot check is aligned with
    ``core._has_custom_tts()``: that side uses
    ``voice_id_exists_in_any_storage`` to search all API-key slots (the user may
    have cloned 'leo' in the qwen slot while the current session is grok).

    Case (b) guards against misrouting "no meta but not xAI": ``voice_meta is
    None`` can also mean a remote cosyvoice clone succeeded but the local meta
    was lost, or a historical non-xAI voice id. The old design blindly
    short-circuited to the grok worker, and xAI would reject those non-8-char
    ids. Now only the actual xAI custom format matches, and other unknown ids
    fall back to cosyvoice.
    """
    if not voice_id:
        return False
    try:
        from utils.grok_tts_voices import normalize_grok_tts_voice
    except Exception:
        # 没装 grok adapter — 保守要求 xAI custom 格式才路由
        return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))
    canonical, recognized = normalize_grok_tts_voice(voice_id)
    if recognized:
        # alias / canonical → collision check (current + 跨槽)
        if _get_voice_meta(canonical) is not None:
            return False
        try:
            if get_config_manager().voice_id_exists_in_any_storage(canonical):
                return False
        except Exception:
            pass
        return True
    # 不识别 → 必须形如 xAI custom voice id 才路由到 grok
    return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))


def get_tts_worker(core_api_type='qwen', has_custom_voice=False, voice_id=''):
    """
    Return a callable based on the core_api type and whether a custom voice exists.

    The callable's signature is (request_queue, response_queue, api_key, voice_id);
    all provider-specific parameters (e.g. base_url) are already bound via partial.
    If a provider needs an api_key replacement, the second return value is non-None.

    Returns:
        (worker_fn, api_key_override, provider_key)
        - worker_fn: TTS worker callable with the unified signature
        - api_key_override: if non-None, replaces tts_config['api_key']
        - provider_key: name of the provider actually selected (a key of
          TTS_PROVIDER_REGISTRY), for the caller to query provider metadata
          (e.g. category).
          Special values: 'free' is deliberately absent from the registry
          (overseas routes to the Gemini backend and needs the normalizer;
          meta=None → the caller falls through and enables the normalizer);
          None when native TTS is unsupported
    """
    cm = get_config_manager()
    try:
        core_cfg = cm.get_core_config() or {}
    except Exception:
        core_cfg = {}

    if core_cfg.get('DISABLE_TTS', False):
        logger.info("TTS disabled; using dummy TTS worker")
        return dummy_tts_worker, None, None

    tts_provider = str(core_cfg.get('TTS_PROVIDER') or core_cfg.get('ttsProvider') or '').strip().lower()
    assist_api_type = str(core_cfg.get('assistApi') or '').strip().lower()

    # 特异 TTS provider（用户显式配置端点的 vllm_omni / 本地 GPT-SoVITS 等）的
    # 选择与 worker 解析已收敛到 utils.tts_provider_registry，按 priority 顺序匹配：
    # GPT-SoVITS（本地显式开关）优先于 vLLM-Omni，二者都优先于克隆音色 /
    # assistApi fallback / 原生 TTS（沿用原内联顺序）。新增此类 provider 只需在
    # 本文件末尾 register 一条，不再在此处插内联特判。凭证防泄漏（vllm_omni 无 key
    # 时返回空串而非 None，避免 fallback 到别家 provider 的 key）由各 adapter 内部
    # 保证，配合 core.resolve_tts_api_key 的 provider_key == 'vllm_omni' 特判。
    # DispatchContext 统一两种选中机制（见设计文档 §3.1）：配置选中（vllm/gptsovits）
    # 与音色元数据选中（克隆系，后续增量折入）。voice_meta 此处刻意不预算——配置选中的
    # provider（vllm/gptsovits）不需要它，且必须在任何 voice_meta 查询之前短路
    # （见 test_get_tts_worker_routes_explicit_vllm_before_cloned_voice）；克隆系折入时
    # 改为按需惰性加载。
    _dispatch_ctx = _tts_providers.DispatchContext(
        core_config=core_cfg,
        cm=cm,
        voice_id=voice_id or '',
        has_custom_voice=bool(has_custom_voice),
        voice_meta_loader=lambda: _get_voice_meta(voice_id),
    )
    special = _tts_providers.resolve_selected(_dispatch_ctx)
    if special is not None:
        logger.info("[get_tts_worker] 命中 TTS provider: %s", special[2])
        return special

    # 克隆音色 provider（MiniMax / ElevenLabs / 阿里 CosyVoice）已折入
    # tts_provider_registry（priority 30/40/50，按 voice_meta.provider 选中），
    # 由上面的 resolve_selected 统一返回（含 cosyvoice_intl key 缺失 → dummy 的兜底）。
    # 这里取出 ctx 已惰性算好的 voice_meta 供下方 grok / cosyvoice fallback 块复用：
    # voice_meta=None 表示远端 clone 无本地元数据 / xAI 自定义 voice，需走 fallback。
    voice_meta = _dispatch_ctx.voice_meta

    # MiMo（assistApi=mimo / TTS_PROVIDER=mimo 选中）已折入 tts_provider_registry
    # （priority 60，clone 之后、native 之前，沿用原顺序），由上面的 resolve_selected 返回。

    # core_api_type 命中 native voice provider + 用户选了该 provider 的原生声线
    # (e.g. Gemini Puck/Leda/中文男) 时优先走原生 worker，不能被 has_custom_voice=False
    # 的 GPT-SoVITS / local CosyVoice fallthrough 拦截 —— _has_custom_tts 已经判断
    # voice_id 不是用户克隆音色，这里 has_custom_voice 必为 False，是用户显式选择的
    # 原生路径，应当尊重该选择喵。api_key 由 provider 注册的 resolver 提供
    # (Gemini 用 CORE_API_KEY；若 fallback 到 get_model_api_config('tts_default')
    # 会拿到自定义 TTS 的 key，鉴权必失败)。
    # 显式选择 MiMo 时已在上方短路，避免 Gemini/Grok 等 core-native voice
    # 覆盖 MiMo 的辅助 API TTS 路由。
    if not has_custom_voice:
        native = get_native_tts_worker(core_api_type, cm, voice_id)
        if native is not None:
            return native

    # GPT-SoVITS（is_custom + GPTSOVITS_ENABLED）已由顶部 tts_provider_registry
    # 以相同 gate 优先返回，此处不再重复判定（原 fallthrough 分支已并入注册表）。

    # 如果有自定义克隆音色，使用 CosyVoice（阿里云）
    # 必须同时有有效的 voice_id 且不是免费预设音色，否则 fallthrough 到默认 TTS
    # 注：core.py 的 _has_custom_tts 对 core_api_type=='gemini' + Gemini voice 短路返回 False，
    # 仅当 voice_id 不在用户已克隆音色列表里时才生效；同名克隆 voice (例如自己上传的 Puck)
    # 仍会保留 has_custom_voice=True 进入此分支。
    if has_custom_voice and voice_id:
        from utils.api_config_loader import get_free_voices
        if voice_id in set(get_free_voices().values()):
            logger.info("voice_id '%s' 是免费预设音色，跳过 CosyVoice，使用默认 TTS", voice_id)
        elif core_api_type == 'grok' and voice_meta is None and _grok_voice_id_is_xai_custom(voice_id):
            # grok session + voice 不是已存 clone（voice_meta=None）+ 不是 free preset
            # + 不是 alias 撞用户克隆 → 必然是 xAI 自定义 voice（8-char lowercase
            # alphanumeric，POST /v1/custom-voices 返回的 id）。走 grok worker
            # 用 xAI 端点合成，api_key 显式给 CORE_API_KEY（has_custom=True 默认
            # 从 tts_custom 槽取凭证，对 xAI 是错凭证）。voice_meta 非 None 的
            # cosyvoice clone 不进这分支，即使 core_api='grok' 也保留 cosyvoice 路径。
            # tts_custom (GPT-SoVITS / local CosyVoice) 已经在前面的 try 块里短路
            # 返回，到不了这里。`_grok_voice_id_is_xai_custom` 还会拦下 alias
            # collision：用户克隆了 canonical voice 'leo' 但输入 alias 'male' 时，
            # core._has_custom_tts 会因 collision 把 has_custom_voice 置 True，
            # 这里要识别出来转走 cosyvoice，否则 grok worker 会把 alias normalize
            # 回内置 voice，悄悄绕过用户的克隆。
            grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
            return grok_streaming_tts_worker, grok_api_key, 'grok'
        else:
            return cosyvoice_vc_tts_worker, None, 'cosyvoice'

    # 没有自定义音色时，使用与 core_api 匹配的默认 TTS
    if core_api_type in ('qwen', 'qwen_intl'):
        return qwen_realtime_tts_worker, None, 'qwen'
    if core_api_type == 'free':
        # provider_key 故意用 'free' 而非 'step'：'free' 不在 TTS_PROVIDER_REGISTRY 中，
        # 使调用方 meta=None → normalizer 启用，因为 free 国外模式走 Gemini 后端需要
        # CJK 空格清理。若改为 'step'（ws_bistream）则国外 free 用户的 normalizer 会被错误禁用。
        return partial(step_realtime_tts_worker, free_mode=True), None, 'free'
    elif core_api_type == 'step':
        return step_realtime_tts_worker, None, 'step'
    elif core_api_type == 'glm':
        return cogtts_tts_worker, None, 'cogtts'
    elif core_api_type == 'gemini':
        return gemini_tts_worker, None, 'gemini'
    elif core_api_type == 'openai':
        return openai_tts_worker, None, 'openai'
    elif core_api_type == 'grok':
        # default 段 fallthrough（has_custom=True + free preset voice 时也会到这里）
        # 也必须显式给 CORE_API_KEY override —— has_custom=True 时 _start_tts_thread
        # 默认从 tts_custom 槽取凭证，对 xAI 是错的鉴权 key（往往是 cosyvoice 或
        # qwen 的 ASSIST key）。与 _resolve_grok_native_tts_worker / cosyvoice 上面
        # 的 grok short-circuit 同源。
        grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
        return grok_streaming_tts_worker, grok_api_key, 'grok'
    else:
        logger.error(f"{core_api_type}不支持原生TTS，请使用自定义语音")
        return dummy_tts_worker, None, None


# ─── 特异 TTS provider 注册（与 utils.tts_provider_registry 对偶）────────────
#
# 在所有 worker 定义之后注册，避免元数据模块过早拉入 soxr/websockets 等重依赖
# （与 native_voice_registry 的两层注册同源）。各 adapter 精确复刻
# get_tts_worker 当前对应分支的读取语义，阶段 1 仅注册、不改 dispatch，保证
# 零行为变化；阶段 2 再把 get_tts_worker 的内联特判换成 resolve_selected。
#
# vllm_omni：从原始 core_config.json 读 ttsModelProvider / ttsModelUrl /
#   ttsModelId / ttsVoiceId / ttsModelApiKey。必须读原始 json 而非 snapshot —
#   ttsModelApiKey 不进 snapshot（见 config_manager.py 的凭证字段说明），且
#   get_tts_worker 历史上整段都走 load_json_config，复刻以保字段一致。
# gptsovits：走 tts_custom 槽位的 is_custom + GPTSOVITS_ENABLED 开关，与
#   get_tts_worker 顶部的早返回分支同源；优先级高于 vllm_omni（沿用原顺序）。


_tts_providers.register(_tts_providers.TTSProvider(
    key='gptsovits',
    kind='local',
    priority=10,
    capabilities=frozenset({'clone'}),  # GPT-SoVITS = 参考音频克隆
    is_selected=_gptsovits_is_selected,
    resolve=_gptsovits_resolve,
    probe_kind='local_http',
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='vllm_omni',
    kind='local',
    priority=20,
    # vLLM-Omni = 选预制音色 id（preset）+ 内联参考音频克隆（clone）。两种选中机制
    # 合并在 _vllm_omni_is_selected/_vllm_omni_resolve 里分流（对偶 MiMo 的单条目双机制）。
    capabilities=frozenset({'preset', 'clone'}),
    is_selected=_vllm_omni_is_selected,
    resolve=_vllm_omni_resolve,
    default_url=VLLM_OMNI_DEFAULT_BASE_URL,
    default_model=VLLM_OMNI_DEFAULT_MODEL,
    default_voice='default',
    editable_endpoint=True,
    probe_kind='ws_handshake',
    probe_sub_type='vllm_omni_tts',
    probe_ws_path='/audio/speech/stream',
))

# 克隆音色 provider（hosted SaaS，按 voice_meta.provider 选中）。priority 30/40/50
# 沿用原 get_tts_worker 克隆块顺序：都在 vllm(20) 之后、mimo/native 之前。capabilities
# 暂记 {clone}（当前仅克隆路由经注册表；preset/design 待 wiring 后按实际能力追加）。
# tts_dropdown_only=False：这几家不靠下拉选中（靠 voice_meta），且 minimax 本身还是
# LLM provider，绝不能被前端从对话/总结等 LLM 下拉里隐藏。它们在 ui_metadata 里的存在
# 是给前端 source-first 选声器读 capabilities 用的，不参与下拉过滤。
_tts_providers.register(_tts_providers.TTSProvider(
    key='minimax',
    kind='hosted',
    priority=30,
    capabilities=frozenset({'clone', 'design'}),
    aliases=frozenset({'minimax_intl'}),
    is_selected=_minimax_clone_is_selected,
    resolve=_minimax_clone_resolve,
    tts_dropdown_only=False,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='elevenlabs',
    kind='hosted',
    priority=40,
    # clone（上传样本）+ design（文字描述生成）。design 经 create-from-preview 落成一个
    # 普通的 ElevenLabs voice_id（voice_meta.source='design'），dispatch 与 clone 同路
    # （_elevenlabs_clone_is_selected 按 provider=='elevenlabs' 选中），无需独立 worker。
    capabilities=frozenset({'clone', 'design'}),
    is_selected=_elevenlabs_clone_is_selected,
    resolve=_elevenlabs_clone_resolve,
    tts_dropdown_only=False,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='cosyvoice',
    kind='hosted',
    priority=50,
    capabilities=frozenset({'clone', 'design'}),
    aliases=frozenset({'cosyvoice_intl'}),
    is_selected=_cosyvoice_clone_is_selected,
    resolve=_cosyvoice_clone_resolve,
    tts_dropdown_only=False,
))

# MiMo：priority 60（clone 之后、native 之前，沿用原 get_tts_worker 顺序）。
# capabilities {preset, clone}：
#   - preset：固定音色目录由 preset_catalog 提供（MIMO_PRESET_CATALOG，数据复用
#     utils.mimo_tts_voices 的固定音色表）——MiMo 预制音色的单一真相，UI /voices 与
#     validate_voice_id 都查注册表，不再借道 native_voice_registry（MiMo 是 hosted SaaS，
#     不是核心自带，见设计文档 §4）。
#   - clone：voiceclone enrollment（characters_router /voice_clone 的 mimo 分支，对偶
#     cosyvoice/minimax）。MiMo 克隆没有远端 voice_id——参考音频本地保存、每次合成内联，
#     dispatch 由 _mimo_resolve 按 voice_meta.provider=='mimo' 选中并读出样本（见 §4/§7）。
# 同一 provider 条目承载两种选中机制（config-selected preset / voice_meta-selected clone），
# 见 workers/mimo.py 的 _mimo_is_selected/_mimo_resolve。
# tts_dropdown_only=False：MiMo 本身是 assist LLM provider，不能被前端从 LLM 下拉隐藏。
_tts_providers.register(_tts_providers.TTSProvider(
    key='mimo',
    kind='hosted',
    priority=60,
    capabilities=frozenset({'preset', 'clone', 'design'}),
    is_selected=_mimo_is_selected,
    resolve=_mimo_resolve,
    preset_catalog=MIMO_PRESET_CATALOG,
    tts_dropdown_only=False,
))

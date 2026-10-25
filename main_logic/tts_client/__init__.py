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

from utils.config_manager import get_config_manager
from utils.tts.native_voice_registry import get_native_tts_worker
from utils.tts.providers.mimo import MIMO_PRESET_CATALOG
from utils.tts import provider_registry as _tts_providers
from utils.logger_config import get_module_logger

# ── shared infrastructure (re-exported for namespace stability) ──────────────
from ._infra import (
    TTS_SHUTDOWN_SENTINEL,
    TTS_AUDIO_DONE_SENTINEL,
    TTS_SOFT_FLUSH_SENTINEL,
    AudioDoneEmitter,
    _resample_audio,
    _parse_env_float,
    _enqueue_error,
    _ws_is_open,
    log_configured_tts_failure,
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
)
from .workers.free import (
    free_realtime_tts_worker,
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
from .workers.cogtts import cogtts_tts_worker, _glm_clone_is_selected, _glm_clone_resolve
from .workers.gemini import gemini_tts_worker
from .workers.openai import (
    openai_tts_worker,
    _custom_openai_tts_is_selected,
    _custom_openai_tts_resolve,
)
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
from .workers.doubao import (
    doubao_tts_worker,
    _doubao_is_selected,
    _doubao_resolve,
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
    "TTS_SHUTDOWN_SENTINEL", "TTS_AUDIO_DONE_SENTINEL", "TTS_SOFT_FLUSH_SENTINEL", "AudioDoneEmitter",
    "_resample_audio", "_parse_env_float", "_enqueue_error",
    "_ws_is_open", "SentenceBuffer", "_AudioQueueProxy", "_non_bistream_tts_main_loop",
    "_run_sentence_tts_worker", "_record_tts_telemetry",
    "TTSProviderMeta", "TTS_PROVIDER_REGISTRY",
    # workers
    "step_realtime_tts_worker", "free_realtime_tts_worker",
    "grok_streaming_tts_worker", "qwen_realtime_tts_worker",
    "cosyvoice_vc_tts_worker", "cogtts_tts_worker", "gemini_tts_worker",
    "openai_tts_worker", "vllm_omni_tts_worker", "mimo_tts_worker",
    "doubao_tts_worker",
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
    "_custom_openai_tts_is_selected", "_custom_openai_tts_resolve",
    "tts_provider_falls_back_on_failure", "tts_provider_uses_configured_preset_voice",
    "selected_configured_tts_preset_provider_key",
    "_minimax_clone_is_selected", "_minimax_clone_resolve",
    "_elevenlabs_clone_is_selected", "_elevenlabs_clone_resolve",
    "_cosyvoice_clone_is_selected", "_cosyvoice_clone_resolve",
    "_mimo_is_selected", "_mimo_resolve",
    "_doubao_is_selected", "_doubao_resolve",
    "_glm_clone_is_selected", "_glm_clone_resolve",
]


def _get_voice_meta(voice_id: str) -> dict | None:
    """Get the voice_data metadata for a voice_id (including the provider field).

    Returns the voice_data dict (containing at least ``provider``), or None if not found.
    """
    if not voice_id:
        return None
    from .remote_voice import active_imported_voice
    imported = active_imported_voice.get()
    if imported and imported.get('remote_voice_id') == voice_id:
        return imported
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
        from utils.tts.providers.grok import normalize_grok_tts_voice
    except Exception:
        # No grok adapter installed: conservatively require xAI custom format before routing.
        return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))
    canonical, recognized = normalize_grok_tts_voice(voice_id)
    if recognized:
        # alias / canonical → collision check (current + cross-slot).
        if _get_voice_meta(canonical) is not None:
            return False
        try:
            if get_config_manager().voice_id_exists_in_any_storage(canonical):
                return False
        except Exception:
            pass
        return True
    # Unrecognized → route to grok only for xAI custom voice id format.
    return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))


def get_tts_worker(
    core_api_type='qwen',
    has_custom_voice=False,
    voice_id='',
    *,
    excluded_provider_keys=frozenset(),
):
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
    from utils.config_manager.imported_voices import is_imported_voice_ref
    from .remote_voice import bind_imported_voice_worker, unavailable_imported_voice_worker

    try:
        core_cfg = cm.get_core_config() or {}
    except Exception:
        core_cfg = {}

    if core_cfg.get('DISABLE_TTS', False):
        logger.info("TTS disabled; using dummy TTS worker")
        return dummy_tts_worker, None, None

    imported_voice = None
    dispatch_manager = cm
    if is_imported_voice_ref(voice_id):
        try:
            imported_voice = cm.get_imported_voice(voice_id)
            if imported_voice:
                from utils.voice_management.providers import get_adapter
                from utils.voice_management.runtime_snapshot import VoiceRuntimeSnapshot
                imported_runtime = get_adapter(imported_voice['provider']).resolve_runtime(cm, voice_data=imported_voice)
                if imported_runtime.scope_id != imported_voice.get('scope_id') or not imported_runtime.api_key:
                    imported_voice = None
                else:
                    dispatch_manager = VoiceRuntimeSnapshot(cm, imported_runtime)
        except Exception:
            imported_voice = None
        if not imported_voice:
            legacy_metadata = _get_voice_meta(voice_id)
            if not legacy_metadata or legacy_metadata.get('origin') == 'import':
                return unavailable_imported_voice_worker, '', None
    tts_provider = str(core_cfg.get('TTS_PROVIDER') or core_cfg.get('ttsProvider') or '').strip().lower()
    assist_api_type = str(core_cfg.get('assistApi') or '').strip().lower()

    # Selection and worker resolution for specific TTS providers (explicit-endpoint vllm_omni / local GPT-SoVITS, etc.)
    # are centralized in utils.tts.provider_registry, matching in priority order:
    # GPT-SoVITS (explicit local enablement) precedes vLLM-Omni; both precede cloned voices /
    # assistApi fallback / native TTS (preserving former inline order). Add such providers by
    # registering an entry at this file's end, without inline special cases here. Each adapter prevents credential leakage
    # (vllm_omni returns empty string, not None, without a key, avoiding fallback to another provider's key),
    # paired with core.resolve_tts_api_key's provider_key == 'vllm_omni' special case.
    # DispatchContext unifies two selection mechanisms (design §3.1): configuration (vllm/gptsovits)
    # and voice metadata (clones, integrated incrementally). Intentionally do not precompute voice_meta:
    # configuration-selected providers (vllm/gptsovits) do not need it and must short-circuit before any voice_meta lookup
    # (see test_get_tts_worker_routes_explicit_vllm_before_cloned_voice); when clones integrate,
    # switch to lazy on-demand loading.
    _dispatch_ctx = _tts_providers.DispatchContext(
        core_config=core_cfg,
        cm=dispatch_manager,
        voice_id=(imported_voice['remote_voice_id'] if imported_voice else voice_id) or '',
        has_custom_voice=bool(has_custom_voice),
        voice_meta_loader=lambda: imported_voice or _get_voice_meta(voice_id),
    )
    # Runtime fallback passes only the failed provider key here; all remaining
    # providers keep their established priority and selection behavior.
    # Runtime fallback excludes only failing providers, preserving all remaining priorities.
    special = _tts_providers.resolve_selected(
        _dispatch_ctx,
        excluded_provider_keys=frozenset(excluded_provider_keys or ()),
    )
    if special is not None:
        logger.info("[get_tts_worker] 命中 TTS provider: %s", special[2])
        if imported_voice:
            return (
                bind_imported_voice_worker(special[0], voice_id, imported_voice),
                special[1], special[2],
            )
        return special

    if imported_voice:
        return unavailable_imported_voice_worker, '', None

    # Clone providers (MiniMax / ElevenLabs / Alibaba CosyVoice) are integrated into
    # tts_provider_registry (priority 30/40/50, selected by voice_meta.provider),
    # returned centrally by resolve_selected above (including missing cosyvoice_intl key → dummy fallback).
    # Reuse ctx's lazily computed voice_meta below for grok / cosyvoice fallback:
    # voice_meta=None means remote clone without local metadata / xAI custom voice, requiring fallback.
    voice_meta = _dispatch_ctx.voice_meta

    # MiMo (selected by assistApi=mimo / TTS_PROVIDER=mimo) is integrated into tts_provider_registry
    # (priority 60, after clones and before native, preserving original order), returned by resolve_selected above.

    # When core_api_type matches a native voice provider and the user chose that provider's native voice
    # (e.g. Gemini Puck/Leda/中文男), prefer native worker; GPT-SoVITS / local CosyVoice fallthrough with has_custom_voice=False
    # must not intercept it. _has_custom_tts already established
    # voice_id is not a user clone, making has_custom_voice False here; respect this explicitly chosen
    # native path, meow. api_key comes from the provider-registered resolver
    # (Gemini uses CORE_API_KEY; falling back to get_model_api_config('tts_default')
    # would obtain a custom-TTS key and necessarily fail authentication).
    # Explicit MiMo already short-circuited above, preventing Gemini/Grok core-native voice
    # from overriding MiMo's assist-API TTS routing.
    if not has_custom_voice:
        native = get_native_tts_worker(core_api_type, cm, voice_id)
        if native is not None:
            return native

    # GPT-SoVITS (is_custom + GPTSOVITS_ENABLED) already returns first with the same gate via
    # tts_provider_registry above; do not duplicate checks (former fallthrough branch merged into registry).

    # Use CosyVoice (Alibaba Cloud) for custom cloned voices.
    # Require a valid voice_id that is not a free preset; otherwise fall through to default TTS.
    # Note: core.py's _has_custom_tts short-circuits False for core_api_type=='gemini' + Gemini voice
    # only if voice_id is absent from the user's cloned-voice list; same-name clones (e.g. user-uploaded Puck)
    # still retain has_custom_voice=True and enter this branch.
    if has_custom_voice and voice_id:
        from utils.api_config_loader import get_free_voices
        if voice_id in set(get_free_voices().values()):
            logger.info("voice_id '%s' 是免费预设音色，跳过 CosyVoice，使用默认 TTS", voice_id)
        elif core_api_type == 'grok' and voice_meta is None and _grok_voice_id_is_xai_custom(voice_id):
            # grok session + voice is not a stored clone (voice_meta=None) + not a free preset
            # + not an alias colliding with a user clone → necessarily an xAI custom voice (8-char lowercase
            # alphanumeric id returned by POST /v1/custom-voices). Use grok worker
            # for xAI-endpoint synthesis and explicitly pass CORE_API_KEY (has_custom=True normally
            # selects tts_custom credentials, incorrect for xAI). CosyVoice clones with non-None voice_meta
            # do not enter this branch; keep their cosyvoice path even with core_api='grok'.
            # tts_custom (GPT-SoVITS / local CosyVoice) already short-circuited in the preceding try block
            # and returned, so cannot reach here. `_grok_voice_id_is_xai_custom` also blocks alias
            # collisions: if a user cloned canonical voice 'leo' but enters alias 'male',
            # core._has_custom_tts sets has_custom_voice True due to collision;
            # detect that here and route to cosyvoice, or grok worker would normalize the alias
            # to a built-in voice, silently bypassing the user's clone.
            grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
            return grok_streaming_tts_worker, grok_api_key, 'grok'
        else:
            return cosyvoice_vc_tts_worker, None, 'cosyvoice'

    # Without custom voice, use default TTS matching core_api.
    if core_api_type in ('qwen', 'qwen_intl'):
        qwen_api_key_override = None
        if excluded_provider_keys:
            qwen_key_field = (
                'ASSIST_API_KEY_QWEN_INTL'
                if core_api_type == 'qwen_intl'
                else 'ASSIST_API_KEY_QWEN'
            )
            # A supervised fallback must not inherit the failed configured
            # endpoint's TTS_MODEL_API_KEY through the tts_default slot.
            qwen_api_key_override = str(core_cfg.get(qwen_key_field) or '').strip()
        return qwen_realtime_tts_worker, qwen_api_key_override, 'qwen'
    if core_api_type == 'free':
        # Free service has its own worker; internally it still shares the streaming
        # wire transport with StepFun, but provider routing, endpoint, and voice selection no longer masquerade as StepFun.
        return free_realtime_tts_worker, None, 'free'
    elif core_api_type == 'step':
        return step_realtime_tts_worker, None, 'step'
    elif core_api_type == 'glm':
        return cogtts_tts_worker, None, 'cogtts'
    elif core_api_type == 'gemini':
        return gemini_tts_worker, None, 'gemini'
    elif core_api_type == 'openai':
        return openai_tts_worker, None, 'openai'
    elif core_api_type == 'grok':
        # Default-section fallthrough (also reached with has_custom=True + free preset voice)
        # must explicitly override with CORE_API_KEY too: with has_custom=True, _start_tts_thread
        # defaults to tts_custom credentials, incorrect for xAI (often cosyvoice or
        # qwen ASSIST key). Same basis as _resolve_grok_native_tts_worker / cosyvoice's earlier
        # grok short circuit.
        grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
        return grok_streaming_tts_worker, grok_api_key, 'grok'
    else:
        logger.error(f"{core_api_type}不支持原生TTS，请使用自定义语音")
        return dummy_tts_worker, None, None


# ─── Specific TTS provider registration (symmetric with utils.tts.provider_registry) ────────────
#
# Register after all worker definitions, avoiding early soxr/websockets heavy imports through metadata modules
# (like native_voice_registry's two-level registration). Each adapter precisely replicates
# its current get_tts_worker branch's read semantics; phase 1 only registers, without dispatch changes,
# ensuring unchanged behavior; phase 2 replaces inline get_tts_worker cases with resolve_selected.
#
# vllm_omni: read ttsModelProvider / ttsModelUrl /
#   ttsModelId / ttsVoiceId / ttsModelApiKey from raw core_config.json, rather than snapshot:
#   ttsModelApiKey is excluded from snapshot (see config_manager.py credential-field notes), and
#   get_tts_worker historically used load_json_config throughout; replicate to keep fields consistent.
# gptsovits: use tts_custom slot's is_custom + GPTSOVITS_ENABLED switch, matching
#   get_tts_worker's early-return branch; precede vllm_omni, preserving original order.


_tts_providers.register(_tts_providers.TTSProvider(
    key='gptsovits',
    kind='local',
    priority=10,
    capabilities=frozenset({'clone'}),  # GPT-SoVITS = reference-audio cloning.
    is_selected=_gptsovits_is_selected,
    resolve=_gptsovits_resolve,
    probe_kind='local_http',
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='vllm_omni',
    kind='local',
    priority=20,
    # vLLM-Omni = preset voice id selection (preset) + inline reference-audio cloning (clone). Both selection mechanisms
    # share _vllm_omni_is_selected/_vllm_omni_resolve for dispatch (symmetric with MiMo's single-entry dual mechanisms).
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
    configured_preset_voice=True,
    fallback_on_failure=True,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='custom',
    kind='hosted',
    priority=25,
    capabilities=frozenset({'preset'}),
    is_selected=_custom_openai_tts_is_selected,
    resolve=_custom_openai_tts_resolve,
    editable_endpoint=True,
    probe_kind='http_tts',
    probe_sub_type='openai_tts',
    configured_preset_voice=True,
    fallback_on_failure=True,
    # The generic "custom" option is already inserted into every model dropdown.
    # Keep this registry entry available as metadata without adding a duplicate.
    tts_config_visible=False,
))


def tts_provider_falls_back_on_failure(provider_key):
    """Expose registry fallback metadata without importing utils in core mixins."""
    # The facade keeps provider policy out of core; core only asks whether the
    # active worker opted into replacement after failure.
    # Isolate provider policy behind a facade; core only asks whether the current worker allows failover.
    return _tts_providers.falls_back_on_failure(provider_key)


def tts_provider_uses_configured_preset_voice(provider_key):
    """Expose configured-preset ownership without importing utils in core mixins."""
    return _tts_providers.uses_configured_preset_voice(provider_key)


def selected_configured_tts_preset_provider_key(core_config, cm, voice_id):
    """Return the selected configured-preset owner for ``voice_id``, if any."""
    # Reuse registry dispatch so core never hardcodes custom/vLLM ownership.
    # Reuse registry checks so core need not recognize concrete providers such as custom or vLLM.
    try:
        provider_key = _tts_providers.selected_preset_provider_key(
            core_config,
            cm,
            voice_id,
        )
        if not _tts_providers.uses_configured_preset_voice(provider_key):
            return None
        return provider_key
    except Exception:
        # Ownership lookup is advisory. A malformed provider must not abort the
        # session, and its exception may contain credentials or signed URLs.
        # On voice-ownership lookup failure, continue legacy routing without logging raw exceptions that may contain credentials.
        log_configured_tts_failure("configured", "ownership")
        return None


# Cloned-voice providers (hosted SaaS, selected by voice_meta.provider). Priority 30/40/50
# preserves original get_tts_worker clone order: after vllm(20), before mimo/native. capabilities
# also declares supported clone/design sources; both route to the same worker by voice_meta after saving.
# tts_dropdown_only=False: these providers use voice_meta rather than dropdown selection; minimax itself is also
# an LLM provider and must never be hidden from conversation/summary LLM dropdowns. Their ui_metadata entries
# let the frontend source-first voice picker read capabilities without participating in dropdown filtering.
_tts_providers.register(_tts_providers.TTSProvider(
    key='minimax',
    kind='hosted',
    priority=30,
    capabilities=frozenset({'clone', 'design'}),
    aliases=frozenset({'minimax_intl'}),
    # MiniMax documents a 500-character maximum for preview_text, not prompt.
    # NEKO supplies a short fixed preview template, so the user description has
    # no documented hard limit to enforce here.
    voice_design=_tts_providers.VoiceDesignMetadata(),
    is_selected=_minimax_clone_is_selected,
    resolve=_minimax_clone_resolve,
    tts_dropdown_only=False,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='elevenlabs',
    kind='hosted',
    priority=40,
    # clone (uploaded samples) + design (text-description generation). create-from-preview saves design as
    # an ordinary ElevenLabs voice_id (voice_meta.source='design'), sharing clone dispatch
    # (_elevenlabs_clone_is_selected matches provider=='elevenlabs'), without a separate worker.
    capabilities=frozenset({'clone', 'design'}),
    # Both ElevenLabs design and create-from-preview reject descriptions outside
    # this documented 20-1000 character window.
    voice_design=_tts_providers.VoiceDesignMetadata(prompt_min=20, prompt_max=1000),
    is_selected=_elevenlabs_clone_is_selected,
    resolve=_elevenlabs_clone_resolve,
    tts_dropdown_only=False,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='cosyvoice',
    kind='hosted',
    priority=50,
    capabilities=frozenset({'clone', 'design'}),
    # DashScope voice-enrollment enforces all four constraints upstream: prompt
    # <=500 characters, alphanumeric prefix <=10, and zh/en language hints only.
    voice_design=_tts_providers.VoiceDesignMetadata(
        prompt_max=500,
        prefix_max=10,
        prefix_pattern=r'^[A-Za-z0-9]+$',
        language_hints=('ch', 'en'),
    ),
    is_selected=_cosyvoice_clone_is_selected,
    resolve=_cosyvoice_clone_resolve,
    tts_dropdown_only=False,
))

# MiMo: priority 60 (after clones, before native, preserving original get_tts_worker order).
# capabilities {preset, clone, design}：
#   - preset: preset_catalog supplies the fixed catalog (MIMO_PRESET_CATALOG, reusing
#     utils.tts.providers.mimo's fixed voice table), the sole source of truth. UI /voices and
#     validate_voice_id both query the registry rather than native_voice_registry (MiMo is hosted SaaS,
#     not core-native; see design §4).
#   - clone: voiceclone enrollment (characters_router /voice_clone's mimo branch, symmetric with
#     cosyvoice/minimax). MiMo clones have no remote voice_id; reference audio is saved locally and inlined for each synthesis,
#     with _mimo_resolve selecting voice_meta.provider=='mimo' and reading samples (see §4/§7).
#   - design: save text descriptions for voicedesign synthesis reuse, also selected via voice_meta.
# One provider entry supports both config-selected presets and voice_meta-selected custom voices;
# see workers/mimo.py's _mimo_is_selected/_mimo_resolve.
# tts_dropdown_only=False: MiMo itself is an assist LLM provider; frontend must not hide it from LLM dropdowns.
_tts_providers.register(_tts_providers.TTSProvider(
    key='mimo',
    kind='hosted',
    priority=60,
    capabilities=frozenset({'preset', 'clone', 'design'}),
    # MiMo recommends a concise 1-4 sentence description but does not document a
    # request limit. A quality recommendation must not become a hard validator.
    voice_design=_tts_providers.VoiceDesignMetadata(),
    is_selected=_mimo_is_selected,
    resolve=_mimo_resolve,
    preset_catalog=MIMO_PRESET_CATALOG,
    tts_dropdown_only=False,
))

_tts_providers.register(_tts_providers.TTSProvider(
    key='doubao_tts',
    kind='hosted',
    priority=65,
    capabilities=frozenset({'clone'}),
    is_selected=_doubao_is_selected,
    resolve=_doubao_resolve,
    default_url='https://openspeech.bytedance.com',
    default_model='seed-icl-2.0',
    default_voice='',
    editable_endpoint=True,
    probe_kind='http_tts',
    probe_sub_type='doubao_tts',
    tts_dropdown_only=True,
    tts_config_visible=False,
))

# GLM cloned voices (selected by voice_meta.provider=='glm_tts', priority 66 immediately after doubao).
# Unlike doubao, synthesis reuses cogtts_tts_worker (/paas/v4/audio/speech's voice parameter
# officially supports clones, sharing the SSE streaming + watermark detection path). Resolve key from assistApiKeyGlm
# (fall back to coreApiKey for core/assist=glm), without a separate worker. is_selected recognizes only
# voice_meta, not config; core_api_type=='glm' native routing still uses get_tts_worker's
# core branch, avoiding interception of native GLM users without clones. tts_dropdown_only=True /
# tts_config_visible=False: GLM is already a core/assist LLM provider, excluded from TTS dropdown.
_tts_providers.register(_tts_providers.TTSProvider(
    key='glm_tts',
    kind='hosted',
    priority=66,
    capabilities=frozenset({'clone'}),
    is_selected=_glm_clone_is_selected,
    resolve=_glm_clone_resolve,
    default_url='https://open.bigmodel.cn/api/paas/v4',
    default_model='glm-tts-clone',
    default_voice='',
    tts_dropdown_only=True,
    tts_config_visible=False,
))

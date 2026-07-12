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

"""Voice storage mixin.

voice_storage.json access, per-provider storage-key resolution, voice_id
validation/normalization and the invalid voice_id cleanup pass.
"""
import asyncio
from copy import deepcopy

from config import DEFAULT_CONFIG_DATA
from utils.doubao_tts import DOUBAO_VOICE_STORAGE_KEY
from utils.native_voice_registry import (
    is_free_lanlan_app_route,
    is_saveable_native_voice,
)
from utils.voice_config import read_legacy_voice_id

from ._shared import _as_bool, logger
from .persona_payload import _DEPRECATED_FREE_YUI_VOICE_IDS
from .reserved_schema import get_reserved, set_reserved


class VoiceStorageMixin:
    """Voice storage buckets, validation and cleanup."""

    # --- Voice storage helpers ---

    def load_voice_storage(self):
        """Load the voice config storage"""
        try:
            return self.load_json_config('voice_storage.json', default_value=deepcopy(DEFAULT_CONFIG_DATA['voice_storage.json']))
        except Exception as e:
            logger.error("加载音色配置失败: %s", e)
            return {}

    def save_voice_storage(self, data):
        """Save the voice config storage"""
        try:
            self.save_json_config('voice_storage.json', data)
        except Exception as e:
            logger.error("保存音色配置失败: %s", e)
            raise

    @staticmethod
    def is_legacy_cosyvoice_id(voice_id: str) -> bool:
        """CosyVoice v2 / v3 cloned voice IDs became invalid with the CosyVoice 3.5 upgrade."""
        return bool(voice_id) and (
            voice_id.startswith("cosyvoice-v2") or voice_id.startswith("cosyvoice-v3-")
        )

    @staticmethod
    def is_deprecated_free_yui_voice_id(voice_id) -> bool:
        """Whether voice_id is the replaced free YUI preset voice still lingering in existing saves."""
        return bool(voice_id) and str(voice_id).strip() in _DEPRECATED_FREE_YUI_VOICE_IDS

    def remap_deprecated_free_yui_voice_id(self, voice_id):
        """Deprecated free YUI preset voice → active CN yui_cn (CN free route migration only).

        Non-deprecated values are returned as-is (no strip normalization), so callers don't
        mistake a mere leading/trailing whitespace difference for "already migrated" and
        continue, skipping this round's cleanup of invalid voice_ids.

        The deprecated value is the CN StepFun YUI tone; only the CN free (lanlan.tech)
        route actually serves it, and only that route migrates to the active
        free_voices["yui_cn"]:
          - overseas free (lanlan.app → free_intl): returned as-is; the existing validate
            marks it invalid on the overseas route and clears it → server-side default
            voice fallback. The client does not inject the "yui"/native alias (PR #1643
            design principle: free_intl inherits the Gemini-native provider and must not
            leak StepFun magic ids or their aliases into that catalog; besides,
            unconditionally swapping to the CN voice tone would land a non-empty voice_id
            on free_intl into external TTS).
          - non-free routes: returned as-is; the deprecated StepFun preset is unusable
            there, left to the clear-and-fallback path.
        Also returned as-is when the active yui_cn is missing/empty/itself in the
        deprecated set — never borrow cuteGirl or other presets as stand-ins to morph YUI
        into a different voice, and never replace one deprecated value with another,
        creating an endless loop.
        """
        if not self.is_deprecated_free_yui_voice_id(voice_id):
            return voice_id
        core_cfg = self.get_core_config() or {}
        if (core_cfg.get("CORE_API_TYPE") or core_cfg.get("coreApi")) != "free":
            return voice_id

        # get_core_config() 已按非大陆把 CORE_URL 改写成 lanlan.app，URL 即可判海外；
        # _check_non_mainland 兜底地理判定。与 ensure_default 同源。海外不迁移（见上）。
        core_url = str(core_cfg.get("CORE_URL") or "")
        overseas = is_free_lanlan_app_route("free", core_url)
        if not overseas:
            try:
                overseas = bool(self._check_non_mainland())
            except Exception:
                overseas = False
        if overseas:
            return voice_id

        from utils.api_config_loader import get_free_voices
        current = str((get_free_voices() or {}).get("yui_cn") or "").strip()
        if current and current not in _DEPRECATED_FREE_YUI_VOICE_IDS:
            return current
        return voice_id

    def get_tts_api_key(self, provider: str) -> str | None:
        """Return the configured TTS API key for a provider, or None.

        - cosyvoice: api_key from the tts_custom model config
        - cosyvoice_intl: API key resolved by the CosyVoice intl runtime
        - minimax:   ASSIST_API_KEY_MINIMAX → MINIMAX_API_KEY fallback
        - minimax_intl: ASSIST_API_KEY_MINIMAX_INTL → MINIMAX_INTL_API_KEY fallback
        - mimo: ASSIST_API_KEY_MIMO
        - doubao_tts: ttsModelApiKey only when the active TTS provider is doubao_tts,
          then the dedicated Doubao Speech keybook entry
        """
        if provider == 'cosyvoice':
            core_config = self.get_core_config()
            if self._is_vllm_omni_tts_selected(core_config):
                return None
            tts_config = self.get_model_api_config('tts_custom')
            key = (tts_config.get('api_key') or '').strip()
            return key or None
        if provider == 'cosyvoice_intl':
            key = (self.get_cosyvoice_clone_runtime(provider).get('api_key') or '').strip()
            return key or None
        if provider in ('minimax', 'minimax_intl'):
            core_config = self.get_core_config()
            if provider == 'minimax_intl':
                key = (core_config.get('ASSIST_API_KEY_MINIMAX_INTL') or '').strip()
            else:
                key = (core_config.get('ASSIST_API_KEY_MINIMAX') or '').strip()
            if not key:
                try:
                    import utils.minimax_api_keys as _mm_keys
                    fallback = getattr(_mm_keys, 'MINIMAX_INTL_API_KEY', None) if provider == 'minimax_intl' else getattr(_mm_keys, 'MINIMAX_API_KEY', None)
                    key = (fallback or '').strip()
                except ImportError:
                    logger.debug("utils.minimax_api_keys not found, no fallback MiniMax keys available")
            return key or None
        if provider == 'elevenlabs':
            core_config = self.get_core_config()
            key = (core_config.get('ASSIST_API_KEY_ELEVENLABS') or '').strip()
            if not key:
                key = (core_config.get('ELEVENLABS_API_KEY') or '').strip()
            if '***' in key:
                return None
            return key or None
        if provider == 'mimo':
            core_config = self.get_core_config()
            use_token_plan = (
                (core_config.get('assistApi') or '').strip() == 'mimo'
                and _as_bool(core_config.get('useMimoTokenPlan', False))
            )
            key_field = 'ASSIST_API_KEY_MIMO_TOKEN_PLAN' if use_token_plan else 'ASSIST_API_KEY_MIMO'
            key = (core_config.get(key_field) or '').strip()
            if '***' in key:
                return None
            return key or None
        if provider == 'doubao_tts':
            try:
                raw_core_config = self.load_json_config('core_config.json', {})
            except Exception:
                raw_core_config = {}
            key = ''
            if str(raw_core_config.get('ttsModelProvider') or '').strip() == 'doubao_tts':
                key = (raw_core_config.get('ttsModelApiKey') or '').strip()
                if '***' in key:
                    key = ''
            if not key:
                key = (raw_core_config.get('assistApiKeyDoubaoTts') or '').strip()
                if '***' in key:
                    key = ''
            return key or None
        return None

    @staticmethod
    def _is_vllm_omni_tts_selected(core_config: dict | None) -> bool:
        if not isinstance(core_config, dict):
            return False
        return _as_bool(core_config.get('ENABLE_CUSTOM_API'), False) and (
            str(core_config.get('ttsModelProvider') or '').strip() == 'vllm_omni'
        )

    def _is_local_tts_storage_active(
        self,
        tts_config: dict | None = None,
        core_config: dict | None = None,
    ) -> bool:
        """Return True when the current TTS config should use __LOCAL_TTS__ voices."""
        if tts_config is None:
            tts_config = self.get_model_api_config('tts_custom')
        if core_config is None:
            core_config = self.get_core_config()
        base_url = str((tts_config or {}).get('base_url') or '')
        return _as_bool((tts_config or {}).get('is_custom'), False) and base_url.startswith(('ws://', 'wss://')) and (
            not self._is_vllm_omni_tts_selected(core_config)
        )

    def get_cosyvoice_clone_runtime(self, provider: str = 'cosyvoice') -> dict:
        """Return the Alibaba CN/international runtime config explicitly selected on the voice-clone page."""
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.get_assist_api_profiles") dotted-path
        # monkeypatches keep intercepting this call site.
        from utils.config_manager import get_assist_api_profiles

        normalized_provider = str(provider or 'cosyvoice').strip().lower()
        if normalized_provider not in ('cosyvoice', 'cosyvoice_intl'):
            normalized_provider = 'cosyvoice'

        qwen_provider = 'qwen_intl' if normalized_provider == 'cosyvoice_intl' else 'qwen'
        key_field = 'ASSIST_API_KEY_QWEN_INTL' if qwen_provider == 'qwen_intl' else 'ASSIST_API_KEY_QWEN'
        core_config = self.get_core_config()
        api_key = (core_config.get(key_field) or '').strip()

        profile = get_assist_api_profiles().get(qwen_provider, {})
        raw_core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])
        try:
            file_data = self.load_json_config('core_config.json', {})
            if isinstance(file_data, dict):
                raw_core_cfg.update(file_data)
        except Exception:
            pass

        base_url = self._get_saved_provider_url(
            raw_core_cfg,
            'assist',
            qwen_provider,
            profile,
            'OPENROUTER_URL',
            'OPENROUTER_URLS',
        )
        if not base_url:
            base_url = profile.get('OPENROUTER_URL', '')

        if normalized_provider == 'cosyvoice' and not api_key:
            if not self._is_vllm_omni_tts_selected(core_config):
                try:
                    legacy_tts_config = self.get_model_api_config('tts_custom')
                except Exception:
                    legacy_tts_config = {}
                legacy_key = (legacy_tts_config.get('api_key') or '').strip()
                legacy_url = (legacy_tts_config.get('base_url') or '').strip()
                if legacy_key and not (
                    'dashscope-intl.aliyuncs.com' in legacy_url
                    or 'dashscope-us.aliyuncs.com' in legacy_url
                ):
                    api_key = legacy_key
                    if legacy_url:
                        base_url = legacy_url

        if normalized_provider == 'cosyvoice_intl' and api_key:
            suffix = api_key[-8:] if len(api_key) >= 8 else api_key
            storage_key = f'__COSYVOICE_INTL__{suffix}'
        else:
            storage_key = api_key

        return {
            'provider': normalized_provider,
            'qwen_provider': qwen_provider,
            'api_key': api_key,
            'base_url': base_url,
            'storage_key': storage_key,
            'provider_label': '阿里国际版CosyVoice' if normalized_provider == 'cosyvoice_intl' else '阿里百炼CosyVoice',
        }

    def _get_cosyvoice_storage_keys(self, voice_storage: dict | None = None) -> list[tuple[str, str]]:
        """Return the voice_storage key for the current Alibaba CN/international API key."""
        if voice_storage is None:
            voice_storage = self.load_voice_storage()
        result: list[tuple[str, str]] = []
        seen = set()

        def _add(bucket: str, provider: str):
            if bucket and bucket in voice_storage and bucket not in seen:
                seen.add(bucket)
                result.append((bucket, provider))

        domestic_runtime = self.get_cosyvoice_clone_runtime('cosyvoice')
        _add(domestic_runtime.get('storage_key', ''), 'cosyvoice')

        intl_runtime = self.get_cosyvoice_clone_runtime('cosyvoice_intl')
        intl_storage_key = intl_runtime.get('storage_key', '')
        _add(intl_storage_key, 'cosyvoice_intl')

        # 旧版国际版曾按原始 API Key 入库，存在时纳入当前视图以免音色丢失。
        intl_raw_key = (intl_runtime.get('api_key') or '').strip()
        if intl_raw_key and intl_raw_key != intl_storage_key:
            _add(intl_raw_key, 'cosyvoice_intl')

        return result

    def _get_minimax_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current MiniMax API keys.

        Uses get_tts_api_key to obtain resolved keys (including env fallback),
        generating bucket prefixes for the CN and international services respectively.
        """
        voice_storage = self.load_voice_storage()
        result = []

        # 国服 key → __MINIMAX__{suffix}
        cn_key = self.get_tts_api_key('minimax')
        if cn_key:
            suffix = cn_key[-8:] if len(cn_key) >= 8 else cn_key
            bucket = f'__MINIMAX__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)

        # 国际服 key → __MINIMAX_INTL__{suffix}
        intl_key = self.get_tts_api_key('minimax_intl')
        if intl_key:
            suffix = intl_key[-8:] if len(intl_key) >= 8 else intl_key
            bucket = f'__MINIMAX_INTL__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)

        return result

    def _get_elevenlabs_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current ElevenLabs API key."""
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('elevenlabs')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'__ELEVENLABS__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_mimo_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current MiMo API key.

        Dual to :meth:`_get_elevenlabs_storage_keys`: MiMo cloned voices live in
        a ``__MIMO__{suffix}`` bucket keyed by the MiMo API key, so they merge
        into the current-API voice list regardless of which core/TTS provider is
        otherwise active (a MiMo clone is selected by ``voice_meta.provider`` at
        dispatch, not by config — see ``workers/mimo.py``)."""
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('mimo')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'__MIMO__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_doubao_tts_storage_keys(self) -> list[str]:
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('doubao_tts')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'{DOUBAO_VOICE_STORAGE_KEY}{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_vllm_omni_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for vLLM-Omni cloned voices.

        Dual to :meth:`_get_mimo_storage_keys`, with one key difference: vLLM-Omni
        is a self-hosted local service with **no API key**, so cloned voices live
        in a single fixed ``__VLLM_OMNI__`` bucket (no key suffix) instead of a
        per-key ``__MIMO__{suffix}`` bucket. A vLLM-Omni clone is selected by
        ``voice_meta.provider`` at dispatch (the inline reference-audio model, see
        ``workers/vllm_omni.py``), so the bucket merges into the current-API voice
        list regardless of which core/TTS provider is otherwise active."""
        voice_storage = self.load_voice_storage()
        bucket = '__VLLM_OMNI__'
        return [bucket] if bucket in voice_storage else []

    @staticmethod
    def _infer_provider_from_storage_key(storage_key: str) -> str:
        """Infer the provider from a voice_storage partition key (only for legacy data compatibility)."""
        if storage_key == '__LOCAL_TTS__':
            return 'local'
        if storage_key.startswith('__VLLM_OMNI__'):
            return 'vllm_omni'
        if storage_key.startswith('__MIMO__'):
            return 'mimo'
        if storage_key.startswith(DOUBAO_VOICE_STORAGE_KEY):
            return 'doubao_tts'
        if storage_key.startswith('__ELEVENLABS__'):
            return 'elevenlabs'
        if storage_key.startswith('__MINIMAX_INTL__'):
            return 'minimax_intl'
        if storage_key.startswith('__MINIMAX__'):
            return 'minimax'
        if storage_key.startswith('__COSYVOICE_INTL__'):
            return 'cosyvoice_intl'
        return 'cosyvoice'

    def get_voices_for_current_api(self, for_listing: bool = False):
        """Get all voices for the current TTS config

        Returns voices based on the TTS config actually in use:
        1. Local TTS (ws/wss protocol) → voices under __LOCAL_TTS__
        2. Alibaba Cloud TTS (via ASSIST_API_KEY_QWEN) → voices under that API key
        3. Otherwise → voices under AUDIO_API_KEY
        The result also merges Alibaba international, MiniMax and ElevenLabs voices.

        Every returned voice_data is guaranteed to contain a ``provider`` field
        (``local`` / ``minimax`` / ``minimax_intl`` / ``elevenlabs`` / ``cosyvoice`` / ``cosyvoice_intl``).

        ``for_listing=True`` enables UI-list-oriented filtering: on the free edition, skip
        the *cloud* main partitions (CosyVoice / Qwen), because those voices require paid
        API key auth (and cannot actually be used at runtime via step_realtime_tts_worker
        free_mode); listing them would only mislead users. ``__LOCAL_TTS__`` runs local
        inference over WebSocket and still works on the free edition, so it must be shown
        even with for_listing+free. MiniMax and GSV use independent config/routing and
        also work on the free edition, so the MiniMax merge below is kept and the
        /custom_tts_voices GSV is unaffected.

        The default ``for_listing=False`` keeps the full view — validation chains like
        ``validate_voice_id`` / ``cleanup_invalid_voice_ids`` must see every voice actually
        present in storage, otherwise the free edition would misjudge voice_ids users saved
        during a paid period as nonexistent and clear them outright during cleanup.

        Provider-keyed CosyVoice clone buckets are still merged below: if a
        clone provider API key is configured and has stored voices, the clone
        list should show those voices even when the main cloud bucket is hidden.
        """
        voice_storage = self.load_voice_storage()
        storage_key = ''
        result: dict = {}

        tts_config = self.get_model_api_config('tts_custom')
        core_config = self.get_core_config()
        is_local_tts = self._is_local_tts_storage_active(tts_config, core_config)
        hide_cloud_main = for_listing and self.is_free_voice()

        if is_local_tts:
            # 本地 WebSocket TTS：免费版仍可用，列表必须可见
            storage_key = '__LOCAL_TTS__'
            all_voices = voice_storage.get(storage_key, {})
            result = dict(all_voices)
        elif not hide_cloud_main:
            tts_api_key = tts_config.get('api_key', '')
            if tts_api_key:
                storage_key = tts_api_key
                all_voices = voice_storage.get(storage_key, {})
                result = dict(all_voices)
            else:
                audio_api_key = core_config.get('AUDIO_API_KEY', '')
                if audio_api_key:
                    storage_key = audio_api_key
                    all_voices = voice_storage.get(storage_key, {})
                    result = dict(all_voices)

        cosyvoice_storage_keys = self._get_cosyvoice_storage_keys(voice_storage)

        # 确保主分区音色有 provider 字段
        default_provider = self._infer_provider_from_storage_key(storage_key) if storage_key else 'cosyvoice'
        for cosy_key, cosy_provider in cosyvoice_storage_keys:
            if cosy_key == storage_key:
                default_provider = cosy_provider
                break
        for vdata in result.values():
            if isinstance(vdata, dict):
                if 'provider' not in vdata:
                    vdata['provider'] = default_provider
                elif default_provider == 'cosyvoice_intl' and vdata.get('provider') == 'cosyvoice':
                    vdata['provider'] = 'cosyvoice_intl'

        # 合并阿里国际版音色，并确保 provider 字段与分区一致
        for ck, cosy_provider in cosyvoice_storage_keys:
            if ck == storage_key:
                continue
            cosy_voices = voice_storage.get(ck, {})
            for vid, vdata in cosy_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and (
                        'provider' not in vdata or ck.startswith('__COSYVOICE_INTL__')
                    ):
                        vdata['provider'] = cosy_provider
                    result[vid] = vdata

        # 合并 MiniMax 音色，并确保 provider 字段
        for mk in self._get_minimax_storage_keys():
            mm_provider = self._infer_provider_from_storage_key(mk)
            minimax_voices = voice_storage.get(mk, {})
            for vid, vdata in minimax_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = mm_provider
                    result[vid] = vdata

        # 合并 ElevenLabs 音色，并确保 provider 字段
        for ek in self._get_elevenlabs_storage_keys():
            eleven_voices = voice_storage.get(ek, {})
            for vid, vdata in eleven_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'elevenlabs'
                    result[vid] = vdata

        # 合并 MiMo 克隆音色，并确保 provider 字段（dual to ElevenLabs/MiniMax；MiMo 克隆走
        # 独立 __MIMO__ 桶 + voice_meta 选中，与当前 core/TTS provider 无关）
        for mimo_key in self._get_mimo_storage_keys():
            mimo_voices = voice_storage.get(mimo_key, {})
            for vid, vdata in mimo_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'mimo'
                    result[vid] = vdata

        for doubao_key in self._get_doubao_tts_storage_keys():
            doubao_voices = voice_storage.get(doubao_key, {})
            for vid, vdata in doubao_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'doubao_tts'
                    result[vid] = vdata

        # 合并 vLLM-Omni 克隆音色（dual to MiMo；vLLM-Omni 克隆走固定 __VLLM_OMNI__ 桶
        # + voice_meta 选中，与 MiMo 同构。差异：vLLM-Omni 是本地服务无 API key，桶名固定）
        for vllm_key in self._get_vllm_omni_storage_keys():
            vllm_voices = voice_storage.get(vllm_key, {})
            for vid, vdata in vllm_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'vllm_omni'
                    result[vid] = vdata

        if for_listing:
            # UI 试听列表不需要 MiMo 克隆的参考样本 base64（可达 MB）——剥掉，避免把大 blob
            # 推给前端。dispatch / preview 走 for_listing=False，仍拿到完整 voice_meta。
            result = {
                vid: ({k: v for k, v in vdata.items() if k != 'clone_sample_b64'}
                      if isinstance(vdata, dict) and 'clone_sample_b64' in vdata else vdata)
                for vid, vdata in result.items()
            }

        return result

    def save_voice_for_current_api(self, voice_id, voice_data):
        """Save a voice for the current AUDIO_API_KEY"""
        core_config = self.get_core_config()
        audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            raise ValueError("未配置 AUDIO_API_KEY")

        voice_storage = self.load_voice_storage()
        if audio_api_key not in voice_storage:
            voice_storage[audio_api_key] = {}

        voice_storage[audio_api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    def save_voice_for_api_key(self, api_key: str, voice_id: str, voice_data: dict):
        """Save a voice for the given API key (used when cloning with the actual API key instead of AUDIO_API_KEY)"""
        if not api_key:
            raise ValueError("API Key 不能为空")

        voice_storage = self.load_voice_storage()
        if api_key not in voice_storage:
            voice_storage[api_key] = {}

        voice_storage[api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    async def asave_voice_for_api_key(self, api_key: str, voice_id: str, voice_data: dict):
        """Persist a registered voice without blocking an async request handler."""
        return await asyncio.to_thread(
            self.save_voice_for_api_key,
            api_key,
            voice_id,
            voice_data,
        )

    def voice_id_exists_in_any_storage(self, voice_id: str) -> bool:
        """Whether voice_id appears under any bucket of voice_storage.json.

        Wider than the view of get_voices_for_current_api(): the latter filters buckets by
        the current tts_custom config (AUDIO_API_KEY / __LOCAL_TTS__ / current
        ASSIST_API_KEY_QWEN etc.), so cloned voices saved in old buckets before a config
        switch don't appear in the view. Collision detection ("has the user ever explicitly
        cloned this voice_id") must look at the full storage, not just the current view,
        otherwise a same-named voice gets silently switched to the built-in provider.
        """
        if not voice_id:
            return False
        voice_storage = self.load_voice_storage()
        if not isinstance(voice_storage, dict):
            return False
        voice_id_key = voice_id.casefold()
        for bucket in voice_storage.values():
            if isinstance(bucket, dict) and any(
                isinstance(stored_voice_id, str)
                and stored_voice_id.casefold() == voice_id_key
                for stored_voice_id in bucket
            ):
                return True
        return False

    def find_voice_by_audio_md5(self, api_key: str, audio_md5: str, ref_language: str | None = None):
        """Look up an existing voice by reference-audio MD5 (and optional ref_language) under the given API key.

        Returns (voice_id, voice_data) or None.
        Old entries without an audio_md5 field are skipped automatically (backward compatible).
        When ref_language is not None, the ref_language in voice_data must also match
        (old entries without a ref_language field are treated as 'ch').
        """
        if not api_key or not audio_md5:
            return None
        voice_storage = self.load_voice_storage()
        voices = voice_storage.get(api_key, {})
        for vid, vdata in voices.items():
            if isinstance(vdata, dict) and vdata.get('audio_md5') == audio_md5:
                if ref_language is not None and vdata.get('ref_language', 'ch') != ref_language:
                    continue
                return (vid, vdata)
        return None

    def find_cosyvoice_voice_by_audio_md5(
        self,
        provider: str,
        audio_md5: str,
        ref_language: str | None = None,
    ):
        """Look up a reference-audio MD5 across the current and legacy CosyVoice storage partitions."""
        runtime = self.get_cosyvoice_clone_runtime(provider)
        storage_keys = []
        seen = set()

        def _add(storage_key: str):
            storage_key = (storage_key or '').strip()
            if storage_key and storage_key not in seen:
                seen.add(storage_key)
                storage_keys.append(storage_key)

        _add(runtime.get('storage_key', ''))
        if runtime.get('provider') == 'cosyvoice_intl':
            # 旧版国际版曾按原始 API Key 入库，MD5 去重也必须兼容该分区。
            _add(runtime.get('api_key', ''))

        for storage_key in storage_keys:
            existing = self.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                return existing
        return None

    def delete_voice_for_current_api(self, voice_id):
        """Delete the given voice under the current TTS config (including standalone-provider voices)"""
        voice_storage = self.load_voice_storage()

        # 先检查带前缀的独立服务商存储（含 vLLM-Omni 固定桶 __VLLM_OMNI__）
        for storage_key in list(voice_storage.keys()):
            if (
                storage_key.startswith('__MINIMAX__')
                or storage_key.startswith('__MINIMAX_INTL__')
                or storage_key.startswith('__ELEVENLABS__')
                or storage_key.startswith('__MIMO__')
                or storage_key.startswith(DOUBAO_VOICE_STORAGE_KEY)
                or storage_key.startswith('__COSYVOICE_INTL__')
                or storage_key.startswith('__VLLM_OMNI__')
            ) and voice_id in voice_storage.get(storage_key, {}):
                # 克隆身份（含 MiMo 的样本 base64）都在 voice_data 里，删除 entry 随之消失，
                # 无旁路本地文件需清理（对偶 MiniMax/ElevenLabs）。
                del voice_storage[storage_key][voice_id]
                self.save_voice_storage(voice_storage)
                return True

        # 再检查当前阿里国内/国际 API Key 的原始分区
        for storage_key, _provider in self._get_cosyvoice_storage_keys():
            if voice_id in voice_storage.get(storage_key, {}):
                del voice_storage[storage_key][voice_id]
                self.save_voice_storage(voice_storage)
                return True
        
        tts_config = self.get_model_api_config('tts_custom')
        is_local_tts = self._is_local_tts_storage_active(tts_config)

        if is_local_tts:
            api_key = '__LOCAL_TTS__'
        else:
            api_key = tts_config.get('api_key', '')
            if not api_key:
                core_config = self.get_core_config()
                api_key = core_config.get('AUDIO_API_KEY', '')

        if not api_key:
            return False

        if api_key not in voice_storage:
            return False

        if voice_id in voice_storage[api_key]:
            del voice_storage[api_key][voice_id]
            self.save_voice_storage(voice_storage)
            return True
        return False

    def _is_selected_hosted_preset_voice(self, voice_id):
        """Whether voice_id is a built-in (preset) voice of the currently selected
        TTS provider in tts_provider_registry (e.g. MiMo, a hosted SaaS).

        Dual to :func:`is_saveable_native_voice` but for the unified provider
        registry: a hosted/local provider's presets are only saveable while that
        provider is the one dispatch would route to, so this gates on the same
        selection the dispatcher uses.

        The hosted providers are registered as a side effect of importing
        ``main_logic.tts_client``. config_manager (utils layer) must NOT import it
        — that's a CI-enforced layer inversion (utils → main_logic). We rely on the
        running app having imported tts_client at startup (the TTS pipeline does),
        so the registry is populated by the time any voice is validated; if it
        isn't (or the lookup errors), this degrades to "not a preset voice" rather
        than breaking validation.
        """
        try:
            from utils import tts_provider_registry
            return tts_provider_registry.is_selected_preset_voice(
                self.get_core_config() or {}, self, voice_id
            )
        except Exception:
            logger.warning("hosted preset voice 校验异常，按非预制处理", exc_info=True)
            return False

    def validate_voice_id(self, voice_id):
        """Validate whether voice_id is valid under the current AUDIO_API_KEY.
        
        Validation covers four kinds of voice_id:
          1. "cosyvoice-v2/v3..." → legacy format, always invalid
          2. "gsv:xxx" → delegated to check_custom_tts_voice_allowed (custom_tts_adapter);
             the adapter decides validity from the tts_custom config
          3. plain IDs → looked up in voice_storage (CosyVoice cloud-cloned voices)
          4. free preset voices → only statically whitelisted here; at runtime core.py
             _should_block_free_preset_voice decides dynamically per route
             (lanlan.tech / lanlan.app) whether they are actually enabled
             (the lanlan.app overseas node does not support preset voices)
        """
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.check_custom_tts_voice_allowed")
        # dotted-path monkeypatches keep intercepting this call site.
        from utils.config_manager import check_custom_tts_voice_allowed

        voice_id = str(voice_id or '').strip()
        if not voice_id:
            return True

        if voice_id.startswith('eleven:'):
            return len(voice_id) > len('eleven:')

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        if self._is_vllm_omni_tts_selected(self.get_core_config()):
            return True

        voices = self.get_voices_for_current_api()
        if voice_id in voices:
            return True

        if is_saveable_native_voice(self, voice_id):
            return True

        # hosted/local provider 的内置预制音色（如选中 MiMo 时的预制声线），由
        # tts_provider_registry 收口，仅在该 provider 被选中时算合法
        if self._is_selected_hosted_preset_voice(voice_id):
            return True

        # 免费预设音色允许豁免保存校验，运行时再由 core.py 按当前线路动态判断可用性
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def validate_voice_id_for_api_key(self, api_key: str, voice_id: str) -> bool:
        """Validate whether voice_id is valid under the given API key"""
        # Late-bound through the package facade (see validate_voice_id).
        from utils.config_manager import check_custom_tts_voice_allowed

        voice_id = str(voice_id or '').strip()
        if not voice_id:
            return True

        if voice_id.startswith('eleven:'):
            return len(voice_id) > len('eleven:')

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        voice_storage = self.load_voice_storage()
        voices = voice_storage.get(api_key, {})
        if voice_id in voices:
            return True

        if is_saveable_native_voice(self, voice_id):
            return True

        if self._is_selected_hosted_preset_voice(voice_id):
            return True

        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def normalize_voice_id_to_config(self, voice_id):
        """Resolve a flat / prefixed ``voice_id`` into a structured ``VoiceConfig``.

        A bare id (no ``gsv:`` / ``eleven:`` prefix) needs runtime context to decide
        its ``source`` / ``provider``; this reuses the same resolution chain as
        :meth:`validate_voice_id` (vLLM selected / a clone in the current API's
        voice_storage / a saveable native voice / a free preset) and feeds that
        context to the pure :func:`utils.voice_config.normalize_voice_id`, so the
        migration stays faithful and unambiguous.

        An unresolvable bare id is carried through unchanged in ``ref`` (never
        dropped); callers treat it as "leave the value as-is".
        """
        from utils.voice_config import normalize_voice_id
        from utils.native_voice_registry import (
            get_active_realtime_native_provider,
            is_saveable_native_voice,
        )
        from utils.api_config_loader import get_free_voices

        _voices_cache = {}

        def _clone_lookup(ref):
            if 'voices' not in _voices_cache:
                _voices_cache['voices'] = self.get_voices_for_current_api()
            vdata = _voices_cache['voices'].get(ref)
            if isinstance(vdata, dict):
                return str(vdata.get('provider') or '')
            return None

        def _hosted_preset_provider(ref):
            """Selected hosted/local provider key when ``ref`` is one of its preset
            voices (e.g. MiMo's "Milo"), else None — so the structured object keeps
            the ``source=preset / provider=<key>`` ownership the flat string drops.

            Dual to :meth:`_is_selected_hosted_preset_voice` (used by validate); both
            gate on tts_provider_registry's selection so a hosted preset is only
            recognized while that provider is the one dispatch would route to. A
            single ``selected_preset_provider_key`` dispatch resolves both membership
            and key together, so the two can never disagree. Same layer rule:
            config_manager (utils) must NOT import main_logic, so we only query the
            same-layer registry, which the running app populates by importing
            main_logic.tts_client at startup. Degrades to None on error.
            """
            try:
                from utils import tts_provider_registry
                return tts_provider_registry.selected_preset_provider_key(
                    self.get_core_config() or {}, self, ref
                )
            except Exception:
                logger.warning("hosted preset voice 归一化异常，按非预制处理", exc_info=True)
                return None

        return normalize_voice_id(
            voice_id,
            vllm_selected=self._is_vllm_omni_tts_selected(self.get_core_config()),
            clone_provider_lookup=_clone_lookup,
            is_native=lambda ref: is_saveable_native_voice(self, ref),
            native_provider=get_active_realtime_native_provider(self) or '',
            hosted_preset_provider=_hosted_preset_provider,
            free_voice_ids=set(get_free_voices().values()),
        )

    def voice_id_to_storage_value(self, voice_id):
        """Convert a user-set legacy ``voice_id`` string into its at-rest storage form.

        Write side of the voice-source-unification "union-find style lazy migration":
        every time the user sets/changes a voice, that one entry is migrated to the
        structured object ``{source, provider, ref}`` (migrate-on-touch, never a
        full-table sweep). Empty value is stored as an empty string (= no voice set).
        The read side (:func:`utils.voice_config.read_legacy_voice_id`) tolerates both
        forms, so untouched legacy flat strings keep working.
        """
        s = str(voice_id or '').strip()
        if not s:
            return ''
        from utils.voice_config import to_legacy_voice_id
        vc = self.normalize_voice_id_to_config(s)
        # Round-trip guard: only migrate to the structured object when it reads back to
        # the exact submitted library key. Otherwise (e.g. a provider-tagged but
        # un-prefixed clone key, where to_legacy_voice_id would re-add a prefix and
        # change the key) keep the legacy string verbatim — never let migration rewrite
        # the key a binding points at, or _get_voice_meta would miss and TTS misroute.
        if to_legacy_voice_id(vc) != s:
            return s
        # Ownership guard: a bare id we could NOT resolve (no source/provider tagged)
        # carries zero information beyond the flat string. Storing it as
        # ``{source:"", provider:"", ref}`` is a half-migrated shell that only bloats
        # storage and disguises "ownership unknown" as "migrated" — keep the legacy
        # string until we can actually tag its ownership. (Only resolved bindings,
        # incl. hosted presets like MiMo's, become structured objects.)
        if not vc.source and not vc.provider:
            return s
        return vc.to_dict()

    def cleanup_invalid_voice_ids(self):
        """Clean up invalid voice_ids in characters.json.
        
        Validity is decided uniformly via validate_voice_id, with no provider-specific logic.
        Note: free preset voices are not cleaned here (whitelisted by validate_voice_id);
        actual availability is decided at runtime by core.py per free + lanlan.app/lanlan.tech route.

        Before clearing, deprecated free YUI preset voices are first remapped to the active
        yui_cn (remap_deprecated_free_yui_voice_id), so existing users aren't judged invalid
        and silently dropped to the generic default voice due to the YUI voice ID change.
        A migration hit also triggers a save.

        Returns:
            (cleaned_count, legacy_cosyvoice_names): total cleaned, and the list of character names still using legacy CosyVoice voices
        """
        character_data = self.load_characters()
        cleaned_count = 0
        migrated_count = 0
        legacy_cosyvoice_names: list[str] = []

        catgirls = character_data.get('猫娘', {})
        for name, config in catgirls.items():
            # 容忍扁平串 / 结构对象两形态，统一按 legacy 字符串做 remap / validate；
            # cleanup 不在此把有效条目压成对象（守住「不 bulk sweep」，迁移只在用户设音色时发生）。
            voice_id = read_legacy_voice_id(get_reserved(config, 'voice_id', default='', legacy_keys=('voice_id',)))
            if not voice_id:
                continue
            # 已废弃的免费 YUI 预设音色：先平移到现役 yui_cn，再 continue 跳过后续
            # invalid 判定（新值在 free_voices 白名单内本就合法），保住默认 YUI 音色
            remapped = self.remap_deprecated_free_yui_voice_id(voice_id)
            if remapped and remapped != voice_id:
                set_reserved(config, 'voice_id', remapped)
                migrated_count += 1
                logger.info(
                    "猫娘 '%s' 的废弃 YUI 预设音色 '%s' 已平移到 '%s'",
                    name,
                    voice_id,
                    remapped,
                )
                continue
            # 旧版 CosyVoice 音色：保留 voice_id 不清空，仅记录供通知
            if self.is_legacy_cosyvoice_id(voice_id):
                legacy_cosyvoice_names.append(name)
                continue
            # 其他无效 voice_id（storage 中已不存在）：清空
            if not self.validate_voice_id(voice_id):
                logger.warning(
                    "猫娘 '%s' 的 voice_id '%s' 在当前 API 的 voice_storage 中不存在，已清除",
                    name,
                    voice_id,
                )
                set_reserved(config, 'voice_id', '')
                cleaned_count += 1

        if cleaned_count > 0 or migrated_count > 0:
            self.save_characters(character_data)
            if cleaned_count > 0:
                logger.info("已清理 %d 个无效的 voice_id 引用", cleaned_count)
            if migrated_count > 0:
                logger.info("已平移 %d 个废弃 YUI 预设音色", migrated_count)

        return cleaned_count, legacy_cosyvoice_names

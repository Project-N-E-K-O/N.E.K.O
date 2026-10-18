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

"""Core API config endpoints (/core_api, /api_providers).

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router
from .connectivity import _auto_resolve_provider_urls_for_save

import asyncio
import json
from copy import deepcopy
from fastapi import Request
from ..shared_state import get_session_manager, get_initialize_character_data
from utils.file_utils import read_json_async
from utils.cloudsave_runtime import MaintenanceModeError
from utils.config_manager import ensure_default_yui_voice_for_free_api


CORE_CONFIG_SECRET_SENTINEL = "__NEKO_SECRET_MASKED__"

CORE_CONFIG_MODEL_TYPES = (
    'conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'emotion',
    'vision', 'agent', 'omni', 'tts', 'image',
)

CORE_CONFIG_ASSIST_API_KEY_FIELDS = (
    'assistApiKeyQwen', 'assistApiKeyQwenIntl', 'assistApiKeyOpenai',
    'assistApiKeyDeepseek', 'assistApiKeyGlm', 'assistApiKeyStep',
    'assistApiKeySilicon', 'assistApiKeyGemini', 'assistApiKeyKimi',
    'assistApiKeyDoubao', 'assistApiKeyDoubaoTts', 'assistApiKeyMinimax',
    'assistApiKeyMinimaxIntl', 'assistApiKeyMimo',
    'assistApiKeyMimoTokenPlan', 'assistApiKeyElevenlabs', 'assistApiKeyGrok',
    'assistApiKeyClaude', 'assistApiKeyKimiCode', 'assistApiKeyOpenrouter',
    'assistApiKeyOrcarouter',
    'assistApiKeyRequesty',
)

CORE_CONFIG_MODEL_API_KEY_FIELDS = tuple(
    f'{model_type}ModelApiKey' for model_type in CORE_CONFIG_MODEL_TYPES
)

CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS = (
    'doubaoVoiceManagementAccessKey', 'doubaoVoiceManagementSecretKey',
)
CORE_CONFIG_VOICE_MANAGEMENT_CONTEXT_FIELDS = (
    'doubaoVoiceManagementAppId', 'doubaoVoiceManagementProjectName',
)

CORE_CONFIG_PROVIDER_API_KEY_FIELDS = frozenset(
    CORE_CONFIG_ASSIST_API_KEY_FIELDS
)

# Config-file field names.  The GET response uses ``api_key`` for
# ``coreApiKey``; all other secret names are identical in both directions.
CORE_CONFIG_SECRET_FIELDS = (
    'coreApiKey',
    *CORE_CONFIG_ASSIST_API_KEY_FIELDS,
    'mcpToken',
    *CORE_CONFIG_MODEL_API_KEY_FIELDS,
    *CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS,
)


def is_core_config_secret_placeholder(value) -> bool:
    """Return whether *value* means "keep the stored secret".

    New clients use one exact sentinel.  The narrowly-shaped star checks keep
    round-trips from older clients working without treating every real key
    containing ``***`` as a placeholder.
    """
    if not isinstance(value, str):
        return False

    stripped = value.strip()
    if stripped == CORE_CONFIG_SECRET_SENTINEL:
        return True
    if len(stripped) >= 3 and set(stripped) == {'*'}:
        return True
    # The all-dot display (short or un-disclosable secrets) must round-trip as
    # "keep existing" too, never be persisted as a literal bullet credential.
    if stripped and set(stripped) == {'•'}:
        return True

    # Display masks keep a visible prefix/suffix and star out the middle.  Both
    # the current 3+3 shape and the legacy 6+6 shape mean "keep existing".
    return _is_display_mask_shape(stripped, 3) or _is_display_mask_shape(stripped, 6)


def _is_display_mask_shape(stripped: str, edge: int) -> bool:
    """Whether *stripped* is ``edge`` visible chars + >=3 stars + ``edge`` chars."""
    if len(stripped) < edge * 2 + 3:
        return False
    prefix, masked, suffix = (
        stripped[:edge],
        stripped[edge:-edge],
        stripped[-edge:],
    )
    return (
        '*' not in prefix
        and '*' not in suffix
        and len(masked) >= 3
        and set(masked) == {'*'}
    )


def redact_core_config_secret(value, *, preserve_free_access: bool = False) -> str:
    """Replace a non-empty config secret with the public response sentinel."""
    if value is None or value == '':
        return ''
    if preserve_free_access and value == 'free-access':
        return 'free-access'
    return CORE_CONFIG_SECRET_SENTINEL


def mask_core_config_secret_for_display(value) -> str:
    """Return a safe, non-reversible partial display for a stored secret.

    Only the first and last three characters are exposed, and only when the
    secret is long enough that those fragments cannot disclose it in full.
    """
    if value is None or value == '' or value == 'free-access':
        return ''
    value = str(value)
    # A three-character prefix/suffix would disclose a short credential in full.
    if len(value) < 9:
        return '••••••••••••'
    prefix, suffix = value[:3], value[-3:]
    if '*' in prefix or '*' in suffix:
        return '••••••••••••'
    return f'{prefix}{"*" * (len(value) - 6)}{suffix}'


def apply_core_config_secret_update(target: dict, source: dict, field: str) -> bool:
    """Apply a submitted secret unless it is a keep-existing placeholder.

    Returns ``True`` only when the field was written.  In particular, an empty
    string is a real update so optional credentials can be explicitly cleared.
    """
    if field not in source or is_core_config_secret_placeholder(source[field]):
        return False
    target[field] = source[field]
    return True


def get_core_config_provider_api_key_field(provider, api_key_registry):
    """Resolve a provider's config field through the allowlisted registry."""
    if not isinstance(provider, str) or not isinstance(api_key_registry, dict):
        return None
    provider_config = api_key_registry.get(provider)
    if not isinstance(provider_config, dict):
        return None
    field = provider_config.get('config_field')
    if field not in CORE_CONFIG_PROVIDER_API_KEY_FIELDS:
        return None
    return field


@router.get("/core_api")
async def get_core_config_api():
    """Get the core config (API keys)."""
    try:
        # Try reading core_config.json
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            core_config_path = str(config_manager.get_runtime_config_path('core_config.json'))
            core_cfg = await read_json_async(core_config_path)
            api_key = core_cfg.get('coreApiKey', '')
            runtime_core_config = await config_manager.aget_core_config()
        except FileNotFoundError:
            # If absent, return CORE_API_KEY from current configuration
            _config_manager = get_config_manager()
            core_config = await _config_manager.aget_core_config()
            runtime_core_config = core_config
            api_key = core_config.get('CORE_API_KEY','')
            # Create empty configuration for default returns
            core_cfg = {}
            runtime_core_api_provider = core_config.get('CORE_API_TYPE') or ''
            runtime_assist_api_provider = core_config.get('assistApi') or ''
        else:
            runtime_core_api_provider = ''
            runtime_assist_api_provider = ''
        
        # Old core_config.json may contain only coreApiKey without assistApiKey* fields;
        # match ConfigManager.get_core_config() fallback logic,
        # but only fall back to providers matching coreApi / assistApi,
        # avoiding incompatible keys populated into other providers.
        fallback_key = api_key if api_key != 'free-access' else ''
        _core_api_provider = core_cfg.get('coreApi') or runtime_core_api_provider or 'qwen'
        _assist_api_provider = core_cfg.get('assistApi') or runtime_assist_api_provider
        if not _assist_api_provider:
            _assist_api_provider = 'free' if _core_api_provider == 'free' else 'qwen'
        realtime_config = await config_manager.aget_model_api_config(
            'realtime',
            core_config=runtime_core_config,
        )
        _effective_core_api_provider = str(
            realtime_config.get('api_type')
            or runtime_core_config.get('CORE_API_TYPE')
            or _core_api_provider
        ).strip().lower()
        from main_logic.asr_client import (
            get_asr_core_capabilities,
            is_local_asr_available,
        )
        _core_asr_capabilities = get_asr_core_capabilities(
            _effective_core_api_provider
        )
        try:
            # find_spec walks sys.path finders; keep it off the event loop.
            _local_asr_available = await asyncio.to_thread(is_local_asr_available)
        except Exception:
            logger.warning('Unable to probe local ASR availability', exc_info=True)
            _local_asr_available = False
        _fallback_providers = {_core_api_provider, _assist_api_provider}
        _doubao_tts_shared_key = ''
        if str(core_cfg.get('ttsModelProvider') or '').strip() == 'doubao_tts':
            _doubao_tts_shared_key = core_cfg.get('ttsModelApiKey', '')

        def _fb(provider: str) -> str:
            """Fall back to coreApiKey only when the provider matches the user-selected coreApi/assistApi."""
            return fallback_key if provider in _fallback_providers else ''

        response = {
            "api_key": api_key,
            "coreApi": _core_api_provider,
            "effectiveCoreApi": _effective_core_api_provider,
            "supportsIndependentAsr": (
                None
                if _core_asr_capabilities is None
                else _core_asr_capabilities.supports_independent_asr
            ),
            # Whether the optional local ASR dependency is installed. Packaged
            # builds do not ship it, so the settings UI hides the option.
            "localAsrAvailable": _local_asr_available,
            "assistApi": _assist_api_provider,
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', '') or _fb('qwen'),
            "assistApiKeyQwenIntl": core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl'),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', '') or _fb('openai'),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', '') or _fb('glm'),
            "assistApiKeyStep": core_cfg.get('assistApiKeyStep', '') or _fb('step'),
            "assistApiKeySilicon": core_cfg.get('assistApiKeySilicon', '') or _fb('silicon'),
            "assistApiKeyGemini": core_cfg.get('assistApiKeyGemini', '') or _fb('gemini'),
            "assistApiKeyKimi": core_cfg.get('assistApiKeyKimi', '') or _fb('kimi'),
            "assistApiKeyKimiCode": core_cfg.get('assistApiKeyKimiCode', '') or _fb('kimi_code'),
            "assistApiKeyDeepseek": core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek'),
            "assistApiKeyDoubao": core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao'),
            "assistApiKeyDoubaoTts": core_cfg.get('assistApiKeyDoubaoTts', '') or _doubao_tts_shared_key,
            # MiniMax/MiMo are assist-only TTS providers, so coreApiKey may be incompatible;
            # do not fall back and inject an invalid TTS credential causing 401.
            "assistApiKeyMinimax": core_cfg.get('assistApiKeyMinimax', ''),
            "assistApiKeyMinimaxIntl": core_cfg.get('assistApiKeyMinimaxIntl', ''),
            "assistApiKeyMimo": core_cfg.get('assistApiKeyMimo', ''),
            "useMimoTokenPlan": core_cfg.get('useMimoTokenPlan', False) is True or str(core_cfg.get('useMimoTokenPlan', False)).lower() in ('true', '1', 'yes', 'on'),
            "assistApiKeyMimoTokenPlan": core_cfg.get('assistApiKeyMimoTokenPlan', ''),
            "assistApiKeyElevenlabs": core_cfg.get('assistApiKeyElevenlabs', ''),
            "assistApiKeyGrok": core_cfg.get('assistApiKeyGrok', '') or _fb('grok'),
            "assistApiKeyClaude": core_cfg.get('assistApiKeyClaude', '') or _fb('claude'),
            "assistApiKeyOpenrouter": core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter'),
            "assistApiKeyOrcarouter": core_cfg.get('assistApiKeyOrcarouter', '') or _fb('orcarouter'),
            "assistApiKeyRequesty": core_cfg.get('assistApiKeyRequesty', ''),
            "mcpToken": core_cfg.get('mcpToken', ''),
            "openclawUrl": core_cfg.get('openclawUrl'),
            "openclawTimeout": core_cfg.get('openclawTimeout'),
            "openclawDefaultSenderId": core_cfg.get('openclawDefaultSenderId'),
            "enableCustomApi": core_cfg.get('enableCustomApi', False),
            "resolvedProviderUrls": core_cfg.get('resolvedProviderUrls', {}) if isinstance(core_cfg.get('resolvedProviderUrls'), dict) else {},
            # Custom API fields: Provider / Url / Id / ApiKey per model type
            **{
                f'{mt}Model{suffix}': core_cfg.get(f'{mt}Model{suffix}', '')
                for mt in CORE_CONFIG_MODEL_TYPES
                for suffix in ('Provider', 'Url', 'Id', 'ApiKey')
            },
            "gptsovitsEnabled": core_cfg.get('gptsovitsEnabled'),
            "ttsProvider": core_cfg.get('ttsProvider', ''),
            "ttsVoiceId": core_cfg.get('ttsVoiceId', ''),
            "disableTts": core_cfg.get('disableTts', False) is True or str(core_cfg.get('disableTts', False)).lower() in ('true', '1', 'yes', 'on'),
            "success": True
        }
        for field in CORE_CONFIG_VOICE_MANAGEMENT_CONTEXT_FIELDS:
            response[field] = core_cfg.get(field, '')
        for field in CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS:
            value = core_cfg.get(field, '')
            response[field] = redact_core_config_secret(value)
            response[f'{field}_display'] = mask_core_config_secret_for_display(value)
        response['api_key'] = redact_core_config_secret(
            response['api_key'],
            preserve_free_access=True,
        )
        # Keep the POST contract as a sentinel while offering a separate,
        # non-reversible value for settings-page display.
        response['api_key_display'] = mask_core_config_secret_for_display(api_key)
        # The assist input needs only its currently selected provider's mask.
        # Resolve through the canonical registry instead of exposing a display
        # fragment for every Key Book entry.
        response['assist_api_key_display'] = ''
        try:
            from utils.api_config_loader import get_config

            provider_config = await asyncio.to_thread(get_config)
            api_key_registry = (
                provider_config.get('api_key_registry', {})
                if isinstance(provider_config, dict)
                else {}
            )
            assist_key_field = get_core_config_provider_api_key_field(
                _assist_api_provider,
                api_key_registry,
            )
            if assist_key_field:
                response['assist_api_key_display'] = mask_core_config_secret_for_display(
                    response.get(assist_key_field, '')
                )
        except Exception:
            logger.warning('Unable to build assist API key display mask', exc_info=True)
        for field in (
            *CORE_CONFIG_ASSIST_API_KEY_FIELDS,
            'mcpToken',
            *CORE_CONFIG_MODEL_API_KEY_FIELDS,
        ):
            response[field] = redact_core_config_secret(response[field])
        return response
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# Serialize /core_api saves at request level, separately from core_config.json file locking:
# each save computes derived fields from a snapshot (resolvedProviderUrls, Key Book slot migrations, etc.),
# then merges differences into the latest file. Interleaved /core_api requests can combine A's provider
# with B's resolvedProviderUrls based on the old provider, a combination nobody submitted. Serialization ensures
# each later snapshot contains the preceding save's result. Other writers such as memory switches use unrelated keys,
# remain outside this lock, and persist normally during URL resolution.
# Lock only snapshot read → disk write, releasing immediately afterward; frontend notification / session ending / configuration reload
# may block on slow WebSockets and must not delay subsequent saves.
_core_api_save_lock = asyncio.Lock()


@router.post("/core_api")
async def update_core_config(request: Request):
    """Update the core config (API keys); the snapshot-to-write section runs one save at a time."""
    save_lock_held = False
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        enable_custom_api = data.get('enableCustomApi', False)

        # Save to core_config.json
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        
        # Read request bodies outside the lock so slow uploads do not delay other saves. Everything from here through persistence
        # depends on the configuration snapshot and must hold the lock.
        await _core_api_save_lock.acquire()
        save_lock_held = True

        # Build configuration by loading existing values, then overlaying this submission.
        # Match the frontend API-management book, avoiding accidental clearing of omitted fields.
        # Start empty only when the file is absent; reject saves when existing files cannot be read/parsed.
        # Previously swallowing these failures as {} overwrote the entire corrupt file with only this submission's few fields.
        # Use this snapshot only for decisions/diff calculation; actual writes use aupdate_json_config below.
        from utils.config_manager.json_update import (
            json_values_equal,
            load_json_config_snapshot,
        )
        try:
            existing_core_cfg = await asyncio.to_thread(
                load_json_config_snapshot, config_manager, 'core_config.json'
            )
        except Exception as exc:
            logger.warning(f"读取 core_config.json 失败，拒绝保存以免覆盖原文件: {exc}")
            return {
                "success": False,
                "error": "core_config.json 读取失败（文件可能已损坏），为避免覆盖现有配置，本次未保存",
            }
        core_cfg = deepcopy(existing_core_cfg)

        def _incoming_provider(field, error_message):
            if field not in data:
                return None
            provider = data.get(field)
            if provider is not None and not isinstance(provider, str):
                raise TypeError(error_message)
            provider = (provider or "").strip()
            return provider or None

        def _stored_provider(field):
            provider = core_cfg.get(field)
            if not isinstance(provider, str):
                return None
            provider = provider.strip()
            return provider or None

        try:
            incoming_core_api = _incoming_provider('coreApi', 'coreApi must be a string')
            incoming_assist_api = _incoming_provider('assistApi', 'assistApi must be a string')
        except TypeError as exc:
            return {"success": False, "error": str(exc)}

        stored_core_api = _stored_provider('coreApi') or 'qwen'
        effective_core_api = incoming_core_api or stored_core_api
        core_uses_free_provider = effective_core_api == 'free'
        stored_assist_api = _stored_provider('assistApi')
        effective_assist_api = incoming_assist_api or stored_assist_api
        if not effective_assist_api:
            effective_assist_api = 'free' if core_uses_free_provider else 'qwen'

        def _usable_provider_api_key(value):
            if not isinstance(value, str):
                return ''
            normalized = value.strip()
            if (
                not normalized
                or normalized == 'free-access'
                or is_core_config_secret_placeholder(normalized)
            ):
                return ''
            return normalized

        provider_changed = bool(
            incoming_core_api and incoming_core_api != stored_core_api
        )
        core_key_is_placeholder = (
            'coreApiKey' in data
            and is_core_config_secret_placeholder(data['coreApiKey'])
        )
        stored_core_api_key = _usable_provider_api_key(
            core_cfg.get('coreApiKey', '')
        )
        needs_provider_key_promotion = (
            core_key_is_placeholder
            and (provider_changed or not stored_core_api_key)
        )
        promoted_core_api_key = None

        if provider_changed or needs_provider_key_promotion:
            from utils.api_config_loader import get_config

            provider_config = await asyncio.to_thread(get_config)
            api_key_registry = (
                provider_config.get('api_key_registry', {})
                if isinstance(provider_config, dict)
                else {}
            )

            # Preserve the old core provider's authoritative key in its Key
            # Book slot before coreApiKey is repointed.  A distinct non-empty
            # key remains authoritative only when that provider stays selected
            # as assist; otherwise the old slot is stale and must be replaced.
            if provider_changed and stored_core_api != 'free' and stored_core_api_key:
                old_provider_field = get_core_config_provider_api_key_field(
                    stored_core_api,
                    api_key_registry,
                )
                old_provider_key = (
                    _usable_provider_api_key(core_cfg.get(old_provider_field, ''))
                    if old_provider_field
                    else ''
                )
                if (
                    old_provider_field
                    and (
                        effective_assist_api != stored_core_api
                        or not old_provider_key
                    )
                ):
                    core_cfg[old_provider_field] = stored_core_api_key

            if needs_provider_key_promotion:
                if core_uses_free_provider:
                    promoted_core_api_key = 'free-access'
                else:
                    target_provider_field = get_core_config_provider_api_key_field(
                        effective_core_api,
                        api_key_registry,
                    )
                    if target_provider_field:
                        submitted_target_key = data.get(target_provider_field)
                        if (
                            target_provider_field in data
                            and not is_core_config_secret_placeholder(submitted_target_key)
                        ):
                            target_key = submitted_target_key
                        else:
                            target_key = core_cfg.get(target_provider_field, '')
                        promoted_core_api_key = _usable_provider_api_key(target_key)

                    if promoted_core_api_key is None:
                        promoted_core_api_key = ''
                    if not promoted_core_api_key and not enable_custom_api:
                        return {"success": False, "error": "API Key不能为空"}

        def _normalize_core_api_key(value):
            if is_core_config_secret_placeholder(value):
                return promoted_core_api_key
            if value is None:
                raise ValueError("API Key不能为null")
            if not isinstance(value, str):
                raise TypeError("API Key必须是字符串类型")
            return value.strip()

        # Allow missing coreApiKey only when custom APIs are enabled
        if enable_custom_api:
            # coreApiKey is optional with custom APIs enabled
            if 'coreApiKey' in data:
                try:
                    api_key = _normalize_core_api_key(data['coreApiKey'])
                except (TypeError, ValueError) as exc:
                    return {"success": False, "error": str(exc)}
                if api_key is not None:
                    core_cfg['coreApiKey'] = api_key
        else:
            # Require coreApiKey when custom APIs are disabled
            if 'coreApiKey' not in data and not core_uses_free_provider:
                return {"success": False, "error": "缺少coreApiKey字段"}
            try:
                api_key = (
                    _normalize_core_api_key(data['coreApiKey'])
                    if 'coreApiKey' in data
                    else None
                )
            except (TypeError, ValueError) as exc:
                return {"success": False, "error": str(exc)}
            effective_api_key = (
                core_cfg.get('coreApiKey', '')
                if api_key is None
                else api_key
            )
            if not core_uses_free_provider and not effective_api_key:
                return {"success": False, "error": "API Key不能为空"}
            if api_key is not None:
                core_cfg['coreApiKey'] = api_key
        # Empty coreApi / assistApi means submission before configuration finished loading, with cleared dropdowns.
        # Never overwrite valid saved providers with emptiness; reload would fall back to another provider,
        # silently moving free users away. Write only nonempty values; otherwise retain existing_core_cfg values.
        if incoming_core_api:
            core_cfg['coreApi'] = incoming_core_api
        if incoming_assist_api:
            core_cfg['assistApi'] = incoming_assist_api
        if 'resolvedProviderUrls' in data:
            resolved_urls = data.get('resolvedProviderUrls')
            if not isinstance(resolved_urls, dict):
                return {"success": False, "error": "resolvedProviderUrls must be an object"}
            sanitized_resolved_urls = {}
            for key, value in resolved_urls.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                normalized_key = key.strip()
                normalized_value = value.strip()
                if normalized_key and normalized_value:
                    sanitized_resolved_urls[normalized_key] = normalized_value
            core_cfg['resolvedProviderUrls'] = sanitized_resolved_urls
        for field in (
            *CORE_CONFIG_ASSIST_API_KEY_FIELDS, 'mcpToken',
            *CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS,
        ):
            if field in CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS and field in data:
                if not isinstance(data[field], str) or len(data[field]) > 2048:
                    return {"success": False, "error": "INVALID_MANAGEMENT_CONFIG"}
            apply_core_config_secret_update(core_cfg, data, field)
        for field in CORE_CONFIG_VOICE_MANAGEMENT_CONTEXT_FIELDS:
            if field in data:
                if not isinstance(data[field], str) or len(data[field]) > 200:
                    return {"success": False, "error": "INVALID_MANAGEMENT_CONFIG"}
                core_cfg[field] = data[field].strip()
        if 'openclawUrl' in data:
            # Frontend forms populate original saved values. If startup migration failed to persist (Windows antivirus may block
            # os.replace), this still receives old 8089; writing unchanged would persist the old port again,
            # preventing migration convergence. Normalize again at this save boundary,
            # allowing any user save to complete startup migration.
            _submitted_openclaw_url = data['openclawUrl']
            core_cfg['openclawUrl'] = (
                config_manager._migrated_openclaw_url(_submitted_openclaw_url)
                or _submitted_openclaw_url
            )
        if 'openclawTimeout' in data:
            core_cfg['openclawTimeout'] = data['openclawTimeout']
        if 'openclawDefaultSenderId' in data:
            core_cfg['openclawDefaultSenderId'] = data['openclawDefaultSenderId']
        if 'enableCustomApi' in data:
            core_cfg['enableCustomApi'] = data['enableCustomApi']
        if 'useMimoTokenPlan' in data:
            if not isinstance(data['useMimoTokenPlan'], bool):
                return {"success": False, "error": "useMimoTokenPlan must be a boolean"}
            core_cfg['useMimoTokenPlan'] = data['useMimoTokenPlan']
        if 'gptsovitsEnabled' in data:
            core_cfg['gptsovitsEnabled'] = data['gptsovitsEnabled']
        for field in (
            'ttsProvider',
        ):
            if field in data:
                core_cfg[field] = data[field]
        if 'disableTts' in data:
            if not isinstance(data['disableTts'], bool):
                return {"success": False, "error": "disableTts must be a boolean"}
            core_cfg['disableTts'] = data['disableTts']

        # Capture the stored provider and whether either Doubao TTS credential
        # was explicitly changed before applying the submitted model fields.
        # A masked value means "keep existing"; an empty string is an explicit
        # clear and must remain distinguishable from that no-op.
        stored_tts_model_provider = str(
            core_cfg.get('ttsModelProvider') or ''
        ).strip()
        doubao_tts_key_was_explicitly_updated = (
            'assistApiKeyDoubaoTts' in data
            and not is_core_config_secret_placeholder(data['assistApiKeyDoubaoTts'])
        )
        tts_model_key_was_explicitly_updated = (
            'ttsModelApiKey' in data
            and not is_core_config_secret_placeholder(data['ttsModelApiKey'])
        )

        # A retained custom credential must never follow an endpoint change.
        if any(field in data for field in (
            "imageModelProvider", "imageModelUrl", "imageModelId", "imageModelApiKey"
        )):
            from utils.image_generation.config import PROVIDERS, resolve_image_config
            if any(not isinstance(data[field], str) for field in (
                "imageModelProvider", "imageModelUrl", "imageModelId", "imageModelApiKey"
            ) if field in data):
                return {"success": False, "error": "Image settings must be strings"}
            candidate = {**core_cfg, **{
                field: data[field] for field in (
                    "imageModelProvider", "imageModelUrl", "imageModelId"
                ) if field in data
            }}
            apply_core_config_secret_update(candidate, data, "imageModelApiKey")
            stored_image_provider = core_cfg.get("imageModelProvider")
            preserve_unknown_image = (
                isinstance(stored_image_provider, str)
                and stored_image_provider not in ("", "disabled")
                and stored_image_provider not in PROVIDERS
                and all(candidate.get(field, "") == core_cfg.get(field, "") for field in (
                    "imageModelProvider", "imageModelUrl", "imageModelId", "imageModelApiKey"
                ))
            )
            # Older builds may retain a future provider, but must not create or edit it.
            if not preserve_unknown_image:
                if core_cfg.get("imageModelApiKey"):
                    old_url = core_cfg.get("imageModelUrl", "")
                    new_url = candidate.get("imageModelUrl", "")
                    from utils.http.url import same_endpoint
                    equivalent_url = (
                        isinstance(old_url, str) and isinstance(new_url, str)
                        and same_endpoint(old_url.strip(), new_url.strip())
                    )
                    if not equivalent_url and core_cfg.get("imageModelApiKey") and (
                        "imageModelApiKey" not in data
                        or is_core_config_secret_placeholder(data["imageModelApiKey"])
                    ):
                        return {"success": False, "error": "Changing image endpoint requires replacing or clearing its API key"}
                try:
                    resolve_image_config(candidate)
                except ValueError as exc:
                    return {"success": False, "error": str(exc)}

        # Custom API configuration: Provider / Url / Id / ApiKey per model type
        for mt in CORE_CONFIG_MODEL_TYPES:
            for suffix in ['Provider', 'Url', 'Id', 'ApiKey']:
                field = f'{mt}Model{suffix}'
                if field in data:
                    if suffix == 'ApiKey':
                        apply_core_config_secret_update(core_cfg, data, field)
                    else:
                        core_cfg[field] = data[field]
        # Lazy migration after retiring gptsovitsEnabled, at the save boundary, symmetric with #1842 voice_id:
        # ttsModelProvider=='gptsovits' is the sole GSV-enabled truth; old gptsovitsEnabled remains only
        # pre-#1830 compatibility fallback. Frontend loading pins enabled GSV dropdowns to 'gptsovits', so any submitted
        # other ttsModelProvider means an explicit user switch; set the leftover old flag False,
        # or get_core_config's follow_* fallback resurrects old true and even follow_assist cannot
        # disable GSV. Partial updates omitting ttsModelProvider retain the flag for legacy users who never resave.
        _incoming_tts_provider = str(data.get('ttsModelProvider', '') or '').strip()
        if _incoming_tts_provider and _incoming_tts_provider != 'gptsovits':
            core_cfg['gptsovitsEnabled'] = False
        if _incoming_tts_provider == 'doubao_tts':
            doubao_key = str(core_cfg.get('assistApiKeyDoubaoTts') or '').strip()
            if doubao_tts_key_was_explicitly_updated:
                # The dedicated Key Book field is authoritative when the user
                # explicitly edits it, including an explicit empty-string clear.
                core_cfg['ttsModelApiKey'] = doubao_key
            elif not tts_model_key_was_explicitly_updated:
                if doubao_key and not is_core_config_secret_placeholder(doubao_key):
                    core_cfg['ttsModelApiKey'] = doubao_key
                elif stored_tts_model_provider != 'doubao_tts':
                    # Switching from another provider must not reuse its shared
                    # model key as a Doubao credential. When already on Doubao,
                    # however, masked/no-op saves preserve the legacy key stored
                    # only in ttsModelApiKey.
                    core_cfg['ttsModelApiKey'] = ''
        if 'ttsVoiceId' in data:
            core_cfg['ttsVoiceId'] = data['ttsVoiceId']

        checked_resolved_urls = data.get('connectivityCheckedProviderUrls')
        if not isinstance(checked_resolved_urls, dict):
            checked_resolved_urls = {}
        save_connectivity = await _auto_resolve_provider_urls_for_save(core_cfg, checked_resolved_urls)

        # All earlier decisions, including potentially networked URL resolution, use the initial snapshot and may take time;
        # other writers such as memory switches may persist meanwhile. Do not write back the whole snapshot;
        # apply only top-level changes/deletions relative to that snapshot to a freshly read file under lock,
        # retaining latest disk values for untouched fields.
        changed_fields = {
            key: value for key, value in core_cfg.items()
            if key not in existing_core_cfg
            or not json_values_equal(existing_core_cfg[key], value)
        }
        removed_fields = [key for key in existing_core_cfg if key not in core_cfg]

        def _apply_core_config_changes(fresh_cfg):
            # Even without changed fields, update_json_config checks the write fence and rejects maintenance-mode writes.
            for key in removed_fields:
                fresh_cfg.pop(key, None)
            fresh_cfg.update(deepcopy(changed_fields))
            return deepcopy(fresh_cfg)

        core_cfg = await config_manager.aupdate_json_config(
            'core_config.json', _apply_core_config_changes
        )
        _core_api_save_lock.release()
        save_lock_held = False

        await ensure_default_yui_voice_for_free_api(config_manager, core_cfg)

        # After API updates, notify every client first, then close sessions, then reload configuration
        logger.info("API配置已更新，准备通知客户端并重置所有session...")
        
        # 1. Notify connected clients of upcoming refresh concurrently while WebSockets remain connected
        # Capture (name, mgr, session) triples so notification and end_session operate on the same managers
        # **and** the same sessions:
        # - Manager identity prevents killing new managers introduced before phase two
        # - Session identity prevents killing newly rotated sessions on existing managers between phases
        #   (frontend reload reconnects immediately and starts a new session that phase two must preserve)
        # end_session already has expected_session stale guards (core.py:3013/3026);
        # pass the captured session to activate them.
        session_manager = get_session_manager()
        mgr_snapshot = [
            (name, mgr, getattr(mgr, "session", None))
            for name, mgr in session_manager.items()
        ]
        reload_payload = json.dumps({
            "type": "reload_page",
            "message": "API配置已更新，页面即将刷新"
        })

        async def _notify(lanlan_name, mgr):
            if not (mgr.is_active and mgr.websocket):
                return False
            try:
                await mgr.websocket.send_text(reload_payload)
                logger.info(f"已通知 {lanlan_name} 的前端刷新页面")
                return True
            except Exception as e:
                logger.warning(f"通知 {lanlan_name} 的WebSocket失败: {e}")
                return False

        _notify_results = await asyncio.gather(
            *(_notify(n, m) for n, m, _session in mgr_snapshot),
            return_exceptions=True,
        )
        notification_count = sum(1 for r in _notify_results if r is True)
        logger.info(f"已通知 {notification_count} 个客户端")

        # 2. Close all active sessions concurrently: end_session takes ~1s each, N seconds serially versus ~1s with gather
        # Reuse the preceding (mgr, session) snapshot, preserving newly reconnected managers
        # and new sessions rotated on existing managers meanwhile.
        async def _end(lanlan_name, mgr, expected_session):
            if not mgr.is_active or expected_session is None:
                return None
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
                logger.info(f"{lanlan_name} 的session已结束")
                return lanlan_name
            except Exception as e:
                logger.error(f"结束 {lanlan_name} 的session时出错: {e}")
                return None

        _end_results = await asyncio.gather(
            *(_end(n, m, s) for n, m, s in mgr_snapshot),
            return_exceptions=True,
        )
        sessions_ended = [r for r in _end_results if isinstance(r, str)]
        
        # 3. Reload configuration and rebuild session managers
        logger.info("正在重新加载配置...")
        try:
            initialize_character_data = get_initialize_character_data()
            await initialize_character_data()
            logger.info("配置重新加载完成，新的API配置已生效")
        except Exception as reload_error:
            logger.error(f"重新加载配置失败: {reload_error}")
            return {"success": False, "error": f"配置已保存但重新加载失败: {str(reload_error)}"}
        
        # 4. Notify agent_server to rebuild CUA adapter with fresh config
        # per-call AsyncClient: cold path triggered only when users save API keys
        try:
            import httpx
            from config import TOOL_SERVER_PORT
            async with httpx.AsyncClient(timeout=5, proxy=None, trust_env=False) as client:
                await client.post(f"http://127.0.0.1:{TOOL_SERVER_PORT}/notify_config_changed")
            logger.info("已通知 agent_server 刷新 CUA 适配器")
        except Exception as notify_err:
            logger.warning(f"通知 agent_server 刷新 CUA 失败 (非致命): {notify_err}")

        logger.info(f"已通知 {notification_count} 个连接的客户端API配置已更新")
        return {
            "success": True,
            "message": "API Key已保存并重新加载配置",
            "sessions_ended": len(sessions_ended),
            "connectivity": save_connectivity,
            "resolvedProviderUrls": core_cfg.get('resolvedProviderUrls', {}),
        }
    except MaintenanceModeError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if save_lock_held:
            _core_api_save_lock.release()


@router.get("/api_providers")
async def get_api_providers_config():
    """Get the API provider config (for frontend use)."""
    try:
        from utils.api_config_loader import (
            get_config,
            get_core_api_providers_for_frontend,
            get_assist_api_providers_for_frontend,
            get_assist_api_profiles,
        )

        full_config = get_config(force_reload=True)
        # API settings is an admin/config surface; prefer current provider metadata
        # over stale in-process cache so label/keybook changes show up immediately.
        core_providers = get_core_api_providers_for_frontend(force_reload=True)
        assist_providers = get_assist_api_providers_for_frontend(force_reload=True)

        # TTS-provider frontend metadata has one source in utils.tts.provider_registry,
        # avoiding frontend re-hardcoding of TTS-only dropdown providers, editable endpoints, supported voice sources,
        # and connectivity-probe types (see api_key_settings.js).
        try:
            from utils.tts import provider_registry
            # Trigger worker-side registration effects; tts_client registers adapters after defining workers
            import main_logic.tts_client  # noqa: F401
            tts_providers = provider_registry.ui_metadata()
        except Exception as e:
            logger.warning(f"加载 TTS provider 元数据失败: {e}")
            tts_providers = []

        from utils.image_generation.config import PROVIDERS as image_providers

        return {
            "image_providers": image_providers,
            "success": True,
            "core_api_providers": core_providers,
            "assist_api_providers": assist_providers,
            "api_key_registry": full_config.get("api_key_registry", {}),
            "assist_api_providers_full": full_config.get("assist_api_providers", {}),
            "assist_model_defaults": {
                key: {field: profile.get(field, '') for field in (
                    'CONVERSATION_MODEL', 'VISION_MODEL', 'SUMMARY_MODEL',
                    'CORRECTION_MODEL', 'EMOTION_MODEL', 'AGENT_MODEL',
                )}
                for key, profile in get_assist_api_profiles().items()
            },
            "core_api_providers_full": full_config.get("core_api_providers", {}),
            "keybook_api_providers_full": full_config.get("keybook_api_providers", {}),
            "tts_providers": tts_providers,
        }
    except Exception as e:
        logger.error(f"获取API服务商配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "core_api_providers": [],
            "assist_api_providers": [],
        }

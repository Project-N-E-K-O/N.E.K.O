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

"""
API config loader
Loads API provider configs and default model configs from JSON files
"""
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional

from config import (
    DEFAULT_CORE_API_PROFILES,
    DEFAULT_ASSIST_API_PROFILES,
    DEFAULT_ASSIST_API_KEY_FIELDS,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# 配置缓存
_config_cache: Optional[Dict[str, Any]] = None


def _get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def _get_default_core_api_profiles() -> Dict[str, Dict[str, Any]]:
    return deepcopy(DEFAULT_CORE_API_PROFILES)


def _get_default_assist_api_profiles() -> Dict[str, Dict[str, Any]]:
    return deepcopy(DEFAULT_ASSIST_API_PROFILES)


def _get_default_assist_api_key_fields() -> Dict[str, str]:
    return deepcopy(DEFAULT_ASSIST_API_KEY_FIELDS)


def _get_config_file_path() -> Path:
    """
    Get the config file path
    
    Returns:
        Path: path to api_providers.json
    """
    return _get_app_root() / "config" / "api_providers.json"


def _load_json_config() -> Dict[str, Any]:
    """
    Load the JSON config file
    
    Returns:
        Dict: config dict
        
    Raises:
        FileNotFoundError: config file does not exist
        json.JSONDecodeError: malformed JSON
    """
    config_path = _get_config_file_path()
    
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"成功加载配置文件: {config_path}")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"JSON格式错误: {config_path}, 错误: {e}")
        raise
    except Exception as e:
        logger.error(f"加载配置文件失败: {config_path}, 错误: {e}")
        raise


def _convert_core_api_profile(json_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert core API config from JSON format to the format used by Python code
    
    Args:
        json_profile: config in JSON format
        
    Returns:
        Dict: format used by Python code (uppercase field names)
    """
    result = {}
    
    # 转换字段名：snake_case -> UPPER_SNAKE_CASE
    field_mapping = {
        'core_url': 'CORE_URL',
        'core_urls': 'CORE_URLS',
        'core_model': 'CORE_MODEL',
        'core_api_key': 'CORE_API_KEY',
    }
    
    for json_key, python_key in field_mapping.items():
        if json_key in json_profile:
            result[python_key] = json_profile[json_key]
    
    return result


def _convert_assist_api_profile(json_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert assist API config from JSON format to the format used by Python code
    
    Args:
        json_profile: config in JSON format
        
    Returns:
        Dict: format used by Python code (uppercase field names)
    """
    result = {}
    
    # 转换字段名：snake_case -> UPPER_SNAKE_CASE
    field_mapping = {
        'openrouter_url': 'OPENROUTER_URL',
        'openrouter_urls': 'OPENROUTER_URLS',
        'token_plan_openrouter_url': 'MIMO_TOKEN_PLAN_OPENROUTER_URL',
        'token_plan_openrouter_urls': 'MIMO_TOKEN_PLAN_OPENROUTER_URLS',
        'conversation_model': 'CONVERSATION_MODEL',
        'summary_model': 'SUMMARY_MODEL',
        'correction_model': 'CORRECTION_MODEL',
        'emotion_model': 'EMOTION_MODEL',
        'vision_model': 'VISION_MODEL',
        'agent_model': 'AGENT_MODEL',
        'audio_api_key': 'AUDIO_API_KEY',
        'openrouter_api_key': 'OPENROUTER_API_KEY',
        'provider_type': 'PROVIDER_TYPE',
    }
    
    for json_key, python_key in field_mapping.items():
        if json_key in json_profile:
            result[python_key] = json_profile[json_key]
    
    return result


def get_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    Get the config (cached)
    
    Args:
        force_reload: whether to force a reload
        
    Returns:
        Dict: config dict
    """
    global _config_cache
    
    if _config_cache is None or force_reload:
        try:
            _config_cache = _load_json_config()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"加载配置失败，使用空配置: {e}")
            _config_cache = {}
    
    return _config_cache


def get_core_api_profiles(force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Get core API configs (compatible with the legacy CORE_API_PROFILES format)
    
    Args:
        force_reload: whether to force-reload the config
    
    Returns:
        Dict: core API config dict, same format as CORE_API_PROFILES
    """
    config = get_config(force_reload=force_reload)
    core_providers = config.get('core_api_providers', {})
    
    result = {}
    for key, profile in core_providers.items():
        # 转换为Python代码使用的格式
        result[key] = _convert_core_api_profile(profile)
    
    if not result:
        return _get_default_core_api_profiles()
    
    return result


def get_assist_api_profiles(force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Get assist API configs (compatible with the legacy ASSIST_API_PROFILES format)
    
    Args:
        force_reload: whether to force-reload the config
    
    Returns:
        Dict: assist API config dict, same format as ASSIST_API_PROFILES
    """
    # 首先获取默认配置作为基础
    defaults = _get_default_assist_api_profiles()
    
    config = get_config(force_reload=force_reload)
    assist_providers = config.get('assist_api_providers', {})
    
    if not assist_providers:
        return defaults
    
    result = {}
    for key, profile in assist_providers.items():
        # 转换为Python代码使用的格式
        converted = _convert_assist_api_profile(profile)
        
        # 与默认配置合并：默认配置作为基础，JSON配置覆盖
        if key in defaults:
            merged = dict(defaults[key])  # 复制默认配置
            merged.update(converted)  # JSON 配置覆盖
            result[key] = merged
        else:
            result[key] = converted
    
    # 添加默认配置中有但 JSON 中没有的 provider
    for key in defaults:
        if key not in result:
            result[key] = defaults[key]
    
    return result


def get_assist_api_key_fields() -> Dict[str, str]:
    """
    Get the assist API key field mapping (compatible with the legacy ASSIST_API_KEY_FIELDS format)
    
    Returns:
        Dict: API key field mapping dict
    """
    config = get_config()
    result = config.get('assist_api_key_fields', {})
    if not result:
        return _get_default_assist_api_key_fields()
    return result


def get_default_models() -> Dict[str, str]:
    """
    Get default model configs
    
    Returns:
        Dict: default model config dict
    """
    config = get_config()
    return config.get('default_models', {})


def get_core_api_providers_for_frontend(force_reload: bool = False) -> list:
    """
    Get the list of core API providers (for the frontend)
    
    Args:
        force_reload: whether to force-reload the config
    
    Returns:
        list: provider info list; each element contains key, name, description
    """
    config = get_config(force_reload=force_reload)
    core_providers = config.get('core_api_providers', {})
    
    result = []
    for key, profile in core_providers.items():
        result.append({
            'key': profile.get('key', key),
            'name': profile.get('name', key),
            'description': profile.get('description', ''),
        })
    
    return result


def get_assist_api_providers_for_frontend(force_reload: bool = False) -> list:
    """
    Get the list of assist API providers (for the frontend)
    
    Args:
        force_reload: whether to force-reload the config
    
    Returns:
        list: provider info list; each element contains key, name, description
    """
    config = get_config(force_reload=force_reload)
    assist_providers = config.get('assist_api_providers', {})
    
    result = []
    for key, profile in assist_providers.items():
        result.append({
            'key': profile.get('key', key),
            'name': profile.get('name', key),
            'description': profile.get('description', ''),
        })
    
    return result


def reload_config():
    """
    Reload the config (clear the cache)
    """
    global _config_cache
    _config_cache = None
    logger.info("配置缓存已清除，下次访问时将重新加载")

def get_free_voices() -> Dict[str, str]:
    """
    Get the list of free preset voices (read from the free_voices field of api_providers.json)
    
    Returns:
        Dict[str, str]: {voiceKey: voice_id} mapping; voiceKey is localized by the frontend
    """
    config = get_config()
    return config.get('free_voices', {})


def _normalize_str_dict(raw: Any) -> Dict[str, str]:
    """Normalize a dict from config to str -> str, filtering out empty keys."""
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key or '').strip()
        if normalized_key:
            result[normalized_key] = str(value or '').strip()
    return result


def _resolve_native_tts_voice_provider_config(
    provider_key: str,
    raw_configs: Dict[str, Any],
    resolving: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Parse native TTS voice provider configs; supports inherits for catalog reuse."""
    key = str(provider_key or '').strip()
    if not key:
        return {}
    raw = raw_configs.get(key)
    if not isinstance(raw, dict):
        return {}

    resolving = set(resolving or set())
    if key in resolving:
        logger.warning(f"原生 TTS 音色配置存在循环继承，已跳过: {key}")
        return {}
    resolving.add(key)

    inherited: Dict[str, Any] = {}
    inherit_key = str(raw.get('inherits') or '').strip()
    if inherit_key:
        inherited = _resolve_native_tts_voice_provider_config(
            inherit_key,
            raw_configs,
            resolving,
        )

    # voices / aliases 走 dict 深合并：继承父目录后只需在子配置里增量声明
    # 新增/覆盖的条目（如 free_intl 在 gemini 全量目录上只加一个 yui），
    # 不必把父目录整张重抄一遍。其余标量字段（catalog_prefix / default_voice
    # 等）仍是子覆盖父的整体替换语义。
    _MERGE_DICT_FIELDS = ('voices', 'aliases')
    merged = deepcopy(inherited)
    for field, value in raw.items():
        if field == 'inherits':
            continue
        if (
            field in _MERGE_DICT_FIELDS
            and isinstance(value, dict)
            and isinstance(merged.get(field), dict)
        ):
            merged[field] = {**merged[field], **deepcopy(value)}
        else:
            merged[field] = deepcopy(value)
    return merged


def get_native_tts_voice_provider_config(provider_key: str) -> Dict[str, Any]:
    """Get a single native TTS voice provider config."""
    raw_configs = get_config().get('native_tts_voice_providers', {})
    if not isinstance(raw_configs, dict):
        return {}
    resolved = _resolve_native_tts_voice_provider_config(provider_key, raw_configs)
    if not resolved:
        return {}

    voices = _normalize_str_dict(resolved.get('voices'))
    aliases = _normalize_str_dict(resolved.get('aliases'))
    default_voice = str(resolved.get('default_voice') or '').strip()
    default_male_voice = str(resolved.get('default_male_voice') or '').strip()
    if not default_voice and voices:
        default_voice = next(iter(voices))
    if not default_male_voice:
        default_male_voice = default_voice

    return {
        'key': str(provider_key or '').strip(),
        'catalog_prefix': str(resolved.get('catalog_prefix') or provider_key or '').strip(),
        'default_voice': default_voice,
        'default_male_voice': default_male_voice,
        'catalog_value_is_display_name': bool(resolved.get('catalog_value_is_display_name', False)),
        'voices': voices,
        'aliases': aliases,
    }


def get_native_tts_voice_provider_configs() -> Dict[str, Dict[str, Any]]:
    """Get all native TTS voice provider configs."""
    raw_configs = get_config().get('native_tts_voice_providers', {})
    if not isinstance(raw_configs, dict):
        return {}
    return {
        str(provider_key): get_native_tts_voice_provider_config(str(provider_key))
        for provider_key in raw_configs
    }


_COSYVOICE_CLONE_MODEL_DEFAULT = "cosyvoice-v3.5-plus"
_COSYVOICE_INTL_CLONE_MODEL_DEFAULT = "cosyvoice-v3-plus"


def get_cosyvoice_clone_model(provider: str | None = None) -> str:
    """Get the model name used for CosyVoice cloning/synthesis.

    The CN edition reads api_providers.json → default_models.cosyvoice_clone_model,
    the international edition reads default_models.cosyvoice_intl_clone_model. Alibaba's
    international deployment does not support ``cosyvoice-v3.5-plus``; a v3-series
    model available in international regions must be used.
    """
    normalized_provider = str(provider or '').strip().lower()
    intl_aliases = {
        'cosyvoice_intl',
        'qwen_intl',
        'qwen_us',
        'intl',
        'international',
        'us',
        'usa',
        'united_states',
        'dashscope_us',
        'dashscope-us',
    }
    if (
        normalized_provider in intl_aliases
        or "dashscope-intl.aliyuncs.com" in normalized_provider
        or "dashscope-us.aliyuncs.com" in normalized_provider
    ):
        return (
            get_default_models().get('cosyvoice_intl_clone_model')
            or _COSYVOICE_INTL_CLONE_MODEL_DEFAULT
        )

    return (
        get_default_models().get('cosyvoice_clone_model')
        or _COSYVOICE_CLONE_MODEL_DEFAULT
    )


def cosyvoice_model_supports_language_hints(model: str | None) -> bool:
    """language_hints only applies to v3 / v3.5 series models; v2 does not support it."""
    return not str(model or _COSYVOICE_CLONE_MODEL_DEFAULT).startswith("cosyvoice-v2")


def _get_livestream_config_path() -> Path:
    """Path of the standalone livestream config file.

    Takes precedence over the livestream_config field in api_providers.json, making
    it easy to distribute to streamers as a single-file patch — drop this json into
    the config directory and it takes effect, without touching the tracked
    api_providers.json. The file is covered by the default config/*.json
    .gitignore rule and never enters git.
    """
    return _get_app_root() / "config" / "livestream_config.json"


def _get_meme_moderation_config_path() -> Path:
    """Path of the standalone meme moderation config file.

    Takes precedence over the meme_moderation_config field in api_providers.json,
    matching the livestream_config override pattern. The file is covered by the
    default config/*.json .gitignore rule and never enters git.
    """
    return _get_app_root() / "config" / "meme_moderation_config.json"


def get_meme_moderation_config() -> Dict[str, Any]:
    """Read the meme moderation config (standalone file first, api_providers.json field as fallback).

    Priority:
    1. ``config/meme_moderation_config.json`` (untracked local/private config)
    2. the ``meme_moderation_config`` field of ``config/api_providers.json`` (compatibility path)

    Returns:
        Dict: {'api_key': str, 'base_url': str, 'model': str}
        Falls back to defaults (empty string) when missing/unreadable/fields absent.
    """
    raw: Optional[Dict[str, Any]] = None
    standalone_path = _get_meme_moderation_config_path()
    if standalone_path.is_file():
        try:
            with open(standalone_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # 兼容两种 shape：flat（顶层就是 api_key）
                # 与 wrapped（顶层 'meme_moderation_config' 包一层，跟 api_providers.json
                # 同构）。私有打包复用 api_providers.json 结构是常见操作，不强求扁平。
                inner = loaded.get('meme_moderation_config')
                raw = inner if isinstance(inner, dict) else loaded
        except Exception as e:
            logger.warning(
                f"读取 {standalone_path.name} 失败，回退到 api_providers.json: {e}"
            )
    if raw is None:
        fallback_raw = get_config().get('meme_moderation_config')
        raw = fallback_raw if isinstance(fallback_raw, dict) else {}
    return {
        'api_key': str(raw.get('api_key', '') or '').strip(),
        'base_url': str(raw.get('base_url', '') or raw.get('url', '') or '').strip(),
        'model': str(raw.get('model', '') or raw.get('model_name', '') or '').strip(),
    }


def get_livestream_config() -> Dict[str, Any]:
    """Read the livestream config (standalone file first, api_providers.json field as fallback).

    Livestream mode is a sub-mode layered on top of core_api_type='free'. When enabled:
    - on the free path, all lanlan.tech URLs are rewritten to server_prefix-derived addresses (/core /text/v1 /tts)
    - on the free path, voice is forced to voice_id (bypassing the free_voices preset gate)
    - OmniRealtimeClient skips the 90-second silence mic-off check

    Priority:
    1. ``config/livestream_config.json`` (untracked, single-file patch for streamer distribution)
    2. the ``livestream_config`` field of ``config/api_providers.json`` (compatibility path)

    Returns:
        Dict: {'enabled': bool, 'server_prefix': str, 'voice_id': str}
        Falls back to defaults (False / empty string) when missing/unreadable/fields absent.
    """
    raw: Optional[Dict[str, Any]] = None
    standalone_path = _get_livestream_config_path()
    if standalone_path.is_file():
        try:
            with open(standalone_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # 兼容两种 shape：flat（顶层就是 enabled/server_prefix/voice_id）
                # 与 wrapped（顶层 'livestream_config' 包一层，跟 api_providers.json
                # 同构）。主播复用 api_providers.json 结构是常见操作，不强求扁平。
                inner = loaded.get('livestream_config')
                raw = inner if isinstance(inner, dict) else loaded
        except Exception as e:
            logger.warning(
                f"读取 {standalone_path.name} 失败，回退到 api_providers.json: {e}"
            )
    if raw is None:
        raw = get_config().get('livestream_config') or {}
    return {
        'enabled': bool(raw.get('enabled', False)),
        'server_prefix': str(raw.get('server_prefix', '') or '').strip(),
        'voice_id': str(raw.get('voice_id', '') or '').strip(),
    }


def is_livestream_active() -> bool:
    """Livestream only actually takes effect when enabled=True and server_prefix is non-empty.

    voice_id is not required (when absent, the free path keeps the original voice resolution).
    """
    cfg = get_livestream_config()
    return cfg['enabled'] and bool(cfg['server_prefix'])


# 导出主要函数
__all__ = [
    'get_core_api_profiles',
    'get_assist_api_profiles',
    'get_assist_api_key_fields',
    'get_default_models',
    'get_core_api_providers_for_frontend',
    'get_assist_api_providers_for_frontend',
    'reload_config',
    'get_config',
    'get_free_voices',
    'get_native_tts_voice_provider_config',
    'get_native_tts_voice_provider_configs',
    'get_cosyvoice_clone_model',
    'cosyvoice_model_supports_language_hints',
    'get_meme_moderation_config',
    'get_livestream_config',
    'is_livestream_active',
]

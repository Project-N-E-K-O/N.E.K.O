# -*- coding: utf-8 -*-
"""Custom TTS compatibility adapter helpers.

This module hosts provider-specific custom TTS voice-id allowance checks,
so shared config modules can delegate provider details here.
"""

import re
from typing import Callable, Optional

from config import GSV_VOICE_PREFIX

LOCAL_LIGHTWEIGHT_TTS_PREFIXES = ("kokoro:", "melotts:", "melo:", "chattts:")
LOCAL_LIGHTWEIGHT_BARE_VOICE_RE = re.compile(r"^(?:zf|zm|zh|af|am|bf|bm)_[A-Za-z0-9_-]+$", re.IGNORECASE)


def _is_custom_ws_tts(get_model_api_config: Callable[[str], dict]) -> bool:
    tts_config = get_model_api_config('tts_custom')
    base_url = (tts_config.get('base_url') or '').strip().lower()
    is_custom = tts_config.get('is_custom', False)
    return bool(is_custom and base_url.startswith(('ws://', 'wss://')))


def _looks_like_local_lightweight_voice_id(value: str) -> bool:
    normalized = (value or '').strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered.startswith(LOCAL_LIGHTWEIGHT_TTS_PREFIXES):
        return True
    return bool(LOCAL_LIGHTWEIGHT_BARE_VOICE_RE.match(normalized))


def check_custom_tts_voice_allowed(
    voice_id: str,
    get_model_api_config: Callable[[str], dict],
) -> Optional[bool]:
    """Return allowance decision for provider-specific custom TTS voice IDs.

    Returns:
        - True / False when the voice_id is recognized by this adapter.
        - None when this adapter does not handle the given voice_id.
    """
    normalized_voice_id = (voice_id or '').strip()
    lowered_voice_id = normalized_voice_id.lower()
    if _looks_like_local_lightweight_voice_id(normalized_voice_id):
        if lowered_voice_id.startswith(LOCAL_LIGHTWEIGHT_TTS_PREFIXES):
            suffix = normalized_voice_id.split(":", 1)[1].strip()
            if not suffix:
                return False
        return _is_custom_ws_tts(get_model_api_config)

    if not lowered_voice_id.startswith(GSV_VOICE_PREFIX.lower()):
        return None

    suffix = normalized_voice_id[len(GSV_VOICE_PREFIX):].strip()
    if not suffix:
        return False

    # gsv: 前缀的 voice_id 仅在 GPT-SoVITS 开关启用且 endpoint 为 HTTP 时有效。
    # ws:// 走本地轻量 TTS 路由，冒号属于 provider-prefixed voice_id（如 kokoro:/chattts:）。
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    gptsovits_enabled = cm.get_core_config().get('GPTSOVITS_ENABLED', False)
    if not gptsovits_enabled:
        return False
    tts_config = get_model_api_config('tts_custom')
    base_url = (tts_config.get('base_url') or '').strip().lower()
    is_custom = tts_config.get('is_custom', False)
    return bool(is_custom and base_url.startswith(('http://', 'https://')))

# -*- coding: utf-8 -*-
"""Custom TTS compatibility adapter helpers.

This module hosts provider-specific custom TTS voice-id allowance checks,
so shared config modules can delegate provider details here.
"""

from typing import Callable, Optional

from config import GSV_VOICE_PREFIX


def check_custom_tts_voice_allowed(
    voice_id: str,
    get_model_api_config: Callable[[str], dict],
) -> Optional[bool]:
    """Return allowance decision for provider-specific custom TTS voice IDs.

    Returns:
        - True / False when the voice_id is recognized by this adapter.
        - None when this adapter does not handle the given voice_id.
    """
    if not voice_id.startswith(GSV_VOICE_PREFIX):
        return None

    suffix = voice_id[len(GSV_VOICE_PREFIX):].strip()
    if not suffix:
        return False

    # gsv: 前缀的 voice_id 仅在用户显式启用 GPT-SoVITS 开关时有效
    from utils.config_manager import get_config_manager
    core_cfg = get_config_manager().get_core_config()
    return bool(core_cfg.get('gptsovitsEnabled', False))

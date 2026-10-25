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

    # A gsv:-prefixed voice_id is valid only with GPT-SoVITS enabled and an HTTP endpoint;
    # ws:// (local CosyVoice) uses `:` as a speed separator and cannot accept the gsv: prefix.
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    gptsovits_enabled = cm.get_core_config().get('GPTSOVITS_ENABLED', False)
    if not gptsovits_enabled:
        return False
    tts_config = get_model_api_config('tts_custom')
    base_url = tts_config.get('base_url') or ''
    is_custom = tts_config.get('is_custom', False)
    return bool(is_custom and base_url.startswith(('http://', 'https://')))

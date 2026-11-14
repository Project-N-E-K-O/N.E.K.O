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

"""DeepSeek's hosted TTS endpoint and built-in voice catalog."""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts.provider_registry import PresetCatalog

DEEPSEEK_TTS_URL = "wss://api.deepseek.com/v1/tools/tts"
_FALLBACK_DEFAULT_VOICE = "stella"
_FALLBACK_DEFAULT_MALE_VOICE = "tide"
_FALLBACK_VOICES = {_FALLBACK_DEFAULT_VOICE: "Female", _FALLBACK_DEFAULT_MALE_VOICE: "Male"}

_config = get_native_tts_voice_provider_config("deepseek")
_default_voice = _config.get("default_voice") or _FALLBACK_DEFAULT_VOICE
_default_male_voice = _config.get("default_male_voice") or _FALLBACK_DEFAULT_MALE_VOICE
_aliases = _config.get("aliases") or {
    "female": _default_voice,
    "女": _default_voice,
    "女声": _default_voice,
    "male": _default_male_voice,
    "男": _default_male_voice,
    "男声": _default_male_voice,
}

DEEPSEEK_PRESET_CATALOG = PresetCatalog(
    catalog=_config.get("voices") or _FALLBACK_VOICES,
    aliases={alias.casefold(): voice for alias, voice in _aliases.items() if alias and voice},
    default_voice=_default_voice,
    catalog_prefix=_config.get("catalog_prefix") or "DeepSeek",
)


def normalize_deepseek_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    return DEEPSEEK_PRESET_CATALOG.normalize(voice_id)

"""Xiaomi MiMo-V2.5-TTS built-in voice catalog adapter.

MiMo's built-in TTS model is exposed through OpenAI-compatible
chat-completions with an ``audio.voice`` field. The canonical voice IDs are
published in the MiMo-V2.5-TTS speech synthesis guide.
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts_provider_registry import PresetCatalog

MIMO_TTS_MODEL = "mimo-v2.5-tts"
# MiMo 的声音克隆模型。当前仅声明为常量占位：MiMo 在 tts_provider_registry 里以
# capabilities={preset, clone} 注册，但克隆 enrollment 流程（上传样本 → 调 MiMo 克隆
# API → 落 voice_id，对偶 cosyvoice/elevenlabs 的 voice_clone 流程）尚未实现，留待后续
# 接手（见 voice-source-unification 设计文档 / 交接 chip）。
MIMO_TTS_VOICECLONE_MODEL = "mimo-v2.5-tts-voiceclone"  # TODO: 接 MiMo 克隆 enrollment
MIMO_TTS_DEFAULT_VOICE = "mimo_default"
MIMO_TTS_BASE_URL = "https://api.xiaomimimo.com/v1"

_FALLBACK_MIMO_TTS_VOICES: dict[str, str] = {
    "mimo_default": "Default",
    "冰糖": "Female",
    "茉莉": "Female",
    "苏打": "Male",
    "白桦": "Male",
    "Mia": "Female",
    "Chloe": "Female",
    "Milo": "Male",
    "Dean": "Male",
}

_FALLBACK_MIMO_TTS_ALIASES: dict[str, str] = {
    "default": MIMO_TTS_DEFAULT_VOICE,
    "默认": MIMO_TTS_DEFAULT_VOICE,
    "female": "冰糖",
    "woman": "冰糖",
    "女": "冰糖",
    "女声": "冰糖",
    "chinese female": "冰糖",
    "中文女": "冰糖",
    "male": "苏打",
    "man": "苏打",
    "男": "苏打",
    "男声": "苏打",
    "chinese male": "苏打",
    "中文男": "苏打",
    "english female": "Mia",
    "英文女": "Mia",
    "english male": "Milo",
    "英文男": "Milo",
}


def _load_provider_config() -> dict:
    return get_native_tts_voice_provider_config("mimo")


_CFG = _load_provider_config()

MIMO_TTS_VOICE_GENDERS: dict[str, str] = (
    _CFG.get("voices") or _FALLBACK_MIMO_TTS_VOICES
)


def _build_aliases(configured: dict[str, str]) -> dict[str, str]:
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _create_preset_catalog() -> PresetCatalog:
    aliases_source = _CFG.get("aliases") or _FALLBACK_MIMO_TTS_ALIASES
    return PresetCatalog(
        catalog=MIMO_TTS_VOICE_GENDERS,
        aliases=_build_aliases(aliases_source),
        default_voice=_CFG.get("default_voice") or MIMO_TTS_DEFAULT_VOICE,
        catalog_prefix=_CFG.get("catalog_prefix") or "MiMo",
        catalog_value_is_display_name=bool(
            _CFG.get("catalog_value_is_display_name", False)
        ),
    )


# MiMo is a hosted SaaS provider (see design doc §4), so its built-in voice
# catalog lives on the unified tts_provider_registry.TTSProvider as a
# preset_catalog, not on native_voice_registry. The TTSProvider entry (in
# main_logic.tts_client) wires this catalog in via ``preset_catalog=``.
MIMO_PRESET_CATALOG = _create_preset_catalog()


def normalize_mimo_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    return MIMO_PRESET_CATALOG.normalize(voice_id)

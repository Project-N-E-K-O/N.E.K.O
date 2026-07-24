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

"""Xiaomi MiMo-V2.5-TTS built-in voice catalog adapter.

MiMo's built-in TTS model is exposed through OpenAI-compatible
chat-completions with an ``audio.voice`` field. The canonical voice IDs are
published in the MiMo-V2.5-TTS speech synthesis guide.
"""

import base64
import uuid
from urllib.parse import urlparse, urlunparse

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts.provider_registry import PresetCatalog

MIMO_TTS_MODEL = "mimo-v2.5-tts"
# MiMo 的声音克隆模型。与 preset 不同，MiMo 克隆没有「注册 → 拿远端 voice_id」这一步：
# 参考音频每次合成请求内联在 ``audio.voice`` 里（``data:audio/...;base64,...``），克隆身份
# 即是这段本地保存的样本。enrollment（保存样本 + voice_meta provider=mimo/source=clone）在
# characters_router 的 /voice_clone mimo 分支，对偶 cosyvoice/minimax 的云端克隆流程；
# dispatch 由 tts_provider_registry 的 mimo provider 按 voice_meta 选中（见设计文档 §4/§7）。
MIMO_TTS_VOICECLONE_MODEL = "mimo-v2.5-tts-voiceclone"
MIMO_TTS_VOICEDESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
MIMO_TTS_DEFAULT_VOICE = "mimo_default"
MIMO_TTS_BASE_URL = "https://api.xiaomimimo.com/v1"
# Voice-storage bucket marker shared by MiMo Clone and Voice Design enrollment.
MIMO_VOICE_STORAGE_KEY = '__MIMO__'


def new_mimo_design_voice_id() -> str:
    """Create an opaque local ID for a MiMo designed voice.

    MiMo has no remote enrolled voice ID and documents no prefix character or
    length restriction. The user-provided prefix stays in metadata for UI
    display, while an opaque ID avoids leaking MiniMax constraints into MiMo.
    """
    return f"mimo-design-{uuid.uuid4().hex}"


def mimo_chat_completions_url(base_url: str | None = None) -> str:
    """Normalize a MiMo API base URL to its chat-completions endpoint.

    Canonical helper shared by the TTS worker and the voice-clone enrollment
    client so the endpoint-derivation rule lives in one place (utils layer).
    ``ws(s)://`` is coerced to ``https://``; a bare host gets ``https://``.

    Note ``ws://`` maps to ``https://`` (not plaintext ``http://``): MiMo is an
    HTTP chat-completions API (``ws`` was never a meaningful scheme for it), and
    requests carry the ``api-key`` header — downgrading to plaintext would leak
    the credential. An explicit ``http://`` the caller configured is left as-is
    (local self-hosted proxies); a project-wide "reject remote plaintext for any
    provider base_url" hardening is tracked separately.
    """
    raw_url = (base_url or MIMO_TTS_BASE_URL).strip().rstrip("/")
    if raw_url.startswith("wss://"):
        raw_url = "https://" + raw_url[6:]
    elif raw_url.startswith("ws://"):
        raw_url = "https://" + raw_url[5:]
    elif not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise ValueError(f"无效的 MiMo base_url: {base_url!r}")

    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    elif not path or path == "/":
        endpoint_path = "/v1/chat/completions"
    elif path.endswith("/v1"):
        endpoint_path = f"{path}/chat/completions"
    else:
        endpoint_path = f"{path}/v1/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))


def mimo_voice_clone_data_uri(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Build the ``data:<mime>;base64,<...>`` URI MiMo expects in ``audio.voice``
    for the ``mimo-v2.5-tts-voiceclone`` model (reference audio is inlined per
    synthesis request — MiMo has no server-side cloned voice id)."""
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    # 先 strip 再回退默认：避免 mime_type="   "（全空白）被 `or` 当真值留下、strip 成空串
    # 生成非法的 ``data:;base64,...``。
    safe_mime = (mime_type or "").strip() or "audio/wav"
    return f"data:{safe_mime};base64,{b64}"

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
    default_voice = _CFG.get("default_voice") or MIMO_TTS_DEFAULT_VOICE
    aliases_source = _CFG.get("aliases") or {
        **_FALLBACK_MIMO_TTS_ALIASES,
        "default": default_voice,
        "默认": default_voice,
    }
    return PresetCatalog(
        catalog=MIMO_TTS_VOICE_GENDERS,
        aliases=_build_aliases(aliases_source),
        default_voice=default_voice,
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

"""结构化 voice config 数据模型 + 无歧义前缀 normalizer 单测（声音来源统一架构）。"""

from config import GSV_VOICE_PREFIX
from utils.elevenlabs_tts_voices import ELEVENLABS_TTS_VOICE_PREFIX
from utils.gptsovits_config import GSV_DISABLED_VOICE_PREFIX
from utils.voice_config import (
    SOURCE_CLONE,
    VoiceConfig,
    parse_legacy_voice_id,
)


def test_to_dict_omits_empty_config():
    vc = VoiceConfig(source="preset", provider="gemini", ref="alloy")
    assert vc.to_dict() == {"source": "preset", "provider": "gemini", "ref": "alloy"}


def test_to_dict_keeps_config():
    vc = VoiceConfig(source="preset", provider="vllm_omni", ref="default", config={"url": "http://x"})
    assert vc.to_dict() == {
        "source": "preset",
        "provider": "vllm_omni",
        "ref": "default",
        "config": {"url": "http://x"},
    }


def test_is_empty():
    assert VoiceConfig().is_empty() is True
    assert VoiceConfig(ref="x").is_empty() is False


def test_from_any_roundtrip_object():
    vc = VoiceConfig(source="clone", provider="cosyvoice", ref="abc")
    assert VoiceConfig.from_any(vc) is vc
    assert VoiceConfig.from_any(vc.to_dict()) == vc


def test_from_any_bare_string_carries_ref():
    # bare id (no prefix) → ref only; provider/source left for context normalizer
    vc = VoiceConfig.from_any("voice-tone-PGLiTXeJCS")
    assert vc.ref == "voice-tone-PGLiTXeJCS"
    assert vc.provider == ""
    assert vc.source == ""


def test_parse_elevenlabs_prefix():
    vc = parse_legacy_voice_id(f"{ELEVENLABS_TTS_VOICE_PREFIX}voiceXYZ")
    assert vc == VoiceConfig(source=SOURCE_CLONE, provider="elevenlabs", ref="voiceXYZ")


def test_parse_gptsovits_prefix():
    vc = parse_legacy_voice_id(f"{GSV_VOICE_PREFIX}my_voice")
    assert vc == VoiceConfig(source=SOURCE_CLONE, provider="gptsovits", ref="my_voice")


def test_parse_disabled_placeholder_is_empty():
    # 退役的 __gptsovits_disabled__| 占位符不是一个活跃音色 → 归一成空
    vc = parse_legacy_voice_id(f"{GSV_DISABLED_VOICE_PREFIX}http://127.0.0.1:9881|my_voice")
    assert vc is not None
    assert vc.is_empty()


def test_parse_empty_is_empty():
    assert parse_legacy_voice_id("").is_empty()
    assert parse_legacy_voice_id(None).is_empty()


def test_parse_bare_id_returns_none():
    # 裸 id 无法在无上下文时定 provider/source
    assert parse_legacy_voice_id("voice-tone-PGLiTXeJCS") is None
    assert parse_legacy_voice_id("alloy") is None

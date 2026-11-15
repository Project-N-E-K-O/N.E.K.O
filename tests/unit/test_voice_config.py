"""Unit tests for the structured voice config model + unambiguous-prefix normalizer."""

from config import GSV_VOICE_PREFIX
from utils.elevenlabs_tts_voices import ELEVENLABS_TTS_VOICE_PREFIX
from utils.gptsovits_config import GSV_DISABLED_VOICE_PREFIX
from utils.voice_config import (
    SOURCE_CLONE,
    SOURCE_PRESET,
    VoiceConfig,
    normalize_voice_id,
    parse_legacy_voice_id,
    read_legacy_voice_id,
    to_legacy_voice_id,
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
    # Retired __gptsovits_disabled__| placeholders are not active voices; normalize to empty.
    vc = parse_legacy_voice_id(f"{GSV_DISABLED_VOICE_PREFIX}http://127.0.0.1:9881|my_voice")
    assert vc is not None
    assert vc.is_empty()


def test_parse_empty_is_empty():
    assert parse_legacy_voice_id("").is_empty()
    assert parse_legacy_voice_id(None).is_empty()


def test_parse_bare_id_returns_none():
    # Bare IDs cannot determine provider/source without context.
    assert parse_legacy_voice_id("voice-tone-PGLiTXeJCS") is None
    assert parse_legacy_voice_id("alloy") is None


# ── normalize_voice_id (pure, injected context) ──────────────────────────────

def test_normalize_prefix_short_circuits_context():
    # Parse prefixed IDs directly without consulting injected context.
    vc = normalize_voice_id(
        "eleven:abc",
        vllm_selected=True,  # Even selected vllm must not cause misclassification: prefixes take precedence.
        clone_provider_lookup=lambda r: "cosyvoice",
    )
    assert vc == VoiceConfig(source=SOURCE_CLONE, provider="elevenlabs", ref="abc")


def test_normalize_vllm_selected_bare_id():
    vc = normalize_voice_id("Ethan", vllm_selected=True)
    assert vc == VoiceConfig(source=SOURCE_PRESET, provider="vllm_omni", ref="Ethan")


def test_normalize_bare_clone_voice():
    vc = normalize_voice_id(
        "cosyvoice-clone-123",
        clone_provider_lookup=lambda r: "cosyvoice_intl" if r == "cosyvoice-clone-123" else None,
    )
    assert vc == VoiceConfig(source=SOURCE_CLONE, provider="cosyvoice_intl", ref="cosyvoice-clone-123")


def test_normalize_preferred_configured_preset_before_same_id_clone():
    vc = normalize_voice_id(
        "vendor-voice",
        preferred_preset_provider=lambda ref: "custom" if ref == "vendor-voice" else None,
        clone_provider_lookup=lambda _ref: "cosyvoice",
    )

    assert vc == VoiceConfig(source=SOURCE_PRESET, provider="custom", ref="vendor-voice")


def test_normalize_native_preset():
    vc = normalize_voice_id(
        "qingchunshaonv",
        is_native=lambda r: True,
        native_provider="step",
    )
    assert vc == VoiceConfig(source=SOURCE_PRESET, provider="step", ref="qingchunshaonv")


def test_normalize_free_preset():
    vc = normalize_voice_id(
        "voice-tone-PGLiTXeJCS",
        free_voice_ids={"voice-tone-PGLiTXeJCS"},
    )
    assert vc == VoiceConfig(source=SOURCE_PRESET, provider="free", ref="voice-tone-PGLiTXeJCS")


def test_normalize_hosted_preset_marks_selected_provider():
    # Presets from the selected hosted/local provider (e.g. MiMo Milo) -> source=preset/provider=<key>.
    vc = normalize_voice_id(
        "Milo",
        hosted_preset_provider=lambda r: "mimo" if r == "Milo" else None,
    )
    assert vc == VoiceConfig(source=SOURCE_PRESET, provider="mimo", ref="Milo")


def test_normalize_hosted_preset_after_native_before_free():
    # Match validate_voice_id order: native before hosted presets, hosted presets before free.
    native_first = normalize_voice_id(
        "Milo",
        is_native=lambda r: True,
        native_provider="step",
        hosted_preset_provider=lambda r: "mimo",
    )
    assert native_first.provider == "step"
    hosted_before_free = normalize_voice_id(
        "Milo",
        hosted_preset_provider=lambda r: "mimo",
        free_voice_ids={"Milo"},
    )
    assert hosted_before_free.provider == "mimo"


def test_normalize_hosted_preset_miss_carries_ref():
    # None lookup (provider not selected / not a preset): preserve the bare ref without mislabeling.
    vc = normalize_voice_id("Milo", hosted_preset_provider=lambda r: None)
    assert vc == VoiceConfig(ref="Milo")


def test_normalize_unresolved_carries_ref():
    vc = normalize_voice_id("totally-unknown")
    assert vc == VoiceConfig(ref="totally-unknown")


def test_normalize_resolution_order_vllm_before_clone():
    # Selected vllm takes precedence over clone lookup, matching validate_voice_id.
    vc = normalize_voice_id(
        "x",
        vllm_selected=True,
        clone_provider_lookup=lambda r: "cosyvoice",
    )
    assert vc.provider == "vllm_omni"


# ── to_legacy_voice_id (reverse shim) ────────────────────────────────────────

def test_to_legacy_roundtrip_prefixed():
    assert to_legacy_voice_id(VoiceConfig(SOURCE_CLONE, "elevenlabs", "abc")) == "eleven:abc"
    assert to_legacy_voice_id(VoiceConfig(SOURCE_CLONE, "gptsovits", "myv")) == "gsv:myv"


def test_to_legacy_bare_for_other_providers():
    assert to_legacy_voice_id(VoiceConfig(SOURCE_PRESET, "step", "qingchunshaonv")) == "qingchunshaonv"
    assert to_legacy_voice_id(VoiceConfig(SOURCE_CLONE, "cosyvoice", "c123")) == "c123"


def test_to_legacy_empty():
    assert to_legacy_voice_id(VoiceConfig()) == ""
    assert to_legacy_voice_id(None) == ""


def test_prefix_roundtrip_via_normalize_and_back():
    for legacy in ("eleven:voiceX", "gsv:my_voice"):
        vc = parse_legacy_voice_id(legacy)
        assert to_legacy_voice_id(vc) == legacy


# ── read_legacy_voice_id (lazy-migration read tolerance) ─────────────────────

def test_read_legacy_from_flat_string():
    assert read_legacy_voice_id("voice-tone-X") == "voice-tone-X"
    assert read_legacy_voice_id("  spaced  ") == "spaced"
    assert read_legacy_voice_id("") == ""
    assert read_legacy_voice_id(None) == ""


def test_read_legacy_from_object_clone_restores_prefix():
    # Read object-form clones back as legacy prefixed strings, preserving dispatch/validate contracts.
    assert read_legacy_voice_id(
        {"source": "clone", "provider": "elevenlabs", "ref": "abc"}
    ) == "eleven:abc"
    assert read_legacy_voice_id(
        {"source": "clone", "provider": "gptsovits", "ref": "myv"}
    ) == "gsv:myv"


def test_read_legacy_from_object_preset_is_bare_ref():
    # Preset/native/free have no prefix: round-trip objects to bare refs, identical to flat storage.
    assert read_legacy_voice_id(
        {"source": "preset", "provider": "gemini", "ref": "Puck"}
    ) == "Puck"
    assert read_legacy_voice_id(
        {"source": "preset", "provider": "free", "ref": "voice-tone-X"}
    ) == "voice-tone-X"


def test_read_legacy_object_str_roundtrip_equivalence():
    # Key invariant: normalize flat string -> store to_dict object -> read back equals original flat string.
    for legacy in ("eleven:abc", "gsv:myv", "voice-tone-X", "Puck", ""):
        vc = parse_legacy_voice_id(legacy) or VoiceConfig(ref=legacy)
        assert read_legacy_voice_id(vc.to_dict()) == legacy

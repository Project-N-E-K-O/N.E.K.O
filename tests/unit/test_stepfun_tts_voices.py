import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.native_voice_registry import (
    NativeVoiceProvider,
    get_provider,
    get_native_voice_catalog_for_ui,
    is_native_voice,
    register_provider,
    resolve_native_voice_for_routing,
)
from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.stepfun_tts_voices import (
    FREE_STEPFUN_PROVIDER,
    FALLBACK_STEPFUN_TTS_DEFAULT_VOICE,
    STEPFUN_TTS_DEFAULT_MALE_VOICE,
    STEPFUN_TTS_DEFAULT_VOICE,
    get_stepfun_tts_default_voice,
    normalize_stepfun_tts_voice,
)


def test_stepfun_and_free_catalogs_are_registered():
    assert STEPFUN_TTS_DEFAULT_VOICE == "qingchunshaonv"
    assert FALLBACK_STEPFUN_TTS_DEFAULT_VOICE == "qingchunshaonv"
    assert STEPFUN_TTS_DEFAULT_MALE_VOICE == "cixingnansheng"
    assert is_native_voice(STEPFUN_TTS_DEFAULT_VOICE, provider_key="step") is True
    assert is_native_voice(STEPFUN_TTS_DEFAULT_VOICE, provider_key="free") is True
    assert is_native_voice("清纯少女", provider_key="step") is True
    assert is_native_voice("中文男", provider_key="free") is True


def test_stepfun_native_voice_aliases_route_to_canonical_ids():
    assert normalize_stepfun_tts_voice(" 中文男 ") == (
        STEPFUN_TTS_DEFAULT_MALE_VOICE,
        True,
    )
    assert resolve_native_voice_for_routing("step", "默认", lambda _voice_id: False) == (
        STEPFUN_TTS_DEFAULT_VOICE,
        True,
    )
    assert resolve_native_voice_for_routing("free", "中文男", lambda _voice_id: False) == (
        STEPFUN_TTS_DEFAULT_MALE_VOICE,
        True,
    )


def test_stepfun_worker_normalization_uses_active_provider_catalog():
    original_free_provider = get_provider("free")
    custom_free_provider = NativeVoiceProvider(
        key="free",
        catalog={"free-only-voice": "免费专属"},
        aliases={"free-alias": "free-only-voice"},
        default_voice="free-only-voice",
        default_male_voice="free-only-voice",
        catalog_prefix="免费 API",
        catalog_value_is_display_name=True,
    )
    register_provider(custom_free_provider)
    try:
        assert normalize_stepfun_tts_voice("free-alias", "free") == (
            "free-only-voice",
            True,
        )
        assert normalize_stepfun_tts_voice("free-alias", "step") == (
            STEPFUN_TTS_DEFAULT_VOICE,
            False,
        )
        assert get_stepfun_tts_default_voice("free") == "free-only-voice"
    finally:
        provider_to_restore = original_free_provider or FREE_STEPFUN_PROVIDER
        if provider_to_restore is not None:
            register_provider(provider_to_restore)


def test_stepfun_ui_catalog_exposes_provider_label():
    catalog = get_native_voice_catalog_for_ui("step")
    assert catalog is not None
    assert STEPFUN_TTS_DEFAULT_VOICE in catalog
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider"] == "step"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider_label"] == "StepFun"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["display_name"] == "清纯少女"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["gender"] == ""
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["prefix"] == "清纯少女"


def test_free_ui_catalog_uses_voice_label_without_provider_prefix():
    catalog = get_native_voice_catalog_for_ui("free")
    assert catalog is not None
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider"] == "free"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider_label"] == "免费 API"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["display_name"] == "清纯少女"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["gender"] == ""
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["prefix"] == "清纯少女"
    assert catalog[STEPFUN_TTS_DEFAULT_MALE_VOICE]["prefix"] == "磁性男声"


def test_stepfun_catalog_is_loaded_from_api_providers_config():
    step_cfg = get_native_tts_voice_provider_config("step")
    free_cfg = get_native_tts_voice_provider_config("free")

    assert step_cfg["voices"][STEPFUN_TTS_DEFAULT_VOICE] == "清纯少女"
    assert step_cfg["default_voice"] == STEPFUN_TTS_DEFAULT_VOICE
    assert step_cfg["default_male_voice"] == STEPFUN_TTS_DEFAULT_MALE_VOICE
    assert free_cfg["voices"] == step_cfg["voices"]
    assert free_cfg["catalog_prefix"] == "免费 API"
    assert free_cfg["catalog_value_is_display_name"] is True

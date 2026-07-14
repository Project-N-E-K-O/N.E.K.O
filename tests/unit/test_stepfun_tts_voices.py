import os
import sys

import pytest


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
from utils.tts.providers import stepfun as stepfun_provider


def test_stepfun_and_free_catalogs_are_registered():
    assert STEPFUN_TTS_DEFAULT_VOICE == "qingchunshaonv"
    assert FALLBACK_STEPFUN_TTS_DEFAULT_VOICE == "linjiameimei"
    assert STEPFUN_TTS_DEFAULT_MALE_VOICE == "wenrounansheng"
    assert is_native_voice(STEPFUN_TTS_DEFAULT_VOICE, provider_key="step") is True
    assert is_native_voice(STEPFUN_TTS_DEFAULT_VOICE, provider_key="free") is True
    assert is_native_voice("青春少女", provider_key="step") is True
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
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["display_name"] == "青春少女"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["gender"] == ""
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["prefix"] == "青春少女"


def test_free_ui_catalog_uses_voice_label_without_provider_prefix():
    catalog = get_native_voice_catalog_for_ui("free")
    assert catalog is not None
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider"] == "free"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["provider_label"] == "免费 API"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["display_name"] == "青春少女"
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["gender"] == ""
    assert catalog[STEPFUN_TTS_DEFAULT_VOICE]["prefix"] == "青春少女"
    assert catalog[STEPFUN_TTS_DEFAULT_MALE_VOICE]["prefix"] == "温柔男声"


def test_stepfun_catalog_is_loaded_from_api_providers_config():
    step_cfg = get_native_tts_voice_provider_config("step")
    free_cfg = get_native_tts_voice_provider_config("free")

    assert step_cfg["voices"][STEPFUN_TTS_DEFAULT_VOICE] == "青春少女"
    assert step_cfg["default_voice"] == STEPFUN_TTS_DEFAULT_VOICE
    assert step_cfg["default_male_voice"] == STEPFUN_TTS_DEFAULT_MALE_VOICE
    assert free_cfg["voices"] == step_cfg["voices"]
    assert free_cfg["catalog_prefix"] == "免费 API"
    assert free_cfg["catalog_value_is_display_name"] is True


def test_missing_configs_use_symmetric_routable_fallbacks(monkeypatch):
    monkeypatch.setattr(
        stepfun_provider,
        "_load_stepfun_provider_config",
        lambda _provider_key: {},
    )

    step_provider = stepfun_provider._create_provider("step")
    free_provider = stepfun_provider._create_provider("free")

    for provider in (step_provider, free_provider):
        assert provider.default_voice == FALLBACK_STEPFUN_TTS_DEFAULT_VOICE
        assert provider.default_voice in provider.catalog
        assert provider.default_male_voice in provider.catalog
        assert provider.normalize("default") == (
            FALLBACK_STEPFUN_TTS_DEFAULT_VOICE,
            True,
        )
    assert step_provider.catalog == free_provider.catalog
    assert step_provider.catalog_prefix == "StepFun"
    assert free_provider.catalog_prefix == "免费 API"


@pytest.mark.parametrize(
    "broken_cfg",
    [
        {"voices": {"configured": "Configured Voice"}},
        {
            "voices": {"configured": "Configured Voice"},
            "default_voice": "missing-from-catalog",
        },
    ],
)
def test_invalid_configs_use_routable_fallbacks(monkeypatch, broken_cfg):
    monkeypatch.setattr(
        stepfun_provider,
        "_load_stepfun_provider_config",
        lambda _provider_key: broken_cfg,
    )

    provider = stepfun_provider._create_provider("step")

    assert provider.default_voice == FALLBACK_STEPFUN_TTS_DEFAULT_VOICE
    assert provider.default_voice in provider.catalog
    assert provider.default_male_voice in provider.catalog


def test_valid_stepfun_config_still_has_priority(monkeypatch):
    cfg = {
        "voices": {"configured": "Configured Voice"},
        "aliases": {"default": "configured"},
        "default_voice": "configured",
        "default_male_voice": "configured",
        "catalog_prefix": "Configured StepFun",
        "catalog_value_is_display_name": True,
    }
    monkeypatch.setattr(
        stepfun_provider,
        "_load_stepfun_provider_config",
        lambda _provider_key: cfg,
    )

    provider = stepfun_provider._create_provider("step")

    assert provider.catalog == cfg["voices"]
    assert provider.default_voice == "configured"
    assert provider.normalize("default") == ("configured", True)
    assert provider.catalog_prefix == "Configured StepFun"

import json

import pytest

# Importing the heavy worker package registers the hosted TTS providers
# (incl. MiMo's TTSProvider + preset_catalog) into tts_provider_registry.
from main_logic import tts_client  # noqa: F401
from utils import tts_provider_registry
from utils.mimo_tts_voices import MIMO_PRESET_CATALOG, normalize_mimo_tts_voice
from utils.native_voice_registry import get_provider, is_native_voice, list_providers
from utils.tts.providers import mimo as mimo_provider
from utils.voice_config import read_legacy_voice_id


class _CM:
    """Minimal ConfigManager stub for registry selection queries.

    selected_provider iterates every registered provider's is_selected, so the
    stub must answer the config reads they do (core_config, the raw
    core_config.json for vLLM/GPT-SoVITS dropdown, the tts_custom slot for the
    GPT-SoVITS legacy gate)."""

    def __init__(self, core_config, tts_custom_config=None, raw_core_config=None, stored_voice_ids=None):
        self._core_config = core_config
        self._tts_custom_config = tts_custom_config or {"is_custom": False}
        self._raw = raw_core_config if raw_core_config is not None else {}
        self._stored_voice_ids = set(stored_voice_ids or ())

    def get_core_config(self):
        return self._core_config

    def voice_id_exists_in_any_storage(self, voice_id):
        return voice_id in self._stored_voice_ids

    def load_json_config(self, filename, default):
        if filename == "core_config.json":
            return self._raw
        return default

    def get_model_api_config(self, model_type):
        if model_type == "tts_custom":
            return self._tts_custom_config
        if model_type == "realtime":
            return {"api_type": "qwen", "base_url": ""}
        return {}

    def get_tts_api_key(self, provider):
        return "test-key"


@pytest.mark.unit
def test_mimo_registered_as_hosted_with_preset_catalog():
    provider = tts_provider_registry.get("mimo")
    assert provider is not None
    assert provider.kind == "hosted"
    assert "preset" in provider.capabilities
    # The preset directory is the single source of truth for MiMo built-in voices, attached to the unified provider
    assert provider.preset_catalog is MIMO_PRESET_CATALOG


@pytest.mark.unit
def test_mimo_no_longer_registered_as_native():
    # mimo has been removed from native_voice_registry (no longer bootstrapped or registered in _PROVIDERS)
    assert get_provider("mimo") is None
    assert "mimo" not in list_providers()
    assert is_native_voice("Milo") is False


@pytest.mark.unit
def test_mimo_voice_aliases_normalize_to_catalog_ids():
    assert normalize_mimo_tts_voice("默认") == ("mimo_default", True)
    assert normalize_mimo_tts_voice("中文女") == ("冰糖", True)
    assert normalize_mimo_tts_voice("english male") == ("Milo", True)
    assert tts_provider_registry.is_preset_voice("mimo", "冰糖") is True
    assert tts_provider_registry.is_preset_voice("mimo", "not-a-mimo-voice") is False


@pytest.mark.unit
def test_partial_config_default_drives_mimo_fallback_aliases(monkeypatch):
    catalog = {"configured-default": "Configured"}
    monkeypatch.setattr(
        mimo_provider,
        "_CFG",
        {"voices": catalog, "default_voice": "configured-default"},
    )
    monkeypatch.setattr(mimo_provider, "MIMO_TTS_VOICE_GENDERS", catalog)

    preset_catalog = mimo_provider._create_preset_catalog()

    assert preset_catalog.normalize("default") == ("configured-default", True)
    assert preset_catalog.normalize("默认") == ("configured-default", True)


@pytest.mark.unit
def test_mimo_preset_catalog_for_ui_shape_matches_native():
    catalog = tts_provider_registry.preset_catalog_for_ui("mimo")
    assert catalog
    milo = catalog["Milo"]
    # Same shape as NativeVoiceProvider.voice_catalog_for_ui: the frontend groups/renders using these fields
    assert milo["provider"] == "mimo"
    assert milo["provider_label"] == "MiMo"
    assert milo["builtin"] is True
    assert set(milo) == {"prefix", "provider", "provider_label", "gender", "display_name", "builtin"}


def _selected_catalog(core_config, cm):
    # Simulate the two steps used by /voices to get the catalog: first get the winning key, then retrieve its static preset catalog by key
    key = tts_provider_registry.selected_provider_key(core_config, cm)
    return tts_provider_registry.preset_catalog_for_ui(key, core_config) if key else None


@pytest.mark.unit
@pytest.mark.parametrize(
    "core_config",
    [
        {"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"},
        {"CORE_API_TYPE": "qwen", "assistApi": "mimo"},
    ],
)
def test_mimo_selected_exposes_preset_catalog_for_ui(core_config):
    catalog = _selected_catalog(core_config, _CM(core_config))
    assert catalog and "Milo" in catalog


@pytest.mark.unit
def test_mimo_assist_api_selects_catalog_even_over_core_native():
    # core=gemini but assistApi=mimo: mimo (priority 60) wins dispatch before native,
    # so the UI catalog should also return mimo (UI/validation and dispatch use the same priority decision).
    core_config = {"CORE_API_TYPE": "gemini", "ttsProvider": "step", "assistApi": "mimo"}
    catalog = _selected_catalog(core_config, _CM(core_config))
    assert catalog and "Milo" in catalog


@pytest.mark.unit
def test_mimo_catalog_hidden_when_gptsovits_custom_tts_wins():
    # GPT-SoVITS (priority 10) wins first and has no static preset catalog → no mimo preset catalog, and it is not considered a valid preset
    core_config = {"CORE_API_TYPE": "qwen", "assistApi": "mimo", "GPTSOVITS_ENABLED": True}
    cm = _CM(core_config, tts_custom_config={"is_custom": True})
    assert _selected_catalog(core_config, cm) is None
    assert tts_provider_registry.is_selected_preset_voice(core_config, cm, "Milo") is False
    # The winner is gptsovits, which has no static catalog: /voices should also suppress core-native (key is non-None but catalog is None)
    assert tts_provider_registry.selected_provider_key(core_config, cm) == "gptsovits"


@pytest.mark.unit
def test_vllm_winner_exposes_configured_default_voice():
    # Custom WSS TTS mirrors HTTPS custom: when the configured voice is empty, display the provider's default voice,
    # and group it under the “Custom API” source while retaining vllm_omni as the actual routing owner.
    core_config = {
        "CORE_API_TYPE": "gemini",
        "ENABLE_CUSTOM_API": True,
        "ttsModelProvider": "vllm_omni",
        "ttsVoiceId": "",
    }
    cm = _CM(core_config, raw_core_config={"ttsModelProvider": "vllm_omni"})
    key = tts_provider_registry.selected_provider_key(core_config, cm)
    assert key == "vllm_omni"
    assert tts_provider_registry.preset_catalog_for_ui(key, core_config) == {
        "default": {
            "prefix": "default",
            "provider": "vllm_omni",
            "provider_label": "custom",
            "gender": "",
            "display_name": "default",
            "builtin": True,
        }
    }


@pytest.mark.unit
def test_preview_guard_blocks_mimo_preset_but_lets_same_name_clone_through():
    from main_routers.characters_router import _is_unpreviewable_selected_preset_voice

    cc = {"CORE_API_TYPE": "qwen", "assistApi": "mimo"}
    # Pure preset voice (no voice_data, absent from every storage bucket) → reject (preview is not supported yet)
    assert _is_unpreviewable_selected_preset_voice(_CM(cc), cc, "Milo", None) is True
    # A cloned voice with the same name as a preset: the current API has voice_data → allow cloned-voice preview
    assert _is_unpreviewable_selected_preset_voice(_CM(cc), cc, "Milo", {"provider": "cosyvoice"}) is False
    # Cross-slot clone (voice_data is None but the voice exists in some storage bucket) → allow as well
    assert _is_unpreviewable_selected_preset_voice(_CM(cc, stored_voice_ids={"Milo"}), cc, "Milo", None) is False
    # MiMo is not selected → this is not a preset, allow
    not_sel = {"CORE_API_TYPE": "qwen", "assistApi": "qwen"}
    assert _is_unpreviewable_selected_preset_voice(_CM(not_sel), not_sel, "Milo", None) is False


@pytest.mark.unit
def test_mimo_preset_voice_saveable_only_when_selected():
    selected = {"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"}
    not_selected = {"CORE_API_TYPE": "qwen", "assistApi": "qwen"}
    assert tts_provider_registry.is_selected_preset_voice(selected, _CM(selected), "Milo") is True
    assert tts_provider_registry.is_selected_preset_voice(not_selected, _CM(not_selected), "Milo") is False


# ── validate_voice_id end-to-end (real ConfigManager): cleanup uses this to determine validity,
#    so it must recognize MiMo preset voices as valid when selected; otherwise existing MiMo config would be cleared ──────────


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


@pytest.mark.unit
def test_validate_voice_id_accepts_mimo_preset_when_selected(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "mimo"})
    assert config_manager.validate_voice_id("Milo") is True
    assert config_manager.validate_voice_id("冰糖") is True


@pytest.mark.unit
def test_validate_voice_id_rejects_mimo_preset_when_not_selected(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "qwen"})
    assert config_manager.validate_voice_id("Milo") is False


# ── normalize/storage end-to-end: when saving a MiMo preset voice, fill in source=preset/provider=mimo
#    ownership metadata (#1842 structured-schema gap, #1848 follow-up), while still round-tripping to the bare key ──


@pytest.mark.unit
def test_normalize_mimo_preset_marks_source_preset_provider_mimo(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "mimo"})
    vc = config_manager.normalize_voice_id_to_config("Milo")
    assert (vc.source, vc.provider, vc.ref) == ("preset", "mimo", "Milo")
    # Write to storage: migrate to a structured object (not a bare string), then round-trip back to the library key 'Milo'
    stored = config_manager.voice_id_to_storage_value("Milo")
    assert stored == {"source": "preset", "provider": "mimo", "ref": "Milo"}
    assert read_legacy_voice_id(stored) == "Milo"


@pytest.mark.unit
def test_normalize_mimo_preset_unresolved_when_not_selected(config_manager):
    # MiMo is not selected → do not recognize as a preset; pass the bare ref through unchanged (empty provider/source), do not mislabel it
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "qwen"})
    vc = config_manager.normalize_voice_id_to_config("Milo")
    assert (vc.source, vc.provider, vc.ref) == ("", "", "Milo")
    # At the persistence layer: save an unresolved bare ref as a flat string (not an empty-shell structured object), guarding against regression
    stored = config_manager.voice_id_to_storage_value("Milo")
    assert stored == "Milo"
    assert read_legacy_voice_id(stored) == "Milo"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "voice_id"),
    [
        ("custom", "vendor-voice"),
        ("vllm_omni", "default"),
    ],
)
def test_configured_custom_tts_voice_saves_with_runtime_owner(
    config_manager,
    provider,
    voice_id,
):
    # Both HTTPS and WSS configured voices use the same preset storage schema,
    # while provider keeps the real worker owner for runtime dispatch.
    # Save both HTTPS and WSS configured voices as presets; provider must retain the actual routing owner.
    _write_core_config(
        config_manager,
        {
            "coreApi": "qwen",
            "enableCustomApi": True,
            "ttsModelProvider": provider,
            "ttsModelUrl": "https://speech.example.com/v1"
            if provider == "custom"
            else "wss://speech.example.com/v1",
            "ttsModelId": "vendor-tts",
            "ttsVoiceId": voice_id,
        },
    )

    stored = config_manager.voice_id_to_storage_value(voice_id)

    assert stored == {
        "source": "preset",
        "provider": provider,
        "ref": voice_id,
    }
    assert read_legacy_voice_id(stored) == voice_id


@pytest.mark.unit
def test_configured_custom_voice_owner_wins_over_same_id_clone(
    config_manager,
    monkeypatch,
):
    _write_core_config(
        config_manager,
        {
            "coreApi": "qwen",
            "enableCustomApi": True,
            "ttsModelProvider": "custom",
            "ttsModelUrl": "https://speech.example.com/v1",
            "ttsModelId": "vendor-tts",
            "ttsVoiceId": "vendor-voice",
        },
    )
    monkeypatch.setattr(
        config_manager,
        "get_voices_for_current_api",
        lambda **_kwargs: {
            "vendor-voice": {"provider": "cosyvoice", "source": "clone"}
        },
    )

    stored = config_manager.voice_id_to_storage_value("vendor-voice")

    assert stored == {
        "source": "preset",
        "provider": "custom",
        "ref": "vendor-voice",
    }

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_STATE = ROOT / "static" / "app" / "app-state.js"
APP_SETTINGS = ROOT / "static" / "app" / "app-settings.js"


def test_new_profile_voice_settings_default_enabled_without_becoming_authoritative() -> None:
    state = APP_STATE.read_text(encoding="utf-8")

    assert "independentAsrEnabled: true" in state
    assert "voiceInputResourceOptimizationEnabled: true" in state
    assert "settingsHydrated: false" in state
    assert "independentAsrAuthoritative: false" in state


def test_voice_settings_preserve_explicit_false_during_boot_merge() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "settings.independentAsrEnabled ?? true" in settings
    assert "settings.voiceInputResourceOptimizationEnabled ?? true" in settings
    assert "settings.independentAsrEnabled || true" not in settings
    assert "settings.voiceInputResourceOptimizationEnabled || true" not in settings


def test_resource_optimization_uses_only_the_canonical_shared_setting_key() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "'voiceInputResourceOptimizationEnabled'" in settings
    assert (
        "voiceInputResourceOptimizationEnabled: "
        "S.voiceInputResourceOptimizationEnabled"
    ) in settings
    assert (
        "voiceInputResourceOptimizationEnabled: currentVoiceResourceOptimization"
    ) in settings
    assert "voice_input_resource_optimization_enabled" not in settings

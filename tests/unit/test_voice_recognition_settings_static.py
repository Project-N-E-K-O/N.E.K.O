import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_STATE = ROOT / "static" / "app" / "app-state.js"
APP_SETTINGS = ROOT / "static" / "app" / "app-settings.js"
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
LOCALE_DIR = ROOT / "static" / "locales"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


def test_new_profile_voice_settings_default_enabled_without_becoming_authoritative() -> None:
    state = APP_STATE.read_text(encoding="utf-8")

    assert "independentAsrEnabled: true" in state
    assert "voiceInputResourceOptimizationEnabled: true" in state
    assert "settingsHydrated: false" in state
    assert "independentAsrAuthoritative: false" in state
    assert "voiceInputResourceOptimizationAuthoritative: false" in state


def test_voice_settings_preserve_explicit_false_during_boot_merge() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "settings.independentAsrEnabled ?? true" in settings
    assert "settings.voiceInputResourceOptimizationEnabled ?? true" in settings
    assert "settings.independentAsrEnabled || true" not in settings
    assert "settings.voiceInputResourceOptimizationEnabled || true" not in settings


def test_reset_defaults_match_new_profile_voice_defaults() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")
    reset_defaults = settings.split(
        "function _defaultConversationSettingsForReset()",
        maxsplit=1,
    )[1].split("function _serverSettingsForMerge", maxsplit=1)[0]

    assert "independentAsrEnabled: true" in reset_defaults
    assert "voiceInputResourceOptimizationEnabled: true" in reset_defaults


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


def test_voice_recognition_popover_has_explicit_portal_lifecycle() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    for function_name in (
        "createVoicePanel",
        "openVoicePanel",
        "closeVoicePanel",
        "destroyVoicePanel",
    ):
        assert f"function {function_name}(" in source

    assert "document.body.appendChild(voicePanel)" in source
    assert "position: 'fixed'" in source
    assert "asrContainer.setAttribute('aria-expanded', 'true')" in source
    assert "asrContainer.setAttribute('aria-expanded', 'false')" in source
    assert "event.key === 'Escape'" in source
    assert "document.addEventListener('pointerdown'" in source
    assert "document.removeEventListener('pointerdown'" in source
    assert "window.addEventListener('resize'" in source
    assert "window.removeEventListener('resize'" in source
    assert "window.addEventListener('scroll'" in source
    assert "window.removeEventListener('scroll'" in source
    assert "asrContainer.addEventListener('mouseenter'" in source
    assert "asrContainer.addEventListener('focusin'" in source
    assert "asrContainer.addEventListener('pointerup'" in source


def test_cross_window_voice_settings_publish_a_shared_pending_route_snapshot() -> None:
    state = APP_STATE.read_text(encoding="utf-8")
    settings = APP_SETTINGS.read_text(encoding="utf-8")
    audio_capture = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    assert "voiceSettingsPendingUntilEpoch: null" in state
    assert "pendingVoiceRouteIndependentAsr: null" in state
    assert "S.voiceSettingsPendingUntilEpoch" in settings
    assert "S.pendingVoiceRouteIndependentAsr" in settings
    assert "neko:voice-settings-pending-changed" in settings
    assert "S.voiceSettingsPendingUntilEpoch" in audio_capture
    assert "S.pendingVoiceRouteIndependentAsr" in audio_capture
    assert "neko:voice-settings-pending-changed" in audio_capture


def test_voice_recognition_copy_keeps_native_and_fail_closed_routes_distinct() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    assert "window.t('microphone.voiceRecognitionDisabled')" in source
    assert "window.t('microphone.voiceRecognitionDisabledHint')" in source
    assert "window.t('microphone.voiceRecognitionUnavailable')" in source
    assert "语音输入已关闭" not in source
    assert "自动回退到 Omni" not in source
    assert "自动选择其他识别服务" not in source


def test_voice_recognition_popover_keys_match_across_all_locales() -> None:
    required = {
        "noiseReduction",
        "noiseReductionHint",
        "independentAsr",
        "independentAsrSummary",
        "independentAsrSummaryGeneric",
        "independentAsrNative",
        "voiceRecognitionSettings",
        "voiceRecognitionDisabled",
        "voiceRecognitionDisabledHint",
        "voiceRecognitionUnavailable",
        "voiceRecognitionStatusReady",
        "voiceRecognitionSettingsPending",
        "voiceResourceOptimization",
        "voiceResourceOptimizationHintOn",
        "voiceResourceOptimizationHintOff",
    }

    key_sets: list[set[str]] = []
    for locale_name in LOCALES:
        locale = json.loads(
            (LOCALE_DIR / f"{locale_name}.json").read_text(encoding="utf-8")
        )
        microphone = locale["microphone"]
        assert required <= set(microphone), locale_name
        assert "RNNoise" not in microphone["noiseReductionHint"]
        assert "Silero" not in microphone["noiseReductionHint"]
        key_sets.append(set(microphone))

    assert all(keys == key_sets[0] for keys in key_sets[1:])


def test_async_asr_status_copy_uses_the_caller_provider_key() -> None:
    for locale_name in LOCALES:
        locale = json.loads(
            (LOCALE_DIR / f"{locale_name}.json").read_text(encoding="utf-8")
        )
        microphone = locale["microphone"]
        for key in (
            "independentAsrActive",
            "independentAsrProviderUnavailable",
        ):
            assert "{{providerKey}}" in microphone[key], (locale_name, key)
            assert "{{provider}}" not in microphone[key], (locale_name, key)

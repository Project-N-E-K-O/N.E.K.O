import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_STATE = ROOT / "static" / "app" / "app-state.js"
APP_SETTINGS = ROOT / "static" / "app" / "app-settings.js"
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
LOCALE_DIR = ROOT / "static" / "locales"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


def test_new_user_voice_recognition_and_optimization_default_enabled() -> None:
    state = APP_STATE.read_text(encoding="utf-8")
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "independentAsrEnabled: true" in state
    assert "voiceInputResourceOptimizationEnabled: true" in state
    assert "settings.independentAsrEnabled ?? true" in settings
    assert "settings.independentAsrEnabled ?? false" not in settings


def test_voice_resource_optimization_round_trips_snake_case_contract() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "voice_input_resource_optimization_enabled: S.voiceInputResourceOptimizationEnabled" in settings
    assert "settings.voice_input_resource_optimization_enabled !== false" in settings
    assert "key === 'voice_input_resource_optimization_enabled'" in settings
    assert "voiceInputResourceOptimizationEnabled: currentVoiceResourceOptimization" in settings


def test_voice_recognition_uses_portal_popover_with_keyboard_and_touch_contracts() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    assert "document.body.appendChild(voicePanel)" in source
    assert "voicePanel.style.position" not in source  # fixed positioning is assigned atomically
    assert "position: 'fixed'" in source
    assert "setTimeout(function () { openVoicePanel(false); }, 150)" in source
    assert "setTimeout(function () { closeVoicePanel(false); }, 300)" in source
    assert "asrContainer.addEventListener('focusin'" in source
    assert "event.key === 'Escape'" in source
    assert "document.addEventListener('pointerdown'" in source
    assert "rect.right + gap + panelWidth <= viewportWidth - 12" in source
    assert "voiceBridge.addEventListener('mouseenter'" in source
    assert "aria-expanded" in source


def test_voice_recognition_ui_never_advertises_omni_native_fallback() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    assert "Using Omni native recognition" not in source
    assert "Using Omni native speech recognition" not in source
    assert "voiceRecognitionDisabledHint" in source
    assert "independentAsrNative" not in source


def test_voice_recognition_popover_keys_exist_in_all_locales() -> None:
    required = {
        "noiseReduction",
        "noiseReductionHint",
        "independentAsr",
        "independentAsrSummary",
        "independentAsrSummaryGeneric",
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

    for locale_name in LOCALES:
        locale = json.loads(
            (LOCALE_DIR / f"{locale_name}.json").read_text(encoding="utf-8")
        )
        assert required <= set(locale["microphone"]), locale_name
        assert "RNNoise" not in locale["microphone"]["noiseReductionHint"]
        assert "native speech recognition" not in locale["microphone"][
            "independentAsrNative"
        ].lower()

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_STATE = ROOT / "static" / "app" / "app-state.js"
APP_SETTINGS = ROOT / "static" / "app" / "app-settings.js"
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
ASR_RUNTIME = ROOT / "main_logic" / "core" / "asr_runtime.py"
LOCALE_DIR = ROOT / "static" / "locales"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


def test_new_profile_independent_asr_defaults_off_without_becoming_authoritative() -> None:
    state = APP_STATE.read_text(encoding="utf-8")

    assert "independentAsrEnabled: false" in state
    assert "coreApiSupportsIndependentAsr: null" in state
    assert "voiceInputResourceOptimizationEnabled: true" in state
    assert "settingsHydrated: false" in state
    assert "independentAsrAuthoritative: false" in state
    assert "voiceInputResourceOptimizationAuthoritative: false" in state


def test_voice_settings_preserve_explicit_false_during_boot_merge() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "settings.independentAsrEnabled ?? false" in settings
    assert "settings.voiceInputResourceOptimizationEnabled ?? true" in settings
    assert "settings.independentAsrEnabled || true" not in settings
    assert "settings.voiceInputResourceOptimizationEnabled || true" not in settings


def test_reset_defaults_match_new_profile_voice_defaults() -> None:
    settings = APP_SETTINGS.read_text(encoding="utf-8")
    reset_defaults = settings.split(
        "function _defaultConversationSettingsForReset()",
        maxsplit=1,
    )[1].split("function _serverSettingsForMerge", maxsplit=1)[0]

    assert "independentAsrEnabled: false" in reset_defaults
    assert "voiceInputResourceOptimizationEnabled: true" in reset_defaults


def test_backend_defaults_missing_independent_asr_preference_off() -> None:
    runtime = ASR_RUNTIME.read_text(encoding="utf-8")

    assert 'settings.get("independentAsrEnabled", False)' in runtime


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


def test_voice_recognition_reuses_the_shared_mic_action_subwindow() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    voice_panel = source.split(
        "function openVoiceRecognitionSubwindow()", maxsplit=1
    )[1].split("async function openMicDeviceSubwindow()", maxsplit=1)[0]
    voice_action = source.split(
        "asrActionButton = createMainActionButton(", maxsplit=1
    )[1].split("// 组装", maxsplit=1)[0]

    assert "createMicSubwindow(" in voice_panel
    assert "panel._nekoMicSubwindowBody" in voice_panel
    assert "panel.classList.add('neko-mic-voice-subwindow')" in voice_panel
    assert "voiceStatus.setAttribute('role', 'status')" in voice_panel
    assert "voiceStatus.setAttribute('aria-live', 'polite')" in voice_panel
    assert "'voice-recognition'" in voice_action
    assert "openVoiceRecognitionSubwindow" in voice_action
    assert "createMainActionRow(" in source
    assert "var asrActionRow = createMainActionRow(" in voice_action
    assert "asrActionButton.replaceChild(" not in voice_action

    for legacy_name in (
        "createVoicePanel",
        "openVoicePanel",
        "closeVoicePanel",
        "positionVoicePanel",
        "togglePinnedVoicePanel",
        "voiceBridge",
        "voicePanelPinned",
    ):
        assert legacy_name not in source

    assert not re.search(
        r"document\.addEventListener\(\s*['\"]pointerdown['\"]", source
    )
    assert not re.search(
        r"window\.addEventListener\(\s*['\"](?:resize|scroll)['\"]", source
    )


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
    assert "当前核心使用免费API；独立 ASR 相关开关不适用" in source
    assert "当前核心使用 Omni 原生语音识别" not in source
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
        "voiceRecognitionNativeCoreHint",
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


def test_native_core_hint_describes_the_free_api_in_all_locales() -> None:
    expected = {
        "en": "This Core uses the free API; independent ASR controls do not apply",
        "es": "Este Core usa la API gratuita; los controles de ASR independiente no se aplican",
        "ja": "この Core は無料 API を使用するため、独立 ASR の設定は適用されません",
        "ko": "이 Core는 무료 API를 사용하므로 독립 ASR 설정이 적용되지 않습니다",
        "pt": "Este Core usa a API gratuita; os controles de ASR independente não se aplicam",
        "ru": "Это ядро использует бесплатный API; настройки независимого ASR неприменимы",
        "zh-CN": "当前核心使用免费API；独立 ASR 相关开关不适用",
        "zh-TW": "目前核心使用免費 API；獨立 ASR 相關開關不適用",
    }

    assert set(expected) == set(LOCALES)

    for locale_name, expected_hint in expected.items():
        locale = json.loads(
            (LOCALE_DIR / f"{locale_name}.json").read_text(encoding="utf-8")
        )
        assert locale["microphone"]["voiceRecognitionNativeCoreHint"] == expected_hint


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

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
        "localAsr",
        "localAsrHint",
        "localAsrDependencyMissing",
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


def test_local_asr_preference_is_a_shared_conversation_setting() -> None:
    state = APP_STATE.read_text(encoding="utf-8")
    settings = APP_SETTINGS.read_text(encoding="utf-8")
    runtime = ASR_RUNTIME.read_text(encoding="utf-8")
    reset_defaults = settings.split(
        "function _defaultConversationSettingsForReset()",
        maxsplit=1,
    )[1].split("function _serverSettingsForMerge", maxsplit=1)[0]

    assert "independentAsrProviderPreference: 'auto'" in state
    assert "independentAsrProviderPreference: 'auto'" in reset_defaults
    assert "'independentAsrProviderPreference'" in settings
    assert (
        "independentAsrProviderPreference: currentIndependentAsrProviderPreference"
    ) in settings
    # Core forwards the persisted value; the provider literal itself must
    # stay below the Core ASR bridge (scripts/check_core_contracts.py).
    assert '"independentAsrProviderPreference"' in runtime
    assert "faster_whisper" not in runtime


def test_frontend_provider_preference_values_match_the_backend_allowlist() -> None:
    from utils.conversation_settings_constants import (
        INDEPENDENT_ASR_PROVIDER_PREFERENCES,
    )

    settings = APP_SETTINGS.read_text(encoding="utf-8")
    body = settings.split(
        "function _normalizeIndependentAsrProviderPreference(value)",
        maxsplit=1,
    )[1].split("}", maxsplit=1)[0]
    match = re.search(r"return \[([^\]]*)\]\.indexOf\(value\)", body)
    assert match is not None
    values = set(re.findall(r"'([^']*)'", match.group(1)))
    assert values == set(INDEPENDENT_ASR_PROVIDER_PREFERENCES)


def test_local_asr_toggle_follows_the_independent_asr_gate() -> None:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")
    voice_panel = source.split(
        "function openVoiceRecognitionSubwindow()", maxsplit=1
    )[1].split("async function openMicDeviceSubwindow()", maxsplit=1)[0]

    local_setting = source.split(
        "function createLocalAsrSetting(panelBody, beforeNode)", maxsplit=1
    )[1].split("function reconcileLocalAsrSetting()", maxsplit=1)[0]
    offer_gate = source.split("function shouldOfferLocalAsr()", maxsplit=1)[1].split(
        "function createLocalAsrSetting", maxsplit=1
    )[0]
    capability_listener = source.split(
        "function onCoreApiCapabilityChanged()", maxsplit=1
    )[1].split("}", maxsplit=1)[0]

    assert "if (shouldOfferLocalAsr())" in voice_panel
    assert "'microphone.localAsr'" in local_setting
    assert "'microphone.localAsrHint'" in local_setting
    assert "? 'faster_whisper'" in local_setting
    assert "localAsrToggle.setDisabled(!enabled);" in source
    # Hidden unless the dependency is installed or the preference is already on.
    assert "S.localAsrAvailable === true" in offer_gate
    assert "|| S.independentAsrProviderPreference === 'faster_whisper'" in offer_gate
    # A capability refresh that lands while the panel is open re-reconciles it.
    assert "reconcileLocalAsrSetting();" in capability_listener
    assert "localAsrAvailable: null" in APP_STATE.read_text(encoding="utf-8")
    websocket = (ROOT / "static" / "app" / "app-websocket.js").read_text(
        encoding="utf-8"
    )
    assert "data.localAsrAvailable" in websocket
    assert "faster_whisper: 'faster-whisper'" in source


def test_dependency_missing_status_has_its_own_toast() -> None:
    websocket = (ROOT / "static" / "app" / "app-websocket.js").read_text(
        encoding="utf-8"
    )
    assert "statusCode === 'ASR_INDEPENDENT_DEPENDENCY_MISSING'" in websocket
    assert "window.t('microphone.localAsrDependencyMissing')" in websocket


def _independent_asr_status_block() -> str:
    websocket = (ROOT / "static" / "app" / "app-websocket.js").read_text(
        encoding="utf-8"
    ).replace(chr(13) + chr(10), chr(10))
    return websocket.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)", 1
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]


def test_preparing_status_informs_without_tearing_the_route_down() -> None:
    block = _independent_asr_status_block()
    preparing = block.split("if (statusCode === 'ASR_INDEPENDENT_PREPARING')", 1)[1]
    preparing = preparing.split("return;", 1)[0]
    assert "window.t('microphone.localAsrPreparing')" in preparing
    assert "tearDownBlockedVoiceRoute" not in preparing
    # Handled before the terminal-failure tail, which would tear it down.
    assert block.index("'ASR_INDEPENDENT_PREPARING'") < block.index(
        "tearDownBlockedVoiceRoute();"
    )


def _failure_toast_helper() -> str:
    websocket = (ROOT / "static" / "app" / "app-websocket.js").read_text(
        encoding="utf-8"
    ).replace(chr(13) + chr(10), chr(10))
    return websocket.split("function independentAsrFailureToastText(reason) {", 1)[1].split(
        chr(10) + "    }", 1
    )[0]


def test_failure_reasons_map_to_their_own_guidance() -> None:
    helper = _failure_toast_helper()
    for reason, key in (
        ("ASR_LOCAL_MODEL_LOAD_FAILED", "microphone.localAsrModelLoadFailed"),
        ("ASR_LOCAL_DEPENDENCY_MISSING", "microphone.localAsrDependencyMissing"),
        ("ASR_PROVIDER_WARMUP_TIMEOUT", "microphone.localAsrWarmupTimeout"),
    ):
        branch = helper.split(f"reason === '{reason}'", 1)[1].split("}", 1)[0]
        assert f"t('{key}')" in branch, reason
    # Anything else keeps the generic text.
    assert helper.rstrip().endswith(
        "'Independent ASR unavailable. Voice input has stopped for this session. "
        "Check the independent ASR configuration, then start a new voice session.';"
    )


def test_terminal_failure_status_uses_its_reason_before_the_generic_text() -> None:
    block = _independent_asr_status_block()
    terminal = block.split("tearDownBlockedVoiceRoute();", 1)[1]
    branch = terminal.split("if (statusDetails && statusDetails.reason) {", 1)[1].split(
        "return;", 1
    )[0]
    assert "independentAsrFailureToastText(statusDetails.reason)" in branch
    assert terminal.index("if (statusDetails && statusDetails.reason) {") < terminal.index(
        "microphone.independentAsrFallback"
    )


def test_local_model_copy_exists_in_every_locale_and_names_hf_endpoint() -> None:
    for locale in LOCALES:
        microphone = json.loads(
            (LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8")
        )["microphone"]
        assert microphone.get("localAsrPreparing"), locale
        assert microphone.get("localAsrReloading"), locale
        assert microphone.get("localAsrReady"), locale
        assert "HF_ENDPOINT" in microphone.get("localAsrModelLoadFailed", ""), locale
        assert "HF_ENDPOINT" in microphone.get("localAsrWarmupTimeout", ""), locale
        # The raw status code must never be what a user sees.
        errors = json.loads(
            (LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8")
        )["errors"]
        assert "HF_ENDPOINT" in errors.get("ASR_PROVIDER_WARMUP_TIMEOUT", ""), locale


def test_provider_preference_handshake_authority_mirrors_the_other_asr_keys() -> None:
    state = APP_STATE.read_text(encoding="utf-8")
    settings = APP_SETTINGS.read_text(encoding="utf-8")

    assert "independentAsrProviderPreferenceAuthoritative: false" in state
    # Granted by a merged server GET, an explicit local change, or an
    # explicit cross-window change -- never by an unrelated save.
    compact = " ".join(settings.split())
    assert (
        "S.voiceInputResourceOptimizationAuthoritative = true; "
        "S.independentAsrProviderPreferenceAuthoritative = true;"
    ) in compact
    assert (
        "if (_dirtySettingsKeys.has('independentAsrProviderPreference')) { "
        "S.independentAsrProviderPreferenceAuthoritative = true;"
    ) in compact
    assert "providerPreferenceValueIsStale" in settings


def test_settings_hydrated_event_fires_after_the_server_merge() -> None:
    # The open voice panel re-decides its local-ASR switch on this event; fired
    # before the merge it would still read the boot default preference.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "static" / "app" / "app-settings.js").read_text(
        encoding="utf-8"
    )
    dispatch_at = source.index("new CustomEvent('neko:conversation-settings-hydrated')")
    # Both anchors live in the same GET-merge callback: the merge log line comes
    # after every server value is copied into S, and the telemetry broadcast is
    # the callback's documented "all settings merged" point.
    merged_log_at = source.index("已从服务器合并对话设置")
    telemetry_at = source.index("new CustomEvent('neko:telemetry-branch-resolved'")
    assert source.count("neko:conversation-settings-hydrated") == 1
    assert merged_log_at < dispatch_at < telemetry_at


def test_server_authoritative_provider_preference_is_not_filtered_as_stale() -> None:
    # A peer window's server merge carries the provider preference as a
    # server-authoritative key (not an explicit change); discarding it as stale
    # would pin this window to an outdated preference forever.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "static" / "app" / "app-settings.js").read_text(
        encoding="utf-8"
    )
    start = source.index("const providerPreferenceValueIsStale = !!meta")
    stale_rule = source[start:source.index(";", start)]
    assert "!providerPreferenceServerAuthoritative" in stale_rule
    authoritative = source[
        source.index("const providerPreferenceServerAuthoritative = !!meta"):start
    ]
    assert "meta.serverAuthoritativeKeys.indexOf(providerPreferenceKey) !== -1" in authoritative
    assert "Number.isInteger(meta.serverRevision)" in authoritative


def test_adopted_server_provider_preference_reconciles_the_open_panel() -> None:
    # The adopted value gets no handshake authority, but the voice panel must
    # still hear about it through the pending-change event.
    source = APP_SETTINGS.read_text(encoding="utf-8")
    candidate_start = source.index("const providerPreferenceServerCandidate =")
    candidate = source[candidate_start:source.index(";", candidate_start)]
    assert "providerPreferenceValueDiffers" in candidate
    assert "providerPreferenceServerAuthoritative" in candidate
    # Decided only after the stale / revision checks have run and the survivors
    # were applied, so a dropped older snapshot reports nothing pending.
    start = source.index("const providerPreferenceAdoptedFromServer =")
    rule = source[start:source.index(";", start)]
    assert "providerPreferenceServerCandidate" in rule
    assert "Object.prototype.hasOwnProperty.call(incoming, providerPreferenceKey)" in rule
    assert source.index("const changed = applySharedRuntimeSettings(incoming);") < start
    gate_start = source.index("|| providerPreferenceAdoptedFromServer")
    gate = source[gate_start:source.index("neko:voice-settings-pending-changed", gate_start)]
    assert "window.dispatchEvent(new CustomEvent(" in gate
    # No authority is granted for it.
    authority = source[
        source.index("if (providerPreferenceChangedByOtherWindow) {"):gate_start
    ]
    assert "providerPreferenceAdoptedFromServer" not in authority

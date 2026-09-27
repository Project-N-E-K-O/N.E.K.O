import json
import re
from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-audio-capture.js"

LOCALES_PATH = Path(__file__).resolve().parents[3] / "static" / "locales"

ASR_REGISTRY_META_PATH = Path(__file__).resolve().parents[3] / "main_logic" / "asr_client" / "_registry_meta.py"

pytestmark = pytest.mark.frontend_contract


def test_independent_asr_injection_failure_does_not_show_fallback_toast():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    status_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    injection_branch = status_block.split(
        "if (statusCode === 'ASR_INDEPENDENT_INJECTION_FAILED')",
        1,
    )[1].split("tearDownBlockedVoiceRoute();", 1)[0]

    assert "return;" in injection_branch
    assert "independentAsrFallback" not in injection_branch


def test_disabled_independent_asr_is_a_normal_native_status_without_failure_toast():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    status_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    disabled_branch = status_block.split(
        "if (statusCode === 'ASR_INDEPENDENT_DISABLED')",
        1,
    )[1].split("if (statusCode === 'ASR_INDEPENDENT_INJECTION_FAILED')", 1)[0]

    assert "S.independentAsrActive = false;" in disabled_branch
    assert "return;" in disabled_branch
    assert "independentAsrFallback" not in disabled_branch


def test_independent_asr_terminal_status_reports_stopped_voice_input():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "Independent ASR unavailable; using Omni native recognition" not in source
    assert "Voice input has stopped for this session" in source


def test_provider_unavailable_status_names_provider_and_denies_silent_switch():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE" in source
    assert "microphone.independentAsrProviderUnavailable" in source
    assert "{ providerKey: asrProvider || 'unknown' }" in source
    assert "It did not switch to another speech recognition service" in source


def test_voice_session_activation_status_is_validated_and_exposed_to_ui():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "statusCode === 'VOICE_SESSION_ACTIVATION_STATE'" in source
    assert "data-voice-session-activation-state" in source
    assert "voice-session-activation-changed" in source
    assert "voiceSessionActivationRevision" in source
    assert "activationRevision <=" in source
    assert "voiceIdentity.sessionWaiting" in source
    assert "voiceIdentity.sessionActive" in source
    assert "voiceIdentity.sessionUnavailable" in source


def test_lease_resync_status_resends_snapshot_only_from_capturing_window():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    resync_branch = source.split(
        "if (statusCode === 'VOICE_INPUT_LEASE_RESYNC_REQUIRED')",
        1,
    )[1].split("if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)", 1)[0]

    assert "S.isRecording === true" in resync_branch
    assert "window.appAudioCapture.sendVoiceInputControlState(true);" in resync_branch
    assert resync_branch.index("S.isRecording === true") < resync_branch.index(
        "window.appAudioCapture.sendVoiceInputControlState(true);"
    )
    assert "return;" in resync_branch
    assert "setInterval" not in resync_branch
    assert "setTimeout" not in resync_branch
    assert "mod.sendVoiceInputControlState = sendVoiceInputControlState;" in capture_source


def test_independent_asr_provider_copy_resolves_via_provider_names():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    assert "{ provider: asrProvider }" not in source
    ready_branch = source.split("if (statusCode === 'ASR_INDEPENDENT_READY')", 1)[1].split(
        "if (statusCode === 'ASR_INDEPENDENT_DISABLED')",
        1,
    )[0]
    assert "window.t('microphone.independentAsrActive', { providerKey: asrProvider || 'unknown' })" in ready_branch
    assert "window.t('microphone.independentAsrProviderUnavailable', { providerKey: asrProvider || 'unknown' })" in source

    # The shared popover now owns the summary. It resolves the registry key to a
    # display name, renders that value through the locale template, and refreshes
    # from the toggle handler so the visible route never lags the user's choice.
    summary_block = capture_source.split(
        "function updateVoiceRecognitionUi() {", 1
    )[1].split("function onVoiceLifecycleChanged()", 1)[0]
    assert "'microphone.independentAsrSummary'" in summary_block
    assert "{ provider: provider }" in summary_block
    assert "'microphone.voiceRecognitionDisabled'" in summary_block
    assert "provider: S.independentAsrProvider" not in summary_block

    change_handler = capture_source.split(
        "var asrToggle = createVoiceSettingToggle(", 1
    )[1].split("var voicePanelId", 1)[0]
    assert "updateVoiceRecognitionUi();" in change_handler


def test_provider_names_cover_asr_registry_keys_in_all_locales():
    registry_source = ASR_REGISTRY_META_PATH.read_text(encoding="utf-8")
    registry_keys = set(re.findall(r'provider_key="([a-z0-9_]+)"', registry_source))
    assert registry_keys, "provider_key extraction regex no longer matches _registry_meta.py"
    required_keys = registry_keys | {"unknown"}

    locale_names = sorted(path.name for path in LOCALES_PATH.glob("*.json"))
    assert len(locale_names) == 8

    key_sets = {}
    for locale_name in locale_names:
        locale = json.loads((LOCALES_PATH / locale_name).read_text(encoding="utf-8"))
        provider_names = locale["api"]["providerNames"]
        key_sets[locale_name] = set(provider_names)
        missing = required_keys - set(provider_names)
        assert not missing, f"{locale_name} providerNames missing: {sorted(missing)}"
        for key in required_keys:
            value = provider_names[key]
            assert isinstance(value, str) and value.strip(), f"{locale_name} providerNames[{key}] is empty"

    reference_locale = locale_names[0]
    for locale_name in locale_names[1:]:
        assert key_sets[locale_name] == key_sets[reference_locale], (
            f"providerNames key set of {locale_name} diverges from {reference_locale}"
        )


def test_stale_unsupported_capability_does_not_override_paid_core_preference_harness():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    start = source.index("function attachStartSessionHandshake(ws)")
    end = source.index("function connectWebSocket()", start)
    attach_source = source[start:end]
    harness = (
        """
        const S = {
          coreApiSupportsIndependentAsr: false,
          settingsHydrated: true,
          independentAsrAuthoritative: true,
          independentAsrEnabled: true,
          voiceInputResourceOptimizationAuthoritative: false,
        };
        """
        + attach_source
        + """
        const frames = [];
        const ws = { send(data) { frames.push(data); } };
        attachStartSessionHandshake(ws);
        ws.send(JSON.stringify({ action: 'start_session', input_type: 'audio' }));
        const sent = JSON.parse(frames[0]);
        if (sent.independent_asr_enabled !== true) {
          throw new Error('stale capability must not replace the authoritative preference');
        }
        console.log('ok');
        """
    )
    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_failure_paths_keep_status_provided_asr_provider():
    # Negative counterpart to the teardown reset: failure paths receive the
    # provider from the status event and must keep it for the toasts/hint,
    # so only the normal teardown clears S.independentAsrProvider.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    lifecycle_block = source.split("if (statusCode === 'ASR_LIFECYCLE_STATE')", 1)[1].split(
        "if (statusCode === 'VOICE_INPUT_LEASE_RESYNC_REQUIRED')",
        1,
    )[0]
    blocked_branch = lifecycle_block.split("if (lifecycleState === 'blocked')", 1)[1]
    assert "S.independentAsrProvider = ''" not in blocked_branch

    prefix_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    assert "S.independentAsrProvider = asrProvider;" in prefix_block
    assert "S.independentAsrProvider = ''" not in prefix_block


def test_status_fanout_comment_states_the_real_delivery_contract():
    # An earlier round shipped a comment claiming status "fans out to every
    # window". It does not: send_status targets the manager's current socket,
    # and sync_message_queue feeds the monitor process on a port no app window
    # connects to. The fix routes mic control-plane codes to the lease holder
    # instead, and the comment must say so or the next reader repeats the
    # mistake.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    assert "fans out to every window" not in websocket_source
    assert "_send_to_voice_owner" in websocket_source

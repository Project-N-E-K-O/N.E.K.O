import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-websocket.js"
APP_STATE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-state.js"
APP_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-settings.js"
APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-audio-capture.js"
APP_BUTTONS_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-buttons.js"
LOCALES_PATH = Path(__file__).resolve().parents[2] / "static" / "locales"
WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"
ASR_REGISTRY_META_PATH = Path(__file__).resolve().parents[2] / "main_logic" / "asr_client" / "_registry_meta.py"


def test_independent_asr_injection_failure_does_not_show_fallback_toast():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    status_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    injection_branch = status_block.split(
        "if (statusCode === 'ASR_INDEPENDENT_INJECTION_FAILED')",
        1,
    )[1].split("S.independentAsrActive = false;", 1)[0]

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


def test_independent_asr_terminal_status_clears_partial_preview():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    status_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    terminal_branch = status_block.split(
        "if (statusCode === 'ASR_INDEPENDENT_INJECTION_FAILED')",
        1,
    )[1]

    assert terminal_branch.index("removeExternalAsrPreview();") < terminal_branch.index(
        "S.independentAsrActive = false;"
    )


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


def test_voice_lifecycle_status_is_validated_and_exposed_to_ui():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "statusCode === 'ASR_LIFECYCLE_STATE'" in source
    assert "voiceInputLifecycleState" in source
    assert "voice-input-lifecycle-changed" in source
    assert "data-voice-input-state" in source


def test_lifecycle_blocked_clears_independent_asr_and_shows_failure_toast():
    # runtime.py _handle_independent_asr_error always broadcasts lifecycle
    # BLOCKED before the fatal status code, and most fatal codes
    # (ASR_ENDPOINTING_FAILED, ASR_BLOCKED_ENDPOINTING,
    # ASR_AUDIO_ORDERING_FAILED, ASR_PROVIDER_FINAL_TIMEOUT, provider codes)
    # do NOT carry the ASR_INDEPENDENT_ prefix. The failure teardown must
    # therefore hang off the BLOCKED lifecycle notification, not off a
    # fatal-code enumeration.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    lifecycle_block = source.split("if (statusCode === 'ASR_LIFECYCLE_STATE')", 1)[1].split(
        "if (statusCode === 'VOICE_INPUT_LEASE_RESYNC_REQUIRED')",
        1,
    )[0]

    # BLOCKED teardown lives inside the validated-state branch.
    assert "if (lifecycleState === 'blocked')" in lifecycle_block
    assert lifecycle_block.index("allowedLifecycleStates.indexOf(lifecycleState)") < lifecycle_block.index(
        "if (lifecycleState === 'blocked')"
    )

    blocked_branch = lifecycle_block.split("if (lifecycleState === 'blocked')", 1)[1]
    assert "removeExternalAsrPreview();" in blocked_branch
    assert "S.independentAsrActive = false;" in blocked_branch
    assert blocked_branch.index("removeExternalAsrPreview();") < blocked_branch.index(
        "S.independentAsrActive = false;"
    )
    assert "microphone.independentAsrFallback" in blocked_branch

    # Cross-reference comment so backend changes to the failure path get
    # traced back here.
    assert "_handle_independent_asr_error" in lifecycle_block

    # Start-path failures never emit BLOCKED; the per-code toasts in the
    # ASR_INDEPENDENT_ prefix branch must survive.
    prefix_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    assert "microphone.independentAsrProviderUnavailable" in prefix_block
    assert "microphone.independentAsrFallback" in prefix_block


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

    hint_block = capture_source.split("var asrHintKey = ", 1)[1].split(
        "leftColumn.appendChild(asrContainer);",
        1,
    )[0]
    assert "{ providerKey: S.independentAsrProvider || 'unknown' }" in hint_block
    assert "asrHint.setAttribute('data-i18n-params', JSON.stringify(asrHintParams));" in hint_block
    assert hint_block.index("asrHint.setAttribute('data-i18n-params', JSON.stringify(asrHintParams));") < hint_block.index(
        "window.t(asrHintKey, asrHintParams)"
    )
    assert "provider: S.independentAsrProvider" not in hint_block


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


def test_independent_asr_failure_copy_matches_hard_route_in_all_locales():
    expected = {
        "en.json": (
            "Independent ASR unavailable. Voice input has stopped for this session. Check the independent ASR configuration, then start a new voice session.",
            "Enabled for the next voice session; it will not automatically switch to Omni if unavailable.",
            "{{provider}} is temporarily unavailable. Voice input has stopped for this session. It did not switch to another speech recognition service. Please start a new voice session later.",
        ),
        "es.json": (
            "El ASR independiente no está disponible. La entrada de voz se ha detenido para esta sesión. Revisa la configuración del ASR independiente y después inicia una nueva sesión de voz.",
            "Se activará en la próxima sesión de voz; no cambiará automáticamente a Omni si no está disponible.",
            "{{provider}} no está disponible temporalmente. La entrada de voz se ha detenido para esta sesión. No se cambió a otro servicio de reconocimiento de voz. Inicia una nueva sesión de voz más tarde.",
        ),
        "ja.json": (
            "独立 ASR を利用できないため、この音声セッションの入力を停止しました。独立 ASR の設定を確認してから、新しい音声セッションを開始してください。",
            "次の音声セッションから有効になります。利用できない場合も Omni へ自動的に切り替わりません。",
            "{{provider}} は一時的に利用できません。この音声セッションの入力を停止しました。別の音声認識サービスには切り替えていません。後でもう一度音声セッションを開始してください。",
        ),
        "ko.json": (
            "독립 ASR을 사용할 수 없어 이번 음성 세션의 입력을 중지했습니다. 독립 ASR 설정을 확인한 다음 새 음성 세션을 시작하세요.",
            "다음 음성 세션부터 활성화되며, 사용할 수 없어도 Omni로 자동 전환되지 않습니다.",
            "{{provider}}을(를) 일시적으로 사용할 수 없어 이번 음성 세션의 입력을 중지했습니다. 다른 음성 인식 서비스로 전환하지 않았습니다. 나중에 새 음성 세션을 시작하세요.",
        ),
        "pt.json": (
            "O ASR independente não está disponível. A entrada de voz foi interrompida nesta sessão. Verifique a configuração do ASR independente e depois inicie uma nova sessão de voz.",
            "Será ativado na próxima sessão de voz; não mudará automaticamente para o Omni se estiver indisponível.",
            "{{provider}} está temporariamente indisponível. A entrada de voz foi interrompida nesta sessão. O sistema não mudou para outro serviço de reconhecimento de voz. Inicie uma nova sessão de voz mais tarde.",
        ),
        "ru.json": (
            "Независимый ASR недоступен. Голосовой ввод в этом сеансе остановлен. Проверьте настройки независимого ASR, затем начните новый голосовой сеанс.",
            "Будет включён в следующем голосовом сеансе; при недоступности автоматического переключения на Omni не произойдёт.",
            "{{provider}} временно недоступен. Голосовой ввод в этом сеансе остановлен. Переключения на другую службу распознавания речи не произошло. Начните новый голосовой сеанс позже.",
        ),
        "zh-CN.json": (
            "独立 ASR 不可用，本次语音输入已停止。请检查独立 ASR 配置，然后重新开始语音会话。",
            "将在下次语音会话启用；不可用时不会自动切换到 Omni。",
            "{{provider}} 暂时不可用，本次语音输入已停止。未切换到其他语音识别服务，请稍后重新开始语音会话。",
        ),
        "zh-TW.json": (
            "獨立 ASR 無法使用，本次語音輸入已停止。請檢查獨立 ASR 設定，然後重新開始語音會話。",
            "將於下次語音會話啟用；無法使用時不會自動切換到 Omni。",
            "{{provider}} 暫時無法使用，本次語音輸入已停止。未切換到其他語音辨識服務，請稍後重新開始語音會話。",
        ),
    }

    for locale_name, copy in expected.items():
        locale = json.loads((LOCALES_PATH / locale_name).read_text(encoding="utf-8"))
        microphone = locale["microphone"]
        assert microphone["independentAsrFallback"] == copy[0]
        assert microphone["independentAsrNextSession"] == copy[1]
        assert microphone["independentAsrProviderUnavailable"] == copy[2]


def test_response_discarded_visible_in_react_chat():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "function appendAssistantStatusMessage(text)" in source
    assert "window.reactChatWindowHost.appendMessage({" in source
    assert "appendAssistantStatusMessage(translatedDiscardMsg);" in source

    helper_block = source.split("function appendAssistantStatusMessage(text)", 1)[1].split(
        "function websocketTraceEnabled()",
        1,
    )[0]
    assert helper_block.index("window.reactChatWindowHost.appendMessage({") < helper_block.index(
        "document.createElement('div')"
    )
    assert "status: 'failed'" in helper_block
    assert "window.currentGeminiMessage" not in helper_block

    response_discarded_block = source.split("// -------- response_discarded --------", 1)[1].split(
        "// -------- user_transcript --------",
        1,
    )[0]
    assert "document.createElement('div')" not in response_discarded_block
    assert "appendChild(messageDiv)" not in response_discarded_block


def test_websocket_has_no_widget_mode_capability_or_lifecycle_protocol():
    frontend_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    router_source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    assert "widget_mode_capable" not in frontend_source
    assert "widget_mode_capable" not in router_source
    assert "response.type.startsWith('widget_mode_')" not in frontend_source
    assert "neko:widget-mode-message" not in frontend_source


def test_external_asr_preview_message_is_declared_app_state_field():
    app_state = APP_STATE_PATH.read_text(encoding="utf-8")

    assert "externalAsrPreviewMessage: null," in app_state


def test_external_asr_preview_uses_owned_react_message_id():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    preview_helper = source.split("function upsertExternalAsrPreview(text)", 1)[1].split(
        "function removeExternalAsrPreview()", 1
    )[0]
    remove_helper = source.split("function removeExternalAsrPreview()", 1)[1].split(
        "function websocketTraceEnabled()", 1
    )[0]
    event_block = source.split("// -------- user_transcript_preview", 1)[1].split(
        "// -------- user_transcript --------", 1
    )[0]
    final_block = source.split("// -------- user_transcript --------", 1)[1].split(
        "// --------", 1
    )[0]

    assert "reactChatWindowHost" in preview_helper
    assert "host.appendMessage({" in preview_helper
    assert "host.updateMessage(existingId" in preview_helper
    assert "querySelectorAll" not in event_block
    assert "window.appendMessage" not in event_block
    assert "host.removeMessage(messageId)" in remove_helper
    assert "removeExternalAsrPreview();" in final_block


def test_external_asr_preview_clears_only_on_current_session_terminals():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    final_block = source.split("// -------- user_transcript --------", 1)[1].split(
        "// --------", 1
    )[0]
    session_ended_block = source.split(
        "// -------- session_ended_by_server --------", 1
    )[1].split("// -------- reload_page --------", 1)[0]
    onclose_block = source.split("// ---- onclose ----", 1)[1].split(
        "// ---- onerror ----", 1
    )[0]
    stale_guard, current_close = onclose_block.split(
        "console.log(window.t('console.websocketClosed'));", 1
    )
    onerror_block = source.split("// ---- onerror ----", 1)[1].split(
        "mod.connectWebSocket = connectWebSocket;", 1
    )[0]

    assert "removeExternalAsrPreview();" in final_block
    assert "removeExternalAsrPreview();" in session_ended_block
    assert "if (S.socket !== _thisSocket)" in stale_guard
    assert "removeExternalAsrPreview();" not in stale_guard
    assert "removeExternalAsrPreview();" in current_close
    assert "removeExternalAsrPreview();" not in onerror_block


def test_empty_preview_message_clears_streaming_preview_bubble():
    # Codex P2: a turn that ends with an EMPTY final (OpenAI/Step stalled-item
    # timeouts) deliberately injects no user_transcript, yet user_transcript
    # was the only per-turn message removing the streaming preview bubble —
    # it lingered forever and got reused by the next turn. The backend now
    # sends user_transcript_preview with empty text as an explicit clear
    # (asr_runtime.py _send_core_asr_preview_clear); pin the handler split.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    event_block = source.split("// -------- user_transcript_preview", 1)[1].split(
        "// -------- user_transcript --------", 1
    )[0]

    # Empty text removes the preview...
    empty_branch = event_block.split("if (externalPreviewText === '') {", 1)[1].split(
        "} else {", 1
    )[0]
    assert "removeExternalAsrPreview();" in empty_branch
    assert "upsertExternalAsrPreview" not in empty_branch

    # ... and ONLY empty text: non-empty partials still upsert (negative:
    # the upsert sits in the else branch, so a clear can never spawn a new
    # empty bubble and a partial can never be dropped).
    else_branch = event_block.split("} else {", 1)[1]
    assert (
        "S.externalAsrPreviewMessage = upsertExternalAsrPreview(externalPreviewText);"
        in else_branch
    )
    assert "removeExternalAsrPreview" not in else_branch
    assert event_block.count("upsertExternalAsrPreview(") == 1


def test_independent_asr_toggle_awaits_server_sync_before_next_session():
    # Session start reads the SERVER-persisted independentAsrEnabled value
    # (asr_runtime.py _start_independent_asr_if_enabled), so the toggle must
    # not rely on the fire-and-forget POST inside saveSettings(): it persists
    # locally, runs the POST itself, and publishes the in-flight promise for
    # the session-start path to await.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    toggle_block = capture_source.split(
        "asrInput.addEventListener('change', function () {",
        1,
    )[1].split("asrRow.appendChild(asrLabel);", 1)[0]
    assert "window.appSettings.saveSettings({ skipServerSync: true });" in toggle_block
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in toggle_block
    assert "S.pendingSettingsSyncPromise = syncPromise;" in toggle_block
    # Completion clears the gate only when it still owns it (a newer toggle
    # may have replaced the pending promise meanwhile).
    assert "if (S.pendingSettingsSyncPromise === syncPromise)" in toggle_block
    assert "S.pendingSettingsSyncPromise = null;" in toggle_block
    # Fallback when the settings module does not expose syncSettingsToServer.
    assert "window.appSettings.saveSettings();" in toggle_block

    gate_block = websocket_source.split(
        "function ensureWebSocketOpen(timeoutMs = 5000)",
        1,
    )[1].split("function ensureWebSocketOpenNow(timeoutMs)", 1)[0]
    assert "S.pendingSettingsSyncPromise" in gate_block
    # Negative: only thenables gate; anything else falls through immediately.
    assert "typeof pendingSync.then === 'function'" in gate_block
    # The wait is bounded and never rejects, so a hung or failed POST cannot
    # block session starts or socket-dependent flows.
    assert "Promise.race([" in gate_block
    assert "SETTINGS_SYNC_GATE_TIMEOUT_MS" in gate_block
    assert "pendingSync.catch(" in gate_block
    assert "return ensureWebSocketOpenNow(timeoutMs);" in gate_block
    assert "var SETTINGS_SYNC_GATE_TIMEOUT_MS = 3000;" in websocket_source


def test_every_start_session_send_sits_behind_the_ensure_websocket_gate():
    # The settings-sync gate lives in ensureWebSocketOpen(), so it only closes
    # the toggle-vs-session-start race if every start_session send awaits
    # ensureWebSocketOpen() right before it.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    buttons_source = APP_BUTTONS_PATH.read_text(encoding="utf-8")

    checked = 0
    for source, ensure_call in (
        (websocket_source, "await ensureWebSocketOpen();"),
        (buttons_source, "await window.ensureWebSocketOpen();"),
    ):
        for match in re.finditer(r"action: 'start_session'", source):
            preceding = source[max(0, match.start() - 600):match.start()]
            assert ensure_call in preceding, (
                "start_session send not preceded by ensureWebSocketOpen(): ..."
                + source[max(0, match.start() - 120):match.end()]
            )
            checked += 1
    assert checked >= 4


def test_start_session_payload_carries_independent_asr_handshake():
    # The bounded settings-sync gate is best-effort: when the settings POST
    # fails or outlives the bound, the backend would read a stale persisted
    # independentAsrEnabled. The send() wrapper stamps the frontend's
    # authoritative toggle onto every start_session payload so the backend can
    # override that read (websocket_router -> set_independent_asr_handshake).
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    wrapper = websocket_source.split(
        "function attachStartSessionHandshake(ws)",
        1,
    )[1].split("function connectWebSocket()", 1)[0]
    # Strict-bool stamp taken from live S state at send time — but only once
    # settings are hydrated (see
    # test_start_session_handshake_omitted_until_settings_hydrated).
    assert "msg.independent_asr_enabled = S.independentAsrEnabled === true;" in wrapper
    # Only start_session text frames are rewritten; binary audio frames and
    # other messages pass through untouched.
    assert "typeof data === 'string'" in wrapper
    assert "msg.action === 'start_session'" in wrapper

    # The wrapper is attached at the single socket-creation seam, so every
    # start_session send site (including the ones in app-buttons.js) carries
    # the field.
    creation_index = websocket_source.index("S.socket = new WebSocket(wsUrl);")
    attach_index = websocket_source.index("attachStartSessionHandshake(S.socket);")
    assert 0 < attach_index - creation_index < 200


def test_start_session_handshake_omitted_until_settings_hydrated():
    # On a fresh browser profile — or while the async conversation-settings
    # GET is still pending — S.independentAsrEnabled is only the boot default
    # false. Stamping that onto an early start_session would override the
    # backend's persisted true. The stamp must therefore be gated on
    # S.settingsHydrated; when the field is omitted the backend falls back to
    # its persisted setting (websocket_router forwards the absent field as
    # None; pinned by
    # test_start_session_handshake_missing_falls_back_to_persisted). A
    # permanently failing GET keeps the field omitted — persisted value
    # governs, which is the correct fallback.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")

    wrapper = websocket_source.split(
        "function attachStartSessionHandshake(ws)",
        1,
    )[1].split("function connectWebSocket()", 1)[0]

    # The stamp exists exactly once and only inside the hydration-gated
    # branch: no second, unconditional assignment path.
    assert wrapper.count("msg.independent_asr_enabled") == 1
    assert (
        "msg.action === 'start_session' && S.settingsHydrated === true" in wrapper
    ), "independent_asr_enabled stamp must be gated on S.settingsHydrated"

    # The flag starts false so a pre-hydration start_session omits the field.
    assert "settingsHydrated: false," in state_source


def test_settings_hydration_marked_on_server_merge_and_user_change():
    # S.settingsHydrated must flip true on exactly the three authoritative
    # events, and never merely at boot:
    #   (1) the conversation-settings GET succeeded (server values merged);
    #   (2) the user explicitly changed a setting — the independent-ASR toggle
    #       handler (app-audio-capture.js) runs saveSettings({skipServerSync})
    #       + syncSettingsToServer({ userInitiated: true }), so the synchronous
    #       marker inside syncSettingsToServer covers it even when the POST
    #       later fails (a user action is authoritative even pre-hydration);
    #   (3) a cross-window independent-ASR flip arrived via the 'storage'
    #       listener — the originating window's user action, pinned by
    #       test_cross_window_asr_flip_marks_hydration_and_asr_dirty.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    # (1) Server merge marks hydration only after the null-guard, i.e. only
    # when the GET actually returned a usable result.
    merge_block = settings_source.split(
        "loadSettingsFromServer().then(serverResult => {",
        1,
    )[1].split(".finally(", 1)[0]
    guard_index = merge_block.index("if (!serverResult) return;")
    hydrate_index = merge_block.index("S.settingsHydrated = true;")
    assert guard_index < hydrate_index, (
        "hydration must only be marked after the serverResult null-guard"
    )

    # (2) syncSettingsToServer marks hydration synchronously, before any
    # await, so a failed POST still leaves the user's choice authoritative
    # and the start_session handshake keeps carrying it — but ONLY for
    # userInitiated callers. The periodic timer passes no options and must
    # never mark hydration (pinned by
    # test_periodic_sync_skips_post_and_never_marks_hydration_while_unhydrated).
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split(
        "function startPeriodicSync()",
        1,
    )[0]
    assert sync_fn.count("S.settingsHydrated = true;") == 1
    user_initiated_gate = sync_fn.split("if (userInitiated) {", 1)[1].split("}", 1)[0]
    assert "S.settingsHydrated = true;" in user_initiated_gate, (
        "the hydration mark must sit inside the userInitiated gate"
    )
    assert sync_fn.index("S.settingsHydrated = true;") < sync_fn.index("await fetch(")

    # The independent-ASR toggle handler reaches that marker via
    # syncSettingsToServer({ userInitiated: true }); the saveSettings call it
    # makes skips the internal server sync, so the direct call is the seam.
    toggle_handler = capture_source.split(
        "asrInput.addEventListener('change', function () {",
        1,
    )[1].split("asrRow.appendChild(", 1)[0]
    assert "S.independentAsrEnabled = asrInput.checked;" in toggle_handler
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in toggle_handler

    # Boot must NOT mark hydration: the first-launch initialization save goes
    # through saveSettings({ skipServerSync: true }) which bypasses
    # syncSettingsToServer, keeping the default-false value non-authoritative
    # until the GET resolves or the user acts.
    first_launch_block = settings_source.split(
        "console.log('未找到保存的设置，使用默认值');",
        1,
    )[1].split("} catch (error) {", 1)[0]
    assert "saveSettings({ skipServerSync: true });" in first_launch_block
    assert "S.settingsHydrated" not in first_launch_block
    # And nothing else in loadSettings' synchronous body marks hydration
    # before the async GET callback runs.
    sync_load_body = settings_source.split("function loadSettings()", 1)[1].split(
        "loadSettingsFromServer().then(serverResult => {",
        1,
    )[0]
    assert "S.settingsHydrated" not in sync_load_body


def test_periodic_sync_skips_post_and_never_marks_hydration_while_unhydrated():
    # Persistent GET failure: loadSettingsFromServer resolves null (or the
    # whole chain throws), yet BOTH failure paths still start the periodic
    # task (the .finally() after the merge callback, and the outer catch).
    # Before the userInitiated split, syncSettingsToServer's entry marked
    # S.settingsHydrated unconditionally, so the 60s tick (a) uploaded the
    # boot default independentAsrEnabled=false over the server-persisted
    # true and (b) falsely armed the start_session handshake with that
    # default. Pin the two-part fix.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # Both GET-failure paths do start the periodic task — that is exactly why
    # the tick itself must carry the guard.
    finally_block = settings_source.split(".finally(() => {", 1)[1].split(
        "});", 1
    )[0]
    assert "startPeriodicSync();" in finally_block
    load_catch_block = settings_source.split(
        "console.error('服务器设置同步启动失败:', error);", 1
    )[1].split("}", 1)[0]
    assert "startPeriodicSync();" in load_catch_block

    # (1) The tick refuses to POST while settings were never hydrated (no
    # successful GET and no user change), logging the skip only once.
    tick_body = settings_source.split("_syncTimerId = setInterval(() => {", 1)[1].split(
        "}, SYNC_INTERVAL_MS);",
        1,
    )[0]
    hydration_guard_index = tick_body.index("if (S.settingsHydrated !== true) {")
    sync_call_index = tick_body.index("syncSettingsToServer();")
    assert hydration_guard_index < sync_call_index, (
        "the unhydrated guard must run before the periodic POST"
    )
    guard_block = tick_body[hydration_guard_index:sync_call_index]
    assert "return;" in guard_block
    assert "_periodicSyncSkippedUnhydratedLogged" in guard_block

    # (2) The periodic caller passes no options, so even a tick that does run
    # (post-hydration, or if the guard ever regressed) can never be the event
    # that marks hydration — only userInitiated callers mark (pinned in
    # test_settings_hydration_marked_on_server_merge_and_user_change).
    assert "userInitiated" not in tick_body


def test_user_toggle_during_get_failure_marks_hydration_posts_and_stamps():
    # Round-10 semantics must survive the userInitiated split: an explicit
    # user change is an authoritative hydration source even while the settings
    # GET keeps failing. The independent-ASR toggle marks S.settingsHydrated
    # synchronously (before its POST awaits) and publishes the POST; the
    # start_session handshake then stamps the user's choice.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    # The toggle's direct sync call is user-initiated and still POSTs.
    toggle_block = capture_source.split(
        "asrInput.addEventListener('change', function () {",
        1,
    )[1].split("asrRow.appendChild(asrLabel);", 1)[0]
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in toggle_block

    # saveSettings' full (non-skipServerSync) path is the other user seam —
    # the settings popup, subtitle toggles and chat-window toggles all route
    # through it — so it must pass userInitiated too.
    save_fn = settings_source.split("function saveSettings(options)", 1)[1].split(
        "function loadSettings()",
        1,
    )[0]
    assert "syncSettingsToServer({ userInitiated: true });" in save_fn
    # ... while the first-launch boot save keeps skipping the sync entirely,
    # so boot defaults still never mark hydration.
    assert "saveSettings({ skipServerSync: true });" in settings_source

    # And the handshake stamp keys off exactly that flag, so the toggle's
    # pre-hydration change reaches the backend on the next start_session.
    wrapper = websocket_source.split(
        "function attachStartSessionHandshake(ws)",
        1,
    )[1].split("function connectWebSocket()", 1)[0]
    assert "msg.action === 'start_session' && S.settingsHydrated === true" in wrapper
    assert "msg.independent_asr_enabled = S.independentAsrEnabled === true;" in wrapper


def test_user_dirty_keys_survive_boot_get_merge_field_level():
    # Codex P2 (field-level authority): the conversation-settings GET may read
    # the server BEFORE a user change POSTs its new value, yet resolve AFTER
    # it. The earlier whole-merge-drop design discarded the ENTIRE server
    # merge as soon as ANY userInitiated change happened while the GET was in
    # flight — so changing one unrelated preference made the full local
    # snapshot (including a boot-default independentAsrEnabled) authoritative
    # and the POST clobbered the persisted ASR choice. Pin the replacement:
    # a dirty-key set records exactly which settings the user changed, and the
    # merge applies server values to NON-dirty keys while preserving dirty
    # ones.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # (1) Dirty keys are recorded only inside the userInitiated gate of
    # syncSettingsToServer, synchronously alongside the hydration mark and
    # before any await.
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    assert sync_fn.count("_markUserDirtySettings();") == 1
    user_initiated_gate = sync_fn.split("if (userInitiated) {", 1)[1].split("}", 1)[0]
    assert "_markUserDirtySettings();" in user_initiated_gate
    assert sync_fn.index("_markUserDirtySettings();") < sync_fn.index("await fetch(")

    # (2) loadSettings snapshots the pre-GET settings as the diff baseline
    # before issuing the GET, so keys changed while it is pending diverge
    # from the snapshot and get marked dirty.
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    snapshot_index = load_fn.index("_settingsBaseline = getConversationSettings();")
    get_index = load_fn.index("loadSettingsFromServer().then(serverResult => {")
    assert snapshot_index < get_index

    # (3) The merge is field-level: the per-key dirty skip runs before the S
    # mutation, the subtitle-bridge mirrors carry the same dirty gating, and
    # the baseline is rolled to the merged state BEFORE the writeback
    # saveSettings() so server-applied values are never misattributed as
    # user-dirty by the writeback's own userInitiated diff.
    merge_block = settings_source.split(
        "loadSettingsFromServer().then(serverResult => {", 1
    )[1].split(".finally(", 1)[0]
    null_guard_index = merge_block.index("if (!serverResult) return;")
    hydrate_index = merge_block.index("S.settingsHydrated = true;")
    assert null_guard_index < hydrate_index
    skip_index = merge_block.index("if (_dirtySettingsKeys.has(key)) continue;")
    assert skip_index < merge_block.index("S[key] = serverSettings[key];")
    assert "!_dirtySettingsKeys.has('subtitleEnabled')" in merge_block
    assert "!_dirtySettingsKeys.has('userLanguage')" in merge_block
    roll_index = merge_block.index("_settingsBaseline = getConversationSettings();")
    assert skip_index < roll_index < merge_block.index("saveSettings();")
    # The whole-merge drop is gone: no early return between the null-guard
    # and the hydration mark, and the old drop log no longer exists.
    after_null_guard = null_guard_index + len("if (!serverResult) return;")
    assert "return;" not in merge_block[after_null_guard:hydrate_index]
    assert "丢弃过期的服务器合并" not in settings_source
    assert "_localSettingsGeneration" not in settings_source

    # (4) Negative validation — non-user flows never dirty keys: the periodic
    # tick passes no options (its POST is not a user change), the boot-time
    # skipServerSync save bypasses syncSettingsToServer entirely, and the set
    # is monotone (no delete/clear), so a toggle-and-back stays authoritative.
    tick_body = settings_source.split("_syncTimerId = setInterval(() => {", 1)[1].split(
        "}, SYNC_INTERVAL_MS);", 1
    )[0]
    assert "_markUserDirtySettings" not in tick_body
    assert "_dirtySettingsKeys" not in tick_body
    first_launch_block = settings_source.split(
        "console.log('未找到保存的设置，使用默认值');", 1
    )[1].split("} catch (error) {", 1)[0]
    assert "_dirtySettingsKeys" not in first_launch_block
    assert "saveSettings({ skipServerSync: true });" in first_launch_block
    assert "_dirtySettingsKeys.delete" not in settings_source
    assert "_dirtySettingsKeys.clear" not in settings_source


def test_settings_post_snapshot_waits_bounded_for_boot_get_merge():
    # Codex P2 (merge-before-post): a POST issued while the boot GET is still
    # pending used to snapshot pure local state, so unchanged fields carried
    # boot defaults. The queued runSync now awaits a bounded, never-rejecting
    # gate that settles when the GET's merge settled — the send-time snapshot
    # is therefore assembled AFTER the merge whenever the GET has resolved,
    # and unchanged fields carry server truth. If the GET outlives the bound
    # the POST proceeds with local state and the merge's writeback
    # saveSettings() converges the server afterwards.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    assert "let _settingsGetGate = Promise.resolve();" in settings_source
    assert "const SETTINGS_GET_GATE_TIMEOUT_MS = 3000;" in settings_source

    # The gate await sits inside the queued runSync, before the send-time
    # snapshot and the fetch.
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    run_sync_body = sync_fn.split("const runSync = async () =>", 1)[1]
    gate_index = run_sync_body.index("await _settingsGetGate;")
    snapshot_index = run_sync_body.index("const settings = getConversationSettings();")
    fetch_index = run_sync_body.index("await fetch(")
    assert gate_index < snapshot_index < fetch_index

    # The gate is armed at GET issue time as a race between the settled merge
    # chain (with a catch so it can never reject) and the bounded timeout.
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    assert "const mergeSettled = loadSettingsFromServer().then(serverResult => {" in load_fn
    gate_assign_index = load_fn.index("_settingsGetGate = Promise.race([")
    assert load_fn.index("startPeriodicSync();") < gate_assign_index
    gate_block = load_fn[gate_assign_index:].split("]);", 1)[0]
    assert "mergeSettled.catch(() => { })" in gate_block
    assert "setTimeout(resolve, SETTINGS_GET_GATE_TIMEOUT_MS)" in gate_block

    # Negative: the synchronous hydration/dirty marks stay at call time —
    # only the POST body waits for the merge, never the authority marks.
    assert sync_fn.index("S.settingsHydrated = true;") < sync_fn.index(
        "const runSync = async () =>"
    )
    assert sync_fn.index("_markUserDirtySettings();") < sync_fn.index(
        "const runSync = async () =>"
    )


def test_settings_posts_serialize_so_a_stale_body_cannot_win_persistence():
    # Codex P2 (round 13): flipping the ASR toggle twice before the first POST
    # completed used to start two independent syncSettingsToServer calls with
    # their own snapshots; the backend saves each in a separate
    # asyncio.to_thread, so the OLDER request could finish LAST and persist the
    # earlier toggle value. Pin the serialization fix: every sync queues behind
    # a module-level chain tail and builds its settings snapshot at SEND time
    # (inside the queued runSync), so at most one POST is in flight and the
    # last-issued request always carries the final local state.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # The chain tail starts resolved and is module-scoped (shared by the
    # toggle path AND the periodic tick, so those cannot race each other
    # either).
    assert "let _syncChainTail = Promise.resolve();" in settings_source

    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]

    # Chaining structure: the queued body is attached on BOTH fulfillment and
    # rejection so one failed sync cannot stall the tail, the tail advances to
    # the newly chained promise, and the caller gets that promise back (the
    # toggle handler publishes it as S.pendingSettingsSyncPromise for the
    # ensureWebSocketOpen gate).
    assert "const chained = _syncChainTail.then(runSync, runSync);" in sync_fn
    assert "_syncChainTail = chained;" in sync_fn
    assert "return chained;" in sync_fn

    # The settings snapshot is built inside the queued runSync — at send time,
    # after the predecessor completed — not at call time.
    run_sync_index = sync_fn.index("const runSync = async () =>")
    snapshot_index = sync_fn.index("const settings = getConversationSettings();")
    fetch_index = sync_fn.index("await fetch(")
    assert run_sync_index < snapshot_index < fetch_index

    # Negative: the synchronous hydration mark and dirty-key recording stay
    # at call time, BEFORE the queued body — deferring them would reopen the
    # stale-GET-merge window and the pre-hydration handshake gap.
    assert sync_fn.index("S.settingsHydrated = true;") < run_sync_index
    assert sync_fn.index("_markUserDirtySettings();") < run_sync_index


def test_cross_window_asr_flip_marks_hydration_and_asr_dirty():
    # Codex P2: a cross-window independent-ASR toggle arrives via the
    # 'storage' listener, which used to copy the value into S without marking
    # S.settingsHydrated or the key's authority. In the receiving
    # window that meant (a) the next start_session omitted the handshake field
    # (the stamp is gated on S.settingsHydrated, pinned by
    # test_start_session_handshake_omitted_until_settings_hydrated), so the
    # backend read the OLD persisted value while the originating window's POST
    # was still in flight, and (b) a still-pending settings GET later merged
    # the stale server snapshot over the flip and POSTed it back via
    # saveSettings(). Pin the fix: the flip is detected before the apply and
    # treated as an authoritative hydration event that marks the ASR key
    # dirty (so the field-level merge preserves it), with no POST from the
    # receiving window.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]

    # Flip detection: own-property guard plus strict inequality against S,
    # computed BEFORE applySharedRuntimeSettings mutates S.
    assert (
        "Object.prototype.hasOwnProperty.call(settings, 'independentAsrEnabled')"
        in listener_block
    )
    assert "S.independentAsrEnabled !== settings.independentAsrEnabled" in listener_block
    assert listener_block.index("const asrChangedByOtherWindow") < listener_block.index(
        "applySharedRuntimeSettings(settings)"
    )

    # Hydration mark + ASR dirty mark sit inside the ASR-flip gate only.
    flip_gate = listener_block.split("if (asrChangedByOtherWindow) {", 1)[1].split(
        "}", 1
    )[0]
    assert "S.settingsHydrated = true;" in flip_gate
    assert "_dirtySettingsKeys.add('independentAsrEnabled');" in flip_gate
    assert listener_block.count("S.settingsHydrated = true;") == 1
    assert listener_block.count("_dirtySettingsKeys.add('independentAsrEnabled');") == 1

    # No POST from the receiving window: the originating window owns
    # persistence, and a receiving-window POST would duplicate writes and
    # loop storage events between windows. (Assert on code lines only — the
    # in-source comment legitimately names saveSettings.)
    listener_code = "\n".join(
        line
        for line in listener_block.splitlines()
        if not line.strip().startswith("//")
    )
    assert "saveSettings" not in listener_code
    assert "syncSettingsToServer" not in listener_code
    assert "fetch(" not in listener_code

    # Negative: applySharedRuntimeSettings itself must stay authority-neutral —
    # other shared keys (and non-flip events) keep syncing values across
    # windows without marking hydration or dirtying keys, so a
    # first-launch boot-defaults write in another window can never arm this
    # window's periodic sync or handshake.
    apply_fn = settings_source.split(
        "function applySharedRuntimeSettings(settings) {", 1
    )[1].split("function isManualScreenShareActive()", 1)[0]
    assert "settingsHydrated" not in apply_fn
    assert "_dirtySettingsKeys" not in apply_fn
    assert "_markUserDirtySettings" not in apply_fn


def _run_settings_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping app-settings harness test")
    return subprocess.run(
        [node_path, "-e", script],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_rapid_asr_toggle_double_flip_persists_final_state_harness():
    # Behavioral pin for the Codex P2 fix: drive the real syncSettingsToServer
    # with a controllable fetch and simulate the double-flip race. Before the
    # fix both POSTs were in flight together and completing them in reverse
    # order let the stale body be the backend's last save; now the second POST
    # must not even be issued until the first settles, and its body must carry
    # the final toggle state.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext() {
          // Module load runs loadSettings(), which issues a boot GET; the
          // harness settles it (as a failure) so the bounded settings-POST
          // gate opens without waiting for its timeout, and assertions below
          // look only at the POST calls.
          const postCalls = [];
          const getCalls = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout,
            clearTimeout,
            localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: { independentAsrEnabled: false, settingsHydrated: false },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener() {},
            removeEventListener() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          // The harness drives hydration and the toggle state explicitly from
          // a clean baseline.
          sandbox.window.appState.settingsHydrated = false;
          return { postCalls, getCalls, S: sandbox.window.appState, mod: sandbox.window.appSettings };
        }

        const okResponse = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function settleBootGet(ctx) {
          // Fail the boot GET (null result: no merge, no hydration) so the
          // settings-POST gate settles deterministically.
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          ctx.getCalls[0].resolve({ ok: false });
          await tick();
          await tick();
        }

        async function main() {
          const ctx = makeContext();
          const { postCalls, S, mod } = ctx;
          await settleBootGet(ctx);
          assert(S.settingsHydrated === false, 'a failed boot GET must not mark hydration');

          // First flip: POST issued with the pre-second-flip snapshot.
          S.independentAsrEnabled = true;
          const p1 = mod.syncSettingsToServer({ userInitiated: true });
          assert(S.settingsHydrated === true, 'hydration must be marked synchronously at call time');
          await tick();
          assert(postCalls.length === 1, 'first sync must POST immediately');
          assert(JSON.parse(postCalls[0].body).independentAsrEnabled === true, 'first body snapshots true');

          // Second flip while the first POST is still in flight.
          S.independentAsrEnabled = false;
          const p2 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(postCalls.length === 1, 'second POST must be queued, not concurrent (reordered completions impossible)');

          // Only when the first settles does the second go out — carrying the
          // FINAL state because the snapshot is taken at send time.
          postCalls[0].resolve(okResponse);
          await tick();
          assert(postCalls.length === 2, 'queued sync must run after the predecessor completed');
          assert(JSON.parse(postCalls[1].body).independentAsrEnabled === false, 'last-issued body must carry the final toggle state');
          postCalls[1].resolve(okResponse);
          await p1;
          await p2;

          // Negative: a predecessor that fails (network reject) must neither
          // stall the chain nor reject the published promises.
          S.independentAsrEnabled = true;
          const p3 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          S.independentAsrEnabled = false;
          const p4 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(postCalls.length === 3, 'third sync in flight, fourth queued');
          postCalls[2].reject(new Error('network down'));
          await tick();
          assert(postCalls.length === 4, 'a failed predecessor must not stall the queued sync');
          assert(JSON.parse(postCalls[3].body).independentAsrEnabled === false, 'post-failure sync still carries the final state');
          postCalls[3].resolve(okResponse);
          await p3;
          await p4;

          // Negative: a non-userInitiated (periodic-style) call serializes the
          // same way but never marks hydration.
          const fresh = makeContext();
          await settleBootGet(fresh);
          const pp = fresh.mod.syncSettingsToServer();
          await tick();
          assert(fresh.S.settingsHydrated === false, 'periodic-style sync must not mark hydration');
          assert(fresh.postCalls.length === 1, 'periodic-style sync still POSTs through the chain');
          fresh.postCalls[0].resolve(okResponse);
          await pp;

          console.log('HARNESS_OK');
          // The bounded settings-POST gate leaves a real timer per context;
          // exit explicitly so the process does not linger on it.
          process.exit(0);
        }

        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exit(1);
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "settings sync harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_cross_window_asr_flip_authoritative_over_pending_get_harness():
    # Behavioral pin for the cross-window Codex P2 fix: drive the real module
    # in a vm sandbox, deliver a 'storage' event carrying another window's
    # independent-ASR flip while this window's boot GET is still pending, then
    # resolve that GET with the stale pre-flip server value. The flip must mark
    # hydration (arming the start_session handshake stamp), the field-level
    # merge must preserve the flipped key (marked dirty by the flip gate), and
    # the receiving window must never POST. Negative: a storage event that
    # does NOT flip the toggle stays non-authoritative and the pending GET
    # still merges normally.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext() {
          const postCalls = [];
          const getCalls = [];
          const listeners = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout,
            clearTimeout,
            localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: { independentAsrEnabled: false, settingsHydrated: false },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener(type, fn) { listeners.push({ type, fn }); },
            removeEventListener() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          const storage = listeners.filter((entry) => entry.type === 'storage');
          assert(storage.length === 1, 'module must register exactly one storage listener');
          return {
            postCalls,
            getCalls,
            S: sandbox.window.appState,
            fireStorage(newValue) {
              storage[0].fn({ key: 'project_neko_settings', newValue });
            },
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // Scenario 1: cross-window ASR flip while the boot GET is pending.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          assert(ctx.S.settingsHydrated === false, 'boot alone must not mark hydration');

          ctx.fireStorage(JSON.stringify({ independentAsrEnabled: true }));
          assert(ctx.S.independentAsrEnabled === true, 'the flip must be applied to S');
          assert(ctx.S.settingsHydrated === true, 'the flip must arm the start_session handshake stamp');
          assert(ctx.postCalls.length === 0, 'the receiving window must not POST (originating window owns persistence)');

          // The GET now resolves with the server value read BEFORE the other
          // window's POST landed: the flipped key is dirty, so the field-level
          // merge must preserve it.
          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: true, settings: { independentAsrEnabled: false }, telemetryBranch: null }),
          });
          await tick();
          await tick();
          assert(ctx.S.independentAsrEnabled === true, 'the stale GET merge must not overwrite the cross-window flip');
          assert(ctx.postCalls.length === 0, 'the dropped merge must not POST the stale value back');

          // Scenario 2 (negative): a storage event without an ASR flip is
          // non-authoritative — other shared keys still sync, hydration stays
          // unmarked, and the pending GET then merges exactly as before.
          const ctx2 = makeContext();
          ctx2.fireStorage(JSON.stringify({ independentAsrEnabled: false, mergeMessagesEnabled: true }));
          assert(ctx2.S.mergeMessagesEnabled === true, 'other shared keys must still sync across windows');
          assert(ctx2.S.settingsHydrated === false, 'no ASR flip means no hydration mark');
          assert(ctx2.postCalls.length === 0, 'a non-flip storage event must not POST either');

          ctx2.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: true, settings: { independentAsrEnabled: true }, telemetryBranch: null }),
          });
          await tick();
          await tick();
          assert(ctx2.S.independentAsrEnabled === true, 'the normal server merge must still apply');
          assert(ctx2.S.settingsHydrated === true, 'the normal server merge must still mark hydration');
          assert(ctx2.postCalls.length === 1, 'the same-window merge write-back POST must be unchanged');
          ctx2.postCalls[0].resolve(okPost);

          console.log('HARNESS_OK');
          // The bounded settings-POST gate leaves a real timer per context;
          // exit explicitly so the process does not linger on it.
          process.exit(0);
        }

        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exit(1);
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "cross-window ASR flip harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_unrelated_change_during_pending_get_preserves_server_asr_harness():
    # Behavioral pin for the field-level authority fix (Codex P2): with the
    # old whole-merge-drop, changing ANY unrelated preference while the boot
    # settings GET was pending made the full saveSettings() POST authoritative
    # — the entire server merge was discarded and the POST (built from local
    # state including the boot-default independentAsrEnabled=false) overwrote
    # the persisted ASR choice. Drive the real module: the user POST must be
    # gated until the GET settles, the merge must hydrate the untouched ASR
    # key from the server while preserving the user's dirty key, and every
    # POST body must then carry the server's ASR value. On the pre-fix code
    # these assertions fail (the POST fires immediately with ASR=false and the
    # merge is dropped wholesale). Second scenario: the ASR-toggle-while-
    # pending flow is unchanged — the toggled key stays authoritative over the
    # stale merge and its POST carries the user's choice.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext() {
          const postCalls = [];
          const getCalls = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout,
            clearTimeout,
            localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: { independentAsrEnabled: false, settingsHydrated: false },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          return {
            postCalls,
            getCalls,
            S: sandbox.window.appState,
            win: sandbox.window,
            mod: sandbox.window.appSettings,
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // Scenario 1: unrelated preference changed while the boot GET is
          // pending; the server holds independentAsrEnabled=true, this boot
          // only has the default false.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');

          ctx.win.mergeMessagesEnabled = true; // the settings-popup mirror
          ctx.mod.saveSettings();              // full user path -> userInitiated POST
          assert(ctx.S.settingsHydrated === true, 'a user change still hydrates synchronously');
          await tick();
          assert(ctx.postCalls.length === 0, 'the user POST must wait (bounded) for the pending GET, not fire with boot defaults');

          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: true, mergeMessagesEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();

          assert(ctx.S.independentAsrEnabled === true, 'the untouched ASR key must hydrate from the server, not stay a boot default');
          assert(ctx.S.mergeMessagesEnabled === true, 'the user-changed (dirty) key must survive the merge');
          assert(ctx.postCalls.length === 1, 'the gated user POST goes out once the GET settled');
          const body1 = JSON.parse(ctx.postCalls[0].body);
          assert(body1.independentAsrEnabled === true, 'the send-time snapshot must carry the SERVER ASR value, not the boot default');
          assert(body1.mergeMessagesEnabled === true, 'the send-time snapshot must carry the user change');

          // The merge writeback POST (queued behind the user POST) carries the
          // same merged state, converging the server.
          ctx.postCalls[0].resolve(okPost);
          await tick();
          await tick();
          assert(ctx.postCalls.length === 2, 'the merge writeback POST follows the user POST');
          const body2 = JSON.parse(ctx.postCalls[1].body);
          assert(body2.independentAsrEnabled === true, 'the writeback keeps the server ASR value');
          assert(body2.mergeMessagesEnabled === true, 'the writeback keeps the user change');
          ctx.postCalls[1].resolve(okPost);
          await tick();

          // Scenario 2: the ASR-toggle-while-GET-pending flow is unchanged —
          // the toggled key is dirty, so the stale merge cannot revert it and
          // its POST carries the user's choice.
          const ctx2 = makeContext();
          ctx2.S.independentAsrEnabled = true;
          ctx2.mod.saveSettings({ skipServerSync: true });
          const p = ctx2.mod.syncSettingsToServer({ userInitiated: true });
          assert(ctx2.S.settingsHydrated === true, 'the toggle must hydrate synchronously at call time');
          await tick();
          assert(ctx2.postCalls.length === 0, 'the toggle POST is gated behind the pending GET too');

          ctx2.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx2.S.independentAsrEnabled === true, 'the stale merge must not revert the user toggle');
          assert(ctx2.postCalls.length === 1, 'a merge that only skipped the dirty key must not add a writeback POST');
          assert(JSON.parse(ctx2.postCalls[0].body).independentAsrEnabled === true, 'the toggle POST carries the user choice');
          ctx2.postCalls[0].resolve(okPost);
          await p;

          console.log('HARNESS_OK');
          // The bounded settings-POST gate leaves a real timer per context;
          // exit explicitly so the process does not linger on it.
          process.exit(0);
        }

        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exit(1);
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "unrelated-change-during-pending-GET harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_normal_teardown_paths_reset_independent_asr_route_flags():
    # ASR_INDEPENDENT_READY sets S.independentAsrActive; ordinary user stop,
    # server-side session end, and socket close must reset it (and the
    # provider) too, or the mic settings hint keeps claiming independent ASR
    # is active until some later route status arrives.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    stop_block = capture_source.split("function stopRecording(options)", 1)[1].split(
        "function startMicVolumeVisualization",
        1,
    )[0]
    pre_early_return = stop_block.split("if (!S.isRecording) return;", 1)[0]
    assert "S.independentAsrActive = false;" in pre_early_return
    assert "S.independentAsrProvider = '';" in pre_early_return
    assert pre_early_return.index("window.removeExternalAsrPreview();") < pre_early_return.index(
        "S.independentAsrActive = false;"
    )

    session_ended_block = websocket_source.split(
        "// -------- session_ended_by_server --------",
        1,
    )[1].split("// -------- reload_page --------", 1)[0]
    assert "S.independentAsrActive = false;" in session_ended_block
    assert "S.independentAsrProvider = '';" in session_ended_block
    # Reset must not hide behind the isRecording branch: a paused mic keeps
    # S.isRecording false while the flags are still set.
    assert session_ended_block.index("S.independentAsrActive = false;") < session_ended_block.index(
        "if (S.isRecording)"
    )

    onclose_block = websocket_source.split("// ---- onclose ----", 1)[1].split(
        "// ---- onerror ----",
        1,
    )[0]
    stale_guard, current_close = onclose_block.split(
        "console.log(window.t('console.websocketClosed'));", 1
    )
    # Negative: a stale socket's onclose must not touch the live session flags.
    assert "S.independentAsrActive = false;" not in stale_guard
    assert "S.independentAsrActive = false;" in current_close
    assert "S.independentAsrProvider = '';" in current_close
    assert current_close.index("S.independentAsrActive = false;") < current_close.index(
        "if (S.isRecording || window.isMicStarting)"
    )


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


def test_startup_greeting_release_event_replaces_home_tutorial_block_state():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "STARTUP_GREETING_RELEASE_EVENT = 'neko:startup-greeting-release'" in source
    assert "STARTUP_GREETING_RELEASE_FALLBACK_MS" in source
    assert "function sendStartupGreetingReleaseRequest(reason)" in source
    assert "function consumeStartupGreetingReleasedDetail()" in source
    assert "delete window.__NEKO_STARTUP_GREETING_RELEASED__" in source
    assert "const released = consumeStartupGreetingReleasedDetail()" in source
    assert "function releaseStartupGreetingCheck(reason)" in source
    assert "function hasStartupGreetingReleaseProducer()" in source
    assert "function isStartupGreetingHomePage()" not in source
    assert "function isStartupTutorialActiveForGreeting()" in source
    assert "function scheduleStartupGreetingReleaseFallback()" in source
    assert "window.addEventListener(STARTUP_GREETING_RELEASE_EVENT" in source
    assert "if (detail.released === false)" in source
    assert "releaseStartupGreetingCheck(reason || 'startup-greeting-no-release-producer')" in source
    assert "releaseStartupGreetingCheck('startup-greeting-release-timeout')" in source
    assert "scheduleStartupGreetingReleaseFallback();" in source
    assert "clearTimeout(S._startupGreetingReleaseFallbackTimer)" in source
    assert "sendHomeTutorialState(" not in source
    assert "neko:home-tutorial-features-suppressed" not in source

    active_block = source.split("function isStartupTutorialActiveForGreeting()", 1)[1].split(
        "function scheduleStartupGreetingReleaseFallback()",
        1,
    )[0]
    assert "manager.isTutorialRunning === true" in active_block
    assert "document.body.classList.contains('yui-taking-over')" in active_block
    assert "window.isInTutorial === true" not in active_block

    producer_block = source.split("function hasStartupGreetingReleaseProducer()", 1)[1].split(
        "function isStartupTutorialActiveForGreeting()",
        1,
    )[0]
    assert "window.universalTutorialManager" in producer_block
    assert "universal-manager.js" in producer_block
    assert "isStartupGreetingHomePage" not in producer_block


def test_blocked_greeting_check_retries_without_home_tutorial_state():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    blocked_branch = source.split("if (_isGreetingCheckBlocked()) {", 1)[1].split(
        "try {",
        1,
    )[0]
    assert "sendHomeTutorialState(" not in blocked_branch
    assert "_scheduleGreetingCheckRetry();" in blocked_branch


def test_greeting_check_defers_until_new_user_icebreaker_ends():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    send_block = source.split("function _sendGreetingCheckIfReady()", 1)[1].split(
        "function _onModelReady()",
        1,
    )[0]
    assert send_block.index("if (_deferGreetingCheckForNewUserIcebreaker())") < send_block.index(
        "if (_isGreetingCheckBlocked())"
    )

    defer_block = source.split("function _deferGreetingCheckForNewUserIcebreaker()", 1)[1].split(
        "function _sendGreetingCheckIfReady()",
        1,
    )[0]
    blocking_block = source.split("function isNewUserIcebreakerBlockingGreeting(reason)", 1)[1].split(
        "function normalizeAssistantTurnId(turnId)",
        1,
    )[0]
    assert "return isNewUserIcebreakerActiveForGreeting();" in blocking_block
    assert "isTutorialReleaseGreetingReason" not in blocking_block
    active_block = source.split("function isNewUserIcebreakerActiveForGreeting()", 1)[1].split(
        "function isNewUserIcebreakerPeriodActive()",
        1,
    )[0]
    assert "window.NekoNewUserIcebreakerState" in active_block
    assert "state.isPeriodActive()" in active_block
    assert "window.newUserIcebreaker.getActiveSession()" in active_block
    assert "return isNewUserIcebreakerStorePeriodActive();" in active_block
    assert "hasRuntimeState" not in active_block
    period_block = source.split("function isNewUserIcebreakerPeriodActive()", 1)[1].split(
        "function isNewUserIcebreakerBlockingGreeting(reason)",
        1,
    )[0]
    assert "isNewUserIcebreakerActiveForGreeting()" in period_block
    assert "isNewUserIcebreakerStorePeriodActive()" not in period_block
    assert "readNewUserIcebreakerStore()" not in period_block
    store_block = source.split("function isNewUserIcebreakerStorePeriodActive()", 1)[1].split(
        "function isNewUserIcebreakerActiveForGreeting()",
        1,
    )[0]
    assert "readNewUserIcebreakerStore()" in store_block
    assert "isNewUserIcebreakerEntryBlocking(entry)" in store_block
    entry_block = source.split("function isNewUserIcebreakerEntryBlocking(entry)", 1)[1].split(
        "function isNewUserIcebreakerStorePeriodActive()",
        1,
    )[0]
    assert "entry.completed !== true" in entry_block
    assert "isRecentNewUserIcebreakerEntry(entry)" in entry_block
    assert "return false;" in store_block
    assert "sendHomeTutorialState(" not in defer_block
    assert "_scheduleGreetingCheckRetry();" in defer_block
    assert "S._greetingCheckPending = false;" not in defer_block
    assert "S._greetingCheckReason = '';" not in defer_block
    assert "_resetGreetingCheckRetry(true);" not in defer_block
    assert "var greetingReason = S._greetingCheckReason || (greetingIsSwitch ? 'character-switch' : 'ws-open');" in send_block
    assert "sendHomeTutorialState(" not in send_block
    assert "reason: greetingReason" in send_block
    assert "if (S._startupGreetingReleasePending) {" in send_block
    assert send_block.index("if (S._startupGreetingReleasePending)") < send_block.index(
        "if (_deferGreetingCheckForNewUserIcebreaker())"
    )
    assert "window.addEventListener('neko:new-user-icebreaker-ended'" in source
    assert "function _consumeGreetingCheckForNewUserIcebreaker()" not in source

    assert "function _isTutorialBlockingGreeting()" not in source
    assert "function isHomeTutorialLockedForGreeting()" not in source


def test_new_user_icebreaker_mirror_turn_end_skips_regular_subtitle_finalize():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "function isNewUserIcebreakerMirrorTurnEnd(response)" in source
    helper_block = source.split("function isNewUserIcebreakerMirrorTurnEnd(response)", 1)[1].split(
        "// turn-end / turn end agent_callback",
        1,
    )[0]
    assert "meta.source === 'new_user_icebreaker'" in helper_block
    assert "meta.kind === 'new_user_icebreaker'" in helper_block
    assert "event.source === 'new_user_icebreaker'" in helper_block

    turn_end_block = source.split("// -------- system turn end --------", 1)[1].split(
        "// AI turn_end 后只 reschedule",
        1,
    )[0]
    assert "flushRealisticBufferOnTurnEnd();" in turn_end_block
    assert "emitAssistantLifecycleEvent('neko-assistant-turn-end'" in turn_end_block
    assert "clearPendingAssistantTurnStart();" in turn_end_block
    assert "if (!isNewUserIcebreakerMirrorTurnEnd(response)) {" in turn_end_block
    assert "finalizeAssistantTurn(assistantTurnId);" in turn_end_block


def test_goodbye_blocks_stale_audio_session_started():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    stale_audio_guard = source.split("// -------- session_started --------", 1)[1].split(
        "console.log(window.t('console.sessionStartedReceived')",
        1,
    )[0]

    assert "response.input_mode !== 'text'" in stale_audio_guard
    assert "window.isNekoGoodbyeModeActive()" in stale_audio_guard
    assert "window.cancelPendingSessionStart('Voice start cancelled by goodbye');" in stale_audio_guard
    assert "S.socket.send(JSON.stringify({ action: 'end_session' }));" in stale_audio_guard
    assert "return;" in stale_audio_guard


def test_session_ended_by_server_stops_assistant_text_output():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    app_state = APP_STATE_PATH.read_text(encoding="utf-8")

    assert "suppressAssistantStreamUntilNextSession: false," in app_state
    helper_block = source.split("function stopAssistantTextOutputOnSessionEnd(source)", 1)[1].split(
        "window.addEventListener('neko-assistant-turn-start'",
        1,
    )[0]
    assert "S.suppressAssistantStreamUntilNextSession = true;" in helper_block
    assert "window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;" in helper_block
    assert "window._realisticGeminiQueue = [];" in helper_block
    assert "window._realisticGeminiBuffer = '';" in helper_block
    assert "window._geminiTurnFullText = '';" in helper_block
    assert "window._isProcessingRealisticQueue = false;" in helper_block
    assert "window._realisticProcessingOwner = null;" in helper_block
    assert "window.setReactMessageStatus(bubble, 'assistant', 'sent');" in helper_block
    assert "window._clearPendingHostMessagesByIds(currentBubbleIds);" in helper_block
    assert "window.currentGeminiMessage = null;" in helper_block
    assert "window.currentTurnGeminiBubbles = [];" in helper_block

    rollback_helper = source.split("function clearPendingRollbackForRequest(requestId)", 1)[1].split(
        "function isNewUserIcebreakerMirrorTurnEnd(response)",
        1,
    )[0]
    assert "window.reactChatWindowHost.clearPendingRollbackDraft(requestId);" in rollback_helper
    assert "window._lastSubmittedRequestId === requestId" in rollback_helper
    assert "window._lastSubmittedText = '';" in rollback_helper
    assert "window._lastSubmittedRequestId = '';" in rollback_helper

    session_ended_block = source.split("// -------- session_ended_by_server --------", 1)[1].split(
        "// -------- reload_page --------",
        1,
    )[0]
    assert "stopAssistantTextOutputOnSessionEnd('session_ended_by_server');" in session_ended_block
    assert session_ended_block.index("stopAssistantTextOutputOnSessionEnd('session_ended_by_server');") < session_ended_block.index(
        "clearAssistantLifecycleOnDisconnect('session_ended_by_server');"
    )

    gemini_block = source.split("// -------- gemini_response --------", 1)[1].split(
        "// -------- response_discarded --------",
        1,
    )[0]
    assert "if (S.suppressAssistantStreamUntilNextSession)" in gemini_block
    assert gemini_block.index("if (S.suppressAssistantStreamUntilNextSession)") < gemini_block.index(
        "window.appendMessage(response.text, 'gemini', isNewMessage)"
    )
    assert "return;" in gemini_block.split("if (S.suppressAssistantStreamUntilNextSession)", 1)[1].split(
        "var isNewMessage",
        1,
    )[0]

    discard_block = source.split("// -------- response_discarded --------", 1)[1].split(
        "// -------- summary_response --------",
        1,
    )[0]
    assert "if (S.suppressAssistantStreamUntilNextSession)" in discard_block
    assert discard_block.index("if (S.suppressAssistantStreamUntilNextSession)") < discard_block.index(
        "// Fallback: clear trailing gemini bubbles not tracked"
    )
    assert "return;" in discard_block.split("if (S.suppressAssistantStreamUntilNextSession)", 1)[1].split(
        "emitAssistantSpeechCancel('response_discarded');",
        1,
    )[0]

    session_started_block = source.split("// -------- session_started --------", 1)[1].split(
        "// -------- session_failed --------",
        1,
    )[0]
    assert "S.suppressAssistantStreamUntilNextSession = false;" in session_started_block

    agent_callback_turn_end_block = source.split("// -------- system turn end (agent_callback", 1)[1].split(
        "// -------- system turn end --------",
        1,
    )[0]
    assert "if (S.suppressAssistantStreamUntilNextSession)" in agent_callback_turn_end_block
    assert agent_callback_turn_end_block.index("if (S.suppressAssistantStreamUntilNextSession)") < agent_callback_turn_end_block.index(
        "flushRealisticBufferOnTurnEnd();"
    )
    assert agent_callback_turn_end_block.index("clearPendingRollbackForRequest(response.request_id);") < agent_callback_turn_end_block.index(
        "clearPendingAssistantTurnStart();"
    )

    turn_end_block = source.split("// -------- system turn end --------", 1)[1].split(
        "// AI turn_end 后只 reschedule",
        1,
    )[0]
    assert "if (S.suppressAssistantStreamUntilNextSession)" in turn_end_block
    assert turn_end_block.index("if (S.suppressAssistantStreamUntilNextSession)") < turn_end_block.index(
        "flushRealisticBufferOnTurnEnd();"
    )
    assert turn_end_block.index("clearPendingRollbackForRequest(response.request_id);") < turn_end_block.index(
        "clearPendingAssistantTurnStart();"
    )


def test_ws_open_resyncs_goodbye_state_and_defers_regular_greeting_until_release():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    onopen_greeting_block = source.split("// ── 首次连接 / 切换角色：标记 greeting 意图", 1)[1].split(
        "// ── game-window-state 重连兜底",
        1,
    )[0]

    assert "window.isNekoGoodbyeModeActive()" in onopen_greeting_block
    assert "window.__nekoGoodbyeSilentState" in onopen_greeting_block
    assert "pendingGoodbyeState.pending === true" in onopen_greeting_block
    assert "action: 'goodbye_state'" in onopen_greeting_block
    assert "active: !!goodbyeSyncOnOpen.active" in onopen_greeting_block
    assert "reason: 'ws-open-goodbye'" in onopen_greeting_block
    assert "pendingGoodbyeState.active === true" in onopen_greeting_block
    assert "reason: 'ws-open-goodbye-from-sync'" in onopen_greeting_block
    assert "pending: false" in onopen_greeting_block
    assert "if (goodbyeActiveOnOpen || (goodbyeSyncOnOpen && goodbyeSyncOnOpen.active))" in onopen_greeting_block
    assert "var isGreetingSwitchOnOpen = !!S._pendingGreetingSwitch;" in onopen_greeting_block
    assert "var greetingReasonOnOpen = S._greetingCheckReason || (isGreetingSwitchOnOpen ? 'character-switch' : 'ws-open');" in onopen_greeting_block
    assert "_markGreetingCheckPending(isGreetingSwitchOnOpen, greetingReasonOnOpen);" in onopen_greeting_block
    assert "if (isGreetingSwitchOnOpen || S._startupGreetingReleaseGateUsed)" in onopen_greeting_block
    assert "_sendGreetingCheckIfReady();" in onopen_greeting_block
    assert "S._startupGreetingReleaseGateUsed = true;" in onopen_greeting_block
    assert "sendStartupGreetingReleaseRequest('ws-open')" in onopen_greeting_block

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-websocket.js"
APP_STATE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-state.js"
APP_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-settings.js"
APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-audio-capture.js"
APP_BUTTONS_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-buttons.js"
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
APP_GAME_VOICE_CONTROL_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "app" / "app-game-voice-control.js"
)

def test_game_route_close_events_require_matching_generation_when_one_is_active():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert re.search(
        r"\(endedRouteInstanceId \|\| currentRouteInstanceId\)\s*"
        r"&&\s*"
        r"endedRouteInstanceId !== currentRouteInstanceId",
        source,
    )
    assert re.search(
        r"\(incomingGameRouteInstanceId \|\| currentGameRouteInstanceId\)\s*"
        r"&&\s*"
        r"incomingGameRouteInstanceId !== currentGameRouteInstanceId",
        source,
    )

def test_game_route_speech_cancel_is_scoped_to_the_sdk_correlation_id():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    block = _block_after(
        source,
        "} else if (response.type === 'game_route_speech_cancel') {",
    )

    assert "response.sdk_speech_correlation_id" in block
    assert "cancelledCorrelationId === S.currentPlayingSpeechCorrelationId" in block
    assert "applyUserActivityCancel(" in block

def test_reconnect_route_snapshot_cannot_overwrite_a_newer_websocket_route_event():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")
    reconnect_block = _block_after(
        source,
        "function syncGameWindowStateOnWsConnect() {",
    )

    assert "gameRouteStateRevision: 0" in state_source
    assert "var reconciliationGeneration = _gameRouteReconciliationGeneration;" in reconnect_block
    assert "var routeRevisionAtRequest = gameRouteStateRevision();" in reconnect_block
    assert (
        "reconciliationGeneration !== _gameRouteReconciliationGeneration"
        in reconnect_block
    )
    assert (
        "gameRouteStateRevision() !== routeRevisionAtRequest"
        in reconnect_block
    )
    assert reconnect_block.index(
        "gameRouteStateRevision() !== routeRevisionAtRequest"
    ) < reconnect_block.index(
        "window.dispatchEvent(new CustomEvent('neko-game-window-state-change'"
    )
    stt_gate_block = _block_after(
        source,
        "if (statusCode === 'GAME_VOICE_STT_GATE_ACTIVE') {",
    )
    assert "incomingSttSessionId !== currentSttSessionId" in stt_gate_block
    assert re.search(
        r"\(incomingSttRouteInstanceId \|\| currentSttRouteInstanceId\)\s*"
        r"&&\s*"
        r"incomingSttRouteInstanceId !== currentSttRouteInstanceId",
        stt_gate_block,
    )
    assert stt_gate_block.index("if (staleSttGate) {") < stt_gate_block.index(
        "advanceGameRouteStateRevision();"
    )
    assert stt_gate_block.index(
        "advanceGameRouteStateRevision();"
    ) < stt_gate_block.index(
        "S.gameRouteActive = true;"
    )
    assert source.count("advanceGameRouteStateRevision();") >= 4

def test_late_stt_gate_cannot_reactivate_the_most_recently_ended_route():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")
    prune_opener = "function pruneRecentlyEndedGameRouteIdentities() {"
    remember_opener = "function rememberEndedGameRouteIdentity(gameType, sessionId, routeInstanceId) {"
    check_opener = (
        "function isRecentlyEndedGameRouteIdentity(gameType, sessionId, routeInstanceId) {"
    )
    prune_body = _block_after(source, prune_opener)
    remember_body = _block_after(source, remember_opener)
    check_body = _block_after(source, check_opener)
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping ended-route identity harness")

    result = run_node_script(
        node_path,
        textwrap.dedent(
            f"""
            const GAME_ROUTE_ENDED_IDENTITY_LIMIT = 8;
            const GAME_ROUTE_ENDED_IDENTITY_TTL_MS = 2 * 60 * 1000;
            let now = 1000000;
            Date.now = () => now;
            const S = {{ gameRouteRecentlyEndedIdentities: [] }};
            {prune_opener}
            {prune_body}
            }}
            {remember_opener}
            {remember_body}
            }}
            {check_opener}
            {check_body}
            }}
            function assert(value, message) {{ if (!value) throw new Error(message); }}

            rememberEndedGameRouteIdentity('example-game', 'legacy-session', '');
            assert(!isRecentlyEndedGameRouteIdentity(
              'example-game', 'legacy-session', 'identified-successor'
            ), 'identified successor of a generation-less route was rejected');
            assert(isRecentlyEndedGameRouteIdentity(
              'example-game', 'legacy-session', ''
            ), 'generation-less late gate for a generation-less route was not rejected');

            now += 1;
            rememberEndedGameRouteIdentity('example-game', 'reused-session', 'generation-A');
            now += 1;
            rememberEndedGameRouteIdentity('example-game', 'reused-session', 'generation-B');
            assert(isRecentlyEndedGameRouteIdentity(
              'example-game', 'reused-session', 'generation-A'
            ), 'older ended generation was forgotten after its successor closed');
            assert(isRecentlyEndedGameRouteIdentity(
              'example-game', 'reused-session', 'generation-B'
            ), 'latest ended generation was not rejected');
            assert(!isRecentlyEndedGameRouteIdentity(
              'example-game', 'reused-session', 'generation-C'
            ), 'new generation reusing a session was rejected');
            assert(isRecentlyEndedGameRouteIdentity(
              'example-game', 'reused-session', ''
            ), 'generation-less late gate for an identified ended route was not rejected');
            assert(!isRecentlyEndedGameRouteIdentity(
              'example-game', 'new-session', 'generation-A'
            ), 'different session was rejected');

            S.gameRouteRecentlyEndedIdentities = [];
            for (let i = 0; i < 10; i += 1) {{
              now += 1;
              rememberEndedGameRouteIdentity('example-game', `session-${{i}}`, `generation-${{i}}`);
            }}
            assert(S.gameRouteRecentlyEndedIdentities.length === 8, 'ended identity history exceeded capacity');
            assert(!isRecentlyEndedGameRouteIdentity(
              'example-game', 'session-0', 'generation-0'
            ), 'capacity eviction did not release the oldest identity');
            assert(isRecentlyEndedGameRouteIdentity(
              'example-game', 'session-9', 'generation-9'
            ), 'capacity pruning removed the newest identity');

            now += GAME_ROUTE_ENDED_IDENTITY_TTL_MS + 1;
            pruneRecentlyEndedGameRouteIdentities();
            assert(S.gameRouteRecentlyEndedIdentities.length === 0, 'expired identities were not released');
            """
        ),
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    ended_block = _block_after(source, "if (statusCode === 'GAME_ROUTE_ENDED') {")
    stt_gate_block = _block_after(
        source,
        "if (statusCode === 'GAME_VOICE_STT_GATE_ACTIVE') {",
    )
    window_block = _block_after(
        source,
        "} else if (response.type === 'game_window_state_change') {",
    )
    assert "gameRouteRecentlyEndedIdentities: []" in state_source
    assert "GAME_ROUTE_ENDED_IDENTITY_LIMIT = 8" in source
    assert "GAME_ROUTE_ENDED_IDENTITY_TTL_MS = 2 * 60 * 1000" in source
    assert ended_block.index("rememberEndedGameRouteIdentity(") < ended_block.index(
        "S.gameRouteActive = false;"
    )
    assert stt_gate_block.index("isRecentlyEndedGameRouteIdentity(") < stt_gate_block.index(
        "advanceGameRouteStateRevision();"
    )
    assert window_block.index("pruneRecentlyEndedGameRouteIdentities();") < window_block.index(
        "S.gameRouteActive = true;"
    )
    assert window_block.index("rememberEndedGameRouteIdentity(") < window_block.index(
        "S.gameRouteActive = false;"
    )

def test_independent_asr_failure_copy_matches_hard_route_in_all_locales():
    expected = {
        "en.json": (
            "Independent ASR unavailable. Voice input has stopped for this session. Check the independent ASR configuration, then start a new voice session.",
            "Enabled for the next voice session; it will not automatically switch to Omni if unavailable.",
            "{{providerKey}} is temporarily unavailable. Voice input has stopped for this session. It did not switch to another speech recognition service. Please start a new voice session later.",
        ),
        "es.json": (
            "El ASR independiente no está disponible. La entrada de voz se ha detenido para esta sesión. Revisa la configuración del ASR independiente y después inicia una nueva sesión de voz.",
            "Se activará en la próxima sesión de voz; no cambiará automáticamente a Omni si no está disponible.",
            "{{providerKey}} no está disponible temporalmente. La entrada de voz se ha detenido para esta sesión. No se cambió a otro servicio de reconocimiento de voz. Inicia una nueva sesión de voz más tarde.",
        ),
        "ja.json": (
            "独立 ASR を利用できないため、この音声セッションの入力を停止しました。独立 ASR の設定を確認してから、新しい音声セッションを開始してください。",
            "次の音声セッションから有効になります。利用できない場合も Omni へ自動的に切り替わりません。",
            "{{providerKey}} は一時的に利用できません。この音声セッションの入力を停止しました。別の音声認識サービスには切り替えていません。後でもう一度音声セッションを開始してください。",
        ),
        "ko.json": (
            "독립 ASR을 사용할 수 없어 이번 음성 세션의 입력을 중지했습니다. 독립 ASR 설정을 확인한 다음 새 음성 세션을 시작하세요.",
            "다음 음성 세션부터 활성화되며, 사용할 수 없어도 Omni로 자동 전환되지 않습니다.",
            "{{providerKey}}을(를) 일시적으로 사용할 수 없어 이번 음성 세션의 입력을 중지했습니다. 다른 음성 인식 서비스로 전환하지 않았습니다. 나중에 새 음성 세션을 시작하세요.",
        ),
        "pt.json": (
            "O ASR independente não está disponível. A entrada de voz foi interrompida nesta sessão. Verifique a configuração do ASR independente e depois inicie uma nova sessão de voz.",
            "Será ativado na próxima sessão de voz; não mudará automaticamente para o Omni se estiver indisponível.",
            "{{providerKey}} está temporariamente indisponível. A entrada de voz foi interrompida nesta sessão. O sistema não mudou para outro serviço de reconhecimento de voz. Inicie uma nova sessão de voz mais tarde.",
        ),
        "ru.json": (
            "Независимый ASR недоступен. Голосовой ввод в этом сеансе остановлен. Проверьте настройки независимого ASR, затем начните новый голосовой сеанс.",
            "Будет включён в следующем голосовом сеансе; при недоступности автоматического переключения на Omni не произойдёт.",
            "{{providerKey}} временно недоступен. Голосовой ввод в этом сеансе остановлен. Переключения на другую службу распознавания речи не произошло. Начните новый голосовой сеанс позже.",
        ),
        "zh-CN.json": (
            "独立 ASR 不可用，本次语音输入已停止。请检查独立 ASR 配置，然后重新开始语音会话。",
            "将在下次语音会话启用；不可用时不会自动切换到 Omni。",
            "{{providerKey}} 暂时不可用，本次语音输入已停止。未切换到其他语音识别服务，请稍后重新开始语音会话。",
        ),
        "zh-TW.json": (
            "獨立 ASR 無法使用，本次語音輸入已停止。請檢查獨立 ASR 設定，然後重新開始語音會話。",
            "將於下次語音會話啟用；無法使用時不會自動切換到 Omni。",
            "{{providerKey}} 暫時無法使用，本次語音輸入已停止。未切換到其他語音辨識服務，請稍後重新開始語音會話。",
        ),
    }

    for locale_name, copy in expected.items():
        locale = json.loads((LOCALES_PATH / locale_name).read_text(encoding="utf-8"))
        microphone = locale["microphone"]
        assert microphone["independentAsrFallback"] == copy[0]
        assert microphone["independentAsrNextSession"] == copy[1]
        assert microphone["independentAsrProviderUnavailable"] == copy[2]

def test_websocket_has_no_widget_mode_capability_or_lifecycle_protocol():
    frontend_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    router_source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    assert "widget_mode_capable" not in frontend_source
    assert "widget_mode_capable" not in router_source
    assert "response.type.startsWith('widget_mode_')" not in frontend_source
    assert "neko:widget-mode-message" not in frontend_source

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
    assert "coreApiSupportsIndependentAsr" not in wrapper

    # The wrapper is attached at the single socket-creation seam, so every
    # start_session send site (including the ones in app-buttons.js) carries
    # the field.
    creation_index = websocket_source.index("S.socket = new WebSocket(wsUrl);")
    attach_index = websocket_source.index("attachStartSessionHandshake(S.socket);")
    assert 0 < attach_index - creation_index < 200

def test_start_session_payload_carries_resource_optimization_handshake():
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    wrapper = websocket_source.split(
        "function attachStartSessionHandshake(ws)",
        1,
    )[1].split("function connectWebSocket()", 1)[0]

    assert "voiceInputResourceOptimizationAuthoritative: false," in state_source
    assert "S.voiceInputResourceOptimizationAuthoritative === true" in wrapper
    assert (
        "msg.voice_input_resource_optimization_enabled = "
        "S.voiceInputResourceOptimizationEnabled !== false;"
    ) in wrapper
    assert (
        "_dirtySettingsKeys.has('voiceInputResourceOptimizationEnabled')"
        in settings_source
    )
    assert "S.voiceInputResourceOptimizationAuthoritative = true;" in settings_source

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
    # Codex P2: settingsHydrated alone is not enough — it also flips on an
    # unrelated user preference change while independentAsrEnabled is still the
    # boot default. The stamp needs the per-key authority flag as well.
    assert "S.independentAsrAuthoritative === true" in wrapper, (
        "independent_asr_enabled stamp must also require per-key ASR authority"
    )

    # Both flags start false so a pre-hydration start_session omits the field.
    assert "settingsHydrated: false," in state_source
    assert "independentAsrAuthoritative: false," in state_source

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

def test_blocked_route_latch_blocks_game_exit_microphone_resume():
    # The teardown above is skipped while the game STT gate holds the
    # microphone, and BLOCKED is never re-sent, so the game-exit resume path
    # would reopen the mic onto a still-fail-closed route. A sticky latch
    # closes that, and is cleared wherever a fresh route can exist again.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")

    assert "voiceInputRouteBlocked: false," in state_source

    teardown = websocket_source.split(
        "function tearDownBlockedVoiceRoute() {", 1
    )[1].split("\n    }", 1)[0]
    assert "S.voiceInputRouteBlocked = true;" in teardown

    resume = websocket_source.split("if (shouldResumeAudio && wasRecording", 1)[1].split(
        ")", 1
    )[0]
    assert "S.voiceInputRouteBlocked !== true" in resume

    # Cleared only where a fresh or healthy route really exists: a provider
    # that came READY, the DISABLED (native) route, and user intent to start a
    # new voice session. Deliberately NOT in the session_started handler --
    # lifecycle.py runs the route decision BEFORE sending that ack, so clearing
    # there would wipe the current session's own verdict.
    assert websocket_source.count("S.voiceInputRouteBlocked = false;") == 3
    started_handler = websocket_source.split(
        "S.isTextSessionActive = response.input_mode === 'text';", 1
    )[1].split("var _tiaStarted", 1)[0]
    assert "S.voiceInputRouteBlocked = false;" not in started_handler

def test_session_started_ack_latches_a_blocked_microphone_route():
    # The one clear of the latch that is NOT tied to a route verdict is user
    # intent (app-buttons.js, next to _pendingSessionStartMode = 'audio'). What
    # keeps that from opening the microphone onto a dead route is this branch:
    # the ack carries the settled route (send_session_started in notify.py), so
    # a still-blocked route re-latches before the start promise settles.
    #
    # It is also the only channel that reaches a window which never got an
    # ASR_INDEPENDENT_* status at all -- a fenced start emits none, which is the
    # case the backend's dedupe re-decision (#2539) exists to shrink but cannot
    # remove.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    buttons_source = APP_BUTTONS_PATH.read_text(encoding="utf-8")

    # The clear-on-intent this backstops, in the flow that arms the audio start.
    assert "S.voiceInputRouteBlocked = false;" in buttons_source

    started_handler = websocket_source.split(
        "S.isTextSessionActive = response.input_mode === 'text';", 1
    )[1].split("var _tiaStarted", 1)[0]
    latch = started_handler.split("S.voiceInputRouteBlocked = true;", 1)[0].rsplit(
        "if (", 1
    )[1]
    # Only for a request this window actually made: the latch is set-only, so a
    # blocked verdict belonging to another window's start would stick and this
    # window's own healthy ack could not clear it.
    assert "_ackAnswersThisWindow" in latch
    assert "response.input_mode !== 'text'" in latch
    # Set-only, and only on a blocked verdict: the latch is sticky by design
    # (tearDownBlockedVoiceRoute relies on it surviving), and an ack that says
    # native/independent must not clear what a status verdict set.
    assert "response.microphone_route === 'blocked'" in latch
    # Guarded on the field being present, so an older backend that omits it
    # keeps its current behaviour rather than refusing every microphone.
    assert "response.microphone_route !== 'blocked'" not in started_handler

def test_blocked_route_refuses_to_open_the_microphone():
    # THE guard that closes the cold-start hole. On a cold voice start the mic
    # is opened only AFTER session_started -- i.e. after the failure status --
    # so a server-side lease revoke has nothing to revoke yet, and
    # startMicCapture's own refreshMicLease would re-claim the lease anyway
    # (_handle_voice_input_control enforces only generation monotonicity, and
    # the revoke reset the generation to -1). Placed at the top of
    # startMicCapture so it also covers the device-change restore callers.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    start_fn = capture_source.split("async function startMicCapture() {", 1)[1]
    head = start_fn.split("const _mic = micButton();", 1)[0]
    assert "S.voiceInputRouteBlocked === true" in head
    assert "return false;" in head

    # A refused start must unwind the starting-voice UI rather than throw --
    # throwing would replace the accurate ASR toast with a generic failure.
    assert "function abortVoiceStartForBlockedRoute()" in capture_source
    unwind = capture_source.split("function abortVoiceStartForBlockedRoute() {", 1)[
        1
    ].split("\n    }", 1)[0]
    for expected in (
        "S.isRecording = false;",
        "S.voiceStartPending = false;",
        "window.isMicStarting = false;",
    ):
        assert expected in unwind
    assert "throw" not in _code_only(unwind)

    buttons_source = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    assert "window.abortVoiceStartForBlockedRoute();" in buttons_source

def test_audio_preprocessing_failure_tears_down_the_voice_route():
    # Codex P2. ASR_AUDIO_PREPROCESSING_FAILED rides neither the BLOCKED
    # lifecycle channel nor the ASR_INDEPENDENT_ prefix, so it was the one
    # status that announced a dead route while the microphone kept running.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    branch = _block_after(
        websocket_source, "if (statusCode === 'ASR_AUDIO_PREPROCESSING_FAILED') {"
    )
    assert "tearDownBlockedVoiceRoute();" in branch
    assert "microphone.audioPreprocessingFailed" in branch
    # It must be reached before the ASR_INDEPENDENT_ prefix test, which would
    # not match this code anyway but makes the ordering explicit.
    assert websocket_source.index(
        "if (statusCode === 'ASR_AUDIO_PREPROCESSING_FAILED') {"
    ) < websocket_source.index("if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0) {")

def test_auto_restart_does_not_claim_success_on_a_blocked_route():
    # The rebuilt session can come back fail-closed; startMicCapture then
    # refuses silently, and the handler would still light the floating mic,
    # toast "restart complete", and leave the button row it disabled dead.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    restart = websocket_source.split("await sessionStartPromise;", 1)[1].split(
        "app.restartComplete", 1
    )[0]
    assert "S.voiceInputRouteBlocked === true" in restart
    assert "window.abortVoiceStartForBlockedRoute();" in restart
    # It must bail before the toast, and restore controls the restart disabled.
    assert restart.index("S.voiceInputRouteBlocked === true") < restart.index(
        "startMicCapture"
    )
    assert "resetSessionButton(); if (_rsB) _rsB.disabled = false;" in restart

def test_reconnect_reconciliation_repairs_appstate_not_only_the_dom_event():
    """chat.html has no listener that writes S.gameRoute*, so the shared
    reconnect path must repair appState itself.

    The DOM event dispatched here is only turned into appState by
    app-game-voice-control.js, and templates/index.html is the only page that
    loads that file. Without an appState write in this block, a chat window that
    reconnects or reloads while a game route is active keeps gameRouteActive
    false for the rest of the round, and the reverse desync survives too.
    """
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    reconnect_block = _block_after(
        source,
        "function syncGameWindowStateOnWsConnect() {",
    )

    assert "neko-game-window-state-change" in reconnect_block
    for field in (
        "S.gameRouteActive",
        "S.gameRouteGameType",
        "S.gameRouteSessionId",
        "S.gameRouteInstanceId",
    ):
        assert field in reconnect_block, (
            f"reconnect reconciliation does not repair {field}; chat.html has no "
            "other writer for it"
        )
    # Ordering matters: the repair must follow the dispatch, or index.html's
    # voice bridge observes already-cleared identity and broadcasts a closed
    # route with an empty session id. Every assignment, not just the flag --
    # a repair that cleared the identity fields early would produce exactly the
    # empty-session_id broadcast this ordering exists to prevent, and an
    # assertion on the flag alone would not notice.
    dispatch_at = reconnect_block.index("neko-game-window-state-change")
    for assignment in (
        "S.gameRouteActive = true",
        "S.gameRouteGameType = data.game_type",
        "S.gameRouteSessionId = data.session_id",
        "S.gameRouteInstanceId = data.sdk_route_instance_id",
        "S.gameRouteActive = false",
        "S.gameRouteGameType = ''",
        "S.gameRouteSessionId = ''",
        "S.gameRouteInstanceId = ''",
    ):
        assert assignment in reconnect_block, assignment
        assert dispatch_at < reconnect_block.index(assignment), (
            f"appState repair ({assignment}) must follow the dispatch so the "
            "voice bridge keeps its ordering"
        )

def test_reconnect_reconciliation_tombstones_the_route_the_server_finalized():
    """The reconnect snapshot is the compensation path for a missed ``closed``.

    Precisely because the websocket event was lost, no tombstone exists for the
    route this branch clears, so a late ``GAME_VOICE_STT_GATE_ACTIVE`` for it
    would re-activate a dead route on the page -- which suppresses proactive
    chat and auto-goodbye until a full open/close cycle or a reload.

    The identity recorded must come from the server's own snapshot. This read
    can disagree with the socket (character resolution drift), and tombstoning
    the identity the page currently holds would permanently reject the live
    route's real gate -- in browser_fallback mode that means the game never
    receives a transcript.
    """
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    reconnect_block = _block_after(
        source,
        "function syncGameWindowStateOnWsConnect() {",
    )
    closed_branch = reconnect_block[reconnect_block.index("var reconciledWasActive"):]
    assert "advanceGameRouteStateRevision();" in closed_branch, (
        "the reconnect snapshot cleared route state without advancing the "
        "revision, so a snapshot already in flight cannot be recognised as stale"
    )
    assert "rememberEndedGameRouteIdentity(" in closed_branch, (
        "the reconnect snapshot cleared a route without tombstoning it, so a "
        "late STT gate can re-activate it"
    )
    tombstone_call = closed_branch[
        closed_branch.index("rememberEndedGameRouteIdentity("):
    ]
    tombstone_call = tombstone_call[: tombstone_call.index(");")]
    for page_identity in (
        "S.gameRouteGameType",
        "S.gameRouteSessionId",
        "S.gameRouteInstanceId",
    ):
        assert page_identity not in tombstone_call, (
            f"the reconnect tombstone fell back to {page_identity}; only the "
            "server's own finalized identity is safe to record here"
        )
    assert "ended_route" in closed_branch, (
        "the reconnect tombstone must read the identity the server finalized"
    )

@pytest.mark.parametrize("template_name", ["index.html", "chat.html"])
def test_bootstrap_route_snapshot_is_rejected_when_it_lands_late(template_name):
    """The init-time /route/active read must not re-open a route that just closed.

    The request can start while route A is active and resolve after A's `closed`
    websocket event has already been handled; dispatching the snapshot then
    re-opens a dead route on this page, which locks the chat window into its
    collapsed game layout and suppresses proactive chat for the rest of the
    round. Every close path advances the route state revision, so the bootstrap
    compares it across the request the way the reconnect reconciliation in
    app-websocket.js already does.

    Both templates carry their own copy of this IIFE, so both are checked --
    a guard in one of them is a guard in neither for the other window.
    """
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    marker = "fetch('/api/game/route/active?lanlan_name="
    assert source.count(marker) == 1, template_name
    fetch_at = source.index(marker)
    prologue = source[max(0, fetch_at - 1200):fetch_at]
    assert "gameRouteStateRevision" in prologue, (
        f"{template_name} bootstrap does not capture the route state revision "
        "before its /route/active request"
    )
    handler = source[fetch_at:source.index("dispatchEvent", fetch_at)]
    assert "gameRouteStateRevision" in handler, (
        f"{template_name} bootstrap dispatches its snapshot without re-checking "
        "the route state revision, so a snapshot that lands after the route "
        "closed re-opens a dead route"
    )
    assert "return" in handler, (
        f"{template_name} bootstrap compares the revision but never bails out"
    )

def _block_after(js: str, opener: str) -> str:
    """Return the brace-balanced body that follows ``opener``.

    CodeRabbit: ``split("}", 1)[0]`` truncates at the FIRST closing brace in the
    body -- a nested ``if {...}``, an object literal, even a ``}`` inside a
    string -- so the slice can shrink to a line or two and the assertions then
    pass by accident, or miss a real regression. Count braces instead, skipping
    those inside string literals and line comments.

    Two opener shapes are supported: one ending in ``{`` (scope = that block),
    and a plain statement (scope = the rest of its enclosing block). Both leave
    ``depth`` at 1. A TRUNCATED opener is neither -- ``"function foo("`` stops
    before the body brace, so the body's own ``{`` pushes depth to 2 and the
    scan runs past the function into everything that follows it (CodeRabbit
    caught two of these scoped to 1131 lines instead of 29, where the
    assertions could match an unrelated function). An opener with unbalanced
    parentheses is exactly that mistake, so reject it here rather than let a
    later reader rediscover it.
    """

    if opener.count("(") != opener.count(")"):
        raise AssertionError(
            f"opener has unbalanced parentheses, so it stops mid-signature "
            f"and the scan would overrun the block: {opener!r}"
        )
    rest = js.split(opener, 1)[1]
    depth = 1
    out = []
    quote = None
    i = 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if ch == "\\":
                out.append(rest[i : i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "/" and rest[i : i + 2] == "//":
            end = rest.find("\n", i)
            end = len(rest) if end == -1 else end
            out.append(rest[i:end])
            i = end
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(out)
        out.append(ch)
        i += 1
    raise AssertionError(f"unbalanced block after {opener!r}")

def _code_only(js: str) -> str:
    """Strip // line comments so 'does not do X' assertions test code, not prose.

    Several pins in this file assert that a block does NOT call something; a
    comment explaining why it must not would otherwise trip them.
    """

    return "\n".join(line.split("//", 1)[0] for line in js.splitlines())

LOCALES_PATH = Path(__file__).resolve().parents[2] / "static" / "locales"

WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"

ASR_REGISTRY_META_PATH = Path(__file__).resolve().parents[2] / "main_logic" / "asr_client" / "_registry_meta.py"

def _run_settings_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping app-settings harness test")
    # run_node_script writes the script to a temp file: node -e would put the
    # whole harness on the command line, which Windows refuses past 32767
    # characters and which encodes under the locale codec rather than UTF-8.
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

pytestmark = pytest.mark.frontend_contract

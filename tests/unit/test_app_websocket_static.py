import re
from pathlib import Path


APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-websocket.js"
APP_STATE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-state.js"
WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"


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


def test_widget_mode_websocket_events_are_relayed_to_frontend():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    widget_mode_block = source.split(
        "if (typeof response.type === 'string' && response.type.startsWith('widget_mode_'))",
        1,
    )[1].split(
        "// -------- gemini_response --------",
        1,
    )[0]

    assert "new CustomEvent('neko:widget-mode-message'" in widget_mode_block
    assert "detail: response" in widget_mode_block
    assert "return;" in widget_mode_block


def test_websocket_marks_only_widget_mode_pet_pages_as_capable():
    frontend_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    router_source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    assert re.search(
        r"window\.nekoWidgetMode\s*&&\s*window\.nekoWidgetModeHost",
        frontend_source,
    )
    assert "'?widget_mode_capable=1' : ''" in frontend_source
    assert re.search(
        r'websocket\.query_params\.get\(\s*"widget_mode_capable"\s*\)\s*==\s*"1"',
        router_source,
    )
    assert re.search(r"mgr\.widget_mode_capable\s*=\s*widget_mode_capable", router_source)


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

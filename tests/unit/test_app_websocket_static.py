from pathlib import Path


APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[2] / "static" / "app-websocket.js"


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


def test_tutorial_release_greeting_check_bypasses_icebreaker_consumption():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    send_block = source.split("function _sendGreetingCheckIfReady()", 1)[1].split(
        "function _onModelReady()",
        1,
    )[0]
    assert send_block.index("if (_consumeGreetingCheckForNewUserIcebreaker())") < send_block.index(
        "if (_isGreetingCheckBlocked())"
    )

    consume_block = source.split("function _consumeGreetingCheckForNewUserIcebreaker()", 1)[1].split(
        "function _sendGreetingCheckIfReady()",
        1,
    )[0]
    blocking_block = source.split("function isNewUserIcebreakerBlockingGreeting(reason)", 1)[1].split(
        "function normalizeAssistantTurnId(turnId)",
        1,
    )[0]
    assert "if (isTutorialReleaseGreetingReason(normalizedReason))" in blocking_block
    assert "return false;" in blocking_block
    assert "return isNewUserIcebreakerPeriodActive();" in blocking_block
    period_block = source.split("function isNewUserIcebreakerPeriodActive()", 1)[1].split(
        "function isNewUserIcebreakerBlockingGreeting(reason)",
        1,
    )[0]
    assert "readNewUserIcebreakerStore()" in period_block
    assert "window.newUserIcebreaker.getActiveSession()" in period_block
    assert "return false;" in period_block
    assert "if (isTutorialReleaseGreetingReason(S._greetingCheckReason)) return false;" in consume_block
    assert "sendHomeTutorialState(" not in consume_block
    assert "S._greetingCheckPending = false;" in consume_block
    assert "_resetGreetingCheckRetry(true);" in consume_block
    assert "_scheduleGreetingCheckRetry();" not in consume_block
    assert "var greetingReason = S._greetingCheckReason || (greetingIsSwitch ? 'character-switch' : 'ws-open');" in send_block
    assert "sendHomeTutorialState(" not in send_block
    assert "reason: greetingReason" in send_block
    assert "if (S._startupGreetingReleasePending) {" in send_block
    assert send_block.index("if (S._startupGreetingReleasePending)") < send_block.index(
        "if (_consumeGreetingCheckForNewUserIcebreaker())"
    )

    assert "function _isTutorialBlockingGreeting()" not in source
    assert "function isHomeTutorialLockedForGreeting()" not in source


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

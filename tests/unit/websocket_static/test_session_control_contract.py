from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

APP_STATE_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-state.js"

pytestmark = pytest.mark.frontend_contract


def test_rejected_close_events_still_tombstone_their_own_identity():
    """A close event this window rejects still names a dead route.

    ``GAME_ROUTE_ENDED`` and ``game_window_state_change: closed`` are emitted
    only from route finalize, so the identity in the payload is provably dead
    even when it does not match what this window currently holds. Dropping it
    without a tombstone lets a late STT gate for that identity re-activate
    ``S.gameRouteActive`` once the current route also ends -- which suppresses
    proactive chat and auto-goodbye until a full open/close cycle or a reload.

    The tombstone must use the payload's OWN identity: falling back to the
    current one would tombstone the live route and permanently reject its real
    gate, which is the fail-closed direction.
    """
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    rejected_branches = [
        ("忽略过期的 GAME_ROUTE_ENDED | ended_session=", "return;"),
        ("忽略过期的 GAME_ROUTE_ENDED | ended_route=", "return;"),
        ("[GameWindow] 忽略过期窗口事件", "} else if (detail.action === 'opened')"),
    ]
    for marker, terminator in rejected_branches:
        assert source.count(marker) == 1, marker
        # Start after the guard's own console.log, which legitimately prints the
        # live identity it is comparing against.
        start = source.index(");", source.index(marker)) + 2
        end = source.index(terminator, start)
        block = source[start:end]
        assert "rememberEndedGameRouteIdentity(" in block, (
            f"a rejected close event ({marker}) forgot the identity it just refused"
        )
        for live_identity in ("currentSessionId", "currentGameSessionId",
                              "currentRouteInstanceId", "currentGameRouteInstanceId",
                              "S.gameRouteGameType", "S.gameRouteSessionId"):
            assert live_identity not in block, (
                f"a rejected close event ({marker}) tombstoned the live route via "
                f"{live_identity}"
            )


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


def test_session_started_only_settles_the_start_it_answers():
    # Codex P2. The cross-mode guard cannot catch a SAME-mode ack meant for
    # another window, and that is the load-bearing case: the window that claims
    # the microphone mid-start becomes the lease holder, so the in-flight start's
    # ack is fanned out to it. Without this guard it clears its own timeout,
    # resolves, reads the blocked route that ack carries and aborts its
    # microphone flow -- and its real ack, carrying the re-decided route, lands
    # on a flow that already gave up.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    started_handler = websocket_source.split(
        "S.isTextSessionActive = response.input_mode === 'text';", 1
    )[0].rsplit("} else if (response.type === 'session_started') {", 1)[1]
    guard = started_handler.split("var _ackAnswersThisWindow =", 1)[1].split(";", 1)[0]
    assert "response.request_id === S._pendingSessionStartRequestId" in guard
    # Anchored on the resolver first (Codex P2): a dozen places clear the
    # resolver, and expecting every one of them to also clear the id is exactly
    # the checklist that goes stale. A window with no start pending must treat
    # any ack as its own, or a leaked id silently disables the latch forever.
    assert "!S.sessionStartedResolver" in guard
    # When a start request has an id, an ack without an id cannot be attributed
    # to that request. Anonymous acks remain compatible only when there is no
    # pending request id, via the guard below.
    assert "!response.request_id" not in guard
    assert "!S._pendingSessionStartRequestId" in guard

    # Settling is what the guard gates -- the timeout clear and the deferred
    # resolve. The UI sync below it is deliberately NOT gated: the backend did
    # start a session, so composer visibility and the microphone teardown still
    # apply to this window.
    tail = websocket_source.split("var _ackAnswersThisWindow =", 1)[1]
    timeout_clear = tail.split("clearTimeout(window.sessionTimeoutId);", 1)[0]
    assert "_ackAnswersThisWindow && S.sessionStartedResolver" in timeout_clear
    capture = next(l for l in tail.splitlines() if "var _ackedResolver =" in l)
    assert "_ackAnswersThisWindow ?" in capture
    # voiceStartPending is a start-lifecycle flag, not a session fact:
    # app-auto-goodbye.js reads it as "a voice start is in flight", so clearing
    # it on somebody else's ack lets goodbye/idle run through a legitimate mic
    # start that is still waiting for its own ack (Codex P2).
    pending_clear = next(
        l for l in tail.splitlines() if "S.voiceStartPending = false;" in l
    )
    assert "_ackAnswersThisWindow" in pending_clear
    # Session facts stay ungated -- the backend really did start a session.
    session_facts = next(
        l for l in tail.splitlines() if "S.voiceChatActive = response.input_mode" in l
    )
    assert "_ackAnswersThisWindow" not in session_facts


def test_server_side_teardowns_do_not_send_pause_session():
    # A pause_session from a SUPERSEDED recorder socket is not a voice-path
    # message, so the router reads it as a character switch, closes that socket,
    # and its 3s auto-reconnect re-steals the session identity from the window
    # that legitimately owns it. Both server-initiated teardowns must stop the
    # capture without notifying.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    ended_handler = websocket_source.split(
        "} else if (response.type === 'session_ended_by_server') {", 1
    )[1].split("} else if (response.type ===", 1)[0]
    assert "window.stopRecording({ notifyServer: false })" in ended_handler

    auto_close = _block_after(
        websocket_source, "async function resetVoiceUiAfterAutoClose(options) {"
    )
    # Drop recording first so stopMicCapture's own bare stopRecording() hits its
    # !S.isRecording early return and never reaches the pause_session send.
    assert "window.stopRecording({ notifyServer: false });" in auto_close
    assert auto_close.index("window.stopRecording({ notifyServer: false });") < auto_close.index(
        "await window.stopMicCapture();"
    )


def test_deferred_session_start_resolve_is_pinned_to_the_ack_it_belongs_to():
    # Codex P2, twice. A matching session_started clears the start timeout
    # immediately but defers the resolve by 500ms to let the UI settle. The
    # resolver lives in a SHARED slot, and on mobile the composer stays visible
    # during an audio session (the `_shouldHide` guard excludes mobile), so the
    # user can send text inside that window and app-buttons.js then installs a
    # new resolver + mode for the text start.
    #
    # Both halves are load-bearing, and they pull in opposite directions:
    #   * the SLOT must only be cleared while it still holds this ack's start,
    #     or the old audio timer resolves the newer text promise and lets a
    #     queued message go out before the backend acknowledged it;
    #   * the PROMISE must be settled regardless, because its timeout was
    #     already cleared at ack time -- gating the settle on identity too left
    #     the mic-button handler suspended at `await sessionStartPromise`
    #     forever, isMicStarting true and the button stuck.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    capture_line = next(
        l for l in source.splitlines() if "var _ackedResolver =" in l
    )
    assert "S.sessionStartedResolver" in capture_line, (
        "the ack must capture the pending start it belongs to"
    )
    # And only when the ack is answering THIS window's request: a same-mode ack
    # fanned out for somebody else's start must not settle our promise.
    assert "_ackAnswersThisWindow" in capture_line
    capture = capture_line.strip()

    # The capture has to happen at ack time, i.e. before the deferred callback
    # is scheduled -- capturing inside it would read the same shared slot again
    # and pin nothing.
    deferred_end = source.index("}, 500);")
    assert source.index(capture) < deferred_end

    block = source[source.index(capture):deferred_end]
    assert "S.sessionStartedResolver === _ackedResolver" in block, (
        "the shared slot must only be released for the start this ack matched"
    )

    settle = "_ackedResolver(response.input_mode);"
    assert settle in block, "the acknowledged promise must be settled"

    # Structural, not textual: the slot clearing sits INSIDE the identity
    # branch and the settle sits OUTSIDE it, so compare their nesting depth.
    lines = block.splitlines()
    clear_line = next(l for l in lines if "S._pendingSessionStartMode = null;" in l)
    settle_line = next(l for l in lines if settle in l)
    indent = lambda l: len(l) - len(l.lstrip())
    assert indent(clear_line) > indent(settle_line), (
        "clearing the shared slot must be gated on identity while settling the "
        "acknowledged promise must not be"
    )
    assert block.index("S._pendingSessionStartMode = null;") < block.index(settle), (
        "release the slot before settling, so the awaiter never observes a slot "
        "that still points at an already-settled start"
    )


def test_session_started_ack_never_uses_anonymous_id_when_a_start_is_pending():
    """An anonymous ACK cannot settle a start that has an ownership token."""
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    guard = source.split("var _ackAnswersThisWindow =", 1)[1].split(";", 1)[0]

    assert "!S.sessionStartedResolver" in guard
    assert "!S._pendingSessionStartRequestId" in guard
    assert "response.request_id === S._pendingSessionStartRequestId" in guard
    assert "!response.request_id" not in guard
    assert "if (_ackAnswersThisWindow) S.voiceStartPending = false;" in source
    assert "if (_ackAnswersThisWindow && S.sessionStartedResolver" in source
    assert "var _ackedResolver = _ackAnswersThisWindow ?" in source

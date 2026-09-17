import re
from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-audio-capture.js"

pytestmark = pytest.mark.integration_serial


def _code_only(js: str) -> str:
    """Strip // line comments so 'does not do X' assertions test code, not prose.

    Several pins in this file assert that a block does NOT call something; a
    comment explaining why it must not would otherwise trip them.
    """

    return "\n".join(line.split("//", 1)[0] for line in js.splitlines())


def test_text_session_start_stops_an_active_microphone():
    # PR #2345 removed streaming.py's audio-branch session rebuild, so a
    # microphone left running into a text session has every frame accepted at
    # ingress and dropped at routing — no status, no recovery, mic toggle
    # required. The user's most recent explicit action wins: installing a text
    # session stops recording. One-directional on purpose; rebuilding the audio
    # session from the ingress path would re-arm the start_session teardown
    # ping-pong instead.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    started = websocket_source.split(
        "S.isTextSessionActive = response.input_mode === 'text';",
        1,
    )[1].split("var _tiaStarted", 1)[0]

    # CodeRabbit: assert the ENCLOSURE, not three independent substrings. Bare
    # existence checks over the whole block would still pass if the stop call
    # were moved out of the text branch, or if the S.isRecording check belonged
    # to some unrelated path -- exactly the contract this test exists to hold.
    # So slice the smallest guard body and assert the call lives inside it.
    guard_open = "if (response.input_mode === 'text'\n"
    assert guard_open in started, "the teardown must be gated on a text session"
    guard_body = started.split(guard_open, 1)[1].split("\n                    }", 1)[0]

    # Both conditions belong to that one guard, not to separate statements.
    assert "S.isRecording === true" in guard_body
    assert "typeof window.stopRecording === 'function'" in guard_body

    # notifyServer:false is load-bearing, not cosmetic: the default path sends
    # pause_session, which websocket_router.py maps to an ungated end_session()
    # against the text session this very ack just installed, 500 ms before
    # app-buttons.js sends the queued user text.
    assert "window.stopRecording({ notifyServer: false });" in guard_body
    # And the call appears nowhere else in the handler, guarded or not.
    assert started.count("window.stopRecording(") == 1
    assert "window.stopRecording();" not in started
    # stopMicCapture would reject the in-flight text-start promise outright.
    # Match the CALL form: the comment above deliberately names the function.
    assert "window.stopMicCapture(" not in started


def test_blocked_lifecycle_stops_microphone_capture():
    # Codex P2. _handle_core_asr_failure pins the microphone route to "blocked"
    # and nothing re-arms it inside the session, but the frontend only cleared
    # the preview and the route flag: canUploadOrdinaryMicFrame() consults the
    # mic lease and mute/focus, never the lifecycle state, so the hardware
    # microphone (and its OS indicator) stayed open and kept uploading PCM the
    # backend decodes, denoises and VADs before dropping -- while the toast on
    # the very next line says voice input has stopped.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    # The teardown is shared with the STARTUP-failure path, which can never
    # emit a BLOCKED lifecycle event, so it lives in a helper now.
    teardown = websocket_source.split(
        "function tearDownBlockedVoiceRoute() {", 1
    )[1].split("\n    }", 1)[0]
    assert "tearDownBlockedVoiceRoute();" in _block_after(
        websocket_source, "if (lifecycleState === 'blocked') {"
    )

    # Only the capturing window acts, and never while the game STT gate owns
    # the hardware (there the ordinary uplink is already released).
    assert "S.isRecording === true" in teardown
    assert "S.gameVoiceSttGateActive !== true" in teardown
    # stopMicCapture, not bare stopRecording: only it restores the whole
    # non-recording UI rather than leaving it claiming a live voice session.
    assert "window.stopMicCapture" in teardown
    # Teardown precedes the toast so the 5s failure message stays on screen.
    assert websocket_source.index("window.stopMicCapture") < websocket_source.index(
        "microphone.independentAsrFallback"
    )

    # The uplink gate really is lease-only today, which is what makes the
    # teardown necessary. (Gating it on the lifecycle state as well would be a
    # strictly better complementary fix -- this asserts the current shape, it
    # does not forbid that.)
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    can_upload = _block_after(capture_source, "function canUploadOrdinaryMicFrame() {")
    assert "refreshMicLease() !== MIC_LEASE.CORE" in can_upload


def test_cross_mode_session_started_still_stops_the_microphone():
    # The cross-mode ack guard returns early when this window has its own start
    # in flight. In the multi-window sequence "user clicks the mic in A while B
    # sends text", A receives the text session_started with an audio start
    # pending and would return before the teardown -- leaving the hardware mic
    # open and uploading into a route the text session pinned to blocked.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    guard = websocket_source.split(
        "console.log('[App] ignore cross-mode session_started', response.input_mode,", 1
    )[1].split("return;", 1)[0]

    assert "response.input_mode === 'text'" in guard
    assert "S.isRecording === true" in guard
    # Same notifyServer:false reasoning as the main branch: pause_session would
    # end the text session that this very ack just announced.
    assert "window.stopRecording({ notifyServer: false });" in guard


def test_auto_restart_unwinds_a_cancelled_microphone_start():
    # startMicCapture returns false for an ownership cancellation. The restart's
    # backend session has already been accepted by then, so it must enter the
    # common teardown without showing a generic failure toast or continuing to
    # the floating-control/restartComplete success path.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    restart = websocket_source.split("await sessionStartPromise;", 1)[1].split(
        "} catch (error) {", 1
    )[0]
    restart_code = _code_only(restart)
    await_marker = "microphoneStarted = await window.startMicCapture();"
    cancellation_marker = "if (microphoneStarted !== true) {"
    success_marker = "window.syncFloatingMicButtonState(true)"
    assert await_marker in restart_code
    assert cancellation_marker in restart_code
    assert restart_code.index(await_marker) < restart_code.index(cancellation_marker)
    assert restart_code.index(cancellation_marker) < restart_code.index(success_marker)
    assert "microphoneStartCancelled.microphoneStartCancelled = true;" in restart_code
    assert "throw microphoneStartCancelled;" in restart_code

    catch = websocket_source.split("} catch (error) {", 1)[1].split(
        "}, 7500);", 1
    )[0]
    catch_code = _code_only(catch)
    assert "error && error.microphoneStartCancelled" in catch_code
    assert "if (!isMicrophoneStartCancelled" in catch_code
    assert "S.socket.send(JSON.stringify({ action: 'end_session' }));" in catch_code
    assert "window.syncFloatingMicButtonState(false)" in catch_code


def test_microphone_switch_requires_a_live_committed_replacement():
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    select_fn = _code_only(
        _block_after(capture_source, "async function selectMicrophone(deviceId) {")
    )
    await_marker = "const microphoneStarted = await startMicCapture();"
    success_marker = "if (microphoneStarted === true) {"
    retry_marker = "const latestSelectionNeedsRetry = ("
    assert "while (true) {" in select_fn
    assert await_marker in select_fn
    assert success_marker in select_fn
    assert retry_marker in select_fn
    assert select_fn.index(await_marker) < select_fn.index(success_marker)
    assert select_fn.index(success_marker) < select_fn.index(retry_marker)
    retry_condition = select_fn.split(retry_marker, 1)[1].split(");", 1)[0]
    assert (
        "microphoneSelectionGeneration !== selectionGenerationForRestart"
        in retry_condition
    )
    assert "micStartGeneration === expectedRestartGeneration" in retry_condition
    assert "S.voiceInputRouteBlocked !== true" in retry_condition
    assert select_fn.index(retry_marker) < select_fn.index(
        "await window.startScreenSharing();"
    )

    start_fn = _code_only(
        _block_after(capture_source, "async function startMicCapture() {")
    )
    assert "let microphoneSelectionGeneration = 0;" in capture_source
    assert "microphoneSelectionGeneration += 1;" in capture_source
    assert len(re.findall(r"S\.selectedMicrophoneId\s*=(?!=)", capture_source)) == 1, (
        "all microphone-selection writes must go through the generation-tracked helper"
    )
    finish_cancelled = _code_only(
        _block_after(
            capture_source,
            "function finishCancelledMicStart(micElement, micStartToken) {",
        )
    )
    assert (
        start_fn.count(
            "return finishCancelledMicStart(_mic, micStartToken);"
        )
        == 4
    )
    assert "if (hasLiveCommittedMicrophonePipeline()) {" in finish_cancelled
    assert "pendingMicStartUiOwnerToken !== micStartToken" in finish_cancelled
    assert "S.isRecording = false;" in finish_cancelled
    assert "window.isRecording = false;" in finish_cancelled


def test_in_flight_microphone_start_is_cancellable():
    # Codex P2. S.isRecording only flips at the END of startAudioWorklet, after
    # getUserMedia() and audioWorklet.addModule() have both awaited, so every
    # teardown guard keyed on `S.isRecording === true` is a no-op for the whole
    # startup window. The pending start then completed, set recording true and
    # re-claimed via refreshMicLease() the lease the backend had just revoked,
    # uploading PCM into a blocked route.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    # The attempt is claimed before the first await, so the token covers the
    # getUserMedia half of the window too, not just addModule.
    start_fn = _block_after(capture_source, "async function startMicCapture() {")
    assert "micStartGeneration += 1;" in start_fn
    # PER-ATTEMPT local, never a module field. A module-level "pending token" is
    # re-armed by the NEXT startMicCapture -- attempt #1 gets invalidated, #2
    # writes token and generation to the same new value, and #1's guard compares
    # equal again and commits, re-claiming the very lease this counter protects.
    assert "const micStartToken = micStartGeneration;" in start_fn
    assert "pendingMicStartToken" not in capture_source
    # _code_only: the function's own comments mention "await", and an ordering
    # assertion that a comment can satisfy is not an ordering assertion.
    start_code = _code_only(start_fn)
    assert start_code.index("const micStartToken = micStartGeneration;") < start_code.index(
        "await"
    )

    # ...and the commit is gated on it.
    worklet = _block_after(
        capture_source,
        "async function startAudioWorklet(\n"
        "        mediaStream,\n"
        "        startToken,\n"
        "        selectedMicrophoneIdAtStart,\n"
        "        microphoneSelectionGenerationAtStart\n"
        "    ) {",
    )
    assert "startToken !== micStartGeneration" in worklet
    assert "S.voiceInputRouteBlocked === true" in worklet
    assert "S.selectedMicrophoneId !== selectedMicrophoneIdAtStart" in worklet
    assert (
        "microphoneSelectionGeneration !== microphoneSelectionGenerationAtStart"
        in worklet
    )
    # TWO gates on that token, and both are load-bearing. The entry gate stops
    # an attempt that was superseded while still in getUserMedia from running
    # the old-pipeline teardown below it, which would close the WINNER's
    # freshly published AudioContext. The commit gate stops it from publishing.
    assert worklet.count("startToken !== micStartGeneration") == 2, (
        "expected an entry gate and a commit gate on the start token"
    )
    assert worklet.count(
        "S.selectedMicrophoneId !== selectedMicrophoneIdAtStart"
    ) == 2, "expected both gates to enforce microphone-selection ownership"
    assert worklet.count(
        "microphoneSelectionGeneration !== microphoneSelectionGenerationAtStart"
    ) == 2, "expected both gates to preserve intermediate selection changes"
    assert worklet.index("superseded before opening") < worklet.index(
        "await previousContext.close()"
    ), "the entry gate must precede the old-pipeline teardown it protects"
    assert worklet.index("superseded while opening") < worklet.index(
        "S.isRecording = true;"
    )
    # The unwind must NOT re-emit a lease snapshot -- that re-claim is the bug.
    #
    # Sliced from the COMMIT gate's own log line, not from the first
    # occurrence of the token comparison: the entry gate added a second one,
    # and anchoring on the first silently widened this slice to the whole
    # function body, where both assertions below pass for free.
    unwind = worklet.split("superseded while opening", 1)[1].split(
        "S.isRecording = true;", 1
    )[0]
    assert "refreshMicLease()" not in _code_only(unwind)

    # A superseded attempt must REPORT that it unwound. A bare `return` left
    # startMicCapture running its whole success path -- disabling the mic
    # button, toasting "speaking", lighting the floating button and silencing
    # proactive chat -- against hardware the unwind had just torn down.
    assert "return false;" in _code_only(unwind)
    assert "return true;" in _code_only(worklet)
    start_code_only = _code_only(start_fn)
    # The stream is attempt-local now (it used to be published into S.stream
    # before the token gate, where a loser whose getUserMedia settled last
    # could take the slot and then null it out from under the winner), so the
    # handoff goes through the local binding.
    assert "const selectedMicrophoneIdAtStart = S.selectedMicrophoneId;" in start_code_only
    compact_start_code = "".join(start_code_only.split())
    assert (
        "constmicStartCommitted=awaitstartAudioWorklet("
        "ownStream,micStartToken,selectedMicrophoneIdAtStart,"
        "microphoneSelectionGenerationAtStart);"
        in compact_start_code
    )
    assert "if (!micStartCommitted) {" in start_code_only
    # ...and the bail happens before every success-path side effect.
    bail = start_code_only.index("if (!micStartCommitted) {")
    for success_marker in (
        "'app.speaking'",
        "window.syncFloatingMicButtonState(true)",
        "updateMicVolumeStatusNow(true)",
        "window.stopProactiveChatSchedule()",
    ):
        assert bail < start_code_only.index(success_marker), success_marker

    # The fail-closed unwind cancels a pending start as well as a live one.
    abort_fn = _block_after(capture_source, "function abortVoiceStartForBlockedRoute() {")
    assert "invalidatePendingMicStart();" in abort_fn


def test_text_takeover_cancels_a_pending_microphone_start():
    # Both text-session branches stop an ALREADY-recording mic; neither could
    # reach a start still inside its await window.
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    # Count the GUARDED form, not the bare call: `if (false) window.invalid...`
    # keeps the bare substring and would satisfy a looser count.
    guarded = (
        "if (response.input_mode === 'text' "
        "&& typeof window.invalidatePendingMicStart === 'function') "
        "window.invalidatePendingMicStart();"
    )
    assert websocket_source.count(guarded) == 2
    # Each sits with, and before, its paired stopRecording teardown.
    for branch_opener in (
        "console.log('[App] text session installed; stopping the microphone (cross-mode)');",
        "console.log('[App] text session installed; stopping the microphone');",
    ):
        before = websocket_source.split(branch_opener, 1)[0]
        assert "window.invalidatePendingMicStart();" in before

    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    assert "window.invalidatePendingMicStart = invalidatePendingMicStart;" in capture_source

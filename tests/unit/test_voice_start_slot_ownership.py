# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Ownership of the shared voice-start slot.

``S.sessionStartedResolver`` / ``Rejecter`` / ``_pendingSessionStartMode`` are
ONE slot, and concurrent starts genuinely exist: the mic button, the composer's
text send, the avatar-drop text entry and the automatic reconnect restart can
all be in flight together. Every flow used to clear the slot unconditionally on
its way out, so whichever finished first wiped whoever owned it -- the newer
start then hung on a promise nobody would settle, or lost its timeout.

The owner token is the resolver function itself. These cases drive the real
helpers from app-state.js rather than asserting on source text, because the
property that matters is behavioural: a release by a superseded flow must be a
no-op.
"""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

_STATIC_APP = Path(__file__).resolve().parents[2] / "static" / "app"
APP_STATE_PATH = _STATIC_APP / "app-state.js"
START_FLOW_PATHS = (_STATIC_APP / "app-buttons.js", _STATIC_APP / "app-websocket.js")

_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT: ' + msg);
}

// app-state.js is a large IIFE with browser dependencies; the ownership helpers
// and the cancel lever they are defined against are self-contained, so lift
// just that section out and run it against a stub S. cancelPendingSessionStart
// comes along deliberately: the epoch property below is about how the two
// interact, and reimplementing the lever here would test nothing.
const source = fs.readFileSync(__APP_STATE_PATH__, 'utf8');
// makeNekoSessionAbortError comes along because claimSessionStart settles the
// start it displaces with it, and "is that a cancellation or a failure" is the
// property the flows branch on -- stubbing it here would test the stub.
const start = source.indexOf('window.makeNekoSessionAbortError = function');
const end = source.indexOf('// ======================== 工具函数');
assert(start > 0 && end > start, 'could not locate the ownership helpers');

const S = {
  sessionStartedResolver: null,
  sessionStartedRejecter: null,
  _pendingSessionStartMode: null,
  voiceSessionStartEpoch: 0,
  voiceStartPending: false,
};
const sandbox = { S, window: {}, clearTimeout: () => {}, console: { log() {}, warn() {} } };
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox, { filename: 'app-state-helpers.js' });
const W = sandbox.window;

// --- displacing a start settles it ----------------------------------------
// Nothing else can, once it has been displaced: its acknowledgement is dropped
// by the cross-mode guard in the session_started handler, and its 15s timeout
// is cancelled by the claim setup of the very flow that displaced it. Left
// unsettled it sits on `await sessionStartPromise` forever, holding
// window.isMicStarting and an active/disabled mic button through OUR session.
let displacedWith = 'not settled';
W.claimSessionStart('audio', () => {}, (e) => { displacedWith = e; });
W.claimSessionStart('text', () => {}, () => {});
assert(displacedWith !== 'not settled', 'displacing a start must settle it');
assert(displacedWith.sessionStartCancelled === true,
       'settled as a cancellation, so the flows abandon quietly instead of reporting a failure');
assert(displacedWith.voiceStartCancelled === true, 'and the voice flows see the same');

// Claiming an EMPTY slot settles nobody.
W.releaseSessionStart(S.sessionStartedResolver);
let settledFromEmpty = false;
W.claimSessionStart('audio', () => {}, () => { settledFromEmpty = true; });
assert(settledFromEmpty === false, 'claiming an empty slot must not settle anything');
W.releaseSessionStart(S.sessionStartedResolver);

// --- a superseded flow must not release the newer start's slot -------------
const firstResolve = () => {};
const firstReject = () => {};
const firstOwner = W.claimSessionStart('audio', firstResolve, firstReject);
assert(S.sessionStartedResolver === firstResolve, 'the first start claimed the slot');

const secondResolve = () => {};
const secondReject = () => {};
const secondOwner = W.claimSessionStart('text', secondResolve, secondReject);
assert(S.sessionStartedResolver === secondResolve, 'the second start took the slot');
assert(S._pendingSessionStartMode === 'text', 'the mode follows the newest start');

assert(W.sessionStartIsCurrent(firstOwner) === false,
       'the superseded start must not report itself current');
assert(W.sessionStartIsCurrent(secondOwner) === true,
       'the owning start must report itself current');

assert(W.releaseSessionStart(firstOwner) === false,
       'a superseded flow releasing must be refused');
assert(S.sessionStartedResolver === secondResolve,
       'the newer start must still own the slot after a foreign release');
assert(S.sessionStartedRejecter === secondReject, 'and keep its rejecter');
assert(S._pendingSessionStartMode === 'text', 'and keep its mode');

// --- the owner CAN release, exactly once -----------------------------------
assert(W.releaseSessionStart(secondOwner) === true, 'the owner may release');
assert(S.sessionStartedResolver === null, 'the slot is cleared by its owner');
assert(S.sessionStartedRejecter === null, 'rejecter cleared too');
assert(S._pendingSessionStartMode === null, 'mode cleared too');
assert(W.releaseSessionStart(secondOwner) === false,
       'a second release by the same owner is a no-op, not a clear of whoever came next');

// --- a null/absent owner can never clear -----------------------------------
W.claimSessionStart('audio', firstResolve, firstReject);
assert(W.releaseSessionStart(null) === false, 'a missing token must not clear the slot');
assert(W.releaseSessionStart(undefined) === false, 'nor an undefined one');
assert(S.sessionStartedResolver === firstResolve, 'slot survives both');
assert(W.sessionStartIsCurrent(null) === false, 'a missing token is never current');

// --- superseded is about IDENTITY, not mode, and not "not current" ---------
// The takeover guards in the two start flows used to ask
// `_pendingSessionStartMode !== 'audio'`, which is blind to a newer AUDIO
// start -- the automatic reconnect restart claims 'audio' too. The superseded
// flow then fell through and cancelled the newer start's 15s timeout, leaving
// it pending forever when its ack never arrived.
W.releaseSessionStart(firstOwner);
const audioA = () => {};
const ownerA = W.claimSessionStart('audio', audioA, () => {});
assert(W.sessionStartSuperseded(ownerA) === false,
       'the start holding the slot is not superseded');

const audioB = () => {};
const ownerB = W.claimSessionStart('audio', audioB, () => {});
assert(S._pendingSessionStartMode === 'audio',
       'a newer AUDIO start leaves the mode indistinguishable from our own');
assert(W.sessionStartSuperseded(ownerA) === true,
       'an audio start superseded by another audio start must still know it');
assert(W.sessionStartSuperseded(ownerB) === false, 'the newcomer owns the slot');

// An EMPTY slot is NOT superseded: the ack handler releases the slot before it
// settles the promise, so the successful start resumes to an empty slot and
// must still clear the timeout it armed itself. `!sessionStartIsCurrent` here
// would make every successful start believe it had been taken over.
W.releaseSessionStart(ownerB);
assert(S.sessionStartedResolver === null, 'slot is empty');
assert(W.sessionStartSuperseded(ownerB) === false,
       'an empty slot must not read as superseded');
assert(W.sessionStartIsCurrent(ownerB) === false,
       'and the released owner is no longer current -- the two differ here');

// ...but a COMPLETED takeover is still a takeover. B has claimed and released,
// so the slot is empty again and, to anything that only looks at who holds it,
// indistinguishable from A's own release. That is the blind spot that let a
// stale mic start send end_session and reset the UI for the text session which
// had just succeeded inside its getUserMedia await.
assert(W.sessionStartSuperseded(ownerA) === true,
       'a takeover that has already finished must still supersede the start it took over from');

// --- who superseded us decides whether the GLOBAL unwind may run -----------
// abortVoiceStartForBlockedRoute bumps the mic generation and clears
// window.isMicStarting. A newer AUDIO start is sitting on exactly that state
// inside getUserMedia, so unwinding there makes it abandon capture and fail
// its own ensureVoiceStartCurrent -- a session the backend accepted, with the
// microphone closed. A newer TEXT start touches none of it and would instead
// be left with a stranded voice-start UI, so there the unwind must still run.
const audioC = () => {};
const ownerC = W.claimSessionStart('audio', audioC, () => {});
assert(W.supersededByAudioStart(ownerC) === false,
       'the start holding the slot was superseded by nobody');

W.claimSessionStart('audio', () => {}, () => {});
assert(W.supersededByAudioStart(ownerC) === true,
       'an audio takeover must suppress the global voice-start unwind');

// A TEXT takeover with no audio start after us: nobody is driving the voice UI,
// so the unwind must run and hand the mic button back.
const ownerT = W.claimSessionStart('audio', () => {}, () => {});
W.claimSessionStart('text', () => {}, () => {});
assert(W.supersededByAudioStart(ownerT) === false,
       'a TEXT takeover leaves the voice-start UI to us -- the unwind must still run');

const ownerD = S.sessionStartedResolver;
W.releaseSessionStart(ownerD);
assert(W.supersededByAudioStart(ownerT) === false,
       'a text takeover that has completed is still a text takeover -- unwind');

// The mirror case, and the one the pending mode gets wrong: an AUDIO takeover
// that has already been acknowledged and released the slot is MORE alive than a
// pending one, not less. Reading _pendingSessionStartMode here would find null
// and unwind the mic generation out from under a session that is recording.
W.claimSessionStart('audio', () => {}, () => {});
W.releaseSessionStart(S.sessionStartedResolver);
assert(S._pendingSessionStartMode === null, 'the pending mode is gone once released');
assert(W.supersededByAudioStart(ownerT) === true,
       'a completed AUDIO takeover must still suppress the global unwind');

// And the one the LAST claim's mode gets wrong: an audio start still acquiring
// its microphone, followed by a text send. The last claim is text, but the
// audio start is alive and holding exactly the state the unwind destroys, so
// the question is "did an audio start claim after me", not "what claimed last".
const ownerE = W.claimSessionStart('audio', () => {}, () => {});
const ownerF = W.claimSessionStart('audio', () => {}, () => {});
W.claimSessionStart('text', () => {}, () => {});
assert(S._lastSessionStartMode === 'text', 'the newest claim is the text one');
assert(W.supersededByAudioStart(ownerE) === true,
       'an audio start claimed after E and is still live -- E must not unwind');
assert(W.supersededByAudioStart(ownerF) === false,
       'only a text start came after F, which drives none of that state -- F unwinds');

// --- the two signals abandon-detect different things -----------------------
// A cancellation claims nothing, so the claim sequence never moves for it: a
// goodbye, avatar drop or character switch during the restart's 7.5s delay
// leaves the slot untouched and every start's sequence intact. The epoch is
// what sees those -- and it is the automatic restart's only guard, since that
// flow has no ensureVoiceStartCurrent of its own. The mirror hole is above: a
// takeover that claimed and finished moves the sequence and never the epoch,
// which is minted by voice starts only. Neither signal subsumes the other,
// which is why both stand-down checks ask both.
S.voiceSessionStartEpoch = 7;
const restartEpoch = S.voiceSessionStartEpoch;
const ownerR = W.claimSessionStart('audio', () => {}, () => {});
assert(W.voiceStartEpochIsCurrent(restartEpoch) === true,
       'claiming the slot does not by itself move the intent epoch');

W.cancelPendingSessionStart('goodbye');
assert(S.sessionStartedResolver === null, 'the cancel lever cleared the slot');
assert(W.sessionStartSuperseded(ownerR) === false,
       'a cancellation claims nothing, so the claim sequence cannot see it');
assert(W.voiceStartEpochIsCurrent(restartEpoch) === false,
       'the epoch must know the user walked away');

// --- a flow with no owner token yet still has a snapshot -------------------
// The automatic restart sits in a 7.5s timer before it claims anything, and a
// whole text session can start AND finish inside that window: its resolver is
// gone by the time the restart looks, and text never mints an epoch. The claim
// count taken when the restart was scheduled is the only thing that remembers.
const scheduledAt = W.sessionStartClaimSeq();
assert(W.sessionStartsSince(scheduledAt) === false, 'nothing has started yet');

const ghost = W.claimSessionStart('text', () => {}, () => {});
W.releaseSessionStart(ghost);
assert(S.sessionStartedResolver === null, 'the text start finished and released');
assert(W.sessionStartSuperseded(null) === false,
       'with no owner token, "who holds it now" sees nothing at all');
assert(W.sessionStartsSince(scheduledAt) === true,
       'the scheduling snapshot must still see the start that came and went');
assert(W.audioStartsSince(scheduledAt) === false,
       'and must not mistake that text start for a voice one');

W.claimSessionStart('audio', () => {}, () => {});
assert(W.audioStartsSince(scheduledAt) === true,
       'an audio start after the snapshot must suppress the global unwind too');

console.log('HARNESS_OK');
"""

# Both flows funnel every "has someone taken over?" decision through one check.
# Mode alone cannot tell: the automatic restart claims 'audio' exactly like the
# mic button does, and a completed takeover leaves no pending mode at all.
_MODE_TEST = "_pendingSessionStartMode !== 'audio'"

# file -> (stand-down check, how many points in that flow must call it)
#
# app-buttons.js:   after ensureWebSocketOpen, after the start promise, after
#                   getUserMedia, after the proactive-vision acquisition, and in
#                   the outer catch.
# app-websocket.js: before the claim, after ensureWebSocketOpen, after the start
#                   promise, after showCurrentModel, after the capture awaits,
#                   and in the catch.
STAND_DOWN_CHECKS = {
    "app-buttons.js": ("micStartMustStandDown", 5),
    "app-websocket.js": ("restartMustStandDown", 6),
}


def _region(source: str, opening: str, closing: str, where: str) -> str:
    """Slice between two markers, refusing to degrade into "the rest of the file".

    A missing marker makes ``find`` return -1, and ``source[start:-1]`` would then
    hand every later ``in`` assertion nearly the whole file to match against: the
    guard under test could be deleted outright and the case would stay green.
    Anything this returns is bounded by markers that were actually found.
    """
    start = source.find(opening)
    assert start != -1, f"{where}: `{opening}` is gone"
    end = source.find(closing, start)
    assert end != -1, f"{where}: `{closing}` no longer follows `{opening}`"
    return source[start:end]


def _run(script: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping voice-start ownership harness")
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.unit
def test_a_superseded_flow_cannot_release_the_newer_starts_slot():
    # Mutation-verified: drop the identity check in releaseSessionStart and this
    # reddens on "a superseded flow releasing must be refused".
    harness = textwrap.dedent(_HARNESS).replace(
        "__APP_STATE_PATH__", json.dumps(str(APP_STATE_PATH))
    )
    result = _run(harness)
    assert result.returncode == 0, (
        "voice-start ownership harness failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


@pytest.mark.unit
def test_each_flow_decides_takeovers_in_exactly_one_place():
    """Both stand-down checks must ask the whole question.

    Each signal is blind to something the others see, and every round of this
    bug was one await covered by only the blind half: ownership misses a
    takeover that has already finished, the pending mode is null by then, and
    the epoch never moves for a text start at all. The checks also decide
    whether the GLOBAL unwind may run -- it bumps the mic generation and clears
    ``window.isMicStarting``, which is exactly the state a newer audio start is
    sitting on inside getUserMedia.

    Mutation-verified: drop any one signal from either check and this reddens
    naming that file and that signal.
    """
    required = (
        "sessionStartSuperseded",
        _MODE_TEST,
        "supersededByAudioStart",
        "abortVoiceStartForBlockedRoute",
    )
    for path in START_FLOW_PATHS:
        source = path.read_text(encoding="utf-8")
        name = STAND_DOWN_CHECKS[path.name][0]
        body = _region(source, f"function {name}()", "try {", path.name)
        for signal in required:
            assert signal in body, (
                f"{path.name}: {name} ignores `{signal}`, so a takeover it cannot see "
                f"reaches the microphone or tears down the start that took over.\n{body}"
            )


@pytest.mark.unit
def test_every_point_a_flow_resumes_from_an_await_stands_down():
    """One check is worth nothing at the one await that skips it.

    The counts are the awaits each flow resumes from, plus its failure path:
    the mic handler after its start promise, after getUserMedia, and in its
    outer catch; the restart before it claims at all, after its start promise,
    after showCurrentModel, and in its catch. Every one of those was reported
    separately, in three rounds, as its own bug.

    Mutation-verified: delete any single call and this reddens with the count.
    """
    for path in START_FLOW_PATHS:
        source = path.read_text(encoding="utf-8")
        name, expected = STAND_DOWN_CHECKS[path.name]
        calls = source.count(f"{name}()") - 1  # minus the definition
        assert calls >= expected, (
            f"{path.name}: {name} is called at {calls} of the {expected} points this "
            "flow can resume or fail at -- the uncovered one is where a takeover walks "
            "into the microphone or into a foreign session's teardown."
        )


@pytest.mark.unit
def test_standing_down_stops_committed_capture_before_it_unwinds():
    """The unwind on its own leaks the hardware microphone.

    ``abortVoiceStartForBlockedRoute`` clears ``S.isRecording`` without stopping
    the stream, closing the audio context or disconnecting the worklet -- it was
    written for a route that was refused before the mic ever opened. But the
    text ``session_started`` teardown is gated on ``S.isRecording`` being true,
    so unwinding first makes it skip the only pipeline teardown there is, and
    the microphone stays live after the user switched to text.

    Mutation-verified: drop the stopRecording call, or move it after the unwind,
    and this reddens naming that file.
    """
    for path in START_FLOW_PATHS:
        source = path.read_text(encoding="utf-8")
        name = STAND_DOWN_CHECKS[path.name][0]
        body = _region(source, f"function {name}()", "try {", path.name)

        if path.name == "app-buttons.js":
            assert "S.isSwitchingMode = false" in body, (
                "app-buttons.js: standing down returns past both places that clear "
                "S.isSwitchingMode, and this flow may have set it when it began from a "
                "live text session -- left true it suppresses CHARACTER_LEFT handling and "
                f"keeps auto-goodbye treating the app as mid-switch.\n{body}"
            )

        stop = body.find("stopRecording({ notifyServer: false })")
        assert stop != -1, (
            f"{path.name}: {name} unwinds without stopping capture that already "
            f"committed -- the microphone survives the takeover.\n{body}"
        )
        assert "S.isRecording === true" in body, (
            f"{path.name}: the capture teardown in {name} must be gated on there being "
            f"capture to tear down.\n{body}"
        )
        abort = body.find("abortVoiceStartForBlockedRoute()")
        assert abort != -1, f"{path.name}: {name} no longer unwinds at all"
        assert stop < abort, (
            f"{path.name}: {name} unwinds before it stops capture, and the unwind "
            "clears the very flag stopRecording needs -- order matters here."
        )


@pytest.mark.unit
def test_the_mic_flow_rechecks_cancellation_after_proactive_vision():
    """That await needs BOTH questions asked on the other side.

    Proactive vision acquisition spans a backend request and a display-capture
    prompt. A takeover inside it is a new claim, which the stand-down sees; a
    goodbye or reset is not -- cancelPendingSessionStart moves the epoch and
    clears isMicStarting without claiming anything, and only
    ensureVoiceStartCurrent looks at those.

    Mutation-verified: drop either call and this reddens.
    """
    source = (_STATIC_APP / "app-buttons.js").read_text(encoding="utf-8")
    region = _region(
        source, "acquireProactiveVisionStream", "hideVoicePreparingToast", "app-buttons.js"
    )
    for signal in ("ensureVoiceStartCurrent()", "micStartMustStandDown()"):
        assert signal in region, (
            f"app-buttons.js: nothing asks `{signal}` between the proactive-vision await "
            "and the success path, so a voice session the user already ended still gets "
            f"announced.\n{region}"
        )


@pytest.mark.unit
def test_a_displaced_start_is_abandoned_quietly_not_reported_as_a_failure():
    """Being taken over is the user's own next action, not a failure.

    Now that claimSessionStart settles the start it displaces, every flow that
    awaits a start promise can be rejected by a takeover -- and the text flows
    reported that as "启动失败 / 回来失败", carrying the internal English reason
    string into a toast.

    Mutation-verified: remove either quiet-abandon branch and this reddens.
    """
    source = (_STATIC_APP / "app-buttons.js").read_text(encoding="utf-8")
    catches = [
        ("console.askHerBackFailed", "textStartOwner"),
        ("console.startTextSessionFailed", "composerStartOwner"),
    ]
    for marker, owner in catches:
        where = source.find(marker)
        assert where != -1, f"the catch logging {marker} has been rewritten"
        # The decision has to be made before the toast, so look at the window
        # just ahead of it.
        head = source[max(0, where - 900):where]
        assert "sessionStartCancelled" in head and owner in head, (
            f"app-buttons.js: the catch at {marker} reports a takeover as a start failure "
            f"-- with the internal reason string in the toast.\n{head}"
        )


@pytest.mark.unit
def test_the_mic_failure_cleanup_stands_down_before_ending_the_session():
    """A failed start that was superseded must not end the winner's session.

    Gating the slot was never enough: the cleanup also sends ``end_session``,
    calls ``stopRecording`` and rewrites the button row, and after a takeover
    those all land on the start that took over -- frequently the very start
    whose acknowledgement caused this failure.

    Mutation-verified: remove the stand-down call and this reddens.
    """
    source = (_STATIC_APP / "app-buttons.js").read_text(encoding="utf-8")
    guard_region = _region(
        source, "var micStartStillOurs", "action: 'end_session'", "app-buttons.js"
    )
    assert "micStartMustStandDown()" in guard_region, (
        "app-buttons.js: the mic start's failure cleanup reaches end_session without "
        "standing down first -- it would tear down the session of whoever took over.\n"
        f"{guard_region}"
    )


@pytest.mark.unit
def test_the_automatic_restart_stands_down_at_every_resumption_point():
    """Each await this flow resumes from must ask the WHOLE question.

    Neither half is sufficient alone, and asking only one is how this bug kept
    coming back: ownership cannot see a cancel-and-clear (the slot is back to
    empty, exactly like a normal release), and the epoch cannot see a TEXT
    takeover (text starts never mint one, and the disconnect path leaves the
    mobile composer live throughout the showCurrentModel await).

    Mutation-verified: drop either stand-down call, or either half of the
    predicate, and this reddens.
    """
    source = (_STATIC_APP / "app-websocket.js").read_text(encoding="utf-8")
    helper_body = _region(
        source, "function restartMustStandDown()", "try {", "app-websocket.js"
    )
    for signal in (
        "sessionStartSuperseded",
        "voiceStartEpochIsCurrent",
        "sessionStartsSince",
        "audioStartsSince",
    ):
        assert signal in helper_body, (
            f"app-websocket.js: the restart's stand-down check ignores {signal}, so a "
            f"takeover it cannot see reaches the microphone.\n{helper_body}"
        )

    resumption = _region(
        source, "await sessionStartPromise;", "window.startMicCapture()", "app-websocket.js"
    )
    assert resumption.count("restartMustStandDown()") >= 2, (
        "app-websocket.js: the automatic restart resumes twice before opening the "
        "microphone -- from its start promise and from showCurrentModel -- and must "
        f"stand down at both.\n{resumption}"
    )


@pytest.mark.unit
def test_the_restart_takes_both_snapshots_before_the_delay():
    """Snapshot when the restart is DECIDED, not 7.5s later inside the callback.

    Both signals, because the delay is long enough for either kind of event: a
    goodbye or avatar drop bumps the epoch through cancelPendingSessionStart,
    and a whole text session can be started and finished, which moves only the
    claim count. A snapshot taken inside the callback reads whatever happened
    during the delay as its own starting point, so every later check passes and
    the restart proceeds against a user who has already moved on.

    Mutation-verified: move either snapshot inside the callback and this reddens.
    """
    source = (_STATIC_APP / "app-websocket.js").read_text(encoding="utf-8")
    helper = source.find("function restartMustStandDown()")
    assert helper != -1, "the automatic restart's stand-down check has been renamed"
    scheduled = source.rfind("setTimeout(async function ()", 0, helper)
    assert scheduled != -1, "the automatic restart is no longer scheduled on a timer"

    for what, marker in (
        ("intent epoch", "var restartVoiceEpoch = S.voiceSessionStartEpoch;"),
        ("claim sequence", "var restartClaimSeq = window.sessionStartClaimSeq();"),
    ):
        snapshot = source.find(marker)
        assert snapshot != -1, f"the automatic restart no longer snapshots the {what}"
        assert snapshot < scheduled, (
            f"app-websocket.js: the {what} snapshot sits inside the delayed callback, so "
            "whatever happened during the delay becomes its own baseline and every check "
            "against it passes."
        )

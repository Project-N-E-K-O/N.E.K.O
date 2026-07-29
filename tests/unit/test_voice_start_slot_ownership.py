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
import re
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
const start = source.indexOf('window.claimSessionStart = function');
const end = source.indexOf('// ======================== 工具函数');
assert(start > 0 && end > start, 'could not locate the ownership helpers');

const S = {
  sessionStartedResolver: null,
  sessionStartedRejecter: null,
  _pendingSessionStartMode: null,
  voiceSessionStartEpoch: 0,
  voiceStartPending: false,
};
const sandbox = {
  S,
  window: { makeNekoSessionAbortError: (reason) => new Error(reason) },
  clearTimeout: () => {},
  console: { log() {}, warn() {} },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox, { filename: 'app-state-helpers.js' });
const W = sandbox.window;

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

W.claimSessionStart('text', () => {}, () => {});
assert(W.supersededByAudioStart(ownerC) === false,
       'a TEXT takeover leaves the voice-start UI to us -- the unwind must still run');

const ownerD = S.sessionStartedResolver;
W.releaseSessionStart(ownerD);
assert(W.supersededByAudioStart(ownerC) === false,
       'an empty slot means nobody is driving the UI -- the unwind must still run');

// --- the epoch sees the ABA that ownership cannot --------------------------
// The automatic restart has no ensureVoiceStartCurrent of its own, so this is
// the only signal standing between it and reopening the microphone after the
// user has walked away.
S.voiceSessionStartEpoch = 7;
const restartEpoch = S.voiceSessionStartEpoch;
const ownerR = W.claimSessionStart('audio', () => {}, () => {});
assert(W.voiceStartEpochIsCurrent(restartEpoch) === true,
       'claiming the slot does not by itself move the intent epoch');

// A newer start claims inside the ack's deferred window, and then the user
// walks away: goodbye / avatar drop / character switch go through
// cancelPendingSessionStart, which clears the slot outright. Ownership is back
// to EMPTY -- byte for byte what "my own ack released it" looks like.
W.claimSessionStart('audio', () => {}, () => {});
W.cancelPendingSessionStart('goodbye');
assert(S.sessionStartedResolver === null, 'the cancel lever cleared the slot');
assert(W.sessionStartSuperseded(ownerR) === false,
       'ownership alone cannot see the ABA -- that blind spot is the point');
assert(W.voiceStartEpochIsCurrent(restartEpoch) === false,
       'the epoch must still know the user moved on');

console.log('HARNESS_OK');
"""

# Both takeover guards resume from `await sessionStartPromise` and must decide
# whether a newer start has taken the slot. Mode alone cannot tell: the
# automatic restart claims 'audio' exactly like the mic button does.
_MODE_TEST = "_pendingSessionStartMode !== 'audio'"


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
def test_takeover_guards_ask_ownership_not_only_mode():
    """Every "did someone take the slot?" guard must consult the owner token.

    The harness above proves the predicate; this pins the call sites, which is
    where the bug actually lived: both flows resumed from
    ``await sessionStartPromise`` and tested only
    ``_pendingSessionStartMode !== 'audio'``, so a newer AUDIO start (the
    automatic reconnect restart claims 'audio' too) sailed through and the
    superseded flow went on to cancel the newer start's 15s timeout.

    Mutation-verified: drop ``sessionStartSuperseded`` from either guard and
    this reddens naming that file and the surviving mode-only condition.
    """
    checked = 0
    for path in START_FLOW_PATHS:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(re.escape(_MODE_TEST), source):
            head = source[:match.start()]
            condition_start = head.rfind("if (")
            assert condition_start != -1, f"{path.name}: mode test outside any if()"
            condition = source[condition_start:match.end()]
            assert "sessionStartSuperseded" in condition, (
                f"{path.name}: a takeover guard tests the pending mode without asking "
                f"who owns the slot -- a newer AUDIO start passes it.\n{condition}"
            )
            checked += 1
    assert checked >= 2, (
        "expected the mic-button and automatic-restart takeover guards to be found; "
        f"only {checked} matched -- has the guard been rewritten?"
    )


@pytest.mark.unit
def test_takeover_branches_do_not_unwind_under_a_newer_audio_start():
    """The global voice-start unwind must be gated on WHO took the slot.

    ``abortVoiceStartForBlockedRoute`` bumps the mic generation and clears
    ``window.isMicStarting``. Making the takeover branches ownership-aware is
    what made "a newer AUDIO start took over" reachable at all, and that is
    precisely the case where the unwind lands on a start still inside
    getUserMedia: it abandons capture and then fails its own
    ``ensureVoiceStartCurrent``, leaving a session the backend accepted with no
    microphone.

    Mutation-verified: drop the ``supersededByAudioStart`` gate from either
    branch and this reddens naming that file.
    """
    checked = 0
    for path in START_FLOW_PATHS:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(re.escape(_MODE_TEST), source):
            branch_end = source.find("return", match.end())
            assert branch_end != -1, f"{path.name}: takeover branch has no return"
            branch = source[match.end():branch_end]
            if "abortVoiceStartForBlockedRoute" not in branch:
                continue
            assert "supersededByAudioStart" in branch, (
                f"{path.name}: the takeover branch runs the global voice-start unwind "
                "without checking whether a newer AUDIO start is driving that very "
                f"state.\n{branch}"
            )
            checked += 1
    assert checked >= 2, (
        "expected both takeover branches to reach the unwind; "
        f"only {checked} matched -- has the branch been rewritten?"
    )


@pytest.mark.unit
def test_a_superseded_mic_start_does_not_run_its_failure_cleanup():
    """A failed start that no longer owns the slot must not end the session.

    Gating the slot was never enough: the mic handler's failure cleanup also
    sends ``end_session``, calls ``stopRecording`` and rewrites the button row,
    and by then those land on whichever start took over.

    Mutation-verified: remove the superseded early-return and this reddens.
    """
    source = (_STATIC_APP / "app-buttons.js").read_text(encoding="utf-8")
    anchor = source.find("var micStartStillOurs")
    assert anchor != -1, "the mic handler's failure cleanup has been rewritten"
    end_session = source.find("action: 'end_session'", anchor)
    assert end_session != -1, "expected the failure cleanup to send end_session"

    guard_region = source[anchor:end_session]
    assert "sessionStartSuperseded" in guard_region and "return;" in guard_region, (
        "app-buttons.js: the mic start's failure cleanup reaches end_session without "
        "first bailing out when a newer start owns the slot -- it would tear down "
        f"that start's session.\n{guard_region}"
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
    helper = source.find("function restartMustStandDown()")
    assert helper != -1, "the automatic restart's stand-down check has been renamed"
    helper_body = source[helper:source.find("try {", helper)]
    for signal in ("sessionStartSuperseded", "voiceStartEpochIsCurrent"):
        assert signal in helper_body, (
            f"app-websocket.js: the restart's stand-down check ignores {signal}, so a "
            f"takeover it cannot see reaches the microphone.\n{helper_body}"
        )

    resumed = source.find("await sessionStartPromise;", helper)
    assert resumed != -1, "the automatic restart no longer awaits its start promise"
    opens_mic = source.find("window.startMicCapture()", resumed)
    assert opens_mic != -1, "the automatic restart no longer opens the mic"

    resumption = source[resumed:opens_mic]
    assert resumption.count("restartMustStandDown()") >= 2, (
        "app-websocket.js: the automatic restart resumes twice before opening the "
        "microphone -- from its start promise and from showCurrentModel -- and must "
        f"stand down at both.\n{resumption}"
    )


@pytest.mark.unit
def test_the_restart_snapshots_its_epoch_before_the_delay():
    """Snapshot when the restart is DECIDED, not 7.5s later inside the callback.

    A goodbye or avatar drop during the delay bumps the epoch through
    cancelPendingSessionStart. A snapshot taken inside the callback reads that
    cancellation as its own starting point, so every check against it passes and
    the restart proceeds against a user who has already walked away.

    Mutation-verified: move the snapshot inside the callback and this reddens.
    """
    source = (_STATIC_APP / "app-websocket.js").read_text(encoding="utf-8")
    snapshot = source.find("var restartVoiceEpoch = S.voiceSessionStartEpoch;")
    assert snapshot != -1, "the automatic restart no longer snapshots the intent epoch"
    helper = source.find("function restartMustStandDown()")
    assert helper != -1, "the automatic restart's stand-down check has been renamed"
    scheduled = source.rfind("setTimeout(async function ()", 0, helper)
    assert scheduled != -1, "the automatic restart is no longer scheduled on a timer"

    assert snapshot < scheduled, (
        "app-websocket.js: the epoch snapshot sits inside the delayed callback, so a "
        "cancellation during the delay becomes its own baseline and every check "
        "against it passes."
    )

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
"""The voice-start teardown paths, driven rather than read.

``test_voice_start_slot_ownership`` pins the ownership helpers themselves and
then asserts, on source text, that each flow's stand-down check mentions every
signal. That catches a deleted call; it cannot catch a stand-down that consults
all four signals and still reaches the wrong verdict, and the verdict is the
whole product: stand down or keep going, and -- when standing down -- whether
the GLOBAL voice-start unwind may run over whoever took over.

So these cases run the two stand-down checks for real. Each one is lifted
verbatim out of its flow and given the closure variables its flow would have
(the owner token, the epoch and claim-count snapshots), against the real
``app-state.js`` helpers. What is simulated is only the ORDER of events on the
shared slot, which is exactly what each teardown path is:

  cross-mode restart          an audio start superseded by a text send, and the
                              same superseded by the automatic audio restart
  CHARACTER_DISCONNECTED      the 7.5s delay, and what may happen inside it
  goodbye / cat mode          cancelPendingSessionStart after a takeover
  avatar drop to text         the same cancellation followed by a text claim
  disconnect onclose          the resolver killed with the mode left behind

Every case here is mutation-verified against the flow it guards; the docstrings
name the mutation.
"""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

_STATIC_APP = Path(__file__).resolve().parents[2] / "static" / "app"
APP_STATE_PATH = _STATIC_APP / "app-state.js"
APP_BUTTONS_PATH = _STATIC_APP / "app-buttons.js"
APP_WEBSOCKET_PATH = _STATIC_APP / "app-websocket.js"
APP_AUDIO_CAPTURE_PATH = _STATIC_APP / "app-audio-capture.js"

# Shared prologue: the real ownership helpers, the two real stand-down checks,
# and a fresh environment per scenario so one scenario's claim sequence cannot
# leak into the next.
_PRELUDE = r"""
const fs = require('node:fs');
const vm = require('node:vm');

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT: ' + msg);
}

// app-state.js is a large IIFE with browser dependencies; the ownership helpers
// and the cancel lever are self-contained, so lift just that section. The lever
// comes along deliberately -- goodbye, avatar drop and character switch all go
// through it, and reimplementing it here would test the reimplementation.
const stateSource = fs.readFileSync(__APP_STATE_PATH__, 'utf8');
const helpersFrom = stateSource.indexOf('window.makeNekoSessionAbortError = function');
const helpersTo = stateSource.indexOf('// ======================== 工具函数');
assert(helpersFrom > 0 && helpersTo > helpersFrom, 'could not locate the ownership helpers');
const HELPERS = stateSource.slice(helpersFrom, helpersTo);

// Each stand-down check is a nested function declared immediately before the
// `try {` of the flow it guards. Lift it verbatim: a paraphrase here would pass
// while the shipped predicate said something else.
function liftStandDown(path, name) {
  const source = fs.readFileSync(path, 'utf8');
  const from = source.indexOf('function ' + name + '()');
  assert(from !== -1, name + ' is gone from ' + path);
  const to = source.indexOf('try {', from);
  assert(to > from, name + ' is no longer the guard in front of its flow');
  const region = source.slice(from, to);
  const close = region.lastIndexOf('}');
  assert(close !== -1, name + ' has no body');
  return region.slice(0, close + 1);
}

const MIC_STAND_DOWN = liftStandDown(__APP_BUTTONS_PATH__, 'micStartMustStandDown');
const RESTART_STAND_DOWN = liftStandDown(__APP_WEBSOCKET_PATH__, 'restartMustStandDown');

// The onclose cleanup is a plain block inside S.socket.onclose rather than a
// named function, so it is taken by its markers. Lifted for the same reason as
// the two checks above: a hand-written copy of "what onclose does" keeps
// asserting the copy after the real one changes, and the whole point of these
// cases is which state that block leaves behind.
function liftRegion(path, from, to, what) {
  const source = fs.readFileSync(path, 'utf8');
  const start = source.indexOf(from);
  assert(start !== -1, what + ': `' + from + '` is gone');
  assert(source.indexOf(from, start + 1) === -1, what + ': `' + from + '` is no longer unique');
  const end = source.indexOf(to, start);
  assert(end > start, what + ': `' + to + '` no longer follows it');
  return source.slice(start, end);
}

const ONCLOSE_CLEANUP = liftRegion(
  __APP_WEBSOCKET_PATH__,
  '// Clean up session Promise',
  '// Clear audio queue',
  'the onclose session cleanup'
);

function makeEnv() {
  const S = {
    sessionStartedResolver: null,
    sessionStartedRejecter: null,
    _pendingSessionStartMode: null,
    _pendingSessionStartRequestId: null,
    _lastSessionStartMode: null,
    voiceSessionStartEpoch: 0,
    voiceStartPending: false,
    isRecording: false,
    isSwitchingMode: false,
  };
  const sandbox = { S, window: {}, clearTimeout: () => {}, console: { log() {}, warn() {} } };
  vm.createContext(sandbox);
  vm.runInContext(HELPERS, sandbox, { filename: 'app-state-helpers.js' });
  const W = sandbox.window;

  // What the global unwind and the capture teardown DID, and in which order.
  // abortVoiceStartForBlockedRoute really does clear S.isRecording without
  // stopping the stream, which is why the order matters: the text
  // session_started teardown is gated on that flag.
  const env = { S, W, order: [], stopArgs: [], goodbye: false };
  W.abortVoiceStartForBlockedRoute = function () {
    env.order.push('unwind');
    S.isRecording = false;
  };
  W.stopRecording = function (options) {
    env.order.push('stop');
    env.stopArgs.push(options);
  };
  W.isNekoGoodbyeModeActive = function () {
    return env.goodbye;
  };
  return env;
}

// The mic flow closes over `micStartOwner` (null until it claims) and the epoch
// it minted when the button was pressed.
function micFlow(env, voiceStartEpoch) {
  const build = new Function(
    'S', 'window', 'voiceStartEpoch',
    'var micStartOwner = null;' + MIC_STAND_DOWN
      + ';return { standDown: micStartMustStandDown,'
      + ' claim: function (owner) { micStartOwner = owner; } };'
  );
  return build(env.S, env.W, voiceStartEpoch);
}

// The automatic restart closes over two snapshots taken when the restart was
// DECIDED, plus an owner token it does not have until 7.5s later.
function restartFlow(env, restartVoiceEpoch, restartClaimSeq) {
  const build = new Function(
    'S', 'window', 'restartVoiceEpoch', 'restartClaimSeq',
    'var restartStartOwner = null;' + RESTART_STAND_DOWN
      + ';return { standDown: restartMustStandDown,'
      + ' claim: function (owner) { restartStartOwner = owner; } };'
  );
  return build(env.S, env.W, restartVoiceEpoch, restartClaimSeq);
}

function claim(env, mode) {
  return env.W.claimSessionStart(mode, function () {}, function () {});
}

// Run the real block, with the socket's own timer handle in place so the timer
// clear is exercised rather than assumed.
function runOncloseCleanup(env) {
  const run = new Function('S', 'window', 'console', 'clearTimeout', ONCLOSE_CLEANUP);
  run(
    env.S,
    env.W,
    { log() {} },
    function (handle) { env.order.push('clearTimeout:' + handle); }
  );
}
"""

_CROSS_MODE = r"""
// ---------------------------------------------------------------------------
// Cross-mode restart: audio start, text send, back to audio.
// ---------------------------------------------------------------------------
// On mobile the composer stays visible during an audio session, so the user can
// send text inside the ack's 500ms settle window or inside getUserMedia. The
// text start claims the slot, is acknowledged, and RELEASES it -- so by the time
// the mic flow resumes the slot is empty and looks exactly like its own release.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  // This mic start began from a live text session, and it committed capture
  // before the takeover landed.
  env.S.isSwitchingMode = true;
  env.S.isRecording = true;

  const textOwner = claim(env, 'text');
  env.W.releaseSessionStart(textOwner);
  assert(env.S.sessionStartedResolver === null, 'the text start finished and released');

  assert(mic.standDown() === true,
         'a completed TEXT takeover must still stop the mic flow -- otherwise it opens the '
         + "microphone onto the text session's blocked route");
  assert(env.order.join(',') === 'stop,unwind',
         'capture must be stopped BEFORE the unwind: the unwind clears S.isRecording without '
         + 'touching the stream, and the text teardown is gated on that flag, so unwinding '
         + 'first leaves the hardware microphone live (got: ' + env.order.join(',') + ')');
  assert(env.stopArgs[0] && env.stopArgs[0].notifyServer === false,
         'the newer start owns the socket -- a pause_session from here is read as a character '
         + 'switch and closes it out from under them');
  assert(env.S.isSwitchingMode === false,
         'standing down returns past both places that clear S.isSwitchingMode; left true it '
         + 'suppresses CHARACTER_LEFT handling and keeps auto-goodbye treating the app as '
         + 'mid-switch forever');
}

// The dual, and the one a `mode !== 'audio'` test cannot see: the automatic
// restart claims 'audio' exactly like the mic button does.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));
  env.S.isSwitchingMode = true;
  env.S.isRecording = true;

  claim(env, 'audio');

  assert(mic.standDown() === true, 'a newer AUDIO start must stop this one too');
  assert(env.order.length === 0,
         'the unwind is GLOBAL -- it bumps the mic generation and clears isMicStarting, which '
         + 'is the state the newer audio start is sitting on inside getUserMedia. Running it '
         + 'there leaves a backend-accepted session with the microphone closed (ran: '
         + env.order.join(',') + ')');
  assert(env.S.isSwitchingMode === true,
         'only the newer AUDIO start clears S.isSwitchingMode, through its own success or '
         + 'failure path -- clearing it here would race that');
}

// The audio takeover that has already been ACKNOWLEDGED is more alive than a
// pending one, not less: it is recording. The pending mode is null by then, so
// reading it here unwinds the mic generation out from under a live session.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  env.W.releaseSessionStart(claim(env, 'audio'));
  assert(env.S._pendingSessionStartMode === null, 'the takeover released the slot on its ack');

  assert(mic.standDown() === true, 'a completed audio takeover still stops this flow');
  assert(env.order.length === 0,
         'and still forbids the unwind -- that session is recording (ran: '
         + env.order.join(',') + ')');
}

// An audio start still acquiring its microphone, followed by a text send. The
// LAST claim is text, but the audio start is alive and holding exactly the
// state the unwind destroys -- the question is "did an audio start claim after
// me", not "what claimed last".
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  claim(env, 'audio');
  claim(env, 'text');
  assert(env.S._pendingSessionStartMode === 'text', 'the newest claim is the text one');

  assert(mic.standDown() === true, 'this flow stops either way');
  assert(env.order.length === 0,
         'but the audio start behind that text send is still acquiring media, and the unwind '
         + 'would make it abandon capture (ran: ' + env.order.join(',') + ')');
}

// And the case that must NOT stand down: nobody took over.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  const owner = claim(env, 'audio');
  mic.claim(owner);
  assert(mic.standDown() === false, 'an unchallenged start must proceed');

  // The success path releases the slot inside the ack handler BEFORE settling
  // the promise, so a start that simply succeeded also resumes to an empty slot.
  env.W.releaseSessionStart(owner);
  assert(mic.standDown() === false,
         'a start that merely succeeded must not read its own release as a takeover -- it '
         + 'still has a timeout to clear and a microphone to open');
}
"""

_AUTOMATIC_RESTART = r"""
// ---------------------------------------------------------------------------
// CHARACTER_DISCONNECTED: the 7.5s delay before the restart claims anything.
// ---------------------------------------------------------------------------
// Both snapshots are taken where the restart is DECIDED. Inside the delay the
// user can do anything, and the restart has no owner token to compare against
// until it claims -- the snapshots are all it has.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());
  assert(restart.standDown() === false, 'a quiet delay must let the restart proceed');

  const owner = claim(env, 'audio');
  restart.claim(owner);
  assert(restart.standDown() === false, 'and it must not stand down against its own claim');

  env.W.releaseSessionStart(owner);
  assert(restart.standDown() === false, 'nor against its own release once acknowledged');
}

// A whole text session started AND finished inside the delay: its resolver is
// gone, and text never mints an epoch, so only the claim-count snapshot
// remembers it happened at all.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());

  env.W.releaseSessionStart(claim(env, 'text'));
  assert(env.S.sessionStartedResolver === null, 'the text session came and went');
  assert(env.W.voiceStartEpochIsCurrent(5) === true, 'and left the voice epoch untouched');

  assert(restart.standDown() === true,
         'the restart must not reconnect a voice session over the text session the user '
         + 'started during the delay');
  assert(env.order.join(',') === 'unwind',
         'a text start drives none of the voice-start UI, so the unwind must run and hand the '
         + 'mic button back (ran: ' + env.order.join(',') + ')');
}

// The same, but the takeover is a mic press: the unwind must NOT run.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());

  claim(env, 'audio');

  assert(restart.standDown() === true, 'a newer audio start owns the restart out of the way');
  assert(env.order.length === 0,
         'that start is mid-getUserMedia on the very state the unwind destroys (ran: '
         + env.order.join(',') + ')');
}

// Capture committed during the delay -- a restart that already opened the mic on
// an earlier pass -- must be stopped before the unwind, same as the mic flow.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());
  env.S.isRecording = true;

  env.W.releaseSessionStart(claim(env, 'text'));

  assert(restart.standDown() === true, 'the text takeover stops the restart');
  assert(env.order.join(',') === 'stop,unwind',
         'the unwind clears S.isRecording without stopping the stream, and the text teardown '
         + 'is gated on it -- stopping after the unwind leaks the microphone (got: '
         + env.order.join(',') + ')');
  assert(env.stopArgs[0] && env.stopArgs[0].notifyServer === false,
         'the newer start owns the socket');
}
"""

_CANCELLATION = r"""
// ---------------------------------------------------------------------------
// goodbye / cat mode, and avatar drop to text: a cancellation, not a takeover.
// ---------------------------------------------------------------------------
// cancelPendingSessionStart claims nothing, so the claim sequence never moves
// for it. On its own it is the epoch's job, and the mic flow catches it with
// ensureVoiceStartCurrent rather than here.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  env.W.cancelPendingSessionStart('Voice start cancelled by goodbye');
  assert(env.W.voiceStartEpochIsCurrent(3) === false, 'the lever moved the intent epoch');
  assert(mic.standDown() === false,
         'a bare cancellation is not a takeover: nobody claimed, so there is nobody for this '
         + 'flow to stand down against -- ensureVoiceStartCurrent is what stops it');
  assert(env.order.length === 0, 'and nothing may be unwound on that verdict');
}

// Takeover FIRST, then goodbye. The claim sequence still says "superseded", and
// unwinding now would re-enable the mic button and unhide the composer on top of
// the goodbye UI -- while the early return skips the catch's preserveGoodbyeUi
// handling that would have put it back.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));
  env.S.isSwitchingMode = true;

  env.W.releaseSessionStart(claim(env, 'text'));
  env.goodbye = true;

  assert(mic.standDown() === true, 'the flow still has to stop');
  assert(env.order.length === 0,
         'goodbye is the LATER intent and has already put its own UI on screen -- the unwind '
         + 'would overwrite it (ran: ' + env.order.join(',') + ')');
  assert(env.S.isSwitchingMode === false,
         'S.isSwitchingMode is still cleared on the way out: the goodbye check sits after it, '
         + 'and leaving it true is permanent');
}

// Avatar drop: the same shape without goodbye mode. The drop cancels the pending
// voice start (bumping the epoch) and enters text, so the epoch half of the
// check is what has to see it.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  env.W.cancelPendingSessionStart('avatar drop');
  claim(env, 'text');

  assert(env.goodbye === false, 'this path never enters goodbye mode');
  assert(mic.standDown() === true, 'the drop moved on and the text start took the slot');
  assert(env.order.length === 0,
         'the drop already re-dressed the UI for text; unwinding would re-enable the mic '
         + 'button on top of it (ran: ' + env.order.join(',') + ')');
}

// The automatic restart has no ensureVoiceStartCurrent of its own, so for it a
// bare cancellation during the delay MUST be caught here.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());

  env.W.cancelPendingSessionStart('Voice start cancelled by goodbye');

  assert(restart.standDown() === true,
         'the user walked away during the delay -- restarting would reopen a session they '
         + 'just ended');
  assert(env.order.length === 0,
         'the cancel lever already unwound the UI (ran: ' + env.order.join(',') + ')');
}

// Takeover then cancellation, for the restart: same precedence as the mic flow.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 5;
  const restart = restartFlow(env, 5, env.W.sessionStartClaimSeq());

  env.W.releaseSessionStart(claim(env, 'text'));
  env.W.cancelPendingSessionStart('Voice start cancelled by goodbye');

  assert(restart.standDown() === true, 'still has to stop');
  assert(env.order.length === 0,
         'the cancellation is the later intent and has already re-dressed the UI (ran: '
         + env.order.join(',') + ')');
}
"""

_DISCONNECT_ONCLOSE = r"""
// ---------------------------------------------------------------------------
// Disconnect onclose, then the automatic restart it schedules.
// ---------------------------------------------------------------------------
// The onclose cleanup rejects the pending start and nulls the resolver and
// rejecter -- but NOT _pendingSessionStartMode or _pendingSessionStartRequestId.
// That asymmetry is load-bearing for the restart's stand-down check, so pin the
// premise rather than trusting the comment that states it.
{
  const env = makeEnv();

  // The start that was in flight when the socket dropped. It claimed BEFORE the
  // disconnect, so the restart's snapshot -- taken where CHARACTER_DISCONNECTED
  // is handled -- already includes it. Taking it any earlier would make the
  // restart read the very start it is replacing as a takeover.
  let killedWith = null;
  const micOwner = env.W.claimSessionStart('audio', function () {}, function (e) { killedWith = e; });

  const disconnectedAt = env.W.sessionStartClaimSeq();
  const restart = restartFlow(env, env.S.voiceSessionStartEpoch, disconnectedAt);

  // The 15s start timeout this flow armed, so the cleanup has one to cancel.
  env.W.sessionTimeoutId = 'start-timeout';

  runOncloseCleanup(env);

  assert(killedWith !== null,
         'onclose must settle the pending start, or its flow waits on that promise forever '
         + 'with the mic button stuck active/disabled');
  assert(env.S.sessionStartedResolver === null && env.S.sessionStartedRejecter === null,
         'and must vacate the slot');
  assert(env.order.join(',') === 'clearTimeout:start-timeout',
         'and cancel the start timeout, which would otherwise fire into a dead socket (ran: '
         + env.order.join(',') + ')');
  assert(env.W.sessionTimeoutId === null, 'the handle goes with it');
  assert(env.S._pendingSessionStartMode === 'audio',
         'onclose leaves the pending mode behind -- the restart check reads it, so if this '
         + 'ever changes that check changes meaning with it');

  // 7.5s later the restart runs. A disconnect is not a takeover: nobody claimed
  // and the epoch never moved, so it must proceed and rebuild the session.
  assert(restart.standDown() === false,
         'the disconnect that scheduled this restart must not also cancel it');

  const restartOwner = claim(env, 'audio');
  restart.claim(restartOwner);
  assert(restart.standDown() === false, 'and it must not stand down against its own claim');

  // The killed mic flow finally reaches its catch. Every teardown it does is
  // gated on ownership, and it owns nothing now.
  assert(env.W.sessionStartIsCurrent(micOwner) === false,
         'the start onclose killed must not report itself current');
  assert(env.W.releaseSessionStart(micOwner) === false,
         "and must not clear the restart's slot -- or the restart hangs on a promise nobody "
         + 'settles, exactly as before the slot had an owner');
  assert(env.S.sessionStartedResolver === restartOwner, 'the restart still owns the slot');
}

// The mode left behind is the flow's OWN mode, so a disconnected TEXT start
// leaves 'text' there. The restart's `_pendingSessionStartMode !== 'audio'` arm
// then reads that stale value and stands down -- recorded here as the observed
// consequence of the asymmetry above, not as a property worth having.
{
  const env = makeEnv();

  env.W.claimSessionStart('text', function () {}, function () {});
  const disconnectedAt = env.W.sessionStartClaimSeq();
  const restart = restartFlow(env, env.S.voiceSessionStartEpoch, disconnectedAt);

  // No armed timer here, so env.order stays free for the unwind assertion below.
  runOncloseCleanup(env);

  assert(env.S._pendingSessionStartMode === 'text', 'the dead text start left its mode behind');
  assert(env.W.sessionStartsSince(disconnectedAt) === false,
         'nothing claimed after the disconnect -- the stale mode is the only thing left');
  assert(env.W.voiceStartEpochIsCurrent(env.S.voiceSessionStartEpoch) === true,
         'and nobody cancelled either');
  assert(restart.standDown() === true,
         'the restart stands down anyway, on the stale mode alone');
  assert(env.order.join(',') === 'unwind',
         'and unwinds, because a stale text mode is indistinguishable from a live text start '
         + '(ran: ' + env.order.join(',') + ')');
}
"""

_EPOCH_IS_VOICE_ONLY = r"""
// ---------------------------------------------------------------------------
// voiceSessionStartEpoch means "the newest VOICE start intent", and only that.
// ---------------------------------------------------------------------------
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;

  claim(env, 'text');
  assert(env.S.voiceSessionStartEpoch === 3, 'a text claim must not mint a voice epoch');
  claim(env, 'audio');
  assert(env.S.voiceSessionStartEpoch === 3,
         'nor does claiming the slot at all -- the mic button mints it before it claims');

  env.W.cancelPendingSessionStart('goodbye');
  assert(env.S.voiceSessionStartEpoch === 4,
         'the cancel lever is the other mover: that is what makes a moved epoch mean '
         + '"the user walked away"');
}

// What a text start minting an epoch would cost, executed rather than argued:
// the mic flow's stand-down reads a moved epoch as a cancellation and returns
// BEFORE the unwind, so a plain text takeover would stop handing the mic button
// back -- the regression the ownership work exists to prevent.
{
  const env = makeEnv();
  env.S.voiceSessionStartEpoch = 3;
  const mic = micFlow(env, 3);
  mic.claim(claim(env, 'audio'));

  env.W.releaseSessionStart(claim(env, 'text'));
  env.S.voiceSessionStartEpoch += 1;  // <- if the text claim above had minted one

  assert(mic.standDown() === true, 'it still stops, so the damage is silent');
  assert(env.order.length === 0,
         'and the unwind is lost: the moved epoch is indistinguishable from a goodbye, so the '
         + 'mic button stays active/disabled with no voice session behind it');
}
"""

_TAIL = "\nconsole.log('HARNESS_OK');\n"


def _run(script: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping voice-start teardown harness")
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _harness(*sections: str) -> str:
    script = textwrap.dedent(_PRELUDE + "".join(sections) + _TAIL)
    return (
        script.replace("__APP_STATE_PATH__", json.dumps(str(APP_STATE_PATH)))
        .replace("__APP_BUTTONS_PATH__", json.dumps(str(APP_BUTTONS_PATH)))
        .replace("__APP_WEBSOCKET_PATH__", json.dumps(str(APP_WEBSOCKET_PATH)))
    )


def _drive(*sections: str) -> None:
    result = _run(_harness(*sections))
    assert result.returncode == 0, (
        "voice-start teardown harness failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


@pytest.mark.unit
def test_cross_mode_restart_stands_down_and_unwinds_only_for_a_text_takeover():
    """text -> audio -> text, and what the mic flow may touch on its way out.

    Mutation-verified: swap ``supersededByAudioStart`` for the pending-mode test
    in ``micStartMustStandDown`` and the audio-takeover case reddens on the
    unwind; move the ``stopRecording`` call after the unwind and the first case
    reddens on the order; drop ``S.isSwitchingMode = false`` and it reddens on
    the flag.
    """
    _drive(_CROSS_MODE)


@pytest.mark.unit
def test_the_automatic_restart_survives_anything_that_happens_in_its_delay():
    """CHARACTER_DISCONNECTED schedules 7.5s ahead; the user does not wait.

    Mutation-verified: replace the pre-claim ``sessionStartsSince`` branch with
    an ownership test and the completed-text-session case reddens; replace
    ``audioStartsSince`` with ``sessionStartsSince`` and the mic-press case
    reddens on the unwind.
    """
    _drive(_AUTOMATIC_RESTART)


@pytest.mark.unit
def test_a_cancellation_after_a_takeover_outranks_it():
    """goodbye, cat mode and avatar drop are the LATER intent.

    They also claim nothing, so the claim sequence cannot see them at all -- a
    start superseded before the cancellation is still superseded after it, and
    unwinding on that verdict paints over the UI the cancellation just put up.

    Mutation-verified: move either cancellation check below the unwind in either
    stand-down function and this reddens naming that path; drop
    ``voiceStartEpochIsCurrent`` from the restart's check and the bare-goodbye
    case reddens.
    """
    _drive(_CANCELLATION)


@pytest.mark.unit
def test_the_onclose_cleanup_does_not_disarm_the_restart_it_schedules():
    """The disconnect kills the pending start, then rebuilds through the restart.

    The cleanup is lifted out of ``S.socket.onclose`` and executed, not
    reproduced here: a hand-written copy of what onclose does goes on asserting
    the copy after the real block changes, and what that block leaves behind is
    the entire subject of these cases.

    Mutation-verified: make the onclose cleanup null ``_pendingSessionStartMode``
    as well and this reddens on the premise the restart's mode arm reads; stop it
    rejecting the pending start and it reddens on the flow left hanging; drop the
    ownership gate from ``releaseSessionStart`` and the killed flow clears the
    restart's slot.
    """
    _drive(_DISCONNECT_ONCLOSE)


@pytest.mark.unit
def test_only_a_voice_start_mints_the_voice_start_epoch():
    """The epoch is the "user walked away" signal, and text claims must not move it.

    Two consumers depend on that meaning. Both stand-down checks read a moved
    epoch as a cancellation and return before the global unwind, so a text start
    minting one would silently stop handing the mic button back after a text
    takeover. And ``markVoiceSettingsPending`` in app-audio-capture.js encodes
    "applies from the next voice start" as ``voiceSessionStartEpoch + 1``, so a
    text send would consume a pending voice-route change that never took effect.

    Mutation-verified: mint an epoch inside ``claimSessionStart`` and this
    reddens twice -- on the mint itself and on the lost unwind.
    """
    _drive(_EPOCH_IS_VOICE_ONLY)


@pytest.mark.unit
def test_the_voice_settings_gate_is_the_epochs_second_consumer():
    """Named here because the harness above cannot see it.

    ``markVoiceSettingsPending`` targets ``voiceSessionStartEpoch + 1`` and both
    consumers release the pending state once the live epoch reaches it. That is
    a second, independent reason the epoch may only move for voice starts, and
    it lives in a different file from everything else in this suite.
    """
    source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    assert "var targetEpoch = (Number(S.voiceSessionStartEpoch) || 0) + 1;" in source, (
        "app-audio-capture.js: the pending voice-settings gate no longer predicts the next "
        "voice epoch -- if it now keys off something else, the epoch's voice-only meaning "
        "has one fewer reason to hold and this suite's reasoning needs revisiting."
    )
    assert source.count("S.voiceSettingsPendingUntilEpoch = null;") >= 2, (
        "app-audio-capture.js: the pending voice-settings gate is released in fewer places "
        "than the two that read the epoch against it."
    )

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
"""Behavioral cover for overlapping ``startMicCapture()`` attempts.

``startAudioWorklet`` gained a per-attempt cancellation token and an unwind
path so a microphone start superseded mid-open cannot commit against a lease
the backend already revoked. Everything else that guards that machinery is a
STATIC test (``test_mic_lease_static.py``, ``test_app_websocket_static.py``)
-- source-level assertions that can pin which calls exist, but not what two
concurrent attempts do to each other.

They could not. ``startMicCapture`` has no re-entrancy guard and several of
its callers are fire-and-forget, so two attempts genuinely overlap -- and
every stage of the open window used to run through the module globals
(``S.stream`` / ``S.audioContext`` / ``S.micGainNode`` / ``S.inputAnalyser`` /
``S.workletNode``), which by then could belong to the OTHER attempt. Four
distinct defects came out of that one shape:

* the unwind reset the globals blindly, so a loser stopped the WINNER's
  tracks and nulled its context; the winner then threw on
  ``S.audioContext.sampleRate`` and the user had no microphone at all;
* the loser's post-await SETUP also ran through them: it built its worklet on
  the winner's context and spliced it into the winner's gain node -- two live
  worklets on one microphone, both uploading;
* ``S.stream`` was written before the token gate, so when the loser's
  ``getUserMedia`` settled last it took the slot and its unwind then nulled
  it, leaving the winner recording with ``S.stream === null`` -- nothing could
  stop those tracks again;
* the old-pipeline teardown at the top of ``startAudioWorklet`` was not
  token-gated, so an attempt superseded while still in ``getUserMedia`` came
  back and closed the winner's freshly published context.

The shape is now: build everything attempt-local, publish all five fields at
ONE point past the token gate, and tear down only what this attempt made.
These cases drive the real module under a stubbed browser and assert that.
The control case (one attempt, same harness) is asserted alongside, because a
harness that cannot commit a microphone would report the race as "fixed" for
the wrong reason.
"""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


APP_AUDIO_CAPTURE_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "app" / "app-audio-capture.js"
)


_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(__APP_AUDIO_CAPTURE_PATH__, 'utf8');

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT: ' + msg);
}

// One sandbox per scenario: the module keeps its cancellation generation in
// module scope, so scenarios must not share an instance.
function loadModule() {
  const streams = [];
  const contexts = [];
  const workletNodes = [];
  const nodes = [];
  // Gates for the two real await points inside the open window, so an attempt
  // can be parked at either one: audioWorklet.addModule() (a network fetch)
  // and getUserMedia() (device open / permission).
  let addModuleGate = Promise.resolve();
  let getUserMediaGate = Promise.resolve();
  // Blink caps hardware AudioContexts per document (~6), so `new AudioContext()`
  // genuinely throws in the field once starts have leaked.
  let captureContextThrows = false;

  function makeStream() {
    const track = {
      stopped: false,
      enabled: true,
      muted: false,
      readyState: 'live',
      label: 'mic',
      stop() { this.stopped = true; this.readyState = 'ended'; },
    };
    const stream = { id: streams.length + 1, getTracks: () => [track], getAudioTracks: () => [track] };
    streams.push(stream);
    return stream;
  }

  // Nodes remember the context they were built on and what they are CURRENTLY
  // wired into, so a test can ask "whose graph is this node in right now?" --
  // the question the duplicate-worklet defect turns on.
  //
  // disconnect() really removes, per CodeRabbit: a no-op stub makes `connected`
  // grow-only, which both flags a connect-then-correctly-disconnect
  // implementation as a duplicate AND leaves the assertions unable to tell
  // "never connected" from "connected then torn down" -- so the loser's
  // teardown could be deleted outright and stay green.
  function trackNode(node) {
    nodes.push(node);
    return node;
  }

  function makeNode(context, extra) {
    return trackNode(Object.assign(
      {
        context,
        connected: [],
        connect(target) { this.connected.push(target); },
        disconnect(target) {
          if (target === undefined) { this.connected.length = 0; return; }
          const at = this.connected.indexOf(target);
          if (at >= 0) this.connected.splice(at, 1);
        },
      },
      extra || {},
    ));
  }

  class FakeAudioContext {
    constructor(options) {
      // Only the CAPTURE context takes options ({sampleRate: 48000}); the TTS
      // playback context is constructed bare, so this fails just the one the
      // scenario is about.
      if (options && captureContextThrows) {
        throw new Error('AudioContext construction failed');
      }
      this.state = 'running';
      this.sampleRate = 48000;
      contexts.push(this);
      this.id = contexts.length;
      this.audioWorklet = { addModule: () => addModuleGate };
    }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { return makeNode(this, { __kind: 'source' }); }
    createGain() { return makeNode(this, { __kind: 'gain', gain: { value: 0 } }); }
    createAnalyser() {
      return makeNode(this, { __kind: 'analyser', fftSize: 0, smoothingTimeConstant: 0 });
    }
    resume() { return Promise.resolve(); }
  }

  const element = () => ({
    classList: { add() {}, remove() {}, contains: () => false },
    disabled: false,
    style: {},
    setAttribute() {},
    removeAttribute() {},
  });

  const appState = {
    isRecording: false, isPlaying: false, isMicMuted: false,
    stream: null, audioContext: null, workletNode: null, micGainNode: null,
    inputAnalyser: null, audioPlayerContext: null, microphoneGainDb: 0,
    selectedMicrophoneId: null, socket: null, voiceInputRouteBlocked: false,
    gameVoiceSttGateActive: false,
  };

  const sandbox = {
    // Every console method the module can reach, not just the ones the
    // passing path uses: a missing one turns a real assertion failure into a
    // TypeError, which still reddens but reports the wrong cause.
    console: { log() {}, info() {}, warn() {}, error() {}, debug() {},
               dir() {}, trace() {}, table() {}, group() {}, groupEnd() {} },
    // Every module-scope timer here is a deferred UI/permission side effect
    // (mic permission pre-request, floating list render). Suppressing them
    // keeps the harness to the capture pipeline and lets node exit cleanly.
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    AudioContext: FakeAudioContext,
    AudioWorkletNode: class {
      constructor(context) {
        this.context = context;
        this.__kind = 'worklet';
        this.connected = [];
        this.port = { onmessage: null, postMessage() {} };
        workletNodes.push(this);
        nodes.push(this);
      }
      connect(target) { this.connected.push(target); }
      disconnect(target) {
        if (target === undefined) { this.connected.length = 0; return; }
        const at = this.connected.indexOf(target);
        if (at >= 0) this.connected.splice(at, 1);
      }
    },
    MediaStream: class {},
    WebSocket: { OPEN: 1 },
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init || {}); } },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: {
      mediaDevices: {
        getUserMedia: async () => { await getUserMediaGate; return makeStream(); },
        enumerateDevices: async () => [],
        addEventListener() {},
      },
      permissions: { query: async () => ({ state: 'granted', addEventListener() {} }) },
    },
    document: {
      documentElement: element(),
      head: { appendChild() {} },
      body: { appendChild() {} },
      getElementById: () => null,
      createElement: () => element(),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
  };
  // The floating mic button is the observable half of the caller-side UI
  // restore: both the commit path and the unwind path drive it.
  const micButtonStates = [];
  sandbox.window = {
    appState,
    appConst: {},
    // Identity so a test can read back which dB the publish actually applied.
    appUtils: { isMobile: () => false, dbToLinear: (db) => db },
    AudioContext: FakeAudioContext,
    addEventListener() {}, dispatchEvent() {},
    showStatusToast() {}, t: (key) => key,
    localStorage: sandbox.localStorage,
    syncFloatingMicButtonState(on) { micButtonStates.push(on); },
    syncVoiceChatComposerHidden() {},
  };
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'app-audio-capture.js' });

  return {
    mod: sandbox.window.appAudioCapture,
    S: appState,
    streams,
    contexts,
    workletNodes,
    nodes,
    micButtonStates,
    // `addModule: () => addModuleGate` reads the gate at CALL time, so an
    // attempt already parked keeps awaiting the promise it captured even
    // after `unpark()` swaps the gate for the next attempt. That is what lets
    // the winner commit FIRST and the loser unwind after it -- the ordering
    // the caller-side UI guard exists for.
    parkAddModule() {
      let release;
      const parked = new Promise((resolve) => { release = resolve; });
      addModuleGate = parked;
      return release;
    },
    unparkAddModule() {
      addModuleGate = Promise.resolve();
    },
    parkGetUserMedia() {
      let release;
      const parked = new Promise((resolve) => { release = resolve; });
      getUserMediaGate = parked;
      return release;
    },
    unparkGetUserMedia() {
      getUserMediaGate = Promise.resolve();
    },
    failCaptureContext() {
      captureContextThrows = true;
    },
    // stopProactiveChatSchedule is the LAST thing on the success path, so this
    // throws only after the pipeline has committed and published.
    failAfterCommit() {
      sandbox.window.stopProactiveChatSchedule = () => {
        throw new Error('post-commit failure');
      };
    },
    failAddModule(error) {
      addModuleGate = Promise.reject(error || new Error('addModule failed'));
      // Nothing awaits this rejection until the module does; keep node from
      // reporting it as unhandled in the meantime.
      addModuleGate.catch(() => {});
    },
  };
}

async function settle(times) {
  for (let i = 0; i < (times || 60); i += 1) await Promise.resolve();
}

async function raceCase() {
  const env = loadModule();

  // #1 parks inside startAudioWorklet, where the real one awaits addModule().
  const releaseFirst = env.parkAddModule();
  const first = env.mod.startMicCapture();
  await settle();
  assert(env.streams.length === 1, 'attempt #1 should have opened a device');
  assert(env.S.stream === null,
         'an in-flight attempt must stay invisible in S.stream until it wins');

  // #2 overlaps -- a device-change restore, or the session_started auto-start.
  // It does NOT park, so it runs all the way to its commit while #1 is still
  // suspended: the loser then unwinds against a fully live winner, which is
  // the ordering both halves of the fix have to survive.
  env.unparkAddModule();
  const second = env.mod.startMicCapture();
  await settle();
  await second;
  assert(env.S.stream === env.streams[1], 'attempt #2 should have published its stream');
  assert(env.S.isRecording === true, 'attempt #2 should have committed');
  assert(env.micButtonStates[env.micButtonStates.length - 1] === true,
         'attempt #2 should have lit the floating mic button');
  // The publish is all-or-nothing: a field left out of it is a live pipeline
  // the rest of the module cannot see. S.inputAnalyser in particular drives
  // silence detection and the volume meter, so dropping it yields a working
  // mic with a frozen meter -- silent, and exactly the symptom this module
  // works hard elsewhere to avoid.
  assert(env.S.audioContext !== null && env.S.micGainNode !== null
         && env.S.inputAnalyser !== null && env.S.workletNode !== null,
         'the winning publish must include every graph field');
  assert(env.S.micGainNode.context === env.S.audioContext
         && env.S.inputAnalyser.context === env.S.audioContext,
         'every published node must belong to the published context');
  assert(env.S.isPlaying === false,
         'committing releases the focus-mode playback guard');

  // Only now let the superseded attempt resume and unwind.
  releaseFirst();
  await first;

  assert(env.streams[0].getTracks()[0].stopped === true,
         'the superseded attempt must stop its OWN stream');
  assert(env.streams[1].getTracks()[0].stopped === false,
         "the superseded attempt must not stop the winner's stream");
  assert(env.S.stream === env.streams[1],
         "S.stream must still be the winner's stream, not null");
  assert(env.S.audioContext !== null && env.S.audioContext.state === 'running',
         "the winner's AudioContext must survive the loser's unwind");
  assert(env.S.isRecording === true,
         'the winner must still be recording after the loser unwinds');
  assert(env.S.workletNode !== null && env.S.micGainNode !== null,
         "the winner's graph nodes must not be cleared by the loser");
  assert(env.micButtonStates[env.micButtonStates.length - 1] === true,
         'the loser must not paint "not recording" over the recording winner');

  // Codex P2. Scoping only the TEARDOWN is not enough: the loser resumes from
  // addModule() and does all its post-await graph SETUP through the shared
  // globals too, which by then are the winner's. It built its worklet on the
  // winner's context, overwrote S.workletNode, and spliced itself into the
  // winner's gain node -- two live worklets on one microphone, both uploading,
  // and the winner's own node orphaned where no teardown would ever find it.
  assert(env.S.workletNode.context === env.S.audioContext,
         "S.workletNode must belong to the winner's own AudioContext");
  const winnerWorklets = env.S.micGainNode.connected.filter((t) => t && t.__kind === 'worklet');
  assert(winnerWorklets.length === 1,
         'exactly one worklet may hang off the winning gain node, found ' + winnerWorklets.length);
  assert(winnerWorklets[0] === env.S.workletNode,
         'the worklet in the winning graph must be the one S.workletNode names');
  env.workletNodes.forEach((node) => {
    assert(node === env.S.workletNode || node.context !== env.S.audioContext,
           "no abandoned worklet may be left alive on the winner's context");
  });

  // The loser's own node must be silenced, not merely left out of S.*: its
  // processor can already have a frame in flight, and the handler closes over
  // S.socket / S.isRecording, both of which the WINNER has now made live. An
  // unsilenced loser therefore uploads duplicate PCM into the winner's turn.
  const loserWorklets = env.workletNodes.filter((n) => n !== env.S.workletNode);
  assert(loserWorklets.length === 1, 'the superseded attempt should have built one worklet');
  assert(loserWorklets[0].port.onmessage === null,
         "the superseded worklet's port handler must be cleared, not left armed");
  assert(env.contexts.some((c) => c !== env.S.audioContext && c.state === 'closed'),
         "the superseded attempt must close its own context");
  // Now that disconnect() really removes, "torn down" is distinguishable from
  // "never wired up": every gain node except the winner's must have been
  // unwired by discardOwnPipeline, so deleting that teardown reddens here
  // rather than passing because the loser happened to stay out of S.*.
  env.nodes
    .filter((n) => n.__kind === 'gain' && n !== env.S.micGainNode)
    .forEach((gain) => {
      assert(gain.connected.length === 0,
             "a superseded attempt's gain node must be disconnected, not left wired");
    });
}

async function streamPublishOrderCase() {
  // The stream used to be written by startMicCapture BEFORE the token gate,
  // i.e. outside the publish point -- and that write lands after an await, so
  // it was unordered with respect to the token. When the loser's getUserMedia
  // settles LAST it wrote S.stream last, and its unwind then legitimately saw
  // `S.stream === mediaStream` and nulled it, leaving the winner recording
  // with S.stream === null: stopRecording's `if (S.stream)` never stops those
  // tracks (OS mic indicator lit for the life of the page) and the
  // `S.stream && S.audioContext && S.workletNode` liveness probes read dead
  // against a live pipeline and open a second microphone on top of it.
  const env = loadModule();

  // #1 (older token) parks in getUserMedia -- a cold device open.
  const releaseGum = env.parkGetUserMedia();
  const first = env.mod.startMicCapture();
  await settle();
  assert(env.streams.length === 0, 'attempt #1 should still be waiting on the device');

  // #2 (newer token) opens against a now-warm device and commits.
  env.unparkGetUserMedia();
  const second = env.mod.startMicCapture();
  await settle();
  await second;
  assert(env.S.isRecording === true, 'attempt #2 should have committed');
  const winnerStream = env.S.stream;
  const winnerContext = env.S.audioContext;
  assert(winnerStream !== null, 'the winner published its stream');

  // Only NOW does the loser's device open finish -- so its stream write, if
  // there were one, would land last.
  releaseGum();
  await first;

  assert(env.S.stream === winnerStream,
         "a loser whose getUserMedia settles last must not take over S.stream");
  assert(winnerStream.getTracks()[0].stopped === false,
         "the winner's tracks must survive the late loser");
  assert(env.S.audioContext === winnerContext,
         "the winner's context must survive the late loser");
  assert(env.S.isRecording === true, 'the winner must still be recording');
  const loserStream = env.streams.find((s) => s !== winnerStream);
  assert(loserStream && loserStream.getTracks()[0].stopped === true,
         'the late loser must still stop its own device');
}

async function controlCase() {
  const env = loadModule();
  const release = env.parkAddModule();
  const only = env.mod.startMicCapture();
  await settle();
  release();
  await only;

  assert(env.S.isRecording === true, 'a lone attempt must commit');
  assert(env.S.stream === env.streams[0], 'a lone attempt must keep its stream');
  assert(env.streams[0].getTracks()[0].stopped === false,
         'a lone attempt must not stop its own tracks');
  assert(env.micButtonStates[env.micButtonStates.length - 1] === true,
         'a lone attempt must light the floating mic button');
}

async function failClosedCase() {
  // The unwind's OTHER trigger, which must keep working: the backend
  // fail-closed the route while this single attempt was opening. Nothing
  // superseded it, so it owns every global it touches and must leave the
  // window fully torn down.
  const env = loadModule();
  const release = env.parkAddModule();
  const only = env.mod.startMicCapture();
  await settle();
  env.S.voiceInputRouteBlocked = true;
  // S.isPlaying is the focus-mode playback guard. Clearing it is part of
  // COMMITTING ("the user opened the mic, let their voice through"), so an
  // attempt that never commits must leave it alone -- otherwise a superseded
  // or fail-closed start silences the guard mid-TTS for whoever does own the
  // microphone.
  env.S.isPlaying = true;
  release();
  await only;

  assert(env.S.isPlaying === true,
         'an attempt that never commits must not clear the playback guard');

  // Asserted on work the attempt actually DID, not on fields that were never
  // written: with the pipeline attempt-local, `S.stream === null` and
  // `S.audioContext === null` are the harness's own starting state, so on
  // their own they would restate the fixture rather than cover the unwind.
  // contexts[] also holds S.audioPlayerContext (the TTS playback context that
  // startMicCapture creates on first use), so select the capture ones.
  const captureContexts = env.contexts.filter((c) => c !== env.S.audioPlayerContext);
  assert(captureContexts.length === 1, 'the attempt should have created one capture context');
  assert(captureContexts[0].state === 'closed',
         'a fail-closed unwind must close the context it created');
  assert(env.streams.length === 1 && env.streams[0].getTracks()[0].stopped === true,
         'a fail-closed unwind must stop the device it opened');
  assert(env.S.isRecording === false, 'a fail-closed route must not commit');
  assert(env.S.stream === null && env.S.audioContext === null,
         'a fail-closed attempt must never publish');
  assert(env.micButtonStates[env.micButtonStates.length - 1] === false,
         'a fail-closed unwind must restore the pre-start mic button');
}

async function restartThenFailClosedCase() {
  // The `S.audioContext === null` branch in discardOwnPipeline only does work
  // when a PREVIOUS pipeline was published and then torn down by the top of
  // startAudioWorklet -- which is the case a cold fail-closed start cannot
  // reach, and the reason the case above cannot cover it.
  const env = loadModule();
  await env.mod.startMicCapture();
  assert(env.S.isRecording === true, 'the first start should commit');
  const firstContext = env.S.audioContext;
  assert(env.S.inputAnalyser !== null, 'the first start published its analyser');

  const release = env.parkAddModule();
  const restart = env.mod.startMicCapture();
  await settle();
  assert(firstContext.state === 'closed',
         'the restart should have closed the previous context on entry');
  assert(env.S.audioContext === null && env.S.inputAnalyser === null
         && env.S.micGainNode === null && env.S.workletNode === null,
         'closing the previous pipeline must clear every handle to it, not just two');

  env.S.voiceInputRouteBlocked = true;
  release();
  await restart;

  assert(env.S.audioContext === null && env.S.micGainNode === null
         && env.S.inputAnalyser === null && env.S.workletNode === null,
         'after a fail-closed restart no stale graph handle may remain');
  const restartContext = env.contexts.filter((c) => c !== env.S.audioPlayerContext).pop();
  assert(restartContext !== firstContext && restartContext.state === 'closed',
         'the fail-closed restart must close its own context too');
}

async function addModuleFailureCase() {
  // The other way out of the try: addModule() rejects (worklet script 404s,
  // offline, CSP). Nothing was published, so this graph is unreachable from
  // S.* -- if the catch does not discard it, the AudioContext and the
  // microphone track leak with no later attempt able to find them.
  const env = loadModule();
  env.failAddModule(new Error('boom'));
  const only = await env.mod.startMicCapture();

  assert(only === undefined, 'startMicCapture resolves without a value');
  assert(env.S.isRecording === false, 'a failed addModule must not commit');
  const own = env.contexts.filter((c) => c !== env.S.audioPlayerContext).pop();
  assert(own, 'the attempt should have created a capture context');
  assert(own.state === 'closed', "a failed attempt must close its own AudioContext");
  assert(env.streams[0].getTracks()[0].stopped === true,
         'a failed attempt must stop its own microphone track');
  assert(env.S.stream === null, 'a failed attempt must drop its own stream reference');
  assert(env.S.audioContext === null, 'a failed attempt must not publish its context');
}

async function gainChangedDuringOpenCase() {
  // The published gain node is created before addModule() awaits, and the
  // attempt is deliberately absent from S.micGainNode for that whole window --
  // so setMicrophoneGain's poke at S.micGainNode lands on nothing. Unless the
  // publish re-reads S.microphoneGainDb, the slider and localStorage would show
  // the new dB while the live microphone stayed at the old one until the next
  // restart.
  const env = loadModule();
  const release = env.parkAddModule();
  const only = env.mod.startMicCapture();
  await settle();
  env.S.microphoneGainDb = 7;
  release();
  await only;

  assert(env.S.isRecording === true, 'the attempt should have committed');
  assert(env.S.micGainNode.gain.value === 7,
         'the committed graph must carry the gain set during the open window, got '
         + env.S.micGainNode.gain.value);
}

async function preWorkletSetupFailureCase() {
  // Codex P2. getUserMedia() succeeds, then `new AudioContext()` (or the
  // source/gain/analyser wiring) throws -- all of it BEFORE the try whose
  // catch runs discardOwnPipeline(). The stream is attempt-local and not yet
  // published, and `const ownStream` inside the try block is invisible to
  // `catch (err)`, so nothing could stop its tracks: the UI reported a failed
  // start while the browser microphone stayed live.
  const env = loadModule();
  env.failCaptureContext();

  let threw = false;
  try {
    await env.mod.startMicCapture();
  } catch (_) {
    threw = true;
  }

  assert(threw, 'startMicCapture should rethrow the setup failure');
  assert(env.streams.length === 1, 'the device was opened before the failure');
  assert(env.streams[0].getTracks()[0].stopped === true,
         'a start that fails before the worklet must still release the device it opened');
  assert(env.S.stream === null, 'a failed start must not publish its stream');
  assert(env.S.isRecording === false, 'a failed start must not commit');
}

async function postCommitFailureCase() {
  // The other side of releasing the stream on failure: once the attempt has
  // COMMITTED, its stream is the live microphone and belongs to the pipeline,
  // so a throw from the success path below the commit must not stop it. This
  // is what the `S.stream !== ownStream` guard on that release buys.
  const env = loadModule();
  env.failAfterCommit();

  let threw = false;
  try {
    await env.mod.startMicCapture();
  } catch (_) {
    threw = true;
  }

  assert(threw, 'the post-commit failure should propagate');
  // The catch clears S.isRecording for its own reasons, so the commit is
  // evidenced by the published pipeline rather than by that flag.
  assert(env.S.audioContext !== null && env.S.workletNode !== null,
         'the pipeline committed and published before the failure');
  assert(env.S.stream !== null, 'the committed stream stays published');
  assert(env.S.stream.getTracks()[0].stopped === false,
         'a failure AFTER the commit must not stop the live microphone');
}

(async () => {
  await raceCase();
  await preWorkletSetupFailureCase();
  await postCommitFailureCase();
  await gainChangedDuringOpenCase();
  await streamPublishOrderCase();
  await controlCase();
  await failClosedCase();
  await restartThenFailClosedCase();
  await addModuleFailureCase();
  console.log('HARNESS_OK');
})().catch((error) => {
  console.log('HARNESS_FAILED: ' + (error && error.message ? error.message : error));
  process.exitCode = 1;
});
"""


def _run_mic_capture_harness(script: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping mic-start race harness test")
    # run_node_script writes to a temp file rather than passing the harness on
    # the command line: Windows caps argv at 32767 characters and encodes it
    # under the locale codec instead of UTF-8.
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
def test_superseded_mic_start_does_not_tear_down_the_winner_harness():
    # Mutation-verified, each against the assertion it is meant to protect:
    #   unwind scoping removed (unconditional S.stream/S.audioContext reset)
    #     -> "the superseded attempt must not stop the winner's stream"
    #   caller-side UI guard removed
    #     -> 'the loser must not paint "not recording" over the recording winner'
    #   unwind disabled entirely (`if (false)`)
    #     -> "the superseded attempt must stop its OWN stream"
    # so none of the three is carried by another's assertion.
    harness = textwrap.dedent(_HARNESS).replace(
        "__APP_AUDIO_CAPTURE_PATH__", json.dumps(str(APP_AUDIO_CAPTURE_PATH))
    )

    result = _run_mic_capture_harness(harness)
    assert result.returncode == 0, (
        "mic-start race harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout

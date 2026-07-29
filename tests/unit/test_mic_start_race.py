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

They could not: the unwind ran on the LOSING attempt but reset the module
globals (``S.stream`` / ``S.audioContext`` / ``S.workletNode`` ...) shared by
every attempt. ``startMicCapture`` publishes ``S.stream`` before it ever calls
``startAudioWorklet``, has no re-entrancy guard, and several of its callers
are fire-and-forget -- so by the time the loser
unwound, those fields belonged to the WINNER: its tracks were stopped and its
context nulled, it then threw on ``S.audioContext.sampleRate``, and the user
was left with no microphone at all -- from exactly the overlap the token was
added to survive.

So this drives the real module under a stubbed browser and asserts the
outcome. The control case (one attempt, same harness) is asserted alongside,
because a harness that cannot commit a microphone would report the race as
"fixed" for the wrong reason.
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
  // Gate for attempt #1's audioWorklet.addModule(), so it can be parked
  // mid-open exactly where the real one awaits a network fetch.
  let addModuleGate = Promise.resolve();

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

  class FakeAudioContext {
    constructor() {
      this.state = 'running';
      this.sampleRate = 48000;
      contexts.push(this);
      this.id = contexts.length;
      this.audioWorklet = { addModule: () => addModuleGate };
    }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { return { connect() {} }; }
    createGain() { return { gain: { value: 0 }, connect() {} }; }
    createAnalyser() { return { fftSize: 0, smoothingTimeConstant: 0, connect() {} }; }
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
    AudioWorkletNode: class { constructor() { this.port = { onmessage: null, postMessage() {} }; }
                              connect() {} disconnect() {} },
    MediaStream: class {},
    WebSocket: { OPEN: 1 },
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init || {}); } },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: {
      mediaDevices: {
        getUserMedia: async () => makeStream(),
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
    appUtils: { isMobile: () => false, dbToLinear: () => 1 },
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
  assert(env.S.stream === env.streams[0], 'attempt #1 should have published its stream');

  // #2 overlaps -- a device-change restore, or the session_started auto-start.
  // It does NOT park, so it runs all the way to its commit while #1 is still
  // suspended: the loser then unwinds against a fully live winner, which is
  // the ordering both halves of the fix have to survive.
  env.unparkAddModule();
  const second = env.mod.startMicCapture();
  await settle();
  await second;
  assert(env.S.stream === env.streams[1], 'attempt #2 should have taken over S.stream');
  assert(env.S.isRecording === true, 'attempt #2 should have committed');
  assert(env.micButtonStates[env.micButtonStates.length - 1] === true,
         'attempt #2 should have lit the floating mic button');

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
  release();
  await only;

  assert(env.S.isRecording === false, 'a fail-closed route must not commit');
  assert(env.S.stream === null, 'a fail-closed unwind must drop its own stream');
  assert(env.streams[0].getTracks()[0].stopped === true,
         'a fail-closed unwind must stop its own tracks');
  assert(env.S.audioContext === null, 'a fail-closed unwind must clear its own context');
  assert(env.micButtonStates[env.micButtonStates.length - 1] === false,
         'a fail-closed unwind must restore the pre-start mic button');
}

(async () => {
  await raceCase();
  await controlCase();
  await failClosedCase();
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

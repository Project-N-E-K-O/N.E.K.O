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
"""Behavioral cover for the desktop settings page's 15-second mic probe.

``window.startSettingsMicVolumeTest`` awaits ``getUserMedia`` (permission /
device open) and possibly ``AudioContext.resume()``. The settings window can
stop the test or start a new one while either is pending, and the Electron
``closed`` hook sends exactly one stop. Each case below parks the probe at one
of those awaits and asserts that no microphone stream outlives its owner and
that a stale attempt never tears down a newer probe.

Reuses the stubbed-browser loader from ``test_mic_start_race.py``.
"""

import json
import textwrap

import pytest

from tests.unit.test_mic_start_race import (
    APP_AUDIO_CAPTURE_PATH,
    _HARNESS,
    _run_mic_capture_harness,
)


_LOADER = _HARNESS[: _HARNESS.index("async function raceCase()")]

_CASES = r"""
function isLive(stream) {
  return stream.getTracks()[0].stopped === false;
}

function mediaError(name) {
  const error = new Error(name);
  error.name = name;
  return error;
}

async function stopDuringPermissionCase() {
  const env = loadModule();
  const release = env.parkGetUserMedia();
  const pending = env.win.startSettingsMicVolumeTest();
  await settle();
  env.win.stopSettingsMicVolumeTest();
  release();
  const result = await pending;

  assert(result.ok === false, 'a start stopped mid-permission must not report success');
  assert(env.streams.length === 1 && !isLive(env.streams[0]),
         'the stream granted after stop must be released on the spot');
  assert(env.contexts.length === 0, 'no probe context may be built after stop');
  assert(env.mod.sampleMicVolumeLevel().recording === false,
         'no probe may be published after stop');
}

async function overlappingStartsCase() {
  const env = loadModule();
  const releaseFirst = env.parkGetUserMedia();
  const first = env.win.startSettingsMicVolumeTest();
  await settle();
  const releaseSecond = env.parkGetUserMedia();
  const second = env.win.startSettingsMicVolumeTest();
  await settle();

  releaseSecond();
  const secondResult = await second;
  releaseFirst();
  const firstResult = await first;

  assert(secondResult.ok === true && secondResult.mode === 'probe', 'the newer start must win');
  assert(firstResult.ok === false, 'the superseded start must not report success');
  // The harness mints a stream when getUserMedia's gate RELEASES, so streams[]
  // is in settle order: [0] is the winner's (released first), [1] the loser's.
  const [winnerStream, loserStream] = env.streams;
  assert(env.streams.length === 2, 'both starts reach getUserMedia');
  assert(!isLive(loserStream), "the superseded start must stop its own stream");
  assert(isLive(winnerStream), "the superseded start must not stop the winner's stream");
  assert(env.contexts.length === 1 && env.contexts[0].state !== 'closed',
         "only the winner builds a context, and it stays open");

  env.win.stopSettingsMicVolumeTest();
  assert(!isLive(winnerStream) && env.contexts[0].state === 'closed',
         'one stop must release the surviving probe completely');
}

async function staleFailureKeepsNewerProbeCase() {
  const env = loadModule();
  env.S.selectedMicrophoneId = 'usb-mic';
  const releaseFirst = env.parkGetUserMedia();
  const first = env.win.startSettingsMicVolumeTest();
  await settle();
  const releaseSecond = env.parkGetUserMedia();
  const second = env.win.startSettingsMicVolumeTest();
  await settle();

  releaseSecond();
  assert((await second).ok === true, 'the newer start must publish its probe');
  env.failNextGetUserMedia(mediaError('OverconstrainedError'));
  releaseFirst();
  const firstResult = await first;

  assert(firstResult.ok === false, 'the stale start reports failure');
  assert(env.getUserMediaCalls.length === 2,
         'a stale start must not retry the fallback device');
  // Only the newer start ever got a stream (settle order, see above).
  assert(env.streams.length === 1 && isLive(env.streams[0]) && env.contexts[0].state !== 'closed',
         "a stale start's failure must not release the newer probe");
}

async function staleFallbackThrowKeepsNewerProbeCase() {
  // The stale start is already inside its fallback getUserMedia when it is
  // superseded, so it genuinely THROWS out of the inner function. The public
  // wrapper's catch must not answer that by releasing whatever probe is
  // current -- that probe belongs to the newer start.
  const env = loadModule();
  env.S.selectedMicrophoneId = 'usb-mic';
  const releaseSelected = env.parkGetUserMedia();
  const first = env.win.startSettingsMicVolumeTest();
  await settle();
  const releaseFallback = env.parkGetUserMedia();
  env.failNextGetUserMedia(mediaError('OverconstrainedError'));
  releaseSelected();
  await settle();
  assert(env.getUserMediaCalls.length === 2, 'the first start is parked in its fallback');

  const releaseSecond = env.parkGetUserMedia();
  const second = env.win.startSettingsMicVolumeTest();
  await settle();
  releaseSecond();
  assert((await second).ok === true, 'the newer start must publish its probe');

  env.failNextGetUserMedia(mediaError('NotReadableError'));
  releaseFallback();
  assert((await first).ok === false, 'the stale start reports failure');
  assert(env.streams.length === 1 && isLive(env.streams[0]) && env.contexts[0].state !== 'closed',
         "a stale start's thrown failure must not release the newer probe");
}

async function permissionDeniedDoesNotFallBackCase() {
  // Only device-class errors may retry on the default microphone, matching
  // openMicrophoneStreamWithFallback. A permission denial must surface as-is:
  // retrying would re-prompt, or open a device the user never picked.
  for (const name of ['NotAllowedError', 'SecurityError', 'AbortError']) {
    const env = loadModule();
    env.S.selectedMicrophoneId = 'usb-mic';
    env.failNextGetUserMedia(mediaError(name));
    const result = await env.win.startSettingsMicVolumeTest();

    assert(result.ok === false, name + ' must report failure');
    assert(env.getUserMediaCalls.length === 1, name + ' must not retry the default microphone');
    assert(env.streams.length === 0 && env.contexts.length === 0,
           name + ' must not open any stream or context');
  }

  const env = loadModule();
  env.S.selectedMicrophoneId = 'usb-mic';
  env.failNextGetUserMedia(mediaError('NotFoundError'));
  const result = await env.win.startSettingsMicVolumeTest();
  assert(result.ok === true && result.mode === 'probe', 'a missing device falls back to the default');
  assert(env.getUserMediaCalls.length === 2 && env.getUserMediaCalls[1].audio.deviceId === undefined,
         'the fallback request must drop the exact deviceId');
}

async function contextConstructionFailureCase() {
  const env = loadModule();
  env.win.AudioContext = class { constructor() { throw new Error('too many AudioContexts'); } };
  const result = await env.win.startSettingsMicVolumeTest();

  assert(result.ok === false, 'a context construction failure reports failure');
  assert(env.streams.length === 1 && !isLive(env.streams[0]),
         'the granted stream must be stopped when the context cannot be built');
}

async function resumeFailureCase() {
  const env = loadModule();
  const Base = env.win.AudioContext;
  env.win.AudioContext = class extends Base {
    constructor() { super(); this.state = 'suspended'; }
    resume() { return Promise.reject(new Error('autoplay blocked')); }
  };
  const result = await env.win.startSettingsMicVolumeTest();

  assert(result.ok === false, 'a context that never runs must not report a working probe');
  assert(!isLive(env.streams[0]) && env.contexts[0].state === 'closed',
         'a probe that cannot run must be released');
}

async function liveRecordingTakesOverCase() {
  const env = loadModule();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  await env.mod.startMicCapture();

  assert(env.S.isRecording === true, 'the real recording must commit');
  assert(!isLive(env.streams[0]),
         'the probe stream must be released as soon as real recording commits');
  assert(env.S.stream === env.streams[1] && isLive(env.streams[1]),
         'the real recording keeps its own stream');
}

(async () => {
  await stopDuringPermissionCase();
  await overlappingStartsCase();
  await staleFailureKeepsNewerProbeCase();
  await staleFallbackThrowKeepsNewerProbeCase();
  await permissionDeniedDoesNotFallBackCase();
  await contextConstructionFailureCase();
  await resumeFailureCase();
  await liveRecordingTakesOverCase();
  console.log('HARNESS_OK');
})().catch((error) => {
  console.log('HARNESS_FAILED: ' + (error && error.message ? error.message : error));
  process.exitCode = 1;
});
"""


@pytest.mark.unit
def test_settings_mic_probe_never_leaks_or_steals_a_stream_harness():
    harness = textwrap.dedent(_LOADER + _CASES).replace(
        "__APP_AUDIO_CAPTURE_PATH__", json.dumps(str(APP_AUDIO_CAPTURE_PATH))
    )
    result = _run_mic_capture_harness(harness)
    assert result.returncode == 0, (
        "settings mic probe harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout

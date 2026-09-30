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


function watchdogTimers(timers) {
  return timers.filter((timer) => timer.delay === 20000);
}

// 成功的 start 会重新计时（先清掉入口处那一个），只数仍然有效的。
function activeWatchdogTimers(timers) {
  return watchdogTimers(timers).filter((timer) => !timer.cleared);
}

async function watchdogReleasesAbandonedProbeCase() {
  // The settings window can die without sending stop (crash, reload, force
  // close). The page must release the mic on its own.
  const env = loadModule();
  const timers = env.captureTimeouts();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts');
  const armed = activeWatchdogTimers(timers);
  assert(armed.length === 1, 'a settings start leaves one armed watchdog');

  armed[0].callback();
  assert(!isLive(env.streams[0]) && env.contexts[0].state === 'closed',
         'the watchdog must release an abandoned probe');

  const env2 = loadModule();
  const timers2 = env2.captureTimeouts();
  await env2.win.startSettingsMicVolumeTest();
  env2.win.stopSettingsMicVolumeTest();
  assert(watchdogTimers(timers2).every((timer) => timer.cleared),
         'an explicit stop must disarm the watchdog');
}

async function probeResumesAfterLiveEndsCase() {
  const env = loadModule();
  const timers = env.captureTimeouts();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  await env.mod.startMicCapture();
  assert(!isLive(env.streams[0]), 'the probe yields to real recording');

  // Real recording ends while the settings test window is still open.
  const streamsBefore = env.streams.length;
  env.mod.stopRecording({ notifyServer: false });
  await settle(10);
  assert(env.streams.length === streamsBefore + 1 && isLive(env.streams[streamsBefore]),
         'stopping real recording rebuilds exactly one probe');
  const probeContext = env.contexts[env.contexts.length - 1];
  assert(probeContext.state !== 'closed', 'the rebuilt probe has an open context');

  const armed = activeWatchdogTimers(timers);
  assert(armed.length === 1 && watchdogTimers(timers).length === 2,
         'rebuilding the probe must not extend the watchdog');
  armed[0].callback();
  assert(!isLive(env.streams[streamsBefore]) && probeContext.state === 'closed',
         'the original watchdog still bounds the rebuilt probe');
}

async function liveOnlySampleIgnoresProbeCase() {
  // The floating-button volume bar samples with liveOnly: it must not read,
  // yield, or rebuild the settings probe.
  const env = loadModule();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts');
  const sample = env.mod.sampleMicVolumeLevel({ liveOnly: true });
  assert(sample.recording === false && sample.percent === 0,
         'a liveOnly sample must not report the probe level');
  assert(isLive(env.streams[0]) && env.contexts[0].state !== 'closed',
         'a liveOnly sample must leave the probe running');
}

async function deadTrackFallsBackCase() {
  // 选中设备给出的音轨已 ended：和正式录音一样合成 NotReadableError，退回默认麦克风。
  const env = loadModule();
  env.S.selectedMicrophoneId = 'usb-mic';
  env.endTrackOnGetUserMediaCall(1);
  const result = await env.win.startSettingsMicVolumeTest();

  assert(result.ok === true && result.mode === 'probe', 'a dead selected track falls back to a working probe');
  assert(env.getUserMediaCalls.length === 2, 'exactly one fallback open after the dead track');
  assert(env.getUserMediaCalls[0].audio.deviceId.exact === 'usb-mic', 'the first open targets the selected device');
  assert(env.getUserMediaCalls[1].audio.deviceId === undefined, 'the fallback open targets the default device');
  assert(!isLive(env.streams[0]) && isLive(env.streams[1]), 'the dead stream is stopped, the fallback stream runs');
}

async function resumeDuringLiveReturnsLiveCase() {
  // 只挂起 probe 自己的 context.resume()；挂起期间正式录音接管，start 应报 live 而不是失败。
  const env = loadModule();
  const Base = env.win.AudioContext;
  let probeContext = null;
  let releaseResume;
  const resumeGate = new Promise((resolve) => { releaseResume = resolve; });
  env.win.AudioContext = class extends Base {
    constructor(options) {
      super(options);
      if (!probeContext) { probeContext = this; this.state = 'suspended'; }
    }
    resume() { return this === probeContext ? resumeGate : super.resume(); }
  };
  const pending = env.win.startSettingsMicVolumeTest();
  await settle();
  await env.mod.startMicCapture();
  assert(env.S.isRecording === true, 'the real recording commits while the probe resume is parked');
  releaseResume();
  const result = await pending;

  assert(result.ok === true && result.mode === 'live', 'a probe overtaken by real recording reports live');
  assert(!isLive(env.streams[0]), 'the overtaken probe stream is released');
}

async function rebuildFailureIsTerminalCase() {
  const env = loadModule();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  await env.mod.startMicCapture();
  env.failNextGetUserMedia(mediaError('NotReadableError'));
  env.mod.stopRecording({ notifyServer: false });
  await settle(10);
  const callsAfterRebuild = env.getUserMediaCalls.length;

  for (let i = 0; i < 5; i += 1) {
    const sample = env.mod.sampleMicVolumeLevel();
    assert(sample.failed === true && sample.recording === false, 'a failed rebuild is reported to the settings page');
  }
  await settle(10);
  assert(env.getUserMediaCalls.length === callsAfterRebuild, 'a failed rebuild must not be retried on every poll');
  assert(env.mod.sampleMicVolumeLevel({ liveOnly: true }).failed !== true,
         'the floating-button sampler never reports the settings failure');
}

async function deviceSwitchReopensProbeCase() {
  const env = loadModule();
  const timers = env.captureTimeouts();
  env.S.selectedMicrophoneId = 'mic-A';
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts on mic-A');
  await env.win.selectMicrophone('mic-B');
  await settle(10);

  assert(env.getUserMediaCalls.length === 2, 'switching device reopens the probe once');
  assert(env.getUserMediaCalls[1].audio.deviceId.exact === 'mic-B', 'the reopened probe targets mic-B');
  assert(!isLive(env.streams[0]) && isLive(env.streams[1]), 'mic-A is released and mic-B runs');
  assert(activeWatchdogTimers(timers).length === 1 && watchdogTimers(timers).length === 2,
         'reopening on device switch must not extend the watchdog');
}

async function deviceSwitchDuringPermissionCase() {
  const env = loadModule();
  env.S.selectedMicrophoneId = 'mic-A';
  const release = env.parkGetUserMedia();
  const pending = env.win.startSettingsMicVolumeTest();
  await settle();
  await env.win.selectMicrophone('mic-B');
  release();
  const result = await pending;

  assert(result.ok === true && result.mode === 'probe', 'the start still succeeds after the switch');
  assert(env.getUserMediaCalls.length === 2, 'the stale mic-A grant is replaced by one mic-B open');
  assert(env.getUserMediaCalls[1].audio.deviceId.exact === 'mic-B', 'the probe reopens on mic-B');
  assert(!isLive(env.streams[0]) && isLive(env.streams[1]),
         'the mic-A stream granted after the switch is released, mic-B runs');
}

async function directTeardownResumesProbeCase() {
  // WebSocket 断线等路径直接把 isRecording 置 false，不经过 stopRecording。
  // 采样本身仍不开设备，但会排一次恢复：重复轮询只重建一个 probe。
  const env = loadModule();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  await env.mod.startMicCapture();
  env.S.isRecording = false;
  env.S.inputAnalyser = null;
  const streamsBefore = env.streams.length;
  for (let i = 0; i < 3; i += 1) env.mod.sampleMicVolumeLevel();
  assert(env.streams.length === streamsBefore, 'sampling itself must not open a microphone');
  await settle(10);
  assert(env.streams.length === streamsBefore + 1 && isLive(env.streams[streamsBefore]),
         'a direct teardown rebuilds exactly one probe');
  assert(env.mod.sampleMicVolumeLevel().recording === true, 'the rebuilt probe reports a level again');
  const liveOnlyEnv = loadModule();
  assert((await liveOnlyEnv.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts');
  await liveOnlyEnv.mod.startMicCapture();
  liveOnlyEnv.S.isRecording = false;
  const liveOnlyBefore = liveOnlyEnv.streams.length;
  liveOnlyEnv.mod.sampleMicVolumeLevel({ liveOnly: true });
  await settle(10);
  assert(liveOnlyEnv.streams.length === liveOnlyBefore, 'a liveOnly sample never rebuilds the probe');
}

async function probeYieldsWhenLiveStartClaimsDeviceCase() {
  // 独占式驱动上两路流会冲突：正式录音一开始占设备就让位，不等提交。
  const env = loadModule();
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  const release = env.parkGetUserMedia();
  const starting = env.mod.startMicCapture();
  await settle();
  assert(!isLive(env.streams[0]) && env.contexts[0].state === 'closed',
         'the probe releases the device before the live getUserMedia resolves');
  release();
  await starting;
  assert(env.S.isRecording === true && isLive(env.S.stream), 'the live recording commits');
}

async function fallbackUpdatesSelectionCase() {
  const env = loadModule();
  env.S.selectedMicrophoneId = 'usb-mic';
  env.failNextGetUserMedia(mediaError('NotFoundError'));
  const result = await env.win.startSettingsMicVolumeTest();
  assert(result.ok === true && result.fellBack === true, 'a fallback is reported to the settings page');
  assert(env.S.selectedMicrophoneId === null,
         'the selection follows the device actually being tested');

  const plain = loadModule();
  plain.S.selectedMicrophoneId = 'usb-mic';
  const plainResult = await plain.win.startSettingsMicVolumeTest();
  assert(plainResult.fellBack === undefined && plain.S.selectedMicrophoneId === 'usb-mic',
         'a working selected device is left alone');
}

async function failureReportsErrorNameCase() {
  const env = loadModule();
  env.failNextGetUserMedia(mediaError('NotAllowedError'));
  const result = await env.win.startSettingsMicVolumeTest();
  assert(result.ok === false && result.error === 'NotAllowedError',
         'a permission denial reaches the settings page by name');
}

async function deviceSwitchDuringResumeCountsAsSuccessCase() {
  // 设置页的 start 停在 context.resume() 时切了麦克风：内部 reopen 越过它不算失败。
  const env = loadModule();
  const timers = env.captureTimeouts();
  env.S.selectedMicrophoneId = 'mic-A';
  const Base = env.win.AudioContext;
  let firstContext = null;
  let releaseResume;
  const resumeGate = new Promise((resolve) => { releaseResume = resolve; });
  env.win.AudioContext = class extends Base {
    constructor(options) {
      super(options);
      if (!firstContext) { firstContext = this; this.state = 'suspended'; }
    }
    resume() {
      if (this !== firstContext) return super.resume();
      return resumeGate.then(() => { this.state = 'running'; });
    }
  };
  const pending = env.win.startSettingsMicVolumeTest();
  await settle();
  await env.win.selectMicrophone('mic-B');
  await settle(10);
  releaseResume();
  const result = await pending;

  assert(result.ok === true && result.mode === 'probe', 'the settings start follows the reopen and succeeds');
  assert(env.getUserMediaCalls[env.getUserMediaCalls.length - 1].audio.deviceId.exact === 'mic-B',
         'the running probe is on mic-B');
  assert(activeWatchdogTimers(timers).length === 1, 'the watchdog stays armed for the running probe');
}

async function liveDeviceSwitchKeepsProbeOffCase() {
  // 正式录音中切换设备：切换期间 inputAnalyser 被清空，probe 不能趁机抢占设备。
  const env = loadModule();
  env.S.selectedMicrophoneId = 'mic-A';
  assert((await env.win.startSettingsMicVolumeTest()).mode === 'probe', 'probe starts first');
  await env.mod.startMicCapture();
  const liveStreamsBefore = env.streams.length;

  env.enableDeferredTimeouts();
  const releaseSwitch = env.parkGetUserMedia();
  const switching = env.win.selectMicrophone('mic-B');
  await settle();
  assert(env.S.inputAnalyser === null, 'the switch tears the old pipeline down first');
  for (let i = 0; i < 3; i += 1) env.mod.sampleMicVolumeLevel();
  await settle(10);
  assert(env.streams.length === liveStreamsBefore,
         'no probe may open while the live pipeline is switching devices');

  releaseSwitch();
  await switching;
  await settle(10);
  assert(env.S.isRecording === true && isLive(env.S.stream), 'real recording resumes on mic-B');
  assert(env.streams.length === liveStreamsBefore + 1,
         'only the live pipeline reopens the device; no probe is rebuilt');
  assert(env.contexts[0].state === 'closed', 'the yielded probe context stays closed after the switch');
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
  await watchdogReleasesAbandonedProbeCase();
  await probeResumesAfterLiveEndsCase();
  await liveOnlySampleIgnoresProbeCase();
  await deadTrackFallsBackCase();
  await resumeDuringLiveReturnsLiveCase();
  await rebuildFailureIsTerminalCase();
  await deviceSwitchReopensProbeCase();
  await deviceSwitchDuringPermissionCase();
  await directTeardownResumesProbeCase();
  await probeYieldsWhenLiveStartClaimsDeviceCase();
  await fallbackUpdatesSelectionCase();
  await failureReportsErrorNameCase();
  await deviceSwitchDuringResumeCountsAsSuccessCase();
  await liveDeviceSwitchKeepsProbeOffCase();
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


@pytest.mark.unit
def test_floating_volume_bar_samples_live_only():
    # 悬浮按钮弹窗里的音量条只看正式录音；无参调用会读到 / 重建设置页的 probe。
    source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    body = source[source.index("function updateVolumeDisplay"):]
    body = body[: body.index("\n    }\n")]
    assert "sampleMicVolumeLevel({ liveOnly: true })" in body
    assert "sampleMicVolumeLevel()" not in body

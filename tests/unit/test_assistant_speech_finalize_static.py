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

"""Assistant speech finalize gate, driven through the real frontend module.

The bug this guards (issue #1566) is a timing one: an empty audio queue means
"nothing in hand right now", not "nothing more is coming".  Grepping the source
cannot tell those apart, so this suite loads app-state.js and
app-audio-playback.js into a node vm with controllable timers and drives the
real state machine through each ordering.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "static" / "app" / "app-state.js"
PLAYBACK_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-playback.js"


HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');

const STATE_SRC = fs.readFileSync(__STATE_PATH__, 'utf8');
const PLAYBACK_SRC = fs.readFileSync(__PLAYBACK_PATH__, 'utf8');

class CustomEventLike {
  constructor(type, init) {
    this.type = type;
    this.detail = (init || {}).detail;
  }
}

function createHarness() {
  const listeners = new Map();
  const dispatched = [];
  const timers = new Map();
  let nextTimerId = 1;

  const win = {
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener() {},
    dispatchEvent(ev) {
      dispatched.push({ type: ev.type, detail: ev.detail });
      for (const handler of (listeners.get(ev.type) || []).slice()) {
        handler.call(win, ev);
      }
      return true;
    },
    setTimeout(fn, ms) {
      const id = nextTimerId++;
      timers.set(id, { fn: fn, ms: ms });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    CustomEvent: CustomEventLike,
  };
  win.window = win;

  const documentStub = {
    getElementById() { return null; },
    addEventListener() {},
    removeEventListener() {},
  };

  const storage = {
    data: {},
    setItem(k, v) { this.data[k] = String(v); },
    getItem(k) { return Object.prototype.hasOwnProperty.call(this.data, k) ? this.data[k] : null; },
    removeItem(k) { delete this.data[k]; },
  };

  const sandbox = {
    window: win,
    document: documentStub,
    console: console,
    localStorage: storage,
    navigator: { userAgent: 'node-harness' },
    CustomEvent: CustomEventLike,
    setTimeout: win.setTimeout,
    clearTimeout: win.clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(STATE_SRC, sandbox, { filename: 'app-state.js' });
  vm.runInContext(PLAYBACK_SRC, sandbox, { filename: 'app-audio-playback.js' });

  const S = win.appState;
  const mod = win.appAudioPlayback;

  // 只跑调用时刻已经排好的 timer：give-up 收尾会再排新 timer，
  // 无快照的话这里会自己转成死循环。
  function flushTimers() {
    for (const [id, timer] of Array.from(timers.entries())) {
      if (!timers.has(id)) continue;
      timers.delete(id);
      timer.fn();
    }
  }

  function speechEnds() {
    return dispatched
      .filter((e) => e.type === 'neko-assistant-speech-end')
      .map((e) => (e.detail || {}).turnId);
  }

  // 把一轮摆成"已经放过音频、四个队列此刻都空了"——
  // 也就是阵间空档和真结束长得一模一样的那个瞬间。
  function primeSpeakingTurn(turnId) {
    S.assistantTurnId = turnId;
    S.assistantSpeechActiveTurnId = turnId;
    S.assistantSpeechStartedTurnId = turnId;
    S.isPlaying = true;
    S.scheduledSources = [];
    S.audioBufferQueue = [];
    S.pendingAudioChunkMetaQueue = [];
    S.incomingAudioBlobQueue = [];
    S.isProcessingIncomingAudioBlob = false;
    S.processingAudioBlobTurnId = null;
  }

  function turnEnd(turnId) {
    win.dispatchEvent(new CustomEventLike('neko-assistant-turn-end', {
      detail: { turnId: turnId, source: 'turn_end' },
    }));
  }

  return {
    win, S, mod, timers,
    flushTimers, speechEnds, primeSpeakingTurn, turnEnd,
    pendingTimers() { return timers.size; },
  };
}

const report = {};

// 阵间空档：turn-end 落在队列瞬时为空的时刻，audio_done 还没到 → 不许收尾。
{
  const h = createHarness();
  h.primeSpeakingTurn('T1');
  h.mod.rememberAssistantAudioSpeechTurn('sid-1', 'T1');
  h.turnEnd('T1');
  report.gap_speech_ends = h.speechEnds();
  report.gap_armed_timer = h.pendingTimers() > 0;
  h.mod.noteAssistantAudioStreamClosed('sid-1');
  report.gap_after_audio_done = h.speechEnds();
  report.gap_timer_cleared = h.pendingTimers();
}

// audio_done 漏发（provider 不支持 / 掉包）→ give-up 到点照样收尾。
{
  const h = createHarness();
  h.primeSpeakingTurn('T2');
  h.turnEnd('T2');
  report.giveup_before = h.speechEnds();
  h.flushTimers();
  report.giveup_after = h.speechEnds();
}

// give-up 到点时又有新一阵音频在排队 → 不能强收（否则等于在播放中途掐断）。
{
  const h = createHarness();
  h.primeSpeakingTurn('T3');
  h.turnEnd('T3');
  h.S.audioBufferQueue.push({ turnId: 'T3', speechId: 'sid-3' });
  h.flushTimers();
  report.giveup_resumed = h.speechEnds();
}

// 出队后、解码完成前的那段空洞：chunk 不在任何队列里，但本轮还没放完。
// 权威信号已到（门是开的），唯一该拦住收尾的就是这段 in-flight 解码。
{
  const h = createHarness();
  h.primeSpeakingTurn('T4');
  h.mod.rememberAssistantAudioSpeechTurn('sid-4', 'T4');
  h.mod.noteAssistantAudioStreamClosed('sid-4');
  h.S.isProcessingIncomingAudioBlob = true;
  h.S.processingAudioBlobTurnId = 'T4';
  h.turnEnd('T4');
  report.decoding_speech_ends = h.speechEnds();
  h.S.isProcessingIncomingAudioBlob = false;
  h.S.processingAudioBlobTurnId = null;
  h.mod.noteAssistantAudioStreamClosed('sid-4');
  report.decoding_after = h.speechEnds();
}

// 别人的解码不该拦住本轮（否则纯文本轮/交错轮会被永远判成没放完）。
{
  const h = createHarness();
  h.primeSpeakingTurn('T5');
  h.mod.rememberAssistantAudioSpeechTurn('sid-5', 'T5');
  h.mod.noteAssistantAudioStreamClosed('sid-5');
  h.S.isProcessingIncomingAudioBlob = true;
  h.S.processingAudioBlobTurnId = 'OTHER-TURN';
  h.turnEnd('T5');
  report.other_turn_decoding = h.speechEnds();
}

// audio_done 只带 speech_id：必须按映射归到它自己那一轮，
// 不能回落到"当前轮"（那时下一轮可能已经开始了）。
{
  const h = createHarness();
  h.primeSpeakingTurn('T6');
  h.mod.rememberAssistantAudioSpeechTurn('sid-6', 'T6');
  // 下一轮已经开始：不查映射就会回落到 assistantTurnId，归错轮。
  h.S.assistantTurnId = 'T7';
  h.mod.noteAssistantAudioStreamClosed('sid-6');
  report.mapped_closed_turn = h.S.assistantAudioStreamClosedTurnId;
  h.turnEnd('T6');
  report.mapped_speech_ends = h.speechEnds();
}

// 打断推 epoch 之后才迟到的 audio_done：作废，不能拿它收尾。
{
  const h = createHarness();
  h.primeSpeakingTurn('T8');
  h.mod.rememberAssistantAudioSpeechTurn('sid-8', 'T8');
  h.mod.noteAssistantAudioStreamClosed('sid-8');
  h.S.incomingAudioEpoch += 1;
  h.turnEnd('T8');
  report.stale_epoch_speech_ends = h.speechEnds();
}

// 干净收尾要置 settledId、落 isPlaying：少了 settledId，
// isAssistantTextResponseInFlight 会把已说完的轮当成在途 → 切语音干等 15s。
{
  const h = createHarness();
  h.primeSpeakingTurn('T9');
  h.mod.rememberAssistantAudioSpeechTurn('sid-9', 'T9');
  h.turnEnd('T9');
  h.mod.noteAssistantAudioStreamClosed('sid-9');
  report.settled_id = h.S.assistantTurnSettledId;
  report.settled_speech_ends = h.speechEnds();
  report.settled_is_playing = h.S.isPlaying;
  report.settled_active_turn = h.S.assistantSpeechActiveTurnId;
}

// 新一轮开始要把上一轮的 close 标记清掉，否则新轮会拿旧信号提前收尾。
{
  const h = createHarness();
  h.primeSpeakingTurn('TA');
  h.mod.rememberAssistantAudioSpeechTurn('sid-a', 'TA');
  h.mod.noteAssistantAudioStreamClosed('sid-a');
  h.win.dispatchEvent(new CustomEventLike('neko-assistant-turn-start', { detail: {} }));
  report.turn_start_cleared_mark = h.S.assistantAudioStreamClosedTurnId;
  report.turn_start_cleared_map = Object.keys(h.S.assistantAudioTurnBySpeechId || {}).length;
}

// 从没播过音频的 sid 发来的 audio_done：跟正在说话的那一轮无关，必须忽略。
// 后端确实有零音频也发信号的轮（整轮 TTS 文本被标点过滤成空）。
{
  const h = createHarness();
  h.primeSpeakingTurn('TB');
  h.mod.rememberAssistantAudioSpeechTurn('sid-b', 'TB');
  h.turnEnd('TB');
  h.mod.noteAssistantAudioStreamClosed('sid-never-played');
  report.unknown_sid_speech_ends = h.speechEnds();
  report.unknown_sid_closed_turn = h.S.assistantAudioStreamClosedTurnId;
  h.mod.noteAssistantAudioStreamClosed('sid-b');
  report.unknown_sid_then_real = h.speechEnds();
}

console.log(JSON.stringify(report));
"""


def _report() -> dict:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required to drive the assistant speech finalize harness")

    script = (
        HARNESS
        .replace("__STATE_PATH__", json.dumps(str(STATE_PATH)))
        .replace("__PLAYBACK_PATH__", json.dumps(str(PLAYBACK_PATH)))
    )
    result: subprocess.CompletedProcess[str] = run_node_script(
        node_path,
        script,
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"harness failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def report() -> dict:
    return _report()


def test_inter_burst_gap_does_not_finalize(report):
    """A turn-end landing in the gap between audio bursts must not finalize."""
    assert report["gap_speech_ends"] == []
    assert report["gap_armed_timer"] is True


def test_audio_done_finalizes_immediately(report):
    """The authoritative signal releases the gate without waiting for give-up."""
    assert report["gap_after_audio_done"] == ["T1"]
    assert report["gap_timer_cleared"] == 0


def test_give_up_timer_finalizes_when_signal_never_arrives(report):
    """A provider that never emits audio_done still finalizes, bounded."""
    assert report["giveup_before"] == []
    assert report["giveup_after"] == ["T2"]


def test_give_up_skips_when_audio_resumed(report):
    """Audio queued again before the deadline must not be cut off."""
    assert report["giveup_resumed"] == []


def test_in_flight_decode_blocks_finalize(report):
    """A chunk dequeued but still decoding is not in any queue, yet still pending."""
    assert report["decoding_speech_ends"] == []
    assert report["decoding_after"] == ["T4"]


def test_in_flight_decode_of_other_turn_does_not_block(report):
    """The in-flight guard is turn-scoped, so it cannot wedge unrelated turns."""
    assert report["other_turn_decoding"] == ["T5"]


def test_audio_done_maps_speech_id_to_its_own_turn(report):
    """audio_done carries only speech_id; it must not land on the current turn."""
    assert report["mapped_closed_turn"] == "T6"
    assert report["mapped_speech_ends"] == ["T6"]


def test_stale_audio_done_after_interrupt_is_ignored(report):
    """A signal that arrives after the epoch bump belongs to a cancelled turn."""
    assert report["stale_epoch_speech_ends"] == []


def test_clean_finalize_marks_turn_settled(report):
    """Finalize must still settle the turn, or switching to voice waits 15s."""
    assert report["settled_id"] == "T9"
    assert report["settled_speech_ends"] == ["T9"]
    assert report["settled_is_playing"] is False
    assert report["settled_active_turn"] is None


def test_turn_start_clears_previous_close_mark(report):
    """A new turn must not inherit the previous turn's stream-closed mark."""
    assert report["turn_start_cleared_mark"] is None
    assert report["turn_start_cleared_map"] == 0


def test_audio_done_for_never_played_speech_is_ignored(report):
    """A signal for audio this client never played says nothing about this turn."""
    assert report["unknown_sid_speech_ends"] == []
    assert report["unknown_sid_closed_turn"] is None
    assert report["unknown_sid_then_real"] == ["TB"]

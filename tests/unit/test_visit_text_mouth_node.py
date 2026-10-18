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

"""Behaviour of ``static/visit/text-mouth-driver.js`` (design §5 PR-13, OD-15 v3).

Runs the driver in a node vm with a fake ``nekoFramePacing`` frame queue and a
fake clock, feeds ``neko-visit-frame`` events and records every
``LanLan1.setMouth`` value.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DRIVER_PATH = PROJECT_ROOT / "static" / "visit" / "text-mouth-driver.js"

HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const SRC = fs.readFileSync(__DRIVER__, 'utf8');
class CustomEventLike { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } }

function createHarness(opts) {
  opts = opts || {};
  const listeners = new Map();
  let clock = 0;
  let frames = [];
  const mouth = [];
  const win = {
    addEventListener(t, f) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(f); },
    dispatchEvent(ev) { (listeners.get(ev.type) || []).forEach((f) => f(ev)); return true; },
    // 语音设置是开的：本场走了 TTS 兜底，判据只看 paced
    appState: { lipSyncActive: false, visitVoiceEnabled: true },
    LanLan1: { live2dModel: {}, setMouth(v) { mouth.push([clock, v]); } },
    appAudioPlayback: { getActiveAvatarModelType: () => opts.modelType || 'live2d' },
    nekoFramePacing: {
      requestPacedFrame(cb) { const f = { cb, dead: false }; frames.push(f); return () => { f.dead = true; }; },
    },
  };
  win.window = win;
  const sandbox = { window: win, console, performance: { now: () => clock }, Math, Date };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'text-mouth-driver.js' });
  function frame(msg) { win.dispatchEvent(new CustomEventLike('neko-visit-frame', { detail: msg })); }
  function run(ms, step) {
    step = step || 16;
    const end = clock + ms;
    while (clock < end) {
      clock += step;
      const due = frames; frames = [];
      due.forEach((f) => { if (!f.dead) f.cb(clock); });
    }
  }
  function delta(text, extra) {
    frame(Object.assign({
      type: 'visit_line_delta', visit_id: 'V'.repeat(26), line_id: 'L1', i: 0, lp: 1, text,
      speaker: { side: 'guest', kind: 'cat', name: 'x', self: true }, paced: 'estimate',
    }, extra || {}));
  }
  return { win, mouth, frame, run, delta, pending: () => frames.length };
}

function peaks(samples) {
  let n = 0;
  for (let i = 1; i < samples.length - 1; i++) {
    if (samples[i][1] > samples[i - 1][1] && samples[i][1] >= samples[i + 1][1] && samples[i][1] > 0.05) n++;
  }
  return n;
}

const report = {};
{
  const h = createHarness();
  const text = '今天天气真好，我们出去走走吧。';
  const est = h.win.nekoVisitTextMouth.estimateSpeechMs(text);   // 13 字 + 逗号 + 句号 = 2710 ms
  report.estimate_ms = est;
  h.delta(text);
  h.run(1000);
  const firstSecond = h.mouth.slice();
  report.estimate_writes = firstSecond.length;
  report.estimate_max = Math.max(...firstSecond.map((m) => m[1]));
  report.estimate_peaks_1s = peaks(firstSecond);
  h.run(3000);
  report.estimate_last = h.mouth[h.mouth.length - 1];
  report.estimate_stopped = h.pending();
  // 收尾 250 ms 衰减：最后 250 ms 内的值不超过衰减包络
  const tail = h.mouth.filter((m) => m[0] > est - 250 && m[0] < est);
  report.tail_samples = tail.length;
  report.tail_envelope_ok = tail.every((m) => m[1] <= 0.8 * (est - m[0]) / 250 + 1e-9);
}
{
  const h = createHarness();
  h.delta('一二三四五', { paced: 'audio' });
  h.delta('一二三四五', { speaker: { side: 'host', kind: 'cat', self: false } });
  h.run(500);
  report.audio_or_peer_writes = h.mouth.length;
}
{
  const h = createHarness();
  h.win.appState.lipSyncActive = true;
  h.delta('一二三四五');
  h.run(500);
  report.rms_blocked_writes = h.mouth.length;
  const h2 = createHarness();
  h2.delta('一二三四五六七八九十');
  h2.run(200);
  const before = h2.mouth.length;
  h2.win.appState.lipSyncActive = true;
  h2.run(1000);
  report.rms_takeover_extra_writes = h2.mouth.length - before;
}
{
  const h = createHarness({ modelType: 'vrm' });
  h.delta('一二三四五');
  h.run(500);
  report.vrm_writes = h.mouth.length;
}
{
  const h = createHarness();
  h.delta('一二三四五六七八九十');
  h.run(300);
  h.frame({ type: 'visit_line_abort', visit_id: 'V'.repeat(26), line_id: 'L1', reason: 'human' });
  const at = h.mouth.length;
  report.abort_last = h.mouth[at - 1][1];
  h.run(500);
  report.abort_extra_writes = h.mouth.length - at;
}
{
  // 分片接续：第二片到达时延长而不是重来
  const h = createHarness();
  h.delta('一二三');       // 540 ms
  h.run(400);
  h.delta('四五六');       // +540 ms
  h.run(2000);
  const nonzero = h.mouth.filter((m) => m[1] > 0);
  report.chained_last_open_at = nonzero[nonzero.length - 1][0];
}
process.stdout.write(JSON.stringify(report));
"""


@pytest.fixture(scope="module")
def report() -> dict:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the text mouth harness")
    result = run_node_script(
        node_path, HARNESS.replace("__DRIVER__", json.dumps(str(DRIVER_PATH))),
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_estimate_paced_delta_drives_live2d_mouth_even_with_voice_setting_on(report):
    # visitVoiceEnabled remains true (TTS fallback): take over lip movement based solely on paced:'estimate'.
    assert report["estimate_writes"] > 30
    assert 0.35 <= report["estimate_max"] <= 0.8
    assert 7 <= report["estimate_peaks_1s"] <= 11


def test_mouth_closes_after_the_estimate_with_a_decay(report):
    assert report["estimate_last"][1] == 0
    assert report["estimate_stopped"] == 0
    assert report["estimate_ms"] == 2710 and report["tail_samples"] >= 10
    assert report["tail_envelope_ok"] is True


def test_audio_paced_or_peer_pieces_are_ignored(report):
    assert report["audio_or_peer_writes"] == 0


def test_rms_lip_sync_is_mutually_exclusive(report):
    assert report["rms_blocked_writes"] == 0
    assert report["rms_takeover_extra_writes"] == 0


def test_only_live2d_is_driven(report):
    assert report["vrm_writes"] == 0


def test_line_abort_stops_immediately(report):
    assert report["abort_last"] == 0
    assert report["abort_extra_writes"] == 0


def test_following_piece_extends_the_same_run(report):
    assert 1000 <= report["chained_last_open_at"] <= 1100

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

"""Behaviour of ``static/visit/visit-pacer.js`` (design §5 PR-13, §4.5 visit_speech_progress).

The pacer runs in a node vm with a fake clock shared by the AudioContext and
``Date.now``; every scenario feeds the real browser events
(``neko-speech-playback-state`` / ``neko-audio-stream-closed`` /
``neko-visit-bound`` / ``neko-visit-frame``) and reads back the
``visit_speech_progress`` frames written to the socket. A second harness loads
the real ``app-state.js`` + ``app-audio-playback.js`` to pin the read-only
``pendingAudioCountForSpeech`` query the pacer relies on.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACER_PATH = PROJECT_ROOT / "static" / "visit" / "visit-pacer.js"
STATE_PATH = PROJECT_ROOT / "static" / "app" / "app-state.js"
PLAYBACK_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-playback.js"

PACER_HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const SRC = fs.readFileSync(__PACER_PATH__, 'utf8');

class CustomEventLike {
  constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
}

function createHarness(opts) {
  opts = opts || {};
  const listeners = new Map();
  const timers = [];
  let nextId = 1;
  let wall = 1000000;
  const ctx = { currentTime: 0 };
  const sent = [];
  const pending = {};
  const socket = { readyState: 1, send(m) { sent.push(Object.assign({ at: ctx.currentTime }, JSON.parse(m))); } };
  const win = {
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener() {},
    dispatchEvent(ev) { (listeners.get(ev.type) || []).slice().forEach((fn) => fn(ev)); return true; },
    appState: { audioPlayerContext: ctx, socket: socket },
    appAudioPlayback: { pendingAudioCountForSpeech: (sid) => pending[sid] || 0 },
  };
  win.window = win;
  const sandbox = {
    window: win,
    console: console,
    WebSocket: { OPEN: 1 },
    Date: { now: () => wall },
    setTimeout(fn, ms) { const id = nextId++; timers.push({ id, at: wall + (ms || 0), fn }); return id; },
    clearTimeout(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    Map, Set, Number, Math, JSON, Object, Array,
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'visit-pacer.js' });

  function emit(type, detail) { win.dispatchEvent(new CustomEventLike(type, { detail })); }
  let frozen = false;
  function advance(ms) {
    const target = wall + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > target) break;
      timers.shift();
      if (!frozen) ctx.currentTime += (next.at - wall) / 1000;
      wall = next.at;
      next.fn();
    }
    if (!frozen) ctx.currentTime += (target - wall) / 1000;
    wall = target;
  }
  function freeze() { frozen = true; }
  function thaw() { frozen = false; }
  function chunk(sid, start, end, extra) {
    const detail = Object.assign({
      reason: 'chunk_scheduled', speechId: sid, scheduledEndAudioTime: end,
      audioContextTime: ctx.currentTime, chunkStartAudioTime: start, chunkDurationSec: end - start,
    }, extra || {});
    emit('neko-speech-playback-state', detail);
  }
  function bind() {
    emit('neko-visit-bound', { socket: socket });
    emit('neko-visit-frame', { type: 'visit_state_change', action: 'started', visit_id: 'V'.repeat(26), side: 'guest' });
  }
  function bySid(sid) { return sent.filter((m) => m.speech_id === sid); }
  return { win, ctx, sent, pending, emit, advance, chunk, bind, bySid, socket, timers, freeze, thaw };
}

const report = {};

// 开播时刻钳位：上一块的排程终点落后于此刻、本块又没带开播字段 → 从此刻开播
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 5;
  h.chunk('old', 4, 5);
  h.advance(5000);          // ctx = 10，上一块终点 5 早已落后
  h.chunk('sid', null, 11, { chunkStartAudioTime: undefined, chunkDurationSec: undefined });
  h.advance(500);
  const prog = h.bySid('sid').filter((m) => !m.ended);
  report.clamp_last_played = prog.length ? prog[prog.length - 1].played_ms : null;
}

// lookahead：块提前 2 s 排程，真开播前 played_ms 不超前
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 12, 13);
  h.advance(1900);
  report.lookahead_sent_before_start = h.bySid('sid').length;
  h.advance(600);           // ctx ≈ 12.5
  const prog = h.bySid('sid').filter((m) => !m.ended);
  report.lookahead_after_start = prog[prog.length - 1].played_ms;
}

// 同 speech_id 多块累加、不重置开播时刻；频率 ≈4 Hz
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 11);
  h.advance(400);
  h.chunk('sid', 11, 12);
  h.advance(1100);          // ctx ≈ 11.5
  const prog = h.bySid('sid').filter((m) => !m.ended);
  report.accumulate_played = prog[prog.length - 1].played_ms;
  const gaps = [];
  for (let i = 1; i < prog.length; i++) gaps.push(Math.round((prog[i].at - prog[i - 1].at) * 1000));
  report.accumulate_min_gap_ms = Math.min(...gaps);
  report.accumulate_count_1500ms = prog.length;
  report.visit_id = prog[0].visit_id;
}

// 正常收口：结束标记 + 播过终点 + 无待解码块 → final:true 恰一次
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 11);
  h.advance(300);
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.advance(2000);
  const ended = h.bySid('sid').filter((m) => m.ended);
  report.normal_ended = ended.map((m) => m.final);
  report.normal_final_played = ended.length ? ended[ended.length - 1].played_ms : null;
}

// 尾块还在解码：结束标记到达时 pending>0 → 不报 final，尾块播完才报
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 11);
  h.advance(900);
  h.pending.sid = 1;        // 尾块在解码
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.advance(600);           // 前面的音频已播完（排空一次），尾块仍未解码完
  report.tail_before = h.bySid('sid').filter((m) => m.ended).map((m) => m.final);
  h.pending.sid = 0;
  h.chunk('sid', h.ctx.currentTime, h.ctx.currentTime + 0.5);
  h.advance(200);
  report.tail_mid = h.bySid('sid').filter((m) => m.ended && m.final).length;
  h.advance(1000);
  const finals = h.bySid('sid').filter((m) => m.ended && m.final);
  report.tail_final_count = finals.length;
  report.tail_final_played = finals.length ? finals[0].played_ms : null;
}

// 两行共用同一 turnId：前一行先收到结束标记、后一行仍在播 → 只有前一行 final
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('a', 10, 11, { turnId: 'T' });
  h.chunk('b', 11, 14, { turnId: 'T' });
  h.emit('neko-audio-stream-closed', { speechId: 'a' });
  h.advance(2000);          // ctx = 12：a 已播完，b 还在播
  report.shared_a = h.bySid('a').filter((m) => m.ended).map((m) => m.final);
  report.shared_b_ended = h.bySid('b').filter((m) => m.ended).length;
  report.shared_b_progress = h.bySid('b').filter((m) => !m.ended).length > 0;
}

// 流式追上：排空 → 再入队 → 再排空 → 收到结束标记，每次排空一条 ended、final 恰一次
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 10.5);
  h.advance(1000);          // 排空一次（还没结束标记）
  h.chunk('sid', h.ctx.currentTime, h.ctx.currentTime + 0.5);
  h.advance(1000);          // 再排空一次
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.advance(1000);
  const ended = h.bySid('sid').filter((m) => m.ended);
  report.stream_ended = ended.map((m) => m.final);
  // 迟到的块 / 第二次结束标记不再产生 final
  h.chunk('sid', h.ctx.currentTime, h.ctx.currentTime + 0.2);
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.advance(1000);
  report.stream_final_total = h.bySid('sid').filter((m) => m.ended && m.final).length;
}

// 被清掉也算排空：ended:true、final:false
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 14);
  h.advance(500);
  h.emit('neko-speech-playback-state', { reason: 'clear_audio_queue', active: false, audioContextTime: h.ctx.currentTime });
  h.advance(300);
  report.cleared = h.bySid('sid').filter((m) => m.ended).map((m) => [m.final, m.played_ms]);
}

// 整段还在 lookahead 里就被清掉：只报一次排空，之后不再发进度（Codex P2）
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 12, 14);
  h.advance(300);
  h.emit('neko-speech-playback-state', { reason: 'clear_audio_queue', active: false, audioContextTime: h.ctx.currentTime });
  h.advance(300);
  const before = h.bySid('sid').length;
  report.pre_start_cleared = h.bySid('sid').filter((m) => m.ended).map((m) => [m.final, m.played_ms]);
  h.advance(3000);
  report.pre_start_cleared_more = h.bySid('sid').length - before;
}

// 一块音频都没排过就收到结束标记：什么都不报，交给后端的起播超时（Codex P2）
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.emit('neko-audio-stream-closed', { speechId: 'silent' });
  h.advance(5000);
  report.no_audio_sent = h.bySid('silent').length;
  // 结束标记到达时尾块还在解码：等它排上、播完，照常收口
  h.pending.late = 1;
  h.emit('neko-audio-stream-closed', { speechId: 'late' });
  h.advance(500);
  report.late_before_chunk = h.bySid('late').length;
  h.pending.late = 0;
  h.chunk('late', h.ctx.currentTime, h.ctx.currentTime + 0.5);
  h.advance(2000);
  report.late_finals = h.bySid('late').filter((m) => m.ended && m.final).length;
}

// 没 visit_bind：一律不回报；串门未开始：不跟踪
{
  const h = createHarness();
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'started', visit_id: 'V'.repeat(26) });
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 11);
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.advance(2000);
  report.unbound_sent = h.sent.length;
  const h2 = createHarness();
  h2.emit('neko-visit-bound', { socket: h2.socket });
  h2.ctx.currentTime = 10;
  h2.chunk('sid', 10, 11);
  h2.advance(2000);
  report.unarmed_sent = h2.sent.length;
}

// 串门结束后的收尾窗口（仪式句 / 简述）照常回报；换了 socket 未重新 bind 则不发
{
  const h = createHarness();
  h.bind();
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'ended', visit_id: 'V'.repeat(26) });
  h.ctx.currentTime = 10;
  h.chunk('ritual', 10, 11);
  h.emit('neko-audio-stream-closed', { speechId: 'ritual' });
  h.advance(2000);
  report.after_end_final = h.bySid('ritual').filter((m) => m.ended && m.final).length;
  h.win.appState.socket = { readyState: 1, send(m) { h.sent.push(JSON.parse(m)); } };
  h.chunk('later', h.ctx.currentTime, h.ctx.currentTime + 1);
  h.advance(2000);
  report.rebound_needed = h.bySid('later').length;
}

// AudioContext 被挂起（时钟停住）：暂停回报、不删记录；恢复后接着报，结束标记照常收口
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 12);
  h.advance(500);
  h.emit('neko-audio-stream-closed', { speechId: 'sid' });
  h.freeze();
  const before = h.bySid('sid').length;
  h.advance(180000);
  report.frozen_progress_total = h.bySid('sid').length - before;
  h.thaw();
  h.advance(3000);
  const finals = h.bySid('sid').filter((m) => m.ended && m.final);
  report.frozen_resumed_finals = finals.length;
  report.frozen_final_played = finals.length ? finals[0].played_ms : null;
}

// 串门开始之前（建房 / 等邀请 / 入房途中重连 / 开始前就中止）收到的状态帧不让 pacer 跟踪普通对话
{
  const h = createHarness();
  h.emit('neko-visit-bound', { socket: h.socket });
  const V = 'V'.repeat(26);
  for (const action of ['pending', 'invite_ready', 'joining', 'reconnecting', 'awaiting_accept']) {
    h.emit('neko-visit-frame', { type: 'visit_state_change', action, visit_id: V });
  }
  h.ctx.currentTime = 10;
  h.chunk('ordinary', 10, 11);
  h.advance(2000);
  report.pre_start_sent = h.sent.length;
  // 开始之前就中止（邀请过期 / 被拒）：ending / ended 不开收尾窗口
  for (const action of ['ending', 'ended']) {
    h.emit('neko-visit-frame', { type: 'visit_state_change', action, visit_id: V });
  }
  h.chunk('ordinary2', h.ctx.currentTime, h.ctx.currentTime + 1);
  h.advance(2000);
  report.aborted_before_start_sent = h.sent.length;
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'started', visit_id: 'V'.repeat(26) });
  h.chunk('line', h.ctx.currentTime, h.ctx.currentTime + 1);
  h.advance(2000);
  report.after_start_sent = h.bySid('line').length > 0;
}

// 页面在串门途中重载：bind 带 active:true（没有 started 帧）也算开始过，结束后的仪式句照常回报（云评审第 3 轮）
{
  const h = createHarness();
  const V = 'W'.repeat(26);
  h.emit('neko-visit-bound', { socket: h.socket, active: true, visit_id: V });
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'ended', visit_id: V });
  h.ctx.currentTime = 10;
  h.chunk('ritual', 10, 11);
  h.emit('neko-audio-stream-closed', { speechId: 'ritual' });
  h.advance(2000);
  report.reload_ritual_final = h.bySid('ritual').filter((m) => m.ended && m.final).length;
}

// 断线期间那一场结束、ended 帧丢了：重新 bind 带 active:false 时收掉残留状态，普通语音不再被跟踪（Codex P2）
{
  const h = createHarness();
  h.bind();                                           // started 过的一场
  h.emit('neko-visit-bound', { socket: h.socket, active: false });
  h.ctx.currentTime = 10;
  h.chunk('ordinary', 10, 11);
  h.advance(2000);
  report.rebound_inactive_sent = h.bySid('ordinary').length;
}

// A 结束后的收尾窗口里新一场 B 的开始前帧到达：A 的仪式句仍按 A 回报，B 没开始不认作 B（Codex P2）
{
  const h = createHarness();
  const A = 'A'.repeat(26), B = 'B'.repeat(26);
  h.emit('neko-visit-bound', { socket: h.socket });
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'started', visit_id: A });
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'ended', visit_id: A });
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'pending', visit_id: B });
  h.ctx.currentTime = 10;
  h.chunk('ritualA', 10, 11);
  h.emit('neko-audio-stream-closed', { speechId: 'ritualA' });
  h.advance(2000);
  report.window_visit_ids = Array.from(new Set(h.bySid('ritualA').map((m) => m.visit_id)));
  // A 的简述还在播时 B 已 started：这一段剩下的回报仍是 A（记录建立时就定下场次）
  h.chunk('debriefA', h.ctx.currentTime, h.ctx.currentTime + 2);
  h.advance(600);
  h.emit('neko-visit-frame', { type: 'visit_state_change', action: 'started', visit_id: B });
  h.emit('neko-audio-stream-closed', { speechId: 'debriefA' });
  h.advance(3000);
  report.debrief_visit_ids = Array.from(new Set(h.bySid('debriefA').map((m) => m.visit_id)));
  h.chunk('lineB', h.ctx.currentTime, h.ctx.currentTime + 1);
  h.advance(1500);
  report.lineB_visit_ids = Array.from(new Set(h.bySid('lineB').map((m) => m.visit_id)));
}

// socket 换了还没重新 bind：发送一直失败，记录静置后丢掉、定时器停下（CodeRabbit）
{
  const h = createHarness();
  h.bind();
  h.ctx.currentTime = 10;
  h.chunk('sid', 10, 11);
  h.advance(300);
  h.win.appState.socket = { readyState: 1, send(m) { h.sent.push(JSON.parse(m)); } };
  h.advance(70000);
  report.unbound_timers_left = h.timers.length;
  report.unbound_sent_after_swap = h.sent.filter((m) => m.at > 10.3).length;
}

process.stdout.write(JSON.stringify(report));
"""

PLAYER_HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const STATE_SRC = fs.readFileSync(__STATE_PATH__, 'utf8');
const PLAYBACK_SRC = fs.readFileSync(__PLAYBACK_PATH__, 'utf8');
class CustomEventLike { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } }
const listeners = new Map();
const win = {
  addEventListener(t, f) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(f); },
  removeEventListener() {},
  dispatchEvent(ev) { (listeners.get(ev.type) || []).forEach((f) => f(ev)); return true; },
  setTimeout() { return 0; }, clearTimeout() {},
  requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
  CustomEvent: CustomEventLike,
};
win.window = win;
const storage = { data: {}, setItem(k, v) { this.data[k] = String(v); }, getItem(k) { return this.data[k] ?? null; }, removeItem(k) { delete this.data[k]; } };
const sandbox = {
  window: win, console, localStorage: storage, navigator: { userAgent: 'node' },
  document: { getElementById() { return null; }, addEventListener() {}, removeEventListener() {} },
  CustomEvent: CustomEventLike, setTimeout: win.setTimeout, clearTimeout: win.clearTimeout,
};
vm.createContext(sandbox);
vm.runInContext(STATE_SRC, sandbox, { filename: 'app-state.js' });
vm.runInContext(PLAYBACK_SRC, sandbox, { filename: 'app-audio-playback.js' });
const S = win.appState;
const mod = win.appAudioPlayback;
const e = S.incomingAudioEpoch;
S.pendingAudioChunkMetaQueue = [{ speechId: 'a', epoch: e }, { speechId: 'a', epoch: e, shouldSkip: true }, { speechId: 'b', epoch: e }];
S.incomingAudioBlobQueue = [{ speechId: 'a', epoch: e }, { kind: 'audio_done', speechId: 'a', epoch: e }, { speechId: 'a', epoch: e - 1 }];
S.audioBufferQueue = [{ speechId: 'a' }, { speechId: 'b' }];
const report = {
  a: mod.pendingAudioCountForSpeech('a'),
  b: mod.pendingAudioCountForSpeech('b'),
  none: mod.pendingAudioCountForSpeech('zzz'),
  empty: mod.pendingAudioCountForSpeech(''),
};
const before = JSON.stringify([S.pendingAudioChunkMetaQueue, S.incomingAudioBlobQueue, S.audioBufferQueue]);
mod.pendingAudioCountForSpeech('a');
report.readonly = before === JSON.stringify([S.pendingAudioChunkMetaQueue, S.incomingAudioBlobQueue, S.audioBufferQueue]);
process.stdout.write(JSON.stringify(report));
"""


def _run(script: str) -> dict:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the visit pacer harness")
    result = run_node_script(node_path, script, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def report() -> dict:
    return _run(PACER_HARNESS.replace("__PACER_PATH__", json.dumps(str(PACER_PATH))))


def test_start_time_is_clamped_to_now_when_previous_end_lags(report):
    # 上一块终点落后 5 s：按 max(prevScheduledEnd, audioContextTime) 换算，不把 5 s 空档算成已播
    # 回报按 ≈250 ms 采样，最后一条最多落后一拍
    assert 250 <= report["clamp_last_played"] <= 600


def test_lookahead_chunk_does_not_run_played_ms_ahead(report):
    # 第一块真开播之前一条都不报（后端收到首条回报就起停滞计时，Codex P1）
    assert report["lookahead_sent_before_start"] == 0
    assert 250 <= report["lookahead_after_start"] <= 600


def test_chunks_of_one_speech_accumulate_without_resetting_start(report):
    assert 1250 <= report["accumulate_played"] <= 1600
    assert report["visit_id"] == "V" * 26


def test_progress_rate_is_about_four_hertz(report):
    assert report["accumulate_min_gap_ms"] >= 250
    assert 5 <= report["accumulate_count_1500ms"] <= 7


def test_stream_close_after_playback_reports_final_once(report):
    assert report["normal_ended"] == [True]
    assert 900 <= report["normal_final_played"] <= 1100


def test_tail_chunk_still_decoding_holds_the_final(report):
    # 结束标记到达时尾块在解码：先只报一次排空（final:false），尾块播完才 final:true
    assert report["tail_before"] == [False]
    assert report["tail_mid"] == 0
    assert report["tail_final_count"] == 1
    assert 1400 <= report["tail_final_played"] <= 1600


def test_lines_sharing_a_turn_id_finalize_by_speech_id(report):
    assert report["shared_a"] == [True]
    assert report["shared_b_ended"] == 0 and report["shared_b_progress"] is True


def test_every_drain_reports_ended_and_final_only_after_end_marker(report):
    assert report["stream_ended"] == [False, False, True]
    assert report["stream_final_total"] == 1


def test_cleared_queue_counts_as_a_non_final_drain(report):
    assert len(report["cleared"]) == 1
    final, played = report["cleared"][0]
    assert final is False and 400 <= played <= 600


def test_clearing_a_speech_still_in_lookahead_drains_once_and_stops(report):
    assert report["pre_start_cleared"] == [[False, 0]]
    assert report["pre_start_cleared_more"] == 0


def test_a_stream_closed_without_any_audio_reports_nothing(report):
    assert report["no_audio_sent"] == 0
    assert report["late_before_chunk"] == 0
    assert report["late_finals"] == 1


def test_nothing_is_sent_before_bind_or_outside_a_visit(report):
    assert report["unbound_sent"] == 0
    assert report["unarmed_sent"] == 0


def test_post_visit_segments_report_and_socket_swap_needs_rebind(report):
    assert report["after_end_final"] == 1
    assert report["rebound_needed"] == 0


def test_frozen_audio_clock_pauses_reporting_and_resumes_to_the_final(report):
    # 时钟停住的 180 s 里零回报（不暂停则约 720 条）；记录不删，恢复后照常报出唯一的 final
    assert report["frozen_progress_total"] == 0
    assert report["frozen_resumed_finals"] == 1
    assert 1900 <= report["frozen_final_played"] <= 2100


def test_pre_start_state_frames_do_not_arm_the_pacer(report):
    assert report["pre_start_sent"] == 0
    assert report["aborted_before_start_sent"] == 0
    assert report["after_start_sent"] is True


def test_bind_after_a_mid_visit_reload_still_opens_the_post_end_window(report):
    assert report["reload_ritual_final"] == 1


def test_rebinding_an_inactive_visit_clears_the_stale_active_state(report):
    assert report["rebound_inactive_sent"] == 0


def test_post_end_window_reports_under_the_visit_that_ended(report):
    assert report["window_visit_ids"] == ["A" * 26]
    assert report["debrief_visit_ids"] == ["A" * 26]
    assert report["lineB_visit_ids"] == ["B" * 26]


def test_records_unsendable_after_a_socket_swap_are_dropped_and_ticking_stops(report):
    assert report["unbound_sent_after_swap"] == 0
    assert report["unbound_timers_left"] == 0


def test_pending_audio_count_for_speech_is_a_read_only_query():
    script = (PLAYER_HARNESS
              .replace("__STATE_PATH__", json.dumps(str(STATE_PATH)))
              .replace("__PLAYBACK_PATH__", json.dumps(str(PLAYBACK_PATH))))
    out = _run(script)
    # a：一条 meta（跳过的不算）+ 一个待解码 blob + 待 flush 尾段的 audio_done + 一个已解码 buffer；旧世代不算
    assert out == {"a": 4, "b": 2, "none": 0, "empty": 0, "readonly": True}

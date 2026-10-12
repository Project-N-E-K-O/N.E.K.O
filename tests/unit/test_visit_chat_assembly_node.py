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

"""Behaviour of ``static/app/app-react-chat-window/visit-chat.js`` (design §5 PR-12).

The real script runs in a node vm with a fake clock, a tiny DOM shim (enough
for its dialogs), a fake chat host and a scripted ``fetch``. Scenarios:
line assembly (out of order, gaps, final overwrite, abort, 20 s stall),
transcript export for both response shapes, the details pager, the
composer gate and backend refusals, the persona 409 retry order, the
debrief chips, and the ``visit_bind`` state probe racing a state frame
(loaded together with the real ``visit-pacer.js``).
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_PATH = PROJECT_ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
PACER_PATH = PROJECT_ROOT / "static" / "visit" / "visit-pacer.js"

HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const CHAT_SRC = fs.readFileSync(__CHAT_PATH__, 'utf8');
const PACER_SRC = fs.readFileSync(__PACER_PATH__, 'utf8');
const VID = 'AAAAAAAAAAAAAAAAAAAAAA';
const VID2 = 'BBBBBBBBBBBBBBBBBBBBBB';

class CustomEventLike {
  constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
}

// ── 极简 DOM：对话框只用到 createElement / appendChild / removeChild / textContent / 事件 ──
class Node {
  constructor(tag) {
    this.tagName = String(tag || '').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._text = '';
    this.className = '';
    this.attrs = {};
    this.listeners = {};
    this.style = {};
    this.value = '';
    this.disabled = false;
    this.checked = false;
  }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = []; this._text = String(v); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); child.parentNode = null; return child; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type, ev) { (this.listeners[type] || []).forEach((fn) => fn(Object.assign({ target: this, stopPropagation() {}, preventDefault() {} }, ev || {}))); }
  click() { if (!this.disabled) this.dispatch('click'); }
  focus() {}
  all(pred, out) { out = out || []; if (pred(this)) out.push(this); this.children.forEach((c) => c.all(pred, out)); return out; }
}

function createHarness(opts) {
  opts = opts || {};
  const listeners = new Map();
  const timers = [];
  let nextId = 1;
  let wall = 1700000000000;
  const sent = [];
  const fetches = [];
  const routes = opts.routes || {};
  const messages = [];
  const rollbacks = [];
  const toasts = [];
  const sendCalls = [];
  const socket = { readyState: 1, send(m) { sent.push(JSON.parse(m)); } };
  const body = new Node('body');
  const docListeners = [];
  const documentShim = {
    body: body, documentElement: body,
    createElement: (tag) => new Node(tag),
    addEventListener(type, fn) { docListeners.push({ type, fn }); },
    removeEventListener(type, fn) { const i = docListeners.findIndex((l) => l.type === type && l.fn === fn); if (i >= 0) docListeners.splice(i, 1); },
  };
  function pressKey(key) { docListeners.filter((l) => l.type === 'keydown').slice().forEach((l) => l.fn({ key })); }
  const host = {
    appendMessage(m) { messages.push(JSON.parse(JSON.stringify(m))); return m; },
    updateMessage(id, patch) { const m = messages.find((x) => x.id === id); if (m) Object.assign(m, JSON.parse(JSON.stringify(patch))); },
    getState() { return { messages: messages }; },
  };
  const I = {
    renderWindow() {},
    rollbackLastDraft(id) { rollbacks.push(id); },
    clearPendingRollbackDraft() {},
  };
  const win = {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
    removeEventListener() {},
    dispatchEvent(ev) { (listeners.get(ev.type) || []).slice().forEach((fn) => fn(ev)); return true; },
    __appReactChatWindowParts: I,
    reactChatWindowHost: host,
    lanlan_config: { lanlan_name: '咪咪', master_display_name: '阿明' },
    appState: { socket: socket, audioPlayerContext: { currentTime: 0 } },
    location: { pathname: '/' },
    showStatusToast(text) { toasts.push(text); },
    visitEnabled: true,
    nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'tok' }) },
    appChat: {
      renderVisitLine(spec) {
        win.__lastAvatar = spec.avatarUrl || '';
        const blocks = [{ type: 'text', text: spec.text, plain_text: true }];
        if (spec.notice) blocks.push({ type: 'status', text: spec.notice });
        const existing = messages.find((x) => x.id === spec.messageId);
        if (existing) Object.assign(existing, { blocks: blocks, author: spec.author, status: 'sent' });
        else messages.push({ id: spec.messageId, author: spec.author, blocks: blocks, status: 'sent' });
      },
    },
    appButtons: {
      sendTextPayload(text, options) { sendCalls.push({ text, options }); socket.send(JSON.stringify({ action: 'stream_data', data: text, request_id: options.requestId, source: options.source })); return Promise.resolve(true); },
    },
    appendReactChatBlocks(response) {
      messages.push({ id: 'plugin-blocks-' + response.request_id + '-' + (wall++) + '-1', role: 'system', blocks: JSON.parse(JSON.stringify(response.blocks)) });
      return true;
    },
  };
  win.window = win;
  win.t = () => '';
  async function fetchStub(url, init) {
    fetches.push({ url: url, method: (init && init.method) || 'GET', body: init && init.body ? JSON.parse(init.body) : null, headers: (init && init.headers) || {} });
    const handler = routes[url] || Object.keys(routes).filter((k) => k.endsWith('*') && url.startsWith(k.slice(0, -1))).map((k) => routes[k])[0];
    const reply = handler ? await handler(url, init) : { status: 404, body: {} };
    return {
      status: reply.status, ok: reply.status >= 200 && reply.status < 300,
      async json() { return reply.body; },
      clone() { return this; },
    };
  }
  const sandbox = {
    window: win, document: documentShim, console: console, fetch: fetchStub,
    CustomEvent: CustomEventLike, WebSocket: { OPEN: 1 },
    navigator: { clipboard: { writeText: async () => {} } },
    Blob: function (parts) { this.text = parts.join(''); },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
    Date: { now: () => wall },
    setTimeout(fn, ms) { const id = nextId++; timers.push({ id, at: wall + (ms || 0), fn, every: 0 }); return id; },
    clearTimeout(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    setInterval(fn, ms) { const id = nextId++; timers.push({ id, at: wall + ms, fn, every: ms }); return id; },
    clearInterval(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    Map, Set, Number, Math, JSON, Object, Array, String, Promise, Infinity, RegExp, Error,
    localStorage: (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; })(),
  };
  vm.createContext(sandbox);
  if (opts.withPacer) vm.runInContext(PACER_SRC, sandbox, { filename: 'visit-pacer.js' });
  vm.runInContext(CHAT_SRC, sandbox, { filename: 'visit-chat.js' });

  function advance(ms) {
    const target = wall + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > target) break;
      wall = next.at;
      if (next.every) next.at += next.every; else timers.shift();
      next.fn();
    }
    wall = target;
  }
  async function settle() {
    for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r));
  }
  const chat = win.nekoVisitChat;
  function frame(f) { chat.handleFrame(f); }
  function state(action, extra) { frame(Object.assign({ type: 'visit_state_change', action: action, visit_id: VID, side: 'host', ts: 1 }, extra || {})); }
  function dialogs() { return body.all((n) => n.className && n.className.indexOf('visit-dialog ') === 0 || n.className === 'visit-dialog'); }
  function buttonsOf(node) { return node.all((n) => n.tagName === 'BUTTON'); }
  function lineText(id) { const m = messages.find((x) => x.id === 'visit-line-' + id); return m ? m.blocks.map((b) => b.text).join('|') : null; }
  return { win, I, chat, sent, fetches, messages, rollbacks, toasts, sendCalls, socket, body, advance, settle, frame, state, dialogs, buttonsOf, lineText, timers, pressKey };
}

const report = {};

(async () => {
  // ── 拼接：乱序落位、缺片留占位不补洞、final 覆盖 ──
  {
    const h = createHarness();
    h.state('started');
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L1', i: 2, text: 'C', speaker: sp });
    report.gap_first = h.lineText('L1');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L1', i: 0, text: 'A', speaker: sp });
    report.gap_second = h.lineText('L1');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L1', i: 1, text: 'B', speaker: sp });
    report.gap_filled = h.lineText('L1');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'L1', text: '全文', final: true, truncated: false, speaker: sp });
    report.final_text = h.lineText('L1');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L1', i: 3, text: '迟到', speaker: sp });
    report.after_final = h.lineText('L1');
    report.plain = h.messages.find((m) => m.id === 'visit-line-L1').blocks[0].plain_text === true;
  }
  // ── abort 立即截断到 i_done ──
  {
    const h = createHarness();
    h.state('started');
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    ['A', 'B', 'C'].forEach((t, i) => h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L2', i: i, text: t, speaker: sp }));
    h.frame({ type: 'visit_line_abort', visit_id: VID, line_id: 'L2', i_done: 1, reason: 'human_interrupt' });
    report.abort_text = h.lineText('L2');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'L2', text: 'A', final: true, truncated: true, speaker: sp });
    report.abort_final = h.lineText('L2');
  }
  // ── 20 s 没有新片也没有 final：本地截断；之后 final 照样覆盖 ──
  {
    const h = createHarness();
    h.state('started');
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'L3', i: 0, text: '一半', speaker: sp });
    h.advance(19000);
    report.stall_before = h.lineText('L3');
    h.advance(1500);
    report.stall_after = h.lineText('L3');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'L3', text: '一半和另一半', final: true, truncated: false, speaker: sp });
    report.stall_final = h.lineText('L3');
  }
  // ── 导出：本地已删 → 云端精简形态；本地完整形态 ──
  {
    const h = createHarness();
    const cloud = { source: 'cloud', visit_id: VID, role: 'guest', lines: [
      { lp: 1, side: 'host', from: 'peer_cat', ts: 1700000000, text: '你好', truncated: false },
      { lp: 2, side: 'guest', from: 'own_cat', ts: 1700000001, text: '你好呀', truncated: true },
      { lp: 3, side: 'host', from: 'peer_human', ts: 1700000002, text: '欢迎', truncated: false },
    ] };
    const text = h.chat._test.buildTranscriptText(cloud);
    const body = text.split('\n').slice(1);
    report.cloud_lines = body.length;
    report.cloud_labels = body.map((l) => l.replace(/^\[[^\]]*\] /, '').split('：')[0]);
    report.cloud_clean = !/undefined|null/.test(text);
    const local = { source: 'spool', visit_id: VID, peer_short_id: 'ABCDEF', lines: [
      { line_id: 'x', lp: 1, side: 'host', speaker_kind: 'cat', ts: 1700000000, text: '甲', truncated: false },
      { line_id: 'y', lp: 1, side: 'guest', speaker_kind: 'human', ts: 1700000001, text: '乙', truncated: false },
    ] };
    const text2 = h.chat._test.buildTranscriptText(local);
    report.local_lines = text2.split('\n').length - 1;
    report.local_clean = !/undefined|null/.test(text2);
  }
  // ── 查看详情：500 / 500 / 200 行三页，按 next_cursor 翻到第 3 页，之后不再出「下一页」 ──
  {
    const pages = { '': { n: 500, next: 'c1' }, c1: { n: 500, next: 'c2' }, c2: { n: 200, next: null } };
    const seen = [];
    const routes = {};
    routes['/api/visit/details/*'] = async (url) => {
      const m = /cursor=([^&]+)/.exec(url);
      const cursor = m ? decodeURIComponent(m[1]) : '';
      seen.push(cursor);
      const page = pages[cursor];
      const lines = [];
      for (let i = 0; i < page.n; i++) lines.push({ lp: i, side: 'host', host: { from: 'own_cat', ts: 1, text: 't' + i, truncated: false }, guest: null, status: 'only_host' });
      const b = { visit_id: VID, duration_s: 125, free_minutes_deducted: 3, usage: { llm_input_tokens: 1, llm_output_tokens: 2, tts_requests: 3, tts_chars: 4 }, lines: lines, uploaded: { host: true, guest: true } };
      if (page.next) b.next_cursor = page.next;
      return { status: 200, body: b };
    };
    const h = createHarness({ routes });
    const dialog = await h.chat._test.openDetails(VID);
    await h.settle();
    const nextButton = () => h.buttonsOf(dialog.root).find((b) => b.attrs['data-visit-dialog-button'] === 'next');
    report.details_page1_next = !!nextButton();
    nextButton().click();
    await h.settle();
    nextButton().click();
    await h.settle();
    report.details_rows = dialog.root.all((n) => typeof n.className === 'string' && n.className.indexOf('visit-details-row') === 0).length;
    report.details_last_next = !!nextButton();
    report.details_cursors = seen;
    report.details_urls_ok = h.fetches.every((f) => f.url.indexOf('/api/visit/details/' + VID + '?') === 0);
  }
  // ── 输入：接待确认期间回车 → 不发 WS、文字放回输入框 ──
  {
    const h = createHarness();
    h.state('pending');
    h.state('awaiting_accept');
    report.gate_active = h.chat.isActive();
    const accepted = h.I.submitVisitChatText({ text: '你好', requestId: 'r1', submitMethod: 'enter' });
    h.advance(1);
    report.gate_accepted = accepted;
    report.gate_ws = h.sent.filter((m) => m.action === 'stream_data').length;
    report.gate_send_calls = h.sendCalls.length;
    report.gate_rollback = h.rollbacks.slice();
  }
  // ── 输入：后端拒绝 → 按 request_id 标那一个气泡「未送达」并恢复文字 ──
  {
    const h = createHarness();
    h.state('started');
    h.I.submitVisitChatText({ text: '第一句', requestId: 'r1' });
    h.I.submitVisitChatText({ text: '第二句', requestId: 'r2' });
    report.refuse_sources = h.sendCalls.map((c) => c.options.source);
    h.chat.handleStatus('VISIT_E_BUSY', { visit_id: VID, request_id: 'r2' });
    const m1 = h.messages.find((m) => m.id === 'visit-local-r1');
    const m2 = h.messages.find((m) => m.id === 'visit-local-r2');
    report.refuse_m1_status = m1.status;
    report.refuse_m2_status = m2.status;
    report.refuse_m2_notice = m2.blocks.length > 1;
    report.refuse_rollbacks = h.rollbacks.slice();
    report.local_plain = m1.blocks[0].plain_text === true;
    // 第一句的回显到了：认领本地气泡（原地换成后端清洗后的文字）
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'H1', text: '第一句（清洗后）', final: true, truncated: false,
      speaker: { side: 'host', kind: 'human', name: '', self: true }, addressee: { side: 'guest', kind: 'cat' } });
    report.echo_claimed = h.messages.find((m) => m.id === 'visit-local-r1').blocks[0].text;
    report.echo_no_dup = h.messages.filter((m) => m.id === 'visit-line-H1').length;
    // 收件人切到自家猫娘
    h.I.handleVisitBarAction('to_own');
    h.I.submitVisitChatText({ text: '给她的', requestId: 'r3' });
    report.recipient_own = h.sendCalls[h.sendCalls.length - 1].options.source;
  }
  // ── guest 侧与收尾：拦下 ──
  {
    const h = createHarness();
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID, side: 'guest', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'departed', visit_id: VID, side: 'guest', ts: 1 });
    report.guest_accepted = h.I.submitVisitChatText({ text: 'x', requestId: 'g1' });
    report.guest_disabled = h.I.getVisitChatView().composerDisabled;
    const h2 = createHarness();
    h2.state('started');
    h2.state('wrap_up');
    report.wrap_accepted = h2.I.submitVisitChatText({ text: 'x', requestId: 'w1' });
    h2.state('ending');
    report.ending_active = h2.chat.isActive();
  }
  // ── 出门：预览在 join 之前；预览失败不发 join；屏蔽者不给确认；409 人设先确认再重试一次 ──
  {
    const routes = {};
    let previewStatus = 200;
    let blocked = false;
    let joinReplies = [];
    routes['/api/visit/invites/*'] = async () => previewStatus === 200
      ? { status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: true, expires_at: 9e9, locally_blocked: blocked } }
      : { status: previewStatus, body: { code: previewStatus === 410 ? 'invite_expired' : 'invite_invalid' } };
    routes['/api/visit/rooms/' + VID2 + '/join'] = async () => joinReplies.shift() || { status: 202, body: { ok: true } };
    const h = createHarness({ routes });
    let personaCalls = 0;
    let personaJoinsBefore = -1;
    h.win.openVisitPersonaPreview = async () => {
      personaCalls += 1;
      personaJoinsBefore = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
      return true;
    };
    // 预览 404：不弹框、不 join
    previewStatus = 404;
    await h.chat._test.joinWithCode('ABCDEFGH23');
    report.preview_fail_joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
    report.preview_fail_dialogs = h.dialogs().length;
    // 屏蔽：确认框没有「出门」按钮
    previewStatus = 200;
    blocked = true;
    const pending = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    const d = h.dialogs()[0];
    report.blocked_buttons = h.buttonsOf(d).map((b) => b.attrs['data-visit-dialog-button']);
    report.blocked_text = d.textContent;
    h.buttonsOf(d)[0].click();
    await pending;
    // 正常：确认后 join；409 人设 → 先确认再重试一次
    blocked = false;
    joinReplies = [{ status: 409, body: { code: 'VISIT_PERSONA_UNREVIEWED', state: 'unreviewed' } }, { status: 202, body: { ok: true } }];
    const p2 = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    const d2 = h.dialogs()[0];
    report.confirm_text = d2.textContent;
    h.buttonsOf(d2).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await p2;
    const joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1);
    report.join_count = joins.length;
    report.join_body = joins[0].body;
    report.persona_calls = personaCalls;
    report.persona_joins_before = personaJoinsBefore;
    const order = h.fetches.map((f) => f.url.indexOf('/preview') !== -1 ? 'preview' : (f.url.indexOf('/join') !== -1 ? 'join' : 'other'));
    report.order_after_confirm = order.slice(-3);
  }
  // ── 叫她回来：route/end 请求体逐字段 ──
  {
    const routes = { '/api/visit/route/end': async () => ({ status: 409, body: { ok: false, code: 'VISIT_RECALL_ALREADY' } }) };
    const h = createHarness({ routes });
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID, side: 'guest', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'departed', visit_id: VID, side: 'guest', ts: 1 });
    h.I.handleVisitBarAction('recall');
    await h.settle();
    const call = h.fetches.find((f) => f.url === '/api/visit/route/end');
    report.recall_body = call && call.body;
    report.recall_toast = h.toasts.length > 0;
  }
  // ── debrief：芯片去重 + ack；点「记成日记」带 lanlan_name；预览块 committing 帧 ──
  {
    const routes = { '/api/visit/debrief/choice': async () => ({ status: 200, body: { ok: true, applied: 'preview', preview: { diary: 'd', facts: [] } } }) };
    const h = createHarness({ routes });
    const chips = { type: 'chat_blocks', request_id: 'visit-debrief:' + VID, blocks: [
      { type: 'text', text: '要记成日记吗？' },
      { type: 'buttons', buttons: [
        { id: 'diary', label: '记成日记', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'diary' } },
        { id: 'forget', label: '不记', action: 'visit_debrief_choice', variant: 'danger', payload: { visit_id: VID, choice: 'forget' } },
      ] },
    ] };
    report.chips_handled = h.chat.handleChatBlocks(chips);
    h.chat.handleChatBlocks(chips);   // bind 重放：同一 request_id 不重复上屏
    report.chips_rendered = h.messages.filter((m) => m.id.indexOf('plugin-blocks-visit-debrief:' + VID) === 0).length;
    report.chips_acks = h.sent.filter((m) => m.action === 'visit_debrief_chip_ack').map((m) => m.request_id);
    const chipMsg = h.messages.find((m) => m.id.indexOf('plugin-blocks-visit-debrief:') === 0);
    h.win.dispatchEvent(new CustomEventLike('react-chat-window:action', { detail: { message: chipMsg, action: chipMsg.blocks[1].buttons[0] } }));
    await h.settle();
    const post = h.fetches.find((f) => f.url === '/api/visit/debrief/choice');
    report.choice_body = post && post.body;
    report.chips_disabled = chipMsg.blocks[1].buttons.every((b) => b.disabled === true);
    // 预览块到了，之后别页推进到 committing：「不记」置灰、「写入」留着
    const preview = { type: 'chat_blocks', request_id: 'visit-debrief-preview:' + VID, blocks: [
      { type: 'text', text: 'd', plain_text: true },
      { type: 'buttons', buttons: [
        { id: 'confirm', label: '写入', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'confirm' } },
        { id: 'forget', label: '不记', action: 'visit_debrief_choice', variant: 'danger', payload: { visit_id: VID, choice: 'forget' } },
      ] },
    ] };
    h.chat.handleChatBlocks(preview);
    h.frame({ type: 'visit_debrief', visit_id: VID, phase: 'committing', request_id: 'visit-debrief-preview:' + VID, seq: 0 });
    const pm = h.messages.find((m) => m.id.indexOf('plugin-blocks-visit-debrief-preview:') === 0);
    report.committing_buttons = pm.blocks.find((b) => b.type === 'buttons').buttons.map((b) => [b.payload.choice, b.disabled === true]);
    report.committing_status = pm.blocks[pm.blocks.length - 1].type;
    // 失败块 seq 1 → 再来 seq 2：旧块两钮置灰，新块可点
    const failed = (seq) => ({ type: 'chat_blocks', request_id: 'visit-debrief-failed:' + VID + ':' + seq, blocks: [
      { type: 'text', text: 'failed' },
      { type: 'buttons', buttons: [
        { id: 'retry', label: '重试', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'confirm', seq: seq } },
        { id: 'abandon', label: '放弃', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'abandon', seq: seq, written: { facts: false, cache: false }, unconfirmed: { facts: false, cache: false } } },
      ] },
    ] });
    h.chat.handleChatBlocks(failed(1));
    h.chat.handleChatBlocks(failed(2));
    const f1 = h.messages.find((m) => m.id.indexOf('plugin-blocks-visit-debrief-failed:' + VID + ':1-') === 0);
    const f2 = h.messages.find((m) => m.id.indexOf('plugin-blocks-visit-debrief-failed:' + VID + ':2-') === 0);
    report.failed_old_disabled = f1.blocks.find((b) => b.type === 'buttons').buttons.every((b) => b.disabled === true);
    report.failed_new_enabled = f2.blocks.find((b) => b.type === 'buttons').buttons.every((b) => !b.disabled);
  }
  // ── 放弃：有结果不明的写入 → 先确认、不明优先的文案 ──
  {
    const routes = { '/api/visit/debrief/choice': async () => ({ status: 200, body: { ok: true, applied: 'abandoned', written: { facts: true, cache: false }, unconfirmed: { facts: false, cache: true } } }) };
    const h = createHarness({ routes });
    h.chat.handleChatBlocks({ type: 'chat_blocks', request_id: 'visit-debrief-failed:' + VID + ':3', blocks: [
      { type: 'buttons', buttons: [
        { id: 'abandon', label: '放弃', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'abandon', seq: 3, written: { facts: true, cache: false }, unconfirmed: { facts: false, cache: true } } },
      ] },
    ] });
    const msg = h.messages[h.messages.length - 1];
    const run = h.chat._test.onDebriefAction(msg, msg.blocks[0].buttons[0]);
    await h.settle();
    report.abandon_confirm_dialog = h.dialogs().length;
    report.abandon_posted_before_confirm = h.fetches.filter((f) => f.url === '/api/visit/debrief/choice').length;
    const d = h.dialogs()[0];
    h.buttonsOf(d).find((b) => b.attrs['data-visit-dialog-button'] === 'ok').click();
    await run;
    const post = h.fetches.find((f) => f.url === '/api/visit/debrief/choice');
    report.abandon_body = post.body;
    report.abandon_status = msg.blocks[msg.blocks.length - 1].text;
  }
  // ── visit_bind 之后读 state：读的期间收到 started → 迟到的旧快照不清掉进行中状态 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'awaiting_accept', enabled: true } }; } };
    const h = createHarness({ routes, withPacer: true });
    const bound = [];
    h.win.addEventListener('neko-visit-bound', (ev) => bound.push(ev.detail));
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    const started = { type: 'visit_state_change', action: 'started', visit_id: VID, side: 'host', ts: 1 };
    h.win.dispatchEvent(new CustomEventLike('neko-visit-frame', { detail: started }));
    h.chat.handleFrame(started);
    release();
    await probe;
    report.race_detail_has_active = Object.prototype.hasOwnProperty.call(bound[0] || {}, 'active');
    report.race_pacer_armed = h.win.nekoVisitPacer.isArmed();
    report.race_chat_phase = h.chat._test.state().visit.phase;
  }
  // ── 页面在串门进行中重载 → active:true；没有串门 → active:false ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true, peer: { cat_name: '小白' }, transcript: [] } }) };
    const h = createHarness({ routes, withPacer: true });
    const bound = [];
    h.win.addEventListener('neko-visit-bound', (ev) => bound.push(ev.detail));
    await h.chat.afterBind(h.socket, '咪咪');
    report.reload_active = bound[0].active;
    report.reload_visit = bound[0].visit_id === VID;
    report.reload_pacer_armed = h.win.nekoVisitPacer.isArmed();
    report.reload_chat_phase = h.chat._test.state().visit.phase;
    const routes2 = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h2 = createHarness({ routes: routes2, withPacer: true });
    const bound2 = [];
    h2.win.addEventListener('neko-visit-bound', (ev) => bound2.push(ev.detail));
    await h2.chat.afterBind(h2.socket, '咪咪');
    report.idle_active = bound2[0].active;
    report.idle_bar_entry = h2.I.getVisitChatView().bar.actions.map((a) => a.id);
  }
  // ── 被拒那句之后又提交过：不把旧文字塞回输入框，气泡照样标「未送达」 ──
  {
    const h = createHarness();
    h.state('started');
    h.I.submitVisitChatText({ text: '第一句', requestId: 'r1' });
    h.I.submitVisitChatText({ text: '第二句', requestId: 'r2' });
    h.chat.handleStatus('VISIT_E_BUSY', { visit_id: VID, request_id: 'r1' });
    report.later_submit_rollbacks = h.rollbacks.slice();
    report.later_submit_m1 = h.messages.find((m) => m.id === 'visit-local-r1').status;
    report.attachments_ignored = h.sendCalls.every((c) => c.options.ignoreComposerAttachments === true);
  }
  // ── 详情：说话人一律按请求者一侧标（guest 看 host 记的那份也一样） ──
  {
    const routes = {};
    routes['/api/visit/details/*'] = async () => ({ status: 200, body: { visit_id: VID, requester_role: 'guest', duration_s: 1, free_minutes_deducted: 0, usage: null,
      uploaded: { host: true, guest: true }, lines: [
        { lp: 1, side: 'host', host: { from: 'own_cat', ts: 1, text: 'a', truncated: false }, guest: { from: 'peer_cat', ts: 1, text: 'a', truncated: false }, status: 'agree' },
        { lp: 2, side: 'guest', host: { from: 'peer_cat', ts: 2, text: 'b', truncated: false }, guest: { from: 'own_cat', ts: 2, text: 'c', truncated: false }, status: 'differ' },
      ] } });
    const h = createHarness({ routes });
    const dialog = await h.chat._test.openDetails(VID);
    await h.settle();
    report.details_labels = dialog.root.all((n) => n.className === 'visit-details-from').map((n) => n.textContent);
  }
  // ── 导出：本侧未知 → 邀请方 / 来访方；见过这一场 → 我 / 对方 ──
  {
    const h = createHarness();
    const local = { source: 'memory', visit_id: VID2, lines: [
      { line_id: 'x', lp: 1, side: 'host', speaker_kind: 'cat', ts: 1, text: '甲', truncated: false },
      { line_id: 'y', lp: 1, side: 'guest', speaker_kind: 'human', ts: 1, text: '乙', truncated: false },
    ] };
    const label = (text) => text.split('\n').slice(1).map((l) => l.replace(/^\[[^\]]*\] /, '').split('：')[0]);
    report.export_unknown = label(h.chat._test.buildTranscriptText(local));
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'guest', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'ended', visit_id: VID2, side: 'guest', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID, side: 'host', ts: 1 });
    report.export_known = label(h.chat._test.buildTranscriptText(local));
  }
  // ── 读 state 期间到了不带阶段的帧：阶段仍按快照补上 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.chat.handleFrame({ type: 'visit_state_change', action: 'peer_hidden', visit_id: VID, side: 'host', ts: 1 });
    release();
    await probe;
    report.nonphase_phase = h.chat._test.state().visit.phase;
    report.nonphase_active = h.chat.isActive();
  }
  // ── 接待请求一时失败：框不关、按钮恢复；再点成功才关 ──
  {
    const replies = [{ status: 500, body: {} }, { status: 200, body: { ok: true } }];
    const routes = {};
    routes['/api/visit/rooms/' + VID + '/accept'] = async () => replies.shift();
    const h = createHarness({ routes });
    h.state('pending');
    h.frame({ type: 'visit_invite', visit_id: VID, peer_name: '小白', peer_short_id: 'QWERTY', cross_region: false, expires_at: 1700000000 + 60 });
    const d = h.dialogs()[0];
    h.buttonsOf(d).find((b) => b.attrs['data-visit-dialog-button'] === 'accept').click();
    await h.settle();
    report.invite_after_fail_open = h.dialogs().length;
    report.invite_after_fail_enabled = h.buttonsOf(h.dialogs()[0]).every((b) => !b.disabled);
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'accept').click();
    await h.settle();
    report.invite_after_ok_open = h.dialogs().length;
    report.invite_posts = h.fetches.filter((f) => f.url.endsWith('/accept')).map((f) => f.body.accept);
  }
  // ── 举报：409 要登录但已排队 → 按已排队 ──
  {
    const routes = { '/api/visit/report': async () => ({ status: 409, body: { ok: false, code: 'VISIT_LOGIN_REQUIRED', queued: true } }) };
    const h = createHarness({ routes });
    const msg = { id: 'visit-ended-' + VID, blocks: [{ type: 'buttons', buttons: [{ id: 'report', label: '举报', action: 'visit_report', payload: { visit_id: VID } }] }] };
    h.messages.push(msg);
    h.win.dispatchEvent(new CustomEventLike('react-chat-window:action', { detail: { message: msg, action: msg.blocks[0].buttons[0] } }));
    await h.settle();
    const d = h.dialogs()[0];
    h.buttonsOf(d).find((b) => b.attrs['data-visit-dialog-button'] === 'submit').click();
    await h.settle();
    report.report_queued_button = msg.blocks[0].buttons[0].disabled === true;
  }
  // ── Escape 关得掉可关闭的对话框（焦点不在对话框里也行） ──
  {
    const routes = { '/api/visit/invites/*': async () => ({ status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }) };
    const h = createHarness({ routes });
    const p = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    report.escape_before = h.dialogs().length;
    h.pressKey('Escape');
    await p;
    report.escape_after = h.dialogs().length;
    report.escape_joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
  }
  // ── 读 state 期间先到了 ended：迟到的旧快照不把这一场救活 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.chat.handleFrame({ type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', ts: 1 });
    release();
    await probe;
    report.ended_race_active = h.chat.isActive();
    report.ended_race_phase = h.chat._test.state().visit.phase;
  }
  // ── guest：预览到的对端名带进本场；换场清访客头像 ──
  {
    const routes = {
      '/api/visit/invites/*': async () => ({ status: 200, body: { visit_id: VID2, host_display_name: '邻居家', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }),
    };
    routes['/api/visit/rooms/' + VID2 + '/join'] = async () => ({ status: 202, body: { ok: true } });
    const h = createHarness({ routes });
    h.win.dispatchEvent(new CustomEventLike('neko-visit-first-frame', { detail: { dataURL: 'data:image/png;base64,OLD' } }));
    const p = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await p;
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'guest', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'departed', visit_id: VID2, side: 'guest', ts: 1 });
    report.guest_badge = h.I.getVisitChatView().bar.badge;
    h.frame({ type: 'visit_line', visit_id: VID2, line_id: 'z1', text: '你好', final: true, truncated: false,
      speaker: { side: 'host', kind: 'cat', name: '小黑', self: false } });
    const m = h.messages.find((x) => x.id === 'visit-line-z1');
    report.stale_avatar_used = !!(m && m.avatarUrl);
    report.renderVisitLine_avatar = h.win.__lastAvatar || '';
  }
  // ── 重连轮询读到「没在串门」：收掉这一场，输入框还给普通聊天 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.state('reconnecting');
    report.recon_before = h.I.getVisitChatView().composerDisabled;
    h.advance(5100);
    await h.settle();
    report.recon_after_active = h.chat.isActive();
    report.recon_after_disabled = h.I.getVisitChatView().composerDisabled;
  }
  // ── 重连轮询的旧请求回来时已换成新一场：不拿旧快照收新一场 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }; } };
    const h = createHarness({ routes });
    h.state('started');
    h.state('reconnecting');
    h.advance(5100);          // 轮询请求发出、挂起
    await h.settle();
    h.frame({ type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'host', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'started', visit_id: VID2, side: 'host', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'reconnecting', visit_id: VID2, side: 'host', ts: 1 });
    release();
    await h.settle();
    report.old_poll_new_visit = h.chat._test.state().visit.visitId === VID2 && h.chat.isActive();
  }
  // ── 读快照期间被非阶段帧抢先：对端名与重载前台词照样并进来 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true, peer: { cat_name: '小白', short_id: 'QWERTY' },
      transcript: [{ type: 'visit_line', visit_id: VID, line_id: 'old1', text: '重载前的话', final: true, truncated: false,
        speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } }] } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.chat.handleFrame({ type: 'visit_state_change', action: 'peer_hidden', visit_id: VID, side: 'host', ts: 1 });
    release();
    await probe;
    report.stale_merge_peer = h.chat._test.state().visit.peerName;
    report.stale_merge_line = h.lineText('old1');
  }
  // ── 快照带来另一场：清掉上一场的台词拼接与访客头像 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: {
      active: true, visit_id: VID2, phase: 'active', side: 'host', enabled: true,
      transcript: [{ type: 'visit_line', visit_id: VID2, line_id: 'L1', text: '新一场的第一句', final: true, truncated: false,
        speaker: { side: 'guest', kind: 'cat', name: '小黑', self: false } }] } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'L1', text: '旧一场的第一句', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    h.win.dispatchEvent(new CustomEventLike('neko-visit-first-frame', { detail: { dataURL: 'data:image/png;base64,OLD' } }));
    await h.chat.afterBind(h.socket, '咪咪');
    report.snapshot_switch_line = h.chat._test.state().lines.get('L1').text;
    report.snapshot_switch_avatar = h.win.__lastAvatar || '';
  }
  // ── 重连轮询发现已结束：照样留下该场的结束消息（查看详情 / 导出 / 举报） ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.state('reconnecting');
    h.advance(5100);
    await h.settle();
    report.recover_ended_message = h.messages.some((m) => m.id === 'visit-ended-' + VID);
  }
  process.stdout.write(JSON.stringify(report));
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


def _run(script: str) -> dict:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the visit chat harness")
    result = run_node_script(node_path, script, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def report() -> dict:
    return _run(HARNESS.replace("__CHAT_PATH__", json.dumps(str(CHAT_PATH)))
                .replace("__PACER_PATH__", json.dumps(str(PACER_PATH))))


def test_out_of_order_pieces_land_by_index_and_gaps_stay_placeholders(report):
    assert report["gap_first"] == "……C"
    assert report["gap_second"] == "A…C"
    assert report["gap_filled"] == "ABC"


def test_final_overwrites_and_late_pieces_are_ignored(report):
    assert report["final_text"] == "全文"
    assert report["after_final"] == "全文"
    assert report["plain"] is True


def test_abort_truncates_to_i_done_immediately(report):
    text, _, notice = report["abort_text"].partition("|")
    assert text == "A" and notice
    assert report["abort_final"].startswith("A|")


def test_twenty_second_stall_truncates_locally_and_final_still_wins(report):
    assert report["stall_before"] == "一半"
    assert report["stall_after"].startswith("一半|")
    assert report["stall_final"] == "一半和另一半"


def test_cloud_transcript_exports_by_side_and_from(report):
    assert report["cloud_lines"] == 3
    # role=guest：side guest 的是本侧，side host 的是对端；猫娘 / 亲人看 from
    assert report["cloud_labels"] == ["对方的猫娘", "我家猫娘", "对方的家里人"]
    assert report["cloud_clean"] is True
    assert report["local_lines"] == 2
    assert report["local_clean"] is True


def test_details_pages_through_next_cursor(report):
    assert report["details_page1_next"] is True
    assert report["details_rows"] == 1200
    assert report["details_last_next"] is False
    assert report["details_cursors"] == ["", "c1", "c2"]
    assert report["details_urls_ok"] is True


def test_reception_confirmation_keeps_the_text_and_sends_nothing(report):
    assert report["gate_active"] is True
    assert report["gate_accepted"] is False
    assert report["gate_ws"] == 0 and report["gate_send_calls"] == 0
    assert report["gate_rollback"] == ["r1"]


def test_backend_refusal_marks_that_bubble_and_restores_the_text(report):
    assert report["refuse_sources"] == ["neko_visit:guest_cat", "neko_visit:guest_cat"]
    assert report["refuse_m1_status"] == "sending"
    assert report["refuse_m2_status"] == "failed" and report["refuse_m2_notice"] is True
    assert report["refuse_rollbacks"] == ["r2"]
    assert report["local_plain"] is True
    assert report["echo_claimed"] == "第一句（清洗后）"
    assert report["echo_no_dup"] == 0
    assert report["recipient_own"] == "neko_visit:own_cat"


def test_guest_side_and_wrap_up_refuse_but_ending_does_not_take_input(report):
    assert report["guest_accepted"] is False and report["guest_disabled"] is True
    assert report["wrap_accepted"] is False
    assert report["ending_active"] is False


def test_preview_comes_first_and_failures_never_join(report):
    assert report["preview_fail_joins"] == 0 and report["preview_fail_dialogs"] == 0
    assert report["blocked_buttons"] == ["cancel"]
    assert "你已屏蔽此人" in report["blocked_text"]
    # 确认框只有显示名 + 短码 + 跨区提示，没有任何技术数字
    assert "邻居" in report["confirm_text"] and "ABCDEF" in report["confirm_text"]
    assert "token" not in report["confirm_text"].lower() and "tts" not in report["confirm_text"].lower()
    assert report["order_after_confirm"] == ["preview", "join", "join"]


def test_persona_409_is_confirmed_before_the_single_retry(report):
    assert report["persona_calls"] == 1
    assert report["persona_joins_before"] == 1       # 第一次 join 回 409 之后、重试之前
    assert report["join_count"] == 2
    assert report["join_body"] == {"catgirl": "咪咪", "invite_code": "ABCDEFGH23", "confirm": True}


def test_recall_posts_the_route_end_body_and_toasts_recall_already(report):
    assert report["recall_body"] == {
        "lanlan_name": "咪咪", "visit_id": "AAAAAAAAAAAAAAAAAAAAAA", "reason": "recall",
    }
    assert report["recall_toast"] is True


def test_debrief_chips_dedupe_ack_and_choice_body(report):
    assert report["chips_handled"] is True
    assert report["chips_rendered"] == 1
    assert report["chips_acks"] == ["visit-debrief:AAAAAAAAAAAAAAAAAAAAAA"] * 2
    assert report["choice_body"] == {
        "visit_id": "AAAAAAAAAAAAAAAAAAAAAA", "choice": "diary", "lanlan_name": "咪咪",
    }
    assert report["chips_disabled"] is True
    assert report["committing_buttons"] == [["confirm", False], ["forget", True]]
    assert report["committing_status"] == "status"
    assert report["failed_old_disabled"] is True
    assert report["failed_new_enabled"] is True


def test_abandon_with_unconfirmed_writes_asks_first_and_says_so(report):
    assert report["abandon_confirm_dialog"] == 1
    assert report["abandon_posted_before_confirm"] == 0
    assert report["abandon_body"]["seq"] == 3 and report["abandon_body"]["choice"] == "abandon"
    assert report["abandon_body"]["lanlan_name"] == "咪咪"
    assert "未能确认" in report["abandon_status"]


def test_state_probe_racing_a_started_frame_keeps_the_visit_active(report):
    assert report["race_detail_has_active"] is False
    assert report["race_pacer_armed"] is True
    assert report["race_chat_phase"] == "active"


def test_reload_mid_visit_binds_active_and_idle_binds_inactive(report):
    assert report["reload_active"] is True and report["reload_visit"] is True
    assert report["reload_pacer_armed"] is True
    assert report["reload_chat_phase"] == "active"
    assert report["idle_active"] is False
    assert report["idle_bar_entry"] == ["entry"]


def test_a_later_submission_keeps_an_old_refused_text_out_of_the_composer(report):
    assert report["later_submit_rollbacks"] == []
    assert report["later_submit_m1"] == "failed"
    assert report["attachments_ignored"] is True


def test_details_label_speakers_from_the_requester_side(report):
    # 请求者是 guest：host 侧的是对方，guest 侧的是我；differ 行两份同一口径
    assert report["details_labels"] == ["对方的猫娘", "我家猫娘", "我家猫娘"]


def test_export_uses_the_side_of_that_visit_or_neutral_labels(report):
    assert report["export_unknown"] == ["邀请方的猫娘", "来访方的家里人"]
    assert report["export_known"] == ["对方的猫娘", "我"]


def test_snapshot_phase_survives_a_non_phase_frame_during_the_probe(report):
    assert report["nonphase_phase"] == "active"
    assert report["nonphase_active"] is True


def test_invitation_stays_answerable_after_a_failed_accept(report):
    assert report["invite_after_fail_open"] == 1
    assert report["invite_after_fail_enabled"] is True
    assert report["invite_after_ok_open"] == 0
    assert report["invite_posts"] == [True, True]


def test_login_blocked_report_that_was_queued_shows_as_queued(report):
    assert report["report_queued_button"] is True


def test_escape_closes_a_dismissible_dialog(report):
    assert report["escape_before"] == 1
    assert report["escape_after"] == 0
    assert report["escape_joins"] == 0


def test_a_late_snapshot_never_revives_a_visit_that_already_ended(report):
    assert report["ended_race_active"] is False
    assert report["ended_race_phase"] is None


def test_guest_keeps_the_previewed_host_name_and_drops_the_old_avatar(report):
    assert "邻居家" in report["guest_badge"]
    assert report["renderVisitLine_avatar"] == ""


def test_reconnect_poll_applies_an_ended_snapshot(report):
    assert report["recon_before"] is True
    assert report["recon_after_active"] is False
    assert report["recon_after_disabled"] is False


def test_an_old_reconnect_poll_does_not_clear_the_next_visit(report):
    assert report["old_poll_new_visit"] is True


def test_stale_snapshot_still_merges_peer_and_history(report):
    assert report["stale_merge_peer"] == "小白"
    assert report["stale_merge_line"] == "重载前的话"


def test_snapshot_visit_switch_resets_line_and_avatar_caches(report):
    assert report["snapshot_switch_line"] == "新一场的第一句"
    assert report["snapshot_switch_avatar"] == ""


def test_snapshot_recovery_of_an_ended_visit_keeps_its_ended_message(report):
    assert report["recover_ended_message"] is True

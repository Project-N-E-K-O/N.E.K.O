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

# The frontend emptiness-after-cleaning check must match backend sanitize.strip_control_chars + strip().
RELAY_SAMPLES = [
    "\u200b", "\u200b\u202e\ufeff", " \u2066 ", "\u200d", "\u200c\u200d", "a\u200b", "\x07\x1b",
    "\t\n", "\u2028", "\u00ad", "\U0001F468\u200d\U0001F469", "\u061c", "x",
]

HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const CHAT_SRC = fs.readFileSync(__CHAT_PATH__, 'utf8');
const PACER_SRC = fs.readFileSync(__PACER_PATH__, 'utf8');
const VID = 'AAAAAAAAAAAAAAAAAAAAAA';
const VID2 = 'BBBBBBBBBBBBBBBBBBBBBB';
const RELAY_SAMPLES = __RELAY_SAMPLES__;

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
  const relayFrames = [];
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
  // 与真实宿主一样：每条消息带 sortKey（没给就排到最后），按 sortKey 排序
  let sortSeq = 0;
  function sortAll() { messages.sort((a, b) => a.sortKey - b.sortKey); }
  function pushMessage(m) {
    const copy = JSON.parse(JSON.stringify(m));
    if (typeof copy.sortKey !== 'number') copy.sortKey = (sortSeq += 1000);
    messages.push(copy);
    sortAll();
    return copy;
  }
  const host = {
    appendMessage(m) { pushMessage(m); return m; },
    updateMessage(id, patch) { const m = messages.find((x) => x.id === id); if (m) { Object.assign(m, JSON.parse(JSON.stringify(patch))); sortAll(); } },
    removeMessage(id) { const i = messages.findIndex((x) => x.id === id); if (i >= 0) messages.splice(i, 1); return i >= 0; },
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
    nekoRelayVisitStateToDisplay(frame) { relayFrames.push(JSON.parse(JSON.stringify(frame))); },
    visitEnabled: true,
    nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'tok' }) },
    appChat: {
      renderVisitLine(spec) {
        win.__lastAvatar = spec.avatarUrl || '';
        const blocks = [{ type: 'text', text: spec.text, plain_text: true }];
        if (spec.notice) blocks.push({ type: 'status', text: spec.notice });
        const existing = messages.find((x) => x.id === spec.messageId);
        const patch = { blocks: blocks, author: spec.author, status: 'sent', avatarUrl: spec.avatarUrl || '' };
        if (typeof spec.sortKey === 'number') patch.sortKey = spec.sortKey;
        if (existing) { Object.assign(existing, patch); sortAll(); }
        else pushMessage(Object.assign({ id: spec.messageId }, patch));
      },
    },
    appButtons: {
      sendTextPayload(text, options) { sendCalls.push({ text, options }); socket.send(JSON.stringify({ action: 'stream_data', data: text, request_id: options.requestId, source: options.source })); return Promise.resolve(true); },
      markTextSubmissionRefused(id) { (win.__refused = win.__refused || []).push(id); return true; },
    },
    appendReactChatBlocks(response) {
      pushMessage({ id: 'plugin-blocks-' + response.request_id + '-' + (wall++) + '-1', role: 'system', blocks: response.blocks });
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
    localStorage: opts.noStorage ? undefined : (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; })(),
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
  function lineMessage(id) { return messages.find((x) => x.id.indexOf('visit-line-') === 0 && x.id.endsWith(':' + id)); }
  function lineText(id) { const m = lineMessage(id); return m ? m.blocks.map((b) => b.text).join('|') : null; }
  return { win, I, chat, sent, fetches, messages, rollbacks, toasts, sendCalls, relayFrames, socket, body, advance, settle, frame, state, dialogs, buttonsOf, lineText, lineMessage, timers, pressKey };
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
    report.plain = h.lineMessage('L1').blocks[0].plain_text === true;
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
    let geometryRefreshes = 0;
    h.win.addEventListener('neko:compact-interaction-geometry-refresh', () => { geometryRefreshes += 1; });
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
    report.echo_no_dup = h.lineMessage('H1') ? 1 : 0;
    // 收件人切到自家猫娘
    const refreshesBeforeRecipientSwitch = geometryRefreshes;
    h.I.handleVisitBarAction('to_own');
    report.recipient_switch_refreshed_hit_geometry = geometryRefreshes > refreshesBeforeRecipientSwitch;
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
    let previewCode = '';
    let blocked = false;
    let joinReplies = [];
    routes['/api/visit/invites/*'] = async () => previewStatus === 200
      ? { status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: true, expires_at: 9e9, locally_blocked: blocked } }
      : { status: previewStatus, body: { code: previewCode || (previewStatus === 410 ? 'invite_expired' : 'invite_invalid') } };
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
    // 额度预检失败：下一步后直接用全局横幅提示，不弹确认、不发 join。
    previewStatus = 429;
    previewCode = 'VISIT_QUOTA_EXCEEDED';
    const quotaPreviewJoins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
    const quotaPreviewToasts = h.toasts.length;
    await h.chat._test.joinWithCode('ABCDEFGH23');
    report.quota_preview_joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length - quotaPreviewJoins;
    report.quota_preview_dialogs = h.dialogs().length;
    report.quota_preview_toasts = h.toasts.slice(quotaPreviewToasts);
    // 屏蔽：确认框没有「出门」按钮
    previewStatus = 200;
    previewCode = '';
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
    // 额度用完：复用全局状态横幅，不另造串门弹窗
    joinReplies = [{ status: 429, body: { code: 'VISIT_QUOTA_EXCEEDED' } }];
    const toastsBeforeQuota = h.toasts.length;
    const p3 = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    const d3 = h.dialogs()[0];
    h.buttonsOf(d3).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await p3;
    report.quota_failure_dialogs = h.dialogs().length;
    report.quota_failure_toasts = h.toasts.length - toastsBeforeQuota;
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
  // ── 连续串门：新的日记询问替换旧询问，不在聊天窗里纵向堆叠；预览流程不受影响 ──
  {
    const h = createHarness();
    const chips = (visitId, label) => ({ type: 'chat_blocks', request_id: 'visit-debrief:' + visitId, blocks: [
      { type: 'text', text: label },
      { type: 'buttons', buttons: [
        { id: 'diary', label: '记成日记', action: 'visit_debrief_choice', payload: { visit_id: visitId, choice: 'diary' } },
        { id: 'forget', label: '不记', action: 'visit_debrief_choice', payload: { visit_id: visitId, choice: 'forget' } },
      ] },
    ] });
    h.chat.handleChatBlocks(chips(VID, '第一次'));
    h.chat.handleChatBlocks({ type: 'chat_blocks', request_id: 'visit-debrief-preview:' + VID, blocks: [
      { type: 'text', text: '旧场次的日记预览' },
    ] });
    h.chat.handleChatBlocks(chips(VID2, '第二次'));
    const prompts = h.messages.filter((m) => /^plugin-blocks-visit-debrief:[^-]+/.test(m.id));
    report.latest_chip_only = prompts.map((m) => m.blocks[0].text);
    report.preview_survives_new_chip = h.messages.some((m) => m.id.indexOf('plugin-blocks-visit-debrief-preview:' + VID) === 0);
    h.chat.handleChatBlocks(chips(VID2, '第二次'));
    report.latest_chip_deduped = h.messages.filter((m) => m.id.indexOf('plugin-blocks-visit-debrief:' + VID2) === 0).length;

    const legacy = createHarness({ routes: {
      '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }),
    } });
    legacy.win.appendReactChatBlocks(chips(VID, '旧版堆叠一'));
    legacy.win.appendReactChatBlocks(chips(VID2, '旧版堆叠二'));
    await legacy.chat.afterBind(legacy.socket, '咪咪');
    legacy.chat.handleChatBlocks(chips(VID, '旧版堆叠一'));
    legacy.chat.handleChatBlocks(chips(VID2, '旧版堆叠二'));
    report.legacy_stack_collapsed = legacy.messages
      .filter((m) => /^plugin-blocks-visit-debrief:[^-]+/.test(m.id))
      .map((m) => m.blocks[0].text);
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
    report.race_detail_active = (bound[0] || {}).active;
    report.race_pacer_armed = h.win.nekoVisitPacer.isArmed();
    report.race_chat_phase = h.chat._test.state().visit.phase;
    report.race_relay_actions = h.relayFrames.map((frame) => frame.action);
  }
  // ── 页面在串门进行中重载 → active:true；没有串门 → active:false ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true, peer: { cat_name: '小白' }, room: { peer_crop: 'full' }, transcript: [] } }) };
    const h = createHarness({ routes, withPacer: true });
    const bound = [];
    h.win.addEventListener('neko-visit-bound', (ev) => bound.push(ev.detail));
    await h.chat.afterBind(h.socket, '咪咪');
    report.reload_active = bound[0].active;
    report.reload_visit = bound[0].visit_id === VID;
    report.reload_pacer_armed = h.win.nekoVisitPacer.isArmed();
    report.reload_chat_phase = h.chat._test.state().visit.phase;
    report.reload_relay_crop = h.relayFrames.length ? h.relayFrames[0].peer_crop : null;
    const routes2 = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h2 = createHarness({ routes: routes2, withPacer: true });
    const bound2 = [];
    h2.win.addEventListener('neko-visit-bound', (ev) => bound2.push(ev.detail));
    await h2.chat.afterBind(h2.socket, '咪咪');
    report.idle_active = bound2[0].active;
    report.idle_bar_entry = h2.I.getVisitChatView().bar;
  }
  // ── 重载快照中继给 Pet 窗：阶段帧之后带上出门样式与对端显隐（中继可能抢在 Pet 自己的恢复之前建 iframe） ──
  {
    const snapRelay = async (body) => {
      const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body }) };
      const h = createHarness({ routes });
      await h.chat.afterBind(h.socket, '咪咪');
      return h.relayFrames.map((frame) => frame.action + ':' + frame.side + ':' + frame.visit_id);
    };
    report.reload_relay_guest_hidden = await snapRelay({ active: true, visit_id: VID, phase: 'active', side: 'guest', enabled: true,
      peer: { cat_name: '小白', hidden: true }, room: { peer_crop: 'upper' }, transcript: [] });
    report.reload_relay_host_visible = await snapRelay({ active: true, visit_id: VID, phase: 'wrap_up', side: 'host', enabled: true,
      peer: { cat_name: '小白', hidden: false }, room: { peer_crop: 'full' }, transcript: [] });
    report.reload_relay_guest_waiting = await snapRelay({ active: true, visit_id: VID, phase: 'joining', side: 'guest', enabled: true,
      transcript: [] });
  }
  // ── 旧的未开始 ending 快照迟到：不能结束读 state 期间已经打开的新场次 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'ending', side: 'guest', enabled: true, room: null, transcript: [],
    } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'host', ts: 4 });
    release();
    await probe;
    report.stale_unstarted_ending_visit = h.chat._test.state().visit.visitId;
    report.stale_unstarted_ending_phase = h.chat._test.state().visit.phase;
    report.stale_unstarted_ending_relay = h.relayFrames.map((frame) => frame.action + ':' + frame.visit_id);
  }
  // ── 同场 pending 后读到未开始 ending 快照：即使读取期间有帧，也必须完成收尾 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'ending', side: 'guest', enabled: true, room: null, transcript: [],
    } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID, side: 'guest', ts: 4 });
    release();
    await probe;
    report.stale_same_visit_ending_phase = h.chat._test.state().visit.phase;
    report.stale_same_visit_ending_relay = h.relayFrames.map((frame) => frame.action + ':' + frame.visit_id);
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
    const h = createHarness({ routes, withPacer: true });
    const bound = [];
    h.win.addEventListener('neko-visit-bound', (ev) => bound.push(ev.detail));
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    const ended = { type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', ts: 1 };
    h.win.dispatchEvent(new CustomEventLike('neko-visit-frame', { detail: ended }));
    h.chat.handleFrame(ended);
    release();
    await probe;
    report.ended_race_bound_active = 'active' in (bound[0] || {}) ? bound[0].active : null;
    report.ended_race_pacer_active = h.win.nekoVisitPacer.isArmed() && !!(bound[0] || {}).active;
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
    report.guest_waiting_bar = h.I.getVisitChatView().bar;
    h.frame({ type: 'visit_state_change', action: 'departed', visit_id: VID2, side: 'guest', ts: 1 });
    report.guest_badge = h.I.getVisitChatView().bar.badge;
    report.guest_peer_name = h.chat._test.state().visit.peerName;
    h.frame({ type: 'visit_line', visit_id: VID2, line_id: 'z1', text: '你好', final: true, truncated: false,
      speaker: { side: 'host', kind: 'cat', name: '小黑', self: false } });
    const m = h.lineMessage('z1');
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
    report.snapshot_switch_line = [...h.chat._test.state().lines.values()].find((e) => e.lineId === 'L1' && e.visitId === VID2).text;
    report.snapshot_switch_avatar = h.win.__lastAvatar || '';
    report.snapshot_switch_relay = h.relayFrames.slice(-2).map((frame) => frame.action + ':' + frame.visit_id);
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
    report.recover_ended_relay = h.relayFrames[h.relayFrames.length - 1].action;
  }
  // ── abort 的 i_done 比收到的片多：补占位，迟片还能落位 ──
  {
    const h = createHarness();
    h.state('started');
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'A1', i: 0, text: 'A', speaker: sp });
    h.frame({ type: 'visit_line_abort', visit_id: VID, line_id: 'A1', i_done: 3, reason: 'human_interrupt' });
    report.abort_extend = h.lineText('A1').split('|')[0];
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'A1', i: 1, text: 'B', speaker: sp });
    report.abort_extend_late = h.lineText('A1').split('|')[0];
  }
  // ── 入房失败原因在 details.reason ──
  {
    const routes = {
      '/api/visit/invites/*': async () => ({ status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }),
    };
    routes['/api/visit/rooms/' + VID2 + '/join'] = async () => ({ status: 409, body: { ok: false, code: 'VISIT_INVITE_INVALID', details: { reason: 'peer_blocked' } } });
    const h = createHarness({ routes });
    const p = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await p;
    report.join_reason_toast = h.toasts[h.toasts.length - 1];
  }
  // ── 接待回 VISIT_DISABLED：框留着，还能婉拒 ──
  {
    const replies = [{ status: 409, body: { ok: false, code: 'VISIT_DISABLED' } }, { status: 200, body: { ok: true } }];
    const routes = {};
    routes['/api/visit/rooms/' + VID + '/accept'] = async () => replies.shift();
    const h = createHarness({ routes });
    h.state('pending');
    h.frame({ type: 'visit_invite', visit_id: VID, peer_name: '小白', peer_short_id: 'QWERTY', cross_region: false, expires_at: 1700000000 + 60 });
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'accept').click();
    await h.settle();
    report.disabled_open = h.dialogs().length;
    report.disabled_text = h.dialogs()[0] ? h.dialogs()[0].textContent : '';
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'decline').click();
    await h.settle();
    report.disabled_declined = h.fetches.filter((f) => f.url.endsWith('/accept')).map((f) => f.body.accept);
  }
  // ── ended 先到、快照说这场开始过：补上结束消息，但不复活 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'ending', side: 'host', enabled: true, peer: { cat_name: '小白' }, room: { phase: 'ending' } } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.chat.handleFrame({ type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', ts: 1 });
    release();
    await probe;
    report.late_ended_message = h.messages.filter((m) => m.id === 'visit-ended-' + VID).length;
    report.late_ended_active = h.chat.isActive();
  }
  // ── bind 后读 state 失败：隔一会儿重试，读到了就恢复 ──
  {
    let calls = 0;
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { calls += 1; return calls === 1 ? { status: 503, body: {} } : { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes });
    await h.chat.afterBind(h.socket, '咪咪');
    report.probe_retry_before = h.chat.isActive();
    h.advance(2100);
    await h.settle();
    report.probe_retry_after = h.chat.isActive();
    report.probe_retry_calls = calls;
  }
  // ── 只剩会被后端清洗掉的字符：不发、文字放回输入框 ──
  {
    const h = createHarness();
    h.state('started');
    const accepted = h.I.submitVisitChatText({ text: '​‮​', requestId: 'z1' });
    h.advance(1);
    report.invisible_accepted = accepted;
    report.invisible_sent = h.sendCalls.length;
    report.invisible_rollback = h.rollbacks.slice();
    report.invisible_bubbles = h.messages.filter((m) => m.id === 'visit-local-z1').length;
    report.clean_samples = RELAY_SAMPLES.map((x) => h.chat._test.relayCleanIsEmpty(x));
  }
  // ── 举报没保存下来：框留着、内容还在，再点一次成功才关 ──
  {
    const replies = [{ status: 503, body: {} }, { status: 200, body: { ok: true, report_id: 'r' } }];
    const routes = { '/api/visit/report': async () => replies.shift() };
    const h = createHarness({ routes });
    const msg = { id: 'visit-ended-' + VID, blocks: [{ type: 'buttons', buttons: [{ id: 'report', label: '举报', action: 'visit_report', payload: { visit_id: VID } }] }] };
    h.messages.push(msg);
    h.win.dispatchEvent(new CustomEventLike('react-chat-window:action', { detail: { message: msg, action: msg.blocks[0].buttons[0] } }));
    await h.settle();
    const d = h.dialogs()[0];
    const note = d.all((n) => n.tagName === 'TEXTAREA')[0];
    note.value = '详细经过';
    h.buttonsOf(d).find((b) => b.attrs['data-visit-dialog-button'] === 'submit').click();
    await h.settle();
    report.report_fail_open = h.dialogs().length;
    report.report_fail_note = h.dialogs()[0] ? h.dialogs()[0].all((n) => n.tagName === 'TEXTAREA')[0].value : null;
    report.report_fail_enabled = h.dialogs()[0] ? h.buttonsOf(h.dialogs()[0]).every((b) => !b.disabled) : false;
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'submit').click();
    await h.settle();
    report.report_retry_closed = h.dialogs().length;
    report.report_bodies = h.fetches.filter((f) => f.url === '/api/visit/report').map((f) => f.body.note);
  }
  // ── 晚到快照补结束消息时一并记下本侧：导出按「我 / 对方」标 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'ending', side: 'guest', enabled: true, room: { phase: 'ending' } } }; } };
    const h = createHarness({ routes, noStorage: true });   // 本地存储不可用：只能靠 lastEnded 认本侧
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.chat.handleFrame({ type: 'visit_state_change', action: 'ended', visit_id: VID, ts: 1 });
    release();
    await probe;
    const doc = { source: 'memory', visit_id: VID, lines: [{ line_id: 'x', lp: 1, side: 'guest', speaker_kind: 'cat', ts: 1, text: '甲', truncated: false }] };
    report.late_ended_export = h.chat._test.buildTranscriptText(doc).split('\n')[1].replace(/^\[[^\]]*\] /, '').split('：')[0];
  }
  // ── 上一场结束后，重放的邀请把页面带进新的一场：旧的台词缓存清掉 ──
  {
    const h = createHarness();
    h.state('started');
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'L1', text: '旧一场', final: true, truncated: false, speaker: sp });
    h.state('ended');
    h.frame({ type: 'visit_invite', visit_id: VID2, peer_name: '小黑', peer_short_id: 'ZXCVBN', cross_region: false, expires_at: 1700000000 + 60 });
    h.frame({ type: 'visit_line_delta', visit_id: VID2, line_id: 'L1', i: 0, text: '新一场的片', speaker: sp });
    report.invite_new_visit_line = [...h.chat._test.state().lines.values()].find((e) => e.lineId === 'L1' && e.visitId === VID2).text;
  }
  // ── debrief 块被消息上限裁掉：新连接重放时重新上屏 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    const chips = { type: 'chat_blocks', request_id: 'visit-debrief:' + VID, blocks: [
      { type: 'buttons', buttons: [{ id: 'diary', label: '记成日记', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'diary' } }] },
    ] };
    h.chat.handleChatBlocks(chips);
    h.messages.splice(0, h.messages.length);   // 宿主按上限裁掉了
    await h.chat.afterBind(h.socket, '咪咪');  // 新连接
    h.chat.handleChatBlocks(chips);            // bind 重放
    report.pruned_chip_back = h.messages.filter((m) => m.id.indexOf('plugin-blocks-visit-debrief:' + VID) === 0).length;
    h.chat.handleChatBlocks(chips);            // 同一连接再到一次：还在页上，不重复
    report.pruned_chip_no_dup = h.messages.filter((m) => m.id.indexOf('plugin-blocks-visit-debrief:' + VID) === 0).length;
  }
  // ── 下一场的同号台词：新气泡，不改上一场的 ──
  {
    const h = createHarness();
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.state('started');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', text: '第一场', final: true, truncated: false, speaker: sp });
    h.state('ended');
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'host', ts: 1 });
    h.frame({ type: 'visit_state_change', action: 'started', visit_id: VID2, side: 'host', ts: 1 });
    h.frame({ type: 'visit_line', visit_id: VID2, line_id: 'g:1', text: '第二场', final: true, truncated: false, speaker: sp });
    const both = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0 && m.id.endsWith(':g:1')).map((m) => m.blocks[0].text);
    report.scoped_ids = both;
  }
  // ── 快照里的终稿补全只拼了一半的行；快照历史不认领新发出的本地气泡 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true,
      transcript: [
        { type: 'visit_line', visit_id: VID, line_id: 'g:5', text: '完整的一句', final: true, truncated: false, speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } },
        { type: 'visit_line', visit_id: VID, line_id: 'h:2', text: '以前说过的话', final: true, truncated: false, speaker: { side: 'host', kind: 'human', name: '', self: true } },
      ] } }; } };
    const h = createHarness({ routes });
    h.state('started');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'g:5', i: 0, text: '完整', speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.I.submitVisitChatText({ text: '新的一句', requestId: 'n1' });
    release();
    await probe;
    report.snapshot_final_fill = h.lineText('g:5');
    report.snapshot_no_claim = h.messages.find((m) => m.id === 'visit-local-n1').blocks[0].text;
    report.snapshot_history_shown = h.lineText('h:2');
  }
  // ── 快照带来另一场、上一场还在进行：先给上一场收尾 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: true, visit_id: VID2, phase: 'active', side: 'host', enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.I.submitVisitChatText({ text: '等回显', requestId: 'p1' });
    await h.chat.afterBind(h.socket, '咪咪');
    report.switch_old_ended_message = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    report.switch_old_bubble = h.messages.find((m) => m.id === 'visit-local-p1').status;
    report.switch_new_visit = h.chat._test.state().visit.visitId === VID2;
  }
  // ── 读快照期间被非阶段帧抢先、这一场仍在进行：pacer 也拿到 active:true ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes, withPacer: true });
    const bound = [];
    h.win.addEventListener('neko-visit-bound', (ev) => bound.push(ev.detail));
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    const hidden = { type: 'visit_state_change', action: 'peer_hidden', visit_id: VID, side: 'host', ts: 1 };
    h.win.dispatchEvent(new CustomEventLike('neko-visit-frame', { detail: hidden }));
    h.chat.handleFrame(hidden);
    release();
    await probe;
    report.nonphase_bound_active = bound[0] && bound[0].active;
    report.nonphase_pacer_armed = h.win.nekoVisitPacer.isArmed();
  }
  // ── 漏了上一场的 ended、新一场的 pending 先到：上一场先收尾 ──
  {
    const h = createHarness();
    h.state('started');
    h.I.submitVisitChatText({ text: '等回显', requestId: 'q1' });
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'host', ts: 2 });
    report.pending_switch_ended_message = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    report.pending_switch_old_bubble = h.messages.find((m) => m.id === 'visit-local-q1').status;
    report.pending_switch_new_visit = h.chat._test.state().visit.visitId === VID2;
  }
  // ── 预览块推送没到：用 200 应答里的同一份块补上屏，之后的推送不重复 ──
  {
    const previewFrame = { type: 'chat_blocks', request_id: 'visit-debrief-preview:' + VID, blocks: [
      { type: 'text', text: '今天的日记', plain_text: true },
      { type: 'buttons', buttons: [
        { id: 'confirm', label: '写入', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'confirm' } },
      ] },
    ], metadata: { source: 'system', source_name: '咪咪', passthrough: true } };
    const routes = { '/api/visit/debrief/choice': async () => ({ status: 200, body: { ok: true, applied: 'preview', preview: { diary: '今天的日记', facts: [] }, chat_blocks: previewFrame } }) };
    const h = createHarness({ routes });
    h.chat.handleChatBlocks({ type: 'chat_blocks', request_id: 'visit-debrief:' + VID, blocks: [
      { type: 'buttons', buttons: [{ id: 'diary', label: '记成日记', action: 'visit_debrief_choice', payload: { visit_id: VID, choice: 'diary' } }] },
    ] });
    const chipMsg = h.messages.find((m) => m.id.indexOf('plugin-blocks-visit-debrief:') === 0);
    h.win.dispatchEvent(new CustomEventLike('react-chat-window:action', { detail: { message: chipMsg, action: chipMsg.blocks[0].buttons[0] } }));
    await h.settle();
    const previews = () => h.messages.filter((m) => m.id.indexOf('plugin-blocks-visit-debrief-preview:' + VID) === 0);
    report.http_preview_rendered = previews().length;
    report.http_preview_text = previews()[0] ? previews()[0].blocks[0].text : null;
    report.http_preview_chip_disabled = chipMsg.blocks[0].buttons.every((b) => b.disabled === true);
    h.chat.handleChatBlocks(previewFrame);   // 推送后到 / bind 重放
    report.http_preview_no_dup = previews().length;
  }
  // ── 只为串门句起的文本会话：串门结束后撤掉；串门前就有的普通会话不动 ──
  {
    const h = createHarness();
    h.state('started');
    h.win.appState.isTextSessionActive = false;
    h.I.submitVisitChatText({ text: '你好', requestId: 'a1' });
    h.win.appState.isTextSessionActive = true;   // 串门路由回的 ack
    h.state('ended');
    report.ack_session_after_end = h.win.appState.isTextSessionActive;
    const h2 = createHarness();
    h2.win.appState.isTextSessionActive = true;   // 串门前就在普通文本聊天
    h2.state('started');
    h2.I.submitVisitChatText({ text: '你好', requestId: 'a2' });
    h2.state('ended');
    report.real_session_after_end = h2.win.appState.isTextSessionActive;
  }
  // ── 台词按 (lp, side_rank) 落位：同 lp 时 host 在前；快照重放的更早的行排在实时行之前 ──
  {
    const h = createHarness();
    h.state('started', { side: 'guest' });
    const own = { side: 'guest', kind: 'cat', name: '小白', self: true };
    const hostCat = { side: 'host', kind: 'cat', name: '咪咪', self: false };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 5, text: '客人这句', final: true, truncated: false, speaker: own });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:1', lp: 5, text: '东家这句', final: true, truncated: false, speaker: hostCat });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:0', lp: 2, text: '更早一句', final: true, truncated: false, speaker: hostCat });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:2', lp: 9, text: '最新一句', final: true, truncated: false, speaker: own });
    report.lamport_order = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0).map((m) => m.blocks[0].text);
  }
  // ── 气泡头已有作者名：去掉模型 / 旧端误放在正文开头的说话人或收话人名称标记 ──
  {
    const h = createHarness();
    h.state('started', { side: 'host', peer_name: '11' });
    const peer = { side: 'guest', kind: 'cat', name: '小白', self: false };
    const own = { side: 'host', kind: 'cat', name: '咪咪', self: true };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:p1', lp: 1, text: '[小白] 你好呀', final: true, truncated: false, speaker: peer });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:p2', lp: 2, text: '【小白】：又见面了', final: true, truncated: false, speaker: peer });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:p3', lp: 3, text: '[别人] 这不是我的标记', final: true, truncated: false, speaker: peer });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:p4', lp: 4, text: '[11] 哟，又来了？', final: true, truncated: false,
      speaker: own, addressee: { side: 'guest', kind: 'cat' } });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:p5', lp: 5, text: '[11]，你也来了。', final: true, truncated: false,
      speaker: own, addressee: { side: 'guest', kind: 'cat' } });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:p6', lp: 6, text: '我刚才听见【11】叫我。', final: true, truncated: false,
      speaker: own, addressee: { side: 'guest', kind: 'cat' } });
    report.repeated_speaker_prefix = [h.lineText('g:p1'), h.lineText('g:p2'), h.lineText('g:p3'), h.lineText('h:p4'),
      h.lineText('h:p5'), h.lineText('h:p6')];
    report.repeated_speaker_author = h.lineMessage('g:p1').author;
  }
  // ── 实时自家猫娘走普通助手气泡；重载时则由 transcript 补回，不能丢历史 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true,
      transcript: [{ type: 'visit_line', visit_id: VID, line_id: 'h:reload', lp: 4,
        text: '重载后还在', final: true, truncated: false,
        speaker: { side: 'host', kind: 'cat', name: '咪咪', self: true } }],
    } }) };
    const h = createHarness({ routes });
    await h.chat.afterBind(h.socket, '咪咪');
    report.snapshot_own_cat_restored = h.lineText('h:reload');
  }
  // ── 读快照期间提交、快照里已有这句、实时终稿后到：认领并撤掉本地气泡，不留重复 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const me = { side: 'host', kind: 'human', name: '', self: true };
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true,
      transcript: [{ type: 'visit_line', visit_id: VID, line_id: 'h:7', lp: 7, text: '刚发的', final: true, truncated: false, speaker: me }],
    } }; } };
    const h = createHarness({ routes });
    h.state('started');
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.I.submitVisitChatText({ text: '刚发的', requestId: 's1' });
    release();
    await probe;
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:7', lp: 7, text: '刚发的', final: true, truncated: false, speaker: me });
    report.snapshot_then_live_local = h.messages.some((m) => m.id === 'visit-local-s1');
    report.snapshot_then_live_lines = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0).length;
    report.snapshot_then_live_pending = h.chat._test.state().pending.length;
  }
  // ── 等客时取消（后端不推 ending：pending → ended）：不留查看详情 / 导出 / 举报 ──
  {
    const h = createHarness();
    h.state('pending');
    h.state('ended');
    report.cancel_before_start_phase_after_ended = h.chat._test.state().visit.phase;
    report.cancel_before_start_ended_message = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    // 漏收 started：ending 只推给已激活的场次，本身就算开始过
    const h4 = createHarness();
    h4.state('pending');
    h4.state('ending');
    h4.state('ended');
    report.missed_started_ending_ended_message = h4.messages.some((m) => m.id === 'visit-ended-' + VID);
    const h2 = createHarness();
    h2.state('started');
    h2.state('ending');
    h2.state('ended');
    report.started_then_ended_message = h2.messages.some((m) => m.id === 'visit-ended-' + VID);
    const h3 = createHarness();
    h3.chat.applyState({ active: true, visit_id: VID, phase: 'ending', side: 'guest', room: null, transcript: [], enabled: true }, false);
    report.unstarted_ending_snapshot_phase = h3.chat._test.state().visit.phase;
    report.unstarted_ending_snapshot_relay = h3.relayFrames.map((frame) => frame.action);
  }
  // ── ending 起输入框交还普通聊天：串门 ack 的会话标记在 ending 撤掉，之后起的真会话 ended 时不动 ──
  {
    const h = createHarness();
    h.state('started');
    h.win.appState.isTextSessionActive = false;
    h.I.submitVisitChatText({ text: '你好', requestId: 'e1' });
    h.win.appState.isTextSessionActive = true;   // 串门 ack
    h.state('ending');
    report.ending_released_ack = h.win.appState.isTextSessionActive;
    h.win.appState.isTextSessionActive = true;   // ending 之后普通聊天起了真会话
    h.state('ended');
    report.real_session_kept_after_ended = h.win.appState.isTextSessionActive;
  }
  // ── 台词先于第一帧到：第一帧到了，已上屏的对端猫娘气泡补上头像 ──
  {
    const h = createHarness();
    h.state('started');
    const peerCat = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '你好呀', final: true, truncated: false, speaker: peerCat });
    report.avatar_before_first_frame = h.lineMessage('g:1').avatarUrl;
    h.win.dispatchEvent(new CustomEventLike('neko-visit-first-frame', { detail: { dataURL: 'data:image/png;base64,AAAA', visitId: VID } }));
    report.avatar_after_first_frame = h.lineMessage('g:1').avatarUrl;
    report.avatar_text_kept = h.lineText('g:1');
  }
  // ── 按 request_id 认领：A 的实时回显丢了、快照带回 A，再发 B，各自留在自己的气泡里 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const me = { side: 'host', kind: 'human', name: '', self: true };
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true,
      transcript: [{ type: 'visit_line', visit_id: VID, line_id: 'h:1', lp: 3, text: '句子A', final: true, truncated: false, speaker: me, request_id: 'ra' }],
    } }; } };
    const h = createHarness({ routes });
    h.state('started');
    h.I.submitVisitChatText({ text: '句子A', requestId: 'ra' });   // A 的回显随断线丢了
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    release();
    await probe;
    h.I.submitVisitChatText({ text: '句子B', requestId: 'rb' });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:2', lp: 4, text: '句子B', final: true, truncated: false, speaker: me, request_id: 'rb' });
    report.by_id_a = (h.messages.find((m) => m.id === 'visit-local-ra') || { blocks: [{}] }).blocks[0].text;
    report.by_id_b = (h.messages.find((m) => m.id === 'visit-local-rb') || { blocks: [{}] }).blocks[0].text;
    report.by_id_pending = h.chat._test.state().pending.length;
    h.state('ended');
    report.by_id_failed = h.messages.filter((m) => m.status === 'failed').length;
    report.by_id_bubbles = h.messages.filter((m) => m.id.indexOf('visit-local-') === 0 || m.id.indexOf('visit-line-') === 0).length;
  }
  // ── 带 id 的回显不认领别人的气泡（别的窗口发的那句）──
  {
    const h = createHarness();
    h.state('started');
    h.I.submitVisitChatText({ text: '我发的', requestId: 'mine' });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:1', lp: 1, text: '别的窗口发的', final: true, truncated: false,
      speaker: { side: 'host', kind: 'human', name: '', self: true }, request_id: 'other-window' });
    report.foreign_id_kept = h.messages.find((m) => m.id === 'visit-local-mine').blocks[0].text;
    report.foreign_id_pending = h.chat._test.state().pending.length;
  }
  // ── 普通句被串门拒了（不在 pendingLocal 里）：原文放回输入框、乐观气泡标失败 ──
  {
    const h = createHarness();
    h.chat.handleStatus('VISIT_E_UNAUTHORIZED', { request_id: 'ordinary-1' });
    report.ordinary_refused_rollback = h.rollbacks.indexOf('ordinary-1') !== -1;
    report.ordinary_refused_marked = (h.win.__refused || []).indexOf('ordinary-1') !== -1;
    h.state('started');
    h.I.submitVisitChatText({ text: '串门句', requestId: 'v-1' });
    h.chat.handleStatus('VISIT_E_BUSY', { request_id: 'v-1' });
    report.visit_refused_not_ordinary = (h.win.__refused || []).indexOf('v-1') === -1;
  }
  // ── 被拒的行（reason: rejected）：撤掉已开出的半截气泡 ──
  {
    const h = createHarness();
    h.state('started');
    const peerCat = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'g:9', i: 0, lp: 9, text: '半截', speaker: peerCat });
    report.rejected_before = !!h.lineMessage('g:9');
    h.frame({ type: 'visit_line_abort', visit_id: VID, line_id: 'g:9', i_done: 0, reason: 'rejected' });
    report.rejected_after = !!h.lineMessage('g:9');
    report.rejected_entry = [...h.chat._test.state().lines.values()].some((e) => e.lineId === 'g:9');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'g:8', i: 0, lp: 8, text: '被打断的', speaker: peerCat });
    h.frame({ type: 'visit_line_abort', visit_id: VID, line_id: 'g:8', i_done: 1, reason: 'interrupted' });
    report.interrupted_kept = h.lineText('g:8');
  }
  // ── 空的模型结果不是消息：不新建气泡，也撤掉此前 delta 留下的半截气泡 ──
  {
    const h = createHarness();
    h.state('started');
    const peerCat = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:empty', lp: 10, text: '', final: true, truncated: false, speaker: peerCat });
    report.empty_final_never_rendered = !!h.lineMessage('g:empty');
    h.frame({ type: 'visit_line_delta', visit_id: VID, line_id: 'g:emptied', i: 0, lp: 11, text: '半句', speaker: peerCat });
    report.empty_final_before = !!h.lineMessage('g:emptied');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:emptied', lp: 11, text: '', final: true, truncated: false, speaker: peerCat });
    report.empty_final_after = !!h.lineMessage('g:emptied');
  }
  // ── 漏了 started、/state 也读不到：收到过这一场的台词，结束时照样留结束消息 ──
  {
    const h = createHarness();
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '你好', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    h.frame({ type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', ts: 2 });
    report.line_proves_started = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    report.line_kept_after_switch = h.lineText('g:1');
  }
  {
    // 页面已知这一场（等客时）、漏了 started：台词到了同样算开始过
    const h = createHarness();
    h.state('pending');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '你好', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    h.state('ended');
    report.line_proves_started_known_visit = h.messages.some((m) => m.id === 'visit-ended-' + VID);
  }
  // ── 切换角色时连接先被清空：旧连接的快照不再写进新聊天 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: {
      active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true,
      transcript: [{ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '旧角色的', final: true, truncated: false,
        speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } }] } }; } };
    const h = createHarness({ routes });
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.win.appState.socket = null;   // app-character.js 切换角色：先置 null、再清聊天
    release();
    await probe;
    report.null_socket_visit = h.chat._test.state().visit.visitId;
    report.null_socket_lines = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0).length;
  }
  // ── 裸 DOM 对话框开 / 关都通知 compact 窗口重算命中区 ──
  {
    const h = createHarness();
    let refreshes = 0;
    h.win.addEventListener('neko:compact-interaction-geometry-refresh', () => { refreshes += 1; });
    h.frame({ type: 'visit_invite', visit_id: VID, peer_name: '小白', peer_short_id: 'QWERTY', cross_region: false, expires_at: 1700000000 + 60 });
    report.geometry_on_open = refreshes;
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'decline').click();
    await h.settle();
    report.geometry_on_close = refreshes;
    report.geometry_dialog_closed = h.dialogs().length;
  }
  // ── 切换角色：上一位的串门 UI 状态清掉，不往新角色的聊天里补结束消息 ──
  {
    const routes = {
      '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }),
      '/api/visit/state?catgirl=%E5%B0%8F%E6%A9%98': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }),
    };
    const h = createHarness({ routes });
    let personaClosed = 0;
    h.win.closeVisitPersonaPreview = () => { personaClosed += 1; };
    await h.chat.afterBind(h.socket, '咪咪');
    h.I.submitVisitChatText({ text: '还在等回显', requestId: 'sw1' });
    report.switch_before_active = h.chat.isActive();
    const socket2 = { readyState: 1, send() {} };
    h.win.appState.socket = socket2;
    h.win.lanlan_config.lanlan_name = '小橘';
    await h.chat.afterBind(socket2, '小橘');
    report.switch_after_active = h.chat.isActive();
    report.switch_no_ended = !h.messages.some((m) => m.id === 'visit-ended-' + VID);
    report.switch_pending = h.chat._test.state().pending.length;
    report.switch_persona_closed = personaClosed;
  }
  // ── 出门预览期间别的窗口换了角色：不弹确认、不发 join ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = {
      '/api/visit/invites/*': async () => { await gate; return { status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }; },
      ['/api/visit/rooms/' + VID2 + '/join']: async () => ({ status: 202, body: { ok: true } }),
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    h.win.lanlan_config.lanlan_name = '小橘';
    release();
    await pending;
    report.switch_preview_dialogs = h.dialogs().length;
    report.switch_preview_joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
  }
  // ── 出门确认框写的是发起时那位；确认前换了角色就不 join ──
  {
    const routes = {
      '/api/visit/invites/*': async () => ({ status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }),
      ['/api/visit/rooms/' + VID2 + '/join']: async () => ({ status: 202, body: { ok: true } }),
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    report.confirm_names_original = h.dialogs()[0] ? h.dialogs()[0].textContent.indexOf('咪咪') !== -1 : false;
    h.win.lanlan_config.lanlan_name = '小橘';
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await pending;
    report.confirm_switch_joins = h.fetches.filter((f) => f.url.indexOf('/join') !== -1).length;
  }
  // ── 空闲快照先替这一场收了尾，迟到的 ended 帧不再追加第二条结束消息 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '你好', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    await h.chat.afterBind(h.socket, '咪咪');
    h.state('ended');
    report.ended_once = h.messages.filter((m) => m.id === 'visit-ended-' + VID).length;
    report.ended_not_revived = h.chat.isActive();
  }
  // ── 旧一场还挂着、bind 重放来了新一场的 invite_ready：切到新一场（旧一场先收尾）──
  {
    const h = createHarness();
    h.state('started');
    h.frame({ type: 'visit_state_change', action: 'invite_ready', visit_id: VID2, side: 'host', ts: 3, invite_code: 'QWERTYUI23', invite_expires_at: 1700000000 + 600 });
    report.replay_invite_switched = h.chat._test.state().visit.visitId === VID2;
    report.replay_invite_phase = h.chat._test.state().visit.phase;
    report.replay_invite_old_ended = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    report.replay_invite_relay = h.relayFrames.slice(-2).map((frame) => frame.action + ':' + frame.visit_id);
  }
  // ── 旧一场还挂着、bind 重放来了新一场的邀请：换到新一场（旧一场先收尾），之后 started 对得上 ──
  {
    const h = createHarness();
    h.state('started');
    h.frame({ type: 'visit_invite', visit_id: VID2, peer_name: '小黑', peer_short_id: 'ZXCVBN', cross_region: false, expires_at: 1700000000 + 60 });
    report.replay_invite_visit = h.chat._test.state().visit.visitId === VID2;
    report.replay_invite_old_ended2 = h.messages.some((m) => m.id === 'visit-ended-' + VID);
    h.frame({ type: 'visit_state_change', action: 'started', visit_id: VID2, side: 'host', ts: 5 });
    report.replay_invite_started = h.chat._test.state().visit.phase;
    // 已结束那一场的邀请迟到：不复活
    const before = h.dialogs().length;
    h.frame({ type: 'visit_invite', visit_id: VID, peer_name: '小白', peer_short_id: 'QWERTY', cross_region: false, expires_at: 1700000000 + 60 });
    report.ended_invite_ignored = h.chat._test.state().visit.visitId === VID2 && h.dialogs().length === before;
  }
  // ── 上一场的 h:1 已收口留着；漏了新一场的阶段帧、先来了它的 h:1：不撞、各自一条 ──
  {
    const h = createHarness();
    const sp = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.state('started');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '上一场', final: true, truncated: false, speaker: sp });
    h.state('ended');
    h.frame({ type: 'visit_line_delta', visit_id: VID2, line_id: 'g:1', i: 0, lp: 1, text: '新一场的片', speaker: sp });
    const texts = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0 && m.id.endsWith(':g:1')).map((m) => m.blocks[0].text);
    report.keyed_lines = texts;
  }
  // ── join 在途时换了角色：后端收下的那一场立刻硬结束 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = {
      '/api/visit/invites/*': async () => ({ status: 200, body: { visit_id: VID2, host_display_name: '邻居', host_short_code: 'ABCDEF', cross_region: false, expires_at: 9e9, locally_blocked: false } }),
      ['/api/visit/rooms/' + VID2 + '/join']: async () => { await gate; return { status: 202, body: { ok: true, visit_id: VID2, phase: 'pending' } }; },
      '/api/visit/route/end': async () => ({ status: 200, body: { ok: true } }),
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.joinWithCode('ABCDEFGH23');
    await h.settle();
    h.buttonsOf(h.dialogs()[0]).find((b) => b.attrs['data-visit-dialog-button'] === 'go').click();
    await h.settle();
    h.win.lanlan_config.lanlan_name = '小橘';
    release();
    await pending;
    const end = h.fetches.find((f) => f.url === '/api/visit/route/end');
    report.switched_join_end = end && end.body;
  }
  // ── 页面漏了邀请帧：再次点击直接恢复原邀请码，不再 POST 新房间 ──
  {
    const currentUrl = '/api/visit/rooms/current?catgirl=%E5%92%AA%E5%92%AA';
    const routes = {
      [currentUrl]: async () => ({ status: 200, body: { visit_id: VID2, phase: 'invite_ready', invite_code: 'ABCDEFGH23', invite_expires_at: 9e9 } }),
    };
    const h = createHarness({ routes });
    await h.chat._test.startInvite();
    const state = h.chat._test.state().visit;
    report.existing_invite_phase = state.phase;
    report.existing_invite_code = state.inviteCode;
    report.existing_invite_creates = h.fetches.filter((f) => f.url === '/api/visit/rooms' && f.method === 'POST').length;
  }
  // ── 恢复 GET 在途时收到更晚的 started 帧：旧响应不能让页面倒退 ──
  {
    const currentUrl = '/api/visit/rooms/current?catgirl=%E5%92%AA%E5%92%AA';
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = {
      [currentUrl]: async () => {
        await gate;
        return { status: 200, body: { visit_id: VID, phase: 'invite_ready', invite_code: 'ABCDEFGH23', invite_expires_at: 9e9 } };
      },
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.startInvite();
    await h.settle();
    h.frame({ type: 'visit_state_change', action: 'started', visit_id: VID, side: 'host', ts: 1 });
    release();
    await pending;
    const state = h.chat._test.state().visit;
    report.stale_host_recovery_phase = state.phase;
    report.stale_host_recovery_code = state.inviteCode;
    report.stale_host_recovery_creates = h.fetches.filter((f) => f.url === '/api/visit/rooms' && f.method === 'POST').length;
  }
  // ── 新房间的 invite_ready 帧丢失：本机恢复接口轮询出原邀请码，不重复建房 ──
  {
    const currentUrl = '/api/visit/rooms/current?catgirl=%E5%92%AA%E5%92%AA';
    let probes = 0;
    const routes = {
      [currentUrl]: async () => {
        probes += 1;
        if (probes === 1) return { status: 404, body: { error: 'no_pending_invite' } };
        return { status: 200, body: { visit_id: VID2, phase: 'invite_ready', invite_code: 'QWERTYUI23', invite_expires_at: 9e9 } };
      },
      '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID2, phase: 'pending' } }),
    };
    const h = createHarness({ routes });
    await h.chat._test.startInvite();
    report.created_invite_initial_phase = h.chat._test.state().visit.phase;
    h.advance(300);
    await h.settle();
    const state = h.chat._test.state().visit;
    report.created_invite_recovered_phase = state.phase;
    report.created_invite_recovered_code = state.inviteCode;
    report.created_invite_creates = h.fetches.filter((f) => f.url === '/api/visit/rooms' && f.method === 'POST').length;
  }
  // ── POST 在途先到 pending 帧：仍要安排邀请码恢复，不能因响应已旧而永久等候 ──
  {
    const currentUrl = '/api/visit/rooms/current?catgirl=%E5%92%AA%E5%92%AA';
    let probes = 0;
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = {
      [currentUrl]: async () => {
        probes += 1;
        if (probes === 1) return { status: 404, body: { error: 'no_pending_invite' } };
        return { status: 200, body: { visit_id: VID2, phase: 'invite_ready', invite_code: 'ZXCVBNMA23', invite_expires_at: 9e9 } };
      },
      '/api/visit/rooms': async () => { await gate; return { status: 202, body: { visit_id: VID2, phase: 'pending' } }; },
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.startInvite();
    await h.settle();
    h.frame({ type: 'visit_state_change', action: 'pending', visit_id: VID2, side: 'host', ts: 1 });
    release();
    await pending;
    h.advance(300);
    await h.settle();
    const state = h.chat._test.state().visit;
    report.pending_before_create_recovered_phase = state.phase;
    report.pending_before_create_recovered_code = state.inviteCode;
    report.pending_before_create_count = h.fetches.filter((f) => f.url === '/api/visit/rooms' && f.method === 'POST').length;
  }
  // ── 恢复接口 404：用权威 state 收掉已结束场次，不能永远停在等待邀请码 ──
  {
    const currentUrl = '/api/visit/rooms/current?catgirl=%E5%92%AA%E5%92%AA';
    const stateUrl = '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA';
    const routes = {
      [currentUrl]: async () => ({ status: 404, body: { error: 'no_pending_invite' } }),
      [stateUrl]: async () => ({ status: 200, body: { enabled: true, active: false } }),
      '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID2, phase: 'pending' } }),
    };
    const h = createHarness({ routes });
    await h.chat._test.startInvite();
    h.advance(300);
    await h.settle();
    report.missing_current_reconciled_visit = h.chat._test.state().visit.visitId;
    report.missing_current_state_probes = h.fetches.filter((f) => f.url === stateUrl).length;
  }
  // ── 建房在途时换了角色：后端收下的那一场同样立刻硬结束 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = {
      '/api/visit/rooms': async () => { await gate; return { status: 202, body: { visit_id: VID2, phase: 'pending' } }; },
      '/api/visit/route/end': async () => ({ status: 200, body: { ok: true } }),
    };
    const h = createHarness({ routes });
    const pending = h.chat._test.startInvite();
    await h.settle();
    h.win.lanlan_config.lanlan_name = '小橘';
    release();
    await pending;
    const end = h.fetches.find((f) => f.url === '/api/visit/route/end');
    report.switched_create_end = end && end.body;
  }
  // ── 读 state 期间到了新一场的邀请：旧快照作废，不把新一场又收掉 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes });
    h.state('started');
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.frame({ type: 'visit_invite', visit_id: VID2, peer_name: '小黑', peer_short_id: 'ZXCVBN', cross_region: false, expires_at: 1700000000 + 60 });
    release();
    await probe;
    report.invite_probe_visit = h.chat._test.state().visit.visitId === VID2;
    report.invite_probe_phase = h.chat._test.state().visit.phase;
  }
  // ── 快照先于 ending 帧报了 ending：同样撤掉只由串门 ack 置上的会话标记 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: true, visit_id: VID, phase: 'ending', side: 'host', enabled: true, room: {} } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.win.appState.isTextSessionActive = false;
    h.I.submitVisitChatText({ text: '你好', requestId: 'se1' });
    h.win.appState.isTextSessionActive = true;   // 串门 ack
    await h.chat.afterBind(h.socket, '咪咪');
    report.snapshot_ending_released = h.win.appState.isTextSessionActive;
  }
  // ── 还没恢复阶段（visit.side 为空）时到了发给自家猫娘的本侧亲人句：收件人按这一行自己的 side 判 ──
  {
    const h = createHarness();
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:1', lp: 1, text: '给自家的', final: true, truncated: false,
      speaker: { side: 'host', kind: 'human', name: '', self: true }, addressee: { side: 'host', kind: 'cat' } });
    report.own_recipient_author = h.lineMessage('h:1').author;
  }
  // ── 读 state 期间到了新一场的台词（漏了它的阶段帧）：换进新一场，旧快照作废 ──
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => { await gate; return { status: 200, body: { active: true, visit_id: VID, phase: 'active', side: 'host', enabled: true } }; } };
    const h = createHarness({ routes });
    h.state('started');
    const probe = h.chat.afterBind(h.socket, '咪咪');
    await h.settle();
    h.frame({ type: 'visit_line', visit_id: VID2, line_id: 'g:1', lp: 1, text: '新一场', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小黑', self: false } });
    release();
    await probe;
    report.line_switch_visit = h.chat._test.state().visit.visitId === VID2;
    report.line_switch_phase = h.chat._test.state().visit.phase;
    report.line_switch_side = h.chat._test.state().visit.side;
    report.line_switch_old_ended = h.messages.some((m) => m.id === 'visit-ended-' + VID);
  }
  // ── 串门句带上发起时的场次 ──
  {
    const h = createHarness();
    h.state('started');
    h.I.submitVisitChatText({ text: '你好', requestId: 'vid1' });
    report.submit_visit_id = h.sendCalls[h.sendCalls.length - 1].options.visitId;
  }
  // ── 接待确认后漏了 started、同一场的台词到了：推进到 active ──
  {
    const h = createHarness();
    h.frame({ type: 'visit_invite', visit_id: VID, peer_name: '小白', peer_short_id: 'QWERTY', cross_region: false, expires_at: 1700000000 + 60 });
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '打扰啦', final: true, truncated: false,
      speaker: { side: 'guest', kind: 'cat', name: '小白', self: false } });
    report.same_visit_line_phase = h.chat._test.state().visit.phase;
    report.same_visit_line_active = h.chat.isActive();
  }
  // ── 空闲快照抢在回显前面收了尾：迟到的回显认领那个已标「未送达」的气泡，原地改回已发出 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    h.I.submitVisitChatText({ text: '最后一句', requestId: 'le1' });
    await h.chat.afterBind(h.socket, '咪咪');
    report.late_echo_before = h.messages.find((m) => m.id === 'visit-local-le1').status;
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'h:9', lp: 9, text: '最后一句', final: true, truncated: false,
      speaker: { side: 'host', kind: 'human', name: '', self: true }, request_id: 'le1' });
    const bubble = h.messages.find((m) => m.id === 'visit-local-le1');
    report.late_echo_after = bubble.status;
    report.late_echo_text = bubble.blocks[0].text;
    report.late_echo_copies = h.messages.filter((m) => (m.blocks || []).some((b) => b.text === '最后一句')).length;
  }
  // ── 空闲快照抢在对方最后一句前面收了尾：那句排到结束消息之前 ──
  {
    const routes = { '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA': async () => ({ status: 200, body: { active: false, visit_id: null, phase: null, enabled: true } }) };
    const h = createHarness({ routes });
    h.state('started');
    const peerCat = { side: 'guest', kind: 'cat', name: '小白', self: false };
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:1', lp: 1, text: '第一句', final: true, truncated: false, speaker: peerCat });
    await h.chat.afterBind(h.socket, '咪咪');
    h.frame({ type: 'visit_line', visit_id: VID, line_id: 'g:2', lp: 2, text: '最后一句', final: true, truncated: false, speaker: peerCat });
    report.late_peer_order = h.messages.filter((m) => m.id.indexOf('visit-line-') === 0 || m.id === 'visit-ended-' + VID)
      .map((m) => (m.id.indexOf('visit-ended-') === 0 ? 'ENDED' : m.blocks[0].text));
  }
  // ── 邀请通知仍未读：轮询必须重试首次失败的普通助手问句投递 ──
  {
    let deliveries = 0;
    const routes = {
      ['/api/visit/direct/' + VID + '/preview']: async () => ({ status: 200, body: { visit_id: VID, expires_at: 9e9 } }),
      ['/api/visit/direct/' + VID + '/question?catgirl=%E5%92%AA%E5%92%AA']: async () => ({ status: 200, body: { question: '要去串门吗？' } }),
      ['/api/visit/direct/' + VID + '/deliver-question']: async () => {
        deliveries += 1;
        return deliveries === 1
          ? { status: 503, body: { error: 'temporary_failure' } }
          : { status: 200, body: { delivered: true } };
      },
      '/api/community/visit-hub': async () => ({ status: 200, body: {
        incoming_invite: { id: 'notif-retry', visit_id: VID, actor_id: 'friend-1', expires_at: 9e9 },
        incoming_invite_ids: ['notif-retry'], notifications_ok: true,
      } }),
    };
    const h = createHarness({ routes });
    h.chat.applyState({ enabled: true, active: false });
    await h.chat._test.presentDirectInvitation({
      id: 'notif-retry', visit_id: VID, actor_id: 'friend-1', expires_at: 9e9,
    });
    await h.chat._test.pollDirectInvitations();
    report.direct_invite_delivery_retries = deliveries;
  }
  // ── 已在另一场串门中：新邀请不打断语音、不发出无法回答的问句，通知保持未读 ──
  {
    const h = createHarness();
    h.chat.applyState({ enabled: true, active: false });
    h.state('started');
    const shown = await h.chat._test.presentDirectInvitation({
      id: 'notif-during-visit', visit_id: VID2, actor_id: 'friend-2', expires_at: 9e9,
    });
    report.direct_invite_deferred_during_visit = shown;
    report.direct_invite_deferred_fetches = h.fetches.length;
  }
  // ── 同一邀请还在时切换猫娘：轮询必须为新角色重新生成并投递问句 ──
  {
    const questionUrls = [];
    const routes = {
      ['/api/visit/direct/' + VID + '/preview']: async () => ({ status: 200, body: { visit_id: VID, expires_at: 9e9 } }),
      '/api/visit/direct/AAAAAAAAAAAAAAAAAAAAAA/question?catgirl=*': async (url) => {
        questionUrls.push(url);
        return { status: 200, body: { question: '要去串门吗？' } };
      },
      ['/api/visit/direct/' + VID + '/deliver-question']: async () => ({ status: 200, body: { delivered: true } }),
      '/api/community/visit-hub': async () => ({ status: 200, body: {
        incoming_invite: { id: 'notif-character', visit_id: VID, actor_id: 'friend-1', expires_at: 9e9 },
        incoming_invite_ids: ['notif-character'], notifications_ok: true,
      } }),
    };
    const h = createHarness({ routes });
    h.chat.applyState({ enabled: true, active: false });
    await h.chat._test.presentDirectInvitation({
      id: 'notif-character', visit_id: VID, actor_id: 'friend-1', expires_at: 9e9,
    });
    h.win.lanlan_config.lanlan_name = '小橘';
    await h.chat._test.pollDirectInvitations();
    report.direct_invite_character_questions = questionUrls;
  }
  // Startup failures must reach the caller without depending on global toasts.
  report.invite_quota_orders = [];
  for (const windowKind of ['web', 'pet', 'chat']) {
    for (const order of ['status', 'ended', 'status_then_ended', 'ended_then_status']) {
      const target = '11111111-1111-4111-8111-111111111111';
      const h = createHarness({ routes: {
        '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID, phase: 'pending', target_user_id: target } }),
      } });
      if (windowKind !== 'web') {
        h.win.__NEKO_MULTI_WINDOW__ = true;
        h.win.location.pathname = windowKind === 'chat' ? '/chat' : '/';
      }
      delete h.win.showStatusToast;
      let outcome = 'pending';
      h.chat.createDirectInvitation(target).then(() => { outcome = 'unexpected_success'; }, (error) => { outcome = error.message; });
      await h.settle();
      const status = () => h.chat.handleStatus('VISIT_QUOTA_EXCEEDED', { visit_id: VID });
      const ended = () => h.state('ended', { reason: 'quota_exceeded' });
      if (order === 'status') status();
      else if (order === 'ended') ended();
      else if (order === 'status_then_ended') { status(); ended(); }
      else { ended(); status(); }
      h.advance(120);
      await h.settle();
      const early = outcome;
      h.advance(15120);
      await h.settle();
      report.invite_quota_orders.push({ windowKind, order, early, final: outcome,
        remainingWaiters: h.timers.filter((entry) => entry.every === 120).length,
        extraCleanup: h.fetches.filter((entry) => entry.url === '/api/visit/route/end').length,
      });
    }
  }
  // Both invitation callers retain specific peer failures even when the
  // corresponding status toast is missing and only ended reaches the page.
  report.invite_peer_failures = [];
  for (const kind of ['manual', 'direct']) {
    for (const reason of ['peer_lost', 'proto_mismatch', 'peer_identity_rejected', 'peer_blocked']) {
      const target = '11111111-1111-4111-8111-111111111111';
      const statusTexts = JSON.parse(fs.readFileSync('static/locales/zh-CN.json', 'utf8')).visit.status;
      const h = createHarness({ routes: {
        '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID, phase: 'pending', target_user_id: target } }),
      } });
      h.win.t = (key) => statusTexts[key.replace(/^visit\.status\./, '')] || key;
      delete h.win.showStatusToast;
      let outcome = 'pending';
      const pending = kind === 'manual' ? h.chat.createInviteCode() : h.chat.createDirectInvitation(target);
      pending.then(() => { outcome = 'unexpected_success'; }, (error) => { outcome = error.message; });
      await h.settle();
      h.state('ended', { reason });
      h.advance(120);
      await h.settle();
      report.invite_peer_failures.push({ kind, reason, error: outcome,
        remainingWaiters: h.timers.filter((entry) => entry.every === 120).length,
        extraCleanup: h.fetches.filter((entry) => entry.url === '/api/visit/route/end').length,
      });
    }
  }
  // WS can finish the visit before the HTTP creation response reaches this
  // window. Preserve its error instead of reporting another invite conflict.
  {
    const target = '11111111-1111-4111-8111-111111111111';
    let h;
    h = createHarness({ routes: {
      '/api/visit/rooms': async () => {
        h.state('pending', { target_user_id: target });
        h.state('ended', { reason: 'quota_exceeded' });
        return { status: 202, body: { visit_id: VID, phase: 'pending', target_user_id: target } };
      },
    } });
    report.invite_quota_before_http = await h.chat.createDirectInvitation(target).catch((error) => error.message);
  }
  // A previous visit's delayed error must not reject a new successful invite.
  {
    const target = '11111111-1111-4111-8111-111111111111';
    const h = createHarness({ routes: {
      '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID2, phase: 'pending', target_user_id: target } }),
    } });
    const pending = h.chat.createDirectInvitation(target);
    await h.settle();
    h.chat.handleStatus('VISIT_QUOTA_EXCEEDED', { visit_id: VID });
    h.chat.handleStatus('VISIT_E_BUSY', { visit_id: VID2, request_id: 'ordinary-input' });
    h.state('invite_ready', { visit_id: VID2, target_user_id: target });
    h.advance(120);
    report.invite_success_after_old_quota = (await pending).visit_id;
  }
  // Manual-code invitations use the same terminal failure path.
  {
    const h = createHarness({ routes: {
      '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID, phase: 'pending' } }),
    } });
    const pending = h.chat.createInviteCode().catch((error) => error.message);
    await h.settle();
    h.state('ended', { reason: 'quota_exceeded' });
    h.advance(120);
    report.manual_invite_quota = await pending;
  }
  // A real readiness timeout still cancels its own pending room.
  {
    const target = '11111111-1111-4111-8111-111111111111';
    const h = createHarness({ routes: {
      '/api/visit/rooms': async () => ({ status: 202, body: { visit_id: VID, phase: 'pending', target_user_id: target } }),
      '/api/visit/route/end': async () => ({ status: 200, body: { ok: true } }),
    } });
    const pending = h.chat.createDirectInvitation(target).catch((error) => error.message);
    await h.settle();
    h.advance(15120);
    report.invite_real_timeout = await pending;
    report.invite_real_timeout_cleanup = h.fetches.filter((entry) => entry.url === '/api/visit/route/end').length;
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
                .replace("__PACER_PATH__", json.dumps(str(PACER_PATH)))
                .replace("__RELAY_SAMPLES__", json.dumps(RELAY_SAMPLES)))


def test_quota_reaches_invite_caller_in_web_and_electron_for_both_event_orders(report):
    assert len(report["invite_quota_orders"]) == 12
    for case in report["invite_quota_orders"]:
        assert case["early"] == case["final"] == "今天的串门时间用完了", case
        assert case["remainingWaiters"] == 0, case
        assert case["extraCleanup"] == 0, case


def test_quota_failure_before_http_response_is_not_lost_or_reported_as_an_invite_conflict(report):
    assert report["invite_quota_before_http"] == "今天的串门时间用完了"


@pytest.mark.parametrize("kind", ["manual", "direct"])
@pytest.mark.parametrize("reason,message", [
    ("peer_lost", "和对方断开了连接"),
    ("proto_mismatch", "对方版本不兼容，请双方更新"),
    ("peer_identity_rejected", "没能确认对方的身份，串门已取消"),
    ("peer_blocked", "没能确认对方的身份，串门已取消"),
])
def test_ended_peer_failure_reaches_invitation_caller_without_status(report, kind, reason, message):
    result = next(item for item in report["invite_peer_failures"]
                  if item["kind"] == kind and item["reason"] == reason)
    assert result["error"] == message
    assert result["remainingWaiters"] == 0
    assert result["extraCleanup"] == 0


def test_old_quota_error_and_input_refusals_do_not_reject_a_new_invitation(report):
    assert report["invite_success_after_old_quota"] == "B" * 22


def test_manual_invite_reports_quota_and_genuine_timeout_still_cancels_the_room(report):
    assert report["manual_invite_quota"] == "今天的串门时间用完了"
    assert report["invite_real_timeout"] == "定向串门房间没有准备好，请稍后再试"
    assert report["invite_real_timeout_cleanup"] == 1


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
    # role=guest: side guest is local, side host is remote; use from to distinguish catgirl/family.
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
    assert report["recipient_switch_refreshed_hit_geometry"] is True


def test_guest_side_and_wrap_up_refuse_but_ending_does_not_take_input(report):
    assert report["guest_accepted"] is False and report["guest_disabled"] is True
    assert report["wrap_accepted"] is False
    assert report["ending_active"] is False


def test_preview_comes_first_and_failures_never_join(report):
    assert report["preview_fail_joins"] == 0 and report["preview_fail_dialogs"] == 0
    assert report["quota_preview_joins"] == 0 and report["quota_preview_dialogs"] == 0
    assert report["quota_preview_toasts"] == ["今天的串门时间用完了"]
    assert report["blocked_buttons"] == ["cancel"]
    assert "你已屏蔽此人" in report["blocked_text"]
    # The confirmation contains only a display name, short code, and cross-region notice, without technical numbers.
    assert "邻居" in report["confirm_text"] and "ABCDEF" in report["confirm_text"]
    assert "token" not in report["confirm_text"].lower() and "tts" not in report["confirm_text"].lower()
    assert report["order_after_confirm"] == ["preview", "join", "join"]


def test_persona_409_is_confirmed_before_the_single_retry(report):
    assert report["persona_calls"] == 1
    assert report["persona_joins_before"] == 1       # After the first join returns 409, before retry.
    assert report["join_count"] == 2
    assert report["join_body"] == {"catgirl": "咪咪", "invite_code": "ABCDEFGH23", "confirm": True,
                                   "crop": "full"}


def test_guest_quota_exhaustion_reuses_the_global_status_banner(report):
    assert report["quota_failure_dialogs"] == 0
    assert report["quota_failure_toasts"] == 1


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


def test_a_new_debrief_question_replaces_the_older_question_only(report):
    assert report["latest_chip_only"] == ["第二次"]
    assert report["preview_survives_new_chip"] is True
    assert report["latest_chip_deduped"] == 1
    assert report["legacy_stack_collapsed"] == ["旧版堆叠二"]


def test_abandon_with_unconfirmed_writes_asks_first_and_says_so(report):
    assert report["abandon_confirm_dialog"] == 1
    assert report["abandon_posted_before_confirm"] == 0
    assert report["abandon_body"]["seq"] == 3 and report["abandon_body"]["choice"] == "abandon"
    assert report["abandon_body"]["lanlan_name"] == "咪咪"
    assert "未能确认" in report["abandon_status"]


def test_state_probe_racing_a_started_frame_keeps_the_visit_active(report):
    # A late old awaiting_accept snapshot must not overwrite frame-driven state with active:false; true based on current post-frame state is allowed.
    assert report["race_detail_active"] is True
    assert report["race_pacer_armed"] is True
    assert report["race_chat_phase"] == "active"
    assert report["race_relay_actions"] == ["started"]


def test_reload_mid_visit_binds_active_and_idle_binds_inactive(report):
    assert report["reload_active"] is True and report["reload_visit"] is True
    assert report["reload_pacer_armed"] is True
    assert report["reload_chat_phase"] == "active"
    assert report["reload_relay_crop"] == "full"
    assert report["idle_active"] is False
    assert report["idle_bar_entry"] is None


def test_reload_snapshot_relay_carries_away_and_peer_visibility_after_the_phase(report):
    # The relayed phase frame can create the Pet iframe before Pet's own reload recovery,
    # which then bails out; the relay itself must restore what that recovery would have.
    assert report["reload_relay_guest_hidden"] == [
        "started:guest:AAAAAAAAAAAAAAAAAAAAAA",
        "departed:guest:AAAAAAAAAAAAAAAAAAAAAA",
        "peer_hidden:guest:AAAAAAAAAAAAAAAAAAAAAA",
    ]
    assert report["reload_relay_host_visible"] == [
        "wrap_up:host:AAAAAAAAAAAAAAAAAAAAAA",
        "peer_visible:host:AAAAAAAAAAAAAAAAAAAAAA",
    ]
    assert report["reload_relay_guest_waiting"] == ["joining:guest:AAAAAAAAAAAAAAAAAAAAAA"]


def test_stale_unstarted_ending_snapshot_does_not_close_the_newer_visit(report):
    assert report["stale_unstarted_ending_visit"] == "BBBBBBBBBBBBBBBBBBBBBB"
    assert report["stale_unstarted_ending_phase"] == "pending"
    assert report["stale_unstarted_ending_relay"] == ["pending:BBBBBBBBBBBBBBBBBBBBBB"]


def test_stale_unstarted_ending_snapshot_closes_the_same_visit(report):
    assert report["stale_same_visit_ending_phase"] is None
    assert report["stale_same_visit_ending_relay"] == [
        "pending:AAAAAAAAAAAAAAAAAAAAAA",
        "ended:AAAAAAAAAAAAAAAAAAAAAA",
    ]


def test_a_later_submission_keeps_an_old_refused_text_out_of_the_composer(report):
    assert report["later_submit_rollbacks"] == []
    assert report["later_submit_m1"] == "failed"
    assert report["attachments_ignored"] is True


def test_details_label_speakers_from_the_requester_side(report):
    # The requester is guest: host is the other side and guest is me; both differ rows use the same perspective.
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
    # The frame chain has ended: a late active snapshot must never rearm this visit.
    assert report["ended_race_bound_active"] is not True
    assert report["ended_race_pacer_active"] is False
    assert report["ended_race_phase"] is None


def test_guest_keeps_the_previewed_host_name_and_drops_the_old_avatar(report):
    assert report["guest_waiting_bar"] is None
    assert report["guest_peer_name"] == "邻居家"
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
    assert report["snapshot_switch_relay"] == [
        "ended:AAAAAAAAAAAAAAAAAAAAAA", "started:BBBBBBBBBBBBBBBBBBBBBB",
    ]


def test_snapshot_recovery_of_an_ended_visit_keeps_its_ended_message(report):
    assert report["recover_ended_message"] is True
    assert report["recover_ended_relay"] == "ended"


def test_abort_extends_to_i_done_with_placeholders(report):
    assert report["abort_extend"] == "A……"
    assert report["abort_extend_late"] == "AB…"


def test_join_reads_the_nested_refusal_reason(report):
    assert report["join_reason_toast"] == "你已屏蔽此人"


def test_disabled_accept_keeps_the_invitation_open_for_a_decline(report):
    assert report["disabled_open"] == 1
    assert "允许串门" in report["disabled_text"]
    assert report["disabled_declined"] == [True, False]


def test_late_snapshot_restores_the_ended_message_without_reviving(report):
    assert report["late_ended_message"] == 1
    assert report["late_ended_active"] is False


def test_failed_state_probe_retries(report):
    assert report["probe_retry_before"] is False
    assert report["probe_retry_after"] is True
    assert report["probe_retry_calls"] == 2


def test_invisible_only_input_is_kept_and_not_sent(report):
    assert report["invisible_accepted"] is False
    assert report["invisible_sent"] == 0
    assert report["invisible_rollback"] == ["z1"]
    assert report["invisible_bubbles"] == 0


def test_frontend_empty_check_matches_the_backend_sanitizer(report):
    from main_logic.visit.sanitize import strip_control_chars

    expected = [not strip_control_chars(sample).strip() for sample in RELAY_SAMPLES]
    assert report["clean_samples"] == expected


def test_failed_report_keeps_the_dialog_and_draft_for_retry(report):
    assert report["report_fail_open"] == 1
    assert report["report_fail_note"] == "详细经过"
    assert report["report_fail_enabled"] is True
    assert report["report_retry_closed"] == 0
    assert report["report_bodies"] == ["详细经过", "详细经过"]


def test_late_ended_message_also_records_the_side(report):
    assert report["late_ended_export"] == "我家猫娘"


def test_replayed_invite_starts_a_clean_visit(report):
    assert report["invite_new_visit_line"] == "新一场的片"


def test_pruned_debrief_block_comes_back_on_replay(report):
    assert report["pruned_chip_back"] == 1
    assert report["pruned_chip_no_dup"] == 1


def test_line_bubbles_are_scoped_to_their_visit(report):
    assert report["scoped_ids"] == ["第一场", "第二场"]


def test_snapshot_finals_fill_partial_lines_without_claiming_new_bubbles(report):
    assert report["snapshot_final_fill"] == "完整的一句"
    assert report["snapshot_no_claim"] == "新的一句"
    assert report["snapshot_history_shown"] == "以前说过的话"


def test_snapshot_switch_finalizes_the_previous_visit(report):
    assert report["switch_old_ended_message"] is True
    assert report["switch_old_bubble"] == "failed"
    assert report["switch_new_visit"] is True


def test_non_phase_frame_during_probe_still_activates_the_pacer(report):
    assert report["nonphase_bound_active"] is True
    assert report["nonphase_pacer_armed"] is True


def test_pending_frame_for_a_new_visit_finalizes_the_previous_one(report):
    assert report["pending_switch_ended_message"] is True
    assert report["pending_switch_old_bubble"] == "failed"
    assert report["pending_switch_new_visit"] is True


def test_preview_from_the_http_answer_shows_when_the_push_is_missing(report):
    assert report["http_preview_rendered"] == 1
    assert report["http_preview_text"] == "今天的日记"
    assert report["http_preview_chip_disabled"] is True
    assert report["http_preview_no_dup"] == 1


def test_ack_only_text_session_is_released_when_the_visit_ends(report):
    assert report["ack_session_after_end"] is False
    assert report["real_session_after_end"] is True


def test_lines_are_ordered_by_lamport_key_with_host_first(report):
    # 自家猫娘经标准 gemini_response 上屏；专用 visit_line 只画对端身份。
    assert report["lamport_order"] == ["更早一句", "东家这句"]


def test_repeated_speaker_prefix_is_hidden_but_the_author_remains(report):
    assert report["repeated_speaker_prefix"] == [
        "你好呀",
        "又见面了",
        "[别人] 这不是我的标记",
        None,
        None,
        None,
    ]
    assert report["repeated_speaker_author"] == "小白"


def test_reload_restores_own_cat_lines_from_the_visit_snapshot(report):
    assert report["snapshot_own_cat_restored"] == "重载后还在"


def test_live_final_after_snapshot_claims_and_removes_the_local_bubble(report):
    assert report["snapshot_then_live_local"] is False
    assert report["snapshot_then_live_lines"] == 1
    assert report["snapshot_then_live_pending"] == 0


def test_direct_invitation_is_deferred_while_another_visit_is_active(report):
    assert report["direct_invite_deferred_during_visit"] is False
    assert report["direct_invite_deferred_fetches"] == 0


def test_cancel_before_start_leaves_no_ended_message(report):
    assert report["cancel_before_start_phase_after_ended"] is None
    assert report["missed_started_ending_ended_message"] is True
    assert report["unstarted_ending_snapshot_phase"] is None
    assert report["unstarted_ending_snapshot_relay"] == ["ended"]
    assert report["cancel_before_start_ended_message"] is False
    assert report["started_then_ended_message"] is True


def test_ack_session_is_released_at_ending_and_a_later_real_session_is_kept(report):
    assert report["ending_released_ack"] is False
    assert report["real_session_kept_after_ended"] is True


def test_first_frame_refreshes_avatars_of_lines_already_shown(report):
    assert report["avatar_before_first_frame"] == ""
    assert report["avatar_after_first_frame"] == "data:image/png;base64,AAAA"
    assert report["avatar_text_kept"] == "你好呀"


def test_local_bubbles_are_claimed_by_request_id(report):
    assert report["by_id_a"] == "句子A"
    assert report["by_id_b"] == "句子B"
    assert report["by_id_pending"] == 0
    assert report["by_id_failed"] == 0
    assert report["by_id_bubbles"] == 2


def test_echo_with_a_foreign_request_id_claims_nothing(report):
    assert report["foreign_id_kept"] == "我发的"
    assert report["foreign_id_pending"] == 1


def test_refused_ordinary_submission_restores_the_draft(report):
    assert report["ordinary_refused_rollback"] is True
    assert report["ordinary_refused_marked"] is True
    assert report["visit_refused_not_ordinary"] is True


def test_rejected_line_bubble_is_withdrawn(report):
    assert report["rejected_before"] is True
    assert report["rejected_after"] is False
    assert report["rejected_entry"] is False
    assert report["interrupted_kept"] == "被打断的|（被打断）"


def test_empty_visit_lines_never_leave_blank_bubbles(report):
    assert report["empty_final_never_rendered"] is False
    assert report["empty_final_before"] is True
    assert report["empty_final_after"] is False


def test_a_received_line_proves_the_visit_started(report):
    assert report["line_proves_started"] is True
    assert report["line_proves_started_known_visit"] is True
    assert report["line_kept_after_switch"] == "你好"


def test_snapshot_is_dropped_once_the_socket_is_cleared(report):
    assert report["null_socket_visit"] is None
    assert report["null_socket_lines"] == 0


def test_raw_dialogs_refresh_compact_hit_geometry(report):
    assert report["geometry_on_open"] >= 1
    assert report["geometry_on_close"] > report["geometry_on_open"]
    assert report["geometry_dialog_closed"] == 0


def test_character_switch_clears_the_old_visit_without_an_ended_message(report):
    assert report["switch_before_active"] is True
    assert report["switch_after_active"] is False
    assert report["switch_no_ended"] is True
    assert report["switch_pending"] == 0
    assert report["switch_persona_closed"] == 1


def test_join_flow_stays_pinned_to_the_starting_character(report):
    assert report["switch_preview_dialogs"] == 0
    assert report["switch_preview_joins"] == 0
    assert report["confirm_names_original"] is True
    assert report["confirm_switch_joins"] == 0


def test_late_ended_frame_after_state_reconciliation_appends_nothing(report):
    assert report["ended_once"] == 1
    assert report["ended_not_revived"] is False


def test_replayed_invite_ready_switches_to_the_new_visit(report):
    assert report["replay_invite_switched"] is True
    assert report["replay_invite_phase"] == "invite_ready"
    assert report["replay_invite_old_ended"] is True
    assert report["replay_invite_relay"] == [
        "ended:AAAAAAAAAAAAAAAAAAAAAA", "invite_ready:BBBBBBBBBBBBBBBBBBBBBB",
    ]


def test_replayed_invite_switches_from_a_retained_visit(report):
    assert report["replay_invite_visit"] is True
    assert report["replay_invite_old_ended2"] is True
    assert report["replay_invite_started"] == "active"
    assert report["ended_invite_ignored"] is True


def test_line_assembly_is_keyed_by_visit(report):
    assert report["keyed_lines"] == ["上一场", "新一场的片"]


def test_join_admitted_after_a_character_switch_is_ended(report):
    end = report["switched_join_end"]
    assert end is not None
    assert end["lanlan_name"] == "咪咪" and end["reason"] == "route_end" and end["visit_id"]


def test_invitation_during_a_state_probe_invalidates_the_snapshot(report):
    assert report["invite_probe_visit"] is True
    assert report["invite_probe_phase"] == "awaiting_accept"


def test_ending_snapshot_releases_the_ack_only_session(report):
    assert report["snapshot_ending_released"] is False


def test_room_created_after_a_character_switch_is_ended(report):
    end = report["switched_create_end"]
    assert end is not None
    assert end["lanlan_name"] == "咪咪" and end["reason"] == "route_end" and end["visit_id"]


def test_clicking_invite_restores_the_existing_code_without_creating_another_room(report):
    assert report["existing_invite_phase"] == "invite_ready"
    assert report["existing_invite_code"] == "ABCDEFGH23"
    assert report["existing_invite_creates"] == 0


def test_late_host_recovery_reply_does_not_overwrite_a_newer_socket_phase(report):
    assert report["stale_host_recovery_phase"] == "active"
    assert report["stale_host_recovery_code"] in (None, "")
    assert report["stale_host_recovery_creates"] == 0


def test_a_new_room_recovers_its_code_when_the_ready_frame_is_missed(report):
    assert report["created_invite_initial_phase"] == "pending"
    assert report["created_invite_recovered_phase"] == "invite_ready"
    assert report["created_invite_recovered_code"] == "QWERTYUI23"
    assert report["created_invite_creates"] == 1


def test_pending_frame_before_create_reply_still_recovers_the_invite_code(report):
    assert report["pending_before_create_recovered_phase"] == "invite_ready"
    assert report["pending_before_create_recovered_code"] == "ZXCVBNMA23"
    assert report["pending_before_create_count"] == 1


def test_missing_current_invite_reconciles_authoritative_visit_state(report):
    assert report["missing_current_reconciled_visit"] in (None, "")
    assert report["missing_current_state_probes"] == 1


def test_self_line_recipient_uses_the_lines_own_side(report):
    assert report["own_recipient_author"].endswith("咪咪")


def test_line_from_a_new_visit_switches_and_invalidates_the_probe(report):
    assert report["line_switch_visit"] is True
    assert report["line_switch_phase"] == "active"
    assert report["line_switch_side"] == "host"
    assert report["line_switch_old_ended"] is True


def test_visit_submissions_carry_their_visit_id(report):
    assert report["submit_visit_id"] == "A" * 22


def test_same_visit_line_promotes_a_waiting_phase_to_active(report):
    assert report["same_visit_line_phase"] == "active"
    assert report["same_visit_line_active"] is True


def test_late_echo_reclaims_its_failed_local_bubble(report):
    assert report["late_echo_before"] == "failed"
    assert report["late_echo_after"] == "sent"
    assert report["late_echo_text"] == "最后一句"
    assert report["late_echo_copies"] == 1


def test_late_peer_line_is_placed_before_the_ended_message(report):
    assert report["late_peer_order"] == ["第一句", "最后一句", "ENDED"]


def test_pending_direct_invite_retries_failed_question_delivery(report):
    assert report["direct_invite_delivery_retries"] == 2


def test_pending_direct_invite_regenerates_after_character_switch(report):
    assert report["direct_invite_character_questions"] == [
        "/api/visit/direct/AAAAAAAAAAAAAAAAAAAAAA/question?catgirl=%E5%92%AA%E5%92%AA",
        "/api/visit/direct/AAAAAAAAAAAAAAAAAAAAAA/question?catgirl=%E5%B0%8F%E6%A9%98",
    ]

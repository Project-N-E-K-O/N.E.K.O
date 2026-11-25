// Chat voice messages during a hosted visit: the transcript is sent as a
// visit line (same path as typed visit input), not as ordinary chat text.
//   - static/app/app-buttons.js            sendAudioMessagePayload routes to the visit
//   - static/app/app-react-chat-window/visit-chat.js  submitVisitAudioTranscript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../..');
const buttonsSource = fs.readFileSync(path.join(ROOT, 'static/app/app-buttons.js'), 'utf8');
const visitSource = fs.readFileSync(
  path.join(ROOT, 'static/app/app-react-chat-window/visit-chat.js'), 'utf8');
const VID = 'AAAAAAAAAAAAAAAAAAAAAA';

async function settle() {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
}

async function rejection(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail('expected a rejection');
}

// ── Host glue (app-buttons.js) ────────────────────────────────────────

const glueStart = buttonsSource.indexOf('    // ===== 语音消息：录音 -> 离线 ASR 转写');
const glue = buttonsSource.slice(
  glueStart, buttonsSource.indexOf('    function clearReactChatWindowHostBindingPoll() {', glueStart));

function glueHarness({ visitActive = true, outcomes = ['sent'], text = '你好', sendResult = true } = {}) {
  const calls = { fetch: [], sent: [], visit: [], toasts: [] };
  const queue = outcomes.slice();
  const flags = { visitActive };
  const window = {
    lanlan_config: { lanlan_name: 'Neko' },
    reactChatWindowHost: { setAudioMessageTarget() { return true; }, setOnComposerAudioMessage() {} },
    showStatusToast(message) { calls.toasts.push(message); },
    t: (key) => key,
    setInterval() { return 1; },
    addEventListener() {},
    nekoVisitChat: {
      isActive: () => flags.visitActive,
      async submitAudioTranscript(detail) {
        calls.visit.push(detail);
        const next = queue.shift();
        return typeof next === 'function' ? next(window) : next;
      },
    },
  };
  const mod = {
    async sendTextPayload(sentText, options) {
      calls.sent.push({ text: sentText, options });
      return typeof sendResult === 'function' ? sendResult(flags, window) : sendResult;
    },
  };
  async function fetch(url, init) {
    calls.fetch.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
    return { ok: true, status: 200, async json() { return { ok: true, text }; } };
  }
  const context = vm.createContext({
    window, mod, fetch, console: { warn() {} }, encodeURIComponent, JSON, Object, String, Error, Promise,
  });
  vm.runInContext(`${glue}\nthis.api = { sendAudioMessagePayload };`, context);
  return { api: context.api, calls };
}

const payload = { requestId: '1764-abc', target: 'Neko', audio: 'AAAA', sampleRateHz: 16000 };

test('during a visit the transcript goes out as a visit line, not ordinary chat text', async () => {
  const { api, calls } = glueHarness();
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.visit)),
    [{ text: '你好', requestId: '1764-abc', expectedLanlanName: 'Neko' }]);
  assert.equal(calls.sent.length, 0);
});

test('a refused visit line rejects as busy; the retry reuses the request id', async () => {
  const { api, calls } = glueHarness({ outcomes: ['refused', 'sent'] });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'busy');
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  // Same requestId to the backend (cached transcript) and to the visit line.
  assert.deepEqual(calls.fetch.map((call) => call.body.requestId), ['1764-abc', '1764-abc']);
  assert.deepEqual(calls.visit.map((detail) => detail.requestId), ['1764-abc', '1764-abc']);
  assert.equal(calls.sent.length, 0);
});

test('a transcript with nothing left to relay counts as no speech', async () => {
  const { api, calls } = glueHarness({ outcomes: ['empty'] });
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.deepEqual(calls.toasts, ['chat.audioMessageNoSpeech']);
  assert.equal(calls.sent.length, 0);
});

test('without a visit owning the composer the ordinary text path is used', async () => {
  const { api, calls } = glueHarness({ visitActive: false });
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.equal(calls.visit.length, 0);
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.sent[0].options.source, 'react-chat-window');
});

test('a visit that starts while the text is queued reroutes it to the visit line', async () => {
  // The ordinary send path hands the text back instead of sending it
  // (queued behind an avatar interaction, or waiting for the text session).
  const { api, calls } = glueHarness({
    visitActive: false,
    outcomes: ['refused', 'sent'],
    sendResult: (flags) => { flags.visitActive = true; return 'visit_reroute'; },
  });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'busy', 'a refused visit line keeps the draft');
  assert.equal(calls.sent[0].options.rerouteToVisit, true);
  assert.equal(calls.sent[0].options.awaitDeferredSend, true);
  assert.deepEqual(calls.visit.map((detail) => detail.requestId), ['1764-abc']);
  // The retry goes straight to the visit.
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.visit.length, 2);
});

test('switching characters while queued, then a visit starting, never sends to the new visit', async () => {
  const { api, calls } = glueHarness({
    visitActive: false,
    sendResult: (flags, window) => {
      window.lanlan_config.lanlan_name = 'Other';
      flags.visitActive = true;
      return 'visit_reroute';
    },
  });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'stale_target');
  assert.equal(calls.visit.length, 0);
});

test('a visit line refused after a character switch rejects as stale_target', async () => {
  // The visit send path refuses when the character changed while it waited.
  const { api } = glueHarness({
    outcomes: [(window) => { window.lanlan_config.lanlan_name = 'Other'; return 'refused'; }],
  });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'stale_target');
});

test('the ordinary send path checks for a visit before the bubble and right before sending', () => {
  const start = buttonsSource.indexOf('        async function sendTextPayloadInternal(rawText, options) {');
  const body = buttonsSource.slice(start, buttonsSource.indexOf('\n        function ', start + 10));
  const first = body.indexOf("options.rerouteToVisit === true && visitOwnsComposerForAudioMessage()");
  const second = body.indexOf("options.rerouteToVisit === true && visitOwnsComposerForAudioMessage()", first + 1);
  const bubble = body.indexOf('appendReactUserMessage');
  const sessionWait = body.indexOf('await mod._textSessionStartPromise;');
  const send = body.indexOf('            // Send message\n            if (S.socket && S.socket.readyState === WebSocket.OPEN)');
  assert.ok(first > 0 && first < bubble, 'checked before any bubble is drawn (queued submissions)');
  assert.ok(sessionWait < second && second < send, 'checked again after the text session wait');
  assert.ok(body.slice(second, send).includes('removeMessage(reactOptimisticMessageId)'));
  // Visit lines are not React-window sends, so they still wait for the user
  // name after the first character check: the check runs again after that wait.
  const nameWait = body.indexOf('await window.appChat.ensureUserDisplayName();');
  const textSend = body.indexOf('S.socket.send(JSON.stringify(textMessage));');
  const recheck = body.slice(nameWait, textSend);
  assert.ok(nameWait > send && textSend > nameWait);
  assert.match(recheck, /window\.lanlan_config\.lanlan_name === options\.expectedLanlanName\)\)\s*\{\s*updateReactOptimisticMessageStatus\('failed'\);\s*return false;/);
  // The deferred queue settles awaiting callers with the send result itself.
  const flush = buttonsSource.slice(buttonsSource.indexOf('    function flushDeferredTextSubmissions() {'));
  assert.match(flush.slice(0, flush.indexOf('\n    }\n')), /settleDeferredSubmission\(submission, sent\)/);
});

test('the chat window offers voice messages while a visit owns the composer', () => {
  const props = fs.readFileSync(
    path.join(ROOT, 'static/app/app-react-chat-window/geometry-and-messages.js'), 'utf8');
  const line = props.split('\n').find((text) => text.includes('audioMessageTarget:'));
  assert.ok(line, 'audioMessageTarget prop missing');
  assert.doesNotMatch(line, /visitOwnsComposer/);
  assert.match(line, /typeof I\.state\.onComposerAudioMessage === 'function'/);
});

// ── visit-chat.js (real script in a vm) ───────────────────────────────

function visitHarness({ sendResult = true } = {}) {
  const timers = [];
  let nextId = 1;
  let wall = 1700000000000;
  const messages = [];
  const rollbacks = [];
  const toasts = [];
  const sendCalls = [];
  const listeners = new Map();
  const host = {
    appendMessage(message) { messages.push(JSON.parse(JSON.stringify(message))); return message; },
    updateMessage(id, patch) {
      const message = messages.find((entry) => entry.id === id);
      if (message) Object.assign(message, JSON.parse(JSON.stringify(patch)));
    },
    removeMessage(id) {
      const index = messages.findIndex((entry) => entry.id === id);
      if (index >= 0) messages.splice(index, 1);
      return index >= 0;
    },
    getState() { return { messages }; },
  };
  const I = {
    renderWindow() {},
    rollbackLastDraft(id) { rollbacks.push(id); },
    clearPendingRollbackDraft() {},
  };
  const body = {
    children: [],
    appendChild(child) { this.children.push(child); return child; },
  };
  const documentShim = {
    body, documentElement: body, activeElement: body,
    createElement() {
      return {
        children: [], style: {}, attrs: {}, appendChild(child) { this.children.push(child); return child; },
        setAttribute(key, value) { this.attrs[key] = String(value); }, addEventListener() {},
      };
    },
    querySelectorAll() { return []; },
    addEventListener() {},
    removeEventListener() {},
  };
  const win = {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
    removeEventListener() {},
    dispatchEvent(event) { (listeners.get(event.type) || []).slice().forEach((fn) => fn(event)); return true; },
    __appReactChatWindowParts: I,
    reactChatWindowHost: host,
    lanlan_config: { lanlan_name: '咪咪', master_display_name: '阿明' },
    appState: { socket: { readyState: 1, send() {} }, isTextSessionActive: true },
    location: { pathname: '/' },
    showStatusToast(text) { toasts.push(text); },
    visitEnabled: true,
    nekoSocial: {},
    appChat: {
      renderVisitLine(spec) {
        const blocks = [{ type: 'text', text: spec.text, plain_text: true }];
        const existing = messages.find((entry) => entry.id === spec.messageId);
        const patch = { blocks, author: spec.author, status: 'sent' };
        if (existing) Object.assign(existing, patch);
        else messages.push(Object.assign({ id: spec.messageId }, patch));
      },
    },
    appButtons: {
      sendTextPayload(text, options) {
        sendCalls.push({ text, options });
        return Promise.resolve(typeof sendResult === 'function' ? sendResult() : sendResult);
      },
      markTextSubmissionRefused() { return true; },
    },
  };
  win.window = win;
  win.t = () => '';
  const sandbox = {
    window: win, document: documentShim, console,
    fetch: async () => ({ status: 404, ok: false, async json() { return {}; }, clone() { return this; } }),
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } },
    WebSocket: { OPEN: 1 },
    Date: class extends Date { static now() { return wall; } },
    setTimeout(fn, ms) { const id = nextId++; timers.push({ id, at: wall + (ms || 0), fn, every: 0 }); return id; },
    clearTimeout(id) { const index = timers.findIndex((timer) => timer.id === id); if (index >= 0) timers.splice(index, 1); },
    setInterval(fn, ms) { const id = nextId++; timers.push({ id, at: wall + ms, fn, every: ms }); return id; },
    clearInterval(id) { const index = timers.findIndex((timer) => timer.id === id); if (index >= 0) timers.splice(index, 1); },
    Map, Set, Number, Math, JSON, Object, Array, String, Promise, Infinity, RegExp, Error,
    localStorage: (() => {
      const store = new Map();
      return { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
    })(),
  };
  vm.createContext(sandbox);
  vm.runInContext(visitSource, sandbox, { filename: 'visit-chat.js' });

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
  const chat = win.nekoVisitChat;
  function state(action, extra) {
    chat.handleFrame(Object.assign({ type: 'visit_state_change', action, visit_id: VID, side: 'host', ts: 1 }, extra || {}));
  }
  function echo(requestId, text, lineId) {
    chat.handleFrame({
      type: 'visit_line', visit_id: VID, line_id: lineId || 'H1', text, final: true, truncated: false,
      request_id: requestId,
      speaker: { side: 'host', kind: 'human', name: '', self: true }, addressee: { side: 'guest', kind: 'cat' },
    });
  }
  function track(promise) {
    const result = { value: undefined, done: false };
    promise.then((value) => { result.value = value; result.done = true; });
    return result;
  }
  const local = (id) => messages.filter((message) => message.id === `visit-local-${id}`);
  return { I, chat, messages, rollbacks, toasts, sendCalls, state, echo, advance, track, local };
}

test('visit: the transcript is sent to the selected recipient and resolves on the echo', async () => {
  const h = visitHarness();
  h.state('started');
  assert.equal(h.chat.isActive(), true);
  const result = h.track(h.chat.submitAudioTranscript({ text: ' 你好 ', requestId: 'rec-1' }));
  await settle();
  assert.equal(h.sendCalls.length, 1);
  const { text, options } = h.sendCalls[0];
  assert.equal(text, '你好');
  assert.equal(options.source, 'neko_visit:guest_cat');
  assert.equal(options.visitId, VID);
  assert.equal(options.requestId, 'rec-1');
  assert.equal(options.visitLine, true);
  assert.equal(options.ignoreComposerAttachments, true);
  assert.equal(h.local('rec-1').length, 1);
  assert.equal(result.done, false, 'waits for the backend before clearing the draft');

  h.echo('rec-1', '你好');
  await settle();
  assert.deepEqual(result, { value: 'sent', done: true });
  assert.equal(h.local('rec-1')[0].blocks[0].text, '你好');

  h.I.handleVisitBarAction('to_own');
  const own = h.track(h.chat.submitAudioTranscript({ text: '给你的', requestId: 'rec-2' }));
  await settle();
  assert.equal(h.sendCalls[1].options.source, 'neko_visit:own_cat');
  h.echo('rec-2', '给你的', 'H2');
  await settle();
  assert.equal(own.value, 'sent');
});

test('visit: a backend refusal keeps the draft and the retry sends once more', async () => {
  const h = visitHarness();
  h.state('started');
  const first = h.track(h.chat.submitAudioTranscript({ text: '太快了', requestId: 'rec-1' }));
  await settle();
  h.chat.handleStatus('VISIT_INPUT_REFUSED_RATE', { visit_id: VID, request_id: 'rec-1' });
  await settle();
  assert.deepEqual(first, { value: 'refused', done: true });
  // No "not delivered" bubble and no transcript pushed into the text composer.
  assert.equal(h.local('rec-1').length, 0);
  assert.deepEqual(h.rollbacks, []);

  const retry = h.track(h.chat.submitAudioTranscript({ text: '太快了', requestId: 'rec-1' }));
  await settle();
  assert.equal(h.sendCalls.length, 2);
  assert.equal(h.local('rec-1').length, 1);
  h.echo('rec-1', '太快了');
  await settle();
  assert.equal(retry.value, 'sent');
  assert.equal(h.local('rec-1').length, 1);
});

test('visit: a line the page could not send is refused and leaves no bubble', async () => {
  const h = visitHarness({ sendResult: false });
  h.state('started');
  const result = h.track(h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }));
  await settle();
  assert.equal(result.value, 'refused');
  assert.equal(h.local('rec-1').length, 0);
  assert.deepEqual(h.rollbacks, []);
});

test('visit: phases that do not take input refuse without sending', async () => {
  for (const setup of [
    (h) => { h.state('pending'); h.state('awaiting_accept'); },
    (h) => { h.state('started'); h.state('wrap_up'); },
    (h) => { h.state('pending', { side: 'guest' }); h.state('departed', { side: 'guest' }); },
  ]) {
    const h = visitHarness();
    setup(h);
    assert.equal(h.chat.isActive(), true);
    assert.equal(await h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }), 'refused');
    h.advance(1);
    assert.equal(h.sendCalls.length, 0);
    assert.deepEqual(h.rollbacks, []);
    assert.equal(h.messages.length, 0);
  }
});

test('visit: text the relay would strip counts as empty and is not sent', async () => {
  const h = visitHarness();
  h.state('started');
  assert.equal(await h.chat.submitAudioTranscript({ text: '​‮', requestId: 'rec-1' }), 'empty');
  assert.equal(h.sendCalls.length, 0);
});

test('visit: without a verdict the line counts as sent; a late refusal marks it like typed input', async () => {
  const h = visitHarness();
  h.state('started');
  const result = h.track(h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }));
  await settle();
  h.advance(9999);
  await settle();
  assert.equal(result.done, false);
  h.advance(1);
  await settle();
  assert.equal(result.value, 'sent');
  h.chat.handleStatus('VISIT_E_BUSY', { visit_id: VID, request_id: 'rec-1' });
  assert.equal(h.local('rec-1')[0].status, 'failed');
});

test('visit: the echo timeout starts only after the line was actually sent', async () => {
  // Opening the text session first can take longer than the echo timeout.
  let finishSend;
  const h = visitHarness({ sendResult: () => new Promise((resolve) => { finishSend = resolve; }) });
  h.state('started');
  const result = h.track(h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }));
  await settle();
  h.advance(20000);
  await settle();
  assert.equal(result.done, false, 'still waiting for the send itself');
  finishSend(false); // the session never came up
  await settle();
  assert.equal(result.value, 'refused');
  assert.equal(h.local('rec-1').length, 0);

  const slow = visitHarness({ sendResult: () => new Promise((resolve) => { finishSend = resolve; }) });
  slow.state('started');
  const sent = slow.track(slow.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-2' }));
  await settle();
  slow.advance(20000);
  finishSend(true);
  await settle();
  slow.advance(9999);
  await settle();
  assert.equal(sent.done, false);
  slow.advance(1);
  await settle();
  assert.equal(sent.value, 'sent');
});

test('visit: the visit ending while waiting counts as sent (the line may have arrived)', async () => {
  const h = visitHarness();
  h.state('started');
  const result = h.track(h.chat.submitAudioTranscript({ text: '再见', requestId: 'rec-1' }));
  await settle();
  h.chat.handleFrame({ type: 'visit_state_change', action: 'ended', visit_id: VID, side: 'host', reason: 'normal', ts: 2 });
  await settle();
  assert.equal(result.value, 'sent');
  assert.equal(h.local('rec-1')[0].status, 'failed');
  assert.equal(h.chat.isActive(), false);
});

test('visit: a recording made for another character is not sent into this visit', async () => {
  const h = visitHarness();
  h.state('started');
  assert.equal(await h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1', expectedLanlanName: '别人' }), 'refused');
  assert.equal(h.sendCalls.length, 0);
  // The matching character is forwarded so the send path re-checks it after any wait.
  h.track(h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-2', expectedLanlanName: '咪咪' }));
  await settle();
  assert.equal(h.sendCalls[0].options.expectedLanlanName, '咪咪');
  assert.equal(h.sendCalls[0].options.visitLine, true);
});

test('visit: a second submission of a recording still in flight is not sent twice', async () => {
  const h = visitHarness();
  h.state('started');
  h.track(h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }));
  await settle();
  assert.equal(await h.chat.submitAudioTranscript({ text: '你好', requestId: 'rec-1' }), 'refused');
  assert.equal(h.sendCalls.length, 1);
  assert.equal(h.local('rec-1').length, 1);
});

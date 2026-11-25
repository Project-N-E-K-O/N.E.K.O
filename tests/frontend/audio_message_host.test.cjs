// Host glue for chat voice messages (static/app/app-buttons.js):
// recording -> /api/audio-message/transcribe -> ordinary text message.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-buttons.js'), 'utf8');

function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `missing Host section: ${start}`);
  return source.slice(from, to);
}

const glue = section(
  '    // ===== 语音消息：录音 -> 离线 ASR 转写',
  '    function clearReactChatWindowHostBindingPoll() {',
);

function harness({ responses = [], sendResult = true, target = 'Neko' } = {}) {
  const calls = { fetch: [], sent: [], toasts: [], targets: [], keepDraft: [], stopCapture: [], handlers: [], intervals: [], refreshedToken: 0 };
  const queue = responses.slice();
  const host = {
    setOnComposerAudioMessage(handler) { calls.handlers.push(handler); },
    draftOpen: false,
    capturing: false, // the open draft is still recording from the microphone
    // Mirrors the chat-window host: hiding is refused while a draft is open
    // and the caller asked to keep it, except that stopCapture ends a draft
    // that is still recording.
    setAudioMessageTarget(value, options) {
      calls.keepDraft.push(!!(options && options.keepDraft));
      calls.stopCapture.push(!!(options && options.stopCapture));
      if (!value && options && (options.keepDraft || options.stopCapture) && host.draftOpen
          && !(options.stopCapture && host.capturing)) return false;
      calls.targets.push(value);
      return true;
    },
  };
  const window = {
    lanlan_config: { lanlan_name: target },
    reactChatWindowHost: host,
    nekoLocalMutationSecurity: {
      async getMutationHeaders() { return { 'X-CSRF-Token': `token-${calls.refreshedToken}` }; },
      async refreshToken() { calls.refreshedToken += 1; },
    },
    showStatusToast(message, duration) { calls.toasts.push([message, duration]); },
    t: (key) => key,
    setInterval(fn) { calls.intervals.push(fn); return calls.intervals.length; },
    addEventListener() {},
  };
  const mod = {
    async sendTextPayload(text, options) {
      calls.sent.push({ text, options });
      return sendResult;
    },
  };
  async function fetch(url, init) {
    calls.fetch.push({ url, init });
    const next = queue.shift() || { status: 200, body: { supported: true } };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      async json() { return next.body; },
    };
  }
  const context = vm.createContext({
    window, mod, fetch, console: { warn() {} }, encodeURIComponent, JSON, Object, String, Error, Promise,
  });
  vm.runInContext(`${glue}\nthis.api = { sendAudioMessagePayload, refreshAudioMessageAvailability, bindAudioMessageHost };`, context);
  return { api: context.api, calls, host, window };
}

const payload = { requestId: '1764-abc', target: 'Neko', audio: 'AAAA', sampleRateHz: 16000 };

async function rejection(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail('expected a rejection');
}

test('transcribes, then sends the text through the ordinary text path', async () => {
  const { api, calls } = harness({ responses: [{ status: 200, body: { ok: true, text: ' 你好 ' } }] });
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  const [{ url, init }] = calls.fetch;
  assert.equal(url, '/api/audio-message/transcribe');
  assert.equal(init.method, 'POST');
  assert.equal(init.headers['X-CSRF-Token'], 'token-0');
  assert.deepEqual(JSON.parse(init.body), payload);
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.sent[0].text, '你好');
  assert.equal(calls.sent[0].options.requestId, '1764-abc');
  assert.equal(calls.sent[0].options.source, 'react-chat-window');
  assert.equal(calls.sent[0].options.ignoreComposerAttachments, true);
});

test('provider throttling and a refused text send both reject as busy', async () => {
  let { api } = harness({ responses: [{ status: 429, body: { ok: false, error_code: 'ASR_RATE_LIMITED' } }] });
  let error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'busy');

  const refused = harness({ responses: [{ status: 200, body: { ok: true, text: '好' } }], sendResult: false });
  error = await rejection(refused.api.sendAudioMessagePayload(payload));
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'busy');
});

test('a route that cannot transcribe rejects and hides the microphone', async () => {
  const { api, calls } = harness({
    responses: [
      { status: 409, body: { ok: false, error_code: 'ASR_CREDENTIALS_MISSING' } },
      { status: 200, body: { supported: false, error_code: 'ASR_CREDENTIALS_MISSING' } },
    ],
  });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'ASR_CREDENTIALS_MISSING');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.fetch[1].url, '/api/audio-message/status?target=Neko');
  // Hidden at once, then confirmed by the status check.
  assert.deepEqual(calls.targets, ['', '']);
  assert.equal(calls.sent.length, 0);
});

test('a stale CSRF token is refreshed and the upload retried once', async () => {
  const { api, calls } = harness({
    responses: [
      { status: 403, body: { ok: false, error_code: 'csrf_validation_failed' } },
      { status: 200, body: { ok: true, text: '重试成功' } },
    ],
  });
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.equal(calls.refreshedToken, 1);
  assert.equal(calls.fetch.length, 2);
  assert.equal(calls.fetch[1].init.headers['X-CSRF-Token'], 'token-1');
  assert.equal(calls.sent[0].text, '重试成功');
});

test('a recording without speech resolves with a notice and sends nothing', async () => {
  const { api, calls } = harness({ responses: [{ status: 200, body: { ok: true, text: '' } }] });
  assert.equal(await api.sendAudioMessagePayload(payload), true);
  assert.equal(calls.sent.length, 0);
  assert.deepEqual(calls.toasts, [['chat.audioMessageNoSpeech', 4000]]);
});

test('a recording made for another character is not sent', async () => {
  const { api, calls } = harness({ target: 'Other' });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'stale_target');
  assert.equal(calls.fetch.every((call) => !call.url.endsWith('/transcribe')), true);
});

test('network failure rejects as a general failure', async () => {
  const { window } = harness();
  const sent = [];
  const failing = vm.createContext({
    window,
    mod: { async sendTextPayload(text) { sent.push(text); return true; } },
    fetch: async () => { throw new Error('offline'); },
    console: { warn() {} }, encodeURIComponent, JSON, Object, String, Error, Promise,
  });
  vm.runInContext(`${glue}\nthis.api = { sendAudioMessagePayload };`, failing);
  const error = await rejection(failing.api.sendAudioMessagePayload(payload));
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'network');
  assert.deepEqual(sent, []);
});

test('binding installs the handler and offers the target only when supported', async () => {
  const supported = harness({ responses: [{ status: 200, body: { supported: true } }] });
  supported.api.bindAudioMessageHost(supported.host);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(supported.calls.handlers.length, 1);
  assert.deepEqual(supported.calls.targets, ['Neko']);

  const unsupported = harness({ responses: [{ status: 200, body: { supported: false } }] });
  await unsupported.api.refreshAudioMessageAvailability();
  assert.deepEqual(unsupported.calls.targets, ['']);
});

test('switching characters while transcribing drops the text instead of sending it', async () => {
  const context = harness({ responses: [{ status: 200, body: { ok: true, text: '给旧角色的话' } }] });
  const pending = context.api.sendAudioMessagePayload(payload);
  context.window.lanlan_config.lanlan_name = 'Other';
  const error = await rejection(pending);
  assert.equal(error.name, 'AudioMessageRejected');
  assert.equal(error.message, 'stale_target');
  assert.equal(context.calls.sent.length, 0);
});

test('a rejected key hides the microphone at once', async () => {
  const { api, calls } = harness({
    responses: [
      { status: 409, body: { ok: false, error_code: 'ASR_CREDENTIALS_REJECTED' } },
      { status: 200, body: { supported: false, error_code: 'ASR_CREDENTIALS_REJECTED' } },
    ],
  });
  const error = await rejection(api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'ASR_CREDENTIALS_REJECTED');
  assert.equal(calls.targets[0], '');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.targets, ['', '']);
});

test('hiding the microphone waits for an open draft so the recording survives', async () => {
  const context = harness({
    responses: [
      { status: 200, body: { supported: true } },
      { status: 409, body: { ok: false, error_code: 'ASR_CREDENTIALS_REJECTED' } },
      { status: 200, body: { supported: false, error_code: 'ASR_CREDENTIALS_REJECTED' } },
    ],
  });
  const { api, calls, host } = context;
  api.bindAudioMessageHost(host);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.targets, ['Neko']);

  host.draftOpen = true; // the failed recording is back in preview
  await rejection(api.sendAudioMessagePayload(payload));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.targets, ['Neko'], 'target kept while the draft is open');
  assert.equal(calls.keepDraft.at(-1), true);

  calls.intervals[0](); // still open: nothing changes
  assert.deepEqual(calls.targets, ['Neko']);
  host.draftOpen = false; // user cancelled or sent the draft
  calls.intervals[0]();
  assert.deepEqual(calls.targets, ['Neko', '']);
});

test('realtime voice withdraws the microphone, stops a recording in progress, and gives it back afterwards', async () => {
  const context = harness({ responses: [{ status: 200, body: { supported: true } }] });
  const { api, calls, host, window } = context;
  window.appState = { isRecording: false, voiceChatActive: false, voiceStartPending: false };
  api.bindAudioMessageHost(host);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.targets, ['Neko']);

  host.draftOpen = true; // a voice message is being recorded
  host.capturing = true;
  window.appState.isRecording = true; // realtime voice starts (mobile keeps the composer visible)
  calls.intervals[0]();
  assert.equal(calls.stopCapture.at(-1), true, 'the second microphone capture must stop');
  assert.deepEqual(calls.targets, ['Neko', '']);
  calls.intervals[0](); // nothing changes while realtime voice stays on
  assert.deepEqual(calls.targets, ['Neko', '']);

  host.draftOpen = false;
  window.appState.isRecording = false;
  calls.intervals[0]();
  assert.deepEqual(calls.targets, ['Neko', '', 'Neko']);
});

test('realtime voice that is on or starting keeps the microphone hidden when availability is checked', async () => {
  for (const busy of [{ appState: { voiceChatActive: true } }, { appState: { voiceStartPending: true } }, { isMicStarting: true }]) {
    const context = harness({ responses: [{ status: 200, body: { supported: true } }] });
    Object.assign(context.window, busy);
    await context.api.refreshAudioMessageAvailability();
    assert.deepEqual(context.calls.targets, [''], JSON.stringify(busy));
  }
});

test('a microphone withdrawn for a route error does not come back when realtime voice ends', async () => {
  let answerStatus;
  const context = harness({
    responses: [
      { status: 200, body: { supported: true } },
      { status: 409, body: { ok: false, error_code: 'ASR_CREDENTIALS_MISSING' } },
      // The follow-up status check is still in flight while realtime voice toggles.
      { status: 200, body: new Promise((resolve) => { answerStatus = resolve; }) },
    ],
  });
  const { api, calls, host, window } = context;
  window.appState = { isRecording: false };
  api.bindAudioMessageHost(host);
  await new Promise((resolve) => setImmediate(resolve));
  await rejection(api.sendAudioMessagePayload(payload));
  window.appState.isRecording = true;
  calls.intervals[0]();
  window.appState.isRecording = false;
  calls.intervals[0]();
  assert.equal(calls.targets.includes('Neko', 1), false, 'the failed route must not be offered again');
  answerStatus({ supported: false, error_code: 'ASR_CREDENTIALS_MISSING' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.targets.at(-1), '');
});

test('switching to a character without offline ASR does not keep the old draft', async () => {
  const context = harness({
    responses: [
      { status: 200, body: { supported: true } },
      { status: 200, body: { supported: false, error_code: 'ASR_OFFLINE_NOT_SUPPORTED' } },
    ],
  });
  const { api, calls, host, window } = context;
  api.bindAudioMessageHost(host);
  await new Promise((resolve) => setImmediate(resolve));
  host.draftOpen = true;
  window.lanlan_config.lanlan_name = 'Other';
  calls.intervals[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.keepDraft.at(-1), false);
  assert.deepEqual(calls.targets, ['Neko', '']);
});

test('the text send carries the recording character and reports a switch during it', async () => {
  const { window } = harness();
  // The shared send path refuses (returns false) when the character changed
  // while it was opening the text session.
  const send = [];
  const ctx = vm.createContext({
    window,
    mod: {
      async sendTextPayload(text, options) {
        send.push({ text, options });
        window.lanlan_config.lanlan_name = 'Other';
        return false;
      },
    },
    fetch: async () => ({ ok: true, status: 200, async json() { return { ok: true, text: '你好' }; } }),
    console: { warn() {} }, encodeURIComponent, JSON, Object, String, Error, Promise,
  });
  vm.runInContext(`${glue}\nthis.api = { sendAudioMessagePayload };`, ctx);
  const error = await rejection(ctx.api.sendAudioMessagePayload(payload));
  assert.equal(error.message, 'stale_target');
  assert.equal(send[0].options.expectedLanlanName, 'Neko');
});

test('the shared text send re-checks the character right before sending', () => {
  const guard = source.indexOf("if (typeof options.expectedLanlanName === 'string' && options.expectedLanlanName");
  const sendStart = source.indexOf('            // Send message\n            if (S.socket && S.socket.readyState === WebSocket.OPEN)');
  const sessionStart = source.indexOf('await mod._textSessionStartPromise;');
  assert.ok(guard > 0 && sendStart > 0 && sessionStart > 0);
  // After waiting for the text session (and its WebSocket), before the send.
  assert.ok(sessionStart < guard && guard < sendStart);
});

test('chat-window host: no voice messages while a visit owns the composer', () => {
  const props = fs.readFileSync(
    path.join(__dirname, '../../static/app/app-react-chat-window/geometry-and-messages.js'), 'utf8');
  assert.match(
    props,
    /audioMessageTarget: typeof I\.state\.onComposerAudioMessage === 'function' && !visitOwnsComposer/,
  );
  assert.ok(props.indexOf('var visitOwnsComposer') < props.indexOf('audioMessageTarget:'));
  assert.match(props, /onAudioMessageActiveChange: I\.handleAudioMessageActiveChange/);
});

test('chat-window host: draft activity comes from the chat state, not only the DOM', () => {
  const dock = fs.readFileSync(
    path.join(__dirname, '../../static/app/app-react-chat-window/minimize-and-idle-dock.js'), 'utf8');
  const start = dock.indexOf('function isAudioMessageActive()');
  const body = dock.slice(start, dock.indexOf('}', dock.indexOf('querySelector', start)));
  assert.ok(body.indexOf('I.state.audioMessageActive') > 0
    && body.indexOf('I.state.audioMessageActive') < body.indexOf('querySelector'));
  const handlers = fs.readFileSync(
    path.join(__dirname, '../../static/app/app-react-chat-window/message-bundle-actions-and-prompts.js'), 'utf8');
  const sandbox = { I: { state: { audioMessageActive: false } } };
  vm.runInNewContext(
    handlers.slice(
      handlers.indexOf('    I.handleAudioMessageActiveChange = '),
      handlers.indexOf('    I.handleComposerScreenshot = '),
    ),
    sandbox,
  );
  sandbox.I.handleAudioMessageActiveChange(true);
  assert.equal(sandbox.I.state.audioMessageActive, true);
  sandbox.I.handleAudioMessageActiveChange('yes');
  assert.equal(sandbox.I.state.audioMessageActive, false);
});

function deferredQueueHarness(sendHandler) {
  const window = { lanlan_config: { lanlan_name: 'Neko' } };
  const context = vm.createContext({ window, console: { error() {} }, Object, String, Promise });
  vm.runInContext([
    section('    var avatarInteractionTextContinuationState = {', '    var avatarInteractionDispatchGateState = {'),
    section('    function hasPendingAvatarInteractionContinuation() {', '    function releaseDeferredTextAfterAvatarInteraction() {'),
    'this.api = { state: avatarInteractionTextContinuationState, queueDeferredTextSubmission, flushDeferredTextSubmissions };',
  ].join('\n'), context);
  context.api.state.deferredSendHandler = sendHandler;
  return { ...context.api, window };
}

async function settleQueue() {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

test('a queued voice transcript for a switched character is dropped, not retried forever', async () => {
  let attempts = 0;
  // Mirrors sendTextPayloadInternal's guard: refuses while the character differs.
  const queue = deferredQueueHarness(async (text, options) => {
    attempts += 1;
    return queue.window.lanlan_config.lanlan_name === options.expectedLanlanName;
  });
  const settled = [];
  queue.queueDeferredTextSubmission('给旧角色的话', { expectedLanlanName: 'Neko' }, (sent) => settled.push(sent));
  queue.window.lanlan_config.lanlan_name = 'Other'; // switched while the avatar interaction ran
  queue.flushDeferredTextSubmissions();
  await settleQueue();
  assert.equal(attempts, 0, 'a void voice message never enters the send path');
  assert.deepEqual(settled, [false]);
  assert.equal(queue.state.deferredTextSubmissions.length, 0);
  assert.equal(queue.state.drainingDeferredTextSubmissions, false);
});

test('a queued voice transcript reports its real send result to the caller', async () => {
  const sent = [];
  const queue = deferredQueueHarness(async (text) => { sent.push(text); return true; });
  const settled = [];
  queue.queueDeferredTextSubmission('你好', { expectedLanlanName: 'Neko' }, (ok) => settled.push(ok));
  queue.flushDeferredTextSubmissions();
  await settleQueue();
  assert.deepEqual(sent, ['你好']);
  assert.deepEqual(settled, [true]);
});

test('a queued voice transcript that fails is handed back instead of re-queued', async () => {
  let attempts = 0;
  const queue = deferredQueueHarness(async () => { attempts += 1; return false; });
  const settled = [];
  queue.queueDeferredTextSubmission('稍后再发', { expectedLanlanName: 'Neko' }, (ok) => settled.push(ok));
  queue.flushDeferredTextSubmissions();
  await settleQueue();
  assert.equal(attempts, 1);
  assert.deepEqual(settled, [false]);
  assert.equal(queue.state.deferredTextSubmissions.length, 0);
});

test('the deferral branch hands voice messages a promise of the real result', () => {
  const branch = section(
    "                if (deferredOptions.awaitDeferredSend === true) {",
    '                queueDeferredTextSubmission(text, deferredOptions);\n                return true;',
  );
  assert.match(branch, /return new Promise\(function \(resolve\) \{\s*queueDeferredTextSubmission\(text, deferredOptions, resolve\);/);
  assert.match(glue, /awaitDeferredSend: true/);
});

test('realtime voice keeps a recording that is already being sent until its request settles', async () => {
  const context = harness({ responses: [{ status: 200, body: { supported: true } }] });
  const { api, calls, host, window } = context;
  window.appState = { isRecording: false };
  api.bindAudioMessageHost(host);
  await new Promise((resolve) => setImmediate(resolve));

  host.draftOpen = true; // recorded and now sending (no microphone in use)
  host.capturing = false;
  window.appState.isRecording = true;
  calls.intervals[0]();
  assert.deepEqual(calls.targets, ['Neko'], 'a sending recording is not cancelled');
  assert.equal(calls.stopCapture.at(-1), true);
  calls.intervals[0](); // still sending: still kept
  assert.deepEqual(calls.targets, ['Neko']);

  host.draftOpen = false; // the send settled (sent, or cancelled after a failure)
  calls.intervals[0]();
  assert.deepEqual(calls.targets, ['Neko', '']);
});

test('chat-window host: only a capturing draft is stopped for realtime voice', () => {
  const api = fs.readFileSync(
    path.join(__dirname, '../../static/app/app-react-chat-window/resize-drag-and-api.js'), 'utf8');
  const start = api.indexOf('        setAudioMessageTarget: function (target, options) {');
  const setter = api.slice(start, api.indexOf('\n        },', start) + 11);
  const run = (state, target, options, active) => {
    const I = { state: Object.assign({ audioMessageTarget: 'Neko', deferredAudioMessageTarget: null }, state),
      isAudioMessageActive: () => active, renderWindow() {} };
    const sandbox = { I };
    vm.runInNewContext(`this.setter = { ${setter.trim().replace(/,$/, '')} }.setAudioMessageTarget;`, sandbox);
    return { applied: sandbox.setter(target, options), I };
  };
  assert.equal(run({ audioMessagePhase: 'recording' }, '', { stopCapture: true }, true).applied, true);
  const sending = run({ audioMessagePhase: 'sending' }, '', { stopCapture: true }, true);
  assert.equal(sending.applied, false);
  assert.equal(sending.I.state.deferredAudioMessageTarget, '');
  assert.equal(run({ audioMessagePhase: 'preview' }, '', { keepDraft: true }, true).applied, false);
  assert.equal(run({ audioMessagePhase: 'idle' }, '', { stopCapture: true }, false).applied, true);
  assert.equal(run({ audioMessagePhase: 'preview' }, '', {}, true).applied, true, 'a character switch clears at once');
});

test('chat-window host: a deferred hide applies the moment the draft ends', () => {
  const handlers = fs.readFileSync(
    path.join(__dirname, '../../static/app/app-react-chat-window/message-bundle-actions-and-prompts.js'), 'utf8');
  let renders = 0;
  const sandbox = { I: { state: { audioMessageActive: true, audioMessageTarget: 'Neko', deferredAudioMessageTarget: '' },
    renderWindow() { renders += 1; } } };
  vm.runInNewContext(
    handlers.slice(handlers.indexOf('    I.handleAudioMessageActiveChange = '), handlers.indexOf('    I.handleComposerScreenshot = ')),
    sandbox,
  );
  sandbox.I.handleAudioMessageActiveChange(true, 'sending');
  assert.equal(sandbox.I.state.audioMessageTarget, 'Neko');
  sandbox.I.handleAudioMessageActiveChange(false, 'idle');
  assert.equal(sandbox.I.state.audioMessageTarget, '');
  assert.equal(sandbox.I.state.deferredAudioMessageTarget, null);
  assert.equal(renders, 1);
});

test('a voice transcript that lands after a theater started is not submitted as a theater line', () => {
  // Same character, so both target checks pass; the theater branch must
  // refuse voice messages before handing text to the theater runtime.
  assert.match(glue, /audioMessage: true/);
  const start = source.indexOf("            if (theaterRuntime && typeof theaterRuntime.isActive === 'function' && theaterRuntime.isActive()) {");
  const branch = source.slice(start, source.indexOf('return theaterRuntime.handleComposerSubmit(text);', start));
  assert.ok(start > 0 && branch.length > 0);
  // The refusal is the branch's first check, ahead of any theater dispatch;
  // ordinary typed theater input still reaches handleComposerSubmit.
  const refusal = branch.indexOf('if (options.audioMessage === true) return false;');
  assert.ok(refusal >= 0 && refusal < branch.indexOf("options.source !== 'react-chat-window'"));
});

test('a queued voice transcript is dropped when a theater started meanwhile; queued text is not', async () => {
  const sent = [];
  const queue = deferredQueueHarness(async (text) => { sent.push(text); return true; });
  const settled = [];
  queue.queueDeferredTextSubmission('语音', { expectedLanlanName: 'Neko', audioMessage: true }, (ok) => settled.push(ok));
  queue.queueDeferredTextSubmission('打字', { source: 'react-chat-window' });
  queue.window.nekoTheaterRuntime = { isActive: () => true };
  queue.flushDeferredTextSubmissions();
  await settleQueue();
  assert.deepEqual(settled, [false]);
  assert.deepEqual(sent, ['打字']);
});

test('the shared text send re-checks the theater for voice messages right before sending', () => {
  const sendStart = source.indexOf('            // Send message\n            if (S.socket && S.socket.readyState === WebSocket.OPEN)');
  const sessionStart = source.indexOf('await mod._textSessionStartPromise;');
  const guard = source.indexOf('if (options.audioMessage === true && isTheaterRuntimeActive()) {');
  assert.ok(sessionStart > 0 && guard > sessionStart && guard < sendStart);
});

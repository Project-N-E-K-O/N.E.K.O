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
  const calls = { fetch: [], sent: [], toasts: [], targets: [], handlers: [], refreshedToken: 0 };
  const queue = responses.slice();
  const host = {
    setOnComposerAudioMessage(handler) { calls.handlers.push(handler); },
    setAudioMessageTarget(value) { calls.targets.push(value); },
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
    setInterval() { return 1; },
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
  assert.deepEqual(calls.targets, ['']);
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

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const readRepoFile = (relPath) => fs.readFileSync(path.join(ROOT, relPath), 'utf8');

const HOST_JS = readRepoFile('static/game/sdk/neko-minigame-same-origin-host.js');
const REG_JS = readRepoFile('static/game/games/soccer/soccer-neko-host-registration.js');
const BOOTSTRAP_JS = readRepoFile('static/game/sdk/neko-minigame-same-origin-bootstrap.js');
const ADAPTER_JS = readRepoFile('static/game/games/soccer/soccer-neko-adapter.js');
const SDK_JS = readRepoFile('static/game/sdk/neko-minigame-sdk.js');

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function createSoccerSdkHarness({ makeFetch, security } = {}) {
  const storage = new Map();
  const listeners = new Map();
  const addEventListener = (type, handler) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(handler);
  };
  const removeEventListener = (type, handler) => listeners.get(type)?.delete(handler);

  const launchNode = {
    textContent: JSON.stringify({
      registrations: {
        soccer: {
          mode: 'registered',
          gameId: 'soccer',
          publisherId: 'project-n-e-k-o',
          version: '1.0.0',
          allowedCapabilities: ['runtime', 'logging', 'dialogue', 'quick-lines'],
        },
      },
    }),
    remove() { this.removed = true; },
  };

  const sandbox = {
    console,
    AbortController,
    Response,
    Promise,
    Error,
    TypeError,
    URL,
    Blob: typeof Blob !== 'undefined' ? Blob : class Blob {},
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
    clearInterval: () => {},
  };

  const documentMock = {
    hidden: false,
    visibilityState: 'visible',
    currentScript: null,
    addEventListener,
    removeEventListener,
    getElementById: (id) => (id === 'neko-minigame-host-launch' ? launchNode : null),
    createElement: () => ({ remove() {} }),
    head: {
      appendChild(script) {
        documentMock.currentScript = script;
        try {
          vm.runInContext(HOST_JS, sandbox, { filename: 'neko-minigame-same-origin-host.js' });
        } finally {
          documentMock.currentScript = null;
        }
        script.onload?.();
      },
    },
  };

  let sandboxJson = null;
  const inSandbox = (value) => sandboxJson.parse(JSON.stringify(value));
  const jsonResponse = (payload, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => inSandbox(payload),
    clone() {
      return jsonResponse(payload, status);
    },
  });

  const windowMock = {
    console,
    document: documentMock,
    navigator: { sendBeacon: () => false },
    location: { origin: 'http://127.0.0.1:48911', search: '' },
    lanlan_config: { lanlan_name: 'runtime-neko' },
    nekoLocalMutationSecurity: security,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
    clearInterval: () => {},
    addEventListener,
    removeEventListener,
    createSoccerAvatarHost: () => ({ async mount() { return { dispose() {} }; }, dispose() {} }),
    localStorage: {
      get length() { return storage.size; },
      key: (i) => Array.from(storage.keys())[i] ?? null,
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    fetch: makeFetch({ jsonResponse }),
  };

  sandbox.window = windowMock;
  sandbox.document = documentMock;
  sandbox.navigator = windowMock.navigator;
  vm.createContext(sandbox);

  sandboxJson = vm.runInContext('JSON', sandbox);
  const sandboxPayload = (value) => Object.assign(vm.runInContext('({})', sandbox), value);

  vm.runInContext(REG_JS, sandbox, { filename: 'soccer-neko-host-registration.js' });
  vm.runInContext(BOOTSTRAP_JS, sandbox, { filename: 'neko-minigame-same-origin-bootstrap.js' });
  await windowMock.nekoMiniGameSameOriginHostReady;
  vm.runInContext(ADAPTER_JS, sandbox, { filename: 'soccer-neko-adapter.js' });
  vm.runInContext(SDK_JS, sandbox, { filename: 'neko-minigame-sdk.js' });

  const host = await windowMock.createSoccerNekoAdapter({ audioHost: {} });
  const game = await windowMock.NekoMiniGame.connect(inSandbox({
    id: 'soccer',
    version: '1.0.0',
    protocolVersion: '1',
    requiredCapabilities: ['runtime', 'logging'],
    optionalCapabilities: ['dialogue', 'quick-lines'],
  }), {
    transport: host,
    windowImpl: windowMock,
    documentImpl: documentMock,
  });

  const callQuickLines = (payload, opts) => game.dialogue.quickLines(
    inSandbox(payload),
    opts ? sandboxPayload(opts) : undefined,
  );

  return { windowMock, host, game, callQuickLines, sandbox };
}

test('Codex #79: stalled /api/config/page_config abort settles quickLines provider and does not leak inFlight busy slots', async () => {
  for (const mode of ['abort-aware', 'hang-forever']) {
    let pageConfigStalled = true;
    let pageConfigRequests = 0;
    let pageConfigAborted = 0;
    const quickLinePosts = [];

    const { host, game, callQuickLines } = await createSoccerSdkHarness({
      security: undefined,
      makeFetch: ({ jsonResponse }) => async (url, init = {}) => {
        const target = String(url);
        if (target === '/api/config/page_config') {
          pageConfigRequests += 1;
          if (pageConfigStalled) {
            if (mode === 'hang-forever') {
              return new Promise(() => {});
            }
            return new Promise((_, reject) => {
              const onAbort = () => {
                pageConfigAborted += 1;
                const err = new Error('Aborted');
                err.name = 'AbortError';
                reject(err);
              };
              if (init.signal?.aborted) onAbort();
              else init.signal?.addEventListener('abort', onAbort, { once: true });
            });
          }
          return jsonResponse({ autostart_csrf_token: 'tok-soccer-recovered' });
        }
        if (target === '/api/game/soccer/quick-lines') {
          quickLinePosts.push({ headers: { ...(init.headers || {}) }, body: init.body });
          return jsonResponse({ ok: true, lines: ['Shoot!'] });
        }
        throw new Error(`unexpected fetch in ${mode}: ${target}`);
      },
    });

    try {
      // MAX_DIALOGUE_PENDING_REQUESTS is 4: trigger 5 timed-out calls to prove inFlight slots release every time.
      for (let i = 0; i < 5; i += 1) {
        await assert.rejects(
          () => callQuickLines({ i18n_language: 'zh-CN' }, { timeoutMs: 250 }),
          (err) => err && err.code === 'timeout',
        );
      }

      // Let any microtask cleanup finish and confirm zero unauthenticated POSTs escaped.
      await new Promise((resolve) => setTimeout(resolve, 10));
      assert.equal(pageConfigRequests, 5);
      assert.equal(quickLinePosts.length, 0);
      if (mode === 'abort-aware') {
        assert.equal(pageConfigAborted, 5);
      }
      assert.equal(game.dialogue.pendingCount, 0);

      // Once upstream recovers, the next call must succeed rather than failing with code='busy'.
      pageConfigStalled = false;
      const recovered = await callQuickLines({ i18n_language: 'zh-CN' }, { timeoutMs: 500 });
      assert.equal(recovered.ok, true);
      assert.deepEqual(Array.from(recovered.data.lines), ['Shoot!']);
      assert.equal(quickLinePosts.length, 1);
      assert.equal(quickLinePosts[0].headers['X-CSRF-Token'], 'tok-soccer-recovered');
    } finally {
      game.dispose();
      host.dispose();
    }
  }
});

test('Codex #79: stalled security.getMutationHeaders aborts without falling back to page_config or leaking busy slots', async () => {
  let stallSecurity = true;
  let pageConfigCalls = 0;
  const quickLinePosts = [];

  const security = {
    async getMutationHeaders() {
      if (stallSecurity) return new Promise(() => {});
      return { 'X-CSRF-Token': 'security-token-ok' };
    },
  };

  const { host, game, callQuickLines } = await createSoccerSdkHarness({
    security,
    makeFetch: ({ jsonResponse }) => async (url, init = {}) => {
      const target = String(url);
      if (target === '/api/config/page_config') {
        pageConfigCalls += 1;
        return jsonResponse({ autostart_csrf_token: 'should-not-be-used' });
      }
      if (target === '/api/game/soccer/quick-lines') {
        quickLinePosts.push({ headers: { ...(init.headers || {}) } });
        return jsonResponse({ ok: true, lines: ['Nice pass!'] });
      }
      throw new Error(`unexpected fetch: ${target}`);
    },
  });

  try {
    for (let i = 0; i < 5; i += 1) {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 5);
      await assert.rejects(
        () => callQuickLines({ i18n_language: 'zh-CN' }, { signal: controller.signal, timeoutMs: 500 }),
        (err) => err && err.code === 'cancelled',
      );
    }

    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(pageConfigCalls, 0, 'abort during security.getMutationHeaders must not fall back to page_config');
    assert.equal(quickLinePosts.length, 0);

    stallSecurity = false;
    const recovered = await callQuickLines({ i18n_language: 'zh-CN' }, { timeoutMs: 500 });
    assert.equal(recovered.ok, true);
    assert.equal(quickLinePosts.length, 1);
    assert.equal(quickLinePosts[0].headers['X-CSRF-Token'], 'security-token-ok');
  } finally {
    game.dispose();
    host.dispose();
  }
});

test('Codex #79: abort during 403 clone().json() and 403 security.refreshToken() rejects with AbortError without retrying', async () => {
  for (const scenario of ['stalled-403-json', 'stalled-refresh-token']) {
    let quickLinePosts = 0;
    let pageConfigCalls = 0;
    let allowSuccess = false;

    const security = {
      async refreshToken() {
        if (scenario === 'stalled-refresh-token' && !allowSuccess) {
          return new Promise(() => {});
        }
      },
      async getMutationHeaders() {
        return { 'X-CSRF-Token': allowSuccess ? 'fresh-csrf' : 'stale-csrf' };
      },
    };

    const { host, game, callQuickLines } = await createSoccerSdkHarness({
      security,
      makeFetch: ({ jsonResponse }) => async (url) => {
        const target = String(url);
        if (target === '/api/config/page_config') {
          pageConfigCalls += 1;
          return jsonResponse({ autostart_csrf_token: 'unexpected' });
        }
        if (target === '/api/game/soccer/quick-lines') {
          quickLinePosts += 1;
          if (allowSuccess) {
            return jsonResponse({ ok: true, lines: ['Recovered!'] });
          }
          if (scenario === 'stalled-403-json') {
            return {
              ok: false,
              status: 403,
              clone() {
                return {
                  json: () => new Promise(() => {}),
                };
              },
            };
          }
          return jsonResponse({ error_code: 'csrf_validation_failed' }, 403);
        }
        throw new Error(`unexpected fetch: ${target}`);
      },
    });

    try {
      // First verify raw host-provider rejection is AbortError when options.signal aborts.
      const hostAc = new AbortController();
      setTimeout(() => hostAc.abort(), 10);
      await assert.rejects(
        () => host.getQuickLines({ i18n_language: 'zh-CN' }, { signal: hostAc.signal }),
        (err) => err && err.name === 'AbortError',
      );
      assert.equal(quickLinePosts, 1, `${scenario}: raw abort must not issue retry POST`);

      // Next verify 5 SDK cancellations also free all dialogue.quickLines slots cleanly.
      quickLinePosts = 0;
      for (let i = 0; i < 5; i += 1) {
        const sdkAc = new AbortController();
        setTimeout(() => sdkAc.abort(), 10);
        await assert.rejects(
          () => callQuickLines({ i18n_language: 'zh-CN' }, { signal: sdkAc.signal, timeoutMs: 500 }),
          (err) => err && err.code === 'cancelled',
        );
      }
      assert.equal(quickLinePosts, 5, `${scenario}: cancelled retries must not issue second POST`);
      assert.equal(pageConfigCalls, 0, `${scenario}: abort must not fall back to page_config`);

      allowSuccess = true;
      const recovered = await callQuickLines({ i18n_language: 'zh-CN' }, { timeoutMs: 500 });
      assert.equal(recovered.ok, true);
      assert.deepEqual(Array.from(recovered.data.lines), ['Recovered!']);
    } finally {
      game.dispose();
      host.dispose();
    }
  }
});

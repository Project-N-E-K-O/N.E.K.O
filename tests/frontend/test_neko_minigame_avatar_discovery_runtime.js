const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.NEKO_AVATAR_REFERENCE_DIR || path.resolve(__dirname, '../..');
const read = (name) => fs.readFileSync(path.join(root, 'static/game/sdk', name), 'utf8');
const tick = async () => { for (let i = 0; i < 40; i += 1) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const response = (data) => ({ ok: true, status: 200, json: async () => data, clone: () => response(data) });
const descriptor = { name: 'Neko', model: { type: 'vrm', path: '/models/neko.vrm' }, rendererAvailable: true };

async function environment(factory, fetchImpl) {
  const timers = new Map();
  const listeners = new Map();
  let timerId = 0;
  const w = {
    AbortController, console: { warn() {}, error() {}, log() {} },
    location: { origin: 'http://127.0.0.1:48911' }, navigator: {},
    fetch: fetchImpl || (async (url) => {
      if (String(url).endsWith('/characters')) return response({ names: ['Neko', 'Other'] });
      if (String(url).includes('/character')) return response({
        lanlan_name: 'Neko', model_type: 'live3d', live3d_sub_type: 'vrm',
        vrm_path: '/models/neko.vrm', language: 'ja', language_preference_resolved: true,
        personality: 'private', api_key: 'private',
      });
      return response({ ok: true, active: true, state: { game_route_active: true } });
    }),
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { throw new Error('Unexpected monitor'); }, clearInterval() {},
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
  };
  const launch = {
    textContent: JSON.stringify({ registrations: { 'example-game': {
      mode: 'registered', gameId: 'example-game', version: '1.0.0',
      allowedCapabilities: ['runtime', 'logging', 'avatar-renderer'],
    } } }),
    nekoCapabilityProviders: { 'example-game': { avatarHostFactory: factory } }, remove() {},
  };
  w.document = {
    currentScript: null, getElementById: () => launch, createElement: () => ({ remove() {} }),
    head: { appendChild(script) {
      w.document.currentScript = script;
      vm.runInThisContext(read('neko-minigame-same-origin-host.js'));
      w.document.currentScript = null;
      script.onload();
    } },
  };
  global.window = w;
  vm.runInThisContext(read('neko-minigame-same-origin-bootstrap.js'));
  await w.nekoMiniGameSameOriginHostReady;
  vm.runInThisContext(read('neko-minigame-sdk.js'));
  return {
    w, timers, listeners,
    host: (extra = {}) => w.createNekoMiniGameSameOriginHost({ gameType: 'example-game', ...extra }),
    async game(host, required = true) {
      const game = await w.NekoMiniGame.connect({ id: 'example-game', version: '1.0.0',
        requiredCapabilities: ['runtime', 'logging', ...(required ? ['avatar-renderer'] : [])],
        optionalCapabilities: required ? [] : ['avatar-renderer'],
      }, { transport: host, windowImpl: w, documentImpl: w.document });
      game.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
      return game;
    },
  };
}

async function factories() {
  let created = 0; let released = 0;
  const env = await environment(() => { created++; return { mount() {}, dispose() { released++; } }; });
  for (let i = 0; i < 3; i++) assert.throws(() => env.host({ gameVersion: 'wrong' }), { code: 'game_unregistered' });
  assert.equal(created, 0, 'provider created before constructor validation');
  const host = env.host();
  assert.equal(created, 1, 'trusted factory was not used');
  host.dispose(); host.dispose();
  assert.equal(released, 1);
  const failed = await environment(({ onCleanup, signal }) => {
    assert.equal(signal.aborted, false);
    onCleanup(() => { released++; assert.equal(signal.aborted, true); });
    throw new Error('partial construction');
  });
  const optional = await failed.game(failed.host(), false);
  assert.equal(optional.capabilities.has('avatar-renderer'), false);
  assert.equal(released, 2, 'partial factory allocation leaked');
  optional.dispose();
  await assert.rejects(failed.game(failed.host()), { code: 'capability_unavailable' });
  assert.equal(released, 3);
  const absent = await environment(null);
  let legacyDisposed = 0; let mounted = 0;
  let controllerDisposed = 0;
  // Same lazy proxy shape used by the existing soccer page; no new query hooks.
  const backing = { mount() { mounted++; return { dispose() { controllerDisposed++; } }; }, dispose() { legacyDisposed++; } };
  const legacy = { mount(config) { return backing.mount(config); }, dispose() { backing.dispose(); } };
  const legacyHost = absent.host({ avatarHost: legacy });
  const legacyGame = await absent.game(legacyHost);
  assert.equal(legacyGame.capabilities.has('avatar-renderer'), true);
  const oldResponse = await legacyHost.getCharacter();
  const oldData = await oldResponse.json();
  assert.equal(oldData.vrm_path, '/models/neko.vrm');
  assert.equal(oldData.language, 'ja');
  assert.equal(oldData.language_preference_resolved, true);
  assert.equal(legacyHost.routeLanlanName, 'Neko');
  await legacyGame.avatar.mount({ slot: 'opponent', model: descriptor.model,
    viewport: { mode: 'fixed', width: 200, height: 300 }, resize: { mode: 'fixed' } });
  assert.equal(mounted, 1);
  legacyGame.dispose(); legacyHost.dispose();
  assert.equal(legacyDisposed, 1);
  assert.equal(controllerDisposed, 1);
  const preferred = env.host({ avatarHost: legacy });
  assert.equal(created, 1, 'legacy injection unnecessarily created a registered provider');
  preferred._initializeAvatar(() => { throw new Error('must only initialize once'); });
  preferred.dispose();
  const noAvatar = await absent.game(absent.host({ trustedAvatarHost: legacy }), false);
  assert.equal(noAvatar.capabilities.has('avatar-renderer'), false);
  noAvatar.dispose();
  assert.equal(env.timers.size + failed.timers.size + absent.timers.size, 0);

  let cleanupCount = 0;
  const invalid = await environment(({ onCleanup }) => {
    onCleanup(() => { cleanupCount++; });
    return { get mount() { throw new Error('bad provider'); }, dispose() { cleanupCount++; } };
  });
  const degraded = await invalid.game(invalid.host(), false);
  assert.equal(cleanupCount, 2, 'invalid provider or partial allocation leaked');
  degraded.dispose();

  const late = deferred(); let lateDisposed = 0;
  const asyncFactory = await environment(() => late.promise);
  const asyncHost = await asyncFactory.game(asyncFactory.host(), false);
  asyncHost.dispose();
  late.resolve({ dispose() { lateDisposed++; } });
  await tick();
  assert.equal(lateDisposed, 1, 'accidentally async factory result leaked');
  let boundedCleanup = 0;
  const overflow = await environment(({ onCleanup }) => {
    for (let i = 0; i < 17; i++) onCleanup(() => { boundedCleanup++; });
  });
  const overflowGame = await overflow.game(overflow.host(), false);
  assert.equal(boundedCleanup, 17, 'cleanup overflow leaked a partial allocation');
  overflowGame.dispose();

  for (const failedInitialization of [false, true]) {
    let registerCleanup;
    let releasedLate = 0;
    const unhandled = [];
    const onUnhandled = error => unhandled.push(error.message);
    const env = await environment(({ onCleanup }) => {
      registerCleanup = onCleanup;
      if (failedInitialization) throw new Error('initialization failed');
      return { mount() {}, dispose() {} };
    });
    const host = env.host();
    if (!failedInitialization) host.dispose();
    process.on('unhandledRejection', onUnhandled);
    let synchronousError;
    try {
      try {
        registerCleanup(() => { releasedLate++; throw new Error('late sync cleanup'); });
      } catch (error) { synchronousError = error.message; }
      registerCleanup(async () => { releasedLate++; throw new Error('late async cleanup'); });
      registerCleanup(() => { releasedLate++; });
      await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual({ synchronousError, unhandled }, { synchronousError: undefined, unhandled: [] },
        'late cleanup failures escaped the host after disposal or failed initialization');
      assert.equal(releasedLate, 3, 'late cleanup failure prevented remaining resource release');
      assert.equal(host._avatarCleanup.length, 0, 'late callbacks were retained after release');
    } finally {
      process.off('unhandledRejection', onUnhandled);
      host.dispose();
    }
  }
}

async function queries() {
  for (const length of [65, 128, 129]) {
    const name = '🐈'.repeat(length);
    const value = { ...descriptor, name };
    // Exercise both the built-in HTTP source and the SDK's transport boundary.
    for (const customTransport of [false, true]) {
      const env = await environment(() => ({ mount() {}, dispose() {} }), async url => (
        response(String(url).endsWith('/characters') ? { names: [name] } : {
          lanlan_name: name, model_type: 'live3d', live3d_sub_type: 'vrm', vrm_path: descriptor.model.path,
        })
      ));
      const host = env.host();
      if (customTransport) {
        host.getAvatarCharacter = async () => value;
        host.listAvatarCharacters = async () => [name];
      }
      const game = await env.game(host);
      try {
        if (length <= 128) {
          assert.deepEqual(await game.avatar.listCharacters(), [name]);
          assert.deepEqual(await game.avatar.getCurrentCharacter(), value);
          assert.deepEqual(await game.avatar.getCharacter(name), value);
        } else {
          await assert.rejects(game.avatar.listCharacters(), { code: 'invalid_response' });
          await assert.rejects(game.avatar.getCurrentCharacter(), { code: 'invalid_response' });
          await assert.rejects(game.avatar.getCharacter(name), { code: 'invalid_request' });
        }
      } finally { game.dispose(); }
      assert.equal(env.timers.size, 0);
    }
  }
  for (const name of ['', '   ', 42, '🐈'.repeat(129)]) {
    for (const customTransport of [false, true]) {
      let calls = 0;
      const malformed = () => { calls++; return { ...descriptor, name }; };
      const names = () => { calls++; return [name]; };
      const env = await environment(() => ({
        mount() {}, dispose() {},
        getCurrentCharacter: malformed, getCharacter: malformed, listCharacters: names,
      }));
      const host = env.host();
      if (customTransport) {
        host.getAvatarCharacter = malformed;
        host.listAvatarCharacters = names;
      }
      const game = await env.game(host);
      try {
        await assert.rejects(game.avatar.getCharacter(name), { code: 'invalid_request' });
        assert.equal(calls, 0, 'invalid caller name reached the host');
        await assert.rejects(game.avatar.getCurrentCharacter(), { code: 'invalid_response' });
        await assert.rejects(game.avatar.getCharacter('Neko'), { code: 'invalid_response' });
        await assert.rejects(game.avatar.listCharacters(), { code: 'invalid_response' });
        assert.equal(game.avatar.pendingQueryCount, 0, 'invalid responses retained query capacity');
      } finally { game.dispose(); }
      assert.equal(env.timers.size, 0);
    }
  }
  const env = await environment(() => ({ mount() {}, dispose() {} }));
  const game = await env.game(env.host());
  assert.deepEqual(await game.avatar.getCurrentCharacter(), descriptor);
  assert.equal(await game.avatar.getCharacter('Missing'), null, 'unknown name fell back to current character');
  assert.deepEqual(await game.avatar.listCharacters(), ['Neko', 'Other']);
  assert.equal(Object.isFrozen(await game.avatar.getCurrentCharacter()), true);
  game.dispose();
  assert.equal(env.timers.size, 0);

  for (const action of ['timeout', 'abort', 'end', 'dispose', 'body', 'sdk', 'reset', 'page-exit']) {
    const gate = deferred(); let entered = 0; const signals = [];
    const hold = (options) => { entered++; signals.push(options.signal); return gate.promise; };
    const e = await environment(() => ({
      mount() {}, dispose() {},
      getCurrentCharacter: hold,
      getCharacter: (_name, options) => hold(options),
      listCharacters: hold,
    }), action === 'body' ? async () => ({ ok: true, status: 200, json: () => gate.promise }) : undefined);
    // A separate provider with only mount uses the built-in HTTP source.
    const h = action === 'body' ? null : e.host();
    if (action === 'sdk') {
      h.getAvatarCharacter = (_name, options) => hold(options);
      h.listAvatarCharacters = options => hold(options);
    }
    let actual = e;
    if (action === 'body') actual = await environment(() => ({ mount() {}, dispose() {} }), async () => ({
      ok: true, status: 200, json: () => { entered++; return gate.promise; },
    }));
    const client = await actual.game(h || actual.host());
    if (['end', 'page-exit'].includes(action)) await client.runtime.start();
    if (action === 'page-exit') client.runtime.configure({ heartbeat: false, outputs: false, pageExit: true });
    const abort = new AbortController();
    const calls = [
      client.avatar.getCurrentCharacter({ signal: abort.signal, timeoutMs: 250 }),
      client.avatar.getCharacter('Neko', { signal: abort.signal, timeoutMs: 250 }),
      client.avatar.listCharacters({ signal: abort.signal, timeoutMs: 250 }),
      client.avatar.getCurrentCharacter({ signal: abort.signal, timeoutMs: 250 }),
    ].map(p => p.then(() => 'success', error => error.code));
    await tick();
    assert.equal(entered, 4, `${action}: query capacity not applied`);
    await assert.rejects(client.avatar.getCurrentCharacter(), { code: 'busy' });
    if (action === 'dispose') client.dispose();
    else if (action === 'abort') abort.abort();
    else if (action === 'end') await client.runtime.end();
    else if (action === 'reset') client.runtime.reset();
    else if (action === 'page-exit') {
      for (const listener of actual.listeners.get('pagehide') || []) listener({ type: 'pagehide' });
      await tick();
    }
    else for (const timer of [...actual.timers.values()]) timer.fn();
    const expected = ['timeout', 'body', 'sdk'].includes(action) ? 'timeout' : ['dispose', 'page-exit'].includes(action) ? 'disposed' : 'cancelled';
    assert.deepEqual(await Promise.all(calls), Array(4).fill(expected));
    assert.equal(actual.timers.size, 0, `${action}: timers leaked`);
    if (!['dispose', 'page-exit'].includes(action)) await assert.rejects(client.avatar.getCurrentCharacter(), { code: action === 'end' ? 'invalid_state' : 'busy' });
    if (action !== 'body') assert(signals.every(s => s.aborted), `${action}: provider did not receive cancellation`);
    gate.resolve(action === 'body' ? { lanlan_name: 'Neko' } : descriptor);
    await tick();
    if (!['end', 'dispose', 'page-exit'].includes(action)) {
      assert.equal((await client.avatar.getCurrentCharacter()).name, 'Neko',
        `${action}: query did not succeed after abandoned work settled`);
      assert.equal(client.avatar.pendingQueryCount, 0, `${action}: completed query retained capacity`);
    }
    client.dispose();
    assert.equal(actual.timers.size, 0);
  }
}

async function hostBodies() {
  for (const action of ['timeout', 'abort', 'dispose', 'success', 'bad-json']) {
    const gate = deferred(); let signal;
    const env = await environment(null, async (_url, options) => {
      signal = options.signal;
      return { ok: false, status: 409, json: async () => {
        const value = await gate.promise;
        if (action === 'bad-json') throw new SyntaxError('invalid JSON');
        return value;
      } };
    });
    const host = env.host({ pendingRequestLimit: 1 });
    const abort = new AbortController();
    let settled = false;
    const waiting = host._request('/body', {}, { signal: abort.signal, timeoutMs: 250 })
      .then(value => { settled = true; return value; }, error => { settled = true; return error; });
    await tick();
    assert.equal(settled, false, 'host retired request after response headers');
    assert.equal(host._pendingRequests.size, 1);
    await assert.rejects(host._request('/body'), { code: 'busy' });
    if (action === 'abort') abort.abort();
    else if (action === 'dispose') host.dispose();
    else if (action === 'timeout') for (const timer of [...env.timers.values()]) timer.fn();
    else gate.resolve({ detail: 'conflict' });
    const result = await waiting;
    if (['success', 'bad-json'].includes(action)) {
      assert.equal(result.ok, false);
      assert.equal(result.status, 409);
      if (action === 'bad-json') {
        await assert.rejects(result.clone().json(), SyntaxError);
        await assert.rejects(result.json(), SyntaxError);
      } else {
        assert.deepEqual(await result.clone().json(), { detail: 'conflict' });
        assert.deepEqual(await result.json(), { detail: 'conflict' });
      }
    } else {
      assert.equal(result.code, action === 'abort' ? 'cancelled' : action === 'dispose' ? 'disposed' : 'timeout');
      assert.equal(signal.aborted, true);
      if (action !== 'dispose') await assert.rejects(host._request('/body'), { code: 'busy' });
    }
    assert.equal(host._pendingRequests.size, 0);
    assert.equal(env.timers.size, 0);
    gate.resolve({ detail: 'late' });
    await tick();
    host.dispose();
  }
  // Native Response identity fields and one-shot body/clone semantics survive buffering.
  const env = await environment(null, async () => new Response('{"value":1}', {
    status: 201, headers: { 'content-type': 'application/json' },
  }));
  const host = env.host();
  const native = await host._request('/body');
  assert.equal(native.status, 201);
  assert.equal(native.bodyUsed, false);
  assert.deepEqual(await native.clone().json(), { value: 1 });
  assert.deepEqual(await native.json(), { value: 1 });
  assert.equal(native.bodyUsed, true);
  host.dispose();
  let networkCancelled = false;
  const slow = await environment(null, async (_url, { signal }) => new Response(new ReadableStream({
    start(controller) {
      signal.addEventListener('abort', () => {
        networkCancelled = true;
        controller.error(new DOMException('aborted', 'AbortError'));
      }, { once: true });
      controller.enqueue(new TextEncoder().encode('{'));
    },
  })));
  const slowHost = slow.host();
  const abort = new AbortController();
  const incomplete = slowHost._request('/body', {}, { signal: abort.signal }).catch(error => error);
  await tick();
  abort.abort();
  assert.equal((await incomplete).code, 'cancelled');
  await tick();
  assert.equal(networkCancelled, true, 'fetch signal was detached before body consumption');
  assert.equal(slowHost._rawRequests.size, 0, 'cancelled native body reader remained resident');
  assert.equal(slow.timers.size, 0);
  slowHost.dispose();
}

const watchdog = setTimeout(() => { throw new Error('Avatar discovery test did not settle'); }, 15000);
(async () => {
  if (!process.argv.includes('--bodies-only')) {
    if (!process.argv.includes('--queries-only')) await factories();
    if (!process.argv.includes('--factories-only')) await queries();
  }
  if (!process.argv.includes('--queries-only') && !process.argv.includes('--factories-only')) await hostBodies();
  console.log('mini-game avatar discovery runtime test passed');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => clearTimeout(watchdog));

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Drain promise continuations without wall-clock sleeps or live microphone access.
async function settle() {
  for (let i = 0; i < 30; i += 1) await Promise.resolve();
}

async function fixture({ voice = true, endOk = true, endThrows = false } = {}) {
  const timers = new Map();
  let timerId = 0;
  const window = {
    console,
    AbortController,
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const source = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  global.window = window;
  vm.runInThisContext(fs.readFileSync(source, 'utf8'), { filename: source });
  let bridge;
  let startResult;
  let generation;
  let active = true;
  const requests = [];
  const transport = {
    logger: {
      log() {}, info() {}, warn() {}, error() {}, reset() {},
      async enable() {}, async enableAfterRouteStart() {}, async flush() {},
    },
    connectGame({ manifest }) {
      return {
        accepted: true, protocolVersion: '1', hostVersion: 'test',
        registration: { mode: 'registered', gameId: manifest.id, version: manifest.version },
        grantedCapabilities: manifest.requiredCapabilities,
      };
    },
    getRuntimeState: () => ({ sessionId: 'example-session', characterName: 'example-character' }),
    resetRuntime() { return this.getRuntimeState(); },
    applyRuntimeState() {},
    start(payload) {
      generation = payload.sdk_route_instance_id;
      startResult = deferred();
      return startResult.promise;
    },
    end() {
      if (endThrows) throw new Error('synchronous end failure');
      return Promise.resolve({ ok: endOk });
    },
    heartbeat: async () => ({ ok: true, active }),
    drain: async () => ({ ok: true, outputs: [] }),
    startVoiceControlBridge(options) { bridge = options; return true; },
    stopVoiceControlBridge() {},
    requestVoiceControl(action, options) {
      const result = deferred();
      requests.push({ action, options, ...result });
      return result.promise; // Ignores abort: the raw query must keep its capacity slot.
    },
    dispose() {},
  };
  const game = await window.NekoMiniGame.connect({
    id: 'example-game', version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', ...(voice ? ['voice-input'] : [])],
  }, { transport, windowImpl: window });
  return {
    game, requests, timers,
    get generation() { return generation; },
    setActive(value) { active = value; },
    state(extra = {}) {
      return { sdk_route_instance_id: generation, available: true, active: false, reason: 'idle', ...extra };
    },
    broadcast(state) { bridge.onState(state); },
    finishStart(value = { ok: true, active: true }) { startResult.resolve(value); },
    failStart(error) { startResult.reject(error); },
  };
}

async function main() {
  const f = await fixture();
  try {
    const states = [];
    f.game.voice.onState((state) => states.push(state));
    const starting = f.game.runtime.start();
    await settle();
    f.broadcast(f.state({ reason: 'early-notification' }));
    assert.equal(states.length, 0, 'unestablished route notification must remain gated');
    assert.equal(f.requests.length, 0, 'must not query before route acceptance');
    f.finishStart();
    await starting;
    await settle();
    assert.equal(f.requests.length, 1, 'SDK must query after route acceptance without game-side query');
    assert.equal(f.requests[0].action, 'query', 'synchronization must not start the microphone');
    assert.equal(f.requests[0].options.sdkRouteInstanceId, f.generation);
    f.requests[0].resolve(f.state());
    await settle();
    assert.equal(states.length, 1);
    assert.equal(states[0].reason, 'idle');
    assert(Object.isFrozen(states[0]));
    const late = [];
    const unsubscribe = f.game.voice.onState((state) => late.push(state));
    assert.equal(late.length, 1, 'late subscribers receive the current snapshot');
    assert.equal(f.requests.length, 1, 'cached state must not trigger redundant queries');
    unsubscribe();
    f.broadcast(f.state({ active: true }));
    f.broadcast(f.state({ sdk_route_instance_id: 'old-generation', active: false }));
    assert.equal(states.length, 2, 'reject stale generations while preserving normal updates');
    assert.equal(late.length, 1, 'unsubscribe must release the listener');

    await f.game.runtime.end();
    const afterEnd = [];
    f.game.voice.onState((state) => afterEnd.push(state));
    assert.equal(afterEnd.length, 0, 'end clears the snapshot');
    assert.equal(f.requests.length, 1, 'inactive subscriptions must not query');
    const nextStart = f.game.runtime.start();
    await settle();
    f.finishStart();
    await nextStart;
    await settle();
    assert.equal(f.requests.length, 2, 'successor route gets a new query');
    f.requests[1].resolve(f.state({ reason: 'successor' }));
    await settle();
    assert.equal(afterEnd[0].reason, 'successor');
  } finally { f.game.dispose(); }

  // Subscription fan-out coalesces into one query, including while it is pending.
  const coalesced = await fixture();
  try {
    const start = coalesced.game.runtime.start();
    await settle();
    coalesced.finishStart();
    await start;
    const seen = [];
    for (let i = 0; i < 10; i += 1) coalesced.game.voice.onState((s) => seen.push(s));
    await settle();
    assert.equal(coalesced.requests.length, 1);
    const reply = coalesced.state();
    coalesced.broadcast(reply);
    coalesced.requests[0].resolve(reply);
    await settle();
    assert.equal(seen.length, 10, 'bridge delivery and returned reply must not double-emit');
    assert.equal(coalesced.timers.size, 0, 'settled query releases its timeout');
  } finally { coalesced.game.dispose(); }

  // A newer broadcast wins over a delayed query response, even within one route.
  const updated = await fixture();
  try {
    const start = updated.game.runtime.start();
    await settle();
    updated.finishStart();
    await start;
    await settle();
    updated.broadcast(updated.state({ active: true }));
    const seen = [];
    updated.game.voice.onState((state) => seen.push(state));
    updated.requests[0].resolve(updated.state({ active: false }));
    await settle();
    assert.equal(seen.length, 1);
    assert.equal(seen[0].active, true, 'query must not roll back a newer broadcast');
  } finally { updated.game.dispose(); }

  for (const boundary of ['end', 'inactive', 'dispose']) {
    const ended = await fixture();
    try {
      const seen = [];
      const errors = [];
      ended.game.voice.onState((state) => seen.push(state));
      ended.game.voice.onError((error) => errors.push(error));
      // pulse() needs heartbeat configuration but no live interval is used here.
      ended.game.runtime.configure({ heartbeat: {} });
      const start = ended.game.runtime.start();
      await settle();
      ended.finishStart();
      await start;
      await settle();
      const pending = ended.requests[0];
      const oldState = ended.state();
      if (boundary === 'end') await ended.game.runtime.end();
      else if (boundary === 'inactive') {
        ended.setActive(false);
        await ended.game.runtime.pulse(true);
      } else ended.game.dispose();
      await settle();
      assert.equal(pending.options.signal.aborted, true, `${boundary} aborts automatic query`);
      assert.equal(ended.timers.size, 0, `${boundary} releases query timeout`);
      pending.resolve(oldState);
      ended.broadcast(oldState);
      await settle();
      assert.equal(seen.length, 0, `${boundary} rejects stale query and bridge results`);
      assert.equal(errors.length, 0, 'expected cleanup cancellation must stay quiet');
    } finally { ended.game.dispose(); }
  }

  // Repeated route replacement cannot accumulate transports that ignore abort.
  const replaced = await fixture();
  try {
    const first = replaced.game.runtime.start();
    await settle(); replaced.finishStart(); await first; await settle();
    const stale = replaced.state({ reason: 'stale' });
    const errors = [];
    replaced.game.voice.onError(error => errors.push(error));
    for (let i = 0; i < 8; i += 1) {
      await replaced.game.runtime.end();
      const next = replaced.game.runtime.start();
      await settle(); replaced.finishStart(); await next; await settle();
      assert.equal(replaced.requests.length, 1, 'cancellation freed an unresolved raw voice query slot');
      assert.equal(replaced.timers.size, 0, 'abandoned query waiter retained its timeout');
    }
    assert.equal(errors.length, 0, 'waiting for a raw query slot must not report busy');
    const seen = [];
    replaced.game.voice.onState((state) => seen.push(state));
    replaced.requests[0].resolve(stale);
    await settle();
    assert.equal(seen.length, 0);
    assert.equal(replaced.requests.length, 2, 'raw settlement must synchronize only the latest active route');
    assert.equal(replaced.requests[1].options.sdkRouteInstanceId, replaced.generation);
    replaced.requests[1].resolve(replaced.state({ reason: 'current' }));
    await settle();
    assert.equal(seen.length, 1);
    assert.equal(seen[0].reason, 'current');
  } finally { replaced.game.dispose(); }

  const timeout = await fixture();
  try {
    const errors = [];
    timeout.game.voice.onError((error) => errors.push(error));
    const start = timeout.game.runtime.start();
    await settle(); timeout.finishStart(); await start; await settle();
    assert.equal(timeout.requests.length, 1, 'start completes without awaiting voice query');
    assert.equal(timeout.timers.size, 1);
    const timer = [...timeout.timers.values()][0];
    assert.equal(timer.ms, 15000, 'automatic query has a fixed timeout');
    timer.fn();
    await settle();
    assert.equal(errors.length, 1);
    assert.equal(errors[0].error.code, 'timeout');
    assert.equal(timeout.requests[0].options.signal.aborted, true);
    assert.equal(timeout.timers.size, 0, 'timeout releases resources even if transport ignores abort');
    await settle();
    assert.equal(timeout.requests.length, 1, 'timeout must not start an unbounded retry loop');
    timeout.requests[0].resolve(timeout.state());
    await settle();
    assert.equal(timeout.requests.length, 1, 'late settlement after timeout must not retry the same route');
    assert.equal(timeout.timers.size, 0);
  } finally { timeout.game.dispose(); }

  // A timed-out waiter is gone before route exit, but its raw query still blocks.
  const timedOutRoute = await fixture();
  try {
    const errors = [];
    timedOutRoute.game.voice.onError(error => errors.push(error));
    const start = timedOutRoute.game.runtime.start();
    await settle(); timedOutRoute.finishStart(); await start; await settle();
    [...timedOutRoute.timers.values()][0].fn();
    await settle();
    assert.equal(errors.length, 1);
    assert.equal(errors[0].error.code, 'timeout');
    await timedOutRoute.game.runtime.end();
    const successor = timedOutRoute.game.runtime.start();
    await settle(); timedOutRoute.finishStart(); await successor; await settle();
    assert.equal(timedOutRoute.requests.length, 1, 'route restart bypassed timed-out raw query capacity');
    timedOutRoute.requests[0].reject(new Error('late timeout rejection'));
    await settle();
    assert.equal(errors.length, 1, 'late rejection reported a second voice error');
    assert.equal(timedOutRoute.requests.length, 2, 'timed-out predecessor left the new route unsynchronized');
    timedOutRoute.requests[1].resolve(timedOutRoute.state());
    await settle();
    assert.equal(timedOutRoute.timers.size, 0);
  } finally { timedOutRoute.game.dispose(); }

  for (const endThrows of [false, true]) {
    const recovering = await fixture({ endOk: false, endThrows });
    try {
      const start = recovering.game.runtime.start();
      await settle(); recovering.finishStart(); await start; await settle();
      const cancelled = recovering.requests[0];
      const errors = [];
      recovering.game.voice.onError((error) => errors.push(error));
      if (endThrows) await assert.rejects(recovering.game.runtime.end(), /synchronous end failure/);
      else await recovering.game.runtime.end();
      await settle();
      assert.equal(cancelled.options.signal.aborted, true);
      assert.equal(recovering.requests.length, 1, 'failed end must wait for the old raw query to retire');
      assert.equal(recovering.timers.size, 0, 'cancelled public waiter must release its timeout');
      const states = [];
      recovering.game.voice.onState((state) => states.push(state));
      if (endThrows) cancelled.reject(new Error('late cancelled failure'));
      else cancelled.resolve(recovering.state({ reason: 'stale cancelled reply' }));
      await settle();
      assert.equal(states.length, 0, 'cancelled query must not publish a recovered snapshot');
      assert.equal(recovering.requests.length, 2, 'failed end resynchronizes the still-owned route');
      assert.equal(errors.length, 0, 'recovery must wait for cancellation cleanup instead of reporting busy');
      assert.equal(recovering.timers.size, 1, 'only the recovery query timeout remains');
      recovering.requests[1].resolve(recovering.state());
      await settle();
      assert.equal(states.length, 1);
      assert.equal(recovering.timers.size, 0, 'recovered query releases its timer');
    } finally { recovering.game.dispose(); }
  }

  const failed = await fixture();
  try {
    const start = failed.game.runtime.start();
    const rejected = assert.rejects(start);
    await settle(); failed.failStart(new Error('offline')); await rejected; await settle();
    assert.equal(failed.requests.length, 0, 'failed start must not query an unresolved route');
  } finally { failed.game.dispose(); }

  const immediate = await fixture();
  const immediateStart = immediate.game.runtime.start();
  await settle(); immediate.finishStart(); await immediateStart;
  immediate.game.dispose();
  await settle();
  assert.equal(immediate.timers.size, 0, 'immediate disposal releases queued synchronization');
  for (const request of immediate.requests) assert.equal(request.options.signal.aborted, true);

  for (const mode of ['inactive', 'rejected', 'no-voice']) {
    const idle = await fixture({ voice: mode !== 'no-voice' });
    try {
      const start = idle.game.runtime.start();
      await settle();
      idle.finishStart(mode === 'inactive' ? { ok: true, active: false }
        : mode === 'rejected' ? { ok: false } : { ok: true, active: true });
      await start; await settle();
      assert.equal(idle.requests.length, 0, `${mode} must not send a voice query`);
    } finally { idle.game.dispose(); }
  }
  console.log('Mini-game voice state runtime regression passed');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

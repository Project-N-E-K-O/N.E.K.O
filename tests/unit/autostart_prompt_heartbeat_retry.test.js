const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const sharedScript = fs.readFileSync(path.join(root, 'static/app/app-prompt-shared.js'), 'utf8');
const autostartScript = fs.readFileSync(path.join(root, 'static/app/app-autostart-prompt.js'), 'utf8');

function createFakeClock() {
  let nowMs = 1000000;
  let nextTimerId = 1;
  const timers = new Map();

  return {
    now: () => nowMs,
    advanceBy(ms) {
      nowMs += ms;
    },
    setTimeout(fn, delay) {
      const id = nextTimerId++;
      timers.set(id, { fn, delay, type: 'timeout', at: nowMs + (Number(delay) || 0) });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    setInterval(fn, delay) {
      const id = nextTimerId++;
      timers.set(id, { fn, delay, type: 'interval', at: nowMs + (Number(delay) || 0) });
      return id;
    },
    clearInterval(id) {
      timers.delete(id);
    },
    getTimers(type) {
      return Array.from(timers.entries())
        .filter(([, entry]) => !type || entry.type === type)
        .map(([id, entry]) => ({ id, ...entry }));
    },
    async runNextTimeout() {
      const timeouts = this.getTimers('timeout').sort((a, b) => a.at - b.at);
      assert.ok(timeouts.length > 0, 'expected a scheduled timeout');
      const next = timeouts[0];
      timers.delete(next.id);
      if (next.at > nowMs) {
        nowMs = next.at;
      }
      await next.fn();
      await flushMicrotasks();
    },
    async runInterval() {
      const intervals = this.getTimers('interval');
      assert.ok(intervals.length > 0, 'expected a scheduled interval');
      nowMs += Number(intervals[0].delay) || 0;
      await intervals[0].fn();
      await flushMicrotasks();
    },
  };
}

async function flushMicrotasks(rounds = 40) {
  for (let i = 0; i < rounds; i += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => setImmediate(resolve));
  for (let i = 0; i < rounds; i += 1) {
    await Promise.resolve();
  }
}

function loadAutostartHarness(heartbeatResponder) {
  const clock = createFakeClock();
  const windowListeners = {};
  const documentListeners = {};
  const heartbeatRequests = [];
  let tokenCounter = 0;

  class FakeElement {}

  const document = {
    visibilityState: 'visible',
    hidden: false,
    hasFocus: () => true,
    addEventListener(type, handler) {
      (documentListeners[type] = documentListeners[type] || []).push(handler);
    },
    removeEventListener(type, handler) {
      documentListeners[type] = (documentListeners[type] || []).filter((fn) => fn !== handler);
    },
    querySelector() {
      return null;
    },
  };

  const window = {
    crypto: {
      randomUUID() {
        tokenCounter += 1;
        return `hb-token-${tokenCounter}`;
      },
    },
    nekoAutostartProvider: {
      async getStatus() {
        return {
          ok: true,
          supported: true,
          enabled: false,
          authoritative: true,
          provider: 'neko-pc',
          mechanism: 'test',
        };
      },
      async enable() {
        return { ok: true, enabled: true };
      },
    },
    addEventListener(type, handler) {
      (windowListeners[type] = windowListeners[type] || []).push(handler);
    },
    removeEventListener(type, handler) {
      windowListeners[type] = (windowListeners[type] || []).filter((fn) => fn !== handler);
    },
    dispatchEvent(event) {
      const type = event && event.type ? event.type : String(event);
      for (const handler of windowListeners[type] || []) {
        handler(event);
      }
      return true;
    },
  };
  window.window = window;

  async function fetch(url, options) {
    if (String(url).includes('/api/autostart-prompt/state')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          state: {
            status: 'observing',
            autostart_enabled: false,
            deferred_until: 0,
            can_never_remind: true,
          },
        }),
      };
    }
    if (String(url).includes('/api/autostart-prompt/heartbeat')) {
      const payload = JSON.parse(options.body);
      heartbeatRequests.push(payload);
      return heartbeatResponder(payload, heartbeatRequests.length);
    }
    throw new Error(`Unexpected fetch URL: ${url}`);
  }

  const FakeDate = class extends Date {
    constructor(...args) {
      if (args.length === 0) {
        super(clock.now());
      } else {
        super(...args);
      }
    }
    static now() {
      return clock.now();
    }
  };

  const context = vm.createContext({
    window,
    document,
    Element: FakeElement,
    fetch,
    Date: FakeDate,
    setTimeout: (fn, delay) => clock.setTimeout(fn, delay),
    clearTimeout: (id) => clock.clearTimeout(id),
    setInterval: (fn, delay) => clock.setInterval(fn, delay),
    clearInterval: (id) => clock.clearInterval(id),
    console: {
      log() {},
      warn() {},
      error() {},
    },
  });

  vm.runInContext(sharedScript, context);
  vm.runInContext(autostartScript, context);
  window.appAutostartPrompt.init();

  return {
    clock,
    window,
    heartbeatRequests,
    async settleInit() {
      await flushMicrotasks();
    },
  };
}

test('F0588: failed heartbeat preserves exact batch and heartbeat_token across retry and clears on success', async () => {
  let failNext = true;
  const harness = loadAutostartHarness(async (payload, attempt) => {
    if (attempt === 1) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, should_prompt: false, state: { status: 'observing' } }),
      };
    }
    if (failNext) {
      failNext = false;
      throw new Error('network timeout');
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, should_prompt: false, state: { status: 'observing' } }),
    };
  });

  await harness.settleInit();
  assert.strictEqual(harness.heartbeatRequests.length, 1);

  // Accumulate foreground ms + chat turn after gate settled.
  harness.clock.advanceBy(4000);
  harness.window.dispatchEvent({ type: 'neko:user-content-sent' });
  await harness.clock.runNextTimeout();

  assert.strictEqual(harness.heartbeatRequests.length, 2);
  const failedAttempt = harness.heartbeatRequests[1];
  assert.strictEqual(failedAttempt.foreground_ms_delta, 5200);
  assert.strictEqual(failedAttempt.chat_turns_delta, 1);
  assert.ok(failedAttempt.heartbeat_token, 'expected heartbeat_token on delta batch');

  // Trigger retry via periodic interval without adding new deltas yet.
  await harness.clock.runInterval();
  assert.strictEqual(harness.heartbeatRequests.length, 3);
  const retryAttempt = harness.heartbeatRequests[2];
  assert.strictEqual(retryAttempt.heartbeat_token, failedAttempt.heartbeat_token);
  assert.strictEqual(retryAttempt.foreground_ms_delta, failedAttempt.foreground_ms_delta);
  assert.strictEqual(retryAttempt.chat_turns_delta, failedAttempt.chat_turns_delta);

  // After retry succeeds, new deltas get a fresh heartbeat_token and only new deltas.
  harness.clock.advanceBy(2500);
  harness.window.dispatchEvent({ type: 'neko:voice-session-started' });
  await harness.clock.runNextTimeout();

  assert.strictEqual(harness.heartbeatRequests.length, 4);
  const nextBatch = harness.heartbeatRequests[3];
  assert.notStrictEqual(nextBatch.heartbeat_token, failedAttempt.heartbeat_token);
  assert.strictEqual(nextBatch.chat_turns_delta, 0);
  assert.strictEqual(nextBatch.voice_sessions_delta, 1);
});

test('F0588: new deltas arriving after a failed heartbeat do not mutate the retry batch and flush in follow-up', async () => {
  let shouldFail = true;
  const harness = loadAutostartHarness(async (payload, attempt) => {
    if (attempt === 1) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, should_prompt: false, state: { status: 'observing' } }),
      };
    }
    if (shouldFail) {
      shouldFail = false;
      return {
        ok: false,
        status: 503,
        json: async () => ({ detail: 'upstream unavailable' }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, should_prompt: false, state: { status: 'observing' } }),
    };
  });

  await harness.settleInit();

  // Attempt 2: 1000ms foreground + 1 chat turn -> fails with 503.
  harness.clock.advanceBy(1000);
  harness.window.dispatchEvent({ type: 'neko:user-content-sent' });
  await harness.clock.runNextTimeout();

  assert.strictEqual(harness.heartbeatRequests.length, 2);
  const firstFailed = harness.heartbeatRequests[1];
  assert.strictEqual(firstFailed.foreground_ms_delta, 2200);
  assert.strictEqual(firstFailed.chat_turns_delta, 1);

  // While waiting to retry, user sends another message and spends 2000ms in foreground.
  harness.clock.advanceBy(2000);
  harness.window.dispatchEvent({ type: 'neko:user-content-sent' });
  await harness.clock.runNextTimeout();

  // Retry must send the EXACT snapshot of Attempt 2 (same token, 2200ms, 1 chat turn).
  assert.strictEqual(harness.heartbeatRequests.length, 3);
  const retryAttempt = harness.heartbeatRequests[2];
  assert.strictEqual(retryAttempt.heartbeat_token, firstFailed.heartbeat_token);
  assert.strictEqual(retryAttempt.foreground_ms_delta, 2200);
  assert.strictEqual(retryAttempt.chat_turns_delta, 1);

  // Because new deltas arrived during the retry window, a fast follow-up timeout is scheduled.
  assert.ok(
    harness.clock.getTimers('timeout').length > 0,
    'expected follow-up fast heartbeat after retry succeeded with pending deltas'
  );
  await harness.clock.runNextTimeout();

  assert.strictEqual(harness.heartbeatRequests.length, 4);
  const followUp = harness.heartbeatRequests[3];
  assert.notStrictEqual(followUp.heartbeat_token, firstFailed.heartbeat_token);
  assert.strictEqual(followUp.chat_turns_delta, 1);
  assert.ok(followUp.foreground_ms_delta >= 2000);
});

test('F0588: zero-delta heartbeat omits heartbeat_token and does not create a sticky retry batch', async () => {
  let failZero = true;
  const harness = loadAutostartHarness(async (payload, attempt) => {
    if (attempt === 1 && failZero) {
      failZero = false;
      throw new Error('offline');
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, should_prompt: false, state: { status: 'observing' } }),
    };
  });

  await harness.settleInit();
  assert.strictEqual(harness.heartbeatRequests.length, 1);
  assert.strictEqual(harness.heartbeatRequests[0].foreground_ms_delta, 0);
  assert.strictEqual(harness.heartbeatRequests[0].heartbeat_token, undefined);

  // Next heartbeat with real deltas must not be stuck on a zero-delta retry batch.
  harness.clock.advanceBy(3000);
  harness.window.dispatchEvent({ type: 'neko:user-content-sent' });
  await harness.clock.runNextTimeout();

  assert.strictEqual(harness.heartbeatRequests.length, 2);
  assert.strictEqual(harness.heartbeatRequests[1].foreground_ms_delta, 4200);
  assert.strictEqual(harness.heartbeatRequests[1].chat_turns_delta, 1);
  assert.ok(harness.heartbeatRequests[1].heartbeat_token);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'static/app/app-cat-mind.js'), 'utf8');

class EventTargetLike {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  dispatchEvent(event) {
    for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail || {};
  }
}

function cloneMath(random) {
  const result = {};
  for (const key of Object.getOwnPropertyNames(Math)) {
    Object.defineProperty(result, key, Object.getOwnPropertyDescriptor(Math, key));
  }
  Object.defineProperty(result, 'random', { value: random, configurable: true });
  return result;
}

function createRuntime(randomValues = [], semanticActionId = '', options = {}) {
  let now = 1000;
  let nextTimerId = 1;
  let randomCalls = 0;
  const timers = new Map();
  const requests = [];
  const providerCalls = [];
  const win = new EventTargetLike();
  win.setTimeout = (callback, delayMs = 0) => {
    const id = nextTimerId++;
    timers.set(id, { callback, dueAt: now + Math.max(0, Number(delayMs) || 0) });
    return id;
  };
  win.clearTimeout = (id) => timers.delete(id);
  win.setInterval = () => 1;
  win.clearInterval = () => {};
  const random = () => {
    const value = randomValues[randomCalls];
    randomCalls += 1;
    return value === undefined ? 0.99 : value;
  };
  const context = {
    window: win,
    CustomEvent: CustomEventLike,
    Date: { now: () => now },
    Math: cloneMath(random),
    console,
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  if (Number.isFinite(options.autoLastInteractionAt)) {
    win.nekoAutoGoodbye = {
      getState: () => ({ lastInteractionAt: options.autoLastInteractionAt }),
    };
  }
  let sadAllowed = options.sadAllowed === true;
  win.NekoCatMindActionProviders = {
    getRuntimeGateSnapshot() {
      return {
        returnPending: false,
        dragPending: false,
        dragging: false,
        transitionActive: false,
        activeIndependentAction: false,
        returnBallVisible: true,
        validCatRuntime: true,
        chatSurfaceDragging: false,
        yarnDragActive: false,
        yarnSettling: false,
        edgePeekActive: false,
        cat1PositionPresentationBusy: false,
      };
    },
    dryRun(actionId) {
      providerCalls.push(actionId);
      const allowed = actionId === 'cat1_idle_random' ||
        actionId === semanticActionId ||
        (actionId === 'cat1_inactive_sad' && sadAllowed);
      return { allowed, reason: allowed ? 'allowed' : 'test_disabled' };
    },
  };
  win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

  const flush = () => {
    let remaining = 500;
    while (remaining-- > 0) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.dueAt <= now)
        .sort((left, right) => left[1].dueAt - right[1].dueAt || left[0] - right[0])[0];
      if (!due) break;
      timers.delete(due[0]);
      due[1].callback();
    }
    assert.ok(remaining > 0, 'scheduler must stay bounded');
  };
  const observe = (type, detail = {}, source = 'test') => {
    now += 1;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: { type, source, tier: 'cat1', timestamp: now, detail },
    }));
    flush();
  };
  const observeAt = (type, timestamp, detail = {}, source = 'test') => {
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: { type, source, tier: 'cat1', timestamp, detail },
    }));
    flush();
  };
  const tick = (elapsedMs = 30 * 1000) => {
    now += elapsedMs;
    observe('cat_elapsed', { elapsedMs }, 'cat-mind-clock');
  };
  now += Math.max(0, Number(options.entryDelayMs) || 0);
  win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: {
      active: true,
      source: options.autoLastInteractionAt == null ? 'manual-goodbye' : 'auto-goodbye',
      autoGoodbye: options.autoLastInteractionAt != null,
      timestamp: now,
    },
  }));
  flush();
  providerCalls.length = 0;

  return {
    win,
    requests,
    providerCalls,
    observe,
    observeAt,
    tick,
    flush,
    randomCalls: () => randomCalls,
    now: () => now,
    advance(ms) { now += ms; flush(); },
    setSadAllowed(value) { sadAllowed = value === true; },
    globalActivity(source = 'pointerdown') {
      win.dispatchEvent(new CustomEventLike('neko:auto-goodbye:state-change', {
        detail: {
          type: 'user-interaction',
          source,
          timestamp: now,
          preserveVisualTierCountdown: true,
        },
      }));
      flush();
    },
  };
}

function startRequest(runtime, request, runId) {
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId,
    timestamp: runtime.now(),
  }), true);
  assert.equal(runtime.win.nekoCatMind.getState().actionCooldowns.cat1_idle_random, undefined,
    'accepted alone must not start the cooldown');
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'started',
    runId,
    timestamp: runtime.now(),
  }), true);
}

function finishRequest(runtime, request, runId) {
  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {
    detail: {
      actionId: request.actionId,
      result: 'done',
      reason: 'cat1-idle-random-finished',
      source: 'cat_mind',
      tier: 'cat1',
      timestamp: runtime.now(),
      detail: { requestId: request.requestId, runId, actionKey: 'happy' },
    },
  }));
  runtime.flush();
}

test('idle random policy owns 20% of an eligible CAT1 clock tick before semantic candidates', () => {
  const runtime = createRuntime([0.199999], 'cat1_social_ping');
  runtime.tick(15 * 60 * 1000);

  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_idle_random');
  assert.deepEqual(runtime.providerCalls, ['cat1_inactive_sad', 'cat1_idle_random'],
    'after the deterministic inactivity action is unavailable, a successful 20% roll owns the slot before semantic dry-runs');
  assert.equal(runtime.randomCalls(), 1);
});

test('20% boundary miss leaves the same clock tick to existing Cat Mind candidates', () => {
  const runtime = createRuntime([0.20], 'cat1_social_ping');
  runtime.tick(15 * 60 * 1000);

  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_social_ping');
  assert.equal(runtime.providerCalls[0], 'cat1_inactive_sad');
  assert.equal(runtime.providerCalls[1], 'cat1_idle_random');
  assert.ok(runtime.providerCalls.includes('cat1_social_ping'));
  assert.equal(runtime.randomCalls(), 1);
});

test('five-minute hard cooldown starts only at started and blocks both provider and chance rolls', () => {
  const runtime = createRuntime([0.1, 0.1]);
  runtime.tick();
  const first = runtime.requests[0];
  assert.equal(first.actionId, 'cat1_idle_random');
  startRequest(runtime, first, 'idle-random-run-1');
  assert.equal(
    runtime.win.nekoCatMind.getState().actionCooldowns.cat1_idle_random.fullCooldownMs,
    5 * 60 * 1000,
  );
  finishRequest(runtime, first, 'idle-random-run-1');

  const idleProviderCallsAfterStart = runtime.providerCalls.filter(
    (actionId) => actionId === 'cat1_idle_random',
  ).length;
  const randomCallsAfterStart = runtime.randomCalls();
  runtime.tick(4 * 60 * 1000);
  assert.equal(runtime.requests.length, 1);
  assert.equal(
    runtime.providerCalls.filter((actionId) => actionId === 'cat1_idle_random').length,
    idleProviderCallsAfterStart,
    'hard cooldown is checked before random-library provider work',
  );
  assert.equal(runtime.randomCalls(), randomCallsAfterStart,
    'hard cooldown does not consume a chance roll');

  runtime.tick(60 * 1000);
  assert.equal(runtime.requests.length, 2);
  assert.equal(runtime.requests[1].actionId, 'cat1_idle_random');
  assert.equal(runtime.randomCalls(), randomCallsAfterStart + 1);
});

test('non-clock observations never roll the idle random library', () => {
  const runtime = createRuntime([0]);
  runtime.observe('cat_hover_reaction');
  assert.equal(runtime.requests.length, 0);
  assert.equal(runtime.randomCalls(), 0);
  assert.equal(runtime.providerCalls.includes('cat1_idle_random'), false);
});

test('public contract exposes the exact random policy', () => {
  const runtime = createRuntime();
  assert.equal(runtime.win.NekoCatMindContract.ACTION_IDS.CAT1_IDLE_RANDOM, 'cat1_idle_random');
  assert.equal(runtime.win.NekoCatMindContract.CAT1_IDLE_RANDOM_POLICY.tickChance, 0.20);
  assert.equal(runtime.win.NekoCatMindContract.CAT1_IDLE_RANDOM_POLICY.cooldownMs, 5 * 60 * 1000);
  assert.equal(runtime.win.NekoCatMindContract.ACTION_IDS.CAT1_INACTIVE_SAD, 'cat1_inactive_sad');
  assert.equal(runtime.win.NekoCatMindContract.CAT1_INACTIVE_SAD_POLICY.inactivityMs, 13 * 60 * 1000);
});

test('inactive sad uses the global idle anchor, owns the 13-minute CAT1 tick, and resets on standard activity', () => {
  const baseline = 1000;
  const runtime = createRuntime([], '', {
    autoLastInteractionAt: baseline,
    entryDelayMs: 10 * 60 * 1000,
    sadAllowed: true,
  });

  assert.equal(runtime.win.nekoCatMind.getState().clock.lastUserInteractionAt, baseline,
    'automatic CAT1 keeps the original global inactivity anchor');
  runtime.tick(3 * 60 * 1000);
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_inactive_sad');
  assert.deepEqual(runtime.providerCalls, ['cat1_inactive_sad']);
  assert.equal(runtime.randomCalls(), 0, 'the deterministic sad action does not consume the random-library roll');

  const request = runtime.requests[0];
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId: 'inactive-sad-run-1',
    timestamp: runtime.now(),
  }), true);
  assert.equal(runtime.win.nekoCatMind.getState().cat1InactiveSadStartedForIdleEpisode, false,
    'accepted is not proof that the animation started');
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'started',
    runId: 'inactive-sad-run-1',
    timestamp: runtime.now(),
  }), true);
  assert.equal(runtime.win.nekoCatMind.getState().cat1InactiveSadStartedForIdleEpisode, true);

  runtime.globalActivity('keydown');
  const state = runtime.win.nekoCatMind.getState();
  assert.equal(state.cat1InactiveSadStartedForIdleEpisode, false);
  assert.equal(state.clock.lastUserInteractionAt, runtime.now());
  assert.equal(runtime.requests.length, 1, 'activity resets the policy without creating another action request');
});

test('stale interaction observations cannot rewind or rearm the inactive-sad episode', () => {
  const baseline = 1000;
  const runtime = createRuntime([], '', {
    autoLastInteractionAt: baseline,
    entryDelayMs: 10 * 60 * 1000,
    sadAllowed: true,
  });
  runtime.tick(3 * 60 * 1000);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'inactive-sad-stale-observation-run');

  runtime.observeAt('cat_hover_reaction', baseline - 1, {}, 'stale-hover');
  runtime.observeAt('cat_hover_reaction', baseline, {}, 'equal-hover');
  let state = runtime.win.nekoCatMind.getState();
  assert.equal(state.clock.lastUserInteractionAt, baseline);
  assert.equal(state.cat1InactiveSadStartedForIdleEpisode, true);

  runtime.observeAt('cat_hover_reaction', runtime.now() + 1, {}, 'fresh-hover');
  state = runtime.win.nekoCatMind.getState();
  assert.equal(state.clock.lastUserInteractionAt, runtime.now() + 1);
  assert.equal(state.cat1InactiveSadStartedForIdleEpisode, false);
});

test('inactive sad retries after provider rejection and only becomes one-shot after started', () => {
  const runtime = createRuntime([], '', {
    autoLastInteractionAt: 1000,
    entryDelayMs: 10 * 60 * 1000,
  });
  runtime.tick(3 * 60 * 1000);
  assert.equal(runtime.requests.length, 0);
  assert.equal(runtime.providerCalls.includes('cat1_inactive_sad'), true);
  assert.equal(runtime.win.nekoCatMind.getState().cat1InactiveSadStartedForIdleEpisode, false);

  runtime.setSadAllowed(true);
  runtime.tick();
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_inactive_sad');
});

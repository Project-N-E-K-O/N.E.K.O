const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const catMindSource = fs.readFileSync(
  path.join(projectRoot, 'static', 'app', 'app-cat-mind.js'),
  'utf8'
);

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  dispatchEvent(event) {
    for (const handler of (this.listeners.get(event.type) || []).slice()) {
      handler.call(this, event);
    }
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail || {};
  }
}

function createRuntime(allowedActionId, options = {}) {
  let now = 1000;
  const timers = [];
  const requests = [];
  const gates = {
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
  };
  let providerReady = options.providerReady !== false;
  let dryRunCalls = 0;
  const win = new EventTargetLike();
  win.setTimeout = (callback) => {
    timers.push(callback);
    return timers.length;
  };
  win.setInterval = () => 1;
  win.clearInterval = () => {};
  const context = {
    window: win,
    CustomEvent: CustomEventLike,
    Date: { now: () => now },
    console,
  };
  vm.createContext(context);
  vm.runInContext(catMindSource, context);
  win.NekoCatMindActionProviders = {
    getRuntimeGateSnapshot() {
      return { ...gates };
    },
    dryRun(actionId) {
      dryRunCalls += 1;
      const allowed = actionId === allowedActionId && providerReady;
      return {
        allowed,
        reason: allowed ? 'allowed' : (actionId === allowedActionId ? 'provider_not_ready' : 'test_disabled'),
      };
    },
  };
  win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

  const flush = () => {
    let remaining = 500;
    while (timers.length && remaining-- > 0) timers.shift()();
    assert.ok(remaining > 0, 'scheduler must remain asynchronous and bounded');
  };
  const observe = (type, detail = {}, tier = 'cat1') => {
    now += 1;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: { type, source: 'scheduler-test', tier, timestamp: now, detail },
    }));
    flush();
  };
  const enter = () => {
    win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {
      detail: { source: 'manual-goodbye', timestamp: now },
    }));
    flush();
  };
  const advanceNeed = (minutes = 15) => {
    now += minutes * 60 * 1000;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: {
        type: 'cat_elapsed',
        source: 'cat-mind-clock',
        tier: 'cat1',
        timestamp: now,
        detail: { elapsedMs: minutes * 60 * 1000 },
      },
    }));
    flush();
  };
  return {
    win,
    gates,
    requests,
    flush,
    observe,
    enter,
    advanceNeed,
    now: () => now,
    setNow: (value) => { now = value; },
    setProviderReady: (value) => { providerReady = value; },
    dryRunCalls: () => dryRunCalls,
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
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'started',
    runId,
    timestamp: runtime.now(),
  }), true);
}

function reportResult(runtime, request, runId, result, reason, detail = {}) {
  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {
    detail: {
      actionId: request.actionId,
      result,
      reason,
      source: 'cat_mind',
      tier: 'cat1',
      timestamp: runtime.now(),
      detail: { requestId: request.requestId, runId, ...detail },
    },
  }));
  runtime.flush();
}

test('active user observations coalesce into one evaluation after terminal settle', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'social-active-run');

  const dryRunsBeforeInput = runtime.dryRunCalls();
  for (let index = 0; index < 20; index += 1) {
    runtime.observe('cat_hover_reaction');
  }
  assert.equal(runtime.dryRunCalls(), dryRunsBeforeInput, 'active runner blocks selector dry-runs');

  reportResult(runtime, request, 'social-active-run', 'done', 'social-finished');
  assert.equal(
    runtime.dryRunCalls() - dryRunsBeforeInput,
    4,
    'twenty inputs coalesce into one CAT1 selector pass after post-settle'
  );
  assert.ok(
    runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.triggerTypes.includes('cat_hover_reaction')
  );
});

test('provider-ready presentation wakes retained yarn intent without waiting for clock', () => {
  const runtime = createRuntime('cat1_play_yarn', { providerReady: false });
  runtime.enter();
  runtime.observe('chat_yarn_drag_completed', {
    userInitiated: true,
    startedFarFromCat: true,
    endedNearCat: true,
    startDistanceToCatPx: 320,
    endDistanceToCatPx: 20,
    directApproachDistancePx: 300,
    pathDistancePx: 310,
    movementThresholdPx: 24,
  });
  assert.equal(runtime.requests.length, 0);
  assert.ok(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);

  runtime.setProviderReady(true);
  runtime.observe('cat1_stretch_done_near_chat', { reason: 'stretch-settled' });
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_play_yarn');
  assert.ok(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);

  startRequest(runtime, runtime.requests[0], 'provider-ready-yarn');
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn, undefined);
});

test('interrupted small move settles its physical facts once and interruption metadata adds no needs', () => {
  const runtime = createRuntime('cat1_small_move');
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'small-move-interrupted');
  const before = runtime.win.nekoCatMind.getState().fields;

  reportResult(runtime, request, 'small-move-interrupted', 'interrupted', 'return-ball-drag-active', {
    activityId: 'small-move-interrupted',
    pathDistancePx: 80,
    durationMs: 1100,
  });
  const after = runtime.win.nekoCatMind.getState().fields;
  assert.ok(Math.abs(after.appetite - (before.appetite + 0.02)) < 1e-9);
  assert.ok(Math.abs(after.energy - (before.energy - 0.0225)) < 1e-9);
  assert.ok(Math.abs(after.sleepiness - (before.sleepiness + 0.01)) < 1e-9);
  assert.equal(after.social_need, before.social_need);
  assert.equal(after.stimulation_need, before.stimulation_need);
  const recentTypes = runtime.win.nekoCatMind.getDebugSnapshot().recentEvents.map((event) => event.type);
  assert.ok(recentTypes.includes('small_move_cancelled'));
  assert.ok(recentTypes.includes('action_interrupted_by_drag'));
});

test('done before started releases the request without cooldown or completion feedback', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed();
  const request = runtime.requests[0];
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId: 'done-before-started',
    timestamp: runtime.now(),
  }), true);
  const before = runtime.win.nekoCatMind.getState().fields;

  reportResult(runtime, request, 'done-before-started', 'done', 'protocol-bad-order');
  const state = runtime.win.nekoCatMind.getState();
  assert.deepEqual(state.fields, before);
  assert.equal(state.actionCooldowns.cat1_social_ping, undefined);
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().returnEpisode.preview, null);
  assert.equal(
    runtime.win.nekoCatMind.getDebugSnapshot().scheduler.lastProtocolFailure.type,
    'result_before_started'
  );
});

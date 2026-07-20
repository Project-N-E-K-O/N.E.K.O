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

function createProbe() {
  let now = 1000;
  const timers = [];
  const requests = [];
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
      };
    },
    dryRun() {
      return { allowed: false, reason: 'probe_provider_disabled' };
    },
  };
  win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

  const flush = () => {
    let remaining = 200;
    while (timers.length && remaining-- > 0) timers.shift()();
    assert.ok(remaining > 0, 'probe scheduler must stay asynchronous and bounded');
  };
  const observe = (type, detail = {}) => {
    now += 1;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: { type, source: 'interaction-consequence-test', tier: 'cat1', timestamp: now, detail },
    }));
    flush();
  };
  const dispatchReturnBallMove = (detail = {}) => {
    now += 1;
    win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {
      detail: { ...detail, timestamp: Number(detail.timestamp) || now },
    }));
    flush();
  };
  const snapshot = () => ({
    fields: { ...win.nekoCatMind.getState().fields },
    scores: Object.fromEntries(
      win.nekoCatMind.getDebugSnapshot().actionScores
        .filter((item) => item.actionId.startsWith('cat1_'))
        .map((item) => [item.actionId, item.score])
    ),
    intents: JSON.parse(JSON.stringify(
      win.nekoCatMind.getDebugSnapshot().actionIntentEvidence
    )),
  });

  win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: true, source: 'manual-goodbye', timestamp: now },
  }));
  flush();
  const recentEvents = () => JSON.parse(JSON.stringify(win.nekoCatMind.getRecentEvents()));
  const advanceTime = (milliseconds) => { now += milliseconds; };
  return { observe, dispatchReturnBallMove, snapshot, recentEvents, requests, advanceTime };
}

function runScenario(events) {
  const runtime = createProbe();
  const before = runtime.snapshot();
  for (const event of events) runtime.observe(event.type, event.detail || {});
  return { runtime, before, after: runtime.snapshot() };
}

function scoreDelta(result, actionId) {
  return result.after.scores[actionId] - result.before.scores[actionId];
}

function assertClose(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} !== ${expected}`);
}

function mergeInteractionDose(current, dose) {
  return current + dose * (1 - current) * (1 + 0.8 * current);
}

test('hover, ordinary drag, and rapid drag produce distinct scored consequences', () => {
  const hover = runScenario([{ type: 'cat_hover_reaction' }]);
  const ordinary = runScenario([
    { type: 'drag_start' },
    { type: 'drag_end', detail: { activityId: 'ordinary-drag', pathDistancePx: 160, durationMs: 2200 } },
  ]);
  const rapid = runScenario([
    { type: 'drag_start' },
    { type: 'rapid_drag' },
    { type: 'drag_end', detail: { activityId: 'rapid-drag', pathDistancePx: 160, durationMs: 2200 } },
  ]);

  assert.equal(hover.after.fields.appetite, hover.before.fields.appetite);
  assert.equal(hover.after.fields.sleepiness, hover.before.fields.sleepiness);
  assert.ok(hover.after.fields.social_need > hover.before.fields.social_need);
  assert.ok(hover.after.fields.stimulation_need > hover.before.fields.stimulation_need);
  assert.ok(scoreDelta(hover, 'cat1_play_yarn') > scoreDelta(hover, 'cat1_small_move'));

  assert.ok(ordinary.after.fields.appetite > ordinary.before.fields.appetite);
  assert.ok(ordinary.after.fields.sleepiness > ordinary.before.fields.sleepiness);
  assert.ok(ordinary.after.fields.energy < ordinary.before.fields.energy);
  assert.ok(
    ordinary.after.fields.stimulation_need - ordinary.before.fields.stimulation_need >
      hover.after.fields.stimulation_need - hover.before.fields.stimulation_need
  );
  assert.ok(scoreDelta(ordinary, 'cat1_play_yarn') > scoreDelta(hover, 'cat1_play_yarn'));
  assert.ok(scoreDelta(ordinary, 'cat1_play_yarn') > scoreDelta(ordinary, 'cat1_small_move'));
  assert.ok(scoreDelta(ordinary, 'cat1_small_move') > scoreDelta(ordinary, 'cat1_social_ping'));

  assertClose(rapid.after.fields.appetite, ordinary.after.fields.appetite);
  assertClose(rapid.after.fields.sleepiness, ordinary.after.fields.sleepiness);
  assertClose(rapid.after.fields.energy, ordinary.after.fields.energy);
  assert.ok(rapid.after.fields.social_need > ordinary.after.fields.social_need);
  assert.ok(rapid.after.fields.stimulation_need > ordinary.after.fields.stimulation_need);
  assert.ok(scoreDelta(rapid, 'cat1_play_yarn') > scoreDelta(ordinary, 'cat1_play_yarn'));
  assert.ok(scoreDelta(rapid, 'cat1_small_move') > scoreDelta(ordinary, 'cat1_small_move'));

  for (const result of [hover, ordinary, rapid]) {
    assert.deepEqual(result.after.intents, {}, 'generic interaction intensity must stay in the five needs');
    assert.equal(result.runtime.requests.length, 0, 'disabled providers must prevent an action request');
  }
});

test('a strong yarn offer changes only play intent while bubble pop satisfies contact', () => {
  const yarn = runScenario([{
    type: 'chat_yarn_drag_completed',
    detail: {
      userInitiated: true,
      startedFarFromCat: true,
      endedNearCat: true,
      startDistanceToCatPx: 320,
      endDistanceToCatPx: 20,
      directApproachDistancePx: 300,
      pathDistancePx: 310,
      movementThresholdPx: 24,
    },
  }]);
  for (const field of Object.keys(yarn.before.fields)) {
    assert.equal(yarn.after.fields[field], yarn.before.fields[field]);
  }
  assert.ok(scoreDelta(yarn, 'cat1_play_yarn') > 81);
  for (const actionId of ['cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move']) {
    assert.equal(scoreDelta(yarn, actionId), 0);
  }
  assert.equal(yarn.after.intents.cat1_play_yarn.reason, 'yarn_offer_far_to_near');
  assert.equal(yarn.runtime.requests.length, 0, 'intent must not bypass a rejected provider');

  const hoverOnly = runScenario([{ type: 'cat_hover_reaction' }]);
  const popped = runScenario([
    { type: 'cat_hover_reaction' },
    { type: 'thought_bubble_pop' },
  ]);
  assert.equal(popped.after.fields.appetite, hoverOnly.after.fields.appetite);
  assert.equal(popped.after.fields.energy, hoverOnly.after.fields.energy);
  assert.equal(popped.after.fields.sleepiness, hoverOnly.after.fields.sleepiness);
  assert.ok(popped.after.fields.social_need < hoverOnly.after.fields.social_need);
  assert.ok(popped.after.fields.stimulation_need < hoverOnly.after.fields.stimulation_need);
  assert.ok(popped.after.scores.cat1_social_ping < hoverOnly.after.scores.cat1_social_ping);
  assert.ok(popped.after.scores.cat1_play_yarn < hoverOnly.after.scores.cat1_play_yarn);
});

test('repeated real movement accumulates hunger and fatigue without creating action intent', () => {
  const repeated = runScenario(Array.from({ length: 6 }, (_, index) => ([
    { type: 'drag_start' },
    {
      type: 'drag_end',
      detail: {
        activityId: `repeated-move-${index}`,
        pathDistancePx: 160,
        durationMs: 2200,
      },
    },
  ])).flat());
  assert.ok(repeated.after.fields.appetite >= repeated.before.fields.appetite + 0.23);
  assert.ok(repeated.after.fields.energy <= repeated.before.fields.energy - 0.26);
  assert.ok(repeated.after.fields.sleepiness >= repeated.before.fields.sleepiness + 0.11);
  assert.ok(scoreDelta(repeated, 'cat1_eat_snack') > 50,
    'real repeated movement must make eating materially more competitive');
  assert.deepEqual(repeated.after.intents, {});
});

test('duplicate drag terminals settle interaction and physical feedback only once', () => {
  const runtime = createProbe();
  runtime.observe('drag_start');
  runtime.observe('drag_end', {
    activityId: 'deduplicated-terminal',
    pathDistancePx: 160,
    durationMs: 2200,
  });
  const settled = runtime.snapshot();

  runtime.observe('drag_end', {
    activityId: 'deduplicated-terminal',
    pathDistancePx: 160,
    durationMs: 2200,
  });
  assert.deepEqual(runtime.snapshot().fields, settled.fields);
});

test('delayed end-only terminal does not reuse rapid feedback from a stale gesture', () => {
  const runtime = createProbe();
  runtime.observe('drag_start');
  runtime.observe('rapid_drag');
  const beforeTerminal = runtime.snapshot().fields;

  runtime.advanceTime(10 * 1000);
  runtime.observe('drag_end', { activityId: 'delayed-end-only-terminal' });
  const afterTerminal = runtime.snapshot().fields;

  assertClose(
    afterTerminal.social_need,
    mergeInteractionDose(beforeTerminal.social_need, 0.025)
  );
  assertClose(
    afterTerminal.stimulation_need,
    mergeInteractionDose(beforeTerminal.stimulation_need, 0.18)
  );
});

test('NEKO-PC return-ball phases enter the shared Cat Mind scoring path', () => {
  const runtime = createProbe();
  const before = runtime.snapshot();
  const activityId = 'neko-pc-return-drag:1';

  runtime.dispatchReturnBallMove({
    source: 'neko-pc',
    reason: 'return-ball-drag-start',
    activityId,
  });
  runtime.dispatchReturnBallMove({
    source: 'neko-pc',
    reason: 'return-ball-drag-active',
    activityId,
  });
  runtime.dispatchReturnBallMove({
    source: 'neko-pc',
    reason: 'return-ball-drag-end',
    activityId,
    pathDistancePx: 160,
    displacementPx: 120,
    durationMs: 2200,
    dragCancelled: false,
  });

  const after = runtime.snapshot();
  const dragEvents = runtime.recentEvents().filter((event) => (
    event.type === 'drag_start' || event.type === 'drag_end'
  ));

  assert.deepEqual(dragEvents.map((event) => event.type), ['drag_start', 'drag_end']);
  assert.equal(dragEvents[0].detail.source, 'neko-pc');
  assert.equal(dragEvents[1].detail.activityId, activityId);
  assert.ok(after.fields.appetite > before.fields.appetite);
  assert.ok(after.fields.sleepiness > before.fields.sleepiness);
  assert.ok(after.fields.energy < before.fields.energy);
  assert.ok(after.fields.social_need > before.fields.social_need);
  assert.ok(after.fields.stimulation_need > before.fields.stimulation_need);
  assert.ok(after.scores.cat1_play_yarn > before.scores.cat1_play_yarn);
  assert.ok(after.scores.cat1_small_move > before.scores.cat1_small_move);
  assert.deepEqual(after.intents, {}, 'ordinary desktop drag must not become an action command');
  assert.equal(runtime.requests.length, 0, 'desktop observations must still respect provider rejection');
});

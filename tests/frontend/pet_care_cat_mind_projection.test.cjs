const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'static', 'app', 'app-cat-mind.js'),
  'utf8'
);

class EventTargetLike {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatchEvent(event) {
    for (const listener of (this.listeners.get(event.type) || []).slice()) listener.call(this, event);
    return true;
  }
}

function createRuntime() {
  let now = Date.parse('2026-10-21T00:00:00Z');
  let monotonicNow = 0;
  const win = new EventTargetLike();
  win.performance = { now: () => monotonicNow };
  win.setTimeout = () => 0;
  win.clearTimeout = () => {};
  win.setInterval = () => 0;
  win.clearInterval = () => {};
  class CustomEventLike {
    constructor(type, init = {}) { this.type = type; this.detail = init.detail || {}; }
  }
  const FakeDate = { now: () => now, parse: Date.parse };
  const context = vm.createContext({ window: win, CustomEvent: CustomEventLike, Date: FakeDate, console });
  vm.runInContext(source, context, { filename: 'app-cat-mind.js' });
  return {
    win,
    setNow(value) { now = value; },
    advanceMonotonic(value) { monotonicNow += value; },
  };
}

function scores(win) {
  return Object.fromEntries(win.nekoCatMind.getDebugSnapshot().actionScores.map(item => [
    item.actionId, item.score,
  ]));
}

function projection(overrides = {}) {
  return {
    schema_version: 3,
    config_revision: 'candidate-1',
    effective_at: '2026-10-20T00:00:00Z',
    server_time: '2026-10-21T00:00:00Z',
    cat_mind_valid_until: '2026-10-21T00:05:00Z',
    status_rules: {
      clamp: { min: 0, max: 100 }, paused_during_exploration: true,
      per_hour: { satiety: -1, energy: 4, cleanliness: -1, interaction: -0.5 },
      mood_weights: {},
    },
    status_seed: { interaction: 40 },
    cat_mind: {
      cat1_social_ping: { score_bias: 0 },
      cat1_eat_snack: { score_bias: 20 },
      cat1_small_move: { score_bias: 0 },
      cat1_play_yarn: { score_bias: -5 },
      cat2_nap_feedback: { score_bias: 4 },
      cat3_sleep_feedback: { score_bias: 6 },
    },
    ...overrides,
  };
}

test('PetCare projection changes Cat Mind weights without writing its five needs', () => {
  const { win } = createRuntime();
  const beforeFields = win.nekoCatMind.getState().fields;
  const beforeScores = scores(win);

  assert.equal(win.nekoCatMind.setPetCareProjection(projection()), true);
  const afterFields = win.nekoCatMind.getState().fields;
  const afterScores = scores(win);
  assert.deepEqual(afterFields, beforeFields);
  assert.equal(afterScores.cat1_eat_snack - beforeScores.cat1_eat_snack, 20);
  assert.equal(afterScores.cat1_play_yarn - beforeScores.cat1_play_yarn, -5);
  assert.deepEqual(Object.keys(win.nekoCatMind.getPetCareProjection()).sort(), [
    'action_biases', 'cat_mind_valid_until', 'config_revision', 'effective_at',
    'schema_version', 'server_time',
  ]);
});

test('invalid or expired projections fail closed and restore ordinary Cat Mind scoring', () => {
  const runtime = createRuntime();
  const ordinary = scores(runtime.win);
  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection()), true);
  assert.notEqual(scores(runtime.win).cat1_eat_snack, ordinary.cat1_eat_snack);

  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection({ config_revision: '' })), false);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);
  assert.equal(scores(runtime.win).cat1_eat_snack, ordinary.cat1_eat_snack);

  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection({ status_seed: {} })), false);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);

  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection({
    cat_mind_valid_until: '2026-10-21T00:05:00.001Z',
  })), false);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);

  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection({
    cat_mind: { cat1_eat_snack: { score_bias: null } },
  })), true);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection().action_biases.cat1_eat_snack,
    undefined);

  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection()), true);
  runtime.setNow(Date.parse('2026-10-21T00:05:00Z'));
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);
  assert.equal(scores(runtime.win).cat1_eat_snack, ordinary.cat1_eat_snack);
});

test('legacy v2 projection keeps its original validity field and expiry behavior', () => {
  const runtime = createRuntime();
  const legacy = projection({
    schema_version: 2,
    valid_until: '2026-10-21T00:05:00Z',
  });
  delete legacy.cat_mind_valid_until;
  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(legacy), true);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection().valid_until,
    Date.parse('2026-10-21T00:05:00Z'));
  runtime.setNow(Date.parse('2026-10-21T00:05:00Z'));
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);
});

test('wall-clock rollback cannot extend a Cat Mind projection past five real minutes', () => {
  const runtime = createRuntime();
  assert.equal(runtime.win.nekoCatMind.setPetCareProjection(projection()), true);
  runtime.setNow(Date.parse('2026-10-20T00:00:00Z'));
  runtime.advanceMonotonic(5 * 60 * 1000);
  assert.equal(runtime.win.nekoCatMind.getPetCareProjection(), null);
});

test('out-of-contract score biases are ignored without exposing PetCare status fields', () => {
  const { win } = createRuntime();
  const ordinary = scores(win);
  const value = projection({
    cat_mind: {
      cat1_eat_snack: { score_bias: 101 },
      satiety: { score_bias: 99 },
    },
  });
  assert.equal(win.nekoCatMind.setPetCareProjection(value), true);
  assert.equal(scores(win).cat1_eat_snack, ordinary.cat1_eat_snack);
  assert.equal(Object.keys(win.nekoCatMind.getPetCareProjection().action_biases).length, 0);
  assert.equal('satiety' in win.nekoCatMind.getState().fields, false);
});

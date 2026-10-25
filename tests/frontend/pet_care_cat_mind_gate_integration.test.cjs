const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const MODE_SOURCE = fs.readFileSync(
  path.join(PROJECT_ROOT, 'static', 'app', 'app-pet-care-mode.js'),
  'utf8'
);
const CAT_MIND_SOURCE = fs.readFileSync(
  path.join(PROJECT_ROOT, 'static', 'app', 'app-cat-mind.js'),
  'utf8'
);
const PARTS_DIR = path.join(PROJECT_ROOT, 'static', 'avatar', 'avatar-ui-buttons');
const PART_NAMES = [
  'cat-resource-registry.js',
  'core.js',
  'idle-assets-and-question.js',
  'idle-playground.js',
  'idle-actions-and-audio.js',
  'idle-drag-and-subactions.js',
  'idle-journey-and-presentation.js',
  'idle-cat-mind-observations.js',
  'methods-setup.js',
  'methods-buttons.js',
  'methods-return.js',
  'methods-state-and-cleanup.js',
];

class CustomEventLike {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
  }

  dispatchEvent(event) {
    for (const listener of (this.listeners.get(event.type) || []).slice()) {
      listener.call(this, event);
    }
    return true;
  }
}

function loadIntegratedRuntime() {
  const buttons = [];
  const document = {
    currentScript: { src: 'http://127.0.0.1/static/avatar/avatar-ui-buttons/core.js?v=test' },
    getElementById() { return null; },
    querySelectorAll() { return buttons; },
    addEventListener() {},
    removeEventListener() {},
  };
  const window = new EventTargetLike();
  window.location = { href: 'http://127.0.0.1/', search: '' };
  window.innerWidth = 1280;
  window.innerHeight = 720;
  window.performance = { now: () => 0 };
  window.setTimeout = setTimeout;
  window.clearTimeout = clearTimeout;
  window.setInterval = setInterval;
  window.clearInterval = clearInterval;
  const context = vm.createContext({
    URL,
    CustomEvent: CustomEventLike,
    Date,
    Map,
    Math,
    Number,
    Object,
    Promise,
    clearInterval,
    clearTimeout,
    console,
    document,
    requestAnimationFrame() { return 1; },
    cancelAnimationFrame() {},
    setInterval,
    setTimeout,
    window,
  });

  vm.runInContext(MODE_SOURCE, context, { filename: 'app-pet-care-mode.js' });
  vm.runInContext(CAT_MIND_SOURCE, context, { filename: 'app-cat-mind.js' });
  const catMind = window.nekoCatMind;
  for (const name of PART_NAMES) {
    vm.runInContext(
      fs.readFileSync(path.join(PARTS_DIR, name), 'utf8'),
      context,
      { filename: name }
    );
  }
  return { buttons, catMind, context, window };
}

function projection(now = Date.now()) {
  return {
    schema_version: 3,
    config_revision: 'gate-integration-v1',
    effective_at: new Date(now - 1000).toISOString(),
    server_time: new Date(now).toISOString(),
    cat_mind_valid_until: new Date(now + 5 * 60 * 1000).toISOString(),
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
  };
}

function actionScore(catMind, actionId) {
  return catMind.getDebugSnapshot().actionScores.find(item => item.actionId === actionId).score;
}

test('idle projected Cat Mind runs, PetCare presentation gates temporarily, and exploration still owns the entity', () => {
  const { buttons, catMind, context, window } = loadIntegratedRuntime();
  const acknowledgements = [];
  const providerRequests = [];
  const runs = [];
  const cleanups = [];
  const button = {
    __nekoIdleCat1EatActionState: { active: true, catMindSource: 'cat_mind' },
    __nekoIdleCat1PlayActionState: { active: true, catMindSource: 'cat_mind' },
    __nekoIdleCat1Journey: {
      catMindActionId: 'cat1_small_move',
      catMindSource: 'cat_mind',
      catMindRunId: 'existing-small-move-run',
    },
  };
  buttons.push(button);

  window.nekoCatMind = {
    getDebugSnapshot: catMind.getDebugSnapshot,
    getPetCareProjection: catMind.getPetCareProjection,
    setPetCareProjection: catMind.setPetCareProjection,
    acknowledgeActionRequest(payload) {
      acknowledgements.push(payload);
      return true;
    },
  };
  Object.assign(context, {
    _dryRunNekoCatMindActionProvider(actionId) {
      providerRequests.push(actionId);
      return { allowed: true, reason: 'ready' };
    },
    _findNekoCatMindVisibleButtonForTier() { return button; },
    _playNekoIdleCat1EatAction(_button, options) {
      runs.push({ actionId: 'cat1_eat_snack', options });
      return true;
    },
    _playNekoIdleCat1PlayAction(_button, options) {
      runs.push({ actionId: 'cat1_play_yarn', options });
      return true;
    },
    _cancelNekoIdleCat1EatAction() { cleanups.push('cat1_eat_snack'); },
    _cancelNekoIdleCat1PlayAction() { cleanups.push('cat1_play_yarn'); },
    _cancelNekoIdleCat1Journey() { cleanups.push('cat1_small_move'); },
    _forceNekoIdleCat1LayerCleanup() {},
    _getNekoIdleReturnContainerFromButton() { return {}; },
    _isNekoIdleReturnDragActionActive() { return false; },
    _finishNekoIdleReturnDragAction() {},
    _clearNekoIdleCat1EdgePeek() {},
    _stopNekoIdleSleepSound() { cleanups.push('sleep'); },
    _stopNekoIdleCat1AmbientSound() { cleanups.push('cat1_social_ping'); },
  });

  window.nekoPetCareMode.setMode('pet-available', { reason: 'complete-bound-snapshot' });
  window.nekoPetCareMode.setCatLocalActive(true, { reason: 'cat-form-active' });
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: false,
    reason: 'pet-care-idle',
  });

  const ordinaryEatScore = actionScore(catMind, 'cat1_eat_snack');
  assert.equal(catMind.setPetCareProjection(projection()), true);
  const projectedEatScore = actionScore(catMind, 'cat1_eat_snack');
  assert.equal(projectedEatScore - ordinaryEatScore, 20);
  window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
    detail: {
      source: 'cat_mind', requestId: 'projected-idle-eat', actionId: 'cat1_eat_snack',
      tier: 'cat1', detail: { triggerTypes: ['clock_tick'], score: projectedEatScore },
    },
  }));
  assert.equal(runs.length, 1);
  assert.equal(runs[0].actionId, 'cat1_eat_snack');
  assert.ok(providerRequests.length > 0);

  const providersBeforeConflict = providerRequests.length;
  const runsBeforeConflict = runs.length;
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    reason: 'pet-care-presentation-active',
  });

  assert.ok(cleanups.includes('cat1_eat_snack'));
  assert.ok(cleanups.includes('cat1_play_yarn'));
  assert.ok(cleanups.includes('cat1_small_move'));
  assert.ok(cleanups.includes('cat1_social_ping'));

  const actionTiers = new Map([
    ['cat1_social_ping', 'cat1'],
    ['cat1_eat_snack', 'cat1'],
    ['cat1_small_move', 'cat1'],
    ['cat1_play_yarn', 'cat1'],
    ['cat2_nap_feedback', 'cat2'],
    ['cat3_sleep_feedback', 'cat3'],
  ]);
  for (const [actionId, tier] of actionTiers) {
    window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
      detail: {
        source: 'cat_mind',
        requestId: `blocked-${actionId}`,
        actionId,
        tier,
        detail: { triggerTypes: ['clock_tick'] },
      },
    }));
  }

  assert.equal(providerRequests.length, providersBeforeConflict,
    'blocked requests must not reach a provider or runner');
  assert.equal(runs.length, runsBeforeConflict);
  assert.deepEqual(
    acknowledgements.slice(-actionTiers.size).map(({ status, reason }) => ({ status, reason })),
    [...actionTiers.keys()].map(() => ({
      status: 'rejected',
      reason: 'pet-care-cat-mind-suppressed',
    }))
  );

  window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
    detail: {
      source: 'cat_mind',
      requestId: 'user-yarn-request',
      actionId: 'cat1_play_yarn',
      tier: 'cat1',
      detail: { triggerTypes: ['chat_yarn_drag_completed'] },
    },
  }));
  assert.equal(runs.length, runsBeforeConflict + 1);
  assert.equal(runs.at(-1).actionId, 'cat1_play_yarn');

  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: false,
    reason: 'pet-care-presentation-ended',
  });
  window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
    detail: {
      source: 'cat_mind',
      requestId: 'ordinary-cat-request',
      actionId: 'cat1_eat_snack',
      tier: 'cat1',
      detail: { triggerTypes: ['clock_tick'] },
    },
  }));
  assert.equal(runs.at(-1).actionId, 'cat1_eat_snack');

  const runsBeforeExploration = runs.length;
  window.nekoPetCareMode.beginExplorationAway({ activityId: 'explore-1' });
  for (const request of [
    { requestId: 'away-eat', actionId: 'cat1_eat_snack', triggerTypes: ['clock_tick'] },
    { requestId: 'away-user-yarn', actionId: 'cat1_play_yarn', triggerTypes: ['chat_yarn_drag_completed'] },
  ]) {
    window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
      detail: {
        source: 'cat_mind', requestId: request.requestId, actionId: request.actionId,
        tier: 'cat1', detail: { triggerTypes: request.triggerTypes },
      },
    }));
  }
  assert.equal(runs.length, runsBeforeExploration);
  assert.deepEqual(
    acknowledgements.slice(-2).map(({ status, reason }) => ({ status, reason })),
    [
      { status: 'rejected', reason: 'pet-care-exploration-away' },
      { status: 'rejected', reason: 'pet-care-exploration-away' },
    ]
  );

  const away = window.nekoPetCareMode.getState().explorationAway;
  window.nekoPetCareMode.endExplorationAway({
    activityId: away.activityId,
    activityGeneration: away.generation,
  });
  window.nekoPetCareMode.setMode('pet-unavailable', { reason: 'ordinary-cat-fallback' });
  window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-request', {
    detail: {
      source: 'cat_mind', requestId: 'ordinary-cat-request', actionId: 'cat1_eat_snack',
      tier: 'cat1', detail: { triggerTypes: ['clock_tick'] },
    },
  }));
  assert.equal(runs.length, runsBeforeExploration + 1);
  assert.equal(runs.at(-1).actionId, 'cat1_eat_snack');
});

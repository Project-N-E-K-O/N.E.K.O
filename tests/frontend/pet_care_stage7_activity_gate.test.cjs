const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const modeSource = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care-mode.js'), 'utf8');
const returnSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-ui', 'surface-floating-controls.js'),
  'utf8',
);
const gateListenerSources = [
  'static/avatar/avatar-ui-buttons/idle-desktop-window-edge-peek.js',
  'static/avatar/avatar-ui-buttons/idle-desktop-window-top-edge.js',
  'static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js',
  'static/avatar/avatar-ui-buttons/idle-desktop-window-gravity.js',
  'static/app/app-desktop-window-sensing.js',
  'static/avatar/avatar-ui-buttons/idle-playground.js',
  'static/app/app-auto-goodbye.js',
  'static/avatar/avatar-ui-buttons/idle-actions-and-audio.js',
  'static/avatar/avatar-ui-buttons/idle-assets-and-question.js',
].map(file => fs.readFileSync(path.join(root, file), 'utf8'));

class EventTargetLike {
  constructor() { this.listeners = new Map(); this.events = []; }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatchEvent(event) {
    this.events.push(event);
    for (const listener of (this.listeners.get(event.type) || []).slice()) listener.call(this, event);
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
}

function createRuntime() {
  const window = new EventTargetLike();
  const context = { window, CustomEvent: CustomEventLike, Date, console };
  vm.createContext(context);
  vm.runInContext(modeSource, context, { filename: 'app-pet-care-mode.js' });
  return window;
}

test('exploration gate is independent from the Stage 3 Cat Mind compatibility gate', () => {
  const window = createRuntime();
  const cleanups = [];
  const gates = [];
  window.addEventListener('neko:pet-care:cleanup-request', event => cleanups.push(event.detail));
  window.addEventListener('neko:pet-care:exploration-gate', event => gates.push(event.detail));

  window.nekoPetCareMode.setMode('pet-available');
  window.nekoPetCareMode.setCatMindCompatibility({ suppressed: true });
  const started = window.nekoPetCareMode.beginExplorationAway({ activityId: 'explore-1' });
  assert.equal(started.explorationAway.active, true);
  assert.equal(started.explorationAway.activityId, 'explore-1');
  assert.equal(started.mode, 'pet-available');
  assert.equal(started.catMindSuppressed, true);
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_small_move',
  }).reason, 'pet-care-exploration-away');
  assert.equal(window.nekoPetCareMode.shouldBlockEntityOutput({ source: 'journey' }).blocked, true);
  assert.equal(window.nekoPetCareMode.shouldBlockEntityOutput({ source: 'pet-care-exploration' }).blocked, false);
  assert.equal(cleanups.at(-1).source, 'exploration');
  assert.equal(cleanups.at(-1).activityId, 'explore-1');
  assert.equal(gates.at(-1).active, true);

  const repeated = window.nekoPetCareMode.beginExplorationAway({ activityId: 'explore-1' });
  assert.equal(repeated.explorationAway.generation, started.explorationAway.generation);
  const staleEnd = window.nekoPetCareMode.endExplorationAway({ activityId: 'other' });
  assert.equal(staleEnd.explorationAway.activityId, 'explore-1');
  const unscopedEnd = window.nekoPetCareMode.endExplorationAway({ reason: 'late-response' });
  assert.equal(unscopedEnd.explorationAway.activityId, 'explore-1');
  const ended = window.nekoPetCareMode.endExplorationAway({
    activityId: 'explore-1',
    activityGeneration: started.explorationAway.generation,
  });
  assert.equal(ended.explorationAway, null);
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(gates.at(-1).active, false);
});

test('surface return events carry exploration recall identity and do not rely on Cat Mind', () => {
  assert.match(returnSource, /explorationRecall/);
  assert.match(returnSource, /activityId: explorationActivityId/);
  assert.match(returnSource, /activityGeneration: explorationActivityGeneration/);
  assert.match(returnSource, /neko:cat-return-commit/);
  assert.match(returnSource, /neko:cat-return-complete/);
  assert.match(returnSource, /neko:cat-return-abort/);
  assert.match(returnSource, /neko:pet-care:exploration-recall-request/);
  assert.match(returnSource, /source: 'pet-care-exploration-recall'/);
});

test('context changes release the old exploration gate before a new account can load', () => {
  const window = createRuntime();
  window.nekoPetCareMode.updateContext({ accountId: 'a', currentCharacterUid: 'role-a', boundRoleId: 'role-a' });
  const started = window.nekoPetCareMode.beginExplorationAway({ activityId: 'old-activity' });
  window.nekoPetCareMode.updateContext({ accountId: 'b', currentCharacterUid: 'role-b', boundRoleId: 'role-b' });
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(window.nekoPetCareMode.isCurrentExploration(started.explorationAway), false);
});

test('display context change does not release exploration gate before lifecycle owner recall', () => {
  const window = createRuntime();
  window.nekoPetCareMode.updateContext({ accountId: 'a', currentCharacterUid: 'role-a', boundRoleId: 'role-a' });
  window.nekoPetCareMode.beginExplorationAway({ activityId: 'active-activity' });
  const requests = [];
  window.addEventListener('neko:pet-care:context-change-request', event => requests.push(event.detail));
  window.dispatchEvent(new CustomEventLike('chat-avatar-display-updated', {
    detail: { character_uid: 'role-b', reason: 'model-switch' },
  }));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].nextCharacterUid, 'role-b');
  assert.equal(window.nekoPetCareMode.getState().currentCharacterUid, 'role-a');
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
});

test('an external server-confirmed exploration gate event activates the shared adapter', () => {
  const window = createRuntime();
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: true, exploration_id: 'server-activity', generation: 'server-generation' },
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  assert.equal(window.nekoPetCareMode.getState().explorationAway.activityId, 'server-activity');
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: false, exploration_id: 'server-activity', generation: 'server-generation' },
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
});

test('late exploration gate events from an older activity are ignored', () => {
  const window = createRuntime();
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: true, activityId: 'activity-current' },
  }));
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: true, activityId: 'activity-stale' },
  }));
  assert.equal(window.nekoPetCareMode.getState().explorationAway.activityId, 'activity-current');
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: false, activityId: 'activity-stale' },
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: false, activityId: 'activity-current' },
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(window.nekoPetCareMode.shouldApplyExplorationGate({
    active: false, activityId: 'activity-current',
  }), true);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: { active: true, activityId: 'activity-current' },
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
});

test('renderer exploration listeners validate the shared activity identity', () => {
  for (const source of gateListenerSources) {
    assert.match(source, /shouldApplyExplorationGate/);
    assert.match(source, /activityId \|\| detail\.explorationId \|\| detail\.exploration_id/);
  }
});

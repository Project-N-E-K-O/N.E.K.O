const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const modeSource = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care-mode.js'), 'utf8');
const catLocalChatLexiconSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-react-chat-window', 'cat-local-chat-lexicon.js'),
  'utf8',
);
const catLocalChatSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-react-chat-window', 'cat-local-chat.js'),
  'utf8',
);
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
  'static/app/app-react-chat-window/cat-local-chat.js',
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

function gateDetail(window, detail, context = window.nekoPetCareMode.captureContext()) {
  return { ...detail, context };
}

function createCatLocalChatRuntime() {
  const window = new EventTargetLike();
  const timers = new Map();
  let timerSequence = 0;
  window.location = { pathname: '/', href: 'http://localhost/' };
  window.__appReactChatWindowParts = {};
  window.setTimeout = callback => {
    timerSequence += 1;
    timers.set(timerSequence, callback);
    return timerSequence;
  };
  window.clearTimeout = timerId => timers.delete(timerId);
  window.nekoCatMind = {
    getState() {
      return { active: true, tier: 'cat1', enteredAt: 1 };
    },
    observe() { return true; },
  };
  const deterministicMath = Object.create(Math);
  deterministicMath.random = () => 0.5;
  const context = {
    window,
    CustomEvent: CustomEventLike,
    Date,
    Math: deterministicMath,
    console,
    document: { currentScript: null },
  };
  vm.createContext(context);
  vm.runInContext(modeSource, context, { filename: 'app-pet-care-mode.js' });
  vm.runInContext(catLocalChatLexiconSource, context, { filename: 'cat-local-chat-lexicon.js' });
  vm.runInContext(catLocalChatSource, context, { filename: 'cat-local-chat.js' });
  return { window, timers };
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
  assert.equal(window.nekoPetCareMode.shouldBlockEntityOutput({
    source: 'journey', allowDuringExploration: true,
  }).blocked, true);
  assert.equal(window.nekoPetCareMode.shouldBlockEntityOutput({
    source: 'pet-care-exploration',
  }).blocked, true);
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
  const cleanups = [];
  window.addEventListener('neko:pet-care:cleanup-request', event => cleanups.push(event.detail));
  const active = gateDetail(window, {
    active: true, exploration_id: 'server-activity', generation: 'server-generation',
  });
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: active,
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  assert.equal(window.nekoPetCareMode.getState().explorationAway.activityId, 'server-activity');
  assert.equal(cleanups.length, 1);
  assert.equal(cleanups[0].source, 'exploration');
  assert.equal(cleanups[0].activityId, 'server-activity');
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: active,
  }));
  assert.equal(cleanups.length, 1);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, {
      active: false, exploration_id: 'server-activity', generation: 'server-generation',
    }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
});

test('late exploration gate events from an older activity are ignored', () => {
  const window = createRuntime();
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-current' }),
  }));
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-stale' }),
  }));
  assert.equal(window.nekoPetCareMode.getState().explorationAway.activityId, 'activity-current');
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: false, activityId: 'activity-stale' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: false, activityId: 'activity-current' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(window.nekoPetCareMode.shouldApplyExplorationGate({
    active: false, activityId: 'activity-current',
    context: window.nekoPetCareMode.captureContext(),
  }), true);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-current' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
});

test('a terminal activity remains fenced after a newer activity also finishes', () => {
  const window = createRuntime();
  for (const activityId of ['activity-a', 'activity-b']) {
    window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
      detail: gateDetail(window, { active: true, activityId }),
    }));
    window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
      detail: gateDetail(window, { active: false, activityId }),
    }));
  }

  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-a' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(window.nekoPetCareMode.getState().explorationAway, null);
});

test('cat local reply work ignores a stale active gate but current exploration still cancels it', () => {
  const { window, timers } = createCatLocalChatRuntime();
  const oldActivity = window.nekoPetCareMode.beginExplorationAway({ activityId: 'activity-old' });
  window.nekoPetCareMode.endExplorationAway({
    activityId: 'activity-old',
    activityGeneration: oldActivity.explorationAway.generation,
  });

  assert.equal(window.nekoCatLocalChatManager.submit({
    text: 'hello', requestId: 'request-1', enteredAt: 1,
  }), true);
  assert.equal(timers.size, 1);
  const firstReply = timers.values().next().value;

  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-old' }),
  }));
  assert.equal(timers.size, 1);
  firstReply();
  assert.deepEqual(
    Array.from(window.nekoCatLocalChatManager.getSnapshot().items, item => item.role),
    ['user', 'assistant'],
  );

  timers.clear();
  assert.equal(window.nekoCatLocalChatManager.submit({
    text: 'again', requestId: 'request-2', enteredAt: 1,
  }), true);
  assert.equal(timers.size, 1);
  window.nekoPetCareMode.beginExplorationAway({ activityId: 'activity-current' });
  assert.equal(timers.size, 0);
});

test('a context release does not retire the authoritative activity for the same account login', () => {
  const window = createRuntime();
  const accountA = {
    accountId: 'account-a', currentCharacterUid: 'role-a', boundRoleId: 'role-a',
  };
  window.nekoPetCareMode.updateContext(accountA);
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-a' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);

  window.nekoPetCareMode.updateContext({
    accountId: '', currentCharacterUid: '', boundRoleId: '', forceNewGeneration: true,
  });
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  window.nekoPetCareMode.updateContext({ ...accountA, forceNewGeneration: true });
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-a' }),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), true);
  assert.equal(window.nekoPetCareMode.getState().explorationAway.activityId, 'activity-a');
});

test('an old account event cannot restore its activity after the context moves to another account', () => {
  const window = createRuntime();
  window.nekoPetCareMode.updateContext({
    accountId: 'account-a', currentCharacterUid: 'role-a', boundRoleId: 'role-a',
  });
  const oldAccountContext = window.nekoPetCareMode.captureContext();
  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-a' }, oldAccountContext),
  }));
  window.nekoPetCareMode.updateContext({
    accountId: 'account-b', currentCharacterUid: 'role-b', boundRoleId: 'role-b',
  });

  window.dispatchEvent(new CustomEventLike('neko:pet-care:exploration-gate', {
    detail: gateDetail(window, { active: true, activityId: 'activity-a' }, oldAccountContext),
  }));
  assert.equal(window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(window.nekoPetCareMode.getState().explorationAway, null);
});

test('renderer exploration listeners validate the shared activity identity', () => {
  for (const source of gateListenerSources) {
    assert.match(source, /shouldApplyExplorationGate/);
    assert.match(source, /activityId \|\| detail\.explorationId \|\| detail\.exploration_id/);
  }
});

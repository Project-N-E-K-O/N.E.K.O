const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const modeSource = fs.readFileSync(
  path.join(projectRoot, 'static', 'app', 'app-pet-care-mode.js'),
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
    this.detail = init.detail;
  }
}

function createRuntime() {
  const win = new EventTargetLike();
  const context = {
    window: win,
    CustomEvent: CustomEventLike,
    Date,
    console,
  };
  vm.createContext(context);
  vm.runInContext(modeSource, context);
  return win;
}

test('stage 0 starts in ordinary-cat session-invalid mode', () => {
  const win = createRuntime();
  const state = win.nekoPetCareMode.getState();
  assert.equal(state.mode, 'session-invalid');
  assert.equal(state.generation, 0);
  assert.equal(state.catMindSuppressed, false);
  const initialGate = win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'cat1_eat_snack',
  });
  assert.equal(initialGate.blocked, false);
  assert.equal(initialGate.reason, '');
});

test('stage 0 context tokens fence late account and role responses', () => {
  const win = createRuntime();
  const first = win.nekoPetCareMode.updateContext({
    accountId: 'account-a',
    currentCharacterUid: 'local-role-a',
    boundRoleId: 'role-a',
    reason: 'initial-session',
  });
  const second = win.nekoPetCareMode.updateContext({
    accountId: 'account-b',
    currentCharacterUid: 'local-role-b',
    boundRoleId: 'role-b',
    reason: 'account-switch',
  });

  assert.equal(first.generation, 1);
  assert.equal(second.generation, 2);
  assert.equal(win.nekoPetCareMode.isCurrentContext(first), false);
  assert.equal(win.nekoPetCareMode.isCurrentContext(second), true);
});

test('Cat Mind compatibility gate blocks only configured autonomous requests and emits cleanup', () => {
  const win = createRuntime();
  const cleanups = [];
  win.addEventListener('neko:pet-care:cleanup-request', (event) => cleanups.push(event.detail));

  win.nekoPetCareMode.setMode('pet-available', { reason: 'snapshot-ready' });
  win.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ['cat1_eat_snack', 'cat1_play_yarn'],
    reason: 'pet-care-action-conflict',
  });

  assert.deepEqual(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'cat1_eat_snack',
  }).blocked, true);
  assert.equal(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'cat1_small_move',
  }).blocked, false);
  assert.equal(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'user',
    actionId: 'cat1_eat_snack',
  }).blocked, false);
  assert.equal(cleanups.length, 1);
  assert.equal(cleanups[0].reason, 'pet-care-action-conflict');
  assert.deepEqual(Array.from(cleanups[0].actionIds), ['cat1_eat_snack', 'cat1_play_yarn']);

  win.nekoPetCareMode.setCatMindCompatibility({ suppressed: true, actionIds: [] });
  assert.equal(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'cat1_eat_snack',
  }).blocked, false);
});

test('current character display updates advance the context generation', () => {
  const win = createRuntime();
  win.nekoPetCareMode.updateContext({
    accountId: 'account-a',
    boundRoleId: 'role-a',
    reason: 'session-ready',
  });
  win.dispatchEvent(new CustomEventLike('chat-avatar-display-updated', {
    detail: { character_uid: 'local-role-a', reason: 'switch-commit' },
  }));
  const state = win.nekoPetCareMode.getState();
  assert.equal(state.generation, 2);
  assert.equal(state.accountId, 'account-a');
  assert.equal(state.currentCharacterUid, 'local-role-a');
  assert.equal(state.boundRoleId, 'role-a');
  assert.equal(state.reason, 'switch-commit');
});

test('leaving an available mode restores the ordinary Cat Mind path', () => {
  const win = createRuntime();
  win.nekoPetCareMode.setMode('pet-available');
  win.nekoPetCareMode.setCatMindCompatibility({ suppressed: true });
  win.nekoPetCareMode.setMode('session-invalid', { reason: 'logout' });
  assert.equal(win.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'cat1_play_yarn',
  }).blocked, false);
});

test('context changes fail closed and clean the old Cat Mind gate', () => {
  const win = createRuntime();
  const cleanups = [];
  win.addEventListener('neko:pet-care:cleanup-request', event => cleanups.push(event.detail));
  win.nekoPetCareMode.setCatMindCompatibility({ suppressed: true });
  win.nekoPetCareMode.updateContext({ accountId: 'account-b', reason: 'account-switch' });
  assert.equal(win.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(win.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_eat_snack',
  }).blocked, false);
  assert.equal(cleanups.at(-1).reason, 'pet-care-context-changed');
});

test('leaving the cat form fails closed and cleans autonomous actions', () => {
  const win = createRuntime();
  const cleanups = [];
  win.addEventListener('neko:pet-care:cleanup-request', event => cleanups.push(event.detail));
  win.nekoPetCareMode.setCatMindCompatibility({ suppressed: true });
  win.nekoPetCareMode.setCatLocalActive(true);
  win.nekoPetCareMode.setCatLocalActive(false, { reason: 'cat-form-left' });
  assert.equal(win.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(cleanups.at(-1).reason, 'pet-care-cat-form-left');
});

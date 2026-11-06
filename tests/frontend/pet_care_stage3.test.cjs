const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const modeSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-mode.js'),
  'utf8',
);
const petCareSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care.js'),
  'utf8',
);
const actionAdapterSource = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'idle-actions-and-audio.js'),
  'utf8',
);
const returnMethodsSource = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'methods-return.js'),
  'utf8',
);
const idleCssSource = fs.readFileSync(
  path.join(root, 'static', 'css', 'index.css'),
  'utf8',
);

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
    this.events = [];
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter((item) => item !== listener));
  }

  dispatchEvent(event) {
    this.events.push(event);
    for (const listener of (this.listeners.get(event.type) || []).slice()) {
      listener.call(this, event);
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

class LocalStorageLike {
  constructor(seed) {
    this.values = new Map(Object.entries(seed || {}));
  }

  getItem(key) {
    return this.values.has(String(key)) ? this.values.get(String(key)) : null;
  }

  setItem(key, value) {
    this.values.set(String(key), String(value));
  }

  removeItem(key) {
    this.values.delete(String(key));
  }

  key(index) {
    return [...this.values.keys()][index] || null;
  }

  get length() {
    return this.values.size;
  }

  entries() {
    return [...this.values.entries()];
  }
}

function createRuntime(storage = new LocalStorageLike()) {
  const window = new EventTargetLike();
  window.localStorage = storage;
  const context = {
    window,
    CustomEvent: CustomEventLike,
    Date,
    console,
  };
  vm.createContext(context);
  vm.runInContext(modeSource, context, { filename: 'app-pet-care-mode.js' });
  return { window, storage };
}

const ACTION_IDS = [
  'cat1_social_ping',
  'cat1_eat_snack',
  'cat1_small_move',
  'cat1_play_yarn',
  'cat2_nap_feedback',
  'cat3_sleep_feedback',
];

function enterAvailablePet(runtime) {
  const { window } = runtime;
  window.nekoPetCareMode.updateContext({
    accountId: 'account-a',
    currentCharacterUid: 'role-a',
    boundRoleId: 'server-role-a',
    reason: 'complete-snapshot',
  });
  window.nekoPetCareMode.setMode('pet-available', {
    reason: 'complete-snapshot',
  });
  // This is the existing cat-form fact. It intentionally carries no model
  // manager or model-specific object.
  window.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: true, reason: 'cat-form-entered' },
  }));
}

test('stage 3 mode keeps the Cat Mind gate explicit and independent of context values', () => {
  const runtime = createRuntime();
  const { window } = runtime;

  window.nekoPetCareMode.setMode('pet-available', { reason: 'without-context' });
  window.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: true },
  }));
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);

  enterAvailablePet(runtime);
  const state = window.nekoPetCareMode.getState();
  assert.equal(state.mode, 'pet-available');
  assert.equal(state.catLocalActive, true);
  assert.equal(state.accountId, 'account-a');
  assert.equal(state.currentCharacterUid, 'role-a');
  assert.equal(state.boundRoleId, 'server-role-a');
  // The app integration owns the complete-snapshot decision. The mode
  // contract only changes this gate when its explicit compatibility API is
  // called; a non-empty role context must never be enough on its own.
  assert.equal(state.catMindSuppressed, false);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, true);
});

test('stage 3 blocks only the six autonomous Cat Mind actions and keeps user actions independent', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });

  for (const actionId of ACTION_IDS) {
    const decision = window.nekoPetCareMode.shouldBlockCatMindActionRequest({
      source: 'cat_mind',
      actionId,
      requestId: `request-${actionId}`,
    });
    assert.equal(decision.blocked, true, actionId);
    assert.equal(decision.reason, 'pet-care-cat-mind-suppressed');
  }

  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'user',
    actionId: 'cat1_eat_snack',
  }).blocked, false);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'care-operation',
    actionId: 'cat1_play_yarn',
  }).blocked, false);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind',
    actionId: 'future_action',
  }).blocked, false);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_play_yarn',
    detail: { triggerTypes: ['chat_yarn_drag_completed'] },
  }).blocked, false);
});

test('stage 3 leaving the cat form or losing the service context restores ordinary Cat Mind', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, true);

  window.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: false, reason: 'cat-form-left' },
  }));
  // The app integration observes the same fact and explicitly releases the
  // mode gate. The mode contract itself does not own product readiness.
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: false,
    reason: 'cat-left',
  });
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_play_yarn',
  }).blocked, false);

  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  window.nekoPetCareMode.setMode('session-invalid', { reason: 'logout' });
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);

  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  window.nekoPetCareMode.updateContext({
    accountId: 'account-a',
    currentCharacterUid: 'role-a',
    boundRoleId: '',
    reason: 'snapshot-incomplete',
  });
  window.nekoPetCareMode.setMode('pet-unavailable', { reason: 'snapshot-incomplete' });
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);
});

test('stage 3 leaving compatibility requests source-filtered cleanup for prior Cat Mind runs', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  const cleanups = [];
  window.addEventListener('neko:pet-care:cleanup-request', (event) => cleanups.push(event.detail));

  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: false,
    reason: 'cat-form-left',
  });

  assert.equal(cleanups.length, 2);
  assert.deepEqual(Array.from(cleanups[0].actionIds), ACTION_IDS);
  assert.deepEqual(Array.from(cleanups[1].actionIds), ACTION_IDS);
  assert.equal(cleanups[1].source, 'cat_mind');
  assert.equal(cleanups[1].suppressed, false);
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);
});

test('stage 3 cleanup restores audio providers only after suppression is released', () => {
  const window = new EventTargetLike();
  let explorationAway = false;
  const stopped = [];
  const synced = [];
  window.nekoPetCareMode = { isExplorationAway: () => explorationAway };
  const context = {
    window,
    _NEKO_CAT_MIND_ACTION_IDS: {
      CAT2_NAP_FEEDBACK: 'cat2_nap_feedback',
      CAT3_SLEEP_FEEDBACK: 'cat3_sleep_feedback',
    },
    _nekoIdleSleepSoundState: { catMindActionId: 'cat2_nap_feedback' },
    _forEachNekoIdleReturnButton: () => {},
    _stopNekoIdleSleepSound: () => stopped.push('sleep'),
    _stopNekoIdleCat1AmbientSound: () => stopped.push('ambient'),
    _getActiveNekoIdleReturnTier: () => 'cat1',
    _syncNekoIdleSleepSoundForTier: tier => synced.push(['sleep', tier]),
    _syncNekoIdleCat1AmbientSoundForTier: tier => synced.push(['ambient', tier]),
  };
  const marker = '// Stage 0 pet-care compatibility cleanup.';
  vm.createContext(context);
  vm.runInContext(actionAdapterSource.slice(actionAdapterSource.indexOf(marker)), context);

  window.dispatchEvent(new CustomEventLike('neko:pet-care:cleanup-request', {
    detail: { source: 'cat_mind', suppressed: true, actionIds: ACTION_IDS },
  }));
  assert.deepEqual(stopped, ['sleep', 'ambient']);
  assert.deepEqual(synced, []);

  window.dispatchEvent(new CustomEventLike('neko:pet-care:cleanup-request', {
    detail: { source: 'cat_mind', suppressed: false, actionIds: ACTION_IDS },
  }));
  assert.deepEqual(synced, [['sleep', 'cat1'], ['ambient', 'cat1']]);

  explorationAway = true;
  window.dispatchEvent(new CustomEventLike('neko:pet-care:cleanup-request', {
    detail: { source: 'cat_mind', suppressed: false, actionIds: ACTION_IDS },
  }));
  assert.equal(synced.length, 2);
});

test('stage 3 mode exit cleans a running Cat Mind compatibility set', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  const cleanups = [];
  window.addEventListener('neko:pet-care:cleanup-request', (event) => cleanups.push(event.detail));

  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ['cat1_small_move'],
    reason: 'pet-care-ready',
  });
  window.nekoPetCareMode.setMode('session-invalid', { reason: 'logout' });

  assert.equal(cleanups.length, 2);
  assert.deepEqual(Array.from(cleanups[1].actionIds), ['cat1_small_move']);
  assert.equal(cleanups[1].source, 'cat_mind');
});

test('stage 3 rejects a late old-context response after a new account generation', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  enterAvailablePet(runtime);
  window.nekoPetCareMode.setCatMindCompatibility({
    suppressed: true,
    actionIds: ACTION_IDS,
    reason: 'pet-care-ready',
  });
  const oldContext = window.nekoPetCareMode.captureContext();

  window.nekoPetCareMode.updateContext({
    accountId: 'account-b',
    currentCharacterUid: 'role-b',
    boundRoleId: 'server-role-b',
    reason: 'account-switch',
  });
  window.nekoPetCareMode.setMode('pet-loading', { reason: 'account-switch' });
  assert.equal(window.nekoPetCareMode.isCurrentContext(oldContext), false);
  assert.equal(window.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_social_ping',
  }).blocked, false);
});

test('negative presentation is context fenced, supports coexistence and clears on identity change', () => {
  const runtime = createRuntime();
  const { window } = runtime;
  const changes = [];
  window.addEventListener('neko:pet-care:negative-presentation-change', event => {
    changes.push(event.detail);
  });
  enterAvailablePet(runtime);
  const accountAContext = window.nekoPetCareMode.captureContext();

  window.nekoPetCareMode.setNegativePresentation({
    sick: true,
    sulk: true,
    context: accountAContext,
    reason: 'complete-authoritative-snapshot',
  });
  let state = window.nekoPetCareMode.getState();
  assert.equal(state.negativePresentation.sick, true);
  assert.equal(state.negativePresentation.sulk, true);
  assert.equal(window.nekoPetCareMode.shouldSuppressHoverResponse(), true);

  window.nekoPetCareMode.updateContext({
    accountId: 'account-b',
    currentCharacterUid: 'role-b',
    boundRoleId: 'server-role-b',
    reason: 'account-switch',
  });
  state = window.nekoPetCareMode.getState();
  assert.equal(state.negativePresentation.sick, false);
  assert.equal(state.negativePresentation.sulk, false);
  assert.equal(window.nekoPetCareMode.shouldSuppressHoverResponse(), false);

  window.nekoPetCareMode.setNegativePresentation({
    sick: true,
    sulk: true,
    context: accountAContext,
    reason: 'late-account-a-snapshot',
  });
  state = window.nekoPetCareMode.getState();
  assert.equal(state.negativePresentation.sick, false);
  assert.equal(state.negativePresentation.sulk, false);
  assert.equal(changes.length, 2);
  assert.equal(changes[0].current.sick, true);
  assert.equal(changes[0].current.sulk, true);
  assert.equal(changes[1].current.sick, false);
  assert.equal(changes[1].current.sulk, false);
});

test('negative presentation affects only avatar feedback and keeps the PetCare entry independent', () => {
  assert.match(petCareSource, /function syncNegativePresentation\(reason\)/);
  assert.match(petCareSource, /negative\.sick\.active === true/);
  assert.match(petCareSource, /negative\.sulk\.active === true/);
  assert.match(actionAdapterSource, /neko:pet-care:negative-presentation-change/);
  assert.match(actionAdapterSource, /data-neko-pet-care-sick/);
  assert.match(actionAdapterSource, /data-neko-pet-care-sulk/);
  assert.match(returnMethodsSource, /shouldSuppressHoverResponse/);
  assert.match(idleCssSource, /data-neko-pet-care-sick="true"/);
});

test('stage 3 app integration requires account, bound role, complete snapshot and cat form', () => {
  assert.match(petCareSource, /function isPetCareCompatibilityReady\(\)/);
  assert.match(petCareSource, /state\.authStatus\s*===\s*['"]logged-in['"]/);
  assert.match(petCareSource, /state\.bindingStatus\s*===\s*['"]bound['"]/);
  assert.match(petCareSource, /state\.serviceStatus\s*===\s*['"]ready['"]/);
  assert.match(petCareSource, /!!state\.snapshot/);
  assert.match(petCareSource, /state\.catLocalActive/);
  assert.match(petCareSource, /setCatMindCompatibility\s*\(/);
  assert.match(petCareSource, /CAT_MIND_ACTION_IDS/);
});

test('stage 3 keeps the feature hint device-local, visible and informational', () => {
  assert.match(petCareSource, /FEATURE_HINT_STORAGE_KEY/);
  assert.match(petCareSource, /window\.localStorage\.getItem\(FEATURE_HINT_STORAGE_KEY\)/);
  assert.match(petCareSource, /window\.localStorage\.setItem\(FEATURE_HINT_STORAGE_KEY,\s*['"]1['"]\)/);
  assert.match(petCareSource, /showPetCareToast\(['"]已开启养宠功能/);
  assert.doesNotMatch(petCareSource, /emit\(['"]neko:pet-care:feature-hint/);
  assert.match(petCareSource, /已开启养宠功能/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const apiSource = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care-api.js'), 'utf8');
const clientSource = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care-client.js'), 'utf8');
const source = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care.js'), 'utf8');
const pendingWriteSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-pending-writes.js'),
  'utf8',
);
const modeSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-mode.js'),
  'utf8',
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

class ElementLike extends EventTargetLike {
  constructor(tagName) {
    super();
    this.tagName = tagName.toUpperCase();
    this.id = '';
    this.className = '';
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.textContent = '';
    this.children = [];
    this.parentElement = null;
    this.offsetWidth = 184;
    this.offsetHeight = 42;
    this._rect = { left: 100, top: 100, right: 300, bottom: 400, width: 200, height: 300 };
  }
  appendChild(child) {
    child.parentElement = this;
    if (child.className === 'neko-pet-care-entry-button') child.offsetWidth = 54;
    if (child.className === 'neko-pet-care-entry-panel') child.offsetWidth = 304;
    this.children.push(child); return child;
  }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name] || null; }
  getBoundingClientRect() { return this._rect; }
  matches(selector) { return selector === ':hover' ? false : false; }
  querySelector() { return null; }
}

class FakeLockManager {
  constructor() {
    this.active = new Set();
    this.queues = new Map();
  }

  request(name, options, callback) {
    if (typeof options === 'function') {
      callback = options;
      options = {};
    }
    if (options.ifAvailable === true && this.active.has(name)) {
      return Promise.resolve().then(() => callback(null));
    }
    return new Promise((resolve, reject) => {
      if (!this.queues.has(name)) this.queues.set(name, []);
      this.queues.get(name).push({ callback, resolve, reject });
      this.#drain(name);
    });
  }

  #drain(name) {
    const queue = this.queues.get(name);
    if (this.active.has(name) || !queue || queue.length === 0) return;
    const request = queue.shift();
    this.active.add(name);
    Promise.resolve()
      .then(() => request.callback({ name, mode: 'exclusive' }))
      .then(request.resolve, request.reject)
      .finally(() => {
        this.active.delete(name);
        this.#drain(name);
      });
  }
}

function createHarness(fetchImpl, identityValue = { uid: 'local-role-a', name: '小猫 A' }, pathname = '/', storage = null) {
  const window = new EventTargetLike();
  window.location = {
    pathname,
    origin: 'http://127.0.0.1',
    href: 'http://127.0.0.1' + pathname,
  };
  window.innerWidth = 1280;
  window.innerHeight = 800;
  window.setTimeout = setTimeout;
  window.clearTimeout = clearTimeout;
  // The production refresh loop is covered by explicit refresh calls here;
  // avoid keeping the test process alive for its real 30-second interval.
  window.setInterval = () => 0;
  window.clearInterval = () => {};
  if (storage && typeof storage.monotonicNow === 'function') {
    window.performance = { now: storage.monotonicNow };
  }
  window.getComputedStyle = () => ({ display: 'block', visibility: 'visible', opacity: '1' });
  window.open = () => ({ closed: false });
  window.navigator = storage && storage.locksUnavailable === true
    ? {}
    : { locks: storage && storage.lockManager || new FakeLockManager() };
  const localStorageValues = storage && storage.localStorageValues || new Map();
  window.localStorage = {
    getItem(key) { return localStorageValues.has(key) ? localStorageValues.get(key) : null; },
    setItem(key, value) { localStorageValues.set(key, String(value)); },
    removeItem(key) { localStorageValues.delete(key); },
  };
  const sessionStorageValues = storage && storage.sessionStorageValues || new Map();
  window.sessionStorage = {
    getItem(key) { return sessionStorageValues.has(key) ? sessionStorageValues.get(key) : null; },
    setItem(key, value) { sessionStorageValues.set(key, String(value)); },
    removeItem(key) { sessionStorageValues.delete(key); },
  };
  window.appChatAvatarState = { getIdentity: () => identityValue };
  window.NekoPetCareModeContract = { MODES: {
    SESSION_INVALID: 'session-invalid', PET_LOADING: 'pet-loading',
    PET_AVAILABLE: 'pet-available', PET_UNAVAILABLE: 'pet-unavailable',
    EXPLORATION_AWAY: 'exploration-away',
  } };
  window.nekoPetCareMode = {
    state: { generation: 0, mode: 'session-invalid', catMindSuppressed: false, catLocalActive: false,
      negativePresentation: { sick: false, sulk: false } },
    setMode(mode, detail = {}) {
      this.state.mode = mode;
      this.state.reason = detail.reason || '';
      return this.state;
    },
    setCatMindCompatibility(detail = {}) {
      this.state.catMindSuppressed = detail.suppressed === true;
      this.state.blockedCatMindActionIds = this.state.catMindSuppressed
        ? (detail.actionIds || null) : null;
      return this.state;
    },
    setNegativePresentation(detail = {}) {
      this.state.negativePresentation = {
        sick: detail.sick === true,
        sulk: detail.sulk === true,
      };
      return this.state;
    },
    setCatLocalActive(active) {
      this.state.catLocalActive = active === true;
      return this.state;
    },
    updateContext(detail) {
      this.state.generation += 1;
      this.state.context = { ...detail };
      return { generation: this.state.generation };
    },
    captureContext() {
      const context = this.state.context || {};
      return {
        generation: this.state.generation,
        accountId: context.accountId || '',
        currentCharacterUid: context.currentCharacterUid || '',
        boundRoleId: context.boundRoleId || '',
      };
    },
    getState() { return this.state; },
  };

  const body = new ElementLike('body');
  const live2d = new ElementLike('div');
  live2d.id = 'live2d-container';
  const document = new EventTargetLike();
  document.readyState = 'complete';
  document.body = body;
  document.visibilityState = 'visible';
  document.createElement = tag => new ElementLike(tag);
  document.getElementById = id => id === 'live2d-container' ? live2d : body.children.find(child => child.id === id) || null;
  document.querySelector = selector => selector === '#pngtuber-container .pngtuber-image' || selector.includes('edge-peek') ? null : null;
  document.addEventListener = EventTargetLike.prototype.addEventListener;
  document.dispatchEvent = EventTargetLike.prototype.dispatchEvent;
  class CustomEventLike {
    constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
  }
  window.CustomEvent = CustomEventLike;
  const baseFetcher = fetchImpl || (async () => ({ ok: true, json: async () => ({ logged_in: false, local_user_id: null }) }));
  const fetcher = async (url, options = {}) => {
    if (String(url).endsWith('/api/pet-care/session/resume')) {
      let response;
      try {
        response = await baseFetcher(url, options);
      } catch (error) {
        if (!error || !/unexpected request/i.test(String(error.message || error))) throw error;
        return { ok: true, status: 200, json: async () => resumeEnvelope() };
      }
      if (response && response.ok !== false && typeof response.json === 'function') {
        const body = await response.json();
        // Older scenario fixtures use their generic non-resume response for
        // endpoints outside the behavior under test. Supply the formal
        // resume envelope only when that body contains no resume field. An
        // explicitly incomplete resume envelope remains observable and must
        // stay pending in the product code.
        if (body && typeof body === 'object' && !Array.isArray(body) &&
            !['request_id', 'server_time', 'resume_not_before', 'needs_binding',
              'pet_refresh_required', 'settlement_id'].some(key => Object.hasOwn(body, key))) {
          return { ...response, status: response.status || 200,
            json: async () => resumeEnvelope() };
        }
        return { ...response, json: async () => body };
      }
      return response;
    }
    if (!String(url).includes('/api/pet-care/context')) return baseFetcher(url, options);
    try {
      const response = await baseFetcher(url, options);
      if (!response || response.ok !== false) return response;
      const body = typeof response.json === 'function' ? await response.json() : null;
      if (!body || body.detail !== 'unexpected') return response;
    } catch (error) {
      if (!error || !/unexpected request/i.test(String(error.message || error))) throw error;
    }
    // Most tests predate the production two-step disclosure contract and
    // mock only the snapshot endpoint. Supply only the bound discovery fact;
    // the product code must still perform and validate the separate GET.
    return { ok: true, status: 200, json: async () => ({
      binding_status: 'bound', needs_binding: false,
    }) };
  };
  window.fetch = fetcher;
  window.AbortController = AbortController;
  // This harness exercises panel behavior with an in-memory fetch fixture.
  // The transport capability contract has dedicated adapter/gateway tests.
  const context = vm.createContext({ window, document, CustomEvent: CustomEventLike,
    URL, console, fetch: fetcher });
  if (storage && storage.useRealPetCareMode === true) {
    vm.runInContext(modeSource, context, { filename: 'app-pet-care-mode.js' });
  }
  vm.runInContext(apiSource, context, { filename: 'app-pet-care-api.js' });
  window.NekoPetCareApi = window.NekoPetCareApi.createTransport({
    fetch: fetcher,
    localCapability: false,
  });
  vm.runInContext(clientSource, context, { filename: 'app-pet-care-client.js' });
  vm.runInContext(pendingWriteSource, context, { filename: 'app-pet-care-pending-writes.js' });
  vm.runInContext(source, context, { filename: 'app-pet-care.js' });
  return { window, document, body, live2d };
}

async function waitFor(predicate, timeoutMs = 250) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('timed out waiting for test state');
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}

function resumeEnvelope(overrides = {}) {
  return {
    request_id: 'request-resume-1',
    server_time: '2026-10-21T00:00:00Z',
    resume_not_before: '2026-10-21T00:00:00Z',
    needs_binding: false,
    pet_refresh_required: false,
    ...overrides,
  };
}

function pendingWriteStorageKey(accountId = 'account-a') {
  return `neko.pet-care.pending-write-account.v1:${encodeURIComponent(accountId)}`;
}

function explorationLeaseStorageKey(accountId) {
  return `neko.pet-care.exploration-lease.v1:${encodeURIComponent(accountId)}`;
}

function pendingWriteRecord(storage, accountId = 'account-a') {
  const raw = storage.localStorageValues.get(pendingWriteStorageKey(accountId));
  return raw ? JSON.parse(raw).record : null;
}

function enterCatForm(h) {
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: true, reason: 'cat-form-entered' },
  }));
}

function installVisibleCatAnchor(h) {
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({
      visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 },
    }),
  };
}

function dispatchYarnActionLifecycle(h, activityId, result = 'done') {
  const requestId = `cat-mind:cat1_play_yarn:${activityId}:request`;
  const runId = `cat1_play_yarn:${activityId}:run`;
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-request', { detail: {
    source: 'cat_mind', actionId: 'cat1_play_yarn', requestId,
    detail: { activityId, triggerTypes: ['chat_yarn_drag_completed'] },
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:state-change', { detail: {
    reason: 'action-started', detail: {
      source: 'cat_mind', actionId: 'cat1_play_yarn', activityId, requestId, runId,
    },
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result, source: 'cat_mind',
    detail: { activityId, requestId, runId },
  } }));
}

function invokePresentationTerminal(entry, result, reason) {
  entry.onTerminal(result, {
    actionId: 'play_teaser_wand', source: entry.source,
    activityId: entry.activityId, requestId: entry.requestId, runId: entry.runId,
    reason,
  });
}

function entryOf(h) {
  return h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
}

function panelOf(h) {
  const entry = entryOf(h);
  return entry && entry.children[0];
}

function loginHintOf(h) {
  const entry = entryOf(h);
  return entry && entry.children.find(child => child.className === 'neko-pet-care-login-hint');
}

function gridOf(h) {
  const grid = panelOf(h).children.find(child => child.className === 'neko-pet-care-stats');
  // The stage 9 surface adds a heading row and a unit node to each stat.
  // Keep this helper semantic so the older stage behavior assertions can
  // continue to verify the values without depending on the new layout order.
  return {
    hidden: grid.hidden,
    children: grid.children.slice(1, 6).map(stat => ({
      children: [stat.children[0].children[0], stat.children[0].children[1].children[0]],
    })),
  };
}

function progressStatOf(h) {
  return panelOf(h).children
    .find(child => child.className === 'neko-pet-care-stats')
    .children.find(child => String(child.className).includes('neko-pet-care-progress-stat'));
}

function progressUnlockPanelOf(h) {
  return panelOf(h).children
    .find(child => child.className === 'neko-pet-care-stats')
    .children.find(child => child.className === 'neko-pet-care-progress-unlock-panel');
}

function actionsOf(h) {
  return panelOf(h).children.find(child => child.className === 'neko-pet-care-actions');
}

function detailsOf(h) {
  return panelOf(h).children.find(child => child.className === 'neko-pet-care-action-details');
}

function taskOf(h) {
  const panel = panelOf(h);
  const content = panel.children.find(child => child.className === 'neko-pet-care-content');
  return content && content.children.find(child => child.className === 'neko-pet-care-task neko-pet-care-module');
}

function inventoryOf(h) {
  const panel = panelOf(h);
  const content = panel.children.find(child => child.className === 'neko-pet-care-content');
  return content && content.children.find(child => child.className === 'neko-pet-care-module neko-pet-care-inventory-module');
}

function lettersOf(h) {
  const panel = panelOf(h);
  const content = panel.children.find(child => child.className === 'neko-pet-care-content');
  return content && content.children.find(child => child.className === 'neko-pet-care-module neko-pet-care-letters-module');
}

function footerOf(h) {
  return panelOf(h).children.find(child => child.className === 'neko-pet-care-panel-footer');
}

function overviewOf(h) {
  return panelOf(h).children.find(child => child.className === 'neko-pet-care-content')
    .children.find(child => child.className.includes('neko-pet-care-overview'));
}

function navButtonOf(h, view) {
  return panelOf(h).children
    .find(child => child.className === 'neko-pet-care-rail')
    .children.find(child => child.dataset.view === view);
}

async function openPanel(h) {
  if (h.window.nekoPetCareEntry.getState().panelOpen) return;
  h.window.nekoCatInteractionAnchor = h.window.nekoCatInteractionAnchor || {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 450));
}

async function createReadyHarness() {
  const snapshot = stage4Snapshot();
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/tasks')) {
      return { ok: true, status: 200, json: async () => ({ snapshot }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  return h;
}

function selectView(h, view) {
  const button = navButtonOf(h, view);
  assert.ok(button, `missing pet-care view: ${view}`);
  button.dispatchEvent({ type: 'click', stopPropagation() {} });
}

async function openTasks(h) {
  await openPanel(h);
  selectView(h, 'tasks');
}

function taskRowTitle(row) {
  return row.children[1].children[0].textContent;
}

function taskRowDescendant(row, className) {
  const visit = node => {
    if (!node) return null;
    if (node.className === className) return node;
    for (const child of node.children || []) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  };
  return visit(row);
}

function taskRowProgress(row) {
  return taskRowDescendant(row, 'neko-pet-care-task-progress-value');
}

function taskRowProgressSummary(row) {
  return taskRowDescendant(row, 'neko-pet-care-task-progress-summary');
}

function businessPostCount(calls) {
  return calls.filter(item => item.method === 'POST' &&
    item.url !== '/api/pet-care/session/resume').length;
}

const RAW_PLAYER_DIAGNOSTIC = 'HTTP 503\nTraceback: File /srv/pet_care/service.py line 91';

function assertSafePlayerFailure(node, expectedText) {
  assert.ok(node, 'missing player-visible failure node');
  assert.equal(node.hidden, false);
  assert.equal(node.textContent, expectedText);
  assert.equal(node.textContent.includes(RAW_PLAYER_DIAGNOSTIC), false);
  assert.doesNotMatch(node.textContent, /HTTP\s*503|Traceback|\/srv\/|service\.py|pet_care_/);
}

test('stage 1 creates an independent entry surface without model floating buttons', async () => {
  const h = createHarness();
  await new Promise(resolve => setTimeout(resolve, 0));
  const entry = entryOf(h);
  assert.ok(entry);
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-out');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
  assert.equal(entry.dataset.status, 'logged-out');
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(entry.dataset.available, 'false');
  assert.equal(h.body.children.some(child => /floating-buttons/.test(child.id)), false);
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(entry.dataset.available, 'false');
  assert.equal(entry.dataset.loginHint, 'true');
  assert.equal(entry.dataset.expanded, 'false');
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
});

test('chat follower surface does not create a second pet-care UI or auth owner', async () => {
  const h = createHarness(null, { uid: 'local-role-a', name: '小猫 A' }, '/chat');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.window.nekoPetCareSurface.kind, 'chat-follower');
  assert.equal(h.window.nekoPetCareSurface.ownsUi, false);
  assert.equal(h.window.nekoPetCareSurface.ownsLifecycle, false);
  assert.equal(h.window.nekoPetCareSurface.ownsNotices, false);
  assert.equal(h.body.children.some(child => child.id === 'neko-pet-care-entry-overlay'), false);
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'unknown');
});

test('logged-out cat form shows one direct unified-account hint per cat episode', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: false, local_user_id: null }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().authStatus === 'logged-out');
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));

  const entry = entryOf(h);
  const hint = loginHintOf(h);
  let state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.loginHintVisible, true);
  assert.equal(state.loginHintShownForCatEpisode, true);
  assert.equal(hint.hidden, false);
  assert.equal(entry.dataset.loginHint, 'true');
  assert.equal(entry.dataset.expanded, 'false');
  assert.equal(state.panelOpen, false);

  hint.children[2].dispatchEvent({ type: 'click', stopPropagation() {} });
  state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.loginHintVisible, false);
  assert.equal(state.loginHintShownForCatEpisode, true);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: true, reason: 'duplicate-cat-active' },
  }));
  assert.equal(h.window.nekoPetCareEntry.getState().loginHintVisible, false);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: false, reason: 'cat-form-left' },
  }));
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.window.nekoPetCareEntry.getState().loginHintVisible, true);
});

test('unknown account state never claims that the user is logged out', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) throw new Error('auth-unavailable');
    throw new Error('unexpected request: ' + url);
  });
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().authStatus === 'unknown');
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.loginHintVisible, false);
  assert.equal(state.loginHintShownForCatEpisode, false);
});

test('stage 1 only floats beside the visible cat anchor after the hover delay', async () => {
  const h = await createReadyHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  assert.equal(h.window.nekoPetCareEntry.getState().hoverVisible, false);
  await new Promise(resolve => setTimeout(resolve, 430));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.hoverVisible, true);
  const entry = entryOf(h);
  assert.equal(entry.dataset.available, 'true');
  assert.equal(entry.dataset.panelSide, undefined);
  assert.equal(entry.style.left, '-180px');
});

test('stage 1 opens the single surface after the semantic cat hover delay', async () => {
  const h = await createReadyHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = entryOf(h);
  assert.equal(entry.dataset.visible, 'true');
  assert.equal(entry.dataset.expanded, 'true');
  assert.equal(entry.dataset.panelOpen, 'true');
  assert.equal(entry.children[0].className, 'neko-pet-care-surface');
  h.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.equal(entry.dataset.expanded, 'false');
});

test('stage 2 exposes an obvious panel close button and keeps operation labels at category level', async () => {
  const h = await createReadyHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = entryOf(h);
  const panel = panelOf(h);
  const close = panel.children[0];
  assert.equal(close.textContent, '×');
  assert.equal(close.getAttribute('aria-label'), '关闭养宠面板');
  await openPanel(h);
  assert.equal(entry.dataset.expanded, 'true');
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'false');
  assert.deepEqual(actionsOf(h).children.map(button => button.children[1].textContent),
    ['喂食', '清洁', '陪玩', '探索']);
  assert.equal(detailsOf(h).className, 'neko-pet-care-action-details');
  assert.equal(footerOf(h).className, 'neko-pet-care-panel-footer');
});

test('open pet-care surface exposes close and leaves only after a cancellable pointer grace period', async () => {
  const snapshot = stage4Snapshot();
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/tasks')) {
      return { ok: true, status: 200, json: async () => ({ snapshot }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  const entry = entryOf(h);
  const close = panelOf(h).children[0];
  assert.equal(close.hidden, false);
  assert.equal(close.getAttribute('aria-label'), '关闭养宠面板');

  entry.dispatchEvent({ type: 'mouseleave' });
  await new Promise(resolve => setTimeout(resolve, 350));
  entry.dispatchEvent({ type: 'mouseenter' });
  await new Promise(resolve => setTimeout(resolve, 400));
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);

  entry.dispatchEvent({ type: 'mouseleave' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === false, 900);
  assert.equal(entry.dataset.expanded, 'false');

  await openPanel(h);
  entry.dispatchEvent({ type: 'pointerdown' });
  entry.dispatchEvent({ type: 'mouseleave' });
  await new Promise(resolve => setTimeout(resolve, 750));
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  h.document.dispatchEvent({ type: 'pointerup' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === false, 900);

  await openPanel(h);
  entry.dispatchEvent({ type: 'pointerdown' });
  entry.dispatchEvent({ type: 'mouseleave' });
  h.document.dispatchEvent({ type: 'pointercancel' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === false, 900);

  await openPanel(h);
  entry.dispatchEvent({ type: 'pointerdown' });
  entry.dispatchEvent({ type: 'mouseleave' });
  h.document.dispatchEvent({
    type: 'pointermove', clientX: 900, clientY: 700, pointerType: 'mouse', buttons: 0,
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === false, 900);

  await openPanel(h);
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
});

test('active exploration can collapse and reopen from the shared nest anchor without recall', async () => {
  const calls = [];
  const active = {
    exploration_id: 'explore-collapsible-panel', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  let snapshot = stage4Snapshot({ exploration: active });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);

  const entry = entryOf(h);
  const close = panelOf(h).children[0];
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  assert.equal(close.hidden, false);
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.exploration_id, active.exploration_id);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, true);
  assert.equal(businessPostCount(calls), 0);

  await h.window.nekoPetCareEntry.refreshBinding('test-same-exploration-refresh');
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);

  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === true, 600);
  assert.equal(entry.dataset.expanded, 'true');

  entry.dispatchEvent({ type: 'mouseleave' });
  await new Promise(resolve => setTimeout(resolve, 350));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 400));
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);

  entry.dispatchEvent({ type: 'mouseleave' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === false, 900);
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === true, 600);

  h.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await waitFor(() => h.window.nekoPetCareEntry.getState().panelOpen === true, 600);
  h.document.dispatchEvent({ type: 'click' });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);

  const nextActive = { ...active, exploration_id: 'explore-collapsible-panel-next' };
  snapshot = stage4Snapshot({ exploration: nextActive });
  h.window.nekoPetCareEntry.setBindingResult({
    account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
    pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
  });
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.exploration_id, nextActive.exploration_id);
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  assert.equal(businessPostCount(calls), 0);
});

test('a stale binding read cannot restore an exploration after a confirmed terminal write', async () => {
  const active = {
    exploration_id: 'explore-binding-race', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const terminal = { ...active, status: 'completed', active: false };
  const activeSnapshot = stage4Snapshot({ exploration: active });
  const terminalSnapshot = stage4Snapshot({ exploration: terminal });
  let bindingReads = 0;
  let resolveStaleRead;
  const staleRead = new Promise(resolve => { resolveStaleRead = resolve; });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-binding-race/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: terminalSnapshot, reward: { drops: [] },
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: activeSnapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      bindingReads += 1;
      if (bindingReads > 1) return staleRead;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: activeSnapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null &&
    h.window.nekoPetCareEntry.getState().bindingPending === false);
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-in');
  assert.equal(h.window.nekoPetCareEntry.getState().currentCharacterUid, 'local-role-a');

  const bindingReadBaseline = bindingReads;
  const pendingRead = h.window.nekoPetCareEntry.refreshBinding('test-stale-binding-read');
  await waitFor(() => bindingReads > bindingReadBaseline);
  await h.window.nekoPetCareEntry.completeExploration();
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);

  resolveStaleRead({ ok: true, status: 200, json: async () => ({
    account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
    pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: activeSnapshot,
  }) });
  await pendingRead;
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.status, 'completed');
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
});

test('a hidden logged-out pet-care surface can still cancel an in-flight OAuth attempt safely', async () => {
  let openedPopup = null;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/start')) {
      return { ok: true, json: async () => ({ auth_url: 'https://community.example/login', state: 'oauth-state' }) };
    }
    if (url.includes('/api/card-drop/oauth/completion')) {
      return { ok: true, json: async () => ({ logged_in: false }) };
    }
    return { ok: true, json: async () => ({ logged_in: false, local_user_id: null }) };
  });
  h.window.open = () => {
    openedPopup = { closed: false, close() { this.closed = true; } };
    return openedPopup;
  };
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = entryOf(h);
  const close = panelOf(h).children[0];
  const loginPromise = h.window.nekoPetCareEntry.startLogin();
  await waitFor(() => h.window.nekoPetCareEntry.getState().oauthPending);
  await waitFor(() => openedPopup !== null);
  entry.dispatchEvent({ type: 'mouseleave' });
  await new Promise(resolve => setTimeout(resolve, 750));
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(openedPopup.closed, false);
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  await loginPromise;
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.oauthPending, false);
  assert.equal(state.reason, 'oauth-cancelled-by-user');
  assert.equal(openedPopup.closed, true);
});

test('stage 1 translates the fixed 760x560 scene with the semantic anchor without side switching', async () => {
  const h = await createReadyHarness();
  h.window.innerWidth = 700;
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 520, top: 100, right: 660, bottom: 300, width: 140, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 580, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.equal(entry.dataset.panelSide, undefined);
  assert.equal(entry.style.left, '210px');
});

test('stage 1 tracks current role separately and accepts only a service binding result', async () => {
  const h = createHarness(async () => ({ ok: true, json: async () => ({
    logged_in: true,
    local_user_id: 'account-a',
    session_generation: 7,
    user: { display_name: '账号 A' },
  }) }));
  await new Promise(resolve => setTimeout(resolve, 0));
  const before = h.window.nekoPetCareEntry.getState();
  assert.equal(before.authStatus, 'logged-in');
  assert.equal(before.sessionGeneration, '7');
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', {
    detail: { character_uid: 'local-role-b', character_name: '小猫 B', reason: 'switch-commit' },
  }));
  const changed = h.window.nekoPetCareEntry.getState();
  assert.equal(changed.currentCharacterUid, 'local-role-b');
  assert.equal(changed.currentCharacterName, '小猫 B');
  assert.ok(changed.generation > before.generation);
  assert.equal(changed.bindingStatus, 'unknown');
  const rejected = h.window.nekoPetCareEntry.setBindingResult({
    status: 'bound', boundRoleId: 'wrong-account-role', accountId: 'account-b',
    character_uid: 'local-role-b',
  });
  assert.equal(rejected.bindingStatus, 'unknown');
  const currentGeneration = h.window.nekoPetCareEntry.getState().generation;
  const stale = h.window.nekoPetCareEntry.setBindingResult({
    status: 'bound', boundRoleId: 'stale-role', accountId: 'account-a',
    character_uid: 'local-role-b', generation: currentGeneration - 1,
  });
  assert.equal(stale.bindingStatus, 'unknown');
  h.window.nekoPetCareEntry.setBindingResult({
    status: 'bound', boundRoleId: 'stable-role-b', accountId: 'account-a', character_uid: 'local-role-b',
    generation: currentGeneration,
  });
  const bound = h.window.nekoPetCareEntry.getState();
  assert.equal(bound.bindingStatus, 'bound');
  assert.equal(bound.bindingRoleId, 'stable-role-b');
});

test('stage 8 accepts the local OAuth login session id as the context fence', async () => {
  const h = createHarness(async () => ({ ok: true, json: async () => ({
    logged_in: true,
    local_user_id: 'account-a',
    session_id: 'opaque-login-session-a',
  }) }));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.window.nekoPetCareEntry.getState().sessionGeneration, 'opaque-login-session-a');
});

test('account activity resume is role-free and only consumes verified waiting interactions', async () => {
  const resumeBodies = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumeBodies.push(JSON.parse(options.body));
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'needs-binding',
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => resumeBodies.length === 1);
  assert.deepEqual(Object.keys(resumeBodies[0]), ['idempotency_key']);
  assert.ok(resumeBodies[0].idempotency_key);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-extend', {
    detail: { phase: 'reply', source: 'assistant', content: 'must not be forwarded' },
  }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-extend', {
    detail: { phase: 'speaking', source: 'assistant' },
  }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-extend', {
    detail: { phase: 'settling', source: 'assistant' },
  }));
  h.document.dispatchEvent({ type: 'click' });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(resumeBodies.length, 1);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-start', {
    detail: { phase: 'waiting', source: 'text', content: 'private text' },
  }));
  await waitFor(() => resumeBodies.length === 2);
  assert.deepEqual(Object.keys(resumeBodies[1]), ['idempotency_key']);
});

test('resume uses the server hint only as monotonic traffic throttling', async () => {
  let resumes = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumes += 1;
      return { ok: true, status: 200, json: async () => resumeEnvelope({
        resume_not_before: '2026-10-21T00:01:00Z',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'needs-binding',
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => resumes === 1);
  await h.window.nekoPetCareEntry.requestAccountResume('test-early-event');
  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-start', {
    detail: { phase: 'waiting' },
  }));
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(resumes, 1);
});

test('a newer auth generation gets one trailing resume behind the old in-flight request', async () => {
  let oauthReads = 0;
  let resumeCalls = 0;
  let releaseFirst;
  const firstResume = new Promise(resolve => { releaseFirst = resolve; });
  const resumeBody = resumeEnvelope();
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      oauthReads += 1;
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: oauthReads === 1 ? 'session-a' : 'session-b',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumeCalls += 1;
      if (resumeCalls === 1) {
        await firstResume;
      }
      return { ok: true, status: 200, json: async () => resumeBody };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'needs-binding',
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => resumeCalls === 1);
  const refreshed = h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
  await waitFor(() => oauthReads === 2);
  releaseFirst();
  await refreshed;
  await waitFor(() => resumeCalls === 2);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(resumeCalls, 2);
});

test('reauth reconciles an unknown resume key before recording the new generation', async () => {
  let oauthReads = 0;
  const resumeKeys = [];
  let releaseUnknown;
  const unknown = new Promise(resolve => { releaseUnknown = resolve; });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      oauthReads += 1;
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: oauthReads === 1 ? 'session-a' : 'session-b',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumeKeys.push(JSON.parse(options.body).idempotency_key);
      if (resumeKeys.length === 1) {
        await unknown;
        throw new TypeError('simulated response loss');
      }
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'needs-binding',
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => resumeKeys.length === 1);
  const refreshed = h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
  await waitFor(() => oauthReads === 2);
  releaseUnknown();
  await refreshed;
  await waitFor(() => resumeKeys.length === 3);
  assert.equal(resumeKeys[1], resumeKeys[0]);
  assert.notEqual(resumeKeys[2], resumeKeys[0]);
});

test('an incomplete resume envelope keeps the account lane pending and blocks pet writes', async () => {
  let resumes = 0;
  let carePosts = 0;
  const snapshot = stage4Snapshot({ care_actions: { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } } });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumes += 1;
      return { ok: true, status: 200, json: async () => resumes === 1
        ? ({ request_id: 'incomplete-resume' }) : resumeEnvelope() };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      carePosts += 1;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null &&
    h.window.nekoPetCareEntry.getState().pendingWrite?.kind === 'session-resume');
  enterCatForm(h);
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);
  assert.equal(carePosts, 0);
  await h.window.nekoPetCareEntry.retryPendingWrite();
  assert.equal(resumes, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWrite, null);
});

test('resume may restore a bound exploration snapshot while the cat form is inactive', async () => {
  const snapshot = stage4Snapshot({
    exploration: {
      exploration_id: 'resume-away', status: 'active', active: true,
      started_at: '2026-10-21T00:00:00Z', ends_at: '2026-10-21T01:00:00Z',
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope({
        pet_refresh_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.catLocalActive, false);
  assert.equal(state.petRefreshRequired, false);
  assert.equal(state.exploration.exploration_id, 'resume-away');
  assert.equal(entryOf(h).dataset.available, 'true');
});

test('resume refresh latch survives a non-bound role without exposing pet state', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope({
        pet_refresh_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'unbound',
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.petRefreshRequired, true);
  assert.equal(state.snapshot, null);
  assert.equal(entryOf(h).dataset.available, 'false');
});

test('stage 1 resolves first binding through the service instead of waiting for manual injection', async () => {
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', headers: options.headers, body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'needs-binding' }) };
    }
    if (url.includes('/api/pet-care/bind')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'bound', bound_role_id: 'server-role-a',
        operation_result: { status: 'confirmed' } }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(h.window.nekoPetCareEntry.getState().bindingStatus, 'needs-binding');
  h.window.NekoPetCareRoleAliasSync = { rememberOwner: () => Promise.resolve() };
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  assert.equal(entryOf(h).dataset.available, 'true');
  await h.window.nekoPetCareEntry.bindCurrentRole();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.bindingRoleId, 'server-role-a');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.ok(calls.some(item => item.url.includes('/api/pet-care/context')));
  const bindCall = calls.find(item => item.url.includes('/api/pet-care/bind') && item.method === 'POST');
  assert.ok(bindCall);
  const bindBody = JSON.parse(bindCall.body);
  assert.equal(bindBody.character_uid, 'local-role-a');
  assert.ok(bindBody.idempotency_key);
  assert.equal(bindCall.headers['Idempotency-Key'], bindBody.idempotency_key);
  assert.equal(Object.hasOwn(bindBody, 'expected_pet_id'), false);
  assert.equal(Object.hasOwn(bindBody, 'expected_binding_revision'), false);
  assert.equal(Object.hasOwn(bindBody, 'expected_state_version'), false);
});

test('official role rebind uses both revisions and retains the same pet identity', async () => {
  const calls = [];
  const reboundSnapshot = formalSnapshot({
    bound_role_id: 'server-role-b', binding_revision: 8, state_version: 13,
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body, headers: options.headers });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope({
        pet_refresh_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 7, state_version: 12,
        can_rebind: true,
      }) };
    }
    if (url.includes('/api/pet-care/bind')) {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-bind-1', correlation_id: 'correlation-bind-1',
        server_time: '2026-10-21T00:00:00Z',
        activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', kind: 'official_rebind' },
        binding_revision: 8, state_version: 13, config_revision: 'config-1',
        snapshot_required: false, snapshot: reboundSnapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: false, status: 404, json: async () => ({ detail: 'context-only' }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  h.window.NekoPetCareRoleAliasSync = { rememberOwner: () => Promise.resolve() };
  const unboundState = h.window.nekoPetCareEntry.getState();
  assert.equal(unboundState.canRebind, true);
  assert.equal(unboundState.snapshot, null);
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  assert.equal(entryOf(h).dataset.available, 'true');
  const profile = panelOf(h).children.find(child => child.className === 'neko-pet-care-content')
    .children.find(child => child.className === 'neko-pet-care-module neko-pet-care-profile-module');
  assert.equal(profile.hidden, true);
  const visibleProfileValues = profile.children[1].children.map(row => row.children[1].textContent);
  assert.equal(visibleProfileValues.includes('pet-a'), false);
  assert.equal(visibleProfileValues.includes('7'), false);
  assert.equal(visibleProfileValues.includes('12'), false);
  let confirmations = 0;
  h.window.showConfirm = async () => { confirmations += 1; return true; };
  const footer = panelOf(h).children.find(child => child.className === 'neko-pet-care-panel-footer');
  const bindButton = footer.children.find(child => child.className === 'neko-pet-care-bind-button');
  assert.equal(bindButton.hidden, false);
  assert.equal(bindButton.textContent, '换绑到当前角色');
  bindButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  // This file runs in parallel with the other frontend suites. The click goes
  // through an async confirmation before the durable write is prepared, so
  // give this integration boundary enough wall-clock time under CI load.
  await waitFor(() => calls.some(call => call.url.includes('/api/pet-care/bind')), 1000);
  const bindCall = calls.find(call => call.url.includes('/api/pet-care/bind'));
  assert.equal(confirmations, 1);
  const request = JSON.parse(bindCall.body);
  assert.equal(request.character_uid, 'local-role-a');
  assert.equal(request.expected_pet_id, 'pet-a');
  assert.equal(request.expected_binding_revision, 7);
  assert.equal(request.expected_state_version, 12);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.petId, 'pet-a');
  assert.equal(state.bindingRevision, 8);
  assert.equal(state.petStateVersion, 13);
});

test('official role rebind preserves its alias context and original rejection without snapshot hydration', async () => {
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 7, state_version: 12,
        can_rebind: true,
      }) };
    }
    if (url.includes('/api/pet-care/bind') && options.method === 'POST') {
      return { ok: false, status: 409, json: async () => ({
        request_id: 'request-bind-rejected-1', correlation_id: 'correlation-bind-rejected-1',
        operation_result: { status: 'rejected', code: 'pet_care_binding_blocked_by_active_exploration' },
        snapshot_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 7, state_version: 12,
        can_rebind: true,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  h.window.NekoPetCareRoleAliasSync = { rememberOwner: () => Promise.resolve() };
  const before = h.window.nekoPetCareEntry.getState();
  await h.window.nekoPetCareEntry.rebindCurrentRole();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(state.petId, before.petId);
  assert.equal(state.bindingRevision, before.bindingRevision);
  assert.equal(state.petStateVersion, before.petStateVersion);
  assert.equal(state.canRebind, true);
  assert.equal(state.serviceStatus, 'ready');
  assert.equal(state.serviceReason, 'pet_care_binding_blocked_by_active_exploration');
  assert.equal(state.snapshot, null);
  assert.equal(calls.filter(call => call.url.includes('/api/pet-care/pets/')).length, 0);
});

test('official role rebind rejects a response that replaces the account pet', async () => {
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'unbound', pet_id: 'pet-a', binding_revision: 7, state_version: 12,
        can_rebind: true,
      }) };
    }
    if (url.includes('/api/pet-care/bind') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-b', bound_role_id: 'server-role-b',
        binding_revision: 8, state_version: 13,
        snapshot: stage4Snapshot({ pet_id: 'pet-b', state_version: 13 }),
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: false, status: 404, json: async () => ({ detail: 'context-only' }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  h.window.NekoPetCareRoleAliasSync = { rememberOwner: () => Promise.resolve() };
  await h.window.nekoPetCareEntry.rebindCurrentRole();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.snapshot, null);
  assert.equal(state.petId, '');
  assert.equal(state.serviceReason, 'pet-care-rebind-contract-invalid');
});

test('official role rebind stays closed without the service rebind permission', async () => {
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'unbound', pet_id: 'pet-a', binding_revision: 7, state_version: 12,
        can_rebind: false,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: false, status: 404, json: async () => ({ detail: 'context-only' }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  await h.window.nekoPetCareEntry.rebindCurrentRole();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.canRebind, false);
  assert.equal(state.serviceReason, 'rebind-version-required');
  assert.equal(calls.some(call => call.url.includes('/api/pet-care/bind')), false);
});

test('stage 1 logout clears the pet-care context and closes the open panel', async () => {
  let loggedIn = true;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a', session_generation: 1 }
        : { logged_in: false, local_user_id: null, session_generation: 2 } };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'bound' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, json: async () => ({ binding_status: 'bound',
        snapshot: stage4Snapshot() }) };
    }
    return { ok: true, json: async () => ({}) };
  });
  const notices = [];
  h.window.showAlert = (message, title) => { notices.push({ message, title }); return Promise.resolve(true); };
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = entryOf(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('feeding');
  assert.equal(h.window.nekoPetCareEntry.getState().selectedCategory, 'feeding');
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  loggedIn = false;
  await h.window.nekoPetCareEntry.refreshAuthStatus('logout');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.panelOpen, false);
  assert.equal(h.window.nekoPetCareMode.state.context.accountId, '');
  assert.equal(h.window.nekoPetCareMode.state.context.boundRoleId, '');
  assert.equal(state.selectedCategory, '');
  assert.equal(entry.dataset.expanded, 'false');
  assert.equal(taskOf(h).hidden, true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(notices.at(-1).title, '养宠');
  assert.match(notices.at(-1).message, /已退出 N\.E\.K\.O 账号/);
});

test('external confirmed logout explains itself on the next login', async () => {
  let loggedIn = true;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a', session_generation: 1 }
        : { logged_in: false, local_user_id: null, session_generation: 2 } };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'unbound' }) };
    }
    return { ok: true, json: async () => ({}) };
  });
  const notices = [];
  h.window.showAlert = (message, title) => { notices.push({ message, title }); return Promise.resolve(true); };
  await new Promise(resolve => setTimeout(resolve, 10));
  loggedIn = false;
  h.window.dispatchEvent(new h.window.CustomEvent('neko:auth-session-change', {
    detail: { logged_in: false },
  }));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(notices.length, 0);
  loggedIn = true;
  await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(notices.at(-1).title, '养宠');
  assert.match(notices.at(-1).message, /其他窗口或设备退出/);
});

test('reload after confirmed external logout retains only the active exploration lease for re-login', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  storage.sessionStorageValues.set('neko.pet-care.auth-presence.v1', JSON.stringify({
    status: 'logged-in', accountId: 'account-a', sessionGeneration: '1', updatedAt: Date.now(),
  }));
  storage.sessionStorageValues.set('neko.pet-care.exploration-lease.v1', JSON.stringify({
    accountId: 'account-a', characterUid: 'local-role-a', boundRoleId: 'local-role-a',
    explorationId: 'explore-reload', updatedAt: Date.now(),
    exploration: {
      active: true, status: 'active', exploration_id: 'explore-reload',
      location_id: 'test-path', started_at: new Date().toISOString(),
      ends_at: new Date(Date.now() + 60000).toISOString(),
    },
  }));
  let loggedIn = false;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a', session_generation: 2 }
        : { logged_in: false, local_user_id: null, session_generation: 2 } };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'bound', bound_role_id: 'local-role-a',
        snapshot: { exploration: { active: true, status: 'active', exploration_id: 'explore-reload' } } }) };
    }
    return { ok: true, json: async () => ({}) };
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-out');
  assert.ok(storage.sessionStorageValues.has('neko.pet-care.exploration-lease.v1'));
  assert.match(JSON.parse(storage.sessionStorageValues.get('neko.pet-care.auth-notice.v1')).message, /重新登录/);
  loggedIn = true;
  const notices = [];
  h.window.showAlert = (message, title) => { notices.push({ message, title }); return Promise.resolve(true); };
  await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(notices.at(-1).message, /上次账号会话已退出/);
});

test('confirmed external logout immediately hides an active pet and retains only its opaque exploration lease', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const active = {
    exploration_id: 'explore-external-logout', status: 'active', active: true,
    started_at: '2026-10-21T02:00:00Z', ends_at: '2026-10-21T03:00:00Z',
  };
  const snapshot = stage4Snapshot({ exploration: active });
  let loggedIn = true;
  let recallCalls = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a', session_generation: 1 }
        : { logged_in: false, local_user_id: null, session_generation: 2 } };
    }
    if (url.endsWith('/explorations/explore-external-logout/recall')) {
      recallCalls += 1;
      return { ok: false, status: 401, json: async () => ({ detail: 'session_expired' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);

  loggedIn = false;
  await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(recallCalls, 0);
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.snapshot, null);
  assert.equal(state.exploration, null);
  assert.equal(state.bindingRoleId, '');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
  const lease = JSON.parse(storage.sessionStorageValues.get('neko.pet-care.exploration-lease.v1'));
  assert.equal(lease.accountId, 'account-a');
  assert.equal(lease.explorationId, 'explore-external-logout');
  assert.equal(Object.hasOwn(lease, 'inventory'), false);
  assert.equal(Object.hasOwn(lease, 'status_values'), false);
});

test('confirmed A to B account replacement hides A immediately without recalling it as B', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const active = {
    exploration_id: 'explore-account-a', status: 'active', active: true,
    started_at: '2026-10-21T02:00:00Z', ends_at: '2026-10-21T03:00:00Z',
  };
  const snapshot = stage4Snapshot({ exploration: active });
  let accountId = 'account-a';
  let failAccountAContext = false;
  let recallCalls = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: accountId,
        session_generation: accountId === 'account-a' ? 1 : 2,
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.endsWith('/explorations/explore-account-a/recall')) {
      recallCalls += 1;
      return { ok: false, status: 404, json: async () => ({ detail: 'pet_care_exploration_not_found' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      if (accountId === 'account-b') {
        throw new TypeError('simulated account B PetCare outage');
      }
      if (accountId === 'account-a' && failAccountAContext) {
        throw new TypeError('simulated PetCare outage');
      }
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  let modeExplorationId = '';
  const updateModeContext = h.window.nekoPetCareMode.updateContext.bind(
    h.window.nekoPetCareMode,
  );
  h.window.nekoPetCareMode.updateContext = function (detail) {
    const previous = this.captureContext();
    const result = updateModeContext(detail);
    if (previous.accountId !== (detail.accountId || '') ||
        previous.currentCharacterUid !== (detail.currentCharacterUid || '') ||
        previous.boundRoleId !== (detail.boundRoleId || '')) {
      modeExplorationId = '';
    }
    return result;
  };
  h.window.nekoPetCareMode.isCurrentExploration = detail =>
    !!modeExplorationId && modeExplorationId === String(detail && detail.activityId || '');
  h.window.addEventListener('neko:pet-care:exploration-gate', event => {
    const detail = event && event.detail || {};
    const activityId = String(detail.activityId || detail.explorationId || '');
    if (detail.active === true) modeExplorationId = activityId;
    else if (!activityId || activityId === modeExplorationId) modeExplorationId = '';
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);

  accountId = 'account-b';
  await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(recallCalls, 0);
  assert.equal(state.authStatus, 'logged-in');
  assert.equal(state.accountId, 'account-b');
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(state.snapshot, null);
  assert.equal(state.exploration, null);
  assert.equal(state.bindingRoleId, '');
  assert.equal(h.window.nekoPetCareMode.state.context.accountId, 'account-b');
  assert.equal(h.window.nekoPetCareMode.state.context.boundRoleId, '');
  assert.equal(storage.sessionStorageValues.has('neko.pet-care.exploration-lease.v1'), false);
  const lease = JSON.parse(storage.sessionStorageValues.get(explorationLeaseStorageKey('account-a')));
  assert.equal(lease.accountId, 'account-a');
  assert.equal(lease.explorationId, 'explore-account-a');
  assert.equal(storage.sessionStorageValues.has(explorationLeaseStorageKey('account-b')), false);

  failAccountAContext = true;
  accountId = 'account-a';
  await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
  const restored = h.window.nekoPetCareEntry.getState();
  assert.equal(restored.accountId, 'account-a');
  assert.equal(restored.serviceStatus, 'unavailable');
  assert.equal(restored.snapshot.pet_id, 'recovery-only');
  assert.equal(restored.exploration.exploration_id, 'explore-account-a');
  assert.equal(h.window.nekoPetCareMode.isCurrentExploration({
    activityId: 'explore-account-a',
  }), true);

  failAccountAContext = false;
  await h.window.nekoPetCareEntry.refreshBinding('service-restored');
  const confirmed = h.window.nekoPetCareEntry.getState();
  assert.equal(confirmed.snapshot.pet_id, 'pet-a');
  assert.equal(confirmed.bindingRoleId, 'server-role-a');
  assert.equal(confirmed.explorationGateActive, true);
  assert.equal(h.window.nekoPetCareMode.isCurrentExploration({
    activityId: 'explore-account-a',
  }), true);
});

test('logout failure releases an already-enabled Cat Mind compatibility gate', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'unbound' }) };
    }
    if (url.includes('/api/card-drop/oauth/logout')) {
      return { ok: false, json: async () => ({ detail: 'logout-failed' }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  h.window.nekoPetCareMode.state.catMindSuppressed = true;
  h.window.nekoPetCareMode.state.mode = 'pet-available';
  await h.window.nekoPetCareEntry.logout();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-in');
  assert.equal(state.snapshot, null);
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
});

test('stage 1 mismatched role stays on the ordinary-cat fallback', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'unbound' }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const state = h.window.nekoPetCareEntry.getState();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.equal(entry.dataset.bindingStatus, 'unbound');
  assert.equal(entry.dataset.available, 'false');
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(taskOf(h).hidden, true);
  assert.equal(actionsOf(h).children.every(button => button.disabled), true);
});

test('stage 1 service failure exposes retry and keeps ordinary-cat mode', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: false, status: 503,
        json: async () => ({ detail: RAW_PLAYER_DIAGNOSTIC }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(state.serviceReason, RAW_PLAYER_DIAGNOSTIC);
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  assert.equal(entry.dataset.available, 'true');
  assert.equal(entry.dataset.degraded, 'true');
  assert.equal(entry.dataset.visible, 'true');
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  assert.equal(gridOf(h).hidden, true);
  assert.equal(actionsOf(h).hidden, true);
  assert.equal(overviewOf(h).hidden, false);
  assertSafePlayerFailure(overviewOf(h).children[1],
    '养宠功能暂时不可用，猫的普通行为不受影响。');
  assert.equal(overviewOf(h).children[1].getAttribute('data-neko-pet-care-input-surface'), 'true');
  assert.equal(['overview', 'tasks', 'inventory', 'profile']
    .every(view => navButtonOf(h, view).disabled), true);
  assert.equal(footerOf(h).children[2].hidden, false);
});

test('cat-form exit hides the entry immediately and restores it on re-entry', async () => {
  const h = await createReadyHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.equal(entry.dataset.visible, 'true');
  await openPanel(h);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: false, reason: 'cat-form-left' },
  }));
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(entry.dataset.available, 'false');
  assert.equal(entry.dataset.expanded, 'false');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');

  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: true, reason: 'cat-form-entered' },
  }));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  assert.equal(entry.dataset.visible, 'true');
});

test('auth status failure clears the old account context before retry', async () => {
  let failStatus = false;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      if (failStatus) throw new Error('auth-unavailable');
      return { ok: true, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 1,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: formalSnapshot({ account_id: 'account-a',
          status_values: { satiety: 70, energy: 70, cleanliness: 70, mood: 70, progress: 0 },
          negative_status: {}, inventory: {} }),
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  failStatus = true;
  await h.window.nekoPetCareEntry.refreshAuthStatus('auth-failure');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'unknown');
  assert.equal(state.accountId, '');
  assert.equal(state.sessionGeneration, '');
  assert.equal(state.snapshot, null);
  assert.equal(h.window.nekoPetCareMode.state.context.accountId, '');
  assert.equal(h.window.nekoPetCareMode.state.context.boundRoleId, '');
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const footer = footerOf(h);
  assert.equal(footer.children[0].hidden, false);
  assert.equal(footer.children[2].hidden, false);
});

test('saved OAuth session uncertainty keeps account identity but fails pet care closed', async () => {
  let cloudUnavailable = false;
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 70, energy: 70, cleanliness: 80, mood: 65, progress: 4 },
    negative_status: {}, inventory: {} });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => cloudUnavailable
        ? { logged_in: false, local_user_id: null, session_generation: 1, session_saved: true }
        : { logged_in: true, local_user_id: 'account-a', session_generation: 1 } };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  cloudUnavailable = true;
  await h.window.nekoPetCareEntry.refreshAuthStatus('focus');
  let state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-in');
  assert.equal(state.accountId, 'account-a');
  assert.equal(state.snapshot, null);
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');

  cloudUnavailable = false;
  await h.window.nekoPetCareEntry.refreshAuthStatus('focus-retry');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-in');
  assert.equal(state.serviceStatus, 'ready');
  assert.equal(state.bindingStatus, 'bound');
});

test('reopening saved-session degraded surface performs one auth recovery and never revives logout', async () => {
  let cloudUnavailable = false;
  let confirmedLogout = false;
  let deferStatus = false;
  let resolveDeferredStatus = null;
  let statusCalls = 0;
  let contextCalls = 0;
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 70, energy: 70, cleanliness: 80, mood: 65, progress: 4 },
    negative_status: {}, inventory: {} });
  const statusResponse = () => ({ ok: true, status: 200, json: async () => confirmedLogout
    ? { logged_in: false, local_user_id: null, session_generation: 2 }
    : cloudUnavailable
      ? { logged_in: false, local_user_id: null, session_generation: 1, session_saved: true }
      : { logged_in: true, local_user_id: 'account-a', session_generation: 1 } });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      statusCalls += 1;
      if (deferStatus) {
        return new Promise(resolve => { resolveDeferredStatus = () => resolve(statusResponse()); });
      }
      return statusResponse();
    }
    if (url.includes('/api/card-drop/oauth/logout')) {
      confirmedLogout = true;
      return { ok: true, status: 200, json: async () => ({ logged_out: true }) };
    }
    if (url.includes('/api/pet-care/tasks')) {
      return { ok: true, status: 200, json: async () => ({ snapshot }) };
    }
    if (url.includes('/api/pet-care/context')) {
      contextCalls += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  panelOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, false);

  cloudUnavailable = true;
  await h.window.nekoPetCareEntry.refreshAuthStatus('saved-session-unavailable');
  assert.equal(h.window.nekoPetCareEntry.getState().serviceReason, 'oauth-status-unavailable');
  assert.equal(navButtonOf(h, 'overview').disabled, true);

  const retryButton = footerOf(h).children.find(child =>
    child.className === 'neko-pet-care-retry-button');
  const statusBeforeManualRetry = statusCalls;
  const contextBeforeManualRetry = contextCalls;
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  await waitFor(() => statusCalls === statusBeforeManualRetry + 1 && !retryButton.disabled);
  assert.equal(contextCalls, contextBeforeManualRetry);

  cloudUnavailable = false;
  deferStatus = true;
  const statusBeforeReopen = statusCalls;
  await openPanel(h);
  await waitFor(() => typeof resolveDeferredStatus === 'function');
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(statusCalls, statusBeforeReopen + 1);
  assert.equal(retryButton.disabled, true);
  assert.equal(overviewOf(h).children[1].textContent, '正在重新确认账号状态…');
  deferStatus = false;
  resolveDeferredStatus();
  await waitFor(() => h.window.nekoPetCareEntry.getState().serviceStatus === 'ready');
  assert.equal(['overview', 'tasks', 'inventory', 'profile']
    .every(view => navButtonOf(h, view).disabled === false), true);

  await h.window.nekoPetCareEntry.logout();
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-out');
  const statusAfterLogout = statusCalls;
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  await openPanel(h);
  assert.equal(statusCalls, statusAfterLogout);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot, null);
});

test('reopening a pure PetCare outage retries only binding and deduplicates the pending read', async () => {
  let serviceAvailable = true;
  let deferContext = false;
  let resolveDeferredContext = null;
  let statusCalls = 0;
  let contextCalls = 0;
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 72, energy: 68, cleanliness: 80, mood: 66, progress: 5 },
    negative_status: {}, inventory: {} });
  const boundContextResponse = () => ({ ok: true, status: 200, json: async () => ({
    account_id: 'account-a', current_character_uid: 'local-role-a',
    binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
  }) });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      statusCalls += 1;
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 1,
      }) };
    }
    if (url.includes('/api/pet-care/tasks')) {
      return { ok: true, status: 200, json: async () => ({ snapshot }) };
    }
    if (url.includes('/api/pet-care/context')) {
      contextCalls += 1;
      if (!serviceAvailable) {
        return { ok: false, status: 503, json: async () => ({ detail: 'service-unavailable' }) };
      }
      if (deferContext) {
        return new Promise(resolve => { resolveDeferredContext = () => resolve(boundContextResponse()); });
      }
      return boundContextResponse();
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  panelOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });

  serviceAvailable = false;
  await h.window.nekoPetCareEntry.refreshBinding('simulate-pet-care-503');
  assert.equal(h.window.nekoPetCareEntry.getState().serviceStatus, 'unavailable');
  const retryButton = footerOf(h).children.find(child =>
    child.className === 'neko-pet-care-retry-button');
  const contextBeforeManualRetry = contextCalls;
  const statusBeforeManualRetry = statusCalls;
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  await waitFor(() => contextCalls === contextBeforeManualRetry + 1 && !retryButton.disabled);
  assert.equal(statusCalls, statusBeforeManualRetry);

  serviceAvailable = true;
  deferContext = true;
  const contextBeforeReopen = contextCalls;
  const statusBeforeReopen = statusCalls;
  await openPanel(h);
  await waitFor(() => typeof resolveDeferredContext === 'function');
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(contextCalls, contextBeforeReopen + 1);
  assert.equal(statusCalls, statusBeforeReopen);
  assert.equal(retryButton.disabled, true);
  deferContext = false;
  resolveDeferredContext();
  await waitFor(() => h.window.nekoPetCareEntry.getState().serviceStatus === 'ready');
  assert.equal(['overview', 'tasks', 'inventory', 'profile']
    .every(view => navButtonOf(h, view).disabled === false), true);
});

test('pet-care 401 refreshes the shared auth state and drops stale context', async () => {
  let expired = false;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => expired
        ? ({ logged_in: false, local_user_id: null })
        : ({ logged_in: true, local_user_id: 'account-a', session_generation: 1 }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      if (expired) return { ok: false, status: 401, json: async () => ({ code: 'login_required' }) };
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: formalSnapshot({ account_id: 'account-a',
          status_values: { satiety: 70, energy: 70, cleanliness: 70, mood: 70, progress: 0 },
          negative_status: {}, inventory: {} }),
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  expired = true;
  await h.window.nekoPetCareEntry.refreshBinding('token-expired');
  await waitFor(() => h.window.nekoPetCareEntry.getState().authStatus === 'logged-out');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.snapshot, null);
  assert.equal(state.accountId, '');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
});

test('stage 1 does not wait forever when the role identity is not ready', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  }, null);
  await new Promise(resolve => setTimeout(resolve, 10));
  let state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.serviceReason, 'character-identity-unavailable');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  h.window.appChatAvatarState.getIdentity = () => ({ uid: 'local-role-a', name: '小猫 A' });
  await h.window.nekoPetCareEntry.refreshBinding('identity-retry');
  // refreshBinding discovers the late identity through setCharacter(), which
  // starts a second asynchronous binding request. Let that request settle so
  // this assertion checks the final failure state rather than the transient
  // reset to `unknown` made by setCharacter().
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unavailable');
  state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.currentCharacterUid, 'local-role-a');
  assert.equal(state.bindingStatus, 'unavailable');
});

test('stage 1 OAuth popup failure leaves the ordinary cat usable', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/start')) {
      return { ok: true, json: async () => ({ auth_url: 'https://community.example/login', state: 'oauth-state' }) };
    }
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: false, local_user_id: null }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  h.window.open = () => null;
  await new Promise(resolve => setTimeout(resolve, 10));
  await h.window.nekoPetCareEntry.startLogin();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.oauthPending, false);
  assert.equal(state.reason, 'oauth-popup-blocked');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
});

test('stage 1 OAuth popup cancellation returns to a retryable ordinary-cat state', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/start')) {
      return { ok: true, json: async () => ({ auth_url: 'https://community.example/login', state: 'oauth-state' }) };
    }
    if (url.includes('/api/card-drop/oauth/completion')) {
      return { ok: true, json: async () => ({ logged_in: false }) };
    }
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: false, local_user_id: null }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  h.window.open = () => ({ closed: true });
  await new Promise(resolve => setTimeout(resolve, 10));
  await h.window.nekoPetCareEntry.startLogin();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.oauthPending, false);
  assert.equal(state.reason, 'oauth-cancelled');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
});

test('stage 1 OAuth success continues into account and role binding', async () => {
  let statusCalls = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/start')) {
      return { ok: true, json: async () => ({ auth_url: 'https://community.example/login', state: 'oauth-state' }) };
    }
    if (url.includes('/api/card-drop/oauth/completion')) {
      return { ok: true, json: async () => ({ logged_in: true }) };
    }
    if (url.includes('/api/card-drop/oauth/status')) {
      statusCalls += 1;
      return { ok: true, json: async () => statusCalls > 1
        ? { logged_in: true, local_user_id: 'account-a', user: { display_name: '账号 A' } }
        : { logged_in: false, local_user_id: null } };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'bound' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, json: async () => ({ binding_status: 'bound',
        snapshot: stage4Snapshot() }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  await h.window.nekoPetCareEntry.startLogin();
  // setAuth() launches refreshBinding() without awaiting it; wait for the
  // service request before inspecting the post-login binding state.
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'bound');
  const state = h.window.nekoPetCareEntry.getState();
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.equal(state.authStatus, 'logged-in');
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.bindingRoleId, 'server-role-a');
  assert.equal(entry.dataset.bindingStatus, 'bound');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-available');
});

test('stage 2 reads the canonical pet snapshot and keeps all operations read-only', async () => {
  const calls = [];
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    inventory: {} });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', user: { display_name: '账号 A' },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        locations: [{ id: 'forged-location', name: '不应显示' }],
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const state = h.window.nekoPetCareEntry.getState();
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const panel = panelOf(h);
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot.status_values.satiety, 72);
  assert.equal(state.explorationLocations[0].id, 'window-path');
  assert.ok(calls.some(item => item.url.includes('/api/pet-care/pets/local-role-a')));
  assert.equal(calls.some(item => item.url.includes('/api/pet-care/context')), true);
  assert.ok(calls.findIndex(item => item.url.includes('/api/pet-care/context')) <
    calls.findIndex(item => item.url.includes('/api/pet-care/pets/local-role-a')));
  assert.equal(gridOf(h).children[0].children[1].textContent, '72');
  assert.equal(gridOf(h).children[4].children[1].textContent, '13');
  assert.equal(actionsOf(h).children[0].disabled, true);
  assert.equal(actionsOf(h).children[1].disabled, true);
  assert.equal(actionsOf(h).children[2].disabled, true);
  assert.equal(actionsOf(h).children[3].disabled, true);
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-available');
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
});

test('stage 2 does not poll or re-read the pet snapshot when the account is unchanged', async () => {
  const calls = [];
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 70, energy: 60, cleanliness: 80, mood: 65, progress: 4 },
    inventory: {} });
  const h = createHarness(async (url) => {
    calls.push(url);
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'same-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const before = calls.filter(url => url.includes('/api/pet-care/pets/')).length;
  await h.window.nekoPetCareEntry.refreshAuthStatus('focus');
  const after = calls.filter(url => url.includes('/api/pet-care/pets/')).length;
  assert.equal(after, before);
});

test('stage 2 treats a partial snapshot as ordinary-cat fallback', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: { schema_version: 1, account_id: 'account-a', pet_id: 'pet-a',
          bound_role_id: 'server-role-a', status_values: { satiety: 72 } },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot, null);
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.equal(gridOf(h).children[0].children[1].textContent, '—');
  assert.equal(taskOf(h).hidden, true);
  assert.equal(actionsOf(h).children.every(button => button.disabled), true);
});

test('formal snapshot rejects malformed or contradictory exploration records', async () => {
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-canonical-active', location_id: 'window-path',
    status: 'active', active: true,
    started_at: '2026-10-21T00:00:00Z', ends_at: '2026-10-21T01:00:00Z',
    next_letter_at: '2026-10-21T00:20:00Z',
    letters_revealed: [{ id: 'letter-1', text: '第一封信', available_ratio: 0.333333 }],
  });
  const terminal = canonicalExplorationFixture({
    ...active, status: 'completed', active: false,
    letters: [{ id: 'letter-1', text: '第一封信', available_ratio: 0.333333 }],
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 3,
    completion_ratio: 1,
  });
  const without = (value, key) => {
    const result = { ...value };
    delete result[key];
    return result;
  };
  const invalidExplorations = [
    'active',
    [],
    {},
    { status: 'active', active: true },
    { exploration_id: 'explore-invalid-active', status: 'active', active: false },
    { exploration_id: 'explore-invalid-terminal', status: 'completed', active: true },
    { exploration_id: 'explore-conflict-a', activity_id: 'explore-conflict-b', status: 'active', active: true },
    { exploration_id: 'explore-unknown', status: 'pending', active: true },
    { ...active, status: 'away' },
    { ...active, active: false },
    without(active, 'location_id'),
    { ...active, location: { ...active.location, id: 'another-location' } },
    { ...active, started_at: '2026-02-30T00:00:00Z', departed_at: '2026-02-30T00:00:00Z' },
    { ...active, departed_at: '2026-10-21T00:00:01Z' },
    { ...active, duration_seconds: active.duration_seconds + 1 },
    { ...active, letters_available: active.letters_available + 1 },
    { ...active, letters_revealed: [{ ...active.letters_revealed[0], available_ratio: '0.5' }] },
    { ...active, next_letter_at: '2026-10-21T01:00:01Z' },
    { ...active, letters: [{ ...active.letters_revealed[0] }] },
    { ...active, drops: [{ item_id: 'teaser_wand', quantity: 1 }] },
    { ...active, progress_delta: 1 },
    { ...active, settled_at: '2026-10-21T00:30:00Z' },
    { ...active, fixed_result: { drops: [] } },
    { ...terminal, next_letter_at: '2026-10-21T00:20:00Z' },
    { ...terminal, status: 'returned' },
    { ...terminal, letters: [], letters_revealed: [], letters_available: 0 },
    { ...terminal, letters_available: terminal.letters_available + 1 },
    { ...terminal, drops: [{ item_id: 'teaser_wand', quantity: 0 }] },
    { ...terminal, progress_delta: null },
    { ...terminal, completion_ratio: '1' },
    { ...terminal, completion_ratio: 1.1 },
    { ...terminal, completed_at: '2026-10-21T01:00:01Z' },
  ];

  for (const exploration of invalidExplorations) {
    const incomplete = formalSnapshot({ exploration }, { rawExploration: true });
    const h = createHarness(async url => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          snapshot: incomplete,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().serviceReason === 'snapshot-incomplete');
    const state = h.window.nekoPetCareEntry.getState();
    assert.equal(state.snapshot, null);
    assert.equal(state.serviceStatus, 'unavailable');
    assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  }
});

test('a malformed exploration refresh cannot release an existing gate or enable care', async () => {
  const active = {
    exploration_id: 'explore-malformed-refresh', status: 'active', active: true,
    started_at: '2026-10-21T02:00:00Z', ends_at: '2026-10-21T03:00:00Z',
  };
  const initial = formalSnapshot({ exploration: active });
  const malformed = formalSnapshot({
    state_version: 2,
    exploration: { ...initial.exploration, active: false },
  }, { rawExploration: true });
  let snapshotReads = 0;
  let carePosts = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') carePosts += 1;
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: snapshotReads === 1 ? initial : malformed,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const gates = [];
  h.window.addEventListener('neko:pet-care:exploration-gate', event => gates.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.exploration_id === active.exploration_id);
  assert.equal(gates.some(detail => detail.active === true), true);

  await h.window.nekoPetCareEntry.refreshBinding('malformed-exploration-refresh');
  await waitFor(() => h.window.nekoPetCareEntry.getState().serviceReason === 'snapshot-incomplete');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot, null);
  assert.equal(gates.some(detail => detail.active === false), false);
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);
  assert.equal(carePosts, 0);
});

test('formal snapshot rejects action catalogs outside its state-version fence', async () => {
  const incomplete = formalSnapshot();
  delete incomplete.care_actions;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: incomplete,
        care_actions: { feeding: { small_fish: { inventory_item: 'small_fish' } } },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot, null);
  assert.equal(state.careActions, null);
  assert.equal(state.serviceReason, 'snapshot-incomplete');
});

test('formal snapshot rejects inventory actions without a positive configured consume quantity', async () => {
  const incomplete = formalSnapshot({
    care_actions: { feeding: { small_fish: {
      label: '小鱼干', inventory_item: 'small_fish', satiety_delta: 25,
    } } },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: incomplete,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot, null);
  assert.equal(state.serviceReason, 'snapshot-incomplete');
});

test('formal snapshot rejects service actions that reference an unregistered client adapter', async () => {
  for (const actionOverride of [
    { care_actions: { feeding: { small_fish: {
      label: '小鱼干', inventory_item: 'small_fish', consume_quantity: 1,
      satiety_delta: 25, feedback_action: 'run_arbitrary_window_function',
    } } } },
    { play_actions: { future_toy: {
      label: '未来玩具', inventory_item: 'future_toy', consume_quantity: 1,
      inventory_quantity: 1, daily_count: 0, daily_limit: 1,
      feedback_action: 'reuse_unknown_animation', minimum_duration_seconds: 1,
    } }, inventory: { small_fish: 2, future_toy: 1 } },
  ]) {
    const snapshot = formalSnapshot(actionOverride);
    const h = createHarness(async url => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          binding_status: 'bound', snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().serviceReason === 'snapshot-incomplete');
    assert.equal(h.window.nekoPetCareEntry.getState().snapshot, null);
  }
});

test('structured cleaning supplies show inventory and fail closed before transport when empty', async () => {
  let carePosts = 0;
  const snapshot = formalSnapshot({
    service_capabilities: { item_directory: 1 },
    inventory: { wool_comb: 0 },
    item_directory: {
      wool_comb: {
        item_id: 'wool_comb', display_name: '羊毛梳', category: 'cleaning_supply',
        asset_url: '/static/assets/neko-idle/pet-care-tool/wash/wool-comb.png',
        acquisition_sources: ['initial'],
      },
    },
    care_actions: { cleaning: { brushing: {
      label: '梳毛', inventory_item: 'wool_comb', consume_quantity: 1,
      cleanliness_delta: 20, progress_delta: 2, feedback_action: 'stretch',
    } } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') carePosts += 1;
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('cleaning');
  const cleaningButton = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'brushing');
  assert.equal(cleaningButton.disabled, true);
  assert.match(cleaningButton.title, /库存不足/);
  assert.match(cleaningButton.children.find(child =>
    child.className === 'neko-pet-care-content-meta').textContent, /库存不足 · 库存 × 0/);
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('cleaning', 'brushing'), null);
  assert.equal(carePosts, 0);
});

test('structured item categories must match the action that consumes them', async () => {
  const snapshot = formalSnapshot({
    service_capabilities: { item_directory: 1 },
    inventory: { wool_comb: 1 },
    item_directory: {
      wool_comb: {
        item_id: 'wool_comb', display_name: '错误分类梳子', category: 'food',
        asset_url: '/static/assets/neko-idle/pet-care-tool/wash/wool-comb.png',
        acquisition_sources: ['initial'],
      },
    },
    care_actions: { cleaning: { brushing: {
      label: '梳毛', inventory_item: 'wool_comb', consume_quantity: 1,
      cleanliness_delta: 20, progress_delta: 2, feedback_action: 'stretch',
    } } },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().serviceReason === 'snapshot-incomplete');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot, null);
});

test('configured consume quantities gate care and toy play before transport and remain visible', async () => {
  let carePosts = 0;
  let playLeasePosts = 0;
  const snapshot = formalSnapshot({
    inventory: { small_fish: 1, teaser_wand: 1 },
    care_actions: { feeding: { small_fish: {
      label: '小鱼干', inventory_item: 'small_fish', consume_quantity: 2,
      satiety_delta: 25, feedback_action: 'eat_snack',
    } } },
    play_actions: { teaser_wand: {
      content_id: 'teaser_wand', label: '逗猫棒', inventory_item: 'teaser_wand',
      consume_quantity: 2, inventory_quantity: 1, daily_count: 0, daily_limit: 3,
      feedback_action: 'play_teaser_wand', minimum_duration_seconds: 8,
    } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') carePosts += 1;
    if (url.endsWith('/play-leases') && options.method === 'POST') playLeasePosts += 1;
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);

  h.window.nekoPetCareEntry.setActionCategory('feeding');
  const foodButton = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'small_fish');
  assert.equal(foodButton.disabled, true);
  assert.match(foodButton.title, /库存不足/);
  assert.match(foodButton.children.find(child => child.className === 'neko-pet-care-content-meta').textContent,
    /库存不足 · 库存 × 1 · 本次 × 2/);
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);

  h.window.nekoPetCareEntry.setActionCategory('play');
  const toyButton = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.equal(toyButton.disabled, true);
  assert.match(toyButton.title, /库存不足/);
  assert.match(toyButton.children.find(child => child.className === 'neko-pet-care-content-meta').textContent,
    /库存不足 · 库存 × 1 · 本次 × 2/);
  detailsOf(h).dispatchEvent({ type: 'click', target: toyButton, stopPropagation() {} });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(carePosts, 0);
  assert.equal(playLeasePosts, 0);
});

test('official snapshot requires a versioned projection and has no browser-clock settlement fallback', async () => {
  const incomplete = formalSnapshot({
    settlement: {
      satiety_decay_per_hour: 99,
      cleanliness_decay_per_hour: 99,
      energy_recovery_per_hour: 99,
    },
  });
  delete incomplete.client_projection;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: incomplete,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot, null);
  assert.equal(state.serviceReason, 'snapshot-incomplete');
  assert.equal(gridOf(h).children[0].children[1].textContent, '—');

  const valueStart = source.indexOf('function value(key)');
  const valueEnd = source.indexOf('function careReady(', valueStart);
  const valueSource = source.slice(valueStart, valueEnd);
  assert.doesNotMatch(valueSource, /Date\.now\(\)|state\.settlement|pre-projection/);
});

test('official snapshot rejects incomplete or contradictory projection rules', async () => {
  const mutations = [
    value => { delete value.status_rules.full_threshold; },
    value => { value.status_rules.paused_during_exploration = false; },
    value => { value.status_rules.clamp.min = value.status_rules.clamp.max; },
    value => { value.status_rules.mood_weights.interaction = 0.5; },
    value => { value.cat_mind.cat1_eat_snack.score_bias = 101; },
    value => { value.cat_mind_valid_until = '2026-10-21T02:05:00.001Z'; },
  ];

  for (const mutate of mutations) {
    const invalidProjection = clientProjection();
    mutate(invalidProjection);
    const incomplete = formalSnapshot({ client_projection: invalidProjection });
    const h = createHarness(async url => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          binding_status: 'bound', snapshot: incomplete,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().serviceReason === 'snapshot-incomplete');
    assert.equal(h.window.nekoPetCareEntry.getState().snapshot, null);
  }
});

test('stage 3 keeps the Cat Mind gate out of the stage 2 snapshot path until cat form is active', () => {
  assert.match(source, /setCatMindCompatibility/);
  assert.doesNotMatch(source, /shouldBlockCatMindActionRequest/);
});

test('stage 3 keeps idle Cat Mind active for a complete bound cat-form snapshot', async () => {
  const snapshot = formalSnapshot({ account_id: 'account-a',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    inventory: {} });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: true, tier: 'cat1' },
  }));
  const ready = h.window.nekoPetCareMode.state;
  assert.equal(ready.catMindSuppressed, false);
  assert.equal(ready.blockedCatMindActionIds, null);
  assert.equal(h.window.nekoPetCareEntry.getState().catMindCompatibilityEnabled, true);
  assert.equal(h.window.nekoPetCareEntry.getState().catLocalActive, true);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: false, tier: 'cat1' },
  }));
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
});

test('stage 1 source keeps OAuth and pet identity separate from model button code', () => {
  const methodsButtons = fs.readFileSync(path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'methods-buttons.js'), 'utf8');
  const pagesRouter = fs.readFileSync(path.join(root, 'main_routers', 'pages_router.py'), 'utf8');
  const surfaceControls = fs.readFileSync(path.join(root, 'static', 'app', 'app-ui', 'surface-floating-controls.js'), 'utf8');
  assert.equal(methodsButtons.includes('pet-care'), false);
  assert.match(source, /api\/card-drop\/oauth\/status/);
  assert.match(source, /appChatAvatarState/);
  assert.match(source, /model button/i);
  assert.equal(source.includes('getAvatarBounds'), false);
  assert.match(clientSource, /api\/pet-care\/context/);
  assert.match(clientSource, /api\/pet-care\/bind/);
  assert.match(clientSource, /tasks\//);
  assert.doesNotMatch(source, /fetch\([^\n]*api\/pet-care/);
  assert.match(surfaceControls, /neko-auth-platform/);
  assert.match(surfaceControls, /api\/card-drop\/oauth\/logout/);
  assert.match(source, /饱食度/);
  assert.match(source, /actionLabels = \['喂食', '清洁', '陪玩', '探索'\]/);
  assert.match(source, /关闭养宠面板/);
  assert.equal(source.includes('喂食 · 小鱼干'), false);
  assert.equal(source.includes('清洁 · 梳毛'), false);
  assert.match(pagesRouter, /static\/app\/app-pet-care\.js/);
  for (const templateName of ['index.html', 'chat.html']) {
    const template = fs.readFileSync(path.join(root, 'templates', templateName), 'utf8');
    assert.match(template, /static\/css\/pet-care\.css\?v=\{\{ static_asset_version \}\}/);
    assert.match(template, /static\/app\/app-pet-care-mode\.js\?v=\{\{ static_asset_version \}\}/);
    assert.match(template, /static\/app\/app-pet-care\.js\?v=\{\{ static_asset_version \}\}/);
    assert.ok(template.indexOf('/static/app/app-chat-avatar-state.js') < template.indexOf('/static/app/app-pet-care.js'));
    assert.ok(template.indexOf('/static/app/app-pet-care-api.js') < template.indexOf('/static/app/app-pet-care-client.js'));
    assert.ok(template.indexOf('/static/app/app-pet-care-client.js') < template.indexOf('/static/app/app-pet-care.js'));
    assert.ok(template.indexOf('/static/app/app-pet-care-pending-writes.js') < template.indexOf('/static/app/app-pet-care.js'));
  }
});

function canonicalExplorationLetterFixture(value, index, total) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const text = String(source.text || source.body || `第 ${index + 1} 封来信`);
  const letter = {
    id: String(source.id || source.letter_id || `letter-${index + 1}`),
    text,
    available_ratio: source.available_ratio == null
      ? Number(((index + 1) / Math.max(3, total)).toFixed(6))
      : source.available_ratio,
  };
  if (Object.hasOwn(source, 'image_url')) letter.image_url = source.image_url;
  if (Object.hasOwn(source, 'requires_unlock_id')) {
    letter.requires_unlock_id = source.requires_unlock_id;
  }
  return letter;
}

function canonicalExplorationFixture(value, locations = []) {
  if (value === null || !value || typeof value !== 'object' || Array.isArray(value)) return value;
  const active = value.active === true;
  const startedAt = value.started_at || value.departed_at || '2026-10-21T00:00:00Z';
  const endsAt = value.ends_at || value.due_at ||
    new Date(Date.parse(startedAt) + 60 * 60 * 1000).toISOString();
  const locationId = String(value.location_id || value.location?.id || 'window-path');
  const catalogueLocation = locations.find(location =>
    location && String(location.id) === locationId) || {};
  const sourceLocation = value.location && typeof value.location === 'object'
    ? value.location : catalogueLocation;
  const location = {
    id: locationId,
    name: String(sourceLocation.name || sourceLocation.title || '测试探索地'),
  };
  for (const key of ['description', 'asset_url', 'note']) {
    if (Object.hasOwn(sourceLocation, key)) location[key] = sourceLocation[key];
  }
  const revealedInput = Array.isArray(value.letters_revealed) ? value.letters_revealed : [];
  let lettersRevealed = revealedInput.map((letter, index) =>
    canonicalExplorationLetterFixture(letter, index, revealedInput.length));
  let letters = [];
  if (!active) {
    const planInput = Array.isArray(value.letters) && value.letters.length
      ? value.letters : (revealedInput.length ? revealedInput : [{ id: 'letter-1', text: '探索来信' }]);
    letters = planInput.map((letter, index) =>
      canonicalExplorationLetterFixture(letter, index, planInput.length));
    lettersRevealed = letters.map(letter => ({ ...letter }));
  }
  const durationSeconds = value.duration_seconds == null
    ? Math.max(1, Math.floor((Date.parse(endsAt) - Date.parse(startedAt)) / 1000))
    : value.duration_seconds;
  const exploration = {
    exploration_id: String(value.exploration_id || 'exploration-fixture'),
    location_id: locationId,
    status: value.status || (active ? 'active' : 'completed'),
    active,
    started_at: startedAt,
    departed_at: value.departed_at || startedAt,
    ends_at: endsAt,
    due_at: value.due_at || endsAt,
    duration_seconds: durationSeconds,
    settled_at: active ? null : (value.settled_at || value.completed_at || endsAt),
    completed_at: active ? null : (value.completed_at || value.settled_at || endsAt),
    letters_revealed: lettersRevealed,
    letters_available: lettersRevealed.length,
    letters,
    drops: active ? [] : (Array.isArray(value.drops) ? value.drops : []),
    progress_delta: active ? null : (value.progress_delta == null ? 0 : value.progress_delta),
    completion_ratio: active ? null : (value.completion_ratio == null
      ? (value.status === 'recalled' ? 0.5 : 1) : value.completion_ratio),
    location,
  };
  if (active && Object.hasOwn(value, 'next_letter_at')) {
    exploration.next_letter_at = value.next_letter_at;
  }
  return exploration;
}

function stage4Snapshot(overrides = {}, options = {}) {
  const snapshot = {
    schema_version: 1,
    pet_id: 'pet-a', account_id: 'account-a', bound_role_id: 'server-role-a',
    binding_revision: 1, state_version: 1,
    last_settled_at: '2026-10-21T02:00:00Z',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: { small_fish: 2 }, tasks: { items: [] },
    unlocks: [], exploration: null,
    cleaning_cooldowns: {}, activity_counters: {}, unlock_checkpoint: 0,
    settlement: null, client_projection: clientProjection(), content_directory: {},
    care_actions: {}, play_actions: {},
    exploration_locations: [{ id: 'window-path', name: '窗边小路',
      description: '在窗边走一小段路。', duration_seconds: 1800, costs: {} }],
    service_capabilities: { config_revision_write_cas: 1 },
    ...overrides,
  };
  if (overrides.service_capabilities &&
      typeof overrides.service_capabilities === 'object' &&
      !Array.isArray(overrides.service_capabilities)) {
    snapshot.service_capabilities = {
      config_revision_write_cas: 1,
      ...overrides.service_capabilities,
    };
  }
  if (Object.hasOwn(overrides, 'exploration') && options.rawExploration !== true) {
    snapshot.exploration = canonicalExplorationFixture(
      overrides.exploration, snapshot.exploration_locations);
  }
  return snapshot;
}

function progressContentDirectory(unlockedIds = [], additionalEntries = {}) {
  const unlocked = new Set(unlockedIds);
  return {
    exploration_mailbox: {
      unlock_id: 'cat-nest-mailbox', unlocked: unlocked.has('cat-nest-mailbox'),
      threshold: 10, display_name: '猫窝信箱',
    },
    cat1_petting: {
      unlock_id: 'cat1-petting-action', unlocked: unlocked.has('cat1-petting-action'),
      threshold: 140, display_name: '撸猫动作', content_id: 'cat1_petting',
      feedback_action: 'cat1_petting',
    },
    ...additionalEntries,
  };
}

function clientProjection(overrides = {}) {
  return {
    schema_version: 3, config_revision: 'candidate-1',
    effective_at: '2026-10-20T00:00:00Z', server_time: '2026-10-21T02:00:00Z',
    cat_mind_valid_until: '2026-10-21T02:05:00Z',
    status_rules: {
      clamp: { min: 0, max: 100 }, full_threshold: 100,
      paused_during_exploration: true,
      per_hour: { satiety: -1, energy: 4, cleanliness: -1, interaction: -0.5 },
      mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
    },
    status_seed: { interaction: 40 },
    cat_mind: {
      cat1_social_ping: { score_bias: 0 }, cat1_eat_snack: { score_bias: 5 },
      cat1_small_move: { score_bias: 0 }, cat1_play_yarn: { score_bias: 0 },
      cat2_nap_feedback: { score_bias: 0 }, cat3_sleep_feedback: { score_bias: 0 },
    },
    ...overrides,
  };
}

function formalSnapshot(overrides = {}, options = {}) {
  const snapshot = stage4Snapshot({
    tasks: { items: [] },
    service_capabilities: {
      play_lease_complete: 1, session_resume: 1,
      content_directory: 1,
    },
    ...overrides,
  }, options);
  delete snapshot.account_id;
  return snapshot;
}

test('versioned client projection uses service time and feeds Cat Mind read-only input', async () => {
  const snapshot = stage4Snapshot({
    last_settled_at: '2026-10-21T00:00:00Z',
    client_projection: clientProjection(),
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const installed = [];
  let cleared = 0;
  h.window.nekoCatMind = {
    setPetCareProjection(value) { installed.push(value); return true; },
    clearPetCareProjection() { cleared += 1; },
  };
  await h.window.nekoPetCareEntry.refreshBinding('projection-adapter-test');
  assert.equal(installed.length, 1);
  assert.equal(installed[0].config_revision, 'candidate-1');
  assert.equal(installed[0].cat_mind.cat1_eat_snack.score_bias, 5);

  enterCatForm(h);
  await openPanel(h);
  const stats = gridOf(h);
  assert.equal(stats.children[0].children[1].textContent, '70');
  assert.equal(stats.children[1].children[1].textContent, '69');
  assert.equal(stats.children[2].children[1].textContent, '86');
  assert.equal(stats.children[3].children[1].textContent, '66');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 72);
  assert.equal(Object.keys(h.window.nekoPetCareEntry.getState().clientProjection)
    .some(key => key.startsWith('_')), false);

  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', {
    detail: { character_uid: 'local-role-b', character_name: '小猫 B' },
  }));
  await waitFor(() => cleared > 0);
});

test('server-confirmed return settlement is shown once per account and pet while a new settlement is shown again', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  let snapshot = stage4Snapshot({ settlement: {
    settlement_id: '11111111-1111-4111-8111-111111111111', activity_sequence: 2,
    daily_login: { progress_delta: 4 }, progress_rollback: 3,
    negative_statuses: ['sick'],
  } });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  const notices = [];
  h.window.showAlertDialog = (message, options) => { notices.push({ message, options }); };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await waitFor(() => notices.length === 1);
  assert.match(notices[0].message, /每日登录：养成进度 \+4/);
  assert.match(notices[0].message, /离线期间：养成进度回退 3/);
  assert.match(notices[0].message, /生病/);

  await h.window.nekoPetCareEntry.refreshBinding('same-settlement');
  assert.equal(notices.length, 1);

  snapshot = stage4Snapshot({ state_version: 2, settlement: {
    settlement_id: '22222222-2222-4222-8222-222222222222', activity_sequence: 3,
    negative_status: 'sulk', progress_rollback: 1,
  } });
  await h.window.nekoPetCareEntry.refreshBinding('new-settlement');
  await waitFor(() => notices.length === 2);
  assert.match(notices[1].message, /赌气/);
});

test('settlement presentation deduplication is isolated by account and pet', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const settlement = {
    settlement_id: '33333333-3333-4333-8333-333333333333', activity_sequence: 1,
    daily_login: { progress_delta: 2 },
  };
  async function createSettlementHarness(accountId, petId, uid) {
    const snapshot = stage4Snapshot({ account_id: accountId, pet_id: petId, settlement });
    const h = createHarness(async url => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: accountId }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: accountId, current_character_uid: uid, binding_status: 'bound',
          pet_id: petId, bound_role_id: 'server-role-a', snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    }, { uid, name: '小猫' }, '/', storage);
    const notices = [];
    h.window.showAlertDialog = message => { notices.push(message); };
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    await waitFor(() => notices.length === 1);
    return notices;
  }

  const first = await createSettlementHarness('account-a', 'pet-a', 'a'.repeat(32));
  const second = await createSettlementHarness('account-b', 'pet-b', 'b'.repeat(32));
  assert.equal(first.length, 1);
  assert.equal(second.length, 1);
});

test('stage 4 uses the versioned service projection for read-only elapsed display', async () => {
  const serverTime = Date.now();
  const snapshot = stage4Snapshot({
    last_settled_at: new Date(serverTime - 2 * 60 * 60 * 1000).toISOString(),
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    client_projection: clientProjection({
      schema_version: 2,
      effective_at: new Date(serverTime - 3 * 60 * 60 * 1000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      valid_until: new Date(serverTime + 60 * 60 * 1000).toISOString(),
      cat_mind_valid_until: undefined,
    }),
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openPanel(h);
  const stats = gridOf(h);
  assert.equal(stats.children[0].children[1].textContent, '70');
  assert.equal(stats.children[1].children[1].textContent, '69');
  assert.equal(stats.children[2].children[1].textContent, '86');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 72);
});

test('legacy v2 status display expires with its single projection window', async () => {
  let monotonic = 0;
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const legacyProjection = clientProjection({
    schema_version: 2,
    effective_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
    server_time: new Date(serverTime).toISOString(),
    valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
    cat_mind_valid_until: undefined,
    status_rules: {
      clamp: { min: 0, max: 100 }, full_threshold: 100,
      paused_during_exploration: true,
      per_hour: { satiety: -6, energy: 0, cleanliness: 0, interaction: 0 },
      mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
    },
  });
  const snapshot = stage4Snapshot({
    last_settled_at: new Date(serverTime).toISOString(),
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    client_projection: legacyProjection,
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', {
    monotonicNow: () => monotonic,
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  monotonic = 10 * 60 * 1000;
  h.window.nekoPetCareEntry.setActionCategory('feeding');
  assert.equal(gridOf(h).children[0].children[1].textContent, '72');
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection, null);
});

test('care and exploration start refresh locally when their config revision is unavailable', async () => {
  for (const operation of ['care', 'exploration']) {
    let monotonic = 0;
    let snapshotReads = 0;
    const businessWrites = [];
    const serverTime = Date.parse('2026-10-21T02:00:00Z');
    const careActions = { feeding: { small_fish: {
      inventory_item: 'small_fish', consume_quantity: 1,
      satiety_delta: 25, progress_delta: 0, feedback_action: 'eat_snack',
    } } };
    const legacyProjection = clientProjection({
      schema_version: 2,
      effective_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
      cat_mind_valid_until: undefined,
    });
    const initial = stage4Snapshot({
      inventory: { small_fish: 1 }, care_actions: careActions,
      client_projection: legacyProjection,
    });
    const refreshed = stage4Snapshot({
      state_version: 2, inventory: { small_fish: 1 }, care_actions: careActions,
      client_projection: clientProjection({ config_revision: 'rules-current' }),
    });
    const h = createHarness(async (url, options = {}) => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (options.method === 'POST' &&
          (url.endsWith('/care-actions') || url.endsWith('/explorations'))) {
        businessWrites.push({ url, body: options.body });
        throw new Error('a write without config revision must not reach transport');
      }
      if (url.endsWith('/tasks')) {
        return { ok: true, status: 200,
          json: async () => ({ binding_status: 'bound', snapshot: initial }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        snapshotReads += 1;
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          snapshot: snapshotReads === 1 ? initial : refreshed,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    }, { uid: 'local-role-a', name: '小猫 A' }, '/', {
      monotonicNow: () => monotonic,
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    enterCatForm(h);
    await openPanel(h);
    await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false);
    monotonic = 10 * 60 * 1000;
    h.window.nekoPetCareEntry.setActionCategory(
      operation === 'care' ? 'feeding' : 'exploration');
    assert.equal(h.window.nekoPetCareEntry.getState().clientProjection, null);

    const result = operation === 'care'
      ? await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish')
      : await h.window.nekoPetCareEntry.startExploration('window-path');
    assert.equal(result, null);
    assert.equal(businessWrites.length, 0);
    assert.equal(snapshotReads, 2);
    assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.config_revision,
      'rules-current');
    assert.equal(operation === 'care'
      ? h.window.nekoPetCareEntry.getState().actionError
      : h.window.nekoPetCareEntry.getState().explorationError,
    'pet_care_config_revision_required');
  }
});

test('a service without config revision CAS cannot receive current-config writes', async () => {
  const writes = [];
  const snapshot = stage4Snapshot({
    inventory: { small_fish: 1 },
    service_capabilities: { config_revision_write_cas: 0 },
    care_actions: { feeding: { small_fish: {
      inventory_item: 'small_fish', consume_quantity: 1,
      satiety_delta: 25, progress_delta: 0, feedback_action: 'eat_snack',
    } } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      writes.push(options.body);
      throw new Error('unsupported service must not receive current-config writes');
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);
  assert.equal(writes.length, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().actionError,
    'pet_care_config_revision_cas_unavailable');
});

test('play lease issuance refreshes locally when its config revision is unavailable', async () => {
  let monotonic = 0;
  let snapshotReads = 0;
  const leaseWrites = [];
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const initial = formalSnapshot({
    client_projection: clientProjection({
      schema_version: 2,
      effective_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
      cat_mind_valid_until: undefined,
    }),
    play_actions: { yarn: { minimum_duration_seconds: 1 } },
  });
  const refreshed = formalSnapshot({
    state_version: 2,
    client_projection: clientProjection({ config_revision: 'rules-current' }),
    play_actions: { yarn: { minimum_duration_seconds: 1 } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      leaseWrites.push(options.body);
      throw new Error('an unversioned play lease must not reach transport');
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200,
        json: async () => ({ binding_status: 'bound', snapshot: initial }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: snapshotReads === 1 ? initial : refreshed,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', {
    monotonicNow: () => monotonic,
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false);
  monotonic = 10 * 60 * 1000;
  h.window.nekoPetCareEntry.setActionCategory('play');
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection, null);

  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'missing-config-revision', source: 'yarn', trigger: 'yarn-click',
      timestamp: Date.now(), waitUntil(value) { waits.push(Promise.resolve(value)); },
    },
  }));
  assert.equal(waits.length, 1);
  assert.equal(await waits[0], false);
  assert.equal(leaseWrites.length, 0);
  assert.equal(snapshotReads, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.config_revision,
    'rules-current');
});

test('v3 status display continues after the short Cat Mind bias expires or is rejected', async () => {
  let monotonic = 0;
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const snapshot = stage4Snapshot({
    last_settled_at: new Date(serverTime).toISOString(),
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
      status_rules: {
        clamp: { min: 0, max: 100 }, full_threshold: 100,
        paused_during_exploration: true,
        per_hour: { satiety: -6, energy: 0, cleanliness: 0, interaction: 0 },
        mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
      },
    }),
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', {
    monotonicNow: () => monotonic,
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);

  let rejectedBiasClears = 0;
  h.window.nekoCatMind = {
    setPetCareProjection() { return false; },
    clearPetCareProjection() { rejectedBiasClears += 1; },
  };
  await h.window.nekoPetCareEntry.refreshBinding('projection-domain-separation-test');
  assert.equal(rejectedBiasClears, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.schema_version, 3);

  await openPanel(h);
  assert.equal(gridOf(h).children[0].children[1].textContent, '72');
  monotonic = 10 * 60 * 1000;
  h.window.nekoPetCareEntry.setActionCategory('feeding');
  assert.equal(gridOf(h).children[0].children[1].textContent, '71');
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.schema_version, 3);
});

test('v3 status display stays frozen while the authoritative exploration is active', async () => {
  let monotonic = 0;
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const snapshot = stage4Snapshot({
    last_settled_at: new Date(serverTime).toISOString(),
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    exploration: {
      exploration_id: 'exp-active', status: 'active', active: true,
      started_at: new Date(serverTime).toISOString(),
      ends_at: new Date(serverTime + 2 * 60 * 60 * 1000).toISOString(),
    },
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
      status_rules: {
        clamp: { min: 0, max: 100 }, full_threshold: 100,
        paused_during_exploration: true,
        per_hour: { satiety: -6, energy: 4, cleanliness: -2, interaction: -1 },
        mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
      },
    }),
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', {
    monotonicNow: () => monotonic,
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  monotonic = 60 * 60 * 1000;
  h.window.nekoPetCareEntry.setActionCategory('exploration');
  assert.equal(gridOf(h).children[0].children[1].textContent, '72');
  assert.equal(gridOf(h).children[1].children[1].textContent, '61');
  assert.equal(gridOf(h).children[2].children[1].textContent, '88');
  assert.equal(gridOf(h).children[3].children[1].textContent, '67');
});

test('official surface ignores local fill-status metadata and exposes no product route', async () => {
  const initial = stage4Snapshot({
    status_values: { satiety: 12, energy: 23, cleanliness: 34, mood: 45, progress: 56 },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: initial, dev_tools: { fill_status: true },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  assert.equal(panelOf(h).children.some(child => child.className === 'neko-pet-care-dev-tools'), false);
  assert.equal(h.window.nekoPetCareEntry.debugFillStatus, undefined);
  assert.equal(h.window.nekoPetCareEntry.getState().devTools, undefined);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 12);
});

test('service content directory independently gates the mailbox and local petting capability', async () => {
  const lockedDirectory = {
    exploration_mailbox: {
      unlock_id: 'cat-nest-mailbox', unlocked: false,
      location_id: 'window-path', first_letter_style: 'cat-nest-mailbox',
    },
    cat1_petting: {
      unlock_id: 'cat1-petting-action', unlocked: false,
      content_id: 'cat1_petting',
      feedback_action: 'cat1_petting',
    },
  };
  const unlocked = stage4Snapshot({
    service_capabilities: { content_directory: 1 },
    content_directory: {
      exploration_mailbox: { ...lockedDirectory.exploration_mailbox, unlocked: true },
      cat1_petting: { ...lockedDirectory.cat1_petting, unlocked: true },
    },
    letters: [{ title: '窗边来信', text: '今天看见了云。' }],
  });
  const initial = stage4Snapshot({
    service_capabilities: { content_directory: 1 },
    content_directory: lockedDirectory,
    letters: [{ title: '已到达但信箱未解锁', text: '不应开放持久信箱入口。' }],
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const pettingAvailability = [];
  h.window.NekoCatIdlePresentation = {
    setCat1PettingUnlocked(value) { pettingAvailability.push(value); },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  assert.equal(navButtonOf(h, 'letters').hidden, true);
  selectView(h, 'letters');
  assert.equal(h.window.nekoPetCareEntry.getState().selectedView, 'overview');
  assert.equal(lettersOf(h).hidden, true);
  assert.equal(pettingAvailability.at(-1), false);

  h.window.nekoPetCareEntry.setBindingResult({
    account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
    pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: unlocked,
    generation: h.window.nekoPetCareEntry.getState().generation,
  });
  assert.equal(navButtonOf(h, 'letters').hidden, false);
  selectView(h, 'letters');
  const letters = lettersOf(h);
  assert.equal(letters.hidden, false);
  assert.equal(letters.dataset.letterStyle, 'cat-nest-mailbox');
  const list = letters.children.find(child => child.className === 'neko-pet-care-letters-list');
  assert.equal(list.children[0].dataset.letterStyle, 'cat-nest-mailbox');
  assert.equal(pettingAvailability.at(-1), true);

  const applyBoundSnapshot = snapshot => h.window.nekoPetCareEntry.setBindingResult({
    account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
    pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
    generation: h.window.nekoPetCareEntry.getState().generation,
  });
  applyBoundSnapshot({
    ...unlocked, state_version: 2,
    content_directory: {
      ...unlocked.content_directory,
      cat1_petting: { ...unlocked.content_directory.cat1_petting, content_id: 'another_action' },
    },
  });
  assert.equal(pettingAvailability.at(-1), false,
    'an unlocked entry with the wrong content id must remain closed');
  applyBoundSnapshot({
    ...unlocked, state_version: 3,
    content_directory: {
      ...unlocked.content_directory,
      cat1_petting: { ...unlocked.content_directory.cat1_petting, feedback_action: 'another_action' },
    },
  });
  assert.equal(pettingAvailability.at(-1), false,
    'an unlocked entry with the wrong feedback action must remain closed');
  applyBoundSnapshot({ ...unlocked, state_version: 4 });
  assert.equal(pettingAvailability.at(-1), true);

  h.window.nekoPetCareEntry.setBindingResult({
    account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'unbound',
    pet_id: 'pet-a', bound_role_id: 'server-role-b', can_rebind: true,
    generation: h.window.nekoPetCareEntry.getState().generation,
  });
  assert.equal(pettingAvailability.at(-1), false);
});

test('service-revealed letters render only safe service images after the mailbox unlock', async () => {
  const snapshot = stage4Snapshot({
    service_capabilities: { content_directory: 1 },
    content_directory: {
      exploration_mailbox: { unlock_id: 'cat-nest-mailbox', unlocked: true },
    },
    exploration: {
      exploration_id: 'exploration-letters-1', active: false, status: 'completed',
      location: {
        id: 'window-path', title: '窗边小路',
        letter_image_url: '/static/assets/neko-idle/pet-care-ui-icon/letters.png',
      },
      letters_revealed: [
        { title: '云边', text: '今天看见云从窗边经过。', image_url: '/static/assets/neko-idle/exploration-window-path.svg' },
        { title: '脚印', text: '小路上留下了一串脚印。' },
        { title: '远程地址', text: '这封信没有可展示的图片。', image_url: 'https://cdn.example/letter-cloud.png' },
      ],
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  assert.equal(navButtonOf(h, 'letters').hidden, false);
  selectView(h, 'letters');
  const list = lettersOf(h).children.find(child => child.className === 'neko-pet-care-letters-list');
  assert.equal(list.children.length, 3);
  const firstImage = list.children[0].children.find(child => child.tagName === 'IMG');
  const fallbackImage = list.children[1].children.find(child => child.tagName === 'IMG');
  const unsafeImage = list.children[2].children.find(child => child.tagName === 'IMG');
  assert.equal(firstImage.src, '/static/assets/neko-idle/exploration-window-path.svg');
  assert.equal(fallbackImage, undefined);
  assert.equal(unsafeImage, undefined);
  assert.match(list.children[0].textContent + list.children[0].children[1].children[1].textContent,
    /今天看见云从窗边经过/);
});

test('letter images reject every non-canonical or non-local v1 asset path', async () => {
  const maliciousUrls = [
    'static/assets/neko-idle/cat-bad.png',
    '/static/assets/other/cat-bad.png',
    '/static/assets/neko-idle/',
    '/static/assets/neko-idle/folder/../cat-bad.png',
    '/static/assets/neko-idle//cat-bad.png',
    '//cdn.example/cat-bad.png',
    '/\\cdn.example/cat-bad.png',
    'http://127.0.0.1/static/assets/neko-idle/cat-bad.png',
    'https://cdn.example/cat-bad.png',
    'javascript:alert(1)',
    'data:image/png;base64,AAAA',
    'file:///static/assets/neko-idle/cat-bad.png',
    'blob:http://127.0.0.1/asset-id',
    'https://user:pass@127.0.0.1/static/assets/neko-idle/cat-bad.png',
    '/static/assets/neko-idle/cat-bad.png?revision=1',
    '/static/assets/neko-idle/cat-bad.png#preview',
    '/static/assets/neko-idle/%2fcat-bad.png',
    '/static/assets/neko-idle/%5Ccat-bad.png',
    '/static/assets/neko-idle/%2e%2e/cat-bad.png',
    ' /static/assets/neko-idle/cat-bad.png',
  ];
  const snapshot = stage4Snapshot({
    service_capabilities: { content_directory: 1 },
    content_directory: {
      exploration_mailbox: { unlock_id: 'cat-nest-mailbox', unlocked: true },
    },
    exploration: {
      exploration_id: 'exploration-malicious-letters', active: false, status: 'completed',
      location: { id: 'window-path', title: '窗边小路' },
      letters_revealed: maliciousUrls.map((imageUrl, index) => ({
        title: `坏地址 ${index + 1}`, text: '不应创建图片节点。', image_url: imageUrl,
      })),
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  selectView(h, 'letters');
  const list = lettersOf(h).children.find(child => child.className === 'neko-pet-care-letters-list');
  assert.equal(list.children.length, maliciousUrls.length);
  list.children.forEach(row => {
    assert.equal(row.children.find(child => child.tagName === 'IMG'), undefined);
  });
});

test('unsafe content-directory item image falls back to the built-in item asset', async () => {
  const snapshot = stage4Snapshot({
    inventory: { teaser_wand: 2 },
    service_capabilities: { content_directory: 1 },
    content_directory: {
      teaser_tool: {
        unlock_id: 'teaser-tool', unlocked: true, item_id: 'teaser_wand',
        asset_url: '/\\cdn.example/cat-teaser.png',
      },
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  selectView(h, 'inventory');
  const image = taskRowDescendant(inventoryOf(h), 'neko-pet-care-inventory-image');
  assert.ok(image);
  assert.equal(image.src, '/static/assets/neko-idle/pet-care-tool/play/cat-teaser.png');
});

test('versioned item directory owns new item labels, images and descriptions across item surfaces', async () => {
  const snapshot = stage4Snapshot({
    inventory: { moon_snack: 2 },
    service_capabilities: { item_directory: 1 },
    item_directory: {
      moon_snack: {
        item_id: 'moon_snack', display_name: '月亮饼干', category: 'food',
        description: '补充精力的夜间点心。',
        asset_url: '/static/assets/neko-idle/pet-care-tool/eat/moon-snack.png',
        acquisition_sources: ['task:daily-work'],
      },
    },
    care_actions: { feeding: { moon_snack: {
      inventory_item: 'moon_snack', consume_quantity: 1,
      satiety_delta: 10, energy_delta: 5, progress_delta: 0,
      feedback_action: 'eat_snack',
    } } },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);

  selectView(h, 'inventory');
  const inventoryCard = taskRowDescendant(inventoryOf(h), 'neko-pet-care-inventory-item');
  assert.ok(inventoryCard);
  assert.equal(inventoryCard.title, '月亮饼干：补充精力的夜间点心。');
  assert.doesNotMatch(inventoryCard.title, /moon_snack/);
  const inventoryImage = taskRowDescendant(inventoryCard, 'neko-pet-care-inventory-image');
  assert.equal(inventoryImage.src, '/static/assets/neko-idle/pet-care-tool/eat/moon-snack.png');

  selectView(h, 'overview');
  actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const actionButton = detailsOf(h).children[1];
  assert.equal(actionButton.textContent, '月亮饼干');
  assert.equal(actionButton.title, '执行月亮饼干：补充精力的夜间点心。');
  const actionImage = taskRowDescendant(actionButton, 'neko-pet-care-content-image');
  assert.ok(actionImage);
  assert.equal(actionImage.src, '/static/assets/neko-idle/pet-care-tool/eat/moon-snack.png');
});

test('item-directory assets stay same-origin and unknown sparse ids stay player-safe', async () => {
  const snapshot = stage4Snapshot({
    inventory: { future_reward_item: 1, moon_snack: 1 },
    service_capabilities: { item_directory: 1 },
    item_directory: {
      future_reward_item: { item_id: 'future_reward_item' },
      moon_snack: {
        item_id: 'moon_snack', display_name: '月亮饼干', category: 'food',
        asset_url: 'https://cdn.example/moon-snack.png',
        acquisition_sources: ['task:daily-work'],
      },
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  selectView(h, 'inventory');
  const list = taskRowDescendant(inventoryOf(h), 'neko-pet-care-inventory-list');
  assert.equal(list.children.length, 2);
  const unknown = list.children.find(card => card.dataset.itemId === 'future_reward_item');
  const unsafe = list.children.find(card => card.dataset.itemId === 'moon_snack');
  assert.equal(unknown.title, '道具');
  assert.doesNotMatch(unknown.title, /future_reward_item/);
  assert.equal(taskRowDescendant(unsafe, 'neko-pet-care-inventory-image'), null);
  assert.equal(unsafe.title, '月亮饼干');
});

test('unsafe exploration image falls back in both the panel and emitted desktop event', async () => {
  const snapshot = stage4Snapshot({
    exploration: {
      exploration_id: 'exploration-unsafe-asset', active: true, status: 'active',
      started_at: '2026-10-21T01:00:00Z', ends_at: '2026-10-21T03:00:00Z',
      location_id: 'window-path',
    },
    exploration_locations: [{
      id: 'window-path', name: '窗边小路', duration_seconds: 1800, costs: {},
      asset_url: '/static/assets/neko-idle/folder/../remote.png',
    }],
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const emitted = [];
  h.window.addEventListener('neko:pet-care:exploration-asset', event => emitted.push(event.detail));
  enterCatForm(h);
  assert.equal(emitted.at(-1).asset_url, '/static/assets/neko-idle/cat-bad.png');
  const nestImage = taskRowDescendant(panelOf(h), 'neko-pet-care-exploration-nest-image');
  assert.ok(nestImage);
  assert.equal(nestImage.src, '/static/assets/neko-idle/cat-bad.png');
});

test('blank exploration location fails closed without persistence or network traffic', async () => {
  const snapshot = stage4Snapshot();
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(),
  };
  const requests = [];
  const h = createHarness(async (url, options = {}) => {
    requests.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', storage);
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const requestCount = requests.length;

  assert.equal(await h.window.nekoPetCareEntry.startExploration('   '), null);
  assert.equal(requests.length, requestCount);
  assert.equal(pendingWriteRecord(storage), null);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationPending, null);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationError,
    'exploration_location_required');
});

test('pending exploration start suppresses Cat Mind and a rejected start restores the idle gate', async () => {
  const initial = stage4Snapshot();
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(), useRealPetCareMode: true,
  };
  let releaseStart;
  let startRequested = false;
  let startRequestBody = null;
  const startResponse = new Promise(resolve => { releaseStart = resolve; });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations') && options.method === 'POST') {
      startRequested = true;
      startRequestBody = JSON.parse(options.body);
      return startResponse;
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', storage);
  await waitFor(() => {
    const state = h.window.nekoPetCareEntry.getState();
    return state.snapshot !== null && !state.bindingPending && !state.taskLoading &&
      !state.accountResumePending;
  });
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareEntry.getState().catLocalActive, true);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);

  const pending = h.window.nekoPetCareEntry.startExploration('window-path');
  await waitFor(() => startRequested);
  assert.equal(startRequestBody.expected_state_version, 1);
  assert.equal(startRequestBody.expected_config_revision, 'candidate-1');
  assert.equal(h.window.nekoPetCareEntry.getState().explorationPending.operation, 'start');
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  assert.equal(h.window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_eat_snack',
  }).reason, 'pet-care-cat-mind-suppressed');

  releaseStart({ ok: false, status: 422, json: async () => ({
    operation_result: { status: 'rejected', code: 'exploration_unavailable' },
  }) });
  assert.equal(await pending, null);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationPending, null);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
  const released = h.window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_eat_snack',
  });
  assert.equal(released.blocked, false);
  assert.equal(released.reason, '');
});

test('confirmed exploration start hands the transient Cat Mind gate to the away gate', async () => {
  const initial = stage4Snapshot();
  const active = {
    exploration_id: 'explore-start-gate-handoff', active: true, status: 'active',
    location_id: 'window-path', started_at: new Date().toISOString(),
    ends_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  };
  const confirmed = stage4Snapshot({ state_version: 2, exploration: active });
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(), useRealPetCareMode: true,
  };
  let releaseStart;
  let startRequested = false;
  const startResponse = new Promise(resolve => { releaseStart = resolve; });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations') && options.method === 'POST') {
      startRequested = true;
      return startResponse;
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', storage);
  await waitFor(() => {
    const state = h.window.nekoPetCareEntry.getState();
    return state.snapshot !== null && !state.bindingPending && !state.taskLoading &&
      !state.accountResumePending;
  });
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareEntry.getState().catLocalActive, true);

  const pending = h.window.nekoPetCareEntry.startExploration('window-path');
  await waitFor(() => startRequested);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  assert.equal(h.window.nekoPetCareMode.isExplorationAway(), false);

  releaseStart({ ok: true, status: 200, json: async () => ({
    operation_result: { status: 'confirmed' }, snapshot: confirmed,
  }) });
  assert.equal(await pending, confirmed);
  const mode = h.window.nekoPetCareMode.getState();
  assert.equal(mode.catMindSuppressed, false);
  assert.equal(mode.explorationAway.active, true);
  assert.equal(mode.explorationAway.activityId, active.exploration_id);
  assert.equal(h.window.nekoPetCareMode.shouldBlockCatMindActionRequest({
    source: 'cat_mind', actionId: 'cat1_eat_snack',
  }).reason, 'pet-care-exploration-away');
});

test('entity exits retire preauthorized yarn before returning to idle Cat Mind', async () => {
  const playActions = {
    yarn: {
      content_id: 'yarn', label: '毛线球', inventory_item: null,
      consume_quantity: null, inventory_quantity: null,
      daily_count: 0, daily_limit: 3, feedback_action: 'cat1_play_yarn',
      minimum_duration_seconds: 1,
    },
  };
  const initial = formalSnapshot({ play_actions: playActions });
  const activeInput = {
    exploration_id: 'explore-after-yarn-authorization', active: true, status: 'active',
    location_id: 'window-path', started_at: '2026-10-21T02:00:00Z',
    ends_at: '2026-10-21T03:00:00Z',
  };
  const active = formalSnapshot({ state_version: 2, play_actions: playActions,
    exploration: activeInput });
  const recalled = formalSnapshot({ state_version: 3, play_actions: playActions,
    exploration: { ...activeInput, active: false, status: 'recalled' } });
  const calls = [];
  let leaseSequence = 0;
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(), useRealPetCareMode: true,
  };
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      leaseSequence += 1;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, lease: {
          lease_id: `lease-test-${leaseSequence}`, pet_id: 'pet-a',
          bound_role_id: 'server-role-a', activity_id: request.activity_id,
          content_id: 'yarn', action_id: 'cat1_play_yarn',
          source: 'cat-yarn-observation-adapter',
          issued_at: '2026-10-21T02:00:00Z', expires_at: '2026-10-21T02:00:30Z',
          issued_state_version: request.expected_state_version, issued_binding_revision: 1,
          minimum_duration_seconds: 1,
        },
      }) };
    }
    if (url.endsWith('/explorations') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: active,
      }) };
    }
    if (url.endsWith('/explorations/explore-after-yarn-authorization/recall')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: recalled,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  }, undefined, '/', storage);
  await waitFor(() => {
    const state = h.window.nekoPetCareEntry.getState();
    return state.snapshot !== null && !state.bindingPending && !state.taskLoading;
  });
  enterCatForm(h);

  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'before-exploration', source: 'yarn', trigger: 'yarn-click',
      timestamp: Date.now(), waitUntil(value) { waits.push(Promise.resolve(value)); },
    },
  }));
  assert.equal((await waits[0]).authorized, true);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);

  assert.equal(await h.window.nekoPetCareEntry.startExploration('window-path'), active);
  assert.equal(h.window.nekoPetCareMode.isExplorationAway(), true);
  assert.equal(h.window.nekoPetCareEntry.requestExplorationRecall({
    source: 'test-nest', presentation: 'cat-form',
  }), true);
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.status === 'recalled');
  await waitFor(() => !h.window.nekoPetCareMode.isExplorationAway());
  assert.equal(h.window.nekoPetCareMode.isExplorationAway(), false);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:pet-care:yarn-click-response', {
    detail: {
      type: 'playground_yarn_click_responded', source: 'cat-yarn-observation-adapter',
      timestamp: Date.now(), detail: {
        activityId: 'playground-yarn-click:before-exploration', originSource: 'yarn',
        userInitiated: true, catResponded: true, terminalStatus: 'done',
        petCareAuthorized: true,
      },
    },
  }));
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.some(call => call.url.includes('/play-leases/') &&
    call.url.endsWith('/complete')), false);

  const secondWaits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'before-cat-form-exit', source: 'yarn', trigger: 'yarn-click',
      timestamp: Date.now(), waitUntil(value) { secondWaits.push(Promise.resolve(value)); },
    },
  }));
  assert.equal((await secondWaits[0]).authorized, true);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: false, reason: 'test-cat-form-left' },
  }));
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:pet-care:yarn-click-response', {
    detail: {
      type: 'playground_yarn_click_responded', source: 'cat-yarn-observation-adapter',
      timestamp: Date.now(), detail: {
        activityId: 'playground-yarn-click:before-cat-form-exit', originSource: 'yarn',
        userInitiated: true, catResponded: true, terminalStatus: 'done',
        petCareAuthorized: true,
      },
    },
  }));
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.some(call => call.url.includes('/play-leases/') &&
    call.url.endsWith('/complete')), false);
});

test('role and account switches retire a ready playground yarn authorization', async () => {
  for (const switchKind of ['role', 'account']) {
    let activeAccount = 'account-a';
    const calls = [];
    const playActions = {
      yarn: {
        content_id: 'yarn', label: '毛线球', inventory_item: null,
        consume_quantity: null, inventory_quantity: null,
        daily_count: 0, daily_limit: 3, feedback_action: 'cat1_play_yarn',
        minimum_duration_seconds: 1,
      },
    };
    const snapshot = formalSnapshot({
      play_actions: playActions,
      activity_counters: { service_day: '2026-10-21', playing: {} },
    });
    const h = createHarness(async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET', body: options.body });
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200, json: async () => ({
          logged_in: true, local_user_id: activeAccount,
          session_generation: activeAccount === 'account-a' ? 'session-a' : 'session-b',
        }) };
      }
      if (url === '/api/pet-care/session/resume') {
        return { ok: true, status: 200, json: async () => resumeEnvelope() };
      }
      if (url.endsWith('/play-leases') && options.method === 'POST') {
        const request = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({
          operation_result: { status: 'confirmed' }, lease: {
            lease_id: `lease-${switchKind}`, pet_id: 'pet-a', bound_role_id: 'server-role-a',
            activity_id: request.activity_id, content_id: 'yarn', action_id: 'cat1_play_yarn',
            source: 'cat-yarn-observation-adapter', issued_state_version: 1,
            issued_binding_revision: 1, minimum_duration_seconds: 1,
            issued_at: '2026-10-21T02:00:00Z', expires_at: '2026-10-21T02:00:30Z',
          },
        }) };
      }
      if (url.includes('/play-leases/') && url.endsWith('/complete')) {
        return { ok: true, status: 200, json: async () => ({
          operation_result: { status: 'confirmed' }, activity_result: { confirmed: true },
          snapshot,
        }) };
      }
      if (url.includes('/api/pet-care/context')) {
        const role = new URL(url, 'http://127.0.0.1').searchParams.get('character_uid');
        if (activeAccount === 'account-a' && role === 'local-role-a') {
          return { ok: true, status: 200, json: async () => ({
            account_id: 'account-a', current_character_uid: 'local-role-a',
            binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          }) };
        }
        return { ok: true, status: 200, json: async () => ({
          account_id: activeAccount, current_character_uid: role,
          binding_status: 'unbound', pet_id: activeAccount === 'account-a' ? 'pet-a' : 'pet-b',
          binding_revision: 1, state_version: 1, can_rebind: true,
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
    }, { uid: 'local-role-a', name: '小猫 A' });
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    enterCatForm(h);

    const activationId = `before-${switchKind}-switch`;
    const waits = [];
    h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
      detail: {
        activationId, source: 'yarn', trigger: 'yarn-click', timestamp: Date.now(),
        waitUntil(value) { waits.push(Promise.resolve(value)); },
      },
    }));
    assert.equal((await waits[0]).authorized, true, switchKind);
    assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true, switchKind);

    if (switchKind === 'role') {
      h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
        character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch',
      } }));
      await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
      h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
        character_uid: 'local-role-a', character_name: '小猫 A', reason: 'test-role-return',
      } }));
    } else {
      activeAccount = 'account-b';
      await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
      assert.equal(h.window.nekoPetCareEntry.getState().bindingStatus, 'unbound');
      activeAccount = 'account-a';
      await h.window.nekoPetCareEntry.refreshAuthStatus('external-session-change');
    }
    await waitFor(() => {
      const state = h.window.nekoPetCareEntry.getState();
      return state.accountId === 'account-a' && state.currentCharacterUid === 'local-role-a' &&
        state.bindingStatus === 'bound' && state.serviceStatus === 'ready' && state.snapshot !== null;
    });
    assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false, switchKind);

    h.window.dispatchEvent(new h.window.CustomEvent('neko:pet-care:yarn-click-response', {
      detail: {
        type: 'playground_yarn_click_responded', source: 'cat-yarn-observation-adapter',
        timestamp: Date.now(), detail: {
          activityId: `playground-yarn-click:${activationId}`, originSource: 'yarn',
          userInitiated: true, catResponded: true, terminalStatus: 'done',
          petCareAuthorized: true,
        },
      },
    }));
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls.filter(call => call.url.includes('/play-leases/') &&
      call.url.endsWith('/complete')).length, 0, switchKind);
    const current = h.window.nekoPetCareEntry.getState().snapshot;
    assert.equal(Number(current.activity_counters.playing.yarn || 0), 0, switchKind);
    assert.equal(current.status_values.progress, 13, switchKind);
  }
});

test('a late playground lease response cannot revive play after cat-form exit', async () => {
  const calls = [];
  let markIssueStarted;
  let releaseIssue;
  const issueStarted = new Promise(resolve => { markIssueStarted = resolve; });
  const issueBarrier = new Promise(resolve => { releaseIssue = resolve; });
  const snapshot = formalSnapshot({
    play_actions: { yarn: {
      content_id: 'yarn', label: '毛线球', inventory_item: null,
      consume_quantity: null, inventory_quantity: null,
      daily_count: 0, daily_limit: 3, feedback_action: 'cat1_play_yarn',
      minimum_duration_seconds: 1,
    } },
    activity_counters: { service_day: '2026-10-21', playing: {} },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      markIssueStarted();
      await issueBarrier;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, lease: {
          lease_id: 'lease-after-cat-exit', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          activity_id: request.activity_id, content_id: 'yarn', action_id: 'cat1_play_yarn',
          source: 'cat-yarn-observation-adapter', issued_state_version: 1,
          issued_binding_revision: 1, minimum_duration_seconds: 1,
          issued_at: '2026-10-21T02:00:00Z', expires_at: '2026-10-21T02:00:30Z',
        },
      }) };
    }
    if (url.includes('/play-leases/') && url.endsWith('/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, activity_result: { confirmed: true }, snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);

  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'lease-in-flight', source: 'yarn', trigger: 'yarn-click',
      timestamp: Date.now(), waitUntil(value) { waits.push(Promise.resolve(value)); },
    },
  }));
  await issueStarted;
  assert.equal(h.window.nekoPetCareEntry.getState().playPending.phase, 'lease');
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: false, reason: 'test-cat-form-left-during-lease' },
  }));
  assert.equal(h.window.nekoPetCareEntry.getState().playPending, null);
  releaseIssue();
  assert.equal(await waits[0], false);
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity, null);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:pet-care:yarn-click-response', {
    detail: {
      type: 'playground_yarn_click_responded', source: 'cat-yarn-observation-adapter',
      timestamp: Date.now(), detail: {
        activityId: 'playground-yarn-click:lease-in-flight', originSource: 'yarn',
        userInitiated: true, catResponded: true, terminalStatus: 'done',
        petCareAuthorized: true,
      },
    },
  }));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls.filter(call => call.url.includes('/play-leases/') &&
    call.url.endsWith('/complete')).length, 0);
  const current = h.window.nekoPetCareEntry.getState().snapshot;
  assert.equal(Number(current.activity_counters.playing.yarn || 0), 0);
  assert.equal(current.status_values.progress, 13);
});

test('active exploration resolves its exact location id instead of the first catalogue entry', async () => {
  const locationA = {
    id: 'path-a', name: '地点 A', note: 'A 纸条',
    asset_url: '/static/assets/neko-idle/pet-care-ui-icon/overview.png',
  };
  const locationB = {
    id: 'path-b', name: '地点 B', note: 'B 纸条',
    asset_url: '/static/assets/neko-idle/pet-care-ui-icon/exploration.png',
  };
  const snapshot = stage4Snapshot({
    exploration: {
      exploration_id: 'exploration-path-b', active: true, status: 'active',
      started_at: '2026-10-21T01:00:00Z', ends_at: '2026-10-21T03:00:00Z',
      location_id: 'path-b',
    },
    exploration_locations: [locationA, locationB],
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const emitted = [];
  h.window.addEventListener('neko:pet-care:exploration-asset', event => emitted.push(event.detail));
  enterCatForm(h);

  assert.equal(emitted.at(-1).location_id, 'path-b');
  assert.equal(emitted.at(-1).asset_url, locationB.asset_url);
  assert.equal(emitted.at(-1).note, 'B 纸条');
  assert.equal(taskRowDescendant(panelOf(h),
    'neko-pet-care-exploration-status-location').textContent, '地点 B');
});

test('active exploration pinned location metadata overrides the current catalogue revision', async () => {
  const pinnedLocation = {
    id: 'path-b', name: '出发时的地点 B', note: '出发时的纸条',
    asset_url: '/static/assets/neko-idle/pet-care-ui-icon/letters.png',
  };
  const snapshot = stage4Snapshot({
    exploration: {
      exploration_id: 'exploration-pinned-b', active: true, status: 'active',
      started_at: '2026-10-21T01:00:00Z', ends_at: '2026-10-21T03:00:00Z',
      location_id: 'path-b', location: pinnedLocation,
    },
    exploration_locations: [
      { id: 'path-a', name: '地点 A',
        asset_url: '/static/assets/neko-idle/pet-care-ui-icon/overview.png' },
      { id: 'path-b', name: '新版本地点 B', note: '新版本纸条',
        asset_url: '/static/assets/neko-idle/pet-care-ui-icon/exploration.png' },
    ],
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const emitted = [];
  h.window.addEventListener('neko:pet-care:exploration-asset', event => emitted.push(event.detail));
  enterCatForm(h);

  assert.equal(emitted.at(-1).location_id, 'path-b');
  assert.equal(emitted.at(-1).asset_url, pinnedLocation.asset_url);
  assert.equal(emitted.at(-1).note, '出发时的纸条');
  assert.equal(taskRowDescendant(panelOf(h),
    'neko-pet-care-exploration-status-location').textContent, '出发时的地点 B');
});

test('an unlocked mailbox with no service letters renders one empty state', async () => {
  const snapshot = stage4Snapshot({
    service_capabilities: { content_directory: 1 },
    content_directory: {
      exploration_mailbox: {
        unlock_id: 'cat-nest-mailbox', unlocked: true,
        location_id: 'window-path', first_letter_style: 'cat-nest-mailbox',
      },
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);
  selectView(h, 'letters');
  const list = lettersOf(h).children.find(child => child.className === 'neko-pet-care-letters-list');
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].textContent, '暂无来信');
});

test('stage 4 keeps category and concrete content separate and replaces the snapshot after confirmation', async () => {
  const calls = [];
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    interaction_delta: 40, feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  let releaseAction;
  const actionGate = new Promise(resolve => { releaseAction = resolve; });
  const confirmed = stage4Snapshot({
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 },
    care_actions: careActions,
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      await actionGate;
      const request = JSON.parse(options.body);
      assert.equal(request.category, 'feeding');
      assert.equal(request.content_id, 'small_fish');
      assert.ok(request.idempotency_key);
      assert.equal(request.expected_state_version, 1);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        operation: { status: 'confirmed', category: 'feeding', content_id: 'small_fish', feedback_action: 'eat_snack' },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const wakes = [];
  const feedback = [];
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  h.window.addEventListener('neko:pet-care:wake-cat1', event => wakes.push(event.detail));
  h.window.addEventListener('neko:pet-care:feedback', event => feedback.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openPanel(h);
  actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = detailsOf(h);
  assert.equal(details.children[0].textContent, '喂食');
  const detailsHeightStable = details.children.length;
  details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction !== null);
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, true);
  assert.deepEqual(Array.from(h.window.nekoPetCareMode.state.blockedCatMindActionIds), [
    'cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move',
    'cat1_play_yarn', 'cat2_nap_feedback', 'cat3_sleep_feedback',
  ]);
  const optimistic = h.window.nekoPetCareEntry.getState();
  assert.equal(optimistic.snapshot.status_values.satiety, 72);
  assert.equal(Math.round(optimistic.optimisticSnapshot.status_values.satiety), 97);
  assert.equal(optimistic.optimisticSnapshot.status_values.progress, 13);
  assert.equal(optimistic.optimisticSnapshot.inventory.small_fish, 2);
  assert.equal(details.children.length, detailsHeightStable);
  assert.equal(details.children[1].disabled, true);
  assert.equal(wakes.length, 1);
  assert.equal(feedback.length, 1);
  releaseAction();
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null &&
    h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety === 97);
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
  assert.equal(wakes.length, 1);
  assert.equal(wakes[0].category, 'feeding');
  assert.equal(feedback.length, 1);
  assert.equal(feedback[0].started, true);
  assert.equal(feedback[0].optimistic, true);
  assert.equal(businessPostCount(calls), 1);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish, 1);
});

test('optimistic care settles elapsed status before applying a capped action delta', async () => {
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const projection = clientProjection({
    server_time: new Date(serverTime).toISOString(),
    cat_mind_valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
    status_rules: {
      clamp: { min: 0, max: 100 }, full_threshold: 100,
      paused_during_exploration: true,
      per_hour: { satiety: -10, energy: 0, cleanliness: 0, interaction: 0 },
      mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
    },
  });
  const initial = stage4Snapshot({
    last_settled_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
    status_values: { satiety: 95, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    care_actions: careActions, client_projection: projection,
  });
  const confirmed = stage4Snapshot({
    state_version: 3, last_settled_at: new Date(serverTime).toISOString(),
    status_values: { satiety: 100, energy: 61, cleanliness: 88, mood: 72, progress: 13 },
    inventory: { small_fish: 1 }, care_actions: careActions, client_projection: projection,
  });
  let releaseAction;
  const actionGate = new Promise(resolve => { releaseAction = resolve; });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      await actionGate;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot_required: false,
        binding_status: 'bound', snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', { monotonicNow: () => 0 });
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  assert.equal(gridOf(h).children[0].children[1].textContent, '85');
  const moodBeforeAction = gridOf(h).children[3].children[1].textContent;
  actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = detailsOf(h);
  details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction !== null);

  const pending = h.window.nekoPetCareEntry.getState();
  assert.equal(pending.snapshot.status_values.satiety, 95);
  assert.equal(pending.optimisticSnapshot.status_values.satiety, 100);
  assert.equal(pending.optimisticSnapshot.last_settled_at, new Date(serverTime).toISOString());
  assert.equal(pending.optimisticSnapshot.inventory.small_fish, 2);
  assert.equal(pending.optimisticSnapshot.status_values.progress, 13);
  assert.equal(pending.optimisticSnapshot.status_values.mood, 67);
  assert.equal(gridOf(h).children[0].children[1].textContent, '100');
  assert.equal(gridOf(h).children[3].children[1].textContent, moodBeforeAction);

  releaseAction();
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 100);
});

test('care effect-vector preview clamps every direct status and fully rolls back on rejection', async () => {
  const scenarios = [
    {
      category: 'feeding', categoryIndex: 0, contentId: 'energy_bite',
      action: {
        label: '能量点心', inventory_item: 'small_fish', consume_quantity: 1,
        satiety_delta: 15, energy_delta: 10, cleanliness_delta: -5,
        progress_delta: 9, feedback_action: 'eat_snack',
      },
      expected: { satiety: 100, energy: 100, cleanliness: 85 },
    },
    {
      category: 'cleaning', categoryIndex: 1, contentId: 'conditioning_comb',
      action: {
        label: '护理梳', satiety_delta: -8, energy_delta: 10,
        cleanliness_delta: 20, progress_delta: 9, feedback_action: 'stretch',
      },
      expected: { satiety: 82, energy: 100, cleanliness: 100 },
    },
  ];

  for (const scenario of scenarios) {
    const careActions = { feeding: {}, cleaning: {} };
    careActions[scenario.category][scenario.contentId] = scenario.action;
    const originalStatus = {
      satiety: 90, energy: 95, cleanliness: 90, mood: 67, progress: 13,
    };
    const initial = stage4Snapshot({
      status_values: originalStatus,
      inventory: { small_fish: 2 },
      care_actions: careActions,
    });
    let releaseAction;
    const actionGate = new Promise(resolve => { releaseAction = resolve; });
    const h = createHarness(async (url, options = {}) => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.endsWith('/care-actions') && options.method === 'POST') {
        await actionGate;
        return { ok: true, status: 200, json: async () => ({
          operation_result: { status: 'rejected', code: 'preview_rejected' },
          snapshot_required: false,
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          snapshot: initial,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    }, { uid: 'local-role-a', name: '小猫 A' }, '/', { monotonicNow: () => 0 });
    h.window.NekoCatIdlePresentation = {
      requestCat1Eat: () => true,
      requestCat1Stretch: () => true,
    };
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    enterCatForm(h);
    await openPanel(h);
    actionsOf(h).children[scenario.categoryIndex]
      .dispatchEvent({ type: 'click', stopPropagation() {} });
    const details = detailsOf(h);
    details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
    await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction !== null);

    const pending = h.window.nekoPetCareEntry.getState();
    assert.deepEqual(pending.snapshot.status_values, originalStatus);
    assert.equal(pending.optimisticSnapshot.status_values.satiety, scenario.expected.satiety);
    assert.equal(pending.optimisticSnapshot.status_values.energy, scenario.expected.energy);
    assert.equal(pending.optimisticSnapshot.status_values.cleanliness,
      scenario.expected.cleanliness);
    assert.equal(pending.optimisticSnapshot.status_values.mood, originalStatus.mood);
    assert.equal(pending.optimisticSnapshot.status_values.progress, originalStatus.progress);
    assert.equal(pending.optimisticSnapshot.inventory.small_fish, 2);

    releaseAction();
    await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null);
    const rolledBack = h.window.nekoPetCareEntry.getState();
    assert.equal(rolledBack.optimisticSnapshot, null);
    assert.deepEqual(rolledBack.snapshot.status_values, originalStatus);
    assert.deepEqual(rolledBack.snapshot.inventory, { small_fish: 2 });
    assert.equal(rolledBack.actionError, 'preview_rejected');
  }
});

test('ambiguous care response persists one request and manual retry replays the exact body without replaying feedback', async () => {
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  const confirmed = stage4Snapshot({
    state_version: 2,
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 }, care_actions: careActions,
  });
  const posts = [];
  let attempts = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/session/resume')) {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      attempts += 1;
      posts.push({ body: options.body, key: options.headers['Idempotency-Key'] });
      if (attempts === 1) {
        return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
      }
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  const feedback = [];
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  h.window.addEventListener('neko:pet-care:feedback', event => feedback.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  const pending = h.window.nekoPetCareEntry.getState().pendingWrite;
  assert.equal(pending.kind, 'care-action');
  assert.equal(pending.character_uid, 'local-role-a');
  assert.equal(feedback.length, 1);
  const stored = storage.localStorageValues.get(pendingWriteStorageKey());
  assert.ok(stored);
  assert.doesNotMatch(stored, /access[_-]?token|proof|snapshot|reward|https?:\/\//i);

  await h.window.nekoPetCareEntry.retryPendingWrite();
  assert.equal(attempts, 2);
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(feedback.length, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWrite, null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.state_version, 2);
});

test('two windows serialize one new account write and replay only its durable identity', async () => {
  const storage = {
    localStorageValues: new Map(),
    sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(),
  };
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  const confirmed = stage4Snapshot({
    state_version: 2,
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 }, care_actions: careActions,
  });
  const posts = [];
  let releaseFirst;
  const firstResponse = new Promise(resolve => { releaseFirst = resolve; });
  const fetcher = async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/session/resume')) {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      posts.push({ body: options.body, key: options.headers['Idempotency-Key'] });
      if (posts.length === 1) return firstResponse;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  };
  const first = createHarness(fetcher, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  const second = createHarness(fetcher, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  await waitFor(() => first.window.nekoPetCareEntry.getState().snapshot !== null &&
    second.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(first);
  enterCatForm(second);

  const firstWrite = first.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  const competingWrite = second.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  await waitFor(() => posts.length === 1);
  assert.equal(posts.length, 1);
  await competingWrite;
  assert.equal(posts.length, 1);

  releaseFirst({ ok: false, status: 503,
    json: async () => ({ detail: 'service_unavailable' }) });
  await firstWrite;
  const durable = pendingWriteRecord(storage);
  assert.equal(durable.idempotency_key, posts[0].key);

  await second.window.nekoPetCareEntry.retryPendingWrite();
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(second.window.nekoPetCareEntry.getState().pendingWrite, null);
});

test('missing Web Locks blocks mutations without blocking the confirmed read-only pet snapshot', async () => {
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(), locksUnavailable: true,
  };
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  let writes = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      writes += 1;
      throw new Error('mutation must not be sent without Web Locks');
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  assert.equal(writes, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.state_version, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWrite, null);
  assert.equal(h.window.nekoPetCareEntry.getState().actionError,
    'pet_care_pending_write_lock_unavailable');
});

test('an old-role pending write reconciles through its original route without hydrating that snapshot into the current role', async () => {
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  const confirmed = stage4Snapshot({
    state_version: 2,
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 }, care_actions: careActions,
  });
  const posts = [];
  let roleBReads = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context') && url.includes('local-role-b')) {
      roleBReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-b',
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 1,
        state_version: 1, can_rebind: true,
      }) };
    }
    if (url.endsWith('/pets/local-role-a/care-actions') && options.method === 'POST') {
      posts.push({ url, body: options.body, key: options.headers['Idempotency-Key'] });
      if (posts.length === 1) {
        return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
      }
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/local-role-a')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWrite?.character_uid, 'local-role-a');

  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
    character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch',
  } }));
  await waitFor(() => posts.length === 2 && roleBReads >= 2, 600);
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingWrite === null, 600);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.currentCharacterUid, 'local-role-b');
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(state.snapshot, null);
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(posts.every(call => call.url.endsWith('/pets/local-role-a/care-actions')), true);
});

test('a role switch while pending replay waits on the account lock cannot install the old-role response', async () => {
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(),
  };
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  const oldRoleConfirmed = stage4Snapshot({
    state_version: 2,
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 }, care_actions: careActions,
  });
  const posts = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context') && url.includes('local-role-b')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-b',
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 1,
        state_version: 1, can_rebind: true,
      }) };
    }
    if (url.endsWith('/pets/local-role-a/care-actions') && options.method === 'POST') {
      posts.push({ url, body: options.body, key: options.headers['Idempotency-Key'] });
      if (posts.length === 1) {
        return { ok: false, status: 503,
          json: async () => ({ detail: 'service_unavailable' }) };
      }
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: oldRoleConfirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/local-role-a')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', storage);
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  assert.equal(posts.length, 1);

  let unblock;
  let markBlocked;
  const blocked = new Promise(resolve => { markBlocked = resolve; });
  const gate = new Promise(resolve => { unblock = resolve; });
  const blocker = storage.lockManager.request(
    'neko.pet-care.pending-write.v1:account-a',
    { mode: 'exclusive' },
    async () => { markBlocked(); await gate; },
  );
  await blocked;
  const retry = h.window.nekoPetCareEntry.retryPendingWrite();
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
    character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch-while-waiting',
  } }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().currentCharacterUid === 'local-role-b');
  assert.equal(posts.length, 1);
  unblock();
  await blocker;
  await retry;
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(state.currentCharacterUid, 'local-role-b');
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(state.snapshot, null);
});

test('an ambiguous old-role reconciliation keeps the exact pending request and current role isolated', async () => {
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  const posts = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context') && url.includes('local-role-b')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-b',
        binding_status: 'ordinary-cat', pet_id: 'pet-a', binding_revision: 1,
        state_version: 1, can_rebind: true,
      }) };
    }
    if (url.endsWith('/pets/local-role-a/care-actions') && options.method === 'POST') {
      posts.push({ body: options.body, key: options.headers['Idempotency-Key'] });
      return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
    }
    if (url.includes('/api/pet-care/pets/local-role-a')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
    character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch',
  } }));
  await waitFor(() => posts.length === 2, 600);
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.currentCharacterUid, 'local-role-b');
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(state.snapshot, null);
  assert.equal(state.pendingWrite?.character_uid, 'local-role-a');
  assert.deepEqual(posts[1], posts[0]);
});

test('confirmed care with a failed required snapshot read clears the write and freezes new mutations for refresh only', async () => {
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({ care_actions: careActions });
  let snapshotReads = 0;
  let posts = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/session/resume')) {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({ binding_status: 'bound' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      posts += 1;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      if (snapshotReads > 1) {
        return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
      }
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  h.window.NekoCatIdlePresentation = { requestCat1Eat: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.pendingWrite, null);
  assert.equal(state.petRefreshRequired, true);
  assert.equal(state.pendingWriteError, 'pet_care_refresh_required');
  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);
  assert.equal(posts, 1);
});

test('formal operation replay refreshes its snapshot before unfreezing the next write', async () => {
  const calls = [];
  let snapshotReads = 0;
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = formalSnapshot({ state_version: 4, care_actions: careActions });
  const refreshed = formalSnapshot({
    state_version: 5,
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 },
    care_actions: careActions,
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-care-replay-1', correlation_id: 'correlation-care-replay-1',
        server_time: '2026-10-21T00:00:00Z', replayed: true,
        activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', category: 'feeding', content_id: 'small_fish' },
        state_version: 5, binding_revision: 1, config_revision: 'config-1',
        snapshot_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      const snapshot = snapshotReads === 1 ? initial : refreshed;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', activity_result: { confirmed: true }, snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const result = await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
  assert.equal(result.state_version, 5);
  assert.equal(snapshotReads, 2);
  const operation = calls.find(call => call.url.endsWith('/care-actions'));
  const request = JSON.parse(operation.body);
  assert.equal(request.expected_state_version, 4);
  assert.equal(request.expected_config_revision, 'candidate-1');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().actionError, '');
});

test('stale care config refreshes the authoritative snapshot without retrying the mutation', async () => {
  const calls = [];
  let snapshotReads = 0;
  const careActions = { feeding: { small_fish: {
    inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
    feedback_action: 'eat_snack',
  } } };
  const initial = formalSnapshot({
    care_actions: careActions,
    client_projection: clientProjection({ config_revision: 'rules-old' }),
  });
  const refreshed = formalSnapshot({
    state_version: 2,
    care_actions: careActions,
    client_projection: clientProjection({ config_revision: 'rules-current' }),
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'rejected', code: 'config_revision_conflict' },
        config_revision: 'rules-current', snapshot_required: true,
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: snapshotReads === 1 ? initial : refreshed,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);

  assert.equal(await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish'), null);
  const writes = calls.filter(call => call.url.endsWith('/care-actions'));
  assert.equal(writes.length, 1);
  assert.equal(JSON.parse(writes[0].body).expected_config_revision, 'rules-old');
  assert.equal(snapshotReads, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.config_revision,
    'rules-current');
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWrite, null);
  assert.equal(h.window.nekoPetCareEntry.getState().actionError, 'config_revision_conflict');
  const renderedError = panelOf(h).children.find(
    child => child.className === 'neko-pet-care-action-error'
  );
  assert.equal(renderedError.textContent, '养宠规则已更新，请再试一次');
});

test('stage 4 renders additional food operations from the confirmed service catalog', async () => {
  const snapshot = stage4Snapshot({
    inventory: { small_fish: 1, tuna: 2 },
    care_actions: {
      feeding: {
        small_fish: { label: '小鱼干', inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0 },
        tuna: { label: '金枪鱼', inventory_item: 'tuna', consume_quantity: 1, satiety_delta: 10, progress_delta: 0 },
      },
      cleaning: { brushing: { label: '梳毛', cleanliness_delta: 30, progress_delta: 4 } },
    },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        care_actions: { feeding: { forged: { label: '不应显示' } } },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openPanel(h);
  const feeding = actionsOf(h).children[0];
  feeding.dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = detailsOf(h);
  assert.equal(details.children.length, 3);
  assert.equal(details.children[1].textContent, '小鱼干');
  assert.equal(details.children[1].children[0].textContent, '库存 × 1');
  assert.equal(details.children[2].textContent, '金枪鱼');
  assert.equal(details.children[2].children[0].textContent, '库存 × 2');
  assert.equal(details.children.some(child => child.dataset && child.dataset.contentId === 'forged'), false);
});

test('stage 4 treats the service cleaning available_at as the complete cooldown deadline', async () => {
  const serverNow = Date.parse('2026-10-21T02:00:00Z');
  const careActions = { cleaning: { brushing: {
    cleanliness_delta: 12, progress_delta: 4, cooldown_seconds: 3600,
    feedback_action: 'stretch',
  } } };
  const initial = stage4Snapshot({
    // A past deadline stays available even though the catalog still carries
    // cooldown_seconds; the client must not add the duration a second time.
    cleaning_cooldowns: { brushing: { available_at: new Date(serverNow - 1000).toISOString() } },
    care_actions: careActions,
  });
  const confirmed = stage4Snapshot({
    status_values: { ...initial.status_values, cleanliness: 100 },
    cleaning_cooldowns: { brushing: { available_at: new Date(serverNow + 3600000).toISOString() } },
    care_actions: careActions,
  });
  let postCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      postCalls += 1;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = { requestCat1Stretch: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openPanel(h);
  actionsOf(h).children[1].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = detailsOf(h);
  assert.equal(details.children.length, 2);
  const brushing = details.children[1];
  assert.equal(brushing.dataset.itemId, 'brushing');
  assert.equal(brushing.disabled, false);
  details.dispatchEvent({ type: 'click', target: brushing, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null &&
    h.window.nekoPetCareEntry.getState().snapshot.status_values.cleanliness === 100);
  assert.equal(details.children.length, 2);
  assert.equal(details.children[1].disabled, true);
  assert.match(details.children[1].children[0].textContent, /冷却中/);
  details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
  assert.equal(postCalls, 1);
});

test('stage 4 keeps the confirmed snapshot and diagnostic reason for rollback cleanup', async () => {
  const initial = stage4Snapshot({
    status_values: { satiety: 100, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    care_actions: { feeding: { small_fish: {
      inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25, progress_delta: 0,
      feedback_action: 'eat_snack',
    } } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      return { ok: false, status: 409, json: async () => ({ code: 'satiety_full', detail: 'satiety_full' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const rollbacks = [];
  const wakes = [];
  const feedback = [];
  const cancellations = [];
  h.window.addEventListener('neko:pet-care:action-rollback', event => rollbacks.push(event.detail));
  h.window.addEventListener('neko:pet-care:wake-cat1', event => wakes.push(event.detail));
  h.window.addEventListener('neko:pet-care:feedback', event => feedback.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  h.window.NekoCatIdlePresentation = {
    requestCat1Eat: () => true,
    cancelPetCare: detail => cancellations.push(detail),
  };
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openPanel(h);
  actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = detailsOf(h);
  details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 100);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().actionError, 'satiety_full');
  assert.equal(rollbacks.length, 1);
  assert.equal(rollbacks[0].reason, 'satiety_full');
  assert.equal(wakes.length, 1);
  assert.equal(feedback.length, 1);
  assert.equal(feedback[0].optimistic, true);
  assert.equal(cancellations.length, 1);
  assert.equal(cancellations[0].reason, 'satiety_full');
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot, null);
});

test('pet-care failure hints keep diagnostics internal and show short user messages', async () => {
  const scenarios = [
    ['login_required', '登录状态已失效，请重新登录'],
    ['role_mismatch', '当前角色与宠物绑定不一致，请切回绑定角色'],
    ['inventory_insufficient', '背包中没有可用食物'],
    ['pet_care_activity_conflict', '猫咪正在进行其他活动，请稍后再试'],
    ['pet_care_service_unavailable', '养宠功能暂时不可用，请稍后重试'],
    ['Traceback: File /srv/pet_care/service.py line 91', '这次操作没有完成，请稍后重试'],
  ];
  for (const [serviceCode, userMessage] of scenarios) {
    const initial = stage4Snapshot({
      care_actions: { feeding: { small_fish: {
        inventory_item: 'small_fish', consume_quantity: 1,
        satiety_delta: 25, progress_delta: 0, feedback_action: 'eat_snack',
      } } },
    });
    const h = createHarness(async (url, options = {}) => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({
          snapshot_required: false,
          operation_result: { status: 'rejected', code: serviceCode },
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          snapshot: initial,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    enterCatForm(h);
    await openPanel(h);
    actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
    await h.window.nekoPetCareEntry.performCareAction('feeding', 'small_fish');
    const renderedError = panelOf(h).children.find(
      child => child.className === 'neko-pet-care-action-error'
    );
    assert.equal(h.window.nekoPetCareEntry.getState().actionError, serviceCode,
      'raw diagnostic remains available outside the user copy');
    assert.equal(renderedError.textContent, userMessage);
    assert.equal(renderedError.hidden, false);
    assert.equal(renderedError.textContent.includes(serviceCode), false);
    assert.doesNotMatch(renderedError.textContent, /Traceback|\/srv\/|service\.py|pet_care_/);
  }
});

test('inventory failure copy follows the visible care category', async () => {
  const initial = stage4Snapshot({
    inventory: { wool_comb: 1 },
    care_actions: { cleaning: { brushing: {
      inventory_item: 'wool_comb', consume_quantity: 1,
      cleanliness_delta: 20, progress_delta: 2, feedback_action: 'stretch',
    } } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        snapshot_required: false,
        operation_result: { status: 'rejected', code: 'inventory_insufficient' },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  actionsOf(h).children[1].dispatchEvent({ type: 'click', stopPropagation() {} });
  await h.window.nekoPetCareEntry.performCareAction('cleaning', 'brushing');
  const renderedError = panelOf(h).children.find(
    child => child.className === 'neko-pet-care-action-error'
  );
  assert.equal(h.window.nekoPetCareEntry.getState().actionError, 'inventory_insufficient');
  assert.equal(renderedError.textContent, '缺少这次清洁所需的道具');
});

test('player surfaces replace missing or machine-valued display metadata with neutral copy', async () => {
  const task = {
    task_id: 'daily-work', cycle_key: '2026-10-23', title: 'daily-work',
    status: 'active', claimable: true, reset_policy: 'daily',
    completion_action: { id: 'organize-desk', label: 'organize-desk' },
    reward: { item_id: 'future_food', quantity: 1 },
  };
  const snapshot = formalSnapshot({
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 10 },
    inventory: { future_food: 1, future_toy: 1 },
    tasks: { items: [task] },
    care_actions: { feeding: { future_food: {
      label: 'future_food', inventory_item: 'future_food', consume_quantity: 1,
    } } },
    play_actions: { future_toy: {
      label: 'future_toy', inventory_item: 'future_toy', inventory_quantity: 1,
      consume_quantity: 1,
    } },
    content_directory: { future_capability: {
      unlock_id: 'future_unlock', display_name: 'future_unlock', threshold: 10, unlocked: true,
    } },
    unlocks: ['future_unlock'],
    exploration_locations: [{
      id: 'machine-place', name: 'machine-place',
      description: '去附近走一走。', duration_seconds: 1800, costs: {},
    }],
  });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a',
        user: { display_name: 'account-a' },
      }) };
    }
    if (url.includes('/api/pet-care/pets/') || url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: 'local-role-a' });
  const visibleCopy = node => {
    if (!node) return '';
    return [node.textContent, node.title, node['aria-label'], ...(node.children || []).map(visibleCopy)]
      .filter(Boolean).join(' ');
  };
  const internalIds = /account-a|local-role-a|daily-work|organize-desk|future_food|future_toy|future_unlock|machine-place/;

  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);

  actionsOf(h).children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(detailsOf(h).children[1].textContent, '食物');
  assert.doesNotMatch(visibleCopy(detailsOf(h)), internalIds);

  h.window.nekoPetCareEntry.setActionCategory('exploration');
  assert.doesNotMatch(visibleCopy(detailsOf(h)), internalIds);
  assert.match(visibleCopy(detailsOf(h)), /探索地点/);

  h.window.nekoPetCareEntry.setActionCategory('');
  const progress = progressStatOf(h);
  progress.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.doesNotMatch(visibleCopy(progressUnlockPanelOf(h)), internalIds);
  assert.match(visibleCopy(progressUnlockPanelOf(h)), /解锁内容/);

  selectView(h, 'tasks');
  assert.doesNotMatch(visibleCopy(taskOf(h)), internalIds);
  assert.match(visibleCopy(taskOf(h)), /任务|完成任务/);

  selectView(h, 'inventory');
  assert.doesNotMatch(visibleCopy(inventoryOf(h)), internalIds);

  selectView(h, 'profile');
  const profile = panelOf(h).children.find(child => child.className === 'neko-pet-care-content')
    .children.find(child => child.className === 'neko-pet-care-module neko-pet-care-profile-module');
  assert.doesNotMatch(visibleCopy(profile), internalIds);
  assert.match(visibleCopy(profile), /已连接|当前角色/);
});

function stage5FishTask(status = 'completed') {
  return { task_id: 'daily-work', title: '每日打工',
    description: '整理桌面，完成后可领取小鱼干。', claimable: true, status,
    completion_action: { id: 'organize-desk', label: '整理桌面' }, reset_policy: 'daily',
    reward: { item_id: 'small_fish', quantity: 1 } };
}

function stage6YarnTask(progress = 0) {
  return { task_id: 'daily-yarn-play', title: '毛线球',
    description: '用户主动操作且猫真实回应后增加进度', progress, target: 3,
    progress_source: { category: 'playing', content_id: 'yarn' }, reset_policy: 'daily',
    claimable: false, status: progress >= 3 ? 'completed' : 'active', reward: {} };
}

function stage5Tasks(serviceDay, fishStatus = 'completed') {
  const fish = { ...stage5FishTask(fishStatus), cycle_key: serviceDay };
  const yarn = { ...stage6YarnTask(), cycle_key: serviceDay };
  return { items: [fish, yarn] };
}

test('task, exploration and pending-write surfaces hide transport diagnostics', async () => {
  const taskSnapshot = stage4Snapshot({ tasks: stage5Tasks('2026-10-21', 'active') });
  const taskHarness = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks') && (options.method || 'GET') === 'GET') {
      return { ok: false, status: 503,
        json: async () => ({ detail: RAW_PLAYER_DIAGNOSTIC }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: taskSnapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => taskHarness.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(taskHarness);
  await waitFor(() => taskHarness.window.nekoPetCareEntry.getState().taskError === RAW_PLAYER_DIAGNOSTIC);
  selectView(taskHarness, 'tasks');
  assertSafePlayerFailure(
    taskOf(taskHarness).children.find(child => child.className === 'neko-pet-care-task-error'),
    '任务暂时无法处理，请稍后重试',
  );

  const explorationSnapshot = stage4Snapshot();
  const explorationHarness = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations') && options.method === 'POST') {
      return { ok: false, status: 503,
        json: async () => ({ detail: RAW_PLAYER_DIAGNOSTIC }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: explorationSnapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => explorationHarness.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(explorationHarness);
  await waitFor(() => explorationHarness.window.nekoPetCareEntry.getState().taskLoading === false);
  explorationHarness.window.nekoPetCareEntry.setActionCategory('exploration');
  await explorationHarness.window.nekoPetCareEntry.startExploration('window-path');
  const explorationState = explorationHarness.window.nekoPetCareEntry.getState();
  assert.equal(explorationState.explorationError, RAW_PLAYER_DIAGNOSTIC);
  assert.equal(explorationState.pendingWriteError, RAW_PLAYER_DIAGNOSTIC);
  assertSafePlayerFailure(
    detailsOf(explorationHarness).children.find(
      child => child.className === 'neko-pet-care-exploration-error',
    ),
    '探索暂时无法处理，请稍后重试',
  );
  assertSafePlayerFailure(
    panelOf(explorationHarness).children.find(
      child => child.className === 'neko-pet-care-action-error',
    ),
    '探索暂时无法处理，请稍后重试',
  );
});

test('formal snapshot rejects legacy, duplicate and ambiguous task payloads', async () => {
  const canonical = stage5Tasks('2026-10-17', 'completed');
  const legacyIdFish = { ...canonical.items[0], id: 'daily-work' };
  delete legacyIdFish.task_id;
  const cases = [
    { daily: canonical.items, state: [] },
    { ...canonical, service_day: '2026-10-17' },
    { items: [legacyIdFish, canonical.items[1]] },
    { items: [canonical.items[0], { ...canonical.items[0], cycle_key: '2026-10-18' }] },
    { items: [{ ...canonical.items[0], description: null }, canonical.items[1]] },
  ];

  for (const tasks of cases) {
    const calls = [];
    const snapshot = stage4Snapshot({ tasks });
    const h = createHarness(async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET' });
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200,
          json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a',
          binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'bound' &&
      h.window.nekoPetCareEntry.getState().serviceStatus === 'unavailable');
    const state = h.window.nekoPetCareEntry.getState();
    assert.equal(state.snapshot, null);
    assert.equal(state.tasks, null);
    assert.equal(await h.window.nekoPetCareEntry.claimTask('daily-work'), null);
    assert.equal(calls.some(call => call.url.includes('/tasks/daily-work/claim')), false);
  }
});

test('stage 5 loads independent fish and yarn tasks without a second daily check-in write', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17', 'completed') });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openTasks(h);
  const task = taskOf(h);
  assert.equal(task.hidden, false);
  assert.equal(task.children.length, 3);
  assert.equal(task.children.some(child => child.className === 'neko-pet-care-task-load'), false);
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false &&
    h.window.nekoPetCareEntry.getState().tasks !== null);
  assert.equal(calls.filter(item => item.url.endsWith('/tasks')).length, 1);
  assert.equal(calls.filter(item => item.url.endsWith('/daily-check-in')).length, 0);
  assert.equal(task.children[1].children.length, 2);
  const fishRow = task.children[1].children[0];
  assert.equal(fishRow.children.some(child => child.className === 'neko-pet-care-task-toggle'), true);
  assert.equal(fishRow.children.find(child => child.className === 'neko-pet-care-task-reward').className,
    'neko-pet-care-task-reward');
  assert.equal(fishRow.textContent.includes('0/0'), false);
  const fishClaim = fishRow.children.find(child => child.dataset && child.dataset.taskId);
  assert.ok(fishClaim);
  assert.equal(fishClaim.dataset.taskId, 'daily-work');
  assert.equal(fishClaim.textContent, '领取');
  assert.equal(fishClaim.disabled, false);
  assert.equal(taskRowDescendant(fishRow, 'neko-pet-care-task-tick').textContent, '');
  const collapsedDetails = taskRowDescendant(fishRow, 'neko-pet-care-task-details');
  const collapsedToggle = taskRowDescendant(fishRow, 'neko-pet-care-task-toggle');
  assert.equal(collapsedDetails.hidden, true);
  assert.equal(collapsedToggle.getAttribute('aria-expanded'), 'false');
  collapsedToggle.dispatchEvent({ type: 'click', stopPropagation() {} });
  const expandedFishRow = task.children[1].children[0];
  assert.equal(taskRowTitle(expandedFishRow), '每日打工');
  assert.equal(taskRowDescendant(expandedFishRow, 'neko-pet-care-task-details').hidden, false);
  assert.equal(taskRowDescendant(expandedFishRow, 'neko-pet-care-task-description').textContent,
    '整理桌面，完成后可领取小鱼干。');
  assert.equal(taskRowDescendant(expandedFishRow, 'neko-pet-care-task-toggle').getAttribute('aria-expanded'), 'true');
  const yarnRow = task.children[1].children[1];
  assert.equal(taskRowTitle(yarnRow), '毛线球');
  assert.equal(taskRowProgressSummary(yarnRow).textContent, '0/3');
  assert.equal(taskRowProgress(yarnRow).textContent, '0/3');
  assert.equal(yarnRow.children.some(child => child.dataset && child.dataset.taskId), false);
  assert.equal(await h.window.nekoPetCareEntry.claimTask('daily-yarn-play'), null);
  assert.equal(calls.some(item => item.url.includes('/tasks/daily-yarn-play/claim')), false);
  assert.equal(task.children[2].hidden, true);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 13);
});

test('daily work completes before claim while yarn stays an independent play-progress task', async () => {
  const serviceDay = '2026-10-22';
  const capabilities = { task_complete: 1 };
  const initial = stage4Snapshot({
    inventory: {}, service_capabilities: capabilities,
    tasks: stage5Tasks(serviceDay, 'active'),
  });
  const completed = stage4Snapshot({
    state_version: 2, inventory: {}, service_capabilities: capabilities,
    tasks: stage5Tasks(serviceDay, 'completed'),
  });
  const claimed = stage4Snapshot({
    state_version: 3, inventory: { small_fish: 1 }, service_capabilities: capabilities,
    tasks: stage5Tasks(serviceDay, 'claimed'),
  });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body, headers: options.headers || {} });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks/daily-work/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: completed,
      }) };
    }
    if (url.endsWith('/tasks/daily-work/claim')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed', task_id: 'daily-work',
          cycle_key: serviceDay, reward: { item_id: 'small_fish', quantity: 1 } },
        snapshot: claimed,
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const claimEvents = [];
  h.window.addEventListener('neko:pet-care:task-claimed', event => claimEvents.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);

  let workAction = task.children[1].children[0].children.find(child =>
    child.dataset && child.dataset.taskAction === 'complete');
  assert.ok(workAction);
  assert.equal(workAction.textContent, '整理桌面');
  assert.equal(workAction.dataset.completionActionId, 'organize-desk');
  assert.equal(taskRowProgress(task.children[1].children[1]).textContent, '0/3');
  task.children[1].dispatchEvent({ type: 'click', target: workAction, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().tasks.items[0].status === 'completed');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish || 0, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().tasks.items[1].progress, 0);

  workAction = task.children[1].children[0].children.find(child =>
    child.dataset && child.dataset.taskAction === 'claim');
  assert.ok(workAction);
  assert.equal(workAction.textContent, '领取');
  task.children[1].dispatchEvent({ type: 'click', target: workAction, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().tasks.items[0].status === 'claimed');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().tasks.items[1].progress, 0);
  assert.deepEqual(claimEvents[0].reward, { item_id: 'small_fish', quantity: 1 });

  const completeCall = calls.find(call => call.url.endsWith('/tasks/daily-work/complete'));
  const claimCall = calls.find(call => call.url.endsWith('/tasks/daily-work/claim'));
  const completeBody = JSON.parse(completeCall.body);
  const claimBody = JSON.parse(claimCall.body);
  assert.equal(completeBody.cycle_key, serviceDay);
  assert.equal(completeBody.expected_state_version, 1);
  assert.equal(claimBody.cycle_key, serviceDay);
  assert.equal(claimBody.expected_state_version, 2);
  assert.equal(completeCall.headers['Idempotency-Key'], completeBody.idempotency_key);
  assert.equal(claimCall.headers['Idempotency-Key'], claimBody.idempotency_key);
  assert.equal(calls.some(call => call.url.includes('/tasks/daily-yarn-play/')), false);
  assert.equal(calls.some(call => call.url.endsWith('/daily-check-in')), false);
});

test('one task intent waits for account resume and uses the refreshed task CAS', async () => {
  const serviceDay = '2026-10-23';
  const refreshedServiceDay = '2026-10-24';
  const careActions = { feeding: { small_fish: {
    label: '小鱼干', inventory_item: 'small_fish', consume_quantity: 1, satiety_delta: 25,
    progress_delta: 0, feedback_action: 'eat_snack',
  } } };
  const initial = stage4Snapshot({
    state_version: 1,
    inventory: { small_fish: 1 }, care_actions: careActions,
    service_capabilities: { task_complete: 1 },
    tasks: stage5Tasks(serviceDay, 'active'),
  });
  const refreshed = stage4Snapshot({
    state_version: 2,
    inventory: { small_fish: 1 }, care_actions: careActions,
    service_capabilities: { task_complete: 1 },
    tasks: stage5Tasks(refreshedServiceDay, 'active'),
  });
  const completed = stage4Snapshot({
    state_version: 3, inventory: { small_fish: 1 }, care_actions: careActions,
    service_capabilities: { task_complete: 1 },
    tasks: stage5Tasks(refreshedServiceDay, 'completed'),
  });
  const calls = [];
  let resumeCalls = 0;
  let petReads = 0;
  let releaseInteractionResume;
  const interactionResumeBarrier = new Promise(resolve => { releaseInteractionResume = resolve; });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body,
      headers: options.headers || {} });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumeCalls += 1;
      if (resumeCalls === 2) await interactionResumeBarrier;
      return { ok: true, status: 200, json: async () => resumeEnvelope(
        resumeCalls === 2 ? { pet_refresh_required: true } : {}) };
    }
    if (url.endsWith('/tasks/daily-work/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: completed,
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      petReads += 1;
      const snapshot = petReads === 1 ? initial : refreshed;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null &&
    resumeCalls === 1 && h.window.nekoPetCareEntry.getState().accountResumePending === false);
  enterCatForm(h);
  await openTasks(h);
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false &&
    taskOf(h).children[1].children.length === 2);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-start', {
    detail: { phase: 'waiting', source: 'text' },
  }));

  // The verified interaction starts account settlement immediately before
  // the control click. The same user intent must remain clickable and wait;
  // it must not be discarded or sent with the pre-resume state version.
  assert.equal(h.window.nekoPetCareEntry.getState().accountResumePending, true);
  const workAction = taskOf(h).children[1].children[0].children.find(child =>
    child.dataset && child.dataset.taskAction === 'complete');
  assert.ok(workAction);
  assert.equal(workAction.disabled, false);
  const mutation = h.window.nekoPetCareEntry.completeTask('daily-work');
  await waitFor(() => resumeCalls === 2);
  assert.equal(businessPostCount(calls), 0);

  releaseInteractionResume();
  await mutation;
  const taskCalls = calls.filter(call => call.url.endsWith('/tasks/daily-work/complete'));
  assert.equal(taskCalls.length, 1);
  const request = JSON.parse(taskCalls[0].body);
  assert.equal(request.expected_state_version, 2);
  assert.equal(request.cycle_key, refreshedServiceDay);
  assert.equal(taskCalls[0].headers['Idempotency-Key'], request.idempotency_key);
  assert.equal(h.window.nekoPetCareEntry.getState().tasks.items[0].status, 'completed');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.state_version, 3);
  assert.equal(h.window.nekoPetCareEntry.getState().taskError, '');
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWriteError, '');
});

test('task read ordinary-cat invalidates the stale bound snapshot and task controls', async () => {
  const initial = stage4Snapshot({
    inventory: {}, tasks: stage5Tasks('2026-10-22', 'completed'),
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'ordinary-cat', can_rebind: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.snapshot, null);
  assert.equal(state.tasks, null);
  assert.equal(state.bindingRoleId, '');
  assert.equal(state.canRebind, true);
  assert.equal(state.taskPendingId, '');
  assert.equal(taskOf(h).hidden, true);
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
});

test('task write ordinary-cat invalidates stale tasks instead of keeping a claimable row', async () => {
  const initial = stage4Snapshot({
    inventory: {}, tasks: stage5Tasks('2026-10-22', 'completed'),
  });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks/daily-work/claim')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'ordinary-cat', can_rebind: true,
        activity_result: { confirmed: true },
        operation_result: { status: 'confirmed' },
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false);
  await h.window.nekoPetCareEntry.claimTask('daily-work');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(calls.filter(call => call.url.endsWith('/tasks/daily-work/claim')).length, 1);
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(state.snapshot, null);
  assert.equal(state.tasks, null);
  assert.equal(state.taskPendingId, '');
  assert.equal(state.snapshot?.inventory?.small_fish, undefined);
  assert.equal(taskOf(h).hidden, true);
});

test('new binding snapshot atomically replaces task day without a second task read', async () => {
  const initial = stage4Snapshot({
    inventory: {}, state_version: 1,
    tasks: stage5Tasks('2026-10-21', 'completed'),
  });
  const nextTasks = stage5Tasks('2026-10-22', 'active');
  nextTasks.items[1] = { ...stage6YarnTask(2), cycle_key: '2026-10-22' };
  const next = stage4Snapshot({
    inventory: {}, state_version: 2, tasks: nextTasks,
  });
  let snapshotReads = 0;
  let taskReads = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({ binding_status: 'bound' }) };
    }
    if (url.endsWith('/tasks')) {
      taskReads += 1;
      return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      const snapshot = snapshotReads === 1 ? initial : next;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false);
  assert.equal(h.window.nekoPetCareEntry.getState().tasks.items[0].cycle_key, '2026-10-21');

  await h.window.nekoPetCareEntry.refreshBinding('test-task-day-rollover');
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === false);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.snapshot.state_version, 2);
  assert.equal(state.snapshot.tasks.items[0].cycle_key, '2026-10-22');
  assert.equal(state.tasks.items[0].cycle_key, '2026-10-22');
  assert.equal(state.tasks.items[0].status, 'active');
  assert.equal(state.tasks.items[1].progress, 2);
  assert.equal(taskRowProgressSummary(taskOf(h).children[1].children[1]).textContent, '2/3');
  assert.equal(taskReads, 1);
  assert.equal(state.taskError, '');
});

test('stage 6 completed yarn progress stays non-claimable', async () => {
  const tasks = stage5Tasks('2026-10-17', 'claimed');
  const completedYarn = stage6YarnTask(3);
  tasks.items[1] = { ...completedYarn, cycle_key: '2026-10-17' };
  const snapshot = stage4Snapshot({ tasks });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  const yarnRow = task.children[1].children[1];
  assert.equal(h.window.nekoPetCareEntry.getState().tasks.items[1].status, 'completed');
  assert.equal(taskRowProgressSummary(yarnRow).textContent, '已完成');
  assert.equal(taskRowDescendant(yarnRow, 'neko-pet-care-task-tick').textContent, '✓');
  assert.equal(taskRowProgress(yarnRow).textContent, '3/3');
  assert.equal(yarnRow.children.some(child => child.dataset && child.dataset.taskId), false);
  assert.equal(await h.window.nekoPetCareEntry.claimTask('daily-yarn-play'), null);
  assert.equal(calls.some(item => item.url.includes('/tasks/daily-yarn-play/claim')), false);
});

test('stage 5 does not claim an active fish task', async () => {
  for (const status of ['active']) {
    const tasks = stage5Tasks('2026-10-17', status);
    const snapshot = stage4Snapshot({ tasks });
    const calls = [];
    const h = createHarness(async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET' });
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.endsWith('/tasks')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
          bound_role_id: 'server-role-a', snapshot,
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
          pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
    });
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    enterCatForm(h);
    await openTasks(h);
    const task = taskOf(h);
    await waitFor(() => task.children[1].children.length === 2 &&
      h.window.nekoPetCareEntry.getState().taskLoading === false);
    const claim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
    assert.ok(claim);
    assert.equal(claim.textContent, '未完成');
    assert.equal(claim.disabled, true);
    assert.equal(await h.window.nekoPetCareEntry.claimTask('daily-work'), null);
    assert.equal(calls.some(item => item.url.includes('/tasks/daily-work/claim')), false);
  }
});

test('stage 5 does not invent placeholder copy for sparse task records', async () => {
  const sparseTasks = {
    items: [{ task_id: 'configured-task', cycle_key: '2026-10-17', title: '完成一件事',
      status: 'active', claimable: false, reward: {}, reset_policy: 'daily',
      completion_action: { id: 'configured-action', label: '完成' } }],
  };
  const snapshot = stage4Snapshot({ tasks: sparseTasks });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 1);
  const row = task.children[1].children[0];
  assert.equal(row.textContent.includes('可领取'), false);
  assert.equal(row.textContent.includes('奖励待配置'), false);
  assert.equal(row.children.length, 2);
  assert.equal(taskRowTitle(row), '完成一件事');
});

test('task accordion keeps confirmed yarn progress visible while details stay collapsed', async () => {
  const yarnTasks = {
    items: [{ ...stage6YarnTask(0), cycle_key: '2026-10-17' }],
  };
  const snapshot = stage4Snapshot({ tasks: yarnTasks });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 1);
  const row = task.children[1].children[0];
  assert.equal(taskRowTitle(row), '毛线球');
  assert.equal(taskRowProgressSummary(row).textContent, '0/3');
  assert.equal(taskRowProgress(row).textContent, '0/3');
  assert.equal(taskRowDescendant(row, 'neko-pet-care-task-details').hidden, true);
  assert.equal(row.children.some(child => child.className === 'neko-pet-care-task-reward'), false);
  assert.equal(row.children.some(child => child.dataset && child.dataset.taskId), false);

  taskRowDescendant(row, 'neko-pet-care-task-toggle')
    .dispatchEvent({ type: 'click', stopPropagation() {} });
  const expandedRow = task.children[1].children[0];
  assert.equal(taskRowDescendant(expandedRow, 'neko-pet-care-task-details').hidden, false);
  assert.equal(taskRowProgress(expandedRow).textContent, '0/3');
  assert.equal(taskRowDescendant(expandedRow, 'neko-pet-care-task-progress-track').getAttribute('aria-valuemax'), '3');
  assert.equal(taskRowDescendant(expandedRow, 'neko-pet-care-task-progress-track').getAttribute('aria-valuenow'), '0');
});

test('stage 8 completes and claims a sick recovery task before using its medicine', async () => {
  const activeTask = { task_id: 'recovery-sick', cycle_key: 'recovery:1:sick', title: '求药', status: 'active', claimable: true,
    completion_action: { id: 'find-medicine', label: '寻找药品' },
    recovery_status: 'sick', recovery_item_id: 'medicine', reward: { item_id: 'medicine', quantity: 1 } };
  const completedTask = { ...activeTask, status: 'completed' };
  const claimedTask = { ...completedTask, status: 'claimed' };
  const initialTasks = { items: [activeTask] };
  const completedTasks = { items: [completedTask] };
  const claimedTasks = { items: [claimedTask] };
  const initial = stage4Snapshot({
    negative_status: { sick: { active: true }, sulk: { active: false } },
    inventory: {}, tasks: initialTasks, service_capabilities: { task_complete: 1 },
  });
  const completed = stage4Snapshot({
    state_version: 2,
    negative_status: { sick: { active: true }, sulk: { active: false } },
    inventory: {}, tasks: completedTasks, service_capabilities: { task_complete: 1 },
  });
  const claimed = stage4Snapshot({
    state_version: 3,
    negative_status: { sick: { active: true }, sulk: { active: false } },
    inventory: { medicine: 1 }, tasks: claimedTasks, service_capabilities: { task_complete: 1 },
  });
  const recovered = stage4Snapshot({
    state_version: 4,
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: {}, tasks: claimedTasks, service_capabilities: { task_complete: 1 },
  });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks/recovery-sick/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: completed,
      }) };
    }
    if (url.endsWith('/tasks/recovery-sick/claim')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: claimed,
      }) };
    }
    if (url.endsWith('/status-recovery')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: recovered,
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({ snapshot: initial }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareMode.state.negativePresentation.sick, true);
  assert.equal(h.window.nekoPetCareMode.state.negativePresentation.sulk, false);
  await openTasks(h);
  await waitFor(() => taskOf(h).children[1].children.length === 1 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  let row = taskOf(h).children[1].children[0];
  let complete = row.children.find(child => child.dataset && child.dataset.taskAction === 'complete');
  assert.ok(complete);
  assert.equal(complete.textContent, '寻找药品');
  assert.equal(complete.dataset.completionActionId, 'find-medicine');
  await h.window.nekoPetCareEntry.completeTask('recovery-sick');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.medicine, undefined);
  row = taskOf(h).children[1].children[0];
  let claim = row.children.find(child => child.dataset && child.dataset.taskAction === 'claim');
  assert.ok(claim);
  assert.equal(claim.textContent, '领取');
  assert.equal(claim.disabled, false);

  await h.window.nekoPetCareEntry.claimTask('recovery-sick');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.medicine, 1);
  row = taskOf(h).children[1].children[0];
  const use = row.children.find(child => child.dataset && child.dataset.recoveryStatus === 'sick');
  assert.ok(use);
  assert.equal(use.disabled, false);

  await h.window.nekoPetCareEntry.useRecoveryItem('sick');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.negative_status.sick.active, false);
  assert.equal(h.window.nekoPetCareMode.state.negativePresentation.sick, false);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.medicine, undefined);
  assert.equal(calls.filter(call => call.url.endsWith('/tasks/recovery-sick/complete')).length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/tasks/recovery-sick/claim')).length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/status-recovery')).length, 1);
});

test('active sulk accepts its configured teaser before task claim and blocks other play', async () => {
  const calls = [];
  const terminals = [];
  const tasks = {
    items: [{ task_id: 'recovery-sulk', cycle_key: 'recovery:1:sulk', title: '哄哄她', status: 'active', claimable: true,
      completion_action: { id: 'find-teaser-wand', label: '寻找逗猫棒' },
      recovery_status: 'sulk', recovery_item_id: 'teaser_wand',
      reward: { item_id: 'teaser_wand', quantity: 1 } }],
  };
  const playActions = {
    yarn: { content_id: 'yarn', label: '毛线球', inventory_item: null, consume_quantity: null,
      inventory_quantity: null, daily_count: 0, daily_limit: 3, feedback_action: 'cat1_play_yarn' },
    teaser_wand: { label: '逗猫棒', inventory_item: 'teaser_wand', consume_quantity: 1,
      inventory_quantity: 1, daily_count: 3, daily_limit: 3, feedback_action: 'play_teaser_wand',
      minimum_duration_seconds: 8 },
  };
  const snapshot = stage4Snapshot({
    negative_status: { sick: { active: false }, sulk: { active: true } },
    inventory: { teaser_wand: 1 }, tasks, play_actions: playActions,
    service_capabilities: { task_complete: 1, play_lease_complete: 1 },
  });
  const completedTasks = { ...tasks, items: [{ ...tasks.items[0], status: 'completed' }] };
  const completed = stage4Snapshot({ state_version: 2,
    negative_status: { sick: { active: false }, sulk: { active: true } },
    inventory: { teaser_wand: 1 }, tasks: completedTasks, play_actions: playActions,
    service_capabilities: { task_complete: 1, play_lease_complete: 1 },
  });
  const claimedTasks = { ...tasks, items: [{ ...tasks.items[0], status: 'claimed' }] };
  const claimed = stage4Snapshot({ state_version: 3,
    negative_status: { sick: { active: false }, sulk: { active: true } },
    inventory: { teaser_wand: 2 }, tasks: claimedTasks,
    play_actions: { ...playActions,
      teaser_wand: { ...playActions.teaser_wand, inventory_quantity: 2 } },
    service_capabilities: { task_complete: 1, play_lease_complete: 1 },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks/recovery-sulk/complete')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: completed,
      }) };
    }
    if (url.endsWith('/tasks/recovery-sulk/claim')) {
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot: claimed,
      }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot_required: false,
        lease: {
          lease_id: 'eyJhbGciOiJIUzI1NiJ9.sulk-recovery.signature~v1',
          pet_id: 'pet-a', bound_role_id: 'server-role-a',
          activity_id: request.activity_id, content_id: 'teaser_wand',
          action_id: 'play_teaser_wand', source: 'pet-care-toy',
          issued_at: '2026-10-21T00:00:00Z', issued_state_version: 1,
          issued_binding_revision: 1, minimum_duration_seconds: 8,
          expires_at: '2026-10-21T00:00:30Z',
        },
      }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = {
    requestCat1PlayTeaserWand(options) { terminals.push(options); return true; },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('play');
  let teaser = detailsOf(h).children.find(child => child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.equal(teaser.disabled, false);
  detailsOf(h).dispatchEvent({ type: 'click', target: teaser, stopPropagation() {} });
  await waitFor(() => terminals.length === 1);
  assert.equal(calls.filter(call => call.url.endsWith('/play-leases')).length, 1);
  invokePresentationTerminal(terminals[0], 'cancelled', 'sulk-recovery-preview-finished');
  await waitFor(() => h.window.nekoPetCareEntry.getState().playActivity === null);
  const yarnWaits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: { activationId: 'sulk-yarn', source: 'yarn', timestamp: Date.now(),
      waitUntil(value) { yarnWaits.push(Promise.resolve(value)); } },
  }));
  assert.equal(yarnWaits.length, 1);
  assert.equal(await yarnWaits[0], false);
  await openTasks(h);
  await waitFor(() => taskOf(h).children[1].children.length === 1 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  let row = taskOf(h).children[1].children[0];
  const complete = row.children.find(child => child.dataset && child.dataset.taskAction === 'complete');
  assert.ok(complete);
  assert.equal(complete.textContent, '寻找逗猫棒');
  await h.window.nekoPetCareEntry.completeTask('recovery-sulk');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.teaser_wand, 1);
  row = taskOf(h).children[1].children[0];
  const claim = row.children.find(child => child.dataset && child.dataset.taskAction === 'claim');
  assert.ok(claim);
  assert.equal(claim.textContent, '领取');
  await h.window.nekoPetCareEntry.claimTask('recovery-sulk');
  row = taskOf(h).children[1].children[0];
  assert.equal(row.children.some(child => child.dataset && child.dataset.recoveryStatus === 'sulk'), false);
  assert.equal(await h.window.nekoPetCareEntry.useRecoveryItem('sulk'), null);
  assert.equal(calls.some(call => call.url.endsWith('/status-recovery')), false);
  h.window.nekoPetCareEntry.setActionCategory('play');
  teaser = detailsOf(h).children.find(child => child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.ok(teaser);
  assert.equal(teaser.disabled, false, JSON.stringify(h.window.nekoPetCareEntry.getState()));
  assert.equal(calls.filter(call => call.url.endsWith('/play-leases')).length, 1);
});

test('ordinary toy play remains blocked after its configured daily limit', async () => {
  let playLeasePosts = 0;
  const snapshot = formalSnapshot({
    inventory: { teaser_wand: 1 },
    play_actions: { teaser_wand: {
      content_id: 'teaser_wand', label: '逗猫棒', inventory_item: 'teaser_wand',
      consume_quantity: 1, inventory_quantity: 1, daily_count: 3, daily_limit: 3,
      feedback_action: 'play_teaser_wand', minimum_duration_seconds: 8,
    } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') playLeasePosts += 1;
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('play');
  const teaser = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.equal(teaser.disabled, true);
  assert.match(teaser.title, /今日次数已用完/);
  detailsOf(h).dispatchEvent({ type: 'click', target: teaser, stopPropagation() {} });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(playLeasePosts, 0);
});

test('effective action cooldown deadlines block configured food and toy actions', async () => {
  let carePosts = 0;
  let playLeasePosts = 0;
  const availableAt = '2026-10-21T03:00:00Z';
  const snapshot = formalSnapshot({
    inventory: { small_fish: 1, teaser_wand: 1 },
    care_actions: { feeding: { small_fish: {
      label: '小鱼干', inventory_item: 'small_fish', consume_quantity: 1,
      satiety_delta: 25, feedback_action: 'eat_snack',
      cooldown_available_at: availableAt,
    } } },
    play_actions: { teaser_wand: {
      content_id: 'teaser_wand', label: '逗猫棒', inventory_item: 'teaser_wand',
      consume_quantity: 1, inventory_quantity: 1, daily_count: 0, daily_limit: 3,
      feedback_action: 'play_teaser_wand', minimum_duration_seconds: 8,
      cooldown_available_at: availableAt,
    } },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/care-actions') && options.method === 'POST') carePosts += 1;
    if (url.endsWith('/play-leases') && options.method === 'POST') playLeasePosts += 1;
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);

  h.window.nekoPetCareEntry.setActionCategory('feeding');
  const food = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'small_fish');
  assert.equal(food.disabled, true);
  assert.match(food.title, /冷却中/);
  detailsOf(h).dispatchEvent({ type: 'click', target: food, stopPropagation() {} });

  h.window.nekoPetCareEntry.setActionCategory('play');
  const toy = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.equal(toy.disabled, true);
  assert.match(toy.title, /冷却中/);
  detailsOf(h).dispatchEvent({ type: 'click', target: toy, stopPropagation() {} });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(carePosts, 0);
  assert.equal(playLeasePosts, 0);
});

test('stage 6 reconciles a stale pet snapshot so the yarn task appears on panel open', async () => {
  const staleTask = { task_id: 'prior-task', cycle_key: '2026-10-17', title: '旧任务', status: 'claimed',
    claimable: true, reset_policy: 'daily', reward: { item_id: 'small_fish', quantity: 1 },
    completion_action: { id: 'old-action', label: '旧任务' } };
  const staleTasks = { items: [staleTask] };
  const yarnTask = { task_id: 'daily-yarn-play', cycle_key: '2026-10-17', title: '毛线球', progress: 0, target: 3,
    claimable: false, status: 'active', reset_policy: 'daily', reward: {},
    progress_source: { category: 'playing', content_id: 'yarn' } };
  const reconciledTasks = { ...staleTasks,
    items: [...staleTasks.items, yarnTask] };
  const initial = stage4Snapshot({ tasks: staleTasks });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: stage4Snapshot({ tasks: reconciledTasks }),
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  assert.deepEqual(
    [...task.children[1].children].map(taskRowTitle),
    ['旧任务', '毛线球'],
  );
  assert.equal(task.children[1].children[1].children.length, 4);
  assert.equal(taskRowProgress(task.children[1].children[1]).textContent, '0/3');
});

test('stage 7 panel recall settles exploration without entering the model return chain', async () => {
  const active = {
    exploration_id: 'explore-panel-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const recalled = { ...active, status: 'recalled', active: false };
  const initial = formalSnapshot({ exploration: active });
  const terminal = formalSnapshot({ exploration: recalled, state_version: 2 });
  const recallEvents = [];
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-panel-1/recall')) {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-exploration-recall-1', correlation_id: 'correlation-exploration-recall-1',
        server_time: '2026-10-21T00:00:01Z', activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', kind: 'exploration_recall', reward: { drops: [] } },
        state_version: 2, binding_revision: 1, config_revision: 'config-1',
        snapshot_required: false, snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.addEventListener('neko:pet-care:exploration-recall-request', event => recallEvents.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareEntry.requestExplorationRecall({
    source: 'pet-care-nest', presentation: 'cat-form',
  }), true);
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.status === 'recalled');
  const request = JSON.parse(calls.find(call => call.url.endsWith('/recall')).body);
  assert.equal(request.expected_state_version, 1);
  assert.equal(Object.hasOwn(request, 'exploration_id'), false);
  assert.equal(recallEvents.length, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().catLocalActive, true);
});

test('one exploration recall waits for account resume and uses the refreshed CAS', async () => {
  const active = {
    exploration_id: 'explore-resume-lane-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const initial = formalSnapshot({ state_version: 1, exploration: active });
  const refreshed = formalSnapshot({ state_version: 2, exploration: active });
  const terminal = formalSnapshot({ state_version: 3,
    exploration: { ...active, status: 'recalled', active: false } });
  const calls = [];
  let resumeCalls = 0;
  let petReads = 0;
  let releaseInteractionResume;
  const interactionResumeBarrier = new Promise(resolve => { releaseInteractionResume = resolve; });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body,
      headers: options.headers || {} });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      resumeCalls += 1;
      if (resumeCalls === 2) await interactionResumeBarrier;
      return { ok: true, status: 200, json: async () => resumeEnvelope(
        resumeCalls === 2 ? { pet_refresh_required: true } : {}) };
    }
    if (url.endsWith('/explorations/explore-resume-lane-1/recall')) {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-exploration-resume-recall',
        operation_result: { status: 'confirmed', kind: 'exploration_recall' },
        state_version: 3, binding_revision: 1, config_revision: 'config-1',
        snapshot_required: false, snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      petReads += 1;
      const snapshot = petReads === 1 ? initial : refreshed;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null &&
    resumeCalls === 1 && h.window.nekoPetCareEntry.getState().accountResumePending === false);
  enterCatForm(h);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:widget-interaction-start', {
    detail: { phase: 'waiting', source: 'text' },
  }));
  assert.equal(h.window.nekoPetCareEntry.getState().accountResumePending, true);

  assert.equal(h.window.nekoPetCareEntry.requestExplorationRecall({
    source: 'pet-care-nest', presentation: 'cat-form',
  }), true);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationRecallRequested, true);
  assert.equal(calls.filter(call => call.url.endsWith('/explorations/explore-resume-lane-1/recall')).length, 0);

  releaseInteractionResume();
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.status === 'recalled');
  const recallCalls = calls.filter(call =>
    call.url.endsWith('/explorations/explore-resume-lane-1/recall'));
  assert.equal(recallCalls.length, 1);
  const request = JSON.parse(recallCalls[0].body);
  assert.equal(request.expected_state_version, 2);
  assert.equal(recallCalls[0].headers['Idempotency-Key'], request.idempotency_key);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationRecallRequested, false);
});

test('stage 7 releases the renderer gate when a later authoritative snapshot has no current exploration', async () => {
  const active = {
    exploration_id: 'explore-null-home-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const initial = formalSnapshot({ exploration: active });
  const home = formalSnapshot({ exploration: null, state_version: 2 });
  let snapshotReads = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_id: 'session-a',
      }) };
    }
    if (url === '/api/pet-care/session/resume') {
      return { ok: true, status: 200, json: async () => resumeEnvelope() };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({ binding_status: 'bound' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: initial,
      }) };
    }
    if (url === '/api/pet-care/pets/local-role-a') {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: snapshotReads === 1 ? initial : home,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const gates = [];
  h.window.addEventListener('neko:pet-care:exploration-gate', event => gates.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.exploration_id === 'explore-null-home-1');
  await h.window.nekoPetCareEntry.refreshBinding('authoritative-home-refresh');
  await waitFor(() => gates.some(detail => detail.active === false), 600);
  const released = gates.find(detail => detail.active === false);
  assert.equal(released.activityId, 'explore-null-home-1');
  assert.equal(h.window.nekoPetCareEntry.getState().exploration, null);
});

test('stage 7 model-exit coordinator sends one confirmed recall for concurrent exits', async () => {
  const active = {
    exploration_id: 'explore-model-exit-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const recalled = { ...active, status: 'recalled', active: false };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ exploration: recalled });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-model-exit-1/recall')) {
      calls.push({ url, options });
      await new Promise(resolve => setTimeout(resolve, 5));
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', bound_role_id: 'server-role-a', snapshot: terminal,
        reward: { drops: [] },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const first = h.window.nekoPetCareEntry.ensureExplorationEndedBeforeModelExit({ source: 'return-session' });
  const second = h.window.nekoPetCareEntry.ensureExplorationEndedBeforeModelExit({ source: 'show-current-model' });
  assert.equal(first, second);
  assert.equal(await first !== false, true);
  assert.equal(calls.length, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration?.status, 'recalled');
});

test('role display changes keep the old exploration when recall is rejected', async () => {
  const active = {
    exploration_id: 'explore-role-race-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const initial = stage4Snapshot({ exploration: active });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-role-race-1/recall')) {
      return { ok: false, status: 503, json: async () => ({ code: 'service_unavailable' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', {
    detail: { character_uid: 'local-role-b', reason: 'role-switch' },
  }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().explorationError === 'exploration-recall-failed');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.currentCharacterUid, 'local-role-a');
  assert.equal(state.exploration?.exploration_id, 'explore-role-race-1');
  assert.equal(state.exploration?.active, true);
  assert.equal(calls.filter(call => call.url.endsWith('/recall')).length, 1);
});

test('explicit logout recalls before ending the account session', async () => {
  const active = {
    exploration_id: 'explore-logout-order-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const recalled = { ...active, status: 'recalled', active: false };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ exploration: recalled });
  let loggedIn = true;
  const order = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a' }
        : { logged_in: false, local_user_id: null } };
    }
    if (url.endsWith('/explorations/explore-logout-order-1/recall')) {
      order.push('recall');
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', bound_role_id: 'server-role-a', snapshot: terminal,
      }) };
    }
    if (url.includes('/api/card-drop/oauth/logout')) {
      order.push('logout'); loggedIn = false;
      return { ok: true, status: 200, json: async () => ({}) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url + ' ' + (options.method || 'GET'));
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.logout();
  assert.deepEqual(order, ['recall', 'logout']);
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-out');
  assert.equal(h.window.nekoPetCareEntry.getState().exploration, null);
});

test('explicit logout is not blocked when exploration recall is unavailable', async () => {
  const active = {
    exploration_id: 'explore-logout-unavailable-1', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const initial = stage4Snapshot({ exploration: active });
  const storage = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  let loggedIn = true;
  const order = [];
  const notices = [];
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => loggedIn
        ? { logged_in: true, local_user_id: 'account-a' }
        : { logged_in: false, local_user_id: null } };
    }
    if (url.endsWith('/explorations/explore-logout-unavailable-1/recall')) {
      order.push('recall');
      return { ok: false, status: 503, json: async () => ({ code: 'service_unavailable' }) };
    }
    if (url.includes('/api/card-drop/oauth/logout')) {
      order.push('logout'); loggedIn = false;
      return { ok: true, status: 200, json: async () => ({}) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', storage);
  h.window.showAlertDialog = async (message) => { notices.push(message); };

  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await h.window.nekoPetCareEntry.logout();
  await waitFor(() => notices.some(message => message.includes('探索召回尚未完成')));

  assert.deepEqual(order, ['recall', 'logout']);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.snapshot, null);
  assert.equal(state.tasks, null);
  assert.equal(state.careActions, null);
  assert.equal(state.playActions, null);
  assert.equal(state.exploration, null);
  const lease = JSON.parse(storage.sessionStorageValues.get(
    explorationLeaseStorageKey('account-a'),
  ));
  assert.equal(lease.accountId, 'account-a');
  assert.equal(lease.characterUid, 'local-role-a');
  assert.equal(lease.explorationId, active.exploration_id);
  assert.deepEqual(lease.exploration, initial.exploration);
  assert.equal(Object.hasOwn(lease, 'snapshot'), false);
  assert.equal(Object.hasOwn(lease, 'inventory'), false);
  assert.equal(Object.hasOwn(lease, 'tasks'), false);
});

test('stage 7 rehydrates a terminal exploration with one non-modal result toast after refresh', async () => {
  const terminal = {
    exploration_id: 'explore-refresh-terminal', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  };
  const snapshot = stage4Snapshot({ exploration: terminal });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'refresh-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const notices = [];
  const modalNotices = [];
  h.window.showStatusToast = (message, duration, options) => {
    notices.push({ message, duration, options });
    return true;
  };
  h.window.showAlertDialog = (message, options) => modalNotices.push({ message, options });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 1);
  assert.equal(notices[0].message, '探索已完成，收获：逗猫棒 × 1');
  assert.doesNotMatch(notices[0].message, /服务|teaser_wand/);
  assert.equal(notices[0].options.priority, 10);
  assert.equal(modalNotices.length, 0);
  await h.window.nekoPetCareEntry.refreshAuthStatus('pageshow');
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 1);
});

test('stage 7 does not replay a terminal notice in a fresh renderer after reload', async () => {
  const terminal = {
    exploration_id: 'explore-persistent-terminal', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  };
  const snapshot = stage4Snapshot({ exploration: terminal });
  const fetchImpl = async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'reload-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  };
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const first = createHarness(fetchImpl, undefined, '/', persistent);
  const firstNotices = [];
  const firstModalNotices = [];
  first.window.showStatusToast = (message, duration, options) => {
    firstNotices.push({ message, duration, options });
    return true;
  };
  first.window.showAlertDialog = (message, options) =>
    firstModalNotices.push({ message, options });
  await waitFor(() => first.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(firstNotices.length, 1);
  assert.equal(firstNotices[0].message, '探索已完成，收获：逗猫棒 × 1');
  assert.equal(firstModalNotices.length, 0);

  // A reload/new webview gets a new session store, while the device-local
  // presentation record remains available to suppress the same activity.
  const second = createHarness(fetchImpl, undefined, '/', {
    localStorageValues: persistent.localStorageValues,
    sessionStorageValues: new Map(),
  });
  const secondNotices = [];
  const secondModalNotices = [];
  second.window.showStatusToast = (message, duration, options) => {
    secondNotices.push({ message, duration, options });
    return true;
  };
  second.window.showAlertDialog = (message, options) =>
    secondModalNotices.push({ message, options });
  await waitFor(() => second.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(secondNotices.length, 0);
  assert.equal(secondModalNotices.length, 0);
});

test('terminal notice receipt is written only after the toast surface accepts the message', async () => {
  const terminal = {
    exploration_id: 'explore-toast-retry', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  };
  const snapshot = stage4Snapshot({ exploration: terminal });
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'toast-retry-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', persistent);
  const storageKey = 'neko.pet-care.exploration-notice.v3:account-a:server-role-a';
  const notices = [];
  h.window.showStatusToast = () => false;
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(persistent.localStorageValues.has(storageKey), false);

  h.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  await h.window.nekoPetCareEntry.refreshBinding('toast-surface-ready');
  await waitFor(() => notices.length === 1);
  assert.equal(notices[0], '探索已完成，收获：逗猫棒 × 1');
  assert.deepEqual(JSON.parse(persistent.localStorageValues.get(storageKey)).activityIds,
    ['explore-toast-retry']);
});

test('a delayed terminal notice is replaced by the current refresh generation', async () => {
  const initial = stage4Snapshot();
  const terminal = stage4Snapshot({ exploration: {
    exploration_id: 'explore-generation-fence', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  } });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'generation-fence-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  const notices = [];
  h.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const delayed = [];
  const realWindowTimeout = h.window.setTimeout;
  h.window.setTimeout = (callback, delay, ...args) => {
    if (delay === 0) {
      delayed.push(() => callback(...args));
      return delayed.length;
    }
    return realWindowTimeout(callback, delay, ...args);
  };
  const applyTerminal = () => {
    const generation = h.window.nekoPetCareEntry.getState().generation;
    h.window.nekoPetCareEntry.setBindingResult({
      generation, account_id: 'account-a', current_character_uid: 'local-role-a',
      binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: terminal,
    });
  };
  applyTerminal();
  applyTerminal();
  h.window.setTimeout = realWindowTimeout;
  delayed.splice(0).forEach(callback => callback());
  assert.equal(notices.length, 1);
  assert.equal(notices[0], '探索已完成，收获：逗猫棒 × 1');
});

test('a delayed terminal notice cannot cross an account switch', async () => {
  let activeAccount = 'account-a';
  const initial = stage4Snapshot();
  const terminal = stage4Snapshot({ exploration: {
    exploration_id: 'explore-account-fence', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  } });
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: activeAccount,
        session_generation: `session-${activeAccount}`,
      }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => activeAccount === 'account-a'
        ? ({ binding_status: 'bound', needs_binding: false })
        : ({ binding_status: 'needs-binding', needs_binding: true }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', persistent);
  const notices = [];
  h.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const delayed = [];
  const realWindowTimeout = h.window.setTimeout;
  h.window.setTimeout = (callback, delay, ...args) => {
    if (delay === 0) {
      delayed.push(() => callback(...args));
      return delayed.length;
    }
    return realWindowTimeout(callback, delay, ...args);
  };
  h.window.nekoPetCareEntry.setBindingResult({
    generation: h.window.nekoPetCareEntry.getState().generation,
    account_id: 'account-a', current_character_uid: 'local-role-a',
    binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: terminal,
  });
  activeAccount = 'account-b';
  await h.window.nekoPetCareEntry.refreshAuthStatus('test-account-switch');
  assert.equal(h.window.nekoPetCareEntry.getState().accountId, 'account-b');
  h.window.setTimeout = realWindowTimeout;
  delayed.splice(0).forEach(callback => callback());
  assert.equal(notices.length, 0);
  assert.equal(persistent.localStorageValues.has(
    'neko.pet-care.exploration-notice.v3:account-a:server-role-a'), false);
});

test('a delayed terminal notice cannot cross a role switch', async () => {
  const initial = stage4Snapshot();
  const terminal = stage4Snapshot({ exploration: {
    exploration_id: 'explore-role-fence', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
  } });
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'role-fence-session',
      }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => url.includes('local-role-b')
        ? ({ binding_status: 'unbound', needs_binding: false })
        : ({ binding_status: 'bound', needs_binding: false }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', persistent);
  const notices = [];
  h.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const delayed = [];
  const realWindowTimeout = h.window.setTimeout;
  h.window.setTimeout = (callback, delay, ...args) => {
    if (delay === 0) {
      delayed.push(() => callback(...args));
      return delayed.length;
    }
    return realWindowTimeout(callback, delay, ...args);
  };
  h.window.nekoPetCareEntry.setBindingResult({
    generation: h.window.nekoPetCareEntry.getState().generation,
    account_id: 'account-a', current_character_uid: 'local-role-a',
    binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: terminal,
  });
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', {
    detail: { character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch' },
  }));
  assert.equal(h.window.nekoPetCareEntry.getState().currentCharacterUid, 'local-role-b');
  h.window.setTimeout = realWindowTimeout;
  delayed.splice(0).forEach(callback => callback());
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'unbound');
  assert.equal(notices.length, 0);
  assert.equal(persistent.localStorageValues.has(
    'neko.pet-care.exploration-notice.v3:account-a:server-role-a'), false);
});

test('terminal notice history is account/role-scoped and retains more than the latest activity', async () => {
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const run = async (accountId, localRoleId, boundRoleId, activityId) => {
    const snapshot = stage4Snapshot({
      account_id: accountId, pet_id: `pet-${accountId}`, bound_role_id: boundRoleId,
      exploration: {
      exploration_id: activityId, status: 'completed', active: false,
      started_at: new Date(Date.now() - 60000).toISOString(),
      ends_at: new Date(Date.now() - 1000).toISOString(),
      drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 0,
    } });
    const h = createHarness(async url => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200, json: async () => ({
          logged_in: true, local_user_id: accountId, session_generation: `session-${accountId}`,
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: accountId, current_character_uid: localRoleId,
          binding_status: 'bound', pet_id: `pet-${accountId}`,
          bound_role_id: boundRoleId, snapshot,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    }, { uid: localRoleId, name: localRoleId }, '/', {
      localStorageValues: persistent.localStorageValues,
      sessionStorageValues: new Map(),
    });
    const notices = [];
    h.window.showStatusToast = message => {
      notices.push(message);
      return true;
    };
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    await new Promise(resolve => setTimeout(resolve, 5));
    return notices;
  };

  assert.equal((await run('account-a', 'local-role-a', 'server-role-a', 'explore-ledger-a1')).length, 1);
  assert.equal((await run('account-b', 'local-role-a', 'server-role-a', 'explore-ledger-a1')).length, 1);
  assert.equal((await run('account-a', 'local-role-a', 'server-role-a', 'explore-ledger-a2')).length, 1);
  assert.equal((await run('account-a', 'local-role-b', 'server-role-b', 'explore-ledger-a1')).length, 1);
  assert.equal((await run('account-a', 'local-role-a', 'server-role-a', 'explore-ledger-a1')).length, 0);
  const accountLedger = JSON.parse(persistent.localStorageValues.get(
    'neko.pet-care.exploration-notice.v3:account-a:server-role-a',
  ));
  assert.deepEqual(accountLedger.activityIds, ['explore-ledger-a1', 'explore-ledger-a2']);
  assert.equal(accountLedger.roleId, 'server-role-a');
});

test('first ready PetCare episode shows one real toast before marking the device hint', async () => {
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const snapshot = stage4Snapshot();
  const fetchImpl = async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'hint-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  };
  const first = createHarness(fetchImpl, undefined, '/', persistent);
  const notices = [];
  first.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  installVisibleCatAnchor(first);
  await waitFor(() => first.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(first);
  await waitFor(() => notices.length === 1);
  assert.match(notices[0], /已开启养宠功能/);
  assert.equal(persistent.localStorageValues.get('neko.pet-care.feature-hint-shown'), '1');

  const second = createHarness(fetchImpl, undefined, '/', {
    localStorageValues: persistent.localStorageValues,
    sessionStorageValues: new Map(),
  });
  second.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  installVisibleCatAnchor(second);
  await waitFor(() => second.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(second);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 1);
});

test('first ready hint is not consumed until a visible toast surface accepts it', async () => {
  const persistent = { localStorageValues: new Map(), sessionStorageValues: new Map() };
  const snapshot = stage4Snapshot();
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'hint-retry-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, undefined, '/', persistent);
  installVisibleCatAnchor(h);
  h.window.showStatusToast = () => false;
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(persistent.localStorageValues.has('neko.pet-care.feature-hint-shown'), false);

  const notices = [];
  h.window.showStatusToast = message => {
    notices.push(message);
    return true;
  };
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-return-ball-state', {
    detail: { visible: true, reason: 'toast-surface-ready' },
  }));
  await waitFor(() => notices.length === 1);
  assert.match(notices[0], /已开启养宠功能/);
  assert.equal(persistent.localStorageValues.get('neko.pet-care.feature-hint-shown'), '1');
});

test('stage 7 suppresses the terminal notice during a model-exit recall', async () => {
  const active = {
    exploration_id: 'explore-model-exit', status: 'active', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const recalled = { ...active, status: 'recalled', active: false };
  const snapshot = stage4Snapshot({ exploration: active });
  const recalledSnapshot = stage4Snapshot({ exploration: recalled });
  const notices = [];
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({
        logged_in: true, local_user_id: 'account-a', session_generation: 'model-exit-session',
      }) };
    }
    if (url.includes('/api/pet-care/pets/') && url.endsWith('/recall')) {
      return { ok: true, status: 200, json: async () => ({ snapshot: recalledSnapshot }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.showAlertDialog = (message, options) => notices.push({ message, options });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await h.window.nekoPetCareEntry.ensureExplorationEndedBeforeModelExit({
    source: 'model-switch', suppressNotice: true,
  });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 0);
});

test('stage 6 waits for binding and performs one task refresh when the surface opens', async () => {
  const staleFish = stage5FishTask('claimed');
  const staleTasks = { items: [{ ...staleFish, cycle_key: '2026-10-17' }] };
  const yarnTask = { ...stage6YarnTask(), cycle_key: '2026-10-17' };
  const reconciledTasks = { ...staleTasks,
    items: [...staleTasks.items, yarnTask] };
  const initial = stage4Snapshot({ tasks: staleTasks });
  let taskReads = 0;
  let resolveSnapshot;
  const snapshotGate = new Promise(resolve => { resolveSnapshot = resolve; });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      taskReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: stage4Snapshot({ tasks: reconciledTasks }),
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => {
        await snapshotGate;
        return {
          account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
          pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        };
      } };
    }
    throw new Error('unexpected request: ' + url);
  });
  enterCatForm(h);
  const entry = entryOf(h);
  await openPanel(h);
  assert.equal(entry.dataset.expanded, 'false');
  resolveSnapshot();
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  assert.equal(taskReads, 0);
  await openTasks(h);
  assert.equal(entry.dataset.expanded, 'true');
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  assert.equal(taskReads, 1);
  assert.equal(taskRowTitle(task.children[1].children[1]), '毛线球');
});

test('formal completed fish task stays disabled while pending and applies reward only after confirmation', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17', 'completed') });
  const confirmed = stage4Snapshot({
    inventory: { small_fish: 1 }, tasks: stage5Tasks('2026-10-17', 'claimed'),
  });
  let releaseClaim;
  const claimGate = new Promise(resolve => { releaseClaim = resolve; });
  let postCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/tasks/daily-work/claim')) {
      postCalls += 1; await claimGate;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        reward: { item_id: 'small_fish', quantity: 1 },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  await openTasks(h);
  const task = taskOf(h);
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  const claim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.ok(claim);
  assert.equal(claim.dataset.taskId, 'daily-work');
  assert.equal(task.children[1].children[1].children.some(child => child.dataset && child.dataset.taskId), false);
  task.children[1].dispatchEvent({ type: 'click', target: claim, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskPendingId === 'daily-work');
  const pendingClaim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.equal(pendingClaim.disabled, true);
  assert.equal(task.children[1].children.length, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish || 0, 0);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'claim-race-yarn' },
  } }));
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity, null);
  assert.equal(h.window.nekoPetCareEntry.getState().taskPendingId, 'daily-work');
  releaseClaim();
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskPendingId === '' &&
    h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish === 1);
  assert.equal(postCalls, 1);
  const confirmedClaim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.equal(confirmedClaim.textContent, '已领取');
});

test('an obsolete task status rejects the whole snapshot without sending a claim', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17') });
  initial.tasks.items[0].status = 'available';
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/tasks/daily-work/claim')) throw new Error('obsolete task must not be claimed');
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'bound' &&
    h.window.nekoPetCareEntry.getState().serviceStatus === 'unavailable');
  assert.equal(await h.window.nekoPetCareEntry.claimTask('daily-work'), null);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.snapshot, null);
  assert.equal(state.tasks, null);
  assert.equal(state.taskError, '');
  assert.equal(calls.some(item => item.url.includes('/tasks/daily-work/claim')), false);
});

test('stage 5 accepts cumulative progress above the bounded status range', async () => {
  const initial = stage4Snapshot({
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 125 },
    tasks: stage5Tasks('2026-10-17'),
  });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 125);
});

test('progress bar uses the next confirmed unlock threshold and expands unlocked rows directly', async () => {
  const unlockedIds = ['cat-nest-mailbox'];
  const initial = stage4Snapshot({
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 70 },
    unlocks: unlockedIds,
    content_directory: progressContentDirectory(unlockedIds),
    service_capabilities: { content_directory: 1 },
  });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') || url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);

  const progress = progressStatOf(h);
  assert.equal(progress.children[1].children[0].style.width, '50%');
  assert.equal(progress.children[1].getAttribute('aria-valuemax'), '140');
  assert.equal(progress.children[2].children[0].textContent, '下一阶段 · 140 点');
  assert.equal(progress.children[2].children[1].textContent, '撸猫动作');

  progress.dispatchEvent({ type: 'click', stopPropagation() {} });
  const unlockPanel = progressUnlockPanelOf(h);
  assert.equal(unlockPanel.hidden, false);
  assert.equal(unlockPanel.children[0].children[0].textContent, '已解锁内容');
  assert.equal(unlockPanel.children[0].children[1].textContent, '1 项');
  assert.equal(unlockPanel.children[1].children[0].textContent, '猫窝信箱');
  assert.equal(unlockPanel.children.some(child =>
    child.className === 'neko-pet-care-progress-unlock-toggle'), false);
});

test('progress unlock disclosure supports synthetic future content beyond the two current unlocks', async () => {
  const unlockedIds = ['cat-nest-mailbox', 'cat1-petting-action', 'synthetic-future-unlock'];
  const syntheticFutureContent = {
    synthetic_future_content: {
      unlock_id: 'synthetic-future-unlock', unlocked: true,
      threshold: 800, display_name: '测试未来内容',
    },
  };
  const initial = stage4Snapshot({
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 920 },
    unlocks: unlockedIds,
    content_directory: progressContentDirectory(unlockedIds, syntheticFutureContent),
    service_capabilities: { content_directory: 1 },
  });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') || url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);

  const progress = progressStatOf(h);
  assert.equal(progress.children[1].children[0].style.width, '100%');
  assert.equal(progress.children[2].children[0].textContent, '全部阶段已解锁');
  assert.equal(progress.children[2].children[1].textContent, '持续累计');
  progress.dispatchEvent({ type: 'click', stopPropagation() {} });

  const unlockPanel = progressUnlockPanelOf(h);
  assert.equal(unlockPanel.children.filter(child =>
    child.className === 'neko-pet-care-progress-unlock-row').length, 2);
  assert.equal(unlockPanel.children[1].children[0].textContent, '测试未来内容');
  const expand = unlockPanel.children.find(child =>
    child.className === 'neko-pet-care-progress-unlock-toggle');
  assert.equal(expand.textContent, '展开全部 3 项');
  assert.equal(expand.getAttribute('aria-expanded'), 'false');

  expand.dispatchEvent({ type: 'click', stopPropagation() {} });
  const expandedPanel = progressUnlockPanelOf(h);
  assert.equal(expandedPanel.children.filter(child =>
    child.className === 'neko-pet-care-progress-unlock-row').length, 3);
  const collapse = expandedPanel.children.find(child =>
    child.className === 'neko-pet-care-progress-unlock-toggle');
  assert.equal(collapse.textContent, '收起完整列表');
  assert.equal(collapse.getAttribute('aria-expanded'), 'true');
});

test('progress summary keeps the cumulative value and fails closed without service thresholds', async () => {
  const initial = stage4Snapshot({
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 186 },
    unlocks: ['cat-nest-mailbox'],
    content_directory: {
      exploration_mailbox: {
        unlock_id: 'cat-nest-mailbox', unlocked: true, display_name: '猫窝信箱',
      },
    },
    service_capabilities: { content_directory: 1 },
  });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') || url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await openPanel(h);

  const progress = progressStatOf(h);
  assert.equal(progress.children[0].children[1].children[0].textContent, '186');
  assert.equal(progress.children[1].children[0].style.width, '0%');
  assert.equal(progress.children[2].children[0].textContent, '下一阶段数据不可用');
  assert.equal(progress.children[2].children[1].hidden, true);
  assert.equal(progress.dataset.contract, 'missing');
});

test('stage 6 ignores autonomous or incomplete play results without a user yarn lease', async () => {
  const calls = [];
  const snapshot = stage4Snapshot({ activity_counters: { service_day: '2026-10-17', playing: {} } });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'neko-pc',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'untrusted-yarn' },
  } }));
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity, null);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'cat1_local_play_done', source: 'cat1-journey-local', detail: {},
  } }));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(businessPostCount(calls), 0);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 13);
});

test('formal playground yarn requests a service lease before accepting the cat response', async () => {
  const calls = [];
  let snapshotReads = 0;
  let releaseLease;
  const leaseBarrier = new Promise(resolve => { releaseLease = resolve; });
  const initial = formalSnapshot({
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 2 } },
    activity_counters: { service_day: '2026-10-21', playing: {} },
  });
  const refreshed = formalSnapshot({
    state_version: 2,
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 2 } },
    activity_counters: { service_day: '2026-10-21', playing: {} },
  });
  const confirmed = formalSnapshot({
    state_version: 3,
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 2 } },
    activity_counters: { service_day: '2026-10-21', playing: { yarn: 1 } },
    status_values: { ...initial.status_values, progress: 17 },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body, headers: options.headers });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/play-leases/') && url.endsWith('/complete')) {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-play-complete-1', correlation_id: 'correlation-play-complete-1',
        server_time: '2026-10-21T00:00:01Z', activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', content_id: 'yarn',
          activity_id: 'playground-yarn-click:formal-1' },
        state_version: confirmed.state_version, binding_revision: confirmed.binding_revision,
        config_revision: 'config-1', snapshot_required: false, snapshot: confirmed,
      }) };
    }
    if (url.endsWith('/play-leases')) {
      const request = JSON.parse(options.body);
      await leaseBarrier;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, ok: true,
        state_version: 1, binding_revision: 1, snapshot_required: true, lease: {
        lease_id: 'lease-yarn-1', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        activity_id: request.activity_id,
        content_id: 'yarn', action_id: 'cat1_play_yarn',
        source: 'cat-yarn-observation-adapter', issued_at: '2026-10-21T00:00:00Z',
        issued_state_version: 1, issued_binding_revision: 1,
        minimum_duration_seconds: 3,
        expires_at: '2026-10-21T00:00:30Z',
      } }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: snapshotReads === 1 ? initial : refreshed,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'formal-1', source: 'yarn', trigger: 'yarn-click', timestamp: Date.now(),
      waitUntil(value) { waits.push(Promise.resolve(value)); },
    },
  }));
  assert.equal(waits.length, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().playPending.phase, 'lease');
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  releaseLease();
  const authorization = await waits[0];
  assert.equal(authorization.authorized, true);
  // The lease response is immutable for this activity and wins over the
  // older two-second snapshot if config changed while issuance was in flight.
  assert.equal(authorization.minimumDurationMs, 3000);
  assert.equal(snapshotReads, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.state_version, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().playPending, null);
  // The authorized playground response already owns and moves the visible
  // cat until its delayed terminal observation is reported.
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  const issue = calls.find(call => call.url.endsWith('/play-leases'));
  const issueBody = JSON.parse(issue.body);
  assert.equal(issueBody.content_id, 'yarn');
  assert.equal(issueBody.activity_id, 'playground-yarn-click:formal-1');
  assert.equal(issueBody.source, 'cat-yarn-observation-adapter');
  assert.equal(issueBody.expected_state_version, 1);
  assert.equal(issueBody.expected_config_revision, 'candidate-1');
  assert.ok(issueBody.idempotency_key);
  assert.ok(issueBody.idempotency_key.length <= 200);
  assert.equal(issue.headers['Idempotency-Key'], issueBody.idempotency_key);
  const resumeCountBeforeResponse = calls.filter(call => call.url.endsWith('/session/resume')).length;
  await h.window.nekoPetCareEntry.requestAccountResume('focus-during-yarn-response', { force: true });
  assert.equal(calls.filter(call => call.url.endsWith('/session/resume')).length,
    resumeCountBeforeResponse);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:pet-care:yarn-click-response', {
    detail: {
      type: 'playground_yarn_click_responded', source: 'cat-yarn-observation-adapter',
      timestamp: Date.now(), detail: {
        activityId: 'playground-yarn-click:formal-1', originSource: 'yarn',
        userInitiated: true, catResponded: true, terminalStatus: 'done', petCareAuthorized: true,
      },
    },
  }));
  await waitFor(() => calls.some(call => call.url.endsWith('/play-leases/lease-yarn-1/complete')));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot.status_values.progress === 17);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
  const complete = calls.find(call => call.url.endsWith('/play-leases/lease-yarn-1/complete'));
  const completeBody = JSON.parse(complete.body);
  assert.equal(completeBody.expected_state_version, 1);
  assert.equal(completeBody.activity_id, 'playground-yarn-click:formal-1');
  assert.equal(completeBody.terminal_status, 'done');
  assert.ok(completeBody.idempotency_key);
  assert.ok(completeBody.idempotency_key.length <= 200);
  assert.equal(complete.headers['Idempotency-Key'], completeBody.idempotency_key);
  assert.equal(Object.hasOwn(completeBody, 'source'), false);
  assert.equal(Object.hasOwn(completeBody, 'client_id'), false);
  await waitFor(() => calls.filter(call => call.url.endsWith('/session/resume')).length ===
    resumeCountBeforeResponse + 1);
});

test('playground yarn rejects rewardable authorization when its snapshot duration floor is invalid', async () => {
  const calls = [];
  const snapshot = formalSnapshot({
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 0 } },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: {
      activationId: 'invalid-duration', source: 'yarn', trigger: 'yarn-click',
      timestamp: Date.now(), waitUntil(value) { waits.push(Promise.resolve(value)); },
    },
  }));
  assert.equal(waits.length, 1);
  assert.equal(await waits[0], false);
  assert.equal(calls.some(call => call.url.endsWith('/play-leases')), false);
});

test('formal user yarn settles only its own Cat Mind run and rejects a late role generation', async () => {
  const calls = [];
  let releaseFirstLease;
  const firstLeaseGate = new Promise(resolve => { releaseFirstLease = resolve; });
  const initial = formalSnapshot({
    service_capabilities: { play_lease_complete: 1 },
    activity_counters: { service_day: '2026-10-21', playing: {} },
    play_actions: { yarn: { minimum_duration_seconds: 1 } },
  });
  const confirmed = formalSnapshot({
    state_version: 2,
    service_capabilities: { play_lease_complete: 1 },
    activity_counters: { service_day: '2026-10-21', playing: { yarn: 1 } },
    play_actions: { yarn: { minimum_duration_seconds: 1 } },
    status_values: { ...initial.status_values, progress: 17 },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/play-leases/') && url.endsWith('/complete')) {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-yarn-complete-1', correlation_id: 'correlation-yarn-complete-1',
        server_time: '2026-10-21T00:00:01Z', activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', content_id: 'yarn', activity_id: request.activity_id },
        state_version: confirmed.state_version, binding_revision: confirmed.binding_revision,
        config_revision: 'config-1', snapshot_required: false, snapshot: confirmed,
      }) };
    }
    if (url.endsWith('/play-leases')) {
      const request = JSON.parse(options.body);
      if (request.activity_id === 'formal-yarn-1') await firstLeaseGate;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, ok: true, lease: {
        lease_id: 'lease.' + request.activity_id + '~opaque',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', activity_id: request.activity_id,
        content_id: 'yarn', action_id: 'cat1_play_yarn', source: 'cat-yarn-observation-adapter',
        issued_state_version: 1, issued_binding_revision: 1,
        minimum_duration_seconds: 1,
        issued_at: '2026-10-21T00:00:00Z', expires_at: '2026-10-21T00:00:30Z',
      } }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'formal-yarn-1' },
  } }));
  await waitFor(() => calls.some(call => call.url.endsWith('/play-leases')));
  const requestId = 'cat-mind:formal-yarn-1:request';
  const runId = 'cat-mind:formal-yarn-1:run';
  const actionRequest = {
    source: 'cat_mind', actionId: 'cat1_play_yarn', requestId,
    detail: { activityId: 'formal-yarn-1', triggerTypes: ['chat_yarn_drag_completed'] },
  };
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-request', {
    detail: actionRequest,
  }));
  const actionAuthorization = h.window.nekoPetCareEntry.authorizeCatMindYarnAction(actionRequest);
  assert.equal(typeof actionAuthorization?.then, 'function');
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity, null);
  releaseFirstLease();
  const authorizedAction = await actionAuthorization;
  assert.equal(authorizedAction.authorized, true);
  assert.equal(authorizedAction.minimumDurationMs, 1000);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:state-change', { detail: {
    reason: 'action-started', detail: {
      source: 'cat_mind', actionId: 'cat1_play_yarn', activityId: 'formal-yarn-1', requestId, runId,
    },
  } }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().playActivity !== null);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, true);
  let activity = h.window.nekoPetCareEntry.getState().playActivity;
  assert.equal(activity.cat_mind_request_id, requestId);
  assert.equal(activity.cat_mind_run_id, runId);
  assert.equal(activity.account_id, 'account-a');
  assert.equal(activity.current_character_uid, 'local-role-a');
  assert.equal(activity.bound_role_id, 'server-role-a');
  assert.equal(activity.pet_id, 'pet-a');
  const resumeCountDuringAction = calls.filter(call => call.url.endsWith('/session/resume')).length;
  await h.window.nekoPetCareEntry.requestAccountResume('focus-during-cat-response', { force: true });
  assert.equal(calls.filter(call => call.url.endsWith('/session/resume')).length,
    resumeCountDuringAction);
  const terminal = {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
    detail: { activityId: 'formal-yarn-1', requestId, runId },
  };
  for (const invalid of [
    { ...terminal, source: 'pet-care-toy' },
    { ...terminal, actionId: 'play_teaser_wand' },
    { ...terminal, detail: { ...terminal.detail, activityId: 'another-activity' } },
    { ...terminal, detail: { ...terminal.detail, requestId: 'another-request' } },
    { ...terminal, detail: { ...terminal.detail, runId: 'another-run' } },
  ]) {
    h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: invalid }));
  }
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.some(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete')), false);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: terminal }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot.status_values.progress === 17);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
  await waitFor(() => calls.filter(call => call.url.endsWith('/session/resume')).length ===
    resumeCountDuringAction + 1);
  const completedCount = calls.filter(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete')).length;
  assert.equal(completedCount, 1);

  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'formal-yarn-late' },
  } }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().playActivity &&
    h.window.nekoPetCareEntry.getState().playActivity.activity_id === 'formal-yarn-late');
  const lateRequestId = 'cat-mind:formal-yarn-late:request';
  const lateRunId = 'cat-mind:formal-yarn-late:run';
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-request', { detail: {
    source: 'cat_mind', actionId: 'cat1_play_yarn', requestId: lateRequestId,
    detail: { activityId: 'formal-yarn-late', triggerTypes: ['chat_yarn_drag_completed'] },
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:state-change', { detail: {
    reason: 'action-started', detail: {
      source: 'cat_mind', actionId: 'cat1_play_yarn', activityId: 'formal-yarn-late',
      requestId: lateRequestId, runId: lateRunId,
    },
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('chat-avatar-display-updated', { detail: {
    character_uid: 'local-role-b', character_name: '小猫 B', reason: 'test-role-switch',
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
    detail: { activityId: 'formal-yarn-late', requestId: lateRequestId, runId: lateRunId },
  } }));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls.filter(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete')).length,
    completedCount);
});

test('valid user yarn supersedes an in-flight task read without accepting its stale snapshot', async () => {
  const calls = [];
  let releaseTaskRead;
  const taskReadBarrier = new Promise(resolve => { releaseTaskRead = resolve; });
  const serviceDay = '2026-10-21';
  const initial = formalSnapshot({
    tasks: stage5Tasks(serviceDay, 'completed'),
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 1 } },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks') && (options.method || 'GET') === 'GET') {
      await taskReadBarrier;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, ok: true,
        state_version: 1, binding_revision: 1, snapshot_required: false,
        lease: {
          lease_id: 'lease-task-read-yarn', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          activity_id: request.activity_id, content_id: 'yarn', action_id: 'cat1_play_yarn',
          source: 'cat-yarn-observation-adapter', issued_state_version: 1,
          issued_binding_revision: 1, minimum_duration_seconds: 1,
          issued_at: '2026-10-21T00:00:00Z', expires_at: '2026-10-21T00:00:30Z',
        },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);

  const staleTaskRead = h.window.nekoPetCareEntry.refreshTasks();
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskLoading === true);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'task-read-yarn' },
  } }));

  await waitFor(() => calls.some(call => call.url.endsWith('/play-leases')));
  await waitFor(() => h.window.nekoPetCareEntry.getState().playActivity !== null);
  assert.equal(h.window.nekoPetCareEntry.getState().taskLoading, false);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity.activity_id, 'task-read-yarn');

  releaseTaskRead();
  assert.equal(await staleTaskRead, null);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity.activity_id, 'task-read-yarn');
  assert.equal(calls.filter(call => call.url.endsWith('/play-leases')).length, 1);
});

test('rejected play lease replay refreshes the snapshot and never authorizes a rewardable response', async () => {
  const calls = [];
  let snapshotReads = 0;
  const initial = formalSnapshot({ state_version: 4,
    client_projection: clientProjection({ config_revision: 'rules-old' }),
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 2 } } });
  const refreshed = formalSnapshot({ state_version: 5,
    client_projection: clientProjection({ config_revision: 'rules-current' }),
    service_capabilities: { play_lease_complete: 1 },
    play_actions: { yarn: { minimum_duration_seconds: 2 } } });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-play-conflict-1', correlation_id: 'correlation-play-conflict-1',
        server_time: '2026-10-21T00:00:01Z', activity_result: { confirmed: true },
        operation_result: { status: 'rejected', code: 'config_revision_conflict' },
        state_version: 5, binding_revision: 1, config_revision: 'rules-current',
        snapshot_required: true,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', snapshot: snapshotReads === 1 ? initial : refreshed,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const waits = [];
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: { activationId: 'conflict-1', source: 'yarn', trigger: 'yarn-click', timestamp: Date.now(),
      waitUntil(value) { waits.push(Promise.resolve(value)); } },
  }));
  assert.equal(waits.length, 1);
  assert.equal(await waits[0], false);
  const issue = calls.find(call => call.url.endsWith('/play-leases'));
  assert.equal(JSON.parse(issue.body).expected_config_revision, 'rules-old');
  assert.equal(snapshotReads, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.state_version, 5);
  assert.equal(h.window.nekoPetCareEntry.getState().clientProjection.config_revision,
    'rules-current');
  assert.equal(h.window.nekoPetCareEntry.getState().playError, 'config_revision_conflict');
  assert.equal(calls.some(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete')), false);
});

test('optimistic play settles elapsed recovery before applying the action cost', async () => {
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const terminals = [];
  const catalog = { teaser_wand: {
    label: '逗猫棒', inventory_item: 'teaser_wand', consume_quantity: 1, inventory_quantity: 1,
    daily_count: 0, daily_limit: 3, energy_delta: -8, cleanliness_delta: 0,
    interaction_delta: 40, feedback_action: 'play_teaser_wand', minimum_duration_seconds: 8,
  } };
  const projection = clientProjection({
    server_time: new Date(serverTime).toISOString(),
    cat_mind_valid_until: new Date(serverTime + 5 * 60 * 1000).toISOString(),
    status_rules: {
      clamp: { min: 0, max: 100 }, full_threshold: 100,
      paused_during_exploration: true,
      per_hour: { satiety: 0, energy: 10, cleanliness: 0, interaction: 0 },
      mood_weights: { satiety: 0.25, energy: 0.25, cleanliness: 0.25, interaction: 0.25 },
    },
  });
  const initial = formalSnapshot({
    last_settled_at: new Date(serverTime - 60 * 60 * 1000).toISOString(),
    status_values: { satiety: 72, energy: 95, cleanliness: 88, mood: 67, progress: 13 },
    inventory: { teaser_wand: 1 }, play_actions: catalog,
    client_projection: projection, service_capabilities: { play_lease_complete: 1 },
  });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, snapshot_required: false,
        lease: {
          lease_id: 'eyJhbGciOiJIUzI1NiJ9.preview.signature~v1',
          pet_id: 'pet-a', bound_role_id: 'server-role-a',
          activity_id: request.activity_id, content_id: 'teaser_wand',
          action_id: 'play_teaser_wand', source: 'pet-care-toy',
          issued_at: new Date(serverTime).toISOString(), issued_state_version: 1,
          issued_binding_revision: 1, minimum_duration_seconds: 8,
          expires_at: new Date(serverTime + 30 * 1000).toISOString(),
        },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/', { monotonicNow: () => 0 });
  h.window.NekoCatIdlePresentation = {
    requestCat1PlayTeaserWand(options) { terminals.push(options); return true; },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  assert.equal(gridOf(h).children[1].children[1].textContent, '100');
  const moodBeforeAction = gridOf(h).children[3].children[1].textContent;
  h.window.nekoPetCareEntry.setActionCategory('play');
  const toyButton = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'teaser_wand');
  detailsOf(h).dispatchEvent({ type: 'click', target: toyButton, stopPropagation() {} });
  await waitFor(() => terminals.length === 1);

  const pending = h.window.nekoPetCareEntry.getState();
  assert.equal(pending.snapshot.status_values.energy, 95);
  assert.equal(pending.optimisticSnapshot.status_values.energy, 92);
  assert.equal(pending.optimisticSnapshot.last_settled_at, new Date(serverTime).toISOString());
  assert.equal(pending.optimisticSnapshot.inventory.teaser_wand, 1);
  assert.equal(pending.optimisticSnapshot.status_values.progress, 13);
  assert.equal(pending.optimisticSnapshot.status_values.mood, 67);
  assert.equal(gridOf(h).children[1].children[1].textContent, '92');
  assert.equal(gridOf(h).children[3].children[1].textContent, moodBeforeAction);

  invokePresentationTerminal(terminals[0], 'cancelled', 'preview-test-finished');
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity, null);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot, null);
});

test('formal teaser wand obtains a bound lease before starting PlayTeaserWandAction', async () => {
  const calls = [];
  const terminals = [];
  const completionBodies = [];
  let completionAttempts = 0;
  const catalog = { teaser_wand: {
    label: '逗猫棒', inventory_item: 'teaser_wand', consume_quantity: 1, inventory_quantity: 1,
    daily_count: 0, daily_limit: 3, feedback_action: 'play_teaser_wand',
    minimum_duration_seconds: 8,
  } };
  const initial = formalSnapshot({
    service_capabilities: { play_lease_complete: 1 }, inventory: { teaser_wand: 1 },
    play_actions: catalog,
  });
  const confirmed = formalSnapshot({
    state_version: 2,
    service_capabilities: { play_lease_complete: 1 }, inventory: { teaser_wand: 0 },
    status_values: { ...initial.status_values, progress: 21 },
    play_actions: { teaser_wand: { ...catalog.teaser_wand, inventory_quantity: 0, daily_count: 1 } },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body, headers: options.headers });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/play-leases/') && url.endsWith('/complete')) {
      completionAttempts += 1;
      completionBodies.push(JSON.parse(options.body));
      if (completionAttempts === 1) throw new TypeError('simulated lost completion response');
      return { ok: true, status: 200, json: async () => ({
        request_id: 'request-teaser-complete-1', correlation_id: 'correlation-teaser-complete-1',
        server_time: '2026-10-21T00:00:01Z', activity_result: { confirmed: true },
        operation_result: { status: 'confirmed', content_id: 'teaser_wand',
          activity_id: JSON.parse(options.body).activity_id },
        state_version: confirmed.state_version, binding_revision: confirmed.binding_revision,
        config_revision: 'config-1', snapshot_required: false, snapshot: confirmed,
      }) };
    }
    if (url.endsWith('/play-leases')) {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, ok: true, lease: {
        lease_id: 'eyJhbGciOiJIUzI1NiJ9.payload.signature~v1', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        activity_id: request.activity_id,
        content_id: 'teaser_wand', action_id: 'play_teaser_wand',
        source: 'pet-care-toy', issued_at: '2026-10-21T00:00:00Z',
        issued_state_version: 1, issued_binding_revision: 1,
        minimum_duration_seconds: 8,
        expires_at: '2026-10-21T00:00:30Z',
      } }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  h.window.NekoCatIdlePresentation = {
    requestCat1PlayTeaserWand(options) { terminals.push(options); return true; },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('play');
  const toyButton = detailsOf(h).children.find(child => child.dataset && child.dataset.contentId === 'teaser_wand');
  detailsOf(h).dispatchEvent({ type: 'click', target: toyButton, stopPropagation() {} });
  await waitFor(() => terminals.length === 1);
  assert.equal(calls.some(call => call.url.endsWith('/play-leases')), true);
  const issue = JSON.parse(calls.find(call => call.url.endsWith('/play-leases')).body);
  assert.equal(issue.expected_state_version, 1);
  assert.ok(issue.activity_id);
  assert.ok(issue.idempotency_key);
  assert.equal(calls.some(call => call.url.endsWith('/complete')), false);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity.lease_id,
    'eyJhbGciOiJIUzI1NiJ9.payload.signature~v1');
  assert.equal(terminals[0].minimumDurationMs, 8000);
  const validTerminal = {
    actionId: 'play_teaser_wand', source: terminals[0].source,
    activityId: terminals[0].activityId, requestId: terminals[0].requestId,
    runId: terminals[0].runId,
  };
  for (const [field, value] of [
    ['source', 'cat_mind'],
    ['actionId', 'cat1_play_yarn'],
    ['activityId', 'another-activity'],
    ['requestId', 'another-request'],
    ['runId', 'another-run'],
  ]) {
    terminals[0].onTerminal('done', { ...validTerminal, [field]: value });
  }
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(completionAttempts, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity.activity_id, issue.activity_id);
  invokePresentationTerminal(terminals[0], 'done', 'play-teaser-wand-action-finished');
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingWrite !== null);
  const retry = h.window.nekoPetCareEntry.getState().pendingWrite;
  assert.equal(retry.kind, 'play-lease-complete');
  assert.equal(retry.character_uid, 'local-role-a');
  const retryButton = footerOf(h).children.find(child =>
    child.className === 'neko-pet-care-retry-button');
  assert.equal(retryButton.disabled, false);
  retryButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  await waitFor(() => completionAttempts === 2);
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot.inventory.teaser_wand === 0);
  assert.equal(terminals.length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/play-leases')).length, 1);
  const complete = calls.find(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete'));
  const body = JSON.parse(complete.body);
  assert.equal(body.expected_state_version, 1);
  assert.equal(body.activity_id, issue.activity_id);
  assert.equal(body.terminal_status, 'done');
  assert.equal(Object.hasOwn(body, 'content_id'), false);
  assert.equal(Object.hasOwn(body, 'source'), false);
  assert.deepEqual(completionBodies[1], completionBodies[0]);
  const completionCalls = calls.filter(call => call.url.includes('/play-leases/') && call.url.endsWith('/complete'));
  assert.equal(completionCalls[1].headers['Idempotency-Key'], completionCalls[0].headers['Idempotency-Key']);
});

test('toy lease releases the Cat Mind conflict gate when its visual runner is unavailable', async () => {
  const calls = [];
  const catalog = { teaser_wand: {
    label: '逗猫棒', inventory_item: 'teaser_wand', consume_quantity: 1,
    inventory_quantity: 1, daily_count: 0, daily_limit: 3,
    feedback_action: 'play_teaser_wand', minimum_duration_seconds: 8,
  } };
  const initial = formalSnapshot({
    service_capabilities: { play_lease_complete: 1 },
    inventory: { teaser_wand: 1 }, play_actions: catalog,
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/play-leases')) {
      const request = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' }, ok: true, lease: {
          lease_id: 'lease-missing-runner', pet_id: 'pet-a', bound_role_id: 'server-role-a',
          activity_id: request.activity_id, content_id: 'teaser_wand',
          action_id: 'play_teaser_wand', source: 'pet-care-toy',
          issued_at: '2026-10-21T00:00:00Z', issued_state_version: 1,
          issued_binding_revision: 1, minimum_duration_seconds: 8,
          expires_at: '2026-10-21T00:00:30Z',
        },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('play');
  const toyButton = detailsOf(h).children.find(child =>
    child.dataset && child.dataset.contentId === 'teaser_wand');
  detailsOf(h).dispatchEvent({ type: 'click', target: toyButton, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().playError ===
    'play_feedback_unavailable');

  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(calls.filter(call => call.url.endsWith('/play-leases')).length, 1);
  assert.equal(calls.some(call => call.url.endsWith('/complete')), false);
  assert.equal(state.playPending, null);
  assert.equal(state.playActivity, null);
  assert.equal(state.optimisticSnapshot, null);
  assert.equal(h.window.nekoPetCareMode.getState().catMindSuppressed, false);
});

test('play without the lease capability fails closed without a fallback settlement path', async () => {
  const calls = [];
  const initial = stage4Snapshot({ activity_counters: { service_day: '2026-10-21', playing: {} } });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: 'local-role-a', name: '小猫 A' }, '/');
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, sessionId: 'formal-without-capability' },
  } }));
  await waitFor(() => h.window.nekoPetCareEntry.getState().playPending === null);
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.playActivity, null);
  assert.equal(state.playError, 'play_lease_required');
  assert.equal(calls.filter(call => call.method === 'POST' && !call.url.includes('/session/resume')).length, 0);
});

test('bind records the verified account and local alias before the request can become ambiguous', async () => {
  const validUid = 'a'.repeat(32);
  const snapshot = stage4Snapshot({
    bound_role_id: 'server-role-a',
  });
  let releaseBind;
  const bindGate = new Promise(resolve => { releaseBind = resolve; });
  let releaseOwner;
  const ownerGate = new Promise(resolve => { releaseOwner = resolve; });
  const remembered = [];
  let bindCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: validUid,
        binding_status: 'needs-binding', needs_binding: true,
      }) };
    }
    if (url === '/api/pet-care/bind' && options.method === 'POST') {
      bindCalls += 1;
      await bindGate;
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed' },
        account_id: 'account-a', current_character_uid: validUid,
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: validUid, name: '小猫 A' });
  h.window.NekoPetCareRoleAliasSync = {
    rememberOwner(characterUid, ownerAccount) {
      remembered.push({ characterUid, ownerAccount });
      return ownerGate;
    },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'needs-binding');
  const binding = h.window.nekoPetCareEntry.bindCurrentRole();
  await waitFor(() => remembered.length === 1);
  assert.deepEqual(remembered[0], { characterUid: validUid, ownerAccount: 'account-a' });
  assert.equal(bindCalls, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().bindingPending, true);
  releaseOwner();
  await waitFor(() => bindCalls === 1);
  releaseBind();
  await binding;
  assert.equal(h.window.nekoPetCareEntry.getState().bindingStatus, 'bound');
});

test('bind keeps its durable identity and does not send when owner-ledger persistence fails', async () => {
  const validUid = 'a'.repeat(32);
  const storage = {
    localStorageValues: new Map(), sessionStorageValues: new Map(),
    lockManager: new FakeLockManager(),
  };
  let bindCalls = 0;
  let ownerPersisted = false;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: validUid,
        binding_status: 'needs-binding', needs_binding: true,
      }) };
    }
    if (url === '/api/pet-care/bind' && options.method === 'POST') {
      bindCalls += 1;
      assert.equal(ownerPersisted, true);
      return { ok: false, status: 503,
        json: async () => ({ detail: 'service_unavailable' }) };
    }
    throw new Error('unexpected request: ' + url);
  }, { uid: validUid, name: '小猫 A' }, '/', storage);
  h.window.NekoPetCareRoleAliasSync = {
    rememberOwner() { return Promise.reject(new Error('owner-ledger-write-failed')); },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().bindingStatus === 'needs-binding');
  await h.window.nekoPetCareEntry.bindCurrentRole();

  assert.equal(bindCalls, 0);
  const durable = pendingWriteRecord(storage);
  assert.equal(durable.kind, 'bind-create');
  assert.equal(durable.body.character_uid, validUid);
  assert.equal(h.window.nekoPetCareEntry.getState().pendingWriteError, 'owner-ledger-write-failed');

  h.window.NekoPetCareRoleAliasSync = {
    rememberOwner() { ownerPersisted = true; return Promise.resolve(); },
  };
  await h.window.nekoPetCareEntry.retryPendingWrite();
  assert.equal(bindCalls, 1);
  const retried = pendingWriteRecord(storage);
  assert.equal(retried.idempotency_key, durable.idempotency_key);
  assert.deepEqual(retried.body, durable.body);
});

test('exploration status refresh accepts the narrow service DTO and reveals only the same active plan', async () => {
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-letters-1', location_id: 'garden', status: 'active', active: true,
    started_at: '2026-10-21T00:00:00Z', ends_at: '2026-10-21T01:00:00Z',
    letters_revealed: [], letters_available: 0,
  });
  const revealed = canonicalExplorationFixture({
    ...active,
    letters_revealed: [{ letter_id: 'letter-1', title: '树下的纸条', body: '今天风很轻。' }],
    letters_available: 1,
  });
  const snapshot = stage4Snapshot({ exploration: active });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-letters-1')) {
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', exploration: revealed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await h.window.nekoPetCareEntry.refreshExploration();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.explorationError, '');
  assert.equal(state.exploration.exploration_id, 'explore-letters-1');
  assert.equal(state.exploration.letters_available, 1);
  assert.equal(state.exploration.letters_revealed[0].id, 'letter-1');
  assert.equal(state.snapshot.state_version, snapshot.state_version);
  enterCatForm(h);
  await openPanel(h);
  h.window.nekoPetCareEntry.setActionCategory('exploration');
  assert.equal(navButtonOf(h, 'letters').hidden, true,
    'an arrived exploration letter must not unlock the persistent mailbox');
  const arrived = taskRowDescendant(detailsOf(h), 'neko-pet-care-exploration-letters');
  assert.ok(arrived);
  assert.equal(arrived.children[0].textContent, '新来信');
  assert.equal(arrived.children[1].children[0].children[0].textContent, '来信');
  assert.equal(arrived.children[1].children[0].children[1].textContent, '今天风很轻。');
});

test('a service next_letter_at hint schedules exactly one exploration status read', async () => {
  const serverTime = Date.now();
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-letter-hint-1', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 1000).toISOString(),
    ends_at: new Date(serverTime + 2000).toISOString(),
    next_letter_at: new Date(serverTime + 70).toISOString(),
    letters_revealed: [], letters_available: 0,
  });
  const revealed = canonicalExplorationFixture({
    ...active,
    letters_revealed: [{ letter_id: 'letter-1', title: '窗边来信', body: '今天看见了云。' }],
    letters_available: 1,
  });
  delete revealed.next_letter_at;
  const snapshot = stage4Snapshot({
    exploration: active,
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 300000).toISOString(),
    }),
  });
  let statusReads = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-letter-hint-1')) {
      statusReads += 1;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', server_time: new Date(serverTime + 100).toISOString(),
        exploration: revealed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await waitFor(() => statusReads === 1, 500);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.letters_available, 1);
  await new Promise(resolve => setTimeout(resolve, 120));
  assert.equal(statusReads, 1);
});

test('the same letter hint cannot be rearmed by repeated visibility changes', async () => {
  const serverTime = Date.now();
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-letter-same-hint-1', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 1000).toISOString(),
    ends_at: new Date(serverTime + 2000).toISOString(),
    next_letter_at: new Date(serverTime + 60).toISOString(),
    letters_revealed: [], letters_available: 0,
  });
  const snapshot = stage4Snapshot({
    exploration: active,
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 300000).toISOString(),
    }),
  });
  let statusReads = 0;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-letter-same-hint-1')) {
      statusReads += 1;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', server_time: new Date(serverTime + 60).toISOString(), exploration: active,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await waitFor(() => statusReads === 1, 500);
  for (let index = 0; index < 3; index += 1) {
    h.document.visibilityState = 'hidden';
    h.document.dispatchEvent({ type: 'visibilitychange' });
    h.document.visibilityState = 'visible';
    h.document.dispatchEvent({ type: 'visibilitychange' });
    h.window.dispatchEvent({ type: 'pageshow' });
  }
  await new Promise(resolve => setTimeout(resolve, 120));
  assert.equal(statusReads, 1);
});

test('letter refresh pauses while hidden and visible plus pageshow coalesce to one catch-up read', async () => {
  const serverTime = Date.now();
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-letter-resume-1', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 1000).toISOString(),
    ends_at: new Date(serverTime + 2000).toISOString(),
    next_letter_at: new Date(serverTime + 90).toISOString(),
    letters_revealed: [], letters_available: 0,
  });
  const revealed = canonicalExplorationFixture({ ...active, letters_revealed: [
    { letter_id: 'letter-1', title: '树下的纸条', body: '今天风很轻。' },
  ], letters_available: 1 });
  delete revealed.next_letter_at;
  const snapshot = stage4Snapshot({
    exploration: active,
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 300000).toISOString(),
    }),
  });
  let statusReads = 0;
  let releaseStatus = null;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-letter-resume-1')) {
      statusReads += 1;
      return new Promise(resolve => {
        releaseStatus = () => resolve({ ok: true, status: 200, json: async () => ({
          binding_status: 'bound', server_time: new Date(serverTime + 150).toISOString(),
          exploration: revealed,
        }) });
      });
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  h.document.visibilityState = 'hidden';
  h.document.dispatchEvent({ type: 'visibilitychange' });
  await new Promise(resolve => setTimeout(resolve, 130));
  assert.equal(statusReads, 0);

  h.document.visibilityState = 'visible';
  h.document.dispatchEvent({ type: 'visibilitychange' });
  h.window.dispatchEvent({ type: 'pageshow' });
  await waitFor(() => statusReads === 1, 500);
  h.window.dispatchEvent({ type: 'pagehide' });
  h.window.dispatchEvent({ type: 'pageshow' });
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(statusReads, 1);
  releaseStatus();
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration.letters_available === 1);
});

test('a terminal exploration snapshot invalidates a pending letter status response', async () => {
  const serverTime = Date.now();
  const active = canonicalExplorationFixture({
    exploration_id: 'explore-letter-terminal-1', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 1000).toISOString(),
    ends_at: new Date(serverTime + 2000).toISOString(),
    next_letter_at: new Date(serverTime + 60).toISOString(),
    letters_revealed: [], letters_available: 0,
  });
  const projection = clientProjection({
    effective_at: new Date(serverTime - 60000).toISOString(),
    server_time: new Date(serverTime).toISOString(),
    cat_mind_valid_until: new Date(serverTime + 300000).toISOString(),
  });
  const snapshot = stage4Snapshot({ exploration: active, client_projection: projection });
  const terminal = stage4Snapshot({
    state_version: 2, client_projection: projection,
    exploration: { ...active, status: 'completed', active: false },
  });
  delete terminal.exploration.next_letter_at;
  let statusReads = 0;
  let releaseStatus = null;
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-letter-terminal-1')) {
      statusReads += 1;
      return new Promise(resolve => {
        releaseStatus = () => resolve({ ok: true, status: 200, json: async () => ({
          binding_status: 'bound', server_time: new Date(serverTime + 80).toISOString(),
          exploration: canonicalExplorationFixture({ ...active, letters_revealed: [
            { letter_id: 'late-letter', title: '迟到来信', body: '不应覆盖终态。' },
          ], letters_available: 1 }),
        }) });
      });
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await waitFor(() => statusReads === 1, 500);
  const generation = h.window.nekoPetCareEntry.getState().generation;
  h.window.nekoPetCareEntry.setBindingResult({
    generation, account_id: 'account-a', current_character_uid: 'local-role-a',
    binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: terminal,
  });
  releaseStatus();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(statusReads, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.letters_available, 1);
});

test('exploration completion uses the service-time anchor when the device wall clock is ahead', async () => {
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const dueAt = serverTime + 650;
  const active = {
    exploration_id: 'explore-server-clock', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 60000).toISOString(),
    ends_at: new Date(dueAt).toISOString(),
  };
  const initial = stage4Snapshot({
    exploration: active,
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60000).toISOString(),
      server_time: new Date(serverTime).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 60000).toISOString(),
    }),
  });
  const terminal = stage4Snapshot({
    state_version: 2,
    exploration: { ...active, status: 'completed', active: false },
    client_projection: clientProjection({
      effective_at: new Date(serverTime - 60000).toISOString(),
      server_time: new Date(dueAt + 10).toISOString(),
      cat_mind_valid_until: new Date(serverTime + 60000).toISOString(),
    }),
  });
  let completeCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-server-clock/complete') && options.method === 'POST') {
      completeCalls += 1;
      return { ok: true, status: 200, json: async () => ({
        server_time: new Date(dueAt + 10).toISOString(),
        operation_result: { status: 'confirmed', exploration_status: 'completed',
          exploration_id: 'explore-server-clock', drops: [], progress_delta: 0,
          completion_ratio: 1 },
        state_version: 2, binding_revision: 1, config_revision: 'candidate-1',
        snapshot_required: false, snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 350));
  assert.equal(completeCalls, 0);
  await waitFor(() => completeCalls === 1, 1000);
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration.active === false);
});

test('a known early exploration rejection gets a fresh key after the server deadline', async () => {
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const dueAt = serverTime + 60000;
  const active = {
    exploration_id: 'explore-early-retry', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 60000).toISOString(), ends_at: new Date(dueAt).toISOString(),
  };
  const projectionAt = now => clientProjection({
    effective_at: new Date(serverTime - 60000).toISOString(),
    server_time: new Date(now).toISOString(),
    cat_mind_valid_until: new Date(serverTime + 120000).toISOString(),
  });
  const initial = stage4Snapshot({ exploration: active, client_projection: projectionAt(serverTime) });
  const refreshed = stage4Snapshot({ exploration: active,
    client_projection: projectionAt(dueAt - 100) });
  const terminal = stage4Snapshot({ state_version: 2,
    exploration: { ...active, status: 'completed', active: false },
    client_projection: projectionAt(dueAt + 100) });
  let snapshotReads = 0;
  const keys = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-early-retry/complete') && options.method === 'POST') {
      keys.push(options.headers['Idempotency-Key']);
      if (keys.length === 1) {
        return { ok: true, status: 200, json: async () => ({
          server_time: new Date(dueAt - 100).toISOString(), snapshot_required: true,
          operation_result: { status: 'rejected', code: 'exploration_not_complete' },
        }) };
      }
      return { ok: true, status: 200, json: async () => ({
        server_time: new Date(dueAt + 100).toISOString(), snapshot_required: false,
        operation_result: { status: 'confirmed', exploration_status: 'completed',
          exploration_id: 'explore-early-retry', drops: [], progress_delta: 0,
          completion_ratio: 1 },
        state_version: 2, binding_revision: 1, config_revision: 'candidate-1', snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      snapshotReads += 1;
      const snapshot = snapshotReads === 1 ? initial : refreshed;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await h.window.nekoPetCareEntry.completeExploration();
  assert.equal(h.window.nekoPetCareEntry.getState().explorationError, 'exploration_not_complete');
  await new Promise(resolve => setTimeout(resolve, 150));
  await h.window.nekoPetCareEntry.completeExploration();
  assert.equal(keys.length, 2);
  assert.notEqual(keys[0], keys[1]);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);
});

test('a parseable HTTP 409 exploration rejection stops automatic retries and keeps manual recovery', async () => {
  const serverTime = Date.parse('2026-10-21T02:00:00Z');
  const active = {
    exploration_id: 'explore-stable-rejection', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(serverTime - 120000).toISOString(),
    ends_at: new Date(serverTime - 1000).toISOString(),
  };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ state_version: 2,
    exploration: { ...active, status: 'completed', active: false },
  });
  let completeCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-stable-rejection/complete') && options.method === 'POST') {
      completeCalls += 1;
      if (completeCalls === 1) {
        return { ok: false, status: 409, json: async () => ({
          server_time: new Date(serverTime).toISOString(), snapshot_required: false,
          operation_result: { status: 'rejected', code: 'exploration_requirement_failed' },
          state_version: 1, binding_revision: 1, config_revision: 'candidate-1',
          snapshot: initial,
        }) };
      }
      return { ok: true, status: 200, json: async () => ({
        server_time: new Date(serverTime + 1000).toISOString(), snapshot_required: false,
        operation_result: { status: 'confirmed', exploration_status: 'completed',
          exploration_id: 'explore-stable-rejection', drops: [], progress_delta: 0,
          completion_ratio: 1 },
        state_version: 2, binding_revision: 1, config_revision: 'candidate-1',
        snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await waitFor(() => completeCalls === 1, 1000);
  await new Promise(resolve => setTimeout(resolve, 700));
  assert.equal(completeCalls, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationAutoCompleteBlockedId,
    'explore-stable-rejection');

  await h.window.nekoPetCareEntry.completeExploration();
  assert.equal(completeCalls, 2);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);
  assert.equal(h.window.nekoPetCareEntry.getState().explorationAutoCompleteBlockedId, '');
});

test('an unknown exploration outcome replays its exact durable key', async () => {
  const active = {
    exploration_id: 'explore-unknown-retry', location_id: 'garden', status: 'active', active: true,
    started_at: '2026-10-21T00:00:00Z', ends_at: '2026-10-21T01:00:00Z',
  };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ state_version: 2,
    exploration: { ...active, status: 'completed', active: false } });
  const keys = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200,
        json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-unknown-retry/complete') && options.method === 'POST') {
      keys.push(options.headers['Idempotency-Key']);
      if (keys.length === 1) {
        return { ok: false, status: 503, json: async () => ({ detail: 'service_unavailable' }) };
      }
      return { ok: true, status: 200, json: async () => ({
        operation_result: { status: 'confirmed', exploration_status: 'completed',
          exploration_id: 'explore-unknown-retry', drops: [], progress_delta: 0,
          completion_ratio: 1 },
        state_version: 2, binding_revision: 1, config_revision: 'candidate-1',
        snapshot_required: false, snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await h.window.nekoPetCareEntry.completeExploration();
  assert.ok(h.window.nekoPetCareEntry.getState().pendingWrite);
  await h.window.nekoPetCareEntry.retryPendingWrite();
  assert.deepEqual(keys, [keys[0], keys[0]]);
  assert.equal(h.window.nekoPetCareEntry.getState().exploration.active, false);
});

test('exploration terminal notice consumes the real operation_result drops and progress shape', async () => {
  const active = {
    exploration_id: 'explore-real-dto-1', location_id: 'garden', status: 'active', active: true,
    started_at: new Date(Date.now() - 120000).toISOString(),
    ends_at: new Date(Date.now() - 60000).toISOString(),
  };
  const terminalExploration = {
    ...active, status: 'completed', active: false,
    drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 4,
  };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ state_version: 2, exploration: terminalExploration,
    inventory: { teaser_wand: 1 },
    status_values: { ...initial.status_values, progress: initial.status_values.progress + 4 },
  });
  const notices = [];
  const modalNotices = [];
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-real-dto-1/complete') && options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({
        activity_result: { confirmed: true },
        operation_result: {
          status: 'confirmed', exploration_status: 'completed',
          exploration_id: 'explore-real-dto-1',
          drops: [{ item_id: 'teaser_wand', quantity: 1 }], progress_delta: 4,
          completion_ratio: 1,
        },
        state_version: 2, binding_revision: 1, config_revision: 'config-1',
        snapshot_required: false, snapshot: terminal,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.showStatusToast = (message, duration, options) => {
    notices.push({ message, duration, options });
    return true;
  };
  h.window.showAlertDialog = (message, options) => modalNotices.push({ message, options });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await h.window.nekoPetCareEntry.completeExploration();
  await waitFor(() => notices.length === 1);
  assert.equal(notices[0].message, '探索已完成，收获：逗猫棒 × 1、养成进度 +4');
  assert.equal(modalNotices.length, 0);
  assert.doesNotMatch(notices[0].message, /服务|teaser_wand/);
  assert.match(notices[0].message, /养成进度 \+4/);
  assert.deepEqual(h.window.nekoPetCareEntry.getState().explorationReward.drops,
    [{ item_id: 'teaser_wand', quantity: 1 }]);
});

test('exploration terminal notice maps legacy and directory items without exposing machine ids', async () => {
  for (const [suffix, reward, expected, directory] of [
    ['known', 'teaser_wand', '探索已完成，收获：逗猫棒', null],
    ['unknown', 'future_reward_item', '探索已完成，收获：道具', null],
    ['directory', 'future_reward_item', '探索已完成，收获：星光零食', {
      future_reward_item: {
        item_id: 'future_reward_item', display_name: '星光零食', category: 'food',
        asset_url: '/static/assets/neko-idle/pet-care-tool/eat/starlight-snack.png',
        acquisition_sources: ['exploration:garden'],
      },
    }],
  ]) {
    const explorationId = `explore-legacy-reward-${suffix}`;
    const active = {
      exploration_id: explorationId, location_id: 'garden', status: 'active', active: true,
      started_at: new Date(Date.now() - 120000).toISOString(),
      ends_at: new Date(Date.now() - 60000).toISOString(),
    };
    const terminalExploration = { ...active, status: 'completed', active: false };
    const directoryFields = directory ? {
      service_capabilities: { item_directory: 1 }, item_directory: directory,
    } : {};
    const initial = stage4Snapshot({ exploration: active, ...directoryFields });
    const terminal = stage4Snapshot({
      state_version: 2, exploration: terminalExploration, ...directoryFields,
    });
    const notices = [];
    const modalNotices = [];
    const h = createHarness(async (url, options = {}) => {
      if (url.includes('/api/card-drop/oauth/status')) {
        return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
      }
      if (url.endsWith(`/explorations/${explorationId}/complete`) && options.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({
          operation_result: {
            status: 'confirmed', exploration_status: 'completed',
            exploration_id: explorationId, reward,
          },
          state_version: 2, binding_revision: 1, config_revision: 'config-1',
          snapshot_required: false, snapshot: terminal,
        }) };
      }
      if (url.includes('/api/pet-care/pets/')) {
        return { ok: true, status: 200, json: async () => ({
          account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
          pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        }) };
      }
      throw new Error('unexpected request: ' + url);
    });
    h.window.showStatusToast = (message, duration, options) => {
      notices.push({ message, duration, options });
      return true;
    };
    h.window.showAlertDialog = (message, options) => modalNotices.push({ message, options });
    await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
    await h.window.nekoPetCareEntry.completeExploration();
    await waitFor(() => notices.length === 1);
    assert.equal(notices[0].message, expected);
    assert.equal(modalNotices.length, 0);
    assert.doesNotMatch(notices[0].message, /teaser_wand|future_reward_item|服务/);
  }
});

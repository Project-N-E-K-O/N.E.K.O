const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care.js'), 'utf8');

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

function createHarness(fetchImpl, identityValue = { uid: 'local-role-a', name: '小猫 A' }, pathname = '/', storage = null) {
  const window = new EventTargetLike();
  window.location = { pathname };
  window.innerWidth = 1280;
  window.innerHeight = 800;
  window.setTimeout = setTimeout;
  window.clearTimeout = clearTimeout;
  // The production refresh loop is covered by explicit refresh calls here;
  // avoid keeping the test process alive for its real 30-second interval.
  window.setInterval = () => 0;
  window.clearInterval = () => {};
  window.getComputedStyle = () => ({ display: 'block', visibility: 'visible', opacity: '1' });
  window.open = () => ({ closed: false });
  const localStorageValues = storage && storage.localStorageValues || new Map();
  window.localStorage = {
    getItem(key) { return localStorageValues.has(key) ? localStorageValues.get(key) : null; },
    setItem(key, value) { localStorageValues.set(key, String(value)); },
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
    state: { generation: 0, mode: 'session-invalid', catMindSuppressed: false, catLocalActive: false },
    setMode(mode, detail = {}) {
      this.state.mode = mode;
      this.state.reason = detail.reason || '';
      return this.state;
    },
    setCatMindCompatibility(detail = {}) {
      this.state.catMindSuppressed = detail.suppressed === true;
      this.state.blockedCatMindActionIds = detail.actionIds || null;
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
  const fetcher = fetchImpl || (async () => ({ ok: true, json: async () => ({ logged_in: false, local_user_id: null }) }));
  const context = vm.createContext({ window, document, CustomEvent: CustomEventLike,
    console, fetch: fetcher });
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

function enterCatForm(h) {
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-local-active-change', {
    detail: { active: true, reason: 'cat-form-entered' },
  }));
}

test('stage 1 creates an independent entry surface without model floating buttons', async () => {
  const h = createHarness();
  await new Promise(resolve => setTimeout(resolve, 0));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.ok(entry);
  assert.equal(h.window.nekoPetCareEntry.getState().authStatus, 'logged-out');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'session-invalid');
  assert.equal(entry.dataset.status, 'logged-out');
  assert.equal(entry.dataset.visible, 'false');
  assert.equal(entry.dataset.available, 'false');
  assert.equal(h.body.children.some(child => /floating-buttons/.test(child.id)), false);
  assert.equal(entry.children[1].children[1].textContent, '登录 N.E.K.O 账号后查看账号宠物。');
  assert.equal(entry.children[1].children[8].children[0].textContent, '登录 N.E.K.O 账号');
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

test('stage 1 only floats beside the visible cat anchor after the hover delay', async () => {
  const h = createHarness();
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
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  assert.equal(entry.dataset.available, 'true');
  assert.equal(entry.dataset.panelSide, 'right');
  assert.equal(entry.style.left, '312px');
});

test('stage 1 keeps hover as a launcher and opens the panel only after click', async () => {
  const h = createHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const trigger = entry.children[0];
  assert.equal(entry.dataset.visible, 'true');
  assert.equal(entry.dataset.expanded, 'false');
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'true');
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'false');
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
  h.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.equal(entry.dataset.expanded, 'false');
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
  h.document.dispatchEvent({ type: 'click', target: h.document.body });
  assert.equal(entry.dataset.expanded, 'false');
});

test('stage 2 exposes an obvious panel close button and keeps operation labels at category level', async () => {
  const h = createHarness();
  h.window.nekoCatInteractionAnchor = {
    getState: () => ({ visible: true, busy: false,
      rect: { left: 100, top: 100, right: 300, bottom: 300, width: 200, height: 200 } }),
  };
  enterCatForm(h);
  await new Promise(resolve => setTimeout(resolve, 0));
  h.document.dispatchEvent({ type: 'pointermove', clientX: 200, clientY: 200, pointerType: 'mouse' });
  await new Promise(resolve => setTimeout(resolve, 430));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const panel = entry.children[1];
  const close = panel.children[0].children[2];
  assert.equal(close.textContent, '×');
  assert.equal(close.getAttribute('aria-label'), '关闭登录');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'true');
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'false');
  assert.deepEqual(panel.children[3].children.map(button => button.textContent),
    ['喂食', '清洁', '陪玩', '探索']);
  assert.equal(panel.children[4].className, 'neko-pet-care-action-details');
  assert.equal(panel.children[8].className, 'neko-pet-care-panel-footer');
});

test('login close button cancels the OAuth popup and polling', async () => {
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
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const close = entry.children[1].children[0].children[2];
  const loginPromise = h.window.nekoPetCareEntry.startLogin();
  await waitFor(() => h.window.nekoPetCareEntry.getState().oauthPending);
  await waitFor(() => openedPopup !== null);
  close.dispatchEvent({ type: 'click', stopPropagation() {} });
  await loginPromise;
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.oauthPending, false);
  assert.equal(state.reason, 'oauth-cancelled-by-user');
  assert.equal(openedPopup.closed, true);
});

test('stage 1 moves the panel to the cat\'s left when the right side cannot fit it', async () => {
  const h = createHarness();
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
  assert.equal(entry.dataset.panelSide, 'left');
  assert.equal(entry.style.left, '454px');
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

test('stage 1 resolves first binding through the service instead of waiting for manual injection', async () => {
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'needs-binding' }) };
    }
    if (url.includes('/api/pet-care/bind')) {
      return { ok: true, json: async () => ({ account_id: 'account-a',
        current_character_uid: 'local-role-a', binding_status: 'bound', bound_role_id: 'server-role-a' }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(h.window.nekoPetCareEntry.getState().bindingStatus, 'needs-binding');
  await h.window.nekoPetCareEntry.bindCurrentRole();
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.bindingRoleId, 'server-role-a');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.ok(calls.some(item => item.url.includes('/api/pet-care/context')));
  assert.ok(calls.some(item => item.url.includes('/api/pet-care/bind') && item.method === 'POST'));
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
        current_character_uid: 'local-role-a', binding_status: 'bound', bound_role_id: 'server-role-a' }) };
    }
    return { ok: true, json: async () => ({}) };
  });
  const notices = [];
  h.window.showAlert = (message, title) => { notices.push({ message, title }); return Promise.resolve(true); };
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const trigger = entry.children[0];
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
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
  assert.equal(entry.children[1].children[6].hidden, true);
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
      active: true, status: 'away', exploration_id: 'explore-reload',
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
        snapshot: { exploration: { active: true, status: 'away', exploration_id: 'explore-reload' } } }) };
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
  assert.equal(state.bindingStatus, 'unbound');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.equal(entry.children[0].textContent, '养宠');
  assert.equal(entry.children[1].children[6].hidden, true);
  assert.equal(entry.children[1].children[3].children.every(button => button.disabled), true);
});

test('stage 1 service failure exposes retry and keeps ordinary-cat mode', async () => {
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/context')) {
      return { ok: false, json: async () => ({ detail: 'service_unavailable' }) };
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) };
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.bindingStatus, 'unavailable');
  assert.equal(state.serviceStatus, 'unavailable');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
  assert.equal(entry.children[1].children[8].children[2].hidden, false);
});

test('cat-form exit hides the entry immediately and restores it on re-entry', async () => {
  const h = createHarness();
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
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
        snapshot: {
          schema_version: 1, pet_id: 'pet-a', account_id: 'account-a',
          bound_role_id: 'server-role-a', last_settled_at: '2026-10-16T00:00:00+00:00',
          status_values: { satiety: 70, energy: 70, cleanliness: 70, mood: 70, progress: 0 },
          negative_status: {}, inventory: {}, tasks: {}, unlocks: [], exploration: null,
        },
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
  const footer = entry.children[1].children[8];
  assert.equal(footer.children[0].hidden, false);
  assert.equal(footer.children[2].hidden, false);
});

test('saved OAuth session uncertainty keeps account identity but fails pet care closed', async () => {
  let cloudUnavailable = false;
  const snapshot = {
    schema_version: 1, pet_id: 'pet-a', account_id: 'account-a',
    bound_role_id: 'server-role-a', last_settled_at: '2026-10-16T00:00:00+00:00',
    status_values: { satiety: 70, energy: 70, cleanliness: 80, mood: 65, progress: 4 },
    negative_status: {}, inventory: {}, tasks: {}, unlocks: [], exploration: null,
  };
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
        snapshot: {
          schema_version: 1, pet_id: 'pet-a', account_id: 'account-a',
          bound_role_id: 'server-role-a', last_settled_at: '2026-10-16T00:00:00+00:00',
          status_values: { satiety: 70, energy: 70, cleanliness: 70, mood: 70, progress: 0 },
          negative_status: {}, inventory: {}, tasks: {}, unlocks: [], exploration: null,
        },
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
        current_character_uid: 'local-role-a', binding_status: 'bound', bound_role_id: 'server-role-a' }) };
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
  assert.equal(entry.children[0].textContent, '养宠 · 已绑定');
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-unavailable');
});

test('stage 2 reads the canonical pet snapshot and keeps all operations read-only', async () => {
  const calls = [];
  const snapshot = {
    schema_version: 1,
    pet_id: 'pet-a', account_id: 'account-a', bound_role_id: 'server-role-a',
    last_settled_at: '2026-10-16T00:00:00+00:00',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: {}, tasks: {}, unlocks: [], exploration: null,
  };
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
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  const state = h.window.nekoPetCareEntry.getState();
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const panel = entry.children[1];
  assert.equal(state.bindingStatus, 'bound');
  assert.equal(state.snapshot.status_values.satiety, 72);
  assert.ok(calls.some(item => item.url.includes('/api/pet-care/pets/local-role-a')));
  assert.equal(calls.some(item => item.url.includes('/api/pet-care/context')), false);
  assert.equal(panel.children[2].children[0].children[1].textContent, '72');
  assert.equal(panel.children[2].children[4].children[1].textContent, '13');
  assert.equal(panel.children[3].children[0].disabled, true);
  assert.equal(panel.children[3].children[1].disabled, true);
  assert.equal(panel.children[3].children[2].disabled, true);
  assert.equal(panel.children[3].children[3].disabled, true);
  assert.equal(h.window.nekoPetCareMode.state.mode, 'pet-available');
  assert.equal(h.window.nekoPetCareMode.state.catMindSuppressed, false);
});

test('stage 2 does not poll or re-read the pet snapshot when the account is unchanged', async () => {
  const calls = [];
  const snapshot = {
    schema_version: 1,
    pet_id: 'pet-a', account_id: 'account-a', bound_role_id: 'server-role-a',
    last_settled_at: '2026-10-16T00:00:00+00:00',
    status_values: { satiety: 70, energy: 60, cleanliness: 80, mood: 65, progress: 4 },
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: {}, tasks: {}, unlocks: [], exploration: null,
  };
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
  assert.equal(entry.children[1].children[2].children[0].children[1].textContent, '—');
  assert.equal(entry.children[1].children[6].hidden, true);
  assert.equal(entry.children[1].children[3].children.every(button => button.disabled), true);
});

test('stage 3 keeps the Cat Mind gate out of the stage 2 snapshot path until cat form is active', () => {
  assert.match(source, /setCatMindCompatibility/);
  assert.doesNotMatch(source, /shouldBlockCatMindActionRequest/);
});

test('stage 3 enables the six-action Cat Mind gate only after the full context and cat form are ready', async () => {
  const snapshot = {
    schema_version: 1,
    pet_id: 'pet-a', account_id: 'account-a', bound_role_id: 'server-role-a',
    last_settled_at: '2026-10-16T00:00:00+00:00',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: {}, tasks: {}, unlocks: [], exploration: null,
  };
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
  assert.equal(ready.catMindSuppressed, true);
  assert.deepEqual(Array.from(ready.blockedCatMindActionIds), [
    'cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move',
    'cat1_play_yarn', 'cat2_nap_feedback', 'cat3_sleep_feedback',
  ]);
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
  assert.match(source, /api\/pet-care\/context/);
  assert.match(source, /api\/pet-care\/pets\//);
  assert.match(source, /api\/pet-care\/bind/);
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
  }
});

function stage4Snapshot(overrides = {}) {
  return {
    schema_version: 1,
    pet_id: 'pet-a', account_id: 'account-a', bound_role_id: 'server-role-a',
    last_settled_at: '2026-10-16T00:00:00+00:00',
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: { small_fish: 2 }, tasks: {}, unlocks: [], exploration: null,
    ...overrides,
  };
}

test('stage 4 uses service settlement rules for read-only elapsed display', async () => {
  const snapshot = stage4Snapshot({
    last_settled_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    status_values: { satiety: 72, energy: 61, cleanliness: 88, mood: 67, progress: 13 },
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        settlement: { satiety_decay_per_hour: 1, cleanliness_decay_per_hour: 1, energy_recovery_per_hour: 4 },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const stats = entry.children[1].children[2];
  assert.equal(stats.children[0].children[1].textContent, '70');
  assert.equal(stats.children[1].children[1].textContent, '69');
  assert.equal(stats.children[2].children[1].textContent, '86');
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety, 72);
});

test('development fill-status button stays hidden without service capability and applies only confirmed values', async () => {
  const initial = stage4Snapshot({
    status_values: { satiety: 12, energy: 23, cleanliness: 34, mood: 45, progress: 56 },
  });
  const confirmed = stage4Snapshot({
    status_values: { satiety: 100, energy: 100, cleanliness: 100, mood: 45, progress: 56 },
  });
  let devEnabled = false;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/debug/fill-status') && options.method === 'POST') {
      assert.equal(devEnabled, true);
      const body = JSON.parse(options.body);
      assert.ok(body.idempotency_key);
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a',
        snapshot: confirmed, dev_tools: { fill_status: true },
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
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const devTools = entry.children[1].children.find(child => child.className === 'neko-pet-care-dev-tools');
  assert.equal(devTools.hidden, true);

  // A server capability response is what makes the developer-only control
  // appear; the browser cannot enable it by changing the status values.
  devEnabled = true;
  h.window.nekoPetCareEntry.setBindingResult({
    account_id: 'account-a', current_character_uid: 'local-role-a',
    binding_status: 'bound', bound_role_id: 'server-role-a', snapshot: initial,
    dev_tools: { fill_status: true }, generation: h.window.nekoPetCareEntry.getState().generation,
  });
  assert.equal(devTools.hidden, false);
  const devButton = devTools.children[1];
  devButton.dispatchEvent({ type: 'click', stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety === 100);
  const finalValues = h.window.nekoPetCareEntry.getState().snapshot.status_values;
  assert.deepEqual(finalValues, { satiety: 100, energy: 100, cleanliness: 100, mood: 45, progress: 56 });
});

test('stage 4 keeps category and concrete content separate and replaces the snapshot after confirmation', async () => {
  const calls = [];
  const initial = stage4Snapshot();
  let releaseAction;
  const actionGate = new Promise(resolve => { releaseAction = resolve; });
  const confirmed = stage4Snapshot({
    status_values: { ...initial.status_values, satiety: 97 },
    inventory: { small_fish: 1 },
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
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        operation: { status: 'confirmed', category: 'feeding', content_id: 'small_fish', feedback_action: 'eat_snack' },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        care_actions: { feeding: { small_fish: {
          inventory_item: 'small_fish', satiety_delta: 25, progress_delta: 0,
          feedback_action: 'eat_snack',
        } } },
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  entry.children[1].children[3].children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = entry.children[1].children[4];
  assert.equal(details.children[0].textContent, '喂食 · 选择具体操作');
  const detailsHeightStable = details.children.length;
  details.dispatchEvent({ type: 'click', target: details.children[1], stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction !== null);
  const optimistic = h.window.nekoPetCareEntry.getState();
  assert.equal(optimistic.snapshot.status_values.satiety, 72);
  assert.equal(optimistic.optimisticSnapshot.status_values.satiety, 97);
  assert.equal(optimistic.optimisticSnapshot.status_values.progress, 13);
  assert.equal(optimistic.optimisticSnapshot.inventory.small_fish, 2);
  assert.equal(details.children.length, detailsHeightStable);
  assert.equal(details.children[1].disabled, true);
  assert.equal(wakes.length, 1);
  assert.equal(feedback.length, 1);
  releaseAction();
  await waitFor(() => h.window.nekoPetCareEntry.getState().pendingAction === null &&
    h.window.nekoPetCareEntry.getState().snapshot.status_values.satiety === 97);
  assert.equal(wakes.length, 1);
  assert.equal(wakes[0].category, 'feeding');
  assert.equal(feedback.length, 1);
  assert.equal(feedback[0].started, true);
  assert.equal(feedback[0].optimistic, true);
  assert.equal(calls.filter(item => item.method === 'POST').length, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish, 1);
});

test('stage 4 renders additional food operations from the confirmed service catalog', async () => {
  const snapshot = stage4Snapshot({ inventory: { small_fish: 1, tuna: 2 } });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot,
        care_actions: {
          feeding: {
            small_fish: { label: '小鱼干', inventory_item: 'small_fish', satiety_delta: 25, progress_delta: 0 },
            tuna: { label: '金枪鱼', inventory_item: 'tuna', satiety_delta: 10, progress_delta: 0 },
          },
          cleaning: { brushing: { label: '梳毛', cleanliness_delta: 30, progress_delta: 4 } },
        },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const feeding = entry.children[1].children[3].children[0];
  feeding.dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = entry.children[1].children[4];
  assert.equal(details.children.length, 3);
  assert.equal(details.children[1].textContent, '小鱼干');
  assert.equal(details.children[1].children[0].textContent, '库存 × 1');
  assert.equal(details.children[2].textContent, '金枪鱼');
  assert.equal(details.children[2].children[0].textContent, '库存 × 2');
});

test('stage 4 keeps brushing visible and gates a confirmed cleaning cooldown locally', async () => {
  const initial = stage4Snapshot({ cleaning_cooldowns: {} });
  const confirmed = stage4Snapshot({
    status_values: { ...initial.status_values, cleanliness: 100 },
    cleaning_cooldowns: { brushing: new Date().toISOString() },
  });
  let postCalls = 0;
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      postCalls += 1;
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a',
        binding_status: 'bound', pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        care_actions: { cleaning: { brushing: {
          cleanliness_delta: 12, progress_delta: 4, cooldown_seconds: 3600,
          feedback_action: 'stretch',
        } } },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = { requestCat1Stretch: () => true };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  entry.children[1].children[3].children[1].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = entry.children[1].children[4];
  assert.equal(details.children.length, 2);
  const brushing = details.children[1];
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

test('stage 4 keeps the confirmed snapshot on a service rejection and exposes the server reason', async () => {
  const initial = stage4Snapshot({ status_values: { satiety: 100, energy: 61, cleanliness: 88, mood: 67, progress: 13 } });
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
        care_actions: { feeding: { small_fish: {
          inventory_item: 'small_fish', satiety_delta: 25, progress_delta: 0,
          feedback_action: 'eat_snack',
        } } },
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  entry.children[1].children[3].children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const details = entry.children[1].children[4];
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

function stage5Tasks(serviceDay, status = 'available', checked = true) {
  return {
    service_day: serviceDay,
    items: [{ id: 'daily-care-placeholder', title: '领取小鱼干', description: '可直接领取',
      status, condition: null, reward: { item_id: 'small_fish', quantity: 1 } }],
    daily: [{ id: 'daily-care-placeholder', title: '领取小鱼干', description: '可直接领取',
      status, condition: null, reward: { item_id: 'small_fish', quantity: 1 } }],
    state: [], daily_login: { service_day: serviceDay, checked },
  };
}

test('stage 5 loads a manual task and confirms daily login before showing the result', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17', 'available', false) });
  const checked = stage4Snapshot({
    inventory: {}, status_values: { ...initial.status_values, progress: 17 },
    tasks: stage5Tasks('2026-10-17', 'available', true),
    unlocks: [{ id: 'cat-nest-mailbox', name: '猫窝信箱', threshold: 4, type: 'exploration' }],
  });
  const calls = [];
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial, tasks: initial.tasks,
      }) };
    }
    if (url.endsWith('/daily-check-in')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: checked, progress_delta: 4, unlocked: [],
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  assert.equal(task.hidden, false);
  assert.equal(task.children.length, 4);
  assert.equal(task.children.some(child => child.className === 'neko-pet-care-task-load'), false);
  await waitFor(() => h.window.nekoPetCareEntry.getState().dailyCheckInPending === false &&
    h.window.nekoPetCareEntry.getState().tasks !== null);
  assert.equal(calls.filter(item => item.url.endsWith('/tasks')).length, 1);
  assert.equal(calls.filter(item => item.url.endsWith('/daily-check-in')).length, 1);
  assert.equal(task.children[1].children.length, 1);
  const fishRow = task.children[1].children[0];
  assert.equal(fishRow.children.length, 3);
  assert.equal(fishRow.children[1].className, 'neko-pet-care-task-reward');
  assert.equal(fishRow.textContent.includes('0/0'), false);
  assert.equal(task.children[2].children[0].textContent, '已解锁');
  assert.equal(task.children[2].children[1].textContent, '猫窝信箱');
  assert.equal(task.children[3].hidden, true);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 17);
});

test('stage 5 does not invent placeholder copy for sparse task records', async () => {
  const sparseTasks = {
    service_day: '2026-10-17',
    items: [{ task_id: 'configured-task', title: '完成一件事', status: 'available', claimable: false }],
    daily_login: { service_day: '2026-10-17', checked: true },
  };
  const snapshot = stage4Snapshot({ tasks: sparseTasks });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot, tasks: sparseTasks,
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 1);
  const row = task.children[1].children[0];
  assert.equal(row.textContent.includes('可领取'), false);
  assert.equal(row.textContent.includes('奖励待配置'), false);
  assert.equal(row.children.length, 1);
  assert.equal(row.children[0].children[0].textContent, '完成一件事');
});

test('stage 8 hides reclaim for a completed recovery task', async () => {
  const recoveryTasks = {
    service_day: '2026-10-17',
    items: [{ task_id: 'recovery-sick', title: '求药', status: 'completed', claimable: true,
      recovery_status: 'sick', recovery_item_id: 'medicine', reward: { item_id: 'medicine', quantity: 1 } }],
    daily_login: { service_day: '2026-10-17', checked: true },
  };
  const snapshot = stage4Snapshot({
    negative_status: { sick: { active: false }, sulk: { active: false } },
    inventory: {}, tasks: recoveryTasks,
  });
  const h = createHarness(async url => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot, tasks: recoveryTasks,
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
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 1 && h.window.nekoPetCareEntry.getState().taskLoading === false);
  const row = task.children[1].children[0];
  assert.equal(row.textContent.includes('领取'), false);
  assert.equal(row.children.some(child => child.dataset && child.dataset.taskId), false);
});

test('stage 6 reconciles a stale pet snapshot so the yarn task appears on panel open', async () => {
  const staleTasks = stage5Tasks('2026-10-17', 'claimed');
  const yarnTask = { task_id: 'daily-yarn-play', title: '毛线球', progress: 0, target: 3,
    claimable: false, reset_policy: 'daily', reward: {},
    progress_source: { category: 'playing', content_id: 'yarn' } };
  const reconciledTasks = { ...staleTasks,
    items: [...staleTasks.items, yarnTask],
    daily: [...staleTasks.daily, yarnTask] };
  const initial = stage4Snapshot({ tasks: staleTasks });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: stage4Snapshot({ tasks: reconciledTasks }),
        tasks: reconciledTasks,
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  assert.deepEqual(
    [...task.children[1].children].map(row => row.children[0].children[0].textContent),
    ['领取小鱼干', '毛线球'],
  );
  assert.equal(task.children[1].children[1].children.length, 2);
  assert.equal(task.children[1].children[1].children[1].textContent, '0/3');
});

test('stage 7 panel recall settles exploration without entering the model return chain', async () => {
  const active = {
    exploration_id: 'explore-panel-1', status: 'away', active: true,
    started_at: new Date(Date.now() - 1000).toISOString(),
    ends_at: new Date(Date.now() + 60000).toISOString(),
  };
  const recalled = { ...active, status: 'recalled', active: false };
  const initial = stage4Snapshot({ exploration: active });
  const terminal = stage4Snapshot({ exploration: recalled });
  const recallEvents = [];
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/explorations/explore-panel-1/recall')) {
      return { ok: true, status: 200, json: async () => ({
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
  h.window.addEventListener('neko:pet-care:exploration-recall-request', event => recallEvents.push(event.detail));
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  assert.equal(h.window.nekoPetCareEntry.requestExplorationRecall({
    source: 'pet-care-nest', presentation: 'cat-form',
  }), true);
  await waitFor(() => h.window.nekoPetCareEntry.getState().exploration?.status === 'recalled');
  assert.equal(recallEvents.length, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().catLocalActive, true);
});

test('stage 7 model-exit coordinator sends one confirmed recall for concurrent exits', async () => {
  const active = {
    exploration_id: 'explore-model-exit-1', status: 'away', active: true,
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
    exploration_id: 'explore-role-race-1', status: 'away', active: true,
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
    exploration_id: 'explore-logout-order-1', status: 'away', active: true,
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

test('stage 7 rehydrates a terminal exploration with one confirmable notice after refresh', async () => {
  const terminal = {
    exploration_id: 'explore-refresh-terminal', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    rewards: { drops: [{ item_id: 'small_fish', quantity: 1 }] },
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
  h.window.showAlertDialog = (message, options) => notices.push({ message, options });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 1);
  assert.match(notices[0].message, /探索已完成/);
  assert.equal(notices[0].options.title, '养宠');
  await h.window.nekoPetCareEntry.refreshAuthStatus('pageshow');
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(notices.length, 1);
});

test('stage 7 does not replay a terminal notice in a fresh renderer after reload', async () => {
  const terminal = {
    exploration_id: 'explore-persistent-terminal', status: 'completed', active: false,
    started_at: new Date(Date.now() - 60000).toISOString(),
    ends_at: new Date(Date.now() - 1000).toISOString(),
    rewards: { drops: [{ item_id: 'small_fish', quantity: 1 }] },
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
  first.window.showAlertDialog = (message, options) => firstNotices.push({ message, options });
  await waitFor(() => first.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(firstNotices.length, 1);

  // A reload/new webview gets a new session store, while the device-local
  // presentation record remains available to suppress the same activity.
  const second = createHarness(fetchImpl, undefined, '/', {
    localStorageValues: persistent.localStorageValues,
    sessionStorageValues: new Map(),
  });
  const secondNotices = [];
  second.window.showAlertDialog = (message, options) => secondNotices.push({ message, options });
  await waitFor(() => second.window.nekoPetCareEntry.getState().snapshot !== null);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(secondNotices.length, 0);
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

test('stage 6 reconciles tasks when the panel opened before binding completed', async () => {
  const staleTasks = stage5Tasks('2026-10-17', 'claimed');
  const yarnTask = { task_id: 'daily-yarn-play', title: '毛线球', progress: 0, target: 3,
    claimable: false, reset_policy: 'daily', reward: {},
    progress_source: { category: 'playing', content_id: 'yarn' } };
  const reconciledTasks = { ...staleTasks,
    items: [...staleTasks.items, yarnTask],
    daily: [...staleTasks.daily, yarnTask] };
  const initial = stage4Snapshot({ tasks: staleTasks });
  let resolveSnapshot;
  const snapshotGate = new Promise(resolve => { resolveSnapshot = resolve; });
  const h = createHarness(async (url) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: stage4Snapshot({ tasks: reconciledTasks }),
        tasks: reconciledTasks,
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
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(entry.dataset.expanded, 'true');
  resolveSnapshot();
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 2 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  assert.equal(task.children[1].children[1].children[0].children[0].textContent, '毛线球');
});

test('stage 5 keeps a task claim disabled while pending and applies reward only after confirmation', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17') });
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
        bound_role_id: 'server-role-a', snapshot: initial, tasks: initial.tasks,
      }) };
    }
    if (url.includes('/tasks/daily-care-placeholder/claim')) {
      postCalls += 1; await claimGate;
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        tasks: confirmed.tasks, reward: { item_id: 'small_fish', quantity: 1 },
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 1 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  const claim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.ok(claim);
  task.children[1].dispatchEvent({ type: 'click', target: claim, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskPendingId === 'daily-care-placeholder');
  const pendingClaim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.equal(pendingClaim.disabled, true);
  assert.equal(task.children[1].children.length, 1);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish || 0, 0);
  releaseClaim();
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskPendingId === '' &&
    h.window.nekoPetCareEntry.getState().snapshot.inventory.small_fish === 1);
  assert.equal(postCalls, 1);
  const confirmedClaim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.equal(confirmedClaim.textContent, '已领取');
});

test('stage 5 leaves the confirmed snapshot unchanged when task claim fails', async () => {
  const initial = stage4Snapshot({ inventory: {}, tasks: stage5Tasks('2026-10-17') });
  const h = createHarness(async (url, options = {}) => {
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/tasks/daily-care-placeholder/claim')) {
      return { ok: false, status: 409, json: async () => ({ code: 'task_reward_unavailable' }) };
    }
    if (url.endsWith('/tasks')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        bound_role_id: 'server-role-a', snapshot: initial, tasks: initial.tasks,
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
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  const task = entry.children[1].children[6];
  await waitFor(() => task.children[1].children.length === 1 &&
    h.window.nekoPetCareEntry.getState().taskLoading === false);
  const claim = task.children[1].children[0].children.find(child => child.dataset && child.dataset.taskId);
  assert.ok(claim);
  task.children[1].dispatchEvent({ type: 'click', target: claim, stopPropagation() {} });
  await waitFor(() => h.window.nekoPetCareEntry.getState().taskPendingId === '');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.snapshot.inventory.small_fish || 0, 0);
  assert.equal(state.snapshot.status_values.progress, 13);
  assert.equal(state.tasks.items[0].status, 'available');
  assert.equal(state.taskError, 'task_reward_unavailable');
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

test('stage 6 settles existing user yarn play only after the cat terminal response', async () => {
  const calls = [];
  const initialTasks = {
    service_day: '2026-10-17',
    items: [{ task_id: 'daily-yarn-play', title: '毛线球', progress: 0, target: 3,
      claimable: false, reset_policy: 'daily', reward: {},
      progress_source: { category: 'playing', content_id: 'yarn' } }],
  };
  const confirmedTasks = {
    ...initialTasks,
    items: [{ ...initialTasks.items[0], progress: 1, status: 'in_progress' }],
  };
  const initial = stage4Snapshot({ activity_counters: { service_day: '2026-10-17', playing: {} }, tasks: initialTasks });
  const confirmed = stage4Snapshot({
    activity_counters: { service_day: '2026-10-17', playing: { yarn: 1 } },
    status_values: { ...initial.status_values, energy: 53, cleanliness: 86, progress: 17 },
    tasks: confirmedTasks,
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      assert.equal(request.category, 'playing');
      assert.equal(request.content_id, 'yarn');
      assert.equal(request.user_initiated, true);
      assert.equal(request.cat_responded, true);
      assert.equal(request.terminal_status, 'done');
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        play_actions: { yarn: { daily_count: 1, daily_progress_limit: 3, remaining_progress_count: 2,
          progress_delta: 4, energy_delta: -8, cleanliness_delta: -2 } },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        play_actions: { yarn: { daily_count: 0, daily_progress_limit: 3, remaining_progress_count: 3,
          progress_delta: 4, energy_delta: -8, cleanliness_delta: -2 } },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  h.window.nekoPetCareEntry.setActionCategory('play');
  const playDetails = entry.children[1].children[4];
  assert.equal(playDetails.children.length, 0);
  assert.equal(playDetails.children.some(child => child.dataset && child.dataset.contentId), false);
  assert.equal(playDetails.children.some(child => child.className === 'neko-pet-care-play-info'), false);
  await waitFor(() => entry.children[1].children[6].children[1].children.length === 1);
  const taskRow = entry.children[1].children[6].children[1].children[0];
  assert.equal(taskRow.className, 'neko-pet-care-task-item');
  assert.equal(taskRow.children[0].children[0].textContent, '毛线球');
  assert.equal(taskRow.children[1].textContent, '0/3');
  assert.equal(taskRow.children.length, 2);
  const wakeCalls = [];
  h.window.nekoAutoGoodbye = {
    restartVisualTierForPetCare(source) {
      wakeCalls.push(source);
      return true;
    },
  };
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'chat_yarn_drag_completed', source: 'cat-yarn-observation-adapter',
    detail: { userInitiated: true, endedNearCat: true, originSource: 'chat-yarn', sessionId: 'yarn-1' },
  } }));
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity !== null, true);
  assert.deepEqual(wakeCalls, ['pet-care-user-action']);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.energy, 53);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.cleanliness, 86);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.progress, 17);
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'cat1_local_play_done', source: 'cat1-journey-local', detail: {},
  } }));
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity !== null, true);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
  } }));
  await waitFor(() => calls.filter(item => item.method === 'POST').length === 1);
  await waitFor(() => h.window.nekoPetCareEntry.getState().playPending === null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 17);
  const confirmedTaskRow = entry.children[1].children[6].children[1].children[0];
  assert.equal(confirmedTaskRow.children[1].textContent, '1/3');
});

test('stage 6 settles a consumable toy from its explicit presentation terminal', async () => {
  const calls = [];
  let terminal = null;
  const initial = stage4Snapshot({
    activity_counters: { service_day: '2026-10-17', playing: {} },
    inventory: { teaser_wand: 1 },
  });
  const confirmed = stage4Snapshot({
    activity_counters: { service_day: '2026-10-17', playing: { teaser_wand: 1 } },
    inventory: { teaser_wand: 0 },
    status_values: { ...initial.status_values, energy: 51, cleanliness: 86, progress: 21 },
  });
  const h = createHarness(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.includes('/api/card-drop/oauth/status')) {
      return { ok: true, status: 200, json: async () => ({ logged_in: true, local_user_id: 'account-a' }) };
    }
    if (url.includes('/api/pet-care/pets/') && options.method === 'POST') {
      const request = JSON.parse(options.body);
      assert.equal(request.category, 'playing');
      assert.equal(request.content_id, 'teaser_wand');
      assert.equal(request.source, 'pet-care-toy');
      assert.equal(request.user_initiated, true);
      assert.equal(request.cat_responded, true);
      assert.equal(request.terminal_status, 'done');
      return { ok: true, status: 200, json: async () => ({
        binding_status: 'bound', account_id: 'account-a', current_character_uid: 'local-role-a',
        bound_role_id: 'server-role-a', snapshot: confirmed,
        play_actions: { teaser_wand: { label: '逗猫棒', inventory_item: 'teaser_wand', inventory_quantity: 0, daily_count: 1, daily_limit: 3,
          progress_delta: 8, energy_delta: -10, cleanliness_delta: -2 } },
      }) };
    }
    if (url.includes('/api/pet-care/pets/')) {
      return { ok: true, status: 200, json: async () => ({
        account_id: 'account-a', current_character_uid: 'local-role-a', binding_status: 'bound',
        pet_id: 'pet-a', bound_role_id: 'server-role-a', snapshot: initial,
        play_actions: {
          yarn: { daily_count: 0, daily_progress_limit: 3, remaining_progress_count: 3 },
          teaser_wand: { label: '逗猫棒', inventory_item: 'teaser_wand', inventory_quantity: 1, daily_count: 0, daily_limit: 3,
            progress_delta: 8, energy_delta: -10, cleanliness_delta: -2 },
        },
      }) };
    }
    throw new Error('unexpected request: ' + url);
  });
  h.window.NekoCatIdlePresentation = {
    requestCat1Play(options) {
      terminal = options.onTerminal;
      return true;
    },
  };
  await waitFor(() => h.window.nekoPetCareEntry.getState().snapshot !== null);
  enterCatForm(h);
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  entry.children[0].dispatchEvent({ type: 'click', stopPropagation() {} });
  h.window.nekoPetCareEntry.setActionCategory('play');
  const playDetails = entry.children[1].children[4];
  const toyButton = playDetails.children.find(child => child.dataset && child.dataset.contentId === 'teaser_wand');
  assert.ok(toyButton);
  assert.equal(h.window.nekoPetCareEntry.getState().playActions.teaser_wand.inventory_quantity, 1);
  assert.equal(toyButton.disabled, false);
  playDetails.dispatchEvent({ type: 'click', target: toyButton, stopPropagation() {} });
  assert.equal(h.window.nekoPetCareEntry.getState().playActivity.content_id, 'teaser_wand');
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.energy, 51);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.cleanliness, 86);
  assert.equal(h.window.nekoPetCareEntry.getState().optimisticSnapshot.status_values.progress, 21);
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
  } }));
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  terminal('done', { reason: 'cat1-play-action-finished' });
  await waitFor(() => calls.filter(item => item.method === 'POST').length === 1);
  await waitFor(() => h.window.nekoPetCareEntry.getState().playPending === null);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.inventory.teaser_wand, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 21);
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
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:action-result', { detail: {
    actionId: 'cat1_play_yarn', result: 'done', source: 'cat_mind',
  } }));
  h.window.dispatchEvent(new h.window.CustomEvent('neko:cat-mind:observation', { detail: {
    type: 'cat1_local_play_done', source: 'cat1-journey-local', detail: {},
  } }));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  assert.equal(h.window.nekoPetCareEntry.getState().snapshot.status_values.progress, 13);
});

test('stage 6 keeps the current playground restore event out of play settlement', async () => {
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
  h.window.dispatchEvent(new h.window.CustomEvent('neko:idle-cat1-playground-yarn-activate', {
    detail: { activationId: 'playground-1', source: 'cat1-playground', trigger: 'yarn-click' },
  }));
  await new Promise(resolve => setTimeout(resolve, 10));
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.playActivity, null);
  assert.equal(state.playPending, null);
  assert.equal(calls.filter(item => item.method === 'POST').length, 0);
  assert.equal(state.snapshot.status_values.progress, 13);
});

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

function createHarness(fetchImpl, identityValue = { uid: 'local-role-a', name: '小猫 A' }) {
  const window = new EventTargetLike();
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
  const localStorageValues = new Map();
  window.localStorage = {
    getItem(key) { return localStorageValues.has(key) ? localStorageValues.get(key) : null; },
    setItem(key, value) { localStorageValues.set(key, String(value)); },
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
  assert.equal(entry.children[1].children[7].children[0].textContent, '登录 N.E.K.O 账号');
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
  assert.equal(panel.children[6].textContent, '物品与背包 · 后续阶段开放');
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
  await new Promise(resolve => setTimeout(resolve, 10));
  const entry = h.body.children.find(child => child.id === 'neko-pet-care-entry-overlay');
  const trigger = entry.children[0];
  trigger.dispatchEvent({ type: 'click', stopPropagation() {} });
  assert.equal(h.window.nekoPetCareEntry.getState().panelOpen, true);
  loggedIn = false;
  await h.window.nekoPetCareEntry.refreshAuthStatus('logout');
  const state = h.window.nekoPetCareEntry.getState();
  assert.equal(state.authStatus, 'logged-out');
  assert.equal(state.panelOpen, false);
  assert.equal(h.window.nekoPetCareMode.state.context.accountId, '');
  assert.equal(h.window.nekoPetCareMode.state.context.boundRoleId, '');
  assert.equal(entry.dataset.expanded, 'false');
  assert.equal(entry.children[1].children[4].hidden, true);
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
  assert.equal(entry.children[1].children[4].hidden, true);
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
  assert.equal(entry.children[1].children[7].children[2].hidden, false);
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
  const footer = entry.children[1].children[7];
  assert.equal(footer.children[0].hidden, false);
  assert.equal(footer.children[2].hidden, false);
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
  assert.equal(panel.children[3].children.every(button => button.disabled), true);
  assert.equal(panel.children[3].children.every(button => button.getAttribute('title') === '后续阶段开放'), true);
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
  assert.equal(entry.children[1].children[4].hidden, true);
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

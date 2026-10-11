// Retained-root cleanup can now delete part of the old directory and answer
// 409 retained_source_cleanup_incomplete. The completion card must say what
// was kept instead of the generic "failed, try again" text.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'static/app/app-storage-location.js'), 'utf8');
const messages = JSON.parse(fs.readFileSync(path.join(root, 'static/locales/en.json'), 'utf8'));

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.className = '';
    const classes = new Set();
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      toggle: (name, force) => (force === undefined ? !classes.delete(name) && classes.add(name) : force ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    };
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((item) => item !== child); return child; }
  insertBefore(child) { this.children.unshift(child); return child; }
  addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  removeEventListener() {}
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] || null; }
  removeAttribute(name) { delete this.attributes[name]; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
}

function loadStorageLocation(cleanupResponse) {
  const created = [];
  const toasts = [];
  const requests = [];
  const windowListeners = {};
  const notice = {
    completed: true,
    retained_root_exists: true,
    cleanup_available: true,
    retained_root: 'D:/old/N.E.K.O',
    target_root: 'E:/new/N.E.K.O',
  };
  const document = {
    currentScript: { getAttribute() { return 'false'; } },
    body: new FakeElement('body'),
    documentElement: new FakeElement('html'),
    createElement(tag) { const element = new FakeElement(tag); created.push(element); return element; },
    addEventListener() {},
    removeEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
  };
  const window = {
    location: { origin: 'http://localhost' },
    localStorage: { getItem() { return ''; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return ''; }, setItem() {}, removeItem() {} },
    addEventListener(type, handler) { (windowListeners[type] = windowListeners[type] || []).push(handler); },
    removeEventListener() {},
    confirm() { return true; },
    setTimeout() { return 0; },
    clearTimeout() {},
    showStatusToast(message) { toasts.push(String(message)); },
    safeT(key, fallback) {
      return key.split('.').reduce((value, part) => value && value[part], messages) || fallback;
    },
  };
  async function fetch(url, options) {
    requests.push(String(url));
    if (String(url).endsWith('/retained-source/cleanup')) {
      return { ok: cleanupResponse.status === 200, status: cleanupResponse.status, json: async () => cleanupResponse.body };
    }
    if (String(url).endsWith('/api/storage/location/status')) {
      return { ok: true, status: 200, json: async () => ({ ok: true, ready: true, completion_notice: notice }) };
    }
    throw new Error(`unexpected request ${url} ${options && options.method}`);
  }
  const context = vm.createContext({ window, document, console, fetch, setTimeout: () => 0, clearTimeout() {} });
  vm.runInContext(script, context);
  return { window, windowListeners, created, toasts, requests };
}

async function openCardAndClickCleanup(env) {
  for (const handler of env.windowListeners['neko:startup-greeting-release'] || []) {
    handler({ detail: { released: true } });
  }
  await env.window.appStorageLocation.refreshCompletionNotice();
  const cleanupButton = env.created.find(
    (element) => element.tagName === 'BUTTON' && element.textContent === messages.storage.cleanupRetainedRoot
  );
  assert.ok(cleanupButton, 'completion card shows the cleanup button');
  for (const handler of cleanupButton.listeners.click || []) {
    await handler();
  }
  return cleanupButton;
}

test('partial cleanup names the kept entries and keeps the card usable', async () => {
  const env = loadStorageLocation({
    status: 409,
    body: {
      ok: false,
      error_code: 'retained_source_cleanup_incomplete',
      error: 'raw backend detail',
      remaining_entries: ['config', 'memory'],
    },
  });
  const cleanupButton = await openCardAndClickCleanup(env);

  assert.strictEqual(env.toasts.length, 1);
  assert.ok(env.toasts[0].startsWith(messages.storage.retainedSourceCleanupIncomplete), env.toasts[0]);
  assert.ok(env.toasts[0].endsWith('config, memory'), env.toasts[0]);
  assert.ok(!env.toasts[0].includes(messages.storage.cleanupRetainedRootFailed));
  assert.strictEqual(cleanupButton.disabled, false);
  // The notice is fetched again after the partial cleanup.
  const statusRequests = env.requests.filter((url) => url.endsWith('/api/storage/location/status'));
  assert.strictEqual(statusRequests.length, 2);
});

test('cleanup that leaves other user files says the directory was kept', async () => {
  const env = loadStorageLocation({
    status: 200,
    body: { ok: true, cleaned_root: 'D:/old/N.E.K.O', retained_root_kept: true },
  });
  await openCardAndClickCleanup(env);

  assert.deepStrictEqual(env.toasts, [messages.storage.cleanupRetainedRootDoneKept]);
});

test('cleanup that removes the whole directory keeps the original message', async () => {
  const env = loadStorageLocation({
    status: 200,
    body: { ok: true, cleaned_root: 'D:/old/N.E.K.O', retained_root_kept: false },
  });
  await openCardAndClickCleanup(env);

  assert.deepStrictEqual(env.toasts, [messages.storage.cleanupRetainedRootDone]);
});

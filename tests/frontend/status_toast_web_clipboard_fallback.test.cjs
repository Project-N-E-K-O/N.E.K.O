'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.resolve(__dirname, '../../static/app/app-ui/bootstrap-goodbye-and-toasts.js');
const source = fs.readFileSync(sourcePath, 'utf8');

function classList() {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

function createElement(tagName) {
  const element = {
    id: '',
    tagName: String(tagName).toUpperCase(),
    nodeType: 1,
    childNodes: [],
    parentNode: null,
    classList: classList(),
    style: { cssText: '' },
    textContent: '',
    value: '',
    appendChild(child) {
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.childNodes = this.parentNode.childNodes.filter((child) => child !== this);
      this.parentNode = null;
    },
    querySelector(selector) {
      if (!selector.startsWith('#')) return null;
      return this.childNodes.find((child) => child.id === selector.slice(1)) || null;
    },
    setAttribute() {},
    removeAttribute() {},
    addEventListener() {},
    removeEventListener() {},
    _hover: false,
    matches(selector) { return this._hover && String(selector).includes(':hover'); },
    focus() { document.activeElement = this; },
    blur() { if (document.activeElement === this) document.activeElement = null; },
    select() {},
  };
  return element;
}

let document;

function createDeferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function createHarness(navigatorClipboard) {
  const statusToast = createElement('div');
  statusToast.id = 'status-toast';
  const body = createElement('body');
  const head = createElement('head');
  const copied = [];
  const timers = new Map();
  let nextTimerId = 0;
  document = {
    body,
    head,
    activeElement: null,
    createElement,
    getElementById(id) { return id === 'status-toast' ? statusToast : null; },
    querySelector() { return null; },
    execCommand(command) {
      copied.push(command);
      return command === 'copy';
    },
  };
  const window = {
    appUi: {},
    __appUiParts: {},
    appState: { dom: { statusToast }, _statusToastPriority: 0 },
    appConst: {},
    t(_key, options) { return options && options.defaultValue ? options.defaultValue : ''; },
    addEventListener() {},
    dispatchEvent() {},
  };
  vm.runInNewContext(source, {
    window,
    document,
    navigator: navigatorClipboard ? { clipboard: navigatorClipboard } : {},
    console: { log() {}, error() {} },
    Promise,
    Date,
    Array,
    Object,
    Number,
    String,
    Math,
    setTimeout(callback, delay) {
      nextTimerId += 1;
      timers.set(nextTimerId, { callback, delay });
      return nextTimerId;
    },
    clearTimeout(id) { timers.delete(id); },
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = init && init.detail; }
    },
  }, { filename: sourcePath });
  return {
    window,
    statusToast,
    copied,
    countTimers(delay) {
      return Array.from(timers.values()).filter((timer) => timer.delay === delay).length;
    },
    runTimer(delay) {
      const match = Array.from(timers.entries()).find(([, timer]) => timer.delay === delay);
      assert.ok(match, `expected a pending ${delay}ms timer`);
      timers.delete(match[0]);
      match[1].callback();
    },
  };
}

async function clickToastText(harness) {
  harness.window.showStatusToast('API request failed: 401');
  const text = harness.statusToast.querySelector('#status-toast-text');
  assert.ok(text);
  text.onclick({ stopPropagation() {} });
  await Promise.resolve();
  await Promise.resolve();
}

test('ordinary web toast falls back to execCommand when Clipboard API is unavailable', async () => {
  const harness = createHarness(null);

  await clickToastText(harness);

  assert.deepEqual(harness.copied, ['copy']);
});

test('ordinary web toast falls back when Clipboard API rejects', async () => {
  const harness = createHarness({ writeText: () => Promise.reject(new Error('denied')) });

  await clickToastText(harness);

  assert.deepEqual(harness.copied, ['copy']);
});

test('ordinary pointer copy releases focus and resumes auto-hide', () => {
  const harness = createHarness(null);
  harness.window.showStatusToast('copy and close later');
  const text = harness.statusToast.querySelector('#status-toast-text');
  text.focus();
  harness.window.appState.statusToastTimeout = null;

  text.onclick({ stopPropagation() {} });

  assert.equal(document.activeElement, null);
  assert.notEqual(harness.window.appState.statusToastTimeout, null);
});

test('ordinary pointer copy keeps auto-hide paused while the toast remains hovered', () => {
  const harness = createHarness(null);
  harness.window.showStatusToast('keep reading');
  const text = harness.statusToast.querySelector('#status-toast-text');
  text.focus();
  harness.window.appState.statusToastTimeout = null;
  harness.statusToast._hover = true;

  text.onclick({ stopPropagation() {} });

  assert.equal(document.activeElement, null);
  assert.equal(harness.window.appState.statusToastTimeout, null);

  harness.statusToast._hover = false;
  harness.statusToast._toastLeave();
  assert.notEqual(harness.window.appState.statusToastTimeout, null);
});

test('ordinary repeated copies reset the success feedback timer', async () => {
  const harness = createHarness(null);
  harness.window.showStatusToast('copy twice');
  const text = harness.statusToast.querySelector('#status-toast-text');

  text.onclick({ stopPropagation() {} });
  await Promise.resolve();
  await Promise.resolve();
  text.onclick({ stopPropagation() {} });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(harness.countTimers(450), 1);
  assert.equal(text.classList.contains('status-toast-copy-success'), true);
  harness.runTimer(450);
  assert.equal(text.classList.contains('status-toast-copy-success'), false);
});

test('a delayed web copy cannot flash success on a newer toast', async () => {
  const pendingCopy = createDeferred();
  const harness = createHarness({ writeText: () => pendingCopy.promise });
  harness.window.showStatusToast('first error');
  const text = harness.statusToast.querySelector('#status-toast-text');
  text.onclick({ stopPropagation() {} });

  harness.window.showStatusToast('second error');
  pendingCopy.resolve();
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(text.textContent, 'second error');
  assert.equal(text.classList.contains('status-toast-copy-success'), false);
});

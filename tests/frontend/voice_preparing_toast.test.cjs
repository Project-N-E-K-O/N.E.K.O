'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.resolve(__dirname, '../../static/app/app-ui/bootstrap-goodbye-and-toasts.js');
const source = fs.readFileSync(sourcePath, 'utf8');

function createElement(tagName) {
  return {
    id: '',
    tagName: String(tagName).toUpperCase(),
    childNodes: [],
    style: { cssText: '' },
    textContent: '',
    innerHTML: '',
    appendChild(child) { this.childNodes.push(child); return child; },
    setAttribute() {},
    addEventListener() {},
  };
}

function createHarness() {
  const byId = new Map();
  const body = createElement('body');
  const head = createElement('head');
  const timers = new Map();
  let nextTimerId = 0;
  const document = {
    body,
    head,
    createElement,
    getElementById(id) {
      if (byId.has(id)) return byId.get(id);
      const found = body.childNodes.find((child) => child.id === id) || null;
      if (found) byId.set(id, found);
      return found;
    },
    querySelector() { return null; },
    addEventListener() {},
  };
  const window = {
    appUi: {},
    __appUiParts: {},
    appState: { localAsrPreparingMessage: null },
    appConst: {},
    t(_key, options) { return options && options.defaultValue ? options.defaultValue : ''; },
    addEventListener() {},
    dispatchEvent() {},
  };
  vm.runInNewContext(source, {
    window,
    document,
    navigator: {},
    console: { log() {}, warn() {}, error() {} },
    Promise, Date, Array, Object, Number, String, Math,
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
  const I = window.__appUiParts;
  return {
    S: window.appState,
    show: (message) => I.showVoicePreparingToast(message),
    hide: (options) => I.hideVoicePreparingToast(options),
    toast: () => document.getElementById('voice-preparing-toast'),
    runTimers() {
      for (const [id, timer] of Array.from(timers.entries())) {
        timers.delete(id);
        timer.callback();
      }
    },
  };
}

test('a notice shown again right after a hide is not hidden by that hide\'s fade-out', () => {
  // READY hides the notice, PREPARING shows it again at once: the earlier
  // hide's delayed display:none must not take the new notice down.
  const harness = createHarness();
  harness.show('Loading the local speech model');
  harness.hide();
  harness.show('Loading the local speech model');
  harness.runTimers();
  assert.equal(harness.toast().style.display, 'flex');
});

test('a plain hide retires the local-model notice (error paths, avatar drop)', () => {
  const harness = createHarness();
  harness.S.localAsrPreparingMessage = 'Loading the local speech model';
  harness.show(harness.S.localAsrPreparingMessage);
  harness.hide();
  harness.runTimers();
  assert.equal(harness.toast().style.display, 'none');
  assert.equal(harness.S.localAsrPreparingMessage, null);
});

test('the voice start flow can hide its own toast and keep the local-model notice', () => {
  const harness = createHarness();
  harness.S.localAsrPreparingMessage = 'Loading the local speech model';
  harness.show('Starting voice…');
  harness.hide({ keepLocalAsrNotice: true });
  harness.runTimers();
  assert.equal(harness.toast().style.display, 'flex');
  assert.equal(harness.S.localAsrPreparingMessage, 'Loading the local speech model');
});

test('only the successful-start hides keep the local-model notice', () => {
  const read = (name) => fs.readFileSync(path.resolve(__dirname, '../../static/app', name), 'utf8');
  const keeps = (text) => (text.match(/hideVoicePreparingToast\(\{ keepLocalAsrNotice: true \}\)/g) || []).length;
  const buttons = read('app-buttons.js');
  const websocket = read('app-websocket.js');
  assert.equal(keeps(buttons), 1);
  assert.equal(keeps(websocket), 1);
  // The start succeeded: the preparing toast gives way to "ready".
  assert.match(buttons, /Success — hide preparing toast, show ready\s+window\.hideVoicePreparingToast\(\{ keepLocalAsrNotice: true \}\);/);
  // The session_started ack drops the banner in a window whose start it answers.
  assert.match(websocket, /_ackAnswersThisWindow && typeof window\.hideVoicePreparingToast === 'function'\) window\.hideVoicePreparingToast\(\{ keepLocalAsrNotice: true \}\);/);
});

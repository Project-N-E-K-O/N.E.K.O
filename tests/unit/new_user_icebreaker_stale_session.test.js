'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ICEBREAKER_PATH = path.resolve(__dirname, '../../static/tutorial/icebreaker/new-user-icebreaker.js');

function loadIcebreaker({ fetchImpl }) {
  const listeners = new Map();
  const storage = new Map();
  const clearPromptCalls = [];
  const setPromptCalls = [];

  const chatHost = {
    openWindow() {},
    isMounted: () => true,
    appendMessage: async () => true,
    setIcebreakerChoicePrompt(payload) { setPromptCalls.push(payload); },
    clearIcebreakerChoicePrompt(sessionId) { clearPromptCalls.push(sessionId); },
  };

  const windowObj = {
    location: { pathname: '/' },
    document: {
      visibilityState: 'visible',
      getElementById: () => null,
      querySelector: () => null,
      addEventListener() {},
      removeEventListener() {},
    },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    lanlan_config: { lanlan_name: 'Neko' },
    reactChatWindowHost: chatHost,
    nekoLocalMutationSecurity: {
      getMutationHeaders: async () => ({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok-1' }),
      refreshToken: async () => {},
    },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener() {},
    dispatchEvent(evt) {
      for (const fn of listeners.get(evt.type) || []) fn(evt);
    },
    setTimeout(fn, delay) {
      if (delay < 10000) return global.setTimeout(fn, 0);
      return 1;
    },
    clearTimeout() {},
  };

  const context = {
    window: windowObj,
    document: windowObj.document,
    CustomEvent: windowObj.CustomEvent,
    fetch: fetchImpl,
    console: { log() {}, warn() {}, error() {} },
    setTimeout: windowObj.setTimeout,
    clearTimeout: windowObj.clearTimeout,
    Promise,
    Date,
    Math,
    JSON,
    Object,
    Array,
    String,
    Number,
    Error,
  };
  windowObj.window = windowObj;

  vm.runInNewContext(fs.readFileSync(ICEBREAKER_PATH, 'utf8'), context, { filename: ICEBREAKER_PATH });
  return { windowObj, listeners, clearPromptCalls, setPromptCalls };
}

async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) return false;
    await new Promise((r) => setTimeout(r, 5));
  }
  return true;
}

test('F0489: stale_session on /choice deactivates session without retrying or restoring choice prompt', async () => {
  const fetchCalls = [];
  const { windowObj, listeners, clearPromptCalls, setPromptCalls } = loadIcebreaker({
    fetchImpl: async (url) => {
      fetchCalls.push(url);
      if (url.endsWith('icebreaker_scripts.json')) {
        return {
          ok: true,
          json: async () => ({
            days: {
              '1': {
                root: 'n1',
                nodes: {
                  n1: {
                    lineKey: 'l1',
                    options: [{ id: 'opt1', labelKey: 'o1', next: 'n2' }],
                  },
                  n2: { lineKey: 'l2', handoffKey: 'h1' },
                },
              },
            },
          }),
        };
      }
      if (url.includes('/locales/')) {
        return { ok: true, json: async () => ({ l1: 'Hello', o1: 'Hi', l2: 'Next' }) };
      }
      if (url.endsWith('/api/icebreaker/choice')) {
        return { ok: true, json: async () => ({ ok: true, skipped: 'stale_session', reason: 'session_id_mismatch' }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });

  const started = await windowObj.newUserIcebreaker.start('1');
  assert.equal(started, true);
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), true);
  assert.ok(windowObj.newUserIcebreaker.getActiveSession());

  const beforePromptCount = setPromptCalls.length;
  const beforeClearCount = clearPromptCalls.length;
  const choiceListeners = listeners.get('neko:icebreaker-choice-selected') || [];
  assert.ok(choiceListeners.length >= 1);

  choiceListeners[0]({ detail: { choice: 'opt1' } });
  const settled = await waitFor(() => !windowObj.NekoNewUserIcebreakerState.isPeriodActive());
  assert.equal(settled, true, 'activeSession must be cleared');

  const choicePostCalls = fetchCalls.filter((u) => u.endsWith('/api/icebreaker/choice'));
  assert.equal(choicePostCalls.length, 1, 'should not retry /choice after stale_session');
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), false, 'activeSession must be cleared');
  assert.equal(windowObj.newUserIcebreaker.getActiveSession(), null);
  assert.equal(setPromptCalls.length, beforePromptCount, 'must not restore choice prompt after stale_session');
  assert.ok(clearPromptCalls.length > beforeClearCount, 'must clear choice prompt on stale_session');
});

test('F0489: stale_session on /speak deactivates active icebreaker session', async () => {
  const { windowObj } = loadIcebreaker({
    fetchImpl: async (url) => {
      if (url.endsWith('icebreaker_scripts.json')) {
        return {
          ok: true,
          json: async () => ({
            days: {
              '1': {
                root: 'n1',
                nodes: {
                  n1: { lineKey: 'l1', options: [{ id: 'opt1', labelKey: 'o1', next: 'n2' }] },
                },
              },
            },
          }),
        };
      }
      if (url.includes('/locales/')) {
        return { ok: true, json: async () => ({ l1: 'Hello there', o1: 'Hi' }) };
      }
      if (url.endsWith('/api/icebreaker/speak')) {
        return { ok: true, json: async () => ({ ok: true, skipped: 'stale_session', reason: 'session_id_mismatch' }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });

  await windowObj.newUserIcebreaker.start('1');
  const settled = await waitFor(() => !windowObj.NekoNewUserIcebreakerState.isPeriodActive());
  assert.equal(settled, true);
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), false);
  assert.equal(windowObj.newUserIcebreaker.getActiveSession(), null);
});

test('F0489: stale_session on /context deactivates active icebreaker session', async () => {
  const { windowObj } = loadIcebreaker({
    fetchImpl: async (url) => {
      if (url.endsWith('icebreaker_scripts.json')) {
        return {
          ok: true,
          json: async () => ({
            days: {
              '1': {
                root: 'n1',
                nodes: {
                  n1: { lineKey: 'l1', options: [{ id: 'opt1', labelKey: 'o1', next: 'n2' }] },
                },
              },
            },
          }),
        };
      }
      if (url.includes('/locales/')) {
        return { ok: true, json: async () => ({ l1: 'Hello there', o1: 'Hi' }) };
      }
      if (url.endsWith('/api/icebreaker/context')) {
        return { ok: true, json: async () => ({ ok: true, skipped: 'stale_session', reason: 'session_id_mismatch' }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });

  const started = await windowObj.newUserIcebreaker.start('1');
  assert.equal(started, false);
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), false);
  assert.equal(windowObj.newUserIcebreaker.getActiveSession(), null);
});

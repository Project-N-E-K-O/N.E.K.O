'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const FREE_TEXT_RUNTIME_PATH = path.resolve(__dirname, '../../static/tutorial/icebreaker/free-text-runtime.js');
const ICEBREAKER_PATH = path.resolve(__dirname, '../../static/tutorial/icebreaker/new-user-icebreaker.js');

function loadIcebreaker({ fetchImpl }) {
  const listeners = new Map();
  const storage = new Map();
  const clearPromptCalls = [];
  const setPromptCalls = [];
  const dispatchedEvents = [];

  const chatHost = {
    openWindow() {},
    isMounted: () => true,
    appendMessage: async (msg) => msg || true,
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
      dispatchedEvents.push(evt);
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

  vm.createContext(context);
  vm.runInContext(fs.readFileSync(FREE_TEXT_RUNTIME_PATH, 'utf8'), context, { filename: FREE_TEXT_RUNTIME_PATH });
  vm.runInContext(fs.readFileSync(ICEBREAKER_PATH, 'utf8'), context, { filename: ICEBREAKER_PATH });
  return { windowObj, listeners, clearPromptCalls, setPromptCalls, dispatchedEvents };
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

test('F0489: stale_session on /speak returns false without deactivating active session', async () => {
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

  const started = await windowObj.newUserIcebreaker.start('1');
  assert.equal(started, true);
  const activeBefore = windowObj.newUserIcebreaker.getActiveSession();
  assert.ok(activeBefore);

  const speakOk = await windowObj.newUserIcebreaker.speak('Hello again', '');
  assert.equal(speakOk, false);
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), true);
  assert.equal(windowObj.newUserIcebreaker.getActiveSession()?.sessionId, activeBefore.sessionId);
});

test('F0489: stale_session on /speak during free-text release still completes markDay and emits ended event', async () => {
  const fetchCalls = [];
  const { windowObj, listeners, dispatchedEvents } = loadIcebreaker({
    fetchImpl: async (url) => {
      fetchCalls.push(url);
      if (url.endsWith('icebreaker_scripts.json')) {
        return {
          ok: true,
          json: async () => ({
            days: {
              '1': {
                root: 'n1',
                fallback: { releaseKey: 'rel1', redirectKey: 'red1' },
                nodes: {
                  n1: { lineKey: 'l1', options: [{ id: 'A', labelKey: 'o1', next: 'n2' }] },
                  n2: { lineKey: 'l2', handoffKey: 'h1' },
                },
              },
            },
          }),
        };
      }
      if (url.includes('/locales/')) {
        return { ok: true, json: async () => ({ l1: 'Hello there', o1: 'Hi', rel1: 'Okay, go ahead', red1: 'Back to topic' }) };
      }
      if (url.endsWith('/api/icebreaker/free-text/interpret')) {
        return { ok: true, json: async () => ({ ok: true, action: 'release', reply: 'Go ahead', topic_state: 'hard_exit' }) };
      }
      if (url.endsWith('/api/icebreaker/speak')) {
        return { ok: true, json: async () => ({ ok: true, skipped: 'stale_session', reason: 'session_id_mismatch' }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });

  const started = await windowObj.newUserIcebreaker.start('1');
  assert.equal(started, true);
  const freeTextListeners = listeners.get('neko:icebreaker-free-text-submitted') || [];
  assert.ok(freeTextListeners.length >= 1);

  freeTextListeners[0]({ detail: { text: 'Let us stop here', requestId: 'ft-1' } });
  const completed = await waitFor(() => windowObj.NekoNewUserIcebreakerState.hasCompletedDay('1'));
  assert.equal(completed, true, 'day 1 must be marked completed even when /speak returns stale_session');

  const store = windowObj.NekoNewUserIcebreakerState.readStore();
  assert.equal(store.days['1'].completed, true);
  assert.equal(store.days['1'].releasedByFreeText, true);
  assert.ok(fetchCalls.some((u) => u.endsWith('/api/icebreaker/route/end')), 'must call /route/end');
  const endedEvt = dispatchedEvents.find((e) => e.type === 'neko:new-user-icebreaker-ended');
  assert.ok(endedEvt, 'must emit neko:new-user-icebreaker-ended');
  assert.equal(endedEvt.detail.reason, 'free_text_release');
});

test('F0489: late stale_session from superseded startForDay does not deactivate replacement session', async () => {
  let contextCallCount = 0;
  let resolveFirstContext = null;
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
        contextCallCount += 1;
        if (contextCallCount === 1) {
          return new Promise((resolve) => {
            resolveFirstContext = () => resolve({
              ok: true,
              json: async () => ({ ok: true, skipped: 'stale_session', reason: 'session_id_mismatch' }),
            });
          });
        }
        return { ok: true, json: async () => ({ ok: true }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });

  const firstStartPromise = windowObj.newUserIcebreaker.start('1');
  const firstReachedContext = await waitFor(() => typeof resolveFirstContext === 'function');
  assert.equal(firstReachedContext, true);
  const firstSessionId = windowObj.newUserIcebreaker.getActiveSession()?.sessionId;
  assert.ok(firstSessionId);

  const secondStartPromise = windowObj.newUserIcebreaker.start('1');
  const replaced = await waitFor(() => {
    const active = windowObj.newUserIcebreaker.getActiveSession();
    return active && active.sessionId !== firstSessionId;
  });
  assert.equal(replaced, true);
  const replacementSession = windowObj.newUserIcebreaker.getActiveSession();
  assert.ok(replacementSession);

  resolveFirstContext();
  const [firstStarted, secondStarted] = await Promise.all([firstStartPromise, secondStartPromise]);
  assert.equal(firstStarted, false);
  assert.equal(secondStarted, true);
  assert.equal(windowObj.NekoNewUserIcebreakerState.isPeriodActive(), true);
  assert.equal(windowObj.newUserIcebreaker.getActiveSession()?.sessionId, replacementSession.sessionId);
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

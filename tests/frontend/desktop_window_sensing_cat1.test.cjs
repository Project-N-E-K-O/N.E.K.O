const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const adapterPath = path.join(
  projectRoot,
  'static',
  'app',
  'app-desktop-window-sensing.js',
);
const templatePath = path.join(projectRoot, 'templates', 'index.html');

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const bucket = this.listeners.get(type) || [];
    bucket.push(listener);
    this.listeners.set(type, bucket);
  }

  removeEventListener(type, listener) {
    const bucket = this.listeners.get(type) || [];
    this.listeners.set(type, bucket.filter(candidate => candidate !== listener));
  }

  dispatchEvent(event) {
    const bucket = this.listeners.get(event.type) || [];
    bucket.slice().forEach(listener => listener.call(this, event));
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail || {};
  }
}

function createDeferred() {
  let resolve;
  const promise = new Promise(resolvePromise => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function createRuntime(options = {}) {
  const window = new EventTargetLike();
  const observations = [];
  const starts = [];
  const stops = [];
  const subscriptions = [];
  let changedListener = null;
  let unsubscribeCount = 0;
  let now = 1000;
  let currentTier = 'cat1';

  window.NekoCatMindContract = {
    EVENT_NAMES: {
      OBSERVATION: 'neko:cat-mind:observation',
      CAT_LOCAL_ACTIVE_CHANGE: 'neko:cat-local-active-change',
    },
    OBSERVATION_TYPES: {
      DESKTOP_OCCLUSION_OR_LAYER_CHANGE: 'desktop_occlusion_or_layer_change',
    },
  };
  window.nekoCatMind = {
    getState: () => ({ active: true, tier: currentTier }),
  };

  if (options.bridge !== false) {
    window.nekoDesktopWindowSensing = {
      start() {
        starts.push(now);
        if (typeof options.start === 'function') {
          return options.start();
        }
        return Promise.resolve({
          status: 'ready',
          sessionId: `session-${starts.length}`,
          rect: { x: 10, y: 20, width: 300, height: 200 },
        });
      },
      stop(sessionId) {
        stops.push(sessionId);
        return Promise.resolve({ status: 'stopped' });
      },
      onChanged(listener) {
        changedListener = listener;
        subscriptions.push(listener);
        let active = true;
        return () => {
          if (!active) return;
          active = false;
          unsubscribeCount += 1;
          if (changedListener === listener) changedListener = null;
        };
      },
    };
  }

  window.addEventListener('neko:cat-mind:observation', event => {
    observations.push(event.detail);
  });

  const source = fs.readFileSync(adapterPath, 'utf8');
  const context = vm.createContext({
    window,
    CustomEvent: CustomEventLike,
    Date: { now: () => now },
    Promise,
    Object,
    Array,
    Number,
    Set,
    console,
  });
  vm.runInContext(source, context, {
    filename: 'static/app/app-desktop-window-sensing.js',
  });

  return {
    window,
    observations,
    starts,
    stops,
    subscriptions,
    get unsubscribeCount() {
      return unsubscribeCount;
    },
    setNow(value) {
      now = value;
    },
    setTier(value) {
      currentTier = value;
    },
    publishCatState(detail) {
      window.dispatchEvent(new CustomEventLike(
        'neko:cat-local-active-change',
        { detail },
      ));
    },
    publishTierState(detail) {
      if (detail && ['cat1', 'cat2', 'cat3'].includes(detail.tier)) {
        currentTier = detail.tier;
      }
      window.dispatchEvent(new CustomEventLike(
        'neko:auto-goodbye:state-change',
        { detail },
      ));
    },
    publishChanged(detail) {
      if (changedListener) changedListener(detail);
    },
  };
}

test('adapter is loaded after Cat Mind and before goodbye can enter CAT1', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const catMind = '/static/app/app-cat-mind.js';
  const adapter = '/static/app/app-desktop-window-sensing.js';
  const autoGoodbye = '/static/app/app-auto-goodbye.js';

  assert.ok(template.includes(adapter));
  assert.ok(template.indexOf(catMind) < template.indexOf(adapter));
  assert.ok(template.indexOf(adapter) < template.indexOf(autoGoodbye));
});

test('adapter stays idle before a real cat appearance and ignores ball appearance', async () => {
  const runtime = createRuntime();

  runtime.publishCatState({ active: false, appearance: 'cat' });
  runtime.publishCatState({ active: true, appearance: 'ball' });
  await flushPromises();

  assert.equal(runtime.starts.length, 0);
  assert.equal(runtime.subscriptions.length, 0);
  assert.equal(runtime.observations.length, 0);
});

test('one CAT1 phase owns one formal sensing session and forwards safe observations', async () => {
  const runtime = createRuntime();

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
    source: 'manual-goodbye',
  });
  await flushPromises();

  assert.equal(runtime.starts.length, 1);
  assert.equal(runtime.subscriptions.length, 1);
  assert.deepEqual(plain(runtime.observations[0]), {
    type: 'desktop_occlusion_or_layer_change',
    source: 'desktop-window-sensing',
    tier: 'cat1',
    timestamp: 1000,
    detail: {
      status: 'current',
      changes: [],
      movement: null,
      rect: { x: 10, y: 20, width: 300, height: 200 },
    },
  });

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
    source: 'duplicate-cat-active',
  });
  await flushPromises();
  assert.equal(runtime.starts.length, 1);

  runtime.setNow(2000);
  runtime.publishChanged({
    status: 'changed',
    sessionId: 'session-1',
    changes: ['identity', 'position'],
    movement: { x: 1, y: -1 },
    rect: { x: 40, y: 50, width: 320, height: 220 },
    title: 'must-not-cross',
    pid: 123,
    id: 456,
  });

  assert.deepEqual(plain(runtime.observations[1]), {
    type: 'desktop_occlusion_or_layer_change',
    source: 'desktop-window-sensing',
    tier: 'cat1',
    timestamp: 2000,
    detail: {
      status: 'changed',
      changes: ['identity', 'position'],
      movement: { x: 1, y: -1 },
      rect: { x: 40, y: 50, width: 320, height: 220 },
    },
  });
  assert.equal(JSON.stringify(runtime.observations).includes('must-not-cross'), false);
  assert.equal(JSON.stringify(runtime.observations).includes('session-1'), false);

  runtime.publishTierState({
    type: 'visual-tier',
    tier: 'cat2',
    source: 'auto-goodbye',
  });
  await flushPromises();

  assert.deepEqual(runtime.stops, ['session-1']);
  assert.equal(runtime.unsubscribeCount, 1);

  runtime.publishChanged({
    status: 'changed',
    sessionId: 'session-1',
    changes: ['size'],
    movement: null,
    rect: { x: 40, y: 50, width: 400, height: 220 },
  });
  assert.equal(runtime.observations.length, 2);

  runtime.publishTierState({
    type: 'visual-tier',
    tier: 'cat1',
    source: 'return-ball-drag-demotion',
  });
  await flushPromises();

  assert.equal(runtime.starts.length, 2);
  assert.equal(runtime.observations.length, 3);
  assert.equal(runtime.observations[2].tier, 'cat1');

  runtime.publishCatState({
    active: false,
    appearance: 'cat',
    reason: 'return-commit',
  });
  await flushPromises();

  assert.deepEqual(runtime.stops, ['session-1', 'session-2']);
  assert.equal(runtime.unsubscribeCount, 2);
});

test('leaving CAT1 stops a start result that arrives late', async () => {
  const deferred = createDeferred();
  const runtime = createRuntime({ start: () => deferred.promise });

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
  });
  runtime.publishTierState({
    type: 'visual-tier',
    tier: 'cat2',
    source: 'auto-goodbye',
  });

  deferred.resolve({
    status: 'ready',
    sessionId: 'late-session',
    rect: { x: 10, y: 20, width: 300, height: 200 },
  });
  await flushPromises();

  assert.deepEqual(runtime.stops, ['late-session']);
  assert.equal(runtime.observations.length, 0);
  assert.equal(runtime.unsubscribeCount, 1);
});

test('switching to ball or unloading the page stops the current cat session', async () => {
  const runtime = createRuntime();

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
  });
  await flushPromises();
  runtime.publishCatState({
    active: false,
    appearance: 'ball',
    reason: 'appearance-change',
  });
  await flushPromises();
  assert.deepEqual(runtime.stops, ['session-1']);

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
  });
  await flushPromises();
  runtime.window.dispatchEvent({ type: 'pagehide' });
  await flushPromises();

  assert.deepEqual(runtime.stops, ['session-1', 'session-2']);
  assert.equal(runtime.unsubscribeCount, 2);
});

test('CAT2 and CAT3 never start sensing until the visible cat returns to CAT1', async () => {
  const runtime = createRuntime();

  runtime.setTier('cat2');
  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat2',
  });
  runtime.publishTierState({
    type: 'visual-tier',
    tier: 'cat3',
    source: 'auto-goodbye',
  });
  await flushPromises();

  assert.equal(runtime.starts.length, 0);
  assert.equal(runtime.subscriptions.length, 0);

  runtime.publishTierState({
    type: 'visual-tier',
    tier: 'cat1',
    source: 'return-ball-drag-demotion',
  });
  await flushPromises();

  assert.equal(runtime.starts.length, 1);
  assert.equal(runtime.observations[0].tier, 'cat1');
});

test('ordinary web pages without the desktop bridge remain unaffected', async () => {
  const runtime = createRuntime({ bridge: false });

  runtime.publishCatState({
    active: true,
    appearance: 'cat',
    tier: 'cat1',
  });
  runtime.publishCatState({
    active: false,
    appearance: 'cat',
    reason: 'return-commit',
  });
  await flushPromises();

  assert.equal(runtime.starts.length, 0);
  assert.equal(runtime.observations.length, 0);
});

test('adapter has no reader, polling timer, DOM state or Cat Mind action producer', () => {
  const source = fs.readFileSync(adapterPath, 'utf8');

  assert.doesNotMatch(source, /\b(?:activeWindow|openWindows|get-windows)\b/);
  assert.doesNotMatch(source, /\b(?:setInterval|setTimeout)\s*\(/);
  assert.doesNotMatch(source, /\b(?:document|querySelector|getElementById)\b/);
  assert.doesNotMatch(source, /cat-mind:action-request/);
  assert.doesNotMatch(source, /nekoDesktopWindowSensing\.start\(\)/);
});

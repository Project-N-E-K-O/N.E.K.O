const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const runnerPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-edge-peek.js'
);
const coordinatorPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js'
);
const topEdgeRunnerPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-top-edge.js'
);
const templatePath = path.join(projectRoot, 'templates/index.html');
const cssPath = path.join(projectRoot, 'static/css/index.css');

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const active = force === undefined ? !this.values.has(name) : !!force;
    if (active) this.values.add(name); else this.values.delete(name);
    return active;
  }
}

function createRuntime(options = {}) {
  const listeners = new Map();
  const sharedListeners = new Set();
  const rafs = new Map();
  const timers = new Map();
  const mutationObservers = new Set();
  const gateOverrides = Object.assign({}, options.gate || {});
  let current = null;
  let now = 1000;
  let nextRaf = 1;
  let nextTimer = 1;
  let journeySyncCalls = 0;
  const runtimeMath = Object.create(Math);
  runtimeMath.random = typeof options.random === 'function' ? options.random : () => 0.5;

  const attributes = new Map([['data-neko-idle-tier', 'cat1']]);
  const containerAttributes = new Map();
  const artAttributes = new Map([['src', 'idle.gif']]);
  const art = {
    src: 'idle.gif',
    __nekoIdleHoverSrc: '',
    getAttribute(name) { return artAttributes.get(name) || ''; },
    setAttribute(name, value) {
      artAttributes.set(name, String(value));
      if (name === 'src') this.src = String(value);
    },
  };
  const catParent = { id: 'cat-presentation-layer' };
  const container = {
    id: 'live2d-return-button-container',
    isConnected: true,
    style: {
      display: 'block',
      left: `${options.catLeft ?? 200}px`,
      top: `${options.catTop ?? 300}px`,
      right: '',
      bottom: '',
      transform: 'none',
      setProperty(name, value) { this[name] = String(value); },
      removeProperty(name) { delete this[name]; },
      getPropertyValue(name) { return this[name] || ''; },
    },
    classList: new FakeClassList(),
    parentNode: catParent,
    getAttribute(name) { return containerAttributes.get(name) || null; },
    setAttribute(name, value) { containerAttributes.set(name, String(value)); },
    removeAttribute(name) { containerAttributes.delete(name); },
    getBoundingClientRect() {
      const left = Number.parseFloat(this.style.left);
      const top = Number.parseFloat(this.style.top);
      const width = options.catWidth ?? 100;
      const height = options.catHeight ?? 100;
      return { left, top, right: left + width, bottom: top + height, width, height };
    },
  };
  const button = {
    id: 'live2d-btn-return',
    isConnected: true,
    classList: new FakeClassList(),
    getAttribute(name) { return attributes.get(name) || null; },
    setAttribute(name, value) { attributes.set(name, String(value)); },
    querySelector(selector) { return selector === '.neko-idle-return-art' ? art : null; },
  };
  const shared = options.bridge === false ? null : {
    getCurrent: () => current,
    subscribe(listener) {
      sharedListeners.add(listener);
      return () => sharedListeners.delete(listener);
    },
  };
  const window = {
    innerWidth: options.innerWidth ?? 1000,
    innerHeight: options.innerHeight ?? 800,
    screenX: options.screenX ?? 100,
    screenY: options.screenY ?? 50,
    screenLeft: options.screenX ?? 100,
    screenTop: options.screenY ?? 50,
    nekoDesktopWindowSensingContext: shared || undefined,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) {
      Array.from(listeners.get(event.type) || []).forEach((listener) => listener(event));
      return true;
    },
    requestAnimationFrame(callback) {
      const id = nextRaf++;
      rafs.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { rafs.delete(id); },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, delay: Number(delay) || 0 });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    queueMicrotask(callback) { Promise.resolve().then(callback); },
    matchMedia: () => ({ matches: options.reducedMotion === true }),
  };
  const document = {
    querySelectorAll(selector) {
      return selector === '.neko-idle-return-btn' ? [button] : [];
    },
  };
  const context = {
    window,
    document,
    Math: runtimeMath,
    Object,
    Number,
    Date: { now: () => now },
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _getNekoGoodbyeIdleAppearance: () => 'cat',
    _normalizeNekoIdleReturnTier: (tier) => tier || 'cat1',
    _getNekoIdleReturnContainerFromButton: (candidate) => candidate === button ? container : null,
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _getNekoIdleCat1WalkingAssetUrl: () => 'walk.gif',
    _setNekoIdleReturnArtSource: (candidate, src) => candidate.setAttribute('src', src),
    _getActiveNekoIdleReturnTier: () => gateOverrides.tier || 'cat1',
    _isNekoIdleReturnDragActionBlocking: () => !!gateOverrides.drag,
    _isAnyNekoIdleReturnDragActionBlocking: () => false,
    _isNekoIdleReturnPending: () => !!gateOverrides.returnPending,
    _isAnyNekoIdleReturnPending: () => false,
    _isNekoIdlePresentationTransitionActive: () => !!gateOverrides.transition,
    _isNekoIdleCompactSurfaceDragging: () => !!gateOverrides.compactDrag,
    _isNekoIdleCat1EdgePeekActive: () => !!gateOverrides.screenEdgePeek,
    _isAnyNekoIdleCat1IndependentActionActive: () => !!gateOverrides.activeIndependentAction,
    _isNekoIdleCat1PositionPresentationBusy: () => !!gateOverrides.cat1PositionPresentationBusy,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _scheduleNekoIdleCat1JourneySync: () => { journeySyncCalls += 1; },
    MutationObserver: class MutationObserver {
      constructor(callback) { this.callback = callback; }
      observe() { mutationObservers.add(this); }
      disconnect() { mutationObservers.delete(this); }
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(coordinatorPath, 'utf8'), context);
  if (options.withTopEdge === true) {
    vm.runInContext(fs.readFileSync(topEdgeRunnerPath, 'utf8'), context);
  } else if (window.NekoDesktopWindowInteractions) {
    window.NekoDesktopWindowInteractions.register({
      kind: 'desktop-window-top-edge',
      priority: 0,
      handleSensingResult: () => false,
      getCandidate: () => null,
      startCandidate: () => false,
      consumeOpportunity: () => false,
      rearmOpportunity: () => false,
      isActive: () => false,
      cancel: () => false,
    });
  }
  vm.runInContext(fs.readFileSync(runnerPath, 'utf8'), context);
  const startupCallbacks = Array.from(timers.values()).map((entry) => entry.callback);
  timers.clear();
  startupCallbacks.forEach((callback) => callback());

  return {
    window,
    button,
    container,
    art,
    setShared(value) {
      current = value;
      Array.from(sharedListeners).forEach((listener) => listener(value));
    },
    async flushMicrotasks() { await Promise.resolve(); await Promise.resolve(); },
    flushRafs(limit = 240) {
      let count = 0;
      while (rafs.size && count < limit) {
        const callbacks = Array.from(rafs.values());
        rafs.clear();
        now += 48;
        callbacks.forEach((callback) => callback(now));
        count += 1;
      }
      return count;
    },
    flushTimers() {
      const callbacks = Array.from(timers.values()).map((entry) => entry.callback);
      timers.clear();
      callbacks.forEach((callback) => callback());
    },
    runNextTimer() {
      const next = timers.entries().next();
      if (next.done) return false;
      const [id, entry] = next.value;
      timers.delete(id);
      entry.callback();
      return true;
    },
    nextTimerDelay() {
      const next = timers.values().next();
      return next.done ? null : next.value.delay;
    },
    activeTimers: () => timers.size,
    journeySyncCalls: () => journeySyncCalls,
    advanceTime(durationMs) { now += Math.max(0, Number(durationMs) || 0); },
    emit(type, detail) { window.dispatchEvent({ type, detail }); },
    setGate(value) { Object.assign(gateOverrides, value); },
    setDragging(value) { container.setAttribute('data-dragging', value); },
  };
}

function sensingResult(revision, rect, extra = {}) {
  return Object.freeze({
    status: extra.status || (revision === 1 ? 'current' : 'changed'),
    sessionId: extra.sessionId || 'session-1',
    revision,
    changes: extra.changes || [],
    movement: extra.movement || null,
    rect: rect ? Object.freeze(rect) : undefined,
    timestamp: 1000 + revision,
    reason: extra.reason,
  });
}

test('edge-peek runner loads after the selector and stays independent from screen-edge state', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const source = fs.readFileSync(runnerPath, 'utf8');
  const css = fs.readFileSync(cssPath, 'utf8');
  const ownerIndex = template.indexOf('/static/app/app-desktop-window-sensing.js');
  const selectorIndex = template.indexOf('/static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js');
  const runnerIndex = template.indexOf('/static/avatar/avatar-ui-buttons/idle-desktop-window-edge-peek.js');

  assert.ok(ownerIndex >= 0 && selectorIndex > ownerIndex && runnerIndex > selectorIndex);
  assert.match(source, /TRIGGER_DISTANCE_PX\s*=\s*200/);
  assert.match(source, /EDGES\s*=\s*Object\.freeze\(\['left', 'right', 'bottom'\]\)/);
  assert.doesNotMatch(source, /_applyNekoIdleCat1EdgePeek|_clearNekoIdleCat1EdgePeek|is-cat1-edge-peek-/);
  assert.doesNotMatch(source, /nekoDesktopWindowSensing\.(?:start|stop|activeWindow|openWindows)/);
  assert.doesNotMatch(source, /subscribe\(handleSensingResult\)|desktop-window-edge-peek:terminal/);
  assert.match(source, /coordinator\.canStart/);
  assert.doesNotMatch(source, /_getNekoCatMindRuntimeGateSnapshot|NekoCatMind/);
  assert.doesNotMatch(source, /_scheduleNekoIdleCat1JourneySync|compact-surface-layout-change/);
  assert.match(source, /SIDE_VISIBLE_RATIO\s*=\s*0\.5/);
  assert.match(source, /BOTTOM_HIDDEN_RATIO\s*=\s*0\.56/);
  assert.match(source, /PEEK_CYCLE_DELAY_MIN_MS\s*=\s*6000/);
  assert.match(source, /PEEK_CYCLE_DELAY_MAX_MS\s*=\s*12000/);
  assert.doesNotMatch(source, /setInterval/);
  assert.match(css, /edge-peek-left[\s\S]*polygon\([^)]*50%/);
  assert.match(css, /edge-peek-right[\s\S]*polygon\(50%/);
  assert.match(css, /edge-peek-left[\s\S]*rotate\(-60deg\)/);
  assert.match(css, /edge-peek-right[\s\S]*rotate\(60deg\)/);
  assert.match(
    css,
    /edge-peek-left[^}]*edge-peek-right[^}]*edge-peek-bottom[^}]*scaleX\(1\)/
  );
  assert.match(
    css,
    /is-cat1-facing-right\.is-cat1-desktop-window-edge-peek-walking:not\(\.is-cat1-desktop-window-edge-peek-facing-right\)[^}]*scaleX\(1\)/
  );
  assert.match(css, /is-cat1-desktop-window-edge-peek-bottom[\s\S]*rotate\(180deg\)/);
  assert.match(css, /is-cat1-desktop-window-edge-peeking[\s\S]*pointer-events:\s*none/);
  assert.match(css, /edge-peek-left[^}]*58%[^}]*translate3d\(-8%, 0, 0\)/);
  assert.match(css, /edge-peek-right[^}]*42%[^}]*translate3d\(8%, 0, 0\)/);
  assert.match(css, /edge-peek-bottom[^}]*48%[^}]*translate3d\(0, 8%, 0\)/);
  assert.match(css, /@keyframes nekoIdleCat1DesktopWindowEdgePeekCycle[\s\S]*46%/);
  assert.doesNotMatch(
    css,
    /\.neko-idle-return-button-container\.is-cat1-desktop-window-edge-peek-cycle-active\s*\{[^}]*animation:/
  );
  assert.doesNotMatch(source, /freezePeekCyclePosition/);
});

test('the nearest valid left, right, or bottom edge becomes the target', async () => {
  const left = createRuntime({ catLeft: 200, catTop: 300 });
  left.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await left.flushMicrotasks();
  assert.equal(left.window.NekoDesktopWindowEdgePeek.getState().edge, 'left');
  assert.equal(left.window.NekoDesktopWindowEdgePeek.getState().targetLeft, 330);

  const right = createRuntime({ catLeft: 650, catTop: 300 });
  right.setShared(sensingResult(1, { x: 400, y: 300, width: 300, height: 300 }));
  await right.flushMicrotasks();
  assert.equal(right.window.NekoDesktopWindowEdgePeek.getState().edge, 'right');
  assert.equal(right.window.NekoDesktopWindowEdgePeek.getState().targetLeft, 550);

  const bottom = createRuntime({ catLeft: 450, catTop: 480 });
  bottom.setShared(sensingResult(1, { x: 400, y: 150, width: 400, height: 300 }));
  await bottom.flushMicrotasks();
  assert.equal(bottom.window.NekoDesktopWindowEdgePeek.getState().edge, 'bottom');
  assert.equal(bottom.window.NekoDesktopWindowEdgePeek.getState().targetTop, 344);
});

test('walking direction is owned by edge-peek even when the previous generic facing remains', async () => {
  const left = createRuntime({ catLeft: 650, catTop: 300 });
  left.button.classList.add('is-cat1-facing-right');
  left.setShared(sensingResult(1, { x: 400, y: 300, width: 300, height: 300 }));
  await left.flushMicrotasks();
  assert.equal(left.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
  assert.equal(left.button.classList.contains('is-cat1-desktop-window-edge-peek-facing-right'), false);

  const right = createRuntime({ catLeft: 200, catTop: 300 });
  right.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await right.flushMicrotasks();
  assert.equal(right.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
  assert.equal(right.button.classList.contains('is-cat1-desktop-window-edge-peek-facing-right'), true);
});

test('settled edge composition ignores a previous generic facing on all three edges', async () => {
  const css = fs.readFileSync(cssPath, 'utf8');
  const genericFacingIndex = css.indexOf(
    '.neko-idle-return-btn.is-cat1-facing-right > .neko-idle-return-art'
  );
  const settledOverrideIndex = css.indexOf(
    '.neko-idle-return-btn.is-cat1-facing-right.is-cat1-desktop-window-edge-peek-left'
  );
  assert.ok(genericFacingIndex >= 0 && settledOverrideIndex > genericFacingIndex,
    'settled edge composition must override the later generic facing cascade');
  assert.match(css.slice(settledOverrideIndex),
    /is-cat1-desktop-window-edge-peek-right[\s\S]*is-cat1-desktop-window-edge-peek-bottom[\s\S]*--neko-idle-return-facing-transform:\s*scaleX\(1\)/);
  const cases = [
    { catLeft: 200, catTop: 300, rect: { x: 480, y: 300, width: 300, height: 300 }, edge: 'left' },
    { catLeft: 650, catTop: 300, rect: { x: 400, y: 300, width: 300, height: 300 }, edge: 'right' },
    { catLeft: 450, catTop: 480, rect: { x: 400, y: 150, width: 400, height: 300 }, edge: 'bottom' },
  ];
  for (const entry of cases) {
    const runtime = createRuntime(entry);
    runtime.button.classList.add('is-cat1-facing-right');
    runtime.setShared(sensingResult(1, entry.rect));
    await runtime.flushMicrotasks();
    runtime.flushRafs();
    assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
    assert.equal(runtime.button.classList.contains(`is-cat1-desktop-window-edge-peek-${entry.edge}`), true);
    assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-edge-peek-facing-right'), false);
  }
});

test('real runners choose the closest presentation and keep equal-time ownership singular', () => {
  const edgeWins = createRuntime({ catLeft: 200, catTop: 300, withTopEdge: true });
  edgeWins.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  assert.equal(edgeWins.window.NekoDesktopWindowInteractions.getState().activeKind, 'desktop-window-edge-peek');
  assert.equal(edgeWins.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
  assert.equal(edgeWins.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  const topWins = createRuntime({ catLeft: 200, catTop: 300, withTopEdge: true });
  topWins.setShared(sensingResult(1, { x: 200, y: 422, width: 300, height: 300 }));
  assert.equal(topWins.window.NekoDesktopWindowInteractions.getState().activeKind, 'desktop-window-top-edge');
  assert.equal(topWins.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'perched');
  assert.equal(topWins.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
});

test('after a completed drop and 30 seconds both real runners compare the new fact again', () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300, withTopEdge: true });
  runtime.setShared(sensingResult(1, { x: 200, y: 422, width: 300, height: 300 }));
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'perched');

  runtime.setShared(sensingResult(2, { x: 200, y: 422, width: 320, height: 300 }, {
    changes: ['size'],
  }));
  runtime.flushTimers();
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().activeKind, '');

  runtime.setShared(sensingResult(3, { x: 480, y: 300, width: 300, height: 300 }, {
    changes: ['identity'],
  }));
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().activeKind, '');

  runtime.advanceTime(30000);
  runtime.setShared(sensingResult(4, { x: 480, y: 300, width: 300, height: 300 }, {
    status: 'current',
  }));
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().activeKind, 'desktop-window-edge-peek');
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
});

test('200px starts while 201px and unusable outside space do not', async () => {
  const atThreshold = createRuntime({ catLeft: 130, catTop: 300 });
  atThreshold.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await atThreshold.flushMicrotasks();
  assert.equal(atThreshold.window.NekoDesktopWindowEdgePeek.getState().distancePx, 200);

  const outside = createRuntime({ catLeft: 129, catTop: 300 });
  outside.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await outside.flushMicrotasks();
  assert.equal(outside.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  const noOutsideRoom = createRuntime({ catLeft: 400, catTop: 500 });
  noOutsideRoom.setShared(sensingResult(1, { x: 100, y: 350, width: 1000, height: 500 }));
  await noOutsideRoom.flushMicrotasks();
  assert.equal(noOutsideRoom.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
});

test('a stable current fact retries edge-peek after renderer occupancy clears', async () => {
  const runtime = createRuntime({
    catLeft: 200,
    catTop: 300,
    gate: { activeIndependentAction: true },
  });
  const rect = { x: 480, y: 300, width: 300, height: 300 };

  runtime.setShared(sensingResult(1, rect));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  runtime.setGate({ activeIndependentAction: false });
  runtime.setShared(sensingResult(2, rect, { status: 'current' }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
});

test('a stable current fact starts edge-peek after the cat itself moves into range', async () => {
  const runtime = createRuntime({ catLeft: 0, catTop: 300 });
  const rect = { x: 480, y: 300, width: 300, height: 300 };

  runtime.setShared(sensingResult(1, rect));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  runtime.container.style.left = '200px';
  runtime.setShared(sensingResult(2, rect, { status: 'current' }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
});

test('arrival applies one independent crop presentation and a short cue', async () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300 });
  const rect = { x: 480, y: 300, width: 300, height: 300 };
  runtime.setShared(sensingResult(1, rect));
  await runtime.flushMicrotasks();
  assert.equal(runtime.art.src, 'walk.gif');
  runtime.flushRafs();

  const state = runtime.window.NekoDesktopWindowEdgePeek.getState();
  assert.equal(state.phase, 'peeking');
  assert.equal(state.edge, 'left');
  assert.equal(runtime.art.src, 'idle.gif');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-edge-peek-left'), true);
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peeking'), true);
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cue-active'), true);
  assert.equal(runtime.activeTimers(), 1);

  runtime.setShared(sensingResult(2, rect, { status: 'current' }));
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
  assert.equal(runtime.activeTimers(), 1);

  runtime.flushTimers();
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cue-active'), false);
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
});

test('all three edges occasionally perform one short out-and-back animation without moving the owner', () => {
  const cases = [
    { edge: 'left', catLeft: 200, catTop: 300, rect: { x: 480, y: 300, width: 300, height: 300 } },
    { edge: 'right', catLeft: 650, catTop: 300, rect: { x: 400, y: 300, width: 300, height: 300 } },
    { edge: 'bottom', catLeft: 450, catTop: 480, rect: { x: 400, y: 150, width: 400, height: 300 } },
  ];

  cases.forEach(({ edge, catLeft, catTop, rect }) => {
    const runtime = createRuntime({ catLeft, catTop });
    runtime.setShared(sensingResult(1, rect));
    runtime.flushRafs();
    const settledLeft = runtime.container.style.left;
    const settledTop = runtime.container.style.top;

    assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().edge, edge);
    assert.equal(runtime.nextTimerDelay(), 700);
    runtime.runNextTimer();
    assert.equal(runtime.nextTimerDelay(), 9000);
    assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), false);

    runtime.runNextTimer();
    assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), true);
    assert.equal(
      runtime.container.style.getPropertyValue('--neko-desktop-window-edge-peek-cycle-duration'),
      '850ms'
    );
    assert.equal(runtime.container.style.left, settledLeft);
    assert.equal(runtime.container.style.top, settledTop);
    assert.equal(runtime.nextTimerDelay(), 850);
    assert.equal(runtime.activeTimers(), 1);

    runtime.runNextTimer();
    assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), false);
    assert.equal(
      runtime.container.style.getPropertyValue('--neko-desktop-window-edge-peek-cycle-duration'),
      ''
    );
    assert.equal(runtime.container.style.left, settledLeft);
    assert.equal(runtime.container.style.top, settledTop);
    assert.equal(runtime.nextTimerDelay(), 9000);
    assert.equal(runtime.activeTimers(), 1);
    assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
  });
});

test('reduced motion keeps the settled peek static without scheduling a cycle', () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300, reducedMotion: true });
  runtime.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  runtime.flushRafs();
  runtime.runNextTimer();

  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), false);
  assert.equal(runtime.activeTimers(), 0);
});

test('window change interrupts an active peek cycle and leaves only once', () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300 });
  runtime.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  runtime.flushRafs();
  runtime.runNextTimer();
  runtime.runNextTimer();
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), true);

  runtime.setShared(sensingResult(2, { x: 490, y: 300, width: 300, height: 300 }, {
    changes: ['position'],
    movement: { x: 1, y: 0 },
  }));
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'leaving');
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), false);
  assert.equal(runtime.activeTimers(), 1);

  runtime.setShared(sensingResult(3, { x: 500, y: 300, width: 300, height: 300 }, {
    changes: ['position'],
    movement: { x: 1, y: 0 },
  }));
  assert.equal(runtime.activeTimers(), 1);
  runtime.runNextTimer();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(runtime.activeTimers(), 0);
});

test('window changes cancel walking or make a peeking cat leave once with event-driven cooldown', async () => {
  const walking = createRuntime({ catLeft: 200, catTop: 300 });
  walking.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await walking.flushMicrotasks();
  walking.setShared(sensingResult(2, { x: 490, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  assert.equal(walking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(walking.journeySyncCalls(), 0);
  assert.equal(walking.activeTimers(), 0);

  const peeking = createRuntime({ catLeft: 200, catTop: 300 });
  peeking.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await peeking.flushMicrotasks();
  peeking.flushRafs();
  peeking.setShared(sensingResult(2, { x: 490, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'leaving');
  assert.equal(peeking.container.style.top, '352px');
  assert.equal(peeking.activeTimers(), 1);
  peeking.setShared(sensingResult(3, { x: 490, y: 300, width: 300, height: 300 }, { status: 'current' }));
  assert.equal(peeking.activeTimers(), 1);
  peeking.flushTimers();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(peeking.journeySyncCalls(), 0);

  peeking.setShared(sensingResult(4, { x: 490, y: 300, width: 300, height: 300 }, { status: 'current' }));
  await peeking.flushMicrotasks();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  peeking.advanceTime(30000);
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  peeking.setShared(sensingResult(5, { x: 490, y: 300, width: 300, height: 300 }, { status: 'current' }));
  await peeking.flushMicrotasks();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.isActive(peeking.button), true);
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
});

test('pending gate blocks start while pointer drag-start immediately reveals the whole cat', async () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300 });
  runtime.setDragging('pending');
  runtime.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  runtime.setDragging('false');
  runtime.setShared(sensingResult(2, { x: 480, y: 300, width: 300, height: 300 }, { changes: ['size'] }));
  await runtime.flushMicrotasks();
  runtime.flushRafs();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
  runtime.runNextTimer();
  runtime.runNextTimer();
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), true);
  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
    container: runtime.container,
  });
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peeking'), false);
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cycle-active'), false);
  assert.equal(runtime.activeTimers(), 0);
  assert.equal(runtime.journeySyncCalls(), 0);

  runtime.setShared(sensingResult(3, { x: 480, y: 300, width: 300, height: 300 }, {
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  runtime.setDragging('true');
  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-active',
    container: runtime.container,
  });
  runtime.setDragging('false');
  runtime.setShared(sensingResult(4, { x: 480, y: 300, width: 300, height: 300 }, {
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  runtime.advanceTime(30000);
  runtime.setShared(sensingResult(5, { x: 480, y: 300, width: 300, height: 300 }, {
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.isActive(runtime.button), true);

  const cleared = createRuntime({ catLeft: 200, catTop: 300 });
  cleared.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await cleared.flushMicrotasks();
  cleared.flushRafs();
  cleared.setShared(null);
  assert.equal(cleared.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(cleared.activeTimers(), 0);
  assert.equal(cleared.journeySyncCalls(), 0);
});

test('a peek drag press without real movement does not start the chat journey', async () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300 });
  runtime.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await runtime.flushMicrotasks();
  runtime.flushRafs();
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
    container: runtime.container,
  });
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(runtime.journeySyncCalls(), 0);

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-cancel',
    container: runtime.container,
  });
  assert.equal(runtime.journeySyncCalls(), 0);
});

test('ordinary web pages never expose the desktop edge-peek runner', () => {
  const runtime = createRuntime({ bridge: false });
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek, undefined);
});

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
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    queueMicrotask(callback) { Promise.resolve().then(callback); },
    matchMedia: () => ({ matches: false }),
  };
  const document = {
    querySelectorAll(selector) {
      return selector === '.neko-idle-return-btn' ? [button] : [];
    },
  };
  const context = {
    window,
    document,
    Math,
    Object,
    Number,
    Date: { now: () => now },
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _getNekoGoodbyeIdleAppearance: () => 'cat',
    _getNekoIdleReturnContainerFromButton: (candidate) => candidate === button ? container : null,
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _getNekoIdleCat1WalkingAssetUrl: () => 'walk.gif',
    _setNekoIdleReturnArtSource: (candidate, src) => candidate.setAttribute('src', src),
    _getNekoCatMindRuntimeGateSnapshot: () => Object.assign({
      validCatRuntime: true,
      tier: 'cat1',
      returnPending: false,
      dragPending: false,
      dragging: false,
      edgePeekActive: false,
      transitionActive: false,
      activeIndependentAction: false,
      cat1PositionPresentationBusy: false,
      returnBallVisible: true,
      chatSurfaceDragging: false,
      yarnDragActive: false,
      yarnSettling: false,
    }, gateOverrides),
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
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
  const startupCallbacks = Array.from(timers.values());
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
      const callbacks = Array.from(timers.values());
      timers.clear();
      callbacks.forEach((callback) => callback());
    },
    activeTimers: () => timers.size,
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
  assert.match(source, /SIDE_VISIBLE_RATIO\s*=\s*0\.5/);
  assert.match(source, /BOTTOM_HIDDEN_RATIO\s*=\s*0\.6/);
  assert.match(css, /edge-peek-left[\s\S]*polygon\([^)]*50%/);
  assert.match(css, /edge-peek-right[\s\S]*polygon\(50%/);
  assert.match(css, /edge-peek-left[\s\S]*rotate\(-60deg\)/);
  assert.match(css, /edge-peek-right[\s\S]*rotate\(60deg\)/);
  assert.match(css, /is-cat1-desktop-window-edge-peek-bottom[\s\S]*rotate\(180deg\)/);
  assert.match(css, /is-cat1-desktop-window-edge-peeking[\s\S]*pointer-events:\s*none/);
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
  assert.equal(bottom.window.NekoDesktopWindowEdgePeek.getState().targetTop, 340);
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
  runtime.setShared(sensingResult(4, { x: 490, y: 300, width: 300, height: 300 }, {
    changes: ['position'],
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

test('arrival applies one independent crop presentation and a short cue', async () => {
  const runtime = createRuntime({ catLeft: 200, catTop: 300 });
  runtime.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
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

  runtime.flushTimers();
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peek-cue-active'), false);
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
});

test('window changes cancel walking or make a peeking cat leave once with event-driven cooldown', async () => {
  const walking = createRuntime({ catLeft: 200, catTop: 300 });
  walking.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await walking.flushMicrotasks();
  walking.setShared(sensingResult(2, { x: 490, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  assert.equal(walking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(walking.activeTimers(), 0);

  const peeking = createRuntime({ catLeft: 200, catTop: 300 });
  peeking.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await peeking.flushMicrotasks();
  peeking.flushRafs();
  peeking.setShared(sensingResult(2, { x: 490, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'leaving');
  assert.equal(peeking.container.style.top, '352px');
  assert.equal(peeking.activeTimers(), 1);
  peeking.setShared(sensingResult(3, { x: 500, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  assert.equal(peeking.activeTimers(), 1);
  peeking.flushTimers();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');

  peeking.setShared(sensingResult(4, { x: 510, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  await peeking.flushMicrotasks();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  peeking.advanceTime(30000);
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  peeking.setShared(sensingResult(5, { x: 520, y: 300, width: 300, height: 300 }, { changes: ['position'] }));
  await peeking.flushMicrotasks();
  assert.equal(peeking.window.NekoDesktopWindowEdgePeek.getState().phase, 'walking');
});

test('pending drag blocks start while real drag and owner clear remove the presentation', async () => {
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
  runtime.setDragging('pending');
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'peeking');
  runtime.setDragging('true');
  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-active',
    container: runtime.container,
  });
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(runtime.container.classList.contains('is-cat1-desktop-window-edge-peeking'), false);

  const cleared = createRuntime({ catLeft: 200, catTop: 300 });
  cleared.setShared(sensingResult(1, { x: 480, y: 300, width: 300, height: 300 }));
  await cleared.flushMicrotasks();
  cleared.flushRafs();
  cleared.setShared(null);
  assert.equal(cleared.window.NekoDesktopWindowEdgePeek.getState().phase, 'idle');
  assert.equal(cleared.activeTimers(), 0);
});

test('ordinary web pages never expose the desktop edge-peek runner', () => {
  const runtime = createRuntime({ bridge: false });
  assert.equal(runtime.window.NekoDesktopWindowEdgePeek, undefined);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const runnerPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-top-edge.js'
);
const templatePath = path.join(projectRoot, 'templates/index.html');
const methodsReturnPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/methods-return.js'
);

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
  const terminalEvents = [];
  const mutationObservers = new Set();
  const observerRecords = [];
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
    style: {
      display: 'block',
      left: `${options.catLeft ?? 200}px`,
      top: `${options.catTop ?? 200}px`,
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
    devicePixelRatio: options.devicePixelRatio ?? 2,
    nekoDesktopWindowSensingContext: shared || undefined,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) {
      if (event.type === 'neko:desktop-window-top-edge:terminal') {
        terminalEvents.push(event.detail);
      }
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
    documentElement: {},
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
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _getNekoGoodbyeIdleAppearance: () => 'cat',
    _normalizeNekoIdleReturnTier: (tier) => tier || 'cat1',
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
      observe(target, observeOptions) {
        mutationObservers.add(this);
        observerRecords.push({ target, options: Object.assign({}, observeOptions) });
      }
      disconnect() { mutationObservers.delete(this); }
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(runnerPath, 'utf8'), context);

  return {
    window,
    document,
    button,
    container,
    catParent,
    art,
    terminalEvents,
    observerRecords,
    setShared(value) {
      current = value;
      Array.from(sharedListeners).forEach((listener) => listener(value));
    },
    async flushMicrotasks() { await Promise.resolve(); await Promise.resolve(); },
    advanceTime(durationMs) { now += Math.max(0, Number(durationMs) || 0); },
    flushRafs(limit = 200) {
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
    emit(type, detail) { window.dispatchEvent({ type, detail }); },
    setGate(value) { Object.assign(gateOverrides, value); },
    notifyMutation() {
      Array.from(mutationObservers).forEach((observer) => observer.callback([]));
    },
    removeCatContainer() {
      button.isConnected = false;
      container.isConnected = false;
      this.notifyMutation();
    },
    activeTimers: () => timers.size,
  };
}

function sensingResult(revision, rect, extra = {}) {
  return Object.freeze({
    status: extra.status || (revision === 1 ? 'current' : 'changed'),
    sessionId: extra.sessionId || 'session-1',
    revision,
    changes: extra.changes || [],
    movement: null,
    rect: rect ? Object.freeze(rect) : undefined,
    timestamp: 1000 + revision,
    reason: extra.reason,
  });
}

test('desktop runner is loaded after the sole sensing owner and stays independent from compact', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const source = fs.readFileSync(runnerPath, 'utf8');
  const returnSource = fs.readFileSync(methodsReturnPath, 'utf8');
  const ownerIndex = template.indexOf('/static/app/app-desktop-window-sensing.js');
  const runnerIndex = template.indexOf('/static/avatar/avatar-ui-buttons/idle-desktop-window-top-edge.js');

  assert.ok(ownerIndex >= 0 && runnerIndex > ownerIndex);
  assert.match(source, /TRIGGER_DISTANCE_PX\s*=\s*200/);
  assert.doesNotMatch(source, /_stepNekoIdleCat1Walk|_finishNekoIdleCat1CompactTopEdgeWalk|_dropNekoIdleCat1FromCompactTopEdge/);
  assert.doesNotMatch(source, /COMPACT_TOP_EDGE_FOLLOW_DISTANCE|compact-mirror|compactTopEdgeRearm|is-cat1-on-compact-top-edge/);
  assert.doesNotMatch(source, /nekoDesktopWindowSensing\.(?:start|stop|activeWindow|openWindows)/);
  assert.doesNotMatch(source, /__nekoIdleReturnSubactionState|__nekoIdleCat1Journey|_scheduleNekoIdleCat1JourneySync/);
  assert.doesNotMatch(source, /yarn-gate-released|compact-surface-layout-change|neko:cat-mind:action-result|attributeFilter/);
  assert.match(returnSource, /NekoDesktopWindowTopEdgePerch[\s\S]*return-click/);
});

test('200px center distance starts, 201px does not, and desktop coordinates ignore DPR', async () => {
  const atThreshold = createRuntime({ catTop: 200, devicePixelRatio: 3 });
  atThreshold.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await atThreshold.flushMicrotasks();
  assert.equal(atThreshold.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
  assert.equal(atThreshold.window.NekoDesktopWindowTopEdgePerch.getState().distancePx, 200);

  const outside = createRuntime({ catTop: 201, devicePixelRatio: 0.75 });
  outside.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await outside.flushMicrotasks();
  assert.equal(outside.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(outside.container.style.top, '201px');
});

test('negative desktop origins convert to local coordinates and unusable top edges are rejected', async () => {
  const negativeOrigin = createRuntime({ screenX: -1000, screenY: -500, catLeft: 200, catTop: 200 });
  negativeOrigin.setShared(sensingResult(1, { x: -900, y: -428, width: 400, height: 300 }));
  await negativeOrigin.flushMicrotasks();
  const state = negativeOrigin.window.NekoDesktopWindowTopEdgePerch.getState();
  assert.equal(state.phase, 'walking');
  assert.equal(state.targetLeft, 200);
  assert.equal(state.targetTop, 0);

  const tooHigh = createRuntime({ catTop: 100 });
  tooHigh.setShared(sensingResult(1, { x: 200, y: 80, width: 400, height: 300 }));
  await tooHigh.flushMicrotasks();
  assert.equal(tooHigh.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  const tooNarrow = createRuntime({ catTop: 200 });
  tooNarrow.setShared(sensingResult(1, { x: 200, y: 122, width: 120, height: 300 }));
  await tooNarrow.flushMicrotasks();
  assert.equal(tooNarrow.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
});

test('accepted action walks the real container once and perches without following', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.art.src, 'walk.gif');

  runtime.flushRafs();
  const perched = runtime.window.NekoDesktopWindowTopEdgePerch.getState();
  assert.equal(perched.phase, 'perched');
  assert.equal(runtime.container.style.left, '200px');
  assert.equal(runtime.container.style.top, '0px');
  assert.equal(runtime.art.src, 'idle.gif');
  assert.equal(runtime.terminalEvents.filter((item) => item.phase === 'perched').length, 1);
  assert.ok(runtime.terminalEvents[0].activityId);
  assert.ok(runtime.terminalEvents[0].pathDistancePx > 0);
});

test('one target episode cancels or drops once without restarting on later geometry revisions', async () => {
  const walking = createRuntime();
  walking.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await walking.flushMicrotasks();
  walking.setShared(sensingResult(2, { x: 220, y: 122, width: 400, height: 300 }, { changes: ['position'] }));
  assert.equal(walking.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(walking.activeTimers(), 0);
  assert.equal(walking.container.classList.contains('is-cat1-desktop-window-top-edge-dropping'), false);
  walking.setShared(sensingResult(3, { x: 200, y: 122, width: 400, height: 300 }, { changes: ['position'] }));
  await walking.flushMicrotasks();
  assert.equal(walking.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(walking.window.NekoDesktopWindowTopEdgePerch.isActive(walking.button), false);

  const perched = createRuntime();
  perched.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await perched.flushMicrotasks();
  perched.flushRafs();
  perched.setShared(sensingResult(2, { x: 200, y: 122, width: 420, height: 300 }, { changes: ['size'] }));
  assert.equal(perched.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'dropping');
  assert.equal(perched.container.style.top, '52px');
  assert.equal(perched.activeTimers(), 1);
  perched.setShared(sensingResult(3, { x: 200, y: 122, width: 440, height: 300 }, { changes: ['size'] }));
  assert.equal(perched.activeTimers(), 1);
  perched.setShared(sensingResult(4, { x: 240, y: 122, width: 440, height: 300 }, { changes: ['identity'] }));
  perched.setShared(sensingResult(5, null, { status: 'unavailable', reason: 'no-window' }));
  assert.equal(perched.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'dropping');
  assert.equal(perched.activeTimers(), 1);
  perched.flushTimers();
  assert.equal(perched.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(perched.terminalEvents.filter((item) => item.phase === 'dropped').length, 1);
  perched.setShared(sensingResult(6, { x: 200, y: 122, width: 460, height: 300 }, { changes: ['size'] }));
  await perched.flushMicrotasks();
  assert.equal(perched.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(perched.activeTimers(), 0);
});

test('a replacement window gets a fresh episode after cancelling the previous walk', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');

  runtime.setShared(sensingResult(2, { x: 240, y: 122, width: 400, height: 300 }, {
    changes: ['identity'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(3, { x: 220, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
});

test('availability recovery creates a fresh target episode after a cancelled walk', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();

  runtime.setShared(sensingResult(2, null, {
    status: 'unavailable',
    reason: 'no-window',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(3, { x: 220, y: 122, width: 400, height: 300 }, {
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
});

test('identity replacement and unavailable make a perched cat drop once without chasing', async () => {
  for (const replacement of [
    sensingResult(2, { x: 250, y: 122, width: 400, height: 300 }, { changes: ['identity'] }),
    sensingResult(2, null, { status: 'unavailable', reason: 'no-window' }),
  ]) {
    const runtime = createRuntime();
    runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
    await runtime.flushMicrotasks();
    runtime.flushRafs();
    runtime.setShared(replacement);
    await runtime.flushMicrotasks();
    assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'dropping');
    assert.equal(runtime.container.style.top, '52px');
    assert.equal(runtime.activeTimers(), 1);
    runtime.flushTimers();
    assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
    assert.equal(runtime.terminalEvents.filter((item) => item.phase === 'dropped').length, 1);
    runtime.setShared(sensingResult(3, { x: 200, y: 122, width: 400, height: 300 }, {
      status: 'current',
    }));
    await runtime.flushMicrotasks();
    assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.isActive(runtime.button), false);
    assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  }
});

test('a completed drop starts a 30-second event-driven action cooldown', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  runtime.flushRafs();
  runtime.setShared(sensingResult(2, { x: 220, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  runtime.flushTimers();

  runtime.setShared(sensingResult(3, { x: 210, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.advanceTime(30000);
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(4, { x: 200, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
});

test('a new sensing session rearms once, while stale revisions cannot reopen an episode', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  runtime.setShared(sensingResult(2, { x: 220, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }, {
    sessionId: 'session-2',
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
  runtime.setShared(sensingResult(1, { x: 260, y: 122, width: 400, height: 300 }, {
    sessionId: 'session-2',
    status: 'changed',
    changes: ['position'],
  }));
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
});

test('the first usable target after initial unavailable can start exactly once', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, null, {
    status: 'unavailable',
    reason: 'no-window',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(2, { x: 200, y: 122, width: 400, height: 300 }, {
    status: 'current',
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
  runtime.setShared(sensingResult(3, { x: 220, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  runtime.setShared(sensingResult(4, { x: 200, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
});

test('an unconsumed idle target may be replaced by a new native-window candidate', async () => {
  const runtime = createRuntime({ catTop: 500 });
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  runtime.setShared(sensingResult(2, { x: 200, y: 422, width: 400, height: 300 }, {
    changes: ['identity'],
  }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');
});

test('runner waits for the existing independent-action and settled-journey gates', async () => {
  const busy = createRuntime({ gate: { activeIndependentAction: true } });
  busy.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await busy.flushMicrotasks();
  assert.equal(busy.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  busy.setGate({ activeIndependentAction: false });
  await busy.flushMicrotasks();
  assert.equal(busy.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  busy.setShared(sensingResult(2, { x: 200, y: 122, width: 400, height: 300 }, {
    changes: ['position'],
  }));
  await busy.flushMicrotasks();
  assert.equal(busy.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'walking');

  const compact = createRuntime({
    gate: { cat1PositionPresentationBusy: true },
  });
  compact.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await compact.flushMicrotasks();
  assert.equal(compact.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
});

test('perched container removal releases the gate and all owned DOM references', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  runtime.flushRafs();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.isActive(runtime.button), true);

  runtime.removeCatContainer();
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.isActive(runtime.button), false);
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
});

test('pending drag does not interrupt; real drag, owner clear, return and unload do', async () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  runtime.flushRafs();

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start', container: runtime.container,
  });
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'perched');
  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-active', container: runtime.container,
  });
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  const returned = createRuntime();
  returned.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await returned.flushMicrotasks();
  returned.flushRafs();
  assert.equal(returned.window.NekoDesktopWindowTopEdgePerch.cancel(returned.button, {
    reason: 'return-click', restoreArt: false,
  }), true);
  assert.equal(returned.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');

  const tier = createRuntime();
  tier.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await tier.flushMicrotasks();
  const beforeOwnerClearTop = tier.container.style.top;
  tier.setShared(null);
  assert.equal(tier.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(tier.container.style.top, beforeOwnerClearTop);
  assert.equal(tier.activeTimers(), 0);

  const goodbye = createRuntime();
  goodbye.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await goodbye.flushMicrotasks();
  goodbye.setShared(null);
  assert.equal(goodbye.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.ok(goodbye.window.NekoDesktopWindowTopEdgePerch, 'session cleanup must not dispose future CAT1 cycles');

  const leavingDuringDrop = createRuntime();
  leavingDuringDrop.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await leavingDuringDrop.flushMicrotasks();
  leavingDuringDrop.flushRafs();
  leavingDuringDrop.setShared(sensingResult(2, { x: 200, y: 122, width: 420, height: 300 }, {
    changes: ['size'],
  }));
  assert.equal(leavingDuringDrop.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'dropping');
  leavingDuringDrop.setShared(null);
  assert.equal(leavingDuringDrop.window.NekoDesktopWindowTopEdgePerch.getState().phase, 'idle');
  assert.equal(leavingDuringDrop.activeTimers(), 0);
  assert.equal(leavingDuringDrop.terminalEvents.filter((item) => item.phase === 'dropped').length, 0);

  const unload = createRuntime();
  unload.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await unload.flushMicrotasks();
  unload.emit('pagehide');
  assert.equal('NekoDesktopWindowTopEdgePerch' in unload.window, false);
});

test('ordinary web pages never expose the desktop-only runner', () => {
  const runtime = createRuntime({ bridge: false });
  assert.equal(runtime.window.NekoDesktopWindowTopEdgePerch, undefined);
  assert.equal(runtime.observerRecords.length, 0);
});

test('the runner observes only active-container removal, not presentation attributes', async () => {
  const runtime = createRuntime();
  assert.equal(runtime.observerRecords.length, 0);
  runtime.setShared(sensingResult(1, { x: 200, y: 122, width: 400, height: 300 }));
  await runtime.flushMicrotasks();
  assert.equal(runtime.observerRecords.length, 1);
  assert.equal(runtime.observerRecords[0].target, runtime.catParent);
  assert.deepEqual(runtime.observerRecords[0].options, { childList: true });
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.NEKO_TEST_SOURCE_ROOT || path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/app/app-react-chat-window/minimize-and-idle-dock.js'), 'utf8');
const resizeSource = fs.readFileSync(path.join(root, 'static/app/app-react-chat-window/resize-drag-and-api.js'), 'utf8');

function classes() {
  const names = new Set();
  return { add: (...values) => values.forEach(value => names.add(value)),
    remove: (...values) => values.forEach(value => names.delete(value)),
    toggle(value, enabled) { if (enabled) names.add(value); else names.delete(value); },
    contains: value => names.has(value) };
}

// Run the complete dock controller and its actual tier/appearance listeners.
// DOM layout, animation delivery and the existing surface-mode API are the
// controlled host boundaries; dock admission and restoration stay production.
function runtime({ minimized = true, avatarGate = false } = {}) {
  const listeners = new Map(), timers = new Map(), observers = [], requests = [];
  const cat = {}, icon = {}, container = {
    getBoundingClientRect: () => ({ left: 500, top: 400, width: 96, height: 96 }),
  };
  const shell = {
    style: { left: '100px', top: '200px', removeProperty(name) { delete this[name]; } },
    classList: classes(), querySelector: () => icon,
    getBoundingClientRect() {
      return { left: parseFloat(this.style.left), top: parseFloat(this.style.top), width: 51, height: 51 };
    },
  };
  const I = {
    IDLE_DOCK_TIER_NONE: 'none', IDLE_DOCK_TIER_CAT2: 'cat2', IDLE_DOCK_TIER_CAT3: 'cat3',
    HOME_IDLE_DOCK_GAP: 12, idleDockTier: 'cat2', minimized,
    state: { chatSurfaceMode: minimized ? 'minimized' : 'compact' },
    lastRestorableChatSurfaceMode: 'compact', idleCatPlaygroundYarnSkinActive: false,
    isElectronChatWindow: () => false, getShell: () => shell, getOverlay: () => ({ hidden: false }),
    coerceChatSurfaceModeForHost: value => value,
    getCurrentChatSurfaceMode: () => I.state.chatSurfaceMode,
    getNextChatSurfaceMode: () => 'compact',
    setGoodbyeComposerHidden() {},
    applyPosition(left, top) { shell.style.left = `${left}px`; shell.style.top = `${top}px`; },
    CHAT_MINIMIZED_BALL_ICON_SRC: '/normal.png',
    CHAT_MINIMIZED_BALL_ICON_SRCSET: '', CHAT_MINIMIZED_BALL_PLAYGROUND_ICON_SRC: '/playground.png',
  };
  const window = {
    __appReactChatWindowParts: I, innerWidth: 1280, innerHeight: 720,
    addEventListener: (name, callback) => listeners.set(name, callback),
    setTimeout(callback) { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    nekoAutoGoodbye: { getState: () => ({ visualTier: 'cat2' }) },
  };
  const context = vm.createContext({ window, I, document: {
    querySelector: () => container, querySelectorAll: () => [cat],
  }, MutationObserver: class {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
    observe() {} disconnect() { this.disconnected = true; }
  }, console, setTimeout: window.setTimeout, clearTimeout: window.clearTimeout });
  if (avatarGate) context._isAnyNekoIdleCat1PlaygroundDropActive = () =>
    !!(cat.__nekoIdleCat1PlaygroundPendingEntry || cat.__nekoIdleCat1PlaygroundDropState?.active);
  vm.runInContext(source, context);
  // The shell animation belongs to the chat host. Delay its completion so the
  // existing observer/fallback can race the Playground takeover deterministically.
  I.setChatSurfaceMode = mode => { requests.push(mode); return mode; };
  I.syncChatSurfaceModeUI = () => {};
  const first = resizeSource.indexOf("        window.addEventListener('neko:auto-goodbye:state-change'");
  const last = resizeSource.indexOf("        window.addEventListener('neko:idle-return-ball-state'", first);
  assert.ok(first >= 0 && last > first);
  vm.runInContext(resizeSource.slice(first, last), context);
  return { I, cat, shell, requests, observers, timers,
    emit(name, detail) { listeners.get(name)({ detail }); },
    runTimers() { for (const callback of [...timers.values()]) callback(); timers.clear(); },
    position: () => [shell.style.left, shell.style.top],
  };
}

for (const avatarGate of [false, true]) {
  for (const phase of ['pending', 'active', 'teardown']) {
    test(`${avatarGate ? 'avatar gate' : 'lifecycle fallback'}: ${phase} blocks direct and tier-driven dock`, () => {
      const r = runtime({ avatarGate });
      if (phase === 'pending') r.cat.__nekoIdleCat1PlaygroundPendingEntry = {};
      else r.cat.__nekoIdleCat1PlaygroundDropState = { active: true, released: phase === 'teardown' };
      const position = r.position();
      r.I.enterIdleDock();
      r.emit('neko:auto-goodbye:state-change', { type: 'visual-tier', tier: 'cat3' });
      r.emit('neko:goodbye-idle-appearance', { mode: 'cat' });
      assert.deepEqual(r.position(), position);
      assert.equal(r.I.idleDockActive, false);
      assert.equal(r.I.idleDockTriggeredMinimize, false);
      assert.equal(r.I.idleDockSavedPosition, null);
      assert.deepEqual(r.requests, []);
    });
  }
}

test('late dock observer and fallback cannot move or overwrite a Playground minimize transaction', () => {
  const r = runtime({ minimized: false });
  r.I.enterIdleDock();
  assert.deepEqual(r.requests, ['minimized']);
  const observer = r.observers[0]; assert.ok(observer);
  r.cat.__nekoIdleCat1PlaygroundPendingEntry = {};
  const ownedSurface = { mode: 'minimized', playgroundEntry: true, transitionOptions: {} };
  r.I.pendingChatSurfaceMode = ownedSurface;
  r.I.isMinimizeTransitioning = true;
  r.shell.classList.add('is-minimized');
  observer.callback(); r.runTimers();
  assert.equal(observer.disconnected, true);
  assert.equal(r.I.pendingChatSurfaceMode, ownedSurface);
  assert.equal(r.I.isMinimizeTransitioning, true);
  assert.equal(r.I.minimized, false, 'old fallback cannot commit the newer surface transaction');
  assert.deepEqual(r.position(), ['100px', '200px']);
  assert.deepEqual(r.requests, ['minimized']);
  assert.equal(r.I.idleDockActive, false);
  assert.equal(r.I.idleDockTriggeredMinimize, false);
});

test('Playground takeover discards old dock restore coordinates without expanding or moving chat', () => {
  const r = runtime(); r.I.enterIdleDock();
  assert.equal(r.I.idleDockActive, true);
  const position = r.position();
  r.cat.__nekoIdleCat1PlaygroundDropState = { active: true };
  r.I._handleIdleCat1PlaygroundState({ detail: { active: true, yarnEnabled: true } });
  r.I.exitIdleDock();
  assert.deepEqual(r.position(), position);
  assert.deepEqual(r.requests, []);
  assert.equal(r.I.idleDockSavedPosition, null);
  assert.equal(r.shell.classList.contains('is-idle-docked'), false);
});

for (const avatarGate of [false, true]) {
  test(`received Playground yarn lifecycle blocks dock with ${avatarGate ? 'empty' : 'absent'} avatar renderer`, () => {
    const r = runtime({ avatarGate }); r.I.idleCatPlaygroundYarnSkinActive = true;
    r.I.enterIdleDock();
    assert.equal(r.I.idleDockActive, false);
    assert.deepEqual(r.requests, []);
    assert.deepEqual(r.position(), ['100px', '200px']);
  });
}

test('ordinary minimized dock retains position restoration and successful demotion preservation', () => {
  for (const preserveCurrentPosition of [false, true]) {
    const r = runtime();
    r.emit('neko:auto-goodbye:state-change', { type: 'visual-tier', tier: 'cat2' });
    const docked = r.position();
    assert.equal(r.I.idleDockActive, true);
    assert.notDeepEqual(docked, ['100px', '200px']);
    r.I.exitIdleDock({ preserveCurrentPosition });
    assert.deepEqual(r.position(), preserveCurrentPosition ? docked : ['100px', '200px']);
    assert.deepEqual(r.requests, []);
    assert.equal(r.I.idleDockActive, false);
  }
});

test('ordinary async dock still finishes and restores only its own automatic minimize', () => {
  const r = runtime({ minimized: false });
  r.I.enterIdleDock();
  r.I.minimized = true; r.I.state.chatSurfaceMode = 'minimized';
  r.shell.classList.add('is-minimized');
  r.observers[0].callback();
  assert.equal(r.I.idleDockActive, true);
  r.I.exitIdleDock();
  assert.deepEqual(r.position(), ['100px', '200px']);
  assert.deepEqual(r.requests, ['minimized', 'compact']);
});

test('Playground exit does not start dock; the next ordinary tier event keeps the original qualification', () => {
  const r = runtime();
  r.cat.__nekoIdleCat1PlaygroundDropState = { active: true };
  r.emit('neko:auto-goodbye:state-change', { type: 'visual-tier', tier: 'cat3' });
  r.cat.__nekoIdleCat1PlaygroundDropState.active = false;
  r.I._handleIdleCat1PlaygroundState({ detail: { active: false, restoreChatOnExit: false } });
  assert.equal(r.I.idleDockActive, false);
  assert.deepEqual(r.position(), ['100px', '200px']);
  r.emit('neko:auto-goodbye:state-change', { type: 'visual-tier', tier: 'cat3' });
  assert.equal(r.I.idleDockActive, true);
});

test('old observer and fallback cannot commit a new ordinary dock after Playground exits', () => {
  const r = runtime({ minimized: false });
  r.I.enterIdleDock();
  const oldObserver = r.observers[0], oldFallback = [...r.timers.values()][0];
  r.cat.__nekoIdleCat1PlaygroundPendingEntry = {};
  r.I._handleIdleCat1PlaygroundState({ detail: { active: true, yarnEnabled: false } });
  delete r.cat.__nekoIdleCat1PlaygroundPendingEntry;
  r.I._handleIdleCat1PlaygroundState({ detail: { active: false } });
  r.I.enterIdleDock();
  const newObserver = r.observers[1];
  assert.ok(newObserver);
  r.shell.classList.add('is-minimized');
  oldObserver.callback(); oldFallback();
  assert.equal(r.I.idleDockMinimizeObserver, newObserver);
  assert.equal(newObserver.disconnected, false);
  assert.equal(r.I.minimized, false);
  assert.equal(r.I.idleDockActive, false);
  assert.equal(r.I.idleDockTriggeredMinimize, true);
  assert.deepEqual(r.position(), ['100px', '200px']);
  assert.deepEqual(r.requests, ['minimized', 'minimized']);
  r.I.minimized = true;
  newObserver.callback();
  assert.equal(r.I.idleDockActive, true);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

// Native IPC promises, time and animation delivery are host boundaries. The
// complete production Electron controller still owns admission, cancellation,
// rollback and the transition from entry to positioning.
function electronRuntime({ collapsed = true } = {}) {
  const frames = new Map(), timers = new Map(), calls = [], events = [];
  let nextId = 0, playgroundActive = false;
  const originalBounds = { x: 100, y: 200, width: 88, height: 88 };
  const workArea = { x: 0, y: 0, width: 1280, height: 900 };
  const I = {
    IDLE_DOCK_TIER_NONE: 'none', IDLE_DOCK_TIER_CAT2: 'cat2', IDLE_DOCK_TIER_CAT3: 'cat3',
    HOME_IDLE_DOCK_GAP: 12, electronIdleDockGeneration: 0, electronIdleDockPositionSeq: 0,
    electronIdleDockDesired: false, electronIdleDockActive: false, electronIdleDockEntering: false,
    electronIdleDockTriggeredCollapse: false, electronIdleDockSavedBounds: null,
    electronIdleDockCurrentBounds: null, electronIdleDockWorkArea: null,
    electronIdleDockRetryTimer: 0, electronIdleDockPositionFrame: 0,
    coerceChatSurfaceModeForHost: value => value, lastRestorableChatSurfaceMode: 'compact',
    isElectronChatWindow: () => true, isElectronLinuxRuntime: () => false,
    isCatLocalChatActive: () => false, getCurrentChatSurfaceMode: () => 'compact',
    getShell: () => null, idleCatPlaygroundYarnSkinActive: false,
  };
  const bridge = {
    getBounds: async () => ({ ...originalBounds }), getWorkArea: async () => ({ ...workArea }),
    isCollapsed: () => collapsed, isCatPlaygroundActive: () => playgroundActive,
    setBounds(...bounds) { calls.push({ kind: 'set', bounds }); },
    async idleDockCollapse() {
      calls.push({ kind: 'collapse' }); collapsed = true; return { ...originalBounds };
    },
    async idleDockExpand(bounds) {
      calls.push({ kind: 'expand', bounds }); collapsed = false; return bounds;
    },
    async idleDockCommitCollapsedBounds(bounds) {
      calls.push({ kind: 'commit', bounds }); return bounds;
    },
  };
  const window = {
    __appReactChatWindowParts: I, nekoChatWindow: bridge, innerWidth: 1280, innerHeight: 900,
    __nekoMinimizedChatBallScreenRect: { x: 100, y: 200, width: 51, height: 51 },
    screen: { availWidth: 1280, availHeight: 900 },
    requestAnimationFrame(callback) { const id = ++nextId; frames.set(id, callback); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout(callback) { const id = ++nextId; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id), addEventListener() {},
    dispatchEvent: event => events.push(event),
  };
  vm.runInNewContext(source, { window, document: {
    hidden: false, querySelectorAll: () => [], querySelector: () => null,
  }, console, setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
  CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } } });
  const r = {
    I, bridge, calls, events, frames, timers, originalBounds, workArea,
    start(rect = { left: 700, top: 500, width: 96, height: 96 }) {
      I.handleElectronIdleReturnBallState({ visible: true, tier: 'cat2', screenRect: rect, reason: 'visual-tier' });
    },
    setCollapsed(value) { collapsed = value; },
    setPlaygroundActive(value) { playgroundActive = value; },
    lifecycle(active, yarnEnabled = false) {
      playgroundActive = active;
      I._handleIdleCat1PlaygroundState({ detail: { active, yarnEnabled, restoreChatOnExit: false } });
    },
    async settle() { for (let index = 0; index < 20; index += 1) await Promise.resolve(); },
    async frame() {
      const callbacks = [...frames.values()]; frames.clear();
      for (const callback of callbacks) callback();
      await r.settle();
    },
    async timer() {
      const callbacks = [...timers.values()]; timers.clear();
      for (const callback of callbacks) callback();
      await r.settle();
    },
  };
  return r;
}

function assertElectronDockSuspended(r) {
  assert.equal(r.I.electronIdleDockDesired, false);
  assert.equal(r.I.electronIdleDockActive, false);
  assert.equal(r.I.electronIdleDockEntering, false);
  assert.equal(r.I.electronIdleDockTriggeredCollapse, false);
  assert.equal(r.I.electronIdleDockSavedBounds, null);
  assert.equal(r.I.electronIdleDockCurrentBounds, null);
  assert.equal(r.I.electronIdleDockWorkArea, null);
  assert.equal(r.I.electronIdleDockRetryTimer, 0);
  assert.equal(r.I.electronIdleDockPositionFrame, 0);
}

for (const yarnEnabled of [false, true]) {
  const label = yarnEnabled ? 'yarn' : 'catOnly';
  test(`remote ${label} lifecycle blocks Electron dock without a local avatar`, async () => {
    const r = electronRuntime(); r.lifecycle(true, yarnEnabled); r.start();
    await r.settle(); await r.frame();
    assertElectronDockSuspended(r);
    assert.deepEqual(r.calls, []);
  });

  for (const phase of ['bounds', 'work-area', 'collapse', 'position-commit']) {
    test(`remote ${label} takeover invalidates pending Electron ${phase} and preserves its surface`, async () => {
      const r = electronRuntime({ collapsed: phase !== 'collapse' }), pending = deferred();
      if (phase === 'bounds') r.bridge.getBounds = () => pending.promise;
      if (phase === 'work-area') r.bridge.getWorkArea = () => pending.promise;
      if (phase === 'collapse') r.bridge.idleDockCollapse = () => {
        r.calls.push({ kind: 'collapse' }); return pending.promise;
      };
      if (phase === 'position-commit') r.bridge.idleDockCommitCollapsedBounds = bounds => {
        r.calls.push({ kind: 'commit', bounds }); return pending.promise;
      };
      r.start(); await r.settle();
      if (phase === 'position-commit') await r.frame();
      const requestsBeforeTakeover = r.calls.length;
      r.lifecycle(true, yarnEnabled);
      assertElectronDockSuspended(r);
      if (phase === 'collapse') r.setCollapsed(true);
      pending.resolve(phase === 'work-area' ? r.workArea : phase === 'position-commit' ? false : r.originalBounds);
      await r.settle(); await r.frame();
      assertElectronDockSuspended(r);
      assert.equal(r.calls.length, requestsBeforeTakeover, 'late completion must not expand, fallback-set or commit');
    });
  }
}

test('retained native Playground session blocks admission when the lifecycle event was missed', async () => {
  const r = electronRuntime(); r.setPlaygroundActive(true); r.start();
  await r.settle(); await r.frame();
  assertElectronDockSuspended(r);
  assert.deepEqual(r.calls, []);
});

test('retained native session stops queued positioning before its first native write', async () => {
  const r = electronRuntime(); r.start(); await r.settle();
  assert.equal(r.I.electronIdleDockActive, true);
  r.setPlaygroundActive(true);
  await r.frame();
  assertElectronDockSuspended(r);
  assert.deepEqual(r.calls, []);
});

test('Playground takeover clears an Electron retry and a delivered old retry cannot revive it', async () => {
  const r = electronRuntime({ collapsed: false });
  r.bridge.idleDockCollapse = async () => { r.calls.push({ kind: 'collapse' }); return false; };
  r.start(); await r.settle();
  const retry = r.timers.get(r.I.electronIdleDockRetryTimer); assert.ok(retry);
  const generation = r.I.electronIdleDockGeneration;
  r.lifecycle(true, false);
  assert.ok(r.I.electronIdleDockGeneration > generation);
  assertElectronDockSuspended(r);
  assert.equal(r.timers.size, 0);
  retry(); await r.settle();
  assert.equal(r.calls.length, 1);
  assertElectronDockSuspended(r);
});

for (const phase of ['bounds', 'work-area', 'collapse']) {
  test(`late ${phase} from a pre-Playground dock cannot mutate a newer ordinary entry`, async () => {
    const r = electronRuntime({ collapsed: phase !== 'collapse' }), old = deferred(), current = deferred();
    if (phase === 'bounds') r.bridge.getBounds = () => old.promise;
    if (phase === 'work-area') r.bridge.getWorkArea = () => old.promise;
    if (phase === 'collapse') r.bridge.idleDockCollapse = () => old.promise;
    r.start(); await r.settle();
    r.lifecycle(true, false); r.lifecycle(false);
    r.bridge.getBounds = () => current.promise;
    r.start({ left: 900, top: 500, width: 96, height: 96 });
    assert.equal(r.I.electronIdleDockEntering, true);
    const generation = r.I.electronIdleDockGeneration;
    if (phase === 'collapse') r.setCollapsed(true);
    old.resolve(phase === 'work-area' ? r.workArea : r.originalBounds);
    await r.settle(); await r.frame();
    assert.equal(r.I.electronIdleDockGeneration, generation);
    assert.equal(r.I.electronIdleDockEntering, true, 'old continuation cannot clear the newer entry guard');
    assert.equal(r.I.electronIdleDockSavedBounds, null, 'old bounds cannot replace the newer entry snapshot');
    assert.equal(r.I.electronIdleDockWorkArea, null, 'old work-area cannot become the new entry cache');
    assert.deepEqual(r.calls, [], 'a PG-cancelled collapse never rolls back after PG exits');
    r.bridge.getWorkArea = async () => r.workArea;
    current.resolve({ x: 300, y: 400, width: 88, height: 88 });
    await r.settle(); await r.frame();
    assert.equal(r.I.electronIdleDockActive, true);
    assert.equal(r.I.electronIdleDockSavedBounds.x, 300);
    assert.equal(r.calls.filter(call => call.kind === 'commit').length, 1);
    assert.equal(r.calls.find(call => call.kind === 'commit').bounds.x, 800);
  });
}

test('Playground cancels a pending preserve-position commit without retry or fallback after exit', async () => {
  const r = electronRuntime(); r.start(); await r.settle(); await r.frame();
  const pending = deferred();
  r.bridge.idleDockCommitCollapsedBounds = bounds => { r.calls.push({ kind: 'commit', bounds }); return pending.promise; };
  const exit = r.I.exitElectronIdleDock({ preserveCurrentPosition: true,
    preserveScreenRect: { left: 800, top: 500, width: 96, height: 96 } });
  await r.settle();
  const requestCount = r.calls.length;
  r.lifecycle(true, false); r.lifecycle(false);
  pending.resolve(false); await exit; await r.timer(); await r.frame();
  assertElectronDockSuspended(r);
  assert.equal(r.calls.length, requestCount);
});

test('Playground invalidates an already waiting preserve-position retry', async () => {
  const r = electronRuntime(); r.start(); await r.settle(); await r.frame();
  r.bridge.idleDockCommitCollapsedBounds = async bounds => { r.calls.push({ kind: 'commit', bounds }); return false; };
  const exit = r.I.exitElectronIdleDock({ preserveCurrentPosition: true,
    preserveScreenRect: { left: 800, top: 500, width: 96, height: 96 } });
  await r.settle();
  assert.equal(r.timers.size, 1, 'ordinary preserve uses the existing native retry');
  const requestCount = r.calls.length;
  r.lifecycle(true, true); r.lifecycle(false);
  await r.timer(); await exit;
  assert.equal(r.calls.length, requestCount);
  assertElectronDockSuspended(r);
});

test('late rejected ordinary expand cannot fallback to old bounds after Playground takes ownership', async () => {
  const r = electronRuntime({ collapsed: false }); r.start(); await r.settle(); await r.frame();
  const pending = deferred();
  r.bridge.idleDockExpand = bounds => { r.calls.push({ kind: 'expand', bounds }); return pending.promise; };
  const exit = r.I.exitElectronIdleDock(); await r.settle();
  const requestCount = r.calls.length;
  r.lifecycle(true, false); r.lifecycle(false);
  pending.reject(new Error('stale native expansion failed'));
  await exit; await r.frame();
  assert.equal(r.calls.length, requestCount);
  assertElectronDockSuspended(r);
});

test('ordinary Electron automatic collapse, positioning and restore keep their original protocol', async () => {
  const r = electronRuntime({ collapsed: false }); r.start();
  await r.settle(); await r.frame();
  assert.equal(r.I.electronIdleDockActive, true);
  assert.deepEqual(r.calls.map(call => call.kind), ['collapse', 'commit']);
  assert.equal(r.calls[1].bounds.x, 600);
  assert.equal(r.calls[1].bounds.y, 504);
  await r.I.exitElectronIdleDock();
  assert.deepEqual(r.calls.map(call => call.kind), ['collapse', 'commit', 'expand']);
  assert.deepEqual(r.calls[2].bounds, r.originalBounds);
  assertElectronDockSuspended(r);
});

test('ordinary cancellation during native collapse still rolls back its own automatic collapse', async () => {
  const r = electronRuntime({ collapsed: false }), pending = deferred();
  r.bridge.idleDockCollapse = () => { r.calls.push({ kind: 'collapse' }); return pending.promise; };
  r.start(); await r.settle();
  await r.I.exitElectronIdleDock();
  r.setCollapsed(true); pending.resolve(r.originalBounds); await r.settle();
  assert.deepEqual(r.calls.map(call => call.kind), ['collapse', 'expand']);
  assert.deepEqual(r.calls[1].bounds, r.originalBounds);
  assertElectronDockSuspended(r);
});

test('ordinary preserve-position keeps native retries and the legacy fallback', async () => {
  const r = electronRuntime(); r.start(); await r.settle(); await r.frame();
  r.bridge.idleDockCommitCollapsedBounds = async bounds => { r.calls.push({ kind: 'commit', bounds }); return false; };
  const exit = r.I.exitElectronIdleDock({ preserveCurrentPosition: true,
    preserveScreenRect: { left: 800, top: 500, width: 96, height: 96 } });
  await r.settle();
  for (let index = 0; index < 3; index += 1) await r.timer();
  await exit;
  assert.equal(r.calls.filter(call => call.kind === 'commit').length, 5);
  const fallback = r.calls.find(call => call.kind === 'set'); assert.ok(fallback);
  assert.deepEqual(fallback.bounds, [700, 504, 88, 88]);
  assert.equal(r.I.electronIdleDockDesired, false);
  assert.equal(r.I.electronIdleDockActive, false);
  assert.equal(r.I.electronIdleDockEntering, false);
  assert.equal(r.I.electronIdleDockSavedBounds, null);
  assert.equal(r.I.electronIdleDockCurrentBounds.x, 700, 'ordinary fallback retains its existing bounds cache');
  assert.equal(r.I.electronIdleDockCurrentBounds.y, 504);
});

test('Playground takeover retires a Web wake-up restore without reopening chat', () => {
  const r = runtime();
  r.I.idleDockParkedRestore = { position: { left: 20, top: 30 } };
  const position = r.position();
  r.I._handleIdleCat1PlaygroundState({ detail: { active: true, yarnEnabled: false } });
  assert.equal(r.I.idleDockParkedRestore, null);
  r.I._handleIdleCat1PlaygroundState({ detail: { active: false } });
  r.I.exitIdleDock();
  assert.deepEqual(r.requests, []);
  assert.deepEqual(r.position(), position);
  r.I.enterIdleDock();
  assert.equal(r.I.idleDockActive, true, 'new ordinary docking still works');
});

test('Playground takeover retires an Electron wake-up restore through the real return', async () => {
  const r = electronRuntime();
  r.I.electronIdleDockParkedRestoreBounds = r.originalBounds;
  r.lifecycle(true, false);
  assert.equal(r.I.electronIdleDockParkedRestoreBounds, null);
  r.lifecycle(false);
  r.I.handleElectronIdleReturnBallState({ visible: false, tier: 'none', reason: 'return' });
  await r.settle(); await r.frame();
  assert.deepEqual(r.calls, []);
  assertElectronDockSuspended(r);
});

test('an awaited parked Electron restore cannot fallback after Playground takes over', async () => {
  const r = electronRuntime(), pending = deferred();
  r.I.electronIdleDockParkedRestoreBounds = r.originalBounds;
  r.bridge.idleDockExpand = bounds => {
    r.calls.push({ kind: 'expand', bounds }); return pending.promise;
  };
  const exit = r.I.exitElectronIdleDock(); await r.settle();
  assert.deepEqual(r.calls.map(call => call.kind), ['expand']);
  r.lifecycle(true, false); r.lifecycle(false);
  pending.reject(new Error('old wake-up restore failed'));
  await exit;
  assert.deepEqual(r.calls.map(call => call.kind), ['expand']);
  assert.equal(r.I.electronIdleDockParkedRestoreBounds, null);
  assertElectronDockSuspended(r);
});

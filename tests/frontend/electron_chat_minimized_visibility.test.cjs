const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '../../static/app/app-react-chat-window/minimize-and-idle-dock.js'),
  'utf8',
);

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createHarness({
  available = true,
  collapsed = true,
  exposeAvailabilityEpoch = true,
  getBounds = () => Promise.resolve({ x: 100, y: 200, width: 88, height: 88 }),
} = {}) {
  const emitted = [];
  const animationFrames = [];
  const windowListeners = new Map();
  const documentListeners = new Map();
  let boundsCalls = 0;
  let nextTimerId = 1;
  let availabilityEpoch = 0;

  const bridge = {
    isCollapsed: () => collapsed,
    isIdleTargetAvailable: () => available,
    getIdleTargetAvailabilityEpoch: () => availabilityEpoch,
    getBounds: () => {
      boundsCalls += 1;
      return getBounds();
    },
  };
  if (!exposeAvailabilityEpoch) delete bridge.getIdleTargetAvailabilityEpoch;

  const document = {
    hidden: !available,
    addEventListener(type, listener) {
      documentListeners.set(type, listener);
    },
    querySelector() { return null; },
  };
  const parts = {
    isElectronChatWindow: () => true,
    electronChatMinimizedStateSignature: '',
    electronChatMinimizedStatePublishedAt: 0,
    electronChatMinimizedStateFrame: 0,
    electronChatMinimizedStateTimer: 0,
    electronChatMinimizedStateFullRateUntil: 0,
    ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS: 1000,
  };
  const window = {
    __appReactChatWindowParts: parts,
    reactChatWindowHost: {},
    nekoChatWindow: bridge,
    innerWidth: 1280,
    innerHeight: 720,
    screen: { availWidth: 1280, availHeight: 720 },
    requestAnimationFrame(callback) {
      animationFrames.push(callback);
      return animationFrames.length;
    },
    cancelAnimationFrame() {},
    setInterval() { return nextTimerId += 1; },
    clearInterval() {},
    setTimeout() { return nextTimerId += 1; },
    clearTimeout() {},
    addEventListener(type, listener) {
      windowListeners.set(type, listener);
    },
    dispatchEvent(event) {
      emitted.push(event);
      return true;
    },
  };

  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }

  vm.runInNewContext(source, {
    window,
    document,
    CustomEvent,
    console,
    Promise,
    Date,
    Number,
    Math,
    Object,
    Array,
    String,
    Boolean,
    JSON,
  }, { filename: 'minimize-and-idle-dock.js' });

  return {
    parts,
    emitted,
    get boundsCalls() { return boundsCalls; },
    setAvailable(nextAvailable) {
      const normalized = nextAvailable === true;
      if (normalized !== available) availabilityEpoch += 1;
      available = normalized;
      document.hidden = !available;
    },
    setCollapsed(nextCollapsed) {
      collapsed = nextCollapsed === true;
    },
    flushAnimationFrames() {
      while (animationFrames.length) {
        animationFrames.shift()();
      }
    },
    dispatchVisibilityChange() {
      const listener = documentListeners.get('visibilitychange');
      assert.equal(typeof listener, 'function');
      listener();
    },
  };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

test('Playground skin state changes only the minimized icon selected by the chat owner', () => {
  const harness = createHarness();
  Object.assign(harness.parts, {
    CHAT_MINIMIZED_BALL_ICON_SRC: '/normal.png',
    CHAT_MINIMIZED_BALL_ICON_SRCSET: '/normal.png 1x, /normal@2x.png 2x',
    CHAT_MINIMIZED_BALL_LEGACY_FULL_ICON_SRC: '/legacy.png',
    CHAT_MINIMIZED_BALL_PLAYGROUND_ICON_SRC: '/playground.png',
    lastRestorableChatSurfaceMode: 'compact',
    coerceChatSurfaceModeForHost: value => value,
    getShell: () => null,
    idleCatPlaygroundYarnSkinActive: false,
  });
  const icon = {};
  harness.parts.applyMinimizedBallSkin(icon);
  assert.deepEqual(icon, {
    src: '/normal.png',
    srcset: '/normal.png 1x, /normal@2x.png 2x',
  });
  harness.parts.idleCatPlaygroundYarnSkinActive = true;
  harness.parts.applyMinimizedBallSkin(icon);
  assert.deepEqual(icon, { src: '/playground.png', srcset: '' });
  harness.parts.idleCatPlaygroundYarnSkinActive = false;
  harness.parts.applyMinimizedBallSkin(icon);
  assert.equal(icon.src, '/normal.png');
});

test('cat-only Playground state keeps the ordinary minimized-ball skin', () => {
  const harness = createHarness();
  Object.assign(harness.parts, {
    getCurrentChatSurfaceMode: () => 'full',
    idleCatPlaygroundYarnSkinActive: true,
  });
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: true, yarnEnabled: false },
  });
  assert.equal(harness.parts.idleCatPlaygroundYarnSkinActive, false);
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: true, yarnEnabled: true },
  });
  assert.equal(harness.parts.idleCatPlaygroundYarnSkinActive, true);
});

test('Playground exit queues entry-owned chat restore through an in-flight collapse', () => {
  const harness = createHarness();
  Object.assign(harness.parts, {
    getCurrentChatSurfaceMode: () => 'full',
    getNextChatSurfaceMode: () => 'full',
    isMinimizeTransitioning: true,
    pendingChatSurfaceMode: null,
  });
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'entry-cancel', restoreChatOnExit: true },
  });
  assert.equal(harness.parts.pendingChatSurfaceMode.mode, 'full');
  assert.deepEqual(Object.keys(harness.parts.pendingChatSurfaceMode.transitionOptions), []);
});

test('Playground cancellation replaces its own queued minimize, not a later user request', () => {
  const harness = createHarness();
  Object.assign(harness.parts, {
    getCurrentChatSurfaceMode: () => 'full',
    getNextChatSurfaceMode: () => 'full',
    isMinimizeTransitioning: true,
    pendingChatSurfaceMode: null,
    setChatSurfaceMode(mode) {
      this.pendingChatSurfaceMode = { mode, transitionOptions: {} };
    },
  });
  harness.parts._handleIdleCat1PlaygroundYarnRequest({});
  assert.equal(harness.parts.pendingChatSurfaceMode.playgroundEntry, true);
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'entry-cancel', restoreChatOnExit: true },
  });
  assert.equal(harness.parts.pendingChatSurfaceMode.mode, 'full');

  const userMinimize = { mode: 'minimized', transitionOptions: {} };
  harness.parts.pendingChatSurfaceMode = userMinimize;
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'entry-cancel', restoreChatOnExit: true },
  });
  assert.equal(harness.parts.pendingChatSurfaceMode, userMinimize);
});

test('user minimize queued before Playground entry keeps ownership after the transition', () => {
  for (const cancelBeforeCommit of [true, false]) {
    const harness = createHarness();
    const userMinimize = { mode: 'minimized', transitionOptions: {} };
    const requestedModes = [];
    Object.assign(harness.parts, {
      getCurrentChatSurfaceMode: () => 'full',
      getNextChatSurfaceMode: () => 'full',
      isMinimizeTransitioning: true,
      pendingChatSurfaceMode: userMinimize,
      setChatSurfaceMode(mode) { requestedModes.push(mode); },
    });
    harness.parts._handleIdleCat1PlaygroundYarnRequest({});
    assert.equal(harness.parts.pendingChatSurfaceMode, userMinimize);
    assert.equal(userMinimize.playgroundEntry, undefined);
    assert.deepEqual(requestedModes, []);

    if (cancelBeforeCommit) {
      harness.parts._handleIdleCat1PlaygroundState({
        detail: { active: false, reason: 'entry-cancel', restoreChatOnExit: true },
      });
      assert.equal(harness.parts.pendingChatSurfaceMode, userMinimize);
    } else {
      harness.parts.pendingChatSurfaceMode = null;
      harness.parts.isMinimizeTransitioning = false;
      harness.parts.getCurrentChatSurfaceMode = () => 'minimized';
      harness.parts.getShell = () => null;
      harness.parts.applyMinimizedBallSkin = () => {};
      harness.parts._handleIdleCat1PlaygroundState({
        detail: { active: false, reason: 'cat-click', restoreChatOnExit: true },
      });
      harness.parts._handleIdleCat1PlaygroundState({
        detail: { active: false, reason: 'cat-click', restoreChatOnExit: true },
      });
      assert.deepEqual(requestedModes, []);

      harness.parts.getCurrentChatSurfaceMode = () => 'full';
      harness.parts._handleIdleCat1PlaygroundYarnRequest({});
      assert.deepEqual(requestedModes, ['minimized']);
      harness.parts.getCurrentChatSurfaceMode = () => 'minimized';
      harness.parts._handleIdleCat1PlaygroundState({
        detail: { active: false, reason: 'cat-click', restoreChatOnExit: true },
      });
      assert.deepEqual(requestedModes, ['minimized', 'full']);
    }
  }
});

test('cat return preserves an independently minimized chat surface', () => {
  const harness = createHarness();
  const restored = [];
  Object.assign(harness.parts, {
    getCurrentChatSurfaceMode: () => 'minimized',
    getNextChatSurfaceMode: () => 'compact',
    getShell: () => null,
    applyMinimizedBallSkin() {},
    setChatSurfaceMode(mode) { restored.push(mode); },
  });
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'cat-click', restoreChatOnExit: false },
  });
  assert.deepEqual(restored, []);
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'cat-click', restoreChatOnExit: true },
  });
  assert.deepEqual(restored, ['compact']);
});

test('returning from Playground reapplies the ordinary Web ball viewport clamp', () => {
  const harness = createHarness();
  const positions = [];
  const shell = {
    querySelector: () => ({}),
    getBoundingClientRect: () => ({ left: -6, top: 680, width: 51, height: 51 }),
  };
  Object.assign(harness.parts, {
    idleCatPlaygroundYarnSkinActive: true,
    getCurrentChatSurfaceMode: () => 'minimized',
    isElectronChatWindow: () => false,
    getShell: () => shell,
    applyMinimizedBallSkin() {},
    applyPosition(left, top) { positions.push([left, top]); },
  });
  harness.parts._handleIdleCat1PlaygroundState({
    detail: { active: false, reason: 'question-block-click', restoreChatOnExit: false },
  });
  assert.deepEqual(positions, [[-6, 680]]);
  assert.equal(harness.parts.idleCatPlaygroundYarnSkinActive, false);
});

test('host mode changes preserve an active voice message', () => {
  const { parts } = createHarness({ collapsed: false });
  let audioActive = true;
  parts.getShell = () => ({
    querySelector(selector) {
      assert.equal(selector, '.audio-message-composer[data-audio-message-active="true"]');
      return audioActive ? {} : null;
    },
  });
  parts.coerceChatSurfaceModeForHost = mode => mode;
  parts.getCurrentChatSurfaceMode = () => 'compact';
  parts.isMinimizeTransitioning = true;
  parts.pendingChatSurfaceMode = null;

  assert.equal(parts.setChatSurfaceMode('minimized'), 'compact');
  assert.equal(parts.pendingChatSurfaceMode, null);

  assert.equal(parts.setChatSurfaceMode('minimized', { fromCompactMinimizeButton: true }), 'compact');
  assert.equal(parts.pendingChatSurfaceMode.mode, 'minimized');
  parts.pendingChatSurfaceMode = null;

  audioActive = false;
  assert.equal(parts.setChatSurfaceMode('minimized'), 'compact');
  assert.equal(parts.pendingChatSurfaceMode.mode, 'minimized');
});

test('a visible collapsed Electron chat publishes its minimized yarn target', async () => {
  const harness = createHarness();
  harness.parts.ensureElectronChatMinimizedStateBridge();
  harness.flushAnimationFrames();
  await flushPromises();

  assert.equal(harness.boundsCalls, 1);
  assert.equal(harness.emitted.length, 1);
  assert.equal(harness.emitted[0].type, 'neko:idle-chat-minimized-state');
  assert.equal(harness.emitted[0].detail.available, true);
  assert.equal(harness.emitted[0].detail.minimized, true);
  assert.deepEqual(
    JSON.parse(JSON.stringify(harness.emitted[0].detail.screenRect)),
    { left: 119, top: 219, width: 51, height: 51, right: 170, bottom: 270 },
  );
});

test('a hidden Electron chat retires the target without querying stale bounds', () => {
  const harness = createHarness({ available: false });
  harness.parts.ensureElectronChatMinimizedStateBridge();
  harness.flushAnimationFrames();

  assert.equal(harness.boundsCalls, 0);
  assert.equal(harness.emitted.length, 1);
  assert.equal(harness.emitted[0].detail.available, false);
  assert.equal(harness.emitted[0].detail.minimized, false);
  assert.equal(harness.emitted[0].detail.screenRect, null);
});

test('a bounds reply that arrives after tray close cannot resurrect the yarn target', async () => {
  const deferredBounds = createDeferred();
  const harness = createHarness({ getBounds: () => deferredBounds.promise });
  harness.parts.ensureElectronChatMinimizedStateBridge();
  harness.flushAnimationFrames();
  assert.equal(harness.boundsCalls, 1);
  assert.equal(harness.emitted.length, 0);

  harness.setAvailable(false);
  harness.dispatchVisibilityChange();
  assert.equal(harness.emitted.length, 1);
  assert.equal(harness.emitted[0].detail.available, false);

  deferredBounds.resolve({ x: 100, y: 200, width: 88, height: 88 });
  await flushPromises();

  assert.equal(
    harness.emitted.some((event) => event.detail && event.detail.minimized === true),
    false,
  );
  assert.equal(harness.emitted.length, 1);
});

test('a bounds reply from before a hide and quick reopen is discarded by availability epoch', async () => {
  const oldBounds = createDeferred();
  const currentBounds = createDeferred();
  let requestCount = 0;
  const harness = createHarness({
    getBounds: () => {
      requestCount += 1;
      return requestCount === 1 ? oldBounds.promise : currentBounds.promise;
    },
  });
  harness.parts.ensureElectronChatMinimizedStateBridge();
  harness.flushAnimationFrames();
  assert.equal(harness.boundsCalls, 1);

  // X11 can keep document visibility unchanged; the preload-owned epoch still records both transitions.
  harness.setAvailable(false);
  harness.setAvailable(true);

  oldBounds.resolve({ x: 100, y: 200, width: 88, height: 88 });
  await flushPromises();
  assert.equal(
    harness.emitted.some((event) => event.detail && event.detail.minimized === true),
    false,
    'the pre-hide request must not publish after reopen',
  );

  harness.flushAnimationFrames();
  assert.equal(harness.boundsCalls, 2);
  currentBounds.resolve({ x: 400, y: 300, width: 88, height: 88 });
  await flushPromises();

  const availableEvents = harness.emitted.filter((event) => event.detail && event.detail.available === true);
  assert.equal(availableEvents.length, 1);
  assert.deepEqual(
    JSON.parse(JSON.stringify(availableEvents[0].detail.screenRect)),
    { left: 419, top: 319, width: 51, height: 51, right: 470, bottom: 370 },
  );
});

test('visibility transitions invalidate old bounds when the preload epoch API is absent', async () => {
  const oldBounds = createDeferred();
  const harness = createHarness({
    exposeAvailabilityEpoch: false,
    getBounds: () => oldBounds.promise,
  });
  harness.parts.ensureElectronChatMinimizedStateBridge();
  harness.flushAnimationFrames();

  harness.setAvailable(false);
  harness.dispatchVisibilityChange();
  harness.setAvailable(true);
  harness.dispatchVisibilityChange();
  oldBounds.resolve({ x: 100, y: 200, width: 88, height: 88 });
  await flushPromises();

  assert.equal(
    harness.emitted.some((event) => event.detail && event.detail.minimized === true),
    false,
    'renderer visibility epochs must protect compatibility with older preload versions',
  );
});

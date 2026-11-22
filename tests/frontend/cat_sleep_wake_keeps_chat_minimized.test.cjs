// When the cat is asleep (CAT2/CAT3) the chat is auto-collapsed next to her.
// A random sleep-tier action can wake her up (back to CAT1) with nobody at the
// computer. That wake-up must not reopen the chat; only the user actually
// bringing her back may, and that reopen must still happen.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '../../static/app/app-react-chat-window/minimize-and-idle-dock.js'),
  'utf8',
);

const EXPANDED = { x: 300, y: 260, width: 420, height: 520 };
const CAT_RECT = { left: 900, top: 600, width: 120, height: 120, right: 1020, bottom: 720 };

function createHarness({ electron }) {
  const animationFrames = [];
  const expandCalls = [];
  const setBoundsCalls = [];
  const surfaceModes = [];
  let nextTimerId = 1;
  let collapsed = false;
  let windowBounds = { ...EXPANDED };

  const bridge = {
    isCollapsed: () => collapsed,
    isIdleTargetAvailable: () => true,
    getIdleTargetAvailabilityEpoch: () => 0,
    getBounds: () => Promise.resolve({ ...windowBounds }),
    getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1920, height: 1080 }),
    idleDockCollapse: () => {
      collapsed = true;
      windowBounds = { x: windowBounds.x, y: windowBounds.y, width: 88, height: 88 };
      return Promise.resolve({ ...windowBounds });
    },
    idleDockExpand: (bounds) => {
      expandCalls.push({ ...bounds });
      collapsed = false;
      windowBounds = { ...bounds };
      return Promise.resolve(true);
    },
    idleDockCommitCollapsedBounds: (bounds) => {
      windowBounds = { ...bounds };
      return Promise.resolve({ ...bounds });
    },
    setBounds: (x, y, width, height) => {
      setBoundsCalls.push({ x, y, width, height });
      windowBounds = { x, y, width, height };
    },
  };

  const shellClasses = new Set();
  const shell = {
    style: {},
    classList: {
      add: (name) => shellClasses.add(name),
      remove: (...names) => names.forEach((name) => shellClasses.delete(name)),
      contains: (name) => shellClasses.has(name),
    },
    getBoundingClientRect: () => ({ left: 40, top: 50, width: 51, height: 51 }),
  };

  let tier = 'none';
  const parts = {
    isElectronChatWindow: () => electron,
    isCatLocalChatActive: () => false,
    getCurrentChatSurfaceMode: () => (parts.minimized ? 'minimized' : 'compact'),
    coerceChatSurfaceModeForHost: (mode) => mode,
    lastRestorableChatSurfaceMode: 'compact',
    isIdleDockTierActive: () => tier === 'cat2' || tier === 'cat3',
    getShell: () => shell,
    IDLE_DOCK_TIER_NONE: 'none',
    IDLE_DOCK_TIER_CAT2: 'cat2',
    IDLE_DOCK_TIER_CAT3: 'cat3',
    HOME_IDLE_DOCK_GAP: -12,
    MINIMIZED_SIZE: 51,
    MINIMIZED_DOWN_OFFSET: 0,
    minimized: false,
    idleDockActive: false,
    idleDockSavedPosition: null,
    idleDockTriggeredMinimize: false,
    idleDockMinimizeObserver: null,
    idleDockParkedRestore: null,
    electronIdleDockActive: false,
    electronIdleDockTriggeredCollapse: false,
    electronIdleDockSavedBounds: null,
    electronIdleDockLastScreenRect: null,
    electronIdleDockEntering: false,
    electronIdleDockRetryTimer: 0,
    electronIdleDockDesired: false,
    electronIdleDockGeneration: 0,
    electronIdleDockPositionFrame: 0,
    electronIdleDockPositionSeq: 0,
    electronIdleDockCurrentBounds: null,
    electronIdleDockWorkArea: null,
    electronIdleDockParkedRestoreBounds: null,
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
    innerWidth: 1920,
    innerHeight: 1080,
    screen: { availWidth: 1920, availHeight: 1080 },
    requestAnimationFrame(callback) {
      animationFrames.push(callback);
      return animationFrames.length;
    },
    cancelAnimationFrame() {},
    setInterval() { return nextTimerId += 1; },
    clearInterval() {},
    setTimeout() { return nextTimerId += 1; },
    clearTimeout() {},
    addEventListener() {},
    dispatchEvent() { return true; },
  };

  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }

  vm.runInNewContext(source, {
    window,
    document: { hidden: false, addEventListener() {}, querySelector() { return null; } },
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

  // The surface switch itself is covered elsewhere; record what the dock asks for.
  Object.assign(parts, {
    setChatSurfaceMode(mode) {
      surfaceModes.push(mode);
      parts.minimized = mode === 'minimized';
    },
    syncChatSurfaceModeUI() {},
  });

  async function settle() {
    for (let round = 0; round < 8; round += 1) {
      while (animationFrames.length) animationFrames.shift()();
      await Promise.resolve();
      await Promise.resolve();
    }
  }

  return {
    parts,
    bridge,
    shell,
    expandCalls,
    setBoundsCalls,
    surfaceModes,
    settle,
    setTier(nextTier) { tier = nextTier; },
    get collapsed() { return collapsed; },
    setCollapsed(next) { collapsed = next; },
    // Electron: what the pet window broadcasts about the return ball.
    async petState(detail) {
      parts.handleElectronIdleReturnBallState({
        visible: true,
        screenRect: CAT_RECT,
        ...detail,
      });
      await settle();
    },
  };
}

async function electronDockedAsleep() {
  const h = createHarness({ electron: true });
  await h.petState({ tier: 'cat2', reason: 'visual-tier' });
  assert.equal(h.parts.electronIdleDockActive, true);
  assert.equal(h.parts.electronIdleDockTriggeredCollapse, true);
  assert.equal(h.collapsed, true);
  return h;
}

test('Electron: the cat waking up on her own leaves the yarn ball collapsed', async () => {
  const h = await electronDockedAsleep();

  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });
  assert.deepEqual(h.expandCalls, [], 'waking up must not reopen the chat');
  assert.equal(h.collapsed, true);
  assert.equal(h.parts.electronIdleDockActive, false, 'the dock lets go so CAT1 can play with the ball');

  // Ordinary CAT1 traffic afterwards is not a return either.
  await h.petState({ tier: 'cat1', reason: 'return-ball-revealed' });
  await h.petState({ tier: 'cat1', reason: 'goodbye-idle-appearance' });
  assert.deepEqual(h.expandCalls, []);
  assert.equal(h.collapsed, true);
});

test('Electron: bringing her back after she woke up still reopens the chat where it was', async () => {
  const h = await electronDockedAsleep();
  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });

  // Drag frames hide the ball for a moment, and the native drag window resizes
  // the pet viewport; neither is the return.
  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-drag-start' });
  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'viewport-resize' });
  assert.deepEqual(h.expandCalls, []);
  assert.ok(h.parts.electronIdleDockParkedRestoreBounds, 'the reopen is still owed');

  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-hide' });
  assert.deepEqual(h.expandCalls, [EXPANDED]);
  assert.equal(h.collapsed, false);
});

test('Electron: falling asleep again after waking keeps the duty to reopen on return', async () => {
  const h = await electronDockedAsleep();
  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });
  await h.petState({ tier: 'cat2', reason: 'visual-tier' });
  assert.equal(h.parts.electronIdleDockActive, true);
  assert.equal(h.parts.electronIdleDockTriggeredCollapse, true);
  assert.deepEqual(h.expandCalls, []);

  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-hide' });
  assert.deepEqual(h.expandCalls, [EXPANDED]);
});

test('Electron: waking again before a re-dock has finished keeps the reopen owed', async () => {
  const h = await electronDockedAsleep();
  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });

  // She falls asleep again; the re-dock waits on the window bounds...
  let releaseBounds;
  const realGetBounds = h.bridge.getBounds;
  h.bridge.getBounds = () => new Promise((resolve) => {
    releaseBounds = () => realGetBounds().then(resolve);
  });
  h.parts.handleElectronIdleReturnBallState({ visible: true, screenRect: CAT_RECT, tier: 'cat2', reason: 'visual-tier' });
  assert.equal(h.parts.hasElectronIdleDockPendingOrActive(), true);
  assert.equal(h.parts.electronIdleDockActive, false);

  // ...and wakes up again before it lands.
  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });
  h.bridge.getBounds = realGetBounds;
  releaseBounds();
  await h.settle();
  assert.equal(h.parts.electronIdleDockActive, false, 'the stale re-dock gives up');
  assert.deepEqual(h.expandCalls, []);

  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-hide' });
  assert.deepEqual(h.expandCalls, [EXPANDED]);
});

test('Electron: a user who opens the ball themselves is not reopened again on return', async () => {
  const h = await electronDockedAsleep();
  await h.petState({ tier: 'cat1', reason: 'cat-sleep-wake-demotion' });

  // User clicks the yarn ball open, the minimized-state poll notices.
  h.setCollapsed(false);
  h.parts.ensureElectronChatMinimizedStateBridge();
  await h.settle();
  assert.equal(h.parts.electronIdleDockParkedRestoreBounds, null);

  // ...and collapses it again by hand before bringing the cat back.
  h.setCollapsed(true);
  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-hide' });
  assert.deepEqual(h.expandCalls, []);
});

test('Electron: bringing her back while still asleep reopens the chat as before', async () => {
  const h = await electronDockedAsleep();
  await h.petState({ visible: false, tier: 'none', screenRect: null, reason: 'return-ball-hide' });
  assert.deepEqual(h.expandCalls, [EXPANDED]);
});

test('Electron: a demotion the user caused still reopens the chat', async () => {
  const h = await electronDockedAsleep();
  // Right-click "scared" demotion and other user-driven CAT1 changes arrive as visual-tier.
  await h.petState({ tier: 'cat1', reason: 'visual-tier' });
  assert.deepEqual(h.expandCalls, [EXPANDED]);
});

function webDockedAsleep() {
  const h = createHarness({ electron: false });
  // The dock minimized an open compact chat for the sleeping cat.
  h.setTier('cat2');
  Object.assign(h.parts, {
    minimized: true,
    idleDockActive: true,
    idleDockTriggeredMinimize: true,
    idleDockSavedPosition: { left: 10, top: 20 },
  });
  return h;
}

test('Web: the cat waking up on her own leaves the chat minimized', () => {
  const h = webDockedAsleep();
  h.setTier('cat1');
  h.parts.exitIdleDock({ preserveCurrentPosition: true, parkRestoreForReturn: true });
  assert.deepEqual(h.surfaceModes, []);
  assert.equal(h.parts.minimized, true);
  assert.equal(h.parts.idleDockActive, false);
});

test('Web: bringing her back after she woke up still reopens the chat where it was', () => {
  const h = webDockedAsleep();
  h.setTier('cat1');
  h.parts.exitIdleDock({ preserveCurrentPosition: true, parkRestoreForReturn: true });

  h.setTier('none');
  h.parts.exitIdleDock({});
  assert.deepEqual(h.surfaceModes, ['compact']);
  assert.equal(h.shell.style.left, '10px');
  assert.equal(h.shell.style.top, '20px');
});

test('Web: falling asleep again after waking keeps the duty to reopen on return', () => {
  const h = webDockedAsleep();
  h.setTier('cat1');
  h.parts.exitIdleDock({ preserveCurrentPosition: true, parkRestoreForReturn: true });

  h.setTier('cat3');
  h.parts.enterIdleDock();
  assert.equal(h.parts.idleDockActive, true);
  assert.equal(h.parts.idleDockTriggeredMinimize, true);

  h.setTier('none');
  h.parts.exitIdleDock({});
  assert.deepEqual(h.surfaceModes, ['compact']);
  assert.equal(h.shell.style.left, '10px');
});

test('Web: a drag demotion (user present) keeps its old no-reopen behavior', () => {
  const h = webDockedAsleep();
  h.setTier('cat1');
  h.parts.exitIdleDock({ preserveCurrentPosition: true });
  assert.equal(h.parts.idleDockParkedRestore, null);
  h.parts.exitIdleDock({});
  assert.deepEqual(h.surfaceModes, []);
});

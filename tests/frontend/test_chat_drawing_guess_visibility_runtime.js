const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function drawingGuessVisibilitySource() {
  const templatePath = path.resolve(__dirname, '../../templates/chat.html');
  const source = fs.readFileSync(templatePath, 'utf8');
  const start = source.indexOf('        var _drawingGuessChatHiddenSnap = null;');
  const end = source.indexOf(
    "        window.addEventListener('neko-game-window-state-change'",
    start,
  );
  assert(start >= 0 && end > start, 'drawing-guess chat visibility block was not found');
  return source.slice(start, end);
}

function createHarness({
  visible,
  getBounds,
  exposeAvailability = true,
  deferVisibilityCommands = false,
}) {
  const calls = [];
  let bridgeVisible = visible;
  let availabilityEpoch = 0;
  const documentListeners = new Map();
  const visibilityCommands = [];
  function applyVisibility(nextVisible) {
    const changed = bridgeVisible !== nextVisible;
    bridgeVisible = nextVisible;
    if (!changed) return;
    availabilityEpoch += 1;
    for (const callback of documentListeners.get('visibilitychange') || []) callback();
  }
  function requestVisibility(nextVisible) {
    if (deferVisibilityCommands) visibilityCommands.push(nextVisible);
    else applyVisibility(nextVisible);
  }
  const W = {
    getBounds: getBounds || (() => Promise.resolve({ x: 10, y: 20, width: 420, height: 360 })),
    setBounds(x, y, width, height) { calls.push(['setBounds', x, y, width, height]); },
    hide() { calls.push(['hide']); requestVisibility(false); },
    show() { calls.push(['show']); requestVisibility(true); },
    setCompactChatBallTemporarilyHidden(hidden) { calls.push(['compactHidden', hidden]); },
  };
  if (exposeAvailability) {
    W.isIdleTargetAvailable = () => bridgeVisible;
    W.getIdleTargetAvailabilityEpoch = () => availabilityEpoch;
  }
  const document = {};
  Object.defineProperty(document, 'hidden', { get: () => !bridgeVisible });
  document.addEventListener = (type, callback) => {
    if (!documentListeners.has(type)) documentListeners.set(type, new Set());
    documentListeners.get(type).add(callback);
  };
  const context = {
    W,
    document,
    Promise,
    String,
    savedWH: { w: 420, h: 360 },
    animating: false,
    _gameWindowActive: false,
    _gameMinimizeForGame() { calls.push(['fallbackMinimize']); },
    _gameRestoreAfterGame() { calls.push(['fallbackRestore']); },
    requestAnimationFrame(callback) { callback(); return 1; },
  };
  context.globalThis = context;
  const instrumented = `${drawingGuessVisibilitySource()}
    globalThis.__drawingGuessVisibility = {
      open: function () { _gameWindowActive = true; _hideChatForDrawingGuess(); },
      repeatOpen: function () { _hideChatForDrawingGuess(); },
      close: function () { _gameWindowActive = false; _restoreChatAfterDrawingGuess(); },
      snapshot: function () { return _drawingGuessChatHiddenSnap; }
    };
  `;
  vm.runInNewContext(instrumented, context, {
    filename: 'templates/chat.html#drawing-guess-visibility',
    timeout: 5000,
  });
  return {
    api: context.__drawingGuessVisibility,
    calls,
    flushVisibilityCommands() {
      while (visibilityCommands.length) applyVisibility(visibilityCommands.shift());
    },
    isVisible() { return bridgeVisible; },
  };
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function testVisibleChatIsHiddenAndRestored() {
  const { api, calls } = createHarness({ visible: true });
  api.open();
  await settle();
  assert(calls.filter((entry) => entry[0] === 'hide').length === 1,
    'a visible chat window was not hidden for the drawing game');
  assert(api.snapshot()?.wasVisible === true, 'the visible pre-game state was not latched');

  api.repeatOpen();
  await settle();
  api.close();
  assert(calls.filter((entry) => entry[0] === 'show').length === 1,
    'a chat window hidden by the drawing game was not restored');
}

async function testPreHiddenChatRemainsHidden() {
  for (const exposeAvailability of [true, false]) {
    const { api, calls } = createHarness({ visible: false, exposeAvailability });
    api.open();
    await settle();
    api.repeatOpen();
    await settle();
    api.close();

    assert(!calls.some((entry) => entry[0] === 'hide'),
      'the drawing flow claimed ownership of an already-hidden chat window');
    assert(!calls.some((entry) => entry[0] === 'show'),
      'closing the drawing game exposed a chat window that was hidden beforehand');
  }
}

async function testCloseBeforeBoundsSettlementDoesNotChangeVisibility() {
  const bounds = deferred();
  const { api, calls } = createHarness({ visible: true, getBounds: () => bounds.promise });
  api.open();
  api.close();
  bounds.resolve({ x: 10, y: 20, width: 420, height: 360 });
  await settle();

  assert(!calls.some((entry) => entry[0] === 'hide' || entry[0] === 'show'),
    'a stale bounds callback changed chat visibility after the game had closed');
}

async function testEarlierRoundCannotTakeOverReopenedGame() {
  for (const staleResult of ['resolve', 'reject']) {
    const firstBounds = deferred();
    const secondBounds = deferred();
    const requests = [firstBounds.promise, secondBounds.promise];
    let requestIndex = 0;
    const { api, calls } = createHarness({
      visible: true,
      getBounds: () => requests[requestIndex++],
    });

    api.open();
    api.close();
    api.open();
    if (staleResult === 'resolve') {
      firstBounds.resolve({ x: 1, y: 2, width: 300, height: 200 });
    } else {
      firstBounds.reject(new Error('stale_bounds_failure'));
    }
    await settle();
    assert(api.snapshot() === null && !calls.some((entry) => entry[0] === 'hide'),
      `a stale ${staleResult} callback took ownership of the reopened game`);

    secondBounds.resolve({ x: 30, y: 40, width: 500, height: 400 });
    await settle();
    assert(api.snapshot()?.bounds?.x === 30
      && calls.filter((entry) => entry[0] === 'hide').length === 1,
    'the current game round did not own its resolved chat bounds');
    api.close();
    assert(calls.filter((entry) => entry[0] === 'show').length === 1,
      'the reopened game did not restore its visible chat exactly once');
    assert(calls.some((entry) => entry[0] === 'setBounds'
      && entry[1] === 30 && entry[2] === 40 && entry[3] === 500 && entry[4] === 400),
    'the reopened game restored stale chat bounds');
  }
}

async function testPendingShowIsSupersededByImmediateReopen() {
  const harness = createHarness({ visible: true, deferVisibilityCommands: true });
  const { api, calls } = harness;

  api.open();
  await settle();
  harness.flushVisibilityCommands();
  assert(harness.isVisible() === false, 'the first game did not apply its queued hide');

  api.close();
  api.open();
  await settle();
  assert(calls.filter((entry) => entry[0] === 'hide').length === 2,
    'the reopened game did not supersede the preceding one-way show request');
  harness.flushVisibilityCommands();
  assert(harness.isVisible() === false,
    'a delayed show from the previous round exposed chat during the reopened game');

  api.close();
  harness.flushVisibilityCommands();
  assert(harness.isVisible() === true,
    'the reopened game did not restore the chat after its final close');
}

async function main() {
  await testVisibleChatIsHiddenAndRestored();
  await testPreHiddenChatRemainsHidden();
  await testCloseBeforeBoundsSettlementDoesNotChangeVisibility();
  await testEarlierRoundCannotTakeOverReopenedGame();
  await testPendingShowIsSupersededByImmediateReopen();
  process.stdout.write('chat drawing-guess visibility runtime tests passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});

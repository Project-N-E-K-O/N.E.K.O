const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const nativeDragPath = path.join(projectRoot, 'static/app/app-ui/return-window-drag.js');
const manualDragPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/methods-return.js');
const journeyPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js');
const vrmDragPath = path.join(projectRoot, 'static/vrm/vrm-ui-buttons.js');

function sourceBetween(filePath, startMarker, endMarker) {
  const source = fs.readFileSync(filePath, 'utf8');
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${path.basename(filePath)} helper slice not found`);
  return source.slice(start, end);
}

function assertFivePixelSegments(facts, expectedDurationMs) {
  assert.equal(facts.pathDistancePx, 10);
  assert.equal(facts.displacementPx, 10);
  assert.equal(facts.durationMs, expectedDurationMs);
  assert.ok(facts.activityId);
}

test('native and DOM drag producers aggregate motion once at the terminal event', () => {
  let now = 1000;
  const nativeContext = { state: {}, Date: { now: () => now }, Math };
  vm.createContext(nativeContext);
  vm.runInContext(sourceBetween(
    nativeDragPath,
    'function startReturnBallDragActivity',
    'function dispatchReturnBallRevealFailed'
  ), nativeContext);

  nativeContext.startReturnBallDragActivity(7, 0, 0);
  nativeContext.recordReturnBallDragActivityPoint(7, 3, 4);
  nativeContext.recordReturnBallDragActivityPoint(7, 6, 8);
  now = 1450;
  assertFivePixelSegments(nativeContext.finishReturnBallDragActivity(7, 6, 8), 450);
  assert.equal(nativeContext.finishReturnBallDragActivity(7, 6, 8), null);

  now = 2000;
  const domContext = { Date: { now: () => now }, Math };
  vm.createContext(domContext);
  vm.runInContext(
    `let dragActivity = null;\n${sourceBetween(
      manualDragPath,
      'const startDragActivity',
      'const finishDragState'
    )}\nthis.api = { startDragActivity, recordDragActivityPoint, finishDragActivity };`,
    domContext
  );

  domContext.api.startDragActivity(9, 0, 0);
  domContext.api.recordDragActivityPoint(3, 4);
  domContext.api.recordDragActivityPoint(6, 8);
  now = 2600;
  assertFivePixelSegments(domContext.api.finishDragActivity(9), 600);
  assert.equal(domContext.api.finishDragActivity(9), null);
});

test('VRM return-ball drag dispatches one terminal event with aggregated physical facts', () => {
  let now = 1000;
  let nextRafId = 1;
  const rafCallbacks = new Map();
  const documentListeners = new Map();
  const containerListeners = new Map();
  const terminalCalls = [];
  const rawEvents = [];
  const attributes = new Map();
  const returnButton = { style: {} };
  const container = {
    style: {},
    contains: () => true,
    getBoundingClientRect: () => ({ left: 100, top: 200, width: 64, height: 64 }),
    querySelector: () => returnButton,
    addEventListener: (name, handler) => containerListeners.set(name, handler),
    setAttribute: (name, value) => attributes.set(name, String(value)),
    getAttribute: (name) => attributes.get(name) || null
  };
  const context = {
    Math,
    Date: { now: () => now },
    VRMManager: function VRMManager() {},
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init.detail;
    },
    window: {
      innerWidth: 1280,
      innerHeight: 720,
      dispatchEvent: (event) => rawEvents.push(event)
    },
    document: {
      addEventListener: (name, handler) => documentListeners.set(name, handler),
      removeEventListener: (name) => documentListeners.delete(name)
    },
    requestAnimationFrame: (callback) => {
      const id = nextRafId++;
      rafCallbacks.set(id, callback);
      return id;
    },
    cancelAnimationFrame: (id) => rafCallbacks.delete(id),
    setTimeout: () => 0,
    _dispatchNekoIdleReturnBallManualMove: (target, reason, detail) => {
      terminalCalls.push({ target, reason, detail });
    }
  };
  const flushAnimationFrames = () => {
    const callbacks = Array.from(rafCallbacks.values());
    rafCallbacks.clear();
    callbacks.forEach((callback) => callback());
  };
  const mouseEvent = (x, y) => ({
    button: 0,
    target: container,
    clientX: x,
    clientY: y,
    preventDefault: () => {},
    stopImmediatePropagation: () => {}
  });

  vm.createContext(context);
  vm.runInContext(sourceBetween(
    vrmDragPath,
    'VRMManager.prototype._setupReturnButtonDrag = function',
    '/**\n * 添加"请她回来"按钮的呼吸灯动画效果'
  ), context);
  const manager = new context.VRMManager();
  manager._setupReturnButtonDrag(container);

  containerListeners.get('mousedown')(mouseEvent(10, 10));
  documentListeners.get('mousemove')(mouseEvent(13, 14));
  flushAnimationFrames();
  documentListeners.get('mousemove')(mouseEvent(16, 18));
  flushAnimationFrames();
  now = 1450;
  documentListeners.get('mouseup')();
  documentListeners.get('mouseup')();

  assert.equal(terminalCalls.length, 1);
  assert.equal(terminalCalls[0].target, container);
  assert.equal(terminalCalls[0].reason, 'return-ball-drag-end');
  assert.match(terminalCalls[0].detail.activityId, /^return-cat-drag-vrm:1000:1$/);
  assert.equal(terminalCalls[0].detail.pathDistancePx, 10);
  assert.equal(terminalCalls[0].detail.displacementPx, 10);
  assert.equal(terminalCalls[0].detail.durationMs, 450);
  assert.equal(terminalCalls[0].detail.movedDistancePx, 10);
  assert.equal(terminalCalls[0].detail.dragCancelled, false);
  assert.equal(rawEvents.filter((event) => event.detail.reason === 'return-ball-drag-active').length, 1);

  now = 2000;
  containerListeners.get('mousedown')(mouseEvent(20, 20));
  now = 2250;
  documentListeners.get('mouseup')();
  documentListeners.get('mouseup')();

  assert.equal(terminalCalls.length, 2);
  assert.equal(terminalCalls[1].reason, 'return-ball-drag-cancel');
  assert.match(terminalCalls[1].detail.activityId, /^return-cat-drag-vrm:2000:2$/);
  assert.equal(terminalCalls[1].detail.pathDistancePx, 0);
  assert.equal(terminalCalls[1].detail.displacementPx, 0);
  assert.equal(terminalCalls[1].detail.durationMs, 250);
  assert.equal(terminalCalls[1].detail.movedDistancePx, 0);
  assert.equal(terminalCalls[1].detail.dragCancelled, true);
});

test('cat walk producer aggregates actual step path once', () => {
  let now = 3000;
  const context = { Date: { now: () => now }, Math };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'let _nekoIdleCat1WalkActivitySequence',
    'function _setNekoIdleCat1PairMoveChatPosition'
  ), context);

  const state = {};
  context._beginNekoIdleCat1WalkActivity(state, { left: 0, top: 0 });
  context._appendNekoIdleCat1WalkActivityPoint(state, 3, 4);
  context._appendNekoIdleCat1WalkActivityPoint(state, 6, 8);
  now = 3900;
  assertFivePixelSegments(context._completeNekoIdleCat1WalkActivity(state), 900);
  assert.equal(context._completeNekoIdleCat1WalkActivity(state), null);
});

test('cat1 small move keeps plan facts after clearing its active plan', () => {
  const finishSource = sourceBetween(
    journeyPath,
    'function _finishNekoIdleCat1PairMove(button)',
    'function _stepNekoIdleCat1PairMove'
  );
  let reported = null;
  const profile = {
    idleSubstate: 'idle',
    assets: { idle: () => 'idle.gif' },
    tier: 'cat1'
  };
  const state = {
    pairMovePlan: {
      dx: 3,
      dy: 4,
      durationMs: 700,
      activityId: 'cat1_small_move:run-4'
    },
    pairMoveFrame: 3,
    profile,
    catMindRunId: 'cat1_small_move:run-4'
  };
  const context = {
    Math,
    _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW: profile,
    _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
    _applyNekoIdleCat1PairMovePlan: () => {},
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _resetNekoIdleCat1WalkSpeed: () => {},
    _setNekoIdleCat1Classes: () => {},
    _setNekoIdleReturnArtSource: () => {},
    _reportNekoCatMindStateActionResult: (_state, result, detail) => {
      reported = { result, detail };
    }
  };
  vm.createContext(context);
  vm.runInContext(finishSource, context);
  context._finishNekoIdleCat1PairMove({
    __nekoIdleReturnSubactionState: state,
    querySelector: () => null
  });

  assert.equal(state.pairMovePlan, null);
  assert.equal(reported.result, 'done');
  assert.deepEqual(JSON.parse(JSON.stringify(reported.detail.detail)), {
    restored: true,
    activityId: 'cat1_small_move:run-4',
    distancePx: 5,
    pathDistancePx: 5,
    plannedDurationMs: 700
  });
});

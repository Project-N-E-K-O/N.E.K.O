const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const reasons = ['start', 'active', 'motion', 'end', 'cancel'];

function sourceBetween(file, start, end) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `missing consumer boundary: ${file}`);
  return source.slice(first, last);
}

function createGravityConsumer(hasOwner = true) {
  const calls = [];
  const container = {};
  const owner = { container, button: {}, frame: 19, pointerHeld: false };
  const drags = new WeakMap();
  const context = vm.createContext({
    current: hasOwner ? owner : null,
    dragMotions: drags,
    window: { cancelAnimationFrame: id => calls.push(`cancel-frame:${id}`) },
    _createNekoIdleDragSampleSession: () => ({}),
    recordDragPoint: () => calls.push('sample'),
    releaseVelocity: drag => drag && drag.moved ? { vx: 123, vy: -45 } : null,
    requestContainerAdmission: () => calls.push('admit'),
    getBody: () => null,
    now: () => 1000,
    reconcilePausedOwner: () => calls.push('reconcile'),
    collide: () => calls.push('collide'),
    render: () => calls.push('render'),
    requestFrame: () => calls.push('frame'),
    publishMovementState: () => calls.push('publish'),
    cancel: () => calls.push('cancel-owner'),
  });
  vm.runInContext(sourceBetween(
    'static/avatar/avatar-ui-buttons/idle-desktop-window-gravity.js',
    '    function handleManualMove(event) {',
    '    function handlePlayground(event) {',
  ), context);
  return {
    calls, owner, container, drags,
    emit(reason, source) {
      context.handleManualMove({ detail: {
        container, reason: `return-ball-drag-${reason}`, source, dragSessionId: 7,
      } });
    },
  };
}

test('Playground manual facts do not pause, release or readmit the legacy gravity owner', () => {
  for (const hasOwner of [true, false]) {
    const runtime = createGravityConsumer(hasOwner);
    for (const reason of reasons) runtime.emit(reason, 'cat1-playground');
    assert.deepEqual(runtime.calls, []);
    assert.equal(runtime.drags.has(runtime.container), false);
    assert.equal(runtime.owner.pointerHeld, false);
    assert.equal(runtime.owner.frame, 19);
  }
});

test('ordinary manual facts retain legacy gravity hold, release velocity and rendering', () => {
  const runtime = createGravityConsumer();
  runtime.emit('start');
  assert.equal(runtime.owner.pointerHeld, true);
  assert.equal(runtime.owner.frame, 0);
  runtime.emit('active');
  runtime.emit('motion');
  runtime.emit('end');
  assert.deepEqual(runtime.calls, [
    'sample', 'cancel-frame:19', 'publish', 'cancel-owner', 'sample',
    'reconcile', 'collide', 'render', 'frame',
  ]);
  assert.equal(runtime.owner.pointerHeld, false);
  assert.equal(runtime.owner.vx, 123);
  assert.equal(runtime.owner.vy, -45);
  assert.equal(runtime.drags.has(runtime.container), false);
});

test('ordinary drag release still requests legacy admission when no owner exists', () => {
  const runtime = createGravityConsumer(false);
  runtime.emit('start', 'native-return-ball');
  runtime.emit('motion', 'native-return-ball');
  runtime.emit('end', 'native-return-ball');
  assert.deepEqual(runtime.calls, ['sample', 'sample', 'admit']);
});

function createDesktopBridgeConsumer() {
  const calls = [];
  const listeners = new Map();
  const container = { offsetWidth: 64, offsetHeight: 64 };
  const context = vm.createContext({
    window: { addEventListener: (name, callback) => listeners.set(name, callback) },
    clearLive2DPeekReturnBallEdgeAnchor: value => {
      assert.equal(value, container);
      calls.push('clear-anchor');
    },
    I: {
      getVisibleIdleReturnBallContainer: () => container,
      getReturnBallDragScreenRect: () => ({ left: 10, top: 20, width: 64, height: 64 }),
      scheduleIdleReturnBallDesktopDragState: () => calls.push('drag-state'),
      scheduleIdleReturnBallDesktopBridge: reason => calls.push(`bridge:${reason}`),
    },
  });
  vm.runInContext(sourceBetween(
    'static/app/app-ui/return-transitions.js',
    '    function syncIdleReturnBallDesktopStateFromManualMove(detail) {',
    "    window.addEventListener('neko:auto-goodbye:state-change', (event) => {",
  ), context);
  return {
    calls,
    emit(reason, source) {
      listeners.get('neko:return-ball-manual-move')({ detail: {
        container, reason: `return-ball-drag-${reason}`, source, screenX: 42, screenY: 52,
      } });
    },
  };
}

test('Playground manual facts neither clear ordinary edge anchors nor schedule its desktop bridge', () => {
  const runtime = createDesktopBridgeConsumer();
  for (const reason of reasons) runtime.emit(reason, 'cat1-playground');
  assert.deepEqual(runtime.calls, []);
});

test('ordinary manual facts retain edge anchor clearing and desktop bridge scheduling', () => {
  const runtime = createDesktopBridgeConsumer();
  runtime.emit('start');
  runtime.emit('active');
  runtime.emit('motion');
  runtime.emit('end');
  assert.deepEqual(runtime.calls, [
    'bridge:return-ball-drag-start', 'clear-anchor', 'bridge:return-ball-drag-active',
    'drag-state', 'bridge:return-ball-dragging', 'bridge:return-ball-drag-end',
  ]);
});

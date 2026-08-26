const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const coordinatorPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js'
);

function createRuntime(options = {}) {
  const listeners = new Map();
  const sharedListeners = new Set();
  const timers = new Map();
  let current = options.current || null;
  let now = 1000;
  let nextTimer = 1;
  const sensingContext = options.bridge === false ? null : {
    getCurrent: () => current,
    subscribe(listener) {
      sharedListeners.add(listener);
      return () => sharedListeners.delete(listener);
    },
  };
  const window = {
    nekoDesktopWindowSensingContext: sensingContext || undefined,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) {
      Array.from(listeners.get(event.type) || []).forEach((listener) => listener(event));
    },
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  };
  const context = vm.createContext({
    window,
    Object,
    Number,
    Map,
    Math,
    String,
    Date: { now: () => now },
  });
  vm.runInContext(fs.readFileSync(coordinatorPath, 'utf8'), context);
  return {
    window,
    emit(value) {
      current = value;
      Array.from(sharedListeners).forEach((listener) => listener(value));
    },
    emitEvent(type, detail) { window.dispatchEvent({ type, detail }); },
    flushTimers() {
      const callbacks = Array.from(timers.values());
      timers.clear();
      callbacks.forEach((callback) => callback());
    },
    advanceTime(durationMs) { now += Math.max(0, Number(durationMs) || 0); },
  };
}

function createRunner(kind, distancePx, priority, options = {}) {
  const calls = { handled: 0, started: 0, consumed: 0, rearmed: 0, cancelled: 0 };
  let active = options.active === true;
  return {
    calls,
    runner: {
      kind,
      priority,
      handleSensingResult() {
        calls.handled += 1;
        if (options.endActiveOnHandle) active = false;
        return options.eligible !== false;
      },
      getCandidate() {
        if (options.throwAt === 'getCandidate') throw new Error('candidate failed');
        const currentDistance = typeof distancePx === 'function' ? distancePx() : distancePx;
        return { targetKind: kind, distancePx: currentDistance };
      },
      startCandidate() {
        calls.started += 1;
        active = true;
        return true;
      },
      consumeOpportunity() {
        calls.consumed += 1;
        return true;
      },
      rearmOpportunity() {
        calls.rearmed += 1;
        return true;
      },
      isActive() { return active; },
      cancel() {
        if (!active) return false;
        active = false;
        calls.cancelled += 1;
        return true;
      },
    },
  };
}

test('the closest candidate starts and the other runner consumes the same opportunity', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 130, 0);
  const edge = createRunner('desktop-window-edge-peek', 80, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });

  assert.equal(top.calls.started, 0);
  assert.equal(top.calls.consumed, 1);
  assert.equal(edge.calls.started, 1);
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().activeKind, 'desktop-window-edge-peek');
});

test('top-edge priority resolves an equal-distance tie without listener-order dependence', () => {
  const runtime = createRuntime();
  const edge = createRunner('desktop-window-edge-peek', 100, 1);
  const top = createRunner('desktop-window-top-edge', 100, 0);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });

  assert.equal(top.calls.started, 1);
  assert.equal(edge.calls.started, 0);
  assert.equal(edge.calls.consumed, 1);
});

test('startup facts wait for both runners and are replayed without partial registration races', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 130, 0);
  const edge = createRunner('desktop-window-edge-peek', 80, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });
  assert.equal(top.calls.handled, 0);
  assert.equal(top.calls.started, 0);

  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.flushTimers();
  assert.equal(top.calls.handled, 1);
  assert.equal(edge.calls.handled, 1);
  assert.equal(edge.calls.started, 1);
});

test('the sensing selector only owns top-edge and edge-peek candidates', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 130, 0);
  const edge = createRunner('desktop-window-edge-peek', 80, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.flushTimers();

  assert.deepEqual(
    Array.from(runtime.window.NekoDesktopWindowInteractions.getState().registeredKinds),
    ['desktop-window-top-edge', 'desktop-window-edge-peek']
  );
});

test('a fact that ends one action cannot start another until the next sensing fact', () => {
  const runtime = createRuntime();
  const ending = createRunner('desktop-window-top-edge', 90, 0, { active: true, endActiveOnHandle: true });
  const waiting = createRunner('desktop-window-edge-peek', 70, 1);
  runtime.window.NekoDesktopWindowInteractions.register(ending.runner);
  runtime.window.NekoDesktopWindowInteractions.register(waiting.runner);

  runtime.emit({ sessionId: 's1', revision: 2 });
  assert.equal(waiting.calls.started, 0);
  runtime.emit({ sessionId: 's1', revision: 3 });
  assert.equal(waiting.calls.started, 1);
});

test('runner failures stop the revision and remain inspectable without changing the action cycle', () => {
  const runtime = createRuntime();
  const failing = createRunner('desktop-window-top-edge', 90, 0, { throwAt: 'getCandidate' });
  const waiting = createRunner('desktop-window-edge-peek', 70, 1);
  runtime.window.NekoDesktopWindowInteractions.register(failing.runner);
  runtime.window.NekoDesktopWindowInteractions.register(waiting.runner);

  runtime.emit({ sessionId: 's1', revision: 2 });

  const state = runtime.window.NekoDesktopWindowInteractions.getState();
  assert.equal(failing.calls.started, 0);
  assert.equal(waiting.calls.started, 0);
  assert.equal(state.lastRunnerFailure.stage, 'getCandidate');
  assert.equal(state.lastRunnerFailure.runnerKind, 'desktop-window-top-edge');
  assert.equal(state.lastRunnerFailure.message, 'candidate failed');
});

test('coordinator cancellation only affects the active desktop-window runner', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 80, 0);
  const edge = createRunner('desktop-window-edge-peek', 120, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });

  assert.equal(runtime.window.NekoDesktopWindowInteractions.cancel(null, { reason: 'return-click' }), true);
  assert.equal(top.calls.cancelled, 1);
  assert.equal(edge.calls.cancelled, 0);
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().activeKind, '');
});

test('dragging a cat out of a desktop presentation starts the shared 30-second cooldown', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 80, 0);
  const edge = createRunner('desktop-window-edge-peek', 120, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });
  assert.equal(top.calls.started, 1);

  runtime.emitEvent('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
  });
  runtime.emitEvent('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-active',
  });
  runtime.window.NekoDesktopWindowInteractions.cancel(null, { reason: 'return-ball-drag-active' });
  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().rearmOnNextFact, true);

  runtime.emit({ sessionId: 's1', revision: 2 });
  assert.equal(top.calls.rearmed, 0);
  assert.equal(edge.calls.rearmed, 0);
  assert.equal(top.calls.started, 1);

  runtime.advanceTime(30000);
  runtime.emit({ sessionId: 's1', revision: 3 });
  assert.equal(top.calls.rearmed, 1);
  assert.equal(edge.calls.rearmed, 1);
  assert.equal(top.calls.started, 2);
});

test('ordinary manual dragging outside a desktop presentation creates no presentation cooldown', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 80, 0, { eligible: false });
  const edge = createRunner('desktop-window-edge-peek', 120, 1, { eligible: false });
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);

  runtime.emitEvent('neko:return-ball-manual-move', { reason: 'return-ball-drag-start' });
  runtime.emitEvent('neko:return-ball-manual-move', { reason: 'return-ball-drag-active' });

  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().cooldownUntil, 0);
});

test('a new owner clears a pending desktop-presentation drag origin', () => {
  const runtime = createRuntime();
  const top = createRunner('desktop-window-top-edge', 80, 0);
  const edge = createRunner('desktop-window-edge-peek', 120, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });

  runtime.emitEvent('neko:return-ball-manual-move', { reason: 'return-ball-drag-start' });
  runtime.window.NekoDesktopWindowInteractions.cancel(null, { reason: 'return-click' });
  runtime.emitEvent('neko:return-ball-manual-move', { reason: 'return-ball-drag-active' });

  assert.equal(runtime.window.NekoDesktopWindowInteractions.getState().cooldownUntil, 0);
});

test('a completed presentation waits 30 seconds and then compares both runners again', () => {
  const runtime = createRuntime();
  let topDistance = 80;
  let edgeDistance = 120;
  const top = createRunner('desktop-window-top-edge', () => topDistance, 0);
  const edge = createRunner('desktop-window-edge-peek', () => edgeDistance, 1);
  runtime.window.NekoDesktopWindowInteractions.register(top.runner);
  runtime.window.NekoDesktopWindowInteractions.register(edge.runner);
  runtime.emit({ sessionId: 's1', revision: 1 });
  assert.equal(top.calls.started, 1);

  runtime.window.NekoDesktopWindowInteractions.completePresentation(30000);
  runtime.window.NekoDesktopWindowInteractions.cancel(null, { reason: 'completed-drop' });
  topDistance = 140;
  edgeDistance = 70;
  runtime.emit({ sessionId: 's1', revision: 2 });
  assert.equal(edge.calls.started, 0);

  runtime.advanceTime(30000);
  runtime.emit({ sessionId: 's1', revision: 3 });
  assert.equal(top.calls.rearmed, 1);
  assert.equal(edge.calls.rearmed, 1);
  assert.equal(top.calls.started, 1);
  assert.equal(edge.calls.started, 1);
});

test('ordinary web pages do not expose the desktop-window coordinator', () => {
  const runtime = createRuntime({ bridge: false });
  assert.equal(runtime.window.NekoDesktopWindowInteractions, undefined);
});

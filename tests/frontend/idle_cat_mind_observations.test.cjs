const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SOURCE_PATH = path.join(
    PROJECT_ROOT,
    'static',
    'avatar',
    'avatar-ui-buttons',
    'idle-cat-mind-observations.js'
);

function createHarness() {
    const listeners = new Map();
    const rafQueue = [];
    const timers = new Map();
    const dispatched = [];
    let nextTimerId = 1;
    const catContainer = {
        getBoundingClientRect() {
            return { left: 400, top: 100, width: 100, height: 100 };
        },
    };
    class CustomEvent {
        constructor(type, init) {
            this.type = type;
            this.detail = init && init.detail;
        }
    }
    const window = {
        NekoCatMindContract: {
            EVENT_NAMES: { OBSERVATION: 'neko:test-cat-mind-observation' },
        },
        screenX: 0,
        screenY: 0,
        addEventListener(type, listener) {
            const bucket = listeners.get(type) || [];
            bucket.push(listener);
            listeners.set(type, bucket);
        },
        dispatchEvent(event) {
            dispatched.push(event);
            for (const listener of listeners.get(event.type) || []) listener(event);
            return true;
        },
        requestAnimationFrame(callback) {
            rafQueue.push(callback);
            return rafQueue.length;
        },
        setTimeout(callback) {
            const id = nextTimerId++;
            timers.set(id, callback);
            return id;
        },
        clearTimeout(id) {
            timers.delete(id);
        },
    };
    const normalizeRect = (value) => {
        if (!value || typeof value !== 'object') return null;
        const left = Number(value.left);
        const top = Number(value.top);
        const width = Number(value.width);
        const height = Number(value.height);
        if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
        return { left, top, width, height };
    };
    const context = vm.createContext({
        console,
        CustomEvent,
        Date,
        Math,
        Object,
        window,
        _NEKO_CAT_IDLE_OBSERVATION_SOURCE_EVENT: 'neko:test-cat-mind-observation',
        _NEKO_IDLE_TIER_CAT1: 'cat1',
        _NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX: 180,
        _NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX: 14,
        _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX: 24,
        _normalizeNekoIdleScreenRect: normalizeRect,
        _findNekoCatMindVisibleButtonForTier() { return {}; },
        _getNekoIdleReturnContainerFromButton() { return catContainer; },
        _getNekoIdleCat1Target() {
            throw new Error('observation geometry must not call the mutating journey target helper');
        },
    });
    vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context, { filename: SOURCE_PATH });

    function emit(type, detail) {
        window.dispatchEvent(new CustomEvent(type, { detail }));
    }
    function emitMinimized(detail) {
        emit('neko:idle-chat-minimized-state', detail);
    }
    function flushOneRaf() {
        const callbacks = rafQueue.splice(0, rafQueue.length);
        callbacks.forEach((callback) => callback());
    }
    function observations() {
        return dispatched
            .filter((event) => event.type === 'neko:test-cat-mind-observation')
            .map((event) => event.detail);
    }
    function runTimers() {
        const callbacks = [...timers.values()];
        timers.clear();
        callbacks.forEach((callback) => callback());
    }
    function gate() {
        return JSON.parse(JSON.stringify(window.NekoCatMindYarnObservationAdapter.getGateSnapshot()));
    }
    function stableRect(coordinateSpace = 'screen') {
        const rect = vm.runInContext(`_nekoCatMindStableYarnRectBySpace[${JSON.stringify(coordinateSpace)}] || null`, context);
        return rect ? JSON.parse(JSON.stringify(rect)) : null;
    }
    return { window, emit, emitMinimized, flushOneRaf, observations, runTimers, gate, stableRect };
}

const FAR_RECT = { left: 0, top: 120, width: 40, height: 40 };
const NEAR_RECT = { left: 360, top: 120, width: 40, height: 40 };

test('end-only Wayland drag uses stable rect and dispatches after the journey RAF', () => {
    const harness = createHarness();
    harness.emitMinimized({
        minimized: true,
        reason: 'idle-dock',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    assert.deepEqual(harness.gate(), {
        yarnDragActive: false,
        yarnSettling: false,
    });

    harness.emitMinimized({
        source: 'neko-pc',
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 2000,
    });
    assert.equal(harness.observations().length, 0);
    assert.equal(harness.gate().yarnSettling, true);

    harness.flushOneRaf();
    assert.equal(harness.observations().length, 0);
    assert.equal(harness.gate().yarnSettling, true);
    harness.flushOneRaf();

    const [observation] = harness.observations();
    assert.equal(observation.type, 'chat_yarn_drag_completed');
    assert.equal(observation.detail.originSource, 'neko-pc');
    assert.equal(observation.detail.startedFarFromCat, true);
    assert.equal(observation.detail.endedNearCat, true);
    assert.equal(observation.detail.startDistanceToCatPx, 360);
    assert.equal(observation.detail.endDistanceToCatPx, 0);
    assert.equal(observation.detail.directApproachDistancePx, 360);
    assert.equal(harness.gate().yarnSettling, false);
});

test('settling fallback releases the gate once when animation frames are paused', () => {
    const harness = createHarness();
    harness.emitMinimized({
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emitMinimized({
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 2000,
    });
    assert.equal(harness.gate().yarnSettling, true);
    assert.equal(harness.observations().length, 0);

    harness.runTimers();
    assert.equal(harness.gate().yarnSettling, false);
    assert.equal(harness.observations().length, 1);

    harness.flushOneRaf();
    harness.flushOneRaf();
    assert.equal(harness.observations().length, 1, 'late RAF callbacks must not dispatch twice');
});

test('move-only sessions aggregate samples, terminal events deduplicate, and emit once', () => {
    const harness = createHarness();
    harness.emitMinimized({ minimized: true, reason: 'resize', screenRect: FAR_RECT, timestamp: 1000 });
    harness.emitMinimized({
        minimized: true,
        reason: 'self-ball-wayland-virtual-drag-move',
        screenRect: { left: 180, top: 120, width: 40, height: 40 },
        timestamp: 1100,
    });
    harness.emitMinimized({
        minimized: true,
        reason: 'self-ball-wayland-virtual-drag-move',
        screenRect: NEAR_RECT,
        timestamp: 1200,
    });
    assert.equal(harness.gate().yarnDragActive, true);
    assert.equal(harness.observations().length, 0);

    const terminal = {
        minimized: true,
        reason: 'self-ball-wayland-virtual-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 1300,
    };
    harness.emitMinimized(terminal);
    harness.emitMinimized({ ...terminal, timestamp: 1380 });
    harness.emitMinimized({ ...terminal, timestamp: 1560 });
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.pathDistancePx, 360);
});

test('all desktop terminal aliases support factual end-only completion', () => {
    for (const reason of [
        'ball-drag-end',
        'self-ball-drag-stop',
        'self-ball-wayland-drag-stop',
        'self-ball-wayland-virtual-drag-stop',
        'self-ball-force-release',
    ]) {
        const harness = createHarness();
        harness.emitMinimized({ minimized: true, reason: 'poll', screenRect: FAR_RECT, timestamp: 1000 });
        harness.emitMinimized({ minimized: true, reason, screenRect: NEAR_RECT, timestamp: 2000 });
        harness.flushOneRaf();
        harness.flushOneRaf();
        assert.equal(harness.observations().length, 1, reason);
        assert.equal(harness.observations()[0].detail.directApproachDistancePx, 360, reason);
    }
});

test('embedded viewport start/move/end phases remain one aggregate gesture', () => {
    const harness = createHarness();
    const emitPhase = (phase, screenRect, moved, timestamp) => {
        harness.emit('neko:chat-yarn-user-drag', {
            phase,
            sessionId: 'viewport-offer',
            source: 'react-chat-window',
            coordinateSpace: 'viewport',
            moved,
            screenRect,
            timestamp,
        });
    };
    emitPhase('start', FAR_RECT, false, 1000);
    emitPhase('move', { left: 180, top: 120, width: 40, height: 40 }, true, 1100);
    emitPhase('move', NEAR_RECT, true, 1200);
    assert.equal(harness.observations().length, 0);
    emitPhase('end', NEAR_RECT, true, 1300);
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.sessionId, 'viewport-offer');
    assert.equal(harness.observations()[0].detail.directApproachDistancePx, 360);
});

test('delayed explicit phases cannot sample or settle a replacement yarn session', () => {
    const harness = createHarness();
    const emitPhase = (sessionId, phase, screenRect, timestamp) => {
        harness.emit('neko:chat-yarn-user-drag', {
            phase,
            sessionId,
            source: 'react-chat-window',
            coordinateSpace: 'viewport',
            moved: phase !== 'start',
            screenRect,
            timestamp,
        });
    };

    emitPhase('session-a', 'start', FAR_RECT, 1000);
    emitPhase('session-b', 'start', FAR_RECT, 1100);
    emitPhase('session-a', 'move', NEAR_RECT, 1200);
    emitPhase('session-a', 'end', NEAR_RECT, 1300);

    assert.deepEqual(harness.gate(), {
        yarnDragActive: true,
        yarnSettling: false,
        sessionId: 'session-b',
    });
    assert.equal(harness.observations().length, 0);

    emitPhase('session-b', 'end', NEAR_RECT, 1400);
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.sessionId, 'session-b');
    assert.equal(harness.observations()[0].detail.pathDistancePx, 360);
});

test('cancel and blur release the gate without creating intent observations', () => {
    for (const reason of [
        'ball-drag-cancel',
        'self-ball-wayland-virtual-drag-cancel',
        'self-ball-wayland-virtual-drag-blur',
    ]) {
        const harness = createHarness();
        harness.emitMinimized({ minimized: true, reason: 'poll', screenRect: FAR_RECT, timestamp: 1000 });
        harness.emitMinimized({
            minimized: true,
            reason: 'self-ball-drag-move',
            screenRect: NEAR_RECT,
            timestamp: 1100,
        });
        harness.emitMinimized({ minimized: true, reason, screenRect: NEAR_RECT, timestamp: 1200 });
        harness.flushOneRaf();
        harness.flushOneRaf();
        assert.equal(harness.observations().length, 0, reason);
        assert.deepEqual(harness.gate(), {
            yarnDragActive: false,
            yarnSettling: false,
        }, reason);
    }
});

test('stale active drag releases its gate without synthesizing completion', () => {
    const harness = createHarness();
    harness.emit('neko:chat-yarn-user-drag', {
        phase: 'start',
        sessionId: 'lost-end',
        coordinateSpace: 'viewport',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    assert.deepEqual(harness.gate(), {
        yarnDragActive: true,
        yarnSettling: false,
        sessionId: 'lost-end',
    });

    harness.runTimers();

    assert.deepEqual(harness.gate(), {
        yarnDragActive: false,
        yarnSettling: false,
    });
    assert.equal(harness.observations().length, 0);
});

test('unavailable terminal immediately releases an active yarn drag without completion', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'self-ball-drag-move',
        screenRect: NEAR_RECT,
        timestamp: 1100,
    });
    assert.equal(harness.gate().yarnDragActive, true);
    assert.equal(harness.gate().yarnSettling, false);
    assert.match(harness.gate().sessionId, /^yarn-drag:/);

    harness.emitMinimized({
        available: false,
        minimized: false,
        reason: 'chat-user-closed',
        screenRect: null,
        timestamp: 1200,
    });

    assert.deepEqual(harness.gate(), {
        yarnDragActive: false,
        yarnSettling: false,
    });
    assert.equal(harness.observations().length, 0);

    harness.flushOneRaf();
    harness.flushOneRaf();
    harness.runTimers();
    assert.equal(harness.observations().length, 0, 'late callbacks must not synthesize completion');
});

test('compact surface unavailable terminal immediately releases the yarn gate', () => {
    const harness = createHarness();
    harness.emit('neko:chat-yarn-user-drag', {
        available: true,
        phase: 'start',
        sessionId: 'compact-pagehide',
        coordinateSpace: 'screen',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    assert.equal(harness.gate().yarnDragActive, true);

    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        visible: false,
        reason: 'pagehide',
        screenRect: null,
        timestamp: 1100,
    });

    assert.deepEqual(harness.gate(), {
        yarnDragActive: false,
        yarnSettling: false,
    });
    harness.flushOneRaf();
    harness.flushOneRaf();
    harness.runTimers();
    assert.equal(harness.observations().length, 0);
});

test('delayed unavailable terminal cannot clear a newer active or settling yarn session', () => {
    const harness = createHarness();
    const emitPhase = (phase, screenRect, timestamp) => {
        harness.emit('neko:chat-yarn-user-drag', {
            available: true,
            phase,
            sessionId: 'reopened-yarn',
            coordinateSpace: 'screen',
            moved: phase !== 'start',
            screenRect,
            timestamp,
        });
    };
    const staleUnavailable = {
        available: false,
        visible: false,
        reason: 'delayed-pagehide',
        screenRect: null,
        timestamp: 1000,
    };

    emitPhase('start', FAR_RECT, 2000);
    emitPhase('move', NEAR_RECT, 2100);
    harness.emit('neko:idle-chat-compact-surface-state', staleUnavailable);
    assert.deepEqual(harness.gate(), {
        yarnDragActive: true,
        yarnSettling: false,
        sessionId: 'reopened-yarn',
    });

    emitPhase('end', NEAR_RECT, 2200);
    assert.equal(harness.gate().yarnSettling, true);
    harness.emit('neko:idle-chat-compact-surface-state', staleUnavailable);
    assert.equal(harness.gate().yarnSettling, true);
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.sessionId, 'reopened-yarn');
});

test('delayed unavailable terminal cannot erase a newer stable rect before an end-only drag', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'pre-hide-poll',
        screenRect: FAR_RECT,
        timestamp: 500,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: true,
        reason: 'visibility-visible',
        screenRect: { left: 200, top: 80, width: 320, height: 64 },
        timestamp: 2000,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        visible: false,
        reason: 'delayed-pagehide',
        screenRect: null,
        timestamp: 1000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 3000,
    });
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.startedFarFromCat, true);
    assert.equal(harness.observations()[0].detail.endedNearCat, true);
});

test('positive yarn updates older than the lifecycle terminal stay retired', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        visible: false,
        reason: 'pagehide',
        screenRect: null,
        timestamp: 2000,
    });
    assert.equal(harness.stableRect(), null);

    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'delayed-poll',
        screenRect: FAR_RECT,
        timestamp: 1500,
    });
    harness.emit('neko:chat-yarn-user-drag', {
        available: true,
        phase: 'start',
        sessionId: 'delayed-start',
        coordinateSpace: 'screen',
        screenRect: FAR_RECT,
        timestamp: 1600,
    });

    assert.equal(harness.stableRect(), null);
    assert.deepEqual(harness.gate(), {
        yarnDragActive: false,
        yarnSettling: false,
    });
});

test('compact heartbeat does not advance the native yarn drag watermark', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: true,
        heartbeat: true,
        screenRect: { left: 20, top: 20, width: 300, height: 160 },
        timestamp: 5000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 2000,
    });
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.startedFarFromCat, true);
    assert.equal(harness.observations()[0].detail.endedNearCat, true);
});

test('minimized geometry polls do not advance the native yarn drag watermark', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 5000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 2000,
    });
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 1);
    assert.equal(harness.observations()[0].detail.startedFarFromCat, true);
    assert.equal(harness.observations()[0].detail.endedNearCat, true);
});

test('a reopened lifecycle still rejects drag phases older than its last terminal', () => {
    const harness = createHarness();
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 1000,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 2000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'poll',
        screenRect: FAR_RECT,
        timestamp: 3000,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'self-ball-wayland-drag-stop',
        screenRect: NEAR_RECT,
        timestamp: 1500,
    });
    harness.flushOneRaf();
    harness.flushOneRaf();

    assert.equal(harness.observations().length, 0);
    assert.deepEqual(harness.stableRect(), FAR_RECT);
});

test('yarn lifecycle sequence orders same-millisecond terminal and reopen updates', () => {
    const harness = createHarness();
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 6000,
        lifecycleSequence: 2,
    });
    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'delayed-poll',
        screenRect: FAR_RECT,
        timestamp: 6000,
        lifecycleSequence: 1,
    });
    assert.equal(harness.stableRect(), null);

    harness.emitMinimized({
        available: true,
        minimized: true,
        reason: 'reopen-poll',
        screenRect: FAR_RECT,
        timestamp: 6000,
        lifecycleSequence: 3,
    });
    assert.deepEqual(harness.stableRect(), FAR_RECT);

    harness.emit('neko:chat-yarn-user-drag', {
        available: true,
        phase: 'start',
        sessionId: 'same-millisecond-reopen',
        coordinateSpace: 'screen',
        screenRect: FAR_RECT,
        timestamp: 6000,
        lifecycleSequence: 3,
    });
    assert.equal(harness.gate().yarnDragActive, true,
        'a drag carrying the accepted reopen sequence supersedes the terminal watermark');
});

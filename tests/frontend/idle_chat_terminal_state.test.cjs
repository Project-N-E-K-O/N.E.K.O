const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const DRAG_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'avatar',
    'avatar-ui-buttons',
    'idle-drag-and-subactions.js'
);
const JOURNEY_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'avatar',
    'avatar-ui-buttons',
    'idle-journey-and-presentation.js'
);
const CHAT_GEOMETRY_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'app',
    'app-react-chat-window',
    'geometry-and-messages.js'
);
const INTERPAGE_BROADCAST_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'app',
    'app-interpage',
    'cross-window-broadcast-and-bridge.js'
);
const INTERPAGE_BOOTSTRAP_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'app',
    'app-interpage',
    'bootstrap-resources-and-model-reload.js'
);
const INTERPAGE_RELAY_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'app',
    'app-interpage',
    'guide-message-relay.js'
);
const INTERPAGE_LISTENERS_SOURCE = path.join(
    PROJECT_ROOT,
    'static',
    'app',
    'app-interpage',
    'listeners-and-api.js'
);

function readFunction(sourcePath, name) {
    const source = fs.readFileSync(sourcePath, 'utf8');
    const start = source.indexOf(`function ${name}`);
    assert.notEqual(start, -1, `missing function ${name}`);
    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `missing function body ${name}`);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`unterminated function ${name}`);
}

class CustomEventLike {
    constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
    }
}

class WindowLike {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
    }

    dispatchEvent(event) {
        for (const listener of this.listeners.get(event.type) || []) listener(event);
        return true;
    }
}

function createHarness() {
    let now = 10_000;
    const window = new WindowLike();
    const context = vm.createContext({
        console,
        CustomEvent: CustomEventLike,
        Date: { now: () => now },
        window,
        document: {
            getElementById() { return null; },
            querySelectorAll() { return []; },
        },
        _NEKO_IDLE_RETURN_BUTTON_SELECTOR: '.neko-idle-return-btn',
        _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX: 24,
        _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS: 10_000,
        _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS: 10_000,
        _NEKO_GOODBYE_IDLE_APPEARANCE_BALL: 'ball',
        _getNekoDesktopVirtualViewportOrigin() { return { x: 0, y: 0 }; },
        _getNekoIdleReactChatCompactSurfaceRect() { return null; },
        _getNekoIdleRectCenterMoveDistance() { return Infinity; },
        _isAnyNekoIdleCat1PlaygroundDropLifecycleActive() { return false; },
        _isNekoIdleCat1PlaygroundPairMoveFeedback() { return false; },
        _readNekoAutoGoodbyeVisualTier() { return 'cat1'; },
        _getNekoGoodbyeIdleAppearance() { return 'cat'; },
        _syncNekoIdleSleepSoundForTier() {},
        _syncNekoIdleCat1AmbientSoundForTier() {},
        _syncAllNekoIdleReturnButtons() {},
        _stopNekoGoodbyeIdleBallCatSounds() {},
    });

    const support = [
        '_getNekoIdleDesktopStateSourceUpdatedAt',
        '_getNekoIdleDesktopStateLifecycleSequence',
        '_compareNekoIdleDesktopStateOrder',
        '_isNekoIdleDesktopStateStaleAgainst',
        '_isNekoIdleDesktopStateNewerThan',
        '_makeNekoIdleDesktopChatMinimizedState',
        '_makeNekoIdleDesktopCompactSurfaceState',
    ].map((name) => readFunction(DRAG_SOURCE, name)).join('\n');
    const journey = [
        '_normalizeNekoIdleScreenRect',
        '_getNekoIdleDesktopCompactSurfaceRect',
        '_ensureNekoIdleReturnPresentationBridge',
    ].map((name) => readFunction(JOURNEY_SOURCE, name)).join('\n');

    vm.runInContext(`
        let _nekoIdleDesktopChatMinimizedState = {
            minimized: false,
            screenRect: null,
            updatedAt: 0,
            sourceUpdatedAt: 0,
            lifecycleSequence: 0,
            lifecycleTerminal: false,
            expandedRecent: false
        };
        let _nekoIdleDesktopCompactSurfaceState = {
            visible: false,
            screenRect: null,
            updatedAt: 0,
            sourceUpdatedAt: 0,
            lifecycleSequence: 0,
            lifecycleTerminal: false
        };
        let _nekoIdleCompactSurfaceDragging = false;
        function _handleNekoIdleCompactSurfaceMoveState(detail) {
            _nekoIdleCompactSurfaceDragging = !!(detail && (detail.dragging || detail.resizeActive));
        }
        ${support}
        ${journey}
        window.__getIdleChatTargetState = () => ({
            minimized: JSON.parse(JSON.stringify(_nekoIdleDesktopChatMinimizedState)),
            compact: JSON.parse(JSON.stringify(_nekoIdleDesktopCompactSurfaceState)),
            compactDragging: _nekoIdleCompactSurfaceDragging
        });
        window.__setIdleCompactDragging = (active) => { _nekoIdleCompactSurfaceDragging = !!active; };
        _ensureNekoIdleReturnPresentationBridge();
    `, context);

    function emit(type, detail) {
        window.dispatchEvent(new CustomEventLike(type, { detail }));
    }
    function snapshot() {
        return JSON.parse(JSON.stringify(window.__getIdleChatTargetState()));
    }
    return {
        emit,
        snapshot,
        setCompactDragging(active) { window.__setIdleCompactDragging(active); },
        setNow(value) { now = value; },
    };
}

const MINIMIZED_RECT = { left: 80, top: 120, width: 64, height: 64 };
const COMPACT_RECT = { left: 240, top: 180, width: 320, height: 180 };

test('compact lifecycle republish bypasses an unchanged geometry snapshot', () => {
    const published = [];
    const parts = {
        compactSurfaceAnchorSnapshot: '240:180:320:180:idle',
        isCompactHomeMinimizeBallEnabled() { return true; },
        getCurrentCompactSurfaceRect() { return COMPACT_RECT; },
        dispatchCompactSurfaceLayoutChange(detail) { published.push(detail); },
    };
    vm.runInNewContext(fs.readFileSync(CHAT_GEOMETRY_SOURCE, 'utf8'), {
        window: {
            __appReactChatWindowParts: parts,
            reactChatWindowHost: {},
        },
        document: {},
        console,
        Date,
        Math,
        Number,
        Object,
        Array,
        String,
        Boolean,
        JSON,
    }, { filename: CHAT_GEOMETRY_SOURCE });

    assert.equal(parts.republishCompactSurfaceLayoutChange('visibility-visible'), true);
    assert.deepEqual(JSON.parse(JSON.stringify(published)), [{
        ...COMPACT_RECT,
        reason: 'visibility-visible',
    }]);
});

test('persisted pageshow republishes and restarts compact target tracking', () => {
    const calls = [];
    const I = {
        isStandaloneChatPage() { return true; },
        isIdleChatSurfaceAvailable() { return true; },
    };
    const window = {
        reactChatWindowHost: {
            republishCompactSurfaceLayoutChange(reason) { calls.push(['republish', reason]); },
            scheduleCompactMinimizeBallTracking() { calls.push(['schedule']); },
        },
    };
    const document = { hidden: false };
    const restoreSource = readFunction(
        INTERPAGE_LISTENERS_SOURCE,
        'restoreIdleChatCompactSurfaceAfterPageShow'
    );
    const context = { I, window, document };

    vm.runInNewContext(`${restoreSource}\nrestoreIdleChatCompactSurfaceAfterPageShow({ persisted: false });`, context);
    assert.deepEqual(calls, []);
    vm.runInNewContext(`${restoreSource}\nrestoreIdleChatCompactSurfaceAfterPageShow({ persisted: true });`, context);
    assert.deepEqual(calls, [
        ['republish', 'pageshow-persisted'],
        ['schedule'],
    ]);
});

test('message dedup distinguishes same-millisecond lifecycle updates by sequence', () => {
    const dedupSource = readFunction(INTERPAGE_BOOTSTRAP_SOURCE, 'isDuplicateMessage');
    const context = vm.createContext({ setTimeout() {} });
    vm.runInContext(`
        var _processedMsgKeys = Object.create(null);
        ${dedupSource}
        globalThis.isDuplicateMessage = isDuplicateMessage;
    `, context);

    assert.equal(context.isDuplicateMessage('idle_chat_compact_surface_state', 1000, 1), false);
    assert.equal(context.isDuplicateMessage('idle_chat_compact_surface_state', 1000, 2), false);
    assert.equal(context.isDuplicateMessage('idle_chat_compact_surface_state', 1000, 2), true);
    assert.equal(context.isDuplicateMessage('legacy_action', 1000), false);
    assert.equal(context.isDuplicateMessage('legacy_action', 1000), true);
    assert.match(fs.readFileSync(INTERPAGE_RELAY_SOURCE, 'utf8'),
        /message\.timestamp,\s*message\.lifecycleSequence/);
});

test('pagehide cleanup keeps a failed compact terminal retryable until delivery succeeds', () => {
    const source = fs.readFileSync(INTERPAGE_BROADCAST_SOURCE, 'utf8');
    const lifecycleEnd = source.indexOf('    function scheduleYuiGuideChatMessageFlush');
    assert.notEqual(lifecycleEnd, -1);
    const lifecycleSource = `${source.slice(0, lifecycleEnd)}\n})();`;
    let attempts = 0;
    let available = true;
    let failNextPost = false;
    let heartbeatCallback = null;
    let terminalRetryCallback = null;
    let clearIntervalCalls = 0;
    let clearTimeoutCalls = 0;
    const attemptedMessages = [];
    const compactRepublishReasons = [];
    let compactTrackingSchedules = 0;
    const parts = {
        yuiGuideInterpageResources: {
            setInterval(callback) {
                heartbeatCallback = callback;
                return 1;
            },
            clearInterval() {
                clearIntervalCalls += 1;
                heartbeatCallback = null;
            },
            setTimeout(callback) {
                terminalRetryCallback = callback;
                return 2;
            },
            clearTimeout() {
                clearTimeoutCalls += 1;
                terminalRetryCallback = null;
            },
        },
        getCurrentLanlanName() { return 'test'; },
        isStandaloneChatPage() { return true; },
        clearYuiGuideChatFlushTimer() {},
        clearIcebreakerBridgeFlushTimer() {},
        clearYuiGuideChatSpotlightTracking() {},
    };
    const window = {
        appInterpage: {},
        __appInterpageParts: parts,
        reactChatWindowHost: {
            republishCompactSurfaceLayoutChange(reason) { compactRepublishReasons.push(reason); },
            scheduleCompactMinimizeBallTracking() { compactTrackingSchedules += 1; },
        },
        nekoChatWindow: { isIdleTargetAvailable() { return available; } },
        opener: null,
    };
    vm.runInNewContext(lifecycleSource, {
        window,
        document: { hidden: false },
        console,
        Date,
        Math,
        Number,
        Object,
        Array,
        String,
        Boolean,
        JSON,
    }, { filename: INTERPAGE_BROADCAST_SOURCE });
    parts.nekoBroadcastChannel = {
        postMessage(message) {
            attempts += 1;
            attemptedMessages.push(JSON.parse(JSON.stringify(message)));
            if (failNextPost) {
                failNextPost = false;
                throw new Error('transient broadcast failure');
            }
        },
    };

    assert.equal(parts.postIdleChatCompactSurfaceState({
        ...COMPACT_RECT,
        reason: 'visibility-visible',
    }), true);
    assert.deepEqual(attemptedMessages.at(-1).screenRect, COMPACT_RECT,
        'flat lifecycle geometry is normalized into the published screen rect');
    assert.equal(attemptedMessages.at(-1).visible, true);
    assert.equal(typeof heartbeatCallback, 'function');
    available = false;
    failNextPost = true;
    const cleanupSource = readFunction(
        INTERPAGE_LISTENERS_SOURCE,
        'cleanupAppInterpageTransientResources'
    );
    vm.runInNewContext(`${cleanupSource}\ncleanupAppInterpageTransientResources();`, { I: parts });
    assert.equal(clearIntervalCalls, 0, 'a failed terminal must keep the retry heartbeat alive');
    assert.equal(typeof terminalRetryCallback, 'function');
    const pagehideTerminalTimestamp = attemptedMessages.at(-1).timestamp;
    const retryHeartbeat = heartbeatCallback;
    available = true;
    retryHeartbeat();
    assert.equal(attemptedMessages.at(-1).available, false,
        'quick reopen cannot let a cached positive heartbeat overtake the pending terminal');
    assert.notEqual(attemptedMessages.at(-1).heartbeat, true);
    assert.equal(attemptedMessages.at(-1).timestamp, pagehideTerminalTimestamp,
        'positive heartbeat retries the pending terminal without refreshing its timestamp');
    assert.equal(clearIntervalCalls, 1, 'the successful retry retires the heartbeat');
    assert.equal(clearTimeoutCalls, 1, 'heartbeat delivery also retires the independent retry');
    const relaySource = readFunction(INTERPAGE_LISTENERS_SOURCE, 'relayIdleChatMinimizedState');
    const deliveredTerminal = attemptedMessages.at(-1);
    const attemptsAfterTerminal = attempts;
    vm.runInNewContext(`${relaySource}\nrelayIdleChatMinimizedState(event);`, {
        I: parts,
        window,
        event: {
            detail: {
                available: true,
                minimized: false,
                timestamp: deliveredTerminal.timestamp,
                lifecycleSequence: deliveredTerminal.lifecycleSequence - 1,
            },
        },
    });
    assert.equal(attempts, attemptsAfterTerminal,
        'an older same-millisecond positive cannot resume a delivered terminal');
    parts.postIdleChatCompactSurfaceUnavailable('duplicate-pagehide');
    assert.equal(attempts, 3, 'a delivered terminal may be deduplicated only after the successful retry');

    available = true;
    const reopenDetail = {
        available: true,
        minimized: false,
        timestamp: deliveredTerminal.timestamp,
        lifecycleSequence: deliveredTerminal.lifecycleSequence + 1,
    };
    window.__nekoIdleChatLifecycleSequence = reopenDetail.lifecycleSequence;
    failNextPost = true;
    vm.runInNewContext(`${relaySource}\nrelayIdleChatMinimizedState(event);`, {
        I: parts,
        window,
        event: { detail: reopenDetail },
    });
    assert.deepEqual(compactRepublishReasons, [],
        'a failed recovery relay must preserve the delivered terminal');
    assert.equal(compactTrackingSchedules, 0);
    vm.runInNewContext(`${relaySource}\nrelayIdleChatMinimizedState(event);`, {
        I: parts,
        window,
        event: { detail: reopenDetail },
    });
    assert.deepEqual(compactRepublishReasons, ['native-availability-restored']);
    assert.equal(compactTrackingSchedules, 1);
    available = false;
    assert.equal(parts.postIdleChatCompactSurfaceUnavailable('hidden-after-minimized-reopen'), true,
        'a successfully relayed minimized reopen invalidates terminal dedupe');
    assert.equal(attemptedMessages.at(-1).available, false);

    available = true;
    assert.equal(parts.postIdleChatCompactSurfaceState({
        screenRect: null,
        reason: 'compact-tracking-disabled',
    }), true);
    assert.equal(heartbeatCallback, null, 'tracking-disabled state has no positive heartbeat');
    available = false;
    failNextPost = true;
    assert.equal(parts.postIdleChatCompactSurfaceUnavailable('window-hidden-without-heartbeat'), false);
    assert.equal(typeof terminalRetryCallback, 'function', 'failed terminal schedules an independent retry');
    const staleRetryAfterReopen = terminalRetryCallback;
    available = true;
    const minimizedReopenDetail = {
        available: true,
        minimized: true,
        timestamp: attemptedMessages.at(-1).timestamp + 1,
        lifecycleSequence: parts.nextIdleChatLifecycleSequence(),
    };
    vm.runInNewContext(`${relaySource}\nrelayIdleChatMinimizedState(event);`, {
        I: parts,
        window,
        event: { detail: minimizedReopenDetail },
    });
    assert.equal(terminalRetryCallback, null, 'a reopened minimized lifecycle cancels pending terminal retry');
    available = false;
    failNextPost = true;
    assert.equal(parts.postIdleChatCompactSurfaceUnavailable('window-hidden-retry'), false);
    const retryWithoutHeartbeat = terminalRetryCallback;
    const attemptsAfterNewTerminal = attempts;
    staleRetryAfterReopen();
    assert.equal(attempts, attemptsAfterNewTerminal,
        'a captured stale retry callback cannot publish or consume a newer retry');
    assert.equal(terminalRetryCallback, retryWithoutHeartbeat);
    const failedTerminalTimestamp = attemptedMessages.at(-1).timestamp;
    terminalRetryCallback = null;
    retryWithoutHeartbeat();
    assert.equal(attemptedMessages.at(-1).timestamp, failedTerminalTimestamp,
        'terminal retry keeps the original lifecycle timestamp');
    parts.postIdleChatCompactSurfaceUnavailable('duplicate-after-independent-retry');
    assert.equal(attempts, 11, 'successful independent retry enables terminal deduplication');

    available = true;
    failNextPost = true;
    assert.equal(parts.postIdleChatCompactSurfaceState({
        screenRect: null,
        reason: 'recovery-without-heartbeat',
    }), false);
    assert.equal(typeof terminalRetryCallback, 'function',
        'a failed recovery without geometry also schedules an independent retry');
    const recoveryRetry = terminalRetryCallback;
    const failedRecovery = attemptedMessages.at(-1);
    terminalRetryCallback = null;
    recoveryRetry();
    assert.equal(attemptedMessages.at(-1).available, true);
    assert.equal(attemptedMessages.at(-1).timestamp, failedRecovery.timestamp,
        'recovery retry keeps the original lifecycle timestamp');
    assert.equal(heartbeatCallback, null);

    available = false;
    assert.equal(parts.postIdleChatCompactSurfaceUnavailable('closed-after-recovery'), true);
    available = true;
    failNextPost = true;
    assert.equal(parts.postIdleChatCompactSurfaceState({
        screenRect: null,
        reason: 'second-recovery-without-heartbeat',
    }), false);
    const supersededRecoveryRetry = terminalRetryCallback;
    available = false;
    assert.equal(parts.postIdleChatCompactSurfaceUnavailable('closed-before-recovery-retry'), true);
    assert.equal(attemptedMessages.at(-1).available, false,
        'a newer terminal replaces an undelivered recovery');
    const attemptsAfterSupersededRecovery = attempts;
    supersededRecoveryRetry();
    assert.equal(attempts, attemptsAfterSupersededRecovery,
        'the superseded recovery callback cannot revive the closed surface');
});

test('either unavailable terminal clears minimized and compact targets together', () => {
    for (const terminalType of [
        'neko:idle-chat-minimized-state',
        'neko:idle-chat-compact-surface-state',
    ]) {
        const harness = createHarness();
        harness.emit('neko:idle-chat-compact-surface-state', {
            available: true,
            visible: true,
            screenRect: COMPACT_RECT,
            timestamp: 1_000,
        });
        harness.emit('neko:idle-chat-minimized-state', {
            available: true,
            minimized: true,
            screenRect: MINIMIZED_RECT,
            timestamp: 2_000,
        });
        harness.setCompactDragging(true);
        harness.emit(terminalType, {
            available: false,
            minimized: false,
            visible: false,
            screenRect: null,
            timestamp: 3_000,
        });

        const state = harness.snapshot();
        assert.equal(state.minimized.minimized, false, terminalType);
        assert.equal(state.minimized.screenRect, null, terminalType);
        assert.equal(state.compact.visible, false, terminalType);
        assert.equal(state.compact.screenRect, null, terminalType);
        assert.equal(state.compactDragging, false, terminalType);
    }
});

test('delayed unavailable terminals cannot overwrite a reopened target', () => {
    const harness = createHarness();
    harness.emit('neko:idle-chat-minimized-state', {
        available: false,
        timestamp: 2_000,
    });
    harness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 4_000,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 3_000,
    });
    let state = harness.snapshot();
    assert.equal(state.minimized.minimized, true);
    assert.deepEqual(state.minimized.screenRect, {
        ...MINIMIZED_RECT,
        right: MINIMIZED_RECT.left + MINIMIZED_RECT.width,
        bottom: MINIMIZED_RECT.top + MINIMIZED_RECT.height,
    });

    harness.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: true,
        screenRect: COMPACT_RECT,
        timestamp: 6_000,
    });
    harness.emit('neko:idle-chat-minimized-state', {
        available: false,
        timestamp: 5_000,
    });
    state = harness.snapshot();
    assert.equal(state.compact.visible, true);
    assert.deepEqual(state.compact.screenRect, {
        ...COMPACT_RECT,
        right: COMPACT_RECT.left + COMPACT_RECT.width,
        bottom: COMPACT_RECT.top + COMPACT_RECT.height,
    });
});

test('lifecycle sequence orders same-millisecond terminal and reopen updates', () => {
    const harness = createHarness();
    harness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 9_000,
        lifecycleSequence: 1,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 9_000,
        lifecycleSequence: 2,
    });
    harness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 9_000,
        lifecycleSequence: 1,
    });
    let state = harness.snapshot();
    assert.equal(state.minimized.minimized, false, 'delayed pre-terminal positive stays retired');
    assert.equal(state.compact.visible, false);

    harness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 9_000,
        lifecycleSequence: 3,
    });
    state = harness.snapshot();
    assert.equal(state.minimized.minimized, true, 'newer same-millisecond sequence can reopen');

    const legacyHarness = createHarness();
    legacyHarness.emit('neko:idle-chat-minimized-state', { available: false, timestamp: 10_000 });
    legacyHarness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 10_000,
    });
    assert.equal(legacyHarness.snapshot().minimized.minimized, false,
        'equal-time legacy positive cannot overtake a terminal');
});

test('a catch-up heartbeat advances recovered compact lifecycle ordering', () => {
    const harness = createHarness();
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 1000,
        lifecycleSequence: 1,
    });
    harness.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: false,
        timestamp: 1100,
        lifecycleSequence: 2,
    });
    harness.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: true,
        heartbeat: true,
        screenRect: COMPACT_RECT,
        timestamp: 3000,
        lifecycleSequence: 3,
    });

    let state = harness.snapshot();
    assert.equal(state.compact.visible, true);
    assert.equal(state.compact.sourceUpdatedAt, 3000);
    assert.equal(state.compact.lifecycleSequence, 3);
    assert.equal(state.compact.lifecycleTerminal, false);

    harness.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 2000,
        lifecycleSequence: 2,
    });
    state = harness.snapshot();
    assert.equal(state.compact.visible, true, 'a delayed pre-recovery terminal stays retired');
});

test('inactive cross-stream watermarks reject delayed terminals and intermediate positives', () => {
    const minimizedInactive = createHarness();
    minimizedInactive.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: false,
        timestamp: 4_000,
    });
    minimizedInactive.emit('neko:idle-chat-compact-surface-state', {
        available: false,
        timestamp: 3_000,
    });
    minimizedInactive.emit('neko:idle-chat-minimized-state', {
        available: true,
        minimized: true,
        screenRect: MINIMIZED_RECT,
        timestamp: 3_500,
    });
    let state = minimizedInactive.snapshot();
    assert.equal(state.minimized.minimized, false);
    assert.equal(state.minimized.sourceUpdatedAt, 4_000);

    const compactInactive = createHarness();
    compactInactive.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: false,
        timestamp: 8_000,
    });
    compactInactive.emit('neko:idle-chat-minimized-state', {
        available: false,
        timestamp: 7_000,
    });
    compactInactive.emit('neko:idle-chat-compact-surface-state', {
        available: true,
        visible: true,
        screenRect: COMPACT_RECT,
        timestamp: 7_500,
    });
    state = compactInactive.snapshot();
    assert.equal(state.compact.visible, false);
    assert.equal(state.compact.sourceUpdatedAt, 8_000);
});

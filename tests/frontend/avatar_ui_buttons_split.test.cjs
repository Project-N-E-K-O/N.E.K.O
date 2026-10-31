const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const PARTS_DIR = path.join(PROJECT_ROOT, 'static', 'avatar', 'avatar-ui-buttons');
const PART_NAMES = [
    'cat-resource-registry.js',
    'core.js',
    'idle-assets-and-question.js',
    'idle-playground.js',
    'idle-actions-and-audio.js',
    'idle-drag-and-subactions.js',
    'idle-journey-and-presentation.js',
    'idle-cat-mind-observations.js',
    'methods-setup.js',
    'methods-buttons.js',
    'methods-return.js',
    'methods-state-and-cleanup.js',
];
const STANDALONE_PART_NAMES = [
    'edge-peek-controller.js',
    'idle-desktop-window-edge-peek.js',
    'idle-desktop-window-gravity.js',
    'idle-desktop-window-interactions.js',
    'idle-desktop-window-top-edge.js',
];
const EXPECTED_METHOD_NAMES = [
    '_addReturnButtonBreathingAnimation',
    '_setupReturnButtonDrag',
    '_syncButtonStatesWithGlobalState',
    'cleanupFloatingButtons',
    'createButtonElement',
    'createMicMuteButton',
    'createReturnButton',
    'createScreenShareQuickButton',
    'createVoiceSessionQuickControls',
    'getDefaultButtonConfigs',
    'resetAllButtons',
    'setButtonActive',
    'setupFloatingButtonsBase',
    'syncResponsiveButtonVisibility',
    'updateSeparatePopupTriggerIcon',
];

function loadMixin() {
    const listeners = new Map();
    const document = {
        currentScript: { src: 'http://127.0.0.1/static/avatar/avatar-ui-buttons/core.js?v=test' },
        getElementById() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        removeEventListener() {},
    };
    const window = {
        location: { href: 'http://127.0.0.1/' },
        addEventListener(type, listener) { listeners.set(type, listener); },
        removeEventListener(type) { listeners.delete(type); },
    };
    const cancelledAnimationFrames = [];
    const context = vm.createContext({
        URL,
        clearInterval,
        clearTimeout,
        console,
        document,
        Map,
        Object,
        setInterval,
        setTimeout,
        window,
        cancelAnimationFrame(id) { cancelledAnimationFrames.push(id); },
        requestAnimationFrame() { return 1; },
    });

    for (const name of PART_NAMES) {
        const source = fs.readFileSync(path.join(PARTS_DIR, name), 'utf8');
        vm.runInContext(source, context, { filename: name });
    }
    vm.runInContext('globalThis.__avatarButtonMixin = AvatarButtonMixin;', context);
    return { mixin: context.__avatarButtonMixin, cancelledAnimationFrames, window, listeners, context };
}

test('provider itself rejects busy CAT1 presentation and allows it again after settling', () => {
    const { context } = loadMixin();
    let tier = 'cat1';
    const state = { profile: { idleSubstate: 'idle' }, substate: 'idle', actionSettled: true };
    const button = { __nekoIdleCat1Journey: state, querySelector: () => ({}) };
    Object.assign(context, {
        _getActiveNekoIdleReturnTier: () => tier,
        _isNekoCatMindButtonContainerVisible: () => true,
        _isAnyNekoCatMindReturnPending: () => false,
        _isNekoCatMindTransitionActive: () => false,
        _isNekoIdleCompactSurfaceDragging: () => false,
        _isAnyNekoIdleCat1IndependentActionActive: () => false,
        _isNekoCatMindAudioActionActive: () => false,
        _isNekoIdleCat1EdgePeekActive: () => false,
        isNekoIdleCatAudioEnabled: () => true,
        _isNekoCatMindCat1NearChat: () => true,
        _isNekoIdleReturnDragActionBlocking: () => false,
        _isAnyNekoIdleReturnDragActionBlocking: () => false,
    });
    const evaluate = () => context._evaluateNekoCatMindActionProvider('cat1_eat_snack', { button });
    assert.equal(evaluate().allowed, true);
    for (const busy of [{ frame: 1 }, { paused: true }, { pendingWalkReady: true }, { pairMovePlan: {} }]) {
        Object.assign(state, busy);
        assert.equal(evaluate().allowed, false);
        assert.equal(evaluate().reason, 'cat1_position_presentation_busy');
        for (const key of Object.keys(busy)) delete state[key];
        assert.equal(evaluate().allowed, true);
    }
    tier = 'cat2';
    state.frame = 1;
    vm.runInContext("_nekoIdleSleepSoundState.tier = 'cat2';", context);
    assert.equal(context._evaluateNekoCatMindActionProvider('cat2_nap_feedback', { button }).allowed, true);
});

test('avatar button parts install the unchanged method contract for every backend', () => {
    const discoveredParts = fs.readdirSync(PARTS_DIR)
        .filter((name) => name.endsWith('.js'))
        .sort();
    assert.deepEqual(discoveredParts, [...PART_NAMES, ...STANDALONE_PART_NAMES].sort());

    const { mixin } = loadMixin();
    for (const prefix of ['live2d', 'vrm', 'mmd']) {
        const prototype = {};
        mixin.apply(prototype, prefix, {});
        const methodNames = Object.keys(prototype)
            .filter((name) => typeof prototype[name] === 'function')
            .sort();
        assert.deepEqual(methodNames, EXPECTED_METHOD_NAMES, prefix);
    }
});

test('cleanup still cancels and clears the active floating-button animation frame', () => {
    const { mixin, cancelledAnimationFrames } = loadMixin();
    const prototype = {};
    mixin.apply(prototype, 'live2d', {});
    const manager = Object.create(prototype);
    manager._uiUpdateLoopId = 73;
    manager._uiWindowHandlers = [];

    manager.cleanupFloatingButtons();

    assert.deepEqual(cancelledAnimationFrames, [73]);
    assert.equal(manager._uiUpdateLoopId, null);
    assert.equal(manager._updateFloatingButtonsPositionNow, null);
});

test('Cat Mind dry-run rejects missing action resources before runtime execution gates', () => {
    const { window, listeners } = loadMixin();
    const requests = [];
    const acknowledgements = [];
    window.NekoCatResourceRegistry = {
        getActionCapabilities(actionId) {
            requests.push(actionId);
            return { available: false, reason: 'voice_unavailable' };
        },
    };

    const decision = window.NekoCatMindActionProviders.dryRun('cat1_eat_snack');
    assert.equal(decision.allowed, false);
    assert.equal(decision.reason, 'voice_unavailable');
    assert.equal(decision.detail.resourceCapability.reason, 'voice_unavailable');
    assert.deepEqual(requests, ['cat1_eat_snack']);

    window.nekoCatMind = {
        acknowledgeActionRequest(payload) {
            acknowledgements.push(payload);
            return true;
        },
    };
    const requestListener = listeners.get('neko:cat-mind:action-request');
    assert.equal(typeof requestListener, 'function');
    requestListener({ detail: {
        source: 'cat_mind', requestId: 'missing-resource-request',
        actionId: 'cat1_eat_snack', tier: 'cat1',
    }});
    assert.deepEqual(acknowledgements.map(({ status, reason }) => ({ status, reason })), [
        { status: 'rejected', reason: 'voice_unavailable' },
    ]);
});

test('Cat Mind dry-run applies the resource preflight to every registered action', () => {
    const { window } = loadMixin();
    const actionIds = [
        'cat1_social_ping',
        'cat1_eat_snack',
        'cat1_small_move',
        'cat1_play_yarn',
        'cat2_nap_feedback',
        'cat3_sleep_feedback',
        'cat1_hiss_stretch',
    ];
    const requests = [];
    window.NekoCatResourceRegistry = {
        getActionCapabilities(actionId) {
            requests.push(actionId);
            return { available: false, reason: 'appearance_unavailable' };
        },
    };

    for (const actionId of actionIds) {
        const decision = window.NekoCatMindActionProviders.dryRun(actionId);
        assert.equal(decision.allowed, false, actionId);
        assert.equal(decision.reason, 'appearance_unavailable', actionId);
    }
    assert.deepEqual(requests, actionIds);
});

test('user yarn runner waits for PetCare lease authorization and preserves activity identity', async () => {
    const { window, listeners, context } = loadMixin();
    const acknowledgements = [];
    const runs = [];
    let releaseAuthorization;
    const authorization = new Promise((resolve) => { releaseAuthorization = resolve; });
    window.nekoPetCareEntry = {
        authorizeCatMindYarnAction(request) {
            assert.equal(request.detail.activityId, 'chat-yarn-session-1');
            return authorization;
        },
    };
    window.nekoCatMind = {
        acknowledgeActionRequest(payload) {
            acknowledgements.push(payload);
            return true;
        },
    };
    context._dryRunNekoCatMindActionProvider = () => ({ allowed: true, reason: 'ready' });
    context._findNekoCatMindVisibleButtonForTier = () => ({ id: 'cat1-button' });
    context._playNekoIdleCat1PlayAction = (_button, options) => {
        runs.push(options);
        options.onAccepted({ runId: 'yarn-run-1' });
        options.onStarted({ runId: 'yarn-run-1' });
        return true;
    };

    const requestListener = listeners.get('neko:cat-mind:action-request');
    requestListener({ detail: {
        source: 'cat_mind', requestId: 'yarn-request-1',
        actionId: 'cat1_play_yarn', tier: 'cat1',
        detail: { activityId: 'chat-yarn-session-1' },
    }});
    assert.equal(runs.length, 0);
    assert.equal(acknowledgements[0].status, 'deferred');

    releaseAuthorization({ authorized: true, minimumDurationMs: 8000 });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(runs.length, 1);
    assert.equal(runs[0].activityId, 'chat-yarn-session-1');
    assert.equal(runs[0].minimumDurationMs, 8000);
    assert.deepEqual(acknowledgements.map(({ status, runId }) => ({ status, runId })), [
        { status: 'deferred', runId: '' },
        { status: 'deferred', runId: '' },
        { status: 'accepted', runId: 'yarn-run-1' },
        { status: 'started', runId: 'yarn-run-1' },
    ]);
});

test('user yarn authorization remains fail-closed and legacy boolean success adds no local floor', async () => {
    const { window, listeners, context } = loadMixin();
    const acknowledgements = [];
    const runs = [];
    const authorizations = [
        () => Promise.resolve(false),
        () => Promise.resolve({ authorized: true, minimumDurationMs: 0 }),
        () => Promise.reject(new Error('lease unavailable')),
        () => Promise.resolve(true),
        () => Promise.resolve({ authorized: true, minimumDurationMs: 29000 }),
    ];
    window.nekoPetCareEntry = {
        authorizeCatMindYarnAction() { return authorizations.shift()(); },
    };
    window.nekoCatMind = {
        acknowledgeActionRequest(payload) {
            acknowledgements.push(payload);
            return true;
        },
    };
    context._dryRunNekoCatMindActionProvider = () => ({ allowed: true, reason: 'ready' });
    context._findNekoCatMindVisibleButtonForTier = () => ({ id: 'cat1-button' });
    context._playNekoIdleCat1PlayAction = (_button, options) => {
        runs.push(options);
        return true;
    };
    const requestListener = listeners.get('neko:cat-mind:action-request');
    const request = (suffix) => requestListener({ detail: {
        source: 'cat_mind', requestId: `yarn-request-${suffix}`,
        actionId: 'cat1_play_yarn', tier: 'cat1',
        detail: { activityId: `chat-yarn-session-${suffix}` },
    }});

    request('rejected');
    await new Promise((resolve) => setImmediate(resolve));
    request('invalid-floor');
    await new Promise((resolve) => setImmediate(resolve));
    request('failed');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(runs.length, 0);
    assert.equal(acknowledgements.filter((item) => item.status === 'rejected').length, 3);

    request('legacy');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(runs.length, 1);
    assert.equal(Object.hasOwn(runs[0], 'minimumDurationMs'), false);

    request('lease-floor');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(runs.length, 2);
    assert.equal(runs[1].minimumDurationMs, 29000);
});

test('desktop exploration art revalidates custom-event asset URLs before assigning src', () => {
    const { window, listeners, context } = loadMixin();
    const attributes = new Map([
        ['src', '/static/assets/neko-idle/original.png'],
        ['srcset', '/static/assets/neko-idle/original@2x.png 2x'],
    ]);
    const art = {
        src: '/static/assets/neko-idle/original.png',
        dataset: {},
        style: {},
        getAttribute(name) { return attributes.get(name) || ''; },
        setAttribute(name, value) { attributes.set(name, String(value)); },
        removeAttribute(name) { attributes.delete(name); },
    };
    const buttonAttributes = new Map([['data-neko-idle-tier', 'cat1']]);
    const button = {
        dataset: {},
        querySelector(selector) {
            return selector === '.neko-idle-return-art' ? art : null;
        },
        getAttribute(name) { return buttonAttributes.get(name) || ''; },
        setAttribute(name, value) { buttonAttributes.set(name, String(value)); },
    };
    const container = {
        dataset: {},
        getAttribute(name) { return name === 'data-neko-return-visible' ? 'true' : ''; },
    };
    window.nekoPetCareEntry = { getState() { return {
        catLocalActive: true,
        explorationRecallRequested: false,
        explorationPending: null,
        exploration: { exploration_id: 'exp-asset-1', active: true, status: 'active' },
    }; } };
    Object.assign(context, {
        _forEachNekoIdleReturnButton(callback) { callback(button); },
        _getNekoIdleReturnContainerFromButton() { return container; },
        _cleanupNekoIdleArtTransition() {},
        _clearNekoIdleHoverPlayback() {},
        _clearNekoIdleGifPlaybackSource() {},
    });

    const listener = listeners.get('neko:pet-care:exploration-asset');
    assert.equal(typeof listener, 'function');
    listener({ detail: {
        active: true, exploration_id: 'exp-asset-1',
        asset_url: '/\\cdn.example/exploration.png',
    } });
    assert.equal(art.src, '/static/assets/neko-idle/cat-bad.png');

    listener({ detail: {
        active: true, exploration_id: 'exp-asset-1',
        asset_url: '/static/assets/neko-idle/exploration-window-path.svg',
    } });
    assert.equal(art.src, '/static/assets/neko-idle/exploration-window-path.svg');
});

test('right-click petting is gated by the explicit unlock marker and leaves left-click return intact', () => {
    const methodsSource = fs.readFileSync(path.join(PARTS_DIR, 'methods-return.js'), 'utf8');
    const contextMenuStart = methodsSource.indexOf("returnBtn.addEventListener('contextmenu'");
    const thoughtBubbleStart = methodsSource.indexOf('const thoughtBubble =', contextMenuStart);
    assert.ok(contextMenuStart >= 0 && thoughtBubbleStart > contextMenuStart);
    const contextMenuSection = methodsSource.slice(contextMenuStart, thoughtBubbleStart);
    assert.match(contextMenuSection, /event\.preventDefault\(\)/);
    assert.match(contextMenuSection, /_isNekoIdleCat1PettingEnabledForButton\(returnBtn\)/);
    assert.match(contextMenuSection, /_requestNekoIdleCat1PettingPresentation\(returnBtn\)/);

    const clickStart = methodsSource.indexOf("returnBtn.addEventListener('click'");
    const clickSection = methodsSource.slice(clickStart, contextMenuStart);
    assert.match(clickSection, /_dispatchNekoIdleReturnClickFromButton\(returnBtn\)/);
    assert.doesNotMatch(clickSection, /_requestNekoIdleCat1PettingPresentation/);

    const mouseStart = methodsSource.indexOf("container.addEventListener('mousedown'");
    const mouseEnd = methodsSource.indexOf("container.addEventListener('touchstart'", mouseStart);
    const mouseSection = methodsSource.slice(mouseStart, mouseEnd);
    assert.match(mouseSection,
        /e\.button === 2 && _isNekoIdleCat1PettingEnabledForButton\(button\)\) return;/);
});

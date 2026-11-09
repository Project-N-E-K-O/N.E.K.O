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
    'collision-deformation.js',
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
        dispatchEvent(event) {
            const listener = listeners.get(event && event.type);
            if (typeof listener === 'function') listener.call(this, event);
            return true;
        },
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
        'cat1_idle_random',
        'cat1_inactive_sad',
        'cat2_nap_feedback',
        'cat3_sleep_feedback',
        'cat1_happy',
        'cat1_touch',
        'cat1_cosplay_1',
        'cat1_change',
        'cat1_cosplay_2',
        'cat1_eat_2',
        'cat1_eat_3',
        'cat1_laugh',
        'cat1_lion_dance',
        'cat1_love_1',
        'cat1_mouse',
        'cat1_wave',
        'cat1_sad',
        'cat1_scared',
        'cat1_stroke',
        'cat1_wagging',
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

test('idle-random missing-art diagnostics match the provider rejection', () => {
    const { window, context } = loadMixin();
    const container = { style: { display: 'block' }, getAttribute: () => '' };
    const button = {
        __nekoIdleCat1Journey: { profile: { idleSubstate: 'idle' }, substate: 'idle', actionSettled: true },
        querySelector: () => null,
    };
    window.NekoCatResourceRegistry = {
        getActionCapabilities: () => ({ available: true, reason: 'allowed' }),
    };
    Object.assign(context, {
        _findNekoCatMindVisibleButtonForTier: () => button,
        _getNekoIdleReturnContainerFromButton: () => container,
        _getActiveNekoIdleReturnTier: () => 'cat1',
        _isNekoCatMindButtonContainerVisible: () => true,
        _isAnyNekoCatMindReturnPending: () => false,
        _isNekoCatMindTransitionActive: () => false,
        _isNekoIdleCompactSurfaceDragging: () => false,
        _isAnyNekoIdleCat1IndependentActionActive: () => false,
        _isNekoCatMindAudioActionActive: () => false,
        _isNekoIdleCat1EdgePeekActive: () => false,
        _isNekoIdleReturnDragActionBlocking: () => false,
        _isAnyNekoIdleReturnDragActionBlocking: () => false,
    });

    const decision = window.NekoCatMindActionProviders.dryRun('cat1_idle_random');
    assert.equal(decision.allowed, false);
    assert.equal(decision.reason, 'missing_art');
    assert.deepEqual(
        Array.from(decision.detail.checks)
            .filter((check) => check.id === 'return_art_ready')
            .map((check) => ({ id: check.id, passed: check.passed })),
        [{ id: 'return_art_ready', passed: false }],
    );
});

test('visual-only actions stay callable presentations without becoming Cat Mind actions', () => {
    const { window, context } = loadMixin();
    const requests = [];
    const presentationCalls = [];
    context._requestNekoIdleCat1VisualActionPresentation = (key, options) => {
        presentationCalls.push([key, options && options.auditId]);
        return true;
    };
    window.NekoCatResourceRegistry = {
        getActionCapabilities(actionId) {
            requests.push(actionId);
            return { available: true, reason: '' };
        },
    };

    const visualActions = [
        ['happy', 'cat1_happy', 'requestCat1Happy'],
        ['touch', 'cat1_touch', 'requestCat1Touch'],
        ['cosplay1', 'cat1_cosplay_1', 'requestCat1Cosplay1'],
        ['change', 'cat1_change', 'requestCat1Change'],
        ['cosplay2', 'cat1_cosplay_2', 'requestCat1Cosplay2'],
        ['eat2', 'cat1_eat_2', 'requestCat1Eat2'],
        ['eat3', 'cat1_eat_3', 'requestCat1Eat3'],
        ['laugh', 'cat1_laugh', 'requestCat1Laugh'],
        ['lionDance', 'cat1_lion_dance', 'requestCat1LionDance'],
        ['love1', 'cat1_love_1', 'requestCat1Love1'],
        ['mouse', 'cat1_mouse', 'requestCat1Mouse'],
        ['wave', 'cat1_wave', 'requestCat1Wave'],
        ['sad', 'cat1_sad', 'requestCat1Sad'],
        ['scared', 'cat1_scared', 'requestCat1Scared'],
        ['stroke', 'cat1_stroke', 'requestCat1Stroke'],
        ['wagging', 'cat1_wagging', 'requestCat1Wagging'],
    ];
    for (const [key, actionId, requestName] of visualActions) {
        assert.equal(typeof window.NekoCatIdlePresentation[requestName], 'function', requestName);
        assert.equal(window.NekoCatIdlePresentation[requestName]({ auditId: actionId }), true, requestName);
        const decision = window.NekoCatMindActionProviders.dryRun(actionId);
        assert.equal(decision.allowed, false, actionId);
        assert.equal(decision.reason, 'unknown_action', actionId);
    }
    assert.deepEqual(presentationCalls, visualActions.map(([key, actionId]) => [key, actionId]));
    assert.deepEqual(requests, visualActions.map(([, actionId]) => actionId));
});

test('grouped CAT1 idle random request selects one member and reports one Cat Mind lifecycle', () => {
    const { window, context, listeners } = loadMixin();
    const acknowledgements = [];
    const results = [];
    let selectedKey = '';
    let terminal = null;
    let picks = 0;
    const state = { profile: { idleSubstate: 'idle' }, substate: 'idle', actionSettled: true };
    const button = {
        __nekoIdleCat1Journey: state,
        querySelector: (selector) => selector === '.neko-idle-return-art' ? {} : null,
    };
    Object.assign(context, {
        CustomEvent: class {
            constructor(type, init = {}) { this.type = type; this.detail = init.detail || {}; }
        },
        _findNekoCatMindVisibleButtonForTier: () => button,
        _getActiveNekoIdleReturnTier: () => 'cat1',
        _isNekoCatMindButtonContainerVisible: () => true,
        _isAnyNekoCatMindReturnPending: () => false,
        _isNekoCatMindTransitionActive: () => false,
        _isNekoIdleCompactSurfaceDragging: () => false,
        _isAnyNekoIdleCat1IndependentActionActive: () => false,
        _isNekoIdleCat1IndependentActionActive: () => false,
        _isNekoCatMindAudioActionActive: () => false,
        _isNekoIdleCat1EdgePeekActive: () => false,
        _isNekoIdleReturnDragActionBlocking: () => false,
        _isAnyNekoIdleReturnDragActionBlocking: () => false,
        _pickNekoIdleCat1RandomVisualActionKey: () => { picks += 1; return 'love1'; },
        _playNekoIdleCat1VisualAction: (_button, actionKey, options) => {
            selectedKey = actionKey;
            terminal = options.onTerminal;
            return true;
        },
    });
    window.NekoCatResourceRegistry = {
        getActionCapabilities(actionId) {
            return { available: actionId === 'cat1_idle_random', reason: '' };
        },
    };
    window.nekoCatMind = {
        acknowledgeActionRequest(payload) {
            acknowledgements.push(payload);
            return true;
        },
    };
    window.addEventListener('neko:cat-mind:action-result', (event) => results.push(event.detail));

    const requestListener = listeners.get('neko:cat-mind:action-request');
    requestListener({ detail: {
        source: 'cat_mind',
        requestId: 'idle-random-request',
        actionId: 'cat1_idle_random',
        tier: 'cat1',
    }});

    assert.equal(picks, 1);
    assert.equal(selectedKey, 'love1');
    assert.equal(typeof terminal, 'function');
    assert.deepEqual(acknowledgements.map(({ actionId, status }) => ({ actionId, status })), [
        { actionId: 'cat1_idle_random', status: 'accepted' },
        { actionId: 'cat1_idle_random', status: 'started' },
    ]);
    terminal('done', { reason: 'cat1-love-1-action-finished' });
    terminal('done', { reason: 'duplicate' });
    assert.equal(results.length, 1);
    assert.equal(results[0].actionId, 'cat1_idle_random');
    assert.equal(results[0].source, 'cat_mind');
    assert.equal(results[0].detail.actionKey, 'love1');
});

test('inactive sad request runs the fixed sad profile with one Cat Mind lifecycle', () => {
    const { window, context, listeners } = loadMixin();
    const acknowledgements = [];
    const results = [];
    let selectedKey = '';
    let terminal = null;
    const button = { querySelector: () => ({}) };
    Object.assign(context, {
        CustomEvent: class {
            constructor(type, init = {}) { this.type = type; this.detail = init.detail || {}; }
        },
        _findNekoCatMindVisibleButtonForTier: () => button,
        _getActiveNekoIdleReturnTier: () => 'cat1',
        _isNekoCatMindButtonContainerVisible: () => true,
        _isNekoCatMindCat1PositionBusy: () => false,
        _isAnyNekoCatMindReturnPending: () => false,
        _isNekoCatMindTransitionActive: () => false,
        _isNekoIdleCompactSurfaceDragging: () => false,
        _isAnyNekoIdleCat1IndependentActionActive: () => false,
        _isNekoIdleCat1IndependentActionActive: () => false,
        _isNekoCatMindAudioActionActive: () => false,
        _isNekoIdleCat1EdgePeekActive: () => false,
        _isNekoIdleReturnDragActionBlocking: () => false,
        _isAnyNekoIdleReturnDragActionBlocking: () => false,
        _playNekoIdleCat1VisualAction: (_button, actionKey, options) => {
            selectedKey = actionKey;
            terminal = options.onTerminal;
            return true;
        },
    });
    window.NekoCatResourceRegistry = {
        getActionCapabilities(actionId) {
            return { available: actionId === 'cat1_inactive_sad', reason: '' };
        },
    };
    window.nekoCatMind = {
        acknowledgeActionRequest(payload) {
            acknowledgements.push(payload);
            return true;
        },
    };
    window.addEventListener('neko:cat-mind:action-result', (event) => results.push(event.detail));

    listeners.get('neko:cat-mind:action-request')({ detail: {
        source: 'cat_mind',
        requestId: 'inactive-sad-request',
        actionId: 'cat1_inactive_sad',
        tier: 'cat1',
    }});

    assert.equal(selectedKey, 'sad');
    assert.equal(typeof terminal, 'function');
    assert.deepEqual(acknowledgements.map(({ actionId, status }) => ({ actionId, status })), [
        { actionId: 'cat1_inactive_sad', status: 'accepted' },
        { actionId: 'cat1_inactive_sad', status: 'started' },
    ]);
    terminal('done', { reason: 'cat1-sad-action-finished' });
    assert.equal(results.length, 1);
    assert.equal(results[0].actionId, 'cat1_inactive_sad');
    assert.equal(results[0].detail.actionKey, 'sad');
});

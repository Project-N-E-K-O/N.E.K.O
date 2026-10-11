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

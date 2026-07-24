const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
    path.join(projectRoot, 'static', 'vrm', 'vrm-manager.js'),
    'utf8'
);

function applyMouseTrackingPreference(resourcePhase, faceForwardLocked = false) {
    const calls = [];
    const context = {
        console: { log() {}, warn() {}, error() {} },
        window: {
            mouseTrackingEnabled: false,
            nekoYuiGuideFaceForwardLock: faceForwardLocked,
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context, { filename: 'vrm-manager.js' });

    const manager = Object.create(context.window.VRMManager.prototype);
    manager._gameModeResourcePhase = resourcePhase;
    manager._mouseTrackingEnabled = false;
    manager._cursorFollow = {
        setEnabled(enabled) {
            calls.push(enabled);
        },
    };
    manager.setMouseTrackingEnabled(true);
    return { calls, context, manager };
}

for (const resourcePhase of ['soft_protected', 'deep_sleep']) {
    test(`VRM records mouse tracking preference without enabling it during ${resourcePhase}`, () => {
        const { calls, context, manager } = applyMouseTrackingPreference(resourcePhase);

        assert.equal(context.window.mouseTrackingEnabled, true);
        assert.equal(manager._mouseTrackingEnabled, true);
        assert.deepEqual(calls, [false]);
    });
}

test('VRM enables mouse tracking while resource protection is idle', () => {
    const { calls } = applyMouseTrackingPreference('idle');

    assert.deepEqual(calls, [true]);
});

test('VRM enables mouse tracking before resource protection is initialized', () => {
    const { calls } = applyMouseTrackingPreference(undefined);

    assert.deepEqual(calls, [true]);
});

test('VRM keeps mouse tracking disabled while the Yui face-forward lock is active', () => {
    const { calls } = applyMouseTrackingPreference('idle', true);

    assert.deepEqual(calls, [false]);
});

function initializeMouseTracking(resourcePhase) {
    const calls = [];
    const context = {
        console: { log() {}, warn() {}, error() {} },
        window: {
            THREE: {},
            CursorFollowController: function CursorFollowController() {},
            mouseTrackingEnabled: true,
            nekoYuiGuideFaceForwardLock: false,
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context, { filename: 'vrm-manager.js' });

    const manager = Object.create(context.window.VRMManager.prototype);
    manager.scene = {};
    manager.camera = {};
    manager._gameModeResourcePhase = resourcePhase;
    manager._mouseTrackingEnabled = false;
    manager._cursorFollow = {
        _initialized: true,
        isEnabled() {
            return false;
        },
        setEnabled(enabled) {
            calls.push(enabled);
        },
    };
    manager._initMouseLookAtTracking();
    return { calls, manager };
}

for (const resourcePhase of ['soft_protected', 'deep_sleep']) {
    test(`VRM reload keeps cursor tracking disabled during ${resourcePhase}`, () => {
        const { calls, manager } = initializeMouseTracking(resourcePhase);

        assert.deepEqual(calls, []);
        assert.equal(manager._mouseTrackingEnabled, true);
    });
}

test('VRM reload enables cursor tracking while resource protection is idle', () => {
    const { calls, manager } = initializeMouseTracking('idle');

    assert.deepEqual(calls, [true]);
    assert.equal(manager._mouseTrackingEnabled, true);
});

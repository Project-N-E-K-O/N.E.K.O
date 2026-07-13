const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const PARTS_DIR = path.join(PROJECT_ROOT, 'static', 'avatar', 'avatar-ui-buttons');
const PART_NAMES = [
    '00-core.js',
    '10-idle-assets-and-question.js',
    '20-idle-playground.js',
    '30-idle-actions-and-audio.js',
    '40-idle-drag-and-subactions.js',
    '45-idle-journey-and-presentation.js',
    '50-methods-setup.js',
    '60-methods-buttons.js',
    '70-methods-return.js',
    '80-methods-state-and-cleanup.js',
];
const EXPECTED_METHOD_NAMES = [
    '_addReturnButtonBreathingAnimation',
    '_setupReturnButtonDrag',
    '_syncButtonStatesWithGlobalState',
    'cleanupFloatingButtons',
    'createButtonElement',
    'createMicMuteButton',
    'createReturnButton',
    'getDefaultButtonConfigs',
    'resetAllButtons',
    'setButtonActive',
    'setupFloatingButtonsBase',
    'updateSeparatePopupTriggerIcon',
];

function loadMixin() {
    const listeners = new Map();
    const document = {
        currentScript: { src: 'http://127.0.0.1/static/avatar/avatar-ui-buttons/00-core.js?v=test' },
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
    return { mixin: context.__avatarButtonMixin, cancelledAnimationFrames };
}

test('avatar button parts install the unchanged method contract for every backend', () => {
    const discoveredParts = fs.readdirSync(PARTS_DIR)
        .filter((name) => name.endsWith('.js'))
        .sort();
    assert.deepEqual(discoveredParts, PART_NAMES);

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

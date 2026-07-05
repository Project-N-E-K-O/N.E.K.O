const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const predictorSource = fs.readFileSync(
    path.join(__dirname, 'tutorial/core/avatar-floating-boot-predictor.js'),
    'utf-8'
);

function createStorage() {
    const values = new Map();
    return {
        getItem(key) {
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            values.set(key, String(value));
        }
    };
}

function loadPredictor(bridge) {
    const window = {
        innerWidth: 1280,
        localStorage: createStorage(),
        sessionStorage: createStorage(),
        nekoTutorialLoadingOverlay: bridge
    };
    const context = vm.createContext({ window, console, Date, Math });
    vm.runInContext(predictorSource, context);
    return window;
}

test('direct tutorial startup loading is scoped to the primary display', () => {
    const begins = [];
    const updates = [];
    const clears = [];
    const window = loadPredictor({
        begin(payload) {
            begins.push(payload);
        },
        update(payload) {
            updates.push(payload);
        },
        clear(payload) {
            clears.push(payload);
        }
    });

    assert.equal(window.NekoAvatarFloatingBoot.beginDirectTutorialLoading('startup-direct-tutorial-predicted'), true);

    assert.equal(begins.length, 1);
    assert.equal(begins[0].reason, 'startup-direct-tutorial-predicted');
    assert.equal(begins[0].displayScope, 'primary');
    assert.equal(updates.length, 1);
    assert.equal(updates[0].payload.loading.visible, true);
    assert.equal(updates[0].payload.loading.reason, 'startup-direct-tutorial-predicted');
    assert.equal(updates[0].payload.loading.displayScope, 'primary');

    assert.equal(window.NekoAvatarFloatingBoot.clearDirectTutorialLoading('avatar-floating-yui-ready'), true);
    assert.equal(updates[1].payload.loading, null);
    assert.equal(updates[1].payload.reason, 'avatar-floating-yui-ready');
    assert.equal(updates[1].payload.displayScope, 'primary');
    assert.equal(clears.length, 1);
    assert.equal(clears[0].reason, 'avatar-floating-yui-ready');
    assert.equal(clears[0].displayScope, 'primary');
});

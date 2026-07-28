const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
    path.join(projectRoot, 'static', 'mmd', 'mmd-cursor-follow.js'),
    'utf8'
);

function createHarness(resourcePhase) {
    const calls = [];
    const context = {
        THREE: null,
        console: { log() {}, warn() {}, error() {} },
        window: {
            mouseTrackingEnabled: true,
            nekoYuiGuideFaceForwardLock: false,
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context, { filename: 'mmd-cursor-follow.js' });
    vm.runInContext('globalThis.MMDCursorFollowForTest = MMDCursorFollow;', context);

    const cursorFollow = Object.create(context.MMDCursorFollowForTest.prototype);
    cursorFollow.manager = { _gameModeResourcePhase: resourcePhase };
    cursorFollow.enabled = false;
    cursorFollow._disabledByYuiGuideFaceForwardLock = true;
    cursorFollow.setEnabled = function (enabled) {
        calls.push(enabled);
        this.enabled = enabled;
    };
    return { calls, cursorFollow };
}

for (const resourcePhase of ['soft_protected', 'deep_sleep']) {
    test(`guide unlock does not restart MMD cursor tracking during ${resourcePhase}`, () => {
        const { calls, cursorFollow } = createHarness(resourcePhase);

        cursorFollow.update(1 / 60);

        assert.deepEqual(calls, []);
        assert.equal(cursorFollow.enabled, false);
        assert.equal(cursorFollow._disabledByYuiGuideFaceForwardLock, true);
    });
}

test('guide unlock restores MMD cursor tracking after resource protection is idle', () => {
    const { calls, cursorFollow } = createHarness('idle');

    cursorFollow.update(1 / 60);

    assert.deepEqual(calls, [true]);
    assert.equal(cursorFollow.enabled, true);
    assert.equal(cursorFollow._disabledByYuiGuideFaceForwardLock, false);
});

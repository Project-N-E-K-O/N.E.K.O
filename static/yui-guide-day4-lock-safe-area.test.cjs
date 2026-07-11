const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

function readStatic(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
}

test('day4 model lock spotlight uses a scene-scoped lock icon safe area', () => {
    const directorSource = readStatic('tutorial/yui-guide/director.js');
    const orchestratorSource = readStatic('tutorial/core/scene-orchestrator.js');
    const sharedButtonsSource = readStatic('avatar-ui-buttons.js');

    assert.match(directorSource, /const DAY4_LOCK_SPOTLIGHT_SAFE_BOTTOM_PX = 112;/);
    assert.match(directorSource, /syncDay4LockSpotlightSafeAreaForScene\(scene\) \{[\s\S]*sceneId === 'day4_model_lock'/);
    assert.match(directorSource, /if \(this\.day4LockSpotlightSafeAreaActive === shouldActivate\) \{[\s\S]*return shouldActivate;/);
    assert.match(directorSource, /getDay4LockButtonSpotlightTarget\(\) \{[\s\S]*setDay4LockSpotlightSafeAreaActive\(true, 'day4_model_lock'\)[\s\S]*adjustDay4LockSpotlightTarget\(lockIcon\)/);
    assert.match(directorSource, /setDay4LockSpotlightSafeAreaActive\(false, 'termination-cleanup'\)/);
    assert.match(orchestratorSource, /syncDay4LockSpotlightSafeAreaForScene\(scene\)/);
    assert.match(orchestratorSource, /setDay4LockSpotlightSafeAreaActive\(false, 'round-complete'\)/);
    assert.match(sharedButtonsSource, /window\.getNekoYuiGuideLockIconMaxTop = getNekoYuiGuideLockIconMaxTop;/);

    [
        'live2d-ui-buttons.js',
        'vrm-ui-buttons.js',
        'mmd-ui-buttons.js',
        'pngtuber-core.js'
    ].forEach((fileName) => {
        assert.match(
            readStatic(fileName),
            /getNekoYuiGuideLockIconMaxTop/,
            `${fileName} should honor the tutorial lock spotlight safe area`
        );
    });

    assert.match(readStatic('vrm-ui-buttons.js'), /_updateFloatingButtonsPositionNow/);
    assert.match(readStatic('mmd-ui-buttons.js'), /_updateFloatingButtonsPositionNow/);
    assert.match(readStatic('vrm-ui-buttons.js'), /const minLockY = Math\.min\(20, maxLockY\);[\s\S]*const boundedLockY = Math\.max\(minLockY, Math\.min\(lockTargetY, maxLockY\)\);/);
    assert.match(readStatic('mmd-ui-buttons.js'), /const minLockY = Math\.min\(20, maxLockY\);[\s\S]*const boundedLockY = Math\.max\(minLockY, Math\.min\(lockTargetY, maxLockY\)\);/);
});

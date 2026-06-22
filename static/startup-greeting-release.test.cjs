const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const appWebsocketSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-websocket.js'), 'utf8');
const universalManagerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
const websocketRouterSource = fs.readFileSync(path.join(repoRoot, 'main_routers/websocket_router.py'), 'utf8');

test('startup greeting waits for an explicit release instead of firing on websocket open', () => {
    assert.match(appWebsocketSource, /STARTUP_GREETING_RELEASE_EVENT/);
    assert.match(appWebsocketSource, /STARTUP_GREETING_RELEASE_FALLBACK_MS/);
    assert.match(appWebsocketSource, /function releaseStartupGreetingCheck\(reason\)/);
    assert.match(appWebsocketSource, /window\.addEventListener\(STARTUP_GREETING_RELEASE_EVENT,\s*function/);

    const wsOpenStart = appWebsocketSource.lastIndexOf(
        'if (goodbyeActiveOnOpen || (goodbyeSyncOnOpen && goodbyeSyncOnOpen.active))',
        appWebsocketSource.indexOf("sendStartupGreetingReleaseRequest('ws-open')")
    );
    assert.notEqual(wsOpenStart, -1, 'expected websocket open to register a startup greeting release request');
    const wsOpenEnd = appWebsocketSource.indexOf('// ── game-window-state 重连兜底', wsOpenStart);
    assert.notEqual(wsOpenEnd, -1, 'expected to locate end of websocket open greeting block');
    const wsOpenBlock = appWebsocketSource.slice(wsOpenStart, wsOpenEnd);

    assert.match(wsOpenBlock, /_markGreetingCheckPending\(/);
    assert.doesNotMatch(wsOpenBlock, /_sendGreetingCheckIfReady\(\);/);

    const sendBlock = appWebsocketSource.split('function _sendGreetingCheckIfReady()')[1].split(
        'function _onModelReady()',
        1,
    )[0];
    assert.match(sendBlock, /if \(S\._startupGreetingReleasePending\) \{\s*return;\s*\}/);
    assert.ok(
        sendBlock.indexOf('if (S._startupGreetingReleasePending)') < sendBlock.indexOf('if (_consumeGreetingCheckForNewUserIcebreaker())'),
        'model-ready sends must wait for the startup greeting release gate before icebreaker or send checks'
    );

    const fallbackBlock = appWebsocketSource.split('function scheduleStartupGreetingReleaseFallback()')[1].split(
        'function sendStartupGreetingReleaseRequest(reason)',
        1,
    )[0];
    assert.match(fallbackBlock, /S\._startupGreetingReleaseFallbackTimer = setTimeout/);
    assert.match(fallbackBlock, /if \(isStartupTutorialActiveForGreeting\(\)\) \{\s*scheduleStartupGreetingReleaseFallback\(\);\s*return;\s*\}/);
    assert.match(fallbackBlock, /releaseStartupGreetingCheck\('startup-greeting-release-timeout'\)/);

    const requestBlock = appWebsocketSource.split('function sendStartupGreetingReleaseRequest(reason)')[1].split(
        'function releaseStartupGreetingCheck(reason)',
        1,
    )[0];
    assert.match(requestBlock, /if \(!hasStartupGreetingReleaseProducer\(\)\) \{\s*releaseStartupGreetingCheck\(reason \|\| 'startup-greeting-no-release-producer'\);\s*return;\s*\}/);
    assert.match(requestBlock, /scheduleStartupGreetingReleaseFallback\(\)/);

    const releaseBlock = appWebsocketSource.split('function releaseStartupGreetingCheck(reason)')[1].split(
        'function _consumeGreetingCheckForNewUserIcebreaker()',
        1,
    )[0];
    assert.match(releaseBlock, /clearTimeout\(S\._startupGreetingReleaseFallbackTimer\)/);
});

test('tutorial manager releases startup greeting after tutorial decisions and endings', () => {
    assert.match(universalManagerSource, /STARTUP_GREETING_RELEASE_EVENT/);
    assert.match(universalManagerSource, /dispatchStartupGreetingRelease\(reason/);
    assert.match(universalManagerSource, /dispatchStartupGreetingReleaseWithoutManager\(reason/);
    assert.match(universalManagerSource, /new CustomEvent\(STARTUP_GREETING_RELEASE_EVENT/);
    assert.match(universalManagerSource, /dispatchStartupGreetingRelease\('avatar-floating-round-start-skipped'/);
    assert.match(universalManagerSource, /dispatchStartupGreetingRelease\('avatar-floating-round-start-failed'/);
    assert.match(universalManagerSource, /dispatchStartupGreetingRelease\('avatar-floating-auto-round-check-failed'\)/);
    assert.match(universalManagerSource, /dispatchStartupGreetingReleaseWithoutManager\('mobile-tutorial-disabled'/);

    const autoRoundBlockStart = universalManagerSource.indexOf('this.maybeStartAvatarFloatingGuideAutoRound(1200)');
    assert.notEqual(autoRoundBlockStart, -1, 'expected home tutorial auto-round decision');
    const autoRoundBlockEnd = universalManagerSource.indexOf('\n            });', autoRoundBlockStart);
    assert.notEqual(autoRoundBlockEnd, -1, 'expected end of auto-round decision block');
    const autoRoundBlock = universalManagerSource.slice(autoRoundBlockStart, autoRoundBlockEnd);
    assert.match(autoRoundBlock, /then\(\(started\) =>/);
    assert.match(autoRoundBlock, /dispatchStartupGreetingRelease\('no-avatar-floating-round'\)/);

    const endBlockStart = universalManagerSource.indexOf('\n    onTutorialEnd()');
    assert.notEqual(endBlockStart, -1, 'expected tutorial end handler');
    const endBlockEnd = universalManagerSource.indexOf('\n    /**', endBlockStart);
    assert.notEqual(endBlockEnd, -1, 'expected end of tutorial end handler');
    const endBlock = universalManagerSource.slice(endBlockStart, endBlockEnd);
    assert.match(endBlock, /dispatchStartupGreetingRelease\(/);
    assert.match(endBlock, /tutorial-completed/);
    assert.match(endBlock, /tutorial-skipped/);
});

test('old home tutorial websocket greeting guard is removed', () => {
    assert.doesNotMatch(appWebsocketSource, /home_tutorial_state/);
    assert.doesNotMatch(appWebsocketSource, /blocking_greeting/);
    assert.doesNotMatch(appWebsocketSource, /sendHomeTutorialState/);

    assert.doesNotMatch(websocketRouterSource, /_home_tutorial_blocking_greeting/);
    assert.doesNotMatch(websocketRouterSource, /home_tutorial_state/);
    assert.doesNotMatch(websocketRouterSource, /blocking_greeting/);
});

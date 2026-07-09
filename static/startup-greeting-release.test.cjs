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
    assert.match(appWebsocketSource, /function consumeStartupGreetingReleasedDetail\(\)/);
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
    assert.match(wsOpenBlock, /var isGreetingSwitchOnOpen = !!S\._pendingGreetingSwitch/);
    assert.match(wsOpenBlock, /var greetingReasonOnOpen = S\._greetingCheckReason \|\| \(isGreetingSwitchOnOpen \? 'character-switch' : 'ws-open'\)/);
    assert.match(wsOpenBlock, /if \(isGreetingSwitchOnOpen \|\| S\._startupGreetingReleaseGateUsed\) \{\s*_sendGreetingCheckIfReady\(\);\s*\} else \{\s*S\._startupGreetingReleaseGateUsed = true;\s*sendStartupGreetingReleaseRequest\('ws-open'\);\s*\}/);

    const sendBlock = appWebsocketSource.split('function _sendGreetingCheckIfReady()')[1].split(
        'function _onModelReady()',
        1,
    )[0];
    assert.match(sendBlock, /if \(S\._startupGreetingReleasePending\) \{\s*return;\s*\}/);
    assert.ok(
        sendBlock.indexOf('if (S._startupGreetingReleasePending)') < sendBlock.indexOf('if (_deferGreetingCheckForNewUserIcebreaker())'),
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
    assert.match(requestBlock, /const released = consumeStartupGreetingReleasedDetail\(\)/);
    assert.match(requestBlock, /if \(!hasStartupGreetingReleaseProducer\(\)\) \{\s*releaseStartupGreetingCheck\(reason \|\| 'startup-greeting-no-release-producer'\);\s*return;\s*\}/);
    assert.match(requestBlock, /scheduleStartupGreetingReleaseFallback\(\)/);

    const consumeBlock = appWebsocketSource.split('function consumeStartupGreetingReleasedDetail()')[1].split(
        'function hasStartupGreetingReleaseProducer()',
        1,
    )[0];
    assert.match(consumeBlock, /delete window\.__NEKO_STARTUP_GREETING_RELEASED__/);

    const releaseBlock = appWebsocketSource.split('function releaseStartupGreetingCheck(reason)')[1].split(
        'function _deferGreetingCheckForNewUserIcebreaker()',
        1,
    )[0];
    assert.match(releaseBlock, /clearTimeout\(S\._startupGreetingReleaseFallbackTimer\)/);

    const producerBlock = appWebsocketSource.split('function hasStartupGreetingReleaseProducer()')[1].split(
        'function isStartupTutorialActiveForGreeting()',
        1,
    )[0];
    assert.doesNotMatch(producerBlock, /isStartupGreetingHomePage/);
    assert.match(producerBlock, /window\.universalTutorialManager/);
    assert.match(producerBlock, /querySelector\('script\[src\*="\/static\/tutorial\/core\/universal-manager\.js"\]/);

    const listenerBlock = appWebsocketSource.split('window.addEventListener(STARTUP_GREETING_RELEASE_EVENT')[1].split(
        "window.addEventListener('neko:cat-greeting-check'",
        1,
    )[0];
    assert.match(listenerBlock, /if \(detail\.released === false\) \{\s*return;\s*\}/);
});

test('startup greeting is deferred until new-user icebreaker ends', () => {
    assert.match(appWebsocketSource, /function isNewUserIcebreakerActiveForGreeting\(\)/);
    assert.match(appWebsocketSource, /function _deferGreetingCheckForNewUserIcebreaker\(\)/);
    assert.doesNotMatch(appWebsocketSource, /function _consumeGreetingCheckForNewUserIcebreaker\(\)/);

    const blockingBlock = appWebsocketSource.split('function isNewUserIcebreakerBlockingGreeting(reason)')[1].split(
        'function normalizeAssistantTurnId(turnId)',
        1,
    )[0];
    assert.match(blockingBlock, /return isNewUserIcebreakerActiveForGreeting\(\);/);
    assert.doesNotMatch(blockingBlock, /isTutorialReleaseGreetingReason/);

    const deferBlock = appWebsocketSource.split('function _deferGreetingCheckForNewUserIcebreaker()')[1].split(
        'function _sendGreetingCheckIfReady()',
        1,
    )[0];
    assert.match(deferBlock, /if \(!isNewUserIcebreakerBlockingGreeting\(S\._greetingCheckReason\)\) return false;/);
    assert.match(deferBlock, /_scheduleGreetingCheckRetry\(\);/);
    assert.doesNotMatch(deferBlock, /S\._greetingCheckPending = false;/);
    assert.doesNotMatch(deferBlock, /S\._greetingCheckReason = '';/);
    assert.doesNotMatch(deferBlock, /_resetGreetingCheckRetry\(true\);/);

    const sendBlock = appWebsocketSource.split('function _sendGreetingCheckIfReady()')[1].split(
        'function _onModelReady()',
        1,
    )[0];
    assert.match(sendBlock, /if \(_deferGreetingCheckForNewUserIcebreaker\(\)\) \{\s*return;\s*\}/);

    const icebreakerEndListener = appWebsocketSource.split("window.addEventListener('neko:new-user-icebreaker-ended'")[1].split(
        "window.addEventListener(STARTUP_GREETING_RELEASE_EVENT",
        1,
    )[0];
    assert.match(icebreakerEndListener, /_sendGreetingCheckIfReady\(\);/);
});

test('tutorial manager releases startup greeting after tutorial decisions and endings', () => {
    assert.match(universalManagerSource, /STARTUP_GREETING_RELEASE_EVENT/);
    assert.match(universalManagerSource, /dispatchStartupGreetingRelease\(reason/);
    assert.match(universalManagerSource, /clearStartupGreetingRelease\(reason/);
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
    assert.match(endBlock, /Promise\.resolve\(teardownPromise\)\.finally/);
    assert.ok(
        endBlock.indexOf('Promise.resolve(teardownPromise).finally') < endBlock.indexOf('this.dispatchStartupGreetingRelease(startupGreetingReleaseReason'),
        'startup greeting release must wait for tutorial teardown to settle'
    );
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

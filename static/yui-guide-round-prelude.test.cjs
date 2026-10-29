const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readDirectorSource } = require('./yui-guide-director-test-parts.cjs');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const preludePath = path.join(__dirname, 'tutorial/core/round-prelude-controller.js');
const managerSource = fs.readFileSync(path.join(__dirname, 'tutorial/core/universal-manager.js'), 'utf8');

test('round prelude controller exports reusable controller facade', () => {
    assert.ok(fs.existsSync(preludePath), 'tutorial/core/round-prelude-controller.js should exist');
    const api = require('./tutorial/core/round-prelude-controller.js');

    assert.equal(typeof api.TutorialRoundPreludeController, 'function');
    assert.equal(typeof api.createController, 'function');
});

test('round prelude runs avatar override, visibility, delay, lifecycle and started event in order', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() {
            calls.push('begin');
            return Promise.resolve();
        },
        revealPrepared() {
            calls.push('reveal');
        },
        ensureVisible(sceneId) {
            calls.push(['visible', sceneId]);
            return Promise.resolve();
        },
        waitForAvatarReady(sceneId) {
            calls.push(['ready', sceneId]);
            return Promise.resolve();
        },
        sleep(delayMs) {
            calls.push(['sleep', delayMs]);
            return Promise.resolve();
        },
        beginTakingOver(detail) {
            calls.push(['takeover', detail.day, detail.source]);
        },
        setLifecycleActive(active) {
            calls.push(['lifecycle', active]);
        },
        showSkipButton() {
            calls.push('skip');
        },
        dispatchStarted(detail) {
            calls.push(['started', detail.day, detail.source]);
        }
    });

    await controller.play(4, { source: 'manual' });

    assert.deepEqual(calls, [
        'begin',
        'reveal',
        ['visible', 'avatar_floating_day4'],
        ['ready', 'avatar_floating_day4'],
        ['sleep', 1500],
        ['takeover', 4, 'manual'],
        ['lifecycle', true],
        'skip',
        ['started', 4, 'manual']
    ]);
});

test('round prelude does not restore tutorial UI after cancellation during loading', async (t) => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const stages = ['beginAvatarOverride', 'revealPrepared', 'ensureVisible', 'waitForAvatarReady', 'sleep'];

    for (const stage of stages) {
        for (const rejectLoading of [false, true]) {
            await t.test(`${stage}: ${rejectLoading ? 'rejects' : 'resolves'} after Escape`, async () => {
                const calls = [];
                let cancelled = false;
                let settle;
                let reached;
                const blocked = new Promise((resolve, reject) => {
                    settle = () => rejectLoading ? reject(new Error('late loading failure')) : resolve();
                });
                const waiting = new Promise((resolve) => { reached = resolve; });
                const options = Object.fromEntries(stages.map((name) => [name, () => {
                    calls.push(name);
                    if (name === stage) {
                        reached();
                        return blocked;
                    }
                }]));
                for (const name of ['beginTakingOver', 'setLifecycleActive', 'showSkipButton', 'dispatchStarted', 'warn']) {
                    options[name] = () => calls.push(name);
                }
                const controller = new TutorialRoundPreludeController(options);
                const pending = controller.play(1, { cancelCheck: () => cancelled });
                await waiting;
                cancelled = true;
                settle();
                if (rejectLoading && ['revealPrepared', 'sleep'].includes(stage)) {
                    // These callbacks propagate errors; the manager owns their teardown.
                    await assert.rejects(pending, /late loading failure/);
                } else {
                    assert.equal(await pending, false, 'a cancelled prelude must not start the round');
                }
                assert.deepEqual(calls, stages.slice(0, stages.indexOf(stage) + 1));
            });
        }
    }
});

test('round prelude ignores an already cancelled run and supports a later live run', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() { calls.push('model'); },
        showSkipButton() { calls.push('skip'); }
    });

    assert.equal(await controller.play(1, { cancelCheck: () => true }), false);
    assert.deepEqual(calls, []);
    assert.equal(await controller.play(1, { cancelCheck: () => false, deferRevealPrepared: true }), true);
    assert.deepEqual(calls, ['model', 'skip']);
});

test('manager cancels the loading prelude when it ends or its original director stops', async (t) => {
    for (const stopState of ['destroyed', 'ended', 'director-stopped']) {
        await t.test(stopState, async () => {
            const calls = [];
            const module = { exports: {} };
            vm.runInNewContext(managerSource, {
                window: {
                    TutorialRoundPrelude: require('./tutorial/core/round-prelude-controller.js'),
                    dispatchEvent() { calls.push('started'); }
                },
                module,
                console,
                CustomEvent: class {}
            });
            const manager = Object.create(module.exports.UniversalTutorialManager.prototype);
            let release;
            let reached;
            const blocked = new Promise((resolve) => { release = resolve; });
            const waiting = new Promise((resolve) => { reached = resolve; });
            Object.assign(manager, {
                beginTutorialAvatarOverride() { calls.push('loading'); reached(); return blocked; },
                revealTutorialLive2dPrepared() { calls.push('reveal'); },
                ensureTutorialYuiLive2dVisible() { calls.push('visible'); },
                waitForTutorialYuiLive2dVisualReady() { calls.push('ready'); },
                sleep() { calls.push('delay'); },
                showSkipButton() { calls.push('skip'); }
            });
            let directorStopped = false;
            const director = {
                isStopping: () => directorStopped,
                setTutorialTakingOver() { calls.push('takeover'); }
            };
            const pending = manager.playAvatarFloatingRoundPrelude(1, 'auto', director);
            await waiting;
            manager._isDestroyed = stopState === 'destroyed';
            manager._tutorialEndHandled = stopState === 'ended';
            // The original director stays stopped even if a restart resets manager flags.
            directorStopped = stopState === 'director-stopped';
            release();

            assert.equal(await pending, false);
            assert.deepEqual(calls, ['loading']);
            assert.notEqual(manager._yuiGuideLifecycleActive, true);
        });
    }
});

test('a restarted tutorial keeps ownership when its i18n startup caller settles late', async (t) => {
    const deferred = () => {
        let resolve;
        let reject;
        const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
        return { promise, resolve, reject };
    };
    for (const stage of ['sleep', 'round', 'flush', 'prepare', 'teardown', 'rollback', 'cancel-flush']) {
        for (const rejects of [false, true]) {
            if (stage === 'cancel-flush' && rejects) continue;
            await t.test(`${stage}: old startup ${rejects ? 'rejects' : 'resolves'} after restart`, async () => {
                const oldGate = deferred();
                const oldWaiting = deferred();
                const newGate = deferred();
                const newPlaying = deferred();
                const ends = [];
                const releases = [];
                const recoveries = [];
                const greetingReleases = [];
                const relays = [];
                const prepares = [];
                let currentRun = 0;
                let flushCalls = 0;
                let manager;
                const waitAt = (name) => {
                    if (currentRun === 1 && (name === stage || (stage === 'cancel-flush' && name === 'flush'))) {
                        oldWaiting.resolve();
                        return oldGate.promise;
                    }
                    return Promise.resolve(true);
                };
                const window = {
                    TutorialRoundPrelude: require('./tutorial/core/round-prelude-controller.js'),
                    NekoSevenDayTutorialState: {
                        normalizeRound: day => day,
                        flush() {
                            flushCalls++;
                            if (stage === 'rollback' && currentRun === 1 && flushCalls === 2) {
                                oldWaiting.resolve();
                                return oldGate.promise;
                            }
                            return waitAt('flush');
                        }
                    },
                    dispatchEvent() {}
                };
                const module = { exports: {} };
                vm.runInNewContext(managerSource, {
                    window, module, console: { ...console, error() {} }, CustomEvent: class {},
                    setTimeout,
                    document: { body: { classList: { add() {} } } }
                });
                manager = Object.create(module.exports.UniversalTutorialManager.prototype);
                Object.assign(manager, {
                    currentPage: 'home',
                    lifecycleStateStore: { resetEndReason() {}, getEndReason() {}, getEndRawReason() {} },
                    waitUntilInitialized: async () => true,
                    isDirectAvatarFloatingTutorialBoot: () => true,
                    waitForTutorialModelHostReady: async () => true,
                    claimDirectAvatarFloatingTutorialBoot() { currentRun++; },
                    releaseDirectAvatarFloatingTutorialBoot() { releases.push(currentRun); },
                    recoverUserModelAfterDirectTutorialBootFailure() { recoveries.push(currentRun); },
                    relayYuiGuideTutorialLifecycleStarted() { relays.push(currentRun); },
                    prepareYuiGuideCompactChatForTutorial() {
                        prepares.push(currentRun);
                        if (currentRun === 1 && stage === 'rollback') this.requestTutorialEnd('skip');
                        return waitAt('prepare');
                    },
                    sleep: () => waitAt('sleep'),
                    isI18nReady: () => true,
                    getHomeAvatarFloatingGuideLaunchRound: () => 1,
                    consumeTutorialStartSource: () => 'auto',
                    dispatchStartupGreetingRelease() { greetingReleases.push(currentRun); },
                    getHomeAvatarFloatingGuideStartRound: () => 1,
                    getStorageKeysForPage: () => [],
                    ensureYuiGuideDirector() {
                        const run = currentRun;
                        const director = {
                            destroyed: false,
                            isStopping() { return this.destroyed; },
                            setTutorialTakingOver() {},
                            async playAvatarFloatingRound() {
                                if (run === 1 && stage === 'teardown') return false;
                                if (run === 1) return waitAt('round');
                                newPlaying.resolve();
                                return newGate.promise;
                            }
                        };
                        this.yuiGuideDirector = director;
                        return director;
                    },
                    async requestTutorialEnd(reason) {
                        ends.push([currentRun, reason]);
                        this._tutorialEndHandled = true;
                        this._isDestroyed = true;
                        this.isTutorialRunning = false;
                        window.isInTutorial = false;
                        if (this.yuiGuideDirector) this.yuiGuideDirector.destroyed = true;
                        this.yuiGuideDirector = null;
                        if (reason === 'destroy') await waitAt('teardown');
                    }
                });
                for (const name of ['markAvatarFloatingGuideRoundAutoShown', 'rollbackAvatarFloatingGuideRoundAutoShown',
                    'resetAvatarFloatingGuideRoundState',
                    'setAvatarFloatingGuideCurrentRound', 'snapshotAvatarFloatingModelInteractionState', 'lockBodyScroll',
                    'syncYuiGuideCompactChatFixedLayout', 'emitTutorialStarted',
                    'beginTutorialAvatarOverride', 'ensureTutorialYuiLive2dVisible', 'waitForTutorialYuiLive2dVisualReady',
                    'revealTutorialLive2dPrepared', 'showSkipButton', 'restoreYuiGuideCompactChatSurface']) {
                    manager[name] = () => {};
                }
                const startRound = manager.startAvatarFloatingGuideRound;
                let oldStartup;
                manager.startAvatarFloatingGuideRound = function (...args) {
                    const pending = startRound.apply(this, args);
                    oldStartup ||= pending;
                    return pending;
                };
                manager.startTutorialWhenI18nReady();
                await oldWaiting.promise;
                if (stage === 'cancel-flush') {
                    await manager.requestTutorialEnd('skip');
                    oldGate.resolve(true);
                    assert.equal(await oldStartup, false, 'a current cancelled startup still allows normal recovery');
                    assert.deepEqual(relays, [], 'Escape during flush must not reopen the lifecycle');
                    assert.deepEqual(prepares, [], 'a cancelled startup must not prepare chat');
                    assert.deepEqual(releases, [1]);
                    assert.deepEqual(recoveries, [1]);
                    assert.deepEqual(greetingReleases, [1]);
                    return;
                }
                const restarted = manager.restartCurrentTutorial();
                await newPlaying.promise;
                const newDirector = manager.yuiGuideDirector;
                if (rejects) oldGate.reject(new Error('old startup failed late'));
                else oldGate.resolve(true);
                const priorEnds = stage === 'teardown' ? [[1, 'destroy'], [1, 'restart']]
                    : stage === 'rollback' ? [[1, 'skip'], [1, 'restart']] : [[1, 'restart']];
                const priorReleases = ['teardown', 'rollback'].includes(stage) ? [1] : [];
                try {
                    assert.equal(await oldStartup, null, 'superseded startup must not trigger caller recovery');
                    assert.equal(manager.yuiGuideDirector, newDirector);
                    assert.equal(newDirector.isStopping(), false);
                    assert.equal(manager.isTutorialRunning, true);
                    assert.equal(window.isInTutorial, true);
                    assert.deepEqual(ends, priorEnds);
                    assert.deepEqual(releases, priorReleases, 'old completion must not release the new direct-boot claim');
                    assert.deepEqual(recoveries, [], 'old failures must not restore over the new model');
                    assert.deepEqual(greetingReleases, [], 'the old caller must not release the new startup barrier');
                } finally {
                    newGate.resolve(true);
                    await restarted;
                }
                assert.deepEqual(releases, [...priorReleases, 2], 'the new round still releases its own direct-boot claim');
                assert.deepEqual(ends, [...priorEnds, [2, 'complete']]);
            });
        }
    }
});

test('late chat preparation cannot replace the restarted round snapshot', async (t) => {
    for (const completion of ['ack', 'timeout']) {
        await t.test(completion, async () => {
            const listeners = new Set();
            const timers = new Map();
            const messages = [];
            const values = new Map();
            let timerId = 0;
            const window = {
                addEventListener(_type, listener) { listeners.add(listener); },
                removeEventListener(_type, listener) { listeners.delete(listener); },
                setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
                clearTimeout(id) { timers.delete(id); },
                localStorage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) },
                nekoTutorialOverlay: { relayToChat(message) { messages.push(message); } }
            };
            const module = { exports: {} };
            vm.runInNewContext(managerSource, { window, module, console: { ...console, warn() {} } });
            const manager = Object.create(module.exports.UniversalTutorialManager.prototype);
            manager._avatarFloatingGuideStartupToken = {};
            const oldPrepare = manager.prepareYuiGuideCompactChatForTutorial();
            const oldTimeout = timers.get(1);
            manager._avatarFloatingGuideStartupToken = {};
            const newPrepare = manager.prepareYuiGuideCompactChatForTutorial();
            const emitReady = (requestId, wasCollapsed) => {
                for (const listener of [...listeners]) listener({ detail: { requestId, wasCollapsed } });
            };
            emitReady(messages[1].requestId, false);
            assert.equal(await newPrepare, true);
            const snapshot = manager._avatarFloatingChatSurfaceSnapshot;
            assert.equal(snapshot.requestId, messages[1].requestId);
            if (completion === 'ack') emitReady(messages[0].requestId, true);
            else oldTimeout();
            assert.equal(await oldPrepare, false);
            assert.equal(manager._avatarFloatingChatSurfaceSnapshot, snapshot);
            assert.equal(listeners.size, 0);
            assert.equal(timers.size, 0);
        });
    }
});

test('round prelude starts takeover for day one before audio activation', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() {
            calls.push('begin');
            return Promise.resolve();
        },
        revealPrepared() {
            calls.push('reveal');
        },
        ensureVisible() {
            calls.push('visible');
            return Promise.resolve();
        },
        waitForAvatarReady() {
            calls.push('ready');
            return Promise.resolve();
        },
        sleep() {
            calls.push('sleep');
            return Promise.resolve();
        },
        beginTakingOver() {
            calls.push('takeover');
        },
        setLifecycleActive() {
            calls.push('lifecycle');
        },
        showSkipButton() {
            calls.push('skip');
        },
        dispatchStarted() {
            calls.push('started');
        }
    });

    await controller.play(1, { source: 'manual' });

    assert.deepEqual(calls, [
        'begin',
        'reveal',
        'visible',
        'ready',
        'sleep',
        'takeover',
        'lifecycle',
        'skip',
        'started'
    ]);
});

test('round prelude stops after avatar override failure', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const warnings = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() {
            calls.push('begin');
            return Promise.reject(new Error('override failed'));
        },
        revealPrepared() {
            calls.push('reveal');
        },
        ensureVisible() {
            calls.push('visible');
        },
        sleep(delayMs) {
            calls.push(['sleep', delayMs]);
            return Promise.resolve();
        },
        beginTakingOver(detail) {
            calls.push(['takeover', detail.day, detail.source]);
        },
        setLifecycleActive(active) {
            calls.push(['lifecycle', active]);
        },
        showSkipButton() {
            calls.push('skip');
        },
        dispatchStarted(detail) {
            calls.push(['started', detail.day, detail.source]);
        },
        warn(message) {
            warnings.push(message);
        }
    });

    await assert.rejects(
        () => controller.play(2, { source: 'auto', delayMs: 20 }),
        /override failed/
    );

    assert.deepEqual(calls, [
        'begin',
        'reveal'
    ]);
    assert.equal(warnings.length, 1);
});

test('round prelude stops after visibility failure', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const warnings = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() {
            calls.push('begin');
        },
        revealPrepared() {
            calls.push('reveal');
        },
        ensureVisible() {
            calls.push('visible');
            return Promise.reject(new Error('visible failed'));
        },
        sleep(delayMs) {
            calls.push(['sleep', delayMs]);
            return Promise.resolve();
        },
        beginTakingOver(detail) {
            calls.push(['takeover', detail.day, detail.source]);
        },
        setLifecycleActive(active) {
            calls.push(['lifecycle', active]);
        },
        showSkipButton() {
            calls.push('skip');
        },
        dispatchStarted(detail) {
            calls.push(['started', detail.day, detail.source]);
        },
        warn(message) {
            warnings.push(message);
        }
    });

    await assert.rejects(
        () => controller.play(2, { source: 'auto', delayMs: 20 }),
        /visible failed/
    );

    assert.deepEqual(calls, [
        'begin',
        'reveal',
        'visible',
        'reveal'
    ]);
    assert.equal(warnings.length, 1);
});

test('round prelude continues after avatar ready wait failure', async () => {
    const { TutorialRoundPreludeController } = require('./tutorial/core/round-prelude-controller.js');
    const calls = [];
    const warnings = [];
    const controller = new TutorialRoundPreludeController({
        beginAvatarOverride() {
            calls.push('begin');
        },
        revealPrepared() {
            calls.push('reveal');
        },
        ensureVisible() {
            calls.push('visible');
        },
        waitForAvatarReady() {
            calls.push('ready');
            return Promise.reject(new Error('ready timeout'));
        },
        sleep(delayMs) {
            calls.push(['sleep', delayMs]);
            return Promise.resolve();
        },
        beginTakingOver(detail) {
            calls.push(['takeover', detail.day, detail.source]);
        },
        setLifecycleActive(active) {
            calls.push(['lifecycle', active]);
        },
        showSkipButton() {
            calls.push('skip');
        },
        dispatchStarted(detail) {
            calls.push(['started', detail.day, detail.source]);
        },
        warn(message) {
            warnings.push(message);
        }
    });

    await controller.play(1, { source: 'auto', delayMs: 20 });

    assert.deepEqual(calls, [
        'begin',
        'reveal',
        'visible',
        'ready',
        ['sleep', 20],
        ['takeover', 1, 'auto'],
        ['lifecycle', true],
        'skip',
        ['started', 1, 'auto']
    ]);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /等待 YUI 模型视觉就绪失败/);
});

test('manager delegates avatar floating prelude to TutorialRoundPreludeController', () => {
    const managerClassBlock = managerSource.split('class UniversalTutorialManager {')[1];
    const constructorBlock = managerClassBlock.split('constructor() {')[1].split(
        'this._teardownPromise = null;',
        1
    )[0];
    const startRoundBlock = managerSource.split('async startAvatarFloatingGuideRound(day, options = {}) {')[1].split(
        '    clearModelManagerTutorialRecheckTimer() {',
        1
    )[0];

    assert.match(constructorBlock, /this\._tutorialRoundPreludeController = null;/);
    assert.match(managerSource, /ensureTutorialRoundPreludeController\(\) \{/);
    assert.match(startRoundBlock, /await this\.playAvatarFloatingRoundPrelude\(round,\s*source,\s*director,\s*\{/);
    assert.match(startRoundBlock, /const endReason = completed[\s\S]*\? 'complete'[\s\S]*this\.lifecycleStateStore\.getEndReason\(\)[\s\S]*this\.requestTutorialDestroy\(endReason\);/);
    assert.doesNotMatch(startRoundBlock, /await this\.beginTutorialAvatarOverride\(\)/);
    assert.doesNotMatch(startRoundBlock, /await this\.sleep\(1500\)/);
    assert.doesNotMatch(startRoundBlock, /window\.dispatchEvent\(new CustomEvent\('neko:avatar-floating-guide-started'/);
});

test('director leaves round takeover startup to the round prelude', () => {
    const directorSource = readDirectorSource(__dirname);
    const roundBlock = directorSource.split('        async playAvatarFloatingRound(round, options) {')[1].split(
        '        disableInterrupts() {',
        1
    )[0];

    assert.doesNotMatch(roundBlock, /setTutorialTakingOver\(true\)/);
});

test('full tutorial pages load round prelude controller before manager', () => {
    for (const templatePath of [
        'templates/index.html',
        'templates/api_key_settings.html',
        'templates/memory_browser.html'
    ]) {
        const source = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const preludeIndex = source.indexOf('/static/tutorial/core/round-prelude-controller.js');
        const managerIndex = source.indexOf('/static/tutorial/core/universal-manager.js');

        assert.notEqual(preludeIndex, -1, templatePath + ' should load tutorial/core/round-prelude-controller.js');
        assert.notEqual(managerIndex, -1, templatePath + ' should load tutorial/core/universal-manager.js');
        assert.ok(preludeIndex < managerIndex, templatePath + ' should load round prelude before manager');
    }
});

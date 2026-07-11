const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

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
    assert.match(startRoundBlock, /await this\.playAvatarFloatingRoundPrelude\(round,\s*source,\s*director\);/);
    assert.match(startRoundBlock, /const endReason = completed[\s\S]*\? 'complete'[\s\S]*this\.lifecycleStateStore\.getEndReason\(\)[\s\S]*this\.requestTutorialDestroy\(endReason\);/);
    assert.doesNotMatch(startRoundBlock, /await this\.beginTutorialAvatarOverride\(\)/);
    assert.doesNotMatch(startRoundBlock, /await this\.sleep\(1500\)/);
    assert.doesNotMatch(startRoundBlock, /window\.dispatchEvent\(new CustomEvent\('neko:avatar-floating-guide-started'/);
});

test('director leaves round takeover startup to the round prelude', () => {
    const directorSource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/director.js'), 'utf8');
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

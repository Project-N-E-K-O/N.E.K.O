const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readDirectorSource } = require('./yui-guide-director-test-parts.cjs');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const controllersPath = path.join(__dirname, 'tutorial/visual/controllers.js');
const highlightControllerPath = path.join(__dirname, 'tutorial/visual/highlight-controller.js');
const avatarStandInControllerPath = path.join(__dirname, 'tutorial/avatar/standin-controller.js');
const spotlightControllerPath = path.join(__dirname, 'tutorial/visual/spotlight-controller.js');
const ghostCursorControllerPath = path.join(__dirname, 'tutorial/visual/ghost-cursor-controller.js');
const petalTransitionControllerPath = path.join(__dirname, 'tutorial/visual/petal-transition-controller.js');
const directorSource = readDirectorSource(__dirname);
const controllersSource = fs.existsSync(controllersPath) ? fs.readFileSync(controllersPath, 'utf8') : '';
const highlightControllerSource = fs.existsSync(highlightControllerPath)
    ? fs.readFileSync(highlightControllerPath, 'utf8')
    : '';
const avatarStandInControllerSource = fs.existsSync(avatarStandInControllerPath)
    ? fs.readFileSync(avatarStandInControllerPath, 'utf8')
    : '';
const spotlightControllerSource = fs.existsSync(spotlightControllerPath)
    ? fs.readFileSync(spotlightControllerPath, 'utf8')
    : '';
const ghostCursorControllerSource = fs.existsSync(ghostCursorControllerPath)
    ? fs.readFileSync(ghostCursorControllerPath, 'utf8')
    : '';
const petalTransitionControllerSource = fs.existsSync(petalTransitionControllerPath)
    ? fs.readFileSync(petalTransitionControllerPath, 'utf8')
    : '';

function createFakeStyle(initialValues) {
    const values = Object.assign(Object.create(null), initialValues || {});
    return {
        values,
        get opacity() {
            return values.opacity || '';
        },
        set opacity(value) {
            values.opacity = String(value);
        },
        get transition() {
            return values.transition || '';
        },
        set transition(value) {
            values.transition = String(value);
        },
        setProperty(name, value) {
            values[name] = String(value);
        },
        removeProperty(name) {
            delete values[name];
        }
    };
}

async function withFakeBrowserEnvironment(fakeWindow, fakeDocument, run) {
    const previousWindow = globalThis.window;
    const previousDocument = globalThis.document;
    const previousPerformance = globalThis.performance;
    globalThis.window = fakeWindow;
    globalThis.document = fakeDocument;
    globalThis.performance = fakeWindow.performance;
    try {
        return await run();
    } finally {
        if (typeof previousWindow === 'undefined') {
            delete globalThis.window;
        } else {
            globalThis.window = previousWindow;
        }
        if (typeof previousDocument === 'undefined') {
            delete globalThis.document;
        } else {
            globalThis.document = previousDocument;
        }
        if (typeof previousPerformance === 'undefined') {
            delete globalThis.performance;
        } else {
            globalThis.performance = previousPerformance;
        }
    }
}

test('visual controller module exports the phase three facades', () => {
    assert.ok(fs.existsSync(controllersPath), 'tutorial/visual/controllers.js should exist');
    assert.ok(fs.existsSync(highlightControllerPath), 'tutorial/visual/highlight-controller.js should exist');
    assert.ok(fs.existsSync(avatarStandInControllerPath), 'tutorial/avatar/standin-controller.js should exist');
    assert.ok(fs.existsSync(spotlightControllerPath), 'tutorial/visual/spotlight-controller.js should exist');
    assert.ok(fs.existsSync(ghostCursorControllerPath), 'tutorial/visual/ghost-cursor-controller.js should exist');
    assert.ok(fs.existsSync(petalTransitionControllerPath), 'tutorial/visual/petal-transition-controller.js should exist');
    const visualControllers = require('./tutorial/visual/controllers.js');
    const highlightControllers = require('./tutorial/visual/highlight-controller.js');
    const avatarStandInControllers = require('./tutorial/avatar/standin-controller.js');
    const spotlightControllers = require('./tutorial/visual/spotlight-controller.js');
    const ghostCursorControllers = require('./tutorial/visual/ghost-cursor-controller.js');
    const petalTransitionControllers = require('./tutorial/visual/petal-transition-controller.js');

    for (const exportName of [
        'TutorialHighlightController',
        'YuiGuideGhostCursor',
        'GhostCursorController',
        'SpotlightController',
        'AvatarStandInController',
        'PetalTransitionController',
        'createHighlightController'
    ]) {
        assert.equal(typeof visualControllers[exportName], 'function', exportName + ' should be exported');
        if (exportName === 'TutorialHighlightController' || exportName === 'createHighlightController') {
            assert.equal(typeof highlightControllers[exportName], 'function');
            assert.match(highlightControllerSource, /root\.TutorialHighlightController = api/);
            assert.match(controllersSource, /require\('\.\/highlight-controller\.js'\)/);
            assert.match(controllersSource, /highlightControllerApi\.TutorialHighlightController/);
            assert.match(controllersSource, /highlightControllerApi\.createHighlightController/);
            assert.doesNotMatch(controllersSource, /class TutorialHighlightController/);
        } else if (exportName === 'YuiGuideGhostCursor' || exportName === 'GhostCursorController') {
            assert.equal(typeof ghostCursorControllers[exportName], 'function');
            assert.match(ghostCursorControllerSource, /root\.TutorialGhostCursorController = api/);
            assert.match(ghostCursorControllerSource, new RegExp('class ' + exportName));
            assert.match(controllersSource, /require\('\.\/ghost-cursor-controller\.js'\)/);
            assert.match(controllersSource, /ghostCursorControllerApi\.YuiGuideGhostCursor/);
            assert.match(controllersSource, /ghostCursorControllerApi\.GhostCursorController/);
            assert.doesNotMatch(controllersSource, new RegExp('class ' + exportName));
        } else if (exportName === 'SpotlightController') {
            assert.equal(typeof spotlightControllers.SpotlightController, 'function');
            assert.match(spotlightControllerSource, /root\.TutorialSpotlightController = api/);
            assert.match(spotlightControllerSource, /class SpotlightController/);
            assert.match(controllersSource, /require\('\.\/spotlight-controller\.js'\)/);
            assert.match(controllersSource, /spotlightControllerApi\.SpotlightController/);
            assert.doesNotMatch(controllersSource, /class SpotlightController/);
        } else if (exportName === 'AvatarStandInController') {
            assert.equal(typeof avatarStandInControllers.AvatarStandInController, 'function');
            assert.match(avatarStandInControllerSource, /root\.TutorialAvatarStandInController = api/);
            assert.match(avatarStandInControllerSource, /class AvatarStandInController/);
            assert.match(controllersSource, /require\('\.\.\/avatar\/standin-controller\.js'\)/);
            assert.match(controllersSource, /avatarStandInControllerApi\.AvatarStandInController/);
            assert.doesNotMatch(controllersSource, /class AvatarStandInController/);
        } else if (exportName === 'PetalTransitionController') {
            assert.equal(typeof petalTransitionControllers.PetalTransitionController, 'function');
            assert.match(petalTransitionControllerSource, /root\.TutorialPetalTransitionController = api/);
            assert.match(petalTransitionControllerSource, /class PetalTransitionController/);
            assert.match(controllersSource, /require\('\.\/petal-transition-controller\.js'\)/);
            assert.match(controllersSource, /petalTransitionControllerApi\.PetalTransitionController/);
            assert.doesNotMatch(controllersSource, /class PetalTransitionController/);
        } else {
            assert.match(controllersSource, new RegExp('class ' + exportName));
        }
    }
    assert.match(controllersSource, /root\.TutorialVisualControllers = api/);
    assert.match(controllersSource, /root\.TutorialHighlightController = root\.TutorialHighlightController \|\| \{/);
});

test('full tutorial pages load visual controllers before the director', () => {
    for (const templatePath of [
        'templates/index.html'
    ]) {
        const templateSource = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const avatarStandInControllerIndex = templateSource.indexOf('/static/tutorial/avatar/standin-controller.js');
        const spotlightControllerIndex = templateSource.indexOf('/static/tutorial/visual/spotlight-controller.js');
        const ghostCursorControllerIndex = templateSource.indexOf('/static/tutorial/visual/ghost-cursor-controller.js');
        const petalTransitionControllerIndex = templateSource.indexOf('/static/tutorial/visual/petal-transition-controller.js');
        const highlightControllerIndex = templateSource.indexOf('/static/tutorial/visual/highlight-controller.js');
        const controllersIndex = templateSource.indexOf('/static/tutorial/visual/controllers.js');
        const directorIndex = templateSource.indexOf('/static/tutorial/yui-guide/director/bootstrap.js');

        assert.notEqual(avatarStandInControllerIndex, -1, templatePath + ' should load tutorial/avatar/standin-controller.js');
        assert.notEqual(spotlightControllerIndex, -1, templatePath + ' should load tutorial/visual/spotlight-controller.js');
        assert.notEqual(ghostCursorControllerIndex, -1, templatePath + ' should load tutorial/visual/ghost-cursor-controller.js');
        assert.notEqual(petalTransitionControllerIndex, -1, templatePath + ' should load tutorial/visual/petal-transition-controller.js');
        assert.notEqual(highlightControllerIndex, -1, templatePath + ' should load tutorial/visual/highlight-controller.js');
        assert.notEqual(controllersIndex, -1, templatePath + ' should load tutorial/visual/controllers.js');
        assert.notEqual(directorIndex, -1, templatePath + ' should load tutorial/yui-guide/director parts');
        assert.ok(
            avatarStandInControllerIndex < controllersIndex,
            templatePath + ' should load avatar stand-in controller before visual controllers'
        );
        assert.ok(
            spotlightControllerIndex < controllersIndex,
            templatePath + ' should load spotlight controller before visual controllers'
        );
        assert.ok(
            ghostCursorControllerIndex < controllersIndex,
            templatePath + ' should load ghost cursor controller before visual controllers'
        );
        assert.ok(
            petalTransitionControllerIndex < controllersIndex,
            templatePath + ' should load petal transition controller before visual controllers'
        );
        assert.ok(
            highlightControllerIndex < controllersIndex,
            templatePath + ' should load highlight controller before visual controllers'
        );
        assert.ok(controllersIndex < directorIndex, templatePath + ' should load visual controllers before director');
    }
});

test('director consumes external visual controllers instead of declaring phase three facades inline', () => {
    const constructorBlock = directorSource.split('    class YuiGuideDirector {')[1].split(
        '            this.latestExternalizedChatCursorMoveSceneId =',
        1
    )[0];

    assert.match(directorSource, /const TutorialVisualControllers = window\.TutorialVisualControllers \|\| \{\};/);
    assert.doesNotMatch(directorSource, /class GhostCursorController/);
    assert.doesNotMatch(directorSource, /class YuiGuideGhostCursor/);
    assert.doesNotMatch(directorSource, /class SpotlightController/);
    assert.doesNotMatch(directorSource, /class AvatarStandInController/);
    assert.doesNotMatch(directorSource, /class PetalTransitionController/);
    assert.match(constructorBlock, /new TutorialVisualControllers\.GhostCursorController\(new TutorialVisualControllers\.YuiGuideGhostCursor\(this\.overlay\),\s*\{\s*registry: this\.targetGeometryRegistry\s*\}\)/);
    assert.doesNotMatch(constructorBlock, /this\.highlightController\s*=/);
    assert.match(constructorBlock, /new TutorialVisualControllers\.SpotlightController\(TutorialVisualControllers\.createHighlightController\(\{/);
    assert.match(constructorBlock, /registry: this\.targetGeometryRegistry/);
    assert.match(constructorBlock, /new TutorialVisualControllers\.AvatarStandInController\(this\)/);
    assert.match(constructorBlock, /new TutorialVisualControllers\.PetalTransitionController\(this\)/);
});

test('YuiGuideGhostCursor is a thin overlay adapter for GhostCursorController', () => {
    const { YuiGuideGhostCursor } = require('./tutorial/visual/controllers.js');
    const overlay = { id: 'overlay' };
    const cursor = new YuiGuideGhostCursor(overlay);

    assert.equal(cursor.overlay, overlay);
    assert.equal(cursor.lastTarget, null);
    cursor.lastTarget = { x: 12, y: 24 };
    assert.deepEqual(cursor.lastTarget, { x: 12, y: 24 });

    for (const methodName of [
        'hasPosition',
        'hasVisiblePosition',
        'showAt',
        'moveToPoint',
        'moveToRect',
        'click',
        'wobble',
        'reactToUserMotion',
        'resistTo',
        'runPauseAwareEllipse',
        'clearPosition',
        'hide',
        'cancel'
    ]) {
        assert.equal(Object.prototype.hasOwnProperty.call(YuiGuideGhostCursor.prototype, methodName), false);
    }
});

test('TutorialHighlightController preserves spotlight state while paused for resistance', () => {
    const { TutorialHighlightController } = require('./tutorial/visual/highlight-controller.js');
    const overlayCalls = [];
    const overlay = {
        activateSpotlight(element) {
            overlayCalls.push(['activate', element.id || '']);
        },
        clearActionSpotlight() {
            overlayCalls.push(['clearAction']);
        },
        setPersistentSpotlight(element) {
            overlayCalls.push(['persistent', element.id || '']);
        },
        clearPersistentSpotlight() {
            overlayCalls.push(['clearPersistent']);
        },
        activateSecondarySpotlight(element) {
            overlayCalls.push(['secondary', element.id || '']);
        },
        setExtraSpotlights(elements) {
            overlayCalls.push(['extra', elements.slice()]);
        },
        clearExtraSpotlights() {
            overlayCalls.push(['clearExtra']);
        }
    };
    const createElement = (tagName, rect) => ({
        tagName,
        id: '',
        attributes: Object.create(null),
        isConnected: true,
        parentNode: null,
        style: {},
        closest() {
            return null;
        },
        matches() {
            return false;
        },
        getBoundingClientRect() {
            return rect || { left: 10, top: 20, right: 90, bottom: 70, width: 80, height: 50 };
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        getAttribute(name) {
            return this.attributes[name] || '';
        },
        removeAttribute(name) {
            delete this.attributes[name];
        }
    });
    const fakeDocument = {
        body: {
            children: [],
            appendChild(element) {
                this.children.push(element);
                element.parentNode = this;
            },
            removeChild(element) {
                this.children = this.children.filter((candidate) => candidate !== element);
                element.parentNode = null;
            }
        },
        createElement(tagName) {
            return createElement(tagName);
        }
    };
    const target = createElement('button');
    target.id = 'galgame-button';
    const controller = new TutorialHighlightController({
        document: fakeDocument,
        window: { innerWidth: 800, innerHeight: 600 },
        overlay
    });

    controller.applyGuideHighlights({ primary: target });
    const virtual = controller.createVirtualSpotlight('resistance-target', {
        left: 100,
        top: 120,
        right: 180,
        bottom: 180,
        width: 80,
        height: 60
    });
    controller.setSceneExtraSpotlights([target]);
    controller.setSpotlightGeometryHint(target, { padding: 4, geometry: 'circle' });
    overlayCalls.length = 0;

    controller.pause();
    controller.applyGuideHighlights({ primary: null, persistent: null, secondary: null });
    const pausedTarget = createElement('button');
    pausedTarget.id = 'paused-target';
    pausedTarget.matches = (selector) => selector === '.composer-tool-btn, .composer-icon-button[data-avatar-tool-id]';
    controller.applyCircularFloatingButtonSpotlightHint(pausedTarget);
    controller.clearAllVirtualSpotlights();
    controller.clearSceneExtraSpotlights();
    controller.clearAllExtraSpotlights();
    controller.clearSpotlightGeometryHints();
    controller.clearSpotlightVariantHints();

    assert.deepEqual(overlayCalls, []);
    assert.equal(fakeDocument.body.children.includes(virtual), true);
    assert.equal(controller.virtualSpotlights.has('resistance-target'), true);
    assert.equal(target.attributes['data-yui-guide-spotlight-padding'], '4');
    assert.equal(pausedTarget.attributes['data-yui-guide-spotlight-padding'], undefined);
});

test('SpotlightController consumes target geometry registry for semantic target lookups', () => {
    const { SpotlightController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const registry = {
        resolve(key) {
            calls.push(['resolve', key]);
            if (key !== 'chat-capsule-input') {
                return null;
            }
            return {
                key,
                externalKind: 'capsule-input',
                shape: 'rounded-rect',
                localSelectors: ['.chat-capsule-input', '[data-guide-target="chat-capsule-input"]']
            };
        },
        getExternalKind(key) {
            calls.push(['getExternalKind', key]);
            return key === 'chat-capsule-input' ? 'capsule-input' : '';
        },
        getLocalSelectors(key) {
            calls.push(['getLocalSelectors', key]);
            return key === 'chat-capsule-input'
                ? ['.chat-capsule-input']
                : [];
        }
    };
    const controller = new SpotlightController(null, { registry });

    assert.deepEqual(controller.resolveTargetEntry('chat-capsule-input'), {
        key: 'chat-capsule-input',
        externalKind: 'capsule-input',
        shape: 'rounded-rect',
        localSelectors: ['.chat-capsule-input', '[data-guide-target="chat-capsule-input"]']
    });
    assert.equal(controller.getExternalKind('chat-capsule-input'), 'capsule-input');
    assert.deepEqual(controller.getLocalSelectors('chat-capsule-input'), ['.chat-capsule-input']);
    assert.deepEqual(calls, [
        ['resolve', 'chat-capsule-input'],
        ['getExternalKind', 'chat-capsule-input'],
        ['getLocalSelectors', 'chat-capsule-input']
    ]);
});

test('GhostCursorController consumes target geometry registry for cursor target lookups', () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const registry = {
        resolve(key) {
            if (key !== 'chat-tool-toggle') {
                return null;
            }
            return {
                key,
                externalKind: 'tool-toggle',
                localSelectors: ['.send-button-circle.compact-input-tool-toggle']
            };
        },
        getExternalKind(key) {
            return key === 'chat-tool-toggle' ? 'tool-toggle' : '';
        },
        getLocalSelectors(key) {
            return key === 'chat-tool-toggle'
                ? ['.send-button-circle.compact-input-tool-toggle']
                : [];
        }
    };
    const controller = new GhostCursorController({}, { registry });

    assert.deepEqual(controller.resolveTargetEntry('chat-tool-toggle'), {
        key: 'chat-tool-toggle',
        externalKind: 'tool-toggle',
        localSelectors: ['.send-button-circle.compact-input-tool-toggle']
    });
    assert.equal(controller.getExternalKind('chat-tool-toggle'), 'tool-toggle');
    assert.deepEqual(controller.getLocalSelectors('chat-tool-toggle'), ['.send-button-circle.compact-input-tool-toggle']);
});

test('SpotlightController owns pause and resume facade hooks', () => {
    const { SpotlightController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const controller = new SpotlightController({
        pause() {
            calls.push('pause');
            return 'paused';
        },
        resume() {
            calls.push('resume');
            return 'resumed';
        }
    });

    assert.equal(controller.pause(), 'paused');
    assert.equal(controller.resume(), 'resumed');
    assert.deepEqual(calls, ['pause', 'resume']);

    const noPauseController = new SpotlightController({});
    assert.equal(noPauseController.pause(), undefined);
    assert.equal(noPauseController.resume(), undefined);
});

test('SpotlightController exposes explicit highlight primitive methods without string dispatch', () => {
    const { SpotlightController } = require('./tutorial/visual/controllers.js');
    const spotlightBlock = spotlightControllerSource.split('    class SpotlightController {')[1].split(
        '\n    return {\n        SpotlightController',
        1
    )[0];
    const calls = [];
    const primitive = {
        createVirtualSpotlight(key, rect, options) {
            calls.push(['createVirtualSpotlight', key, rect, options]);
            return { key, rect, options };
        },
        normalizeHighlightTarget(target, fallbackKey) {
            calls.push(['normalizeHighlightTarget', target, fallbackKey]);
            return target || fallbackKey;
        },
        applyGuideHighlights(config) {
            calls.push(['applyGuideHighlights', config]);
            return { primary: config.primary };
        },
        clearAllExtraSpotlights() {
            calls.push(['clearAllExtraSpotlights']);
        },
        destroy() {
            calls.push(['destroy']);
        }
    };
    const controller = new SpotlightController(primitive);

    assert.doesNotMatch(spotlightBlock, /call\(methodName/);
    for (const methodName of [
        'createVirtualSpotlight',
        'normalizeHighlightTarget',
        'applyGuideHighlights',
        'clearAllExtraSpotlights',
        'destroy'
    ]) {
        assert.doesNotMatch(spotlightBlock, new RegExp("this\\.call\\('" + methodName + "'"));
    }

    assert.deepEqual(controller.createVirtualSpotlight('target', { left: 1 }, { padding: 4 }), {
        key: 'target',
        rect: { left: 1 },
        options: { padding: 4 }
    });
    assert.equal(controller.normalizeHighlightTarget('selector', 'fallback'), 'selector');
    assert.deepEqual(controller.applyGuideHighlights({ primary: 'button' }), { primary: 'button' });
    controller.clearAllExtraSpotlights();
    controller.destroy();
    assert.deepEqual(calls, [
        ['createVirtualSpotlight', 'target', { left: 1 }, { padding: 4 }],
        ['normalizeHighlightTarget', 'selector', 'fallback'],
        ['applyGuideHighlights', { primary: 'button' }],
        ['clearAllExtraSpotlights'],
        ['destroy']
    ]);

    const fallbackController = new SpotlightController(null);
    assert.equal(fallbackController.createVirtualSpotlight('missing'), null);
    assert.equal(fallbackController.normalizeHighlightTarget('missing'), null);
    assert.deepEqual(fallbackController.applyGuideHighlights({ primary: 'missing' }), {});
    assert.equal(fallbackController.clearAllExtraSpotlights(), undefined);
    assert.equal(fallbackController.destroy(), undefined);
});

test('GhostCursorController owns pause and resume facade hooks', () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const controller = new GhostCursorController({
        pause() {
            calls.push('pause');
            return 'paused';
        },
        cancel() {
            calls.push('cancel');
            return 'cancelled';
        },
        resume() {
            calls.push('resume');
            return 'resumed';
        }
    });

    assert.equal(controller.pause(), 'paused');
    assert.equal(controller.resume(), 'resumed');
    assert.equal(controller.cancel(), 'cancelled');
    assert.deepEqual(calls, ['pause', 'resume', 'cancel']);

    const noPauseController = new GhostCursorController({
        cancel() {
            calls.push('cancel-only');
        }
    });
    assert.equal(noPauseController.pause(), undefined);
    assert.equal(noPauseController.resume(), undefined);
});

test('GhostCursorController preserves overlay and lastTarget legacy state accessors', () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const legacy = {
        overlay: null,
        lastTarget: null
    };
    const controller = new GhostCursorController(legacy);
    const overlay = { id: 'overlay' };
    const lastTarget = { x: 12, y: 24 };

    controller.overlay = overlay;
    controller.lastTarget = lastTarget;

    assert.equal(legacy.overlay, overlay);
    assert.equal(legacy.lastTarget, lastTarget);
    assert.equal(controller.overlay, overlay);
    assert.equal(controller.lastTarget, lastTarget);
});

test('GhostCursorController owns moveToPoint slowdown and cancellation semantics', async () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const overlay = {
        moveCursorTo(x, y, options) {
            calls.push(['move', x, y, options.durationMs, options.cancelCheck()]);
            this.lastMoveOptions = options;
            this.lastCancelCheck = options.cancelCheck;
            return Promise.resolve(!options.cancelCheck());
        }
    };
    const legacy = {
        overlay,
        lastTarget: null,
        resistanceRestPoint: { x: 1, y: 2 },
        resistanceToken: 3,
        moveToPoint() {
            throw new Error('legacy moveToPoint should not be called');
        }
    };
    const controller = new GhostCursorController(legacy);

    assert.equal(await controller.moveToPoint(30, 40, {
        durationMs: 480,
        effect: 'click',
        effectDurationMs: 480,
        cancelCheck: () => false
    }), true);
    assert.deepEqual(calls[0], ['move', 30, 40, 3780, false]);
    assert.equal(overlay.lastMoveOptions.effect, 'click');
    assert.equal(overlay.lastMoveOptions.effectDurationMs, 3780);
    assert.deepEqual(controller.lastTarget, { x: 30, y: 40 });
    assert.equal(controller.resistanceRestPoint, null);
    assert.equal(controller.resistanceToken, 1);

    const secondMove = controller.moveToPoint(50, 60, {
        durationMs: 1200,
        cancelCheck: () => false
    });
    assert.equal(overlay.lastCancelCheck(), false);
    controller.cancel();
    assert.equal(overlay.lastCancelCheck(), true);
    assert.equal(await secondMove, true);
    assert.deepEqual(calls[1], ['move', 50, 60, 2100, false]);
});

test('GhostCursorController pause keeps in-flight movement resumable', async () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const overlay = {
        moveCursorTo(x, y, options) {
            this.lastCancelCheck = options.cancelCheck;
            return Promise.resolve(!options.cancelCheck());
        }
    };
    const legacy = {
        overlay,
        cancel() {
            this.cancelled = true;
        },
        pause() {
            this.paused = true;
        },
        resume() {
            this.resumed = true;
        }
    };
    const controller = new GhostCursorController(legacy);

    const movePromise = controller.moveToPoint(80, 90, {
        durationMs: 1200,
        cancelCheck: () => false
    });
    assert.equal(overlay.lastCancelCheck(), false);

    controller.pause();
    assert.equal(legacy.paused, true);
    assert.equal(legacy.cancelled, undefined);
    assert.equal(overlay.lastCancelCheck(), false);

    controller.resume();
    assert.equal(legacy.resumed, true);
    assert.equal(await movePromise, true);
}
);

test('GhostCursorController owns resistance and user-motion reaction movement', async () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const overlay = {
        cursorPosition: { x: 100, y: 100 },
        hasCursorPosition() {
            return !!this.cursorPosition;
        },
        isCursorVisible() {
            return true;
        },
        getCursorPosition() {
            return this.cursorPosition;
        },
        moveCursorTo(x, y, options) {
            calls.push(['move', Math.round(x), Math.round(y), options.durationMs, options.forcePcOverlay === true]);
            this.cursorPosition = { x, y };
            return Promise.resolve(true);
        }
    };
    const legacy = {
        overlay,
        resistTo() {
            throw new Error('legacy resistTo should not be called');
        },
        reactToUserMotion() {
            throw new Error('legacy reactToUserMotion should not be called');
        }
    };
    const controller = new GhostCursorController(legacy);

    await controller.resistTo(160, 100, { motionDx: 4, motionDy: 0 });
    assert.deepEqual(calls.splice(0), [
        ['move', 82, 100, 180, false],
        ['move', 100, 100, 260, false]
    ]);

    await controller.reactToUserMotion(160, 100, {
        motionDx: 4,
        motionDy: 0,
        outDurationMs: 120,
        backDurationMs: 220
    });
    assert.deepEqual(calls, [
        ['move', 82, 100, 120, false],
        ['move', 100, 100, 220, false]
    ]);
    calls.length = 0;

    await controller.resistTo(160, 100, { motionDx: 4, motionDy: 0, forcePcOverlay: true });
    assert.deepEqual(calls, [
        ['move', 82, 100, 180, true],
        ['move', 100, 100, 260, true]
    ]);
});

test('GhostCursorController owns pause-aware ellipse cancellation semantics', async () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const overlay = {
        runEllipseAnimation(...args) {
            calls.push([
                args[0],
                args[1],
                args[2],
                args[3],
                args[4],
                typeof args[5],
                typeof args[6],
                args[7]()
            ]);
            this.lastCancelCheck = args[7];
            return Promise.resolve(true);
        }
    };
    const legacy = {
        overlay,
        resistanceRestPoint: { x: 3, y: 4 },
        resistanceToken: 5,
        runPauseAwareEllipse() {
            throw new Error('legacy runPauseAwareEllipse should not be called');
        }
    };
    const controller = new GhostCursorController(legacy);

    assert.equal(await controller.runPauseAwareEllipse(
        50,
        60,
        12,
        18,
        900,
        () => false,
        () => false,
        () => false
    ), true);
    assert.deepEqual(calls[0], [50, 60, 12, 18, 900, 'function', 'function', false]);
    assert.equal(controller.resistanceRestPoint, null);
    assert.equal(controller.resistanceToken, 1);

    controller.cancel();
    assert.equal(overlay.lastCancelCheck(), true);
});

test('GhostCursorController owns basic cursor overlay operations', () => {
    const { GhostCursorController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const overlay = {
        showCursorAt(x, y) {
            calls.push(['show', x, y]);
        },
        clickCursor(durationMs) {
            calls.push(['click', durationMs]);
        },
        wobbleCursor(durationMs) {
            calls.push(['wobble', durationMs]);
        },
        hideCursor() {
            calls.push(['hide']);
        },
        clearCursorPosition() {
            calls.push(['clear']);
        }
    };
    const legacy = {
        overlay,
        resistanceRestPoint: { x: 1, y: 2 },
        resistanceToken: 9,
        showAt() {
            throw new Error('legacy showAt should not be called');
        },
        click() {
            throw new Error('legacy click should not be called');
        },
        wobble() {
            throw new Error('legacy wobble should not be called');
        },
        hide() {
            throw new Error('legacy hide should not be called');
        },
        clearPosition() {
            throw new Error('legacy clearPosition should not be called');
        }
    };
    const controller = new GhostCursorController(legacy);

    controller.showAt(12, 24);
    controller.click(420);
    controller.wobble(360);
    controller.hide();
    controller.clearPosition();

    assert.deepEqual(calls, [
        ['show', 12, 24],
        ['click', 420],
        ['wobble', 360],
        ['hide'],
        ['clear']
    ]);
    assert.equal(controller.resistanceRestPoint, null);
    assert.equal(controller.resistanceToken, 3);
});

test('GhostCursorController no longer delegates core cursor motion primitives to legacy cursor', () => {
    const controllerBlock = ghostCursorControllerSource.split('    class GhostCursorController {')[1].split(
        '\n    return {\n        YuiGuideGhostCursor',
        1
    )[0];

    for (const methodName of [
        'showAt',
        'moveToPoint',
        'moveToRect',
        'click',
        'wobble',
        'reactToUserMotion',
        'resistTo',
        'runPauseAwareEllipse',
        'clearPosition',
        'hide'
    ]) {
        assert.doesNotMatch(controllerBlock, new RegExp('legacyCursor\\.' + methodName + '\\s*\\('));
    }
});

test('PetalTransitionController owns return transition active guard', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    let finishFirstTransition;
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false
    };
    const controller = new PetalTransitionController(director);
    controller.executeReturnTransition = (options) => {
        calls.push(options.id);
        assert.equal(director.returnPetalTransitionActive, true);
        if (options.id === 'first') {
            return new Promise((resolve) => {
                finishFirstTransition = resolve;
            });
        }
        return Promise.resolve();
    };

    const first = controller.playReturn({ id: 'first' });
    const second = controller.playReturn({ id: 'second' });

    assert.deepEqual(calls, ['first']);
    assert.equal(second, undefined);
    assert.equal(controller.isActive(), true);
    assert.equal(director.returnPetalTransitionActive, true);

    finishFirstTransition();
    await first;

    assert.equal(controller.isActive(), false);
    assert.equal(director.returnPetalTransitionActive, false);

    await controller.playReturn({ id: 'third' });
    assert.deepEqual(calls, ['first', 'third']);
});

test('PetalTransitionController owns return transition start notification guard', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    let transitionStartCount = 0;
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false
    };
    const controller = new PetalTransitionController(director);
    controller.executeReturnTransition = (options) => {
        options.onTransitionStart();
        options.onTransitionStart();
        return Promise.resolve();
    };

    await controller.playReturn({
        onTransitionStart() {
            transitionStartCount += 1;
        }
    });

    assert.equal(transitionStartCount, 1);
    assert.equal(controller.isActive(), false);
});

test('PetalTransitionController owns return transition duration normalization', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const timings = [];
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false,
        reducedMotion: false,
        shouldReduceTutorialMotion() {
            return this.reducedMotion;
        },
        loadReturnPetalSequence() {
            return Promise.resolve(null);
        }
    };
    const controller = new PetalTransitionController(director);
    const captureTiming = (options) => {
        const context = controller.createReturnPlaybackContext(options);
        timings.push({
            baseTransitionDurationMs: context.baseTransitionDurationMs,
            transitionDurationMs: context.transitionDurationMs,
            finalOpacity: context.finalOpacity
        });
    };

    captureTiming({});
    director.reducedMotion = true;
    captureTiming({});
    director.reducedMotion = false;
    captureTiming({ durationMs: 3000 });

    assert.deepEqual(timings, [
        { baseTransitionDurationMs: 4800, transitionDurationMs: 6200, finalOpacity: 0.6 },
        { baseTransitionDurationMs: 420, transitionDurationMs: 420, finalOpacity: 0.6 },
        { baseTransitionDurationMs: 3000, transitionDurationMs: 6200, finalOpacity: 0.6 }
    ]);

    assert.doesNotMatch(directorSource, /playReturnPetalTransitionLegacy/);
    assert.match(petalTransitionControllerSource, /const result = this\.executeReturnTransition\(returnOptions\);/);
});

test('PetalTransitionController owns return petal sequence loading', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const loadedUrls = [];
    let imageConstructCount = 0;
    class FakeImage {
        constructor() {
            imageConstructCount += 1;
            this.naturalWidth = 512;
            this.naturalHeight = 256;
            this.width = 128;
            this.height = 64;
            this.decoding = '';
        }

        set src(value) {
            this._src = value;
            loadedUrls.push(value);
            this.onload();
        }

        get src() {
            return this._src;
        }
    }
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false,
        shouldReduceTutorialMotion() {
            return false;
        },
        loadReturnPetalSequence() {
            throw new Error('director should not own return petal sequence loading');
        }
    };
    const controller = new PetalTransitionController(director);
    const previousImage = globalThis.Image;
    globalThis.Image = FakeImage;

    try {
        const context = controller.createReturnPlaybackContext({});
        const receivedSequence = await context.petalSequencePromise;
        const cachedSequence = await controller.preloadReturnPetalSequence();

        assert.deepEqual(loadedUrls, ['/static/assets/tutorial/petals/yui-guide-petal-transition.webp']);
        assert.equal(imageConstructCount, 1);
        assert.equal(receivedSequence.url, '/static/assets/tutorial/petals/yui-guide-petal-transition.webp');
        assert.equal(receivedSequence.width, 512);
        assert.equal(receivedSequence.height, 256);
        assert.equal(cachedSequence, receivedSequence);
    } finally {
        if (typeof previousImage === 'undefined') {
            delete globalThis.Image;
        } else {
            globalThis.Image = previousImage;
        }
    }

    assert.doesNotMatch(directorSource, /playReturnPetalTransitionLegacy/);
    assert.match(petalTransitionControllerSource, /returnOptions\.petalSequencePromise = this\.preloadReturnPetalSequence\(\);/);
    assert.doesNotMatch(petalTransitionControllerSource, /director\.loadReturnPetalSequence/);
});

test('PetalTransitionController owns return avatar restore strategy', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false,
        tutorialManager: {
            restoreTutorialAvatarOverride() {
                calls.push('manager:restore-avatar');
                return Promise.resolve(true);
            }
        },
        restoreTutorialAvatarForReturnPetalTransition() {
            throw new Error('director restore wrapper should not be called');
        }
    };
    const controller = new PetalTransitionController(director);
    controller.returnPetalSequencePromise = Promise.resolve(null);
    controller.fadeReturnModelOut = () => Promise.resolve(true);
    controller.restoreOpacityTargets = () => {
        calls.push('restore-opacity');
    };
    controller.waitForNarrationEnd = () => Promise.resolve();

    await controller.playReturn({ durationMs: 0 });

    assert.deepEqual(calls, [
        'manager:restore-avatar',
        'restore-opacity',
        'restore-opacity'
    ]);
});

test('PetalTransitionController owns return transition playback context', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const origin = { x: 42, y: 84 };
    const calls = [];
    const sequence = { url: '/petal.webp' };
    const transition = {
        done() {
            calls.push('transition:done');
            return Promise.resolve();
        },
        finish() {
            calls.push('transition:finish');
            return Promise.resolve();
        }
    };
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false,
        overlay: {
            isPcOverlayActive() {
                calls.push('overlay:active');
                return true;
            },
            playPetalTransition(receivedOrigin, options) {
                calls.push(['pc', receivedOrigin, options.durationMs, options.finalOpacity]);
                return true;
            }
        },
        shouldReduceTutorialMotion() {
            return false;
        },
        getReturnPetalTransitionOrigin() {
            calls.push('origin');
            return origin;
        },
        tutorialManager: {
            restoreTutorialAvatarOverride() {
                calls.push('restore-avatar');
                return Promise.resolve(true);
            }
        },
        playReturnPetalTransitionLegacy: undefined
    };
    const controller = new PetalTransitionController(director);
    controller.returnPetalSequencePromise = Promise.resolve(sequence);
    controller.fadeReturnModelOut = (durationMs) => {
        calls.push(['fade', durationMs]);
        return Promise.resolve(true);
    };
    controller.createReturnPetalTransition = (receivedOrigin, options) => {
        calls.push(['dom', receivedOrigin, options.durationMs, options.finalOpacity, options.sequence, !!options.skipPcOverlay]);
        return transition;
    };
    controller.restoreOpacityTargets = () => {
        calls.push('restore-opacity');
    };
    controller.waitForNarrationEnd = (durationMs) => {
        calls.push(['wait', durationMs]);
        return Promise.resolve();
    };

    await controller.playReturn({ durationMs: 0 });

    assert.deepEqual(calls, [
        'origin',
        'overlay:active',
        ['pc', origin, 6200, 0.6],
        ['fade', 0],
        ['dom', origin, 6200, 0.6, sequence, true],
        ['wait', 0],
        'restore-avatar',
        'restore-opacity',
        'transition:done',
        'transition:finish',
        'restore-opacity'
    ]);
    assert.equal(controller.isActive(), false);
});

test('PetalTransitionController consumes injected return transition factory', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const sequence = { url: '/static/assets/tutorial/petals/yui-guide-petal-transition.webp' };
    const origin = { x: 123, y: 456 };
    const calls = [];
    const director = {
        destroyed: false,
        returnPetalTransitionActive: false,
        overlay: {
            playPetalTransition() {
                return null;
            }
        },
        shouldReduceTutorialMotion() {
            return false;
        }
    };
    const controller = new PetalTransitionController(director);
    controller.fadeReturnModelOut = () => Promise.resolve(true);
    controller.restoreTutorialAvatarForReturn = () => Promise.resolve(true);
    controller.restoreOpacityTargets = () => {};
    controller.waitForNarrationEnd = () => Promise.resolve();
    controller.createReturnPetalTransition = () => {
        throw new Error('default transition factory should not be used');
    };

    await controller.executeReturnTransition({
        origin,
        durationMs: 3000,
        baseTransitionDurationMs: 3000,
        transitionDurationMs: 6200,
        finalOpacity: 0.6,
        canStartPcPetalImmediately: true,
        petalSequencePromise: Promise.resolve(sequence),
        createReturnPetalTransition(receivedOrigin, options) {
            calls.push({
                origin: receivedOrigin,
                durationMs: options && options.durationMs,
                finalOpacity: options && options.finalOpacity,
                sequence: options && options.sequence,
                skipPcOverlay: !!(options && options.skipPcOverlay)
            });
            return null;
        }
    });

    assert.deepEqual(calls, [
        {
            origin,
            durationMs: 6200,
            finalOpacity: 0.6,
            sequence,
            skipPcOverlay: true
        }
    ]);
});

test('PetalTransitionController creates DOM return petal transitions', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const appendedLayers = [];
    const stage = {
        querySelector() {
            return null;
        },
        appendChild(layer) {
            appendedLayers.push(layer);
            layer.parentNode = stage;
        }
    };
    const fakeDocument = {
        createElement(tagName) {
            return {
                tagName,
                className: '',
                attributes: Object.create(null),
                children: [],
                parentNode: null,
                style: createFakeStyle(),
                classList: {
                    values: [],
                    add(value) {
                        this.values.push(value);
                    }
                },
                setAttribute(name, value) {
                    this.attributes[name] = String(value);
                },
                appendChild(child) {
                    this.children.push(child);
                    child.parentNode = this;
                },
                removeAttribute(name) {
                    delete this.attributes[name];
                }
            };
        },
        body: {
            classList: { add() {}, remove() {} },
            style: createFakeStyle()
        }
    };
    const fakeWindow = {
        innerWidth: 800,
        innerHeight: 600,
        requestAnimationFrame(callback) {
            callback(16);
            return 1;
        },
        setTimeout(callback) {
            callback();
            return 1;
        },
        clearTimeout() {},
        performance: {
            now() {
                return 0;
            }
        }
    };
    const director = {
        overlay: {
            ensureRoot() {
                return {
                    querySelector(selector) {
                        return selector === '.yui-guide-stage' ? stage : null;
                    }
                };
            }
        },
        shouldReduceTutorialMotion() {
            return false;
        },
        getViewportCenter() {
            return { x: 400, y: 300 };
        }
    };
    const controller = new PetalTransitionController(director);
    let started = false;

    await withFakeBrowserEnvironment(fakeWindow, fakeDocument, async () => {
        const transition = controller.createReturnPetalTransition({ x: 12, y: 34 }, {
            durationMs: 900,
            finalOpacity: 0.4,
            sequence: { url: '/petal.webp' },
            onStart() {
                started = true;
            }
        });

        assert.equal(typeof transition.done, 'function');
        assert.equal(typeof transition.finish, 'function');
    });

    const layer = appendedLayers[0];
    const playback = layer.children[0];
    assert.equal(layer.className, 'yui-guide-petal-transition');
    assert.equal(playback.className, 'yui-guide-petal-sequence');
    assert.equal(playback.src, '/petal.webp');
    assert.equal(playback.style.values['--yui-guide-petal-origin-x'], '12px');
    assert.equal(playback.style.values['--yui-guide-petal-origin-y'], '34px');
    assert.equal(playback.style.values['--yui-guide-petal-final-opacity'], '0.4');
    assert.equal(started, true);
});

test('PetalTransitionController fades return transition opacity targets', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const appliedOpacities = [];
    const model = { alpha: 1 };
    const element = {
        style: createFakeStyle({ opacity: '0.8', transition: 'opacity 120ms ease' }),
        getBoundingClientRect() {
            return { width: 120, height: 80 };
        }
    };
    const fakeDocument = {
        querySelector(selector) {
            return selector === '#live2d-container' ? element : null;
        },
        body: {
            classList: {
                values: [],
                add(value) {
                    this.values.push(value);
                },
                remove() {}
            },
            style: createFakeStyle()
        }
    };
    const frameTimes = [0, 240];
    const fakeWindow = {
        live2dManager: {
            currentModel: model
        },
        getComputedStyle(target) {
            return { opacity: target.style.opacity || '1' };
        },
        requestAnimationFrame(callback) {
            callback(frameTimes.shift());
            return 1;
        },
        performance: {
            now() {
                return 0;
            }
        }
    };
    const director = {
        destroyed: false,
        resolveModelPrefix() {
            return 'live2d';
        },
        shouldReduceTutorialMotion() {
            return false;
        }
    };
    const controller = new PetalTransitionController(director);
    const originalSetProperty = element.style.setProperty.bind(element.style);
    element.style.setProperty = (name, value) => {
        originalSetProperty(name, value);
        if (name === 'opacity') {
            appliedOpacities.push(Number(value));
        }
    };

    const result = await withFakeBrowserEnvironment(fakeWindow, fakeDocument, () => (
        controller.fadeReturnModelOut(240)
    ));

    assert.equal(result, true);
    assert.equal(fakeDocument.body.classList.values.includes('yui-guide-return-petal-fade'), true);
    assert.equal(fakeDocument.body.style.values['--yui-guide-return-avatar-opacity'], '0');
    assert.deepEqual(appliedOpacities, [0.8, 0]);
    assert.equal(model.alpha, 0);
});

test('PetalTransitionController owns return opacity target collection and restore', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const clearedTimers = [];
    const model = { alpha: 0.65 };
    const element = {
        style: createFakeStyle({ opacity: '0.7', transition: 'opacity 100ms ease' }),
        getBoundingClientRect() {
            return { width: 100, height: 40 };
        }
    };
    const hiddenElement = {
        style: createFakeStyle({ opacity: '1' }),
        getBoundingClientRect() {
            return { width: 0, height: 0 };
        }
    };
    const manager = {
        currentModel: model,
        _canvasRevealTimer: 99,
        canvas: element
    };
    const fakeDocument = {
        querySelector(selector) {
            if (selector === '#live2d-container') {
                return element;
            }
            if (selector === '#live2d-canvas') {
                return hiddenElement;
            }
            return null;
        },
        body: {
            classList: {
                removed: [],
                add() {},
                remove(value) {
                    this.removed.push(value);
                }
            },
            style: createFakeStyle({ '--yui-guide-return-avatar-opacity': '0.2' })
        }
    };
    const fakeWindow = {
        live2dManager: manager,
        clearTimeout(timerId) {
            clearedTimers.push(timerId);
        },
        getComputedStyle(target) {
            return { opacity: target.style.opacity || '1' };
        },
        performance: {
            now() {
                return 0;
            }
        }
    };
    const director = {
        resolveModelPrefix() {
            return 'live2d';
        }
    };
    const controller = new PetalTransitionController(director);

    await withFakeBrowserEnvironment(fakeWindow, fakeDocument, async () => {
        const targets = controller.prepareReturnOpacityTargets(controller.getReturnModel());

        assert.equal(targets.length, 2);
        assert.deepEqual(clearedTimers, [99]);
        assert.equal(manager._canvasRevealTimer, null);
        targets.forEach((target) => target.apply(0.25));
        assert.equal(element.style.values.transition, 'none');
        assert.equal(element.style.values.opacity, '0.25');
        assert.equal(model.alpha, 0.25);

        controller.restoreOpacityTargets();
    });

    assert.equal(element.style.values.transition, 'opacity 100ms ease');
    assert.equal(element.style.values.opacity, '0.7');
    assert.equal(model.alpha, 0.65);
    assert.deepEqual(fakeDocument.body.classList.removed, ['yui-guide-return-petal-fade']);
    assert.equal(fakeDocument.body.style.values['--yui-guide-return-avatar-opacity'], undefined);
});

test('PetalTransitionController owns cue-triggered petal cleanup flow', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const director = {
        sceneRunId: 7,
        destroyed: false,
        returnPetalTransitionActive: false,
        loadReturnPetalSequence() {
            calls.push('preload');
            return Promise.resolve(null);
        },
        getAvatarFloatingNarrationDurationMs() {
            return 4000;
        },
        waitForSceneDelay(delayMs) {
            calls.push(['wait', delayMs]);
            return Promise.resolve(true);
        },
        isStopping() {
            return false;
        },
        cursor: {
            hide() {
                calls.push('cursor:hide');
            }
        },
        clearExternalizedChatGuideTarget(options) {
            calls.push(['external:clear', options.clearCursor]);
        },
        overlay: {
            clearPersistentSpotlight() {
                calls.push('spotlight:persistent');
            },
            clearActionSpotlight() {
                calls.push('spotlight:action');
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('spotlight:scene-extra');
        },
        clearRetainedExtraSpotlights() {
            calls.push('spotlight:retained');
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        disableInterrupts() {
            calls.push('interrupts:disable');
        },
        runReturnControlCueWavePerformance() {
            calls.push('wave');
            return Promise.resolve();
        },
        shouldReduceTutorialMotion() {
            return false;
        },
        playAvatarFloatingPetalTransitionAtCueLegacy: undefined
    };
    const controller = new PetalTransitionController(director);
    controller.preloadReturnPetalSequence = () => {
        calls.push('preload');
        return Promise.resolve(null);
    };
    controller.executeReturnTransition = (options) => {
        calls.push(['return', options.durationMs]);
        options.onTransitionStart();
        options.onTransitionStart();
        return Promise.resolve();
    };

    await controller.playAtCue({ id: 'wrap' }, 7, 'voice', 'text', Date.now() - 100000);

    assert.deepEqual(calls, [
        'preload',
        ['wait', 0],
        'wave',
        ['return', 2600],
        'cursor:hide',
        ['external:clear', true],
        'spotlight:persistent',
        'spotlight:action',
        'spotlight:scene-extra',
        'spotlight:retained',
        'spotlight:virtual',
        'spotlight:geometry',
        'spotlight:variant',
        'interrupts:disable'
    ]);
    assert.doesNotMatch(directorSource, /playAvatarFloatingPetalTransitionAtCueLegacy/);
    assert.match(petalTransitionControllerSource, /const petalSequencePromise = this\.preloadReturnPetalSequence\(\);/);
    assert.match(petalTransitionControllerSource, /await this\.playReturn\(\{/);
});

test('PetalTransitionController aligns every cue-triggered petal transition from animation duration', async () => {
    const { PetalTransitionController } = require('./tutorial/visual/controllers.js');
    const calls = [];
    const director = {
        sceneRunId: 7,
        destroyed: false,
        returnPetalTransitionActive: false,
        getAvatarFloatingNarrationDurationMs() {
            return 10000;
        },
        waitForSceneDelay(durationMs) {
            calls.push(['wait', durationMs]);
            return Promise.resolve(true);
        },
        cursor: {
            hide() {
                calls.push('cursor:hide');
            }
        },
        clearExternalizedChatGuideTarget() {},
        overlay: {
            clearPersistentSpotlight() {},
            clearActionSpotlight() {}
        },
        clearSceneExtraSpotlights() {},
        clearRetainedExtraSpotlights() {},
        clearAllVirtualSpotlights() {},
        clearSpotlightGeometryHints() {},
        clearSpotlightVariantHints() {},
        disableInterrupts() {},
        runReturnControlCueWavePerformance() {
            calls.push('wave');
            return Promise.resolve();
        },
        shouldReduceTutorialMotion() {
            return false;
        }
    };
    const controller = new PetalTransitionController(director);
    controller.preloadReturnPetalSequence = () => Promise.resolve(null);
    controller.executeReturnTransition = (options) => {
        calls.push(['return', options.durationMs]);
        return Promise.resolve();
    };

    await controller.playAtCue(
        { id: 'generic_wrap' },
        7,
        'voice',
        'text',
        Date.now() - 1000,
        3000
    );

    assert.equal(calls[1], 'wave');
    assert.deepEqual(calls[2], ['return', 3000]);
    assert.ok(
        calls[0][1] >= 5950 && calls[0][1] <= 6050,
        'petal cue should wait until narration duration minus the shared transition window'
    );
});

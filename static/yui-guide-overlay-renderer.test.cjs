const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/overlay.js'), 'utf8');
const rendererPath = path.join(__dirname, 'tutorial/visual/overlay-renderer.js');
const rendererSource = fs.existsSync(rendererPath) ? fs.readFileSync(rendererPath, 'utf8') : '';
const overlaySource = source.split('    class YuiGuideOverlay {')[1];

test('overlay exposes TutorialOverlayRenderer facade around the PC overlay bridge', () => {
    const constructorBlock = overlaySource.split('        constructor(doc) {')[1].split(
        '        isPcOverlayActive() {',
        1
    )[0];
    const isPcOverlayActiveBlock = overlaySource.split('        isPcOverlayActive() {')[1].split(
        '        shouldSuppressDomForPcOverlay() {',
        1
    )[0];
    const suppressBlock = overlaySource.split('        shouldSuppressDomForPcOverlay() {')[1].split(
        '        ensureRoot() {',
        1
    )[0];

    assert.match(rendererSource, /class TutorialOverlayRenderer/);
    assert.match(rendererSource, /root\.TutorialOverlayRenderer = TutorialOverlayRenderer/);
    assert.doesNotMatch(source, /class TutorialOverlayRenderer/);
    assert.doesNotMatch(source, /class FallbackTutorialOverlayRenderer/);
    assert.match(source, /const OverlayRendererClass = window\.TutorialOverlayRenderer;/);
    assert.match(constructorBlock, /this\.overlayRenderer = new OverlayRendererClass\(this\.pcOverlayBridge\);/);
    assert.match(isPcOverlayActiveBlock, /this\.overlayRenderer\.isAvailable\(\)/);
    assert.match(suppressBlock, /this\.overlayRenderer\.shouldSuppressDom\(\)/);
});

test('PC overlay bridge composes complete state through renderer store', () => {
    const bridgeBlock = source.split('    function createPcOverlayBridge(doc) {')[1].split(
        '    const OverlayRendererClass =',
        1
    )[0];

    assert.match(rendererSource, /function createPcOverlayCompleteStateStore/);
    assert.match(rendererSource, /TutorialOverlayRenderer\.createPcOverlayCompleteStateStore = createPcOverlayCompleteStateStore/);
    assert.match(source, /const OverlayRendererApi = window\.TutorialOverlayRendererApi \|\| \{\};/);
    assert.match(bridgeBlock, /createPcOverlayCompleteStateStore/);
    assert.match(bridgeBlock, /completeStateStore\.applyPatch\(patch \|\| \{\}\)/);
    assert.match(bridgeBlock, /completeStateStore\.reset\(\)/);
    assert.match(bridgeBlock, /completeStateStore\.clearCursorCache\(\)/);
    assert.doesNotMatch(bridgeBlock, /let currentSpotlights = \[\]/);
    assert.doesNotMatch(bridgeBlock, /let currentCursor = null/);
    assert.doesNotMatch(bridgeBlock, /currentCursor = null/);
});

test('PC overlay bridge marks itself inactive when clearing the remote tutorial overlay', () => {
    const bridgeBlock = source.split('    function createPcOverlayBridge(doc) {')[1].split(
        '    const OverlayRendererClass =',
        1
    )[0];
    const sendBlock = bridgeBlock.split('        const send = (patch, force) => {')[1].split(
        '        };',
        1
    )[0];
    const clearBlockMatch = bridgeBlock.match(/            clear\(\) \{([\s\S]*?)\n            \}\n        \};/);
    assert.ok(clearBlockMatch, 'PC overlay bridge clear block should be present');
    const clearBlock = clearBlockMatch[1];

    assert.match(sendBlock, /if \(cleared\) \{\s*return;\s*\}/);
    assert.match(clearBlock, /active = false;/);
    assert.match(clearBlock, /cleared = true;/);
    assert.match(clearBlock, /remoteReady = false;/);
    assert.match(clearBlock, /completeStateStore\.reset\(\)/);
    assert.match(clearBlock, /host\.clear\(\{ tutorialRunId: runId \}\)/);
});

test('full tutorial pages load TutorialOverlayRenderer before the overlay', () => {
    for (const templatePath of [
        'templates/index.html'
    ]) {
        const templateSource = fs.readFileSync(path.join(__dirname, '..', templatePath), 'utf8');
        const rendererIndex = templateSource.indexOf('/static/tutorial/visual/overlay-renderer.js');
        const overlayIndex = templateSource.indexOf('/static/tutorial/yui-guide/overlay.js');

        assert.notEqual(rendererIndex, -1, templatePath + ' should load tutorial/visual/overlay-renderer.js');
        assert.notEqual(overlayIndex, -1, templatePath + ' should load tutorial/yui-guide/overlay.js');
        assert.ok(rendererIndex < overlayIndex, templatePath + ' should load renderer before overlay');
    }
});

test('frontend harness loads TutorialOverlayRenderer before overlay consumers', () => {
    const harnessSource = fs.readFileSync(
        path.join(__dirname, '..', 'tests', 'frontend', 'test_home_prompt_flow.py'),
        'utf8'
    );
    const overlayDependencyBlock = harnessSource.split('_YUI_OVERLAY_DEPENDENCIES = (')[1].split(')', 1)[0];
    const directorDependencyBlock = harnessSource.split('_YUI_DIRECTOR_DEPENDENCIES = (')[1].split(')', 1)[0];

    assert.match(overlayDependencyBlock, /"tutorial\/visual\/overlay-renderer\.js",/);
    assert.match(directorDependencyBlock, /"tutorial\/visual\/overlay-renderer\.js",\s*"tutorial\/yui-guide\/overlay\.js",/);
    assert.match(
        harnessSource,
        /if script_name == "tutorial\/yui-guide\/overlay\.js":[\s\S]*for dependency in _YUI_OVERLAY_DEPENDENCIES:/
    );
});

test('TutorialOverlayRenderer delegates full-state calls to the bridge', () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { TutorialOverlayRenderer } = require('./tutorial/visual/overlay-renderer.js');
    const calls = [];
    const bridge = {
        isAvailable: () => true,
        shouldSuppressDom: () => true,
        canRenderPetalTransition: () => true,
        setSpotlights: (rects) => calls.push(['spotlights', rects.length]),
        showCursorAt: (x, y) => calls.push(['showCursor', x, y]),
        moveCursorTo: (x, y, durationMs, effect) => calls.push(['moveCursor', x, y, durationMs, effect]),
        hideCursor: () => calls.push(['hideCursor']),
        clearCursorCache: () => calls.push(['clearCursorCache']),
        playPetalTransition: (origin, options) => calls.push(['petal', origin.x, options.durationMs]),
        showAvatarStandIn: (standIn) => calls.push(['standIn', standIn.resource, standIn.url || '']),
        clearAvatarStandIn: () => calls.push(['clearStandIn']),
        clear: () => calls.push(['clear'])
    };
    assert.equal(typeof TutorialOverlayRenderer, 'function');
    const renderer = new TutorialOverlayRenderer(bridge);

    assert.equal(renderer.isAvailable(), true);
    assert.equal(renderer.shouldSuppressDom(), true);
    assert.equal(renderer.canRenderPetalTransition(), true);
    renderer.setSpotlights([{ rect: {} }]);
    renderer.showCursorAt(1, 2);
    renderer.moveCursorTo(3, 4, 500, 'click', 300);
    renderer.hideCursor();
    renderer.clearCursorCache();
    assert.equal(renderer.playPetalTransition({ x: 5, y: 6 }, { durationMs: 700 }), true);
    renderer.showAvatarStandIn({ resource: 'day5' });
    renderer.clearAvatarStandIn();
    renderer.clear();

    assert.deepEqual(calls, [
        ['spotlights', 1],
        ['showCursor', 1, 2],
        ['moveCursor', 3, 4, 500, 'click'],
        ['hideCursor'],
        ['clearCursorCache'],
        ['petal', 5, 700],
        ['standIn', 'day5', ''],
        ['clearStandIn'],
        ['clear']
    ]);
});

test('OverlaySpotlightDomRenderer owns DOM spotlight frame and cutout primitives', () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { OverlaySpotlightDomRenderer } = require('./tutorial/visual/overlay-renderer.js');
    assert.equal(typeof OverlaySpotlightDomRenderer, 'function');

    const classListEvents = [];
    function createClassList() {
        const values = new Set();
        return {
            add(name) {
                values.add(name);
                classListEvents.push(['add', name]);
            },
            remove(name) {
                values.delete(name);
                classListEvents.push(['remove', name]);
            },
            toggle(name, active) {
                if (active) {
                    values.add(name);
                } else {
                    values.delete(name);
                }
                classListEvents.push(['toggle', name, !!active]);
            },
            contains(name) {
                return values.has(name);
            }
        };
    }
    const created = [];
    const fakeDocument = {
        createElement(tagName) {
            const element = {
                tagName,
                className: '',
                style: {
                    removeProperty(name) {
                        delete this[name];
                    }
                },
                children: [],
                classList: createClassList(),
                appendChild(child) {
                    child.parentNode = this;
                    this.children.push(child);
                    return child;
                },
                querySelector(selector) {
                    const className = selector.replace('.', '');
                    return this.children.find((child) => String(child.className || '').split(/\s+/).includes(className)) || null;
                },
                querySelectorAll(selector) {
                    const classes = selector.split(',').map((value) => value.trim().replace('.', ''));
                    return this.children.filter((child) => {
                        const names = String(child.className || '').split(/\s+/);
                        return classes.some((className) => names.includes(className));
                    });
                }
            };
            created.push(element);
            return element;
        }
    };
    const renderer = new OverlaySpotlightDomRenderer({
        document: fakeDocument,
        shouldSuppressDom: () => false
    });
    const cutoutAttrs = {};
    const cutout = {
        hidden: false,
        style: {
            removeProperty(name) {
                delete this[name];
            }
        },
        setAttribute(name, value) {
            cutoutAttrs[name] = value;
        }
    };
    const frame = fakeDocument.createElement('div');
    frame.hidden = true;

    renderer.ensureSpotlightFrameDecorations(frame);
    assert.ok(frame.querySelector('.yui-guide-spotlight-chrome'));
    assert.ok(frame.querySelector('.yui-guide-spotlight-sweep'));
    assert.ok(frame.querySelector('.yui-guide-spotlight-circle-skin'));

    renderer.updateBackdropCutout(cutout, {
        left: 10,
        top: 20,
        width: 60,
        height: 40,
        radius: 12,
        padding: 6
    });
    assert.equal(cutout.hidden, false);
    assert.equal(cutoutAttrs.x, '14');
    assert.equal(cutoutAttrs.y, '24');
    assert.equal(cutoutAttrs.width, '52');
    assert.equal(cutoutAttrs.height, '32');
    assert.equal(cutoutAttrs.rx, '8');

    renderer.updateSpotlightFrame(frame, {
        left: 10,
        top: 20,
        width: 60,
        height: 40,
        radius: 12,
        isCircular: true
    }, { variant: 'circle-image' });
    assert.equal(frame.hidden, false);
    assert.equal(frame.style.left, '10px');
    assert.equal(frame.style.top, '20px');
    assert.equal(frame.style.width, '60px');
    assert.equal(frame.style.height, '40px');
    assert.equal(frame.style.borderRadius, '12px');
    assert.equal(frame.classList.contains('is-circle-image'), true);

    renderer.updateBackdropCutout(cutout, null);
    assert.equal(cutout.hidden, true);
    assert.equal(cutoutAttrs.width, '0');
    renderer.updateSpotlightFrame(frame, null);
    assert.equal(frame.hidden, true);
    assert.equal(frame.classList.contains('is-visible'), false);
});

test('OverlaySpotlightDomRenderer owns spotlight rect radius and variant parsing', () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { OverlaySpotlightDomRenderer } = require('./tutorial/visual/overlay-renderer.js');

    const element = {
        attrs: {
            'data-yui-guide-spotlight-padding': '8',
            'data-yui-guide-spotlight-geometry': 'circle'
        },
        getAttribute(name) {
            return this.attrs[name] || '';
        },
        getBoundingClientRect() {
            return {
                left: 4.4,
                top: 10.2,
                right: 54.6,
                bottom: 40.7,
                width: 50.2,
                height: 30.5
            };
        }
    };
    const radiusElement = {
        attrs: {
            'data-yui-guide-spotlight-padding': '3',
            'data-yui-guide-spotlight-radius': '13',
            'data-yui-guide-spotlight-variant': 'thin'
        },
        getAttribute(name) {
            return this.attrs[name] || '';
        },
        getBoundingClientRect() {
            return {
                left: 30,
                top: 20,
                right: 70,
                bottom: 60,
                width: 40,
                height: 40
            };
        }
    };
    const circularButton = {
        attrs: {},
        getAttribute(name) {
            return this.attrs[name] || '';
        },
        getBoundingClientRect() {
            return {
                left: 1,
                top: 2,
                right: 21,
                bottom: 22,
                width: 20,
                height: 20
            };
        }
    };
    const renderer = new OverlaySpotlightDomRenderer({
        defaultSpotlightPadding: 6,
        getWindow: () => ({
            innerWidth: 80,
            innerHeight: 64,
            getComputedStyle(target) {
                return target === element ? { borderTopLeftRadius: '10px' } : {};
            }
        }),
        isCircularElement: (target) => target === circularButton
    });

    assert.equal(renderer.getSpotlightRadius(element, 8), 18);
    assert.deepEqual(renderer.getSpotlightRect(element), {
        left: 0,
        top: 2,
        right: 63,
        bottom: 49,
        width: 63,
        height: 47,
        radius: 18,
        padding: 8,
        isCircular: true
    });
    assert.deepEqual(renderer.getSpotlightRect(radiusElement), {
        left: 27,
        top: 17,
        right: 73,
        bottom: 63,
        width: 46,
        height: 46,
        radius: 13,
        padding: 3,
        isCircular: false
    });
    assert.equal(renderer.getFrameVariantFromElement(radiusElement), 'thin');
    assert.equal(renderer.getFrameVariantFromElement(element), 'circle-image');
    assert.equal(renderer.getFrameVariantFromElement(circularButton), 'circle-image');
    assert.equal(renderer.getSpotlightRect({ getBoundingClientRect: () => ({ width: 0, height: 10 }) }), null);
});

test('OverlaySpotlightDomRenderer composes spotlight targets and PC payload entries', () => {
    const { OverlaySpotlightDomRenderer } = require('./tutorial/visual/overlay-renderer.js');
    const makeElement = (name, rect, attrs) => ({
        name,
        attrs: attrs || {},
        getAttribute(attributeName) {
            return this.attrs[attributeName] || '';
        },
        getBoundingClientRect() {
            return rect;
        }
    });
    const persistent = makeElement('persistent', {
        left: 10,
        top: 10,
        right: 30,
        bottom: 30,
        width: 20,
        height: 20
    }, { 'data-yui-guide-spotlight-variant': 'thin' });
    const action = makeElement('action', {
        left: 40,
        top: 10,
        right: 70,
        bottom: 40,
        width: 30,
        height: 30
    });
    const secondaryAction = makeElement('secondary', {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
        width: 0,
        height: 0
    });
    const extra = makeElement('extra', {
        left: 5,
        top: 45,
        right: 25,
        bottom: 65,
        width: 20,
        height: 20
    }, { 'data-yui-guide-spotlight-geometry': 'circle' });
    const renderer = new OverlaySpotlightDomRenderer({
        defaultSpotlightPadding: 0,
        getWindow: () => ({
            innerWidth: 100,
            innerHeight: 100,
            getComputedStyle: () => ({})
        })
    });

    const targets = renderer.resolveSpotlightTargets({
        persistent,
        action,
        secondaryAction,
        extra: [extra, null]
    });

    assert.equal(targets.persistent.element, persistent);
    assert.equal(targets.persistent.variant, 'thin');
    assert.equal(targets.action.element, action);
    assert.equal(targets.secondaryAction.rect, null);
    assert.equal(targets.extra.length, 2);
    assert.equal(targets.extra[0].variant, 'circle-image');
    assert.equal(targets.extra[1].rect, null);
    assert.deepEqual(renderer.buildPcSpotlights(targets).map((entry) => ({
        kind: entry.kind,
        variant: entry.variant,
        left: entry.rect.left
    })), [
        { kind: 'persistent', variant: 'thin', left: 10 },
        { kind: 'primary', variant: '', left: 40 },
        { kind: 'extra', variant: 'circle-image', left: 5 }
    ]);
    assert.equal(renderer.hasAnySpotlightRect(targets), true);
    assert.equal(renderer.hasAnySpotlightRect({
        persistent: { rect: null },
        action: { rect: null },
        secondaryAction: { rect: null },
        extra: [{ rect: null }]
    }), false);
});

test('OverlaySpotlightDomRenderer renders resolved DOM spotlight targets', () => {
    const { OverlaySpotlightDomRenderer } = require('./tutorial/visual/overlay-renderer.js');
    const renderer = new OverlaySpotlightDomRenderer();
    const calls = [];
    renderer.updateBackdropCutout = (cutout, rect) => {
        calls.push(['cutout', cutout, rect && rect.id || null]);
    };
    renderer.updateSpotlightFrame = (frame, rect, options) => {
        calls.push(['frame', frame, rect && rect.id || null, options && options.variant || '']);
    };
    const persistentRect = { id: 'persistent' };
    const actionRect = { id: 'action' };
    const extraRect = { id: 'extra' };
    const extraEntries = [
        { frame: 'extra-frame-0', cutout: 'extra-cutout-0' },
        { frame: 'stale-frame-1', cutout: 'stale-cutout-1' }
    ];

    renderer.renderDomSpotlights({
        targets: {
            persistent: { rect: persistentRect, variant: 'thin' },
            action: { rect: actionRect, variant: '' },
            secondaryAction: { rect: null, variant: 'plain-circle' },
            extra: [
                { rect: extraRect, variant: 'circle-image' }
            ]
        },
        frames: {
            persistent: 'persistent-frame',
            action: 'action-frame',
            secondaryAction: 'secondary-frame'
        },
        cutouts: {
            persistent: 'persistent-cutout',
            action: 'action-cutout',
            secondaryAction: 'secondary-cutout'
        },
        extraEntries,
        ensureExtraSpotlightEntry(index) {
            return extraEntries[index] || null;
        }
    });

    assert.deepEqual(calls, [
        ['frame', 'persistent-frame', 'persistent', 'thin'],
        ['frame', 'action-frame', 'action', ''],
        ['frame', 'secondary-frame', null, 'plain-circle'],
        ['cutout', 'persistent-cutout', 'persistent'],
        ['cutout', 'action-cutout', 'action'],
        ['cutout', 'secondary-cutout', null],
        ['cutout', 'extra-cutout-0', 'extra'],
        ['frame', 'extra-frame-0', 'extra', 'circle-image'],
        ['cutout', 'stale-cutout-1', null],
        ['frame', 'stale-frame-1', null, '']
    ]);
});

test('OverlayCursorStateStore owns cursor position and suppressed motion state', async () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { OverlayCursorStateStore } = require('./tutorial/visual/overlay-renderer.js');
    assert.equal(typeof OverlayCursorStateStore, 'function');

    let now = 1000;
    const timers = [];
    const store = new OverlayCursorStateStore({
        now: () => now,
        setTimeout(callback, delayMs) {
            timers.push({ callback, delayMs });
            return timers.length;
        },
        clearTimeout(timerId) {
            timers.push({ clear: timerId });
        }
    });

    assert.equal(store.hasPosition(), false);
    assert.deepEqual(store.syncPosition(10, 20, true), true);
    assert.deepEqual(store.getPosition(), { x: 10, y: 20 });
    assert.equal(store.isVisible(), true);
    assert.equal(store.getSmoothShowDurationMs(13, 24, 560), 560);
    assert.equal(store.getSmoothShowDurationMs(10.5, 20.5, 560), 0);

    const movePromise = store.animateTo(110, 120, 1000, {});
    now = 1500;
    const mid = store.updateMotion(now);
    assert.ok(mid.x > 10 && mid.x < 110);
    assert.ok(mid.y > 20 && mid.y < 120);
    assert.equal(timers.some((entry) => entry.delayMs === 48), true);

    now = 2000;
    store.updateMotion(now);
    assert.equal(await movePromise, true);
    assert.deepEqual(store.getPosition(), { x: 110, y: 120 });
    assert.equal(store.isVisible(), true);

    store.syncPosition(1, 2, true);
    const cancelledPromise = store.animateTo(5, 6, 500, {
        cancelCheck: () => true
    });
    store.tickMotion();
    assert.equal(await cancelledPromise, false);
    assert.deepEqual(store.getPosition(), { x: 1, y: 2 });

    store.clear();
    assert.equal(store.hasPosition(), false);
    assert.equal(store.isVisible(), false);
});

test('OverlaySpotlightStateStore owns spotlight target state', () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { OverlaySpotlightStateStore } = require('./tutorial/visual/overlay-renderer.js');
    assert.equal(typeof OverlaySpotlightStateStore, 'function');

    const classEvents = [];
    function createTarget(name) {
        return {
            name,
            getBoundingClientRect() {
                return { left: 0, top: 0, right: 10, bottom: 10, width: 10, height: 10 };
            },
            classList: {
                add(className) {
                    classEvents.push(['add', name, className]);
                },
                remove(className) {
                    classEvents.push(['remove', name, className]);
                }
            }
        };
    }

    const persistent = createTarget('persistent');
    const action = createTarget('action');
    const extra = createTarget('extra');
    const invalid = { name: 'invalid' };
    const store = new OverlaySpotlightStateStore();

    assert.equal(store.isSuppressed(), false);
    store.setPersistent(persistent);
    store.setAction(action);
    assert.equal(store.hasAny(), true);
    assert.deepEqual(store.getTargets(), {
        persistent,
        action,
        secondaryAction: null,
        extra: []
    });
    assert.deepEqual(classEvents, [
        ['add', 'persistent', 'yui-guide-chat-target']
    ]);

    store.setExtra([extra, invalid, null]);
    assert.deepEqual(store.getExtraElements(), [extra]);
    assert.equal(store.hasAny(), true);

    store.clearPersistent();
    assert.equal(store.getTargets().persistent, null);
    assert.deepEqual(classEvents.slice(-1), [
        ['remove', 'persistent', 'yui-guide-chat-target']
    ]);

    store.setSuppressed(true);
    assert.equal(store.isSuppressed(), true);
    assert.equal(store.hasAny(), true);
    assert.equal(store.getTargets().action, action);
    assert.deepEqual(store.getExtraElements(), [extra]);
});

test('PC overlay complete state store composes full visual payloads', () => {
    assert.ok(fs.existsSync(rendererPath), 'tutorial/visual/overlay-renderer.js should exist');
    const { createPcOverlayCompleteStateStore } = require('./tutorial/visual/overlay-renderer.js');
    const storage = new Map();
    let now = 1000;
    const store = createPcOverlayCompleteStateStore({
        now: () => now,
        storage: {
            setItem(key, value) {
                storage.set(key, value);
            },
            removeItem(key) {
                storage.delete(key);
            }
        }
    });

    const clickPayload = store.applyPatch({
        spotlights: [{ id: 'spotlight-a' }],
        cursor: {
            visible: true,
            x: 12,
            y: 24,
            effect: 'click',
            effectDurationMs: 120
        },
        avatarStandIn: {
            visible: true,
            resource: 'day5'
        }
    });
    assert.deepEqual(clickPayload.spotlights, [{ id: 'spotlight-a' }]);
    assert.equal(clickPayload.cursor.effect, 'click');
    assert.equal(clickPayload.avatarStandIn.resource, 'day5');
    assert.equal(JSON.parse(storage.get('yuiGuidePcOverlayAvatarStandIn')).resource, 'day5');

    now = 1050;
    const suppressedPayload = store.applyPatch({
        spotlights: [{ id: 'spotlight-b' }],
        petal: { id: 'petal-a' }
    });
    assert.deepEqual(suppressedPayload.spotlights, [{ id: 'spotlight-b' }]);
    assert.equal(Object.prototype.hasOwnProperty.call(suppressedPayload, 'cursor'), false);
    assert.equal(suppressedPayload.petal.id, 'petal-a');
    assert.equal(suppressedPayload.avatarStandIn.resource, 'day5');

    now = 1200;
    const resumedPayload = store.applyPatch({
        spotlights: [{ id: 'spotlight-c' }]
    });
    assert.deepEqual(resumedPayload.cursor, {
        visible: true,
        x: 12,
        y: 24
    });
    assert.equal(store.getPetal().id, 'petal-a');

    const clearedPayload = store.applyPatch({
        petal: null,
        avatarStandIn: null
    });
    assert.equal(clearedPayload.petal, null);
    assert.equal(clearedPayload.avatarStandIn, null);
    assert.equal(storage.has('yuiGuidePcOverlayAvatarStandIn'), false);

    store.reset();
    assert.deepEqual(store.applyPatch({}), {});
});

test('PC overlay complete state store owns cursor cache clearing', () => {
    const { createPcOverlayCompleteStateStore } = require('./tutorial/visual/overlay-renderer.js');
    let now = 1000;
    const store = createPcOverlayCompleteStateStore({
        now: () => now,
        defaultCursorClickVisibleMs: 420
    });

    const clickPayload = store.applyPatch({
        cursor: {
            visible: true,
            x: 10,
            y: 20,
            effect: 'click'
        }
    });
    assert.equal(clickPayload.cursor.effect, 'click');
    now += 100;
    assert.deepEqual(store.applyPatch({
        spotlights: [{ id: 'spotlight-a' }]
    }), {
        spotlights: [{ id: 'spotlight-a' }]
    });

    store.clearCursorCache();
    assert.deepEqual(store.applyPatch({
        spotlights: [{ id: 'spotlight-b' }]
    }), {
        spotlights: [{ id: 'spotlight-b' }]
    });
});

test('PC overlay complete state store omits empty spotlights for cursor-only patches', () => {
    const { createPcOverlayCompleteStateStore } = require('./tutorial/visual/overlay-renderer.js');
    const store = createPcOverlayCompleteStateStore();

    const cursorOnlyPayload = store.applyPatch({
        cursor: {
            visible: true,
            x: 10,
            y: 20
        }
    });
    assert.equal(Object.prototype.hasOwnProperty.call(cursorOnlyPayload, 'spotlights'), false);
    assert.deepEqual(cursorOnlyPayload.cursor, {
        visible: true,
        x: 10,
        y: 20
    });

    const explicitClearPayload = store.applyPatch({
        spotlights: []
    });
    assert.deepEqual(explicitClearPayload, {
        spotlights: [],
        cursor: {
            visible: true,
            x: 10,
            y: 20
        }
    });
});

test('overlay visual outputs go through TutorialOverlayRenderer before DOM fallback', () => {
    const constructorBlock = overlaySource.split('        constructor(doc) {')[1].split(
        '        isPcOverlayActive() {',
        1
    )[0];
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        updateSpotlightFrame',
        1
    )[0];
    const clearSpotlightBlock = overlaySource.split('        clearSpotlight() {')[1].split(
        '        hasCursorPosition() {',
        1
    )[0];
    const showStandInBlock = overlaySource.split('        showAvatarStandIn(standIn) {')[1].split(
        '        clearAvatarStandIn() {',
        1
    )[0];
    const clearStandInBlock = overlaySource.split('        clearAvatarStandIn() {')[1].split(
        '        setSpotlightSuppressed',
        1
    )[0];
    const hideCursorBlock = overlaySource.split('        hideCursor() {')[1].split(
        '        playPetalTransition',
        1
    )[0];
    const petalBlock = overlaySource.split('        playPetalTransition(origin, options) {')[1].split(
        '        destroy() {',
        1
    )[0];

    assert.match(source, /const OverlayCursorStateStore = OverlayRendererApi\.OverlayCursorStateStore/);
    assert.match(source, /const OverlaySpotlightStateStore = OverlayRendererApi\.OverlaySpotlightStateStore/);
    assert.match(rendererSource, /class OverlayCursorStateStore/);
    assert.match(rendererSource, /class OverlaySpotlightStateStore/);
    assert.match(constructorBlock, /this\.cursorState = new OverlayCursorStateStore\(\{/);
    assert.match(constructorBlock, /this\.installCursorStateAccessors\(\);/);
    assert.match(constructorBlock, /this\.spotlightState = new OverlaySpotlightStateStore\(\);/);
    assert.match(constructorBlock, /this\.spotlightDomRenderer = new OverlaySpotlightDomRenderer\(\{/);
    assert.match(constructorBlock, /this\.installSpotlightStateAccessors\(\);/);
    assert.match(constructorBlock, /this\.pcCursorOutputSuppressed = false;/);
    assert.match(constructorBlock, /this\.installPcOverlayBridgeAccessor\(\);/);
    assert.match(source, /installPcOverlayBridgeAccessor\(\) \{/);
    assert.match(source, /installSpotlightStateAccessors\(\) \{/);
    assert.match(source, /setPcCursorOutputSuppressed\(suppressed\) \{/);
    assert.match(source, /shouldForwardCursorToPcOverlay\(\) \{/);
    assert.match(source, /return this\.isPcOverlayActive\(\) && this\.pcCursorOutputSuppressed !== true;/);
    assert.match(source, /this\.overlayRenderer\.pcOverlayBridge = this\._pcOverlayBridge;/);
    assert.match(
        refreshBlock,
        /this\.overlayRenderer\.setSpotlights\(\s*this\.spotlightDomRenderer\.buildPcSpotlights\(spotlightTargets\)\s*\)/
    );
    const pcSpotlightReturnIndex = refreshBlock.indexOf('                return;');
    const domSpotlightRenderIndex = refreshBlock.indexOf('this.spotlightDomRenderer.renderDomSpotlights');
    assert.ok(pcSpotlightReturnIndex >= 0);
    assert.ok(domSpotlightRenderIndex > pcSpotlightReturnIndex);
    assert.match(clearSpotlightBlock, /this\.overlayRenderer\.setSpotlights\(\[\]\)/);
    assert.match(showStandInBlock, /this\.overlayRenderer\.showAvatarStandIn\(payload\)/);
    assert.match(clearStandInBlock, /this\.overlayRenderer\.clearAvatarStandIn\(\)/);
    assert.doesNotMatch(showStandInBlock, /this\.avatarStandIn/);
    assert.doesNotMatch(clearStandInBlock, /this\.avatarStandIn/);
    assert.match(hideCursorBlock, /this\.overlayRenderer\.hideCursor\(\)/);
    assert.match(petalBlock, /this\.overlayRenderer\.playPetalTransition\(origin,\s*options \|\| \{\}\)/);
    assert.doesNotMatch(refreshBlock, /this\.pcOverlayBridge\.setSpotlights\(pcRects\)/);
});

test('overlay delegates DOM spotlight frame and cutout primitives to OverlaySpotlightDomRenderer', () => {
    const constructorBlock = overlaySource.split('        constructor(doc) {')[1].split(
        '        isPcOverlayActive() {',
        1
    )[0];
    const overlayClassBlock = overlaySource;
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        scheduleSpotlightRefresh() {',
        1
    )[0];
    const ensureRootBlock = overlaySource.split('        ensureRoot() {')[1].split(
        '        ensureExtraSpotlightEntry',
        1
    )[0];

    assert.match(rendererSource, /class OverlaySpotlightDomRenderer/);
    assert.match(rendererSource, /TutorialOverlayRenderer\.OverlaySpotlightDomRenderer = OverlaySpotlightDomRenderer/);
    assert.match(source, /const OverlaySpotlightDomRenderer = OverlayRendererApi\.OverlaySpotlightDomRenderer/);
    assert.match(constructorBlock, /this\.spotlightDomRenderer = new OverlaySpotlightDomRenderer\(\{/);
    assert.match(ensureRootBlock, /this\.spotlightDomRenderer\.ensureSpotlightFrameDecorations/);
    assert.match(refreshBlock, /this\.spotlightDomRenderer\.renderDomSpotlights/);
    assert.doesNotMatch(overlayClassBlock, /\n        updateBackdropCutout\(cutout, spotlightRect\) \{/);
    assert.doesNotMatch(overlayClassBlock, /\n        updateSpotlightFrame\(frame, spotlightRect, options\) \{/);
});

test('overlay delegates spotlight rect radius and variant parsing to OverlaySpotlightDomRenderer', () => {
    const constructorBlock = overlaySource.split('        constructor(doc) {')[1].split(
        '        isPcOverlayActive() {',
        1
    )[0];
    const overlayClassBlock = overlaySource;
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        scheduleSpotlightRefresh() {',
        1
    )[0];

    assert.match(rendererSource, /getSpotlightRect\(element\) \{/);
    assert.match(rendererSource, /getSpotlightRadius\(element, padding\) \{/);
    assert.match(rendererSource, /getFrameVariantFromElement\(element\) \{/);
    assert.match(constructorBlock, /defaultSpotlightPadding: DEFAULT_SPOTLIGHT_PADDING/);
    assert.match(constructorBlock, /getWindow: \(\) => window/);
    assert.match(constructorBlock, /isCircularElement: isCircularFloatingButtonElement/);
    assert.match(refreshBlock, /this\.spotlightDomRenderer\.resolveSpotlightTargets\(\{/);
    assert.match(refreshBlock, /persistent: this\.persistentHighlightedElement/);
    assert.match(refreshBlock, /targets: spotlightTargets/);
    assert.doesNotMatch(refreshBlock, /const getFrameVariantFromElement =/);
    assert.doesNotMatch(overlayClassBlock, /\n        getSpotlightRect\(element\) \{/);
    assert.doesNotMatch(overlayClassBlock, /\n        getSpotlightRadius\(element, padding\) \{/);
    assert.doesNotMatch(source, /function readSpotlightNumberAttr/);
});

test('overlay delegates PC spotlight payload composition to OverlaySpotlightDomRenderer', () => {
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        scheduleSpotlightRefresh() {',
        1
    )[0];

    assert.match(rendererSource, /resolveSpotlightTargets\(targets\) \{/);
    assert.match(rendererSource, /buildPcSpotlights\(resolvedTargets\) \{/);
    assert.match(refreshBlock, /const spotlightTargets = this\.spotlightDomRenderer\.resolveSpotlightTargets\(\{/);
    assert.match(refreshBlock, /this\.overlayRenderer\.setSpotlights\(\s*this\.spotlightDomRenderer\.buildPcSpotlights\(spotlightTargets\)\s*\)/);
    assert.doesNotMatch(refreshBlock, /const pcRects = \[\]/);
    assert.doesNotMatch(refreshBlock, /pcRects\.push/);
});

test('overlay delegates DOM spotlight target rendering to OverlaySpotlightDomRenderer', () => {
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        scheduleSpotlightRefresh() {',
        1
    )[0];

    assert.match(rendererSource, /renderDomSpotlights\(options\) \{/);
    assert.match(refreshBlock, /this\.spotlightDomRenderer\.renderDomSpotlights\(\{/);
    assert.match(refreshBlock, /ensureExtraSpotlightEntry: \(index\) => this\.ensureExtraSpotlightEntry\(index\)/);
    assert.doesNotMatch(refreshBlock, /this\.spotlightDomRenderer\.updateSpotlightFrame/);
    assert.doesNotMatch(refreshBlock, /this\.spotlightDomRenderer\.updateBackdropCutout/);
    assert.doesNotMatch(refreshBlock, /extraRects\.forEach/);
});

test('overlay delegates backdrop spotlight visibility checks to OverlaySpotlightDomRenderer', () => {
    const refreshBlock = overlaySource.split('        refreshSpotlight() {')[1].split(
        '        scheduleSpotlightRefresh() {',
        1
    )[0];

    assert.match(rendererSource, /hasAnySpotlightRect\(resolvedTargets\) \{/);
    assert.match(refreshBlock, /BACKDROP_DIM_ENABLED\s+&& this\.spotlightDomRenderer\.hasAnySpotlightRect\(spotlightTargets\)/);
    assert.doesNotMatch(refreshBlock, /const persistentRect =/);
    assert.doesNotMatch(refreshBlock, /const extraRects =/);
});

test('overlay spotlight DOM fallback tracking is routed through small helpers', () => {
    const helperSplit = overlaySource.split('        clearIfSpotlightSuppressed() {');
    const helperBlock = helperSplit[1] && helperSplit[1].split(
        '        syncBackdropViewport() {',
        1
    )[0];
    const setExtraBlock = overlaySource.split('        setExtraSpotlights(elements) {')[1].split(
        '        clearExtraSpotlights() {',
        1
    )[0];
    const clearExtraBlock = overlaySource.split('        clearExtraSpotlights() {')[1].split(
        '        syncBackdropViewport() {',
        1
    )[0];
    const setPersistentBlock = overlaySource.split('        setPersistentSpotlight(element) {')[1].split(
        '        activateSpotlight(element) {',
        1
    )[0];
    const actionSpotlightBlock = overlaySource.split('        activateSpotlight(element) {')[1].split(
        '        clearSpotlight() {',
        1
    )[0];

    assert.ok(helperBlock, 'YuiGuideOverlay should define spotlight fallback helpers');
    assert.match(helperBlock, /this\.spotlightsSuppressed/);
    assert.doesNotMatch(helperBlock, /this\.clearSpotlight\(\)/);
    assert.match(helperBlock, /syncSpotlightTracking\(\) \{/);
    assert.match(helperBlock, /this\.spotlightState\.hasAny\(\)/);
    assert.match(setExtraBlock, /if \(this\.clearIfSpotlightSuppressed\(\)\) \{/);
    assert.match(setExtraBlock, /this\.syncSpotlightTracking\(\)/);
    assert.match(clearExtraBlock, /this\.syncSpotlightTracking\(\)/);
    assert.match(setPersistentBlock, /if \(this\.clearIfSpotlightSuppressed\(\)\) \{/);
    assert.match(setPersistentBlock, /this\.syncSpotlightTracking\(\)/);
    assert.match(actionSpotlightBlock, /this\.syncSpotlightTracking\(\)/);
    assert.doesNotMatch(setExtraBlock, /this\.persistentHighlightedElement[\s\S]*this\.startSpotlightTracking\(\)/);
    assert.doesNotMatch(clearExtraBlock, /this\.persistentHighlightedElement[\s\S]*this\.stopSpotlightTracking\(\)/);
});

test('overlay spotlight suppression preserves existing spotlight state', () => {
    const { OverlaySpotlightStateStore } = require('./tutorial/visual/overlay-renderer.js');
    const store = new OverlaySpotlightStateStore();
    const target = { getBoundingClientRect() {} };
    const extra = { getBoundingClientRect() {} };

    store.setPersistent(target);
    store.setAction(target);
    store.setExtra([extra]);
    store.setSuppressed(true);

    const targets = store.getTargets();
    assert.equal(store.isSuppressed(), true);
    assert.equal(targets.persistent, target);
    assert.equal(targets.action, target);
    assert.deepEqual(targets.extra, [extra]);
    assert.equal(store.hasAny(), true);
});

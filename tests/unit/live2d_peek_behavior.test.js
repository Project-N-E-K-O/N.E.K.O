const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const interactionPath = path.join(projectRoot, 'static', 'live2d', 'live2d-interaction.js');
const corePath = path.join(projectRoot, 'static', 'live2d', 'live2d-core.js');
const performanceStagePath = path.join(projectRoot, 'static', 'avatar', 'avatar-performance-stage.js');

function createHarness({
    widgetModeEnabled = true,
    stealthModeEnabled = true,
    innerWidth = 1000,
    innerHeight = 800,
    platform = '',
    currentDisplay = null,
    desktopRuntime = true,
    interactionActive = true,
    reducedMotion = false
} = {}) {
    const rafQueue = [];
    const bodyClasses = new Set();
    const listeners = new Map();
    let stealthEnabledState = stealthModeEnabled;

    function Live2DManager() {}

    const context = {
        Live2DManager,
        console,
        setTimeout,
        clearTimeout,
        performance: {
            now: () => 0
        },
        requestAnimationFrame: (callback) => {
            rafQueue.push(callback);
            return rafQueue.length;
        },
        CustomEvent: function CustomEvent(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        },
        window: {
            innerWidth,
            innerHeight,
            screen: { id: 'display-test', width: innerWidth, height: innerHeight },
            devicePixelRatio: 1.25,
            matchMedia: () => ({ matches: reducedMotion }),
            __NEKO_DESKTOP_RUNTIME__: desktopRuntime ? { platform } : null,
            electronScreen: currentDisplay ? {
                async getCurrentDisplay() {
                    return currentDisplay;
                }
            } : null,
            nekoWidgetMode: {
                isEnabled: () => widgetModeEnabled,
                isStealthEnabled: () => stealthEnabledState
            },
            NekoWidgetInteraction: {
                isActive: () => interactionActive
            },
            addEventListener(type, handler) {
                const handlers = listeners.get(type) || [];
                handlers.push(handler);
                listeners.set(type, handlers);
            },
            dispatchEvent(event) {
                this.lastEvent = event;
                const handlers = listeners.get(event && event.type) || [];
                for (const handler of handlers) {
                    handler(event);
                }
                return true;
            }
        },
        document: {
            body: {
                classList: {
                    add: (name) => bodyClasses.add(name),
                    remove: (name) => bodyClasses.delete(name),
                    contains: (name) => bodyClasses.has(name)
                }
            },
            getElementById: () => null
        }
    };
    context.globalThis = context;

    const source = fs.readFileSync(interactionPath, 'utf8');
    vm.runInNewContext(source, context, { filename: interactionPath });

    return {
        Live2DManager,
        rafQueue,
        bodyClasses,
        window: context.window,
        setStealthModeEnabled(enabled) {
            stealthEnabledState = enabled === true;
        }
    };
}

function createModel({ x = 0, y = 120, width = 500, height = 600 } = {}) {
    return {
        x,
        y,
        rotation: 0,
        destroyed: false,
        interactive: true,
        scale: { x: 1, y: 1 },
        getBounds() {
            return {
                left: this.x,
                top: this.y,
                right: this.x + width,
                bottom: this.y + height,
                width,
                height
            };
        }
    };
}

function createRotatingModel({ x, y, scaleX = 1, width = 300, height = 600 }) {
    const model = {
        x,
        y,
        rotation: 0,
        destroyed: false,
        interactive: true,
        scale: { x: scaleX, y: 1 },
        transformPoint(localX, localY) {
            const scaledX = localX * this.scale.x;
            return {
                x: this.x + scaledX * Math.cos(this.rotation) - localY * Math.sin(this.rotation),
                y: this.y + scaledX * Math.sin(this.rotation) + localY * Math.cos(this.rotation)
            };
        },
        toGlobal(point) {
            return this.transformPoint(Number(point.x), Number(point.y));
        },
        toLocal(point) {
            const dx = Number(point.x) - this.x;
            const dy = Number(point.y) - this.y;
            const cos = Math.cos(this.rotation);
            const sin = Math.sin(this.rotation);
            return {
                x: (dx * cos + dy * sin) / this.scale.x,
                y: -dx * sin + dy * cos
            };
        },
        getBounds() {
            const points = [
                this.transformPoint(0, 0),
                this.transformPoint(width, 0),
                this.transformPoint(0, height),
                this.transformPoint(width, height)
            ];
            const xs = points.map((point) => point.x);
            const ys = points.map((point) => point.y);
            const left = Math.min(...xs);
            const right = Math.max(...xs);
            const top = Math.min(...ys);
            const bottom = Math.max(...ys);
            return { left, right, top, bottom, width: right - left, height: bottom - top };
        }
    };
    return model;
}

function flushNextFrame(harness, time = 400) {
    const callback = harness.rafQueue.shift();
    assert.equal(typeof callback, 'function');
    callback(time);
}

async function waitForQueuedFrame(harness, attempts = 10) {
    for (let attempt = 0; attempt < attempts && harness.rafQueue.length === 0; attempt += 1) {
        await Promise.resolve();
    }
}

function createCoreHarness({
    innerWidth = 1000,
    innerHeight = 800,
    elementsById = {},
    bodyClasses = new Set(),
    getComputedStyle = null
} = {}) {
    const context = {
        console,
        setTimeout,
        clearTimeout,
        PIXI: {
            live2d: {
                Live2DModel: function Live2DModel() {}
            }
        },
        window: {
            innerWidth,
            innerHeight,
            screen: { width: innerWidth, height: innerHeight },
            devicePixelRatio: 1,
            addEventListener() {},
            __LANLAN_IS_ELECTRON_PET__: false,
            getComputedStyle: getComputedStyle || undefined
        },
        document: {
            body: {
                classList: {
                    contains: (name) => bodyClasses.has(name)
                }
            },
            getElementById: (id) => elementsById[id] || null
        }
    };
    context.globalThis = context;
    context.window.PIXI = context.PIXI;

    const source = fs.readFileSync(corePath, 'utf8');
    vm.runInNewContext(source, context, { filename: corePath });

    return {
        Live2DManager: context.window.Live2DManager,
        window: context.window
    };
}

test('edge peek enter naturally moves model offscreen and reports visible bounds', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const promise = manager._tryApplyLive2DPeek(model);
    assert.equal(manager.isLive2DPeekActive(), true);
    flushNextFrame(harness);
    const entered = await promise;

    assert.equal(entered, true);
    assert.equal(model.x, -390);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 60 * Math.PI / 180);
    assert.equal(model.scale.x, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(manager._live2DPeekState.visibleBounds)), {
        left: 0,
        right: 110,
        top: 120,
        bottom: 720,
        width: 110,
        height: 600,
        centerX: 55,
        centerY: 420
    });
    assert.equal(harness.bodyClasses.has('neko-live2d-peek'), true);
});

test('restore anchor stores semantic display identity without absolute coordinates', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    const anchor = harness.window.nekoLive2DPeek.captureRestoreAnchor();

    assert.equal(anchor.side, 'left');
    assert.equal(anchor.facing, 'inward');
    assert.equal(anchor.display.id, 'display-test');
    assert.equal(anchor.display.scaleFactor, 1.25);
    assert.equal('screenX' in anchor.display, false);
    assert.equal('screenY' in anchor.display, false);
});

test('detail-less goodbye events preserve the edge anchor for later listeners', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    let receivedAnchor = null;
    harness.window.addEventListener('live2d-goodbye-click', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        receivedAnchor = detail.edgeAnchor || (event && event.__nekoLive2DPeekEdgeAnchor) || null;
    });
    harness.window.dispatchEvent({ type: 'live2d-goodbye-click' });

    assert.equal(receivedAnchor.kind, 'live2d-edge-peek');
    assert.equal(receivedAnchor.edge, 'left');
    assert.equal(manager.isLive2DPeekActive(), false);
});

test('head anchor keeps the face visible when a tail widens the model bounds', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0, y: 120, width: 700, height: 600 });
    const transformedPoint = (localX, localY) => ({
        x: model.x + localX * Math.cos(model.rotation) - localY * Math.sin(model.rotation),
        y: model.y + localX * Math.sin(model.rotation) + localY * Math.cos(model.rotation)
    });
    manager.getHeadScreenAnchor = () => transformedPoint(140, 110);
    manager.getBodyScreenRectInfo = () => {
        const waist = transformedPoint(140, 330);
        return { rect: { centerX: waist.x, bottom: waist.y } };
    };

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    const head = manager.getHeadScreenAnchor();
    assert.equal(manager._live2DPeekState.side, 'left');
    assert.equal(manager._live2DPeekState.headAnchored, true);
    assert.ok(head.x > 20 && head.x < 260, `head should lean inside the edge, got ${head.x}`);
    assert.equal(manager._live2DPeekState.waistAnchored, true);
    assert.ok(Math.abs(manager.getBodyScreenRectInfo().rect.centerX + 8) < 0.001);
    assert.ok(Math.abs(manager.getBodyScreenRectInfo().rect.bottom - 450) < 0.001);
    const lowerBody = transformedPoint(140, 600);
    assert.ok(lowerBody.x < 0, 'lower body should remain outside the side edge');
    assert.ok(model.x > -500, 'placement must not expose only the tail-side edge of the bounds');
});

test('corner peeks keep the head at the corner and the body outside the matching vertical edge', async () => {
    const cases = [
        { edge: 'top-left', x: 0, y: 0, scaleX: 1, rotation: 135 },
        { edge: 'top-right', x: 1000, y: 0, scaleX: -1, rotation: -135 },
        { edge: 'bottom-left', x: 0, y: 200, scaleX: 1, rotation: 45, inwardX: -1 },
        { edge: 'bottom-right', x: 1000, y: 200, scaleX: -1, rotation: -45, inwardX: 1 }
    ];

    for (const item of cases) {
        const harness = createHarness();
        const manager = new harness.Live2DManager();
        const model = createRotatingModel(item);
        manager.getHeadScreenAnchor = () => model.transformPoint(150, 110);
        manager.getBodyScreenRectInfo = () => {
            const waist = model.transformPoint(150, 330);
            return { rect: { centerX: waist.x, bottom: waist.y } };
        };

        const enterPromise = manager._tryApplyLive2DPeek(model);
        flushNextFrame(harness);
        assert.equal(await enterPromise, true, `${item.edge} should enter`);
        assert.equal(manager._live2DPeekState.edge, item.edge);
        assert.equal(model.rotation, item.rotation * Math.PI / 180);

        const head = manager.getHeadScreenAnchor();
        const expectedHeadXRange = item.edge.endsWith('-left')
            ? [48, 84]
            : [916, 952];
        assert.ok(
            head.x >= expectedHeadXRange[0] && head.x <= expectedHeadXRange[1],
            `${item.edge} head should sit just inside the side edge, got ${head.x}`
        );

        if (item.edge.startsWith('top-')) {
            const lowerBody = model.transformPoint(150, 560);
            assert.ok(head.y >= 36 && head.y <= 64, `${item.edge} head should sit just below the top edge`);
            assert.ok(lowerBody.y < head.y, `${item.edge} body should stay above the head`);
            assert.ok(lowerBody.y < 0, `${item.edge} body should stay outside above the viewport`);
        } else {
            const lowerBody = model.transformPoint(150, 560);
            assert.ok(head.y >= 736 && head.y <= 764, `${item.edge} head should sit just above the bottom edge`);
            assert.ok(lowerBody.y > head.y, `${item.edge} body should stay below the head`);
            assert.ok(lowerBody.y > 800, `${item.edge} body should stay outside below the viewport`);
        }
    }
});

test('semantic corner anchor restores the model to the same corner', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createRotatingModel({ x: 0, y: 0, scaleX: 1 });
    manager.currentModel = model;
    manager.getHeadScreenAnchor = () => model.transformPoint(150, 110);
    manager.getBodyScreenRectInfo = () => {
        const waist = model.transformPoint(150, 330);
        return { rect: { centerX: waist.x, bottom: waist.y } };
    };
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    const anchor = harness.window.nekoLive2DPeek.captureRestoreAnchor();
    assert.equal(anchor.edge, 'top-left');

    manager.clearLive2DPeek('widget-mode-disabled');
    model.x = 350;
    model.y = 100;
    const restorePromise = harness.window.nekoLive2DPeek.restoreAnchor(anchor);
    flushNextFrame(harness);
    assert.equal(await restorePromise, true);
    assert.equal(manager._live2DPeekState.edge, 'top-left');
    assert.equal(model.rotation, 135 * Math.PI / 180);
});

test('corner peeks fall back to model transforms when no head anchor is available', async () => {
    const cases = [
        { edge: 'top-left', y: 0, headYRange: [36, 64], bodyOutside: (bodyY) => bodyY < 0 },
        { edge: 'bottom-left', y: 200, headYRange: [736, 764], bodyOutside: (bodyY) => bodyY > 800 }
    ];

    for (const item of cases) {
        const harness = createHarness();
        const manager = new harness.Live2DManager();
        const model = createRotatingModel({ x: 0, y: item.y, scaleX: 1 });

        const enterPromise = manager._tryApplyLive2DPeek(model);
        flushNextFrame(harness);
        assert.equal(await enterPromise, true);

        const estimatedHead = model.transformPoint(150, 600 * 0.24);
        const lowerBody = model.transformPoint(150, 560);
        assert.equal(manager._live2DPeekState.edge, item.edge);
        assert.equal(manager._live2DPeekState.headAnchorSource, 'bounds-fallback');
        assert.ok(estimatedHead.x >= 48 && estimatedHead.x <= 84, `fallback head x should remain visible, got ${estimatedHead.x}`);
        assert.ok(
            estimatedHead.y >= item.headYRange[0] && estimatedHead.y <= item.headYRange[1],
            `${item.edge} fallback head y should remain visible, got ${estimatedHead.y}`
        );
        assert.ok(item.bodyOutside(lowerBody.y), `${item.edge} fallback should keep the lower body outside the viewport`);
    }
});

test('macOS top corners trigger at the current display work-area top', async () => {
    const cases = [
        { edge: 'top-left', x: 0, scaleX: 1, rotation: 135 },
        { edge: 'top-right', x: 1000, scaleX: -1, rotation: -135 }
    ];

    for (const item of cases) {
        const harness = createHarness({
            platform: 'darwin',
            currentDisplay: {
                screenX: 0,
                screenY: 0,
                width: 1000,
                height: 800,
                workArea: { x: 0, y: 28, width: 1000, height: 744 }
            }
        });
        const manager = new harness.Live2DManager();
        const model = createRotatingModel({ x: item.x, y: 28, scaleX: item.scaleX });
        manager.getHeadScreenAnchor = () => model.transformPoint(150, 110);
        manager.getBodyScreenRectInfo = () => {
            const waist = model.transformPoint(150, 330);
            return { rect: { centerX: waist.x, bottom: waist.y } };
        };

        const enterPromise = manager._tryApplyLive2DPeek(model);
        await waitForQueuedFrame(harness);
        flushNextFrame(harness);
        assert.equal(await enterPromise, true);
        assert.equal(manager._live2DPeekState.edge, item.edge);
        assert.equal(model.rotation, item.rotation * Math.PI / 180);
    }
});

test('Windows bottom corners trigger at the current display work-area bottom', async () => {
    const cases = [
        { edge: 'bottom-left', x: 0, scaleX: 1, rotation: 45 },
        { edge: 'bottom-right', x: 1000, scaleX: -1, rotation: -45 }
    ];

    for (const item of cases) {
        const harness = createHarness({
            innerHeight: 1440,
            platform: 'windows',
            currentDisplay: {
                screenX: 0,
                screenY: 0,
                width: 1000,
                height: 1440,
                workArea: { x: 0, y: 0, width: 1000, height: 1392 }
            }
        });
        const manager = new harness.Live2DManager();
        const model = createRotatingModel({ x: item.x, y: 792, scaleX: item.scaleX });
        manager.getHeadScreenAnchor = () => model.transformPoint(150, 110);
        manager.getBodyScreenRectInfo = () => {
            const waist = model.transformPoint(150, 330);
            return { rect: { centerX: waist.x, bottom: waist.y } };
        };

        const enterPromise = manager._tryApplyLive2DPeek(model);
        await waitForQueuedFrame(harness);
        flushNextFrame(harness);
        assert.equal(await enterPromise, true);
        assert.equal(manager._live2DPeekState.edge, item.edge);
        assert.equal(model.rotation, item.rotation * Math.PI / 180);
    }
});

test('clearing during enter animation prevents stale peek writeback', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const promise = manager._tryApplyLive2DPeek(model);
    assert.equal(manager.isLive2DPeekActive(), true);
    manager.clearLive2DPeek('widget-mode-disabled');

    flushNextFrame(harness);
    const entered = await promise;

    assert.equal(entered, false);
    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-peek'), false);
});

test('right edge peek faces inward and restores original transform', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 520, y: 100 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    assert.equal(model.x, 890);
    assert.equal(model.y, 100);
    assert.equal(model.rotation, -60 * Math.PI / 180);
    assert.equal(model.scale.x, 1);

    const restorePromise = manager.restoreLive2DPeek('click-restore');
    flushNextFrame(harness);
    assert.equal(await restorePromise, true);

    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(model.x, 520);
    assert.equal(model.y, 100);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-peek'), false);
});

test('edge peek uses renderer screen bounds when canvas differs from window viewport', async () => {
    const harness = createHarness({ innerWidth: 1280, innerHeight: 720 });
    const manager = new harness.Live2DManager();
    manager.pixi_app = {
        renderer: {
            screen: { width: 1080, height: 720 }
        }
    };
    const model = createModel({ x: 580, y: 100 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    assert.equal(model.x, 970);
    assert.equal(model.rotation, -60 * Math.PI / 180);
    assert.equal(model.scale.x, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(manager._live2DPeekState.visibleBounds)), {
        left: 970,
        right: 1080,
        top: 100,
        bottom: 700,
        width: 110,
        height: 600,
        centerX: 1025,
        centerY: 400
    });
});

test('normal snap uses renderer screen bounds before edge peek stores its base position', async () => {
    const harness = createHarness({ innerWidth: 1280, innerHeight: 720 });
    const manager = new harness.Live2DManager();
    manager.pixi_app = {
        renderer: {
            screen: { width: 1080, height: 720 }
        }
    };
    const model = createModel({ x: 1180, y: 100, width: 300, height: 500 });

    const snap = await manager._checkSnapRequired(model);

    assert.equal(snap.targetX, 775);
    assert.equal(snap.targetY, 100);
    assert.equal(snap.overflow.right, 400);
});

test('normal snap supports all four edges and all four corners', async () => {
    const cases = [
        { name: 'left', x: -450, y: 100, targetX: 5, targetY: 100 },
        { name: 'right', x: 950, y: 100, targetX: 495, targetY: 100 },
        { name: 'top', x: 250, y: -550, targetX: 250, targetY: 5 },
        { name: 'bottom', x: 250, y: 750, targetX: 250, targetY: 195 },
        { name: 'top-left', x: -450, y: -550, targetX: 5, targetY: 5 },
        { name: 'top-right', x: 950, y: -550, targetX: 495, targetY: 5 },
        { name: 'bottom-left', x: -450, y: 750, targetX: 5, targetY: 195 },
        { name: 'bottom-right', x: 950, y: 750, targetX: 495, targetY: 195 }
    ];

    for (const item of cases) {
        const harness = createHarness();
        const manager = new harness.Live2DManager();
        const model = createModel({ x: item.x, y: item.y });
        const snap = await manager._checkSnapRequired(model);
        assert.ok(snap, `${item.name} should snap`);
        assert.equal(snap.targetX, item.targetX, `${item.name} targetX`);
        assert.equal(snap.targetY, item.targetY, `${item.name} targetY`);
    }
});

test('drag-style clear exits peek without restoring position but restores transform', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0, y: 120 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    assert.equal(model.x, -390);

    manager.clearLive2DPeek('drag-start', { restore: false });

    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(model.x, -390);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
});

test('edge peek only triggers while Widget Mode is enabled', async () => {
    const harness = createHarness({ widgetModeEnabled: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0, y: 120 });

    const entered = await manager._tryApplyLive2DPeek(model);

    assert.equal(entered, false);
    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(harness.rafQueue.length, 0);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
});

test('Widget Mode disabled event restores active edge peek to its base position', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    harness.window.live2dManager = manager;
    const model = createModel({ x: 0, y: 120 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    assert.equal(model.x, -390);

    harness.window.dispatchEvent({
        type: 'neko:widget-mode-state-changed',
        detail: { enabled: false }
    });

    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-peek'), false);
});

test('top and bottom edges alone do not trigger edge peek', async () => {
    const topHarness = createHarness();
    const topManager = new topHarness.Live2DManager();
    const topModel = createModel({ x: 250, y: 0 });

    assert.equal(await topManager._tryApplyLive2DPeek(topModel), false);
    assert.equal(topManager.isLive2DPeekActive(), false);
    assert.equal(topHarness.rafQueue.length, 0);

    const bottomHarness = createHarness();
    const bottomManager = new bottomHarness.Live2DManager();
    const bottomModel = createModel({ x: 250, y: 200, height: 600 });

    assert.equal(await bottomManager._tryApplyLive2DPeek(bottomModel), false);
    assert.equal(bottomManager.isLive2DPeekActive(), false);
    assert.equal(bottomHarness.rafQueue.length, 0);
});

test('visible reveal width is clamped between 96 and 180 pixels', async () => {
    const narrowHarness = createHarness();
    const narrowManager = new narrowHarness.Live2DManager();
    const narrowModel = createModel({ x: 0, width: 300 });

    const narrowPromise = narrowManager._tryApplyLive2DPeek(narrowModel);
    flushNextFrame(narrowHarness);
    assert.equal(await narrowPromise, true);
    assert.equal(narrowManager._live2DPeekState.visibleBounds.width, 96);
    assert.equal(narrowModel.x, -204);

    const wideHarness = createHarness();
    const wideManager = new wideHarness.Live2DManager();
    const wideModel = createModel({ x: 0, width: 1200 });

    const widePromise = wideManager._tryApplyLive2DPeek(wideModel);
    flushNextFrame(wideHarness);
    assert.equal(await widePromise, true);
    assert.equal(wideManager._live2DPeekState.visibleBounds.width, 180);
});

test('core model screen bounds reports viewport intersection while edge peek is active', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: -390, y: 120, width: 500, height: 600 });
    manager.currentModel = model;

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelScreenBounds())), {
        left: -390,
        right: 110,
        top: 120,
        bottom: 720,
        width: 500,
        height: 600,
        centerX: -140,
        centerY: 420
    });

    manager._live2DPeekState = {
        active: true,
        model
    };

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelScreenBounds())), {
        left: 0,
        right: 110,
        top: 120,
        bottom: 720,
        width: 110,
        height: 600,
        centerX: 55,
        centerY: 420
    });
});

test('core edge peek screen bounds use renderer screen instead of wider window', () => {
    const harness = createCoreHarness({ innerWidth: 1280, innerHeight: 720 });
    const manager = new harness.Live2DManager();
    manager.pixi_app = {
        renderer: {
            screen: { width: 1080, height: 720 }
        }
    };
    const model = createModel({ x: 970, y: 100, width: 500, height: 600 });
    manager.currentModel = model;
    manager._live2DPeekState = {
        active: true,
        model
    };

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelScreenBounds())), {
        left: 970,
        right: 1080,
        top: 100,
        bottom: 700,
        width: 110,
        height: 600,
        centerX: 1025,
        centerY: 400
    });
});

test('core model input regions preserve asymmetric drawable geometry before viewport clipping', () => {
    const harness = createCoreHarness({ innerWidth: 1920, innerHeight: 1200 });
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager.pixi_app = {
        renderer: {
            screen: { width: 1920, height: 1200 }
        }
    };
    manager.getModelScreenBounds = () => ({
        left: 1600,
        right: 1940,
        top: 200,
        bottom: 900,
        width: 340,
        height: 700,
        centerX: 1770,
        centerY: 550
    });
    manager._getRenderableDrawableScreenRects = () => [
        {
            left: 1810,
            right: 1940,
            top: 310,
            bottom: 820,
            width: 130,
            height: 510,
            centerX: 1875,
            centerY: 565
        },
        {
            left: 1935,
            right: 1970,
            top: 420,
            bottom: 500,
            width: 35,
            height: 80,
            centerX: 1952.5,
            centerY: 460
        }
    ];

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: 8 }))), [
        {
            left: 1802,
            right: 1920,
            top: 302,
            bottom: 828,
            width: 118,
            height: 526,
            centerX: 1861,
            centerY: 565
        }
    ]);
});

test('core model input regions return empty when drawable geometry is unavailable', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager.getModelScreenBounds = () => ({
        left: 100,
        right: 500,
        top: 100,
        bottom: 700,
        width: 400,
        height: 600
    });
    manager._getRenderableDrawableScreenRects = () => [];

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
});

test('core model input regions stay empty while edge peek is hiding or hidden', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager._getRenderableDrawableScreenRects = () => {
        throw new Error('hidden edge peek must not calculate drawable geometry');
    };

    for (const phase of ['hiding', 'hidden']) {
        manager._live2DPeekState = { active: true, phase };
        assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    }
});

test('core model input regions stay empty until interaction is ready', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager._getRenderableDrawableScreenRects = () => {
        throw new Error('loading model must not calculate drawable geometry');
    };

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
});

test('core model input regions stay empty when the model surface is hidden or non-interactive', () => {
    const containerClasses = new Set(['minimized']);
    const bodyClasses = new Set();
    const container = {
        classList: { contains: (name) => containerClasses.has(name) },
        style: {},
        getAttribute: () => null
    };
    const canvas = {
        classList: { contains: () => false },
        style: {},
        getAttribute: () => null
    };
    const harness = createCoreHarness({
        elementsById: {
            'live2d-container': container,
            'live2d-canvas': canvas
        },
        bodyClasses,
        getComputedStyle: (element) => {
            if (bodyClasses.has('neko-game-active')) {
                return { ...element.style, display: 'none' };
            }
            if (bodyClasses.has('yui-guide-live2d-preparing')) {
                return { ...element.style, opacity: '0', pointerEvents: 'none' };
            }
            return {
                ...element.style,
                transform: element.style.transform || 'matrix(1, 0, 0, 1, 0, 0)'
            };
        }
    });
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager._getRenderableDrawableScreenRects = () => {
        throw new Error('hidden model must not calculate drawable geometry');
    };

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    containerClasses.clear();
    canvas.style.pointerEvents = 'none';
    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    canvas.style.pointerEvents = '';
    for (const bodyClass of [
        'neko-main-ui-hidden-by-model-manager',
        'neko-model-hidden-by-manager-overlap'
    ]) {
        bodyClasses.clear();
        bodyClasses.add(bodyClass);
        assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    }
    for (const bodyClass of [
        'neko-game-active',
        'yui-guide-live2d-preparing'
    ]) {
        bodyClasses.clear();
        bodyClasses.add(bodyClass);
        assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    }
    bodyClasses.clear();
    harness.window._nekoModelReturnEnterContainer = container;
    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    harness.window._nekoModelReturnEnterContainer = null;
    container.style.transform = 'translate3d(8px, 0, 0)';
    harness.window._nekoAvatarPerformanceFrameContainer = container;
    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects())), []);
    container.style.transform = '';
    assert.equal(manager._isModelInputRegionInteractive(), true);
    assert.equal(harness.window._nekoAvatarPerformanceFrameContainer, null);
});

test('performance frame session markers clear on restore and remain for committed transforms', () => {
    const container = {
        style: {
            transform: '',
            transition: '',
            transformOrigin: '',
            opacity: '',
            willChange: ''
        }
    };
    const replacementContainer = {
        style: {
            transform: '',
            transition: '',
            transformOrigin: '',
            opacity: '',
            willChange: ''
        }
    };
    let currentContainer = container;
    const context = {
        console,
        document: {
            getElementById: (id) => id === 'live2d-container' ? container : null
        },
        window: {
            performance: { now: () => 0 },
            matchMedia: () => ({ matches: false }),
            requestAnimationFrame: () => 1,
            cancelAnimationFrame: () => {},
            setTimeout
        }
    };
    context.globalThis = context;
    vm.runInNewContext(
        fs.readFileSync(performanceStagePath, 'utf8'),
        context,
        { filename: performanceStagePath }
    );

    const driver = context.window.AvatarPerformance.createLive2DDriver({
        managerResolver: () => null,
        containerResolver: () => currentContainer
    });
    const stage = context.window.AvatarPerformance.createStage({ driver });
    const session = stage.acquire('input-region-test', { capabilities: ['frame'] });

    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, container);
    assert.equal(stage.release(session.id, 'test-complete'), true);
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, null);

    const committedSession = stage.acquire('committed-frame-test', { capabilities: ['frame'] });
    driver.applyFrame({ x: 12, y: 0, scale: 1, rotate: 0, opacity: '' }, committedSession);
    assert.equal(stage.commitCurrentFrameAsBaseline(committedSession.id), true);
    assert.equal(stage.release(committedSession.id, 'commit-complete'), true);
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, container);

    container.style.transform = '';
    const clearedSession = stage.acquire('externally-cleared-transform-test', { capabilities: ['motion'] });
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, null);
    assert.equal(stage.release(clearedSession.id, 'external-transform-cleared'), true);

    const fullTurnSession = stage.acquire('full-turn-frame-test', { capabilities: ['frame'] });
    driver.applyFrame({ x: 0, y: 0, scale: 1, rotate: 360, opacity: '' }, fullTurnSession);
    assert.equal(stage.commitCurrentFrameAsBaseline(fullTurnSession.id), true);
    assert.equal(stage.release(fullTurnSession.id, 'full-turn-committed'), true);
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, null);

    const replacedSession = stage.acquire('replaced-container-test', { capabilities: ['frame'] });
    driver.applyFrame({ x: 8, y: 0, scale: 1, rotate: 0, opacity: '' }, replacedSession);
    currentContainer = replacementContainer;
    assert.equal(stage.release(replacedSession.id, 'container-replaced'), true);
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, null);
    assert.equal(replacementContainer.style.transform, '');

    const removedSession = stage.acquire('removed-container-test', { capabilities: ['frame'] });
    driver.applyFrame({ x: 6, y: 0, scale: 1, rotate: 0, opacity: '' }, removedSession);
    currentContainer = null;
    assert.equal(stage.release(removedSession.id, 'container-removed'), true);
    assert.equal(context.window._nekoAvatarPerformanceFrameContainer, null);
});

test('core model input regions clamp padding to the supported 0-32 range', () => {
    const harness = createCoreHarness({ innerWidth: 1000, innerHeight: 1000 });
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager.pixi_app = {
        renderer: {
            screen: { width: 1000, height: 1000 }
        }
    };
    manager.getModelScreenBounds = () => ({
        left: 400,
        right: 500,
        top: 400,
        bottom: 500,
        width: 100,
        height: 100,
        centerX: 450,
        centerY: 450
    });
    manager._getRenderableDrawableScreenRects = () => [
        {
            left: 400,
            right: 500,
            top: 400,
            bottom: 500,
            width: 100,
            height: 100,
            centerX: 450,
            centerY: 450
        }
    ];

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: 100 }))), [
        {
            left: 368,
            right: 532,
            top: 368,
            bottom: 532,
            width: 164,
            height: 164,
            centerX: 450,
            centerY: 450
        }
    ]);
    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: -5 }))), [
        {
            left: 400,
            right: 500,
            top: 400,
            bottom: 500,
            width: 100,
            height: 100,
            centerX: 450,
            centerY: 450
        }
    ]);
    for (const invalidPadding of [null, '', false, true]) {
        assert.deepEqual(
            JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: invalidPadding }))),
            [
                {
                    left: 392,
                    right: 508,
                    top: 392,
                    bottom: 508,
                    width: 116,
                    height: 116,
                    centerX: 450,
                    centerY: 450
                }
            ]
        );
    }
    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: '12' }))), [
        {
            left: 388,
            right: 512,
            top: 388,
            bottom: 512,
            width: 124,
            height: 124,
            centerX: 450,
            centerY: 450
        }
    ]);
});

test('core model input regions keep per-drawable mapped geometry when direct vertices are unavailable', () => {
    const harness = createCoreHarness({ innerWidth: 800, innerHeight: 600 });
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager.currentModel = {
        internalModel: {
            coreModel: {
                getDrawableCount: () => 2
            }
        }
    };
    manager.pixi_app = {
        renderer: {
            screen: { width: 800, height: 600 }
        }
    };
    manager.getModelScreenBounds = () => ({
        left: 100,
        right: 500,
        top: 60,
        bottom: 560,
        width: 400,
        height: 500
    });
    manager._getModelLogicalRect = () => ({
        left: -1,
        right: 1,
        top: -1,
        bottom: 1,
        width: 2,
        height: 2
    });
    manager._ensureModelWorldTransform = () => {};
    manager._isDrawableRenderable = () => true;
    manager._getDrawableDirectScreenRect = () => null;
    manager._getDrawableScreenRect = (index) => index === 0
        ? {
            left: 140,
            right: 240,
            top: 120,
            bottom: 260,
            width: 100,
            height: 140
        }
        : {
            left: 300,
            right: 420,
            top: 280,
            bottom: 500,
            width: 120,
            height: 220
        };

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: 0 }))), [
        {
            left: 140,
            right: 240,
            top: 120,
            bottom: 260,
            width: 100,
            height: 140,
            centerX: 190,
            centerY: 190
        },
        {
            left: 300,
            right: 420,
            top: 280,
            bottom: 500,
            width: 120,
            height: 220,
            centerX: 360,
            centerY: 390
        }
    ]);
});

test('core model input regions enumerate visible legacy Cubism 2 draw data', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    const drawContexts = [
        { _$IP: 0, _$VS: 1, baseOpacity: 1, _$yo: () => true },
        { _$IP: 1, _$VS: 1, baseOpacity: 1, _$yo: () => true },
        { _$IP: 2, _$VS: 1, baseOpacity: 1, _$yo: () => true },
        { _$IP: 3, _$VS: 1, baseOpacity: 1, _$yo: () => false },
        { _$IP: 4, _$VS: 1, baseOpacity: 1, _$yo: () => true }
    ];
    const drawData = [
        { getOpacity: () => 1 },
        { getOpacity: () => 0 },
        { getOpacity: () => 1 },
        { getOpacity: () => 1 }
    ];
    const modelContext = {
        _$8b: drawContexts,
        _$Hr: [
            { getPartsOpacity: () => 1 },
            { getPartsOpacity: () => 1 },
            { getPartsOpacity: () => 0 },
            { getPartsOpacity: () => 1 }
        ],
        getDrawData: (index) => drawData[index]
    };
    manager.currentModel = {
        internalModel: {
            coreModel: {
                getModelContext: () => modelContext
            },
            drawDataCount: 5,
            getDrawableBounds: (index) => index === 0
                ? { x: -1, y: -2, width: 1, height: 2 }
                : { x: 0, y: 0, width: 2, height: 3 }
        }
    };
    manager.getModelScreenBounds = () => ({ left: 0, right: 100, top: 0, bottom: 100 });
    manager._ensureModelWorldTransform = () => {};
    manager._getDrawableScreenRect = (index) => ({
        left: index * 10,
        right: index * 10 + 5,
        top: 0,
        bottom: 5,
        width: 5,
        height: 5
    });

    assert.deepEqual(
        JSON.parse(JSON.stringify(manager._getModelLogicalRect())),
        { x: -1, y: -2, width: 3, height: 5 }
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(manager._getRenderableDrawableScreenRects(null, null, true))),
        [
            {
                index: 0,
                rect: { left: 0, right: 5, top: 0, bottom: 5, width: 5, height: 5 }
            }
        ]
    );
});

test('core drawable collection keeps direct vertices when model mapping bounds are unavailable', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager.currentModel = {
        internalModel: {
            coreModel: {
                getDrawableCount: () => 1
            }
        }
    };
    manager.getModelScreenBounds = () => null;
    manager._getModelLogicalRect = () => null;
    manager._ensureModelWorldTransform = () => {};
    manager._isDrawableRenderable = () => true;
    manager._getDrawableDirectScreenRect = () => ({
        left: 20,
        right: 80,
        top: 30,
        bottom: 90,
        width: 60,
        height: 60,
        centerX: 50,
        centerY: 60
    });

    assert.deepEqual(
        JSON.parse(JSON.stringify(manager._getRenderableDrawableScreenRects(null, null, false))),
        [
            {
                left: 20,
                right: 80,
                top: 30,
                bottom: 90,
                width: 60,
                height: 60,
                centerX: 50,
                centerY: 60
            }
        ]
    );
});

test('core DisplayInfo collection uses legacy Cubism 2 drawable count fallback', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager.currentModel = {
        internalModel: {
            coreModel: {},
            drawDataCount: 1
        }
    };
    manager.getModelScreenBounds = () => ({
        left: 0,
        right: 100,
        top: 0,
        bottom: 100,
        width: 100,
        height: 100
    });
    manager._getModelLogicalRect = () => ({ x: -1, y: -1, width: 2, height: 2 });
    manager._getCoreModelPartIds = () => ['PartFace'];
    manager._getCoreModelDrawableParentPartIndices = () => [0];
    manager._getCoreModelPartParentPartIndices = () => [-1];
    manager._partIndexMatchesTargetIds = () => true;
    manager._isDrawableRenderable = () => true;
    manager._getDrawableScreenRect = () => ({
        left: 20,
        right: 80,
        top: 10,
        bottom: 70,
        width: 60,
        height: 60,
        centerX: 50,
        centerY: 40
    });

    assert.deepEqual(
        JSON.parse(JSON.stringify(manager._collectDisplayInfoPartScreenRectInfo(['PartFace'], 'face'))),
        {
            rect: {
                left: 20,
                right: 80,
                top: 10,
                bottom: 70,
                width: 60,
                height: 60,
                centerX: 50,
                centerY: 40
            },
            mode: 'face',
            source: 'displayInfo'
        }
    );
});

test('core drawable fallback transforms logical corners through a rotated model', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager.currentModel = {
        internalModel: {
            localTransform: { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
            getDrawableBounds: () => ({ x: 10, y: 20, width: 30, height: 40 })
        },
        worldTransform: { a: 0, b: 1, c: -1, d: 0, tx: 300, ty: 100 }
    };
    manager._getDrawableVertexSequence = () => null;

    assert.deepEqual(JSON.parse(JSON.stringify(manager._getDrawableScreenRect(
        0,
        { x: 0, y: 0, width: 100, height: 100 },
        { left: 0, right: 500, top: 0, bottom: 500, width: 500, height: 500 },
        true
    ))), {
        left: 240,
        right: 280,
        top: 110,
        bottom: 140,
        width: 40,
        height: 30,
        centerX: 260,
        centerY: 125
    });
});

test('core edge peek fallback maps drawables against unclipped model bounds before viewport clipping', () => {
    const harness = createCoreHarness({ innerWidth: 800, innerHeight: 600 });
    const manager = new harness.Live2DManager();
    manager._isModelReadyForInteraction = true;
    const model = {
        destroyed: false,
        getBounds: () => ({
            left: -390,
            right: 110,
            top: 0,
            bottom: 600,
            width: 500,
            height: 600
        }),
        internalModel: {
            coreModel: {
                getDrawableCount: () => 1
            }
        }
    };
    manager.currentModel = model;
    manager._live2DPeekState = { active: true, model };
    manager.pixi_app = {
        renderer: {
            screen: { width: 800, height: 600 }
        }
    };
    manager._getModelLogicalRect = () => ({
        x: -1,
        y: -1,
        width: 2,
        height: 2
    });
    manager._getDrawableLogicalRect = () => ({
        x: 0.6,
        y: -0.5,
        width: 0.4,
        height: 1
    });
    manager._getDrawableDirectScreenRect = () => null;
    manager._ensureModelWorldTransform = () => {};
    manager._isDrawableRenderable = () => true;

    assert.deepEqual(JSON.parse(JSON.stringify(manager.getModelInputRegionRects({ padding: 0 }))), [
        {
            left: 10,
            right: 110,
            top: 150,
            bottom: 450,
            width: 100,
            height: 300,
            centerX: 60,
            centerY: 300
        }
    ]);
});

test('ordinary browser runtime cannot arm edge peek', async () => {
    const harness = createHarness({ desktopRuntime: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    assert.equal(await manager._tryApplyLive2DPeek(model), false);
    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(model.x, 0);
    assert.equal(model.interactive, true);
});

test('idle anchored model becomes fully hidden and non-interactive', async () => {
    const harness = createHarness({ interactionActive: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    assert.equal(manager._live2DPeekState.phase, 'hiding');
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.ok(model.getBounds().right < 0);
    assert.equal(model.interactive, false);
    assert.equal(manager._live2DPeekState.visibleBounds.left, 0);
});

test('peek reveal uses a soft overshoot and settles on the exact anchor', async () => {
    const harness = createHarness({ interactionActive: true });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const enterPromise = manager._tryApplyLive2DPeek(model);
    const targetX = manager._live2DPeekState.peekX;
    flushNextFrame(harness, 225);
    assert.ok(model.x < targetX, 'soft-back easing should briefly pass the left anchor');
    flushNextFrame(harness, 400);
    assert.equal(await enterPromise, true);
    assert.equal(model.x, targetX);
    assert.equal(manager._live2DPeekState.phase, 'peeking');
});

test('reduced-motion preference completes the peek transition without RAF', async () => {
    const harness = createHarness({
        interactionActive: false,
        reducedMotion: true
    });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    assert.equal(await manager._tryApplyLive2DPeek(model), true);
    assert.equal(harness.rafQueue.length, 0);
    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.equal(model.interactive, false);
});

test('parent Edge Peek stays visible until the Stealth Mode child is enabled', async () => {
    const harness = createHarness({
        interactionActive: false,
        stealthModeEnabled: false
    });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    assert.equal(manager._live2DPeekState.phase, 'peeking');
    assert.equal(model.interactive, true);
    assert.ok(model.getBounds().right > 0);

    harness.setStealthModeEnabled(true);
    harness.window.dispatchEvent({
        type: 'neko:widget-mode-state-changed',
        detail: { enabled: true, stealthEnabled: true }
    });
    assert.equal(manager._live2DPeekState.phase, 'hiding');
    flushNextFrame(harness);
    await Promise.resolve();
    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.equal(model.interactive, false);
});

test('interaction state reveals and then hides the existing anchor', async () => {
    const harness = createHarness({ interactionActive: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    harness.window.dispatchEvent({
        type: 'neko:widget-interaction-state-changed',
        detail: { active: true, reason: 'user-message' }
    });
    assert.equal(manager._live2DPeekState.phase, 'revealing');
    flushNextFrame(harness);
    await Promise.resolve();
    assert.equal(manager._live2DPeekState.phase, 'peeking');
    assert.equal(model.interactive, true);
    assert.ok(model.getBounds().right > 0);

    harness.window.dispatchEvent({
        type: 'neko:widget-interaction-state-changed',
        detail: { active: false, reason: 'complete' }
    });
    assert.equal(manager._live2DPeekState.phase, 'hiding');
    flushNextFrame(harness);
    await Promise.resolve();
    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.equal(model.interactive, false);
    assert.ok(model.getBounds().right < 0);
});

test('new interaction transition cancels stale animation writeback', async () => {
    const harness = createHarness({ interactionActive: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    flushNextFrame(harness);
    await enterPromise;

    harness.window.dispatchEvent({
        type: 'neko:widget-interaction-state-changed',
        detail: { active: true, reason: 'start' }
    });
    harness.window.dispatchEvent({
        type: 'neko:widget-interaction-state-changed',
        detail: { active: false, reason: 'cancel' }
    });
    assert.equal(manager._live2DPeekState.phase, 'hiding');

    flushNextFrame(harness);
    flushNextFrame(harness);
    await Promise.resolve();
    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.equal(model.interactive, false);
    assert.ok(model.getBounds().right < 0);
});

test('display change rebuilds the active anchor against the refreshed display', async () => {
    const currentDisplay = {
        screenX: 0,
        screenY: 0,
        width: 1000,
        height: 800,
        workArea: { x: 0, y: 0, width: 1000, height: 800 }
    };
    const harness = createHarness({
        interactionActive: false,
        currentDisplay
    });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;
    manager.currentModel = model;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    await waitForQueuedFrame(harness);
    flushNextFrame(harness);
    await enterPromise;
    const hiddenRotation = model.rotation;
    const hiddenScaleX = model.scale.x;
    currentDisplay.height = 1000;
    currentDisplay.workArea.height = 1000;
    harness.window.innerHeight = 1000;
    harness.window.screen.height = 1000;
    harness.window.dispatchEvent({ type: 'electron-display-changed' });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(
        manager.isLive2DPeekActive(),
        false,
        'anchor restore must wait until the renderer delayed resize has settled'
    );
    harness.window.dispatchEvent({ type: 'electron-display-changed' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setTimeout(resolve, 180));
    flushNextFrame(harness);
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(manager.isLive2DPeekActive(), true);
    assert.equal(manager._live2DPeekState.phase, 'hidden');
    assert.equal(model.x, -502);
    assert.equal(model.y, 225);
    assert.equal(model.rotation, hiddenRotation);
    assert.equal(model.scale.x, hiddenScaleX);
    assert.equal(model.interactive, false);
});

test('non-display clear invalidates a pending display anchor restore', async () => {
    const currentDisplay = {
        screenX: 0,
        screenY: 0,
        width: 1000,
        height: 800,
        workArea: { x: 0, y: 0, width: 1000, height: 800 }
    };
    const harness = createHarness({ currentDisplay });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;
    manager.currentModel = model;

    const enterPromise = manager._tryApplyLive2DPeek(model);
    await waitForQueuedFrame(harness);
    flushNextFrame(harness);
    await enterPromise;

    harness.window.dispatchEvent({ type: 'electron-display-changed' });
    await new Promise((resolve) => setImmediate(resolve));
    harness.window.nekoLive2DPeek.clear('model-reload');
    await new Promise((resolve) => setTimeout(resolve, 180));

    assert.equal(manager.isLive2DPeekActive(), false);
    assert.equal(harness.rafQueue.length, 0);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
});

test('core model bounds are null while an anchor is hiding or hidden', () => {
    const harness = createCoreHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: -502 });
    manager.currentModel = model;
    manager._live2DPeekState = { active: true, phase: 'hidden', model };

    assert.equal(manager.getModelScreenBounds(), null);
    manager._live2DPeekState.phase = 'hiding';
    assert.equal(manager.getModelScreenBounds(), null);
});

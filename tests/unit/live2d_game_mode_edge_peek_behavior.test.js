const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const interactionPath = path.join(projectRoot, 'static', 'live2d', 'live2d-interaction.js');
const corePath = path.join(projectRoot, 'static', 'live2d', 'live2d-core.js');

function createHarness({
    gameModeEnabled = true,
    innerWidth = 1000,
    innerHeight = 800,
    platform = '',
    currentDisplay = null
} = {}) {
    const rafQueue = [];
    const bodyClasses = new Set();
    const listeners = new Map();

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
            __NEKO_DESKTOP_RUNTIME__: platform ? { platform } : {},
            electronScreen: currentDisplay ? {
                async getCurrentDisplay() {
                    return currentDisplay;
                }
            } : null,
            nekoGameModeBeta: {
                isEnabled: () => gameModeEnabled
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

    return { Live2DManager, rafQueue, bodyClasses, window: context.window };
}

function createModel({ x = 0, y = 120, width = 500, height = 600 } = {}) {
    return {
        x,
        y,
        rotation: 0,
        destroyed: false,
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

function flushNextFrame(harness, time = 250) {
    const callback = harness.rafQueue.shift();
    assert.equal(typeof callback, 'function');
    callback(time);
}

function createCoreHarness({ innerWidth = 1000, innerHeight = 800 } = {}) {
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
            __LANLAN_IS_ELECTRON_PET__: false
        },
        document: {
            getElementById: () => null
        }
    };
    context.globalThis = context;
    context.window.PIXI = context.PIXI;

    const source = fs.readFileSync(corePath, 'utf8');
    vm.runInNewContext(source, context, { filename: corePath });

    return {
        Live2DManager: context.window.Live2DManager
    };
}

test('edge peek enter naturally moves model offscreen and reports visible bounds', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const promise = manager._tryApplyLive2DGameModeEdgePeek(model);
    assert.equal(manager.isLive2DGameModeEdgePeekActive(), true);
    flushNextFrame(harness);
    const entered = await promise;

    assert.equal(entered, true);
    assert.equal(model.x, -390);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 60 * Math.PI / 180);
    assert.equal(model.scale.x, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(manager._live2DGameModeEdgePeekState.visibleBounds)), {
        left: 0,
        right: 110,
        top: 120,
        bottom: 720,
        width: 110,
        height: 600,
        centerX: 55,
        centerY: 420
    });
    assert.equal(harness.bodyClasses.has('neko-live2d-game-mode-edge-peek'), true);
});

test('restore anchor stores semantic display identity without absolute coordinates', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });
    harness.window.live2dManager = manager;

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    const anchor = harness.window.nekoLive2DGameModeEdgePeek.captureRestoreAnchor();

    assert.equal(anchor.side, 'left');
    assert.equal(anchor.facing, 'inward');
    assert.equal(anchor.display.id, 'display-test');
    assert.equal(anchor.display.scaleFactor, 1.25);
    assert.equal('screenX' in anchor.display, false);
    assert.equal('screenY' in anchor.display, false);
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

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    const head = manager.getHeadScreenAnchor();
    assert.equal(manager._live2DGameModeEdgePeekState.side, 'left');
    assert.equal(manager._live2DGameModeEdgePeekState.headAnchored, true);
    assert.ok(head.x > 20 && head.x < 260, `head should lean inside the edge, got ${head.x}`);
    assert.equal(manager._live2DGameModeEdgePeekState.waistAnchored, true);
    assert.ok(Math.abs(manager.getBodyScreenRectInfo().rect.centerX + 8) < 0.001);
    assert.ok(Math.abs(manager.getBodyScreenRectInfo().rect.bottom - 450) < 0.001);
    const lowerBody = transformedPoint(140, 600);
    assert.ok(lowerBody.x < 0, 'lower body should remain outside the side edge');
    assert.ok(model.x > -500, 'placement must not expose only the tail-side edge of the bounds');
});

test('top corners use 135 degree poses with the body outside above the viewport', async () => {
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

        const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
        flushNextFrame(harness);
        assert.equal(await enterPromise, true, `${item.edge} should enter`);
        assert.equal(manager._live2DGameModeEdgePeekState.edge, item.edge);
        assert.equal(model.rotation, item.rotation * Math.PI / 180);

        if (item.edge.startsWith('top-')) {
            const head = manager.getHeadScreenAnchor();
            const lowerBody = model.transformPoint(150, 560);
            assert.ok(head.y >= 36 && head.y <= 64, `${item.edge} head should sit just below the top edge`);
            assert.ok(lowerBody.y < head.y, `${item.edge} body should stay above the head`);
            assert.ok(lowerBody.y < 0, `${item.edge} body should stay outside above the viewport`);
        } else {
            assert.ok(manager.getBodyScreenRectInfo().rect.bottom > 800, `${item.edge} waist should sit outside the bottom edge`);
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

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    const anchor = harness.window.nekoLive2DGameModeEdgePeek.captureRestoreAnchor();
    assert.equal(anchor.edge, 'top-left');

    manager.clearLive2DGameModeEdgePeek('game-mode-auto');
    model.x = 350;
    model.y = 100;
    const restorePromise = harness.window.nekoLive2DGameModeEdgePeek.restoreAnchor(anchor);
    flushNextFrame(harness);
    assert.equal(await restorePromise, true);
    assert.equal(manager._live2DGameModeEdgePeekState.edge, 'top-left');
    assert.equal(model.rotation, 135 * Math.PI / 180);
});

test('top corner falls back to model transforms when no head anchor is available', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createRotatingModel({ x: 0, y: 0, scaleX: 1 });

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    const estimatedHead = model.transformPoint(150, 600 * 0.24);
    const lowerBody = model.transformPoint(150, 560);
    assert.equal(manager._live2DGameModeEdgePeekState.edge, 'top-left');
    assert.equal(manager._live2DGameModeEdgePeekState.headAnchorSource, 'bounds-fallback');
    assert.ok(estimatedHead.x >= 48 && estimatedHead.x <= 84, `fallback head x should remain visible, got ${estimatedHead.x}`);
    assert.ok(estimatedHead.y >= 36 && estimatedHead.y <= 64, `fallback head y should remain visible, got ${estimatedHead.y}`);
    assert.ok(lowerBody.y < 0, 'fallback should keep the lower body outside above the viewport');
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

        const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
        for (let attempt = 0; attempt < 10 && harness.rafQueue.length === 0; attempt += 1) {
            await Promise.resolve();
        }
        flushNextFrame(harness);
        assert.equal(await enterPromise, true);
        assert.equal(manager._live2DGameModeEdgePeekState.edge, item.edge);
        assert.equal(model.rotation, item.rotation * Math.PI / 180);
    }
});

test('clearing during enter animation prevents stale peek writeback', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0 });

    const promise = manager._tryApplyLive2DGameModeEdgePeek(model);
    assert.equal(manager.isLive2DGameModeEdgePeekActive(), true);
    manager.clearLive2DGameModeEdgePeek('game-mode-disabled');

    flushNextFrame(harness);
    const entered = await promise;

    assert.equal(entered, false);
    assert.equal(manager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-game-mode-edge-peek'), false);
});

test('right edge peek faces inward and restores original transform', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 520, y: 100 });

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    assert.equal(model.x, 890);
    assert.equal(model.y, 100);
    assert.equal(model.rotation, -60 * Math.PI / 180);
    assert.equal(model.scale.x, -1);

    const restorePromise = manager.restoreLive2DGameModeEdgePeek('click-restore');
    flushNextFrame(harness);
    assert.equal(await restorePromise, true);

    assert.equal(manager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(model.x, 520);
    assert.equal(model.y, 100);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-game-mode-edge-peek'), false);
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

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);

    assert.equal(model.x, 970);
    assert.equal(model.rotation, -60 * Math.PI / 180);
    assert.equal(model.scale.x, -1);
    assert.deepEqual(JSON.parse(JSON.stringify(manager._live2DGameModeEdgePeekState.visibleBounds)), {
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

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    assert.equal(model.x, -390);

    manager.clearLive2DGameModeEdgePeek('drag-start', { restore: false });

    assert.equal(manager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(model.x, -390);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
});

test('edge peek only triggers while game mode beta is enabled', async () => {
    const harness = createHarness({ gameModeEnabled: false });
    const manager = new harness.Live2DManager();
    const model = createModel({ x: 0, y: 120 });

    const entered = await manager._tryApplyLive2DGameModeEdgePeek(model);

    assert.equal(entered, false);
    assert.equal(manager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(harness.rafQueue.length, 0);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
});

test('game mode disabled event restores active edge peek to its base position', async () => {
    const harness = createHarness();
    const manager = new harness.Live2DManager();
    harness.window.live2dManager = manager;
    const model = createModel({ x: 0, y: 120 });

    const enterPromise = manager._tryApplyLive2DGameModeEdgePeek(model);
    flushNextFrame(harness);
    assert.equal(await enterPromise, true);
    assert.equal(model.x, -390);

    harness.window.dispatchEvent({
        type: 'neko:game-mode-beta-state',
        detail: { enabled: false }
    });

    assert.equal(manager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(model.x, 0);
    assert.equal(model.y, 120);
    assert.equal(model.rotation, 0);
    assert.equal(model.scale.x, 1);
    assert.equal(harness.bodyClasses.has('neko-live2d-game-mode-edge-peek'), false);
});

test('top and bottom edges alone do not trigger edge peek', async () => {
    const topHarness = createHarness();
    const topManager = new topHarness.Live2DManager();
    const topModel = createModel({ x: 250, y: 0 });

    assert.equal(await topManager._tryApplyLive2DGameModeEdgePeek(topModel), false);
    assert.equal(topManager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(topHarness.rafQueue.length, 0);

    const bottomHarness = createHarness();
    const bottomManager = new bottomHarness.Live2DManager();
    const bottomModel = createModel({ x: 250, y: 200, height: 600 });

    assert.equal(await bottomManager._tryApplyLive2DGameModeEdgePeek(bottomModel), false);
    assert.equal(bottomManager.isLive2DGameModeEdgePeekActive(), false);
    assert.equal(bottomHarness.rafQueue.length, 0);
});

test('visible reveal width is clamped between 96 and 180 pixels', async () => {
    const narrowHarness = createHarness();
    const narrowManager = new narrowHarness.Live2DManager();
    const narrowModel = createModel({ x: 0, width: 300 });

    const narrowPromise = narrowManager._tryApplyLive2DGameModeEdgePeek(narrowModel);
    flushNextFrame(narrowHarness);
    assert.equal(await narrowPromise, true);
    assert.equal(narrowManager._live2DGameModeEdgePeekState.visibleBounds.width, 96);
    assert.equal(narrowModel.x, -204);

    const wideHarness = createHarness();
    const wideManager = new wideHarness.Live2DManager();
    const wideModel = createModel({ x: 0, width: 1200 });

    const widePromise = wideManager._tryApplyLive2DGameModeEdgePeek(wideModel);
    flushNextFrame(wideHarness);
    assert.equal(await widePromise, true);
    assert.equal(wideManager._live2DGameModeEdgePeekState.visibleBounds.width, 180);
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

    manager._live2DGameModeEdgePeekState = {
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
    manager._live2DGameModeEdgePeekState = {
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

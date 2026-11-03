const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');

function dom() {
    class Target {
        constructor() { this.listeners = new Map(); this.style = {}; }
        addEventListener(type, fn) {
            if (!this.listeners.has(type)) this.listeners.set(type, new Set());
            this.listeners.get(type).add(fn);
        }
        removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
        fire(type, event = {}) {
            event.type = type;
            event.preventDefault ||= () => {};
            event.stopPropagation ||= () => {};
            for (const fn of [...(this.listeners.get(type) || [])]) fn(event);
        }
    }
    const window = new Target();
    const document = new Target();
    const canvas = new Target();
    const shakes = [];
    canvas.animate = (keyframes, options) => {
        const animation = {
            keyframes, options, cancelled: false,
            cancel() { this.cancelled = true; this.oncancel?.(); }
        };
        shakes.push(animation);
        return animation;
    };
    canvas.setPointerCapture = () => {};
    canvas.releasePointerCapture = () => {};
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 800 });
    canvas.clientWidth = 1000;
    canvas.clientHeight = 800;
    canvas.ownerDocument = document;
    document.defaultView = window;
    document.body = { classList: { contains: () => false } };
    document.getElementById = () => canvas;
    return { window, document, canvas, shakes };
}

function pointer(pointerId, clientX, clientY = 200, pointerType = 'touch') {
    return { pointerId, pointerType, clientX, clientY, screenX: clientX, screenY: clientY,
        button: 0, preventDefault() {}, stopPropagation() {} };
}

function load(harness, filename, extra = {}) {
    const context = vm.createContext({ ...harness, console, performance, setTimeout, clearTimeout, ...extra });
    vm.runInContext(fs.readFileSync(path.join(root, filename), 'utf8'), context);
    return context;
}

function helper() {
    const h = dom();
    h.window.window = h.window;
    load(h, 'static/avatar/avatar-click-shake.js', { window: h.window });
    return { ...h, shake: h.window.NekoAvatarClickShake };
}

function live2d() {
    const h = dom();
    load(h, 'static/avatar/avatar-click-shake.js');
    load(h, 'static/avatar/avatar-touch-gestures.js');
    class Live2DManager {}
    const context = load(h, 'static/live2d/live2d-interaction.js', { Live2DManager });
    vm.runInContext('clearLive2DPeek = () => {};', context);
    const model = {
        x: 100, y: 100, scale: { x: 1, set(value) { this.x = value; } },
        on() {},
        toLocal(p) { return { x: (p.x - this.x) / this.scale.x, y: (p.y - this.y) / this.scale.x }; },
        toGlobal(p) { return { x: this.x + p.x * this.scale.x, y: this.y + p.y * this.scale.x }; },
        containsPoint: () => true, hitTest: () => ['head']
    };
    h.window.DragHelpers = { disableButtonPointerEvents() {}, restoreButtonPointerEvents() {} };
    const manager = new Live2DManager();
    Object.assign(manager, {
        _isModelReadyForInteraction: true, currentModel: model,
        pixi_app: { view: h.canvas, renderer: { screen: { width: 1000, height: 800 } } },
        isLive2DPeekActive: () => false,
        // 点击 300ms 后的动作/表情分支不在本用例范围内
        _canTriggerTouchSetArea: () => false,
        _savePositionAfterInteraction: async () => {},
        _settleLive2DDragTerminal: async () => {}
    });
    h.window.live2dManager = manager;
    manager.setupDragAndDrop(model);
    manager.setupTouchZoom(model);
    return { ...h, manager, model };
}

async function humanoid(kind) {
    const THREE = await import(pathToFileURL(path.join(root, 'static/libs/three.module.js')).href);
    const h = dom();
    h.window.THREE = THREE;
    load(h, 'static/avatar/avatar-click-shake.js');
    load(h, 'static/avatar/avatar-touch-gestures.js');
    const context = load(h, `static/${kind}/${kind}-interaction.js`);
    const model = new THREE.Object3D();
    const camera = new THREE.PerspectiveCamera(40, 1.25, 0.1, 100);
    camera.position.z = 10;
    camera.updateMatrixWorld();
    const manager = {
        camera, renderer: { domElement: h.canvas }, _isModelReadyForInteraction: true,
        currentModel: { scene: model, mesh: model },
        setModelScaleScalar(value) { model.scale.setScalar(value); }
    };
    const Klass = vm.runInContext(kind === 'vrm' ? 'VRMInteraction' : 'MMDInteraction', context);
    const interaction = new Klass(manager);
    interaction._hitTestModel = () => true;
    interaction.checkLocked = () => false;
    interaction._rememberPanDragPointer = () => {};
    interaction._rememberDragHintPanPointer = () => {};
    interaction._recordDragHintPointerEdgeRelease = async () => {};
    interaction._savePositionAfterInteraction = async () => {};
    interaction._checkAndSwitchDisplay = async () => false;
    interaction._snapModelIntoScreen = async () => false;
    interaction.initDragAndZoom();
    return { ...h, interaction };
}

function mouse(clientX, clientY = 400, button = 0) {
    return { ...pointer(1, clientX, clientY, 'mouse'), button };
}

test('helper shakes horizontally via the translate property and restarts on rapid clicks', () => {
    const h = helper();
    const first = h.shake.play(h.canvas);
    assert.equal(h.shakes.length, 1);
    assert.ok(first.keyframes.every(frame => /^-?\d+(\.\d+)?px 0$/.test(frame.translate)),
        'must not write transform, which would drop the canvas translateZ(0) layer');
    assert.equal(first.keyframes[0].translate, '0px 0');
    assert.equal(first.keyframes.at(-1).translate, '0px 0');
    h.shake.play(h.canvas);
    assert.equal(first.cancelled, true);
    assert.equal(h.shakes.length, 2);
});

test('helper respects the animation settings toggle and missing elements', () => {
    const h = helper();
    h.window.modelClickShakeEnabled = false;
    assert.equal(h.shake.play(h.canvas), null);
    assert.equal(h.shakes.length, 0);
    h.window.modelClickShakeEnabled = true;
    assert.equal(h.shake.play(null), null);
    assert.ok(h.shake.play(h.canvas));
});

test('helper falls back to saved settings on pages without app-settings.js', () => {
    for (const [saved, expected] of [
        [{ modelClickShakeEnabled: false }, false],
        [{ modelClickShakeEnabled: 'false' }, false],
        [{ modelClickShakeEnabled: true }, true],
        [null, true],
        ['{not json', true]
    ]) {
        const h = helper();
        h.window.localStorage = {
            getItem: key => (key === 'project_neko_settings' && saved !== null
                ? (typeof saved === 'string' ? saved : JSON.stringify(saved))
                : null)
        };
        assert.equal(h.shake.isEnabled(), expected, JSON.stringify(saved));
        assert.equal(!!h.shake.play(h.canvas), expected);
        // 本页运行时的取值优先于存储
        h.window.modelClickShakeEnabled = !expected;
        assert.equal(h.shake.isEnabled(), !expected);
    }
});

test('helper skips the shake when the system asks for reduced motion', () => {
    const h = helper();
    let reduce = true;
    h.window.matchMedia = query => ({ matches: reduce && query === '(prefers-reduced-motion: reduce)' });
    assert.equal(h.shake.play(h.canvas), null);
    reduce = false;
    assert.ok(h.shake.play(h.canvas));
});

test('helper tap detection matches the Live2D click thresholds', () => {
    const h = helper();
    const start = h.shake.tapStart({ clientX: 100, clientY: 100 });
    assert.equal(h.shake.isTap(start, { clientX: 106, clientY: 108 }), true);
    assert.equal(h.shake.isTap(start, { clientX: 111, clientY: 100 }), false);
    assert.equal(h.shake.isTap(null, { clientX: 100, clientY: 100 }), false);
    assert.equal(h.shake.isTap({ ...start, time: Date.now() - 300 }, { clientX: 100, clientY: 100 }), false);
});

test('Live2D tap shakes immediately, before the delayed motion; a drag does not', async () => {
    const h = live2d();
    h.canvas.fire('pointerdown', pointer(1, 200));
    h.document.fire('pointerup', pointer(1, 200));
    assert.equal(h.shakes.length, 1, 'shake must not wait for the 300ms motion delay');

    h.canvas.fire('pointerdown', pointer(2, 200));
    h.document.fire('pointermove', pointer(2, 260));
    h.document.fire('pointerup', pointer(2, 260));
    await new Promise(setImmediate);
    assert.equal(h.shakes.length, 1);

    h.window.modelClickShakeEnabled = false;
    h.canvas.fire('pointerdown', pointer(3, 260));
    h.document.fire('pointerup', pointer(3, 260));
    assert.equal(h.shakes.length, 1);
});

test('VRM and MMD shake on a left-click or one-finger tap, not on drag, orbit or pinch', async () => {
    for (const kind of ['vrm', 'mmd']) {
        const h = await humanoid(kind);
        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mouseup', mouse(403));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} click`);

        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mousemove', mouse(460));
        h.document.fire('mouseup', mouse(460));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} drag`);

        // 拖出点击范围再拖回原处，仍是拖拽
        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mousemove', mouse(430));
        h.document.fire('mousemove', mouse(401));
        h.document.fire('mouseup', mouse(401));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} drag out and back`);

        h.canvas.fire('pointerdown', pointer(9, 400, 400));
        h.document.fire('pointermove', pointer(9, 440, 400));
        h.document.fire('pointermove', pointer(9, 402, 400));
        h.document.fire('pointerup', pointer(9, 402, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} touch drag out and back`);

        h.canvas.fire('mousedown', mouse(400, 400, 2));
        h.document.fire('mouseup', mouse(400, 400, 2));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} orbit`);

        h.canvas.fire('pointerdown', pointer(10, 400, 400));
        h.document.fire('pointerup', pointer(10, 400, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 2, `${kind} tap`);

        h.canvas.fire('pointerdown', pointer(11, 400, 400));
        h.canvas.fire('pointerdown', pointer(12, 600, 400));
        h.document.fire('pointerup', pointer(12, 600, 400));
        h.document.fire('pointerup', pointer(11, 400, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 2, `${kind} pinch leftover finger`);

        h.interaction.cleanupDragAndZoom();
    }
});

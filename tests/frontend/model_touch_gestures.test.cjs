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
    const captured = new Set();
    canvas.setPointerCapture = id => captured.add(id);
    canvas.releasePointerCapture = id => captured.delete(id);
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 800 });
    canvas.clientWidth = 1000;
    canvas.clientHeight = 800;
    canvas.ownerDocument = document;
    document.defaultView = window;
    document.body = { classList: { contains: () => false } };
    document.getElementById = () => canvas;
    return { window, document, canvas, captured };
}

function pointer(pointerId, clientX, clientY = 200) {
    return { pointerId, pointerType: 'touch', clientX, clientY, screenX: clientX, screenY: clientY,
        button: 0, preventDefault() {}, stopPropagation() {} };
}

function load(harness, filename, extra = {}) {
    const context = vm.createContext({ ...harness, console, performance, setTimeout, clearTimeout, ...extra });
    vm.runInContext(fs.readFileSync(path.join(root, filename), 'utf8'), context);
    return context;
}

function live2d(extra = {}) {
    const h = dom();
    load(h, 'static/avatar/avatar-touch-gestures.js');
    class Live2DManager {}
    const context = load(h, 'static/live2d/live2d-interaction.js', { Live2DManager, ...extra });
    // Keep the real event handlers and coordinate conversion; exclude unrelated
    // peek presentation and persistence side effects from this gesture fixture.
    vm.runInContext('clearLive2DPeek = () => {};', context);
    const model = {
        x: 100, y: 100, scale: { x: 1, set(value) { this.x = value; } },
        on() {},
        toLocal(p) { return { x: (p.x - this.x) / this.scale.x, y: (p.y - this.y) / this.scale.x }; },
        toGlobal(p) { return { x: this.x + p.x * this.scale.x, y: this.y + p.y * this.scale.x }; },
        containsPoint: () => true, hitTest: () => ['head']
    };
    let saved = 0;
    let buttonsDisabled = false;
    h.window.DragHelpers = {
        disableButtonPointerEvents() { buttonsDisabled = true; },
        restoreButtonPointerEvents() { buttonsDisabled = false; }
    };
    const manager = new Live2DManager();
    Object.assign(manager, {
        _isModelReadyForInteraction: true, currentModel: model,
        pixi_app: { view: h.canvas, renderer: { screen: { width: 1000, height: 800 } } },
        isLive2DPeekActive: () => false,
        _savePositionAfterInteraction: async () => { saved++; },
        _settleLive2DDragTerminal: async () => { saved++; }
    });
    h.window.live2dManager = manager;
    manager.setupDragAndDrop(model);
    manager.setupTouchZoom(model);
    return { ...h, manager, model, saved: () => saved, buttonsDisabled: () => buttonsDisabled };
}

test('Live2D pinch keeps its midpoint anchor and resumes one-finger drag without a jump', async () => {
    const h = live2d();
    h.canvas.fire('pointerdown', pointer(10, 200));
    h.document.fire('pointermove', pointer(10, 220));
    assert.equal(h.model.x, 120);
    h.canvas.fire('pointerdown', pointer(20, 320));
    h.document.fire('pointermove', pointer(20, 370));
    assert.equal(h.model.scale.x, 1.5);
    assert.equal(h.model.x, 70); // Midpoint moved 25px; original local point remains beneath it.
    const pinchX = h.model.x;
    h.window.fire('pointermove', { ...pointer(1, 950), pointerType: 'mouse' });
    assert.equal(h.model.x, pinchX, 'OS cursor must not move a touch-owned model');
    h.document.fire('pointerup', pointer(20, 370));
    h.document.fire('pointermove', pointer(10, 220));
    assert.equal(h.model.x, pinchX);
    h.document.fire('pointermove', pointer(10, 250));
    assert.equal(h.model.x, pinchX + 30);
    h.document.fire('pointerup', pointer(10, 250));
    await new Promise(setImmediate);
    assert.equal(h.saved(), 1);
    assert.equal(h.buttonsDisabled(), false);
    assert.equal(h.captured.size, 0);
});

test('Live2D pinch supersedes an active snap before capturing its anchor', async () => {
    const frames = [];
    const h = live2d({ requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
        cancelAnimationFrame() {}, performance: { now: () => 0 } });
    h.manager._live2DDragGeneration = 4;
    const snap = h.manager._performSnapAnimation(h.model,
        { startX: 100, startY: 100, targetX: 400, targetY: 300 });
    frames.shift()(100);
    const startX = h.model.x;
    h.canvas.fire('pointerdown', pointer(1, startX + 100));
    h.canvas.fire('pointerdown', pointer(2, startX + 200));
    assert.equal(h.manager._live2DDragGeneration, 5);
    assert.equal(h.manager._isSnapping, false);
    h.document.fire('pointermove', pointer(2, startX + 250));
    const anchor = { x: h.model.x, y: h.model.y };
    frames.shift()(300);
    assert.equal(await snap, false);
    assert.deepEqual({ x: h.model.x, y: h.model.y }, anchor, 'stale snap cannot overwrite the pinch');
    h.document.fire('pointerup', pointer(2, startX + 250));
    h.document.fire('pointerup', pointer(1, startX + 100));
    await new Promise(setImmediate);
    assert.equal(h.saved(), 1);
});

test('host-owned Live2D dragging permits pinch scaling without competing position writes', async () => {
    const h = live2d();
    let hostOwns = true;
    h.window.__nekoNiriPetPhysicalCrop = { hostModelDragOwnershipVersion: 1,
        isHostModelDragActive: () => hostOwns };
    h.canvas.fire('pointerdown', pointer(1, 200));
    h.document.fire('pointermove', pointer(1, 220));
    assert.equal(h.model.x, 100, 'single-finger position belongs to the host');
    h.canvas.fire('pointerdown', pointer(2, 320));
    h.document.fire('pointermove', pointer(2, 370));
    assert.equal(h.model.scale.x, 1.5);
    assert.equal(h.model.x, 100, 'pinch cannot overwrite the host position');
    h.document.fire('pointerup', pointer(2, 370));
    h.document.fire('pointerup', pointer(1, 220));
    await new Promise(setImmediate);
    assert.equal(h.saved(), 0, 'the host owns terminal settlement');
    assert.equal(h.buttonsDisabled(), false);
    assert.equal(h.captured.size, 0);
    hostOwns = false;
    h.canvas.fire('pointerdown', pointer(1, 200));
    h.document.fire('pointermove', pointer(1, 220));
    assert.equal(h.model.x, 120, 'ordinary dragging resumes once host ownership ends');
    h.manager._touchGestures.dispose();
});

test('cancellation, lost capture, locking and disposal clear every touch and never save a cancelled pinch', () => {
    for (const reason of ['pointercancel', 'lostpointercapture', 'blur', 'lock', 'dispose']) {
        const h = live2d();
        h.canvas.fire('pointerdown', pointer(10, 200));
        h.canvas.fire('pointerdown', pointer(20, 300));
        h.document.fire('pointermove', pointer(20, 350));
        if (reason === 'blur') h.window.fire('blur');
        else if (reason === 'dispose') h.manager._touchGestures.dispose();
        else if (reason === 'lock') { h.manager.isLocked = true; h.document.fire('pointermove', pointer(10, 190)); }
        else (reason === 'lostpointercapture' ? h.canvas : h.document).fire(reason, pointer(10, 200));
        assert.equal(h.manager._touchGestures.active, false, reason);
        assert.equal(h.manager._isDraggingModel, false, reason);
        assert.equal(h.buttonsDisabled(), false, reason);
        assert.equal(h.captured.size, 0, reason);
        assert.equal(h.saved(), 0, reason);
    }
});

test('coincident contacts and third-finger changes keep a finite scale and stable ownership', () => {
    const h = live2d();
    h.canvas.fire('pointerdown', pointer(10, 200));
    h.canvas.fire('pointerdown', pointer(20, 200));
    h.document.fire('pointermove', pointer(20, 300));
    assert.equal(h.model.scale.x, 1);
    h.document.fire('pointermove', pointer(20, 350));
    assert.equal(h.model.scale.x, 1.5);
    h.canvas.fire('pointerdown', pointer(30, 500));
    h.document.fire('pointerup', pointer(30, 500));
    assert.equal(h.manager._touchGestures.active, true);
    assert.equal(h.model.scale.x, 1.5);
    h.manager._touchGestures.dispose();
});

test('unowned touch and mouse pointers cannot move or release an active drag', () => {
    const h = live2d();
    h.canvas.fire('pointerdown', pointer(1, 200));
    h.document.fire('pointermove', pointer(1, 220));
    h.window.fire('pointermove', pointer(99, 900));
    h.window.fire('pointerup', pointer(99, 900));
    h.document.fire('pointermove', { ...pointer(1, 950), pointerType: 'mouse' });
    assert.equal(h.model.x, 120);
    assert.equal(h.manager._isDraggingModel, true);
    h.document.fire('pointerup', pointer(1, 250));
    assert.equal(h.model.x, 150, 'release includes the final contact displacement');
});

test('peek mode allows the drag press but rejects pinch scaling and persistence', () => {
    const h = live2d();
    let peek = true;
    h.manager.isLive2DPeekActive = () => peek;
    h.canvas.fire('pointerdown', pointer(1, 200));
    assert.equal(h.manager._touchGestures.active, true);
    assert.equal(h.manager._isDraggingModel, true, 'single-finger peek drag remains available');
    h.canvas.fire('pointerdown', pointer(2, 300));
    h.document.fire('pointermove', pointer(2, 350));
    assert.equal(h.model.scale.x, 1);
    h.document.fire('pointerup', pointer(1, 200));
    h.document.fire('pointerup', pointer(2, 350));
    assert.equal(h.saved(), 0);
    assert.equal(h.buttonsDisabled(), false);
});

test('VRM and MMD adapters use model scaling and preserve the projected pinch anchor', async () => {
    const THREE = await import(pathToFileURL(path.join(root, 'static/libs/three.module.js')).href);
    for (const kind of ['vrm', 'mmd']) {
        const h = dom();
        h.window.THREE = THREE;
        load(h, 'static/avatar/avatar-touch-gestures.js');
        const context = load(h, `static/${kind}/${kind}-interaction.js`);
        const model = new THREE.Object3D();
        const camera = new THREE.PerspectiveCamera(40, 1.25, 0.1, 100);
        camera.position.z = 10;
        camera.updateMatrixWorld();
        let saved = 0;
        let scaleCalls = 0;
        const manager = {
            camera, renderer: { domElement: h.canvas }, _isModelReadyForInteraction: true,
            currentModel: { scene: model, mesh: model },
            setModelScaleScalar(value) { scaleCalls++; model.scale.setScalar(value); }
        };
        const Klass = vm.runInContext(kind === 'vrm' ? 'VRMInteraction' : 'MMDInteraction', context);
        const interaction = new Klass(manager);
        interaction._hitTestModel = () => true;
        interaction.checkLocked = () => false;
        interaction._rememberPanDragPointer = () => {};
        interaction._rememberDragHintPanPointer = () => {};
        interaction._savePositionAfterInteraction = async () => { saved++; };
        interaction._checkAndSwitchDisplay = async () => false;
        interaction._snapModelIntoScreen = async () => false;
        interaction.initDragAndZoom();
        h.canvas.fire('pointerdown', pointer(10, 400, 400));
        h.canvas.fire('pointerdown', pointer(20, 600, 400));
        h.document.fire('pointermove', pointer(10, 300, 400));
        h.document.fire('pointermove', pointer(20, 700, 400));
        assert.equal(model.scale.x, 2, kind);
        assert.ok(Math.abs(model.position.x) < 1e-10, `${kind} midpoint should stay fixed`);
        if (kind === 'vrm') assert.equal(scaleCalls, 2, 'VRM collider-aware scale API');
        h.document.fire('pointerup', pointer(10, 300, 400));
        h.document.fire('pointerup', pointer(20, 700, 400));
        await new Promise(setImmediate);
        assert.equal(saved, 1, kind);
        interaction.cleanupDragAndZoom();
        assert.equal(h.captured.size, 0);
    }
});

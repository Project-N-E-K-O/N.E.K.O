// VRM/MMD 与桌面宿主的两条约定：
// 1. 宿主通过 window.__nekoNiriPetPhysicalCrop 声明接管拖动时，平移拖动和双指捏合不写模型坐标；
// 2. 画布尺寸和像素比都没变时，resize 不重设画布（重设会清空画布缓冲区）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');

function createEventTarget() {
    const listeners = new Map();
    return {
        listeners,
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(handler);
        },
        removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
        emit(type, event) {
            for (const handler of [...(listeners.get(type) || [])]) handler({ type, ...event });
        }
    };
}

async function loadThree() {
    const source = fs.readFileSync(path.join(root, 'static/libs/three.core.js')).toString('base64');
    return import('data:text/javascript;base64,' + source);
}

function createContext(THREE) {
    const document = { ...createEventTarget(), body: { classList: { contains: () => false } } };
    const window = { THREE, ...createEventTarget(), document,
        NekoModelTouchGestures: { installThree: () => ({ dispose() {} }) } };
    document.defaultView = window;
    const context = vm.createContext({ window, document, console, THREE,
        performance: { now: () => 0 }, requestAnimationFrame: () => 0, cancelAnimationFrame() {},
        setTimeout, clearTimeout });
    return { context, window, document };
}

function hostCrop(active) {
    return Object.freeze({ hostModelDragOwnershipVersion: 1, isHostModelDragActive: () => active });
}

// 一个能被 three.js 的 setSize/setPixelRatio 语义驱动的渲染器替身，记下每次重设画布。
function createRenderer(THREE, { width = 1920, height = 1080, pixelRatio = 1 } = {}) {
    const canvas = { ...createEventTarget(), style: { width: `${width}px`, height: `${height}px` },
        width: Math.floor(width * pixelRatio), height: Math.floor(height * pixelRatio),
        clientWidth: width, clientHeight: height,
        getBoundingClientRect: () => ({ left: 0, top: 0, width, height }) };
    const state = { width, height, pixelRatio, setSizeCalls: 0, setPixelRatioCalls: 0 };
    const renderer = {
        domElement: canvas,
        getSize: target => target.set(state.width, state.height),
        getPixelRatio: () => state.pixelRatio,
        setPixelRatio(value) {
            state.setPixelRatioCalls += 1;
            state.pixelRatio = value;
            renderer.setSize(state.width, state.height, false);
        },
        setSize(w, h, updateStyle = true) {
            state.setSizeCalls += 1;
            state.width = w;
            state.height = h;
            canvas.width = Math.floor(w * state.pixelRatio);
            canvas.height = Math.floor(h * state.pixelRatio);
            if (updateStyle) {
                canvas.style.width = `${w}px`;
                canvas.style.height = `${h}px`;
            }
        }
    };
    return { renderer, canvas, state };
}

test('VRM 平移拖动：宿主接管时不写模型坐标，指针照常记录；未接管或旧宿主时照常移动', async () => {
    const THREE = await loadThree();
    const { context, window } = createContext(THREE);
    for (const file of ['vrm-orientation.js', 'vrm-interaction.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'static/vrm', file), 'utf8'), context);
    }
    function drag(crop) {
        window.__nekoNiriPetPhysicalCrop = crop;
        const scene = new THREE.Object3D();
        const camera = new THREE.PerspectiveCamera(30, 16 / 9, 0.1, 100);
        camera.position.z = 5;
        const { renderer } = createRenderer(THREE);
        const manager = { camera, renderer, currentModel: { scene, vrm: {} },
            _isModelReadyForInteraction: true, isLocked: false };
        const interaction = new window.VRMInteraction(manager);
        assert.equal(interaction.hostModelDragOwnershipVersion, 1);
        interaction.clampModelPosition = position => position;
        interaction._hitTestModel = () => true;
        interaction.initDragAndZoom();
        const event = (x, y) => ({ button: 0, clientX: x, clientY: y, screenX: 1000 + x, screenY: 500 + y,
            preventDefault() {}, stopPropagation() {} });
        interaction.mouseDownHandler(event(100, 100));
        interaction.dragHandler(event(160, 130));
        return { scene, interaction };
    }

    const owned = drag(hostCrop(true));
    assert.deepEqual(owned.scene.position.toArray(), [0, 0, 0], '宿主接管：模型不动');
    // vm 里建的对象原型不同，按字段比。
    assert.deepEqual({ ...owned.interaction._lastPanDragPointerScreen }, { x: 1160, y: 630 }, '跨屏判定用的指针照常记');
    assert.deepEqual({ ...owned.interaction.previousMousePosition }, { x: 160, y: 130 });

    for (const [label, crop] of [
        ['没有宿主', undefined],
        ['宿主没接管这次按下', hostCrop(false)],
        ['旧宿主没声明接管能力', Object.freeze({ isHostModelDragActive: () => true })]
    ]) {
        const free = drag(crop);
        assert.ok(free.scene.position.x > 0 && free.scene.position.y < 0, `${label}：模型跟着指针走`);
    }
});

test('MMD 平移拖动：宿主接管时不写模型坐标，未接管时照常移动', async () => {
    const THREE = await loadThree();
    const { context, window } = createContext(THREE);
    vm.runInContext(fs.readFileSync(path.join(root, 'static/mmd/mmd-interaction.js'), 'utf8'), context);
    const MMDInteraction = vm.runInContext('MMDInteraction', context);
    function drag(crop) {
        window.__nekoNiriPetPhysicalCrop = crop;
        const mesh = new THREE.Object3D();
        const camera = new THREE.PerspectiveCamera(30, 16 / 9, 0.1, 100);
        camera.position.z = 5;
        const { renderer } = createRenderer(THREE);
        const manager = { camera, renderer, currentModel: { mesh }, _isModelReadyForInteraction: true };
        const interaction = new MMDInteraction(manager);
        assert.equal(interaction.hostModelDragOwnershipVersion, 1);
        interaction.checkLocked = () => false;
        interaction._hitTestModel = () => true;
        interaction.initDragAndZoom();
        const event = (x, y) => ({ button: 0, clientX: x, clientY: y, screenX: 1000 + x, screenY: 500 + y,
            preventDefault() {}, stopPropagation() {} });
        interaction.mouseDownHandler(event(100, 100));
        interaction.dragHandler(event(160, 130));
        return { mesh, interaction };
    }
    const owned = drag(hostCrop(true));
    assert.deepEqual(owned.mesh.position.toArray(), [0, 0, 0]);
    assert.deepEqual({ ...owned.interaction._lastPanDragPointerScreen }, { x: 1160, y: 630 });
    const free = drag(hostCrop(false));
    assert.ok(free.mesh.position.x > 0 && free.mesh.position.y < 0);
});

test('三维模型双指捏合：宿主接管时只缩放不挪位置，未接管时按捏合中心挪', async () => {
    const THREE = await loadThree();
    const { context, window, document } = createContext(THREE);
    vm.runInContext(fs.readFileSync(path.join(root, 'static/avatar/avatar-touch-gestures.js'), 'utf8'), context);
    const gestures = window.NekoModelTouchGestures;
    function pinch(hostActive) {
        const { renderer, canvas } = createRenderer(THREE, { width: 800, height: 600 });
        canvas.ownerDocument = document;
        canvas.setPointerCapture = () => {};
        canvas.releasePointerCapture = () => {};
        const camera = new THREE.PerspectiveCamera(30, 800 / 600, 0.1, 100);
        camera.position.z = 5;
        const model = new THREE.Object3D();
        model.position.set(0.5, 0, 0);
        const interaction = { manager: { renderer, camera }, _hitTestModel: () => true,
            _rememberPanDragPointer() {}, _rememberDragHintPanPointer() {},
            _disableButtonPointerEvents() {}, _restoreButtonPointerEvents() {}, dragHandler() {} };
        const handle = gestures.installThree(interaction, {
            getModel: () => model,
            setScale: scale => model.scale.setScalar(scale),
            enabled: () => true,
            isHostModelDragActive: () => hostActive
        });
        const touch = (pointerId, clientX, clientY) => ({ pointerId, pointerType: 'touch', clientX, clientY,
            preventDefault() {}, stopPropagation() {} });
        canvas.emit('pointerdown', touch(1, 300, 300));
        canvas.emit('pointerdown', touch(2, 500, 300));
        document.emit('pointermove', touch(2, 600, 300));
        handle.dispose();
        return model;
    }
    const owned = pinch(true);
    assert.ok(owned.scale.x > 1, '照常缩放');
    assert.deepEqual(owned.position.toArray(), [0.5, 0, 0], '宿主接管：位置不动');
    const free = pinch(false);
    assert.ok(free.scale.x > 1);
    assert.notDeepEqual(free.position.toArray(), [0.5, 0, 0], '未接管：按捏合中心挪');
});

test('VRM resize：尺寸和像素比都没变时不重设画布，变了照常重设', async () => {
    const THREE = await loadThree();
    const { context, window } = createContext(THREE);
    window.renderQuality = 'medium';
    window.__NEKO_MULTI_WINDOW__ = true;
    window.screen = { width: 1920, height: 1080 };
    window.innerWidth = 1920;
    window.innerHeight = 1080;
    for (const file of ['vrm-core.js', 'vrm-manager.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'static/vrm', file), 'utf8'), context);
    }
    const VRMCore = vm.runInContext('VRMCore', context);
    const camera = new THREE.PerspectiveCamera(30, 1920 / 1080, 0.1, 100);
    const { renderer, state } = createRenderer(THREE);
    const container = { clientWidth: 1920, clientHeight: 1080 };
    const manager = Object.assign(Object.create(window.VRMManager.prototype), { camera, renderer, container });
    const core = new VRMCore(manager);
    manager.core = core;

    core.syncRendererPixelRatio('resize');
    manager.onWindowResize();
    assert.equal(state.setPixelRatioCalls, 0, '像素比没变：不调 setPixelRatio（它会连带重设画布）');
    assert.equal(state.setSizeCalls, 0, '尺寸没变：不重设画布');

    container.clientWidth = 1280;
    manager.onWindowResize();
    assert.equal(state.setSizeCalls, 1, '尺寸变了：重设');
    assert.equal(camera.aspect, 1280 / 1080);

    window.renderQuality = 'low';
    core.syncRendererPixelRatio('resize');
    assert.equal(state.setPixelRatioCalls, 1, '像素比变了：照常设');
    assert.equal(state.pixelRatio, 0.8);
    manager.onWindowResize();
    assert.equal(state.setSizeCalls, 2, 'setPixelRatio 已按新像素比重设过，这次尺寸没变不再重设');

    // 非多窗口（网页版）分支同样只在尺寸变了时重设。
    window.__NEKO_MULTI_WINDOW__ = false;
    manager.onWindowResize();
    assert.equal(state.setSizeCalls, 2, '网页版分支：尺寸没变不重设');
    container.clientHeight = 720;
    manager.onWindowResize();
    assert.equal(state.setSizeCalls, 3, '网页版分支：尺寸变了重设');
    assert.equal(camera.aspect, 1280 / 720);
});

test('MMD resize：尺寸和像素比都没变时不重设画布和描边效果，变了照常重设', async () => {
    const THREE = await loadThree();
    const { context, window } = createContext(THREE);
    window.renderQuality = 'medium';
    window.innerWidth = 1920;
    window.innerHeight = 1080;
    vm.runInContext(fs.readFileSync(path.join(root, 'static/mmd/mmd-core.js'), 'utf8'), context);
    const MMDCore = vm.runInContext('MMDCore', context);
    const camera = new THREE.PerspectiveCamera(30, 1920 / 1080, 0.1, 100);
    const { renderer, state } = createRenderer(THREE);
    let effectSetSizeCalls = 0;
    const manager = { camera, renderer, container: { clientWidth: 1920, clientHeight: 1080 },
        effect: { setSize: () => { effectSetSizeCalls += 1; } } };
    const core = new MMDCore(manager);

    core.onWindowResize('resize');
    assert.equal(state.setPixelRatioCalls, 0);
    assert.equal(state.setSizeCalls, 0);
    assert.equal(effectSetSizeCalls, 0);

    manager.container.clientHeight = 900;
    core.onWindowResize('resize');
    assert.equal(state.setSizeCalls, 1);
    assert.equal(effectSetSizeCalls, 1);
    assert.equal(camera.aspect, 1920 / 900);

    window.renderQuality = 'low';
    core.onWindowResize('neko-render-quality-changed');
    assert.equal(state.setPixelRatioCalls, 1);
    assert.equal(state.pixelRatio, 0.8);
    assert.equal(state.setSizeCalls, 2, '只有 setPixelRatio 那一次');
});

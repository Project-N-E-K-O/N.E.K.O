const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const surfaceSource = read('static/app/app-ui/surface-floating-controls.js');
const threePromise = import('data:text/javascript;base64,'
    + Buffer.from(read('static/libs/three.core.js')).toString('base64'));

function between(source, start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `missing source boundary: ${start}`);
    return source.slice(from, to);
}

async function harness(type, { cropX = 0, cropY = 0, settleBounds = false } = {}) {
    const THREE = await threePromise;
    const elements = new Map();
    const window = {
        THREE, innerWidth: 1280, innerHeight: 720, appUi: {},
        __appUiParts: { mod: {} }, addEventListener() {},
    };
    if (cropX || cropY) {
        window.__nekoNiriPetPhysicalCrop = {
            toVirtualRect: rect => ({
                left: rect.x + cropX, top: rect.y + cropY, width: rect.width, height: rect.height,
            }),
            getState: () => ({ enabled: true, virtualBounds: { width: 1280, height: 720 } }),
        };
    }
    const document = {
        documentElement: { clientWidth: 1280, clientHeight: 720 },
        getElementById: id => elements.get(id) || null,
    };
    let frameTime = 0;
    let frameId = 0;
    const frames = new Set();
    const context = vm.createContext({
        window, document, console: { ...console, log() {} }, clearTimeout, setTimeout,
        performance: { now: () => frameTime },
        requestAnimationFrame(callback) {
            const id = ++frameId;
            frames.add(id);
            queueMicrotask(() => {
                if (frames.delete(id)) callback(frameTime += 300);
            });
            return id;
        },
        cancelAnimationFrame: id => frames.delete(id),
    });
    vm.runInContext(read('static/app/app-ui/return-transitions.js'), context);
    const I = window.__appUiParts;
    I.applyGoodbyeIdleAppearanceToReturnButton = () => {};
    I.scheduleIdleReturnBallDesktopBridge = () => {};
    I.clearNekoIdleCat1EdgePeek = () => {};

    const managerClass = type === 'vrm' ? 'VRMManager' : 'MMDManager';
    vm.runInContext(read(`static/${type}/${type}-manager.js`), context);
    const uiSource = read(`static/${type}/${type}-ui-buttons.js`);
    vm.runInContext(uiSource.slice(uiSource.indexOf(`${managerClass}.prototype.applyScreenDelta`)), context);
    vm.runInContext(read(`static/${type}/${type}-interaction.js`), context);
    const manager = Object.create(window[managerClass].prototype);
    const scene = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.8, 0.25));
    scene.scale.setScalar(6);
    scene.position.set(-2.5, -4, 0);
    manager.currentModel = { scene, mesh: scene, vrm: { scene } };
    manager.camera = new THREE.PerspectiveCamera(40, 1280 / 720, 0.1, 100);
    manager.camera.position.set(0, 1, 4);
    manager.camera.lookAt(0, 1, 0);
    manager.camera.updateMatrixWorld(true);
    manager.renderer = { domElement: { getBoundingClientRect: () => ({
        left: -cropX, top: -cropY, width: 1280, height: 720,
    }) } };
    window[`${type}Manager`] = manager;
    const Interaction = vm.runInContext(type === 'vrm' ? 'VRMInteraction' : 'MMDInteraction', context);
    manager.interaction = new Interaction(manager);
    const savedPositions = [];
    manager.interaction._savePositionAfterInteraction = async () => savedPositions.push(scene.position.clone());
    elements.set(`${type}-container`, {
        style: { display: 'block', visibility: 'visible' }, classList: { contains: () => false },
    });
    const appliedDeltas = [];
    const applyScreenDelta = manager.applyScreenDelta.bind(manager);
    manager.applyScreenDelta = (...args) => { appliedDeltas.push(args); applyScreenDelta(...args); };

    const attributes = new Map();
    const container = {
        style: { removeProperty(name) { delete this[name]; } },
        offsetWidth: 64, offsetHeight: 64,
        setAttribute: (name, value) => attributes.set(name, value),
        removeAttribute: name => attributes.delete(name),
        querySelector: () => null,
        getBoundingClientRect() {
            // Model the entrance transform so capturing during the animation
            // would incorrectly add another 8px to the return origin.
            const translateY = this.style.transform?.includes('translate3d') ? 8 : 0;
            return {
                left: parseFloat(this.style.left) - cropX,
                top: parseFloat(this.style.top) - cropY + translateY,
                width: 64, height: 64,
            };
        },
    };
    elements.set(`${type}-return-button-container`, container);

    const savedRect = I.toNekoVirtualTransitionRect(manager.getModelScreenBounds());
    assert.ok(savedRect.top < 720 && savedRect.bottom > 0, 'enlarged model is initially visible');
    assert.ok(savedRect.top + savedRect.height / 2 > 720, 'model center is outside the viewport');
    window._savedGoodbyeRect = savedRect;
    const goodbyePlacement = between(surfaceSource,
        '// 显示独立的"请她回来"按钮',
        'I.ensureMultiWindowReturnBallDrag(activeReturnButtonContainer);');
    const place = () => vm.runInContext(`{
        const I = window.__appUiParts;
        const isMmdActive = ${type === 'mmd'};
        const isVrmActive = ${type === 'vrm'};
        const isPngtuberActive = false;
        const savedGoodbyeRect = window._savedGoodbyeRect;
        const live2DPeekEdgeAnchor = null;
        ${goodbyePlacement}
    }`, context);
    const returnMovement = between(surfaceSource,
        '// 如果返回按钮被拖拽到新位置',
        '// 恢复 VRM canvas 的可见性');
    let moved;
    I.showCurrentModel = async () => true;
    const settleReturnedModelBounds = I.settleReturnedModelBounds;
    I.settleReturnedModelBounds = async value => {
        moved = value;
        if (settleBounds) await settleReturnedModelBounds(value);
    };
    const returnModel = (rect) => {
        context.event = { detail: rect ? { returnButtonRect: rect } : {} };
        return vm.runInContext(`(async () => {
            const I = window.__appUiParts;
            const isReturningToPngtuber = false;
            ${returnMovement}
        })()`, context);
    };
    const rect = () => I.toNekoVirtualTransitionRect(container.getBoundingClientRect());
    return { THREE, window, I, manager, scene, container, place, returnModel, rect, appliedDeltas,
        savedPositions, moved: () => moved };
}

function assertVisible(h) {
    const bounds = h.I.toNekoVirtualTransitionRect(h.manager.getModelScreenBounds());
    const width = Math.min(1280, bounds.right) - Math.max(0, bounds.left);
    const height = Math.min(720, bounds.bottom) - Math.max(0, bounds.top);
    assert.ok(width >= 199.99 && height >= 199.99,
        `returned model must be visible: width=${width}, height=${height}`);
}

for (const type of ['vrm', 'mmd']) {
    for (const originZ of [6, 4, 3.95]) {
        for (const parented of [false, true]) {
            test(`${type}: visible body follows the cat when its origin is behind or inside the near plane (z=${originZ}, parent=${parented})`, async () => {
                const h = await harness(type);
                const root = new h.THREE.Group();
                root.scale.setScalar(6);
                const parent = new h.THREE.Group();
                if (parented) {
                    parent.position.set(-3, -2, 1);
                    parent.rotation.set(0.2, 0.5, 0.1);
                    parent.scale.set(1.2, 0.9, 0.8);
                    parent.add(root);
                }
                const origin = new h.THREE.Vector3(0, 1, originZ);
                root.position.copy(parented ? parent.worldToLocal(origin) : origin);
                const body = new h.THREE.Mesh(new h.THREE.BoxGeometry(0.6, 1.8, 0.25));
                root.add(body);
                body.position.copy(root.worldToLocal(new h.THREE.Vector3(0, 1, -6)));
                h.manager.currentModel = { scene: root, mesh: root, vrm: { scene: root } };
                root.updateMatrixWorld(true);
                const raycaster = new h.THREE.Raycaster();
                raycaster.setFromCamera(new h.THREE.Vector2(0, 0), h.manager.camera);
                assert.ok(raycaster.intersectObject(root, true).some(hit => {
                    const depth = -hit.point.clone().applyMatrix4(h.manager.camera.matrixWorldInverse).z;
                    return depth >= h.manager.camera.near && depth <= h.manager.camera.far;
                }), 'the body is visible even though the model origin cannot be projected safely');
                const start = root.position.clone();
                const scale = root.scale.clone();
                const rotation = root.quaternion.clone();
                const screenStart = body.getWorldPosition(new h.THREE.Vector3()).project(h.manager.camera);
                h.manager.applyScreenDelta(120, -80, { clamp: false });
                const screenEnd = body.getWorldPosition(new h.THREE.Vector3()).project(h.manager.camera);
                assert.ok(Math.abs((screenEnd.x - screenStart.x) * 640 - 120) < 1e-6);
                assert.ok(Math.abs((screenStart.y - screenEnd.y) * 360 + 80) < 1e-6);
                h.manager.applyScreenDelta(-120, 80, { clamp: false });
                assert.ok(root.position.distanceTo(start) < 1e-8, 'moving the cat back restores the original position');
                assert.ok(root.scale.equals(scale));
                assert.ok(root.quaternion.equals(rotation));
            });
        }
    }

    test(`${type}: a model wholly behind the camera does not produce an invalid return displacement`, async () => {
        const h = await harness(type);
        h.scene.position.z = 1000;
        const start = h.scene.position.clone();
        h.manager.applyScreenDelta(120, -80, { clamp: false });
        assert.ok(h.scene.position.equals(start));
        assert.ok([h.scene.position.x, h.scene.position.y, h.scene.position.z].every(Number.isFinite));
    });

    test(`${type}: cat movement preserves screen distance with a rotated camera and transformed model parent`, async () => {
        const h = await harness(type);
        const parent = new h.THREE.Group();
        parent.position.set(-3, -2, 1);
        parent.rotation.set(0.2, 0.5, 0.1);
        parent.scale.set(1.2, 0.9, 0.8);
        parent.add(h.scene);
        h.manager.camera.position.set(3, 2, 6);
        h.manager.camera.lookAt(-1, 1, 0);
        h.manager.camera.updateMatrixWorld(true);
        const start = h.scene.position.clone();
        const screenStart = h.scene.getWorldPosition(new h.THREE.Vector3()).project(h.manager.camera);
        h.manager.applyScreenDelta(500, 200, { clamp: false });
        const screenEnd = h.scene.getWorldPosition(new h.THREE.Vector3()).project(h.manager.camera);
        assert.ok(Math.abs((screenEnd.x - screenStart.x) * 640 - 500) < 1e-6);
        assert.ok(Math.abs((screenStart.y - screenEnd.y) * 360 - 200) < 1e-6);
        h.manager.applyScreenDelta(-500, -200, { clamp: false });
        assert.ok(h.scene.position.distanceTo(start) < 1e-8);
    });

    for (const hidden of [false, true]) {
        test(`${type}: cat movement is reversible when the model is far off axis (hidden=${hidden})`, async () => {
            const h = await harness(type);
            h.scene.position.set(-8, -12, 0);
            h.manager.camera.zoom = 1.4;
            h.manager.camera.updateProjectionMatrix();
            if (hidden) h.manager.renderer.domElement.getBoundingClientRect = () => ({ width: 0, height: 0 });
            const start = h.scene.position.clone();
            const screenStart = start.clone().project(h.manager.camera);
            h.manager.applyScreenDelta(900, -500, { clamp: false });
            const screenEnd = h.scene.position.clone().project(h.manager.camera);
            assert.ok(Math.abs((screenEnd.x - screenStart.x) * 640 - 900) < 1e-6);
            assert.ok(Math.abs((screenStart.y - screenEnd.y) * 360 + 500) < 1e-6);
            h.manager.applyScreenDelta(-900, 500, { clamp: false });
            assert.ok(h.scene.position.distanceTo(start) < 1e-8, 'dragging back must restore the model position');
        });
    }

    test(`${type}: an enlarged model rebounds from far outside without crossing the opposite screen edge`, async () => {
        const h = await harness(type);
        for (const [x, y] of [[100, 80], [-100, -80], [100, -80], [-100, 80]]) {
            h.scene.position.set(x, y, 0);
            assert.equal(await h.manager.interaction._snapModelIntoScreen({ animate: false }), true);
            assertVisible(h);
            const settled = h.scene.position.clone();
            assert.equal(await h.manager.interaction._snapModelIntoScreen({ animate: false }), false);
            assert.ok(h.scene.position.equals(settled), 'a settled model must not keep bouncing between edges');
        }
    });

    test(`${type}: a model crossing the camera plane is pulled into view using its front-facing extent`, async () => {
        const h = await harness(type);
        h.scene.scale.setScalar(48);
        h.scene.position.set(100, 1, 0);
        assert.equal(await h.manager.interaction._snapModelIntoScreen({ animate: false }), true);
        h.scene.updateMatrixWorld(true);
        const raycaster = new h.THREE.Raycaster();
        let visiblePixels = 0;
        for (let x = 32; x < 1280; x += 64) {
            for (let y = 36; y < 720; y += 72) {
                raycaster.setFromCamera(new h.THREE.Vector2(x / 640 - 1, 1 - y / 360), h.manager.camera);
                if (raycaster.intersectObject(h.scene).some(hit => {
                    const depth = -hit.point.clone().applyMatrix4(h.manager.camera.matrixWorldInverse).z;
                    return depth >= h.manager.camera.near && depth <= h.manager.camera.far;
                })) visiblePixels++;
            }
        }
        assert.ok(visiblePixels >= 20, `returned model should cover visible samples, got ${visiblePixels}`);
    });

    for (const crop of [{ cropX: 0, cropY: 0 }, { cropX: 320, cropY: 180 }]) {
        test(`${type}: repeatedly dragging the cat across the screen and expanding keeps the model visible (crop=${crop.cropX})`, async () => {
            const h = await harness(type, { ...crop, settleBounds: true });
            h.scene.scale.setScalar(24);
            h.scene.position.set(-5, -19, 0);
            const scale = h.scene.scale.clone();
            const rotation = h.scene.quaternion.clone();
            for (const [left, top] of [[1200, 600], [0, 0], [1200, 0], [0, 600], [600, 300], [0, 0]]) {
                h.window._savedGoodbyeRect = h.I.toNekoVirtualTransitionRect(h.manager.getModelScreenBounds());
                h.place();
                h.container.style.transform = 'none';
                await h.returnModel({ ...h.rect(), left, top });
                assertVisible(h);
                assert.ok(h.scene.scale.equals(scale), 'return must preserve user scale');
                assert.ok(h.scene.quaternion.equals(rotation), 'return must preserve user rotation');
                assert.ok(h.savedPositions.at(-1).distanceTo(h.scene.position) < 1e-8,
                    'only the final visible position should be persisted');
            }
        });
    }

    for (const crop of [{ cropX: 0, cropY: 0 }, { cropX: 320, cropY: 180 }]) {
        const mode = crop.cropX ? 'Electron cropped viewport' : 'web viewport';
        test(`${type}: enlarged model keeps its position on return in ${mode}`, async () => {
            const h = await harness(type, crop);
            const position = h.scene.position.clone();
            const scale = h.scene.scale.clone();
            h.place();
            h.container.style.transform = 'none';
            await h.returnModel(h.rect());
            assert.deepEqual(h.appliedDeltas, [], 'automatic return-button clamping is not a model drag');
            assert.ok(h.scene.position.equals(position));
            assert.ok(h.scene.scale.equals(scale));
            assert.equal(h.moved(), false);
        });

        test(`${type}: only return-button movement offsets the model in ${mode}`, async () => {
            const h = await harness(type, crop);
            h.place();
            h.container.style.transform = 'none';
            const initial = h.rect();
            await h.returnModel({ ...initial, left: initial.left + 120, top: initial.top - 80 });
            assert.equal(h.appliedDeltas.length, 1);
            assert.equal(h.appliedDeltas[0][0], 120);
            assert.equal(h.appliedDeltas[0][1], -80);
            assert.equal(h.appliedDeltas[0][2].clamp, false);
            assert.equal(h.moved(), true);
        });
    }

    test(`${type}: each goodbye refreshes the return origin`, async () => {
        const h = await harness(type);
        h.place();
        h.container.style.transform = 'none';
        await h.returnModel(h.rect());
        h.scene.position.set(0, 1, 0);
        h.window._savedGoodbyeRect = h.manager.getModelScreenBounds();
        h.place();
        h.container.style.transform = 'none';
        h.appliedDeltas.length = 0;
        await h.returnModel(h.rect());
        assert.deepEqual(h.appliedDeltas, []);
    });

    test(`${type}: programmatic return without button geometry preserves position`, async () => {
        const h = await harness(type);
        h.place();
        const position = h.scene.position.clone();
        await h.returnModel();
        assert.deepEqual(h.appliedDeltas, []);
        assert.ok(h.scene.position.equals(position));
        assert.equal(h.moved(), false);
    });

    test(`${type}: re-showing a blocked return button preserves its original movement baseline`, async () => {
        const h = await harness(type);
        h.place();
        h.container.style.transform = 'none';
        const initial = h.rect();
        const moved = { ...initial, left: initial.left + 60, top: initial.top - 100 };
        h.I.showReturnBallContainer(h.container, moved, { deferReveal: true });
        h.container.style.transform = 'none';
        await h.returnModel(h.rect());
        assert.equal(h.appliedDeltas.length, 1);
        assert.equal(h.appliedDeltas[0][0], 60);
        assert.equal(h.appliedDeltas[0][1], -100);
    });
}

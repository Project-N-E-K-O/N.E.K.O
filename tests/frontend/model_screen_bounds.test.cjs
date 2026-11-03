const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const threePromise = import('data:text/javascript;base64,'
    + Buffer.from(read('static/libs/three.core.js')).toString('base64'));

async function harness(type, offset = { left: 0, top: 0 }) {
    const THREE = await threePromise;
    let now = 1000;
    let rect = { ...offset, width: 1280, height: 720 };
    const window = { THREE, addEventListener() {} };
    const context = vm.createContext({ window, console, performance: { now: () => now } });
    vm.runInContext(read(`static/${type}/${type}-manager.js`), context);
    vm.runInContext(read(`static/${type}/${type}-interaction.js`), context);
    const Manager = window[type === 'vrm' ? 'VRMManager' : 'MMDManager'];
    const Interaction = vm.runInContext(type === 'vrm' ? 'VRMInteraction' : 'MMDInteraction', context);
    const manager = Object.create(Manager.prototype);
    const scene = new THREE.Group();
    const face = new THREE.Mesh(new THREE.PlaneGeometry(0.3, 0.3), new THREE.MeshBasicMaterial());
    face.position.z = 0.65;
    scene.add(face);
    // The projected face remains visible while other parts of the model make
    // its world-space bounding box extend through and behind the camera.
    for (const z of [-3, 3]) {
        const extremity = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, 0.05));
        extremity.position.z = z;
        scene.add(extremity);
    }
    manager.currentModel = { scene, mesh: scene, vrm: { scene } };
    manager.camera = new THREE.PerspectiveCamera(40, 1280 / 720, 0.1, 100);
    manager.camera.position.z = 1.5;
    manager.camera.updateMatrixWorld(true);
    manager.renderer = { domElement: { getBoundingClientRect: () => rect } };
    manager._externalCameraFraming = true;
    manager.interaction = new Interaction(manager);
    return {
        THREE, manager, scene,
        refresh() { now += 250; scene.updateMatrixWorld(true); manager.interaction.updateModelBoundsCache(); },
        setRect(value) { rect = value; },
        get rect() { return rect; },
    };
}

function visibleSamples(h) {
    const { THREE, scene, manager, rect } = h;
    const raycaster = new THREE.Raycaster();
    const samples = [];
    for (let x = Math.max(0, rect.left) + 32; x < rect.left + rect.width; x += 32) {
        for (let y = Math.max(0, rect.top) + 36; y < rect.top + rect.height; y += 36) {
            raycaster.setFromCamera(new THREE.Vector2(
                (x - rect.left) / rect.width * 2 - 1,
                1 - (y - rect.top) / rect.height * 2,
            ), manager.camera);
            const visible = raycaster.intersectObject(scene, true).some(hit => {
                const depth = -hit.point.clone().applyMatrix4(manager.camera.matrixWorldInverse).z;
                return depth >= manager.camera.near && depth <= manager.camera.far;
            });
            if (visible) samples.push({ x, y });
        }
    }
    return samples;
}

function assertCovers(bounds, samples, label) {
    assert.ok(bounds, `${label}: bounds must exist for a visible model`);
    assert.ok(Object.values(bounds).every(Number.isFinite), `${label}: bounds must remain finite`);
    for (const { x, y } of samples) {
        assert.ok(x >= bounds.left - 1e-6 && x <= bounds.right + 1e-6
            && y >= bounds.top - 1e-6 && y <= bounds.bottom + 1e-6,
        `${label}: visible point (${x}, ${y}) was excluded by ${JSON.stringify(bounds)}`);
    }
}

for (const type of ['vrm', 'mmd']) {
    for (const offset of [{ left: 0, top: 0 }, { left: -320, top: -180 }]) {
        test(`${type}: visible model stays inside UI and hit-test bounds across large zooms (offset=${offset.left})`, async () => {
            const h = await harness(type, offset);
            for (const scale of [0.25, 0.45, 0.5, 0.6, 1, 2, 1, 0.45]) {
                h.scene.scale.setScalar(scale);
                h.refresh();
                const samples = visibleSamples(h);
                assert.ok(samples.length > 0, `scale ${scale}: fixture must have visible geometry`);
                const bounds = h.manager.getModelScreenBounds();
                assertCovers(bounds, samples, `scale ${scale}: UI`);
                const cached = h.manager.interaction._cachedScreenBounds;
                assert.ok(cached, 'desktop hit-test cache must exist');
                const hitBounds = { left: cached.minX, right: cached.maxX, top: cached.minY, bottom: cached.maxY };
                assertCovers(hitBounds, samples, `scale ${scale}: hit test`);
                assert.equal(cached.minX, bounds.left);
                assert.equal(cached.maxX, bounds.right);
                assert.equal(cached.minY, bounds.top);
                assert.equal(cached.maxY, bounds.bottom);
            }
        });
    }

    test(`${type}: a bounding-box corner exactly on the camera plane does not erase the model bounds`, async () => {
        const h = await harness(type);
        h.scene.scale.setScalar(1.5 / 3.025);
        h.refresh();
        const samples = visibleSamples(h);
        assert.ok(samples.length > 0);
        assertCovers(h.manager.getModelScreenBounds(), samples, 'camera plane');
    });

    test(`${type}: clipping works with a rotated camera`, async () => {
        const h = await harness(type);
        h.scene.scale.setScalar(2);
        h.manager.camera.position.set(0.1, 0.1, 1.5);
        h.manager.camera.lookAt(0, 0, 0.8);
        h.manager.camera.updateMatrixWorld(true);
        h.refresh();
        const samples = visibleSamples(h);
        assert.ok(samples.length > 0);
        assertCovers(h.manager.getModelScreenBounds(), samples, 'rotated camera');
    });

    test(`${type}: hidden and empty models clear cached bounds and recover when visible`, async () => {
        const h = await harness(type);
        h.scene.scale.setScalar(2);
        h.refresh();
        assert.ok(h.manager.interaction._cachedScreenBounds);
        h.setRect({ left: 0, top: 0, width: 0, height: 0 });
        h.refresh();
        assert.equal(h.manager.getModelScreenBounds(), null);
        assert.equal(h.manager.interaction._cachedScreenBounds, null);
        h.setRect({ left: 0, top: 0, width: 1280, height: 720 });
        h.refresh();
        assertCovers(h.manager.getModelScreenBounds(), visibleSamples(h), 'restored canvas');
        h.scene.clear();
        h.refresh();
        assert.equal(h.manager.getModelScreenBounds(), null);
        assert.equal(h.manager.interaction._cachedScreenBounds, null);
    });

    test(`${type}: geometry wholly behind the camera or before the near plane has no hit bounds`, async () => {
        const h = await harness(type);
        h.scene.position.z = 20;
        h.refresh();
        assert.equal(h.manager.getModelScreenBounds(), null);
        assert.equal(h.manager.interaction._cachedScreenBounds, null);
        h.scene.clear();
        const plane = new h.THREE.Mesh(new h.THREE.PlaneGeometry(1, 1));
        h.scene.add(plane);
        h.scene.position.z = h.manager.camera.position.z - h.manager.camera.near / 2;
        h.refresh();
        assert.equal(h.manager.getModelScreenBounds(), null);
        assert.equal(h.manager.interaction._cachedScreenBounds, null);
    });
}

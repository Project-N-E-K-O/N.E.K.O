const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoStatic = __dirname;

class FakeVector2 {
    constructor(x = 0, y = 0) {
        this.x = x;
        this.y = y;
    }
}

class FakeVector3 {
    constructor(x = 0, y = 0, z = 0) {
        this.x = x;
        this.y = y;
        this.z = z;
        this.isVector3 = true;
    }

    clone() {
        return new FakeVector3(this.x, this.y, this.z);
    }

    copy(other) {
        this.x = other.x;
        this.y = other.y;
        this.z = other.z;
        return this;
    }

    add(other) {
        this.x += other.x;
        this.y += other.y;
        this.z += other.z;
        return this;
    }

    multiplyScalar(value) {
        this.x *= value;
        this.y *= value;
        this.z *= value;
        return this;
    }

    applyQuaternion() {
        return this;
    }

    distanceTo(other) {
        const dx = this.x - other.x;
        const dy = this.y - other.y;
        const dz = this.z - other.z;
        return Math.sqrt(dx * dx + dy * dy + dz * dz);
    }

    project() {
        return this;
    }
}

class FakeBox3 {
    setFromObject(object) {
        this.min = object._box.min.clone();
        this.max = object._box.max.clone();
        return this;
    }
}

class FakeQuaternion {
    setFromAxisAngle() {
        return this;
    }

    multiplyQuaternions() {
        return this;
    }
}

class FakeRaycaster {
    setFromCamera() {}
    intersectObject() {
        return [];
    }
}

function ndcFromScreenX(screenX, width) {
    return (screenX / width - 0.5) * 2;
}

function ndcFromScreenY(screenY, height) {
    return -((screenY / height - 0.5) * 2);
}

function createInteractionContext(sourceFile, exportExpression = '') {
    const source = fs.readFileSync(path.join(repoStatic, sourceFile), 'utf8');
    const window = {
        innerWidth: 2560,
        innerHeight: 1440,
        screen: { width: 2560, height: 1440 },
        THREE: {
            Box3: FakeBox3,
            Quaternion: FakeQuaternion,
            Raycaster: FakeRaycaster,
            Vector2: FakeVector2,
            Vector3: FakeVector3
        },
        NekoAvatarMultiScreenDragHint: {
            recordDisplaySwitchMiss() {},
            markDisplaySwitchSuccess() {}
        }
    };
    const context = vm.createContext({
        window,
        globalThis: window,
        document: {
            body: { classList: { contains: () => false } }
        },
        performance: { now: () => 0 },
        requestAnimationFrame(callback) {
            callback();
            return 1;
        },
        cancelAnimationFrame() {},
        setTimeout(callback) {
            callback();
            return 1;
        },
        clearTimeout() {},
        console
    });
    vm.runInContext(source + '\n' + exportExpression, context, {
        filename: path.join(repoStatic, sourceFile)
    });
    return window;
}

function createSceneLike(options = {}) {
    const minX = options.minX ?? 2550;
    const maxX = options.maxX ?? 2590;
    const minY = options.minY ?? 700;
    const maxY = options.maxY ?? 740;
    return {
        position: new FakeVector3(0, 0, 0),
        quaternion: { clone: () => ({}) },
        scale: { x: 1, y: 1, z: 1 },
        rotation: { x: 0, y: 0, z: 0 },
        _box: {
            min: new FakeVector3(ndcFromScreenX(minX, 2560), ndcFromScreenY(minY, 1440), 0),
            max: new FakeVector3(ndcFromScreenX(maxX, 2560), ndcFromScreenY(maxY, 1440), 0)
        },
        updateMatrixWorld() {}
    };
}

function createRenderer(viewport = { width: 2560, height: 1440 }) {
    return {
        domElement: {
            get clientWidth() {
                return viewport.width;
            },
            get clientHeight() {
                return viewport.height;
            },
            getBoundingClientRect: () => ({
                left: 0,
                top: 0,
                width: viewport.width,
                height: viewport.height
            })
        }
    };
}

function createCamera() {
    return {
        fov: 30,
        quaternion: {},
        position: new FakeVector3(0, 0, 10)
    };
}

function createScreenBridge({ onMove } = {}) {
    const calls = [];
    return {
        calls,
        bridge: {
            async getAllDisplays() {
                return [
                    { id: 1, screenX: 0, screenY: 0, width: 2560, height: 1440 },
                    { id: 2, screenX: 2560, screenY: 0, width: 1470, height: 956 }
                ];
            },
            async getCurrentDisplay() {
                return { id: 1, screenX: 0, screenY: 0, width: 2560, height: 1440 };
            },
            async moveWindowToDisplay(screenX, screenY) {
                calls.push({ screenX, screenY });
                if (typeof onMove === 'function') {
                    onMove();
                }
                return {
                    success: true,
                    sameDisplay: false,
                    displayId: 2,
                    bounds: { x: 2560, y: 0, width: 1470, height: 956 },
                    scaleFactor: 1,
                    scaleRatio: 1
                };
            }
        }
    };
}

test('VRM display switch uses the released drag pointer as the cross-screen target', async () => {
    const window = createInteractionContext('vrm/vrm-interaction.js');
    const scene = createSceneLike();
    const viewport = { width: 2560, height: 1440 };
    const screenBridge = createScreenBridge({
        onMove() {
            viewport.width = 1470;
            viewport.height = 956;
        }
    });
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.VRMInteraction({
        currentModel: { scene, vrm: { scene }, url: '/vrm/yui.vrm' },
        camera: createCamera(),
        renderer: createRenderer(viewport),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 3200, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    let snapCalls = 0;
    interaction._snapModelIntoScreen = async () => {
        snapCalls += 1;
        return false;
    };
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 3200, screenY: 720 });
    assert.ok(scene.position.x > -6, 'target-display viewport should be used when preserving the released pointer');
    assert.equal(snapCalls, 0, 'pointer-anchored cross-display drag should not run a second snap animation');
    assert.equal(interaction._lastPanDragPointerScreen, null, 'display switch should consume the cached drag pointer');
});

test('VRM display switch uses the released pointer even when the model center is still in the source window', async () => {
    const window = createInteractionContext('vrm/vrm-interaction.js');
    const scene = createSceneLike({ minX: 2500, maxX: 2530 });
    const screenBridge = createScreenBridge();
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.VRMInteraction({
        currentModel: { scene, vrm: { scene }, url: '/vrm/yui.vrm' },
        camera: createCamera(),
        renderer: createRenderer(),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 3200, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    interaction._snapModelIntoScreen = async () => false;
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 3200, screenY: 720 });
});

test('VRM display switch falls back to the model center when the release pointer remains on the source display', async () => {
    const window = createInteractionContext('vrm/vrm-interaction.js');
    const scene = createSceneLike();
    const screenBridge = createScreenBridge();
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.VRMInteraction({
        currentModel: { scene, vrm: { scene }, url: '/vrm/yui.vrm' },
        camera: createCamera(),
        renderer: createRenderer(),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 2500, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    interaction._snapModelIntoScreen = async () => false;
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 2570, screenY: 720 });
});

test('MMD display switch uses the released drag pointer as the cross-screen target', async () => {
    const window = createInteractionContext('mmd/mmd-interaction.js', 'window.MMDInteraction = MMDInteraction;');
    const mesh = createSceneLike();
    const viewport = { width: 2560, height: 1440 };
    const screenBridge = createScreenBridge({
        onMove() {
            viewport.width = 1470;
            viewport.height = 956;
        }
    });
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.MMDInteraction({
        currentModel: { mesh, url: '/mmd/yui.pmx' },
        camera: createCamera(),
        renderer: createRenderer(viewport),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 3200, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    let snapCalls = 0;
    interaction._snapModelIntoScreen = async () => {
        snapCalls += 1;
        return false;
    };
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 3200, screenY: 720 });
    assert.ok(mesh.position.x > -6, 'target-display viewport should be used when preserving the released pointer');
    assert.equal(snapCalls, 0, 'pointer-anchored cross-display drag should not run a second snap animation');
    assert.equal(interaction._lastPanDragPointerScreen, null, 'display switch should consume the cached drag pointer');
});

test('MMD display switch uses the released pointer even when the model center is still in the source window', async () => {
    const window = createInteractionContext('mmd/mmd-interaction.js', 'window.MMDInteraction = MMDInteraction;');
    const mesh = createSceneLike({ minX: 2500, maxX: 2530 });
    const screenBridge = createScreenBridge();
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.MMDInteraction({
        currentModel: { mesh, url: '/mmd/yui.pmx' },
        camera: createCamera(),
        renderer: createRenderer(),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 3200, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    interaction._snapModelIntoScreen = async () => false;
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 3200, screenY: 720 });
});

test('MMD display switch falls back to the model center when the release pointer remains on the source display', async () => {
    const window = createInteractionContext('mmd/mmd-interaction.js', 'window.MMDInteraction = MMDInteraction;');
    const mesh = createSceneLike();
    const screenBridge = createScreenBridge();
    window.electronScreen = screenBridge.bridge;
    const interaction = new window.MMDInteraction({
        currentModel: { mesh, url: '/mmd/yui.pmx' },
        camera: createCamera(),
        renderer: createRenderer(),
        core: { saveUserPreferences: async () => true }
    });
    interaction._lastPanDragPointerScreen = { x: 2500, y: 720 };
    interaction._panDragModelCenterOffset = { x: 0, y: 0 };
    interaction._snapModelIntoScreen = async () => false;
    interaction._savePositionAfterInteraction = async () => {};

    await interaction._checkAndSwitchDisplay();

    assert.deepEqual(screenBridge.calls[0], { screenX: 2570, screenY: 720 });
});

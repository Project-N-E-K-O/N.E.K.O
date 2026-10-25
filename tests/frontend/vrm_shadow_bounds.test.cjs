const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
const root = path.resolve(__dirname, '../..');
const THREE = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(root, 'static/libs/three.core.js')).toString('base64'));

const context = vm.createContext({
    window: { THREE, addEventListener() {} },
    console
});
vm.runInContext(fs.readFileSync(path.join(root, 'static/vrm/vrm-manager.js'), 'utf8'), context);
const { VRMManager } = context.window;

function skinnedBox(width, height, depth) {
    const geometry = new THREE.BoxGeometry(width, height, depth);
    const count = geometry.attributes.position.count;
    const skinIndices = new Uint16Array(count * 4);
    const skinWeights = new Float32Array(count * 4);
    for (let i = 0; i < count; i++) skinWeights[i * 4] = 1;
    geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(skinIndices, 4));
    geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(skinWeights, 4));
    const bone = new THREE.Bone();
    const mesh = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
    mesh.add(bone);
    mesh.bind(new THREE.Skeleton([bone]));
    return mesh;
}

function footprintOf(scene) {
    const manager = Object.create(VRMManager.prototype);
    const box = manager._collectSceneLocalBodyBox(scene);
    const size = box.getSize(new THREE.Vector3());
    return { size, footprint: Math.max(size.x, size.z) };
}

const yawCases = [0, Math.PI / 4, Math.PI / 2, Math.PI];
const scaleCases = [0.5, 1, 2];

for (const yaw of yawCases) {
    for (const scale of scaleCases) {
        const scene = new THREE.Group();
        const mesh = skinnedBox(1, 2, 1);
        scene.add(mesh);
        scene.rotation.y = yaw;
        scene.scale.setScalar(scale);
        const { size, footprint } = footprintOf(scene);
        assert.ok(Math.abs(footprint - 1) < 1e-4, `yaw ${yaw} scale ${scale} footprint ${footprint}`);
        assert.ok(Math.abs(size.y - 2) < 1e-4, `yaw ${yaw} scale ${scale} height ${size.y}`);
        assert.equal(mesh.boundingBox, null);
    }
}

{
    const scene = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.8, 1.5, 0.3));
    scene.add(mesh);
    scene.rotation.y = Math.PI / 4;
    scene.scale.setScalar(0.25);
    const { size, footprint } = footprintOf(scene);
    assert.ok(Math.abs(footprint - 0.8) < 1e-4, `static mesh footprint ${footprint}`);
    assert.ok(Math.abs(size.y - 1.5) < 1e-4, `static mesh height ${size.y}`);
}

{
    // 没有蒙皮网格时才走普通网格分支，阴影平面的排除检查才会被执行。
    const scene = new THREE.Group();
    scene.add(new THREE.Mesh(new THREE.BoxGeometry(0.4, 1, 0.2)));
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(4, 4));
    scene.add(shadow);
    const manager = Object.create(VRMManager.prototype);
    manager._shadowMesh = shadow;
    scene.rotation.y = Math.PI / 4;
    const size = manager._collectSceneLocalBodyBox(scene).getSize(new THREE.Vector3());
    assert.ok(Math.abs(Math.max(size.x, size.z) - 0.4) < 1e-4, `shadow plane widened the body box: ${size.x}, ${size.z}`);
}

{
    const scene = new THREE.Group();
    const mesh = skinnedBox(1, 2, 1);
    const saved = new THREE.Box3(new THREE.Vector3(-3, -3, -3), new THREE.Vector3(3, 3, 3));
    mesh.boundingBox = saved;
    scene.add(mesh);
    scene.rotation.y = 0.4;
    footprintOf(scene);
    assert.equal(mesh.boundingBox, saved);
    assert.ok(saved.min.distanceTo(new THREE.Vector3(-3, -3, -3)) < 1e-8);
    assert.ok(saved.max.distanceTo(new THREE.Vector3(3, 3, 3)) < 1e-8);
}
})().catch((error) => {
    console.error(error);
    process.exit(1);
});

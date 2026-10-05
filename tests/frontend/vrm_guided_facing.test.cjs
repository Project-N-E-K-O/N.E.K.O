const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
const root = path.resolve(__dirname, '../..');
// Use the shipped Three.js implementation, including quaternion/Euler coupling.
const THREE = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(root, 'static/libs/three.core.js')).toString('base64'));
const { Vector3, Quaternion, Object3D } = THREE;
let frameCallback = null;
const context = vm.createContext({ window: { THREE }, console, performance: { now: () => 0 },
    requestAnimationFrame: callback => { frameCallback = callback; return 1; },
    cancelAnimationFrame: () => { frameCallback = null; } });
for (const file of ['vrm-orientation.js', 'vrm-interaction.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'static/vrm', file), 'utf8'), context);
}
const { VRMOrientationDetector: detector, VRMInteraction: Interaction } = context.window;
assert.equal(detector.getMovementFacingProfile({ meta: { metaVersion: '1.0' } }, '0.0').yawOffset, Math.PI);
assert.equal(detector.getMovementFacingProfile({ meta: { metaVersion: 'broken' } }, '1.0').yawOffset, 0);
assert.equal(detector.getMovementFacingProfile({ meta: { metaVersion: '1.0' } }, 'unknown').yawOffset, 0);
assert.equal(detector.getMovementFacingProfile({ meta: { metaVersion: '0.0' } }).yawOffset, Math.PI);

// A reverse-authored VRM1 goes through the real bone detector before preferences.
for (const savedYaw of [null, 0.7]) {
    const scene = new Object3D(); const chest = new Object3D(); const head = new Object3D();
    head.position.set(0, 1, 0.2); scene.add(chest, head);
    const vrm = { scene, meta: { metaVersion: '1.0' }, userData: {},
        humanoid: { humanBones: { head: { node: head }, chest: { node: chest } } } };
    vrm.userData.orientationFlipped = detector.detectNeedsRotation(vrm);
    assert.equal(vrm.userData.orientationFlipped, true);
    detector.applyRotation(vrm, detector.detectAndFixOrientation(vrm,
        savedYaw === null ? null : { x: 0, y: savedYaw, z: 0 }));
    const interaction = new Interaction({ currentModel: { scene, vrm }, core: { vrmVersion: '1.0' },
        camera: { position: new Vector3(0, 0, 5), quaternion: new Quaternion(),
            getWorldDirection(v) { return v.set(0, 0, -1); } } });
    assert.equal(interaction._getMovementFacingProfile().yawOffset, Math.PI);
    for (const y of [1, -1]) {
        interaction.isMoving = true;
        interaction.moveTarget = scene.position.clone().add(new Vector3(0, y * 10, 0));
        interaction._movementFacingProfile = null;
        interaction._updateGuidedMovement(0.1);
        assert.ok(new Vector3(0, 0, -1).applyQuaternion(scene.quaternion).z * -y > 0.99);
    }
    interaction._setSceneYaw(scene, interaction._getCameraFacingRotationY(scene));
    assert.ok(new Vector3(0, 0, -1).applyQuaternion(scene.quaternion).z > 0.99);
}

for (const version of ['0.0', '1.0']) {
    const authoredFront = version === '1.0' ? 1 : -1;
    for (const cameraYaw of [0, 0.7, -1.2]) {
        const camera = {
            quaternion: new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), cameraYaw),
            position: new Vector3(5 * Math.sin(cameraYaw), 0, 5 * Math.cos(cameraYaw)),
            getWorldDirection(v) { Object.assign(v, new Vector3(0, 0, -1).applyQuaternion(this.quaternion)); }
        };
        const right = new Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
        const forward = new Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
        for (const [x, y] of [[0, 1], [0, -1], [1, 0], [-1, 0], [1, 1], [-1, 1], [1, -1], [-1, -1]]) {
            const scene = new Object3D();
            const interaction = Object.create(Interaction.prototype);
            Object.assign(interaction, {
                manager: { camera, currentModel: { scene, vrm: {} }, core: { vrmVersion: version } },
                isMoving: true, moveTarget: right.clone().multiplyScalar(x).addScaledVector(new Vector3(0, 1, 0), y),
                movementArrivalThreshold: 0.01, movementVelocity: 0,
                movementMaxSpeed: 0.5, movementAcceleration: 1, movementDeceleration: 1
            });
            interaction._updateGuidedMovement(0.1);
            const actual = new Vector3(0, 0, authoredFront).applyQuaternion(scene.quaternion);
            const expected = right.clone().multiplyScalar(x).addScaledVector(forward, y).normalize();
            assert.ok(actual.dot(expected) > 1 - 1e-8, `${version}: camera ${cameraYaw}, direction ${x},${y}`);

            const restYaw = interaction._getCameraFacingRotationY(scene);
            const restFront = new Vector3(authoredFront * Math.sin(restYaw), 0, authoredFront * Math.cos(restYaw));
            const toCamera = camera.position.clone().sub(scene.position); toCamera.y = 0;
            assert.ok(restFront.dot(toCamera.normalize()) > 1 - 1e-8, `${version}: arrival faces camera`);
        }
    }
}
// Module loaders continue after a failed orientation script; retain safe parity.
const helper = context.window.VRMOrientationDetector;
for (const missing of [undefined, {}]) {
    context.window.VRMOrientationDetector = missing;
    for (const version of ['0.0', '1.0', 'unknown']) {
        for (const flipped of [false, true]) {
            const vrm = { meta: { metaVersion: '1.0' }, userData: { orientationFlipped: flipped } };
            const interaction = new Interaction({ currentModel: { vrm }, core: { vrmVersion: version } });
            assert.equal(interaction._getMovementFacingProfile().yawOffset, helper.getMovementFacingProfile(vrm, version).yawOffset);
        }
    }
}
context.window.VRMOrientationDetector = helper;

// No detector means no calibration for a fresh reverse-authored VRM1.
context.window.VRMOrientationDetector = undefined;
{
    const scene = new Object3D(); const head = new Object3D(); const chest = new Object3D();
    head.position.set(0, 1, 0.2); scene.add(head, chest);
    const vrm = { scene, meta: { metaVersion: '1.0' }, userData: {},
        humanoid: { humanBones: { head: { node: head }, chest: { node: chest } } } };
    assert.equal(helper.detectNeedsRotation(vrm), true);
    const interaction = new Interaction({ currentModel: { scene, vrm }, core: { vrmVersion: '1.0' },
        camera: { position: new Vector3(0, 0, 5), quaternion: new Quaternion(),
            getWorldDirection(v) { return v.set(0, 0, -1); } } });
    assert.equal(interaction._getMovementFacingProfile().yawOffset, null);
    assert.equal(interaction._getCameraFacingRotationY(scene), null);
    const pose = scene.quaternion.clone();
    interaction.isMoving = true; interaction.moveTarget = new Vector3(0, 10, 0);
    interaction._updateGuidedMovement(0.1);
    assert.ok(scene.position.y > 0, 'uncalibrated model can still translate');
    assert.ok(scene.quaternion.angleTo(pose) < 1e-8, 'do not guess an uncalibrated heading');
    assert.equal(await interaction._smoothTurnToCamera(scene), true, 'arrival can finish and persist without turning');
    assert.ok(scene.quaternion.angleTo(pose) < 1e-8);
}
context.window.VRMOrientationDetector = helper;

console.log('VRM guided movement facing: OK');

// Real frame intervals must also work when the previous trip left the model
// facing the opposite direction. A large single delta hides backward steps.
for (const version of ['0.0', '1.0']) {
    const authoredFront = version === '1.0' ? 1 : -1;
    for (const rotation of [[0, 0, 0], [0, Math.PI, 0], [-3.1354805766406257, 0.05088144987006485, -3.128094060958441]]) {
    const scene = new Object3D();
    scene.rotation.set(...rotation);
    const camera = {
        quaternion: new Quaternion(), position: new Vector3(0, 0, 5),
        getWorldDirection(v) { Object.assign(v, new Vector3(0, 0, -1)); }
    };
    const interaction = Object.create(Interaction.prototype);
    Object.assign(interaction, {
        manager: { camera, currentModel: { scene, vrm: {} }, core: { vrmVersion: version } },
        isMoving: true, movementArrivalThreshold: 0.01, movementVelocity: 0,
        movementMaxSpeed: 0.5, movementAcceleration: 1, movementDeceleration: 1,
        enableFaceCamera: true, _smoothFacingFrame: null
    });
    for (const y of [1, -1, 1, -1, 1, -1]) {
        interaction.moveTarget = scene.position.clone().addScaledVector(new Vector3(0, 1, 0), y * 10);
        let moved = false;
        for (let frame = 0; frame < 90; frame++) {
            const previous = scene.position.clone();
            interaction.update(1 / 60);
            if (scene.position.clone().sub(previous).lengthSq() > 1e-12) {
                moved = true;
                const frontZ = new Vector3(0, 0, authoredFront).applyQuaternion(scene.quaternion).z;
                assert.ok(frontZ * -y > 0.9, `${version}: trip ${y}, frame ${frame} moves while facing backward`);
            }
        }
        assert.ok(moved, 'turning must eventually allow movement');
        // Arrival and the next trip must retain the same physical heading,
        // even when Three.js rewrites the equivalent XYZ Euler representation.
        const upBefore = new Vector3(0, 1, 0).applyQuaternion(scene.quaternion).y;
        interaction._smoothTurnToCamera(scene);
        assert.ok(frameCallback);
        frameCallback(360);
        assert.equal(interaction._smoothFacingFrame, null);
        const front = new Vector3(0, 0, authoredFront).applyQuaternion(scene.quaternion);
        const toCamera = camera.position.clone().sub(scene.position); toCamera.y = 0;
        front.y = 0;
        assert.ok(front.normalize().dot(toCamera.normalize()) > 1 - 1e-8);
        assert.ok(Math.abs(new Vector3(0, 1, 0).applyQuaternion(scene.quaternion).y - upBefore) < 1e-8, 'turning preserves tilt');
    }
    }
}
console.log('VRM repeated movement at 60 FPS: OK');
// A nearly vertical local forward axis must not stall guided movement.
for (const pitch of [Math.PI / 2, -Math.PI / 2, Math.PI / 2 - 1e-7]) {
    const scene = new Object3D(); scene.rotation.set(pitch, 0, 0);
    const interaction = new Interaction({ currentModel: { scene, vrm: {} }, core: { vrmVersion: '1.0' },
        camera: { position: new Vector3(0, 0, 5), quaternion: new Quaternion(),
            getWorldDirection(v) { return v.set(0, 0, -1); } } });
    interaction._rotateSceneYaw(scene, 0.7);
    assert.ok(Math.abs(interaction._getSceneYaw(scene) - 0.7) < 1e-8);
    const tilt = new Vector3(0, 0, 1).applyQuaternion(scene.quaternion).y;
    interaction.isMoving = true; interaction.moveTarget = new Vector3(10, 0, 0);
    for (let i = 0; i < 120; i++) interaction._updateGuidedMovement(1 / 60);
    assert.ok(scene.position.x > 0, 'vertical pose still turns and moves');
    assert.ok(Math.abs(new Vector3(0, 0, 1).applyQuaternion(scene.quaternion).y - tilt) < 1e-8);
}
})().catch(error => { console.error(error); process.exitCode = 1; });

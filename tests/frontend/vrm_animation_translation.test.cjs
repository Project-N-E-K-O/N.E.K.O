const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

global.window = global;
global.THREE = {};
vm.runInThisContext(
    fs.readFileSync(path.resolve(__dirname, '../../static/vrm/vrm-animation.js'), 'utf8'),
    { filename: 'static/vrm/vrm-animation.js' }
);

async function playFixture(vrmVersion, options, rejectCoverage = false) {
    const rotationNames = ['Normalized_J_Bip_C_Hips', 'Normalized_Spine', 'Normalized_Head'];
    const hipsPosition = { name: 'Normalized_J_Bip_C_Hips.position', values: [0, 1, 0, 0, 0.5, 0] };
    const rootPositions = [hipsPosition, { name: 'Reference.position' }, { name: 'Root.position' }];
    const handPosition = { name: 'Normalized_LeftHand.position' };
    const rotations = rotationNames.map(name => ({ name: `${name}.quaternion` }));
    const tracks = [...rootPositions, handPosition, ...rotations];
    const clip = { tracks: [...tracks] };
    const boneNames = new Set(tracks.map(track => track.name.split('.')[0]));
    const scene = {
        uuid: `translation-${vrmVersion}`,
        traverse() {},
        getObjectByName(name) { return boneNames.has(name) ? { name } : null; }
    };
    const vrm = { scene, humanoid: { autoUpdateHumanBones: true, getNormalizedBoneNode: bone => bone === 'hips' ? { name: 'Normalized_J_Bip_C_Hips' } : null } };
    const animation = new global.VRMAnimation({ currentModel: { vrm }, core: { vrmVersion } });
    animation._initLoader = async () => ({
        loadAsync: async () => ({ userData: { vrmAnimations: [{}] } })
    });
    global.VRMAnimation._animationModuleCache = { createVRMAnimationClip: () => clip };
    let configuredClip;
    let playedAction;
    const action = {};
    animation._createAndConfigureAction = (nextClip, mixerRoot) => {
        assert.equal(mixerRoot, scene);
        configuredClip = nextClip;
        return action;
    };
    animation._playAction = nextAction => { playedAction = nextAction; };

    if (rejectCoverage) {
        scene.getObjectByName = () => null;
        const idleAction = {};
        animation.currentAction = idleAction;
        animation.vrmaIsPlaying = true;
        animation.isIdleAnimation = true;
        await assert.rejects(animation.playVRMAAnimation('/fixture.vrma', options), /轨道匹配不足/);
        assert.equal(animation.currentAction, idleAction);
        assert.equal(animation.vrmaIsPlaying, true);
        assert.equal(animation.isIdleAnimation, true, 'failed movement must preserve idle rendering policy');
        assert.equal(configuredClip, undefined);
        return;
    }
    let startedAction;
    assert.equal(await animation.playVRMAAnimation('/fixture.vrma', {
        ...options, onStarted: nextAction => { startedAction = nextAction; }
    }), true);
    assert.equal(playedAction, action);
    assert.equal(startedAction, action);
    return { configuredClip, tracks, rootPositions, handPosition, rotations, hipsPosition };
}

(async () => {
    // Stopping a specific walk must leave a newer loading request valid, and
    // must do nothing after the dance has replaced that walk.
    {
        const animation = new global.VRMAnimation({}); const walk = { paused: true }; const dance = {};
        let released = 0;
        animation._releaseMixerAction = action => { assert.equal(action, walk); released++; };
        animation._restorePhysics = () => {};
        animation.currentAction = walk; animation._playRequestGeneration = 7;
        animation.stopVRMAAnimation({ expectedAction: walk, preservePending: true });
        assert.equal(released, 1); assert.equal(animation.currentAction, null);
        assert.equal(animation._playRequestGeneration, 7, 'pending dance request stays valid');
        animation.currentAction = dance;
        animation.stopVRMAAnimation({ expectedAction: walk, preservePending: true });
        assert.equal(animation.currentAction, dance); assert.equal(released, 1);
        assert.equal(animation._playRequestGeneration, 7);
        animation.currentAction = null; animation.stopVRMAAnimation();
        assert.equal(animation._playRequestGeneration, 8, 'ordinary stop still cancels pending loads');
    }
    for (const vrmVersion of ['0.0', '1.0']) {
        // Low quality / disabled physics skips vrm.update(); mixer playback must
        // still synchronize normalized bones even when its root is the scene.
        let humanoidUpdates = 0;
        const scene = { uuid: `pose-${vrmVersion}`, traverse() {}, updateMatrixWorld() {} };
        const humanoid = { autoUpdateHumanBones: true, update() { humanoidUpdates++; } };
        const animation = new global.VRMAnimation({ currentModel: { vrm: { scene, humanoid } }, core: { vrmVersion } });
        animation.vrmaIsPlaying = true;
        animation.vrmaMixer = { update() {}, getRoot: () => scene };
        animation.update(1 / 60);
        assert.equal(humanoidUpdates, 1);
        humanoid.autoUpdateHumanBones = false;
        animation.update(1 / 60);
        assert.equal(humanoidUpdates, 1, 'explicitly disabled synchronization remains respected');
        // Ordinary sitting/rest playback must preserve authored hip height.
        for (const options of [undefined, { movement: false }, { isIdle: true }]) {
            const fixture = await playFixture(vrmVersion, options);
            assert.deepEqual(fixture.configuredClip.tracks, fixture.tracks);
            assert.deepEqual(fixture.hipsPosition.values, [0, 1, 0, 0, 0.5, 0]);
        }
        await playFixture(vrmVersion, { movement: true }, true);
        const fixture = await playFixture(vrmVersion, { movement: true });
        assert.deepEqual(fixture.configuredClip.tracks, [fixture.handPosition, ...fixture.rotations]);
        assert.equal(fixture.rootPositions.some(track => fixture.configuredClip.tracks.includes(track)), false);
    }
    console.log('VRM animation translation: OK (normal poses and movement in VRM 0/1)');
})().catch(error => {
    console.error(error.stack || error);
    process.exitCode = 1;
});

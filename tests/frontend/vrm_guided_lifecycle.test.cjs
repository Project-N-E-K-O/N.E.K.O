const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
    const root = path.resolve(__dirname, '../..');
    const THREE = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(root, 'static/libs/three.core.js')).toString('base64'));
    const frames = new Map();
    let frameId = 0;
    const events = { addEventListener() {}, removeEventListener() {} };
    const window = { THREE, ...events, NekoModelTouchGestures: { installThree: () => ({ dispose() {} }) } };
    const context = vm.createContext({ window, console, performance: { now: () => 0 },
        document: { ...events, body: { classList: { contains: () => false } } },
        requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame(id) { frames.delete(id); }, clearTimeout, setTimeout });
    for (const file of ['vrm-orientation.js', 'vrm-interaction.js', 'vrm-manager.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'static/vrm', file), 'utf8'), context);
    }
    const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
    const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
    const finishTurn = () => {
        const pending = [...frames.entries()];
        assert.ok(pending.length, 'arrival must schedule a turn');
        for (const [id, callback] of pending) { frames.delete(id); callback(360); }
    };
    function fixture() {
        assert.equal(frames.size, 0, 'previous case must not leave animation frames');
        const leases = new Map();
        window.electronScreen = null;
        window.screen = { width: 1920, height: 1080 };
        window.innerWidth = 1920; window.innerHeight = 1080;
        const saves = [];
        let rests = 0;
        window.NekoMotion = {
            hasOtherExternalPlayback(owner) { return [...leases.keys()].some(key => key !== owner); },
            async holdExternalPlayback(owner, { token }) { leases.set(owner, token); },
            async releaseExternalPlayback(owner, { token, resume = true }) {
                if (leases.get(owner) !== token) return false;
                leases.delete(owner); if (resume && !leases.size) rests++; return true;
            },
            async rest() { rests++; }
        };
        const scene = new THREE.Object3D();
        const camera = new THREE.PerspectiveCamera(); camera.position.z = 5;
        const manager = { camera, currentModel: { scene, vrm: {} }, core: { vrmVersion: '1.0' },
            _isModelReadyForInteraction: true, isLocked: false,
            renderer: { domElement: { ...events, style: {} } },
            playVRMAAnimation: async () => true, stopVRMAAnimation() {} };
        const interaction = new window.VRMInteraction(manager);
        interaction.clampModelPosition = position => position;
        interaction._snapModelIntoScreen = async () => {};
        interaction._savePositionAfterInteraction = async () => { saves.push(scene.quaternion.clone()); };
        interaction._hitTestModel = () => true;
        interaction.initDragAndZoom();
        const select = target => {
            interaction._screenPointToMovementTarget = () => target;
            assert.equal(interaction._selectMovementTarget(0, 0), true);
        };
        const mouseDown = button => interaction.mouseDownHandler({ button, clientX: 0, clientY: 0,
            preventDefault() {}, stopPropagation() {} });
        return { interaction, manager, scene, leases, saves, select, mouseDown, rests: () => rests };
    }
    function capturePreferences(f) {
        const writes = [];
        f.manager.currentModel.url = '/model-a.vrm';
        f.manager.core.saveUserPreferences = async (...args) => { writes.push(args); return true; };
        f.interaction._savePositionAfterInteraction = window.VRMInteraction.prototype._savePositionAfterInteraction;
        return writes;
    }

    // Locking during a real drag rebound keeps its final position and save.
    {
        const f = fixture();
        f.scene.position.x = 2;
        f.interaction.isDragging = true; f.interaction.dragMode = 'pan';
        f.interaction._checkAndSwitchDisplay = async () => false;
        f.interaction._recordDragHintPointerEdgeRelease = async () => false;
        f.interaction.clampModelPosition = position => position.set(1, 0, 0);
        f.interaction._snapModelIntoScreen = window.VRMInteraction.prototype._snapModelIntoScreen;
        const ending = f.interaction._endDrag(); await flush();
        const token = f.interaction.movementToken;
        for (const [id, callback] of [...frames]) { frames.delete(id); callback(26); }
        assert.ok(f.scene.position.x > 1 && f.scene.position.x < 2);
        f.interaction.setLocked(true);
        assert.equal(f.interaction.movementToken, token);
        finishTurn(); await ending;
        assert.equal(f.scene.position.x, 1);
        assert.equal(f.saves.length, 1, 'rebound completion must persist exactly once');
        f.interaction.cleanupDragAndZoom();
    }
    // Explicit position writers still invalidate a pending rebound.
    for (const method of ['setModelPosition', 'resetModelPosition']) {
        const f = fixture(); f.manager.interaction = f.interaction;
        f.manager.currentModel.vrm.scene = f.scene;
        f.manager.setModelScaleScalar = () => {};
        f.scene.position.x = 2;
        const snapping = f.interaction._animateModelToPosition(f.scene.position.clone(), new THREE.Vector3(1, 0, 0));
        if (method === 'setModelPosition') window.VRMManager.prototype[method].call(f.manager, 3, 2, 1);
        else window.VRMManager.prototype[method].call(f.manager);
        const position = f.scene.position.clone(); finishTurn();
        assert.equal(await snapping, false);
        assert.ok(f.scene.position.equals(position), 'old rebound must not overwrite the new position');
        f.interaction.cleanupDragAndZoom();
    }
    // Rapid retargeting queues only the departure and eventual arrival saves.
    {
        const f = fixture();
        f.select(f.scene.position.clone()); await flush();
        assert.equal(f.saves.length, 0, 'stationary selections need no write');
        f.select(new THREE.Vector3(2, 0, 0)); await flush();
        for (let i = 0; i < 10; i++) {
            f.interaction.update(1 / 60);
            f.select(new THREE.Vector3(2 + i / 10, 0, 0)); await flush();
        }
        assert.equal(f.saves.length, 1, 'retargets must not queue intermediate poses');
        f.scene.position.copy(f.interaction.moveTarget);
        f.interaction.update(1 / 60); await flush();
        if (frames.size) finishTurn(); await flush();
        assert.equal(f.saves.length, 2, 'arrival still persists the final pose');
        f.interaction.cleanupDragAndZoom();
    }

    // Retarget then replace the model before arrival: persist the old model's
    // stopped pose once, even when display IPC completes after replacement.
    {
        const f = fixture(); const writes = capturePreferences(f); const display = deferred();
        window.electronScreen = { getCurrentDisplay: () => display.promise };
        f.select(new THREE.Vector3(2, 0, 0)); await flush();
        f.interaction.update(0.2);
        f.select(new THREE.Vector3(3, 0, 0)); await flush();
        f.interaction.update(0.2);
        const stopped = f.scene.position.clone();
        assert.ok(stopped.x > 0);
        f.interaction.cleanupDragAndZoom();
        f.manager.currentModel = { url: '/model-b.vrm', scene: new THREE.Object3D(), vrm: {} };
        f.interaction.cleanupDragAndZoom();
        display.resolve({ id: 1 }); await flush();
        assert.equal(writes.length, 2, 'departure plus one cleanup save, no retarget or duplicate cleanup writes');
        assert.equal(writes[1][0], '/model-a.vrm');
        assert.deepEqual({ ...writes[1][1] }, { x: stopped.x, y: stopped.y, z: stopped.z });
        assert.equal(f.manager.currentModel.scene.position.x, 0);
        assert.equal(f.leases.size, 0);
        window.electronScreen = null;
    }

    // Starting a new trip during arrival persists the prior endpoint with its
    // intended yaw, without writing the half-turned pose or changing the scene.
    {
        const f = fixture(); const writes = capturePreferences(f);
        f.scene.rotation.set(0.15, 0.3, -0.1);
        const restPose = f.scene.quaternion.clone();
        f.select(new THREE.Vector3(1, 0, 0)); await flush();
        f.scene.position.copy(f.interaction.moveTarget);
        f.interaction._rotateSceneYaw(f.scene, 1);
        const ending = f.interaction._finishMovement(); await flush();
        for (const [id, callback] of [...frames]) { frames.delete(id); callback(150); }
        const halfTurn = f.scene.quaternion.clone();
        f.select(new THREE.Vector3(3, 0, 0)); await flush(); await ending;
        assert.equal(writes.length, 2, 'departure plus previous endpoint');
        assert.deepEqual({ ...writes[1][1] }, { x: 1, y: 0, z: 0 });
        const restored = new THREE.Object3D(); const rotation = writes[1][3];
        restored.rotation.set(rotation.x, rotation.y, rotation.z);
        assert.ok(restored.quaternion.angleTo(restPose) < 1e-7);
        assert.ok(f.scene.quaternion.angleTo(halfTurn) < 1e-7, 'snapshot correction must not jump the visible pose');
        for (let i = 0; i < 3; i++) f.select(new THREE.Vector3(4 + i, 0, 0));
        await flush(); assert.equal(writes.length, 2, 'ordinary retargets still do not write');
        f.interaction.cleanupDragAndZoom(); await flush();
    }
    // Full manager disposal must enqueue the old pose before core clears it.
    {
        const f = fixture(); const writes = capturePreferences(f);
        f.manager.interaction = f.interaction;
        f.manager._stopIdleFpsGovernor = () => {};
        f.manager._disposeShadowResources = () => {};
        f.manager.renderer.dispose = () => {};
        f.manager.core.disposeVRM = async () => {
            assert.equal(f.interaction.isMoving, false, 'interaction stops before model disposal');
            f.manager.currentModel = null;
        };
        f.select(new THREE.Vector3(2, 0, 0)); await flush();
        f.interaction.update(0.2); const stopped = f.scene.position.clone();
        await window.VRMManager.prototype.dispose.call(f.manager); await flush();
        assert.equal(writes.length, 2);
        assert.equal(writes[1][0], '/model-a.vrm');
        assert.deepEqual({ ...writes[1][1] }, { x: stopped.x, y: stopped.y, z: stopped.z });
        assert.equal(f.manager.currentModel, null); assert.equal(f.leases.size, 0);
        assert.equal(frames.size, 0);
    }

    // Existing external playback survives translation, arrival and cancellation.
    for (const cancel of [false, true]) {
        const f = fixture(); let plays = 0; let stops = 0; let boosts = 0;
        f.leases.set('jukebox', 'song');
        f.manager.playVRMAAnimation = async () => { plays++; return true; };
        f.manager.stopVRMAAnimation = () => { stops++; };
        f.manager._boostInteractiveFPS = () => { boosts++; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        assert.equal(boosts, 1);
        f.interaction._updateGuidedMovement(0.1);
        assert.ok(f.scene.position.y > 0, 'external playback must not prevent translation');
        if (cancel) f.interaction._cancelGuidedMovement();
        else {
            f.scene.rotation.y = Math.PI / 2;
            f.interaction.moveTarget.copy(f.scene.position);
            f.interaction._updateGuidedMovement(1 / 60); finishTurn();
        }
        await flush();
        assert.equal(plays, 0); assert.equal(stops, 0); assert.equal(f.rests(), 0);
        assert.equal(f.leases.size, 1); assert.equal(f.leases.get('jukebox'), 'song');
        f.interaction.cleanupDragAndZoom(); await flush();
    }

    // A dance starting while the walk loads must not be overwritten or stopped.
    {
        const f = fixture(); const loading = deferred(); let stops = 0;
        f.manager.playVRMAAnimation = async (_path, options) => {
            await loading.promise; return options.shouldApply();
        };
        f.manager.stopVRMAAnimation = () => { stops++; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.leases.set('jukebox', 'new-song'); loading.resolve(); await flush();
        assert.equal(f.interaction._movementAction, null);
        f.interaction._cancelGuidedMovement(); await flush();
        assert.equal(stops, 0); assert.equal(f.rests(), 0);
        assert.equal(f.leases.size, 1); assert.equal(f.leases.get('jukebox'), 'new-song');
        f.interaction.cleanupDragAndZoom();
    }

    // A held-but-loading dance allows stopping only the walk instance; an
    // already-started dance must survive the same movement completion.
    for (const danceStarted of [false, true]) {
        const f = fixture(); const walk = {}; const dance = {}; let currentAction; let stopped = 0;
        f.manager.playVRMAAnimation = async (_path, options) => {
            currentAction = walk; options.onStarted(walk); return true;
        };
        f.manager.stopVRMAAnimation = options => {
            assert.equal(options.expectedAction, walk); assert.equal(options.preservePending, true);
            if (currentAction === options.expectedAction) { currentAction = null; stopped++; }
        };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.leases.set('jukebox', 'song-loading');
        if (danceStarted) currentAction = dance;
        f.interaction._cancelGuidedMovement(); await flush();
        assert.equal(stopped, danceStarted ? 0 : 1);
        assert.equal(currentAction, danceStarted ? dance : null);
        assert.equal(f.leases.size, 1); assert.equal(f.rests(), 0);
        f.interaction.cleanupDragAndZoom();
    }

    // The idle governor sees pure movement and arrival turns without a walk clip.
    {
        const manager = Object.create(window.VRMManager.prototype);
        manager.animation = { vrmaIsPlaying: true, isIdleAnimation: true };
        manager.interaction = { isMoving: false, _smoothFacingFrame: null };
        assert.equal(manager._hasRenderActivity(), false);
        manager.interaction.isMoving = true;
        assert.equal(manager._hasRenderActivity(), true);
        manager.interaction.isMoving = false; manager.interaction._smoothFacingFrame = () => {};
        assert.equal(manager._hasRenderActivity(), true);
        manager.interaction._smoothFacingFrame = null;
        assert.equal(manager._hasRenderActivity(), false);
    }

    // Replacing a target after walking starts keeps the clip and its lease.
    {
        const f = fixture(); let plays = 0;
        f.manager.playVRMAAnimation = async () => { plays++; return true; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        const ownerToken = f.interaction._movementOwnerToken;
        f.select(new THREE.Vector3(1, 1, 0)); await flush();
        assert.equal(plays, 1);
        assert.equal(f.interaction._movementOwnerToken, ownerToken);
        assert.equal(f.leases.size, 1);
        f.interaction.cleanupDragAndZoom(); await flush();
        assert.equal(f.leases.size, 0);
    }

    // Clicking the current position before the walk loads must release the lease,
    // even though no movement action has been assigned yet.
    for (const result of [true, false]) {
        const f = fixture();
        const loading = deferred(); let options;
        f.manager.playVRMAAnimation = (_path, settings) => { options = settings; return loading.promise; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        assert.equal(f.leases.size, 1);
        assert.equal(f.interaction._movementAction, null);
        f.select(f.scene.position.clone()); await flush();
        assert.equal(f.leases.size, 0);
        assert.equal(f.interaction.isMoving, false);
        assert.equal(options.shouldApply(), false, 'cancelled loading must not apply its clip');
        loading.resolve(result); await flush();
        assert.equal(f.interaction._movementAction, null);
        f.interaction.cleanupDragAndZoom();
    }

    // A failed clip leaves pure translation active; cleanup must release it.
    {
        const f = fixture(); let stops = 0;
        f.manager.playVRMAAnimation = async () => false;
        f.manager.stopVRMAAnimation = () => { stops++; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        assert.equal(f.interaction.isMoving, true);
        assert.equal(f.interaction._movementAction, null);
        f.interaction.cleanupDragAndZoom(); await flush();
        assert.equal(f.leases.size, 0);
        assert.equal(stops, 0, 'failed walk must not stop the existing idle action');
    }

    // Cleanup also covers ownership alone, and must survive a stop failure.
    {
        const f = fixture();
        f.interaction._movementOwnerToken = 'held-without-action';
        f.interaction._movementPlaybackAction = {};
        f.leases.set(f.interaction._movementRestOwner, 'held-without-action');
        const warnings = [];
        context.console = { ...console, warn: (...args) => warnings.push(args) };
        f.manager.stopVRMAAnimation = () => { throw new Error('stop fixture failure'); };
        f.interaction.cleanupDragAndZoom(); await flush();
        assert.equal(f.leases.size, 0);
        assert.equal(warnings.length, 1);
        context.console = console;
    }

    // A stale hold completion may release only its own token, never a new trip.
    {
        const f = fixture(); const held = deferred(); let holds = 0;
        window.NekoMotion.holdExternalPlayback = async (owner, { token }) => {
            f.leases.set(owner, token); if (++holds === 1) await held.promise;
        };
        f.select(new THREE.Vector3(0, 1, 0));
        f.select(new THREE.Vector3(0, -1, 0)); await flush();
        const newToken = f.interaction._movementOwnerToken;
        held.resolve(); await flush();
        assert.equal(f.leases.get(f.interaction._movementRestOwner), newToken);
        assert.equal(f.interaction._movementAction, 'walk');
        f.interaction.cleanupDragAndZoom(); await flush();
        assert.equal(f.leases.size, 0);
    }

    // Save the final arrival heading, after the actual turn has finished.
    {
        const f = fixture();
        f.select(new THREE.Vector3(0, 1, 0)); await flush(); f.saves.length = 0;
        f.scene.rotation.y = Math.PI / 2;
        f.interaction.moveTarget.copy(f.scene.position);
        f.interaction._updateGuidedMovement(1 / 60); await flush();
        assert.equal(f.saves.length, 0);
        finishTurn(); await flush();
        assert.equal(f.saves.length, 1);
        assert.ok(f.saves[0].angleTo(new THREE.Quaternion()) < 1e-8);
        f.interaction.cleanupDragAndZoom();
    }

    // Both manual controls cancel active motion and pending arrival turns.
    for (const button of [0, 1, 2]) {
        for (const arriving of [false, true]) {
            const f = fixture();
            f.select(new THREE.Vector3(0, 1, 0)); await flush(); f.saves.length = 0;
            if (arriving) { f.scene.rotation.y = Math.PI / 2; void f.interaction._finishMovement(); await flush(); }
            f.mouseDown(button); await flush();
            assert.equal(f.interaction.isMoving, false);
            assert.equal(f.leases.size, 0);
            assert.equal(frames.size, 0);
            assert.equal(f.interaction.dragMode, button === 2 ? 'orbit' : 'pan');
            assert.equal(f.saves.length, 0, 'cancelled arrival must not save a stale heading');
            if (arriving) assert.ok(f.scene.quaternion.angleTo(new THREE.Quaternion()) < 1e-8, 'manual takeover starts from the intended arrival heading');
            f.interaction.cleanupDragAndZoom();
        }
    }

    // A missed pan hit preserves movement, while locking cancels it.
    {
        const f = fixture(); f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.interaction._hitTestModel = () => false; f.mouseDown(0);
        assert.equal(f.interaction.isMoving, true);
        f.interaction.setLocked(true); await flush();
        assert.equal(f.leases.size, 0); assert.equal(f.interaction.isMoving, false);
        f.interaction.cleanupDragAndZoom();
    }

    // Even after the final frame, a pending release cannot restore/save over a
    // newer manual interaction, target selection, or disposal.
    for (const takeover of ['orbit', 'cleanup', 'same-position']) {
        const f = fixture(); const releasing = deferred();
        f.select(new THREE.Vector3(0, 1, 0)); await flush(); f.saves.length = 0;
        const release = window.NekoMotion.releaseExternalPlayback;
        window.NekoMotion.releaseExternalPlayback = async (...args) => { await release(...args); await releasing.promise; };
        f.scene.rotation.y = Math.PI / 2;
        const finished = f.interaction._finishMovement();
        finishTurn(); await flush();
        if (takeover === 'orbit') f.mouseDown(2);
        else if (takeover === 'cleanup') f.interaction.cleanupDragAndZoom();
        else f.select(f.scene.position.clone());
        const expectedSaves = f.saves.length;
        releasing.resolve(); await finished;
        assert.equal(f.saves.length, expectedSaves + (takeover === 'same-position' ? 1 : 0));
        assert.equal(f.rests(), 1, 'release restores idle once before the pending continuation');
        f.interaction.cleanupDragAndZoom();
    }
    // Locking settles the current pose; unlike a drag, there is no later mouseup
    // to persist it. Cover every stage, including release after the final frame.
    for (const stage of ['moving', 'arrival-start', 'arrival-middle', 'release-pending']) {
        const f = fixture(); const writes = capturePreferences(f);
        f.select(new THREE.Vector3(0, 2, 0)); await flush(); writes.length = 0;
        f.scene.position.set(0, stage === 'moving' ? 1 : 2, 0);
        f.scene.rotation.y = Math.PI / 2;
        const releasing = deferred();
        if (stage === 'release-pending') {
            const release = window.NekoMotion.releaseExternalPlayback;
            window.NekoMotion.releaseExternalPlayback = async (...args) => { await release(...args); await releasing.promise; };
        }
        if (stage !== 'moving') { f.interaction._updateGuidedMovement(1 / 60); await flush(); }
        if (stage === 'arrival-middle') {
            for (const [id, callback] of [...frames.entries()]) { frames.delete(id); callback(180); }
        } else if (stage === 'release-pending') finishTurn();
        const lockedRotation = stage === 'moving' ? f.scene.quaternion.clone() : new THREE.Quaternion();
        f.interaction.setLocked(true); await flush();
        assert.equal(writes.length, 1, `${stage}: locking must persist the stopped pose`);
        assert.deepEqual([writes[0][1].x, writes[0][1].y, writes[0][1].z], [0, stage === 'moving' ? 1 : 2, 0]);
        const savedRotation = writes[0][3];
        const restored = new THREE.Object3D();
        restored.position.set(writes[0][1].x, writes[0][1].y, writes[0][1].z);
        restored.rotation.set(savedRotation.x, savedRotation.y, savedRotation.z);
        assert.ok(restored.position.distanceTo(f.scene.position) < 1e-8);
        assert.ok(restored.quaternion.angleTo(lockedRotation) < 1e-7);
        assert.equal(frames.size, 0); assert.equal(f.leases.size, 0);
        releasing.resolve(); await flush();
        assert.equal(writes.length, 1, 'cancelled arrival must not overwrite the locked pose');
        f.interaction.cleanupDragAndZoom();
    }
    // Preview pages without a motion runtime retain the existing animation.
    for (const cancel of [false, true]) {
        const f = fixture(); delete window.NekoMotion;
        let plays = 0, stops = 0;
        f.manager.playVRMAAnimation = async () => { plays++; return true; };
        f.manager.stopVRMAAnimation = () => { stops++; };
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.interaction._updateGuidedMovement(0.1);
        assert.ok(f.scene.position.y > 0, 'preview still supports pure translation');
        if (cancel) f.interaction.setLocked(true);
        else { f.scene.position.copy(f.interaction.moveTarget); const finished = f.interaction._finishMovement();
            if (frames.size) finishTurn(); await finished; }
        await flush(); assert.equal(plays, 0); assert.equal(stops, 0);
        f.interaction.cleanupDragAndZoom();
    }

    // A retarget during the arrival turn inherits the intended rest heading.
    for (const samePosition of [false, true]) {
        const f = fixture(); f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.scene.rotation.y = Math.PI / 2;
        void f.interaction._finishMovement(); await flush();
        for (const [id, callback] of [...frames]) { frames.delete(id); callback(150); }
        assert.ok(f.scene.rotation.y > 0.1);
        f.saves.length = 0;
        f.select(samePosition ? f.scene.position.clone() : new THREE.Vector3(0, 2, 0));
        await flush(); assert.equal(f.saves.length, 0, 'never save a half-finished arrival heading');
        if (!samePosition) {
            assert.equal(f.interaction._movementRestRotationY, 0);
            f.scene.rotation.y = -Math.PI / 2;
            void f.interaction._finishMovement(); await flush();
        }
        finishTurn(); await flush();
        assert.ok(f.scene.quaternion.angleTo(new THREE.Quaternion()) < 1e-8);
        assert.equal(f.saves.length, 1);
        f.interaction.cleanupDragAndZoom();
    }

    // Facing calibration is read once per trip, then refreshed on retarget.
    {
        const f = fixture(); let reads = 0;
        const profile = f.interaction._getMovementFacingProfile.bind(f.interaction);
        f.interaction._getMovementFacingProfile = () => { reads++; return profile(); };
        f.select(new THREE.Vector3(0, -2, 0)); await flush();
        for (let i = 0; i < 5; i++) f.interaction._updateGuidedMovement(1 / 60);
        assert.equal(reads, 1);
        f.select(new THREE.Vector3(0, -3, 0)); await flush();
        assert.equal(reads, 2);
        f.interaction.cleanupDragAndZoom(); await flush();
    }
    // Locking during pan or orbit must persist the manually changed pose.
    for (const button of [0, 2]) {
        const f = fixture(); f.mouseDown(button);
        f.scene.position.set(1, 2, 3); f.scene.rotation.y = 0.7;
        const expected = f.scene.quaternion.clone();
        f.interaction.setLocked(true); await flush();
        assert.equal(f.saves.length, 1);
        assert.ok(f.saves[0].angleTo(expected) < 1e-8);
        assert.equal(f.interaction.isDragging, false);
        f.interaction.cleanupDragAndZoom();
    }
    // Locking finishes drag boundaries before saving, without a duplicate mouseup.
    for (const switched of [false, true]) {
        const f = fixture(); const order = []; const snapping = deferred();
        f.mouseDown(0);
        f.interaction._checkAndSwitchDisplay = async () => { order.push('display');
            if (switched) { f.scene.position.x = 2; await f.interaction._savePositionAfterInteraction(); }
            return switched; };
        f.interaction._recordDragHintPointerEdgeRelease = async () => { order.push('hint'); };
        f.interaction._snapModelIntoScreen = async () => { order.push('snap'); await snapping.promise; f.scene.position.x = 1; };
        f.interaction._savePositionAfterInteraction = async () => { order.push('save'); };
        f.interaction.setLocked(true); await flush();
        if (!switched) { assert.deepEqual(order, ['display', 'hint', 'snap']); snapping.resolve(); await flush(); }
        assert.deepEqual(order, switched ? ['display', 'save'] : ['display', 'hint', 'snap', 'save']);
        await f.interaction.mouseUpHandler({});
        assert.equal(order.filter(x => x === 'save').length, 1);
        assert.equal(f.scene.position.x, switched ? 2 : 1);
        f.interaction.cleanupDragAndZoom();
    }
    // A model switch during drag completion saves the old immutable snapshot
    // and never snaps or persists the newly selected model.
    for (const stage of ['display', 'hint', 'snap']) {
        const f = fixture(); const writes = capturePreferences(f); const waiting = deferred();
        f.mouseDown(0); f.scene.position.set(3, 2, 1);
        f.interaction.clampModelPosition = position => { position.x = 1; return position; };
        let snaps = 0;
        f.interaction._checkAndSwitchDisplay = async () => { if (stage === 'display') await waiting.promise; return false; };
        f.interaction._recordDragHintPointerEdgeRelease = async () => { if (stage === 'hint') await waiting.promise; };
        f.interaction._snapModelIntoScreen = async () => { snaps++; if (stage === 'snap') await waiting.promise; };
        f.interaction.setLocked(true); await flush();
        f.manager.currentModel = { url: '/model-b.vrm', scene: new THREE.Object3D() };
        f.scene.position.set(99, 99, 99);
        waiting.resolve(); await flush();
        assert.equal(writes.length, 1, stage);
        assert.equal(writes[0][0], '/model-a.vrm');
        assert.deepEqual({ ...writes[0][1] }, { x: 1, y: 2, z: 1 });
        assert.equal(snaps, stage === 'snap' ? 1 : 0);
        assert.deepEqual(f.manager.currentModel.scene.position.toArray(), [0, 0, 0]);
        f.interaction.cleanupDragAndZoom();
    }
    // An original-position selection while release is pending must retain one
    // arrival/rest flow, including selection partway through the turn.
    for (const halfway of [false, true]) {
        const f = fixture(); const releasing = deferred();
        f.select(new THREE.Vector3(0, 1, 0)); await flush(); f.saves.length = 0;
        const release = window.NekoMotion.releaseExternalPlayback;
        window.NekoMotion.releaseExternalPlayback = async (...args) => { await release(...args); await releasing.promise; };
        f.scene.rotation.y = Math.PI / 2;
        const finished = f.interaction._finishMovement();
        if (halfway) for (const [id, callback] of [...frames]) { frames.delete(id); callback(150); }
        const token = f.interaction.movementToken;
        f.select(f.scene.position.clone());
        assert.equal(f.interaction.movementToken, token);
        finishTurn(); releasing.resolve(); await finished;
        assert.equal(f.rests(), 1); assert.equal(f.saves.length, 1);
        assert.ok(f.scene.quaternion.angleTo(new THREE.Quaternion()) < 1e-8);
        f.interaction.cleanupDragAndZoom();
    }

    // Ordinary lock toggles never write preferences; interrupted movement does.
    {
        const f = fixture(); f.interaction.setLocked(true); await flush();
        f.interaction.setLocked(false); f.interaction.setLocked(true); await flush();
        assert.equal(f.saves.length, 0); f.interaction.cleanupDragAndZoom();
    }
    for (const modifier of ['ctrlKey', 'metaKey', 'altKey']) {
        const f = fixture(); let prevented = false;
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', [modifier]: true,
            preventDefault() { prevented = true; } });
        assert.equal(f.interaction.targetMode, false); assert.equal(prevented, false);
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', preventDefault() { prevented = true; } });
        assert.equal(f.interaction.targetMode, true); assert.equal(prevented, true);
        f.interaction._movementKeyUpHandler({ key: 'f', code: 'KeyF' });
        assert.equal(f.interaction.targetMode, false); f.interaction.cleanupDragAndZoom();
    }
    for (const key of ['а', 'φ', 'F']) {
        const f = fixture();
        f.interaction._movementKeyDownHandler({ key, code: 'KeyF', preventDefault() {} });
        assert.equal(f.interaction.targetMode, true, 'physical F works across layouts');
        f.interaction._movementKeyUpHandler({ key, code: 'KeyF' });
        assert.equal(f.interaction.targetMode, false);
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyA', preventDefault() {} });
        assert.equal(f.interaction.targetMode, false, 'another physical key cannot activate F');
        f.interaction.cleanupDragAndZoom();
    }
    // Arrival scheduling uses the shared paced-frame hook, including cancellation.
    {
        const f = fixture(); let scheduled = 0, cancelled = 0;
        window.nekoFramePacing = { requestPacedFrame(callback) {
            scheduled++; const id = ++frameId; frames.set(id, callback);
            return () => { cancelled++; frames.delete(id); };
        } };
        f.scene.rotation.y = Math.PI / 2;
        const turn = f.interaction._smoothTurnToCamera(f.scene, 0);
        assert.equal(scheduled, 1); f.interaction._cancelSmoothFacing();
        assert.equal(await turn, false); assert.equal(cancelled, 1); assert.equal(frames.size, 0);
        delete window.nekoFramePacing; f.interaction.cleanupDragAndZoom();
    }


    // Editable DOM descendants, including plaintext-only/inherited editors, never arm F.
    {
        const f = fixture();
        assert.equal(f.interaction._isEditableTarget({ isContentEditable: true }), true);
        for (const mode of ['plaintext-only', '']) {
            let selector;
            assert.equal(f.interaction._isEditableTarget({ closest(value) { selector = value; return { contentEditable: mode }; } }), true);
            assert.ok(selector.includes('[contenteditable]:not([contenteditable="false"])'));
        }
        f.interaction.cleanupDragAndZoom();
    }
    // Persistence may stay pending after arrival; its token and idle finish promptly.
    {
        const f = fixture(); const writing = deferred(); let count = 0;
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        f.manager.currentModel.url = '/slow-save.vrm';
        f.manager.core.saveUserPreferences = () => { count++; return writing.promise; };
        f.interaction._savePositionAfterInteraction = window.VRMInteraction.prototype._savePositionAfterInteraction;
        f.scene.rotation.y = Math.PI / 2;
        const done = f.interaction._finishMovement(); finishTurn(); await done;
        assert.equal(f.interaction._movementFinishingToken, null);
        assert.equal(f.rests(), 1); assert.equal(count, 1);
        f.interaction.setLocked(true); await flush(); assert.equal(count, 1);
        writing.resolve(true); await flush(); f.interaction.cleanupDragAndZoom();
    }
    // Reuse display IPC while capturing the final, post-snap pose.
    {
        const f = fixture(); const writes = capturePreferences(f); let queries = 0;
        window.electronScreen = { getCurrentDisplay: async () => { queries++; return { screenX: 0, screenY: 0 }; } };
        f.interaction.isDragging = true; f.interaction.dragMode = 'pan';
        f.interaction._checkAndSwitchDisplay = async () => false;
        f.interaction._snapModelIntoScreen = async () => { f.scene.position.x = 2; };
        await f.interaction._endDrag(); await flush();
        assert.equal(queries, 1); assert.equal(writes[0][1].x, 2);
        f.interaction.cleanupDragAndZoom();
    }


    // Programmatic repositioning owns the model before the next movement frame.
    for (const method of ['setModelPosition', 'resetModelPosition']) {
        const f = fixture(); f.manager.interaction = f.interaction;
        f.manager.currentModel.vrm.scene = f.scene;
        f.manager.setModelScaleScalar = () => {};
        f.select(new THREE.Vector3(0, 5, 0)); await flush();
        if (method === 'setModelPosition') window.VRMManager.prototype[method].call(f.manager, 1, 2, 3);
        else window.VRMManager.prototype[method].call(f.manager);
        const position = f.scene.position.clone();
        f.interaction.update(1 / 60); await flush();
        assert.equal(f.interaction.isMoving, false);
        assert.ok(f.scene.position.equals(position), 'stale target cannot overwrite programmatic position');
        f.interaction.cleanupDragAndZoom();
    }


    // An old drag continuation cannot cancel or save over a newer target.
    for (const stage of ['display', 'hint', 'snap']) {
        const f = fixture(); const pending = deferred();
        f.interaction.isDragging = true; f.interaction.dragMode = 'pan';
        f.interaction._checkAndSwitchDisplay = async () => stage === 'display' ? pending.promise : false;
        f.interaction._recordDragHintPointerEdgeRelease = async () => stage === 'hint' ? pending.promise : false;
        f.interaction._snapModelIntoScreen = async () => stage === 'snap' ? pending.promise : false;
        const ending = f.interaction._endDrag(); await flush();
        f.select(new THREE.Vector3(0, 5, 0)); await flush();
        const token = f.interaction.movementToken; const saves = f.saves.length;
        pending.resolve(false); await ending;
        assert.equal(f.interaction.isMoving, true, stage);
        assert.equal(f.interaction.movementToken, token, stage);
        assert.equal(f.saves.length, saves, 'old drag must not persist over new movement');
        f.interaction.cleanupDragAndZoom(); await flush();
    }


    // The real Electron display query must stop before moving the window if a new target owns the scene.
    for (const stage of ['all-displays', 'current-display']) {
        const f = fixture(); const pending = deferred(); let moves = 0;
        f.scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
        f.manager.currentModel.vrm.scene = f.scene;
        f.manager.renderer.domElement.getBoundingClientRect = () => ({ width: 1920, height: 1080 });
        f.manager.camera.updateMatrixWorld();
        f.interaction._lastPanDragPointerScreen = { x: 2000, y: 500 };
        window.electronScreen = {
            getAllDisplays: async () => stage === 'all-displays' ? pending.promise : [
                { id: 1, screenX: 0, screenY: 0, width: 1920, height: 1080 },
                { id: 2, screenX: 1920, screenY: 0, width: 1920, height: 1080 }],
            getCurrentDisplay: async () => pending.promise,
            moveWindowToDisplay: async () => { moves++; return { success: true }; }
        };
        const checking = f.interaction._checkAndSwitchDisplay(); await flush();
        f.select(new THREE.Vector3(0, 5, 0)); await flush();
        pending.resolve(stage === 'all-displays' ? [{ id: 1 }, { id: 2 }] : { screenX: 0, screenY: 0 });
        assert.equal(await checking, false); assert.equal(moves, 0);
        assert.equal(f.interaction.isMoving, true);
        f.interaction.cleanupDragAndZoom(); await flush();
    }
    // Once Electron moved the window, takeover still reports success without
    // changing or persisting the position owned by the new movement.
    for (const stage of ['ipc', 'frame', 'snap']) {
        const f = fixture(); const pending = deferred(); let successes = 0;
        f.scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
        f.manager.currentModel.vrm.scene = f.scene;
        f.manager.renderer.domElement.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1920, height: 1080 });
        f.manager.camera.updateMatrixWorld();
        if (stage === 'snap') f.scene.position.x = 4;
        else f.interaction._lastPanDragPointerScreen = { x: 2000, y: 500 };
        window.NekoAvatarMultiScreenDragHint = { markDisplaySwitchSuccess() { successes++; } };
        window.electronScreen = {
            getAllDisplays: async () => [
                { id: 1, screenX: 0, screenY: 0, width: 1920, height: 1080 },
                { id: 2, screenX: 1920, screenY: 0, width: 1920, height: 1080 }],
            getCurrentDisplay: async () => ({ screenX: 0, screenY: 0 }),
            moveWindowToDisplay: async () => stage === 'ipc' ? pending.promise : { success: true }
        };
        f.interaction._snapModelIntoScreen = async () => pending.promise;
        const checking = f.interaction._checkAndSwitchDisplay(); await flush();
        if (stage === 'snap') {
            for (const [id, callback] of [...frames]) { frames.delete(id); callback(0); }
            await flush();
        }
        f.select(new THREE.Vector3(0, 5, 0)); await flush();
        const token = f.interaction.movementToken; const saves = f.saves.length;
        f.scene.position.x = 10;
        f.interaction.clampModelPosition = position => { position.x = 1; return position; };
        pending.resolve({ success: true });
        if (stage === 'frame') {
            for (const [id, callback] of [...frames]) { frames.delete(id); callback(0); }
        }
        assert.equal(await checking, true, stage);
        assert.equal(successes, 1); assert.equal(f.scene.position.x, 10);
        assert.equal(f.saves.length, saves);
        assert.equal(f.interaction.isMoving, true); assert.equal(f.interaction.movementToken, token);
        f.interaction.cleanupDragAndZoom(); await flush();
        delete window.NekoAvatarMultiScreenDragHint;
    }

    // A new movement revalidates its own target after either window resize path,
    // without changing the live position, token or persisting an intermediate pose.
    for (const multiWindow of [false, true]) {
        const f = fixture();
        f.select(new THREE.Vector3(5, 0, 0)); await flush();
        const token = f.interaction.movementToken; const saves = f.saves.length;
        const position = f.scene.position.clone();
        window.__NEKO_MULTI_WINDOW__ = multiWindow;
        f.manager.container = { clientWidth: 640, clientHeight: 1080 };
        let resized = false;
        f.manager.renderer.setSize = (width, height) => { assert.equal(width, 640); assert.equal(height, 1080); resized = true; };
        f.manager.interaction = f.interaction;
        f.interaction.clampModelPosition = target => {
            assert.equal(resized, true); assert.equal(f.manager.camera.aspect, 640 / 1080);
            target.x = Math.min(target.x, 1); return target;
        };
        window.VRMManager.prototype.onWindowResize.call(f.manager);
        assert.equal(f.interaction.moveTarget.x, 1);
        assert.ok(f.scene.position.equals(position)); assert.equal(f.saves.length, saves);
        assert.equal(f.interaction.movementToken, token);
        // Arrival also guards targets when a resize notification was missed.
        f.interaction.moveTarget.x = 5; f.scene.position.x = 5;
        f.interaction._updateGuidedMovement(1 / 60); await flush();
        assert.equal(f.interaction.moveTarget.x, 1);
        assert.ok(f.scene.position.x >= 5 - 0.9 / 60, 'revalidated arrival must respect the movement speed bound');
        assert.equal(f.interaction.isMoving, true); assert.equal(f.saves.length, saves);
        assert.equal(f.interaction.movementToken, token);
        f.scene.position.copy(f.interaction.moveTarget);
        f.interaction._updateGuidedMovement(1 / 60); await flush();
        assert.equal(f.scene.position.x, 1); assert.equal(f.interaction.isMoving, false);
        f.interaction.cleanupDragAndZoom(); await flush();
        delete window.__NEKO_MULTI_WINDOW__;
    }

    // A temporarily zero-height canvas cannot poison a valid movement target.
    {
        const f = fixture();
        f.scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
        f.manager.currentModel.vrm.scene = f.scene;
        f.manager.camera.updateMatrixWorld();
        f.manager.renderer.domElement.getBoundingClientRect = () => ({ width: 640, height: 0 });
        window.innerWidth = 640; window.innerHeight = 0;
        const aspect = f.manager.camera.aspect;
        f.manager.renderer.setSize = () => { throw new Error('invalid viewport must not resize renderer'); };
        window.VRMManager.prototype.onWindowResize.call(f.manager);
        assert.equal(f.manager.camera.aspect, aspect, 'invalid resize preserves the previous projection');
        f.interaction.clampModelPosition = window.VRMInteraction.prototype.clampModelPosition;
        f.select(new THREE.Vector3(100, 0, 0)); await flush();
        f.interaction._revalidateMovementTarget();
        assert.ok([f.interaction.moveTarget.x, f.interaction.moveTarget.y, f.interaction.moveTarget.z].every(Number.isFinite));
        assert.equal(f.scene.position.x, 0);
        // A faulty boundary result is ignored rather than copied into the target.
        f.interaction.clampModelPosition = () => new THREE.Vector3(NaN, Infinity, 0);
        f.interaction._revalidateMovementTarget(); assert.equal(f.interaction.moveTarget.x, 100);
        f.interaction.cleanupDragAndZoom(); await flush();
    }
    // An already-invalid target cancels and releases playback, without saving.
    {
        const f = fixture(); f.select(new THREE.Vector3(0, 1, 0)); await flush();
        const saves = f.saves.length; f.interaction.moveTarget.x = NaN;
        f.interaction._updateGuidedMovement(1 / 60); await flush();
        assert.equal(f.interaction.isMoving, false); assert.equal(f.leases.size, 0);
        assert.equal(f.interaction._smoothFacingFrame, null); assert.equal(f.saves.length, saves);
        f.interaction.cleanupDragAndZoom();
    }

    // Idle dragging does not invalidate the interaction token every frame.
    {
        const f = fixture(); f.interaction.isDragging = true;
        const token = f.interaction.movementToken;
        for (let i = 0; i < 60; i++) f.interaction._updateGuidedMovement(1 / 60);
        assert.equal(f.interaction.movementToken, token);
        f.interaction.cleanupDragAndZoom();
    }

    // A queued rebound frame cannot write over a newly selected movement.
    {
        const f = fixture(); const rebound = f.interaction._animateModelToPosition(f.scene.position.clone(), new THREE.Vector3(2, 0, 0));
        f.select(new THREE.Vector3(0, 5, 0)); await flush();
        const position = f.scene.position.clone();
        for (const [id, callback] of [...frames]) { frames.delete(id); callback(360); }
        assert.equal(await rebound, false); assert.ok(f.scene.position.equals(position));
        assert.equal(f.interaction.isMoving, true);
        f.interaction.cleanupDragAndZoom(); await flush();
    }


    // Zero speed cancels both active movement and a subsequently selected trip.
    for (const zeroBeforeSelect of [false, true]) {
        const f = fixture();
        if (zeroBeforeSelect) f.interaction.setMovementSpeed(0);
        f.select(new THREE.Vector3(0, 1, 0)); await flush();
        if (zeroBeforeSelect) f.interaction._updateGuidedMovement(1 / 60);
        else f.interaction.setMovementSpeed(0);
        await flush();
        assert.equal(f.interaction.isMoving, false); assert.equal(f.leases.size, 0);
        assert.equal(f.interaction._smoothFacingFrame, null);
        assert.equal(f.interaction._movementPlaybackAction, null);
        f.interaction.cleanupDragAndZoom();
    }

    // F mode survives enter and hover, and clears on blur or release.
    {
        const f = fixture(); const canvas = f.manager.renderer.domElement;
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', preventDefault() {} });
        f.interaction.mouseEnterHandler(); f.interaction.mouseHoverHandler({ clientX: 0, clientY: 0 });
        assert.equal(canvas.style.cursor, 'crosshair');
        f.interaction._movementBlurHandler();
        assert.equal(f.interaction.targetMode, false); assert.equal(canvas.style.cursor, 'default');
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', preventDefault() {} });
        f.interaction._movementKeyUpHandler({ key: 'f', code: 'KeyF' });
        assert.equal(canvas.style.cursor, 'default');
        f.interaction.cleanupDragAndZoom();
    }

    // IME composing F events must remain available to the input method.
    for (const composing of [{ isComposing: true }, { keyCode: 229 }]) {
        const f = fixture(); let prevented = false;
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', ...composing, preventDefault() { prevented = true; } });
        assert.equal(f.interaction.targetMode, false);
        assert.equal(prevented, false);
        f.interaction._movementKeyDownHandler({ key: 'f', code: 'KeyF', preventDefault() {} });
        assert.equal(f.interaction.targetMode, true, 'ordinary F still selects a target');
        f.interaction.cleanupDragAndZoom();
    }

    // Preference snapshots already accepted for saving survive model switches;
    // request ordering and snapshot isolation use the real core in the dedicated
    // vrm_preferences_persistence.test.cjs regression suite.
    console.log('VRM guided lifecycle: OK (loading, retargeting, arrival, pan/orbit, locking persistence and cleanup)');
})().catch(error => { console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');

function fixture(ids = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamA']) {
    const window = new EventTarget();
    const sandbox = vm.createContext({
        window, PIXI: { live2d: { Live2DModel: class {} } }, console: { log() {}, warn() {}, error() {} },
        performance: { now: () => 50 }, setTimeout, clearTimeout, clearInterval,
    });
    for (const file of ['live2d-core.js', 'live2d-model.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'static/live2d', file), 'utf8'), sandbox, { filename: file });
    }
    const manager = new window.Live2DManager();
    Object.assign(manager, {
        _resolveRuntimeBreathParams: () => [],
        _getNativeRuntimeBreathParamIds: () => new Set(),
        _updateRuntimeBreath() {}, _updateRandomLookAt() {},
        _isEyeBlinkParamId: () => false,
        isAvatarPerformanceCapabilityLocked: () => false,
    });
    const makeModel = (parameterIds = ids) => {
        const values = parameterIds.map(id => id === 'ParamMouthForm' ? -0.2 : 0);
        const rendered = [];
        let motionCalls = 0;
        let coreCalls = 0;
        const core = {
            getParameterIndex: id => parameterIds.indexOf(id),
            getParameterCount: () => parameterIds.length,
            getParameterId: idx => parameterIds[idx],
            getParameterValueByIndex: idx => values[idx],
            getParameterDefaultValueByIndex: () => 0,
            setParameterValueByIndex(idx, value) {
                assert.ok(idx >= 0 && idx < values.length, 'only indices belonging to this model can be written');
                values[idx] = value;
            },
            setParameterValueById(id, value) {
                const idx = parameterIds.indexOf(id);
                if (idx >= 0) values[idx] = value;
            },
            update() {
                coreCalls++;
                rendered.push(Object.fromEntries(parameterIds.map((id, idx) => [id, values[idx]])));
            },
        };
        const motion = {
            state: { currentPriority: 2, currentGroup: 'Test', currentIndex: 0 },
            update() {
                motionCalls++;
                core.setParameterValueById('ParamMouthOpenY', 0.7);
                core.setParameterValueById('ParamMouthForm', -0.2);
            },
        };
        const model = {
            internalModel: { coreModel: core, motionManager: motion }, deltaTime: 16.66,
            destroy() { this.destroyed = true; },
        };
        return { model, core, motion, values, rendered, get coreCalls() { return coreCalls; }, get motionCalls() { return motionCalls; } };
    };
    const mounted = makeModel();
    manager.currentModel = mounted.model;
    manager.installMouthOverride();
    const frame = (target = mounted) => {
        target.motion.update();
        target.core.update();
        return target.rendered.at(-1);
    };
    return { manager, window, mounted, makeModel, frame };
}

test('audio controls opening through silence while expressions retain mouth form', () => {
    const f = fixture();
    const owner = {};
    assert.ok(f.window.LIPSYNC_PARAMS.includes('ParamMouthForm'), 'appearance protection remains broad');
    assert.ok(!f.window.LIPSYNC_AMPLITUDE_PARAMS.includes('ParamMouthForm'));
    f.manager.persistentExpressionParamsByName = { smile: [{ Id: 'ParamMouthForm', Value: -0.4 }] };
    f.manager.beginLipSync(owner);
    f.manager.setMouth(0.6, owner);
    assert.deepEqual(f.frame(), { ParamMouthOpenY: 0.6, ParamMouthForm: -0.4, ParamA: 0.6 });
    f.manager.setMouth(0, owner);
    assert.deepEqual(f.frame(), { ParamMouthOpenY: 0, ParamMouthForm: -0.4, ParamA: 0 });
    assert.equal(f.manager.hasActiveLipSync(), true);
    delete f.manager.persistentExpressionParamsByName.smile;
    assert.deepEqual(f.frame(), { ParamMouthOpenY: 0, ParamMouthForm: -0.2, ParamA: 0 });
    assert.equal(f.manager.endLipSync(owner), true);
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frame().ParamMouthOpenY, 0.7, 'motion regains opening after speech ends');
});

test('old voice updates and ends cannot close the next voice, nor can legacy callers steal it', () => {
    const f = fixture();
    const old = {}, current = {};
    f.manager.beginLipSync(old);
    f.manager.setMouth(0.8, old);
    f.manager.beginLipSync(current);
    f.manager.setMouth(0.4, current);
    assert.equal(f.manager.setMouth(0, old), false);
    assert.equal(f.manager.endLipSync(old), false);
    assert.equal(f.manager.setMouth(0), false);
    assert.equal(f.frame().ParamMouthOpenY, 0.4);
    assert.equal(f.manager.beginLipSync(current), true);
    assert.equal(f.manager.mouthValue, 0.4, 'replaying the same owner is idempotent');
    f.manager.endLipSync(current);
    assert.equal(f.manager.setMouth(0.3), true);
    assert.equal(f.frame().ParamMouthOpenY, 0.3, 'legacy opening calls remain supported');
    assert.equal(f.frame().ParamMouthForm, -0.2);
});

test('click fade interpolation cannot reclaim opening during a quiet speech frame', () => {
    const f = fixture();
    const owner = {};
    f.manager.beginLipSync(owner);
    f.manager.setMouth(0, owner);
    f.manager._clickFadeState = {
        startTime: 0, duration: 100,
        startValues: { ParamMouthOpenY: 1, ParamMouthForm: 0 },
        targetValues: { ParamMouthOpenY: 0.8, ParamMouthForm: -0.8 },
    };
    f.mounted.motion.update();
    assert.equal(f.mounted.values[0], 0, 'physics sees voice opening after click interpolation');
    f.mounted.core.update();
    assert.equal(f.mounted.rendered.at(-1).ParamMouthOpenY, 0);
    assert.ok(Math.abs(f.mounted.rendered.at(-1).ParamMouthForm + 0.7) < 1e-12);
});

test('saved appearance cannot freeze mouth form or opening after responsibility split', () => {
    const f = fixture();
    f.manager.savedModelParameters = { ParamMouthOpenY: 0.9, ParamMouthForm: 0.9 };
    f.manager._shouldApplySavedParams = true;
    f.manager.installMouthOverride();
    const rendered = f.frame();
    assert.equal(rendered.ParamMouthForm, -0.2);
    assert.equal(rendered.ParamMouthOpenY, 0.7);
});

test('teardown rejects late callbacks and releases both wrappers and index caches', async () => {
    const f = fixture();
    const owner = {};
    f.manager.beginLipSync(owner);
    f.manager.setMouth(0.5, owner);
    const oldMotionUpdate = f.mounted.motion.update;
    const oldCoreUpdate = f.mounted.core.update;
    f.manager._expressionAmplitudeIndexCache = { core: f.mounted.core };
    await f.manager.removeModel({ skipCloseWindows: true });
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.manager._cachedMouthIndices, null);
    assert.equal(f.manager._cachedMouthIndicesModel, null);
    assert.equal(f.manager._expressionAmplitudeIndexCache, null);
    const next = f.makeModel(['ParamMouthForm', 'ParamA', 'ParamMouthOpenY']);
    f.manager.currentModel = next.model;
    f.manager.installMouthOverride();
    const newOwner = {};
    f.manager.beginLipSync(newOwner);
    f.manager.setMouth(0.2, newOwner);
    assert.equal(f.manager.setMouth(1, owner), false);
    assert.equal(f.manager.endLipSync(owner), false);
    oldMotionUpdate();
    oldCoreUpdate();
    assert.equal(f.mounted.motionCalls, 0, 'retired motion callback does not execute');
    assert.equal(f.mounted.coreCalls, 0, 'retired core callback does not execute');
    assert.equal(next.values[0], -0.2, 'stale callback cannot write old indices into a new model');
    assert.equal(f.frame(next).ParamMouthOpenY, 0.2);
    assert.equal(f.frame(next).ParamMouthForm, -0.2);
});

test('reinstall retires previously captured wrappers even on the same model', () => {
    const f = fixture();
    const oldCoreUpdate = f.mounted.core.update;
    const oldMotionUpdate = f.mounted.motion.update;
    f.manager.installMouthOverride();
    oldCoreUpdate(); oldMotionUpdate();
    assert.equal(f.mounted.coreCalls, 0);
    assert.equal(f.mounted.motionCalls, 0);
    f.frame();
    assert.equal(f.mounted.coreCalls, 1);
    assert.equal(f.mounted.motionCalls, 1);
});

test('models without standard mouth parameters remain renderable', () => {
    const f = fixture(['CustomUnmappedMouth']);
    const owner = {};
    f.manager.beginLipSync(owner);
    f.manager.setMouth(0.8, owner);
    assert.deepEqual(f.frame(), { CustomUnmappedMouth: 0 });
    assert.equal(f.manager.endLipSync(owner), true);
});

test('reinstall after core failure still restores motion before wrapping it again', () => {
    const f = fixture();
    // core 的错误恢复会先放下 installed 标志，但 motion 包装函数仍在。
    f.manager._mouthOverrideInstalled = false;
    f.manager.installMouthOverride();
    f.frame();
    assert.equal(f.mounted.motionCalls, 1, 'motion must not wrap a retired wrapper');
    assert.equal(f.mounted.coreCalls, 1);
});

test('installing a replacement model never binds old model methods onto the replacement', () => {
    const f = fixture();
    const oldMotionUpdate = f.mounted.motion.update;
    const oldCoreUpdate = f.mounted.core.update;
    const next = f.makeModel(['ParamMouthForm', 'ParamA', 'ParamMouthOpenY']);
    f.manager.currentModel = next.model;
    f.manager.installMouthOverride();
    oldMotionUpdate(); oldCoreUpdate();
    f.frame(next);
    assert.equal(f.mounted.motionCalls, 0);
    assert.equal(f.mounted.coreCalls, 0);
    assert.equal(next.motionCalls, 1);
    assert.equal(next.coreCalls, 1);
});

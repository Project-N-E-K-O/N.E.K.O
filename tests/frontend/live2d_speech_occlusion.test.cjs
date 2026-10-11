const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { EventEmitter } = require('node:events');

const modelPath = '/static/yui-origin/yui-origin.model3.json';
const defaultIds = ['ParamMouthOpenY', 'ParamMouthForm', 'Param71', 'Param72', 'Param78', 'ParamAngleX'];

function fixture(options = {}) {
    let now = 50;
    const window = new EventTarget();
    if (options.location !== false) window.location = { href: 'http://localhost:48921/' };
    const sandbox = vm.createContext({
        window, URL, PIXI: { live2d: { Live2DModel: class {} } },
        console: { log() {}, warn() {}, error() {} }, performance: { now: () => now },
        setTimeout, clearTimeout, clearInterval, cancelAnimationFrame() {},
    });
    for (const file of ['live2d-core.js', 'live2d-model.js', 'live2d-emotion.js']) {
        const sourcePath = path.resolve(__dirname, '../../static/live2d', file);
        vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), sandbox, { filename: sourcePath });
    }
    const manager = new window.Live2DManager();
    Object.assign(manager, {
        _resolveRuntimeBreathParams: () => [], _getNativeRuntimeBreathParamIds: () => new Set(),
        _updateRuntimeBreath() {}, _updateRandomLookAt() {}, _isEyeBlinkParamId: () => false,
        isAvatarPerformanceCapabilityLocked: () => false, _scheduleReinstallOverride() {},
    });
    const makeModel = (ids = options.ids || defaultIds) => {
        const values = ids.map(() => 0);
        const rendered = [];
        const writes = [];
        const core = {
            getParameterCount: () => ids.length, getParameterId: i => ids[i],
            getParameterIndex: id => ids.indexOf(id) < 0 && options.virtualMissing ? ids.length : ids.indexOf(id),
            getParameterDefaultValueByIndex: () => 0,
            getParameterValueByIndex: i => values[i], getParameterValueById: id => values[ids.indexOf(id)],
            setParameterValueByIndex(i, value) {
                assert.ok(i >= 0 && i < ids.length, 'never write a missing or retired model index');
                writes.push([ids[i], value]); values[i] = value;
            },
            setParameterValueById(id, value) { const i = ids.indexOf(id); if (i >= 0) this.setParameterValueByIndex(i, value); },
            update() {
                rendered.push(Object.fromEntries(ids.map((id, i) => [id, values[i]])));
                if (options.onUpdate) options.onUpdate(manager, core);
                if (options.failUpdate) throw new Error('injected Core failure');
            },
        };
        const internalModel = new EventEmitter();
        Object.assign(internalModel, { coreModel: core, motionManager: { update() {}, state: {} } });
        const model = { internalModel, deltaTime: 16.66, expression: async () => false, destroy() {} };
        return { model, core, rendered, values, writes };
    };
    const mounted = makeModel();
    manager.currentModel = mounted.model;
    manager._lastLoadedModelPath = options.path === undefined ? modelPath : options.path;
    manager.installMouthOverride();
    manager.recordInitialParameters();
    const frame = (target = mounted) => {
        target.model.internalModel.emit('beforeModelUpdate');
        target.model.internalModel.motionManager.update();
        target.core.update();
        return target.rendered.at(-1);
    };
    return { manager, mounted, frame, makeModel, advance: value => { now = value; } };
}

for (const id of ['Param71', 'Param72']) {
    for (const order of ['expression-first', 'speech-first']) {
        test(`${id}: ${order}, silence stays masked and speech end restores current expression`, () => {
            const f = fixture(), owner = {};
            if (order === 'speech-first') f.manager.beginLipSync(owner);
            f.manager.persistentExpressionParamsByName = { resident: [{ Id: id, Value: 1 }, { Id: 'ParamAngleX', Value: 12 }] };
            assert.equal(f.frame()[id], order === 'speech-first' ? 0 : 1);
            f.manager.beginLipSync(owner);
            for (const volume of [0.6, 0, 0.8]) {
                f.manager.setMouth(volume, owner);
                const rendered = f.frame();
                assert.equal(rendered[id], 0);
                assert.equal(rendered.ParamMouthOpenY, volume);
                assert.equal(rendered.ParamAngleX, 12, 'other expression parameters keep running');
                assert.equal(f.mounted.core.getParameterValueById(id), 1, 'authoring state survives each render');
            }
            f.manager.endLipSync(owner);
            assert.equal(f.frame()[id], 1);
        });
    }
}

test('z3 and mouth form remain visible during speech', () => {
    const f = fixture(), owner = {};
    f.manager.persistentExpressionParamsByName = { z3: [{ Id: 'Param78', Value: 1 }, { Id: 'ParamMouthForm', Value: -0.7 }] };
    f.manager.beginLipSync(owner); f.manager.setMouth(0.5, owner);
    const rendered = f.frame();
    assert.equal(rendered.Param78, 1); assert.equal(rendered.ParamMouthForm, -0.7);
});

test('switching and clearing a resident expression during speech never restores an old overlay', async () => {
    const f = fixture(), owner = {};
    f.manager.persistentExpressionNames = ['z1'];
    f.manager.persistentExpressionParamsByName = { z1: [{ Id: 'Param71', Value: 1 }] };
    await f.manager.applyPersistentExpressionsNative();
    f.manager.beginLipSync(owner); f.frame();
    f.manager.teardownPersistentExpressions();
    f.manager.persistentExpressionNames = ['z2'];
    f.manager.persistentExpressionParamsByName = { z2: [{ Id: 'Param72', Value: 1 }] };
    await f.manager.applyPersistentExpressionsNative(); f.frame();
    f.manager.endLipSync(owner);
    assert.equal(f.frame().Param71, 0); assert.equal(f.frame().Param72, 1);
    f.manager.beginLipSync(owner); f.frame();
    f.manager.teardownPersistentExpressions(); f.frame(); f.manager.endLipSync(owner);
    assert.equal(f.frame().Param72, 0);
});

test('manual fallback continues fading underneath mask and clear remains effective', async () => {
    const f = fixture(), owner = {};
    f.manager.beginLipSync(owner);
    f.manager._activeExpressionParamIds = new Set(['Param71']);
    f.manager._installManualExpressionOverride([{ Id: 'Param71', Value: 1 }], 100);
    f.advance(100); assert.equal(f.frame().Param71, 0);
    assert.ok(f.mounted.core.getParameterValueById('Param71') > 0, 'manual expression still evaluates');
    await f.manager.clearExpression(); f.manager.endLipSync(owner);
    assert.equal(f.frame().Param71, 0);
});

test('native/transient expression latest value and natural expiry are not snapshotted at speech start', () => {
    const f = fixture(), owner = {};
    f.manager.beginLipSync(owner);
    // Native expressions evaluate before the final Core update; exercise its current frame output.
    for (const value of [0, 0.25, 1, 0.6, 0]) {
        f.mounted.core.setParameterValueById('Param71', value);
        assert.equal(f.frame().Param71, 0);
        assert.equal(f.mounted.core.getParameterValueById('Param71'), value);
    }
    f.manager.endLipSync(owner); assert.equal(f.frame().Param71, 0, 'expired transient is not resurrected');
});

test('click fades and temporary pose are evaluated before masking', () => {
    const f = fixture(), owner = {};
    f.manager.beginLipSync(owner);
    f.manager._clickFadeState = { startTime: 0, duration: 100, startValues: { Param71: 0 }, targetValues: { Param71: 1 } };
    assert.equal(f.frame().Param71, 0);
    assert.ok(f.mounted.core.getParameterValueById('Param71') > 0);
    f.manager._applyTemporaryPoseOverride = core => core.setParameterValueById('Param72', 0.8);
    assert.equal(f.frame().Param72, 0);
    assert.equal(f.mounted.core.getParameterValueById('Param72'), 0.8);
});

test('old voice end cannot release the mask owned by the next voice', () => {
    const f = fixture(), old = {}, current = {};
    f.mounted.core.setParameterValueById('Param72', 1);
    f.manager.beginLipSync(old); f.manager.beginLipSync(current);
    assert.equal(f.manager.endLipSync(old), false);
    assert.equal(f.frame().Param72, 0);
    f.manager.endLipSync(current); assert.equal(f.frame().Param72, 1);
});

for (const otherPath of [undefined, '/user_live2d/yui-origin/yui-origin.model3.json', '/static/yui-lolita/yui-lolita.model3.json',
    '/static/yui-origin/custom.model3.json', 'https://example.com/static/yui-origin/yui-origin.model3.json', 'invalid://[']) {
    test(`unrecognized model path is unaffected: ${otherPath}`, () => {
        const f = fixture({ path: otherPath === undefined ? null : otherPath }), owner = {};
        f.mounted.core.setParameterValueById('Param71', 1); f.manager.beginLipSync(owner);
        assert.equal(f.frame().Param71, 1);
    });
}

for (const localPath of [modelPath + '?v=1#model', 'http://localhost:48921' + modelPath]) {
    test(`bundled same-origin URL supports cache busting: ${localPath}`, () => {
        const f = fixture({ path: localPath }); f.mounted.core.setParameterValueById('Param71', 1);
        f.manager.beginLipSync({}); assert.equal(f.frame().Param71, 0);
    });
}

test('legacy mouth updates alone do not imply a whole speech lifecycle', () => {
    const f = fixture(); f.mounted.core.setParameterValueById('Param71', 1); f.manager.setMouth(0.8);
    assert.equal(f.frame().Param71, 1);
});

test('non-browser fixture only recognizes the exact bundled relative URL', () => {
    for (const url of [modelPath, '/custom/yui-origin.model3.json']) {
        const f = fixture({ location: false, path: url });
        f.mounted.core.setParameterValueById('Param71', 1); f.manager.beginLipSync({});
        assert.equal(f.frame().Param71, url === modelPath ? 0 : 1);
    }
});

for (const ids of [['Param71', 'Param72'], ['ParamMouthOpenY', 'Param71']]) {
    test(`missing physical mouth/overlay parameters remain compatible: ${ids}`, () => {
        const f = fixture({ ids, virtualMissing: true }); f.manager.beginLipSync({});
        f.mounted.core.setParameterValueById('Param71', 1);
        assert.equal(f.frame().Param71, ids.includes('ParamMouthOpenY') ? 0 : 1);
    });
}

test('a failed Core update restores parameter state and triggers existing cleanup', () => {
    const f = fixture({ failUpdate: true }); f.mounted.core.setParameterValueById('Param71', 0.75);
    f.mounted.core.setParameterValueById('Param72', 0.4); f.manager.beginLipSync({}); f.frame();
    assert.equal(f.mounted.core.getParameterValueById('Param71'), 0.75);
    assert.equal(f.mounted.core.getParameterValueById('Param72'), 0.4);
    assert.equal(f.manager._mouthOverrideInstalled, false);
    assert.equal(f.mounted.rendered[0].Param71, 0, 'failure was injected after masking reached Core');
});

test('partial masking failure restores already-written parameters', () => {
    const f = fixture();
    f.mounted.core.setParameterValueById('Param71', 0.75);
    f.mounted.core.setParameterValueById('Param72', 0.4);
    const set = f.mounted.core.setParameterValueByIndex;
    let failed = false;
    f.mounted.core.setParameterValueByIndex = function(index, value) {
        if (!failed && index === 3 && value === 0) { failed = true; throw new Error('injected parameter failure'); }
        return set.call(this, index, value);
    };
    f.manager.beginLipSync({}); f.frame();
    assert.ok(failed);
    assert.equal(f.mounted.core.getParameterValueById('Param71'), 0.75);
    assert.equal(f.mounted.core.getParameterValueById('Param72'), 0.4);
    assert.equal(f.manager._mouthOverrideInstalled, false);
});

test('model replacement retires cached indices and captured old callbacks', () => {
    const f = fixture(); const stale = f.mounted.core.update;
    f.manager.beginLipSync({}); f.mounted.core.setParameterValueById('Param71', 1);
    const next = f.makeModel([...defaultIds].reverse()); f.manager.currentModel = next.model; f.manager.installMouthOverride();
    next.core.setParameterValueById('Param72', 1); const before = f.mounted.writes.length;
    stale(); assert.equal(f.mounted.writes.length, before);
    assert.equal(f.frame(next).Param72, 0); assert.equal(next.core.getParameterValueById('Param72'), 1);
});

test('reentrant model replacement skips restoration writes to the retired Core', () => {
    let writesAtReplacement;
    const f = fixture({ onUpdate(manager, core) {
        writesAtReplacement = f.mounted.writes.length;
        manager.currentModel = null;
    } });
    f.manager.beginLipSync({}); f.mounted.core.setParameterValueById('Param71', 1); f.frame();
    assert.equal(f.mounted.writes.length, writesAtReplacement);
});

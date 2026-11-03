const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { loadRuntime, createMao, compareModels } = require('./fixtures/live2d_runtime_fixture.cjs');

const runtimePromise = loadRuntime();
async function withMao(callback) {
    const fixture = createMao(await runtimePromise);
    try { await callback(fixture); } finally { fixture.release(); }
}

test('default source validation preserves numeric strings and zero without changing other range readers', async () => {
    const runtime = await runtimePromise;
    const manager = new runtime.context.Live2DManager();
    for (const method of ['getParameterDefaultValueByIndex', 'getParameterDefaultValue',
        'getParamDefaultValue', 'getParamDefault']) {
        for (const [raw, expected] of [[0, 0], ['0.75', .75], [' 0 ', 0], [null, undefined],
            [false, undefined], [true, undefined], ['', undefined], [' ', undefined],
            [undefined, undefined], [NaN, undefined], [Infinity, undefined]]) {
            assert.equal(manager._getCoreParameterDefaultValue({ [method]() { return raw; } }, 0), expected);
        }
        assert.equal(manager._getCoreParameterDefaultValue({ [method]() { throw new Error('missing'); },
            parameters: { defaults: ['0.25'] } }, 0), .25);
    }
    const roots = [value => value, value => ({ parameters: value }), value => ({ _model: value }),
        value => ({ _model: { parameters: value } }), value => ({ model: value }),
        value => ({ model: { parameters: value } })];
    for (const wrap of roots) for (const key of ['defaultValues', 'defaults', '_parameterDefaultValues']) {
        assert.equal(manager._getCoreParameterDefaultValue(wrap({ [key]: ['0.75'] }), 0), .75);
        assert.equal(manager._getCoreParameterDefaultValue(wrap({ [key]: [null] }), 0), undefined);
    }
    assert.equal(manager._readParameterValueByIndex({ getParamMin() { return null; } },
        0, ['getParamMin'], [], -1), 0, 'non-default callers retain conversion behavior');
});

test('actual SDK getter supplies a cached saved offset and idle restoration', () => withMao(f => {
    assert.equal(typeof f.core.getParameterDefaultValueByIndex, 'undefined');
    f.apply({ ParamAngleX: 2 });
    const reads = f.defaultReads;
    assert.ok(reads > 0, 'must call the real bundled getter during preparation');
    f.writes = { ParamAngleX: 4 };
    for (let frame = 0; frame < 30; frame++) {
        f.motionManager.update(frame / 30);
        assert.equal(f.value('ParamAngleX'), 6);
    }
    f.writes = {};
    f.core.setParameterValueById('ParamAngleX', 7);
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 2);
    assert.equal(f.defaultReads, reads, 'steady updates never reread default metadata');
}));

test('nonzero model defaults are offsets and require explicit reinstall after metadata changes', () => withMao(f => {
    const index = f.core.getParameterIndex('ParamAngleX');
    f.defaultOverrides.set(index, 1);
    f.apply({ ParamAngleX: 3 });
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 6);
    f.defaultOverrides.set(index, 2);
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 6, 'live metadata is not watched each frame');
    f.manager.installMouthOverride();
    f.motionManager.update(2);
    assert.equal(f.value('ParamAngleX'), 5);
}));

test('zero offset is retained for frames without motion changes', () => withMao(f => {
    f.apply({ ParamAngleX: 0 });
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 4);
    f.writes = {};
    f.core.setParameterValueById('ParamAngleX', 7);
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 0);
}));

test('held and slowly changing motion values retain control over saved defaults', () => withMao(f => {
    f.apply({ ParamAngleX: 0 });
    for (const value of [4, 4, 4, 4.0004, 4.0008, 4.0008, 4.0012]) {
        f.core.loadParameters();
        f.writes = { ParamAngleX: value };
        f.motionManager.update(0);
        f.core.saveParameters();
        assert.ok(Math.abs(f.value('ParamAngleX') - value) < 1e-6);
    }
    f.writes = {};
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 0, 'release restores the saved default');
}));

test('saved offsets stay stable when motion reaches a parameter limit', () => withMao(f => {
    f.apply({ ParamAngleX: 2 });
    f.writes = { ParamAngleX: 30 };
    for (let frame = 0; frame < 30; frame++) {
        f.core.loadParameters();
        f.motionManager.update(frame / 30);
        f.core.saveParameters();
        assert.equal(f.value('ParamAngleX'), 30);
    }
    f.writes = {};
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 2);
}));

test('real SDK constant curves preserve saved offsets until the motion stops', () => withMao(f => {
    f.apply({ ParamAngleX: 0, ParamAngleY: 2 });
    f.queue = new f.runtime.sdk.Queue();
    const motion = f.runtime.sdk.Motion.create({ Version: 3,
        Meta: { Duration: 2, Fps: 30, Loop: false, AreBeziersRestricted: true,
            FadeInTime: 0, FadeOutTime: 0, CurveCount: 2,
            TotalSegmentCount: 2, TotalPointCount: 4 },
        Curves: ['ParamAngleX', 'ParamAngleY'].map(id => ({
            Target: 'Parameter', Id: id, Segments: [0, 30, 0, 2, 30]
        })) });
    motion.setEffectIds([], []);
    f.queue.startMotion(motion, false, 0);
    for (let frame = 0; frame < 30; frame++) {
        f.core.loadParameters();
        f.motionManager.update(frame / 30);
        f.core.saveParameters();
        assert.equal(f.value('ParamAngleX'), 30);
        assert.equal(f.value('ParamAngleY'), 30);
    }
    f.queue.stopAllMotions();
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 0);
    assert.equal(f.value('ParamAngleY'), 2);
}));

for (const savedOffset of [2, 0.1, -2]) {
    test(`real SDK fade composes saved offset ${savedOffset} once`, () => withMao(f => {
        const reference = createMao(f.runtime);
        try {
            f.apply({ ParamAngleX: savedOffset });
            reference.apply({});
            for (const fixture of [f, reference]) {
                fixture.queue = new fixture.runtime.sdk.Queue();
                const motion = fixture.runtime.sdk.Motion.create({ Version: 3,
                    Meta: { Duration: 2, Fps: 30, Loop: false, AreBeziersRestricted: true,
                        FadeInTime: 0.5, FadeOutTime: 0.5, CurveCount: 1,
                        TotalSegmentCount: 1, TotalPointCount: 2 },
                    Curves: [{ Target: 'Parameter', Id: 'ParamAngleX', Segments: [0, 4, 0, 2, 4] }] });
                motion.setEffectIds([], []);
                fixture.queue.startMotion(motion, false, 0);
                fixture.core.saveParameters();
            }
            for (let frame = 0; frame < 60; frame++) {
                for (const fixture of [f, reference]) {
                    fixture.core.loadParameters();
                    fixture.motionManager.update(frame / 30);
                    fixture.core.saveParameters();
                }
                assert.ok(Math.abs(f.value('ParamAngleX') - reference.value('ParamAngleX') - savedOffset) < 1e-5,
                    `frame ${frame}: saved offset must be composed once`);
            }
            f.queue.stopAllMotions();
            f.motionManager.update(2);
            assert.ok(Math.abs(f.value('ParamAngleX') - savedOffset) < 1e-6);
        } finally { reference.release(); }
    }));
}

test('motion write recording releases the Core setter after success and exceptions', () => withMao(f => {
    f.apply({ ParamAngleX: 0 });
    const setter = f.core.setParameterValueByIndex;
    assert.equal(Object.hasOwn(f.core, 'setParameterValueByIndex'), false);
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    assert.equal(f.core.setParameterValueByIndex, setter);
    assert.equal(Object.hasOwn(f.core, 'setParameterValueByIndex'), false);

    f.core.setParameterValueByIndex = function(...args) { return setter.apply(this, args); };
    const ownSetter = f.core.setParameterValueByIndex;
    f.afterMotion = () => { throw new Error('motion update failed'); };
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 4, 'writes before the error still belong to motion');
    assert.equal(f.core.setParameterValueByIndex, ownSetter);
    assert.equal(Object.hasOwn(f.core, 'setParameterValueByIndex'), true);
}));

test('canonical and index aliases install only one offset and invalid indices stay outside Core', () => withMao(f => {
    const index = f.core.getParameterIndex('ParamAngleX');
    f.manager.savedModelParameters = { [`param_${index}`]: 5, ParamAngleX: 2,
        UnknownSavedParameter: 9, param_999999: 1 };
    f.manager._shouldApplySavedParams = true;
    f.manager.installMouthOverride();
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 6);
    assert.equal(f.manager.savedModelParameters.UnknownSavedParameter, 9, 'runtime rejection preserves saved data');
}));

test('missing defaults skip one runtime entry and produce one bounded install diagnostic', () => withMao(f => {
    const index = f.core.getParameterIndex('ParamAngleX');
    f.defaultOverrides.set(index, NaN);
    f.raw.parameters.defaultValues[index] = NaN;
    const start = f.runtime.warnings.length;
    f.apply({ ParamAngleX: 2, ParamAngleY: 3 });
    const diagnostics = f.runtime.warnings.slice(start).filter(args => String(args[0]).includes('SavedParameters'));
    assert.equal(diagnostics.length, 1);
    assert.ok(JSON.stringify(diagnostics[0]).length < 1500, 'diagnostic details must be bounded');
    f.writes = { ParamAngleX: 4, ParamAngleY: 4 };
    for (let frame = 0; frame < 30; frame++) {
        f.motionManager.update(frame / 30);
        f.core.update();
    }
    assert.equal(f.value('ParamAngleX'), 4);
    assert.equal(f.value('ParamAngleY'), 7);
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 2);
    assert.equal(f.coreCalls, 30);
    assert.equal(f.runtime.warnings.slice(start).filter(args => String(args[0]).includes('SavedParameters')).length, 1);
}));

test('empty saved config and editor mode keep their existing parameter ownership', () => withMao(f => {
    f.apply({});
    let reads = f.defaultReads;
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 4);
    assert.equal(f.defaultReads, reads);
    f.manager._parameterEditingMode = true;
    f.apply({ ParamAngleX: 2 });
    reads = f.defaultReads;
    f.writes = {};
    f.core.setParameterValueById('ParamAngleX', 7);
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 7);
    assert.equal(f.defaultReads, reads);
}));

test('mouth, breath, blink and visibility remain runtime channels', () => withMao(f => {
    f.apply({ ParamMouthOpenY: .8, ParamMouthForm: .8, ParamBreath: .8,
        ParamEyeLOpen: .1, ParamOpacity: .1 });
    const reads = f.defaultReads;
    f.writes = { ParamMouthOpenY: .3, ParamMouthForm: .2, ParamBreath: .4, ParamEyeLOpen: .7 };
    f.motionManager.update(0);
    assert.ok(Math.abs(f.value('ParamMouthOpenY') - .3) < 1e-6);
    assert.ok(Math.abs(f.value('ParamMouthForm') - .2) < 1e-6);
    assert.ok(Math.abs(f.value('ParamEyeLOpen') - .7) < 1e-6);
    assert.equal(f.defaultReads, reads);
}));

test('persistent protections stay dynamic after installation and click fade temporarily owns saved values', () => withMao(f => {
    f.apply({ ParamAngleX: 2 });
    f.writes = { ParamAngleX: 4 };
    f.manager.persistentExpressionParamsByName = { protection: [{ Id: 'ParamAngleX', Value: 1 }] };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 1);
    f.manager.persistentExpressionParamsByName = {};
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 6);
    f.manager._clickFadeState = { startTime: 0, duration: 100,
        startValues: { ParamAngleX: 0 }, targetValues: { ParamAngleX: 10 } };
    f.runtime.context.clock = 50;
    f.motionManager.update(2);
    assert.equal(f.value('ParamAngleX'), 8.75);
    f.runtime.context.clock = 100;
    f.motionManager.update(3);
    assert.equal(f.value('ParamAngleX'), 10);
    assert.equal(f.manager._clickFadeState, null);
    f.motionManager.update(4);
    assert.equal(f.value('ParamAngleX'), 6);
}));

test('refresh and model replacement retire old wrappers before their SDK callbacks', () => withMao(f => {
    f.apply({ ParamAngleX: 2 });
    const oldMotion = f.motionManager.update, oldCore = f.core.update;
    f.apply({ ParamAngleX: 3 });
    const calls = [f.motionCalls, f.coreCalls];
    oldMotion(0); oldCore();
    assert.deepEqual([f.motionCalls, f.coreCalls], calls);
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleX'), 7);
    const other = createMao(f.runtime);
    try {
        const retiredMotion = f.motionManager.update;
        f.manager.currentModel = other.model;
        f.manager._applyEffectiveModelParameters(other.model, {}, { ParamAngleX: 5 });
        retiredMotion(2);
        assert.equal(other.value('ParamAngleX'), 5);
    } finally { other.release(); }
}));

test('takeover inside original motion callback prevents stale saved offset commits', () => withMao(f => {
    const other = createMao(f.runtime);
    try {
        f.apply({ ParamAngleX: 2 });
        f.writes = { ParamAngleX: 4 };
        f.afterMotion = () => { f.manager.currentModel = other.model; };
        f.motionManager.update(0);
        assert.equal(f.value('ParamAngleX'), 4);
        assert.equal(other.value('ParamAngleX'), 0);
    } finally { other.release(); }
}));

test('model removal cancels scheduled reinstall and retires captured update wrappers', () => withMao(async f => {
    f.apply({ ParamAngleX: 2 });
    const motion = f.motionManager.update, core = f.core.update;
    f.manager._scheduleReinstallOverride();
    const handle = f.manager._reinstallTimer;
    await f.manager.removeModel({ skipCloseWindows: true });
    assert.equal(handle.cancelled, true);
    const calls = [f.motionCalls, f.coreCalls];
    motion(0); core();
    assert.deepEqual([f.motionCalls, f.coreCalls], calls);
    assert.equal(f.manager.currentModel, null);
}));

test('restoring the absent ByIndex getter mutation fails the real SDK saved-offset assertion', async () => {
    const marker = 'const offset = entry.offset;';
    const runtime = await loadRuntime({ transformModelSource(source) {
        assert.equal(source.split(marker).length, 2);
        return source.replace(marker, 'const offset = entry.value - coreModel.getParameterDefaultValueByIndex(entry.idx);');
    } });
    const f = createMao(runtime);
    try {
        f.apply({ ParamAngleX: 2 });
        f.writes = { ParamAngleX: 4 };
        f.motionManager.update(0);
        assert.throws(() => assert.equal(f.value('ParamAngleX'), 6), { code: 'ERR_ASSERTION' });
        assert.equal(f.value('ParamAngleX'), 4, 'mutation must reach the failure path');
    } finally { f.release(); }
});

test('every tracked Mao motion matches the existing algorithm with its correct SDK getter', async () => {
    const marker = 'const offset = entry.offset;';
    const referenceRuntime = await loadRuntime({ transformModelSource(source) {
        assert.equal(source.split(marker).length, 2);
        return source.replace(marker, 'const offset = entry.value - coreModel.getParameterDefaultValue(entry.idx);');
    } });
    const fixtures = [createMao(await runtimePromise), createMao(referenceRuntime)];
    const files = [...new Set(Object.values(fixtures[0].settings.FileReferences.Motions)
        .flat().map(motion => motion.File))];
    assert.ok(files.length >= 7);
    try {
        for (const f of fixtures) {
            f.queue = new f.runtime.sdk.Queue();
            f.physics = f.runtime.sdk.Physics.create(JSON.parse(fs.readFileSync(
                path.join(f.root, f.settings.FileReferences.Physics), 'utf8')));
            const saved = Object.fromEntries(Array.from(f.raw.parameters.ids,
                (id, index) => [id, f.raw.parameters.defaultValues[index]]));
            saved.ParamAllColor1 = .8;
            f.apply(saved);
        }
        let frame = 0;
        for (const file of files) {
            const json = JSON.parse(fs.readFileSync(path.join(fixtures[0].root, file), 'utf8'));
            for (const f of fixtures) {
                f.queue.stopAllMotions();
                const motion = f.runtime.sdk.Motion.create(json);
                motion.setEffectIds([], []);
                motion.setIsLoop(true);
                f.queue.startMotion(motion, false, frame / 30);
            }
            const frames = Math.ceil(json.Meta.Duration * 30) + 2;
            for (let local = 0; local < frames; local++, frame++) {
                for (const f of fixtures) {
                    f.core.loadParameters();
                    f.motionManager.update(frame / 30);
                    f.core.saveParameters();
                    f.physics.evaluate(f.core, 1 / 30);
                    f.core.update();
                }
                compareModels(fixtures[0], fixtures[1], `${file}, frame ${local}`);
            }
        }
    } finally { fixtures.forEach(f => f.release()); }
});

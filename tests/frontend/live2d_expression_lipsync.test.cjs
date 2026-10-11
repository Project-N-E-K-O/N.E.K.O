const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/live2d/live2d-emotion.js'), 'utf8');
const FORM = 'ParamMouthForm';
const OPEN = 'ParamMouthOpenY';
const parameters = [{ Id: FORM, Value: -0.4 }, { Id: OPEN, Value: 0.95 }];
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function createModel(expression = async () => false) {
    const ids = [FORM, OPEN, 'ParamAngleX'];
    const values = [0.2, 0.6, 0];
    const writes = [];
    const coreModel = {
        getParameterCount: () => ids.length,
        getParameterId: index => ids[index],
        getParameterIndex: id => ids.indexOf(id),
        getParameterValueByIndex: index => values[index],
        getParameterValueById: id => values[ids.indexOf(id)],
        setParameterValueByIndex(index, value) { values[index] = value; writes.push([ids[index], value]); },
        setParameterValueById(id, value) { this.setParameterValueByIndex(ids.indexOf(id), value); },
    };
    const internalModel = new EventEmitter();
    internalModel.coreModel = coreModel;
    internalModel.motionManager = { expressionManager: { stopAllExpressions() {}, reserveExpressionIndex: -1 } };
    return { internalModel, expression, values, writes };
}
function fixture(options = {}) {
    let now = 0;
    class Live2DManager {}
    const context = vm.createContext({
        Live2DManager, Set, Promise, console: { log() {}, warn() {}, error() {} },
        window: { LIPSYNC_PARAMS: [OPEN, FORM], LIPSYNC_AMPLITUDE_PARAMS: [OPEN] },
        performance: { now: () => now },
        fetch: options.fetch || (async () => ({ ok: true, json: async () => ({ Parameters: parameters }) })),
        cancelAnimationFrame() {}, setTimeout: options.setTimeout || setTimeout, clearTimeout,
    });
    vm.runInContext(source, context);
    const manager = new Live2DManager();
    manager.currentModel = createModel(options.expression);
    manager.persistentExpressionNames = [];
    manager.persistentExpressionParamsByName = {};
    manager._persistentParamsBackup = {};
    manager._isRuntimeManagedAppearanceParam = id => context.window.LIPSYNC_PARAMS.includes(id);
    manager._resolveModelParameterKey = (core, id) => ({ idx: core.getParameterIndex(id), resolvedId: id });
    manager.resolveAssetPath = file => file;
    manager.resolveExpressionReferenceByFile = file => ({ name: file.replace('.exp3.json', ''), file });
    manager.hasActiveLipSync = () => manager.speaking === true;
    manager.recordInitialParameters();
    return { manager, context, advance: value => { now = value; } };
}
function resident(manager) {
    manager.persistentExpressionNames = ['resident'];
    manager.persistentExpressionParamsByName = { resident: parameters };
}

test('expression baseline records shape while saved appearance and amplitude protections remain', () => {
    const { manager } = fixture();
    assert.equal(manager.initialParameters[FORM], 0.2);
    assert.equal(manager.initialParameters[OPEN], undefined);
    assert.equal(manager.appearanceBaselineParameters[FORM], undefined);
    assert.equal(manager._getActiveExpressionParamIds().has(FORM), false);
    assert.equal(manager._getActiveExpressionParamIds().has(OPEN), true);
});

test('manual expression controls shape but preserves speech opening; clear restores shape only', async () => {
    const { manager, advance } = fixture();
    manager.speaking = true;
    manager._activeExpressionParamIds = new Set([FORM, OPEN]);
    manager._installManualExpressionOverride(parameters, 50);
    advance(50);
    manager.currentModel.internalModel.emit('beforeModelUpdate');
    assert.ok(Math.abs(manager.currentModel.values[0] + 0.4) < 1e-12);
    assert.equal(manager.currentModel.values[1], 0.6);
    await manager.clearExpression();
    assert.equal(manager.currentModel.values[0], 0.2);
    assert.equal(manager.currentModel.values[1], 0.6);
    assert.equal(manager.currentModel.internalModel.listenerCount('beforeModelUpdate'), 0);
});

for (const mode of ['false', 'reject', 'missing']) {
    test(`resident ${mode} fallback backs up/restores shape without touching opening`, async () => {
        const { manager } = fixture({ expression: mode === 'reject' ? async () => { throw new Error('load failed'); } : async () => false });
        if (mode === 'missing') manager.currentModel.expression = undefined;
        manager.speaking = true;
        resident(manager);
        await manager.applyPersistentExpressionsNative();
        assert.equal(manager.currentModel.values[0], -0.4);
        assert.equal(manager.currentModel.values[1], 0.6);
        assert.equal(manager._persistentParamsBackup[FORM], 0.2);
        assert.equal(manager._persistentParamsBackup[OPEN], undefined);
        manager.teardownPersistentExpressions();
        assert.equal(manager.currentModel.values[0], 0.2);
        assert.equal(manager.currentModel.values[1], 0.6);
    });
}

test('clearing a transient expression preserves resident shape and current speech opening', async () => {
    const { manager } = fixture();
    resident(manager);
    await manager.applyPersistentExpressionsNative();
    manager.speaking = true;
    manager._activeExpressionParamIds = new Set([FORM, OPEN]);
    await manager.clearExpression();
    assert.equal(manager.currentModel.values[0], -0.4);
    assert.equal(manager.currentModel.values[1], 0.6);
});

test('retired resident backup cannot restore old values into a new model', async () => {
    const { manager } = fixture();
    resident(manager);
    await manager.applyPersistentExpressionsNative();
    manager.currentModel = createModel();
    manager.currentModel.values[0] = 0.8;
    manager.teardownPersistentExpressions();
    assert.equal(manager.currentModel.values[0], 0.8);
    assert.equal(manager.currentModel.writes.length, 0);
});

for (const action of ['teardown', 'model-change', 'new-apply']) {
    for (const result of ['false', 'reject']) {
        test(`late resident ${result} after ${action} cannot write retired state`, async () => {
            const gate = deferred();
            const entered = deferred();
            const { manager } = fixture({ expression: () => { entered.resolve(); return gate.promise; } });
            const oldModel = manager.currentModel;
            resident(manager);
            const task = manager.applyPersistentExpressionsNative();
            await entered.promise;
            let successor;
            if (action === 'teardown') manager.teardownPersistentExpressions();
            if (action === 'model-change') manager.currentModel = createModel();
            if (action === 'new-apply') {
                oldModel.expression = async () => false;
                manager.persistentExpressionParamsByName.resident = [{ Id: FORM, Value: 0.7 }];
                successor = manager.applyPersistentExpressionsNative(true);
            }
            const before = oldModel.writes.length;
            result === 'reject' ? gate.reject(new Error('late failure')) : gate.resolve(false);
            assert.equal(await task, false);
            if (successor) await successor;
            assert.equal(oldModel.writes.length, before + (successor ? 1 : 0));
            assert.equal(manager.currentModel.values[0], action === 'new-apply' ? 0.7 : 0.2);
        });
    }
}

for (const phase of ['fetch', 'json']) {
    test(`persistent setup cancelled during ${phase} cannot republish retired expressions`, async () => {
        const gate = deferred();
        const response = { ok: true, json: () => phase === 'json' ? gate.promise : Promise.resolve({ Parameters: parameters }) };
        const entered = deferred();
        const { manager } = fixture({ fetch: () => { entered.resolve(); return phase === 'fetch' ? gate.promise : Promise.resolve(response); } });
        manager.collectPersistentExpressionFiles = () => ['resident.exp3.json'];
        const task = manager.setupPersistentExpressions();
        await entered.promise;
        if (phase === 'json') await Promise.resolve();
        manager.teardownPersistentExpressions();
        gate.resolve(phase === 'fetch' ? response : { Parameters: parameters });
        assert.equal(await task, false);
        assert.equal(manager.persistentExpressionNames.length, 0);
        assert.equal(manager.currentModel.writes.length, 0);
    });
}

for (const action of ['clear', 'model-change', 'new-expression']) {
    test(`late native rejection after ${action} does not install an obsolete manual override`, async () => {
        const gate = deferred();
        const entered = deferred();
        const { manager } = fixture({ expression: () => { entered.resolve(); return gate.promise; } });
        const task = manager.playExpression('happy', 'happy.exp3.json');
        await entered.promise;
        let next;
        if (action === 'clear') await manager.clearExpression();
        if (action === 'model-change') manager.currentModel = createModel();
        if (action === 'new-expression') {
            manager.currentModel.expression = async () => false;
            next = manager.playExpression('new', 'new.exp3.json');
        }
        gate.reject(new Error('late native rejection'));
        assert.equal(await task, false);
        if (next) assert.equal(await next, true);
        assert.equal(!!manager._manualExpressionListener, action === 'new-expression');
    });
}

test('smooth expression fade never captures/applies a speech opening delta', async () => {
    const { manager, advance } = fixture();
    manager.speaking = true;
    const model = manager.currentModel;
    model.values[0] = -0.4;
    const done = manager.smoothResetToInitialState(100);
    model.internalModel.emit('beforeModelUpdate');
    model.values[0] = 0.2;
    model.values[1] = 0.1;
    model.internalModel.emit('beforeModelUpdate');
    assert.equal(model.values[1], 0.1);
    advance(50);
    model.values[1] = 0;
    model.internalModel.emit('beforeModelUpdate');
    assert.equal(model.values[1], 0);
    advance(100);
    model.internalModel.emit('beforeModelUpdate');
    await done;
    assert.equal(model.internalModel.listenerCount('beforeModelUpdate'), 0);
});

test('full reset skips index aliases for speech opening and invalidates its core cache on model change', () => {
    const { manager } = fixture();
    manager.speaking = true;
    manager.initialParameters = { param_0: 0.2, param_1: 0 };
    manager._resetParametersToInitialState({ preserveExpression: false });
    assert.equal(manager.currentModel.values[1], 0.6);
    const oldCore = manager._expressionAmplitudeIndexCache.core;
    manager.currentModel = createModel();
    manager._resetParametersToInitialState({ preserveExpression: false });
    assert.notEqual(manager._expressionAmplitudeIndexCache.core, oldCore);
    assert.equal(manager.currentModel.values[1], 0.6);
});

test('cancelled fade and manual callbacks cannot remove their successor listeners', () => {
    const { manager } = fixture();
    manager._installManualExpressionOverride(parameters, 50);
    const retiredManual = manager._manualExpressionListener;
    manager._installManualExpressionOverride(parameters, 50);
    const currentManual = manager._manualExpressionListener;
    retiredManual();
    assert.equal(manager._manualExpressionListener, currentManual);
    manager.smoothResetToInitialState(100);
    const retiredFade = manager._smoothResetListener;
    manager.smoothResetToInitialState(100);
    const currentFade = manager._smoothResetListener;
    retiredFade();
    assert.equal(manager._smoothResetListener, currentFade);
    manager._cancelSmoothReset();
    manager._removeManualExpressionOverride();
});

test('soft-clear cancelled by model replacement does not reset the new model', async () => {
    const { manager } = fixture();
    const task = manager.softClearEmotionEffects({ duration: 100 });
    manager.currentModel = createModel();
    manager._cancelSmoothReset();
    assert.equal(await task, false);
    assert.equal(manager.currentModel.writes.length, 0);
});

test('new expression cancels an old fade even when no transient expression was marked active', async () => {
    const { manager } = fixture();
    const fade = manager.softClearEmotionEffects({ duration: 100 });
    assert.ok(manager._smoothResetListener);
    assert.equal(await manager.playExpression('happy', 'happy.exp3.json'), true);
    assert.equal(await fade, false);
    assert.equal(manager._smoothResetListener, null);
    assert.ok(manager._manualExpressionListener);
});

let sdkRuntime;
async function realExpressionManager() {
    if (!sdkRuntime) sdkRuntime = (async () => {
        const context = { console: { log() {}, info() {}, debug() {}, warn() {}, error() {}, assert() {} },
            setTimeout, clearTimeout, performance, TextDecoder, TextEncoder, atob, btoa,
            document: { currentScript: { src: 'http://localhost/static/libs/live2dcubismcore.min.js' } },
            navigator: { userAgent: 'node-expression-test' }, addEventListener() {}, removeEventListener() {},
            PIXI: { utils: { EventEmitter }, Transform: class {}, Container: class extends EventEmitter {},
                Point: class {}, Matrix: class {}, ObservablePoint: class {} },
            PhysicsHair: { Src: { SRC_TO_X: 0, SRC_TO_Y: 1, SRC_TO_G_ANGLE: 2 } },
            Live2D: {}, Live2DMotion: class { updateParam() {} }, AMotion: class {},
        };
        context.window = context;
        context.self = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../static/libs/live2dcubismcore.min.js'), 'utf8'), context);
        await new Promise(resolve => setImmediate(resolve));
        vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../static/libs/index.min.js'), 'utf8'), context);
        return context.PIXI.live2d;
    })();
    const sdk = await sdkRuntime;
    const expressionManager = new sdk.Cubism4ExpressionManager({ name: 'test', expressions: [{ Name: 'happy', File: 'happy.exp3.json' }] });
    return { expressionManager, expression: sdk.Live2DModel.prototype.expression };
}

async function checkEmotionRetiresPendingNative({ reject = false, residentExpression = false, preserveIdle = false } = {}) {
    const { expressionManager: native, expression } = await realExpressionManager();
    const gate = deferred(), entered = deferred();
    const obsolete = native.createExpression({ Parameters: [{ Id: FORM, Value: -0.4, Blend: 'Overwrite' }] });
    const residentValue = native.createExpression({ Parameters: [{ Id: FORM, Value: 0.8, Blend: 'Overwrite' }] });
    native.definitions.push({ Name: 'resident', File: 'resident.exp3.json' });
    let loads = 0;
    native._loadExpression = () => {
        if (++loads === 1) { entered.resolve(); return gate.promise; }
        return Promise.resolve(residentValue);
    };
    const commits = [];
    const commit = native._setExpression.bind(native);
    native._setExpression = value => { commits.push(value); return commit(value); };
    const { manager } = fixture();
    manager.currentModel.internalModel.motionManager.expressionManager = native;
    manager.currentModel.expression = expression;
    // Keep the independent motion slot occupied so this checks expression cancellation.
    manager.hasActiveActionMotion = () => true;
    let old, next;
    try {
        old = manager.playExpression('happy', 'happy.exp3.json');
        await entered.promise;
        if (residentExpression) {
            manager.persistentExpressionNames = ['resident'];
            manager.persistentExpressionParamsByName = { resident: [{ Id: FORM, Value: 0.8 }] };
        }
        next = manager.setEmotion(preserveIdle ? 'Idle' : 'unmapped');
        assert.equal(native.reserveExpressionIndex, preserveIdle ? 0 : -1,
            'a replacing emotion must revoke the SDK reservation before awaiting old work');
        if (reject) gate.reject(new Error('controlled old expression load failure'));
        else gate.resolve(obsolete);
        assert.equal(await old, preserveIdle);
        await next;
        assert.equal(manager.currentEmotion, preserveIdle ? 'Idle' : 'unmapped');
        assert.equal(manager.isEmotionChanging, false);
        if (preserveIdle) {
            assert.deepEqual(commits, [obsolete]);
            assert.equal(manager._activeTransientExpression, true);
        } else if (residentExpression) {
            assert.deepEqual(commits, [residentValue], 'only the current resident may enter the real SDK queue');
            assert.equal(native.currentExpression, residentValue);
        } else {
            assert.deepEqual(commits, [], 'obsolete native loading must not publish a motion');
            assert.equal(native.queueManager._motions.length, 0, 'no obsolete expression remains available for rendering');
            assert.notEqual(native.currentExpression, obsolete);
        }
    } finally {
        gate.reject(new Error('test teardown'));
        await Promise.allSettled([old, next]);
        native.destroy();
    }
}

for (const reject of [false, true]) {
    for (const residentExpression of [false, true]) {
        test(`emotion without an expression retires pending native ${reject ? 'rejection' : 'completion'} and ${residentExpression ? 'replays current resident' : 'leaves no obsolete motion'}`, () =>
            checkEmotionRetiresPendingNative({ reject, residentExpression }));
    }
}
test('Idle without a mapped expression preserves the supported pending transient expression', () =>
    checkEmotionRetiresPendingNative({ preserveIdle: true }));

for (const direction of ['resident-to-transient', 'transient-to-resident']) {
    test(`real SDK cancellation and same-name ${direction} never reuses the old reservation`, async () => {
        const { expressionManager: native, expression } = await realExpressionManager();
        const gate = deferred();
        const entered = deferred();
        const realExpression = native.createExpression({ Parameters: [{ Id: FORM, Value: -0.4, Blend: 'Overwrite' }], FadeInTime: 0, FadeOutTime: 0 });
        let loads = 0;
        native._loadExpression = () => { loads++; entered.resolve(); return gate.promise; };
        const commits = [];
        const commit = native._setExpression.bind(native);
        native._setExpression = value => { commits.push(value); return commit(value); };
        const { manager } = fixture();
        manager.currentModel.internalModel.motionManager.expressionManager = native;
        manager.currentModel.expression = expression;
        if (direction === 'resident-to-transient') {
            manager.persistentExpressionNames = ['happy'];
            manager.persistentExpressionParamsByName = { happy: parameters };
        }
        const old = direction === 'resident-to-transient'
            ? manager.applyPersistentExpressionsNative()
            : manager.playExpression('happy', 'happy.exp3.json');
        await entered.promise;
        manager.teardownPersistentExpressions();
        let next;
        if (direction === 'resident-to-transient') next = manager.playExpression('happy', 'happy.exp3.json');
        else {
            manager.persistentExpressionNames = ['happy'];
            manager.persistentExpressionParamsByName = { happy: parameters };
            next = manager.applyPersistentExpressionsNative();
        }
        await new Promise(resolve => setImmediate(resolve));
        const loadsBeforeRelease = loads;
        gate.resolve(realExpression);
        assert.equal(await old, false);
        assert.equal(await next, true);
        assert.equal(loadsBeforeRelease, 1, 'successor waits before reserving the same SDK index');
        assert.equal(commits.length, 1, 'only the successor may enter the real SDK motion queue');
        assert.equal(native.currentExpression, realExpression);
        native.destroy();
    });
}

for (const lateResult of ['reject', 'resolve']) {
test(`real SDK timeout retires native until model reload and late ${lateResult} cannot take over`, async () => {
    const { expressionManager: native, expression } = await realExpressionManager();
    const defaultExpression = native.currentExpression;
    const gate = deferred();
    const entered = deferred();
    let triggerTimeout;
    let loads = 0;
    native._loadExpression = () => { loads++; entered.resolve(); return gate.promise; };
    const { manager, advance } = fixture({ setTimeout(callback, delay) {
        assert.equal(delay, 15000);
        triggerTimeout = callback;
        return 123;
    } });
    manager.currentModel.internalModel.motionManager.expressionManager = native;
    manager.currentModel.expression = expression;
    const task = manager.playExpression('happy', 'happy.exp3.json');
    await entered.promise;
    triggerTimeout();
    assert.equal(await task, true, 'existing manual fallback succeeds after native timeout');
    advance(250);
    manager.currentModel.internalModel.emit('beforeModelUpdate');
    assert.ok(Math.abs(manager.currentModel.values[0] + 0.4) < 1e-12);
    assert.equal(native.reserveExpressionIndex, -1);
    assert.equal(await manager.playExpression('happy', 'happy.exp3.json'), true);
    assert.equal(loads, 1, 'retired SDK manager is not reused');
    if (lateResult === 'reject') gate.reject(new Error('late SDK failure'));
    else gate.resolve(native.createExpression({ Parameters: [{ Id: FORM, Value: 0.9, Blend: 'Overwrite' }] }));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(native.currentExpression, defaultExpression, 'late native result never starts an obsolete expression');
    const replacement = await realExpressionManager();
    let replacementLoads = 0;
    replacement.expressionManager._loadExpression = async () => {
        replacementLoads++;
        return replacement.expressionManager.createExpression({ Parameters: [{ Id: FORM, Value: -0.4, Blend: 'Overwrite' }] });
    };
    manager.currentModel = createModel(replacement.expression);
    manager.currentModel.internalModel.motionManager.expressionManager = replacement.expressionManager;
    assert.equal(await manager.playExpression('happy', 'happy.exp3.json'), true);
    assert.equal(replacementLoads, 1, 'a new model can use native expressions again');
    replacement.expressionManager.destroy();
    native.destroy();
});
}

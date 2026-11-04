const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { read, loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

async function fixture({ manual = false } = {}) {
    const runtime = await loadRuntime();
    vm.runInContext(read('static/live2d/live2d-emotion.js'), runtime.context);
    vm.runInContext(read('static/live2d/live2d-interaction.js'), runtime.context);
    const f = createMao(runtime), api = runtime.context.PIXI.live2d;
    const motionFile = Object.values(f.settings.FileReferences.Motions).flat()[0].File;
    const motionData = JSON.parse(fs.readFileSync(path.join(f.root, motionFile), 'utf8'));
    const settings = new api.Cubism4ModelSettings({ ...f.settings, url: 'http://localhost/mao/model.model3.json',
        FileReferences: { ...f.settings.FileReferences,
            Motions: { Idle: [{ File: motionFile }], happy: [{ File: motionFile }] },
            Expressions: [{ Name: 'happy', File: 'happy.exp3.json' }, { Name: 'resident', File: 'resident.exp3.json' }] } });
    const native = new api.Cubism4MotionManager(settings, { motionPreload: api.MotionPreloadStrategy.NONE });
    native._loadMotion = async () => runtime.sdk.Motion.create(motionData);
    native.expressionManager._loadExpression = async index => native.expressionManager.createExpression({
        Parameters: [{ Id: index === 0 ? 'ParamAngleX' : 'ParamAngleY', Value: 7, Blend: 'Overwrite' }]
    });
    const events = new EventEmitter();
    Object.assign(f.model.internalModel, { motionManager: native, events,
        on: events.on.bind(events), off: events.off.bind(events) });
    native.on('motionFinish', () => events.emit('motionFinish'));
    f.model.motion = (...args) => native.startMotion(...args);
    if (!manual) f.model.expression = (...args) => api.Live2DModel.prototype.expression.apply(f.model, args);
    f.manager.fileReferences = settings.json.FileReferences;
    f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'] } };
    f.manager.resolveAssetPath = file => file;
    f.manager.recordInitialParameters();
    runtime.context.fetch = async () => ({ ok: true, json: async () => ({
        Parameters: [{ Id: 'ParamAngleX', Value: 7 }], Meta: { Duration: 1 }, Curves: []
    }) });
    f.manager.installMouthOverride();
    f.native = native;
    const releaseCore = f.release;
    f.release = () => { f.manager._cancelAvatarOperations(); native.destroy(); releaseCore(); };
    return f;
}

test('an active action rejects transient expressions and combined emotions without changing the action', async () => {
    const f = await fixture();
    try {
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
        const generation = f.manager._actionMotionGeneration;
        assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), false);
        assert.equal(await f.manager.setEmotion('happy'), false);
        assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 9 }]), false);
        assert.equal(f.manager._actionMotionGeneration, generation);
        assert.equal(f.native.state.currentPriority, 2);
        assert.equal(f.manager.hasActiveTransientExpression(), false);
    } finally { f.release(); }
});

test('a pending SDK action reservation also rejects a transient expression', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        const load = f.native._loadMotion;
        f.native._loadMotion = async (...args) => { entered.resolve(); await gate.promise; return load(...args); };
        const action = f.manager.playActionMotion('happy', 0);
        await entered.promise;
        assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), false);
        gate.resolve();
        assert.equal(await action, true);
    } finally { gate.resolve(); f.release(); }
});

for (const manual of [false, true]) {
    test(`${manual ? 'manual' : 'native'} transient expression stops idle and excludes every motion entry`, async () => {
        const f = await fixture({ manual });
        try {
            await f.manager._playIdleMotion(f.native);
            assert.equal(f.native.state.currentPriority, 1);
            f.manager._setActiveMotionParamIds(['ParamAngleY']);
            f.core.setParameterValueById('ParamAngleY', 9);
            assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), true);
            assert.equal(f.value('ParamAngleY'), 0, 'the old idle pose is reset before the expression');
            assert.equal(f.native.state.currentPriority, 0);
            assert.equal(await f.manager.playActionMotion('happy', 0), false);
            assert.equal(await f.manager.playMotion('happy'), false);
            assert.equal(f.manager.playSimpleMotion('happy'), false);
            await f.manager._playIdleMotion(f.native);
            f.native.update(f.core, 1);
            assert.equal(f.native.state.currentPriority, 0, 'SDK automatic idle must also stay stopped');

            f.manager.persistentExpressionNames = ['resident'];
            f.manager.persistentExpressionParamsByName = { resident: [{ Id: 'ParamAngleY', Value: 7 }] };
            await f.manager.applyPersistentExpressionsNative(true);
            assert.equal(f.manager.hasActiveTransientExpression(), true);
            await f.manager.clearExpression();
            assert.equal(f.manager.hasActiveTransientExpression(), false);
            assert.equal(await f.manager.playActionMotion('happy', 0), true, 'resident expressions do not exclude actions');
        } finally { f.release(); }
    });
}

test('expression fetch reserves the slot synchronously and a failed fetch releases it', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        f.runtime.context.fetch = async () => { entered.resolve(); await gate.promise; return { ok: false }; };
        const expression = f.manager.playExpression('happy', 'happy.exp3.json');
        assert.equal(f.manager.hasActiveTransientExpression(), true);
        assert.equal(await f.manager.playActionMotion('happy', 0), false);
        await entered.promise;
        await f.manager._playIdleMotion(f.native);
        assert.equal(f.native.state.currentPriority, 0);
        gate.resolve();
        assert.equal(await expression, false);
        assert.equal(f.manager.hasActiveTransientExpression(), false);
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
    } finally { gate.resolve(); f.release(); }
});

test('clearing a pending expression lets an action start and the late fetch cannot install an expression', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        const fetch = f.runtime.context.fetch;
        f.runtime.context.fetch = async (...args) => { entered.resolve(); await gate.promise; return fetch(...args); };
        const expression = f.manager.playExpression('happy', 'happy.exp3.json');
        await entered.promise;
        await f.manager.clearExpression();
        assert.equal(f.manager.hasActiveTransientExpression(), false);
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
        gate.resolve();
        assert.equal(await expression, false);
        assert.equal(f.native.state.currentPriority, 2);
        assert.equal(f.manager._manualExpressionListener, null);
    } finally { gate.resolve(); f.release(); }
});

test('a late idle load cannot start after a transient expression takes over', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        const load = f.native._loadMotion;
        f.native._loadMotion = async (...args) => { entered.resolve(); await gate.promise; return load(...args); };
        const idle = f.manager._playIdleMotion(f.native);
        await entered.promise;
        assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), true);
        gate.resolve();
        await idle;
        assert.equal(f.native.state.currentPriority, 0);
        assert.equal(f.manager.hasActiveTransientExpression(), true);
    } finally { gate.resolve(); f.release(); }
});

for (const tracked of [false, true]) {
    test(`stopping idle clears the cached arm pose even when motion IDs are ${tracked ? 'loaded' : 'still loading'}`, async () => {
        const f = await fixture();
        try {
            const arm = 'ParamArmLA01';
            const baseline = f.value(arm);
            await f.manager._playIdleMotion(f.native);
            if (tracked) f.manager._setActiveMotionParamIds([arm]);
            else f.manager._clearActiveMotionParamIds();
            f.core.setParameterValueById(arm, baseline + 1);
            f.core.saveParameters();
            assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), true);
            assert.equal(f.value(arm), baseline);
            for (let frame = 0; frame < 3; frame++) {
                f.core.loadParameters();
                f.native.update(f.core, frame);
                assert.equal(f.value(arm), baseline, 'the SDK cache must not revive the old arm');
            }
        } finally { f.release(); }
    });
}

test('idle scheduling retries during expressions and resumes the saved idle after clearing', async () => {
    const f = await fixture();
    try {
        f.native.motionGroups.PreviewAll = [await f.native.loadMotion('Idle', 0)];
        f.native.definitions.PreviewAll = f.native.definitions.Idle;
        f.manager._userIdleAnimations = [path.basename(f.native.definitions.Idle[0].File)];
        f.manager.setupIdleMotionLoop(f.model);
        assert.equal(await f.manager.playExpression('happy', 'happy.exp3.json'), true);
        let timer = [...f.manager._idleMotionLoopTimers][0];
        timer.callback();
        assert.equal(f.native.state.currentPriority, 0);
        timer = [...f.manager._idleMotionLoopTimers][0];
        assert.equal(timer.delay, 1000);
        await f.manager.clearExpression();
        timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.native.state.currentGroup, 'PreviewAll');
        assert.equal(f.native.state.currentPriority, 1);
    } finally { f.release(); }
});

test('smooth expression cleanup holds exclusion until the fade completes', async () => {
    const f = await fixture({ manual: true });
    try {
        await f.manager.playExpression('happy', 'happy.exp3.json');
        const reset = f.manager.smoothResetToInitialState(100);
        f.model.internalModel.events.emit('beforeModelUpdate');
        f.core.setParameterValueById('ParamAngleX', -2);
        f.model.internalModel.events.emit('beforeModelUpdate');
        assert.equal(await f.manager.playActionMotion('happy', 0), false);
        f.runtime.context.clock += 100;
        f.model.internalModel.events.emit('beforeModelUpdate');
        await reset;
        assert.equal(f.manager.hasActiveTransientExpression(), false);
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
    } finally { f.release(); }
});

test('combined emotions choose an expression when configured and a motion otherwise', async () => {
    const f = await fixture();
    try {
        await f.manager.setEmotion('happy');
        assert.equal(f.manager.hasActiveTransientExpression(), true);
        assert.equal(f.native.state.currentPriority, 0);
        await f.manager.clearExpression();
        await f.manager.setEmotion('happy');
        assert.equal(f.manager.hasActiveTransientExpression(), true, 'the same emotion can replay after its expression is cleared');
        assert.equal(f.native.state.currentPriority, 0);
        await f.manager.clearExpression();
        f.manager.emotionMapping.expressions = {};
        f.manager.fileReferences.Expressions = [];
        await f.manager.setEmotion('happy');
        assert.equal(f.manager.hasActiveTransientExpression(), false);
        assert.equal(f.native.state.currentPriority, 2);
    } finally { f.release(); }
});

test('click fallback cannot add a transient expression to an occupied action', async () => {
    const f = await fixture();
    try {
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
        assert.equal(await f.manager._playTemporaryClickEffect('happy'), false);
        assert.equal(f.native.state.currentPriority, 2);
        assert.equal(f.manager.hasActiveTransientExpression(), false);
    } finally { f.release(); }
});

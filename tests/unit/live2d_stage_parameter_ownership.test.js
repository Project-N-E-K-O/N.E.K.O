const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { read, loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

async function runCancelledEmotion(options = {}) {
    const f = await fixture(options);
    try {
        f.apply({ ParamAngleX: 2 });
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'] } };
        f.manager.fileReferences = { Expressions: [{ Name: 'happy', File: 'happy.exp3.json' }] };
        const entered = deferred(), gate = deferred(), motions = [];
        let persistentCalls = 0;
        // Delegate the real setEmotion and expression loader. Only observe its
        // downstream motion admission and persistent reapply boundaries.
        f.manager.playMotion = async emotion => { motions.push(emotion); return true; };
        f.manager.applyPersistentExpressionsNative = async () => { persistentCalls++; };
        f.runtime.context.fetch = async () => {
            if (f.manager.isEmotionChanging) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 7 }] }) };
        };
        const old = f.stage.acquire('old-emotion');
        const pending = f.stage.applyEmotion('happy');
        await entered.promise;
        assert.equal(f.manager.isEmotionChanging, true, 'reach the real manager await, not driver preflight');
        f.apply({ ParamAngleX: 3 });
        f.stage.release(old.id, 'cancelled');
        const next = f.stage.acquire('successor');
        gate.resolve();
        const result = await pending;
        assert.equal(result, false);
        assert.equal(f.manager._avatarPerformanceSessionOwners.get('motion'), next);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 0);
        assert.equal(f.manager.isEmotionChanging, false);
        f.motionManager.update(0);
        f.core.update();
        assert.ok(f.coreCalls > 0);
        f.stage.release(next.id, 'test_cleanup');
        return { motions, persistentCalls };
    } finally { f.release(); }
}

test('cancelled Stage emotion cannot admit a motion or persistent reapply after its real expression wait', async () => {
    const result = await runCancelledEmotion();
    assert.deepEqual(result, { motions: [], persistentCalls: 0 });
});

test('a cancelled Stage signal also protects the manager when the explicit continuation callback is removed', async () => {
    const result = await runCancelledEmotion({ transformStage(source) {
        const guarded = 'Object.assign({}, normalized, { isCurrent, onApplied })';
        assert.ok(source.includes(guarded));
        return source.replace(guarded, 'Object.assign({}, normalized, { onApplied })');
    } });
    assert.deepEqual(result.motions, []);
    assert.equal(result.persistentCalls, 0);
});

test('a newer bridge emotion supersedes the already entered manager request before motion admission', async () => {
    const f = await fixture();
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        const entered = deferred(), gate = deferred(), motions = [];
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'], sad: ['sad.exp3.json'] } };
        f.manager.fileReferences = { Expressions: [] };
        f.manager.playMotion = async emotion => { motions.push(emotion); return true; };
        f.manager.applyPersistentExpressionsNative = async () => {};
        f.runtime.context.fetch = async file => {
            if (file === 'happy.exp3.json') { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) };
        };
        const first = bridge.queueLive2DEmotionApply('happy');
        await entered.promise;
        assert.equal(f.manager.isEmotionChanging, true);
        const latest = bridge.queueLive2DEmotionApply('sad');
        gate.resolve();
        await first;
        await latest;
        assert.deepEqual(motions, ['sad']);
        assert.equal(f.manager.currentEmotion, 'sad');
        assert.equal(f.manager.isEmotionChanging, false);
    } finally { f.release(); }
});

test('ordinary emotion calls also fence Core replacement after the real expression loader', async () => {
    const f = await fixture(), next = createMao(f.runtime);
    try {
        const entered = deferred(), gate = deferred();
        let motions = 0, persistentCalls = 0;
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'] } };
        f.manager.fileReferences = { Expressions: [] };
        f.manager.playMotion = async () => { motions++; };
        f.manager.applyPersistentExpressionsNative = async () => { persistentCalls++; };
        f.runtime.context.fetch = async () => {
            entered.resolve(); await gate.promise;
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) };
        };
        const pending = f.manager.setEmotion('happy');
        await entered.promise;
        assert.equal(f.manager.isEmotionChanging, true);
        f.model.internalModel.coreModel = next.core;
        next.core.setParameterValueById('ParamAngleX', 9);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(motions, 0);
        assert.equal(persistentCalls, 0);
        assert.equal(next.value('ParamAngleX'), 9);
        assert.equal(f.manager.isEmotionChanging, false);
    } finally { f.release(); next.release(); }
});

test('a live ordinary emotion retains expression, motion and persistent reapply behavior', async () => {
    const f = await fixture();
    try {
        const motions = []; let persistentCalls = 0;
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'] } };
        f.manager.fileReferences = { Expressions: [] };
        f.manager.playMotion = async emotion => { motions.push(emotion); return true; };
        f.manager.applyPersistentExpressionsNative = async () => { persistentCalls++; };
        f.runtime.context.fetch = async () => ({ ok: true,
            json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        assert.notEqual(await f.manager.setEmotion('happy'), false);
        assert.deepEqual(motions, ['happy']);
        // Existing behavior reapplies once in _playExpressionNow and once in setEmotion.
        assert.equal(persistentCalls, 2);
        assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 1);
        assert.equal(f.manager.isEmotionChanging, false);
        f.manager._removeManualExpressionOverride();
    } finally { f.release(); }
});

test('an accepted SDK expression queued behind a fetch cannot start on a replacement Core', async () => {
    const f = await fixture();
    const next = createMao(f.runtime);
    try {
        const entered = deferred(), gate = deferred();
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async file => {
            if (file === 'first.exp3.json') { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) };
        };
        const first = f.manager.playExpression('happy', 'first.exp3.json');
        await entered.promise;
        const second = f.manager.playExpression('happy', 'second.exp3.json');
        f.model.internalModel.coreModel = next.core;
        gate.resolve();
        assert.equal(await first, false);
        assert.equal(await second, false);
        assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 0);
        assert.equal(next.value('ParamAngleX'), 0);
    } finally { f.release(); next.release(); }
});

for (const handover of [false, true]) {
    test(`queued guide emotion checks its Core owner after the real motion wait (handover ${handover})`, async () => {
        const f = await fixture();
        const next = createMao(f.runtime);
        try {
            const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
            let busy = true, calls = 0;
            f.manager.hasActiveMotionPlayback = () => busy;
            f.manager.setEmotion = async () => { calls++; };
            const pending = bridge.queueLive2DEmotionApply('happy');
            await new Promise(resolve => setImmediate(resolve));
            const waiting = f.runtime.timers.find(timer => timer.delay === 48);
            assert.ok(waiting, 'production motion wait must reach its asynchronous barrier');
            if (handover) f.model.internalModel.coreModel = next.core;
            busy = false;
            waiting.callback();
            await pending;
            assert.equal(calls, handover ? 0 : 1);
            assert.equal(next.value('ParamAngleX'), 0);
        } finally { f.release(); next.release(); }
    });
}

test('SDK expression fetch cannot install on a replacement Core in the same model', async () => {
    const f = await fixture();
    const next = createMao(f.runtime);
    try {
        const entered = deferred(), gate = deferred();
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async () => {
            entered.resolve();
            await gate.promise;
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) };
        };
        const pending = f.manager.playExpression('happy', 'replacement.exp3.json');
        await entered.promise;
        f.model.internalModel.coreModel = next.core;
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 0);
        assert.notEqual(f.manager._activeTransientExpression, true);
        assert.equal(next.value('ParamAngleX'), 0);
    } finally { f.release(); next.release(); }
});

test('SDK manual fallback reports unapplied when registration loses its Core owner', async () => {
    const f = await fixture();
    const next = createMao(f.runtime);
    try {
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async () => ({ ok: true,
            json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        const emitter = f.model.internalModel;
        const register = emitter.on;
        emitter.on = (...args) => {
            register(...args);
            emitter.coreModel = next.core;
        };
        assert.equal(await f.manager.playExpression('happy', 'registration.exp3.json'), false);
        assert.equal(emitter.events.listenerCount('beforeModelUpdate'), 0);
        assert.notEqual(f.manager.expressionApplied, true);
        assert.notEqual(f.manager._activeTransientExpression, true);
        assert.equal(next.value('ParamAngleX'), 0);
    } finally { f.release(); next.release(); }
});

async function fixture(options = {}) {
    const runtime = await loadRuntime({ transformModelSource: options.transformModelSource });
    const frames = new Map();
    let frameId = 0;
    runtime.context.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
    runtime.context.cancelAnimationFrame = id => frames.delete(id);
    const sourcePath = file => path.resolve(__dirname, '../../', file);
    const emotionSource = read('static/live2d/live2d-emotion.js');
    vm.runInContext(options.transformEmotion ? options.transformEmotion(emotionSource) : emotionSource, runtime.context,
        { filename: sourcePath(options.transformEmotion ? 'mutation/live2d-emotion.js' : 'static/live2d/live2d-emotion.js') });
    const stageSource = read('static/avatar/avatar-performance-stage.js');
    vm.runInContext(options.transformStage ? options.transformStage(stageSource) : stageSource, runtime.context,
        { filename: sourcePath(options.transformStage ? 'mutation/avatar-performance-stage.js' : 'static/avatar/avatar-performance-stage.js') });
    const yuiSource = read('static/tutorial/avatar/yui-stage.js');
    vm.runInContext(options.transformYui ? options.transformYui(yuiSource) : yuiSource, runtime.context,
        { filename: sourcePath(options.transformYui ? 'mutation/yui-stage.js' : 'static/tutorial/avatar/yui-stage.js') });
    runtime.context.__YuiGuideDirector = {};
    vm.runInContext(read('static/tutorial/yui-guide/director/emotion-bridge.js'), runtime.context,
        { filename: sourcePath('static/tutorial/yui-guide/director/emotion-bridge.js') });
    const f = createMao(runtime);
    // Runtime shell emits the same event API as Pixi's internal model; SDK/Core
    // and all parameter/default operations remain real bundled implementations.
    const emitter = new EventEmitter();
    f.model.internalModel.events = emitter;
    f.model.internalModel.on = emitter.on.bind(emitter);
    f.model.internalModel.off = emitter.off.bind(emitter);
    f.manager.recordInitialParameters();
    f.container = { style: {}, getBoundingClientRect() {
        return { left: 0, top: 0, right: 400, bottom: 600, width: 400, height: 600 };
    } };
    runtime.context.live2dManager = f.manager;
    runtime.context.document.getElementById = () => f.container;
    f.driver = runtime.context.AvatarPerformance.createLive2DDriver({
        managerResolver: () => f.manager, containerResolver: () => f.container
    });
    f.stage = runtime.context.AvatarPerformance.createStage({ driver: f.driver });
    f.runtime = runtime;
    f.frames = frames;
    return f;
}

async function runInnerCancellation({ standalone = false, native = false, successorExpression = false, checkCleanup = true, ...options } = {}) {
    const f = await fixture(options), entered = deferred(), gate = deferred(), admissions = [];
    try {
        f.apply({ ParamAngleX: 2 });
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.emotionMapping = { expressions: { happy: ['happy.exp3.json'] } };
        f.manager.fileReferences = { Motions: { happy: [{ File: 'happy.motion3.json' }] }, Expressions: [] };
        f.model.motion = async (...args) => { admissions.push(args); return true; };
        if (native) {
            f.model.expression = async () => true;
            f.manager.fileReferences.Expressions = [{ Name: 'happy', File: 'happy.exp3.json' }];
        }
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({
            Parameters: [{ Id: 'ParamAngleX', Value: 7 }], Meta: { Duration: 1 }, Curves: []
        }) });
        const reset = f.manager.resetTransientMotionAndExpressionState;
        let preparingMotion = false;
        f.manager.resetTransientMotionAndExpressionState = async function(...args) {
            preparingMotion = true;
            try { return await reset.apply(this, args); } finally { preparingMotion = false; }
        };
        const persistent = f.manager.applyPersistentExpressionsNative;
        let blocked = false;
        f.manager.applyPersistentExpressionsNative = async function(...args) {
            const result = await persistent.apply(this, args);
            if (!blocked && (standalone || preparingMotion)) { blocked = true; entered.resolve(); await gate.promise; }
            return result;
        };
        const old = f.stage.acquire('inner-wait');
        const pending = standalone ? f.stage.applyExpression('happy') : f.stage.applyEmotion('happy');
        await entered.promise;
        assert.equal(f.manager._activeTransientExpression, true, 'reach installed expression, not preflight');
        if (!native) assert.ok(f.manager._manualExpressionListener);
        const oldListener = f.manager._manualExpressionListener;
        f.apply({ ParamAngleX: 3 });
        f.stage.release(old.id, 'cancelled');
        const removedAtRelease = !f.manager._manualExpressionListener;
        const next = f.stage.acquire('successor');
        let nextListener;
        if (successorExpression) {
            f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 9 }], 0);
            nextListener = f.manager._manualExpressionListener;
            assert.notEqual(nextListener, oldListener);
        }
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.manager._avatarPerformanceSessionOwners.get('motion'), next);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        if (successorExpression) assert.equal(f.manager._manualExpressionListener, nextListener);
        else if (checkCleanup) {
            assert.equal(f.manager._activeTransientExpression, false);
            assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 0);
            assert.equal(f.value('ParamAngleX'), 3);
        }
        f.stage.release(next.id, 'test_cleanup');
        f.manager._removeManualExpressionOverride();
        return { admissions: admissions.length, removedAtRelease };
    } finally { gate.resolve(); f.release(); }
}

test('cancel after expression install during real motion preparation cleans its listener and admits no action', async () => {
    assert.deepEqual(await runInnerCancellation(), { admissions: 0, removedAtRelease: true });
});

test('independent manual expression can be released during its resident-expression wait', async () => {
    assert.deepEqual(await runInnerCancellation({ standalone: true }), { admissions: 0, removedAtRelease: true });
});

test('independent native expression publishes ownership before its resident-expression wait', async () => {
    assert.deepEqual(await runInnerCancellation({ standalone: true, native: true }), { admissions: 0, removedAtRelease: true });
});

test('retired combined emotion leaves a successor expression installed while its motion preparation settles', async () => {
    assert.deepEqual(await runInnerCancellation({ successorExpression: true }), { admissions: 0, removedAtRelease: true });
});

test('operation cancellation also protects motion admission after an inner continuation callback is removed', async () => {
    const result = await runInnerCancellation({ transformEmotion(source) {
        const begin = source.indexOf('Live2DManager.prototype.playMotion =');
        const end = source.indexOf('Live2DManager.prototype.playSimpleMotion', begin);
        const section = source.slice(begin, end);
        const guard = "&& (typeof options.isCurrent !== 'function' || options.isCurrent())";
        assert.ok(section.includes(guard));
        return source.slice(0, begin) + section.replace(guard, '') + source.slice(end);
    } });
    assert.deepEqual(result, { admissions: 0, removedAtRelease: true });
});

test('removing early expression resource publication reproduces the installed listener leak', async () => {
    const result = await runInnerCancellation({ checkCleanup: false, transformEmotion(source) {
        assert.ok(source.includes('publishAppliedExpression();'));
        return source.replaceAll('publishAppliedExpression();', '/* ownership publication removed */');
    } });
    assert.deepEqual(result, { admissions: 0, removedAtRelease: false });
});

test('a completed live emotion still allows its motion end timer to clear the admitted motion', async () => {
    const f = await fixture();
    try {
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.fileReferences = { Motions: { happy: [{ File: 'happy.motion3.json' }] } };
        f.manager.emotionMapping = { expressions: {} };
        f.model.motion = async () => true;
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Meta: { Duration: 1 }, Curves: [] }) });
        let requestCurrent = true;
        await f.manager.setEmotion('happy', { isCurrent: () => requestCurrent });
        assert.equal(f.manager.isEmotionChanging, false);
        assert.equal(f.manager._emotionChangeOperation, null);
        const timer = f.manager.motionTimer;
        assert.ok(timer && timer.id.callback, 'ordinary emotion must actually admit a motion and end timer');
        let cleanups = 0;
        f.manager.clearEmotionEffects = () => { cleanups++; };
        // A newer emotion can replace the expression while retaining an already
        // admitted busy motion. Its timer owns that motion by generation.
        requestCurrent = false;
        timer.id.callback();
        assert.equal(cleanups, 1, 'motion cleanup survives successful outer operation completion');
    } finally { f.release(); }
});

test('motion metadata decoded after a Core replacement cannot publish tracked IDs or an end timer', async () => {
    const f = await fixture(), next = createMao(f.runtime), entered = deferred(), gate = deferred();
    try {
        f.motionManager.state.currentPriority = 0;
        f.manager.resolveAssetPath = file => file;
        f.manager.fileReferences = { Motions: { happy: [{ File: 'happy.motion3.json' }] } };
        f.manager.emotionMapping = { expressions: {} };
        let admissions = 0;
        f.model.motion = async () => { admissions++; return true; };
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => {
            entered.resolve(); await gate.promise;
            return { Meta: { Duration: 1 }, Curves: [{ Target: 'Parameter', Id: 'ParamAngleX' }] };
        } });
        const pending = f.manager.setEmotion('happy');
        await entered.promise;
        assert.equal(admissions, 1, 'reach metadata decode after actual manager admission');
        f.model.internalModel.coreModel = next.core;
        const successorIds = new Set(['ParamAngleY']);
        f.manager._activeMotionParamIds = successorIds;
        next.core.setParameterValueById('ParamAngleX', 9);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.manager._activeMotionParamIds, successorIds);
        assert.equal(f.manager.motionTimer, null);
        assert.equal(next.value('ParamAngleX'), 9);
    } finally { gate.resolve(); f.release(); next.release(); }
});

test('resident expression fallback after Core replacement writes neither the retired Core nor its successor', async () => {
    const f = await fixture(), next = createMao(f.runtime), entered = deferred(), gate = deferred();
    try {
        f.core.setParameterValueById('ParamAngleX', 2);
        f.manager.persistentExpressionNames = ['resident'];
        f.manager.persistentExpressionParamsByName = { resident: [{ Id: 'ParamAngleX', Value: 8 }] };
        f.model.expression = async () => { entered.resolve(); await gate.promise; return false; };
        const pending = f.manager.applyPersistentExpressionsNative(true);
        await entered.promise;
        f.model.internalModel.coreModel = next.core;
        next.core.setParameterValueById('ParamAngleX', 9);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.value('ParamAngleX'), 2);
        assert.equal(next.value('ParamAngleX'), 9);
    } finally { gate.resolve(); f.release(); next.release(); }
});

test('a capture without temporary writes never rewinds model parameters or configuration', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('look-only', { capabilities: ['lookAt'] });
        f.apply({ ParamAngleX: 3 });
        f.stage.release(s.id);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        assert.equal(f.manager.effectiveModelParameters.ParamAngleX, 3);
        f.motionManager.update(0);
        assert.equal(f.value('ParamAngleX'), 3);
    } finally { f.release(); }
});

test('saved configuration accepted during a temporary parameter wins immediately and next frame', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('pose');
        assert.equal(f.stage.setTemporaryParam('ParamAngleX', 9), true);
        f.apply({ ParamAngleX: 3 });
        assert.equal(f.stage.release(s.id), true);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        f.motionManager.update(0);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.stage.release(s.id), false);
    } finally { f.release(); }
});

test('unchanged configuration restores temporary values, unrelated changes do not erase them', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        f.apply({ ParamAngleX: 2, ParamAngleY: 3 });
        f.stage.release(s.id);
        assert.equal(f.value('ParamAngleX'), 2);
        assert.equal(f.value('ParamAngleY'), 3);
    } finally { f.release(); }
});

test('removed saved parameter restores the real default instead of the old appearance baseline', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        f.apply({});
        f.stage.release(s.id);
        assert.equal(f.value('ParamAngleX'), f.core.getParameterDefaultValue(f.core.getParameterIndex('ParamAngleX')));
        assert.equal(f.manager.savedModelParameters, null);
    } finally { f.release(); }
});

test('old stage with the same string ID cannot reset a new stage sharing its driver', async () => {
    const f = await fixture();
    try {
        const old = f.stage.acquire('old');
        f.stage.setTemporaryParam('ParamAngleX', 2);
        const nextStage = f.runtime.context.AvatarPerformance.createStage({ driver: f.driver });
        const next = nextStage.acquire('next');
        assert.equal(old.id, next.id, 'local string IDs really collide');
        nextStage.setTemporaryParam('ParamAngleX', 3);
        nextStage.frame({ x: 20 });
        const transform = f.container.style.transform;
        f.stage.release(old.id);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.container.style.transform, transform);
        assert.equal(f.driver.ownerSession, next);
        nextStage.release(next.id);
    } finally { f.release(); }
});

test('same-source replacement survives old look-at cleanup', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('look', { capabilities: ['lookAt'] });
        f.stage.lookAt({ x: 900, y: 100 });
        const source = f.driver.lookAtSource;
        f.manager.setTemporaryPoseOverride(source, core => core.setParameterValueById('ParamAngleX', 7));
        const replacement = f.manager._temporaryPoseOverrides.get(source);
        f.manager._applyTemporaryPoseOverride(f.core);
        assert.equal(f.value('ParamAngleX'), 7);
        f.stage.release(s.id);
        assert.equal(f.manager._temporaryPoseOverrides.get(source), replacement);
        assert.equal(f.value('ParamAngleX'), 7);
    } finally { f.release(); }
});

test('persistent content mutation/removal cannot be rolled back by an old temporary pose', async () => {
    const f = await fixture();
    try {
        f.manager.persistentExpressionParamsByName = { resident: [{ Id: 'ParamAngleX', Value: 2 }] };
        const s = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        const map = f.manager.persistentExpressionParamsByName;
        map.resident[0].Value = 3;
        f.core.setParameterValueById('ParamAngleX', 3);
        f.stage.release(s.id);
        assert.equal(f.manager.persistentExpressionParamsByName, map);
        assert.equal(f.value('ParamAngleX'), 3);
        const next = f.stage.acquire('next');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        delete map.resident;
        f.stage.release(next.id);
        assert.equal(f.value('ParamAngleX'), 0);
    } finally { f.release(); }
});

test('independent drivers cannot restore parameters owned by a newer overlapping session', async () => {
    const f = await fixture();
    try {
        const old = f.stage.acquire('old');
        f.stage.setTemporaryParam('ParamAngleX', 2);
        const next = f.runtime.context.AvatarPerformance.createLive2DPerformance({ driverOptions: {
            managerResolver: () => f.manager, containerResolver: () => f.container
        } });
        const session = next.acquire('next');
        next.setTemporaryParam('ParamAngleX', 3);
        next.frame({ x: 20 });
        const transform = f.container.style.transform;
        f.stage.release(old.id);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.container.style.transform, transform);
        assert.equal(next.driver.ownsSession(session), true);
        next.release(session.id);
        assert.equal(f.manager._avatarPerformanceSessionOwners.size, 0,
            f.runtime.warnings.map(args => args.map(value => value && value.stack || String(value)).join(' ')).join('\n'));
    } finally { f.release(); }
});

test('different capability sessions retain independent ownership and cleanup', async () => {
    const f = await fixture();
    try {
        const look = f.stage.acquire('look', { capabilities: ['lookAt'] });
        f.stage.lookAt({ x: 900, y: 100 });
        const source = f.driver.lookAtSource;
        const next = f.runtime.context.AvatarPerformance.createLive2DPerformance({ driverOptions: {
            managerResolver: () => f.manager, containerResolver: () => f.container
        } });
        const frame = next.acquire('frame', { capabilities: ['frame'] });
        next.frame({ x: 20 });
        assert.equal(f.driver.ownsSession(look), true);
        next.release(frame.id);
        assert.ok(f.manager._temporaryPoseOverrides.has(source));
        assert.equal(f.driver.ownsSession(look), true);
        f.stage.release(look.id);
        assert.equal(f.manager._temporaryPoseOverrides.has(source), false);
    } finally { f.release(); }
});

test('raw restoration respects active mouth, breathing, visibility and expression protection', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('pose');
        for (const id of ['ParamMouthOpenY', 'ParamBreath', 'ParamAngleX']) f.stage.setTemporaryParam(id, 9);
        f.core.setParameterValueById('ParamMouthOpenY', .7);
        f.core.setParameterValueById('ParamBreath', .7);
        f.core.setParameterValueById('ParamAngleX', 3);
        const protectedSet = vm.runInContext('new Set(["ParamAngleX"])', f.runtime.context);
        f.manager._activeExpressionParamIds = protectedSet;
        f.stage.release(s.id);
        assert.ok(Math.abs(f.value('ParamMouthOpenY') - .7) < 1e-6);
        assert.ok(Math.abs(f.value('ParamBreath') - .7) < 1e-6);
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager._activeExpressionParamIds, protectedSet);
    } finally { f.release(); }
});

test('Yui actual start, parameter refresh and stop preserve the new configuration', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const session = new f.runtime.context.YuiGuideAvatarStage.Live2DIntroVoiceLookAtSession({
            manager: f.manager, model: f.model, coreModel: f.core, ticker: null
        }, { container: f.container, getPoint: () => null, token: 'yui-probe' });
        assert.equal(session.start(), true);
        f.manager._loadModelDirectoryParameters = async () => ({});
        f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 3 });
        assert.equal((await f.manager.reloadModelParameters({ model_name: 'mao_pro' })).applied, true);
        await session.stop('replaced');
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        assert.equal(f.manager._temporaryPoseOverrides.size, 0);
        f.motionManager.update(0);
        assert.equal(f.value('ParamAngleX'), 3);
    } finally { f.release(); }
});

test('real Yui tick stops an old model without writing the replacement', async () => {
    const f = await fixture();
    const second = createMao(f.runtime);
    try {
        f.apply({ ParamAngleX: 2 });
        const session = new f.runtime.context.YuiGuideAvatarStage.Live2DIntroVoiceLookAtSession({
            manager: f.manager, model: f.model, coreModel: f.core, ticker: null
        }, { container: f.container, getPoint: () => null, token: 'model-probe' });
        assert.equal(session.start(), true);
        await f.manager.removeModel({ skipCloseWindows: true });
        f.manager.currentModel = second.model;
        f.manager._applyEffectiveModelParameters(second.model, {}, { ParamAngleX: 3 });
        let stopping;
        const stop = session.stop.bind(session);
        session.stop = reason => { stopping = stop(reason); return stopping; };
        session.tick();
        assert.ok(stopping);
        await stopping;
        assert.equal(second.value('ParamAngleX'), 3);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
        second.motionManager.update(0);
        assert.equal(second.value('ParamAngleX'), 3);
    } finally { f.release(); second.release(); }
});

test('same-session expression preflight cannot supersede a newer expression', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('expression');
        const gate = deferred(), entered = deferred();
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async file => {
            if (file === 'old.exp3.json') { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: file === 'old.exp3.json' ? 2 : 3, Blend: 'Overwrite' }] }) };
        };
        const old = f.stage.applyExpression({ file: 'old.exp3.json' });
        await entered.promise;
        assert.equal(await f.stage.applyExpression({ file: 'new.exp3.json' }), true);
        const listener = f.manager._manualExpressionListener;
        const ids = f.manager._activeExpressionParamIds;
        gate.resolve();
        assert.equal(await old, false);
        assert.equal(f.manager._manualExpressionListener, listener);
        assert.equal(f.manager._activeExpressionParamIds, ids);
        f.stage.release(s.id);
        assert.equal(f.manager._manualExpressionListener, null);
    } finally { f.release(); }
});

test('release invalidates a pending expression fetch while retaining an existing manual listener', async () => {
    const f = await fixture();
    try {
        f.manager.resolveAssetPath = file => file;
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 1 }], 50);
        const existing = f.manager._manualExpressionListener;
        const s = f.stage.acquire('expression');
        const gate = deferred(), entered = deferred();
        let calls = 0;
        f.runtime.context.fetch = async () => {
            if (++calls === 2) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 2 }] }) };
        };
        const pending = f.stage.applyExpression({ file: 'pending.exp3.json' });
        await entered.promise;
        f.stage.release(s.id);
        assert.equal(f.manager._manualExpressionListener, existing);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.manager._manualExpressionListener, existing);
    } finally { f.release(); }
});

test('expression SDK stop failure does not prevent parameter, marker and owner retirement', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('expression');
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async () => ({ ok: true,
            json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        assert.equal(await f.stage.applyExpression({ file: 'test.exp3.json' }), true);
        f.model.internalModel.motionManager.expressionManager = {
            stopAllExpressions() { throw new Error('controlled SDK stop failure'); }
        };
        f.core.setParameterValueById('ParamAngleX', 3);
        f.stage.frame({ x: 20 });
        assert.equal(f.stage.release(s.id), true);
        assert.equal(f.manager._manualExpressionListener, null);
        assert.equal(f.manager._activeTransientExpression, false);
        assert.equal(f.manager._activeExpressionParamIds, null);
        assert.equal(f.value('ParamAngleX'), 0);
        assert.equal(f.runtime.context._nekoAvatarPerformanceFrameContainer, null);
        assert.equal(f.manager._avatarPerformanceSessionOwners.size, 0);
    } finally { f.release(); }
});

test('standalone expression clear remains targeted and does not remove an unrelated manual listener', async () => {
    const f = await fixture();
    try {
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 1 }], 50);
        const existing = f.manager._manualExpressionListener;
        f.core.setParameterValueById('ParamAngleY', 7);
        await f.driver.applyExpression({ params: { ParamAngleX: 2 } });
        assert.equal(f.value('ParamAngleX'), 2);
        await f.driver.clearExpression();
        assert.equal(f.value('ParamAngleX'), 0);
        assert.equal(f.value('ParamAngleY'), 7);
        assert.equal(f.manager._manualExpressionListener, existing);
    } finally { f.release(); }
});

test('emotion bridge rejects a file response after model takeover', async () => {
    const f = await fixture();
    const replacement = createMao(f.runtime);
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        const gate = deferred(), entered = deferred();
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async () => { entered.resolve(); await gate.promise;
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 2 }] }) }; };
        const pending = bridge.queueLive2DExpressionApply('test.exp3.json');
        await entered.promise;
        f.manager.currentModel = replacement.model;
        replacement.core.setParameterValueById('ParamAngleX', 3);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.manager._manualExpressionListener, undefined);
        assert.equal(replacement.value('ParamAngleX'), 3);
    } finally { f.release(); replacement.release(); }
});

test('emotion bridge same-file retry and old cleanup cannot remove a newer manual expression', async () => {
    const f = await fixture();
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        const gate = deferred(), entered = deferred();
        let calls = 0;
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async () => {
            if (++calls === 1) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: calls }] }) };
        };
        const old = bridge.queueLive2DExpressionApply('same.exp3.json');
        await entered.promise;
        const next = bridge.queueLive2DExpressionApply('same.exp3.json');
        gate.resolve();
        assert.equal(await old, false);
        assert.equal(await next, true);
        assert.equal(calls, 2);
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 7 }], 50);
        const latest = f.manager._manualExpressionListener;
        const ids = vm.runInContext('new Set(["ParamAngleY"])', f.runtime.context);
        f.manager._activeExpressionParamIds = ids;
        bridge.clearLive2DGuideExpression();
        assert.equal(f.manager._manualExpressionListener, latest);
        assert.equal(f.manager._activeExpressionParamIds, ids);
    } finally { f.release(); }
});

test('timeline cancellation cleans owned pose and blink flags, retaining a newer saved value', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('pose');
        const pending = f.driver.runPoseTimeline({ durationMs: 1000, params: { angle: 'ParamAngleX' },
            computePose: () => ({ angle: 9 }) }, { session: s });
        await Promise.resolve();
        assert.equal(f.value('ParamAngleX'), 9);
        assert.equal(f.manager._suspendEyeBlinkOverride, true);
        assert.equal(f.manager._temporaryPoseOverrides.size, 1);
        f.apply({ ParamAngleX: 3 });
        f.stage.release(s.id);
        await pending;
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager._temporaryPoseOverrides.size, 0);
        assert.equal(f.manager._suspendEyeBlinkOverride, false);
    } finally { f.release(); }
});

test('manual takeover retires pending SDK playback and old release cannot change the new owner', async () => {
    const f = await fixture();
    try {
        f.manager.resolveAssetPath = file => file;
        const s = f.stage.acquire('expression');
        const gate = deferred(), entered = deferred();
        let calls = 0;
        f.runtime.context.fetch = async () => {
            if (++calls === 2) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 2 }] }) };
        };
        const pending = f.stage.applyExpression({ file: 'pending.exp3.json' });
        await entered.promise;
        f.manager._activeExpressionParamIds = vm.runInContext('new Set(["ParamAngleX"])', f.runtime.context);
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 3 }], 50);
        const listener = f.manager._manualExpressionListener;
        const ids = f.manager._activeExpressionParamIds;
        const generation = f.manager._transientExpressionGeneration;
        f.stage.release(s.id);
        assert.equal(f.manager._manualExpressionListener, listener);
        assert.equal(f.manager._activeExpressionParamIds, ids);
        assert.equal(f.manager._transientExpressionGeneration, generation);
        gate.resolve();
        assert.equal(await pending, false);
        assert.equal(f.manager._manualExpressionListener, listener);
        assert.equal(f.manager._activeExpressionParamIds, ids);
        assert.equal(f.manager._transientExpressionGeneration, generation);
    } finally { f.release(); }
});

test('Yui neutral animation yields to a same-model newer session before its late frame', async () => {
    const f = await fixture();
    try {
        const makeSession = token => new f.runtime.context.YuiGuideAvatarStage.Live2DIntroVoiceLookAtSession({
            manager: f.manager, model: f.model, coreModel: f.core, ticker: null
        }, { container: f.container, getPoint: () => null, token });
        const old = makeSession('old'), next = makeSession('next');
        assert.equal(old.start(), true);
        const stopping = old.stop('stopped');
        const releaseFrames = Array.from(f.frames.values());
        assert.ok(releaseFrames.length > 0, 'normal neutral animation is actually pending');
        assert.equal(next.start(), true);
        f.core.setParameterValueById('ParamAngleX', 7);
        releaseFrames.forEach(callback => callback(f.runtime.context.clock + 20));
        await stopping;
        assert.equal(f.value('ParamAngleX'), 7);
        assert.equal(next.stage.driver.ownsSession(next.stageSession), true);
        assert.ok(f.manager._temporaryPoseOverrides.has(next.poseOverrideSource));
        assert.equal(f.runtime.context.AvatarPerformance.isCapabilityLocked('main-live2d', 'lookAt'), true);
        await next.stop('replaced');
    } finally { f.release(); }
});

test('old Yui lock handle cannot decrement a replacement record after coordinator preemption', async () => {
    const f = await fixture();
    try {
        const makeSession = token => new f.runtime.context.YuiGuideAvatarStage.Live2DIntroVoiceLookAtSession({
            manager: f.manager, model: f.model, coreModel: f.core, ticker: null
        }, { container: f.container, getPoint: () => null, token });
        const old = makeSession('old'), next = makeSession('next');
        old.start();
        const coordinator = f.runtime.context.AvatarPerformance.getDefaultCoordinator();
        coordinator.acquire({ owner: 'preemptor', avatarId: 'main-live2d', priority: 100, force: true,
            capabilities: ['lookAt'] });
        next.start();
        const active = coordinator.getActiveSession('main-live2d');
        await old.stop('replaced');
        assert.equal(coordinator.getActiveSession('main-live2d'), active);
        assert.equal(f.runtime.context.AvatarPerformance.isCapabilityLocked('main-live2d', 'lookAt'), true);
        await next.stop('replaced');
    } finally { f.release(); }
});

test('editing and unavailable defaults never fall back to stale parameters or invented zero', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const s = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        f.manager._parameterEditingMode = true;
        f.core.setParameterValueById('ParamAngleX', 7);
        f.stage.release(s.id);
        assert.equal(f.value('ParamAngleX'), 7);
        f.manager._parameterEditingMode = false;
        f.apply({ ParamAngleX: 2 });
        const next = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        // The default must already be unavailable when the override is released;
        // a valid default is now restored during parameter publication itself.
        f.manager._getCoreParameterDefaultValue = () => undefined;
        f.apply({});
        f.stage.release(next.id);
        assert.equal(f.value('ParamAngleX'), 9);
        assert.equal(f.runtime.warnings.filter(args => String(args[0]).includes('unavailable defaults')).length, 1);
    } finally { f.release(); }
});

test('configuration and cross-driver ownership guards fail their regression scenarios when removed', async () => {
    const configMarker = 'const configChanged = manager._parameterConfigRevision !== state.snapshot.configRevision';
    const f = await fixture({ transformStage(source) {
        assert.ok(source.includes(configMarker));
        return source.replace(configMarker, 'const configChanged = false');
    } });
    try {
        f.apply({ ParamAngleX: 2 });
        const session = f.stage.acquire('pose');
        f.stage.setTemporaryParam('ParamAngleX', 9);
        f.apply({ ParamAngleX: 3 });
        f.stage.release(session.id);
        assert.throws(() => assert.equal(f.value('ParamAngleX'), 3), assert.AssertionError);
    } finally { f.release(); }
    const marker = 'ownsSession(session) {\n            if (this.ownerSession !== session) return false;';
    const g = await fixture({ transformStage(source) {
        assert.ok(source.includes(marker));
        return source.replace(marker, 'ownsSession(session) {\n            return this.ownerSession === session;');
    } });
    try {
        const old = g.stage.acquire('old');
        g.stage.setTemporaryParam('ParamAngleX', 2);
        const next = g.runtime.context.AvatarPerformance.createLive2DPerformance({ driverOptions: {
            managerResolver: () => g.manager, containerResolver: () => g.container
        } });
        const s = next.acquire('next');
        next.setTemporaryParam('ParamAngleX', 3);
        g.stage.release(old.id);
        assert.throws(() => assert.equal(g.value('ParamAngleX'), 3), assert.AssertionError);
        next.release(s.id);
    } finally { g.release(); }
});

test('same-session replacement timeline cannot be overwritten or unpaused by an older timeline', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('pose');
        const old = f.driver.runPoseTimeline({ durationMs: 1000, params: { angle: 'ParamAngleX' },
            computePose: () => ({ angle: 9 }) }, { session: s });
        await Promise.resolve();
        const next = f.driver.runPoseTimeline({ durationMs: 1000, params: { angle: 'ParamAngleX' },
            computePose: () => ({ angle: 7 }) }, { session: s });
        await Promise.resolve();
        f.manager._applyTemporaryPoseOverride(f.core);
        assert.equal(f.value('ParamAngleX'), 7);
        assert.equal(f.manager._suspendEyeBlinkOverride, true);
        assert.equal(f.manager._temporaryPoseOverrides.size, 1);
        await old;
        f.stage.release(s.id);
        await next;
        assert.equal(f.manager._suspendEyeBlinkOverride, false);
        assert.equal(f.manager._temporaryPoseOverrides.size, 0);
        assert.equal(f.value('ParamAngleX'), 0);
    } finally { f.release(); }
});

test('new same-source Yui pose retains both its entry and its actual Core values on old stop', async () => {
    const f = await fixture();
    try {
        const session = new f.runtime.context.YuiGuideAvatarStage.Live2DIntroVoiceLookAtSession({
            manager: f.manager, model: f.model, coreModel: f.core, ticker: null
        }, { container: f.container, getPoint: () => null, token: 'source-owner' });
        session.start();
        f.manager.setTemporaryPoseOverride(session.poseOverrideSource,
            core => core.setParameterValueById('ParamAngleX', 7));
        const next = f.manager._temporaryPoseOverrides.get(session.poseOverrideSource);
        f.manager._applyTemporaryPoseOverride(f.core);
        assert.equal(f.value('ParamAngleX'), 7);
        await session.stop('replaced');
        assert.equal(f.manager._temporaryPoseOverrides.get(session.poseOverrideSource), next);
        assert.equal(f.value('ParamAngleX'), 7);
    } finally { f.release(); }
});

test('expression receipt and pose-entry CAS regressions are detected when their guards are removed', async () => {
    const receipt = '&& state.expressionRequest === request';
    const f = await fixture({ transformStage(source) {
        assert.equal(source.split(receipt).length, 3);
        return source.replaceAll(receipt, '&& true');
    } });
    try {
        const s = f.stage.acquire('expression');
        const gate = deferred(), entered = deferred();
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async file => {
            if (file === 'old.exp3.json') { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: file === 'old.exp3.json' ? 2 : 3 }] }) };
        };
        const old = f.stage.applyExpression({ file: 'old.exp3.json' });
        await entered.promise;
        await f.stage.applyExpression({ file: 'new.exp3.json' });
        const newer = f.manager._manualExpressionListener;
        gate.resolve();
        await old;
        assert.throws(() => assert.equal(f.manager._manualExpressionListener, newer), assert.AssertionError);
        f.stage.release(s.id);
    } finally { f.release(); }
    const cas = 'manager.clearTemporaryPoseOverride(this.lookAtSource, entry)';
    const g = await fixture({ transformStage(source) {
        assert.equal(source.split(cas).length, 2);
        return source.replace(cas, 'manager.clearTemporaryPoseOverride(this.lookAtSource)');
    } });
    try {
        const s = g.stage.acquire('look', { capabilities: ['lookAt'] });
        g.stage.lookAt({ x: 900, y: 100 });
        const source = g.driver.lookAtSource;
        g.manager.setTemporaryPoseOverride(source, () => {});
        const newer = g.manager._temporaryPoseOverrides.get(source);
        g.stage.release(s.id);
        assert.throws(() => assert.equal(g.manager._temporaryPoseOverrides.get(source), newer), assert.AssertionError);
    } finally { g.release(); }
});

test('failed expression requests cannot claim and remove a pre-existing manual expression', async () => {
    const f = await fixture();
    try {
        f.manager.resolveAssetPath = file => file;
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 1 }], 50);
        const existing = f.manager._manualExpressionListener;
        const previousIds = vm.runInContext('new Set(["ParamAngleX"])', f.runtime.context);
        f.manager._activeExpressionParamIds = previousIds;
        const s = f.stage.acquire('expression');
        f.runtime.context.fetch = async () => ({ ok: false, statusText: 'controlled missing file' });
        assert.equal(await f.stage.applyExpression({ file: 'missing.exp3.json' }), false);
        f.stage.release(s.id);
        assert.equal(f.manager._manualExpressionListener, existing);
        assert.equal(f.manager._activeExpressionParamIds, previousIds);
    } finally { f.release(); }
});

test('a shared driver gives each acquisition an independent motion pause receipt', async () => {
    const f = await fixture();
    try {
        const old = f.stage.acquire('old');
        const oldToken = f.manager._temporaryMotionSuspendToken;
        const next = f.runtime.context.AvatarPerformance.createStage({ driver: f.driver });
        const s = next.acquire('next');
        const newer = f.manager._temporaryMotionSuspendToken;
        assert.equal(old.id, s.id);
        assert.notEqual(oldToken, newer);
        f.stage.release(old.id);
        assert.equal(f.manager._temporaryMotionSuspendToken, newer);
        assert.equal(f.driver.ownsSession(s), true);
        next.release(s.id);
        assert.equal(f.manager._temporaryMotionSuspendToken, null);
    } finally { f.release(); }
});


async function flushFrames(f, at) {
    f.runtime.context.clock = at;
    const callbacks = [...f.frames.values()];
    f.frames.clear();
    callbacks.forEach(callback => callback(at));
    await new Promise(resolve => setImmediate(resolve));
}

for (const parameters of [{ ParamAngleX: 3 }, {}]) {
    test(`public greeting naturally completes without reverting refreshed ${JSON.stringify(parameters)}`, async () => {
        const f = await fixture();
        try {
            f.apply({ ParamAngleX: 2 });
            const play = f.runtime.context.YuiGuideAvatarStage.playIntroGreetingHug({
                readyWaitMs: 0, approachMs: 100, settleMs: 100, releaseMs: 100, token: 'public-completion'
            });
            await new Promise(resolve => setImmediate(resolve));
            assert.ok(f.manager._temporaryPoseOverrides.size);
            f.runtime.context.clock = 150;
            f.core.update();
            f.manager._loadModelDirectoryParameters = async () => ({});
            f.manager.loadUserPreferences = async () => f.preferences(parameters);
            assert.equal((await f.manager.reloadModelParameters({ model_name: 'mao_pro' })).applied, true);
            await flushFrames(f, 500);
            const result = await play;
            assert.equal(result.result, 'played');
            const expected = parameters.ParamAngleX ?? f.core.getParameterDefaultValue(f.core.getParameterIndex('ParamAngleX'));
            assert.equal(f.value('ParamAngleX'), expected);
            assert.equal(f.manager._temporaryPoseOverrides.size, 0);
            f.motionManager.update(0);
            assert.equal(f.value('ParamAngleX'), expected);
        } finally { f.release(); }
    });
}

test('direct greeting switch cleanup respects newer nonzero configuration and aliases', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const Session = f.runtime.context.YuiGuideAvatarStage.Live2DIntroGreetingHugSession;
        const session = new Session({ manager: f.manager, model: f.model, coreModel: f.core }, { token: 'switch' });
        // Map a Yui-only switch to an existing real Mao SDK channel. Host tests
        // use Yui's actual switch; CI exercises the same cleanup algorithm.
        session.params.yuiHeartSwitch = session.params.angleY;
        assert.equal(session.start(), true);
        f.apply({ ParamAngleX: 3, ParamAngleY: 4 });
        const idx = f.core.getParameterIndex('ParamAngleY');
        f.manager.effectiveModelParameters = { ParamAngleX: 3, ['param_' + idx]: 4 };
        session.stop('played');
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.value('ParamAngleY'), 4);
    } finally { f.release(); }
});

test('direct pose old stop yields to new owner and same-source replacement', async () => {
    const f = await fixture();
    try {
        const api = f.runtime.context.YuiGuideAvatarStage;
        const context = { manager: f.manager, model: f.model, coreModel: f.core };
        const old = new api.Live2DIntroGreetingHugSession(context, { token: 'same-source' });
        assert.equal(old.start(), true);
        const next = new api.Live2DIntroGreetingHugSession(context, { token: 'same-source' });
        assert.equal(next.start(), true);
        f.core.setParameterValueById('ParamAngleX', 7);
        const entry = f.manager._temporaryPoseOverrides.get(next.poseOverrideSource);
        const frame = { x: f.model.x, y: f.model.y, scale: { ...f.model.scale } };
        old.stop('replaced');
        assert.equal(f.value('ParamAngleX'), 7);
        assert.equal(f.manager._temporaryPoseOverrides.get(next.poseOverrideSource), entry);
        assert.deepEqual({ x: f.model.x, y: f.model.y, scale: { ...f.model.scale } }, frame);
        assert.equal(next.isCurrentModel(), true);
        next.stop('played');
        assert.equal(f.manager._temporaryPoseOverrides.size, 0);
    } finally { f.release(); }
});

test('all direct pose consumers retain normal frames and restore accepted configuration', async () => {
    for (const name of ['Live2DWakeupSession', 'Live2DIntroGiftHeartSession', 'Live2DSettingsPeekPanicSession',
        'Live2DInterruptResistSession', 'Live2DAngryExitSession', 'Live2DGuideIdleSwaySession']) {
        const f = await fixture();
        try {
            f.apply({ ParamAngleX: 2 });
            const session = new f.runtime.context.YuiGuideAvatarStage[name]({
                manager: f.manager, model: f.model, coreModel: f.core
            }, { token: name, reducedMotion: true, releaseMs: 0 });
            assert.equal(session.start(), true, name + ' start');
            f.runtime.context.clock = 150;
            f.core.update();
            assert.equal(session.isCurrentModel(), true, name + ' normal callback owner');
            assert.ok(session.parameterRestoreState.written.has('ParamAngleX'), name + ' real parameter write');
            f.apply({ ParamAngleX: 3 });
            const stop = session.stop('played');
            if (stop?.then) { await flushFrames(f, 1000); await stop; }
            assert.equal(f.value('ParamAngleX'), 3, name + ' current config on stop');
            assert.equal(f.manager._temporaryPoseOverrides.size, 0, name + ' cleanup');
            assert.equal(!!f.manager._suspendEyeBlinkOverride, false, name + ' blink cleanup');
        } finally { f.release(); }
    }
});

test('manual expressions cannot write a new Core under the same internal model', async () => {
    const f = await fixture();
    const other = createMao(f.runtime);
    try {
        assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 3 }], 50), true);
        const emitter = f.model.internalModel;
        emitter.coreModel = other.core;
        other.core.setParameterValueById('ParamAngleX', 7);
        f.runtime.context.clock = 200;
        emitter.events.emit('beforeModelUpdate');
        assert.equal(other.value('ParamAngleX'), 7);
        assert.equal(f.manager._manualExpressionListener, null);
        emitter.coreModel = f.core;
    } finally { f.release(); other.release(); }
});

test('bridge removal failure retains its exact receipt and protection for a successful retry', async () => {
    const f = await fixture();
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        assert.equal(await bridge.queueLive2DExpressionApply('guide.exp3.json'), true);
        const owner = bridge.guideExpressionOwner;
        const emitter = f.model.internalModel;
        const off = emitter.off;
        emitter.off = () => { throw new Error('off failure'); };
        assert.equal(bridge.clearLive2DGuideExpression(), false);
        assert.equal(bridge.guideExpressionOwner, owner);
        assert.equal(f.manager._manualExpressionListener, owner.listener);
        assert.equal(f.manager._activeExpressionParamIds, owner.paramIds);
        emitter.off = off;
        assert.equal(bridge.clearLive2DGuideExpression(), true);
        assert.equal(bridge.guideExpressionOwner, null);
        assert.equal(f.manager._manualExpressionListener, null);
        assert.equal(f.manager._activeExpressionParamIds, null);
    } finally { f.release(); }
});

test('bridge registration failure cannot publish unregistered listener or new parameter protection', async () => {
    const f = await fixture();
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        const ids = f.manager._activeExpressionParamIds;
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        const emitter = f.model.internalModel;
        const on = emitter.on;
        emitter.on = () => { throw new Error('on failure'); };
        await assert.rejects(bridge.queueLive2DExpressionApply('guide.exp3.json'), /on failure/);
        assert.equal(bridge.guideExpressionOwner, null);
        assert.equal(f.manager._manualExpressionListener ?? null, null);
        assert.equal(f.manager._activeExpressionParamIds, ids);
        emitter.on = on;
        assert.equal(await bridge.queueLive2DExpressionApply('guide.exp3.json'), true);
        assert.equal(bridge.clearLive2DGuideExpression(), true);
    } finally { f.release(); }
});

test('failed guide replacement keeps the existing registered manual expression and its protection', async () => {
    const f = await fixture();
    try {
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 3 }] }) });
        assert.equal(await bridge.queueLive2DExpressionApply('first.exp3.json'), true);
        const owner = bridge.guideExpressionOwner;
        const emitter = f.model.internalModel;
        const on = emitter.on;
        emitter.on = () => { throw new Error('replacement registration failure'); };
        await assert.rejects(bridge.queueLive2DExpressionApply('second.exp3.json'), /replacement registration failure/);
        assert.equal(bridge.guideExpressionOwner, owner);
        assert.equal(f.manager._manualExpressionListener, owner.listener);
        assert.equal(f.manager._activeExpressionParamIds, owner.paramIds);
        f.runtime.context.clock = 1000;
        emitter.events.emit('beforeModelUpdate');
        assert.equal(f.value('ParamAngleX'), 3);
        emitter.on = on;
        assert.equal(bridge.clearLive2DGuideExpression(), true);
    } finally { f.release(); }
});

for (const latest of [{ ParamAngleX: 3 }, {}]) {
    test(`direct pose start captures the accepted baseline after construction ${JSON.stringify(latest)}`, async () => {
        const f = await fixture();
        try {
            f.apply({ ParamAngleX: 2 });
            const session = new f.runtime.context.YuiGuideAvatarStage.Live2DIntroGreetingHugSession({
                manager: f.manager, model: f.model, coreModel: f.core
            }, { token: 'ctor-start-gap' });
            f.apply(latest);
            const baseline = f.value('ParamAngleX');
            assert.equal(session.start(), true);
            session.stop('cancelled');
            assert.equal(f.value('ParamAngleX'), baseline);
            f.motionManager.update(0);
            assert.equal(f.value('ParamAngleX'), baseline);
        } finally { f.release(); }
    });
}

test('failed bridge registration preserves old Stage expression cleanup authority', async () => {
    const f = await fixture();
    try {
        f.manager.resolveAssetPath = file => file;
        f.runtime.context.fetch = async file => ({ ok: true, json: async () => ({
            Parameters: [{ Id: file === 'stage.exp3.json' ? 'ParamAngleX' : 'ParamAngleY', Value: 3 }]
        }) });
        const session = f.stage.acquire('manual-expression');
        assert.equal(await f.stage.applyExpression({ file: 'stage.exp3.json' }), true);
        f.runtime.context.clock = 1000;
        f.manager._manualExpressionListener();
        const listener = f.manager._manualExpressionListener;
        const generation = f.manager._transientExpressionGeneration;
        const bridge = new f.runtime.context.__YuiGuideDirector.YuiGuideEmotionBridge();
        const emitter = f.model.internalModel;
        const on = emitter.on;
        emitter.on = () => { throw new Error('bridge registration failure'); };
        await assert.rejects(bridge.queueLive2DExpressionApply('bridge.exp3.json'), /bridge registration failure/);
        assert.equal(f.manager._transientExpressionGeneration, generation);
        assert.equal(f.manager._manualExpressionListener, listener);
        emitter.on = on;
        assert.equal(f.stage.release(session.id), true);
        assert.equal(f.manager._manualExpressionListener, null);
        assert.equal(f.manager._activeExpressionParamIds, null);
        assert.equal(f.manager._activeTransientExpression, false);
        assert.equal(f.value('ParamAngleX'), 0);
    } finally { f.release(); }
});

test('public resistance resumes the paused greeting with its configuration and coordinator lock', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 2 });
        const api = f.runtime.context.YuiGuideAvatarStage;
        const greeting = api.playIntroGreetingHug({ readyWaitMs: 0, approachMs: 1000, settleMs: 1000, releaseMs: 1000 });
        await new Promise(resolve => setImmediate(resolve));
        const original = f.manager._avatarPerformanceSessionOwners.get('params');
        const interruption = api.playInterruptResist({ readyWaitMs: 0, reducedMotion: true });
        await new Promise(resolve => setImmediate(resolve));
        await flushFrames(f, 150);
        assert.equal(original.active, true, 'greeting pauses while resistance plays');
        f.apply({ ParamAngleX: 3 });
        await flushFrames(f, 2000);
        await interruption;
        await flushFrames(f, 2050);
        assert.equal(original.active, true, 'paused greeting must resume instead of ending model_changed');
        assert.equal(original.isCurrentModel(), true);
        assert.equal(original.performanceLock.isCurrent(), true);
        assert.equal(f.manager._avatarPerformanceSessionOwners.get('params'), original);
        await flushFrames(f, 10000);
        await flushFrames(f, 10050);
        assert.equal((await greeting).result, 'played');
        assert.equal(f.value('ParamAngleX'), 3);
        assert.equal(f.manager._temporaryPoseOverrides.size, 0);
        assert.equal(f.manager._avatarPerformanceSessionOwners.size, 0);
    } finally { f.release(); }
});

for (const name of ['Live2DWakeupSession', 'Live2DIntroGiftHeartSession', 'Live2DSettingsPeekPanicSession']) {
    test(`resistance returns its temporary claims to ${name}`, async () => {
        const f = await fixture();
        try {
            const api = f.runtime.context.YuiGuideAvatarStage;
            const original = new api[name]({ manager: f.manager, model: f.model, coreModel: f.core }, { token: name });
            assert.equal(original.start(), true);
            const interruption = api.playInterruptResist({ readyWaitMs: 0, reducedMotion: true });
            await new Promise(resolve => setImmediate(resolve));
            await flushFrames(f, 150);
            f.apply({});
            await flushFrames(f, 2000);
            await interruption;
            assert.equal(original.isCurrentModel(), true);
            assert.equal(original.performanceLock.isCurrent(), true);
            const stopped = original.stop('cancelled');
            if (stopped?.then) { await flushFrames(f, 3000); await stopped; }
            assert.equal(f.manager._avatarPerformanceSessionOwners.size, 0);
            assert.equal(!!f.manager._suspendEyeBlinkOverride, false);
        } finally { f.release(); }
    });
}

for (const takeover of ['session', 'core', 'lock', 'cancel']) {
    test(`resistance cannot revive a paused pose after ${takeover} takeover`, async () => {
        const f = await fixture();
        const nextCore = takeover === 'core' ? createMao(f.runtime) : null;
        try {
            const api = f.runtime.context.YuiGuideAvatarStage;
            const original = api.createWakeupSession({ manager: f.manager, model: f.model, coreModel: f.core }, {});
            assert.equal(original.start(), true);
            const interruption = api.playInterruptResist({ readyWaitMs: 0, reducedMotion: true });
            await new Promise(resolve => setImmediate(resolve));
            await flushFrames(f, 150);
            let successor;
            const coordinator = f.runtime.context.AvatarPerformance.getDefaultCoordinator();
            if (takeover === 'session') {
                successor = f.stage.acquire('successor');
                f.stage.setTemporaryParam('ParamAngleX', 7);
            } else if (takeover === 'core') {
                f.model.internalModel.coreModel = nextCore.core;
                nextCore.core.setParameterValueById('ParamAngleX', 7);
            } else if (takeover === 'lock') {
                successor = coordinator.acquire({ owner: 'successor', avatarId: 'main-live2d', force: true, capabilities: ['motion'] });
            } else {
                original.stop('cancelled');
            }
            await flushFrames(f, 2000);
            await interruption;
            assert.equal(f.manager._avatarPerformanceSessionOwners.get('params') === original, false);
            if (takeover === 'session') {
                assert.equal(f.driver.ownsSession(successor), true);
                assert.equal(f.value('ParamAngleX'), 7);
                f.stage.release(successor.id);
            } else if (takeover === 'core') {
                assert.equal(nextCore.value('ParamAngleX'), 7);
                f.model.internalModel.coreModel = f.core;
            } else if (takeover === 'lock') {
                assert.equal(successor.cancelled, false, 'resume must not force-preempt a successor lock');
                coordinator.release(successor);
            }
            original.stop('cancelled');
            assert.equal(!!f.manager._suspendEyeBlinkOverride, false);
        } finally { f.release(); nextCore?.release(); }
    });
}

for (const mutation of ['restore', 'lock']) {
    test(`removing the resistance ${mutation} guard reproduces its regression`, async () => {
        const marker = mutation === 'restore'
            ? 'state.capabilities.forEach(capability => owners.set(capability, session));'
            : 'if (coordinator && state.capabilities.some(capability => coordinator.isCapabilityLocked(YUI_GUIDE_AVATAR_ID, capability))) return;';
        const f = await fixture({ transformYui(source) {
            assert.equal(source.split(marker).length, 2, 'mutation must remove exactly one live guard');
            return source.replace(marker, '/* removed guard */');
        } });
        try {
            const api = f.runtime.context.YuiGuideAvatarStage;
            const original = api.createWakeupSession({ manager: f.manager, model: f.model, coreModel: f.core }, {});
            assert.equal(original.start(), true);
            const interruption = api.playInterruptResist({ readyWaitMs: 0, reducedMotion: true });
            await new Promise(resolve => setImmediate(resolve));
            await flushFrames(f, 150);
            const coordinator = f.runtime.context.AvatarPerformance.getDefaultCoordinator();
            const successor = mutation === 'lock'
                ? coordinator.acquire({ owner: 'new motion', avatarId: 'main-live2d', capabilities: ['motion'] }) : null;
            await flushFrames(f, 2000);
            await interruption;
            if (mutation === 'restore') assert.equal(original.isCurrentModel(), false, 'the normal resume assertion fails without restoration');
            else assert.equal(successor.cancelled, true, 'priority-based acquisition evicts the successor without the guard');
            original.stop('cancelled');
            if (successor) coordinator.release(successor);
        } finally { f.release(); }
    });
}

test('a retired resistance cannot unfreeze overlay tickers owned by a successor', async () => {
    const f = await fixture();
    try {
        const ticks = new Set();
        let calls = 0;
        const anchor = () => { calls++; };
        f.manager._floatingButtonsTicker = anchor;
        f.manager.pixi_app = { ticker: { add: tick => ticks.add(tick), remove: tick => ticks.delete(tick) } };
        ticks.add(anchor);
        const api = f.runtime.context.YuiGuideAvatarStage;
        const context = { manager: f.manager, model: f.model, coreModel: f.core };
        const old = new api.Live2DInterruptResistSession(context, { reducedMotion: true });
        assert.equal(old.start(), true);
        const successor = new api.Live2DSettingsPeekPanicSession(context, { reducedMotion: true });
        assert.equal(successor.start(), true);
        old.stop('replaced');
        assert.equal(ticks.has(anchor), false);
        assert.equal(calls, 0, 'stale stop must not invoke a shared anchor callback');
        assert.equal(successor.isCurrentModel(), true);
        await successor.stop('cancelled');
        assert.equal(ticks.has(anchor), true);
        assert.equal(calls, 1);
    } finally { f.release(); }
});

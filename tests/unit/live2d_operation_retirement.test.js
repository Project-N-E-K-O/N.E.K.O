const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { read, loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

async function fixture(options = {}) {
    const runtime = await loadRuntime(options);
    for (const file of ['static/live2d/live2d-emotion.js', 'static/avatar/avatar-performance-stage.js']) {
        let source = read(file);
        if (options.transformStage && file.includes('avatar-performance')) source = options.transformStage(source);
        if (options.transformEmotion && file.includes('live2d-emotion')) source = options.transformEmotion(source);
        const transformed = (options.transformStage && file.includes('avatar-performance'))
            || (options.transformEmotion && file.includes('live2d-emotion'));
        vm.runInContext(source, runtime.context, { filename: path.resolve(__dirname, '../../', transformed ? 'mutation/' + path.basename(file) : file) });
    }
    const f = createMao(runtime), api = runtime.context.PIXI.live2d;
    const settings = new api.Cubism4ModelSettings({ ...f.settings, url: 'http://localhost/mao/mao_pro.model3.json' });
    const native = new api.Cubism4MotionManager(settings, { motionPreload: api.MotionPreloadStrategy.NONE });
    const emitter = new EventEmitter();
    Object.assign(f.model.internalModel, { motionManager: native, events: emitter,
        on: emitter.on.bind(emitter), off: emitter.off.bind(emitter) });
    f.model.motion = (...args) => native.startMotion(...args);
    f.manager.fileReferences = { Motions: native.definitions, Expressions: [] };
    f.manager.emotionMapping = { expressions: {} };
    f.manager.resolveAssetPath = file => file;
    f.manager.recordInitialParameters();
    const group = Object.keys(native.definitions).find(key => key !== 'Idle' && native.definitions[key].length);
    const file = native.definitions[group][0].File;
    const data = JSON.parse(fs.readFileSync(path.join(f.root, file)));
    f.motion = () => runtime.sdk.Motion.create(data);
    native._loadMotion = async () => f.motion();
    f.container = { style: {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 600 }) };
    f.stage = runtime.context.AvatarPerformance.createStage({ driver:
        runtime.context.AvatarPerformance.createLive2DDriver({ managerResolver: () => f.manager, containerResolver: () => f.container }) });
    f.native = native; f.group = group; f.runtime = runtime;
    const releaseCore = f.release;
    f.release = () => {
        if (f.stage.activeSession) f.stage.release(f.stage.activeSession.id, 'cleanup');
        f.manager._cancelAvatarOperations();
        native.destroy(); releaseCore();
    };
    return f;
}

async function cancelledNative(options = {}) {
    const f = await fixture(options), entered = deferred(), gate = deferred();
    let starts = 0;
    try {
        f.native.on('motionStart', () => starts++);
        f.native._loadMotion = async () => { entered.resolve(); await gate.promise; return f.motion(); };
        const session = f.stage.acquire('pending-native');
        const pending = f.stage.playMotion({ group: f.group, index: 0 });
        await entered.promise;
        assert.equal(f.native.state.reservePriority, 2, 'real SDK reservation reached');
        f.stage.release(session.id, 'cancelled');
        // Result must settle without waiting for the native asset response.
        if (!options.unprotected) assert.equal(await pending, false);
        gate.resolve();
        assert.equal(await pending, false);
        await new Promise(resolve => setImmediate(resolve));
        return { starts, priority: f.native.state.currentPriority, owners: f.manager._avatarPerformanceSessionOwners.size };
    } finally { gate.resolve(); f.release(); }
}

test('cancel before the real SDK loader returns revokes its reservation and settles before the response', async () => {
    assert.deepEqual(await cancelledNative(), { starts: 0, priority: 0, owners: 0 });
});

test('a cancelled same-index request cannot consume or stop the successor SDK reservation', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    let loads = 0, starts = 0;
    try {
        f.native.on('motionStart', () => starts++);
        f.native._loadMotion = async () => { if (++loads === 1) { entered.resolve(); await gate.promise; } return f.motion(); };
        const old = f.stage.acquire('old'), pending = f.stage.playMotion({ group: f.group, index: 0 });
        await entered.promise;
        f.stage.release(old.id, 'cancelled');
        const next = f.stage.acquire('next');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        const owner = f.manager._activeActionMotionOperation;
        assert.ok(owner && f.native.state.currentPriority === 2);
        gate.resolve(); assert.equal(await pending, false);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(starts, 1);
        assert.equal(f.manager._activeActionMotionOperation, owner);
        assert.equal(f.manager._avatarPerformanceSessionOwners.get('motion'), next);
        assert.equal(f.stage.release(old.id), false);
        assert.equal(f.native.state.currentPriority, 2);
    } finally { gate.resolve(); f.release(); }
});

test('release stops its already admitted real SDK action', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('active');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        assert.equal(f.native.state.currentPriority, 2);
        f.stage.release(s.id, 'cancelled');
        assert.equal(f.native.state.currentPriority, 0);
        assert.equal(f.manager._activeActionMotionOperation, null);
        assert.equal(f.manager._avatarOperations.size, 0);
    } finally { f.release(); }
});

for (const loading of [true, false]) {
    test(`SDK reset failure still retires its ${loading ? 'pending' : 'active'} operation and permits explicit reset retry`, async () => {
        const f = await fixture(), entered = deferred(), gate = deferred();
        const sdkReset = f.native.state.reset, error = new Error('controlled SDK reset failure');
        let pending;
        try {
            f.native.state.reset = () => { throw error; };
            if (loading) f.native._loadMotion = async () => { entered.resolve(); await gate.promise; return f.motion(); };
            pending = f.manager.playActionMotion(f.group, 0);
            if (loading) await entered.promise;
            else assert.equal(await pending, true);
            const operation = loading ? f.manager._actionMotionRequestPendingOperation : f.manager._activeActionMotionOperation;
            assert.ok(operation);
            assert.throws(() => f.native.stopAllMotions(), thrown => thrown === error);
            if (loading) {
                assert.equal(operation.signal.aborted, true, 'reset failure must abort the detached pending receipt');
                assert.equal(await pending, false);
                assert.equal(f.manager._actionMotionRequestPendingOperation, null);
            } else assert.ok(!f.manager._activeActionMotionOperation, 'reset failure must retire the detached active receipt');
            assert.equal(f.manager._avatarOperations.size, 0);
            // A failed SDK reset is not reported as an idle/native success.
            assert.ok(f.native.state.currentPriority || f.native.state.reservePriority);
            f.native.state.reset = sdkReset; f.native.stopAllMotions();
            gate.resolve(); await pending;
            assert.equal(await f.manager.playActionMotion(f.group, 0), true);
        } finally { f.native.state.reset = sdkReset; gate.resolve(); if (pending) await pending.catch(() => false); f.release(); }
    });
}

for (const idle of [true, false]) for (const saved of [null, 3]) {
test(`real SDK natural completion restores motion-only parameters with Idle=${idle} and saved=${saved}`, async () => {
    const f = await fixture();
    try {
        const data = { Version: 3, Meta: { Duration: 1, Fps: 30, Loop: false, AreBeziersRestricted: true,
            CurveCount: 1, TotalSegmentCount: 1, TotalPointCount: 2, UserDataCount: 0, TotalUserDataSize: 0 },
            Curves: [{ Target: 'Parameter', Id: 'ParamAngleX', Segments: [0, 9, 0, 1, 9], FadeInTime: 0, FadeOutTime: 0 }], UserData: [] };
        const idleData = { ...data, Meta: { ...data.Meta, Loop: true, CurveCount: 0, TotalSegmentCount: 0, TotalPointCount: 0 }, Curves: [] };
        f.native._loadMotion = async group => f.runtime.sdk.Motion.create(group === 'Idle' ? idleData : data);
        if (!idle) f.native.definitions.Idle = [];
        if (saved !== null) f.apply({ ParamAngleX: saved });
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => data });
        const events = [];
        f.native.on('motionStart', group => events.push(`start:${group}:${f.native.state.currentPriority}`));
        f.native.on('motionFinish', () => events.push(`finish:${f.native.state.currentPriority}`));
        assert.equal(await f.manager.playMotion(f.group), true);
        const timer = f.manager.motionTimer, baseline = f.manager._findRecordedParameterBaseline('ParamAngleX', f.core, { includeSavedParameters: true }).value;
        f.native.update(f.core, 0); f.native.update(f.core, 500);
        assert.equal(f.value('ParamAngleX'), 9 + (saved || 0), 'actual parsed motion and saved offset must drive the Core parameter');
        for (let time = 1000; time <= 3000 && (!events.includes('finish:2') || (idle && !events.some(event => event.startsWith('start:Idle')))); time += 100) {
            f.native.update(f.core, time); await new Promise(resolve => setImmediate(resolve));
        }
        assert.ok(events.includes('finish:2'), 'real SDK emits completion before its priority is reset');
        assert.equal(events.includes('start:Idle:1'), idle, 'real SDK idle admission follows registered resources');
        assert.equal(f.value('ParamAngleX'), saved === null ? baseline : saved, 'natural retirement and the saved layer recover the accepted parameter');
        assert.equal(f.manager._activeActionMotionOperation, null);
        assert.equal(f.manager.motionTimer, null);
        assert.equal(timer.id.cancelled, true);
        assert.equal(f.manager._avatarOperations.size, 0);
    } finally { f.release(); }
});
}

test('old release preserves a newer direct SDK editor/preview action', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('stage');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        assert.ok(f.native.definitions[f.group].length > 1);
        assert.equal(await f.model.motion(f.group, 1, 3), true, 'real force admission takes over another registered index');
        f.stage.release(s.id, 'cancelled');
        assert.equal(f.native.state.currentPriority, 3);
        assert.equal(f.native.state.currentGroup, f.group);
        assert.equal(f.native.state.currentIndex, 1);
    } finally { f.release(); }
});

test('a cancelled native load deadline is bounded and its late result cannot start', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        f.native._loadMotion = async () => { entered.resolve(); await gate.promise; return f.motion(); };
        const pending = f.manager.playActionMotion(f.group, 0);
        await entered.promise;
        const operation = f.manager._actionMotionRequestPendingOperation;
        const deadline = f.runtime.timers.find(timer => !timer.cancelled && timer.delay === 15000);
        assert.ok(operation && deadline);
        deadline.callback();
        assert.equal(await pending, false);
        gate.resolve(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.native.state.currentPriority, 0);
        assert.equal(f.manager._avatarOperations.size, 0);
    } finally { gate.resolve(); f.release(); }
});

test('retiring an active action preserves a foreign force reservation that is still loading', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        const s = f.stage.acquire('predecessor');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        f.native._loadMotion = async () => { entered.resolve(); await gate.promise; return f.motion(); };
        const foreign = f.model.motion(f.group, 1, 3);
        await entered.promise;
        f.stage.release(s.id, 'cancelled');
        assert.equal(f.native.state.reservedIndex, 1);
        assert.equal(f.native.state.reservePriority, 3);
        gate.resolve();
        assert.equal(await foreign, true);
        assert.equal(f.native.state.currentPriority, 3);
        assert.equal(f.native.state.currentIndex, 1);
    } finally { gate.resolve(); f.release(); }
});

test('retiring a motion does not stop a newer speaking resource on the same SDK manager', async () => {
    const f = await fixture();
    try {
        const s = f.stage.acquire('motion');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        const audio = { owner: 'new-speaking-resource' };
        let stopped = 0;
        f.native.currentAudio = audio;
        const stopSpeaking = f.native.stopSpeaking;
        f.native.stopSpeaking = () => { stopped++; f.native.currentAudio = undefined; };
        f.stage.release(s.id, 'cancelled');
        assert.equal(f.native.state.currentPriority, 0);
        assert.equal(f.native.currentAudio, audio);
        assert.equal(stopped, 0);
        f.native.currentAudio = undefined; f.native.stopSpeaking = stopSpeaking;
    } finally { f.release(); }
});

async function permissionDuringAwait(transformStage) {
    const f = await fixture({ transformStage }), entered = deferred(), gate = deferred();
    const coordinator = f.runtime.context.AvatarPerformance.getDefaultCoordinator();
    let lease;
    try {
        let calls = 0;
        f.runtime.context.fetch = async () => {
            if (++calls === 2) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 7 }] }) };
        };
        const s = f.stage.acquire('expression'), pending = f.stage.applyExpression({ file: 'held.exp3.json' });
        await entered.promise;
        lease = coordinator.acquire({ owner: 'lock-owner', avatarId: 'main-live2d', capabilities: ['motion'] });
        const started = await f.manager.playActionMotion(f.group, 0);
        const bypass = f.manager._avatarPerformanceBypassLocks;
        f.stage.release(s.id, 'cancelled');
        gate.resolve(); await pending;
        return { started, bypass };
    } finally { gate.resolve(); if (lease) coordinator.release(lease); f.release(); }
}

test('unscoped motion admission respects a real coordinator lock while a Stage expression awaits', async () => {
    assert.deepEqual(await permissionDuringAwait(), { started: false, bypass: false });
});

test('restoring the old async global bypass reproduces the unscoped SDK admission', async () => {
    const result = await permissionDuringAwait(source => {
        const scoped = 'const result = callback();\n                restore();\n                return Promise.resolve(result);';
        assert.ok(source.includes(scoped));
        return source.replace(scoped, 'return Promise.resolve(callback()).finally(restore);');
    });
    assert.deepEqual(result, { started: true, bypass: true });
});

test('model removal cancels a pending real native load before destroying its manager', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        f.native._loadMotion = async () => { entered.resolve(); await gate.promise; return f.motion(); };
        const pending = f.manager.playActionMotion(f.group, 0);
        await entered.promise;
        await f.manager.removeModel({ skipCloseWindows: true });
        assert.equal(await pending, false);
        gate.resolve(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.manager._actionMotionRequestPendingModel, null);
    } finally { gate.resolve(); f.release(); }
});

test('normal native retirement restores the recorded baseline and preserves saved-parameter recovery', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 3 });
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Meta: { Duration: 2 }, Curves: [{ Target: 'Parameter', Id: 'ParamAngleX' }] }) });
        assert.equal(await f.manager.playMotion(f.group), true);
        const timer = f.manager.motionTimer;
        f.core.setParameterValueById('ParamAngleX', 9);
        f.native.stopAllMotions();
        assert.equal(f.value('ParamAngleX'), f.manager._findRecordedParameterBaseline('ParamAngleX', f.core, { includeSavedParameters: true }).value);
        assert.equal(f.manager._activeMotionParamIds, null);
        assert.equal(f.manager._activeActionMotionOperation, null);
        assert.equal(timer.id.cancelled, true);
        f.native.update(f.core, 0);
        f.core.update();
        assert.equal(f.value('ParamAngleX'), 3, 'the saved-parameter layer still restores its accepted value');
    } finally { f.release(); }
});

test('a real SDK load rejection frees reservation, busy admission and its operation record', async () => {
    const f = await fixture();
    try {
        f.native._loadMotion = async () => { throw Error('controlled native load rejection'); };
        await assert.rejects(f.manager.playActionMotion(f.group, 0), /controlled native load rejection/);
        assert.equal(f.native.state.reservePriority, 0);
        assert.equal(f.manager._actionMotionRequestPendingModel, null);
        assert.equal(f.manager._avatarOperations.size, 0);
    } finally { f.release(); }
});

test('an already retired emotion request creates no timer or operation receipt', async () => {
    const f = await fixture();
    try {
        const timers = f.runtime.timers.length;
        assert.equal(await f.manager.setEmotion('happy', { isCurrent: () => false }), false);
        assert.equal(f.runtime.timers.length, timers);
        assert.equal(f.manager._avatarOperations?.size || 0, 0);
    } finally { f.release(); }
});

for (const decode of [false, true]) {
    test(`Stage cancellation retires expression ${decode ? 'JSON decode' : 'fetch'} and frees busy and bypass`, async () => {
        const f = await fixture(), entered = deferred(), gate = deferred();
        try {
            f.manager.emotionMapping.expressions = { happy: ['held.exp3.json'] };
            f.runtime.context.fetch = async () => {
                if (f.manager.isEmotionChanging && !decode) { entered.resolve(); await gate.promise; }
                return { ok: true, json: async () => {
                    if (f.manager.isEmotionChanging && decode) { entered.resolve(); await gate.promise; }
                    return { Parameters: [{ Id: 'ParamAngleX', Value: 7 }] };
                } };
            };
            const s = f.stage.acquire('fetch'), pending = f.stage.applyEmotion('happy');
            await entered.promise;
            f.stage.release(s.id, 'cancelled');
            assert.equal(await pending, false, 'must end without releasing the response/decode');
            assert.equal(f.manager.isEmotionChanging, false);
            assert.equal(f.manager._actionMotionRequestPendingModel, null);
            assert.equal(f.manager._avatarPerformanceBypassLocks, false);
            const next = f.stage.acquire('next');
            assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
            gate.resolve(); await new Promise(resolve => setImmediate(resolve));
            assert.equal(f.manager._avatarPerformanceSessionOwners.get('motion'), next);
            assert.equal(f.native.state.currentPriority, 2);
            assert.equal(f.manager._manualExpressionListener ?? null, null);
        } finally { gate.resolve(); f.release(); }
    });
}

test('a new expression can enter after cancellation while the old fetch remains blocked', async () => {
    const f = await fixture(), entered = deferred(), gate = deferred();
    try {
        let requests = 0;
        f.runtime.context.fetch = async () => {
            if (++requests === 2) { entered.resolve(); await gate.promise; }
            return { ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 4 }] }) };
        };
        const old = f.stage.acquire('old'), pending = f.stage.applyExpression({ file: 'held.exp3.json' });
        await entered.promise;
        f.stage.release(old.id, 'cancelled'); assert.equal(await pending, false);
        f.stage.acquire('next');
        assert.equal(await f.stage.applyExpression({ file: 'next.exp3.json' }), true);
        const listener = f.manager._manualExpressionListener;
        gate.resolve(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.manager._manualExpressionListener, listener);
    } finally { gate.resolve(); f.release(); }
});

test('failed off logically retires a listener and preserves a bounded retry receipt', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamAngleX: 3 });
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Parameters: [{ Id: 'ParamAngleX', Value: 7 }] }) });
        const s = f.stage.acquire('off');
        assert.equal(await f.stage.applyExpression({ file: 'shape.exp3.json' }), true);
        const emitter = f.model.internalModel, off = emitter.off, listener = f.manager._manualExpressionListener;
        emitter.off = () => { throw Error('controlled physical off failure'); };
        f.stage.release(s.id, 'cancelled');
        assert.equal(f.manager._manualExpressionListener, null);
        assert.equal(f.manager._retiredManualExpressions.size, 1);
        assert.equal(emitter.events.listenerCount('beforeModelUpdate'), 1);
        f.runtime.context.clock = 1000;
        f.core.setParameterValueById('ParamAngleX', 3); emitter.events.emit('beforeModelUpdate');
        assert.equal(f.value('ParamAngleX'), 3, 'retired listener is already unable to write');
        // Retrying cannot add more inactive listeners while physical off is broken.
        assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 9 }], 50), false);
        assert.equal(emitter.events.listenerCount('beforeModelUpdate'), 1);
        emitter.off = off;
        f.manager._retryRetiredManualExpressions(emitter);
        assert.equal(f.manager._retiredManualExpressions.size, 0);
        assert.equal(emitter.events.listeners('beforeModelUpdate').includes(listener), false);
        assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 9 }], 50), true);
        const next = f.manager._manualExpressionListener;
        f.manager._retryRetiredManualExpressions(emitter);
        assert.equal(f.manager._manualExpressionListener, next);
        f.manager._removeManualExpressionOverride();
    } finally { f.release(); }
});

test('physical cleanup retry stops scheduling after its budget but leaves the receipt for explicit recovery', async () => {
    const f = await fixture();
    try {
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 7 }], 50);
        const listener = f.manager._manualExpressionListener, emitter = f.model.internalModel, off = emitter.off;
        emitter.off = () => { throw Error('off unavailable'); };
        assert.equal(f.manager._removeManualExpressionOverride(listener, emitter, { retire: true }), false);
        for (let attempt = 0; attempt < 3; attempt++) {
            const resource = Array.from(f.manager._retiredManualExpressions)[0];
            assert.ok(resource.timer); resource.timer.callback();
        }
        const resource = Array.from(f.manager._retiredManualExpressions)[0];
        assert.equal(resource.timer, null); assert.equal(resource.active, false);
        emitter.off = off; f.manager._retryRetiredManualExpressions(emitter);
        assert.equal(f.manager._retiredManualExpressions.size, 0);
    } finally { f.release(); }
});

test('a registered but unpublished listener retains its cleanup receipt when ownership changes and off fails', async () => {
    const f = await fixture();
    try {
        const emitter = f.model.internalModel, on = emitter.on, off = emitter.off;
        emitter.on = (event, listener) => {
            on(event, listener);
            f.manager._transientExpressionGeneration = (f.manager._transientExpressionGeneration || 0) + 1;
        };
        emitter.off = () => { throw Error('unpublished off failure'); };
        assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 7 }], 50), false);
        assert.equal(f.manager._manualExpressionListener || null, null);
        assert.equal(f.manager._retiredManualExpressions.size, 1);
        f.core.setParameterValueById('ParamAngleX', 3); emitter.events.emit('beforeModelUpdate');
        assert.equal(f.value('ParamAngleX'), 3);
        emitter.on = on; emitter.off = off; f.manager._retryRetiredManualExpressions(emitter);
        assert.equal(emitter.events.listenerCount('beforeModelUpdate'), 0);
        assert.equal(f.manager._retiredManualExpressions.size, 0);
    } finally { f.release(); }
});

test('retiring a native action also cancels its manager end timer and prevents a later successor stop', async () => {
    const f = await fixture();
    try {
        f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({ Meta: { Duration: 2 }, Curves: [] }) });
        const s = f.stage.acquire('emotion');
        assert.equal(await f.stage.applyEmotion(f.group), true);
        const timer = f.manager.motionTimer;
        assert.ok(timer && timer.operation);
        f.stage.release(s.id, 'cancelled');
        assert.equal(timer.id.cancelled, true);
        f.stage.acquire('successor');
        assert.equal(await f.stage.playMotion({ group: f.group, index: 0 }), true);
        const owner = f.manager._activeActionMotionOperation;
        timer.id.callback();
        assert.equal(f.manager._activeActionMotionOperation, owner);
        assert.equal(f.native.state.currentPriority, 2);
    } finally { f.release(); }
});

test('removing SDK cancellation fences restores the actual SDK late-start regression', async () => {
    const result = await cancelledNative({ unprotected: true, transformModelSource(source) {
        const listener = "operation.signal.addEventListener('abort', retire, { once: true });";
        const loader = "if (typeof load === 'function') motionManager.loadMotion = guardedLoad;";
        const stale = 'if (!operation.isCurrent()) { retire(); return false; }';
        assert.ok(source.includes(listener) && source.includes(loader) && source.includes(stale));
        return source.replace(listener, '/* native reservation cancellation removed */')
            .replace(loader, '/* guarded native loader removed */')
            .replace(stale, 'if (!operation.isCurrent()) return false;');
    } });
    assert.deepEqual(result, { starts: 1, priority: 2, owners: 0 });
});

test('removing logical listener retirement reproduces a retired Core write despite a retained cleanup receipt', async () => {
    const f = await fixture({ transformEmotion(source) {
        assert.ok(source.includes('if (!resource.active) return;'));
        assert.ok(source.includes('if (self._manualExpressionListener !== onBeforeUpdate) return;'));
        return source.replaceAll('if (!resource.active) return;', '')
            .replaceAll('if (self._manualExpressionListener !== onBeforeUpdate) return;', '')
            .replace('if (self._manualExpressionListener !== onBeforeUpdate || self.currentModel !== model', 'if (self.currentModel !== model');
    } });
    try {
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleX', Value: 7 }], 50);
        const emitter = f.model.internalModel, listener = f.manager._manualExpressionListener, off = emitter.off;
        emitter.off = () => { throw Error('physical off failure'); };
        f.manager._removeManualExpressionOverride(listener, emitter, { retire: true });
        f.runtime.context.clock = 1000;
        f.core.setParameterValueById('ParamAngleX', 3); emitter.events.emit('beforeModelUpdate');
        assert.equal(f.value('ParamAngleX'), 7);
        emitter.off = off; f.manager._retryRetiredManualExpressions(emitter);
    } finally { f.release(); }
});

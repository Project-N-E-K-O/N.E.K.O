const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { read, loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

async function fixture({ manual = false } = {}) {
    const runtime = await loadRuntime();
    runtime.context.document.body.classList.remove = () => {};
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
            if (manual) {
                f.runtime.context.clock += 220;
                f.model.internalModel.events.emit('beforeModelUpdate');
            } else {
                const expressions = f.native.expressionManager;
                const fadeIn = expressions.currentExpression.getFadeInTime();
                assert.ok(fadeIn > 0);
                expressions.update(f.core, 0);
                assert.equal(f.value('ParamAngleX'), 0, 'native expression starts at zero fade weight');
                expressions.update(f.core, fadeIn / 2);
                assert.ok(f.value('ParamAngleX') > 0 && f.value('ParamAngleX') < 7);
                expressions.update(f.core, fadeIn);
            }
            assert.equal(f.value('ParamAngleX'), 7, 'the expression must actually write the Core while motion is paused');
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

function installMainIdleRestore(f, options = {}) {
    const source = read('static/app/app-interpage/bootstrap-resources-and-model-reload.js');
    const start = source.indexOf('    function _injectMotionGroupSafely(');
    const exportLine = 'window.restoreLive2DIdleAnimationOnMainPage = restoreLive2DIdleAnimationOnMainPage;';
    const end = source.indexOf(exportLine, start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(source.slice(start, end + exportLine.length), f.runtime.context);
    f.runtime.context.live2dManager = f.manager;
    f.runtime.context.lanlan_config = { lanlan_name: 'test' };
    const savedFile = options.savedFile || f.native.definitions.Idle[0].File;
    const motionFiles = options.motionFiles || [savedFile];
    let configuredIdle = savedFile;
    const fetch = f.runtime.context.fetch;
    f.runtime.context.fetch = async (url, ...args) => {
        if (url === '/api/characters') return { json: async () => ({ 猫娘: { test: {
            live2d: 'mao_pro', _reserved: { avatar: { live2d: { idle_animation: configuredIdle } } }
        } } }) };
        if (url === '/api/live2d/model_files/mao_pro') return { json: async () => ({ motion_files: motionFiles }) };
        return fetch(url, ...args);
    };
    return { savedFile, select(file) { configuredIdle = file; }, clearSelection() { configuredIdle = ''; } };
}

test('main-page saved idle deferred by an expression resumes through the common scheduler', async () => {
    const f = await fixture();
    try {
        const main = installMainIdleRestore(f);
        f.manager.setupIdleMotionLoop(f.model);
        await f.manager.playExpression('happy', 'happy.exp3.json');
        await f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
        assert.deepEqual(Array.from(f.manager._userIdleAnimations), [main.savedFile]);
        assert.equal(f.native.state.currentPriority, 0, 'saved idle cannot interrupt the expression');
        await f.manager.clearExpression();
        [...f.manager._idleMotionLoopTimers][0].callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.native.state.currentGroup, 'PreviewAll', 'restore the saved group rather than default Idle');
        assert.equal(f.native.state.currentPriority, 1);
        assert.equal(f.native.definitions.PreviewAll[f.native.state.currentIndex].File, main.savedFile);
        main.clearSelection();
        await f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
        assert.deepEqual(Array.from(f.manager._userIdleAnimations), []);
    } finally { f.release(); }
});

for (const selectedFile of ['idle/wave.motion3.json', 'wave.motion3.json']) {
    test(`saved idle ${selectedFile} does not match another directory's same-name motion`, async () => {
        const f = await fixture();
        try {
            installMainIdleRestore(f, {
                savedFile: selectedFile, motionFiles: ['actions/wave.motion3.json', selectedFile]
            });
            vm.runInContext('Math.random = () => 0.99;', f.runtime.context);
            f.manager.setupIdleMotionLoop(f.model);
            await f.manager.playExpression('happy', 'happy.exp3.json');
            await f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
            assert.deepEqual(Array.from(f.manager._userIdleAnimations), [selectedFile]);
            await f.manager.clearExpression();
            [...f.manager._idleMotionLoopTimers][0].callback();
            await new Promise(resolve => setImmediate(resolve));
            assert.equal(f.native.state.currentGroup, 'PreviewAll');
            assert.equal(f.native.definitions.PreviewAll[f.native.state.currentIndex].File, selectedFile);
        } finally { f.release(); }
    });
}

test('an old main-page idle request cannot clear the selection of a replacement model', async () => {
    const f = await fixture(), next = createMao(f.runtime), entered = deferred(), gate = deferred();
    try {
        const main = installMainIdleRestore(f);
        main.clearSelection();
        const fetch = f.runtime.context.fetch;
        f.runtime.context.fetch = async (url, ...args) => {
            if (url === '/api/characters') { entered.resolve(); await gate.promise; }
            return fetch(url, ...args);
        };
        const pending = f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
        await entered.promise;
        f.manager.currentModel = next.model;
        f.manager._userIdleAnimations = ['new.motion3.json'];
        gate.resolve();
        await pending;
        assert.deepEqual(Array.from(f.manager._userIdleAnimations), ['new.motion3.json']);
    } finally { gate.resolve(); f.release(); next.release(); }
});

for (const nextSelection of ['custom/new.motion3.json', '']) {
    test(`a newer idle ${nextSelection ? 'selection' : 'clear'} supersedes an older load on the same model`, async () => {
        const f = await fixture(), entered = deferred(), gate = deferred();
        try {
            const oldFile = 'custom/old.motion3.json';
            const main = installMainIdleRestore(f, {
                savedFile: oldFile, motionFiles: [oldFile, 'custom/new.motion3.json']
            });
            const load = f.native.loadMotion;
            f.native.loadMotion = async function(group, index, ...args) {
                if (group === 'PreviewAll' && index === 0) { entered.resolve(); await gate.promise; }
                return load.call(this, group, index, ...args);
            };
            const oldRestore = f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
            await entered.promise;
            main.select(nextSelection);
            await f.runtime.context.restoreLive2DIdleAnimationOnMainPage();
            gate.resolve();
            await oldRestore;
            assert.deepEqual(Array.from(f.manager._userIdleAnimations), nextSelection ? [nextSelection] : []);
            if (nextSelection) {
                assert.equal(f.native.definitions.PreviewAll[f.native.state.currentIndex].File, nextSelection);
            } else {
                assert.equal(f.native.state.currentPriority, 0, 'the obsolete load cannot restart an explicitly cleared idle');
            }
        } finally { gate.resolve(); f.release(); }
    });
}

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

test('switching from a transient expression to a motion-only emotion clears the expression before starting the action', async () => {
    const f = await fixture();
    try {
        f.native.definitions.sad = f.native.definitions.happy;
        f.native.motionGroups.sad = [];
        f.manager.fileReferences.Motions.sad = f.native.definitions.sad;
        await f.manager.setEmotion('happy');
        assert.equal(f.manager.hasActiveTransientExpression(), true);
        let expressionAtMotionStart;
        f.native.on('motionStart', () => { expressionAtMotionStart = f.manager.hasActiveTransientExpression(); });
        await f.manager.setEmotion('sad');
        assert.equal(f.manager.hasActiveTransientExpression(), false);
        assert.equal(f.native.state.currentGroup, 'sad');
        assert.equal(expressionAtMotionStart, false, 'clear the old expression before admitting the new motion');
        assert.equal(f.native.state.currentPriority, 2);
        assert.equal(f.manager._actionMotionRequestPendingModel, null);
    } finally { f.release(); }
});

test('Idle without an expression preserves the current transient expression and does not start a motion', async () => {
    const f = await fixture();
    try {
        await f.manager.setEmotion('happy');
        await f.manager.setEmotion('Idle');
        assert.equal(f.manager.hasActiveTransientExpression(), true);
        assert.equal(f.native.state.currentPriority, 0);
    } finally { f.release(); }
});

for (const knownMissing of [false, true]) {
    test(`a ${knownMissing ? 'known missing' : 'failed'} expression falls back to its configured motion`, async () => {
        const f = await fixture();
        try {
            if (knownMissing) f.manager.markExpressionFileMissing('happy.exp3.json');
            f.runtime.context.fetch = async file => file.endsWith('.exp3.json')
                ? { ok: false, statusText: 'Not Found' }
                : { ok: true, json: async () => ({ Meta: { Duration: 1 }, Curves: [] }) };
            await f.manager.setEmotion('happy');
            assert.equal(f.manager.hasActiveTransientExpression(), false);
            assert.equal(f.native.state.currentGroup, 'happy');
            assert.equal(f.native.state.currentPriority, 2);
        } finally { f.release(); }
    });
}

for (const fading of [false, true]) {
    test(`model removal clears ${fading ? 'fading' : 'manual'} expression ownership before the next model`, async () => {
        const f = await fixture({ manual: true }), next = createMao(f.runtime);
        try {
            await f.manager.playExpression('happy', 'happy.exp3.json');
            const fade = fading ? f.manager.smoothResetToInitialState(800) : null;
            assert.equal(f.manager.hasActiveTransientExpression(), true);
            f.manager._userIdleAnimations = ['old.motion3.json'];
            await f.manager.removeModel({ skipCloseWindows: true });
            if (fade) await fade;
            assert.equal(f.model.internalModel.events.listenerCount('beforeModelUpdate'), 0);
            assert.equal(f.manager._manualExpressionListener, null);
            assert.equal(f.manager._smoothResetListener, null);
            assert.deepEqual(Array.from(f.manager._userIdleAnimations), []);
            f.manager.currentModel = next.model;
            next.motionManager.state.currentPriority = 0;
            assert.equal(f.manager.hasActiveTransientExpression(), false);
            f.manager.playSimpleMotion('happy');
            assert.equal(next.value('ParamAngleY'), 8, 'old expression state cannot block the new model');
        } finally { f.release(); next.release(); }
    });
}

test('click fallback cannot add a transient expression to an occupied action', async () => {
    const f = await fixture();
    try {
        assert.equal(await f.manager.playActionMotion('happy', 0), true);
        assert.equal(await f.manager._playTemporaryClickEffect('happy'), false);
        assert.equal(f.native.state.currentPriority, 2);
        assert.equal(f.manager.hasActiveTransientExpression(), false);
    } finally { f.release(); }
});

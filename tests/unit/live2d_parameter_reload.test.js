const assert = require('node:assert/strict');
const test = require('node:test');
const { loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

const runtimePromise = loadRuntime();
async function withMao(callback) {
    const fixture = createMao(await runtimePromise);
    try { await callback(fixture); } finally { fixture.release(); }
}

async function reorderedReads(f, phase) {
    const gate = deferred(), entered = deferred();
    let calls = 0;
    if (phase === 'directory') {
        f.manager._loadModelDirectoryParameters = async () => {
            if (++calls === 1) { entered.resolve(); return gate.promise; }
            return { ParamAngleY: 8 };
        };
        f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 1 });
    } else {
        f.manager._loadModelDirectoryParameters = async () => ({});
        f.manager.loadUserPreferences = async () => {
            if (++calls === 1) { entered.resolve(); return gate.promise; }
            return f.preferences({ ParamAngleX: 8 });
        };
    }
    const older = f.manager.reloadModelParameters();
    await entered.promise;
    const newer = await f.manager.reloadModelParameters();
    assert.equal(newer.applied, true);
    const id = phase === 'directory' ? 'ParamAngleY' : 'ParamAngleX';
    const revision = f.manager._parameterConfigRevision;
    gate.resolve(phase === 'directory' ? { ParamAngleY: 5 } : f.preferences({ ParamAngleX: 5 }));
    const oldResult = await older;
    assert.equal(oldResult.applied, false);
    assert.equal(oldResult.reason, 'superseded');
    assert.equal(f.manager.savedModelParameters[id], 8);
    assert.equal(f.manager.effectiveModelParameters[id], 8);
    assert.equal(f.value(id), 8);
    assert.equal(f.manager._parameterConfigRevision, revision);
    f.writes = { [id]: 4 };
    f.motionManager.update(0);
    assert.equal(f.value(id), 12, 'the cache must also belong to the winning configuration');
}

for (const phase of ['directory', 'preferences']) {
    test(`same-model ${phase} reordering cannot overwrite the newer saved value or offset`,
        () => withMao(f => reorderedReads(f, phase)));
    test(`model/Core takeover during ${phase} await rejects the old result`, () => withMao(async f => {
        const other = createMao(f.runtime), gate = deferred(), entered = deferred();
        try {
            f.manager._loadModelDirectoryParameters = async () => {
                if (phase === 'directory') { entered.resolve(); await gate.promise; }
                return {};
            };
            f.manager.loadUserPreferences = async () => {
                if (phase === 'preferences') { entered.resolve(); await gate.promise; }
                return f.preferences({ ParamAngleX: 5 });
            };
            const pending = f.manager.reloadModelParameters();
            await entered.promise;
            f.manager.currentModel = other.model;
            gate.resolve();
            const result = await pending;
            assert.equal(result.reason, 'model_mismatch');
            assert.equal(result.applied, false);
            assert.equal(other.value('ParamAngleX'), 0);
            assert.equal(f.manager._parameterConfigRevision, 0);
        } finally { other.release(); }
    }));
}

for (const phase of ['directory', 'preferences']) for (const failure of ['reject', 'empty']) {
    test(`late ${phase} ${failure} becomes superseded instead of a fallback-triggering error`,
        () => withMao(async f => {
            const gate = deferred(), entered = deferred();
            let calls = 0;
            f.manager._loadModelDirectoryParameters = async () => {
                if (phase === 'directory' && ++calls === 1) { entered.resolve(); return gate.promise; }
                return {};
            };
            f.manager.loadUserPreferences = async () => {
                if (phase === 'preferences' && ++calls === 1) { entered.resolve(); return gate.promise; }
                return f.preferences({ ParamAngleX: 8 });
            };
            const warnings = f.runtime.warnings.length;
            const pending = f.manager.reloadModelParameters();
            await entered.promise;
            assert.equal((await f.manager.reloadModelParameters()).applied, true);
            const revision = f.manager._parameterConfigRevision;
            if (failure === 'reject') gate.reject(new Error('stale read failed'));
            else gate.resolve(phase === 'directory' ? {} : []);
            assert.equal((await pending).reason, 'superseded');
            assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
            assert.equal(f.manager._parameterConfigRevision, revision);
            assert.equal(f.runtime.warnings.length, warnings, 'caller emits no stale directory failure warning');
        }));
}

test('latest valid request failure does not reactivate an older successful read', () => withMao(async f => {
    f.apply({ ParamAngleX: 2 });
    const revision = f.manager._parameterConfigRevision;
    const gate = deferred(), entered = deferred();
    let calls = 0;
    f.manager._loadModelDirectoryParameters = async () => ({});
    f.manager.loadUserPreferences = async () => {
        if (++calls === 1) { entered.resolve(); return gate.promise; }
        return [];
    };
    const older = f.manager.reloadModelParameters();
    await entered.promise;
    await assert.rejects(f.manager.reloadModelParameters(), error => {
        assert.equal(f.manager._getParameterReloadTicketStatus(error._parameterReloadTicket), 'active');
        return /未找到当前模型/.test(error.message);
    });
    gate.resolve(f.preferences({ ParamAngleX: 5 }));
    assert.equal((await older).reason, 'superseded');
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 2);
    assert.equal(f.manager._parameterConfigRevision, revision);
}));

test('current directory failure retains preference fallback and only the current request warns', () => withMao(async f => {
    f.manager._loadModelDirectoryParameters = async () => { throw new Error('directory unavailable'); };
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 3 });
    const warnings = f.runtime.warnings.length;
    assert.equal((await f.manager.reloadModelParameters()).applied, true);
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 3);
    assert.equal(f.runtime.warnings.length, warnings + 1);
}));

test('preference transport failures return an empty list, then current reload emits its original error with a receipt', () => withMao(async f => {
    f.runtime.context.fetch = async () => { throw new Error('network failure'); };
    f.manager._loadModelDirectoryParameters = async () => ({ ParamAngleX: 5 });
    const before = f.manager._parameterConfigRevision;
    await assert.rejects(f.manager.reloadModelParameters(), error => {
        assert.match(error.message, /未找到当前模型/);
        assert.equal(f.manager._getParameterReloadTicketStatus(error._parameterReloadTicket), 'active');
        return true;
    });
    assert.equal(f.manager._parameterConfigRevision, before);
    assert.equal(f.manager.savedModelParameters, null);
}));

test('mismatched notifications do not revoke an already accepted valid request', () => withMao(async f => {
    const gate = deferred(), entered = deferred();
    f.manager._loadModelDirectoryParameters = async () => { entered.resolve(); return gate.promise; };
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 5 });
    const pending = f.manager.reloadModelParameters();
    await entered.promise;
    const sequence = f.manager._parameterReloadSequence;
    const wrong = await f.manager.reloadModelParameters({ model_path: '/static/other/other.model3.json' });
    assert.equal(wrong.reason, 'model_mismatch');
    assert.equal(f.manager._parameterReloadSequence, sequence);
    gate.resolve({});
    assert.equal((await pending).applied, true);
}));

test('another direct config publication invalidates a reader even when its model stays identical', () => withMao(async f => {
    const gate = deferred(), entered = deferred();
    f.manager._loadModelDirectoryParameters = async () => { entered.resolve(); return gate.promise; };
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 5 });
    const pending = f.manager.reloadModelParameters();
    await entered.promise;
    f.apply({ ParamAngleX: 8 });
    gate.resolve({});
    assert.equal((await pending).reason, 'superseded');
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
}));

test('empty config and deleted entries publish a new revision and rebuild the offset snapshot', () => withMao(f => {
    f.apply({ ParamAngleX: 2, ParamAngleY: 3 });
    const first = f.manager._parameterConfigRevision;
    f.apply({ ParamAngleY: 5 });
    assert.equal(f.manager._parameterConfigRevision, first + 1);
    f.writes = { ParamAngleX: 4, ParamAngleY: 4 };
    f.motionManager.update(0);
    assert.equal(f.value('ParamAngleX'), 4);
    assert.equal(f.value('ParamAngleY'), 9);
    f.apply({});
    assert.equal(f.manager._parameterConfigRevision, first + 2);
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._shouldApplySavedParams, false);
    f.motionManager.update(1);
    assert.equal(f.value('ParamAngleY'), 4);
}));

test('override reinstall does not advance the confirmed configuration revision', () => withMao(f => {
    f.apply({ ParamAngleX: 2 });
    const ticket = f.manager._createParameterReloadTicket();
    f.manager.installMouthOverride();
    assert.equal(f.manager._getParameterReloadTicketStatus(ticket), 'active');
    assert.equal(f.manager._parameterConfigRevision, ticket.configRevision);
}));

test('installation failure never reports success and its receipt includes the config already published', () => withMao(async f => {
    f.manager._loadModelDirectoryParameters = async () => ({});
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 5 });
    f.manager.installMouthOverride = () => { throw new Error('installation failed'); };
    await assert.rejects(f.manager.reloadModelParameters(), error => {
        assert.match(error.message, /installation failed/);
        assert.equal(f.manager._getParameterReloadTicketStatus(error._parameterReloadTicket), 'active');
        assert.equal(error._parameterReloadTicket.configRevision, 1);
        return true;
    });
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 5);
}));

test('a publication followed by SDK write failure disables the old saved cache while Core still advances', () => withMao(async f => {
    f.apply({ ParamAngleX: 2 });
    f.manager._loadModelDirectoryParameters = async () => ({});
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 8 });
    f.manager.applyModelParameters = () => { throw new Error('SDK write failed before reinstall'); };
    await assert.rejects(f.manager.reloadModelParameters(), /SDK write failed before reinstall/);
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
    f.writes = { ParamAngleX: 4 };
    f.motionManager.update(0);
    f.core.update();
    assert.equal(f.value('ParamAngleX'), 4, 'retired offset 2 must not write after revision 8 is published');
    assert.equal(f.coreCalls, 1, 'mouth/renderer Core channel remains active until fallback');
}));

test('synchronous installer takeover then throw cannot attach a current fallback receipt to an old request', () => withMao(async f => {
    f.manager._loadModelDirectoryParameters = async () => ({});
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 5 });
    const original = f.manager.installMouthOverride.bind(f.manager);
    f.manager.installMouthOverride = () => {
        f.manager.installMouthOverride = original;
        f.apply({ ParamAngleX: 8 });
        throw new Error('obsolete installer failed after takeover');
    };
    const result = await f.manager.reloadModelParameters();
    assert.equal(result.applied, false);
    assert.equal(result.reason, 'superseded');
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
    assert.equal(f.manager._parameterConfigRevision, 2);
}));

test('unload/reset retires tickets even if the same object and path are later reused', () => withMao(async f => {
    const gate = deferred(), entered = deferred();
    f.manager._loadModelDirectoryParameters = async () => { entered.resolve(); return gate.promise; };
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 5 });
    const pending = f.manager.reloadModelParameters();
    await entered.promise;
    await f.manager.removeModel({ skipCloseWindows: true });
    const revision = f.manager._parameterConfigRevision;
    f.manager.currentModel = f.model;
    f.manager.modelName = 'mao_pro';
    f.manager._lastLoadedModelPath = '/static/mao_pro/mao_pro.model3.json';
    gate.resolve({});
    assert.equal((await pending).reason, 'superseded');
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._parameterConfigRevision, revision);
}));

// UI/renderer timing is controlled; parameter reading/publication, tickets and
// the actual _configureLoadedModel body are never replaced.
function prepareConfiguration(f) {
    f.manager.pixi_app = { stage: { addChild() {}, removeChild() {} },
        ticker: { start() {}, stop() {} }, view: { style: {} } };
    f.manager.applyModelSettings = () => {};
    f.manager.recordInitialParameters = () => {};
    f.manager._preTickPhysics = async () => {};
    f.manager._waitForModelVisualStability = async () => {};
    return { minimalEmbed: true, loadEmotionMapping: false, suppressPersistentExpressions: true,
        suppressInitialIdle: true, preferences: { parameters: { ParamAngleX: 2 } } };
}

test('cold load honors an explicit empty snapshot over a stale directory mirror', () => withMao(async f => {
    const options = prepareConfiguration(f);
    options.preferences = { parameters: {}, parameters_mode: 'replace' };
    f.manager._loadModelDirectoryParameters = async () => ({ ParamAngleX: 12 });
    await f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
    assert.equal(f.manager._modelLoadState, 'ready');
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._shouldApplySavedParams, false);
    assert.equal(f.value('ParamAngleX'), 0);
}));

test('accepting a new load invalidates parameter tickets before its asynchronous model factory completes', () => withMao(async f => {
    const options = prepareConfiguration(f), gate = deferred(), entered = deferred();
    const other = createMao(f.runtime);
    const factory = f.runtime.context.Live2DModel.from;
    const ticket = f.manager._createParameterReloadTicket();
    const modelPath = f.manager._lastLoadedModelPath;
    f.runtime.context.Live2DModel.from = async () => { entered.resolve(); return gate.promise; };
    f.manager._loadModelDirectoryParameters = async () => ({});
    try {
        const pending = f.manager.loadModel(modelPath, options);
        await entered.promise;
        assert.ok(f.manager._parameterReloadSequence > ticket.sequence);
        assert.notEqual(f.manager._getParameterReloadTicketStatus(ticket), 'active');
        gate.resolve(other.model);
        assert.equal(await pending, other.model);
        assert.equal(f.manager._modelLoadState, 'ready');
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 2);
    } finally {
        f.runtime.context.Live2DModel.from = factory;
        other.release();
    }
}));

test('cold-load parameters cannot replace a hot-refresh winner; the valid overall load still becomes ready', () => withMao(async f => {
    const options = prepareConfiguration(f), gate = deferred(), entered = deferred();
    let calls = 0;
    f.manager._loadModelDirectoryParameters = async () => {
        if (++calls === 1) { entered.resolve(); return gate.promise; }
        return {};
    };
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 8 });
    const load = f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
    await entered.promise;
    assert.equal((await f.manager.reloadModelParameters()).applied, true);
    const revision = f.manager._parameterConfigRevision;
    gate.resolve({ ParamAngleX: 5 });
    await load;
    assert.equal(f.manager._modelLoadState, 'ready');
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
    assert.equal(f.manager._parameterConfigRevision, revision);
}));

test('latest hot failure does not revive old cold-load parameters', () => withMao(async f => {
    const options = prepareConfiguration(f), gate = deferred(), entered = deferred();
    let calls = 0;
    f.manager._loadModelDirectoryParameters = async () => {
        if (++calls === 1) { entered.resolve(); return gate.promise; }
        return {};
    };
    f.manager.loadUserPreferences = async () => [];
    const load = f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
    await entered.promise;
    await assert.rejects(f.manager.reloadModelParameters(), /未找到当前模型/);
    gate.resolve({ ParamAngleX: 5 });
    await load;
    assert.equal(f.manager._modelLoadState, 'ready');
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._parameterConfigRevision, 0);
}));

test('cold-load directory rejection after load-token retirement does not publish parameters or finish the old model', () => withMao(async f => {
    const options = prepareConfiguration(f), gate = deferred(), entered = deferred();
    f.manager._loadModelDirectoryParameters = async () => { entered.resolve(); return gate.promise; };
    const pending = f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
    await entered.promise;
    f.manager._activeLoadToken++;
    gate.reject(new Error('old directory unavailable'));
    await pending;
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._parameterConfigRevision, 0);
    assert.notEqual(f.manager._modelLoadState, 'ready');
}));

test('a refresh accepted during cold-load persistent setup retires its older parameter ticket', () => withMao(async f => {
    const options = prepareConfiguration(f), gate = deferred(), entered = deferred();
    options.suppressPersistentExpressions = false;
    f.manager.syncEmotionMappingWithServer = async () => {};
    f.manager.setupPersistentExpressions = async () => { entered.resolve(); await gate.promise; };
    f.manager._loadModelDirectoryParameters = async () => ({});
    f.manager.loadUserPreferences = async () => f.preferences({ ParamAngleX: 8 });
    const pending = f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
    await entered.promise;
    assert.equal((await f.manager.reloadModelParameters()).applied, true);
    gate.resolve();
    await pending;
    assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
    assert.equal(f.manager._modelLoadState, 'ready');
}));

test('request-fence mutation fails the same-model stale-result guard', async () => {
    const marker = "return this._parameterReloadSequence === ticket.sequence";
    const runtime = await loadRuntime({ transformModelSource(source) {
        assert.equal(source.split(marker).length, 2);
        return source.replace(marker, 'return true');
    } });
    const f = createMao(runtime);
    // Remove the revision comparison too: this mutation intentionally disables
    // the shared request/configuration fence while retaining identity checks.
    const status = f.manager._getParameterReloadTicketStatus.bind(f.manager);
    f.manager._getParameterReloadTicketStatus = ticket => status(ticket) === 'model_mismatch' ? 'model_mismatch' : 'active';
    try {
        await assert.rejects(reorderedReads(f, 'preferences'), { code: 'ERR_ASSERTION' });
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 5, 'mutation must actually publish the stale result');
    } finally { f.release(); }
});

test('optional pose and motion CAS cannot clean or resume a replacement owner', () => withMao(f => {
    const manager = f.manager;
    manager.setTemporaryPoseOverride('same-source', () => {});
    const first = manager._temporaryPoseOverrides.get('same-source');
    manager.setTemporaryPoseOverride('same-source', () => {});
    const second = manager._temporaryPoseOverrides.get('same-source');
    manager.clearTemporaryPoseOverride('same-source', first);
    assert.equal(manager._temporaryPoseOverrides.get('same-source'), second);
    manager.setTemporaryPoseOverride('throws', () => {
        manager.setTemporaryPoseOverride('throws', () => {});
        throw new Error('old callback threw after replacing itself');
    });
    const failed = manager._temporaryPoseOverrides.get('throws');
    manager._applyTemporaryPoseOverride(f.core);
    assert.notEqual(manager._temporaryPoseOverrides.get('throws'), failed);
    let resumed = 0;
    manager.setupIdleMotionLoop = () => { resumed++; };
    manager._temporaryMotionSuspendToken = null;
    assert.equal(manager.resumeTemporaryMotions('old', f.model), false);
    manager._temporaryMotionSuspendToken = 'new';
    assert.equal(manager.resumeTemporaryMotions('old', f.model), false);
    assert.equal(manager.resumeTemporaryMotions('new', {}), false);
    assert.equal(manager.resumeTemporaryMotions('new', f.model), true);
    assert.equal(resumed, 1);
}));

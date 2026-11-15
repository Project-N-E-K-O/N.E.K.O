const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { loadRuntime, createMao, deferred } = require('./fixtures/live2d_runtime_fixture.cjs');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-interpage/bootstrap-resources-and-model-reload.js'), 'utf8');
const start = source.indexOf('    I.handleReloadModelParametersMessage = async function');
const end = source.indexOf('    /**', start);
assert.ok(start >= 0 && end > start, 'production parameter reload handler must exist');
// Preserve source offsets for useful stack traces and changed-line coverage.
const handlerSource = source.slice(0, start).replace(/[^\r\n]/g, ' ') + source.slice(start, end);
const handlerFilename = path.join(__dirname, '../../static/app/app-interpage/bootstrap-resources-and-model-reload.js');

function harness() {
    const coreModel = {};
    const model = { internalModel: { coreModel } };
    const manager = {
        currentModel: model, modelName: 'mao_pro',
        _lastLoadedModelPath: '/static/mao_pro/mao_pro.model3.json',
        _parameterReloadSequence: 1, _parameterConfigRevision: 1,
        _getParameterReloadTicketStatus(ticket) {
            if (this.currentModel !== ticket.model || this.currentModel?.internalModel?.coreModel !== ticket.coreModel) return 'model_mismatch';
            return this._parameterReloadSequence === ticket.sequence && this._parameterConfigRevision === ticket.configRevision
                ? 'active' : 'superseded';
        },
        reloadModelParameters: async () => ({ applied: true }),
    };
    let fallbackCalls = 0;
    const context = { console: { warn() {}, log() {} },
        I: { handleModelReload: async () => { fallbackCalls++; } },
        window: { live2dManager: manager } };
    vm.createContext(context);
    vm.runInContext(handlerSource, context, { filename: handlerFilename });
    const ticket = () => ({ sequence: manager._parameterReloadSequence,
        configRevision: manager._parameterConfigRevision, model: manager.currentModel,
        internalModel: manager.currentModel?.internalModel, coreModel: manager.currentModel?.internalModel?.coreModel,
        modelName: manager.modelName, modelPath: manager._lastLoadedModelPath });
    return { context, manager, ticket, get fallbackCalls() { return fallbackCalls; },
        run: () => context.I.handleReloadModelParametersMessage({ model_name: 'mao_pro', model_path: manager._lastLoadedModelPath }) };
}

test('active successful parameter refresh reports applied without a full reload', async () => {
    const h = harness();
    assert.equal(await h.run(), true);
    assert.equal(h.fallbackCalls, 0);
});

for (const reason of ['model_mismatch', 'superseded']) {
    test(`${reason} is an ordinary unapplied result, not a reload failure`, async () => {
        const h = harness();
        h.manager.reloadModelParameters = async () => ({ applied: false, reason });
        assert.equal(await h.run(), false);
        assert.equal(h.fallbackCalls, 0);
    });
}

test('a failed current request preserves the existing full reload fallback', async () => {
    const h = harness();
    const error = Object.assign(new Error('preferences read failed'), { _parameterReloadTicket: h.ticket() });
    h.manager.reloadModelParameters = async () => { throw error; };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 1);
});

test('a ticket retired before rejection cannot initiate a full reload', async () => {
    const h = harness();
    const error = Object.assign(new Error('late failure'), { _parameterReloadTicket: h.ticket() });
    h.manager._parameterReloadSequence++;
    h.manager.reloadModelParameters = async () => { throw error; };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 0);
});

test('takeover between inner rejection and the outer catch cancels old fallback', async () => {
    const h = harness();
    const error = Object.assign(new Error('current failure becomes stale'), { _parameterReloadTicket: h.ticket() });
    h.manager.reloadModelParameters = async () => {
        queueMicrotask(() => { h.manager._parameterReloadSequence++; });
        throw error;
    };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 0);
});

test('manager replacement blocks fallback even if the old ticket remains current in its manager', async () => {
    const h = harness();
    const error = Object.assign(new Error('old manager failed'), { _parameterReloadTicket: h.ticket() });
    h.manager.reloadModelParameters = async () => {
        h.context.window.live2dManager = { currentModel: { internalModel: { coreModel: {} } } };
        throw error;
    };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 0);
});

test('model takeover blocks a readiness failure that has no parameter ticket', async () => {
    const h = harness();
    h.manager.reloadModelParameters = async () => {
        h.manager.currentModel = { internalModel: { coreModel: {} } };
        throw new Error('readiness failure without ticket');
    };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 0);
});

test('an unchanged readiness failure keeps the existing fallback', async () => {
    const h = harness();
    h.manager.currentModel = null;
    h.manager.reloadModelParameters = async () => { throw new Error('not ready'); };
    assert.equal(await h.run(), false);
    assert.equal(h.fallbackCalls, 1);
});

async function actualHandler() {
    const runtime = await loadRuntime();
    const fixture = createMao(runtime);
    let fallbackCalls = 0;
    runtime.context.live2dManager = fixture.manager;
    runtime.context.I = { handleModelReload: async () => { fallbackCalls++; } };
    vm.runInContext(handlerSource, runtime.context, { filename: handlerFilename });
    return { fixture, runtime, get fallbackCalls() { return fallbackCalls; },
        run: () => runtime.context.I.handleReloadModelParametersMessage({ model_name: 'mao_pro',
            model_path: fixture.manager._lastLoadedModelPath }) };
}

for (const failure of ['reject', 'empty']) {
    test(`real SDK manager: late ${failure} leaves newer saved parameters without full reload`, async () => {
        const h = await actualHandler();
        const { fixture: f } = h;
        const entered = deferred(), gate = deferred();
        try {
            let reads = 0;
            f.manager._loadModelDirectoryParameters = async () => ({});
            f.manager.loadUserPreferences = async () => {
                if (++reads === 1) {
                    entered.resolve();
                    await gate.promise;
                    if (failure === 'reject') throw new Error('old preference read failed');
                    return [];
                }
                return f.preferences({ ParamAngleX: 8 });
            };
            const older = h.run();
            await entered.promise;
            assert.equal(await h.run(), true);
            gate.resolve();
            assert.equal(await older, false);
            assert.equal(h.fallbackCalls, 0);
            assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
            f.motionManager.update(0);
            assert.equal(f.value('ParamAngleX'), 8);
        } finally { gate.resolve(); f.release(); }
    });
}

test('real SDK manager receipt is rechecked after a configuration publication during rejection propagation', async () => {
    const h = await actualHandler();
    const { fixture: f } = h;
    try {
        f.manager._loadModelDirectoryParameters = async () => ({});
        let readFinished = false, statusAfterRead = 0;
        f.manager.loadUserPreferences = async () => { readFinished = true; return []; };
        const status = f.manager._getParameterReloadTicketStatus.bind(f.manager);
        f.manager._getParameterReloadTicketStatus = ticket => {
            const result = status(ticket);
            // First status is after the preference await; second is immediately
            // before the real reload attaches its failure receipt and throws.
            if (readFinished && ++statusAfterRead === 2) queueMicrotask(() => f.apply({ ParamAngleX: 8 }));
            return result;
        };
        assert.equal(await h.run(), false);
        assert.equal(f.manager.savedModelParameters.ParamAngleX, 8);
        assert.equal(h.fallbackCalls, 0);
    } finally { f.release(); }
});

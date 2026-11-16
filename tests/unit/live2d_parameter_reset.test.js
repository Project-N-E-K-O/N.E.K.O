const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const { read, loadRuntime, createMao } = require('./fixtures/live2d_runtime_fixture.cjs');

const runtimePromise = loadRuntime();
const editorSource = read('static/js/live2d_parameter_editor.js');
function sourceBetween(start, end) {
    const from = editorSource.indexOf(start);
    const to = editorSource.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `editor section: ${start}`);
    return editorSource.slice(from, to);
}

function prepareEditor(f) {
    const context = f.runtime.context;
    context.live2dManager = f.manager;
    context.testModel = f.model;
    context.testResetAllButton = { addEventListener(_name, callback) { this.click = callback; } };
    // Execute the real draft initialization, reset button and save helpers.
    // DOM layout and status text are outside this parameter/persistence contract.
    vm.runInContext(`
        var live2dModel = testModel, draftParameters = {};
        var draftParameterIndexes = {}, parameterCatalog = [], parameterCatalogByIndex = new Map();
        var resetAllBtn = testResetAllButton;
        function updateParameterUIValues() {}
        function showStatus() {}
        function t(_key, fallback) { return fallback; }
    ` + sourceBetween('function initializeParameterDraft()', '// 获取参数的范围和默认值')
        + sourceBetween('function getParameterRange(', '// 更新参数UI值')
        + sourceBetween('function resetParameterOverrides(', 'if (saveBtn) {')
        + '\ninitializeParameterDraft();', context);
    return {
        resetAll() { context.testResetAllButton.click(); },
        resetOne(index) { vm.runInContext(`resetParameterOverrides([parameterCatalogByIndex.get(${index})]);`, context); },
        parameters() { return JSON.parse(vm.runInContext('JSON.stringify(buildParametersFromDraft(draftParameters, draftParameterIndexes, window.live2dManager, live2dModel.internalModel.coreModel))', context)); },
    };
}

async function withMao(callback) {
    const f = createMao(await runtimePromise);
    try { await callback(f); } finally { f.release(); }
}

test('an untouched editor does not turn model values into saved overrides', () => withMao(f => {
    f.apply({});
    const editor = prepareEditor(f);
    assert.deepEqual(editor.parameters(), {});
}));

test('single reset restores the model default and releases only that override', () => withMao(f => {
    f.apply({ ParamAngleX: 12, ParamAngleY: 8 });
    const editor = prepareEditor(f);
    editor.resetOne(f.core.getParameterIndex('ParamAngleX'));
    assert.equal(f.value('ParamAngleX'), 0);
    assert.deepEqual(editor.parameters(), { ParamAngleY: 8 });
}));

test('reset all leaves an empty snapshot and later edits add only their own override', () => withMao(f => {
    f.apply({ ParamAngleX: 12, ParamAngleY: 8 });
    const editor = prepareEditor(f);
    editor.resetAll();
    assert.equal(f.value('ParamAngleX'), 0);
    assert.equal(f.value('ParamAngleY'), 0);
    assert.deepEqual(editor.parameters(), {});
    vm.runInContext('draftParameters.ParamAngleY = 3;', f.runtime.context);
    assert.deepEqual(editor.parameters(), { ParamAngleY: 3 });
}));

test('an unavailable default releases the override without inventing a preview value', () => withMao(f => {
    f.apply({ ParamAngleX: 12 });
    const editor = prepareEditor(f);
    const index = f.core.getParameterIndex('ParamAngleX');
    f.defaultOverrides.set(index, NaN);
    f.raw.parameters.defaultValues[index] = NaN;
    vm.runInContext(`parameterCatalogByIndex.get(${index}).defaultValue = undefined;`, f.runtime.context);
    editor.resetOne(index);
    assert.equal(f.value('ParamAngleX'), 12);
    assert.deepEqual(editor.parameters(), {});
}));

test('reset persists and reloads even when the model directory is read-only', () => withMao(async f => {
    const context = f.runtime.context;
    const staleDirectory = { ParamAngleX: 12, ParamAllColor1: 0.8 };
    f.manager.initialParameters = Object.fromEntries(f.manager._buildModelParameterCatalog(f.core)
        .map(p => [p.key, p.defaultValue]));
    f.apply(staleDirectory);
    f.core.saveParameters();
    const editor = prepareEditor(f);
    editor.resetAll();
    let storedPreference;
    context.fetch = async (_url, options) => {
        if (options) storedPreference = JSON.parse(options.body);
        return { ok: true, json: async () => options ? { success: true } : [storedPreference] };
    };
    f.manager._loadModelDirectoryParameters = async () => staleDirectory;
    let mirrorParameters;
    const result = await context.persistParameterDraft({ name: 'mao_pro', path: f.manager._lastLoadedModelPath },
        f.model, editor.parameters(), { manager: f.manager, fetchImpl: async (_url, options) => {
            mirrorParameters = JSON.parse(options.body).parameters;
            return { ok: false, status: 403, json: async () => ({ success: false, error: 'read-only' }) };
        } });
    assert.equal(result.fileSuccess, false);
    assert.equal(result.prefSuccess, true);
    assert.deepEqual(mirrorParameters, {});
    assert.deepEqual(storedPreference.parameters, {});
    assert.equal(storedPreference.parameters_mode, 'replace');

    assert.equal((await f.manager.reloadModelParameters()).applied, true);
    assert.equal(f.manager.savedModelParameters, null);
    assert.equal(f.manager._shouldApplySavedParams, false);
    f.core.loadParameters();
    for (const id of Object.keys(staleDirectory)) {
        const defaultValue = f.core.getParameterDefaultValue(f.core.getParameterIndex(id));
        assert.equal(f.value(id), defaultValue, `${id}: the SDK cache cannot revive the old value`);
    }
    assert.equal(f.manager.appearanceBaselineParameters.ParamAllColor1,
        f.core.getParameterDefaultValue(f.core.getParameterIndex('ParamAllColor1')));
    f.writes = { ParamAngleX: 4 };
    for (let frame = 0; frame < 10; frame++) {
        f.motionManager.update(frame / 30);
        assert.equal(f.value('ParamAngleX'), 4);
    }

    // A new manager/model represents restart: the stale directory must stay ignored.
    const fresh = createMao(f.runtime);
    try {
        fresh.manager._applyEffectiveModelParameters(fresh.model, staleDirectory, storedPreference.parameters,
            undefined, storedPreference.parameters_mode);
        assert.equal(fresh.manager.savedModelParameters, null);
        const sparse = { ...storedPreference, parameters: { ParamAngleY: 3 } };
        fresh.manager._applyEffectiveModelParameters(fresh.model, staleDirectory, sparse.parameters,
            undefined, sparse.parameters_mode);
        assert.deepEqual(JSON.parse(JSON.stringify(fresh.manager.effectiveModelParameters)), { ParamAngleY: 3 });
        assert.equal(fresh.value('ParamAngleX'), 0);
    } finally { fresh.release(); }
}));

test('legacy preferences keep directory fallback until saved as a complete snapshot', () => withMao(f => {
    const directory = { ParamAngleX: 12, ParamAngleY: 8 };
    const legacy = f.manager._mergeEffectiveModelParameters(directory, { ParamAngleX: 3 }, f.core);
    assert.deepEqual(JSON.parse(JSON.stringify(legacy)), { ParamAngleX: 3, ParamAngleY: 8 });
    const snapshot = f.manager._mergeEffectiveModelParameters(directory, { ParamAngleX: 3 }, f.core, 'replace');
    assert.deepEqual(JSON.parse(JSON.stringify(snapshot)), { ParamAngleX: 3 });
}));

test('releasing an override does not capture unrelated transient values into the SDK cache', () => withMao(f => {
    f.apply({ ParamAllColor1: 0.8 });
    f.core.setParameterValueById('ParamAngleX', 7);
    f.core.saveParameters();
    f.core.setParameterValueById('ParamAngleX', 12);
    f.apply({});
    assert.equal(f.value('ParamAngleX'), 12);
    f.core.loadParameters();
    assert.equal(f.value('ParamAngleX'), 7);
    assert.equal(f.value('ParamAllColor1'),
        f.core.getParameterDefaultValue(f.core.getParameterIndex('ParamAllColor1')));
}));

const assert = require('node:assert/strict');
const test = require('node:test');
const { loadRuntime, createMao } = require('./fixtures/live2d_runtime_fixture.cjs');

// 走真实的 _configureLoadedModel：桌宠窗口里 Cubism renderer 的每帧
// saveProfile/restoreProfile 必须在模型配置完成后被跳过（#3593）。
const runtimePromise = loadRuntime();

async function configureWithRenderer({ pet }) {
    const runtime = await runtimePromise;
    const f = createMao(runtime);
    const previousPet = runtime.context.__LANLAN_IS_ELECTRON_PET__;
    runtime.context.__LANLAN_IS_ELECTRON_PET__ = pet;
    const calls = { save: 0, restore: 0 };
    const renderer = {
        saveProfile() { calls.save++; },
        restoreProfile() { calls.restore++; },
        // 与 vendored Cubism renderer 一致：drawModel 内部包一层 save/restore
        drawModel() { this.saveProfile(); this.restoreProfile(); },
    };
    f.model.internalModel.renderer = renderer;
    // 只替换 UI/渲染时序相关的依赖，_configureLoadedModel 本体照常执行
    f.manager.pixi_app = { stage: { addChild() {}, removeChild() {} },
        ticker: { start() {}, stop() {} }, view: { style: {} } };
    f.manager.applyModelSettings = () => {};
    f.manager.recordInitialParameters = () => {};
    f.manager._preTickPhysics = async () => {};
    f.manager._waitForModelVisualStability = async () => {};
    f.manager._loadModelDirectoryParameters = async () => ({});
    const options = { minimalEmbed: true, loadEmotionMapping: false, suppressPersistentExpressions: true,
        suppressInitialIdle: true, preferences: { parameters: {} } };
    try {
        await f.manager._configureLoadedModel(f.model, f.manager._lastLoadedModelPath, options, 0);
        assert.equal(f.manager._modelLoadState, 'ready');
        renderer.drawModel();
        return { calls, manager: f.manager };
    } finally {
        runtime.context.__LANLAN_IS_ELECTRON_PET__ = previousPet;
        f.release();
    }
}

test('Electron 桌宠窗口：模型配置完成后 drawModel 不再 save/restore WebGL profile', async () => {
    const { calls, manager } = await configureWithRenderer({ pet: true });
    assert.equal(calls.save, 0);
    assert.equal(calls.restore, 0);
    assert.equal(manager._rendererProfileOptimized, true);
});

test('非桌宠页面：模型配置完成后 drawModel 仍然 save/restore WebGL profile', async () => {
    const { calls, manager } = await configureWithRenderer({ pet: false });
    assert.equal(calls.save, 1);
    assert.equal(calls.restore, 1);
    assert.equal(manager._rendererProfileOptimized, false);
});

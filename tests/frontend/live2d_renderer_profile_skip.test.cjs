const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// pytest 包装层把本文件内容写到临时路径再交给 node 跑，__dirname 不是仓库根。
const fileRoot = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const read = (rel) => fs.readFileSync(path.join(PROJECT_ROOT, ...rel.split('/')), 'utf8');

const LIVE2D_CORE_SRC = read('static/live2d/live2d-core.js');

// 只需要 Live2DManager 能被构造；其余浏览器 API 给最小桩。
function createManager({ pet = false, linuxX11 = false } = {}) {
    const PIXI = {
        live2d: { Live2DModel: class Live2DModel {} },
        Ticker: { shared: {}, system: {} },
    };
    const window = {
        PIXI,
        __LANLAN_IS_ELECTRON_PET__: pet === true,
        __NEKO_DESKTOP_RUNTIME__: linuxX11 ? { isLinuxX11: true } : undefined,
        innerWidth: 1920,
        innerHeight: 1080,
        screen: { width: 1920, height: 1080 },
        devicePixelRatio: 1,
        location: { hostname: 'localhost', pathname: '/' },
        navigator: { userAgent: 'node' },
        localStorage: { getItem() { return null; }, setItem() {} },
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() { return true; },
    };
    const document = {
        hidden: false,
        readyState: 'complete',
        getElementById() { return null; },
        addEventListener() {},
        createElement() { return { style: {} }; },
        body: { classList: { contains() { return false; } } },
    };
    const sandbox = {
        window,
        document,
        PIXI,
        console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
        performance: { now: () => 0 },
        navigator: window.navigator,
        localStorage: window.localStorage,
        setTimeout() { return 0; },
        clearTimeout() {},
        setInterval() { return 0; },
        clearInterval() {},
        requestAnimationFrame() { return 0; },
        cancelAnimationFrame() {},
        CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    };
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(LIVE2D_CORE_SRC, sandbox, { filename: 'live2d-core.js' });
    return new window.Live2DManager();
}

function makeModel() {
    const calls = { save: 0, restore: 0 };
    const renderer = {
        saveProfile() { calls.save++; },
        restoreProfile() { calls.restore++; },
        // 与 vendored Cubism renderer 一致：drawModel 内部包一层 save/restore
        drawModel() { this.saveProfile(); this.restoreProfile(); },
    };
    return { model: { internalModel: { renderer } }, renderer, calls };
}

test('Electron 桌宠窗口跳过 Cubism 每帧 profile save/restore（#3593）', () => {
    const mgr = createManager({ pet: true });
    const { model, renderer, calls } = makeModel();
    mgr._optimizeRendererProfile(model);
    renderer.drawModel();
    assert.equal(calls.save, 0);
    assert.equal(calls.restore, 0);
    assert.equal(renderer.__nekoProfileOptimized, true);
    assert.equal(mgr._rendererProfileOptimized, true);
    // 原函数保留，便于排查时手动还原
    assert.equal(typeof renderer.__nekoOriginalSaveProfile, 'function');
    assert.equal(typeof renderer.__nekoOriginalRestoreProfile, 'function');
});

test('Linux X11 桌面运行时仍然跳过 profile save/restore', () => {
    const mgr = createManager({ linuxX11: true });
    const { model, renderer, calls } = makeModel();
    mgr._optimizeRendererProfile(model);
    renderer.drawModel();
    assert.equal(calls.save, 0);
    assert.equal(calls.restore, 0);
});

test('非桌宠、非 X11 页面（模型管理器、预览页、开发单窗口）保持原 save/restore', () => {
    const mgr = createManager();
    const { model, renderer, calls } = makeModel();
    mgr._optimizeRendererProfile(model);
    renderer.drawModel();
    assert.equal(calls.save, 1);
    assert.equal(calls.restore, 1);
    assert.equal(renderer.__nekoProfileOptimized, undefined);
    assert.equal(mgr._rendererProfileOptimized, false);
});

test('重复调用不会把空函数存成原函数', () => {
    const mgr = createManager({ pet: true });
    const { model, renderer } = makeModel();
    const originalSave = renderer.saveProfile;
    mgr._optimizeRendererProfile(model);
    mgr._optimizeRendererProfile(model);
    assert.equal(renderer.__nekoOriginalSaveProfile, originalSave);
});

test('renderer 缺失或不带 save/restore 时安全跳过', () => {
    const mgr = createManager({ pet: true });
    assert.doesNotThrow(() => mgr._optimizeRendererProfile(null));
    assert.doesNotThrow(() => mgr._optimizeRendererProfile({ internalModel: {} }));
    assert.doesNotThrow(() => mgr._optimizeRendererProfile({ internalModel: { renderer: {} } }));
    assert.equal(mgr._rendererProfileOptimized, false);
});
// 真实加载路径（_configureLoadedModel）的行为测试在 tests/unit/live2d_renderer_profile_load.test.js。

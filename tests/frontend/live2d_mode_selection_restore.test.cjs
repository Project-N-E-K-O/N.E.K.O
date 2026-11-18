const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const PAGE_CONTROLLER_JS = path.join(
    PROJECT_ROOT, 'static', 'js', 'model_manager', 'page-controller.js');
const source = fs.readFileSync(PAGE_CONTROLLER_JS, 'utf8');

function loadSelectionHelpers() {
    const start = source.indexOf('function normalizeLive2DSelectionPath(');
    const end = source.indexOf('function readRememberedLive2DSelection()', start);
    assert.ok(start >= 0 && end > start, 'Live2D 选择匹配 helper 区块不存在');
    const sandbox = {};
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end) + `
        globalThis.helpers = {
            getLive2DModelSelection,
            getLive2DOptionSelection,
            findLive2DSelectionMatch,
        };`, sandbox);
    return sandbox.helpers;
}

const helpers = loadSelectionHelpers();

function option({ itemId = '', modelPath = '', source = '', name }) {
    return {
        value: name,
        dataset: {
            modelType: 'live2d',
            itemId,
            modelPath,
            modelSource: source,
            modelName: name,
        },
    };
}

function match(options, selection) {
    return helpers.findLive2DSelectionMatch(
        options,
        selection,
        helpers.getLive2DOptionSelection,
    );
}

test('Live2D → PNGTuber → Live2D 优先按 item_id 恢复', () => {
    const builtin = option({ modelPath: '/static/shared/model.model3.json', source: 'static', name: 'shared' });
    const workshop = option({ itemId: '123', modelPath: '/workshop/123/shared/model.model3.json', source: 'steam_workshop', name: 'shared' });
    assert.equal(match([builtin, workshop], { item_id: '123', name: 'shared' }), workshop);
});

test('刷新后可用休眠 Live2D 路径恢复，不依赖 PNGTuber 覆盖的来源字段', () => {
    const local = option({ modelPath: '/user_live2d/shared/model.model3.json', source: 'documents', name: 'shared' });
    const workshop = option({ itemId: '456', modelPath: '/workshop/456/shared/model.model3.json', source: 'steam_workshop', name: 'shared' });
    assert.equal(match([workshop, local], { path: '/user_live2d/shared/model.model3.json' }), local);
});

test('同名模型用来源消歧，只有名称且重名时拒绝误选', () => {
    const builtin = option({ modelPath: '/static/shared/model.model3.json', source: 'builtin', name: 'shared' });
    const local = option({ modelPath: '/user_live2d/shared/model.model3.json', source: 'local_imported', name: 'shared' });
    assert.equal(match([builtin, local], { source: 'local_imported', name: 'shared' }), local);
    assert.equal(match([builtin, local], { name: 'shared' }), null);
});

test('原 Live2D 已删除时返回 null，由调用方才选择兜底模型', () => {
    const fallback = option({ modelPath: '/static/mao-pro/mao-pro.model3.json', source: 'builtin', name: 'mao-pro' });
    assert.equal(match([fallback], { item_id: 'missing', path: '/workshop/missing/model.model3.json', name: 'gone' }), null);
});

test('模式切换先恢复记忆选择，匹配失败后才取列表第一项', () => {
    const start = source.indexOf('async function reloadSelectedLive2DModelAfterModeSwitch()');
    const end = source.indexOf('async function previewPNGTuberConfig(', start);
    const block = source.slice(start, end);
    assert.ok(block.indexOf('findLive2DOptionBySelection(rememberedSelection)') >= 0);
    assert.ok(block.indexOf('findLive2DOptionBySelection(rememberedSelection)')
        < block.indexOf("find(option => option.dataset.modelType === 'live2d')"));
});

test('休眠绑定恢复不读取共享 asset_source 字段', () => {
    const start = source.indexOf('function rememberDormantLive2DModelFromCharacterConfig(');
    const end = source.indexOf('function createLive2DModelOption(', start);
    const block = source.slice(start, end);
    const executableBlock = block.replace(/\/\/.*$/gm, '');
    assert.doesNotMatch(executableBlock, /asset_source(?:_id)?/);
    assert.match(block, /configuredLive2D\.model_path/);
});

test('Live2D keeps backend disambiguation labels for local, Steam and managed models', () => {
    const start = source.indexOf('function createLive2DModelOption(');
    const end = source.indexOf('async function captureWorkshopListContext()', start);
    const sandbox = {
        getLive2DModelSelection: helpers.getLive2DModelSelection,
        document: { createElement() { return { dataset: {}, setAttribute() {} }; } },
    };
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end) + '\nglobalThis.make = createLive2DModelOption;', sandbox);
    const models = [
        { name: 'Hiyori', path: '/user_live2d/Hiyori/a.model3.json', display_name: 'Hiyori (local)' },
        { name: 'Hiyori', path: '/workshop/123/Hiyori/a.model3.json', item_id: '123', display_name: 'Hiyori (steam_workshop 123)' },
        { name: 'neko_model_raw', path: '/user_live2d/neko_model_raw/a.model3.json', model_workshop: { install_id: 'a'.repeat(32) }, display_name: 'Package title' },
    ];
    const options = models.map(sandbox.make);
    assert.deepEqual(options.map(item => item.textContent), models.map(item => item.display_name));
    assert.deepEqual(options.map(item => item.value), models.map(item => item.name));
    assert.equal(options[1].dataset.itemId, '123');
    assert.equal(options[2].dataset.itemId, undefined);
});

test('legacy uploads show translated workshop errors in all eight languages', () => {
    const start = source.indexOf('function legacyModelUploadError(');
    const end = source.indexOf('function createLive2DModelOption(', start);
    for (const language of ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW']) {
        const translations = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, 'static/locales', language + '.json'), 'utf8')).modelWorkshop;
        const sandbox = { t: key => translations[key.split('.')[1]] };
        vm.createContext(sandbox);
        vm.runInContext(source.slice(start, end) + '\nglobalThis.message = legacyModelUploadError;', sandbox);
        for (const [code, key] of [['model_workshop_install_conflict', 'conflict'], ['model_workshop_busy', 'busy'], ['model_workshop_state_unreadable', 'stateUnavailable']]) {
            assert.ok(translations[key]);
            assert.equal(sandbox.message({ error_code: code }), translations[key]);
        }
        assert.equal(sandbox.message({ error: 'ordinary server error' }), 'ordinary server error');
        assert.equal(sandbox.message({}), translations.importFailed);
    }
});

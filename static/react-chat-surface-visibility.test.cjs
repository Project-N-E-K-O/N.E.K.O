const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const geometrySource = fs.readFileSync(
    path.join(__dirname, 'app', 'app-react-chat-window', 'message-bundle-actions-and-prompts.js'),
    'utf8'
);
const apiSource = fs.readFileSync(
    path.join(__dirname, 'app', 'app-react-chat-window', 'resize-drag-and-api.js'),
    'utf8'
);
const bootstrapSource = fs.readFileSync(
    path.join(__dirname, 'app', 'app-react-chat-window', 'bootstrap-state-and-geometry.js'),
    'utf8'
);

function chatFontPresetBootstrapBlock() {
    const start = bootstrapSource.indexOf('    var CHAT_FONT_PRESET_STORAGE_KEY');
    const end = bootstrapSource.indexOf('\n    I.GALGAME_HISTORY_LIMIT', start);
    assert.notEqual(start, -1, 'missing chat font preset bootstrap');
    assert.notEqual(end, -1, 'missing chat font preset bootstrap boundary');
    return bootstrapSource.slice(start, end);
}

function createChatFontPresetContext(storedPreset = null) {
    const attributes = {};
    const listeners = {};
    const localStorage = {
        getItem: (key) => key === 'neko.reactChatWindow.fontPreset' ? storedPreset : null
    };
    const context = {
        I: {},
        window: {
            localStorage,
            addEventListener(type, listener) {
                listeners[type] = listener;
            }
        },
        document: {
            documentElement: {
                setAttribute(name, value) {
                    attributes[name] = value;
                }
            }
        }
    };
    vm.runInNewContext(chatFontPresetBootstrapBlock(), context);
    return {
        attributes,
        listeners,
        localStorage,
        restorePreference: context.I.restoreChatFontPresetPreference
    };
}

function assignmentBlock(name, nextName) {
    const start = geometrySource.indexOf(`I.${name} = function ${name}`);
    const end = geometrySource.indexOf(`\n    I.${nextName}`, start);
    assert.notEqual(start, -1, `missing ${name}`);
    assert.notEqual(end, -1, `missing ${nextName} boundary`);
    return geometrySource.slice(start, end);
}

function createContext(options = {}) {
    const shell = {
        style: {},
        getBoundingClientRect: () => ({
            left: 100,
            top: 300,
            width: 500,
            height: 400,
            ...(options.shellRect || {})
        })
    };
    let compactApplication = null;
    const context = {
        I: {
            minimized: false,
            COMPACT_SURFACE_DEFAULT_HEIGHT: 96,
            getShell: () => shell,
            getCurrentChatSurfaceMode: () => options.mode || 'full',
            getCurrentCompactSurfaceRect: () => options.currentCompactRect || null,
            getCompactSurfaceTarget: () => options.compactTarget || null,
            applyCompactSurfaceRect: (left, top, width, height, applyOptions) => {
                compactApplication = { left, top, width, height, options: applyOptions };
                return compactApplication;
            }
        },
        window: {
            innerWidth: 1000,
            innerHeight: options.viewportHeight || 600
        }
    };
    vm.runInNewContext(assignmentBlock('clampPosition', 'ensureChatSurfaceVisible'), context);
    vm.runInNewContext(assignmentBlock('ensureChatSurfaceVisible', 'applyPosition'), context);
    return { context, shell, getCompactApplication: () => compactApplication };
}

test('the default position clamp keeps the existing header-visible boundary', () => {
    const { context } = createContext({ viewportHeight: 300 });
    const position = context.I.clampPosition(100, 200);
    assert.equal(position.left, 100);
    assert.equal(position.top, 200);
});

test('full chat uses complete height only through the explicit visibility operation', () => {
    const { context, shell } = createContext({ mode: 'full', viewportHeight: 600 });

    assert.equal(context.I.ensureChatSurfaceVisible(), true);
    assert.equal(shell.style.left, '100px');
    assert.equal(shell.style.top, '200px');
    assert.equal(shell.style.transform, 'none');
});

test('full chat returns false without rewriting a position that already fits', () => {
    const { context, shell } = createContext({
        mode: 'full',
        viewportHeight: 600,
        shellRect: { top: 100 }
    });

    assert.equal(context.I.ensureChatSurfaceVisible(), false);
    assert.deepEqual(shell.style, {});
});

test('dragged compact chat reapplies its mode-specific clamped target without persistence', () => {
    const { context, getCompactApplication } = createContext({
        mode: 'compact',
        currentCompactRect: { left: 100, top: 300, width: 430, height: 96 },
        compactTarget: { left: 100, top: 180, width: 430, height: 96 }
    });

    assert.equal(context.I.ensureChatSurfaceVisible(), true);
    const application = getCompactApplication();
    assert.equal(application.left, 100);
    assert.equal(application.top, 180);
    assert.equal(application.width, 430);
    assert.equal(application.height, 96);
    assert.equal(application.options.persist, false);
    assert.match(apiSource, /ensureChatSurfaceVisible: I\.ensureChatSurfaceVisible/);
});

test('compact chat returns false when its clamped target already matches the current rect', () => {
    const compactRect = { left: 100, top: 180, width: 430, height: 96 };
    const { context, getCompactApplication } = createContext({
        mode: 'compact',
        currentCompactRect: compactRect,
        compactTarget: { ...compactRect }
    });

    assert.equal(context.I.ensureChatSurfaceVisible(), false);
    assert.equal(getCompactApplication(), null);
});

test('chat host restores the font after the storage barrier and before React mounts', () => {
    const state = createChatFontPresetContext();

    assert.equal(state.attributes['data-neko-chat-font-preset'], undefined);
    assert.match(
        apiSource,
        /function init\(\) \{[\s\S]*?I\.restoreChatFontPresetPreference\(\);[\s\S]*?I\.syncChatSurfaceModeUI\(\);/
    );
    state.restorePreference();

    assert.equal(state.attributes['data-neko-chat-font-preset'], 'handwritten');
});

test('chat host restores and live-syncs the shared font preset', () => {
    const restored = createChatFontPresetContext('system');
    restored.restorePreference();
    assert.equal(restored.attributes['data-neko-chat-font-preset'], 'system');

    const live = createChatFontPresetContext();
    live.restorePreference();
    live.listeners['neko:chat-font-preset-changed']({ detail: { preset: 'system' } });
    assert.equal(live.attributes['data-neko-chat-font-preset'], 'system');

    live.listeners.storage({
        storageArea: {},
        key: 'neko.reactChatWindow.fontPreset',
        newValue: 'handwritten'
    });
    assert.equal(live.attributes['data-neko-chat-font-preset'], 'system');

    live.listeners.storage({
        storageArea: live.localStorage,
        key: 'neko.reactChatWindow.fontPreset',
        newValue: 'handwritten'
    });
    assert.equal(live.attributes['data-neko-chat-font-preset'], 'handwritten');

    live.listeners['neko:chat-font-preset-changed']({ detail: { preset: 'system' } });
    live.listeners.storage({
        storageArea: live.localStorage,
        key: null,
        newValue: null
    });
    assert.equal(live.attributes['data-neko-chat-font-preset'], 'handwritten');
});

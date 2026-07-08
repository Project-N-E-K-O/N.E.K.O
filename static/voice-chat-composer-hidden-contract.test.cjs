'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app-interpage.js'), 'utf8');

function createEventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
        },
        removeEventListener(type, handler) {
            const list = listeners.get(type);
            if (!list) return;
            const index = list.indexOf(handler);
            if (index !== -1) list.splice(index, 1);
        },
        dispatchEvent(event) {
            const list = listeners.get(event && event.type) || [];
            for (const handler of list.slice()) {
                handler.call(this, event);
            }
            return true;
        },
    };
}

function loadHarness() {
    const windowTarget = createEventTarget();
    const documentTarget = createEventTarget();
    let composerHidden = null;
    const localStorageData = new Map();
    const document = Object.assign(documentTarget, {
        hidden: false,
        visibilityState: 'visible',
        body: createEventTarget(),
        documentElement: { classList: { add() {}, remove() {} }, style: {} },
        getElementById() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement() {
            return Object.assign(createEventTarget(), {
                style: {},
                classList: { add() {}, remove() {}, contains() { return false; } },
                appendChild() {},
                setAttribute() {},
                removeAttribute() {},
            });
        },
    });
    function CustomEvent(type, options) {
        this.type = type;
        this.detail = options && options.detail;
    }
    const window = Object.assign(windowTarget, {
        window: null,
        document,
        console,
        location: { pathname: '/chat_full', origin: 'http://localhost', search: '', href: 'http://localhost/chat_full' },
        appState: {
            lanlan_name: '',
            isRecording: false,
            voiceChatActive: false,
            voiceStartPending: false,
        },
        appConst: {},
        appUtils: { isMobile: () => false },
        reactChatWindowHost: {
            setComposerHidden(hidden) {
                composerHidden = !!hidden;
            },
        },
        YuiGuideCommon: {
            createScopedTutorialResources() {
                return {
                    addEventListener(target, type, handler, options) {
                        target.addEventListener(type, handler, options);
                        return handler;
                    },
                    setTimeout: () => 0,
                    clearTimeout() {},
                    setInterval: () => 0,
                    clearInterval() {},
                    destroy() {},
                };
            },
        },
        CustomEvent,
        localStorage: {
            getItem(key) { return localStorageData.get(key) || null; },
            setItem(key, value) { localStorageData.set(key, String(value)); },
            removeItem(key) { localStorageData.delete(key); },
        },
        sessionStorage: {
            getItem() { return null; },
            setItem() {},
            removeItem() {},
        },
        navigator: { language: 'en-US' },
        setTimeout: () => 0,
        clearTimeout() {},
        setInterval: () => 0,
        clearInterval() {},
        requestAnimationFrame: () => 0,
        cancelAnimationFrame() {},
        getComputedStyle() { return {}; },
    });
    window.window = window;
    const context = {
        window,
        document,
        console,
        CustomEvent,
        Date,
        Math,
        Object,
        Array,
        String,
        Number,
        Boolean,
        URLSearchParams,
        setTimeout: window.setTimeout,
        clearTimeout: window.clearTimeout,
        setInterval: window.setInterval,
        clearInterval: window.clearInterval,
        requestAnimationFrame: window.requestAnimationFrame,
        cancelAnimationFrame: window.cancelAnimationFrame,
        localStorage: window.localStorage,
        sessionStorage: window.sessionStorage,
    };
    vm.runInNewContext(source, context, { filename: 'app-interpage.js' });
    return {
        window,
        get composerHidden() {
            return composerHidden;
        },
    };
}

test('voice chat composer sync posts through BroadcastChannel and Electron bridge', () => {
    assert.match(source, /function getVoiceChatComposerHiddenElectronBridge\(\)/);
    assert.match(source, /function postVoiceChatComposerHiddenElectron\(payload\)/);
    assert.match(source, /function postVoiceChatComposerHiddenPayload\(payload\)/);
    assert.match(source, /postInterpageMessage\(payload\);/);
    assert.match(source, /postVoiceChatComposerHiddenElectron\(payload\);/);
    assert.match(source, /postVoiceChatComposerHiddenPayload\(\{\s*action: 'voice_chat_active'/);
});

test('voice chat composer sync handles Electron restore events without rebroadcasting', () => {
    assert.match(source, /function handleVoiceChatComposerHiddenMessage\(data\)/);
    assert.match(source, /window\.addEventListener\('neko:electron-voice-chat-composer-hidden'/);
    assert.match(source, /handleVoiceChatComposerHiddenMessage\(\(event && event\.detail\) \|\| \{\}\);/);
});

test('voice chat composer state is buffered before full chat config hydration', () => {
    assert.match(
        source,
        /function handleVoiceChatComposerHiddenMessage\(data\) \{[\s\S]*?if \(data\.lanlan_name && !getCurrentLanlanName\(\)\) \{[\s\S]*?rememberPendingVoiceChatComposerHiddenMessage\(data\);[\s\S]*?return true;[\s\S]*?\}/,
    );
    assert.doesNotMatch(source, /return data\.active === false;/);
});

test('voice chat composer handler buffers scoped restore until matching config arrives', () => {
    const harness = loadHarness();
    assert.equal(harness.composerHidden, null);

    assert.equal(harness.window.appInterpage.handleVoiceChatComposerHiddenMessage({
        action: 'voice_chat_active',
        active: false,
        lanlan_name: 'A',
        timestamp: Date.now(),
    }), true);
    assert.equal(harness.composerHidden, null);

    harness.window.dispatchEvent(new harness.window.CustomEvent('neko:config-injected', {
        detail: { lanlan_name: 'B' },
    }));
    assert.equal(harness.composerHidden, null);

    harness.window.appState.lanlan_name = 'A';
    harness.window.lanlan_config = { lanlan_name: 'A' };
    harness.window.dispatchEvent(new harness.window.CustomEvent('neko:config-injected', {
        detail: { lanlan_name: 'A' },
    }));
    assert.equal(harness.composerHidden, false);
    assert.equal(harness.window.appState.voiceChatActive, false);
});

test('voice chat composer helper applies active state through the shared effective-hidden path', () => {
    const harness = loadHarness();
    harness.window.appState.lanlan_name = 'A';
    harness.window.lanlan_config = { lanlan_name: 'A' };

    harness.window.appInterpage.applyVoiceComposerHiddenFromActive(true);
    assert.equal(harness.composerHidden, true);
    assert.equal(harness.window.appState.voiceChatActive, true);

    harness.window.appInterpage.applyVoiceComposerHiddenFromActive(false);
    assert.equal(harness.composerHidden, false);
    assert.equal(harness.window.appState.voiceChatActive, false);
});

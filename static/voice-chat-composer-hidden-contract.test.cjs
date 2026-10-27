'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { readJsParts } = require('./app-part-test-utils.cjs');

const appInterpageDirectory = path.join(__dirname, 'app/app-interpage');
const source = readJsParts(appInterpageDirectory);
const runtimeSource = readJsParts(appInterpageDirectory, { contractView: false });

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

function loadHarness({ pathname = '/chat_full', voiceBridge, BroadcastChannel } = {}) {
    const windowTarget = createEventTarget();
    const documentTarget = createEventTarget();
    let composerHidden = null;
    let goodbyeComposerHidden = null;
    const catSnapshots = [];
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
        location: { pathname, origin: 'http://localhost', search: '', href: 'http://localhost' + pathname },
        nekoElectronVoiceChatComposerHidden: voiceBridge,
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
            setGoodbyeComposerHidden(hidden) {
                goodbyeComposerHidden = !!hidden;
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
    window.addEventListener('neko:cat-local-chat-state', (event) => {
        catSnapshots.push(event.detail);
    });
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
        BroadcastChannel,
    };
    vm.runInNewContext(runtimeSource, context, { filename: 'app/app-interpage' });
    return {
        window,
        get composerHidden() {
            return composerHidden;
        },
        get goodbyeComposerHidden() {
            return goodbyeComposerHidden;
        },
        catSnapshots,
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

for (const pathname of ['/chat', '/chat_full']) {
    test(`${pathname} requests the current voice state after config injection and when shown again`, () => {
        const sent = [];
        const harness = loadHarness({ pathname, voiceBridge: { send: (data) => sent.push(data) } });
        const { window } = harness;
        assert.equal(sent.length, 0, 'wait for a character identity before requesting state');

        window.appState.lanlan_name = 'A';
        window.dispatchEvent(new window.CustomEvent('neko:config-injected', { detail: { lanlan_name: 'A' } }));
        window.dispatchEvent(new window.CustomEvent('focus'));
        window.document.hidden = true;
        window.document.dispatchEvent(new window.CustomEvent('visibilitychange'));
        window.document.hidden = false;
        window.document.dispatchEvent(new window.CustomEvent('visibilitychange'));

        assert.deepEqual(sent.map((data) => [data.action, data.lanlan_name]), [
            ['request_voice_chat_active', 'A'],
            ['request_voice_chat_active', 'A'],
            ['request_voice_chat_active', 'A'],
        ]);
    });
}

test('the main page restores a newly opened chat from its live voice state over Electron IPC', () => {
    let chat;
    const responses = [];
    const main = loadHarness({
        pathname: '/',
        voiceBridge: { send(data) {
            responses.push(data);
            chat.window.dispatchEvent(new chat.window.CustomEvent('neko:electron-voice-chat-composer-hidden', {
                detail: data,
            }));
        } },
    });
    chat = loadHarness({ voiceBridge: { send(data) {
        main.window.dispatchEvent(new main.window.CustomEvent('neko:electron-voice-chat-composer-hidden', {
            detail: data,
        }));
    } } });
    main.window.appState.lanlan_name = 'A';
    main.window.appState.isRecording = true;
    chat.window.appState.lanlan_name = 'A';

    chat.window.dispatchEvent(new chat.window.CustomEvent('neko:config-injected', { detail: { lanlan_name: 'A' } }));
    assert.equal(chat.composerHidden, true);
    assert.equal(chat.window.appState.voiceChatActive, true);
    assert.equal(responses.length, 1, 'chat mirrors must not answer state requests');

    main.window.appState.isRecording = false;
    main.window.appInterpage.syncVoiceChatComposerHidden(false);
    assert.equal(chat.composerHidden, false);
    assert.equal(chat.window.appState.voiceChatActive, false);
});

test('voice state requests use BroadcastChannel in the web host and stay scoped to the main page character', async () => {
    const sent = [];
    const channels = [];
    class BroadcastChannel {
        constructor() { channels.push(this); }
        postMessage(data) { sent.push(data); }
    }
    const main = loadHarness({ pathname: '/', BroadcastChannel });
    main.window.appState.lanlan_name = 'A';
    main.window.appState.voiceStartPending = true;
    const request = { action: 'request_voice_chat_active', lanlan_name: 'A', timestamp: Date.now() };
    await channels[0].onmessage({ data: request });
    assert.equal(sent.length, 1);
    assert.equal(sent[0].action, 'voice_chat_active');
    assert.equal(sent[0].active, true);
    assert.equal(sent[0].lanlan_name, 'A');

    main.window.appInterpage.handleVoiceChatComposerHiddenMessage({ ...request, lanlan_name: 'B' });
    main.window.appInterpage.handleVoiceChatComposerHiddenMessage({ ...request, lanlan_name: '' });
    assert.equal(sent.length, 1, 'ignore requests for an unknown or different character');

    const chat = loadHarness({ BroadcastChannel });
    chat.window.appState.lanlan_name = 'A';
    const messagesBeforeRequest = sent.length;
    chat.window.appInterpage.handleVoiceChatComposerHiddenMessage(request);
    assert.equal(sent.length, messagesBeforeRequest, 'a stale chat mirror cannot publish authoritative voice state');
    assert.equal(chat.window.appInterpage.requestVoiceChatComposerHiddenState(), true);
    assert.equal(sent.at(-1).action, 'request_voice_chat_active');
});

test('goodbye cat snapshot waits for matching config instead of being dropped', () => {
    const harness = loadHarness();
    const message = {
        action: 'goodbye_chat_composer_hidden',
        hidden: true,
        lanlan_name: 'A',
        cat_active: true,
        cat_tier: 'cat1',
        cat_entered_at: 1000,
        cat_items: [],
        timestamp: Date.now(),
    };

    assert.equal(
        harness.window.appInterpage.handleGoodbyeChatComposerHiddenMessage(message, 'test'),
        true,
    );
    assert.equal(harness.goodbyeComposerHidden, null);
    assert.equal(harness.catSnapshots.length, 0);

    harness.window.appState.lanlan_name = 'A';
    harness.window.lanlan_config = { lanlan_name: 'A' };
    harness.window.dispatchEvent(new harness.window.CustomEvent('neko:config-injected', {
        detail: { lanlan_name: 'A' },
    }));

    assert.equal(harness.goodbyeComposerHidden, true);
    assert.equal(harness.catSnapshots.length, 1);
    assert.equal(harness.catSnapshots[0].active, true);
    assert.equal(harness.catSnapshots[0].enteredAt, 1000);
});

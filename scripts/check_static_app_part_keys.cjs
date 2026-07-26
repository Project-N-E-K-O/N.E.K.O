'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');


const repoRoot = path.resolve(__dirname, '..');

// Snapshot of the public keys published by the three monoliths immediately
// before they were split. Keep this contract independent of git history so the
// check remains runnable after the deleted source files reach main.
const expectedPublicKeys = {
    reactChatWindowHost: [
        'appendMessage', 'clearChoicePromptBySource', 'clearGuideMessages',
        'clearIcebreakerChoicePrompt', 'clearMessages', 'clearPendingRollbackDraft',
        'closeWindow', 'cycleChatSurfaceMode', 'deactivateAvatarTool', 'ensureBundleLoaded',
        'getChatSurfaceMode', 'getState', 'handleMiniGameInviteResolved',
        'isGalgameModeEnabled', 'isMounted', 'openWindow', 'prepareCompactHistoryDropSubmit',
        'refreshGalgameOptions', 'removeMessage', 'rollbackLastDraft', 'rotateCompactToolWheel',
        'setAvatarToolMenuOpen', 'setChatSurfaceMode', 'setChoicePrompt',
        'setCompactChatState', 'setCompactHistoryOpen', 'setCompactToolFanOpen',
        'setCompactToolWheelIndex', 'setComposerAttachments', 'setComposerHidden',
        'setGalgameModeEnabled', 'setGoodbyeComposerHidden', 'setHomeTutorialInputLocked',
        'setHomeTutorialInteractionLocked', 'setIcebreakerChoicePrompt', 'setMessages',
        'setMiniGameInvitePrompt', 'setNewUserIcebreakerPrompt', 'setOnAvatarInteraction',
        'setOnAvatarToolStateChange', 'setOnComposerImportImage', 'setOnComposerRemoveAttachment',
        'setOnComposerScreenshot', 'setOnComposerSubmit', 'setOnMessageAction',
        'setTranslateEnabled', 'setViewProps', 'syncGoodbyeComposerHidden',
        'triggerComposerScreenshot', 'updateMessage',
    ],
    appUi: [
        'completeGoodbyeResourceSuspend', 'ensureHiddenElements', 'hideLive2d',
        'hideVoicePreparingToast', 'initFinalUiGuards', 'initFloatingButtonListeners',
        'restoreGoodbyeResourceSuspend', 'showCurrentModel', 'showLive2d',
        'showProminentNotice', 'showReadyToSpeakToast', 'showStatusToast', 'showSurveyModal',
        'showVoicePreparingToast', 'syncFloatingMicButtonState', 'syncFloatingScreenButtonState',
    ],
    appInterpage: [
        'applyGoodbyeChatComposerHidden', 'applyTutorialChatIdentityOverride',
        'applyVoiceComposerHiddenFromActive', 'cleanupLive2DOverlayUI', 'cleanupMMDOverlayUI',
        'cleanupPNGTuberOverlayUI', 'cleanupVRMOverlayUI',
        'consumePendingVoiceChatComposerHiddenMessage', 'handleGoodbyeChatComposerHiddenMessage',
        'handleHideMainUI', 'handleMemoryEdited', 'handleModelReload', 'handleShowMainUI',
        'handleVoiceChatComposerHiddenMessage', 'isMainUIHiddenByModelManager',
        'isVoiceConfigSwitching', 'nekoBroadcastChannel', 'postGoodbyeChatComposerHiddenElectron',
        'postGoodbyeChatComposerHiddenState', 'postIcebreakerBridgeEvent',
        'postIcebreakerChoiceSelected', 'postIcebreakerFreeTextSubmitted',
        'postVoiceChatComposerHiddenElectron', 'requestGoodbyeChatComposerHiddenState',
        'resetToDefaultModel', 'shouldKeepVoiceComposerHidden', 'syncVoiceChatComposerHidden',
        'waitForVoiceConfigSwitchReady',
    ],
};


function createElement() {
    return {
        hidden: false,
        style: { setProperty() {}, removeProperty() {} },
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        dataset: {},
        appendChild() {},
        remove() {},
        setAttribute() {},
        removeAttribute() {},
        getAttribute() { return null; },
        addEventListener() {},
        removeEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    };
}


function createContext(options = {}) {
    const listeners = new Map();
    const storage = new Map();
    const documentElement = createElement();
    const body = createElement();
    const document = {
        readyState: 'loading',
        hidden: options.hidden === true,
        currentScript: null,
        body,
        head: createElement(),
        documentElement,
        createElement,
        getElementById() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener(type, handler) {
            const key = `document:${type}`;
            listeners.set(key, [...(listeners.get(key) || []), handler]);
        },
        removeEventListener() {},
    };
    const localStorage = {
        getItem(key) { return storage.has(key) ? storage.get(key) : null; },
        setItem(key, value) { storage.set(key, String(value)); },
        removeItem(key) { storage.delete(key); },
    };
    const quietConsole = { log() {}, warn() {}, error() {}, debug() {} };
    const window = {
        appState: {},
        appConst: {},
        appUtils: {},
        lanlan_config: {},
        location: {
            href: `http://localhost${options.pathname || '/'}`,
            origin: 'http://localhost',
            pathname: options.pathname || '/',
        },
        localStorage,
        innerWidth: options.width || 1280,
        innerHeight: options.height || 720,
        devicePixelRatio: 1,
        screenX: 0,
        screenY: 0,
        console: quietConsole,
        addEventListener(type, handler) {
            listeners.set(type, [...(listeners.get(type) || []), handler]);
        },
        removeEventListener() {},
        dispatchEvent() { return true; },
        setTimeout() { return 1; },
        clearTimeout() {},
        setInterval() { return 1; },
        clearInterval() {},
        requestAnimationFrame() { return 1; },
        cancelAnimationFrame() {},
        getComputedStyle() { return { display: 'block', visibility: 'visible', opacity: '1' }; },
    };
    window.window = window;
    class CustomEvent {
        constructor(type, init) {
            this.type = type;
            this.detail = init && init.detail;
        }
    }
    class MutationObserver {
        observe() {}
        disconnect() {}
    }
    class Image {
        set src(_value) {}
    }
    const context = {
        window,
        document,
        localStorage,
        console: quietConsole,
        CustomEvent,
        MutationObserver,
        Image,
        URL,
        URLSearchParams,
        WebSocket: { OPEN: 1 },
        navigator: { language: 'en-US', userAgent: 'node-contract-harness' },
        screen: { width: 1280, height: 720, availWidth: 1280, availHeight: 720 },
        fetch: async () => ({ ok: true, json: async () => ({}), text: async () => '' }),
        setTimeout: window.setTimeout,
        clearTimeout: window.clearTimeout,
        setInterval: window.setInterval,
        clearInterval: window.clearInterval,
        requestAnimationFrame: window.requestAnimationFrame,
        cancelAnimationFrame: window.cancelAnimationFrame,
    };
    return { context, window, listeners };
}


function partPaths(relativeDir) {
    const directory = path.join(repoRoot, relativeDir);
    return fs.readdirSync(directory)
        .filter((name) => name.endsWith('.js'))
        .sort()
        .map((name) => path.join(directory, name));
}


function runSource(source, filename) {
    const { context, window } = createContext();
    vm.runInNewContext(source, context, { filename });
    return window;
}


function runParts(relativeDir, options) {
    const { context, window } = createContext(options);
    for (const partPath of partPaths(relativeDir)) {
        vm.runInNewContext(fs.readFileSync(partPath, 'utf8'), context, { filename: partPath });
    }
    return window;
}


function checkInterpageEventBindingOrder() {
    const { context, window, listeners } = createContext();
    const channels = [];
    let relayedCustomEventHandler;
    let relayedWindowMessageHandler;
    class BroadcastChannel {
        constructor(name) {
            this.name = name;
            this.onmessage = null;
            channels.push(this);
        }

        postMessage() {}
        close() {}
    }
    context.BroadcastChannel = BroadcastChannel;

    const paths = partPaths('static/app/app-interpage');
    for (const partPath of paths) {
        vm.runInNewContext(fs.readFileSync(partPath, 'utf8'), context, { filename: partPath });
        if (path.basename(partPath) === 'cross-window-broadcast-and-bridge.js') {
            relayedCustomEventHandler = window.__appInterpageParts.handleYuiGuideRelayedCustomEvent;
            relayedWindowMessageHandler = window.__appInterpageParts.handleYuiGuideRelayedWindowMessage;
            assert.ok(
                !(listeners.get('neko:tutorial-overlay-relay') || []).includes(relayedCustomEventHandler),
                'tutorial overlay relay bound before later helpers loaded',
            );
            assert.ok(
                !(listeners.get('message') || []).includes(relayedWindowMessageHandler),
                'tutorial window message relay bound before later helpers loaded',
            );
        }
        if (path.basename(partPath) === 'guide-message-relay.js') {
            assert.equal(channels.length, 1);
            assert.equal(channels[0].onmessage, null, 'BroadcastChannel bound before later helpers loaded');
        }
    }
    assert.equal(typeof channels[0].onmessage, 'function', 'final part did not bind BroadcastChannel');
    assert.ok(
        (listeners.get('neko:tutorial-overlay-relay') || []).includes(relayedCustomEventHandler),
        'final part did not bind tutorial overlay relay',
    );
    assert.ok(
        (listeners.get('message') || []).includes(relayedWindowMessageHandler),
        'final part did not bind tutorial window message relay',
    );
    assert.equal(window.__appInterpageParts, undefined, 'internal namespace leaked after final assembly');
    process.stdout.write('appInterpage: cross-window event bindings deferred until final part\n');
}


for (const contract of [
    {
        partDir: 'static/app/app-react-chat-window',
        publicName: 'reactChatWindowHost',
    },
    { partDir: 'static/app/app-ui', publicName: 'appUi' },
    {
        partDir: 'static/app/app-interpage',
        publicName: 'appInterpage',
    },
]) {
    const partWindow = runParts(contract.partDir);
    const partKeys = Object.keys(partWindow[contract.publicName] || {}).sort();
    assert.deepEqual(partKeys, expectedPublicKeys[contract.publicName], `${contract.publicName} key set changed`);
    process.stdout.write(`${contract.publicName}: ${partKeys.length} keys match\n`);
}

for (const scenario of [
    { label: 'index-wide', pathname: '/', width: 1440, height: 900 },
    { label: 'index-narrow', pathname: '/', width: 390, height: 844 },
    { label: 'chat-hidden', pathname: '/chat', width: 900, height: 700, hidden: true },
]) {
    const scenarioWindow = runParts('static/app/app-react-chat-window', scenario);
    const host = scenarioWindow.reactChatWindowHost;
    host.setMessages([{ id: 'harness-message', role: 'assistant', content: scenario.label }]);
    const snapshot = host.getState();
    assert.equal(snapshot.messages.length, 1);
    assert.equal(snapshot.messages[0].id, 'harness-message');
    process.stdout.write(`${scenario.label}: React host state/render scheduling smoke passed\n`);
}

checkInterpageEventBindingOrder();

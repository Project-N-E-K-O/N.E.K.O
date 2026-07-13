'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');


const repoRoot = path.resolve(__dirname, '..');


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
        addEventListener(type, handler) { listeners.set(`document:${type}`, handler); },
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
        addEventListener(type, handler) { listeners.set(type, handler); },
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
    return { context, window };
}


function baselineSource(relativePath) {
    return childProcess.execFileSync(
        'git',
        ['show', `origin/main:${relativePath.replaceAll('\\', '/')}`],
        { cwd: repoRoot, encoding: 'utf8' },
    );
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


function checkInterpageBroadcastBindingOrder() {
    const { context, window } = createContext();
    const channels = [];
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
        if (path.basename(partPath) === 'guide-message-relay.js') {
            assert.equal(channels.length, 1);
            assert.equal(channels[0].onmessage, null, 'BroadcastChannel bound before later helpers loaded');
        }
    }
    assert.equal(typeof channels[0].onmessage, 'function', 'final part did not bind BroadcastChannel');
    assert.equal(window.__appInterpageParts, undefined, 'internal namespace leaked after final assembly');
    process.stdout.write('appInterpage: BroadcastChannel binding deferred until final part\n');
}


for (const contract of [
    {
        oldPath: 'static/app/app-' + 'react-chat-window' + '.js',
        partDir: 'static/app/app-react-chat-window',
        publicName: 'reactChatWindowHost',
    },
    { oldPath: 'static/app/app-' + 'ui' + '.js', partDir: 'static/app/app-ui', publicName: 'appUi' },
    {
        oldPath: 'static/app/app-' + 'interpage' + '.js',
        partDir: 'static/app/app-interpage',
        publicName: 'appInterpage',
    },
]) {
    const baselineWindow = runSource(baselineSource(contract.oldPath), contract.oldPath);
    const partWindow = runParts(contract.partDir);
    const baselineKeys = Object.keys(baselineWindow[contract.publicName] || {}).sort();
    const partKeys = Object.keys(partWindow[contract.publicName] || {}).sort();
    assert.deepEqual(partKeys, baselineKeys, `${contract.publicName} key set changed`);
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

checkInterpageBroadcastBindingOrder();
